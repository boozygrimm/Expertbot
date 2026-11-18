"""Approved overlay dialogue through editor, owner, queue and real rendering."""
from array import array
import copy
import json
from unittest.mock import AsyncMock, patch

import imageio_ffmpeg
import pytest
from mongomock_motor import AsyncMongoMockClient

from news_prototype import (bulletin_contract, bulletin_dialogue_audio, bulletin_editor, bulletin_pipeline,
    bulletin_timing, bulletin_video_candidate, bulletin_video_pipeline, news_queue, owner_bulletin,
    owner_bulletin_admission, owner_bulletin_video_review, owner_trial, podcast_video_trial)
from tests.test_bulletin_dialogue_audio import payload as dialogue_payload, wav
from tests.test_bulletin_foundation import inputs, response
from tests.test_bulletin_video_worker_integration import setup_review, source_mp4


REMARKS = [{'speaker': 'Бендер', 'text': 'Глянем, что там!' },
           {'speaker': 'Киберсенсей', 'text': 'Давай посмотрим.'}]


def test_editor_only_adds_bounded_nonfact_comments_to_one_detected_video():
    stories = inputs(2)
    stories[0]['telegram_video_status'] = 'available'
    prompt, material = bulletin_editor.prompt_for(stories)
    assert 'Ты не видел видео' in prompt
    assert json.loads(material)['stories'][0]['video_candidate'] is True
    data = response(stories); data['sections'][0]['video_remarks'] = REMARKS
    checked = bulletin_editor.parse_draft(json.dumps(data, ensure_ascii=False), stories)
    assert checked['sections'][0]['video_remarks'] == REMARKS
    assert checked['spoken_characters'] == sum(len(t['text']) for s in checked['sections'] for t in s['turns']) + sum(len(t['text']) for t in REMARKS)
    for invalid in ([REMARKS[0]], [REMARKS[0], REMARKS[0]],
                    [REMARKS[0], {'speaker': 'Киберсенсей', 'text': 'x' * 121}],
                    [REMARKS[0], {'speaker': 'Киберсенсей', 'text': 'x\ny'}]):
        bad = copy.deepcopy(data); bad['sections'][0]['video_remarks'] = invalid
        with pytest.raises(ValueError):
            bulletin_editor.parse_draft(json.dumps(bad, ensure_ascii=False), stories)
    bad = copy.deepcopy(data); bad['sections'][1]['video_remarks'] = REMARKS
    with pytest.raises(ValueError):
        bulletin_editor.parse_draft(json.dumps(bad, ensure_ascii=False), stories)
    bad = copy.deepcopy(data); bad['sections'][0]['video_remarks'] = REMARKS
    without_video = inputs(2)
    with pytest.raises(ValueError):
        bulletin_editor.parse_draft(json.dumps(bad, ensure_ascii=False), without_video)


@pytest.mark.asyncio
async def test_comments_are_revision_bound_and_not_rendered_without_selection(tmp_path, source_mp4):
    db = AsyncMongoMockClient().test
    owner, number, item, acquire = await setup_review(db, source_mp4)
    text = '\n'.join(f'{t["speaker"]}: {t["text"]}' for t in REMARKS)
    assert await owner_bulletin.change_video_remarks(db, owner, number, item['revision'], 1, text) == 'saved'
    reviewed = await owner_bulletin.get(db, owner, number)
    assert reviewed['sections'][0]['video_remarks'] == REMARKS
    assert 'video' not in await owner_bulletin.approved_payload(db, owner, number, reviewed['revision'])
    assert await owner_bulletin_video_review.approve_for_bulletin(db, owner, number, reviewed['revision'], 1) == 'not_reviewed'
    with patch.object(bulletin_video_candidate, 'acquire', side_effect=acquire):
        assert await owner_bulletin_video_review.deliver(db, owner, number, reviewed['revision'], 1, AsyncMock(return_value=55)) == 'sent'
    assert await owner_bulletin_video_review.approve_for_bulletin(db, owner, number, reviewed['revision'], 1) == 'selected'
    selected = await owner_bulletin.get(db, owner, number)
    payload = await owner_bulletin.approved_payload(db, owner, number, selected['revision'])
    assert payload['video']['remarks'] == REMARKS
    with patch.object(owner_trial, 'runtime_enabled', AsyncMock(return_value=True)):
        assert await owner_bulletin_admission.approve(db, owner, number, selected['revision']) == 'queued'
    assert (await db.news_owner_trial_jobs.find_one({}))['bulletin'] == payload
    # Already frozen: cannot mutate the previously approved job.
    assert await owner_bulletin.change_video_remarks(db, owner, number, selected['revision'], 1, '-') == 'stale'


