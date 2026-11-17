"""One explicitly confirmed owner-only source-video preview, never a render.

Caller must authenticate the owner in a private chat and obtain a one-use
confirmation. No candidate is approved here. One persistent, revision-bound
attempt prevents automatic duplicate delivery, including after an ambiguous send.
The exact bytes checked by SHA-256 are sent; the temporary file is not retained.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import tempfile
from pathlib import Path

from pymongo.errors import DuplicateKeyError
from news_prototype import bulletin_limits, bulletin_video_candidate as video, owner_drafts

# Bound memory and local disk use to one source preview per bot process.
_lock = asyncio.Semaphore(1)


def identity(owner_id: int, number: int, revision: str, section_id: int) -> str:
    if (not bulletin_limits.number(owner_id) or not bulletin_limits.number(number)
            or not bulletin_limits.number(section_id) or not isinstance(revision, str)
            or not owner_drafts.REVISION.fullmatch(revision)):
        raise ValueError('Неверные данные просмотра')
    digest = hashlib.sha256(f'video-review-v1:{owner_id}:{number}:{revision}:{section_id}'.encode()).hexdigest()
    return 'bulletin-video-review:' + digest


async def deliver(db, owner_id: int, number: int, revision: str, section_id: int, send) -> str:
    """Download and send once after explicit confirmation; never enqueue/render.

    ``send(owner_id, checked_bytes, caption)`` must send only to that private
    owner. It must raise on uncertain delivery and return a positive message ID
    on confirmed delivery. No retry is performed, even after cancellation or an
    unknown result. The UI must not treat a sent preview as approval or consent.
    """
    key = identity(owner_id, number, revision, section_id)
    try:
        await video._current_story(db, owner_id, number, revision, section_id)
    except (KeyError, TypeError, ValueError):
        return 'stale'
    try:
        await db.news_owner_bulletin_video_reviews.insert_one({
            '_id': key, 'owner_id': owner_id, 'bulletin_id': number,
            'revision': revision, 'section_id': section_id, 'stage': 'acquiring',
            'created_at': datetime.now(timezone.utc)})
    except DuplicateKeyError:
        return 'already_started'
    records = db.news_owner_bulletin_video_reviews
    sending = False
    try:
        async with _lock:
            with tempfile.TemporaryDirectory(prefix='owner-video-review-') as temporary:
                candidate = await video.acquire(db, owner_id, number, revision, section_id, Path(temporary) / 'source')
                if (candidate.owner_id, candidate.bulletin_id, candidate.revision, candidate.section_id) != (
                        owner_id, number, revision, section_id):
                    raise ValueError('Неверная привязка')
                await video.verify(db, candidate)
                data = await asyncio.to_thread(video._check_file, candidate, read_bytes=True)
                await video._current_story(db, owner_id, number, revision, section_id)
                changed = await records.update_one({'_id': key, 'stage': 'acquiring'}, {'$set': {
                    'stage': 'sending', 'video_sha256': candidate.video_sha256,
                    'caption_sha256': candidate.caption_sha256, 'source_url': candidate.post_url,
                    'size': candidate.size, 'duration': candidate.duration,
                    'width': candidate.width, 'height': candidate.height, 'has_audio': candidate.has_audio}})
                if not changed.modified_count:
                    return 'uncertain'
                sending = True
                caption = (f'Исходное видео для проверки: вестник №{number}, новость №{section_id}.\n'
                           f'Источник: {candidate.post_url}\nSHA-256: {candidate.video_sha256}\n'
                           'Просмотри видео и звук; проверь права отдельно. Это не одобрение и не рендер.')
                message_id = await send(owner_id, data, caption)
                if type(message_id) is not int or message_id <= 0:
                    raise ValueError('Нет подтверждения отправки')
                changed = await records.update_one({'_id': key, 'stage': 'sending'}, {'$set': {
                    'stage': 'sent', 'message_id': message_id, 'sent_at': datetime.now(timezone.utc)}})
                return 'sent' if changed.modified_count else 'uncertain'
    except Exception:
        # No exception message is persisted: network and media errors might
        # contain signed URLs or other sensitive operational details.
        try:
            await records.update_one({'_id': key, 'stage': 'sending' if sending else 'acquiring'},
                                     {'$set': {'stage': 'uncertain' if sending else 'failed'}})
        except Exception:
            pass  # acquiring/sending remains blocked; never retry blindly
        return 'uncertain' if sending else 'failed'
