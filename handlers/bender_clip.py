"""Explicit, opt-in video replies routed through the experimental worker."""
import asyncio
import logging
import os
import random
import re
import time
import uuid

from aiogram import F, Router, types
from aiogram.filters import Command

from ai_logic import generate_with_retry
from bender_triggers import EX_VIDEO_PATTERN, parse_video_request
from bender_worker_client import (
    BenderWorkerClient, WorkerBusy, WorkerError, WorkerFailed, WorkerUploadUncertain,
)
from bender_queue import VideoQueue
from database import db
from handlers.admin_panel import get_chat_feature_flags

router = Router()
logger = logging.getLogger(__name__)
# One shared MongoDB document coordinates admission and a single consumer.
video_queue = VideoQueue(db.db.bender_video_queue)

STATUS_MESSAGES = (
    '🎬 Снимаю видос. Не отвлекайте звезду, кожаные мешки!',
    '🧽 Полирую пузо для крупного плана. Это искусство!',
    '📹 Настраиваю камеру. Мой блестящий зад должен попасть в кадр!',
    '🎙 Прочищаю динамик. Без автографов, я занят!',
    '💰 Считаю гонорар за съёмку. Вам выставить счёт?',
    '🤖 Даже шедевру нужно время. Особенно такому великолепному!',
    '🎞 Ищу свой лучший ракурс. Трудный выбор: все хороши!',
    '🔧 Подкручиваю обаяние. Осторожно, может искрить!',
    '✨ Навожу блеск. Камера должна ослепнуть от зависти!',
    '🎭 Репетирую гениальную реплику. Не мешайте таланту!',
    '🪙 Проверяю, оплатили ли вы выступление. Пока сомневаюсь!',
    '🎬 Прогоняю дубль. Даже мои ошибки достойны награды!',
    '📡 Заряжаю сарказм. Держитесь подальше от антенны!',
    '🛠 Смазываю шарниры. Звезда должна двигаться красиво!',
    '🍿 Выгоняю зрителей со съёмочной площадки. Сеанс позже!',
    '💡 Ставлю свет. Пусть все увидят моё металлическое великолепие!',
    '🎥 Убираю лишнее из кадра. Люди, это я про вас!',
    '🏆 Готовлю речь для вручения награды самому себе!',
)
STATUS_INTERVAL_SECONDS = 15


def choose_status(previous=None):
    """Pick randomly, avoiding an immediate repeat when possible."""
    choices = [text for text in STATUS_MESSAGES if text != previous]
    return random.choice(choices or STATUS_MESSAGES)


def spoken_text(value):
    """Keep text within the worker limit without cutting in the middle of a word."""
    text = re.sub(r'\s+', ' ', value or '').strip()
    text = re.sub(r'[*_`#]', '', text).strip()
    if len(text) <= 480:
        return text
    prefix = text[:479]
    last_sentence = max(prefix.rfind('. '), prefix.rfind('! '), prefix.rfind('? '))
    if last_sentence >= 60:
        return prefix[:last_sentence + 1].strip()
    cut = prefix.rfind(' ')
    return (prefix[:cut] if cut > 0 else prefix).rstrip(' ,;:-') + '…'


def reply_context(message):
    source = message.reply_to_message
    return ((source.text or source.caption or '').strip()[:4000] if source else '')


def reply_options(message):
    """Keep the original chat/topic, even if the triggering message is deleted."""
    return {
        'chat_id': message.chat.id,
        'message_thread_id': getattr(message, 'message_thread_id', None),
        'reply_parameters': types.ReplyParameters(
            message_id=message.message_id, allow_sending_without_reply=True,
        ),
    }


async def reply_text_safely(message, text):
    try:
        await message.bot.send_message(text=text, **reply_options(message))
    except Exception:
        logger.warning('Не удалось отправить текстовый ответ: chat_id=%s', message.chat.id)


CHECKING_MESSAGES = (
    '🔧 Проверяю список заявок. Гениальность тоже любит порядок!',
    '📋 Сверяюсь с очередью, кожаный мешок. Не дави на мои кнопки!',
    '🎟 Ищу свободное место на съёмочной площадке!',
)
QUEUE_MESSAGES = (
    '🎟 Записал в очередь. Величайший робот занят, но до тебя тоже доберётся!',
    '🤖 Вставай в очередь, кожаный мешок. Звезда принимает по одному!',
    '🎬 Твой билет на съёмку принят. Пока полюбуйся моим великолепием!',
    '🪙 Очередь занята, но для тебя я оставил местечко. Бесплатно, пока что!',
    '📋 Внёс тебя в список. Нет, взятка не ускорит очередь. Наверное.',
    '🍿 Готовь попкорн: твоё выступление моего величества будет чуть позже!',
    '🔧 Заявка принята. Смазываю механизм очереди сарказмом!',
    '🏆 Даже гении не могут быть сразу везде. Хотя я стараюсь!',
)
FULL_MESSAGES = (
    '🚧 Очередь забита до блеска! Приходи чуть позже, кожаный мешок.',
    '🎟 Все места заняты. Даже у моей гениальности есть предел пропускной способности!',
    '🤖 Сегодня аншлаг! Дай мне разгребсти очередь и попробуй ещё раз.',
)
BUSY_MESSAGES = (
    '🎬 Площадка ещё занята. Твоя очередь сохранена, жду своего выхода!',
    '🔧 Предыдущий дубль ещё заканчивается. Твоё место в очереди на месте!',
    '🍿 Камера занята, но твой билет никуда не делся. Потерпи немного!',
)


