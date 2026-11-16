import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from PIL import Image  # noqa: F401
    from news_prototype import run_podcast_video_trial as trial
except ImportError:
    trial = None


@unittest.skipIf(trial is None, 'Pillow is optional for the isolated news prototype')
class RunPodcastVideoTests(unittest.TestCase):
    def test_latest_complete_dialogue_only(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            older, newer, incomplete = (base / 'news-dialogue-old', base / 'news-dialogue-new', base / 'news-dialogue-broken')
            for path in (older, newer, incomplete):
                path.mkdir()
                (path / 'dialogue.wav').write_bytes(b'wav')
            for path in (older, newer):
                for i in range(1, 5):
                    (path / f'{i}-normalized.wav').write_bytes(b'wav')
            os.utime(older / 'dialogue.wav', (1, 1))
            os.utime(newer / 'dialogue.wav', (2, 2))
            os.utime(incomplete / 'dialogue.wav', (3, 3))
            self.assertEqual(trial.find_dialogue(base), newer)

    def test_uses_saved_audio_and_sends_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            src, dst = root / 'dialogue', root / 'video'
            src.mkdir(); dst.mkdir()
            output = io.StringIO()
            def render(command, **kwargs):
                self.assertEqual(command[command.index('--dialogue-dir') + 1], str(src))
                Path(command[command.index('--output') + 1]).write_bytes(b'video')
            with patch.object(trial, 'load_token', return_value='private-token'), \
                 patch.object(trial, 'find_dialogue', return_value=src), \
                 patch.object(trial.shutil, 'which', return_value='/usr/bin/ffmpeg'), \
                 patch.object(trial.podcast_video_trial, 'dialogue_timeline'), \
                 patch.object(trial.bender_trial, 'load_image'), \
                 patch.object(trial.tempfile, 'mkdtemp', return_value=str(dst)), \
                 patch.object(trial.subprocess, 'run', side_effect=render), \
                 patch.object(trial, 'send_video', side_effect=OSError('private-token')) as send, \
                 contextlib.redirect_stdout(output):
                trial.main()
            self.assertEqual(send.call_count, 1)
            self.assertIn('без повторной озвучки', output.getvalue())
            self.assertIn('Проверьте чат', output.getvalue())
            self.assertNotIn('private-token', output.getvalue())
            self.assertTrue((dst / 'video.mp4').exists())


if __name__ == '__main__':
    unittest.main()
