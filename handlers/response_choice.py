"""Per-request response format choice, isolated from special-purpose handlers."""
from datetime import datetime, timedelta, timezone
import logging
import re
import uuid

from aiogram import F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from database import db
from handlers.admin_panel import get_chat_feature_flags

router = Router()
logger = logging.getLogger(__name__)
CHOICE_LIFETIME = timedelta(minutes=15)
CALLBACK_RE = re.compile(r'^exfmt:([0-9a-f]{32}):(text|video)$')


def now():
    return datetime.now(timezone.utc)


class OriginalReplyTarget:
    """Reply to the original request even if its Telegram message was removed."""
    def __init__(self, callback, doc):
        self.chat = callback.message.chat
        self.bot = callback.bot
        self.from_user = callback.from_user
        self.message_id = doc['message_id']
        self.message_thread_id = doc.get('thread_id')

    async def reply(self, text, **kwargs):
        return await self.bot.send_message(
            chat_id=self.chat.id, message_thread_id=self.message_thread_id, text=text,
            reply_parameters=types.ReplyParameters(message_id=self.message_id, allow_sending_without_reply=True),
            **kwargs,
        )


async def offer_response_choice(message, query, context, *, kind='chat', persona_id=None):
    """Return True if a choice (or a safe error) was handled; False for legacy text."""
    if message.chat.type not in ('group', 'supergroup') or not message.from_user:
        return False
    flags = await get_chat_feature_flags(message.chat.id)
    if not flags.get('assistant', True) or not flags.get('video_notes', False):
        return False
    # Deterministic id makes redelivery of the original Telegram message harmless.
    choice_id = uuid.uuid5(uuid.NAMESPACE_URL, f'exfmt:{message.chat.id}:{message.message_id}').hex
    doc = {
        '_id': choice_id, 'chat_id': message.chat.id, 'message_id': message.message_id,
        'thread_id': getattr(message, 'message_thread_id', None), 'user_id': message.from_user.id,
        'query': query, 'context': context, 'kind': kind, 'persona_id': persona_id,
        'state': 'pending', 'expires_at': now() + CHOICE_LIFETIME,
    }
    try:
        await db.ex_response_choices.insert_one(doc)
    except DuplicateKeyError:
        return True
    except Exception as exc:
        logger.warning('Не удалось сохранить выбор ответа: %s', type(exc).__name__)
        await message.reply('🤖 Не смог подготовить выбор ответа. Попробуй ещё раз позже.')
        return True
    builder = InlineKeyboardBuilder()
    builder.button(text='⭕ Кружок — наряднее', callback_data=f'exfmt:{choice_id}:video')
    builder.button(text='💬 Текст — быстрее', callback_data=f'exfmt:{choice_id}:text')
    builder.adjust(2)
    menu = None
    try:
        # Publish the buttons only after the message ID is stored: a callback
        # can arrive immediately, before a send_message call returns.
        menu = await message.reply('🤖 Как мне ответить?')
        result = await db.ex_response_choices.update_one(
            {'_id': choice_id, 'state': 'pending'}, {'$set': {'menu_message_id': menu.message_id}})
        if not result.matched_count:
            raise RuntimeError('choice no longer pending')
        await menu.edit_reply_markup(reply_markup=builder.as_markup())
    except Exception as exc:
        logger.warning('Не удалось открыть выбор ответа: %s', type(exc).__name__)
        # An edit can succeed at Telegram even if its response is lost. A user
        # may already have clicked it; don't overwrite a successfully claimed menu.
        try:
            failed = await db.ex_response_choices.find_one_and_update(
                {'_id': choice_id, 'state': 'pending'}, {'$set': {'state': 'failed'}})
        except Exception:
            failed = None  # outcome is unknown; don't interfere with a possible claim
        if failed and menu is not None:
            try:
                await menu.edit_text('🤖 Не смог подготовить выбор ответа. Попробуй ещё раз позже.')
            except Exception:
                pass
    return True


@router.callback_query(F.data.startswith('exfmt:'))
async def response_choice_callback(callback: types.CallbackQuery):
    match = CALLBACK_RE.fullmatch(callback.data or '')
    if not match or not callback.message or not callback.from_user:
        return await callback.answer('Некорректная кнопка.', show_alert=True)
    choice_id, choice = match.groups()
    chat_id = callback.message.chat.id
    # The message middleware's authorization check does not run on callbacks.
    try:
        auth = await db.auth_chats.find_one({'chat_id': chat_id, 'status': 'approved'})
        if not auth:
            return await callback.answer('Этот чат больше не авторизован.', show_alert=True)
        doc = await db.ex_response_choices.find_one({'_id': choice_id})
        if not doc or doc.get('chat_id') != chat_id or doc.get('menu_message_id') != callback.message.message_id:
            return await callback.answer('Выбор устарел. Отправь запрос заново.', show_alert=True)
        if doc.get('user_id') != callback.from_user.id:
            return await callback.answer('Выбрать может автор запроса.', show_alert=True)
        flags = await get_chat_feature_flags(chat_id)
        if not flags.get('assistant', True):
            return await callback.answer('Эксперт выключен в настройках чата.', show_alert=True)
        if choice == 'video' and not flags.get('video_notes', False):
            return await callback.answer('Кружки выключены. Можно выбрать текст.', show_alert=True)
        claimed = await db.ex_response_choices.find_one_and_update(
            {'_id': choice_id, 'chat_id': chat_id, 'menu_message_id': callback.message.message_id,
             'user_id': callback.from_user.id, 'state': 'pending', 'expires_at': {'$gt': now()}},
            {'$set': {'state': 'claimed', 'choice': choice}}, return_document=ReturnDocument.AFTER)
    except Exception as exc:
        logger.warning('Не удалось обработать выбор ответа: %s', type(exc).__name__)
        return await callback.answer('Не удалось обработать выбор. Попробуй позже.', show_alert=True)
    if not claimed:
        return await callback.answer('Выбор уже сделан или устарел.', show_alert=True)
    try:
        await callback.answer()
    except Exception as exc:
        # The choice is already claimed; a lost callback acknowledgement must
        # not prevent the selected action from being started.
        logger.warning('Не удалось подтвердить нажатие: %s', type(exc).__name__)
    try:
        await callback.message.delete()
    except Exception as exc:
        logger.debug('Не удалось удалить меню выбора: %s', type(exc).__name__)
    target = OriginalReplyTarget(callback, claimed)
    if choice == 'video':
        from handlers.bender_clip import enqueue_video_reply
        return await enqueue_video_reply(target, claimed['query'], claimed['context'][:4000])
    from handlers.ex_logic import generate_chat_response, generate_persona_opinion
    if claimed['kind'] == 'opinion':
        persona = await db.personas.find_one({'p_id': claimed['persona_id']})
        if not persona:
            return await target.reply('🤖 Не нашёл настройки Бендера. Попробуй позже.')
        return await generate_persona_opinion(target, persona, claimed['query'], claimed['context'])
    personas = await db.personas.find().to_list(length=100)
    return await generate_chat_response(target, claimed['query'], claimed['context'], personas)
