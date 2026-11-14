from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from video_prototype import gaze_trial_render as gaze


class GazeTests(unittest.TestCase):
    def test_smooth_look_toward_creature(self):
        self.assertEqual(gaze.gaze_amount(1), 0)
        self.assertLess(gaze.gaze_amount(2.3), gaze.gaze_amount(2.5))
        self.assertEqual(gaze.gaze_amount(3), 1)
        self.assertLess(gaze.gaze_amount(6.2), gaze.gaze_amount(6))
        self.assertEqual(gaze.gaze_amount(7), 0)

    def test_frame_in_bounds_and_does_not_override_blinks(self):
        frames = [Image.new('RGB', (gaze.EYE_BOX[2]-gaze.EYE_BOX[0], gaze.EYE_BOX[3]-gaze.EYE_BOX[1]), (i*20,0,0)) for i in range(11)]
        frame = Image.new('RGB',(gaze.SIZE,gaze.SIZE),(0,0,0)).tobytes()
        self.assertEqual(gaze.frame_with_gaze(frame, 4, frames), frame)
        after = Image.frombytes('RGB',(gaze.SIZE,gaze.SIZE),gaze.frame_with_gaze(frame, 3, frames))
        self.assertNotEqual(after.getpixel((250,170)), (0,0,0))
        self.assertEqual(after.getpixel((10,10)), (0,0,0))

    def test_prepare_frames_and_reuse_approved_mouth(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/'square.png'
            Image.new('RGB',(480,480),(200,200,100)).save(source)
            eyes = gaze.prepare_eye_frames(source)
            self.assertEqual(len(eyes), 11)
            self.assertNotEqual(eyes[0].tobytes(), eyes[-1].tobytes())
        with patch.object(gaze, 'prepare_eye_frames',return_value=eyes), \
             patch.object(gaze, 'prepare_sprites'), patch.object(gaze, 'base_render') as render:
            gaze.render('a','b','c','d','e','f')
        self.assertTrue(callable(render.call_args.kwargs['frame_effect']))

if __name__ == '__main__':
    unittest.main()
