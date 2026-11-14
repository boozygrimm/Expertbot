import unittest
import uuid
from aiohttp import web
from bender_worker_client import (
    BenderWorkerClient, WorkerBusy, WorkerFailed, WorkerTimeout, WorkerUnavailable, WorkerUploadUncertain, validate_url,
)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.job = None
        self.post_count = 0
        self.poll_count = 0
        self.mode = 'ok'
        async def post(request):
            self.post_count += 1
            self.assertEqual(request.headers.get('Authorization'), 'Bearer test-secret')
            if self.mode == 'busy':
                return web.json_response({'error': 'Воркер занят'}, status=409)
            if self.mode == 'conflict':
                return web.json_response({'error': 'request_id уже использован для другого текста'}, status=409)
            if self.mode == 'redirect':
                return web.Response(status=302, headers={'Location': '/other'})
            self.job = await request.json()
            if self.mode == 'ambiguous':
                request.transport.close()
                return web.Response()
            return web.json_response({'request_id': self.job['request_id'], 'status': 'running'}, status=202)
        async def get(request):
            self.poll_count += 1
            job_id = request.match_info['job_id']
            if self.mode == 'missing':
                return web.json_response({'error': 'not_found'}, status=404)
            if self.mode == 'failed':
                return web.json_response({'request_id': job_id, 'status': 'failed'})
            if self.mode == 'uncertain':
                return web.json_response({'request_id': job_id, 'status': 'upload_uncertain'})
            if self.mode == 'slow' or self.poll_count == 1 and self.mode == 'ok':
                return web.json_response({'request_id': job_id, 'status': 'running'})
            return web.json_response({'request_id': job_id, 'status': 'done', 'file_id': 'cached-id'})
        async def video(request):
            self.assertEqual(request.headers.get('Authorization'), 'Bearer test-secret')
            if self.mode == 'missing':
                return web.Response(status=404)
            if self.mode == 'redirect':
                return web.Response(status=302, headers={'Location': '/other'})
            if self.mode == 'large':
                return web.Response(body=b'x' * (50 * 1024 * 1024 + 1), content_type='video/mp4')
            return web.Response(body=b'video bytes', content_type='video/mp4')
        async def health(request):
            self.assertEqual(request.headers.get('Authorization'), 'Bearer test-secret')
            return web.json_response({'status': 'ok', 'capabilities': [] if self.mode == 'old-worker' else ['animated-sign-v1']})
        app = web.Application()
        app.router.add_get('/health', health)
        app.router.add_post('/jobs', post)
        app.router.add_get('/jobs/{job_id}', get)
        app.router.add_get('/jobs/{job_id}/video', video)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.client = BenderWorkerClient(f'http://127.0.0.1:{port}', 'test-secret',
                                         total_timeout=1, request_timeout=.3, poll_interval=.02)

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def test_job_and_polling(self):
        self.assertEqual(await self.client.render('Привет'), 'cached-id')
        self.assertEqual(self.post_count, 1)
        self.assertGreaterEqual(self.poll_count, 2)
        self.assertEqual(self.job['text'], 'Привет')
        self.assertEqual(set(self.job), {'text', 'request_id'})

    async def test_sign_is_optional_and_separate_from_speech(self):
        self.assertEqual(await self.client.render('Озвучка', sign='Космос в отпуске'), 'cached-id')
        self.assertEqual(self.job['text'], 'Озвучка')
        self.assertEqual(self.job['sign'], 'КОСМОС В ОТПУСКЕ')
        self.poll_count = 0
        self.assertEqual(await self.client.render('Озвучка', sign='😀'), 'cached-id')
        self.assertNotIn('sign', self.job)
        self.mode = 'old-worker'
        self.assertEqual(await self.client.render('Озвучка', sign='КОСМОС'), 'cached-id')
        self.assertNotIn('sign', self.job)

    async def test_busy_and_failed(self):
        self.mode = 'busy'
        with self.assertRaises(WorkerBusy):
            await self.client.render('Привет')
        self.mode = 'failed'
        with self.assertRaises(WorkerFailed):
            await self.client.render('Привет')

    async def test_conflict_is_not_safe_busy(self):
        self.mode = 'conflict'
        with self.assertRaises(WorkerUnavailable):
            await self.client.render('Привет')
        self.assertEqual(self.post_count, 1)

    async def test_resume_only_reads_existing_uuid(self):
        self.mode = 'ok'
        job_id = str(uuid.uuid4())
        self.assertEqual(await self.client.resume(job_id), 'cached-id')
        self.assertEqual(self.post_count, 0)
        self.mode = 'missing'
        with self.assertRaises(WorkerUnavailable):
            await self.client.resume(job_id)
        self.assertEqual(self.post_count, 0)

    async def test_post_disconnect_does_not_repeat_job(self):
        self.mode = 'ambiguous'
        self.assertEqual(await self.client.render('Привет'), 'cached-id')
        self.assertEqual(self.post_count, 1)

    async def test_never_follow_redirect(self):
        self.mode = 'redirect'
        with self.assertRaises(WorkerUnavailable):
            await self.client.render('Привет')

    async def test_uncertain_job_fetches_only_same_uuid_without_resubmitting(self):
        self.mode = 'uncertain'
        job = str(uuid.uuid4())
        with self.assertRaises(WorkerUploadUncertain):
            await self.client.resume(job)
        self.assertEqual(await self.client.fetch_uncertain_video(job), b'video bytes')
        self.assertEqual(self.post_count, 0)

    async def test_video_fetch_rejects_missing_redirect_and_oversized(self):
        job = str(uuid.uuid4())
        for mode in ('missing', 'redirect', 'large'):
            self.mode = mode
            with self.subTest(mode=mode), self.assertRaises(WorkerUnavailable):
                await self.client.fetch_uncertain_video(job)
        with self.assertRaises(ValueError):
            await self.client.fetch_uncertain_video('not-a-uuid')

    async def test_timeout(self):
        self.mode = 'slow'
        self.client.total_timeout = .1
        with self.assertRaises(WorkerTimeout):
            await self.client.render('Привет')

    async def test_input_validation(self):
        with self.assertRaises(ValueError):
            await self.client.render('  ')
        with self.assertRaises(ValueError):
            await self.client.render('x' * 481)
        with self.assertRaises(ValueError):
            await self.client.render('текст', request_id='not-uuid')
        self.assertEqual(self.post_count, 0)


class URLTests(unittest.TestCase):
    def test_valid_url(self):
        self.assertEqual(validate_url('https://worker.example/'), 'https://worker.example')
        self.assertEqual(validate_url('http://127.0.0.1:8088'), 'http://127.0.0.1:8088')

    def test_bad_urls(self):
        for url in ('http://example.org', 'https://user:pass@example.org',
                    'https://example.org/path', 'https://example.org/?x=1', 'file:///tmp/a'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)
