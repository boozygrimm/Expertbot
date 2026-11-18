"""Multi-video path, exclusively synthetic: no external providers or publication."""
from array import array
from copy import deepcopy
import hashlib
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import imageio_ffmpeg
import pytest
from mongomock_motor import AsyncMongoMockClient

from bender_queue import VideoQueue
from news_prototype import (bulletin_contract, bulletin_dialogue_audio, bulletin_pipeline,
    bulletin_timing, bulletin_video_candidate, bulletin_video_pipeline, news_queue,
    owner_bulletin, owner_bulletin_admission, owner_bulletin_sources,
    owner_bulletin_video_review, owner_trial, podcast_video_trial)
from tests.test_bulletin_dialogue_audio import payload, wav
from tests.test_bulletin_foundation import response
from tests.test_bulletin_video_worker_integration import source_mp4
from tests.test_owner_drafts import SOURCE


def multi_data(count=2):
    data = payload(count)
    data['videos'] = [dict(section_id=i, post_url=f'https://t.me/ExampleNews/{100+i}',
                           caption_sha256='a'*64, video_sha256='b'*64, cut_after=1,
                           remarks=[{'speaker':'Бендер','text':'Смотрим!'},
                                    {'speaker':'Киберсенсей','text':'Давай.'}]) for i in (1, count)]
    return data


def test_multi_contract_is_ordered_unique_and_backward_compatible():
    data = multi_data()
    assert bulletin_contract.validate_bulletin(data) == data
    for bad in ([], [data['videos'][0]], list(reversed(data['videos'])),
                [data['videos'][0], data['videos'][0]]):
        with pytest.raises(ValueError):
            bulletin_contract.validate_bulletin({**data, 'videos': bad})
    with pytest.raises(ValueError):
        bulletin_contract.validate_bulletin({**data, 'video': data['videos'][0]})
    single = {'sections': data['sections'], 'video': data['videos'][0]}
    assert bulletin_contract.validate_bulletin(single) == single


@pytest.mark.asyncio
async def test_independent_review_revision_queue_and_edits(tmp_path, source_mp4):
    db = AsyncMongoMockClient().test
    owner, number = 123, 47
    await owner_bulletin_sources.create(db, owner, number, [
        {'url': f'https://t.me/ExampleNews/{100+i}', 'title': f'Новость {i}'} for i in (1, 2)])
    async def fetch(url): return {'summary': SOURCE, 'telegram_video_status': 'available'}
    assert await owner_bulletin_sources.collect(db, owner, number, fetch, AsyncMock()) == 'new'
    assert await owner_bulletin.prepare(db, owner, number, AsyncMock(side_effect=response)) == 'ready_for_review'
    item = await owner_bulletin.get(db, owner, number)
    async def acquire(db, oid, no, rev, section, folder):
        folder.mkdir(); path = folder / 'source.mp4'; shutil.copyfile(source_mp4, path)
        return bulletin_video_candidate.Candidate(oid, no, rev, section, f'https://t.me/ExampleNews/{100+section}',
            hashlib.sha256(SOURCE.strip().encode()).hexdigest(), hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_size, 2., 320, 180, True, path)
    old = item['revision']
    with patch.object(bulletin_video_candidate, 'acquire', side_effect=acquire):
        assert await owner_bulletin_video_review.deliver(db, owner, number, old, 1, AsyncMock(return_value=1)) == 'sent'
        assert await owner_bulletin_video_review.deliver(db, owner, number, old, 2, AsyncMock(return_value=2)) == 'sent'
        assert await owner_bulletin_video_review.approve_for_bulletin(db, owner, number, old, 1) == 'selected'
        first = await owner_bulletin.get(db, owner, number)
        assert await owner_bulletin_video_review.approve_for_bulletin(db, owner, number, old, 2) == 'stale'
        assert await owner_bulletin_video_review.approve_for_bulletin(db, owner, number, first['revision'], 2) == 'not_reviewed'
        assert await owner_bulletin_video_review.deliver(db, owner, number, first['revision'], 2, AsyncMock(return_value=3)) == 'sent'
        assert await owner_bulletin_video_review.approve_for_bulletin(db, owner, number, first['revision'], 2) == 'selected'
    final = await owner_bulletin.get(db, owner, number)
    frozen = await owner_bulletin.approved_payload(db, owner, number, final['revision'])
    assert [v['section_id'] for v in frozen['videos']] == [1, 2]
    with patch.object(owner_trial, 'runtime_enabled', AsyncMock(return_value=True)):
        assert await owner_bulletin_admission.approve(db, owner, number, final['revision']) == 'queued'
    assert (await db.news_owner_trial_jobs.find_one({}))['bulletin'] == frozen


