"""Small, manual-only HTTP worker for video-note experiments.

It keeps jobs in memory, allows one active job, and returns a Telegram file_id.
It is not a production service: restart loses jobs and replay protection is not
persistent. The caller owns chat routing and reply context.
"""
import argparse
import hmac
import json
import itertools
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

if __package__:
    from .sign_text import normalize_sign
else:
    from sign_text import normalize_sign

HERE = Path(__file__).resolve().parent
MAX_BODY = 8192
MAX_JOBS = 100
RETENTION_SECONDS = 30 * 24 * 60 * 60
CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60
MAX_VIDEO_BYTES = 50 * 1024 * 1024


class UploadUncertain(Exception):
    """Storage upload has no reliable response; media was already created."""
    def __init__(self, video_path, kind='video'):
        super().__init__('Результат загрузки на склад неизвестен')
        self.video_path = Path(video_path)
        self.kind = kind


class BusyError(Exception):
    pass


class JobConflict(Exception):
    pass


class JobStore:
    def __init__(self, run_job, cleanup_dir=None):
        self.lock = threading.Lock()
        self.jobs = {}
        self.active = None
        self.run_job = run_job
        self.cleanup_dir = Path(cleanup_dir) if cleanup_dir is not None else None
        self.next_cleanup = 0.0
        self.cleanup_running = False
        # Optional safety gate supplied by the disabled-by-default news extension.
        self.external_busy = None

    def submit(self, request_id, text, sign=None, kind='video'):
        if kind not in ('video', 'voice') or (kind == 'voice' and sign):
            raise ValueError('Некорректный формат задачи')
        sign = normalize_sign(sign)
        # Lock covers duplicate detection, capacity check, and job creation.
        with self.lock:
            existing = self.jobs.get(request_id)
            if existing:
                if existing['text'] != text or existing.get('sign') != sign or existing.get('kind', 'video') != kind:
                    raise JobConflict('request_id уже использован для другого текста')
                return self._public(request_id, existing), False
            if self.active is not None or (self.external_busy is not None and self.external_busy()):
                raise BusyError('Воркер занят')
            # Completed records are bounded; an evicted request can be submitted again.
            while len(self.jobs) >= MAX_JOBS:
                self.jobs.pop(next(iter(self.jobs)))
            self.jobs[request_id] = {'text': text, 'sign': sign, 'kind': kind, 'status': 'running'}
            self.active = request_id
            threading.Thread(target=self._execute, args=(request_id, text, sign, kind), daemon=True).start()
            return self._public(request_id, self.jobs[request_id]), True

    def _execute(self, request_id, text, sign, kind):
        try:
            if kind == 'voice':
                file_id = self.run_job(request_id, text, kind='voice')
            else:
                file_id = self.run_job(request_id, text, sign) if sign else self.run_job(request_id, text)
            if not isinstance(file_id, str) or not file_id:
                raise ValueError('Не получен file_id')
            outcome = {'status': 'done', 'file_id': file_id}
        except UploadUncertain as exc:
            print(f'Задача {request_id}: ответ склада неизвестен, медиа сохранено', flush=True)
            outcome = {'status': 'upload_uncertain', '_video_path': exc.video_path}
        except Exception as exc:
            # Never echo subprocess arguments, HTTP URLs, tokens, or input into the API.
            print(f'Задача {request_id}: ошибка {type(exc).__name__}; проверь журнал задачи и консоль сервиса', flush=True)
            outcome = {'status': 'failed', 'error': 'Не удалось создать или загрузить медиа'}
        with self.lock:
            self.jobs[request_id].update(outcome)
            self.active = None
        self.schedule_cleanup()

    def schedule_cleanup(self):
        """Scan at startup and at most daily after jobs; never wake an idle VM."""
        if self.cleanup_dir is None:
            return
        with self.lock:
            now = time.monotonic()
            if self.cleanup_running or now < self.next_cleanup:
                return
            self.cleanup_running = True
            self.next_cleanup = now + CLEANUP_INTERVAL_SECONDS
        try:
            threading.Thread(target=self._cleanup_background, daemon=True).start()
        except RuntimeError as exc:
            with self.lock:
                self.cleanup_running = False
                self.next_cleanup = 0.0
            print(f'Очистка задач: не удалось запустить поток {type(exc).__name__}', flush=True)

    def _cleanup_background(self):
        try:
            self.cleanup_old_jobs()
        except Exception as exc:
            # Maintenance must never fail the worker or disclose file contents.
            print(f'Очистка задач: ошибка {type(exc).__name__}', flush=True)
            with self.lock:
                self.next_cleanup = 0.0  # Retry after the next completed job.
        finally:
            with self.lock:
                self.cleanup_running = False

    def cleanup_old_jobs(self, *, now=None):
        """Remove old job folders only; synchronize deletion with submission."""
        if self.cleanup_dir is None:
            return 0
        now = time.time() if now is None else now
        try:
            entries = list(self.cleanup_dir.iterdir())
        except FileNotFoundError:
            return 0
        removed = 0
        for path in entries:
            try:
                # Only canonical UUID-named directories from this worker, never links.
                if str(uuid.UUID(path.name)) != path.name:
                    continue
            except ValueError:
                continue
            with self.lock:
                if path.name == self.active:
                    continue
                try:
                    info = path.lstat()
                    if not stat.S_ISDIR(info.st_mode):
                        continue
                    log_info = (path / 'worker.log').lstat()
                    if not stat.S_ISREG(log_info.st_mode):
                        continue
                    latest = max(info.st_mtime, log_info.st_mtime)
                    if now - latest < RETENTION_SECONDS:
                        continue
                    # Submit cannot mark this UUID active until removal finishes.
                    shutil.rmtree(path)
                    removed += 1
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    print(f'Очистка задачи {path.name}: ошибка {type(exc).__name__}', flush=True)
        if removed:
            print(f'Очистка задач: удалено папок {removed}', flush=True)
        return removed

    @staticmethod
    def _public(request_id, job):
        result = {'request_id': request_id, 'status': job['status']}
        for field in ('file_id', 'error'):
            if field in job:
                result[field] = job[field]
        return result

    def get(self, request_id):
        with self.lock:
            job = self.jobs.get(request_id)
            return self._public(request_id, job) if job else None

    def read_uncertain_video(self, request_id):
        return self._read_uncertain(request_id, 'video')

    def read_uncertain_voice(self, request_id):
        return self._read_uncertain(request_id, 'voice')

    def _read_uncertain(self, request_id, kind):
        """Expose only the media of the requested type for an uncertain job."""
        with self.lock:
            job = self.jobs.get(request_id)
            if not job or job.get('status') != 'upload_uncertain' or job.get('kind', 'video') != kind:
                return None
            path = job['_video_path']
        try:
            # The path comes only from the runner, never from the request.
            # Do not follow a final symlink and do not return arbitrarily large files.
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_VIDEO_BYTES:
                    return None
                data = source.read(MAX_VIDEO_BYTES + 1)
            return data if 0 < len(data) <= MAX_VIDEO_BYTES else None
        except OSError:
            return None


