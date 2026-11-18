import unittest
from unittest.mock import AsyncMock
try:
    from mongomock_motor import AsyncMongoMockClient
except ImportError:
    AsyncMongoMockClient = None
from bender_queue import VideoQueue
from news_prototype import news_queue
from news_prototype.news_worker_client import NewsBusy, NewsUncertain


@unittest.skipIf(AsyncMongoMockClient is None, 'install mongomock-motor for MongoDB queue tests')
class NewsQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db = AsyncMongoMockClient().test
        self.jobs = db.news_jobs; self.queue = VideoQueue(db.media, owner='owner')
        await self.queue.ensure(); self.assertTrue(await self.queue.acquire())
        self.script = {'turns':[{'speaker':'Бендер','text':'Тест'},
                                {'speaker':'Киберсенсей','text':'Ответ'}]}
        self.client = type('Client', (), {})()
        self.client.supports_story_studio = AsyncMock(return_value=True)
        self.client.submit_studio = AsyncMock(return_value={'status':'running'})
        self.client.status = AsyncMock(return_value={'status':'ready'})
        self.client.status_studio = AsyncMock(return_value={'status':'ready'})

    async def job(self): return await self.jobs.find_one({})

    async def test_idempotent_enqueue_and_complete_without_delivery(self):
        self.assertTrue(await news_queue.enqueue_script(self.jobs,'candidate-1',self.script))
        self.assertFalse(await news_queue.enqueue_script(self.jobs,'candidate-1',self.script))
        changed = {'turns': [{'speaker': 'Бендер', 'text': 'Другой текст'}]}
        with self.assertRaises(ValueError):
            await news_queue.enqueue_script(self.jobs,'candidate-1',changed)
        await news_queue.run_idle_once(self.queue,self.jobs,self.client)
        self.assertEqual((await self.job())['stage'],'running')
        self.client.submit_studio.assert_awaited_once()
        await news_queue.run_idle_once(self.queue,self.jobs,self.client)
        self.assertEqual((await self.job())['stage'],'ready_for_review')
        self.client.status_studio.assert_awaited_once()

    async def test_new_studio_is_required_even_without_image(self):
        await news_queue.enqueue_script(self.jobs, 'candidate', self.script)
        self.client.supports_story_studio.return_value = False
        await news_queue.run_idle_once(self.queue, self.jobs, self.client)
        self.client.submit_studio.assert_not_awaited()
        self.assertEqual((await self.job())['stage'], 'queued')
        self.client.supports_story_studio.return_value = True
        await news_queue.run_idle_once(self.queue, self.jobs, self.client)
        self.client.submit_studio.assert_awaited_once()

    async def test_digest_selection_order_and_dispatch(self):
        await news_queue.enqueue_script(self.jobs,'one',self.script)
        await news_queue.enqueue_script(self.jobs,'two',self.script)
        jobs=await self.jobs.find({}).to_list(length=10)
        ids=[j['_id'] for j in jobs]
        with self.assertRaises(ValueError):
            await news_queue.enqueue_digest(self.jobs,22,33,ids)
        await self.jobs.update_many({}, {'$set': {'stage':'review_sent'}})
        with self.assertRaises(ValueError):
            await news_queue.enqueue_digest(self.jobs,22,33,[ids[0],ids[0]])
        self.assertTrue(await news_queue.enqueue_digest(self.jobs,22,33,ids[::-1]))
        self.assertFalse(await news_queue.enqueue_digest(self.jobs,22,33,ids[::-1]))
        self.client.submit_digest=AsyncMock(return_value={'status':'running'})
        await news_queue.run_idle_once(self.queue,self.jobs,self.client)
        digest=await self.jobs.find_one({'kind':'digest'})
        self.client.submit_digest.assert_awaited_once_with(digest['request_id'],ids[::-1])
        await news_queue.run_idle_once(self.queue,self.jobs,self.client)
        self.assertEqual((await self.jobs.find_one({'kind':'digest'}))['stage'],'ready_for_review')
        with self.assertRaises(ValueError):
            await news_queue.enqueue_digest(self.jobs,22,34,[ids[0],digest['_id']])

    async def test_short_codes_must_be_unambiguous_and_can_use_full_id(self):
        await news_queue.enqueue_script(self.jobs,'one',self.script)
        await news_queue.enqueue_script(self.jobs,'two',self.script)
        jobs=await self.jobs.find({}).to_list(length=10); ids=[j['_id'] for j in jobs]
        self.assertEqual(await news_queue.resolve_story_codes(self.jobs,[ids[1][:8],ids[0]]),ids[::-1])
        await self.jobs.insert_one({'_id':ids[1][:8]+'-0000-0000-0000-000000000000'})
        with self.assertRaises(ValueError):
            await news_queue.resolve_story_codes(self.jobs,[ids[1][:8],ids[0][:8]])
        with self.assertRaises(ValueError):
            await news_queue.resolve_story_codes(self.jobs,['.*','12345678'])

    async def test_user_jobs_take_priority(self):
        await news_queue.enqueue_script(self.jobs,'candidate-1',self.script)
        await self.queue.enqueue({'id':'user-job'})
        await news_queue.run_idle_once(self.queue,self.jobs,self.client)
        self.client.submit_studio.assert_not_awaited()
        self.assertEqual((await self.job())['stage'],'queued')

    async def test_busy_can_retry_but_uncertain_post_cannot(self):
        await news_queue.enqueue_script(self.jobs,'candidate-1',self.script)
        self.client.submit_studio.side_effect=NewsBusy()
        await news_queue.run_idle_once(self.queue,self.jobs,self.client)
        self.assertEqual((await self.job())['stage'],'queued')
        self.client.submit_studio.side_effect=NewsUncertain()
        await news_queue.run_idle_once(self.queue,self.jobs,self.client)
        self.assertEqual((await self.job())['stage'],'uncertain')
        await news_queue.run_idle_once(self.queue,self.jobs,self.client)
        self.assertEqual(self.client.submit_studio.await_count,2)

    async def test_recovery_queries_same_id_and_never_resubmits(self):
        await news_queue.enqueue_script(self.jobs,'candidate-1',self.script)
        job=await self.job()
        await self.jobs.update_one({'_id':job['_id']},{'$set':{'stage':'submitting'}})
        self.client.status.return_value=None
        await news_queue.run_idle_once(self.queue,self.jobs,self.client)
        self.client.status.assert_awaited_once_with(job['request_id'])
        self.client.submit_studio.assert_not_awaited()
        self.assertEqual((await self.job())['stage'],'uncertain')

    async def test_user_request_arrives_before_submission(self):
        await news_queue.enqueue_script(self.jobs,'candidate-1',self.script)
        checks=0
        original=self.queue.head
        async def head():
            nonlocal checks
            checks += 1
            return {'id':'user'} if checks >= 3 else await original()
        self.queue.head=head
        await news_queue.run_idle_once(self.queue,self.jobs,self.client)
        self.client.submit_studio.assert_not_awaited()
        self.assertEqual((await self.job())['stage'],'queued')


if __name__ == '__main__': unittest.main()
