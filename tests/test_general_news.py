import asyncio
from datetime import datetime, timedelta, timezone
import os
from unittest.mock import AsyncMock, patch

import pytest
from mongomock_motor import AsyncMongoMockClient

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
from news_prototype import general_discovery as discovery, general_queue as queue, owner_discovery

NOW = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
HTML = b'''<div class="tgme_widget_message" data-post="nplusone/12">
<div class="tgme_widget_message_text js-message_text">A science story</div>
<time datetime="2026-01-02T11:00:00Z"></time></div>
<div class="tgme_widget_message" data-post="SenseiDurova/13">
<div class="tgme_widget_message_text js-message_text">Other channel</div>
<time datetime="2026-01-02T11:00:00Z"></time></div>'''
RSS = b'''<rss><channel><item><title>Technology story</title>
<link>https://example.com/news?utm_source=x</link>
<pubDate>Fri, 02 Jan 2026 11:00:00 GMT</pubDate></item></channel></rss>'''


def test_registry_keeps_sensei_separate_but_retains_crypto():
    assert all(channel.casefold() != 'senseidurova' for _, channel, _ in discovery.CHANNELS.values())
    assert any(topic == 'crypto' for _, _, topic in discovery.FEEDS.values())


def test_parser_requires_exact_channel_and_preserves_old_default():
    entries = owner_discovery.parse_channel(HTML, since=NOW-timedelta(days=1), now=NOW,
                                             channel='nplusone', source='N + 1')
    assert [i['url'] for i in entries] == ['https://t.me/nplusone/12']
    assert entries[0]['source'] == 'N + 1'
    old = owner_discovery.parse_channel(HTML, since=NOW-timedelta(days=1), now=NOW)
    assert [i['url'] for i in old] == ['https://t.me/SenseiDurova/13']
    with pytest.raises(ValueError):
        owner_discovery.ChannelParser('../SenseiDurova')


@pytest.mark.asyncio
async def test_collection_isolated_sources_and_failure():
    channels = {'nplusone': discovery.CHANNELS['nplusone'], 'space': discovery.CHANNELS['space']}
    feeds = {'openai': discovery.FEEDS['openai']}
    async def fetch(session, url):
        if 'nplusone' in url:
            return HTML
        if 'prokosmosru' in url:
            raise OSError('private provider details')
        return RSS
    with patch.object(discovery, 'CHANNELS', channels), patch.object(discovery, 'FEEDS', feeds), \
         patch.object(discovery, 'PARSE_FEEDS', {'openai': discovery.PARSE_FEEDS['openai']}):
        items, failures = await discovery.collect(now=NOW, fetch=fetch)
    assert failures == ['space']
    assert {i['source_id'] for i in items} == {'nplusone', 'openai'}
    assert {i['url'] for i in items} == {'https://t.me/nplusone/12', 'https://example.com/news'}
    assert all('SenseiDurova' not in i['url'] for i in items)


def item(url, source='openai', title='A story', at=NOW-timedelta(hours=1)):
    return {'source_id': source, 'source': 'untrusted', 'topic_hint': 'untrusted',
            'url': url, 'title': title, 'published_at': at.isoformat()}


@pytest.mark.asyncio
async def test_storage_dedup_claim_and_old_cleanup():
    db = AsyncMongoMockClient().test
    sample = [item('https://example.com/news?utm_source=one'), item('https://example.com/news?utm_source=two'),
              item('https://t.me/SenseiDurova/20', 'cointelegraph'),
              item('https://t.me/s/SenseiDurova/21', 'cointelegraph'),
              item('https://example.com/future', at=NOW+timedelta(hours=1)),
              item('https://example.com/unknown', 'bad')]
    fetch = AsyncMock(return_value=(sample, ['space']))
    assert await queue.poll(db, now=NOW, fetch=fetch) == (1, ['space'])
    assert await queue.poll(db, now=NOW, fetch=fetch) == (0, ['space'])
    record = await db.news_general_candidates.find_one({})
    assert record['source'] == 'OpenAI' and record['topic_hint'] == 'technology'
    assert record['url'] == 'https://example.com/news'
    claims = await asyncio.gather(queue.reserve(db, record['_id'], 123), queue.reserve(db, record['_id'], 123))
    assert sum(c is not None for c in claims) == 1
    assert (await db.news_general_candidates.find_one({}))['stage'] == 'reserved'
    await queue.poll(db, now=NOW + timedelta(days=8), fetch=AsyncMock(return_value=([], [])))
    assert await db.news_general_candidates.count_documents({}) == 1