def validate_request(data):
    if not isinstance(data, dict) or not {'request_id', 'text'} <= set(data) or set(data) - {'request_id', 'text', 'sign'}:
        raise ValueError('Нужны request_id, text и необязательная sign')
    text = data['text']
    if not isinstance(text, str) or not text.strip() or len(text) > 480:
        raise ValueError('Текст должен содержать от 1 до 480 символов')
    request_id = data['request_id']
    if not isinstance(request_id, str):
        raise ValueError('request_id должен быть UUID')
    try:
        parsed = uuid.UUID(request_id)
    except (ValueError, AttributeError):
        raise ValueError('request_id должен быть UUID') from None
    if str(parsed) != request_id:
        raise ValueError('request_id должен быть UUID в стандартной записи')
    return request_id, text, normalize_sign(data.get('sign'))


def runner(output_dir, channel_id, token, timeout, smoke_plate=None, scene_portrait=None):
    """Return a runner that isolates output/logs per UUID, using the existing CLI."""
    key_rotation = itertools.count()  # One worker serializes jobs; restart resets rotation.
    def run(request_id, text, sign=None, kind='video'):
        job_dir = output_dir / request_id
        job_dir.mkdir(parents=True, exist_ok=True)
        # Never recover a stale clip from an earlier execution with the same UUID.
        previous = set(job_dir.glob('clip-*'))
        environment = os.environ.copy()
        environment.pop('VIDEO_WORKER_KEY', None)
        environment['BOT_TOKEN'] = token
        environment['BENDER_TTS_START_INDEX'] = str(next(key_rotation))
        command = [
            sys.executable, '-u', str(HERE / 'pipeline.py'), '--text', text,
            '--channel-id', str(channel_id), '--output-dir', str(job_dir),
        ]
        if kind == 'voice':
            command.append('--voice-only')
        elif smoke_plate is not None:
            command.extend(['--smoke-plate', str(smoke_plate)])
        if kind == 'video' and scene_portrait is not None:
            # A UUID determines the scene without affecting the separate TTS seed.
            command.extend(['--scene-seed', request_id, '--portrait', str(scene_portrait)])
            if sign:
                command.append('--sign=' + sign)
        with (job_dir / 'worker.log').open('a', encoding='utf-8') as log:
            process = subprocess.Popen(command, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                result = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Stop the pipeline and its TTS/renderer children together.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                result = None
        # Only inspect clip folders made during this invocation. A complete
        # result takes precedence even if the subprocess subsequently failed.
        current = [p for p in job_dir.glob('clip-*') if p not in previous and p.is_dir() and not p.is_symlink()]
        for clip in current:
            try:
                record = json.loads((clip / 'result.json').read_text(encoding='utf-8'))
                file_id = record.get('file_id')
                if isinstance(file_id, str) and file_id:
                    return file_id
            except (OSError, ValueError, AttributeError):
                pass
        # A marker is written only after rendering and immediately before the
        # storage upload. The destination chat has not been contacted here.
        for clip in current:
            video = clip / ('voice.ogg' if kind == 'voice' else 'video.mp4')
            try:
                info = video.lstat()
                marker = (clip / 'storage-upload.pending').lstat()
                if stat.S_ISREG(info.st_mode) and 0 < info.st_size <= MAX_VIDEO_BYTES and stat.S_ISREG(marker.st_mode):
                    raise UploadUncertain(video, kind)
            except OSError:
                continue
        if result is None:
            raise RuntimeError('Превышено время выполнения')
        raise RuntimeError('Процесс завершился с ошибкой или результат отсутствует')
    return run


def handler_for(store, secret, news=None):
    secret_bytes = secret.encode('utf-8')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # The default logger includes raw paths; no input or token in logs.
            return

        def respond(self, status, data):
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            header = self.headers.get('Authorization', '')
            prefix = 'Bearer '
            return header.startswith(prefix) and hmac.compare_digest(
                header[len(prefix):].encode('utf-8'), secret_bytes
            )

        def do_GET(self):
            if not self.authorized():
                return self.respond(401, {'error': 'unauthorized'})
            path = urlsplit(self.path).path
            if path == '/health':
                capabilities = ['animated-sign-v1', 'voice-notes-v1']
                if news is not None:
                    capabilities.extend(['news-story-v1', 'news-story-image-v1', 'news-image-focus-v1'])
                    if news.run_program is not None:
                        capabilities.append('news-program-v1')
                    if getattr(news, 'run_bulletin', None) is not None:
                        capabilities.append('news-bulletin-v1')
                        if shutil.which('ffprobe'):
                            capabilities.append('news-bulletin-video-v1')
                return self.respond(200, {'status': 'ok', 'capabilities': capabilities})
            news_match = re.fullmatch(r'/news-jobs/([0-9a-f-]{36})(/video)?', path)
            if news is not None and news_match:
                request_id = news_match.group(1)
                try:
                    state = news.status(request_id)
                except ValueError:
                    state = None
                if state is None:
                    return self.respond(404, {'error': 'not_found'})
                if not news_match.group(2):
                    return self.respond(200, state)
                if state['status'] != 'ready':
                    return self.respond(404, {'error': 'not_found'})
                try:
                    fd, size = news.open_video(request_id)
                except (ValueError, OSError):
                    return self.respond(404, {'error': 'not_found'})
                with os.fdopen(fd, 'rb') as video:
                    self.send_response(200)
                    self.send_header('Content-Type', 'video/mp4')
                    self.send_header('Content-Length', str(size))
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    shutil.copyfileobj(video, self.wfile, length=1024 * 1024)
                return
            voice_match = re.fullmatch(r'/jobs/([0-9a-f-]{36})/voice', path)
            if voice_match:
                data = store.read_uncertain_voice(voice_match.group(1))
                if data is None:
                    return self.respond(404, {'error': 'not_found'})
                self.send_response(200)
                self.send_header('Content-Type', 'audio/ogg')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(data)
                return
            video_match = re.fullmatch(r'/jobs/([0-9a-f-]{36})/video', path)
            if video_match:
                data = store.read_uncertain_video(video_match.group(1))
                if data is None:
                    return self.respond(404, {'error': 'not_found'})
                self.send_response(200)
                self.send_header('Content-Type', 'video/mp4')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(data)
                return
            match = re.fullmatch(r'/jobs/([0-9a-f-]{36})', path)
            if not match:
                return self.respond(404, {'error': 'not_found'})
            job = store.get(match.group(1))
            return self.respond(200, job) if job else self.respond(404, {'error': 'not_found'})

        def do_POST(self):
            if not self.authorized():
                return self.respond(401, {'error': 'unauthorized'})
            path = urlsplit(self.path).path
            if path not in ('/jobs', '/voice-jobs') and not (news is not None and path in ('/news-jobs', '/news-digests', '/news-programs', '/news-bulletins')):
                return self.respond(404, {'error': 'not_found'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                return self.respond(400, {'error': 'invalid_length'})
            # An optional sanitized thumbnail is bounded separately by its codec.
            limit = (5_100_000 if path == '/news-bulletins' else 2_000_000 if path == '/news-programs' else
                     600_000 if path == '/news-jobs' else 100_000 if path == '/news-digests' else MAX_BODY)
            if not 0 < length <= limit:
                return self.respond(413, {'error': 'invalid_length'})
            if path in ('/news-jobs', '/news-digests', '/news-programs', '/news-bulletins'):
                from video_prototype.news_jobs import NewsBusy, NewsConflict, validate_request as validate_news, validate_digest, validate_program, validate_bulletin
                try:
                    data = json.loads(self.rfile.read(length).decode('utf-8'))
                    if path == '/news-bulletins':
                        request_id, bulletin = validate_bulletin(data)
                        job, created = news.submit_bulletin(request_id, bulletin)
                    elif path == '/news-digests':
                        request_id, inputs = validate_digest(data)
                        job, created = news.submit_digest(request_id, inputs)
                    elif path == '/news-programs':
                        request_id, scripts = validate_program(data)
                        job, created = news.submit_program(request_id, scripts)
                    else:
                        request_id, script = validate_news(data)
                        job, created = news.submit(request_id, script)
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
                    return self.respond(400, {'error': 'invalid_news_job'})
                except NewsBusy:
                    return self.respond(409, {'error': 'busy'})
                except NewsConflict:
                    return self.respond(409, {'error': 'conflict'})
                except (OSError, RuntimeError):
                    # A reservation may already exist. Caller must inspect it,
                    # not submit a new UUID after an uncertain response.
                    return self.respond(503, {'error': 'news_submission_uncertain'})
                return self.respond(202 if created else 200, job)
            try:
                data = json.loads(self.rfile.read(length).decode('utf-8'))
                request_id, text, sign = validate_request(data)
                kind = 'voice' if path == '/voice-jobs' else 'video'
                if kind == 'voice' and sign:
                    raise ValueError('Для голосового надпись не поддерживается')
                job, created = store.submit(request_id, text, sign, kind=kind)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                return self.respond(400, {'error': str(exc)})
            except (BusyError, JobConflict) as exc:
                return self.respond(409, {'error': str(exc)})
            return self.respond(202 if created else 200, job)

    return Handler


def main():
    parser = argparse.ArgumentParser(description='Экспериментальный видеоворкер')
    parser.add_argument('--bind', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8088)
    parser.add_argument('--output-dir', type=Path, default=Path('/data/video-prototype/jobs'))
    parser.add_argument('--smoke-plate', type=Path, help='Бездымная подложка для анимации дыма')
    parser.add_argument('--random-guests', action='store_true', help='Включить одобренную сцену со случайными гостями')
    parser.add_argument('--portrait', type=Path, help='Сохранённый портрет сенсея')
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args()
    secret = os.environ.get('VIDEO_WORKER_KEY', '')
    token = os.environ.get('BOT_TOKEN', '')
    channel = os.environ.get('STORAGE_CHANNEL_ID', '')
    if len(secret) < 24 or not token.strip():
        parser.error('Нужны VIDEO_WORKER_KEY (не менее 24 символов) и BOT_TOKEN')
    try:
        channel_id = int(channel)
    except ValueError:
        parser.error('STORAGE_CHANNEL_ID должен быть числом')
    if args.timeout <= 0 or not 1 <= args.port <= 65535:
        parser.error('Некорректный порт или таймаут')
    if args.smoke_plate is not None and not args.smoke_plate.is_file():
        parser.error('Бездымная подложка не найдена')
    if args.random_guests and (not args.smoke_plate or not args.portrait or not args.portrait.is_file()):
        parser.error('Для случайных гостей нужны бездымная подложка и доступный портрет сенсея')
    if not args.random_guests and args.portrait:
        parser.error('--portrait нужен только вместе с --random-guests')
    store = JobStore(runner(args.output_dir, channel_id, token.strip(), args.timeout,
                            args.smoke_plate, args.portrait if args.random_guests else None),
                     cleanup_dir=args.output_dir)
    news = None
    if os.getenv('NEWS_WORKER_ENABLED', '').strip() == '1':
        # The new mode is opt-in and uses the exact same active slot as circles.
        # Import lazily so the current deployment does not require news assets.
        if str(HERE.parent) not in sys.path:
            sys.path.insert(0, str(HERE.parent))
        from news_prototype import story_pipeline  # noqa: F401
        from video_prototype.news_jobs import NewsJobs, runner as news_runner, digest_runner, program_runner, bulletin_runner, bulletin_assets_ready
        news_root = args.output_dir.parent / 'news-jobs'
        news = NewsJobs(store, news_root, news_runner(HERE.parent), digest_runner(HERE.parent, news_root),
                        program_runner(HERE.parent), bulletin_runner(HERE.parent) if bulletin_assets_ready() else None)
        if news.run_bulletin is None:
            print('Вестник недоступен: исходные слои или шрифт не прошли проверку.', flush=True)
    server = ThreadingHTTPServer((args.bind, args.port), handler_for(store, secret, news))
    store.schedule_cleanup()
    print(f'Воркер запущен на {args.bind}:{args.port}; один запрос за раз', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