@pytest.mark.asyncio
async def test_old_worker_cannot_receive_multi_video():
    db = AsyncMongoMockClient().test
    queue = VideoQueue(db.media, owner='test'); await queue.ensure(); await queue.acquire()
    await news_queue.enqueue_bulletin(db.jobs, 'approved-multi', multi_data())
    client = SimpleNamespace(supports_bulletins=AsyncMock(return_value=True),
        supports_bulletin_multi_video=AsyncMock(return_value=False), submit_bulletin=AsyncMock(return_value={'status':'running'}))
    await news_queue.run_idle_once(queue, db.jobs, client)
    client.submit_bulletin.assert_not_awaited()
    client.supports_bulletin_multi_video.return_value = True
    await news_queue.run_idle_once(queue, db.jobs, client)
    client.submit_bulletin.assert_awaited_once()


def test_second_source_failure_precedes_all_tts(tmp_path, source_mp4):
    with patch.object(bulletin_pipeline, '_acquire_reviewed_video', side_effect=[(source_mp4, 'ffprobe'), ValueError('changed')]) as acquire, \
         patch.object(bulletin_dialogue_audio, 'request_one') as tts:
        with pytest.raises(ValueError):
            bulletin_pipeline.run(multi_data(), tmp_path / 'work', imageio_ffmpeg.get_ffmpeg_exe(), ['fake'])
    assert acquire.call_count == 2
    tts.assert_not_called()
    assert not (tmp_path / 'work').exists()


def test_two_continuous_decoders_and_pcm_mix_in_real_synthetic_render(tmp_path, source_mp4):
    from news_prototype import bulletin_layered_video
    import subprocess
    data = multi_data()
    popen = subprocess.Popen
    with patch.object(bulletin_pipeline, '_acquire_reviewed_video', return_value=(source_mp4, 'fake')), \
         patch.object(bulletin_video_pipeline.telegram_public_source, 'inspect', return_value=(2., 320, 180, True)), \
         patch.object(bulletin_dialogue_audio, 'request_one', return_value=(wav(), 1.)) as tts, \
         patch.object(bulletin_layered_video.subprocess, 'Popen', wraps=popen) as processes, \
         patch.object(bulletin_video_pipeline, '_mix', wraps=bulletin_video_pipeline._mix) as mix, \
         patch.object(bulletin_video_pipeline.bulletin_full_video_trial, 'build_timeline', wraps=bulletin_video_pipeline.bulletin_full_video_trial.build_timeline) as timeline:
        output = bulletin_pipeline.run(data, tmp_path / 'work', imageio_ffmpeg.get_ffmpeg_exe(), ['fake'])
    assert output.stat().st_size > 1000 and tts.call_count == 6 and mix.call_count == 2
    cuts = timeline.call_args.kwargs['group_cuts']
    assert set(cuts) == {1, 2} and all(turn_count == 1 and offset > 0 for turn_count, offset in cuts.values())
    assert len([c for c in processes.call_args_list if '-stream_loop' in c.args[0]]) == 2
    plan = mix.call_args_list[0].args[0]
    _, original = podcast_video_trial.read_wav(tmp_path / 'work' / 'dialogue.wav')
    entries = [c.args[1] for c in mix.call_args_list]
    extra = [(e.before_frames + e.clip_frames + e.hold_frames + e.after_frames) * 1000 for e in entries]
    c1, c2 = [e.cut_frame * 1000 for e in entries]
    assert plan.samples[:c1] == original[:c1]
    assert plan.samples[c1 + extra[0]:c2 + extra[0]] == original[c1:c2]
    assert plan.samples[c2 + sum(extra):] == original[c2:]
    assert plan.events[1].clip_start > plan.events[0].resume_frame
    for i in (1, 2):
        _, before = podcast_video_trial.read_wav(tmp_path / 'work' / f'{i}-before-normalized.wav')
        _, after = podcast_video_trial.read_wav(tmp_path / 'work' / f'{i}-after-normalized.wav')
        _, story = podcast_video_trial.read_wav(tmp_path / 'work' / f'{i}-normalized.wav')
        assert story == before + array('h', [0])*12000 + after


@pytest.mark.parametrize('count', [5, 10, 20])
def test_multiple_inserts_reserve_budget_before_continuation(tmp_path, source_mp4, count):
    data = multi_data(count)
    with patch.object(bulletin_pipeline, '_acquire_reviewed_video', return_value=(source_mp4, 'fake')), \
         patch.object(bulletin_dialogue_audio, 'request_one', return_value=(wav(280), 280.)) as tts:
        with pytest.raises(bulletin_timing.BulletinTooLong):
            bulletin_pipeline.run(data, tmp_path / 'work', imageio_ffmpeg.get_ffmpeg_exe(), ['fake'])
    assert tts.call_count == 1
    assert (tmp_path / 'work' / '1-before-normalized.wav').exists()
    assert not (tmp_path / 'work' / 'video.mp4').exists()


