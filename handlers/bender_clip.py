"""Explicit, opt-in video replies routed through the experimental worker."""
import asyncio
import logging
import os
import re

from aiogram import F, Router, types
from aiogram.filters import Command

from ai_logic import generate_with_retry
from bender_triggers import EX_VIDEO_PATTERN, parse_video_request
from bender_worker_client import BenderWorkerClient, WorkerBusy, WorkerError, WorkerTimeout
from database import db
from handlers.admin_panel import get_chat_feature_flags

router = Router()
logger = logging.getLogger(__name__)
# A process-local guard avoids generating multiple LLM answers while the worker
# handles a single job. The worker also enforces its own single-job limit.
_render_lock = asyncio.Lock()


def spoken_text(value):
    """Keep text within the worker limit without cutting in the middle of a word."""
    text = re.sub(r'\s+', ' ', value or '').strip()
    text = re.sub(r'[*_`#]', '', text).strip()
    if len(text) <= 240:
        return text
    prefix = text[:239]
    last_sentence = max(prefix.rfind('. '), prefix.rfind('! '), prefix.rfind('? '))
    if last_sentence >= 60:
        return prefix[:last_sentence + 1].strip()
    cut = prefix.rfind(' ')
    return (prefix[:cut] if cut > 0 else prefix).rstrip(' ,;:-') + '…'


def reply_context(message):
    source = message.reply_to_message
    return ((source.text or source.caption or '').strip()[:4000] if source else '')


async def reply_text_safely(message, text):
    try:
        await message.reply(text)
    except Exception:
        logger.warning('Не удалось отправить текстовый ответ: chat_id=%s', message.chat.id)


@router.message(Command('bender'))
@router.message(F.text.regexp(EX_VIDEO_PATTERN))
async def video_reply(message: types.Message):
    # During the experiment activation is per group. Unlike is_feature_enabled,
    # this check does not enable the feature automatically in private chats.
    if message.chat.type not in ('group', 'supergroup'):
        return
    flags = await get_chat_feature_flags(message.chat.id)
    if not flags.get('video_notes', False):
        return
    query = parse_video_request(message.text)
    if query is None:
        return
    context = reply_context(message)
    if not query and not context:
        await reply_text_safely(message, 'Укажи тему или ответь командой на сообщение.')
        return
    url = os.getenv('BENDER_WORKER_URL', '').strip()
    key = os.getenv('BENDER_WORKER_KEY', '').strip()
    if not url or not key:
        await reply_text_safely(message, 'Сервис видеокружков пока не настроен.')
        return
    try:
        client = BenderWorkerClient(url, key)
    except ValueError:
        logger.warning('Некорректные настройки видеоворкера')
        await reply_text_safely(message, 'Сервис видеокружков пока не настроен.')
        return
    if _render_lock.locked():
        await reply_text_safely(message, 'Я уже записываю другой кружок. Попробуй чуть позже.')
        return

    async with _render_lock:
        status = None
        answer = ''
        try:
            status = await message.reply('🎬 Готовлю кружок, это может занять пару минут…')
            persona = await db.personas.find_one({'p_id': 'bender'})
            if not persona:
                await reply_text_safely(message, 'Не нашёл настройки Бендера.')
                return
            prompt = (
                f"РОЛЬ: {persona.get('prompt', 'Саркастичный робот Бендер.')}\n"
                f"СООБЩЕНИЕ ДЛЯ КОНТЕКСТА: {context or '(нет)'}\n"
                f"ВОПРОС ПОЛЬЗОВАТЕЛЯ: {query or 'Прокомментируй сообщение.'}\n"
                'Ответь на русском от лица персонажа, не более 200 символов, '
                'одной законченной мыслью. Только текст для произнесения, без разметки и эмодзи.'
            )
            generated = await asyncio.wait_for(generate_with_retry(prompt), timeout=45)
            answer = spoken_text(generated)
            if not answer:
                await reply_text_safely(message, 'Не смог придумать ответ. Попробуй позже.')
                return
            file_id = await client.render(answer)
            # reply_video_note uses the triggering message's chat and message ID,
            # preserving the normal reply relationship, including group topics.
            await message.reply_video_note(video_note=file_id)
        except WorkerBusy:
            await reply_text_safely(message, 'Воркер сейчас занят. Попробуй чуть позже.')
        except WorkerTimeout:
            await reply_text_safely(message, f'Кружок готовится слишком долго. Вот текст ответа:\n{answer}')
        except WorkerError:
            await reply_text_safely(message, f'Не смог сделать кружок. Вот текст ответа:\n{answer}')
        except asyncio.TimeoutError:
            await reply_text_safely(message, 'Не удалось вовремя придумать ответ. Попробуй позже.')
        except Exception as exc:
            logger.warning('Ошибка создания видеокружка: chat_id=%s type=%s', message.chat.id, type(exc).__name__)
            fallback = f'Не смог отправить кружок. Вот текст ответа:\n{answer}' if answer else 'Не смог подготовить кружок. Попробуй позже.'
            await reply_text_safely(message, fallback)
        finally:
            if status is not None:
                try:
                    await status.delete()
                except Exception:
                    logger.debug('Не удалось удалить статус видеокружка: chat_id=%s', message.chat.id)
