#!/usr/bin/env python3
"""Fail the build when a committed RAID page is older than its sources.

`lunapark-raid*.html` is a generated artifact: both build scripts hash their
inputs and stamp the result into the page as `raid-build`. If the page in git
is stale, GitHub Pages keeps serving the old game while the sources claim the
fix is in — the failure is silent and shows up only on a phone.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def expected_build_id(script: str, names: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for name in names:
        source = HERE / name
        digest.update(name.encode('utf-8') + b'\0' + source.read_bytes() + b'\0')
    return digest.hexdigest()[:12]


def classic_sources() -> tuple[str, ...]:
    names = ['build_raid.py', 'raid.html', 'raid.css', 'raid.js', 'skull_sprites.json']
    return tuple(name for name in names if (HERE / name).exists())


def magitech_sources() -> tuple[str, ...]:
    # Импортируем сам сборщик: список входов (включая ассеты) живёт там, и
    # копия здесь разошлась бы с ним при первом же добавлении картинки.
    sys.path.insert(0, str(HERE))
    import build_raid_magitech
    return tuple(build_raid_magitech.SOURCES)


def stamped(target: Path) -> str:
    head = target.read_text(encoding='utf-8', errors='replace')[:2048]
    marker = '<meta name="raid-build" content="'
    start = head.find(marker)
    if start < 0:
        return ''
    start += len(marker)
    return head[start:start + 12]


def main() -> int:
    targets = [
        (ROOT / 'lunapark-raid.html', 'build_raid.py', classic_sources()),
        (ROOT / 'lunapark-raid-magitech.html', 'build_raid_magitech.py', magitech_sources()),
    ]
    stale = []
    for target, script, sources in targets:
        if not target.exists():
            stale.append(f'{target.name}: файла нет — запусти `npm run build --prefix miniapp_preview`')
            continue
        want, have = expected_build_id(script, sources), stamped(target)
        if have != want:
            stale.append(f'{target.name}: сборка {have or "?"}, по исходникам {want} — '
                         f'запусти `npm run build --prefix miniapp_preview`')
    for line in stale:
        print(line, file=sys.stderr)
    if stale:
        print('Страница RAID устарела: Pages отдавал бы старую игру.', file=sys.stderr)
        return 1
    print('Страницы RAID соответствуют исходникам.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
