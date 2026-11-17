import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from mongomock_motor import AsyncMongoMockClient
from news_prototype import owner_bulletin, owner_bulletin_sources, owner_bulletin_admission as admission, owner_trial, news_queue
from tests.test_owner_drafts import SOURCE
from tests.test_bulletin_foundation import response


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        p = patch.object(owner_trial, 'runtime_enabled', AsyncMock(return_value=True)); p.start(); self.addCleanup(p.stop)

    async def prepare(self, count=2, number=99):
        choices = [{'url':f'https://example.com/{n}', 'title':f'Новость {n}'} for n in range(count)]
        await owner_bulletin_sources.create(self.db, 123, number, choices)
        await owner_bulletin_sources.collect(self.db, 123, number, AsyncMock(return_value={'summary':SOURCE}), AsyncMock(return_value=None))
        await owner_bulletin.prepare(self.db, 123, number, AsyncMock(side_effect=lambda s:response(s)))
        return await owner_bulletin.get(self.db, 123, number)

    async def test_2_5_10_20_atomic_admission_once_and_exact_snapshot(self):
        for count in (2, 5, 10, 20):
            with self.subTest(count=count):
                self.db = AsyncMongoMockClient().test
                item = await self.prepare(count)
                expected = await owner_bulletin.approved_payload(self.db, 123, 99, item['revision'])
                result = await asyncio.gather(*[admission.approve(self.db, 123, 99, item['revision']) for _ in range(3)])
                self.assertEqual(result.count('queued'), 1)
                self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 1)
                job = await self.db.news_owner_trial_jobs.find_one({})
                self.assertEqual(job['bulletin'], expected)
                self.assertEqual(job['kind'], 'bulletin')
                self.assertEqual((await admission.status(self.db, 123, 99))[0], 'queued')
                self.assertEqual(await admission.status(self.db, 124, 99), ('not_found', None))
                self.assertEqual(await owner_bulletin.change_section(self.db, 123, 99, item['revision'],
                    item['sections'][0]['id'], text='Строка: новое\nБендер: Текст.'), 'stale')

    async def test_edit_race_and_stale_confirmation(self):
        item = await self.prepare()
        revision = item['revision']; section = item['sections'][0]['id']
        edited = 'Строка: новый заголовок\nБендер: Новая реплика.'
        results = await asyncio.gather(admission.approve(self.db, 123, 99, revision),
            owner_bulletin.change_section(self.db, 123, 99, revision, section, text=edited))
        self.assertIn(results, (['queued', 'stale'], ['stale', 'saved'], ['changed', 'saved']))
        if results[1] == 'saved':
            self.assertEqual(await admission.approve(self.db, 123, 99, revision), 'stale')
            self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_changed_sources_and_disabled_prevent_admission(self):
        item = await self.prepare()
        await self.db.news_owner_bulletins.update_one({'bulletin_id':99}, {'$set': {'source_revision':'0'*64}})
        self.assertEqual(await admission.approve(self.db, 123, 99, item['revision']), 'stale')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
        await self.db.news_owner_bulletins.update_one({'bulletin_id':99}, {'$set': {'source_revision':owner_bulletin.revision(item['stories'], [])}})
        owner_trial.runtime_enabled.return_value = False
        self.assertEqual(await admission.approve(self.db, 123, 99, item['revision']), 'disabled')
        self.assertEqual((await owner_bulletin.get(self.db,123,99))['stage'], 'ready_for_review')
        self.assertEqual(await self.db.news_owner_trial_gate.count_documents({}), 0)

    async def test_slot_busy_and_reuse_only_confirmed_delivery(self):
        one = await self.prepare(number=99)
        two = await self.prepare(number=100)
        self.assertEqual(await admission.approve(self.db,123,99,one['revision']), 'queued')
        self.assertEqual(await admission.approve(self.db,123,100,two['revision']), 'trial_busy')
        job = await self.db.news_owner_trial_jobs.find_one({})
        await self.db.news_owner_trial_jobs.update_one({'_id':job['_id']}, {'$set':{'stage':'review_sent','review_message_id':7}})
        self.assertEqual(await admission.approve(self.db,123,100,two['revision']), 'queued')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 2)
        self.assertEqual(await admission.approve(self.db,123,99,one['revision']), 'already_processed')

    async def test_partial_admission_never_automatically_retries(self):
        item = await self.prepare()
        real = news_queue.enqueue_bulletin
        async def insert_then_fail(*args):
            await real(*args)
            raise TimeoutError('response lost')
        with patch.object(news_queue, 'enqueue_bulletin', side_effect=insert_then_fail):
            self.assertEqual(await admission.approve(self.db,123,99,item['revision']), 'approval_uncertain')
        self.assertEqual((await admission.status(self.db,123,99))[0], 'queued')
        self.assertEqual(await admission.approve(self.db,123,99,item['revision']), 'already_processed')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 1)

    async def test_duration_rejection_requires_edits_and_new_confirmation(self):
        item = await self.prepare()
        self.assertEqual(await admission.approve(self.db,123,99,item['revision']), 'queued')
        job = await self.db.news_owner_trial_jobs.find_one({})
        self.assertEqual(await admission.reopen_too_long(self.db,123,99,item['revision']), 'not_allowed')
        await self.db.news_owner_trial_jobs.update_one({'_id':job['_id']}, {'$set':{'stage':'audio_too_long'}})
        self.assertEqual(await admission.reopen_too_long(self.db,124,99,item['revision']), 'stale')
        self.assertEqual(await admission.reopen_too_long(self.db,123,99,item['revision']), 'reopened')
        self.assertEqual(await admission.approve(self.db,123,99,item['revision']), 'revision_used')
        result = await owner_bulletin.change_section(self.db,123,99,item['revision'],item['sections'][0]['id'],
                    text='Строка: коротко\nБендер: Короче.')
        self.assertEqual(result, 'saved')
        changed = await owner_bulletin.get(self.db,123,99)
        self.assertNotEqual(item['revision'], changed['revision'])
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 1)
        self.assertEqual(await admission.approve(self.db,123,99,changed['revision']), 'queued')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 2)

    async def test_interrupted_reopen_can_resume_without_new_job(self):
        item = await self.prepare(); rev = item['revision']
        await admission.approve(self.db,123,99,rev)
        job = await self.db.news_owner_trial_jobs.find_one({})
        await self.db.news_owner_trial_jobs.update_one({'_id':job['_id']}, {'$set':{'stage':'audio_too_long'}})
        await self.db.news_owner_bulletins.update_one({'bulletin_id':99}, {'$set':{'stage':'shortening'}})
        await self.db.news_owner_trial_gate.update_one({'_id':'slot'}, {'$set':{'candidate_id':None,'ready_for_next':True, 'last_released_request_id':job['_id']}})
        self.assertEqual(await admission.reopen_too_long(self.db,123,99,rev), 'reopened')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 1)

    async def test_two_different_bulletins_compete_for_one_slot(self):
        one = await self.prepare(number=99); two = await self.prepare(number=100)
        results = await asyncio.gather(admission.approve(self.db, 123, 99, one['revision']),
                                       admission.approve(self.db, 123, 100, two['revision']))
        self.assertEqual(results.count('queued'), 1)
        self.assertIn(next(r for r in results if r != 'queued'), ('trial_busy', 'approval_uncertain'))
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 1)
        job = await self.db.news_owner_trial_jobs.find_one({})
        gate = await self.db.news_owner_trial_gate.find_one({'_id':'slot'})
        self.assertEqual(gate['candidate_id'], job['candidate_id'])

    async def test_failure_after_slot_reservation_never_requeues(self):
        item = await self.prepare()
        with patch.object(news_queue, 'enqueue_bulletin', AsyncMock(side_effect=OSError('database unavailable'))):
            self.assertEqual(await admission.approve(self.db,123,99,item['revision']), 'approval_uncertain')
        self.assertEqual((await admission.status(self.db,123,99))[0], 'approval_uncertain')
        self.assertEqual(await admission.approve(self.db,123,99,item['revision']), 'already_processed')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
        self.assertIsNotNone((await self.db.news_owner_trial_gate.find_one({'_id':'slot'}))['candidate_id'])
