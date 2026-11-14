import unittest
from video_prototype.gaze_planner import gaze_at
from video_prototype.guest_planner import GUEST_NAMES, DECOR_NAMES, make_plan, local_time


class GuestPlannerTests(unittest.TestCase):
    def test_always_selects_guest_varies_and_is_reproducible(self):
        samples = set()
        for seed in range(500):
            first = make_plan(240, seed)
            self.assertEqual(first, make_plan(240, seed))
            names = tuple(a.name for a in first.guests)
            self.assertTrue(names)
            self.assertEqual(len(names), len(set(names)))
            self.assertTrue(set(names) <= set(GUEST_NAMES))
            samples.add(names)
        self.assertEqual(len(samples), 2**len(GUEST_NAMES)-1)
        self.assertTrue(any(len(s) > 1 for s in samples))

    def test_decor_varies_independently_and_guests_remain_required(self):
        combinations = set()
        for seed in range(2000):
            plan = make_plan(240, seed)
            guests = tuple(g.name for g in plan.guests)
            decor = tuple(d.name for d in plan.decorations)
            self.assertTrue(guests)
            self.assertTrue(set(decor) <= set(DECOR_NAMES))
            self.assertTrue(all(look.guest in guests for look in plan.looks))
            self.assertEqual(plan, make_plan(240, seed))
            combinations.add((guests, decor))
        self.assertEqual(len(combinations), 7 * 8)

    def test_every_guest_and_gaze_stays_in_valid_frames(self):
        for frames in (1,2,12,23,24,48,76,77,100,132,240,720,1439):
            for seed in range(40):
                plan = make_plan(frames, seed)
                guests = {g.name: g for g in plan.guests}
                for g in plan.guests + plan.decorations:
                    self.assertLessEqual(0, g.start_frame)
                    self.assertLess(g.start_frame, g.end_frame)
                    self.assertLessEqual(g.end_frame, frames)
                    self.assertLessEqual(g.start_frame, g.visible_from_frame)
                    self.assertLess(g.visible_from_frame, g.visible_until_frame)
                    self.assertLessEqual(g.visible_until_frame, g.end_frame)
                    self.assertIsNone(local_time(g, g.end_frame))
                    self.assertIsNotNone(local_time(g, g.visible_from_frame))
                for look in plan.looks:
                    guest = guests[look.guest]
                    self.assertGreaterEqual(look.start + 1e-10, guest.visible_from_frame / plan.fps)
                    self.assertLessEqual(look.end, guest.visible_until_frame / plan.fps + 1e-10)
                for a, b in zip(plan.looks, plan.looks[1:]):
                    self.assertLessEqual(a.end, b.start)
                for i in range(frames):
                    target, amount = gaze_at(i / plan.fps, plan.looks)
                    if target:
                        g = guests[target]
                        self.assertLessEqual(g.visible_from_frame, i)
                        self.assertLess(i, g.visible_until_frame)
                        self.assertTrue(0 <= amount <= 1)

    def test_short_clip_has_nonempty_visible_pose(self):
        for seed in range(30):
            plan = make_plan(1, seed)
            self.assertTrue(plan.guests)
            self.assertTrue(all(g.start_frame == 0 and g.end_frame == 1 for g in plan.guests))
            self.assertTrue(all(g.mode in ('short_hold','short_hedgehog') for g in plan.guests))
            self.assertTrue(all(local_time(g, 0) is not None for g in plan.guests))

    def test_natural_length_avoids_sped_up_animation(self):
        for seed in range(25):
            plan = make_plan(24 * 15, seed)
            self.assertTrue(all(g.mode == 'normal' for g in plan.guests))
            for g in plan.guests:
                self.assertAlmostEqual(local_time(g,g.start_frame+1)-local_time(g,g.start_frame), 1/24)

    def test_reject_invalid_parameters(self):
        for n in (0,-1,1.5,True):
            with self.assertRaises(ValueError): make_plan(n, 'seed')
        for seed in (None, 1.5, True):
            with self.assertRaises(ValueError): make_plan(24, seed)
        for fps in (0, -1, 1.5, True):
            with self.assertRaises(ValueError): make_plan(24, 'seed', fps)


if __name__ == '__main__':
    unittest.main()
