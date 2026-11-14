import asyncio
import logging
import os
import re
import urllib.parse

import aiohttp
from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile

from database import db
from config import ADMIN_ID, get_msk_now
from ai_logic import generate_fast_image_prompt
from handlers import image_providers

router = Router()
logger = logging.getLogger(__name__)

generation_lock = asyncio.Lock()
_probe_lock = asyncio.Lock()
COOLDOWN_SECONDS = 5  # Сохраняем прежнюю общую паузу между запросами.
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
    """Existing generator, retained only until the new providers are verified."""
    safe_prompt = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{safe_prompt}?width=1024&height=1024&nologo=true"
    try:
        async with aiohttp.ClientSession() as session:
            for attempt in range(2):
                async with session.get(url, timeout=60) as resp:
                    if resp.status == 200:
                        return await resp.read()
                    logger.error("Ошибка Pollinations %s", resp.status)
                    await asyncio.sleep(2)
    except Exception as e:
        logger.error("Сетевая ошибка Pollinations: %s", type(e).__name__)
    return None


async def generate_image(prompt: str):
    # Activation is explicit. Empty or unknown values keep the old service;
    # simply configuring Cloudflare credentials must not switch production.
    if os.getenv('IMAGE_PROVIDER_MODE', '').strip().lower() == 'cloudflare':
        return await image_providers.generate_image(prompt)
    return await fetch_image_pollinations(prompt)


async def process_image_request(message: types.Message, query: str):
    chat_id = message.chat.id
    user_id = message.from_user.id
    user_name = message.from_user.first_name

    # 1. ОЧЕРЕДЬ ГЕНЕРАЦИИ
    is_queued = generation_lock.locked()
    status_msg = await message.reply(
        "⏳ *Художник занят. Запрос в очереди...*" if is_queued else "🎨 *Готовлю холст...*", 
        parse_mode="Markdown"
    )

    async with generation_lock:
        # The same in-process lock also covers delivery and accounting below.
        # Read the current day, quota and balance only when this request reaches
        # the front of the queue, not when it was submitted.
        today_str = get_msk_now().strftime("%Y-%m-%d")
        try:
            usage_doc = await db.game_data.find_one({
                "type": "daily_free_img", "chat_id": chat_id,
                "user_id": user_id, "date": today_str,
            })
        except Exception as e:
            logger.error("Ошибка БД при проверке лимита: %s", type(e).__name__)
            await status_msg.edit_text("❌ Не удалось проверить лимит картинок. Попробуй позже.")
            return
        free_used = usage_doc.get("count", 0) if usage_doc else 0
        is_free = free_used < 2

        if not is_free:
            pipeline = [
                {"$match": {"chat_id": chat_id, "user_id": user_id}},
                {"$group": {"_id": "$user_id", "total_score": {"$sum": "$score"}}},
            ]
            try:
                cursor = db.economy.aggregate(pipeline)
                result = await cursor.to_list(length=1)
                balance = result[0]["total_score"] if result else 0
            except Exception as e:
                logger.error("Ошибка БД при проверке баланса: %s", type(e).__name__)
                await status_msg.edit_text("❌ Не удалось проверить баланс. Попробуй позже.")
                return

            if balance < IMAGE_PRICE:
                await status_msg.edit_text(
                    f"❌ **Художник требует оплату!**\n\n"
                    f"Бесплатные попытки на сегодня закончились.\n"
                    f"Стоимость картины: `{IMAGE_PRICE}` очков.\n"
                    f"Твой баланс: `{balance}` очков.\n\n"
                    f"Иди зарабатывай в `/pole`, `/vis` или Анахронизм!",
                    parse_mode="Markdown",
                )
                return

        if is_queued:
            try:
                await status_msg.edit_text("🎨 *Очередь подошла! Начинаю рисовать...*", parse_mode="Markdown")
            except Exception as e:
                logger.debug("Failed to update queued image status message: %s", e)

        final_prompt = await get_smart_prompt(query)
        image_data = await generate_image(final_prompt)

        # 3. ВЫДАЧА РЕЗУЛЬТАТА И СПИСАНИЕ ОЧКОВ/ЛИМИТОВ
        if image_data:
            try:
                filename = "result.png" if image_data.startswith(b"\x89PNG") else "result.jpg"
                photo = BufferedInputFile(image_data, filename=filename)

                if is_free:
                    remains = 1 - free_used
                    caption = f"✨ **Успешно!** (Бесплатная генерация, осталось на сегодня: {remains})\nЗапрос: _{query}_"
                else:
                    caption = f"✨ **Успешно!** (Стоимость: {IMAGE_PRICE} очков)\nЗапрос: _{query}_"

                await message.answer_photo(photo=photo, caption=caption, parse_mode="Markdown")
            except Exception as e:
                logger.error("Не подтверждена отправка фото в Telegram: %s", type(e).__name__)
                try:
                    await status_msg.edit_text("❌ Не удалось подтвердить отправку в Telegram. Проверь чат; очки/лимиты не списывались.")
                except Exception as edit_error:
                    logger.debug("Failed to update image error status message: %s", edit_error)
            else:
                # Списание остаётся после отправки. Если БД не ответила, результат
                # записи неизвестен: не сообщаем, что фото не доставлено или что
                # списание точно не произошло.
                try:
                    if is_free:
                        await db.game_data.update_one(
                            {"type": "daily_free_img", "chat_id": chat_id, "user_id": user_id, "date": today_str},
                            {"$inc": {"count": 1}}, upsert=True
                        )
                    else:
                        await db.economy.insert_one({
                            "game_type": "image_generation_paid", "chat_id": chat_id,
                            "user_id": user_id, "user_name": user_name,
                            "score": -IMAGE_PRICE, "date": get_msk_now()
                        })
                except Exception as e:
                    logger.error("Не подтверждён учёт изображения: %s", type(e).__name__)
                    try:
                        await status_msg.edit_text("⚠️ Картинка отправлена, но не удалось подтвердить учёт очков или лимита.")
                    except Exception as edit_error:
                        logger.debug("Failed to update image accounting status: %s", edit_error)
                else:
                    try:
                        await status_msg.delete()
                    except Exception as e:
                        logger.debug("Failed to delete image status message: %s", e)
        else:
            try:
                await status_msg.edit_text("❌ Ошибка генерации (нейросеть недоступна). Очки/лимиты сохранены. Попробуй позже.")
            except Exception as e:
                logger.debug("Failed to update unavailable image status message: %s", e)
        
        await asyncio.sleep(COOLDOWN_SECONDS)

