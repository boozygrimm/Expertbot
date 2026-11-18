"""Button-driven review and program assembly, without exposing revision hashes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import secrets

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import ADMIN_ID
from database import db
from news_prototype import owner_drafts, owner_program, owner_trial
from handlers import owner_tools_panel as tools

router = Router()
logger = logging.getLogger(__name__)


async def card(message, owner_id: int, kind: str, item: dict, *, for_program: bool = False,
               text: str | None = None, media_view: bool = False):
    """Store the reviewed revision server-side (it cannot fit in callback_data)."""
    if kind == 'draft':
        number = item['draft_id']
        stage = item.get('stage')
        revision = item.get('revision')
        if stage not in ('draft_ready', 'needs_source'):
            return
    else:
        number = item['program_id']
        stage = item.get('stage')
        revision = item.get('revision')
        if stage != 'ready_for_review':
            return
    token = secrets.token_hex(8)
    now = datetime.now(timezone.utc)
    await db.db.owner_news_cards.delete_many({'expires_at': {'$lt': now}})
    await db.db.owner_news_cards.insert_one({'_id': token, 'owner_id': owner_id, 'kind': kind,
        'number': number, 'revision': revision, 'stage': stage, 'for_program': for_program,
        'expires_at': now + timedelta(days=2)})
    b = InlineKeyboardBuilder()
    if kind == 'draft' and stage == 'needs_source':
        b.button(text='📄 Прислать текст источника', callback_data=f'newsact:source:{token}')
    elif kind == 'draft' and media_view:
        if item.get('script', {}).get('image_regions'):
            b.button(text='🔍 Убрать увеличенные области', callback_data=f'newsact:nofocus:{token}')
        if item.get('script', {}).get('image_b64'):
            b.button(text='🖼 Убрать картинку', callback_data=f'newsact:noimage:{token}')
        b.button(text='↩️ К сценарию', callback_data=f'newsact:back:{token}')
    elif kind == 'draft':
        b.button(text='✏️ Исправить сценарий', callback_data=f'newsact:edit:{token}')
        if item.get('script', {}).get('image_b64'):
            b.button(text='🖼 Проверить картинку', callback_data=f'newsact:media:{token}')
        if for_program:
            b.button(text='📺 Собрать выпуск', callback_data='newsact:choose')
        else:
            b.button(text='🎬 Подтвердить рендер сюжета', callback_data=f'newsact:approve:{token}')
    else:
        b.button(text='🎬 Подтвердить рендер выпуска', callback_data=f'newsact:approve:{token}')
    b.button(text='📰 Панель новостей', callback_data='news_panel:home')
    b.adjust(1)
    await message.answer(text if text is not None else ('Действия с этой версией:' if stage != 'needs_source' else 'Источник требует уточнения:'),
                         reply_markup=b.as_markup(), parse_mode=None, disable_web_page_preview=True)


async def selection(message, session):
    entries = session['items']
    selected = session.get('selected', [])
    page = session.get('page', 0)
    per_page = 8
    pages = max(1, (len(entries) + per_page - 1) // per_page)
    page = max(0, min(page, pages - 1))
    b = InlineKeyboardBuilder()
    for item in entries[page * per_page:(page + 1) * per_page]:
        number = item['number']
        label = item.get('label') or f'Черновик №{number}'
        b.button(text=('✅ ' if number in selected else '▫️ ') + label[:65],
                 callback_data=f'newsact:select:{session["_id"]}:{number}')
    b.adjust(1)
    nav = InlineKeyboardBuilder()
    if page:
        nav.button(text='⬅️', callback_data=f'newsact:page:{session["_id"]}:{page - 1}')
    if page + 1 < pages:
        nav.button(text='➡️', callback_data=f'newsact:page:{session["_id"]}:{page + 1}')
    nav.adjust(2)
    if page or page + 1 < pages:
        b.attach(nav)
    end = InlineKeyboardBuilder()
    if len(selected) >= 2:
        end.button(text='📺 Собрать выпуск', callback_data=f'newsact:build:{session["_id"]}')
    end.button(text='📰 Панель новостей', callback_data='news_panel:home')
    end.adjust(1)
    b.attach(end)
    text = (f'📺 Выбор сюжетов · страница {page + 1}/{pages}\n'
            f'Выбрано {len(selected)} из 4. Показаны готовые и подтверждённо доставленные черновики среди 100 последних. Нужны 2–4 проверенных сюжета. '
            'Создание выпуска не запускает рендер. При подтверждении все сюжеты будут созданы заново с расходом квоты.')
    await message.edit_text(text, reply_markup=b.as_markup(), parse_mode=None)


@router.callback_query(F.data.startswith('newsact:'))
async def callback_action(callback: types.CallbackQuery, state: FSMContext):
    if not tools.authorized(callback.from_user, callback.message.chat if callback.message else None):
        return await callback.answer('Доступно только владельцу в личке.', show_alert=True)
    parts = (callback.data or '').split(':')
    action = parts[1] if len(parts) > 1 else ''
    try:
        if action == 'choose':
            if not await owner_trial.drafts_enabled(db.db):
                return await callback.answer('Включи подготовку сценариев.', show_alert=True)
            docs = await db.db.news_owner_drafts.find({'owner_id': callback.from_user.id,
                'stage': {'$in': ['draft_ready', 'queued', 'already_queued']}}).sort('created_at', -1).limit(100).to_list(length=100)
            docs = [d for d in docs if await owner_program.eligible_draft(db.db, callback.from_user.id, d)]
            items = [{'number': d['draft_id'], 'revision': d['revision'],
                      'label': ('[уже отправлен] ' if d.get('stage') != 'draft_ready' else '') +
                      ((d.get('script') or {}).get('ticker', '') or f'Черновик №{d["draft_id"]}')} for d in docs]
            token = secrets.token_hex(8)
            now = datetime.now(timezone.utc)
            await db.db.owner_news_selections.delete_many({'expires_at': {'$lt': now}})
            session = {'_id': token, 'owner_id': callback.from_user.id, 'message_id': callback.message.message_id,
                       'items': items, 'selected': [], 'page': 0, 'stage': 'open',
                       'expires_at': now + timedelta(hours=2)}
            await db.db.owner_news_selections.insert_one(session)
            await callback.answer()
            await selection(callback.message, session)
            return
        if len(parts) < 3 or len(parts[2]) != 16 or any(c not in '0123456789abcdef' for c in parts[2]):
            raise ValueError('Неверная кнопка')
        token = parts[2]
        if action in ('select', 'page', 'build'):
            session = await db.db.owner_news_selections.find_one({'_id': token, 'owner_id': callback.from_user.id,
                'message_id': callback.message.message_id, 'stage': 'open', 'expires_at': {'$gt': datetime.now(timezone.utc)}})
            if not session:
                return await callback.answer('Выбор устарел. Открой его заново.', show_alert=True)
            selected = session['selected']
            if action in ('select', 'page'):
                if len(parts) != 4 or not parts[3].isdigit():
                    raise ValueError('Неверный номер')
                value = int(parts[3])
                if action == 'select':
                    if value not in [item['number'] for item in session['items']]:
                        raise ValueError('Неверный черновик')
                    if value in selected:
                        changed = [n for n in selected if n != value]
                    elif len(selected) < 4:
                        changed = selected + [value]
                    else:
                        return await callback.answer('Не больше четырёх сюжетов.', show_alert=True)
                    result = await db.db.owner_news_selections.update_one({'_id': token, 'stage': 'open',
                        'selected': selected}, {'$set': {'selected': changed}})
                    if not result.modified_count:
                        return await callback.answer('Выбор изменился, попробуй снова.', show_alert=True)
                    session['selected'] = changed
                else:
                    if value >= max(1, (len(session['items']) + 7) // 8):
                        raise ValueError('Неверная страница')
                    session['page'] = value
                    await db.db.owner_news_selections.update_one({'_id': token, 'stage': 'open'}, {'$set': {'page': value}})
                await callback.answer()
                await selection(callback.message, session)
                return
            if not 2 <= len(selected) <= 4:
                raise ValueError('Нужно выбрать сюжеты')
            result = await db.db.owner_news_selections.update_one({'_id': token, 'stage': 'open',
                'selected': selected}, {'$set': {'stage': 'confirmed'}})
            if not result.modified_count:
                return await callback.answer('Выбор уже обрабатывается.', show_alert=True)
            await callback.answer()
            revisions = {str(i['number']): i['revision'] for i in session['items'] if i['number'] in selected}
            labels = {i['number']: i['label'] for i in session['items']}
            names = '\n'.join(f'{index}. {str(labels[n])[:100]} (№{n})' for index, n in enumerate(selected, 1))
            await tools.new_confirmation(callback.message, callback.from_user.id, 'build_program',
                f'Собрать выпуск в этом порядке?\n{names}\nРендер не запускается.',
                draft_ids=selected, revisions=revisions)
            return
        card_doc = await db.db.owner_news_cards.find_one({'_id': token, 'owner_id': callback.from_user.id,
                                                          'expires_at': {'$gt': datetime.now(timezone.utc)}})
        if not card_doc:
            return await callback.answer('Карточка устарела. Открой её заново.', show_alert=True)
        if not await owner_trial.drafts_enabled(db.db):
            return await callback.answer('Подготовка сценариев выключена.', show_alert=True)
        kind, number, rev = card_doc['kind'], card_doc['number'], card_doc.get('revision')
        item = await (owner_drafts.get(db.db, callback.from_user.id, number) if kind == 'draft'
                      else owner_program.get(db.db, callback.from_user.id, number))
        if not item or item.get('stage') != card_doc['stage'] or (rev and item.get('revision') != rev):
            return await callback.answer('Состояние изменилось. Открой карточку заново.', show_alert=True)
        if action == 'media' and kind == 'draft' and card_doc['stage'] == 'draft_ready':
            if not item.get('script', {}).get('image_b64'):
                return await callback.answer('Картинка больше не выбрана.', show_alert=True)
            await callback.answer()
            from handlers import news_owner_drafts
            return await news_owner_drafts.show_image_details(callback.message, callback.from_user.id, item,
                                                               for_program=card_doc.get('for_program', False))
        if action == 'back' and kind == 'draft' and card_doc['stage'] == 'draft_ready':
            await callback.answer()
            from handlers import news_owner_drafts
            return await news_owner_drafts.show(tools.proxy(callback.message, callback.from_user), number,
                                                item=item, for_program=card_doc.get('for_program', False))
        if action == 'source' and kind == 'draft' and card_doc['stage'] == 'needs_source':
            await tools.ask(callback, state, 'news_source', 'Пришли полный текст источника одним сообщением. '
                            'Редактор использует квоту Gemini; рендер не начнётся.', number=number)
            return
        if action == 'edit' and kind == 'draft' and card_doc['stage'] == 'draft_ready':
            editable = owner_drafts.editable_message(number, item['url'], item['script']).split('\n', 1)[1]
            await tools.ask(callback, state, 'news_edit', editable, number=number, revision=rev,
                            for_program=card_doc.get('for_program', False),
                            answer_text='Скопируй новое сообщение, исправь строки и пришли обратно. '
                                        'Ссылку источника менять нельзя. Для отмены нажми кнопку под сообщением.')
            return
        if action in ('noimage', 'nofocus') and kind == 'draft' and card_doc['stage'] == 'draft_ready':
            await callback.answer()
            prompt = ('Убрать изображение из этой версии черновика?' if action == 'noimage' else
                      'Убрать автоматически выбранные области увеличения из этой версии черновика?')
            await tools.new_confirmation(callback.message, callback.from_user.id, action, prompt,
                                         number=number, revision=rev, for_program=card_doc.get('for_program', False))
            return
        if action == 'approve' and card_doc['stage'] in ('draft_ready', 'ready_for_review'):
            await callback.answer()
            await tools.new_confirmation(callback.message, callback.from_user.id, 'approve_' + kind,
                'Запустить рендер проверенного ' + ('сюжета' if kind == 'draft' else 'выпуска') +
                f' №{number}? Готовое видео придёт в личку, публикации не будет.' +
                (' Все сюжеты выпуска будут созданы заново, расходуется квота озвучки.' if kind == 'program' else ''),
                number=number, revision=rev)
            return
        await callback.answer('Неизвестное действие.', show_alert=True)
    except Exception as exc:
        logger.warning('Не удалось выполнить действие с новостью: %s', type(exc).__name__)
        try:
            await callback.answer('Не удалось выполнить действие. Открой карточку заново.', show_alert=True)
        except Exception:
            pass
