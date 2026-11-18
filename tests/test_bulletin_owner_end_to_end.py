"""Real owner -> queue -> worker -> owner flow; no provider or Telegram calls."""
import asyncio
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import imageio_ffmpeg
import pytest
from mongomock_motor import AsyncMongoMockClient
from bender_queue import VideoQueue
from news_prototype import (bulletin_dialogue_audio, bulletin_job, news_queue, owner_bulletin,
    owner_bulletin_admission, owner_bulletin_sources, owner_trial, review_delivery)
from news_prototype.news_worker_client import NewsWorkerClient
from tests.test_bulletin_dialogue_audio import wav
from tests.test_bulletin_foundation import response
from tests.test_owner_drafts import SOURCE
from video_prototype import news_jobs, worker


@pytest.mark.asyncio
async def test_owner_review_to_actual_worker_video_and_private_delivery():
    db = AsyncMongoMockClient().test
    owner, number = 123, 99
    choices = [{'url': f'https://example.com/news/{i}', 'title': f'Новость {i}'} for i in range(1, 4)]
    await owner_bulletin_sources.create(db, owner, number, choices)
    async def source(url): return {'summary': SOURCE}
    assert await owner_bulletin_sources.collect(db, owner, number, source, AsyncMock()) == 'new'
    # Generate two alternating voices per story using source-grounded fixtures.
    def editor(stories):
        result = response(stories)
        for section in result['sections']:
            section['turns'].append({'speaker': 'Киберсенсей' if section['turns'][0]['speaker'] == 'Бендер' else 'Бендер',
                                     'text': 'Короткая реплика.', 'kind': 'comment', 'evidence': ''})
        return result
    assert await owner_bulletin.prepare(db, owner, number, AsyncMock(side_effect=editor)) == 'ready_for_review'
    item = await owner_bulletin.get(db, owner, number)
    assert await db.news_owner_trial_jobs.count_documents({}) == 0
    queue = VideoQueue(db.media, owner='test'); await queue.ensure(); await queue.acquire()
    with tempfile.TemporaryDirectory() as temp:
        def render_actual(request_id, folder):
            bulletin_job.run(folder / 'request.json', folder / 'work', imageio_ffmpeg.get_ffmpeg_exe(), ['fake'])
        shared = worker.JobStore(lambda *_: 'unused')
        news = news_jobs.NewsJobs(shared, Path(temp) / 'worker', render_actual, run_bulletin=render_actual)
        server = ThreadingHTTPServer(('127.0.0.1', 0), worker.handler_for(shared, 'test-secret', news))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            client = NewsWorkerClient(f'http://127.0.0.1:{server.server_port}', 'test-secret')
            with patch.object(owner_trial, 'runtime_enabled', AsyncMock(return_value=True)), \
                 patch.object(bulletin_dialogue_audio, 'request_one', return_value=(wav(), 1.0)) as tts:
                assert await owner_bulletin_admission.approve(db, owner, number, item['revision']) == 'queued'
                assert await owner_bulletin_admission.approve(db, owner, number, item['revision']) == 'already_processed'
                for _ in range(600):
                    await news_queue.run_idle_once(queue, db.news_owner_trial_jobs, client)
                    job = await db.news_owner_trial_jobs.find_one({})
                    if job['stage'] not in ('queued', 'submitting', 'running'):
                        break
                    await asyncio.sleep(.05)
                assert job['stage'] == 'ready_for_review', job
                assert tts.call_count == len(choices)
                assert all(len(call.args[1]['contents'][0]['parts']) == 2 for call in tts.call_args_list)
                video_bytes = []
                async def send_video(chat_id, file, **kwargs):
                    assert chat_id == owner
                    video_bytes.append(Path(file.path).read_bytes())
                    assert 'полноту речи' in kwargs['caption']
                    return SimpleNamespace(message_id=55)
                bot = SimpleNamespace(send_video=AsyncMock(side_effect=send_video))
                assert await review_delivery.run_once(db.news_owner_trial_jobs, db.news_owner_trial_candidates,
                                                      client, bot, owner) == 'review_sent'
                assert len(video_bytes) == 1 and len(video_bytes[0]) > 1000
                assert await review_delivery.run_once(db.news_owner_trial_jobs, db.news_owner_trial_candidates,
                                                      client, bot, owner) == 'idle'
                bot.send_video.assert_awaited_once()
        finally:
            server.shutdown(); server.server_close(); thread.join()