@pytest.mark.asyncio
async def test_cleanup_only_unclaimed_and_bad_inputs():
    db = AsyncMongoMockClient().test
    await queue.poll(db, now=NOW, fetch=AsyncMock(return_value=([item('https://example.com/a')], [])))
    await queue.poll(db, now=NOW+timedelta(days=8), fetch=AsyncMock(return_value=([], [])))
    assert await db.news_general_candidates.count_documents({}) == 0
    with pytest.raises(ValueError):
        await queue.reserve(db, 'bad', 123)
    with pytest.raises(ValueError):
        await queue.poll(db, now=datetime(2026, 1, 1), fetch=AsyncMock())


def test_fairness_uses_topic_before_source_but_not_invented_story_dedup():
    items = [dict(source_id='a', topic_hint='science', published_at='2026-01-02T11:00:00+00:00'),
             dict(source_id='b', topic_hint='science', published_at='2026-01-02T10:00:00+00:00'),
             dict(source_id='c', topic_hint='sport', published_at='2026-01-02T09:00:00+00:00')]
    selected = queue.select_diverse(items, 2)
    assert [x['source_id'] for x in selected] == ['a', 'c']
    with pytest.raises(ValueError):
        queue.select_diverse(items, 0)


@pytest.mark.asyncio
async def test_prepare_creates_one_unverified_owner_draft_without_render():
    from news_prototype import owner_drafts
    db = AsyncMongoMockClient().test
    await queue.poll(db, now=NOW, fetch=AsyncMock(return_value=([item('https://example.com/a')], [])))
    record = await db.news_general_candidates.find_one({})
    fetch = AsyncMock(return_value={'title': 'A story', 'summary': 'Достоверный материал. ' * 10})
    draft = AsyncMock(return_value={'decision': 'draft', 'verification': 'needs_review',
        'script': {'ticker': 'Новость', 'turns': [{'speaker': 'Бендер', 'text': 'Новость.'},
                                                  {'speaker': 'Киберсенсей', 'text': 'Принято.'}]},
        'evidence': []})
    outcome = await queue.prepare_reserved(db, record['_id'], 123, fetch, draft)
    assert outcome == 'draft_ready'
    saved = await owner_drafts.get(db, 123, queue.draft_id_for(record['_id']))
    assert saved['source'] == 'OpenAI'
    assert saved['verification'] == 'needs_review'
    assert saved['stage'] == 'draft_ready'
    assert await queue.prepare_reserved(db, record['_id'], 123, fetch, draft) == 'already_claimed'
    draft.assert_awaited_once()
    assert await db.news_owner_trial.count_documents({}) == 0


@pytest.mark.asyncio
async def test_prepare_does_not_reuse_existing_draft_or_replay_after_failure():
    from news_prototype import owner_drafts
    db = AsyncMongoMockClient().test
    await queue.poll(db, now=NOW, fetch=AsyncMock(return_value=([item('https://example.com/a')], [])))
    record = await db.news_general_candidates.find_one({})
    ident = queue.draft_id_for(record['_id'])
    await owner_drafts.create(db, 123, ident, record['url'], '')
    draft = AsyncMock()
    assert await queue.prepare_reserved(db, record['_id'], 123, AsyncMock(), draft) == 'id_conflict'
    draft.assert_not_awaited()
    assert await queue.prepare_reserved(db, record['_id'], 123, AsyncMock(), draft) == 'already_claimed'


