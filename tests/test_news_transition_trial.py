"""Tests for the opt-in, offline television transition sketch."""
import unittest

try:
    from PIL import Image
    from news_prototype import transition_trial as trial
    from news_prototype import press_trial as press
except ImportError:
    Image = None
    trial = None
    press = None


@unittest.skipIf(trial is None, 'Pillow is optional for the isolated news prototype')
class NewsTransitionTrialTests(unittest.TestCase):
    def test_on_switch_and_off(self):
        self.assertEqual(trial.screen_visibility(0), 0)
        self.assertEqual(trial.screen_visibility(2), 1)
        self.assertEqual(trial.screen_visibility(5), 0)
        self.assertEqual(trial.screen_visibility(6), 1)
        self.assertEqual(trial.screen_visibility(10), 0)
        self.assertEqual(trial.story_label(2)[0], 'СЮЖЕТ 1')
        self.assertEqual(trial.story_label(6)[0], 'СЮЖЕТ 2')
        self.assertEqual(trial.story_label(10)[0], '')

    def test_pulse_only_at_press_moments(self):
        for timestamp in trial.PRESSES:
            self.assertAlmostEqual(trial.button_pulse(timestamp), 1)
        self.assertEqual(trial.button_pulse(3), 0)

    def test_mask_keeps_remote_and_limits_effect_to_screen(self):
        mask = trial.screen_mask()
        self.assertEqual(mask.getpixel(trial.point(700, 350)), 255)
        self.assertEqual(mask.getpixel(trial.point(1080, 590)), 0)
        self.assertEqual(mask.getpixel(trial.point(40, 40)), 0)

    def test_press_is_synchronized_with_each_transition(self):
        for timestamp in trial.PRESSES:
            self.assertAlmostEqual(press.press_amount(timestamp), 1)
            self.assertEqual(press.press_amount(timestamp - 0.4), 0)
            self.assertEqual(press.press_amount(timestamp + 0.3), 0)
        self.assertEqual(press.press_amount(3), 0)

    def test_click_audio_is_silent_between_presses(self):
        samples = press.audio_samples()
        self.assertEqual(len(samples), round(trial.DURATION * press.SAMPLE_RATE))
        self.assertEqual(max(map(abs, samples[:int(0.5 * press.SAMPLE_RATE)])), 0)
        for timestamp in trial.PRESSES:
            start = round(timestamp * press.SAMPLE_RATE)
            self.assertGreater(max(map(abs, samples[start:start + 1000])), 0)
        self.assertEqual(max(map(abs, samples[int(3 * press.SAMPLE_RATE):int(3.2 * press.SAMPLE_RATE)])), 0)

    def test_finger_overlay_changes_frame_only_during_press(self):
        base = Image.new('RGB', trial.SIZE, (100, 100, 100))
        layer = press.finger_layer(base)
        self.assertEqual(press.add_finger(base, layer, 0).tobytes(), base.tobytes())
        self.assertEqual(press.add_finger(base, layer, 1).size, base.size)
        self.assertIsNotNone(layer.getchannel('A').getbbox())

    def test_frame_size_and_mode(self):
        base = Image.new('RGB', trial.SIZE, (100, 100, 100))
        for t in (0, 1.06, 2, 5.01, 6, 9.39, 10):
            with self.subTest(t=t):
                frame = trial.make_frame(base, trial.screen_mask(), t, trial.find_font())
                self.assertEqual(frame.size, trial.SIZE)
                self.assertEqual(frame.mode, 'RGB')


if __name__ == '__main__':
    unittest.main()
