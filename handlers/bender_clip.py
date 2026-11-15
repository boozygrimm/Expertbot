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
from aiogram.exceptions import TelegramBadRequest

from ai_logic import generate_with_retry
from bender_style import apply_bender_tone
from bender_triggers import EX_VIDEO_PATTERN, parse_video_request
from bender_worker_client import (
    BenderWorkerClient, WorkerBusy, WorkerError, WorkerFailed, WorkerUploadUncertain,
)
from bender_queue import VideoQueue
from config import get_msk_now
from video_prototype.sign_text import parse_generated
from database import db
from handlers.admin_panel import get_chat_feature_flags
from handlers.photo_context import has_request_photo, add_photo_context
from handlers.recent_context import add_recent_context
from handlers.voice_context import add_voice_reply_context

router = Router()
logger = logging.getLogger(__name__)
# One shared MongoDB document coordinates admission and a single consumer.
video_queue = VideoQueue(db.db.bender_video_queue)

STATUS_MESSAGES = (
    '🎬 Снимаю видос. Звезда готовится к выходу!',
    '🧽 Полирую пузо для крупного плана. Это искусство!',
    '📹 Настраиваю камеру. Мой блестящий зад должен попасть в кадр!',
    '🎙 Прочищаю динамик. Скоро можно будет брать автографы!',
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
    '🍿 Готовлю съёмочную площадку. Сеанс скоро начнётся!',
    '💡 Ставлю свет. Пусть все увидят моё металлическое великолепие!',
    '🎥 Убираю лишнее из кадра. Моё великолепие останется!',
    '🏆 Готовлю речь для вручения награды самому себе!',
)
VOICE_STATUS_MESSAGES = (
    '🎙 Записываю голосовое. Мои динамики уже разогреваются!',
    '🎧 Готовлю ответ. У величайшего робота отличный голос!',
    '🎤 Проверяю микрофон. Скоро всё расскажу!',
)
STATUS_INTERVAL_SECONDS = 15


