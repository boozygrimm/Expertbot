import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from handlers import ex_logic, search_api, web_search
from tests.test_search_api import mocked_session


class SearchImageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.results = [{'title': 'Марс', 'body': 'Планета', 'href': 'https://example.org/mars', 'date': ''}]
        self.candidates = [{'url': 'https://images.example.org/mars.jpg', 'description': 'Поверхность Марса'}]

    async def test_tavily_requests_images_in_same_basic_search(self):
        cm, session = mocked_session({'results': [{'title': 'Марс', 'url': 'https://example.org/mars'}],
                                      'images': self.candidates})
        with patch.dict(os.environ, {'TAVILY_API_KEY': 'secret'}), \
             patch.object(search_api.aiohttp, 'ClientSession', return_value=cm):
            results, images = await search_api.fetch_tavily('Марс', news=False, days=None, include_images=True)
        self.assertEqual(len(results), 1)
        self.assertEqual(images, self.candidates)
        payload = session.post.call_args.kwargs['json']
        self.assertEqual(payload['search_depth'], 'basic')
        self.assertFalse(payload['auto_parameters'])
        self.assertTrue(payload['include_images'])
        self.assertTrue(payload['include_image_descriptions'])
        self.assertTrue(payload['safe_search'])
        session.post.assert_called_once()

    def test_candidates_reject_untrusted_url_shapes_and_missing_descriptions(self):
        bad = ['http://example.org/a', 'https://127.0.0.1/a', 'https://169.254.169.254/a',
               'https://localhost/a', 'https://service.internal/a', 'https://foo.local/a',
               'https://u:p@example.org/a', 'https://example.org:8080/a',
               'https://example.org/a\nsecret', 'data:image/png,xx']
        data = {'images': [{'url': u, 'description': 'x'} for u in bad] +
                [{'url': 'https://example.org/a', 'description': ''},
                 {'url': 'https://example.org/ok.jpg', 'description': 'Марс'}]}
        self.assertEqual(search_api._image_candidates(data), [
            {'url': 'https://example.org/ok.jpg', 'description': 'Марс'}])
        self.assertEqual(search_api._image_candidates({'images': 'bad'}), [])

    async def test_selection_only_accepts_exact_index_and_hides_urls(self):
        for reply, expected in [('1', self.candidates[0]['url']), ('NONE', None), ('1 and more', None), ('5', None)]:
            with self.subTest(reply=reply), patch.object(web_search, 'generate_with_retry', new=AsyncMock(return_value=reply)) as ai:
                self.assertEqual(await web_search._select_image('Марс', 'Планета', self.candidates), expected)
                self.assertNotIn(self.candidates[0]['url'], ai.await_args.args[0])
        with patch.object(web_search, 'generate_with_retry', new=AsyncMock(side_effect=OSError('secret'))):
            self.assertIsNone(await web_search._select_image('Марс', 'Планета', self.candidates))

    async def test_primary_success_uses_same_results_and_image(self):
        with patch.dict(os.environ, {'TAVILY_API_KEY': 't'}), \
             patch.object(search_api, 'fetch_tavily', new=AsyncMock(return_value=(self.results, self.candidates))) as primary, \
             patch.object(search_api, 'fetch_firecrawl', new_callable=AsyncMock) as backup, \
             patch.object(web_search, '_evaluate_results', new=AsyncMock(return_value=('ok', 'Марс — планета.', 'context'))), \
             patch.object(web_search, '_select_image', new=AsyncMock(return_value=self.candidates[0]['url'])):
            text, image = await web_search.smart_web_search_with_image('Марс')
        self.assertIn('Марс — планета.', text)
        self.assertEqual(image, self.candidates[0]['url'])
        primary.assert_awaited_once_with('Марс', news=False, days=None, include_images=True)
        backup.assert_not_awaited()

    async def test_bad_primary_results_discard_its_images_and_backup_stays_text_only(self):
        with patch.dict(os.environ, {'TAVILY_API_KEY': 't'}), \
             patch.object(search_api, 'fetch_tavily', new=AsyncMock(return_value=(self.results, self.candidates))), \
             patch.object(search_api, 'fetch_firecrawl', new=AsyncMock(return_value=self.results)) as backup, \
             patch.object(web_search, '_evaluate_results', new=AsyncMock(side_effect=[('irrelevant', '', ''), ('ok', 'Ответ', '')])), \
             patch.object(web_search, '_select_image', new=AsyncMock(return_value=None)) as select:
            text, image = await web_search.smart_web_search_with_image('Марс')
        self.assertIn('Ответ', text)
        self.assertIsNone(image)
        backup.assert_awaited_once()
        self.assertEqual(select.await_args.args[2], [])

    async def test_unverified_text_has_no_image(self):
        with patch.dict(os.environ, {'TAVILY_API_KEY': 't'}), \
             patch.object(search_api, 'fetch_tavily', new=AsyncMock(return_value=(self.results, self.candidates))), \
             patch.object(web_search, '_evaluate_results', new=AsyncMock(return_value=('unknown', '', 'фрагмент'))), \
             patch.object(web_search, '_select_image', new_callable=AsyncMock) as select:
            text, image = await web_search.smart_web_search_with_image('Марс')
        self.assertIn('фрагмент', text)
        self.assertIsNone(image)
        select.assert_not_awaited()

    async def test_probe_is_private_admin_only_and_never_touches_accounting(self):
        message = SimpleNamespace(chat=SimpleNamespace(id=123, type='private'), from_user=SimpleNamespace(id=123),
                                  answer=AsyncMock(), answer_photo=AsyncMock())
        command = SimpleNamespace(args=None)
        with patch.object(web_search, 'ADMIN_ID', 123), patch.object(web_search, '_image_probe_lock', asyncio.Lock()), \
             patch.object(web_search, 'smart_web_search_with_image', new=AsyncMock(return_value=('answer', self.candidates[0]['url']))) as search:
            await web_search.cmd_search_image_probe(message, command)
            self.assertEqual(message.answer_photo.await_count, 1)
            self.assertIn('обычном поиске: выключены', message.answer.await_args.args[0])
            for chat, user in [(SimpleNamespace(id=123, type='group'), SimpleNamespace(id=123)),
                               (SimpleNamespace(id=123, type='private'), SimpleNamespace(id=124)),
                               (SimpleNamespace(id=123, type='private'), None)]:
                message.chat, message.from_user = chat, user
                await web_search.cmd_search_image_probe(message, command)
            search.assert_awaited_once()

    async def test_probe_delivery_uncertain_does_not_retry(self):
        message = SimpleNamespace(chat=SimpleNamespace(id=123, type='private'), from_user=SimpleNamespace(id=123),
                                  answer=AsyncMock(), answer_photo=AsyncMock(side_effect=OSError('secret')))
        with patch.object(web_search, 'ADMIN_ID', 123), patch.object(web_search, '_image_probe_lock', asyncio.Lock()), \
             patch.object(web_search, 'smart_web_search_with_image', new=AsyncMock(return_value=('answer', self.candidates[0]['url']))):
            await web_search.cmd_search_image_probe(message, SimpleNamespace(args='Марс'))
        message.answer_photo.assert_awaited_once()
        self.assertTrue(any('Проверь чат' in call.args[0] for call in message.answer.await_args_list))


class ImageDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_delivers_text_before_image_and_does_not_retry(self):
        order = []
        async def edit(text):
            order.append(('text', text))
            return status
        async def photo(**kwargs):
            order.append(('photo', kwargs))
            raise OSError('private URL')
        status = SimpleNamespace(edit_text=AsyncMock(side_effect=edit))
        message = SimpleNamespace(text='Экс, найди Марс', chat=SimpleNamespace(id=-1001, type='supergroup'),
            from_user=SimpleNamespace(id=123), bot=SimpleNamespace(id=42), reply_to_message=None,
            reply=AsyncMock(return_value=status), reply_photo=AsyncMock(side_effect=photo))
        personas = SimpleNamespace(find=MagicMock(return_value=SimpleNamespace(to_list=AsyncMock(
            return_value=[{'p_id': 'bender', 'name': 'Бендер'}]))))
        with patch.dict(os.environ, {'SEARCH_IMAGES': 'enabled'}), \
             patch.object(ex_logic, 'db', SimpleNamespace(personas=personas)), \
             patch.object(ex_logic, 'analyze_intent', new=AsyncMock(return_value=('SEARCH', 'Марс'))), \
             patch.object(ex_logic, 'is_feature_enabled', new=AsyncMock(return_value=True)), \
             patch.object(ex_logic, 'smart_web_search_with_image', new=AsyncMock(return_value=('Бендер: Марс!', 'https://example.org/x.jpg'))), \
             patch.object(ex_logic, 'smart_web_search', new_callable=AsyncMock) as old:
            await ex_logic.handle_ex_request(message)
        self.assertEqual([kind for kind, _ in order], ['text', 'photo'])
        self.assertIn('Бендер: Марс!', order[0][1])
        message.reply_photo.assert_awaited_once()
        old.assert_not_awaited()

    async def test_feature_disabled_retains_old_search(self):
        status = SimpleNamespace(edit_text=AsyncMock())
        message = SimpleNamespace(text='Экс, найди Марс', chat=SimpleNamespace(id=-1001, type='supergroup'),
            from_user=SimpleNamespace(id=123), bot=SimpleNamespace(id=42), reply_to_message=None,
            reply=AsyncMock(return_value=status), reply_photo=AsyncMock())
        personas = SimpleNamespace(find=MagicMock(return_value=SimpleNamespace(to_list=AsyncMock(
            return_value=[{'p_id': 'bender', 'name': 'Бендер'}]))))
        with patch.dict(os.environ, {'SEARCH_IMAGES': ''}), \
             patch.object(ex_logic, 'db', SimpleNamespace(personas=personas)), \
             patch.object(ex_logic, 'analyze_intent', new=AsyncMock(return_value=('SEARCH', 'Марс'))), \
             patch.object(ex_logic, 'is_feature_enabled', new=AsyncMock(return_value=True)), \
             patch.object(ex_logic, 'smart_web_search', new=AsyncMock(return_value='ответ')) as old, \
             patch.object(ex_logic, 'smart_web_search_with_image', new_callable=AsyncMock) as new:
            await ex_logic.handle_ex_request(message)
        old.assert_awaited_once_with('Марс')
        new.assert_not_awaited()
        message.reply_photo.assert_not_awaited()
