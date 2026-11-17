from array import array
import json
import math
import subprocess
import wave
from unittest.mock import patch

import imageio_ffmpeg
import pytest
from PIL import Image, ImageChops

from news_prototype import telegram_story_podcast_trial as trial, podcast_audio_trial, combined_trial


def test_script_is_approved_ten_turns_five_pairs():
    sections = trial.script()['sections']
    assert len(sections) == 5
    assert [turn['text'] for section in sections for turn in section['turns']] == trial.TEXT
    assert all([t['speaker'] for t in section['turns']] == ['Бендер', 'Киберсенсей'] for section in sections)


def test_render_loops_video_and_excludes_source_audio(tmp_path):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    source = tmp_path / 'source.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24',
                    '-f', 'lavfi', '-i', 'sine=frequency=1700:sample_rate=24000', '-t', '1',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)], check=True)
    audio = tmp_path / 'speech.wav'
    rate = podcast_audio_trial.RATE
    samples = array('h', (round(4000 * math.sin(2 * math.pi * 300 * i / rate)) for i in range(rate * 2)))
    with wave.open(str(audio), 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate); out.writeframes(samples.tobytes())
    original = audio.read_bytes()
    result = tmp_path / 'output.mp4'
    with patch.object(combined_trial, 'display', wraps=combined_trial.display) as display:
        trial.render(source, result, ffmpeg, (audio, ['Бендер'] * 48, [True] * 48), clip_seconds=1)
    assert audio.read_bytes() == original
    assert any(c.args[3] < 1.0 for c in display.call_args_list)
    assert any(c.args[3] > 4.0 for c in display.call_args_list)
    output = subprocess.check_output([ffmpeg, '-v', 'error', '-i', str(result), '-map', '0:a:0',
                                      '-f', 's16le', '-ac', '1', '-ar', str(rate), 'pipe:1'])
    decoded = array('h'); decoded.frombytes(output)
    # At the centre of speech, the only tone should be the 300 Hz dialogue.
    start = round((combined_trial.INTRO_FRAMES / 24 + .2) * rate)
    window = decoded[start:start + rate]
    def power(freq):
        re = sum(x * math.cos(2 * math.pi * freq * i / rate) for i, x in enumerate(window))
        im = sum(x * math.sin(2 * math.pi * freq * i / rate) for i, x in enumerate(window))
        return math.hypot(re, im)
    assert power(300) > 100 * power(1700)
    images = []
    for n, when in enumerate((2.0, 2.5, 3.0)):
        p = tmp_path / f'{n}.png'
        subprocess.run([ffmpeg, '-v', 'error', '-ss', str(when), '-i', str(result), '-frames:v', '1', str(p)], check=True)
        with Image.open(p) as img: images.append(img.convert('RGB').copy())
    assert ImageChops.difference(images[0], images[1]).crop((335, 130, 550, 280)).getbbox()
    assert ImageChops.difference(images[1], images[2]).crop((335, 130, 550, 280)).getbbox()
    with pytest.raises(ValueError):
        trial.render(source, result, ffmpeg, (audio, ['Бендер'] * 48, [True] * 48), clip_seconds=1)


def test_existing_folder_never_requests_tts(tmp_path):
    source = tmp_path / 'source.mp4'; source.write_bytes(b'x')
    with patch.object(trial, 'SOURCE_SIZE', 1), patch.object(trial.bulletin_preflight, 'check', return_value=[]), \
         patch.object(trial.bulletin_duo_content_probe, 'read_worker_env', return_value={}), \
         patch.object(trial.bulletin_duo_content_probe, 'worker_keys', return_value=['key']), \
         patch.object(trial.bulletin_dialogue_audio, 'prepare') as prepare:
        with pytest.raises(FileExistsError): trial.run(tmp_path, source, 'ffmpeg')
        prepare.assert_not_called()
