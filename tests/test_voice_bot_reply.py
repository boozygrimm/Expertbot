"""Replying to this bot by voice is an address, even without its name."""
import asyncio
from datetime import datetime, timezone
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from mongomock_motor import AsyncMongoMockClient
from handlers import admin_panel, audio_transcription, auth, bender_clip, ex_logic, logger, response_choice, voice_context, voice_rec
from tests.test_voice_history_context import event


class VoiceBotReplyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database = AsyncMongoMockClient().test
        self.db = SimpleNamespace(messages=database.messages, settings=database.settings,
                                  personas=database.personas, auth_chats=database.auth_chats,
                                  ex_response_choices=database.choices)
        await self.db.personas.insert_one({'p_id': 'bender', 'name': 'Бендер', 'emoji': '🤖'})
        await self.db.auth_chats.insert_one({'chat_id': -101, 'status': 'approved'})
        self.flags = {'voice': True, 'assistant': True, 'auto_voice_replies': True,
                      'voice_replies': True, 'video_notes': True}
        self.transcription = AsyncMock(return_value='А что было дальше?')
        for p in [patch.object(voice_rec, 'db', self.db), patch.object(ex_logic, 'db', self.db),
                  patch.object(audio_transcription, 'db', self.db), patch.object(response_choice, 'db', self.db),
                  patch.object(voice_rec, 'get_transcription', self.transcription),
                  patch.object(voice_rec, 'get_chat_feature_flags', new=AsyncMock(side_effect=lambda _: self.flags)),
                  patch.object(ex_logic, 'get_chat_feature_flags', new=AsyncMock(side_effect=lambda _: self.flags)),
                  patch.object(response_choice, 'get_chat_feature_flags', new=AsyncMock(side_effect=lambda _: self.flags)),
                  patch.object(ex_logic, 'is_feature_enabled', new=AsyncMock(return_value=True)),
                  patch.object(voice_context, 'is_feature_enabled', new=AsyncMock(return_value=True))]:
            p.start(); self.addCleanup(p.stop)
        await self.db.messages.insert_one({'chat_id': -101, 'message_id': 40, 'message_thread_id': 7,
            'user_name': '🤖 Бендер', 'text': '[Голосовое Бендера, текст для озвучки: Раньше было вот что.]',
            'audio_status': 'ready', 'audio_transcript': 'Раньше было вот что.', 'audio_source': 'tts_input',
            'createdAt': datetime.now(timezone.utc)})

    def request(self, incoming='voice', source_kind='voice', *, source_id=999, mid=50):
        message = event(incoming, mid=mid, thread=7)
        media = SimpleNamespace(file_id='bot-file', file_size=100)
        message.reply_to_message = SimpleNamespace(
            chat=message.chat, message_id=40, message_thread_id=7,
            from_user=SimpleNamespace(id=source_id) if source_id is not None else None,
            voice=media if source_kind == 'voice' else None,
            video_note=media if source_kind == 'video_note' else None,
            text='Раньше было вот что.' if source_kind == 'text' else None, caption=None, bot=message.bot)
        message.model_copy.side_effect = lambda update: SimpleNamespace(**{**vars(message), **update})
        message.reply.return_value = SimpleNamespace(message_id=800 + mid, chat=message.chat,
            edit_reply_markup=AsyncMock(), edit_text=AsyncMock(), delete=AsyncMock())
        return message

    async def test_voice_and_circle_reply_to_any_bot_format_auto_with_context(self):
        for incoming in ('voice', 'video_note'):
            for source_kind in ('voice', 'video_note', 'text'):
                with self.subTest(incoming=incoming, source=source_kind):
                    message = self.request(incoming, source_kind)
                    with patch.object(ex_logic, 'analyze_intent', new=AsyncMock(side_effect=lambda q, context='': ('CHAT', q))), \
                         patch.object(bender_clip, 'enqueue_voice_reply', new_callable=AsyncMock) as send, \
                         patch.object(audio_transcription, 'start_transcription') as recognize_source:
                        await voice_rec.handle_voice_messages(message)
                    send.assert_awaited_once()
                    target, query, context = send.await_args.args
                    self.assertEqual(query, 'А что было дальше?')
                    self.assertIn('Раньше было вот что.', context)
                    self.assertEqual((target.message_id, target.message_thread_id), (50, 7))
                    message.reply.assert_not_awaited()
                    recognize_source.assert_not_called()

    async def test_auto_off_opens_menu_and_callback_keeps_context(self):
        self.flags['auto_voice_replies'] = False
        msg = self.request()
        with patch.object(ex_logic, 'analyze_intent', new=AsyncMock(return_value=('CHAT', 'А что было дальше?'))):
            await voice_rec.handle_voice_messages(msg)
        self.assertEqual(msg.reply.await_args.args[0], '🤖 Как мне ответить?')
        choice = await self.db.ex_response_choices.find_one({'chat_id': -101, 'message_id': 50})
        self.assertIn('Раньше было вот что.', choice['context'])
        callback = SimpleNamespace(data=f"exfmt:{choice['_id']}:voice", message=msg.reply.return_value,
            from_user=msg.from_user, bot=msg.bot, answer=AsyncMock())
        with patch.object(bender_clip, 'enqueue_voice_reply', new_callable=AsyncMock) as send:
            await response_choice.response_choice_callback(callback)
        target, query, context = send.await_args.args
        self.assertEqual((target.message_id, target.message_thread_id, query), (50, 7, 'А что было дальше?'))
        self.assertIn('Раньше было вот что.', context)

    async def test_without_menu_feature_legacy_text_path_is_preserved(self):
        self.flags.update(auto_voice_replies=False, voice_replies=False, video_notes=False)
        msg = self.request()
        with patch.object(ex_logic, 'analyze_intent', new=AsyncMock(return_value=('CHAT', 'А что было дальше?'))), \
             patch.object(ex_logic, 'generate_chat_response', new_callable=AsyncMock) as answer:
            await voice_rec.handle_voice_messages(msg)
        answer.assert_awaited_once()
        self.assertIn('Раньше было вот что.', answer.await_args.args[2])

    async def test_reply_to_other_person_or_no_reply_is_not_implicit_address(self):
        for source_id in (8, 998, None):
            with self.subTest(source_id=source_id):
                msg = self.request(source_id=source_id)
                with patch.object(voice_rec, 'handle_ex_request', new_callable=AsyncMock) as handler:
                    await voice_rec.handle_voice_messages(msg)
                handler.assert_not_awaited(); msg.reply.assert_not_awaited()
        msg = self.request(); msg.reply_to_message = None
        with patch.object(voice_rec, 'handle_ex_request', new_callable=AsyncMock) as handler:
            await voice_rec.handle_voice_messages(msg)
        handler.assert_not_awaited()

    async def test_similar_words_preserved_as_reply_and_never_address_without_reply(self):
        for phrase in ('Экскурсия в парк', 'Экспертный совет'):
            with self.subTest(phrase=phrase):
                self.transcription.return_value = '  ' + phrase + '  '
                msg = self.request()
                with patch.object(ex_logic, 'analyze_intent', new=AsyncMock(side_effect=lambda q, context='': ('CHAT', q))) as classify, \
                     patch.object(bender_clip, 'enqueue_voice_reply', new_callable=AsyncMock) as send:
                    await voice_rec.handle_voice_messages(msg)
                self.assertEqual(classify.await_args.args[0], phrase)
                self.assertEqual(send.await_args.args[1], phrase)
                msg.reply_to_message = None
                with patch.object(voice_rec, 'handle_ex_request', new_callable=AsyncMock) as handler:
                    await voice_rec.handle_voice_messages(msg)
                handler.assert_not_awaited()

    async def test_ordinary_conversation_about_voice_is_not_mistaken_for_transcription(self):
        self.transcription.return_value = 'У тебя классный голос'
        msg = self.request()
        with patch.object(ex_logic, 'analyze_intent', new=AsyncMock(return_value=('CHAT', 'У тебя классный голос'))), \
             patch.object(bender_clip, 'enqueue_voice_reply', new_callable=AsyncMock) as send:
            await voice_rec.handle_voice_messages(msg)
        send.assert_awaited_once()
        self.assertIn('Раньше было вот что.', send.await_args.args[2])
        msg.reply.assert_not_awaited()

    async def test_explicit_transcription_request_remains_special_action(self):
        self.transcription.return_value = 'Расшифруй'
        msg = self.request()
        with patch.object(bender_clip, 'enqueue_voice_reply', new_callable=AsyncMock) as send:
            await voice_rec.handle_voice_messages(msg)
        send.assert_not_awaited()
        self.assertIn('Раньше было вот что.', msg.reply.return_value.edit_text.await_args.args[0])

    async def test_special_music_request_is_not_converted_to_voice(self):
        self.transcription.return_value = 'Включи песню Time in a Bottle'
        msg = self.request()
        with patch.object(ex_logic, 'handle_music_request', new_callable=AsyncMock) as music, \
             patch.object(bender_clip, 'enqueue_voice_reply', new_callable=AsyncMock) as voice:
            await voice_rec.handle_voice_messages(msg)
        music.assert_awaited_once()
        voice.assert_not_awaited()

    async def test_other_special_requests_remain_special(self):
        for intent in ('IMAGE', 'SEARCH', 'GOSSIP', 'POLE'):
            with self.subTest(intent=intent):
                msg = self.request()
                with patch.object(ex_logic, 'analyze_intent', new=AsyncMock(return_value=(intent, 'пример'))), \
                     patch.object(ex_logic, 'process_image_request', new_callable=AsyncMock) as image, \
                     patch.dict(os.environ, {'SEARCH_IMAGES': ''}), \
                     patch.object(ex_logic, 'smart_web_search', new=AsyncMock(return_value='ответ')) as search, \
                     patch.object(ex_logic, 'send_persona_selection', new_callable=AsyncMock) as gossip, \
                     patch.object(ex_logic, 'start_game', new_callable=AsyncMock) as pole, \
                     patch.object(bender_clip, 'enqueue_voice_reply', new_callable=AsyncMock) as voice:
                    await voice_rec.handle_voice_messages(msg)
                voice.assert_not_awaited()
                {'IMAGE': image, 'SEARCH': search, 'GOSSIP': gossip, 'POLE': pole}[intent].assert_awaited_once()

    async def test_disabled_modules_and_failed_transcription_do_not_answer(self):
        for disabled in ('voice', 'assistant'):
            with self.subTest(disabled=disabled):
                self.flags[disabled] = False
                msg = self.request()
                with patch.object(voice_rec, 'handle_ex_request', new_callable=AsyncMock) as handler:
                    await voice_rec.handle_voice_messages(msg)
                handler.assert_not_awaited()
                self.flags[disabled] = True
        for result in (None, '', '   '):
            self.transcription.return_value = result
            msg = self.request()
            with patch.object(voice_rec, 'handle_ex_request', new_callable=AsyncMock) as handler:
                await voice_rec.handle_voice_messages(msg)
            handler.assert_not_awaited()

    async def test_voice_on_still_publishes_and_answers_bot_reply(self):
        await self.db.settings.insert_one({'setting': 'auto_voice', 'chat_id': -101, 'value': True})
        msg = self.request()
        with patch.object(ex_logic, 'analyze_intent', new=AsyncMock(return_value=('CHAT', 'А что было дальше?'))), \
             patch.object(bender_clip, 'enqueue_voice_reply', new_callable=AsyncMock) as send:
            await voice_rec.handle_voice_messages(msg)
        self.assertIn('А что было дальше?', msg.reply.return_value.edit_text.await_args.args[0])
        send.assert_awaited_once()

    async def test_unauthorized_group_reply_never_reaches_recognition(self):
        from aiogram.types import Message
        msg = self.request()
        msg = Message.model_construct(**vars(msg))
        handler = AsyncMock()
        await self.db.auth_chats.update_one({'chat_id': -101}, {'$set': {'status': 'pending'}})
        with patch.object(auth, 'db', self.db):
            await auth.AuthMiddleware()(handler, msg, {})
        handler.assert_not_awaited()
        self.transcription.assert_not_awaited()

    async def test_real_aiogram_dispatcher_authorization_and_reply(self):
        from aiogram import Bot, Dispatcher, F
        from aiogram.types import Update
        await self.db.settings.insert_one({'setting': 'feature_panel', 'chat_id': -101,
            'features': self.flags.copy()})
        bot = Bot('123456:TESTTOKEN')
        dp = Dispatcher()
        dp.message.middleware(auth.AuthMiddleware())
        dp.message.middleware(logger.ChatLoggerMiddleware())
        dp.message.middleware(admin_panel.FeatureAccessMiddleware())
        dp.message.register(voice_rec.handle_voice_messages, F.voice | F.video_note)
        voice = {'file_id': 'file-123', 'file_unique_id': 'uniq', 'duration': 1}
        update = Update.model_validate({'update_id': 23, 'message': {'message_id': 50, 'date': 1760000000,
            'chat': {'id': -101, 'type': 'supergroup', 'is_forum': True}, 'message_thread_id': 7,
            'from': {'id': 7, 'is_bot': False, 'first_name': 'Аня'}, 'voice': voice,
            'reply_to_message': {'message_id': 40, 'date': 1760000000,
                'chat': {'id': -101, 'type': 'supergroup', 'is_forum': True},
                'from': {'id': 123456, 'is_bot': True, 'first_name': 'Бендер'}, 'voice': voice}}})
        async def download(file_id, destination):
            with open(destination, 'wb') as f:
                f.write(b'audio')
        try:
            with patch.object(auth, 'db', self.db), patch.object(logger, 'db', self.db), \
                 patch.object(admin_panel, 'db', self.db), \
                 patch.object(audio_transcription, 'transcribe_audio_groq', new=AsyncMock(return_value='А что дальше?')) as transcribe, \
                 patch.object(bot, 'download', new=AsyncMock(side_effect=download)), \
                 patch.object(ex_logic, 'analyze_intent', new=AsyncMock(return_value=('CHAT', 'А что дальше?'))), \
                 patch.object(bender_clip, 'enqueue_voice_reply', new_callable=AsyncMock) as enqueue:
                await dp.feed_update(bot, update)
            enqueue.assert_awaited_once()
            self.assertIn('Раньше было вот что.', enqueue.await_args.args[2])
            transcribe.assert_awaited_once()
            self.assertEqual((await self.db.messages.find_one({'message_id': 50}))['audio_transcript'], 'А что дальше?')
        finally:
            await bot.session.close()
            tasks = list(audio_transcription._jobs.values())
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            audio_transcription._jobs.clear()
