import asyncio
from datetime import datetime, timedelta, timezone
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from aiogram.types import ContentType, Message
from mongomock_motor import AsyncMongoMockClient
from handlers import audio_transcription as audio, admin_panel, auth, bender_clip, ex_logic, logger, recent_context, response_choice, voice_context, voice_rec


def event(kind='voice', *, mid=50, chat=-101, thread=None, caption=None):
    media = SimpleNamespace(file_id='file-123', file_size=100)
    bot = SimpleNamespace(id=999, download=AsyncMock())
    msg = SimpleNamespace(chat=SimpleNamespace(id=chat, type='supergroup', is_forum=False),
        message_id=mid, message_thread_id=thread, from_user=SimpleNamespace(id=7, first_name='Аня'),
        sender_chat=None, bot=bot, text=None, caption=caption, photo=None, animation=None, sticker=None,
        voice=media if kind == 'voice' else None, video_note=media if kind == 'video_note' else None,
        content_type=ContentType.VOICE if kind == 'voice' else
                     ContentType.VIDEO_NOTE if kind == 'video_note' else ContentType.AUDIO,
        reply_to_message=None, reply=AsyncMock(), model_copy=MagicMock())
    async def download(file_id, destination):
        with open(destination, 'wb') as f:
            f.write(b'fake audio')
    bot.download.side_effect = download
    return msg


class VoiceHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database = AsyncMongoMockClient().test
        self.db = SimpleNamespace(messages=self.database.messages, settings=self.database.settings)
        self.feature = AsyncMock(return_value=True)
        self.flags = {'voice': True, 'assistant': True}
        self.transcribe = AsyncMock(return_value='Привет, это речь')
        for p in [patch.object(logger, 'db', self.db), patch.object(audio, 'db', self.db),
                  patch.object(voice_rec, 'db', self.db), patch.object(logger, 'is_feature_enabled', self.feature),
                  patch.object(voice_rec, 'get_chat_feature_flags', new=AsyncMock(side_effect=lambda _: self.flags)),
                  patch.object(audio, 'transcribe_audio_groq', self.transcribe)]:
            p.start(); self.addCleanup(p.stop)
        self.handler = AsyncMock(return_value='ok')

    async def asyncTearDown(self):
        pending = list(audio._jobs.values())
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        audio._jobs.clear()

    def test_transcript_truncation_respects_telegram_utf16_limit(self):
        self.assertEqual(audio.short_transcript('Привет'), 'Привет')
        shortened = audio.short_transcript('🦾' * 2000)
        self.assertTrue(shortened.endswith('…'))
        self.assertLessEqual(len(shortened.encode('utf-16-le')) // 2, 3001)

    async def test_voice_and_video_note_saved_even_when_voice_publication_off(self):
        for kind, label in [('voice', 'Голосовое'), ('video_note', 'Кружок')]:
            msg = event(kind, mid=50 if kind == 'voice' else 51, thread=8, caption='Подпись' if kind == 'voice' else None)
            self.assertEqual(await logger.ChatLoggerMiddleware()(self.handler, msg, {}), 'ok')
            task = audio._jobs[(msg.chat.id, msg.message_id)]
            await task
            doc = await self.database.messages.find_one({'message_id': msg.message_id})
            self.assertEqual(doc['audio_status'], 'ready')
            self.assertEqual(doc['audio_transcript'], 'Привет, это речь')
            self.assertIn(label, doc['text'])
            self.assertIn('Привет, это речь', doc['text'])
            self.assertEqual(doc['message_thread_id'], 8)
            if msg.caption:
                self.assertIn(msg.caption, doc['text'])
            msg.reply.assert_not_awaited()
        self.assertEqual(self.transcribe.await_count, 2)
        self.assertIsNone(await self.database.settings.find_one({'setting': 'auto_voice'}))

    async def test_disabled_module_and_audio_files_are_not_transcribed(self):
        self.feature.return_value = False
        disabled = event()
        await logger.ChatLoggerMiddleware()(self.handler, disabled, {})
        self.assertEqual(await self.database.messages.count_documents({}), 0)
        self.transcribe.assert_not_awaited()
        self.feature.return_value = True
        file = event('audio', mid=51)
        await logger.ChatLoggerMiddleware()(self.handler, file, {})
        self.transcribe.assert_not_awaited()
        self.feature.assert_awaited_once_with(-101, 'voice')

    async def test_feature_check_error_fails_closed(self):
        self.feature.side_effect = RuntimeError()
        await logger.ChatLoggerMiddleware()(self.handler, event(), {})
        self.transcribe.assert_not_awaited()
        self.handler.assert_awaited_once()
        self.assertEqual(await self.database.messages.count_documents({}), 0)

    async def test_unauthorized_message_never_reaches_logger(self):
        actual_message = Message.model_construct(**vars(event()))
        handler = AsyncMock()
        with patch.object(auth, 'db', SimpleNamespace(auth_chats=self.database.auth_chats)):
            await auth.AuthMiddleware()(handler, actual_message, {})
        handler.assert_not_awaited()
        self.transcribe.assert_not_awaited()

    async def test_auto_publication_shares_inflight_job_and_escapes_html(self):
        await self.database.settings.insert_one({'setting': 'auto_voice', 'chat_id': -101, 'value': True})
        started, release = asyncio.Event(), asyncio.Event()
        paths = []
        async def recognize(path):
            paths.append(path)
            started.set()
            await release.wait()
            return 'Привет <b>друг</b>'
        self.transcribe.side_effect = recognize
        msg = event()
        status = SimpleNamespace(edit_text=AsyncMock())
        msg.reply.return_value = status
        await logger.ChatLoggerMiddleware()(self.handler, msg, {})
        await started.wait()
        publication = asyncio.create_task(voice_rec.handle_voice_messages(msg))
        await asyncio.sleep(0)
        release.set()
        await publication
        self.assertEqual(self.transcribe.await_count, 1)
        msg.bot.download.assert_awaited_once()
        self.assertIn('&lt;b&gt;друг&lt;/b&gt;', status.edit_text.await_args.args[0])
        self.assertFalse(os.path.exists(paths[0]))
        self.assertEqual((await self.database.messages.find_one({'message_id': 50}))['audio_transcript'], 'Привет <b>друг</b>')

    async def test_failure_and_too_large_remain_honest_no_temp_files(self):
        paths = []
        async def fail(path):
            paths.append(path)
            return '❌ Ошибка при распознавании'
        self.transcribe.side_effect = fail
        msg = event()
        await logger.ChatLoggerMiddleware()(self.handler, msg, {})
        await audio._jobs[(msg.chat.id, msg.message_id)]
        doc = await self.database.messages.find_one({'message_id': 50})
        self.assertEqual(doc['audio_status'], 'failed')
        self.assertNotIn('audio_transcript', doc)
        self.assertIn('расшифровка не удалась', doc['text'])
        self.assertFalse(os.path.exists(paths[0]))
        large = event(mid=51)
        large.voice.file_size = audio.MAX_FILE_BYTES + 1
        await logger.ChatLoggerMiddleware()(self.handler, large, {})
        await audio._jobs[(large.chat.id, large.message_id)]
        self.assertEqual((await self.database.messages.find_one({'message_id': 51}))['audio_status'], 'failed')
        large.bot.download.assert_not_awaited()

    async def test_full_queue_records_failure_without_starting(self):
        with patch.object(audio, 'MAX_PENDING', 0):
            await logger.ChatLoggerMiddleware()(self.handler, event(), {})
        self.assertEqual((await self.database.messages.find_one({'message_id': 50}))['audio_status'], 'failed')
        self.transcribe.assert_not_awaited()

    async def test_saved_result_reused_for_manual_transcription(self):
        msg = event()
        await logger.ChatLoggerMiddleware()(self.handler, msg, {})
        await audio._jobs[(msg.chat.id, msg.message_id)]
        self.assertEqual(await audio.get_transcription(msg), 'Привет, это речь')
        self.assertEqual(self.transcribe.await_count, 1)

    async def test_voice_on_command_still_triggers_ex_and_stores(self):
        await self.database.settings.insert_one({'setting': 'auto_voice', 'chat_id': -101, 'value': True})
        self.transcribe.return_value = 'Экс, привет'
        msg = event()
        msg.reply.return_value = SimpleNamespace(edit_text=AsyncMock())
        msg.model_copy.return_value = SimpleNamespace(text='Экс, привет')
        await logger.ChatLoggerMiddleware()(self.handler, msg, {})
        with patch.object(voice_rec, 'handle_ex_request', new_callable=AsyncMock) as handler:
            await voice_rec.handle_voice_messages(msg)
        handler.assert_awaited_once_with(msg.model_copy.return_value)
        self.assertEqual(self.transcribe.await_count, 1)

    async def test_voice_off_still_answers_addressed_voice_and_video_without_publishing(self):
        for kind, mid in [('voice', 50), ('video_note', 51)]:
            self.transcribe.return_value = 'Экс, как дела?'
            msg = event(kind, mid=mid)
            fake = SimpleNamespace(text='Экс, как дела?')
            msg.model_copy.return_value = fake
            await logger.ChatLoggerMiddleware()(self.handler, msg, {})
            with patch.object(voice_rec, 'handle_ex_request', new_callable=AsyncMock) as handler:
                await voice_rec.handle_voice_messages(msg)
            handler.assert_awaited_once_with(fake)
            msg.reply.assert_not_awaited()
            self.assertEqual((await self.database.messages.find_one({'message_id': mid}))['audio_transcript'], 'Экс, как дела?')
        self.assertEqual(self.transcribe.await_count, 2)

    async def test_voice_off_real_routing_keeps_text_video_choice(self):
        self.transcribe.return_value = 'Экс, как дела?'
        self.db.personas = self.database.personas
        self.db.ex_response_choices = self.database.ex_response_choices
        self.db.auth_chats = self.database.auth_chats
        await self.db.auth_chats.insert_one({'chat_id': -101, 'status': 'approved'})
        await self.db.personas.insert_one({'p_id': 'bender', 'name': 'Бендер', 'emoji': '🤖'})
        with patch.object(ex_logic, 'db', self.db), patch.object(response_choice, 'db', self.db), \
             patch.object(ex_logic, 'is_feature_enabled', new=AsyncMock(return_value=True)), \
             patch.object(ex_logic, 'analyze_intent', new=AsyncMock(return_value=('CHAT', 'как дела?'))), \
             patch.object(response_choice, 'get_chat_feature_flags', new=AsyncMock(return_value={'assistant': True, 'video_notes': True})):
            for kind, mode, mid in [('voice', 'text', 50), ('video_note', 'video', 51)]:
                msg = event(kind, mid=mid)
                menu = SimpleNamespace(message_id=900 + mid, chat=msg.chat, edit_reply_markup=AsyncMock(), delete=AsyncMock())
                msg.reply.return_value = menu
                msg.model_copy.side_effect = lambda update, msg=msg: SimpleNamespace(**{**vars(msg), **update})
                await logger.ChatLoggerMiddleware()(self.handler, msg, {})
                await voice_rec.handle_voice_messages(msg)
                self.assertEqual(msg.reply.await_args.args[0], '🤖 Как мне ответить?')
                doc = await self.db.ex_response_choices.find_one({'message_id': mid})
                self.assertEqual(doc['query'], 'как дела?')
                callback = SimpleNamespace(data=f"exfmt:{doc['_id']}:{mode}", message=menu,
                                           from_user=msg.from_user, bot=msg.bot, answer=AsyncMock())
                with patch.object(ex_logic, 'generate_chat_response', new_callable=AsyncMock) as text, \
                     patch.object(bender_clip, 'enqueue_video_reply', new_callable=AsyncMock) as video:
                    await response_choice.response_choice_callback(callback)
                (text if mode == 'text' else video).assert_awaited_once()
                (video if mode == 'text' else text).assert_not_awaited()
        self.assertEqual(self.transcribe.await_count, 2)

    async def test_real_dispatcher_keeps_completed_task_if_history_update_fails(self):
        from aiogram import Bot, Dispatcher, F
        from aiogram.types import Update
        await self.database.auth_chats.insert_one({'chat_id': -101, 'status': 'approved'})
        self.transcribe.return_value = 'Экс, привет'
        async def late_settings(*args, **kwargs):
            task = audio._jobs.get((-101, 50))
            if task:
                await asyncio.shield(task)
            return None  # /voice off
        settings = SimpleNamespace(find_one=AsyncMock(side_effect=late_settings))
        self.db.settings = settings
        failed_audio_db = SimpleNamespace(messages=SimpleNamespace(
            update_many=AsyncMock(side_effect=RuntimeError('history unavailable')),
            find_one=AsyncMock(return_value=None)))
        dp = Dispatcher()
        dp.message.middleware(auth.AuthMiddleware())
        dp.message.middleware(logger.ChatLoggerMiddleware())
        dp.message.middleware(admin_panel.FeatureAccessMiddleware())
        dp.message.register(voice_rec.handle_voice_messages, F.voice | F.video_note)
        bot = Bot('123456:TESTTOKEN')
        async def download(file_id, destination):
            with open(destination, 'wb') as f:
                f.write(b'fake audio')
        update = Update.model_validate({'update_id': 1, 'message': {
            'message_id': 50, 'date': 1700000000,
            'chat': {'id': -101, 'type': 'supergroup'},
            'from': {'id': 7, 'is_bot': False, 'first_name': 'Аня'},
            'voice': {'file_id': 'file-123', 'file_unique_id': 'uniq', 'duration': 1},
        }})
        try:
            with patch.object(auth, 'db', SimpleNamespace(auth_chats=self.database.auth_chats)), \
                 patch.object(admin_panel, 'is_feature_enabled', new=AsyncMock(return_value=True)), \
                 patch.object(audio, 'db', failed_audio_db), \
                 patch.object(bot, 'download', new=AsyncMock(side_effect=download)) as download_mock, \
                 patch.object(voice_rec, 'handle_ex_request', new_callable=AsyncMock) as handler:
                await dp.feed_update(bot, update)
            handler.assert_awaited_once()
            self.assertEqual(handler.await_args.args[0].text, 'Экс, привет')
            self.transcribe.assert_awaited_once()
            download_mock.assert_awaited_once()
            failed_audio_db.messages.find_one.assert_not_awaited()
        finally:
            await bot.session.close()

    async def test_voice_off_does_not_answer_unaddressed_speech_or_similar_words(self):
        for mid, phrase in enumerate(['Привет всем', 'Экскурсия начинается', 'Экспертный совет'], 50):
            self.transcribe.return_value = phrase
            msg = event(mid=mid)
            await logger.ChatLoggerMiddleware()(self.handler, msg, {})
            with patch.object(voice_rec, 'handle_ex_request', new_callable=AsyncMock) as handler:
                await voice_rec.handle_voice_messages(msg)
            handler.assert_not_awaited()
            msg.reply.assert_not_awaited()

    async def test_addressed_speech_respects_assistant_and_voice_flags(self):
        self.transcribe.return_value = 'Экс, привет'
        for mid, disabled in [(50, 'assistant'), (51, 'voice')]:
            msg = event(mid=mid)
            await logger.ChatLoggerMiddleware()(self.handler, msg, {})
            self.flags[disabled] = False
            with patch.object(voice_rec, 'handle_ex_request', new_callable=AsyncMock) as handler:
                await voice_rec.handle_voice_messages(msg)
            handler.assert_not_awaited()
            msg.reply.assert_not_awaited()
            self.flags[disabled] = True

    async def test_failed_feature_check_keeps_published_transcript_without_answer(self):
        await self.database.settings.insert_one({'setting': 'auto_voice', 'chat_id': -101, 'value': True})
        self.transcribe.return_value = 'Экс, привет'
        msg = event()
        status = SimpleNamespace(edit_text=AsyncMock())
        msg.reply.return_value = status
        await logger.ChatLoggerMiddleware()(self.handler, msg, {})
        with patch.object(voice_rec, 'get_chat_feature_flags', new=AsyncMock(side_effect=RuntimeError)), \
             patch.object(voice_rec, 'handle_ex_request', new_callable=AsyncMock) as handler:
            await voice_rec.handle_voice_messages(msg)
        handler.assert_not_awaited()
        status.edit_text.assert_awaited_once()
        self.assertIn('Экс, привет', status.edit_text.await_args.args[0])

    async def test_failed_background_is_not_automatically_retried_for_voice_off(self):
        self.transcribe.return_value = '❌ не удалось'
        msg = event()
        await logger.ChatLoggerMiddleware()(self.handler, msg, {})
        await audio._jobs[(msg.chat.id, msg.message_id)]
        with patch.object(voice_rec, 'handle_ex_request', new_callable=AsyncMock) as handler:
            await voice_rec.handle_voice_messages(msg)
        handler.assert_not_awaited()
        self.assertEqual(self.transcribe.await_count, 1)
        msg.reply.assert_not_awaited()

    async def test_manual_request_can_retry_previous_failure(self):
        self.transcribe.return_value = '❌ не удалось'
        msg = event()
        await logger.ChatLoggerMiddleware()(self.handler, msg, {})
        await audio._jobs[(msg.chat.id, msg.message_id)]
        self.transcribe.return_value = 'Готовая расшифровка'
        self.assertEqual(await audio.get_transcription(msg), 'Готовая расшифровка')
        self.assertEqual(self.transcribe.await_count, 2)
        self.assertEqual((await self.database.messages.find_one({'message_id': 50}))['audio_status'], 'ready')

    async def test_transcription_parallelism_is_bounded(self):
        current = 0
        maximum = 0
        reached = asyncio.Event()
        release = asyncio.Event()
        async def recognize(path):
            nonlocal current, maximum
            current += 1
            maximum = max(maximum, current)
            if current == audio.MAX_CONCURRENT:
                reached.set()
            try:
                await release.wait()
                return 'Готово'
            finally:
                current -= 1
        self.transcribe.side_effect = recognize
        tasks = []
        for mid in range(50, 56):
            msg = event(mid=mid)
            await logger.ChatLoggerMiddleware()(self.handler, msg, {})
            tasks.append(audio._jobs[(msg.chat.id, msg.message_id)])
        await asyncio.wait_for(reached.wait(), 1)
        self.assertEqual(self.transcribe.await_count, audio.MAX_CONCURRENT)
        release.set()
        await asyncio.gather(*tasks)
        self.assertLessEqual(maximum, audio.MAX_CONCURRENT)
        self.assertEqual(await self.database.messages.count_documents({'audio_status': 'ready'}), 6)

    async def test_voice_reply_uses_saved_transcript_without_repeat(self):
        source = event()
        await logger.ChatLoggerMiddleware()(self.handler, source, {})
        await audio._jobs[(source.chat.id, source.message_id)]
        request = event(mid=51)
        request.voice = None
        request.reply_to_message = source
        with patch.object(voice_context, 'is_feature_enabled', new=AsyncMock(return_value=True)):
            context = await voice_context.add_voice_reply_context(request, '')
        self.assertIn('Привет, это речь', context)
        self.assertIn('может содержать ошибки', context)
        self.assertEqual(self.transcribe.await_count, 1)

    async def test_voice_reply_timeout_keeps_shared_background_task_running(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def recognize(path):
            started.set()
            await release.wait()
            return 'Запись готова'
        self.transcribe.side_effect = recognize
        source = event()
        await logger.ChatLoggerMiddleware()(self.handler, source, {})
        task = audio._jobs[(source.chat.id, source.message_id)]
        await started.wait()
        request = event(mid=51)
        request.voice = None
        request.reply_to_message = source
        with patch.object(voice_context, 'is_feature_enabled', new=AsyncMock(return_value=True)), \
             patch.object(voice_context, 'REPLY_WAIT_SECONDS', 0.01):
            self.assertIsNone(await voice_context.add_voice_reply_context(request, ''))
        self.assertIn('ещё распознаётся', request.reply.await_args.args[0])
        self.assertFalse(task.cancelled())
        release.set()
        await task
        self.assertEqual((await self.database.messages.find_one({'message_id': 50}))['audio_transcript'], 'Запись готова')
        self.assertEqual(self.transcribe.await_count, 1)

    async def test_voice_reply_disabled_or_failed_never_guesses(self):
        source = event()
        request = event(mid=51)
        request.voice = None
        request.reply_to_message = source
        with patch.object(voice_context, 'is_feature_enabled', new=AsyncMock(return_value=False)):
            self.assertIsNone(await voice_context.add_voice_reply_context(request, ''))
        self.transcribe.assert_not_awaited()
        with patch.object(voice_context, 'is_feature_enabled', new=AsyncMock(return_value=True)), \
             patch.object(voice_context, 'get_transcription', new=AsyncMock(return_value=None)):
            self.assertIsNone(await voice_context.add_voice_reply_context(request, ''))
        self.assertIn('не стану выдумывать', request.reply.await_args.args[0])

    async def test_manual_command_uses_saved_transcript(self):
        source = event()
        await logger.ChatLoggerMiddleware()(self.handler, source, {})
        await audio._jobs[(source.chat.id, source.message_id)]
        request = event(mid=51)
        request.voice = None
        request.text = 'Экс, расшифруй'
        request.reply_to_message = source
        request.reply.return_value = SimpleNamespace(edit_text=AsyncMock())
        with patch.object(ex_logic, 'is_feature_enabled', new=AsyncMock(return_value=True)):
            await ex_logic.handle_ex_request(request)
        self.assertIn('Привет, это речь', request.reply.return_value.edit_text.await_args.args[0])
        self.assertEqual(self.transcribe.await_count, 1)

    async def test_ask_about_voice_puts_transcript_into_choice(self):
        source = event()
        await logger.ChatLoggerMiddleware()(self.handler, source, {})
        await audio._jobs[(source.chat.id, source.message_id)]
        request = event(mid=51)
        request.voice = None
        request.text = 'Экс, что ты думаешь?'
        request.reply_to_message = source
        fake_db = SimpleNamespace(messages=self.database.messages, personas=self.database.personas)
        await self.database.personas.insert_one({'p_id': 'bender', 'name': 'Бендер'})
        with patch.object(ex_logic, 'db', fake_db), \
             patch.object(ex_logic, 'is_feature_enabled', new=AsyncMock(return_value=True)), \
             patch.object(ex_logic, 'analyze_intent', new=AsyncMock(return_value=('CHAT', 'что ты думаешь?'))), \
             patch.object(voice_context, 'is_feature_enabled', new=AsyncMock(return_value=True)), \
             patch.object(ex_logic, 'offer_response_choice', new=AsyncMock(return_value=True)) as offer:
            await ex_logic.handle_ex_request(request)
        self.assertIn('Привет, это речь', offer.await_args.args[2])
        self.assertEqual(self.transcribe.await_count, 1)


class RecentContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.collection = AsyncMongoMockClient().test.messages
        self.now = datetime.now(timezone.utc)
        self.message = event(mid=100)
        self.message.text = 'Экс, ответь'
        self.message.voice = None

    async def insert(self, mid, text, *, chat=-101, age=1, thread=None, name='Автор', include_thread=True):
        doc = {'chat_id': chat, 'message_id': mid, 'text': text, 'user_name': name,
               'createdAt': self.now - timedelta(minutes=age)}
        if include_thread:
            doc['message_thread_id'] = thread
        await self.collection.insert_one(doc)

    async def test_recent_context_scoped_bounded_and_in_order(self):
        for mid in range(1, 8):
            await self.insert(mid, f'сообщение {mid}', age=8-mid)
        await self.insert(8, '[Голосовое: распознаётся...]', age=0.8)
        await self.insert(9, '[Голосовое: расшифровка не удалась]', age=0.7)
        await self.insert(10, 'старое', age=30)
        await self.insert(101, 'после запроса', age=0.1)
        await self.insert(11, 'чужой чат', chat=-102, age=0.5)
        result = await recent_context.add_recent_context(self.message, '', self.collection)
        self.assertNotIn('чужой чат', result)
        self.assertNotIn('после запроса', result)
        self.assertNotIn('старое', result)
        self.assertNotIn('распознаётся', result)
        self.assertIn('сообщение 7', result)
        self.assertLess(result.index('сообщение 6'), result.index('сообщение 7'))
        self.assertLessEqual(result.count('Автор:'), 5)
        self.assertLessEqual(len(result), 4000)

    async def test_forum_topic_isolation_and_legacy_exclusion(self):
        self.message.chat.is_forum = True
        self.message.message_thread_id = 7
        await self.insert(1, 'наш топик', thread=7)
        await self.insert(2, 'чужой топик', thread=8)
        await self.insert(3, 'старая запись без топика', include_thread=False)
        result = await recent_context.add_recent_context(self.message, '', self.collection)
        self.assertIn('наш топик', result)
        self.assertNotIn('чужой топик', result)
        self.assertNotIn('старая запись', result)
        self.message.message_thread_id = None
        self.assertEqual(await recent_context.add_recent_context(self.message, '', self.collection), '')

    async def test_reply_photo_context_remains_first_and_history_bounded(self):
        self.message.reply_to_message = SimpleNamespace(message_id=5)
        await self.insert(5, 'ответ на это сообщение')
        await self.insert(6, 'другое обсуждение')
        original = 'Фото в запросе: кот'
        result = await recent_context.add_recent_context(self.message, original, self.collection)
        self.assertTrue(result.startswith(original))
        self.assertIn('другое обсуждение', result)
        self.assertNotIn('ответ на это сообщение', result)

    async def test_full_explicit_context_is_not_trimmed_for_recent_messages(self):
        await self.insert(1, 'недавний разговор')
        original = 'Фото и точный контекст: ' + ('я' * 3750)
        result = await recent_context.add_recent_context(self.message, original, self.collection)
        self.assertTrue(result.startswith(original))
        self.assertLessEqual(len(result), 4000)
        long_original = 'я' * 4000
        self.assertEqual(await recent_context.add_recent_context(self.message, long_original, self.collection), long_original)

    async def test_history_failure_is_safe(self):
        collection = SimpleNamespace(find=MagicMock(side_effect=RuntimeError))
        self.assertEqual(await recent_context.add_recent_context(self.message, 'исходное', collection), 'исходное')

    async def test_explicit_video_reply_receives_same_context(self):
        await self.insert(1, 'недавняя тема')
        msg = self.message
        msg.text = 'Экс, кружок расскажи'
        fake_db = SimpleNamespace(messages=self.collection)
        with patch.object(bender_clip, 'db', fake_db), \
             patch.object(bender_clip, 'get_chat_feature_flags', new=AsyncMock(return_value={'video_notes': True})), \
             patch.object(bender_clip, 'enqueue_video_reply', new_callable=AsyncMock) as enqueue:
            await bender_clip.video_reply(msg)
        self.assertIn('недавняя тема', enqueue.await_args.args[2])

    async def test_choice_passes_history_to_text_and_video(self):
        await self.insert(1, 'недавняя тема')
        database = AsyncMongoMockClient().test
        db = SimpleNamespace(messages=self.collection, personas=database.personas,
                             ex_response_choices=database.ex_response_choices, auth_chats=database.auth_chats)
        await db.auth_chats.insert_one({'chat_id': -101, 'status': 'approved'})
        await db.personas.insert_one({'p_id': 'bender', 'name': 'Бендер', 'emoji': '🤖'})
        menu = SimpleNamespace(message_id=999, chat=self.message.chat, edit_reply_markup=AsyncMock(), delete=AsyncMock())
        flags = {'assistant': True, 'video_notes': True}
        with patch.object(ex_logic, 'db', db), patch.object(response_choice, 'db', db), \
             patch.object(ex_logic, 'is_feature_enabled', new=AsyncMock(return_value=True)), \
             patch.object(ex_logic, 'analyze_intent', new=AsyncMock(return_value=('CHAT', 'ответь'))), \
             patch.object(response_choice, 'get_chat_feature_flags', new=AsyncMock(return_value=flags)):
            for mode in ('text', 'video'):
                self.message.message_id += 1
                self.message.reply.return_value = menu
                await ex_logic.handle_ex_request(self.message)
                doc = await db.ex_response_choices.find_one({'message_id': self.message.message_id})
                self.assertIn('недавняя тема', doc['context'])
                callback = SimpleNamespace(data=f"exfmt:{doc['_id']}:{mode}", message=menu,
                                           from_user=self.message.from_user, bot=self.message.bot, answer=AsyncMock())
                with patch.object(ex_logic, 'generate_chat_response', new_callable=AsyncMock) as text, \
                     patch.object(bender_clip, 'enqueue_video_reply', new_callable=AsyncMock) as video:
                    await response_choice.response_choice_callback(callback)
                self.assertIn('недавняя тема', (text if mode == 'text' else video).await_args.args[2])
