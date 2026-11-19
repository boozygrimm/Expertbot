import asyncio
import json
import os
from unittest.mock import AsyncMock

import pytest
from mongomock_motor import AsyncMongoMockClient

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
from news_prototype import owner_drafts, story_review

TEXT = 'Учёные сообщили о новом исследовании. Оно было проведено на небольшой группе участников. Данные предварительные.'
URL = 'https://example.com/article'


async def ready(db):
    await owner_drafts.create(db, 123, 9, URL, '')
    fetch = AsyncMock(return_value={'title': 'Исследование', 'summary': TEXT})
    editor = AsyncMock(return_value={'decision': 'draft', 'verification': 'needs_review',
        'script': {'ticker': 'Новое исследование', 'turns': [
            {'speaker': 'Бендер', 'text': 'Исследование проводилось на небольшой группе.'},
            {'speaker': 'Киберсенсей', 'text': 'Выводы пока предварительные.'}]},
        'evidence': [{'kind': 'fact', 'quote': 'небольшой группе участников'},
                     {'kind': 'fact', 'quote': 'Данные предварительные'}]})
    assert await owner_drafts.process_new(db, 123, 9, fetch, editor) == 'draft_ready'
    return await owner_drafts.get(db, 123, 9)


def answer(status='issues', quote='небольшой группе участников'):
    issues = [{'location': 'Реплика 1', 'reason': 'Размер группы не указан.', 'quote': quote}] if status == 'issues' else []
    return json.dumps({'status': status, 'issues': issues}, ensure_ascii=False)


@pytest.mark.asyncio
async def test_review_is_bound_to_revision_and_shows_issues():
    db = AsyncMongoMockClient().test
    item = await ready(db)
    request = AsyncMock(return_value=answer())
    assert await story_review.check(db, 123, 9, request=request) == 'issues'
    result = await owner_drafts.get(db, 123, 9)
    assert result['verification'] == 'needs_review'
    assert result['revision'] == item['revision']
    assert 'Размер группы не указан' in story_review.display(result)
    assert 'не проверка истинности' in story_review.display(result)
    payload = json.loads(request.await_args.args[1])
    assert payload['source']['summary'] == TEXT
    assert payload['script']['ticker'] == 'Новое исследование'
    assert 'image_b64' not in request.await_args.args[1]
    edited = {'ticker': 'Изменено', 'turns': item['script']['turns']}
    assert await owner_drafts.edit(db, 123, 9, item['revision'], URL, edited) == 'saved'
    assert 'устарела' in story_review.display(await owner_drafts.get(db, 123, 9))
    assert await db.news_owner_trial.count_documents({}) == 0


@pytest.mark.parametrize('response', [
    answer('issues', 'несуществующая цитата'),
    json.dumps({'status': 'consistent_with_source', 'issues': [{'location': '1', 'reason': 'ошибка', 'quote': ''}]}),
    'not json',
])
@pytest.mark.asyncio
async def test_invalid_or_fabricated_review_fails_closed(response):
    db = AsyncMongoMockClient().test
    await ready(db)
    assert await story_review.check(db, 123, 9, request=AsyncMock(return_value=response)) == 'unavailable'
    item = await owner_drafts.get(db, 123, 9)
    assert 'не выполнена' in story_review.display(item)
    assert item['verification'] == 'needs_review'


@pytest.mark.asyncio
async def test_provider_failure_does_not_approve_or_retry():
    db = AsyncMongoMockClient().test
    await ready(db)
    request = AsyncMock(side_effect=TimeoutError('sensitive body'))
    assert await story_review.check(db, 123, 9, request=request) == 'unavailable'
    request.assert_awaited_once()
    item = await owner_drafts.get(db, 123, 9)
    assert item['independent_review']['status'] == 'unavailable'
    assert 'sensitive body' not in repr(item['independent_review'])


