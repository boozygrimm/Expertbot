import unittest
from unittest.mock import MagicMock, patch

from PIL import Image, ImageChops, ImageDraw

from video_prototype import smoke_render


class SmokeRenderTests(unittest.TestCase):
    def setUp(self):
        self.original = Image.new('RGB', (480, 480), 'black')
        draw = ImageDraw.Draw(self.original)
        for x in range(0, 480, 8):
            draw.rectangle((x, 0, x + 3, 479), fill=(180, 160, 140))

    def test_effects_are_deterministic_and_change_with_time(self):
        raw = self.original.tobytes()
        first = smoke_render.frame_with_effects(raw, 0)
        self.assertEqual(first, smoke_render.frame_with_effects(raw, 0))
        self.assertNotEqual(first, smoke_render.frame_with_effects(raw, 2))
        self.assertEqual(len(first), 480 * 480 * 3)

    def test_effects_are_limited_to_smoke_and_ember(self):
        self.assertIsNotNone(smoke_render.SMOKE_MASK.getbbox())
        self.assertIsNotNone(smoke_render.EMBER_MASK.getbbox())
        modified = Image.frombytes('RGB', (480, 480),
                                   smoke_render.frame_with_effects(self.original.tobytes(), 1))
        self.assertEqual(modified.getpixel((20, 20)), self.original.getpixel((20, 20)))
        self.assertIsNotNone(ImageChops.difference(self.original, modified).getbbox())
        self.assertEqual(smoke_render.SMOKE_MASK.getpixel((20, 20)), 0)
        self.assertEqual(smoke_render.EMBER_MASK.getpixel((20, 20)), 0)

    def test_render_uses_audio_frame_count_and_closes_ffmpeg(self):
        process = MagicMock()
        process.wait.return_value = 0
        pixels = self.original.tobytes()
        frames = {(mouth, blink): pixels for mouth in (False, True) for blink in (False, True)}
        with patch.object(smoke_render, 'audio_levels', return_value=[0, 1, 0]), \
             patch.object(smoke_render, 'make_frames', return_value=frames), \
             patch.object(smoke_render.subprocess, 'Popen', return_value=process):
            smoke_render.render('a.wav', 'closed.png', 'open.png', 'blink.png', '/tmp/clip.mp4')
        self.assertEqual(process.stdin.write.call_count, 3)
        process.stdin.close.assert_called_once()
        process.wait.assert_called_once()


if __name__ == '__main__':
    unittest.main()
