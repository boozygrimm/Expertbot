"""Owner-only controls for the separate general-news candidates and sources.

No scheduled automation. Explicit source polling never generates a script;
preparing one script is a separate one-use confirmation in owner_tools_panel.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import secrets

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import ADMIN_ID
from database import db
from handlers import owner_tools_panel as tools
from news_prototype import general_auto, general_editorial, general_queue, general_sources, news_scheduler, owner_drafts, owner_trial

router = Router()
logger = logging.getLogger(__name__)
PAGE_SIZE = 6


def _plain(value, limit=80):
    return ' '.join(str(value or '').split())[:limit]


async def _edit(message, text, **kwargs):
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        if 'message is not modified' not in str(exc).lower():
            raise


def home_markup():
    b = InlineKeyboardBuilder()
    b.button(text='📚 Новости', callback_data='gnews:news:0')
    b.button(text='📄 Готовые сценарии', callback_data='gnews:ready:0')
    b.button(text='🗂 Источники', callback_data='gnews:sources:0')
    b.button(text='⚙️ Автоотбор', callback_data='gnews:auto')
    b.button(text='🔗 Добавить новость', callback_data='gnews:addnews')
    b.button(text='🔄 Собрать новости сейчас', callback_data='gnews:poll')
    b.button(text='⬅️ Панель новостей', callback_data='news_panel:home')
    b.adjust(1)
    return b.as_markup()


async def home(message):
    sources = await general_sources.list_sources(db.db)
    total = await db.db.news_general_candidates.count_documents({'hidden': {'$ne': True}})
    state = await db.db.news_general_state.find_one({'_id': 'last_poll'}) or {}
    automatic = await general_auto.enabled(db.db)
    text = ('📰 Общая подборка\n' + ('🟢 Автоотбор включён.\n' if automatic else '🔴 Автоотбор выключен.\n')
            + f'Источников: {len(sources)} · новостей: {total}.\n'
            'Crypto Sensei остаётся отдельно. Рендер и публикация не запускаются.')
    if state.get('at'):
        text += '\nПоследний сбор: ' + state['at'].strftime('%d.%m %H:%M') + ' UTC.'
    if state.get('failed'):
        text += f'\nНедоступных источников: {len(state["failed"])}.'
    await _edit(message, text, reply_markup=home_markup(), parse_mode=None, disable_web_page_preview=True)


async def auto_screen(message):
    automatic = await general_auto.enabled(db.db)
    count = await general_auto.usage(db.db)
    last = await db.db.news_general_state.find_one({'_id': 'auto_last'}) or {}
    b = InlineKeyboardBuilder()
    b.button(text='⏸ Выключить автоотбор' if automatic else '▶️ Включить автоотбор',
             callback_data='gnews:autooff' if automatic else 'gnews:autoon')
    b.button(text='⬅️ Общая подборка', callback_data='gnews:home')
    b.adjust(1)
    text = ('⚙️ Автоотбор ' + ('включён' if automatic else 'выключен') + '\n'
            'Сбор раз в 30 минут; подготовка не чаще одного сюжета за 15 минут.\n'
            f'Автоматических попыток сегодня по МСК: {count} из {general_auto.DAILY_ATTEMPTS}. '
            'Даже неудачная попытка учитывается. Это лимит количества, не денежных расходов.\n'
            'Используется квота Gemini и Groq; рендер и публикация не запускаются. '
            'Уже начатая обработка после выключения может завершиться.\n'
            'Темы определяются приблизительно; посты с рекламной отметкой и похожими заголовками откладываются. Повторы возможны.')
    descriptions = {'keys_missing': 'Нет ключей Gemini или Groq.', 'drafts_off': 'Подготовка сценариев выключена.',
        'timeout_uncertain': 'Время обработки истекло; результат нужно проверить, повтор не запущен.',
        'budget_exhausted': 'Достигнут дневной лимит попыток.', 'no_candidates': 'Сейчас нет подходящих кандидатов.',
        'draft_ready': 'Последний черновик подготовлен.', 'needs_source': 'Для последнего сюжета нужен источник.',
        'editor_failed': 'Последний сценарий не удалось подготовить.', 'skipped': 'Последний сюжет пропущен редактором.',
        'uncertain': 'Результат последней попытки не подтверждён.', 'already_claimed': 'Новость уже обработана или обрабатывается.'}
    if last.get('status') in descriptions:
        text += '\n' + descriptions[last['status']]
    await _edit(message, text, reply_markup=b.as_markup(), parse_mode=None)


async def _session(message, owner_id, kind, ids):
    now = datetime.now(timezone.utc)
    token = secrets.token_hex(8)
    await db.db.news_general_panel_sessions.delete_many({'expires_at': {'$lt': now}})
    await db.db.news_general_panel_sessions.insert_one({'_id': token, 'owner_id': owner_id,
        'message_id': message.message_id, 'kind': kind, 'ids': ids,
        'expires_at': now + timedelta(hours=2)})
    return token


async def _selected(callback, token, index, kind):
    if len(token) != 16 or any(c not in '0123456789abcdef' for c in token):
        return None
    if not index.isascii() or not index.isdigit() or len(index) > 4:
        return None
    session = await db.db.news_general_panel_sessions.find_one({'_id': token, 'owner_id': callback.from_user.id,
        'message_id': callback.message.message_id, 'kind': kind, 'expires_at': {'$gt': datetime.now(timezone.utc)}})
    number = int(index)
    if not session or number >= len(session['ids']):
        return None
    return session['ids'][number]


async def source_list(message, owner_id, page):
    sources = await general_sources.list_sources(db.db, include_removed=True)
    pages = max(1, (len(sources) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    token = await _session(message, owner_id, 'source', [source['id'] for source in sources])
    b = InlineKeyboardBuilder()
    for index, source in enumerate(sources[page * PAGE_SIZE:(page + 1) * PAGE_SIZE], start=page * PAGE_SIZE):
        b.button(text=('🟢 ' if source['enabled'] else '⚪️ ') + _plain(source['name'], 52),
                 callback_data=f'gnews:source:{token}:{index}')
    if page:
        b.button(text='⬅️ Назад', callback_data=f'gnews:sources:{page - 1}')
    if page + 1 < pages:
        b.button(text='Дальше ➡️', callback_data=f'gnews:sources:{page + 1}')
    b.button(text='➕ Telegram-канал', callback_data='gnews:addtg')
    b.button(text='➕ RSS сайта', callback_data='gnews:addrss')
    b.button(text='⬅️ Общая подборка', callback_data='gnews:home')
    b.adjust(1)
    await _edit(message, f'🗂 Источники · страница {page + 1}/{pages}\n'
        'Выбери источник, чтобы убрать или вернуть его. Уже собранные новости и сценарии сохранятся.\n'
        'Для сайта пока нужна ссылка на его RSS-ленту.', reply_markup=b.as_markup(), parse_mode=None)


async def source_card(message, source, token, index):
    b = InlineKeyboardBuilder()
    action = 'removesource' if source['enabled'] else 'restoresource'
    b.button(text='🗑 Убрать источник' if source['enabled'] else '↩️ Вернуть источник',
             callback_data=f'gnews:{action}:{token}:{index}')
    b.button(text='⬅️ Источники', callback_data='gnews:sources:0')
    b.adjust(1)
    label = 'Telegram' if source['kind'] == 'telegram' else 'RSS'
    await _edit(message, f'{_plain(source["name"], 100)} · {label}\n{_plain(source["location"], 500)}\n'
        + ('Активен.' if source['enabled'] else 'Убран из будущего сбора.') +
        '\nУже начавшийся сбор может завершиться; готовые сценарии не удаляются.',
        reply_markup=b.as_markup(), parse_mode=None, disable_web_page_preview=True)


async def news_list(message, owner_id, page, kind='news'):
    query = {'hidden': True} if kind == 'removed' else {'hidden': {'$ne': True}}
    if kind == 'ready':
        query['stage'] = 'draft_ready'
    items = await db.db.news_general_candidates.find(query).sort('first_seen_at', -1).limit(240).to_list(length=240)
    if kind == 'ready':
        # Candidate status is a snapshot; a draft may since have been queued or
        # removed. Show only the owner's currently reviewable matching drafts.
        numbers = [item['draft_id'] for item in items
                   if item.get('owner_id') == owner_id and isinstance(item.get('draft_id'), int)]
        drafts = await db.db.news_owner_drafts.find({'owner_id': owner_id, 'stage': 'draft_ready',
            'draft_id': {'$in': numbers}}, {'draft_id': 1, 'general_candidate_id': 1}).to_list(length=240)
        current = {(draft.get('draft_id'), draft.get('general_candidate_id')) for draft in drafts}
        items = [item for item in items if (item.get('draft_id'), item['_id']) in current]
    pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    token = await _session(message, owner_id, kind, [item['_id'] for item in items])
    b = InlineKeyboardBuilder()
    for index, item in enumerate(items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE], start=page * PAGE_SIZE):
        label = _plain(item.get('title'), 58) or 'Новость'
        b.button(text=f'{index + 1}. {label}', callback_data=f'gnews:item:{token}:{index}')
    if page:
        b.button(text='⬅️ Назад', callback_data=f'gnews:{kind}:{page - 1}')
    if page + 1 < pages:
        b.button(text='Дальше ➡️', callback_data=f'gnews:{kind}:{page + 1}')
    b.button(text='↩️ Убранные новости' if kind != 'removed' else '📚 К новостям',
             callback_data='gnews:removed:0' if kind != 'removed' else 'gnews:news:0')
    b.button(text='🔗 Добавить новость', callback_data='gnews:addnews')
    b.button(text='⬅️ Общая подборка', callback_data='gnews:home')
    b.adjust(1)
    title = {'removed': 'Убранные новости', 'ready': 'Готовые сценарии', 'news': 'Новости общей подборки'}[kind]
    await _edit(message, f'📚 {title} · {len(items)} · страница {page + 1}/{pages}\n'
        'Показаны последние 240. Проверка и рендер требуют внимания владельца.', reply_markup=b.as_markup(), parse_mode=None)


async def news_card(message, item, token, index):
    hidden = item.get('hidden') is True
    stages = {'collected': 'ожидает подготовки', 'reserved': 'обрабатывается или нужна проверка состояния',
              'draft_ready': 'сценарий сохранён', 'needs_source': 'нужен текст источника',
              'skipped': 'редактор пропустил', 'editor_failed': 'не удалось подготовить', 'id_conflict': 'конфликт номера'}
    text = (f'📰 {_plain(item.get("title"), 180)}\nИсточник: {_plain(item.get("source"), 100)}\n'
            f'Состояние: {stages.get(item.get("stage"), "неизвестно")}.'
            + ('\nНовость убрана из общего списка.' if hidden else ''))
    if item.get('stage') == 'collected':
        assessment = general_editorial.assess(item.get('title', ''), item.get('topic_hint', 'general'))
        topics = ', '.join(general_editorial.LABELS.get(t, t) for t in assessment['topics'])
        text += '\nВозможные темы: ' + topics + ' (предварительно).'
        reason = await general_auto.defer_reason(db.db, item)
        if reason == 'advertising_marker':
            text += '\nЕсть рекламная отметка: автоотбор пропустит новость, вручную можно выбрать.'
        elif reason == 'similar_headline':
            text += '\nПохоже на уже рассмотренную новость: автоотбор пропустит, вручную можно выбрать.'
    b = InlineKeyboardBuilder()
    if item.get('url'):
        b.button(text='🔗 Открыть источник', url=item['url'])
    if not hidden and item.get('stage') == 'collected':
        b.button(text='📝 Подготовить сценарий', callback_data=f'gnews:prepare:{token}:{index}')
    if not hidden and item.get('draft_id') and item.get('owner_id') == ADMIN_ID:
        b.button(text='📄 Открыть черновик', callback_data=f'gnews:draft:{token}:{index}')
    b.button(text='↩️ Вернуть новость' if hidden else '🗑 Убрать новость',
             callback_data=f'gnews:{"restore" if hidden else "hide"}:{token}:{index}')
    b.button(text='⬅️ Новости', callback_data='gnews:removed:0' if hidden else 'gnews:news:0')
    b.adjust(1)
    await _edit(message, text, reply_markup=b.as_markup(), parse_mode=None, disable_web_page_preview=True)


@router.callback_query(F.data.startswith('gnews:'))
async def callback_action(callback: types.CallbackQuery, state: FSMContext):
    if not callback.message or not tools.authorized(callback.from_user, callback.message.chat):
        return await callback.answer('Только для владельца в личке.', show_alert=True)
    parts = (callback.data or '').split(':')
    action = parts[1] if len(parts) > 1 else ''
    try:
        if action in ('home', 'auto', 'sources', 'news', 'removed', 'ready'):
            await state.clear()
            page = 0
            if action not in ('home', 'auto'):
                if len(parts) != 3 or not parts[2].isascii() or not parts[2].isdigit() or len(parts[2]) > 4:
                    raise ValueError('Неверная страница')
                page = int(parts[2])
            await callback.answer()
            if action == 'home':
                await home(callback.message)
            elif action == 'auto':
                await auto_screen(callback.message)
            elif action == 'sources':
                await source_list(callback.message, callback.from_user.id, page)
            else:
                await news_list(callback.message, callback.from_user.id, page, action)
            return
        if action == 'autoon':
            if await general_auto.enabled(db.db):
                return await callback.answer('Автоотбор уже включён.', show_alert=True)
            if not await owner_trial.drafts_enabled(db.db):
                return await callback.answer('Сначала включи сценарии в панели новостей.', show_alert=True)
            if not general_auto.keys_ready():
                return await callback.answer('Нужны настроенные ключи Gemini и Groq.', show_alert=True)
            await callback.answer()
            await tools.new_confirmation(callback.message, callback.from_user.id, 'general_auto_on',
                'Включить автоматический сбор каждые 30 минут и подготовку не чаще раза в 15 минут? '
                f'До {general_auto.DAILY_ATTEMPTS} автоматических попыток в сутки по МСК, включая неудачные. '
                'Используется квота Gemini и Groq; это не денежный лимит. Проверка Groq может быть недоступна. '
                'Рендер и публикация не запускаются. Уже начатая обработка может завершиться после выключения.',
                version=await general_auto.setting_version(db.db))
            return
        if action == 'autooff':
            await general_auto.set_enabled(db.db, False)
            await callback.answer('Выключено. Начатая обработка может завершиться.')
            await auto_screen(callback.message)
            return
        if action in ('addtg', 'addrss', 'addnews'):
            prompts = {'addtg': 'Пришли название канала и его @имя или ссылку, каждое с новой строки.',
                       'addrss': 'Пришли название сайта и прямую HTTPS-ссылку на RSS, каждое с новой строки. Автопоиск RSS пока не поддерживается.',
                       'addnews': 'Пришли HTTPS-ссылку на статью или отдельный публичный пост. Сценарий сам не запустится.'}
            await tools.ask(callback, state, 'gnews_' + action, prompts[action])
            return
        if action == 'poll':
            await callback.answer('Собираю. Это не запускает сценарии.')
            status = {}
            async def work():
                status['result'] = await asyncio.wait_for(general_queue.poll(db.db), timeout=140)
            try:
                await news_scheduler._locked(db.db.news_general_locks, 'manual-collect', work)
            except Exception as exc:
                logger.warning('Сбор общей подборки не завершён: %s', type(exc).__name__)
                return await callback.message.answer('Сбор не завершён. Часть новостей могла сохраниться; проверь список.', parse_mode=None, reply_markup=home_markup())
            if 'result' not in status:
                return await callback.message.answer('Сбор уже идёт. Проверь список позже.', parse_mode=None, reply_markup=home_markup())
            inserted, failed = status['result']
            return await callback.message.answer(f'Добавлено новостей: {inserted}. Недоступно источников: {len(failed)}. Сценарии не запускались.',
                                                  parse_mode=None, reply_markup=home_markup())
        if len(parts) != 4:
            raise ValueError('Неверная кнопка')
        token, index = parts[2], parts[3]
        if action in ('source', 'removesource', 'restoresource'):
            ident = await _selected(callback, token, index, 'source')
            all_sources = await general_sources.list_sources(db.db, include_removed=True)
            source = next((row for row in all_sources if row['id'] == ident), None)
            if not source:
                return await callback.answer('Источник устарел. Открой список заново.', show_alert=True)
            if action == 'removesource':
                await general_sources.remove_source(db.db, ident)
            elif action == 'restoresource':
                await general_sources.add_source(db.db, source['kind'], source['name'], source['location'], source['topic_hint'])
            if action != 'source':
                source = next(row for row in await general_sources.list_sources(db.db, include_removed=True) if row['id'] == ident)
            await callback.answer('Сохранено.' if action != 'source' else '')
            await source_card(callback.message, source, token, index)
            return
        if action in ('item', 'hide', 'restore', 'prepare', 'draft'):
            ident = await _selected(callback, token, index, 'news')
            if not ident:
                ident = await _selected(callback, token, index, 'removed')
            if not ident:
                ident = await _selected(callback, token, index, 'ready')
            item = await db.db.news_general_candidates.find_one({'_id': ident}) if ident else None
            if not item:
                return await callback.answer('Новость устарела. Открой список заново.', show_alert=True)
            if action == 'hide':
                await general_queue.remove_news(db.db, ident)
            elif action == 'restore':
                await general_queue.add_news(db.db, item['url'])
            elif action == 'prepare':
                if item.get('hidden') is True or item.get('stage') != 'collected':
                    return await callback.answer('Новость изменилась. Открой её заново.', show_alert=True)
                if not await owner_trial.drafts_enabled(db.db):
                    return await callback.answer('Включи подготовку сценариев в панели новостей.', show_alert=True)
                await callback.answer()
                await tools.new_confirmation(callback.message, callback.from_user.id, 'general_prepare',
                    'Подготовить сценарий и проверить его другим запросом? Используется квота Gemini и Groq. '
                    'Рендер и публикация не запускаются.\n\n' + _plain(item.get('title'), 180), key=ident)
                return
            elif action == 'draft':
                if item.get('hidden') is True or item.get('owner_id') != callback.from_user.id or not item.get('draft_id'):
                    return await callback.answer('Черновик недоступен.', show_alert=True)
                draft = await owner_drafts.get(db.db, callback.from_user.id, item['draft_id'])
                if not draft or draft.get('general_candidate_id') != ident:
                    return await callback.answer('Черновик не найден; проверь состояние.', show_alert=True)
                await callback.answer()
                from handlers import news_owner_drafts
                await news_owner_drafts.show(tools.proxy(callback.message, callback.from_user), item['draft_id'], item=draft, panel_mode=True)
                return
            await callback.answer('Сохранено.' if action in ('hide', 'restore') else '')
            await news_card(callback.message, await db.db.news_general_candidates.find_one({'_id': ident}), token, index)
            return
        await callback.answer('Неизвестное действие.', show_alert=True)
    except Exception as exc:
        logger.warning('Не удалось управлять общей подборкой: %s', type(exc).__name__)
        try:
            await callback.answer('Не удалось выполнить действие. Открой подборку заново.', show_alert=True)
        except Exception:
            pass


async def prepare_confirmed(message, owner_id: int, key: str):
    """Run once after the existing owner confirmation has been consumed."""
    if owner_id != ADMIN_ID or not await owner_trial.drafts_enabled(db.db):
        return await message.answer('Подготовка сценариев выключена или доступ запрещён.', parse_mode=None)
    from handlers import news_owner_drafts
    from news_prototype import article_fetch, news_image_fetch, story_review
    result = await general_queue.prepare_reserved(db.db, key, owner_id, article_fetch.fetch_article,
        news_owner_drafts.make_draft, news_image_fetch.fetch_thumbnail, news_owner_drafts.analyze_image,
        review=story_review.check)
    candidate = await db.db.news_general_candidates.find_one({'_id': key})
    if candidate and candidate.get('hidden') is True:
        return await message.answer('Новость убрана; начатая обработка могла завершиться. Черновик сохранён отдельно.', parse_mode=None)
    if candidate and candidate.get('draft_id') and result not in ('already_claimed', 'id_conflict', 'uncertain'):
        draft = await owner_drafts.get(db.db, owner_id, candidate['draft_id'])
        if draft and draft.get('general_candidate_id') == key:
            return await news_owner_drafts.show(message, candidate['draft_id'], item=draft, panel_mode=True)
    labels = {'already_claimed': 'Новость уже обрабатывалась. Проверь её состояние; повтор не запущен.',
              'id_conflict': 'Конфликт номера черновика. Сценарий не создавался.',
              'uncertain': 'Результат не подтверждён. Проверь новость и черновики; повтор не запущен.'}
    await message.answer(labels.get(result, 'Состояние не подтверждено; проверь список новостей и черновиков.'), parse_mode=None)
