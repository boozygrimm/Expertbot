import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import os
import tempfile
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer

os.environ.setdefault('MONGO_URI','mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN','123456:TESTTOKEN')
try:
    from mongomock_motor import AsyncMongoMockClient
except ImportError:
    AsyncMongoMockClient = None
from news_prototype import owner_trial, review_delivery
from bender_queue import VideoQueue

TEXT = '''/news_trial
Источник: https://t.me/durov/552
Строка: Telegram Designers: идеи с работающим кодом и видео
Бендер: Павел Дуров поделился сообщением дизайнеров Telegram о подаче идей.
Киберсенсей: Покажем, как идея работает.
Бендер: Там просят готовый код и демонстрационное видео.
Киберсенсей: Только не выключай студию, Бендер.
'''


@unittest.skipIf(AsyncMongoMockClient is None, 'mongomock-motor optional')
class OwnerTrialTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db=AsyncMongoMockClient().test
        self.flags=patch.dict(os.environ, {'NEWS_OWNER_TRIAL_ENABLED':'1', **{k:'0' for k in owner_trial.OTHER_FLAGS}})
        self.flags.start()
    async def asyncTearDown(self): self.flags.stop()

    def test_only_trial_and_no_general_news_flags(self):
        self.assertTrue(owner_trial.enabled())
        for flag in owner_trial.OTHER_FLAGS:
            with patch.dict(os.environ,{flag:'1'}): self.assertFalse(owner_trial.enabled())
        with patch.dict(os.environ,{'NEWS_OWNER_TRIAL_ENABLED':'0'}): self.assertFalse(owner_trial.enabled())

    def test_parses_and_validates_source_turns_ticker(self):
        url,script=owner_trial.parse_message(TEXT)
        self.assertEqual(url,'https://t.me/durov/552'); self.assertEqual(len(script['turns']),4)
        self.assertEqual(script['turns'][0]['speaker'],'Бендер')
        self.assertIn('работающим кодом',script['ticker'])
        for invalid in (TEXT.replace('https://t.me/durov/552','http://example.com/'),
                        TEXT.replace('Киберсенсей:','Кто-то:'), TEXT+'Строка: вторая\n',
                        TEXT.replace('/news_trial','/other'), TEXT.replace('Бендер:', 'Киберсенсей:')):
            with self.subTest(invalid=invalid[:60]), self.assertRaises(ValueError):
                owner_trial.parse_message(invalid)

    async def test_content_idempotency_and_one_slot(self):
        url,script=owner_trial.parse_message(TEXT)
        self.assertEqual(await owner_trial.admit(self.db,123,url,script),'queued')
        self.assertEqual(await owner_trial.admit(self.db,123,url,script),'already_queued')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}),1)
        self.assertEqual(await owner_trial.status(self.db),'queued')
        script['turns'][0]['text']='Другой сценарий'
        with self.assertRaises(ValueError): await owner_trial.admit(self.db,123,url,script)
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}),1)

    async def test_blocked_when_user_waits_or_worker_not_ready(self):
        url,script=owner_trial.parse_message(TEXT); await owner_trial.admit(self.db,123,url,script)
        queue=VideoQueue(self.db.media); await queue.ensure(); await queue.acquire()
        client=SimpleNamespace(supports_news=AsyncMock(return_value=True),submit=AsyncMock())
        await queue.enqueue({'id':'ordinary'})
        await owner_trial.dispatch_once(queue,self.db.news_owner_trial_jobs,client)
        client.supports_news.assert_not_awaited(); client.submit.assert_not_awaited()
        await queue.remove('ordinary'); client.supports_news.return_value=False
        await owner_trial.dispatch_once(queue,self.db.news_owner_trial_jobs,client)
        self.assertEqual(await owner_trial.status(self.db),'queued'); client.submit.assert_not_awaited()
        await queue.release()

    async def test_uncertain_worker_and_delivery_do_not_repeat(self):
        from news_prototype.news_worker_client import NewsUncertain
        url,script=owner_trial.parse_message(TEXT); await owner_trial.admit(self.db,123,url,script)
        queue=VideoQueue(self.db.media); await queue.ensure(); await queue.acquire()
        client=SimpleNamespace(supports_news=AsyncMock(return_value=True), submit=AsyncMock(side_effect=NewsUncertain()))
        await owner_trial.dispatch_once(queue,self.db.news_owner_trial_jobs,client)
        self.assertEqual(await owner_trial.status(self.db),'uncertain')
        await owner_trial.dispatch_once(queue,self.db.news_owner_trial_jobs,client)
        self.assertEqual(client.submit.await_count,1)
        self.assertEqual(await owner_trial.admit(self.db,123,url,script),'already_queued')
        await self.db.news_owner_trial_jobs.update_one({}, {'$set': {'stage':'ready_for_review'}})
        async def download(job,path): path.write_bytes(b'video'); return 5,'checksum'
        client.download=AsyncMock(side_effect=download)
        bot=SimpleNamespace(send_video=AsyncMock(side_effect=TimeoutError()))
        self.assertEqual(await review_delivery.run_once(self.db.news_owner_trial_jobs,
            self.db.news_owner_trial_candidates,client,bot,123),'review_uncertain')
        self.assertEqual(await review_delivery.run_once(self.db.news_owner_trial_jobs,
            self.db.news_owner_trial_candidates,client,bot,123),'idle')
        self.assertEqual(bot.send_video.await_count,1)
        await queue.release()

    async def test_full_path_with_worker_http_and_mock_delivery(self):
        from video_prototype import worker,news_jobs
        from news_prototype.news_worker_client import NewsWorkerClient
        with tempfile.TemporaryDirectory() as temp:
            def render(job,folder):
                work=folder/'work'; work.mkdir(); (work/'video.mp4').write_bytes(b'video')
                (work/'status.json').write_text('{"stage":"ready_for_review"}')
            shared=worker.JobStore(lambda *_:'normal')
            news=news_jobs.NewsJobs(shared,Path(temp)/'worker',render)
            server=ThreadingHTTPServer(('127.0.0.1',0),worker.handler_for(shared,'secret',news))
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            try:
                from handlers import news_owner_trial as handler
                msg=SimpleNamespace(from_user=SimpleNamespace(id=123),chat=SimpleNamespace(type='private'),text=TEXT,answer=AsyncMock())
                with patch.object(handler,'ADMIN_ID',123), patch.object(handler,'db',SimpleNamespace(db=self.db)):
                    await handler.submit_trial(msg)
                self.assertEqual(await owner_trial.status(self.db),'queued')
                url,script=owner_trial.parse_message(TEXT)
                queue=VideoQueue(self.db.media); await queue.ensure(); await queue.acquire()
                client=NewsWorkerClient(f'http://127.0.0.1:{server.server_port}','secret')
                for _ in range(100):
                    await owner_trial.dispatch_once(queue,self.db.news_owner_trial_jobs,client)
                    if await owner_trial.status(self.db)=='ready_for_review': break
                    await asyncio.sleep(.01)
                self.assertEqual(await owner_trial.status(self.db),'ready_for_review')
                bot=SimpleNamespace(send_video=AsyncMock(return_value=SimpleNamespace(message_id=99)))
                self.assertEqual(await review_delivery.run_once(self.db.news_owner_trial_jobs,
                    self.db.news_owner_trial_candidates,client,bot,123),'review_sent')
                self.assertEqual(bot.send_video.await_args.args[0],123)
                self.assertEqual(await owner_trial.status(self.db),'review_sent')
                self.assertEqual(await owner_trial.admit(self.db,123,url,script),'already_queued')
                self.assertEqual(await review_delivery.run_once(self.db.news_owner_trial_jobs,
                    self.db.news_owner_trial_candidates,client,bot,123),'idle')
                bot.send_video.assert_awaited_once()
            finally:
                server.shutdown(); server.server_close(); thread.join()

    async def test_handler_only_owner_in_private(self):
        from handlers import news_owner_trial as handler
        with patch.object(handler,'ADMIN_ID',123), patch.object(handler,'db',SimpleNamespace(db=self.db)):
            for user,chat in [(456,'private'),(123,'group')]:
                msg=SimpleNamespace(from_user=SimpleNamespace(id=user),chat=SimpleNamespace(type=chat),text=TEXT,answer=AsyncMock())
                await handler.submit_trial(msg); msg.answer.assert_not_awaited()
            self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}),0)
            msg=SimpleNamespace(from_user=SimpleNamespace(id=123),chat=SimpleNamespace(type='private'),text=TEXT,answer=AsyncMock())
            await handler.submit_trial(msg)
            self.assertIn('поставлен',msg.answer.await_args.args[0])
            self.assertEqual(await owner_trial.status(self.db),'queued')


if __name__ == '__main__': unittest.main()
