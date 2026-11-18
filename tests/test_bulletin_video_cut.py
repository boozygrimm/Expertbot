"""Explicit, reviewed insertion between complete speech groups."""
from array import array
from io import BytesIO
import json
import wave
from unittest.mock import AsyncMock, patch

import imageio_ffmpeg
import pytest
from mongomock_motor import AsyncMongoMockClient

from news_prototype import (bulletin_contract, bulletin_dialogue_audio, bulletin_editor, bulletin_pipeline,
    bulletin_timing, bulletin_video_candidate, bulletin_video_pipeline, news_queue, owner_bulletin,
    owner_bulletin_video_review, podcast_video_trial)
from tests.test_bulletin_dialogue_audio import payload, wav
from tests.test_bulletin_foundation import inputs, response
from tests.test_bulletin_video_worker_integration import setup_review, source_mp4
from tests.test_bulletin_video_remarks import REMARKS


def video_data():
    data = payload(2)
    data['sections'][0]['turns'].extend([
        {'speaker': 'Бендер', 'text': 'Подводка к ролику.'},
        {'speaker': 'Киберсенсей', 'text': 'Продолжение новости.'}])
    data['video'] = {'section_id': 1, 'post_url': 'https://t.me/ExampleNews/102',
                     'caption_sha256': 'a' * 64, 'video_sha256': 'b' * 64,
                     'remarks': REMARKS, 'cut_after': 2}
    return data


