"""Re-use a saved podcast audition for an isolated video, then send it once."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import wave

from news_prototype import bender_trial, podcast_video_trial
from news_prototype.run_bender_trial import send_video
from news_prototype.send_press_trial import load_token

BASE = Path('/data/video-prototype/clips')


def find_dialogue(base: Path = BASE) -> Path | None:
    candidates = []
    for folder in base.glob('news-dialogue-*'):
        audio = folder / 'dialogue.wav'
        try:
            if audio.is_file() and all((folder / f'{i}-normalized.wav').is_file() for i in range(1, 5)):
                candidates.append((audio.stat().st_mtime_ns, str(folder), folder))
        except OSError:
            continue
    return max(candidates)[2] if candidates else None


def main() -> None:
    token = load_token()
    if not token:
        raise SystemExit('Не найден BOT_TOKEN; ничего не отправлено.')
    source = find_dialogue()
    if source is None:
        raise SystemExit('Не найдены сохранённые реплики предыдущего диалога; ничего не отправлено.')
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise SystemExit('Не найден ffmpeg; ничего не отправлено.')
    try:
        podcast_video_trial.dialogue_timeline(source)
        for name in ('studio_mockup_soft.png', 'bender_soft_closed.png',
                     'bender_blink_reference.png', 'sensei_open_subtle.png'):
            bender_trial.load_image(bender_trial.HERE / name)
    except (OSError, ValueError, wave.Error, EOFError):
        raise SystemExit('Не удалось проверить исходные реплики или картинки; ничего не отправлено.') from None
    work = Path(tempfile.mkdtemp(prefix='news-podcast-video-', dir=BASE))
    video = work / 'video.mp4'
    print('Использую сохранённый диалог без повторной озвучки.', flush=True)
    print(f'Папка видеопробы: {work}', flush=True)
    env = os.environ.copy()
    for name in ('BOT_TOKEN', 'VIDEO_WORKER_KEY', 'STORAGE_CHANNEL_ID', 'GEMINI_TTS_KEYS',
                 'GEMINI_KEYS', 'GEMINI_API_KEYS', 'GEMINI_API_KEY'):
        env.pop(name, None)
    try:
        subprocess.run([sys.executable, '-m', 'news_prototype.podcast_video_trial',
                        '--dialogue-dir', str(source), '--output', str(video), '--ffmpeg', ffmpeg],
                       env=env, check=True)
    except (OSError, subprocess.CalledProcessError):
        raise SystemExit('Не удалось создать видео; ничего не отправлено.') from None
    print('Отправляю видеопробу в тестовый чат один раз...', flush=True)
    try:
        accepted = send_video(token, video)
    except Exception:
        accepted = False
    if accepted:
        print('Готово: видео отправлено в тестовый чат.', flush=True)
    else:
        print('Нет подтверждения отправки. Проверьте чат перед повтором.', flush=True)
    print(f'Готовый файл сохранён: {video}', flush=True)


if __name__ == '__main__':
    main()
