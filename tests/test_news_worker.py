import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer
from video_prototype import worker, news_jobs


class NewsWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.started, self.release = threading.Event(), threading.Event()
        self.calls = []
        def normal(job, text):
            self.started.set(); self.release.wait(4); return 'file-id'
        def news(job, folder):
            self.calls.append(job); self.started.set(); self.release.wait(4)
            work = folder / 'work'; work.mkdir()
            (work/'video.mp4').write_bytes(b'example video')
            (work/'status.json').write_text('{"stage":"ready_for_review"}')
        self.shared = worker.JobStore(normal)
        self.news = news_jobs.NewsJobs(self.shared, self.root/'news', news)
        self.secret='test-secret-1234567890'; self.start_server()
        self.script={'turns':[{'speaker':'Бендер','text':'Первый текст.'},
                              {'speaker':'Киберсенсей','text':'Вторая реплика.'}], 'ticker':'ПРОБА'}

    def start_server(self):
        self.server=ThreadingHTTPServer(('127.0.0.1',0),worker.handler_for(self.shared,self.secret,self.news))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.release.set(); self.server.shutdown(); self.server.server_close(); self.thread.join(); self.tmp.cleanup()

    def request(self,method,path,data=None,auth=True):
        raw=json.dumps(data,ensure_ascii=False).encode() if data is not None else None
        h={'Authorization':'Bearer '+self.secret} if auth else {}
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url+path,data=raw,method=method,headers=h),timeout=3) as r:
                return r.status,r.read(),r.headers.get('Content-Type')
        except urllib.error.HTTPError as e: return e.code,e.read(),e.headers.get('Content-Type')

    def status(self, job):
        code, body, _ = self.request('GET','/news-jobs/'+job)
        return code,json.loads(body)

    def test_auth_single_slot_idempotency_and_persistent_recovery(self):
        job=str(uuid.uuid4()); payload={'request_id':job,'script':self.script}
        self.assertEqual(self.request('POST','/news-jobs',payload,auth=False)[0],401)
        self.assertIn('news-story-v1',json.loads(self.request('GET','/health')[1])['capabilities'])
        self.assertEqual(self.request('POST','/news-jobs',payload)[0],202)
        self.assertTrue(self.started.wait(2))
        self.assertEqual(self.request('POST','/news-jobs',payload)[0],200)
        other={'request_id':str(uuid.uuid4()),'text':'обычная задача'}
        self.assertEqual(self.request('POST','/jobs',other)[0],409)
        changed={'request_id':job,'script':self.script | {'ticker':'ИЗМЕНЕНО'}}
        self.assertEqual(self.request('POST','/news-jobs',changed)[0],409)
        self.assertEqual(self.request('GET','/news-jobs/'+job+'/video')[0],404)
        self.release.set()
        for _ in range(200):
            status=self.status(job)[1]['status']
            if status=='ready': break
            time.sleep(.01)
        self.assertEqual(status,'ready')
        self.assertEqual(self.request('GET','/news-jobs/'+job+'/video',auth=False)[0],401)
        code,body,ctype=self.request('GET','/news-jobs/'+job+'/video')
        self.assertEqual((code,body,ctype),(200,b'example video','video/mp4'))
        self.assertEqual(self.calls,[job])
        # A new service instance recovers completion without rerendering or TTS.
        restarted=news_jobs.NewsJobs(worker.JobStore(lambda *args: None),self.root/'news',lambda *args: self.fail('rerun'))
        state,created=restarted.submit(job,self.script)
        self.assertFalse(created); self.assertEqual(state['status'],'ready')
        self.assertEqual(restarted.status(job)['status'],'ready')

    def test_versioned_studio_endpoint_and_legacy_id_collision(self):
        job = str(uuid.uuid4()); payload = {'request_id': job, 'script': self.script}
        self.assertNotIn('news-story-bulletin-studio-v1', json.loads(self.request('GET', '/health')[1])['capabilities'])
        self.assertEqual(self.request('POST', '/news-studio-jobs', payload)[0], 404)
        self.assertEqual(self.calls, [])
        self.news.run_story_studio = self.news.run_job
        self.assertIn('news-story-bulletin-studio-v1', json.loads(self.request('GET', '/health')[1])['capabilities'])
        # Historic request on the old endpoint is never mistaken for a new-studio job.
        old = self.root / 'news' / job; (old / 'work').mkdir(parents=True)
        (old / 'request.json').write_text(json.dumps(self.script, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
        (old / 'work' / 'status.json').write_text('{"stage":"ready_for_review"}')
        (old / 'work' / 'video.mp4').write_bytes(b'old video')
        self.assertEqual(self.request('POST', '/news-studio-jobs', payload)[0], 409)
        self.assertEqual(self.request('GET', '/news-studio-jobs/' + job)[0], 404)
        self.assertEqual(self.request('GET', '/news-jobs/' + job)[0], 200)
        self.assertEqual(self.calls, [])
        fresh = str(uuid.uuid4()); payload['request_id'] = fresh
        self.assertEqual(self.request('POST', '/news-studio-jobs', payload)[0], 202)
        self.assertTrue(self.started.wait(2))
        self.assertEqual(self.request('POST', '/news-studio-jobs', payload)[0], 200)
        self.assertEqual(self.request('GET', '/news-studio-jobs/' + fresh)[0], 200)
        stored = json.loads((self.root / 'news' / fresh / 'request.json').read_text())
        self.assertEqual(stored, {'studio': 'bulletin-v1', 'script': self.script})
        self.assertEqual(self.calls, [fresh])

    def test_digest_validates_sources_and_uses_same_slot(self):
        self.news.run_digest = self.news.run_job
        ids = [str(uuid.uuid4()) for _ in range(2)]
        for source in ids:
            folder = self.root/'news'/source; (folder/'work').mkdir(parents=True)
            (folder/'request.json').write_text(json.dumps(self.script))
            (folder/'work'/'status.json').write_text('{"stage":"ready_for_review"}')
            (folder/'work'/'video.mp4').write_bytes(b'video')
        digest = str(uuid.uuid4())
        payload = {'request_id': digest, 'inputs': ids}
        self.assertEqual(self.request('POST','/news-digests',payload,auth=False)[0],401)
        self.assertEqual(self.request('POST','/news-digests',{'request_id':digest,'inputs':[ids[0],ids[0]]})[0],400)
        self.assertEqual(self.request('POST','/news-digests',payload)[0],202)
        self.assertTrue(self.started.wait(2))
        self.assertEqual(self.request('POST','/news-digests',payload)[0],200)
        self.assertEqual(self.request('POST','/jobs',{'request_id':str(uuid.uuid4()),'text':'test'})[0],409)
        self.release.set()
        for _ in range(200):
            status=self.status(digest)[1]['status']
            if status=='ready': break
            time.sleep(.01)
        self.assertEqual(status,'ready')
        self.assertEqual(self.request('POST','/news-digests',{'request_id':str(uuid.uuid4()),'inputs':[ids[0],digest]})[0],400)
        self.assertEqual(self.calls,[digest])

    def test_image_capability_payload_and_idempotency(self):
        from io import BytesIO
        from PIL import Image
        from news_prototype.news_image_codec import make_thumbnail
        image = BytesIO(); Image.new('RGB', (20, 20), 'red').save(image, format='PNG')
        thumb = make_thumbnail(image.getvalue())
        job = str(uuid.uuid4()); script = self.script | {'image_b64': thumb}
        self.assertIn('news-story-image-v1', json.loads(self.request('GET', '/health')[1])['capabilities'])
        self.assertEqual(self.request('POST', '/news-jobs', {'request_id': job, 'script': script})[0], 202)
        self.assertTrue(self.started.wait(2))
        self.assertEqual(self.request('POST', '/news-jobs', {'request_id': job, 'script': script})[0], 200)
        saved = json.loads((self.root / 'news' / job / 'request.json').read_text())
        self.assertEqual(saved['image_b64'], thumb)
        changed = dict(script); changed.pop('image_b64')
        self.assertEqual(self.request('POST', '/news-jobs', {'request_id': job, 'script': changed})[0], 409)
        self.assertEqual(self.request('POST', '/news-jobs', {'request_id': str(uuid.uuid4()),
                         'script': script | {'image_b64': 'bad!'}})[0], 400)

    def test_program_capability_validation_and_shared_slot(self):
        self.news.run_program = self.news.run_job
        job = str(uuid.uuid4())
        payload = {'request_id': job, 'scripts': [self.script, self.script]}
        self.assertIn('news-program-v1', json.loads(self.request('GET', '/health')[1])['capabilities'])
        self.assertEqual(self.request('POST', '/news-programs', payload, auth=False)[0], 401)
        self.assertEqual(self.request('POST', '/news-programs', {'request_id': job, 'scripts': [self.script]})[0], 400)
        self.assertEqual(self.request('POST', '/news-programs', payload)[0], 202)
        self.assertTrue(self.started.wait(2))
        self.assertEqual(self.request('POST', '/news-programs', payload)[0], 200)
        changed = {'request_id': job, 'scripts': [self.script, self.script | {'ticker':'ИЗМЕНЕНО'}]}
        self.assertEqual(self.request('POST', '/news-programs', changed)[0], 409)
        self.assertEqual(self.request('POST', '/jobs', {'request_id':str(uuid.uuid4()), 'text':'test'})[0], 409)
        self.assertEqual(json.loads((self.root/'news'/job/'request.json').read_text())['scripts'], payload['scripts'])
        self.assertEqual(self.calls, [job])

    def test_normal_job_blocks_news(self):
        self.assertEqual(self.request('POST','/jobs',{'request_id':str(uuid.uuid4()),'text':'test'})[0],202)
        self.assertTrue(self.started.wait(2))
        self.assertEqual(self.request('POST','/news-jobs',{'request_id':str(uuid.uuid4()),'script':self.script})[0],409)
        self.assertEqual(self.calls,[])

    def test_invalid_and_interrupted_jobs_are_not_replayed(self):
        job=str(uuid.uuid4()); self.assertEqual(self.request('POST','/news-jobs',{'request_id':job,'script':{}})[0],400)
        folder=self.root/'news'/job; folder.mkdir(); (folder/'request.json').write_text(json.dumps(self.script,sort_keys=True,ensure_ascii=False,separators=(',',':')))
        self.assertEqual(self.status(job)[1]['status'],'interrupted')
        self.assertEqual(self.request('POST','/news-jobs',{'request_id':job,'script':self.script})[0],200)
        self.assertEqual(self.calls,[])

    def test_interrupted_reservation_blocks_all_new_jobs_after_restart(self):
        old = str(uuid.uuid4())
        folder = self.root / 'news' / old
        folder.mkdir()
        (folder / 'request.json').write_text('{}')
        restarted_shared = worker.JobStore(lambda *args: 'file-id')
        restarted = news_jobs.NewsJobs(restarted_shared, self.root/'news', lambda *args: None)
        self.assertTrue(restarted.has_unresolved())
        with self.assertRaises(worker.BusyError):
            restarted_shared.submit(str(uuid.uuid4()), 'обычный запрос')
        with self.assertRaises(news_jobs.NewsBusy):
            restarted.submit(str(uuid.uuid4()), self.script)
        # Only a confirmed terminal marker removes the block.
        (folder / 'failed').write_text('failed')
        self.assertFalse(restarted.has_unresolved())

    def test_symlinked_video_never_served(self):
        job=str(uuid.uuid4()); folder=self.root/'news'/job; (folder/'work').mkdir(parents=True)
        (folder/'work'/'status.json').write_text('{"stage":"ready_for_review"}')
        (folder/'work'/'video.mp4').symlink_to('/etc/passwd')
        self.assertEqual(self.status(job)[1]['status'],'interrupted')
        self.assertEqual(self.request('GET','/news-jobs/'+job+'/video')[0],404)


if __name__ == '__main__': unittest.main()
