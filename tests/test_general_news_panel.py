import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from mongomock_motor import AsyncMongoMockClient

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
from handlers import general_news_panel as panel, owner_tools_panel as tools, news_owner_drafts, owner_news_actions
from news_prototype import general_queue, general_sources, owner_drafts, owner_trial


@pytest.fixture
def context():
    db = AsyncMongoMockClient().test
    fake = SimpleNamespace(db=db)
    patches = [patch.object(m, 'db', fake) for m in (panel, tools, news_owner_drafts, owner_news_actions)]
    patches += [patch.object(panel, 'ADMIN_ID', 123), patch.object(tools, 'ADMIN_ID', 123),
                patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True))]
    for p in patches:
        p.start()
    message = SimpleNamespace(chat=SimpleNamespace(id=123, type='private'), message_id=71,
        answer=AsyncMock(), edit_text=AsyncMock(), reply=AsyncMock(), answer_photo=AsyncMock(), delete=AsyncMock())
    callback = SimpleNamespace(from_user=SimpleNamespace(id=123), message=message, answer=AsyncMock(), data='')
    state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock(), update_data=AsyncMock(), get_data=AsyncMock())
    yield SimpleNamespace(db=db, message=message, callback=callback, state=state)
    for p in reversed(patches):
        p.stop()


def button(message, text):
    markup = message.edit_text.await_args.kwargs['reply_markup']
    return next(b.callback_data for row in markup.inline_keyboard for b in row if text in b.text)


async def click(ctx, data):
    ctx.callback.data = data
    await panel.callback_action(ctx.callback, ctx.state)


@pytest.mark.asyncio
async def test_navigation_and_source_disable_restore(context):
    c = context
    await click(c, 'gnews:home')
    assert 'Автоотбор выключен' in c.message.edit_text.await_args.args[0]
    await click(c, 'gnews:sources:0')
    source = button(c.message, 'N + 1')
    assert len(source.encode()) <= 64
    await click(c, source)
    await click(c, button(c.message, 'Убрать источник'))
    assert 'nplusone' not in {x['id'] for x in await general_sources.list_sources(c.db)}
    await click(c, button(c.message, 'Вернуть источник'))
    assert 'nplusone' in {x['id'] for x in await general_sources.list_sources(c.db)}
    assert await c.db.news_owner_drafts.count_documents({}) == 0


@pytest.mark.asyncio
async def test_add_input_is_owner_only_and_does_not_prepare(context):
    c = context
    await click(c, 'gnews:addtg')
    assert c.state.update_data.await_args.kwargs['action'] == 'gnews_addtg'
    incoming = SimpleNamespace(from_user=SimpleNamespace(id=123), chat=c.message.chat,
        text='Мой канал\n@example_news', answer=AsyncMock(), message_id=100)
    c.state.get_data.return_value = {'action': 'gnews_addtg'}
    await tools.receive_input(incoming, c.state)
    assert any(s['location'] == 'example_news' for s in await general_sources.list_sources(c.db))
    c.state.get_data.return_value = {'action': 'gnews_addnews'}
    incoming.text = 'https://example.com/story'
    await tools.receive_input(incoming, c.state)
    assert await c.db.news_general_candidates.count_documents({}) == 1
    assert await c.db.news_owner_drafts.count_documents({}) == 0
    c.callback.from_user = SimpleNamespace(id=999)
    await click(c, 'gnews:poll')
    assert 'Только для владельца' in c.callback.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_hide_and_restore_news_and_stale_card(context):
    c = context
    key = await general_queue.add_news(c.db, 'https://example.com/story')
    await click(c, 'gnews:news:0')
    item = button(c.message, 'Новость по ссылке')
    await click(c, item)
    await click(c, button(c.message, 'Убрать новость'))
    assert (await c.db.news_general_candidates.find_one({'_id': key}))['hidden'] is True
    assert await general_queue.reserve(c.db, key, 123) is None
    await click(c, button(c.message, 'Вернуть новость'))
    assert (await c.db.news_general_candidates.find_one({'_id': key}))['hidden'] is False
    # Old page token cannot be used from a different message.
    c.message.message_id = 72
    await click(c, item)
    assert 'устарела' in c.callback.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_preparation_requires_one_use_confirmation_and_respects_removal(context):
    c = context
    key = await general_queue.add_news(c.db, 'https://example.com/story')
    await click(c, 'gnews:news:0')
    await click(c, button(c.message, 'Новость по ссылке'))
    prepare = button(c.message, 'Подготовить сценарий')
    await click(c, prepare)
    confirmation = await c.db.owner_panel_confirmations.find_one({'action': 'general_prepare'})
    assert confirmation['data']['key'] == key
    assert await c.db.news_owner_drafts.count_documents({}) == 0
    # Removal after opening confirmation prevents the confirmed operation from claiming.
    await general_queue.remove_news(c.db, key)
    c.callback.data = 'owner_tools:run:' + confirmation['_id']
    with patch.object(general_queue, 'prepare_reserved', AsyncMock(wraps=general_queue.prepare_reserved)) as work:
        await tools.action(c.callback, c.state, bot=None)
        await tools.action(c.callback, c.state, bot=None)
        work.assert_awaited_once()
    assert await c.db.news_owner_drafts.count_documents({}) == 0
    assert (await c.db.owner_panel_confirmations.find_one({'_id': confirmation['_id']}))['stage'] == 'done'


