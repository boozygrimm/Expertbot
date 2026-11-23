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

    async def test_help_screen_lists_every_reserved_command(self):
        await self.action('help')
        text = self.message.edit_text.await_args.args[0]
        callbacks = [b.callback_data for row in self.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard
                     for b in row]
        self.assertIn('owner_panel:commands', callbacks)
        await self.action('commands')
        text = self.message.edit_text.await_args.args[0]
        self.assertIn('Все команды', text)
        listed = {command for titles, entries in panel.COMMAND_GROUPS for command, _ in entries}
        # Everything the owner may still need to type is on one screen, grouped.
        for command in ('/news_prepare ССЫЛКА', '/news_retry НОМЕР', '/news_approve НОМЕР ВЕРСИЯ',
                        '/news_novideo', '/news_program_approve'):
            self.assertIn(command, listed)
        for command in listed:
            self.assertIn(command, text)
        self.assertLessEqual(len(text.encode('utf-16-le')) // 2, 4096)
        callbacks = [b.callback_data for row in self.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard
                     for b in row]
        self.assertIn('owner_panel:help', callbacks)
        self.assertIn('owner_panel:commands', callbacks)

    async def test_status_links_to_a_worker_screen_that_names_the_gap(self):
        from news_prototype import news_worker_client
        await self.action('status')
        callbacks = [b.callback_data for row in self.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard
                     for b in row]
        self.assertIn('owner_panel:worker', callbacks)
        cases = (
            ({'reachable': True, 'capabilities': {'news-bulletin-v1', 'news-bulletin-video-v1',
                                                  'news-bulletin-manual-video-v1',
                                                  'news-story-manual-video-v1'}}, 'поддерживает'),
            # Old worker: the protocol itself is missing -> update the worker.
            ({'reachable': True, 'capabilities': {'news-bulletin-v1'}}, 'не хватает'),
            # All bulletin video capabilities present: the binaries exist, so
            # the gap is old worker code, not a missing ffmpeg. Saying "install
            # ffmpeg" here sent the owner chasing a binary already installed.
            ({'reachable': True, 'capabilities': {'news-bulletin-v1', 'news-bulletin-video-v1',
                                                  'news-bulletin-manual-video-v1',
                                                  'news-bulletin-video-cut-v1'}},
             'устанавливать ffmpeg не нужно'),
            # Unreachable is not the same as outdated.
            ({'reachable': False, 'capabilities': set()}, 'не ответил'),
        )
        for worker, expected in cases:
            with self.subTest(expected=expected), patch.dict(os.environ, {
                    'BENDER_WORKER_URL': 'https://worker.example.com', 'BENDER_WORKER_KEY': 'k'}), \
                    patch.object(news_worker_client.NewsWorkerClient, 'media_capabilities',
                                 AsyncMock(return_value=worker)):
                await self.action('worker')
            text = self.message.edit_text.await_args.args[0]
            self.assertIn(expected, text)
            self.assertIn('ничего не запускает', text)
        # The screen explains that the check is read-only and offers a retry.
        callbacks = [b.callback_data for row in self.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard
                     for b in row]
        self.assertIn('owner_panel:worker', callbacks)
        self.assertIn('owner_panel:status', callbacks)

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
        # Every private screen ends with refresh + close, so the panel must too.
        self.assertEqual([len(row) for row in markup.inline_keyboard], [1, 2, 2, 2, 2, 1, 1, 1, 1])
        self.assertEqual(callbacks(markup), {'owner_panel:news', 'ovideo:open', 'owner_panel:group_menu',
                         'owner_panel:alchemy', 'owner_panel:media', 'owner_panel:help',
                         'owner_panel:home', 'owner_panel:close', 'owner_panel:status', 'owner_panel:raid_test',
                         'gnews:home', 'news_panel:scripts', 'news_panel:editions'})
        for route, expected, back in [
            ('group_menu', {'owner_panel:groups', 'owner_tools:reports', 'owner_tools:points'}, 'owner_panel:home'),
            ('alchemy', {'owner_tools:base', 'owner_panel:alchemy_ref', 'owner_panel:alchemy_admin'}, 'owner_panel:home'),
            ('alchemy_ref', {'owner_tools:recipes', 'owner_tools:recipe', 'owner_tools:lorehelp'}, 'owner_panel:alchemy'),
            ('alchemy_admin', {'owner_tools:lore', 'owner_tools:regen', 'owner_tools:seed', 'owner_tools:pack',
                               'owner_tools:clear', 'owner_tools:reset'}, 'owner_panel:alchemy'),
            ('media', {'owner_panel:images', 'owner_panel:search'}, 'owner_panel:home'),
            ('help', {'owner_tools:help', 'owner_panel:commands'}, 'owner_panel:home'),
            ('tools', {'ovideo:open', 'owner_panel:alchemy', 'owner_panel:group_menu'}, 'owner_panel:home'),
        ]:
            markup = await self.click('owner_panel:' + route)
            self.assertEqual(callbacks(markup), expected | {back, 'owner_panel:close',
                                                             'owner_panel:' + route})
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


class RaidMiniAppEntryTests(unittest.IsolatedAsyncioTestCase):
    """Only the bot entry is private; a public static URL is not an auth layer."""

    def message(self, user=None, chat_id=None, kind='private'):
        return SimpleNamespace(
            from_user=SimpleNamespace(id=panel.ADMIN_ID if user is None else user),
            chat=SimpleNamespace(type=kind, id=panel.ADMIN_ID if chat_id is None else chat_id),
            answer=AsyncMock(), edit_text=AsyncMock())

    def assert_launcher(self, text, markup):
        import re
        from pathlib import Path
        from aiogram.types import WebAppInfo
        from urllib.parse import parse_qs, urlsplit
        buttons = [b for row in markup.inline_keyboard for b in row]
        launchers = [b for b in buttons if b.web_app]
        self.assertEqual(len(launchers), 1)
        self.assertIsInstance(launchers[0].web_app, WebAppInfo)
        self.assertIsNone(launchers[0].url, 'ordinary URL button is not a Mini App launch')
        url = urlsplit(launchers[0].web_app.url)
        self.assertEqual((url.scheme, url.netloc, url.path),
                         ('https', 'boozygrimm.github.io', '/Expertbot/lunapark-raid.html'))
        build = re.search(r'<meta name="raid-build" content="([a-f0-9]{12})">',
                          (Path(__file__).resolve().parents[1] / 'lunapark-raid.html').read_text()).group(1)
        self.assertEqual(parse_qs(url.query), {'telegram': ['1'], 'build': [build]})
        self.assertIn(build, text)
        self.assertIn('публичный', text)
        self.assertIn('локальные', text)
        self.assertIn('Без рабочей базы', text)
        self.assertLessEqual(len(text.encode('utf-16-le')) // 2, 4096)
        self.assertEqual(callbacks(markup), {'owner_panel:home', 'owner_panel:raid_test', 'owner_panel:close'})

    async def test_command_sends_web_app_and_clears_only_owner_private_input_without_db(self):
        class NoDatabase:
            def __getattr__(self, name):
                raise AssertionError('Mini App launcher accessed database: ' + name)
        message = self.message()
        state = SimpleNamespace(clear=AsyncMock())
        with patch.object(panel, 'db', NoDatabase()):
            await panel.open_raid_test(message, state)
        state.clear.assert_awaited_once()
        message.answer.assert_awaited_once()
        args = message.answer.await_args
        self.assert_launcher(args.args[0], args.kwargs['reply_markup'])
        self.assertIsNone(args.kwargs['parse_mode'])
        self.assertTrue(args.kwargs['protect_content'])

    async def test_command_denies_other_users_groups_wrong_private_id_and_missing_sender(self):
        for sender, chat_id, kind in (
                (panel.ADMIN_ID + 1, panel.ADMIN_ID + 1, 'private'),
                (panel.ADMIN_ID + 1, panel.ADMIN_ID, 'private'),
                (panel.ADMIN_ID, panel.ADMIN_ID + 1, 'private'),
                (panel.ADMIN_ID, -100123, 'group'),
                (panel.ADMIN_ID, -100123, 'supergroup'),
                (panel.ADMIN_ID, panel.ADMIN_ID, 'channel')):
            with self.subTest(sender=sender, chat=chat_id, kind=kind):
                message = self.message(sender, chat_id, kind)
                state = SimpleNamespace(clear=AsyncMock())
                await panel.open_raid_test(message, state)
                message.answer.assert_not_awaited()
                state.clear.assert_not_awaited()
        message = self.message(); message.from_user = None
        await panel.open_raid_test(message)
        message.answer.assert_not_awaited()

    async def test_panel_callback_has_same_launcher_and_owner_checks_before_clearing_state(self):
        message = self.message()
        cb = SimpleNamespace(from_user=message.from_user, message=message,
                             data='owner_panel:raid_test', answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())
        with patch.object(panel, 'db', None):
            await panel.control_action(cb, state)
        state.clear.assert_awaited_once()
        self.assert_launcher(message.edit_text.await_args.args[0],
                             message.edit_text.await_args.kwargs['reply_markup'])
        for sender, chat, kind in ((panel.ADMIN_ID + 1, panel.ADMIN_ID, 'private'),
                                   (panel.ADMIN_ID, panel.ADMIN_ID + 1, 'private'),
                                   (panel.ADMIN_ID, -100123, 'supergroup')):
            message = self.message(sender, chat, kind)
            cb.from_user = message.from_user; cb.message = message
            state.clear.reset_mock()
            await panel.control_action(cb, state)
            state.clear.assert_not_awaited()
            message.edit_text.assert_not_awaited()
            self.assertTrue(cb.answer.await_args.kwargs['show_alert'])
        cb.message = None
        await panel.control_action(cb, state)
        state.clear.assert_not_awaited()

    async def test_missing_build_file_does_not_break_fixed_https_launcher(self):
        from pathlib import Path
        with patch.object(Path, 'open', side_effect=FileNotFoundError):
            text, markup = panel.raid_preview_content()
        self.assertIn('Номер сборки', text)
        self.assertEqual(markup.inline_keyboard[0][0].web_app.url, panel.RAID_PREVIEW_URL)

    async def test_command_menu_is_published_only_in_owner_chat_scope(self):
        from app_setup import set_commands
        from bot_commands import ADMIN_PRIVATE_COMMAND_SPECS, USER_PRIVATE_COMMAND_SPECS, GROUP_COMMAND_SPECS
        self.assertIn('raid_test', dict(ADMIN_PRIVATE_COMMAND_SPECS))
        self.assertNotIn('raid_test', dict(USER_PRIVATE_COMMAND_SPECS))
        self.assertNotIn('raid_test', dict(GROUP_COMMAND_SPECS))
        bot = SimpleNamespace(set_my_commands=AsyncMock())
        await set_commands(bot)
        calls = [c for c in bot.set_my_commands.await_args_list
                 if any(command.command == 'raid_test' for command in c.args[0])]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].kwargs['scope'].chat_id, panel.ADMIN_ID)

    async def test_real_dispatcher_launch_escapes_broad_fsm_inputs_and_denies_forged_entries(self):
        import subprocess
        import sys
        script = r'''
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiogram import Bot
from aiogram.methods import SendMessage, EditMessageText, GetMe
from aiogram.types import Update, User
from app_setup import configure_dispatcher
from config import ADMIN_ID
from handlers import owner_control_panel as panel, owner_tools_panel as tools, alc_build, alc_intro, auth, logger

class NoDatabase:
    def __getattr__(self, name):
        raise AssertionError('Unexpected database access: ' + name)

async def run():
    bot = Bot('123456:TESTTOKEN')
    sent = []
    async def request(bot_arg, method, **kwargs):
        if isinstance(method, GetMe):
            return User(id=bot.id, is_bot=True, first_name='Test', username='raid_owner_bot')
        sent.append(method)
        return True
    dp = configure_dispatcher()
    user = {'id':ADMIN_ID, 'is_bot':False, 'first_name':'Owner'}
    chat = {'id':ADMIN_ID, 'type':'private'}
    auth_db = SimpleNamespace(auth_chats=SimpleNamespace(find_one=AsyncMock(return_value={'status':'approved'})))
    try:
        with patch.object(panel, 'db', NoDatabase()), patch.object(alc_build, 'db', NoDatabase()), \
             patch.object(tools, 'db', NoDatabase()), patch.object(logger, 'db', NoDatabase()), \
             patch.object(auth, 'db', auth_db), \
             patch.object(alc_intro, 'get_intro_session', AsyncMock(side_effect=AssertionError('root entry reached intro'))), \
             patch.object(bot.session, 'make_request', AsyncMock(side_effect=request)):
            context = dp.fsm.get_context(bot=bot, chat_id=ADMIN_ID, user_id=ADMIN_ID)
            for i, waiting in enumerate((None, tools.OwnerInput.waiting, 'PanelState:waiting_for_daily_time', 'LegacyForm:waiting'), 1):
                await context.set_state(waiting); await context.set_data({'action':'prepare', 'sentinel':i})
                msg = {'message_id':i, 'date':1760000000, 'chat':chat, 'from':user,
                       'text':'/raid_test@raid_owner_bot' if i == 2 else '/raid_test'}
                before = len(sent)
                await asyncio.wait_for(dp.feed_update(bot, Update.model_validate({'update_id':i, 'message':msg})), 4)
                new = sent[before:]
                assert len(new) == 1 and isinstance(new[0], SendMessage), new
                assert new[0].reply_markup.inline_keyboard[0][0].web_app.url.startswith(panel.RAID_PREVIEW_URL)
                assert await context.get_state() is None and await context.get_data() == {}
            for i, (sender, chat_id, kind) in enumerate(((ADMIN_ID+1, ADMIN_ID+1, 'private'),
                    (ADMIN_ID+1, ADMIN_ID, 'private'), (ADMIN_ID, ADMIN_ID+1, 'private'),
                    (ADMIN_ID, -1001, 'supergroup')), 20):
                who = dict(user, id=sender); where = {'id':chat_id, 'type':kind}
                denied_context = dp.fsm.get_context(bot=bot, chat_id=chat_id, user_id=sender)
                await denied_context.set_state(tools.OwnerInput.waiting)
                await denied_context.set_data({'sentinel':'must survive'})
                before = len(sent)
                msg = {'message_id':i, 'date':1760000000, 'chat':where, 'from':who, 'text':'/raid_test'}
                await asyncio.wait_for(dp.feed_update(bot, Update.model_validate({'update_id':i, 'message':msg})), 4)
                assert len(sent) == before
                assert await denied_context.get_state() == tools.OwnerInput.waiting.state
                assert await denied_context.get_data() == {'sentinel':'must survive'}
                cb = {'id':str(i), 'chat_instance':'test', 'from':who, 'data':'owner_panel:raid_test',
                      'message':{'message_id':99, 'date':1760000000, 'chat':where,
                                 'from':{'id':bot.id,'is_bot':True,'first_name':'Test'}, 'text':'Panel'}}
                await asyncio.wait_for(dp.feed_update(bot, Update.model_validate({'update_id':i+100, 'callback_query':cb})), 4)
                assert not any(isinstance(m, (SendMessage, EditMessageText)) for m in sent[before:])
                assert await denied_context.get_state() == tools.OwnerInput.waiting.state
            cb['id']='owner'; cb['from']=user; cb['message']['chat']=chat
            await context.set_state(tools.OwnerInput.waiting)
            await asyncio.wait_for(dp.feed_update(bot, Update.model_validate({'update_id':200, 'callback_query':cb})), 4)
            assert isinstance(sent[-1], EditMessageText)
            assert sent[-1].reply_markup.inline_keyboard[0][0].web_app
            assert await context.get_state() is None
    finally:
        await dp.storage.close()
        await bot.session.close()
asyncio.run(run())
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stderr)
