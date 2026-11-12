import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from video_prototype import worker


class WorkerHTTPTests(unittest.TestCase):
    def setUp(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = []
        def run(request_id, text):
            self.calls.append((request_id, text))
            self.started.set()
            if not self.release.wait(3):
                raise RuntimeError('test timed out')
            return 'telegram-file-id'
        self.secret = 'test-secret-1234567890123456'
        self.store = worker.JobStore(run)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), worker.handler_for(self.store, self.secret))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, data=None, secret=True):
        body = json.dumps(data).encode() if data is not None else None
        headers = {'Authorization': 'Bearer ' + self.secret} if secret else {}
        req = urllib.request.Request(self.url + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=3) as result:
                return result.status, json.load(result)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_health_requires_secret(self):
        self.assertEqual(self.request('GET', '/health', secret=False)[0], 401)
        self.assertEqual(self.request('GET', '/health'), (200, {'status': 'ok'}))

    def test_job_is_idempotent_busy_and_returns_file_id(self):
        job = str(uuid.uuid4())
        payload = {'request_id': job, 'text': 'Привет'}
        status, result = self.request('POST', '/jobs', payload)
        self.assertEqual(status, 202)
        self.assertEqual(result['status'], 'running')
        self.assertTrue(self.started.wait(2))
        self.assertEqual(self.request('POST', '/jobs', payload)[0], 200)
        self.assertEqual(self.request('POST', '/jobs', {'request_id': job, 'text': 'Другое'})[0], 409)
        self.assertEqual(self.request('POST', '/jobs', {'request_id': str(uuid.uuid4()), 'text': 'Привет'})[0], 409)
        self.assertEqual(len(self.calls), 1)
        self.release.set()
        for _ in range(200):
            state = self.store.get(job)
            if state['status'] == 'done':
                break
            time.sleep(.01)
        self.assertEqual(self.request('GET', '/jobs/' + job)[1]['file_id'], 'telegram-file-id')
        self.assertNotIn('text', self.request('GET', '/jobs/' + job)[1])

    def test_rejects_extra_fields_and_bad_payloads(self):
        payload = {'request_id': str(uuid.uuid4()), 'text': 'Привет', 'chat_id': -1001}
        self.assertEqual(self.request('POST', '/jobs', payload)[0], 400)
        payload.pop('chat_id')
        payload['request_id'] = 'bad-id'
        self.assertEqual(self.request('POST', '/jobs', payload)[0], 400)
        payload['request_id'] = str(uuid.uuid4())
        payload['text'] = ' ' * 10
        self.assertEqual(self.request('POST', '/jobs', payload)[0], 400)

    def test_failure_does_not_leak_error(self):
        def fail(request_id, text):
            raise RuntimeError('secret description')
        self.store = worker.JobStore(fail)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), worker.handler_for(self.store, self.secret))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        job = str(uuid.uuid4())
        self.request('POST', '/jobs', {'request_id': job, 'text': 'Привет'})
        for _ in range(200):
            state = self.store.get(job)
            if state['status'] == 'failed':
                break
            time.sleep(.01)
        self.assertEqual(state['status'], 'failed')
        self.assertNotIn('secret', json.dumps(state))


class WorkerRunnerTests(unittest.TestCase):
    @patch('video_prototype.worker.subprocess.Popen')
    @patch.dict(os.environ, {'VIDEO_WORKER_KEY': 'worker-secret'})
    def test_runs_channel_only_and_returns_file_id(self, popen):
        popen.return_value.wait.return_value = 0
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job_id = str(uuid.uuid4())
            output = root / job_id / 'clip-one'
            output.mkdir(parents=True)
            (output / 'result.json').write_text(json.dumps({'file_id': 'hello'}))
            result = worker.runner(root, -1001, 'bot-secret', 600)(job_id, 'Текст')
            self.assertEqual(result, 'hello')
            args = popen.call_args.args[0]
            self.assertIn('--channel-id', args)
            self.assertIn('--ukusi-stress', args)
            self.assertNotIn('--stress-marks', args)
            self.assertNotIn('--chat-id', args)
            self.assertNotIn('bot-secret', args)
            env = popen.call_args.kwargs['env']
            self.assertEqual(env['BOT_TOKEN'], 'bot-secret')
            self.assertNotIn('VIDEO_WORKER_KEY', env)


if __name__ == '__main__':
    unittest.main()
