import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
from mongomock_motor import AsyncMongoMockClient
from handlers import owner_news_actions as news, owner_tools_panel as tools, news_owner_drafts
from news_prototype import news_image_fetch, owner_drafts, owner_program, owner_trial
from tests.test_owner_drafts import DRAFT, SOURCE


class NewsActionsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        fake = SimpleNamespace(db=self.db)
        for module in (news, tools, news_owner_drafts):
            p = patch.object(module, 'db', fake); p.start(); self.addCleanup(p.stop)
        p = patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)); p.start(); self.addCleanup(p.stop)
        self.user = SimpleNamespace(id=news.ADMIN_ID)
        self.message = SimpleNamespace(chat=SimpleNamespace(id=news.ADMIN_ID, type='private'), message_id=321,
            answer=AsyncMock(), answer_photo=AsyncMock(), reply=AsyncMock(), edit_text=AsyncMock())
        self.callback = SimpleNamespace(from_user=self.user, message=self.message, answer=AsyncMock(), data='')
        self.state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock(), update_data=AsyncMock(), get_data=AsyncMock())

    async def ready(self, number):
        await owner_drafts.create(self.db, news.ADMIN_ID, number, f'https://t.me/ExampleNews/{number}', '')
        await owner_drafts.process_new(self.db, news.ADMIN_ID, number, AsyncMock(return_value={'summary': SOURCE}),
                                       AsyncMock(return_value=DRAFT))
        return await owner_drafts.get(self.db, news.ADMIN_ID, number)

    async def action(self, data):
        self.callback.data = data
        await news.callback_action(self.callback, self.state)

    async def test_card_requires_current_revision_owner_and_confirmation(self):
        item = await self.ready(12)
        await news.card(self.message, news.ADMIN_ID, 'draft', item)
        buttons = self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard
        approve = next(b.callback_data for row in buttons for b in row if b.text.startswith('🎬'))
        self.callback.from_user = SimpleNamespace(id=news.ADMIN_ID + 1)
        await self.action(approve)
        self.assertEqual(await self.db.owner_panel_confirmations.count_documents({}), 0)
        self.callback.from_user = self.user
        await self.action(approve)
        confirmation = await self.db.owner_panel_confirmations.find_one({})
        self.assertEqual(confirmation['data']['revision'], item['revision'])
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
        await self.db.news_owner_drafts.update_one({'_id': item['_id']}, {'$set': {'revision': 'f'*64}})
        await self.action(approve)
        self.assertIn('изменилось', self.callback.answer.await_args.args[0])
        self.assertEqual(await self.db.owner_panel_confirmations.count_documents({}), 1)

    async def test_failed_draft_shows_reason_and_one_retry_button(self):
        from news_prototype import editor_client
        await owner_drafts.create(self.db, news.ADMIN_ID, 15, 'https://t.me/ExampleNews/15', '')
        await owner_drafts.process_new(self.db, news.ADMIN_ID, 15, AsyncMock(return_value={'summary': SOURCE}),
                                       AsyncMock(side_effect=editor_client.EditorialError('q', code='http')))
        item = await owner_drafts.get(self.db, news.ADMIN_ID, 15)
        self.assertEqual(item['stage'], 'editor_failed')
        await news.card(self.message, news.ADMIN_ID, 'draft', item)
        buttons = self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard
        retry = next(b.callback_data for row in buttons for b in row if b.text.startswith('🔄'))
        detail = news_owner_drafts.editor_failure_text(item)
        self.assertIn('HTTP', detail)
        self.assertEqual(await owner_drafts.retry(self.db, news.ADMIN_ID, 15, AsyncMock(return_value=DRAFT)), 'draft_ready')
        # The stale card must not retry again once the draft is no longer failed.
        await self.action(retry)
        self.assertEqual((await owner_drafts.get(self.db, news.ADMIN_ID, 15))['stage'], 'draft_ready')

    async def test_needs_source_explains_album_posts(self):
        from news_prototype.telegram_post import GroupedPost, GROUPED_MESSAGE
        await owner_drafts.create(self.db, news.ADMIN_ID, 31, 'https://t.me/exploitex/38046', '')
        fetch = AsyncMock(side_effect=GroupedPost(GROUPED_MESSAGE))
        await owner_drafts.process_new(self.db, news.ADMIN_ID, 31, fetch, AsyncMock())
        item = await owner_drafts.get(self.db, news.ADMIN_ID, 31)
        self.assertEqual(item['stage'], 'needs_source')
        detail = news_owner_drafts.source_failure_text(item)
        self.assertIn('альбом', detail)
        self.assertEqual(news_owner_drafts.source_failure_text({}), '')

    async def test_editor_failure_text_never_invents_unknown_codes(self):
        self.assertIn('не записана', news_owner_drafts.editor_failure_text({}))
        self.assertIn('не распознана', news_owner_drafts.editor_failure_text({'editor_error': {'code': 'hack'}}))
        self.assertIn('квота', news_owner_drafts.editor_failure_text(
            {'editor_error': {'code': 'http', 'http_status': 429}}))

    async def test_multi_story_preview_offers_program_not_single_render(self):
        item = await self.ready(12)
        self.message.from_user = self.user
        self.message.panel_mode = True
        await news_owner_drafts.show(self.message, 12, item=item, for_program=True)
        markup = self.message.answer.await_args.kwargs['reply_markup']
        buttons = [button for row in markup.inline_keyboard for button in row]
        self.assertTrue(any(button.callback_data == 'newsact:choose' for button in buttons))
        self.assertFalse(any('рендер сюжета' in button.text for button in buttons))
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
        await news_owner_drafts.show(self.message, 12, item=item, for_program=False)
        markup = self.message.answer.await_args.kwargs['reply_markup']
        self.assertTrue(any('рендер сюжета' in button.text for row in markup.inline_keyboard for button in row))

    async def test_edit_mode_uses_captured_revision_and_validation(self):
        item = await self.ready(12)
        await news.card(self.message, news.ADMIN_ID, 'draft', item)
        buttons = self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard
        edit = next(b.callback_data for row in buttons for b in row if b.text.startswith('✏️'))
        await self.action(edit)
        self.assertEqual(self.state.update_data.await_args.kwargs['revision'], item['revision'])
        self.assertNotIn('/news_edit', self.message.answer.await_args.args[0])
        self.message.from_user = self.user
        self.message.text = 'неверный формат'
        self.state.clear.reset_mock()
        self.state.get_data.return_value = {'action': 'news_edit', 'number': 12, 'revision': item['revision']}
        await tools.receive_input(self.message, self.state)
        self.state.clear.assert_not_awaited()
        self.message.text = owner_drafts.editable_message(12, item['url'], item['script']).split('\n', 1)[1]
        with patch.object(news_owner_drafts, 'edit_draft', AsyncMock()) as fn:
            await tools.receive_input(self.message, self.state)
            self.assertTrue(fn.await_args.args[0].text.startswith('/news_edit 12 ' + item['revision']))

    async def test_compact_single_card_and_explicit_media_review(self):
        from io import BytesIO
        from PIL import Image
        from news_prototype import news_image_codec
        out = BytesIO(); Image.new('RGB', (40, 40), 'red').save(out, format='PNG')
        image = news_image_codec.make_thumbnail(out.getvalue())
        await owner_drafts.create(self.db, news.ADMIN_ID, 12, 'https://t.me/ExampleNews/12', '')
        await owner_drafts.process_new(self.db, news.ADMIN_ID, 12,
            AsyncMock(return_value={'summary': SOURCE, 'media': {'kind':'photo', 'url':'https://cdn4.telesco.pe/file/x'}}),
            AsyncMock(return_value=DRAFT), AsyncMock(return_value=image),
            AsyncMock(return_value={'description':'На картинке красный квадрат.', 'regions': [[100, 100, 900, 900]]}))
        item = await owner_drafts.get(self.db, news.ADMIN_ID, 12)
        self.message.from_user = self.user
        self.message.panel_mode = True
        await news_owner_drafts.show(self.message, 12, item=item)
        self.assertEqual(self.message.answer.await_count, 1)
        self.message.answer_photo.assert_not_awaited()
        text = self.message.answer.await_args.args[0]
        self.assertIn('Черновик №12 · сценарий', text)
        self.assertIn('Источник:', text); self.assertIn('Бендер:', text)
        self.assertIn('открой и проверь', text)
        buttons = [b for row in self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
        media = next(b.callback_data for b in buttons if b.text == '🖼 Проверить картинку')
        self.assertFalse(any('Убрать картинку' in b.text for b in buttons))
        await self.action(media)
        self.assertEqual(self.message.answer_photo.await_count, 2)
        self.assertIn('красный квадрат', self.message.answer.await_args.args[0])
        buttons = [b for row in self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertTrue(any('Убрать картинку' in b.text for b in buttons))
        back = next(b.callback_data for b in buttons if b.text == '↩️ К сценарию')
        await self.action(back)
        self.assertIn('Черновик №12 · сценарий', self.message.answer.await_args.args[0])
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
        await self.db.news_owner_drafts.update_one({'_id':item['_id']}, {'$set':{'revision':'b'*64}})
        self.message.answer_photo.reset_mock()
        await self.action(media)
        self.message.answer_photo.assert_not_awaited()
        self.assertIn('изменилось', self.callback.answer.await_args.args[0])

    async def test_media_removal_confirmation_and_program_context(self):
        from io import BytesIO
        from PIL import Image
        from news_prototype.news_image_codec import make_thumbnail
        out = BytesIO(); Image.new('RGB', (40, 40), 'red').save(out, format='PNG')
        image = make_thumbnail(out.getvalue())
        await owner_drafts.create(self.db, news.ADMIN_ID, 12, 'https://t.me/ExampleNews/12', '')
        await owner_drafts.process_new(self.db, news.ADMIN_ID, 12,
            AsyncMock(return_value={'summary': SOURCE, 'media': {'kind': 'photo', 'url': 'https://cdn4.telesco.pe/file/x'}}),
            AsyncMock(return_value=DRAFT), AsyncMock(return_value=image),
            AsyncMock(return_value={'description': 'Тест', 'regions': [[100, 100, 900, 900]]}))
        item = await owner_drafts.get(self.db, news.ADMIN_ID, 12)
        self.message.from_user = self.user; self.message.panel_mode = True
        await news_owner_drafts.show(self.message, 12, item=item, for_program=True)
        media = next(b.callback_data for row in self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard
                     for b in row if b.text == '🖼 Проверить картинку')
        await self.action(media)
        focus = next(b.callback_data for row in self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard
                     for b in row if 'Убрать увеличенные' in b.text)
        await self.action(focus)
        confirm = await self.db.owner_panel_confirmations.find_one({'action': 'nofocus'})
        self.assertTrue(confirm['data']['for_program'])
        self.assertTrue((await owner_drafts.get(self.db, news.ADMIN_ID, 12))['script'].get('image_regions'))
        self.callback.data = 'owner_tools:run:' + confirm['_id']
        self.message.answer.reset_mock()
        await tools.action(self.callback, self.state, None)
        self.assertEqual(self.message.answer.await_count, 1)
        self.assertIn('Автоматические области убраны', self.message.answer.await_args.args[0])
        self.assertTrue(any(b.callback_data == 'newsact:choose' for row in self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row))
        updated = await owner_drafts.get(self.db, news.ADMIN_ID, 12)
        self.assertNotIn('image_regions', updated['script'])
        self.assertTrue(updated['script']['image_b64'])
        media = next(b.callback_data for row in self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row if b.text == '🖼 Проверить картинку')
        await self.action(media)
        remove = next(b.callback_data for row in self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row if b.text == '🖼 Убрать картинку')
        await self.action(remove)
        confirm = await self.db.owner_panel_confirmations.find_one({'action': 'noimage'})
        self.assertTrue((await owner_drafts.get(self.db, news.ADMIN_ID, 12))['script'].get('image_b64'))
        self.callback.data = 'owner_tools:run:' + confirm['_id']
        self.message.answer.reset_mock()
        await tools.action(self.callback, self.state, None)
        self.assertEqual(self.message.answer.await_count, 1)
        self.assertIn('Изображение убрано', self.message.answer.await_args.args[0])
        self.assertFalse((await owner_drafts.get(self.db, news.ADMIN_ID, 12))['script'].get('image_b64'))
        self.assertTrue(any(b.callback_data == 'newsact:choose' for row in self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row))
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_expired_and_foreign_media_card_are_rejected(self):
        item = await self.ready(12)
        await news.card(self.message, news.ADMIN_ID, 'draft', item)
        doc = await self.db.owner_news_cards.find_one({})
        media = 'newsact:media:' + doc['_id']
        self.callback.from_user = SimpleNamespace(id=news.ADMIN_ID + 1)
        await self.action(media)
        self.message.answer_photo.assert_not_awaited()
        self.callback.from_user = self.user
        await self.db.owner_news_cards.update_one({'_id': doc['_id']}, {'$set': {'expires_at': datetime.now(timezone.utc)-timedelta(seconds=1)}})
        await self.action(media)
        self.assertIn('устарела', self.callback.answer.await_args.args[0])
        self.message.answer_photo.assert_not_awaited()

    async def test_edit_saves_one_card_and_program_context(self):
        item = await self.ready(12)
        self.message.from_user = self.user; self.message.panel_mode = True
        await news_owner_drafts.show(self.message, 12, item=item, for_program=True)
        edit = next(b.callback_data for row in self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row if b.text.startswith('✏️'))
        self.message.answer.reset_mock()
        await self.action(edit)
        self.assertEqual(self.message.answer.await_count, 1)
        data = self.state.update_data.await_args.kwargs
        self.assertTrue(data['for_program'])
        self.state.get_data.return_value = data
        self.message.text = self.message.answer.await_args.args[0]
        self.message.answer.reset_mock()
        await tools.receive_input(self.message, self.state)
        self.assertEqual(self.message.answer.await_count, 1)
        self.assertIn('Текст не изменился.', self.message.answer.await_args.args[0])
        self.assertTrue(any(b.callback_data == 'newsact:choose' for row in self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row))

    async def test_no_image_card_and_long_text_are_safe(self):
        item = await self.ready(12)
        self.message.from_user = self.user; self.message.panel_mode = True
        await news_owner_drafts.show(self.message, 12, item=item)
        self.assertEqual(self.message.answer.await_count, 1)
        self.assertIn('Без картинки', self.message.answer.await_args.args[0])
        self.assertIn('не нашлось подходящего изображения', self.message.answer.await_args.args[0])
        self.assertFalse(any(b.text == '🖼 Проверить картинку' for row in self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row))
        self.message.answer_photo.assert_not_awaited()
        self.message.answer.reset_mock()
        with patch.object(owner_drafts, 'editable_message', return_value='/news_edit\n' + '🦔' * 2050):
            await news_owner_drafts.show(self.message, 12, item=item)
        texts = [c.args[0] for c in self.message.answer.await_args_list]
        self.assertGreater(len(texts), 1)
        self.assertTrue(all(len(t.encode('utf-16-le')) // 2 <= 4096 for t in texts))
        self.assertEqual(''.join(texts).count('🦔'), 2050)
        self.assertIn('reply_markup', self.message.answer.await_args.kwargs)

    async def test_media_description_split_by_utf16_limit(self):
        item = await self.ready(12)
        item['script']['image_b64'] = 'unused'
        item['image_analysis'] = 'analyzed'
        item['image_description'] = '🦔' * 2500
        with patch('news_prototype.news_image_codec.decode_thumbnail', return_value=b'image'):
            await news_owner_drafts.show_image_details(self.message, news.ADMIN_ID, item)
        texts = [c.args[0] for c in self.message.answer.await_args_list]
        self.assertGreater(len(texts), 1)
        self.assertTrue(all(len(t.encode('utf-16-le')) // 2 <= 4096 for t in texts))
        self.assertEqual(''.join(texts).count('🦔'), 2500)

    async def test_selection_builds_snapshot_once_and_never_renders(self):
        await self.ready(10); await self.ready(11)
        await self.action('newsact:choose')
        session = await self.db.owner_news_selections.find_one({})
        token = session['_id']
        await self.action(f'newsact:select:{token}:10')
        await self.action(f'newsact:select:{token}:11')
        await self.action(f'newsact:build:{token}')
        self.assertEqual(await self.db.news_owner_programs.count_documents({}), 0)
        confirm = await self.db.owner_panel_confirmations.find_one({})
        self.assertEqual(confirm['data']['draft_ids'], [10, 11])
        self.callback.data = 'owner_tools:run:' + confirm['_id']
        with patch.object(news_owner_drafts, 'show_program', AsyncMock()) as show:
            await tools.action(self.callback, self.state, None)
            await tools.action(self.callback, self.state, None)
            show.assert_awaited_once()
        program = await owner_program.get(self.db, news.ADMIN_ID, 321)
        self.assertEqual(program['stage'], 'ready_for_review')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_selection_includes_only_confirmed_delivered_draft(self):
        await self.ready(10)
        item = await self.ready(11)
        await owner_drafts.approve(self.db, news.ADMIN_ID, 11, item['revision'])
        await self.action('newsact:choose')
        session = await self.db.owner_news_selections.find_one({'stage': 'open'})
        self.assertEqual([entry['number'] for entry in session['items']], [10])
        job = await self.db.news_owner_trial_jobs.find_one({})
        await self.db.news_owner_trial_jobs.update_one({'_id': job['_id']},
            {'$set': {'stage': 'review_sent', 'review_message_id': 55}})
        await self.action('newsact:choose')
        session = await self.db.owner_news_selections.find_one({'stage': 'open', 'items.number': 11})
        self.assertEqual({entry['number'] for entry in session['items']}, {10, 11})
        self.assertIn('уже отправлен', next(x['label'] for x in session['items'] if x['number'] == 11))
        token = session['_id']
        await self.action(f'newsact:select:{token}:10')
        await self.action(f'newsact:select:{token}:11')
        await self.action(f'newsact:build:{token}')
        conf = await self.db.owner_panel_confirmations.find_one({})
        self.callback.data = 'owner_tools:run:' + conf['_id']
        with patch.object(news_owner_drafts, 'show_program', AsyncMock()):
            await tools.action(self.callback, self.state, None)
        self.assertEqual(len((await owner_program.get(self.db, news.ADMIN_ID, 321))['stories']), 2)
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 1)

    async def test_changed_draft_or_expired_selection_cannot_build(self):
        item = await self.ready(10); await self.ready(11)
        await self.action('newsact:choose')
        session = await self.db.owner_news_selections.find_one({}); token = session['_id']
        await self.action(f'newsact:select:{token}:10'); await self.action(f'newsact:select:{token}:11')
        await self.action(f'newsact:build:{token}')
        confirm = await self.db.owner_panel_confirmations.find_one({})
        await self.db.news_owner_drafts.update_one({'_id': item['_id']}, {'$set': {'revision': 'a'*64}})
        self.callback.data = 'owner_tools:run:' + confirm['_id']
        await tools.action(self.callback, self.state, None)
        self.assertEqual(await self.db.news_owner_programs.count_documents({}), 0)
        await self.action('newsact:choose')
        session = await self.db.owner_news_selections.find_one({'stage':'open'})
        await self.db.owner_news_selections.update_one({'_id': session['_id']}, {'$set': {'expires_at': datetime.now(timezone.utc)-timedelta(seconds=1)}})
        await self.action('newsact:select:' + session['_id'] + ':10')
        self.assertIn('устарел', self.callback.answer.await_args.args[0])

    async def test_panel_view_renders_review_and_buttons_from_same_snapshot(self):
        from handlers import news_owner_panel
        item = await self.ready(12)
        self.callback.data = 'news_panel:view_drafts:12'
        with patch.object(news_owner_panel, 'db', SimpleNamespace(db=self.db)):
            await news_owner_panel.news_panel_action(self.callback)
        texts = [call.args[0] for call in self.message.answer.await_args_list]
        self.assertTrue(any('Черновик №12 · сценарий' in text for text in texts))
        self.assertFalse(any('/news_edit' in text or '/news_approve' in text for text in texts))
        card = await self.db.owner_news_cards.find_one({})
        self.assertEqual(card['revision'], item['revision'])

    async def test_card_offers_image_retry_only_when_the_picture_is_missing(self):
        from news_prototype import article_fetch as fetch_mod
        from news_prototype import news_image_codec
        from io import BytesIO
        from PIL import Image
        await owner_drafts.create(self.db, news.ADMIN_ID, 12, 'https://t.me/ExampleNews/12', '')
        article = {'summary': SOURCE, 'media': {'url': 'https://cdn4.telesco.pe/file/x', 'kind': 'photo'}}
        await owner_drafts.process_new(self.db, news.ADMIN_ID, 12, AsyncMock(return_value=article),
                                       AsyncMock(return_value=DRAFT),
                                       AsyncMock(side_effect=fetch_mod.ArticleError('Изображение недоступно')))
        item = await owner_drafts.get(self.db, news.ADMIN_ID, 12)
        await news.card(self.message, news.ADMIN_ID, 'draft', item)
        buttons = [b for row in self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
        retry = next(b.callback_data for b in buttons if 'загрузку картинки' in b.text)
        data = BytesIO(); Image.new('RGB', (160, 90), 'blue').save(data, format='JPEG')
        thumbnail = news_image_codec.make_thumbnail(data.getvalue())
        with patch.object(news_image_fetch, 'fetch_thumbnail', AsyncMock(return_value=thumbnail)), \
             patch.object(news_owner_drafts, 'analyze_image', AsyncMock(return_value={'description': 'мем'})):
            await self.action(retry)
        fresh = await owner_drafts.get(self.db, news.ADMIN_ID, 12)
        self.assertIn('image_b64', fresh['script'])
        self.assertEqual(fresh['stage'], 'draft_ready')
        texts = '\n'.join(c.args[0] for c in self.message.answer.await_args_list if c.args)
        self.assertIn('Картинка загружена заново', texts)
        # With the picture attached the button is gone.
        await news.card(self.message, news.ADMIN_ID, 'draft', fresh)
        labels = [b.text for row in self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertFalse(any('загрузку картинки' in label for label in labels))
        self.assertTrue(any('Проверить картинку' in label for label in labels))

    async def test_confirmation_uses_captured_approval_revision_once(self):
        item = await self.ready(12)
        await news.card(self.message, news.ADMIN_ID, 'draft', item)
        markup = self.message.answer.await_args.kwargs['reply_markup']
        approve = next(b.callback_data for row in markup.inline_keyboard for b in row if b.text.startswith('🎬'))
        await self.action(approve)
        conf = await self.db.owner_panel_confirmations.find_one({})
        self.callback.data = 'owner_tools:run:' + conf['_id']
        with patch.object(news_owner_drafts, 'approve_draft', AsyncMock()) as fn:
            await tools.action(self.callback, self.state, None)
            await tools.action(self.callback, self.state, None)
            self.assertEqual(fn.await_count, 1)
            self.assertEqual(fn.await_args.args[0].text, f'/news_approve 12 {item["revision"]}')

    async def test_program_page_rejects_invalid_or_stale_card(self):
        first = await self.ready(10); await self.ready(11)
        await owner_program.create(self.db, news.ADMIN_ID, 300, [10,11])
        program = await owner_program.get(self.db, news.ADMIN_ID, 300)
        await news.card(self.message, news.ADMIN_ID, 'program', program)
        markup = self.message.answer.await_args.kwargs['reply_markup']
        approve = markup.inline_keyboard[0][0].callback_data
        await self.db.news_owner_programs.update_one({'_id': program['_id']}, {'$set': {'revision':'e'*64}})
        await self.action(approve)
        self.assertIn('изменилось', self.callback.answer.await_args.args[0])
        self.assertEqual(await self.db.owner_panel_confirmations.count_documents({}), 0)

    async def test_dispatcher_real_news_card_and_build_flow(self):
        import subprocess, sys
        script = r'''
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiogram import Bot
from aiogram.types import Update
from aiogram.methods import SendMessage
from mongomock_motor import AsyncMongoMockClient
from config import ADMIN_ID
from app_setup import configure_dispatcher
from handlers import owner_news_actions, owner_tools_panel, news_owner_panel, news_owner_drafts
from news_prototype import owner_drafts, owner_trial
from tests.test_owner_drafts import DRAFT, SOURCE

async def run():
    database = AsyncMongoMockClient().test
    fake = SimpleNamespace(db=database, settings=database.settings, alchemy_players=database.alchemy_players)
    bot = Bot('123456:TESTTOKEN')
    sent = []
    async def request(bot_arg, method, **kwargs):
        sent.append(method)
        return True
    dp = configure_dispatcher()
    with patch.object(owner_news_actions, 'db', fake), patch.object(owner_tools_panel, 'db', fake), \
         patch.object(news_owner_panel, 'db', fake), patch.object(news_owner_drafts, 'db', fake), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)), \
         patch.object(bot.session, 'make_request', AsyncMock(side_effect=request)):
        for n in (10, 11):
            await owner_drafts.create(database, ADMIN_ID, n, f'https://t.me/ExampleNews/{n}', '')
            await owner_drafts.process_new(database, ADMIN_ID, n, AsyncMock(return_value={'summary':SOURCE}), AsyncMock(return_value=DRAFT))
        user = {'id':ADMIN_ID, 'is_bot':False, 'first_name':'Owner'}
        chat = {'id':ADMIN_ID, 'type':'private'}
        async def cb(i, data, message_id=321):
            update = Update.model_validate({'update_id':i, 'callback_query':{
                'id':str(i), 'chat_instance':'owner', 'from':user, 'data':data,
                'message':{'message_id':message_id, 'date':1760000000, 'chat':chat,
                           'from':{'id':123456,'is_bot':True,'first_name':'Bot'}, 'text':'Panel'}}})
            await asyncio.wait_for(dp.feed_update(bot, update), 5)
        await cb(1, 'news_panel:view_drafts:10')
        assert any(isinstance(m, SendMessage) and 'Черновик №10 · сценарий' in m.text for m in sent), sent
        await cb(2, 'newsact:choose')
        session = await database.owner_news_selections.find_one({})
        token = session['_id']
        await cb(3, f'newsact:select:{token}:10')
        await cb(4, f'newsact:select:{token}:11')
        await cb(5, f'newsact:build:{token}')
        assert await database.news_owner_programs.count_documents({}) == 0
        confirm = await database.owner_panel_confirmations.find_one({'action':'build_program'})
        await cb(6, 'owner_tools:run:' + confirm['_id'], 322)
        assert await database.news_owner_programs.count_documents({'stage':'ready_for_review'}) == 1
        assert await database.news_owner_trial_jobs.count_documents({}) == 0
        assert any(isinstance(m, SendMessage) and m.text == 'Если все сюжеты проверены, '
                'подтверди один общий рендер кнопкой ниже:' for m in sent)
    await bot.session.close()
asyncio.run(run())
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    async def test_immediate_discovery_does_not_prepare_or_render(self):
        from handlers import news_owner_panel
        from news_prototype import owner_discovery
        now = datetime.now(timezone.utc).isoformat()
        items = [{'source':'Test', 'title':f'News {i}', 'url':f'https://example.com/{i}',
                  'published_at':now} for i in (1,2)]
        self.callback.data = 'news_panel:discover'
        with patch.object(news_owner_panel, 'db', SimpleNamespace(db=self.db)), \
             patch.object(owner_discovery, 'collect', AsyncMock(return_value=(items, ['OpenAI']))), \
             patch.object(owner_discovery, 'translate_titles', AsyncMock(return_value=None)), \
             patch.object(news_owner_drafts, 'prepare_urls', AsyncMock()) as prepare:
            await news_owner_panel.news_panel_action(self.callback)
            prepare.assert_not_awaited()
        self.assertEqual(await self.db.news_owner_panel_sessions.count_documents({}), 1)
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
        self.assertTrue(any('OpenAI' in call.args[0] for call in self.message.answer.await_args_list))
