import unittest
from unittest.mock import patch
from PIL import Image
from video_prototype import flask_eye_render as eye


class FlaskEyeTests(unittest.TestCase):
    def test_eye_appears_looks_and_disappears(self):
        self.assertIsNone(eye.eye_layer(1))
        self.assertIsNone(eye.eye_layer(7))
        self.assertNotEqual(eye.eye_layer(3.1).tobytes(), eye.eye_layer(4).tobytes())
        frame = Image.new('RGB', (eye.SIZE, eye.SIZE), (30, 30, 30)).tobytes()
        self.assertEqual(eye.frame_with_eye(frame, 1), frame)
        self.assertNotEqual(eye.frame_with_eye(frame, 4), frame)
        self.assertEqual(eye.frame_with_eye(frame, 7), frame)

    def test_reuses_base_render_with_eye_effect(self):
        with patch.object(eye, 'render_flask') as render:
            eye.render('audio', 'closed', 'open', 'blink', 'plate', 'output')
        self.assertIs(render.call_args.kwargs['frame_effect'], eye.frame_with_eye)

if __name__ == '__main__':
    unittest.main()
