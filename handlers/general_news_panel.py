"""Owner-only controls for the separate general-news candidates and sources.

The opt-in automation is separate. Explicit polling never generates a script;
preparing one script is a separate one-use confirmation in owner_tools_panel.
"""
from __future__ import annotations

import asyncio
from collections import Counter, deque
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
NEWS_PAGE_SIZE = 4
NEWS_LIMIT = 1000


def _plain(value, limit=80):
    return ' '.join(str(value or '').split())[:limit]


def category_label(topic):
    label = general_editorial.LABELS[topic]
    return label[:1].upper() + label[1:]


def _with_close(markup):
    """Add a close button to navigation screens, never to a pending confirmation."""
    if not isinstance(markup, types.InlineKeyboardMarkup):
        return markup
    rows = [list(row) for row in markup.inline_keyboard]
    if any(button.callback_data == 'owner_panel:close' for row in rows for button in row):
        return markup
    close = types.InlineKeyboardButton(text='Закрыть', callback_data='owner_panel:close')
    if rows and len(rows[-1]) == 1 and rows[-1][0].text.startswith(('⬅️', '↩️', '‹')):
        rows[-1].append(close)
    else:
        rows.append([close])
    return markup.model_copy(update={'inline_keyboard': rows})


async def _edit(message, text, **kwargs):
    if kwargs.get('reply_markup') is not None:
        kwargs['reply_markup'] = _with_close(kwargs['reply_markup'])
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        if 'message is not modified' not in str(exc).lower():
            raise


def home_markup(has_failures=False):
    b = InlineKeyboardBuilder()
    b.button(text='▶️ Собрать и подготовить подборку', callback_data='gnews:runonce')
    b.button(text='📂 Новости по категориям', callback_data='gnews:categories')
    b.button(text='📚 Все новости', callback_data='gnews:news:0')
    b.button(text='📄 Готовые сценарии', callback_data='gnews:ready:0')
    b.button(text='🗂 Источники', callback_data='gnews:sources:0')
    b.button(text='⚙️ Автоотбор', callback_data='gnews:auto')
    b.button(text='🔗 Добавить новость', callback_data='gnews:addnews')
    b.button(text='🔄 Собрать новости сейчас', callback_data='gnews:poll')
    if has_failures:
        b.button(text='⚠️ Недоступные источники', callback_data='gnews:failed:0')
    b.button(text='⬅️ Панель новостей', callback_data='news_panel:home')
    b.adjust(1)
    return b.as_markup()


def run_summary(result):
    labels = {'running': 'в процессе или прерван; проверь готовые сценарии',
              'done': 'завершён', 'no_candidates': 'нет подходящих новостей',
              'budget_exhausted': 'дневной лимит исчерпан', 'busy': 'другой запуск или редактор занят',
              'collect_busy': 'сбор уже идёт', 'collect_failed': 'сбор не завершён',
              'drafts_off': 'подготовка сценариев выключена', 'keys_missing': 'нет ключей',
              'off': 'подготовка недоступна', 'throttled': 'ограничение частоты',
              'timeout_uncertain': 'время обработки истекло; результат не подтверждён',
              'uncertain': 'результат не подтверждён', 'already_claimed': 'новость уже занята'}
    status = labels.get(result.get('status'), 'состояние неизвестно')
    if result.get('status') == 'running':
        return status + '.'
    text = (f'{status}; добавлено {result.get("inserted", 0)}, '
            f'сценариев готово {result.get("ready", 0)} из {result.get("attempts", 0)} завершённых попыток.')
    if result.get('status') == 'collect_failed':
        text += ' Часть новостей могла сохраниться.'
    outcomes = Counter(result.get('outcomes') or [])
    outcome_labels = {'needs_source': 'нужен источник', 'skipped': 'пропущено редактором',
                      'editor_failed': 'ошибка редактора', 'id_conflict': 'конфликт номера',
                      'uncertain': 'не подтверждено', 'timeout_uncertain': 'истекло время',
                      'already_claimed': 'уже обрабатывалось'}
    details = [f'{label}: {outcomes[key]}' for key, label in outcome_labels.items() if outcomes[key]]
    if details:
        text += ' ' + '; '.join(details) + '.'
    return text


