"""Owner-only, explicitly confirmed one-off video delivery to an approved group."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import io
import logging
import math
import re
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
from handlers.telegram_error_codes import TELEGRAM_ERROR_CODES

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


def _chat_id(value: object) -> int:
    # MongoDB decodes 64-bit Telegram IDs as bson.int64.Int64, an int subclass.
    # Accept numeric integers only, not bool/float/strings or invalid ranges.
    if not isinstance(value, int) or isinstance(value, bool) or not -(2**63) <= value < 0:
        raise ValueError('Группа недоступна')
    return int(value)


async def _group(bot, chat_id: int) -> str:
    """Check the approved ID and the bot's *current* membership, not history."""
    chat_id = _chat_id(chat_id)
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
    displayed = 0
    for row in rows[:PAGE_SIZE]:
        try:
            cid = _chat_id(row.get('chat_id'))
        except ValueError:
            continue
        title = str(row.get('title') or 'Группа')[:55]
        b.button(text=f'{title} · {cid}', callback_data=f'ovideo:chat:{cid}')
        displayed += 1
    if page:
        b.button(text='⬅️ Предыдущая страница', callback_data=f'ovideo:page:{page - 1}')
    if len(rows) > PAGE_SIZE:
        b.button(text='➡️ Следующая страница', callback_data=f'ovideo:page:{page + 1}')
    b.button(text='⬅️ Пульт', callback_data='owner_panel:tools')
    await callback.answer()
    text = ('Выбери разрешённую группу. Состав бота в ней будет проверен перед отправкой.' if displayed else
            'На этой странице нет доступных групп. Проверь другие страницы или доступ к группам в пульте.' if rows else
            'Разрешённых групп не найдено. Проверь доступ к группам в пульте.')
    await callback.message.answer(text, reply_markup=_buttons(b), parse_mode=None)


# Telegram's error text may contain URLs, file identifiers or other private
# data. Log only known, static descriptions; never log an arbitrary response.
_TELEGRAM_ERRORS = (
    ('file is too big', 'file_is_too_big', 'Файл слишком большой для Telegram.'),
    ('wrong file identifier', 'wrong_file_identifier', 'Telegram не смог распознать файл.'),
    ('file_id_invalid', 'file_id_invalid', 'Telegram не смог распознать файл.'),
    ('video_content_type_invalid', 'video_content_type_invalid', 'Telegram не принял формат видео.'),
    ('video_note_length_invalid', 'video_note_length_invalid', 'Telegram не принял размер кружка.'),
    ('wrong video note length', 'wrong_video_note_length', 'Telegram не принял размер кружка.'),
    ('video_note_invalid', 'video_note_invalid', 'Telegram не принял кружок.'),
    ('video_process_failed', 'video_process_failed', 'Telegram не смог обработать кружок.'),
    ('video_file_invalid', 'video_file_invalid', 'Telegram не принял видеофайл.'),
    ('media_empty', 'media_empty', 'Telegram получил пустой медиафайл.'),
    ('media_invalid', 'media_invalid', 'Telegram не принял медиафайл.'),
    ('there is no video note in the request', 'no_video_note', 'Telegram не получил видео для кружка.'),
    ('wrong type of the web page content', 'wrong_content_type', 'Telegram не принял формат видео.'),
    ('chat not found', 'chat_not_found', 'Telegram не нашёл чат.'),
    ('not enough rights', 'not_enough_rights', 'Telegram сообщает, что боту не хватает прав.'),
    ('have no rights', 'not_enough_rights', 'Telegram сообщает, что боту не хватает прав.'),
)


def _telegram_error(exc: TelegramBadRequest) -> tuple[str, str]:
    message = str(exc.message or '').lower()
    for phrase, code, explanation in _TELEGRAM_ERRORS:
        if phrase in message:
            return code, explanation
    return 'other', 'Telegram не сообщил распознаваемую причину.'


# Only a fixed vocabulary of technical error words can reach the logs. Unknown
# words, identifiers, URLs and numbers are discarded instead of logging the
# arbitrary exception message, which may contain private data.
_ERROR_WORDS = frozenset("""bad request error telegram failed fail invalid wrong
    unsupported support not no is there the a an in on of for to from and or with
    must be can cannot can't could unable too long short big large small empty
    media video note file content type size length duration dimensions dimension
    square format audio image upload download send sending sent input output
    parse processing process processed message chat user bot rights permission
    forbidden server internal timeout timed out available found specified missing
    document parameter parameters field required value remote unknown exceeded
    multipart attachment attach part entity data read write request body http url
    photo voice stream streams detected corrupt corrupted codec mp4 unable to
    access invalidated valid video_note video_note_invalid video_note_length_invalid
    video_content_type_invalid video_process_failed video_file_invalid
    media_invalid media_empty file_id_invalid
""".split())


