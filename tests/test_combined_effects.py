import itertools
import unittest
from PIL import Image, ImageChops
from video_prototype.render import SIZE, FPS
from video_prototype import beholder_render as beast, hedgehog_render as hedge
from video_prototype import flask_render as flask, pyramid_render as pyramid, pai_mei_render as pai
from video_prototype import coin_drop_render as coin
from video_prototype import combined_effects_render as combined


class CombinedEffectsTests(unittest.TestCase):
    def test_effect_footprints_do_not_overlap_at_any_video_frame(self):
        # A fully opaque portrait is conservative versus the real cutout.
        portrait = Image.new('RGBA', (round(SIZE*.33), round(SIZE*.37)), (255,255,255,255))
        hedgehog = hedge.make_run_frames(hedge.HERE / 'assets/hedgehog.png')
        beholder = beast.prepare_sprites()
        frame = Image.new('RGB', (SIZE, SIZE), (0,0,0)).tobytes()
        actions = {
            'bubbles': lambda t: flask.frame_with_bubbles(frame, t),
            'beholder': lambda t: beast.frame_with_beholder(frame, t, beholder),
            'pyramid': lambda t: pyramid.frame_with_pyramid(frame, t),
            'hedgehog': lambda t: hedge.frame_with_hedgehog(frame, t, hedgehog),
            'pai_mei': lambda t: pai.frame_with_cameo(frame, t, portrait),
            'coin_trial': lambda t: coin.frame_with_coin(frame, t),
        }
        # Compare changed pixels on each frame; any touch is a conflict.
        for i in range(7*FPS):
            t = i / FPS
            masks = {name: Image.frombytes('RGB', (SIZE, SIZE), action(t)).convert('L').point(
                lambda value: 255 if value else 0) for name, action in actions.items()}
            for left, right in itertools.combinations(masks, 2):
                overlap = ImageChops.multiply(masks[left], masks[right]).getbbox()
                self.assertIsNone(overlap, f'{left} overlaps {right} at {t:.3f}: {overlap}')

    def test_combined_layer_runs_every_effect(self):
        calls = []
        def simple(name):
            def action(frame, seconds, *args):
                calls.append(name)
                return frame + name.encode()
            return action
        from unittest.mock import patch
        with patch.object(combined, 'make_run_frames'), patch.object(combined, 'prepare_beholder'), \
             patch.object(combined, 'prepare_portrait'), \
             patch.object(combined, 'frame_with_bubbles', side_effect=simple('b')), \
             patch.object(combined, 'frame_with_beholder', side_effect=simple('m')), \
             patch.object(combined, 'frame_with_pyramid', side_effect=simple('p')), \
             patch.object(combined, 'frame_with_hedgehog', side_effect=simple('h')), \
             patch.object(combined, 'frame_with_cameo', side_effect=simple('c')):
            effect = combined.make_effect('portrait')
            self.assertEqual(effect(b'', 3), b'bmphc')
            self.assertEqual(calls, ['b','m','p','h','c'])


if __name__ == '__main__':
    unittest.main()
