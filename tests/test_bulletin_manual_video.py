import asyncio
import hashlib
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp
import imageio_ffmpeg
import pytest
from mongomock_motor import AsyncMongoMockClient

from bender_queue import VideoQueue
from news_prototype import (bulletin_contract, bulletin_dialogue_audio, bulletin_manual_media as media,
    bulletin_pipeline, news_queue, owner_bulletin, owner_bulletin_manual_video, owner_bulletin_sources,
    telegram_public_source as source)
from tests.test_bulletin_dialogue_audio import payload, wav
from tests.test_bulletin_foundation import response
from tests.test_bulletin_video_worker_integration import source_mp4
from tests.test_owner_drafts import SOURCE


def test_large_video_notice_is_bound_to_exact_post():
    html = b'''<div class="tgme_widget_message" data-post="ulta_games/23320">
      <div class="tgme_widget_message_text js-message_text">A reasonably long caption with source material.</div>
      <a class="tgme_widget_message_video_player" href="https://t.me/ulta_games/23320">
      <i class="tgme_widget_message_video_thumb"></i>
      <div class="tgme_widget_message_video_wrap" style="width:1080px;padding-top:100%"></div>
      <div class="message_video_play js-message_video_play"></div>
      <time class="message_video_duration js-message_video_duration">1:19</time>
      <div class="message_media_not_supported_wrap"><div class="message_media_not_supported">
      <div class="message_media_not_supported_label">Media is too big</div>
      <span class="message_media_view_in_telegram">VIEW IN TELEGRAM</span></div></div></a></div>'''
    # Structure adapted from a saved real Telegram widget for durov/552.
    post = source.parse_widget('https://t.me/ulta_games/23320', html)
    assert post.video_status == 'too_big' and post.video_url is None
    wrong = html.replace(b'href="https://t.me/ulta_games/23320"', b'href="https://t.me/ulta_games/999"')
    assert source.parse_widget('https://t.me/ulta_games/23320', wrong).video_status == 'none'
    reply = html.replace(b'<a class=', b'<div class="tgme_widget_message_reply"><a class=').replace(b'</a></div>', b'</a></div></div>')
    assert source.parse_widget('https://t.me/ulta_games/23320', reply).video_status == 'none'
    unavailable = html.replace(b'Media is too big', b'Please open Telegram to view this post')
    assert source.parse_widget('https://t.me/ulta_games/23320', unavailable).video_status == 'none'


def test_manual_contract_and_private_upload_validation(tmp_path, source_mp4):
    blob = source_mp4.read_bytes(); digest = hashlib.sha256(blob).hexdigest()
    item = payload(); item['video'] = {'section_id': 1, 'manual': True, 'video_sha256': digest}
    assert bulletin_contract.validate_bulletin(item) == item
    for invalid in ({'section_id': 1, 'manual': False, 'video_sha256': digest},
                    {'section_id': 1, 'manual': True, 'video_sha256': '../secret'},
                    {'section_id': 1, 'manual': 1, 'video_sha256': digest},
                    {'section_id': 1, 'manual': True, 'video_sha256': digest, 'path': '/etc/passwd'}):
        with pytest.raises(ValueError): bulletin_contract.validate_bulletin({**item, 'video': invalid})
    with patch.object(source, 'inspect', return_value=(2., 320, 180, True)) as inspect:
        media.save(tmp_path, digest, blob, ffmpeg='ffmpeg', ffprobe='ffprobe')
        media.save(tmp_path, digest, blob, ffmpeg='ffmpeg', ffprobe='ffprobe')
        assert inspect.call_count == 1
        copied = media.acquire(tmp_path, digest, tmp_path / 'copy', ffmpeg='ffmpeg', ffprobe='ffprobe')
        assert copied.read_bytes() == blob
        assert inspect.call_count == 2
        with pytest.raises(ValueError): media.save(tmp_path, 'a'*64, blob, ffmpeg='ffmpeg', ffprobe='ffprobe')
        with pytest.raises(ValueError): media.acquire(tmp_path, '../oops', tmp_path/'x', ffmpeg='ffmpeg', ffprobe='ffprobe')
        (tmp_path/'.bulletin-manual-video'/(digest+'.mp4')).write_bytes(blob[:-1] + b'x')
        with pytest.raises(ValueError): media.acquire(tmp_path, digest, tmp_path/'bad', ffmpeg='ffmpeg', ffprobe='ffprobe')


