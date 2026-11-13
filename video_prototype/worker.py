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


def runner(output_dir, channel_id, token, timeout, smoke_plate=None):
    """Return a runner that isolates output/logs per UUID, using the existing CLI."""
    def run(request_id, text):
        job_dir = output_dir / request_id
        job_dir.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment.pop('VIDEO_WORKER_KEY', None)
        environment['BOT_TOKEN'] = token
        command = [
            sys.executable, '-u', str(HERE / 'pipeline.py'), '--text', text,
            '--channel-id', str(channel_id), '--output-dir', str(job_dir),
        ]
        if smoke_plate is not None:
            command.extend(['--smoke-plate', str(smoke_plate)])
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
                raise RuntimeError('Превышено время выполнения') from None
            if result != 0:
                raise RuntimeError('Процесс завершился с ошибкой')
        # The CLI creates a fresh clip directory for this invocation.
        results = sorted(job_dir.glob('clip-*/result.json'), key=lambda p: p.stat().st_mtime_ns)
        if not results:
            raise RuntimeError('Не найден результат загрузки')
        record = json.loads(results[-1].read_text(encoding='utf-8'))
        return record['file_id']
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
    store = JobStore(runner(args.output_dir, channel_id, token.strip(), args.timeout,
                            args.smoke_plate), cleanup_dir=args.output_dir)
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
