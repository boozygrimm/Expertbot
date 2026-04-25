import asyncio
import logging
import os
import tempfile
import uuid
from pathlib import Path

import yt_dlp
from aiogram import F, Router, types
from aiogram.filters import Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ai_logic import normalize_music_query

router = Router()
download_semaphore = asyncio.Semaphore(1)

logger = logging.getLogger(__name__)

UNDESIRED_TITLE_MARKERS = {
    "remix",
    "cover",
    "live",
    "slowed",
    "reverb",
    "nightcore",
    "karaoke",
    "edit",
    "version",
    "tribute",
    "instrumental",
    "sped up",
    "speed up",
    "mashup",
}
ARTIST_HINT_BLACKLIST = {
    "original",
    "оригинал",
    "official",
    "audio",
    "video",
    "lyrics",
    "lyric",
}
PREFERRED_AUDIO_EXTS = {"mp3", "m4a", "mp4", "aac", "opus", "ogg", "webm"}
music_pick_sessions: dict[str, dict] = {}


def _tokenize(text: str) -> list[str]:
    cleaned = []
    current = []
    for char in (text or "").lower():
        if char.isalnum():
            current.append(char)
        else:
            if current:
                cleaned.append("".join(current))
                current = []
    if current:
        cleaned.append("".join(current))
    return cleaned


def _has_undesired_marker(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in UNDESIRED_TITLE_MARKERS)


def _split_artist_title(normalized_query: str) -> tuple[list[str], list[str]]:
    tokens = [token for token in _tokenize(normalized_query) if token not in ARTIST_HINT_BLACKLIST]
    if len(tokens) <= 1:
        return [], tokens
    if len(tokens) == 2:
        return [tokens[0]], [tokens[1]]
    return [tokens[0]], tokens[1:]


def _contains_all(tokens: list[str], haystack: str) -> bool:
    return bool(tokens) and all(token in haystack for token in tokens)


def _score_candidate(entry: dict, normalized_query: str) -> int:
    title = str(entry.get("title", "") or "")
    uploader = str(entry.get("uploader", "") or "")
    artist = str(entry.get("artist", "") or "")
    channel = str(entry.get("channel", "") or "")
    creator = str(entry.get("creator", "") or "")
    haystack = f"{title} {uploader} {artist} {channel} {creator}".lower()
    query_tokens = _tokenize(normalized_query)
    title_tokens = set(_tokenize(title))
    uploader_tokens = set(_tokenize(uploader))
    artist_tokens = set(_tokenize(artist))
    artist_query_tokens, title_query_tokens = _split_artist_title(normalized_query)

    score = 0
    for token in query_tokens:
        if token in title_tokens:
            score += 3
        elif token in uploader_tokens:
            score += 2
        elif token in artist_tokens:
            score += 4
        elif token in haystack:
            score += 1

    if query_tokens and all(token in haystack for token in query_tokens):
        score += 4

    artist_haystack = f"{uploader} {artist} {channel} {creator}".lower()
    title_haystack = title.lower()

    if artist_query_tokens:
        if _contains_all(artist_query_tokens, artist_haystack):
            score += 8
        elif _contains_all(artist_query_tokens, title_haystack):
            score += 1
        else:
            score -= 10

    if title_query_tokens:
        if _contains_all(title_query_tokens, title_haystack):
            score += 6
        elif _contains_all(title_query_tokens, haystack):
            score += 2

    if artist_query_tokens and title_query_tokens:
        if _contains_all(artist_query_tokens, artist_haystack) and _contains_all(title_query_tokens, title_haystack):
            score += 10

    if _has_undesired_marker(title) and not _has_undesired_marker(normalized_query):
        score -= 8

    duration = int(entry.get("duration", 0) or 0)
    if 120 <= duration <= 420:
        score += 1

    return score