async def home(message):
    sources = await general_sources.list_sources(db.db)
    total = await db.db.news_general_candidates.count_documents({'hidden': {'$ne': True}})
    state = await db.db.news_general_state.find_one({'_id': 'last_poll'}) or {}
    automatic = await general_auto.enabled(db.db)
    last_run = await db.db.news_general_state.find_one({'_id': 'one_shot_last'}) or {}
    text = ('📰 Общая подборка\n' + ('🟢 Автоотбор включён.\n' if automatic else '🔴 Автоотбор выключен.\n')
            + f'Источников: {len(sources)} · новостей: {total}.\n'
            'Crypto Sensei остаётся отдельно. Рендер и публикация не запускаются.')
    if last_run.get('at'):
        text += '\nПоследний запуск кнопкой: ' + run_summary(last_run)
    if state.get('at'):
        text += '\nПоследний сбор: ' + state['at'].strftime('%d.%m %H:%M') + ' UTC.'
    if state.get('failed'):
        text += f'\nНедоступных источников: {len(state["failed"])}.'
    await _edit(message, text, reply_markup=home_markup(bool(state.get('failed'))), parse_mode=None, disable_web_page_preview=True)


async def failed_names(ids):
    sources = await general_sources.list_sources(db.db, include_removed=True)
    names = {source['id']: _plain(source['name'], 100) for source in sources}
    return [names.get(source_id, 'Источник больше не в списке') for source_id in ids]


