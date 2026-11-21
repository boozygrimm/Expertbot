"""Read-only check: can this worker actually render video fragments?

Run on the worker machine (InstaBox) as the service user, or as root:

    python3 -m video_prototype.worker_media_doctor

It prints what the service sees (PATH, found binaries, unit paths) and what the
running worker advertises on /health, then the exact commands that would fix the
gap. It changes nothing, prints no keys or environment files and makes no
Telegram or Gemini request.

`--video PATH` additionally samples an already rendered file and lists stretches
where the picture does not change at all. That is how a frozen fragment is told
apart from a slow one: the report gives the exact seconds, so the render cannot
be blamed (or excused) by guesswork.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

from news_prototype import media_tools

UNIT = 'bender-video.service'
# Sampling rate for the freeze check, and how long a still picture must last to
# be worth reporting as a frozen stretch rather than an ordinary held frame.
SAMPLE_FPS = 2.0
STATIC_SECONDS = 0.75
# Mean per-channel difference below which two sampled frames count as identical.
STATIC_TOLERANCE = 0.6


def systemctl(*args: str) -> str:
    try:
        return subprocess.run(['systemctl', *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              stdin=subprocess.DEVNULL, check=True, timeout=20).stdout.decode().strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def unit_lines() -> list[str]:
    if not systemctl('show', UNIT, '-p', 'LoadState', '--value'):
        return [f'Служба {UNIT} недоступна: systemctl не ответил или её нет.']
    lines = [f'Служба {UNIT}']
    for prop, label in (('LoadState', 'состояние'), ('ActiveState', 'активна'),
                        ('SubState', 'подсостояние'), ('User', 'пользователь')):
        value = systemctl('show', UNIT, '-p', prop, '--value')
        if value:
            lines.append(f'· {label}: {value}')
    exec_start = systemctl('show', UNIT, '-p', 'ExecStart', '--value')
    if exec_start:
        # The value contains argv with a leading path marker; it never holds keys.
        path = exec_start.split('path=', 1)[-1].split(' ;', 1)[0]
        lines.append(f'· запуск: {path}')
    for prop, label in (('EnvironmentFiles', 'файлы окружения'),
                        ('DropInPaths', 'drop-in')):
        value = systemctl('show', UNIT, '-p', prop, '--value')
        if value:
            lines.append(f'· {label}: {value}')
    return lines


def binary_lines() -> list[str]:
    lines = ['Медиа-инструменты']
    for name in media_tools.TOOLS:
        found = media_tools.find(name)
        lines.append(f'· {name}: {found or "НЕ НАЙДЕН"}')
        if not found:
            hits = [str(Path(folder) / name) for folder in media_tools.FALLBACK_DIRS
                    if (Path(folder) / name).exists()]
            lines.append('  проверены: ' + ', '.join(media_tools.FALLBACK_DIRS))
            if hits:
                lines.append('  похожие файлы без права запуска: ' + ', '.join(hits))
    return lines


def video_lines(path: Path) -> list[str]:
    """Report still stretches in a rendered file, without changing it.

    Only ffmpeg is used and it writes into a pipe: the file is opened read-only,
    nothing is re-encoded to disk, and the numbers below describe what the
    render actually produced.
    """
    from PIL import Image, ImageChops, ImageStat

    ffmpeg = media_tools.find('ffmpeg')
    if not ffmpeg:
        return ['Ролик не проверен: не найден ffmpeg.']
    if path.is_symlink() or not path.is_file():
        return ['Ролик не проверен: файл не найден.']
    command = [ffmpeg, '-nostdin', '-v', 'error', '-i', str(path), '-an', '-sn', '-dn',
               '-vf', f'fps={SAMPLE_FPS},scale=320:180', '-pix_fmt', 'rgb24',
               '-f', 'rawvideo', 'pipe:1']
    try:
        data = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              stdin=subprocess.DEVNULL, check=True, timeout=900).stdout
    except (OSError, subprocess.SubprocessError):
        return ['Ролик не проверен: ffmpeg не смог его прочитать.']
    size = 320 * 180 * 3
    count = len(data) // size
    if not count:
        return ['Ролик не проверен: в нём нет кадров.']
    runs, previous, run_start = [], None, None
    for index in range(count):
        frame = Image.frombytes('RGB', (320, 180), data[index * size:(index + 1) * size])
        unchanged = (previous is not None and max(
            ImageStat.Stat(ImageChops.difference(frame, previous)).mean) < STATIC_TOLERANCE)
        if unchanged and run_start is None:
            run_start = index - 1
        elif not unchanged and run_start is not None:
            if (index - run_start) / SAMPLE_FPS >= STATIC_SECONDS:
                runs.append((run_start / SAMPLE_FPS, index / SAMPLE_FPS))
            run_start = None
        previous = frame
    if run_start is not None and (count - run_start) / SAMPLE_FPS >= STATIC_SECONDS:
        runs.append((run_start / SAMPLE_FPS, count / SAMPLE_FPS))
    lines = [f'Ролик: {path.name}, {count / SAMPLE_FPS:.1f} с, опрос {SAMPLE_FPS:g} кадр/с']
    if not runs:
        lines.append(f'· неподвижных участков дольше {STATIC_SECONDS:g} с нет: '
                     'картинка в ролике меняется.')
        return lines
    lines.append('· неподвижная картинка (кадры совпадают): '
                 + ', '.join(f'{start:.1f}–{end:.1f} с' for start, end in runs))
    lines.append('  участок сразу после крупного плана вставки означает, что после '
                 'вставки не вернулась анимация студии.')
    return lines


def health_lines(url: str) -> list[str]:
    if not url:
        return ['/health не проверен: не задан адрес воркера (--url).']
    try:
        with urllib.request.urlopen(url.rstrip('/') + '/health', timeout=15) as response:
            data = json.loads(response.read(4096))
    except Exception:
        return ['/health не ответил: проверь адрес, ключ и что служба запущена.']
    capabilities = data.get('capabilities') if isinstance(data, dict) else None
    if not isinstance(capabilities, list):
        return ['/health вернул неожиданный ответ.']
    lines = ['Возможности работающего воркера']
    for name in ('news-bulletin-v1', 'news-bulletin-video-v1', 'news-bulletin-manual-video-v1',
                 'news-story-manual-video-v1'):
        lines.append(f'· {name}: {"да" if name in capabilities else "нет"}')
    return lines


def advice(lines_missing: bool, worker_missing: bool) -> list[str]:
    if not lines_missing:
        return ['ffmpeg и ffprobe найдены. Если воркер всё ещё не объявляет видеофрагмент — '
                'он запущен со старым кодом: нужен обновлённый video_prototype/worker.py.']
    tips = ['Чего делать (по шагам, ничего не запускать автоматически):',
            '1. Установить оба бинарника: sudo apt-get update && sudo apt-get install -y ffmpeg',
            '2. Проверить, что статическая сборка не лежит «вручную» в /opt: '
            '  sudo find / -maxdepth 4 -name ffprobe -type f 2>/dev/null',
            '3. Перезапустить службу: sudo systemctl restart ' + UNIT,
            '4. Посмотреть /health и убедиться, что появился news-story-manual-video-v1.']
    if worker_missing:
        tips.append('Если бинарники есть, а возможности нет — обновить код воркера: '
                    'health объявляет видеофрагмент только из нового worker.py.')
    return tips


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default=os.getenv('BENDER_WORKER_URL', ''),
                        help='адрес воркера для проверки /health; ничего не отправляется, кроме GET')
    parser.add_argument('--video', type=Path, default=None,
                        help='готовый MP4: показать участки, где картинка не меняется')
    args = parser.parse_args()
    lines = ['Проверка медиа-инструментов воркера (только чтение)', '']
    lines += unit_lines()
    lines.append('')
    lines.append(f'PATH процесса: {os.environ.get("PATH", "")}')
    lines += binary_lines()
    lines.append('')
    # The health call is made once; its verdict decides the advice.
    health = health_lines(args.url)
    lines += health
    lines.append('')
    missing = not all(media_tools.find(name) for name in media_tools.TOOLS)
    advertises = any('news-story-manual-video-v1: да' in line for line in health)
    if args.video is not None:
        lines.append('')
        lines += video_lines(args.video)
    lines += advice(missing, worker_missing=missing and not advertises)
    print('\n'.join(lines))
    return 0 if not missing else 1


if __name__ == '__main__':
    sys.exit(main())
