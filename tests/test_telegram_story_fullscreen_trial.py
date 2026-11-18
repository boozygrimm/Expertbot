from array import array
import json
import math
import subprocess
import wave
from unittest.mock import patch

import imageio_ffmpeg
import pytest
from PIL import Image, ImageChops, ImageStat

from news_prototype import (telegram_story_fullscreen_trial as full, telegram_story_podcast_trial as story,
                            podcast_video_trial, combined_trial)


def audio_file(path, duration, freq):
    rate = 24000
    samples = array('h', (round(5000 * math.sin(2 * math.pi * freq * i / rate)) for i in range(duration * rate)))
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate); wav.writeframes(samples.tobytes())
    return samples


def tone_strength(samples, freq):
    n = len(samples); rate = 24000
    re = sum(v * math.cos(2 * math.pi * freq * i / rate) for i, v in enumerate(samples))
    im = sum(v * math.sin(2 * math.pi * freq * i / rate) for i, v in enumerate(samples))
    return math.hypot(re, im) / n


def test_fullscreen_audio_timing_video_and_remote(tmp_path):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    source = tmp_path / 'source.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24',
                    '-f', 'lavfi', '-i', 'sine=frequency=1200:sample_rate=24000', '-t', '2',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)], check=True)
    dialogue = tmp_path / 'dialogue.wav'; speech = audio_file(dialogue, 3, 300)
    source_audio = tmp_path / 'source.wav'
    full.extract_audio(source, source_audio, ffmpeg)
    _, clip = podcast_video_trial.read_wav(source_audio)
    mix = tmp_path / 'mix.wav'
    timeline, (start, end), clicks, total = full.build_audio_and_timeline(dialogue, source_audio, mix, 2, story.script())
    _, mixed = podcast_video_trial.read_wav(mix)
    assert mixed[:len(speech)] == speech
    assert mixed[start * 1000:start * 1000 + len(clip)] == clip
    assert all(not any(mixed[p:p+1320]) for p in clicks)
    from news_prototype import bulletin_layered_video, bulletin_remote_animation
    sound = tmp_path / 'sound.wav'
    bulletin_layered_video.write_sound(mix, sound, len(timeline[1]), clicks=clicks)
    _, padded = podcast_video_trial.read_wav(sound)
    intro_samples = combined_trial.INTRO_FRAMES * 1000
    effect = bulletin_layered_video.remote_click(24000)
    for pos in clicks:
        assert padded[intro_samples + pos:intro_samples + pos + len(effect)] == effect
        t = intro_samples / 24000 + pos / 24000 + .002
        assert bulletin_remote_animation.press_amount(t, [t]) == 1
    assert start >= math.ceil(len(speech) / 1000) + full.GAP_FRAMES
    assert end >= start + math.ceil(len(clip) / 1000)
    result = tmp_path / 'video.mp4'
    before = dialogue.read_bytes()
    story.render(source, result, ffmpeg, timeline, fullscreen=(start, end), clicks=clicks)
    assert dialogue.read_bytes() == before
    audio = subprocess.check_output([ffmpeg, '-v', 'error', '-i', str(result), '-map', '0:a:0',
                                     '-f', 's16le', '-ac', '1', '-ar', '24000', 'pipe:1'])
    decoded = array('h'); decoded.frombytes(audio)
    intro = combined_trial.INTRO_FRAMES / 24
    start_s = intro + start / 24
    def window(when):
        n = round(when * 24000)
        return decoded[n:n + 12000]
    assert tone_strength(window(intro + .3), 300) > 30 * tone_strength(window(intro + .3), 1200)
    assert tone_strength(window(start_s + .3), 1200) > 30 * tone_strength(window(start_s + .3), 300)
    frames = []
    for i, when in enumerate((intro + .6, start_s + .5, intro + end / 24 + .3)):
        target = tmp_path / f'frame-{i}.png'
        subprocess.run([ffmpeg, '-v', 'error', '-ss', str(when), '-i', str(result),
                        '-frames:v', '1', str(target)], check=True)
        with Image.open(target) as im: frames.append(im.convert('RGB').copy())
    # The former host position is occupied by the video, then studio returns.
    changed = ImageChops.difference(frames[0], frames[1]).crop((240, 165, 320, 300))
    assert sum(ImageStat.Stat(changed).mean) > 30
    restored = ImageChops.difference(frames[0], frames[2]).crop((240, 165, 320, 300))
    assert sum(ImageStat.Stat(restored).mean) < sum(ImageStat.Stat(changed).mean)
    # Outside outer TV (including its bezel) is preserved while video is shown.
    assert frames[0].getpixel((40, 60)) == frames[1].getpixel((40, 60))
    assert total <= 300


