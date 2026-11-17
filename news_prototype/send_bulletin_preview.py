"""One-off InstaBox delivery of the silent bulletin preview to the owner.

Downloads one pinned, checksum-verified public GitHub blob. Uses the existing
local bot credential, never prints it, and never retries an uncertain send.
No TTS, renderer, worker, queue, database or service is touched.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import urllib.error
import urllib.request

BLOB_URL = 'https://api.github.com/repos/boozygrimm/Expertbot/git/blobs/9f9beed0e7868889b118eccc7dbe2d526008a95d'
SHA256 = 'e0d49cae4b5b049b7469d9fe73f97e70b0f73f6564ef9b1461254edf603f16aa'
DEFAULT_OWNER = '980561369'  # Same fallback as config.py.


def read_settings(path=Path('/data/video-prototype/.env')):
    result = {}
    try:
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line.startswith('export '):
                line = line[7:].strip()
            key, sep, value = line.partition('=')
            key = key.strip()
            if sep and key in ('BOT_TOKEN', 'TELEGRAM_TOKEN', 'ADMIN_ID'):
                parts = shlex.split(value, comments=True)
                if parts:
                    result[key] = parts[0]
    except (OSError, ValueError):
        pass
    return result


def download():
    request = urllib.request.Request(BLOB_URL, headers={'User-Agent': 'bulletin-preview',
                                                        'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read(2_000_000))
    if payload.get('encoding') != 'base64':
        raise ValueError('Unexpected blob encoding')
    video = base64.b64decode(payload['content'], validate=False)
    if hashlib.sha256(video).hexdigest() != SHA256:
        raise ValueError('Checksum mismatch')
    return video


def main():
    settings = read_settings()
    token = (os.environ.get('BOT_TOKEN') or os.environ.get('TELEGRAM_TOKEN')
             or settings.get('BOT_TOKEN') or settings.get('TELEGRAM_TOKEN'))
    chat = os.environ.get('ADMIN_ID') or settings.get('ADMIN_ID') or DEFAULT_OWNER
    if not token or not chat.isdigit() or int(chat) <= 0:
        raise SystemExit('Не найдены настройки бота или ID владельца. Ничего не отправлено.')
    try:
        video = download()
    except Exception:
        raise SystemExit('Не удалось скачать и проверить видео. Ничего не отправлено.') from None
    boundary = '----bulletin-' + secrets.token_hex(16)
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="video"; filename="bulletin-preview.mp4"\r\n'
            'Content-Type: video/mp4\r\n\r\n').encode() + video + f'\r\n--{boundary}--\r\n'.encode()
    request = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendVideo', data=body,
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'}, method='POST')
    print('Отправляю готовую пробу в личку владельца...', flush=True)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.load(response)
        if result.get('ok'):
            print('Готово: Telegram подтвердил отправку.')
        else:
            print('Отправка не подтверждена. Проверьте личку перед повтором.')
    except urllib.error.HTTPError as exc:
        if 400 <= exc.code < 500 and exc.code != 408:
            print(f'Telegram отклонил отправку (HTTP {exc.code}).')
        else:
            print('Нет подтверждения отправки. Проверьте личку перед повтором.')
    except Exception:
        print('Нет подтверждения отправки. Проверьте личку перед повтором.')


if __name__ == '__main__':
    main()