def _is_probable_original(candidate: dict, normalized_query: str) -> bool:
    artist_tokens, title_tokens = _split_artist_title(normalized_query)
    artist_haystack = " ".join(
        str(candidate.get(field, "") or "")
        for field in ("performer", "uploader", "artist", "channel", "creator")
    ).lower()
    title_haystack = str(candidate.get("title", "") or "").lower()

    if artist_tokens and not _contains_all(artist_tokens, artist_haystack):
        return False
    if title_tokens and not _contains_all(title_tokens, title_haystack):
        return False
    if _has_undesired_marker(title_haystack) and not _has_undesired_marker(normalized_query):
        return False
    return True


def _candidate_label(candidate: dict, index: int) -> str:
    performer = candidate.get("performer") or "Unknown"
    title = candidate.get("title") or "Без названия"
    short_title = title if len(title) <= 32 else f"{title[:29]}..."
    short_performer = performer if len(performer) <= 18 else f"{performer[:15]}..."
    return f"{index}. {short_title} — {short_performer}"


def _build_candidate_keyboard(session_id: str, candidates: list[dict]) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, candidate in enumerate(candidates, start=1):
        builder.button(
            text=_candidate_label(candidate, index),
            callback_data=f"music_pick:{session_id}:{index - 1}",
        )
    builder.adjust(1)
    return builder.as_markup()


def search_track_candidates_sync(query: str) -> list[dict]:
    duration_filter = yt_dlp.utils.match_filter_func("duration >? 60 & duration <? 1800")
    ydl_opts = {
        "noplaylist": True,
        "quiet": True,
        "match_filter": duration_filter,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"scsearch10:{query}", download=False)
    except Exception:
        logger.exception("SoundCloud search error for query=%r", query)
        return []

    if not info or "entries" not in info:
        return []

    candidates: list[dict] = []
    seen_urls: set[str] = set()
    for entry in info["entries"]:
        if entry is None:
            continue
        webpage_url = entry.get("webpage_url") or entry.get("url")
        if not webpage_url or not str(webpage_url).startswith(("http://", "https://")):
            continue
        if webpage_url in seen_urls:
            continue
        seen_urls.add(webpage_url)

        candidate = {
            "title": entry.get("title", query.title()),
            "performer": entry.get("uploader", "Unknown Artist"),
            "uploader": entry.get("uploader", "Unknown Artist"),
            "artist": entry.get("artist", ""),
            "channel": entry.get("channel", ""),
            "creator": entry.get("creator", ""),
            "webpage_url": webpage_url,
            "duration": int(entry.get("duration", 0) or 0),
            "score": _score_candidate(entry, query),
        }
        candidates.append(candidate)

    candidates.sort(
        key=lambda item: (
            item["score"],
            not _has_undesired_marker(item["title"]),
            -abs(item["duration"] - 240) if item["duration"] else -9999,
        ),
        reverse=True,
    )
    probable_originals = [candidate for candidate in candidates if _is_probable_original(candidate, query)]
    if probable_originals:
        return probable_originals[:3]
    return candidates[:3]


def pick_best_candidate(candidates: list[dict]) -> dict | None:
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    best = candidates[0]
    second = candidates[1]
    if best["score"] >= 8 and best["score"] - second["score"] >= 3:
        return best
    if best["score"] >= 11:
        return best
    return None


def download_audio_sync(query: str) -> tuple:
    candidates = search_track_candidates_sync(query)
    candidate = pick_best_candidate(candidates) or (candidates[0] if candidates else None)
    if not candidate:
        return None, "Ничего не найдено", None

    return download_candidate_audio_sync(candidate)


