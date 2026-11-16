import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from PIL import Image, ImageChops
    from news_prototype import desk_ticker as ticker, ticker_trial, run_ticker_trial as runner
except ImportError:
    ticker = None


@unittest.skipIf(ticker is None, 'Pillow is optional for this isolated prototype')
class DeskTickerTests(unittest.TestCase):
    def test_scrolls_and_loops_without_affecting_other_parts_of_frame(self):
        assets = ticker.prepare()
        first, pos = ticker.overlay(assets, 0)
        later, pos2 = ticker.overlay(assets, 3)
        loop, pos3 = ticker.overlay(assets, assets[1] / ticker.SPEED)
        self.assertEqual(pos, pos2)
        self.assertEqual(pos, pos3)
        self.assertIsNotNone(ImageChops.difference(first.convert('RGB'), later.convert('RGB')).getbbox())
        self.assertEqual(first.tobytes(), loop.tobytes())
        frame = Image.new('RGB', ticker.bender.SIZE, (100, 120, 140))
        output = ticker.apply(frame, assets, 3)
        bbox = ImageChops.difference(frame, output).getbbox()
        self.assertGreater(bbox[0], 320)
        self.assertLess(bbox[2], 650)
        self.assertGreater(bbox[1], 390)
        self.assertLess(bbox[3], 425)

    def test_rejects_empty_or_overlong_text(self):
        for value in ('', 'X' * 201):
            with self.assertRaises(ValueError):
                ticker.prepare(value)

    def test_combined_renderer_receives_scrolling_overlay(self):
        assets = ticker.prepare()
        with patch.object(ticker_trial.desk_ticker, 'prepare', return_value=assets), \
             patch.object(ticker_trial.combined_trial, 'render') as render:
            ticker_trial.render(Path('input'), Path('out'), '/ffmpeg')
        callback = render.call_args.kwargs['screen_overlay']
        frame = Image.new('RGB', ticker.bender.SIZE)
        self.assertEqual(callback(frame, 0).tobytes(), callback(frame, 1.48).tobytes())
        self.assertNotEqual(callback(frame, 1.48).tobytes(), callback(frame, 4.48).tobytes())

    def test_saved_dialogue_one_send_no_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); src, dst = root / 'src', root / 'dst'
            src.mkdir(); dst.mkdir()
            output = io.StringIO()
            def render(command, **kwargs):
                self.assertEqual(command[2], 'news_prototype.ticker_trial')
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
                 patch.object(runner.desk_ticker, 'prepare'), \
                 patch.object(runner.tempfile, 'mkdtemp', return_value=str(dst)), \
                 patch.object(runner.subprocess, 'run', side_effect=render), \
                 patch.object(runner, 'send_video', side_effect=OSError('secret')) as send, \
                 contextlib.redirect_stdout(output):
                runner.main()
            self.assertEqual(send.call_count, 1)
            self.assertIn('Проверьте чат', output.getvalue())
            self.assertNotIn('secret', output.getvalue())

    def test_no_dialogue_means_no_send(self):
        with patch.object(runner, 'load_token', return_value='token'), \
             patch.object(runner, 'find_dialogue', return_value=None), \
             patch.object(runner, 'send_video') as send:
            with self.assertRaises(SystemExit):
                runner.main()
            send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
