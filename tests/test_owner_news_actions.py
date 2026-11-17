import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
from mongomock_motor import AsyncMongoMockClient
from handlers import owner_news_actions as news, owner_tools_panel as tools, news_owner_drafts
from news_prototype import owner_drafts, owner_program, owner_trial
from tests.test_owner_drafts import DRAFT, SOURCE


class NewsActionsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        fake = SimpleNamespace(db=self.db)
        for module in (news, tools, news_owner_drafts):
            p = patch.object(module, 'db', fake); p.start(); self.addCleanup(p.stop)
        p = patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)); p.start(); self.addCleanup(p.stop)
        self.user = SimpleNamespace(id=news.ADMIN_ID)
        self.message = SimpleNamespace(chat=SimpleNamespace(id=news.ADMIN_ID, type='private'), message_id=321,
            answer=AsyncMock(), answer_photo=AsyncMock(), reply=AsyncMock(), edit_text=AsyncMock())
        self.callback = SimpleNamespace(from_user=self.user, message=self.message, answer=AsyncMock(), data='')
        self.state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock(), update_data=AsyncMock(), get_data=AsyncMock())

    async def ready(self, number):
        await owner_drafts.create(self.db, news.ADMIN_ID, number, f'https://t.me/ExampleNews/{number}', '')
        await owner_drafts.process_new(self.db, news.ADMIN_ID, number, AsyncMock(return_value={'summary': SOURCE}),
                                       AsyncMock(return_value=DRAFT))
        return await owner_drafts.get(self.db, news.ADMIN_ID, number)

    async def action(self, data):
        self.callback.data = data
        await news.callback_action(self.callback, self.state)

    async def test_card_requires_current_revision_owner_and_confirmation(self):
        item = await self.ready(12)
        await news.card(self.message, news.ADMIN_ID, 'draft', item)
        buttons = self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard
        approve = next(b.callback_data for row in buttons for b in row if b.text.startswith('🎬'))
        self.callback.from_user = SimpleNamespace(id=news.ADMIN_ID + 1)
        await self.action(approve)
        self.assertEqual(await self.db.owner_panel_confirmations.count_documents({}), 0)
        self.callback.from_user = self.user
        await self.action(approve)
        confirmation = await self.db.owner_panel_confirmations.find_one({})
        self.assertEqual(confirmation['data']['revision'], item['revision'])
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
        await self.db.news_owner_drafts.update_one({'_id': item['_id']}, {'$set': {'revision': 'f'*64}})
        await self.action(approve)
        self.assertIn('изменилось', self.callback.answer.await_args.args[0])
        self.assertEqual(await self.db.owner_panel_confirmations.count_documents({}), 1)

    async def test_edit_mode_uses_captured_revision_and_validation(self):
        item = await self.ready(12)
        await news.card(self.message, news.ADMIN_ID, 'draft', item)
        buttons = self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard
        edit = next(b.callback_data for row in buttons for b in row if b.text.startswith('✏️'))
        await self.action(edit)
        self.assertEqual(self.state.update_data.await_args.kwargs['revision'], item['revision'])
        self.assertNotIn('/news_edit', self.message.answer.await_args.args[0])
        self.message.from_user = self.user
        self.message.text = 'неверный формат'
        self.state.clear.reset_mock()
        self.state.get_data.return_value = {'action': 'news_edit', 'number': 12, 'revision': item['revision']}
        await tools.receive_input(self.message, self.state)
        self.state.clear.assert_not_awaited()
        self.message.text = owner_drafts.editable_message(12, item['url'], item['script']).split('\n', 1)[1]
        with patch.object(news_owner_drafts, 'edit_draft', AsyncMock()) as fn:
            await tools.receive_input(self.message, self.state)
            self.assertTrue(fn.await_args.args[0].text.startswith('/news_edit 12 ' + item['revision']))

    async def test_selection_builds_snapshot_once_and_never_renders(self):
        await self.ready(10); await self.ready(11)
        await self.action('newsact:choose')
        session = await self.db.owner_news_selections.find_one({})
        token = session['_id']
        await self.action(f'newsact:select:{token}:10')
        await self.action(f'newsact:select:{token}:11')
        await self.action(f'newsact:build:{token}')
        self.assertEqual(await self.db.news_owner_programs.count_documents({}), 0)
        confirm = await self.db.owner_panel_confirmations.find_one({})
        self.assertEqual(confirm['data']['draft_ids'], [10, 11])
        self.callback.data = 'owner_tools:run:' + confirm['_id']
        with patch.object(news_owner_drafts, 'show_program', AsyncMock()) as show:
            await tools.action(self.callback, self.state, None)
            await tools.action(self.callback, self.state, None)
            show.assert_awaited_once()
        program = await owner_program.get(self.db, news.ADMIN_ID, 321)
        self.assertEqual(program['stage'], 'ready_for_review')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_changed_draft_or_expired_selection_cannot_build(self):
        item = await self.ready(10); await self.ready(11)
        await self.action('newsact:choose')
        session = await self.db.owner_news_selections.find_one({}); token = session['_id']
        await self.action(f'newsact:select:{token}:10'); await self.action(f'newsact:select:{token}:11')
        await self.action(f'newsact:build:{token}')
        confirm = await self.db.owner_panel_confirmations.find_one({})
        await self.db.news_owner_drafts.update_one({'_id': item['_id']}, {'$set': {'revision': 'a'*64}})
        self.callback.data = 'owner_tools:run:' + confirm['_id']
        await tools.action(self.callback, self.state, None)
        self.assertEqual(await self.db.news_owner_programs.count_documents({}), 0)
        await self.action('newsact:choose')
        session = await self.db.owner_news_selections.find_one({'stage':'open'})
        await self.db.owner_news_selections.update_one({'_id': session['_id']}, {'$set': {'expires_at': datetime.now(timezone.utc)-timedelta(seconds=1)}})
        await self.action('newsact:select:' + session['_id'] + ':10')
        self.assertIn('устарел', self.callback.answer.await_args.args[0])

    async def test_panel_view_renders_review_and_buttons_from_same_snapshot(self):
        from handlers import news_owner_panel
        item = await self.ready(12)
        self.callback.data = 'news_panel:view_drafts:12'
        with patch.object(news_owner_panel, 'db', SimpleNamespace(db=self.db)):
            await news_owner_panel.news_panel_action(self.callback)
        texts = [call.args[0] for call in self.message.answer.await_args_list]
        self.assertTrue(any('Действия с этой версией' in text for text in texts))
        self.assertFalse(any('/news_edit' in text or '/news_approve' in text for text in texts))
        card = await self.db.owner_news_cards.find_one({})
        self.assertEqual(card['revision'], item['revision'])

    async def test_confirmation_uses_captured_approval_revision_once(self):
        item = await self.ready(12)
        await news.card(self.message, news.ADMIN_ID, 'draft', item)
        markup = self.message.answer.await_args.kwargs['reply_markup']
        approve = next(b.callback_data for row in markup.inline_keyboard for b in row if b.text.startswith('🎬'))
        await self.action(approve)
        conf = await self.db.owner_panel_confirmations.find_one({})
        self.callback.data = 'owner_tools:run:' + conf['_id']
        with patch.object(news_owner_drafts, 'approve_draft', AsyncMock()) as fn:
            await tools.action(self.callback, self.state, None)
            await tools.action(self.callback, self.state, None)
            self.assertEqual(fn.await_count, 1)
            self.assertEqual(fn.await_args.args[0].text, f'/news_approve 12 {item["revision"]}')

    async def test_program_page_rejects_invalid_or_stale_card(self):
        first = await self.ready(10); await self.ready(11)
        await owner_program.create(self.db, news.ADMIN_ID, 300, [10,11])
        program = await owner_program.get(self.db, news.ADMIN_ID, 300)
        await news.card(self.message, news.ADMIN_ID, 'program', program)
        markup = self.message.answer.await_args.kwargs['reply_markup']
        approve = markup.inline_keyboard[0][0].callback_data
        await self.db.news_owner_programs.update_one({'_id': program['_id']}, {'$set': {'revision':'e'*64}})
        await self.action(approve)
        self.assertIn('изменилось', self.callback.answer.await_args.args[0])
        self.assertEqual(await self.db.owner_panel_confirmations.count_documents({}), 0)

    async def test_dispatcher_real_news_card_and_build_flow(self):
        import subprocess, sys
        script = r'''
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiogram import Bot
from aiogram.types import Update
from aiogram.methods import SendMessage
from mongomock_motor import AsyncMongoMockClient
from config import ADMIN_ID
from app_setup import configure_dispatcher
from handlers import owner_news_actions, owner_tools_panel, news_owner_panel, news_owner_drafts, alc_build, alc_intro
from news_prototype import owner_drafts, owner_trial
from tests.test_owner_drafts import DRAFT, SOURCE

async def run():
    database = AsyncMongoMockClient().test
    fake = SimpleNamespace(db=database, settings=database.settings, alchemy_players=database.alchemy_players)
    bot = Bot('123456:TESTTOKEN')
    sent = []
    async def request(bot_arg, method, **kwargs):
        sent.append(method)
        return True
    dp = configure_dispatcher()
    with patch.object(owner_news_actions, 'db', fake), patch.object(owner_tools_panel, 'db', fake), \
         patch.object(news_owner_panel, 'db', fake), patch.object(news_owner_drafts, 'db', fake), \
         patch.object(alc_build, 'db', fake), patch.object(alc_intro, 'get_intro_session', AsyncMock(return_value=None)), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)), \
         patch.object(bot.session, 'make_request', AsyncMock(side_effect=request)):
        for n in (10, 11):
            await owner_drafts.create(database, ADMIN_ID, n, f'https://t.me/ExampleNews/{n}', '')
            await owner_drafts.process_new(database, ADMIN_ID, n, AsyncMock(return_value={'summary':SOURCE}), AsyncMock(return_value=DRAFT))
        user = {'id':ADMIN_ID, 'is_bot':False, 'first_name':'Owner'}
        chat = {'id':ADMIN_ID, 'type':'private'}
        async def cb(i, data, message_id=321):
            update = Update.model_validate({'update_id':i, 'callback_query':{
                'id':str(i), 'chat_instance':'owner', 'from':user, 'data':data,
                'message':{'message_id':message_id, 'date':1760000000, 'chat':chat,
                           'from':{'id':123456,'is_bot':True,'first_name':'Bot'}, 'text':'Panel'}}})
            await asyncio.wait_for(dp.feed_update(bot, update), 5)
        await cb(1, 'news_panel:view_drafts:10')
        assert any(isinstance(m, SendMessage) and 'Действия с этой версией' in m.text for m in sent), sent
        await cb(2, 'newsact:choose')
        session = await database.owner_news_selections.find_one({})
        token = session['_id']
        await cb(3, f'newsact:select:{token}:10')
        await cb(4, f'newsact:select:{token}:11')
        await cb(5, f'newsact:build:{token}')
        assert await database.news_owner_programs.count_documents({}) == 0
        confirm = await database.owner_panel_confirmations.find_one({'action':'build_program'})
        await cb(6, 'owner_tools:run:' + confirm['_id'], 322)
        assert await database.news_owner_programs.count_documents({'stage':'ready_for_review'}) == 1
        assert await database.news_owner_trial_jobs.count_documents({}) == 0
        assert any(isinstance(m, SendMessage) and m.text == 'Действия с этой версией:' for m in sent)
    await bot.session.close()
asyncio.run(run())
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    async def test_immediate_discovery_does_not_prepare_or_render(self):
        from handlers import news_owner_panel
        from news_prototype import owner_discovery
        now = datetime.now(timezone.utc).isoformat()
        items = [{'source':'Test', 'title':f'News {i}', 'url':f'https://example.com/{i}',
                  'published_at':now} for i in (1,2)]
        self.callback.data = 'news_panel:discover'
        with patch.object(news_owner_panel, 'db', SimpleNamespace(db=self.db)), \
             patch.object(owner_discovery, 'collect', AsyncMock(return_value=(items, ['OpenAI']))), \
             patch.object(owner_discovery, 'translate_titles', AsyncMock(return_value=None)), \
             patch.object(news_owner_drafts, 'prepare_urls', AsyncMock()) as prepare:
            await news_owner_panel.news_panel_action(self.callback)
            prepare.assert_not_awaited()
        self.assertEqual(await self.db.news_owner_panel_sessions.count_documents({}), 1)
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
        self.assertTrue(any('OpenAI' in call.args[0] for call in self.message.answer.await_args_list))
