import asyncio
from datetime import timedelta
import unittest

from bender_queue import QUEUE_ID, QUEUE_LIMIT, LeaseLost, VideoQueue, now

try:
    from mongomock_motor import AsyncMongoMockClient
except ImportError:
    AsyncMongoMockClient = None


@unittest.skipIf(AsyncMongoMockClient is None, 'install mongomock-motor for MongoDB queue tests')
class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.collection = AsyncMongoMockClient().test.video_queue
        self.first = VideoQueue(self.collection, owner='first')
        self.second = VideoQueue(self.collection, owner='second')
        await self.first.ensure()

    async def test_atomic_capacity_fifo_and_duplicate(self):
        for i in range(QUEUE_LIMIT):
            self.assertEqual(await self.first.enqueue({'id': str(i), 'stage': 'queued'}), 'accepted')
        self.assertEqual(await self.second.enqueue({'id': '0'}), 'duplicate')
        self.assertEqual(await self.second.enqueue({'id': 'extra'}), 'full')
        self.assertTrue(await self.first.acquire())
        self.assertEqual((await self.first.head())['id'], '0')
        await self.first.remove('0')
        self.assertEqual(await self.second.enqueue({'id': 'extra'}), 'accepted')
        self.assertEqual((await self.first.head())['id'], '1')

    async def test_concurrent_admission_remains_bounded_and_deduplicated(self):
        results = await asyncio.gather(*(
            (self.first if i % 2 == 0 else self.second).enqueue({'id': str(i)})
            for i in range(30)))
        self.assertEqual(results.count('accepted'), QUEUE_LIMIT)
        doc = await self.collection.find_one({'_id': QUEUE_ID})
        self.assertEqual(len(doc['items']), QUEUE_LIMIT)
        await self.collection.update_one({'_id': QUEUE_ID}, {'$set': {'items': []}})
        results = await asyncio.gather(*(self.first.enqueue({'id': 'same'}) for _ in range(10)))
        self.assertEqual(results.count('accepted'), 1)
        self.assertEqual(results.count('duplicate'), 9)

    async def test_only_owner_can_modify_and_expired_lease_is_fenced(self):
        await self.first.enqueue({'id': 'a', 'stage': 'queued'})
        self.assertTrue(await self.first.acquire())
        self.assertFalse(await self.second.acquire())
        with self.assertRaises(LeaseLost):
            await self.second.update('a', stage='ready')
        await self.collection.update_one({'_id': QUEUE_ID}, {'$set': {'lease_until': now() - timedelta(seconds=1)}})
        with self.assertRaises(LeaseLost):
            await self.first.update('a', stage='ready')
        self.assertTrue(await self.second.acquire())
        await self.second.update('a', stage='submitting', request_id='known-uuid')
        await self.first.release()  # stale owner must not clear the new lease
        self.assertEqual((await self.second.head())['request_id'], 'known-uuid')

    async def test_restart_recovers_fifo_items(self):
        await self.first.enqueue({'id': 'a', 'stage': 'queued'})
        await self.first.enqueue({'id': 'b', 'stage': 'queued'})
        self.assertTrue(await self.first.acquire())
        await self.first.update('a', stage='submitting', request_id='stable-uuid')
        await self.first.release()
        self.assertTrue(await self.second.acquire())
        self.assertEqual((await self.second.head())['stage'], 'submitting')
        self.assertEqual((await self.second.head())['request_id'], 'stable-uuid')
        await self.second.remove('a')
        self.assertEqual((await self.second.head())['id'], 'b')

    async def test_two_consumers_run_jobs_in_order_without_parallel_work(self):
        for i in range(4):
            await self.first.enqueue({'id': str(i), 'stage': 'queued'})
        active = 0
        observed = []
        completed = asyncio.Event()
        async def process(queue, item):
            nonlocal active
            active += 1
            self.assertEqual(active, 1)
            observed.append(item['id'])
            await asyncio.sleep(.02)
            await queue.remove(item['id'])
            active -= 1
            if len(observed) == 4:
                completed.set()
            return False
        tasks = [asyncio.create_task(queue.run(process)) for queue in (self.first, self.second)]
        try:
            await asyncio.wait_for(completed.wait(), 5)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        self.assertEqual(observed, ['0', '1', '2', '3'])

    async def test_queue_consumer_continues_after_one_error(self):
        await self.first.enqueue({'id': 'a', 'stage': 'queued'})
        called = 0
        finished = asyncio.Event()
        async def process(queue, item):
            nonlocal called
            called += 1
            if called == 1:
                raise RuntimeError('transient failure')
            await queue.remove(item['id'])
            finished.set()
            return False
        task = asyncio.create_task(self.first.run(process))
        try:
            await asyncio.wait_for(finished.wait(), 5)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(called, 2)
