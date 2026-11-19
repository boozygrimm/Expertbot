"""Isolated candidate store for future owner-only editorial automation.

Reserving alone never calls an AI
provider or creates a draft; the separate prepare_reserved function can invoke
the supplied draft callback. Nothing here renders or publishes. Semantic story
deduplication and recovery of reserved items are not implemented yet.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import re
from urllib.parse import urlsplit

from pymongo import ReturnDocument
from news_prototype import general_discovery, general_sources, owner_discovery

KEEP_DAYS = 7


def _utc(now: datetime | None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Нужно время с часовым поясом')
    return now.astimezone(timezone.utc)


def _is_sensei(url: str) -> bool:
    parts = urlsplit(url)
    segments = parts.path.strip('/').split('/')
    if segments and segments[0].casefold() == 's':
        segments = segments[1:]
    return bool(parts.hostname in ('t.me', 'telegram.me', 'www.t.me', 'www.telegram.me') and segments
                and segments[0].casefold() == 'senseidurova')


def _candidate(item: dict, now: datetime, sources: dict) -> dict | None:
    try:
        source_id = item['source_id']
        source = sources[source_id]
        name, topic = source['name'], source['topic_hint']
        url = owner_discovery.canonical_url(item['url'])
        if _is_sensei(url):
            return None
        date = owner_discovery.news_sources.parse_date(item['published_at'])
        title = item['title']
        if (not date or not now - timedelta(days=1) <= date <= now
                or not isinstance(title, str) or not title.strip() or len(url) > 500):
            return None
        title = re.sub(r'\s+', ' ', title).strip()[:180]
        return {'url': url, 'title': title, 'source_id': source_id, 'source': name,
                'topic_hint': topic, 'published_at': date.isoformat()}
    except (ValueError, TypeError, KeyError, owner_discovery.article_fetch.ArticleError):
        return None


async def poll(db, *, now: datetime | None = None, fetch=None) -> tuple[int, list[str]]:
    """Idempotently store canonical URLs; preserve statuses on repeated polls."""
    now = _utc(now)
    sources = await general_sources.list_sources(db)
    items, failed = await (fetch or general_discovery.collect)(now=now, sources=sources)
    # Recheck after fetching. A removal after this snapshot can race with insertion;
    # future scheduling/panel integration must account for in-flight work.
    active = {source['id']: source for source in await general_sources.list_sources(db)}
    inserted = 0
    for item in items:
        candidate = _candidate(item, now, active)
        if candidate is None:
            continue
        key = hashlib.sha256(candidate['url'].encode('utf-8')).hexdigest()
        result = await db.news_general_candidates.update_one(
            {'_id': key}, {'$setOnInsert': {**candidate, 'stage': 'collected', 'first_seen_at': now}}, upsert=True)
        inserted += int(result.upserted_id is not None)
    # Keep owner-removed entries as tombstones so they cannot silently reappear.
    # Never remove manual entries or work that has left the initial stage.
    await db.news_general_candidates.delete_many(
        {'stage': 'collected', 'manual': {'$ne': True}, 'hidden': {'$ne': True},
         'published_at': {'$lt': (now - timedelta(days=KEEP_DAYS)).isoformat()}})
    await db.news_general_state.update_one({'_id': 'last_poll'},
        {'$set': {'at': now, 'failed': [s for s in failed if s in active],
                  'inserted': inserted}}, upsert=True)
    return inserted, failed


async def add_news(db, url: str, *, now: datetime | None = None) -> str:
    """Add an exact article/post, without fetching or launching an AI call.

    Re-adding a hidden existing URL restores it, but never restarts a draft.
    Publication time is unknown; first_seen_at is not the article's date.
    """
    from news_prototype import owner_drafts
    now = _utc(now)
    url = owner_discovery.canonical_url(owner_drafts._url(url))
    if len(url) > 500 or _is_sensei(url):
        raise ValueError('Неверная ссылка или отдельный Crypto Sensei')
    key = hashlib.sha256(url.encode()).hexdigest()
    doc = {'url': url, 'title': 'Новость по ссылке', 'source_id': 'manual',
           'source': urlsplit(url).hostname, 'topic_hint': 'general',
           'published_at': '', 'stage': 'collected', 'first_seen_at': now, 'manual': True}
    await db.news_general_candidates.update_one({'_id': key},
        {'$setOnInsert': doc, '$set': {'hidden': False}}, upsert=True)
    return key


async def remove_news(db, key: str) -> bool:
    """Hide one candidate and prevent new claims; preserve any existing draft.

    Already claimed provider work may finish; hiding does not cancel it, render
    anything, or alter a draft already in the owner's existing studio.
    """
    if not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{64}', key):
        raise ValueError('Неверный идентификатор')
    result = await db.news_general_candidates.update_one({'_id': key}, {'$set': {'hidden': True}})
    return bool(result.matched_count)


def select_diverse(items: list[dict], limit: int) -> list[dict]:
    """Balance rough topic hints, then sources; not semantic story deduplication."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Неверный размер выборки')
    ordered = sorted((i for i in items if i.get('hidden') is not True),
                     key=lambda i: i.get('published_at') or '', reverse=True)
    result, topic_counts, source_counts = [], {}, {}
    while ordered and len(result) < limit:
        # Stable input order breaks ties by recency.
        index = min(range(len(ordered)), key=lambda n: (
            topic_counts.get(ordered[n].get('topic_hint', 'general'), 0),
            source_counts.get(ordered[n].get('source_id', ''), 0)))
        item = ordered.pop(index)
        result.append(item)
        topic, source = item.get('topic_hint', 'general'), item.get('source_id', '')
        topic_counts[topic] = topic_counts.get(topic, 0) + 1
        source_counts[source] = source_counts.get(source, 0) + 1
    return result


