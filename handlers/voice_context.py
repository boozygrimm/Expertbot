"""Use a replied-to voice message as explicit context without guessing its contents."""
import asyncio
import logging

from handlers.admin_panel import is_feature_enabled
from handlers.audio_transcription import get_transcription

logger = logging.getLogger(__name__)
REPLY_WAIT_SECONDS = 4


async def add_voice_reply_context(message, context):
    source = getattr(message, 'reply_to_message', None)
    if not source or not (getattr(source, 'voice', None) or getattr(source, 'video_note', None)):
        return context
    if not await is_feature_enabled(message.chat.id, 'voice'):
        await message.reply('🎙 Модуль голосовых сейчас выключен, поэтому я не могу прослушать запись.')
        return None
    try:
        # The shared transcription continues in the background after timeout.
        text = await asyncio.wait_for(get_transcription(source), timeout=REPLY_WAIT_SECONDS)
    except asyncio.TimeoutError:
        await message.reply('🎙 Запись ещё распознаётся. Попробуй спросить снова чуть позже.')
        return None
    except Exception as exc:
        logger.warning('Не удалось получить контекст голосового: %s', type(exc).__name__)
        text = None
    if not text:
        await message.reply('🎙 Не смог разобрать запись, поэтому не стану выдумывать её содержание.')
        return None
    label = 'Текст записи (распознавание или текст для озвучки; может содержать ошибки и отличаться от аудио): '
    prefix = (context or '')[:3200]
    available = max(0, 3900 - len(prefix) - len(label) - 2)
    return (prefix + '\n\n' if prefix else '') + label + text[:available]
