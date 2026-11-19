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


def news_button(message, title):
    import re
    text = message.edit_text.await_args.args[0]
    for line in text.splitlines():
        match = re.match(r'^(\d+)\. ', line)
        if match and title in line:
            markup = message.edit_text.await_args.kwargs['reply_markup']
            return next(b.callback_data for row in markup.inline_keyboard for b in row if b.text == match.group(1))
    raise AssertionError('Новость не найдена в тексте')


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
    item = news_button(c.message, 'Новость по ссылке')
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
    await click(c, news_button(c.message, 'Новость по ссылке'))
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
    await click(c, news_button(c.message, 'Новость по ссылке'))
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
    assert 'Добавлено новостей: 3' in c.message.edit_text.await_args.args[0]
    c.message.answer.assert_not_awaited()
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
    await click(c, news_button(c.message, 'Реклама: новая выставка'))
    text = c.message.edit_text.await_args.args[0]
    assert 'рекламная отметка' in text
    assert 'музыка и культура' in text
    assert button(c.message, 'Подготовить сценарий')
    assert await c.db.news_owner_drafts.count_documents({}) == 0


@pytest.mark.asyncio
async def test_balanced_list_is_readable_and_preserves_exact_item_when_order_changes(context):
    from datetime import datetime, timedelta, timezone
    c = context
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    keys = []
    titles = ['Путин сделал заявление', 'Правительство объявило решение',
              'Парламент обсудил закон', 'Президент выступил',
              'Учёные провели эксперимент', 'Новая видеоигра вышла']
    for i, title in enumerate(titles):
        key = await general_queue.add_news(c.db, f'https://example.com/{i}', now=now-timedelta(minutes=i))
        await c.db.news_general_candidates.update_one({'_id': key}, {'$set': {
            'title': title, 'source': 'Тестовый источник', 'source_id': 'tass', 'topic_hint': 'general'}})
        keys.append(key)
    await click(c, 'gnews:news:0')
    text = c.message.edit_text.await_args.args[0]
    assert 'Учёные провели эксперимент' in text and 'Новая видеоигра вышла' in text
    assert 'Путин сделал заявление' in text and 'политика и общество' in text
    assert len([b for row in c.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard
                for b in row if b.callback_data and b.callback_data.startswith('gnews:item:')]) == 4
    chosen = news_button(c.message, 'Учёные провели эксперимент')
    # A new collection arriving after the list was shown must not redirect the old button.
    await general_queue.add_news(c.db, 'https://example.com/new', now=now+timedelta(minutes=1))
    await click(c, chosen)
    assert 'Учёные провели эксперимент' in c.message.edit_text.await_args.args[0]
    await click(c, button(c.message, 'Убрать новость'))
    assert (await c.db.news_general_candidates.find_one({'_id': keys[4]}))['hidden'] is True
    assert (await c.db.news_general_candidates.find_one({'_id': keys[0]})).get('hidden') is False


