import asyncio
import logging
from aiogram import BaseMiddleware
from aiogram.types import Message, ContentType
from database import db
from ai_logic import recognize_media
from config import get_msk_now

async def background_vision_update(bot, message_id, chat_id, file_id, is_animation=False):
    """Фоновое распознавание медиа"""
    try:
        desc = await recognize_media(bot, file_id, is_animation=is_animation)
        
        if desc and desc not in ["[ФОТО]", "[СТИКЕР]", "[GIF]", "[МЕДИА]"]:
            msg_doc = await db.messages.find_one({"message_id": message_id, "chat_id": chat_id})
            if msg_doc:
                new_text = f"{msg_doc.get('text', '')} {desc}".strip()
                await db.messages.update_one(
                    {"message_id": message_id, "chat_id": chat_id},
                    {"$set": {"text": new_text}}
                )
    except Exception as e:
        logging.error(f"Фоновая ошибка Vision: {e}")

class ChatLoggerMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message, data: dict):
        # 1. Проверяем, нужно ли игнорировать этот тип контента
        # Убрали ContentType.ANIMATION, чтобы бот видел гифки!
        ignore_types = [
            ContentType.NEW_CHAT_MEMBERS, ContentType.LEFT_CHAT_MEMBER, 
            ContentType.NEW_CHAT_TITLE, ContentType.PINNED_MESSAGE,
            ContentType.VIDEO, ContentType.VIDEO_NOTE,
            ContentType.VOICE, ContentType.DOCUMENT
        ]
        
        should_log = event.content_type not in ignore_types

        # Игнорируем команды и системные слова
        if event.text:
            text_lower = event.text.lower()
            if text_lower.startswith(("/", "буква ", "слово ")):
                should_log = False

        # 2. Если всё ок - пишем в базу
        if should_log:
            user_name = event.from_user.first_name if event.from_user else (event.sender_chat.title if event.sender_chat else "Кто-то")
            content = event.text or event.caption or ""
            
            has_media = False
            is_animation = False
            file_id = None

            if event.photo:
                has_media = True
                file_id = event.photo[-1].file_id
                content = f"{content} [Фото анализируется...]".strip()
            elif event.animation:
                has_media = True
                is_animation = True
                file_id = event.animation.file_id
                content = f"{content} [GIF анализируется...]".strip()
            elif event.sticker and not (event.sticker.is_animated or event.sticker.is_video):
                has_media = True
                file_id = event.sticker.file_id
                content = f"{content} [Стикер анализируется...]".strip()

            try:
                # Мгновенная запись
                await db.messages.insert_one({
                    "message_id": event.message_id,
                    "chat_id": event.chat.id,
                    "user_name": user_name,
                    "text": content,
                    "reactions": [],
                    "createdAt": get_msk_now()
                })
            except Exception as e:
                logging.error(f"Ошибка БД в логгере: {e}")

            # Запускаем зрение в фоне
            if has_media and file_id:
                asyncio.create_task(
                    background_vision_update(event.bot, event.message_id, event.chat.id, file_id, is_animation)
                )

        # 3. КРИТИЧНО: Передаем сообщение дальше по цепочке роутеров!
        return await handler(event, data)
        