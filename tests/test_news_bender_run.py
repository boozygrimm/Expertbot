import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from PIL import Image  # noqa: F401
    from news_prototype import run_bender_trial as trial
except ImportError:
    trial = None


@unittest.skipIf(trial is None, 'Pillow is optional for the isolated news prototype')
class BenderRunTests(unittest.TestCase):
    def test_synthesis_render_and_single_send(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            output = io.StringIO()
            def synth(_text, path, _keys):
                path.write_bytes(b'wav')
            def render(command, **kwargs):
                Path(command[command.index('--output') + 1]).write_bytes(b'video')
            with patch.object(trial, 'load_token', return_value='secret-token'), \
                 patch.object(trial, 'load_keys', return_value=['secret-key']), \
                 patch.object(trial.shutil, 'which', return_value='/usr/bin/ffmpeg'), \
                 patch.object(trial.tempfile, 'mkdtemp', return_value=str(folder)), \
                 patch.object(trial.Path, 'mkdir'), \
                 patch.object(trial.gemini_tts, 'synthesize', side_effect=synth) as tts, \
                 patch.object(trial.subprocess, 'run', side_effect=render) as rendering, \
                 patch.object(trial, 'send_video', return_value=True) as send, \
                 contextlib.redirect_stdout(output):
                trial.main()
            self.assertEqual(tts.call_count, 1)
            self.assertEqual(rendering.call_count, 1)
            self.assertEqual(send.call_count, 1)
            self.assertEqual((folder / 'video.mp4').read_bytes(), b'video')
            self.assertNotIn('secret-token', output.getvalue())
            self.assertNotIn('secret-key', output.getvalue())
            self.assertIn('видео отправлено', output.getvalue())

    def test_send_failure_is_not_retried_and_does_not_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            output = io.StringIO()
            def render(command, **kwargs):
                Path(command[command.index('--output') + 1]).write_bytes(b'video')
            with patch.object(trial, 'load_token', return_value='secret-token'), \
                 patch.object(trial, 'load_keys', return_value=['key']), \
                 patch.object(trial.shutil, 'which', return_value='/usr/bin/ffmpeg'), \
                 patch.object(trial.tempfile, 'mkdtemp', return_value=str(folder)), \
                 patch.object(trial.Path, 'mkdir'), \
                 patch.object(trial.gemini_tts, 'synthesize'), \
                 patch.object(trial.subprocess, 'run', side_effect=render), \
                 patch.object(trial, 'send_video', side_effect=OSError('secret-token')) as send, \
                 contextlib.redirect_stdout(output):
                trial.main()
            self.assertEqual(send.call_count, 1)
            self.assertNotIn('secret-token', output.getvalue())
            self.assertIn('Проверьте чат', output.getvalue())
            self.assertTrue((folder / 'video.mp4').exists())


if __name__ == '__main__':
    unittest.main()