@pytest.mark.asyncio
async def test_upload_endpoint_auth_and_existing_client(tmp_path, source_mp4):
    from http.server import ThreadingHTTPServer
    from video_prototype import worker, news_jobs
    from news_prototype.news_worker_client import NewsWorkerClient
    root = tmp_path / 'jobs'; shared = worker.JobStore(lambda *args: 'file')
    news = news_jobs.NewsJobs(shared, root, lambda *args: None, run_bulletin=lambda *args: None)
    server = ThreadingHTTPServer(('127.0.0.1', 0), worker.handler_for(shared, 'secret', news))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_port}'; data = source_mp4.read_bytes(); digest = hashlib.sha256(data).hexdigest()
    try:
        with patch('shutil.which', return_value='ffmpeg'), \
             patch.object(source, 'inspect', return_value=(2., 320, 180, True)), \
             patch('news_prototype.news_worker_client.validate_url', return_value=base):
            client = NewsWorkerClient(base, 'secret')
            assert await client.supports_bulletin_manual_video()
            async with aiohttp.ClientSession() as session:
                async with session.post(base + '/news-bulletin-manual/' + digest, data=data) as r:
                    assert r.status == 401
                async with session.post(base + '/news-bulletin-manual/' + '0'*64, data=data,
                                        headers={'Authorization':'Bearer secret'}) as r:
                    assert r.status == 400
            assert await client.upload_bulletin_video(data) == digest
            assert (root/'.bulletin-manual-video'/(digest+'.mp4')).read_bytes() == data
    finally:
        server.shutdown(); server.server_close(); thread.join()


@pytest.mark.asyncio
async def test_confirmed_manual_fragment_renders_and_keeps_queue_compatibility(tmp_path, source_mp4):
    db = AsyncMongoMockClient().test
    await owner_bulletin_sources.create(db, 123, 47, [
        {'url':'https://t.me/ExampleNews/102','title':'Видео'},
        {'url':'https://example.com/story','title':'Новость'}])
    await owner_bulletin_sources.collect(db, 123, 47,
        AsyncMock(return_value={'summary': SOURCE, 'telegram_video_status': 'too_big'}), AsyncMock())
    await owner_bulletin.prepare(db, 123, 47, AsyncMock(side_effect=response))
    item = await owner_bulletin.get(db, 123, 47)
    blob = source_mp4.read_bytes(); digest = hashlib.sha256(blob).hexdigest()
    assert await owner_bulletin_manual_video.attach(db, 123, 47, item['revision'], 1, digest) == 'selected'
    assert await owner_bulletin_manual_video.attach(db, 123, 47, item['revision'], 1, digest) == 'stale'
    item = await owner_bulletin.get(db, 123, 47)
    script = await owner_bulletin.approved_payload(db, 123, 47, item['revision'])
    assert script['video'] == {'section_id': 1, 'manual': True, 'video_sha256': digest}
    queue = VideoQueue(db.media, owner='test'); await queue.ensure(); await queue.acquire()
    await news_queue.enqueue_bulletin(db.jobs, 'manual', script)
    client = SimpleNamespace(supports_bulletins=AsyncMock(return_value=True),
        supports_bulletin_manual_video=AsyncMock(return_value=False), supports_bulletin_video=AsyncMock(return_value=True),
        submit_bulletin=AsyncMock(return_value={'status':'running'}))
    await news_queue.run_idle_once(queue, db.jobs, client)
    client.submit_bulletin.assert_not_awaited()
    client.supports_bulletin_manual_video.return_value = True
    await news_queue.run_idle_once(queue, db.jobs, client)
    client.submit_bulletin.assert_awaited_once()
    root = tmp_path / 'news-jobs'; root.mkdir(); job = root / 'job'; job.mkdir()
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    with patch.object(source, 'inspect', return_value=(2., 320, 180, True)), \
         patch('shutil.which', return_value='ffprobe'), \
         patch.object(bulletin_dialogue_audio, 'request_one', return_value=(wav(), 1.0)) as tts:
        media.save(root, digest, blob, ffmpeg=ffmpeg, ffprobe='ffprobe')
        output = bulletin_pipeline.run(script, job/'work', ffmpeg, ['fake'])
        assert tts.call_count == 2
        assert output.stat().st_size > 1000
        subprocess.run([ffmpeg, '-v', 'error', '-xerror', '-i', str(output), '-f', 'null', '-'], check=True)
    # Missing fragment fails before any TTS.
    (root/'.bulletin-manual-video'/(digest+'.mp4')).unlink()
    job2 = root / 'job2'; job2.mkdir()
    with patch('shutil.which', return_value='ffprobe'), \
         patch.object(bulletin_dialogue_audio, 'request_one') as tts:
        with pytest.raises((ValueError, OSError)):
            bulletin_pipeline.run(script, job2/'work', ffmpeg, ['fake'])
        tts.assert_not_called()


