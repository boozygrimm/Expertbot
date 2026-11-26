import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

import pytest
from mongomock_motor import AsyncMongoMockClient
from handlers import general_news_panel as panel, owner_bulletin_panel as bulletin, owner_tools_panel as tools
from news_prototype import general_bulletin_selection as basket, owner_trial, article_fetch, news_image_fetch
from tests.test_owner_drafts import SOURCE
from tests.test_bulletin_foundation import response


@pytest.fixture
def ctx():
    db = AsyncMongoMockClient().test
    fake = SimpleNamespace(db=db)
    user = SimpleNamespace(id=tools.ADMIN_ID)
    msg = SimpleNamespace(chat=SimpleNamespace(id=user.id, type='private'), message_id=123,
        from_user=user, answer=AsyncMock(return_value=SimpleNamespace(message_id=777)),
        edit_text=AsyncMock(), delete=AsyncMock(), answer_photo=AsyncMock(), reply=AsyncMock())
    cb = SimpleNamespace(from_user=user, message=msg, data='', answer=AsyncMock())
    state = SimpleNamespace(clear=AsyncMock(), get_data=AsyncMock(), set_state=AsyncMock(), update_data=AsyncMock())
    with patch.object(panel, 'db', fake), patch.object(bulletin, 'db', fake), patch.object(tools, 'db', fake), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)):
        yield SimpleNamespace(db=db, user=user, msg=msg, cb=cb, state=state)


def buttons(msg):
    return [b for row in msg.edit_text.await_args.kwargs['reply_markup'].inline_keyboard for b in row]


def button(msg, prefix):
    return next(b.callback_data for b in buttons(msg) if (b.callback_data or '').startswith(prefix))


async def click(ctx, data):
    ctx.cb.data = data
    await panel.callback_action(ctx.cb, ctx.state)


async def add_item(ctx, topic, title, ident):
    await ctx.db.news_general_candidates.insert_one({'_id': ident, 'url': f'https://example.com/{ident}',
        'title': title, 'source': 'Example', 'stage': 'collected', 'category_override': topic})
    await click(ctx, f'gnews:cat:{topic}:0')
    await click(ctx, button(ctx.msg, 'gnews:item:'))
    await click(ctx, button(ctx.msg, 'gnews:badd:'))


@pytest.mark.asyncio
async def test_categories_to_bulletin_full_flow_no_implicit_calls(ctx):
    fetch = AsyncMock(return_value={'summary': SOURCE})
    editor = AsyncMock(side_effect=response)
    with patch.object(article_fetch, 'fetch_article', fetch), patch.object(news_image_fetch, 'fetch_thumbnail', AsyncMock()), \
         patch.object(bulletin, 'make_bulletin', editor):
        await click(ctx, 'gnews:basket')
        assert 'минимум две' in ctx.msg.edit_text.await_args.args[0]
        await add_item(ctx, 'games', 'Игры', 'a')
        await add_item(ctx, 'science', 'Наука', 'b')
        await click(ctx, button(ctx.msg, 'gnews:badd:'))  # repeated click is idempotent
        assert await basket.ids(ctx.db, ctx.user.id) == ['a', 'b']
        await click(ctx, 'gnews:basket')
        assert '1. Игры' in ctx.msg.edit_text.await_args.args[0]
        assert '2. Наука' in ctx.msg.edit_text.await_args.args[0]
        await click(ctx, 'gnews:bbuild')
        session = await ctx.db.owner_bulletin_selections.find_one({'origin': 'general'})
        assert session['selected'] == [1, 2]
        assert [i['label'] for i in session['items']] == ['Игры', 'Наука']
        assert 'отдельная копия' in ctx.msg.edit_text.await_args.args[0]
        fetch.assert_not_awaited(); editor.assert_not_awaited()
        assert await ctx.db.news_owner_bulletins.count_documents({}) == 0
        ctx.cb.data = f'nb:prepare:{session["_id"]}'
        await bulletin.callback_action(ctx.cb, ctx.state)
        confirmation = await ctx.db.owner_panel_confirmations.find_one({'action': 'collect_bulletin_sources'})
        assert [c['url'] for c in confirmation['data']['choices']] == ['https://example.com/a', 'https://example.com/b']
        fetch.assert_not_awaited(); editor.assert_not_awaited()
        ctx.cb.data = 'owner_tools:run:' + confirmation['_id']
        await asyncio.gather(tools.action(ctx.cb, ctx.state, None), tools.action(ctx.cb, ctx.state, None))
        assert fetch.await_count == 2
        editor.assert_not_awaited()
        assert (await ctx.db.news_owner_bulletins.find_one({}))['stage'] == 'new'
        ctx.cb.data = 'nb:editor:123'
        await bulletin.callback_action(ctx.cb, ctx.state)
        confirm = await ctx.db.owner_panel_confirmations.find_one({'action': 'run_bulletin_editor'})
        ctx.cb.data = 'owner_tools:run:' + confirm['_id']
        await asyncio.gather(tools.action(ctx.cb, ctx.state, None), tools.action(ctx.cb, ctx.state, None))
        editor.assert_awaited_once()
        assert (await ctx.db.news_owner_bulletins.find_one({}))['stage'] == 'ready_for_review'
        assert await ctx.db.news_owner_trial_jobs.count_documents({}) == 0
    assert await ctx.db.news_general_candidates.count_documents({}) == 2
    assert await basket.ids(ctx.db, ctx.user.id) == ['a', 'b']