async def reserve(db, key: str, owner_id: int) -> dict | None:
    """Claim once; no automatic replay after an ambiguous/interrupted claim."""
    if not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{64}', key):
        raise ValueError('Неверный идентификатор')
    if not isinstance(owner_id, int) or isinstance(owner_id, bool) or owner_id <= 0:
        raise ValueError('Неверный владелец')
    return await db.news_general_candidates.find_one_and_update(
        {'_id': key, 'stage': 'collected', 'hidden': {'$ne': True}},
        {'$set': {'stage': 'reserved', 'owner_id': owner_id, 'reserved_at': _utc(None)}},
        return_document=ReturnDocument.AFTER)


def draft_id_for(key: str) -> int:
    """Deterministic 15-digit ID in a range above existing message/batch IDs.

    Hash collisions are possible: create checks the full URL and a preexisting
    ID is never reused here. No retry is automatic after an ambiguous write.
    """
    if not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{64}', key):
        raise ValueError('Неверный идентификатор')
    return 800_000_000_000_000 + int(key, 16) % 100_000_000_000_000


async def prepare_reserved(db, key: str, owner_id: int, fetch, draft,
                           fetch_image=None, analyze_image=None, review=None) -> str:
    """Claim and prepare one existing owner draft, without scheduling/rendering.

    Explicit caller responsibility: quotas, authorization, topic/story dedup,
    independent verification and approval. Once claimed, a crash or ambiguous
    write leaves the candidate reserved for inspection, never automatic replay.
    """
    from news_prototype import owner_drafts
    candidate = await reserve(db, key, owner_id)
    if not candidate:
        return 'already_claimed'
    draft_id = draft_id_for(key)
    await db.news_general_candidates.update_one({'_id': key, 'stage': 'reserved', 'owner_id': owner_id},
                                                  {'$set': {'draft_id': draft_id}})
    try:
        created = await owner_drafts.create(db, owner_id, draft_id, candidate['url'], '')
        if not created:
            # Do not accidentally attach or reprocess a preexisting draft.
            await db.news_general_candidates.update_one({'_id': key, 'stage': 'reserved'},
                                                         {'$set': {'stage': 'id_conflict'}})
            return 'id_conflict'
        draft_key = owner_drafts._key(owner_id, draft_id)
        updated = await db.news_owner_drafts.update_one(
            {'_id': draft_key, 'stage': 'new', 'url': candidate['url']},
            {'$set': {'source': candidate['source'], 'general_candidate_id': key}})
        if not updated.matched_count:
            return 'uncertain'
        result = await owner_drafts.process_new(db, owner_id, draft_id, fetch, draft,
                                                fetch_image, analyze_image)
        await db.news_general_candidates.update_one({'_id': key, 'stage': 'reserved', 'owner_id': owner_id},
                                                     {'$set': {'stage': result}})
        if result == 'draft_ready' and review is not None:
            try:
                await review(db, owner_id, draft_id)
            except Exception:
                pass  # The draft remains unverified; never replay automatically.
        return result
    except Exception:
        # The draft or provider call may have succeeded despite a lost reply.
        # Leave the reservation untouched; the caller must inspect both records.
        return 'uncertain'
