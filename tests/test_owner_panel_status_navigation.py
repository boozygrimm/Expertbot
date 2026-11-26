import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

import pytest
from mongomock_motor import AsyncMongoMockClient
from handlers import auth, general_news_panel as general, news_owner_panel as news, owner_control_panel as panel, owner_video_sender as video
from news_prototype import general_auto, general_queue, owner_daily, owner_trial


def callbacks(markup):
    return {b.callback_data for row in markup.inline_keyboard for b in row}


@pytest.fixture
def ctx():
    db = AsyncMongoMockClient().test
    fake = SimpleNamespace(db=db, settings=db.settings, auth_chats=db.auth_chats, messages=db.messages)
    msg = SimpleNamespace(chat=SimpleNamespace(type='private', id=panel.ADMIN_ID), message_id=22,
                          answer=AsyncMock(), edit_text=AsyncMock(), delete=AsyncMock())
    cb = SimpleNamespace(from_user=SimpleNamespace(id=panel.ADMIN_ID), message=msg, answer=AsyncMock(), data='')
    state = SimpleNamespace(clear=AsyncMock(), get_data=AsyncMock(return_value={}))
    with patch.object(panel, 'db', fake), patch.object(news, 'db', fake), patch.object(general, 'db', fake), patch.object(auth, 'db', fake), patch.object(video, 'db', fake):
        yield SimpleNamespace(db=db, msg=msg, cb=cb, state=state)


@pytest.mark.asyncio
async def test_status_read_only_reports_real_saved_state_even_when_off(ctx):
    now = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
    await ctx.db.news_general_candidates.insert_many([{'_id': 'one'}, {'_id': 'two', 'hidden': True}])
    await ctx.db.news_owner_drafts.insert_many([
        {'_id': 1, 'owner_id': panel.ADMIN_ID, 'stage': 'draft_ready'},
        {'_id': 2, 'owner_id': panel.ADMIN_ID, 'stage': 'editor_failed'},
        {'_id': 3, 'owner_id': panel.ADMIN_ID + 1, 'stage': 'draft_ready'}])
    await ctx.db.news_general_state.insert_many([
        {'_id': 'last_poll', 'at': now, 'failed': ['one', 'two']},
        {'_id': 'one_shot_last', 'at': now, 'status': 'collect_failed'},
        {'_id': 'auto_last', 'at': now, 'status': 'editor_failed'}])
    with patch.object(general_queue, 'poll', AsyncMock()) as poll, \
         patch.object(owner_daily, 'enabled', AsyncMock(return_value=False)), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=False)), \
         patch.object(owner_trial, 'runtime_enabled', AsyncMock(return_value=False)), \
         patch.object(owner_trial, 'status_info', AsyncMock(return_value=('review_uncertain', None))), \
         patch.object(general_auto, 'keys_ready', return_value=False):
        ctx.cb.data = 'owner_panel:status'
        await panel.control_action(ctx.cb, ctx.state)
        text = ctx.msg.edit_text.await_args.args[0]
        markup = ctx.msg.edit_text.await_args.kwargs['reply_markup']
        assert 'Автоотбор · выключен' in text
        assert 'Сохранено видимых новостей · 1' in text
        assert 'Черновиков готово к просмотру · 1' in text
        assert '02.01.2026 15:00 МСК' in text
        assert 'Ошибок источников при сборе · 2' in text
        assert 'сбор не завершён' in text and 'ошибка редактора' in text
        assert 'Режим видео · выключен' in text and 'отправка не подтверждена' in text
        assert 'Ключи Gemini и Groq · один или оба не указаны' in text
        assert 'не проверяются' in text and 'не проверка работы сервера' in text
        assert {'owner_panel:home', 'owner_panel:close', 'gnews:failed:0', 'news_panel:collection'} <= callbacks(markup)
        poll.assert_not_awaited()
    assert await ctx.db.news_general_candidates.count_documents({}) == 2