@pytest.mark.asyncio
async def test_prepare_runs_reviewer_and_opens_result_only_after_confirmation(context):
    c = context
    key = await general_queue.add_news(c.db, 'https://example.com/story')
    await click(c, 'gnews:news:0')
    await click(c, button(c.message, 'Новость по ссылке'))
    await click(c, button(c.message, 'Подготовить сценарий'))
    confirmation = await c.db.owner_panel_confirmations.find_one({'action': 'general_prepare'})
    c.callback.data = 'owner_tools:run:' + confirmation['_id']
    fetch = AsyncMock(return_value={'title': 'История', 'summary': 'Проверяемый текст источника. ' * 5})
    draft = AsyncMock(return_value={'decision': 'draft', 'verification': 'needs_review',
        'script': {'ticker': 'Новость', 'turns': [{'speaker': 'Бендер', 'text': 'Новость.'},
                                                  {'speaker': 'Киберсенсей', 'text': 'Принято.'}]}, 'evidence': []})
    from news_prototype import article_fetch, story_review
    check_review = story_review.check
    async def review(db, owner_id, draft_id):
        return await check_review(db, owner_id, draft_id,
            request=AsyncMock(return_value='{"status":"issues","issues":[{"location":"Строка","reason":"Уточнить","quote":""}]}'))
    with patch.object(article_fetch, 'fetch_article', fetch), patch.object(news_owner_drafts, 'make_draft', draft), \
         patch.object(story_review, 'check', review):
        await tools.action(c.callback, c.state, bot=None)
    result = await owner_drafts.get(c.db, 123, general_queue.draft_id_for(key))
    assert result['stage'] == 'draft_ready'
    assert result['independent_review']['status'] == 'issues'
    assert 'Уточнить' in c.message.answer.await_args.args[0]
    assert await c.db.news_owner_trial_jobs.count_documents({}) == 0


@pytest.mark.asyncio
async def test_poll_only_after_explicit_button(context):
    c = context
    poll = AsyncMock(return_value=(3, ['space']))
    with patch.object(general_queue, 'poll', poll):
        await click(c, 'gnews:home')
        poll.assert_not_awaited()
        await click(c, 'gnews:poll')
        poll.assert_awaited_once()
    assert 'Добавлено новостей: 3' in c.message.answer.await_args.args[0]
    assert await c.db.news_owner_drafts.count_documents({}) == 0


@pytest.mark.asyncio
async def test_automatic_mode_requires_one_use_confirmation_and_can_be_disabled(context):
    from news_prototype import general_auto
    c = context
    with patch.object(general_auto, 'keys_ready', return_value=True):
        await click(c, 'gnews:auto')
        assert '10' in c.message.edit_text.await_args.args[0]
        await click(c, 'gnews:autoon')
        assert not await general_auto.enabled(c.db)
        confirmation = await c.db.owner_panel_confirmations.find_one({'action': 'general_auto_on'})
        assert confirmation and confirmation['stage'] == 'ready'
        c.callback.data = 'owner_tools:run:' + confirmation['_id']
        await tools.action(c.callback, c.state, bot=None)
        assert await general_auto.enabled(c.db)
        await tools.action(c.callback, c.state, bot=None)
        assert await general_auto.enabled(c.db)
        await click(c, 'gnews:autooff')
        assert not await general_auto.enabled(c.db)


@pytest.mark.asyncio
async def test_ready_list_contains_only_visible_ready_items(context):
    c = context
    key = await general_queue.add_news(c.db, 'https://example.com/ready')
    await general_queue.add_news(c.db, 'https://example.com/pending')
    ident = general_queue.draft_id_for(key)
    await c.db.news_general_candidates.update_one({'_id': key}, {'$set': {'stage': 'draft_ready', 'owner_id': 123, 'draft_id': ident}})
    await c.db.news_owner_drafts.insert_one({'_id': 'ready', 'owner_id': 123, 'stage': 'draft_ready',
                                              'draft_id': ident, 'general_candidate_id': key})
    await click(c, 'gnews:ready:0')
    assert 'Готовые сценарии · 1' in c.message.edit_text.await_args.args[0]
    await general_queue.remove_news(c.db, key)
    await click(c, 'gnews:ready:0')
    assert 'Готовые сценарии · 0' in c.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_old_enable_confirmation_cannot_undo_explicit_disable(context):
    from news_prototype import general_auto
    c = context
    with patch.object(general_auto, 'keys_ready', return_value=True):
        await click(c, 'gnews:autoon')
        confirmation = await c.db.owner_panel_confirmations.find_one({'action': 'general_auto_on'})
        await click(c, 'gnews:autooff')
        c.callback.data = 'owner_tools:run:' + confirmation['_id']
        await tools.action(c.callback, c.state, bot=None)
        assert not await general_auto.enabled(c.db)
        assert 'устарело' in c.message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_candidate_card_explains_ad_deferral_and_allows_manual_prepare(context):
    c = context
    key = await general_queue.add_news(c.db, 'https://example.com/ad')
    await c.db.news_general_candidates.update_one({'_id': key}, {'$set': {'title': 'Реклама: новая выставка'}})
    await click(c, 'gnews:news:0')
    await click(c, button(c.message, 'Реклама: новая выставка'))
    text = c.message.edit_text.await_args.args[0]
    assert 'рекламная отметка' in text
    assert 'музыка и культура' in text
    assert button(c.message, 'Подготовить сценарий')
    assert await c.db.news_owner_drafts.count_documents({}) == 0
