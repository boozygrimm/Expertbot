import unittest
from unittest.mock import MagicMock, patch
from PIL import Image, ImageChops
from video_prototype import flask_render as flask


class FlaskTests(unittest.TestCase):
    def test_effect_stays_in_flask_and_disappears(self):
        source = Image.new('RGB', (flask.SIZE, flask.SIZE), (20, 20, 20))
        frame = source.tobytes()
        self.assertEqual(flask.frame_with_bubbles(frame, .5), frame)
        self.assertEqual(flask.frame_with_bubbles(frame, 7.1), frame)
        effect = Image.frombytes('RGB', source.size, flask.frame_with_bubbles(frame, 3))
        difference = ImageChops.difference(effect, source)
        self.assertIsNotNone(difference.getbbox())
        outside_mask = flask.FLASK_MASK.point(lambda v: 255 if v == 0 else 0)
        outside = ImageChops.multiply(difference.convert('L'), outside_mask)
        self.assertIsNone(outside.getbbox())

    def test_bubbles_move_over_time(self):
        a = flask.bubble_layer(2).tobytes()
        b = flask.bubble_layer(2.25).tobytes()
        self.assertNotEqual(a, b)

    def test_audio_frame_count_and_layers(self):
        process = MagicMock()
        process.wait.return_value = 0
        frame = Image.new('RGB', (flask.SIZE, flask.SIZE)).tobytes()
        with patch.object(flask, 'audio_levels', return_value=[0, 1, 0]), \
             patch.object(flask, 'make_frames'), \
             patch.object(flask, 'prepare_frames', return_value={(m, e): None for m in (0,1) for e in (0,1)}), \
             patch.object(flask, 'frame_with_layers', return_value=frame) as smoke, \
             patch.object(flask.subprocess, 'Popen', return_value=process):
            flask.render('a.wav', 'closed', 'open', 'blink', 'plate', '/tmp/flask.mp4')
        self.assertEqual(process.stdin.write.call_count, 3)
        self.assertEqual(smoke.call_count, 3)
        process.stdin.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
