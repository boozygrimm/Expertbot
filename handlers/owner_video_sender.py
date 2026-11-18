"""Owner-only, explicitly confirmed one-off video delivery to an approved group."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import io
import logging
from pathlib import Path
import secrets
import tempfile

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from pymongo import ReturnDocument

from database import db
from handlers import owner_tools_panel as tools, owner_video_media

router = Router()
logger = logging.getLogger(__name__)
PAGE_SIZE = 8


class _LimitedBuffer(io.BytesIO):
    def write(self, data):
        if self.tell() + len(data) > owner_video_media.MAX_BYTES:
            raise ValueError('Слишком большой файл')
        return super().write(data)


def _owner(user, chat):
    return tools.authorized(user, chat)


def _buttons(markup):
    markup.adjust(1)
    return markup.as_markup()


async def _group(bot, chat_id: int) -> str:
    """Check the approved ID and the bot's *current* membership, not history."""
    if type(chat_id) is not int or chat_id >= 0:
        raise ValueError('Группа недоступна')
    if not await db.auth_chats.find_one({'chat_id': chat_id, 'status': 'approved'}):
        raise ValueError('Группа больше не разрешена')
    chat = await bot.get_chat(chat_id)
    if chat.id != chat_id or chat.type not in ('group', 'supergroup'):
        raise ValueError('Это не разрешённая группа')
    me = await bot.get_me()
    member = await bot.get_chat_member(chat_id, me.id)
    status = getattr(member, 'status', None)
    if status not in ('creator', 'administrator', 'member', 'restricted') or (
            status == 'restricted' and not getattr(member, 'is_member', False)):
        raise ValueError('Бот больше не состоит в группе')
    return str(chat.title or 'Без названия')[:100]


async def _list(callback, page: int) -> None:
    if page < 0 or page > 10000:
        raise ValueError('Неверная страница')
    rows = await db.auth_chats.find({'status': 'approved', 'chat_id': {'$lt': 0}},
        {'chat_id': 1, 'title': 1}).sort('chat_id', 1).skip(page * PAGE_SIZE).limit(PAGE_SIZE + 1).to_list(length=PAGE_SIZE + 1)
    if page and not rows:
        return await callback.answer('Страница пуста. Открой список заново.', show_alert=True)
    b = InlineKeyboardBuilder()
    for row in rows[:PAGE_SIZE]:
        cid = row.get('chat_id')
        if type(cid) is int and cid < 0:
            title = str(row.get('title') or 'Группа')[:55]
            b.button(text=f'{title} · {cid}', callback_data=f'ovideo:chat:{cid}')
    if page:
        b.button(text='⬅️ Предыдущая страница', callback_data=f'ovideo:page:{page - 1}')
    if len(rows) > PAGE_SIZE:
        b.button(text='➡️ Следующая страница', callback_data=f'ovideo:page:{page + 1}')
    b.button(text='⬅️ Пульт', callback_data='owner_panel:tools')
    await callback.answer()
    await callback.message.answer('Выбери разрешённую группу. Состав бота в ней будет проверен перед отправкой.'
        if rows else 'Разрешённых групп не найдено. Проверь доступ к группам в пульте.',
        reply_markup=_buttons(b), parse_mode=None)


