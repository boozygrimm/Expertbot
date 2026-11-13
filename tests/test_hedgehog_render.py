import unittest
from unittest.mock import MagicMock, patch

from PIL import Image, ImageChops
from video_prototype import hedgehog_render as hedge


class HedgehogTests(unittest.TestCase):
    def setUp(self):
        self.sprite = hedge.HERE / 'assets/hedgehog.png'
        self.sprites = hedge.make_run_frames(self.sprite)
        self.frame = Image.new('RGB', (480, 480), (30, 30, 30)).tobytes()

    def test_running_feet_have_multiple_distinct_poses(self):
        self.assertEqual(len(self.sprites), 12)
        self.assertTrue(any(s.tobytes() != self.sprites[0].tobytes() for s in self.sprites[1:]))

    def test_hedgehog_moves_right_and_is_hidden_outside_run(self):
        self.assertIsNone(hedge.run_position(.5))
        self.assertIsNone(hedge.run_position(5))
        self.assertLess(hedge.run_position(1.5)[0], hedge.run_position(2.5)[0])
        self.assertEqual(hedge.frame_with_hedgehog(self.frame, 5, self.sprites), self.frame)
        self.assertNotEqual(hedge.frame_with_hedgehog(self.frame, 2.5, self.sprites), self.frame)

    def test_mask_keeps_robot_and_sign_in_front(self):
        output = Image.frombytes('RGB', (480, 480), hedge.frame_with_hedgehog(self.frame, 2.5, self.sprites))
        self.assertEqual(output.getpixel((270, 224)), (30, 30, 30))
        self.assertEqual(output.getpixel((140, 220)), (30, 30, 30))
        difference = ImageChops.difference(output, Image.frombytes('RGB', (480, 480), self.frame))
        self.assertIsNotNone(difference.getbbox())
        self.assertIsNone(difference.crop((0, 320, 480, 480)).getbbox())

    def test_renderer_preserves_audio_frame_count(self):
        process = MagicMock()
        process.wait.return_value = 0
        with patch.object(hedge, 'audio_levels', return_value=[0, 1, 0]), \
             patch.object(hedge, 'make_frames'), \
             patch.object(hedge, 'prepare_frames', return_value={(m, e): None for m in (0, 1) for e in (0, 1)}), \
             patch.object(hedge, 'make_run_frames', return_value=self.sprites), \
             patch.object(hedge, 'frame_with_layers', return_value=self.frame), \
             patch.object(hedge.subprocess, 'Popen', return_value=process):
            hedge.render('a.wav', 'closed.png', 'open.png', 'blink.png', 'plate.png', self.sprite, '/tmp/hedge.mp4')
        self.assertEqual(process.stdin.write.call_count, 3)
        process.stdin.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
