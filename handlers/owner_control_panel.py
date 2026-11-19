"""Owner's private control panel, separate from per-group /panel settings."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
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
    text = ('Пульт владельца\n\n'
            f'Сбор Crypto Sensei и RSS · {"включён" if daily else "выключен"}\n'
            f'Сценарии · {"включены" if drafts else "выключены"}\n'
            f'Рисование · {image}\n'
            f'Картинки в поиске · {search}\n\n'
            'Выбери раздел. Настройки картинок и поиска действуют во всех чатах. '
            'Модули группы — через /panel в этой группе.')
    builder = InlineKeyboardBuilder()
    builder.button(text='📰 Новости', callback_data='owner_panel:news')
    builder.button(text='🎬 Видео в чат', callback_data='ovideo:open')
    builder.button(text='👥 Группы', callback_data='owner_panel:group_menu')
    builder.button(text='⚗️ Алхимия', callback_data='owner_panel:alchemy')
    builder.button(text='🎨 Картинки и поиск', callback_data='owner_panel:media')
    builder.button(text='❔ Справка', callback_data='owner_panel:help')
    builder.button(text='📊 Статус', callback_data='owner_panel:status')
    builder.button(text='🔄 Обновить', callback_data='owner_panel:home')
    builder.button(text='❌ Закрыть', callback_data='owner_panel:close')
    builder.adjust(2, 2, 1, 2, 2)

    return text, builder.as_markup()


def section_content(section):
    """Navigation only; existing action callbacks and confirmations stay intact."""
    menus = {
        'group_menu': ('👥 Группы\n\nДоступ, расписания отчётов и очки участников.', [
            ('Доступ к группам', 'owner_panel:groups'),
            ('Расписания отчётов', 'owner_tools:reports'),
            ('Очки игрока в группе', 'owner_tools:points'),
        ], 'owner_panel:home'),
        'alchemy': ('⚗️ Алхимия\n\nШтаб, справочники и управление игрой.', [
            ('Штаб алхимии', 'owner_tools:base'),
            ('Рецепты и справка', 'owner_panel:alchemy_ref'),
            ('Управление алхимией', 'owner_panel:alchemy_admin'),
        ], 'owner_panel:home'),
        'alchemy_ref': ('📚 Рецепты и справка', [
            ('Все рецепты', 'owner_tools:recipes'),
            ('Найти рецепт', 'owner_tools:recipe'),
            ('Справка по наследиям', 'owner_tools:lorehelp'),
        ], 'owner_panel:alchemy'),
        'alchemy_admin': ('🛠 Управление алхимией\n\nПроверь действие перед запуском: некоторые изменения необратимы.', [
            ('Редактор наследий', 'owner_tools:lore'),
            ('Перезапустить квесты', 'owner_tools:regen'),
            ('Обновить начальные наследия', 'owner_tools:seed'),
            ('Выдать промопак игроку', 'owner_tools:pack'),
            ('Очистить инвентарь игрока', 'owner_tools:clear'),
            ('Удалить профиль алхимии', 'owner_tools:reset'),
        ], 'owner_panel:alchemy'),
        'media': ('🎨 Картинки и поиск\n\nНастройки действуют во всех чатах.', [
            ('Рисование', 'owner_panel:images'),
            ('Картинки в поиске', 'owner_panel:search'),
        ], 'owner_panel:home'),
        'help': ('❔ Справка', [
            ('Справка по боту', 'owner_tools:help'),
        ], 'owner_panel:home'),
        # Keep the old route usable from already-sent keyboards and video flow.
        'tools': ('🧰 Инструменты\n\nВыбери раздел.', [
            ('🎬 Видео в чат', 'ovideo:open'),
            ('👥 Группы', 'owner_panel:group_menu'),
            ('⚗️ Алхимия', 'owner_panel:alchemy'),
        ], 'owner_panel:home'),
    }
    text, actions, back = menus[section]
    b = InlineKeyboardBuilder()
    for label, callback in actions:
        b.button(text=label, callback_data=callback)
    if section == 'help':
        b.button(text='Связь с владельцем', url='https://t.me/boozygrimm')
    b.adjust(1)
    b.row(types.InlineKeyboardButton(text='‹ Назад', callback_data=back),
          types.InlineKeyboardButton(text='Закрыть', callback_data='owner_panel:close'))
    return text, b.as_markup()


async def image_content():
    prefs = await owner_preferences.get(db.settings)
    active = prefs['image_provider']
    text = ('🎨 Рисование\n\n'
            f'Сейчас: {"Cloudflare + резерв OVHcloud" if active == "cloudflare" else "Pollinations"}.\n'
            'Выбор действует во всех чатах и сам не запускает проверку. Cloudflare требует настроенный доступ. '
            'Проверка сервисов потребует подтверждения и может расходовать квоту.\n\n'
            'Выбери сервис:')
    builder = InlineKeyboardBuilder()
    builder.button(text=('✅ ' if active == 'legacy' else '') + 'Pollinations', callback_data='owner_panel:image_legacy')
    builder.button(text=('✅ ' if active == 'cloudflare' else '') + 'Cloudflare + OVHcloud', callback_data='owner_panel:image_cloudflare')
    builder.button(text='Нарисовать картинку', callback_data='owner_tools:draw')
    builder.button(text='Проверить сервисы', callback_data='owner_tools:image_probe')
    builder.adjust(1)
    builder.row(types.InlineKeyboardButton(text='‹ Картинки и поиск', callback_data='owner_panel:media'),
                types.InlineKeyboardButton(text='Закрыть', callback_data='owner_panel:close'))
    return text, builder.as_markup()


async def search_content():
    prefs = await owner_preferences.get(db.settings)
    active = prefs['search_images']
    text = ('🌐 Картинки в поиске\n\n'
            f'Сейчас: {"включены" if active else "выключены"} во всех чатах.\n'
            'При включении бот может добавлять найденную картинку к ответу; '
            'это может расходовать квоту поиска. Картинка не подтверждает достоверность ответа.')
    builder = InlineKeyboardBuilder()
    builder.button(text='⏸ Выключить' if active else '▶️ Включить',
                   callback_data='owner_panel:search_off' if active else 'owner_panel:search_on')
    builder.button(text='Проверить поиск', callback_data='owner_tools:search_probe')
    builder.button(text='Проверить поиск по своему запросу', callback_data='owner_tools:search_query')
    builder.adjust(1)
    builder.row(types.InlineKeyboardButton(text='‹ Картинки и поиск', callback_data='owner_panel:media'),
                types.InlineKeyboardButton(text='Закрыть', callback_data='owner_panel:close'))
    return text, builder.as_markup()


def _msk_time(value):
    if not isinstance(value, datetime):
        return 'ещё не было'
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)  # Mongo stores UTC.
    return value.astimezone(owner_daily.MSK).strftime('%d.%m.%Y %H:%M МСК')


async def status_content():
    """Read-only snapshot of saved state; it does not probe external services."""
    from news_prototype import general_auto, general_editorial, general_sources
    from handlers.general_news_panel import run_summary
    prefs = await owner_preferences.get(db.settings)
    daily = await owner_daily.enabled(db.db)
    drafts = await owner_trial.drafts_enabled(db.db)
    video_enabled = await owner_trial.runtime_enabled(db.db)
    automatic = await general_auto.enabled(db.db)
    categories = len(general_editorial.LABELS) - len(await general_auto.disabled_categories(db.db))
    sources = await general_sources.list_sources(db.db)
    collected = await db.db.news_general_candidates.count_documents({'hidden': {'$ne': True}})
    ready = await db.db.news_owner_drafts.count_documents({'owner_id': ADMIN_ID, 'stage': 'draft_ready'})
    poll = await db.db.news_general_state.find_one({'_id': 'last_poll'}) or {}
    once = await db.db.news_general_state.find_one({'_id': 'one_shot_last'}) or {}
    last_auto = await db.db.news_general_state.find_one({'_id': 'auto_last'}) or {}
    usage = await general_auto.usage(db.db)
    stage, _ = await owner_trial.status_info(db.db)
    video_status = {
        'not_queued': 'слот свободен', 'admission_incomplete': 'сохранение не завершено',
        'queued': 'в очереди', 'submitting': 'проверяется приём', 'running': 'создаётся',
        'ready_for_review': 'готово к доставке', 'fetching_review': 'скачивается для просмотра',
        'sending_review': 'отправляется владельцу', 'review_sent': 'отправлено владельцу',
        'review_uncertain': 'отправка не подтверждена', 'review_too_large': 'слишком большое видео',
        'review_download_failed': 'ошибка получения видео', 'uncertain': 'приём не подтверждён',
        'interrupted': 'задача прервана', 'failed': 'ошибка', 'audio_too_long': 'слишком длинное аудио',
    }.get(stage, 'неизвестно, нужна проверка')
    statuses = {'no_candidates': 'нет подходящих новостей', 'draft_ready': 'сценарий подготовлен',
        'budget_exhausted': 'исчерпан лимит', 'keys_missing': 'не заданы ключи',
        'drafts_off': 'сценарии выключены', 'timeout_uncertain': 'время истекло, результат не подтверждён',
        'editor_failed': 'ошибка редактора', 'needs_source': 'нужен источник', 'skipped': 'пропущено редактором',
        'id_conflict': 'конфликт номера сценария',
        'uncertain': 'результат не подтверждён', 'already_claimed': 'новость уже обрабатывалась',
        'throttled': 'ожидание следующего интервала', 'busy': 'редактор занят', 'off': 'отбор выключен'}
    image = 'Cloudflare + OVHcloud' if prefs['image_provider'] == 'cloudflare' else 'Pollinations'
    text = ('📊 Статус пульта\n\n'
            'Общая подборка\n'
            f'Автоотбор · {"включён" if automatic else "выключен"}\n'
            f'Категории · {categories} из {len(general_editorial.LABELS)}\n'
            f'Источники · {len(sources)} активных\n'
            f'Сохранено видимых новостей · {collected}\n'
            f'Последний сбор · {_msk_time(poll.get("at"))}\n')
    if poll.get('at'):
        text += f'Ошибок источников при сборе · {len(poll.get("failed") or [])}\n'
    else:
        text += 'Источники сбором ещё не проверялись.\n'
    text += f'Попытки сегодня · {usage} из {general_auto.DAILY_ATTEMPTS} (авто и запуск кнопкой)\n'
    if once.get('at'):
        text += f'Последний запуск кнопкой · {_msk_time(once["at"])}\n{run_summary(once)}\n'
    if last_auto.get('at'):
        text += (f'Последняя проверка отбора · {_msk_time(last_auto["at"])} · '
                 + statuses.get(last_auto.get('status'), 'состояние неизвестно') + '\n')
    text += ('\nСценарии и видео\n'
             f'Подготовка сценариев · {"включена" if drafts else "выключена"}\n'
             f'Черновиков готово к просмотру · {ready}\n'
             f'Режим видео · {"включён" if video_enabled else "выключен"}\n'
             f'Слот видео · {video_status}\n'
             f'Ключи Gemini и Groq · {"оба указаны" if general_auto.keys_ready() else "один или оба не указаны"}\n'
             'Доступность и квоты сервисов этим экраном не проверяются.\n\n'
             'Остальное\n'
             f'Сбор Crypto Sensei и RSS · {"включён" if daily else "выключен"}\n'
             f'Рисование · {image}\n'
             f'Картинки в поиске · {"включены" if prefs["search_images"] else "выключены"}\n\n'
             'Статус — сохранённые данные, а не проверка работы сервера или источников в реальном времени.')
    b = InlineKeyboardBuilder()
    b.button(text='Обновить', callback_data='owner_panel:status')
    b.button(text='Общая подборка', callback_data='gnews:home')
    b.button(text='Готовые сценарии', callback_data='news_panel:drafts')
    b.button(text='Crypto Sensei и RSS', callback_data='news_panel:collection')
    if poll.get('failed'):
        b.button(text='Ошибки источников', callback_data='gnews:failed:0')
    b.adjust(1)
    b.row(types.InlineKeyboardButton(text='‹ Пульт', callback_data='owner_panel:home'),
          types.InlineKeyboardButton(text='Закрыть', callback_data='owner_panel:close'))
    return text, b.as_markup()


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
async def control_action(callback: types.CallbackQuery, state: FSMContext = None):
    if not authorized(callback):
        await callback.answer('Пульт доступен только владельцу в личке.', show_alert=True)
        return
    action = (callback.data or '').partition(':')[2]
    if state and action in ('home', 'news', 'close', 'tools', 'images', 'search', 'groups',
                            'group_menu', 'alchemy', 'alchemy_ref', 'alchemy_admin', 'media', 'help', 'status'):
        await state.clear()
    try:
        if action == 'close':
            await callback.answer()
            await callback.message.delete()
            return
        if action == 'groups':
            # Reuse the existing owner-only access control menu, without commands.
            from handlers import auth
            message = SimpleNamespace(from_user=callback.from_user, chat=callback.message.chat, answer=callback.message.answer)
            await callback.answer()
            await auth.admin_chats_menu(message)
            return
        if action == 'news':
            from handlers.news_owner_panel import panel_content
            text, markup = await panel_content()
        elif action == 'home':
            text, markup = await home_content()
        elif action == 'status':
            text, markup = await status_content()
        elif action in ('tools', 'group_menu', 'alchemy', 'alchemy_ref', 'alchemy_admin', 'media', 'help'):
            text, markup = section_content(action)
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
