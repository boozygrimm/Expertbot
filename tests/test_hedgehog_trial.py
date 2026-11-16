import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from PIL import Image, ImageChops
    from news_prototype import hedgehog_motion as motion, run_hedgehog_trial as runner
except ImportError:
    motion = runner = None


@unittest.skipIf(motion is None, 'Pillow is optional for this isolated prototype')
class HedgehogTrialTests(unittest.TestCase):
    def test_gesture_is_bounded_and_limited_to_one_short_event(self):
        self.assertEqual(motion.gesture(0, 10), (0, 0))
        self.assertEqual(motion.gesture(10, 10), (0, 0))
        self.assertEqual(motion.gesture(1, 2), (0, 0))
        self.assertEqual(motion.gesture(2.55, 10)[1], 1)
        self.assertEqual(motion.gesture(3.65, 10)[1], 1)
        for i in range(240):
            nod, blink = motion.gesture(i / 24, 10)
            self.assertTrue(0 <= nod <= 1 and 0 <= blink <= 1)

    def test_animation_stays_in_portrait_and_approaches_identity(self):
        assets = motion.prepare()
        zero = motion.overlay(assets, 0, 0)
        self.assertIsNone(zero.getchannel('A').getbbox())
        tiny = motion.overlay(assets, .0001, 0)
        self.assertLessEqual(tiny.getchannel('A').getextrema()[1], 1)
        overlay = motion.overlay(assets, 1, 1)
        bbox = overlay.getchannel('A').getbbox()
        self.assertGreater(bbox[0], 700)
        self.assertLess(bbox[2], 770)
        self.assertGreater(bbox[1], 125)
        self.assertLess(bbox[3], 185)
        self.assertIsNotNone(ImageChops.difference(assets[0].convert('RGB'), assets[1].convert('RGB')).getbbox())

    def test_reuses_saved_dialogue_and_sends_once_on_ambiguous_response(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            src, dst = root / 'dialogue', root / 'result'
            src.mkdir(); dst.mkdir()
            output = io.StringIO()
            def render(command, **kwargs):
                self.assertEqual(command[command.index('--dialogue-dir') + 1], str(src))
                self.assertEqual(command[2], 'news_prototype.hedgehog_trial')
                self.assertNotIn('BOT_TOKEN', kwargs['env'])
                Path(command[command.index('--output') + 1]).write_bytes(b'video')
            with patch.object(runner, 'load_token', return_value='secret-token'), \
                 patch.object(runner, 'find_dialogue', return_value=src), \
                 patch.object(runner.shutil, 'which', return_value='/usr/bin/ffmpeg'), \
                 patch.object(runner.podcast_video_trial, 'dialogue_timeline'), \
                 patch.object(runner.podcast_video_trial, 'prepare_frames'), \
                 patch.object(runner.hedgehog_motion, 'prepare'), \
                 patch.object(runner.tempfile, 'mkdtemp', return_value=str(dst)), \
                 patch.object(runner.subprocess, 'run', side_effect=render), \
                 patch.object(runner, 'send_video', side_effect=OSError('secret-token')) as send, \
                 contextlib.redirect_stdout(output):
                runner.main()
            self.assertEqual(send.call_count, 1)
            self.assertIn('Проверьте чат', output.getvalue())
            self.assertNotIn('secret-token', output.getvalue())

    def test_missing_dialogue_never_sends(self):
        with patch.object(runner, 'load_token', return_value='token'), \
             patch.object(runner, 'find_dialogue', return_value=None), \
             patch.object(runner, 'send_video') as send:
            with self.assertRaises(SystemExit):
                runner.main()
            send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
