import json
import math
from array import array
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import Mock, patch
import uuid
import wave

import imageio_ffmpeg
from PIL import Image
import pytest

from news_prototype import bulletin_full_video_trial as trial, podcast_audio_trial, podcast_video_trial
from tests.test_bulletin_pipeline import payload as picture_payload


def segment(path, with_pause=True):
    rate = podcast_audio_trial.RATE
    samples = array('h')
    for i in range(rate * 3):
        silent = with_pause and int(rate * 1.3) <= i < int(rate * 1.7)
        samples.append(0 if silent else round(10000 * math.sin(2 * math.pi * 200 * i / rate)))
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate); out.writeframes(samples.tobytes())
    return samples


def test_pause_heuristic_and_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        samples = segment(Path(tmp) / 'part.wav')
        turns = [{'speaker':'Бендер','text':'Один.'},{'speaker':'Киберсенсей','text':'Двааа.'}]
        estimate = trial.candidate_split(samples, turns)
        assert estimate == pytest.approx(1.5, abs=.1)
        assert trial.candidate_split(segment(Path(tmp) / 'no.wav', False), turns) is None
        assert trial.candidate_split(samples, turns + turns[:1]) is None


def test_real_synthetic_render_uses_existing_sound_and_pictures():
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    data = picture_payload()
    for section in data['sections']:
        section['turns'] = [{'speaker':'Бендер','text':'Первая реплика.'},
                            {'speaker':'Киберсенсей','text':'Вторая реплика.'}]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); job = root / str(uuid.uuid4()); job.mkdir()
        (job / 'request.json').write_text(json.dumps({'bulletin':data}, ensure_ascii=False), encoding='utf-8')
        base = root / 'trial'; base.mkdir(); audio_dir = base / 'audio'; audio_dir.mkdir()
        (base / 'bulletin.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        parts = [audio_dir / '1-normalized.wav', audio_dir / '2-normalized.wav']
        segment(parts[0]); segment(parts[1], False)
        podcast_audio_trial.assemble_wav(parts, audio_dir / 'dialogue.wav')
        before = (audio_dir / 'dialogue.wav').read_bytes()
        with patch.object(trial.bulletin_preflight, 'check', return_value=[]), \
             patch.object(trial.bulletin_duo_content_probe, 'read_worker_env', return_value={}), \
             patch.object(trial, 'send_video_once', return_value='sent') as send:
            output = trial.run(job, base, ffmpeg)
            assert output.is_file()
            send.assert_called_once()
            assert (audio_dir / 'dialogue.wav').read_bytes() == before
            with pytest.raises(FileExistsError):
                trial.run(job, base, ffmpeg)
            send.assert_called_once()
        for when, expected in [(2.4, 'red'), (5.7, 'blue')]:
            image = root / f'{expected}.png'
            subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-ss', str(when), '-i', str(output),
                            '-frames:v', '1', str(image)], check=True)
            with Image.open(image) as frame:
                r, _, b = frame.convert('RGB').getpixel((450, 140))
                assert r > b + 60 if expected == 'red' else b > r + 60
        # Power animation is visible before and after the complete dialogue.
        screenshots = []
        for when in (.1, 1.4, 9.3):
            target = root / f'power-{when}.png'
            subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-ss', str(when), '-i', str(output),
                            '-frames:v', '1', str(target)], check=True)
            with Image.open(target) as frame:
                screenshots.append(frame.convert('RGB').getpixel((450, 140)))
        assert sum(screenshots[1]) > sum(screenshots[0]) + 30
        assert sum(screenshots[1]) > sum(screenshots[2]) + 30
        # The first switch occurs at 5.10 s with the click/press just before it.
        before, pressed = root / 'before.png', root / 'pressed.png'
        for when, target in [(4.7, before), (5.02, pressed)]:
            subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-ss', str(when), '-i', str(output),
                            '-frames:v', '1', str(target)], check=True)
        from PIL import ImageChops
        with Image.open(before) as a, Image.open(pressed) as b:
            diff = ImageChops.difference(a.convert('RGB'), b.convert('RGB')).crop((720, 385, 760, 415))
            assert diff.getbbox() is not None


def test_timeline_closes_mouths_for_ambiguous_section_and_rejects_changed_audio():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); a = root / 'a.wav'; b = root / 'b.wav'
        segment(a); segment(b, False)
        joined = root / 'dialogue.wav'; podcast_audio_trial.assemble_wav([a,b], joined)
        turns = [{'speaker':'Бендер','text':'Один.'}, {'speaker':'Киберсенсей','text':'Двааа.'}]
        sections = [{'id':1,'turns':turns},{'id':2,'turns':turns}]
        timeline, notes = trial.build_timeline(joined, [(1,a),(2,b)], sections)
        assert 'предположительная' in notes[0] and 'неподвижны' in notes[1]
        assert any(s == 'Бендер' for s in timeline[1]) and any(s == 'Киберсенсей' for s in timeline[1])
        assert all(s is None for s in timeline[1][math.ceil(3.35 * 24):])
        segment(b)
        with pytest.raises(ValueError): trial.build_timeline(joined, [(1,a),(2,b)], sections)