def download_candidate_audio_sync(candidate: dict) -> tuple:
    temp_dir = tempfile.gettempdir()
    token = uuid.uuid4().hex
    outtmpl = os.path.join(temp_dir, f"{token}.%(ext)s")
    ydl_opts = {
        "format": "bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "outtmpl": outtmpl,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(candidate["webpage_url"], download=True)
            filepath = Path(ydl.prepare_filename(info))
    except Exception:
        logger.exception("Audio download error for candidate=%r", candidate)
        return None, candidate.get("title", "Ничего не найдено"), candidate.get("performer")

    if not filepath.exists():
        fallback = list(Path(temp_dir).glob(f"{token}.*"))
        filepath = fallback[0] if fallback else filepath

    if not filepath.exists():
        return None, candidate.get("title", "Ничего не найдено"), candidate.get("performer")

    if filepath.suffix.lower().lstrip(".") not in PREFERRED_AUDIO_EXTS:
        logger.warning("Unexpected audio extension for %s", filepath)

    return str(filepath), candidate.get("title", "Без названия"), candidate.get("performer", "Unknown Artist")


def resolve_audio_url_sync(query: str) -> tuple:
    candidates = search_track_candidates_sync(query)
    candidate = pick_best_candidate(candidates) or (candidates[0] if candidates else None)
    if not candidate:
        return None, "Ничего не найдено", None
    return candidate["webpage_url"], candidate["title"], candidate["performer"]


async def _send_downloaded_audio(message: types.Message, candidate: dict, status_msg: types.Message):
    async with download_semaphore:
        filepath, title, performer = await asyncio.to_thread(download_candidate_audio_sync, candidate)

    if not filepath or not os.path.exists(filepath):
        await status_msg.edit_text(
            f"❌ Не смог скачать трек **{candidate.get('title', 'без названия')}**. Попробуй другой вариант.",
            parse_mode="Markdown",
        )
        return

    try:
        await status_msg.edit_text("📤 Отправляю трек...")
        audio = types.FSInputFile(filepath)
        await message.answer_audio(audio=audio, title=title, performer=performer)
        try:
            await status_msg.delete()
        except Exception:
            logger.warning("Failed to delete music status message")
    except Exception:
        logger.exception("Audio send error for filepath=%r", filepath)
        await status_msg.edit_text(
            f"❌ Не смог отправить трек **{title}**. Попробуй другой вариант.",
            parse_mode="Markdown",
        )
    finally:
        try:
            os.remove(filepath)
        except OSError:
            logger.warning("Failed to remove temp audio file %s", filepath)


async def handle_music_request(message: types.Message, query: str):
    if not query:
        return await message.reply("Укажи песню. Пример: /song rammstein sonne")

    status_msg = await message.reply("🎵 Уточняю запрос и ищу лучший вариант...")
    normalized_query = await normalize_music_query(query)
    display_query = normalized_query or query

    async with download_semaphore:
        candidates = await asyncio.to_thread(search_track_candidates_sync, display_query)

    if not candidates:
        return await status_msg.edit_text(
            f"❌ Не нашёл трек по запросу «{display_query}». Попробуй указать исполнителя или точнее название."
        )

    best = pick_best_candidate(candidates)
    if best:
        return await _send_downloaded_audio(message, best, status_msg)

    session_id = uuid.uuid4().hex[:10]
    music_pick_sessions[session_id] = {
        "user_id": message.from_user.id if message.from_user else 0,
        "query": query,
        "normalized_query": display_query,
        "candidates": candidates,
    }
    lines = [f"🎵 Нашёл несколько вариантов для **{display_query}**. Выбери нужный:"]
    for index, candidate in enumerate(candidates, start=1):
        lines.append(f"{index}. {candidate['title']} — {candidate['performer']}")
    await status_msg.edit_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=_build_candidate_keyboard(session_id, candidates),
    )


@router.callback_query(F.data.startswith("music_pick:"))
async def music_pick(callback: types.CallbackQuery):
    _, session_id, index_raw = callback.data.split(":")
    session = music_pick_sessions.get(session_id)
    if not session:
        return await callback.answer("Список кандидатов устарел. Запроси трек заново.", show_alert=True)
    if session.get("user_id") and session["user_id"] != callback.from_user.id:
        return await callback.answer("Это не твой список треков.", show_alert=True)

    try:
        index = int(index_raw)
        candidate = session["candidates"][index]
    except Exception:
        return await callback.answer("Не удалось выбрать трек.", show_alert=True)

    await callback.answer()
    music_pick_sessions.pop(session_id, None)
    await _send_downloaded_audio(callback.message, candidate, callback.message)


@router.message(Command("song"))
async def cmd_search_song(message: types.Message, command: CommandObject):
    await handle_music_request(message, command.args or "")
