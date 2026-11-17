import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
from mongomock_motor import AsyncMongoMockClient

import owner_preferences
from handlers import admin_panel, news_owner_panel, owner_control_panel as panel, image_gen, image_providers, ex_logic, web_search
from news_prototype import owner_trial


class OwnerControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        self.database = SimpleNamespace(db=self.db, settings=self.db.settings)
        p = patch.object(panel, 'db', self.database)
        p.start(); self.addCleanup(p.stop)
        p = patch.object(news_owner_panel, 'db', self.database)
        p.start(); self.addCleanup(p.stop)
        self.owner = SimpleNamespace(id=panel.ADMIN_ID)
        self.message = SimpleNamespace(chat=SimpleNamespace(type='private', id=panel.ADMIN_ID), message_id=55,
                                       answer=AsyncMock(), edit_text=AsyncMock(), delete=AsyncMock())
        self.callback = SimpleNamespace(from_user=self.owner, message=self.message, data='', answer=AsyncMock())

    async def action(self, action):
        self.callback.data = 'owner_panel:' + action
        await panel.control_action(self.callback)

    async def test_migration_preserves_current_values_and_survives_removal(self):
        for provider in ('legacy', 'cloudflare'):
            for search in ('', 'enabled'):
                with self.subTest(provider=provider, search=search):
                    db = AsyncMongoMockClient().test
                    with patch.dict(os.environ, {'IMAGE_PROVIDER_MODE': provider, 'SEARCH_IMAGES': search}):
                        before = await owner_preferences.get(db.settings)
                        await owner_preferences.migrate(db.settings)
                    with patch.dict(os.environ, {'IMAGE_PROVIDER_MODE':'', 'SEARCH_IMAGES':''}):
                        self.assertEqual(await owner_preferences.get(db.settings), before)
                        await owner_preferences.migrate(db.settings)
                        self.assertEqual(await owner_preferences.get(db.settings), before)

    async def test_home_migrates_and_shows_shared_settings_news_and_navigation(self):
        flags = {'IMAGE_PROVIDER_MODE':'cloudflare', 'SEARCH_IMAGES':'enabled',
                 'NEWS_OWNER_DAILY_ENABLED':'1', 'NEWS_OWNER_DRAFT_ENABLED':'1', 'NEWS_OWNER_TRIAL_ENABLED':'1',
                 **{name:'0' for name in owner_trial.OTHER_FLAGS}}
        with patch.dict(os.environ, flags):
            await panel.open_owner_panel(self.message)
        text = self.message.answer.await_args.args[0]
        self.assertIn('ПУЛЬТ ВЛАДЕЛЬЦА', text)
        self.assertIn('Cloudflare + OVHcloud', text)
        self.assertIn('включены', text)
        self.assertIn('во всех чатах', text)
        with patch.dict(os.environ, {'IMAGE_PROVIDER_MODE':'', 'SEARCH_IMAGES':'',
                                     'NEWS_OWNER_DAILY_ENABLED':'0', 'NEWS_OWNER_DRAFT_ENABLED':'0',
                                     'NEWS_OWNER_TRIAL_ENABLED':'0'}):
            prefs = await owner_preferences.get(self.db.settings)
            self.assertEqual(prefs, {'image_provider':'cloudflare', 'search_images':True})
            await self.action('news')
            self.assertIn('ПАНЕЛЬ НОВОСТЕЙ', self.message.edit_text.await_args.args[0])
            await self.action('home')
            self.assertIn('ПУЛЬТ ВЛАДЕЛЬЦА', self.message.edit_text.await_args.args[0])

    async def test_changes_are_owner_only_and_persistent_with_explicit_options(self):
        with patch.dict(os.environ, {'IMAGE_PROVIDER_MODE':'legacy', 'SEARCH_IMAGES':''}):
            await self.action('image_cloudflare')
            await self.action('search_on')
            prefs = await owner_preferences.get(self.db.settings)
            self.assertEqual(prefs, {'image_provider':'cloudflare', 'search_images':True})
            self.callback.from_user = SimpleNamespace(id=panel.ADMIN_ID + 1)
            await self.action('search_off')
            self.assertTrue((await owner_preferences.get(self.db.settings))['search_images'])
            self.callback.from_user = self.owner
            self.message.chat.type = 'group'
            await self.action('image_legacy')
            self.assertEqual((await owner_preferences.get(self.db.settings))['image_provider'], 'cloudflare')
            self.message.chat.type = 'private'
            self.message.chat.id += 1
            await self.action('image_legacy')
            self.assertEqual((await owner_preferences.get(self.db.settings))['image_provider'], 'cloudflare')
            self.message.chat.id -= 1
            await self.action('image_legacy')
            await self.action('search_off')
            self.assertEqual(await owner_preferences.get(self.db.settings),
                             {'image_provider':'legacy', 'search_images':False})

    async def test_runtime_routing_uses_saved_choice_not_environment(self):
        await owner_preferences.set_image_provider(self.db.settings, 'cloudflare')
        await owner_preferences.set_search_images(self.db.settings, True)
        async def current():
            return await owner_preferences.get(self.db.settings)
        with patch.dict(os.environ, {'IMAGE_PROVIDER_MODE':'legacy', 'SEARCH_IMAGES':''}), \
             patch.object(image_gen, 'get_owner_preferences', AsyncMock(side_effect=current)), \
             patch.object(image_providers, 'generate_image', AsyncMock(return_value=b'image')) as cloud, \
             patch.object(image_gen, 'fetch_image_pollinations', AsyncMock()) as legacy:
            self.assertEqual(await image_gen.generate_image('кошка'), b'image')
            cloud.assert_awaited_once_with('кошка')
            legacy.assert_not_awaited()
        with patch.object(ex_logic, 'get_owner_preferences', AsyncMock(side_effect=current)), \
             patch.object(web_search, 'get_owner_preferences', AsyncMock(side_effect=current)):
            self.assertTrue((await ex_logic.get_owner_preferences())['search_images'])
            self.assertTrue((await web_search.get_owner_preferences())['search_images'])

    async def test_real_dispatcher_opens_panel_and_navigates_buttons(self):
        # Dispatcher routers are singletons, so exercise actual aiogram routing in
        # a fresh interpreter rather than attaching them twice to test dispatchers.
        import subprocess
        import sys
        script = r'''
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiogram import Bot
from aiogram.methods import SendMessage, EditMessageText
from aiogram.types import Update
from mongomock_motor import AsyncMongoMockClient
from config import ADMIN_ID
from app_setup import configure_dispatcher
from handlers import owner_control_panel, news_owner_panel, alc_build, alc_intro

async def run():
    database = AsyncMongoMockClient().test
    fake = SimpleNamespace(db=database, settings=database.settings, alchemy_players=database.alchemy_players)
    bot = Bot('123456:TESTTOKEN')
    sent = []
    async def request(bot_arg, method, **kwargs):
        sent.append(method)
        return True
    dp = configure_dispatcher()
    with patch.object(owner_control_panel, 'db', fake), patch.object(news_owner_panel, 'db', fake), \
         patch.object(alc_build, 'db', fake), patch.object(alc_intro, 'get_intro_session', AsyncMock(return_value=None)), \
         patch.object(bot.session, 'make_request', AsyncMock(side_effect=request)):
        msg = {'message_id':1, 'date':1760000000, 'chat':{'id':ADMIN_ID, 'type':'private'},
               'from':{'id':ADMIN_ID, 'is_bot':False, 'first_name':'Owner'}, 'text':'/panel'}
        await dp.feed_update(bot, Update.model_validate({'update_id':1, 'message':msg}))
        assert any(isinstance(m, SendMessage) and 'ПУЛЬТ ВЛАДЕЛЬЦА' in m.text for m in sent)
        for i, data in enumerate(('owner_panel:images', 'owner_panel:search', 'owner_panel:news'), 2):
            cb = {'id':str(i), 'chat_instance':'owner', 'from':msg['from'], 'data':data,
                  'message':{'message_id':2, 'date':1760000000, 'chat':msg['chat'],
                             'from':{'id':123456,'is_bot':True,'first_name':'Bot'}, 'text':'Panel'}}
            await dp.feed_update(bot, Update.model_validate({'update_id':i, 'callback_query':cb}))
        edits = [m.text for m in sent if isinstance(m, EditMessageText)]
        assert len(edits) == 3 and 'РИСОВАНИЕ' in edits[0] and 'ПОИСКЕ' in edits[1] and 'ПАНЕЛЬ НОВОСТЕЙ' in edits[2], edits
    await bot.session.close()

asyncio.run(run())
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    async def test_invalid_values_and_group_menu_delegation(self):
        with self.assertRaises(ValueError):
            await owner_preferences.set_image_provider(self.db.settings, 'unknown')
        with self.assertRaises(ValueError):
            await owner_preferences.set_search_images(self.db.settings, 'yes')
        from handlers import auth
        with patch.object(auth, 'admin_chats_menu', AsyncMock()) as menu:
            await self.action('groups')
            menu.assert_awaited_once()
            self.assertEqual(menu.await_args.args[0].from_user.id, panel.ADMIN_ID)
            self.callback.from_user = SimpleNamespace(id=panel.ADMIN_ID+1)
            await self.action('groups')
            menu.assert_awaited_once()


if __name__ == '__main__': unittest.main()
