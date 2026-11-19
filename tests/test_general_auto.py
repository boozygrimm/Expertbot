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


@pytest.mark.asyncio
async def test_auto_considers_diverse_candidate_beyond_first_250():
    db = AsyncMongoMockClient().test
    bulk = [{'_id': f'politics-{i}', 'stage': 'collected', 'source_id': 'tass',
             'title': 'Путин рассказал о решении', 'topic_hint': 'general',
             'first_seen_at': NOW - timedelta(seconds=i), 'published_at': NOW.isoformat()}
            for i in range(260)]
    bulk.append({'_id': 'science', 'stage': 'collected', 'source_id': 'nplusone',
                 'title': 'Учёные провели эксперимент', 'topic_hint': 'science',
                 'first_seen_at': NOW - timedelta(seconds=300), 'published_at': NOW.isoformat()})
    await db.news_general_candidates.insert_many(bulk)
    await db.news_general_attempts.insert_one({'_id': 'previous', 'at': NOW,
                                                'source_id': 'tass', 'topic_hint': 'politics_society'})
    chosen = await general_auto.pick(db, NOW)
    assert chosen['_id'] == 'science'
    assert await db.news_general_candidates.count_documents({}) == 261


@pytest.mark.asyncio
async def test_auto_respects_selected_categories_and_owner_override():
    db = AsyncMongoMockClient().test
    politics = await add(db, 1, title='Путин сделал заявление', topic='general', source='tass')
    game = await add(db, 2, title='Новая видеоигра вышла', topic='games', source='igromania')
    await general_auto.set_category_enabled(db, 'politics_society', False)
    chosen = await general_auto.pick(db, NOW)
    assert chosen['_id'] == game
    assert await db.news_general_candidates.count_documents({}) == 2
    await general_auto.set_category_enabled(db, 'games', False)
    assert await general_auto.pick(db, NOW) is None
    await db.news_general_candidates.update_one({'_id': politics}, {'$set': {'category_override': 'humor'}})
    chosen = await general_auto.pick(db, NOW)
    assert chosen['_id'] == politics and chosen['topic_basis'] == 'owner'
    await general_auto.set_category_enabled(db, 'humor', False)
    assert await general_auto.pick(db, NOW) is None
    await general_auto.set_category_enabled(db, 'politics_society', True)
    assert await general_auto.pick(db, NOW) is None  # explicit override is authoritative
    await db.news_general_candidates.update_one({'_id': politics}, {'$unset': {'category_override': ''}})
    assert (await general_auto.pick(db, NOW))['_id'] == politics


@pytest.mark.asyncio
async def test_disabled_category_cannot_crowd_out_selected_category():
    db = AsyncMongoMockClient().test
    await db.news_general_candidates.insert_many([
        {'_id': f'p{i}', 'stage': 'collected', 'source_id': 'tass',
         'title': 'Путин ответил', 'topic_hint': 'general', 'first_seen_at': NOW - timedelta(seconds=i),
         'published_at': NOW.isoformat()} for i in range(1001)
    ] + [{'_id': 'game', 'stage': 'collected', 'source_id': 'igromania',
          'title': 'Видеоигра вышла', 'topic_hint': 'games',
          'first_seen_at': NOW - timedelta(seconds=1100), 'published_at': NOW.isoformat()}])
    await general_auto.set_category_enabled(db, 'politics_society', False)
    assert (await general_auto.pick(db, NOW))['_id'] == 'game'


@pytest.mark.asyncio
async def test_multi_topic_eligible_if_one_category_is_enabled():
    db = AsyncMongoMockClient().test
    key = await add(db, 1, title='Вакцина для животных', source='nplusone')
    await general_auto.set_category_enabled(db, 'health', False)
    choice = await general_auto.pick(db, NOW)
    assert choice['_id'] == key and choice['topic_hint'] == 'animals'
    await general_auto.set_category_enabled(db, 'animals', False)
    assert await general_auto.pick(db, NOW) is None


