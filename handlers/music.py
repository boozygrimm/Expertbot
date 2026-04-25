import asyncio
import logging
import yt_dlp
from aiogram import Router, types
from aiogram.filters import Command, CommandObject

router = Router()
download_semaphore = asyncio.Semaphore(1)

logger = logging.getLogger(__name__)

def resolve_audio_url_sync(query: str) -> tuple:
    """Ищет страницу трека без скачивания аудио."""

    duration_filter = yt_dlp.utils.match_filter_func("duration >? 60 & duration <? 1800")
    ydl_opts = {
        'noplaylist': True,
        'quiet': True,
        'match_filter': duration_filter,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"scsearch5:{query}", download=False)
            if not info or 'entries' not in info:
                return None, "Ничего не найдено", None

            for entry in info['entries']:
                if entry is None:
                    continue
                webpage_url = entry.get('webpage_url') or entry.get('url')
                if not webpage_url or not str(webpage_url).startswith(("http://", "https://")):
                    continue
                title = entry.get('title', query.title())
                performer = entry.get('uploader', 'Unknown Artist')
                return webpage_url, title, performer
    except Exception:
        logger.exception("SoundCloud resolve error for query=%r", query)

    return None, "Ничего не найдено", None

@router.message(Command("song"))
async def cmd_search_song(message: types.Message, command: CommandObject):
    query = command.args

    if not query:
        return await message.reply("Укажи песню. Пример: /song rammstein sonne")

    status_msg = await message.reply("🎵 Ищу трек в SoundCloud...")
    async with download_semaphore:
        track_url, title, performer = await asyncio.to_thread(resolve_audio_url_sync, query)

    if track_url:
        try:
            text = f"🎵 **{title}**"
            if performer:
                text += f"\n👤 {performer}"
            text += f"\n🔗 {track_url}"
            await status_msg.edit_text(text, parse_mode="Markdown", disable_web_page_preview=False)
        except Exception:
            logger.exception("Track link send error for query=%r", query)
            await status_msg.edit_text(f"🔗 {track_url}")
    else:
        await status_msg.edit_text("❌ Не нашёл трек подходящей длительности (от 1 до 30 минут).")
        