@pytest.mark.asyncio
async def test_editing_comments_after_video_selection_invalidates_approval(tmp_path, source_mp4):
    db = AsyncMongoMockClient().test
    owner, number, item, acquire = await setup_review(db, source_mp4)
    with patch.object(bulletin_video_candidate, 'acquire', side_effect=acquire):
        assert await owner_bulletin_video_review.deliver(db, owner, number, item['revision'], 1, AsyncMock(return_value=55)) == 'sent'
    assert await owner_bulletin_video_review.approve_for_bulletin(db, owner, number, item['revision'], 1) == 'selected'
    selected = await owner_bulletin.get(db, owner, number)
    assert await owner_bulletin.change_video_remarks(db, owner, number, selected['revision'], 1,
        'Бендер: Глянем!\nКиберсенсей: Давай.') == 'saved'
    updated = await owner_bulletin.get(db, owner, number)
    assert 'video' not in await owner_bulletin.approved_payload(db, owner, number, updated['revision'])
    assert await owner_bulletin_video_review.approve_for_bulletin(db, owner, number, updated['revision'], 1) == 'not_reviewed'


def video_data():
    data = dialogue_payload(2)
    data['video'] = {'section_id': 1, 'post_url': 'https://t.me/ExampleNews/102',
                     'caption_sha256': 'a' * 64, 'video_sha256': 'b' * 64, 'remarks': REMARKS}
    return data


def test_real_render_uses_one_joint_comments_request_and_preserves_pcm(tmp_path, source_mp4):
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    with patch.object(bulletin_pipeline, '_acquire_reviewed_video', return_value=(source_mp4, 'fake-ffprobe')), \
         patch.object(bulletin_video_pipeline.telegram_public_source, 'inspect', return_value=(2., 320, 180, True)), \
         patch.object(bulletin_dialogue_audio, 'request_one', return_value=(wav(), 1.)) as tts, \
         patch.object(bulletin_video_pipeline, '_mix', wraps=bulletin_video_pipeline._mix) as mix:
        output = bulletin_pipeline.run(video_data(), tmp_path / 'work', ff, ['fake'])
    assert output.stat().st_size > 1000 and tts.call_count == 3
    parts = tts.call_args_list[2].args[1]['contents'][0]['parts']
    assert [p['text'] for p in parts] == [t['text'] for t in REMARKS]
    assert [p['speechMetadata']['speaker'] for p in parts] == ['Bender', 'Sensei']
    assert (tmp_path / 'work' / 'video-remarks' / 'remarks-raw.wav').exists()
    plan, entry, source, duration, remarks = mix.call_args.args
    assert remarks is not None and len(remarks) == 24000 and any(remarks)
    mixed = bulletin_video_pipeline._mix(plan, entry, source, duration, remarks)
    start = (plan.events[0].clip_start - bulletin_video_pipeline.combined_trial.INTRO_FRAMES) * 1000
    end = start + entry.clip_frames * 1000
    assert mixed[:start] == plan.samples[:start] and mixed[end:] == plan.samples[end:]
    # Without background, overlay contains every original sample unchanged.
    voice_only = bulletin_video_pipeline._mix(plan, entry, array('h'), duration, remarks)
    assert voice_only[start + 24000:start + 24000 + len(remarks)] == remarks


def test_long_remarks_extend_clip_but_not_speech_and_respect_entire_budget(tmp_path, source_mp4):
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    # Base timeline fits with the minimum insert but 11 s of remarks extends
    # that insert enough to cross 300 seconds. No media decode or render starts.
    replies = [(wav(141), 141.), (wav(142), 142.), (wav(11), 11.)]
    with patch.object(bulletin_pipeline, '_acquire_reviewed_video', return_value=(source_mp4, 'fake')), \
         patch.object(bulletin_dialogue_audio, 'request_one', side_effect=replies) as tts, \
         patch.object(bulletin_video_pipeline.telegram_public_source, 'inspect') as inspect, \
         patch.object(bulletin_video_pipeline.bulletin_layered_video, 'render') as render:
        with pytest.raises(bulletin_timing.BulletinTooLong):
            bulletin_pipeline.run(video_data(), tmp_path / 'work', ff, ['fake'])
    assert tts.call_count == 3
    inspect.assert_not_called(); render.assert_not_called()
    assert 'audio_too_long' in (tmp_path / 'work' / 'status.json').read_text()
    _, remarks = podcast_video_trial.read_wav(tmp_path / 'work' / 'video-remarks' / 'remarks.wav')
    assert len(remarks) == 11 * 24000
    assert not (tmp_path / 'work' / 'video.mp4').exists()


@pytest.mark.asyncio
async def test_queue_requires_overlay_capability():
    from bender_queue import VideoQueue
    from types import SimpleNamespace
    db = AsyncMongoMockClient().test
    queue = VideoQueue(db.media, owner='test'); await queue.ensure(); await queue.acquire()
    await news_queue.enqueue_bulletin(db.jobs, 'owner-reviewed', video_data())
    client = SimpleNamespace(supports_bulletins=AsyncMock(return_value=True),
        supports_bulletin_video=AsyncMock(return_value=True), supports_bulletin_video_remarks=AsyncMock(return_value=False),
        submit_bulletin=AsyncMock(return_value={'status': 'running'}))
    await news_queue.run_idle_once(queue, db.jobs, client)
    client.submit_bulletin.assert_not_awaited()
    assert (await db.jobs.find_one({}))['stage'] == 'queued'
    client.supports_bulletin_video_remarks.return_value = True
    await news_queue.run_idle_once(queue, db.jobs, client)
    client.submit_bulletin.assert_awaited_once()


