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
async def test_converted_note_preview_and_confirm_when_private_notes_forbidden(ctx):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import SendVideoNote
    await ctx.db.auth_chats.insert_one({'chat_id': -101, 'title': 'Друзья', 'status': 'approved'})
    data = {'action': 'owner_send_video', 'input_token': 'a'*16, 'chat_id': -101, 'mode': 'note'}
    ctx.state.get_data.return_value = data
    ctx.message.video = SimpleNamespace(file_id='input', file_size=20_000_001, duration=30)
    await sender.receive_video(ctx.message, ctx.state, data)
    assert '20 МБ' in ctx.message.answer.await_args.args[0]
    ctx.bot.download.assert_not_awaited()
    source = b'1234ftypxxxx'
    converted = b'1234ftypconverted'
    ctx.message.video.file_size = len(source)
    async def download(file_id, destination):
        destination.write(source if file_id == 'input' else converted)
    async def make_note(source, target):
        target.write_bytes(converted)
        return 2.4
    async def send_note(chat_id, video_note, **kwargs):
        if chat_id == ctx.user.id:
            raise TelegramBadRequest(method=SendVideoNote(chat_id=chat_id, video_note='x'),
                                     message='Bad Request: VOICE_MESSAGES_FORBIDDEN')
        return SimpleNamespace(message_id=93)
    ctx.bot.download.side_effect = download
    ctx.bot.send_video.return_value = SimpleNamespace(video=SimpleNamespace(file_id='preview-video'))
    ctx.bot.send_video_note.side_effect = send_note
    with patch.object(sender.owner_video_media, 'make_note', side_effect=make_note):
        await sender.receive_video(ctx.message, ctx.state, data)
    ctx.bot.send_video_note.assert_not_awaited()
    assert ctx.bot.send_video.await_args.kwargs['chat_id'] == ctx.user.id
    assert ctx.bot.send_video.await_args.kwargs['video'].data == converted
    assert 'обычное видео' in ctx.message.answer.await_args.args[0]
    record = await ctx.db.owner_video_confirmations.find_one({})
    assert record['file_id'] == 'preview-video' and record['delivery'] == 'converted_note'
    assert record['duration'] == 3
    ctx.callback.data = 'ovideo:send:' + record['_id']
    await asyncio.gather(sender.action(ctx.callback, ctx.state, ctx.bot),
                         sender.action(ctx.callback, ctx.state, ctx.bot))
    ctx.bot.send_video_note.assert_awaited_once()
    sent = ctx.bot.send_video_note.await_args.kwargs
    assert sent['chat_id'] == -101 and sent['video_note'].data == converted
    assert sent['length'] == 480 and sent['duration'] == 3
    assert (await ctx.db.owner_video_confirmations.find_one({}))['stage'] == 'sent'


