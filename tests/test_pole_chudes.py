"""Поле чудес: буквы, очки, барабан и запись результата в экономику."""
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from mongomock_motor import AsyncMongoMockClient

from handlers import pole_chudes


WORD = 'АСТРОЛЯБИЯ'
CHAT = -1001


def make_game(word=WORD, user_id=7, name='Игрок'):
    return {
        'word': word,
        'guessed': ['_'] * len(word),
        'used_letters': set(),
        'missed_letters': set(),
        'hint': 'Старинный прибор',
        'players': {user_id: {'name': name, 'score': 0}},
        'main_msg_id': 55,
        'current_turn': user_id,
        'current_value': 100,
        'last_player_id': None,
        'last_move_time': 0,
        'timeout_task': None,
        'last_comment': '',
        'last_text': '',
    }


def callback(data, user_id=7, message=None):
    msg = message or type('Message', (), {
        'chat': type('Chat', (), {'id': CHAT})(),
        'edit_text': AsyncMock(),
        'bot': Mock(),
    })()
    return type('Callback', (), {
        'data': data,
        'from_user': type('User', (), {'id': user_id, 'first_name': 'Игрок'})(),
        'message': msg,
        'answer': AsyncMock(),
    })()


async def guess(game, letter, user_id=7):
    """Сделать ход буквой: в реальной игре игрок сначала крутит барабан."""
    game['current_turn'] = user_id
    callback_ = callback(f'cl_{letter}', user_id=user_id)
    await pole_chudes.handle_letter(callback_)
    return callback_


class HelpersTests(unittest.TestCase):
    def test_letter_score_follows_vowels_and_consonants(self):
        for vowel in 'АЕЁИОУЫЭЮЯ':
            with self.subTest(letter=vowel):
                self.assertEqual(pole_chudes.get_letter_score(vowel), 2)
        for consonant in 'БГЖЗКЛМНПРСТФХЦЧШЩЪЬ':
            with self.subTest(letter=consonant):
                self.assertEqual(pole_chudes.get_letter_score(consonant), 3)

    def test_player_is_added_once(self):
        game = {'players': {}}
        pole_chudes.init_player_if_needed(game, 7, 'Игрок')
        pole_chudes.init_player_if_needed(game, 7, 'Другое имя')
        self.assertEqual(game['players'], {7: {'name': 'Игрок', 'score': 0}})

    def test_keyboard_offers_spin_then_alphabet_without_used_letters(self):
        game = make_game()
        game['current_turn'] = None
        spin = pole_chudes.get_main_kb(game)
        self.assertEqual(spin.inline_keyboard[0][0].text, '🎰 Крутить барабан')

        game['current_turn'] = 7
        game['used_letters'] = {'А', 'О'}
        game['missed_letters'] = {'Б'}
        board = [b.text for row in pole_chudes.get_main_kb(game).inline_keyboard for b in row]
        self.assertNotIn('А', board)
        self.assertNotIn('Б', board)
        self.assertIn('В', board)
        self.assertEqual(board[-1], '💡 Слово целиком')


class UpdateMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_absent_game_is_a_no_op(self):
        bot = Mock()
        await pole_chudes.update_main_message(bot, 999)
        bot.edit_message_text.assert_not_called()

    async def test_text_shows_guessed_letters_and_hides_empty_scoreboard(self):
        game = make_game()
        game['guessed'][0] = 'А'
        game['missed_letters'] = {'Б', 'В'}
        pole_chudes.active_games[CHAT] = game
        self.addCleanup(pole_chudes.active_games.clear)
        bot = Mock(edit_message_text=AsyncMock())
        await pole_chudes.update_main_message(bot, CHAT)
        text = bot.edit_message_text.await_args.kwargs['text']
        self.assertIn('А _ _ _ _ _ _ _ _ _', text)
        self.assertIn('Б, В', text)
        self.assertNotIn('Счёт', text, 'пустая таблица очков не показывается')
        self.assertIn('Ход игрока', text)

    async def test_identical_text_is_not_sent_twice(self):
        game = make_game()
        pole_chudes.active_games[CHAT] = game
        self.addCleanup(pole_chudes.active_games.clear)
        bot = Mock(edit_message_text=AsyncMock())
        await pole_chudes.update_main_message(bot, CHAT)
        await pole_chudes.update_main_message(bot, CHAT)
        self.assertEqual(bot.edit_message_text.await_count, 1,
                         'тот же текст не должен тратить лимит Telegram')

    async def test_telegram_rejection_does_not_break_the_turn(self):
        game = make_game()
        pole_chudes.active_games[CHAT] = game
        self.addCleanup(pole_chudes.active_games.clear)
        bot = Mock(edit_message_text=AsyncMock(side_effect=RuntimeError('message not modified')))
        await pole_chudes.update_main_message(bot, CHAT)  # не должно бросить


class LetterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        pole_chudes.active_games.clear()
        self.addCleanup(pole_chudes.active_games.clear)
        self.db = AsyncMongoMockClient().test
        patch.object(pole_chudes, 'db', self.db).start()
        self.addCleanup(patch.stopall)
        patch.object(pole_chudes, 'spawn', lambda coro, **kw: coro.close()).start()
        patch.object(pole_chudes, 'get_comment', AsyncMock(return_value='Так держать')).start()

    async def test_guessed_letter_scores_per_occurrence(self):
        game = make_game()  # две «Я» в слове
        pole_chudes.active_games[CHAT] = game
        timer = Mock()
        game['timeout_task'] = timer
        cb = callback('cl_Я')
        await pole_chudes.handle_letter(cb)
        self.assertEqual(game['players'][7]['score'], 200, '100 очков × 2 вхождения')
        self.assertEqual(game['guessed'], ['_', '_', '_', '_', '_', '_', 'Я', '_', '_', 'Я'])
        timer.cancel.assert_called_once()
        self.assertIsNone(game['timeout_task'], 'таймер снимается, чтобы не сработал дважды')

    async def test_missed_letter_is_marked_and_turn_released(self):
        game = make_game()
        pole_chudes.active_games[CHAT] = game
        cb = callback('cl_Ж')
        await pole_chudes.handle_letter(cb)
        self.assertIn('Ж', game['missed_letters'])
        self.assertIsNone(game['current_turn'])
        self.assertEqual(game['players'][7]['score'], 0)

    async def test_click_without_the_turn_is_refused(self):
        game = make_game()
        game['current_turn'] = 8
        pole_chudes.active_games[CHAT] = game
        cb = callback('cl_А')
        await pole_chudes.handle_letter(cb)
        self.assertIn('не твой ход', cb.answer.await_args.args[0])
        self.assertEqual(game['guessed'], ['_'] * len(WORD))

    async def test_click_after_the_game_is_refused(self):
        cb = callback('cl_А')
        await pole_chudes.handle_letter(cb)
        self.assertIn('Игра окончена', cb.answer.await_args.args[0])

    async def test_solving_the_word_writes_exactly_one_economy_row_and_ends_the_game(self):
        game = make_game(word='ПРИЗ')  # короткое слово удобнее открыть целиком
        game['current_value'] = 50
        pole_chudes.active_games[CHAT] = game
        for letter in 'ПРИ':
            await guess(game, letter)
        cb = await guess(game, 'З')
        rows = await self.db.economy.find({'game_type': 'pole'}).to_list(length=10)
        self.assertEqual(len(rows), 1)
        # 4 буквы × 50 очков сектора; каждая встречается в слове один раз.
        self.assertEqual((rows[0]['chat_id'], rows[0]['user_id'], rows[0]['score']), (CHAT, 7, 200))
        self.assertNotIn(CHAT, pole_chudes.active_games)
        self.assertIn('ПОБЕДА', cb.message.edit_text.await_args.args[0])

    async def test_solved_word_edits_the_board_with_the_reward_text(self):
        game = make_game(word='ПРИЗ')
        pole_chudes.active_games[CHAT] = game
        for letter in 'ПРИ':
            await guess(game, letter)
        cb = await guess(game, 'З')
        text = cb.message.edit_text.await_args.args[0]
        self.assertIn('ПОБЕДА', text)
        self.assertIn('ПРИЗ', text)
        self.assertIn('Игрок', text)


class SpinTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        pole_chudes.active_games.clear()
        self.addCleanup(pole_chudes.active_games.clear)
        patch.object(pole_chudes, 'spawn', lambda coro, **kw: coro.close()).start()
        patch.object(pole_chudes, 'update_main_message', AsyncMock()).start()
        self.addCleanup(patch.stopall)

    async def test_busy_drum_is_refused(self):
        game = make_game()
        game['current_turn'] = 8
        pole_chudes.active_games[CHAT] = game
        cb = callback('spin_drum')
        await pole_chudes.handle_spin(cb)
        self.assertIn('Барабан уже занят', cb.answer.await_args.args[0])

    async def test_same_player_must_wait_between_spins(self):
        game = make_game()
        game['current_turn'] = None
        game['last_player_id'] = 7
        game['last_move_time'] = pole_chudes.time.time()
        pole_chudes.active_games[CHAT] = game
        cb = callback('spin_drum')
        await pole_chudes.handle_spin(cb)
        self.assertIn('Дай шанс другим', cb.answer.await_args.args[0])

    async def test_spin_assigns_turn_value_and_timer(self):
        game = make_game()
        game['current_turn'] = None
        pole_chudes.active_games[CHAT] = game
        cb = callback('spin_drum')
        with patch.object(pole_chudes.random, 'choice', return_value=250):
            await pole_chudes.handle_spin(cb)
        self.assertEqual(game['current_turn'], 7)
        self.assertEqual(game['current_value'], 250)
        self.assertIsNotNone(game['timeout_task'])
        game['timeout_task'].cancel()

    async def test_plus_sector_opens_a_random_letter_and_frees_the_drum(self):
        game = make_game()
        game['current_turn'] = None
        pole_chudes.active_games[CHAT] = game
        cb = callback('spin_drum')
        # Первый choice — сектор, второй — какая именно буква откроется.
        with patch.object(pole_chudes.random, 'choice', side_effect=['ПЛЮС', 'Я']):
            await pole_chudes.handle_spin(cb)
        opened = [l for l in game['guessed'] if l != '_']
        self.assertEqual(len(opened), 2, 'обе буквы «Я» открылись сразу')
        self.assertEqual(set(opened), {'Я'})
        self.assertIsNone(game['current_turn'])
        self.assertIsNone(game['timeout_task'])


if __name__ == '__main__':
    unittest.main()
