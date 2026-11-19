import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from mongomock_motor import AsyncMongoMockClient
from news_prototype import general_translation as tr, general_queue, general_bulletin_selection as basket


def test_language_detection_and_stale_cache():
    assert tr.needs_translation('Rare animals return to the forest')
    assert tr.needs_translation('日本の最新ニュース')
    assert not tr.needs_translation('Apple показала новый смартфон с AI')
    assert not tr.needs_translation('123!')
    item = {'title': 'New title', 'translated_title': 'Старый перевод', 'translated_title_source': 'Old title'}
    assert tr.display_title(item) == 'New title'


@pytest.mark.asyncio
async def test_cache_reuse_original_and_cooldown():
    db = AsyncMongoMockClient().test
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    items = [{'_id': 'a', 'title': 'Birds return'}, {'_id': 'b', 'title': 'Новости Apple'}]
    await db.news_general_candidates.insert_many(items)
    translate = AsyncMock(return_value=['Птицы вернулись'])
    assert not await tr.translate_visible(db, items, translate=translate, now=now)
    translate.assert_awaited_once_with(['Birds return'])
    assert (await db.news_general_candidates.find_one({'_id': 'a'}))['title'] == 'Birds return'
    assert tr.display_title(items[0]) == 'Птицы вернулись'
    again = [await db.news_general_candidates.find_one({'_id': 'a'})]
    await tr.translate_visible(db, again, translate=translate, now=now)
    translate.assert_awaited_once()
    assert basket.choice({**again[0], 'url': 'https://example.com/a'})['title'] == 'Птицы вернулись'
    failed = {'_id': 'c', 'title': 'New whales'}
    await db.news_general_candidates.insert_one(failed)
    broken = AsyncMock(return_value=None)
    assert await tr.translate_visible(db, [failed], translate=broken, now=now)
    again = [await db.news_general_candidates.find_one({'_id': 'c'})]
    assert await tr.translate_visible(db, again, translate=broken, now=now + timedelta(minutes=1))
    broken.assert_awaited_once()
    await tr.translate_visible(db, again, translate=broken, now=now + timedelta(minutes=6))
    assert broken.await_count == 2


@pytest.mark.asyncio
async def test_title_change_during_request_cannot_attach_stale_translation():
    db = AsyncMongoMockClient().test
    item = {'_id': 'a', 'title': 'Old title'}
    await db.news_general_candidates.insert_one(item)
    async def translate(titles):
        await db.news_general_candidates.update_one({'_id': 'a'}, {'$set': {'title': 'New title'}})
        return ['Старый заголовок']
    await tr.translate_visible(db, [item], translate=translate)
    current = await db.news_general_candidates.find_one({'_id': 'a'})
    assert 'translated_title' not in current
    assert tr.display_title(current) == 'New title'


@pytest.mark.asyncio
async def test_fallback_openrouter_on_missing_or_invalid_groq():
    import ai_providers
    import models
    outputs = [json.dumps({'titles': {'0': 'Первая новость', '1': 'Вторая новость'}}, ensure_ascii=False)]
    with patch.object(ai_providers, 'fire_groq_key', return_value='secret-groq'), \
         patch.object(ai_providers, 'fire_openrouter_key', return_value='secret-or'), \
         patch.object(models, 'GROQ_FAST_MODELS', ['openai/gpt-oss-20b']), \
         patch.object(models, 'OPENROUTER_TEXT_MODEL', 'vendor/model:free'), \
         patch.object(tr, '_provider', new=AsyncMock(side_effect=['invalid', *outputs])) as provider:
        assert await tr.translate_batch(['One', 'Two']) == ['Первая новость', 'Вторая новость']
        assert provider.await_count == 2
        assert provider.await_args_list[0].args[0].startswith('https://api.groq.com/')
        assert provider.await_args_list[1].args[0].startswith('https://openrouter.ai/')
        assert provider.await_args_list[0].args[2]['response_format'] == {'type': 'json_object'}


def test_reject_bad_or_untranslated_model_output():
    assert tr._parse_result('{"titles":{"0":"Перевод"}}', 2) is None
    assert tr._parse_result('{"titles":{"1":"Перевод"}}', 1) is None
    assert tr._parse_result('{"titles":{"0":"Untranslated"}}', 1) is None
    assert tr._parse_result('{"titles":{"0":"Перевод", "x":"лишнее"}}', 1) is None
    assert tr._parse_result('garbage', 1) is None


@pytest.mark.asyncio
async def test_provider_limits_response_and_never_follows_redirects():
    class Content:
        def __init__(self, data):
            self.data = data
        async def iter_chunked(self, size):
            yield self.data
    class Response:
        def __init__(self, status, data):
            self.status = status
            self.content = Content(data)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
    class Session:
        def __init__(self, status, data):
            self.response = Response(status, data)
            self.kwargs = None
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def post(self, url, **kwargs):
            self.kwargs = kwargs
            return self.response
    good = Session(200, json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': 'ok'}}]}).encode())
    with patch.object(tr.aiohttp, 'ClientSession', return_value=good):
        assert await tr._provider('https://example.com', 'secret', {'model': 'x'}) == 'ok'
    assert good.kwargs['allow_redirects'] is False
    assert good.kwargs['headers']['Authorization'] == 'Bearer secret'
    for status, body in [(302, b'{}'), (200, b'x' * 65537), (200, b'invalid'),
                         (200, b'{"choices":[{"finish_reason":"length"}]}')]:
        with patch.object(tr.aiohttp, 'ClientSession', return_value=Session(status, body)):
            assert await tr._provider('https://example.com', 'secret', {}) is None


@pytest.mark.asyncio
async def test_failure_cooldown_does_not_suppress_changed_title():
    db = AsyncMongoMockClient().test
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    item = {'_id': 'a', 'title': 'Old title'}
    await db.news_general_candidates.insert_one(item)
    await tr.translate_visible(db, [item], translate=AsyncMock(return_value=None), now=now)
    await db.news_general_candidates.update_one({'_id': 'a'}, {'$set': {'title': 'New title'}})
    item = await db.news_general_candidates.find_one({'_id': 'a'})
    translate = AsyncMock(return_value=['Новый заголовок'])
    assert not await tr.translate_visible(db, [item], translate=translate, now=now + timedelta(seconds=1))
    translate.assert_awaited_once_with(['New title'])
    stored = await db.news_general_candidates.find_one({'_id': 'a'})
    assert 'translation_retry_at' not in stored and 'translation_retry_source' not in stored
