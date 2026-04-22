import os
import asyncio
import logging
from aiogram import Router, types, F
from aiogram.filters import Command

from database import db
from ai_logic import transcribe_audio_groq
# Импортируем обработчик Экса, чтобы он мог отвечать на голосовые команды
from handlers.ex_logic import handle_ex_request

router = Router()
logger = logging.getLogger(__name__)

# --- НАСТРОЙКИ АВТОРАСПОЗНАВАНИЯ ---
@router.message(Command("voice"))
async def toggle_voice(message: types.Message):
    """Включение/выключение авто-перевода ГС в текст для чата"""
    chat_id = message.chat.id
    args = message.text.split()
    
    if len(args) < 2 or args[1].lower() not in ["on", "off"]:
        doc = await db.settings.find_one({"setting": "auto_voice", "chat_id": chat_id})
        status = "ВКЛЮЧЕНО ✅" if doc and doc.get("value") else "ВЫКЛЮЧЕНО ❌"
        return await message.reply(
            f"🎙 **Распознавание голосовых**\nСейчас: {status}\n\n"
            f"Для изменения напиши:\n`/voice on` — включить авто-текст\n`/voice off` — отключить"
        )

    new_status = args[1].lower() == "on"
    
    await db.settings.update_one(
        {"setting": "auto_voice", "chat_id": chat_id},
        {"$set": {"value": new_status}},
        upsert=True
    )
    
    await message.reply(f"🎙 Авто-распознавание голосовых {'ВКЛЮЧЕНО ✅' if new_status else 'ВЫКЛЮЧЕНО ❌'}")

# --- ОБРАБОТЧИК ГОЛОСОВЫХ И КРУЖКОВ ---
@router.message(F.voice | F.video_note)
async def handle_voice_messages(message: types.Message):
    chat_id = message.chat.id
    
    # 1. Проверяем, включено ли авто-распознавание
    doc = await db.settings.find_one({"setting": "auto_voice", "chat_id": chat_id})
    auto_voice = doc.get("value", False) if doc else False
    
    # Если выключено и это не прямой ответ-команда Эксу (обработается в ex_logic) - игнорим
    if not auto_voice:
        return

    file_id = message.voice.file_id if message.voice else message.video_note.file_id
    file_ext = "ogg" if message.voice else "mp4"
    file_path = f"temp_{file_id}.{file_ext}"

    status_msg = await message.reply("⏳ <i>Слушаю...</i>", parse_mode="HTML")

    try:
        # Скачиваем файл
        file = await message.bot.get_file(file_id)
        await message.bot.download_file(file.file_path, file_path)
        
        # Распознаем
        text = await transcribe_audio_groq(file_path)
        text = text.strip()

        if not text or "❌" in text:
            await status_msg.edit_text("🤷‍♂️ Не удалось разобрать речь.")
            return

        # Отправляем результат
        result_text = f"🎙 <b>Расшифровка:</b>\n{text}"
        await status_msg.edit_text(result_text, parse_mode="HTML")

        # А теперь МАГИЯ: Если ГС начинается с обращения к Эксу, заставляем Экса ответить!
        lower_text = text.lower()
        if lower_text.startswith(("экс", "эксперт")):
            # Создаем фейковое сообщение с распознанным текстом
            fake_message = message.model_copy(update={"text": text})
            # Прокидываем его в основной ИИ-модуль
            await handle_ex_request(fake_message)

    except Exception as e:
        logger.error(f"Ошибка обработки голосового: {e}")
        await status_msg.edit_text("❌ Ошибка при обработке аудио.")
    finally:
        # Убираем мусор за собой
        if os.path.exists(file_path):
            os.remove(file_path)
            