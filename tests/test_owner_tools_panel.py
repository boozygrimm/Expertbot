import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
from mongomock_motor import AsyncMongoMockClient
from handlers import owner_tools_panel as tools, admin_cron, owner_control_panel


class OwnerToolsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        self.dbpatch = patch.object(tools, 'db', SimpleNamespace(db=self.db))
        self.dbpatch.start(); self.addCleanup(self.dbpatch.stop)
        self.user = SimpleNamespace(id=tools.ADMIN_ID)
        self.message = SimpleNamespace(chat=SimpleNamespace(type='private', id=tools.ADMIN_ID), message_id=23,
            answer=AsyncMock(), reply=AsyncMock(), answer_photo=AsyncMock(), edit_text=AsyncMock())
        self.cb = SimpleNamespace(from_user=self.user, message=self.message, data='', answer=AsyncMock())
        self.state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock(), update_data=AsyncMock(), get_data=AsyncMock())

    async def test_buttons_and_access(self):
        with patch.object(owner_control_panel, 'db', SimpleNamespace(db=self.db, settings=self.db.settings)):
            _, markup = await owner_control_panel.home_content()
            self.assertIn('owner_panel:tools', [b.callback_data for row in markup.inline_keyboard for b in row])
        self.cb.data = 'owner_tools:regen'
        self.cb.from_user = SimpleNamespace(id=tools.ADMIN_ID + 1)
        await tools.action(self.cb, self.state, None)
        self.assertEqual(await self.db.owner_panel_confirmations.count_documents({}), 0)
        self.cb.from_user = self.user
        self.message.chat.type = 'group'
        await tools.action(self.cb, self.state, None)
        self.assertEqual(await self.db.owner_panel_confirmations.count_documents({}), 0)

    async def test_confirmation_is_single_use_and_cancel(self):
        from handlers import alc_quests
        self.cb.data = 'owner_tools:regen'
        await tools.action(self.cb, self.state, None)
        row = self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard[0]
        token_data = row[0].callback_data
        self.cb.data = token_data
        with patch.object(alc_quests, 'admin_regen_quests', AsyncMock()) as regen:
            await tools.action(self.cb, self.state, None)
            await tools.action(self.cb, self.state, None)
            regen.assert_awaited_once()
        self.cb.data = 'owner_tools:regen'
        await tools.action(self.cb, self.state, None)
        markup = self.message.answer.await_args.kwargs['reply_markup']
        self.cb.data = markup.inline_keyboard[1][0].callback_data
        await tools.action(self.cb, self.state, None)
        self.cb.data = markup.inline_keyboard[0][0].callback_data
        with patch.object(alc_quests, 'admin_regen_quests', AsyncMock()) as regen:
            await tools.action(self.cb, self.state, None)
            regen.assert_not_awaited()

    async def test_prepare_input_validation_and_dispatch(self):
        from handlers import news_owner_drafts
        self.message.from_user = self.user
        self.message.text = 'not a link'
        self.state.get_data.return_value = {'action':'prepare'}
        await tools.receive_input(self.message, self.state)
        self.state.clear.assert_not_awaited()
        self.message.text = 'https://t.me/example/123'
        with patch.object(news_owner_drafts, 'prepare', AsyncMock()) as prepare:
            await tools.receive_input(self.message, self.state)
            prepare.assert_awaited_once()
            self.assertEqual(prepare.await_args.args[0].text, '/news_prepare https://t.me/example/123')
            self.state.clear.assert_awaited_once()

    async def test_reset_requires_existing_player_and_confirmation(self):
        self.message.from_user = self.user
        self.message.text = '42'
        self.state.get_data.return_value = {'action':'reset'}
        await tools.receive_input(self.message, self.state)
        self.state.clear.assert_not_awaited()
        await self.db.alchemy_players.insert_one({'user_id': 42})
        await tools.receive_input(self.message, self.state)
        self.state.clear.assert_awaited_once()
        record = await self.db.owner_panel_confirmations.find_one({})
        self.assertEqual(record['data']['player_id'], 42)
        self.assertEqual(await self.db.alchemy_players.count_documents({}), 1)

    async def test_cron_rejects_other_users_and_bad_group(self):
        self.cb.from_user = SimpleNamespace(id=tools.ADMIN_ID+1)
        self.cb.data = 'c_stop:-123'
        fake = SimpleNamespace(settings=self.db.settings)
        with patch.object(admin_cron, 'db', fake):
            await admin_cron.cb_cron_select_stop(self.cb, None)
            self.assertEqual(await self.db.settings.count_documents({}), 0)
            self.cb.from_user = self.user
            self.cb.data = 'c_stop:42'
            await admin_cron.cb_cron_select_stop(self.cb, None)
            self.assertEqual(await self.db.settings.count_documents({}), 0)

    async def test_real_dispatcher_guided_input_and_cancel(self):
        import subprocess, sys
        script = r'''
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiogram import Bot
from aiogram.methods import SendMessage
from aiogram.types import Update
from mongomock_motor import AsyncMongoMockClient
from config import ADMIN_ID
from app_setup import configure_dispatcher
from handlers import owner_tools_panel as tools, owner_control_panel, news_owner_drafts, alc_build, alc_intro, logger

async def run():
    database = AsyncMongoMockClient().test
    fake = SimpleNamespace(db=database, settings=database.settings, alchemy_players=database.alchemy_players, messages=database.messages)
    bot = Bot('123456:TESTTOKEN')
    sent = []
    async def request(bot_arg, method, **kwargs):
        sent.append(method)
        return True
    dp = configure_dispatcher()
    with patch.object(tools, 'db', fake), patch.object(owner_control_panel, 'db', fake), patch.object(logger, 'db', fake), \
         patch.object(alc_build, 'db', fake), patch.object(alc_intro, 'get_intro_session', AsyncMock(return_value=None)), \
         patch.object(tools.owner_trial, 'drafts_enabled', AsyncMock(return_value=True)), \
         patch.object(news_owner_drafts, 'prepare', AsyncMock()) as prepare, \
         patch.object(bot.session, 'make_request', AsyncMock(side_effect=request)):
        user = {'id':ADMIN_ID, 'is_bot':False, 'first_name':'Owner'}
        chat = {'id':ADMIN_ID, 'type':'private'}
        async def cb(i, data):
            await asyncio.wait_for(dp.feed_update(bot, Update.model_validate({'update_id':i, 'callback_query':{
                'id':str(i), 'chat_instance':'owner', 'from':user, 'data':data,
                'message':{'message_id':10, 'date':1760000000, 'chat':chat,
                           'from':{'id':123456,'is_bot':True,'first_name':'Bot'}, 'text':'Panel'}}})), timeout=5)
        async def msg(i, text):
            await asyncio.wait_for(dp.feed_update(bot, Update.model_validate({'update_id':i, 'message':{
                'message_id':i, 'date':1760000000, 'chat':chat, 'from':user, 'text':text}})), timeout=5)
        await cb(1, 'owner_tools:prepare')
        assert any(isinstance(m, SendMessage) and 'четырёх ссылок' in m.text for m in sent), sent
        await msg(2, 'https://t.me/example/123')
        assert prepare.await_count == 1 and prepare.await_args.args[0].message_id == 2
        await cb(3, 'owner_tools:prepare')
        await cb(4, 'owner_tools:cancel_input')
        from aiogram.fsm.storage.base import StorageKey
        key = StorageKey(bot_id=bot.id, chat_id=ADMIN_ID, user_id=ADMIN_ID)
        assert await dp.storage.get_state(key) is None
        assert prepare.await_count == 1
    await bot.session.close()
asyncio.run(run())
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    async def test_points_target_group_and_single_execution(self):
        self.message.from_user = self.user
        self.message.text = '42 -15'
        await self.db.economy.insert_one({'chat_id': -100, 'user_id': 42, 'user_name':'Игрок', 'score':100})
        await self.db.economy.insert_one({'chat_id': -200, 'user_id': 42, 'user_name':'Игрок', 'score':500})
        self.state.get_data.return_value = {'action':'points', 'chat_id':-100, 'chat_title':'Группа A'}
        await tools.receive_input(self.message, self.state)
        conf = await self.db.owner_panel_confirmations.find_one({})
        self.assertIn('Группа A', self.message.answer.await_args.args[0])
        self.assertEqual(conf['data']['chat_id'], -100)
        self.cb.data = 'owner_tools:run:' + conf['_id']
        await tools.action(self.cb, self.state, None)
        await tools.action(self.cb, self.state, None)
        entries = await self.db.economy.find({'game_type':'promo'}).to_list(length=10)
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0]['chat_id'], entries[0]['user_id'], entries[0]['score']), (-100, 42, -15))

    async def test_points_unknown_player_needs_name_and_invalid_sums_are_rejected(self):
        self.message.from_user = self.user
        self.state.get_data.return_value = {'action':'points', 'chat_id':-100, 'chat_title':'A'}
        await self.db.economy.insert_one({'chat_id':-200, 'user_id':42, 'user_name':'Игрок'})
        for text in ('42 100', '42 0', '42 1000001', 'abc 100', '42 +5'):
            self.message.text = text
            await tools.receive_input(self.message, self.state)
        self.state.clear.assert_not_awaited()
        self.assertEqual(await self.db.owner_panel_confirmations.count_documents({}), 0)

    async def test_clear_and_pack_require_confirmation(self):
        from handlers import alc_core
        self.message.from_user = self.user
        self.message.text = '42'
        await self.db.alchemy_players.insert_one({'user_id':42, 'user_name':'Alice', 'inventory': {'w':10}})
        self.state.get_data.return_value = {'action':'clear'}
        await tools.receive_input(self.message, self.state)
        self.assertEqual((await self.db.alchemy_players.find_one({'user_id':42}))['inventory'], {'w':10})
        conf = await self.db.owner_panel_confirmations.find_one({})
        self.cb.data = 'owner_tools:run:' + conf['_id']
        with patch.object(alc_core, 'db', SimpleNamespace(alchemy_players=self.db.alchemy_players)):
            await tools.action(self.cb, self.state, None)
        self.assertEqual((await self.db.alchemy_players.find_one({'user_id':42}))['inventory'], {})
        self.state.get_data.return_value = {'action':'pack','size':'m'}
        await tools.receive_input(self.message, self.state)
        conf = await self.db.owner_panel_confirmations.find_one({'action':'pack'})
        self.cb.data = 'owner_tools:run:' + conf['_id']
        with patch.object(alc_core, 'admin_promo', AsyncMock()) as promo:
            await tools.action(self.cb, self.state, None)
            args = promo.await_args.args
            self.assertEqual(args[0].reply_to_message.from_user.id, 42)
            self.assertEqual(args[1].args, 'm')

    async def test_all_cron_callbacks_and_fsm_reject_non_owner(self):
        self.cb.from_user = SimpleNamespace(id=tools.ADMIN_ID + 1)
        self.cb.data = 'c_set:-123'
        for function, args in (
            (admin_cron.cb_cron_menu_set, (None,)),
            (admin_cron.cb_cron_select_set, (self.state,)),
            (admin_cron.cb_cron_menu_stop, (None,)),
            (admin_cron.cb_cron_select_stop, (None,)),
            (admin_cron.cb_cron_status, (None,)),
            (admin_cron.cb_cron_back, (self.state,)),
        ):
            with self.subTest(function=function.__name__):
                self.cb.answer.reset_mock()
                await function(self.cb, *args)
                self.assertTrue(self.cb.answer.await_args.kwargs['show_alert'])
        self.message.from_user = self.cb.from_user
        self.message.text = '09:00'
        with patch.object(admin_cron, 'db', SimpleNamespace(settings=self.db.settings)):
            await admin_cron.process_time_set(self.message, None, self.state)
        self.assertEqual(await self.db.settings.count_documents({}), 0)
        self.state.update_data.assert_not_awaited()

    async def test_group_points_menu_revalidates_group(self):
        from handlers import admin_cron
        self.cb.data = 'owner_tools:pointsgroup:-123'
        with patch.object(admin_cron, 'get_known_groups', AsyncMock(return_value={-123:'Группа A'})):
            await tools.action(self.cb, self.state, None)
        self.assertIn('Группа A', self.message.answer.await_args.args[0])
        self.state.update_data.reset_mock()
        with patch.object(admin_cron, 'get_known_groups', AsyncMock(return_value={-456:'Группа B'})):
            await tools.action(self.cb, self.state, None)
        self.state.update_data.assert_not_awaited()

    async def test_profiles_button_selects_exact_player_with_confirmation(self):
        await self.db.alchemy_players.insert_one({'user_id':42, 'user_name':'Alice', 'inventory': {'w':5}})
        self.cb.data = 'owner_tools:clear'
        await tools.action(self.cb, self.state, None)
        buttons = self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard
        data = next(b.callback_data for row in buttons for b in row if 'Alice' in b.text)
        self.cb.data = data
        await tools.action(self.cb, self.state, None)
        conf = await self.db.owner_panel_confirmations.find_one({})
        self.assertEqual((conf['action'], conf['data']['player_id']), ('clear', 42))
        self.assertEqual((await self.db.alchemy_players.find_one({'user_id':42}))['inventory'], {'w':5})
        self.cb.data = 'owner_tools:profile:reset:42'
        await tools.action(self.cb, self.state, None)
        conf = await self.db.owner_panel_confirmations.find_one({'action':'reset'})
        self.assertEqual(conf['data']['player_id'], 42)
        self.assertEqual(await self.db.alchemy_players.count_documents({}), 1)

    async def test_image_generation_prompt_and_explicit_confirmation(self):
        from handlers import image_gen
        self.message.from_user = self.user
        self.message.text = 'Рыжий кот в космосе'
        self.state.get_data.return_value = {'action':'draw'}
        with patch.object(image_gen, 'process_image_request', AsyncMock()) as draw:
            await tools.receive_input(self.message, self.state)
            draw.assert_not_awaited()
            conf = await self.db.owner_panel_confirmations.find_one({'action':'draw'})
            self.assertIn('попытки', self.message.answer.await_args.args[0])
            self.cb.data = 'owner_tools:run:' + conf['_id']
            await tools.action(self.cb, self.state, None)
            await tools.action(self.cb, self.state, None)
            draw.assert_awaited_once()
            self.assertEqual(draw.await_args.args[1], 'Рыжий кот в космосе')
            self.assertEqual(draw.await_args.args[0].chat.id, tools.ADMIN_ID)
            self.assertEqual(draw.await_args.args[0].from_user.id, tools.ADMIN_ID)

    async def test_owner_can_open_base_and_help_via_buttons(self):
        from handlers import alc_base
        self.cb.data = 'owner_tools:base'
        with patch.object(alc_base, 'cmd_base', AsyncMock()) as base:
            await tools.action(self.cb, self.state, None)
            self.assertEqual(base.await_args.args[0].from_user.id, tools.ADMIN_ID)
        self.cb.data = 'owner_tools:help'
        await tools.action(self.cb, self.state, None)
        from handlers.ex_logic import HELP_TEXT
        self.assertEqual(self.message.answer.await_args.args[0], HELP_TEXT)

    async def test_points_player_buttons_keep_exact_group_and_user(self):
        from handlers import admin_cron
        await self.db.economy.insert_one({'chat_id':-123, 'user_id':42, 'user_name':'Alice', 'score':10})
        await self.db.economy.insert_one({'chat_id':-999, 'user_id':43, 'user_name':'Bob', 'score':10})
        with patch.object(admin_cron, 'get_known_groups', AsyncMock(return_value={-123:'Группа A'})):
            self.cb.data = 'owner_tools:pointsgroup:-123'
            await tools.action(self.cb, self.state, None)
            buttons = [b for row in self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
            self.assertTrue(any('Alice' in b.text for b in buttons))
            self.assertFalse(any('Bob' in b.text for b in buttons))
            self.cb.data = next(b.callback_data for b in buttons if 'Alice' in b.text)
            await tools.action(self.cb, self.state, None)
            data = self.state.update_data.await_args.kwargs
            self.assertEqual((data['chat_id'], data['player_id']), (-123, 42))
            self.message.from_user = self.user
            self.message.text = '100'
            self.state.get_data.return_value = data
            await tools.receive_input(self.message, self.state)
            conf = await self.db.owner_panel_confirmations.find_one({'action':'points'})
            self.assertEqual((conf['data']['chat_id'], conf['data']['player_id'], conf['data']['amount']), (-123,42,100))
            self.cb.data = 'owner_tools:run:' + conf['_id']
            await tools.action(self.cb, self.state, None)
        row = await self.db.economy.find_one({'game_type':'promo'})
        self.assertEqual((row['chat_id'], row['user_id'], row['score']), (-123,42,100))

    async def test_can_grant_new_player_only_after_explicit_confirmation(self):
        self.message.from_user = self.user
        self.message.text = '88 75'
        self.state.get_data.return_value = {'action':'points', 'chat_id':-123, 'chat_title':'Группа A'}
        await tools.receive_input(self.message, self.state)
        self.assertEqual(self.state.update_data.await_args.kwargs['action'], 'points_new_name')
        self.assertEqual(await self.db.economy.count_documents({}), 0)
        self.message.text = 'Новичок'
        self.state.get_data.return_value = {'action':'points_new_name', 'chat_id':-123,
                                           'chat_title':'Группа A', 'player_id':88, 'amount':75}
        await tools.receive_input(self.message, self.state)
        conf = await self.db.owner_panel_confirmations.find_one({'action':'points'})
        self.assertTrue(conf['data']['allow_new'])
        self.assertIn('Группа A', self.message.answer.await_args.args[0])
        self.assertEqual(await self.db.economy.count_documents({}), 0)
        self.cb.data = 'owner_tools:run:' + conf['_id']
        await tools.action(self.cb, self.state, None)
        row = await self.db.economy.find_one({})
        self.assertEqual((row['chat_id'], row['user_id'], row['user_name'], row['score']), (-123,88,'Новичок',75))

    async def test_pack_can_create_new_profile_only_after_confirmation(self):
        from handlers import alc_core
        self.message.from_user = self.user
        self.message.text = '88'
        self.state.get_data.return_value = {'action':'pack', 'size':'s'}
        await tools.receive_input(self.message, self.state)
        self.assertEqual(self.state.update_data.await_args.kwargs['action'], 'pack_new_name')
        self.assertEqual(await self.db.alchemy_players.count_documents({}), 0)
        self.message.text = 'Новичок'
        self.state.get_data.return_value = {'action':'pack_new_name', 'player_id':88, 'size':'s'}
        await tools.receive_input(self.message, self.state)
        conf = await self.db.owner_panel_confirmations.find_one({'action':'pack'})
        self.assertTrue(conf['data']['allow_create'])
        self.cb.data = 'owner_tools:run:' + conf['_id']
        with patch.object(alc_core, 'admin_promo', AsyncMock()) as promo:
            await tools.action(self.cb, self.state, None)
            self.assertEqual(promo.await_args.args[0].reply_to_message.from_user.id, 88)
            self.assertEqual(promo.await_args.args[0].reply_to_message.from_user.first_name, 'Новичок')

    async def test_open_lore_warns_before_legacy_deletion(self):
        from handlers import alc_heritage
        await self.db.alchemy_lore.insert_one({'id':'forge_legacy', 'name':'старое наследие'})
        self.cb.data = 'owner_tools:lore'
        with patch.object(alc_heritage, 'lore_edit', AsyncMock()) as lore:
            await tools.action(self.cb, self.state, None)
            lore.assert_not_awaited()
            self.assertIn('удалит', self.message.answer.await_args.args[0])
            conf = await self.db.owner_panel_confirmations.find_one({'action':'lore_open'})
            self.cb.data = 'owner_tools:run:' + conf['_id']
            await tools.action(self.cb, self.state, None)
            lore.assert_awaited_once()
