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
        self.assertIn('Пульт владельца', text)
        self.assertIn('Cloudflare + OVHcloud', text)
        self.assertIn('включены', text)
        self.assertIn('во всех чатах', text)
        with patch.dict(os.environ, {'IMAGE_PROVIDER_MODE':'', 'SEARCH_IMAGES':'',
                                     'NEWS_OWNER_DAILY_ENABLED':'0', 'NEWS_OWNER_DRAFT_ENABLED':'0',
                                     'NEWS_OWNER_TRIAL_ENABLED':'0'}):
            prefs = await owner_preferences.get(self.db.settings)
            self.assertEqual(prefs, {'image_provider':'cloudflare', 'search_images':True})
            await self.action('news')
            self.assertIn('📰 Новости', self.message.edit_text.await_args.args[0])
            await self.action('home')
            self.assertIn('Пульт владельца', self.message.edit_text.await_args.args[0])

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
from aiogram.methods import SendMessage, EditMessageText, DeleteMessage
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
        assert any(isinstance(m, SendMessage) and 'Пульт владельца' in m.text for m in sent)
        for i, data in enumerate(('owner_panel:group_menu', 'owner_panel:alchemy', 'owner_panel:alchemy_admin',
                                  'owner_panel:media', 'owner_panel:images', 'owner_panel:search',
                                  'owner_panel:news', 'news_panel:collection', 'news_panel:scripts',
                                  'news_panel:editions', 'news_panel:settings', 'owner_panel:status'), 2):
            cb = {'id':str(i), 'chat_instance':'owner', 'from':msg['from'], 'data':data,
                  'message':{'message_id':2, 'date':1760000000, 'chat':msg['chat'],
                             'from':{'id':123456,'is_bot':True,'first_name':'Bot'}, 'text':'Panel'}}
            await dp.feed_update(bot, Update.model_validate({'update_id':i, 'callback_query':cb}))
        edits = [m.text for m in sent if isinstance(m, EditMessageText)]
        assert len(edits) == 12, edits
        for text, expected in zip(edits, ('Группы', 'Алхимия', 'Управление алхимией',
                                          'Картинки и поиск', 'Рисование', 'поиске',
                                          '📰 Новости', 'Crypto Sensei', 'Сценарии',
                                          'Вестники и выпуски', 'Настройки новостей', 'Статус пульта')):
            assert expected in text, (text, expected)
        cb['id'] = 'close'
        cb['data'] = 'owner_panel:close'
        await dp.feed_update(bot, Update.model_validate({'update_id':100, 'callback_query':cb}))
        assert any(isinstance(m, DeleteMessage) for m in sent)
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


def callbacks(markup):
    return {button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data}


class OwnerNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        fake = SimpleNamespace(db=self.db, settings=self.db.settings)
        self.patches = [patch.object(panel, 'db', fake), patch.object(news_owner_panel, 'db', fake)]
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)
        self.message = SimpleNamespace(chat=SimpleNamespace(type='private', id=panel.ADMIN_ID), message_id=55,
                                       answer=AsyncMock(), edit_text=AsyncMock(), delete=AsyncMock())
        self.callback = SimpleNamespace(from_user=SimpleNamespace(id=panel.ADMIN_ID), message=self.message, data='', answer=AsyncMock())
        self.state = SimpleNamespace(clear=AsyncMock())

    async def click(self, data):
        self.callback.data = data
        await panel.control_action(self.callback, self.state)
        return self.message.edit_text.await_args.kwargs['reply_markup']

    async def test_main_sections_and_backward_compatibility(self):
        markup = (await panel.home_content())[1]
        self.assertEqual([len(row) for row in markup.inline_keyboard], [2, 2, 1, 2, 2])
        self.assertEqual(callbacks(markup), {'owner_panel:news', 'ovideo:open', 'owner_panel:group_menu',
                         'owner_panel:alchemy', 'owner_panel:media', 'owner_panel:help',
                         'owner_panel:home', 'owner_panel:close', 'owner_panel:status'})
        for route, expected, back in [
            ('group_menu', {'owner_panel:groups', 'owner_tools:reports', 'owner_tools:points'}, 'owner_panel:home'),
            ('alchemy', {'owner_tools:base', 'owner_panel:alchemy_ref', 'owner_panel:alchemy_admin'}, 'owner_panel:home'),
            ('alchemy_ref', {'owner_tools:recipes', 'owner_tools:recipe', 'owner_tools:lorehelp'}, 'owner_panel:alchemy'),
            ('alchemy_admin', {'owner_tools:lore', 'owner_tools:regen', 'owner_tools:seed', 'owner_tools:pack',
                               'owner_tools:clear', 'owner_tools:reset'}, 'owner_panel:alchemy'),
            ('media', {'owner_panel:images', 'owner_panel:search'}, 'owner_panel:home'),
            ('help', {'owner_tools:help'}, 'owner_panel:home'),
            ('tools', {'ovideo:open', 'owner_panel:alchemy', 'owner_panel:group_menu'}, 'owner_panel:home'),
        ]:
            markup = await self.click('owner_panel:' + route)
            self.assertEqual(callbacks(markup), expected | {back, 'owner_panel:close'})
        self.assertGreaterEqual(self.state.clear.await_count, 7)

    async def test_sections_are_owner_only_and_do_not_launch_any_actions(self):
        from handlers import auth
        from news_prototype import general_queue
        with patch.object(auth, 'admin_chats_menu', AsyncMock()) as groups, \
             patch.object(general_queue, 'poll', AsyncMock()) as poll:
            for route in ('group_menu', 'alchemy', 'alchemy_ref', 'alchemy_admin', 'media', 'help', 'tools'):
                await self.click('owner_panel:' + route)
            groups.assert_not_awaited(); poll.assert_not_awaited()
            self.assertEqual(await self.db.owner_panel_confirmations.count_documents({}), 0)
            self.message.edit_text.reset_mock()
            self.callback.from_user.id += 1
            await panel.control_action(self.callback, self.state)
            self.message.edit_text.assert_not_awaited()
            self.assertIn('только владельцу', self.callback.answer.await_args.args[0])

    async def test_media_subscreens_have_back_navigation(self):
        for route in ('images', 'search'):
            markup = await self.click('owner_panel:' + route)
            self.assertIn('owner_panel:media', callbacks(markup))
