"""Owner-configurable sources for the future general-news workflow.

This is storage/business logic only, not an authorization boundary or a panel.
Callers must authenticate the owner. No network request is made here.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit
from news_prototype import article_fetch


def builtin_sources() -> list[dict]:
    # Imported lazily to avoid binding a mutable test registry at import time.
    from news_prototype import general_discovery
    channels = [{'id': key, 'kind': 'telegram', 'name': name, 'location': channel,
                 'topic_hint': topic, 'enabled': True, 'builtin': True}
                for key, (name, channel, topic) in general_discovery.CHANNELS.items()]
    feeds = [{'id': key, 'kind': 'rss', 'name': name, 'location': url,
              'topic_hint': topic, 'enabled': True, 'builtin': True}
             for key, (name, url, topic) in general_discovery.FEEDS.items()]
    return channels + feeds


def validate_source(kind: str, name: str, location: str, topic_hint: str = 'general') -> dict:
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100 or any(ord(c) < 32 for c in name):
        raise ValueError('Нужно название источника до 100 символов')
    if not isinstance(topic_hint, str) or not re.fullmatch(r'[a-z0-9_-]{1,32}', topic_hint):
        raise ValueError('Неверная тема')
    if not isinstance(location, str):
        raise ValueError('Неверный адрес')
    if kind == 'telegram':
        # Accept a username or a public channel link, never a post or private invite.
        location = location.strip()
        if location.startswith('@'):
            location = location[1:]
        elif '://' in location:
            try:
                url = article_fetch.validate_url(location)
                parts = urlsplit(url)
            except article_fetch.ArticleError:
                raise ValueError('Неверная ссылка на канал') from None
            segments = parts.path.strip('/').split('/')
            if len(segments) == 2 and segments[0].lower() == 's':
                segments = segments[1:]
            if parts.hostname not in ('t.me', 'www.t.me', 'telegram.me', 'www.telegram.me') or len(segments) != 1 or parts.query:
                raise ValueError('Нужна ссылка на публичный канал')
            location = segments[0]
        if not re.fullmatch(r'[A-Za-z0-9_]{4,32}', location) or location.casefold() == 'senseidurova':
            raise ValueError('Неверный канал или отдельный Crypto Sensei')
        location = location.lower()
    elif kind == 'rss':
        try:
            location = article_fetch.validate_url(location.strip())
        except article_fetch.ArticleError:
            raise ValueError('Нужна публичная HTTPS RSS-лента') from None
        # A Telegram page is not an RSS feed and must not bypass channel rules.
        if urlsplit(location).hostname in ('t.me', 'www.t.me', 'telegram.me', 'www.telegram.me'):
            raise ValueError('Добавьте Telegram как канал')
    else:
        raise ValueError('Нужен Telegram-канал или RSS-лента')
    return {'kind': kind, 'name': name.strip(), 'location': location, 'topic_hint': topic_hint}


def identity(source: dict) -> str:
    location = source['location'].casefold() if source['kind'] == 'telegram' else source['location']
    return source['kind'] + ':' + location


async def list_sources(db, *, include_removed: bool = False) -> list[dict]:
    overrides = await db.news_general_sources.find({}).to_list(length=1000)
    by_id = {row['_id']: row for row in overrides}
    sources = []
    for builtin in builtin_sources():
        state = by_id.get('builtin:' + builtin['id'], {})
        builtin['enabled'] = state.get('enabled') is not False
        sources.append(builtin)
    for row in overrides:
        if not str(row.get('_id', '')).startswith('custom:'):
            continue
        try:
            validated = validate_source(row['kind'], row['name'], row['location'], row['topic_hint'])
        except (KeyError, ValueError, TypeError):
            continue  # Corrupt or unsafe records never reach the fetcher.
        validated.update(id=row['_id'], enabled=row.get('enabled') is True, builtin=False)
        sources.append(validated)
    return sources if include_removed else [source for source in sources if source['enabled']]


async def add_source(db, kind: str, name: str, location: str, topic_hint: str = 'general') -> str:
    source = validate_source(kind, name, location, topic_hint)
    for builtin in builtin_sources():
        if identity(builtin) == identity(source):
            # Re-add a removed built-in, keeping its original name and topic.
            await db.news_general_sources.update_one({'_id': 'builtin:' + builtin['id']},
                                                       {'$set': {'enabled': True}}, upsert=True)
            return builtin['id']
    key = 'custom:' + hashlib.sha256(identity(source).encode()).hexdigest()
    await db.news_general_sources.update_one({'_id': key},
        {'$set': {**source, 'enabled': True}}, upsert=True)
    return key


async def remove_source(db, source_id: str) -> bool:
    """Disable future collection; do not alter existing candidates or drafts."""
    if not isinstance(source_id, str):
        raise ValueError('Неверный источник')
    for builtin in builtin_sources():
        if builtin['id'] == source_id:
            await db.news_general_sources.update_one({'_id': 'builtin:' + source_id},
                                                       {'$set': {'enabled': False}}, upsert=True)
            return True
    if not re.fullmatch(r'custom:[0-9a-f]{64}', source_id):
        return False
    result = await db.news_general_sources.update_one({'_id': source_id}, {'$set': {'enabled': False}})
    return bool(result.matched_count)
