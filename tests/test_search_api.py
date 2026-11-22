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

    def test_mode_reads_the_requested_period(self):
        for query, expected in [
            ('новости за сутки', 1), ('новости за последние сутки', 1),
            ('новости за 24 часа', 1), ('новости за последний день', 1),
            ('новости вчера', 1), ('news for the last 24 hours', 1),
            ('новости за неделю', 7), ('новости за 7 дней', 7),
            ('новости за месяц', 30), ('новости bitcoin', 30),
        ]:
            with self.subTest(query=query):
                self.assertEqual(search_api.search_mode(query), (True, expected))

    def test_provider_query_keeps_only_the_subject(self):
        # The window is a provider parameter, so the wrapper words are noise.
        self.assertEqual(search_api.news_terms('новости за сутки включая курс bitcoin и gram'),
                         'курс bitcoin и gram')
        self.assertEqual(search_api.news_terms('последние новости телескопа Уэбб'), 'телескопа Уэбб')
        self.assertEqual(search_api.news_terms('bitcoin news for the last 24 hours'), 'bitcoin')
        # Nothing left but the wrapper: search it as the user wrote it.
        self.assertEqual(search_api.news_terms('новости за сутки'), 'новости за сутки')
        self.assertEqual(search_api.news_terms('новости'), 'новости')

    def test_date_formats_providers_return(self):
        for value in ['2026-11-21', '2026-11-21T10:00:00Z', 'Fri, 21 Nov 2026 12:00:00 GMT',
                      '21 Nov 2026', '21 November 2026', 'Nov 21, 2026', '21.11.2026',
                      '2026/11/21', '12 hours ago', 'an hour ago', 'yesterday', 'вчера',
                      1763712000]:
            with self.subTest(value=value):
                self.assertIsNotNone(search_api._date(value))
        for value in ['', '   ', 'bad date', None, True, [1], float('nan')]:
            with self.subTest(value=value):
                self.assertIsNone(search_api._date(value))

    def test_bare_date_covers_its_whole_day(self):
        now = datetime.now(timezone.utc)
        yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
        week_old = (now - timedelta(days=9)).strftime('%Y-%m-%d')
        items = [{'title': str(i), 'href': f'https://example.org/{i}', 'date': date}
                 for i, date in enumerate([yesterday, week_old])]
        # Read as midnight UTC, yesterday's article would fail a 24-hour window.
        self.assertEqual([r['title'] for r in search_api._normalize(items, days=1)], ['0'])
        self.assertEqual([r['title'] for r in search_api._normalize(items, days=30)], ['0', '1'])

    async def test_tavily_week_window(self):
        cm, session = mocked_session({'results': []})
        with patch.dict(os.environ, {'TAVILY_API_KEY': 'secret'}), \
             patch.object(search_api.aiohttp, 'ClientSession', return_value=cm):
            await search_api.fetch_tavily('новости', news=True, days=7)
        self.assertEqual(session.post.call_args.kwargs['json']['time_range'], 'week')

    def test_dropped_items_are_reported_in_the_log(self):
        now = datetime.now(timezone.utc)
        items = [{'title': 'без даты', 'href': 'https://example.org/1', 'date': ''},
                 {'title': 'не распознана', 'href': 'https://example.org/2', 'date': 'дата неизвестна'},
                 {'title': 'старое', 'href': 'https://example.org/3',
                  'date': (now - timedelta(days=40)).isoformat()}]
        with self.assertLogs('handlers.search_api', level='INFO') as logs:
            self.assertEqual(search_api._normalize(items, days=1, source='Tavily'), [])
        line = '\n'.join(logs.output)
        self.assertIn('Tavily', line)
        self.assertIn('без даты — 2', line)
        self.assertIn('вне окна — 1', line)
        self.assertIn('дат не распознано — 1', line)


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
        self.assertNotIn('https://example.org/p', answer)

    async def test_bender_persona_request_keeps_facts_and_dates_without_links(self):
        result = [{'title': 'Телескоп', 'body': 'Факт о космосе',
                   'href': 'https://example.org/news', 'date': '2026-11-12'}]
        ai = AsyncMock(return_value='STATUS: OK\nКожаные мешки, вот вам факт от 2026-11-12 [1]!')
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
        self.assertNotIn('https://example.org/news', answer)
        self.assertNotIn('[1]', answer)
        self.assertIn('2026-11-12', answer)
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
        # The provider gets the subject; «последние новости» is the wrapper the
        # topic and the date window already express.
        first.assert_awaited_once_with('Уэбб', news=True, days=30)
        second.assert_awaited_once_with('Уэбб', news=True, days=30)
        wiki.assert_not_awaited()
        self.assertIn('свежих новостей', answer)

    async def test_day_window_news_reaches_the_provider_as_the_subject(self):
        dated = [{'title': 'Bitcoin', 'body': 'Курс вырос', 'href': 'https://example.org/btc',
                  'date': '2026-11-22'}]
        with patch.object(search_api, 'fetch_tavily', new=AsyncMock(return_value=dated)) as first, \
             patch.object(search_api, 'fetch_firecrawl', new_callable=AsyncMock) as backup, \
             patch.object(web_search, 'generate_with_retry',
                          new=AsyncMock(return_value='STATUS: OK\nКурс bitcoin подрос.')):
            answer = await web_search.smart_web_search('новости за сутки включая курс bitcoin и gram')
        first.assert_awaited_once_with('курс bitcoin и gram', news=True, days=1)
        backup.assert_not_awaited()
        self.assertIn('Курс bitcoin подрос.', answer)

    async def test_partial_coverage_is_not_a_refusal(self):
        # A digest request is never fully answerable from five snippets; the gate
        # must reject off-topic results only, or every news search ends empty.
        dated = [{'title': 'Bitcoin', 'body': 'Курс вырос', 'href': 'https://example.org/btc',
                  'date': '2026-11-22'}]
        with patch.object(search_api, 'fetch_tavily', new=AsyncMock(return_value=dated)), \
             patch.object(search_api, 'fetch_firecrawl', new_callable=AsyncMock), \
             patch.object(web_search, 'generate_with_retry',
                          new=AsyncMock(return_value='STATUS: OK\nВот что есть.')) as ai:
            answer = await web_search.smart_web_search('новости за сутки')
        prompt = ai.await_args.args[0]
        self.assertIn('Неполнота отказом не считается', prompt)
        self.assertIn('STATUS: NO только если фрагменты не по теме', prompt)
        self.assertNotIn('или информации недостаточно', prompt)
        self.assertIn('Вот что есть.', answer)

    async def test_empty_and_rejected_searches_are_distinguishable_in_the_log(self):
        dated = [{'title': 'Bitcoin', 'body': 'Курс', 'href': 'https://example.org/btc',
                  'date': '2026-11-22'}]
        with patch.object(search_api, 'fetch_tavily', new=AsyncMock(return_value=[])), \
             patch.object(search_api, 'fetch_firecrawl', new=AsyncMock(return_value=[])), \
             self.assertLogs('handlers.web_search', level='INFO') as logs:
            await web_search.smart_web_search('новости за сутки')
        self.assertIn('ни одного подходящего результата', '\n'.join(logs.output))
        self.assertIn('Поиск без ответа', '\n'.join(logs.output))

        with patch.object(search_api, 'fetch_tavily', new=AsyncMock(return_value=dated)), \
             patch.object(search_api, 'fetch_firecrawl', new=AsyncMock(return_value=[])), \
             patch.object(web_search, 'generate_with_retry', new=AsyncMock(return_value='STATUS: NO')), \
             self.assertLogs('handlers.web_search', level='INFO') as logs:
            await web_search.smart_web_search('новости за сутки')
        joined = '\n'.join(logs.output)
        self.assertIn('Tavily: 1 результатов, оценка ИИ — irrelevant', joined)
        self.assertIn('найденное не подошло', joined)

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
