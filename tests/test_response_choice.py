import asyncio
from datetime import timedelta
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from mongomock_motor import AsyncMongoMockClient
from handlers import response_choice as choice, ex_logic, bender_clip, admin_panel


class ChoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        client = AsyncMongoMockClient()
        database = client.test
        self.db = SimpleNamespace(ex_response_choices=database.ex_response_choices,
                                  auth_chats=database.auth_chats, personas=database.personas)
        await self.db.auth_chats.insert_one({'chat_id': -1001, 'status': 'approved'})
        await self.db.personas.insert_one({'p_id': 'bender', 'name': 'Бендер', 'emoji': '🤖'})
        self.menu = SimpleNamespace(message_id=99, delete=AsyncMock(), edit_text=AsyncMock(), edit_reply_markup=AsyncMock(),
                                    chat=SimpleNamespace(id=-1001, type='supergroup'))
        self.user = SimpleNamespace(id=123, first_name='User')
        self.bot = SimpleNamespace(send_message=AsyncMock())
        self.message = SimpleNamespace(chat=SimpleNamespace(id=-1001, type='supergroup'),
            message_id=42, message_thread_id=7, from_user=self.user, bot=self.bot,
            reply=AsyncMock(return_value=self.menu))
        self.flags = {'assistant': True, 'video_notes': True}
        for p in [patch.object(choice, 'db', self.db),
                  patch.object(choice, 'get_chat_feature_flags', new=AsyncMock(side_effect=lambda chat_id: self.flags))]:
            p.start(); self.addCleanup(p.stop)

    async def offer(self, **kwargs):
        self.assertTrue(await choice.offer_response_choice(self.message, 'Что думаешь?', 'Контекст', **kwargs))
        markup = self.menu.edit_reply_markup.await_args.kwargs['reply_markup']
        self.assertEqual([b.text for row in markup.inline_keyboard for b in row],
                         ['⭕ Кружок — наряднее', '💬 Текст — быстрее'])
        doc = await self.db.ex_response_choices.find_one({'chat_id': -1001})
        self.assertEqual(doc['menu_message_id'], 99)
        return doc

    def callback(self, doc, mode='text', user=None, menu=None):
        return SimpleNamespace(data=f"exfmt:{doc['_id']}:{mode}", message=menu or self.menu,
                               from_user=user or self.user, bot=self.bot, answer=AsyncMock())

    async def test_no_menu_when_video_off_or_private(self):
        self.flags['video_notes'] = False
        self.assertFalse(await choice.offer_response_choice(self.message, 'Привет', ''))
        self.message.chat.type = 'private'; self.flags['video_notes'] = True
        self.assertFalse(await choice.offer_response_choice(self.message, 'Привет', ''))
        self.message.reply.assert_not_awaited()
        self.assertEqual(await self.db.ex_response_choices.count_documents({}), 0)

    async def test_buttons_are_published_only_after_message_id_is_saved(self):
        async def on_publish(**kwargs):
            doc = await self.db.ex_response_choices.find_one({'chat_id': -1001})
            self.assertEqual(doc['menu_message_id'], 99)
            callback_data = kwargs['reply_markup'].inline_keyboard[0][1].callback_data
            callback = SimpleNamespace(data=callback_data, message=self.menu, from_user=self.user,
                                       bot=self.bot, answer=AsyncMock())
            await choice.response_choice_callback(callback)
        self.menu.edit_reply_markup.side_effect = on_publish
        with patch.object(ex_logic, 'generate_chat_response', new_callable=AsyncMock) as text:
            self.assertTrue(await choice.offer_response_choice(self.message, 'Привет', ''))
        text.assert_awaited_once()
        self.menu.edit_text.assert_not_awaited()

    async def test_failure_to_publish_menu_reports_error_and_marks_failed(self):
        self.menu.edit_reply_markup.side_effect = OSError('telegram unavailable')
        self.assertTrue(await choice.offer_response_choice(self.message, 'Привет', ''))
        self.menu.edit_text.assert_awaited_once()
        self.assertEqual((await self.db.ex_response_choices.find_one({'chat_id': -1001}))['state'], 'failed')

    async def test_text_preserves_context_original_reply_and_topic(self):
        doc = await self.offer()
        callback = self.callback(doc)
        with patch.object(ex_logic, 'generate_chat_response', new_callable=AsyncMock) as generate, \
             patch.object(bender_clip, 'enqueue_video_reply', new_callable=AsyncMock) as video:
            await choice.response_choice_callback(callback)
        generate.assert_awaited_once()
        target, query, context, personas = generate.await_args.args
        self.assertEqual((query, context), ('Что думаешь?', 'Контекст'))
        self.assertEqual(personas[0]['p_id'], 'bender')
        self.assertEqual(target.message_id, 42)
        self.assertEqual(target.message_thread_id, 7)
        await target.reply('Ответ')
        fields = self.bot.send_message.await_args.kwargs
        self.assertEqual(fields['chat_id'], -1001)
        self.assertEqual(fields['message_thread_id'], 7)
        self.assertEqual(fields['reply_parameters'].message_id, 42)
        self.assertTrue(fields['reply_parameters'].allow_sending_without_reply)
        video.assert_not_awaited()
        self.menu.delete.assert_awaited_once()

    async def test_video_uses_original_request_not_menu(self):
        doc = await self.offer()
        callback = self.callback(doc, 'video')
        with patch.object(bender_clip, 'enqueue_video_reply', new_callable=AsyncMock) as video:
            await choice.response_choice_callback(callback)
        target, query, context = video.await_args.args
        self.assertEqual((target.message_id, target.message_thread_id, query, context),
                         (42, 7, 'Что думаешь?', 'Контекст'))

    async def test_other_user_and_other_chat_cannot_claim(self):
        doc = await self.offer()
        bad_user = self.callback(doc, user=SimpleNamespace(id=999))
        await choice.response_choice_callback(bad_user)
        self.assertIn('автор', bad_user.answer.await_args.args[0])
        other_menu = SimpleNamespace(message_id=99, chat=SimpleNamespace(id=-1002, type='supergroup'))
        await self.db.auth_chats.insert_one({'chat_id': -1002, 'status': 'approved'})
        bad_chat = self.callback(doc, menu=other_menu)
        await choice.response_choice_callback(bad_chat)
        self.assertIn('устарел', bad_chat.answer.await_args.args[0])
        self.assertEqual((await self.db.ex_response_choices.find_one({'_id': doc['_id']}))['state'], 'pending')

    async def test_double_click_only_one_action(self):
        doc = await self.offer()
        first, second = self.callback(doc, 'video'), self.callback(doc, 'text')
        with patch.object(bender_clip, 'enqueue_video_reply', new_callable=AsyncMock) as video, \
             patch.object(ex_logic, 'generate_chat_response', new_callable=AsyncMock) as text:
            await asyncio.gather(choice.response_choice_callback(first), choice.response_choice_callback(second))
        self.assertEqual(video.await_count + text.await_count, 1)

    async def test_panel_changed_video_off_keeps_text_choice(self):
        doc = await self.offer()
        self.flags['video_notes'] = False
        callback = self.callback(doc, 'video')
        with patch.object(bender_clip, 'enqueue_video_reply', new_callable=AsyncMock) as video:
            await choice.response_choice_callback(callback)
        video.assert_not_awaited()
        self.assertIn('выключены', callback.answer.await_args.args[0])
        self.assertEqual((await self.db.ex_response_choices.find_one({'_id': doc['_id']}))['state'], 'pending')
        with patch.object(ex_logic, 'generate_chat_response', new_callable=AsyncMock) as text:
            await choice.response_choice_callback(self.callback(doc, 'text'))
        text.assert_awaited_once()

    async def test_panel_assistant_off_and_revoked_auth(self):
        doc = await self.offer()
        self.flags['assistant'] = False
        callback = self.callback(doc)
        await choice.response_choice_callback(callback)
        self.assertIn('выключен', callback.answer.await_args.args[0])
        await self.db.auth_chats.update_one({'chat_id': -1001}, {'$set': {'status': 'denied'}})
        callback = self.callback(doc)
        await choice.response_choice_callback(callback)
        self.assertIn('авторизован', callback.answer.await_args.args[0])
        self.assertEqual((await self.db.ex_response_choices.find_one({'_id': doc['_id']}))['state'], 'pending')

    async def test_expired_or_duplicate_or_wrong_menu_does_not_run(self):
        doc = await self.offer()
        await self.db.ex_response_choices.update_one({'_id': doc['_id']},
            {'$set': {'expires_at': choice.now() - timedelta(seconds=1)}})
        callback = self.callback(doc)
        with patch.object(ex_logic, 'generate_chat_response', new_callable=AsyncMock) as text:
            await choice.response_choice_callback(callback)
        text.assert_not_awaited()
        self.assertIn('устарел', callback.answer.await_args.args[0])
        self.assertTrue(await choice.offer_response_choice(self.message, 'Что думаешь?', 'Контекст'))
        self.message.reply.assert_awaited_once()  # duplicate message does not send another menu

    async def test_opinion_text_preserves_selected_persona(self):
        doc = await self.offer(kind='opinion', persona_id='bender')
        with patch.object(ex_logic, 'generate_persona_opinion', new_callable=AsyncMock) as opinion:
            await choice.response_choice_callback(self.callback(doc))
        target, persona, query, context = opinion.await_args.args
        self.assertEqual((target.message_id, persona['p_id'], query, context),
                         (42, 'bender', 'Что думаешь?', 'Контекст'))

    async def test_callback_ack_error_does_not_cancel_claimed_action(self):
        doc = await self.offer()
        callback = self.callback(doc, 'video')
        callback.answer.side_effect = OSError('connection')
        with patch.object(bender_clip, 'enqueue_video_reply', new_callable=AsyncMock) as video:
            await choice.response_choice_callback(callback)
        video.assert_awaited_once()


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_special_intents_do_not_show_format_menu(self):
        message = SimpleNamespace(text='Экс, найди песню test', chat=SimpleNamespace(id=-1001, type='supergroup'),
                                  from_user=SimpleNamespace(id=123), bot=SimpleNamespace(id=42),
                                  reply_to_message=None, reply=AsyncMock())
        personas = SimpleNamespace(find=MagicMock(return_value=SimpleNamespace(to_list=AsyncMock(return_value=[{'p_id': 'bender', 'name': 'Бендер'}]))))
        with patch.object(ex_logic, 'db', SimpleNamespace(personas=personas)), \
             patch.object(ex_logic, 'is_feature_enabled', new=AsyncMock(return_value=True)), \
             patch.object(ex_logic, 'offer_response_choice', new_callable=AsyncMock) as offer, \
             patch.object(ex_logic, 'handle_music_request', new_callable=AsyncMock) as music:
            await ex_logic.handle_ex_request(message)
        music.assert_awaited_once_with(message, 'test')
        offer.assert_not_awaited()

    async def test_normal_chat_offers_choice_without_generating(self):
        message = SimpleNamespace(text='Экс, привет', chat=SimpleNamespace(id=-1001, type='supergroup'),
                                  from_user=SimpleNamespace(id=123), bot=SimpleNamespace(id=42),
                                  reply_to_message=None, reply=AsyncMock())
        personas = SimpleNamespace(find=MagicMock(return_value=SimpleNamespace(to_list=AsyncMock(
            return_value=[{'p_id': 'bender', 'name': 'Бендер'}]))))
        with patch.object(ex_logic, 'db', SimpleNamespace(personas=personas)), \
             patch.object(ex_logic, 'analyze_intent', new=AsyncMock(return_value=('CHAT', 'привет'))), \
             patch.object(ex_logic, 'is_feature_enabled', new=AsyncMock(return_value=True)), \
             patch.object(ex_logic, 'offer_response_choice', new=AsyncMock(return_value=True)) as offer, \
             patch.object(ex_logic, 'generate_chat_response', new_callable=AsyncMock) as generate:
            await ex_logic.handle_ex_request(message)
        offer.assert_awaited_once_with(message, 'привет', '')
        generate.assert_not_awaited()

    async def test_normal_chat_with_video_disabled_still_generates_text(self):
        message = SimpleNamespace(text='Экс, привет', chat=SimpleNamespace(id=-1001, type='supergroup'),
                                  from_user=SimpleNamespace(id=123), bot=SimpleNamespace(id=42),
                                  reply_to_message=None, reply=AsyncMock())
        person = {'p_id': 'bender', 'name': 'Бендер'}
        personas = SimpleNamespace(find=MagicMock(return_value=SimpleNamespace(to_list=AsyncMock(return_value=[person]))))
        with patch.object(ex_logic, 'db', SimpleNamespace(personas=personas)), \
             patch.object(ex_logic, 'analyze_intent', new=AsyncMock(return_value=('CHAT', 'привет'))), \
             patch.object(ex_logic, 'is_feature_enabled', new=AsyncMock(return_value=True)), \
             patch.object(ex_logic, 'offer_response_choice', new=AsyncMock(return_value=False)), \
             patch.object(ex_logic, 'generate_chat_response', new_callable=AsyncMock) as generate:
            await ex_logic.handle_ex_request(message)
        generate.assert_awaited_once_with(message, 'привет', '', [person])

    async def test_reply_opinion_offers_choice_with_original_context(self):
        message = SimpleNamespace(text='Экс, что думает Бендер?', chat=SimpleNamespace(id=-1001, type='supergroup'),
            from_user=SimpleNamespace(id=123), bot=SimpleNamespace(id=42),
            reply_to_message=SimpleNamespace(text='Исходное сообщение', caption=None,
                                             from_user=SimpleNamespace(id=1), voice=None, video_note=None),
            reply=AsyncMock())
        person = {'p_id': 'bender', 'name': 'Бендер'}
        personas = SimpleNamespace(find=MagicMock(return_value=SimpleNamespace(to_list=AsyncMock(return_value=[person]))))
        with patch.object(ex_logic, 'db', SimpleNamespace(personas=personas)), \
             patch.object(ex_logic, 'offer_response_choice', new=AsyncMock(return_value=True)) as offer, \
             patch.object(ex_logic, 'generate_persona_opinion', new_callable=AsyncMock) as opinion:
            await ex_logic.handle_ex_request(message)
        offer.assert_awaited_once_with(message, 'что думает Бендер?', 'Исходное сообщение', kind='opinion', persona_id='bender')
        opinion.assert_not_awaited()

    async def test_image_search_and_gossip_bypass_format_choice(self):
        for intent, target in [('IMAGE', 'process_image_request'), ('SEARCH', 'smart_web_search'),
                               ('GOSSIP', 'send_persona_selection')]:
            with self.subTest(intent=intent):
                status = SimpleNamespace(edit_text=AsyncMock())
                message = SimpleNamespace(text='Экс, запрос', chat=SimpleNamespace(id=-1001, type='supergroup'),
                    from_user=SimpleNamespace(id=123), bot=SimpleNamespace(id=42),
                    reply_to_message=None, reply=AsyncMock(return_value=status))
                person = {'p_id': 'bender', 'name': 'Бендер'}
                personas = SimpleNamespace(find=MagicMock(return_value=SimpleNamespace(to_list=AsyncMock(return_value=[person]))))
                with patch.object(ex_logic, 'db', SimpleNamespace(personas=personas)), \
                     patch.object(ex_logic, 'analyze_intent', new=AsyncMock(return_value=(intent, 'пример'))), \
                     patch.object(ex_logic, 'is_feature_enabled', new=AsyncMock(return_value=True)), \
                     patch.object(ex_logic, 'offer_response_choice', new_callable=AsyncMock) as offer, \
                     patch.object(ex_logic, target, new=AsyncMock(return_value='Результат')) as action:
                    await ex_logic.handle_ex_request(message)
                offer.assert_not_awaited()
                action.assert_awaited_once()

    async def test_direct_video_trigger_still_panel_feature(self):
        message = SimpleNamespace(chat=SimpleNamespace(type='supergroup'), text='Экс, кружок тема',
                                  voice=None, video_note=None, reply_to_message=None, bot=SimpleNamespace(id=42))
        self.assertEqual(admin_panel.detect_message_feature(message), 'video_notes')

if __name__ == '__main__': unittest.main()
