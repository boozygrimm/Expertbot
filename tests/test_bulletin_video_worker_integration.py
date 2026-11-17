"""Owner-approved source through the real bulletin path; no external services."""
import hashlib
from pathlib import Path
import shutil
import subprocess
from unittest.mock import AsyncMock, patch

import imageio_ffmpeg
import pytest
from mongomock_motor import AsyncMongoMockClient

from news_prototype import (bulletin_dialogue_audio, bulletin_pipeline, bulletin_timing,
    bulletin_video_candidate, bulletin_video_pipeline, news_queue, owner_bulletin,
    owner_bulletin_admission, owner_bulletin_sources, owner_bulletin_video_review,
    owner_trial, podcast_video_trial, telegram_public_source)
from tests.test_bulletin_dialogue_audio import wav
from tests.test_bulletin_foundation import response
from tests.test_owner_drafts import SOURCE


@pytest.fixture
def source_mp4(tmp_path):
    path = tmp_path / 'original.mp4'
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=24000', '-t', '2',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(path)], check=True)
    return path


async def setup_review(db, original):
    owner, number = 123, 47
    await owner_bulletin_sources.create(db, owner, number, [
        {'url': 'https://t.me/ExampleNews/102', 'title': 'Видео'},
        {'url': 'https://example.com/other', 'title': 'Вторая новость'}])
    async def fetch(url):
        return {'summary': SOURCE, 'telegram_video_status': 'available' if 't.me' in url else 'none'}
    assert await owner_bulletin_sources.collect(db, owner, number, fetch, AsyncMock()) == 'new'
    assert await owner_bulletin.prepare(db, owner, number, AsyncMock(side_effect=response)) == 'ready_for_review'
    item = await owner_bulletin.get(db, owner, number)
    async def acquire(db, oid, no, rev, section, folder):
        folder.mkdir()
        path = folder / 'source.mp4'; shutil.copyfile(original, path)
        return bulletin_video_candidate.Candidate(oid, no, rev, section, 'https://t.me/ExampleNews/102',
            hashlib.sha256(SOURCE.strip().encode()).hexdigest(), hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_size, 2., 320, 180, True, path)
    return owner, number, item, acquire


@pytest.mark.asyncio
async def test_owner_approval_frozen_job_and_actual_video_pipeline(tmp_path, source_mp4):
    db = AsyncMongoMockClient().test
    owner, number, item, acquire = await setup_review(db, source_mp4)
    rev = item['revision']
    assert await owner_bulletin_video_review.approve_for_bulletin(db, owner, number, rev, 1) == 'not_reviewed'
    with patch.object(bulletin_video_candidate, 'acquire', side_effect=acquire):
        assert await owner_bulletin_video_review.deliver(db, owner, number, rev, 1, AsyncMock(return_value=55)) == 'sent'
    # Viewing is not authorization.
    assert 'video' not in await owner_bulletin.approved_payload(db, owner, number, rev)
    assert await owner_bulletin_video_review.approve_for_bulletin(db, owner, number, rev, 1) == 'selected'
    selected = await owner_bulletin.get(db, owner, number)
    assert selected['revision'] != rev
    payload = await owner_bulletin.approved_payload(db, owner, number, selected['revision'])
    assert payload['video']['video_sha256'] == hashlib.sha256(source_mp4.read_bytes()).hexdigest()
    assert payload['video']['section_id'] == 1
    with patch.object(owner_trial, 'runtime_enabled', AsyncMock(return_value=True)):
        assert await owner_bulletin_admission.approve(db, owner, number, selected['revision']) == 'queued'
    job = await db.news_owner_trial_jobs.find_one({})
    assert job['bulletin'] == payload

    post = telegram_public_source.Post('https://t.me/ExampleNews/102', 'ExampleNews/102', SOURCE.strip(),
                                      'available', 'https://example.invalid/private')
    def download(_post, folder, **_kwargs):
        folder.mkdir()
        local = folder / 'source.mp4'; shutil.copyfile(source_mp4, local)
        return telegram_public_source.Video(local, hashlib.sha256(local.read_bytes()).hexdigest(),
                                           local.stat().st_size, 2., 320, 180, True)
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    with patch.object(bulletin_pipeline.shutil, 'which', return_value='test-ffprobe'), \
         patch.object(telegram_public_source, 'fetch_post', return_value=post) as fetch, \
         patch.object(telegram_public_source, 'download_video', side_effect=download) as acquire_media, \
         patch.object(telegram_public_source, 'inspect', return_value=(2., 320, 180, True)), \
         patch.object(bulletin_dialogue_audio, 'request_one', return_value=(wav(), 1.0)) as tts, \
         patch.object(bulletin_video_pipeline, 'render', wraps=bulletin_video_pipeline.render) as render:
        output = bulletin_pipeline.run(job['bulletin'], tmp_path / 'work', ff, ['fake'])
    assert output.stat().st_size > 1000
    assert fetch.call_count == acquire_media.call_count == 1
    assert tts.call_count == 2 and render.call_count == 1
    # Full original first news preserved, then added silence for an end-of-story cut.
    _, raw = podcast_video_trial.read_wav(tmp_path / 'work' / '1-raw.wav')
    _, normalized = podcast_video_trial.read_wav(tmp_path / 'work' / '1-normalized.wav')
    assert normalized[:len(raw)] == raw and len(normalized) == len(raw) + 12000
    assert not any(normalized[len(raw):])
    subprocess.run([ff, '-v', 'error', '-xerror', '-i', str(output), '-f', 'null', '-'], check=True)


