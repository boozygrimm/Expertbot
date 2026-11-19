import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from mongomock_motor import AsyncMongoMockClient
from news_prototype import general_auto, general_discovery, general_editorial, general_queue, general_sources


@pytest.mark.asyncio
async def test_one_time_politics_migration_preserves_other_preferences_and_reenable():
    db = AsyncMongoMockClient().test
    await db.news_general_state.insert_one({'_id': 'categories', 'disabled': ['games', 'sport']})
    await asyncio.gather(*(general_auto.migrate_politics_preference(db) for _ in range(4)))
    assert await general_auto.disabled_categories(db) == {'games', 'sport', 'politics_society'}
    await general_auto.set_category_enabled(db, 'politics_society', True)
    await general_auto.migrate_politics_preference(db)
    assert await general_auto.disabled_categories(db) == {'games', 'sport'}
    fresh = AsyncMongoMockClient().test
    await general_auto.migrate_politics_preference(fresh)
    assert await general_auto.disabled_categories(fresh) == {'politics_society'}


@pytest.mark.asyncio
async def test_mixed_political_story_is_excluded_from_auto_but_stored():
    db = AsyncMongoMockClient().test
    now = datetime.now(timezone.utc)
    await general_auto.migrate_politics_preference(db)
    political = await general_queue.add_news(db, 'https://example.com/political', now=now)
    safe = await general_queue.add_news(db, 'https://example.com/animals', now=now)
    await db.news_general_candidates.update_one({'_id': political}, {'$set': {
        'title': 'Путин рассказал о нейросети и ChatGPT', 'category_override': 'ai'}})
    await db.news_general_candidates.update_one({'_id': safe}, {'$set': {'title': 'В лес вернулся редкий пингвин'}})
    assert (await general_auto.pick(db, now))['_id'] == safe
    await general_queue.remove_news(db, safe)
    assert await general_auto.pick(db, now) is None
    assert await db.news_general_candidates.count_documents({}) == 2
    assert await general_queue.reserve(db, political, 123) is not None  # manual work remains available


@pytest.mark.parametrize('title', ['Rare birds returned to the forest', 'Penguins spotted on the coast',
    'В лес вернулся пингвин', 'Слон вернулся в заповедник', 'Лиса нашла дом'])
def test_animal_headlines(title):
    assert 'animals' in general_editorial.assess(title)['topics']


@pytest.mark.parametrize('title', ['ChatGPT и искусственный интеллект: Путин сделал заявление',
                                    'Президент рассказал о видеоигре',
                                    'Government proposes new science funding',
                                    'Trump discusses TikTok and AI',
                                    'Sanctions affect satellite technology',
                                    'Military deploys new drones'])
def test_political_signal_even_when_other_topics_dominate(title):
    assert general_editorial.political_signal({'title': title})


@pytest.mark.asyncio
async def test_new_builtin_does_not_reenable_or_duplicate_existing_custom():
    db = AsyncMongoMockClient().test
    # User added and subsequently disabled this channel before it became builtin.
    with patch.dict(general_discovery.CHANNELS, {}, clear=True), patch.dict(general_discovery.FEEDS, {}, clear=True):
        ident = await general_sources.add_source(db, 'telegram', 'Мой источник', '@d_code', 'internet')
    await general_sources.remove_source(db, ident)
    assert not any(s['location'] == 'd_code' for s in await general_sources.list_sources(db))
    entries = [s for s in await general_sources.list_sources(db, include_removed=True) if s['location'] == 'd_code']
    assert len(entries) == 1 and entries[0]['id'] == ident and not entries[0]['enabled']
    assert await general_sources.add_source(db, 'telegram', 'Мой источник', '@d_code', 'internet') == ident
    entries = [s for s in await general_sources.list_sources(db) if s['location'] == 'd_code']
    assert len(entries) == 1 and entries[0]['id'] == ident


@pytest.mark.asyncio
async def test_new_source_records_have_valid_definitions_and_explicit_topics():
    ids = {'habr_news', 'rozetked', 'd_code', 'stopgame', 'nakedscience', 'rgo', 'mospriroda', 'vetlife', 'sciencedaily_birds'}
    sources = {s['id']: s for s in general_sources.builtin_sources()}
    assert ids <= sources.keys()
    assert {sources[i]['topic_hint'] for i in ids} >= {'internet', 'animals', 'games', 'nature', 'science'}
    for ident in ids:
        source = sources[ident]
        general_sources.validate_source(source['kind'], source['name'], source['location'], source['topic_hint'])
    assert all(s['location'].lower() != 'senseidurova' for s in sources.values())


def test_nonpolitical_star_wars_is_not_blocked():
    assert not general_editorial.political_signal({'title': 'Новая игра Звёздные войны выйдет в Steam'})


@pytest.mark.asyncio
async def test_new_topic_feeds_collect_and_reclassify_without_network():
    now = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
    all_sources = {s['id']: s for s in general_sources.builtin_sources()}
    selected = [all_sources['d_code'], all_sources['sciencedaily_birds']]
    html = b'''<div class="tgme_widget_message" data-post="d_code/12">
        <div class="tgme_widget_message_text js-message_text">New service update</div>
        <time datetime="2026-01-02T11:00:00Z"></time></div>'''
    rss = b'''<rss><channel><item><title>Birds return to forests</title>
        <link>https://example.com/birds</link><pubDate>Fri, 02 Jan 2026 11:00:00 GMT</pubDate></item></channel></rss>'''
    async def mock_fetch(session, url):
        return html if 't.me/' in url else rss
    db = AsyncMongoMockClient().test
    async def mock_collect(now, sources):
        return await general_discovery.collect(now=now, sources=selected, fetch=mock_fetch)
    inserted, failed = await general_queue.poll(db, now=now, fetch=mock_collect)
    assert (inserted, failed) == (2, [])
    docs = await db.news_general_candidates.find({}).to_list(length=10)
    assert {d['source_id'] for d in docs} == {'d_code', 'sciencedaily_birds'}
    topics = {d['source_id']: general_editorial.assess_item(d)['topics'] for d in docs}
    assert 'internet' in topics['d_code'] and 'animals' in topics['sciencedaily_birds']


