"""One-off delivery of the pre-rendered trial to the previously used test chat.

Run on InstaBox. Reads BOT_TOKEN from the environment or the existing local
.env; never prints secrets and never retries an uncertain Telegram send.
"""
import hashlib
import json
import os
from pathlib import Path
import shlex
import urllib.request

CHAT = '-1004395648779'
SHA256 = '1dc9bc1d698cd4eae23f2bc61dc0fcc149605664527d30c98dfdf07c4bf55036'
URL = ('https://api.github.com/repos/boozygrimm/Expertbot/contents/'
       'news_prototype/press_trial.mp4'
       '?ref=03fcd678b1ed737f73c327b44a7fc4221715c397')


def load_token():
    token = os.environ.get('BOT_TOKEN', '').strip()
    if token:
        return token
    try:
        for line in Path('/data/video-prototype/.env').read_text().splitlines():
            line = line.strip()
            if line.startswith('export '):
                line = line[7:].strip()
            key, sep, value = line.partition('=')
            if sep and key.strip() == 'BOT_TOKEN':
                parts = shlex.split(value, comments=True)
                return parts[0] if parts else ''
    except (OSError, ValueError):
        pass
    return ''


def main():
    token = load_token()
    if not token:
        raise SystemExit('Не найден BOT_TOKEN на InstaBox; ничего не отправлено.')
    try:
        req = urllib.request.Request(URL, headers={
            'Accept': 'application/vnd.github.raw+json', 'User-Agent': 'news-trial'})
        with urllib.request.urlopen(req, timeout=60) as response:
            video = response.read(2_000_000)
    except Exception:
        raise SystemExit('Не удалось скачать пробу; ничего не отправлено.') from None
    if hashlib.sha256(video).hexdigest() != SHA256:
        raise SystemExit('Файл не прошёл проверку; ничего не отправлено.')
    path = Path('/data/video-prototype/clips/news-press-trial-1dc9bc1d.mp4')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(video)
    except OSError:
        raise SystemExit('Не удалось сохранить пробу на InstaBox; ничего не отправлено.') from None
    print('Проба скачана и проверена. Отправляю в тестовый чат...', flush=True)
    boundary = '----newsPress1dc9bc1d'
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{CHAT}\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="video"; '
        'filename="news-press-trial.mp4"\r\nContent-Type: video/mp4\r\n\r\n'
    ).encode() + video + f'\r\n--{boundary}--\r\n'.encode()
    request = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendVideo', data=body,
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.load(response)
        if result.get('ok'):
            print('Готово: видео отправлено в тестовый чат.')
        else:
            print('Telegram не подтвердил отправку. Проверь тестовый чат.')
    except Exception:
        print('Нет подтверждения отправки. Проверь тестовый чат перед повтором.')


if __name__ == '__main__':
    main()
