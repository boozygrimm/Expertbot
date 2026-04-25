import asyncio
import urllib.parse
import aiohttp
import logging
import re
from datetime import datetime
from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile

from database import db
from config import get_msk_now
from ai_logic import generate_fast_image_prompt

router = Router()
logger = logging.getLogger(__name__)

generation_lock = asyncio.Lock()
COOLDOWN_SECONDS = 5 # Можно уменьшить кулдаун, Pollinations работает быстро
IMAGE_PRICE = 250 

async def get_smart_prompt(user_query: str) -> str:
    res = await generate_fast_image_prompt(user_query)
    
    if res and "❌" not in res:
        return res
        
    logger.warning("ИИ-переводчик недоступен. Используем План Б (сырой промпт).")
    if not bool(re.search('[а-яА-ЯёЁ]', user_query)):
        return f"{user_query}, masterpiece, highly detailed, 4k"
    else:
        return f"{user_query}, masterpiece, best quality"

async def fetch_image_pollinations(prompt: str):
    # Pollinations.ai принимает промпт прямо в URL и возвращает картинку
    safe_prompt = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{safe_prompt}?width=1024&height=1024&nologo=true"

    try:
        async with aiohttp.ClientSession() as session:
            # Делаем пару попыток на случай обрыва сети
            for attempt in range(2):
                async with session.get(url, timeout=60) as resp:
                    if resp.status == 200:
                        return await resp.read()
                    else:
                        logger.error(f"Ошибка Pollinations {resp.status}")
                        await asyncio.sleep(2)
    except Exception as e:
        logger.error(f"Сетевая ошибка при генерации Pollinations: {e}")
        return None
    return None

async def process_image_request(message: types.Message, query: str):
    chat_id = message.chat.id
    user_id = message.from_user.id
    user_name = message.from_user.first_name

    # 0. ПРОВЕРКА БЕСПЛАТНЫХ ЛИМИТОВ
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    usage_doc = await db.game_data.find_one({
        "type": "daily_free_img", 
        "chat_id": chat_id, 
        "user_id": user_id, 
        "date": today_str
    })
    free_used = usage_doc.get("count", 0) if usage_doc else 0
    is_free = free_used < 2

    # 1. ПРОВЕРКА БАЛАНСА (Если лимит исчерпан)
    if not is_free:
        pipeline = [
            {"$match": {"chat_id": chat_id, "user_id": user_id}},
            {"$group": {"_id": "$user_id", "total_score": {"$sum": "$score"}}}
        ]
        
        try:
            cursor = db.economy.aggregate(pipeline)
            result = await cursor.to_list(length=1)
            balance = result[0]["total_score"] if result else 0
        except Exception as e:
            logger.error(f"Ошибка БД при проверке баланса: {e}")
            balance = 0

        if balance < IMAGE_PRICE:
            return await message.reply(
                f"❌ **Художник требует оплату!**\n\n"
                f"Бесплатные попытки на сегодня закончились.\n"
                f"Стоимость картины: `{IMAGE_PRICE}` очков.\n"
                f"Твой баланс: `{balance}` очков.\n\n"
                f"Иди зарабатывай в `/pole`, `/vis` или Анахронизм!",
                parse_mode="Markdown"
            )

    # 2. ОЧЕРЕДЬ ГЕНЕРАЦИИ
    is_queued = generation_lock.locked()
    status_msg = await message.reply(
        "⏳ *Художник занят. Запрос в очереди...*" if is_queued else "🎨 *Готовлю холст...*", 
        parse_mode="Markdown"
    )

    async with generation_lock:
        if is_queued:
            try:
                await status_msg.edit_text("🎨 *Очередь подошла! Начинаю рисовать...*", parse_mode="Markdown")
            except Exception as e:
                logger.debug("Failed to update queued image status message: %s", e)
        
        final_prompt = await get_smart_prompt(query)
        image_data = await fetch_image_pollinations(final_prompt)

        # 3. ВЫДАЧА РЕЗУЛЬТАТА И СПИСАНИЕ ОЧКОВ/ЛИМИТОВ
        if image_data:
            try:
                photo = BufferedInputFile(image_data, filename="result.jpg")
                
                if is_free:
                    remains = 1 - free_used
                    caption = f"✨ **Успешно!** (Бесплатная генерация, осталось на сегодня: {remains})\nЗапрос: _{query}_"
                else:
                    caption = f"✨ **Успешно!** (Списано {IMAGE_PRICE} очков)\nЗапрос: _{query}_"

                await message.answer_photo(
                    photo=photo, 
                    caption=caption,
                    parse_mode="Markdown"
                )
                try:
                    await status_msg.delete()
                except Exception as e:
                    logger.debug("Failed to delete image status message: %s", e)

                if is_free:
                    await db.game_data.update_one(
                        {"type": "daily_free_img", "chat_id": chat_id, "user_id": user_id, "date": today_str},
                        {"$inc": {"count": 1}},
                        upsert=True
                    )
                else:
                    await db.economy.insert_one({
                        "game_type": "image_generation_paid",
                        "chat_id": chat_id,
                        "user_id": user_id,
                        "user_name": user_name,
                        "score": -IMAGE_PRICE,
                        "date": get_msk_now()
                    })
            except Exception as e:
                logger.error(f"Ошибка отправки фото ТГ: {e}")
                try:
                    await status_msg.edit_text("❌ Ошибка отправки фото в Telegram. Очки/лимиты сохранены.")
                except Exception as edit_error:
                    logger.debug("Failed to update image error status message: %s", edit_error)
        else:
            try:
                await status_msg.edit_text("❌ Ошибка генерации (нейросеть недоступна). Очки/лимиты сохранены. Попробуй позже.")
            except Exception as e:
                logger.debug("Failed to update unavailable image status message: %s", e)
        
        await asyncio.sleep(COOLDOWN_SECONDS)

@router.message(Command("img", "pic", "рис"))
async def cmd_generate_image(message: types.Message, command: CommandObject):
    query = command.args
    if not query:
        return await message.reply("Напиши, что нарисовать. Пример: `/img кот самурай`", parse_mode="Markdown")
    await process_image_request(message, query)