async def failed_screen(message, page):
    state = await db.db.news_general_state.find_one({'_id': 'last_poll'}) or {}
    failed = state.get('failed') or []
    names = await failed_names(failed)
    size = 20
    pages = max(1, (len(names) + size - 1) // size)
    page = max(0, min(page, pages - 1))
    b = InlineKeyboardBuilder()
    if page:
        b.button(text='⬅️ Назад', callback_data=f'gnews:failed:{page - 1}')
    if page + 1 < pages:
        b.button(text='Дальше ➡️', callback_data=f'gnews:failed:{page + 1}')
    b.button(text='🗂 Источники', callback_data='gnews:sources:0')
    b.button(text='⬅️ Общая подборка', callback_data='gnews:home')
    b.adjust(1)
    if names:
        text = (f'⚠️ Недоступны при последнем сборе: {len(names)} · {page + 1}/{pages}\n\n'
                + '\n'.join('• ' + name for name in names[page * size:(page + 1) * size]))
        text += ('\n\nПричина может быть в доступности или формате источника. '
                 'Это не означает, что другие источники обязательно содержали свежие новости.')
    else:
        text = 'При последнем сборе недоступные источники не отмечены.'
    await _edit(message, text, reply_markup=b.as_markup(), parse_mode=None)


async def auto_screen(message):
    automatic = await general_auto.enabled(db.db)
    count = await general_auto.usage(db.db)
    last = await db.db.news_general_state.find_one({'_id': 'auto_last'}) or {}
    b = InlineKeyboardBuilder()
    b.button(text='⏸ Выключить автоотбор' if automatic else '▶️ Включить автоотбор',
             callback_data='gnews:autooff' if automatic else 'gnews:autoon')
    b.button(text='🗂 Категории отбора', callback_data='gnews:catsettings')
    b.button(text='⬅️ Общая подборка', callback_data='gnews:home')
    b.adjust(1)
    disabled = await general_auto.disabled_categories(db.db)
    text = ('⚙️ Автоотбор ' + ('включён' if automatic else 'выключен') + '\n'
            'Сбор раз в 30 минут; подготовка не чаще одного сюжета за 15 минут.\n'
            f'Попыток автоотбора и запуска кнопкой сегодня по МСК: {count} из {general_auto.DAILY_ATTEMPTS}. '
            'Даже неудачная попытка учитывается. Это лимит количества, не денежных расходов.\n'
            'Используется квота Gemini и Groq; рендер и публикация не запускаются. '
            'Уже начатая обработка после выключения может завершиться.\n'
            f'Категорий включено: {len(general_editorial.LABELS) - len(disabled)} из {len(general_editorial.LABELS)}.\n'
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


async def _session(message, owner_id, kind, ids, **context):
    now = datetime.now(timezone.utc)
    token = secrets.token_hex(8)
    await db.db.news_general_panel_sessions.delete_many({'expires_at': {'$lt': now}})
    await db.db.news_general_panel_sessions.insert_one({'_id': token, 'owner_id': owner_id,
        'message_id': message.message_id, 'kind': kind, 'ids': ids,
        'expires_at': now + timedelta(hours=2), **context})
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
    b.button(text='🏷 Категория источника', callback_data=f'gnews:scat:{token}:{index}')
    page = int(index) // PAGE_SIZE
    b.button(text='⬅️ Источники', callback_data=f'gnews:sources:{page}')
    b.adjust(1)
    label = 'Telegram' if source['kind'] == 'telegram' else 'RSS'
    await _edit(message, f'{_plain(source["name"], 100)} · {label}\n{_plain(source["location"], 500)}\n'
        + ('Активен.' if source['enabled'] else 'Убран из будущего сбора.') +
        f'\nКатегория по умолчанию: {general_editorial.LABELS.get(source.get("topic_hint"), "другое")}. Используется для новостей источника, если заголовок не распознан.' +
        '\nУже начавшийся сбор может завершиться; готовые сценарии не удаляются.',
        reply_markup=b.as_markup(), parse_mode=None, disable_web_page_preview=True)


def balanced_items(items):
    """Spread provisional topics and sources without discarding any news."""
    remaining = [(item, general_editorial.assess_item(item))
                 for item in items]
    topic_counts, source_counts = Counter(), Counter()
    result = []
    while remaining:
        def score(index):
            item, assessment = remaining[index]
            topic = general_editorial.choose_topic(assessment, topic_counts)
            return topic_counts[topic], source_counts[item.get('source_id', '')], index
        index = min(range(len(remaining)), key=score)
        item, assessment = remaining.pop(index)
        topic = general_editorial.choose_topic(assessment, topic_counts)
        result.append((item, topic))
        topic_counts[topic] += 1
        source_counts[item.get('source_id', '')] += 1
    return result


def short_headline(value, limit=130):
    text = _plain(value, 1000) or 'Новость по ссылке'
    if len(text) <= limit:
        return text
    prefix = text[:limit].rsplit(' ', 1)[0].rstrip(' ,;:—-')
    return (prefix or text[:limit]) + '…'


async def news_list(message, owner_id, page, kind='news'):
    query = {'hidden': True} if kind == 'removed' else {'hidden': {'$ne': True}}
    chronological = kind in ('all', 'removed')
    order = [('first_seen_at', -1), ('published_at', -1), ('_id', -1)]
    if chronological:
        total = await db.db.news_general_candidates.count_documents(query)
        pages = max(1, (total + NEWS_PAGE_SIZE - 1) // NEWS_PAGE_SIZE)
        page = max(0, min(page, pages - 1))
        items = await db.db.news_general_candidates.find(query).sort(order).skip(
            page * NEWS_PAGE_SIZE).limit(NEWS_PAGE_SIZE).to_list(length=NEWS_PAGE_SIZE)
        visible = [(item, None) for item in items]
    else:
        if kind == 'ready':
            query['stage'] = 'draft_ready'
        items = await db.db.news_general_candidates.find(query).sort(order).limit(NEWS_LIMIT).to_list(length=NEWS_LIMIT)
        limited = len(items) == NEWS_LIMIT
        if kind == 'ready':
            numbers = [item['draft_id'] for item in items
                       if item.get('owner_id') == owner_id and isinstance(item.get('draft_id'), int)]
            drafts = await db.db.news_owner_drafts.find({'owner_id': owner_id, 'stage': 'draft_ready',
                'draft_id': {'$in': numbers}}, {'draft_id': 1, 'general_candidate_id': 1}).to_list(length=NEWS_LIMIT)
            current = {(draft.get('draft_id'), draft.get('general_candidate_id')) for draft in drafts}
            items = [item for item in items if (item.get('draft_id'), item['_id']) in current]
        ordered = balanced_items(items)
        total = len(ordered)
        pages = max(1, (total + NEWS_PAGE_SIZE - 1) // NEWS_PAGE_SIZE)
        page = max(0, min(page, pages - 1))
        visible = ordered[page * NEWS_PAGE_SIZE:(page + 1) * NEWS_PAGE_SIZE]
    # Store only this page, so every button stays bound to the displayed item
    # even when collection or ranking changes before it is clicked.
    token = await _session(message, owner_id, kind, [item['_id'] for item, _ in visible], page=page)
    title = {'removed': 'Убранные новости', 'ready': 'Готовые сценарии',
             'news': 'Новости общей подборки', 'all': 'Все новости по дате сбора'}[kind]
    lines = [f'📚 {title} · {total} · {page + 1}/{pages}', '']
    b = InlineKeyboardBuilder()
    for index, (item, topic) in enumerate(visible):
        number = page * NEWS_PAGE_SIZE + index + 1
        source = _plain(item.get('source'), 45) or 'Источник не указан'
        label = general_editorial.LABELS.get(topic) if topic else None
        lines.append(f'{number}. {short_headline(item.get("title"))}\n   {source}'
                     + (f' · {label}' if label else ''))
        b.button(text=str(number), callback_data=f'gnews:item:{token}:{index}')
    if visible:
        b.adjust(len(visible))
    else:
        lines.append('Пока ничего нет.')
    nav = InlineKeyboardBuilder()
    if page:
        nav.button(text='⬅️ Назад', callback_data=f'gnews:{kind}:{page - 1}')
    if page + 1 < pages:
        nav.button(text='Дальше ➡️', callback_data=f'gnews:{kind}:{page + 1}')
    if page or page + 1 < pages:
        nav.adjust(2)
        b.attach(nav)
    actions = InlineKeyboardBuilder()
    if kind != 'removed':
        actions.button(text='🕒 Все по дате' if kind != 'all' else '📚 Разнообразная подборка',
                       callback_data='gnews:all:0' if kind != 'all' else 'gnews:news:0')
    actions.button(text='↩️ Убранные новости' if kind != 'removed' else '📚 К новостям',
                   callback_data='gnews:removed:0' if kind != 'removed' else 'gnews:news:0')
    actions.button(text='🔗 Добавить новость', callback_data='gnews:addnews')
    actions.button(text='⬅️ Общая подборка', callback_data='gnews:home')
    actions.adjust(1)
    b.attach(actions)
    lines.append('\nНажми номер, чтобы открыть новость.' +
                 (' Темы определяются приблизительно.' if not chronological else ''))
    if not chronological and limited:
        lines.append(f'Подборка ограничена {NEWS_LIMIT} последними записями. Все сохранённые новости — в «Все по дате».')
    await _edit(message, '\n\n'.join(lines), reply_markup=b.as_markup(), parse_mode=None)


async def categories_screen(message):
    b = InlineKeyboardBuilder()
    for topic, label in general_editorial.LABELS.items():
        b.button(text=category_label(topic), callback_data=f'gnews:cat:{topic}:0')
    b.adjust(2)
    actions = InlineKeyboardBuilder()
    actions.button(text='📚 Все новости', callback_data='gnews:news:0')
    actions.button(text='⚙️ Категории отбора', callback_data='gnews:catsettings')
    actions.button(text='⬅️ Общая подборка', callback_data='gnews:home')
    actions.adjust(1)
    b.attach(actions)
    await _edit(message, '📂 Выбери категорию новостей.\nКатегория определяется приблизительно; её можно исправить в карточке.\nЗдесь выбор не запускает подготовку сценария.',
                reply_markup=b.as_markup(), parse_mode=None)


async def category_settings(message):
    disabled = await general_auto.disabled_categories(db.db)
    b = InlineKeyboardBuilder()
    for topic, label in general_editorial.LABELS.items():
        active = topic not in disabled
        b.button(text=('✅ ' if active else '⛔️ ') + category_label(topic),
                 callback_data=f'gnews:ctoggle:{topic}:{0 if active else 1}')
    b.adjust(2)
    actions = InlineKeyboardBuilder()
    actions.button(text='✅ Включить все', callback_data='gnews:call:1')
    actions.button(text='⛔️ Выключить все', callback_data='gnews:call:0')
    actions.button(text='⬅️ Автоотбор', callback_data='gnews:auto')
    actions.button(text='📂 К категориям', callback_data='gnews:categories')
    actions.adjust(1)
    b.attach(actions)
    await _edit(message, '⚙️ Категории отбора\n✅ — включена, ⛔️ — выключена. Нажми для изменения.\n'
        'Сбор и ручной просмотр сохраняют все категории. Выключение не отменяет уже начатую подготовку. '
        'Многотемная новость может попасть по другой включённой категории. '
        'Определение темы приблизительное; её можно исправить вручную.', reply_markup=b.as_markup(), parse_mode=None)


async def category_list(message, owner_id, topic, page):
    if topic not in general_editorial.LABELS:
        raise ValueError('Неверная категория')
    # Scan all saved records, not only a recent general-news window: a noisy
    # category must not crowd older entries out of another category's view.
    cursor = db.db.news_general_candidates.find({'hidden': {'$ne': True}},
        {'title': 1, 'source': 1, 'topic_hint': 1, 'category_override': 1}).sort(
        [('first_seen_at', -1), ('published_at', -1), ('_id', -1)])
    count = 0
    visible = []
    last = deque(maxlen=NEWS_PAGE_SIZE)
    async for item in cursor:
        if topic not in general_editorial.assess_item(item)['topics']:
            continue
        if page * NEWS_PAGE_SIZE <= count < (page + 1) * NEWS_PAGE_SIZE:
            visible.append(item)
        last.append(item)
        count += 1
    pages = max(1, (count + NEWS_PAGE_SIZE - 1) // NEWS_PAGE_SIZE)
    if page >= pages:
        page = pages - 1
        visible = list(last)[-(count - page * NEWS_PAGE_SIZE):] if count else []
    token = await _session(message, owner_id, 'category', [item['_id'] for item in visible], category=topic, page=page)
    lines = [f'📂 {category_label(topic)} · {count} · {page + 1}/{pages}', '']
    b = InlineKeyboardBuilder()
    for index, item in enumerate(visible):
        number = page * NEWS_PAGE_SIZE + index + 1
        lines.append(f'{number}. {short_headline(item.get("title"))}\n   {_plain(item.get("source"), 45) or "Источник не указан"}')
        b.button(text=str(number), callback_data=f'gnews:item:{token}:{index}')
    if visible:
        b.adjust(len(visible))
    else:
        lines.append('Пока нет новостей этой категории. Проверь источники и время последнего сбора.')
    nav = InlineKeyboardBuilder()
    if page:
        nav.button(text='⬅️ Назад', callback_data=f'gnews:cat:{topic}:{page - 1}')
    if page + 1 < pages:
        nav.button(text='Дальше ➡️', callback_data=f'gnews:cat:{topic}:{page + 1}')
    if page or page + 1 < pages:
        nav.adjust(2)
        b.attach(nav)
    actions = InlineKeyboardBuilder()
    actions.button(text='📂 Все категории', callback_data='gnews:categories')
    actions.button(text='📚 Все новости', callback_data='gnews:all:0')
    actions.button(text='⬅️ Общая подборка', callback_data='gnews:home')
    actions.adjust(1)
    b.attach(actions)
    lines.append('Нажми номер, чтобы открыть новость. Категории могут определяться неточно.')
    await _edit(message, '\n\n'.join(lines), reply_markup=b.as_markup(), parse_mode=None)


async def category_picker(message, token, index, source=False):
    b = InlineKeyboardBuilder()
    action = 'sassign' if source else 'nassign'
    for topic, label in general_editorial.LABELS.items():
        b.button(text=category_label(topic), callback_data=f'gnews:{action}:{token}:{index}:{topic}')
    if not source:
        b.button(text='↩️ Определять автоматически', callback_data=f'gnews:{action}:{token}:{index}:reset')
    b.adjust(2)
    b.row(types.InlineKeyboardButton(text='⬅️ Назад', callback_data=f'gnews:{"source" if source else "item"}:{token}:{index}'))
    text = ('Выбери категорию по умолчанию для новостей источника. Она используется, если тема заголовка не распознана.'
            if source else 'Выбери категорию этой новости или верни автоматическое определение. Это влияет и на автоотбор.')
    await _edit(message, '🏷 ' + text, reply_markup=b.as_markup(), parse_mode=None)


async def selected_news(callback, token, index):
    for kind in ('news', 'all', 'removed', 'ready', 'category'):
        ident = await _selected(callback, token, index, kind)
        if ident:
            return ident
    return None


async def news_card(message, item, token, index):
    hidden = item.get('hidden') is True
    stages = {'collected': 'ожидает подготовки', 'reserved': 'обрабатывается или нужна проверка состояния',
              'draft_ready': 'сценарий сохранён', 'needs_source': 'нужен текст источника',
              'skipped': 'редактор пропустил', 'editor_failed': 'не удалось подготовить', 'id_conflict': 'конфликт номера'}
    text = (f'📰 {_plain(item.get("title"), 180)}\nИсточник: {_plain(item.get("source"), 100)}\n'
            f'Состояние: {stages.get(item.get("stage"), "неизвестно")}.'
            + ('\nНовость убрана из общего списка.' if hidden else ''))
    assessment = general_editorial.assess_item(item)
    topics = ', '.join(general_editorial.LABELS.get(t, t) for t in assessment['topics'])
    text += ('\nКатегория выбрана вручную: ' + topics + '.' if assessment['basis'] == 'owner'
             else '\nВозможные темы: ' + topics + ' (предварительно).')
    if item.get('stage') == 'collected':
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
    b.button(text='🏷 Изменить категорию', callback_data=f'gnews:ncat:{token}:{index}')
    session = await db.db.news_general_panel_sessions.find_one({'_id': token}) or {}
    if session.get('kind') == 'category' and session.get('category') in general_editorial.LABELS:
        back = f"gnews:cat:{session['category']}:{session.get('page', 0)}"
    elif session.get('kind') in ('news', 'all', 'removed', 'ready'):
        back = f"gnews:{session['kind']}:{session.get('page', 0)}"
    else:
        back = 'gnews:removed:0' if hidden else 'gnews:news:0'
    b.button(text='⬅️ Новости', callback_data=back)
    b.adjust(1)
    await _edit(message, text, reply_markup=b.as_markup(), parse_mode=None, disable_web_page_preview=True)


@router.callback_query(F.data.startswith('gnews:'))
async def callback_action(callback: types.CallbackQuery, state: FSMContext):
    if not callback.message or not tools.authorized(callback.from_user, callback.message.chat):
        return await callback.answer('Только для владельца в личке.', show_alert=True)
    parts = (callback.data or '').split(':')
    action = parts[1] if len(parts) > 1 else ''
    try:
        if action in ('home', 'auto', 'categories', 'catsettings', 'failed', 'sources', 'news', 'all', 'removed', 'ready'):
            await state.clear()
            page = 0
            if action not in ('home', 'auto', 'categories', 'catsettings'):
                if len(parts) != 3 or not parts[2].isascii() or not parts[2].isdigit() or len(parts[2]) > 9:
                    raise ValueError('Неверная страница')
                page = int(parts[2])
            await callback.answer()
            if action == 'home':
                await home(callback.message)
            elif action == 'auto':
                await auto_screen(callback.message)
            elif action == 'categories':
                await categories_screen(callback.message)
            elif action == 'catsettings':
                await category_settings(callback.message)
            elif action == 'failed':
                await failed_screen(callback.message, page)
            elif action == 'sources':
                await source_list(callback.message, callback.from_user.id, page)
            else:
                await news_list(callback.message, callback.from_user.id, page, action)
            return
        if action == 'call':
            if len(parts) != 3 or parts[2] not in ('0', '1'):
                raise ValueError('Неверная настройка')
            await general_auto.set_all_categories_enabled(db.db, parts[2] == '1')
            await callback.answer('Настройка сохранена.')
            return await category_settings(callback.message)
        if action in ('cat', 'ctoggle'):
            if len(parts) != 4 or parts[2] not in general_editorial.LABELS:
                raise ValueError('Неверная категория')
            topic, value = parts[2:]
            if action == 'cat':
                if not value.isascii() or not value.isdigit() or len(value) > 9:
                    raise ValueError('Неверная страница')
                await callback.answer()
                return await category_list(callback.message, callback.from_user.id, topic, int(value))
            if value not in ('0', '1'):
                raise ValueError('Неверное действие')
            await general_auto.set_category_enabled(db.db, topic, value == '1')
            await callback.answer('Настройка сохранена.')
            return await category_settings(callback.message)
        if action in ('nassign', 'sassign'):
            if len(parts) != 5:
                raise ValueError('Неверная кнопка')
            token, index, topic = parts[2:]
            if topic not in general_editorial.LABELS and not (action == 'nassign' and topic == 'reset'):
                raise ValueError('Неверная категория')
            if action == 'sassign':
                ident = await _selected(callback, token, index, 'source')
                if not ident or not await general_sources.set_source_topic(db.db, ident, topic):
                    return await callback.answer('Источник устарел. Открой список заново.', show_alert=True)
                source = next(s for s in await general_sources.list_sources(db.db, include_removed=True) if s['id'] == ident)
                await callback.answer('Категория сохранена.')
                return await source_card(callback.message, source, token, index)
            ident = await selected_news(callback, token, index)
            if not ident:
                return await callback.answer('Новость устарела. Открой список заново.', show_alert=True)
            change = {'$unset': {'category_override': ''}} if topic == 'reset' else {'$set': {'category_override': topic}}
            result = await db.db.news_general_candidates.update_one({'_id': ident}, change)
            if not result.matched_count:
                return await callback.answer('Новость устарела. Открой список заново.', show_alert=True)
            await callback.answer('Категория сохранена.')
            return await news_card(callback.message, await db.db.news_general_candidates.find_one({'_id': ident}), token, index)
        if action == 'runonce':
            if not await owner_trial.drafts_enabled(db.db):
                return await callback.answer('Сначала включи подготовку сценариев в панели новостей.', show_alert=True)
            if not general_auto.keys_ready():
                return await callback.answer('Нужны настроенные ключи Gemini и Groq.', show_alert=True)
            await callback.answer()
            await tools.new_confirmation(callback.message, callback.from_user.id, 'general_run_once',
                'Собрать свежие новости и подготовить до ' + str(general_auto.ONE_SHOT_ATTEMPTS) +
                ' сценариев по включённым категориям с отдельной проверкой? '
                'Расходуется квота Gemini и Groq; все попытки (включая ошибки) входят в общий лимит '
                f'{general_auto.DAILY_ATTEMPTS} за сутки по МСК вместе с автоотбором. Это не денежный лимит. '
                'Автоотбор не включится, рендер и публикация не запустятся.')
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
                f'Общий лимит — {general_auto.DAILY_ATTEMPTS} попыток в сутки по МСК для автомата и запуска кнопкой, включая неудачные. '
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
            await _edit(callback.message, '🔄 Собираю новости. Сценарии и видео не запускаются.',
                        reply_markup=None, parse_mode=None)
            status = {}
            async def work():
                status['result'] = await asyncio.wait_for(general_queue.poll(db.db), timeout=140)
            try:
                await news_scheduler._locked(db.db.news_general_locks, 'manual-collect', work)
            except Exception as exc:
                logger.warning('Сбор общей подборки не завершён: %s', type(exc).__name__)
                return await _edit(callback.message,
                    'Сбор не завершён. Часть новостей могла сохраниться; проверь список.',
                    parse_mode=None, reply_markup=home_markup())
            if 'result' not in status:
                return await _edit(callback.message, 'Сбор уже идёт. Проверь список позже.',
                                   parse_mode=None, reply_markup=home_markup())
            inserted, failed = status['result']
            text = f'✅ Добавлено новостей: {inserted}.\nНедоступно источников: {len(failed)}.'
            if failed:
                names = await failed_names(failed)
                text += '\n' + ', '.join(names[:3]) + (' и другие.' if len(names) > 3 else '.')
            text += '\nСценарии и видео не запускались.'
            return await _edit(callback.message, text, parse_mode=None, reply_markup=home_markup(bool(failed)))
        if len(parts) != 4:
            raise ValueError('Неверная кнопка')
        token, index = parts[2], parts[3]
        if action in ('source', 'removesource', 'restoresource', 'scat'):
            ident = await _selected(callback, token, index, 'source')
            all_sources = await general_sources.list_sources(db.db, include_removed=True)
            source = next((row for row in all_sources if row['id'] == ident), None)
            if not source:
                return await callback.answer('Источник устарел. Открой список заново.', show_alert=True)
            if action == 'scat':
                await callback.answer()
                return await category_picker(callback.message, token, index, source=True)
            if action == 'removesource':
                await general_sources.remove_source(db.db, ident)
            elif action == 'restoresource':
                await general_sources.add_source(db.db, source['kind'], source['name'], source['location'], source['topic_hint'])
            if action != 'source':
                source = next(row for row in await general_sources.list_sources(db.db, include_removed=True) if row['id'] == ident)
            await callback.answer('Сохранено.' if action != 'source' else '')
            await source_card(callback.message, source, token, index)
            return
        if action in ('item', 'hide', 'restore', 'prepare', 'draft', 'ncat'):
            ident = await selected_news(callback, token, index)
            item = await db.db.news_general_candidates.find_one({'_id': ident}) if ident else None
            if not item:
                return await callback.answer('Новость устарела. Открой список заново.', show_alert=True)
            if action == 'ncat':
                await callback.answer()
                return await category_picker(callback.message, token, index)
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


async def run_once_confirmed(message, owner_id):
    if owner_id != ADMIN_ID:
        return await _edit(message, 'Доступ запрещён.', reply_markup=home_markup(), parse_mode=None)
    await _edit(message, '🔄 Собираю новости и готовлю сценарии. Это может занять несколько минут. '
                'Проверить результат можно в общей подборке; повторять запуск вслепую не нужно.',
                reply_markup=None, parse_mode=None)
    result = await general_auto.run_once(db.db, owner_id)
    text = '📰 Запуск подборки: ' + run_summary(result)
    failed = result.get('failed') or []
    if failed:
        text += f'\nОшибок источников: {len(failed)}.'
    if result.get('status') in ('uncertain', 'timeout_uncertain', 'running'):
        text += '\nПроверь карточки и готовые сценарии, прежде чем запускать снова.'
    text += '\nРендер и публикация не запускались. Результат проверки — в карточке сценария.'
    await _edit(message, text, parse_mode=None, reply_markup=home_markup(bool(failed)))
