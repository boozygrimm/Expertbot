"""Bounded owner-supplied bulletin video storage on the worker.

Files are keyed only by their SHA-256, never by an untrusted path. They are
validated before acknowledgement and copied and rechecked before any TTS.
No credentials or Telegram file URLs are saved here.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
import threading

from news_prototype import telegram_public_source

MAX_BYTES = 20_000_000
MAX_STORE_BYTES = 1024 * 1024 * 1024
_lock = threading.Lock()


def _path(root: Path, digest: str) -> Path:
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('Неверный хеш видео')
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Хранилище недоступно')
    directory = root / '.bulletin-manual-video'
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Хранилище недоступно')
    return directory / (digest + '.mp4')


def _read(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or not 16 <= info.st_size <= MAX_BYTES:
            raise ValueError('Неверное видео')
        data = f.read(MAX_BYTES + 1)
    if len(data) != info.st_size or data[4:8] != b'ftyp':
        raise ValueError('Неверное видео')
    return data


def save(root: Path, digest: str, data: bytes, *, ffmpeg: str, ffprobe: str) -> None:
    if not isinstance(data, bytes) or not 16 <= len(data) <= MAX_BYTES or data[4:8] != b'ftyp':
        raise ValueError('Нужен MP4 размером до 20 МБ')
    target = _path(root, digest)
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('Хеш видео не совпадает')
    with _lock:
        if target.exists() or target.is_symlink():
            if hashlib.sha256(_read(target)).hexdigest() != digest:
                raise ValueError('Сохранённый файл изменился')
            return
        used = sum(p.stat().st_size for p in target.parent.glob('*.mp4') if p.is_file() and not p.is_symlink())
        if used + len(data) > MAX_STORE_BYTES:
            raise ValueError('Хранилище фрагментов заполнено')
        fd, name = tempfile.mkstemp(prefix='.upload-', dir=target.parent)
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(data); out.flush(); os.fsync(out.fileno())
            telegram_public_source.inspect(Path(name), ffprobe, ffmpeg)
            os.link(name, target)
        finally:
            Path(name).unlink(missing_ok=True)


def acquire(root: Path, digest: str, folder: Path, *, ffmpeg: str, ffprobe: str) -> Path:
    """Copy exact stored bytes to a private per-render folder, check again."""
    data = _read(_path(root, digest))
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('Фрагмент изменился')
    folder = Path(folder)
    folder.mkdir(mode=0o700, exist_ok=False)
    output = folder / 'source.mp4'
    with output.open('xb') as out:
        out.write(data)
    telegram_public_source.inspect(output, ffprobe, ffmpeg)
    return output
