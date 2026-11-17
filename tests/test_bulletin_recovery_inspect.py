import json
from pathlib import Path
import tempfile
import unittest
import uuid
import wave
from unittest.mock import patch

from news_prototype import bulletin_recovery_inspect as check


def wav(path, *, frames=2400, rate=24000, silent=False):
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(rate)
        f.writeframes((b'\0\0' if silent else b'\1\0') * frames)


class InspectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.job = Path(self.tmp.name) / str(uuid.uuid4()); self.job.mkdir()
        self.work = self.job / 'work'; self.work.mkdir()
        self.turns = [{'speaker': 'Бендер', 'text': 'Текст 1'},
                      {'speaker': 'Киберсенсей', 'text': 'Текст 2'},
                      {'speaker': 'Бендер', 'text': 'Текст 3'}]
        bulletin = {'sections': [{'id': 1, 'ticker': 'Первая', 'turns': self.turns[:2]},
                                  {'id': 2, 'ticker': 'Вторая', 'turns': self.turns[2:]}]}
        (self.job/'request.json').write_text(json.dumps({'bulletin': bulletin}), encoding='utf8')
        (self.job/'failed').write_text('failed')
        (self.work/'script.json').write_text(json.dumps({'turns': self.turns, 'ticker': 'Первая'}), encoding='utf8')
        for index in (1, 2):
            wav(self.work/f'{index}-raw.wav'); wav(self.work/f'{index}-normalized.wav')

    def test_inspection_does_not_write_or_request_tts(self):
        paths = {p: p.read_bytes() for p in self.job.rglob('*') if p.is_file()}
        with patch('video_prototype.gemini_tts.synthesize') as tts:
            result = check.inspect(self.job)
        tts.assert_not_called()
        self.assertEqual((result.turns, result.complete_pairs), (3, 2))
        self.assertAlmostEqual(result.seconds, 0.2)
        self.assertEqual(paths, {p: p.read_bytes() for p in self.job.rglob('*') if p.is_file()})

    def test_inconsistent_script_and_missing_marker_rejected(self):
        (self.work/'script.json').write_text('{}')
        with self.assertRaises(check.InspectionError): check.inspect(self.job)
        (self.job/'failed').unlink()
        with self.assertRaises(check.InspectionError): check.inspect(self.job)

    def test_gaps_silence_incomplete_and_symlink_rejected(self):
        (self.work/'1-normalized.wav').unlink()
        with self.assertRaises(check.InspectionError): check.inspect(self.job)
        wav(self.work/'1-normalized.wav', silent=True)
        with self.assertRaises(check.InspectionError): check.inspect(self.job)
        (self.work/'1-normalized.wav').unlink()
        (self.work/'1-normalized.wav').symlink_to(self.work/'1-raw.wav')
        with self.assertRaises(check.InspectionError): check.inspect(self.job)

    def test_finished_or_ambiguous_artifacts_rejected(self):
        for filename in ('video.mp4', 'story.json', '3-raw.wav.tmp'):
            p = self.work/filename; p.touch()
            with self.assertRaises(check.InspectionError): check.inspect(self.job)
            p.unlink()
        wav(self.work/'3-raw.wav')
        with self.assertRaises(check.InspectionError): check.inspect(self.job)

class ResumePrimitiveTests(InspectTests):
    def test_resume_uses_only_remaining_turn_in_fresh_folder(self):
        import imageio_ffmpeg
        from news_prototype import bulletin_recovery_audio, story_audio, story_video
        before = {p: p.read_bytes() for p in self.job.rglob('*') if p.is_file()}
        called = []
        def fake_tts(text, target, keys, start_index):
            called.append((text, start_index))
            wav(target)
        output = Path(self.tmp.name)/'new-work'
        with patch.object(story_audio.gemini_tts, 'synthesize', side_effect=fake_tts):
            bulletin_recovery_audio.prepare_copy(self.job, output, imageio_ffmpeg.get_ffmpeg_exe(), ['fake'])
        self.assertEqual(called, [('Текст 3', 2)])
        self.assertEqual((output/'1-normalized.wav').read_bytes(), (self.work/'1-normalized.wav').read_bytes())
        self.assertEqual(before, {p: p.read_bytes() for p in self.job.rglob('*') if p.is_file()})
        _, turns, _ = story_video.load_story(output)
        self.assertEqual(len(turns), 3)
        with patch.object(story_audio.gemini_tts, 'synthesize') as api:
            with self.assertRaises(FileExistsError):
                bulletin_recovery_audio.prepare_copy(self.job, output, imageio_ffmpeg.get_ffmpeg_exe(), ['fake'])
            api.assert_not_called()

    def test_invalid_original_does_not_contact_tts(self):
        import imageio_ffmpeg
        from news_prototype import bulletin_recovery_audio, story_audio
        (self.work/'script.json').write_text('{}')
        with patch.object(story_audio.gemini_tts, 'synthesize') as api:
            with self.assertRaises(check.InspectionError):
                bulletin_recovery_audio.prepare_copy(self.job, Path(self.tmp.name)/'new-work',
                                                     imageio_ffmpeg.get_ffmpeg_exe(), ['fake'])
        api.assert_not_called()

    def test_output_inside_original_is_rejected(self):
        from news_prototype import bulletin_recovery_audio, story_audio
        with patch.object(story_audio.gemini_tts, 'synthesize') as api:
            with self.assertRaises(check.InspectionError):
                bulletin_recovery_audio.prepare_copy(self.job, self.work/'new', 'ffmpeg', ['fake'])
        api.assert_not_called()
        self.assertFalse((self.work/'new').exists())

    def test_failed_attempt_keeps_original_and_cannot_repeat_into_same_folder(self):
        import imageio_ffmpeg
        from news_prototype import bulletin_recovery_audio, story_audio
        before = {p: p.read_bytes() for p in self.job.rglob('*') if p.is_file()}
        output = Path(self.tmp.name)/'attempt'
        with patch.object(story_audio.gemini_tts, 'synthesize', side_effect=story_audio.gemini_tts.TTSFailure('fake')) as api:
            with self.assertRaises(story_audio.gemini_tts.TTSFailure):
                bulletin_recovery_audio.prepare_copy(self.job, output, imageio_ffmpeg.get_ffmpeg_exe(), ['fake'])
            self.assertEqual(api.call_count, 1)
        self.assertEqual(before, {p: p.read_bytes() for p in self.job.rglob('*') if p.is_file()})
        self.assertTrue((output/'1-normalized.wav').is_file())
        self.assertFalse((output/'story.json').exists())
        with patch.object(story_audio.gemini_tts, 'synthesize') as api:
            with self.assertRaises(FileExistsError):
                bulletin_recovery_audio.prepare_copy(self.job, output, imageio_ffmpeg.get_ffmpeg_exe(), ['fake'])
            api.assert_not_called()
