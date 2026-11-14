import array
from pathlib import Path
import tempfile
import unittest
import wave
from video_prototype import scripted_reaction_demo as demo, scripted_reaction_render as scene
from video_prototype.gaze_planner import gaze_at


def write_wave(path, samples, rate=8000):
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate)
        wav.writeframes(array.array('h', samples).tobytes())


class ScriptedReactionTests(unittest.TestCase):
    def test_join_preserves_audio_and_exact_second_start(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b, out = (Path(folder)/n for n in ('a.wav','b.wav','out.wav'))
            write_wave(a, [12]*8000); write_wave(b, [34]*16000)
            start, duration = demo.join_audio(a,b,out)
            self.assertAlmostEqual(start,1.25)
            self.assertAlmostEqual(duration,3.25)
            with wave.open(str(out),'rb') as wav:
                data = array.array('h', wav.readframes(wav.getnframes()))
            self.assertEqual(data[:8000], array.array('h', [12]*8000))
            self.assertEqual(data[8000:10000], array.array('h', [0]*2000))
            self.assertEqual(data[10000:], array.array('h', [34]*16000))

    def test_schedule_beholder_left_and_sensei_right_when_spoken(self):
        start, end, hedge_start, looks = scene.scene_schedule(4., 9.)
        self.assertAlmostEqual(start,3.2)
        self.assertGreater(end,4.)
        self.assertEqual(gaze_at(1.0,looks)[0], 'beholder')
        self.assertEqual(gaze_at(4.5,looks)[0], 'pai_mei')
        self.assertEqual(gaze_at(5.8,looks)[0], 'hedgehog')
        for earlier, later in zip(looks, looks[1:]):
            self.assertLess(earlier.end, later.start)
        for original, target in zip([(268,171),(313,166)], scene.TARGETS['beholder']):
            self.assertLess(target[0], original[0])
        for original, target in zip([(268,171),(313,166)], scene.TARGETS['pai_mei']):
            self.assertGreater(target[0], original[0])
        self.assertIsNotNone(scene.cameo_time(4, start,end))
        self.assertIsNone(scene.cameo_time(end,start,end))

    def test_short_chunk_stays_inside_guest_visibility(self):
        for second_start, duration in ((2,4), (3,6), (6,9), (8,12)):
            start, end, hedge, looks = scene.scene_schedule(second_start,duration)
            for look in looks:
                self.assertLessEqual(look.end,duration)
                if look.guest == 'beholder': self.assertLess(look.end, 5.5)
                if look.guest == 'pai_mei':
                    self.assertGreaterEqual(look.start,start)
                    self.assertLessEqual(look.end,end)
                if look.guest == 'hedgehog':
                    self.assertGreaterEqual(look.start,hedge+.5)
                    self.assertLessEqual(look.end,hedge+3.2)


if __name__ == '__main__':
    unittest.main()
