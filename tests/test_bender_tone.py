import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from mongomock_motor import AsyncMongoMockClient
import ai_logic
import database
import personas
from bender_style import BENDER_TONE, apply_bender_tone
from handlers import bender_clip, ex_logic, web_search
from tests.test_bender_clip import FakeQueue


class PersonaTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_persona_is_updated_on_startup(self):
        collection = AsyncMongoMockClient().test.personas
        await collection.insert_one({'p_id': 'bender', 'prompt': 'старый характер', 'loading': ['старое']})
        with patch.object(personas, 'db', SimpleNamespace(personas=collection)):
            await personas.init_personas()
        saved = await collection.find_one({'p_id': 'bender'})
        self.assertIn(BENDER_TONE, saved['prompt'])
        self.assertNotIn('старый характер', saved['prompt'])
        self.assertEqual(saved['loading'], personas.INITIAL_PERSONAS[0]['loading'])

    async def test_empty_collection_is_initialized(self):
        collection = AsyncMongoMockClient().test.personas
        with patch.object(personas, 'db', SimpleNamespace(personas=collection)):
            await personas.init_personas()
            original = await collection.find_one({'p_id': 'bender'})
            await personas.init_personas()
        saved = await collection.find_one({'p_id': 'bender'})
        self.assertEqual(saved['_id'], original['_id'])
        self.assertIn(BENDER_TONE, saved['prompt'])

    def test_role_keeps_tone_once_even_for_legacy_persona(self):
        legacy = apply_bender_tone('Бендер')
        self.assertIn('Бендер', legacy)
        self.assertEqual(legacy.count(BENDER_TONE), 1)
        self.assertEqual(apply_bender_tone(legacy), legacy)
        self.assertIn('Если с тобой общаются нормально', BENDER_TONE)
        self.assertIn('Если собеседник сам грубит', BENDER_TONE)
        self.assertIn('а не под грубость в цитатах', BENDER_TONE)

    def test_static_waiting_messages_no_unprovoked_insults(self):
        messages = (personas.INITIAL_PERSONAS[0]['loading'] + list(bender_clip.STATUS_MESSAGES)
                    + list(bender_clip.CHECKING_MESSAGES) + list(bender_clip.QUEUE_MESSAGES)
                    + list(bender_clip.FULL_MESSAGES) + list(bender_clip.BUSY_MESSAGES))
        self.assertGreaterEqual(len(bender_clip.STATUS_MESSAGES), 12)
        for text in messages:
            self.assertNotIn('кожан', text.lower())
            self.assertNotIn('укусите', text.lower())


class ToneRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.persona = {'p_id': 'bender', 'name': 'Бендер', 'emoji': '🤖',
                        'prompt': personas.INITIAL_PERSONAS[0]['prompt'], 'loading': ['Думаю']}
        self.status = SimpleNamespace(delete=AsyncMock())
        self.sent = SimpleNamespace(message_id=101)
        self.message = SimpleNamespace(chat=SimpleNamespace(id=-1001), from_user=SimpleNamespace(id=7),
            reply=AsyncMock(side_effect=[self.status, self.sent]))

    async def test_text_and_opinion_prompts_keep_query_and_tone(self):
        for kind, query in [('chat', 'Привет, как дела?'), ('chat', 'Эй, глупый робот!'),
                            ('opinion', 'Что думает Бендер?')]:
            with self.subTest(kind=kind, query=query):
                self.message.reply.reset_mock(side_effect=True)
                self.message.reply.side_effect = [self.status, self.sent]
                fake_db = SimpleNamespace(messages=SimpleNamespace(insert_one=AsyncMock()))
                with patch.object(ex_logic, 'db', fake_db), \
                     patch.object(ex_logic, 'generate_with_retry', new=AsyncMock(return_value='Ответ')) as generate:
                    if kind == 'chat':
                        await ex_logic.generate_chat_response(self.message, query, 'Исходный контекст', [self.persona])
                    else:
                        await ex_logic.generate_persona_opinion(self.message, self.persona, query, 'Исходный контекст')
                prompt = generate.await_args.args[0]
                self.assertEqual(prompt.count(BENDER_TONE), 1)
                self.assertIn(query, prompt)
                self.assertIn('Исходный контекст', prompt)

    async def test_other_persona_does_not_receive_bender_tone(self):
        other = {'p_id': 'other', 'name': 'Другой', 'emoji': '🙂', 'prompt': 'Особый характер'}
        fake_db = SimpleNamespace(messages=SimpleNamespace(insert_one=AsyncMock()))
        with patch.object(ex_logic, 'db', fake_db), \
             patch.object(ex_logic, 'generate_with_retry', new=AsyncMock(return_value='Ответ')) as generate:
            await ex_logic.generate_chat_response(self.message, 'Привет', '', [other])
        self.assertNotIn(BENDER_TONE, generate.await_args.args[0])

    async def test_search_tone_does_not_remove_facts_or_validation(self):
        results = [{'title': 'Тема', 'body': 'Проверяемый факт', 'href': 'https://example.org'}]
        with patch.object(web_search, 'generate_with_retry', new=AsyncMock(return_value='STATUS: OK\nОтвет')) as generate:
            status, answer, _ = await web_search._evaluate_results('Найди тему', results)
        prompt = generate.await_args.args[0]
        self.assertEqual((status, answer), ('ok', 'Ответ'))
        self.assertIn(BENDER_TONE, prompt)
        self.assertIn('Проверяемый факт', prompt)
        self.assertIn('Не выдумывай факты или даты', prompt)
        self.assertIn('STATUS: NO', prompt)
        self.assertNotIn('сарказмом на протяжении всего ответа', prompt)

    async def test_video_queue_prompt_has_same_tone(self):
        queue = FakeQueue()
        item = {'id': 'job', 'request_id': 'req', 'chat_id': -1001, 'message_id': 42,
                'thread_id': None, 'status_message_id': None, 'query': 'Привет!',
                'context': 'Тема разговора', 'stage': 'queued'}
        queue.items.append(item.copy())
        bot = SimpleNamespace(edit_message_text=AsyncMock(), delete_message=AsyncMock(), send_video_note=AsyncMock())
        client = MagicMock()
        client.render = AsyncMock(return_value='file-id')
        fake_db = SimpleNamespace(personas=SimpleNamespace(find_one=AsyncMock(return_value=self.persona)))
        with patch.object(bender_clip, 'db', fake_db), \
             patch.object(bender_clip, 'generate_with_retry', new=AsyncMock(
                 return_value='{"speech":"Здравствуйте!","sign":"БЕНДЕР В ДЕЛЕ"}')) as generate, \
             patch.object(bender_clip, 'BenderWorkerClient', return_value=client):
            await bender_clip.process_queued_video(queue, bot, item)
        prompt = generate.await_args.args[0]
        self.assertEqual(prompt.count(BENDER_TONE), 1)
        self.assertIn('Привет!', prompt)
        self.assertIn('Тема разговора', prompt)
        bot.send_video_note.assert_awaited_once()

    async def test_gossip_does_not_require_mocking_people(self):
        fake_db = SimpleNamespace(personas=SimpleNamespace(find_one=AsyncMock(return_value=self.persona)))
        with patch.object(database, 'db', fake_db), patch.object(ai_logic, 'is_gemini_alive', return_value=True), \
             patch.object(ai_logic, 'GEMINI_KEYS', ['key']), patch.object(ai_logic, 'fire_gemini_key', return_value='key'), \
             patch.object(ai_logic, 'make_rest_request', new=AsyncMock(return_value=('Сводка', 200))) as generate:
            self.assertEqual(await ai_logic.get_expert_report('Участник: привет', 'bender'), 'Сводка')
        prompt = generate.await_args.args[0]
        self.assertIn(BENDER_TONE, prompt)
        self.assertNotIn('подколи активистов', prompt)
        self.assertIn('главные темы', prompt)
