"""Настройки системных уведомлений игрока.

Каждая категория уведомлений — это отдельное булево поле в документе игрока
(коллекция `db.alchemy_players`). Отсутствие поля трактуется как «уведомления
включены», поэтому миграция существующим профилям не нужна: `get_or_create_player`
дописывает недостающие ключи шаблона и сохраняет их точечно через `$set`, так что
произвольные поля не затираются.

Модуль намеренно не импортирует другие хендлеры. Его подключают сразу три
контура — `handlers.alc_base` (главное меню базы), `handlers.alc_tasks`
(утренний отчёт) и `handlers.alc_raids` (отчёты о нападениях на форт), — поэтому
любая зависимость от них создала бы цикл импортов.

Категории:

- Утренний отчёт алхимии (`process_barracks_upkeep`, cron 3:10 МСК): «Служба
  снабжения», «Наследие мира», «Биомы мира».
- Отчёты о нападениях на форт: «АТАКА НА ФОРТ» от другого игрока и «НАЛЁТ НА
  ФОРТ» от случайной NPC-банды. Итоги собственного рейда игрока («ОТЧЁТ О
  РЕЙДЕ») не гейтятся: это отклик на его действие, а не фоновая рассылка.

Флаги гейтят только отправку сообщения. Экономика — списание энергии за бойцов,
начисления наследия и миров, потери при налёте, счётчики — выполняется всегда,
поэтому отключение уведомлений не даёт игроку преимущества и не наказывает его.
"""

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- Утренний отчёт алхимии -------------------------------------------------
MORNING_REPORT_MUTE_FIELD = "morning_report_muted"
REPORT_MUTE_CALLBACK = "alc:report_mute"
REPORT_TOGGLE_CALLBACK = "alc:report_toggle"
MORNING_REPORT_MUTE_TEXT = "🔕 Не присылать отчёты"
MORNING_REPORT_TOGGLE_ON_TEXT = "📊 Отчёты: вкл"
MORNING_REPORT_TOGGLE_OFF_TEXT = "🔕 Отчёты: выкл"
# Утренний отчёт открывается из меню базы, поэтому «Закрыть» ведёт туда же.
MORNING_REPORT_CLOSE_CALLBACK = "alc:base_close"

# --- Отчёты о нападениях на форт -------------------------------------------
RAID_REPORT_MUTE_FIELD = "raid_report_muted"
RAID_REPORT_MUTE_CALLBACK = "alc:raid_report_mute"
RAID_REPORT_TOGGLE_CALLBACK = "alc:raid_report_toggle"
RAID_REPORT_MUTE_TEXT = "🔕 Не присылать отчёты о нападениях"
RAID_REPORT_TOGGLE_ON_TEXT = "⚔️ Нападения: вкл"
RAID_REPORT_TOGGLE_OFF_TEXT = "🔕 Нападения: выкл"
# DM-отчёты рейдов живут в своём окне, у них собственный callback закрытия.
RAID_REPORT_CLOSE_CALLBACK = "close_pm_report"

CLOSE_BUTTON_TEXT = "❌ Закрыть"


def _is_muted(player: dict | None, field: str) -> bool:
    """Общий доступ к флагу: нет поля или False — уведомления включены."""
    return bool((player or {}).get(field, False))


def _build_close_markup(close_callback: str) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=CLOSE_BUTTON_TEXT, callback_data=close_callback)
    return builder.as_markup()


def _build_mute_markup(
    muted: bool,
    mute_text: str,
    mute_callback: str,
    close_callback: str,
) -> types.InlineKeyboardMarkup:
    """Клавиатура отчёта: кнопка отключения рассылки над кнопкой «Закрыть».

    Если уведомления уже отключены, кнопка отключения не показывается — иначе
    игрок может нажать её повторно и получить лишний алерт.
    """
    if muted:
        return _build_close_markup(close_callback)

    builder = InlineKeyboardBuilder()
    builder.button(text=mute_text, callback_data=mute_callback)
    builder.button(text=CLOSE_BUTTON_TEXT, callback_data=close_callback)
    builder.adjust(1)
    return builder.as_markup()


def _build_toggle_button(
    player: dict | None,
    field: str,
    on_text: str,
    off_text: str,
    callback: str,
) -> types.InlineKeyboardButton:
    """Кнопка главного меню базы: показывает состояние и переключает рассылку."""
    text = off_text if _is_muted(player, field) else on_text
    return types.InlineKeyboardButton(text=text, callback_data=callback)


# --- Утренний отчёт: публичный API -----------------------------------------


def is_morning_report_muted(player: dict | None) -> bool:
    """Отключены ли у игрока утренние отчёты алхимии."""
    return _is_muted(player, MORNING_REPORT_MUTE_FIELD)


def build_report_close_markup() -> types.InlineKeyboardMarkup:
    """Клавиатура утреннего отчёта без кнопки отключения рассылки."""
    return _build_close_markup(MORNING_REPORT_CLOSE_CALLBACK)


def build_report_markup(muted: bool = False) -> types.InlineKeyboardMarkup:
    """Клавиатура утреннего отчёта: «Не присылать отчёты» + «Закрыть»."""
    return _build_mute_markup(
        muted,
        MORNING_REPORT_MUTE_TEXT,
        REPORT_MUTE_CALLBACK,
        MORNING_REPORT_CLOSE_CALLBACK,
    )


def build_report_toggle_button(player: dict | None) -> types.InlineKeyboardButton:
    """Переключатель утренних отчётов для главного меню базы."""
    return _build_toggle_button(
        player,
        MORNING_REPORT_MUTE_FIELD,
        MORNING_REPORT_TOGGLE_ON_TEXT,
        MORNING_REPORT_TOGGLE_OFF_TEXT,
        REPORT_TOGGLE_CALLBACK,
    )


# --- Отчёты о нападениях: публичный API ------------------------------------


def is_raid_report_muted(player: dict | None) -> bool:
    """Отключены ли у игрока отчёты о нападениях на его форт."""
    return _is_muted(player, RAID_REPORT_MUTE_FIELD)


def should_send_raid_defense_report(player: dict | None) -> bool:
    """Слать ли игроку отчёт о нападении на его форт.

    Точка решения одна: если позже появятся дополнительные условия (например,
    не беспокоить новичков), их добавляют здесь, а не в каждом месте отправки.
    """
    return not is_raid_report_muted(player)


def build_raid_report_close_markup() -> types.InlineKeyboardMarkup:
    """Клавиатура DM-отчёта рейда без кнопки отключения рассылки."""
    return _build_close_markup(RAID_REPORT_CLOSE_CALLBACK)


def build_raid_report_markup(muted: bool = False) -> types.InlineKeyboardMarkup:
    """Клавиатура отчёта о нападении: «Не присылать…» + «Закрыть»."""
    return _build_mute_markup(
        muted,
        RAID_REPORT_MUTE_TEXT,
        RAID_REPORT_MUTE_CALLBACK,
        RAID_REPORT_CLOSE_CALLBACK,
    )


def build_raid_report_toggle_button(player: dict | None) -> types.InlineKeyboardButton:
    """Переключатель отчётов о нападениях для главного меню базы."""
    return _build_toggle_button(
        player,
        RAID_REPORT_MUTE_FIELD,
        RAID_REPORT_TOGGLE_ON_TEXT,
        RAID_REPORT_TOGGLE_OFF_TEXT,
        RAID_REPORT_TOGGLE_CALLBACK,
    )
