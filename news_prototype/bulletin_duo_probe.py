"""Isolated one-request, two-voice audio probe; not connected to the bot/worker.

Explicit use only. Reads the first two turns of a saved bulletin request and
sends only that text to Gemini; no image, database, queue, video, or Telegram
access. One exclusive directory records that a request has been attempted;
there is no key rotation or automatic retry, even after ambiguous outcomes.
"""
from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import urllib.error
import urllib.request
import wave

from video_prototype.news_jobs import MAX_BULLETIN_BYTES, validate_bulletin

MODEL = 'gemini-3.8-flash-tts'
API = 'https://generativelanguage.googleapis.com/v1beta/interactions'
MAX_RESPONSE = 12 * 1024 * 1024
MAX_AUDIO = 8 * 1024 * 1024
VOICES = {
    'Бендер': ('Bender', 'Algenib', 'По-русски, выразительно, с лёгким сарказмом.'),
    'Киберсенсей': ('Sensei', 'Iapetus', 'По-русски спокойно, тепло и доброжелательно.'),
}


class ProbeError(Exception):
    """Safe message without URLs, request text, keys or provider response."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def dialogue_from_job(job: Path) -> list[dict]:
    job = Path(job)
    path = job / 'request.json'
    if job.is_symlink() or not job.is_dir() or path.is_symlink():
        raise ProbeError('Папка задачи недоступна')
    try:
        if path.stat().st_size > MAX_BULLETIN_BYTES:
            raise ValueError
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or set(data) != {'bulletin'}:
            raise ValueError
        _, bulletin = validate_bulletin({'request_id': job.name, 'bulletin': data['bulletin']})
        turns = bulletin['sections'][0]['turns']
        if len(turns) != 2 or {turn['speaker'] for turn in turns} != set(VOICES):
            raise ValueError
        return turns
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        raise ProbeError('Не удалось проверить первые две реплики задачи') from None


def request_body(turns: list[dict]) -> dict:
    if not isinstance(turns, list) or len(turns) != 2:
        raise ProbeError('Для пробы нужны две реплики')
    content = []
    speakers = set()
    for turn in turns:
        if not isinstance(turn, dict) or set(turn) != {'speaker', 'text'}:
            raise ProbeError('Неверные реплики')
        who, text = turn['speaker'], turn['text']
        if (not isinstance(who, str) or who not in VOICES or not isinstance(text, str) or
                not text.strip() or len(text) > 480 or
                any(ord(c) < 32 and c not in '\n\t' for c in text)):
            raise ProbeError('Неверные реплики')
        alias, _, style = VOICES[who]
        speakers.add(who)
        content.append({'type': 'text', 'text': text, 'annotations': [
            {'type': 'speech_metadata', 'speaker': alias, 'style': style}]})
    if speakers != set(VOICES):
        raise ProbeError('Для пробы нужны оба ведущих')
    return {'model': MODEL, 'store': False,
            'input': [{'type': 'user_input', 'content': content}],
            'response_format': {'type': 'audio', 'mime_type': 'audio/wav'},
            'generation_config': {'speech_config': {'speakers': [
                {'speaker': alias, 'voice': voice} for alias, voice, _ in VOICES.values()]}}}


def decode_audio(data: object) -> tuple[bytes, float]:
    try:
        if not isinstance(data, dict) or data.get('status') != 'completed':
            raise ValueError
        steps = data['steps']
        if not isinstance(steps, list):
            raise ValueError
        audios = []
        for step in steps:
            if not isinstance(step, dict) or step.get('type') != 'model_output':
                continue
            parts = step.get('content', [])
            if not isinstance(parts, list):
                raise ValueError
            audios.extend(part for part in parts if isinstance(part, dict) and part.get('type') == 'audio')
        if len(audios) != 1 or audios[0].get('mime_type') != 'audio/wav':
            raise ValueError
        wav = base64.b64decode(audios[0]['data'], validate=True)
        if len(wav) > MAX_AUDIO:
            raise ValueError
        with wave.open(io.BytesIO(wav), 'rb') as f:
            rate, frames = f.getframerate(), f.getnframes()
            if ((f.getnchannels(), f.getsampwidth(), rate, f.getcomptype()) != (1, 2, 24000, 'NONE')
                    or not frames or frames / rate >= 120):
                raise ValueError
            samples = f.readframes(frames)
            if len(samples) != frames * 2 or not any(samples):
                raise ValueError
            return wav, frames / rate
    except (KeyError, TypeError, ValueError, wave.Error, EOFError):
        raise ProbeError('Ответ не содержит корректного завершённого WAV') from None


def service_keys() -> list[str]:
    """Use existing local settings without displaying them or starting a service."""
    raw = os.environ.get('GEMINI_TTS_KEYS', '').strip()
    if not raw:
        try:
            raw = Path('/data/video-prototype/gemini-tts-keys.txt').read_text(encoding='utf-8').strip()
        except (OSError, UnicodeError):
            pass
    if not raw:
        try:
            result = subprocess.run(['systemctl', 'show', 'bender-video.service', '-p', 'MainPID', '--value'],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    check=True, timeout=5)
            pid = int(result.stdout.decode().strip())
            if pid > 0:
                env = (Path('/proc') / str(pid) / 'environ').read_bytes().split(b'\0')
                raw = next((x.partition(b'=')[2].decode('utf-8').strip()
                            for x in env if x.startswith(b'GEMINI_TTS_KEYS=')), '')
        except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
            pass
    return list(dict.fromkeys(x.strip() for x in raw.split(',') if x.strip()))


def post_once(key: str, body: dict) -> tuple[bytes, float]:
    request = urllib.request.Request(API, data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'x-goog-api-key': key,
                 'Api-Revision': '2026-05-20'}, method='POST')
    try:
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(request, timeout=90) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ProbeError('Слишком большой ответ Gemini')
        try:
            data = json.loads(raw)
        except (UnicodeError, ValueError, RecursionError):
            raise ProbeError('Некорректный JSON ответа Gemini') from None
        return decode_audio(data)
    except urllib.error.HTTPError as exc:
        raise ProbeError(f'Gemini вернул HTTP {exc.code}') from None
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            category = 'таймаут'
        elif isinstance(reason, socket.gaierror):
            category = 'DNS'
        elif isinstance(reason, ssl.SSLError):
            category = 'TLS'
        else:
            category = 'сетевая ошибка'
        raise ProbeError('Gemini: ' + category + '; результат запроса может быть неизвестен') from None
    except (TimeoutError, OSError):
        raise ProbeError('Ошибка связи с Gemini; результат запроса может быть неизвестен') from None


def run(job: Path, output: Path) -> tuple[Path, float]:
    turns = dialogue_from_job(job)
    body = request_body(turns)
    keys = service_keys()
    if not keys:
        raise ProbeError('Не найдены существующие ключи TTS')
    output = Path(output)
    if output.resolve().is_relative_to(Path(job).resolve()):
        raise ProbeError('Результат нельзя сохранять внутри задачи')
    # One directory per probe: even an interrupted request cannot be repeated
    # through this entry point without a deliberate manual decision.
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    with (output / 'request-started').open('xb') as marker:
        marker.write(b'one request, no automatic retry\n')
        marker.flush(); os.fsync(marker.fileno())
    audio, duration = post_once(keys[0], body)
    path = output / 'duo.wav'
    with path.open('xb') as file:
        file.write(audio); file.flush(); os.fsync(file.fileno())
    return path, duration


def main() -> int:
    # Exactly the reviewed bulletin and dedicated private probe directory;
    # no arbitrary destination, bot or queue operations are exposed here.
    job = Path('/data/video-prototype/news-jobs/fe65e362-28b2-51bd-b626-0e33388b165c')
    out = Path('/data/video-prototype/bulletin-duo-probe-fe65e362')
    try:
        path, duration = run(job, out)
    except FileExistsError:
        print('Папка или файл пробы уже существуют; состояние не подтверждено. Не повторяйте вслепую.')
        return 1
    except ProbeError as exc:
        print('Проба не подтверждена:', str(exc))
        print('Автоматического повтора нет; не запускайте её повторно вслепую.')
        return 1
    except OSError:
        print('Ошибка доступа к файлам; результат запроса может быть неизвестен. Не повторяйте вслепую.')
        return 1
    print('Готова аудиопроба:', path)
    print(f'Длительность: {duration:.1f} сек. Это ещё не подтверждает полное произнесение текста.')
    print('Рендер, очередь и отправка в Telegram не запускались.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
