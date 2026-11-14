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
        self.assertEqual(self.request('GET', '/health'), (200, {'status': 'ok', 'capabilities': ['animated-sign-v1']}))

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

    def test_optional_sign_reaches_worker_and_conflicts_are_rejected(self):
        calls = []
        release = threading.Event()
        started = threading.Event()
        def run(request_id, text, sign):
            calls.append((request_id, text, sign))
            started.set()
            release.wait(3)
            return 'file-id'
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.store = worker.JobStore(run)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), worker.handler_for(self.store, self.secret))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        job = str(uuid.uuid4())
        payload = {'request_id': job, 'text': 'Озвучка', 'sign': 'Гравитация в отпуске'}
        try:
            self.assertEqual(self.request('POST', '/jobs', payload)[0], 202)
            self.assertTrue(started.wait(2))
            self.assertEqual(calls, [(job, 'Озвучка', 'ГРАВИТАЦИЯ В ОТПУСКЕ')])
            self.assertEqual(self.request('POST', '/jobs', payload)[0], 200)
            payload['sign'] = 'ДРУГАЯ ШУТКА'
            self.assertEqual(self.request('POST', '/jobs', payload)[0], 409)
            self.assertNotIn('sign', self.request('GET', '/jobs/' + job)[1])
        finally:
            release.set()

    def test_rejects_extra_fields_and_bad_payloads(self):
        payload = {'request_id': str(uuid.uuid4()), 'text': 'Привет', 'chat_id': -1001}
        self.assertEqual(self.request('POST', '/jobs', payload)[0], 400)
        payload.pop('chat_id')
        payload['request_id'] = 'bad-id'
        self.assertEqual(self.request('POST', '/jobs', payload)[0], 400)
        payload['request_id'] = str(uuid.uuid4())
        payload['text'] = ' ' * 10
        self.assertEqual(self.request('POST', '/jobs', payload)[0], 400)

    def test_video_requires_auth_and_uncertain_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'video.mp4'
            path.write_bytes(b'video bytes')
            job = str(uuid.uuid4())
            self.store.jobs[job] = {'text':'hidden','status':'upload_uncertain','_video_path':path}
            status, public = self.request('GET','/jobs/'+job)
            self.assertEqual(status, 200)
            self.assertEqual(public['status'], 'upload_uncertain')
            self.assertNotIn(str(path), json.dumps(public))
            self.assertEqual(self.request('GET','/jobs/'+job+'/video',secret=False)[0], 401)
            req = urllib.request.Request(self.url+'/jobs/'+job+'/video',headers={'Authorization':'Bearer '+self.secret})
            with urllib.request.urlopen(req,timeout=3) as result:
                self.assertEqual(result.headers['Content-Type'], 'video/mp4')
                self.assertEqual(result.read(), b'video bytes')
            self.store.jobs[job]['status'] = 'done'
            self.assertEqual(self.request('GET','/jobs/'+job+'/video')[0],404)
            self.store.jobs[job]['status'] = 'upload_uncertain'
            link = Path(folder)/'link.mp4'; link.symlink_to(path)
            self.store.jobs[job]['_video_path'] = link
            self.assertEqual(self.request('GET','/jobs/'+job+'/video')[0],404)

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
    def test_scene_worker_rejects_missing_assets_before_start(self):
        with tempfile.TemporaryDirectory() as folder:
            plate = Path(folder) / 'plate.png'; plate.touch()
            env = {'VIDEO_WORKER_KEY': 'x' * 24, 'BOT_TOKEN': 'token', 'STORAGE_CHANNEL_ID': '-1001'}
            cases = [('--random-guests',), ('--random-guests', '--smoke-plate', str(plate), '--portrait', str(plate.parent/'missing')),
                     ('--portrait', str(plate))]
            for options in cases:
                with self.subTest(options=options), patch.dict(os.environ, env), \
                     patch('sys.argv', ['worker', *options]), patch('sys.stderr'), \
                     patch.object(worker, 'ThreadingHTTPServer') as server, self.assertRaises(SystemExit):
                    worker.main()
                server.assert_not_called()

    @patch('video_prototype.worker.subprocess.Popen')
    @patch.dict(os.environ, {'VIDEO_WORKER_KEY': 'worker-secret'})
    def test_runs_channel_only_and_returns_file_id(self, popen):
        popen.return_value.wait.return_value = 0
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job_id = str(uuid.uuid4())
            output = root / job_id / 'clip-one'
            def complete(**kwargs):
                output.mkdir(parents=True)
                (output / 'result.json').write_text(json.dumps({'file_id': 'hello'}))
                return 0
            popen.return_value.wait.side_effect = complete
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


    @patch('video_prototype.worker.subprocess.Popen')
    def test_worker_rotates_start_index_across_jobs(self, popen):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def complete(**kwargs):
                command = popen.call_args.args[0]
                output = Path(command[command.index('--output-dir') + 1]) / 'clip-one'
                output.mkdir(parents=True)
                (output / 'result.json').write_text(json.dumps({'file_id': 'id'}))
                return 0
            popen.return_value.wait.side_effect = complete
            run = worker.runner(root, -1001, 'bot', 600)
            for index in range(3):
                self.assertEqual(run(str(uuid.uuid4()), 'текст'), 'id')
                self.assertEqual(popen.call_args.kwargs['env']['BENDER_TTS_START_INDEX'], str(index))

    @patch('video_prototype.worker.subprocess.Popen')
    def test_passes_smoke_plate_to_pipeline(self, popen):
        popen.return_value.wait.return_value = 0
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job_id = str(uuid.uuid4())
            output = root / job_id / 'clip-one'
            def complete(**kwargs):
                output.mkdir(parents=True)
                (output / 'result.json').write_text(json.dumps({'file_id': 'hello'}))
                return 0
            popen.return_value.wait.side_effect = complete
            plate = root / 'plate.png'
            worker.runner(root, -1001, 'bot-secret', 600, plate)(job_id, 'Текст')
            args = popen.call_args.args[0]
            self.assertEqual(args[args.index('--smoke-plate') + 1], str(plate))

    @patch('video_prototype.worker.subprocess.Popen')
    def test_scene_uses_job_uuid_and_configured_portrait(self, popen):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); job_id = str(uuid.uuid4())
            def complete(**kwargs):
                output = root / job_id / 'clip-one'; output.mkdir(parents=True)
                (output / 'result.json').write_text(json.dumps({'file_id': 'id'}))
                return 0
            popen.return_value.wait.side_effect = complete
            plate = root / 'plate.png'; portrait = root / 'portrait.png'
            result = worker.runner(root, -1001, 'secret', 600, plate, portrait)(job_id, 'text', 'КОСМОС')
            self.assertEqual(result, 'id')
            args = popen.call_args.args[0]
            self.assertEqual(args[args.index('--text') + 1], 'text')
            self.assertIn('--sign=КОСМОС', args)
            self.assertEqual(args[args.index('--scene-seed') + 1], job_id)
            self.assertEqual(args[args.index('--portrait') + 1], str(portrait))
            self.assertEqual(args[args.index('--smoke-plate') + 1], str(plate))
            self.assertNotIn('--seed', args)  # Do not alter the TTS seed.

    @patch('video_prototype.worker.subprocess.Popen')
    def test_uncertain_storage_upload_returns_existing_video_not_stale_one(self, popen):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job_id = str(uuid.uuid4())
            old = root / job_id / 'clip-old'
            old.mkdir(parents=True)
            (old / 'result.json').write_text(json.dumps({'file_id': 'stale'}))
            output = root / job_id / 'clip-new'
            def fail_after_upload(**kwargs):
                output.mkdir()
                (output / 'video.mp4').write_bytes(b'mp4 contents')
                (output / 'storage-upload.pending').write_text('pending')
                return 1
            popen.return_value.wait.side_effect = fail_after_upload
            with self.assertRaises(worker.UploadUncertain) as error:
                worker.runner(root, -1001, 'bot-secret', 600)(job_id, 'Текст')
            self.assertEqual(error.exception.video_path, output/'video.mp4')

    @patch('video_prototype.worker.subprocess.Popen')
    def test_confirmed_file_id_wins_over_later_failure(self, popen):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job_id = str(uuid.uuid4())
            output = root/job_id/'clip-new'
            def after_saved(**kwargs):
                output.mkdir(parents=True)
                (output/'result.json').write_text(json.dumps({'file_id': 'saved'}))
                (output/'storage-upload.pending').write_text('pending')
                return 1
            popen.return_value.wait.side_effect = after_saved
            self.assertEqual(worker.runner(root, -1001, 'secret', 600)(job_id, 'text'), 'saved')

    @patch('video_prototype.worker.subprocess.Popen')
    def test_oversize_or_missing_video_does_not_offer_fallback(self, popen):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for mode in ('missing', 'oversize', 'empty'):
                job_id = str(uuid.uuid4()); output = root/job_id/'clip-new'
                def failed(**kwargs):
                    output.mkdir(parents=True)
                    (output/'storage-upload.pending').write_text('pending')
                    if mode != 'missing':
                        with (output/'video.mp4').open('wb') as video:
                            if mode == 'oversize': video.truncate(worker.MAX_VIDEO_BYTES + 1)
                    return 1
                popen.return_value.wait.side_effect = failed
                with self.subTest(mode=mode), self.assertRaises(RuntimeError):
                    worker.runner(root, -1001, 'secret', 600)(job_id, 'text')

    @patch('video_prototype.worker.subprocess.Popen')
    def test_render_failure_never_returns_partial_video(self, popen):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); job_id = str(uuid.uuid4()); output = root/job_id/'clip-new'
            def failed(**kwargs):
                output.mkdir(parents=True); (output/'video.mp4').write_bytes(b'partial')
                return 1
            popen.return_value.wait.side_effect = failed
            with self.assertRaises(RuntimeError): worker.runner(root, -1001, 'secret', 600)(job_id, 'text')


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
