import asyncio
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
try:
    from mongomock_motor import AsyncMongoMockClient
except ImportError:
    AsyncMongoMockClient = None
from PIL import Image
from news_prototype import owner_drafts, owner_program, owner_trial, news_queue, news_picture, news_image_codec
from tests.test_owner_drafts import URL, SOURCE, DRAFT, SCRIPT


@unittest.skipIf(AsyncMongoMockClient is None, 'mongomock-motor optional')
class ProgramTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        self.flags = patch.dict(os.environ, {'NEWS_OWNER_TRIAL_ENABLED': '1', 'NEWS_OWNER_DRAFT_ENABLED': '1',
                         **{k:'0' for k in owner_trial.OTHER_FLAGS}})
        self.flags.start(); self.addCleanup(self.flags.stop)

    async def ready(self, draft_id, image=False):
        url = f'https://t.me/ExampleNews/{draft_id}'
        await owner_drafts.create(self.db, 123, draft_id, url, '')
        article = {'summary': SOURCE}
        fetch_img = None
        if image:
            data = BytesIO(); Image.new('RGB',(120,240),'red').save(data, format='PNG')
            value = news_image_codec.make_thumbnail(data.getvalue())
            article['media'] = {'url':'https://cdn4.telesco.pe/file/x', 'kind':'photo'}
            fetch_img = AsyncMock(return_value=value)
        await owner_drafts.process_new(self.db, 123, draft_id, AsyncMock(return_value=article),
                                       AsyncMock(return_value=DRAFT), fetch_img)
        return await owner_drafts.get(self.db, 123, draft_id)

    async def ready_program(self):
        await self.ready(10, image=True); await self.ready(11)
        self.assertTrue(await owner_program.create(self.db, 123, 50, [10,11]))
        return await owner_program.get(self.db, 123, 50)

    def test_batch_parsing_and_child_ids(self):
        urls, accent = owner_drafts.prepare_many_input('/news_prepare_many\nhttps://t.me/A/1\nhttps://t.me/B/2\nАкцент: суть')
        self.assertEqual(urls, ['https://t.me/A/1', 'https://t.me/B/2']); self.assertEqual(accent, 'суть')
        ids = owner_drafts.batch_draft_ids(5961, 2)
        self.assertEqual(ids, [100_000_059_611, 100_000_059_612])
        self.assertTrue(all(i > 2**31 for i in ids))
        for bad in ('/news_prepare_many\nhttps://t.me/A/1',
                    '/news_prepare_many\nhttps://t.me/A/1\nhttps://t.me/A/1',
                    '/news_prepare_many\nhttps://t.me/A/1\nhttps://127.0.0.1/',
                    '/news_prepare_many\n' + '\n'.join(f'https://t.me/A/{i}' for i in range(1, 6))):
            with self.subTest(bad=bad), self.assertRaises(ValueError): owner_drafts.prepare_many_input(bad)

    async def test_mongo_int64_batch_ids_are_accepted_by_real_approval_handler(self):
        # PyMongo decodes BSON int64 as bson.int64.Int64, unlike mongomock.
        # Reproduce the production round trip for the exact mixed-ID case.
        from bson import BSON
        from bson.int64 import Int64
        from handlers import news_owner_drafts as handler
        large_id = 100000059802
        await self.ready(5987); await self.ready(large_id, image=True)
        await owner_program.create(self.db, 123, 5993, [5987, large_id])
        saved = await owner_program.get(self.db, 123, 5993)
        round_tripped = BSON.encode({'stories': saved['stories']})
        stories = BSON(round_tripped).decode()['stories']
        self.assertIs(type(stories[1]['draft_id']), Int64)
        await self.db.news_owner_programs.update_one({'_id': saved['_id']}, {'$set': {'stories': stories}})
        reread = await owner_program.get(self.db, 123, 5993)
        self.assertIs(type(reread['stories'][1]['draft_id']), Int64)
        self.assertEqual(owner_program.revision(reread['stories']), saved['revision'])
        client = SimpleNamespace(supports_programs=AsyncMock(return_value=True))
        text = f'/news_program_approve 5993 {saved["revision"]}'
        message = SimpleNamespace(from_user=SimpleNamespace(id=123), chat=SimpleNamespace(type='private'),
                                  text=text, message_id=6000, answer=AsyncMock())
        with patch.object(handler, 'ADMIN_ID', 123), patch.object(handler, 'db', SimpleNamespace(db=self.db)), \
             patch('news_prototype.news_worker_client.NewsWorkerClient', return_value=client):
            await handler.approve_program(message)
            self.assertIn('поставлен', message.answer.await_args.args[0])
            await handler.approve_program(message)
            self.assertIn('повторный рендер не запущен', message.answer.await_args.args[0])
        job = await self.db.news_owner_trial_jobs.find_one({})
        self.assertEqual(job['kind'], 'program')
        self.assertEqual(job['stage'], 'queued')
        self.assertEqual(len(job['scripts']), 2)
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 1)

    async def test_internal_value_error_is_not_reported_as_bad_command(self):
        from handlers import news_owner_drafts as handler
        program = await self.ready_program()
        text = f'/news_program_approve 50 {program["revision"]}'
        message = SimpleNamespace(from_user=SimpleNamespace(id=123), chat=SimpleNamespace(type='private'),
                                  text=text, message_id=6000, answer=AsyncMock())
        client = SimpleNamespace(supports_programs=AsyncMock(return_value=True))
        with patch.object(handler, 'ADMIN_ID', 123), patch.object(handler, 'db', SimpleNamespace(db=self.db)), \
             patch.object(owner_program, 'approve', side_effect=ValueError('internal')), \
             patch('news_prototype.news_worker_client.NewsWorkerClient', return_value=client):
            await handler.approve_program(message)
        self.assertIn('Не удалось проверить данные выпуска', message.answer.await_args.args[0])
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    def test_draft_key_accepts_bson_int64_but_not_bool(self):
        from bson.int64 import Int64
        self.assertEqual(owner_drafts._key(Int64(123), Int64(100000059802)), 'owner-draft:123:100000059802')
        for owner, draft in ((True, 1), (1, True), (1.0, 1), (1, 1.0), (0, 1), (1, -1)):
            with self.subTest(owner=owner, draft=draft), self.assertRaises(ValueError):
                owner_drafts._key(owner, draft)

    async def test_review_snapshot_no_render_until_approval(self):
        program = await self.ready_program()
        self.assertEqual([s['draft_id'] for s in program['stories']], [10,11])
        self.assertIn('image_b64', program['stories'][0]['script'])
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
        self.assertIsNone(await owner_program.get(self.db, 999, 50))
        self.assertFalse(await owner_program.create(self.db, 123, 50, [10,11]))
        self.assertEqual(await owner_program.approve(self.db, 123, 50, 'f'*64), 'stale')
        self.assertEqual(await owner_program.approve(self.db, 123, 50, program['revision']), 'queued')
        self.assertEqual(await owner_program.approve(self.db, 123, 50, program['revision']), 'already_queued')
        job = await self.db.news_owner_trial_jobs.find_one({})
        self.assertEqual(job['kind'], 'program')
        self.assertEqual(job['scripts'], [s['script'] for s in program['stories']])
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 1)
        self.assertEqual(await owner_trial.status(self.db), 'queued')

    async def test_old_snapshot_cannot_approve_after_edit(self):
        program = await self.ready_program()
        item = await owner_drafts.get(self.db, 123, 11)
        edited = dict(SCRIPT, ticker='ДРУГОЙ ЗАГОЛОВОК')
        self.assertEqual(await owner_drafts.edit(self.db, 123, 11, item['revision'], item['url'], edited), 'saved')
        self.assertEqual(await owner_program.approve(self.db, 123, 50, program['revision']), 'stale_draft')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
        self.assertTrue(await owner_program.create(self.db, 123, 51, [10,11]))
        newer = await owner_program.get(self.db, 123, 51)
        self.assertNotEqual(program['revision'], newer['revision'])
        self.assertEqual(await owner_program.approve(self.db, 123, 51, newer['revision']), 'queued')

    async def test_duplicate_approval_and_ambiguous_admission_do_not_repeat(self):
        program = await self.ready_program()
        original = owner_trial.admit_program
        async def lost(*args):
            await original(*args); raise RuntimeError('lost')
        with patch.object(owner_trial, 'admit_program', side_effect=lost) as admit:
            result = await asyncio.gather(*(owner_program.approve(self.db, 123, 50, program['revision']) for _ in range(2)))
            self.assertIn('approval_uncertain', result)
            self.assertNotIn('queued', result)
            admit.assert_awaited_once()
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 1)
        self.assertEqual((await owner_program.get(self.db, 123, 50))['stage'], 'approval_uncertain')

    async def test_previous_completed_slot_reopens_only_on_approval(self):
        oldscript = dict(SCRIPT, ticker='ПРЕДЫДУЩИЙ ВЫПУСК')
        await owner_trial.admit(self.db, 123, URL, oldscript)
        job = await self.db.news_owner_trial_jobs.find_one({})
        await self.db.news_owner_trial_jobs.update_one({'_id':job['_id']}, {'$set': {'stage':'review_sent', 'review_message_id':9}})
        program = await self.ready_program()
        self.assertEqual(await owner_trial.status(self.db), 'review_sent')
        self.assertEqual(await owner_program.approve(self.db, 123, 50, program['revision']), 'queued')
        self.assertEqual(await owner_trial.status(self.db), 'queued')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 2)
        self.assertEqual(await owner_trial.open_next(self.db, 123, job['_id']), 'not_allowed')

    async def test_busy_slot_blocks_program_without_losing_snapshot(self):
        await owner_trial.admit(self.db, 123, URL, SCRIPT)
        program = await self.ready_program()
        self.assertEqual(await owner_program.approve(self.db, 123, 50, program['revision']), 'trial_busy')
        self.assertEqual((await owner_program.get(self.db, 123, 50))['stage'], 'ready_for_review')
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 1)

    async def test_program_waits_for_ordinary_media(self):
        program = await self.ready_program()
        await owner_program.approve(self.db, 123, 50, program['revision'])
        queue = SimpleNamespace(head=AsyncMock(return_value={'kind': 'circle'}))
        client = SimpleNamespace(supports_programs=AsyncMock(return_value=True), submit_program=AsyncMock())
        await news_queue.run_idle_once(queue, self.db.news_owner_trial_jobs, client)
        client.supports_programs.assert_not_awaited()
        client.submit_program.assert_not_awaited()
        self.assertEqual(await owner_trial.status(self.db), 'queued')

    async def test_old_worker_capability_blocks_without_submission(self):
        program = await self.ready_program()
        self.assertEqual(await owner_program.approve(self.db, 123, 50, program['revision']), 'queued')
        queue = SimpleNamespace(head=AsyncMock(return_value=None))
        client = SimpleNamespace(supports_programs=AsyncMock(return_value=False), submit_program=AsyncMock())
        await news_queue.run_idle_once(queue, self.db.news_owner_trial_jobs, client)
        client.submit_program.assert_not_awaited()
        self.assertEqual(await owner_trial.status(self.db), 'queued')
        client.supports_programs.return_value = True
        client.submit_program.return_value = {'status': 'running'}
        await news_queue.run_idle_once(queue, self.db.news_owner_trial_jobs, client)
        self.assertEqual(client.submit_program.await_args.args[1], [s['script'] for s in program['stories']])
        self.assertEqual(await owner_trial.status(self.db), 'running')

    async def test_http_program_and_owner_only_final_delivery(self):
        from video_prototype import worker, news_jobs
        from bender_queue import VideoQueue
        from news_prototype.news_worker_client import NewsWorkerClient
        from news_prototype import review_delivery
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            def run_program(request_id, folder):
                calls.append(json.loads((folder/'request.json').read_text())['scripts'])
                work = folder/'work'; work.mkdir(); (work/'video.mp4').write_bytes(b'program')
                (work/'status.json').write_text('{"stage":"ready_for_review"}')
            store = worker.JobStore(lambda *_:'normal')
            news = news_jobs.NewsJobs(store, Path(tmp)/'news', lambda *_: None, run_program=run_program)
            server = ThreadingHTTPServer(('127.0.0.1',0), worker.handler_for(store, 'secret', news))
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                program = await self.ready_program()
                client = NewsWorkerClient(f'http://127.0.0.1:{server.server_port}', 'secret')
                self.assertTrue(await client.supports_programs())
                self.assertEqual(await owner_program.approve(self.db, 123, 50, program['revision']), 'queued')
                queue = VideoQueue(self.db.media); await queue.ensure(); await queue.acquire()
                for _ in range(100):
                    await owner_trial.dispatch_once(queue, self.db.news_owner_trial_jobs, client)
                    if await owner_trial.status(self.db) == 'ready_for_review': break
                    await asyncio.sleep(.01)
                self.assertEqual(await owner_trial.status(self.db), 'ready_for_review')
                self.assertEqual(calls, [[s['script'] for s in program['stories']]])
                bot = SimpleNamespace(send_video=AsyncMock(return_value=SimpleNamespace(message_id=42)))
                self.assertEqual(await review_delivery.run_once(self.db.news_owner_trial_jobs,
                    self.db.news_owner_trial_candidates, client, bot, 123), 'review_sent')
                self.assertEqual(bot.send_video.await_args.args[0], 123)
                self.assertIn('Выпуск', bot.send_video.await_args.kwargs['caption'])
                self.assertEqual(await review_delivery.run_once(self.db.news_owner_trial_jobs,
                    self.db.news_owner_trial_candidates, client, bot, 123), 'idle')
                bot.send_video.assert_awaited_once()
                _, finished = await owner_trial.status_info(self.db)
                self.assertEqual(await owner_trial.open_next(self.db, 123, finished), 'opened')
                self.assertEqual(await owner_program.approve(self.db, 123, 50, program['revision']), 'already_queued')
                await queue.release()
            finally:
                server.shutdown(); server.server_close(); thread.join()

    async def test_batch_handler_and_capability_gate(self):
        from handlers import news_owner_drafts as handler
        def msg(text, user=123, chat='private', mid=6000):
            return SimpleNamespace(from_user=SimpleNamespace(id=user),chat=SimpleNamespace(type=chat),
                message_id=mid,text=text,answer=AsyncMock(),answer_photo=AsyncMock())
        make = AsyncMock(return_value=DRAFT)
        fetch = AsyncMock(return_value={'summary':SOURCE})
        with patch.object(handler, 'ADMIN_ID', 123), patch.object(handler, 'db', SimpleNamespace(db=self.db)), \
             patch.object(handler, 'make_draft', make), patch.object(handler.article_fetch, 'fetch_article', fetch):
            incoming = msg('/news_prepare_many\nhttps://t.me/A/1\nhttps://t.me/B/2')
            await handler.prepare_many(incoming)
            ids = owner_drafts.batch_draft_ids(6000,2)
            self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
            self.assertEqual(make.await_count, 2)
            for draft_id in ids:
                self.assertEqual((await owner_drafts.get(self.db,123,draft_id))['stage'], 'draft_ready')
            await handler.prepare_many(incoming)
            self.assertEqual(make.await_count, 2)
            issue = msg('/news_program ' + ' '.join(map(str,ids)), mid=6001)
            await handler.create_program(issue)
            p = await owner_program.get(self.db,123,6001)
            self.assertEqual(p['stage'], 'ready_for_review')
            self.assertTrue(any(c.args[0].startswith('Если все сюжеты') for c in issue.answer.await_args_list))
            approval = msg(f'/news_program_approve 6001 {p["revision"]}', mid=6002)
            with patch('news_prototype.news_worker_client.NewsWorkerClient', side_effect=ValueError('old')):
                await handler.approve_program(approval)
            self.assertIn('не подтвердил', approval.answer.await_args.args[0])
            self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
            unauthorized = msg('/news_program ' + ' '.join(map(str,ids)), user=999, mid=6003)
            await handler.create_program(unauthorized); unauthorized.answer.assert_not_awaited()


