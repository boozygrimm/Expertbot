import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
from mongomock_motor import AsyncMongoMockClient
from handlers import admin_panel, news_owner_panel as panel, news_owner_drafts, owner_control_panel
from news_prototype import owner_daily, owner_discovery, owner_trial


class PanelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        self.db_patch = patch.object(panel, 'db', SimpleNamespace(db=self.db))
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        control_db = patch.object(owner_control_panel, 'db', SimpleNamespace(db=self.db, settings=self.db.settings))
        control_db.start()
        self.addCleanup(control_db.stop)
        self.env = patch.dict(os.environ, {'NEWS_OWNER_DAILY_ENABLED':'0', 'NEWS_OWNER_TRIAL_ENABLED':'0',
                                           'NEWS_OWNER_DRAFT_ENABLED':'0'}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.owner = SimpleNamespace(id=panel.ADMIN_ID)
        self.message = SimpleNamespace(chat=SimpleNamespace(type='private', id=panel.ADMIN_ID), message_id=123,
                                       edit_text=AsyncMock(), answer=AsyncMock(), delete=AsyncMock())
        self.callback = SimpleNamespace(from_user=self.owner, message=self.message, data='', answer=AsyncMock())

    async def action(self, data):
        self.callback.data = data
        await panel.news_panel_action(self.callback)

    async def test_panel_entry_for_owner_and_old_group_behavior(self):
        state = SimpleNamespace(clear=AsyncMock())
        private = SimpleNamespace(chat=SimpleNamespace(type='private'), from_user=self.owner, answer=AsyncMock())
        await admin_panel.open_feature_panel(private, None, state)
        self.assertIn('ПУЛЬТ ВЛАДЕЛЬЦА', private.answer.await_args.args[0])
        buttons = private.answer.await_args.kwargs['reply_markup'].inline_keyboard
        self.assertEqual(buttons[0][0].text, '📰 Новости')
        private.from_user = SimpleNamespace(id=panel.ADMIN_ID+1)
        private.reply = AsyncMock()
        await admin_panel.open_feature_panel(private, None, state)
        private.reply.assert_awaited_once()
        group = SimpleNamespace(chat=SimpleNamespace(type='group', id=-42), from_user=self.owner,
                                delete=AsyncMock(), reply=AsyncMock())
        with patch.object(admin_panel, 'is_chat_admin', AsyncMock(return_value=True)), \
             patch.object(admin_panel, 'render_feature_panel', AsyncMock()) as render:
            await admin_panel.open_feature_panel(group, None, state)
            render.assert_awaited_once_with(group)

    async def test_toggle_persists_without_env_and_rejects_unauthorized(self):
        import app_setup
        with patch.object(app_setup, 'collect_owner_daily_news', AsyncMock()) as poll:
            await self.action('news_panel:on')
            self.assertTrue(await owner_daily.enabled(self.db))
            poll.assert_awaited_once()
            await self.action('news_panel:off')
            self.assertFalse(await owner_daily.enabled(self.db))
            with patch.dict(os.environ, {'NEWS_OWNER_DAILY_ENABLED':'1'}):
                self.assertFalse(await owner_daily.enabled(self.db))
            self.callback.from_user = SimpleNamespace(id=panel.ADMIN_ID+1)
            await self.action('news_panel:on')
            self.assertFalse(await owner_daily.enabled(self.db))
            self.callback.from_user = self.owner
            self.message.chat.type = 'group'
            await self.action('news_panel:on')
            self.assertFalse(await owner_daily.enabled(self.db))
            poll.assert_awaited_once()

    async def test_open_panel_preserves_all_existing_modes_when_legacy_flags_are_removed(self):
        flags = {'NEWS_OWNER_DAILY_ENABLED': '1', 'NEWS_OWNER_TRIAL_ENABLED': '1',
                 'NEWS_OWNER_DRAFT_ENABLED': '1', **{name:'0' for name in owner_trial.OTHER_FLAGS}}
        with patch.dict(os.environ, flags):
            await panel.panel_content()
        with patch.dict(os.environ, {'NEWS_OWNER_DAILY_ENABLED':'0', 'NEWS_OWNER_TRIAL_ENABLED':'0',
                                     'NEWS_OWNER_DRAFT_ENABLED':'0', **{name:'0' for name in owner_trial.OTHER_FLAGS}}):
            self.assertTrue(await owner_daily.enabled(self.db))
            self.assertTrue(await owner_trial.runtime_enabled(self.db))
            self.assertTrue(await owner_trial.drafts_enabled(self.db))
            await self.action('news_panel:modeoff')
            self.assertFalse(await owner_trial.drafts_enabled(self.db))
            self.assertFalse(await owner_trial.runtime_enabled(self.db))
            self.assertTrue(await owner_daily.enabled(self.db))
            await self.action('news_panel:modeon')
            self.assertTrue(await owner_trial.drafts_enabled(self.db))
            with patch.dict(os.environ, {'NEWS_COLLECTION_ENABLED':'1'}):
                self.assertFalse(await owner_trial.drafts_enabled(self.db))
                await self.action('news_panel:modeon')
                self.assertIn('Другой новостной режим', self.callback.answer.await_args.args[0])
            self.assertTrue(await owner_trial.drafts_enabled(self.db))

    async def test_pagination_selection_and_explicit_confirmation_are_idempotent(self):
        now = datetime.now(timezone.utc)
        items = [{'source': 'OpenAI', 'title': f'News {i}', 'url': f'https://openai.com/news/{i}',
                  'published_at': (now - timedelta(minutes=i+1)).isoformat()} for i in range(52)]
        await owner_daily.poll(self.db, now=now, fetch=AsyncMock(return_value=(items, [])))
        with patch.object(owner_discovery, 'translate_titles', AsyncMock(return_value=None)) as translate, \
             patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)), \
             patch.object(news_owner_drafts, 'prepare_urls', AsyncMock()) as prepare:
            await self.action('news_panel:list')
            session = await self.db.news_owner_panel_sessions.find_one({})
            token = session['_id']
            self.assertEqual(len(session['items']), 52)
            self.assertIn('страница 1/13', self.message.edit_text.await_args.args[0])
            await self.action(f'news_panel:sel:{token}:1')
            self.assertEqual(translate.await_count, 1)  # cached page translation
            await self.action(f'news_panel:page:{token}:12')
            self.assertIn('страница 13/13', self.message.edit_text.await_args.args[0])
            await self.action(f'news_panel:sel:{token}:52')
            session = await self.db.news_owner_panel_sessions.find_one({'_id': token})
            self.assertEqual(session['selected'], [1, 52])
            prepare.assert_not_awaited()
            self.message.answer.return_value = SimpleNamespace(message_id=124)
            await self.action(f'news_panel:prepare:{token}')
            prepare.assert_not_awaited()
            self.assertIn('Подготовить 2 сценария?', self.message.answer.await_args.args[0])
            confirm = SimpleNamespace(chat=self.message.chat, message_id=124, edit_text=AsyncMock(), answer=AsyncMock())
            self.callback.message = confirm
            await self.action(f'news_panel:confirm:{token}')
            prepare.assert_awaited_once()
            args = prepare.await_args.args
            self.assertEqual(args[0].message_id, 124)
            self.assertEqual(args[0].from_user.id, panel.ADMIN_ID)
            self.assertEqual(args[1], [items[0]['url'], items[-1]['url']])
            await self.action(f'news_panel:confirm:{token}')
            prepare.assert_awaited_once()

    async def test_max_four_selection_and_no_prepare_when_video_mode_off(self):
        now = datetime.now(timezone.utc)
        items = [{'source':'OpenAI', 'title':str(i), 'url':f'https://openai.com/{i}',
                  'published_at':(now-timedelta(minutes=i+1)).isoformat()} for i in range(5)]
        await owner_daily.poll(self.db, now=now, fetch=AsyncMock(return_value=(items, [])))
        with patch.object(owner_discovery, 'translate_titles', AsyncMock(return_value=None)), \
             patch.object(news_owner_drafts, 'prepare_urls', AsyncMock()) as prepare:
            await self.action('news_panel:list')
            token = (await self.db.news_owner_panel_sessions.find_one({}))['_id']
            for i in range(1, 6):
                await self.action(f'news_panel:sel:{token}:{i}')
            self.assertEqual((await self.db.news_owner_panel_sessions.find_one({'_id':token}))['selected'], [1,2,3,4])
            await self.action(f'news_panel:prepare:{token}')
            self.assertIn('выключена', self.callback.answer.await_args.args[0])
            prepare.assert_not_awaited()

    async def test_owner_queue_respects_persistent_mode_and_ordinary_queue(self):
        import main
        from news_prototype import news_queue, news_worker_client
        queue = object()
        flags = {name: '0' for name in owner_trial.OTHER_FLAGS}
        with patch.dict(os.environ, {**flags, 'NEWS_OWNER_TRIAL_ENABLED':'0'}), \
             patch.object(main, 'db', SimpleNamespace(db=self.db)), \
             patch.object(news_worker_client, 'NewsWorkerClient', return_value=object()), \
             patch.object(owner_trial, 'dispatch_once', AsyncMock()) as dispatch, \
             patch.object(news_queue, 'run_idle_once', AsyncMock()) as ordinary:
            await main.run_news_idle(queue)
            dispatch.assert_not_awaited()
            ordinary.assert_not_awaited()
            await owner_trial.set_owner_mode(self.db, True)
            await main.run_news_idle(queue)
            dispatch.assert_awaited_once()
            with patch.dict(os.environ, {'NEWS_RENDER_QUEUE_ENABLED': '1'}):
                await main.run_news_idle(queue)
                dispatch.assert_awaited_once()
                ordinary.assert_awaited_once()
            await owner_trial.set_owner_mode(self.db, False)
            await main.run_news_idle(queue)
            dispatch.assert_awaited_once()

    async def test_four_long_items_fit_telegram_limit(self):
        session = {'_id':'0123456789abcdef', 'items':[
            {'title':'📰'*180, 'source':'OpenAI', 'url':'https://example.com/'+'a'*470+str(i),
             'published_at':datetime.now(timezone.utc).isoformat()} for i in range(4)],
            'selected':[], 'translations': {'0': ['📰'*180]*4}}
        text, markup = await panel.page_content(session, 0)
        self.assertLessEqual(len(text.encode('utf-16-le'))//2, 4096)
        for row in markup.inline_keyboard:
            for button in row:
                if button.callback_data is not None:
                    self.assertLessEqual(len(button.callback_data.encode()), 64)

if __name__ == '__main__': unittest.main()
