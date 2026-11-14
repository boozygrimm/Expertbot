"""Exercise the real scene renderer through the pipeline without Telegram."""
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

from PIL import Image
from video_prototype import pipeline


@unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg is required for this integration test')
class ScenePipelineIntegrationTests(unittest.TestCase):
    def test_gemini_wav_flows_through_real_scene_renderer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            frames = root / 'frames'; frames.mkdir()
            for name in ('closed.png', 'open.png', 'blink.png'):
                Image.new('RGB', (512, 512), (100, 100, 100)).save(frames / name)
            plate = root / 'plate.png'; Image.new('RGB', (512, 512), (60, 60, 60)).save(plate)
            portrait = root / 'portrait.png'; Image.new('RGB', (512, 512), (170, 130, 100)).save(portrait)
            output = io.BytesIO()
            with wave.open(output, 'wb') as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
                wav.writeframes(b'\1\0' * 24000)
            args = pipeline.parse_args(['--text', 'Тест', '--frames-dir', str(frames),
                '--smoke-plate', str(plate), '--portrait', str(portrait), '--scene-seed', '319',
                '--output-dir', str(root / 'jobs'), '--render-python', sys.executable, '--channel-id', '-1001'])
            with patch.dict(os.environ, {'BENDER_TTS_PROVIDER': 'gemini', 'GEMINI_TTS_KEYS': 'secret',
                                         'BOT_TOKEN': 'bot'}), \
                 patch.object(pipeline.gemini_tts, 'request_one', return_value=(output.getvalue(), 1.0)) as api, \
                 patch.object(pipeline, 'telegram_call', return_value={'video_note': {'file_id': 'cached'}}):
                pipeline.run(args)
            work = next((root / 'jobs').glob('clip-*'))
            self.assertEqual(api.call_count, 1)
            self.assertGreater((work / 'video.mp4').stat().st_size, 1000)
            self.assertEqual(json.loads((work / 'result.json').read_text())['file_id'], 'cached')
            self.assertTrue((work / 'scene.json').exists())

    def test_scene_render_manifest_and_storage_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            frames = root / 'frames'; frames.mkdir()
            for name, color in [('closed.png', (100, 100, 100)),
                                ('open.png', (110, 110, 110)),
                                ('blink.png', (90, 90, 90))]:
                Image.new('RGB', (512, 512), color).save(frames / name)
            plate = root / 'plate.png'; Image.new('RGB', (512, 512), (60, 60, 60)).save(plate)
            portrait = root / 'portrait.png'; Image.new('RGB', (512, 512), (170, 130, 100)).save(portrait)
            audio = root / 'speech.wav'
            with wave.open(str(audio), 'wb') as output:
                output.setnchannels(1); output.setsampwidth(2); output.setframerate(24000)
                output.writeframes(b'\0\0' * 24000)
            args = pipeline.parse_args(['--audio', str(audio), '--frames-dir', str(frames),
                '--smoke-plate', str(plate), '--portrait', str(portrait),
                '--scene-seed', '319', '--output-dir', str(root / 'jobs'),
                '--render-python', sys.executable, '--channel-id', '-1001', '--chat-id', '-1002'])
            with patch.dict(os.environ, {'BOT_TOKEN': 'test-secret'}), \
                 patch.object(pipeline, 'telegram_call', side_effect=[{'video_note': {'file_id': 'cached'}}, {}]) as telegram:
                pipeline.run(args)
            work = next((root / 'jobs').glob('clip-*'))
            manifest = json.loads((work / 'scene.json').read_text())
            self.assertEqual(manifest['seed'], '319')
            self.assertEqual({x['name'] for x in manifest['guests']}, {'hedgehog', 'beholder', 'pai_mei'})
            self.assertEqual({x['name'] for x in manifest['decorations']}, {'bubbles', 'pyramid', 'coin'})
            self.assertGreater((work / 'video.mp4').stat().st_size, 1000)
            self.assertEqual(json.loads((work / 'result.json').read_text())['file_id'], 'cached')
            self.assertEqual(telegram.call_count, 2)
            self.assertEqual(telegram.call_args_list[0].args[2], {'chat_id': -1001})
            self.assertEqual(telegram.call_args_list[1].args[2], {'chat_id': -1002, 'video_note': 'cached'})
            self.assertFalse((work / 'storage-upload.pending').exists())
