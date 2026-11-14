import unittest
from unittest.mock import patch
from PIL import Image, ImageChops
from video_prototype import pyramid_render as pyramid, beholder_render as beast, flask_render as flask


class PyramidTests(unittest.TestCase):
    def test_separate_from_both_flasks(self):
        layer = Image.new('L', (pyramid.SIZE, pyramid.SIZE))
        s = pyramid.SIZE / 480
        layer.paste(pyramid.SIGIL.getchannel('A'), (round(130*s), round(96*s)))
        self.assertIsNone(ImageChops.multiply(layer, beast.GREEN_MASK).getbbox())
        self.assertIsNone(ImageChops.multiply(layer, flask.FLASK_MASK).getbbox())
        # Both the running corridor and Pai Mei's lower-right area are below it.
        self.assertLess(pyramid.REGION[3], 200)

    def test_only_active_temporarily_and_pulses(self):
        frame = Image.new('RGB', (pyramid.SIZE, pyramid.SIZE), (20,20,20)).tobytes()
        self.assertEqual(pyramid.frame_with_pyramid(frame, 1), frame)
        self.assertNotEqual(pyramid.frame_with_pyramid(frame, 3), frame)
        self.assertNotEqual(pyramid.frame_with_pyramid(frame, 3), pyramid.frame_with_pyramid(frame, 3.5))
        self.assertEqual(pyramid.frame_with_pyramid(frame, 7), frame)

    def test_existing_render_includes_sigil_effect(self):
        with patch.object(pyramid, 'render_flask') as render:
            pyramid.render('a','b','c','d','e','f')
        self.assertIs(render.call_args.kwargs['frame_effect'], pyramid.frame_with_pyramid)


if __name__ == '__main__':
    unittest.main()
