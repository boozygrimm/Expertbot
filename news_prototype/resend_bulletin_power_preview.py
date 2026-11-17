"""Send the already rendered power-animation preview once after owner review.

No Gemini, rendering, worker, queue or database operations. A separate exclusive
retry directory prevents blindly repeating an ambiguous Telegram send.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

from news_prototype import bulletin_duo_content_probe, bulletin_full_video_trial, podcast_audio_trial

BASE = bulletin_full_video_trial.BASE
PREVIEW = BASE / 'video-preview-power-1'
EXPECTED_SIZE = 4_061_364


def run(preview: Path, ffmpeg: str) -> str:
    if preview.parent.is_symlink() or preview.is_symlink() or not preview.is_dir():
        raise ValueError('Нет папки готового ролика')
    video, previous = preview / 'video.mp4', preview / 'telegram-send-started'
    if (video.is_symlink() or previous.is_symlink() or not video.is_file() or not previous.is_file()
            or video.stat().st_size != EXPECTED_SIZE):
        raise ValueError('Не найден ожидаемый файл или маркер первой отправки')
    print('Проверяю сохранённое видео, без пересборки...', flush=True)
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i', str(video), '-f', 'null', '-'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180,
                   env=podcast_audio_trial.normalized_env())
    env = bulletin_duo_content_probe.read_worker_env()
    retry = preview / 'telegram-retry-1'
    retry.mkdir(mode=0o700, exist_ok=False)
    return bulletin_full_video_trial.send_video_once(video, retry, env)


def main() -> int:
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        print('Не найден ffmpeg; ничего не отправлено.')
        return 1
    try:
        print(run(PREVIEW, ffmpeg), flush=True)
        return 0
    except FileExistsError:
        print('Повторная отправка уже начиналась; проверь личку и не запускай повторно.')
    except (ValueError, OSError, subprocess.SubprocessError, bulletin_duo_content_probe.ProbeError):
        print('Проверка или отправка остановлена; не запускай повторно, пришли вывод.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
