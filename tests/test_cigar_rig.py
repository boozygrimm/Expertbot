import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from video_prototype import cigar_rig


class CigarRigTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.frames = self.root / 'frames'
        self.frames.mkdir()
        for name, color in [('closed', 'red'), ('open', 'green'), ('blink', 'blue')]:
            Image.new('RGB', (512, 512), color).save(self.frames / (name + '.png'))
        self.raised = self.root / 'raised.png'
        Image.new('RGB', (512, 512), 'white').save(self.raised)

    def test_gesture_has_smooth_lift_hold_and_lower(self):
        self.assertEqual(cigar_rig.amount_at(1), 0)
        self.assertAlmostEqual(cigar_rig.amount_at(1.9), .5)
        self.assertEqual(cigar_rig.amount_at(2.6), 1)
        self.assertAlmostEqual(cigar_rig.amount_at(4.3), .5)
        self.assertEqual(cigar_rig.amount_at(5), 0)

    def test_frame_at_rest_uses_original_and_rig_moves(self):
        layers = cigar_rig.build_layers(self.frames, self.raised)
        rest = cigar_rig.frame(layers, False, False, 0)
        moving = cigar_rig.frame(layers, False, False, .5)
        self.assertEqual(len(rest), 480 * 480 * 3)
        self.assertEqual(Image.frombytes('RGB', (480, 480), rest).getpixel((20, 20)), (255, 0, 0))
        self.assertNotEqual(rest, moving)

    def test_render_writes_one_frame_per_audio_level(self):
        process = MagicMock()
        process.wait.return_value = 0
        with patch.object(cigar_rig, 'audio_levels', return_value=[0, 1, 0]), \
             patch.object(cigar_rig, 'build_layers', return_value='layers'), \
             patch.object(cigar_rig, 'frame', return_value=b'frame'), \
             patch.object(cigar_rig.subprocess, 'Popen', return_value=process):
            cigar_rig.render('audio.wav', self.frames, self.raised, self.root / 'out.mp4')
        self.assertEqual(process.stdin.write.call_count, 3)
        process.stdin.close.assert_called_once()
        process.wait.assert_called_once()

    def test_rejects_nonsquare_source(self):
        Image.new('RGB', (512, 400), 'red').save(self.raised)
        with self.assertRaises(ValueError):
            cigar_rig.build_layers(self.frames, self.raised)


if __name__ == '__main__':
    unittest.main()
