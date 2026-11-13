import unittest
from unittest.mock import MagicMock, patch

from PIL import Image, ImageChops

from video_prototype import smoke_render


class SmokeRenderTests(unittest.TestCase):
    def test_effects_are_deterministic_and_change_with_time(self):
        first = smoke_render.effects(0)
        repeated = smoke_render.effects(0)
        other = smoke_render.effects(2)
        self.assertEqual(first.size, (480, 480))
        self.assertEqual(first.tobytes(), repeated.tobytes())
        self.assertNotEqual(first.tobytes(), other.tobytes())
        self.assertIsNotNone(first.getchannel('A').getbbox())

    def test_effects_are_limited_to_cigar_and_nearby_smoke(self):
        layer = smoke_render.effects(0)
        bbox = layer.getchannel('A').getbbox()
        self.assertGreater(bbox[0], 340)
        self.assertLess(bbox[2], 420)
        self.assertGreater(bbox[1], 200)
        self.assertLess(bbox[3], 350)
        original = Image.new('RGB', (480, 480), 'black')
        modified = Image.frombytes('RGB', (480, 480),
                                   smoke_render.frame_with_effects(original.tobytes(), 0))
        self.assertEqual(modified.getpixel((20, 20)), original.getpixel((20, 20)))
        self.assertIsNotNone(ImageChops.difference(original, modified).getbbox())

    def test_render_uses_audio_frame_count_and_closes_ffmpeg(self):
        process = MagicMock()
        process.wait.return_value = 0
        pixels = Image.new('RGB', (480, 480), 'black').tobytes()
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
