import asyncio
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

import pytest
from mongomock_motor import AsyncMongoMockClient
from handlers import owner_video_sender as sender, owner_tools_panel as tools, owner_control_panel


@pytest.fixture
def ctx():
    db = AsyncMongoMockClient().test
    fake = SimpleNamespace(db=db, auth_chats=db.auth_chats)
    user = SimpleNamespace(id=tools.ADMIN_ID)
    message = SimpleNamespace(chat=SimpleNamespace(id=user.id, type='private'), from_user=user,
        message_id=123, answer=AsyncMock(), edit_text=AsyncMock(), video=None, video_note=None,
        document=None, caption=None)
    callback = SimpleNamespace(from_user=user, message=message, data='', answer=AsyncMock())
    state = SimpleNamespace(clear=AsyncMock(), get_state=AsyncMock(), get_data=AsyncMock(),
                            set_state=AsyncMock(), update_data=AsyncMock())
    bot = SimpleNamespace(get_chat=AsyncMock(return_value=SimpleNamespace(id=-101, type='supergroup', title='Друзья')),
        get_me=AsyncMock(return_value=SimpleNamespace(id=300)),
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status='member')),
        send_video=AsyncMock(return_value=SimpleNamespace(message_id=92)),
        send_video_note=AsyncMock(return_value=SimpleNamespace(message_id=93)), download=AsyncMock())
    message.bot = bot
    with patch.object(sender, 'db', fake), patch.object(tools, 'db', fake):
        yield SimpleNamespace(db=db, user=user, message=message, callback=callback, state=state, bot=bot)


async def prepare(ctx, *, mode='video', caption='Подпись'):
    await ctx.db.auth_chats.insert_one({'chat_id': -101, 'title': 'Друзья', 'status': 'approved'})
    ctx.message.caption = caption
    data = {'action': 'owner_send_video', 'input_token': 'a'*16, 'chat_id': -101, 'mode': mode}
    ctx.state.get_data.return_value = data
    if mode == 'video':
        ctx.message.video = SimpleNamespace(file_id='original-video', file_size=50_000_000, duration=12)
    else:
        ctx.message.video_note = SimpleNamespace(file_id='original-note', file_size=100, duration=12)
    await sender.receive_video(ctx.message, ctx.state, data)
    return await ctx.db.owner_video_confirmations.find_one({})


