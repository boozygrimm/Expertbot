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

    async def test_next_trial_only_after_confirmed_delivery_and_is_idempotent(self):
        url, script = owner_trial.parse_message(TEXT)
        await owner_trial.admit(self.db, 123, url, script)
        job = await self.db.news_owner_trial_jobs.find_one({})
        request_id = job['_id']
        for stage in ('queued', 'submitting', 'running', 'ready_for_review', 'fetching_review',
                      'sending_review', 'review_uncertain', 'review_too_large',
                      'review_download_failed', 'uncertain', 'interrupted', 'failed'):
            with self.subTest(stage=stage):
                await self.db.news_owner_trial_jobs.update_one({'_id': request_id}, {'$set': {'stage': stage}})
                self.assertEqual(await owner_trial.open_next(self.db, 123, request_id), 'not_allowed')
                self.assertNotEqual(await owner_trial.status(self.db), 'not_queued')
        await self.db.news_owner_trial_jobs.update_one({'_id': request_id}, {'$set': {'stage': 'review_sent'}})
        self.assertEqual(await owner_trial.open_next(self.db, 123, request_id), 'not_allowed')
        for invalid_id in (0, -1, True, '7'):
            await self.db.news_owner_trial_jobs.update_one({'_id': request_id}, {'$set': {'review_message_id': invalid_id}})
            self.assertEqual(await owner_trial.status_info(self.db), ('review_sent', None))
            self.assertEqual(await owner_trial.open_next(self.db, 123, request_id), 'not_allowed')
        await self.db.news_owner_trial_jobs.update_one({'_id': request_id}, {'$set': {'review_message_id': 7}})
        self.assertEqual(await owner_trial.status_info(self.db), ('review_sent', request_id))
        self.assertEqual(await owner_trial.open_next(self.db, 456, request_id), 'not_allowed')
        self.assertEqual(await owner_trial.open_next(self.db, 123, request_id), 'opened')
        self.assertEqual(await owner_trial.open_next(self.db, 123, request_id), 'already_open')
        self.assertEqual(await owner_trial.status(self.db), 'not_queued')
        self.assertEqual((await self.db.news_owner_trial_jobs.find_one({'_id': request_id}))['stage'], 'review_sent')
        # An old duplicate neither replays the job nor consumes the new slot.
        self.assertEqual(await owner_trial.admit(self.db, 123, url, script), 'already_queued')
        self.assertEqual(await owner_trial.status(self.db), 'not_queued')

        changed = dict(script, ticker='ДРУГАЯ СТРОКА')
        self.assertEqual(await owner_trial.admit(self.db, 123, url, changed), 'queued')
        self.assertEqual(await owner_trial.open_next(self.db, 123, request_id), 'not_allowed')
        self.assertEqual(await owner_trial.status(self.db), 'queued')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 2)

    async def test_concurrent_open_and_admission_do_not_duplicate_or_release_new_trial(self):
        url, script = owner_trial.parse_message(TEXT)
        await owner_trial.admit(self.db, 123, url, script)
        job = await self.db.news_owner_trial_jobs.find_one({})
        await self.db.news_owner_trial_jobs.update_one({'_id': job['_id']},
            {'$set': {'stage': 'review_sent', 'review_message_id': 9}})
        results = await asyncio.gather(*(owner_trial.open_next(self.db, 123, job['_id']) for _ in range(2)))
        self.assertEqual(results.count('opened'), 1)
        self.assertTrue(all(result in ('opened', 'already_open', 'changed') for result in results))
        first = dict(script, ticker='ПЕРВАЯ')
        second = dict(script, ticker='ВТОРАЯ')
        outcomes = await asyncio.gather(owner_trial.admit(self.db, 123, url, first),
                                        owner_trial.admit(self.db, 123, url, second),
                                        return_exceptions=True)
        self.assertEqual(outcomes.count('queued'), 1)
        self.assertEqual(sum(isinstance(result, ValueError) for result in outcomes), 1)
        self.assertEqual(await owner_trial.open_next(self.db, 123, job['_id']), 'not_allowed')
        self.assertEqual(await owner_trial.status(self.db), 'queued')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 2)

    async def test_stale_release_cannot_clear_a_new_gate_during_compare_and_set(self):
        url, script = owner_trial.parse_message(TEXT)
        await owner_trial.admit(self.db, 123, url, script)
        old_gate = await self.db.news_owner_trial_gate.find_one({'_id': 'slot'})
        job = await self.db.news_owner_trial_jobs.find_one({})
        await self.db.news_owner_trial_jobs.update_one({'_id': job['_id']},
            {'$set': {'stage': 'review_sent', 'review_message_id': 10}})
        gate = self.db.news_owner_trial_gate
        new_candidate_id = 'owner-trial:' + 'a' * 64

        class InterleavedGate:
            async def find_one(self, *args, **kwargs):
                return await gate.find_one(*args, **kwargs)
            async def update_one(self, query, update):
                # Simulate another completed release and a new admission after
                # the stale command's reads but before its final CAS write.
                self_test.assertEqual(query['candidate_id'], old_gate['candidate_id'])
                await gate.update_one({'_id': 'slot'}, {'$set': {'candidate_id': new_candidate_id}})
                return await gate.update_one(query, update)

        self_test = self
        db = SimpleNamespace(news_owner_trial_gate=InterleavedGate(),
                             news_owner_trial_candidates=self.db.news_owner_trial_candidates,
                             news_owner_trial_jobs=self.db.news_owner_trial_jobs)
        self.assertEqual(await owner_trial.open_next(db, 123, job['_id']), 'changed')
        self.assertEqual((await gate.find_one({'_id': 'slot'}))['candidate_id'], new_candidate_id)

    async def test_anomalous_existing_job_does_not_claim_open_slot(self):
        url, script = owner_trial.parse_message(TEXT)
        await owner_trial.admit(self.db, 123, url, script)
        job = await self.db.news_owner_trial_jobs.find_one({})
        await self.db.news_owner_trial_jobs.update_one({'_id': job['_id']},
            {'$set': {'stage': 'review_sent', 'review_message_id': 10}})
        self.assertEqual(await owner_trial.open_next(self.db, 123, job['_id']), 'opened')
        # External corruption or an old incompatible writer must not turn an
        # existing uncertain operation into another admitted attempt.
        await self.db.news_owner_trial_jobs.update_one({'_id': job['_id']}, {'$set': {'stage': 'uncertain'}})
        with self.assertRaises(ValueError):
            await owner_trial.admit(self.db, 123, url, script)
        self.assertEqual(await owner_trial.status(self.db), 'not_queued')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 1)

    async def test_invalid_or_corrupt_gate_does_not_authorize_new_trial(self):
        url, script = owner_trial.parse_message(TEXT)
        await self.db.news_owner_trial_gate.insert_one({'_id': 'slot', 'candidate_id': None})
        self.assertEqual(await owner_trial.status(self.db), 'admission_incomplete')
        with self.assertRaises(ValueError):
            await owner_trial.admit(self.db, 123, url, script)
        with self.assertRaises(ValueError):
            await owner_trial.open_next(self.db, 123, 'not-a-uuid')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_blocked_when_user_waits_or_worker_not_ready(self):
        url,script=owner_trial.parse_message(TEXT); await owner_trial.admit(self.db,123,url,script)
        queue=VideoQueue(self.db.media); await queue.ensure(); await queue.acquire()
        client=SimpleNamespace(supports_news=AsyncMock(return_value=True),submit=AsyncMock())
        await queue.enqueue({'id':'ordinary'})
        await owner_trial.dispatch_once(queue,self.db,client)
        client.supports_news.assert_not_awaited(); client.submit.assert_not_awaited()
        await queue.remove('ordinary'); client.supports_news.return_value=False
        await owner_trial.dispatch_once(queue,self.db,client)
        self.assertEqual(await owner_trial.status(self.db),'queued'); client.submit.assert_not_awaited()
        await queue.release()

    async def test_persistent_mode_dispatches_existing_queue_without_env_flag(self):
        import main
        from news_prototype import news_worker_client
        url, script = owner_trial.parse_message(TEXT)
        await owner_trial.admit(self.db, 123, url, script)
        queue = VideoQueue(self.db.media)
        await queue.ensure()
        await queue.acquire()
        client = SimpleNamespace(supports_news=AsyncMock(return_value=True),
                                 submit=AsyncMock(return_value={'status': 'running'}),
                                 status=AsyncMock(return_value={'status': 'ready'}))
        with patch.dict(os.environ, {'NEWS_OWNER_TRIAL_ENABLED': '0'}), \
             patch.object(main, 'db', SimpleNamespace(db=self.db)), \
             patch.object(news_worker_client, 'NewsWorkerClient', return_value=client):
            # No environment opt-in and no persistent opt-in: no dispatch.
            await main.run_news_idle(queue)
            client.submit.assert_not_awaited()
            await owner_trial.set_owner_mode(self.db, True)
            # User videos still take priority; incompatible modes remain blocked.
            await queue.enqueue({'id': 'ordinary'})
            await main.run_news_idle(queue)
            client.submit.assert_not_awaited()
            await queue.remove('ordinary')
            with patch.dict(os.environ, {'NEWS_RENDER_QUEUE_ENABLED': '1'}):
                await owner_trial.dispatch_once(queue, self.db, client)
                client.submit.assert_not_awaited()
            await main.run_news_idle(queue)
            self.assertEqual(await owner_trial.status(self.db), 'running')
            client.submit.assert_awaited_once()
            await main.run_news_idle(queue)
            self.assertEqual(await owner_trial.status(self.db), 'ready_for_review')
            client.submit.assert_awaited_once()
            # Turning off via the panel blocks dispatch despite the legacy env flag.
            await self.db.news_owner_trial_jobs.update_one({}, {'$set': {'stage': 'queued'}})
            await owner_trial.set_owner_mode(self.db, False)
            with patch.dict(os.environ, {'NEWS_OWNER_TRIAL_ENABLED': '1'}):
                await owner_trial.dispatch_once(queue, self.db, client)
            client.submit.assert_awaited_once()
        await queue.release()

    async def test_uncertain_worker_and_delivery_do_not_repeat(self):
        from news_prototype.news_worker_client import NewsUncertain
        url,script=owner_trial.parse_message(TEXT); await owner_trial.admit(self.db,123,url,script)
        queue=VideoQueue(self.db.media); await queue.ensure(); await queue.acquire()
        client=SimpleNamespace(supports_news=AsyncMock(return_value=True), submit=AsyncMock(side_effect=NewsUncertain()))
        await owner_trial.dispatch_once(queue,self.db,client)
        self.assertEqual(await owner_trial.status(self.db),'uncertain')
        await owner_trial.dispatch_once(queue,self.db,client)
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
                    await owner_trial.dispatch_once(queue,self.db,client)
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

                # A confirmed first delivery can be explicitly retired, without
                # replaying it, and one changed scenario can use the same slot.
                _, first_id = await owner_trial.status_info(self.db)
                self.assertEqual(await owner_trial.open_next(self.db, 123, first_id), 'opened')
                revised = dict(script, ticker='БОЛЬШЕ НЕ ПРИНИМАЮТ СТАТИЧНЫЕ МАКЕТЫ')
                self.assertEqual(await owner_trial.admit(self.db, 123, url, revised), 'queued')
                self.assertEqual(await owner_trial.open_next(self.db, 123, first_id), 'not_allowed')
                with self.assertRaises(ValueError):
                    await owner_trial.admit(self.db, 123, url, script)
                for _ in range(100):
                    await owner_trial.dispatch_once(queue, self.db, client)
                    if await owner_trial.status(self.db) == 'ready_for_review': break
                    await asyncio.sleep(.01)
                self.assertEqual(await owner_trial.status(self.db), 'ready_for_review')
                self.assertEqual(await review_delivery.run_once(self.db.news_owner_trial_jobs,
                    self.db.news_owner_trial_candidates, client, bot, 123), 'review_sent')
                self.assertEqual(await review_delivery.run_once(self.db.news_owner_trial_jobs,
                    self.db.news_owner_trial_candidates, client, bot, 123), 'idle')
                self.assertEqual(bot.send_video.await_count, 2)
                self.assertEqual((await self.db.news_owner_trial_jobs.find_one({'_id': first_id}))['stage'], 'review_sent')
            finally:
                server.shutdown(); server.server_close(); thread.join()

    async def test_next_handler_requires_owner_private_and_exact_completed_id(self):
        from handlers import news_owner_trial as handler
        url, script = owner_trial.parse_message(TEXT)
        await owner_trial.admit(self.db, 123, url, script)
        job = await self.db.news_owner_trial_jobs.find_one({})
        await self.db.news_owner_trial_jobs.update_one({'_id': job['_id']},
            {'$set': {'stage': 'review_sent', 'review_message_id': 12}})
        with patch.object(handler, 'ADMIN_ID', 123), patch.object(handler, 'db', SimpleNamespace(db=self.db)):
            for user, chat in [(456, 'private'), (123, 'group')]:
                msg = SimpleNamespace(from_user=SimpleNamespace(id=user), chat=SimpleNamespace(type=chat),
                                      text=f'/news_trial_next {job["_id"]}', answer=AsyncMock())
                await handler.next_trial(msg)
                msg.answer.assert_not_awaited()
            status = SimpleNamespace(from_user=SimpleNamespace(id=123), chat=SimpleNamespace(type='private'),
                                     text='/news_trial_status', answer=AsyncMock())
            await handler.trial_status(status)
            self.assertIn(f'/news_trial_next {job["_id"]}', status.answer.await_args.args[0])
            bad = SimpleNamespace(from_user=SimpleNamespace(id=123), chat=SimpleNamespace(type='private'),
                                  text='/news_trial_next', answer=AsyncMock())
            await handler.next_trial(bad)
            self.assertIn('идентификатором', bad.answer.await_args.args[0])
            self.assertEqual(await owner_trial.status(self.db), 'review_sent')
            msg = SimpleNamespace(from_user=SimpleNamespace(id=123), chat=SimpleNamespace(type='private'),
                                  text=f'/news_trial_next {job["_id"]}', answer=AsyncMock())
            with patch.dict(os.environ, {'NEWS_OWNER_TRIAL_ENABLED': '0'}):
                await handler.next_trial(msg)
                self.assertIn('выключена', msg.answer.await_args.args[0])
            self.assertEqual(await owner_trial.status(self.db), 'review_sent')
            msg.answer.reset_mock()
            await handler.next_trial(msg)
            self.assertIn('Слот свободен', msg.answer.await_args.args[0])
            self.assertEqual(await owner_trial.status(self.db), 'not_queued')

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
