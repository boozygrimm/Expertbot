"""Render the combined experiment from saved dialogue and send once to the test chat."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import wave

from news_prototype import podcast_video_trial, hedgehog_motion, sensei_hatch
from news_prototype.run_bender_trial import send_video
from news_prototype.run_podcast_video_trial import find_dialogue
from news_prototype.send_press_trial import load_token

BASE = Path('/data/video-prototype/clips')


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
        podcast_video_trial.prepare_frames()
        hedgehog_motion.prepare()
        sensei_hatch.prepare()
    except (OSError, ValueError, wave.Error, EOFError):
        raise SystemExit('Не удалось проверить исходные реплики или картинки; ничего не отправлено.') from None
    work = Path(tempfile.mkdtemp(prefix='news-combined-', dir=BASE))
    video = work / 'video.mp4'
    print('Использую сохранённый диалог без повторной озвучки.', flush=True)
    print(f'Папка видеопробы: {work}', flush=True)
    env = os.environ.copy()
    for name in ('BOT_TOKEN', 'VIDEO_WORKER_KEY', 'STORAGE_CHANNEL_ID', 'GEMINI_TTS_KEYS',
                 'GEMINI_KEYS', 'GEMINI_API_KEYS', 'GEMINI_API_KEY'):
        env.pop(name, None)
    try:
        subprocess.run([sys.executable, '-m', 'news_prototype.combined_trial',
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
