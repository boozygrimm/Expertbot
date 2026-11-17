"""Read-only offline prerequisites check for the experimental bulletin worker.

No credentials, service changes, network requests, queue writes, TTS or rendering.
This only checks local dependencies/assets; it does not prove remote readiness.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess


def check(ffmpeg: str = 'ffmpeg') -> list[str]:
    errors = []
    try:
        from video_prototype.news_jobs import bulletin_assets_ready
        if not bulletin_assets_ready():
            errors.append('Не удалось загрузить код, слои студии или шрифт вестника')
    except Exception:
        errors.append('Не удалось загрузить код вестника или его зависимости')
    binary = shutil.which(ffmpeg)
    if binary is None:
        errors.append('Не найден FFmpeg')
        return errors
    try:
        # No worker credentials are passed to the encoder discovery subprocess.
        env = os.environ.copy()
        for name in ('BOT_TOKEN', 'VIDEO_WORKER_KEY', 'STORAGE_CHANNEL_ID', 'GEMINI_TTS_KEYS',
                     'GEMINI_KEYS', 'GEMINI_API_KEYS', 'GEMINI_API_KEY'):
            env.pop(name, None)
        result = subprocess.run([binary, '-hide_banner', '-encoders'], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=20, check=True, env=env)
        text = result.stdout.decode('utf-8', errors='replace')
        for encoder in ('libx264', 'aac'):
            if not re.search(r'^\s*[A-Z.]{6}\s+' + re.escape(encoder) + r'(?:\s|$)', text, re.MULTILINE):
                errors.append('FFmpeg не поддерживает ' + encoder)
    except (OSError, subprocess.SubprocessError):
        errors.append('Не удалось проверить кодеки FFmpeg')
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ffmpeg', default='ffmpeg', help='Путь к локальному FFmpeg')
    args = parser.parse_args()
    errors = check(args.ffmpeg)
    if errors:
        for message in errors:
            print('ОШИБКА:', message)
        print('Ничего не запущено и не изменено.')
        return 1
    print('Код, слои, шрифт и локальные кодеки вестника доступны.')
    print('Это не проверка настройки сервиса, квоты, сети или качества речи; рендер не запускался.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