@pytest.mark.asyncio
async def test_all_by_date_and_removed_page_reach_items_beyond_balanced_window(context):
    from datetime import datetime, timedelta, timezone
    c = context
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    # Keep the test small while exercising the same path used above the real 1000 limit.
    with patch.object(panel, 'NEWS_LIMIT', 4):
        for i in range(9):
            key = await general_queue.add_news(c.db, f'https://example.com/{i}', now=now-timedelta(minutes=i))
            await c.db.news_general_candidates.update_one({'_id': key}, {'$set': {'title': f'История {i}'}})
        await click(c, 'gnews:news:0')
        assert 'ограничена 4' in c.message.edit_text.await_args.args[0]
        await click(c, button(c.message, 'Все по дате'))
        await click(c, 'gnews:all:2')
        assert 'История 8' in c.message.edit_text.await_args.args[0]
        await click(c, news_button(c.message, 'История 8'))
        await click(c, button(c.message, 'Убрать новость'))
        await click(c, 'gnews:removed:0')
        await click(c, news_button(c.message, 'История 8'))
        await click(c, button(c.message, 'Вернуть новость'))
        assert 'Новость убрана' not in c.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_last_poll_failures_show_names_and_pages(context):
    c = context
    from news_prototype import general_sources
    sources = await general_sources.list_sources(c.db)
    ids = [source['id'] for source in sources]
    await c.db.news_general_state.insert_one({'_id': 'last_poll', 'failed': ids})
    await click(c, 'gnews:home')
    await click(c, button(c.message, 'Недоступные источники'))
    assert sources[0]['name'] in c.message.edit_text.await_args.args[0]
    assert 'не означает' in c.message.edit_text.await_args.args[0]
    for _ in range((len(sources) - 1) // 20):
        await click(c, button(c.message, 'Дальше'))
    assert sources[-1]['name'] in c.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_poll_error_replaces_message_and_keeps_navigation(context):
    c = context
    with patch.object(general_queue, 'poll', AsyncMock(side_effect=RuntimeError('private data'))):
        await click(c, 'gnews:poll')
    assert 'Сбор не завершён' in c.message.edit_text.await_args.args[0]
    assert button(c.message, 'Новости')
    c.message.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_category_browse_override_and_manual_operations(context):
    c = context
    game = await general_queue.add_news(c.db, 'https://example.com/game')
    crypto = await general_queue.add_news(c.db, 'https://example.com/crypto')
    await c.db.news_general_candidates.update_one({'_id': game}, {'$set': {'title': 'Вышла новая видеоигра'}})
    await c.db.news_general_candidates.update_one({'_id': crypto}, {'$set': {'title': 'Курс биткоина вырос'}})
    await click(c, 'gnews:home')
    await click(c, button(c.message, 'Новости по категориям'))
    for label in ('ИгроМир', 'Крипта', 'Интернет', 'Юмор'):
        assert button(c.message, label)
    await click(c, button(c.message, 'ИгроМир'))
    assert 'Вышла новая видеоигра' in c.message.edit_text.await_args.args[0]
    assert 'Курс биткоина' not in c.message.edit_text.await_args.args[0]
    await click(c, news_button(c.message, 'Вышла новая видеоигра'))
    assert button(c.message, 'Подготовить сценарий')
    await click(c, button(c.message, 'Изменить категорию'))
    await click(c, button(c.message, 'Юмор'))
    assert 'Категория выбрана вручную: Юмор' in c.message.edit_text.await_args.args[0]
    assert (await c.db.news_general_candidates.find_one({'_id': game}))['category_override'] == 'humor'
    await click(c, 'gnews:cat:games:0')
    assert 'Пока нет новостей' in c.message.edit_text.await_args.args[0]
    await click(c, 'gnews:cat:humor:0')
    await click(c, news_button(c.message, 'Вышла новая видеоигра'))
    await click(c, button(c.message, 'Убрать новость'))
    assert (await c.db.news_general_candidates.find_one({'_id': game}))['hidden'] is True
    await click(c, button(c.message, 'Вернуть новость'))
    assert (await c.db.news_general_candidates.find_one({'_id': game}))['hidden'] is False
    await click(c, button(c.message, 'Изменить категорию'))
    await click(c, button(c.message, 'Определять автоматически'))
    assert 'category_override' not in await c.db.news_general_candidates.find_one({'_id': game})
    assert await c.db.news_owner_drafts.count_documents({}) == 0


@pytest.mark.asyncio
async def test_source_category_can_be_changed_without_losing_source_or_existing_news(context):
    c = context
    key = await general_queue.add_news(c.db, 'https://example.com/story')
    await c.db.news_general_candidates.update_one({'_id': key}, {'$set': {'source_id': 'igromania', 'title': 'Новые подробности'}})
    await click(c, 'gnews:sources:0')
    await click(c, button(c.message, 'Игромания'))
    await click(c, button(c.message, 'Категория источника'))
    await click(c, button(c.message, 'Юмор'))
    assert 'по умолчанию: Юмор' in c.message.edit_text.await_args.args[0]
    await click(c, 'gnews:cat:humor:0')
    assert 'Новые подробности' in c.message.edit_text.await_args.args[0]
    source = next(s for s in await general_sources.list_sources(c.db) if s['id'] == 'igromania')
    assert source['topic_hint'] == 'humor'
    assert await c.db.news_general_candidates.count_documents({}) == 1


@pytest.mark.asyncio
async def test_category_toggle_is_idempotent_does_not_start_automation_and_manual_list_keeps_news(context):
    from news_prototype import general_auto
    c = context
    key = await general_queue.add_news(c.db, 'https://example.com/politics')
    await c.db.news_general_candidates.update_one({'_id': key}, {'$set': {'title': 'Путин выступил'}})
    await click(c, 'gnews:auto')
    await click(c, button(c.message, 'Категории отбора'))
    disable = button(c.message, 'Политика и общество')
    await click(c, disable)
    await click(c, disable)  # stale button repeats a set, not an unintended toggle
    assert 'politics_society' in await general_auto.disabled_categories(c.db)
    assert not await general_auto.enabled(c.db)
    await click(c, 'gnews:cat:politics_society:0')
    assert 'Путин выступил' in c.message.edit_text.await_args.args[0]
    await click(c, 'gnews:catsettings')
    await click(c, button(c.message, 'Политика и общество'))
    assert 'politics_society' not in await general_auto.disabled_categories(c.db)
    assert not await general_auto.enabled(c.db)


@pytest.mark.asyncio
async def test_category_scans_beyond_general_list_window_and_bounds_page(context):
    from datetime import datetime, timedelta, timezone
    c = context
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    for i in range(7):
        key = await general_queue.add_news(c.db, f'https://example.com/c{i}', now=now - timedelta(minutes=i))
        await c.db.news_general_candidates.update_one({'_id': key}, {'$set': {
            'title': 'Видеоигра старая' if i == 6 else 'Путин рассказал о встрече'}})
    with patch.object(panel, 'NEWS_LIMIT', 4):
        await click(c, 'gnews:cat:games:999')
        assert 'Видеоигра старая' in c.message.edit_text.await_args.args[0]
        assert '1/1' in c.message.edit_text.await_args.args[0]
        old_button = news_button(c.message, 'Видеоигра старая')
        # New news cannot change which item the old button opens.
        await general_queue.add_news(c.db, 'https://example.com/newest')
        await click(c, old_button)
        assert 'Видеоигра старая' in c.message.edit_text.await_args.args[0]
        assert button(c.message, '⬅️ Новости') == 'gnews:cat:games:0'


@pytest.mark.asyncio
async def test_category_changes_rejected_for_non_owner_and_stale_button(context):
    from news_prototype import general_auto
    c = context
    c.callback.from_user = SimpleNamespace(id=999)
    await click(c, 'gnews:ctoggle:politics_society:0')
    assert not await general_auto.disabled_categories(c.db)
    assert 'Только для владельца' in c.callback.answer.await_args.args[0]
    c.callback.from_user = SimpleNamespace(id=123)
    await click(c, 'gnews:nassign:0000000000000000:0:humor')
    assert 'устарела' in c.callback.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_choose_only_wanted_categories_with_clear_all(context):
    from news_prototype import general_auto, general_editorial
    c = context
    await click(c, 'gnews:catsettings')
    await click(c, button(c.message, 'Выключить все'))
    assert await general_auto.disabled_categories(c.db) == set(general_editorial.LABELS)
    await click(c, button(c.message, 'ИгроМир'))
    await click(c, button(c.message, 'Крипта'))
    assert set(general_editorial.LABELS) - await general_auto.disabled_categories(c.db) == {'games', 'crypto'}
    assert not await general_auto.enabled(c.db)
    await click(c, button(c.message, 'Включить все'))
    assert not await general_auto.disabled_categories(c.db)


@pytest.mark.asyncio
async def test_one_button_run_requires_one_use_confirmation_and_shows_result(context):
    from news_prototype import general_auto
    c = context
    fake = AsyncMock(return_value={'status': 'done', 'inserted': 4, 'ready': 2, 'attempts': 3, 'failed': []})
    with patch.object(general_auto, 'keys_ready', return_value=True), \
         patch.object(general_auto, 'run_once', fake):
        await click(c, 'gnews:home')
        await click(c, button(c.message, 'Собрать и подготовить подборку'))
        fake.assert_not_awaited()
        confirmation = await c.db.owner_panel_confirmations.find_one({'action': 'general_run_once'})
        assert confirmation and confirmation['stage'] == 'ready'
        assert 'квота' in c.message.answer.await_args.args[0]
        c.callback.data = 'owner_tools:run:' + confirmation['_id']
        await tools.action(c.callback, c.state, bot=None)
        fake.assert_awaited_once_with(c.db, 123)
        assert 'сценариев готово 2 из 3' in c.message.edit_text.await_args.args[0]
        assert 'Рендер и публикация не запускались' in c.message.edit_text.await_args.args[0]
        await tools.action(c.callback, c.state, bot=None)
        fake.assert_awaited_once()
        assert not await general_auto.enabled(c.db)


@pytest.mark.asyncio
async def test_confirmed_button_full_category_collection_to_review_card(context):
    """Exercise the actual UI, collection, editor and review with no external calls."""
    from datetime import datetime, timezone
    from news_prototype import article_fetch, general_auto, general_discovery, story_review
    c = context
    current = datetime.now(timezone.utc).isoformat()
    items = [
        {'source_id': 'igromania', 'title': 'Видеоигра получила обновление',
         'url': 'https://example.com/game', 'published_at': current},
        {'source_id': 'tass', 'title': 'Путин сделал заявление',
         'url': 'https://example.com/politics', 'published_at': current},
    ]
    collect = AsyncMock(return_value=(items, ['space']))
    fetch = AsyncMock(return_value={'title': 'Обновление игры',
        'summary': 'Разработчик сообщил об обновлении игры и перечислил основные изменения. ' * 3,
        'resolved_url': 'https://example.com/game'})
    edit = AsyncMock(return_value={'decision': 'draft', 'verification': 'needs_review',
        'script': {'ticker': 'Обновление игры', 'turns': [
            {'speaker': 'Бендер', 'text': 'Разработчик сообщил об обновлении игры.'},
            {'speaker': 'Киберсенсей', 'text': 'Посмотрим на изменения.'}]}, 'evidence': []})
    review = AsyncMock(return_value='{"status":"issues","issues":[{"location":"Строка","reason":"Уточнить обновление","quote":""}]}')
    with patch.object(general_auto, 'keys_ready', return_value=True), \
         patch.object(general_discovery, 'collect', collect), \
         patch.object(article_fetch, 'fetch_article', fetch), \
         patch.object(news_owner_drafts, 'make_draft', edit), \
         patch.object(story_review, 'request_groq', review):
        await click(c, 'gnews:catsettings')
        await click(c, button(c.message, 'Выключить все'))
        await click(c, button(c.message, 'ИгроМир'))
        await click(c, 'gnews:home')
        await click(c, button(c.message, 'Собрать и подготовить подборку'))
        confirmation = await c.db.owner_panel_confirmations.find_one({'action': 'general_run_once'})
        assert confirmation and confirmation['stage'] == 'ready'
        collect.assert_not_awaited(); fetch.assert_not_awaited(); edit.assert_not_awaited(); review.assert_not_awaited()
        c.callback.data = 'owner_tools:run:' + confirmation['_id']
        await tools.action(c.callback, c.state, bot=None)
        assert 'сценариев готово 1 из 1' in c.message.edit_text.await_args.args[0]
        assert 'Ошибок источников: 1' in c.message.edit_text.await_args.args[0]
        assert await c.db.news_general_candidates.count_documents({}) == 2
        assert await c.db.news_general_candidates.count_documents({'stage': 'draft_ready'}) == 1
        assert not await general_auto.enabled(c.db)
        await click(c, 'gnews:ready:0')
        await click(c, news_button(c.message, 'Видеоигра получила обновление'))
        await click(c, button(c.message, 'Открыть черновик'))
        assert 'Уточнить обновление' in c.message.answer.await_args.args[0]
        assert await c.db.news_owner_trial_jobs.count_documents({}) == 0
        c.callback.data = 'owner_tools:run:' + confirmation['_id']
        await tools.action(c.callback, c.state, bot=None)
        collect.assert_awaited_once(); fetch.assert_awaited_once(); edit.assert_awaited_once(); review.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_category_explains_saved_state_without_collecting(context):
    from datetime import datetime, timezone
    from news_prototype import general_discovery
    c = context
    await c.db.news_general_state.insert_one({'_id': 'last_poll', 'at': datetime(2026, 1, 2, 12, tzinfo=timezone.utc), 'failed': ['tass']})
    await general_queue.add_news(c.db, 'https://example.com/item')
    with patch.object(general_discovery, 'collect', AsyncMock()) as collect:
        await click(c, 'gnews:cat:animals:0')
        text = c.message.edit_text.await_args.args[0]
        assert 'Пока нет новостей этой категории' in text
        assert 'Всего сохранено видимых новостей: 1' in text
        assert 'недоступных источников: 1' in text
        assert 'Источников с этой темой по умолчанию:' in text
        assert '\n\n\n' not in text
        collect.assert_not_awaited()


@pytest.mark.asyncio
async def test_translated_news_page_card_and_basket_reuse_cached_title(context):
    from news_prototype import general_translation
    c = context
    key = await general_queue.add_news(c.db, 'https://example.com/bird')
    await c.db.news_general_candidates.update_one({'_id': key}, {'$set': {
        'title': 'Rare birds return', 'topic_hint': 'animals'}})
    translate = AsyncMock(return_value=['Редкие птицы вернулись'])
    with patch.object(general_translation, 'translate_batch', translate):
        await click(c, 'gnews:cat:animals:0')
        assert 'Редкие птицы вернулись' in c.message.edit_text.await_args.args[0]
        await click(c, news_button(c.message, 'Редкие птицы вернулись'))
        text = c.message.edit_text.await_args.args[0]
        assert 'Редкие птицы вернулись' in text and 'Оригинал: Rare birds return' in text
        await click(c, button(c.message, 'Добавить к вестнику'))
        await click(c, 'gnews:basket')
        assert 'Редкие птицы вернулись' in c.message.edit_text.await_args.args[0]
        await click(c, 'gnews:all:0')
        assert 'Редкие птицы вернулись' in c.message.edit_text.await_args.args[0]
        translate.assert_awaited_once_with(['Rare birds return'])
    assert (await c.db.news_general_candidates.find_one({'_id': key}))['title'] == 'Rare birds return'
    assert await c.db.news_owner_drafts.count_documents({}) == 0


@pytest.mark.asyncio
async def test_translation_failure_shows_original_and_does_not_break_navigation(context):
    from news_prototype import general_translation
    c = context
    key = await general_queue.add_news(c.db, 'https://example.com/english')
    await c.db.news_general_candidates.update_one({'_id': key}, {'$set': {'title': 'English headline'}})
    with patch.object(general_translation, 'translate_batch', AsyncMock(return_value=None)) as translate:
        await click(c, 'gnews:all:0')
        assert 'English headline' in c.message.edit_text.await_args.args[0]
        assert 'перевод временно недоступен' in c.message.edit_text.await_args.args[0]
        await click(c, news_button(c.message, 'English headline'))
        assert 'Перевод временно недоступен' in c.message.edit_text.await_args.args[0]
        translate.assert_awaited_once()
