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


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        bender_clip._render_lock = asyncio.Lock()
        self.status = SimpleNamespace(delete=AsyncMock(), edit_text=AsyncMock())
        self.message = SimpleNamespace(
            chat=SimpleNamespace(id=-1001, type='supergroup'),
            message_id=42, message_thread_id=7,
            bot=SimpleNamespace(send_message=AsyncMock(return_value=self.status), send_video_note=AsyncMock()),
            text='Экс, кружок мнение',
            reply_to_message=SimpleNamespace(text='Исходное сообщение другого человека', caption=None),
        )
        self.flags = patch.object(bender_clip, 'get_chat_feature_flags', new=AsyncMock(return_value={'video_notes': True}))
        self.flags.start()
        self.addCleanup(self.flags.stop)
        self.env = patch.dict(os.environ, {'BENDER_WORKER_URL': 'https://worker.example', 'BENDER_WORKER_KEY': 'secret'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.persona = patch.object(bender_clip.db.personas, 'find_one', new=AsyncMock(return_value={'p_id': 'bender', 'prompt': 'робот'}))
        self.persona.start()
        self.addCleanup(self.persona.stop)
        self.generate = patch.object(bender_clip, 'generate_with_retry', new=AsyncMock(return_value='Вот моё мнение.'))
        self.generate.start()
        self.addCleanup(self.generate.stop)
        self.client = MagicMock()
        self.client.render = AsyncMock(return_value='file-id')
        self.factory = patch.object(bender_clip, 'BenderWorkerClient', return_value=self.client)
        self.factory.start()
        self.addCleanup(self.factory.stop)

    async def test_answers_request_and_includes_reply_context(self):
        await bender_clip.video_reply(self.message)
        self.assertIn('Исходное сообщение другого человека', self.generate.new.call_args.args[0])
        self.client.render.assert_awaited_once_with('Вот моё мнение.')
        self.message.bot.send_video_note.assert_awaited_once_with(video_note='file-id', **bender_clip.reply_options(self.message))
        self.status.delete.assert_awaited_once()
        self.assertEqual(self.flags.new.call_args.args[0], -1001)

    async def test_separate_group_uses_its_message(self):
        another = SimpleNamespace(chat=SimpleNamespace(id=-1002, type='group'), text='/bender вопрос',
                                  message_id=43, message_thread_id=None, reply_to_message=None,
                                  bot=SimpleNamespace(send_message=AsyncMock(return_value=self.status), send_video_note=AsyncMock()))
        await bender_clip.video_reply(self.message)
        await bender_clip.video_reply(another)
        self.message.bot.send_video_note.assert_awaited_once_with(video_note='file-id', **bender_clip.reply_options(self.message))
        another.bot.send_video_note.assert_awaited_once_with(video_note='file-id', **bender_clip.reply_options(another))
        self.assertEqual([x.args[0] for x in self.flags.new.call_args_list], [-1001, -1002])

    async def test_feature_off_and_private_do_nothing(self):
        self.flags.new.return_value = {'video_notes': False}
        await bender_clip.video_reply(self.message)
        self.message.bot.send_message.assert_not_awaited()
        self.client.render.assert_not_awaited()
        self.message.chat.type = 'private'
        await bender_clip.video_reply(self.message)
        self.client.render.assert_not_awaited()

    async def test_rotates_one_message_and_survives_edit_error(self):
        self.status.edit_text.side_effect = [RuntimeError('temporary'), None]
        calls = 0
        async def fake_sleep(interval):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise asyncio.CancelledError()
        with patch.object(bender_clip.random, 'choice', side_effect=lambda choices: choices[0]):
            with self.assertRaises(asyncio.CancelledError):
                await bender_clip.rotate_status(self.status, previous=bender_clip.STATUS_MESSAGES[0],
                                                interval=1, sleep=fake_sleep)
        self.assertEqual(self.status.edit_text.await_count, 2)
        self.assertEqual(self.status.edit_text.await_args_list[0].args[0], bender_clip.STATUS_MESSAGES[1])
        self.assertEqual(self.status.edit_text.await_args_list[1].args[0], bender_clip.STATUS_MESSAGES[1])

    async def test_status_choice_avoids_repeat(self):
        previous = bender_clip.STATUS_MESSAGES[0]
        with patch.object(bender_clip.random, 'choice', side_effect=lambda choices: choices[0]) as choice:
            self.assertNotEqual(bender_clip.choose_status(previous), previous)
            self.assertNotIn(previous, choice.call_args.args[0])
        self.assertGreaterEqual(len(bender_clip.STATUS_MESSAGES), 12)

    async def test_first_status_is_random_and_shared_with_rotator(self):
        selected = bender_clip.STATUS_MESSAGES[-1]
        with patch.object(bender_clip, 'choose_status', return_value=selected), \
             patch.object(bender_clip, 'rotate_status', new=AsyncMock()) as rotation:
            await bender_clip.video_reply(self.message)
        self.assertEqual(self.message.bot.send_message.await_args_list[0].kwargs['text'], selected)
        rotation.assert_awaited_once_with(self.status, previous=selected)

    async def test_status_task_is_cancelled_before_deletion(self):
        running = asyncio.Event()
        stopped = asyncio.Event()
        async def rotation(status, **kwargs):
            running.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        async def render(text):
            await running.wait()
            return 'file-id'
        self.client.render.side_effect = render
        async def delete():
            self.assertTrue(stopped.is_set())
        self.status.delete.side_effect = delete
        with patch.object(bender_clip, 'rotate_status', side_effect=rotation):
            await bender_clip.video_reply(self.message)
        self.assertTrue(stopped.is_set())
        self.status.delete.assert_awaited_once()

    async def test_status_task_failure_does_not_prevent_cleanup(self):
        task_finished = asyncio.Event()
        async def broken_rotation(status, **kwargs):
            task_finished.set()
            raise RuntimeError('edit worker failed')
        async def render(text):
            await task_finished.wait()
            return 'file-id'
        self.client.render.side_effect = render
        with patch.object(bender_clip, 'rotate_status', side_effect=broken_rotation):
            await bender_clip.video_reply(self.message)
        self.status.delete.assert_awaited_once()
        self.message.bot.send_video_note.assert_awaited_once_with(video_note='file-id', **bender_clip.reply_options(self.message))

    async def test_worker_failure_falls_back_to_text(self):
        self.client.render.side_effect = WorkerFailed('internal')
        await bender_clip.video_reply(self.message)
        self.message.bot.send_video_note.assert_not_awaited()
        self.assertIn('Вот моё мнение.', self.message.bot.send_message.await_args_list[-1].kwargs['text'])
        self.status.delete.assert_awaited_once()

    async def test_busy_does_not_run_generation(self):
        await bender_clip._render_lock.acquire()
        try:
            await bender_clip.video_reply(self.message)
        finally:
            bender_clip._render_lock.release()
        self.generate.new.assert_not_awaited()
        self.assertIn('другой кружок', self.message.bot.send_message.await_args.kwargs['text'])
