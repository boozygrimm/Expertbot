"""Explicit news worker client; no Telegram upload or automatic retry of POST.

POST uncertainty is resolved only by querying the same UUID. A missing result
remains uncertain, never an instruction to submit a fresh job automatically.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid
import aiohttp
from bender_worker_client import validate_url


class NewsWorkerError(Exception): pass
class NewsBusy(NewsWorkerError): pass
class NewsConflict(NewsWorkerError): pass
class NewsUncertain(NewsWorkerError): pass


async def read_json(response):
    data = bytearray()
    async for chunk in response.content.iter_chunked(4096):
        data.extend(chunk)
        if len(data) > 8192:
            raise ValueError('Слишком большой ответ')
    return json.loads(data)


def valid_id(value: str) -> str:
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError
    except (AttributeError, ValueError):
        raise ValueError('Неверный ID задачи') from None
    return value


class NewsWorkerClient:
    def __init__(self, base_url: str, secret: str):
        self.base_url = validate_url(base_url)
        if not secret:
            raise ValueError('Нет ключа воркера')
        self.headers = {'Authorization': 'Bearer ' + secret}
        self.timeout = aiohttp.ClientTimeout(total=30)

    async def _supports(self, capability: str) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.base_url + '/health', headers=self.headers,
                                       allow_redirects=False, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status != 200:
                        return False
                    data = await read_json(resp)
                    return (isinstance(data, dict) and isinstance(data.get('capabilities'), list)
                            and capability in data['capabilities'])
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return False

    async def supports_news(self) -> bool:
        """Fail closed unless this worker explicitly advertises the news protocol."""
        return await self._supports('news-story-v1')

    async def supports_images(self) -> bool:
        """Never submit an illustrated story to an older worker that drops images."""
        return await self._supports('news-story-image-v1')

    async def supports_programs(self) -> bool:
        return await self._supports('news-program-v1')

    async def supports_bulletins(self) -> bool:
        return await self._supports('news-bulletin-v1')

    async def supports_bulletin_video(self) -> bool:
        return await self._supports('news-bulletin-video-v1')

    async def supports_bulletin_video_remarks(self) -> bool:
        return await self._supports('news-bulletin-video-remarks-v1')

    async def supports_bulletin_video_cut(self) -> bool:
        return await self._supports('news-bulletin-video-cut-v1')

    async def supports_image_focus(self) -> bool:
        return await self._supports('news-image-focus-v1')

    async def _status(self, session: aiohttp.ClientSession, request_id: str) -> dict | None:
        try:
            async with session.get(self.base_url + '/news-jobs/' + request_id,
                                   headers=self.headers, allow_redirects=False, timeout=self.timeout) as resp:
                if resp.status == 404:
                    return None
                if resp.status != 200:
                    raise NewsUncertain('Не удалось проверить задачу')
                data = await read_json(resp)
                if not isinstance(data, dict) or data.get('request_id') != request_id or data.get('status') not in ('running','ready','failed','interrupted','audio_too_long'):
                    raise NewsUncertain('Не удалось проверить задачу')
                return data
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise NewsUncertain('Не удалось проверить задачу') from None

    async def status(self, request_id: str) -> dict | None:
        valid_id(request_id)
        async with aiohttp.ClientSession() as session:
            return await self._status(session, request_id)

    async def submit(self, request_id: str, script: dict) -> dict:
        valid_id(request_id)
        if not isinstance(script, dict):
            raise ValueError('Нужен сценарий')
        return await self._submit('/news-jobs', request_id, {'request_id': request_id, 'script': script})

    async def submit_program(self, request_id: str, scripts: list[dict]) -> dict:
        valid_id(request_id)
        from video_prototype.news_jobs import validate_program
        _, scripts = validate_program({'request_id': request_id, 'scripts': scripts})
        return await self._submit('/news-programs', request_id, {'request_id': request_id, 'scripts': scripts})

    async def submit_bulletin(self, request_id: str, bulletin: dict) -> dict:
        from video_prototype.news_jobs import validate_bulletin
        request_id, bulletin = validate_bulletin({'request_id': request_id, 'bulletin': bulletin})
        return await self._submit('/news-bulletins', request_id, {'request_id': request_id, 'bulletin': bulletin})

    async def submit_digest(self, request_id: str, inputs: list[str]) -> dict:
        valid_id(request_id)
        if not isinstance(inputs, list) or len(inputs) < 2 or len(set(inputs)) != len(inputs):
            raise ValueError('Нужны разные готовые сюжеты')
        for item in inputs:
            valid_id(item)
        return await self._submit('/news-digests', request_id, {'request_id': request_id, 'inputs': inputs})

    async def _submit(self, endpoint: str, request_id: str, payload: dict) -> dict:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(self.base_url + endpoint, json=payload, headers=self.headers,
                                        allow_redirects=False, timeout=self.timeout) as resp:
                    if resp.status == 409:
                        body = await read_json(resp)
                        if not isinstance(body, dict) or body.get('error') not in ('busy', 'conflict'):
                            raise NewsUncertain('Нет подтверждения приёма задачи')
                        if body['error'] == 'busy':
                            raise NewsBusy('Воркер занят')
                        raise NewsConflict('Идентификатор уже занят')
                    if resp.status == 400 or resp.status == 413:
                        raise NewsWorkerError('Воркер отклонил сценарий')
                    if resp.status not in (200, 202):
                        raise NewsUncertain('Нет подтверждения приёма задачи')
                    data = await read_json(resp)
                    if not isinstance(data, dict) or data.get('request_id') != request_id or data.get('status') not in ('running','ready','failed','interrupted','audio_too_long'):
                        raise NewsUncertain('Нет подтверждения приёма задачи')
                    return data
            except (aiohttp.ClientError, TimeoutError, ValueError, NewsUncertain):
                known = await self._status(session, request_id)
                if known is None:
                    raise NewsUncertain('Результат отправки неизвестен; не создавайте новую задачу вслепую') from None
                return known

    async def download(self, request_id: str, output: Path) -> tuple[int, str]:
        valid_id(request_id)
        if output.exists() or output.is_symlink() or output.suffix.lower() != '.mp4':
            raise ValueError('Нужен новый MP4-файл')
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.news-download-', suffix='.mp4', dir=output.parent)
        temp = Path(name)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.base_url + '/news-jobs/' + request_id + '/video',
                                       headers=self.headers, allow_redirects=False,
                                       timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=60)) as resp:
                    length = resp.content_length
                    if resp.status != 200 or not length or length < 1 or resp.content_type != 'video/mp4':
                        raise NewsWorkerError('Видео недоступно')
                    digest, count = hashlib.sha256(), 0
                    with os.fdopen(fd, 'wb') as target:
                        fd = -1
                        async for chunk in resp.content.iter_chunked(256 * 1024):
                            count += len(chunk)
                            if count > length:
                                raise NewsWorkerError('Получено слишком много данных')
                            digest.update(chunk); target.write(chunk)
                    if count != length:
                        raise NewsWorkerError('Видео получено не полностью')
            os.link(temp, output)
            return count, digest.hexdigest()
        except (aiohttp.ClientError, TimeoutError, OSError):
            raise NewsWorkerError('Не удалось получить видео') from None
        finally:
            if fd >= 0:
                os.close(fd)
            temp.unlink(missing_ok=True)
