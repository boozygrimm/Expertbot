"""Owner-reviewed snapshots of several drafts, approved together as one job."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import uuid
from news_prototype import owner_drafts, owner_trial


def key(owner_id: int, program_id: int) -> str:
    if type(owner_id) is not int or owner_id <= 0 or type(program_id) is not int or program_id <= 0:
        raise ValueError('Неверный номер')
    return f'owner-program:{owner_id}:{program_id}'


def revision(stories: list[dict]) -> str:
    data = json.dumps(stories, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(data).hexdigest()


async def eligible_draft(db, owner_id: int, item: dict | None) -> bool:
    """Allow ready drafts or exact single-story jobs with confirmed delivery."""
    if not item or item.get('owner_id') != owner_id:
        return False
    if item.get('stage') == 'draft_ready':
        return True
    if item.get('stage') not in ('queued', 'already_queued'):
        return False
    try:
        url, script, candidate_id = owner_trial.single_candidate(owner_id, item['url'], item['script'])
        if item.get('revision') != owner_drafts.revision(url, script):
            return False
        request_id = str(uuid.uuid5(owner_trial.NAMESPACE, candidate_id))
        candidate = await db.news_owner_trial_candidates.find_one({'_id': candidate_id, 'owner_id': owner_id})
        if not candidate or candidate.get('url') != url or candidate.get('script') != script:
            return False
        job = await db.news_owner_trial_jobs.find_one({'_id': request_id, 'request_id': request_id,
            'candidate_id': candidate_id, 'stage': 'review_sent'})
        message_id = job.get('review_message_id') if job else None
        return bool(job and job.get('kind', 'story') == 'story' and job.get('script') == script and
                    type(message_id) is int and message_id > 0)
    except (KeyError, TypeError, ValueError):
        return False


async def create(db, owner_id: int, program_id: int, draft_ids: list[int]) -> bool:
    program_key = key(owner_id, program_id)
    if not isinstance(draft_ids, list) or not 2 <= len(draft_ids) <= 4 or len(set(draft_ids)) != len(draft_ids):
        raise ValueError('Нужны от двух до четырёх разных черновиков')
    stories = []
    for draft_id in draft_ids:
        item = await owner_drafts.get(db, owner_id, draft_id)
        if not await eligible_draft(db, owner_id, item):
            raise ValueError('Нужны готовые к проверке черновики')
        script = item['script']
        if item.get('revision') != owner_drafts.revision(item['url'], script):
            raise ValueError('Версия черновика повреждена')
        owner_drafts.editable_message(draft_id, item['url'], script)
        stories.append({'draft_id': draft_id, 'revision': item['revision'], 'url': item['url'],
                        'script': script, 'image_kind': item.get('image_kind')})
    doc = {'owner_id': owner_id, 'program_id': program_id, 'stage': 'ready_for_review',
           'stories': stories, 'revision': revision(stories), 'created_at': datetime.now(timezone.utc)}
    result = await db.news_owner_programs.update_one({'_id': program_key}, {'$setOnInsert': doc}, upsert=True)
    if result.upserted_id is not None:
        return True
    existing = await db.news_owner_programs.find_one({'_id': program_key})
    if not existing or existing.get('owner_id') != owner_id or existing.get('revision') != doc['revision']:
        raise ValueError('Этот номер уже занят другой версией выпуска')
    return False


async def get(db, owner_id: int, program_id: int) -> dict | None:
    return await db.news_owner_programs.find_one({'_id': key(owner_id, program_id), 'owner_id': owner_id})


async def approve(db, owner_id: int, program_id: int, expected: str) -> str:
    if not isinstance(expected, str) or not owner_drafts.REVISION.fullmatch(expected):
        raise ValueError('Неверная версия')
    program_key = key(owner_id, program_id)
    program = await get(db, owner_id, program_id)
    if not program or program.get('revision') != expected:
        return 'stale'
    if program.get('stage') in ('queued', 'already_queued'):
        return 'already_queued'
    if program.get('stage') != 'ready_for_review':
        return 'not_ready'
    if revision(program['stories']) != expected:
        return 'stale'
    for story in program['stories']:
        draft = await owner_drafts.get(db, owner_id, story['draft_id'])
        if not await eligible_draft(db, owner_id, draft) or draft.get('revision') != story['revision']:
            return 'stale_draft'
    stage, finished_id = await owner_trial.status_info(db)
    if stage not in ('not_queued', 'review_sent') or (stage == 'review_sent' and not finished_id):
        return 'trial_busy'
    changed = await db.news_owner_programs.update_one(
        {'_id': program_key, 'owner_id': owner_id, 'stage': 'ready_for_review', 'revision': expected},
        {'$set': {'stage': 'approving'}})
    if not changed.modified_count:
        return 'changed'
    try:
        if finished_id:
            opened = await owner_trial.open_next(db, owner_id, finished_id)
            if opened not in ('opened', 'already_open'):
                await db.news_owner_programs.update_one({'_id': program_key, 'stage': 'approving'},
                                                        {'$set': {'stage': 'approval_uncertain'}})
                return 'approval_uncertain'
        stories = [{'url': story['url'], 'script': story['script']} for story in program['stories']]
        result = await owner_trial.admit_program(db, owner_id, stories)
        final = 'queued' if result == 'queued' else 'already_queued'
        await db.news_owner_programs.update_one({'_id': program_key, 'stage': 'approving'}, {'$set': {'stage': final}})
        return final
    except Exception:
        await db.news_owner_programs.update_one({'_id': program_key, 'stage': 'approving'},
                                                {'$set': {'stage': 'approval_uncertain'}})
        return 'approval_uncertain'
