"""Low-priority news dispatch while the existing media queue lease is held.

This does not generate scripts, schedule polling, or send media. Call only from
the ordinary queue's idle hook. A persisted submitting stage prevents replay
when a POST or process result is uncertain.
"""
from __future__ import annotations

from datetime import datetime, timezone
import uuid
import re
from news_prototype.news_worker_client import NewsBusy, NewsConflict, NewsUncertain, NewsWorkerError
from news_prototype.story_audio import validate_script

NAMESPACE = uuid.UUID('c316a6ce-9e68-42e9-a29f-0fa4d449b5ea')


async def enqueue_script(collection, candidate_id: str, script: dict) -> bool:
    if not isinstance(candidate_id, str) or not candidate_id or len(candidate_id) > 256:
        raise ValueError('Неверный ID новости')
    validated = validate_script(script)
    request_id = str(uuid.uuid5(NAMESPACE, candidate_id))
    doc = {'candidate_id': candidate_id, 'request_id': request_id, 'script': validated,
           'stage': 'queued', 'created_at': datetime.now(timezone.utc)}
    result = await collection.update_one({'_id': request_id}, {'$setOnInsert': doc}, upsert=True)
    if result.upserted_id is not None:
        return True
    existing = await collection.find_one({'_id': request_id})
    if not existing or existing.get('candidate_id') != candidate_id or existing.get('script') != validated:
        raise ValueError('Для этой новости уже сохранён другой сценарий')
    return False


READY_STORY_STAGES = ('ready_for_review', 'fetching_review', 'sending_review', 'review_sent',
                      'review_uncertain', 'review_too_large', 'review_download_failed')


async def resolve_story_codes(collection, codes: list[str]) -> list[str]:
    """Resolve displayed eight-character prefixes only when globally unambiguous."""
    if not isinstance(codes, list) or len(codes) < 2:
        raise ValueError('Нужны хотя бы два сюжета')
    from news_prototype.news_worker_client import valid_id
    resolved = []
    for code in codes:
        if not isinstance(code, str):
            raise ValueError('Неверный код')
        if re.fullmatch(r'[0-9a-f]{8}', code):
            matches = await collection.find({'_id': {'$regex': '^' + code}}).limit(2).to_list(length=2)
            if len(matches) != 1:
                raise ValueError('Код не найден или неоднозначен')
            resolved.append(valid_id(matches[0]['_id']))
        else:
            resolved.append(valid_id(code))
    return resolved


async def enqueue_digest(collection, owner_id: int, message_id: int, inputs: list[str]) -> bool:
    from news_prototype.news_worker_client import valid_id
    if type(owner_id) is not int or owner_id <= 0 or type(message_id) is not int or message_id <= 0:
        raise ValueError('Неверный запрос')
    if not isinstance(inputs, list) or len(inputs) < 2:
        raise ValueError('Нужны хотя бы два сюжета')
    for value in inputs:
        valid_id(value)
    if len(set(inputs)) != len(inputs):
        raise ValueError('Сюжеты не должны повторяться')
    for value in inputs:
        source = await collection.find_one({'_id': value, 'stage': {'$in': list(READY_STORY_STAGES)}})
        if not source or source.get('kind', 'story') != 'story':
            raise ValueError('Доступны только готовые отдельные сюжеты')
    request_id = str(uuid.uuid5(NAMESPACE, f'digest:{owner_id}:{message_id}'))
    doc = {'kind': 'digest', 'inputs': inputs, 'request_id': request_id, 'stage': 'queued',
           'created_at': datetime.now(timezone.utc)}
    result = await collection.update_one({'_id': request_id}, {'$setOnInsert': doc}, upsert=True)
    if result.upserted_id is not None:
        return True
    existing = await collection.find_one({'_id': request_id})
    if not existing or existing.get('kind') != 'digest' or existing.get('inputs') != inputs:
        raise ValueError('Для этого запроса уже выбраны другие сюжеты')
    return False


async def _advance(collection, job, from_stage: str, stage: str) -> None:
    await collection.update_one({'_id': job['_id'], 'stage': from_stage},
                                {'$set': {'stage': stage, 'updated_at': datetime.now(timezone.utc)}})


async def run_idle_once(queue, collection, client) -> None:
    """Called with media queue lease; user requests always checked first."""
    if await queue.head():
        return
    pending = await collection.find_one({'stage': {'$in': ['submitting', 'running']}}, sort=[('created_at', 1)])
    if pending:
        stage = pending['stage']
        try:
            state = await client.status(pending['request_id'])
        except NewsWorkerError:
            # Check again later, never submit a second time.
            return
        if state is None:
            await _advance(collection, pending, stage, 'uncertain')
        else:
            next_stage = {'running': 'running', 'ready': 'ready_for_review',
                          'failed': 'failed', 'interrupted': 'interrupted'}.get(state.get('status'), 'uncertain')
            await _advance(collection, pending, stage, next_stage)
        return
    job = await collection.find_one({'stage': 'queued'}, sort=[('created_at', 1)])
    if not job or await queue.head():
        return
    changed = await collection.update_one({'_id': job['_id'], 'stage': 'queued'},
                                          {'$set': {'stage': 'submitting', 'updated_at': datetime.now(timezone.utc)}})
    if not changed.modified_count:
        return
    # Close the most common priority race after claiming, and fence the lease.
    # If a user request arrives while the worker is already rendering, it must
    # wait: the worker cannot safely preempt a partially rendered story.
    if await queue.head():
        await _advance(collection, job, 'submitting', 'queued')
        return
    try:
        if job.get('kind') == 'digest':
            state = await client.submit_digest(job['request_id'], job['inputs'])
        else:
            state = await client.submit(job['request_id'], job['script'])
    except NewsBusy:
        await _advance(collection, job, 'submitting', 'queued')
        return
    except NewsConflict:
        await _advance(collection, job, 'submitting', 'interrupted')
        return
    except (NewsUncertain, NewsWorkerError):
        await _advance(collection, job, 'submitting', 'uncertain')
        return
    next_stage = {'running': 'running', 'ready': 'ready_for_review',
                  'failed': 'failed', 'interrupted': 'interrupted'}.get(state.get('status'), 'uncertain')
    await _advance(collection, job, 'submitting', next_stage)
