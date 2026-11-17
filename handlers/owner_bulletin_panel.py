"""Owner-only bulletin selection, review and explicitly confirmed rendering."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import secrets

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import db
from handlers import owner_tools_panel as tools
from news_prototype import (article_fetch, editor_client, news_image_fetch, owner_bulletin,
                            owner_bulletin_sources, owner_bulletin_admission, owner_daily, owner_drafts, owner_program, owner_trial)
from news_prototype.news_image_codec import decode_thumbnail

router = Router()
logger = logging.getLogger(__name__)
PAGE_SIZE = 8


async def make_bulletin(stories):
    from ai_providers import fire_gemini_key
    from models import GEMINI_CHAT_MODEL
    return await editor_client.bulletin(stories, fire_gemini_key(), GEMINI_CHAT_MODEL)


async def selection(message, session):
    items, selected = session['items'], session['selected']
    pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(0, session['page']), pages - 1)
    b = InlineKeyboardBuilder()
    for item in items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
        number = item['number']
        b.button(text=('✅ ' if number in selected else '▫️ ') + item['label'][:65],
                 callback_data=f'nb:sel:{session["_id"]}:{number}')
    b.adjust(1)
    if session.get('mode') == 'direct':
        links = InlineKeyboardBuilder()
        for item in items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
            links.button(text=f'🔗 Источник {item["number"]}', url=item['url'])
        if items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
            links.adjust(2); b.attach(links)
    nav = InlineKeyboardBuilder()
    if page:
        nav.button(text='⬅️', callback_data=f'nb:page:{session["_id"]}:{page - 1}')
    if page + 1 < pages:
        nav.button(text='➡️', callback_data=f'nb:page:{session["_id"]}:{page + 1}')
    if page or page + 1 < pages:
        nav.adjust(2); b.attach(nav)
    actions = InlineKeyboardBuilder()
    if len(selected) >= 2:
        actions.button(text=('📥 Прочитать выбранные источники' if session.get('mode') == 'direct' else
                             '📝 Подготовить один вестник'), callback_data=f'nb:prepare:{session["_id"]}')
    actions.button(text='📰 Панель новостей', callback_data='news_panel:home')
    actions.adjust(1); b.attach(actions)
    description = ('Показаны собранные новости за последние сутки. Сначала можно отдельно подтвердить чтение источников, '
                   'затем — один общий запрос редактору; отдельные сюжеты не создаются.'
                   if session.get('mode') == 'direct' else
                   'Показаны до 100 готовых или подтверждённо доставленных черновиков. '
                   'Подготовка общего сценария требует отдельного подтверждения и одного запроса к Gemini; '
                   'отдельные черновики уже могли использовать квоту при создании.')
    await message.edit_text(
        f'📰 Выбор новостей для вестника · {page + 1}/{pages}\n'
        f'Выбрано {len(selected)} из 20, порядок — по нажатию. ' + description + ' Видео не запускается.',
        reply_markup=b.as_markup(), parse_mode=None)


def _chunks(text: str, limit: int = 3600):
    chunk, units = [], 0
    for char in text:
        length = 2 if ord(char) > 0xffff else 1
        if units + length > limit:
            yield ''.join(chunk)
            chunk, units = [], 0
        chunk.append(char); units += length
    if chunk:
        yield ''.join(chunk)


async def show_section(message, owner_id: int, item: dict, section: dict):
    position = next(i for i, part in enumerate(item['sections'], 1) if part['id'] == section['id'])
    lines = [f'📰 Новость {position}/{len(item["sections"])} · №{section["id"]}',
             'Источник: ' + section['url'], 'Строка: ' + section['ticker'], '']
    for turn in section['turns']:
        lines.append(f'{turn["speaker"]}: {turn["text"]}')
        if turn.get('kind') == 'fact':
            lines.append('Цитата источника для проверки: ' + turn['evidence'])
        elif turn.get('kind') == 'owner_edit':
            lines.append('Изменено владельцем — проверь факт и формулировку по источнику.')
        else:
            lines.append('Комментарий/шутка — не факт.')
    for part in _chunks('\n'.join(lines)):
        await message.answer(part, parse_mode=None, disable_web_page_preview=True)
    if section.get('image_b64'):
        await message.answer_photo(types.BufferedInputFile(decode_thumbnail(section['image_b64']), filename='source.jpg'),
            caption='Картинка для этой новости. Проверь, что она соответствует источнику.', parse_mode=None)
    else:
        await message.answer('Картинки нет: на экране будет нейтральный фон.', parse_mode=None)
    source = next((story for story in item.get('stories', []) if story.get('draft_id') == section['id']), None)
    video_status = source.get('telegram_video_status') if source else None
    if section.get('video'):
        await message.answer('Видео выбрано для этой версии. Оно появится на экране студии, затем примерно на 10 секунд '
                             'развернётся со своим звуком после законченной новости. Проверь права на видео и звук.', parse_mode=None)
    elif video_status == 'available':
        hint = ('Сначала посмотри исходник, затем отдельно подтверди его использование и права на видео и звук.'
                if item.get('mode') == 'direct' else 'Выбор видео доступен только при прямом выборе новостей.')
        await message.answer('В этом посте обнаружено видео. Эта версия его не использует. ' + hint +
                             ' Иначе останется картинка или нейтральный экран.', parse_mode=None)
    elif video_status == 'ambiguous':
        await message.answer('Видео поста найдено неоднозначно; оно не используется. '
                             'Проверь источник отдельно.', parse_mode=None)
    token = secrets.token_hex(8)
    await db.db.owner_bulletin_cards.insert_one({'_id': token, 'owner_id': owner_id,
        'bulletin_id': item['bulletin_id'], 'section_id': section['id'], 'revision': item['revision'],
        'expires_at': datetime.now(timezone.utc) + timedelta(days=2)})
    b = InlineKeyboardBuilder()
    b.button(text='✏️ Исправить эту новость', callback_data=f'nb:edit:{token}')
    if video_status == 'available' and item.get('mode') == 'direct':
        from news_prototype import owner_bulletin_video_review
        if section.get('video'):
            b.button(text='❌ Убрать видео из вестника', callback_data=f'nb:novideo:{token}')
        else:
            b.button(text='📼 Проверить исходное видео', callback_data=f'nb:video:{token}')
            review_id = owner_bulletin_video_review.identity(owner_id, item['bulletin_id'], item['revision'], section['id'])
            reviewed = await db.db.news_owner_bulletin_video_reviews.find_one({'_id': review_id, 'stage': 'sent'})
            if reviewed:
                b.button(text='✅ Выбрать просмотренное видео', callback_data=f'nb:usevideo:{token}')
    if section.get('image_b64'):
        b.button(text='🖼 Убрать эту картинку', callback_data=f'nb:noimage:{token}')
    b.button(text='📋 Все вестники', callback_data='nb:list')
    b.adjust(1)
    sent = await message.answer('Действия с этой версией новости:', reply_markup=b.as_markup(), parse_mode=None)
    await db.db.owner_bulletin_cards.update_one({'_id': token}, {'$set': {'message_id': sent.message_id}})


async def show_sources(message, item: dict):
    stage = item.get('stage')
    if stage == 'source_missing':
        await message.answer('Не удалось получить достаточно текста для некоторых новостей. '
                             'Пришли текст для каждой из них; редактор пока не запускался.', parse_mode=None)
        for story in item['stories']:
            if story['draft_id'] not in item['missing_ids']:
                continue
            b = InlineKeyboardBuilder()
            b.button(text='📄 Прислать текст', callback_data=f'nb:source:{item["bulletin_id"]}:{story["draft_id"]}')
            await message.answer(f'Новость {story["draft_id"]}: {story["title"]}\nИсточник: {story["url"]}',
                                 reply_markup=b.as_markup(), parse_mode=None, disable_web_page_preview=True)
    elif stage == 'new':
        b = InlineKeyboardBuilder()
        b.button(text='📝 Подготовить общий сценарий', callback_data=f'nb:editor:{item["bulletin_id"]}')
        await message.answer('Все источники собраны. Картинки, если нашлись, будут показаны вместе со сценарием. '
                             'Следующий шаг требует отдельного подтверждения запроса к Gemini.',
                             reply_markup=b.as_markup(), parse_mode=None)
    else:
        await show_review(message, item['owner_id'], item)


async def collect_selected(message, owner_id: int, data: dict):
    if not await owner_trial.drafts_enabled(db.db):
        return await message.answer('Подготовка сценариев выключена; источники не запрашивались.', parse_mode=None)
    number = message.message_id
    try:
        await owner_bulletin_sources.create(db.db, owner_id, number, data['choices'])
        result = await owner_bulletin_sources.collect(db.db, owner_id, number,
                                                       article_fetch.fetch_article, news_image_fetch.fetch_thumbnail)
    except ValueError:
        return await message.answer('Выбранные ссылки недопустимы; источники не запрашивались.', parse_mode=None)
    item = await owner_bulletin.get(db.db, owner_id, number)
    if result in ('new', 'source_missing'):
        await show_sources(message, item)
    else:
        await message.answer(f'Состояние вестника №{number}: {result}. Проверь список вестников; '
                             'автоматического повтора нет.', parse_mode=None)


async def run_editor(message, owner_id: int, data: dict):
    if not await owner_trial.drafts_enabled(db.db):
        return await message.answer('Подготовка сценариев выключена; редактор не запускался.', parse_mode=None)
    item = await owner_bulletin.get(db.db, owner_id, data['number'])
    if (not item or item.get('mode') != 'direct' or item.get('stage') != 'new' or
            item.get('source_revision') != data['source_revision'] or
            not await owner_bulletin._sources_current(db.db, owner_id, item)):
        return await message.answer('Источники или состояние изменились; редактор не запускался.', parse_mode=None)
    result = await owner_bulletin.prepare(db.db, owner_id, data['number'], make_bulletin)
    item = await owner_bulletin.get(db.db, owner_id, data['number'])
    if result == 'ready_for_review':
        return await show_review(message, owner_id, item)
    if result == 'needs_source':
        return await message.answer('Редактору недостаточно данных: новости ' + ', '.join(map(str, item['missing_ids'])) +
                                    '. Для нового запроса потребуется создать новый вестник и подтвердить его.', parse_mode=None)
    return await message.answer(f'Состояние вестника №{data["number"]}: {result}. '
                                'Автоматического повтора запроса нет; проверь список вестников.', parse_mode=None)


async def action_card(message, owner_id: int, item: dict, action: str, label: str):
    token = secrets.token_hex(8)
    await db.db.owner_bulletin_actions.insert_one({'_id': token, 'owner_id': owner_id,
        'bulletin_id': item['bulletin_id'], 'revision': item['revision'], 'action': action,
        'expires_at': datetime.now(timezone.utc) + timedelta(days=2)})
    b = InlineKeyboardBuilder()
    b.button(text=label, callback_data=f'nb:{action}:{token}')
    b.button(text='📋 Все вестники', callback_data='nb:list'); b.adjust(1)
    sent = await message.answer('Действие для этой версии:', reply_markup=b.as_markup(), parse_mode=None)
    await db.db.owner_bulletin_actions.update_one({'_id': token}, {'$set': {'message_id': sent.message_id}})


async def render_confirmed(message, owner_id: int, data: dict):
    if not await owner_trial.runtime_enabled(db.db):
        return await message.answer('Пробный режим выключен; вестник не поставлен в очередь.', parse_mode=None)
    result = await owner_bulletin_admission.approve(db.db, owner_id, data['number'], data['revision'])
    labels = {
        'queued': 'Вестник поставлен в очередь. Озвучка использует квоту; видео придёт только владельцу, публикации не будет.',
        'stale': 'Версия или источник изменились. Открой вестник заново; рендер не запущен.',
        'revision_used': 'Эта версия уже использовалась. Сначала сократи или измени сценарий и проверь его заново.',
        'trial_busy': 'Слот занят другой пробой. Проверь её состояние; этот вестник не поставлен в очередь.',
        'disabled': 'Пробный режим выключен; вестник не поставлен в очередь.',
        'not_ready': 'Вестник не готов. Открой его заново.',
        'changed': 'Версия уже изменилась или обрабатывается. Проверь состояние вестника.',
        'already_processed': 'Подтверждение уже обработано. Проверь состояние вестника.',
        'approval_uncertain': 'Не удалось подтвердить сохранение. Проверь состояние вестника и слота; не запускай повторно вслепую.',
    }
    await message.answer(labels.get(result, 'Состояние не подтверждено; проверь вестник.'), parse_mode=None)


async def video_review_confirmed(message, owner_id: int, data: dict, bot):
    """Only the one-use owner confirmation may start the preview delivery."""
    from news_prototype import owner_bulletin_video_review
    if owner_id != tools.ADMIN_ID or not tools.authorized(message.from_user, message.chat):
        return await message.answer('Доступно только владельцу в личке.', parse_mode=None)

    async def send(owner, checked_bytes, caption):
        result = await bot.send_video(owner, types.BufferedInputFile(checked_bytes, filename='source.mp4'),
                                      caption=caption, parse_mode=None)
        return result.message_id

    result = await owner_bulletin_video_review.deliver(db.db, owner_id, data['number'], data['revision'],
                                                       data['section_id'], send)
    labels = {
        'sent': 'Исходное видео отправлено в личку для проверки. Оно не добавлено в выпуск и не считается одобренным.',
        'stale': 'Версия или источник изменились. Открой вестник заново; видео не отправлено.',
        'already_started': 'Для этой версии и новости проверка уже запускалась. Проверь личку; не повторяй вслепую.',
        'failed': 'Не удалось подготовить видео. Автоматического повтора нет; проверь источник и состояние.',
        'uncertain': 'Отправка не подтверждена. Проверь личку; не повторяй вслепую.',
    }
    await message.answer(labels.get(result, 'Результат не подтверждён; проверь личку.'), parse_mode=None)


async def use_video_confirmed(message, owner_id: int, data: dict):
    from news_prototype import owner_bulletin_video_review
    if owner_id != tools.ADMIN_ID or not tools.authorized(message.from_user, message.chat):
        return await message.answer('Доступно только владельцу в личке.', parse_mode=None)
    result = await owner_bulletin_video_review.approve_for_bulletin(
        db.db, owner_id, data['number'], data['revision'], data['section_id'])
    if result == 'selected':
        item = await owner_bulletin.get(db.db, owner_id, data['number'])
        await message.answer('Видео выбрано. Сценарий стал новой версией: проверь выпуск заново. '
                             'Рендер не запускался; он потребует отдельного подтверждения.', parse_mode=None)
        return await show_review(message, owner_id, item)
    return await message.answer('Видео не выбрано: исходник не был подтверждён или версия изменилась. '
                                'В одном выпуске пока поддерживается одно видео. Открой вестник заново.', parse_mode=None)


async def shorten_confirmed(message, owner_id: int, data: dict):
    result = await owner_bulletin_admission.reopen_too_long(db.db, owner_id, data['number'], data['revision'])
    if result == 'reopened':
        await message.answer('Слот освобождён. Сократи реплики, проверь все новости и отдельно подтверди новый рендер. '
                             'Прежняя версия не будет повторно озвучена.', parse_mode=None)
        return await show_review(message, owner_id, await owner_bulletin.get(db.db, owner_id, data['number']))
    await message.answer('Не удалось подтвердить освобождение слота. Проверь состояние вестника; рендер не запускается.', parse_mode=None)


async def show_review(message, owner_id: int, item: dict):
    if item.get('mode') == 'direct' and item.get('stage') in ('new', 'source_missing'):
        return await show_sources(message, item)
    if item.get('stage') in ('approving', 'approval_uncertain', 'queued', 'shortening'):
        stage, request_id = await owner_bulletin_admission.status(db.db, owner_id, item['bulletin_id'])
        labels = {'queued':'в очереди, рендер ещё не начался', 'submitting':'проверяется приём воркером',
                  'running':'идёт озвучка или рендер', 'ready_for_review':'видео готово к отправке владельцу',
                  'fetching_review':'видео скачивается для отправки', 'sending_review':'видео отправляется',
                  'review_sent':'видео отправлено владельцу', 'review_uncertain':'отправка не подтверждена — проверь личку',
                  'review_too_large':'видео слишком велико для отправки ботом', 'review_download_failed':'скачивание не удалось',
                  'uncertain':'приём воркером не подтверждён', 'interrupted':'воркер прерван',
                  'failed':'подготовка не удалась', 'approval_uncertain':'постановка не подтверждена',
                  'shortening':'освобождение слота не подтверждено',
                  'audio_too_long':'озвучка с заставками превышает пять минут; видео не создано'}
        await message.answer(f'Вестник №{item["bulletin_id"]}: {labels.get(stage, "неизвестное состояние")}. '
            'Автоматического повтора нет.' + (f' Код задачи: {request_id}' if request_id else ''), parse_mode=None)
        if stage == 'audio_too_long':
            await action_card(message, owner_id, item, 'shorten', '✏️ Сократить сценарий')
        return
    if item.get('stage') != 'ready_for_review':
        labels = {'new':'ожидает подготовки', 'collect_new':'ожидает чтения источников',
                  'fetching_sources':'чтение источников не завершено или результат не подтверждён', 'editing':'редактор работает или ответ не подтверждён',
                  'needs_source':'источников недостаточно', 'editor_failed':'редактор не подготовил сценарий'}
        return await message.answer(f'Вестник №{item["bulletin_id"]}: {labels.get(item.get("stage"), "неизвестное состояние")}. '
                                    'Рендер не запускается.', parse_mode=None)
    if (item.get('revision') != owner_bulletin.revision(item['stories'], item['sections']) or
            not await owner_bulletin._sources_current(db.db, owner_id, item)):
        return await message.answer('Исходные данные изменились. Выбери новости заново; этот вестник устарел.', parse_mode=None)
    await message.answer(f'Вестник №{item["bulletin_id"]}: проверь каждую реплику, цитату, источник и картинку. '
                         'Цитаты не гарантируют точность пересказа. Рендер потребует отдельного подтверждения.', parse_mode=None)
    for section in item['sections']:
        await show_section(message, owner_id, item, section)
    await action_card(message, owner_id, item, 'render', '🎬 Подготовить рендер')


async def prepare_selected(message, owner_id: int, data: dict):
    """Run only after a one-use confirmation in owner_tools_panel."""
    if not await owner_trial.drafts_enabled(db.db):
        return await message.answer('Подготовка сценариев выключена; редактор не запускался.', parse_mode=None)
    for number in data['draft_ids']:
        draft = await owner_drafts.get(db.db, owner_id, number)
        if not await owner_program.eligible_draft(db.db, owner_id, draft) or draft.get('revision') != data['revisions'].get(str(number)):
            return await message.answer('Один из черновиков изменился. Выбери новости заново; редактор не запускался.', parse_mode=None)
    number = message.message_id
    try:
        await owner_bulletin.create(db.db, owner_id, number, data['draft_ids'])
    except ValueError:
        return await message.answer('Не удалось собрать источники: проверь полноту текста и версии черновиков. Редактор не запускался.', parse_mode=None)
    # create may already exist after a duplicate update; prepare itself claims once.
    result = await owner_bulletin.prepare(db.db, owner_id, number, make_bulletin)
    item = await owner_bulletin.get(db.db, owner_id, number)
    if result == 'ready_for_review':
        await show_review(message, owner_id, item)
    elif result == 'needs_source':
        await message.answer('Редактору недостаточно источников: ' + ', '.join(map(str, item['missing_ids'])) +
                             '. Уточни исходные черновики и создай новый вестник.', parse_mode=None)
    elif result == 'stale':
        await message.answer('Источник изменился до запуска редактора; выбери новости заново.', parse_mode=None)
    else:
        await message.answer(f'Состояние вестника №{number}: {result}. Повторного запроса нет; проверь список вестников.', parse_mode=None)


@router.callback_query(F.data.startswith('nb:'))
async def callback_action(callback: types.CallbackQuery, state: FSMContext):
    if not tools.authorized(callback.from_user, callback.message.chat if callback.message else None):
        return await callback.answer('Доступно только владельцу в личке.', show_alert=True)
    parts = (callback.data or '').split(':')
    action = parts[1] if len(parts) > 1 else ''
    owner = callback.from_user.id
    now = datetime.now(timezone.utc)
    try:
        if action == 'list' and len(parts) == 2:
            items = await db.db.news_owner_bulletins.find({'owner_id': owner}).sort('created_at', -1).limit(20).to_list(length=20)
            b = InlineKeyboardBuilder()
            for item in items:
                b.button(text=f'Вестник №{item["bulletin_id"]} · {item["stage"]}', callback_data=f'nb:view:{item["bulletin_id"]}')
            b.button(text='📰 Панель новостей', callback_data='news_panel:home'); b.adjust(1)
            await callback.answer()
            return await callback.message.answer('Последние вестники:' if items else 'Вестников пока нет.', reply_markup=b.as_markup(), parse_mode=None)
        if action == 'view' and len(parts) == 3 and parts[2].isdigit():
            item = await owner_bulletin.get(db.db, owner, int(parts[2]))
            if not item: return await callback.answer('Вестник не найден.', show_alert=True)
            await callback.answer()
            return await show_review(callback.message, owner, item)
        if action == 'daily' and len(parts) == 2:
            if not await owner_trial.drafts_enabled(db.db):
                return await callback.answer('Включи подготовку сценариев.', show_alert=True)
            recent = await owner_daily.recent_items(db.db, now)
            items = [{'number': index, 'label': i['title'], 'url': i['url']}
                     for index, i in enumerate(recent, 1)]
            token = secrets.token_hex(8)
            session = {'_id': token, 'owner_id': owner, 'message_id': callback.message.message_id,
                       'mode': 'direct', 'items': items, 'selected': [], 'page': 0,
                       'stage': 'open', 'expires_at': now + timedelta(hours=2)}
            await db.db.owner_bulletin_selections.insert_one(session)
            await callback.answer(); return await selection(callback.message, session)
        if action in ('editor', 'source') and len(parts) in (3, 4) and parts[2].isdigit():
            if not await owner_trial.drafts_enabled(db.db):
                return await callback.answer('Подготовка сценариев выключена.', show_alert=True)
            item = await owner_bulletin.get(db.db, owner, int(parts[2]))
            if not item or item.get('mode') != 'direct':
                return await callback.answer('Вестник не найден.', show_alert=True)
            if action == 'editor' and len(parts) == 3:
                if item.get('stage') != 'new' or not await owner_bulletin._sources_current(db.db, owner, item):
                    return await callback.answer('Источники или состояние изменились.', show_alert=True)
                await callback.answer()
                return await tools.new_confirmation(callback.message, owner, 'run_bulletin_editor',
                    'Подготовить один общий сценарий? Будет один запрос к Gemini с расходом квоты. '
                    'Рендер и публикация не запускаются.', number=item['bulletin_id'], source_revision=item['source_revision'])
            if action == 'source' and len(parts) == 4 and parts[3].isdigit():
                source_id = int(parts[3])
                if item.get('stage') != 'source_missing' or source_id not in item.get('missing_ids', []):
                    return await callback.answer('Эта новость уже уточнена или устарела.', show_alert=True)
                return await tools.ask(callback, state, 'bulletin_source',
                    f'Пришли текст для новости {source_id} одним сообщением (не менее 80 символов). '
                    'Редактор не запустится автоматически.', number=item['bulletin_id'],
                    section_id=source_id, source_revision=item['source_revision'])
            raise ValueError('Неверная кнопка')
        if action == 'choose' and len(parts) == 2:
            if not await owner_trial.drafts_enabled(db.db):
                return await callback.answer('Включи подготовку сценариев.', show_alert=True)
            docs = await db.db.news_owner_drafts.find({'owner_id': owner,
                'stage': {'$in': ['draft_ready', 'queued', 'already_queued']}}).sort('created_at', -1).limit(100).to_list(length=100)
            docs = [d for d in docs if await owner_program.eligible_draft(db.db, owner, d)]
            items = [{'number': d['draft_id'], 'revision': d['revision'],
                      'label': ((d.get('script') or {}).get('ticker') or f'Черновик №{d["draft_id"]}')} for d in docs]
            token = secrets.token_hex(8)
            session = {'_id': token, 'owner_id': owner, 'message_id': callback.message.message_id,
                       'items': items, 'selected': [], 'page': 0, 'stage': 'open', 'expires_at': now + timedelta(hours=2)}
            await db.db.owner_bulletin_selections.insert_one(session)
            await callback.answer(); return await selection(callback.message, session)
        if len(parts) < 3 or len(parts[2]) != 16 or any(c not in '0123456789abcdef' for c in parts[2]):
            raise ValueError('Неверная кнопка')
        token = parts[2]
        if action in ('sel', 'page', 'prepare'):
            session = await db.db.owner_bulletin_selections.find_one({'_id': token, 'owner_id': owner,
                'message_id': callback.message.message_id, 'stage': 'open', 'expires_at': {'$gt': now}})
            if not session: return await callback.answer('Выбор устарел. Открой его заново.', show_alert=True)
            selected = session['selected']
            if action in ('sel', 'page'):
                if len(parts) != 4 or not parts[3].isdigit(): raise ValueError('Неверная кнопка')
                value = int(parts[3])
                if action == 'sel':
                    if value not in [i['number'] for i in session['items']]: raise ValueError('Неверная новость')
                    if value in selected: changed = [n for n in selected if n != value]
                    elif len(selected) < 20: changed = selected + [value]
                    else: return await callback.answer('Можно выбрать не больше 20 новостей.', show_alert=True)
                    result = await db.db.owner_bulletin_selections.update_one({'_id': token, 'stage': 'open', 'selected': selected}, {'$set': {'selected': changed}})
                    if not result.modified_count: return await callback.answer('Выбор изменился, попробуй ещё раз.', show_alert=True)
                    session['selected'] = changed
                else:
                    if value >= max(1, (len(session['items']) + PAGE_SIZE - 1) // PAGE_SIZE): raise ValueError('Неверная страница')
                    session['page'] = value
                    await db.db.owner_bulletin_selections.update_one({'_id': token, 'stage': 'open'}, {'$set': {'page': value}})
                await callback.answer(); return await selection(callback.message, session)
            if len(parts) != 3 or not 2 <= len(selected) <= 20: raise ValueError('Нужны новости')
            result = await db.db.owner_bulletin_selections.update_one({'_id': token, 'stage': 'open', 'selected': selected}, {'$set': {'stage': 'confirmed'}})
            if not result.modified_count: return await callback.answer('Выбор уже обрабатывается.', show_alert=True)
            entries = {i['number']: i for i in session['items']}
            names = '\n'.join(f'{i}. {str(entries[n]["label"])[:50]} (№{n})' for i, n in enumerate(selected, 1))
            await callback.answer()
            if session.get('mode') == 'direct':
                choices = [{'url': entries[n]['url'], 'title': entries[n]['label']} for n in selected]
                return await tools.new_confirmation(callback.message, owner, 'collect_bulletin_sources',
                    f'Прочитать источники и найти картинки для вестника?\n{names}\n'
                    'Это не запускает редактор, озвучку или рендер. Запрос к Gemini потребует отдельного подтверждения.', choices=choices)
            revisions = {str(i['number']): i['revision'] for i in session['items'] if i['number'] in selected}
            return await tools.new_confirmation(callback.message, owner, 'prepare_bulletin',
                f'Подготовить один общий сценарий по этим новостям?\n{names}\n'
                'Один запрос к Gemini расходует квоту. Видео и публикация не запускаются.',
                draft_ids=selected, revisions=revisions)
        if action in ('render', 'shorten') and len(parts) == 3:
            card = await db.db.owner_bulletin_actions.find_one({'_id': token, 'owner_id': owner,
                'action': action, 'message_id': callback.message.message_id, 'expires_at': {'$gt': now}})
            if not card:
                return await callback.answer('Кнопка устарела. Открой вестник заново.', show_alert=True)
            item = await owner_bulletin.get(db.db, owner, card['bulletin_id'])
            if not item or item.get('revision') != card['revision']:
                return await callback.answer('Версия изменилась. Открой вестник заново.', show_alert=True)
            if not await owner_trial.runtime_enabled(db.db):
                return await callback.answer('Пробный режим выключен.', show_alert=True)
            if action == 'render':
                if item.get('stage') != 'ready_for_review':
                    return await callback.answer('Версия уже обрабатывается. Проверь состояние.', show_alert=True)
                try:
                    await owner_bulletin.approved_payload(db.db, owner, item['bulletin_id'], item['revision'])
                except (ValueError, KeyError, TypeError):
                    return await callback.answer('Источники или версия изменились. Открой вестник заново.', show_alert=True)
                prompt = ('Поставить проверенную версию вестника на озвучку и рендер? '
                          'Каждая новость озвучивается целиком двухголосым запросом Gemini, это расходует квоту. '
                          'В готовом видео нужно проверить голоса и полноту речи; анимация говорящих приблизительная. '
                          'Если выпуск превысит пять минут, он не будет обрезан или автоматически повторён. '
                          'Видео придёт только владельцу для проверки; публикации в канал не будет.')
                selected_video = next((s for s in item['sections'] if s.get('video')), None)
                if selected_video:
                    prompt += (f' В выпуске выбрано видео новости №{selected_video["id"]}: студия, '
                               'затем около 10 секунд на весь экран со звуком, без реплик поверх. '
                               'Если источник изменился или недоступен, выпуск не соберётся.')
                confirmation = 'render_bulletin'
            else:
                stage, _ = await owner_bulletin_admission.status(db.db, owner, item['bulletin_id'])
                if stage != 'audio_too_long':
                    return await callback.answer('Состояние изменилось. Открой вестник заново.', show_alert=True)
                prompt = ('Освободить слот после отказа по длительности и открыть сценарий для сокращения? '
                          'Озвучка и рендер не запустятся; новый запуск потребует отдельного подтверждения.')
                confirmation = 'shorten_bulletin'
            await callback.answer()
            return await tools.new_confirmation(callback.message, owner, confirmation, prompt,
                number=card['bulletin_id'], revision=card['revision'])
        if action in ('usevideo', 'novideo') and len(parts) == 3:
            card = await db.db.owner_bulletin_cards.find_one({'_id': token, 'owner_id': owner,
                'message_id': callback.message.message_id, 'expires_at': {'$gt': now}})
            if not card:
                return await callback.answer('Карточка устарела.', show_alert=True)
            from news_prototype import owner_bulletin_video_review
            if action == 'novideo':
                result = await owner_bulletin_video_review.remove_from_bulletin(
                    db.db, owner, card['bulletin_id'], card['revision'], card['section_id'])
                await callback.answer('Видео убрано.' if result == 'removed' else 'Версия устарела.', show_alert=result != 'removed')
                if result == 'removed':
                    item = await owner_bulletin.get(db.db, owner, card['bulletin_id'])
                    await show_review(callback.message, owner, item)
                return
            await callback.answer()
            return await tools.new_confirmation(callback.message, owner, 'use_bulletin_video',
                'Ты просмотрел исходное видео и проверил права на его изображение и звук для этого выпуска? '
                'Подтверждение выбирает именно просмотренный файл по хешу. В студии он будет за ведущими, '
                'после новости развернётся примерно на 10 секунд со звуком; реплик поверх пока нет. '
                'Озвучка и рендер сейчас не запускаются и требуют отдельного подтверждения.',
                number=card['bulletin_id'], revision=card['revision'], section_id=card['section_id'])
        if action == 'video' and len(parts) == 3:
            card = await db.db.owner_bulletin_cards.find_one({'_id': token, 'owner_id': owner,
                'message_id': callback.message.message_id, 'expires_at': {'$gt': now}})
            if not card:
                return await callback.answer('Карточка устарела. Открой вестник заново.', show_alert=True)
            from news_prototype import bulletin_video_candidate
            try:
                await bulletin_video_candidate._current_story(db.db, owner, card['bulletin_id'],
                                                               card['revision'], card['section_id'])
            except (KeyError, TypeError, ValueError):
                return await callback.answer('Источник или версия изменились. Открой вестник заново.', show_alert=True)
            await callback.answer()
            return await tools.new_confirmation(callback.message, owner, 'review_bulletin_video',
                'Скачать видео из точного публичного поста и один раз отправить тебе в личку для просмотра? '
                'Проверь изображение, звук и права. Это не добавит видео в выпуск и не запустит озвучку или рендер.',
                number=card['bulletin_id'], revision=card['revision'], section_id=card['section_id'])
        if action in ('edit', 'noimage') and len(parts) == 3:
            card = await db.db.owner_bulletin_cards.find_one({'_id': token, 'owner_id': owner,
                'message_id': callback.message.message_id, 'expires_at': {'$gt': now}})
            if not card: return await callback.answer('Карточка устарела.', show_alert=True)
            item = await owner_bulletin.get(db.db, owner, card['bulletin_id'])
            if not item or item.get('stage') != 'ready_for_review' or item.get('revision') != card['revision']:
                return await callback.answer('Версия изменилась. Открой вестник заново.', show_alert=True)
            section = next((s for s in item['sections'] if s['id'] == card['section_id']), None)
            if not section: raise ValueError('Нет новости')
            if not await owner_trial.drafts_enabled(db.db):
                return await callback.answer('Подготовка сценариев выключена.', show_alert=True)
            if action == 'edit':
                editable = owner_bulletin.editable_section(section)
                await tools.ask(callback, state, 'bulletin_edit', 'Скопируй следующую карточку, исправь и отправь одним сообщением. '
                    'Источник менять нельзя; исправленные реплики потребуется проверить. При изменении выбранное видео придётся проверить и выбрать заново.',
                    number=card['bulletin_id'], section_id=card['section_id'], revision=card['revision'])
                return await callback.message.answer(editable, parse_mode=None)
            result = await owner_bulletin.change_section(db.db, owner, card['bulletin_id'], card['revision'], card['section_id'], remove_image=True)
            await callback.answer('Картинка убрана. При изменении версии выбранное видео сброшено; открой вестник заново.' if result == 'saved' else 'Версия устарела. Открой вестник заново.', show_alert=result != 'saved')
            if result == 'saved':
                updated = await owner_bulletin.get(db.db, owner, card['bulletin_id'])
                await show_section(callback.message, owner, updated, next(s for s in updated['sections'] if s['id'] == card['section_id']))
            return
        await callback.answer('Неизвестное действие.', show_alert=True)
    except Exception as exc:
        logger.warning('Ошибка панели вестника: %s', type(exc).__name__)
        try:
            await callback.answer('Действие не подтверждено. Проверь список вестников; не повторяй вслепую.', show_alert=True)
        except Exception:
            pass