@pytest.mark.asyncio
async def test_cancel_during_conversion_never_makes_confirmation(ctx):
    await ctx.db.auth_chats.insert_one({'chat_id': -101, 'title': 'Друзья', 'status': 'approved'})
    data = {'action': 'owner_send_video', 'input_token': 'a'*16, 'chat_id': -101, 'mode': 'note'}
    ctx.message.video = SimpleNamespace(file_id='input', file_size=12, duration=10)
    async def download(file_id, destination): destination.write(b'1234ftypxxxx')
    async def make_note(source, target):
        target.write_bytes(b'1234ftypxxxx')
        return 2.4
    ctx.bot.download.side_effect = download
    ctx.bot.send_video.return_value = SimpleNamespace(video=SimpleNamespace(file_id='preview-video'))
    ctx.state.get_data.return_value = {'action': 'other'}
    with patch.object(sender.owner_video_media, 'make_note', side_effect=make_note):
        await sender.receive_video(ctx.message, ctx.state, data)
    assert await ctx.db.owner_video_confirmations.count_documents({}) == 0
    assert ctx.bot.send_video.await_args.kwargs['chat_id'] == ctx.user.id
    ctx.bot.send_video_note.assert_not_awaited()


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
from handlers import owner_video_sender as sender, owner_tools_panel as tools, logger, voice_rec

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
@pytest.mark.parametrize('step', ['download', 'preview_video', 'group_check'])
async def test_telegram_bad_request_reports_failed_step_without_leaking(ctx, caplog, step):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import GetFile

    await ctx.db.auth_chats.insert_one({'chat_id': -101, 'title': 'Друзья', 'status': 'approved'})
    data = {'action': 'owner_send_video', 'input_token': 'a'*16, 'chat_id': -101, 'mode': 'note'}
    ctx.state.get_data.return_value = data
    ctx.message.video = SimpleNamespace(file_id='input', file_size=12, duration=2)
    async def download(file_id, destination): destination.write(b'1234ftypxxxx')
    async def make_note(source, target):
        target.write_bytes(b'note')
        return 2.4
    ctx.bot.download.side_effect = download
    ctx.bot.send_video.return_value = SimpleNamespace(video=SimpleNamespace(file_id='preview'))
    exc = TelegramBadRequest(method=GetFile(file_id='secret-file-id'),
                             message='Bad Request: file is too big secret-file-id https://example.com/token')
    if step == 'download': ctx.bot.download.side_effect = exc
    elif step == 'preview_video': ctx.bot.send_video.side_effect = exc
    else: ctx.bot.get_chat.side_effect = exc
    with patch.object(sender.owner_video_media, 'make_note', side_effect=make_note):
        await sender.receive_video(ctx.message, ctx.state, data)
    assert f'stage={step} reason=file_is_too_big' in caplog.text
    assert 'secret-file-id' not in caplog.text and 'example.com' not in caplog.text
    assert 'слишком большой' in ctx.message.answer.await_args.args[0]
    assert 'В группу ничего не отправлено' in ctx.message.answer.await_args.args[0]
    assert await ctx.db.owner_video_confirmations.count_documents({}) == 0
    assert all(call.kwargs['chat_id'] != -101 for call in ctx.bot.send_video_note.await_args_list)


def test_unknown_telegram_description_filters_private_values():
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import GetFile
    exc = TelegramBadRequest(method=GetFile(file_id='not-logged'),
        message='Bad Request: video note processing failed at https://host.example/secret 123456:ABCXYZ privateword')
    detail = sender._safe_telegram_detail(exc)
    assert 'video note processing failed' in detail
    assert all(s not in detail for s in ('host', 'secret', '123456', 'abcxyz', 'privateword'))
    # Unknown descriptions retain useful safe vocabulary without exposing IDs.
    assert '[...]' in detail


def test_recognizes_common_video_note_rejections():
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import GetFile
    for error, code in [('VIDEO_NOTE_INVALID', 'video_note_invalid'),
                        ('VIDEO_NOTE_LENGTH_INVALID', 'video_note_length_invalid'),
                        ('Wrong video note length', 'wrong_video_note_length'),
                        ('There is no video note in the request', 'no_video_note')]:
        exc = TelegramBadRequest(method=GetFile(file_id='not-logged'), message=error)
        assert sender._telegram_error(exc)[0] == code


def test_public_telegram_symbol_is_logged_without_logging_arbitrary_input():
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import SendVideoNote
    method = SendVideoNote(chat_id=1, video_note='untrusted-file-id')
    # These actual public Telegram error names were hidden by the older filter.
    for name in ('FILE_PARTS_INVALID', 'FILE_CONTENT_TYPE_INVALID', 'VOICE_MESSAGES_FORBIDDEN'):
        exc = TelegramBadRequest(method=method, message='Bad Request: ' + name)
        assert sender._telegram_symbol(exc) == name
    for message in ('Bad Request: https://private.example/secret',
                    'Bad Request: 123456:BOT_TOKEN',
                    'Bad Request: unexpected arbitrary words',
                    'Bad Request: FILE_PARTS_INVALID private data'):
        exc = TelegramBadRequest(method=method, message=message)
        assert sender._telegram_symbol(exc) == 'unavailable'
    unknown = TelegramBadRequest(method=method, message='Bad Request: NEVER_SEEN_SECRET_IDENTIFIER')
    assert sender._telegram_symbol(unknown).startswith('sha256-')
    assert 'SECRET' not in sender._telegram_symbol(unknown)


