import json
from io import BytesIO
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
from PIL import Image
from video_prototype import news_jobs, worker
from news_prototype import bulletin_job, news_image_codec
from tests.test_bulletin_pipeline import payload


class BulletinWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.started, self.release = threading.Event(), threading.Event()
        self.calls = []
        def run(job, folder):
            self.calls.append(job); self.started.set(); self.release.wait(5)
            work = folder / 'work'; work.mkdir()
            (work / 'status.json').write_text('{"stage":"ready_for_review"}')
            (work / 'video.mp4').write_bytes(b'example')
        self.run = run
        self.shared = worker.JobStore(lambda *args: 'file-id')
        self.news = news_jobs.NewsJobs(self.shared, self.root / 'news', run)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), worker.handler_for(self.shared, 'secret', self.news))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.release.set(); self.server.shutdown(); self.server.server_close(); self.thread.join(); self.tmp.cleanup()

    def request(self, method, path, data=None, auth=True):
        body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
        headers = {'Authorization':'Bearer secret'} if auth else {}
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url + path, data=body, method=method, headers=headers), timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def wait_status(self, job, target):
        for _ in range(200):
            state = self.request('GET', '/news-jobs/' + job)[1]['status']
            if state == target and self.shared.active is None: return state
            time.sleep(.01)
        return state

    def test_capability_validation_idempotency_and_shared_slot(self):
        job = str(uuid.uuid4()); data = {'request_id': job, 'bulletin': payload()}
        self.assertNotIn('news-bulletin-v1', self.request('GET', '/health')[1]['capabilities'])
        self.assertEqual(self.request('POST', '/news-bulletins', data)[0], 400)
        self.news.run_bulletin = self.run
        self.assertIn('news-bulletin-v1', self.request('GET', '/health')[1]['capabilities'])
        self.assertEqual(self.request('POST', '/news-bulletins', data, auth=False)[0], 401)
        self.assertEqual(self.request('POST', '/news-bulletins', {'request_id':job, 'bulletin':{'sections':[]}})[0], 400)
        self.assertEqual(self.request('POST', '/news-bulletins', data)[0], 202)
        self.assertTrue(self.started.wait(2))
        self.assertEqual(self.request('POST', '/news-bulletins', data)[0], 200)
        changed = payload(); changed['sections'][0]['ticker'] = 'ИЗМЕНЕНО'
        self.assertEqual(self.request('POST', '/news-bulletins', {'request_id': job, 'bulletin': changed})[0], 409)
        self.assertEqual(self.request('POST', '/jobs', {'request_id':str(uuid.uuid4()), 'text':'ordinary'})[0], 409)
        self.assertEqual(self.calls, [job])
        self.release.set(); self.assertEqual(self.wait_status(job, 'ready'), 'ready')
        restarted = news_jobs.NewsJobs(worker.JobStore(lambda *args: None), self.root/'news', lambda *args: None,
                                        run_bulletin=lambda *args: self.fail('must not replay'))
        state, created = restarted.submit_bulletin(job, payload())
        self.assertFalse(created); self.assertEqual(state['status'], 'ready')

    def test_20_bounded_images_can_exceed_old_program_limit(self):
        img = Image.frombytes('RGB', (512, 512), os.urandom(512 * 512 * 3))
        buffer = BytesIO(); img.save(buffer, format='PNG')
        thumb = news_image_codec.make_thumbnail(buffer.getvalue())
        sections = [{'id':i, 'ticker':f'Новость {i}', 'turns':[{'speaker':'Бендер' if i % 2 == 0 else 'Киберсенсей',
                    'text':'Коротко.'}], 'image_b64':thumb} for i in range(1, 21)]
        job = str(uuid.uuid4()); data = {'request_id': job, 'bulletin': {'sections': sections}}
        size = len(json.dumps(data, ensure_ascii=False).encode())
        self.assertGreater(size, news_jobs.MAX_PROGRAM_BYTES)
        self.assertLess(size, news_jobs.MAX_BULLETIN_BYTES)
        self.news.run_bulletin = self.run
        self.assertEqual(self.request('POST', '/news-bulletins', data)[0], 202)
        self.assertTrue(self.started.wait(2))
        self.assertEqual((self.root/'news'/job/'request.json').stat().st_size > news_jobs.MAX_PROGRAM_BYTES, True)
        self.assertEqual(self.request('POST', '/news-bulletins', data)[0], 200)

    def test_too_long_is_terminal_only_after_child_exits_and_no_video_is_served(self):
        def long_audio(job, folder):
            work = folder / 'work'; work.mkdir()
            (work / 'status.json').write_text('{"stage":"audio_too_long"}')
            self.started.set(); self.release.wait(5)
            raise RuntimeError('child exited after limit')
        self.news.run_bulletin = long_audio
        job = str(uuid.uuid4())
        self.assertEqual(self.request('POST', '/news-bulletins', {'request_id':job, 'bulletin':payload()})[0], 202)
        self.assertTrue(self.started.wait(2))
        self.assertEqual(self.news.status(job)['status'], 'running')
        self.assertTrue(self.news.has_unresolved())
        self.release.set(); self.assertEqual(self.wait_status(job, 'audio_too_long'), 'audio_too_long')
        self.assertFalse(self.news.has_unresolved())
        self.assertEqual(self.request('GET', '/news-jobs/' + job + '/video')[0], 404)
        restarted = news_jobs.NewsJobs(worker.JobStore(lambda *args: None), self.root/'news', lambda *args: None)
        self.assertEqual(restarted.status(job)['status'], 'audio_too_long')
        self.assertFalse(restarted.has_unresolved())

    def test_unconfirmed_audio_status_after_crash_still_blocks_jobs(self):
        job = str(uuid.uuid4()); folder = self.root/'news'/job
        (folder/'work').mkdir(parents=True)
        (folder/'request.json').write_text('{}')
        (folder/'work'/'status.json').write_text('{"stage":"audio_too_long"}')
        restarted = news_jobs.NewsJobs(worker.JobStore(lambda *args: None), self.root/'news', lambda *args: None)
        self.assertEqual(restarted.status(job)['status'], 'interrupted')
        self.assertTrue(restarted.has_unresolved())

    def test_worker_child_validates_request_before_calling_pipeline(self):
        from unittest.mock import patch
        job = str(uuid.uuid4()); folder = self.root/job; folder.mkdir()
        request = folder/'request.json'; request.write_text(json.dumps({'bulletin':payload()}))
        with patch.object(bulletin_job.bulletin_pipeline, 'run', return_value=folder/'video.mp4') as run:
            bulletin_job.run(request, folder/'work', 'ffmpeg', ['fake'])
            self.assertEqual(run.call_count, 1)
            request.write_text('{"bulletin":{"sections":[]}}')
            with self.assertRaises(ValueError): bulletin_job.run(request, folder/'work', 'ffmpeg', ['fake'])
            self.assertEqual(run.call_count, 1)

    def test_real_child_entrypoint_renders_with_network_blocked_synthetic_tts(self):
        import imageio_ffmpeg
        from unittest.mock import patch
        job = str(uuid.uuid4()); folder = self.root / job; folder.mkdir()
        (folder / 'request.json').write_text(json.dumps({'bulletin': payload()}, ensure_ascii=False))
        hooks = self.root / 'hooks'; hooks.mkdir()
        (hooks / 'sitecustomize.py').write_text("""
import os, socket, wave
from array import array
assert 'BOT_TOKEN' not in os.environ
assert 'VIDEO_WORKER_KEY' not in os.environ
assert 'GEMINI_KEYS' not in os.environ
# Fail closed if any Python network client is accidentally called.
def blocked(*args, **kwargs): raise AssertionError('network disabled in local test')
socket.socket.connect = blocked
from video_prototype import gemini_tts
def synthesize(text, target, keys, start_index, **kwargs):
    with wave.open(str(target), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
        wav.writeframes(array('h', [3000 if i % 6000 < 3000 else 0 for i in range(24000)]).tobytes())
gemini_tts.synthesize = synthesize
""")
        binary = self.root / 'bin'; binary.mkdir()
        (binary / 'ffmpeg').symlink_to(imageio_ffmpeg.get_ffmpeg_exe())
        repo = Path(__file__).resolve().parent.parent
        env = {'PYTHONPATH': str(hooks) + os.pathsep + str(repo),
               'PATH': str(binary) + os.pathsep + os.environ.get('PATH', ''),
               'GEMINI_TTS_KEYS': 'test-only', 'GEMINI_KEYS': 'must-not-leak',
               'BOT_TOKEN': 'must-not-leak', 'VIDEO_WORKER_KEY': 'must-not-leak'}
        with patch.dict(os.environ, env):
            news_jobs.bulletin_runner(repo)(job, folder)
        work = folder / 'work'
        self.assertEqual(json.loads((work/'status.json').read_text())['stage'], 'ready_for_review')
        self.assertGreater((work/'video.mp4').stat().st_size, 1000)
        self.assertNotIn('must-not-leak', (folder/'worker.log').read_text())

    def test_real_child_duration_guard_returns_terminal_without_video_or_repeat(self):
        import imageio_ffmpeg
        from unittest.mock import patch
        hooks = self.root / 'hooks-long'; hooks.mkdir()
        (hooks / 'sitecustomize.py').write_text("""
import socket, wave
from array import array
def blocked(*args, **kwargs): raise AssertionError('network disabled')
socket.socket.connect = blocked
from video_prototype import gemini_tts
def synthesize(text, target, keys, start_index, **kwargs):
    with wave.open(str(target), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
        wav.writeframes((array('h', [3000]*3000 + [0]*3000) * 200).tobytes())
gemini_tts.synthesize = synthesize
""")
        binary = self.root / 'bin-long'; binary.mkdir()
        (binary / 'ffmpeg').symlink_to(imageio_ffmpeg.get_ffmpeg_exe())
        repo = Path(__file__).resolve().parent.parent
        env = {'PYTHONPATH': str(hooks) + os.pathsep + str(repo),
               'PATH': str(binary) + os.pathsep + os.environ.get('PATH', ''), 'GEMINI_TTS_KEYS': 'test-only'}
        data = {'sections': [{'id':i, 'ticker':f'НОВОСТЬ {i}', 'turns':[
            {'speaker': 'Бендер' if i % 2 else 'Киберсенсей', 'text':'Проверка.'}]} for i in range(1,7)]}
        job = str(uuid.uuid4()); self.news.run_bulletin = news_jobs.bulletin_runner(repo)
        with patch.dict(os.environ, env):
            self.assertEqual(self.news.submit_bulletin(job, data)[1], True)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                state = self.news.status(job)['status']
                if state != 'running': break
                time.sleep(.05)
        self.assertEqual(state, 'audio_too_long')
        work = self.root/'news'/job/'work'
        self.assertEqual(json.loads((work/'status.json').read_text())['stage'], 'audio_too_long')
        self.assertFalse((work/'video.mp4').exists())
        self.assertFalse((work/'video.partial.mp4').exists())
        self.assertFalse(self.news.has_unresolved())
        self.assertEqual(self.news.submit_bulletin(job, data), ({'request_id':job, 'status':'audio_too_long'}, False))

    def test_worker_child_imports_without_bot_network_dependencies(self):
        import subprocess, sys
        code = """
import builtins
original = builtins.__import__
def blocked(name, *args, **kwargs):
    if name == 'aiohttp' or name.startswith('aiohttp.'):
        raise ImportError('intentionally unavailable')
    return original(name, *args, **kwargs)
builtins.__import__ = blocked
from news_prototype import bulletin_job
from video_prototype.news_jobs import bulletin_assets_ready
assert callable(bulletin_job.run) and bulletin_assets_ready()
"""
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_asset_readiness_fails_closed(self):
        from unittest.mock import patch
        from news_prototype import bulletin_wide_screen
        self.assertTrue(news_jobs.bulletin_assets_ready())
        with patch.object(bulletin_wide_screen, 'compose', side_effect=FileNotFoundError()):
            self.assertFalse(news_jobs.bulletin_assets_ready())
