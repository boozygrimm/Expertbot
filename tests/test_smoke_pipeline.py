import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_prototype import pipeline


class SmokePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.frames = self.root / 'frames'
        self.frames.mkdir()
        for name in ('closed.png', 'open.png', 'blink.png'):
            (self.frames / name).touch()
        self.audio = self.root / 'audio.wav'
        self.audio.touch()
        self.plate = self.root / 'plate.png'
        self.plate.touch()

    def args(self, *extra):
        return pipeline.parse_args(['--audio', str(self.audio), '--no-send',
            '--frames-dir', str(self.frames), '--output-dir', str(self.root / 'out'), *extra])

    @patch('video_prototype.pipeline.subprocess.run')
    def test_smoke_plate_selects_renderer(self, run):
        pipeline.run(self.args('--smoke-plate', str(self.plate)))
        cmd = run.call_args.args[0]
        self.assertTrue(cmd[1].endswith('/smoke_layer_render.py'))
        self.assertEqual(cmd[cmd.index('--plate') + 1], str(self.plate))

    @patch('video_prototype.pipeline.subprocess.run')
    def test_without_plate_uses_original_renderer(self, run):
        pipeline.run(self.args())
        self.assertTrue(run.call_args.args[0][1].endswith('/render.py'))
        self.assertNotIn('--plate', run.call_args.args[0])

    @patch('video_prototype.pipeline.subprocess.run')
    def test_missing_plate_fails_before_synthesis(self, run):
        with self.assertRaises(FileNotFoundError):
            pipeline.run(self.args('--smoke-plate', str(self.root / 'missing.png')))
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
