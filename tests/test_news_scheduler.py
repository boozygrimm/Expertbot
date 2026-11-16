import asyncio
import unittest
try:
    from mongomock_motor import AsyncMongoMockClient
except ImportError:
    AsyncMongoMockClient = None
from news_prototype import news_scheduler


@unittest.skipIf(AsyncMongoMockClient is None, 'mongomock-motor optional')
class NewsSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_distinct_callers_do_not_run_same_task_concurrently(self):
        locks=AsyncMongoMockClient().test.locks
        started,release=asyncio.Event(),asyncio.Event(); calls=[]
        async def work():
            calls.append('started'); started.set(); await release.wait()
        first=asyncio.create_task(news_scheduler._locked(locks,'poll',work))
        await asyncio.wait_for(started.wait(),1)
        await news_scheduler._locked(locks,'poll',work)
        self.assertEqual(calls,['started'])
        release.set(); await first
        await news_scheduler._locked(locks,'poll',work)
        self.assertEqual(calls,['started','started'])

    async def test_releases_lease_even_on_failure(self):
        locks=AsyncMongoMockClient().test.locks
        async def fail(): raise RuntimeError('error')
        with self.assertRaises(RuntimeError): await news_scheduler._locked(locks,'edit',fail)
        calls=[]
        async def success(): calls.append(1)
        await news_scheduler._locked(locks,'edit',success)
        self.assertEqual(calls,[1])


if __name__ == '__main__': unittest.main()
