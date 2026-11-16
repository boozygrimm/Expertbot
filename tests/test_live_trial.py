import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

try:
    from mongomock_motor import AsyncMongoMockClient
except ImportError:
    AsyncMongoMockClient = None

from news_prototype import live_trial


@unittest.skipIf(AsyncMongoMockClient is None, 'mongomock-motor optional')
class LiveTrialTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.folder=self.root/'trial'; self.db=AsyncMongoMockClient().beleyakov_db
        self.text='Публичный источник сообщает подробности о новой технологии. ' * 3
        self.draft={'decision':'draft', 'verification':'needs_review', 'evidence':[],
                    'script':{'turns':[{'speaker':'Бендер','text':'Первая реплика'},
                                       {'speaker':'Киберсенсей','text':'Вторая реплика'}]}}
        self.fetch=AsyncMock(return_value={'title':'Новость', 'summary':self.text, 'resolved_url':'https://example.com/a'})
        self.editor=AsyncMock(return_value=self.draft)

    async def asyncTearDown(self): self.temp.cleanup()

    async def prepared(self):
        result=await live_trial.prepare(self.folder,'https://example.com/a','важно','secret','model',
                                        fetch=self.fetch,draft=self.editor)
        self.assertEqual(result,'prepared')
        return live_trial.load_prepared(self.folder)

    async def test_prepare_creates_private_folder_and_preserves_source_and_accent(self):
        data=await self.prepared()
        self.assertEqual(self.folder.stat().st_mode & 0o777,0o700)
        self.assertEqual((self.folder/'prepared.json').stat().st_mode & 0o777,0o600)
        self.assertEqual(data['candidate']['summary'],self.text)
        self.assertEqual(self.editor.await_args.args[0]['accent'],'важно')
        self.assertEqual(data['draft']['verification'],'needs_review')
        with self.assertRaises(FileExistsError): await self.prepared()
        self.editor.assert_awaited_once()

    async def test_unavailable_or_insufficient_source_never_calls_editor(self):
        self.fetch.side_effect=RuntimeError('sensitive')
        result=await live_trial.prepare(self.folder,'https://example.com/a','','secret','model',fetch=self.fetch,draft=self.editor)
        self.assertEqual(result,'needs_source'); self.editor.assert_not_awaited()
        self.assertEqual(json.loads((self.folder/'state.json').read_text())['stage'],'needs_source')
        with self.assertRaises(ValueError): live_trial.load_prepared(self.folder)

    async def test_editor_failure_does_not_repeat(self):
        self.editor.side_effect=RuntimeError('sensitive')
        result=await live_trial.prepare(self.folder,'https://example.com/a','','secret','model',fetch=self.fetch,draft=self.editor)
        self.assertEqual(result,'editor_failed')
        with self.assertRaises(FileExistsError):
            await live_trial.prepare(self.folder,'https://example.com/a','','secret','model',fetch=self.fetch,draft=self.editor)
        self.editor.assert_awaited_once()

    async def test_render_uses_real_queue_lease_and_user_priority(self):
        data=await self.prepared()
        self.assertEqual(await live_trial.enqueue(self.db,data),'queued')
        self.assertEqual(await live_trial.enqueue(self.db,data),'already_queued')
        _,jobs=live_trial._collections(self.db,data['trial_id'])
        client=SimpleNamespace(supports_news=AsyncMock(return_value=True),
                               submit=AsyncMock(return_value={'status':'running'}),
                               status=AsyncMock(return_value={'status':'ready'}))
        # Refuse to create a new queue silently.
        with self.assertRaises(ValueError): await live_trial.drive_once(self.db,data,client)
        from bender_queue import VideoQueue
        queue=VideoQueue(self.db.bender_video_queue,owner='ordinary')
        await queue.ensure(); await queue.enqueue({'id':'ordinary'})
        self.assertEqual(await live_trial.drive_once(self.db,data,client),'queued')
        client.submit.assert_not_awaited()
        self.assertTrue(await queue.acquire()); await queue.remove('ordinary'); await queue.release()
        self.assertEqual(await live_trial.drive_once(self.db,data,client),'running')
        client.submit.assert_awaited_once()
        self.assertEqual(await live_trial.drive_once(self.db,data,client),'ready_for_review')
        self.assertEqual((await jobs.find_one({}))['stage'],'ready_for_review')
        self.assertEqual((await self.db.bender_video_queue.find_one({}))['items'],[])

    async def test_busy_and_ambiguous_submission_are_safe(self):
        from bender_queue import VideoQueue
        from news_prototype.news_worker_client import NewsBusy, NewsUncertain
        data=await self.prepared(); await live_trial.enqueue(self.db,data)
        queue=VideoQueue(self.db.bender_video_queue); await queue.ensure()
        client=SimpleNamespace(supports_news=AsyncMock(return_value=False),
                               submit=AsyncMock(side_effect=NewsBusy()), status=AsyncMock())
        self.assertEqual(await live_trial.drive_once(self.db,data,client),'worker_not_ready')
        client.submit.assert_not_awaited()
        client.supports_news.return_value=True
        self.assertEqual(await live_trial.drive_once(self.db,data,client),'queued')
        client.submit.side_effect=NewsUncertain()
        self.assertEqual(await live_trial.drive_once(self.db,data,client),'uncertain')
        self.assertEqual(await live_trial.drive_once(self.db,data,client),'uncertain')
        self.assertEqual(client.submit.await_count,2)

    async def test_delivery_only_once_to_owner(self):
        data=await self.prepared(); await live_trial.enqueue(self.db,data)
        _,jobs=live_trial._collections(self.db,data['trial_id'])
        await jobs.update_one({}, {'$set': {'stage':'ready_for_review'}})
        async def download(job,path):
            path.write_bytes(b'video'); return 5,'checksum'
        client=SimpleNamespace(download=AsyncMock(side_effect=download))
        bot=SimpleNamespace(send_video=AsyncMock(side_effect=TimeoutError('secret')))
        self.assertEqual(await live_trial.deliver_once(self.db,data,client,bot,987),'review_uncertain')
        self.assertEqual(await live_trial.deliver_once(self.db,data,client,bot,987),'idle')
        self.assertEqual(bot.send_video.await_args.args[0],987)
        bot.send_video.assert_awaited_once()

    async def test_end_to_end_with_http_worker_and_no_external_services(self):
        from bender_queue import VideoQueue
        from video_prototype import worker, news_jobs
        from news_prototype.news_worker_client import NewsWorkerClient
        from http.server import ThreadingHTTPServer
        import threading
        def render(job,folder):
            work=folder/'work'; work.mkdir()
            (work/'video.mp4').write_bytes(b'test video')
            (work/'status.json').write_text('{"stage":"ready_for_review"}')
        shared=worker.JobStore(lambda *args: 'normal')
        news=news_jobs.NewsJobs(shared,self.root/'worker',render)
        server=ThreadingHTTPServer(('127.0.0.1',0),worker.handler_for(shared,'secret',news))
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            data=await self.prepared(); await live_trial.enqueue(self.db,data)
            await VideoQueue(self.db.bender_video_queue).ensure()
            client=NewsWorkerClient(f'http://127.0.0.1:{server.server_port}','secret')
            self.assertIn(await live_trial.drive_once(self.db,data,client),('running','ready_for_review'))
            for _ in range(100):
                stage=await live_trial.drive_once(self.db,data,client)
                if stage=='ready_for_review': break
                await asyncio.sleep(.01)
            self.assertEqual(stage,'ready_for_review')
            bot=SimpleNamespace(send_video=AsyncMock(return_value=SimpleNamespace(message_id=9)))
            self.assertEqual(await live_trial.deliver_once(self.db,data,client,bot,987),'review_sent')
            self.assertEqual(bot.send_video.await_args.args[0],987)
            self.assertEqual(await live_trial.deliver_once(self.db,data,client,bot,987),'idle')
        finally:
            server.shutdown(); server.server_close(); thread.join()

    async def test_incomplete_state_cannot_be_submitted(self):
        await self.prepared()
        path=self.folder/'state.json'; state=json.loads(path.read_text()); state['stage']='editing'; path.write_text(json.dumps(state))
        with self.assertRaises(ValueError): live_trial.load_prepared(self.folder)


if __name__ == '__main__': unittest.main()
