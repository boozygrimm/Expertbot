import asyncio
from io import BytesIO
import unittest
from unittest.mock import AsyncMock
from mongomock_motor import AsyncMongoMockClient
from PIL import Image
from news_prototype import owner_bulletin, owner_bulletin_sources as sources, news_image_codec
from tests.test_bulletin_foundation import response
from tests.test_owner_drafts import SOURCE


def choices(n):
    return [{'url': f'https://example.com/story/{i}', 'title': f'Новость {i}'} for i in range(n)]


def thumbnail():
    f = BytesIO(); Image.new('RGB', (200, 100), 'red').save(f, 'PNG')
    return news_image_codec.make_thumbnail(f.getvalue())


class SourceCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        self.owner, self.number = 123, 99

    async def test_up_to_20_no_story_editor_and_only_one_claim(self):
        self.assertTrue(await sources.create(self.db, self.owner, self.number, choices(20)))
        self.assertFalse(await sources.create(self.db, self.owner, self.number, choices(20)))
        active = 0; maximum = 0
        async def fetch(url):
            nonlocal active, maximum
            active += 1; maximum = max(maximum, active)
            await asyncio.sleep(.005)
            active -= 1
            return {'title': url.rsplit('/', 1)[-1], 'summary': SOURCE}
        fake = AsyncMock(side_effect=fetch)
        image = AsyncMock()
        result = await asyncio.gather(sources.collect(self.db, self.owner, self.number, fake, image),
                                      sources.collect(self.db, self.owner, self.number, fake, image))
        self.assertCountEqual(result, ['new', 'not_new'])
        self.assertEqual(fake.await_count, 20)
        self.assertLessEqual(maximum, 4)
        image.assert_not_awaited()
        item = await owner_bulletin.get(self.db, self.owner, self.number)
        self.assertEqual([s['url'] for s in item['stories']], [c['url'] for c in choices(20)])
        self.assertTrue(await owner_bulletin._sources_current(self.db, self.owner, item))
        self.assertEqual(await self.db.news_owner_drafts.count_documents({}), 0)
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
        editor = AsyncMock(side_effect=lambda items: response(items))
        results = await asyncio.gather(owner_bulletin.prepare(self.db, self.owner, self.number, editor),
                                       owner_bulletin.prepare(self.db, self.owner, self.number, editor))
        self.assertCountEqual(results, ['ready_for_review', 'not_new'])
        editor.assert_awaited_once()
        item = await owner_bulletin.get(self.db, self.owner, self.number)
        self.assertEqual(len((await owner_bulletin.approved_payload(self.db, self.owner, self.number, item['revision']))['sections']), 20)

    async def test_missing_text_blocks_editor_and_manual_completion_is_versioned(self):
        await sources.create(self.db, self.owner, self.number, choices(3))
        async def fetch(url):
            if url.endswith('/0') or url.endswith('/1'): raise OSError('offline')
            return {'summary': SOURCE, 'title': 'Взято с сайта'}
        self.assertEqual(await sources.collect(self.db, self.owner, self.number, fetch, AsyncMock()), 'source_missing')
        item = await owner_bulletin.get(self.db, self.owner, self.number)
        self.assertEqual(item['missing_ids'], [1, 2])
        editor = AsyncMock()
        self.assertEqual(await owner_bulletin.prepare(self.db, self.owner, self.number, editor), 'not_new')
        editor.assert_not_awaited()
        self.assertEqual(await sources.provide_source(self.db, self.owner, self.number, 1, item['source_revision'], SOURCE), 'source_missing')
        self.assertEqual(await sources.provide_source(self.db, self.owner, self.number, 2, item['source_revision'], SOURCE), 'stale')
        item = await owner_bulletin.get(self.db, self.owner, self.number)
        with self.assertRaises(ValueError):
            await sources.provide_source(self.db, self.owner, self.number, 2, item['source_revision'], 'коротко')
        self.assertEqual(await sources.provide_source(self.db, self.owner, self.number, 2, item['source_revision'], SOURCE), 'new')
        item = await owner_bulletin.get(self.db, self.owner, self.number)
        self.assertEqual(item['missing_ids'], [])
        self.assertTrue(await owner_bulletin._sources_current(self.db, self.owner, item))
        editor.assert_not_awaited()

    async def test_images_are_associated_and_invalid_media_has_neutral_fallback(self):
        await sources.create(self.db, self.owner, self.number, choices(3))
        async def fetch(url):
            index = url.rsplit('/', 1)[-1]
            media = {'kind':'article_image', 'url': 'https://example.com/image.png'}
            if index == '1': media['url'] = 'https://127.0.0.1/private'
            if index == '2': media = {'kind':'photo', 'url':'https://example.com/not-telegram.png'}
            return {'summary': SOURCE, 'media': media}
        image = AsyncMock(return_value=thumbnail())
        self.assertEqual(await sources.collect(self.db, self.owner, self.number, fetch, image), 'new')
        item = await owner_bulletin.get(self.db, self.owner, self.number)
        self.assertIn('image_b64', item['stories'][0])
        self.assertTrue(all('image_b64' not in s for s in item['stories'][1:]))
        image.assert_awaited_once_with('https://example.com/image.png', article=True)
        self.assertEqual(await owner_bulletin.prepare(self.db, self.owner, self.number,
            AsyncMock(side_effect=lambda items: response(items))), 'ready_for_review')
        item = await owner_bulletin.get(self.db, self.owner, self.number)
        self.assertEqual(item['sections'][0]['image_b64'], item['stories'][0]['image_b64'])
        self.assertNotIn('image_b64', item['sections'][1])

    async def test_urls_duplicates_conflicts_and_tampered_snapshots_rejected(self):
        for bad in (choices(1), choices(21), [choices(2)[0]] * 2,
                    [{'url':'https://127.0.0.1/x', 'title':'a'}, choices(2)[1]],
                    [{'url':'https://t.me/c/123/4', 'title':'a'}, choices(2)[1]]):
            with self.assertRaises(ValueError): await sources.create(self.db, self.owner, self.number, bad)
        await sources.create(self.db, self.owner, self.number, choices(2))
        with self.assertRaises(ValueError): await sources.create(self.db, self.owner, self.number, choices(3))
        await sources.collect(self.db, self.owner, self.number, AsyncMock(return_value={'summary':SOURCE}), AsyncMock())
        item = await owner_bulletin.get(self.db, self.owner, self.number)
        item['stories'][0]['summary'] = 'чужой текст ' * 30
        await self.db.news_owner_bulletins.update_one({'_id': item['_id']}, {'$set': {'stories': item['stories']}})
        editor = AsyncMock()
        self.assertEqual(await owner_bulletin.prepare(self.db, self.owner, self.number, editor), 'stale')
        editor.assert_not_awaited()


class TelegramVideoSourceReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_status_is_bound_to_source_revision_but_never_sneaks_into_v1_render(self):
        db = AsyncMongoMockClient().test
        picked = [{'url': 'https://t.me/ExampleNews/102', 'title': 'Пост'},
                  {'url': 'https://example.com/story/2', 'title': 'Сайт'}]
        await sources.create(db, 123, 77, picked)
        async def fetch(url):
            return {'summary': SOURCE, 'telegram_video_status': 'available',
                    'video_url': 'https://cdn4.telesco.pe/file/secret'}
        await sources.collect(db, 123, 77, fetch, AsyncMock())
        item = await owner_bulletin.get(db, 123, 77)
        self.assertEqual(item['stories'][0]['telegram_video_status'], 'available')
        self.assertNotIn('telegram_video_status', item['stories'][1])
        self.assertNotIn('video_url', repr(item))
        self.assertTrue(await owner_bulletin._sources_current(db, 123, item))
        await owner_bulletin.prepare(db, 123, 77, AsyncMock(side_effect=lambda stories: response(stories)))
        reviewed = await owner_bulletin.get(db, 123, 77)
        payload = await owner_bulletin.approved_payload(db, 123, 77, reviewed['revision'])
        self.assertNotIn('telegram_video_status', repr(payload))
        self.assertNotIn('video_url', repr(payload))
        tampered = reviewed['stories']
        tampered[0]['telegram_video_status'] = 'none'
        await db.news_owner_bulletins.update_one({'_id': reviewed['_id']}, {'$set': {'stories': tampered}})
        with self.assertRaises(ValueError):
            await owner_bulletin.approved_payload(db, 123, 77, reviewed['revision'])

    async def test_unknown_or_forged_hint_does_not_add_video(self):
        db = AsyncMongoMockClient().test
        picked = [{'url': 'https://t.me/ExampleNews/102', 'title': 'Пост'},
                  {'url': 'https://example.com/story/2', 'title': 'Сайт'}]
        await sources.create(db, 123, 78, picked)
        async def fetch(url):
            return {'summary': SOURCE, 'telegram_video_status': {'bogus': True}}
        await sources.collect(db, 123, 78, fetch, AsyncMock())
        item = await owner_bulletin.get(db, 123, 78)
        self.assertTrue(all('telegram_video_status' not in story for story in item['stories']))
