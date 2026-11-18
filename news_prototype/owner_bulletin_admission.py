"""Owner-confirmed, fail-closed admission of one reviewed bulletin.

A CAS freezes the reviewed document before any job is inserted. The shared
owner slot is reserved before the queue document is created. Cross-collection
writes are intentionally not treated as a transaction: an ambiguous failure
stays blocked for inspection and never triggers an automatic retry.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import uuid
from news_prototype import news_queue, owner_bulletin, owner_drafts, owner_trial


def identity(owner_id: int, number: int, revision: str) -> tuple[str, str]:
    owner_bulletin._key(owner_id, number)
    if not isinstance(revision, str) or not owner_drafts.REVISION.fullmatch(revision):
        raise ValueError('Неверная версия')
    digest = hashlib.sha256(f'bulletin-v1:{owner_id}:{number}:{revision}'.encode()).hexdigest()
    candidate_id = 'owner-trial:' + digest
    return candidate_id, str(uuid.uuid5(news_queue.NAMESPACE, candidate_id))


async def status(db, owner_id: int, number: int) -> tuple[str, str | None]:
    item = await owner_bulletin.get(db, owner_id, number)
    if not item:
        return 'not_found', None
    if item.get('stage') not in ('approving', 'approval_uncertain', 'queued', 'shortening'):
        return item.get('stage', 'unknown'), None
    try:
        candidate_id, request_id = identity(owner_id, number, item['revision'])
    except (KeyError, ValueError):
        return 'approval_uncertain', None
    if item.get('candidate_id') != candidate_id or item.get('request_id') != request_id:
        return 'approval_uncertain', None
    job = await db.news_owner_trial_jobs.find_one({'_id': request_id, 'request_id': request_id,
                                                     'candidate_id': candidate_id, 'kind': 'bulletin'})
    if not job:
        return ('shortening' if item['stage'] == 'shortening' else 'approval_uncertain'), request_id
    return job.get('stage', 'unknown'), request_id


async def approve(db, owner_id: int, number: int, expected: str) -> str:
    candidate_id, request_id = identity(owner_id, number, expected)
    item = await owner_bulletin.get(db, owner_id, number)
    if not item or item.get('revision') != expected:
        return 'stale'
    if item.get('stage') in ('approving', 'approval_uncertain', 'queued', 'shortening'):
        return 'already_processed'
    if item.get('stage') != 'ready_for_review':
        return 'not_ready'
    try:
        payload = await owner_bulletin.approved_payload(db, owner_id, number, expected)
    except (KeyError, ValueError, TypeError):
        return 'stale'
    # Never allow a previously synthesized revision to be submitted again,
    # even after a duration failure and reopening for edits.
    if await db.news_owner_trial_jobs.find_one({'_id': request_id}):
        return 'revision_used'
    stage, finished_id = await owner_trial.status_info(db)
    if stage not in ('not_queued', 'review_sent', 'failed') or (stage != 'not_queued' and not finished_id):
        return 'trial_busy'
    key = owner_bulletin._key(owner_id, number)
    claimed = await db.news_owner_bulletins.update_one(
        {'_id': key, 'owner_id': owner_id, 'stage': 'ready_for_review', 'revision': expected},
        {'$set': {'stage': 'approving', 'candidate_id': candidate_id, 'request_id': request_id}})
    if not claimed.modified_count:
        return 'changed'
    try:
        # Recheck after winning against concurrent edits. The queued payload is
        # exactly the snapshot displayed, never a live reread of source text.
        frozen = await owner_bulletin.get(db, owner_id, number)
        if (not frozen or frozen.get('revision') != expected or
                owner_bulletin.revision(frozen['stories'], frozen['sections']) != expected or
                owner_bulletin._payload_from_sections(frozen['sections']) != payload or
                not await owner_bulletin._sources_current(db, owner_id, frozen)):
            await db.news_owner_bulletins.update_one({'_id': key, 'stage': 'approving', 'revision': expected},
                {'$set': {'stage': 'ready_for_review'}, '$unset': {'candidate_id': '', 'request_id': ''}})
            return 'stale'
        if not await owner_trial.runtime_enabled(db):
            await db.news_owner_bulletins.update_one({'_id': key, 'stage': 'approving', 'revision': expected},
                {'$set': {'stage': 'ready_for_review'}, '$unset': {'candidate_id': '', 'request_id': ''}})
            return 'disabled'
        if finished_id:
            release = owner_trial.release_failed_for_bulletin if stage == 'failed' else owner_trial.open_next
            if await release(db, owner_id, finished_id) not in ('opened', 'already_open'):
                raise ValueError('Слот не освобождён')
        # Refuse to claim a newly open slot if a previously issued job exists.
        if await db.news_owner_trial_jobs.find_one({'_id': request_id}):
            raise ValueError('Версия уже использована')
        await db.news_owner_trial_gate.update_one({'_id': 'slot'}, {'$setOnInsert': {
            'candidate_id': candidate_id, 'created_at': datetime.now(timezone.utc)}}, upsert=True)
        gate = await db.news_owner_trial_gate.find_one({'_id': 'slot'})
        if gate and gate.get('candidate_id') is None and gate.get('ready_for_next') is True:
            await db.news_owner_trial_gate.update_one({'_id': 'slot', 'candidate_id': None, 'ready_for_next': True},
                {'$set': {'candidate_id': candidate_id, 'ready_for_next': False, 'created_at': datetime.now(timezone.utc)}})
            gate = await db.news_owner_trial_gate.find_one({'_id': 'slot'})
        if not gate or gate.get('candidate_id') != candidate_id:
            raise ValueError('Слот занят')
        doc = {'owner_id': owner_id, 'source_id': 'owner_trial', 'kind': 'bulletin',
               'bulletin_id': number, 'revision': expected, 'title': 'Новостной вестник',
               'verification': 'needs_review'}
        await db.news_owner_trial_candidates.update_one({'_id': candidate_id}, {'$setOnInsert': doc}, upsert=True)
        stored = await db.news_owner_trial_candidates.find_one({'_id': candidate_id})
        if not stored or any(stored.get(k) != v for k, v in doc.items()):
            raise ValueError('Карточка не совпадает')
        await news_queue.enqueue_bulletin(db.news_owner_trial_jobs, candidate_id, payload)
        await db.news_owner_bulletins.update_one({'_id': key, 'stage': 'approving', 'revision': expected},
                                                 {'$set': {'stage': 'queued'}})
        return 'queued'
    except Exception:
        await db.news_owner_bulletins.update_one({'_id': key, 'stage': 'approving', 'revision': expected},
                                                 {'$set': {'stage': 'approval_uncertain'}})
        return 'approval_uncertain'


async def reopen_too_long(db, owner_id: int, number: int, expected: str) -> str:
    """Explicitly free only a confirmed duration rejection; no render starts."""
    candidate_id, request_id = identity(owner_id, number, expected)
    key = owner_bulletin._key(owner_id, number)
    item = await owner_bulletin.get(db, owner_id, number)
    if not item or item.get('revision') != expected or item.get('candidate_id') != candidate_id:
        return 'stale'
    job = await db.news_owner_trial_jobs.find_one({'_id': request_id, 'request_id': request_id,
        'candidate_id': candidate_id, 'kind': 'bulletin', 'stage': 'audio_too_long'})
    candidate = await db.news_owner_trial_candidates.find_one({'_id': candidate_id, 'owner_id': owner_id,
        'kind': 'bulletin', 'bulletin_id': number, 'revision': expected})
    if not job or not candidate:
        return 'not_allowed'
    # A persisted intermediate stage makes an interruption recoverable with
    # the same explicit action; no job creation occurs on this path.
    if item.get('stage') != 'shortening':
        changed = await db.news_owner_bulletins.update_one({'_id': key, 'owner_id': owner_id,
            'revision': expected, 'candidate_id': candidate_id, 'stage': {'$in': ['queued', 'approving', 'approval_uncertain']}},
            {'$set': {'stage': 'shortening'}})
        if not changed.modified_count:
            return 'changed'
    gate = await db.news_owner_trial_gate.find_one({'_id': 'slot'})
    if gate and gate.get('candidate_id') == candidate_id:
        await db.news_owner_trial_gate.update_one({'_id': 'slot', 'candidate_id': candidate_id},
            {'$set': {'candidate_id': None, 'ready_for_next': True, 'last_released_request_id': request_id,
                      'released_at': datetime.now(timezone.utc)}})
        gate = await db.news_owner_trial_gate.find_one({'_id': 'slot'})
    if not gate or gate.get('candidate_id') is not None or gate.get('ready_for_next') is not True or gate.get('last_released_request_id') != request_id:
        return 'shortening'
    changed = await db.news_owner_bulletins.update_one({'_id': key, 'owner_id': owner_id,
        'revision': expected, 'stage': 'shortening', 'candidate_id': candidate_id},
        {'$set': {'stage': 'ready_for_review'}, '$unset': {'candidate_id': '', 'request_id': ''}})
    return 'reopened' if changed.modified_count else 'changed'