@pytest.mark.asyncio
async def test_ambiguous_draft_creation_is_not_retried():
    from news_prototype import owner_drafts
    db = AsyncMongoMockClient().test
    await queue.poll(db, now=NOW, fetch=AsyncMock(return_value=([item('https://example.com/a')], [])))
    record = await db.news_general_candidates.find_one({})
    with patch.object(owner_drafts, 'create', new=AsyncMock(side_effect=OSError('lost response'))) as create:
        assert await queue.prepare_reserved(db, record['_id'], 123, AsyncMock(), AsyncMock()) == 'uncertain'
        assert await queue.prepare_reserved(db, record['_id'], 123, AsyncMock(), AsyncMock()) == 'already_claimed'
        create.assert_awaited_once()
    stored = await db.news_general_candidates.find_one({'_id': record['_id']})
    assert stored['stage'] == 'reserved' and stored['draft_id'] == queue.draft_id_for(record['_id'])


@pytest.mark.asyncio
async def test_source_add_remove_restore_and_no_draft_deletion():
    from news_prototype import general_sources
    db = AsyncMongoMockClient().test
    channel = await general_sources.add_source(db, 'telegram', 'Канал', 'https://t.me/s/ExampleNews', 'culture')
    same = await general_sources.add_source(db, 'telegram', 'Канал', '@examplenews', 'culture')
    assert same == channel
    feed = await general_sources.add_source(db, 'rss', 'Сайт', 'https://example.com/feed.xml')
    assert channel != feed
    before = {s['id']: s for s in await general_sources.list_sources(db)}
    assert before[channel]['location'] == 'examplenews'
    assert before[feed]['location'] == 'https://example.com/feed.xml'
    await db.news_owner_drafts.insert_one({'_id': 'existing', 'stage': 'draft_ready'})
    assert await general_sources.remove_source(db, channel)
    assert await general_sources.remove_source(db, 'nplusone')
    after = {s['id'] for s in await general_sources.list_sources(db)}
    assert channel not in after and 'nplusone' not in after and feed in after
    assert await db.news_owner_drafts.count_documents({}) == 1
    assert await general_sources.add_source(db, 'telegram', 'Канал', '@ExampleNews') == channel
    assert await general_sources.add_source(db, 'telegram', 'N + 1', '@nplusone') == 'nplusone'
    after = {s['id'] for s in await general_sources.list_sources(db)}
    assert channel in after and 'nplusone' in after


@pytest.mark.parametrize('kind,location', [
    ('telegram', 'https://t.me/SenseiDurova'), ('telegram', '@SENSEIDUROVA'),
    ('telegram', 'https://t.me/nplusone/23'), ('telegram', 'https://evil.example/nplusone'),
    ('rss', 'https://t.me/s/SenseiDurova'), ('rss', 'https://127.0.0.1/feed'),
    ('rss', 'https://evil.example@127.0.0.1/feed'), ('rss', 'http://example.com/feed'),
])
def test_invalid_sources_are_rejected(kind, location):
    from news_prototype import general_sources
    with pytest.raises(ValueError):
        general_sources.validate_source(kind, 'Источник', location)


@pytest.mark.asyncio
async def test_custom_sources_collected_and_removed_during_fetch():
    from news_prototype import general_sources
    db = AsyncMongoMockClient().test
    for builtin in general_sources.builtin_sources():
        await general_sources.remove_source(db, builtin['id'])
    custom = await general_sources.add_source(db, 'rss', 'Свой сайт', 'https://example.com/feed')
    feeds = await general_sources.list_sources(db)
    assert [s['id'] for s in feeds] == [custom]
    async def get(session, url):
        assert url == 'https://example.com/feed'
        return RSS
    items, failures = await discovery.collect(now=NOW, fetch=get, sources=feeds)
    assert not failures and items[0]['source_id'] == custom
    fetch = AsyncMock(return_value=(items, []))
    assert (await queue.poll(db, now=NOW, fetch=fetch))[0] == 1
    saved = await db.news_general_candidates.find_one({})
    assert saved['source'] == 'Свой сайт'
    # Existing candidates remain, but a source removed during a poll contributes no new candidates.
    await db.news_general_candidates.delete_many({})
    async def removed_during_poll(**kwargs):
        await general_sources.remove_source(db, custom)
        return items, []
    assert (await queue.poll(db, now=NOW, fetch=removed_during_poll))[0] == 0