@pytest.mark.asyncio
async def test_source_or_revision_change_fails_closed_before_tts(tmp_path, source_mp4):
    db = AsyncMongoMockClient().test
    owner, number, item, acquire = await setup_review(db, source_mp4)
    with patch.object(bulletin_video_candidate, 'acquire', side_effect=acquire):
        assert await owner_bulletin_video_review.deliver(db, owner, number, item['revision'], 1, AsyncMock(return_value=55)) == 'sent'
    assert await owner_bulletin_video_review.approve_for_bulletin(db, owner, number, item['revision'], 1) == 'selected'
    selected = await owner_bulletin.get(db, owner, number)
    payload = await owner_bulletin.approved_payload(db, owner, number, selected['revision'])
    post = telegram_public_source.Post('https://t.me/ExampleNews/102', 'ExampleNews/102', 'changed', 'available', 'unused')
    with patch.object(bulletin_pipeline.shutil, 'which', return_value='fake'), \
         patch.object(telegram_public_source, 'fetch_post', return_value=post), \
         patch.object(telegram_public_source, 'download_video') as download, \
         patch.object(bulletin_dialogue_audio, 'request_one') as tts:
        with pytest.raises(ValueError):
            bulletin_pipeline.run(payload, tmp_path / 'work', imageio_ffmpeg.get_ffmpeg_exe(), ['fake'])
        download.assert_not_called(); tts.assert_not_called()
        assert not (tmp_path / 'work').exists()
    section = selected['sections'][0]
    text = owner_bulletin.editable_section(section).replace(section['ticker'], 'Новый заголовок')
    assert await owner_bulletin.change_section(db, owner, number, selected['revision'], 1, text=text) == 'saved'
    updated = await owner_bulletin.get(db, owner, number)
    assert 'video' not in await owner_bulletin.approved_payload(db, owner, number, updated['revision'])
    assert await owner_bulletin_video_review.approve_for_bulletin(db, owner, number, item['revision'], 1) == 'stale'


