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
            self.assertNotIn('--ukusi-stress', args)
            self.assertNotIn('--stress-marks', args)
            self.assertNotIn('--chat-id', args)
            self.assertNotIn('bot-secret', args)
            env = popen.call_args.kwargs['env']
            self.assertEqual(env['BOT_TOKEN'], 'bot-secret')
            self.assertNotIn('VIDEO_WORKER_KEY', env)


class WorkerCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'jobs'
        self.root.mkdir()
        self.now = time.time()
        self.store = worker.JobStore(lambda request_id, text: 'file-id', cleanup_dir=self.root)

    def job(self, age, *, name=None, with_log=True):
        path = self.root / (name or str(uuid.uuid4()))
        path.mkdir()
        if with_log:
            log = path / 'worker.log'
            log.write_text('log', encoding='utf-8')
            os.utime(log, (self.now - age, self.now - age))
        os.utime(path, (self.now - age, self.now - age))
        return path

    def test_removes_only_old_real_job_directories(self):
        old = self.job(worker.RETENTION_SECONDS + 10)
        recent = self.job(worker.RETENTION_SECONDS - 10)
        missing_log = self.job(worker.RETENTION_SECONDS + 10, with_log=False)
        other = self.job(worker.RETENTION_SECONDS + 10, name='recordings')
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        (outside / 'worker.log').write_text('keep')
        link = self.root / str(uuid.uuid4())
        link.symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.store.cleanup_old_jobs(now=self.now), 1)
        self.assertFalse(old.exists())
        for path in (recent, missing_log, other, outside, link):
            self.assertTrue(path.exists())

    def test_new_log_protects_old_directory(self):
        old = self.job(worker.RETENTION_SECONDS + 10)
        os.utime(old / 'worker.log', (self.now, self.now))
        self.assertEqual(self.store.cleanup_old_jobs(now=self.now), 0)
        self.assertTrue(old.exists())

    def test_active_job_is_not_removed_even_if_old(self):
        active = self.job(worker.RETENTION_SECONDS + 10)
        self.store.active = active.name
        self.assertEqual(self.store.cleanup_old_jobs(now=self.now), 0)
        self.assertTrue(active.exists())
        self.store.active = None
        self.assertEqual(self.store.cleanup_old_jobs(now=self.now), 1)
        self.assertFalse(active.exists())

    def test_never_follows_symlinked_log(self):
        path = self.job(worker.RETENTION_SECONDS + 10, with_log=False)
        target = Path(self.temp.name) / 'outside.log'
        target.write_text('keep')
        (path / 'worker.log').symlink_to(target)
        os.utime(path, (self.now - worker.RETENTION_SECONDS - 10,) * 2)
        self.assertEqual(self.store.cleanup_old_jobs(now=self.now), 0)
        self.assertTrue(target.exists())
        self.assertTrue(path.exists())

    def test_deletion_and_job_submission_are_serialized(self):
        old = self.job(worker.RETENTION_SECONDS + 10)
        started, release, submitted = threading.Event(), threading.Event(), threading.Event()
        original = worker.shutil.rmtree
        def slow_remove(path):
            started.set()
            if not release.wait(3):
                raise RuntimeError('test timeout')
            original(path)
        with patch('video_prototype.worker.shutil.rmtree', side_effect=slow_remove):
            cleanup = threading.Thread(target=lambda: self.store.cleanup_old_jobs(now=self.now))
            cleanup.start()
            try:
                self.assertTrue(started.wait(2))
                submit = threading.Thread(target=lambda: (self.store.submit(old.name, 'text'), submitted.set()))
                submit.start()
                self.assertFalse(submitted.wait(.1))
            finally:
                release.set()
                cleanup.join(3)
            submit.join(3)
        self.assertFalse(cleanup.is_alive())
        self.assertFalse(submit.is_alive())
        self.assertTrue(submitted.is_set())

    def test_cleanup_errors_do_not_break_remaining_jobs(self):
        bad = self.job(worker.RETENTION_SECONDS + 10)
        good = self.job(worker.RETENTION_SECONDS + 10)
        original = worker.shutil.rmtree
        def failing_remove(path):
            if path == bad:
                raise PermissionError('secret contents')
            return original(path)
        with patch('video_prototype.worker.shutil.rmtree', side_effect=failing_remove):
            self.assertEqual(self.store.cleanup_old_jobs(now=self.now), 1)
        self.assertTrue(bad.exists())
        self.assertFalse(good.exists())

    def test_missing_root_is_ok(self):
        self.store.cleanup_dir = self.root / 'missing'
        self.assertEqual(self.store.cleanup_old_jobs(now=self.now), 0)

    def test_scheduler_runs_once_and_is_throttled(self):
        completed = threading.Event()
        with patch.object(self.store, 'cleanup_old_jobs', side_effect=lambda: completed.set()) as clean:
            self.store.schedule_cleanup()
            self.assertTrue(completed.wait(2))
            self.store.schedule_cleanup()
            self.assertEqual(clean.call_count, 1)

    def test_failed_job_also_triggers_cleanup(self):
        old = self.job(worker.RETENTION_SECONDS + 10)
        deleted = threading.Event()
        def fail(request_id, text):
            raise RuntimeError('expected failure')
        self.store.run_job = fail
        original = self.store.cleanup_old_jobs
        def cleanup():
            result = original(now=self.now)
            deleted.set()
            return result
        with patch.object(self.store, 'cleanup_old_jobs', side_effect=cleanup):
            self.store.submit(str(uuid.uuid4()), 'text')
            self.assertTrue(deleted.wait(3))
        self.assertFalse(old.exists())

    def test_background_scan_failure_resets_schedule(self):
        self.store.next_cleanup = 123
        self.store.cleanup_running = True
        with patch.object(self.store, 'cleanup_old_jobs', side_effect=PermissionError('hidden')):
            self.store._cleanup_background()
        self.assertFalse(self.store.cleanup_running)
        self.assertEqual(self.store.next_cleanup, 0)

    def test_job_completion_triggers_cleanup(self):
        old = self.job(worker.RETENTION_SECONDS + 10)
        deleted = threading.Event()
        original = self.store.cleanup_old_jobs
        def cleanup():
            result = original(now=self.now)
            deleted.set()
            return result
        with patch.object(self.store, 'cleanup_old_jobs', side_effect=cleanup):
            self.store.submit(str(uuid.uuid4()), 'text')
            self.assertTrue(deleted.wait(3))
        self.assertFalse(old.exists())


if __name__ == '__main__':
    unittest.main()
