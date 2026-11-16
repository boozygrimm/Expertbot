import base64
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image
try:
    from mongomock_motor import AsyncMongoMockClient
except ImportError:
    AsyncMongoMockClient = None
from news_prototype import (article_fetch, news_image_codec as codec, news_image_fetch,
                            news_picture, news_queue, owner_drafts, story_audio, telegram_post)
from tests.test_owner_drafts import URL, SOURCE, DRAFT


def picture_bytes(size=(120, 80), fmt='PNG'):
    out = BytesIO()
    Image.new('RGB', size, (255, 10, 20)).save(out, format=fmt)
    return out.getvalue()


def page(media, post='ExampleNews/102'):
    return (f'<div class="tgme_widget_message" data-post="{post}">'
            + media + f'<div class="tgme_widget_message_text js-message_text">{SOURCE}</div></div>').encode()


class TelegramMediaTests(unittest.TestCase):
    def test_exact_post_only_and_ignores_reply_and_neighbour_album(self):
        def photo(href, img):
            return f'<a href="{href}" class="tgme_widget_message_photo_wrap" style="background-image:url(\'{img}\')"></a>'
        right = 'https://cdn4.telesco.pe/file/right.jpg'
        body = (photo('https://t.me/ExampleNews/101', 'https://cdn4.telesco.pe/file/wrong.jpg') +
                '<div class="tgme_widget_message_reply">' + photo(URL, 'https://cdn4.telesco.pe/file/reply.jpg') + '</div>' +
                photo(URL + '?single', right))
        _, _, media = telegram_post.extract_post_info(page(body), 'examplenews/102')
        self.assertEqual(media, {'url': right, 'kind': 'photo'})
        with self.assertRaises(article_fetch.ArticleError):
            telegram_post.extract_post_info(page(body) + page(body), 'examplenews/102')

    def test_exact_video_thumbnail_and_no_link_preview(self):
        thumb = 'https://cdn4.telesco.pe/file/video.jpg'
        html = (f'<a class="tgme_widget_message_video_player" href="{URL}">'
                f'<i class="tgme_widget_message_video_thumb" style="background-image:url(\'{thumb}\')"></i></a>')
        self.assertEqual(telegram_post.extract_post_info(page(html), 'ExampleNews/102')[2],
                         {'url': thumb, 'kind': 'video_thumbnail'})
        self.assertIsNone(telegram_post.extract_post_info(page(html.replace(URL, 'https://t.me/ExampleNews/103')),
                                                         'ExampleNews/102')[2])
        self.assertIsNone(telegram_post.extract_post_info(page(html.replace('cdn4.telesco.pe', 'evil.example')),
                                                         'ExampleNews/102')[2])
        # External web-link previews have different classes and are never used.
        self.assertIsNone(telegram_post.extract_post_info(page(html.replace('tgme_widget_message_video_player', 'link_preview')),
                                                         'ExampleNews/102')[2])

    def test_media_url_is_strict_cdn(self):
        for url in ('https://cdn4.telesco.pe.evil.com/file/x', 'https://127.0.0.1/file/x',
                    'https://cdn4.telesco.pe/other/x', 'http://cdn4.telesco.pe/file/x',
                    'https://evil@cdn4.telesco.pe/file/x', 'https://cdn4.telesco.pe:8080/file/x'):
            with self.subTest(url=url), self.assertRaises(article_fetch.ArticleError): telegram_post.media_url(url)


class ImageCodecTests(unittest.TestCase):
    def test_bounded_metadata_free_jpeg_and_cutaway(self):
        value = codec.make_thumbnail(picture_bytes((1600, 1000)))
        raw = codec.decode_thumbnail(value)
        with Image.open(BytesIO(raw)) as image:
            self.assertEqual(image.format, 'JPEG')
            self.assertLessEqual(max(image.size), 800)
            self.assertFalse(image.getexif())
        panel = news_picture.prepare(value)
        frame = Image.new('RGB', (960, 540), 'black')
        limits = news_picture.window(20, 2)
        self.assertEqual(news_picture.apply(frame, panel, limits[0], limits).getpixel((450, 200)), (0,0,0))
        self.assertGreater(news_picture.apply(frame, panel, limits[0]+1, limits).getpixel((450, 200))[0], 200)
        self.assertEqual(news_picture.apply(frame, panel, limits[1], limits).getpixel((450, 200)), (0,0,0))
        self.assertIsNotNone(news_picture.window(2, 2))
        self.assertIsNone(news_picture.window(0, 2))

    def test_reject_invalid_or_giant(self):
        for val in ('not base64!', base64.b64encode(b'not a jpeg').decode(), 'A' * (codec.MAX_B64_CHARS+1)):
            with self.subTest(value=val[:15]), self.assertRaises(ValueError): codec.decode_thumbnail(val)
        with self.assertRaises(ValueError): codec.make_thumbnail(picture_bytes((4000, 3100)))
        with self.assertRaises(ValueError): codec.decode_thumbnail(base64.b64encode(picture_bytes()).decode())

    def test_script_preserves_and_validates_image(self):
        value = codec.make_thumbnail(picture_bytes())
        script = DRAFT['script'] | {'image_b64': value}
        self.assertEqual(story_audio.validate_script(script)['image_b64'], value)
        with self.assertRaises(ValueError): story_audio.validate_script(script | {'image_b64': 'bad!'})