async def receive_video(message: types.Message, state: FSMContext, data: dict) -> None:
    if not _owner(message.from_user, message.chat):
        return
    mode = data.get('mode')
    if mode not in ('video', 'note'):
        return await message.answer('Ввод устарел. Открой отправку заново.', parse_mode=None)
    video, note, document = message.video, message.video_note, message.document
    media = video or note or document
    if not media or (document and getattr(document, 'mime_type', '') not in ('video/mp4', 'application/octet-stream')):
        return await message.answer('Пришли видео, кружок или MP4-файл.', parse_mode=None)
    file_id = getattr(media, 'file_id', None)
    if not isinstance(file_id, str) or not 1 <= len(file_id) <= 1024:
        return await message.answer('Не удалось получить видео. Пришли его снова.', parse_mode=None)
    caption = (message.caption or '') if mode == 'video' else ''
    if len(caption.encode('utf-16-le')) // 2 > 1024:
        return await message.answer('Подпись слишком длинная: максимум 1024 символа Telegram.', parse_mode=None)
    if mode == 'note' and getattr(media, 'duration', None) is not None and media.duration > 60:
        return await message.answer('Кружок может длиться до 60 секунд. Обрежь видео в Telegram и пришли его снова.', parse_mode=None)
    direct = bool((mode == 'video' and video) or (mode == 'note' and note))
    try:
        # Only a conversion/download needs the Bot API's 20 MB download limit.
        if not direct:
            size = getattr(media, 'file_size', None)
            if type(size) is not int or not 0 < size <= owner_video_media.MAX_BYTES:
                return await message.answer('Для преобразования нужен MP4 до 20 МБ. Обрежь видео в Telegram и пришли снова.', parse_mode=None)
            await message.answer('Готовлю предпросмотр в личке. В группу ничего пока не отправляю.', parse_mode=None)
            buffer = _LimitedBuffer()
            await asyncio.wait_for(message.bot.download(file_id, destination=buffer), timeout=120)
            raw = buffer.getvalue()
            if len(raw) != size or raw[4:8] != b'ftyp':
                raise owner_video_media.VideoError('Не удалось проверить MP4')
            with tempfile.TemporaryDirectory(prefix='owner-video-') as temp:
                source = Path(temp) / 'input.mp4'
                source.write_bytes(raw)
                if mode == 'note':
                    target = Path(temp) / 'note.mp4'
                    await owner_video_media.make_note(source, target)
                    payload = target.read_bytes()
                    sent = await message.bot.send_video_note(chat_id=message.from_user.id,
                        video_note=types.BufferedInputFile(payload, filename='note.mp4'))
                    file_id = sent.video_note.file_id
                else:
                    await owner_video_media.probe(source)
                    sent = await message.bot.send_video(chat_id=message.from_user.id,
                        video=types.BufferedInputFile(raw, filename='video.mp4'), caption=caption or None,
                        parse_mode=None)
                    file_id = sent.video.file_id
                if not isinstance(file_id, str) or not 1 <= len(file_id) <= 1024:
                    raise ValueError('Нет идентификатора предпросмотра')
        title = await _group(message.bot, data['chat_id'])
    except owner_video_media.VideoError as exc:
        return await message.answer(str(exc), parse_mode=None)
    except (ValueError, KeyError):
        return await message.answer('Не удалось проверить файл или доступ к группе. Ничего не отправлено в группу.', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось подготовить отправку видео: %s', type(exc).__name__)
        return await message.answer('Не удалось подготовить видео или проверить группу. В группу ничего не отправлено.', parse_mode=None)
    # A slow download/preview must not resurrect an input cancelled in the meantime.
    async with tools._input_lock:
        current = await state.get_data()
        if current.get('action') != 'owner_send_video' or current.get('input_token') != data.get('input_token'):
            return await message.answer('Ввод уже отменён или заменён. В группу ничего не отправлено.', parse_mode=None)
        await state.clear()
    token = secrets.token_hex(16)
    now = datetime.now(timezone.utc)
    await db.db.owner_video_confirmations.delete_many({'expires_at': {'$lt': now}})
    await db.db.owner_video_confirmations.insert_one({'_id': token, 'owner_id': message.from_user.id,
        'chat_id': data['chat_id'], 'title': title, 'mode': mode, 'file_id': file_id,
        'caption': caption, 'stage': 'ready', 'expires_at': now + timedelta(minutes=15)})
    b = InlineKeyboardBuilder()
    b.button(text='✅ Отправить в этот чат', callback_data=f'ovideo:send:{token}')
    b.button(text='❌ Отмена', callback_data=f'ovideo:cancel:{token}')
    label = 'кружком' if mode == 'note' else 'обычным видео'
    warning = '\nПодпись в кружок не добавляется.' if mode == 'note' and message.caption else ''
    shown_caption = ('\nПодпись: ' + caption if caption else '\nБез подписи.') if mode == 'video' else ''
    await message.answer(f'Отправить {label} от имени бота в группу «{title}» (ID {data["chat_id"]})?'
                         + shown_caption + warning + '\nПроверь видео и чат. Это увидят участники группы.',
                         reply_markup=_buttons(b), parse_mode=None)


@router.callback_query(F.data.startswith('ovideo:'))
async def action(callback: types.CallbackQuery, state: FSMContext, bot):
    if not _owner(callback.from_user, callback.message.chat if callback.message else None):
        return await callback.answer('Доступно только владельцу в личке.', show_alert=True)
    parts = (callback.data or '').split(':')
    op = parts[1] if len(parts) > 1 else ''
    try:
        if op == 'open' and len(parts) == 2:
            await state.clear()
            return await _list(callback, 0)
        if op == 'page' and len(parts) == 3 and parts[2].isascii() and parts[2].isdigit():
            return await _list(callback, int(parts[2]))
        if op in ('chat', 'format') and len(parts) == (3 if op == 'chat' else 4):
            chat_id = int(parts[2])
            title = await _group(bot, chat_id)
            if op == 'chat':
                b = InlineKeyboardBuilder()
                b.button(text='🎬 Обычное видео', callback_data=f'ovideo:format:{chat_id}:video')
                b.button(text='⭕ Кружок', callback_data=f'ovideo:format:{chat_id}:note')
                b.button(text='⬅️ К списку чатов', callback_data='ovideo:open')
                await callback.answer()
                return await callback.message.answer(f'Группа «{title}» (ID {chat_id}). Выбери формат:',
                                                     reply_markup=_buttons(b), parse_mode=None)
            mode = parts[3]
            if mode not in ('video', 'note'):
                raise ValueError('Неверный формат')
            prompt = (f'Группа «{title}» (ID {chat_id}). Пришли видео, кружок или MP4-файл. '
                      + ('Для кружка — до 60 секунд и до 20 МБ при преобразовании; кадр целиком, с полями. '
                         'Подписи у кружка нет. ' if mode == 'note' else
                         'Подпись видео сохранится; для MP4-файла или кружка при преобразовании лимит 20 МБ. ')
                      + 'Публикация — только после отдельного подтверждения.')
            return await tools.ask(callback, state, 'owner_send_video', prompt, chat_id=chat_id, mode=mode)
        if op not in ('send', 'cancel') or len(parts) != 3 or len(parts[2]) != 32 or any(c not in '0123456789abcdef' for c in parts[2]):
            raise ValueError('Неверная кнопка')
        token = parts[2]
        query = {'_id': token, 'owner_id': callback.from_user.id, 'stage': 'ready',
                 'expires_at': {'$gt': datetime.now(timezone.utc)}}
        record = await db.db.owner_video_confirmations.find_one(query)
        if not record:
            return await callback.answer('Подтверждение уже использовано или устарело. Проверь чат.', show_alert=True)
        if op == 'cancel':
            result = await db.db.owner_video_confirmations.update_one(query, {'$set': {'stage': 'cancelled'}, '$unset': {'file_id': '', 'caption': ''}})
            if not result.modified_count:
                return await callback.answer('Действие уже обрабатывается. Проверь чат.', show_alert=True)
            await callback.answer('Отменено')
            return await callback.message.edit_text('Отменено. Видео в группу не отправлено.', parse_mode=None)
        title = await _group(bot, record['chat_id'])
        if title != record['title']:
            return await callback.answer('Название группы изменилось. Открой отправку заново.', show_alert=True)
        claimed = await db.db.owner_video_confirmations.find_one_and_update(
            query, {'$set': {'stage': 'sending'}}, return_document=ReturnDocument.AFTER)
        if not claimed:
            return await callback.answer('Действие уже обрабатывается. Проверь чат.', show_alert=True)
        try:
            await callback.answer('Отправляю')
        except Exception as exc:
            logger.debug('Не удалось подтвердить нажатие: %s', type(exc).__name__)
        try:
            if claimed['mode'] == 'note':
                sent = await bot.send_video_note(chat_id=claimed['chat_id'], video_note=claimed['file_id'])
            else:
                sent = await bot.send_video(chat_id=claimed['chat_id'], video=claimed['file_id'],
                                            caption=claimed.get('caption') or None, parse_mode=None)
        except (TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter):
            await db.db.owner_video_confirmations.update_one({'_id': token, 'stage': 'sending'}, {'$set': {'stage': 'failed'}, '$unset': {'file_id': '', 'caption': ''}})
            return await callback.message.answer('Telegram не принял видео. Проверь права бота, формат и ограничения Telegram; повтор возможен только через новую отправку.', parse_mode=None)
        except Exception as exc:
            logger.warning('Результат отправки видео неизвестен: %s', type(exc).__name__)
            await db.db.owner_video_confirmations.update_one({'_id': token, 'stage': 'sending'}, {'$set': {'stage': 'uncertain'}, '$unset': {'file_id': '', 'caption': ''}})
            return await callback.message.answer('Не удалось подтвердить результат. Проверь группу: видео могло быть отправлено. Автоматического повтора нет.', parse_mode=None)
        try:
            await db.db.owner_video_confirmations.update_one({'_id': token, 'stage': 'sending'},
                {'$set': {'stage': 'sent', 'message_id': sent.message_id}, '$unset': {'file_id': '', 'caption': ''}})
        except Exception as exc:
            logger.warning('Не удалось сохранить результат отправки видео: %s', type(exc).__name__)
        await callback.message.answer(f'Видео отправлено в группу «{title}» (ID {claimed["chat_id"]}).', parse_mode=None)
    except ValueError:
        await callback.answer('Группа недоступна или кнопка устарела. Открой отправку заново.', show_alert=True)
    except Exception as exc:
        logger.warning('Не удалось открыть отправку видео: %s', type(exc).__name__)
        try:
            await callback.answer('Не удалось проверить группу или выполнить действие. Проверь состояние.', show_alert=True)
        except Exception:
            pass
