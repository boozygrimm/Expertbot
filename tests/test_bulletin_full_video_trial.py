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

from news_prototype import bulletin_animation, bulletin_wide_screen, bulletin_full_video_trial as trial, combined_trial, podcast_audio_trial, podcast_video_trial
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
             patch.object(trial, 'send_video_once', return_value='sent') as send, \
             patch.object(combined_trial, 'display', wraps=combined_trial.display) as old_display, \
             patch.object(combined_trial, 'write_sound', wraps=combined_trial.write_sound) as old_sound, \
             patch.object(bulletin_wide_screen, 'compose', wraps=bulletin_wide_screen.compose) as compose:
            output = trial.run(job, base, ffmpeg)
            assert output.is_file()
            send.assert_called_once()
            old_sound.assert_called_once()
            # In the no-pause second story, both presenters are actually
            # composited with their talking layers; speech is not regenerated.
            layers = bulletin_animation.frames()
            calls = [c for c in compose.call_args_list if c.kwargs.get('fallback_position') == 1]
            for state in ((True, False, False), (False, False, True)):
                assert any(all(a is b for a, b in zip(c.kwargs['speaker_layers'], layers[state])) for c in calls)
            times = [call.args[3] for call in old_display.call_args_list]
            assert any(.95 < t < 1.2 for t in times)
            assert any(t > 8.7 for t in times)
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
        screenshots, presenter = [], []
        for when in (.1, 1.4, 9.3):
            target = root / f'power-{when}.png'
            subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-ss', str(when), '-i', str(output),
                            '-frames:v', '1', str(target)], check=True)
            with Image.open(target) as frame:
                frame = frame.convert('RGB')
                screenshots.append(frame.getpixel((450, 140)))
                presenter.append(frame.getpixel((270, 225)))
        assert sum(screenshots[1]) > sum(screenshots[0]) + 30
        assert sum(screenshots[1]) > sum(screenshots[2]) + 30
        assert sum(presenter[1]) > sum(presenter[0]) + 30
        assert sum(presenter[1]) > sum(presenter[2]) + 30
        # The first switch occurs at 5.10 s with the click/press just before it.
        before, pressed = root / 'before.png', root / 'pressed.png'
        for when, target in [(4.7, before), (5.02, pressed)]:
            subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-ss', str(when), '-i', str(output),
                            '-frames:v', '1', str(target)], check=True)
        from PIL import ImageChops
        with Image.open(before) as a, Image.open(pressed) as b:
            diff = ImageChops.difference(a.convert('RGB'), b.convert('RGB')).crop((720, 385, 760, 415))
            assert diff.getbbox() is not None


def test_timeline_estimates_ambiguous_section_and_rejects_changed_audio():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); a = root / 'a.wav'; b = root / 'b.wav'
        segment(a); segment(b, False)
        joined = root / 'dialogue.wav'; podcast_audio_trial.assemble_wav([a,b], joined)
        turns = [{'speaker':'Бендер','text':'Один.'}, {'speaker':'Киберсенсей','text':'Двааа.'}]
        sections = [{'id':1,'turns':turns},{'id':2,'turns':turns}]
        timeline, notes = trial.build_timeline(joined, [(1,a),(2,b)], sections)
        assert all('приблизительные' in note for note in notes)
        assert any(s == 'Бендер' for s in timeline[1]) and any(s == 'Киберсенсей' for s in timeline[1])
        second = slice(math.ceil(3.35 * 24), None)
        for speaker in ('Бендер', 'Киберсенсей'):
            assert any(name == speaker and opened for name, opened in zip(timeline[1][second], timeline[2][second]))
        segment(b)
        with pytest.raises(ValueError): trial.build_timeline(joined, [(1,a),(2,b)], sections)


def test_multi_turn_estimates_and_known_video_cut():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); part = root / 'part.wav'
        samples = segment(part, False)
        joined = root / 'dialogue.wav'; podcast_audio_trial.assemble_wav([part], joined)
        turns = [{'speaker': 'Бендер', 'text': 'а' * 60},
                 {'speaker': 'Киберсенсей', 'text': 'б' * 10},
                 {'speaker': 'Бендер', 'text': 'в' * 10},
                 {'speaker': 'Киберсенсей', 'text': 'г' * 20}]
        sections = [{'id': 1, 'turns': turns}]
        timeline, _ = trial.build_timeline(joined, [(1, part)], sections,
                                           group_cuts={1: (2, len(samples) // 2)})
        active, states = timeline[1:]
        assert active[round(1.45 * 24)] == 'Киберсенсей'
        assert active[round(1.55 * 24)] == 'Бендер'
        assert active[round(2.8 * 24)] == 'Киберсенсей'
        assert all(any(name == speaker and opened for name, opened in zip(active[start:end], states[start:end]))
                   for speaker, start, end in [('Бендер', 0, 35), ('Киберсенсей', 30, 36),
                                               ('Бендер', 36, 48), ('Киберсенсей', 48, 72)])
        with pytest.raises(ValueError):
            trial.build_timeline(joined, [(1, part)], sections, group_cuts={1: (4, 100)})


def test_zero_audio_and_same_speaker_are_safe():
    turns = [{'speaker': 'Бендер', 'text': 'один'}, {'speaker': 'Киберсенсей', 'text': 'два'}]
    ranges = trial.approximate_ranges(array('h', [0] * 24000), turns, 24000)
    assert ranges == [(0, 12000, 'Бендер'), (12000, 24000, 'Киберсенсей')]
    assert trial.approximate_ranges(array('h', [0] * 100), turns[:1], 24000) == [(0, 100, 'Бендер')]
