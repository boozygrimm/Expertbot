"""Bounded, shared speech transcription for history and explicit voice requests.

Only in-flight jobs are shared between handlers in this process. The completed
transcript is stored with the original message, never the audio file itself.
"""
import asyncio
import logging
import os
import tempfile

from ai_logic import transcribe_audio_groq
from database import db
from background import spawn

logger = logging.getLogger(__name__)
MAX_PENDING = 20
MAX_CONCURRENT = 3
MAX_FILE_BYTES = 20 * 1024 * 1024  # Telegram bot downloads are limited to 20 MB.
MAX_TRANSCRIPT_CHARS = 12000
_jobs = {}
_semaphore = None
_semaphore_loop = None


def _get_semaphore():
    global _semaphore, _semaphore_loop
    loop = asyncio.get_running_loop()
    if _semaphore is None or _semaphore_loop is not loop:
        _semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        _semaphore_loop = loop
    return _semaphore


def _media(message):
    voice = getattr(message, 'voice', None)
    note = getattr(message, 'video_note', None)
    return (voice, 'ogg', 'Голосовое') if voice else ((note, 'mp4', 'Кружок') if note else (None, '', ''))


def _key(message):
    return (message.chat.id, message.message_id)


def short_transcript(text, max_units=3000):
    """Bound Telegram output in UTF-16 units without cutting a character."""
    used = 0
    for index, char in enumerate(text):
        used += 2 if ord(char) > 0xffff else 1
        if used > max_units:
            return text[:index] + '…'
    return text


async def _store_result(message, text):
    _, _, label = _media(message)
    caption = (getattr(message, 'caption', None) or '').strip()
    marker = f'[{label}: расшифровка не удалась]' if not text else f'[{label}, распознана речь: {text}]'
    fields = {'text': f'{caption} {marker}'.strip(), 'audio_status': 'ready' if text else 'failed'}
    if text:
        fields['audio_transcript'] = text
    try:
        # Only update history placeholders, never replace an unrelated message.
        await asyncio.wait_for(db.messages.update_many(
            {'chat_id': message.chat.id, 'message_id': message.message_id,
             'audio_status': {'$in': ['pending', 'failed']}}, {'$set': fields}), timeout=1.0)
    except Exception as exc:
        logger.warning('Не удалось сохранить расшифровку: %s', type(exc).__name__)


async def _run(message):
    media, extension, _ = _media(message)
    text = None
    try:
        if not media or (getattr(media, 'file_size', None) or 0) > MAX_FILE_BYTES:
            return None
        async with _get_semaphore():
            with tempfile.TemporaryDirectory(prefix='expertbot-audio-') as folder:
                path = os.path.join(folder, f'audio.{extension}')
                await asyncio.wait_for(message.bot.download(media.file_id, destination=path), timeout=40)
                if os.path.getsize(path) > MAX_FILE_BYTES:
                    return None
                result = await asyncio.wait_for(transcribe_audio_groq(path), timeout=120)
                if isinstance(result, str) and result.strip() and '❌' not in result:
                    text = result.strip()[:MAX_TRANSCRIPT_CHARS]
        return text
    except Exception as exc:
        logger.warning('Не удалось расшифровать аудио: %s', type(exc).__name__)
        return None
    finally:
        await _store_result(message, text)


def start_transcription(message):
    """Start once per message; return None when the bounded queue is full."""
    key = _key(message)
    if key in _jobs:
        return _jobs[key]
    if len(_jobs) >= MAX_PENDING or not _media(message)[0]:
        return None
    task = spawn(_run(message), name='transcription')
    _jobs[key] = task

    def finished(done):
        # Освобождаем ключ всегда: и после успеха, и после падения (его уже
        # залогировал spawn).
        if _jobs.get(key) is done:
            _jobs.pop(key, None)

    task.add_done_callback(finished)
    return task


async def get_transcription(message, *, retry_failed=True):
    """Reuse a shared result; only explicit requests retry a recorded failure."""
    task = _jobs.get(_key(message))
    if task:
        return await asyncio.shield(task)
    try:
        saved = await asyncio.wait_for(db.messages.find_one(
            {'chat_id': message.chat.id, 'message_id': message.message_id,
             'audio_status': {'$in': ['ready', 'failed']}},
            {'audio_transcript': 1, 'audio_status': 1}), timeout=1.0)
        if saved and saved.get('audio_transcript'):
            return saved['audio_transcript']
        if saved and saved.get('audio_status') == 'failed' and not retry_failed:
            return None
    except Exception as exc:
        logger.warning('Не удалось получить сохранённую расшифровку: %s', type(exc).__name__)
    task = start_transcription(message)
    return await asyncio.shield(task) if task else None
