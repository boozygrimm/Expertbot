import asyncio
import logging
import yt_dlp
from aiogram import Router, types
from aiogram.filters import Command, CommandObject

router = Router()
download_semaphore = asyncio.Semaphore(1)

logger = logging.getLogger(__name__)

def resolve_audio_url_sync(query: str) -> tuple:
    """Ищет прямой URL аудио без локального скачивания."""
    max_telegram_url_size = 20 * 1024 * 1024

    duration_filter = yt_dlp.utils.match_filter_func("duration >? 60 & duration <? 1800")
    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'quiet': True,
        'match_filter': duration_filter,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"scsearch5:{query}", download=False)
            if not info or 'entries' not in info:
                return None, "Ничего не найдено", None, None

            for entry in info['entries']:
                if entry is None:
                    continue
                source_url = entry.get('webpage_url') or entry.get('url')
                if not source_url:
                    continue

                try:
                    full_info = ydl.extract_info(source_url, download=False)
                except Exception:
                    logger.warning("Failed to resolve candidate for query=%r url=%r", query, source_url)
                    continue

                audio_url = full_info.get('url')
                if not audio_url or not str(audio_url).startswith(("http://", "https://")):
                    continue

                ext = (full_info.get('ext') or '').lower()
                if ext not in {'mp3', 'm4a'}:
                    continue

                filesize = full_info.get('filesize') or full_info.get('filesize_approx')
                if filesize and int(filesize) > max_telegram_url_size:
                    continue

                title = full_info.get('title', entry.get('title', query.title()))
                performer = full_info.get('uploader', entry.get('uploader', 'Unknown Artist'))
                webpage_url = full_info.get('webpage_url', source_url)
                return audio_url, title, performer, webpage_url
    except Exception:
        logger.exception("SoundCloud resolve error for query=%r", query)

    return None, "Ничего не найдено", None, None

@router.message(Command("song"))
async def cmd_search_song(message: types.Message, command: CommandObject):
    query = command.args

    if not query:
        return await message.reply("Укажи песню. Пример: /song rammstein sonne")

    status_msg = await message.reply("🎵 Ищу трек в SoundCloud...")
    async with download_semaphore:
        audio_url, title, performer, webpage_url = await asyncio.to_thread(resolve_audio_url_sync, query)

    if audio_url:
        sent_ok = False
        try:
            await status_msg.edit_text("📤 Передаю ссылку Telegram...")
            await message.answer_audio(audio=audio_url, title=title, performer=performer)
            sent_ok = True
        except Exception:
            logger.exception("Audio URL send error for query=%r", query)
            if webpage_url:
                await status_msg.edit_text(f"❌ Telegram не забрал аудио. Вот ссылка на трек:\n{webpage_url}")
            else:
                await status_msg.edit_text("❌ Telegram не смог забрать аудио по ссылке.")
        finally:
            if sent_ok:
                try:
                    await status_msg.delete()
                except Exception:
                    logger.warning("Failed to delete status message for query=%r", query)
    else:
        await status_msg.edit_text("❌ Не нашёл подходящий прямой аудиофайл (1-30 минут, до 20 МБ).")
        
