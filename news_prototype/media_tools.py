"""Locate ffmpeg/ffprobe in a worker that runs under systemd.

A service started by systemd gets a minimal `PATH` (`/usr/local/bin:/usr/bin:/bin`
at best, sometimes less), and a manually installed static ffmpeg often lives in
`/opt/...` or `/usr/local/ffmpeg/bin`. Advertising video support only when the
inherited `PATH` happens to contain the binary made a working install look
broken. The extra directories are a fixed list: nothing here comes from a
request, a job file or an environment variable, so no user-controlled path is
ever executed.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

FALLBACK_DIRS = ('/usr/local/bin', '/usr/bin', '/bin', '/usr/local/ffmpeg/bin',
                 '/opt/ffmpeg/bin', '/opt/homebrew/bin', '/snap/bin')

TOOLS = ('ffmpeg', 'ffprobe')


def find(name: str) -> str | None:
    """Absolute path to a trusted media binary, or None."""
    if name not in TOOLS:
        raise ValueError('Неизвестный инструмент')
    found = shutil.which(name)
    if found:
        return found
    for folder in FALLBACK_DIRS:
        candidate = Path(folder) / name
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        except OSError:
            continue
    return None


def child_env(base: dict | None = None) -> dict:
    """Environment for a child render process, with the media dirs in PATH.

    The child re-resolves ffmpeg/ffprobe itself (pipelines use `shutil.which`),
    so the directories that actually contain the binaries are prepended instead
    of hard-coding a path that may not exist on another install.
    """
    env = dict(os.environ if base is None else base)
    extra = []
    for name in TOOLS:
        found = find(name)
        if found:
            folder = str(Path(found).parent)
            if folder not in extra:
                extra.append(folder)
    if extra:
        current = env.get('PATH', '')
        env['PATH'] = os.pathsep.join(extra + ([current] if current else []))
    return env


def ffmpeg() -> str:
    """A renderable ffmpeg path; the historical bare name is the last resort."""
    return find('ffmpeg') or 'ffmpeg'
