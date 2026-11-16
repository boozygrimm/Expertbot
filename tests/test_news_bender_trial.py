import array
import tempfile
import unittest
import wave
from pathlib import Path

try:
    from PIL import Image
    from news_prototype import bender_trial as trial
except ImportError:
    Image = None
    trial = None


@unittest.skipIf(trial is None, 'Pillow is optional for the isolated news prototype')
class BenderTrialTests(unittest.TestCase):
    def test_mouth_tracks_audio_and_silence(self):
        states = trial.mouth_states([0] * 8 + [1000] * 20 + [0] * 8)
        self.assertFalse(any(states[:8]))
        self.assertTrue(any(states[8:28]))
        self.assertFalse(any(states[28:]))
        self.assertEqual(trial.mouth_states([0] * 4), [False] * 4)

    def test_blink_is_brief(self):
        self.assertTrue(trial.is_blink(1.85))
        self.assertFalse(trial.is_blink(2.1))
        self.assertFalse(trial.is_blink(0))

    def test_mouth_and_blink_masks_do_not_change_background(self):
        opened = Image.new('RGB', trial.SIZE, (255, 0, 0))
        closed = Image.new('RGB', trial.SIZE, (0, 255, 0))
        blink = Image.new('RGB', trial.SIZE, (0, 0, 255))
        frames = trial.face_frames(opened, closed, blink)
        closed_frame = Image.frombytes('RGB', trial.SIZE, frames[False, True])
        self.assertEqual(closed_frame.getpixel((0, 0)), (255, 0, 0))
        self.assertEqual(closed_frame.getpixel((313, 243)), (0, 255, 0))
        self.assertEqual(closed_frame.getpixel((330, 194)), (0, 0, 255))

    def test_rejects_truncated_audio_and_pads_valid_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sample.wav'
            samples = array.array('h', [1, -1] * 1200)
            trial.write_wav(path, samples, 24000)
            rate, actual = trial.read_pcm(path)
            self.assertEqual(actual, samples)
            self.assertEqual(rate, 24000)
            self.assertEqual(len(trial.padded(actual, rate)), len(samples) + round(rate * 1.6))
            data = path.read_bytes()
            path.write_bytes(data[:-12])
            with self.assertRaises(ValueError):
                trial.read_pcm(path)


if __name__ == '__main__':
    unittest.main()
