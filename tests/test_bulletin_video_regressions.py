import copy
from unittest.mock import AsyncMock, patch

import pytest
from mongomock_motor import AsyncMongoMockClient
from news_prototype import bulletin_video_candidate, owner_bulletin, owner_bulletin_sources
from news_prototype import telegram_public_source as source, article_fetch
from tests.test_bulletin_foundation import response
from tests.test_owner_drafts import SOURCE
from tests.test_telegram_public_source import page, CAPTION


def test_long_caption_is_marked_incomplete_even_when_prefixes_match():
    prefix = 'А' * source.MAX_TEXT
    first = page().replace(CAPTION.encode(), (prefix + ' первая концовка').encode())
    second = page().replace(CAPTION.encode(), (prefix + ' другая концовка').encode())
    p1 = source.parse_widget('https://t.me/ExampleNews/102', first)
    p2 = source.parse_widget('https://t.me/ExampleNews/102', second)
    assert p1.caption == p2.caption == prefix
    assert not p1.caption_complete and not p2.caption_complete


@pytest.mark.asyncio
async def test_incomplete_caption_does_not_advertise_candidate():
    data = page().replace(CAPTION.encode(), ('А' * (source.MAX_TEXT + 1)).encode())
    class Content:
        async def iter_chunked(self, _): yield data
    class Response:
        status=200; headers={}; content_type='text/html'; content_length=len(data); charset='utf-8'; content=Content()
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        def get(self, *args, **kwargs): return Response()
    with patch.object(article_fetch.aiohttp, 'ClientSession', return_value=Session()):
        result = await article_fetch.fetch_article('https://t.me/ExampleNews/102')
    assert len(result['summary']) == source.MAX_TEXT
    assert 'telegram_video_status' not in result


@pytest.mark.asyncio
async def test_candidate_rejects_incomplete_caption_without_downloading(tmp_path):
    db = AsyncMongoMockClient().test
    choices = [{'url': 'https://t.me/ExampleNews/102', 'title': 'Пост'},
               {'url': 'https://example.com/x', 'title': 'Новость'}]
    await owner_bulletin_sources.create(db, 123, 99, choices)
    async def fetch(url): return {'summary': SOURCE, 'telegram_video_status': 'available'}
    await owner_bulletin_sources.collect(db, 123, 99, fetch, AsyncMock())
    await owner_bulletin.prepare(db, 123, 99, AsyncMock(side_effect=response))
    item = await owner_bulletin.get(db, 123, 99)
    post = source.Post('https://t.me/ExampleNews/102', 'ExampleNews/102', SOURCE.strip(),
                       'available', 'https://cdn4.telesco.pe/file/video', caption_complete=False)
    with patch.object(source, 'fetch_post', return_value=post), patch.object(source, 'download_video') as download:
        with pytest.raises(ValueError):
            await bulletin_video_candidate.acquire(db, 123, 99, item['revision'], 1, tmp_path / 'candidate')
        download.assert_not_called()


@pytest.mark.asyncio
async def test_candidate_checks_source_revision_of_same_snapshot_not_an_earlier_read():
    db = AsyncMongoMockClient().test
    choices = [{'url': 'https://t.me/ExampleNews/102', 'title': 'Пост'},
               {'url': 'https://example.com/x', 'title': 'Новость'}]
    await owner_bulletin_sources.create(db, 123, 99, choices)
    async def fetch(url): return {'summary': SOURCE, 'telegram_video_status': 'available'}
    await owner_bulletin_sources.collect(db, 123, 99, fetch, AsyncMock())
    await owner_bulletin.prepare(db, 123, 99, AsyncMock(side_effect=response))
    current = await owner_bulletin.get(db, 123, 99)
    invalid = copy.deepcopy(current)
    invalid['choices'][0]['url'] = 'https://t.me/ExampleNews/103'
    # Older code accepted this second read because sections/stories still
    # matched the revision after a separate earlier approval check.
    with patch.object(owner_bulletin, 'get', AsyncMock(return_value=invalid)):
        with pytest.raises(ValueError, match='источников изменился'):
            await bulletin_video_candidate._current_story(db, 123, 99, current['revision'], 1)
