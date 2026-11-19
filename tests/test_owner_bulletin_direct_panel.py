import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
from mongomock_motor import AsyncMongoMockClient
from handlers import owner_bulletin_panel as panel, owner_tools_panel as tools
from news_prototype import owner_bulletin, owner_daily, owner_trial, article_fetch, news_image_fetch
from tests.test_bulletin_foundation import response
from tests.test_owner_drafts import SOURCE


class DirectBulletinPanelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        fake = SimpleNamespace(db=self.db)
        for module in (panel, tools):
            p = patch.object(module, 'db', fake); p.start(); self.addCleanup(p.stop)
        p = patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)); p.start(); self.addCleanup(p.stop)
        self.user = SimpleNamespace(id=tools.ADMIN_ID)
        self.message = SimpleNamespace(chat=SimpleNamespace(type='private', id=self.user.id), message_id=321,
            from_user=self.user, answer=AsyncMock(return_value=SimpleNamespace(message_id=777)),
            answer_photo=AsyncMock(), edit_text=AsyncMock(), delete=AsyncMock(), reply=AsyncMock())
        self.callback = SimpleNamespace(from_user=self.user, message=self.message, data='', answer=AsyncMock())
        self.state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock(), update_data=AsyncMock(), get_data=AsyncMock())

    async def click(self, data):
        self.callback.data = data
        await panel.callback_action(self.callback, self.state)

    async def choose(self, count):
        items = [{'title':f'Новость {n}', 'url':f'https://example.com/news/{n}'} for n in range(count)]
        with patch.object(owner_daily, 'recent_items', AsyncMock(return_value=items)):
            await self.click('nb:daily')
        session = await self.db.owner_bulletin_selections.find_one({'mode':'direct'})
        token = session['_id']
        for number in range(1, count + 1): await self.click(f'nb:sel:{token}:{number}')
        await self.click(f'nb:prepare:{token}')
        return await self.db.owner_panel_confirmations.find_one({'action':'collect_bulletin_sources'})

    async def test_5_10_20_direct_two_explicit_confirmations_one_editor(self):
        for count in (5, 10, 20):
            with self.subTest(count=count):
                for name in ('owner_bulletin_selections','owner_panel_confirmations','news_owner_bulletins'):
                    await self.db[name].delete_many({})
                confirm = await self.choose(count)
                self.assertEqual(len(confirm['data']['choices']), count)
                self.callback.data = 'owner_tools:run:' + confirm['_id']
                fetch = AsyncMock(return_value={'summary': SOURCE})
                editor = AsyncMock(side_effect=lambda stories: response(stories))
                with patch.object(article_fetch, 'fetch_article', fetch), \
                     patch.object(news_image_fetch, 'fetch_thumbnail', AsyncMock()), \
                     patch.object(panel, 'make_bulletin', editor):
                    editor.assert_not_awaited(); fetch.assert_not_awaited()
                    await asyncio.gather(tools.action(self.callback, self.state, None),
                                         tools.action(self.callback, self.state, None))
                    self.assertEqual(fetch.await_count, count)
                    editor.assert_not_awaited()
                    item = await owner_bulletin.get(self.db, self.user.id, 321)
                    self.assertEqual(item['stage'], 'new')
                    await self.click('nb:editor:321')
                    editor.assert_not_awaited()
                    next_confirm = await self.db.owner_panel_confirmations.find_one({'action':'run_bulletin_editor'})
                    self.callback.data = 'owner_tools:run:' + next_confirm['_id']
                    await asyncio.gather(tools.action(self.callback, self.state, None),
                                         tools.action(self.callback, self.state, None))
                    editor.assert_awaited_once()
                item = await owner_bulletin.get(self.db, self.user.id, 321)
                self.assertEqual(item['stage'], 'ready_for_review')
                self.assertEqual(len(item['sections']), count)
                self.assertEqual(await self.db.news_owner_drafts.count_documents({}), 0)
                self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
                self.assertEqual(await self.db.news_owner_programs.count_documents({}), 0)
                self.message.answer.reset_mock()

    async def test_unavailable_source_requires_manual_text_then_editor_confirmation(self):
        confirm = await self.choose(2)
        async def fetch(url):
            if url.endswith('/0'): raise OSError('unavailable')
            return {'summary': SOURCE}
        self.callback.data = 'owner_tools:run:' + confirm['_id']
        editor = AsyncMock(side_effect=lambda stories: response(stories))
        with patch.object(article_fetch, 'fetch_article', AsyncMock(side_effect=fetch)), \
             patch.object(panel, 'make_bulletin', editor):
            await tools.action(self.callback, self.state, None)
            item = await owner_bulletin.get(self.db, self.user.id, 321)
            self.assertEqual(item['stage'], 'source_missing')
            self.assertEqual(item['missing_ids'], [1]); editor.assert_not_awaited()
            await self.click('nb:editor:321')
            self.assertEqual(await self.db.owner_panel_confirmations.count_documents({'action':'run_bulletin_editor'}), 0)
            await self.click('nb:source:321:1')
            self.state.get_data.return_value = self.state.update_data.await_args.kwargs
            self.message.text = SOURCE
            await tools.receive_input(self.message, self.state)
            item = await owner_bulletin.get(self.db, self.user.id, 321)
            self.assertEqual(item['stage'], 'new'); editor.assert_not_awaited()
            self.assertNotIn('image_b64', item['stories'][0])
            await self.click('nb:editor:321')
            confirm = await self.db.owner_panel_confirmations.find_one({'action':'run_bulletin_editor'})
            self.callback.data = 'owner_tools:run:' + confirm['_id']
            await tools.action(self.callback, self.state, None)
            editor.assert_awaited_once()
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_cancellation_and_revision_change_prevent_paid_request(self):
        confirm = await self.choose(2)
        self.callback.data = 'owner_tools:cancel:' + confirm['_id']
        await tools.action(self.callback, self.state, None)
        self.callback.data = 'owner_tools:run:' + confirm['_id']
        with patch.object(article_fetch, 'fetch_article', AsyncMock()) as fetch:
            await tools.action(self.callback, self.state, None)
            fetch.assert_not_awaited()
        self.assertEqual(await self.db.news_owner_bulletins.count_documents({}), 0)

    async def test_editor_failure_is_not_retried_and_stale_confirmation_is_blocked(self):
        confirm = await self.choose(2)
        self.callback.data = 'owner_tools:run:' + confirm['_id']
        with patch.object(article_fetch, 'fetch_article', AsyncMock(return_value={'summary': SOURCE})):
            await tools.action(self.callback, self.state, None)
        await self.click('nb:editor:321')
        old_confirm = await self.db.owner_panel_confirmations.find_one({'action':'run_bulletin_editor'})
        await self.db.news_owner_bulletins.update_one({'bulletin_id':321}, {'$set': {'source_revision':'0' * 64}})
        self.callback.data = 'owner_tools:run:' + old_confirm['_id']
        with patch.object(panel, 'make_bulletin', AsyncMock()) as editor:
            await tools.action(self.callback, self.state, None)
            editor.assert_not_awaited()
        item = await owner_bulletin.get(self.db, self.user.id, 321)
        await self.db.news_owner_bulletins.update_one({'bulletin_id':321},
            {'$set': {'source_revision': owner_bulletin.revision(item['stories'], [])}})
        await self.click('nb:editor:321')
        new_confirm = await self.db.owner_panel_confirmations.find_one({'action':'run_bulletin_editor', 'stage':'ready'})
        self.callback.data = 'owner_tools:run:' + new_confirm['_id']
        with patch.object(panel, 'make_bulletin', AsyncMock(side_effect=TimeoutError())) as editor:
            await tools.action(self.callback, self.state, None)
            await tools.action(self.callback, self.state, None)
            editor.assert_awaited_once()
        self.assertEqual((await owner_bulletin.get(self.db, self.user.id, 321))['stage'], 'editor_failed')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_old_failure_can_retry_only_after_confirmation_without_refetch(self):
        confirm = await self.choose(2)
        self.callback.data = 'owner_tools:run:' + confirm['_id']
        with patch.object(article_fetch, 'fetch_article', AsyncMock(return_value={'summary': SOURCE})):
            await tools.action(self.callback, self.state, None)
        # A production failure predating diagnostics has neither field.
        await self.db.news_owner_bulletins.update_one({'bulletin_id': 321}, {'$set': {'stage': 'editor_failed'}})
        await self.click('nb:view:321')
        self.assertIn('не записана предыдущей версией', self.message.answer.await_args.args[0])
        self.assertTrue(any(b.callback_data == 'nb:retry:321' for row in
                            self.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row))
        with patch.object(panel, 'make_bulletin', AsyncMock(side_effect=lambda stories: response(stories))) as editor, \
             patch.object(article_fetch, 'fetch_article', AsyncMock()) as fetch:
            await self.click('nb:retry:321')
            editor.assert_not_awaited()
            confirm = await self.db.owner_panel_confirmations.find_one({'action': 'run_bulletin_editor', 'stage': 'ready'})
            self.assertTrue(confirm['data']['retry'])
            self.callback.data = 'owner_tools:run:' + confirm['_id']
            await asyncio.gather(tools.action(self.callback, self.state, None), tools.action(self.callback, self.state, None))
            editor.assert_awaited_once()
            fetch.assert_not_awaited()
        self.assertEqual((await owner_bulletin.get(self.db, self.user.id, 321))['stage'], 'ready_for_review')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_failed_retry_invalidates_old_confirmation_and_keeps_safe_reason(self):
        from news_prototype import editor_client
        confirm = await self.choose(2)
        self.callback.data = 'owner_tools:run:' + confirm['_id']
        with patch.object(article_fetch, 'fetch_article', AsyncMock(return_value={'summary': SOURCE})):
            await tools.action(self.callback, self.state, None)
        item = await owner_bulletin.get(self.db, self.user.id, 321)
        fail = AsyncMock(side_effect=editor_client.EditorialError('sensitive body', code='http', http_status=429))
        self.assertEqual(await owner_bulletin.prepare(self.db, self.user.id, 321, fail), 'editor_failed')
        failed = await owner_bulletin.get(self.db, self.user.id, 321)
        self.assertEqual(failed['editor_error'], {'code': 'http', 'http_status': 429})
        self.assertNotIn('sensitive', str(failed.get('editor_error')))
        self.assertIn('429', panel.editor_failure_message(failed))
        old = failed['editor_failure_token']
        fetch = AsyncMock(side_effect=RuntimeError('private content'))
        self.assertEqual(await owner_bulletin.prepare(self.db, self.user.id, 321, fetch,
            expected_source=item['source_revision'], retry=True, failure_token=old), 'editor_failed')
        failed2 = await owner_bulletin.get(self.db, self.user.id, 321)
        self.assertNotEqual(old, failed2['editor_failure_token'])
        self.assertEqual(failed2['editor_error'], {'code': 'unknown'})
        self.assertEqual(await owner_bulletin.prepare(self.db, self.user.id, 321, fetch,
            expected_source=item['source_revision'], retry=True, failure_token=old), 'not_new')
        fetch.assert_awaited_once()
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_retry_confirmation_rechecks_sources_before_new_request(self):
        confirm = await self.choose(2)
        self.callback.data = 'owner_tools:run:' + confirm['_id']
        with patch.object(article_fetch, 'fetch_article', AsyncMock(return_value={'summary': SOURCE})):
            await tools.action(self.callback, self.state, None)
        await self.db.news_owner_bulletins.update_one({'bulletin_id': 321}, {'$set': {'stage': 'editor_failed'}})
        await self.click('nb:retry:321')
        confirm = await self.db.owner_panel_confirmations.find_one({'action': 'run_bulletin_editor', 'stage': 'ready'})
        self.callback.data = 'owner_tools:run:' + confirm['_id']
        await self.db.news_owner_bulletins.update_one({'bulletin_id': 321}, {'$set': {'source_revision': 'x' * 64}})
        with patch.object(panel, 'make_bulletin', AsyncMock()) as editor:
            await tools.action(self.callback, self.state, None)
            editor.assert_not_awaited()
        self.assertEqual((await owner_bulletin.get(self.db, self.user.id, 321))['stage'], 'editor_failed')

    async def test_add_links_at_selection_then_confirm_without_premature_fetch(self):
        with patch.object(owner_daily, 'recent_items', AsyncMock(return_value=[])):
            await self.click('nb:daily')
        session = await self.db.owner_bulletin_selections.find_one({'mode': 'direct'})
        token = session['_id']
        markup = self.message.edit_text.await_args.kwargs['reply_markup']
        self.assertTrue(any(b.callback_data == f'nb:addlinks:{token}' for row in markup.inline_keyboard for b in row))
        await self.click(f'nb:addlinks:{token}')
        self.state.get_data.return_value = self.state.update_data.await_args.kwargs
        sent = SimpleNamespace(message_id=777, edit_text=AsyncMock())
        self.message.answer.return_value = sent
        self.message.text = 'https://example.com/a\nhttps://t.me/ExampleNews/123'
        with patch.object(article_fetch, 'fetch_article', AsyncMock()) as fetch:
            await tools.receive_input(self.message, self.state)
            fetch.assert_not_awaited()
        session = await self.db.owner_bulletin_selections.find_one({'_id': token})
        self.assertEqual(session['selected'], [1, 2])
        self.assertEqual([i['url'] for i in session['items']], ['https://example.com/a', 'https://t.me/ExampleNews/123'])
        self.assertEqual(session['message_id'], 777)
        self.assertTrue(any(b.callback_data == f'nb:prepare:{token}' for row in
                            sent.edit_text.await_args.kwargs['reply_markup'].inline_keyboard for b in row))
        self.callback.message.message_id = 777
        await self.click(f'nb:prepare:{token}')
        confirm = await self.db.owner_panel_confirmations.find_one({'action': 'collect_bulletin_sources'})
        self.assertEqual([c['url'] for c in confirm['data']['choices']], ['https://example.com/a', 'https://t.me/ExampleNews/123'])
        self.assertEqual(await self.db.news_owner_bulletins.count_documents({}), 0)

    async def test_selection_deduplicates_limits_and_rejects_expired_buttons(self):
        items = [{'title': 'Одна', 'url': 'https://example.com/a'}]
        with patch.object(owner_daily, 'recent_items', AsyncMock(return_value=items)):
            await self.click('nb:daily')
        session = await self.db.owner_bulletin_selections.find_one({'mode': 'direct'})
        token = session['_id']
        await self.click(f'nb:addlinks:{token}')
        data = self.state.update_data.await_args.kwargs
        self.state.get_data.return_value = data
        self.message.text = 'http://example.com/insecure'
        await tools.receive_input(self.message, self.state)
        self.assertEqual((await self.db.owner_bulletin_selections.find_one({'_id': token}))['selected'], [])
        sent = SimpleNamespace(message_id=777, edit_text=AsyncMock())
        self.message.answer.return_value = sent
        self.message.text = 'https://example.com/a\nhttps://example.com/b'
        await tools.receive_input(self.message, self.state)
        session = await self.db.owner_bulletin_selections.find_one({'_id': token})
        self.assertEqual([i['url'] for i in session['items']], ['https://example.com/a', 'https://example.com/b'])
        self.assertEqual(session['selected'], [1, 2])
        self.assertEqual(await panel.add_selection_links(self.message, self.user.id, data, 'https://example.com/c'), 'stale')
        data['selection_message_id'] = 777
        with self.assertRaises(ValueError):
            await panel.add_selection_links(self.message, self.user.id, data,
                '\n'.join(f'https://example.com/extra{i}' for i in range(19)))
        self.assertEqual((await self.db.owner_bulletin_selections.find_one({'_id': token}))['selected'], [1, 2])

    async def test_remove_missing_then_add_new_to_same_bulletin(self):
        confirm = await self.choose(2)
        self.callback.data = 'owner_tools:run:' + confirm['_id']
        async def fetch(url):
            if url.endswith('/0'): raise OSError('offline')
            return {'summary': SOURCE}
        with patch.object(article_fetch, 'fetch_article', AsyncMock(side_effect=fetch)):
            await tools.action(self.callback, self.state, None)
        card = await self.db.owner_bulletin_cards.find_one({'kind': 'remove_missing'})
        markup = [c.kwargs.get('reply_markup') for c in self.message.answer.await_args_list if c.kwargs.get('reply_markup')]
        self.assertTrue(any(b.callback_data == 'nb:remove_source:' + card['_id'] for m in markup for row in m.inline_keyboard for b in row))
        self.callback.message.message_id = card['message_id']
        await self.click('nb:remove_source:' + card['_id'])
        item = await owner_bulletin.get(self.db, self.user.id, 321)
        self.assertEqual(item['stage'], 'source_too_few')
        await self.click('nb:more:321')
        self.state.get_data.return_value = self.state.update_data.await_args.kwargs
        self.message.text = 'https://example.com/new'
        fake = AsyncMock(return_value={'summary': SOURCE})
        with patch.object(article_fetch, 'fetch_article', fake), patch.object(panel, 'make_bulletin', AsyncMock()) as editor:
            await tools.receive_input(self.message, self.state)
            fake.assert_awaited_once_with('https://example.com/new')
            editor.assert_not_awaited()
        item = await owner_bulletin.get(self.db, self.user.id, 321)
        self.assertEqual(item['stage'], 'new')
        self.assertEqual([s['url'] for s in item['stories']], ['https://example.com/news/1', 'https://example.com/new'])
        self.assertTrue(await owner_bulletin._sources_current(self.db, self.user.id, item))
        await self.click('nb:remove_source:' + card['_id'])
        self.assertEqual((await owner_bulletin.get(self.db, self.user.id, 321))['stage'], 'new')


    async def test_real_dispatcher_direct_selection_does_not_create_individual_drafts(self):
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
from news_prototype import owner_daily, owner_trial, article_fetch
from tests.test_bulletin_foundation import response
from tests.test_owner_drafts import SOURCE
from bender_queue import VideoQueue
from news_prototype import owner_bulletin_admission, review_delivery
from news_prototype.news_worker_client import NewsWorkerClient
from video_prototype import worker, news_jobs
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile, threading

