"""One explicitly confirmed owner-only source-video preview, never a render.

Caller must authenticate the owner in a private chat and obtain a one-use
confirmation. Viewing does not approve a candidate. A separate owner action may
select the sent file and acknowledge rights, bound to a new bulletin revision.
One persistent attempt prevents duplicate delivery after an ambiguous send.
The exact bytes checked by SHA-256 are sent; the temporary file is not retained.
"""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import tempfile
from pathlib import Path

from pymongo.errors import DuplicateKeyError
from news_prototype import bulletin_limits, bulletin_video_candidate as video, owner_bulletin, owner_drafts

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


async def approve_for_bulletin(db, owner_id: int, number: int, revision: str, section_id: int) -> str:
    """Attach exactly one sent source after explicit owner rights confirmation.

    This only changes a reviewed draft; it does not acquire, enqueue or render.
    The new version includes the reviewed hashes. Any later edit invalidates it.
    """
    key = identity(owner_id, number, revision, section_id)
    try:
        story = await video._current_story(db, owner_id, number, revision, section_id)
        item = await owner_bulletin.get(db, owner_id, number)
        review = await db.news_owner_bulletin_video_reviews.find_one({
            '_id': key, 'owner_id': owner_id, 'bulletin_id': number, 'revision': revision,
            'section_id': section_id, 'stage': 'sent'})
        if not review or type(review.get('message_id')) is not int or review['message_id'] <= 0:
            return 'not_reviewed'
        if (review.get('source_url') != story['url'] or review.get('caption_sha256') != video._digest_text(story['summary'])
                or not item or item.get('revision') != revision or item.get('stage') != 'ready_for_review'):
            return 'stale'
        if any('video' in section for section in item['sections']):
            return 'already_selected'
        sections = copy.deepcopy(item['sections'])
        section = next(part for part in sections if part['id'] == section_id)
        section['video'] = {'section_id': section_id, 'post_url': story['url'],
                            'caption_sha256': review['caption_sha256'], 'video_sha256': review['video_sha256']}
        owner_bulletin._payload_from_sections(sections)
        new_revision = owner_bulletin.revision(item['stories'], sections)
        changed = await db.news_owner_bulletins.update_one({'_id': owner_bulletin._key(owner_id, number),
            'owner_id': owner_id, 'stage': 'ready_for_review', 'revision': revision},
            {'$set': {'sections': sections, 'revision': new_revision, 'verification': 'needs_review'}})
        return 'selected' if changed.modified_count else 'stale'
    except (KeyError, TypeError, ValueError, StopIteration):
        return 'stale'


async def remove_from_bulletin(db, owner_id: int, number: int, revision: str, section_id: int) -> str:
    """Remove selected media before render; no download or queue operation."""
    item = await owner_bulletin.get(db, owner_id, number)
    if (not item or item.get('stage') != 'ready_for_review' or item.get('revision') != revision
            or owner_bulletin.revision(item['stories'], item['sections']) != revision
            or not await owner_bulletin._sources_current(db, owner_id, item)):
        return 'stale'
    sections = copy.deepcopy(item['sections'])
    section = next((s for s in sections if s['id'] == section_id), None)
    if not section or 'video' not in section:
        return 'not_selected'
    section.pop('video')
    owner_bulletin._payload_from_sections(sections)
    new_revision = owner_bulletin.revision(item['stories'], sections)
    result = await db.news_owner_bulletins.update_one({'_id': owner_bulletin._key(owner_id, number),
        'owner_id': owner_id, 'stage': 'ready_for_review', 'revision': revision},
        {'$set': {'sections': sections, 'revision': new_revision, 'verification': 'needs_review'}})
    return 'removed' if result.modified_count else 'stale'
