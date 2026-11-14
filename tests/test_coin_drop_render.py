import unittest
from unittest.mock import patch
from PIL import Image, ImageChops
from video_prototype import coin_drop_render as coin
from video_prototype import pai_mei_render as pai

class CoinDropTests(unittest.TestCase):
    def test_reaches_bottom_edge_before_disappearing(self):
        self.assertIsNone(coin.coin_pose(1))
        self.assertLess(coin.coin_pose(4)[0], coin.coin_pose(5.4)[0])
        self.assertGreater(coin.coin_pose(5.4)[0], 437)
        frame = Image.new('RGB', (coin.SIZE, coin.SIZE), (40,40,40)).tobytes()
        self.assertNotEqual(coin.frame_with_coin(frame, 5.3), frame)
        self.assertEqual(coin.frame_with_coin(frame, 5.6), frame)
        self.assertEqual(coin.frame_with_coin(frame, 5.6 - 1/24), frame)

    def test_respects_circle_and_never_hits_cameo(self):
        base = Image.new('RGB',(coin.SIZE,coin.SIZE),(0,0,0)).tobytes()
        portrait = Image.new('RGBA',(round(coin.SIZE*.33),round(coin.SIZE*.37)),(255,255,255,255))
        outside = coin.CIRCLE.point(lambda v: 255 if v == 0 else 0)
        for i in range(24*6):
            t = i/24
            c = Image.frombytes('RGB',(coin.SIZE,coin.SIZE),coin.frame_with_coin(base,t)).convert('L')
            p = Image.frombytes('RGB',(coin.SIZE,coin.SIZE),pai.frame_with_cameo(base,t,portrait)).convert('L')
            self.assertIsNone(ImageChops.multiply(c, outside).getbbox())
            self.assertIsNone(ImageChops.multiply(c, p).getbbox())

    def test_uses_approved_mouth_trial(self):
        with patch.object(coin,'mouth_render') as render:
            coin.render('a','b','c','d','e','f')
        self.assertIs(render.call_args.kwargs['frame_effect'], coin.frame_with_coin)

if __name__ == '__main__':
    unittest.main()
