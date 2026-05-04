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

# Используем нормализацию из твоего ai_logic.py
from ai_logic import normalize_music_query

router = Router()
download_semaphore = asyncio.Semaphore(2)
logger = logging.getLogger(__name__)

# Сессии для хранения результатов поиска
music_pick_sessions: dict[str, dict] = {}

def search_track_candidates_sync(query: str) -> list[dict]:
    """Поиск через scsearch, чтобы не ловить 404 на SoundCloud."""
    ydl_opts = {
        "extract_flat": True,
        "quiet": True,
        "noplaylist": True,
    }
    # Ищем 5 вариантов через внутренний поиск yt-dlp
    search_query = f"scsearch5:{query}"
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_query, download=False)
            if not info or "entries" not in info:
                return []
            
            candidates = []
            for entry in info["entries"]:
                if not entry: continue
                candidates.append({
                    "title": entry.get("title", "Без названия"),
                    "performer": entry.get("uploader", "Неизвестен"),
                    "url": entry.get("url") or entry.get("webpage_url"),
                })
            return candidates
    except Exception as e:
        logger.error(f"Search error: {e}")
        return []

def download_audio_raw_sync(url: str) -> str | None:
    """Просто качаем файл без перекодировки (без FFmpeg)."""
    temp_dir = tempfile.gettempdir()
    file_id = uuid.uuid4().hex
    # Шаблон для скачивания (расширение подставится само)
    outtmpl = os.path.join(temp_dir, f"{file_id}.%(ext)s")
    
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "noplaylist": True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # Узнаем итоговый путь файла
            return ydl.prepare_filename(info)
    except Exception as e:
        logger.error(f"Download error: {e}")
        return None

async def handle_music_request(message: types.Message, query: str):
    if not query.strip():
        return await message.reply("Напиши название песни.")

    status_msg = await message.reply("🔎 Ищу...")
    
    # Нормализация через ИИ (как в твоем файле)
    try:
        search_q = await normalize_music_query(query)
    except Exception:
        search_q = query

    candidates = await asyncio.to_thread(search_track_candidates_sync, search_q)
    
    if not candidates:
        return await status_msg.edit_text(f"❌ Ничего не нашел по запросу: {search_q}")

    session_id = uuid.uuid4().hex[:8]
    music_pick_sessions[session_id] = {
        "user_id": message.from_user.id if message.from_user else 0,
        "candidates": candidates
    }

    builder = InlineKeyboardBuilder()
    for i, c in enumerate(candidates):
        label = f"{c['title'][:30]} — {c['performer'][:15]}"
        builder.button(text=label, callback_data=f"snd:{session_id}:{i}")
    builder.adjust(1)

    await status_msg.edit_text(
        f"🎶 Результаты для: **{search_q}**\nВыбери трек:",
        parse_mode="Markdown",
        reply_markup=builder.as_markup()
    )

@router.callback_query(F.data.startswith("snd:"))
async def music_callback(callback: types.CallbackQuery):
    _, session_id, idx_str = callback.data.split(":")
    session = music_pick_sessions.get(session_id)
    
    if not session or (callback.from_user and callback.from_user.id != session["user_id"]):
        return await callback.answer("Ошибка доступа или сессия истекла.", show_alert=True)

    candidate = session["candidates"][int(idx_str)]
    await callback.message.edit_text(f"📥 Загружаю: {candidate['title']}...")
    
    async with download_semaphore:
        filepath = await asyncio.to_thread(download_audio_raw_sync, candidate["url"])
    
    if filepath and os.path.exists(filepath):
        try:
            # Отправляем как аудио. Telegram сам определит тип файла.
            await callback.message.answer_audio(
                audio=types.FSInputFile(filepath),
                title=candidate["title"],
                performer=candidate["performer"]
            )
            await callback.message.delete()
        except Exception as e:
            logger.error(f"Send error: {e}")
            await callback.message.edit_text("❌ Telegram не принял этот файл.")
        finally:
            if os.path.exists(filepath):
                os.remove(filepath)
    else:
        await callback.message.edit_text("❌ Не удалось скачать файл.")
    
    music_pick_sessions.pop(session_id, None)

@router.message(Command("song"))
async def cmd_song(message: types.Message, command: CommandObject):
    await handle_music_request(message, command.args or "")
        