def coded_wav(value, count=24000):
    stream = BytesIO()
    with wave.open(stream, 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(24000)
        out.writeframes(array('h', [value] * count).tobytes())
    return stream.getvalue()


def test_cut_contract_and_editor_reject_invalid_placement():
    data = video_data()
    assert bulletin_contract.validate_bulletin(data)['video']['cut_after'] == 2
    for invalid in (0, 4, -1, True, '2', None, 2.0):
        bad = video_data(); bad['video']['cut_after'] = invalid
        with pytest.raises(ValueError): bulletin_contract.validate_bulletin(bad)
    stories = inputs(2); stories[0]['telegram_video_status'] = 'available'
    draft = response(stories)
    draft['sections'][0]['turns'].append({'speaker':'Киберсенсей','text':'Продолжение.', 'kind':'comment', 'evidence':''})
    draft['sections'][0]['video_cut_after'] = 1
    checked = bulletin_editor.parse_draft(json.dumps(draft, ensure_ascii=False), stories)
    assert checked['sections'][0]['video_cut_after'] == 1
    draft['sections'][0]['video_cut_after'] = 2
    with pytest.raises(ValueError): bulletin_editor.parse_draft(json.dumps(draft), stories)
    draft['sections'][0]['video_cut_after'] = 1
    with pytest.raises(ValueError): bulletin_editor.parse_draft(json.dumps(draft), inputs(2))


@pytest.mark.asyncio
async def test_owner_edits_reviews_and_freezes_cut_position(tmp_path, source_mp4):
    db = AsyncMongoMockClient().test
    owner, number, item, acquire = await setup_review(db, source_mp4)
    text = 'Строка: Видеоновость\nБендер: Подводка.\nКиберсенсей: Продолжение.\nВидео после: 1'
    assert await owner_bulletin.change_section(db, owner, number, item['revision'], 1, text=text) == 'saved'
    updated = await owner_bulletin.get(db, owner, number)
    assert updated['sections'][0]['video_cut_after'] == 1
    assert 'Видео после: 1' in owner_bulletin.editable_section(updated['sections'][0])
    with patch.object(bulletin_video_candidate, 'acquire', side_effect=acquire):
        assert await owner_bulletin_video_review.deliver(db, owner, number, updated['revision'], 1, AsyncMock(return_value=55)) == 'sent'
    assert await owner_bulletin_video_review.approve_for_bulletin(db, owner, number, updated['revision'], 1) == 'selected'
    selected = await owner_bulletin.get(db, owner, number)
    frozen = await owner_bulletin.approved_payload(db, owner, number, selected['revision'])
    assert frozen['video']['cut_after'] == 1
    bad = copy_of_sections = [dict(s) for s in selected['sections']]
    bad[0] = {**bad[0], 'video_cut_after': 2}
    with pytest.raises(ValueError): owner_bulletin._payload_from_sections(copy_of_sections)
    # Any edit revokes approval and removes an omitted position.
    text_without_cut = text.replace('\nВидео после: 1', '')
    assert await owner_bulletin.change_section(db, owner, number, selected['revision'], 1, text=text_without_cut) == 'saved'
    changed = await owner_bulletin.get(db, owner, number)
    assert 'video_cut_after' not in changed['sections'][0]
    assert 'video' not in await owner_bulletin.approved_payload(db, owner, number, changed['revision'])


def test_owner_marker_parser_rejects_impossible_or_duplicate_positions():
    base = 'Строка: Тест\nБендер: До.\nКиберсенсей: После.'
    assert owner_bulletin.parse_section_edit(base + '\nВидео после: 1')['video_cut_after'] == 1
    for suffix in ('\nВидео после: 0', '\nВидео после: 2', '\nВидео после: -1',
                   '\nВидео после: 1\nВидео после: 1', '\nВидео после: ١'):
        with pytest.raises(ValueError): owner_bulletin.parse_section_edit(base + suffix)


def test_real_video_render_preserves_complete_before_and_after_pcm(tmp_path, source_mp4):
    data = video_data(); ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    responses = [(coded_wav(1000), 1.), (coded_wav(2000), 1.), (coded_wav(3000), 1.), (coded_wav(4000), 1.)]
    with patch.object(bulletin_pipeline, '_acquire_reviewed_video', return_value=(source_mp4, 'fake')), \
         patch.object(bulletin_video_pipeline.telegram_public_source, 'inspect', return_value=(2., 320, 180, True)), \
         patch.object(bulletin_dialogue_audio, 'request_one', side_effect=responses) as tts, \
         patch.object(bulletin_video_pipeline, '_mix', wraps=bulletin_video_pipeline._mix) as mix, \
         patch.object(bulletin_video_pipeline, 'render', wraps=bulletin_video_pipeline.render) as render:
        output = bulletin_pipeline.run(data, tmp_path / 'work', ffmpeg, ['first', 'second'])
    assert output.stat().st_size > 1000 and tts.call_count == 4
    bodies = [call.args[1]['contents'][0]['parts'] for call in tts.call_args_list]
    assert [p['text'] for p in bodies[0]] == [t['text'] for t in data['sections'][0]['turns'][:2]]
    assert [p['text'] for p in bodies[1]] == [t['text'] for t in data['sections'][0]['turns'][2:]]
    assert [p['text'] for p in bodies[2]] == [t['text'] for t in data['sections'][1]['turns']]
    assert [p['text'] for p in bodies[3]] == [t['text'] for t in REMARKS]
    _, before = podcast_video_trial.read_wav(tmp_path / 'work' / '1-before-normalized.wav')
    _, after = podcast_video_trial.read_wav(tmp_path / 'work' / '1-after-normalized.wav')
    _, story = podcast_video_trial.read_wav(tmp_path / 'work' / '1-normalized.wav')
    assert story == before + array('h', [0]) * 12000 + after
    _, original = podcast_video_trial.read_wav(tmp_path / 'work' / 'dialogue.wav')
    plan, entry, _, _, _ = mix.call_args.args
    cut = entry.cut_frame * 1000
    assert 0 < cut < len(story)
    assert not any(story[cut - 3000:cut + 3000])
    added = len(plan.samples) - len(original)
    assert plan.samples[:cut] == original[:cut] and plan.samples[cut + added:] == original[cut:]
    assert entry.cut_frame == render.call_args.kwargs['cut_frame']
    assert any(original[cut:])  # The same story continues after the clip.


def test_refuse_early_without_continuation_request_and_keep_audio(tmp_path, source_mp4):
    with patch.object(bulletin_pipeline, '_acquire_reviewed_video', return_value=(source_mp4, 'fake')), \
         patch.object(bulletin_dialogue_audio, 'request_one', return_value=(wav(290), 290.)) as tts, \
         patch.object(bulletin_video_pipeline, 'render') as render:
        with pytest.raises(bulletin_timing.BulletinTooLong):
            bulletin_pipeline.run(video_data(), tmp_path / 'work', imageio_ffmpeg.get_ffmpeg_exe(), ['fake'])
        assert tts.call_count == 1
        render.assert_not_called()
        assert (tmp_path / 'work' / '1-before-normalized.wav').is_file()
        assert (tmp_path / 'work' / 'dialogue.wav').is_file()
        assert not (tmp_path / 'work' / '1-after-raw.wav').exists()
        assert 'audio_too_long' in (tmp_path / 'work' / 'status.json').read_text()
        with pytest.raises(FileExistsError):
            bulletin_pipeline.run(video_data(), tmp_path / 'work', imageio_ffmpeg.get_ffmpeg_exe(), ['fake'])
        assert tts.call_count == 1


@pytest.mark.asyncio
async def test_cut_task_waits_for_compatible_worker():
    from bender_queue import VideoQueue
    from types import SimpleNamespace
    db = AsyncMongoMockClient().test
    queue = VideoQueue(db.media, owner='test'); await queue.ensure(); await queue.acquire()
    await news_queue.enqueue_bulletin(db.jobs, 'owner-approved-cut', video_data())
    client = SimpleNamespace(supports_bulletins=AsyncMock(return_value=True),
        supports_bulletin_video=AsyncMock(return_value=True), supports_bulletin_video_remarks=AsyncMock(return_value=True),
        supports_bulletin_video_cut=AsyncMock(return_value=False), submit_bulletin=AsyncMock(return_value={'status':'running'}))
    await news_queue.run_idle_once(queue, db.jobs, client)
    client.submit_bulletin.assert_not_awaited()
    client.supports_bulletin_video_cut.return_value = True
    await news_queue.run_idle_once(queue, db.jobs, client)
    client.submit_bulletin.assert_awaited_once()


def test_cut_in_second_story_uses_true_offset_and_continuous_video(tmp_path, source_mp4):
    data = video_data()
    data['video']['section_id'] = 2
    data['video']['cut_after'] = 1
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    responses = [(coded_wav(1000), 1.), (coded_wav(2000), 1.), (coded_wav(3000), 1.), (coded_wav(4000), 1.)]
    with patch.object(bulletin_pipeline, '_acquire_reviewed_video', return_value=(source_mp4, 'fake')), \
         patch.object(bulletin_video_pipeline.telegram_public_source, 'inspect', return_value=(2., 320, 180, True)), \
         patch.object(bulletin_dialogue_audio, 'request_one', side_effect=responses), \
         patch.object(bulletin_video_pipeline, '_mix', wraps=bulletin_video_pipeline._mix) as mix:
        assert bulletin_pipeline.run(data, tmp_path / 'work', ff, ['fake']).is_file()
    plan, entry, _, _, _ = mix.call_args.args
    _, original = podcast_video_trial.read_wav(tmp_path / 'work' / 'dialogue.wav')
    _, first_story = podcast_video_trial.read_wav(tmp_path / 'work' / '1-normalized.wav')
    _, before = podcast_video_trial.read_wav(tmp_path / 'work' / '2-before-normalized.wav')
    section_start = len(first_story) + 8400  # 350 ms between stories
    cut = entry.cut_frame * 1000
    expected_middle = section_start + len(before) + 6000
    assert abs(cut - expected_middle) <= 500
    assert not any(original[cut - 3000:cut + 3000])
    assert any(original[cut + 6000:])
    assert plan.events[0].source_elapsed_at_clip > 0


def test_continuation_failure_keeps_first_group_without_replay(tmp_path, source_mp4):
    from video_prototype.gemini_tts import TTSFailure
    with patch.object(bulletin_pipeline, '_acquire_reviewed_video', return_value=(source_mp4, 'fake')), \
         patch.object(bulletin_dialogue_audio, 'request_one', side_effect=[(wav(), 1.), TTSFailure('HTTP 429')]) as tts, \
         patch.object(bulletin_video_pipeline, 'render') as render:
        with pytest.raises(TTSFailure):
            bulletin_pipeline.run(video_data(), tmp_path / 'work', imageio_ffmpeg.get_ffmpeg_exe(), ['fake'])
        assert (tmp_path / 'work' / '1-before-normalized.wav').is_file()
        assert not (tmp_path / 'work' / '1-normalized.wav').exists()
        assert not (tmp_path / 'work' / 'video.mp4').exists()
        render.assert_not_called()
        with pytest.raises(FileExistsError):
            bulletin_pipeline.run(video_data(), tmp_path / 'work', imageio_ffmpeg.get_ffmpeg_exe(), ['fake'])
        assert tts.call_count == 2


@pytest.mark.parametrize('count', [5, 10, 20])
def test_many_news_with_one_mid_story_video_obey_full_duration_budget(tmp_path, source_mp4, count):
    import subprocess
    data = payload(count)
    chosen = count // 2
    data['video'] = {'section_id': chosen, 'post_url': 'https://t.me/ExampleNews/102',
                     'caption_sha256': 'a' * 64, 'video_sha256': 'b' * 64, 'cut_after': 1}
    captured = []
    def render(data, audio, parts, source, output, ffmpeg, ffprobe, **kwargs):
        windows, total = bulletin_timing.section_windows(parts)
        captured.append((windows, total, kwargs))
        output.write_bytes(b'synthetic video')
    real_run = subprocess.run
    def run(command, **kwargs):
        if command[-3:] == ['-f', 'null', '-']:
            return subprocess.CompletedProcess(command, 0)
        return real_run(command, **kwargs)
    with patch.object(bulletin_pipeline, '_acquire_reviewed_video', return_value=(source_mp4, 'fake')), \
         patch.object(bulletin_dialogue_audio, 'request_one', return_value=(wav(), 1.)) as tts, \
         patch.object(bulletin_video_pipeline, 'render', side_effect=render), \
         patch.object(bulletin_pipeline.subprocess, 'run', side_effect=run):
        assert bulletin_pipeline.run(data, tmp_path / 'work', imageio_ffmpeg.get_ffmpeg_exe(), ['fake']).is_file()
    assert tts.call_count == count + 1  # one extra block, not per-turn requests
    windows, seconds, kwargs = captured[0]
    assert [w.section_id for w in windows] == list(range(1, count + 1))
    assert kwargs['section_id'] == chosen
    assert seconds + (18 + 240 + 2 + 18) / 24 <= 300
