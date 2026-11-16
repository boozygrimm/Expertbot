"""Optional, separately gated poll and editorial steps for the bot scheduler.

Nothing is registered unless configuration explicitly enables it. Only one
instance per task holds a short Mongo lease. No publication or Telegram send.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import logging
import uuid
from news_prototype import collect_news, editorial_queue, editor_client

logger = logging.getLogger(__name__)
LEASE = timedelta(minutes=3)


async def _locked(locks, name, work):
    now = datetime.now(timezone.utc)
    await locks.update_one({'_id': name}, {'$setOnInsert': {'until': datetime(1970,1,1,tzinfo=timezone.utc)}}, upsert=True)
    owner = str(uuid.uuid4())
    result = await locks.update_one({'_id': name, 'until': {'$lte': now}},
                                    {'$set': {'until': now + LEASE, 'owner': owner}})
    if not result.matched_count:
        return
    try:
        await work()
    finally:
        await locks.update_one({'_id': name, 'owner': owner},
                               {'$set': {'until': datetime(1970,1,1,tzinfo=timezone.utc)}, '$unset': {'owner': ''}})


async def poll(db):
    async def work():
        results = await collect_news.collect_once(db.news_candidates, db.news_feed_cursors)
        for source_id, outcome in results.items():
            if not outcome['ok']:
                logger.warning('Новостной источник %s недоступен или не сохранён', source_id)
    await _locked(db.news_locks, 'feed-poll', work)


async def edit(db, key: str, model: str):
    if not key:
        logger.warning('Подготовка новостей отключена: ключ Gemini не настроен')
        return
    async def work():
        async def draft(candidate):
            return await editor_client.draft(candidate, key, model)
        from news_prototype.article_fetch import fetch_article
        await editorial_queue.run_once(db.news_candidates, db.news_render_jobs, draft, fetch_article=fetch_article)
    await _locked(db.news_locks, 'editor', work)
