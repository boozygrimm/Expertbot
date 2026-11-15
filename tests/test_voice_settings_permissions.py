"""Changing chat-wide transcript publication requires chat admin rights."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Update
from mongomock_motor import AsyncMongoMockClient
from handlers import admin_panel, auth, voice_rec


class VoiceSettingsPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database = AsyncMongoMockClient().test
        self.db = SimpleNamespace(settings=database.settings, auth_chats=database.auth_chats)
        patcher = patch.object(voice_rec, 'db', self.db)
        patcher.start(); self.addCleanup(patcher.stop)

    def message(self, text, *, chat_id=-101, chat_type='supergroup', user_id=7, status='member'):
        bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status=status)))
        return SimpleNamespace(text=text, chat=SimpleNamespace(id=chat_id, type=chat_type),
            from_user=SimpleNamespace(id=user_id) if user_id is not None else None,
            bot=bot, reply=AsyncMock())

    async def stored(self, chat_id=-101):
        return await self.db.settings.find_one({'setting': 'auto_voice', 'chat_id': chat_id})

    async def test_member_cannot_enable_or_disable(self):
        for arg, current in (('on', False), ('off', True)):
            with self.subTest(arg=arg):
                await self.db.settings.update_one({'setting': 'auto_voice', 'chat_id': -101},
                                                  {'$set': {'value': current}}, upsert=True)
                msg = self.message('/voice ' + arg)
                await voice_rec.toggle_voice(msg)
                self.assertEqual((await self.stored())['value'], current)
                msg.bot.get_chat_member.assert_awaited_once_with(-101, 7)
                self.assertIn('администраторы', msg.reply.await_args.args[0])

    async def test_admin_and_group_creator_can_toggle(self):
        for status in ('administrator', 'creator'):
            for chat_type in ('group', 'supergroup'):
                with self.subTest(status=status, chat_type=chat_type):
                    msg = self.message('/voice on', status=status, chat_type=chat_type)
                    await voice_rec.toggle_voice(msg)
                    self.assertTrue((await self.stored())['value'])
                    msg.text = '/voice off'
                    await voice_rec.toggle_voice(msg)
                    self.assertFalse((await self.stored())['value'])

    async def test_bot_owner_can_switch_without_chat_member_lookup(self):
        msg = self.message('/voice on', user_id=admin_panel.ADMIN_ID)
        await voice_rec.toggle_voice(msg)
        self.assertTrue((await self.stored())['value'])
        msg.bot.get_chat_member.assert_not_awaited()

    async def test_private_user_can_switch_own_setting_without_admin_lookup(self):
        msg = self.message('/voice on', chat_id=7, chat_type='private')
        await voice_rec.toggle_voice(msg)
        self.assertTrue((await self.stored(7))['value'])
        msg.text = '/voice off'
        await voice_rec.toggle_voice(msg)
        self.assertFalse((await self.stored(7))['value'])
        msg.bot.get_chat_member.assert_not_awaited()

    async def test_status_available_to_member_without_admin_lookup_or_mutation(self):
        msg = self.message('/voice')
        await voice_rec.toggle_voice(msg)
        self.assertIsNone(await self.stored())
        msg.bot.get_chat_member.assert_not_awaited()
        self.assertIn('ВЫКЛЮЧЕНО', msg.reply.await_args.args[0])
        self.assertIn('администраторы', msg.reply.await_args.args[0])

    async def test_missing_sender_or_failed_permission_check_never_changes_setting(self):
        msg = self.message('/voice on', user_id=None)
        await voice_rec.toggle_voice(msg)
        self.assertIsNone(await self.stored())
        msg.bot.get_chat_member.assert_not_awaited()
        for error in (OSError('telegram unavailable'), TimeoutError()):
            with self.subTest(error=type(error).__name__):
                msg = self.message('/voice on')
                msg.bot.get_chat_member.side_effect = error
                await voice_rec.toggle_voice(msg)
                self.assertIsNone(await self.stored())
                self.assertIn('не удалось проверить', msg.reply.await_args.args[0].lower())

    async def test_real_dispatcher_allows_only_admin_to_switch(self):
        from aiogram.methods import GetChatMember, SendMessage
        bot = Bot('123456:TESTTOKEN')
        dp = Dispatcher()
        dp.message.middleware(auth.AuthMiddleware())
        dp.message.middleware(admin_panel.FeatureAccessMiddleware())
        dp.message.register(voice_rec.toggle_voice, Command('voice'))
        await self.db.auth_chats.insert_one({'chat_id': -101, 'status': 'approved'})
        def update(uid, user, text):
            return Update.model_validate({'update_id': uid, 'message': {
                'message_id': uid, 'date': 1760000000, 'chat': {'id': -101, 'type': 'supergroup'},
                'from': {'id': user, 'is_bot': False, 'first_name': 'Участник'}, 'text': text}})
        async def telegram(bot_arg, method, **kwargs):
            if isinstance(method, GetChatMember):
                return SimpleNamespace(status='administrator' if method.user_id == 8 else 'member')
            if isinstance(method, SendMessage):
                return True
            raise AssertionError(type(method))
        try:
            with patch.object(auth, 'db', self.db), patch.object(admin_panel, 'db', self.db), \
                 patch.object(bot.session, 'make_request', new=AsyncMock(side_effect=telegram)):
                await dp.feed_update(bot, update(1, 7, '/voice on'))
                self.assertIsNone(await self.stored())
                await dp.feed_update(bot, update(2, 8, '/voice on'))
                self.assertTrue((await self.stored())['value'])
                await dp.feed_update(bot, update(3, 7, '/voice off'))
                self.assertTrue((await self.stored())['value'])
                await dp.feed_update(bot, update(4, 8, '/voice off'))
                self.assertFalse((await self.stored())['value'])
        finally:
            await bot.session.close()

    async def test_real_dispatcher_blocks_unauthorized_chat_and_disabled_module(self):
        # aiogram's dispatch, auth and feature middleware run before the handler.
        bot = Bot('123456:TESTTOKEN')
        dp = Dispatcher()
        dp.message.middleware(auth.AuthMiddleware())
        dp.message.middleware(admin_panel.FeatureAccessMiddleware())
        dp.message.register(voice_rec.toggle_voice, Command('voice'))
        def update(uid):
            return Update.model_validate({'update_id': uid, 'message': {
                'message_id': uid, 'date': 1760000000,
                'chat': {'id': -101, 'type': 'supergroup'},
                'from': {'id': admin_panel.ADMIN_ID, 'is_bot': False, 'first_name': 'Админ'},
                'text': '/voice on'}})
        try:
            with patch.object(auth, 'db', self.db), patch.object(admin_panel, 'db', self.db), \
                 patch.object(bot, 'send_message', new=AsyncMock()) as send, \
                 patch.object(bot.session, 'make_request', new=AsyncMock()) as api:
                await dp.feed_update(bot, update(1))
                self.assertIsNone(await self.stored())
                send.assert_awaited_once()  # access denied
                await self.db.auth_chats.insert_one({'chat_id': -101, 'status': 'approved'})
                await self.db.settings.insert_one({'setting': 'feature_panel', 'chat_id': -101,
                                                   'features': {'voice': False}})
                await dp.feed_update(bot, update(2))
                self.assertIsNone(await self.stored())
                api.assert_not_awaited()
        finally:
            await bot.session.close()
