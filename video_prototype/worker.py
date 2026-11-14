"""Small, manual-only HTTP worker for video-note experiments.

It keeps jobs in memory, allows one active job, and returns a Telegram file_id.
It is not a production service: restart loses jobs and replay protection is not
persistent. The caller owns chat routing and reply context.
"""
import argparse
import hmac
import json
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

HERE = Path(__file__).resolve().parent
MAX_BODY = 8192
MAX_JOBS = 100
RETENTION_SECONDS = 30 * 24 * 60 * 60
CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60
MAX_VIDEO_BYTES = 50 * 1024 * 1024


class UploadUncertain(Exception):
    """Storage upload has no reliable response; video was already rendered."""
    def __init__(self, video_path):
        super().__init__('Результат загрузки на склад неизвестен')
        self.video_path = Path(video_path)


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

    def submit(self, request_id, text):
        # Lock covers duplicate detection, capacity check, and job creation.
        with self.lock:
            existing = self.jobs.get(request_id)
            if existing:
                if existing['text'] != text:
                    raise JobConflict('request_id уже использован для другого текста')
                return self._public(request_id, existing), False
            if self.active is not None:
                raise BusyError('Воркер занят')
            # Completed records are bounded; an evicted request can be submitted again.
            while len(self.jobs) >= MAX_JOBS:
                self.jobs.pop(next(iter(self.jobs)))
            self.jobs[request_id] = {'text': text, 'status': 'running'}
            self.active = request_id
            threading.Thread(target=self._execute, args=(request_id, text), daemon=True).start()
            return self._public(request_id, self.jobs[request_id]), True

    def _execute(self, request_id, text):
        try:
            file_id = self.run_job(request_id, text)
            if not isinstance(file_id, str) or not file_id:
                raise ValueError('Не получен file_id')
            outcome = {'status': 'done', 'file_id': file_id}
        except UploadUncertain as exc:
            print(f'Задача {request_id}: ответ склада неизвестен, видео сохранено', flush=True)
            outcome = {'status': 'upload_uncertain', '_video_path': exc.video_path}
        except Exception as exc:
            # Never echo subprocess arguments, HTTP URLs, tokens, or input into the API.
            print(f'Задача {request_id}: ошибка {type(exc).__name__}; проверь журнал задачи и консоль сервиса', flush=True)
            outcome = {'status': 'failed', 'error': 'Не удалось создать или загрузить кружок'}
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
        """Only expose a completed local video for a job in the uncertain state."""
        with self.lock:
            job = self.jobs.get(request_id)
            if not job or job.get('status') != 'upload_uncertain':
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
    if not isinstance(data, dict) or set(data) != {'request_id', 'text'}:
        raise ValueError('Нужны только request_id и text')
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
    return request_id, text


def runner(output_dir, channel_id, token, timeout, smoke_plate=None, scene_portrait=None):
    """Return a runner that isolates output/logs per UUID, using the existing CLI."""
    def run(request_id, text):
        job_dir = output_dir / request_id
        job_dir.mkdir(parents=True, exist_ok=True)
        # Never recover a stale clip from an earlier execution with the same UUID.
        previous = set(job_dir.glob('clip-*'))
        environment = os.environ.copy()
        environment.pop('VIDEO_WORKER_KEY', None)
        environment['BOT_TOKEN'] = token
        command = [
            sys.executable, '-u', str(HERE / 'pipeline.py'), '--text', text,
            '--channel-id', str(channel_id), '--output-dir', str(job_dir),
        ]
        if smoke_plate is not None:
            command.extend(['--smoke-plate', str(smoke_plate)])
        if scene_portrait is not None:
            # A UUID determines the scene without affecting the separate TTS seed.
            command.extend(['--scene-seed', request_id, '--portrait', str(scene_portrait)])
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
            video = clip / 'video.mp4'
            try:
                info = video.lstat()
                marker = (clip / 'storage-upload.pending').lstat()
                if stat.S_ISREG(info.st_mode) and 0 < info.st_size <= MAX_VIDEO_BYTES and stat.S_ISREG(marker.st_mode):
                    raise UploadUncertain(video)
            except OSError:
                continue
        if result is None:
            raise RuntimeError('Превышено время выполнения')
        raise RuntimeError('Процесс завершился с ошибкой или результат отсутствует')
    return run


def handler_for(store, secret):
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
                return self.respond(200, {'status': 'ok'})
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
            if urlsplit(self.path).path != '/jobs':
                return self.respond(404, {'error': 'not_found'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                return self.respond(400, {'error': 'invalid_length'})
            if not 0 < length <= MAX_BODY:
                return self.respond(413, {'error': 'invalid_length'})
            try:
                data = json.loads(self.rfile.read(length).decode('utf-8'))
                request_id, text = validate_request(data)
                job, created = store.submit(request_id, text)
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
    server = ThreadingHTTPServer((args.bind, args.port), handler_for(store, secret))
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
