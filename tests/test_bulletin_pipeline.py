import json
from io import BytesIO
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from news_prototype import bulletin_contract, bulletin_pipeline, bulletin_screen, bulletin_timing, bulletin_wide_screen, news_image_codec
from tests.test_bulletin_dialogue_audio import wav


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
        data = payload()
        data['sections'][0]['turns'].append({'speaker': 'Киберсенсей', 'text': 'Короткий ответ.'})
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'bulletin'
            with patch('news_prototype.bulletin_dialogue_audio.request_one', return_value=(wav(), 1.0)) as fake:
                result = bulletin_pipeline.run(data, folder, ffmpeg, ['unused'])
            self.assertEqual(fake.call_count, 2)
            first_body = fake.call_args_list[0].args[1]
            self.assertEqual([p['speechMetadata']['speaker'] for p in first_body['contents'][0]['parts']], ['Bender', 'Sensei'])
            voices = first_body['generationConfig']['speechConfig']['multiSpeakerVoiceConfig']['speakerVoiceConfigs']
            self.assertEqual([v['voiceConfig']['prebuiltVoiceConfig']['voiceName'] for v in voices], ['Algenib', 'Puck'])
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
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'bulletin'
            with patch('news_prototype.bulletin_dialogue_audio.request_one', return_value=(wav(), 1.0)):
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
        def fake_prepare(script, folder, ffmpeg, keys):
            folder.mkdir()
            return folder / 'dialogue.wav', [(s['id'], folder / f'{i}.wav') for i, s in enumerate(script['sections'])]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'bulletin'
            with patch.object(bulletin_pipeline.bulletin_dialogue_audio, 'prepare', side_effect=fake_prepare), \
                 patch.object(bulletin_pipeline.bulletin_timing, 'section_windows', side_effect=bulletin_timing.BulletinTooLong('too long')), \
                 patch.object(bulletin_pipeline.bulletin_layered_video, 'render') as render:
                with self.assertRaises(bulletin_timing.BulletinTooLong):
                    bulletin_pipeline.run(payload(), folder, imageio_ffmpeg.get_ffmpeg_exe(), ['unused'])
            render.assert_not_called()
            self.assertEqual(json.loads((folder / 'status.json').read_text())['stage'], 'audio_too_long')
            self.assertFalse((folder / 'video.mp4').exists())

if __name__ == '__main__': unittest.main()


class MainBulletinDuetTests(unittest.TestCase):
    def test_5_10_20_news_one_joint_request_each_and_original_order(self):
        import imageio_ffmpeg
        from tests.test_bulletin_dialogue_audio import payload as duet_payload
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        run_real = subprocess.run
        for count in (5, 10, 20):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as tmp:
                data = duet_payload(count)
                folder = Path(tmp) / 'work'
                captured = []
                def render(output, ff, *, sections, windows, timeline):
                    captured.append((sections, windows, timeline))
                    Path(output).write_bytes(b'simulated validated video')
                def run(command, **kwargs):
                    if command[-3:] == ['-f', 'null', '-']:
                        return subprocess.CompletedProcess(command, 0)
                    return run_real(command, **kwargs)
                with patch.object(bulletin_pipeline.bulletin_dialogue_audio, 'request_one', return_value=(wav(), 1.0)) as request, \
                     patch.object(bulletin_pipeline.bulletin_layered_video, 'render', side_effect=render), \
                     patch.object(bulletin_pipeline.subprocess, 'run', side_effect=run):
                    result = bulletin_pipeline.run(data, folder, ffmpeg, ['first', 'second'])
                self.assertEqual(request.call_count, count)
                self.assertEqual([call.args[0] for call in request.call_args_list],
                                 ['first' if i % 2 == 0 else 'second' for i in range(count)])
                self.assertTrue(all(len(call.args[1]['contents'][0]['parts']) == 2 for call in request.call_args_list))
                self.assertEqual([section['id'] for section in captured[0][0]], list(range(1, count + 1)))
                self.assertEqual([window.section_id for window in captured[0][1]], list(range(1, count + 1)))
                self.assertLess(len(captured[0][2][1]) / 24, 300)
                self.assertTrue(result.is_file())
                self.assertEqual(json.loads((folder / 'status.json').read_text())['stage'], 'ready_for_review')

    def test_failed_news_does_not_replay_or_render_existing_audio(self):
        import imageio_ffmpeg
        from tests.test_bulletin_dialogue_audio import payload as duet_payload
        from video_prototype.gemini_tts import TTSFailure
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'work'
            def request(key, body, timeout):
                if '2' in body['contents'][0]['parts'][0]['text']:
                    raise TTSFailure('HTTP 429')
                return wav(), 1.0
            with patch.object(bulletin_pipeline.bulletin_dialogue_audio, 'request_one', side_effect=request) as calls, \
                 patch.object(bulletin_pipeline.bulletin_layered_video, 'render') as render:
                with self.assertRaises(TTSFailure):
                    bulletin_pipeline.run(duet_payload(2), folder, imageio_ffmpeg.get_ffmpeg_exe(), ['single'])
                self.assertTrue((folder / '1-normalized.wav').is_file())
                before = calls.call_count
                with self.assertRaises(FileExistsError):
                    bulletin_pipeline.run(duet_payload(2), folder, imageio_ffmpeg.get_ffmpeg_exe(), ['single'])
                self.assertEqual(calls.call_count, before)
                render.assert_not_called()
                self.assertFalse((folder / 'video.mp4').exists())

    def test_early_duration_guard_preserves_recordings_and_stops_further_requests(self):
        import imageio_ffmpeg
        from tests.test_bulletin_dialogue_audio import payload as duet_payload
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'work'
            long_audio = wav(100)
            with patch.object(bulletin_pipeline.bulletin_dialogue_audio, 'request_one', return_value=(long_audio, 100.0)) as requests, \
                 patch.object(bulletin_pipeline.bulletin_layered_video, 'render') as render:
                with self.assertRaises(bulletin_timing.BulletinTooLong):
                    bulletin_pipeline.run(duet_payload(5), folder, imageio_ffmpeg.get_ffmpeg_exe(), ['unused'])
            self.assertEqual(requests.call_count, 3)
            render.assert_not_called()
            self.assertEqual(json.loads((folder / 'status.json').read_text())['stage'], 'audio_too_long')
            self.assertTrue((folder / 'dialogue.wav').is_file())
            self.assertTrue((folder / '3-normalized.wav').is_file())
            self.assertFalse((folder / '4-normalized.wav').exists())
            self.assertFalse((folder / 'video.mp4').exists())