def test_missing_audio_aborts_before_output_or_tts(tmp_path):
    base = tmp_path / 'base'; base.mkdir()
    (base / 'script.json').write_text(json.dumps(story.script(), ensure_ascii=False), encoding='utf-8')
    aud = base / 'audio'; aud.mkdir(); audio_file(aud / 'dialogue.wav', 2, 300)
    source = tmp_path / 'source.mp4'; source.write_bytes(b'x')
    with patch.object(story, 'SOURCE_SIZE', 1), \
         patch.object(full.bulletin_preflight, 'check', return_value=[]), \
         patch.object(story, 'source_duration', return_value=2), \
         patch.object(full, 'has_audio', return_value=False), \
         patch.object(story.gemini_tts, 'synthesize') as tts, \
         patch.object(story, 'render') as render:
        with pytest.raises(full.NoSourceAudio): full.run(base, source, 'ffmpeg', 'ffprobe')
        assert not (base / full.FOLDER).exists()
        tts.assert_not_called(); render.assert_not_called()


def test_too_long_never_truncates(tmp_path):
    dialog = tmp_path / 'dialog.wav'; source = tmp_path / 'source.wav'
    audio_file(dialog, 2, 300); audio_file(source, 2, 1200)
    with pytest.raises(full.bulletin_timing.BulletinTooLong):
        full.build_audio_and_timeline(dialog, source, tmp_path / 'out.wav', 299, story.script())
    assert not (tmp_path / 'out.wav').exists()


def test_rerender_delivered_once_without_tts(tmp_path):
    base = tmp_path / 'base'; base.mkdir()
    (base / 'script.json').write_text(json.dumps(story.script(), ensure_ascii=False), encoding='utf-8')
    aud = base / 'audio'; aud.mkdir(); dialogue = aud / 'dialogue.wav'; audio_file(dialogue, 3, 300)
    original = dialogue.read_bytes()
    source = tmp_path / 'source.mp4'; source.write_bytes(b'x')
    def extract(_source, output, _binary): audio_file(output, 2, 1200)
    def render(_source, output, _binary, timeline, **kwargs):
        assert kwargs['fullscreen'] and len(kwargs['clicks']) == 2
        output.write_bytes(b'video')
    with patch.object(story, 'SOURCE_SIZE', 1), \
         patch.object(full.bulletin_preflight, 'check', return_value=[]), \
         patch.object(story, 'source_duration', return_value=2), \
         patch.object(full, 'has_audio', return_value=True), \
         patch.object(full, 'extract_audio', side_effect=extract), \
         patch.object(full.subprocess, 'run'), \
         patch.object(story, 'render', side_effect=render) as render_fn, \
         patch.object(full.bulletin_duo_content_probe, 'read_worker_env', return_value={}), \
         patch.object(full.bulletin_full_video_trial, 'send_video_once', return_value='sent') as send, \
         patch.object(story.gemini_tts, 'synthesize') as tts:
        result = full.run(base, source, 'ffmpeg', 'ffprobe')
        assert result.read_bytes() == b'video'
        assert dialogue.read_bytes() == original
        assert render_fn.call_count == send.call_count == 1
        tts.assert_not_called()
        with pytest.raises(FileExistsError): full.run(base, source, 'ffmpeg', 'ffprobe')
        assert render_fn.call_count == send.call_count == 1
        tts.assert_not_called()
