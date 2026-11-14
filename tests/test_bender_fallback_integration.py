"""Offline end-to-end check of the uncertain storage delivery path."""
import os
from pathlib import Path
import tempfile
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import uuid

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from bender_worker_client import BenderWorkerClient
from handlers import bender_clip
from tests.test_bender_clip import FakeQueue
from video_prototype import worker, fallback_trial


class FallbackIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_lost_storage_response_delivers_existing_video_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            # Fake only the rendering subprocess and Telegram. The runner,
            # job store, HTTP API, client, and queue handler are real.
            class Process:
                def __init__(self, command, **kwargs):
                    self.output = Path(command[command.index('--output-dir') + 1]) / 'clip-new'
                def wait(self, timeout=None):
                    self.output.mkdir(parents=True)
                    (self.output / 'video.mp4').write_bytes(b'already-rendered-video')
                    (self.output / 'storage-upload.pending').write_text('pending')
                    return 1  # Simulated lost response after storage accepted the video.
            with patch('video_prototype.worker.subprocess.Popen', Process):
                store = worker.JobStore(worker.runner(root, -1001, 'bot-secret', 600))
                server = ThreadingHTTPServer(('127.0.0.1', 0), worker.handler_for(store, 'test-secret'))
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    url = f'http://127.0.0.1:{server.server_port}'
                    client = BenderWorkerClient(url, 'test-secret', total_timeout=5, request_timeout=2, poll_interval=.01)
                    queue = FakeQueue()
                    item = {'id': 'queue-id', 'stage': 'ready', 'answer': 'Тест',
                            'request_id': str(uuid.uuid4()), 'chat_id': -1002,
                            'thread_id': 7, 'message_id': 42, 'status_message_id': None}
                    queue.items.append(item.copy())
                    bot = SimpleNamespace(send_video_note=AsyncMock(), send_message=AsyncMock(),
                                          delete_message=AsyncMock(), edit_message_text=AsyncMock())
                    with patch.dict(os.environ, {'BENDER_WORKER_URL': url, 'BENDER_WORKER_KEY': 'test-secret'}), \
                         patch.object(bender_clip, 'BenderWorkerClient', return_value=client):
                        await bender_clip.process_queued_video(queue, bot, item.copy())
                    bot.send_video_note.assert_awaited_once()
                    sent = bot.send_video_note.await_args.kwargs
                    self.assertEqual(sent['video_note'].data, b'already-rendered-video')
                    self.assertEqual((sent['chat_id'], sent['message_thread_id']), (-1002, 7))
                    self.assertEqual(sent['reply_parameters'].message_id, 42)
                    self.assertEqual(queue.items, [])
                    self.assertEqual(store.get(item['request_id'])['status'], 'upload_uncertain')
                    self.assertEqual(len(list((root / item['request_id']).glob('clip-*'))), 1)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()


class TrialTests(unittest.TestCase):
    def test_trial_sends_existing_video_once_without_storage_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / 'existing.mp4'
            video.write_bytes(b'existing-video')
            sent = []
            def telegram(token, method, fields, file_path):
                sent.append((method, fields, file_path.read_bytes()))
                return {'video_note': {'file_id': 'test'}}
            with patch.dict(os.environ, {'BOT_TOKEN': 'test-token'}), \
                 patch('sys.argv', ['fallback_trial', '--video', str(video), '--chat-id', '-1002', '--thread-id', '7']), \
                 patch.object(fallback_trial.pipeline, 'telegram_call', side_effect=telegram):
                fallback_trial.main()
            self.assertEqual(sent, [('sendVideoNote', {'chat_id': -1002, 'message_thread_id': 7}, b'existing-video')])
