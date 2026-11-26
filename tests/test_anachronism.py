"""«Анахронизм»: списание, выплата и защита от двойного начисления.

Модуль был без тестов. Здесь закреплено то, что раньше держалось на честном
слове: ставка не списывается, если вопрос не выдан; ставка и комиссия — одна
строка в экономике; победа платит ровно один раз, даже если игрок дважды
нажал кнопку.
"""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from mongomock_motor import AsyncMongoMockClient

from handlers import anachronism


def question(options=None):
    return {
        'situation': 'В 1518 году в Страсбурге случилась пляска.',
        'options': options or [
            {'text': 'Пляска святого Витта', 'is_correct': True, 'comment': 'Так и было.'},
            {'text': 'Парад зонтов', 'is_correct': False, 'comment': 'Выдумка.'},
        ],
    }


class AnachronismTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        self.database = type('DB', (), {
            'db': self.db, 'economy': self.db.economy, 'game_data': self.db.game_data})()
        patch.object(anachronism, 'db', self.database).start()
        self.addCleanup(patch.stopall)
        anachronism.active_anachronism_games.clear()
        self.addCleanup(anachronism.active_anachronism_games.clear)

    async def give_points(self, amount=10_000, chat_id=-1001, user_id=7):
        await self.db.economy.insert_one({'chat_id': chat_id, 'user_id': user_id,
                                          'user_name': 'Тестер', 'score': amount})

    def message(self, text='/анахронизм 1000', user_id=7):
        status = AsyncMock()
        status.message_id = 900
        game = AsyncMock()
        game.message_id = 901
        answer = AsyncMock(side_effect=[status, game])
        return type('Message', (), {
            'text': text,
            'from_user': type('User', (), {'id': user_id, 'first_name': 'Тестер'})(),
            'chat': type('Chat', (), {'id': -1001})(),
            'answer': answer,
        })(), status, game

    def callback(self, msg_id, data='anach:0', user_id=7):
        message = AsyncMock()
        message.message_id = msg_id
        message.text = 'вопрос'
        message.chat.id = -1001
        return type('Callback', (), {
            'data': data, 'message': message,
            'from_user': type('User', (), {'id': user_id, 'first_name': 'Тестер'})(),
            'answer': AsyncMock(),
        })()

    async def test_bet_and_fee_are_one_economy_row(self):
        await self.give_points()
        await self.db.anachronism_cache.insert_one(question())
        message, status, game = self.message()
        await anachronism.start_anachronism(message)
        rows = await self.db.economy.find({'game_type': 'anachronism', 'user_id': 7}).to_list(10)
        self.assertEqual([row['score'] for row in rows], [-1000], 'первый вход бесплатный')
        self.assertIn('АРХИВ', game.answer.await_args.args[0] if game.answer.await_args else 'АРХИВ')
        stats = await self.db.game_data.find_one({'type': 'anach_stats'})
        self.assertEqual(stats['attempts'], 1)
        self.assertIn(game.message_id, anachronism.active_anachronism_games)

    async def test_fee_grows_exponentially_for_later_attempts_today(self):
        await self.give_points(100_000)
        today = datetime.now(timezone(timedelta(hours=3))).strftime('%Y-%m-%d')
        await self.db.game_data.insert_one({'user_id': 7, 'chat_id': -1001,
                                            'type': 'anach_stats', 'date': today, 'attempts': 3})
        await self.db.anachronism_cache.insert_one(question())
        message, _, _ = self.message()
        await anachronism.start_anachronism(message)
        row = await self.db.economy.find_one({'game_type': 'anachronism', 'user_id': 7})
        self.assertEqual(row['score'], -(1000 + 2000), 'третья попытка: комиссия 500·2²')

    async def test_empty_archive_costs_nothing(self):
        await self.give_points()
        message, status, _ = self.message()
        with patch.object(anachronism, 'fetch_anachronism_questions', AsyncMock(return_value=False)):
            await anachronism.start_anachronism(message)
        self.assertEqual(await self.db.economy.count_documents({'game_type': 'anachronism'}), 0,
                         'за сбой генерации игрок не платит')
        self.assertEqual(await self.db.game_data.count_documents({}), 0)
        status.edit_text.assert_awaited()

    async def test_bet_above_balance_is_rejected_before_any_write(self):
        await self.give_points(1500)
        await self.db.anachronism_cache.insert_one(question())
        message, _, _ = self.message('/анахронизм 5000')
        await anachronism.start_anachronism(message)
        self.assertIn('больше баланса', message.answer.await_args.args[0])
        self.assertEqual(await self.db.economy.count_documents({'game_type': 'anachronism'}), 0)
        self.assertEqual(await self.db.anachronism_cache.count_documents({}), 1,
                         'вопрос не должен сгореть на отклонённой ставке')

    async def test_zero_balance_never_reaches_the_archive(self):
        await self.db.anachronism_cache.insert_one(question())
        message, _, _ = self.message()
        await anachronism.start_anachronism(message)
        self.assertIn('ноль на балансе', message.answer.await_args.args[0])
        self.assertEqual(await self.db.anachronism_cache.count_documents({}), 1)

    async def test_win_pays_two_and_a_half_bets_once(self):
        anachronism.active_anachronism_games[901] = {
            'user_id': 7, 'user_name': 'Тестер', 'chat_id': -1001, 'bet': 1000,
            'key': anachronism.game_key(901), 'options': question()['options'], 'status': 'active'}
        callback = self.callback(901, 'anach:0')
        await anachronism.process_anachronism_answer(callback)
        rows = await self.db.economy.find({'game_type': 'anachronism'}).to_list(10)
        self.assertEqual([row['score'] for row in rows], [2500], 'ставка 1000 × 2.5')
        self.assertIn('ИСТИНА', callback.message.edit_text.await_args.args[0])
        self.assertNotIn(901, anachronism.active_anachronism_games)

    async def test_second_answer_to_the_same_game_is_refused(self):
        anachronism.active_anachronism_games[901] = {
            'user_id': 7, 'user_name': 'Тестер', 'chat_id': -1001, 'bet': 1000,
            'key': anachronism.game_key(901), 'options': question()['options'], 'status': 'active'}
        await anachronism.process_anachronism_answer(self.callback(901))
        second = self.callback(901)
        await anachronism.process_anachronism_answer(second)
        self.assertTrue(second.answer.await_args.kwargs['show_alert'])
        self.assertEqual(await self.db.economy.count_documents({}), 1)

    async def test_pay_once_never_double_credits_a_reused_key(self):
        first = await anachronism.pay_once('anachronism:1', chat_id=-1001, user_id=7,
                                           user_name='Тестер', score=500)
        second = await anachronism.pay_once('anachronism:1', chat_id=-1001, user_id=7,
                                            user_name='Тестер', score=500)
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(await self.db.economy.count_documents({}), 1)

    async def test_failed_economy_write_releases_the_idempotency_mark(self):
        database = anachronism.db
        broken = type('Economy', (), {
            'insert_one': AsyncMock(side_effect=RuntimeError('нет связи'))})()
        with patch.object(anachronism, 'db', type('DB', (), {
                'db': database.db, 'economy': broken, 'game_data': database.game_data})()):
            with self.assertRaises(RuntimeError):
                await anachronism.pay_once('anachronism:2', chat_id=-1001, user_id=7,
                                           user_name='Тестер', score=500)
        self.assertEqual(await self.db.anachronism_payments.count_documents({}), 0,
                         'иначе повтор после сбоя не смог бы начислить очки')

    async def test_loss_pays_nothing_and_reveals_the_true_option(self):
        anachronism.active_anachronism_games[901] = {
            'user_id': 7, 'user_name': 'Тестер', 'chat_id': -1001, 'bet': 1000,
            'key': anachronism.game_key(901), 'options': question()['options'], 'status': 'active'}
        callback = self.callback(901, 'anach:1')
        await anachronism.process_anachronism_answer(callback)
        self.assertEqual(await self.db.economy.count_documents({'game_type': 'anachronism'}), 0)
        text = callback.message.edit_text.await_args.args[0]
        self.assertIn('ЛОЖЬ', text)
        self.assertIn('Так и было.', text)

    async def test_another_player_cannot_answer_someone_elses_game(self):
        anachronism.active_anachronism_games[901] = {
            'user_id': 7, 'user_name': 'Тестер', 'chat_id': -1001, 'bet': 1000,
            'key': anachronism.game_key(901), 'options': question()['options'], 'status': 'active'}
        stranger = self.callback(901, user_id=8)
        await anachronism.process_anachronism_answer(stranger)
        self.assertTrue(stranger.answer.await_args.kwargs['show_alert'])
        self.assertEqual(await self.db.economy.count_documents({}), 0)
        self.assertIn(901, anachronism.active_anachronism_games)

    async def test_cleanup_drops_only_the_expired_session(self):
        anachronism.active_anachronism_games[1] = {'status': 'active'}
        anachronism.active_anachronism_games[2] = {'status': 'active'}
        with patch.object(anachronism.asyncio, 'sleep', AsyncMock()):
            await anachronism.cleanup_game_session(1)
        self.assertEqual(list(anachronism.active_anachronism_games), [2])

    async def test_llm_question_batch_is_stored_and_bad_json_is_ignored(self):
        payload = '[{"situation": "s", "options": [{"text": "t", "is_correct": true}]}]'
        with patch.object(anachronism, 'generate_with_retry', AsyncMock(return_value=payload)):
            self.assertTrue(await anachronism.fetch_anachronism_questions())
        self.assertEqual(await self.db.anachronism_cache.count_documents({}), 1)

        with patch.object(anachronism, 'generate_with_retry', AsyncMock(return_value='не JSON')):
            self.assertFalse(await anachronism.fetch_anachronism_questions())
        self.assertEqual(await self.db.anachronism_cache.count_documents({}), 1)

        with patch.object(anachronism, 'generate_with_retry', AsyncMock(return_value=None)):
            self.assertFalse(await anachronism.fetch_anachronism_questions())


if __name__ == '__main__':
    unittest.main()