async def run():
    database = AsyncMongoMockClient().test
    fake = SimpleNamespace(db=database, settings=database.settings, alchemy_players=database.alchemy_players)
    bot = Bot('123456:TESTTOKEN'); sent = []; sequence = 500
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
    items = [{'title':f'Новость {n}', 'url':f'https://example.com/{n}'} for n in range(5)]
    with patch.object(panel, 'db', fake), patch.object(tools, 'db', fake), \
         patch.object(news_owner_panel, 'db', fake), patch.object(alc_build, 'db', fake), \
         patch.object(alc_intro, 'get_intro_session', AsyncMock(return_value=None)), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)), \
         patch.object(owner_trial, 'runtime_enabled', AsyncMock(return_value=True)), \
         patch.object(owner_daily, 'recent_items', AsyncMock(return_value=items)), \
         patch.object(article_fetch, 'fetch_article', AsyncMock(return_value={'summary':SOURCE})) as fetch, \
         patch.object(panel, 'make_bulletin', AsyncMock(side_effect=lambda s: response(s))) as editor, \
         patch.object(bot.session, 'make_request', AsyncMock(side_effect=request)):
        user = {'id':ADMIN_ID, 'is_bot':False, 'first_name':'Owner'}
        chat = {'id':ADMIN_ID, 'type':'private'}
        async def cb(i, data, message_id=321):
            update = Update.model_validate({'update_id':i, 'callback_query':{
                'id':str(i), 'chat_instance':'owner', 'from':user, 'data':data,
                'message':{'message_id':message_id, 'date':1760000000, 'chat':chat,
                           'from':{'id':123456,'is_bot':True,'first_name':'Bot'}, 'text':'Panel'}}})
            await asyncio.wait_for(dp.feed_update(bot, update), 5)
        await cb(1, 'nb:daily')
        session = await database.owner_bulletin_selections.find_one({'mode':'direct'}); token = session['_id']
        for n in range(1,6): await cb(n + 1, f'nb:sel:{token}:{n}')
        await cb(7, f'nb:prepare:{token}')
        assert fetch.await_count == editor.await_count == 0
        first = await database.owner_panel_confirmations.find_one({'action':'collect_bulletin_sources'})
        await cb(8, 'owner_tools:run:' + first['_id'], 322)
        assert fetch.await_count == 5 and editor.await_count == 0
        await cb(9, 'nb:editor:322')
        second = await database.owner_panel_confirmations.find_one({'action':'run_bulletin_editor'})
        await cb(10, 'owner_tools:run:' + second['_id'], 323)
        await cb(11, 'owner_tools:run:' + second['_id'], 323)
        assert editor.await_count == 1
        assert await database.news_owner_bulletins.count_documents({'stage':'ready_for_review'}) == 1
        assert await database.news_owner_drafts.count_documents({}) == 0
        assert await database.news_owner_trial_jobs.count_documents({}) == 0
        assert any(isinstance(m, SendMessage) and 'сценарий' in m.text for m in sent)
        assert not any(isinstance(m, SendMessage) and 'Цитата источника' in m.text for m in sent)
        review = await database.owner_bulletin_actions.find_one({'action':'review'})
        await cb(111, f'nb:detail:{review["_id"]}:1', review['message_id'])
        assert any(isinstance(m, SendMessage) and 'Цитата источника' in m.text for m in sent)
        card = await database.owner_bulletin_actions.find_one({'action':'render'})
        await cb(12, 'nb:render:' + card['_id'], card['message_id'])
        confirmation = await database.owner_panel_confirmations.find_one({'action':'render_bulletin'})
        assert await database.news_owner_trial_jobs.count_documents({}) == 0
        await cb(13, 'owner_tools:run:' + confirmation['_id'], 999)
        await cb(14, 'owner_tools:run:' + confirmation['_id'], 999)
        assert await database.news_owner_trial_jobs.count_documents({}) == 1
        item = await database.news_owner_bulletins.find_one({})
        assert item['stage'] == 'queued'
        with tempfile.TemporaryDirectory() as tmp:
            def fake_render(job, folder):
                work=folder/'work'; work.mkdir()
                (work/'video.mp4').write_bytes(b'fake video')
                (work/'status.json').write_text('{"stage":"ready_for_review"}')
            shared=worker.JobStore(lambda *args:'file')
            news=news_jobs.NewsJobs(shared,Path(tmp)/'worker',fake_render,run_bulletin=fake_render)
            server=ThreadingHTTPServer(('127.0.0.1',0),worker.handler_for(shared,'secret',news))
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            try:
                client=NewsWorkerClient(f'http://127.0.0.1:{server.server_port}','secret')
                queue=VideoQueue(database.media,owner='test'); await queue.ensure(); await queue.acquire()
                for _ in range(100):
                    await owner_trial.dispatch_once(queue,database,client)
                    job=await database.news_owner_trial_jobs.find_one({})
                    if job['stage']=='ready_for_review': break
                    await asyncio.sleep(.01)
                assert job['stage']=='ready_for_review', job
                delivery=SimpleNamespace(send_video=AsyncMock(return_value=SimpleNamespace(message_id=77)))
                assert await review_delivery.run_once(database.news_owner_trial_jobs,
                    database.news_owner_trial_candidates,client,delivery,ADMIN_ID)=='review_sent'
                assert delivery.send_video.await_args.args[0]==ADMIN_ID
                assert (await owner_bulletin_admission.status(database,ADMIN_ID,item['bulletin_id']))[0]=='review_sent'
                assert len(news.states)==0
            finally:
                server.shutdown(); server.server_close(); thread.join()
    await bot.session.close()
