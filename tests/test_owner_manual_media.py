"""Owner-supplied picture and video fragment for one reviewed story draft."""
import hashlib
import json
import os
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import imageio_ffmpeg
import pytest
from PIL import Image

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
from mongomock_motor import AsyncMongoMockClient

from news_prototype import (bulletin_timing, news_image_codec, owner_drafts, owner_trial,
                            podcast_audio_trial, story_audio, story_video_insert,
                            telegram_public_source)
from tests.test_owner_drafts import DRAFT, SOURCE, URL
from tests.test_bulletin_video_worker_integration import source_mp4  # noqa: F401  (pytest fixture)
from tests.test_podcast_video_trial import make_segment

OWNER = 123


def png(colour='red', size=(40, 30)):
    buffer = BytesIO()
    Image.new('RGB', size, colour).save(buffer, format='PNG')
    return buffer.getvalue()


async def ready_draft(db, number=10):
    assert await owner_drafts.create(db, OWNER, number, URL, '')
    result = await owner_drafts.process_new(db, OWNER, number, AsyncMock(return_value={
        'summary': SOURCE, 'resolved_url': URL}), AsyncMock(return_value=DRAFT))
    assert result == 'draft_ready'
    return await owner_drafts.get(db, OWNER, number)


@pytest.mark.asyncio
async def test_manual_picture_replaces_image_drops_focus_and_bumps_revision():
    db = AsyncMongoMockClient().test
    item = await ready_draft(db)
    script = dict(item['script'], image_b64=news_image_codec.make_thumbnail(png()),
                  image_regions=[[0, 0, 400, 400]])
    await db.news_owner_drafts.update_one({'_id': item['_id']}, {'$set': {'script': script}})
    item = await owner_drafts.get(db, OWNER, 10)
    assert await owner_drafts.set_image(db, OWNER, 10, item['revision'], png('blue')) == 'saved'
    saved = await owner_drafts.get(db, OWNER, 10)
    assert saved['revision'] != item['revision']
    assert 'image_regions' not in saved['script']
    assert Image.open(BytesIO(news_image_codec.decode_thumbnail(saved['script']['image_b64']))).getpixel((5, 5))[2] > 150
    assert saved['image_kind'] == 'manual'
    # The same version cannot be reused after it changed.
    assert await owner_drafts.set_image(db, OWNER, 10, item['revision'], png('green')) == 'stale'
    with pytest.raises(ValueError):
        await owner_drafts.set_image(db, OWNER, 10, saved['revision'], b'not an image')


@pytest.mark.asyncio
async def test_manual_picture_rejects_garbage_and_non_ready_drafts():
    db = AsyncMongoMockClient().test
    item = await ready_draft(db)
    with pytest.raises(ValueError):
        await owner_drafts.set_image(db, OWNER, 10, item['revision'], b'<html>')
    # A huge picture is rebuilt through the bounded codec, never stored as is.
    with pytest.raises(ValueError):
        await owner_drafts.set_image(db, OWNER, 10, item['revision'], png(size=(9000, 9000)))
    assert await owner_drafts.set_image(db, OWNER, 11, item['revision'], png()) == 'not_ready'


@pytest.mark.asyncio
async def test_manual_video_digest_is_validated_and_can_be_removed():
    db = AsyncMongoMockClient().test
    item = await ready_draft(db)
    digest = hashlib.sha256(b'fragment').hexdigest()
    for invalid in ('not-a-digest', digest.upper(), digest[:-1], None, 42):
        with pytest.raises(ValueError):
            await owner_drafts.set_video(db, OWNER, 10, item['revision'], invalid)
    assert await owner_drafts.set_video(db, OWNER, 10, item['revision'], digest) == 'saved'
    saved = await owner_drafts.get(db, OWNER, 10)
    assert saved['script']['video_sha256'] == digest and saved['revision'] != item['revision']
    assert await owner_drafts.remove_video(db, OWNER, 10, item['revision']) == 'stale'
    assert await owner_drafts.remove_video(db, OWNER, 10, saved['revision']) == 'saved'
    again = await owner_drafts.get(db, OWNER, 10)
    assert 'video_sha256' not in again['script']
    assert await owner_drafts.remove_video(db, OWNER, 10, again['revision']) == 'unchanged'


def test_script_validator_accepts_only_a_hex_digest_for_video():
    turns = [{'speaker': 'Бендер', 'text': 'Привет.'}]
    digest = 'b' * 64
    assert story_audio.validate_script({'turns': turns, 'video_sha256': digest})['video_sha256'] == digest
    for invalid in ('b' * 63, 'B' * 64, '../video.mp4', '', 1):
        with pytest.raises(ValueError):
            story_audio.validate_script({'turns': turns, 'video_sha256': invalid})
    # Unrelated fields are still not smuggled into the script.
    assert 'video' not in story_audio.validate_script({'turns': turns, 'video': digest})


async def story_folder(folder: Path):
    folder.mkdir()
    for name in ('a.wav', 'b.wav'):
        make_segment(folder / name, seconds=6)
    podcast_audio_trial.assemble_wav([folder / 'a.wav', folder / 'b.wav'], folder / 'dialogue.wav')
    (folder / 'story.json').write_text(json.dumps({'ticker': 'ТЕСТ', 'turns': [
        {'speaker': 'Бендер', 'audio': 'a.wav'}, {'speaker': 'Киберсенсей', 'audio': 'b.wav'}]}),
        encoding='utf-8')


