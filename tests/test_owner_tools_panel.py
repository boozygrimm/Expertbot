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
            answer=AsyncMock(), reply=AsyncMock(), answer_photo=AsyncMock(), edit_text=AsyncMock(), delete=AsyncMock())
        self.cb = SimpleNamespace(from_user=self.user, message=self.message, data='', answer=AsyncMock())
        self.state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock(), update_data=AsyncMock(), get_data=AsyncMock(), get_state=AsyncMock())

    async def test_buttons_and_access(self):
        with patch.object(owner_control_panel, 'db', SimpleNamespace(db=self.db, settings=self.db.settings)):
            _, markup = await owner_control_panel.home_content()
            callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
            self.assertNotIn('owner_panel:alchemy', callbacks)
        self.cb.data = 'owner_tools:image_probe'
        self.cb.from_user = SimpleNamespace(id=tools.ADMIN_ID + 1)
        await tools.action(self.cb, self.state, None)
        self.assertEqual(await self.db.owner_panel_confirmations.count_documents({}), 0)
        self.cb.from_user = self.user
        self.message.chat.type = 'group'
        await tools.action(self.cb, self.state, None)
        self.assertEqual(await self.db.owner_panel_confirmations.count_documents({}), 0)

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
from handlers import owner_tools_panel as tools, owner_control_panel, news_owner_drafts, logger

