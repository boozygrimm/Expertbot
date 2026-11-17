import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import uuid

import imageio_ffmpeg
import pytest
from news_prototype import bulletin_full_dialogue_trial as trial, bulletin_dialogue_audio as audio
from tests.test_bulletin_dialogue_audio import payload, wav


def make_job(base):
    job = base / str(uuid.uuid4())
    job.mkdir()
    request = job / 'request.json'
    original = json.dumps({'bulletin': payload(2)}, ensure_ascii=False)
    request.write_text(original, encoding='utf-8')
    return job, request, original


def test_audio_only_trial_uses_one_request_per_story_and_one_send(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        job, request, original = make_job(root)
        folder = root / 'new-trial'
        with patch.object(trial.bulletin_preflight, 'check', return_value=[]), \
             patch.object(trial.bulletin_duo_content_probe, 'read_worker_env', return_value={}), \
             patch.object(trial.bulletin_duo_content_probe, 'worker_keys', return_value=['one', 'two']), \
             patch.object(audio, 'request_one', return_value=(wav(), 1.0)) as tts, \
             patch.object(trial.bulletin_duo_content_probe, 'send_once', return_value='sent') as send:
            trial.run(job, folder, imageio_ffmpeg.get_ffmpeg_exe())
            assert tts.call_count == 2
            send.assert_called_once()
            assert (folder / 'audio' / 'dialogue.wav').is_file()
            assert json.loads((folder / 'bulletin.json').read_text()) == payload(2)
            assert request.read_text(encoding='utf-8') == original
            with pytest.raises(FileExistsError):
                trial.run(job, folder, imageio_ffmpeg.get_ffmpeg_exe())
            assert tts.call_count == 2
            send.assert_called_once()
        output = capsys.readouterr().out
        assert 'Новость 1: 0.00–1.00 с' in output
        assert 'Новость 2: 1.35–2.35 с' in output


def test_too_long_never_delivers_and_does_not_retry():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        job, _, _ = make_job(root)
        folder = root / 'trial'
        with patch.object(trial.bulletin_preflight, 'check', return_value=[]), \
             patch.object(trial.bulletin_duo_content_probe, 'read_worker_env', return_value={}), \
             patch.object(trial.bulletin_duo_content_probe, 'worker_keys', return_value=['one']), \
             patch.object(audio, 'request_one', return_value=(wav(), 1)) as tts, \
             patch.object(trial.bulletin_timing, 'section_windows', side_effect=trial.bulletin_timing.BulletinTooLong('too long')), \
             patch.object(trial.bulletin_duo_content_probe, 'send_once') as send:
            with pytest.raises(trial.bulletin_timing.BulletinTooLong):
                trial.run(job, folder, imageio_ffmpeg.get_ffmpeg_exe())
            assert (folder / 'audio' / 'dialogue.wav').exists()
            send.assert_not_called()
            with pytest.raises(FileExistsError):
                trial.run(job, folder, imageio_ffmpeg.get_ffmpeg_exe())
            # The duration guard is now checked after every completed item.
            assert tts.call_count == 1
            assert not (folder / 'audio' / '2-normalized.wav').exists()