@pytest.mark.asyncio
async def test_manual_input_confirms_exact_clip_before_selection():
    import os
    os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
    os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
    from handlers import owner_tools_panel as tools, owner_bulletin_panel as panel
    from news_prototype import owner_trial, news_worker_client
    db = AsyncMongoMockClient().test
    fake_db = SimpleNamespace(db=db)
    owner = tools.ADMIN_ID
    await owner_bulletin_sources.create(db, owner, 47, [
        {'url':'https://t.me/ExampleNews/102','title':'Видео'}, {'url':'https://example.com/other','title':'Новость'}])
    await owner_bulletin_sources.collect(db, owner, 47, AsyncMock(return_value={'summary':SOURCE,
                                         'telegram_video_status':'too_big'}), AsyncMock())
    await owner_bulletin.prepare(db, owner, 47, AsyncMock(side_effect=response))
    item = await owner_bulletin.get(db, owner, 47)
    raw = b'\x00\x00\x00\x18ftypisom' + b'x'*40
    digest = hashlib.sha256(raw).hexdigest()
    fake_client = SimpleNamespace(supports_bulletin_manual_video=AsyncMock(return_value=True),
                                  upload_bulletin_video=AsyncMock(return_value=digest))
    async def download(_file_id, destination): destination.write(raw)
    message = SimpleNamespace(from_user=SimpleNamespace(id=owner), chat=SimpleNamespace(id=owner,type='private'),
        video=SimpleNamespace(file_id='opaque',file_size=len(raw)), document=None,
        bot=SimpleNamespace(download=AsyncMock(side_effect=download)), answer=AsyncMock())
    state = SimpleNamespace(get_data=AsyncMock(return_value={'action':'bulletin_manual_video','number':47,
        'section_id':1, 'revision':item['revision']}), clear=AsyncMock())
    cancelled_state = SimpleNamespace(get_data=AsyncMock(side_effect=[
        {'action':'bulletin_manual_video', 'number':47, 'section_id':1,
         'revision':item['revision'], 'input_token':'old'},
        {'action':'another_input', 'input_token':'new'}]), clear=AsyncMock())
    with patch.object(tools, 'db', fake_db), patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)), \
         patch.object(news_worker_client, 'NewsWorkerClient', return_value=fake_client):
        await tools.receive_bulletin_video(message, cancelled_state)
        cancelled_state.clear.assert_not_awaited()
        assert await db.owner_panel_confirmations.count_documents({}) == 0
        fake_client.upload_bulletin_video.reset_mock()
        await tools.receive_bulletin_video(message, state)
    fake_client.upload_bulletin_video.assert_awaited_once_with(raw)
    confirm = await db.owner_panel_confirmations.find_one({'action':'use_manual_bulletin_video'})
    assert confirm['data']['digest'] == digest
    assert 'video' not in (await owner_bulletin.approved_payload(db, owner, 47, item['revision']))
    with patch.object(tools, 'db', fake_db), patch.object(panel, 'db', fake_db), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)):
        callback = SimpleNamespace(from_user=SimpleNamespace(id=owner), data='owner_tools:run:' + confirm['_id'],
            message=SimpleNamespace(chat=message.chat, message_id=77, from_user=message.from_user,
            edit_text=AsyncMock(), answer=AsyncMock(return_value=SimpleNamespace(message_id=78))), answer=AsyncMock())
        await tools.action(callback, state, None)
        await tools.action(callback, state, None)
    updated = await owner_bulletin.get(db, owner, 47)
    assert (await owner_bulletin.approved_payload(db, owner, 47, updated['revision']))['video']['video_sha256'] == digest
    assert await db.news_owner_trial_jobs.count_documents({}) == 0


