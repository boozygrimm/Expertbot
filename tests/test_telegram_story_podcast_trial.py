from array import array
import math
import shutil
import subprocess
import wave
from unittest.mock import patch

import imageio_ffmpeg
import pytest
from PIL import Image, ImageChops

from news_prototype import (telegram_story_podcast_trial as trial, podcast_audio_trial,
                            podcast_video_trial, combined_trial)


def make_audio(path, seconds, freq=300):
    rate = podcast_audio_trial.RATE
    samples = array('h', (round(4000 * math.sin(2 * math.pi * freq * i / rate)) for i in range(rate * seconds)))
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate); out.writeframes(samples.tobytes())


def test_script_and_request_are_one_continuous_ten_turn_dialogue():
    data = trial.script()
    assert [turn['text'] for turn in data['turns']] == trial.TEXT
    assert [t['speaker'] for t in data['turns']] == ['Бендер', 'Киберсенсей'] * 5
    body = trial.bulletin_dialogue_audio.request_body(data)
    assert len(body['contents'][0]['parts']) == 10
    assert [p['speechMetadata']['speaker'] for p in body['contents'][0]['parts']] == ['Bender', 'Sensei'] * 5
    voices = body['generationConfig']['speechConfig']['multiSpeakerVoiceConfig']['speakerVoiceConfigs']
    assert [x['voiceConfig']['prebuiltVoiceConfig']['voiceName'] for x in voices] == ['Algenib', 'Puck']


def test_boundaries_are_ordered_and_near_real_silences():
    turns = [{'speaker': 'Бендер' if n % 2 == 0 else 'Киберсенсей', 'text': 'Одинаковая реплика.'} for n in range(10)]
    levels = []
    for i in range(10):
        levels += [500.] * 24
        if i < 9: levels += [0.] * 8
    borders = trial.estimate_boundaries(levels, turns)
    assert len(borders) == 9 and borders == sorted(borders)
    assert all(abs(pos - (24 + n * 32 + 4)) <= 1 for n, pos in enumerate(borders))
    assert len(trial.estimate_boundaries([500.] * 1000, turns)) == 9


def test_one_request_and_no_rerequest_in_existing_folder(tmp_path):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    def synthesize(text, output, keys, **kw):
        assert keys == ['key'] and 'request_fn' in kw
        assert kw['budget'] == 240
        make_audio(output, 26)
    with patch.object(trial.gemini_tts, 'synthesize', side_effect=synthesize) as gen:
        output = trial.prepare_audio(trial.script(), tmp_path / 'audio', ffmpeg, ['key'])
        assert output.exists()
        assert len(podcast_video_trial.read_wav(output)[1]) == 26 * 24000
        assert gen.call_count == 1
        with pytest.raises(FileExistsError): trial.prepare_audio(trial.script(), tmp_path / 'audio', ffmpeg, ['key'])
        assert gen.call_count == 1


