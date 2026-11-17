import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock
from types import SimpleNamespace
from http.server import ThreadingHTTPServer
from mongomock_motor import AsyncMongoMockClient
from bender_queue import VideoQueue
from news_prototype import news_queue, review_delivery
from news_prototype.news_worker_client import NewsWorkerClient, NewsBusy, NewsUncertain
from video_prototype import worker, news_jobs
from tests.test_bulletin_pipeline import payload


class BulletinQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        self.queue = VideoQueue(self.db.media, owner='test'); await self.queue.ensure(); await self.queue.acquire()
        self.jobs = self.db.jobs
        self.client = SimpleNamespace(supports_bulletins=AsyncMock(return_value=True),
            submit_bulletin=AsyncMock(return_value={'status':'running'}), status=AsyncMock(return_value={'status':'ready'}))

    async def test_queue_idempotency_and_capability_fail_closed(self):
        self.assertTrue(await news_queue.enqueue_bulletin(self.jobs, 'bulletin-1', payload()))
        self.assertFalse(await news_queue.enqueue_bulletin(self.jobs, 'bulletin-1', payload()))
        changed = payload(); changed['sections'][0]['ticker'] = 'Другой заголовок'
        with self.assertRaises(ValueError): await news_queue.enqueue_bulletin(self.jobs, 'bulletin-1', changed)
        self.client.supports_bulletins.return_value = False
        await news_queue.run_idle_once(self.queue, self.jobs, self.client)
        self.client.submit_bulletin.assert_not_awaited()
        self.assertEqual((await self.jobs.find_one({}))['stage'], 'queued')
        self.client.supports_bulletins.return_value = True
        await self.queue.enqueue({'id':'ordinary'})
        await news_queue.run_idle_once(self.queue, self.jobs, self.client)
        self.client.submit_bulletin.assert_not_awaited()

    async def test_ambiguous_submission_never_automatically_repeats(self):
        await news_queue.enqueue_bulletin(self.jobs, 'bulletin-1', payload())
        self.client.submit_bulletin.side_effect = NewsBusy()
        await news_queue.run_idle_once(self.queue, self.jobs, self.client)
        self.assertEqual((await self.jobs.find_one({}))['stage'], 'queued')
        self.client.submit_bulletin.side_effect = NewsUncertain()
        await news_queue.run_idle_once(self.queue, self.jobs, self.client)
        self.assertEqual((await self.jobs.find_one({}))['stage'], 'uncertain')
        await news_queue.run_idle_once(self.queue, self.jobs, self.client)
        self.assertEqual(self.client.submit_bulletin.await_count, 2)

    async def test_duration_refusal_is_terminal_and_never_retries(self):
        await news_queue.enqueue_bulletin(self.jobs, 'bulletin-1', payload())
        await news_queue.run_idle_once(self.queue, self.jobs, self.client)
        self.client.status.return_value = {'status':'audio_too_long'}
        await news_queue.run_idle_once(self.queue, self.jobs, self.client)
        self.assertEqual((await self.jobs.find_one({}))['stage'], 'audio_too_long')
        await news_queue.run_idle_once(self.queue, self.jobs, self.client)
        self.client.submit_bulletin.assert_awaited_once()
        self.assertEqual(news_queue._worker_stage({'kind':'story'}, {'status':'audio_too_long'}), 'uncertain')
        self.assertEqual(news_queue._worker_stage({'kind':'bulletin'}, None), 'uncertain')

    async def test_local_http_worker_and_private_delivery_without_real_tts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def fake_render(job, folder):
                work = folder/'work'; work.mkdir()
                (work/'video.mp4').write_bytes(b'synthetic video')
                (work/'status.json').write_text('{"stage":"ready_for_review"}')
            shared = worker.JobStore(lambda *args:'file-id')
            news = news_jobs.NewsJobs(shared, root/'worker', fake_render, run_bulletin=fake_render)
            server = ThreadingHTTPServer(('127.0.0.1',0), worker.handler_for(shared, 'secret', news))
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                client = NewsWorkerClient(f'http://127.0.0.1:{server.server_port}', 'secret')
                self.assertTrue(await client.supports_bulletins())
                await news_queue.enqueue_bulletin(self.jobs, 'local-bulletin', payload())
                await news_queue.run_idle_once(self.queue, self.jobs, client)
                for _ in range(100):
                    await news_queue.run_idle_once(self.queue, self.jobs, client)
                    job = await self.jobs.find_one({})
                    if job['stage'] == 'ready_for_review': break
                    await asyncio.sleep(.01)
                self.assertEqual(job['stage'], 'ready_for_review')
                target = root/'video.mp4'
                size, _ = await client.download(job['request_id'], target)
                self.assertEqual((size, target.read_bytes()), (15, b'synthetic video'))
                await self.db.candidates.insert_one({'_id':job['candidate_id'], 'title':'test'})
                bot = SimpleNamespace(send_video=AsyncMock(return_value=SimpleNamespace(message_id=100)))
                self.assertEqual(await review_delivery.run_once(self.jobs, self.db.candidates, client, bot, 123), 'review_sent')
                self.assertEqual(bot.send_video.await_args.args[0], 123)
                self.assertIn('вестник', bot.send_video.await_args.kwargs['caption'].lower())
            finally:
                server.shutdown(); server.server_close(); thread.join()

    async def test_old_worker_does_not_receive_bulletin_request(self):
        with tempfile.TemporaryDirectory() as temp:
            shared = worker.JobStore(lambda *args:'file-id')
            news = news_jobs.NewsJobs(shared, Path(temp)/'worker', lambda *args: self.fail('unexpected'))
            server = ThreadingHTTPServer(('127.0.0.1',0), worker.handler_for(shared, 'secret', news))
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                client = NewsWorkerClient(f'http://127.0.0.1:{server.server_port}', 'secret')
                self.assertFalse(await client.supports_bulletins())
                await news_queue.enqueue_bulletin(self.jobs, 'local-bulletin', payload())
                await news_queue.run_idle_once(self.queue, self.jobs, client)
                self.assertEqual((await self.jobs.find_one({}))['stage'], 'queued')
                self.assertEqual(list((Path(temp)/'worker').iterdir()), [])
            finally:
                server.shutdown(); server.server_close(); thread.join()
