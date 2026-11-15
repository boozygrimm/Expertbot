"""Photo context for direct requests (separate from background history logging)."""
import logging

from ai_logic import describe_request_photo

logger = logging.getLogger(__name__)
PHOTO_ERROR = '🤖 Не смог рассмотреть фото: распознавание сейчас недоступно. Попробуй ещё раз позже.'


def has_request_photo(message):
    return bool(getattr(message, 'photo', None) or
                (message.reply_to_message and getattr(message.reply_to_message, 'photo', None)))


async def add_photo_context(message, query, context):
    """Return enriched context, or None if any requested photo could not be seen."""
    sources = [(message, 'Фото в запросе'), (message.reply_to_message, 'Фото в сообщении, на которое ответили')]
    descriptions = []
    for source, label in sources:
        photos = getattr(source, 'photo', None) if source else None
        if not photos:
            continue
        try:
            description = await describe_request_photo(message.bot, photos[-1].file_id, query)
        except Exception as exc:
            logger.warning('Не удалось распознать фото: %s', type(exc).__name__)
            description = None
        if not description:
            await message.reply(PHOTO_ERROR)
            return None
        descriptions.append(f'{label}: {description[:1400]}')
    # Keep the descriptions within the video queue's 4000-character context limit.
    parts = descriptions + ([f'Текст исходного сообщения: {context[:900]}'] if context else [])
    return '\n'.join(parts)
