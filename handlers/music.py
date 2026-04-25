import os
import uuid
import asyncio
import logging
import yt_dlp
from aiogram import Router, types
from aiogram.filters import Command, CommandObject

router = Router()
download_semaphore = asyncio.Semaphore(1)

logger = logging.getLogger(__name__)

def download_audio_sync(query: str) -> tuple:
    """Синхронное скачивание аудио через SoundCloud."""
    base_dir = "/tmp"
    filename = uuid.uuid4().hex
    filepath = os.path.join(base_dir, f"{filename}.mp3")

    duration_filter = yt_dlp.utils.match_filter_func("duration >? 60 & duration <? 1800")
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(base_dir, f"{filename}.%(ext)s"),
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'noplaylist': True,
        'quiet': True,
        'extract_audio': True,
        'match_filter': duration_filter,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"scsearch5:{query}", download=True)
            title = query.title()
            performer = "Unknown Artist"

            if info and 'entries' in info:
                for entry in info['entries']:
                    if entry is None:
                        continue
                    title = entry.get('title', title)
                    performer = entry.get('uploader', performer)
                    break

            if os.path.exists(filepath):
                return filepath, title, performer
    except Exception as e:
        logger.exception("SoundCloud download error for query=%r", query)

    if os.path.exists(filepath):
        try:
            os.remove(filepath)
        except OSError:
            logger.warning("Failed to remove temp audio file %s", filepath)

    return None, "Ничего не найдено", None

@router.message(Command("song"))
async def cmd_search_song(message: types.Message, command: CommandObject):
    query = command.args 

    if not query:
        return await message.reply("Укажи песню. Пример: /song rammstein sonne")

    status_msg = await message.reply("🎵 Ищу трек в SoundCloud...")
    async with download_semaphore:
        filepath, title, performer = await asyncio.to_thread(download_audio_sync, query)

    if filepath and os.path.exists(filepath):
        try:
            await status_msg.edit_text("📤 Отправляю файл...")
            audio = types.FSInputFile(filepath)
            await message.answer_audio(audio=audio, title=title, performer=performer)
        except Exception as e:
            logger.exception("Audio send error for query=%r", query)
            await status_msg.edit_text(f"❌ Ошибка отправки аудио: {e}")
        finally:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except OSError:
                    logger.warning("Failed to remove temp audio file %s", filepath)
            try:
                await status_msg.delete()
            except Exception:
                logger.warning("Failed to delete status message for query=%r", query)
    else:
        await status_msg.edit_text("❌ Не нашёл трек подходящей длительности (от 1 до 30 минут).")
        
