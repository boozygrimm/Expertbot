import unittest
from unittest.mock import patch
from PIL import Image
from video_prototype import sign_trial


class SignTrialTests(unittest.TestCase):
    def test_layer_is_confined_and_does_not_cover_fingers(self):
        layer, (x, y) = sign_trial.prepare_sign()
        full = Image.new('RGBA', (480, 480))
        full.paste(layer, (x, y))
        self.assertEqual(full.getpixel((10, 10))[3], 0)
        self.assertGreater(full.getpixel((160, 255))[3], 240)
        self.assertEqual(full.getpixel((94, 274))[3], 0)
        self.assertLess(layer.width, 110)

    def test_sign_is_applied_before_guest_effect(self):
        pixels = bytes(480 * 480 * 3)
        seen = []
        def guest(frame, seconds):
            seen.append(frame)
            return frame
        with patch.object(sign_trial, 'quick_levels', return_value=[0] * 24), \
             patch.object(sign_trial, 'make_effect', return_value=guest), \
             patch.object(sign_trial, 'base_render') as renderer:
            sign_trial.render_trial('a', 'c', 'o', 'b', 'p', 'portrait', 'out')
            output = renderer.call_args.kwargs['frame_effect'](pixels, 0)
        self.assertEqual(seen, [output])
        self.assertNotEqual(output, pixels)


if __name__ == '__main__':
    unittest.main()
