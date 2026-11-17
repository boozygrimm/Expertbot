"""One opt-in owner-only review delivery, never a channel publication.

The delivery stage is persisted before Telegram is contacted. An ambiguous
result is terminal until manually checked; it is never retried automatically.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
from aiogram.types import FSInputFile
from pymongo import ReturnDocument

MAX_BOT_VIDEO_BYTES = 50 * 1024 * 1024  # Current standard Bot API upload route.


def caption(candidate: dict | None) -> str:
    candidate = candidate or {}
    title = candidate.get('title') or candidate.get('text') or 'Новость от владельца'
    title = ' '.join(str(title).split())[:180]
    url = candidate.get('url') or ''
    if not isinstance(url, str):
        url = ''
    # Plain text only. Links are stored as data, never fetched by this function.
    source_line = f'Источник: {url}' if url and len(url) <= 350 else ('Ссылка на источник слишком длинная для подписи.' if url else 'Источник не указан.')
    accent = ' '.join(str(candidate.get('accent') or '').split())[:120]
    published = ' '.join(str(candidate.get('published_at') or '').split())[:40]
    evidence = candidate.get('draft', {}).get('evidence', []) if isinstance(candidate.get('draft'), dict) else []
    quote = next((x.get('quote') for x in evidence if isinstance(x, dict) and x.get('kind') == 'fact' and isinstance(x.get('quote'), str)), '') if isinstance(evidence, list) else ''
    quote = ' '.join(quote.split())[:200]
    return ('Черновик сюжета — факты и подачу нужно проверить перед публикацией.\n'
            + title + '\n' + source_line
            + (f'\nОпубликовано: {published}' if published else '')
            + (f'\nАкцент: {accent}' if accent else '')
            + (f'\nЦитата из карточки: {quote}' if quote else ''))


async def run_once(jobs, candidates, client, bot, admin_id: int) -> str:
    if type(admin_id) is not int or admin_id <= 0:
        raise ValueError('Не задан владелец')
    job = await jobs.find_one_and_update({'stage': 'ready_for_review'},
        {'$set': {'stage': 'fetching_review', 'updated_at': datetime.now(timezone.utc)}},
        sort=[('created_at', 1)], return_document=ReturnDocument.AFTER)
    if not job:
        return 'idle'
    query = {'_id': job['_id'], 'stage': 'fetching_review'}
    sending = False
    try:
        candidate = None if job.get('kind') == 'digest' else await candidates.find_one({'_id': job['candidate_id']})
        with tempfile.TemporaryDirectory(prefix='news-review-') as tmp:
            video = Path(tmp) / 'story.mp4'
            size, checksum = await client.download(job['request_id'], video)
            if size > MAX_BOT_VIDEO_BYTES:
                await jobs.update_one(query, {'$set': {'stage': 'review_too_large'}})
                return 'review_too_large'
            changed = await jobs.update_one(query, {'$set': {'stage': 'sending_review',
                                    'review_sha256': checksum, 'review_size': size}})
            if not changed.modified_count:
                return 'cancelled'
            sending = True
            try:
                if job.get('kind') == 'digest':
                    review_caption = 'Дайджест из готовых сюжетов. Проверь факты и монтаж перед публикацией.'
                elif job.get('kind') == 'bulletin':
                    review_caption = ('Новостной вестник. Прослушай обе роли и полноту речи, проверь факты, '
                                      'изображения и монтаж. Смена говорящих в анимации приблизительная.')
                elif job.get('kind') == 'program':
                    review_caption = 'Выпуск из проверенных сюжетов. Проверь видео и монтаж перед публикацией.'
                else:
                    review_caption = caption(candidate)
                result = await bot.send_video(admin_id, FSInputFile(video), caption=review_caption,
                                              parse_mode=None)
            except Exception:
                await jobs.update_one({'_id': job['_id'], 'stage': 'sending_review'},
                                      {'$set': {'stage': 'review_uncertain'}})
                return 'review_uncertain'
            await jobs.update_one({'_id': job['_id'], 'stage': 'sending_review'},
                                  {'$set': {'stage': 'review_sent', 'review_message_id': result.message_id,
                                            'review_sent_at': datetime.now(timezone.utc)}})
            return 'review_sent'
    except Exception:
        # A failed confirmation after send is still ambiguous; never retry it.
        expected = 'sending_review' if sending else 'fetching_review'
        stage = 'review_uncertain' if sending else 'review_download_failed'
        await jobs.update_one({'_id': job['_id'], 'stage': expected}, {'$set': {'stage': stage}})
        return stage
