import array
from pathlib import Path
import tempfile
import unittest
import wave

try:
    from PIL import Image  # noqa: F401
    from news_prototype import podcast_video_trial as trial
    from news_prototype import podcast_audio_trial as audio_trial
except ImportError:
    trial = None


def make_segment(path: Path, seconds=1):
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        # Add a regular alternating loud/silent cadence.
        samples = array.array('h', [4000 if (i // 3000) % 2 == 0 else 0 for i in range(24000 * seconds)])
        wav.writeframes(samples.tobytes())


@unittest.skipIf(trial is None, 'Pillow is optional for this isolated prototype')
class PodcastVideoTrialTests(unittest.TestCase):
    def make_dialogue(self, root):
        paths = [root / f'{i}-normalized.wav' for i in range(1, 5)]
        for path in paths:
            make_segment(path)
        audio_trial.assemble_wav(paths, root / 'dialogue.wav')
        return paths

    def test_turn_boundaries_and_pauses_follow_source_segments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.make_dialogue(root)
            _, speakers, states = trial.dialogue_timeline(root)
            self.assertEqual(speakers[2], 'Бендер')
            self.assertIsNone(speakers[25])
            self.assertEqual(speakers[34], 'Киберсенсей')
            self.assertTrue(any(states[i] for i, speaker in enumerate(speakers) if speaker == 'Бендер'))
            self.assertTrue(any(states[i] for i, speaker in enumerate(speakers) if speaker == 'Киберсенсей'))
            self.assertFalse(any(states[i] for i, speaker in enumerate(speakers) if speaker is None))

    def test_rejects_audio_which_does_not_match_segments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            parts = self.make_dialogue(root)
            make_segment(parts[0], seconds=2)
            with self.assertRaises(ValueError):
                trial.dialogue_timeline(root)

    def test_independent_mouth_frames_and_non_face_background(self):
        frames = trial.prepare_frames()
        self.assertEqual(len(frames), 8)
        closed = Image.frombytes('RGB', trial.bender.SIZE, frames[False, False, False])
        sensei = Image.frombytes('RGB', trial.bender.SIZE, frames[False, False, True])
        bender = Image.frombytes('RGB', trial.bender.SIZE, frames[True, False, False])
        self.assertEqual(closed.getpixel((0, 0)), sensei.getpixel((0, 0)))
        self.assertEqual(closed.getpixel((0, 0)), bender.getpixel((0, 0)))
        self.assertNotEqual(closed.tobytes(), sensei.tobytes())
        self.assertNotEqual(closed.tobytes(), bender.tobytes())


if __name__ == '__main__':
    unittest.main()
