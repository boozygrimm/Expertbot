import asyncio
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import urllib.error
import urllib.request
import uuid
import wave
from http.server import ThreadingHTTPServer

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from aiohttp import web
from mongomock_motor import AsyncMongoMockClient
from bender_worker_client import BenderWorkerClient, WorkerBusy, WorkerUnavailable, WorkerUploadUncertain
from handlers import admin_panel, bender_clip, ex_logic, response_choice, voice_rec
from tests.test_bender_clip import FakeQueue
from video_prototype import pipeline, worker


class VoicePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wav = self.root / 'test.wav'
        with wave.open(str(self.wav), 'wb') as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(24000); f.writeframes(b'\x00\x00' * 24000)

    def args(self, *extra):
        return pipeline.parse_args(['--audio', str(self.wav), '--voice-only', '--output-dir', str(self.root / 'jobs'), *extra])

    def test_voice_upload_uses_ogg_without_video_render_and_preserves_topic(self):
        def ffmpeg(command, **kwargs):
            self.assertEqual(command[0], 'ffmpeg')
            self.assertIn('libopus', command)
            self.assertNotIn('GEMINI_TTS_KEYS', kwargs['env'])
            self.assertNotIn('BOT_TOKEN', kwargs['env'])
            Path(command[-1]).write_bytes(b'OggSdummy')
        with patch.dict(os.environ, {'BOT_TOKEN': 'secret', 'GEMINI_TTS_KEYS': 'key'}, clear=True), \
             patch.object(pipeline.subprocess, 'run', side_effect=ffmpeg) as render, \
             patch.object(pipeline, 'telegram_call', side_effect=[{'voice': {'file_id': 'voice-id'}}, {}]) as send:
            pipeline.run(self.args('--channel-id', '-100', '--chat-id', '-101',
                                   '--reply-to-message-id', '42', '--message-thread-id', '7'))
        render.assert_called_once()
        self.assertEqual(send.call_args_list[0].args[1], 'sendVoice')
        self.assertEqual(send.call_args_list[0].args[3].name, 'voice.ogg')
        self.assertEqual(send.call_args_list[1].args[2]['voice'], 'voice-id')
        self.assertEqual(send.call_args_list[1].args[2]['message_thread_id'], 7)
        record = json.loads(next((self.root / 'jobs').glob('clip-*/result.json')).read_text())
        self.assertEqual(record['file_id'], 'voice-id')
        self.assertIn('voice', record)
        self.assertNotIn('video', record)

    def test_voice_uncertain_storage_is_recoverable_and_not_reuploaded(self):
        def ffmpeg(command, **kwargs):
            Path(command[-1]).write_bytes(b'OggSdummy')
        with patch.dict(os.environ, {'BOT_TOKEN': 'secret'}, clear=True), \
             patch.object(pipeline.subprocess, 'run', side_effect=ffmpeg), \
             patch.object(pipeline, 'telegram_call', side_effect=RuntimeError('lost')) as send:
            with self.assertRaises(RuntimeError):
                pipeline.run(self.args('--channel-id', '-100'))
        self.assertTrue(next((self.root / 'jobs').glob('clip-*/storage-upload.pending')).is_file())
        self.assertTrue(next((self.root / 'jobs').glob('clip-*/voice.ogg')).is_file())
        send.assert_called_once()

    def test_real_ffmpeg_produces_playable_ogg_opus(self):
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            try:
                import imageio_ffmpeg
                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            except ImportError:
                self.skipTest('ffmpeg not available')
        bindir = self.root / 'bin'; bindir.mkdir()
        (bindir / 'ffmpeg').symlink_to(ffmpeg)
        with patch.dict(os.environ, {'PATH': str(bindir) + os.pathsep + os.environ.get('PATH', '')}):
            pipeline.run(self.args('--no-send'))
        output = next((self.root / 'jobs').glob('clip-*/voice.ogg'))
        self.assertTrue(output.read_bytes().startswith(b'OggS'))
        import subprocess
        check = subprocess.run([ffmpeg, '-v', 'error', '-i', str(output), '-f', 'null', '-'], capture_output=True)
        self.assertEqual(check.returncode, 0, check.stderr.decode(errors='replace'))

    def test_voice_uses_gemini_and_falls_back_to_tera(self):
        def command_run(command, **kwargs):
            if command[0] == 'ffmpeg':
                Path(command[-1]).write_bytes(b'OggS')
            else:
                self.assertIn('tera_speech.py', command[1])
                self.assertNotIn('GEMINI_TTS_KEYS', kwargs['env'])
                Path(command[command.index('--output') + 1]).write_bytes(b'wav')
        args = pipeline.parse_args(['--text', 'Привет!', '--voice-only', '--no-send',
                                    '--output-dir', str(self.root / 'jobs')])
        with patch.dict(os.environ, {'BENDER_TTS_PROVIDER': 'gemini', 'GEMINI_TTS_KEYS': 'key'}, clear=True), \
             patch.object(pipeline.gemini_tts, 'synthesize', side_effect=pipeline.gemini_tts.TTSFailure('quota')) as gemini, \
             patch.object(pipeline.subprocess, 'run', side_effect=command_run) as commands:
            pipeline.run(args)
        gemini.assert_called_once()
        self.assertEqual(commands.call_count, 2)
        self.assertIn('tera_speech.py', commands.call_args_list[0].args[0][1])
        self.assertEqual(commands.call_args_list[1].args[0][0], 'ffmpeg')

    def test_sendvoice_multipart_uses_voice_field(self):
        path = self.root / 'voice.ogg'; path.write_bytes(b'OggSdata')
        response = MagicMock()
        response.__enter__.return_value = io.BytesIO(b'{"ok":true,"result":{"voice":{"file_id":"id"}}}')
        with patch.object(pipeline.urllib.request, 'urlopen', return_value=response) as request:
            pipeline.telegram_call('token', 'sendVoice', {'chat_id': -100}, path)
        body = request.call_args.args[0].data
        self.assertIn(b'name="voice"; filename="voice.ogg"', body)
        self.assertIn(b'Content-Type: audio/ogg', body)


