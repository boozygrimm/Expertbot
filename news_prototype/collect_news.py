"""Explicit RSS poll and idempotent Mongo persistence; not scheduled by default.

One progress record per feed advances only after every candidate was saved. A
small overlap catches late feed entries; deterministic IDs deduplicate retries.
Fetching does not cause scripts, rendering, Telegram delivery or publication.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone
from news_prototype import news_sources

INITIAL_LOOKBACK = timedelta(hours=2)
OVERLAP = timedelta(hours=2)


async def collect_once(candidates, cursors, *, now: datetime | None = None, fetch=None) -> dict:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Время должно содержать часовой пояс')
    now = now.astimezone(timezone.utc)
    fetch = fetch or news_sources.fetch_feed
    results = {}
    for source_id in news_sources.FEEDS:
        try:
            cursor = await cursors.find_one({'_id': source_id})
            prior = news_sources.parse_date(cursor.get('watermark', '')) if cursor else None
            if cursor and (prior is None or prior > now):
                raise ValueError('Повреждённое время источника')
            since = prior - OVERLAP if prior else now - INITIAL_LOOKBACK
            records = await asyncio.to_thread(fetch, source_id, since=since, now=now)
            inserted = 0
            for item in records:
                data = item.copy(); key = 'feed:' + data.pop('id')
                result = await candidates.update_one({'_id': key}, {'$setOnInsert': data}, upsert=True)
                inserted += int(result.upserted_id is not None)
            await cursors.update_one({'_id': source_id}, {'$max': {'watermark': now.isoformat()}}, upsert=True)
            results[source_id] = {'ok': True, 'inserted': inserted}
        except (news_sources.FeedError, OSError, ValueError):
            results[source_id] = {'ok': False}
        except Exception:
            # DB/network errors may contain URLs or credentials; never expose them.
            results[source_id] = {'ok': False}
    return results