def stored_reply_options(item):
    return {
        'chat_id': item['chat_id'],
        'message_thread_id': item.get('thread_id'),
        'reply_parameters': types.ReplyParameters(
            message_id=item['message_id'], allow_sending_without_reply=True,
        ),
    }


async def _edit_status(bot, item, text):
    if item.get('status_message_id') is None:
        return
    try:
        await bot.edit_message_text(chat_id=item['chat_id'], message_id=item['status_message_id'], text=text)
    except Exception as exc:
        logger.debug('Не удалось изменить статус очереди: %s', type(exc).__name__)


async def _delete_status(bot, item):
    if item.get('status_message_id') is None:
        return
    try:
        await bot.delete_message(chat_id=item['chat_id'], message_id=item['status_message_id'])
    except Exception as exc:
        logger.debug('Не удалось удалить статус очереди: %s', type(exc).__name__)


async def _queue_rotation(bot, item, previous):
    while True:
        await asyncio.sleep(STATUS_INTERVAL_SECONDS)
        text = choose_status(previous)
        await _edit_status(bot, item, text)
        previous = text


async def _report_and_remove(queue, bot, item, text):
    # Mark first: on restart an uncertain Telegram send is never repeated.
    await queue.update(item['id'], stage='reporting')
    try:
        await bot.send_message(text=text, **stored_reply_options(item))
    except Exception as exc:
        logger.warning('Не удалось сообщить о результате кружка: %s', type(exc).__name__)
    await queue.remove(item['id'])
    await _delete_status(bot, item)


