import asyncio
import hashlib
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
from mongomock_motor import AsyncMongoMockClient
from handlers import owner_bulletin_panel as panel, owner_tools_panel as tools
from news_prototype import bulletin_video_candidate as candidate, owner_bulletin, owner_bulletin_sources
from news_prototype import owner_bulletin_video_review as review
from tests.test_bulletin_foundation import response
from tests.test_owner_drafts import SOURCE


class OwnerVideoReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        self.owner = tools.ADMIN_ID
        self.number = 42
        await owner_bulletin_sources.create(self.db, self.owner, self.number, [
            {'url': 'https://t.me/ExampleNews/102', 'title': 'Видео'},
            {'url': 'https://example.com/news', 'title': 'Вторая новость'}])
        async def fetch(url): return {'summary': SOURCE, 'telegram_video_status': 'available'}
        await owner_bulletin_sources.collect(self.db, self.owner, self.number, fetch, AsyncMock())
        await owner_bulletin.prepare(self.db, self.owner, self.number, AsyncMock(side_effect=response))
        self.item = await owner_bulletin.get(self.db, self.owner, self.number)
        self.rev = self.item['revision']
        self.video = b'test candidate video bytes'

    async def acquire(self, db, owner, number, revision, section_id, folder):
        folder.mkdir()
        path = folder / 'source.mp4'; path.write_bytes(self.video)
        return candidate.Candidate(owner, number, revision, section_id, 'https://t.me/ExampleNews/102',
            hashlib.sha256(SOURCE.strip().encode()).hexdigest(), hashlib.sha256(self.video).hexdigest(),
            len(self.video), 12.0, 320, 180, True, path)

    async def test_sends_checked_bytes_only_to_owner_once_without_approval_or_queue(self):
        send = AsyncMock(return_value=55)
        with patch.object(candidate, 'acquire', side_effect=self.acquire) as acquisition:
            results = await asyncio.gather(*(review.deliver(self.db, self.owner, self.number, self.rev, 1, send)
                                             for _ in range(2)))
        self.assertCountEqual(results, ['sent', 'already_started'])
        acquisition.assert_awaited_once()
        send.assert_awaited_once()
        owner, sent, caption = send.await_args.args
        self.assertEqual((owner, sent), (self.owner, self.video))
        self.assertIn('проверь права', caption.lower())
        self.assertIn('не одобрение', caption.lower())
        stored = await self.db.news_owner_bulletin_video_reviews.find_one({})
        self.assertEqual((stored['stage'], stored['message_id'], stored['video_sha256']),
                         ('sent', 55, hashlib.sha256(self.video).hexdigest()))
        self.assertNotIn('path', stored)
        self.assertNotIn('token=', repr(stored))
        self.assertEqual(await owner_bulletin.get(self.db, self.owner, self.number), self.item)
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
        payload = await owner_bulletin.approved_payload(self.db, self.owner, self.number, self.rev)
        self.assertNotIn('video', repr(payload))

    async def test_ambiguous_send_is_terminal_and_no_blind_retry(self):
        send = AsyncMock(side_effect=TimeoutError('secret'))
        with patch.object(candidate, 'acquire', side_effect=self.acquire) as acquisition:
            self.assertEqual(await review.deliver(self.db, self.owner, self.number, self.rev, 1, send), 'uncertain')
            self.assertEqual(await review.deliver(self.db, self.owner, self.number, self.rev, 1, send), 'already_started')
        acquisition.assert_awaited_once(); send.assert_awaited_once()
        stored = await self.db.news_owner_bulletin_video_reviews.find_one({})
        self.assertEqual(stored['stage'], 'uncertain')
        self.assertNotIn('secret', repr(stored))

    async def test_changed_file_or_stale_revision_never_sent(self):
        async def bad(*args):
            result = await self.acquire(*args)
            result.path.write_bytes(b'modified')
            return result
        send = AsyncMock()
        with patch.object(candidate, 'acquire', side_effect=bad):
            self.assertEqual(await review.deliver(self.db, self.owner, self.number, self.rev, 1, send), 'failed')
        send.assert_not_awaited()
        db = AsyncMongoMockClient().test
        with patch.object(candidate, 'acquire', AsyncMock()) as acquire:
            self.assertEqual(await review.deliver(db, self.owner, self.number, self.rev, 1, send), 'stale')
        acquire.assert_not_awaited()
        self.assertEqual(await db.news_owner_bulletin_video_reviews.count_documents({}), 0)

    async def test_preview_button_requires_private_owner_and_second_confirmation(self):
        fake = SimpleNamespace(db=self.db)
        user = SimpleNamespace(id=self.owner)
        message = SimpleNamespace(chat=SimpleNamespace(id=self.owner, type='private'), message_id=321,
            answer=AsyncMock(return_value=SimpleNamespace(message_id=999)), answer_photo=AsyncMock())
        state = SimpleNamespace()
        with patch.object(panel, 'db', fake), patch.object(tools, 'db', fake), \
             patch.object(candidate, 'acquire', AsyncMock()) as acquire:
            await panel.show_section(message, self.owner, self.item, self.item['sections'][0])
            card = await self.db.owner_bulletin_cards.find_one({'section_id': 1})
            sent_markup = message.answer.await_args.kwargs['reply_markup']
            self.assertTrue(any(button.callback_data == f'nb:video:{card["_id"]}'
                                for row in sent_markup.inline_keyboard for button in row))
            callback = SimpleNamespace(from_user=user, message=message, data=f'nb:video:{card["_id"]}', answer=AsyncMock())
            message.message_id = 999
            callback.from_user = SimpleNamespace(id=self.owner + 1)
            await panel.callback_action(callback, state)
            self.assertEqual(await self.db.owner_panel_confirmations.count_documents({}), 0)
            callback.from_user = user
            await panel.callback_action(callback, state)
            confirmation = await self.db.owner_panel_confirmations.find_one({'action': 'review_bulletin_video'})
            self.assertEqual(confirmation['data'], {'number': self.number, 'revision': self.rev, 'section_id': 1})
            acquire.assert_not_awaited()
            self.assertEqual(await self.db.news_owner_bulletin_video_reviews.count_documents({}), 0)

    async def test_confirmed_flow_sends_once_and_does_not_render(self):
        fake = SimpleNamespace(db=self.db)
        user = SimpleNamespace(id=self.owner)
        message = SimpleNamespace(chat=SimpleNamespace(id=self.owner, type='private'), message_id=999,
            answer=AsyncMock(return_value=SimpleNamespace(message_id=1000)), edit_text=AsyncMock(),
            delete=AsyncMock())
        callback = SimpleNamespace(from_user=user, message=message, answer=AsyncMock(), data='')
        state = SimpleNamespace()
        bot = SimpleNamespace(send_video=AsyncMock(return_value=SimpleNamespace(message_id=456)))
        with patch.object(panel, 'db', fake), patch.object(tools, 'db', fake), \
             patch.object(candidate, 'acquire', side_effect=self.acquire) as acquisition:
            await tools.new_confirmation(message, self.owner, 'review_bulletin_video', 'Подтверждаешь?',
                                         number=self.number, revision=self.rev, section_id=1)
            confirmation = await self.db.owner_panel_confirmations.find_one({})
            callback.data = 'owner_tools:run:' + confirmation['_id']
            await tools.action(callback, state, bot)
            await tools.action(callback, state, bot)
            acquisition.assert_awaited_once()
            bot.send_video.assert_awaited_once()
            self.assertEqual(bot.send_video.await_args.args[0], self.owner)
            self.assertEqual(bot.send_video.await_args.args[1].data, self.video)
            self.assertEqual((await self.db.news_owner_bulletin_video_reviews.find_one({}))['stage'], 'sent')
            self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_revision_change_during_acquisition_stops_before_send(self):
        async def acquire_and_edit(*args):
            result = await self.acquire(*args)
            old = self.item['sections'][0]
            text = owner_bulletin.editable_section(old).replace(old['ticker'], 'Исправленная новость')
            self.assertEqual(await owner_bulletin.change_section(self.db, self.owner, self.number,
                                                                 self.rev, 1, text=text), 'saved')
            return result
        send = AsyncMock()
        with patch.object(candidate, 'acquire', side_effect=acquire_and_edit):
            self.assertEqual(await review.deliver(self.db, self.owner, self.number, self.rev, 1, send), 'failed')
        send.assert_not_awaited()
        self.assertEqual((await self.db.news_owner_bulletin_video_reviews.find_one({}))['stage'], 'failed')