@pytest.mark.asyncio
async def test_public_telegram_symbol_visible_in_preview_log_without_confirmation(ctx, caplog):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import SendVideoNote
    await ctx.db.auth_chats.insert_one({'chat_id': -101, 'title': 'Друзья', 'status': 'approved'})
    data = {'action': 'owner_send_video', 'input_token': 'a'*16, 'chat_id': -101, 'mode': 'note'}
    ctx.state.get_data.return_value = data
    ctx.message.video = SimpleNamespace(file_id='input-secret', file_size=12, duration=2)
    async def download(file_id, destination): destination.write(b'1234ftypxxxx')
    async def make_note(source, target):
        target.write_bytes(b'note')
        return 2.4
    ctx.bot.download.side_effect = download
    ctx.bot.send_video.side_effect = TelegramBadRequest(
        method=SendVideoNote(chat_id=1, video_note='input-secret'),
        message='Bad Request: FILE_CONTENT_TYPE_INVALID')
    with patch.object(sender.owner_video_media, 'make_note', side_effect=make_note):
        await sender.receive_video(ctx.message, ctx.state, data)
    assert 'stage=preview_video' in caplog.text
    assert 'telegram_code=FILE_CONTENT_TYPE_INVALID' in caplog.text
    assert 'input-secret' not in caplog.text
    assert await ctx.db.owner_video_confirmations.count_documents({}) == 0
    assert all(call.kwargs['chat_id'] != -101 for call in ctx.bot.send_video_note.await_args_list)


async def prepare_converted_note(ctx):
    await ctx.db.auth_chats.insert_one({'chat_id': -101, 'title': 'Друзья', 'status': 'approved'})
    data = {'action': 'owner_send_video', 'input_token': 'a'*16, 'chat_id': -101, 'mode': 'note'}
    ctx.state.get_data.return_value = data
    ctx.message.video = SimpleNamespace(file_id='input', file_size=12, duration=2)
    async def download(file_id, destination): destination.write(b'1234ftypxxxx')
    async def make_note(source, target):
        target.write_bytes(b'1234ftypnote')
        return 2.4
    ctx.bot.download.side_effect = download
    ctx.bot.send_video.return_value = SimpleNamespace(video=SimpleNamespace(file_id='preview-video'))
    with patch.object(sender.owner_video_media, 'make_note', side_effect=make_note):
        await sender.receive_video(ctx.message, ctx.state, data)
    return await ctx.db.owner_video_confirmations.find_one({'stage': 'ready'})


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['changed', 'download_error', 'revoked'])
async def test_converted_note_never_sent_if_retrieval_or_recheck_fails(ctx, failure):
    record = await prepare_converted_note(ctx)
    assert record and record['stage'] == 'ready'
    async def download(file_id, destination):
        if failure == 'download_error':
            raise TimeoutError('private')
        if failure == 'revoked':
            await ctx.db.auth_chats.update_one({'chat_id': -101}, {'$set': {'status': 'denied'}})
        destination.write(b'1234ftypbad!' if failure == 'changed' else b'1234ftypnote')
    ctx.bot.download.side_effect = download
    ctx.callback.data = 'ovideo:send:' + record['_id']
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    ctx.bot.send_video_note.assert_not_awaited()
    assert 'В группу ничего не отправлено' in ctx.message.answer.await_args.args[0]
    assert (await ctx.db.owner_video_confirmations.find_one({}))['stage'] == 'failed'
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    ctx.bot.send_video_note.assert_not_awaited()


