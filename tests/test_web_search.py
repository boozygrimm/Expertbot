import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from handlers import web_search


class WebSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_main_search_success_skips_fallback_and_includes_sources(self):
        result = [{'title': 'Марс', 'body': 'Планета', 'href': 'https://example.org/mars'}]
        with patch.object(web_search, '_fetch_ddgs_raw', return_value=result), \
             patch.object(web_search, '_fetch_wikipedia', new_callable=AsyncMock) as wiki, \
             patch.object(web_search, 'generate_with_retry', new=AsyncMock(return_value='STATUS: OK\nМарс — планета.')):
            answer = await web_search.smart_web_search('Марс')
        wiki.assert_not_awaited()
        self.assertIn('Марс — планета', answer)
        self.assertIn('https://example.org/mars', answer)
        self.assertNotIn('Википедии', answer)

    async def test_failure_uses_labelled_encyclopedia_fallback(self):
        result = [{'title': 'Пифагор', 'body': 'Философ', 'href': 'https://ru.wikipedia.org/?curid=1'}]
        with patch.object(web_search, '_fetch_ddgs_raw', side_effect=OSError('network')), \
             patch.object(web_search, '_fetch_wikipedia', new=AsyncMock(return_value=result)) as wiki, \
             patch.object(web_search, 'generate_with_retry', new=AsyncMock(return_value='STATUS: OK\nПифагор — философ.')):
            answer = await web_search.smart_web_search('Пифагор')
        wiki.assert_awaited_once_with('Пифагор')
        self.assertIn('Википедии', answer)
        self.assertIn('не поиск актуальных новостей', answer)
        self.assertIn('https://ru.wikipedia.org/?curid=1', answer)

    async def test_primary_empty_uses_fallback_too(self):
        with patch.object(web_search, '_fetch_ddgs_raw', return_value=[]), \
             patch.object(web_search, '_fetch_wikipedia', new=AsyncMock(return_value=[{'title': 'A', 'body': 'B'}])) as wiki, \
             patch.object(web_search, 'generate_with_retry', new=AsyncMock(return_value='STATUS: OK\nОтвет')):
            answer = await web_search.smart_web_search('тест')
        wiki.assert_awaited_once()
        self.assertIn('Википедии', answer)

    async def test_both_services_fail_without_claiming_no_results(self):
        with patch.object(web_search, '_fetch_ddgs_raw', side_effect=OSError()), \
             patch.object(web_search, '_fetch_wikipedia', new=AsyncMock(side_effect=OSError())), \
             patch.object(web_search, 'generate_with_retry', new_callable=AsyncMock) as ai:
            answer = await web_search.smart_web_search('тест')
        self.assertIn('недоступен', answer)
        ai.assert_not_awaited()

    async def test_failed_primary_and_empty_wikipedia_are_distinct(self):
        with patch.object(web_search, '_fetch_ddgs_raw', side_effect=OSError()), \
             patch.object(web_search, '_fetch_wikipedia', new=AsyncMock(return_value=[])):
            answer = await web_search.smart_web_search('тест')
        self.assertIn('недоступен', answer)
        self.assertIn('Википедии ничего не нашлось', answer)

    async def test_ai_failure_returns_search_fragments(self):
        with patch.object(web_search, '_fetch_ddgs_raw', return_value=[{'title': 'Марс', 'body': 'Планета'}]), \
             patch.object(web_search, 'generate_with_retry', new=AsyncMock(return_value=None)):
            answer = await web_search.smart_web_search('Марс')
        self.assertIn('Планета', answer)
        self.assertIn('Не удалось проверить', answer)

    async def test_output_capped(self):
        result = [{'title': 'A', 'body': 'B', 'href': 'https://example.org/' + 'x' * 300}] * 5
        with patch.object(web_search, '_fetch_ddgs_raw', return_value=result), \
             patch.object(web_search, 'generate_with_retry', new=AsyncMock(return_value='STATUS: OK\n' + 'x' * 10000)):
            answer = await web_search.smart_web_search('тест')
        self.assertLessEqual(len(answer), 4096)

    async def test_irrelevant_primary_uses_wikipedia_not_unrelated_sources(self):
        unrelated = [{'title': 'Tankstelle', 'body': 'Benzinpreise',
                      'href': 'https://de.wikipedia.org/wiki/Tankstelle'}]
        wiki_results = [{'title': 'Пифагор', 'body': 'Древнегреческий философ',
                         'href': 'https://ru.wikipedia.org/?curid=1'}]
        with patch.object(web_search, '_fetch_ddgs_raw', return_value=unrelated), \
             patch.object(web_search, '_fetch_wikipedia', new=AsyncMock(return_value=wiki_results)) as wiki, \
             patch.object(web_search, 'generate_with_retry', new=AsyncMock(
                 side_effect=['STATUS: NO', 'STATUS: OK\nПифагор — философ.'])) as ai:
            answer = await web_search.smart_web_search('Пифагор')
        wiki.assert_awaited_once_with('Пифагор')
        self.assertEqual(ai.await_count, 2)
        self.assertIn('Пифагор — философ', answer)
        self.assertIn('Википедии', answer)
        self.assertNotIn('Tankstelle', answer)
        self.assertIn('https://ru.wikipedia.org/?curid=1', answer)

    async def test_irrelevant_results_and_wikipedia_failure(self):
        with patch.object(web_search, '_fetch_ddgs_raw', return_value=[{'title': 'unrelated'}]), \
             patch.object(web_search, '_fetch_wikipedia', new=AsyncMock(side_effect=OSError())), \
             patch.object(web_search, 'generate_with_retry', new=AsyncMock(return_value='STATUS: NO')):
            answer = await web_search.smart_web_search('Пифагор')
        self.assertIn('не дал подходящих', answer)
        self.assertIn('недоступен', answer)

    async def test_irrelevant_wikipedia_not_returned(self):
        with patch.object(web_search, '_fetch_ddgs_raw', return_value=[]), \
             patch.object(web_search, '_fetch_wikipedia', new=AsyncMock(return_value=[{'title': 'Несвязанный'}])), \
             patch.object(web_search, 'generate_with_retry', new=AsyncMock(return_value='STATUS: NO')):
            answer = await web_search.smart_web_search('Пифагор')
        self.assertIn('не нашлось', answer)
        self.assertNotIn('Несвязанный', answer)

    async def test_malformed_ai_output_is_not_treated_as_relevant(self):
        with patch.object(web_search, '_fetch_ddgs_raw', return_value=[{'title': 'Tankstelle', 'body': 'Бензин'}]), \
             patch.object(web_search, '_fetch_wikipedia', new_callable=AsyncMock) as wiki, \
             patch.object(web_search, 'generate_with_retry', new=AsyncMock(return_value='Что-то без метки')):
            answer = await web_search.smart_web_search('Пифагор')
        wiki.assert_not_awaited()
        self.assertIn('могут быть не по теме', answer)
        self.assertNotIn('Что-то без метки', answer)

    async def test_wikipedia_api_parameters_and_snippet(self):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = AsyncMock(return_value={'query': {'search': [
            {'pageid': 123, 'title': 'Марс', 'snippet': '<span class="searchmatch">Марс</span> &mdash; планета'},
            {'title': 'no id', 'snippet': ''},
        ]}})
        request = MagicMock()
        request.__aenter__ = AsyncMock(return_value=response)
        request.__aexit__ = AsyncMock(return_value=None)
        session = MagicMock()
        session.get.return_value = request
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=session)
        session_cm.__aexit__ = AsyncMock(return_value=None)
        with patch.object(web_search.aiohttp, 'ClientSession', return_value=session_cm) as factory:
            results = await web_search._fetch_wikipedia('Марс')
        self.assertEqual(results, [{'title': 'Марс', 'body': 'Марс — планета',
                                    'href': 'https://ru.wikipedia.org/?curid=123'}])
        self.assertEqual(session.get.call_args.kwargs['params']['srsearch'], 'Марс')
        self.assertIn('User-Agent', factory.call_args.kwargs['headers'])


if __name__ == '__main__':
    unittest.main()
