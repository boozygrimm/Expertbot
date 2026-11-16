import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from PIL import Image  # noqa: F401
    from news_prototype import run_sensei_hatch_trial as trial
except ImportError:
    trial = None


@unittest.skipIf(trial is None, 'Pillow is optional for the isolated news prototype')
class HatchRunnerTests(unittest.TestCase):
    def test_reuses_saved_dialogue_and_does_not_retry_ambiguous_send(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            src, dst = root / 'dialogue', root / 'result'
            src.mkdir()
            dst.mkdir()
            output = io.StringIO()
            def render(command, **kwargs):
                self.assertEqual(command[command.index('--dialogue-dir') + 1], str(src))
                Path(command[command.index('--output') + 1]).write_bytes(b'video')
                self.assertNotIn('BOT_TOKEN', kwargs['env'])
            with patch.object(trial, 'load_token', return_value='secret-token'), \
                 patch.object(trial, 'find_dialogue', return_value=src), \
                 patch.object(trial.shutil, 'which', return_value='/usr/bin/ffmpeg'), \
                 patch.object(trial.podcast_video_trial, 'dialogue_timeline'), \
                 patch.object(trial.podcast_video_trial, 'prepare_frames'), \
                 patch.object(trial.sensei_hatch, 'prepare'), \
                 patch.object(trial.tempfile, 'mkdtemp', return_value=str(dst)), \
                 patch.object(trial.subprocess, 'run', side_effect=render), \
                 patch.object(trial, 'send_video', side_effect=OSError('secret-token')) as send, \
                 contextlib.redirect_stdout(output):
                trial.main()
            self.assertEqual(send.call_count, 1)
            self.assertIn('без повторной озвучки', output.getvalue())
            self.assertIn('Проверьте чат', output.getvalue())
            self.assertNotIn('secret-token', output.getvalue())
            self.assertTrue((dst / 'video.mp4').exists())

    def test_missing_source_does_not_send(self):
        with patch.object(trial, 'load_token', return_value='token'), \
             patch.object(trial, 'find_dialogue', return_value=None), \
             patch.object(trial, 'send_video') as send:
            with self.assertRaises(SystemExit):
                trial.main()
        send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
