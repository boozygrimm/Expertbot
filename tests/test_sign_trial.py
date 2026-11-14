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

    def test_typewriter_advances_and_holds_the_approved_sign(self):
        frames, position = sign_trial.prepare_typewriter()
        final, final_position = sign_trial.prepare_sign()
        self.assertEqual(position, final_position)
        self.assertEqual(len(frames), 19)
        self.assertNotEqual(frames[0].tobytes(), frames[5].tobytes())
        self.assertNotEqual(frames[5].tobytes(), frames[10].tobytes())
        self.assertEqual(frames[-1].tobytes(), final.tobytes())
        self.assertEqual(sign_trial.typewriter_index(0), 0)
        self.assertEqual(sign_trial.typewriter_index(.29), 0)
        self.assertGreater(sign_trial.typewriter_index(.9), 0)
        self.assertEqual(sign_trial.typewriter_index(3), 18)
        self.assertEqual(sign_trial.typewriter_index(50), 18)

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

    def test_typewriter_layer_changes_before_guest_effect(self):
        pixels = bytes(480 * 480 * 3)
        seen = []
        def guest(frame, seconds):
            seen.append(frame)
            return frame
        with patch.object(sign_trial, 'quick_levels', return_value=[0] * 72), \
             patch.object(sign_trial, 'make_effect', return_value=guest), \
             patch.object(sign_trial, 'base_render') as renderer:
            sign_trial.render_trial('a', 'c', 'o', 'b', 'p', 'portrait', 'out', typewriter=True)
            effect = renderer.call_args.kwargs['frame_effect']
            start = effect(pixels, 0)
            end = effect(pixels, 3)
        self.assertEqual(seen, [start, end])
        self.assertNotEqual(start, end)


if __name__ == '__main__':
    unittest.main()
