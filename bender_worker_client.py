"""Client for the experimental video worker (no chat routing or secrets in logs)."""
import asyncio
import time
import uuid
from urllib.parse import urlsplit

import aiohttp


class WorkerError(Exception):
    """Failure safe to present to the user without exposing request details."""


class WorkerBusy(WorkerError):
    pass


class WorkerUnavailable(WorkerError):
    pass


class WorkerFailed(WorkerError):
    pass


class WorkerUploadUncertain(WorkerError):
    """Storage response was lost, but the worker has a finished video."""
    pass


class WorkerTimeout(WorkerError):
    pass


def validate_url(base_url):
    parsed = urlsplit(base_url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('Некорректный адрес воркера')
    if not parsed.hostname or parsed.scheme not in ('https', 'http'):
        raise ValueError('Некорректный адрес воркера')
    # HTTP is supported only for an explicit local test, never for the published worker.
    if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('Для внешнего адреса воркера требуется HTTPS')
    return base_url.rstrip('/')


class BenderWorkerClient:
    def __init__(self, base_url, secret, *, total_timeout=660, request_timeout=20, poll_interval=3):
        self.base_url = validate_url(base_url)
        if not secret or total_timeout <= 0 or request_timeout <= 0 or poll_interval <= 0:
            raise ValueError('Некорректные настройки воркера')
        self.secret = secret
        self.total_timeout = total_timeout
        self.request_timeout = request_timeout
        self.poll_interval = poll_interval

    async def render(self, text, *, request_id=None):
        if not isinstance(text, str) or not text.strip() or len(text) > 480:
            raise ValueError('Текст должен содержать от 1 до 480 символов')
        request_id = request_id or str(uuid.uuid4())
        if str(uuid.UUID(request_id)) != request_id:
            raise ValueError('Некорректный request_id')
        deadline = time.monotonic() + self.total_timeout
        timeout = aiohttp.ClientTimeout(total=self.request_timeout)
        headers = {'Authorization': 'Bearer ' + self.secret}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            # Never automatically submit again after an ambiguous failure. The
            # worker's in-memory idempotency cannot survive a restart.
            try:
                async with session.post(
                    self.base_url + '/jobs', json={'request_id': request_id, 'text': text},
                    allow_redirects=False, timeout=aiohttp.ClientTimeout(
                        total=min(self.request_timeout, max(0.001, deadline - time.monotonic()))
                    ),
                ) as response:
                    if response.status == 409:
                        payload = await response.json(content_type=None)
                        if isinstance(payload, dict) and payload.get('error') in ('Воркер занят', 'busy'):
                            raise WorkerBusy('Воркер занят')
                        raise WorkerUnavailable('Конфликт запроса воркера')
                    if response.status not in (200, 202):
                        raise WorkerUnavailable('Сервис не принял запрос')
                    job = await response.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                # The POST may have reached the worker; query the same UUID.
                job = await self._get_job(session, request_id, deadline, retry_not_found=False)
            return await self._wait_for_job(session, request_id, job, deadline)

    async def resume(self, request_id):
        """Check a previously submitted UUID without ever submitting it again."""
        if str(uuid.UUID(request_id)) != request_id:
            raise ValueError('Некорректный request_id')
        deadline = time.monotonic() + self.total_timeout
        timeout = aiohttp.ClientTimeout(total=self.request_timeout)
        headers = {'Authorization': 'Bearer ' + self.secret}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            job = await self._get_job(session, request_id, deadline, retry_not_found=False)
            return await self._wait_for_job(session, request_id, job, deadline)

    async def fetch_uncertain_video(self, request_id):
        """Read a finished video for the same UUID; never resubmit generation."""
        try:
            if str(uuid.UUID(request_id)) != request_id:
                raise ValueError('Некорректный request_id')
        except (ValueError, TypeError, AttributeError):
            raise ValueError('Некорректный request_id') from None
        limit = 50 * 1024 * 1024
        # This request transfers the whole video, unlike the small status polls.
        timeout = aiohttp.ClientTimeout(total=max(120, self.request_timeout))
        headers = {'Authorization': 'Bearer ' + self.secret}
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(self.base_url + '/jobs/' + request_id + '/video',
                                       allow_redirects=False) as response:
                    if response.status != 200 or response.content_type != 'video/mp4':
                        raise WorkerUnavailable('Не удалось получить сохранённое видео')
                    if response.content_length is not None and response.content_length > limit:
                        raise WorkerUnavailable('Видео слишком большое')
                    data = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        data.extend(chunk)
                        if len(data) > limit:
                            raise WorkerUnavailable('Видео слишком большое')
                    if not data:
                        raise WorkerUnavailable('Сохранённое видео пустое')
                    return bytes(data)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            raise WorkerUnavailable('Не удалось получить сохранённое видео') from None

    async def _get_job(self, session, request_id, deadline, *, retry_not_found=True):
        while time.monotonic() < deadline:
            try:
                async with session.get(
                    self.base_url + '/jobs/' + request_id, allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=min(self.request_timeout, max(0.001, deadline - time.monotonic())))
                ) as response:
                    if response.status == 404:
                        if not retry_not_found:
                            raise WorkerUnavailable('Статус отправленного запроса неизвестен')
                        raise WorkerUnavailable('Задача не найдена; воркер мог перезапуститься')
                    if response.status in (401, 403) or 300 <= response.status < 400:
                        raise WorkerUnavailable('Сервис отклонил запрос')
                    if response.status != 200:
                        raise aiohttp.ClientError('temporary failure')
                    return await response.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                await asyncio.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))
        raise WorkerTimeout('Время ожидания воркера истекло')

    async def _wait_for_job(self, session, request_id, job, deadline):
        while True:
            if not isinstance(job, dict) or job.get('request_id') != request_id:
                raise WorkerUnavailable('Некорректный ответ воркера')
            status = job.get('status')
            if status == 'done':
                file_id = job.get('file_id')
                if not isinstance(file_id, str) or not file_id:
                    raise WorkerUnavailable('Воркер не вернул file_id')
                return file_id
            if status == 'upload_uncertain':
                raise WorkerUploadUncertain('Ответ склада потерян, видео сохранено')
            if status == 'failed':
                raise WorkerFailed('Воркер не смог создать кружок')
            if status != 'running':
                raise WorkerUnavailable('Неизвестный статус воркера')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WorkerTimeout('Время ожидания воркера истекло')
            await asyncio.sleep(min(self.poll_interval, remaining))
            job = await self._get_job(session, request_id, deadline)
