from io import BytesIO
from unittest.mock import patch
import unittest
from PIL import Image, ImageChops
from news_prototype import bulletin_concept_screen, bulletin_wide_screen, news_image_codec


def thumbnail(color):
    output = BytesIO()
    Image.new('RGB', (400, 250), color).save(output, format='PNG')
    return news_image_codec.make_thumbnail(output.getvalue())


class WideScreenTests(unittest.TestCase):
    def test_wide_screen_and_foreground_are_separate_layers(self):
        self.assertGreater(bulletin_wide_screen.SCREEN[2] - bulletin_wide_screen.SCREEN[0],
                           bulletin_concept_screen.SCREEN[2] - bulletin_concept_screen.SCREEN[0])
        red = bulletin_wide_screen.compose(thumbnail('red'))
        blue = bulletin_wide_screen.compose(thumbnail('blue'))
        self.assertGreater(red.getpixel((450, 150))[0], blue.getpixel((450, 150))[0] + 50)
        self.assertGreater(blue.getpixel((450, 150))[2], red.getpixel((450, 150))[2] + 50)
        for point in ((280, 227), (620, 210), (270, 158), (0, 0), (700, 480)):
            self.assertEqual(red.getpixel(point), blue.getpixel(point))
        for layer in bulletin_wide_screen.LAYERS:
            with Image.open(layer) as image:
                self.assertEqual(image.mode, 'RGBA')
                self.assertEqual(image.size, bulletin_wide_screen.SIZE)
                self.assertEqual(image.getpixel((450, 150))[3], 0)
                opaque = image.getchannel('A').point(lambda x: 255 if x == 255 else 0)
                changed = ImageChops.difference(red, blue).convert('L')
                self.assertIsNone(ImageChops.multiply(changed, opaque).getbbox())

    def test_invalid_image_and_wrong_layer_are_rejected(self):
        with self.assertRaises(ValueError): bulletin_wide_screen.compose('not-a-jpeg')
        with patch.object(bulletin_wide_screen, 'LAYERS', (bulletin_concept_screen.CONCEPT,)):
            with self.assertRaises(ValueError): bulletin_wide_screen.compose()


if __name__ == '__main__': unittest.main()
