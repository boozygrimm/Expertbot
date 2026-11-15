"""Local HTTP/queue/codec integration without real TTS or Telegram calls."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import wave
from http.server import ThreadingHTTPServer

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from mongomock_motor import AsyncMongoMockClient
from bender_worker_client import BenderWorkerClient
from handlers import bender_clip
from tests.test_bender_clip import FakeQueue
from video_prototype import pipeline, worker


class StudioIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_queue_to_worker_real_codec_and_confirmed_history(self):
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            try:
                import imageio_ffmpeg
                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            except ImportError:
                self.skipTest('ffmpeg not available')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            binary = root / 'bin'; binary.mkdir(); (binary / 'ffmpeg').symlink_to(ffmpeg)
            wav = root / 'test.wav'
            with wave.open(str(wav), 'wb') as audio:
                audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
                audio.writeframes(b'\x00\x00' * 24000)
            uploads = []
            def telegram(token, method, fields, media):
                self.assertEqual(method, 'sendVoice')
                self.assertTrue(media.read_bytes().startswith(b'OggS'))
                uploads.append(media.read_bytes())
                return {'voice': {'file_id': 'storage-voice-id'}}
            def run(request_id, text, kind='video'):
                self.assertEqual(kind, 'voice')
                self.assertEqual(text, 'Привет, земляне!')
                args = pipeline.parse_args(['--audio', str(wav), '--voice-only', '--channel-id', '-5',
                                             '--output-dir', str(root / request_id)])
                pipeline.run(args)
                record = next((root / request_id).glob('clip-*/result.json'))
                return json.loads(record.read_text())['file_id']
            secret = 'local-test-secret-1234567890'
            store = worker.JobStore(run)
            server = ThreadingHTTPServer(('127.0.0.1', 0), worker.handler_for(store, secret))
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            self.addCleanup(thread.join)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            port = server.server_port
            client = BenderWorkerClient(f'http://127.0.0.1:{port}', secret, poll_interval=.02, total_timeout=10)
            database = AsyncMongoMockClient().test
            db = SimpleNamespace(messages=database.messages, personas=SimpleNamespace(find_one=AsyncMock(return_value={'prompt': 'Бендер'})))
            bot = SimpleNamespace(send_message=AsyncMock(), edit_message_text=AsyncMock(), delete_message=AsyncMock(),
                                  send_voice=AsyncMock(return_value=SimpleNamespace(message_id=123)), send_video_note=AsyncMock())
            queue = FakeQueue()
            item = {'id': '-101:10', 'request_id': str(uuid.uuid4()), 'kind': 'voice', 'stage': 'queued',
                    'chat_id': -101, 'message_id': 10, 'thread_id': 3, 'status_message_id': None,
                    'query': 'привет', 'context': ''}
            queue.items.append(item.copy())
            with patch.dict(os.environ, {'BOT_TOKEN': 'local-test', 'PATH': str(binary) + os.pathsep + os.environ.get('PATH', '')}), \
                 patch.object(pipeline, 'telegram_call', side_effect=telegram), \
                 patch.object(bender_clip, 'db', db), \
                 patch.object(bender_clip, 'BenderWorkerClient', return_value=client), \
                 patch.object(bender_clip, 'generate_with_retry', new=AsyncMock(return_value='Привет, земляне!')):
                await bender_clip.process_queued_video(queue, bot, item)
            self.assertEqual(len(uploads), 1)
            self.assertEqual(queue.items, [])
            bot.send_voice.assert_awaited_once()
            self.assertEqual(bot.send_voice.await_args.kwargs['voice'], 'storage-voice-id')
            self.assertEqual(bot.send_voice.await_args.kwargs['message_thread_id'], 3)
            bot.send_video_note.assert_not_awaited()
            history = await database.messages.find_one({'chat_id': -101, 'message_id': 123})
            self.assertEqual(history['audio_transcript'], 'Привет, земляне!')
            self.assertEqual(history['audio_source'], 'tts_input')
