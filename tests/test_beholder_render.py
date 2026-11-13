import unittest
from unittest.mock import patch
from PIL import Image, ImageChops
from video_prototype import beholder_render as beast

class BeholderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sprites = beast.prepare_sprites()

    def test_moving_tentacles_and_transparency(self):
        self.assertEqual(len(self.sprites), 24)
        self.assertEqual(self.sprites[0].getchannel('A').getextrema()[0], 0)
        self.assertNotEqual(self.sprites[0].tobytes(), self.sprites[8].tobytes())

    def test_appears_smoothly_only_inside_green_flask(self):
        self.assertIsNone(beast.reveal(1))
        self.assertLess(beast.reveal(1.7), beast.reveal(2.2))
        self.assertIsNone(beast.reveal(7.1))
        image = Image.new('RGB', (beast.SIZE, beast.SIZE), (80, 80, 80))
        frame = image.tobytes()
        self.assertEqual(frame, beast.frame_with_beholder(frame, 1, self.sprites))
        out = Image.frombytes('RGB', image.size, beast.frame_with_beholder(frame, 4, self.sprites))
        difference = ImageChops.difference(out, image).convert('L')
        self.assertIsNotNone(difference.getbbox())
        outside = beast.GREEN_MASK.point(lambda v: 255 if v == 0 else 0)
        self.assertIsNone(ImageChops.multiply(difference, outside).getbbox())

    def test_uses_existing_smoke_and_mouth_renderer(self):
        with patch.object(beast, 'prepare_sprites', return_value=self.sprites), \
             patch.object(beast, 'render_flask') as render:
            beast.render('a', 'b', 'c', 'd', 'e', 'f')
        self.assertTrue(callable(render.call_args.kwargs['frame_effect']))

if __name__ == '__main__':
    unittest.main()
