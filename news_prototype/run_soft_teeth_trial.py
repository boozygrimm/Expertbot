"""Re-render the latest successful presenter WAV with the softer mouth.

Run manually on InstaBox; never resynthesize speech or alter the working service.
The resulting file is saved before a single Telegram send, with no retries.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from news_prototype import bender_trial
from news_prototype.run_bender_trial import send_video
from news_prototype.send_press_trial import load_token

BASE = Path('/data/video-prototype/clips')


def find_previous_audio(base: Path = BASE) -> Path | None:
    candidates = []
    for path in base.glob('news-bender-*/speech.wav'):
        try:
            if path.is_file():
                candidates.append((path.stat().st_mtime_ns, str(path), path))
        except OSError:
            continue
    return max(candidates)[2] if candidates else None


def main() -> None:
    token = load_token()
    if not token:
        raise SystemExit('Не найден BOT_TOKEN; ничего не отправлено.')
    source = find_previous_audio()
    if source is None:
        raise SystemExit('Не найдена озвучка предыдущей пробы; ничего не отправлено.')
    try:
        bender_trial.read_pcm(source)
        for name in ('studio_mockup_soft.png', 'bender_soft_closed.png', 'bender_blink_reference.png'):
            bender_trial.load_image(bender_trial.HERE / name)
    except (OSError, ValueError):
        raise SystemExit('Не удалось проверить озвучку или картинки; ничего не отправлено.') from None
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise SystemExit('Не найден ffmpeg; ничего не отправлено.')
    work = Path(tempfile.mkdtemp(prefix='news-soft-teeth-', dir=BASE))
    audio, video = work / 'speech.wav', work / 'video.mp4'
    try:
        shutil.copyfile(source, audio)
    except OSError:
        raise SystemExit('Не удалось сохранить копию озвучки; ничего не отправлено.') from None
    print('Использую озвучку предыдущей пробы без повторного синтеза.', flush=True)
    print(f'Папка новой пробы: {work}', flush=True)
    env = os.environ.copy()
    for name in ('BOT_TOKEN', 'VIDEO_WORKER_KEY', 'STORAGE_CHANNEL_ID', 'GEMINI_TTS_KEYS',
                 'GEMINI_KEYS', 'GEMINI_API_KEYS', 'GEMINI_API_KEY'):
        env.pop(name, None)
    try:
        subprocess.run([sys.executable, str(bender_trial.HERE / 'bender_trial.py'), '--audio', str(audio),
                        '--output', str(video), '--ffmpeg', ffmpeg, '--soft-teeth'], env=env, check=True)
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