@router.message(Command("image_probe"))
async def cmd_image_probe(message: types.Message):
    """Admin-only Render connectivity test; it does not touch bot accounting."""
    if (not message.from_user or message.from_user.id != ADMIN_ID or
            not message.chat or message.chat.type != 'private' or message.chat.id != ADMIN_ID):
        return
    if _probe_lock.locked():
        await message.answer("Проверка картинок уже выполняется, дождись результата.")
        return
    async with _probe_lock:
        mode = 'Cloudflare + OVHcloud' if os.getenv('IMAGE_PROVIDER_MODE', '').strip().lower() == 'cloudflare' else 'Pollinations (прежний)'
        await message.answer(
            f"Проверяю Cloudflare и OVHcloud с сервера бота. Текущий режим: {mode}. "
            "Тест использует квоты сервисов, но не списывает очки или попытки бота."
        )
        prompt = ('A fluffy cat astronaut drinking tea in a small cafe on Mars, '
                  'with a friendly robot waiter, detailed, warm lighting, no text.')
        for label, fetch in (
            ('Cloudflare FLUX.2 4B', image_providers.fetch_cloudflare),
            ('OVHcloud SDXL', image_providers.fetch_ovh),
        ):
            try:
                image = await fetch(prompt)
            except image_providers.ImageProviderError as error:
                # Only expose known, non-sensitive error classes and HTTP status.
                reason = str(error)
                detail = reason if re.fullmatch(r'HTTP [0-9]{3}', reason) else 'нет изображения или доступа'
                await message.answer(f"❌ {label}: {detail}.")
                continue
            except Exception as error:
                logger.warning('Image probe %s failed: %s', label, type(error).__name__)
                await message.answer(f"❌ {label}: ошибка проверки.")
                continue
            try:
                filename = 'probe.png' if image.startswith(b'\x89PNG') else 'probe.jpg'
                await message.answer_photo(
                    photo=BufferedInputFile(image, filename=filename),
                    caption=f"Проверка с сервера бота: {label}",
                )
            except Exception as error:
                logger.warning('Image probe Telegram delivery unconfirmed: %s', type(error).__name__)
                await message.answer(f"⚠️ {label}: результат получен, но отправка фото не подтверждена. Проверь чат.")
        await message.answer("Проверка завершена. Обычное рисование не переключалось.")


@router.message(Command("img", "pic", "рис"))
async def cmd_generate_image(message: types.Message, command: CommandObject):
    query = command.args
    if not query:
        return await message.reply("Напиши, что нарисовать. Пример: `/img кот самурай`", parse_mode="Markdown")
    await process_image_request(message, query)
