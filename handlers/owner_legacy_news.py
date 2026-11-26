"""Guided access to the optional, incompatible legacy news intake pipeline.

This module never enables any pipeline or changes its environment flags.  Saving
an item and enqueueing a digest are always explicit owner actions.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os
import secrets

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import db
from handlers import owner_tools_panel as tools
from handlers.owner_nav import add_nav
from news_prototype.news_queue import READY_STORY_STAGES
from news_prototype.news_worker_client import valid_id

router = Router()
logger = logging.getLogger(__name__)
PAGE_SIZE = 8
MAX_ITEMS = 100
MAX_CHOICE = 20


def enabled():
    return os.getenv('NEWS_INTAKE_ENABLED', '').strip() == '1'


def queue_enabled():
    return os.getenv('NEWS_RENDER_QUEUE_ENABLED', '').strip() == '1'


LEGACY_STAGES = (
    ('NEWS_COLLECTION_ENABLED', 'сбор'), ('NEWS_EDITOR_ENABLED', 'редактор сценариев'),
    ('NEWS_RENDER_QUEUE_ENABLED', 'очередь видео'), ('NEWS_REVIEW_ENABLED', 'доставка на проверку'),
)


def flag_snapshot():
    return {key: os.getenv(key, '').strip() == '1' for key, _ in LEGACY_STAGES}


def mode_notice():
    snapshot = flag_snapshot()
    active = [label for key, label in LEGACY_STAGES if snapshot[key]]
    return ('Дополнительно активны: ' + ', '.join(active) + '. '
            'Сохранение новости может запустить включённые фоновые этапы; проверь их прежде, чем подтверждать.'
            if active else 'Фоновые этапы этого режима сейчас выключены.')


def home():
    b = InlineKeyboardBuilder()
    if enabled():
        b.button(text='➕ Добавить новость', callback_data='legacynews:add')
        b.button(text='📋 Ручные новости', callback_data='legacynews:list')
        b.button(text='🎞 Готовые сюжеты и дайджест', callback_data='legacynews:ready')
        b.button(text='📊 Статус дайджестов', callback_data='legacynews:status')
    b.adjust(1)
    add_nav(b, back='news_panel:settings', back_label='‹ Настройки новостей', refresh='legacynews:home')
    if not enabled():
        return ('🗂 Дополнительный старый режим\nПульт › Новости › Настройки › Старый режим\n\nОн сейчас выключен и не нужен для обычной '
                'ежедневной подборки и сценариев. Пульт не включает его и не меняет настройки Render.'), b.as_markup()
    return ('🗂 Дополнительный старый режим\nПульт › Новости › Настройки › Старый режим\n\nЭто отдельный режим ручного приёма и дайджестов, '
            'несовместимый с режимом сценариев владельца. ' + mode_notice() +
            '\n\nПанель не включает обработку. Дайджест ставится в очередь только после подтверждения.'), b.as_markup()


async def render_selection(message, session):
    page, items, chosen = session['page'], session['items'], session['selected']
    pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    b = InlineKeyboardBuilder()
    for index in range(page * PAGE_SIZE, min((page + 1) * PAGE_SIZE, len(items))):
        item = items[index]
        b.button(text=('✅ ' if index in chosen else '▫️ ') + item['label'][:60],
                 callback_data=f'legacynews:select:{session["_id"]}:{index}')
    b.adjust(1)
    nav = InlineKeyboardBuilder()
    if page:
        nav.button(text='⬅️', callback_data=f'legacynews:page:{session["_id"]}:{page-1}')
    if page + 1 < pages:
        nav.button(text='➡️', callback_data=f'legacynews:page:{session["_id"]}:{page+1}')
    if page or page + 1 < pages:
        nav.adjust(2)
        b.attach(nav)
    bottom = InlineKeyboardBuilder()
    if len(chosen) >= 2:
        bottom.button(text='🎬 Собрать дайджест', callback_data=f'legacynews:build:{session["_id"]}')
    bottom.adjust(1)
    add_nav(bottom, back='legacynews:home', back_label='‹ Старый режим',
            refresh=f'legacynews:page:{session["_id"]}:{page}')
    b.attach(bottom)
    await message.edit_text(f'Готовые сюжеты · страница {page+1}/{pages}\n'
        f'Показаны до {MAX_ITEMS} последних. Выбрано: {len(chosen)} из {MAX_CHOICE}. '
        'Порядок нажатий определяет порядок в дайджесте. Нужно минимум два сюжета. '
        'До отдельного подтверждения очередь не запускается.', reply_markup=b.as_markup(), parse_mode=None)


@router.callback_query(F.data.startswith('legacynews:'))
async def callback_action(callback: types.CallbackQuery, state: FSMContext):
    if not tools.authorized(callback.from_user, callback.message.chat if callback.message else None):
        return await callback.answer('Доступно только владельцу в личке.', show_alert=True)
    parts = (callback.data or '').split(':')
    op = parts[1] if len(parts) > 1 else ''
    try:
        if op == 'home':
            await state.clear()
            text, markup = home()
            await callback.answer()
            await callback.message.edit_text(text, reply_markup=markup, parse_mode=None)
            return
        if not enabled():
            return await callback.answer('Дополнительный режим выключен.', show_alert=True)
        if op == 'add':
            await tools.ask(callback, state, 'legacy_news', 'Пришли текст новости или ссылку. '
                'Можно добавить отдельную строку «Акцент: ...» и, для текста, «Источник: https://...». '
                'Можно также ответить на сообщение с новостью, написав акцент.\n\n' + mode_notice() +
                '\nПеред сохранением потребуется подтверждение.')
            return
        if op in ('list', 'status'):
            from handlers import news_intake
            await state.clear()
            await callback.answer()
            message = tools.proxy(callback.message, callback.from_user)
            await (news_intake.list_news(message) if op == 'list' else news_intake.list_digests(message))
            return
        if op == 'ready':
            await state.clear()
            jobs = await db.db.news_render_jobs.find({'stage': {'$in': list(READY_STORY_STAGES)},
                '$or': [{'kind': 'story'}, {'kind': {'$exists': False}}]}).sort(
                [('created_at', -1), ('_id', -1)]).limit(MAX_ITEMS).to_list(length=MAX_ITEMS)
            items = []
            for job in jobs:
                try:
                    request_id = valid_id(job.get('_id'))
                except ValueError:
                    continue
                candidate = await db.db.news_candidates.find_one({'_id': job.get('candidate_id')}) or {}
                label = ' '.join(str(candidate.get('title') or candidate.get('text') or candidate.get('url') or 'Сюжет').split())[:100]
                items.append({'id': request_id, 'label': label + ' · ' + request_id[:8]})
            now = datetime.now(timezone.utc)
            token = secrets.token_hex(8)
            session = {'_id': token, 'owner_id': callback.from_user.id, 'message_id': callback.message.message_id,
                       'items': items, 'selected': [], 'page': 0, 'stage': 'open', 'expires_at': now + timedelta(hours=2)}
            await db.db.owner_legacy_news_sessions.delete_many({'expires_at': {'$lt': now}})
            await db.db.owner_legacy_news_sessions.insert_one(session)
            await callback.answer()
            await render_selection(callback.message, session)
            return
        if len(parts) < 3 or len(parts[2]) != 16 or any(c not in '0123456789abcdef' for c in parts[2]):
            raise ValueError('Неверная кнопка')
        token = parts[2]
        session = await db.db.owner_legacy_news_sessions.find_one({'_id': token, 'owner_id': callback.from_user.id,
            'message_id': callback.message.message_id, 'stage': 'open', 'expires_at': {'$gt': datetime.now(timezone.utc)}})
        if not session:
            return await callback.answer('Выбор устарел. Открой список заново.', show_alert=True)
        selected = session['selected']
        if op in ('select', 'page'):
            if len(parts) != 4 or not parts[3].isascii() or not parts[3].isdigit():
                raise ValueError('Неверный номер')
            index = int(parts[3])
            if op == 'page':
                if index >= max(1, (len(session['items']) + PAGE_SIZE - 1) // PAGE_SIZE):
                    raise ValueError('Неверная страница')
                session['page'] = index
                await db.db.owner_legacy_news_sessions.update_one({'_id': token, 'stage': 'open'}, {'$set': {'page': index}})
            else:
                if index >= len(session['items']):
                    raise ValueError('Неверный сюжет')
                if index in selected:
                    changed = [value for value in selected if value != index]
                elif len(selected) < MAX_CHOICE:
                    changed = selected + [index]
                else:
                    return await callback.answer(f'Можно выбрать не больше {MAX_CHOICE} сюжетов.', show_alert=True)
                result = await db.db.owner_legacy_news_sessions.update_one({'_id': token, 'stage': 'open',
                    'selected': selected}, {'$set': {'selected': changed}})
                if not result.modified_count:
                    return await callback.answer('Выбор изменился, попробуй снова.', show_alert=True)
                session['selected'] = changed
            await callback.answer()
            await render_selection(callback.message, session)
            return
        if op == 'build':
            if not queue_enabled():
                return await callback.answer('Очередь дополнительного режима выключена.', show_alert=True)
            if not 2 <= len(selected) <= MAX_CHOICE:
                raise ValueError('Нужно выбрать сюжеты')
            result = await db.db.owner_legacy_news_sessions.update_one({'_id': token, 'stage': 'open',
                'selected': selected}, {'$set': {'stage': 'confirming'}})
            if not result.modified_count:
                return await callback.answer('Выбор уже обрабатывается.', show_alert=True)
            items = [session['items'][i] for i in selected]
            lines = '\n'.join(f'{i}. {item["label"][:60]}' for i, item in enumerate(items, 1))
            await callback.answer()
            await tools.new_confirmation(callback.message, callback.from_user.id, 'legacy_digest',
                'Поставить дайджест из готовых сюжетов в очередь в этом порядке?\n' + lines +
                '\nЭто запустит сборку видео; публикации нет.', ids=[item['id'] for item in items])
            return
        await callback.answer('Неизвестное действие.', show_alert=True)
    except Exception as exc:
        logger.warning('Не удалось открыть дополнительный режим: %s', type(exc).__name__)
        try:
            await callback.answer('Не удалось выполнить действие. Открой раздел заново.', show_alert=True)
        except Exception:
            try:
                await callback.message.answer('Результат не подтверждён. Проверь состояние, не повторяй вслепую.', parse_mode=None)
            except Exception as answer_error:
                logger.debug('Не удалось сообщить о неподтверждённом результате: %s',
                             type(answer_error).__name__)