asyncio.run(run())
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


def test_real_dispatcher_manual_sources_end_to_end():
    import subprocess, sys
    script = r'''
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiogram import Bot
from aiogram.types import Update, Message
from aiogram.methods import SendMessage
from mongomock_motor import AsyncMongoMockClient
from app_setup import configure_dispatcher
from config import ADMIN_ID
from handlers import owner_bulletin_panel as panel, owner_tools_panel as tools, alc_build, alc_intro, logger
from news_prototype import owner_daily, owner_trial, article_fetch
from tests.test_owner_drafts import SOURCE

async def run():
    database = AsyncMongoMockClient().test
    fake = SimpleNamespace(db=database, settings=database.settings, alchemy_players=database.alchemy_players, messages=database.messages)
    bot = Bot('123456:TESTTOKEN'); sequence = 500
    async def request(bot_arg, method, **kwargs):
        nonlocal sequence
        if isinstance(method, SendMessage):
            sequence += 1
            return Message.model_validate({'message_id':sequence, 'date':1760000000,
                 'chat':{'id':ADMIN_ID,'type':'private'},
                 'from':{'id':123456,'is_bot':True,'first_name':'Bot'},'text':method.text}, context={'bot':bot_arg})
        return True
    dp = configure_dispatcher()
    user = {'id':ADMIN_ID,'is_bot':False,'first_name':'Owner'}
    chat = {'id':ADMIN_ID,'type':'private'}
    async def cb(i, data, mid=321):
        update = Update.model_validate({'update_id':i,'callback_query':{'id':str(i),
            'chat_instance':'owner','from':user,'data':data,'message':{'message_id':mid,
            'date':1760000000,'chat':chat,'from':{'id':123456,'is_bot':True,'first_name':'Bot'},'text':'Panel'}}})
        await asyncio.wait_for(dp.feed_update(bot, update), 5)
    async def msg(i, text):
        update = Update.model_validate({'update_id':i,'message':{'message_id':400+i,'date':1760000000,
                                  'chat':chat,'from':user,'text':text}})
        await asyncio.wait_for(dp.feed_update(bot, update), 5)
    async def fetch(url):
        if url.endswith('/missing'): raise OSError()
        return {'summary':SOURCE}
    with patch.object(panel, 'db', fake), patch.object(tools, 'db', fake), patch.object(alc_build, 'db', fake), patch.object(logger, 'db', fake), \
         patch.object(alc_intro, 'get_intro_session', AsyncMock(return_value=None)), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)), \
         patch.object(owner_daily, 'recent_items', AsyncMock(return_value=[])), \
         patch.object(article_fetch, 'fetch_article', AsyncMock(side_effect=fetch)), \
         patch.object(panel, 'make_bulletin', AsyncMock()) as editor, \
         patch.object(bot.session, 'make_request', AsyncMock(side_effect=request)):
        await cb(1,'nb:daily')
        session = await database.owner_bulletin_selections.find_one({}); token = session['_id']
        await cb(2, 'nb:addlinks:'+token)
        await msg(3, 'https://example.com/missing\nhttps://example.com/working')
        session = await database.owner_bulletin_selections.find_one({'_id':token})
        assert session['selected'] == [1,2] and session['message_id'] != 321
        await cb(4, 'nb:prepare:'+token, session['message_id'])
        confirmation = await database.owner_panel_confirmations.find_one({'action':'collect_bulletin_sources'})
        await cb(5, 'owner_tools:run:'+confirmation['_id'], 322)
        item = await database.news_owner_bulletins.find_one({}); assert item['stage'] == 'source_missing'
        card = await database.owner_bulletin_cards.find_one({'kind':'remove_missing'})
        await cb(6, 'nb:remove_source:'+card['_id'], card['message_id'])
        item = await database.news_owner_bulletins.find_one({}); assert item['stage'] == 'source_too_few'
        await cb(7, 'nb:more:322')
        await msg(8, 'https://example.com/third')
        item = await database.news_owner_bulletins.find_one({}); assert item['stage'] == 'new'
        assert [s['url'] for s in item['stories']] == ['https://example.com/working', 'https://example.com/third']
        editor.assert_not_awaited()
        assert await database.news_owner_trial_jobs.count_documents({}) == 0
    await bot.session.close()
asyncio.run(run())
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


class BulletinChatCleanupTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = DirectBulletinPanelTests.asyncSetUp
    click = DirectBulletinPanelTests.click
    choose = DirectBulletinPanelTests.choose

    async def test_selection_confirmation_and_results_remove_only_flow_messages(self):
        sent_ids = iter([400, 401, 402, 403])
        self.message.answer.side_effect = lambda *args, **kwargs: SimpleNamespace(message_id=next(sent_ids))
        confirm = await self.choose(2)
        # A short selection and its confirmation occupy one message, not two.
        confirmation_call = self.message.answer.await_args_list[-1]
        self.assertIn('Выбранные новости в порядке выпуска', confirmation_call.args[0])
        self.assertIn('Прочитать выбранные источники', confirmation_call.args[0])
        self.assertEqual(confirm['cleanup_messages'], [321])
        self.message.message_id = 400
        self.callback.data = 'owner_tools:run:' + confirm['_id']
        bot = SimpleNamespace(delete_message=AsyncMock())
        with patch.object(article_fetch, 'fetch_article', AsyncMock(return_value={'summary': SOURCE})), \
             patch.object(news_image_fetch, 'fetch_thumbnail', AsyncMock()):
            await tools.action(self.callback, self.state, bot)
        bot.delete_message.assert_awaited_once_with(chat_id=self.user.id, message_id=321)
        self.message.delete.assert_awaited_once()
        item = await owner_bulletin.get(self.db, self.user.id, 400)
        self.assertEqual(item['stage'], 'new')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_cancel_keeps_original_selection_usable(self):
        confirm = await self.choose(2)
        selection = await self.db.owner_bulletin_selections.find_one({'_id': confirm['selection_id']})
        self.assertEqual(selection['stage'], 'confirmed')
        self.message.message_id = 777
        self.callback.data = 'owner_tools:cancel:' + confirm['_id']
        bot = SimpleNamespace(delete_message=AsyncMock())
        await tools.action(self.callback, self.state, bot)
        self.assertEqual((await self.db.owner_bulletin_selections.find_one({'_id': selection['_id']}))['stage'], 'open')
        bot.delete_message.assert_not_awaited()
        self.message.message_id = 321
        await self.click(f'nb:sel:{selection["_id"]}:1')
        self.assertEqual((await self.db.owner_bulletin_selections.find_one({'_id': selection['_id']}))['selected'], [2])

    async def test_cancel_editor_confirmation_does_not_remove_source_screen(self):
        confirm = await self.choose(2)
        self.callback.data = 'owner_tools:run:' + confirm['_id']
        with patch.object(article_fetch, 'fetch_article', AsyncMock(return_value={'summary': SOURCE})):
            await tools.action(self.callback, self.state, None)
        self.message.message_id = 500
        await self.click('nb:editor:321')
        confirm = await self.db.owner_panel_confirmations.find_one({'action': 'run_bulletin_editor', 'stage': 'ready'})
        self.assertEqual(confirm['cleanup_messages'], [500])
        self.message.message_id = 600
        self.callback.data = 'owner_tools:cancel:' + confirm['_id']
        bot = SimpleNamespace(delete_message=AsyncMock())
        await tools.action(self.callback, self.state, bot)
        bot.delete_message.assert_not_awaited()
        self.assertEqual((await owner_bulletin.get(self.db, self.user.id, 321))['stage'], 'new')

    async def test_failed_deletion_does_not_repeat_editor_or_remove_result(self):
        confirm = await self.choose(2)
        self.callback.data = 'owner_tools:run:' + confirm['_id']
        with patch.object(article_fetch, 'fetch_article', AsyncMock(return_value={'summary': SOURCE})):
            await tools.action(self.callback, self.state, None)
        self.message.message_id = 500
        await self.click('nb:editor:321')
        confirm = await self.db.owner_panel_confirmations.find_one({'action': 'run_bulletin_editor', 'stage': 'ready'})
        self.message.message_id = 600
        self.message.delete.side_effect = RuntimeError('not allowed')
        self.callback.data = 'owner_tools:run:' + confirm['_id']
        bot = SimpleNamespace(delete_message=AsyncMock(side_effect=RuntimeError('not allowed')))
        with patch.object(panel, 'make_bulletin', AsyncMock(side_effect=lambda stories: response(stories))) as editor:
            await tools.action(self.callback, self.state, bot)
            await tools.action(self.callback, self.state, bot)
        editor.assert_awaited_once()
        self.assertEqual((await owner_bulletin.get(self.db, self.user.id, 321))['stage'], 'ready_for_review')
        self.assertEqual(self.message.edit_text.await_args.args[0], 'Действие завершено. Результат ниже.')
        self.assertTrue(any('сценарий' in call.args[0] for call in self.message.answer.await_args_list))

    async def test_long_selected_list_cancel_cleans_only_overflow_and_retains_selection(self):
        from datetime import datetime, timedelta, timezone
        entries = [{'number': n, 'label': f'{n} ' + 'длинный заголовок ' * 16,
                    'url': f'https://example.com/{n}'} for n in range(1, 21)]
        token = 'abcdef0123456789'
        await self.db.owner_bulletin_selections.insert_one({'_id': token, 'owner_id': self.user.id,
            'message_id': 321, 'mode': 'direct', 'stage': 'open', 'selected': list(range(1, 21)),
            'items': entries, 'page': 0, 'expires_at': datetime.now(timezone.utc) + timedelta(hours=1)})
        sent = iter(range(500, 520))
        self.message.answer.side_effect = lambda *args, **kwargs: SimpleNamespace(message_id=next(sent))
        await self.click(f'nb:prepare:{token}')
        confirm = await self.db.owner_panel_confirmations.find_one({'action': 'collect_bulletin_sources'})
        self.assertGreater(len(confirm['cleanup_messages']), 1)
        all_text = '\n'.join(call.args[0] for call in self.message.answer.await_args_list)
        for entry in entries:
            self.assertIn(entry['label'], all_text)
        for call in self.message.answer.await_args_list:
            self.assertLessEqual(len(call.args[0].encode('utf-16-le')) // 2, 3600)
        self.message.message_id = 999
        self.callback.data = 'owner_tools:cancel:' + confirm['_id']
        bot = SimpleNamespace(delete_message=AsyncMock())
        await tools.action(self.callback, self.state, bot)
        removed = {call.kwargs['message_id'] for call in bot.delete_message.await_args_list}
        self.assertEqual(removed, set(confirm['cleanup_messages']) - {321})
        self.assertEqual((await self.db.owner_bulletin_selections.find_one({'_id': token}))['stage'], 'open')

    async def test_large_video_warning_shares_source_status_message(self):
        item = {'owner_id': self.user.id, 'bulletin_id': 123, 'stage': 'new',
                'stories': [{'draft_id': 1}, {'draft_id': 2, 'telegram_video_status': 'too_big'}]}
        await panel.show_sources(self.message, item)
        self.assertEqual(self.message.answer.await_count, 1)
        text = self.message.answer.await_args.args[0]
        self.assertIn('Все источники собраны', text)
        self.assertIn('видео слишком большое', text)
        self.assertIn('обрезанный фрагмент', text)

    async def test_editor_failure_keeps_diagnostic_result_and_cleans_progress(self):
        from news_prototype import editor_client
        confirm = await self.choose(2)
        self.callback.data = 'owner_tools:run:' + confirm['_id']
        with patch.object(article_fetch, 'fetch_article', AsyncMock(return_value={'summary': SOURCE})):
            await tools.action(self.callback, self.state, None)
        self.message.message_id = 500
        await self.click('nb:editor:321')
        confirm = await self.db.owner_panel_confirmations.find_one({'action': 'run_bulletin_editor', 'stage': 'ready'})
        self.message.message_id = 600
        self.message.delete.reset_mock()
        self.callback.data = 'owner_tools:run:' + confirm['_id']
        bot = SimpleNamespace(delete_message=AsyncMock())
        with patch.object(panel, 'make_bulletin', AsyncMock(side_effect=editor_client.EditorialError(
                'private', code='http', http_status=429))) as editor:
            await tools.action(self.callback, self.state, bot)
        editor.assert_awaited_once()
        self.assertEqual((await owner_bulletin.get(self.db, self.user.id, 321))['stage'], 'editor_failed')
        self.assertIn('429', self.message.answer.await_args.args[0])
        bot.delete_message.assert_awaited_once_with(chat_id=self.user.id, message_id=500)
        self.message.delete.assert_awaited_once()
