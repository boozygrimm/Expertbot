import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock
from http.server import ThreadingHTTPServer
try:
    from mongomock_motor import AsyncMongoMockClient
except ImportError:
    AsyncMongoMockClient = None
from bender_queue import VideoQueue
from video_prototype import worker, news_jobs
from news_prototype import editorial_queue, news_queue, review_delivery
from types import SimpleNamespace
from news_prototype.news_worker_client import NewsWorkerClient


@unittest.skipIf(AsyncMongoMockClient is None, 'mongomock-motor optional')
class EndToEndNewsTests(unittest.IsolatedAsyncioTestCase):
    async def test_candidate_to_recoverable_video_with_no_real_tts_or_delivery(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            def fake_render(job,folder):
                work=folder/'work'; work.mkdir()
                (work/'video.mp4').write_bytes(b'test-video')
                (work/'status.json').write_text('{"stage":"ready_for_review"}')
            shared=worker.JobStore(lambda *args:'old-file-id')
            news=news_jobs.NewsJobs(shared,root/'worker',fake_render,run_story_studio=fake_render)
            server=ThreadingHTTPServer(('127.0.0.1',0),worker.handler_for(shared,'secret',news))
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            try:
                db=AsyncMongoMockClient().test
                queue=VideoQueue(db.media,owner='tester'); await queue.ensure(); await queue.acquire()
                candidate={'_id':'article','status':'candidate','title':'Тест',
                           'summary':'Компания рассказала о выпуске новой технологии. ' * 3,'collected_at':'2026'}
                await db.candidates.insert_one(candidate)
                draft=AsyncMock(return_value={'decision':'draft','verification':'needs_review','evidence':[],
                    'script':{'turns':[{'speaker':'Бендер','text':'Тест'},
                                       {'speaker':'Киберсенсей','text':'Комментарий'}]}})
                self.assertEqual(await editorial_queue.run_once(db.candidates,db.jobs,draft),'draft_ready')
                self.assertEqual(await editorial_queue.run_once(db.candidates,db.jobs,draft),'render_queued')
                client=NewsWorkerClient(f'http://127.0.0.1:{server.server_port}','secret')
                await news_queue.run_idle_once(queue,db.jobs,client)
                for _ in range(100):
                    await news_queue.run_idle_once(queue,db.jobs,client)
                    job=await db.jobs.find_one({})
                    if job['stage']=='ready_for_review': break
                    await asyncio.sleep(.01)
                self.assertEqual(job['stage'],'ready_for_review')
                output=root/'download.mp4'
                self.assertEqual((await client.download(job['request_id'],output))[0],10)
                self.assertEqual(output.read_bytes(),b'test-video')
                bot=SimpleNamespace(send_video=AsyncMock(return_value=SimpleNamespace(message_id=123)))
                self.assertEqual(await review_delivery.run_once(db.jobs, db.candidates, client, bot, 987), 'review_sent')
                self.assertEqual((await db.jobs.find_one({}))['review_message_id'],123)
                self.assertEqual(bot.send_video.await_args.args[0],987)
                self.assertEqual(draft.await_count,1)
            finally:
                server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
