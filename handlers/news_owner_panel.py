"""Private owner news panel; the group /panel remains separate.

Browsing and selection never start Gemini editing or rendering. Preparing
selected stories requires a separate confirmation, and rendering remains a
separate review-and-approve step in the existing workflow.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import secrets
from types import SimpleNamespace

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import ADMIN_ID
from database import db
from news_prototype import owner_daily, owner_discovery, owner_trial

router = Router()
logger = logging.getLogger(__name__)
PAGE_SIZE = 4


def authorized(callback) -> bool:
    return bool(callback.from_user and callback.from_user.id == ADMIN_ID and callback.message
                and callback.message.chat.type == 'private' and callback.message.chat.id == ADMIN_ID)


def daily_button():
    builder = InlineKeyboardBuilder()
    builder.button(text='📰 Открыть панель новостей', callback_data='news_panel:new')
    return builder.as_markup()


async def panel_content():
    now = datetime.now(timezone.utc)
    await owner_trial.migrate_panel_defaults(db.db)
    active = await owner_daily.enabled(db.db)
    drafts_active = await owner_trial.drafts_enabled(db.db)
    state = await db.db.news_owner_daily_state.find_one({'_id': 'last_poll'}) or {}
    count = await db.db.news_owner_daily_items.count_documents({
        'published_at': {'$gte': (now - timedelta(hours=24)).isoformat(), '$lte': now.isoformat()}})
    lines = ['📰 ПАНЕЛЬ НОВОСТЕЙ', '',
             ('🟢 Сбор включён' if active else '🔴 Сбор выключен') + ' · каждые 30 минут',
             ('🟢 Подготовка сценариев доступна' if drafts_active else '🔴 Подготовка сценариев выключена'),
             '📬 Подборка в личку · 03:00 МСК', f'За последние сутки собрано: {count}.']
    at = state.get('at')
    if at:
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        lines.append('Последняя проверка: ' + at.astimezone(owner_daily.MSK).strftime('%d.%m %H:%M МСК') + '.')
    elif active:
        lines.append('Первая проверка ещё не завершена.')
    if state.get('failed'):
        lines.append('Недоступны при проверке: ' + ', '.join(state['failed']) + '.')
    last = await db.db.news_owner_daily_deliveries.find_one({'owner_id': ADMIN_ID}, sort=[('day', -1)])
    if last:
        labels = {'preparing': 'готовится', 'ready': 'ожидает отправки', 'sending': 'отправка не подтверждена',
                  'uncertain': 'отправка не подтверждена', 'sent': 'доставлена'}
        lines.append(f'Последняя подборка ({last["day"]}): {labels.get(last.get("stage"), "неизвестно")}.')
    lines += ['', 'Ежедневная подборка — кандидаты, а не готовый сценарий. Сбор сам не запускает сценарии, рендер или публикацию.']
    builder = InlineKeyboardBuilder()
    builder.button(text='⏸ Выключить сбор' if active else '▶️ Включить сбор',
                   callback_data='news_panel:off' if active else 'news_panel:on')
    builder.button(text='📝 Выключить сценарии' if drafts_active else '📝 Включить сценарии',
                   callback_data='news_panel:modeoff' if drafts_active else 'news_panel:modeon')
    builder.button(text='📚 Собранные новости', callback_data='news_panel:list')
    builder.button(text='🔎 Найти свежие новости сейчас', callback_data='news_panel:discover')
    builder.button(text='🔗 Подготовить по ссылкам', callback_data='owner_tools:prepare')
    builder.button(text='📝 Черновики', callback_data='news_panel:drafts')
    builder.button(text='🎞 Выпуски', callback_data='news_panel:programs')
    builder.button(text='📺 Собрать выпуск', callback_data='newsact:choose')
    builder.button(text='📊 Статус видео', callback_data='owner_tools:trial_status')
    builder.button(text='✍️ Свой сценарий', callback_data='owner_tools:trial')
    builder.button(text='🗂 Дополнительный старый режим', callback_data='legacynews:home')
    builder.button(text='🔄 Обновить', callback_data='news_panel:home')
    builder.button(text='⬅️ Общий пульт', callback_data='owner_panel:home')
    builder.adjust(1, 1, 1, 2, 2)
    return '\n'.join(lines), builder.as_markup()


async def open_owner_panel(message):
    try:
        text, markup = await panel_content()
        await message.answer(text, reply_markup=markup, parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось открыть панель новостей: %s', type(exc).__name__)
        await message.answer('Не удалось открыть панель. Попробуй позже.', parse_mode=None)


async def edit_panel(message, text, markup):
    try:
        await message.edit_text(text, reply_markup=markup, parse_mode=None, disable_web_page_preview=True)
    except TelegramBadRequest as exc:
        if 'message is not modified' not in str(exc).lower():
            raise


async def render_home(message):
    text, markup = await panel_content()
    await edit_panel(message, text, markup)


async def new_session(owner_id: int, message_id: int, *, items=None):
    now = datetime.now(timezone.utc)
    if items is None:
        items = await owner_daily.recent_items(db.db, now)
    token = secrets.token_hex(8)
    session = {'_id': token, 'owner_id': owner_id, 'message_id': message_id,
               'created_at': now, 'items': items, 'selected': [], 'stage': 'open', 'translations': {}}
    await db.db.news_owner_panel_sessions.delete_many({'created_at': {'$lt': now - timedelta(days=2)}})
    await db.db.news_owner_panel_sessions.insert_one(session)
    return session


async def get_session(callback, token):
    if not authorized(callback) or len(token) != 16 or any(c not in '0123456789abcdef' for c in token):
        return None
    return await db.db.news_owner_panel_sessions.find_one({'_id': token, 'owner_id': callback.from_user.id})


async def page_content(session, page):
    items = session['items']
    pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    visible = items[start:start + PAGE_SIZE]
    cached = session.get('translations', {}).get(str(page))
    if cached is None and visible:
        # Only one small translation request per page in a panel session.
        from ai_providers import fire_gemini_key
        from models import GEMINI_CHAT_MODEL
        cached = await owner_discovery.translate_titles(visible, fire_gemini_key(), GEMINI_CHAT_MODEL)
        if not cached or len(cached) != len(visible):
            cached = [item['title'] for item in visible]
        await db.db.news_owner_panel_sessions.update_one({'_id': session['_id']},
                                                          {'$set': {f'translations.{page}': cached}})
    selected = session.get('selected', [])
    lines = [f'📚 Новости за сутки · {len(items)} · страница {page + 1}/{pages}',
             'Выбери 2–4 новости нажатием на номера. Перевод страницы может расходовать квоту Gemini.', '']
    for offset, item in enumerate(visible):
        index = start + offset + 1
        title = ' '.join(cached[offset].split())[:150]
        mark = '✅' if index in selected else '▫️'
        lines.append(f'{mark} {index}. {title}\n{item["source"]} · {item["published_at"][:16].replace("T", " ")} UTC\n')
    if not visible:
        lines.append('Пока нет новостей за последние сутки.')
    lines.append(f'Выбрано: {len(selected)} из 4. Без подтверждения сценарии не создаются.')
    builder = InlineKeyboardBuilder()
    for offset in range(len(visible)):
        index = start + offset + 1
        builder.button(text=f'{"✅" if index in selected else "▫️"} {index}',
                       callback_data=f'news_panel:sel:{session["_id"]}:{index}')
    if visible:
        builder.adjust(len(visible))
        links = InlineKeyboardBuilder()
        for offset, item in enumerate(visible):
            links.button(text=f'🔗 Источник {start + offset + 1}', url=item['url'])
        links.adjust(2)
        builder.attach(links)
    nav = InlineKeyboardBuilder()
    if page > 0:
        nav.button(text='⬅️ Назад', callback_data=f'news_panel:page:{session["_id"]}:{page - 1}')
    if page + 1 < pages:
        nav.button(text='Дальше ➡️', callback_data=f'news_panel:page:{session["_id"]}:{page + 1}')
    if page > 0 or page + 1 < pages:
        nav.adjust(2)
        builder.attach(nav)
    actions = InlineKeyboardBuilder()
    if len(selected) >= 2:
        actions.button(text='📝 Подготовить выбранные', callback_data=f'news_panel:prepare:{session["_id"]}')
    actions.button(text='🏠 Панель', callback_data='news_panel:home')
    actions.adjust(1)
    builder.attach(actions)
    return '\n'.join(lines), builder.as_markup()


async def render_page(message, session, page):
    text, markup = await page_content(session, page)
    await edit_panel(message, text, markup)


@router.callback_query(F.data.startswith('news_panel:'))
async def news_panel_action(callback: types.CallbackQuery, state: FSMContext = None):
    if not authorized(callback):
        await callback.answer('Панель доступна только владельцу в личке.', show_alert=True)
        return
    parts = (callback.data or '').split(':')
    action = parts[1] if len(parts) > 1 else ''
    if state and action in ('home', 'new', 'close', 'list', 'drafts', 'programs', 'discover'):
        await state.clear()
    try:
        if action == 'new':
            await callback.answer()
            await open_owner_panel(callback.message)
            return
        if action == 'close':
            await callback.answer()
            await callback.message.delete()
            return
        if action == 'home':
            await callback.answer()
            await render_home(callback.message)
            return
        if action in ('on', 'off'):
            await owner_daily.set_enabled(db.db, action == 'on')
            await callback.answer('Сбор включён' if action == 'on' else 'Сбор выключен')
            await render_home(callback.message)
            if action == 'on':
                from app_setup import collect_owner_daily_news
                await collect_owner_daily_news()
                await render_home(callback.message)
            return
        if action in ('modeon', 'modeoff'):
            if action == 'modeon' and owner_trial.conflicting_news_mode():
                await callback.answer('Другой новостной режим включён. Нужна проверка настроек.', show_alert=True)
                return
            await owner_trial.set_owner_mode(db.db, action == 'modeon')
            await callback.answer('Сценарии включены' if action == 'modeon' else 'Сценарии выключены')
            await render_home(callback.message)
            return
        if action == 'discover':
            if not await owner_trial.drafts_enabled(db.db):
                return await callback.answer('Включи подготовку сценариев в панели новостей.', show_alert=True)
            await callback.answer('Проверяю источники')
            await edit_panel(callback.message, 'Проверяю источники. Сценарии и видео не создаются.', None)
            items, failed = await owner_discovery.collect()
            if failed:
                await callback.message.answer('Не удалось проверить: ' + ', '.join(failed) + '.', parse_mode=None)
            session = await new_session(callback.from_user.id, callback.message.message_id, items=items)
            await render_page(callback.message, session, 0)
            return
        if action == 'list':
            await callback.answer('Открываю новости')
            session = await new_session(callback.from_user.id, callback.message.message_id)
            await render_page(callback.message, session, 0)
            return
        if action in ('drafts', 'programs'):
            collection = db.db.news_owner_drafts if action == 'drafts' else db.db.news_owner_programs
            id_field = 'draft_id' if action == 'drafts' else 'program_id'
            if len(parts) > 3 or (len(parts) == 3 and (not parts[2].isdigit() or len(parts[2]) > 5)):
                raise ValueError('Неверная страница')
            page = int(parts[2]) if len(parts) == 3 else 0
            entries = await collection.find({'owner_id': callback.from_user.id}).sort(
                [('created_at', -1), ('_id', -1)]).skip(page * 5).limit(6).to_list(length=6)
            has_next = len(entries) > 5
            entries = entries[:5]
            label = 'Черновики' if action == 'drafts' else 'Выпуски'
            builder = InlineKeyboardBuilder()
            stage_labels = {'draft_ready': 'готов к проверке', 'ready_for_review': 'готов к проверке',
                            'queued': 'в очереди', 'editing': 'готовится', 'fetching': 'читаю источник',
                            'editor_failed': 'ошибка подготовки', 'needs_source': 'нужен текст',
                            'approving': 'подтверждается', 'approval_uncertain': 'не подтверждено'}
            for entry in entries:
                number = entry[id_field]
                state = stage_labels.get(entry.get('stage'), entry.get('stage', 'неизвестно'))
                builder.button(text=f'№{number} · {state}', callback_data=f'news_panel:view_{action}:{number}')
            if page:
                builder.button(text='⬅️ Назад', callback_data=f'news_panel:{action}:{page - 1}')
            if has_next:
                builder.button(text='Дальше ➡️', callback_data=f'news_panel:{action}:{page + 1}')
            builder.button(text='🏠 Панель', callback_data='news_panel:home')
            builder.adjust(1)
            await callback.answer()
            await edit_panel(callback.message, f'{label}: страница {page + 1}.' if entries else f'{label}: на этой странице пусто.',
                             builder.as_markup())
            return
        if action in ('view_drafts', 'view_programs'):
            if len(parts) != 3 or not parts[2].isdigit() or len(parts[2]) > 15 or int(parts[2]) <= 0:
                raise ValueError('Неверный номер')
            from handlers import news_owner_drafts
            owner_message = SimpleNamespace(from_user=callback.from_user, chat=callback.message.chat, message_id=callback.message.message_id,
                                            answer=callback.message.answer, answer_photo=callback.message.answer_photo)
            await callback.answer('Открываю')
            if action == 'view_drafts':
                from news_prototype import owner_drafts
                item = await owner_drafts.get(db.db, callback.from_user.id, int(parts[2]))
                await news_owner_drafts.show(owner_message, int(parts[2]), item=item, panel_mode=True)
            else:
                from news_prototype import owner_program
                item = await owner_program.get(db.db, callback.from_user.id, int(parts[2]))
                await news_owner_drafts.show_program(owner_message, int(parts[2]), program=item, panel_mode=True)
            return
        if len(parts) < 3:
            raise ValueError('Неверная кнопка')
        session = await get_session(callback, parts[2])
        if not session:
            await callback.answer('Подборка устарела. Открой новости заново в панели.', show_alert=True)
            return
        if action in ('page', 'sel'):
            if session.get('stage') != 'open' or callback.message.message_id != session['message_id']:
                await callback.answer('Открой новости заново в панели.', show_alert=True)
                return
            if len(parts) != 4 or not parts[3].isdigit():
                raise ValueError('Неверный номер')
            value = int(parts[3])
            if action == 'page':
                if value >= max(1, (len(session['items']) + PAGE_SIZE - 1) // PAGE_SIZE):
                    raise ValueError('Неверная страница')
                await callback.answer()
                await render_page(callback.message, session, value)
                return
            if not 1 <= value <= len(session['items']):
                raise ValueError('Неверная новость')
            selected = list(session.get('selected', []))
            if value in selected:
                changed = [index for index in selected if index != value]
            elif len(selected) < 4:
                changed = selected + [value]
            else:
                await callback.answer('Можно выбрать не больше четырёх новостей.', show_alert=True)
                return
            result = await db.db.news_owner_panel_sessions.update_one(
                {'_id': session['_id'], 'stage': 'open', 'selected': selected}, {'$set': {'selected': changed}})
            await callback.answer()
            if not result.modified_count:
                session = await get_session(callback, session['_id'])
            else:
                session['selected'] = changed
            await render_page(callback.message, session, (value - 1) // PAGE_SIZE)
            return
        if action == 'prepare':
            from handlers import news_owner_drafts
            if not await owner_trial.drafts_enabled(db.db):
                await callback.answer('Подготовка сценариев сейчас выключена; сбор новостей работает отдельно.', show_alert=True)
                return
            selected = session.get('selected', [])
            urls = owner_discovery.choose({'items': session['items']}, selected)
            if callback.message.message_id != session['message_id']:
                raise ValueError('Неверная подборка')
            result = await db.db.news_owner_panel_sessions.update_one(
                {'_id': session['_id'], 'stage': 'open', 'selected': selected}, {'$set': {'stage': 'confirming'}})
            if not result.modified_count:
                await callback.answer('Эта подборка уже обрабатывается.', show_alert=True)
                return
            await callback.answer()
            builder = InlineKeyboardBuilder()
            builder.button(text='✅ Да, подготовить сценарии', callback_data=f'news_panel:confirm:{session["_id"]}')
            builder.button(text='❌ Отмена', callback_data=f'news_panel:cancel:{session["_id"]}')
            builder.adjust(1)
            msg = await callback.message.answer(f'Подготовить {len(urls)} сценария? Используется квота Gemini. '
                'Рендер и публикация не запускаются — сначала проверишь сценарии.',
                reply_markup=builder.as_markup(), parse_mode=None)
            await db.db.news_owner_panel_sessions.update_one({'_id': session['_id'], 'stage': 'confirming'},
                                                             {'$set': {'confirmation_id': msg.message_id}})
            return
        if action in ('confirm', 'cancel'):
            if session.get('stage') != 'confirming' or callback.message.message_id != session.get('confirmation_id'):
                await callback.answer('Подтверждение устарело.', show_alert=True)
                return
            if action == 'cancel':
                result = await db.db.news_owner_panel_sessions.update_one(
                    {'_id': session['_id'], 'stage': 'confirming', 'confirmation_id': callback.message.message_id},
                    {'$set': {'stage': 'cancelled'}})
                await callback.answer()
                if result.modified_count:
                    await edit_panel(callback.message, 'Подготовка отменена. Для нового выбора открой /panel.', None)
                return
            from handlers import news_owner_drafts
            if not await owner_trial.drafts_enabled(db.db):
                await callback.answer('Подготовка сценариев выключена.', show_alert=True)
                return
            urls = owner_discovery.choose({'items': session['items']}, session['selected'])
            result = await db.db.news_owner_panel_sessions.update_one(
                {'_id': session['_id'], 'stage': 'confirming', 'confirmation_id': callback.message.message_id},
                {'$set': {'stage': 'preparing'}})
            if not result.modified_count:
                await callback.answer('Эта подборка уже обрабатывается.', show_alert=True)
                return
            await callback.answer('Готовлю сценарии')
            await edit_panel(callback.message, 'Готовлю сценарии. Рендер ещё не запущен.', None)
            from handlers.owner_tools_panel import proxy
            owner_message = proxy(callback.message, callback.from_user)
            await news_owner_drafts.prepare_urls(owner_message, urls)
            await db.db.news_owner_panel_sessions.update_one({'_id': session['_id'], 'stage': 'preparing'},
                                                             {'$set': {'stage': 'prepared'}})
            return
        raise ValueError('Неизвестное действие')
    except Exception as exc:
        logger.warning('Не удалось выполнить действие панели новостей: %s', type(exc).__name__)
        try:
            await callback.answer('Не удалось выполнить действие. Открой панель заново.', show_alert=True)
        except Exception:
            pass
