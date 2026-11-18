"""Manual two-speaker GenerateContent probe; sends result to owner once.

Isolated subprocess only: no worker restart, production deployment, queue,
video or database operations. Uses existing key rotation in this process. The
result must be listened to: format validation cannot verify every spoken word.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import subprocess
import urllib.error
import urllib.request
import wave

from video_prototype import gemini_tts
from video_prototype.news_jobs import MAX_BULLETIN_BYTES, validate_bulletin
from news_prototype.send_bulletin_preview import read_settings, DEFAULT_OWNER

MODEL = 'gemini-3.8-flash-tts'
API = f'https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent'
MAX_RESPONSE = 12 * 1024 * 1024
VOICES = {
    'Бендер': ('Bender', 'Algenib', 'По-русски, выразительно, с лёгким сарказмом.'),
    'Киберсенсей': ('Sensei', 'Puck', 'По-русски спокойно, тепло и доброжелательно.'),
}


class ProbeError(Exception):
    pass


def read_worker_env() -> dict[str, str]:
    try:
        pid = subprocess.check_output(['systemctl', 'show', 'bender-video.service', '-p', 'MainPID', '--value'],
                                      stderr=subprocess.DEVNULL, timeout=5).strip().decode()
        if not pid.isdigit() or int(pid) <= 0:
            raise ValueError
        records = (Path('/proc') / pid / 'environ').read_bytes().split(b'\0')
        return {name.decode(): value.decode() for item in records for name, sep, value in [item.partition(b'=')]
                if sep and name in (b'GEMINI_TTS_KEYS', b'BOT_TOKEN', b'TELEGRAM_TOKEN', b'ADMIN_ID')}
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        raise ProbeError('Не удалось прочитать настройки работающего воркера') from None


def worker_keys(env: dict[str, str]) -> list[str]:
    raw = env.get('GEMINI_TTS_KEYS', '').strip()
    if not raw:
        try:
            raw = Path('/data/video-prototype/gemini-tts-keys.txt').read_text(encoding='utf-8').strip()
        except (OSError, UnicodeError):
            pass
    return gemini_tts.parse_keys(raw)


def dialogue(job: Path) -> list[dict]:
    request = job / 'request.json'
    try:
        if job.is_symlink() or request.is_symlink() or not job.is_dir() or request.stat().st_size > MAX_BULLETIN_BYTES:
            raise ValueError
        data = json.loads(request.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or set(data) != {'bulletin'}:
            raise ValueError
        _, bulletin = validate_bulletin({'request_id': job.name, 'bulletin': data['bulletin']})
        turns = bulletin['sections'][0]['turns']
        if len(turns) != 2 or {turn['speaker'] for turn in turns} != set(VOICES):
            raise ValueError
        return turns
    except (OSError, ValueError, UnicodeError, TypeError, KeyError):
        raise ProbeError('Не удалось проверить обе реплики') from None


def body_for(turns: list[dict]) -> dict:
    if not isinstance(turns, list) or len(turns) != 2:
        raise ProbeError('Нужны две реплики')
    parts, speakers = [], set()
    for turn in turns:
        if not isinstance(turn, dict) or set(turn) != {'speaker', 'text'}:
            raise ProbeError('Неверная реплика')
        who, text = turn['speaker'], turn['text']
        if (not isinstance(who, str) or who not in VOICES or not isinstance(text, str)
                or not text.strip() or len(text) > 480):
            raise ProbeError('Неверная реплика')
        alias, _, style = VOICES[who]
        speakers.add(who)
        parts.append({'text': text, 'speechMetadata': {'speaker': alias, 'style': style}})
    if speakers != set(VOICES):
        raise ProbeError('Нужны оба ведущих')
    return {'contents': [{'role': 'user', 'parts': parts}],
            'generationConfig': {'responseModalities': ['AUDIO'],
                'speechConfig': {'multiSpeakerVoiceConfig': {'speakerVoiceConfigs': [
                    {'speaker': alias, 'voiceConfig': {'prebuiltVoiceConfig': {'voiceName': voice}}}
                    for alias, voice, _ in VOICES.values()]}}}}


def request_one(key: str, body: dict, timeout: float):
    request = urllib.request.Request(API, data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'x-goog-api-key': key}, method='POST')
    try:
        with urllib.request.build_opener(gemini_tts.NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise gemini_tts.TTSFailure('слишком большой ответ Gemini')
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise gemini_tts.TTSFailure('некорректный JSON Gemini') from None
        return gemini_tts.validated_audio(payload)
    except urllib.error.HTTPError as exc:
        raise gemini_tts.TTSFailure(f'HTTP {exc.code}') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise gemini_tts.TTSFailure('ошибка связи; результат запроса может быть неизвестен') from None


def send_once(path: Path, folder: Path, env: dict[str, str]) -> str:
    settings = read_settings()
    token = (os.environ.get('BOT_TOKEN') or os.environ.get('TELEGRAM_TOKEN') or settings.get('BOT_TOKEN')
             or settings.get('TELEGRAM_TOKEN') or env.get('BOT_TOKEN') or env.get('TELEGRAM_TOKEN'))
    chat = os.environ.get('ADMIN_ID') or settings.get('ADMIN_ID') or env.get('ADMIN_ID') or DEFAULT_OWNER
    if not token or not chat.isdigit() or int(chat) <= 0:
        return 'Аудио сохранено, но настройки отправки не найдены.'
    # Prefer a Telegram-playable MP3. If unavailable, send the WAV as a file.
    method, field, filename, mime = 'sendDocument', 'document', 'proba.wav', 'audio/wav'
    data = path.read_bytes()
    try:
        result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path), '-c:a', 'libmp3lame',
                                 '-b:a', '128k', '-f', 'mp3', 'pipe:1'], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=30)
        if result.returncode == 0 and result.stdout:
            method, field, filename, mime, data = 'sendAudio', 'audio', 'proba.mp3', 'audio/mpeg', result.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    boundary = '----duo' + secrets.token_hex(12)
    payload = (f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat}\r\n'
               f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
               f'Content-Type: {mime}\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
    request = urllib.request.Request(f'https://api.telegram.org/bot{token}/{method}', data=payload,
        headers={'Content-Type': 'multipart/form-data; boundary=' + boundary}, method='POST')
    try:
        with (folder / 'telegram-send-started').open('xb') as f:
            f.write(b'one send\n'); f.flush(); os.fsync(f.fileno())
    except FileExistsError:
        return 'Отправка уже начиналась; проверь личку перед повтором.'
    print('Отправляю аудиопробу в личку...', flush=True)
    try:
        with urllib.request.build_opener(gemini_tts.NoRedirect()).open(request, timeout=120) as response:
            result = json.loads(response.read(65537))
        return 'Telegram подтвердил отправку.' if result.get('ok') is True else 'Отправка не подтверждена; проверь личку.'
    except Exception:
        return 'Отправка не подтверждена; проверь личку перед повтором.'


def run(job: Path, folder: Path) -> tuple[Path, str]:
    turns = dialogue(job); body = body_for(turns)
    env = read_worker_env(); keys = worker_keys(env)
    if not keys:
        raise ProbeError('Не найдены ключи TTS воркера')
    if folder.resolve().is_relative_to(job.resolve()):
        raise ProbeError('Нельзя сохранять результат внутри задачи')
    folder.mkdir(mode=0o700, parents=False, exist_ok=False)
    with (folder/'request-started').open('xb') as f:
        f.write(b'one probe with key rotation\n'); f.flush(); os.fsync(f.fileno())
    audio = folder/'duo.wav'
    original = gemini_tts.request_one
    try:
        gemini_tts.request_one = lambda key, _text, timeout: request_one(key, body, timeout)
        gemini_tts.synthesize('', audio, keys)
    except gemini_tts.TTSFailure as exc:
        raise ProbeError(str(exc)) from None
    finally:
        gemini_tts.request_one = original
    return audio, send_once(audio, folder, env)


def main():
    job = Path('/data/video-prototype/news-jobs/fe65e362-28b2-51bd-b626-0e33388b165c')
    folder = Path('/data/video-prototype/bulletin-duo-content-contrast-fe65e362')
    try:
        audio, delivery = run(job, folder)
        with wave.open(str(audio), 'rb') as f:
            print(f'Аудио сохранено, {f.getnframes()/f.getframerate():.1f} сек.')
        print(delivery)
        print('Проверь обе реплики и голоса на слух; видео и очередь не запускались.')
        return 0
    except FileExistsError:
        print('Проба уже запускалась. Проверь папку и личку; повтор не выполнен.')
    except ProbeError as exc:
        print('Проба не удалась:', str(exc))
    except OSError:
        print('Ошибка доступа к файлам. Проверь состояние пробы; не повторяй вслепую.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
