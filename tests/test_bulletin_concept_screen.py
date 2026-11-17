"""The selected concept remains isolated from the production animation."""
from io import BytesIO
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from news_prototype import bulletin_concept_screen as concept, news_image_codec


def thumbnail(color):
    output = BytesIO()
    Image.new('RGB', (400, 250), color).save(output, format='PNG')
    return news_image_codec.make_thumbnail(output.getvalue())


class BulletinConceptScreenTests(unittest.TestCase):
    def test_frame_is_normalized_and_hosts_and_outer_frame_are_preserved(self):
        frame = concept.base_frame()
        self.assertEqual(frame.size, (960, 540))
        red = concept.apply(frame, thumbnail('red'))
        blue = concept.apply(frame, thumbnail('blue'))
        for point in ((0, 0), (280, 227), (620, 210), (700, 480)):
            self.assertEqual(red.getpixel(point), frame.getpixel(point))
            self.assertEqual(blue.getpixel(point), frame.getpixel(point))
        self.assertGreater(red.getpixel((450, 160))[0], blue.getpixel((450, 160))[0] + 50)
        self.assertGreater(blue.getpixel((450, 160))[2], red.getpixel((450, 160))[2] + 50)

    def test_unknown_or_changed_artwork_fails_closed(self):
        with TemporaryDirectory() as temp:
            fake = Path(temp) / 'other.png'
            Image.new('RGB', concept.SIZE, 'white').save(fake)
            concept.screen_mask.cache_clear()
            try:
                with patch.object(concept, 'CONCEPT', fake):
                    with self.assertRaises(ValueError): concept.screen_mask()
            finally:
                concept.screen_mask.cache_clear()

    def test_rejects_wrong_dimensions_and_invalid_media(self):
        with self.assertRaises(ValueError): concept.apply(Image.new('RGB', (100, 100)), thumbnail('red'))
        with self.assertRaises(ValueError): concept.apply(concept.base_frame(), 'not-a-jpeg')


if __name__ == '__main__': unittest.main()
