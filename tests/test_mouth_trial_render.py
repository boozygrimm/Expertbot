import array
from pathlib import Path
import tempfile
import unittest
import wave
from unittest.mock import MagicMock, patch
from PIL import Image
from video_prototype import mouth_trial_render as trial
from video_prototype.render import mouth_states


class MouthTrialTests(unittest.TestCase):
    def test_silence_and_uninterrupted_speech(self):
        self.assertEqual(trial.trial_states([0]*50), [False]*50)
        self.assertEqual(trial.trial_states([1000]*50), [True]*50)

    def test_stronger_threshold_reacts_to_quiet_transitions(self):
        levels = [0]*5 + [1000]*10 + [300]*3 + [1000]*10 + [0]*5
        old = list(mouth_states(levels))
        new = trial.trial_states(levels)
        self.assertTrue(all(old[15:18]))
        self.assertFalse(any(new[15:18]))
        self.assertEqual(new[:5], [False]*5)
        self.assertEqual(new[-5:], [False]*5)

    def test_one_frame_spike_does_not_jitter(self):
        levels = [0]*5 + [1000] + [0]*5 + [1000]*5
        states = trial.trial_states(levels)
        self.assertFalse(states[5])
        self.assertTrue(all(states[-5:]))

    def test_short_windows_frame_count_and_detect_silence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'sample.wav'
            with wave.open(str(path), 'wb') as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(4800)
                wav.writeframes(array.array('h', [0]*2400+[1000]*2400).tobytes())
            levels = trial.quick_levels(path)
        self.assertEqual(len(levels), 24)
        self.assertEqual(levels[0], 0)
        self.assertGreater(levels[-1], 0)

    def test_renderer_emits_one_frame_per_audio_frame(self):
        process = MagicMock()
        process.wait.return_value = 0
        frame = Image.new('RGB',(trial.SIZE,trial.SIZE)).tobytes()
        with patch.object(trial, 'quick_levels', return_value=[0, 100, 100, 0]), \
             patch.object(trial, 'audio_levels', return_value=[0,100,100,0]), \
             patch.object(trial, 'make_frames'), \
             patch.object(trial, 'prepare_frames', return_value={(m,e): None for m in (0,1) for e in (0,1)}), \
             patch.object(trial, 'frame_with_layers', return_value=frame), \
             patch.object(trial.subprocess, 'Popen', return_value=process):
            trial.render('a','b','c','d','e','/tmp/mouth.mp4')
        self.assertEqual(process.stdin.write.call_count, 4)
        process.stdin.close.assert_called_once()

if __name__ == '__main__':
    unittest.main()