def test_actual_multi_subprocess_without_network(tmp_path, source_mp4):
    import json
    import os
    import uuid
    from pathlib import Path
    from video_prototype import news_jobs
    data = multi_data()
    caption = 'Синтетический пост'
    for video in data['videos']:
        video['caption_sha256'] = hashlib.sha256(caption.encode()).hexdigest()
        video['video_sha256'] = hashlib.sha256(source_mp4.read_bytes()).hexdigest()
    job = str(uuid.uuid4())
    folder = tmp_path / job; folder.mkdir()
    (folder / 'request.json').write_text(json.dumps({'bulletin': data}, ensure_ascii=False))
    hooks = tmp_path / 'hooks'; hooks.mkdir()
    (hooks / 'sitecustomize.py').write_text(f'''
import hashlib, io, shutil, socket, wave
from news_prototype import telegram_public_source as source, bulletin_dialogue_audio as audio
def blocked(*args, **kwargs): raise AssertionError('network disabled')
socket.socket.connect = blocked
def tts(key, body, timeout):
    out = io.BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
        wav.writeframes(b'\\x01\\x00' * 24000)
    return out.getvalue(), 1.
audio.request_one = tts
source.fetch_post = lambda url: source.Post(url, 'ExampleNews/101', {caption!r}, 'available', 'unused')
def download(post, folder, **kwargs):
    folder.mkdir(); path = folder / 'source.mp4'
    shutil.copyfile({str(source_mp4)!r}, path)
    return source.Video(path, hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size, 2., 320, 180, True)
source.download_video = download
source.inspect = lambda *args: (2., 320, 180, True)
''')
    binary = tmp_path / 'bin'; binary.mkdir()
    (binary / 'ffmpeg').symlink_to(imageio_ffmpeg.get_ffmpeg_exe())
    (binary / 'ffprobe').symlink_to(imageio_ffmpeg.get_ffmpeg_exe())  # metadata inspection mocked
    repo = Path(__file__).resolve().parent.parent
    env = {'PYTHONPATH': str(hooks) + os.pathsep + str(repo),
           'PATH': str(binary) + os.pathsep + os.environ.get('PATH', ''), 'GEMINI_TTS_KEYS': 'test-only'}
    with patch.dict(os.environ, env):
        news_jobs.bulletin_runner(repo)(job, folder)
    assert json.loads((folder / 'work' / 'status.json').read_text())['stage'] == 'ready_for_review'
    assert (folder / 'work' / 'video.mp4').stat().st_size > 1000
    assert (folder / 'worker.log').read_text().count('Gemini TTS готов') == 6
    assert all((folder / 'work' / f'video-remarks-{i}' / 'remarks.wav').is_file() for i in (1, 2))


def test_contract_rejects_oversized_video_remarks():
    # The contract counts all approved overlay speech, not each clip separately.
    data = multi_data()
    data['videos'][0]['remarks'][0]['text'] = 'а' * 500
    with pytest.raises(ValueError):
        bulletin_contract.validate_bulletin(data)


@pytest.mark.parametrize('count', [5, 10, 20])
def test_multi_video_in_full_synthetic_bulletins_without_truncation(tmp_path, source_mp4, count):
    import subprocess
    from news_prototype import bulletin_video_timing_plan as timing, combined_trial
    data = multi_data(count)
    captured = []
    def render(_data, audio, parts, output, ffmpeg, ffprobe, *, specs):
        windows, _ = bulletin_timing.section_windows(parts)
        _, samples = podcast_video_trial.read_wav(audio)
        intro = combined_trial.INTRO_FRAMES / timing.FPS
        sections = [timing.Section(w.section_id, round((w.start-intro)*timing.RATE),
                                   round((w.end-intro)*timing.RATE)) for w in windows]
        entries = [timing.Insert(s['section_id'], s['cut_frame'], s['clip_frames']) for s in specs]
        plan = timing.plan(samples, sections, entries)
        captured.append((parts, plan))
        output.write_bytes(b'synthetic')
    real_run = subprocess.run
    def run(cmd, **kwargs):
        if cmd[-3:] == ['-f', 'null', '-']:
            return subprocess.CompletedProcess(cmd, 0)
        return real_run(cmd, **kwargs)
    with patch.object(bulletin_pipeline, '_acquire_reviewed_video', return_value=(source_mp4, 'fake')), \
         patch.object(bulletin_dialogue_audio, 'request_one', return_value=(wav(), 1.)) as tts, \
         patch.object(bulletin_video_pipeline, 'render_many', side_effect=render), \
         patch.object(bulletin_pipeline.subprocess, 'run', side_effect=run):
        assert bulletin_pipeline.run(data, tmp_path / 'work', imageio_ffmpeg.get_ffmpeg_exe(), ['fake']).exists()
    parts, plan = captured[0]
    assert len(parts) == count and len(plan.events) == 2 and plan.total_frames <= 300 * timing.FPS
    assert tts.call_count == count + 4
    for i in (1, count):
        _, before = podcast_video_trial.read_wav(tmp_path / 'work' / f'{i}-before-normalized.wav')
        _, after = podcast_video_trial.read_wav(tmp_path / 'work' / f'{i}-after-normalized.wav')
        _, story = podcast_video_trial.read_wav(tmp_path / 'work' / f'{i}-normalized.wav')
        assert story == before + array('h', [0]) * 12000 + after


