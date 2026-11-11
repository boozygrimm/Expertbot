"""
Тесты для модуля social_rp.py

Покрывают критические функции для сопровождения проекта:
- Валидация ников (безопасность)
- Rate limiting (защита от спама)
- Склонение глаголов (корректность работы)
- Склонение ников по падежам (safe_decline_name)
- Локальное RP-сообщение целиком (generate_local_fallback)
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import sys
import os

# Добавляем корневую директорию в путь для импорта
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Минимальное окружение для импорта реальных модулей (БД и сеть не используются).
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("TELEGRAM_TOKEN", "123456:TESTTOKEN")

# ВАЖНО: раньше здесь подменялись sys.modules['ai_logic'], sys.modules['config']
# и другие на MagicMock. При запуске всего набора (unittest discover / pytest)
# это ломало соседние тесты: unittest.mock.patch("ai_logic._try_groq") патчил
# заглушку, а проверяемая обёртка жила в настоящем модуле, поэтому утверждения
# падали с "Awaited 0 times". Импортируем реальные модули — все зависимости
# объявлены в requirements.txt, а сами тесты мокают только атрибуты
# handlers.social_rp.

# Теперь можно безопасно импортировать тестируемые функции
from handlers.social_rp import (
    validate_nickname,
    is_on_cooldown,
    set_cooldown,
    get_verb_form,
    generate_local_fallback,
    safe_decline_name,
    CASE_GRAMMEMES,
    HAS_MORPH,
    rp_rate_limit,
    RP_COOLDOWN
)


class TestValidateNickname:
    """Тесты валидации никнеймов."""

    def test_valid_nickname(self):
        """Корректный никнейм."""
        is_valid, error = validate_nickname("TestUser")
        assert is_valid is True
        assert error == ""

    def test_empty_nickname(self):
        """Пустой никнейм."""
        is_valid, error = validate_nickname("")
        assert is_valid is False
        assert "пустым" in error.lower()

    def test_whitespace_only_nickname(self):
        """Никнейм только из пробелов."""
        is_valid, error = validate_nickname("   ")
        assert is_valid is False
        assert "пустым" in error.lower()

    def test_too_long_nickname(self):
        """Слишком длинный никнейм (>30 символов)."""
        is_valid, error = validate_nickname("a" * 31)
        assert is_valid is False
        assert "30" in error

    def test_too_short_nickname(self):
        """Слишком короткий никнейм (<2 символов)."""
        is_valid, error = validate_nickname("a")
        assert is_valid is False
        assert "минимум 2" in error

    def test_nickname_with_newline(self):
        """Никнейм с переносом строки."""
        is_valid, error = validate_nickname("Test\nUser")
        assert is_valid is False
        assert "переносы" in error.lower()

    def test_nickname_with_tab(self):
        """Никнейм с табуляцией."""
        is_valid, error = validate_nickname("Test\tUser")
        assert is_valid is False
        assert "переносы" in error.lower()

    def test_nickname_with_carriage_return(self):
        """Никнейм с carriage return."""
        is_valid, error = validate_nickname("Test\rUser")
        assert is_valid is False
        assert "переносы" in error.lower()

    def test_nickname_exactly_30_chars(self):
        """Никнейм ровно 30 символов - должен проходить."""
        is_valid, error = validate_nickname("a" * 30)
        assert is_valid is True
        assert error == ""

    def test_nickname_exactly_2_chars(self):
        """Никнейм ровно 2 символа - должен проходить."""
        is_valid, error = validate_nickname("ab")
        assert is_valid is True
        assert error == ""

    def test_nickname_with_special_chars(self):
        """Никнейм со спецсимволами - должен проходить."""
        is_valid, error = validate_nickname("Test_User-123")
        assert is_valid is True
        assert error == ""

    def test_nickname_with_emoji(self):
        """Никнейм с эмодзи - должен проходить."""
        is_valid, error = validate_nickname("Test😀")
        assert is_valid is True
        assert error == ""


class TestRateLimiting:
    """Тесты rate limiting."""

    def setup_method(self):
        """Очистка перед каждым тестом."""
        rp_rate_limit.clear()

    def test_no_cooldown_initially(self):
        """Изначально кулдауна нет."""
        assert is_on_cooldown(123, 456) is False

    def test_cooldown_after_command(self):
        """Кулдаун устанавливается после команды."""
        set_cooldown(123, 456)
        assert is_on_cooldown(123, 456) is True

    def test_cooldown_expires(self):
        """Кулдаун истекает через RP_COOLDOWN секунд."""
        set_cooldown(123, 456)

        # Симулируем прохождение времени
        with patch('handlers.social_rp.datetime') as mock_dt:
            mock_dt.now.return_value = datetime.now() + timedelta(seconds=RP_COOLDOWN + 1)
            assert is_on_cooldown(123, 456) is False

    def test_cooldown_not_expired_yet(self):
        """Кулдаун ещё не истёк."""
        set_cooldown(123, 456)

        with patch('handlers.social_rp.datetime') as mock_dt:
            mock_dt.now.return_value = datetime.now() + timedelta(seconds=RP_COOLDOWN - 1)
            assert is_on_cooldown(123, 456) is True

    def test_separate_users(self):
        """Кулдаун работает отдельно для каждого пользователя."""
        set_cooldown(123, 456)
        assert is_on_cooldown(123, 456) is True
        assert is_on_cooldown(789, 456) is False

    def test_separate_chats(self):
        """Кулдаун работает отдельно для каждого чата."""
        set_cooldown(123, 456)
        assert is_on_cooldown(123, 456) is True
        assert is_on_cooldown(123, 789) is False

    def test_multiple_commands_update_cooldown(self):
        """Повторные команды обновляют время кулдауна."""
        set_cooldown(123, 456)

        # Симулируем прохождение 1 секунды
        with patch('handlers.social_rp.datetime') as mock_dt:
            first_time = datetime.now()
            mock_dt.now.return_value = first_time + timedelta(seconds=1)
            set_cooldown(123, 456)

            # Кулдаун должен быть обновлён
            assert is_on_cooldown(123, 456) is True


class TestVerbForm:
    """Тесты склонения глаголов."""

    def test_male_gender_returns_unchanged(self):
        """Мужской род - глагол не меняется."""
        result = get_verb_form("обнял", "мужской")
        assert result == "обнял"

    def test_male_gender_case_insensitive(self):
        """Мужской род - проверка регистра."""
        result = get_verb_form("обнял", "МУЖСКОЙ")
        assert result == "обнял"

    def test_female_gender_with_morph_analyzer(self):
        """Женский род с морфологическим анализатором."""
        import handlers.social_rp as sr

        # Создаём mock для morph, если его нет
        if not hasattr(sr, 'morph'):
            sr.morph = MagicMock()

        with patch('handlers.social_rp.HAS_MORPH', True):
            with patch('handlers.social_rp.morph') as mock_morph:
                # Настраиваем mock для морфологического анализатора
                mock_parse = MagicMock()
                mock_parse.tag = 'VERB'
                mock_inflect = MagicMock()
                mock_inflect.word = "обняла"
                mock_parse.inflect.return_value = mock_inflect
                mock_morph.parse.return_value = [mock_parse]

                result = get_verb_form("обнял", "женский")
                assert result == "обняла"

    def test_female_gender_fallback(self):
        """Женский род - fallback без морфологии."""
        with patch('handlers.social_rp.HAS_MORPH', False):
            result = get_verb_form("обнял", "женский")
            assert result == "обняла"

    def test_female_gender_fallback_all_verbs(self):
        """Проверка всех глаголов в fallback."""
        with patch('handlers.social_rp.HAS_MORPH', False):
            test_cases = [
                ("обнял", "обняла"),
                ("поцеловал", "поцеловала"),
                ("укусил", "укусила"),
                ("погладил", "погладила"),
                ("пнул", "пнула"),
                ("ударил", "ударила"),
                ("похлопал", "похлопала"),
                ("сделал кусь", "сделала кусь"),
                ("чмокнул", "чмокнула"),
                ("засосал", "засосала"),
                ("шлёпнул", "шлёпнула"),
                ("связал", "связала"),
                ("разделил", "разделила"),
                ("наказал", "наказала"),
                ("выпорол", "выпорола"),
                ("шмальнул", "шмальнула"),
            ]

            for male_form, expected_female in test_cases:
                result = get_verb_form(male_form, "женский")
                assert result == expected_female, f"Failed for {male_form}"

    def test_unknown_verb_fallback(self):
        """Неизвестный глагол - возвращает исходную форму."""
        with patch('handlers.social_rp.HAS_MORPH', False):
            result = get_verb_form("неизвестный_глагол", "женский")
            assert result == "неизвестный_глагол"

    def test_morph_error_fallback(self):
        """Ошибка морфологии - используется fallback."""
        import handlers.social_rp as sr

        # Создаём mock для morph, если его нет
        if not hasattr(sr, 'morph'):
            sr.morph = MagicMock()

        with patch('handlers.social_rp.HAS_MORPH', True):
            with patch('handlers.social_rp.morph') as mock_morph:
                mock_morph.parse.side_effect = Exception("Test error")

                result = get_verb_form("обнял", "женский")
                assert result == "обняла"  # Fallback значение


needs_morph = pytest.mark.skipif(not HAS_MORPH, reason="морфологический анализатор недоступен")


class TestDeclineName:
    """Тесты склонения ников (safe_decline_name)."""

    def test_latin_name_returns_unchanged(self):
        """Латиница не склоняется."""
        assert safe_decline_name("John", "ins") == "John"

    def test_unknown_case_returns_unchanged(self):
        """Неизвестный падеж - возвращаем ник как есть."""
        assert safe_decline_name("Иван", "zzz") == "Иван"

    def test_without_morph_returns_unchanged(self):
        """Без морфологии склонение отключено, ник не меняется."""
        with patch('handlers.social_rp.HAS_MORPH', False):
            assert safe_decline_name("Иван", "ins") == "Иван"

    @needs_morph
    def test_instrumental_case_for_rp_message(self):
        """RP-сообщение использует творительный падеж: обнял Натой."""
        assert safe_decline_name("Ната", "ins") == "Натой"
        assert safe_decline_name("Иван", "ins") == "Иваном"

    @needs_morph
    def test_all_cases_are_declined(self):
        """Все падежи из карты реально склоняются."""
        expected = {
            "gent": "Наты",
            "dat": "Нате",
            "acc": "Нату",
            "ins": "Натой",
            "abl": "Нате",
        }
        for case, form in expected.items():
            assert safe_decline_name("Ната", case) == form, f"падеж {case}"

    @needs_morph
    def test_uppercase_nick_keeps_register(self):
        """pymorphy отдаёт форму в lowercase - регистр ника должен сохраняться."""
        assert safe_decline_name("ИВАН", "ins") == "ИВАНОМ"

    @needs_morph
    def test_case_grammemes_are_known_to_morph(self):
        """Регрессия: раньше в карте стояли несуществующие грамеммы ('ins', 'dat',
        'acc', 'abl'), pymorphy бросал ValueError, и склонение молча не работало."""
        import handlers.social_rp as sr

        for grammeme in sorted(set(CASE_GRAMMEMES.values())):
            parsed = sr.morph.parse("Иван")[0]
            assert parsed.inflect({grammeme}) is not None, f"граммама {grammeme} не работает"


class TestLocalRpMessage:
    """Сквозные тесты локального RP-сообщения."""

    @needs_morph
    def test_target_nick_is_declined(self):
        """Цель должна стоять в творительном падеже: обнял Натой, обняла Иваном."""
        assert generate_local_fallback("обнять", "Вася", "Ната", "мужской", "") == "Вася обнял Натой."
        assert generate_local_fallback("обнять", "Маша", "Иван", "женский", "крепко") == "Маша обняла Иваном крепко."

    def test_latin_nicks_are_left_as_is(self):
        """Латинские ники не склоняются, но глагол по полу меняется."""
        assert generate_local_fallback("обнять", "Masha", "John", "женский", "") == "Masha обняла John."

    def test_modifier_is_appended(self):
        assert generate_local_fallback("чмок", "Вася", "John", "мужской", "нежно") == "Вася чмокнул John нежно."


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
