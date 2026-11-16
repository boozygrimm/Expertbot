import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from PIL import Image, ImageChops
    from news_prototype import combined_trial as trial, run_combined_trial as runner
    from tests.test_podcast_video_trial import make_segment
except ImportError:
    trial = runner = None


@unittest.skipIf(trial is None, 'Pillow is optional for this isolated prototype')
class CombinedTrialTests(unittest.TestCase):
    def test_screen_and_remote_time_around_dialogue(self):
        off, duration = trial.timing(240)
        start = trial.INTRO_FRAMES / trial.bender.FPS
        end = start + 10
        self.assertEqual(trial.visibility(0, off), 0)
        self.assertEqual(trial.visibility(start, off), 1)
        self.assertEqual(trial.visibility(end, off), 1)
        self.assertEqual(trial.visibility(duration - .01, off), 0)
        self.assertEqual(trial.finger_amount(trial.ON_PRESS, (trial.ON_PRESS, off)), 1)
        self.assertEqual(trial.finger_amount(off, (trial.ON_PRESS, off)), 1)
        self.assertEqual(trial.finger_amount(start, (trial.ON_PRESS, off)), 0)

    def test_preserves_speech_and_uses_clicks_in_padding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, sound = root / 'part.wav', root / 'sound.wav'
            make_segment(source, seconds=2)
            trial.write_sound(source, sound, 48)
            rate, speech = trial.podcast.read_wav(source)
            _, mixed = trial.podcast.read_wav(sound)
            offset = trial.INTRO_FRAMES * rate // trial.bender.FPS
            self.assertEqual(mixed[offset:offset + len(speech)], speech)
            self.assertTrue(any(mixed[:offset]))
            self.assertTrue(any(mixed[offset + len(speech):]))
            self.assertEqual(len(mixed), (trial.INTRO_FRAMES + 48 + trial.TAIL_FRAMES) * rate // trial.bender.FPS)

    def test_off_screen_preserves_outside_screen(self):
        frame = Image.new('RGB', trial.bender.SIZE, (200, 100, 50))
        off = Image.new('RGB', trial.bender.SIZE, (0, 0, 0))
        mask = trial.scene.screen_mask()
        result = trial.display(frame, mask, off, 0, 12)
        self.assertEqual(result.getpixel((0, 0)), frame.getpixel((0, 0)))
        self.assertEqual(result.getpixel((480, 270)), (0, 0, 0))
        lit = trial.display(frame, mask, off, 3, 12)
        self.assertIsNone(ImageChops.difference(lit, frame).getbbox())

    def test_saved_dialogue_and_uncertain_send_is_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); src, dst = root / 'src', root / 'dst'
            src.mkdir(); dst.mkdir()
            output = io.StringIO()
            def render(command, **kwargs):
                self.assertEqual(command[2], 'news_prototype.combined_trial')
                self.assertEqual(command[command.index('--dialogue-dir') + 1], str(src))
                self.assertNotIn('BOT_TOKEN', kwargs['env'])
                Path(command[command.index('--output') + 1]).write_bytes(b'video')
            with patch.object(runner, 'load_token', return_value='secret'), \
                 patch.object(runner, 'find_dialogue', return_value=src), \
                 patch.object(runner.shutil, 'which', return_value='/usr/bin/ffmpeg'), \
                 patch.object(runner.podcast_video_trial, 'dialogue_timeline'), \
                 patch.object(runner.podcast_video_trial, 'prepare_frames'), \
                 patch.object(runner.hedgehog_motion, 'prepare'), \
                 patch.object(runner.sensei_hatch, 'prepare'), \
                 patch.object(runner.tempfile, 'mkdtemp', return_value=str(dst)), \
                 patch.object(runner.subprocess, 'run', side_effect=render), \
                 patch.object(runner, 'send_video', side_effect=OSError('secret')) as send, \
                 contextlib.redirect_stdout(output):
                runner.main()
            self.assertEqual(send.call_count, 1)
            self.assertIn('Проверьте чат', output.getvalue())
            self.assertNotIn('secret', output.getvalue())

    def test_no_source_means_no_send(self):
        with patch.object(runner, 'load_token', return_value='token'), \
             patch.object(runner, 'find_dialogue', return_value=None), \
             patch.object(runner, 'send_video') as send:
            with self.assertRaises(SystemExit):
                runner.main()
            send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
