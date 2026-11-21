import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
try:
    from mongomock_motor import AsyncMongoMockClient
except ImportError:
    AsyncMongoMockClient = None
from news_prototype import article_fetch, editor_client, owner_drafts, owner_trial, story_editor

URL = 'https://t.me/ExampleNews/102'
SOURCE = 'Исходная новость о том, что команда перестала принимать статичные изображения. ' * 3
SCRIPT = {'ticker': 'СТАТИЧНЫЕ МАКЕТЫ БОЛЬШЕ НЕ ПРИНИМАЮТ', 'turns': [
    {'speaker': 'Бендер', 'text': 'Статичные макеты больше не принимают.'},
    {'speaker': 'Киберсенсей', 'text': 'Проверь свою салфетку, Бендер.'}]}
DRAFT = {'decision': 'draft', 'verification': 'needs_review', 'script': SCRIPT,
         'evidence': [{'kind': 'fact', 'quote': 'перестала принимать статичные изображения'}]}


@unittest.skipIf(AsyncMongoMockClient is None, 'mongomock-motor optional')
class DraftFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        self.fetch = AsyncMock(return_value={'title': 'Источник', 'summary': SOURCE,
                                              'resolved_url': URL + '?embed=1'})
        self.draft = AsyncMock(return_value=DRAFT)

    async def create_ready(self, draft_id=10):
        self.assertTrue(await owner_drafts.create(self.db, 123, draft_id, URL, 'суть'))
        self.assertEqual(await owner_drafts.process_new(self.db, 123, draft_id, self.fetch, self.draft), 'draft_ready')
        return await owner_drafts.get(self.db, 123, draft_id)

    def test_input_public_post_and_accent(self):
        self.assertEqual(owner_drafts.prepare_input('/news_prepare https://t.me/ExampleNews/102\nАкцент: что изменилось'),
                         (URL, 'что изменилось'))
        self.assertEqual(owner_drafts.prepare_input('/news_prepare https://example.com/story'),
                         ('https://example.com/story', ''))
        for bad in ('/news_prepare https://t.me/c/123/4', '/news_prepare http://example.com/a',
                    '/news_prepare https://127.0.0.1/', '/news_prepare https://t.me/Name/1\nЧто-то: test',
                    '/news_prepare https://example.com/x\nАкцент: one\nАкцент: two'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                owner_drafts.prepare_input(bad)

    async def test_prepare_is_durable_and_does_not_render_before_approval(self):
        item = await self.create_ready()
        self.assertEqual(item['verification'], 'needs_review')
        self.assertEqual(item['summary'], SOURCE)
        self.assertEqual(item['accent'], 'суть')
        self.assertEqual(self.draft.await_args.args[0]['summary'], SOURCE)
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
        self.assertFalse(await owner_drafts.create(self.db, 123, 10, URL, 'суть'))
        self.assertEqual(await owner_drafts.process_new(self.db, 123, 10, self.fetch, self.draft), 'already_started')
        self.fetch.assert_awaited_once(); self.draft.assert_awaited_once()
        self.assertIsNone(await owner_drafts.get(self.db, 999, 10))

    async def test_channel_name_is_preserved_and_spoken_in_new_single_story(self):
        self.fetch.return_value['telegram_channel_name'] = 'Example News'
        item = await self.create_ready()
        self.assertEqual(item['telegram_channel_name'], 'Example News')
        self.assertTrue(item['script']['turns'][0]['text'].startswith(
            'В телеграм-канале «Example News» поделились новостью. '))
        self.assertIn('Example News', owner_drafts.editable_message(10, URL, item['script']))
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_missing_source_requests_text_and_never_calls_model_twice(self):
        self.fetch.side_effect = article_fetch.ArticleError('unavailable')
        await owner_drafts.create(self.db, 123, 10, URL, '')
        self.assertEqual(await owner_drafts.process_new(self.db, 123, 10, self.fetch, self.draft), 'needs_source')
        self.draft.assert_not_awaited()
        with self.assertRaises(ValueError):
            await owner_drafts.provide_source(self.db, 123, 10, 'short', self.draft)
        self.assertEqual(await owner_drafts.provide_source(self.db, 123, 10, SOURCE, self.draft), 'draft_ready')
        self.assertEqual(await owner_drafts.provide_source(self.db, 123, 10, SOURCE, self.draft), 'not_waiting')
        self.draft.assert_awaited_once()
        self.assertEqual((await owner_drafts.get(self.db, 123, 10))['text'], SOURCE.strip())
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_concurrent_duplicate_prepare_starts_one_editor(self):
        await owner_drafts.create(self.db, 123, 10, URL, '')
        result = await asyncio.gather(*(owner_drafts.process_new(self.db, 123, 10, self.fetch, self.draft) for _ in range(2)))
        self.assertEqual(result.count('draft_ready'), 1)
        self.assertEqual(result.count('already_started'), 1)
        self.fetch.assert_awaited_once(); self.draft.assert_awaited_once()

    async def test_editor_failure_and_skip_never_auto_retry(self):
        await owner_drafts.create(self.db, 123, 10, URL, '')
        self.draft.side_effect = RuntimeError('secret')
        self.assertEqual(await owner_drafts.process_new(self.db, 123, 10, self.fetch, self.draft), 'editor_failed')
        self.assertEqual(await owner_drafts.process_new(self.db, 123, 10, self.fetch, self.draft), 'already_started')
        self.draft.assert_awaited_once()
        await owner_drafts.create(self.db, 123, 11, URL, '')
        self.draft.side_effect = None; self.draft.return_value = {'decision': 'skip'}
        self.assertEqual(await owner_drafts.process_new(self.db, 123, 11, self.fetch, self.draft), 'skipped')

    async def test_editor_failure_keeps_allowlisted_cause_and_can_be_retried_manually(self):
        from news_prototype import editor_client
        await owner_drafts.create(self.db, 123, 12, URL, '')
        self.draft.side_effect = editor_client.EditorialError('внутренний текст', code='max_tokens')
        self.assertEqual(await owner_drafts.process_new(self.db, 123, 12, self.fetch, self.draft), 'editor_failed')
        item = await owner_drafts.get(self.db, 123, 12)
        self.assertEqual(item['stage'], 'editor_failed')
        self.assertEqual(item['editor_error'], {'code': 'max_tokens', 'phase': 'editor'})
        self.assertNotIn('внутренний текст', str(item))
        self.draft.side_effect = None
        self.assertEqual(await owner_drafts.retry(self.db, 123, 12, self.draft), 'draft_ready')
        item = await owner_drafts.get(self.db, 123, 12)
        self.assertNotIn('editor_error', item)
        self.assertEqual(item['stage'], 'draft_ready')

    async def test_retry_is_only_for_failed_stage_and_never_fetches_again(self):
        ready = await self.create_ready(13)
        self.assertEqual(ready['stage'], 'draft_ready')
        self.assertEqual(await owner_drafts.retry(self.db, 123, 13, self.draft), 'not_failed')
        self.assertEqual(await owner_drafts.retry(self.db, 123, 99, self.draft), 'not_found')
        self.assertEqual(self.fetch.await_count, 1)

    async def test_retry_after_http_failure_keeps_status_for_the_owner(self):
        from news_prototype import editor_client
        await owner_drafts.create(self.db, 123, 14, URL, '')
        self.draft.side_effect = editor_client.EditorialError('boom', code='http', http_status=429)
        await owner_drafts.process_new(self.db, 123, 14, self.fetch, self.draft)
        item = await owner_drafts.get(self.db, 123, 14)
        self.assertEqual(item['editor_error'], {'code': 'http', 'phase': 'editor', 'http_status': 429})
        self.draft.side_effect = None
        self.assertEqual(await owner_drafts.retry(self.db, 123, 14, self.draft), 'draft_ready')

    async def test_source_failure_keeps_a_safe_reason_for_the_owner(self):
        from news_prototype.telegram_post import GroupedPost, GROUPED_MESSAGE
        await owner_drafts.create(self.db, 123, 21, URL, '')
        self.fetch.side_effect = GroupedPost(GROUPED_MESSAGE)
        self.assertEqual(await owner_drafts.process_new(self.db, 123, 21, self.fetch, self.draft), 'needs_source')
        item = await owner_drafts.get(self.db, 123, 21)
        self.assertEqual(item['source_error'], {'code': 'grouped_post'})
        self.assertNotIn('Эксплойт', str(item['source_error']))
        self.draft.assert_not_awaited()
        # A supplied text clears the stored reason before the editor runs.
        await owner_drafts.provide_source(self.db, 123, 21, SOURCE, self.draft)
        self.assertNotIn('source_error', await owner_drafts.get(self.db, 123, 21))

    async def test_source_error_codes_are_allowlisted(self):
        from news_prototype.article_fetch import ArticleError
        from news_prototype.telegram_post import GroupedPost, GROUPED_MESSAGE
        cases = [(ValueError('Не найден текст'), 'no_text'),
                 (ArticleError('Страница недоступна'), 'unavailable'),
                 (GroupedPost(GROUPED_MESSAGE), 'grouped_post'),
                 (RuntimeError('секрет'), 'unknown')]
        for exc, code in cases:
            with self.subTest(code=code):
                reason = owner_drafts.source_error(exc)
                self.assertEqual(reason, {'code': code})
                self.assertNotIn('секрет', str(reason))

    async def test_edit_uses_compare_and_set_and_source_is_immutable(self):
        item = await self.create_ready()
        text = owner_drafts.editable_message(10, URL, item['script'])
        parsed = owner_drafts.parse_edit(text.replace('Проверь свою салфетку', 'Покажи свою салфетку'))
        draft_id, old_rev, url, script = parsed
        self.assertEqual(draft_id, 10)
        self.assertEqual(await owner_drafts.edit(self.db, 123, 10, old_rev, url, script), 'saved')
        edited = await owner_drafts.get(self.db, 123, 10)
        self.assertNotEqual(edited['revision'], old_rev)
        self.assertEqual(await owner_drafts.edit(self.db, 123, 10, old_rev, url, script), 'stale')
        self.assertEqual(await owner_drafts.approve(self.db, 123, 10, old_rev), 'stale')
        with self.assertRaises(ValueError):
            await owner_drafts.edit(self.db, 123, 10, edited['revision'], 'https://example.com/a', script)
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_approval_is_explicit_and_idempotent(self):
        item = await self.create_ready()
        self.assertEqual(await owner_drafts.approve(self.db, 123, 10, item['revision']), 'queued')
        self.assertEqual(await owner_drafts.approve(self.db, 123, 10, item['revision']), 'already_queued')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 1)
        self.assertEqual(await owner_trial.status(self.db), 'queued')

    async def test_previous_confirmed_delivery_can_be_reopened_only_on_approval(self):
        old_url, old_script = owner_trial.parse_message('/news_trial\nИсточник: https://example.com/a\n'
            'Строка: ПРОБА\nБендер: Первая\nКиберсенсей: Вторая')
        await owner_trial.admit(self.db, 123, old_url, old_script)
        old_job = await self.db.news_owner_trial_jobs.find_one({})
        await self.db.news_owner_trial_jobs.update_one({'_id': old_job['_id']},
            {'$set': {'stage': 'review_sent', 'review_message_id': 9}})
        item = await self.create_ready()
        self.assertEqual(await owner_trial.status(self.db), 'review_sent')
        self.assertEqual(await owner_drafts.approve(self.db, 123, 10, item['revision']), 'queued')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 2)
        self.assertEqual((await self.db.news_owner_trial_jobs.find_one({'_id': old_job['_id']}))['stage'], 'review_sent')
        self.assertEqual(await owner_trial.status(self.db), 'queued')

    async def test_ongoing_or_uncertain_old_job_blocks_new_approval(self):
        old_url, old_script = owner_trial.parse_message('/news_trial\nИсточник: https://example.com/a\n'
            'Строка: ПРОБА\nБендер: Первая\nКиберсенсей: Вторая')
        await owner_trial.admit(self.db, 123, old_url, old_script)
        job = await self.db.news_owner_trial_jobs.find_one({})
        item = await self.create_ready()
        for stage in ('queued', 'running', 'review_uncertain', 'sending_review', 'failed', 'interrupted'):
            await self.db.news_owner_trial_jobs.update_one({'_id': job['_id']}, {'$set': {'stage': stage}})
            self.assertEqual(await owner_drafts.approve(self.db, 123, 10, item['revision']), 'trial_busy')
            self.assertEqual((await owner_drafts.get(self.db, 123, 10))['stage'], 'draft_ready')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 1)

    async def test_ambiguous_admission_is_never_automatically_retried(self):
        item = await self.create_ready()
        original = owner_trial.admit
        async def lost_response(*args):
            await original(*args)
            raise RuntimeError('lost result')
        with patch.object(owner_trial, 'admit', side_effect=lost_response) as admit:
            self.assertEqual(await owner_drafts.approve(self.db, 123, 10, item['revision']), 'approval_uncertain')
            self.assertEqual(await owner_drafts.approve(self.db, 123, 10, item['revision']), 'not_ready')
            admit.assert_awaited_once()
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 1)
        self.assertEqual(await owner_trial.status(self.db), 'queued')

    async def test_double_approve_creates_only_one_job(self):
        item = await self.create_ready()
        results = await asyncio.gather(*(owner_drafts.approve(self.db, 123, 10, item['revision']) for _ in range(2)))
        self.assertEqual(results.count('queued'), 1)
        self.assertTrue(all(x in ('queued', 'already_queued', 'changed', 'not_ready') for x in results))
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 1)

    def test_multiline_or_oversize_editorial_scripts_are_rejected_before_showing(self):
        bad = dict(SCRIPT, turns=[{'speaker':'Бендер', 'text':'один\nдва'}, SCRIPT['turns'][1]])
        with self.assertRaises(ValueError): owner_drafts.editable_message(10, URL, bad)
        bad = dict(SCRIPT, turns=[{'speaker':'Бендер', 'text':'x'*480}]*9 + [SCRIPT['turns'][1]])
        with self.assertRaises(ValueError): owner_drafts.editable_message(10, URL, bad)


