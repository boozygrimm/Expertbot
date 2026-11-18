"""Single, manually reviewed trial admitted by the owner through the bot.

Uses isolated Mongo collections, not the ordinary news scheduler. The owner
chooses the text; no model, source fetch, channel publication or group send is
started here. A persistent slot and content-derived ID prevent double renders.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import re
import uuid

from news_prototype.article_fetch import validate_url, ArticleError
from news_prototype.news_queue import NAMESPACE, enqueue_script
from news_prototype.news_worker_client import valid_id
from news_prototype.story_audio import validate_script

OTHER_FLAGS = ('NEWS_INTAKE_ENABLED', 'NEWS_COLLECTION_ENABLED', 'NEWS_EDITOR_ENABLED',
               'NEWS_RENDER_QUEUE_ENABLED', 'NEWS_REVIEW_ENABLED')


def enabled() -> bool:
    """Legacy environment-only default, kept for existing installations."""
    return (os.getenv('NEWS_OWNER_TRIAL_ENABLED', '').strip() == '1'
            and not conflicting_news_mode())


def conflicting_news_mode() -> bool:
    return any(os.getenv(name, '').strip() == '1' for name in OTHER_FLAGS)


async def runtime_settings(db) -> tuple[bool, bool]:
    settings = await db.news_owner_daily_state.find_one({'_id': 'owner_mode'}) or {}
    trial = settings.get('trial')
    drafts = settings.get('drafts')
    if type(trial) is not bool:
        trial = os.getenv('NEWS_OWNER_TRIAL_ENABLED', '').strip() == '1'
    if type(drafts) is not bool:
        drafts = os.getenv('NEWS_OWNER_DRAFT_ENABLED', '').strip() == '1'
    return trial and not conflicting_news_mode(), drafts


async def runtime_enabled(db) -> bool:
    trial, _ = await runtime_settings(db)
    return trial


async def drafts_enabled(db) -> bool:
    trial, drafts = await runtime_settings(db)
    return trial and drafts


async def set_owner_mode(db, value: bool) -> None:
    if type(value) is not bool:
        raise ValueError('Неверный режим')
    if value and conflicting_news_mode():
        raise ValueError('Включён другой новостной режим')
    await db.news_owner_daily_state.update_one({'_id': 'owner_mode'},
        {'$set': {'trial': value, 'drafts': value}}, upsert=True)


async def migrate_panel_defaults(db) -> None:
    """Persist existing env defaults once, without changing their effective state."""
    # Resolve the daily value before installing overrides of either setting.
    from news_prototype import owner_daily
    daily = await owner_daily.enabled(db)
    await db.news_owner_daily_state.update_one({'_id': 'settings'},
        {'$setOnInsert': {'enabled': daily}}, upsert=True)
    await db.news_owner_daily_state.update_one({'_id': 'owner_mode'},
        {'$setOnInsert': {'trial': os.getenv('NEWS_OWNER_TRIAL_ENABLED', '').strip() == '1',
                          'drafts': os.getenv('NEWS_OWNER_DRAFT_ENABLED', '').strip() == '1'}}, upsert=True)


def parse_message(message: str) -> tuple[str, dict]:
    if not isinstance(message, str) or len(message) > 4096:
        raise ValueError('Неверное сообщение')
    lines = message.splitlines()
    if not lines or not re.fullmatch(r'/news_trial(?:@[A-Za-z0-9_]+)?\s*', lines[0], re.I):
        raise ValueError('Нужна команда /news_trial')
    url = ticker = None
    turns = []
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        name, sep, text = line.partition(':')
        text = text.strip()
        if not sep or not text:
            raise ValueError('Каждая строка должна начинаться с роли или поля')
        if name.casefold() == 'источник' and url is None:
            try:
                url = validate_url(text)
            except ArticleError:
                raise ValueError('Нужен HTTPS-источник') from None
        elif name.casefold() == 'строка' and ticker is None:
            ticker = text
        elif name in ('Бендер', 'Киберсенсей'):
            turns.append({'speaker': name, 'text': text})
        else:
            raise ValueError('Неизвестная или повторная строка')
    if not url or not ticker or {t['speaker'] for t in turns} != {'Бендер', 'Киберсенсей'}:
        raise ValueError('Нужны источник, строка и оба ведущих')
    return url, validate_script({'turns': turns, 'ticker': ticker})


def single_candidate(owner_id: int, url: str, script: dict) -> tuple[str, dict, str]:
    """Canonical identity shared by admission and delivered-draft verification."""
    if type(owner_id) is not int or owner_id <= 0:
        raise ValueError('Неверный владелец')
    try:
        url = validate_url(url)
    except ArticleError:
        raise ValueError('Нужен HTTPS-источник') from None
    script = validate_script(script)
    if {turn['speaker'] for turn in script['turns']} != {'Бендер', 'Киберсенсей'} or not script['ticker']:
        raise ValueError('Нужны оба ведущих и строка')
    canonical = json.dumps({'owner_id': owner_id, 'url': url, 'script': script},
                           ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    digest = hashlib.sha256(canonical).hexdigest()
    return url, script, 'owner-trial:' + digest


async def admit(db, owner_id: int, url: str, script: dict) -> str:
    url, script, candidate_id = single_candidate(owner_id, url, script)
    doc = {'owner_id': owner_id, 'source_id': 'owner_trial', 'url': url,
           'title': 'Пробный сюжет по сценарию владельца', 'verification': 'needs_review', 'script': script}
    await db.news_owner_trial_gate.update_one({'_id': 'slot'},
        {'$setOnInsert': {'candidate_id': candidate_id, 'created_at': datetime.now(timezone.utc)}}, upsert=True)
    gate = await db.news_owner_trial_gate.find_one({'_id': 'slot'})
    if gate and gate.get('candidate_id') is None and gate.get('ready_for_next') is True:
        # A late duplicate of a delivered scenario must not steal the newly
        # opened slot or start another rendering. Any other existing record in
        # an open slot is anomalous and remains blocked for manual inspection.
        request_id = str(uuid.uuid5(NAMESPACE, candidate_id))
        prior = await db.news_owner_trial_jobs.find_one({'_id': request_id})
        if prior:
            saved = await db.news_owner_trial_candidates.find_one({'_id': candidate_id})
            message_id = prior.get('review_message_id')
            if (saved and all(saved.get(k) == v for k, v in doc.items())
                    and prior.get('candidate_id') == candidate_id and prior.get('request_id') == request_id
                    and prior.get('script') == script and prior.get('stage') == 'review_sent'
                    and type(message_id) is int and message_id > 0):
                return 'already_queued'
            raise ValueError('Сохранённая проба требует ручной проверки')
        # A slot explicitly reopened after confirmed delivery can be claimed
        # once. A malformed/missing candidate alone is not permission.
        await db.news_owner_trial_gate.update_one(
            {'_id': 'slot', 'candidate_id': None, 'ready_for_next': True},
            {'$set': {'candidate_id': candidate_id, 'ready_for_next': False,
                      'created_at': datetime.now(timezone.utc)}})
        gate = await db.news_owner_trial_gate.find_one({'_id': 'slot'})
    if not gate or gate.get('candidate_id') != candidate_id:
        raise ValueError('Слот уже занят другой пробой')
    await db.news_owner_trial_candidates.update_one({'_id': candidate_id}, {'$setOnInsert': doc}, upsert=True)
    stored = await db.news_owner_trial_candidates.find_one({'_id': candidate_id})
    if not stored or any(stored.get(k) != v for k, v in doc.items()):
        raise ValueError('Пробная карточка не совпадает')
    created = await enqueue_script(db.news_owner_trial_jobs, candidate_id, script)
    return 'queued' if created else 'already_queued'


async def admit_program(db, owner_id: int, stories: list[dict]) -> str:
    """Reserve the same owner slot for one reviewed multi-story program."""
    from news_prototype.news_queue import enqueue_program
    from video_prototype.news_jobs import validate_program
    if type(owner_id) is not int or owner_id <= 0 or not isinstance(stories, list) or not 2 <= len(stories) <= 4:
        raise ValueError('Неверный выпуск')
    checked = []
    for item in stories:
        if not isinstance(item, dict) or set(item) != {'url', 'script'}:
            raise ValueError('Неверный сюжет')
        try:
            url = validate_url(item['url'])
        except ArticleError:
            raise ValueError('Неверный источник') from None
        checked.append({'url': url, 'script': validate_script(item['script'])})
    scripts = [item['script'] for item in checked]
    validate_program({'request_id': str(uuid.uuid4()), 'scripts': scripts})
    canonical = json.dumps({'owner_id': owner_id, 'stories': checked}, ensure_ascii=False,
                           sort_keys=True, separators=(',', ':')).encode('utf-8')
    candidate_id = 'owner-trial:' + hashlib.sha256(canonical).hexdigest()
    request_id = str(uuid.uuid5(NAMESPACE, candidate_id))
    doc = {'owner_id': owner_id, 'source_id': 'owner_trial', 'kind': 'program',
           'title': 'Выпуск по сценариям владельца', 'verification': 'needs_review', 'stories': checked}
    await db.news_owner_trial_gate.update_one({'_id': 'slot'},
        {'$setOnInsert': {'candidate_id': candidate_id, 'created_at': datetime.now(timezone.utc)}}, upsert=True)
    gate = await db.news_owner_trial_gate.find_one({'_id': 'slot'})
    if gate and gate.get('candidate_id') is None and gate.get('ready_for_next') is True:
        prior = await db.news_owner_trial_jobs.find_one({'_id': request_id})
        if prior:
            saved = await db.news_owner_trial_candidates.find_one({'_id': candidate_id})
            message_id = prior.get('review_message_id')
            if (saved and all(saved.get(k) == v for k, v in doc.items())
                    and prior.get('kind') == 'program' and prior.get('candidate_id') == candidate_id
                    and prior.get('request_id') == request_id and prior.get('scripts') == scripts
                    and prior.get('stage') == 'review_sent' and type(message_id) is int and message_id > 0):
                return 'already_queued'
            raise ValueError('Сохранённый выпуск требует проверки')
        await db.news_owner_trial_gate.update_one({'_id': 'slot', 'candidate_id': None, 'ready_for_next': True},
            {'$set': {'candidate_id': candidate_id, 'ready_for_next': False,
                      'created_at': datetime.now(timezone.utc)}})
        gate = await db.news_owner_trial_gate.find_one({'_id': 'slot'})
    if not gate or gate.get('candidate_id') != candidate_id:
        raise ValueError('Слот занят')
    await db.news_owner_trial_candidates.update_one({'_id': candidate_id}, {'$setOnInsert': doc}, upsert=True)
    stored = await db.news_owner_trial_candidates.find_one({'_id': candidate_id})
    if not stored or any(stored.get(k) != v for k, v in doc.items()):
        raise ValueError('Сохранённый выпуск не совпадает')
    created = await enqueue_program(db.news_owner_trial_jobs, candidate_id, scripts)
    return 'queued' if created else 'already_queued'


async def dispatch_once(queue, db, client) -> None:
    """Shared media queue idle hook; respect persistent runtime settings."""
    if not await runtime_enabled(db) or await queue.head():
        return
    jobs = db.news_owner_trial_jobs
    pending = await jobs.find_one({'stage': {'$in': ['queued', 'submitting', 'running']}})
    if pending and await client.supports_news():
        from news_prototype.news_queue import run_idle_once
        await run_idle_once(queue, jobs, client)


async def status_info(db) -> tuple[str, str | None]:
    gate = await db.news_owner_trial_gate.find_one({'_id': 'slot'})
    if not gate or (gate.get('candidate_id') is None and gate.get('ready_for_next') is True):
        return 'not_queued', None
    candidate_id = gate.get('candidate_id')
    if not isinstance(candidate_id, str) or not re.fullmatch(r'owner-trial:[0-9a-f]{64}', candidate_id):
        return 'admission_incomplete', None
    request_id = str(uuid.uuid5(NAMESPACE, candidate_id))
    job = await db.news_owner_trial_jobs.find_one({'_id': request_id, 'candidate_id': candidate_id})
    if not job:
        return 'admission_incomplete', None
    stage = job.get('stage', 'unknown')
    message_id = job.get('review_message_id')
    # Failed tasks are terminal, and can be retired when the owner explicitly
    # approves a new bulletin. Never offer a token for an uncertain operation.
    if (job.get('request_id') == request_id and (stage == 'failed' or
            (stage == 'review_sent' and type(message_id) is int and message_id > 0))):
        return stage, request_id
    return stage, None


async def status(db) -> str:
    stage, _ = await status_info(db)
    return stage


async def release_failed_for_bulletin(db, owner_id: int, request_id: str) -> str:
    """Retire a terminal failed trial only during explicit new-bulletin approval.

    Keep the old job unchanged; no replay, deletion or new render here. The
    gate's compare-and-set cannot release another owner's or a newer task.
    """
    if type(owner_id) is not int or owner_id <= 0:
        raise ValueError('Неверный владелец')
    request_id = valid_id(request_id)
    gate = await db.news_owner_trial_gate.find_one({'_id': 'slot'})
    if gate and gate.get('candidate_id') is None and gate.get('ready_for_next') is True:
        return 'already_open' if gate.get('last_released_request_id') == request_id else 'not_allowed'
    candidate_id = gate.get('candidate_id') if gate else None
    if (not isinstance(candidate_id, str) or not re.fullmatch(r'owner-trial:[0-9a-f]{64}', candidate_id)
            or str(uuid.uuid5(NAMESPACE, candidate_id)) != request_id):
        return 'not_allowed'
    candidate = await db.news_owner_trial_candidates.find_one({'_id': candidate_id, 'owner_id': owner_id})
    job = await db.news_owner_trial_jobs.find_one({'_id': request_id, 'request_id': request_id,
                                                  'candidate_id': candidate_id, 'stage': 'failed'})
    if not candidate or not job:
        return 'not_allowed'
    result = await db.news_owner_trial_gate.update_one(
        {'_id': 'slot', 'candidate_id': candidate_id},
        {'$set': {'candidate_id': None, 'ready_for_next': True,
                  'last_released_request_id': request_id, 'released_at': datetime.now(timezone.utc)}})
    return 'opened' if result.modified_count else 'changed'


async def open_next(db, owner_id: int, request_id: str) -> str:
    """Explicitly reopen the slot only for the specified confirmed delivery.

    The old job and candidate are never deleted or replayed. A stale or repeated
    command cannot release a different, newer job. Uncertain/failed/in-flight
    jobs remain blocked for manual investigation.
    """
    if type(owner_id) is not int or owner_id <= 0:
        raise ValueError('Неверный владелец')
    request_id = valid_id(request_id)
    gate = await db.news_owner_trial_gate.find_one({'_id': 'slot'})
    if not gate:
        return 'not_allowed'
    if gate.get('candidate_id') is None and gate.get('ready_for_next') is True:
        return 'already_open' if gate.get('last_released_request_id') == request_id else 'not_allowed'
    candidate_id = gate.get('candidate_id')
    if (not isinstance(candidate_id, str) or not re.fullmatch(r'owner-trial:[0-9a-f]{64}', candidate_id)
            or str(uuid.uuid5(NAMESPACE, candidate_id)) != request_id):
        return 'not_allowed'
    candidate = await db.news_owner_trial_candidates.find_one({'_id': candidate_id, 'owner_id': owner_id})
    job = await db.news_owner_trial_jobs.find_one({'_id': request_id, 'request_id': request_id,
                                                  'candidate_id': candidate_id, 'stage': 'review_sent'})
    message_id = job.get('review_message_id') if job else None
    if not candidate or type(message_id) is not int or message_id <= 0:
        return 'not_allowed'
    result = await db.news_owner_trial_gate.update_one(
        {'_id': 'slot', 'candidate_id': candidate_id},
        {'$set': {'candidate_id': None, 'ready_for_next': True,
                  'last_released_request_id': request_id, 'released_at': datetime.now(timezone.utc)}})
    return 'opened' if result.modified_count else 'changed'