@pytest.mark.asyncio
async def test_status_empty_and_database_failure_and_unauthorized(ctx):
    text, markup = await panel.status_content()
    assert 'ещё не было' in text and 'Источники сбором ещё не проверялись' in text
    assert 'gnews:failed:0' not in callbacks(markup)
    ctx.cb.data = 'owner_panel:status'
    with patch.object(panel, 'status_content', AsyncMock(side_effect=RuntimeError('secret'))):
        await panel.control_action(ctx.cb, ctx.state)
    assert 'Не удалось' in ctx.cb.answer.await_args.args[0]
    ctx.msg.edit_text.assert_not_awaited()
    ctx.cb.from_user.id += 1
    ctx.state.clear.reset_mock()
    await panel.control_action(ctx.cb, ctx.state)
    ctx.state.clear.assert_not_awaited()
    assert 'только владельцу' in ctx.cb.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_close_only_owner_and_navigation_screens(ctx):
    for route in ('group_menu', 'media', 'help', 'tools'):
        _, markup = panel.section_content(route)
        assert 'owner_panel:close' in callbacks(markup)
        assert any(k.startswith('owner_panel:') and k not in ('owner_panel:close',) for k in callbacks(markup) if k)
    for section in ('collection', 'scripts', 'editions', 'settings'):
        _, markup = await news.section_content(section)
        assert {'news_panel:home', 'owner_panel:close'} <= callbacks(markup)
    await general.home(ctx.msg)
    assert {'news_panel:home', 'owner_panel:close'} <= callbacks(ctx.msg.edit_text.await_args.kwargs['reply_markup'])
    ctx.cb.data = 'owner_panel:close'
    ctx.cb.from_user.id += 1
    await panel.control_action(ctx.cb, ctx.state)
    ctx.state.clear.assert_not_awaited(); ctx.msg.delete.assert_not_awaited()
    ctx.cb.from_user.id -= 1
    await panel.control_action(ctx.cb, ctx.state)
    ctx.state.clear.assert_awaited_once(); ctx.msg.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_group_list_empty_and_old_back_callback(ctx):
    ctx.cb.data = 'owner_panel:groups'
    await panel.control_action(ctx.cb, ctx.state)
    markup = ctx.msg.answer.await_args.kwargs['reply_markup']
    assert {'owner_panel:group_menu', 'owner_panel:close'} <= callbacks(markup)
    await ctx.db.auth_chats.insert_one({'chat_id': -1, 'title': 'Test', 'status': 'pending'})
    ctx.cb.data = 'back_to_list'
    await auth.back_to_list(ctx.cb)
    assert 'manage_chat:-1' in callbacks(ctx.msg.answer.await_args.kwargs['reply_markup'])
    ctx.msg.delete.assert_awaited_once()
    ctx.cb.from_user.id += 1
    ctx.msg.answer.reset_mock(); ctx.msg.delete.reset_mock()
    await auth.back_to_list(ctx.cb)
    ctx.msg.answer.assert_not_awaited(); ctx.msg.delete.assert_not_awaited()
    ctx.cb.data = 'manage_chat:-1'
    await auth.manage_chat_item(ctx.cb)
    ctx.msg.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_general_news_card_returns_to_its_page(ctx):
    for i in range(7):
        await ctx.db.news_general_candidates.insert_one({'_id': str(i), 'title': f'item {i}'})
    await general.news_list(ctx.msg, panel.ADMIN_ID, 1, kind='all')
    keyboard = ctx.msg.edit_text.await_args.kwargs['reply_markup']
    item = next(k for k in callbacks(keyboard) if k and k.startswith('gnews:item:'))
    ctx.cb.data = item
    await general.callback_action(ctx.cb, ctx.state)
    keyboard = ctx.msg.edit_text.await_args.kwargs['reply_markup']
    assert {'gnews:all:1', 'owner_panel:close'} <= callbacks(keyboard)


@pytest.mark.asyncio
async def test_source_card_and_video_format_navigation(ctx):
    await general.source_list(ctx.msg, panel.ADMIN_ID, 1)
    markup = ctx.msg.edit_text.await_args.kwargs['reply_markup']
    source = next(k for k in callbacks(markup) if k and k.startswith('gnews:source:'))
    ctx.cb.data = source
    await general.callback_action(ctx.cb, ctx.state)
    assert {'gnews:sources:1', 'owner_panel:close'} <= callbacks(ctx.msg.edit_text.await_args.kwargs['reply_markup'])
    await ctx.db.auth_chats.insert_one({'chat_id': -1, 'title': 'Test', 'status': 'approved'})
    bot = SimpleNamespace(get_chat=AsyncMock(return_value=SimpleNamespace(id=-1, type='group', title='Test')),
                          get_me=AsyncMock(return_value=SimpleNamespace(id=11)),
                          get_chat_member=AsyncMock(return_value=SimpleNamespace(status='member')),
                          delete_message=AsyncMock())
    ctx.cb.data = 'ovideo:open'
    await video.action(ctx.cb, ctx.state, bot)
    assert {'owner_panel:home', 'owner_panel:close'} <= callbacks(ctx.msg.answer.await_args.kwargs['reply_markup'])
    ctx.cb.data = 'ovideo:chat:-1'
    await video.action(ctx.cb, ctx.state, bot)
    assert {'ovideo:back', 'owner_panel:close'} <= callbacks(ctx.msg.answer.await_args.kwargs['reply_markup'])
    bot.delete_message.assert_awaited()
