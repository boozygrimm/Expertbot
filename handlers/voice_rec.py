import asyncio
import html
import logging
from aiogram import Router, types, F
from aiogram.filters import Command

from database import db
from handlers.audio_transcription import get_transcription, short_transcript
from handlers.admin_panel import get_chat_feature_flags
# Импортируем обработчик Экса, чтобы он мог отвечать на голосовые команды
from handlers.ex_logic import EX_ADDRESS_RE, handle_ex_request, is_reply_to_bot

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
            f"🎙 **Публикация расшифровок в чат**\nСейчас: {status}\n\n"
            "`/voice on` — публиковать текст\n`/voice off` — не публиковать\n\n"
            "При включённом модуле «Голосовые» текст сохраняется в истории даже при /voice off. "
            "Если начать запись с «Экс» или ответить записью на сообщение бота, "
            "он ответит и при /voice off — без публикации расшифровки."
        )

    new_status = args[1].lower() == "on"
    
    await db.settings.update_one(
        {"setting": "auto_voice", "chat_id": chat_id},
        {"$set": {"value": new_status}},
        upsert=True
    )
    
    await message.reply(
        f"🎙 Публикация расшифровок в чат {'ВКЛЮЧЕНА ✅' if new_status else 'ВЫКЛЮЧЕНА ❌'}. "
        "Сохранение в историю управляется модулем «Голосовые» в панели."
    )

# --- ОБРАБОТЧИК ГОЛОСОВЫХ И КРУЖКОВ ---
@router.message(F.voice | F.video_note)
async def handle_voice_messages(message: types.Message, voice_transcription_task=None):
    chat_id = message.chat.id
    
    # 1. Проверяем, включено ли авто-распознавание
    doc = await db.settings.find_one({"setting": "auto_voice", "chat_id": chat_id})
    auto_voice = doc.get("value", False) if doc else False
    
    # Publication is optional. The logger already starts transcription even
    # with /voice off; both paths share the same in-flight result.
    status_msg = None
    if auto_voice:
        status_msg = await message.reply("⏳ <i>Слушаю...</i>", parse_mode="HTML")
    try:
        text = (await asyncio.shield(voice_transcription_task) if voice_transcription_task is not None
                else await get_transcription(message, retry_failed=False))
        if not text:
            if status_msg:
                await status_msg.edit_text("🤷‍♂️ Не удалось разобрать речь.")
            return

        if status_msg:
            # Telegram text is limited to 4096 characters; escape recognized HTML.
            shown = short_transcript(text)
            await status_msg.edit_text(f"🎙 <b>Расшифровка:</b>\n{html.escape(shown)}", parse_mode="HTML")

        # A normal word like «экскурсия» is not an address; a direct reply to
        # this bot is, just as it is for text messages.
        text = text.strip()
        if not text or (not EX_ADDRESS_RE.match(text) and not is_reply_to_bot(message)):
            return
        prefer_voice = False
        if message.chat.type in ('group', 'supergroup'):
            try:
                flags = await get_chat_feature_flags(chat_id)
            except Exception as exc:
                logger.warning("Не удалось проверить настройки обращения: %s", type(exc).__name__)
                return
            if not flags.get('voice', True) or not flags.get('assistant', True):
                return
            prefer_voice = bool(message.voice and flags.get('auto_voice_replies', False))
        fake_message = message.model_copy(update={"text": text})
        if prefer_voice:
            await handle_ex_request(fake_message, prefer_voice=True)
        else:
            await handle_ex_request(fake_message)
    except Exception as exc:
        logger.warning("Ошибка обработки голосового: %s", type(exc).__name__)
        if status_msg:
            await status_msg.edit_text("❌ Ошибка при обработке аудио.")