@pytest.mark.asyncio
async def test_politics_migration_recovers_malformed_category_list():
    db = AsyncMongoMockClient().test
    await db.news_general_state.insert_one({'_id': 'categories', 'disabled': 'invalid'})
    await general_auto.migrate_politics_preference(db)
    assert await general_auto.disabled_categories(db) == {'politics_society'}


@pytest.mark.asyncio
async def test_slow_source_does_not_cancel_other_sources():
    now = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
    all_sources = {s['id']: s for s in general_sources.builtin_sources()}
    selected = [all_sources['d_code'], all_sources['sciencedaily_birds']]
    rss = b'''<rss><channel><item><title>Birds fly</title><link>https://example.com/birds</link>
        <pubDate>Fri, 02 Jan 2026 11:00:00 GMT</pubDate></item></channel></rss>'''
    async def fetch(session, url):
        if 't.me/' in url:
            await asyncio.Event().wait()
        return rss
    with patch.object(general_discovery, 'SOURCE_TIMEOUT', 0.01):
        items, failed = await general_discovery.collect(now=now, sources=selected, fetch=fetch)
    assert failed == ['d_code']
    assert [i['source_id'] for i in items] == ['sciencedaily_birds']


@pytest.mark.parametrize('title', ['WhatsApp добавил новую функцию', 'Обновление Телеграма', 'Discord меняет настройки'])
def test_internet_headlines(title):
    assert 'internet' in general_editorial.assess(title)['topics']


@pytest.mark.asyncio
async def test_international_registry_topics_and_large_bounded_collection():
    sources = general_sources.builtin_sources()
    international = {'verge', 'restofworld', 'techcabal', 'inc42', 'science_japan', 'scmp_tech',
                     'eurogamer', 'pcgamer', 'polygon', 'guardian_animals', 'mongabay',
                     'pbs_nature', 'space_com', 'variety_film', 'billboard_music', 'bbc_sport', 'positive_news'}
    by_id = {s['id']: s for s in sources}
    assert len(sources) == 50 and international <= by_id.keys()
    assert {by_id[i]['topic_hint'] for i in international} >= {'internet', 'technology', 'science',
        'games', 'animals', 'nature', 'space', 'cinema', 'culture', 'sport', 'general'}
    for source in sources:
        general_sources.validate_source(source['kind'], source['name'], source['location'], source['topic_hint'])
    active = peak = 0
    async def fetch(session, url):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(.01)
            raise OSError('unavailable')
        finally:
            active -= 1
    now = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
    items, failed = await asyncio.wait_for(general_discovery.collect(now=now, sources=sources, fetch=fetch), timeout=2)
    assert items == [] and len(failed) == 50
    assert peak == general_discovery.SOURCE_CONCURRENCY == 8


@pytest.mark.asyncio
async def test_all_fifty_timeout_individually_instead_of_cancelling_batch():
    async def hang(session, url):
        await asyncio.Event().wait()
    with patch.object(general_discovery, 'SOURCE_TIMEOUT', 0.01):
        items, failures = await asyncio.wait_for(general_discovery.collect(fetch=hang), timeout=2)
    assert items == [] and len(failures) == 50


@pytest.mark.asyncio
async def test_slow_telegram_does_not_starve_rss_or_exceed_host_limit():
    sources = general_sources.builtin_sources()
    active = peak = 0
    rss_started = asyncio.Event()
    release = asyncio.Event()
    async def fetch(session, url):
        nonlocal active, peak
        if 't.me/' in url:
            active += 1
            peak = max(peak, active)
            try:
                await release.wait()
            finally:
                active -= 1
        else:
            rss_started.set()
        raise OSError('unavailable')
    task = asyncio.create_task(general_discovery.collect(sources=sources, fetch=fetch))
    try:
        await asyncio.wait_for(rss_started.wait(), timeout=1)
        assert peak == general_discovery.PER_HOST_CONCURRENCY == 4
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_international_rss_and_atom_keep_topic_and_original_title():
    now = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
    by_id = {s['id']: s for s in general_sources.builtin_sources()}
    sources = [by_id['techcabal'], by_id['guardian_animals']]
    async def fetch(session, url):
        if 'techcabal' in url:
            return b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>New startup launched</title>
                <link href="https://example.com/startup"/><updated>2026-01-02T11:00:00Z</updated></entry></feed>'''
        return b'''<rss><channel><item><title>Rare species returns</title>
            <link>https://example.com/species</link><pubDate>Fri, 02 Jan 2026 11:00:00 GMT</pubDate></item></channel></rss>'''
    items, failed = await general_discovery.collect(now=now, sources=sources, fetch=fetch)
    assert not failed
    assert {(item['source_id'], item['title'], item['topic_hint']) for item in items} == {
        ('techcabal', 'New startup launched', 'technology'),
        ('guardian_animals', 'Rare species returns', 'animals')}
    assert {item['source_id']: general_editorial.assess_item(item)['topics'] for item in items} == {
        'techcabal': ['technology'], 'guardian_animals': ['animals']}
