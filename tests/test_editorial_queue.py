import unittest
from unittest.mock import AsyncMock
try:
    from mongomock_motor import AsyncMongoMockClient
except ImportError:
    AsyncMongoMockClient = None
from news_prototype import editorial_queue


@unittest.skipIf(AsyncMongoMockClient is None, 'mongomock-motor optional')
class EditorialQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db=AsyncMongoMockClient().test; self.candidates=db.candidates; self.jobs=db.jobs
        self.record={'_id':'item-1','status':'candidate','title':'Заголовок',
                     'summary':'Компания сообщила важную новость. ' * 4,'collected_at':'2026-01-01'}
        self.result={'decision':'draft','verification':'needs_review','evidence':[],
                     'script':{'turns':[{'speaker':'Бендер','text':'Новость'},
                                        {'speaker':'Киберсенсей','text':'Комментарий'}]}}

    async def test_saves_draft_before_render_admission(self):
        await self.candidates.insert_one(self.record)
        draft=AsyncMock(return_value=self.result)
        self.assertEqual(await editorial_queue.run_once(self.candidates,self.jobs,draft),'draft_ready')
        self.assertEqual(await self.jobs.count_documents({}),0)
        item=await self.candidates.find_one({'_id':'item-1'})
        self.assertEqual(item['verification'],'needs_review')
        self.assertEqual(await editorial_queue.run_once(self.candidates,self.jobs,draft),'render_queued')
        self.assertEqual(await self.jobs.count_documents({}),1)
        self.assertEqual(draft.await_count,1)

    async def test_interrupted_edit_not_replayed(self):
        await self.candidates.insert_one(self.record | {'status':'editing'})
        draft=AsyncMock(return_value=self.result)
        self.assertEqual(await editorial_queue.run_once(self.candidates,self.jobs,draft),'idle')
        draft.assert_not_awaited()

    async def test_url_without_body_requires_more_data(self):
        await self.candidates.insert_one({'_id':'x','status':'candidate','url':'https://example.com','collected_at':'2026'})
        draft=AsyncMock()
        self.assertEqual(await editorial_queue.run_once(self.candidates,self.jobs,draft),'needs_source')
        draft.assert_not_awaited()

    async def test_manual_url_fetch_supplies_evidence_without_losing_accent(self):
        await self.candidates.insert_one({'_id':'manual:1:2','status':'candidate','source_id':'manual',
                                          'url':'https://example.com/a','accent':'Сроки','collected_at':'2026'})
        fetch=AsyncMock(return_value={'title':'Новость', 'summary':'Событие произошло. ' * 8,
                                      'resolved_url':'https://example.com/a'})
        draft=AsyncMock(return_value=self.result)
        result=await editorial_queue.run_once(self.candidates,self.jobs,draft,fetch_article=fetch)
        self.assertEqual(result,'draft_ready'); fetch.assert_awaited_once_with('https://example.com/a')
        self.assertEqual(draft.await_args.args[0]['accent'],'Сроки')
        saved=await self.candidates.find_one({'_id':'manual:1:2'})
        self.assertIn('Событие',saved['summary']); self.assertEqual(saved['verification'],'needs_review')

    async def test_failed_manual_fetch_requires_more_source(self):
        await self.candidates.insert_one({'_id':'x','status':'candidate','source_id':'manual',
                                          'url':'https://example.com/a','collected_at':'2026'})
        fetch=AsyncMock(side_effect=RuntimeError('unavailable')); draft=AsyncMock()
        result=await editorial_queue.run_once(self.candidates,self.jobs,draft,fetch_article=fetch)
        self.assertEqual(result,'needs_source'); draft.assert_not_awaited()

    async def test_failure_never_retries_automatically(self):
        await self.candidates.insert_one(self.record)
        draft=AsyncMock(side_effect=RuntimeError('private data'))
        self.assertEqual(await editorial_queue.run_once(self.candidates,self.jobs,draft),'editor_failed')
        self.assertEqual(await editorial_queue.run_once(self.candidates,self.jobs,draft),'idle')
        self.assertEqual(draft.await_count,1)
        self.assertEqual(await self.jobs.count_documents({}),0)


if __name__ == '__main__': unittest.main()
