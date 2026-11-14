import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from PIL import Image
from video_prototype import pai_mei_demo, pai_mei_render as cameo


class CameoTests(unittest.TestCase):
    def test_portrait_mask_and_cameo_visibility(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'portrait.png'
            Image.new('RGB', (640, 640), (255, 0, 0)).save(path)
            sprite = cameo.prepare_portrait(path)
            self.assertEqual(sprite.getchannel('A').getextrema(), (0, 255))
            frame = Image.new('RGB', (cameo.SIZE, cameo.SIZE), (0, 0, 0)).tobytes()
            self.assertEqual(cameo.frame_with_cameo(frame, 1, sprite), frame)
            self.assertNotEqual(cameo.frame_with_cameo(frame, 4, sprite), frame)
            self.assertEqual(cameo.frame_with_cameo(frame, 7, sprite), frame)
            image = Image.frombytes('RGB', (cameo.SIZE, cameo.SIZE), cameo.frame_with_cameo(frame, 4, sprite))
            self.assertEqual(image.getpixel((479, 479)), (0, 0, 0))

    def test_smooth_entry_and_exit(self):
        self.assertIsNone(cameo.cameo_offset(1.9))
        self.assertLess(cameo.cameo_offset(2.1), cameo.cameo_offset(2.3))
        self.assertEqual(cameo.cameo_offset(4), 1)
        self.assertLess(cameo.cameo_offset(6.4), cameo.cameo_offset(6.2))
        self.assertIsNone(cameo.cameo_offset(6.5))

    def test_get_photo_via_forward_without_exposing_token(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'picture'
            stream = MagicMock()
            stream.__enter__.return_value.read.return_value = b'image'
            with patch.object(pai_mei_demo, 'telegram_call', side_effect=[
                {'photo': [{'file_id': 'small'}, {'file_id': 'large'}]},
                {'file_path': 'photos/test.jpg'}]) as call, \
                 patch.object(pai_mei_demo.urllib.request, 'urlopen', return_value=stream):
                pai_mei_demo.fetch_photo('secret', -1, 117, -2, path)
            self.assertEqual(path.read_bytes(), b'image')
            self.assertEqual(call.call_args_list[0].args[1:], ('forwardMessage',
                {'chat_id': -2, 'from_chat_id': -1, 'message_id': 117}))
            self.assertEqual(call.call_args_list[1].args[2], {'file_id': 'large'})

    def test_reject_non_square_photo(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'portrait.png'
            Image.new('RGB', (600, 400)).save(path)
            with self.assertRaises(ValueError):
                cameo.prepare_portrait(path)


if __name__ == '__main__':
    unittest.main()
