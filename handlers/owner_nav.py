"""Shared navigation for the owner's private-chat menus.

Every screen in the private chat is reached by a button, so every screen must
offer a way back, a way to refresh its own state, and a way to close it. This
module keeps those three controls identical everywhere instead of letting each
panel grow its own wording and order.
"""
from __future__ import annotations

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

CLOSE_CALLBACK = 'owner_panel:close'
HOME_CALLBACK = 'owner_panel:home'
BACK_LABEL = '‹ Назад'
CLOSE_LABEL = '❌ Закрыть'
REFRESH_LABEL = '🔄 Обновить'


def breadcrumb(*parts: str) -> str:
    """One-line path to the current screen, e.g. «Пульт › Новости › Сценарии»."""
    return ' › '.join(part for part in parts if part)


def section_title(icon: str, title: str, *parents: str, note: str = '') -> str:
    """Title line plus breadcrumb, so the owner always sees where they are."""
    head = f'{icon} {title}'.strip()
    if parents:
        head += f'\n{breadcrumb(*parents, title)}'
    if note:
        head += f'\n\n{note}'
    return head


def add_nav(builder: InlineKeyboardBuilder, *, back: str | None = None,
            back_label: str = BACK_LABEL, refresh: str | None = None,
            refresh_label: str = REFRESH_LABEL) -> InlineKeyboardBuilder:
    """Append the standard bottom block: refresh row, then back/close row."""
    if refresh:
        builder.row(types.InlineKeyboardButton(text=refresh_label, callback_data=refresh))
    tail = []
    if back:
        tail.append(types.InlineKeyboardButton(text=back_label, callback_data=back))
    tail.append(types.InlineKeyboardButton(text=CLOSE_LABEL, callback_data=CLOSE_CALLBACK))
    builder.row(*tail)
    return builder


def ensure_nav(markup: types.InlineKeyboardMarkup | None, *, back: str | None = None,
               back_label: str = BACK_LABEL, refresh: str | None = None,
               refresh_label: str = REFRESH_LABEL) -> types.InlineKeyboardMarkup | None:
    """Add only the missing navigation buttons to an existing keyboard.

    Screens that already drew their own back/close rows keep them; this only
    fills the gaps, so a screen can never end up with no way out.
    """
    if not isinstance(markup, types.InlineKeyboardMarkup):
        return markup
    rows = [list(row) for row in markup.inline_keyboard]
    present = {button.callback_data for row in rows for button in row if button.callback_data}
    has_back = any((button.text or '').startswith(('‹', '⬅️', '↩️')) for row in rows for button in row)
    added = False
    if refresh and refresh not in present:
        rows.append([types.InlineKeyboardButton(text=refresh_label, callback_data=refresh)])
        added = True
    tail = []
    if back and not has_back and back not in present:
        tail.append(types.InlineKeyboardButton(text=back_label, callback_data=back))
    if CLOSE_CALLBACK not in present:
        tail.append(types.InlineKeyboardButton(text=CLOSE_LABEL, callback_data=CLOSE_CALLBACK))
    if tail:
        rows.append(tail)
        added = True
    if not added:
        return markup
    return markup.model_copy(update={'inline_keyboard': rows})
