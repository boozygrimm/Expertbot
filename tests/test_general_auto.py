import asyncio
from datetime import datetime, timedelta, timezone
import os
from unittest.mock import AsyncMock, patch

import pytest
from mongomock_motor import AsyncMongoMockClient

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
from news_prototype import general_auto, general_queue, owner_trial

NOW = datetime(2026, 1, 2, 8, tzinfo=timezone.utc)


async def add(db, num, *, title=None, topic='science', source='nplusone'):
    key = await general_queue.add_news(db, f'https://example.com/{num}', now=NOW)
    await db.news_general_candidates.update_one({'_id': key}, {'$set': {'title': title or f'История номер {num}',
        'topic_hint': topic, 'source_id': source}})
    return key


async def mark_done(db, key, owner):
    assert await general_queue.reserve(db, key, owner)
    await db.news_general_candidates.update_one({'_id': key}, {'$set': {'stage': 'draft_ready'}})
    return 'draft_ready'


@pytest.mark.asyncio
async def test_disabled_defaults_missing_keys_and_drafts_stop_work():
    db = AsyncMongoMockClient().test
    work = AsyncMock(side_effect=mark_done)
    with patch.object(general_auto, 'keys_ready', return_value=False), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)):
        assert await general_auto.tick(db, 123, now=NOW, prepare=work) == 'off'
        with pytest.raises(ValueError):
            await general_auto.set_enabled(db, True)
        await db.news_general_state.insert_one({'_id': 'automatic', 'enabled': True})
        assert await general_auto.tick(db, 123, now=NOW, prepare=work) == 'keys_missing'
        assert await general_auto.usage(db, NOW) == 0
    with patch.object(general_auto, 'keys_ready', return_value=True), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=False)):
        assert await general_auto.tick(db, 123, now=NOW, prepare=work) == 'drafts_off'
    work.assert_not_awaited()


@pytest.mark.asyncio
async def test_daily_budget_counts_attempts_and_resets_at_moscow_midnight():
    db = AsyncMongoMockClient().test
    for i in range(12):
        await add(db, i)
    work = AsyncMock(side_effect=mark_done)
    with patch.object(general_auto, 'keys_ready', return_value=True), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)):
        await general_auto.set_enabled(db, True)
        for i in range(10):
            assert await general_auto.tick(db, 123, now=NOW + timedelta(minutes=15*i), prepare=work) == 'draft_ready'
        assert await general_auto.usage(db, NOW) == 10
        assert await general_auto.tick(db, 123, now=NOW + timedelta(minutes=150), prepare=work) == 'budget_exhausted'
        assert work.await_count == 10
        next_day = datetime(2026, 1, 2, 21, 1, tzinfo=timezone.utc)  # 00:01 MSK
        assert await general_auto.usage(db, next_day) == 0
        # Manual entries without a publication date remain eligible.
        assert await general_auto.tick(db, 123, now=next_day, prepare=work) == 'draft_ready'
        assert await general_auto.usage(db, next_day) == 1


@pytest.mark.asyncio
async def test_concurrent_workers_and_cadence_do_not_duplicate_work():
    db = AsyncMongoMockClient().test
    await add(db, 1); await add(db, 2)
    entered, release = asyncio.Event(), asyncio.Event()
    async def prepare(db, key, owner):
        entered.set()
        await release.wait()
        return await mark_done(db, key, owner)
    with patch.object(general_auto, 'keys_ready', return_value=True), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)):
        await general_auto.set_enabled(db, True)
        task = asyncio.create_task(general_auto.tick(db, 123, now=NOW, prepare=prepare))
        await entered.wait()
        assert await general_auto.tick(db, 123, now=NOW, prepare=prepare) == 'busy'
        release.set()
        assert await task == 'draft_ready'
        assert await general_auto.tick(db, 123, now=NOW + timedelta(minutes=1), prepare=prepare) == 'throttled'
        assert await general_auto.usage(db, NOW) == 1


@pytest.mark.asyncio
async def test_timeout_consumes_attempt_and_does_not_replay_claimed_candidate():
    db = AsyncMongoMockClient().test
    key = await add(db, 1)
    async def hanging(db, ident, owner):
        assert await general_queue.reserve(db, ident, owner)
        await asyncio.Event().wait()
    with patch.object(general_auto, 'keys_ready', return_value=True), \
         patch.object(general_auto, 'WORK_TIMEOUT', 0.01), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)):
        await general_auto.set_enabled(db, True)
        assert await general_auto.tick(db, 123, now=NOW, prepare=hanging) == 'timeout_uncertain'
        assert await general_auto.usage(db, NOW) == 1
        assert (await db.news_general_candidates.find_one({'_id': key}))['stage'] == 'reserved'
        assert await general_auto.tick(db, 123, now=NOW+timedelta(minutes=15), prepare=hanging) == 'no_candidates'


