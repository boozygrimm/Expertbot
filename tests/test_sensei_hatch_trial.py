import unittest

try:
    from PIL import Image, ImageChops
    from news_prototype import bender_trial, sensei_hatch, sensei_hatch_trial
except ImportError:
    Image = None
    sensei_hatch = None


@unittest.skipIf(sensei_hatch is None, 'Pillow is optional for this isolated prototype')
class SenseiHatchTests(unittest.TestCase):
    def test_hatch_opens_holds_and_closes(self):
        f = sensei_hatch.openness
        self.assertEqual(f(0, 1), 0)
        self.assertEqual(f(1, 1), 0)
        self.assertAlmostEqual(f(1.3, 1), .5)
        self.assertEqual(f(2, 1), 1)
        self.assertAlmostEqual(f(4.1, 1), .5)
        self.assertEqual(f(5, 1), 0)

    def test_hatch_overlay_is_local_and_zero_is_identity(self):
        assets = sensei_hatch.prepare()
        scene = assets[0].convert('RGB').resize(bender_trial.SIZE)
        same = sensei_hatch.at_fraction(scene, assets, 0)
        self.assertEqual(scene.tobytes(), same.tobytes())
        opened = sensei_hatch.at_fraction(scene, assets, 1)
        bbox = ImageChops.difference(scene, opened).getbbox()
        self.assertIsNotNone(bbox)
        self.assertGreater(bbox[0], 540)
        self.assertLess(bbox[2], 690)
        self.assertLess(bbox[3], 200)

    def test_overlay_approaches_closed_head_continuously(self):
        assets = sensei_hatch.prepare()
        tiny = sensei_hatch.overlay_at_fraction(assets, .0001)
        small = sensei_hatch.overlay_at_fraction(assets, .01)
        self.assertLessEqual(tiny.getchannel('A').getextrema()[1], 1)
        self.assertGreater(small.getchannel('A').getextrema()[1], tiny.getchannel('A').getextrema()[1])

    def test_lid_rotates_around_fixed_hinge(self):
        lid = Image.new('RGBA', sensei_hatch.NATIVE, (0, 0, 0, 0))
        pivot_x, pivot_y = sensei_hatch.HINGE
        from PIL import ImageDraw
        draw = ImageDraw.Draw(lid)
        draw.ellipse((pivot_x - 2, pivot_y - 2, pivot_x + 2, pivot_y + 2), fill=(255, 255, 255, 255))
        draw.ellipse((pivot_x - 102, pivot_y - 2, pivot_x - 98, pivot_y + 2), fill=(255, 0, 0, 255))
        rotated = sensei_hatch.rotate_lid(lid, 1)
        self.assertGreater(rotated.getpixel((pivot_x, pivot_y))[3], 0)
        # The left tip rises while the right pivot remains fixed.
        bbox = rotated.getchannel('R').getbbox()
        self.assertLess(bbox[1], pivot_y - 25)
        self.assertGreaterEqual(bbox[3], pivot_y)

    def test_event_at_third_turn_and_fits_duration(self):
        active = (['Бендер'] * 72 + [None] * 8 + ['Киберсенсей'] * 72 +
                  [None] * 8 + ['Бендер'] * 72 + [None] * 8 + ['Киберсенсей'] * 72)
        start, hold, motion = sensei_hatch_trial.event_window(active)
        self.assertGreaterEqual(start, 160 / 24)
        self.assertLessEqual(start + hold + 2 * motion, len(active) / 24)
        self.assertIsNone(sensei_hatch_trial.event_window([]))


if __name__ == '__main__':
    unittest.main()