@pytest.mark.asyncio
async def test_converted_note_cancel_and_ambiguous_delivery_never_repeat(ctx):
    record = await prepare_converted_note(ctx)
    ctx.callback.data = 'ovideo:cancel:' + record['_id']
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    ctx.callback.data = 'ovideo:send:' + record['_id']
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    ctx.bot.send_video_note.assert_not_awaited()
    # A new confirmation is necessary; unknown group delivery is never retried.
    await ctx.db.auth_chats.delete_many({})
    record = await prepare_converted_note(ctx)
    async def download(file_id, destination): destination.write(b'1234ftypnote')
    ctx.bot.download.side_effect = download
    ctx.bot.send_video_note.side_effect = TimeoutError('private')
    ctx.callback.data = 'ovideo:send:' + record['_id']
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    ctx.bot.send_video_note.assert_awaited_once()
    assert (await ctx.db.owner_video_confirmations.find_one({'_id': record['_id']}))['stage'] == 'uncertain'


@pytest.mark.asyncio
async def test_converted_note_roundtrip_with_real_aiogram_multipart(ctx):
    # Local fake Bot API exercises the real aiogram upload/download pipeline;
    # it never contacts Telegram or posts to a real chat.
    from aiohttp import web
    from aiogram import Bot
    from aiogram.client.session.aiohttp import AiohttpSession
    from aiogram.client.telegram import TelegramAPIServer

    source = b'1234ftypsrc!'
    converted = b'1234ftypconverted'
    seen = []
    async def method(request):
        name = request.match_info['method']
        form = await request.post()
        if name == 'getFile':
            data = source if form['file_id'] == 'source-id' else converted
            path = 'source.mp4' if form['file_id'] == 'source-id' else 'preview.mp4'
            return web.json_response({'ok': True, 'result': {'file_id': form['file_id'],
                'file_unique_id': 'unique', 'file_size': len(data), 'file_path': path}})
        if name == 'sendVideo':
            upload = form[form['video'].removeprefix('attach://')].file.read()
            seen.append((name, int(form['chat_id']), upload))
            return web.json_response({'ok': True, 'result': {'message_id': 1, 'date': 1760000000,
                'chat': {'id': ctx.user.id, 'type': 'private'}, 'video': {'file_id': 'preview-id',
                'file_unique_id': 'unique', 'width': 480, 'height': 480, 'duration': 3}}})
        if name == 'sendVideoNote':
            upload = form[form['video_note'].removeprefix('attach://')].file.read()
            seen.append((name, int(form['chat_id']), upload, int(form['length']), int(form['duration'])))
            return web.json_response({'ok': True, 'result': {'message_id': 2, 'date': 1760000000,
                'chat': {'id': -101, 'type': 'supergroup', 'title': 'Друзья'},
                'video_note': {'file_id': 'note-id', 'file_unique_id': 'unique', 'length': 480, 'duration': 3}}})
        return web.json_response({'ok': False, 'error_code': 400, 'description': 'Bad Request: unexpected'}, status=400)
    async def file(request):
        return web.Response(body=source if request.match_info['name'] == 'source.mp4' else converted)
    app = web.Application()
    app.router.add_post('/bot{token}/{method}', method)
    app.router.add_get('/file/bot{token}/{name}', file)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    bot = Bot('123456:TESTTOKEN', session=AiohttpSession(api=TelegramAPIServer.from_base(f'http://127.0.0.1:{port}')))
    try:
        ctx.message.bot = bot
        ctx.message.video = SimpleNamespace(file_id='source-id', file_size=len(source), duration=2)
        data = {'action': 'owner_send_video', 'input_token': 'a'*16, 'chat_id': -101, 'mode': 'note'}
        ctx.state.get_data.return_value = data
        async def make_note(src, target):
            target.write_bytes(converted)
            return 2.4
        with patch.object(sender.owner_video_media, 'make_note', side_effect=make_note), \
             patch.object(sender, '_group', AsyncMock(return_value='Друзья')):
            await sender.receive_video(ctx.message, ctx.state, data)
            record = await ctx.db.owner_video_confirmations.find_one({'stage': 'ready'})
            assert record and seen == [('sendVideo', ctx.user.id, converted)]
            ctx.callback.data = 'ovideo:send:' + record['_id']
            await sender.action(ctx.callback, ctx.state, bot)
        assert seen == [('sendVideo', ctx.user.id, converted), ('sendVideoNote', -101, converted, 480, 3)]
        assert (await ctx.db.owner_video_confirmations.find_one({'_id': record['_id']}))['stage'] == 'sent'
    finally:
        await bot.session.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_cleanup_removes_only_video_flow_bot_messages_after_success(ctx):
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=ctx.user.id, user_id=ctx.user.id))
    await ctx.db.auth_chats.insert_one({'chat_id': -101, 'title': 'Друзья', 'status': 'approved'})
    ctx.bot.delete_message = AsyncMock()
    ids = iter([101, 102, 103])  # input prompt, preparing notice, confirmation
    ctx.message.answer.side_effect = lambda *args, **kwargs: SimpleNamespace(message_id=next(ids))
    ctx.callback.data = 'ovideo:format:-101:note'
    await sender.action(ctx.callback, state, ctx.bot)
    data = await state.get_data()
    assert data['video_bot_messages'] == [101]
    source = b'1234ftypxxxx'; result = b'1234ftypnote'
    ctx.message.message_id = 777  # owner's incoming media, never delete it
    ctx.message.video = SimpleNamespace(file_id='source', file_size=len(source), duration=2)
    async def download(file_id, destination): destination.write(source if file_id == 'source' else result)
    async def make_note(src, target):
        target.write_bytes(result)
        return 2.4
    ctx.bot.download.side_effect = download
    ctx.bot.send_video.return_value = SimpleNamespace(message_id=104, video=SimpleNamespace(file_id='preview'))
    with patch.object(sender.owner_video_media, 'make_note', side_effect=make_note):
        await sender.receive_video(ctx.message, state, data)
    record = await ctx.db.owner_video_confirmations.find_one({})
    assert record['cleanup_ids'] == [101, 102, 104]
    ctx.callback.message = SimpleNamespace(chat=ctx.message.chat, message_id=103,
                                           answer=AsyncMock(), edit_text=AsyncMock())
    ctx.callback.data = 'ovideo:send:' + record['_id']
    await sender.action(ctx.callback, state, ctx.bot)
    deleted = {call.kwargs['message_id'] for call in ctx.bot.delete_message.await_args_list}
    assert {101, 102, 103, 104}.issubset(deleted)
    assert 777 not in deleted
    assert all(call.kwargs['chat_id'] == ctx.user.id for call in ctx.bot.delete_message.await_args_list)
    ctx.bot.send_video_note.assert_awaited_once()
    await storage.close()


