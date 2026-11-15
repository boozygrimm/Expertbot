import asyncio
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from bender_triggers import parse_video_request
from bender_worker_client import WorkerFailed
from handlers import admin_panel, bender_clip


class TriggerTests(unittest.TestCase):
    def test_parse_triggers(self):
        for text, result in [('/bender привет', 'привет'), ('/bender@TestBot тема', 'тема'),
                             ('Экс, кружок мнение', 'мнение'), ('Эксперт кружок', ''),
                             ('Экс мнение', None), ('экскружок привет', None),
                             ('/benderish', None)]:
            with self.subTest(text=text):
                self.assertEqual(parse_video_request(text), result)

    def test_feature_default_and_detection(self):
        self.assertFalse(admin_panel.FEATURES['video_notes']['default'])
        def message(text):
            return SimpleNamespace(chat=SimpleNamespace(type='supergroup'), text=text,
                                   voice=None, video_note=None, reply_to_message=None,
                                   bot=SimpleNamespace(id=123))
        self.assertEqual(admin_panel.detect_message_feature(message('Экс, кружок тема')), 'video_notes')
        self.assertEqual(admin_panel.detect_message_feature(message('/bender тема')), 'video_notes')
        self.assertEqual(admin_panel.detect_message_feature(message('Экс мнение')), 'assistant')

    def test_reply_options_keep_topic_and_allow_missing_command(self):
        message = SimpleNamespace(chat=SimpleNamespace(id=-1001), message_id=42, message_thread_id=7)
        options = bender_clip.reply_options(message)
        self.assertEqual(options['chat_id'], -1001)
        self.assertEqual(options['message_thread_id'], 7)
        self.assertEqual(options['reply_parameters'].message_id, 42)
        self.assertTrue(options['reply_parameters'].allow_sending_without_reply)

    def test_spoken_text_cap(self):
        result = bender_clip.spoken_text('**Здравствуй!** ' * 70)
        self.assertLessEqual(len(result), 480)
        self.assertNotIn('*', result)
        self.assertTrue(result.endswith(('!', '…')))