@pytest.mark.asyncio
async def test_manual_input_rejects_large_or_wrong_owner_without_upload():
    import os
    os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
    os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
    from handlers import owner_tools_panel as tools
    owner = tools.ADMIN_ID
    message = SimpleNamespace(from_user=SimpleNamespace(id=owner), chat=SimpleNamespace(id=owner,type='private'),
        video=SimpleNamespace(file_id='opaque',file_size=media.MAX_BYTES + 1), document=None,
        bot=SimpleNamespace(download=AsyncMock()), answer=AsyncMock())
    state = SimpleNamespace(get_data=AsyncMock(return_value={'action':'bulletin_manual_video'}), clear=AsyncMock())
    await tools.receive_bulletin_video(message, state)
    message.bot.download.assert_not_awaited()
    assert '20 МБ' in message.answer.await_args.args[0]
    message.from_user.id += 1
    await tools.receive_bulletin_video(message, state)
    message.bot.download.assert_not_awaited()


def test_dispatcher_receives_video_after_owner_button():
    import os, sys
    env = dict(os.environ, MONGO_URI='mongodb://localhost:27017', TELEGRAM_TOKEN='123456:TESTTOKEN')
    code = r'''
import asyncio, hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiogram import Bot
from aiogram.types import Update, Message
from aiogram.methods import SendMessage, SendPhoto
from mongomock_motor import AsyncMongoMockClient
from app_setup import configure_dispatcher
from config import ADMIN_ID
from handlers import owner_bulletin_panel as panel, owner_tools_panel as tools, logger
from news_prototype import owner_bulletin, owner_bulletin_sources, owner_trial, news_worker_client
from tests.test_bulletin_foundation import response
from tests.test_owner_drafts import SOURCE

async def run():
    db=AsyncMongoMockClient().test
    fake=SimpleNamespace(db=db, settings=db.settings, messages=db.messages)
    await owner_bulletin_sources.create(db,ADMIN_ID,47,[{'url':'https://t.me/ExampleNews/102','title':'Видео'},
                                                      {'url':'https://example.com/2','title':'Новость'}])
    await owner_bulletin_sources.collect(db,ADMIN_ID,47,AsyncMock(return_value={'summary':SOURCE,
                                         'telegram_video_status':'too_big'}),AsyncMock())
    await owner_bulletin.prepare(db,ADMIN_ID,47,AsyncMock(side_effect=response))
    item=await owner_bulletin.get(db,ADMIN_ID,47)
    bot=Bot('123456:TESTTOKEN'); seq=500
    async def request(bot_arg,method,**kwargs):
        nonlocal seq
        if isinstance(method,(SendMessage,SendPhoto)):
            seq+=1
            return Message.model_validate({'message_id':seq,'date':1760000000,
               'chat':{'id':ADMIN_ID,'type':'private'},'from':{'id':123456,'is_bot':True,'first_name':'Bot'},
               'text':'result'},context={'bot':bot_arg})
        return True
    raw=b'\0\0\0\x18ftypisom'+b'x'*40; digest=hashlib.sha256(raw).hexdigest()
    async def download(_file, destination): destination.write(raw)
    client=SimpleNamespace(supports_bulletin_manual_video=AsyncMock(return_value=True),
                           upload_bulletin_video=AsyncMock(return_value=digest))
    dp=configure_dispatcher()
    user={'id':ADMIN_ID,'is_bot':False,'first_name':'Owner'}; chat={'id':ADMIN_ID,'type':'private'}
    async def cb(i,data,mid):
        update=Update.model_validate({'update_id':i,'callback_query':{'id':str(i),'chat_instance':'owner',
            'from':user,'data':data,'message':{'message_id':mid,'date':1760000000,'chat':chat,
            'from':{'id':123456,'is_bot':True,'first_name':'Bot'},'text':'panel'}}})
        await asyncio.wait_for(dp.feed_update(bot,update),5)
    with patch.object(panel,'db',fake), patch.object(tools,'db',fake), patch.object(logger,'db',fake), \
         patch.object(owner_trial,'drafts_enabled',AsyncMock(return_value=True)), \
         patch.object(news_worker_client,'NewsWorkerClient',return_value=client), \
         patch.object(bot,'download',AsyncMock(side_effect=download)), \
         patch.object(bot.session,'make_request',AsyncMock(side_effect=request)):
        msg=Message.model_validate({'message_id':321,'date':1760000000,'chat':chat,
             'from':{'id':123456,'is_bot':True,'first_name':'Bot'},'text':'panel'},context={'bot':bot})
        await panel.show_section(msg,ADMIN_ID,item,item['sections'][0])
        card=await db.owner_bulletin_cards.find_one({'section_id':1})
        await cb(1,'nb:manualvideo:'+card['_id'],card['message_id'])
        update=Update.model_validate({'update_id':2,'message':{'message_id':402,'date':1760000000,
            'chat':chat,'from':user,'video':{'file_id':'file','file_unique_id':'unique',
            'width':320,'height':180,'duration':2,'file_size':len(raw)}}})
        await asyncio.wait_for(dp.feed_update(bot,update),5)
        confirm=await db.owner_panel_confirmations.find_one({'action':'use_manual_bulletin_video'})
        assert confirm and confirm['data']['digest']==digest
        assert 'video' not in (await owner_bulletin.approved_payload(db,ADMIN_ID,47,item['revision']))
        await cb(3,'owner_tools:run:'+confirm['_id'],900)
        final=await owner_bulletin.get(db,ADMIN_ID,47)
        assert (await owner_bulletin.approved_payload(db,ADMIN_ID,47,final['revision']))['video']['video_sha256']==digest
        assert await db.news_owner_trial_jobs.count_documents({})==0
    await bot.session.close()
asyncio.run(run())
'''
    result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_manual_video_child_worker_end_to_end_without_network(tmp_path, source_mp4):
    import json, os, uuid
    from video_prototype import news_jobs
    root = tmp_path/'news-jobs'; root.mkdir()
    request_id = str(uuid.uuid4()); job = root/request_id; job.mkdir()
    data = source_mp4.read_bytes(); digest = hashlib.sha256(data).hexdigest()
    with patch.object(source, 'inspect', return_value=(2., 320, 180, True)):
        media.save(root, digest, data, ffmpeg='ffmpeg', ffprobe='ffprobe')
    script = payload(); script['video'] = {'section_id':1, 'manual':True, 'video_sha256':digest}
    (job/'request.json').write_text(json.dumps({'bulletin':script}, ensure_ascii=False))
    hooks = tmp_path/'hooks'; hooks.mkdir()
    (hooks/'sitecustomize.py').write_text('''
import io, wave, socket
from news_prototype import telegram_public_source as source, bulletin_dialogue_audio as audio
def blocked(*args, **kwargs): raise AssertionError('network disabled')
socket.socket.connect = blocked
def tts(key, body, timeout):
    out=io.BytesIO()
    with wave.open(out, 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(24000); f.writeframes(b'\\x01\\x00' * 24000)
    return out.getvalue(), 1.
audio.request_one=tts
source.inspect=lambda *args: (2.,320,180,True)
''')
    binary = tmp_path/'bin'; binary.mkdir()
    (binary/'ffmpeg').symlink_to(imageio_ffmpeg.get_ffmpeg_exe())
    (binary/'ffprobe').symlink_to(imageio_ffmpeg.get_ffmpeg_exe())
    repo = Path(__file__).resolve().parent.parent
    env = {'PYTHONPATH':str(hooks)+os.pathsep+str(repo), 'PATH':str(binary)+os.pathsep+os.environ.get('PATH',''),
           'GEMINI_TTS_KEYS':'test-only'}
    with patch.dict(os.environ, env):
        news_jobs.bulletin_runner(repo)(request_id, job)
    assert json.loads((job/'work'/'status.json').read_text())['stage'] == 'ready_for_review'
    assert (job/'work'/'video.mp4').stat().st_size > 1000
    assert (job/'worker.log').read_text().count('Gemini TTS готов') == 2