def test_full_video_not_truncated_speech_or_source_audio(tmp_path):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    ffprobe = shutil.which('ffprobe')
    source = tmp_path / 'source.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24',
                    '-f', 'lavfi', '-i', 'sine=frequency=1700:sample_rate=24000', '-t', '14',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)], check=True)
    if ffprobe:
        assert trial.source_duration(source, ffprobe) == pytest.approx(14, abs=.1)
    audio = tmp_path / 'speech.wav'; make_audio(audio, 2)
    original = audio.read_bytes()
    timeline, boundaries, total = trial.build_timeline(audio, trial.script(), 14)
    assert len(timeline[1]) == 14 * 24
    assert total == pytest.approx(14 + (42 + 45) / 24)
    result = tmp_path / 'output.mp4'
    with patch.object(combined_trial, 'display', wraps=combined_trial.display) as display:
        trial.render(source, result, ffmpeg, timeline)
    assert audio.read_bytes() == original
    assert any(c.args[3] < 1.0 for c in display.call_args_list)
    assert any(c.args[3] > 16.0 for c in display.call_args_list)
    output = subprocess.check_output([ffmpeg, '-v', 'error', '-i', str(result), '-map', '0:a:0',
                                      '-f', 's16le', '-ac', '1', '-ar', '24000', 'pipe:1'])
    decoded = array('h'); decoded.frombytes(output)
    rate = 24000; start = round((42 / 24 + .2) * rate); window = decoded[start:start + rate]
    def power(freq):
        re = sum(x * math.cos(2 * math.pi * freq * i / rate) for i, x in enumerate(window))
        im = sum(x * math.sin(2 * math.pi * freq * i / rate) for i, x in enumerate(window))
        return math.hypot(re, im)
    assert power(300) > 100 * power(1700)
    # Frames after 12 seconds still come from the moving source, not a loop or freeze.
    images = []
    for n, when in enumerate((14.0, 15.0)):
        p = tmp_path / f'{n}.png'
        subprocess.run([ffmpeg, '-v', 'error', '-ss', str(when), '-i', str(result), '-frames:v', '1', str(p)], check=True)
        with Image.open(p) as img: images.append(img.convert('RGB').copy())
    assert ImageChops.difference(images[0], images[1]).crop((335, 130, 550, 280)).getbbox()
    with pytest.raises(ValueError): trial.render(source, result, ffmpeg, timeline)


def test_legacy_gestures_are_adapted_and_present():
    assert trial.hatch_frame(24).getbbox()
    assert trial.hedgehog_frame(24, 8).getbbox()
    bbox = trial.hatch_frame(24).getbbox()
    assert 570 < bbox[0] < bbox[2] < 680 and 120 < bbox[1] < bbox[3] < 230


def test_existing_folder_never_requests_tts(tmp_path):
    source = tmp_path / 'source.mp4'; source.write_bytes(b'x')
    with patch.object(trial, 'SOURCE_SIZE', 1), patch.object(trial.bulletin_preflight, 'check', return_value=[]), \
         patch.object(trial.bulletin_duo_content_probe, 'read_worker_env', return_value={}), \
         patch.object(trial.bulletin_duo_content_probe, 'worker_keys', return_value=['key']), \
         patch.object(trial.shutil, 'which', return_value='ffprobe'), \
         patch.object(trial, 'source_duration', return_value=53), \
         patch.object(trial, 'prepare_audio') as prepare:
        with pytest.raises(FileExistsError): trial.run(tmp_path, source, 'ffmpeg')
        prepare.assert_not_called()


def test_run_one_synthesis_one_render_one_owner_delivery(tmp_path):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    source = tmp_path / 'source.mp4'; source.write_bytes(b'x')
    def prepare(data, folder, binary, keys):
        folder.mkdir(); path = folder / 'dialogue.wav'; make_audio(path, 26)
        return path
    def render(source, output, binary, timeline):
        output.write_bytes(b'video')
        assert len(timeline[1]) == 53 * 24
    def check(*args, **kwargs): pass
    with patch.object(trial, 'SOURCE_SIZE', 1), patch.object(trial.bulletin_preflight, 'check', return_value=[]), \
         patch.object(trial.bulletin_duo_content_probe, 'read_worker_env', return_value={}), \
         patch.object(trial.bulletin_duo_content_probe, 'worker_keys', return_value=['key']), \
         patch.object(trial.shutil, 'which', return_value='ffprobe'), \
         patch.object(trial, 'source_duration', return_value=53), \
         patch.object(trial, 'prepare_audio', side_effect=prepare) as gen, \
         patch.object(trial, 'render', side_effect=render) as create, \
         patch.object(trial.subprocess, 'run', side_effect=check), \
         patch.object(trial.bulletin_full_video_trial, 'send_video_once', return_value='sent') as send:
        trial.run(tmp_path / 'new', source, ffmpeg)
        assert gen.call_count == create.call_count == send.call_count == 1
        assert (tmp_path / 'new/video.mp4').read_bytes() == b'video'
        with pytest.raises(FileExistsError): trial.run(tmp_path / 'new', source, ffmpeg)
        assert gen.call_count == create.call_count == send.call_count == 1
