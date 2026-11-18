from array import array
import json
import math
import subprocess
import wave
from unittest.mock import patch

import imageio_ffmpeg
import pytest
from PIL import Image, ImageChops, ImageStat

from news_prototype import (telegram_story_overlay_trial as trial,
    telegram_story_podcast_trial as story, telegram_story_fullscreen_trial as fullscreen,
    podcast_video_trial, combined_trial)

RATE = 24000

def write(path, values):
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(RATE)
        wav.writeframes(values.tobytes())


def tone(freq, seconds, volume=5000):
    return array('h', (round(volume * math.sin(2 * math.pi * freq * i / RATE)) for i in range(round(seconds * RATE))))


def dialogue(path):
    values = array('h')
    for i in range(10):
        values.extend(tone(250 if i % 2 == 0 else 350, 1.0))
        values.extend(array('h', [0]) * int(.4 * RATE))
    write(path, values)
    return values


def comments(path):
    values = array('h', [0]) * (RATE // 2)
    values.extend(tone(500, .8))
    values.extend(array('h', [0]) * RATE)
    values.extend(tone(700, .8))
    values.extend(array('h', [0]) * (RATE // 2))
    write(path, values)
    return values


def test_pause_selection_and_missing_pause(tmp_path):
    path = tmp_path / 'dialogue.wav'
    dialogue(path)
    frame = trial.choose_pause(path, story.script())
    assert 2 * 24 < frame < 12 * 24
    rate, samples = podcast_video_trial.read_wav(path)
    assert max(abs(x) for x in samples[frame * 1000 - 100:frame * 1000 + 100]) == 0
    write(path, tone(300, 14))
    with pytest.raises(trial.NoPause): trial.choose_pause(path, story.script())


def test_duck_preserves_voice_and_reduces_background():
    source = tone(1200, 5, volume=12000)
    voice = tone(300, 1, volume=5000)
    out = trial.mix_with_ducking(source, voice, RATE * 2)
    # Original video has full loudness before and after the remarks.
    assert out[RATE//4:RATE//2] == source[RATE//4:RATE//2]
    i = 2 * RATE + RATE // 2
    # Duck strength after the attack is significantly below the original.
    background_residual = sum(abs(out[j] - voice[j - 2 * RATE]) for j in range(i, i + RATE // 10))
    background_raw = sum(abs(source[j]) for j in range(i, i + RATE // 10))
    assert background_residual < background_raw * .35
    # With extremely loud mixed inputs, clamp only background, never voice.
    loud = array('h', [32000]) * RATE
    spoken = array('h', [32000]) * RATE
    result = trial.mix_with_ducking(loud, spoken, 0)
    assert all(32000 <= x <= 32767 for x in result)


def test_combined_real_render_keeps_speech_and_plays_video_sound_under_voice(tmp_path):
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    source = tmp_path / 'source.mp4'
    subprocess.run([ff, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24',
                    '-f', 'lavfi', '-i', 'sine=frequency=1200:sample_rate=24000', '-t', '12',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)], check=True)
    original = tmp_path / 'dialogue.wav'; speech = dialogue(original); before = original.read_bytes()
    source_audio = tmp_path / 'source.wav'; fullscreen.extract_audio(source, source_audio, ff)
    comment_file = tmp_path / 'comments.wav'; new_comments = comments(comment_file)
    cut = trial.choose_pause(original, story.script())
    output_audio = tmp_path / 'timeline.wav'
    timeline, (start, end), clicks, total, clip_sec = trial.assemble(original, source_audio, comment_file,
        output_audio, cut_frame=cut, source_duration=12, data=story.script())
    _, combined = podcast_video_trial.read_wav(output_audio)
    resume = end + trial.GAP_FRAMES
    assert combined[:cut * 1000] == speech[:cut * 1000]
    assert combined[resume * 1000:resume * 1000 + len(speech) - cut * 1000] == speech[cut * 1000:]
    assert clip_sec == 10
    assert all(not any(combined[pos:pos+1320]) for pos in clicks)
    assert len(timeline[1]) == len(timeline[2])
    assert all(x is None for x in timeline[1][start:end])
    result = tmp_path / 'video.mp4'
    story.render(source, result, ff, timeline, fullscreen=(start, end), clicks=clicks)
    assert original.read_bytes() == before
    subprocess.run([ff, '-v', 'error', '-xerror', '-i', str(result), '-f', 'null', '-'], check=True)
    pcm = subprocess.check_output([ff, '-v', 'error', '-i', str(result), '-map', '0:a:0',
                                   '-f', 's16le', '-ac', '1', '-ar', str(RATE), 'pipe:1'])
    decoded = array('h'); decoded.frombytes(pcm)
    at = (combined_trial.INTRO_FRAMES + start) * 1000 + RATE + RATE//2 + RATE//4
    sample = decoded[at:at + RATE // 2]
    def component(freq):
        return math.hypot(sum(v * math.cos(2 * math.pi * freq * i / RATE) for i, v in enumerate(sample)),
                          sum(v * math.sin(2 * math.pi * freq * i / RATE) for i, v in enumerate(sample)))
    assert component(500) > component(1200) * 2
    assert component(1200) > component(800) * 3  # Source audio remains audible beneath the comment.
    frame_files = []
    for n, when in enumerate(((combined_trial.INTRO_FRAMES + start) / 24 + 2,
                              (combined_trial.INTRO_FRAMES + end) / 24 + .3)):
        target = tmp_path / f'frame-{n}.png'
        subprocess.run([ff, '-v', 'error', '-ss', str(when), '-i', str(result), '-frames:v', '1', str(target)], check=True)
        with Image.open(target) as im: frame_files.append(im.convert('RGB').crop((240, 165, 320, 300)))
    assert sum(ImageStat.Stat(ImageChops.difference(*frame_files)).mean) > 30
    # The overlaid section includes both independent signals, not just speech.
    voice_point = (start * 1000) + RATE + RATE//2 + RATE//4
    original_src = podcast_video_trial.read_wav(source_audio)[1]
    assert any(combined[voice_point + j] != new_comments[RATE//2 + RATE//4 + j] for j in range(1000))
    assert any(original_src[RATE + RATE//2 + RATE//4:RATE + RATE//2 + RATE//4 + 1000])
    assert total < 300


def test_tts_is_one_joint_request(tmp_path):
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    def synth(_text, path, keys, **kwargs):
        assert keys == ['key']
        assert 'request_fn' in kwargs
        comments(path)
    with patch.object(trial.gemini_tts, 'synthesize', side_effect=synth) as tts:
        path = trial.generate_comments(tmp_path / 'new', ff, ['key'])
        assert path.is_file() and tts.call_count == 1
        with pytest.raises(FileExistsError): trial.generate_comments(tmp_path / 'new', ff, ['key'])
        assert tts.call_count == 1
    parts = trial.bulletin_dialogue_audio.request_body(trial.COMMENTS)['contents'][0]['parts']
    assert [part['speechMetadata']['speaker'] for part in parts] == ['Bender', 'Sensei']
    assert [part['text'] for part in parts] == ['Ну вы гляньте, как это смотрится!', 'Да, залипательно.']


def test_missing_source_audio_stops_before_tts(tmp_path):
    base = tmp_path / 'base'; base.mkdir()
    (base / 'script.json').write_text(json.dumps(story.script(), ensure_ascii=False), encoding='utf-8')
    audio = base / 'audio'; audio.mkdir(); dialogue(audio / 'dialogue.wav')
    source = tmp_path / 'source.mp4'; source.write_bytes(b'x')
    with patch.object(story, 'SOURCE_SIZE', 1), patch.object(trial.bulletin_preflight, 'check', return_value=[]), \
         patch.object(story, 'source_duration', return_value=53), patch.object(fullscreen, 'has_audio', return_value=False), \
         patch.object(trial, 'generate_comments') as gen:
        with pytest.raises(fullscreen.NoSourceAudio): trial.run(base, source, 'ffmpeg', 'ffprobe')
        gen.assert_not_called()
        assert not (base / trial.FOLDER).exists()


def test_run_synthesizes_once_delivers_once_and_keeps_existing_audio(tmp_path):
    base = tmp_path / 'base'; base.mkdir()
    (base / 'script.json').write_text(json.dumps(story.script(), ensure_ascii=False), encoding='utf-8')
    audio = base / 'audio'; audio.mkdir(); existing = audio / 'dialogue.wav'; dialogue(existing)
    before = existing.read_bytes()
    source = tmp_path / 'source.mp4'; source.write_bytes(b'x')
    def extract(_source, path, _ff): write(path, tone(1200, 12))
    def generate(folder, _ff, keys):
        assert keys == ['key']; folder.mkdir(); path = folder / 'comments.wav'; comments(path); return path
    def render(_source, path, _ff, timeline, **options):
        assert options['fullscreen'] and len(options['clicks']) == 2
        path.write_bytes(b'video')
    with patch.object(story, 'SOURCE_SIZE', 1), patch.object(trial.bulletin_preflight, 'check', return_value=[]), \
         patch.object(story, 'source_duration', return_value=12), patch.object(fullscreen, 'has_audio', return_value=True), \
         patch.object(fullscreen, 'extract_audio', side_effect=extract), \
         patch.object(trial.bulletin_duo_content_probe, 'read_worker_env', return_value={}), \
         patch.object(trial.bulletin_duo_content_probe, 'worker_keys', return_value=['key']), \
         patch.object(trial, 'generate_comments', side_effect=generate) as gen, \
         patch.object(story, 'render', side_effect=render) as render_fn, \
         patch.object(trial.subprocess, 'run'), \
         patch.object(trial.bulletin_full_video_trial, 'send_video_once', return_value='sent') as send:
        assert trial.run(base, source, 'ffmpeg', 'ffprobe').read_bytes() == b'video'
        assert existing.read_bytes() == before
        assert gen.call_count == render_fn.call_count == send.call_count == 1
        with pytest.raises(FileExistsError): trial.run(base, source, 'ffmpeg', 'ffprobe')
        assert gen.call_count == render_fn.call_count == send.call_count == 1


def test_no_pause_fails_before_any_new_tts(tmp_path):
    base = tmp_path / 'base'; base.mkdir()
    (base / 'script.json').write_text(json.dumps(story.script(), ensure_ascii=False), encoding='utf-8')
    audio = base / 'audio'; audio.mkdir(); write(audio / 'dialogue.wav', tone(300, 14))
    source = tmp_path / 'source.mp4'; source.write_bytes(b'x')
    with patch.object(story, 'SOURCE_SIZE', 1), patch.object(trial.bulletin_preflight, 'check', return_value=[]), \
         patch.object(story, 'source_duration', return_value=53), patch.object(fullscreen, 'has_audio', return_value=True), \
         patch.object(trial, 'generate_comments') as gen:
        with pytest.raises(trial.NoPause): trial.run(base, source, 'ffmpeg', 'ffprobe')
        gen.assert_not_called()
        assert not (base / trial.FOLDER).exists()