def _safe_telegram_detail(exc: TelegramBadRequest) -> str:
    # Tokenize rather than redact an arbitrary string: only fixed, public
    # technical words are emitted, never arbitrary identifiers or URLs.
    words = re.findall(r"[a-z_']+|[^a-z_']+", str(exc.message or '').lower()[:500])
    result = []
    for word in words:
        if re.fullmatch(r"[a-z_']+", word):
            result.append(word if word in _ERROR_WORDS else '[...]')
        else:
            result.append(' ')
    return ' '.join(''.join(result).split())[:240] or '[...]'


def _telegram_symbol(exc: TelegramBadRequest) -> str:
    """Expose only published error names; fingerprint unknown symbolic names.

    A hash of a symbolic code permits matching against an updated public list
    without recording arbitrary server text, file IDs, tokens or URLs.
    """
    message = str(exc.message or '').strip()
    match = re.fullmatch(r'(?i:bad request):\s*([A-Z][A-Z0-9_]{2,79})', message)
    if not match:
        return 'unavailable'
    code = match.group(1)
    if code in TELEGRAM_ERROR_CODES:
        return code
    return 'sha256-' + hashlib.sha256(code.encode('ascii')).hexdigest()[:24]


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
    stage = 'download'
    try:
        # Only a conversion/download needs the Bot API's 20 MB download limit.
        if not direct:
            size = getattr(media, 'file_size', None)
            if type(size) is not int or not 0 < size <= owner_video_media.MAX_BYTES:
                return await message.answer('Для преобразования нужен MP4 до 20 МБ. Обрежь видео в Telegram и пришли снова.', parse_mode=None)
            stage = 'preparation_notice'
            await message.answer('Готовлю предпросмотр в личке. В группу ничего пока не отправляю.', parse_mode=None)
            buffer = _LimitedBuffer()
            stage = 'download'
            await asyncio.wait_for(message.bot.download(file_id, destination=buffer), timeout=120)
            raw = buffer.getvalue()
            if len(raw) != size or raw[4:8] != b'ftyp':
                raise owner_video_media.VideoError('Не удалось проверить MP4')
            with tempfile.TemporaryDirectory(prefix='owner-video-') as temp:
                source = Path(temp) / 'input.mp4'
                source.write_bytes(raw)
                stage = 'conversion'
                if mode == 'note':
                    target = Path(temp) / 'note.mp4'
                    duration = await owner_video_media.make_note(source, target)
                    payload = target.read_bytes()
                    stage = 'preview_note'
                    sent = await message.bot.send_video_note(chat_id=message.from_user.id,
                        video_note=types.BufferedInputFile(payload, filename='note.mp4'),
                        length=owner_video_media.SIZE, duration=math.ceil(duration))
                    file_id = sent.video_note.file_id
                else:
                    await owner_video_media.probe(source)
                    stage = 'preview_video'
                    sent = await message.bot.send_video(chat_id=message.from_user.id,
                        video=types.BufferedInputFile(raw, filename='video.mp4'), caption=caption or None,
                        parse_mode=None)
                    file_id = sent.video.file_id
                if not isinstance(file_id, str) or not 1 <= len(file_id) <= 1024:
                    raise ValueError('Нет идентификатора предпросмотра')
        stage = 'group_check'
        title = await _group(message.bot, data['chat_id'])
    except TelegramBadRequest as exc:
        code, explanation = _telegram_error(exc)
        logger.warning('Подготовка видео: TelegramBadRequest stage=%s reason=%s telegram_code=%s detail=%s',
                       stage, code, _telegram_symbol(exc), _safe_telegram_detail(exc))
        steps = {'download': 'скачивании файла', 'preview_note': 'отправке предпросмотра кружка',
                 'preview_video': 'отправке предпросмотра видео', 'group_check': 'проверке группы',
                 'preparation_notice': 'отправке сообщения о подготовке'}
        return await message.answer(f'Ошибка Telegram при {steps.get(stage, "подготовке видео")}. '
                                    f'{explanation} В группу ничего не отправлено.', parse_mode=None)
    except owner_video_media.VideoError as exc:
        return await message.answer(str(exc), parse_mode=None)
    except (ValueError, KeyError):
        return await message.answer('Не удалось проверить файл или доступ к группе. Ничего не отправлено в группу.', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось подготовить отправку видео: %s stage=%s', type(exc).__name__, stage)
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
        chat_id = _chat_id(record['chat_id'])
        title = await _group(bot, chat_id)
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
                sent = await bot.send_video_note(chat_id=chat_id, video_note=claimed['file_id'])
            else:
                sent = await bot.send_video(chat_id=chat_id, video=claimed['file_id'],
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
