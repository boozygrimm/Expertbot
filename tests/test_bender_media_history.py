import asyncio
import os
import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from mongomock_motor import AsyncMongoMockClient
from bender_worker_client import WorkerUploadUncertain
from handlers import audio_transcription, bender_clip, recent_context, voice_context
from tests.test_bender_clip import FakeQueue


class MediaHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database = AsyncMongoMockClient().test
        self.db = SimpleNamespace(messages=self.database.messages,
                                  personas=SimpleNamespace(find_one=AsyncMock(return_value={'prompt': 'Бендер'})))
        self.queue = FakeQueue()
        self.bot = SimpleNamespace(send_message=AsyncMock(), edit_message_text=AsyncMock(),
                                   delete_message=AsyncMock(), send_voice=AsyncMock(), send_video_note=AsyncMock())
        self.client = MagicMock(render=AsyncMock(return_value='id'), render_voice=AsyncMock(return_value='id'),
                                fetch_uncertain_video=AsyncMock(return_value=b'video'),
                                fetch_uncertain_voice=AsyncMock(return_value=b'OggS'))
        self.generate = AsyncMock(return_value='{"speech":"Привет, земляне!","sign":"КОСМОС"}')
        for p in (patch.object(bender_clip, 'db', self.db),
                  patch.object(audio_transcription, 'db', self.db),
                  patch.object(bender_clip, 'BenderWorkerClient', return_value=self.client),
                  patch.object(bender_clip, 'generate_with_retry', self.generate)):
            p.start(); self.addCleanup(p.stop)

    def item(self, kind, mid=12):
        return {'id': f'-1001:{mid}', 'request_id': str(uuid.uuid4()), 'kind': kind, 'stage': 'queued',
                'chat_id': -1001, 'message_id': mid, 'thread_id': 77, 'status_message_id': None,
                'query': 'привет', 'context': 'пользователь'}

    async def test_confirmed_file_id_and_direct_file_save_voice_and_video(self):
        for kind in ('voice', 'video'):
            for uncertain in (False, True):
                with self.subTest(kind=kind, uncertain=uncertain):
                    self.queue.items.clear()
                    item = self.item(kind); self.queue.items.append(item.copy())
                    sender = self.bot.send_voice if kind == 'voice' else self.bot.send_video_note
                    sender.reset_mock(); sender.return_value = SimpleNamespace(message_id=100 if kind == 'voice' else 101)
                    render = self.client.render_voice if kind == 'voice' else self.client.render
                    render.reset_mock(); render.side_effect = WorkerUploadUncertain() if uncertain else None
                    await self.database.messages.delete_many({})
                    await bender_clip.process_queued_video(self.queue, self.bot, item.copy())
                    sender.assert_awaited_once()
                    self.assertEqual(self.queue.items, [])
                    doc = await self.database.messages.find_one({'chat_id': -1001, 'message_id': sender.return_value.message_id})
                    self.assertEqual(doc['user_name'], '🤖 Бендер')
                    self.assertEqual(doc['message_thread_id'], 77)
                    self.assertEqual(doc['audio_transcript'], 'Привет, земляне!')
                    self.assertEqual(doc['audio_source'], 'tts_input')
                    self.assertEqual(doc['audio_status'], 'ready')
                    self.assertIn('Голосовое' if kind == 'voice' else 'Кружок', doc['text'])
                    self.assertIn('Привет, земляне!', doc['text'])
                    self.assertNotIn('КОСМОС', doc['text'])
                    self.assertIsInstance(doc['createdAt'], datetime)

    async def test_saved_text_is_used_for_direct_reply_and_recent_history_without_groq(self):
        item = self.item('voice'); self.queue.items.append(item.copy())
        self.bot.send_voice.return_value = SimpleNamespace(message_id=30)
        await bender_clip.process_queued_video(self.queue, self.bot, item)
        source = SimpleNamespace(chat=SimpleNamespace(id=-1001), message_id=30, voice=SimpleNamespace(file_id='id'),
                                 video_note=None, bot=SimpleNamespace(download=AsyncMock()))
        reply = SimpleNamespace(chat=SimpleNamespace(id=-1001, is_forum=True), message_id=31,
                                message_thread_id=77, reply_to_message=source, reply=AsyncMock())
        with patch.object(voice_context, 'is_feature_enabled', new=AsyncMock(return_value=True)), \
             patch.object(audio_transcription, 'transcribe_audio_groq', new_callable=AsyncMock) as transcribe:
            explicit = await voice_context.add_voice_reply_context(reply, '')
            self.assertIn('Привет, земляне!', explicit)
            self.assertIn('текст для озвучки', explicit)
            transcribe.assert_not_awaited()
            source.bot.download.assert_not_awaited()
        reply.reply_to_message = None
        recent = await recent_context.add_recent_context(reply, '', self.db.messages)
        self.assertIn('🤖 Бендер', recent)
        self.assertIn('Привет, земляне!', recent)
        reply.message_thread_id = 78
        self.assertEqual(await recent_context.add_recent_context(reply, '', self.db.messages), '')

    async def test_legacy_video_queue_item_is_saved_after_recovery(self):
        item = self.item('video')
        item.pop('kind')
        item.update(stage='file_ready', answer='Сохранённый ответ', file_id='video-id')
        self.queue.items.append(item.copy())
        self.bot.send_video_note.return_value = SimpleNamespace(message_id=90)
        await bender_clip.process_queued_video(self.queue, self.bot, item)
        doc = await self.db.messages.find_one({'message_id': 90})
        self.assertEqual(doc['audio_transcript'], 'Сохранённый ответ')
        self.assertIn('Кружок Бендера', doc['text'])
        self.bot.send_video_note.assert_awaited_once()
        self.assertEqual(self.queue.items, [])

    async def test_database_failure_does_not_repeat_or_report_failed_delivery(self):
        for kind in ('voice', 'video'):
            with self.subTest(kind=kind):
                item = self.item(kind); self.queue.items.append(item.copy())
                sender = self.bot.send_voice if kind == 'voice' else self.bot.send_video_note
                sender.reset_mock(); sender.return_value = SimpleNamespace(message_id=88)
                with patch.object(self.db.messages, 'update_one', new=AsyncMock(side_effect=OSError('db down'))):
                    await bender_clip.process_queued_video(self.queue, self.bot, item)
                sender.assert_awaited_once()
                self.assertEqual(self.queue.items, [])
                self.bot.send_message.assert_not_awaited()

    async def test_ambiguous_send_never_records_or_repeats(self):
        for kind in ('voice', 'video'):
            with self.subTest(kind=kind):
                item = self.item(kind); self.queue.items.append(item.copy())
                sender = self.bot.send_voice if kind == 'voice' else self.bot.send_video_note
                sender.reset_mock(); sender.side_effect = OSError('lost')
                await bender_clip.process_queued_video(self.queue, self.bot, item)
                sender.assert_awaited_once()
                self.assertEqual(await self.db.messages.count_documents({}), 0)
                self.assertEqual(self.queue.items, [])
                sender.side_effect = None

    async def test_recovered_sending_does_not_record_unconfirmed_or_resend(self):
        item = self.item('voice'); item.update(stage='sending', answer='Ответ')
        self.queue.items.append(item.copy())
        await bender_clip.process_queued_video(self.queue, self.bot, item)
        self.bot.send_voice.assert_not_awaited()
        self.assertEqual(await self.db.messages.count_documents({}), 0)

    async def test_save_is_idempotent_and_does_not_overwrite_original(self):
        item = self.item('video'); item['answer'] = 'Первый текст'
        sent = SimpleNamespace(message_id=42)
        await bender_clip._save_sent_media_history(item, sent)
        item['answer'] = 'Другой текст'
        await bender_clip._save_sent_media_history(item, sent)
        docs = await self.db.messages.find({}).to_list(length=10)
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]['audio_transcript'], 'Первый текст')

    async def test_no_invalid_message_id_or_empty_answer(self):
        item = self.item('voice'); item['answer'] = 'текст'
        for value in (None, 0, True, '42'):
            await bender_clip._save_sent_media_history(item, SimpleNamespace(message_id=value))
        item['answer'] = ''
        await bender_clip._save_sent_media_history(item, SimpleNamespace(message_id=42))
        self.assertEqual(await self.db.messages.count_documents({}), 0)
