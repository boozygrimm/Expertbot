import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
from mongomock_motor import AsyncMongoMockClient
from handlers import owner_legacy_news as legacy, owner_tools_panel as tools, news_intake, news_owner_panel
from news_prototype import owner_trial


class LegacyPanelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        fake = SimpleNamespace(db=self.db, settings=self.db.settings)
        for module in (legacy, tools, news_intake, news_owner_panel):
            p = patch.object(module, 'db', fake); p.start(); self.addCleanup(p.stop)
        self.env = patch.dict(os.environ, {'NEWS_INTAKE_ENABLED':'1', 'NEWS_RENDER_QUEUE_ENABLED':'1',
            'NEWS_COLLECTION_ENABLED':'0', 'NEWS_EDITOR_ENABLED':'0', 'NEWS_REVIEW_ENABLED':'0'}, clear=False)
        self.env.start(); self.addCleanup(self.env.stop)
        self.user = SimpleNamespace(id=tools.ADMIN_ID)
        self.message = SimpleNamespace(chat=SimpleNamespace(id=tools.ADMIN_ID, type='private'), message_id=35,
            from_user=self.user, answer=AsyncMock(), reply=AsyncMock(), answer_photo=AsyncMock(),
            edit_text=AsyncMock(), reply_to_message=None, text='')
        self.cb = SimpleNamespace(from_user=self.user, message=self.message, answer=AsyncMock(), data='')
        self.state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock(), update_data=AsyncMock(), get_data=AsyncMock())

    async def action(self, data):
        self.cb.data = data
        await legacy.callback_action(self.cb, self.state)

    async def ready(self, title):
        request_id = str(uuid.uuid4())
        candidate_id = 'candidate:' + request_id
        await self.db.news_candidates.insert_one({'_id': candidate_id, 'title':title})
        await self.db.news_render_jobs.insert_one({'_id':request_id, 'request_id':request_id,
            'candidate_id':candidate_id, 'stage':'ready_for_review', 'created_at':datetime.now(timezone.utc)})
        return request_id

    async def test_disabled_cannot_execute_or_change_flags(self):
        with patch.dict(os.environ, {'NEWS_INTAKE_ENABLED':'0'}):
            await self.action('legacynews:home')
            self.assertIn('выключен', self.message.edit_text.await_args.args[0])
            for op in ('add', 'list', 'status', 'ready'):
                await self.action('legacynews:' + op)
                self.assertIn('выключен', self.cb.answer.await_args.args[0])
            self.assertEqual(os.environ['NEWS_RENDER_QUEUE_ENABLED'], '1')
            self.assertEqual(await self.db.news_candidates.count_documents({}), 0)
            self.assertEqual(await self.db.owner_legacy_news_sessions.count_documents({}), 0)

    async def test_nonowner_and_group_are_denied(self):
        self.cb.from_user = SimpleNamespace(id=tools.ADMIN_ID+1)
        await self.action('legacynews:add')
        self.state.set_state.assert_not_awaited()
        self.cb.from_user = self.user
        self.message.chat.type = 'group'
        await self.action('legacynews:ready')
        self.assertEqual(await self.db.owner_legacy_news_sessions.count_documents({}), 0)

    async def test_manual_entry_confirm_and_duplicate_do_not_auto_queue(self):
        self.message.text = 'Текст новости\nИсточник: https://example.com/story\nАкцент: важный факт'
        self.state.get_data.return_value = {'action':'legacy_news'}
        await tools.receive_input(self.message, self.state)
        self.assertEqual(await self.db.news_candidates.count_documents({}), 0)
        confirm = await self.db.owner_panel_confirmations.find_one({'action':'legacy_news'})
        self.assertEqual(confirm['data']['original_message_id'], 35)
        self.cb.data = 'owner_tools:run:' + confirm['_id']
        await tools.action(self.cb, self.state, None)
        await tools.action(self.cb, self.state, None)
        item = await self.db.news_candidates.find_one({})
        self.assertEqual(item['_id'], f'manual:{tools.ADMIN_ID}:35')
        self.assertEqual(item['text'], 'Текст новости')
        self.assertEqual(item['url'], 'https://example.com/story')
        self.assertEqual(item['accent'], 'важный факт')
        self.assertEqual(await self.db.news_candidates.count_documents({}), 1)
        self.assertEqual(await self.db.news_render_jobs.count_documents({}), 0)

    async def test_changed_flags_or_disabled_prevents_manual_save(self):
        self.message.text = 'https://example.com/story'
        self.state.get_data.return_value = {'action':'legacy_news'}
        await tools.receive_input(self.message, self.state)
        confirm = await self.db.owner_panel_confirmations.find_one({})
        self.cb.data = 'owner_tools:run:' + confirm['_id']
        with patch.dict(os.environ, {'NEWS_EDITOR_ENABLED':'1'}):
            await tools.action(self.cb, self.state, None)
        self.assertEqual(await self.db.news_candidates.count_documents({}), 0)
        self.assertIn('изменились', self.message.answer.await_args.args[0])

    async def test_reply_input_uses_original_text_and_accent(self):
        self.message.text = 'важны сроки'
        self.message.reply_to_message = SimpleNamespace(text='Новость из сообщения', caption=None,
                                                        from_user=SimpleNamespace(is_bot=False))
        self.state.get_data.return_value = {'action':'legacy_news'}
        await tools.receive_input(self.message, self.state)
        confirm = await self.db.owner_panel_confirmations.find_one({})
        self.cb.data = 'owner_tools:run:' + confirm['_id']
        await tools.action(self.cb, self.state, None)
        item = await self.db.news_candidates.find_one({})
        self.assertEqual((item['text'], item['accent']), ('Новость из сообщения', 'важны сроки'))

    async def test_digest_selection_confirmation_order_and_single_enqueue(self):
        first, second = await self.ready('Первая'), await self.ready('Вторая')
        await self.action('legacynews:ready')
        session = await self.db.owner_legacy_news_sessions.find_one({})
        token = session['_id']
        idx = {item['id']: i for i, item in enumerate(session['items'])}
        await self.action(f'legacynews:select:{token}:{idx[first]}')
        await self.action(f'legacynews:select:{token}:{idx[second]}')
        await self.action(f'legacynews:build:{token}')
        self.assertEqual(await self.db.news_render_jobs.count_documents({'kind':'digest'}), 0)
        conf = await self.db.owner_panel_confirmations.find_one({'action':'legacy_digest'})
        self.assertEqual(conf['data']['ids'], [first, second])
        self.assertIn('Первая', self.message.answer.await_args.args[0])
        self.cb.data = 'owner_tools:run:' + conf['_id']
        await tools.action(self.cb, self.state, None)
        await tools.action(self.cb, self.state, None)
        digest = await self.db.news_render_jobs.find_one({'kind':'digest'})
        self.assertEqual(digest['inputs'], [first, second])
        self.assertEqual(digest['stage'], 'queued')
        self.assertEqual(await self.db.news_render_jobs.count_documents({'kind':'digest'}), 1)

    async def test_stale_story_and_disabled_queue_do_not_enqueue(self):
        first, second = await self.ready('Первая'), await self.ready('Вторая')
        await self.action('legacynews:ready')
        session = await self.db.owner_legacy_news_sessions.find_one({})
        token = session['_id']
        for i in range(2):
            await self.action(f'legacynews:select:{token}:{i}')
        with patch.dict(os.environ, {'NEWS_RENDER_QUEUE_ENABLED':'0'}):
            await self.action('legacynews:build:' + token)
            self.assertEqual(await self.db.owner_panel_confirmations.count_documents({}), 0)
        await self.action('legacynews:build:' + token)
        conf = await self.db.owner_panel_confirmations.find_one({})
        await self.db.news_render_jobs.update_one({'_id':first}, {'$set': {'stage':'failed'}})
        self.cb.data = 'owner_tools:run:' + conf['_id']
        await tools.action(self.cb, self.state, None)
        self.assertEqual(await self.db.news_render_jobs.count_documents({'kind':'digest'}), 0)

    async def test_expired_selection_and_wrong_message_are_denied(self):
        await self.ready('Один'); await self.ready('Два')
        await self.action('legacynews:ready')
        session = await self.db.owner_legacy_news_sessions.find_one({}); token = session['_id']
        self.message.message_id += 1
        await self.action(f'legacynews:select:{token}:0')
        self.assertIn('устарел', self.cb.answer.await_args.args[0])
        self.message.message_id -= 1
        await self.db.owner_legacy_news_sessions.update_one({'_id':token},
            {'$set':{'expires_at':datetime.now(timezone.utc)-timedelta(seconds=1)}})
        await self.action(f'legacynews:select:{token}:0')
        self.assertIn('устарел', self.cb.answer.await_args.args[0])

    async def test_home_buttons_status_and_list(self):
        with patch.object(owner_trial, 'migrate_panel_defaults', AsyncMock()):
            _, markup = await news_owner_panel.section_content('settings')
        self.assertIn('legacynews:home', [button.callback_data for row in markup.inline_keyboard for button in row])
        await self.db.news_candidates.insert_one({'source_id':'manual', 'owner_id':tools.ADMIN_ID,
                                                   'text':'Ручная новость', 'collected_at':'2026-01-01'})
        await self.action('legacynews:list')
        self.assertIn('Ручная новость', self.message.answer.await_args.args[0])
        await self.action('legacynews:status')
        self.assertIn('пока нет', self.message.answer.await_args.args[0])

    async def test_real_dispatcher_intake_with_confirmation(self):
        import subprocess, sys
        script = r'''
import asyncio, os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiogram import Bot
from aiogram.types import Update
from aiogram.methods import SendMessage
from mongomock_motor import AsyncMongoMockClient
from config import ADMIN_ID
from app_setup import configure_dispatcher
from handlers import owner_legacy_news, owner_tools_panel, news_intake, logger

async def run():
    database = AsyncMongoMockClient().test
    fake = SimpleNamespace(db=database, settings=database.settings, messages=database.messages,
                           )
    bot = Bot('123456:TESTTOKEN')
    sent = []
    async def request(bot_arg, method, **kwargs):
        sent.append(method)
        return True
    env = {'NEWS_INTAKE_ENABLED':'1', 'NEWS_EDITOR_ENABLED':'0', 'NEWS_RENDER_QUEUE_ENABLED':'0',
           'NEWS_REVIEW_ENABLED':'0', 'NEWS_COLLECTION_ENABLED':'0'}
    with patch.dict(os.environ, env), patch.object(owner_legacy_news, 'db', fake), \
         patch.object(owner_tools_panel, 'db', fake), patch.object(news_intake, 'db', fake), \
         patch.object(logger, 'db', fake), \
         patch.object(bot.session, 'make_request', AsyncMock(side_effect=request)):
        dp = configure_dispatcher()
        user = {'id':ADMIN_ID, 'is_bot':False, 'first_name':'Owner'}
        chat = {'id':ADMIN_ID, 'type':'private'}
        async def cb(i, data, message_id=321):
            event = Update.model_validate({'update_id':i, 'callback_query':{
                'id':str(i), 'chat_instance':'owner', 'from':user, 'data':data,
                'message':{'message_id':message_id, 'date':1760000000, 'chat':chat,
                           'from':{'id':123456,'is_bot':True,'first_name':'Bot'}, 'text':'Panel'}}})
            await asyncio.wait_for(dp.feed_update(bot, event), 5)
        await cb(1, 'legacynews:home')
        await cb(2, 'legacynews:add')
        update = Update.model_validate({'update_id':3, 'message':{'message_id':3, 'date':1760000000,
            'chat':chat, 'from':user, 'text':'Новая история\nАкцент: факты'}})
        await asyncio.wait_for(dp.feed_update(bot, update), 5)
        assert await database.news_candidates.count_documents({}) == 0
        record = await database.owner_panel_confirmations.find_one({'action':'legacy_news'})
        assert record and record['data']['original_message_id'] == 3
        await cb(4, 'owner_tools:run:' + record['_id'], 322)
        item = await database.news_candidates.find_one({})
        assert item['text'] == 'Новая история' and item['accent'] == 'факты'
        assert await database.news_render_jobs.count_documents({}) == 0
        assert any(isinstance(m, SendMessage) and 'Сохранить непроверенный' in m.text for m in sent)
        command = Update.model_validate({'update_id':5, 'message':{'message_id':5, 'date':1760000000,
            'chat':chat, 'from':user, 'text':'/news https://example.com/old'}})
        await asyncio.wait_for(dp.feed_update(bot, command), 5)
        assert await database.news_candidates.count_documents({}) == 2
    await bot.session.close()
asyncio.run(run())
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    async def test_digest_confirmation_does_not_run_when_mode_or_queue_turn_off(self):
        await self.ready('Первая'); await self.ready('Вторая')
        await self.action('legacynews:ready')
        session = await self.db.owner_legacy_news_sessions.find_one({})
        for i in range(2):
            await self.action(f'legacynews:select:{session["_id"]}:{i}')
        await self.action(f'legacynews:build:{session["_id"]}')
        conf = await self.db.owner_panel_confirmations.find_one({'action':'legacy_digest'})
        self.cb.data = 'owner_tools:run:' + conf['_id']
        with patch.dict(os.environ, {'NEWS_RENDER_QUEUE_ENABLED':'0'}):
            await tools.action(self.cb, self.state, None)
        self.assertEqual(await self.db.news_render_jobs.count_documents({'kind':'digest'}), 0)
        self.assertIn('выключены', self.message.answer.await_args.args[0])

    async def test_manual_concurrent_confirmations_save_only_once(self):
        import asyncio
        self.message.text = 'https://example.com/story'
        self.state.get_data.return_value = {'action':'legacy_news'}
        await tools.receive_input(self.message, self.state)
        conf = await self.db.owner_panel_confirmations.find_one({'action':'legacy_news'})
        self.cb.data = 'owner_tools:run:' + conf['_id']
        await asyncio.gather(tools.action(self.cb, self.state, None), tools.action(self.cb, self.state, None))
        self.assertEqual(await self.db.news_candidates.count_documents({}), 1)
        self.assertEqual(await self.db.news_render_jobs.count_documents({}), 0)