@pytest.mark.asyncio
async def test_cancel_input_cleans_bot_not_owner_and_late_preview(ctx):
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=ctx.user.id, user_id=ctx.user.id))
    ctx.bot.delete_message = AsyncMock()
    ids = iter([201, 202])  # prompt and bot's validation message
    ctx.message.answer.side_effect = lambda *args, **kwargs: SimpleNamespace(message_id=next(ids))
    await tools.ask(ctx.callback, state, 'owner_send_video', 'Пришли видео', chat_id=-101, mode='note')
    data = await state.get_data()
    owner_message = SimpleNamespace(from_user=ctx.user, chat=ctx.message.chat, bot=ctx.bot,
                                    message_id=777, text='не видео', answer=ctx.message.answer)
    await tools.receive_input(owner_message, state)
    ctx.callback.message.message_id = 201
    ctx.callback.data = 'owner_tools:cancel_input:' + data['input_token']
    await tools.action(ctx.callback, state, ctx.bot)
    await sender.track_bot_message(state, data['input_token'], SimpleNamespace(message_id=203), ctx.bot, ctx.user.id)
    deleted = {call.kwargs['message_id'] for call in ctx.bot.delete_message.await_args_list}
    assert 202 in deleted and 203 in deleted
    assert 201 not in deleted and 777 not in deleted  # prompt edited to cancellation; owner untouched
    ctx.message.edit_text.assert_awaited_once()
    ctx.bot.send_video_note.assert_not_awaited()
    await storage.close()