class VoiceWorkerTests(unittest.TestCase):
    def setUp(self):
        self.started = threading.Event(); self.release = threading.Event(); self.calls = []
        def run(request_id, text, kind='video'):
            self.calls.append((request_id, text, kind)); self.started.set(); self.release.wait(3)
            return 'voice-id' if kind == 'voice' else 'video-id'
        self.store = worker.JobStore(run)
        self.secret = 'secret-123456789012345678901'
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), worker.handler_for(self.store, self.secret))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.release.set(); self.server.shutdown(); self.server.server_close(); self.thread.join()

    def request(self, method, path, payload=None, authenticated=True):
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {'Authorization': 'Bearer ' + self.secret} if authenticated else {}
        request = urllib.request.Request(self.url + path, method=method, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=3) as result:
                return result.status, result.headers.get_content_type(), result.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get_content_type(), exc.read()

    def test_audio_video_share_worker_capacity_and_uuid_idempotency(self):
        job = str(uuid.uuid4()); data = {'request_id': job, 'text': 'Привет'}
        self.assertIn(b'voice-notes-v1', self.request('GET', '/health')[2])
        self.assertEqual(self.request('POST', '/voice-jobs', data)[0], 202)
        self.assertTrue(self.started.wait(2))
        self.assertEqual(self.request('POST', '/voice-jobs', data)[0], 200)
        self.assertEqual(self.request('POST', '/jobs', data)[0], 409)
        self.assertEqual(self.request('POST', '/jobs', {'request_id': str(uuid.uuid4()), 'text': 'Тест'})[0], 409)
        self.assertEqual(len(self.calls), 1)
        self.release.set()
        for _ in range(200):
            if self.store.get(job)['status'] == 'done': break
            time.sleep(.01)
        self.assertEqual(self.store.get(job)['file_id'], 'voice-id')
        self.assertEqual(self.calls[0][2], 'voice')

    def test_real_runner_requests_voice_without_video_scene_and_recovers_upload(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(worker.subprocess, 'Popen') as popen:
            root = Path(folder); job = str(uuid.uuid4()); output = root / job / 'clip-1'
            def fail_after_upload(**kwargs):
                output.mkdir(parents=True)
                (output / 'voice.ogg').write_bytes(b'OggSvoice')
                (output / 'storage-upload.pending').write_text('pending')
                return 1
            popen.return_value.wait.side_effect = fail_after_upload
            run = worker.runner(root, -1001, 'secret', 600, root/'plate.png', root/'portrait.png')
            with self.assertRaises(worker.UploadUncertain) as failure:
                run(job, 'Текст', kind='voice')
            command = popen.call_args.args[0]
            self.assertIn('--voice-only', command)
            self.assertNotIn('--smoke-plate', command)
            self.assertNotIn('--scene-seed', command)
            self.assertEqual(failure.exception.video_path, output/'voice.ogg')

    def test_uncertain_voice_is_authenticated_type_scoped_and_never_regenerated(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'voice.ogg'; path.write_bytes(b'OggS-voice')
            job = str(uuid.uuid4())
            self.store.jobs[job] = {'text': 'q', 'kind': 'voice', 'status': 'upload_uncertain', '_video_path': path}
            self.assertEqual(self.request('GET', f'/jobs/{job}/voice', authenticated=False)[0], 401)
            status, content_type, data = self.request('GET', f'/jobs/{job}/voice')
            self.assertEqual((status, content_type, data), (200, 'audio/ogg', b'OggS-voice'))
            self.assertEqual(self.request('GET', f'/jobs/{job}/video')[0], 404)
            self.assertEqual(self.calls, [])


class VoiceClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.posts = 0; self.capable = True; self.mode = 'ok'; self.job = None
        self.health_failures = 0; self.health_calls = 0
        async def health(request):
            self.health_calls += 1
            if self.health_failures:
                self.health_failures -= 1
                return web.json_response({'error': 'starting'}, status=503)
            return web.json_response({'capabilities': ['voice-notes-v1'] if self.capable else ['animated-sign-v1']})
        async def submit(request):
            self.posts += 1; self.job = await request.json()
            if self.mode == 'busy':
                return web.json_response({'error': 'Воркер занят'}, status=409)
            if self.mode == 'ambiguous':
                request.transport.close(); return web.Response()
            return web.json_response({'request_id': self.job['request_id'], 'status': 'running'}, status=202)
        async def get(request):
            status = 'upload_uncertain' if self.mode == 'uncertain' else 'done'
            return web.json_response({'request_id': request.match_info['id'], 'status': status, 'file_id': 'id'})
        async def voice(request):
            return web.Response(body=b'OggSvoice', content_type='audio/ogg')
        app = web.Application(); app.router.add_get('/health', health); app.router.add_post('/voice-jobs', submit)
        app.router.add_get('/jobs/{id}', get); app.router.add_get('/jobs/{id}/voice', voice)
        self.runner = web.AppRunner(app); await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0); await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.client = BenderWorkerClient(f'http://127.0.0.1:{port}', 'secret', poll_interval=.01, total_timeout=2)

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def test_voice_submission_capability_and_ambiguous_post_never_repeats(self):
        self.capable = False
        with self.assertRaises(WorkerUnavailable): await self.client.render_voice('Привет')
        self.assertEqual(self.posts, 0)
        self.capable = True; self.mode = 'ambiguous'
        job = str(uuid.uuid4())
        self.assertEqual(await self.client.render_voice('Привет', request_id=job), 'id')
        self.assertEqual(self.posts, 1)
        self.assertEqual(self.job['request_id'], job)

    async def test_worker_wake_retries_health_but_never_repeats_submission(self):
        self.health_failures = 1
        self.client.total_timeout = 5
        self.assertEqual(await self.client.render_voice('Привет'), 'id')
        self.assertEqual(self.health_calls, 2)
        self.assertEqual(self.posts, 1)

    async def test_old_worker_returns_without_health_retries(self):
        self.capable = False
        with self.assertRaises(WorkerUnavailable):
            await self.client.render_voice('Привет')
        self.assertEqual(self.health_calls, 1)
        self.assertEqual(self.posts, 0)

    async def test_busy_and_uncertain_media(self):
        self.mode = 'busy'
        with self.assertRaises(WorkerBusy): await self.client.render_voice('Привет')
        self.mode = 'uncertain'; job = str(uuid.uuid4())
        with self.assertRaises(WorkerUploadUncertain): await self.client.render_voice('Привет', request_id=job)
        self.assertEqual(await self.client.fetch_uncertain_voice(job), b'OggSvoice')


class VoiceChoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database = AsyncMongoMockClient().test
        self.db = SimpleNamespace(ex_response_choices=database.choices, auth_chats=database.auth_chats, personas=database.personas)
        await self.db.auth_chats.insert_one({'chat_id': -1001, 'status': 'approved'})
        await self.db.personas.insert_one({'p_id': 'bender', 'name': 'Бендер', 'emoji': '🤖'})
        self.flags = {'assistant': True, 'video_notes': True, 'voice_replies': True}
        self.menu = SimpleNamespace(message_id=99, chat=SimpleNamespace(id=-1001, type='supergroup'),
                                    edit_reply_markup=AsyncMock(), delete=AsyncMock())
        self.msg = SimpleNamespace(chat=self.menu.chat, message_id=42, message_thread_id=7,
            from_user=SimpleNamespace(id=123), bot=SimpleNamespace(send_message=AsyncMock()), reply=AsyncMock(return_value=self.menu))
        for p in [patch.object(response_choice, 'db', self.db),
                  patch.object(response_choice, 'get_chat_feature_flags', new=AsyncMock(side_effect=lambda _: self.flags))]:
            p.start(); self.addCleanup(p.stop)

    async def test_three_choices_and_voice_keeps_original_context(self):
        await response_choice.offer_response_choice(self.msg, 'вопрос', 'фото и диалог')
        markup = self.menu.edit_reply_markup.await_args.kwargs['reply_markup']
        self.assertEqual([b.text for row in markup.inline_keyboard for b in row], ['⭕ Кружок', '🎙 Голос', '💬 Текст'])
        doc = await self.db.ex_response_choices.find_one({'message_id': 42})
        cb = SimpleNamespace(data=f"exfmt:{doc['_id']}:voice", message=self.menu,
                             from_user=self.msg.from_user, bot=self.msg.bot, answer=AsyncMock())
        with patch.object(bender_clip, 'enqueue_voice_reply', new_callable=AsyncMock) as voice:
            await response_choice.response_choice_callback(cb)
        target, query, context = voice.await_args.args
        self.assertEqual((target.message_id, target.message_thread_id, query, context), (42, 7, 'вопрос', 'фото и диалог'))

    async def test_voice_without_video_and_disabled_or_forged_choice(self):
        self.flags['video_notes'] = False
        await response_choice.offer_response_choice(self.msg, 'вопрос', '')
        doc = await self.db.ex_response_choices.find_one({'message_id': 42})
        self.assertEqual(set(doc['formats']), {'voice', 'text'})
        cb = SimpleNamespace(data=f"exfmt:{doc['_id']}:video", message=self.menu,
                             from_user=self.msg.from_user, bot=self.msg.bot, answer=AsyncMock())
        await response_choice.response_choice_callback(cb)
        self.assertIn('не предлагался', cb.answer.await_args.args[0])
        self.flags['voice_replies'] = False
        cb.data = f"exfmt:{doc['_id']}:voice"
        await response_choice.response_choice_callback(cb)
        self.assertIn('выключены', cb.answer.await_args.args[0])
        self.assertEqual((await self.db.ex_response_choices.find_one({'_id': doc['_id']}))['state'], 'pending')

    async def test_unauthorized_other_user_and_double_click_never_enqueue_twice(self):
        await response_choice.offer_response_choice(self.msg, 'вопрос', 'контекст')
        doc = await self.db.ex_response_choices.find_one({'message_id': 42})
        def cb(user=123):
            return SimpleNamespace(data=f"exfmt:{doc['_id']}:voice", message=self.menu,
                                   from_user=SimpleNamespace(id=user), bot=self.msg.bot, answer=AsyncMock())
        bad = cb(999)
        await response_choice.response_choice_callback(bad)
        self.assertIn('автор', bad.answer.await_args.args[0])
        first, second = cb(), cb()
        with patch.object(bender_clip, 'enqueue_voice_reply', new_callable=AsyncMock) as voice:
            await asyncio.gather(response_choice.response_choice_callback(first), response_choice.response_choice_callback(second))
        voice.assert_awaited_once()


class VoiceQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.queue = FakeQueue()
        self.bot = SimpleNamespace(send_message=AsyncMock(), edit_message_text=AsyncMock(),
                                   delete_message=AsyncMock(), send_voice=AsyncMock(), send_video_note=AsyncMock())
        self.persona = SimpleNamespace(personas=SimpleNamespace(find_one=AsyncMock(return_value={'prompt': 'Бендер'})))
        self.client = MagicMock()
        self.client.render_voice = AsyncMock(return_value='voice-file')
        self.client.fetch_uncertain_voice = AsyncMock(return_value=b'OggS')
        self.client.resume = AsyncMock(return_value='voice-file')
        self.generate = AsyncMock(return_value='Мой голосовой ответ.')
        for p in [patch.object(bender_clip, 'db', self.persona),
                  patch.object(bender_clip, 'BenderWorkerClient', return_value=self.client),
                  patch.object(bender_clip, 'generate_with_retry', self.generate)]:
            p.start(); self.addCleanup(p.stop)
        self.item = {'id': 'job', 'request_id': str(uuid.uuid4()), 'kind': 'voice', 'stage': 'queued',
                     'chat_id': -1001, 'message_id': 42, 'thread_id': 7, 'status_message_id': None,
                     'query': 'Что это?', 'context': 'Фото: кот'}
        self.queue.items.append(self.item.copy())

    async def test_voice_and_video_use_same_fifo(self):
        self.queue.items.clear()
        status = SimpleNamespace(message_id=20, delete=AsyncMock())
        self.bot.send_message.return_value = status
        first = SimpleNamespace(chat=SimpleNamespace(id=-1), message_id=1, message_thread_id=7, bot=self.bot)
        second = SimpleNamespace(chat=SimpleNamespace(id=-2), message_id=2, message_thread_id=None, bot=self.bot)
        with patch.object(bender_clip, 'video_queue', self.queue):
            await bender_clip.enqueue_video_reply(first, 'один', '')
            await bender_clip.enqueue_voice_reply(second, 'два', '')
        self.assertEqual([item['kind'] for item in self.queue.items], ['video', 'voice'])
        self.assertEqual([item['query'] for item in self.queue.items], ['один', 'два'])

    async def test_no_studio_config_still_generates_text_fallback(self):
        self.queue.items.clear()
        status = SimpleNamespace(message_id=20, delete=AsyncMock())
        self.bot.send_message.return_value = status
        msg = SimpleNamespace(chat=SimpleNamespace(id=-1), message_id=1, message_thread_id=7, bot=self.bot)
        with patch.object(bender_clip, 'video_queue', self.queue), \
             patch.object(bender_clip, 'BenderWorkerClient', side_effect=ValueError('not configured')):
            await bender_clip.enqueue_voice_reply(msg, 'вопрос', '')
            self.assertEqual(self.queue.items[0]['kind'], 'voice')
            await bender_clip.process_queued_video(self.queue, self.bot, self.queue.items[0].copy())
        self.assertIn('Мой голосовой ответ.', self.bot.send_message.await_args.kwargs['text'])
        self.assertFalse(self.queue.items)

    async def test_generates_voice_with_context_and_no_video_render(self):
        await bender_clip.process_queued_video(self.queue, self.bot, self.item.copy())
        self.assertIn('Фото: кот', self.generate.await_args.args[0])
        self.assertIn('Верни только произносимый текст', self.generate.await_args.args[0])
        self.client.render_voice.assert_awaited_once_with('Мой голосовой ответ.', request_id=self.item['request_id'])
        self.bot.send_voice.assert_awaited_once()
        self.bot.send_video_note.assert_not_awaited()
        self.assertEqual(self.queue.items, [])

    async def test_worker_failure_returns_prepared_text(self):
        self.client.render_voice.side_effect = WorkerUnavailable()
        await bender_clip.process_queued_video(self.queue, self.bot, self.item.copy())
        self.assertIn('Мой голосовой ответ.', self.bot.send_message.await_args.kwargs['text'])
        self.bot.send_voice.assert_not_awaited()
        self.assertEqual(self.queue.items, [])

    async def test_uncertain_upload_fetches_same_voice_once(self):
        self.client.render_voice.side_effect = WorkerUploadUncertain()
        await bender_clip.process_queued_video(self.queue, self.bot, self.item.copy())
        self.client.fetch_uncertain_voice.assert_awaited_once_with(self.item['request_id'])
        self.client.render_voice.assert_awaited_once()
        self.bot.send_voice.assert_awaited_once()
        self.assertEqual(self.queue.items, [])

    async def test_definite_telegram_rejection_falls_back_to_text(self):
        from aiogram.exceptions import TelegramBadRequest
        self.bot.send_voice.side_effect = TelegramBadRequest(method=MagicMock(), message='voice prohibited')
        await bender_clip.process_queued_video(self.queue, self.bot, self.item.copy())
        self.assertIn('Мой голосовой ответ.', self.bot.send_message.await_args.kwargs['text'])
        self.bot.send_voice.assert_awaited_once()
        self.assertFalse(self.queue.items)

    async def test_ambiguous_voice_send_is_not_repeated_or_followed_by_answer(self):
        self.bot.send_voice.side_effect = OSError('connection lost')
        await bender_clip.process_queued_video(self.queue, self.bot, self.item.copy())
        self.bot.send_voice.assert_awaited_once()
        self.assertNotIn('Мой голосовой ответ.', self.bot.send_message.await_args.kwargs['text'])
        self.assertIn('проверь чат', self.bot.send_message.await_args.kwargs['text'].lower())
        self.assertFalse(self.queue.items)

    async def test_recovery_from_sending_never_sends_voice_twice(self):
        self.queue.items[0].update(stage='sending', answer='Ответ', file_id='id')
        await bender_clip.process_queued_video(self.queue, self.bot, self.queue.items[0].copy())
        self.bot.send_voice.assert_not_awaited()
        self.assertIn('проверь чат', self.bot.send_message.await_args.kwargs['text'].lower())

    async def test_busy_retries_same_uuid_without_regenerating_text(self):
        self.client.render_voice.side_effect = [WorkerBusy(), 'id']
        self.assertTrue(await bender_clip.process_queued_video(self.queue, self.bot, self.item.copy()))
        self.queue.items[0]['retry_after'] = 0
        await bender_clip.process_queued_video(self.queue, self.bot, self.queue.items[0].copy())
        self.assertEqual(self.generate.await_count, 1)
        self.assertEqual([c.kwargs['request_id'] for c in self.client.render_voice.await_args_list],
                         [self.item['request_id'], self.item['request_id']])


class VoiceAutoTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_voice_only_when_all_switches_on(self):
        msg = SimpleNamespace(chat=SimpleNamespace(id=-1001, type='supergroup'))
        flags = {'assistant': True, 'voice': True, 'voice_replies': True, 'auto_voice_replies': True}
        with patch.object(ex_logic, 'get_chat_feature_flags', new=AsyncMock(side_effect=lambda _: flags)), \
             patch.object(bender_clip, 'enqueue_voice_reply', new_callable=AsyncMock) as enqueue:
            self.assertTrue(await ex_logic._try_auto_voice(msg, 'вопрос', 'контекст', True))
            enqueue.assert_awaited_once_with(msg, 'вопрос', 'контекст')
            for key in ('assistant', 'voice', 'auto_voice_replies'):
                flags[key] = False
                self.assertFalse(await ex_logic._try_auto_voice(msg, 'вопрос', 'контекст', True))
                flags[key] = True
            flags['voice_replies'] = False
            self.assertTrue(await ex_logic._try_auto_voice(msg, 'вопрос', 'контекст', True))
            self.assertFalse(await ex_logic._try_auto_voice(msg, 'вопрос', 'контекст', False))
            self.assertEqual(enqueue.await_count, 2)

    async def test_special_music_intent_still_executes_without_auto_voice(self):
        message = SimpleNamespace(text='Экс, включи песню', chat=SimpleNamespace(id=-1001, type='supergroup'),
                                  from_user=SimpleNamespace(id=123), bot=SimpleNamespace(id=42),
                                  reply_to_message=None, reply=AsyncMock())
        personas = SimpleNamespace(find=MagicMock(return_value=SimpleNamespace(to_list=AsyncMock(return_value=[{'p_id': 'bender', 'name': 'Бендер'}]))))
        with patch.object(ex_logic, 'db', SimpleNamespace(personas=personas)), \
             patch.object(ex_logic, 'analyze_intent', new=AsyncMock(return_value=('MUSIC', 'песня'))), \
             patch.object(ex_logic, 'is_feature_enabled', new=AsyncMock(return_value=True)), \
             patch.object(ex_logic, 'handle_music_request', new_callable=AsyncMock) as music, \
             patch.object(bender_clip, 'enqueue_voice_reply', new_callable=AsyncMock) as voice:
            await ex_logic.handle_ex_request(message, prefer_voice=True)
        music.assert_awaited_once()
        voice.assert_not_awaited()


class VoiceEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from tests.test_voice_history_context import event
        self.event = event
        database = AsyncMongoMockClient().test
        self.db = SimpleNamespace(messages=database.messages, settings=database.settings,
                                  personas=database.personas, ex_response_choices=database.choices,
                                  auth_chats=database.auth_chats)
        await self.db.personas.insert_one({'p_id': 'bender', 'name': 'Бендер', 'emoji': '🤖'})
        await self.db.auth_chats.insert_one({'chat_id': -101, 'status': 'approved'})
        self.flags = {'assistant': True, 'voice': True, 'voice_replies': True,
                      'auto_voice_replies': True, 'video_notes': True}
        for p in [patch.object(voice_rec, 'db', self.db), patch.object(ex_logic, 'db', self.db),
                  patch.object(response_choice, 'db', self.db),
                  patch.object(voice_rec, 'get_chat_feature_flags', new=AsyncMock(side_effect=lambda _: self.flags)),
                  patch.object(ex_logic, 'get_chat_feature_flags', new=AsyncMock(side_effect=lambda _: self.flags)),
                  patch.object(response_choice, 'get_chat_feature_flags', new=AsyncMock(side_effect=lambda _: self.flags)),
                  patch.object(ex_logic, 'is_feature_enabled', new=AsyncMock(return_value=True))]:
            p.start(); self.addCleanup(p.stop)

    async def run_message(self, kind, transcript):
        msg = self.event(kind)
        msg.reply.return_value = SimpleNamespace(message_id=777, chat=msg.chat, edit_reply_markup=AsyncMock(), edit_text=AsyncMock(), delete=AsyncMock())
        msg.model_copy.side_effect = lambda update: SimpleNamespace(**{**vars(msg), **update})
        with patch.object(voice_rec, 'get_transcription', new=AsyncMock(return_value=transcript)), \
             patch.object(bender_clip, 'enqueue_voice_reply', new_callable=AsyncMock) as voice:
            await voice_rec.handle_voice_messages(msg)
        return msg, voice

    async def test_addressed_voice_is_auto_but_circle_opens_choice(self):
        with patch.object(ex_logic, 'analyze_intent', new=AsyncMock(return_value=('CHAT', 'как дела?'))):
            msg, voice = await self.run_message('voice', 'Экс, как дела?')
            voice.assert_awaited_once()
            self.assertEqual(voice.await_args.args[1], 'как дела?')
            msg.reply.assert_not_awaited()
            msg, voice = await self.run_message('video_note', 'Экс, как дела?')
            voice.assert_not_awaited()
            self.assertEqual(msg.reply.await_args.args[0], '🤖 Как мне ответить?')
            buttons = msg.reply.return_value.edit_reply_markup.await_args.kwargs['reply_markup']
            self.assertEqual([b.text for row in buttons.inline_keyboard for b in row],
                             ['⭕ Кружок', '🎙 Голос', '💬 Текст'])

    async def test_auto_off_opens_format_menu_and_voice_off_blocks(self):
        self.flags['auto_voice_replies'] = False
        with patch.object(ex_logic, 'analyze_intent', new=AsyncMock(return_value=('CHAT', 'как дела?'))):
            msg, voice = await self.run_message('voice', 'Экс, как дела?')
            voice.assert_not_awaited()
            self.assertEqual(msg.reply.await_args.args[0], '🤖 Как мне ответить?')
            buttons = msg.reply.return_value.edit_reply_markup.await_args.kwargs['reply_markup']
            self.assertIn('🎙 Голос', [b.text for row in buttons.inline_keyboard for b in row])
        self.flags['voice'] = False
        msg, voice = await self.run_message('video_note', 'Экс, как дела?')
        voice.assert_not_awaited(); msg.reply.assert_not_awaited()

    async def test_auto_on_does_not_override_special_intents(self):
        for intent in ('MUSIC', 'IMAGE', 'SEARCH', 'GOSSIP', 'POLE'):
            with self.subTest(intent=intent), \
                 patch.object(ex_logic, 'analyze_intent', new=AsyncMock(return_value=(intent, 'пример'))), \
                 patch.object(ex_logic, 'handle_music_request', new_callable=AsyncMock) as music, \
                 patch.object(ex_logic, 'process_image_request', new_callable=AsyncMock) as image, \
                 patch.dict(os.environ, {'SEARCH_IMAGES': ''}), \
                 patch.object(ex_logic, 'smart_web_search', new=AsyncMock(return_value='answer')) as search, \
                 patch.object(ex_logic, 'send_persona_selection', new_callable=AsyncMock) as gossip, \
                 patch.object(ex_logic, 'start_game', new_callable=AsyncMock) as pole:
                _, voice = await self.run_message('voice', 'Экс, пример')
                voice.assert_not_awaited()
                {'MUSIC': music, 'IMAGE': image, 'SEARCH': search, 'GOSSIP': gossip, 'POLE': pole}[intent].assert_awaited_once()

    async def test_circle_special_actions_still_bypass_choice(self):
        with patch.object(ex_logic, 'analyze_intent', new=AsyncMock(return_value=('MUSIC', 'песня'))), \
             patch.object(ex_logic, 'handle_music_request', new_callable=AsyncMock) as music, \
             patch.object(ex_logic, 'offer_response_choice', new_callable=AsyncMock) as offer:
            _, voice = await self.run_message('video_note', 'Экс, песня')
        music.assert_awaited_once()
        offer.assert_not_awaited()
        voice.assert_not_awaited()

    async def test_circle_with_only_text_enabled_uses_legacy_text(self):
        self.flags.update(voice_replies=False, video_notes=False)
        with patch.object(ex_logic, 'analyze_intent', new=AsyncMock(return_value=('CHAT', 'как дела?'))), \
             patch.object(ex_logic, 'generate_chat_response', new_callable=AsyncMock) as answer:
            msg, voice = await self.run_message('video_note', 'Экс, как дела?')
        answer.assert_awaited_once()
        voice.assert_not_awaited()
        msg.reply.assert_not_awaited()

    async def test_new_switches_default_off(self):
        self.assertFalse(admin_panel.FEATURES['voice_replies']['default'])
        self.assertFalse(admin_panel.FEATURES['auto_voice_replies']['default'])

    async def test_voice_menu_default_follows_video_and_explicit_setting_wins(self):
        with patch.object(admin_panel, 'db', self.db):
            flags = await admin_panel.get_chat_feature_flags(-101)
            self.assertFalse(flags['voice_replies'])
            await admin_panel.set_feature_state(-101, 'video_notes', True)
            flags = await admin_panel.get_chat_feature_flags(-101)
            self.assertTrue(flags['voice_replies'])
            await admin_panel.set_feature_state(-101, 'voice_replies', False)
            flags = await admin_panel.get_chat_feature_flags(-101)
            self.assertFalse(flags['voice_replies'])

    async def test_auto_works_when_voice_menu_is_disabled(self):
        self.flags['voice_replies'] = False
        with patch.object(ex_logic, 'analyze_intent', new=AsyncMock(return_value=('CHAT', 'привет'))):
            _, voice = await self.run_message('voice', 'Экс, привет')
        voice.assert_awaited_once()
