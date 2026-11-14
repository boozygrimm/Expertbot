import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from handlers import search_api, web_search


def mocked_session(data, status=200):
    response = MagicMock(status=status)
    response.json = AsyncMock(return_value=data)
    request = MagicMock()
    request.__aenter__ = AsyncMock(return_value=response)
    request.__aexit__ = AsyncMock(return_value=None)
    session = MagicMock()
    session.get.return_value = request
    session.post.return_value = request
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm, session


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_tavily_uses_basic_and_normalizes_sources(self):
        date = datetime.now(timezone.utc).isoformat()
        cm, session = mocked_session({'results': [{'title': 'Космос', 'content': 'Данные',
                                                    'url': 'https://example.org/a', 'published_date': date}]})
        with patch.dict(os.environ, {'TAVILY_API_KEY': 'secret'}), \
             patch.object(search_api.aiohttp, 'ClientSession', return_value=cm):
            result = await search_api.fetch_tavily('новости', news=True, days=30)
        payload = session.post.call_args.kwargs['json']
        self.assertEqual((payload['search_depth'], payload['topic'], payload['time_range']), ('basic', 'news', 'month'))
        self.assertFalse(payload['auto_parameters'])
        self.assertTrue(payload['filter_by_published_date'])
        self.assertFalse(session.post.call_args.kwargs['allow_redirects'])
        self.assertEqual(result[0]['href'], 'https://example.org/a')
        self.assertTrue(result[0]['date'])

    async def test_firecrawl_keyless_result_shapes(self):
        for news, data in [(False, {'success': True, 'data': {'web': [
                                {'title': 'Марс', 'url': 'https://example.org/mars', 'description': 'планета'}]}}),
                           (True, {'success': True, 'data': {'news': [
                                {'title': 'Уэбб', 'url': 'https://example.org/news',
                                 'snippet': 'Открытие', 'date': '12 hours ago'}]}})]:
            with self.subTest(news=news):
                cm, session = mocked_session(data)
                with patch.object(search_api.aiohttp, 'ClientSession', return_value=cm):
                    result = await search_api.fetch_firecrawl('тема', news=news, days=30 if news else None)
                self.assertEqual(len(result), 1)
                kwargs = session.post.call_args.kwargs
                self.assertEqual(kwargs['json']['sources'], [{'type': 'news' if news else 'web'}])
                self.assertNotIn('headers', kwargs)  # keyless, no authorization
                self.assertNotIn('scrapeOptions', kwargs)
                self.assertNotIn('tbs', kwargs['json'])  # news time filter is local
                self.assertFalse(kwargs['allow_redirects'])
                if news:
                    self.assertEqual(result[0]['date'], '12 hours ago')

    async def test_firecrawl_news_filters_old_missing_and_relative_boundary(self):
        cm, _ = mocked_session({'success': True, 'data': {'news': [
            {'title': 'старое', 'url': 'https://example.org/old', 'date': '31 days ago'},
            {'title': 'нет даты', 'url': 'https://example.org/unknown'},
            {'title': 'свежее', 'url': 'https://example.org/new', 'date': '12 hours ago'},
        ]}})
        with patch.object(search_api.aiohttp, 'ClientSession', return_value=cm):
            result = await search_api.fetch_firecrawl('новости', news=True, days=30)
        self.assertEqual([r['title'] for r in result], ['свежее'])

    async def test_firecrawl_provider_rejects_errors(self):
        for status, data in [(429, {}), (200, {'success': False}), (200, {'success': True, 'data': {'news': 'bad'}})]:
            with self.subTest(status=status, data=data):
                cm, _ = mocked_session(data, status=status)
                with patch.object(search_api.aiohttp, 'ClientSession', return_value=cm):
                    with self.assertRaises(search_api.SearchProviderError):
                        await search_api.fetch_firecrawl('новости', news=True, days=30)

    async def test_news_discards_undated_stale_and_future_items(self):
        now = datetime.now(timezone.utc)
        items = [{'title': str(i), 'href': f'https://example.org/{i}', 'date': date} for i, date in enumerate([
            None, 'bad date', (now-timedelta(days=31)).isoformat(),
            (now+timedelta(days=3)).isoformat(), (now-timedelta(days=3)).isoformat()])]
        results = search_api._normalize(items, days=30)
        self.assertEqual([x['title'] for x in results], ['4'])
        self.assertEqual(len(search_api._normalize(items, days=None)), 5)

    def test_mode(self):
        self.assertEqual(search_api.search_mode('последние новости телескопа'), (True, 30))
        self.assertEqual(search_api.search_mode('новости сегодня'), (True, 1))
        self.assertEqual(search_api.search_mode('история Пифагора'), (False, None))
        self.assertEqual(search_api.search_mode('история новостей'), (True, None))


class SearchFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.keys = patch.dict(os.environ, {'TAVILY_API_KEY': 't'})
        self.keys.start()
        self.addCleanup(self.keys.stop)
        self.item = [{'title': 'Пифагор', 'body': 'Философ', 'href': 'https://example.org/p', 'date': ''}]

    async def test_primary_success_never_calls_backup(self):
        with patch.object(search_api, 'fetch_tavily', new=AsyncMock(return_value=self.item)) as primary, \
             patch.object(search_api, 'fetch_firecrawl', new_callable=AsyncMock) as backup, \
             patch.object(web_search, 'generate_with_retry', new=AsyncMock(return_value='STATUS: OK\nПифагор — философ.')):
            answer = await web_search.smart_web_search('Пифагор')
        primary.assert_awaited_once()
        backup.assert_not_awaited()
        self.assertIn('Пифагор — философ.', answer)
        self.assertIn('https://example.org/p', answer)

    async def test_bender_persona_request_keeps_facts_dates_and_real_sources(self):
        result = [{'title': 'Телескоп', 'body': 'Факт о космосе',
                   'href': 'https://example.org/news', 'date': '2026-11-12'}]
        ai = AsyncMock(return_value='STATUS: OK\nКожаные мешки, вот вам факт [1]!')
        with patch.object(search_api, 'fetch_tavily', new=AsyncMock(return_value=result)), \
             patch.object(search_api, 'fetch_firecrawl', new_callable=AsyncMock) as backup, \
             patch.object(web_search, 'generate_with_retry', new=ai):
            answer = await web_search.smart_web_search('новости телескопа')
        prompt = ai.await_args.args[0]
        self.assertIn('Бендер Сгибальщик Родригес', prompt)
        self.assertIn('на протяжении всего ответа', prompt)
        self.assertIn('Не выдумывай факты или даты', prompt)
        self.assertIn('Факт о космосе', prompt)
        self.assertIn('2026-11-12', prompt)
        self.assertTrue(answer.startswith('🤖 Бендер:'))
        self.assertIn('Кожаные мешки', answer)
        self.assertIn('https://example.org/news — 2026-11-12', answer)
        self.assertNotIn('STATUS:', answer)
        backup.assert_not_awaited()

    async def test_primary_error_empty_and_irrelevant_trigger_backup(self):
        for case in ['error', 'empty', 'irrelevant']:
            with self.subTest(case=case):
                primary = AsyncMock(side_effect=OSError('secret URL')) if case == 'error' else AsyncMock(
                    return_value=self.item if case == 'irrelevant' else [])
                answers = ['STATUS: NO', 'STATUS: OK\nПравильно'] if case == 'irrelevant' else ['STATUS: OK\nПравильно']
                with patch.object(search_api, 'fetch_tavily', new=primary), \
                     patch.object(search_api, 'fetch_firecrawl', new=AsyncMock(return_value=self.item)) as backup, \
                     patch.object(web_search, 'generate_with_retry', new=AsyncMock(side_effect=answers)):
                    answer = await web_search.smart_web_search('Пифагор')
                backup.assert_awaited_once()
                self.assertIn('Правильно', answer)

    async def test_ai_unavailable_does_not_spend_backup_quota(self):
        with patch.object(search_api, 'fetch_tavily', new=AsyncMock(return_value=self.item)), \
             patch.object(search_api, 'fetch_firecrawl', new_callable=AsyncMock) as backup, \
             patch.object(web_search, 'generate_with_retry', new=AsyncMock(return_value=None)):
            answer = await web_search.smart_web_search('Пифагор')
        backup.assert_not_awaited()
        self.assertIn('могут быть не по теме', answer)

    async def test_fresh_news_no_old_wikipedia_fallback(self):
        with patch.object(search_api, 'fetch_tavily', new=AsyncMock(return_value=[])) as first, \
             patch.object(search_api, 'fetch_firecrawl', new=AsyncMock(return_value=[])) as second, \
             patch.object(web_search, '_fetch_wikipedia', new_callable=AsyncMock) as wiki:
            answer = await web_search.smart_web_search('последние новости Уэбб')
        first.assert_awaited_once_with('последние новости Уэбб', news=True, days=30)
        second.assert_awaited_once_with('последние новости Уэбб', news=True, days=30)
        wiki.assert_not_awaited()
        self.assertIn('свежих новостей', answer)

    async def test_both_failed_and_one_failed_are_distinct(self):
        with patch.object(search_api, 'fetch_tavily', new=AsyncMock(side_effect=OSError())), \
             patch.object(search_api, 'fetch_firecrawl', new=AsyncMock(side_effect=OSError())):
            answer = await web_search.smart_web_search('тест')
        self.assertIn('недоступны', answer)
        with patch.object(search_api, 'fetch_tavily', new=AsyncMock(return_value=[])), \
             patch.object(search_api, 'fetch_firecrawl', new=AsyncMock(side_effect=OSError())):
            answer = await web_search.smart_web_search('тест')
        self.assertIn('один из сервисов', answer)

    async def test_no_keys_uses_legacy_but_not_for_current_news(self):
        with patch.dict(os.environ, {'TAVILY_API_KEY': ''}), \
             patch.object(web_search, '_fetch_ddgs_raw') as old, \
             patch.object(web_search, '_fetch_wikipedia', new_callable=AsyncMock) as wiki:
            answer = await web_search.smart_web_search('последние новости Уэбб')
        self.assertIn('не могу надёжно', answer)
        old.assert_not_called()
        wiki.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