def test_second_changed_hash_with_real_acquisition_blocks_all_tts(tmp_path, source_mp4):
    from news_prototype import telegram_public_source as source
    data = multi_data()
    caption = 'Синтетический пост'
    for video in data['videos']:
        video['caption_sha256'] = hashlib.sha256(caption.encode()).hexdigest()
        video['video_sha256'] = hashlib.sha256(source_mp4.read_bytes()).hexdigest()
    data['videos'][1]['video_sha256'] = '0' * 64
    def post(url):
        return source.Post(url, 'ExampleNews/101', caption, 'available', 'unused')
    def download(_post, folder, **kwargs):
        folder.mkdir(); path = folder / 'source.mp4'; shutil.copyfile(source_mp4, path)
        return source.Video(path, hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size, 2., 320, 180, True)
    with patch.object(bulletin_pipeline.shutil, 'which', return_value='fake'), \
         patch.object(source, 'fetch_post', side_effect=post) as fetch, \
         patch.object(source, 'download_video', side_effect=download) as acquire, \
         patch.object(bulletin_dialogue_audio, 'request_one') as tts:
        with pytest.raises(ValueError, match='изменилось'):
            bulletin_pipeline.run(data, tmp_path / 'work', imageio_ffmpeg.get_ffmpeg_exe(), ['fake'])
    assert fetch.call_count == acquire.call_count == 2
    tts.assert_not_called()
    assert not (tmp_path / 'work').exists()


@pytest.mark.asyncio
async def test_edit_of_one_comment_revokes_both_media_but_keeps_other_comment(tmp_path, source_mp4):
    db = AsyncMongoMockClient().test
    owner, number = 123, 48
    await owner_bulletin_sources.create(db, owner, number, [
        {'url': f'https://t.me/ExampleNews/{100+i}', 'title': f'Новость {i}'} for i in (1, 2)])
    async def fetch(url): return {'summary': SOURCE, 'telegram_video_status': 'available'}
    await owner_bulletin_sources.collect(db, owner, number, fetch, AsyncMock())
    await owner_bulletin.prepare(db, owner, number, AsyncMock(side_effect=response))
    text = 'Бендер: Смотрим!\nКиберсенсей: Давай.'
    for i in (1, 2):
        item = await owner_bulletin.get(db, owner, number)
        assert await owner_bulletin.change_video_remarks(db, owner, number, item['revision'], i, text) == 'saved'
    async def acquire(db, oid, no, rev, section, folder):
        folder.mkdir(); path = folder / 'source.mp4'; shutil.copyfile(source_mp4, path)
        return bulletin_video_candidate.Candidate(oid, no, rev, section, f'https://t.me/ExampleNews/{100+section}',
            hashlib.sha256(SOURCE.strip().encode()).hexdigest(), hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_size, 2., 320, 180, True, path)
    with patch.object(bulletin_video_candidate, 'acquire', side_effect=acquire):
        for i in (1, 2):
            item = await owner_bulletin.get(db, owner, number)
            assert await owner_bulletin_video_review.deliver(db, owner, number, item['revision'], i, AsyncMock(return_value=i)) == 'sent'
            assert await owner_bulletin_video_review.approve_for_bulletin(db, owner, number, item['revision'], i) == 'selected'
    item = await owner_bulletin.get(db, owner, number)
    assert len((await owner_bulletin.approved_payload(db, owner, number, item['revision']))['videos']) == 2
    assert await owner_bulletin.change_video_remarks(db, owner, number, item['revision'], 1,
        'Бендер: Новая реплика!\nКиберсенсей: Ну да.') == 'saved'
    changed = await owner_bulletin.get(db, owner, number)
    assert all('video' not in s for s in changed['sections'])
    assert changed['sections'][1]['video_remarks'][0]['text'] == 'Смотрим!'
    assert await owner_bulletin_video_review.approve_for_bulletin(db, owner, number, item['revision'], 2) == 'stale'
