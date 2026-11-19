"""Unscheduled general-news discovery. No editor, renderer or Telegram sends.

A separate registry prevents accidentally importing Crypto Sensei from
owner_discovery. Source categories are provisional hints, not classifications of individual
stories; the auto queue applies additional approximate headline rules. External material is untrusted.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging

import aiohttp
from news_prototype import article_fetch, news_sources, owner_discovery

logger = logging.getLogger(__name__)

# (display name, channel username, provisional topic). Candidates, not an
# assertion that a source is reachable or that every post belongs to its topic.
CHANNELS = {
    'nplusone': ('N + 1', 'nplusone', 'science'),
    'ras': ('РАН', 'rasofficial', 'science'),
    'space': ('Pro Космос', 'prokosmosru', 'space'),
    'archeocode': ('АрхеоКод', 'archeocode', 'history'),
    'igromania': ('Игромания', 'igromania', 'games'),
    '3dnews': ('3DNews', 'ru3dnews', 'technology'),
    'kinopoisk': ('Кинопоиск', 'kinopoisk', 'culture'),
    'afisha': ('Афиша Daily', 'afishadaily', 'culture'),
    'sport': ('РИА Новости Спорт', 'sportrian', 'sport'),
    'kommersant': ('Коммерсантъ', 'kommersant', 'general'),
    'tass': ('ТАСС', 'tass_agency', 'general'),
    'doctor': ('Доктор', 'doctv_official', 'health'),
}
FEEDS = {
    'openai': (*news_sources.FEEDS['openai'], 'technology'),
    'huggingface': (*news_sources.FEEDS['huggingface'], 'technology'),
    'cointelegraph': (*news_sources.FEEDS['cointelegraph'], 'crypto'),
    'decrypt': (*news_sources.FEEDS['decrypt'], 'crypto'),
    'nasa': ('NASA', 'https://www.nasa.gov/news-release/feed/', 'space'),
    'sciencedaily': ('ScienceDaily', 'https://www.sciencedaily.com/rss/all.xml', 'science'),
    'nature': ('Nature', 'https://www.nature.com/nature.rss', 'science'),
    'quanta': ('Quanta Magazine', 'https://www.quantamagazine.org/feed/', 'science'),
    'who': ('ВОЗ', 'https://www.who.int/rss-feeds/news-english.xml?paged=1', 'health'),
}
PARSE_FEEDS = {key: (name, url) for key, (name, url, _) in FEEDS.items()}
SOURCE_IDS = frozenset(CHANNELS) | frozenset(FEEDS)


async def collect(*, now: datetime | None = None, fetch=None, sources=None) -> tuple[list[dict], list[str]]:
    """Collect bounded public pages. A failing source doesn't hide the others.

    `fetch` is a test seam with signature (session, url); the default validates
    DNS, HTTPS and same-host redirects using owner_discovery._fetch. The 24-hour
    window and available page contents cannot fill gaps during long outages.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Нужно время с часовым поясом')
    now = now.astimezone(timezone.utc)
    since = now - timedelta(days=1)
    from news_prototype import general_sources
    sources = general_sources.builtin_sources() if sources is None else sources
    # Revalidate every record at the network boundary, including persisted ones.
    records = []
    for source in sources:
        if source.get('enabled') is not True:
            continue
        valid = general_sources.validate_source(source['kind'], source['name'], source['location'], source['topic_hint'])
        records.append(valid | {'id': source['id']})
    resolver = article_fetch.PublicResolver()
    connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False)
    semaphore = asyncio.Semaphore(4)
    get = fetch or owner_discovery._fetch
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=15, connect=7, sock_read=8),
                trust_env=False, auto_decompress=False, cookie_jar=aiohttp.DummyCookieJar(),
                headers={'User-Agent': 'Expertbot-NewsTrial/1.0', 'Accept-Encoding': 'identity'}) as session:
            async def one(source):
                async with semaphore:
                    source_id, name = source['id'], source['name']
                    topic, location = source['topic_hint'], source['location']
                    if source['kind'] == 'telegram':
                        data = await get(session, 'https://t.me/s/' + location)
                        items = owner_discovery.parse_channel(data, since=since, now=now, channel=location, source=name)
                    else:
                        data = await get(session, location)
                        items = news_sources.parse_feed(data, source_id, since=since, now=now,
                            max_items=5000, feeds={source_id: (name, location)})
                    cleaned = owner_discovery._clean_feed(items, now)
                    return [item | {'source_id': source_id, 'topic_hint': topic} for item in cleaned]
            results = await asyncio.gather(*(one(source) for source in records), return_exceptions=True)
    finally:
        await resolver.close()
    combined, failed = [], []
    for source, result in zip(records, results):
        source_id = source['id']
        if isinstance(result, BaseException):
            logger.warning('Общий источник %s недоступен: %s', source_id, type(result).__name__)
            failed.append(source_id)
        else:
            combined.extend(result)
    return combined, failed
