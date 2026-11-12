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

    def test_aiogram_reply_keeps_chat_message_and_topic(self):
        from datetime import datetime, timezone
        from aiogram.types import Chat, Message
        message = Message(message_id=42, date=datetime.now(timezone.utc),
                          chat=Chat(id=-1001, type='supergroup'),
                          is_topic_message=True, message_thread_id=7)
        method = message.reply_video_note('test-file-id')
        self.assertEqual(method.chat_id, -1001)
        self.assertEqual(method.reply_parameters.message_id, 42)
        self.assertEqual(method.message_thread_id, 7)
        self.assertEqual(method.video_note, 'test-file-id')

    def test_spoken_text_cap(self):
        result = bender_clip.spoken_text('**Здравствуй!** ' * 35)
        self.assertLessEqual(len(result), 240)
        self.assertNotIn('*', result)
        self.assertTrue(result.endswith(('!', '…')))


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        bender_clip._render_lock = asyncio.Lock()
        self.status = SimpleNamespace(delete=AsyncMock())
        self.message = SimpleNamespace(
            chat=SimpleNamespace(id=-1001, type='supergroup'),
            text='Экс, кружок мнение',
            reply_to_message=SimpleNamespace(text='Исходное сообщение другого человека', caption=None),
            reply=AsyncMock(return_value=self.status),
            reply_video_note=AsyncMock(),
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
        self.message.reply_video_note.assert_awaited_once_with(video_note='file-id')
        self.status.delete.assert_awaited_once()
        self.assertEqual(self.flags.new.call_args.args[0], -1001)

    async def test_separate_group_uses_its_message(self):
        another = SimpleNamespace(chat=SimpleNamespace(id=-1002, type='group'), text='/bender вопрос',
                                  reply_to_message=None, reply=AsyncMock(return_value=self.status),
                                  reply_video_note=AsyncMock())
        await bender_clip.video_reply(self.message)
        await bender_clip.video_reply(another)
        self.message.reply_video_note.assert_awaited_once_with(video_note='file-id')
        another.reply_video_note.assert_awaited_once_with(video_note='file-id')
        self.assertEqual([x.args[0] for x in self.flags.new.call_args_list], [-1001, -1002])

    async def test_feature_off_and_private_do_nothing(self):
        self.flags.new.return_value = {'video_notes': False}
        await bender_clip.video_reply(self.message)
        self.message.reply.assert_not_awaited()
        self.client.render.assert_not_awaited()
        self.message.chat.type = 'private'
        await bender_clip.video_reply(self.message)
        self.client.render.assert_not_awaited()

    async def test_worker_failure_falls_back_to_text(self):
        self.client.render.side_effect = WorkerFailed('internal')
        await bender_clip.video_reply(self.message)
        self.message.reply_video_note.assert_not_awaited()
        self.assertIn('Вот моё мнение.', self.message.reply.await_args_list[-1].args[0])
        self.status.delete.assert_awaited_once()

    async def test_busy_does_not_run_generation(self):
        await bender_clip._render_lock.acquire()
        try:
            await bender_clip.video_reply(self.message)
        finally:
            bender_clip._render_lock.release()
        self.generate.new.assert_not_awaited()
        self.assertIn('другой кружок', self.message.reply.await_args.args[0])