class ProgramWorkerTests(unittest.TestCase):
    def test_program_job_stops_at_first_failure_without_assembling(self):
        from news_prototype import program_job
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/str(uuid.uuid4()); folder.mkdir()
            request = folder/'request.json'; request.write_text(json.dumps({'scripts':[SCRIPT,SCRIPT]}))
            def run(script, output, ffmpeg, keys):
                if output.name == 'story-2': raise RuntimeError('fail')
                output.mkdir(); video = output/'video.mp4'; video.write_bytes(b'x'); return video
            with patch.object(program_job.story_pipeline, 'run', side_effect=run) as mock_run, \
                 patch.object(program_job.digest_video, 'assemble') as assemble:
                with self.assertRaises(RuntimeError): program_job.run(request, folder/'work', 'ffmpeg', ['unused'])
            self.assertEqual(mock_run.call_count,2); assemble.assert_not_called()
            self.assertEqual(json.loads((folder/'work'/'status.json').read_text())['stage'],'render_failed')
            self.assertFalse((folder/'work'/'video.mp4').exists())
            with self.assertRaises(FileExistsError): program_job.run(request, folder/'work', 'ffmpeg', ['unused'])

    def test_local_program_renders_and_assembles_synthetic_speech(self):
        try:
            import imageio_ffmpeg
        except ImportError:
            self.skipTest('imageio-ffmpeg optional')
        from news_prototype import program_job, story_audio
        from tests.test_podcast_video_trial import make_segment
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/str(uuid.uuid4()); folder.mkdir()
            from PIL import ImageDraw
            portrait = Image.new('RGB', (100, 200), 'red')
            ImageDraw.Draw(portrait).rectangle((0, 100, 99, 199), fill='blue')
            image = BytesIO(); portrait.save(image, format='PNG')
            first = SCRIPT | {'image_b64': news_image_codec.make_thumbnail(image.getvalue()),
                              'image_regions': [[0, 550, 1000, 1000], [0, 0, 1000, 450]]}
            second = SCRIPT | {'ticker':'ВТОРАЯ НОВОСТЬ'}
            request = folder/'request.json'; request.write_text(json.dumps({'scripts':[first,second]}))
            def synthesize(text, target, keys, start_index):
                make_segment(target, seconds=6 if target.parent.name == 'story-1' else 1)
            with patch.object(story_audio.gemini_tts, 'synthesize', side_effect=synthesize) as synth:
                video = program_job.run(request, folder/'work', imageio_ffmpeg.get_ffmpeg_exe(), ['not-used'])
            self.assertEqual(synth.call_count, 4)
            self.assertGreater(video.stat().st_size, 0)
            self.assertTrue((folder/'work'/'story-1'/'video.mp4').exists())
            self.assertTrue((folder/'work'/'story-2'/'video.mp4').exists())
            self.assertEqual(json.loads((folder/'work'/'status.json').read_text())['stage'], 'ready_for_review')
            # Sample the final assembled MP4, not just the prepared panels.
            import subprocess
            from news_prototype import story_video, podcast_video_trial, combined_trial
            audio, turns, _ = story_video.load_story(folder/'work'/'story-1')
            timeline = podcast_video_trial.dialogue_timeline_from_parts(audio, turns)
            fps = podcast_video_trial.bender.FPS
            start, end = news_picture.window(len(timeline[1]) / fps, combined_trial.INTRO_FRAMES / fps)
            self.assertGreaterEqual(end - start, news_picture.MIN_ZOOM_SECONDS)
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            shots = []
            for fraction in (.10, .34, .64, .90):
                at = start + (end - start) * fraction
                frame = subprocess.check_output([ffmpeg, '-hide_banner', '-loglevel', 'error',
                    '-ss', str(at), '-i', str(video), '-frames:v', '1', '-f', 'image2pipe', '-vcodec', 'png', '-'])
                with Image.open(BytesIO(frame)) as shot:
                    shots.append(shot.convert('RGB'))
            # At x=320 the full portrait is absent but the enlarged halves reach it.
            self.assertLess(shots[0].getpixel((320, 249))[0], 150)
            # The model-selected order is intentionally opposite the fallback.
            self.assertGreater(shots[1].getpixel((320, 249))[2], 150)
            self.assertGreater(shots[2].getpixel((320, 249))[0], 150)
            self.assertLess(shots[3].getpixel((320, 249))[0], 150)
            # The complete picture, including both ends, returns after details.
            self.assertGreater(shots[3].getpixel((467, 70))[0], 150)
            self.assertGreater(shots[3].getpixel((467, 428))[2], 150)

    def test_portrait_larger_and_uncropped_against_blurred_background(self):
        img = Image.new('RGB',(100,200),'green')
        for x in range(100):
            for y in range(15):
                img.putpixel((x,y),(255,0,0)); img.putpixel((x,199-y),(0,0,255))
        stream=BytesIO(); img.save(stream,format='PNG')
        panel = news_picture.prepare(news_image_codec.make_thumbnail(stream.getvalue()))
        self.assertEqual(panel.size, (685,388))
        r,g,b = panel.getpixel((342,15)); self.assertGreater(r,150)
        r,g,b = panel.getpixel((342,373)); self.assertGreater(b,150)
        self.assertGreater(panel.getpixel((60,194))[1], 10)
        self.assertEqual(news_picture.opacity_at(0,None),0)


if __name__ == '__main__': unittest.main()
