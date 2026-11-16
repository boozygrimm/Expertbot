from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from news_prototype import collect_news, news_sources

NOW = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)


class FakeCollection:
    def __init__(self): self.docs = {}; self.fail = False
    async def find_one(self, query): return self.docs.get(query['_id'])
    async def update_one(self, query, update, upsert=False):
        if self.fail: raise RuntimeError('secret')
        key = query['_id']
        fresh = key not in self.docs
        if fresh: self.docs[key] = {'_id': key}
        if '$setOnInsert' in update and fresh: self.docs[key].update(update['$setOnInsert'])
        if '$max' in update:
            for name, value in update['$max'].items():
                if name not in self.docs[key] or value > self.docs[key][name]: self.docs[key][name] = value
        return SimpleNamespace(upserted_id=key if fresh else None)


class CollectNewsTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_idempotency_and_overlap(self):
        candidates, cursors = FakeCollection(), FakeCollection()
        calls = []
        def fetch(source, *, since, now):
            calls.append((source, since))
            return [{'id':'abc', 'source_id': source, 'status':'candidate'}]
        with patch.object(news_sources, 'FEEDS', {'one':('One', 'https://example.com/feed')}):
            first = await collect_news.collect_once(candidates,cursors,now=NOW,fetch=fetch)
            second = await collect_news.collect_once(candidates,cursors,now=NOW+timedelta(minutes=15),fetch=fetch)
        self.assertEqual(first['one']['inserted'],1)
        self.assertEqual(second['one']['inserted'],0)
        self.assertEqual(calls[0][1],NOW-timedelta(hours=2))
        self.assertEqual(calls[1][1],NOW-timedelta(hours=2))
        self.assertEqual(len(candidates.docs),1)

    async def test_failed_save_never_advances_cursor(self):
        candidates, cursors = FakeCollection(), FakeCollection(); candidates.fail = True
        def fetch(source, *, since, now): return [{'id':'abc', 'source_id':source}]
        with patch.object(news_sources, 'FEEDS', {'one':('One', 'https://example.com/feed')}):
            result = await collect_news.collect_once(candidates,cursors,now=NOW,fetch=fetch)
        self.assertFalse(result['one']['ok']); self.assertFalse(cursors.docs)

    async def test_failure_isolated_per_source(self):
        candidates, cursors = FakeCollection(), FakeCollection()
        def fetch(source, *, since, now):
            if source == 'bad': raise news_sources.FeedError('unavailable')
            return []
        with patch.object(news_sources, 'FEEDS', {'bad':('Bad','x'), 'good':('Good','x')}):
            result = await collect_news.collect_once(candidates,cursors,now=NOW,fetch=fetch)
        self.assertFalse(result['bad']['ok']); self.assertTrue(result['good']['ok'])
        self.assertNotIn('bad',cursors.docs); self.assertIn('good',cursors.docs)


if __name__ == '__main__': unittest.main()