def test_cut_is_placed_in_the_real_pause_between_turns(tmp_path):
    import asyncio
    from news_prototype import podcast_video_trial
    folder = tmp_path / 'story'
    asyncio.run(story_folder(folder))
    rate, samples = podcast_video_trial.read_wav(folder / 'dialogue.wav')
    turns = [(folder / 'a.wav', 'Бендер'), (folder / 'b.wav', 'Киберсенсей')]
    frame = story_video_insert.cut_frame(samples, turns)
    cut = frame * (rate // 24)
    # The pause sits between the two six-second turns, well inside the story.
    assert 5 * rate < cut < 9 * rate
    assert not any(samples[cut - 3000:cut + 3000])
    # A single turn has no pause, so nothing is guessed.
    with pytest.raises(ValueError, match='паузы'):
        story_video_insert.cut_frame(samples, turns[:1])


def test_single_story_render_with_fragment_mixes_the_fragment(tmp_path, source_mp4):
    import asyncio
    folder = tmp_path / 'story'
    asyncio.run(story_folder(folder))
    (folder / 'news-image.jpg').write_bytes(news_image_codec.decode_thumbnail(
        news_image_codec.make_thumbnail(png(size=(200, 120)))))
    output = tmp_path / 'video.mp4'
    with patch.object(story_video_insert.shutil, 'which', return_value='ffprobe'), \
         patch.object(telegram_public_source, 'inspect', return_value=(2.0, 320, 180, True)):
        story_video_insert.render(folder, output, imageio_ffmpeg.get_ffmpeg_exe(), source_mp4)
    assert output.stat().st_size > 100_000
    # The fragment really reached the timeline: the same story without an
    # insert is noticeably shorter and smaller.
    from news_prototype import story_studio_video
    plain = tmp_path / 'plain.mp4'
    story_studio_video.render(folder, plain, imageio_ffmpeg.get_ffmpeg_exe())
    assert output.stat().st_size > plain.stat().st_size
    # Rendering never overwrites and never follows a symlinked source.
    with pytest.raises(ValueError):
        story_video_insert.render(folder, output, imageio_ffmpeg.get_ffmpeg_exe(), source_mp4)


@pytest.mark.asyncio
async def test_story_with_fragment_needs_the_new_worker_capability():
    from news_prototype import news_queue
    digest = 'c' * 64
    script = dict(DRAFT['script'])
    script['video_sha256'] = digest
    job = {'kind': 'story', 'script': script}
    for capabilities, expected in (({'news-story-bulletin-studio-v1', 'news-story-image-v1'}, False),
                                   ({'news-story-bulletin-studio-v1', 'news-story-manual-video-v1'}, True)):
        client = SimpleNamespace(
            supports_story_studio=AsyncMock(return_value=True),
            supports_story_manual_video=AsyncMock(return_value='news-story-manual-video-v1' in capabilities),
            supports_images=AsyncMock(return_value=True),
            supports_image_focus=AsyncMock(return_value=True))
        assert await news_queue.capabilities_ready(client, job) is expected
    # Old workers that never advertise the capability fail closed.
    legacy = SimpleNamespace(supports_story_studio=AsyncMock(return_value=True),
                             supports_images=AsyncMock(return_value=True),
                             supports_image_focus=AsyncMock(return_value=True))
    assert await news_queue.capabilities_ready(legacy, job) is False


@pytest.mark.asyncio
async def test_pipeline_rejects_fragment_outside_the_studio(tmp_path):
    from news_prototype import story_pipeline
    script = dict(DRAFT['script'], video_sha256='d' * 64)
    folder = tmp_path / 'job'
    folder.mkdir()
    with patch.object(story_audio, 'prepare', return_value=tmp_path / 'story.json'):
        with pytest.raises(ValueError, match='студии'):
            story_pipeline.run(script, folder, 'ffmpeg', ['unused'], studio='legacy')


def test_worker_prepares_fragment_only_in_the_studio(tmp_path):
    """The worker copies the fragment from the upload cache, never from a path."""
    from news_prototype import story_pipeline
    job = tmp_path / 'root' / 'job'
    work = job / 'work'
    work.mkdir(parents=True)
    script = {'turns': [{'speaker': 'Бендер', 'text': 'Привет.'}], 'ticker': 'Т',
              'video_sha256': 'e' * 64}
    with patch.object(story_audio, 'prepare', return_value=work / 'story.json'), \
         patch('news_prototype.bulletin_manual_media.acquire',
               return_value=work / 'source.mp4') as acquire, \
         patch('news_prototype.story_video_insert.render') as render, \
         patch.object(story_pipeline.shutil, 'which', return_value='/usr/bin/ffprobe'), \
         patch('video_prototype.news_jobs.story_studio_assets_ready', return_value=True):
        render.side_effect = ValueError('render stub')
        with pytest.raises(ValueError, match='render stub'):
            story_pipeline.run(script, work, 'ffmpeg', ['unused'], studio='bulletin')
    # The cache root is the sibling of all job directories, never a request path.
    assert acquire.call_args.args[0] == job.parent
    assert acquire.call_args.args[1] == 'e' * 64
    assert render.call_args.args[3] == work / 'source.mp4'