async def run():
    database = AsyncMongoMockClient().test
    fake = SimpleNamespace(db=database, settings=database.settings, messages=database.messages)
    bot = Bot('123456:TESTTOKEN')
    sent = []
    async def request(bot_arg, method, **kwargs):
        sent.append(method)
        return True
    dp = configure_dispatcher()
    with patch.object(tools, 'db', fake), patch.object(owner_control_panel, 'db', fake), patch.object(logger, 'db', fake), \
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
        prompt = [m for m in sent if isinstance(m, SendMessage) and 'четырёх ссылок' in m.text][-1]
        await cb(4, prompt.reply_markup.inline_keyboard[0][0].callback_data)
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

    async def test_owner_can_open_help_and_removed_alchemy_buttons_do_nothing(self):
        self.cb.data = 'owner_tools:base'
        await tools.action(self.cb, self.state, None)
        self.message.answer.assert_not_awaited()
        self.assertTrue(self.cb.answer.await_args.kwargs['show_alert'])
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

    async def test_probe_cancel_closes_confirmation_and_never_runs(self):
        from handlers import image_gen
        self.cb.data = 'owner_tools:image_probe'
        await tools.action(self.cb, self.state, None)
        markup = self.message.answer.await_args.kwargs['reply_markup']
        confirm = markup.inline_keyboard[0][0].callback_data
        cancel = markup.inline_keyboard[1][0].callback_data
        self.cb.data = cancel
        with patch.object(image_gen, 'cmd_image_probe', AsyncMock()) as probe:
            await tools.action(self.cb, self.state, None)
            self.message.delete.assert_awaited_once()
            self.assertEqual(self.cb.answer.await_args.args[0], 'Отменено')
            self.cb.data = confirm
            await tools.action(self.cb, self.state, None)
            probe.assert_not_awaited()
        record = await self.db.owner_panel_confirmations.find_one({})
        self.assertEqual(record['stage'], 'cancelled')

    async def test_cancel_expired_or_missing_confirmation_still_closes(self):
        from datetime import datetime, timedelta, timezone
        self.cb.data = 'owner_tools:image_probe'
        await tools.action(self.cb, self.state, None)
        markup = self.message.answer.await_args.kwargs['reply_markup']
        cancel = markup.inline_keyboard[1][0].callback_data
        token = cancel.split(':')[-1]
        await self.db.owner_panel_confirmations.update_one({'_id': token},
            {'$set': {'expires_at': datetime.now(timezone.utc) - timedelta(seconds=1)}})
        self.cb.data = cancel
        await tools.action(self.cb, self.state, None)
        self.assertEqual((await self.db.owner_panel_confirmations.find_one({'_id':token}))['stage'], 'cancelled')
        self.message.delete.assert_awaited_once()
        self.assertEqual(self.cb.answer.await_args.args[0], 'Отменено')
        # A duplicate callback and a cleaned-up token should both dismiss the UI.
        await tools.action(self.cb, self.state, None)
        self.assertEqual(self.cb.answer.await_args.args[0], 'Отменено')
        await self.db.owner_panel_confirmations.delete_one({'_id':token})
        await tools.action(self.cb, self.state, None)
        self.assertIn('недоступно', self.cb.answer.await_args.args[0])
        self.assertEqual(self.message.delete.await_count, 3)

    async def test_cancel_falls_back_to_removing_buttons_when_delete_fails(self):
        self.cb.data = 'owner_tools:image_probe'
        await tools.action(self.cb, self.state, None)
        self.cb.data = self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard[1][0].callback_data
        self.message.delete.side_effect = RuntimeError('cannot delete')
        await tools.action(self.cb, self.state, None)
        self.assertEqual(self.message.edit_text.await_args.args[0], 'Отменено. Действие не запущено.')
        self.assertIsNone(self.message.edit_text.await_args.kwargs['reply_markup'])
        self.assertEqual((await self.db.owner_panel_confirmations.find_one({}))['stage'], 'cancelled')

    async def test_cancel_after_claim_does_not_hide_or_stop_running_action(self):
        self.cb.data = 'owner_tools:image_probe'
        await tools.action(self.cb, self.state, None)
        self.cb.data = self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard[1][0].callback_data
        token = self.cb.data.split(':')[-1]
        for stage in ('running', 'done'):
            await self.db.owner_panel_confirmations.update_one({'_id':token}, {'$set': {'stage':stage}})
            self.message.delete.reset_mock(); self.message.edit_text.reset_mock()
            await tools.action(self.cb, self.state, None)
            self.assertTrue(self.cb.answer.await_args.kwargs['show_alert'])
            self.assertIn('нельзя', self.cb.answer.await_args.args[0])
            self.message.delete.assert_not_awaited()
            self.message.edit_text.assert_not_awaited()
            self.assertEqual((await self.db.owner_panel_confirmations.find_one({'_id':token}))['stage'], stage)

    async def test_cancel_is_owner_private_only(self):
        self.cb.data = 'owner_tools:image_probe'
        await tools.action(self.cb, self.state, None)
        self.cb.data = self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard[1][0].callback_data
        self.cb.from_user = SimpleNamespace(id=tools.ADMIN_ID+1)
        await tools.action(self.cb, self.state, None)
        self.cb.from_user = self.user
        self.message.chat.type = 'group'
        await tools.action(self.cb, self.state, None)
        self.message.delete.assert_not_awaited()
        self.assertEqual((await self.db.owner_panel_confirmations.find_one({}))['stage'], 'ready')

    async def test_cancel_during_claim_does_not_break_running_request(self):
        import asyncio
        from handlers import image_gen
        self.cb.data = 'owner_tools:image_probe'
        await tools.action(self.cb, self.state, None)
        markup = self.message.answer.await_args.kwargs['reply_markup']
        entered, proceed = asyncio.Event(), asyncio.Event()
        async def run_ack(*args, **kwargs):
            entered.set()
            await proceed.wait()
        run_callback = SimpleNamespace(from_user=self.user, message=self.message,
                                       data=markup.inline_keyboard[0][0].callback_data, answer=AsyncMock(side_effect=run_ack))
        cancel_callback = SimpleNamespace(from_user=self.user, message=self.message,
                                          data=markup.inline_keyboard[1][0].callback_data, answer=AsyncMock())
        with patch.object(image_gen, 'cmd_image_probe', AsyncMock()) as probe:
            task = asyncio.create_task(tools.action(run_callback, self.state, None))
            await asyncio.wait_for(entered.wait(), 2)
            try:
                await tools.action(cancel_callback, self.state, None)
                self.message.delete.assert_not_awaited()
                self.assertTrue(cancel_callback.answer.await_args.kwargs['show_alert'])
            finally:
                proceed.set()
                await asyncio.wait_for(task, 2)
            probe.assert_awaited_once()
            self.message.edit_text.assert_awaited_once()

    async def test_real_dispatcher_probe_cancel_removes_message(self):
        import subprocess, sys
        script = r'''
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiogram import Bot
from aiogram.methods import SendMessage, DeleteMessage, AnswerCallbackQuery
from aiogram.types import Update
from mongomock_motor import AsyncMongoMockClient
from config import ADMIN_ID
from app_setup import configure_dispatcher
from handlers import owner_tools_panel, image_gen

async def run():
    database = AsyncMongoMockClient().test
    fake = SimpleNamespace(db=database, settings=database.settings)
    bot = Bot('123456:TESTTOKEN')
    calls = []
    async def request(bot_arg, method, **kwargs):
        calls.append(method)
        return True
    dp = configure_dispatcher()
    with patch.object(owner_tools_panel, 'db', fake), \
         patch.object(image_gen, 'cmd_image_probe', AsyncMock()) as probe, \
         patch.object(bot.session, 'make_request', AsyncMock(side_effect=request)):
        user = {'id':ADMIN_ID, 'is_bot':False, 'first_name':'Owner'}
        chat = {'id':ADMIN_ID, 'type':'private'}
        async def click(i, data, message_id):
            update = Update.model_validate({'update_id':i, 'callback_query':{
                'id':str(i), 'chat_instance':'owner', 'from':user, 'data':data,
                'message':{'message_id':message_id, 'date':1760000000, 'chat':chat,
                           'from':{'id':123456,'is_bot':True,'first_name':'Bot'}, 'text':'Panel'}}})
            await asyncio.wait_for(dp.feed_update(bot, update), 5)
        await click(1, 'owner_tools:image_probe', 22)
        prompt = next(m for m in calls if isinstance(m, SendMessage) and 'Cloudflare и OVHcloud' in m.text)
        confirm = prompt.reply_markup.inline_keyboard[0][0].callback_data
        cancel = prompt.reply_markup.inline_keyboard[1][0].callback_data
        await click(2, cancel, 23)
        assert any(isinstance(m, DeleteMessage) and m.message_id == 23 for m in calls), calls
        assert any(isinstance(m, AnswerCallbackQuery) and m.text == 'Отменено' for m in calls), calls
        await click(3, confirm, 23)
        assert probe.await_count == 0
        assert (await database.owner_panel_confirmations.find_one({}))['stage'] == 'cancelled'
    await bot.session.close()
asyncio.run(run())
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    async def test_duplicate_cancel_when_message_already_deleted_is_quiet(self):
        self.cb.data = 'owner_tools:image_probe'
        await tools.action(self.cb, self.state, None)
        self.cb.data = self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard[1][0].callback_data
        await tools.action(self.cb, self.state, None)
        self.message.answer.reset_mock()
        self.message.delete.side_effect = RuntimeError('already deleted')
        self.message.edit_text.side_effect = RuntimeError('already deleted')
        await tools.action(self.cb, self.state, None)
        self.assertEqual(self.cb.answer.await_args.args[0], 'Отменено')
        self.message.answer.assert_not_awaited()
        self.assertEqual((await self.db.owner_panel_confirmations.find_one({}))['stage'], 'cancelled')

    async def test_cancel_closes_even_when_callback_answer_fails(self):
        self.cb.data = 'owner_tools:image_probe'
        await tools.action(self.cb, self.state, None)
        self.cb.data = self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard[1][0].callback_data
        self.cb.answer.side_effect = RuntimeError('callback expired')
        await tools.action(self.cb, self.state, None)
        self.message.delete.assert_awaited_once()
        self.assertEqual((await self.db.owner_panel_confirmations.find_one({}))['stage'], 'cancelled')

    async def test_cancel_db_failure_does_not_claim_success_or_remove_button(self):
        self.cb.data = 'owner_tools:image_probe'
        await tools.action(self.cb, self.state, None)
        self.cb.data = self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard[1][0].callback_data
        collection = self.db.owner_panel_confirmations
        with patch.object(collection, 'update_one', AsyncMock(side_effect=OSError('database unavailable'))), \
             patch.object(tools.db.db, 'owner_panel_confirmations', collection):
            await tools.action(self.cb, self.state, None)
        self.assertTrue(self.cb.answer.await_args.kwargs['show_alert'])
        self.message.delete.assert_not_awaited()
        self.assertEqual((await collection.find_one({}))['stage'], 'ready')

    async def test_old_input_cancel_cannot_clear_new_or_unrelated_state(self):
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.memory import MemoryStorage
        from aiogram.fsm.storage.base import StorageKey
        storage = MemoryStorage()
        state = FSMContext(storage, StorageKey(bot_id=1, chat_id=tools.ADMIN_ID, user_id=tools.ADMIN_ID))
        self.cb.data = 'owner_tools:search_query'
        await tools.action(self.cb, state, None)
        old_button = self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
        self.cb.data = 'owner_tools:draw'
        await tools.action(self.cb, state, None)
        new_button = self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
        before = await state.get_data()
        self.assertNotEqual(old_button, new_button)
        self.cb.data = old_button
        await tools.action(self.cb, state, None)
        self.assertEqual(await state.get_state(), tools.OwnerInput.waiting.state)
        self.assertEqual(await state.get_data(), before)
        self.assertIn('уже завершён', self.cb.answer.await_args.args[0])
        # A pre-upgrade generic button cannot clear a new tokenized form.
        self.cb.data = 'owner_tools:cancel_input'
        await tools.action(self.cb, state, None)
        self.assertEqual(await state.get_data(), before)
        self.cb.data = new_button
        await tools.action(self.cb, state, None)
        self.assertIsNone(await state.get_state())
        await state.set_state(admin_cron.CronAdminState.waiting_for_time_set)
        await state.update_data(target_chat_id=-42)
        self.cb.data = old_button
        await tools.action(self.cb, state, None)
        self.assertEqual(await state.get_state(), admin_cron.CronAdminState.waiting_for_time_set.state)
        self.assertEqual((await state.get_data())['target_chat_id'], -42)
        await storage.close()

    async def test_legacy_input_cancel_works_only_for_legacy_state(self):
        self.state.get_state.return_value = tools.OwnerInput.waiting.state
        self.state.get_data.return_value = {'action':'search_query'}
        self.cb.data = 'owner_tools:cancel_input'
        await tools.action(self.cb, self.state, None)
        self.state.clear.assert_awaited_once()
        self.assertEqual(self.cb.answer.await_args.args[0], 'Ввод отменён')

    async def test_old_input_cancel_and_new_ask_are_serialized(self):
        import asyncio
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.memory import MemoryStorage
        from aiogram.fsm.storage.base import StorageKey
        storage = MemoryStorage()
        state = FSMContext(storage, StorageKey(bot_id=1, chat_id=tools.ADMIN_ID, user_id=tools.ADMIN_ID))
        self.cb.data = 'owner_tools:search_query'
        await tools.action(self.cb, state, None)
        old_button = self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
        entered, proceed = asyncio.Event(), asyncio.Event()
        original_get_state = state.get_state
        async def paused_get_state():
            result = await original_get_state()
            entered.set()
            await proceed.wait()
            return result
        cancel_cb = SimpleNamespace(from_user=self.user, message=self.message, data=old_button, answer=AsyncMock())
        new_cb = SimpleNamespace(from_user=self.user, message=self.message, data='owner_tools:search_query', answer=AsyncMock())
        with patch.object(state, 'get_state', side_effect=paused_get_state):
            cancel_task = asyncio.create_task(tools.action(cancel_cb, state, None))
            await asyncio.wait_for(entered.wait(), 2)
            ask_task = asyncio.create_task(tools.action(new_cb, state, None))
            await asyncio.sleep(0)
            self.assertFalse(ask_task.done())
            proceed.set()
            await asyncio.wait_for(asyncio.gather(cancel_task, ask_task), 2)
        self.assertEqual(await state.get_state(), tools.OwnerInput.waiting.state)
        self.assertEqual((await state.get_data())['action'], 'search_query')
        await storage.close()