@pytest.mark.asyncio
async def test_choose_approved_group_and_mode_no_post(ctx):
    await ctx.db.auth_chats.insert_many([
        {'chat_id': -101, 'title': 'Друзья', 'status': 'approved'},
        {'chat_id': -102, 'title': 'Запрещено', 'status': 'denied'},
        {'chat_id': -103, 'title': 'Ожидает', 'status': 'pending'}])
    ctx.callback.data = 'ovideo:open'
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    buttons = [b for row in ctx.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
    assert any(b.callback_data == 'ovideo:chat:-101' for b in buttons)
    assert not any('-102' in (b.callback_data or '') or '-103' in (b.callback_data or '') for b in buttons)
    ctx.callback.data = 'ovideo:chat:-101'
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    buttons = [b.callback_data for row in ctx.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
    assert 'ovideo:format:-101:video' in buttons and 'ovideo:format:-101:note' in buttons
    ctx.callback.data = 'ovideo:format:-101:note'
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    assert ctx.state.update_data.await_args.kwargs['mode'] == 'note'
    ctx.bot.send_video.assert_not_awaited(); ctx.bot.send_video_note.assert_not_awaited()


@pytest.mark.asyncio
async def test_regular_confirmation_single_use_and_caption(ctx):
    record = await prepare(ctx)
    assert record['caption'] == 'Подпись' and record['file_id'] == 'original-video'
    ctx.bot.send_video.assert_not_awaited()
    ctx.callback.data = 'ovideo:send:' + record['_id']
    await asyncio.gather(sender.action(ctx.callback, ctx.state, ctx.bot), sender.action(ctx.callback, ctx.state, ctx.bot))
    ctx.bot.send_video.assert_awaited_once_with(chat_id=-101, video='original-video', caption='Подпись', parse_mode=None)
    assert (await ctx.db.owner_video_confirmations.find_one({}))['stage'] == 'sent'


@pytest.mark.asyncio
async def test_existing_note_and_cancel(ctx):
    record = await prepare(ctx, mode='note')
    assert not record['caption'] and record['file_id'] == 'original-note'
    ctx.callback.data = 'ovideo:cancel:' + record['_id']
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    ctx.callback.data = 'ovideo:send:' + record['_id']
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    ctx.bot.send_video_note.assert_not_awaited()
    assert (await ctx.db.owner_video_confirmations.find_one({}))['stage'] == 'cancelled'


@pytest.mark.asyncio
async def test_foreign_expired_revoked_or_gone_never_publish(ctx):
    record = await prepare(ctx)
    ctx.callback.data = 'ovideo:send:' + record['_id']
    ctx.callback.from_user = SimpleNamespace(id=ctx.user.id+1)
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    ctx.callback.from_user = ctx.user
    ctx.bot.get_chat_member.return_value = SimpleNamespace(status='left')
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    ctx.bot.get_chat_member.return_value = SimpleNamespace(status='restricted', is_member=False)
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    ctx.bot.get_chat_member.return_value = SimpleNamespace(status='member')
    await ctx.db.auth_chats.update_one({'chat_id': -101}, {'$set': {'status': 'denied'}})
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    await ctx.db.auth_chats.update_one({'chat_id': -101}, {'$set': {'status': 'approved'}})
    await ctx.db.owner_video_confirmations.update_one({'_id': record['_id']}, {'$set': {'expires_at': datetime.now(timezone.utc)-timedelta(seconds=1)}})
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    ctx.bot.send_video.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_delivery_never_retries(ctx):
    record = await prepare(ctx)
    ctx.bot.send_video.side_effect = TimeoutError('secret')
    ctx.callback.data = 'ovideo:send:' + record['_id']
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    ctx.bot.send_video.assert_awaited_once()
    assert (await ctx.db.owner_video_confirmations.find_one({}))['stage'] == 'uncertain'
    assert any('могло быть отправлено' in c.args[0] for c in ctx.message.answer.await_args_list)
    assert all('secret' not in c.args[0] for c in ctx.message.answer.await_args_list)


@pytest.mark.asyncio
async def test_circle_preview_fences_cancel_and_checks_size(ctx):
    await ctx.db.auth_chats.insert_one({'chat_id': -101, 'title': 'Друзья', 'status': 'approved'})
    data = {'action': 'owner_send_video', 'input_token': 'a'*16, 'chat_id': -101, 'mode': 'note'}
    ctx.message.video = SimpleNamespace(file_id='input', file_size=20_000_001, duration=30)
    ctx.state.get_data.return_value = data
    await sender.receive_video(ctx.message, ctx.state, data)
    assert '20 МБ' in ctx.message.answer.await_args.args[0]
    ctx.bot.download.assert_not_awaited()
    ctx.message.video.file_size = 12
    async def download(file_id, destination): destination.write(b'1234ftypxxxx')
    async def make_note(source, target): target.write_bytes(b'note')
    ctx.bot.download.side_effect = download
    ctx.bot.send_video_note.return_value = SimpleNamespace(video_note=SimpleNamespace(file_id='preview-note'))
    with patch.object(sender.owner_video_media, 'make_note', side_effect=make_note):
        await sender.receive_video(ctx.message, ctx.state, data)
    ctx.bot.send_video_note.assert_awaited_once()
    assert ctx.bot.send_video_note.await_args.kwargs['chat_id'] == ctx.user.id
    record = await ctx.db.owner_video_confirmations.find_one({})
    assert record['file_id'] == 'preview-note'
    ctx.callback.data = 'ovideo:send:' + record['_id']
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    assert ctx.bot.send_video_note.await_args.kwargs == {'chat_id': -101, 'video_note': 'preview-note'}


@pytest.mark.asyncio
async def test_cancel_during_conversion_never_makes_confirmation(ctx):
    await ctx.db.auth_chats.insert_one({'chat_id': -101, 'title': 'Друзья', 'status': 'approved'})
    data = {'action': 'owner_send_video', 'input_token': 'a'*16, 'chat_id': -101, 'mode': 'note'}
    ctx.message.video = SimpleNamespace(file_id='input', file_size=12, duration=10)
    async def download(file_id, destination): destination.write(b'1234ftypxxxx')
    async def make_note(source, target): target.write_bytes(b'note')
    ctx.bot.download.side_effect = download
    ctx.bot.send_video_note.return_value = SimpleNamespace(video_note=SimpleNamespace(file_id='preview-note'))
    ctx.state.get_data.return_value = {'action': 'other'}
    with patch.object(sender.owner_video_media, 'make_note', side_effect=make_note):
        await sender.receive_video(ctx.message, ctx.state, data)
    assert await ctx.db.owner_video_confirmations.count_documents({}) == 0
    assert ctx.bot.send_video_note.await_args.kwargs['chat_id'] == ctx.user.id


def test_real_dispatcher_handles_video_note_before_transcription():
    import subprocess, sys
    code = r'''
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiogram import Bot
from aiogram.methods import SendMessage
from aiogram.types import Update, Message
from mongomock_motor import AsyncMongoMockClient
from app_setup import configure_dispatcher
from config import ADMIN_ID
from handlers import owner_video_sender as sender, owner_tools_panel as tools, logger, alc_build, alc_intro, voice_rec

async def run():
    database = AsyncMongoMockClient().test
    fake = SimpleNamespace(db=database, auth_chats=database.auth_chats, settings=database.settings,
                           alchemy_players=database.alchemy_players, messages=database.messages)
    await database.auth_chats.insert_one({'chat_id':-101,'title':'Друзья','status':'approved'})
    bot = Bot('123456:TESTTOKEN'); calls=[]
    async def request(bot_arg, method, **kwargs):
        calls.append(method)
        if isinstance(method, SendMessage):
            return Message.model_validate({'message_id':999,'date':1760000000,
                'chat':{'id':ADMIN_ID,'type':'private'},'text':'ok'}, context={'bot':bot_arg})
        return True
    dp = configure_dispatcher()
    user={'id':ADMIN_ID,'is_bot':False,'first_name':'Owner'}; chat={'id':ADMIN_ID,'type':'private'}
    async def cb(i, data):
        await asyncio.wait_for(dp.feed_update(bot, Update.model_validate({'update_id':i,'callback_query':{
            'id':str(i),'chat_instance':'owner','from':user,'data':data,'message':{
            'message_id':23,'date':1760000000,'chat':chat,'from':{'id':123456,'is_bot':True,'first_name':'Bot'},'text':'panel'}}})), 5)
    with patch.object(sender,'db',fake), patch.object(tools,'db',fake), patch.object(logger,'db',fake), \
         patch.object(alc_build,'db',fake), patch.object(alc_intro,'get_intro_session',AsyncMock(return_value=None)), \
         patch.object(bot,'get_chat',AsyncMock(return_value=SimpleNamespace(id=-101,type='supergroup',title='Друзья'))), \
         patch.object(bot,'get_me',AsyncMock(return_value=SimpleNamespace(id=123456))), \
         patch.object(bot,'get_chat_member',AsyncMock(return_value=SimpleNamespace(status='member'))), \
         patch.object(bot,'send_video_note',AsyncMock(return_value=SimpleNamespace(message_id=88))) as send, \
         patch.object(logger,'start_transcription',AsyncMock()) as transcribe, \
         patch.object(voice_rec,'get_transcription',AsyncMock()) as voice, \
         patch.object(bot.session,'make_request',AsyncMock(side_effect=request)):
        await cb(1,'ovideo:open'); await cb(2,'ovideo:chat:-101'); await cb(3,'ovideo:format:-101:note')
        update=Update.model_validate({'update_id':4,'message':{'message_id':44,'date':1760000000,'chat':chat,
            'from':user,'video_note':{'file_id':'file-note','file_unique_id':'unique','length':240,'duration':2,'file_size':500}}})
        await asyncio.wait_for(dp.feed_update(bot,update),5)
        transcribe.assert_not_called(); voice.assert_not_awaited(); send.assert_not_awaited()
        record = await database.owner_video_confirmations.find_one({})
        assert record and record['file_id']=='file-note'
        await cb(5,'ovideo:send:'+record['_id']); await cb(6,'ovideo:send:'+record['_id'])
        send.assert_awaited_once_with(chat_id=-101, video_note='file-note')
    await bot.session.close()
asyncio.run(run())
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30, env=os.environ.copy())
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
async def test_owner_tools_menu_contains_video_entry(ctx):
    ctx.callback.data = 'owner_panel:tools'
    await owner_control_panel.control_action(ctx.callback, ctx.state)
    markup = ctx.message.edit_text.await_args.kwargs['reply_markup']
    assert any(b.callback_data == 'ovideo:open' for row in markup.inline_keyboard for b in row)


@pytest.mark.asyncio
async def test_title_change_and_inflight_send_are_never_reused(ctx):
    record = await prepare(ctx)
    ctx.callback.data = 'ovideo:send:' + record['_id']
    ctx.bot.get_chat.return_value.title = 'Новое имя'
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    ctx.bot.send_video.assert_not_awaited()
    assert 'изменилось' in ctx.callback.answer.await_args.args[0]
    ctx.bot.get_chat.return_value.title = 'Друзья'
    await ctx.db.owner_video_confirmations.update_one({'_id': record['_id']}, {'$set': {'stage':'sending'}})
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    ctx.bot.send_video.assert_not_awaited()


@pytest.mark.asyncio
async def test_long_circle_and_unapproved_group_block(ctx):
    await ctx.db.auth_chats.insert_one({'chat_id': -101, 'title': 'Друзья', 'status': 'approved'})
    data = {'action': 'owner_send_video', 'input_token': 'a'*16, 'chat_id': -101, 'mode': 'note'}
    ctx.message.video = SimpleNamespace(file_id='source', file_size=100, duration=61)
    ctx.state.get_data.return_value = data
    await sender.receive_video(ctx.message, ctx.state, data)
    assert '60 секунд' in ctx.message.answer.await_args.args[0]
    ctx.bot.download.assert_not_awaited()
    ctx.callback.data = 'ovideo:chat:-102'
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    assert ctx.callback.answer.await_args.kwargs['show_alert']
    assert await ctx.db.owner_video_confirmations.count_documents({}) == 0


@pytest.mark.asyncio
async def test_telegram_rejection_is_final_and_no_retry(ctx):
    from aiogram.exceptions import TelegramForbiddenError
    from aiogram.methods import SendVideo
    record = await prepare(ctx)
    ctx.bot.send_video.side_effect = TelegramForbiddenError(method=SendVideo(chat_id=-101, video='x'), message='Forbidden')
    ctx.callback.data = 'ovideo:send:' + record['_id']
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    assert (await ctx.db.owner_video_confirmations.find_one({}))['stage'] == 'failed'
    ctx.bot.send_video.assert_awaited_once()
    assert any('не принял' in c.args[0] for c in ctx.message.answer.await_args_list)


@pytest.mark.asyncio
async def test_group_list_has_pagination(ctx):
    await ctx.db.auth_chats.insert_many([{'chat_id': -100-i, 'title': f'Чат {i}', 'status': 'approved'} for i in range(10)])
    ctx.callback.data = 'ovideo:open'
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    buttons = [b.callback_data for row in ctx.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
    assert len([b for b in buttons if b.startswith('ovideo:chat:')]) == 8
    assert 'ovideo:page:1' in buttons
    ctx.callback.data = 'ovideo:page:1'
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    buttons = [b.callback_data for row in ctx.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
    assert len([b for b in buttons if b.startswith('ovideo:chat:')]) == 2
    assert 'ovideo:page:0' in buttons


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['video', 'note'])
async def test_mongodb_64bit_group_id_works_through_selection_and_send(ctx, mode):
    from bson import BSON
    original = -1001234567890
    # MongoDB stores this Telegram ID as a BSON int64 and PyMongo reads it as
    # Int64, unlike the small integer identifiers used by older tests.
    cid = BSON(BSON.encode({'id': original})).decode()['id']
    assert type(cid) is not int and isinstance(cid, int)
    await ctx.db.auth_chats.insert_one({'chat_id': cid, 'title': 'Большой чат', 'status': 'approved'})
    ctx.bot.get_chat.return_value = SimpleNamespace(id=original, type='supergroup', title='Большой чат')
    ctx.callback.data = 'ovideo:open'
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    buttons = [b for row in ctx.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
    assert any(b.callback_data == f'ovideo:chat:{original}' for b in buttons)
    ctx.callback.data = f'ovideo:chat:{original}'
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    assert 'Большой чат' in ctx.message.answer.await_args.args[0]
    ctx.callback.data = f'ovideo:format:{original}:{mode}'
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    data = ctx.state.update_data.await_args.kwargs
    ctx.state.get_data.return_value = data
    if mode == 'video':
        ctx.message.video = SimpleNamespace(file_id='video', file_size=1234, duration=2)
    else:
        ctx.message.video_note = SimpleNamespace(file_id='video', file_size=1234, duration=2)
    await sender.receive_video(ctx.message, ctx.state, data)
    record = await ctx.db.owner_video_confirmations.find_one({})
    assert record and record['chat_id'] == original
    # Simulate the second database roundtrip, which is also BSON int64.
    await ctx.db.owner_video_confirmations.update_one({'_id': record['_id']}, {'$set': {'chat_id': cid}})
    ctx.callback.data = 'ovideo:send:' + record['_id']
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    if mode == 'video':
        ctx.bot.send_video.assert_awaited_once_with(chat_id=original, video='video', caption=None, parse_mode=None)
        assert type(ctx.bot.send_video.await_args.kwargs['chat_id']) is int
    else:
        ctx.bot.send_video_note.assert_awaited_once_with(chat_id=original, video_note='video')
        assert type(ctx.bot.send_video_note.await_args.kwargs['chat_id']) is int
    assert (await ctx.db.owner_video_confirmations.find_one({}))['stage'] == 'sent'


@pytest.mark.asyncio
async def test_invalid_group_ids_never_produce_buttons(ctx):
    # Non-integer numeric values compare < 0 in Mongo, but cannot be chat IDs.
    await ctx.db.auth_chats.insert_one({'chat_id': -123.5, 'title': 'Повреждённая запись', 'status': 'approved'})
    ctx.callback.data = 'ovideo:open'
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    buttons = [b.callback_data for row in ctx.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
    assert not any(b.startswith('ovideo:chat:') for b in buttons)
    assert 'нет доступных групп' in ctx.message.answer.await_args.args[0]
    for bad in (False, True, -1.5, '-100', 0, -2**63-1):
        with pytest.raises(ValueError): sender._chat_id(bad)
    ctx.bot.send_video.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('step', ['download', 'preview_note', 'group_check'])
async def test_telegram_bad_request_reports_failed_step_without_leaking(ctx, caplog, step):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import GetFile

    await ctx.db.auth_chats.insert_one({'chat_id': -101, 'title': 'Друзья', 'status': 'approved'})
    data = {'action': 'owner_send_video', 'input_token': 'a'*16, 'chat_id': -101, 'mode': 'note'}
    ctx.state.get_data.return_value = data
    ctx.message.video = SimpleNamespace(file_id='input', file_size=12, duration=2)
    async def download(file_id, destination): destination.write(b'1234ftypxxxx')
    async def make_note(source, target): target.write_bytes(b'note')
    ctx.bot.download.side_effect = download
    ctx.bot.send_video_note.return_value = SimpleNamespace(video_note=SimpleNamespace(file_id='preview'))
    exc = TelegramBadRequest(method=GetFile(file_id='secret-file-id'),
                             message='Bad Request: file is too big secret-file-id https://example.com/token')
    if step == 'download': ctx.bot.download.side_effect = exc
    elif step == 'preview_note': ctx.bot.send_video_note.side_effect = exc
    else: ctx.bot.get_chat.side_effect = exc
    with patch.object(sender.owner_video_media, 'make_note', side_effect=make_note):
        await sender.receive_video(ctx.message, ctx.state, data)
    assert f'stage={step} reason=file_is_too_big' in caplog.text
    assert 'secret-file-id' not in caplog.text and 'example.com' not in caplog.text
    assert 'слишком большой' in ctx.message.answer.await_args.args[0]
    assert 'В группу ничего не отправлено' in ctx.message.answer.await_args.args[0]
    assert await ctx.db.owner_video_confirmations.count_documents({}) == 0
    assert all(call.kwargs['chat_id'] != -101 for call in ctx.bot.send_video_note.await_args_list)