@pytest.mark.asyncio
async def test_add_and_remove_single_news_without_refetch_or_duplicate():
    db = AsyncMongoMockClient().test
    key = await queue.add_news(db, 'https://example.com/article?utm_source=test', now=NOW)
    item1 = await db.news_general_candidates.find_one({'_id': key})
    assert item1['source'] == 'example.com' and item1['published_at'] == ''
    assert await queue.remove_news(db, key)
    assert await queue.reserve(db, key, 123) is None
    assert not queue.select_diverse([await db.news_general_candidates.find_one({'_id': key})], 1)
    assert await queue.add_news(db, 'https://example.com/article', now=NOW) == key
    assert await db.news_general_candidates.count_documents({}) == 1
    assert await queue.reserve(db, key, 123) is not None
    assert await queue.remove_news(db, key)
    assert (await db.news_general_candidates.find_one({'_id': key}))['stage'] == 'reserved'
    with pytest.raises(ValueError):
        await queue.add_news(db, 'https://t.me/nplusone', now=NOW)
    with pytest.raises(ValueError):
        await queue.add_news(db, 'https://t.me/s/SenseiDurova/2', now=NOW)


@pytest.mark.asyncio
async def test_prepare_can_run_independent_check_after_draft():
    import json
    from news_prototype import owner_drafts, story_review
    db = AsyncMongoMockClient().test
    key = await queue.add_news(db, 'https://example.com/a', now=NOW)
    fetch = AsyncMock(return_value={'title': 'Новость', 'summary': 'Достоверный материал. ' * 10})
    draft = AsyncMock(return_value={'decision': 'draft', 'verification': 'needs_review',
        'script': {'ticker': 'Новость', 'turns': [{'speaker': 'Бендер', 'text': 'Новость.'},
                                                  {'speaker': 'Киберсенсей', 'text': 'Принято.'}]},
        'evidence': []})
    request = AsyncMock(return_value=json.dumps({'status': 'consistent_with_source', 'issues': []}))
    async def review(db, owner_id, draft_id):
        return await story_review.check(db, owner_id, draft_id, request=request)
    assert await queue.prepare_reserved(db, key, 123, fetch, draft, review=review) == 'draft_ready'
    result = await owner_drafts.get(db, 123, queue.draft_id_for(key))
    assert result['independent_review']['status'] == 'consistent_with_source'
    assert result['verification'] == 'needs_review'
    request.assert_awaited_once()


@pytest.mark.asyncio
async def test_owner_removed_news_survives_retention_and_repoll():
    db = AsyncMongoMockClient().test
    one = item('https://example.com/removed')
    await queue.poll(db, now=NOW, fetch=AsyncMock(return_value=([one], [])))
    saved = await db.news_general_candidates.find_one({})
    await queue.remove_news(db, saved['_id'])
    later = NOW + timedelta(days=8)
    await queue.poll(db, now=later, fetch=AsyncMock(return_value=([], [])))
    record = await db.news_general_candidates.find_one({'_id': saved['_id']})
    assert record and record['hidden'] is True
    updated = one | {'published_at': later.isoformat()}
    assert (await queue.poll(db, now=later, fetch=AsyncMock(return_value=([updated], []))))[0] == 0
    assert (await db.news_general_candidates.find_one({'_id': saved['_id']}))['hidden'] is True