@pytest.mark.asyncio
async def test_generated_remarks_survive_editor_to_owner_review(tmp_path, source_mp4):
    from news_prototype import owner_bulletin_sources
    from tests.test_owner_drafts import SOURCE
    db = AsyncMongoMockClient().test
    await owner_bulletin_sources.create(db, 123, 88, [
        {'url': 'https://t.me/ExampleNews/102', 'title': 'Видео'},
        {'url': 'https://example.com/news', 'title': 'Ещё новость'}])
    async def fetch(url):
        return {'summary': SOURCE, 'telegram_video_status': 'available' if 't.me' in url else 'none'}
    await owner_bulletin_sources.collect(db, 123, 88, fetch, AsyncMock())
    def generated(stories):
        result = response(stories)
        result['sections'][0]['video_remarks'] = REMARKS
        # editor_client returns validated data, which owner_bulletin validates again.
        return bulletin_editor.parse_draft(json.dumps(result, ensure_ascii=False), stories)
    assert await owner_bulletin.prepare(db, 123, 88, AsyncMock(side_effect=generated)) == 'ready_for_review'
    item = await owner_bulletin.get(db, 123, 88)
    assert item['sections'][0]['video_remarks'] == REMARKS
    assert 'video' not in await owner_bulletin.approved_payload(db, 123, 88, item['revision'])


def test_joint_remarks_failure_never_renders_or_restarts(tmp_path, source_mp4):
    from video_prototype.gemini_tts import TTSFailure
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    with patch.object(bulletin_pipeline, '_acquire_reviewed_video', return_value=(source_mp4, 'fake')), \
         patch.object(bulletin_dialogue_audio, 'request_one', side_effect=[(wav(), 1.), (wav(), 1.), TTSFailure('HTTP 429')]) as tts, \
         patch.object(bulletin_video_pipeline, 'render') as render:
        with pytest.raises(TTSFailure):
            bulletin_pipeline.run(video_data(), tmp_path / 'work', ff, ['fake'])
        assert tts.call_count == 3
        render.assert_not_called()
        assert (tmp_path / 'work' / 'dialogue.wav').is_file()
        assert not (tmp_path / 'work' / 'video.mp4').exists()
        assert 'render_failed' in (tmp_path / 'work' / 'status.json').read_text()
        with pytest.raises(FileExistsError):
            bulletin_pipeline.run(video_data(), tmp_path / 'work', ff, ['fake'])
        assert tts.call_count == 3


def test_joint_remarks_extend_clip_when_they_fit(tmp_path, source_mp4):
    import subprocess
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    calls = []
    def fake_render(data, audio, parts, source, output, ffmpeg, ffprobe, **kwargs):
        calls.append(kwargs)
        output.write_bytes(b'synthetic-checked-video')
    real_run = subprocess.run
    def fake_run(command, **kwargs):
        if command[-3:] == ['-f', 'null', '-']:
            return subprocess.CompletedProcess(command, 0)
        return real_run(command, **kwargs)
    with patch.object(bulletin_pipeline, '_acquire_reviewed_video', return_value=(source_mp4, 'fake')), \
         patch.object(bulletin_dialogue_audio, 'request_one', side_effect=[(wav(), 1.), (wav(), 1.), (wav(11), 11.)]), \
         patch.object(bulletin_video_pipeline, 'render', side_effect=fake_render), \
         patch.object(bulletin_pipeline.subprocess, 'run', side_effect=fake_run):
        output = bulletin_pipeline.run(video_data(), tmp_path / 'work', ff, ['fake'])
    assert output.is_file()
    assert calls[0]['clip_frames'] == 300  # 11s speech + 1.5s room at 24 fps
    _, saved = podcast_video_trial.read_wav(calls[0]['remarks'])
    assert len(saved) == 11 * 24000


def test_abnormally_long_comments_are_saved_without_truncation_or_render(tmp_path, source_mp4):
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    with patch.object(bulletin_pipeline, '_acquire_reviewed_video', return_value=(source_mp4, 'fake')), \
         patch.object(bulletin_dialogue_audio, 'request_one', side_effect=[(wav(), 1.), (wav(), 1.), (wav(29), 29.)]) as tts, \
         patch.object(bulletin_video_pipeline, 'render') as render:
        with pytest.raises(ValueError, match='слишком длинная'):
            bulletin_pipeline.run(video_data(), tmp_path / 'work', ff, ['fake'])
        assert tts.call_count == 3
        render.assert_not_called()
        _, samples = podcast_video_trial.read_wav(tmp_path / 'work' / 'video-remarks' / 'remarks.wav')
        assert len(samples) == 29 * 24000
        assert not (tmp_path / 'work' / 'video.mp4').exists()
