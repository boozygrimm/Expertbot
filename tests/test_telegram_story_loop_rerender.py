from array import array
import json
import math
import subprocess
import wave
from unittest.mock import patch

import imageio_ffmpeg
from PIL import Image, ImageChops, ImageStat
import pytest

from news_prototype import telegram_story_loop_rerender as rerender, telegram_story_podcast_trial as trial


def audio_file(path, duration):
    rate = 24000
    samples = array('h', (round(4000 * math.sin(2 * math.pi * 300 * i / rate)) for i in range(duration * rate)))
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate); wav.writeframes(samples.tobytes())


def test_original_video_moves_after_first_playback(tmp_path):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    source, audio, result = tmp_path / 'source.mp4', tmp_path / 'dialogue.wav', tmp_path / 'result.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24',
                    '-t', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)], check=True)
    audio_file(audio, 4)
    timeline, _, _ = trial.build_timeline(audio, trial.script(), 1)
    trial.render(source, result, ffmpeg, timeline)
    images = []
    # Both timestamps are after the first second of source playback. If the
    # final frame were frozen, this screen region would remain identical.
    for i, when in enumerate((3.0, 3.5)):
        path = tmp_path / f'{i}.png'
        subprocess.run([ffmpeg, '-v', 'error', '-ss', str(when), '-i', str(result),
                        '-frames:v', '1', str(path)], check=True)
        with Image.open(path) as img: images.append(img.convert('RGB').crop((340, 115, 550, 200)))
    diff = ImageChops.difference(*images)
    assert sum(ImageStat.Stat(diff).mean) > 8


def test_rerender_reuses_saved_audio_and_is_one_shot(tmp_path):
    base = tmp_path / 'base'; base.mkdir()
    (base / 'script.json').write_text(json.dumps(trial.script(), ensure_ascii=False), encoding='utf-8')
    audio_dir = base / 'audio'; audio_dir.mkdir()
    audio = audio_dir / 'dialogue.wav'; audio_file(audio, 3)
    before = audio.read_bytes()
    source = tmp_path / 'source.mp4'; source.write_bytes(b'x')
    def render(src, output, binary, timeline):
        assert timeline[0] == audio
        output.write_bytes(b'fake video')
    with patch.object(trial, 'SOURCE_SIZE', 1), \
         patch.object(rerender.bulletin_preflight, 'check', return_value=[]), \
         patch.object(trial, 'source_duration', return_value=1), \
         patch.object(trial, 'render', side_effect=render) as create, \
         patch.object(rerender, 'report_black'), \
         patch.object(rerender.subprocess, 'run'), \
         patch.object(rerender.bulletin_duo_content_probe, 'read_worker_env', return_value={}), \
         patch.object(rerender.bulletin_full_video_trial, 'send_video_once', return_value='sent') as send, \
         patch.object(trial.gemini_tts, 'synthesize') as tts:
        result = rerender.run(base, source, 'ffmpeg', 'ffprobe')
        assert result.read_bytes() == b'fake video'
        assert audio.read_bytes() == before
        assert create.call_count == send.call_count == 1
        tts.assert_not_called()
        with pytest.raises(FileExistsError): rerender.run(base, source, 'ffmpeg', 'ffprobe')
        assert create.call_count == send.call_count == 1
        tts.assert_not_called()


def test_reject_modified_script_before_render(tmp_path):
    base = tmp_path / 'base'; base.mkdir()
    (base / 'script.json').write_text('{}', encoding='utf-8')
    folder = base / 'audio'; folder.mkdir(); audio_file(folder / 'dialogue.wav', 3)
    source = tmp_path / 'source.mp4'; source.write_bytes(b'x')
    with patch.object(trial, 'render') as render:
        with pytest.raises(ValueError): rerender.run(base, source, 'ffmpeg', 'ffprobe')
        render.assert_not_called()
