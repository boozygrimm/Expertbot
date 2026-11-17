"""Owner's private control panel, separate from per-group /panel settings."""
from __future__ import annotations

import logging
from types import SimpleNamespace
from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import ADMIN_ID
from database import db
import owner_preferences
from news_prototype import owner_daily, owner_trial

router = Router()
logger = logging.getLogger(__name__)


def authorized(callback) -> bool:
    return bool(callback.from_user and callback.from_user.id == ADMIN_ID and callback.message
                and callback.message.chat.type == 'private' and callback.message.chat.id == ADMIN_ID)


async def home_content():
    # Migrate old defaults only when opening the panel, preserving current modes.
    await owner_trial.migrate_panel_defaults(db.db)
    await owner_preferences.migrate(db.settings)
    prefs = await owner_preferences.get(db.settings)
    daily = await owner_daily.enabled(db.db)
    drafts = await owner_trial.drafts_enabled(db.db)
    image = 'Cloudflare + OVHcloud' if prefs['image_provider'] == 'cloudflare' else 'Pollinations'
    search = 'включены' if prefs['search_images'] else 'выключены'
    text = ('🛠 ПУЛЬТ ВЛАДЕЛЬЦА\n\n'
            f'📰 Новости: сбор {"включён" if daily else "выключен"}, '
            f'сценарии {"включены" if drafts else "выключены"}\n'
            f'🎨 Рисование: {image}\n'
            f'🌐 Картинки в поиске: {search}\n\n'
            'Настройки рисования и поиска действуют во всех чатах. '
            'Модули конкретной группы настраиваются через /panel в этой группе.')
    builder = InlineKeyboardBuilder()
    builder.button(text='📰 Новости', callback_data='owner_panel:news')
    builder.button(text='🎨 Рисование', callback_data='owner_panel:images')
    builder.button(text='🌐 Картинки в поиске', callback_data='owner_panel:search')
    builder.button(text='🔐 Доступ к группам', callback_data='owner_panel:groups')
    builder.button(text='🔄 Обновить', callback_data='owner_panel:home')
    builder.button(text='❌ Закрыть', callback_data='owner_panel:close')
    builder.adjust(1, 2, 1, 2)
    return text, builder.as_markup()


async def image_content():
    prefs = await owner_preferences.get(db.settings)
    active = prefs['image_provider']
    text = ('🎨 РИСОВАНИЕ\n\n'
            f'Сейчас: {"Cloudflare + резерв OVHcloud" if active == "cloudflare" else "Pollinations"}.\n'
            'Выбор действует во всех чатах. Доступность и квоты сервисов зависят от их настроек; '
            'кнопки не выполняют тестовый запрос. Cloudflare требует настроенный доступ.\n\n'
            'Выбери сервис:')
    builder = InlineKeyboardBuilder()
    builder.button(text=('✅ ' if active == 'legacy' else '') + 'Pollinations', callback_data='owner_panel:image_legacy')
    builder.button(text=('✅ ' if active == 'cloudflare' else '') + 'Cloudflare + OVHcloud', callback_data='owner_panel:image_cloudflare')
    builder.button(text='⬅️ Пульт', callback_data='owner_panel:home')
    builder.adjust(1)
    return text, builder.as_markup()


async def search_content():
    prefs = await owner_preferences.get(db.settings)
    active = prefs['search_images']
    text = ('🌐 КАРТИНКИ В ПОИСКЕ\n\n'
            f'Сейчас: {"включены" if active else "выключены"} во всех чатах.\n'
            'При включении бот может добавлять найденную картинку к ответу; '
            'это может расходовать квоту поиска. Картинка не подтверждает достоверность ответа.')
    builder = InlineKeyboardBuilder()
    builder.button(text='⏸ Выключить' if active else '▶️ Включить',
                   callback_data='owner_panel:search_off' if active else 'owner_panel:search_on')
    builder.button(text='⬅️ Пульт', callback_data='owner_panel:home')
    builder.adjust(1)
    return text, builder.as_markup()


async def edit(message, text, markup):
    try:
        await message.edit_text(text, reply_markup=markup, parse_mode=None)
    except TelegramBadRequest as exc:
        if 'message is not modified' not in str(exc).lower():
            raise


async def open_owner_panel(message):
    try:
        text, markup = await home_content()
        await message.answer(text, reply_markup=markup, parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось открыть пульт владельца: %s', type(exc).__name__)
        await message.answer('Не удалось открыть пульт. Попробуй позже.', parse_mode=None)


@router.callback_query(F.data.startswith('owner_panel:'))
async def control_action(callback: types.CallbackQuery):
    if not authorized(callback):
        await callback.answer('Пульт доступен только владельцу в личке.', show_alert=True)
        return
    action = (callback.data or '').partition(':')[2]
    try:
        if action == 'close':
            await callback.answer()
            await callback.message.delete()
            return
        if action == 'groups':
            # Reuse the existing owner-only access control menu, without commands.
            from handlers import auth
            message = SimpleNamespace(from_user=callback.from_user, answer=callback.message.answer)
            await callback.answer()
            await auth.admin_chats_menu(message)
            return
        if action == 'news':
            from handlers.news_owner_panel import panel_content
            text, markup = await panel_content()
        elif action == 'home':
            text, markup = await home_content()
        elif action == 'images':
            text, markup = await image_content()
        elif action == 'search':
            text, markup = await search_content()
        elif action in ('image_legacy', 'image_cloudflare'):
            await owner_preferences.set_image_provider(db.settings, action.removeprefix('image_'))
            text, markup = await image_content()
        elif action in ('search_on', 'search_off'):
            await owner_preferences.set_search_images(db.settings, action == 'search_on')
            text, markup = await search_content()
        else:
            await callback.answer('Неизвестная кнопка.', show_alert=True)
            return
        await callback.answer()
        await edit(callback.message, text, markup)
    except Exception as exc:
        logger.warning('Не удалось выполнить действие пульта: %s', type(exc).__name__)
        try:
            await callback.answer('Не удалось выполнить действие. Открой пульт заново.', show_alert=True)
        except Exception:
            pass
