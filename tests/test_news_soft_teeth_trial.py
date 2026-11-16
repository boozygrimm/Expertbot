import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from PIL import Image  # noqa: F401
    from news_prototype import run_soft_teeth_trial as trial
except ImportError:
    trial = None


@unittest.skipIf(trial is None, 'Pillow is optional for the isolated news prototype')
class SoftTeethTrialTests(unittest.TestCase):
    def test_picks_latest_presenter_audio_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            older = root / 'news-bender-old' / 'speech.wav'
            newer = root / 'news-bender-new' / 'speech.wav'
            other = root / 'news-soft-teeth-other' / 'speech.wav'
            for file in (older, newer, other):
                file.parent.mkdir()
                file.write_bytes(b'x')
            os.utime(older, (1, 1)); os.utime(newer, (2, 2)); os.utime(other, (3, 3))
            self.assertEqual(trial.find_previous_audio(root), newer)

    def test_existing_audio_is_copied_and_sent_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'previous.wav'
            source.write_bytes(b'original audio')
            dest = root / 'result'
            dest.mkdir()
            output = io.StringIO()
            def render(command, **kwargs):
                self.assertIn('--soft-teeth', command)
                Path(command[command.index('--output') + 1]).write_bytes(b'video')
            with patch.object(trial, 'load_token', return_value='secret'), \
                 patch.object(trial, 'find_previous_audio', return_value=source), \
                 patch.object(trial.bender_trial, 'read_pcm'), \
                 patch.object(trial.bender_trial, 'load_image'), \
                 patch.object(trial.shutil, 'which', return_value='/usr/bin/ffmpeg'), \
                 patch.object(trial.tempfile, 'mkdtemp', return_value=str(dest)), \
                 patch.object(trial.subprocess, 'run', side_effect=render), \
                 patch.object(trial, 'send_video', return_value=True) as send, \
                 contextlib.redirect_stdout(output):
                trial.main()
            self.assertEqual((dest / 'speech.wav').read_bytes(), source.read_bytes())
            self.assertEqual(send.call_count, 1)
            self.assertNotIn('secret', output.getvalue())
            self.assertIn('без повторного синтеза', output.getvalue())


if __name__ == '__main__':
    unittest.main()
