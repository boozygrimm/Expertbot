import json
from io import BytesIO
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from news_prototype import bulletin_contract, bulletin_pipeline, bulletin_screen, bulletin_timing, bulletin_wide_screen, news_image_codec
from tests.test_podcast_video_trial import make_segment


def thumbnail(color):
    buffer = BytesIO(); Image.new('RGB', (400, 200), color).save(buffer, format='PNG')
    return news_image_codec.make_thumbnail(buffer.getvalue())


def payload():
    return {'sections': [
        {'id': 10, 'ticker': 'ПЕРВАЯ НОВОСТЬ', 'turns': [{'speaker': 'Бендер', 'text': 'Первая новость.'}], 'image_b64': thumbnail('red')},
        {'id': 11, 'ticker': 'ВТОРАЯ НОВОСТЬ', 'turns': [{'speaker': 'Киберсенсей', 'text': 'Вторая новость.'}], 'image_b64': thumbnail('blue')} ]}


class BulletinContractTests(unittest.TestCase):
    def test_contract_rejects_malformed_payloads(self):
        data = payload()
        self.assertEqual(len(bulletin_contract.validate_bulletin(data)['sections']), 2)
        bad = payload(); bad['sections'][1]['id'] = 10
        with self.assertRaises(ValueError): bulletin_contract.validate_bulletin(bad)
        bad = payload(); bad['sections'][0]['image_b64'] = 'not-an-image'
        with self.assertRaises(ValueError): bulletin_contract.validate_bulletin(bad)
        bad = payload(); bad['sections'][1]['turns'][0]['speaker'] = 'Бендер'
        with self.assertRaises(ValueError): bulletin_contract.validate_bulletin(bad)
        bad = payload(); bad['sections'][0]['turns'][0]['extra'] = 'bad'
        with self.assertRaises(ValueError): bulletin_contract.validate_bulletin(bad)

    def test_static_screen_preserves_presenter_faces_and_switches_picture(self):
        base = Image.open('news_prototype/studio_mockup_soft.png').convert('RGB').resize((960, 540))
        red = bulletin_screen.apply(base, bulletin_screen.prepare(thumbnail('red')))
        blue = bulletin_screen.apply(base, bulletin_screen.prepare(thumbnail('blue')))
        for point in ((320, 200), (620, 200), (0, 0)):
            self.assertEqual(red.getpixel(point), base.getpixel(point))
            self.assertEqual(blue.getpixel(point), base.getpixel(point))
        self.assertNotEqual(red.getpixel((450, 140)), blue.getpixel((450, 140)))


class BulletinPipelineTests(unittest.TestCase):
    def test_complete_local_render_synthetic_audio_and_images_only(self):
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        def synthesize(text, target, keys, start_index): make_segment(target, seconds=1)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'bulletin'
            with patch('news_prototype.story_audio.gemini_tts.synthesize', side_effect=synthesize) as fake:
                result = bulletin_pipeline.run(payload(), folder, ffmpeg, ['unused'])
            self.assertEqual(fake.call_count, 2)
            self.assertTrue(result.is_file())
            self.assertEqual(json.loads((folder / 'status.json').read_text())['stage'], 'ready_for_review')
            first, second = Path(tmp) / 'first.png', Path(tmp) / 'second.png'
            for at, target in ((2.4, first), (3.4, second)):
                subprocess.run([ffmpeg, '-nostdin', '-loglevel', 'error', '-ss', str(at), '-i', str(result),
                                '-frames:v', '1', str(target)], check=True)
            with Image.open(first) as a, Image.open(second) as b:
                a = a.convert('RGB'); b = b.convert('RGB')
                red, blue = a.getpixel((450, 140)), b.getpixel((450, 140))
                self.assertGreater(red[0], red[2] + 60)
                self.assertGreater(blue[2], blue[0] + 60)

    def test_missing_picture_replaces_previous_with_neutral_screen(self):
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        data = payload()
        del data['sections'][1]['image_b64']
        def synthesize(text, target, keys, start_index): make_segment(target, seconds=1)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'bulletin'
            with patch('news_prototype.story_audio.gemini_tts.synthesize', side_effect=synthesize):
                result = bulletin_pipeline.run(data, folder, ffmpeg, ['unused'])
            target = Path(tmp) / 'fallback.png'
            subprocess.run([ffmpeg, '-nostdin', '-loglevel', 'error', '-ss', '3.4', '-i', str(result),
                            '-frames:v', '1', str(target)], check=True)
            with Image.open(target) as frame:
                pixel = frame.convert('RGB').getpixel((450, 150))
            expected = bulletin_wide_screen.compose().getpixel((450, 150))
            self.assertLess(sum(abs(a - b) for a, b in zip(pixel, expected)), 15)

    def test_overlength_stops_before_video_and_saves_no_partial_result(self):
        import imageio_ffmpeg
        def fake_prepare(script, folder, ffmpeg, keys): folder.mkdir()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'bulletin'
            with patch.object(bulletin_pipeline.story_audio, 'prepare', side_effect=fake_prepare), \
                 patch.object(bulletin_pipeline.bulletin_timing, 'section_windows', side_effect=bulletin_timing.BulletinTooLong('too long')), \
                 patch.object(bulletin_pipeline.bulletin_layered_video, 'render') as render:
                with self.assertRaises(bulletin_timing.BulletinTooLong):
                    bulletin_pipeline.run(payload(), folder, imageio_ffmpeg.get_ffmpeg_exe(), ['unused'])
            render.assert_not_called()
            self.assertEqual(json.loads((folder / 'status.json').read_text())['stage'], 'audio_too_long')
            self.assertFalse((folder / 'video.mp4').exists())

if __name__ == '__main__': unittest.main()
