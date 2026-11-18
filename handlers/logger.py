import asyncio
import logging
from aiogram import BaseMiddleware
from aiogram.types import Message, ContentType
from database import db
from ai_logic import recognize_media
from config import ADMIN_ID, get_msk_now
from handlers.admin_panel import is_feature_enabled
from handlers.audio_transcription import start_transcription, _store_result

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
        # Do not transcribe/log an owner's explicitly selected video for sending.
        if (event.video_note and event.chat.type == 'private' and event.chat.id == ADMIN_ID
                and event.from_user and event.from_user.id == ADMIN_ID and data.get('state')):
            state = data['state']
            from handlers.owner_tools_panel import OwnerInput
            if (await state.get_state() == OwnerInput.waiting.state
                    and (await state.get_data()).get('action') == 'owner_send_video'):
                return await handler(event, data)
        # 1. Проверяем, нужно ли игнорировать этот тип контента
        # Убрали ContentType.ANIMATION, чтобы бот видел гифки!
        ignore_types = [
            ContentType.NEW_CHAT_MEMBERS, ContentType.LEFT_CHAT_MEMBER, 
            ContentType.NEW_CHAT_TITLE, ContentType.PINNED_MESSAGE,
            ContentType.VIDEO, ContentType.VIDEO_NOTE,
            ContentType.VOICE, ContentType.DOCUMENT
        ]
        
        is_speech = bool(event.voice or event.video_note)
        should_log = event.content_type not in ignore_types
        if is_speech:
            # This middleware precedes FeatureAccessMiddleware. Fail closed if
            # the panel cannot be checked; /voice only controls chat publication.
            try:
                should_log = await is_feature_enabled(event.chat.id, "voice")
            except Exception as exc:
                logging.warning("Не удалось проверить модуль голосовых: %s", type(exc).__name__)
                should_log = False

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

            if is_speech:
                label = "Голосовое" if event.voice else "Кружок"
                content = f"{content} [{label}: распознаётся...]".strip()
            elif event.photo:
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

            saved = False
            try:
                # Store the topic for new messages; old forum records lack it.
                document = {
                    "message_id": event.message_id,
                    "chat_id": event.chat.id,
                    "message_thread_id": getattr(event, "message_thread_id", None),
                    "user_name": user_name,
                    "text": content,
                    "reactions": [],
                    "createdAt": get_msk_now(),
                }
                if is_speech:
                    document["audio_status"] = "pending"
                await db.messages.insert_one(document)
                saved = True
            except Exception as e:
                logging.error(f"Ошибка БД в логгере: {e}")

            # Existing photo logging stays independent of the speech pipeline.
            if has_media and file_id:
                asyncio.create_task(
                    background_vision_update(event.bot, event.message_id, event.chat.id, file_id, is_animation)
                )
            if is_speech and saved:
                task = start_transcription(event)
                if task is None:
                    await _store_result(event, None)
                else:
                    # Even a completed task remains available to the selected
                    # handler, avoiding a duplicate request if Mongo is slow.
                    data['voice_transcription_task'] = task

        # 3. КРИТИЧНО: Передаем сообщение дальше по цепочке роутеров!
        return await handler(event, data)
        