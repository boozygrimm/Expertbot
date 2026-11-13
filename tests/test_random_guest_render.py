import itertools
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from PIL import Image, ImageChops
from video_prototype import random_guest_render as renderer
from video_prototype import guest_planner as planner
from video_prototype import hedgehog_render as hedge, beholder_render as beast, pai_mei_render as pai


class RandomGuestRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.size = (renderer.SIZE, renderer.SIZE)
        cls.base = Image.new('RGB', cls.size, (0,0,0)).tobytes()
        cls.hedgehog = hedge.make_run_frames(hedge.HERE/'assets/hedgehog.png')
        cls.beholder = beast.prepare_sprites()
        # This is a conservative opaque region, larger than the cut-out portrait.
        cls.portrait = Image.new('RGBA', (round(renderer.SIZE*.33),round(renderer.SIZE*.37)), (255,255,255,255))

    def test_guest_footprints_never_overlap_for_any_independent_timings(self):
        masks = {}
        specs = {'hedgehog': (1., 4.2), 'beholder': (1.5, 7.), 'pai_mei': (2., 6.5)}
        for name, (start, end) in specs.items():
            mask = Image.new('L', self.size)
            for i in range(int(start*24), int(end*24)):
                t = i/24
                if name == 'hedgehog':
                    rendered = hedge.frame_with_hedgehog(self.base,t,self.hedgehog)
                elif name == 'beholder':
                    rendered = beast.frame_with_beholder(self.base,t,self.beholder)
                else:
                    rendered = pai.frame_with_cameo(self.base,t,self.portrait)
                difference = Image.frombytes('RGB', self.size, rendered).convert('L').point(lambda p: 255 if p else 0)
                mask = ImageChops.lighter(mask,difference)
            self.assertIsNotNone(mask.getbbox(), name)
            masks[name] = mask
        for a,b in itertools.combinations(masks,2):
            self.assertIsNone(ImageChops.multiply(masks[a],masks[b]).getbbox(),f'{a} vs {b}')

    def test_each_guest_really_draws_on_a_one_frame_clip(self):
        found = set()
        for seed in range(50):
            plan = planner.make_plan(1,seed)
            for guest in plan.guests:
                t = planner.local_time(guest,0)
                if guest.name == 'hedgehog':
                    out = hedge.frame_with_hedgehog(self.base,t,self.hedgehog)
                elif guest.name == 'beholder':
                    out = beast.frame_with_beholder(self.base,t,self.beholder)
                else:
                    out = pai.frame_with_cameo(self.base,t,self.portrait)
                self.assertNotEqual(out,self.base,guest.name)
                found.add(guest.name)
        self.assertEqual(found,set(planner.GUEST_NAMES))

    def test_gaze_tracks_hedgehog_approximately_across_the_table(self):
        indices = [renderer.hedgehog_eye_bin(t) for t in (1.7,2.1,2.4,2.7,3.1)]
        self.assertEqual(indices[0],0)
        self.assertEqual(indices[-1],4)
        self.assertEqual(indices,sorted(indices))

    def test_missing_sensei_image_fails_only_if_sensei_was_selected(self):
        for seed in range(100):
            plan = planner.make_plan(240,seed)
            if 'pai_mei' in [g.name for g in plan.guests]:
                with self.assertRaises(FileNotFoundError):
                    renderer.make_effect('closed.png',None,plan)
                break
        else:
            self.fail('No seed selected sensei')

    def test_render_writes_reproducible_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder)/'scene.json'
            with patch.object(renderer,'quick_levels', return_value=[1]*240), \
                 patch.object(renderer,'make_effect',return_value=lambda frame,time: frame), \
                 patch.object(renderer,'base_render') as base:
                plan = renderer.render('a','b','c','d','e',None,'out.mp4','seed',manifest)
            import json
            self.assertEqual(json.loads(manifest.read_text()), plan.to_dict())
            self.assertEqual(plan,planner.make_plan(240,'seed'))
            base.assert_called_once()


if __name__ == '__main__':
    unittest.main()
