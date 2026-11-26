"""Рейтинг чата и `/промокод`: доступ по ADMIN_ID, начисление через reply и по имени."""
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from mongomock_motor import AsyncMongoMockClient

from config import ADMIN_ID
from handlers import game_stats


def message(text, user_id=ADMIN_ID, reply_to=None):
    return type('Message', (), {
        'text': text,
        'from_user': type('User', (), {'id': user_id, 'first_name': 'Владелец'})(),
        'chat': type('Chat', (), {'id': -1001})(),
        'reply_to_message': reply_to,
        'answer': AsyncMock(),
        'delete': AsyncMock(),
    })()


class GameStatsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        patch.object(game_stats, 'db', self.db).start()
        self.addCleanup(patch.stopall)
        # `spawn` внутри модуля подменён, чтобы таймеры удаления не висели.
        patch.object(game_stats, 'spawn', lambda coro, **kw: coro.close()).start()

    async def test_promo_is_closed_to_everyone_but_admin_id(self):
        stranger = message('/промокод 100', user_id=ADMIN_ID + 1)
        await game_stats.admin_promo_code(stranger)
        self.assertIn('только Создателю', stranger.answer.await_args.args[0])
        self.assertEqual(await self.db.economy.count_documents({}), 0)

    async def test_admin_id_comes_from_config_not_a_hardcoded_number(self):
        self.assertIsInstance(ADMIN_ID, int)
        with patch.object(game_stats, 'ADMIN_ID', 42):
            stranger = message('/промокод 100', user_id=ADMIN_ID)
            await game_stats.admin_promo_code(stranger)
            self.assertIn('только Создателю', stranger.answer.await_args.args[0],
                          'смена ADMIN_ID должна менять и доступ')

    async def test_amount_is_required(self):
        msg = message('/промокод')
        await game_stats.admin_promo_code(msg)
        self.assertIn('Укажите сумму', msg.answer.await_args.args[0])

    async def test_reply_credits_the_replied_player_in_this_chat(self):
        target = type('Reply', (), {
            'from_user': type('User', (), {'id': 42, 'first_name': 'Игрок'})()})()
        msg = message('/промокод 500', reply_to=target)
        await game_stats.admin_promo_code(msg)
        row = await self.db.economy.find_one({'game_type': 'promo'})
        self.assertEqual((row['chat_id'], row['user_id'], row['score'], row['user_name']),
                         (-1001, 42, 500, 'Игрок'))

    async def test_negative_amount_writes_a_charge(self):
        target = type('Reply', (), {
            'from_user': type('User', (), {'id': 42, 'first_name': 'Игрок'})()})()
        await game_stats.admin_promo_code(message('/промокод -250', reply_to=target))
        row = await self.db.economy.find_one({'game_type': 'promo'})
        self.assertEqual(row['score'], -250)

    async def test_unknown_name_is_reported_and_nothing_is_written(self):
        msg = message('/промокод 100 Призрак')
        await game_stats.admin_promo_code(msg)
        self.assertIn('не найден', msg.answer.await_args.args[0])
        self.assertEqual(await self.db.economy.count_documents({}), 0)

    async def test_name_lookup_is_case_insensitive_and_anchored(self):
        await self.db.economy.insert_one({'chat_id': -1001, 'user_id': 7,
                                          'user_name': 'Бендер', 'score': 1})
        await self.db.economy.insert_one({'chat_id': -1001, 'user_id': 8,
                                          'user_name': 'Бендерович', 'score': 1})
        await game_stats.admin_promo_code(message('/промокод 300 бендер'))
        row = await self.db.economy.find_one({'game_type': 'promo'})
        self.assertEqual(row['user_id'], 7, 'похожее имя не должно перехватывать начисление')

    async def test_without_reply_or_name_admin_is_told_what_to_do(self):
        msg = message('/промокод 100')
        await game_stats.admin_promo_code(msg)
        self.assertIn('реплай', msg.answer.await_args.args[0])

    async def test_rating_aggregates_only_the_current_chat(self):
        await self.db.economy.insert_many([
            {'chat_id': -1001, 'user_id': 1, 'user_name': 'А', 'score': 100},
            {'chat_id': -1001, 'user_id': 1, 'user_name': 'А', 'score': 50},
            {'chat_id': -1001, 'user_id': 2, 'user_name': 'Б', 'score': 10},
            {'chat_id': -2002, 'user_id': 3, 'user_name': 'В', 'score': 999},
        ])
        msg = message('/очки')
        await game_stats.show_global_top(msg)
        text = msg.answer.return_value.edit_text.await_args.args[0]
        self.assertIn('А', text)
        self.assertIn('150', text)
        self.assertNotIn('999', text, 'чужой чат не должен попадать в рейтинг')

    async def test_empty_rating_says_so_and_never_shows_a_table(self):
        msg = message('/очки')
        await game_stats.show_global_top(msg)
        text = msg.answer.return_value.edit_text.await_args.args[0]
        self.assertIn('пусто', text)
        self.assertNotIn('РЕЙТИНГ', text)

    async def test_a_broken_economy_query_is_reported_to_the_chat_user(self):
        msg = message('/очки')
        broken = type('Collection', (), {
            'aggregate': lambda *a, **k: (_ for _ in ()).throw(RuntimeError('нет связи'))})()
        with patch.object(game_stats, 'db', type('DB', (), {'economy': broken})()):
            await game_stats.show_global_top(msg)
        self.assertIn('Ошибка', msg.answer.return_value.edit_text.await_args.args[0])


if __name__ == '__main__':
    unittest.main()