@pytest.mark.asyncio
async def test_button_batch_collects_and_prepares_selected_categories_without_enabling_auto():
    db = AsyncMongoMockClient().test
    for i in range(5):
        key = await general_queue.add_news(db, f'https://example.com/batch{i}')
        await db.news_general_candidates.update_one({'_id': key}, {'$set': {
            'title': 'Вышла новая видеоигра' if i < 4 else 'Путин ответил', 'source_id': 'igromania' if i < 4 else 'tass'}})
    await general_auto.set_category_enabled(db, 'politics_society', False)
    poll = AsyncMock(return_value=(5, ['space']))
    prepare = AsyncMock(side_effect=mark_done)
    with patch.object(general_auto, 'keys_ready', return_value=True), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)):
        result = await general_auto.run_once(db, 123, poll=poll, prepare=prepare)
    assert result['status'] == 'done'
    assert (result['inserted'], result['attempts'], result['ready'], result['failed']) == (5, 3, 3, ['space'])
    assert not await general_auto.enabled(db)
    assert await general_auto.usage(db) == 3
    assert await db.news_general_attempts.count_documents({'mode': 'button'}) == 3
    assert await db.news_general_candidates.count_documents({'stage': 'draft_ready'}) == 3
    assert (await db.news_general_state.find_one({'_id': 'one_shot_last'}))['status'] == 'done'
    poll.assert_awaited_once()


@pytest.mark.asyncio
async def test_button_batch_shared_budget_and_no_implicit_retry_of_ambiguous_work():
    db = AsyncMongoMockClient().test
    for i in range(3):
        await general_queue.add_news(db, f'https://example.com/limited{i}')
    today = general_auto.day_key(datetime.now(timezone.utc))
    await db.news_general_budget.insert_one({'_id': today, 'used': 9})
    prepare = AsyncMock(side_effect=mark_done)
    with patch.object(general_auto, 'keys_ready', return_value=True), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)):
        result = await general_auto.run_once(db, 123, poll=AsyncMock(return_value=(0, [])), prepare=prepare)
    assert result['status'] == 'budget_exhausted' and result['attempts'] == 1
    assert await general_auto.usage(db) == 10
    prepare.assert_awaited_once()
    db2 = AsyncMongoMockClient().test
    key = await general_queue.add_news(db2, 'https://example.com/uncertain')
    async def unknown(db, ident, owner):
        await general_queue.reserve(db, ident, owner)
        return 'uncertain'
    with patch.object(general_auto, 'keys_ready', return_value=True), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)):
        outcome = await general_auto.run_once(db2, 123, poll=AsyncMock(return_value=(0, [])), prepare=unknown)
    assert outcome['status'] == 'uncertain' and outcome['attempts'] == 1
    assert (await db2.news_general_candidates.find_one({'_id': key}))['stage'] == 'reserved'
    assert await general_auto.usage(db2) == 1


@pytest.mark.asyncio
async def test_button_batch_no_overlap_and_no_drafts_when_collection_fails():
    db = AsyncMongoMockClient().test
    await general_queue.add_news(db, 'https://example.com/concurrent')
    entered, release = asyncio.Event(), asyncio.Event()
    async def collect(db):
        entered.set()
        await release.wait()
        return 0, []
    prepare = AsyncMock(side_effect=mark_done)
    with patch.object(general_auto, 'keys_ready', return_value=True), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)):
        first = asyncio.create_task(general_auto.run_once(db, 123, poll=collect, prepare=prepare))
        await entered.wait()
        assert (await general_auto.run_once(db, 123, poll=collect, prepare=prepare))['status'] == 'busy'
        release.set()
        assert (await first)['ready'] == 1
        failed = await general_auto.run_once(db, 123, poll=AsyncMock(side_effect=RuntimeError()), prepare=prepare)
        assert failed['status'] == 'collect_failed'
        assert prepare.await_count == 1


@pytest.mark.asyncio
async def test_button_and_automatic_editor_share_lock():
    db = AsyncMongoMockClient().test
    await general_queue.add_news(db, 'https://example.com/locked')
    lock = await general_auto._claim_lock(db, datetime.now(timezone.utc))
    assert lock
    prepare = AsyncMock(side_effect=mark_done)
    with patch.object(general_auto, 'keys_ready', return_value=True), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)):
        result = await general_auto.run_once(db, 123, poll=AsyncMock(return_value=(0, [])), prepare=prepare)
    assert result['status'] == 'busy' and result['ready'] == 0
    prepare.assert_not_awaited()
    assert await general_auto.usage(db) == 0