class FakeQueue:
    def __init__(self):
        self.items = []
        self.result = 'accepted'

    async def enqueue(self, item):
        if self.result == 'accepted':
            self.items.append(item.copy())
        return self.result

    async def update(self, item_id, **fields):
        assert self.items[0]['id'] == item_id
        self.items[0].update(fields)

    async def remove(self, item_id):
        assert self.items[0]['id'] == item_id
        self.items.pop(0)


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.status = SimpleNamespace(message_id=55, delete=AsyncMock())
        self.bot = SimpleNamespace(send_message=AsyncMock(return_value=self.status),
                                   edit_message_text=AsyncMock(), delete_message=AsyncMock(),
                                   send_video_note=AsyncMock())
        self.message = SimpleNamespace(
            chat=SimpleNamespace(id=-1001, type='supergroup'),
            message_id=42, message_thread_id=7, bot=self.bot,
            text='Экс, кружок мнение',
            reply_to_message=SimpleNamespace(text='Исходное сообщение другого человека', caption=None),
        )
        self.queue = FakeQueue()
        patches = [
            patch.object(bender_clip, 'video_queue', self.queue),
            patch.object(bender_clip, 'add_recent_context', new=AsyncMock(side_effect=lambda message, context, messages: context)),
            patch.object(bender_clip, 'get_chat_feature_flags', new=AsyncMock(return_value={'video_notes': True})),
            patch.dict(os.environ, {'BENDER_WORKER_URL': 'https://worker.example', 'BENDER_WORKER_KEY': 'secret'}),
            patch.object(bender_clip.db.personas, 'find_one', new=AsyncMock(return_value={'p_id': 'bender', 'prompt': 'робот'})),
            patch.object(bender_clip, 'generate_with_retry', new=AsyncMock(
                return_value='{"speech":"Вот моё мнение.","sign":"БЕНДЕР В ДЕЛЕ"}')),
        ]
        self.generate = patches[-1].new
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.client = MagicMock()
        self.client.render = AsyncMock(return_value='file-id')
        self.client.resume = AsyncMock(return_value='file-id')
        self.factory = patch.object(bender_clip, 'BenderWorkerClient', return_value=self.client)
        self.factory.start()
        self.addCleanup(self.factory.stop)

    async def submit(self):
        await bender_clip.video_reply(self.message)
        self.assertEqual(len(self.queue.items), 1)
        return self.queue.items[0].copy()

    async def test_queues_then_processes_context_and_topic(self):
        item = await self.submit()
        self.generate.assert_not_awaited()
        self.assertEqual(item['context'], 'Исходное сообщение другого человека')
        self.assertEqual(item['stage'], 'queued')
        await bender_clip.process_queued_video(self.queue, self.bot, item)
        self.assertIn(item['context'], self.generate.await_args.args[0])
        self.client.render.assert_awaited_once_with('Вот моё мнение.', request_id=item['request_id'],
                                                   sign='БЕНДЕР В ДЕЛЕ')
        opts = bender_clip.stored_reply_options(item)
        self.bot.send_video_note.assert_awaited_once_with(video_note='file-id', **opts)
        self.assertTrue(opts['reply_parameters'].allow_sending_without_reply)
        self.assertEqual(opts['message_thread_id'], 7)
        self.assertEqual(self.queue.items, [])

    async def test_generated_sign_is_separate_and_survives_busy_retry(self):
        from bender_worker_client import WorkerBusy
        item = await self.submit()
        self.generate.return_value = '{"speech":"Вот моё мнение.","sign":"Гравитация в отпуске"}'
        self.client.render.side_effect = [WorkerBusy(), 'file-id']
        await bender_clip.process_queued_video(self.queue, self.bot, item)
        self.assertEqual(self.queue.items[0]['answer'], 'Вот моё мнение.')
        self.assertEqual(self.queue.items[0]['sign'], 'ГРАВИТАЦИЯ В ОТПУСКЕ')
        self.queue.items[0]['retry_after'] = 0
        await bender_clip.process_queued_video(self.queue, self.bot, self.queue.items[0].copy())
        self.assertEqual(self.generate.await_count, 1)
        self.assertEqual(self.client.render.await_count, 2)
        for call in self.client.render.await_args_list:
            self.assertEqual(call.args[0], 'Вот моё мнение.')
            self.assertEqual(call.kwargs['sign'], 'ГРАВИТАЦИЯ В ОТПУСКЕ')
            self.assertEqual(call.kwargs['request_id'], item['request_id'])

    async def test_invalid_sign_retries_only_sign_and_keeps_speech(self):
        item = await self.submit()
        self.generate.side_effect = ['{"speech":"Мой ответ.","sign":"😀"}', 'КОСМИЧЕСКИЙ БОСС']
        with self.assertLogs(bender_clip.logger, level='WARNING') as logs:
            await bender_clip.process_queued_video(self.queue, self.bot, item)
        self.assertIn('не вернула подходящую надпись', logs.output[0])
        self.assertNotIn('Мой ответ', ' '.join(logs.output))
        self.assertEqual(self.generate.await_count, 2)
        self.assertTrue(self.generate.await_args.kwargs['use_fast_model'])
        self.client.render.assert_awaited_once_with('Мой ответ.', request_id=item['request_id'],
                                                   sign='КОСМИЧЕСКИЙ БОСС')

    async def test_recovered_sign_is_preserved_on_worker_busy(self):
        from bender_worker_client import WorkerBusy
        item = await self.submit()
        self.generate.side_effect = ['{"speech":"Мой ответ.","sign":"😀"}', 'КОСМИЧЕСКИЙ БОСС']
        self.client.render.side_effect = [WorkerBusy(), 'file-id']
        await bender_clip.process_queued_video(self.queue, self.bot, item)
        self.assertEqual(self.queue.items[0]['answer'], 'Мой ответ.')
        self.assertEqual(self.queue.items[0]['sign'], 'КОСМИЧЕСКИЙ БОСС')
        self.queue.items[0]['retry_after'] = 0
        await bender_clip.process_queued_video(self.queue, self.bot, self.queue.items[0].copy())
        self.assertEqual(self.generate.await_count, 2)
        self.assertEqual(self.client.render.await_count, 2)
        for call in self.client.render.await_args_list:
            self.assertEqual(call.args[0], 'Мой ответ.')
            self.assertEqual(call.kwargs['request_id'], item['request_id'])
            self.assertEqual(call.kwargs['sign'], 'КОСМИЧЕСКИЙ БОСС')

    async def test_sign_retry_failure_uses_safe_fallback(self):
        for failure in (asyncio.TimeoutError(), OSError('network'), '😀'):
            with self.subTest(failure=type(failure).__name__):
                item = await self.submit()
                self.generate.reset_mock()
                self.client.render.reset_mock()
                self.generate.side_effect = ['{"speech":"Мой ответ.","sign":"😀"}', failure]
                with self.assertLogs(bender_clip.logger, level='WARNING') as logs:
                    await bender_clip.process_queued_video(self.queue, self.bot, item)
                self.assertIn('Использована запасная надпись', ' '.join(logs.output))
                self.client.render.assert_awaited_once_with('Мой ответ.', request_id=item['request_id'],
                                                           sign=bender_clip.SIGN_FALLBACK)

    async def test_sign_retry_has_a_hard_deadline(self):
        async def slow(*args, **kwargs):
            await asyncio.sleep(2)
            return 'СЛИШКОМ ПОЗДНО'

        with patch.object(bender_clip, 'SIGN_RETRY_TIMEOUT', 0.02), \
             patch.object(bender_clip, 'generate_with_retry', new=AsyncMock(side_effect=slow)):
            result = await asyncio.wait_for(bender_clip.recover_missing_sign('Мой ответ.'), 1)
        self.assertEqual(result, bender_clip.SIGN_FALLBACK)

    async def test_no_extra_sign_request_for_voice(self):
        item = await self.submit()
        self.queue.items[0]['kind'] = 'voice'
        item['kind'] = 'voice'
        self.generate.return_value = 'Мой голосовой ответ.'
        self.client.render_voice = AsyncMock(return_value='voice-id')
        self.bot.send_voice = AsyncMock()
        await bender_clip.process_queued_video(self.queue, self.bot, item)
        self.assertEqual(self.generate.await_count, 1)
        self.client.render_voice.assert_awaited_once_with('Мой голосовой ответ.', request_id=item['request_id'])

    async def test_distinct_chats_are_queued(self):
        await self.submit()
        self.message.chat.id = -1002
        self.message.message_id = 43
        await bender_clip.video_reply(self.message)
        self.assertEqual([x['chat_id'] for x in self.queue.items], [-1001, -1002])
        self.assertNotEqual(self.queue.items[0]['request_id'], self.queue.items[1]['request_id'])

    async def test_full_queue_gives_fun_message(self):
        self.queue.result = 'full'
        await bender_clip.video_reply(self.message)
        self.assertFalse(self.queue.items)
        self.status.delete.assert_awaited_once()
        self.assertIn(self.bot.send_message.await_args.kwargs['text'], bender_clip.FULL_MESSAGES)

    async def test_feature_off_and_private(self):
        with patch.object(bender_clip, 'get_chat_feature_flags', new=AsyncMock(return_value={'video_notes': False})):
            await bender_clip.video_reply(self.message)
        self.message.chat.type = 'private'
        await bender_clip.video_reply(self.message)
        self.assertFalse(self.queue.items)

    async def test_confirmed_busy_keeps_item_and_answer(self):
        from bender_worker_client import WorkerBusy
        item = await self.submit()
        self.client.render.side_effect = WorkerBusy()
        self.assertTrue(await bender_clip.process_queued_video(self.queue, self.bot, item))
        saved = self.queue.items[0]
        self.assertEqual(saved['stage'], 'ready')
        self.assertEqual(saved['answer'], 'Вот моё мнение.')
        self.assertEqual(saved['request_id'], item['request_id'])
        self.bot.send_video_note.assert_not_awaited()
        self.assertTrue(any(call.kwargs['text'] in bender_clip.BUSY_MESSAGES for call in self.bot.edit_message_text.await_args_list))

    async def test_busy_retries_same_uuid_without_regenerating(self):
        from bender_worker_client import WorkerBusy
        item = await self.submit()
        self.client.render.side_effect = [WorkerBusy(), 'file-id']
        await bender_clip.process_queued_video(self.queue, self.bot, item)
        self.queue.items[0]['retry_after'] = 0
        await bender_clip.process_queued_video(self.queue, self.bot, self.queue.items[0].copy())
        self.assertEqual(self.generate.await_count, 1)
        self.assertEqual(self.client.render.await_count, 2)
        self.assertTrue(all(call.kwargs['request_id'] == item['request_id']
                            for call in self.client.render.await_args_list))
        self.bot.send_video_note.assert_awaited_once()

    async def test_send_error_is_not_retried(self):
        item = await self.submit()
        self.bot.send_video_note.side_effect = OSError('network failure')
        await bender_clip.process_queued_video(self.queue, self.bot, item)
        self.assertEqual(self.bot.send_video_note.await_count, 1)
        self.assertFalse(self.queue.items)
        self.assertIn('возможно, он уже пришёл', self.bot.send_message.await_args.kwargs['text'])

    async def test_recovery_polls_existing_job_without_resubmission(self):
        item = await self.submit()
        self.queue.items[0].update(stage='submitting', answer='Сохранённый ответ')
        await bender_clip.process_queued_video(self.queue, self.bot, self.queue.items[0].copy())
        self.client.render.assert_not_awaited()
        self.client.resume.assert_awaited_once_with(item['request_id'])
        self.bot.send_video_note.assert_awaited_once()
        self.generate.assert_not_awaited()

    async def test_uncertain_send_is_not_repeated_after_restart(self):
        await self.submit()
        self.queue.items[0].update(stage='sending', answer='Ответ', file_id='file-id')
        await bender_clip.process_queued_video(self.queue, self.bot, self.queue.items[0].copy())
        self.bot.send_video_note.assert_not_awaited()
        self.assertIn('возможно, он уже пришёл', self.bot.send_message.await_args.kwargs['text'])
        self.assertFalse(self.queue.items)

    async def test_uncertain_storage_sends_saved_video_once_to_original_topic(self):
        from bender_worker_client import WorkerUploadUncertain
        item = await self.submit()
        self.client.render.side_effect = WorkerUploadUncertain()
        self.client.fetch_uncertain_video = AsyncMock(return_value=b'video')
        async def send(**kwargs):
            self.assertEqual(self.queue.items[0]['stage'], 'sending')
        self.bot.send_video_note.side_effect = send
        await bender_clip.process_queued_video(self.queue, self.bot, item)
        self.client.render.assert_awaited_once()
        self.client.fetch_uncertain_video.assert_awaited_once_with(item['request_id'])
        sent = self.bot.send_video_note.await_args.kwargs
        self.assertEqual(sent['video_note'].data, b'video')
        self.assertEqual(sent['chat_id'], -1001)
        self.assertEqual(sent['message_thread_id'], 7)
        self.assertEqual(sent['reply_parameters'].message_id, 42)
        self.assertFalse(self.queue.items)

    async def test_fallback_download_failure_never_sends_video(self):
        from bender_worker_client import WorkerUploadUncertain, WorkerUnavailable
        item = await self.submit()
        self.client.render.side_effect = WorkerUploadUncertain()
        self.client.fetch_uncertain_video = AsyncMock(side_effect=WorkerUnavailable())
        await bender_clip.process_queued_video(self.queue, self.bot, item)
        self.bot.send_video_note.assert_not_awaited()
        self.assertIn('не смог получить', self.bot.send_message.await_args.kwargs['text'])
        self.assertFalse(self.queue.items)

    async def test_fallback_recovery_and_ambiguous_send_never_repeat(self):
        item = await self.submit()
        self.queue.items[0].update(stage='fallback_ready', answer='Ответ')
        self.client.fetch_uncertain_video = AsyncMock(return_value=b'video')
        self.bot.send_video_note.side_effect = OSError('connection lost')
        await bender_clip.process_queued_video(self.queue, self.bot, self.queue.items[0].copy())
        self.client.render.assert_not_awaited()
        self.bot.send_video_note.assert_awaited_once()
        self.assertIn('возможно, он уже пришёл', self.bot.send_message.await_args.kwargs['text'])
        self.assertFalse(self.queue.items)

    async def test_failed_worker_falls_back_to_text(self):
        item = await self.submit()
        self.client.render.side_effect = WorkerFailed('internal')
        await bender_clip.process_queued_video(self.queue, self.bot, item)
        self.bot.send_video_note.assert_not_awaited()
        self.assertIn('Вот моё мнение.', self.bot.send_message.await_args.kwargs['text'])
        self.assertFalse(self.queue.items)

    async def test_unknown_worker_result_never_resubmits(self):
        from bender_worker_client import WorkerUnavailable
        item = await self.submit()
        self.queue.items[0].update(stage='submitting', answer='Ответ')
        self.client.resume.side_effect = WorkerUnavailable('unknown')
        await bender_clip.process_queued_video(self.queue, self.bot, self.queue.items[0].copy())
        self.client.render.assert_not_awaited()
        self.assertIn('не знаю, готов ли кружок', self.bot.send_message.await_args.kwargs['text'])
        self.assertFalse(self.queue.items)

    async def test_rotation_survives_telegram_edit_failure(self):
        item = await self.submit()
        self.bot.edit_message_text.reset_mock()
        self.bot.edit_message_text.side_effect = [OSError('network'), None]
        sleeps = 0
        async def fake_sleep(_):
            nonlocal sleeps
            sleeps += 1
            if sleeps == 3:
                raise asyncio.CancelledError()
        with patch.object(bender_clip.asyncio, 'sleep', side_effect=fake_sleep):
            with self.assertRaises(asyncio.CancelledError):
                await bender_clip._queue_rotation(self.bot, item, bender_clip.STATUS_MESSAGES[0])
        self.assertEqual(self.bot.edit_message_text.await_count, 2)

    async def test_status_choice_avoids_repeat(self):
        previous = bender_clip.STATUS_MESSAGES[0]
        with patch.object(bender_clip.random, 'choice', side_effect=lambda choices: choices[0]):
            self.assertNotEqual(bender_clip.choose_status(previous), previous)
        self.assertGreaterEqual(len(bender_clip.STATUS_MESSAGES), 12)
        self.assertGreaterEqual(len(bender_clip.QUEUE_MESSAGES), 5)