@pytest.mark.asyncio
async def test_queue_will_not_send_video_to_old_worker():
    from bender_queue import VideoQueue
    from tests.test_bulletin_dialogue_audio import payload as bulletin_payload
    db = AsyncMongoMockClient().test
    queue = VideoQueue(db.media, owner='test'); await queue.ensure(); await queue.acquire()
    data = bulletin_payload(2)
    data['video'] = {'section_id': 1, 'post_url': 'https://t.me/ExampleNews/102',
                     'caption_sha256': 'a' * 64, 'video_sha256': 'b' * 64}
    await news_queue.enqueue_bulletin(db.jobs, 'approved', data)
    from types import SimpleNamespace
    client = SimpleNamespace(supports_bulletins=AsyncMock(return_value=True),
        supports_bulletin_video=AsyncMock(return_value=False), submit_bulletin=AsyncMock())
    await news_queue.run_idle_once(queue, db.jobs, client)
    client.submit_bulletin.assert_not_awaited()
    assert (await db.jobs.find_one({}))['stage'] == 'queued'
    client.supports_bulletin_video.return_value = True
    client.submit_bulletin.return_value = {'status': 'running'}
    await news_queue.run_idle_once(queue, db.jobs, client)
    client.submit_bulletin.assert_awaited_once()
    assert (await db.jobs.find_one({}))['stage'] == 'running'


def test_video_added_to_duration_limit_never_cuts_speech(tmp_path, source_mp4):
    from tests.test_bulletin_dialogue_audio import payload as dialogue_payload
    data = dialogue_payload(2)
    data['video'] = {'section_id': 1, 'post_url': 'https://t.me/ExampleNews/102',
                     'caption_sha256': 'a' * 64, 'video_sha256': 'b' * 64}
    with patch.object(bulletin_pipeline, '_acquire_reviewed_video', return_value=(source_mp4, 'fake-ffprobe')), \
         patch.object(bulletin_dialogue_audio, 'request_one', return_value=(wav(145), 145.)) as tts, \
         patch.object(telegram_public_source, 'inspect') as inspect, \
         patch.object(bulletin_video_pipeline.bulletin_layered_video, 'render') as renderer:
        with pytest.raises(bulletin_timing.BulletinTooLong):
            bulletin_pipeline.run(data, tmp_path / 'work', imageio_ffmpeg.get_ffmpeg_exe(), ['fake'])
    assert tts.call_count == 2
    inspect.assert_not_called(); renderer.assert_not_called()
    assert not (tmp_path / 'work' / 'video.mp4').exists()
    assert not (tmp_path / 'work' / 'video.partial.mp4').exists()
    assert 'audio_too_long' in (tmp_path / 'work' / 'status.json').read_text()
    _, speech = podcast_video_trial.read_wav(tmp_path / 'work' / '2-normalized.wav')
    assert len(speech) == 145 * 24000


def test_contract_rejects_untrusted_video_identifiers():
    from news_prototype import bulletin_contract
    from tests.test_bulletin_dialogue_audio import payload as dialogue_payload
    data = dialogue_payload(2)
    base = {'section_id': 1, 'post_url': 'https://t.me/ExampleNews/102',
            'caption_sha256': 'a' * 64, 'video_sha256': 'b' * 64}
    for changed in ({'section_id': 3}, {'section_id': True}, {'post_url': 'http://127.0.0.1/file'},
                    {'post_url': 'https://t.me/ExampleNews/102?query=ignored'}, {'video_sha256': 'B' * 64},
                    {'caption_sha256': 'a'}, {'path': '/etc/passwd'}):
        data['video'] = {**base, **changed}
        with pytest.raises(ValueError):
            bulletin_contract.validate_bulletin(data)