def test_invalid_media_and_full_cache_leave_no_partial_files(tmp_path, source_mp4):
    data = source_mp4.read_bytes(); digest = hashlib.sha256(data).hexdigest()
    with patch.object(source.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'ffprobe')):
        with pytest.raises(source.SourceError):
            media.save(tmp_path, digest, data, ffmpeg='ffmpeg', ffprobe='ffprobe')
    assert list((tmp_path/'.bulletin-manual-video').iterdir()) == []
    with patch.object(media, 'MAX_STORE_BYTES', len(data)-1):
        with pytest.raises(ValueError, match='заполнено'):
            media.save(tmp_path, digest, data, ffmpeg='ffmpeg', ffprobe='ffprobe')
    with pytest.raises(ValueError):
        media.save(tmp_path, hashlib.sha256(b'not a real video').hexdigest(), b'not a real video',
                   ffmpeg='ffmpeg', ffprobe='ffprobe')


@pytest.mark.asyncio
async def test_edit_warns_and_unmodified_edit_retains_video_and_render_warns():
    import os
    os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
    os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
    from handlers import owner_tools_panel as tools, owner_bulletin_panel as panel
    from news_prototype import owner_trial
    db = AsyncMongoMockClient().test
    owner = tools.ADMIN_ID
    await owner_bulletin_sources.create(db, owner, 47, [
        {'url': 'https://t.me/ExampleNews/102', 'title': 'Видео'},
        {'url': 'https://example.com/story', 'title': 'Другая новость'}])
    await owner_bulletin_sources.collect(db, owner, 47,
        AsyncMock(return_value={'summary': SOURCE, 'telegram_video_status': 'too_big'}), AsyncMock())
    await owner_bulletin.prepare(db, owner, 47, AsyncMock(side_effect=response))
    item = await owner_bulletin.get(db, owner, 47)
    assert await owner_bulletin_manual_video.attach(db, owner, 47, item['revision'], 1, 'a'*64) == 'selected'
    selected = await owner_bulletin.get(db, owner, 47)
    unchanged = owner_bulletin.editable_section(selected['sections'][1])
    assert await owner_bulletin.change_section(db, owner, 47, selected['revision'], 2, text=unchanged) == 'unchanged'
    assert await owner_bulletin.get(db, owner, 47) == selected
    message = SimpleNamespace(from_user=SimpleNamespace(id=owner), chat=SimpleNamespace(id=owner, type='private'),
        message_id=123, text=unchanged.replace(selected['sections'][1]['ticker'], 'Исправлено'),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=999)), answer_photo=AsyncMock())
    state = SimpleNamespace(get_data=AsyncMock(return_value={'action': 'bulletin_edit', 'number': 47,
        'section_id': 2, 'revision': selected['revision']}), clear=AsyncMock())
    fake = SimpleNamespace(db=db)
    with patch.object(tools, 'db', fake), patch.object(panel, 'db', fake), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)), \
         patch.object(owner_trial, 'runtime_enabled', AsyncMock(return_value=True)):
        await tools.receive_input(message, state)
        texts = [c.args[0] for c in message.answer.await_args_list]
        assert any('сняты выбранные видео для новостей 1' in t and 'повторно' in t for t in texts)
        item = await owner_bulletin.get(db, owner, 47)
        assert 'video' not in item['sections'][0]
        await panel.show_review(message, owner, item)
        card = await db.owner_bulletin_actions.find_one({'action': 'render'})
        callback = SimpleNamespace(from_user=message.from_user, message=message,
            data='nb:render:' + card['_id'], answer=AsyncMock())
        message.message_id = card['message_id']
        await panel.callback_action(callback, state)
        assert 'НЕТ выбранных видео' in message.answer.await_args.args[0]
        assert await db.news_owner_trial_jobs.count_documents({}) == 0


