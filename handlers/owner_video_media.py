"""Bounded local MP4 validation and conversion for owner-requested video notes.

No network, provider, group delivery, or persistent media storage. The caller
owns the private temporary directory and removes it when the operation ends.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path

MAX_BYTES = 20_000_000  # Standard Telegram Bot API download limit.
MAX_SECONDS = 60.0
SIZE = 480
FORMATS = 'mov,mp4,m4a,3gp,3g2,mj2'


class VideoError(ValueError):
    pass


async def _run(command: list[str], *, capture: bool = False, timeout: int = 120) -> bytes:
    # No application credentials or connection strings in the media process.
    env = {'PATH': os.environ.get('PATH', os.defpath), 'LANG': 'C.UTF-8'}
    try:
        proc = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE if capture else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL, env=env)
    except OSError:
        raise VideoError('Не найден FFmpeg или ffprobe') from None
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    if proc.returncode != 0 or (stdout is not None and len(stdout) > 65536):
        raise VideoError('Не удалось обработать видео')
    return stdout or b''


async def probe(path: Path, *, ffprobe: str = 'ffprobe') -> float:
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
        raise VideoError('Недопустимый размер файла')
    raw = await _run([ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe',
        '-format_whitelist', FORMATS, '-show_entries', 'format=duration:stream=codec_type,width,height',
        '-of', 'json', str(path)], capture=True, timeout=30)
    try:
        info = json.loads(raw)
        streams = [x for x in info['streams'] if x.get('codec_type') == 'video']
        duration = float(info['format']['duration'])
        if (len(streams) != 1 or not math.isfinite(duration) or duration <= 0
                or any(type(streams[0].get(k)) is not int or not 1 <= streams[0][k] <= 4096
                       for k in ('width', 'height'))):
            raise ValueError
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise VideoError('Нужен корректный MP4 с одним видеопотоком') from None
    return duration


async def make_note(source: Path, target: Path, *, ffmpeg: str = 'ffmpeg', ffprobe: str = 'ffprobe') -> None:
    duration = await probe(source, ffprobe=ffprobe)
    if duration > MAX_SECONDS:
        raise VideoError('Кружок должен быть не длиннее 60 секунд; обрежь видео сам в Telegram')
    if target.exists() or target.is_symlink():
        raise VideoError('Файл результата уже существует')
    await _run([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-n',
        '-protocol_whitelist', 'file,pipe', '-format_whitelist', FORMATS, '-i', str(source),
        '-map', '0:v:0', '-map', '0:a:0?', '-map_metadata', '-1', '-map_chapters', '-1',
        # Fit the whole rectangular frame inside the *circle*, not just the
        # surrounding square: the scaled diagonal must be <= circle diameter.
        '-vf', "scale=w='max(2,trunc(iw*470/sqrt(iw*iw+ih*ih)/2)*2)':"
               "h='max(2,trunc(ih*470/sqrt(iw*iw+ih*ih)/2)*2)',"
               f'pad={SIZE}:{SIZE}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1',
        '-r', '30', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '24',
        '-maxrate', '1800k', '-bufsize', '3600k', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '96k', '-movflags', '+faststart', str(target)])
    if target.is_symlink() or not target.is_file() or not 0 < target.stat().st_size <= MAX_BYTES:
        raise VideoError('Кружок получился слишком большим; сократи исходное видео')
    # Reject unexpectedly truncated or malformed output; never deliberately trim.
    converted = await probe(target, ffprobe=ffprobe)
    if converted > MAX_SECONDS:
        raise VideoError('Кружок после преобразования длиннее 60 секунд; немного сократи исходное видео в Telegram')
    if abs(converted - duration) > 0.3:
        raise VideoError('Не удалось сохранить полную длительность видео')