@pytest.mark.asyncio
async def test_remove_stale_hidden_owner_and_persistence(ctx):
    await add_item(ctx, 'games', 'Игры', 'a')
    await add_item(ctx, 'science', 'Наука', 'b')
    await click(ctx, 'gnews:basket')
    old_button = button(ctx.msg, 'gnews:bremove:')
    await ctx.db.news_general_candidates.update_one({'_id': 'b'}, {'$set': {'hidden': True}})
    await click(ctx, 'gnews:basket')
    assert 'недоступна' in ctx.msg.edit_text.await_args.args[0]
    await click(ctx, 'gnews:bbuild')
    assert 'недоступен' in ctx.cb.answer.await_args.args[0]
    assert await ctx.db.owner_bulletin_selections.count_documents({}) == 0
    ctx.cb.from_user = SimpleNamespace(id=ctx.user.id + 1)
    await click(ctx, old_button)
    assert await basket.ids(ctx.db, ctx.user.id) == ['a', 'b']
    ctx.cb.from_user = ctx.user
    # Even an old screen is bound to its original ID, not the current index.
    await click(ctx, old_button)
    assert await basket.ids(ctx.db, ctx.user.id) == ['b']
    assert await ctx.db.news_general_candidates.count_documents({}) == 2
    await click(ctx, old_button)
    assert await basket.ids(ctx.db, ctx.user.id) == ['b']


@pytest.mark.asyncio
async def test_limit_concurrent_and_bad_links(ctx):
    results = await asyncio.gather(*(basket.change(ctx.db, ctx.user.id, str(i), add=True) for i in range(25)))
    assert results.count('saved') == 20
    assert len(await basket.ids(ctx.db, ctx.user.id)) == 20
    assert await basket.change(ctx.db, ctx.user.id, '0', add=True) == 'duplicate'
    with pytest.raises(ValueError):
        basket.choice({'url': 'http://example.com/x'})
    with pytest.raises(ValueError):
        basket.choice({'url': 'https://t.me/privatechannel'})


@pytest.mark.asyncio
async def test_expired_card_and_disabled_mode(ctx):
    from datetime import datetime, timedelta, timezone
    await ctx.db.news_general_candidates.insert_one({'_id':'x', 'url':'https://example.com/x', 'title':'X', 'category_override':'games'})
    await click(ctx, 'gnews:cat:games:0')
    await click(ctx, button(ctx.msg, 'gnews:item:'))
    add = button(ctx.msg, 'gnews:badd:')
    await ctx.db.news_general_panel_sessions.update_many({}, {'$set': {'expires_at': datetime.now(timezone.utc) - timedelta(seconds=1)}})
    await click(ctx, add)
    assert await basket.ids(ctx.db, ctx.user.id) == []
    with patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=False)):
        await click(ctx, 'gnews:bbuild')
    assert 'Включи' in ctx.cb.answer.await_args.args[0]
    assert await ctx.db.owner_bulletin_selections.count_documents({}) == 0


