"""Isolated fallback trial using an existing MP4; never touches the running service.

Simulates a lost storage response, then reads the video through a temporary,
local-only worker and sends it once to a specified test chat. No storage upload,
TTS, or rendering is performed. A failed send is never retried automatically.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from video_prototype import pipeline, worker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video', type=Path, required=True)
    parser.add_argument('--chat-id', type=int, required=True)
    parser.add_argument('--thread-id', type=int)
    args = parser.parse_args()
    token = os.environ.get('BOT_TOKEN', '').strip()
    if not token:
        parser.error('BOT_TOKEN не задан')
    if not args.video.is_file() or not 0 < args.video.stat().st_size <= worker.MAX_VIDEO_BYTES:
        parser.error('Нужен готовый непустой MP4 размером до 50 МБ')

    class SimulatedProcess:
        def __init__(self, command, **kwargs):
            self.work = Path(command[command.index('--output-dir') + 1]) / 'clip-trial'
        def wait(self, timeout=None):
            self.work.mkdir(parents=True)
            shutil.copyfile(args.video, self.work / 'video.mp4')
            (self.work / 'storage-upload.pending').write_text('pending\n')
            return 1

    with tempfile.TemporaryDirectory(prefix='bender-fallback-trial-') as temp, \
         patch.object(worker.subprocess, 'Popen', SimulatedProcess):
        secret = secrets.token_urlsafe(32)
        store = worker.JobStore(worker.runner(Path(temp), -1, token, 600))
        server = ThreadingHTTPServer(('127.0.0.1', 0), worker.handler_for(store, secret))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            job = str(uuid.uuid4())
            base = f'http://127.0.0.1:{server.server_port}'
            headers = {'Authorization': 'Bearer ' + secret}
            request = urllib.request.Request(base + '/jobs', method='POST', headers=headers,
                data=json.dumps({'request_id': job, 'text': 'Изолированная проба'}).encode())
            with urllib.request.urlopen(request, timeout=10):
                pass
            deadline = time.monotonic() + 20
            while True:
                request = urllib.request.Request(base + '/jobs/' + job, headers=headers)
                with urllib.request.urlopen(request, timeout=10) as response:
                    state = json.load(response)['status']
                if state != 'running':
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('Локальный воркер не ответил вовремя')
                time.sleep(.1)
            if state != 'upload_uncertain':
                raise RuntimeError('Локальный воркер не подтвердил запасной путь')
            request = urllib.request.Request(base + '/jobs/' + job + '/video', headers=headers)
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read(worker.MAX_VIDEO_BYTES + 1)
            if not 0 < len(data) <= worker.MAX_VIDEO_BYTES:
                raise RuntimeError('Видео не удалось получить')
            video = Path(temp) / 'downloaded.mp4'
            video.write_bytes(data)
            fields = {'chat_id': args.chat_id}
            if args.thread_id is not None:
                fields['message_thread_id'] = args.thread_id
            print('Запасной путь готов. Один раз отправляю видео в тестовый чат...', flush=True)
            try:
                pipeline.telegram_call(token, 'sendVideoNote', fields, video)
            except Exception as exc:
                print('Ответ Telegram не подтверждён. Проверь чат перед любыми повторами.', flush=True)
                raise RuntimeError('Отправка не подтверждена: ' + type(exc).__name__) from None
            print('Telegram подтвердил отправку в тестовый чат.', flush=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    main()
