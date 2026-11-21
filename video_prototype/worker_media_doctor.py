"""Read-only check: can this worker actually render video fragments?

Run on the worker machine (InstaBox) as the service user, or as root:

    python3 -m video_prototype.worker_media_doctor

It prints what the service sees (PATH, found binaries, unit paths) and what the
running worker advertises on /health, then the exact commands that would fix the
gap. It changes nothing, prints no keys or environment files and makes no
Telegram or Gemini request.
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
    lines += advice(missing, worker_missing=missing and not advertises)
    print('\n'.join(lines))
    return 0 if not missing else 1


if __name__ == '__main__':
    sys.exit(main())