@pytest.mark.asyncio
async def test_missing_source_is_recoverable_and_confirm_cancel_is_safe(ctx):
    await add_item(ctx, 'games', 'Игры', 'a')
    await add_item(ctx, 'science', 'Наука', 'b')
    await click(ctx, 'gnews:bbuild')
    session = await ctx.db.owner_bulletin_selections.find_one({'origin': 'general'})
    ctx.cb.data = f'nb:prepare:{session["_id"]}'
    await bulletin.callback_action(ctx.cb, ctx.state)
    confirmation = await ctx.db.owner_panel_confirmations.find_one({'action': 'collect_bulletin_sources'})
    ctx.cb.data = 'owner_tools:cancel:' + confirmation['_id']
    await tools.action(ctx.cb, ctx.state, None)
    ctx.cb.data = 'owner_tools:run:' + confirmation['_id']
    with patch.object(article_fetch, 'fetch_article', AsyncMock()) as fetch:
        await tools.action(ctx.cb, ctx.state, None)
        fetch.assert_not_awaited()
    assert await ctx.db.news_owner_bulletins.count_documents({}) == 0
    assert await basket.ids(ctx.db, ctx.user.id) == ['a', 'b']
    await click(ctx, 'gnews:bbuild')
    sessions = await ctx.db.owner_bulletin_selections.find({'origin': 'general', 'stage': 'open'}).to_list(length=10)
    ctx.cb.data = f'nb:prepare:{sessions[-1]["_id"]}'
    await bulletin.callback_action(ctx.cb, ctx.state)
    confirmation = await ctx.db.owner_panel_confirmations.find_one({'action': 'collect_bulletin_sources', 'stage': 'ready'})
    ctx.cb.data = 'owner_tools:run:' + confirmation['_id']
    async def fetch(url):
        if url.endswith('/a'):
            raise OSError('offline')
        return {'summary': SOURCE}
    with patch.object(article_fetch, 'fetch_article', AsyncMock(side_effect=fetch)), \
         patch.object(bulletin, 'make_bulletin', AsyncMock()) as editor:
        await tools.action(ctx.cb, ctx.state, None)
        editor.assert_not_awaited()
    item = await ctx.db.news_owner_bulletins.find_one({})
    assert item['stage'] == 'source_missing' and item['missing_ids'] == [1]
    assert await ctx.db.news_general_candidates.count_documents({}) == 2


def test_real_dispatcher_routes_category_to_bulletin():
    import subprocess
    import sys
    script = r'''
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiogram import Bot
from aiogram.methods import EditMessageText
from aiogram.types import Update
from mongomock_motor import AsyncMongoMockClient
from config import ADMIN_ID
from app_setup import configure_dispatcher
from handlers import general_news_panel, owner_bulletin_panel, owner_tools_panel
from news_prototype import owner_trial

async def run():
    database = AsyncMongoMockClient().test
    fake = SimpleNamespace(db=database)
    for n, topic in enumerate(('games','science')):
        await database.news_general_candidates.insert_one({'_id':str(n), 'url':f'https://example.com/{n}',
            'title':f'News {n}', 'category_override':topic})
    bot = Bot('123456:TESTTOKEN')
    sent = []
    async def request(bot_arg, method, **kwargs):
        sent.append(method)
        return True
    dp = configure_dispatcher()
    with patch.object(general_news_panel, 'db', fake), patch.object(owner_bulletin_panel, 'db', fake), \
         patch.object(owner_tools_panel, 'db', fake), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)), \
         patch.object(bot.session, 'make_request', AsyncMock(side_effect=request)):
        user = {'id':ADMIN_ID, 'is_bot':False, 'first_name':'Owner'}
        msg = {'message_id':42,'date':1760000000,'chat':{'id':ADMIN_ID,'type':'private'},
               'from':{'id':123456,'is_bot':True,'first_name':'Bot'},'text':'Panel'}
        i = 0
        async def click(data):
            nonlocal i
            i += 1
            cb = {'id':str(i),'chat_instance':'owner','from':user,'data':data,'message':msg}
            await dp.feed_update(bot, Update.model_validate({'update_id':i,'callback_query':cb}))
        def latest(prefix):
            edit = next(m for m in reversed(sent) if isinstance(m, EditMessageText))
            return next(b.callback_data for row in edit.reply_markup.inline_keyboard for b in row
                        if (b.callback_data or '').startswith(prefix))
        for topic in ('games','science'):
            await click('gnews:cat:' + topic + ':0')
            await click(latest('gnews:item:'))
            await click(latest('gnews:badd:'))
        await click('gnews:basket')
        await click('gnews:bbuild')
        await click(latest('nb:prepare:'))
        session = await database.owner_bulletin_selections.find_one({'origin':'general'})
        confirm = await database.owner_panel_confirmations.find_one({'action':'collect_bulletin_sources'})
        assert session['selected'] == [1, 2]
        assert [c['url'] for c in confirm['data']['choices']] == ['https://example.com/0', 'https://example.com/1']
        assert await database.news_owner_bulletins.count_documents({}) == 0
    await bot.session.close()
asyncio.run(run())
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
