import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
from mongomock_motor import AsyncMongoMockClient
from news_prototype import owner_discovery as discovery, news_sources
from tests.test_editor_client import Response, Session

NOW = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=1)
HTML = b'''<html><body>
<div class="tgme_widget_message" data-post="SenseiDurova/42">
 <div class="tgme_widget_message_reply"><div class="tgme_widget_message_text js-message_text">Wrong quoted text</div><time datetime="2024-01-01T00:00:00Z"></time></div>
 <div class="tgme_widget_message_text js-message_text"><b>AI</b> news<br>Text &amp; context</div>
 <a class="tgme_widget_message_date"><time datetime="2026-01-02T11:00:00Z">date</time></a>
</div>
<div class="tgme_widget_message" data-post="OtherChannel/43"><div class="tgme_widget_message_text js-message_text">Other channel</div><time datetime="2026-01-02T11:00:00Z"></time></div>
<div class="tgme_widget_message" data-post="SenseiDurova/44"><div class="tgme_widget_message_text js-message_text">Old</div><time datetime="2025-12-01T11:00:00Z"></time></div>
<div class="tgme_widget_message" data-post="SenseiDurova/45"><div class="tgme_widget_message_text js-message_text">Future</div><time datetime="2026-01-02T12:01:00Z"></time></div>
<div class="tgme_widget_message" data-post="SenseiDurova/46"><div class="tgme_widget_message_text js-message_text">Unknown date</div></div>
</body></html>'''
RSS = b'''<rss><channel><item><title>AI news</title><link>https://example.com/news</link><pubDate>Fri, 02 Jan 2026 11:00:00 +0000</pubDate></item></channel></rss>'''


class DiscoveryTests(unittest.TestCase):
    def test_channel_uses_post_text_and_timestamp_only(self):
        result = discovery.parse_channel(HTML, since=SINCE, now=NOW)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['url'], 'https://t.me/SenseiDurova/42')
        self.assertEqual(result[0]['title'], 'AI news Text & context')
        self.assertEqual(result[0]['published_at'], '2026-01-02T11:00:00+00:00')

    def test_rss_limits_can_be_increased_only_for_discovery(self):
        data = b'<rss><channel>' + b'<item/>' * 101 + b'</channel></rss>'
        with self.assertRaises(news_sources.FeedError):
            news_sources.parse_feed(data, 'openai', since=SINCE, now=NOW)
        self.assertEqual(news_sources.parse_feed(data, 'openai', since=SINCE, now=NOW, max_items=1000), [])
        with self.assertRaises(news_sources.FeedError):
            news_sources.parse_feed(data, 'openai', since=SINCE, now=NOW, max_items=5001)

    def test_channel_bound_and_invalid_dates(self):
        with self.assertRaises(ValueError): discovery.parse_channel(b'a' * (discovery.MAX_BYTES + 1), since=SINCE, now=NOW)
        with self.assertRaises(ValueError): discovery.parse_channel(b'<div/>', since=SINCE, now=NOW)

    def test_selection_validation(self):
        snap = {'items': [{'url': 'https://example.com/a'}, {'url': 'https://example.com/b'}]}
        self.assertEqual(discovery.choose(snap, [2, 1]), ['https://example.com/b', 'https://example.com/a'])
        for indices in ([1], [1, 1], [0, 1], [1, 3], [True, 1]):
            with self.subTest(indices=indices), self.assertRaises(ValueError): discovery.choose(snap, indices)
        with self.assertRaises(ValueError): discovery.choose(None, [1, 2])


class AsyncDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test

    async def test_all_sources_parallel_partial_failures_and_caps(self):
        started = []
        ready = asyncio.Event()
        async def fetch(session, url):
            started.append(url)
            if len(started) == 5:
                ready.set()
            await asyncio.wait_for(ready.wait(), 1)
            if url == discovery.SOURCES['telegram'][1]: return HTML
            if url == discovery.SOURCES['decrypt'][1]: raise ValueError('offline')
            return RSS
        with patch.object(discovery, '_fetch', side_effect=fetch):
            items, failed = await discovery.collect(now=NOW)
        self.assertEqual(len(started), 5)
        self.assertEqual(failed, ['Decrypt'])
        self.assertEqual({item['url'] for item in items}, {'https://t.me/SenseiDurova/42', 'https://example.com/news'})

    async def test_snapshot_scoped_immutable_and_number_order(self):
        items = [{'url': 'https://example.com/a'}, {'url': 'https://example.com/b'}]
        saved = await discovery.save_snapshot(self.db, 123, 42, items, [])
        again = await discovery.save_snapshot(self.db, 123, 42, list(reversed(items)), ['offline'])
        self.assertEqual(saved['items'], again['items'])
        self.assertIsNone(await discovery.get_snapshot(self.db, 999, 42))
        self.assertEqual(discovery.choose(again, [2, 1]), ['https://example.com/b', 'https://example.com/a'])

    async def test_fetch_follows_only_same_host_redirects_and_bounds_body(self):
        class FakeResponse:
            def __init__(self, status, data=b'', location=None):
                self.status = status
                self.headers = {'Location': location} if location else {}
                self.content = SimpleNamespace(iter_chunked=self.chunks)
                self.data = data
            async def chunks(self, _):
                yield self.data
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        session = SimpleNamespace(get=unittest.mock.Mock(side_effect=[
            FakeResponse(302, location='/new'), FakeResponse(200, data=b'ok')]))
        self.assertEqual(await discovery._fetch(session, 'https://example.com/rss'), b'ok')
        self.assertEqual(session.get.call_count, 2)
        session = SimpleNamespace(get=unittest.mock.Mock(return_value=FakeResponse(302, location='https://other.example/x')))
        with self.assertRaises(ValueError): await discovery._fetch(session, 'https://example.com/rss')
        self.assertEqual(session.get.call_count, 1)
        session = SimpleNamespace(get=unittest.mock.Mock(return_value=FakeResponse(200, data=b'x' * (discovery.MAX_BYTES + 1))))
        with self.assertRaises(ValueError): await discovery._fetch(session, 'https://example.com/rss')

    async def test_one_translation_request_and_invalid_output_fallback(self):
        items = [{'title': 'AI news'}, {'title': 'Крипта'}]
        def response(data):
            body = {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(data, ensure_ascii=False)}]}}]}
            return Response(200, json.dumps(body, ensure_ascii=False).encode())
        calls = []
        session = Session(response({'titles':['Новости ИИ', 'Крипта']}), calls)
        with patch.object(discovery.aiohttp, 'ClientSession', return_value=session):
            self.assertEqual(await discovery.translate_titles(items, 'secret', 'gemini-test'), ['Новости ИИ', 'Крипта'])
        self.assertEqual(len(calls), 1)
        self.assertNotIn('secret', calls[0][0])
        session = Session(response({'titles':['Only one']}), [])
        with patch.object(discovery.aiohttp, 'ClientSession', return_value=session):
            self.assertIsNone(await discovery.translate_titles(items, 'secret', 'gemini-test'))

    async def test_selection_runs_real_draft_flow_once_and_requires_explicit_approval(self):
        from handlers import news_owner_drafts as handler
        from news_prototype import owner_drafts, owner_program
        from tests.test_owner_drafts import SOURCE, DRAFT
        items = [{'url':'https://t.me/SenseiDurova/1'}, {'url':'https://example.com/a'}]
        await discovery.save_snapshot(self.db, 123, 51, items, [])
        msg = SimpleNamespace(from_user=SimpleNamespace(id=123), chat=SimpleNamespace(type='private'),
                              text='/news_pick 51 2 1', message_id=52, answer=AsyncMock())
        fetch = AsyncMock(return_value={'summary': SOURCE})
        editor = AsyncMock(return_value=DRAFT)
        with patch.object(handler, 'ADMIN_ID', 123), patch.object(handler.owner_trial, 'drafts_enabled', AsyncMock(return_value=True)), \
             patch.object(handler, 'db', SimpleNamespace(db=self.db)), \
             patch.object(handler.article_fetch, 'fetch_article', fetch), \
             patch.object(handler, 'make_draft', editor), \
             patch.object(handler, 'show', new_callable=AsyncMock):
            await handler.pick(msg)
            await handler.pick(msg)
        self.assertEqual(fetch.await_count, 2)
        self.assertEqual(editor.await_count, 2)
        ids = owner_drafts.batch_draft_ids(52, 2)
        drafts = [await owner_drafts.get(self.db, 123, value) for value in ids]
        self.assertEqual([d['url'] for d in drafts], ['https://example.com/a','https://t.me/SenseiDurova/1'])
        self.assertEqual([d['stage'] for d in drafts], ['draft_ready', 'draft_ready'])
        await owner_program.create(self.db, 123, 60, ids)
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_owner_discover_then_pick_creates_no_video(self):
        from handlers import news_owner_drafts as handler
        items = [{'source':'Crypto Sensei', 'title':'Новость один', 'url':'https://t.me/SenseiDurova/1', 'published_at':NOW.isoformat()},
                 {'source':'OpenAI', 'title':'AI update', 'url':'https://openai.com/example', 'published_at':NOW.isoformat()}]
        msg = SimpleNamespace(from_user=SimpleNamespace(id=123), chat=SimpleNamespace(type='private'),
                              text='/news_discover', message_id=51, answer=AsyncMock())
        with patch.object(handler, 'ADMIN_ID', 123), patch.object(handler.owner_trial, 'drafts_enabled', AsyncMock(return_value=True)), \
             patch.object(handler, 'db', SimpleNamespace(db=self.db)), \
             patch.object(discovery, 'collect', new=AsyncMock(return_value=(items, ['Decrypt']))), \
             patch.object(discovery, 'translate_titles', new=AsyncMock(return_value=['Новость один','ИИ обновление'])):
            await handler.discover(msg)
        text = '\n'.join(call.args[0] for call in msg.answer.await_args_list)
        self.assertIn('Decrypt', text); self.assertIn('ИИ обновление', text)
        # The picker is a screen: the text points at the panel buttons instead
        # of printing a command with the batch number and indexes.
        self.assertIn('подборки №51 кнопками', text)
        self.assertNotIn('/news_pick', text)
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
        pick_msg = SimpleNamespace(from_user=SimpleNamespace(id=123), chat=SimpleNamespace(type='private'),
                                   text='/news_pick 51 2 1', message_id=52, answer=AsyncMock())
        with patch.object(handler, 'ADMIN_ID', 123), patch.object(handler.owner_trial, 'drafts_enabled', AsyncMock(return_value=True)), \
             patch.object(handler, 'db', SimpleNamespace(db=self.db)), \
             patch.object(handler, 'prepare_urls', new_callable=AsyncMock) as prepare:
            await handler.pick(pick_msg)
        self.assertEqual(prepare.await_args.args[1], ['https://openai.com/example', 'https://t.me/SenseiDurova/1'])
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
        pick_msg.from_user.id = 999
        with patch.object(handler, 'ADMIN_ID', 999), patch.object(handler.owner_trial, 'drafts_enabled', AsyncMock(return_value=True)), \
             patch.object(handler, 'db', SimpleNamespace(db=self.db)), \
             patch.object(handler, 'prepare_urls', new_callable=AsyncMock) as prepare:
            await handler.pick(pick_msg)
        prepare.assert_not_awaited()


if __name__ == '__main__': unittest.main()