@pytest.mark.parametrize('has_remarks', [False, True])
def test_actual_subprocess_worker_video_with_external_network_blocked(tmp_path, source_mp4, has_remarks):
    import json
    import os
    import uuid
    from video_prototype import news_jobs
    from tests.test_bulletin_dialogue_audio import payload as dialogue_payload
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    raw_caption = 'Синтетический пост'
    data = dialogue_payload(2)
    data['video'] = {'section_id': 1, 'post_url': 'https://t.me/ExampleNews/102',
        'caption_sha256': hashlib.sha256(raw_caption.encode()).hexdigest(),
        'video_sha256': hashlib.sha256(source_mp4.read_bytes()).hexdigest()}
    if has_remarks:
        data['video']['remarks'] = [{'speaker': 'Бендер', 'text': 'Смотрим!'},
                                    {'speaker': 'Киберсенсей', 'text': 'Давай.'}]
    job = str(uuid.uuid4())
    folder = tmp_path / job; folder.mkdir()
    (folder / 'request.json').write_text(json.dumps({'bulletin': data}, ensure_ascii=False))
    hooks = tmp_path / 'hooks'; hooks.mkdir()
    (hooks / 'sitecustomize.py').write_text(f'''
import hashlib, io, shutil, socket, wave
from pathlib import Path
from news_prototype import telegram_public_source as source, bulletin_dialogue_audio as audio
def blocked(*args, **kwargs): raise AssertionError('network disabled in subprocess')
socket.socket.connect = blocked
def fake_tts(key, body, timeout):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
        wav.writeframes(b'\\x01\\x00' * 24000)
    return buffer.getvalue(), 1.
audio.request_one = fake_tts
def fake_post(url):
    return source.Post(url, 'ExampleNews/102', {raw_caption!r}, 'available', 'unused')
def fake_download(post, folder, **kwargs):
    folder.mkdir()
    path = folder / 'source.mp4'
    shutil.copyfile({str(source_mp4)!r}, path)
    return source.Video(path, hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size, 2., 320, 180, True)
source.fetch_post = fake_post
source.download_video = fake_download
source.inspect = lambda *args: (2., 320, 180, True)
''')
    binary = tmp_path / 'bin'; binary.mkdir()
    (binary / 'ffmpeg').symlink_to(ff)
    (binary / 'ffprobe').symlink_to(ff)  # inspect is stubbed, not an actual ffprobe.
    repo = Path(__file__).resolve().parent.parent
    env = {'PYTHONPATH': str(hooks) + os.pathsep + str(repo),
           'PATH': str(binary) + os.pathsep + os.environ.get('PATH', ''), 'GEMINI_TTS_KEYS': 'test-only'}
    with patch.dict(os.environ, env):
        news_jobs.bulletin_runner(repo)(job, folder)
    assert json.loads((folder / 'work' / 'status.json').read_text())['stage'] == 'ready_for_review'
    assert (folder / 'work' / 'video.mp4').stat().st_size > 1000
    assert (folder / 'worker.log').read_text().count('Gemini TTS готов') == (3 if has_remarks else 2)
    assert (folder / 'work' / 'video-remarks' / 'remarks.wav').exists() == has_remarks


def test_changed_video_bytes_refused_before_any_tts(tmp_path, source_mp4):
    from tests.test_bulletin_dialogue_audio import payload as dialogue_payload
    caption = 'Текст'
    data = dialogue_payload(2)
    data['video'] = {'section_id': 1, 'post_url': 'https://t.me/ExampleNews/102',
        'caption_sha256': hashlib.sha256(caption.encode()).hexdigest(), 'video_sha256': '0' * 64}
    post = telegram_public_source.Post(data['video']['post_url'], 'ExampleNews/102', caption, 'available', 'unused')
    def download(_post, folder, **kwargs):
        folder.mkdir()
        path = folder / 'source.mp4'; shutil.copyfile(source_mp4, path)
        return telegram_public_source.Video(path, hashlib.sha256(path.read_bytes()).hexdigest(),
                                           path.stat().st_size, 2., 320, 180, True)
    with patch.object(bulletin_pipeline.shutil, 'which', return_value='ffprobe'), \
         patch.object(telegram_public_source, 'fetch_post', return_value=post), \
         patch.object(telegram_public_source, 'download_video', side_effect=download), \
         patch.object(bulletin_dialogue_audio, 'request_one') as tts:
        with pytest.raises(ValueError, match='изменилось'):
            bulletin_pipeline.run(data, tmp_path / 'work', imageio_ffmpeg.get_ffmpeg_exe(), ['fake'])
        tts.assert_not_called()
        assert not (tmp_path / 'work').exists()
