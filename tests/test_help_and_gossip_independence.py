"""Real aiogram routing for help and gossip, with external services mocked."""
import asyncio
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from aiogram import Bot
from aiogram.methods import GetChat, SendMessage
from aiogram.types import Update, User
from mongomock_motor import AsyncMongoMockClient

from app_setup import configure_dispatcher
from handlers import admin_panel, alc_base, alc_build, alc_intro, auth, ex_logic, logger, reports


class HelpAndGossipIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatcher_gossip_help_authorization_and_deep_links(self):
        database = AsyncMongoMockClient().test
        db = SimpleNamespace(settings=database.settings, auth_chats=database.auth_chats,
                             personas=database.personas, messages=database.messages,
                             alchemy_players=database.alchemy_players)
        await db.auth_chats.insert_one({'chat_id': -101, 'status': 'approved'})
        await db.settings.insert_one({'setting': 'feature_panel', 'chat_id': -101,
            'features': {'reports': False, 'daily_reports': False, 'assistant': True}})
        await db.personas.insert_one({'p_id': 'bender', 'name': 'Бендер', 'emoji': '🤖'})
        dp = configure_dispatcher()
        bot = Bot('123456:TESTTOKEN')
        requests = []
        async def telegram(bot_arg, method, **kwargs):
            requests.append(method)
            if isinstance(method, GetChat):
                return SimpleNamespace(active_usernames=[])
            return True

        counter = 0
        async def message(text, *, group=True, reply_to_bot=False):
            nonlocal counter
            counter += 1
            chat = {'id': -101, 'type': 'supergroup'} if group else {'id': 7, 'type': 'private'}
            payload = {'message_id': counter, 'date': 1760000000, 'chat': chat,
                       'from': {'id': 7, 'is_bot': False, 'first_name': 'Аня'}, 'text': text}
            if reply_to_bot:
                payload['reply_to_message'] = {'message_id': 500, 'date': 1760000000, 'chat': chat,
                    'from': {'id': 123456, 'is_bot': True, 'first_name': 'Бендер'}, 'text': 'Привет'}
            await asyncio.wait_for(dp.feed_update(bot, Update.model_validate({'update_id': counter, 'message': payload})), 5)

        async def callback(*, group=True, user=7):
            nonlocal counter
            counter += 1
            chat = {'id': -101, 'type': 'supergroup'} if group else {'id': 7, 'type': 'private'}
            update = {'update_id': counter, 'callback_query': {
                'id': str(counter), 'chat_instance': 'chat', 'from': {'id': user, 'is_bot': False,
                'first_name': 'Аня'}, 'data': 'rep:bender:50',
                'message': {'message_id': 800, 'date': 1760000000, 'chat': chat,
                    'from': {'id': 123456, 'is_bot': True, 'first_name': 'Бендер'}, 'text': 'Выбор'}}}
            await asyncio.wait_for(dp.feed_update(bot, Update.model_validate(update)), 5)

        get_me = AsyncMock(return_value=User(id=123456, is_bot=True, first_name='Бендер', username='ExpertBot'))
        try:
            with patch.object(auth, 'db', db), patch.object(admin_panel, 'db', db), \
                 patch.object(alc_build, 'db', db), patch.object(logger, 'db', db), patch.object(ex_logic, 'db', db), \
                 patch.object(reports, 'db', db), patch.object(bot, 'get_me', get_me), \
                 patch.object(bot.session, 'make_request', new=AsyncMock(side_effect=telegram)), \
                 patch.object(reports, 'run_expert_report', new_callable=AsyncMock) as run, \
                 patch.object(alc_intro, 'get_intro_session', new=AsyncMock(return_value=None)), \
                 patch.object(alc_base, 'get_base_content', new=AsyncMock(return_value=('Штаб открыт', None))), \
                 patch.object(alc_intro, 'run_new_intro', new_callable=AsyncMock) as intro:
                # The previous reports flag is disabled throughout. Both slash
                # and plain variants go to the gossip engine and keep counts.
                for text, expected in [('/gossip 25', 25), ('сплетни 55', 55),
                        ('/сплетни 60', 60), ('вещай', 50), ('/выбор 70', 70),
                        ('/gossip@ExpertBot 42', 42), ('Экс, сплетни 30', 30)]:
                    with self.subTest(text=text):
                        before = run.await_count
                        await message(text, reply_to_bot=text.startswith('/gossip'))
                        self.assertEqual(run.await_count, before + 1)
                        self.assertEqual(run.await_args.args[2], expected)

                # With multiple personas, selection and callback still work even
                # after the former report flag is disabled.
                await db.personas.insert_one({'p_id': 'other', 'name': 'Другой', 'emoji': '🙂'})
                before = run.await_count
                await message('/gossip')
                self.assertEqual(run.await_count, before)
                self.assertIn('Кто сегодня', requests[-1].text)
                await callback()
                self.assertEqual(run.await_count, before + 1)

                # Callbacks have no AuthMiddleware: revocation must block old buttons.
                await db.auth_chats.update_one({'chat_id': -101}, {'$set': {'status': 'denied'}})
                before = run.await_count
                await callback()
                self.assertEqual(run.await_count, before)
                self.assertIn('не авторизован', requests[-1].text)
                await message('/gossip')
                self.assertEqual(run.await_count, before)
                # Private buttons retain their existing behavior.
                await callback(group=False)
                self.assertEqual(run.await_count, before + 1)
                await db.auth_chats.update_one({'chat_id': -101}, {'$set': {'status': 'approved'}})

                for text in ('/help', '/help@ExpertBot', '/хелп', '/хэлп', '/справка',
                             '/экс помощь', '/экс@ExpertBot помощь', 'Экс, хелп', 'Экс, хэлп', '/start'):
                    with self.subTest(help=text):
                        start = len(requests)
                        await message(text)
                        output = [r.text for r in requests[start:] if isinstance(r, SendMessage)]
                        self.assertTrue(any('Экс: краткая справка' in item and '/соцсправка' in item
                                            for item in output), output)
                start = len(requests)
                await message('/help@OtherBot')
                self.assertFalse(any('Экс: краткая справка' in (getattr(r, 'text', '') or '')
                                     for r in requests[start:]))
                for text in ('/start alcbase', '/start@ExpertBot alcbase'):
                    start = len(requests)
                    await message(text, group=False)
                    self.assertTrue(any(getattr(r, 'text', '') == 'Штаб открыт' for r in requests[start:]))
                await message('/start alcintro', group=False)
                intro.assert_awaited_once()

                # Ex requests still respect the assistant switch, unlike the
                # independent direct gossip commands.
                await db.settings.update_one({'setting': 'feature_panel', 'chat_id': -101},
                    {'$set': {'features.assistant': False}})
                before = run.await_count
                await db.personas.delete_one({'p_id': 'other'})
                await message('Экс, сплетни')
                self.assertEqual(run.await_count, before)
                await message('/gossip 15', reply_to_bot=True)
                self.assertEqual(run.await_count, before + 1)
                for text in ('/help@ExpertBot', '/хелп', '/хэлп', '/экс помощь', '/start'):
                    with self.subTest(help_reply=text):
                        start = len(requests)
                        await message(text, reply_to_bot=True)
                        self.assertTrue(any('Экс: краткая справка' in (getattr(r, 'text', '') or '')
                                            for r in requests[start:]))
        finally:
            await bot.session.close()

    async def test_callback_denies_when_authorization_db_fails(self):
        callback = SimpleNamespace(message=SimpleNamespace(chat=SimpleNamespace(id=-101, type='supergroup')),
            data='rep:bender:50', answer=AsyncMock())
        database = SimpleNamespace(auth_chats=SimpleNamespace(find_one=AsyncMock(side_effect=OSError('db'))))
        with patch.object(reports, 'db', database), patch.object(reports, 'run_expert_report', new_callable=AsyncMock) as run:
            await reports.persona_callback(callback)
        run.assert_not_awaited()
        self.assertIn('не удалось проверить', callback.answer.await_args.args[0].lower())

    def test_daily_report_classification_unchanged(self):
        msg = SimpleNamespace(chat=SimpleNamespace(type='supergroup'), voice=None, video_note=None,
                              text='/set_daily 09:30', photo=None, reply_to_message=None, bot=None)
        self.assertEqual(admin_panel.detect_message_feature(msg), 'daily_reports')
        callback = SimpleNamespace(message=SimpleNamespace(chat=msg.chat), data='c_stop:-101')
        self.assertEqual(admin_panel.detect_callback_feature(callback), 'daily_reports')
