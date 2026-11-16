"""Run one isolated Algenib presenter trial on InstaBox and send it once.

Uses existing local credentials; never changes the studio service or its files.
It records the rendered video before attempting Telegram; an uncertain send is
never retried. Never print exceptions that may contain credential-bearing URLs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from video_prototype import gemini_tts
from news_prototype.send_press_trial import load_token

TEXT = ('Привет, земляне! Вы смотрите пробу наших новостей. '
        'Рядом со мной киберсенсей, а на стене боевой ёжик. '
        'Мы пока настраиваем студию, так что оставайтесь на связи!')
CHAT = '-1004395648779'


def load_keys() -> list[str]:
    value = os.environ.get('GEMINI_TTS_KEYS', '').strip()
    if not value:
        try:
            value = Path('/data/video-prototype/gemini-tts-keys.txt').read_text(encoding='utf-8').strip()
        except OSError:
            return []
    return gemini_tts.parse_keys(value)


def send_video(token: str, video: Path) -> bool:
    media = video.read_bytes()
    if len(media) > 50 * 1024 * 1024:
        raise ValueError('Пробный файл больше 50 МБ')
    boundary = '----news-bender-' + secrets.token_hex(12)
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{CHAT}\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="video"; '
        'filename="bender-news-trial.mp4"\r\nContent-Type: video/mp4\r\n\r\n'
    ).encode() + media + f'\r\n--{boundary}--\r\n'.encode()
    request = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendVideo', data=body,
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'}, method='POST')
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    return bool(result.get('ok'))


def main() -> None:
    token, keys = load_token(), load_keys()
    if not token:
        raise SystemExit('Не найден BOT_TOKEN; ничего не отправлено.')
    if not keys:
        raise SystemExit('Не найден список ключей Gemini; ничего не отправлено.')
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise SystemExit('Не найден ffmpeg; ничего не отправлено.')
    try:
        from news_prototype.bender_trial import load_image
    except ImportError:
        raise SystemExit('В этом Python не установлен Pillow; ничего не отправлено.') from None
    try:
        for name in ('studio_mockup_corrected.png', 'bender_closed_reference.png', 'bender_blink_reference.png'):
            load_image(HERE / name)
    except (OSError, ValueError):
        raise SystemExit('Не удалось проверить исходные изображения; ничего не отправлено.') from None
    base = Path('/data/video-prototype/clips')
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='news-bender-', dir=base))
    audio, video = work / 'speech.wav', work / 'video.mp4'
    print(f'Папка пробы: {work}', flush=True)
    print('Создаю озвучку Algenib; это использует существующую квоту Gemini.', flush=True)
    try:
        gemini_tts.synthesize(TEXT, audio, keys)
    except gemini_tts.TTSFailure:
        raise SystemExit('Не удалось получить Algenib; другая озвучка не использована, ничего не отправлено.') from None
    # Keep Gemini and Telegram credentials out of the render subprocess.
    env = os.environ.copy()
    for key in ('BOT_TOKEN', 'VIDEO_WORKER_KEY', 'STORAGE_CHANNEL_ID', 'GEMINI_TTS_KEYS',
                'GEMINI_KEYS', 'GEMINI_API_KEYS', 'GEMINI_API_KEY'):
        env.pop(key, None)
    print('Создаю пробу говорящего ведущего...', flush=True)
    try:
        subprocess.run([sys.executable, str(HERE / 'bender_trial.py'), '--audio', str(audio),
                        '--output', str(video), '--ffmpeg', ffmpeg], env=env, check=True)
    except (OSError, subprocess.CalledProcessError):
        raise SystemExit('Не удалось создать видео; ничего не отправлено.') from None
    print('Отправляю готовый файл в тестовый чат один раз...', flush=True)
    try:
        if send_video(token, video):
            print('Готово: видео отправлено в тестовый чат.')
        else:
            print('Telegram не подтвердил отправку. Проверьте чат перед повтором.')
    except Exception:
        print('Нет подтверждения отправки. Проверьте чат перед повтором.')
    print(f'Готовый файл сохранён: {video}', flush=True)


if __name__ == '__main__':
    main()