@pytest.mark.asyncio
async def test_concurrent_review_and_edit_cannot_store_stale_result():
    db = AsyncMongoMockClient().test
    item = await ready(db)
    entered, release = asyncio.Event(), asyncio.Event()
    async def request(system, material):
        entered.set()
        await release.wait()
        return answer('consistent_with_source')
    task = asyncio.create_task(story_review.check(db, 123, 9, request=request))
    await entered.wait()
    assert await story_review.check(db, 123, 9, request=AsyncMock()) == 'busy_or_changed'
    edited = {'ticker': 'После правки', 'turns': item['script']['turns']}
    assert await owner_drafts.edit(db, 123, 9, item['revision'], URL, edited) == 'saved'
    # A changed revision can start a new check even when the old one is stuck.
    assert await story_review.check(db, 123, 9, request=AsyncMock(return_value=answer('consistent_with_source'))) == 'consistent_with_source'
    release.set()
    assert await task == 'changed'
    result = await owner_drafts.get(db, 123, 9)
    assert result['independent_review']['revision'] == result['revision']
    assert 'не нашла расхождений' in story_review.display(result)


@pytest.mark.asyncio
async def test_source_changed_during_check_cannot_attach_old_result():
    db = AsyncMongoMockClient().test
    await ready(db)
    entered, release = asyncio.Event(), asyncio.Event()
    async def request(system, material):
        entered.set()
        await release.wait()
        return answer('consistent_with_source')
    task = asyncio.create_task(story_review.check(db, 123, 9, request=request))
    await entered.wait()
    await db.news_owner_drafts.update_one({'_id': owner_drafts._key(123, 9)}, {'$set': {'summary': TEXT + ' Новые данные.'}})
    release.set()
    assert await task == 'changed'
    assert 'устарела' in story_review.display(await owner_drafts.get(db, 123, 9))


def test_quote_validation_rejects_image_description_as_primary_source():
    data = {'source': {'title': 'Заголовок', 'summary': 'Текст статьи', 'text': '', 'image_description': 'Выдуманная цифра 200'}}
    with pytest.raises(ValueError):
        story_review.parse_response(answer('issues', 'Выдуманная цифра 200'), data)


@pytest.mark.asyncio
async def test_groq_one_bounded_request_no_secrets_in_url_or_error():
    from unittest.mock import patch
    import ai_providers
    import models
    from tests.test_editor_client import Response, Session
    body = {'choices': [{'finish_reason': 'stop', 'message': {'content': answer('consistent_with_source')}}]}
    calls = []
    session = Session(Response(200, json.dumps(body).encode()), calls)
    with patch.object(ai_providers, 'fire_groq_key', return_value='fake-secret'), \
         patch.object(models, 'GROQ_FAST_MODELS', ['test-model']), \
         patch.object(story_review.aiohttp, 'ClientSession', return_value=session):
        assert await story_review.request_groq('system', 'material') == answer('consistent_with_source')
    assert len(calls) == 1
    assert 'fake-secret' not in calls[0][0]
    assert calls[0][1]['headers']['Authorization'] == 'Bearer fake-secret'
    assert calls[0][1]['allow_redirects'] is False
    calls = []
    session = Session(Response(429, b'private response fake-secret'), calls)
    with patch.object(ai_providers, 'fire_groq_key', return_value='fake-secret'), \
         patch.object(models, 'GROQ_FAST_MODELS', ['test-model']), \
         patch.object(story_review.aiohttp, 'ClientSession', return_value=session):
        with pytest.raises(story_review.ReviewError) as caught:
            await story_review.request_groq('system', 'material')
    assert len(calls) == 1
    assert 'fake-secret' not in str(caught.value)


@pytest.mark.asyncio
async def test_panel_shows_remarks_on_the_same_card():
    from types import SimpleNamespace
    from unittest.mock import patch
    from handlers import news_owner_drafts, owner_news_actions
    db = AsyncMongoMockClient().test
    await ready(db)
    await story_review.check(db, 123, 9, request=AsyncMock(return_value=answer()))
    item = await owner_drafts.get(db, 123, 9)
    message = SimpleNamespace(from_user=SimpleNamespace(id=123), answer=AsyncMock(), panel_mode=True)
    with patch.object(owner_news_actions, 'db', SimpleNamespace(db=db)):
        await news_owner_drafts.show(message, 9, item=item, panel_mode=True)
    message.answer.assert_awaited_once()
    text = message.answer.await_args.args[0]
    assert 'Размер группы не указан' in text
    assert 'Источник:' in text and 'Бендер:' in text
    assert 'не проверка истинности' in text
