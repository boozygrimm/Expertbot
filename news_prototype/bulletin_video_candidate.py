"""Prepare a quarantined exact-post video candidate for one reviewed draft.

This experimental building block is not connected to the panel, queue or worker.
It does not mark content or rights as approved and must not be used to authorize
rendering. The caller must already authenticate the owner and explicitly request
acquisition. Results are local-only; paths must be under a caller-controlled root.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import stat

from news_prototype import bulletin_limits, owner_bulletin, telegram_public_source as source


@dataclass(frozen=True)
class Candidate:
    owner_id: int
    bulletin_id: int
    revision: str
    section_id: int
    post_url: str
    caption_sha256: str
    video_sha256: str
    size: int
    duration: float
    width: int
    height: int
    has_audio: bool
    # Local implementation detail: never include it in a worker payload.
    path: Path = field(repr=False, compare=False)


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


async def _current_story(db, owner_id: int, bulletin_id: int, revision: str, section_id: int) -> dict:
    if (not bulletin_limits.number(owner_id) or not bulletin_limits.number(bulletin_id)
            or not bulletin_limits.number(section_id) or not isinstance(revision, str)
            or not re.fullmatch(r'[0-9a-f]{64}', revision)):
        raise ValueError('Неверные данные вестника')
    # Validate the SAME snapshot, including choices/source_revision, rather
    # than validating one database read and extracting from another. This is
    # still a point-in-time check, not an atomic authorization to render.
    item = await owner_bulletin.get(db, owner_id, bulletin_id)
    if (not item or item.get('stage') != 'ready_for_review' or item.get('revision') != revision
            or item.get('mode') != 'direct'
            or owner_bulletin.revision(item['stories'], item['sections']) != revision):
        raise ValueError('Версия вестника изменилась')
    if not await owner_bulletin._sources_current(db, owner_id, item):
        raise ValueError('Один из источников изменился')
    owner_bulletin._payload_from_sections(item['sections'])
    matches = [story for story in item['stories'] if story.get('draft_id') == section_id]
    if (len(matches) != 1 or not any(section.get('id') == section_id for section in item['sections'])
            or matches[0].get('telegram_video_status') != 'available'):
        raise ValueError('Для новости нет однозначного видео')
    story = matches[0]
    if not isinstance(story.get('summary'), str) or not story['summary']:
        raise ValueError('Нет текста источника')
    try:
        canonical, _ = source.canonical_post(story['url'])
    except (source.SourceError, KeyError, TypeError):
        raise ValueError('Неверная ссылка на пост') from None
    return {'url': canonical, 'summary': story['summary']}


def _check_file(candidate: Candidate, *, read_bytes: bool = False) -> bytes | None:
    """Rehash a bounded regular file; optionally return exactly the checked bytes.

    The caller controls the parent directory. This is a point-in-time check,
    not a lock or a guarantee against later modification before use.
    """
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    try:
        if candidate.path.is_symlink():
            raise ValueError
        fd = os.open(candidate.path, flags)
        with os.fdopen(fd, 'rb') as file:
            before = os.fstat(file.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= source.MAX_VIDEO:
                raise ValueError
            digest = hashlib.sha256()
            size = 0
            chunks = [] if read_bytes else None
            while True:
                block = file.read(65536)
                if not block:
                    break
                size += len(block)
                if size > source.MAX_VIDEO:
                    raise ValueError
                digest.update(block)
                if chunks is not None:
                    chunks.append(block)
            after = os.fstat(file.fileno())
            if (size != candidate.size or digest.hexdigest() != candidate.video_sha256
                    or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise ValueError
            return b''.join(chunks) if chunks is not None else None
    except (OSError, ValueError):
        raise ValueError('Файл видео отсутствует или изменился') from None


async def acquire(db, owner_id: int, bulletin_id: int, revision: str, section_id: int, folder: Path,
                  *, ffmpeg: str | None = None, ffprobe: str | None = None) -> Candidate:
    """Explicitly download one candidate; no TTS, render, delivery or DB writes.

    The folder must not exist and its parent must be controlled by the caller.
    A stale or cancelled request may leave a quarantined folder; it must never be
    treated as approved. Signed CDN URLs are not returned in the candidate.
    """
    story = await _current_story(db, owner_id, bulletin_id, revision, section_id)
    post = await asyncio.to_thread(source.fetch_post, story['url'])
    if (post.url != story['url'] or not post.caption_complete or post.caption != story['summary']
            or post.video_status != 'available'):
        raise ValueError('Пост или его подпись изменились; нужна новая проверка источника')
    video = await asyncio.to_thread(source.download_video, post, Path(folder), ffmpeg=ffmpeg, ffprobe=ffprobe)
    candidate = Candidate(owner_id, bulletin_id, revision, section_id, story['url'], _digest_text(story['summary']),
                          video.sha256, video.size, video.duration, video.width, video.height, video.has_audio, video.path)
    await verify(db, candidate)
    return candidate


async def verify(db, candidate: Candidate) -> None:
    """Recheck binding and file for review; this does not approve or authorize use."""
    if not isinstance(candidate, Candidate):
        raise ValueError('Неверный кандидат')
    story = await _current_story(db, candidate.owner_id, candidate.bulletin_id, candidate.revision, candidate.section_id)
    if story['url'] != candidate.post_url or _digest_text(story['summary']) != candidate.caption_sha256:
        raise ValueError('Источник видео изменился')
    await asyncio.to_thread(_check_file, candidate)
    # Detect edits while hashing, as well as before it. The caller must recheck
    # at the next trust boundary; this check is not an atomic admission claim.
    again = await _current_story(db, candidate.owner_id, candidate.bulletin_id, candidate.revision, candidate.section_id)
    if again != story:
        raise ValueError('Источник видео изменился')
