from array import array
import json
import math
import subprocess
import wave
from unittest.mock import patch

import imageio_ffmpeg
from PIL import Image, ImageChops, ImageStat
import pytest

from news_prototype import (telegram_story_continuous_trial as trial,
    telegram_story_overlay_trial as overlay, telegram_story_podcast_trial as story,
    podcast_video_trial, combined_trial)

RATE = 24000


def wav(path, samples):
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(RATE); out.writeframes(samples.tobytes())


def make_dialogue(path):
    samples = array('h')
    for n in range(10):
        samples.extend(round(5000 * math.sin(2 * math.pi * (250 + 100 * (n % 2)) * i / RATE)) for i in range(RATE))
        samples.extend([0] * 9600)
    wav(path, samples)
    return samples


def test_audio_uses_actual_position_and_wraps_at_video_duration(tmp_path):
    dialogue = tmp_path / 'dialogue.wav'; original = make_dialogue(dialogue)
    source = tmp_path / 'source.wav'
    source_values = array('h', (1000 + 500 * (i // RATE) for i in range(12 * RATE)))
    wav(source, source_values)
    comment = tmp_path / 'comments.wav'; wav(comment, array('h', [5000]) * RATE)
    cut = overlay.choose_pause(dialogue, story.script())
    with patch.object(overlay, 'mix_with_ducking', side_effect=lambda src, voice, offset: src):
        timeline, window, _, _, _ = overlay.assemble(dialogue, source, comment, tmp_path / 'new.wav',
            cut_frame=cut, source_duration=12, data=story.script(), continuous_video=True)
    _, samples = podcast_video_trial.read_wav(timeline[0])
    start, end = window
    period = 12 * RATE
    offset = (start * 1000) % period
    expected = array('h', (source_values[(offset + i) % period] for i in range(10 * RATE)))
    assert samples[start * 1000:start * 1000 + 10 * RATE] == expected
    resume = end + overlay.GAP_FRAMES
    assert samples[:cut * 1000] == original[:cut * 1000]
    assert samples[resume * 1000:resume * 1000 + len(original) - cut * 1000] == original[cut * 1000:]


def test_video_continues_at_fullscreen_transition_not_source_start(tmp_path):
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    source = tmp_path / 'source.mp4'
    subprocess.run([ff, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24',
                    '-t', '7', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)], check=True)
    audio = tmp_path / 'audio.wav'
    wav(audio, array('h', (round(3000 * math.sin(2 * math.pi * 300 * i / RATE)) for i in range(RATE * 6))))
    result = tmp_path / 'video.mp4'
    timeline = (audio, ['Бендер'] * (6 * 24), [True] * (6 * 24))
    story.render(source, result, ff, timeline, fullscreen=(48, 96), continuous_video=True)
    output_image = tmp_path / 'out.png'
    when = combined_trial.INTRO_FRAMES / 24 + 2.5
    subprocess.run([ff, '-v', 'error', '-ss', str(when), '-i', str(result), '-frames:v', '1', str(output_image)], check=True)
    refs = []
    for i, t in enumerate((2.5, .5)):
        path = tmp_path / f'ref-{i}.png'
        subprocess.run([ff, '-v', 'error', '-ss', str(t), '-i', str(source), '-frames:v', '1', str(path)], check=True)
        with Image.open(path) as image:
            refs.append(image.convert('RGB').resize((704, 396), Image.Resampling.LANCZOS))
    with Image.open(output_image) as image:
        # Central TV region excludes rounded edges, controls, and the hand.
        observed = image.convert('RGB').crop((210, 120, 690, 380))
    comparisons = []
    for ref in refs:
        region = ref.crop((100, 53, 580, 313))
        comparisons.append(sum(ImageStat.Stat(ImageChops.difference(observed, region)).mean))
    assert comparisons[0] < comparisons[1] * .55, comparisons


def test_reuses_comments_and_exact_timing_once(tmp_path):
    base = tmp_path / 'base'; base.mkdir()
    (base / 'script.json').write_text(json.dumps(story.script(), ensure_ascii=False), encoding='utf-8')
    aud = base / 'audio'; aud.mkdir(); dialogue = aud / 'dialogue.wav'; make_dialogue(dialogue)
    previous = base / overlay.FOLDER; previous.mkdir()
    (previous / 'comments.json').write_text(json.dumps(overlay.COMMENTS, ensure_ascii=False), encoding='utf-8')
    cdir = previous / 'comments'; cdir.mkdir(); comments = cdir / 'comments.wav'
    wav(comments, array('h', [5000]) * RATE)
    old_comments = comments.read_bytes(); old_dialogue = dialogue.read_bytes()
    source_audio = previous / 'source-audio.wav'
    wav(source_audio, array('h', (1000 + 500 * (i // RATE) for i in range(12 * RATE))))
    cut = overlay.choose_pause(dialogue, story.script())
    overlay.assemble(dialogue, source_audio, comments, previous / 'timeline.wav', cut_frame=cut,
                     source_duration=12, data=story.script())
    source = tmp_path / 'source.mp4'; source.write_bytes(b'x')
    def render(_src, output, _ff, _timeline, **kwargs):
        assert kwargs['continuous_video'] is True
        output.write_bytes(b'video')
    with patch.object(story, 'SOURCE_SIZE', 1), patch.object(trial.bulletin_preflight, 'check', return_value=[]), \
         patch.object(story, 'source_duration', return_value=12), \
         patch.object(story, 'render', side_effect=render) as create, \
         patch.object(trial.subprocess, 'run'), \
         patch.object(trial.bulletin_duo_content_probe, 'read_worker_env', return_value={}), \
         patch.object(trial.bulletin_full_video_trial, 'send_video_once', return_value='sent') as send, \
         patch.object(overlay, 'generate_comments') as gen:
        assert trial.run(base, source, 'ffmpeg', 'ffprobe').read_bytes() == b'video'
        assert old_comments == comments.read_bytes() and old_dialogue == dialogue.read_bytes()
        assert create.call_count == send.call_count == 1
        gen.assert_not_called()
        with pytest.raises(FileExistsError): trial.run(base, source, 'ffmpeg', 'ffprobe')
        assert create.call_count == send.call_count == 1
        gen.assert_not_called()


def test_modified_prior_timeline_stops_before_render(tmp_path):
    base = tmp_path / 'base'; base.mkdir()
    (base / 'script.json').write_text(json.dumps(story.script(), ensure_ascii=False), encoding='utf-8')
    aud = base / 'audio'; aud.mkdir(); dialogue = aud / 'dialogue.wav'; make_dialogue(dialogue)
    previous = base / overlay.FOLDER; previous.mkdir()
    (previous / 'comments.json').write_text(json.dumps(overlay.COMMENTS, ensure_ascii=False), encoding='utf-8')
    cdir = previous / 'comments'; cdir.mkdir(); comments = cdir / 'comments.wav'
    wav(comments, array('h', [5000]) * RATE)
    source_audio = previous / 'source-audio.wav'; wav(source_audio, array('h', [3000]) * (12 * RATE))
    cut = overlay.choose_pause(dialogue, story.script())
    old = previous / 'timeline.wav'
    overlay.assemble(dialogue, source_audio, comments, old, cut_frame=cut, source_duration=12, data=story.script())
    _, changed = podcast_video_trial.read_wav(old); changed[0] = 9999; wav(old, changed)
    source = tmp_path / 'source.mp4'; source.write_bytes(b'x')
    with patch.object(story, 'SOURCE_SIZE', 1), patch.object(trial.bulletin_preflight, 'check', return_value=[]), \
         patch.object(story, 'source_duration', return_value=12), \
         patch.object(story, 'render') as render, \
         patch.object(trial.bulletin_full_video_trial, 'send_video_once') as send, \
         patch.object(overlay, 'generate_comments') as gen:
        with pytest.raises(trial.TimingMismatch): trial.run(base, source, 'ffmpeg', 'ffprobe')
        render.assert_not_called(); send.assert_not_called(); gen.assert_not_called()
