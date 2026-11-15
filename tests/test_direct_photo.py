import io
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from PIL import Image
from mongomock_motor import AsyncMongoMockClient
import ai_logic
from handlers import admin_panel, auth, bender_clip, ex_logic, photo_context, response_choice


def message(text=None, caption=None, photo=None, reply=None):
    return SimpleNamespace(chat=SimpleNamespace(id=-1001, type='supergroup'),
        message_id=42, message_thread_id=None, from_user=SimpleNamespace(id=7),
        bot=SimpleNamespace(id=999, send_message=AsyncMock()), text=text, caption=caption,
        photo=photo, voice=None, video_note=None, reply_to_message=reply, reply=AsyncMock())


def photo(file_id='image-id'):
    return [SimpleNamespace(file_id=file_id + '-small'), SimpleNamespace(file_id=file_id)]


class VisionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        image = Image.new('RGB', (1200, 800), 'red')
        output = io.BytesIO()
        image.save(output, format='PNG')
        async def download(file_id, destination):
            destination.write(output.getvalue())
        self.bot = SimpleNamespace(download=AsyncMock(side_effect=download))

    async def test_uses_photo_and_query_with_gemini(self):
        with patch.object(ai_logic, 'generate_with_retry', new=AsyncMock(return_value='Красный прямоугольник')) as gemini, \
             patch.object(ai_logic, 'try_openrouter_vision', new_callable=AsyncMock) as fallback:
            result = await ai_logic.describe_request_photo(self.bot, 'abc', 'Какой цвет?')
        self.assertEqual(result, 'Красный прямоугольник')
        self.assertIn('Какой цвет?', gemini.await_args.args[0])
        with Image.open(io.BytesIO(gemini.await_args.kwargs['media_data'])) as decoded:
            self.assertEqual(decoded.format, 'JPEG')
            self.assertLessEqual(max(decoded.size), 1024)
        self.bot.download.assert_awaited_once()
        fallback.assert_not_awaited()

    async def test_fallback_and_fail_closed(self):
        with patch.object(ai_logic, 'generate_with_retry', new=AsyncMock(side_effect=RuntimeError)), \
             patch.object(ai_logic, 'try_openrouter_vision', new=AsyncMock(return_value=('Кот', 200))) as fallback:
            self.assertEqual(await ai_logic.describe_request_photo(self.bot, 'abc', 'Что это?'), 'Кот')
            fallback.return_value = ('[ФОТО]', 200)
            self.assertIsNone(await ai_logic.describe_request_photo(self.bot, 'abc', 'Что это?'))
            fallback.return_value = (None, 503)
            self.assertIsNone(await ai_logic.describe_request_photo(self.bot, 'abc', 'Что это?'))

    async def test_paid_vision_model_is_not_activated_as_new_fallback(self):
        with patch.object(ai_logic, 'OPENROUTER_VISION_MODEL', 'vendor/model'), \
             patch.object(ai_logic, 'generate_with_retry', new=AsyncMock(return_value=None)), \
             patch.object(ai_logic, 'try_openrouter_vision', new_callable=AsyncMock) as fallback:
            self.assertIsNone(await ai_logic.describe_request_photo(self.bot, 'abc', 'Что это?'))
            fallback.assert_not_awaited()

    async def test_bad_download_does_not_generate(self):
        self.bot.download.side_effect = OSError()
        with patch.object(ai_logic, 'generate_with_retry', new_callable=AsyncMock) as generate:
            self.assertIsNone(await ai_logic.describe_request_photo(self.bot, 'bad', 'Что?'))
            generate.assert_not_awaited()


class PhotoRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database = AsyncMongoMockClient().test
        self.db = SimpleNamespace(personas=self.database.personas, ex_response_choices=self.database.ex_response_choices,
                                  auth_chats=self.database.auth_chats)
        await self.db.personas.insert_one({'p_id': 'bender', 'name': 'Бендер', 'emoji': '🤖'})
        await self.db.auth_chats.insert_one({'chat_id': -1001, 'status': 'approved'})
        self.menu = SimpleNamespace(message_id=99, chat=SimpleNamespace(id=-1001, type='supergroup'),
            edit_reply_markup=AsyncMock(), delete=AsyncMock())
        self.flags = {'assistant': True, 'video_notes': True}
        patches = [patch.object(ex_logic, 'db', self.db), patch.object(response_choice, 'db', self.db),
            patch.object(response_choice, 'get_chat_feature_flags', new=AsyncMock(side_effect=lambda _: self.flags)),
            patch.object(ex_logic, 'is_feature_enabled', new=AsyncMock(side_effect=lambda _, flag: self.flags.get(flag, True))),
            patch.object(ex_logic, 'analyze_intent', new=AsyncMock(side_effect=lambda query, context='': ('CHAT', query))),
            patch.object(photo_context, 'describe_request_photo', new=AsyncMock(return_value='На фото рыжий кот'))]
        self.recognize = patches[-1].new
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    async def test_photo_caption_choice_reaches_both_formats(self):
        for mode in ('text', 'video'):
            with self.subTest(mode=mode):
                msg = message(caption='Экс, что на фото?', photo=photo())
                msg.message_id = 42 if mode == 'text' else 43
                msg.reply.return_value = self.menu
                await ex_logic.handle_ex_request(msg)
                doc = await self.db.ex_response_choices.find_one({'message_id': msg.message_id})
                self.assertIn('рыжий кот', doc['context'])
                cb = SimpleNamespace(data=f"exfmt:{doc['_id']}:{mode}", message=self.menu,
                                     from_user=msg.from_user, bot=msg.bot, answer=AsyncMock())
                with patch.object(ex_logic, 'generate_chat_response', new_callable=AsyncMock) as text, \
                     patch.object(bender_clip, 'enqueue_video_reply', new_callable=AsyncMock) as video:
                    await response_choice.response_choice_callback(cb)
                selected = text if mode == 'text' else video
                selected.assert_awaited_once()
                self.assertIn('рыжий кот', selected.await_args.args[2])
                (video if mode == 'text' else text).assert_not_awaited()
        self.assertEqual(self.recognize.await_count, 2)

    async def test_reply_to_photo_and_caption_preserved(self):
        source = message(caption='Подпись к фото', photo=photo('reply'))
        msg = message(text='Экс, что это?', reply=source)
        msg.reply.return_value = self.menu
        await ex_logic.handle_ex_request(msg)
        doc = await self.db.ex_response_choices.find_one({'message_id': 42})
        self.assertIn('рыжий кот', doc['context'])
        self.assertIn('Подпись к фото', doc['context'])
        self.assertEqual(self.recognize.await_args.args[1], 'reply')

    async def test_failure_does_not_generate_or_offer(self):
        self.recognize.return_value = None
        msg = message(caption='Экс, что это?', photo=photo())
        with patch.object(ex_logic, 'offer_response_choice', new_callable=AsyncMock) as offer, \
             patch.object(ex_logic, 'generate_chat_response', new_callable=AsyncMock) as generate:
            await ex_logic.handle_ex_request(msg)
        self.assertIn('Не смог рассмотреть фото', msg.reply.await_args.args[0])
        offer.assert_not_awaited()
        generate.assert_not_awaited()

    async def test_disabled_assistant_and_unrelated_photo_do_not_recognize(self):
        self.flags['assistant'] = False
        await ex_logic.handle_ex_request(message(caption='Экс, что это?', photo=photo()))
        await ex_logic.handle_ex_request(message(caption='обычная подпись', photo=photo()))
        self.recognize.assert_not_awaited()
        self.assertEqual(admin_panel.detect_message_feature(message(caption='Экс, что это?', photo=photo())), 'assistant')
        self.assertEqual(admin_panel.detect_message_feature(message(caption='Экс, кружок что это?', photo=photo())), 'video_notes')

    async def test_auth_middleware_blocks_before_handler(self):
        handler = AsyncMock()
        from aiogram.types import Message
        event = Message.model_construct(**vars(message(caption='Экс, что это?', photo=photo())))
        with patch.object(auth, 'db', self.db):
            await self.db.auth_chats.delete_many({})
            await auth.AuthMiddleware()(handler, event, {})
        handler.assert_not_awaited()
        self.recognize.assert_not_awaited()

    async def test_explicit_video_caption_and_reply(self):
        for msg in (message(caption='Экс, кружок что это?', photo=photo()),
                    message(text='Экс, кружок что это?', reply=message(caption='Подпись', photo=photo()))):
            with patch.object(bender_clip, 'get_chat_feature_flags', new=AsyncMock(return_value={'video_notes': True})), \
                 patch.object(bender_clip, 'enqueue_video_reply', new_callable=AsyncMock) as enqueue:
                await bender_clip.video_reply(msg)
            self.assertIn('рыжий кот', enqueue.await_args.args[2])
        with patch.object(bender_clip, 'get_chat_feature_flags', new=AsyncMock(return_value={'video_notes': False})):
            self.recognize.reset_mock()
            await bender_clip.video_reply(message(caption='Экс, кружок фото', photo=photo()))
            self.recognize.assert_not_awaited()

    async def test_explicit_video_vision_failure_does_not_enqueue(self):
        self.recognize.return_value = None
        msg = message(caption='Экс, кружок что это?', photo=photo())
        with patch.object(bender_clip, 'get_chat_feature_flags', new=AsyncMock(return_value={'video_notes': True})), \
             patch.object(bender_clip, 'enqueue_video_reply', new_callable=AsyncMock) as enqueue:
            await bender_clip.video_reply(msg)
        enqueue.assert_not_awaited()
        self.assertIn('Не смог рассмотреть фото', msg.reply.await_args.args[0])

    async def test_no_video_menu_still_generates_with_photo(self):
        self.flags['video_notes'] = False
        msg = message(caption='Экс, что это?', photo=photo())
        with patch.object(ex_logic, 'generate_chat_response', new_callable=AsyncMock) as generate:
            await ex_logic.handle_ex_request(msg)
        self.assertIn('рыжий кот', generate.await_args.args[2])

    async def test_both_photos_failure_does_not_use_partial_context(self):
        self.recognize.side_effect = ['кот', None]
        msg = message(caption='Экс, сравни', photo=photo('one'), reply=message(photo=photo('two')))
        with patch.object(ex_logic, 'offer_response_choice', new_callable=AsyncMock) as offer:
            await ex_logic.handle_ex_request(msg)
        offer.assert_not_awaited()
        self.assertIn('Не смог рассмотреть фото', msg.reply.await_args.args[0])

    async def test_opinion_about_photo_passes_context_to_choice(self):
        source = message(photo=photo(), caption='Сюжет')
        msg = message(text='Экс, что думает Бендер?', reply=source)
        msg.reply.return_value = self.menu
        await ex_logic.handle_ex_request(msg)
        doc = await self.db.ex_response_choices.find_one({'message_id': 42})
        self.assertEqual(doc['kind'], 'opinion')
        self.assertIn('рыжий кот', doc['context'])

    async def test_callback_checks_authorization_again_after_vision(self):
        msg = message(caption='Экс, что это?', photo=photo())
        msg.reply.return_value = self.menu
        await ex_logic.handle_ex_request(msg)
        doc = await self.db.ex_response_choices.find_one({'message_id': 42})
        await self.db.auth_chats.delete_many({})
        cb = SimpleNamespace(data=f"exfmt:{doc['_id']}:text", message=self.menu,
                             from_user=msg.from_user, bot=msg.bot, answer=AsyncMock())
        with patch.object(ex_logic, 'generate_chat_response', new_callable=AsyncMock) as text:
            await response_choice.response_choice_callback(cb)
        text.assert_not_awaited()
        self.assertIn('не авторизован', cb.answer.await_args.args[0])

    async def test_existing_special_intent_does_not_invoke_vision(self):
        msg = message(caption='Экс, нарисуй кота', photo=photo())
        with patch.object(ex_logic, 'analyze_intent', new=AsyncMock(return_value=('IMAGE', 'кот'))), \
             patch.object(ex_logic, 'process_image_request', new_callable=AsyncMock) as generate:
            await ex_logic.handle_ex_request(msg)
        generate.assert_awaited_once()
        self.recognize.assert_not_awaited()
