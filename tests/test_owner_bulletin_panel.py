import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
from mongomock_motor import AsyncMongoMockClient
from PIL import Image
from handlers import owner_bulletin_panel as panel, owner_tools_panel as tools, news_owner_panel
from news_prototype import owner_bulletin, owner_drafts, owner_trial, news_image_codec
from tests.test_bulletin_foundation import response
from tests.test_owner_drafts import SOURCE, DRAFT


def thumbnail():
    out = BytesIO(); Image.new('RGB', (300, 200), 'red').save(out, format='PNG')
    return news_image_codec.make_thumbnail(out.getvalue())


class BulletinPanelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        fake = SimpleNamespace(db=self.db)
        for module in (panel, tools, news_owner_panel):
            p = patch.object(module, 'db', fake); p.start(); self.addCleanup(p.stop)
        p = patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)); p.start(); self.addCleanup(p.stop)
        self.user = SimpleNamespace(id=tools.ADMIN_ID)
        self.message = SimpleNamespace(chat=SimpleNamespace(id=tools.ADMIN_ID, type='private'), message_id=321,
            answer=AsyncMock(return_value=SimpleNamespace(message_id=999)), answer_photo=AsyncMock(),
            reply=AsyncMock(), edit_text=AsyncMock(), delete=AsyncMock(), from_user=self.user)
        self.callback = SimpleNamespace(from_user=self.user, message=self.message, data='', answer=AsyncMock())
        self.state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock(), update_data=AsyncMock(), get_data=AsyncMock())

    async def ready(self, count, picture=False):
        for n in range(1, count + 1):
            await owner_drafts.create(self.db, self.user.id, n, f'https://example.com/news/{n}', '')
            article = {'summary': SOURCE, 'title': f'Новость {n}'}
            image = None
            if picture and n == 1:
                article['media'] = {'kind': 'article_image', 'url': 'https://example.com/image.png'}
                image = AsyncMock(return_value=thumbnail())
            await owner_drafts.process_new(self.db, self.user.id, n, AsyncMock(return_value=article),
                                          AsyncMock(return_value=DRAFT), image)

    async def click(self, data):
        self.callback.data = data
        await panel.callback_action(self.callback, self.state)

    async def prepare_selection(self, count):
        await self.ready(count)
        await self.click('nb:choose')
        session = await self.db.owner_bulletin_selections.find_one({}); token = session['_id']
        for n in range(1, count + 1): await self.click(f'nb:sel:{token}:{n}')
        await self.click(f'nb:prepare:{token}')
        return await self.db.owner_panel_confirmations.find_one({'action': 'prepare_bulletin'})

    async def test_5_10_20_selection_only_one_confirmed_editor_request(self):
        for count in (5, 10, 20):
            with self.subTest(count=count):
                await self.db.owner_bulletin_selections.delete_many({})
                await self.db.owner_panel_confirmations.delete_many({})
                await self.db.news_owner_bulletins.delete_many({})
                await self.db.news_owner_drafts.delete_many({})
                confirm = await self.prepare_selection(count)
                self.assertEqual(confirm['data']['draft_ids'], list(range(1, count + 1)))
                self.assertEqual(await self.db.news_owner_bulletins.count_documents({}), 0)
                self.callback.data = 'owner_tools:run:' + confirm['_id']
                fake = AsyncMock(side_effect=lambda stories: response(stories))
                with patch.object(panel, 'make_bulletin', fake):
                    await asyncio.gather(tools.action(self.callback, self.state, None),
                                         tools.action(self.callback, self.state, None))
                fake.assert_awaited_once()
                item = await owner_bulletin.get(self.db, self.user.id, self.message.message_id)
                self.assertEqual(item['stage'], 'ready_for_review')
                self.assertEqual([s['id'] for s in item['sections']], list(range(1, count + 1)))
                self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
                for call in self.message.answer.await_args_list:
                    text = call.args[0]
                    self.assertLessEqual(len(text.encode('utf-16-le')) // 2, 4096)
                    markup = call.kwargs.get('reply_markup')
                    if markup:
                        for row in markup.inline_keyboard:
                            for button in row:
                                if button.callback_data:
                                    self.assertLessEqual(len(button.callback_data.encode()), 64)
                self.message.answer.reset_mock()

    async def test_unauthorized_expired_and_over_limit(self):
        await self.ready(21)
        self.callback.from_user = SimpleNamespace(id=self.user.id + 1)
        await self.click('nb:choose')
        self.assertEqual(await self.db.owner_bulletin_selections.count_documents({}), 0)
        self.callback.from_user = self.user
        await self.click('nb:choose')
        session = await self.db.owner_bulletin_selections.find_one({}); token = session['_id']
        for n in range(1, 22): await self.click(f'nb:sel:{token}:{n}')
        self.assertEqual(len((await self.db.owner_bulletin_selections.find_one({'_id': token}))['selected']), 20)
        self.assertIn('не больше 20', self.callback.answer.await_args.args[0])
        await self.db.owner_bulletin_selections.update_one({'_id': token}, {'$set': {'expires_at': datetime.now(timezone.utc)-timedelta(seconds=1)}})
        await self.click(f'nb:prepare:{token}')
        self.assertIn('устарел', self.callback.answer.await_args.args[0])
        self.assertEqual(await self.db.owner_panel_confirmations.count_documents({}), 0)

    async def test_changed_source_after_selection_prevents_request(self):
        confirm = await self.prepare_selection(2)
        await self.db.news_owner_drafts.update_one({'draft_id': 1}, {'$set': {'revision': '0' * 64}})
        self.callback.data = 'owner_tools:run:' + confirm['_id']
        with patch.object(panel, 'make_bulletin', AsyncMock()) as fake:
            await tools.action(self.callback, self.state, None)
            fake.assert_not_awaited()
        self.assertEqual(await self.db.news_owner_bulletins.count_documents({}), 0)

    async def test_review_presents_source_evidence_image_and_revision_bound_removal(self):
        await self.ready(2, picture=True)
        await owner_bulletin.create(self.db, self.user.id, 99, [1, 2])
        await owner_bulletin.prepare(self.db, self.user.id, 99, AsyncMock(side_effect=lambda s: response(s)))
        item = await owner_bulletin.get(self.db, self.user.id, 99)
        await panel.show_review(self.message, self.user.id, item)
        messages = [c.args[0] for c in self.message.answer.await_args_list]
        self.assertTrue(any('шуточная заглушка' in s for s in messages))
        self.assertFalse(any('Цитата источника' in s for s in messages))
        self.message.answer_photo.assert_not_awaited()
        review = await self.db.owner_bulletin_actions.find_one({'action': 'review'})
        self.callback.message.message_id = 999
        await self.click(f'nb:detail:{review["_id"]}:1')
        messages = [c.args[0] for c in self.message.answer.await_args_list]
        self.assertTrue(any('https://example.com/news/1' in s and 'Цитата источника' in s for s in messages))
        self.message.answer_photo.assert_awaited_once()
        card = await self.db.owner_bulletin_cards.find_one({'section_id': 1})
        self.callback.message.message_id = 999
        await self.click(f'nb:noimage:{card["_id"]}')
        updated = await owner_bulletin.get(self.db, self.user.id, 99)
        self.assertNotIn('image_b64', updated['sections'][0])
        self.assertNotEqual(item['revision'], updated['revision'])
        await self.click(f'nb:noimage:{card["_id"]}')
        self.assertIn('изменилась', self.callback.answer.await_args.args[0])
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_selection_shows_complete_titles_and_number_buttons(self):
        from datetime import datetime, timezone
        titles = [f'Очень длинный заголовок новости {n} ' + 'важные слова ' * 8 for n in range(1, 11)]
        session = {'_id': '0123456789abcdef', 'items': [
            {'number': n, 'label': title, 'url': f'https://example.com/{n}'}
            for n, title in enumerate(titles, 1)], 'selected': [2, 1], 'page': 0, 'mode': 'direct'}
        await panel.selection(self.message, session)
        text = self.message.edit_text.await_args.args[0]
        self.assertTrue(all(title.strip() in text for title in titles[:8]))
        self.assertIn('✅ 1 · 2.', text)
        self.assertIn('✅ 2 · 1.', text)
        self.assertLessEqual(len(text.encode('utf-16-le')) // 2, 4096)
        buttons = [b for row in self.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
        selects = [b for b in buttons if b.callback_data and ':sel:' in b.callback_data]
        self.assertEqual([b.text for b in selects], ['✅ 1', '✅ 2', '3', '4', '5', '6', '7', '8'])
        self.assertEqual(len([b for b in buttons if b.url]), 8)
        session['page'] = 1
        await panel.selection(self.message, session)
        text = self.message.edit_text.await_args.args[0]
        self.assertTrue(all(title.strip() in text for title in titles[8:]))

    async def test_selection_long_emoji_titles_split_pages_without_truncation(self):
        items = [{'number': i, 'label': '📰' * 300 + f' номер {i}', 'url': f'https://example.com/{i}'}
                 for i in range(1, 9)]
        pages = panel._selection_pages(items)
        self.assertGreater(len(pages), 1)
        self.assertEqual([x for page in pages for x in page], items)
        for page in range(len(pages)):
            session = {'_id': '0123456789abcdef', 'items': items, 'selected': [], 'page': page, 'mode': 'direct'}
            await panel.selection(self.message, session)
            text = self.message.edit_text.await_args.args[0]
            self.assertLessEqual(len(text.encode('utf-16-le')) // 2, 4096)
            for item in pages[page]: self.assertIn(item['label'], text)

    async def test_overview_shows_full_dialogue_but_details_are_on_demand(self):
        await self.ready(5, picture=True)
        await owner_bulletin.create(self.db, self.user.id, 99, [1, 2, 3, 4, 5])
        await owner_bulletin.prepare(self.db, self.user.id, 99, AsyncMock(side_effect=lambda s: response(s)))
        item = await owner_bulletin.get(self.db, self.user.id, 99)
        await panel.show_review(self.message, self.user.id, item)
        text = '\n'.join(c.args[0] for c in self.message.answer.await_args_list)
        for section in item['sections']:
            self.assertIn(section['ticker'], text)
            for turn in section['turns']: self.assertIn(turn['text'], text)
        self.assertIn('включая комментарии', text)
        self.assertNotIn('Цитата источника', text)
        self.assertEqual(await self.db.owner_bulletin_cards.count_documents({}), 0)
        self.message.answer_photo.assert_not_awaited()
        review = await self.db.owner_bulletin_actions.find_one({'action': 'review'})
        self.message.message_id = review['message_id']
        await self.click(f'nb:detail:{review["_id"]}:1')
        self.assertEqual(await self.db.owner_bulletin_cards.count_documents({}), 1)
        self.message.answer_photo.assert_awaited_once()
        self.assertTrue(any('Цитата источника' in c.args[0] for c in self.message.answer.await_args_list))
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_twenty_story_overview_splits_safely_without_losing_text(self):
        await self.ready(20)
        await owner_bulletin.create(self.db, self.user.id, 99, list(range(1, 21)))
        def make(stories):
            result = response(stories)
            for i, section in enumerate(result['sections'], 1):
                section['ticker'] = f'Заголовок {i} ' + '📰' * 45
                section['turns'][0]['text'] = f'Реплика {i}: ' + 'новость ' * 10
            return result
        await owner_bulletin.prepare(self.db, self.user.id, 99, AsyncMock(side_effect=make))
        item = await owner_bulletin.get(self.db, self.user.id, 99)
        await panel.show_review(self.message, self.user.id, item)
        calls = self.message.answer.await_args_list
        self.assertGreater(len(calls), 1)
        combined = ''.join(c.args[0] for c in calls)
        for section in item['sections']:
            self.assertIn(section['ticker'], combined)
            self.assertIn(section['turns'][0]['text'], combined)
        self.assertTrue(all(len(c.args[0].encode('utf-16-le')) // 2 <= 3600 for c in calls))
        markup = calls[-1].kwargs['reply_markup']
        buttons = [b for row in markup.inline_keyboard for b in row if b.callback_data and b.callback_data.startswith('nb:detail:')]
        self.assertEqual(len(buttons), 20)
        self.assertEqual([b.text for b in buttons], [str(i) for i in range(1, 21)])
        self.assertEqual(await self.db.owner_bulletin_cards.count_documents({}), 0)

    async def test_detail_button_rejects_stale_revision_or_wrong_message_and_owner(self):
        await self.ready(2)
        await owner_bulletin.create(self.db, self.user.id, 99, [1, 2])
        await owner_bulletin.prepare(self.db, self.user.id, 99, AsyncMock(side_effect=lambda s: response(s)))
        item = await owner_bulletin.get(self.db, self.user.id, 99)
        await panel.show_review(self.message, self.user.id, item)
        review = await self.db.owner_bulletin_actions.find_one({'action': 'review'})
        self.message.message_id = review['message_id'] + 1
        await self.click(f'nb:detail:{review["_id"]}:1')
        self.assertIn('устарела', self.callback.answer.await_args.args[0])
        self.message.message_id = review['message_id']
        self.callback.from_user = SimpleNamespace(id=self.user.id + 1)
        await self.click(f'nb:detail:{review["_id"]}:1')
        self.assertEqual(await self.db.owner_bulletin_cards.count_documents({}), 0)
        self.callback.from_user = self.user
        text = owner_bulletin.editable_section(item['sections'][0]).replace(item['sections'][0]['ticker'], 'Новый заголовок')
        self.assertEqual(await owner_bulletin.change_section(self.db, self.user.id, 99, item['revision'],
                                                              item['sections'][0]['id'], text=text), 'saved')
        await self.click(f'nb:detail:{review["_id"]}:1')
        self.assertIn('изменилась', self.callback.answer.await_args.args[0])
        self.assertEqual(await self.db.owner_bulletin_cards.count_documents({}), 0)

    async def test_no_retry_after_editor_failure_and_no_render(self):
        confirm = await self.prepare_selection(2)
        self.callback.data = 'owner_tools:run:' + confirm['_id']
        with patch.object(panel, 'make_bulletin', AsyncMock(side_effect=TimeoutError())) as fake:
            await tools.action(self.callback, self.state, None)
            await tools.action(self.callback, self.state, None)
            fake.assert_awaited_once()
        item = await owner_bulletin.get(self.db, self.user.id, self.message.message_id)
        self.assertEqual(item['stage'], 'editor_failed')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_utf16_chunks(self):
        parts = list(panel._chunks('📰' * 5000 + 'test'))
        self.assertEqual(''.join(parts), '📰' * 5000 + 'test')
        self.assertTrue(all(len(s.encode('utf-16-le')) // 2 <= 3600 for s in parts))

    async def test_edit_through_card_and_fsm_does_not_launch_render(self):
        await self.ready(2)
        await owner_bulletin.create(self.db, self.user.id, 99, [1, 2])
        await owner_bulletin.prepare(self.db, self.user.id, 99, AsyncMock(side_effect=lambda s: response(s)))
        item = await owner_bulletin.get(self.db, self.user.id, 99)
        await panel.show_section(self.message, self.user.id, item, item['sections'][0])
        card = await self.db.owner_bulletin_cards.find_one({'section_id': 1})
        self.message.message_id = 999
        await self.click(f'nb:edit:{card["_id"]}')
        args = self.state.update_data.await_args.kwargs
        self.assertEqual(args['revision'], item['revision'])
        self.state.get_data.return_value = args
        self.message.text = owner_bulletin.editable_section(item['sections'][0]).replace(
            item['sections'][0]['turns'][0]['text'], 'Исправленный факт.')
        await tools.receive_input(self.message, self.state)
        updated = await owner_bulletin.get(self.db, self.user.id, 99)
        self.assertEqual(updated['sections'][0]['turns'][0]['kind'], 'owner_edit')
        self.assertEqual(updated['sections'][0]['turns'][0]['text'], 'Исправленный факт.')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_real_dispatcher_selection_confirmation_and_review(self):
        import subprocess, sys
        script = r'''
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiogram import Bot
from aiogram.types import Update, Message
from aiogram.methods import SendMessage, SendPhoto
from mongomock_motor import AsyncMongoMockClient
from app_setup import configure_dispatcher
from config import ADMIN_ID
from handlers import owner_bulletin_panel as panel, owner_tools_panel as tools, news_owner_panel, alc_build, alc_intro
from news_prototype import owner_drafts, owner_trial
from tests.test_bulletin_foundation import response
from tests.test_owner_drafts import SOURCE, DRAFT

async def run():
    database = AsyncMongoMockClient().test
    fake = SimpleNamespace(db=database, settings=database.settings, alchemy_players=database.alchemy_players)
    bot = Bot('123456:TESTTOKEN')
    sent = []
    sequence = 500
    async def request(bot_arg, method, **kwargs):
        nonlocal sequence
        sent.append(method)
        if isinstance(method, (SendMessage, SendPhoto)):
            sequence += 1
            return Message.model_validate({'message_id':sequence, 'date':1760000000,
                 'chat':{'id':ADMIN_ID, 'type':'private'},
                 'from':{'id':123456, 'is_bot':True, 'first_name':'Bot'}, 'text':'response'})
        return True
    dp = configure_dispatcher()
    with patch.object(panel, 'db', fake), patch.object(tools, 'db', fake), \
         patch.object(news_owner_panel, 'db', fake), patch.object(alc_build, 'db', fake), \
         patch.object(alc_intro, 'get_intro_session', AsyncMock(return_value=None)), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)), \
         patch.object(panel, 'make_bulletin', AsyncMock(side_effect=lambda s: response(s))) as editor, \
         patch.object(bot.session, 'make_request', AsyncMock(side_effect=request)):
        for n in (10, 11):
            await owner_drafts.create(database, ADMIN_ID, n, f'https://example.com/{n}', '')
            await owner_drafts.process_new(database, ADMIN_ID, n,
                 AsyncMock(return_value={'summary':SOURCE, 'title':str(n)}), AsyncMock(return_value=DRAFT))
        user = {'id':ADMIN_ID, 'is_bot':False, 'first_name':'Owner'}
        chat = {'id':ADMIN_ID, 'type':'private'}
        async def cb(i, data, message_id=321):
            update = Update.model_validate({'update_id':i, 'callback_query':{
                'id':str(i), 'chat_instance':'owner', 'from':user, 'data':data,
                'message':{'message_id':message_id, 'date':1760000000, 'chat':chat,
                           'from':{'id':123456,'is_bot':True,'first_name':'Bot'}, 'text':'Panel'}}})
            await asyncio.wait_for(dp.feed_update(bot, update), 5)
        await cb(1, 'nb:choose')
        session = await database.owner_bulletin_selections.find_one({}); token = session['_id']
        await cb(2, f'nb:sel:{token}:10')
        await cb(3, f'nb:sel:{token}:11')
        await cb(4, f'nb:prepare:{token}')
        assert editor.await_count == 0
        confirm = await database.owner_panel_confirmations.find_one({'action':'prepare_bulletin'})
        await cb(5, 'owner_tools:run:' + confirm['_id'], 322)
        await cb(6, 'owner_tools:run:' + confirm['_id'], 322)
        assert editor.await_count == 1
        assert await database.news_owner_bulletins.count_documents({'stage':'ready_for_review'}) == 1
        assert await database.news_owner_trial_jobs.count_documents({}) == 0
        assert any(isinstance(m, SendMessage) and 'сценарий' in m.text for m in sent)
        assert not any(isinstance(m, SendMessage) and 'Цитата источника' in m.text for m in sent)
        card = await database.owner_bulletin_actions.find_one({'action':'review'})
        await cb(7, f'nb:detail:{card["_id"]}:1', card['message_id'])
        assert any(isinstance(m, SendMessage) and 'Цитата источника' in m.text for m in sent)
    await bot.session.close()
asyncio.run(run())
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    async def render_ready(self):
        await self.ready(2)
        await owner_bulletin.create(self.db, self.user.id, 99, [1, 2])
        await owner_bulletin.prepare(self.db, self.user.id, 99, AsyncMock(side_effect=lambda s: response(s)))
        item = await owner_bulletin.get(self.db, self.user.id, 99)
        await panel.show_review(self.message, self.user.id, item)
        self.callback.message.message_id = 999
        return item, await self.db.owner_bulletin_actions.find_one({'action':'render'})

    async def test_render_confirmation_cancel_and_stale_edit(self):
        with patch.object(owner_trial, 'runtime_enabled', AsyncMock(return_value=True)):
            item, card = await self.render_ready()
            await self.click('nb:render:' + card['_id'])
            confirmation = await self.db.owner_panel_confirmations.find_one({'action':'render_bulletin'})
            self.assertEqual(confirmation['data']['revision'], item['revision'])
            self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
            self.callback.data = 'owner_tools:cancel:' + confirmation['_id']
            await tools.action(self.callback, self.state, None)
            self.callback.data = 'owner_tools:run:' + confirmation['_id']
            await tools.action(self.callback, self.state, None)
            self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
            await self.click('nb:render:' + card['_id'])
            second = await self.db.owner_panel_confirmations.find_one({'action':'render_bulletin', 'stage':'ready'})
            await owner_bulletin.change_section(self.db, self.user.id, 99, item['revision'], 1,
                text='Строка: Изменено\nБендер: Новая реплика.')
            self.callback.data = 'owner_tools:run:' + second['_id']
            await tools.action(self.callback, self.state, None)
            self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
            self.assertTrue(any('изменились' in c.args[0] for c in self.message.answer.await_args_list))
            await self.click('nb:render:' + card['_id'])
            self.assertIn('изменилась', self.callback.answer.await_args.args[0])

    async def test_render_confirmation_runtime_mode_and_one_time(self):
        with patch.object(owner_trial, 'runtime_enabled', AsyncMock(return_value=True)) as enabled:
            item, card = await self.render_ready()
            await self.click('nb:render:' + card['_id'])
            confirmation = await self.db.owner_panel_confirmations.find_one({'action':'render_bulletin'})
            enabled.return_value = False
            self.callback.data = 'owner_tools:run:' + confirmation['_id']
            await tools.action(self.callback, self.state, None)
            self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
            enabled.return_value = True
            await self.click('nb:render:' + card['_id'])
            confirmation = await self.db.owner_panel_confirmations.find_one({'action':'render_bulletin', 'stage':'ready'})
            self.callback.data = 'owner_tools:run:' + confirmation['_id']
            await asyncio.gather(tools.action(self.callback, self.state, None), tools.action(self.callback, self.state, None))
            self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 1)
            job = await self.db.news_owner_trial_jobs.find_one({})
            self.assertEqual(job['kind'], 'bulletin')
            self.assertEqual((await owner_bulletin.get(self.db, self.user.id, 99))['stage'], 'queued')
            await self.db.news_owner_trial_jobs.update_one({'_id':job['_id']}, {'$set':{'stage':'audio_too_long'}})
            await panel.show_review(self.message, self.user.id, await owner_bulletin.get(self.db, self.user.id, 99))
            card = await self.db.owner_bulletin_actions.find_one({'action':'shorten'})
            await self.click('nb:shorten:' + card['_id'])
            shorten = await self.db.owner_panel_confirmations.find_one({'action':'shorten_bulletin'})
            self.callback.data = 'owner_tools:run:' + shorten['_id']
            await tools.action(self.callback, self.state, None)
            self.assertEqual((await owner_bulletin.get(self.db, self.user.id, 99))['stage'], 'ready_for_review')
            self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 1)
            self.assertTrue(any('Сократи реплики' in c.args[0] for c in self.message.answer.await_args_list))


class BulletinVideoHintReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_shows_video_hint_without_claiming_it_will_be_rendered(self):
        database = AsyncMongoMockClient().test
        message = SimpleNamespace(answer=AsyncMock(return_value=SimpleNamespace(message_id=50)),
                                  answer_photo=AsyncMock(), answer_video=AsyncMock())
        section = {'id': 1, 'url': 'https://t.me/ExampleNews/102', 'ticker': 'Новость',
                   'turns': [{'speaker': 'Бендер', 'text': 'Факт', 'kind': 'fact', 'evidence': 'Цитата'}]}
        item = {'sections': [section], 'stories': [{'draft_id': 1, 'telegram_video_status': 'available'}],
                'bulletin_id': 10, 'revision': 'a' * 64}
        with patch.object(panel, 'db', SimpleNamespace(db=database)):
            await panel.show_section(message, 123, item, section)
        messages = [call.args[0] for call in message.answer.await_args_list]
        self.assertTrue(any('обнаружено видео' in text and 'не использует' in text for text in messages))
        self.assertTrue(any('шуточная заглушка' in text for text in messages))
        message.answer_video.assert_not_awaited()
        self.assertEqual(await database.news_owner_trial_jobs.count_documents({}), 0)
