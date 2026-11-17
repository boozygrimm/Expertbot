import os
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import imageio_ffmpeg
from news_prototype import bulletin_preflight


class BulletinPreflightTests(unittest.TestCase):
    def test_actual_local_prerequisites_without_network_or_render(self):
        self.assertEqual(bulletin_preflight.check(imageio_ffmpeg.get_ffmpeg_exe()), [])
        result = subprocess.run([sys.executable, '-m', 'news_prototype.bulletin_preflight',
                                 '--ffmpeg', imageio_ffmpeg.get_ffmpeg_exe()],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('рендер не запускался', result.stdout)

    def test_missing_ffmpeg_and_assets_fail_closed(self):
        self.assertIn('Не найден FFmpeg', bulletin_preflight.check('/nonexistent/ffmpeg'))
        with patch('video_prototype.news_jobs.bulletin_assets_ready', return_value=False):
            self.assertTrue(any('слои' in e for e in bulletin_preflight.check(imageio_ffmpeg.get_ffmpeg_exe())))

    def test_encoder_capabilities_and_credentials_are_not_inherited(self):
        output = b' V..... libx264 encoder\n A..... aac encoder\n'
        with patch.dict(os.environ, {'BOT_TOKEN':'test-secret', 'GEMINI_TTS_KEYS':'test-secret'}), \
             patch.object(bulletin_preflight.subprocess, 'run', return_value=SimpleNamespace(stdout=output)) as run:
            self.assertEqual(bulletin_preflight.check(imageio_ffmpeg.get_ffmpeg_exe()), [])
            self.assertNotIn('BOT_TOKEN', run.call_args.kwargs['env'])
            self.assertNotIn('GEMINI_TTS_KEYS', run.call_args.kwargs['env'])
            run.return_value.stdout = b' V..... libx264 encoder\n'
            self.assertIn('FFmpeg не поддерживает aac', bulletin_preflight.check(imageio_ffmpeg.get_ffmpeg_exe()))
