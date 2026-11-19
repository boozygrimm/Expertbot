"""Opt-in bounded background collection and draft preparation for the owner.

No Telegram sends, rendering or publishing. Budget counts attempts, not actual
provider billing. Claimed candidates are never replayed after ambiguous failure.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
import re
import secrets

from pymongo import ReturnDocument
from news_prototype import general_editorial, general_queue, general_sources, news_scheduler, news_sources, owner_trial

MSK = timezone(timedelta(hours=3))
DAILY_ATTEMPTS = 10
ONE_SHOT_ATTEMPTS = 3
WORK_TIMEOUT = 150
LEASE = timedelta(minutes=10)
STOPWORDS = frozenset(('этот', 'это', 'этого', 'которая', 'который', 'которые', 'стало', 'the', 'and', 'with', 'that', 'this', 'from', 'новости', 'новость'))
NEGATIONS = frozenset(('не', 'ни', 'нет', 'без', 'никогда', 'not', 'no', 'never', 'without'))


def _now(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Нужно время с часовым поясом')
    return now.astimezone(timezone.utc)


def day_key(now):
    return _now(now).astimezone(MSK).date().isoformat()


def keys_ready() -> bool:
    from config import GEMINI_KEYS, GROQ_KEYS
    return bool(GEMINI_KEYS and GROQ_KEYS)


async def enabled(db) -> bool:
    settings = await db.news_general_state.find_one({'_id': 'automatic'}) or {}
    return settings.get('enabled') is True


async def setting_version(db) -> int:
    row = await db.news_general_state.find_one({'_id': 'automatic'}) or {}
    version = row.get('version', 0)
    return version if type(version) is int and version >= 0 else 0


async def set_enabled(db, value: bool, *, expected_version=None) -> bool:
    if type(value) is not bool:
        raise ValueError('Неверная настройка')
    if value and (not await owner_trial.drafts_enabled(db) or not keys_ready()):
        raise ValueError('Нужны включённые сценарии и ключи Gemini и Groq')
    await db.news_general_state.update_one({'_id': 'automatic'},
        {'$setOnInsert': {'enabled': False, 'version': 0}}, upsert=True)
    await db.news_general_state.update_one({'_id': 'automatic', 'version': {'$exists': False}},
                                           {'$set': {'version': 0}})
    query = {'_id': 'automatic'}
    if expected_version is not None:
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError('Неверная версия')
        query.update(version=expected_version, enabled=False)
    changed = await db.news_general_state.update_one(query, {'$set': {'enabled': value}, '$inc': {'version': 1}})
    return bool(changed.matched_count)


async def disabled_categories(db) -> set[str]:
    row = await db.news_general_state.find_one({'_id': 'categories'}) or {}
    disabled = row.get('disabled')
    if not isinstance(disabled, list):
        return set()
    return {topic for topic in disabled if isinstance(topic, str) and topic in general_editorial.LABELS}


async def set_category_enabled(db, topic: str, value: bool):
    if topic not in general_editorial.LABELS or type(value) is not bool:
        raise ValueError('Неверная категория')
    operation = '$pull' if value else '$addToSet'
    await db.news_general_state.update_one({'_id': 'categories'},
        {operation: {'disabled': topic}}, upsert=True)


async def set_all_categories_enabled(db, value: bool):
    if type(value) is not bool:
        raise ValueError('Неверная настройка')
    disabled = [] if value else list(general_editorial.LABELS)
    await db.news_general_state.update_one({'_id': 'categories'},
        {'$set': {'disabled': disabled}}, upsert=True)


async def usage(db, now=None) -> int:
    record = await db.news_general_budget.find_one({'_id': day_key(_now(now))}) or {}
    return record.get('used', 0)


async def _take_budget(db, now) -> bool:
    key = day_key(now)
    await db.news_general_budget.update_one({'_id': key}, {'$setOnInsert': {'used': 0}}, upsert=True)
    record = await db.news_general_budget.find_one_and_update(
        {'_id': key, 'used': {'$lt': DAILY_ATTEMPTS}}, {'$inc': {'used': 1}}, return_document=ReturnDocument.AFTER)
    return record is not None


def _tokens(title):
    words = re.findall(r'[a-zа-яё0-9]+', (title or '').casefold())
    words = [w for w in words if (len(w) >= 3 or w.isdigit() or w in NEGATIONS) and w not in STOPWORDS]
    return set(words)


def _stem(word):
    # A small conservative Russian inflection heuristic, not semantic matching.
    if not re.fullmatch(r'[а-яё]{5,}', word):
        return word
    for ending in ('иями', 'ыми', 'ого', 'ему', 'ами', 'ями', 'иях',
                   'ий', 'ый', 'ая', 'ое', 'ые', 'ие', 'ых', 'ой', 'ов', 'ей', 'ам', 'ах', 'ям', 'ях'):
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[:-len(ending)]
    if len(word) >= 7 and word[-1] in 'аеёиоуыэюя':
        return word[:-1]
    return word


def similar_title(a: str, b: str) -> bool:
    """Conservative word/inflection signal, not cross-language semantic matching."""
    x, y = _tokens(a), _tokens(b)
    if (len(x) < 4 or len(y) < 4 or (x & NEGATIONS) != (y & NEGATIONS)
            or {t for t in x if t.isdigit()} != {t for t in y if t.isdigit()}):
        return False
    x, y = {_stem(t) for t in x}, {_stem(t) for t in y}
    return len(x & y) >= 4 and len(x & y) / len(x | y) >= 0.8


def _defer(item, history):
    assessment = general_editorial.assess_item(item)
    if assessment['defer_reason']:
        return assessment['defer_reason']
    if any(similar_title(item.get('title', ''), h.get('title', '')) for h in history if item['_id'] != h['_id']):
        return 'similar_headline'
    return None


async def defer_reason(db, item, now=None):
    """Explain automatic deferral; manual selection remains available."""
    since = _now(now) - timedelta(days=3)
    history = await db.news_general_candidates.find({'first_seen_at': {'$gte': since},
        '$or': [{'stage': {'$ne': 'collected'}}, {'hidden': True}]}).sort('first_seen_at', -1).limit(500).to_list(length=500)
    return _defer(item, history)


async def pick(db, now):
    """Diversify using provisional headline topics and defer likely repeats/ads.

    Similar items remain in the panel and can be chosen manually. Hints do not
    reliably identify every story or all translations/reports of it.
    """
    since = now - timedelta(days=3)
    candidates = db.news_general_candidates.find({'stage': 'collected', 'hidden': {'$ne': True},
        'first_seen_at': {'$gte': since}}).sort(
            [('first_seen_at', -1), ('published_at', -1), ('_id', -1)])
    history = await db.news_general_candidates.find({'first_seen_at': {'$gte': since},
        '$or': [{'stage': {'$ne': 'collected'}}, {'hidden': True}]}).sort('first_seen_at', -1).limit(500).to_list(length=500)
    active_ids = {source['id'] for source in await general_sources.list_sources(db)} | {'manual'}
    attempts = await db.news_general_attempts.find({'at': {'$gte': now - timedelta(days=1)}}).limit(250).to_list(length=250)
    topics = Counter(x.get('topic_hint', 'general') for x in attempts)
    sources = Counter(x.get('source_id', '') for x in attempts)
    def recent(item):
        if item.get('manual') is True:
            return True  # Publication date for a manually supplied URL is unknown.
        published = news_sources.parse_date(item.get('published_at', ''))
        return bool(published and now - timedelta(days=1) <= published <= now)
    disabled = await disabled_categories(db)
    best = None
    best_score = None
    eligible_count = 0
    async for candidate in candidates:
        if candidate.get('source_id') not in active_ids or not recent(candidate):
            continue
        assessment = general_editorial.assess_item(candidate)
        allowed = [topic for topic in assessment['topics'] if topic not in disabled]
        if not allowed or _defer(candidate, history):
            continue
        topic = general_editorial.choose_topic({**assessment, 'topics': allowed}, topics)
        score = (topics[topic], sources[candidate.get('source_id', '')])
        if best_score is None or score < best_score:
            best_score = score
            best = candidate | {'topic_hint': topic, 'topic_basis': assessment['basis']}
        eligible_count += 1
        # Bound expensive selection while letting disabled/noisy categories be
        # skipped rather than crowding chosen categories out of the window.
        if eligible_count >= 1000:
            break
    return best



async def _status(db, status, now):
    await db.news_general_state.update_one({'_id': 'auto_last'}, {'$set': {'status': status, 'at': now}}, upsert=True)


async def collect(db):
    """Share the bounded collector lock with the owner-initiated poll."""
    if not await enabled(db):
        return 'off'
    result = {}
    async def work():
        if await enabled(db):
            result['poll'] = await asyncio.wait_for(general_queue.poll(db), timeout=140)
    await news_scheduler._locked(db.news_general_locks, 'manual-collect', work)
    return 'collected' if 'poll' in result else 'busy_or_off'


async def _take_interval(db, now):
    # Enforce cadence across scheduler processes, not only max_instances=1.
    await db.news_general_state.update_one({'_id': 'auto_cadence'},
        {'$setOnInsert': {'next_at': datetime(1970, 1, 1, tzinfo=timezone.utc)}}, upsert=True)
    claimed = await db.news_general_state.find_one_and_update(
        {'_id': 'auto_cadence', 'next_at': {'$lte': now}},
        {'$set': {'next_at': now + timedelta(minutes=15)}}, return_document=ReturnDocument.AFTER)
    return claimed is not None


async def _claim_lock(db, now, name='auto-editor', lease=LEASE):
    await db.news_general_locks.update_one({'_id': name},
        {'$setOnInsert': {'until': datetime(1970, 1, 1, tzinfo=timezone.utc)}}, upsert=True)
    token = secrets.token_hex(16)
    locked = await db.news_general_locks.find_one_and_update(
        {'_id': name, 'until': {'$lte': now}},
        {'$set': {'until': now + lease, 'token': token}}, return_document=ReturnDocument.AFTER)
    return token if locked else None


async def tick(db, owner_id: int, *, now=None, prepare=None, manual=False) -> str:
    """At most one candidate per tick and ten attempts per Moscow day.

    The lock outlives the 150s work timeout. A crashed process eventually
    releases by expiry; its reserved candidate remains blocked, not replayed.
    A severe process pause beyond the lease can cause overlapping different
    candidates, but atomic claims and the daily budget still prevent duplicate
    candidate runs and unbounded attempts. No provider error bodies are logged.
    """
    now = _now(now)
    if not isinstance(owner_id, int) or isinstance(owner_id, bool) or owner_id <= 0:
        raise ValueError('Неверный владелец')
    if not manual and not await enabled(db):
        return 'off'
    if not await owner_trial.drafts_enabled(db):
        await _status(db, 'drafts_off', now)
        return 'drafts_off'
    if not keys_ready():
        await _status(db, 'keys_missing', now)
        return 'keys_missing'
    token = await _claim_lock(db, now)
    if not token:
        return 'busy'
    async def work():
        if (not manual and not await enabled(db)) or not await owner_trial.drafts_enabled(db) or not keys_ready():
            return 'off'
        candidate = await pick(db, now)
        if not candidate:
            return 'no_candidates'
        if await usage(db, now) >= DAILY_ATTEMPTS:
            return 'budget_exhausted'
        if not manual and not await _take_interval(db, now):
            return 'throttled'
        if not await _take_budget(db, now):
            return 'budget_exhausted'
        # The attempt is consumed before any possibly ambiguous external call.
        await db.news_general_attempts.insert_one({'_id': token, 'candidate_id': candidate['_id'],
            'at': now, 'source_id': candidate.get('source_id', ''), 'topic_hint': candidate.get('topic_hint', 'general'),
            'mode': 'button' if manual else 'automatic'})
        if prepare is None:
            from handlers import news_owner_drafts
            from news_prototype import article_fetch, news_image_fetch, story_review
            result = await general_queue.prepare_reserved(db, candidate['_id'], owner_id,
                article_fetch.fetch_article, news_owner_drafts.make_draft, news_image_fetch.fetch_thumbnail,
                news_owner_drafts.analyze_image, review=story_review.check)
        else:
            result = await prepare(db, candidate['_id'], owner_id)
        return result
    try:
        result = await asyncio.wait_for(work(), timeout=WORK_TIMEOUT)
        await _status(db, result, now)
        return result
    except TimeoutError:
        await _status(db, 'timeout_uncertain', now)
        return 'timeout_uncertain'
    finally:
        await db.news_general_locks.update_one({'_id': 'auto-editor', 'token': token},
            {'$set': {'until': datetime(1970, 1, 1, tzinfo=timezone.utc)}, '$unset': {'token': ''}})


async def run_once(db, owner_id: int, *, poll=None, prepare=None):
    """One confirmed, bounded collection and batch; never enables the scheduler.

    Shares editor lock and daily budget with automatic ticks. An ambiguous
    attempt stops the batch and is never replayed. Collection has its own
    shared lock and is skipped if another collection is running.
    """
    if type(owner_id) is not int or owner_id <= 0:
        raise ValueError('Неверный владелец')
    if not await owner_trial.drafts_enabled(db):
        return {'status': 'drafts_off'}
    if not keys_ready():
        return {'status': 'keys_missing'}
    token = await _claim_lock(db, _now(), name='one-shot', lease=timedelta(minutes=15))
    if not token:
        return {'status': 'busy'}
    result = {'status': 'running', 'inserted': 0, 'failed': [], 'attempts': 0, 'ready': 0, 'outcomes': []}
    async def save():
        await db.news_general_state.update_one({'_id': 'one_shot_last', 'token': token},
            {'$set': {**result, 'updated_at': _now()}})
    try:
        await db.news_general_state.update_one({'_id': 'one_shot_last'},
            {'$set': {**result, 'token': token, 'at': _now()}}, upsert=True)
        collected = {}
        async def work():
            collected['result'] = await asyncio.wait_for((poll or general_queue.poll)(db), timeout=140)
        try:
            await news_scheduler._locked(db.news_general_locks, 'manual-collect', work)
        except Exception:
            result['status'] = 'collect_failed'
            await save()
            return result
        if 'result' not in collected:
            result['status'] = 'collect_busy'
            await save()
            return result
        result['inserted'], result['failed'] = collected['result']
        await save()
        for _ in range(ONE_SHOT_ATTEMPTS):
            status = await tick(db, owner_id, manual=True, prepare=prepare)
            if status in ('off', 'drafts_off', 'keys_missing', 'busy', 'no_candidates', 'budget_exhausted', 'throttled'):
                result['status'] = status
                break
            result['attempts'] += 1
            result['outcomes'].append(status)
            result['ready'] += int(status == 'draft_ready')
            await save()
            if status in ('timeout_uncertain', 'uncertain', 'already_claimed'):
                result['status'] = status
                break
        else:
            result['status'] = 'done'
        await save()
        return result
    except Exception:
        result['status'] = 'uncertain'
        await save()
        return result
    finally:
        await db.news_general_locks.update_one({'_id': 'one-shot', 'token': token},
            {'$set': {'until': datetime(1970, 1, 1, tzinfo=timezone.utc)}, '$unset': {'token': ''}})
