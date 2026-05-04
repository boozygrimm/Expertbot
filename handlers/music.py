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
download_semaphore = asyncio.Semaphore(2)

logger = logging.getLogger(__name__)

# Оставили маркеры, но штраф за них теперь минимальный
UNDESIRED_TITLE_MARKERS = {
    "remix", "cover", "live", "slowed", "reverb", "nightcore", "karaoke",
    "edit", "version", "tribute", "instrumental", "sped up", "speed up",
    "mashup", "acoustic", "piano", "guitar", "orchestral", "remastered",
    "reimagined", "rework", "flip", "bootleg", "unofficial",
}

ARTIST_HINT_BLACKLIST = {
    "audio", "video", "lyrics", "lyric",
}
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
    haystack = f"{title} {uploader} {artist}".lower()
    
    query_tokens = _tokenize(normalized_query)
    title_tokens = set(_tokenize(title))
    uploader_tokens = set(_tokenize(uploader))
    artist_tokens = set(_tokenize(artist))
    artist_query_tokens, title_query_tokens = _split_artist_title(normalized_query)

    score = 0
    # Начисляем баллы за совпадения
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

    artist_haystack = f"{uploader} {artist}".lower()
    title_haystack = title.lower()

    if artist_query_tokens:
        if _contains_all(artist_query_tokens, artist_haystack):
            score += 8
        elif _contains_all(artist_query_tokens, title_haystack):
            score += 1

    if title_query_tokens:
        if _contains_all(title_query_tokens, title_haystack):
            score += 6
        elif _contains_all(title_query_tokens, haystack):
            score += 2

    # ИСПРАВЛЕНИЕ: Мягкий штраф (-5 вместо -50). Трек опустится ниже чистого оригинала, но не исчезнет.
    if _has_undesired_marker(title) and not _has_undesired_marker(normalized_query):
        score -= 5

    duration = int(entry.get("duration", 0) or 0)
    if 120 <= duration <= 420:
        score += 1

    return score


def _candidate_label(candidate: dict, index: int) -> str:
    performer = candidate.get("performer") or "Unknown"
    title = candidate.get("title") or "Без названия"
    short_title = title if len(title) <= 32 else f"{title[:29]}..."
    short_performer = performer if len(performer) <= 18 else f"{performer[:15]}..."
    return f"{index}. {short_title} - {short_performer}"


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
    # Расширили окно длительности: 1-30 минут
    duration_filter = yt_dlp.utils.match_filter_func("duration >= 60 & duration <= 1800")
    ydl_opts = {
        "noplaylist": True,
        "quiet": True,
        "match_filter": duration_filter,
    }

    info = None
    # Только SoundCloud. Берем 10 результатов.
    search_query = f"scsearch10:{query}"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_query, download=False)
    except Exception as e:
        logger.debug("Search failed for query=%r error=%s", search_query, e)

    if not info or "entries" not in info:
        return []

    candidates: list[dict] = []
    seen_urls: set[str] = set()
    
    for entry in info["entries"]:
        if entry is None:
            continue
        webpage_url = entry.get("webpage_url") or entry.get("url")
        if not webpage_url or webpage_url in seen_urls:
            continue
        seen_urls.add(webpage_url)

        candidate = {
            "title": entry.get("title", query.title()),
            "performer": entry.get("uploader", "Unknown Artist"),
            "uploader": entry.get("uploader", "Unknown Artist"),
            "artist": entry.get("artist", ""),
            "webpage_url": webpage_url,
            "duration": int(entry.get("duration", 0) or 0),
            "score": _score_candidate(entry, query),
        }
        candidates.append(candidate)

    # ИСПРАВЛЕНИЕ: Убрали параноидальный отсев. Простая сортировка по очкам.
    candidates.sort(
        key=lambda item: (
            item["score"],
            not _has_undesired_marker(item["title"])
        ),
        reverse=True,
    )
    
    return candidates[:5]


def download_candidate_audio_sync(candidate: dict) -> tuple:
    temp_dir = tempfile.gettempdir()
    token = uuid.uuid4().hex
    outtmpl = os.path.join(temp_dir, f"{token}.%(ext)s")
    
    ydl_opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "outtmpl": outtmpl,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(candidate["webpage_url"], download=True)
            filepath = Path(ydl.prepare_filename(info))
    except Exception:
        logger.exception("Audio download error for SC candidate=%r", candidate)
        return None, candidate.get("title", "Ничего не найдено"), candidate.get("performer")

    if not filepath.exists():
        fallback = list(Path(temp_dir).glob(f"{token}.*"))
        filepath = fallback[0] if fallback else filepath

    if not filepath.exists():
        return None, candidate.get("title", "Ничего не найдено"), candidate.get("performer")

    return str(filepath), candidate.get("title", "Без названия"), candidate.get("performer", "Unknown Artist")


async def _send_downloaded_audio(message: types.Message, candidate: dict, status_msg: types.Message):
    async with download_semaphore:
        filepath, title, performer = await asyncio.to_thread(download_candidate_audio_sync, candidate)

    if not filepath or not os.path.exists(filepath):
        await status_msg.edit_text(
            f"❌ Не смог скачать трек **{candidate.get('title', 'без названия')}**.",
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
            pass
    except Exception:
        logger.exception("Audio send error for filepath=%r", filepath)
        await status_msg.edit_text(
            f"❌ Не смог отправить трек **{title}** в Telegram.",
            parse_mode="Markdown",
        )
    finally:
        try:
            os.remove(filepath)
        except OSError:
            pass


async def handle_music_request(message: types.Message, query: str):
    if not query:
        return await message.reply("Укажи песню. Пример: /song rammstein sonne")

    status_msg = await message.reply("🎵 Ищу трек в SoundCloud...")
    
    # 1. Пробуем "умный" нормализованный запрос
    normalized_query = await normalize_music_query(query)
    display_query = normalized_query or query

    async with download_semaphore:
        candidates = await asyncio.to_thread(search_track_candidates_sync, display_query)

    # 2. ИСПРАВЛЕНИЕ: Если нейронка сломала русский запрос, ищем по сырому
    if not candidates and display_query != query:
        logger.info(f"Fallback to raw query: '{query}' instead of '{display_query}'")
        async with download_semaphore:
            candidates = await asyncio.to_thread(search_track_candidates_sync, query)
        display_query = query

    if not candidates:
        return await status_msg.edit_text(
            f"❌ Не нашёл трек по запросу «{display_query}». Возможно, его вообще нет на SoundCloud."
        )

    session_id = uuid.uuid4().hex[:10]
    music_pick_sessions[session_id] = {
        "user_id": message.from_user.id if message.from_user else 0,
        "query": query,
        "normalized_query": display_query,
        "candidates": candidates,
    }
    
    lines = [f"🎶 Нашёл варианты для **{display_query}**. Выбери нужный:"]
    for index, candidate in enumerate(candidates, start=1):
        lines.append(f"{index}. {candidate['title']} - {candidate['performer']}")
    
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
        return await callback.answer("Список устарел. Запроси трек заново.", show_alert=True)
    if session.get("user_id") and session["user_id"] != callback.from_user.id:
        return await callback.answer("Это не твой запрос.", show_alert=True)

    try:
        index = int(index_raw)
        candidate = session["candidates"][index]
    except Exception:
        return await callback.answer("Ошибка выбора трека.", show_alert=True)

    await callback.answer()
    music_pick_sessions.pop(session_id, None)
    await _send_downloaded_audio(callback.message, candidate, callback.message)


@router.message(Command("song"))
async def cmd_search_song(message: types.Message, command: CommandObject):
    await handle_music_request(message, command.args or "")
    