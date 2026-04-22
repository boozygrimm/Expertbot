import os
import uuid
import asyncio
import yt_dlp
from aiogram import Router, types
from aiogram.filters import Command, CommandObject

router = Router()

def download_audio_sync(query: str) -> tuple:
    """Синхронное скачивание аудио через SoundCloud"""
    base_dir = "/tmp"
    filename = uuid.uuid4().hex
    filepath = os.path.join(base_dir, f"{filename}.mp3")
    
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
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"scsearch1:{query}", download=True)
            if 'entries' in info and len(info['entries']) > 0:
                entry = info['entries'][0]
                title = entry.get('title', query.title())
                performer = entry.get('uploader', 'Unknown Artist')
                
                if os.path.exists(filepath):
                    return filepath, title, performer
    except Exception as e:
        print(f"SoundCloud error: {e}")
        
    return None, "Ничего не найдено", None

@router.message(Command("song"))
async def cmd_search_song(message: types.Message, command: CommandObject):
    # Актуальный способ aiogram 3 для получения аргументов команды
    query = command.args 
    
    if not query:
        return await message.reply("Укажи песню. Пример: /song rammstein sonne")
    
    status_msg = await message.reply("🎵 Ищу трек в SoundCloud и конвертирую...")
    filepath, title, performer = await asyncio.to_thread(download_audio_sync, query)
    
    if filepath and os.path.exists(filepath):
        try:
            audio = types.FSInputFile(filepath)
            await message.answer_audio(audio=audio, title=title, performer=performer)
        except Exception as e:
            await status_msg.edit_text(f"❌ Ошибка отправки аудио: {e}")
        finally:
            # Гарантированное удаление файла даже при ошибке Telegram API
            if os.path.exists(filepath): 
                os.remove(filepath)
            await status_msg.delete()
    else:
        await status_msg.edit_text("❌ Песню не удалось найти. Попробуй изменить запрос.")
        