import asyncio
import unittest
from unittest.mock import AsyncMock
from mongomock_motor import AsyncMongoMockClient
from news_prototype import owner_bulletin, owner_drafts
from tests.test_owner_drafts import SOURCE, DRAFT
from tests.test_bulletin_foundation import response


class OwnerBulletinTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        for number in (10, 11):
            await owner_drafts.create(self.db, 123, number, f'https://example.com/news/{number}', '')
            await owner_drafts.process_new(self.db, 123, number,
                AsyncMock(return_value={'summary': SOURCE, 'title': f'Новость {number}'}), AsyncMock(return_value=DRAFT))

    async def test_snapshot_one_request_review_and_no_render(self):
        self.assertTrue(await owner_bulletin.create(self.db, 123, 99, [10, 11]))
        self.assertFalse(await owner_bulletin.create(self.db, 123, 99, [10, 11]))
        editor = AsyncMock(side_effect=lambda stories: response(stories))
        results = await asyncio.gather(owner_bulletin.prepare(self.db, 123, 99, editor),
                                       owner_bulletin.prepare(self.db, 123, 99, editor))
        self.assertCountEqual(results, ['ready_for_review', 'not_new'])
        editor.assert_awaited_once()
        saved = await owner_bulletin.get(self.db, 123, 99)
        result = await owner_bulletin.approved_payload(self.db, 123, 99, saved['revision'])
        self.assertEqual([s['id'] for s in result['sections']], [10, 11])
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
        with self.assertRaises(ValueError):
            await owner_bulletin.approved_payload(self.db, 123, 99, '0' * 64)
        self.assertIsNone(await owner_bulletin.get(self.db, 124, 99))

    async def test_source_change_and_bad_response_do_not_dispatch(self):
        await owner_bulletin.create(self.db, 123, 99, [10, 11])
        await self.db.news_owner_drafts.update_one({'draft_id': 10}, {'$set': {'summary': 'changed source'}})
        editor = AsyncMock()
        self.assertEqual(await owner_bulletin.prepare(self.db, 123, 99, editor), 'stale')
        editor.assert_not_awaited()
        await self.db.news_owner_drafts.update_one({'draft_id': 10}, {'$set': {'summary': SOURCE}})
        editor.return_value = {'decision': 'draft', 'sections': []}
        self.assertEqual(await owner_bulletin.prepare(self.db, 123, 99, editor), 'editor_failed')
        self.assertEqual(await owner_bulletin.prepare(self.db, 123, 99, editor), 'not_new')
        editor.assert_awaited_once()
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_edited_source_invalidates_review(self):
        await owner_bulletin.create(self.db, 123, 99, [10, 11])
        self.assertEqual(await owner_bulletin.prepare(self.db, 123, 99,
            AsyncMock(side_effect=lambda stories: response(stories))), 'ready_for_review')
        saved = await owner_bulletin.get(self.db, 123, 99)
        await self.db.news_owner_drafts.update_one({'draft_id': 11}, {'$set': {'title': 'изменено'}})
        with self.assertRaises(ValueError): await owner_bulletin.approved_payload(self.db, 123, 99, saved['revision'])

    async def test_editor_insufficient_does_not_repeat(self):
        await owner_bulletin.create(self.db, 123, 99, [10, 11])
        editor = AsyncMock(return_value={'decision': 'insufficient', 'ids': [10]})
        self.assertEqual(await owner_bulletin.prepare(self.db, 123, 99, editor), 'needs_source')
        self.assertEqual((await owner_bulletin.get(self.db, 123, 99))['missing_ids'], [10])
        self.assertEqual(await owner_bulletin.prepare(self.db, 123, 99, editor), 'not_new')
        editor.assert_awaited_once()


class OwnerBulletinEditTests(OwnerBulletinTests):
    async def prepared(self):
        await owner_bulletin.create(self.db, 123, 99, [10, 11])
        await owner_bulletin.prepare(self.db, 123, 99, AsyncMock(side_effect=lambda stories: response(stories)))
        return await owner_bulletin.get(self.db, 123, 99)

    async def test_edits_are_revision_bound_and_never_assert_unverified_quotes(self):
        item = await self.prepared()
        section = item['sections'][0]
        text = owner_bulletin.editable_section(section)
        text = text.replace(section['turns'][0]['text'], 'Исправленный факт, требующий проверки.')
        self.assertEqual(await owner_bulletin.change_section(self.db, 123, 99, item['revision'], 10, text=text), 'saved')
        updated = await owner_bulletin.get(self.db, 123, 99)
        turn = updated['sections'][0]['turns'][0]
        self.assertEqual((turn['kind'], turn['evidence']), ('owner_edit', ''))
        self.assertNotEqual(updated['revision'], item['revision'])
        with self.assertRaises(ValueError):
            await owner_bulletin.approved_payload(self.db, 123, 99, item['revision'])
        self.assertEqual(await owner_bulletin.change_section(self.db, 123, 99, item['revision'], 10, text=text), 'stale')
        self.assertEqual((await owner_bulletin.approved_payload(self.db, 123, 99, updated['revision']))['sections'][0]['turns'][0]['text'], turn['text'])
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_source_changed_or_bad_edit_cannot_mutate(self):
        item = await self.prepared()
        with self.assertRaises(ValueError):
            await owner_bulletin.change_section(self.db, 123, 99, item['revision'], 10, text='Источник: https://example.org')
        await self.db.news_owner_drafts.update_one({'draft_id': 10}, {'$set': {'summary': 'changed'}})
        self.assertEqual(await owner_bulletin.change_section(self.db, 123, 99, item['revision'], 10,
            text=owner_bulletin.editable_section(item['sections'][0])), 'stale_source')
        self.assertEqual((await owner_bulletin.get(self.db, 123, 99))['revision'], item['revision'])

    async def test_concurrent_changes_allow_only_one_version(self):
        item = await self.prepared()
        text = owner_bulletin.editable_section(item['sections'][0])
        first = text.replace('Строка:', 'Строка: ПЕРВЫЙ ВАРИАНТ ', 1)
        second = text.replace('Строка:', 'Строка: ВТОРОЙ ВАРИАНТ ', 1)
        results = await asyncio.gather(
            owner_bulletin.change_section(self.db, 123, 99, item['revision'], 10, text=first),
            owner_bulletin.change_section(self.db, 123, 99, item['revision'], 10, text=second))
        self.assertCountEqual(results, ['saved', 'stale'])