class LocalRenderedPictureTest(unittest.TestCase):
    def test_synthetic_speech_and_picture_render_into_video(self):
        import subprocess
        try:
            import imageio_ffmpeg
        except ImportError:
            self.skipTest('imageio-ffmpeg optional')
        from news_prototype import story_pipeline
        from tests.test_podcast_video_trial import make_segment
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'job'
            value = codec.make_thumbnail(picture_bytes())
            script = DRAFT['script'] | {'image_b64': value}
            def synthesize(text, target, keys, start_index):
                make_segment(target, seconds=2)
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            with patch.object(story_audio.gemini_tts, 'synthesize', side_effect=synthesize):
                video = story_pipeline.run(script, path, ffmpeg, ['not-used'])
            self.assertTrue(video.is_file())
            self.assertEqual((path / 'news-image.jpg').read_bytes(), codec.decode_thumbnail(value))
            frame = Path(temp) / 'frame.png'
            subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-ss', '4', '-i', str(video),
                            '-frames:v', '1', str(frame)], check=True)
            with Image.open(frame) as result:
                r, g, b = result.convert('RGB').getpixel((450, 200))
            self.assertGreater(r, 180); self.assertLess(g, 60); self.assertLess(b, 60)


@unittest.skipIf(AsyncMongoMockClient is None, 'mongomock-motor optional')
class MediaFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        self.thumb = codec.make_thumbnail(picture_bytes())
        self.article = {'summary': SOURCE, 'media': {'url': 'https://cdn4.telesco.pe/file/good.jpg', 'kind': 'photo'}}

    async def test_image_goes_through_edit_approval_and_cas_remove(self):
        await owner_drafts.create(self.db, 123, 10, URL, '')
        fetch_img = AsyncMock(return_value=self.thumb)
        self.assertEqual(await owner_drafts.process_new(self.db, 123, 10, AsyncMock(return_value=self.article),
                                                       AsyncMock(return_value=DRAFT), fetch_img), 'draft_ready')
        item = await owner_drafts.get(self.db, 123, 10)
        self.assertEqual(item['script']['image_b64'], self.thumb)
        edit = owner_drafts.editable_message(10, URL, item['script']).replace('Проверь свою', 'Покажи свою')
        draft_id, rev, url, script = owner_drafts.parse_edit(edit)
        self.assertEqual(await owner_drafts.edit(self.db, 123, draft_id, rev, url, script), 'saved')
        item = await owner_drafts.get(self.db, 123, 10)
        self.assertEqual(item['script']['image_b64'], self.thumb)
        self.assertEqual(await owner_drafts.remove_image(self.db, 123, 10, rev), 'stale')
        rev = item['revision']
        self.assertEqual(await owner_drafts.remove_image(self.db, 123, 10, rev), 'saved')
        item = await owner_drafts.get(self.db, 123, 10)
        self.assertNotIn('image_b64', item['script'])
        self.assertNotEqual(item['revision'], rev)
        self.assertEqual(await owner_drafts.approve(self.db, 123, 10, rev), 'stale')
        self.assertEqual(await owner_drafts.approve(self.db, 123, 10, item['revision']), 'queued')

    async def test_missing_or_failed_image_does_not_stop_draft(self):
        for i, img in enumerate((AsyncMock(side_effect=article_fetch.ArticleError('x')), AsyncMock(return_value='bad!'))):
            await owner_drafts.create(self.db, 123, 10+i, URL, '')
            self.assertEqual(await owner_drafts.process_new(self.db, 123, 10+i, AsyncMock(return_value=self.article),
                                                           AsyncMock(return_value=DRAFT), img), 'draft_ready')
            self.assertNotIn('image_b64', (await owner_drafts.get(self.db, 123, 10+i))['script'])

    async def test_older_worker_never_receives_image_payload(self):
        jobs = self.db.jobs
        script = DRAFT['script'] | {'image_b64': self.thumb}
        await news_queue.enqueue_script(jobs, 'some-id', script)
        queue = type('Queue', (), {'head': AsyncMock(return_value=None)})()
        client = type('Client', (), {'supports_images': AsyncMock(return_value=False), 'submit': AsyncMock()})()
        await news_queue.run_idle_once(queue, jobs, client)
        client.submit.assert_not_awaited()
        self.assertEqual((await jobs.find_one({}))['stage'], 'queued')
        client.supports_images.return_value = True
        client.submit.return_value = {'status': 'running'}
        await news_queue.run_idle_once(queue, jobs, client)
        self.assertEqual(client.submit.await_args.args[1]['image_b64'], self.thumb)
        self.assertEqual((await jobs.find_one({}))['stage'], 'running')

    async def test_image_approved_through_http_worker_and_private_delivery(self):
        import asyncio
        from http.server import ThreadingHTTPServer
        import threading
        from types import SimpleNamespace
        from bender_queue import VideoQueue
        from news_prototype import owner_trial, review_delivery
        from news_prototype.news_worker_client import NewsWorkerClient
        from video_prototype import worker, news_jobs
        with tempfile.TemporaryDirectory() as temp:
            def render(job, folder):
                work = folder / 'work'; work.mkdir()
                (work / 'video.mp4').write_bytes(b'video')
                (work / 'status.json').write_text('{"stage":"ready_for_review"}')
            shared = worker.JobStore(lambda *_: 'normal')
            news = news_jobs.NewsJobs(shared, Path(temp) / 'worker', render)
            server = ThreadingHTTPServer(('127.0.0.1', 0), worker.handler_for(shared, 'secret', news))
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                await owner_drafts.create(self.db, 123, 10, URL, '')
                await owner_drafts.process_new(self.db, 123, 10, AsyncMock(return_value=self.article),
                                               AsyncMock(return_value=DRAFT), AsyncMock(return_value=self.thumb))
                item = await owner_drafts.get(self.db, 123, 10)
                self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
                self.assertEqual(await owner_drafts.approve(self.db, 123, 10, item['revision']), 'queued')
                queue = VideoQueue(self.db.media); await queue.ensure(); await queue.acquire()
                client = NewsWorkerClient(f'http://127.0.0.1:{server.server_port}', 'secret')
                self.assertTrue(await client.supports_images())
                with patch.dict('os.environ', {'NEWS_OWNER_TRIAL_ENABLED': '1',
                        **{flag: '0' for flag in owner_trial.OTHER_FLAGS}}):
                    for _ in range(100):
                        await owner_trial.dispatch_once(queue, self.db.news_owner_trial_jobs, client)
                        if await owner_trial.status(self.db) == 'ready_for_review': break
                        await asyncio.sleep(.01)
                self.assertEqual(await owner_trial.status(self.db), 'ready_for_review')
                job = await self.db.news_owner_trial_jobs.find_one({})
                stored = json.loads((Path(temp) / 'worker' / job['_id'] / 'request.json').read_text())
                self.assertEqual(stored['image_b64'], self.thumb)
                bot = SimpleNamespace(send_video=AsyncMock(return_value=SimpleNamespace(message_id=99)))
                self.assertEqual(await review_delivery.run_once(self.db.news_owner_trial_jobs,
                    self.db.news_owner_trial_candidates, client, bot, 123), 'review_sent')
                self.assertEqual(bot.send_video.await_args.args[0], 123)
                self.assertEqual(await review_delivery.run_once(self.db.news_owner_trial_jobs,
                    self.db.news_owner_trial_candidates, client, bot, 123), 'idle')
                bot.send_video.assert_awaited_once()
                await queue.release()
            finally:
                server.shutdown(); server.server_close(); thread.join()

    async def test_fetch_denies_redirect_and_checks_size(self):
        class Response:
            status=302; headers={'Location':'https://127.0.0.1/'}; content_type='image/jpeg'; content_length=1
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return False
        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return False
            def get(self,url,allow_redirects):
                self.url, self.redirects = url, allow_redirects
                return Response()
        session = Session()
        with patch.object(news_image_fetch.aiohttp, 'ClientSession', return_value=session):
            with self.assertRaises(article_fetch.ArticleError):
                await news_image_fetch.fetch_thumbnail('https://cdn4.telesco.pe/file/good.jpg')
        self.assertFalse(session.redirects)


if __name__ == '__main__': unittest.main()