def choose_status(previous=None, *, kind='video'):
    """Pick randomly, avoiding an immediate repeat when possible."""
    messages = VOICE_STATUS_MESSAGES if kind == 'voice' else STATUS_MESSAGES
    choices = [text for text in messages if text != previous]
    return random.choice(choices or messages)


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
    '📋 Сверяюсь с очередью. Мои кнопки уже работают!',
    '🎟 Ищу свободное место на съёмочной площадке!',
)
QUEUE_MESSAGES = (
    '🎟 Записал в очередь. Величайший робот занят, но до тебя тоже доберётся!',
    '🤖 Записал в очередь. Звезда принимает по одному!',
    '🎬 Твой билет на съёмку принят. Пока полюбуйся моим великолепием!',
    '🪙 Очередь занята, но для тебя я оставил местечко. Бесплатно, пока что!',
    '📋 Внёс тебя в список. Нет, взятка не ускорит очередь. Наверное.',
    '🍿 Готовь попкорн: твоё выступление моего величества будет чуть позже!',
    '🔧 Заявка принята. Смазываю механизм очереди сарказмом!',
    '🏆 Даже гении не могут быть сразу везде. Хотя я стараюсь!',
)
FULL_MESSAGES = (
    '🚧 Очередь забита до блеска! Приходи чуть позже.',
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
        text = choose_status(previous, kind=item.get('kind', 'video'))
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


async def _save_sent_media_history(item, sent):
    """Store TTS input after confirmed delivery, without recognizing or resending.

    The deterministic id makes a repeated bookkeeping attempt harmless. A lost
    Telegram response or a crash before this write can still leave no history.
    """
    message_id = getattr(sent, 'message_id', None)
    answer = item.get('answer')
    if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id <= 0:
        return
    if not isinstance(answer, str) or not answer.strip():
        return
    label = 'Голосовое' if item.get('kind', 'video') == 'voice' else 'Кружок'
    document = {
        '_id': f"bender-media:{item['chat_id']}:{message_id}",
        'message_id': message_id,
        'chat_id': item['chat_id'],
        'message_thread_id': item.get('thread_id'),
        'user_name': '🤖 Бендер',
        'text': f'[{label} Бендера, текст для озвучки: {answer}]',
        'audio_status': 'ready',
        'audio_transcript': answer,
        'audio_source': 'tts_input',
        'reactions': [],
        'createdAt': get_msk_now(),
    }
    try:
        await asyncio.wait_for(db.messages.update_one(
            {'_id': document['_id']}, {'$setOnInsert': document}, upsert=True), timeout=1.0)
    except Exception as exc:
        # The message is already delivered: a DB failure must never trigger a resend.
        logger.warning('Не удалось сохранить отправленное медиа в историю: %s', type(exc).__name__)


async def process_queued_video(queue, bot, item):
    """Run or safely recover the head; return True for confirmed worker busy."""
    job_id = item['id']
    stage = item['stage']
    is_voice = item.get('kind', 'video') == 'voice'
    uncertain_delivery = (
        '🤖 Связь прервалась во время отправки голосового. Проверь чат: возможно, оно уже пришло.' if is_voice else
        '🤖 Связь прервалась во время отправки кружка. Проверь чат: возможно, он уже пришёл.'
    )
    if stage == 'reporting':
        await queue.remove(job_id)
        await _delete_status(bot, item)
        return False
    if stage == 'sending':
        await _report_and_remove(queue, bot, item, uncertain_delivery)
        return False
    if stage == 'ready' and item.get('retry_after', 0) > time.time():
        return True

    first_status = choose_status(kind=item.get('kind', 'video'))
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
                    f"РОЛЬ: {apply_bender_tone(persona.get('prompt', 'Самоуверенный робот Бендер.'))}\n"
                    f"СООБЩЕНИЕ ДЛЯ КОНТЕКСТА: {item['context'] or '(нет)'}\n"
                    f"ВОПРОС ПОЛЬЗОВАТЕЛЯ: {item['query'] or 'Прокомментируй сообщение.'}\n"
                    'Ответь на русском от лица персонажа, примерно 300–400 символов, не более 450 символов, '
                    'с законченными предложениями, без разметки и эмодзи. '
                    + ('Верни только произносимый текст, без пояснений и JSON.' if is_voice else
                       'Дополнительно придумай короткую юмористическую надпись по теме сообщения для таблички: от 1 до 3 слов, '
                       'всего не больше 22 символов, каждое слово не больше 12 символов. '
                       'Только русские или латинские буквы, цифры, пробелы и знаки ! ? , . -. '
                       'Верни строго один JSON-объект с полями "speech" (только произносимый ответ) '
                       'и "sign" (только надпись). Без пояснений и Markdown; sign не произносится.')
                )
                generated = await asyncio.wait_for(generate_with_retry(prompt), timeout=45)
                speech, sign = parse_generated(generated)
                answer = spoken_text(speech)
                if not answer:
                    raise ValueError('empty response')
                if not is_voice and not sign:
                    logger.warning('Модель не вернула подходящую надпись для таблички; кружок будет без неё')
            except Exception as exc:
                logger.warning('Не удалось подготовить ответ для медиа: %s', type(exc).__name__)
                await _report_and_remove(queue, bot, item, '🤖 Мои гениальные схемы не придумали ответ. Попробуй позже.')
                return False
            await queue.update(job_id, stage='ready', answer=answer, sign=sign)
            item['answer'] = answer
            item['sign'] = sign
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
                    if is_voice:
                        file_id = await client.render_voice(item['answer'], request_id=item['request_id'])
                    elif item.get('sign'):
                        file_id = await client.render(item['answer'], request_id=item['request_id'], sign=item['sign'])
                    else:
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
                    f"🤖 Голосовое не получилось. Зато вот мой гениальный ответ:\n{item['answer']}" if is_voice else
                    f"🤖 Кружок не получился. Зато вот мой гениальный ответ:\n{item['answer']}")
                return False
            except (WorkerError, ValueError) as exc:
                logger.warning('Неизвестен результат создания медиа: %s', type(exc).__name__)
                await _report_and_remove(queue, bot, item,
                    f"🤖 Связь со студией прервалась, и я не знаю, готово ли голосовое. Вот ответ:\n{item['answer']}" if is_voice else
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
                video = await (client.fetch_uncertain_voice(item['request_id']) if is_voice else
                               client.fetch_uncertain_video(item['request_id']))
            except (WorkerError, ValueError) as exc:
                logger.warning('Не удалось получить сохранённое медиа: %s', type(exc).__name__)
                await _report_and_remove(queue, bot, item,
                    f"🤖 Связь со складом прервалась, и я не смог получить готовое голосовое. Вот ответ:\n{item['answer']}" if is_voice else
                    f"🤖 Связь со складом прервалась, и я не смог получить готовый кружок. Вот ответ:\n{item['answer']}")
                return False
            # If the worker or caller dies after this marker, do not retry the
            # ambiguous Telegram send; the user is asked to check the chat.
            await queue.update(job_id, stage='sending')
            try:
                if is_voice:
                    sent = await bot.send_voice(voice=types.BufferedInputFile(video, filename='voice.ogg'),
                                                **stored_reply_options(item))
                else:
                    sent = await bot.send_video_note(video_note=types.BufferedInputFile(video, filename='video.mp4'),
                                                     **stored_reply_options(item))
            except TelegramBadRequest:
                await _report_and_remove(queue, bot, item,
                    f"🤖 Telegram не принял голосовое. Вот мой ответ:\n{item['answer']}" if is_voice else
                    uncertain_delivery)
                return False
            except Exception as exc:
                logger.warning('Неизвестен результат резервной отправки медиа: %s', type(exc).__name__)
                await _report_and_remove(queue, bot, item,
                    uncertain_delivery)
                return False
            await _save_sent_media_history(item, sent)
            await queue.remove(job_id)
            await _delete_status(bot, item)
            return False

        if stage == 'file_ready':
            await queue.update(job_id, stage='sending')
            try:
                if is_voice:
                    sent = await bot.send_voice(voice=item['file_id'], **stored_reply_options(item))
                else:
                    sent = await bot.send_video_note(video_note=item['file_id'], **stored_reply_options(item))
            except TelegramBadRequest:
                await _report_and_remove(queue, bot, item,
                    f"🤖 Telegram не принял голосовое. Вот мой ответ:\n{item['answer']}" if is_voice else
                    uncertain_delivery)
                return False
            except Exception as exc:
                logger.warning('Неизвестен результат отправки медиа: %s', type(exc).__name__)
                await _report_and_remove(queue, bot, item,
                    uncertain_delivery)
                return False
            await _save_sent_media_history(item, sent)
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
@router.message(F.photo, F.caption.regexp(EX_VIDEO_PATTERN))
async def video_reply(message: types.Message):
    if message.chat.type not in ('group', 'supergroup'):
        return
    flags = await get_chat_feature_flags(message.chat.id)
    if not flags.get('video_notes', False):
        return
    query = parse_video_request(message.text or getattr(message, 'caption', None))
    if query is None:
        return
    context = reply_context(message)
    if has_request_photo(message):
        context = await add_photo_context(message, query or 'Прокомментируй фото.', context)
        if context is None:
            return
    context = await add_voice_reply_context(message, context)
    if context is None:
        return
    context = await add_recent_context(message, context, getattr(db, 'messages', None))
    return await enqueue_video_reply(message, query, context)


async def enqueue_video_reply(message, query, context):
    return await _enqueue_media_reply(message, query, context, kind='video')


async def enqueue_voice_reply(message, query, context):
    return await _enqueue_media_reply(message, query, context, kind='voice')


async def _enqueue_media_reply(message, query, context, *, kind):
    """One shared FIFO for voice and video; never enqueue one request twice."""
    if not query and not context:
        await reply_text_safely(message, 'Укажи тему или ответь командой на сообщение.')
        return
    url = os.getenv('BENDER_WORKER_URL', '').strip()
    key = os.getenv('BENDER_WORKER_KEY', '').strip()
    if kind == 'video':
        try:
            BenderWorkerClient(url, key)
        except ValueError:
            await reply_text_safely(message, '🤖 Студия кружков пока не настроена.')
            return
    # Voice jobs still enter the queue if the studio is unconfigured: the
    # worker error path can then deliver the prepared answer as text.
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
        'query': query, 'context': context, 'stage': 'queued', 'kind': kind,
    }
    try:
        result = await video_queue.enqueue(item)
    except Exception as exc:
        logger.warning('Неизвестен результат постановки в очередь: %s', type(exc).__name__)
        await reply_text_safely(message,
            '🤖 Очередь барахлит, и я не знаю, записал ли запрос. Пока не отправляй его повторно.')
        return
    if result == 'accepted':
        await _edit_status(message.bot, item, ('🎙 Записал голосовой ответ в очередь.' if kind == 'voice' else random.choice(QUEUE_MESSAGES)))
    else:
        if status is not None:
            try:
                await status.delete()
            except Exception:
                pass
        if result == 'full':
            await reply_text_safely(message, random.choice(FULL_MESSAGES))
