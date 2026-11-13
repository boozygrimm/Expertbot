import unittest
from unittest.mock import patch
from PIL import Image
from video_prototype import coin_render as coin


class CoinTests(unittest.TestCase):
    def test_appears_drops_and_disappears(self):
        self.assertIsNone(coin.coin_pose(1))
        self.assertIsNone(coin.coin_pose(6))
        self.assertLess(coin.coin_pose(3.5)[0], coin.coin_pose(4.5)[0])
        frame = Image.new('RGB', (coin.SIZE, coin.SIZE), (25,25,25)).tobytes()
        self.assertEqual(coin.frame_with_coin(frame, 1), frame)
        self.assertNotEqual(coin.frame_with_coin(frame, 3.5), frame)
        self.assertEqual(coin.frame_with_coin(frame, 6), frame)

    def test_uses_shared_mouth_and_smoke_renderer(self):
        with patch.object(coin, 'base_render') as render:
            coin.render('a','b','c','d','e','f')
        self.assertIs(render.call_args.kwargs['frame_effect'], coin.frame_with_coin)


if __name__ == '__main__':
    unittest.main()