@unittest.skipIf(AsyncMongoMockClient is None, 'mongomock-motor optional')
class DraftHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        from handlers import news_owner_drafts
        self.handler = news_owner_drafts
        self.patchers = [patch.object(news_owner_drafts, 'ADMIN_ID', 123),
                         patch.object(news_owner_drafts, 'db', SimpleNamespace(db=self.db)),
                         patch.dict(os.environ, {'NEWS_OWNER_DRAFT_ENABLED':'1', 'NEWS_OWNER_TRIAL_ENABLED':'1',
                             **{k:'0' for k in owner_trial.OTHER_FLAGS}})]
        from handlers import owner_news_actions
        self.patchers.append(patch.object(owner_news_actions, 'db', SimpleNamespace(db=self.db)))
        for p in self.patchers: p.start(); self.addAsyncCleanup(self.stop_patch, p)

    async def stop_patch(self, p): p.stop()

    def msg(self, text, user=123, chat='private', message_id=10):
        return SimpleNamespace(from_user=SimpleNamespace(id=user), chat=SimpleNamespace(type=chat),
                               message_id=message_id, text=text, answer=AsyncMock())

    async def test_only_owner_private_and_enabled(self):
        for user, chat in ((1,'private'), (123,'group')):
            msg = self.msg('/news_prepare ' + URL, user=user, chat=chat)
            await self.handler.prepare(msg); msg.answer.assert_not_awaited()
        self.assertEqual(await self.db.news_owner_drafts.count_documents({}), 0)
        with patch.dict(os.environ, {'NEWS_OWNER_DRAFT_ENABLED':'0'}):
            msg = self.msg('/news_prepare ' + URL)
            await self.handler.prepare(msg)
            self.assertIn('выключена', msg.answer.await_args.args[0])
        self.assertEqual(await self.db.news_owner_drafts.count_documents({}), 0)

    async def test_image_approval_fails_closed_for_old_worker_then_can_remove(self):
        from io import BytesIO
        from PIL import Image
        from news_prototype import news_image_codec
        data = BytesIO(); Image.new('RGB', (20, 20), 'red').save(data, format='PNG')
        thumb = news_image_codec.make_thumbnail(data.getvalue())
        await owner_drafts.create(self.db, 123, 10, URL, '')
        await owner_drafts.process_new(self.db, 123, 10, AsyncMock(return_value={'summary':SOURCE,
            'media': {'url':'https://cdn4.telesco.pe/file/x', 'kind':'photo'}}),
            AsyncMock(return_value=DRAFT), AsyncMock(return_value=thumb))
        item = await owner_drafts.get(self.db, 123, 10)
        msg = self.msg(f'/news_approve 10 {item["revision"]}')
        with patch('news_prototype.news_worker_client.NewsWorkerClient', side_effect=ValueError('no worker')):
            await self.handler.approve_draft(msg)
        self.assertIn('Не удалось проверить воркер', msg.answer.await_args.args[0])
        self.assertIn('черновик сохранён', msg.answer.await_args.args[0])
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
        self.assertEqual(await owner_drafts.remove_image(self.db, 123, 10, item['revision']), 'saved')
        item = await owner_drafts.get(self.db, 123, 10)
        with patch('news_prototype.news_worker_client.NewsWorkerClient', return_value=SimpleNamespace(
                capabilities=AsyncMock(return_value={'news-story-bulletin-studio-v1'}))):
            await self.handler.approve_draft(self.msg(f'/news_approve 10 {item["revision"]}'))
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 1)

    async def test_failed_editor_card_offers_buttons_instead_of_commands(self):
        await owner_drafts.create(self.db, 123, 10, URL, '')
        await owner_drafts.process_new(self.db, 123, 10, AsyncMock(return_value={'summary': SOURCE}),
                                       AsyncMock(side_effect=editor_client.EditorialError(
                                           'x', code='refused')))
        item = await owner_drafts.get(self.db, 123, 10)
        self.assertEqual(item['stage'], 'editor_failed')
        self.assertEqual(item['editor_error']['code'], 'refused')
        msg = self.msg('/news_draft 10')
        await self.handler.show(msg, 10)
        texts = [call.args[0] for call in msg.answer.await_args_list]
        self.assertTrue(any('фильтр безопасности' in text for text in texts))
        self.assertFalse(any('/news_retry' in text or '/news_approve' in text for text in texts))
        keyboards = [call.kwargs.get('reply_markup') for call in msg.answer.await_args_list]
        callbacks = [b.callback_data for markup in keyboards if markup for row in markup.inline_keyboard for b in row]
        self.assertTrue(any(c.startswith('newsact:retry:') for c in callbacks))
        self.assertTrue(any(c.startswith('newsact:source:') for c in callbacks))
        self.assertTrue(any(c.startswith('news_panel:view_drafts:10') for c in callbacks))
        # Owner-supplied text is accepted on a failed draft, not only on a fetch failure.
        with patch.object(story_editor, 'prompt_for'):
            self.assertEqual(await owner_drafts.provide_source(self.db, 123, 10, SOURCE, AsyncMock(return_value=DRAFT)),
                             'draft_ready')

    async def test_failure_detail_is_stored_and_explained_without_model_text(self):
        await owner_drafts.create(self.db, 123, 10, URL, '')
        error = editor_client.EditorialError('внутренний текст модели', code='invalid_schema',
                                            detail='answer:bad_turn')
        await owner_drafts.process_new(self.db, 123, 10, AsyncMock(return_value={'summary': SOURCE}),
                                       AsyncMock(side_effect=error))
        item = await owner_drafts.get(self.db, 123, 10)
        self.assertEqual(item['editor_error']['detail'], 'answer:bad_turn')
        text = self.handler.editor_failure_text(item)
        self.assertIn('answer:bad_turn', text)
        self.assertNotIn('внутренний текст модели', text)
        # An unknown hint is dropped entirely instead of being echoed back.
        self.assertNotIn('detail', owner_drafts.diagnostic(
            editor_client.EditorialError('x', code='invalid_schema', detail='answer:`secret`'), phase='editor'))

    async def test_missing_picture_says_why_it_is_missing(self):
        from news_prototype import article_fetch as fetch_mod
        await owner_drafts.create(self.db, 123, 10, URL, '')
        article = {'summary': SOURCE, 'media': {'url': 'https://cdn4.telesco.pe/file/x', 'kind': 'photo'}}
        failed = AsyncMock(side_effect=fetch_mod.ArticleError('Изображение слишком большое'))
        await owner_drafts.process_new(self.db, 123, 10, AsyncMock(return_value=article),
                                       AsyncMock(return_value=DRAFT), failed)
        item = await owner_drafts.get(self.db, 123, 10)
        # The story itself is not blocked by a missing picture.
        self.assertEqual(item['stage'], 'draft_ready')
        self.assertEqual(item['image_error'], 'too_large')
        self.assertNotIn('image_b64', item['script'])
        status = self.handler.image_status(item)
        self.assertIn('больше допустимого размера', status)
        self.assertNotIn('не нашлось', status)
        # A post with no media at all keeps its own wording, not a false error.
        self.assertIn('не нашлось', self.handler.image_status({'script': {}}))
        # An unreadable thumbnail is reported as such, never as "no image".
        self.assertIn('прочитать', self.handler.image_status({'image_analysis': 'unavailable', 'script': {}}))

    async def test_card_warns_when_facts_rest_on_a_picture_without_one(self):
        # Post text is a joke line; the numbers came from image_description.
        await owner_drafts.create(self.db, 123, 10, URL, '')
        await owner_drafts.process_new(self.db, 123, 10, AsyncMock(return_value={'summary': SOURCE}),
                                       AsyncMock(return_value=DRAFT))
        item = await owner_drafts.get(self.db, 123, 10)
        counts = story_editor.evidence_sources(item['script'], item)
        self.assertIn('text', counts)
        self.assertEqual(self.handler.source_warning(item), '')
        # Now the same draft where a quote exists only in the image description
        # and the image itself is gone.
        item = item | {'image_description': 'агент молится 500 раз в минуту',
                       'evidence': [{'kind': 'fact', 'quote': 'агент молится 500 раз в минуту'},
                                    {'kind': 'comment', 'quote': ''}]}
        counts = story_editor.evidence_sources(item['script'], item)
        self.assertEqual(counts['image'], 1)
        warning = self.handler.source_warning(item)
        self.assertIn('только описанием картинки', warning)
        # With the picture present there is nothing to warn about.
        item['script']['image_b64'] = 'x'
        self.assertEqual(self.handler.source_warning(item), '')
        # A quote nobody can find in the text is flagged as probably from a picture.
        item['script'].pop('image_b64')
        item = item | {'image_description': ''}
        self.assertIn('вероятно, они с картинки', self.handler.source_warning(item))

    async def test_panel_card_explains_missing_picture_and_source_risk(self):
        await owner_drafts.create(self.db, 123, 10, URL, '')
        article = {'summary': SOURCE, 'media': {'url': 'https://cdn4.telesco.pe/file/x', 'kind': 'photo'}}
        from news_prototype import article_fetch as fetch_mod
        await owner_drafts.process_new(self.db, 123, 10, AsyncMock(return_value=article),
                                       AsyncMock(return_value=DRAFT),
                                       AsyncMock(side_effect=fetch_mod.ArticleError('Изображение недоступно')))
        item = await owner_drafts.get(self.db, 123, 10)
        self.assertEqual(item['image_error'], 'unavailable')
        msg = self.msg('/news_draft 10')
        await self.handler.show(msg, 10, panel_mode=True, item=item)
        texts = [call.args[0] for call in msg.answer.await_args_list]
        joined = '\n'.join(texts)
        self.assertIn('Без картинки', joined)
        self.assertIn('Telegram не отдал файл', joined)
        self.assertNotIn('Картинка не найдена или убрана', joined)

    async def test_image_error_codes_are_fixed_and_hide_provider_text(self):
        from news_prototype import article_fetch as fetch_mod
        for message, code in (
                ('Изображение слишком большое', 'too_large'),
                ('Неподдерживаемое сжатие', 'compression'),
                ('Перенаправление изображения недоступно', 'redirect'),
                ('Не удалось безопасно получить изображение', 'network'),
                ('Изображение недоступно', 'unavailable'),
                ('секретный адрес https://x/y', 'other')):
            self.assertEqual(owner_drafts.image_error_code(fetch_mod.ArticleError(message)), code)
        self.assertEqual(owner_drafts.image_error_code(ValueError('плохой jpeg')), 'codec')

    async def test_every_draft_screen_has_navigation_buttons(self):
        await owner_drafts.create(self.db, 123, 10, URL, '')
        msg = self.msg('/news_draft 10')
        await self.handler.show(msg, 10)  # stage 'new': nothing to act on yet
        markup = msg.answer.await_args.kwargs.get('reply_markup')
        callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn('news_panel:scripts', callbacks)
        self.assertIn('news_panel:view_drafts:10', callbacks)

    async def test_trial_status_explains_missing_image_capability(self):
        from handlers import news_owner_trial as handler
        from io import BytesIO
        from PIL import Image
        from news_prototype import news_image_codec
        buf = BytesIO(); Image.new('RGB', (10, 10), 'red').save(buf, format='PNG')
        script = SCRIPT | {'image_b64': news_image_codec.make_thumbnail(buf.getvalue())}
        await owner_trial.admit(self.db, 123, URL, script)
        msg = self.msg('/news_trial_status')
        with patch.object(handler, 'ADMIN_ID', 123), patch.object(handler, 'db', SimpleNamespace(db=self.db)), \
             patch('news_prototype.news_worker_client.NewsWorkerClient', side_effect=ValueError('no worker')):
            await handler.trial_status(msg)
        self.assertIn('проверить поддержку изображения', msg.answer.await_args.args[0])
        self.assertEqual(await owner_trial.status(self.db), 'queued')

    async def test_no_image_story_waits_for_new_studio_worker(self):
        await owner_drafts.create(self.db, 123, 10, URL, '')
        await owner_drafts.process_new(self.db, 123, 10, AsyncMock(return_value={'summary': SOURCE}),
                                       AsyncMock(return_value=DRAFT))
        item = await owner_drafts.get(self.db, 123, 10)
        msg = self.msg(f'/news_approve 10 {item["revision"]}')
        client = SimpleNamespace(capabilities=AsyncMock(return_value={'news-story-v1'}))
        with patch('news_prototype.news_worker_client.NewsWorkerClient', return_value=client):
            await self.handler.approve_draft(msg)
        self.assertIn('новая студия', msg.answer.await_args.args[0])
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
        self.assertEqual((await owner_drafts.get(self.db, 123, 10))['stage'], 'draft_ready')

    async def test_handler_prepares_shows_and_approves_only_explicit_command(self):
        msg = self.msg('/news_prepare ' + URL + '\nАкцент: суть')
        with patch.object(self.handler.article_fetch, 'fetch_article', AsyncMock(return_value={'summary': SOURCE})), \
             patch.object(self.handler, 'make_draft', AsyncMock(return_value=DRAFT)):
            await self.handler.prepare(msg)
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
        item = await owner_drafts.get(self.db, 123, 10)
        self.assertEqual(item['stage'], 'draft_ready')
        texts = [call.args[0] for call in msg.answer.await_args_list]
        self.assertTrue(any(text.startswith('/news_edit 10 ') for text in texts))
        # Every action is a button on the card; the chat never prints a
        # command with a revision hash for the owner to retype.
        self.assertFalse(any('/news_approve' in text for text in texts))
        approval = self.msg('/news_approve 10 ' + item['revision'], message_id=11)
        with patch('news_prototype.news_worker_client.NewsWorkerClient', return_value=SimpleNamespace(
                capabilities=AsyncMock(return_value={'news-story-bulletin-studio-v1'}))):
            await self.handler.approve_draft(approval)
        self.assertIn('поставлен', approval.answer.await_args.args[0])
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 1)


if __name__ == '__main__': unittest.main()