@pytest.mark.asyncio
async def test_verified_channel_name_survives_collection_and_revalidation():
    from news_prototype import bulletin_editor
    import json
    db = AsyncMongoMockClient().test
    await owner_bulletin_sources.create(db, 123, 47, [
        {'url': 'https://t.me/ExampleNews/102', 'title': 'Видео'},
        {'url': 'https://example.com/other', 'title': 'Сайт'}])
    async def fetch(url):
        return {'summary': SOURCE, 'telegram_channel_name': 'Проверенный канал'}
    await owner_bulletin_sources.collect(db, 123, 47, fetch, AsyncMock())
    source_item = await owner_bulletin.get(db, 123, 47)
    assert source_item['stories'][0]['telegram_channel_name'] == 'Проверенный канал'
    assert 'telegram_channel_name' not in source_item['stories'][1]
    assert await owner_bulletin.prepare(db, 123, 47, AsyncMock(side_effect=response)) == 'ready_for_review'
    item = await owner_bulletin.get(db, 123, 47)
    first = item['sections'][0]['turns'][0]['text']
    assert first.startswith('В телеграм-канале «Проверенный канал» поделились новостью.')
    assert first.count('Проверенный канал') == 1
    assert (await owner_bulletin.approved_payload(db, 123, 47, item['revision']))['sections'][0]['turns'][0]['text'] == first
    checked = bulletin_editor.parse_draft(json.dumps({'decision': 'draft', 'sections': item['sections']}, ensure_ascii=False), item['stories'])
    assert checked['sections'][0]['turns'][0]['text'] == first
