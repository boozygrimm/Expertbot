"""Виселица: таймаут хода, начисление очков и остановка партии.

Модуль раньше не был покрыт тестами. Первая же проверка вскрыла баг:
`hm_timeout_task` обращался к `message` и `status_msg`, которых в её
области видимости нет, поэтому КАЖДЫЙ таймаут хода падал с NameError,
а задача создавалась через asyncio.create_task и никем не ожидалась —
исключение молча уходило в лог.
"""
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from mongomock_motor import AsyncMongoMockClient

from handlers import viselitsa


class FakeState:
    """Минимальное состояние партии, как его строит start_hangman."""

    def __init__(self, word='СКЕЛЕТ', **overrides):
        self.data = {
            'word': word,
            'guessed': ['_'] * len(word),
            'used_letters': set(),
            'missed_letters': set(),
            'errors': 0,
            'hint': 'То, что остаётся.',
            'players': {},
            'main_msg_id': 77,
            'current_turn': None,
            'last_move_time': 0,
            'timeout_task': None,
            'last_text': '',
        }
        self.data.update(overrides)


class HangmanTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        self.dbpatch = patch.object(viselitsa, 'db', self.db)
        self.dbpatch.start()
        self.addCleanup(self.dbpatch.stop)
        viselitsa.active_hangman_games.clear()
        self.addCleanup(viselitsa.active_hangman_games.clear)
        self.bot = AsyncMock()
        self.bot.edit_message_text = AsyncMock()

    def callback(self, data, user_id=7, first_name='Тестер'):
        message = AsyncMock()
        message.chat.id = -1001
        message.message_id = 55
        message.bot = self.bot
        return type('Callback', (), {
            'data': data,
            'from_user': type('User', (), {'id': user_id, 'first_name': first_name})(),
            'message': message,
            'answer': AsyncMock(),
        })()

    async def test_timeout_task_survives_without_a_message_object(self):
        """Регресс: таймаут падал с NameError на строках с `message`/`status_msg`."""
        game = FakeState(word='СКЕЛЕТ', current_turn=7)
        game.data['players'] = {7: {'name': 'Тестер', 'score': 0}}
        viselitsa.active_hangman_games[-1001] = game.data
        with patch.object(viselitsa.asyncio, 'sleep', AsyncMock()):
            # Падение раньше было видно только в логе задачи: пробрасываем его.
            await viselitsa.hm_timeout_task(-1001, 7, self.bot)
        self.assertIsNone(game.data['current_turn'])
        self.bot.send_message.assert_awaited_once()
        self.assertIn('Время вышло', self.bot.send_message.await_args.args[1])

    async def test_timeout_of_a_player_who_already_passed_does_nothing(self):
        game = FakeState(current_turn=99)
        game.data['players'] = {7: {'name': 'Тестер', 'score': 0}}
        viselitsa.active_hangman_games[-1001] = game.data
        with patch.object(viselitsa.asyncio, 'sleep', AsyncMock()):
            await viselitsa.hm_timeout_task(-1001, 7, self.bot)
        self.assertEqual(game.data['current_turn'], 99)
        self.bot.send_message.assert_not_awaited()

    async def test_letter_off_turn_is_rejected_without_changing_state(self):
        game = FakeState(current_turn=7)
        game.data['players'] = {7: {'name': 'Тестер', 'score': 0}}
        viselitsa.active_hangman_games[-1001] = game.data
        callback = self.callback('hm_cl_О', user_id=8)
        await viselitsa.hm_handle_letter(callback)
        self.assertTrue(callback.answer.await_args.kwargs['show_alert'])
        self.assertEqual(game.data['errors'], 0)
        self.assertEqual(game.data['guessed'], ['_'] * 6)

    async def test_correct_letter_opens_all_occurrences_and_scores_hundreds(self):
        game = FakeState(word='БАРАБАН', current_turn=7)
        game.data['players'] = {7: {'name': 'Тестер', 'score': 0}}
        viselitsa.active_hangman_games[-1001] = game.data
        callback = self.callback('hm_cl_А')
        with patch.object(viselitsa, 'update_hangman_message', AsyncMock()):
            await viselitsa.hm_handle_letter(callback)
        self.assertEqual(game.data['guessed'], ['_', 'А', '_', 'А', '_', 'А', '_'])
        self.assertEqual(game.data['players'][7]['score'], 300)
        self.assertEqual(game.data['current_turn'], 7, 'угадавший сохраняет ход')

    async def test_win_pays_the_accumulated_score_into_the_economy(self):
        game = FakeState(word='К', current_turn=7)
        game.data['players'] = {7: {'name': 'Тестер', 'score': 0}}
        viselitsa.active_hangman_games[-1001] = game.data
        callback = self.callback('hm_cl_К')
        with patch.object(viselitsa, 'update_hangman_message', AsyncMock()), \
             patch.object(viselitsa, 'unpin_and_delete_after_delay', AsyncMock()):
            await viselitsa.hm_handle_letter(callback)
        self.assertNotIn(-1001, viselitsa.active_hangman_games)
        rows = await self.db.economy.find({'game_type': 'hangman'}).to_list(length=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['chat_id'], rows[0]['user_id'], rows[0]['score']), (-1001, 7, 600),
                         '100 за букву + 500 за спасение')

    async def test_sixth_miss_ends_the_game_without_paying_out(self):
        game = FakeState(word='СКЕЛЕТ', current_turn=7, errors=5)
        game.data['players'] = {7: {'name': 'Тестер', 'score': 400}}
        viselitsa.active_hangman_games[-1001] = game.data
        callback = self.callback('hm_cl_Я')
        with patch.object(viselitsa, 'unpin_and_delete_after_delay', AsyncMock()):
            await viselitsa.hm_handle_letter(callback)
        self.assertNotIn(-1001, viselitsa.active_hangman_games)
        self.assertEqual(await self.db.economy.count_documents({}), 0)
        self.assertIn('ПРИГОВОР', callback.message.edit_text.await_args.args[0])

    async def test_stop_command_clears_the_game_and_cancels_the_timer(self):
        task = Mock()  # cancel() синхронный, как у настоящего asyncio.Task
        game = FakeState(current_turn=7, timeout_task=task)
        viselitsa.active_hangman_games[-1001] = game.data
        message = type('Message', (), {
            'chat': type('Chat', (), {'id': -1001})(),
            'from_user': type('User', (), {'id': 7, 'first_name': 'Тестер'})(),
            'text': '/stop', 'bot': self.bot, 'answer': AsyncMock()})()
        with patch.object(viselitsa, 'unpin_and_delete_after_delay', AsyncMock()):
            await viselitsa.hm_stop_game(message)
        task.cancel.assert_called_once()
        self.assertEqual(viselitsa.active_hangman_games, {})

    async def test_word_from_llm_outside_seven_to_fifteen_letters_falls_back(self):
        with patch.object(viselitsa, 'generate_with_retry', AsyncMock(return_value='КОТ|Маленький')):
            word, hint = await viselitsa.get_hangman_word('нет')
        self.assertEqual(word, 'ЭКЗЕКУЦИЯ')
        self.assertIn('Высшая мера', hint)

    async def test_word_from_llm_is_stripped_to_russian_letters(self):
        with patch.object(viselitsa, 'generate_with_retry', AsyncMock(
                return_value='  паучина!!! |То, что остаётся.  ')):
            word, hint = await viselitsa.get_hangman_word('нет')
        self.assertEqual((word, hint), ('ПАУЧИНА', 'То, что остаётся.'))

    async def test_six_letter_word_falls_back_below_the_documented_minimum(self):
        with patch.object(viselitsa, 'generate_with_retry', AsyncMock(return_value='СКЕЛЕТ|Кости')):
            word, _ = await viselitsa.get_hangman_word('нет')
        self.assertEqual(word, 'ЭКЗЕКУЦИЯ')


if __name__ == '__main__':
    unittest.main()
