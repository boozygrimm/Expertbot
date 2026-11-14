import itertools
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from PIL import Image, ImageChops
from video_prototype import random_guest_render as renderer
from video_prototype import guest_planner as planner
from video_prototype import hedgehog_render as hedge, beholder_render as beast, pai_mei_render as pai
from video_prototype import flask_render as flask, pyramid_render as pyramid, coin_drop_render as coin


class RandomGuestRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.size = (renderer.SIZE, renderer.SIZE)
        cls.base_image = Image.new('RGB', cls.size, (79,80,81))
        cls.base = cls.base_image.tobytes()
        cls.hedgehog = hedge.make_run_frames(hedge.HERE/'assets/hedgehog.png')
        cls.beholder = beast.prepare_sprites()
        # This is a conservative opaque region, larger than the cut-out portrait.
        cls.portrait = Image.new('RGBA', (round(renderer.SIZE*.33),round(renderer.SIZE*.37)), (255,255,255,255))

    def test_guest_footprints_never_overlap_for_any_independent_timings(self):
        masks = {}
        specs = {'hedgehog': (1., 4.2), 'beholder': (1.5, 7.), 'pai_mei': (2., 6.5),
                 'bubbles': (1., 7.), 'pyramid': (1.5, 7.), 'coin': (2., 5.6)}
        for name, (start, end) in specs.items():
            mask = Image.new('L', self.size)
            for i in range(int(start*24), int(end*24)):
                t = i/24
                if name == 'hedgehog':
                    rendered = hedge.frame_with_hedgehog(self.base,t,self.hedgehog)
                elif name == 'beholder':
                    rendered = beast.frame_with_beholder(self.base,t,self.beholder)
                elif name == 'pai_mei':
                    rendered = pai.frame_with_cameo(self.base,t,self.portrait)
                elif name == 'bubbles':
                    rendered = flask.frame_with_bubbles(self.base,t)
                elif name == 'pyramid':
                    rendered = pyramid.frame_with_pyramid(self.base,t)
                else:
                    rendered = coin.frame_with_coin(self.base,t)
                difference = ImageChops.difference(Image.frombytes('RGB', self.size, rendered),
                                                   self.base_image).convert('L').point(lambda p: 255 if p else 0)
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

    def test_each_decor_really_draws_on_a_one_frame_clip(self):
        found = set()
        for seed in range(100):
            plan = planner.make_plan(1,seed)
            for decoration in plan.decorations:
                t = planner.local_time(decoration,0)
                if decoration.name == 'bubbles':
                    out = flask.frame_with_bubbles(self.base,t)
                elif decoration.name == 'pyramid':
                    out = pyramid.frame_with_pyramid(self.base,t)
                else:
                    out = coin.frame_with_coin(self.base,t)
                self.assertNotEqual(out,self.base,decoration.name)
                found.add(decoration.name)
        self.assertEqual(found,set(planner.DECOR_NAMES))

    def test_all_six_selected_elements_are_applied_by_renderer(self):
        from contextlib import ExitStack
        plan = next(planner.make_plan(240, seed) for seed in range(500)
                    if len(planner.make_plan(240, seed).guests) == 3 and
                       len(planner.make_plan(240, seed).decorations) == 3)
        eye = Image.new('RGB', (renderer.EYE_BOX[2]-renderer.EYE_BOX[0],
                                renderer.EYE_BOX[3]-renderer.EYE_BOX[1]))
        names = {'hedgehog': 'frame_with_hedgehog', 'beholder': 'frame_with_beholder',
                 'pai_mei': 'frame_with_cameo', 'bubbles': 'frame_with_bubbles',
                 'pyramid': 'frame_with_pyramid', 'coin': 'frame_with_coin'}
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            portrait = Path(folder)/'portrait.png'; portrait.touch()
            stack.enter_context(patch.object(renderer,'prepare_eye_frames',return_value=[eye]*11))
            stack.enter_context(patch.object(renderer,'make_run_frames',return_value=self.hedgehog))
            stack.enter_context(patch.object(renderer,'prepare_sprites',return_value=self.beholder))
            stack.enter_context(patch.object(renderer,'prepare_portrait',return_value=self.portrait))
            functions = {name: stack.enter_context(patch.object(renderer, function,
                         side_effect=lambda frame,*args: frame)) for name,function in names.items()}
            effect = renderer.make_effect('closed.png',portrait,plan)
            for element in plan.guests + plan.decorations:
                effect(self.base,element.visible_from_frame/plan.fps)
                self.assertTrue(functions[element.name].called, element.name)

    def test_gaze_tracks_hedgehog_approximately_across_the_table(self):
        indices = [renderer.hedgehog_eye_bin(t) for t in (1.7,2.1,2.4,2.7,3.1)]
        self.assertEqual(indices[0],0)
        self.assertEqual(indices[-1],4)
        self.assertEqual(indices,sorted(indices))

    def test_solo_hedgehog_has_visible_eye_frames_on_reported_scene(self):
        # Exercise actual rendered eye pixels for a real 26.3-second scene,
        # not just the scheduled look in its plan.
        plan = planner.make_plan(631, 'c1f0b06b-6a36-42f9-8ab7-d2f6d87689e3')
        self.assertEqual([g.name for g in plan.guests], ['hedgehog'])
        self.assertEqual(plan.looks[0].start, 21.125)
        with tempfile.TemporaryDirectory() as folder:
            closed = Path(folder) / 'closed.png'
            self.base_image.save(closed)
            effect = renderer.make_effect(closed, None, plan)
            before = Image.frombytes('RGB', self.size, effect(self.base, 20.0)).crop(renderer.EYE_BOX)
            looking = Image.frombytes('RGB', self.size, effect(self.base, 21.5)).crop(renderer.EYE_BOX)
            self.assertIsNotNone(ImageChops.difference(before, looking).getbbox())

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