@pytest.mark.asyncio
async def test_suspected_repeats_are_deferred_not_deleted_and_manual_override_allowed():
    db = AsyncMongoMockClient().test
    first = await add(db, 1, title='Учёные обнаружили новый редкий минерал на Луне')
    second = await add(db, 2, title='Учёные обнаружили новый редкий минерал на Луне!')
    await db.news_general_candidates.update_one({'_id': first}, {'$set': {'stage': 'draft_ready'}})
    assert await general_auto.pick(db, NOW) is None
    assert (await db.news_general_candidates.find_one({'_id': second}))['stage'] == 'collected'
    assert await general_queue.reserve(db, second, 123) is not None
    assert not general_auto.similar_title('Учёные не обнаружили новый редкий минерал на Луне',
                                          'Учёные обнаружили новый редкий минерал на Луне')
    assert not general_auto.similar_title('Короткие новости', 'Короткие новости')


@pytest.mark.asyncio
async def test_collection_is_off_and_uses_existing_lock_when_enabled():
    db = AsyncMongoMockClient().test
    fetch = AsyncMock(return_value=(2, []))
    with patch.object(general_queue, 'poll', fetch), patch.object(general_auto, 'keys_ready', return_value=True), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)):
        assert await general_auto.collect(db) == 'off'
        fetch.assert_not_awaited()
        await general_auto.set_enabled(db, True)
        assert await general_auto.collect(db) == 'collected'
        fetch.assert_awaited_once()
        await general_auto.set_enabled(db, False)
        assert await general_auto.collect(db) == 'off'


@pytest.mark.asyncio
async def test_scheduler_jobs_registered_but_off_do_not_call_collect_or_editor():
    import app_setup
    import database
    from types import SimpleNamespace
    db = AsyncMongoMockClient().test
    with patch.object(database, 'db', SimpleNamespace(db=db)), \
         patch.object(general_queue, 'poll', AsyncMock()) as poll, \
         patch.object(general_queue, 'prepare_reserved', AsyncMock()) as prepare:
        scheduler = app_setup.create_scheduler()
        jobs = {job.func.__name__: job for job in scheduler.get_jobs()}
        assert jobs['collect_general_news'].trigger.interval == timedelta(minutes=30)
        assert jobs['prepare_general_news'].trigger.interval == timedelta(minutes=15)
        await app_setup.collect_general_news()
        await app_setup.prepare_general_news()
        poll.assert_not_awaited()
        prepare.assert_not_awaited()


@pytest.mark.asyncio
async def test_background_creates_one_draft_and_runs_independent_review():
    from news_prototype import article_fetch, owner_drafts, story_review
    from handlers import news_owner_drafts
    db = AsyncMongoMockClient().test
    key = await add(db, 1)
    fetch = AsyncMock(return_value={'title': 'Наука', 'summary': 'Проверяемый текст источника. ' * 5})
    editor = AsyncMock(return_value={'decision': 'draft', 'verification': 'needs_review',
        'script': {'ticker': 'Наука', 'turns': [{'speaker': 'Бендер', 'text': 'Новость.'},
                                               {'speaker': 'Киберсенсей', 'text': 'Принято.'}]}, 'evidence': []})
    review_request = AsyncMock(return_value='{"status":"issues","issues":[{"location":"Строка","reason":"Проверить","quote":""}]}')
    check = story_review.check
    async def review(db, owner_id, draft_id):
        return await check(db, owner_id, draft_id, request=review_request)
    with patch.object(general_auto, 'keys_ready', return_value=True), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)), \
         patch.object(article_fetch, 'fetch_article', fetch), patch.object(news_owner_drafts, 'make_draft', editor), \
         patch.object(story_review, 'check', review):
        await general_auto.set_enabled(db, True)
        assert await general_auto.tick(db, 123, now=NOW) == 'draft_ready'
    draft = await owner_drafts.get(db, 123, general_queue.draft_id_for(key))
    assert draft['independent_review']['status'] == 'issues'
    assert draft['verification'] == 'needs_review'
    fetch.assert_awaited_once(); editor.assert_awaited_once(); review_request.assert_awaited_once()
    assert await db.news_owner_trial_jobs.count_documents({}) == 0
