"""Тесты классического Рейда: фильтр ошибок и разбор запросов."""
import os
import unittest
from types import SimpleNamespace

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from aiogram.exceptions import TelegramBadRequest

from handlers.owner_video_sender import _telegram_symbol
from handlers.telegram_error_codes import TELEGRAM_ERROR_CODES


def bad_request(text):
    return SimpleNamespace(message=text)


class TelegramSymbolTests(unittest.TestCase):
    def test_known_public_name_is_kept_verbatim(self):
        self.assertEqual(_telegram_symbol(bad_request(
            'Bad Request: VOICE_MESSAGES_FORBIDDEN')), 'VOICE_MESSAGES_FORBIDDEN')

    def test_prefix_is_case_insensitive(self):
        self.assertEqual(_telegram_symbol(bad_request(
            'BAD REQUEST: USER_IS_BLOCKED')), 'USER_IS_BLOCKED')

    def test_unknown_name_is_hashed_instead_of_logged(self):
        """В лог не должно попасть то, чего нет в публичном списке."""
        symbol = _telegram_symbol(bad_request('Bad Request: SUPER_SECRET_INTERNAL_NAME'))
        self.assertTrue(symbol.startswith('sha256-'))
        self.assertNotIn('SUPER_SECRET', symbol)
        self.assertEqual(len(symbol), len('sha256-') + 24)

    def test_same_unknown_name_gives_the_same_hash(self):
        a = _telegram_symbol(bad_request('Bad Request: MYSTERY_CODE'))
        b = _telegram_symbol(bad_request('Bad Request: MYSTERY_CODE'))
        self.assertEqual(a, b, 'хеш нужен, чтобы сопоставлять повторы')

    def test_free_text_never_becomes_a_symbol(self):
        """Текст ошибки может содержать file_id, URL или название чата."""
        for text in ('Bad Request: chat not found',
                     'Bad Request: file is too big https://example.test/x',
                     'Bad Request:',
                     'Bad Request: A',
                     'Bad Request: lowercase_code'):
            with self.subTest(text=text):
                self.assertEqual(_telegram_symbol(bad_request(text)), 'unavailable')

    def test_length_bounds_of_the_symbol_pattern(self):
        # Регулярка допускает 3–80 символов: короче — это не код, длиннее —
        # уже не имя ошибки, а вырожденный текст.
        self.assertEqual(_telegram_symbol(bad_request('Bad Request: AB')), 'unavailable')
        self.assertEqual(_telegram_symbol(bad_request('Bad Request: ABC')), 'sha256-' + __import__('hashlib').sha256(b'ABC').hexdigest()[:24])
        self.assertTrue(_telegram_symbol(bad_request('Bad Request: ' + 'X' * 80)).startswith('sha256-'))
        self.assertEqual(_telegram_symbol(bad_request('Bad Request: ' + 'X' * 81)), 'unavailable')

    def test_error_without_message_attribute_does_not_crash(self):
        self.assertEqual(_telegram_symbol(SimpleNamespace(message=None)), 'unavailable')

    def test_real_aiogram_exception_is_handled(self):
        exc = TelegramBadRequest(method=SimpleNamespace(), message='Bad Request: BUTTON_DATA_INVALID')
        self.assertEqual(_telegram_symbol(exc), 'BUTTON_DATA_INVALID')


class ErrorListTests(unittest.TestCase):
    def test_list_is_a_frozen_set_of_upper_snake_case_names(self):
        self.assertTrue(TELEGRAM_ERROR_CODES)
        for name in TELEGRAM_ERROR_CODES:
            with self.subTest(name=name):
                self.assertRegex(name, r'^[A-Z][A-Z0-9_]+$')

    def test_codes_used_by_the_bot_are_listed(self):
        for code in ('VOICE_MESSAGES_FORBIDDEN', 'USER_IS_BLOCKED', 'BUTTON_DATA_INVALID',
                     'CHAT_WRITE_FORBIDDEN', 'MESSAGE_TOO_LONG'):
            with self.subTest(code=code):
                self.assertIn(code, TELEGRAM_ERROR_CODES)


if __name__ == '__main__':
    unittest.main()
