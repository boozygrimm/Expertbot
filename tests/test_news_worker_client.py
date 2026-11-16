import hashlib
from pathlib import Path
import tempfile
import unittest
import uuid
from aiohttp import web
from news_prototype.news_worker_client import NewsWorkerClient, NewsBusy, NewsUncertain, NewsWorkerError


class NewsWorkerClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.mode = 'normal'
        self.job = str(uuid.uuid4())
        async def post(request):
            self.requests.append(('post', request.headers.get('Authorization')))
            if self.mode == 'busy': return web.json_response({'error':'busy'},status=409)
            if self.mode == 'uncertain': return web.Response(status=503)
            return web.json_response({'request_id':self.job,'status':'running'},status=202)
        async def get(request):
            self.requests.append(('get',request.match_info['job']))
            if self.mode == 'missing': return web.Response(status=404)
            return web.json_response({'request_id':request.match_info['job'],'status':'ready'})
        async def video(request):
            if self.mode == 'missing': return web.Response(status=404)
            return web.Response(body=b'video data',content_type='video/mp4')
        async def health(request):
            if self.mode == 'unsupported': return web.json_response({'status':'ok','capabilities':[]})
            return web.json_response({'status':'ok','capabilities':['news-story-v1']})
        app=web.Application(); app.router.add_get('/health',health); app.router.add_post('/news-jobs',post); app.router.add_post('/news-digests',post); app.router.add_get('/news-jobs/{job}',get)
        app.router.add_get('/news-jobs/{job}/video',video)
        self.runner=web.AppRunner(app); await self.runner.setup(); site=web.TCPSite(self.runner,'127.0.0.1',0); await site.start()
        port=site._server.sockets[0].getsockname()[1]
        self.client=NewsWorkerClient(f'http://127.0.0.1:{port}','secret')

    async def asyncTearDown(self): await self.runner.cleanup()

    async def test_news_capability_must_be_explicit(self):
        self.assertTrue(await self.client.supports_news())
        self.mode='unsupported'
        self.assertFalse(await self.client.supports_news())

    async def test_submission_once_and_authenticated(self):
        state=await self.client.submit(self.job,{'turns': []})
        self.assertEqual(state['status'],'running'); self.assertEqual(self.requests,[('post','Bearer secret')])

    async def test_uncertain_response_checks_same_id_without_retry(self):
        self.mode='uncertain'
        result=await self.client.submit(self.job,{'turns':[]})
        self.assertEqual(result['status'],'ready')
        self.assertEqual(self.requests,[('post','Bearer secret'),('get',self.job)])

    async def test_missing_after_ambiguous_send_is_not_retried(self):
        # Simulate a lost response followed by a 404.
        from unittest.mock import patch, AsyncMock
        with patch.object(self.client, '_status', new=AsyncMock(return_value=None)):
            self.mode='uncertain'
            with self.assertRaises(NewsUncertain): await self.client.submit(self.job,{})
        self.assertEqual(self.requests,[('post','Bearer secret')])

    async def test_digest_uncertain_queries_same_id_without_reposting(self):
        self.mode='uncertain'
        result=await self.client.submit_digest(self.job,[str(uuid.uuid4()),str(uuid.uuid4())])
        self.assertEqual(result['status'],'ready')
        self.assertEqual(self.requests,[('post','Bearer secret'),('get',self.job)])

    async def test_busy_and_download(self):
        self.mode='busy'
        with self.assertRaises(NewsBusy): await self.client.submit(self.job,{})
        self.mode='normal'
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'result.mp4'
            size,sha=await self.client.download(self.job,output)
            self.assertEqual((size,sha),(10,hashlib.sha256(b'video data').hexdigest()))
            self.assertEqual(output.read_bytes(),b'video data')
            with self.assertRaises(ValueError): await self.client.download(self.job,output)
            self.mode='missing'
            with self.assertRaises(NewsWorkerError): await self.client.download(self.job,Path(tmp)/'other.mp4')
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()),['result.mp4'])


if __name__ == '__main__': unittest.main()