@pytest.mark.asyncio
async def test_cleanup_error_does_not_repeat_or_break_publication(ctx):
    record = await prepare(ctx)
    await ctx.db.owner_video_confirmations.update_one({'_id': record['_id']}, {'$set': {'cleanup_ids': [201]}})
    ctx.bot.delete_message = AsyncMock(side_effect=RuntimeError('cannot delete'))
    ctx.callback.data = 'ovideo:send:' + record['_id']
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    ctx.bot.send_video.assert_awaited_once()
    assert (await ctx.db.owner_video_confirmations.find_one({}))['stage'] == 'sent'
    assert any('отправлено' in call.args[0] for call in ctx.message.answer.await_args_list)


@pytest.mark.asyncio
async def test_cancel_confirmation_removes_only_tracked_bot_messages(ctx):
    record = await prepare(ctx)
    await ctx.db.owner_video_confirmations.update_one({'_id': record['_id']},
                                                     {'$set': {'cleanup_ids': [201, 202]}})
    ctx.bot.delete_message = AsyncMock()
    ctx.callback.message.message_id = 203  # confirmation stays as the cancellation notice
    ctx.callback.data = 'ovideo:cancel:' + record['_id']
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    assert {call.kwargs['message_id'] for call in ctx.bot.delete_message.await_args_list} == {201, 202}
    assert all(call.kwargs['chat_id'] == ctx.user.id for call in ctx.bot.delete_message.await_args_list)
    ctx.message.edit_text.assert_awaited_once()
    ctx.bot.send_video.assert_not_awaited()
    ctx.bot.send_video_note.assert_not_awaited()


@pytest.mark.asyncio
async def test_navigation_replaces_only_video_menus_and_keeps_panel(ctx):
    await ctx.db.auth_chats.insert_many([
        {'chat_id': -n, 'title': str(n), 'status': 'approved'} for n in range(101, 111)])
    ctx.bot.delete_message = AsyncMock()
    ctx.message.message_id = 10  # dashboard is retained
    ctx.callback.data = 'ovideo:open'
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    ctx.bot.delete_message.assert_not_awaited()
    ctx.message.message_id = 11  # list page
    ctx.callback.data = 'ovideo:page:1'
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    ctx.message.message_id = 12  # next page
    ctx.callback.data = 'ovideo:chat:-101'
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    ctx.message.message_id = 13  # format selection
    ctx.callback.data = 'ovideo:format:-101:video'
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    assert [c.kwargs['message_id'] for c in ctx.bot.delete_message.await_args_list] == [11, 12, 13]
    assert all(c.kwargs['chat_id'] == ctx.user.id for c in ctx.bot.delete_message.await_args_list)


@pytest.mark.asyncio
async def test_expired_confirmation_can_be_cancelled_and_cleaned_but_not_sent(ctx):
    record = await prepare(ctx)
    await ctx.db.owner_video_confirmations.update_one({'_id': record['_id']}, {'$set': {
        'expires_at': datetime.now(timezone.utc) - timedelta(seconds=1), 'cleanup_ids': [201]}})
    ctx.bot.delete_message = AsyncMock()
    ctx.callback.data = 'ovideo:send:' + record['_id']
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    ctx.bot.send_video.assert_not_awaited()
    ctx.callback.data = 'ovideo:cancel:' + record['_id']
    await sender.action(ctx.callback, ctx.state, ctx.bot)
    ctx.bot.delete_message.assert_awaited_once_with(chat_id=ctx.user.id, message_id=201)
    assert (await ctx.db.owner_video_confirmations.find_one({}))['stage'] == 'cancelled'
