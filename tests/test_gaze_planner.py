import itertools
import unittest
from video_prototype.gaze_planner import Guest, gaze_at, plan_looks
from video_prototype.multi_gaze_render import GUESTS


class GazePlannerTests(unittest.TestCase):
    def test_simultaneous_guests_take_turns_with_pause(self):
        looks = plan_looks(GUESTS, 10)
        self.assertEqual([x.guest for x in looks], ['hedgehog', 'beholder', 'pai_mei'])
        by_name = {g.name: g for g in GUESTS}
        for look in looks:
            target = by_name[look.guest]
            self.assertGreaterEqual(look.start, target.visible_from)
            self.assertLessEqual(look.end, target.visible_until)
        for a, b in zip(looks, looks[1:]):
            self.assertGreaterEqual(b.start - a.end, .2499)

    def test_short_audio_and_short_lived_guest(self):
        self.assertEqual(plan_looks(GUESTS, 1), [])
        guests = [Guest('quick', 1, 1.4), Guest('long', 1, 3)]
        looks = plan_looks(guests, 2)
        self.assertEqual([x.guest for x in looks], ['long'])
        self.assertLessEqual(looks[0].end, 2)
        self.assertEqual(plan_looks(guests, 0), [])

    def test_no_look_after_target_disappears_for_all_combinations(self):
        for n in range(len(GUESTS)+1):
            for group in itertools.combinations(GUESTS, n):
                for length in (2.0, 3.0, 5.0, 8.0):
                    looks = plan_looks(group, length)
                    selected = {g.name: g for g in group}
                    for i in range(int(length*24)):
                        t = i/24
                        name, amount = gaze_at(t, looks)
                        if name:
                            self.assertIn(name, selected)
                            self.assertLess(t, selected[name].visible_until)
                            self.assertGreaterEqual(t, selected[name].visible_from)
                            self.assertGreaterEqual(amount, 0)
                            self.assertLessEqual(amount, 1)

    def test_gaze_smoothly_starts_and_ends(self):
        looks = plan_looks([Guest('guest', 1, 4)], 10)
        look = looks[0]
        self.assertEqual(gaze_at(look.start, looks)[1], 0)
        self.assertEqual(gaze_at(look.end, looks), (None, 0.))
        self.assertGreater(gaze_at(look.start+.2, looks)[1], gaze_at(look.start+.1, looks)[1])


if __name__ == '__main__':
    unittest.main()