async def process_queued_video(queue, bot, item):
    """Run or safely recover the head; return True for confirmed worker busy."""
    job_id = item['id']
    stage = item['stage']
    if stage == 'reporting':
        await queue.remove(job_id)
        await _delete_status(bot, item)
        return False
    if stage == 'sending':
        await _report_and_remove(queue, bot, item,
            '🤖 Связь прервалась во время отправки кружка. Проверь чат: возможно, он уже пришёл.')
        return False
    if stage == 'ready' and item.get('retry_after', 0) > time.time():
        return True

    first_status = choose_status()
    await _edit_status(bot, item, first_status)
    rotation = asyncio.create_task(_queue_rotation(bot, item, first_status))
    try:
        if stage == 'queued':
            try:
                persona = await db.personas.find_one({'p_id': 'bender'})
                if not persona:
                    await _report_and_remove(queue, bot, item, '🤖 Не нашёл настройки Бендера.')
                    return False
                prompt = (
                    f"РОЛЬ: {persona.get('prompt', 'Саркастичный робот Бендер.')}\n"
                    f"СООБЩЕНИЕ ДЛЯ КОНТЕКСТА: {item['context'] or '(нет)'}\n"
                    f"ВОПРОС ПОЛЬЗОВАТЕЛЯ: {item['query'] or 'Прокомментируй сообщение.'}\n"
                    'Ответь на русском от лица персонажа, примерно 300–400 символов, не более 450 символов, '
                    'с законченными предложениями. Только текст для произнесения, без разметки и эмодзи.'
                )
                generated = await asyncio.wait_for(generate_with_retry(prompt), timeout=45)
                answer = spoken_text(generated)
                if not answer:
                    raise ValueError('empty response')
            except Exception as exc:
                logger.warning('Не удалось подготовить ответ для кружка: %s', type(exc).__name__)
                await _report_and_remove(queue, bot, item, '🤖 Мои гениальные схемы не придумали ответ. Попробуй позже.')
                return False
            await queue.update(job_id, stage='ready', answer=answer)
            item['answer'] = answer
            stage = 'ready'

        if stage in ('ready', 'submitting'):
            try:
                url = os.getenv('BENDER_WORKER_URL', '').strip()
                key = os.getenv('BENDER_WORKER_KEY', '').strip()
                client = BenderWorkerClient(url, key)
                if stage == 'ready':
                    # This marker is persisted before the POST. If the process
                    # dies at any point, recovery only checks the same UUID.
                    await queue.update(job_id, stage='submitting')
                    file_id = await client.render(item['answer'], request_id=item['request_id'])
                else:
                    file_id = await client.resume(item['request_id'])
            except WorkerBusy:
                # The worker explicitly rejected this UUID; retry is safe.
                await queue.update(job_id, stage='ready', retry_after=time.time() + 10)
                await _edit_status(bot, item, random.choice(BUSY_MESSAGES))
                return True
            except WorkerUploadUncertain:
                # No attempt to the destination chat has happened. Persist the
                # recoverable read stage before downloading the existing video.
                await queue.update(job_id, stage='fallback_ready')
                stage = 'fallback_ready'
            except WorkerFailed:
                await _report_and_remove(queue, bot, item,
                    f"🤖 Кружок не получился. Зато вот мой гениальный ответ:\n{item['answer']}")
                return False
            except (WorkerError, ValueError) as exc:
                logger.warning('Неизвестен результат создания кружка: %s', type(exc).__name__)
                await _report_and_remove(queue, bot, item,
                    f"🤖 Связь со студией прервалась, и я не знаю, готов ли кружок. Вот ответ:\n{item['answer']}")
                return False
            if stage != 'fallback_ready':
                await queue.update(job_id, stage='file_ready', file_id=file_id)
                item['file_id'] = file_id
                stage = 'file_ready'

        if stage == 'fallback_ready':
            try:
                url = os.getenv('BENDER_WORKER_URL', '').strip()
                key = os.getenv('BENDER_WORKER_KEY', '').strip()
                client = BenderWorkerClient(url, key)
                video = await client.fetch_uncertain_video(item['request_id'])
            except (WorkerError, ValueError) as exc:
                logger.warning('Не удалось получить сохранённый кружок: %s', type(exc).__name__)
                await _report_and_remove(queue, bot, item,
                    f"🤖 Связь со складом прервалась, и я не смог получить готовый кружок. Вот ответ:\n{item['answer']}")
                return False
            # If the worker or caller dies after this marker, do not retry the
            # ambiguous Telegram send; the user is asked to check the chat.
            await queue.update(job_id, stage='sending')
            try:
                await bot.send_video_note(video_note=types.BufferedInputFile(video, filename='video.mp4'),
                                          **stored_reply_options(item))
            except Exception as exc:
                logger.warning('Неизвестен результат резервной отправки кружка: %s', type(exc).__name__)
                await _report_and_remove(queue, bot, item,
                    '🤖 Связь прервалась во время отправки кружка. Проверь чат: возможно, он уже пришёл.')
                return False
            await queue.remove(job_id)
            await _delete_status(bot, item)
            return False

        if stage == 'file_ready':
            await queue.update(job_id, stage='sending')
            try:
                await bot.send_video_note(video_note=item['file_id'], **stored_reply_options(item))
            except Exception as exc:
                logger.warning('Неизвестен результат отправки кружка: %s', type(exc).__name__)
                await _report_and_remove(queue, bot, item,
                    '🤖 Связь прервалась во время отправки кружка. Проверь чат: возможно, он уже пришёл.')
                return False
            await queue.remove(job_id)
            await _delete_status(bot, item)
            return False
        logger.error('Неизвестный этап задачи кружка: %s', stage)
        await _report_and_remove(queue, bot, item, '🤖 Мои схемы запутались. Попробуй позже.')
        return False
    finally:
        rotation.cancel()
        await asyncio.gather(rotation, return_exceptions=True)


@router.message(Command('bender'))
@router.message(F.text.regexp(EX_VIDEO_PATTERN))
async def video_reply(message: types.Message):
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
    try:
        BenderWorkerClient(url, key)
    except ValueError:
        await reply_text_safely(message, '🤖 Студия кружков пока не настроена.')
        return
    status = None
    try:
        status = await message.bot.send_message(text=random.choice(CHECKING_MESSAGES), **reply_options(message))
    except Exception as exc:
        logger.warning('Не удалось отправить статус очереди: %s', type(exc).__name__)
    item = {
        'id': f'{message.chat.id}:{message.message_id}', 'request_id': str(uuid.uuid4()),
        'chat_id': message.chat.id, 'message_id': message.message_id,
        'thread_id': getattr(message, 'message_thread_id', None),
        'status_message_id': status.message_id if status is not None else None,
        'query': query, 'context': context, 'stage': 'queued',
    }
    try:
        result = await video_queue.enqueue(item)
    except Exception as exc:
        logger.warning('Неизвестен результат постановки в очередь: %s', type(exc).__name__)
        await reply_text_safely(message,
            '🤖 Очередь барахлит, и я не знаю, записал ли запрос. Пока не отправляй его повторно.')
        return
    if result == 'accepted':
        await _edit_status(message.bot, item, random.choice(QUEUE_MESSAGES))
    else:
        if status is not None:
            try:
                await status.delete()
            except Exception:
                pass
        if result == 'full':
            await reply_text_safely(message, random.choice(FULL_MESSAGES))
