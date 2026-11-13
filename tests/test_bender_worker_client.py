import unittest
import uuid
from aiohttp import web
from bender_worker_client import (
    BenderWorkerClient, WorkerBusy, WorkerFailed, WorkerTimeout, WorkerUnavailable, validate_url,
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
                return web.json_response({'error': 'busy'}, status=409)
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
            if self.mode == 'failed':
                return web.json_response({'request_id': job_id, 'status': 'failed'})
            if self.mode == 'slow' or self.poll_count == 1 and self.mode == 'ok':
                return web.json_response({'request_id': job_id, 'status': 'running'})
            return web.json_response({'request_id': job_id, 'status': 'done', 'file_id': 'cached-id'})
        app = web.Application()
        app.router.add_post('/jobs', post)
        app.router.add_get('/jobs/{job_id}', get)
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

    async def test_busy_and_failed(self):
        self.mode = 'busy'
        with self.assertRaises(WorkerBusy):
            await self.client.render('Привет')
        self.mode = 'failed'
        with self.assertRaises(WorkerFailed):
            await self.client.render('Привет')

    async def test_post_disconnect_does_not_repeat_job(self):
        self.mode = 'ambiguous'
        self.assertEqual(await self.client.render('Привет'), 'cached-id')
        self.assertEqual(self.post_count, 1)

    async def test_never_follow_redirect(self):
        self.mode = 'redirect'
        with self.assertRaises(WorkerUnavailable):
            await self.client.render('Привет')

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
