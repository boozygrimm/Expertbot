import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image, ImageChops, ImageDraw
from video_prototype import smoke_layer_render as smoke


class SmokeLayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'plate.png'
        Image.new('RGB', (512, 512), 'blue').save(self.path)
        image = Image.new('RGB', (480, 480), (30, 30, 30))
        draw = ImageDraw.Draw(image)
        for x in range(320, 410, 5):
            draw.line((x, 40, x, 230), fill=(150, 150, 150), width=2)
        self.frames = {(mouth, blink): image.tobytes()
                       for mouth in (False, True) for blink in (False, True)}

    def test_clean_patch_preserves_outside_and_smoke_moves(self):
        layers = smoke.prepare_frames(self.frames, self.path)
        base, mask = layers[False, False]
        self.assertEqual(base.getpixel((20, 20)), (30, 30, 30))
        self.assertIsNotNone(mask.getbbox())
        self.assertNotEqual(smoke.frame_with_layers((base, mask), 0),
                            smoke.frame_with_layers((base, mask), 1))
        self.assertEqual(smoke.PATCH_MASK.getpixel((350, 300)), 0)

    def test_reject_nonsquare_plate(self):
        Image.new('RGB', (100, 200)).save(self.path)
        with self.assertRaises(ValueError):
            smoke.prepare_frames(self.frames, self.path)

    def test_render_writes_audio_length(self):
        process = MagicMock()
        process.wait.return_value = 0
        with patch.object(smoke, 'audio_levels', return_value=[0, 1, 0]), \
             patch.object(smoke, 'make_frames', return_value=self.frames), \
             patch.object(smoke.subprocess, 'Popen', return_value=process):
            smoke.render('a.wav', 'c.png', 'o.png', 'b.png', self.path, '/tmp/sample.mp4')
        self.assertEqual(process.stdin.write.call_count, 3)
        process.stdin.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
