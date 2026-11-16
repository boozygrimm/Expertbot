from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
try:
    from mongomock_motor import AsyncMongoMockClient
except ImportError:
    AsyncMongoMockClient = None
from news_prototype import review_delivery


@unittest.skipIf(AsyncMongoMockClient is None, 'mongomock-motor optional')
class ReviewDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db=AsyncMongoMockClient().test; self.jobs=db.jobs; self.candidates=db.candidates
        await self.jobs.insert_one({'_id':'job','stage':'ready_for_review','request_id':'uuid','candidate_id':'article','created_at':1})
        await self.candidates.insert_one({'_id':'article','title':'Новость','url':'https://example.com'})
        self.client=SimpleNamespace(download=AsyncMock(side_effect=self.download))
        self.bot=SimpleNamespace(send_video=AsyncMock(return_value=SimpleNamespace(message_id=5)))

    async def download(self,job,path):
        Path(path).write_bytes(b'video')
        return 5,'abc'

    async def test_send_owner_only_and_record_delivery(self):
        result=await review_delivery.run_once(self.jobs,self.candidates,self.client,self.bot,123)
        self.assertEqual(result,'review_sent')
        doc=await self.jobs.find_one({'_id':'job'})
        self.assertEqual(doc['stage'],'review_sent'); self.assertEqual(doc['review_message_id'],5)
        self.assertEqual(self.bot.send_video.await_args.args[0],123)
        self.assertIsNone(self.bot.send_video.await_args.kwargs['parse_mode'])
        self.assertIn('проверить',self.bot.send_video.await_args.kwargs['caption'])
        self.assertEqual(await review_delivery.run_once(self.jobs,self.candidates,self.client,self.bot,123),'idle')
        self.bot.send_video.assert_awaited_once()

    async def test_digest_delivery_is_owner_only(self):
        await self.jobs.update_one({'_id':'job'}, {'$set': {'kind':'digest'}, '$unset': {'candidate_id':''}})
        self.assertEqual(await review_delivery.run_once(self.jobs,self.candidates,self.client,self.bot,123),'review_sent')
        self.assertEqual(self.bot.send_video.await_args.args[0],123)
        self.assertIn('Дайджест',self.bot.send_video.await_args.kwargs['caption'])

    async def test_ambiguous_send_is_not_retried(self):
        self.bot.send_video.side_effect=TimeoutError('secret')
        self.assertEqual(await review_delivery.run_once(self.jobs,self.candidates,self.client,self.bot,123),'review_uncertain')
        self.assertEqual((await self.jobs.find_one({'_id':'job'}))['stage'],'review_uncertain')
        self.assertEqual(await review_delivery.run_once(self.jobs,self.candidates,self.client,self.bot,123),'idle')
        self.bot.send_video.assert_awaited_once()

    async def test_no_send_on_failed_download_or_oversize(self):
        self.client.download.side_effect=OSError('fail')
        self.assertEqual(await review_delivery.run_once(self.jobs,self.candidates,self.client,self.bot,123),'review_download_failed')
        self.bot.send_video.assert_not_awaited()
        await self.jobs.update_one({'_id':'job'},{'$set':{'stage':'ready_for_review'}})
        self.client.download.side_effect=None; self.client.download.return_value=(review_delivery.MAX_BOT_VIDEO_BYTES+1,'x')
        self.assertEqual(await review_delivery.run_once(self.jobs,self.candidates,self.client,self.bot,123),'review_too_large')
        self.bot.send_video.assert_not_awaited()

    async def test_invalid_owner_rejected_before_claim(self):
        with self.assertRaises(ValueError):
            await review_delivery.run_once(self.jobs,self.candidates,self.client,self.bot,True)
        self.assertEqual((await self.jobs.find_one({'_id':'job'}))['stage'],'ready_for_review')


if __name__ == '__main__': unittest.main()
