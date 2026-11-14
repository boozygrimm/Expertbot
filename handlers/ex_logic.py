import re
import os
import random
import time
import asyncio
import logging
import urllib.parse

import aiohttp
from aiogram import Router, types, F

from database import db
from ai_logic import generate_with_retry, get_daily_summary, try_groq
from config import get_msk_now

from handlers.viselitsa import start_hangman
from handlers.anachronism import start_anachronism
from handlers.pole_chudes import start_game
from handlers.reports import parse_gossip_count, send_persona_selection
from handlers.music import handle_music_request
from handlers.web_search import smart_web_search, smart_web_search_with_image, deliver_search_result
from handlers.image_gen import process_image_request
from handlers.admin_panel import is_feature_enabled
from handlers.response_choice import offer_response_choice

router = Router()
logger = logging.getLogger(__name__)
CHAT_MUSIC_FALSE_POSITIVES = (
    "подпевай",
    "подпой",
    "спой",
    "спойка",
    "напой",
    "напевай",
)

# Таймауты для шпоры: {user_id: timestamp}
spora_cooldowns = {}

# Вероятность использования кэша (80%)
SPORA_CACHE_HIT_RATE = 0.8


def normalize_recipe_key(ing1: str, ing2: str, res: str) -> str:
    """Создает нормализованный ключ рецепта для кэша."""
    def normalize(s: str) -> str:
        return " ".join(s.strip().lower().split())
    return f"{normalize(ing1)}|{normalize(ing2)}|{normalize(res)}"


async def get_cached_spora_response(recipe_key: str) -> str | None:
    """Получает кэшированный ответ для рецепта."""
    cached = await db.spora_cache.find_one({"recipe_key": recipe_key})
    if cached:
        # Обновляем счетчик использования
        await db.spora_cache.update_one(
            {"_id": cached["_id"]},
            {"$inc": {"used_count": 1}}
        )
        return cached.get("response")
    return None


async def save_spora_response(recipe_key: str, response: str, persona_id: str = "bender"):
    """Сохраняет ответ в кэш."""
    await db.spora_cache.update_one(
        {"recipe_key": recipe_key},
        {
            "$set": {
                "response": response,
                "persona_id": persona_id,
                "updated_at": get_msk_now()
            },
            "$setOnInsert": {
                "created_at": get_msk_now(),
                "used_count": 0
            }
        },
        upsert=True
    )

HELP_TEXT = (
    "🤖 **Экс: краткая справка**\n\n"
    "Как обратиться:\n"
    "• Напиши в начале сообщения **«Экс»**, **«Экс,»** или **«Эксперт»**\n"
    "• Или просто ответь на любое сообщение бота\n\n"
    "Что умею:\n"
    "• **Поиск и веб:** *«Экс, найди инфу про Пифагора»*, *«Экс, что нового в Python»*\n"
    "• **Погода:** *«Экс, погода в Нск»*, *«Экс, прогноз Питер»*\n"
    "• **Музыка:** *«Экс, найди песню Rammstein»*, *«Экс, включи Time in a Bottle»*\n"
    "• **Картинки:** *«Экс, нарисуй кота-джедая»* или команда `/img`\n"
    "• **Голосовые:** команда `/voice`, затем ответом на голосовое или кружок\n"
    "• **Игры:** *«Экс, крути барабан»*, *«Экс, палач»*, *«Экс, ставка 1000»*\n"
    "• **База алхимика:** команда `/base`\n"
    "• **Сплетни чата:** команда `/gossip`\n"
    "• **Шпора по алхимии:** *«Экс, шпора»*\n\n"
    "Полезные команды:\n"
    "• `/help` — эта справка\n"
    "• `/support` — связь с владельцем\n"
    "• `/panel` — панель модулей чата\n"
    "• `/base` — Главный Штаб алхимии\n\n"
    "В личке бот показывает часть команд через Telegram-меню, а остальное доступно по тексту и slash-командам."
)


async def delete_after(msg: types.Message, delay: int = 120):
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception as e:
        logger.debug("Failed to delete delayed message: chat_id=%s message_id=%s error=%s", msg.chat.id, msg.message_id, e)


def find_alchemy_recipe(query: str) -> tuple[str, str, str] | None:
    """Ищет рецепт алхимии по названию результата. Возвращает (ингредиент1, ингредиент2, результат) или None."""
    from handlers.alc_recipes import BATTLE_UNITS, ELEMENTS, RECIPES

    def normalize_name(value: str) -> str:
        normalized = value.strip().lower().replace("ё", "е")
        return " ".join(normalized.split())

    def get_el_name(k):
        el = ELEMENTS.get(k, k)
        if isinstance(el, str):
            parts = el.split(" ", 1)
            return parts[1] if len(parts) > 1 else el
        return str(k)

    query_norm = normalize_name(query)

    for ingredients, result in RECIPES.items():
        # Пропускаем боевые юниты
        if result in BATTLE_UNITS:
            continue

        result_name = normalize_name(get_el_name(result))
        if query_norm in {result_name, normalize_name(result)}:
            ing1 = ELEMENTS.get(ingredients[0], ingredients[0])
            ing2 = ELEMENTS.get(ingredients[1], ingredients[1])
            res = ELEMENTS.get(result, result)
            return ing1, ing2, res
    return None


async def analyze_intent(query: str, context: str = "") -> tuple[str, str]:
    text_lower = query.lower().strip()

    if any(text_lower.startswith(trigger) for trigger in CHAT_MUSIC_FALSE_POSITIVES):
        return "CHAT", query

    if match := re.match(r"^(?:нарисуй|сгенерируй|изобрази)\s+(.+)", text_lower):
        return "IMAGE", match.group(1).strip()

    if match := re.match(r"^(?:погода|прогноз)\s+(.+)", text_lower):
        return "WEATHER", match.group(1).strip()

    if match := re.match(r"^(?:найди песню|включи|песня)\s+(.+)", text_lower):
        return "MUSIC", match.group(1).strip()

    if match := re.match(r"^(?:найди|загугли|поиск)\s+(.+)", text_lower):
        if not text_lower.startswith(("найди песню", "найди трек")):
            return "SEARCH", match.group(1).strip()

    if match := re.match(r"^(?:сплетни|вещай|выбор)(?:\s+(\d+))?$", text_lower):
        count = match.group(1) or "50"
        return "GOSSIP", count

    if match := re.match(r"^шпора(?:\s+(.+))?$", text_lower):
        return "SPORA", (match.group(1) or "").strip()

    if "крути барабан" in text_lower:
        return "POLE", query
    if "палач" in text_lower or "виселица" in text_lower:
        return "VIS", query
    if text_lower.startswith("ставка"):
        match = re.search(r"\d+", text_lower)
        return "ANACH", match.group() if match else "1000"

    prompt = (
        "Ты — строгий классификатор намерений бота.\n"
        "Определи намерение пользователя. Ответь СТРОГО в формате: INTENT|CLEAN_QUERY\n"
        "Доступные INTENT: SEARCH, MUSIC, WEATHER, IMAGE, GOSSIP, SPORA, CHAT.\n\n"
        "ПРАВИЛА:\n"
        "1. Если запрос — это шутка, реакция, ругательство или обычный диалог "
        "(например: 'Не хами', 'Ути пути', 'Привет') — ВСЕГДА выбирай CHAT.\n"
        "1.1. Команды вроде 'подпевай', 'спой', 'напой', если в них нет явного названия песни, — это CHAT, а не MUSIC.\n"
        "2. Выбирай SEARCH, MUSIC, WEATHER, IMAGE и GOSSIP ТОЛЬКО если пользователь "
        "прямо просит выполнить это действие.\n"
        "3. Если сомневаешься — выбирай CHAT.\n\n"
    )

    if context:
        prompt += f"Контекст (на что отвечает пользователь): «{context}»\n"

    prompt += f"Запрос пользователя: «{query}»"

    try:
        res_text, status = await asyncio.wait_for(
            try_groq(prompt, use_fast_model=True),
            timeout=2.0,
        )
        if status == 200 and res_text and "|" in res_text:
            intent, clean_query = res_text.split("|", 1)
            intent = intent.strip().upper()
            clean_query = clean_query.strip()
            if intent in ["MUSIC", "SEARCH", "WEATHER", "IMAGE", "GOSSIP", "SPORA"]:
                return intent, clean_query
    except Exception as e:
        logger.debug("Intent analysis fallback to CHAT: query=%s error=%s", query, e)

    return "CHAT", query


async def generate_persona_opinion(
    message: types.Message,
    target_persona: dict,
    user_query: str,
    source_text: str,
):
    loading_data = target_persona.get("loading", ["Беру интервью..."])
    loading_text = random.choice(loading_data) if isinstance(loading_data, list) else loading_data
    status_msg = await message.reply(loading_text)

    prompt = (
        f"ТЫ: {target_persona['name']}. {target_persona.get('prompt', '')}\n\n"
        f"КОНТЕКСТ (СООБЩЕНИЕ, КОТОРОЕ ТЕБЕ ПОКАЗАЛИ):\n«{source_text}»\n\n"
        f"ВОПРОС ПОЛЬЗОВАТЕЛЯ: {user_query}\n\n"
        "ЗАДАНИЕ: Внимательно проанализируй контекст и выдай подробный, развернутый "
        "комментарий от своего лица. Лимит: 1500 знаков."
    )

    response = await generate_with_retry(prompt)
    if not response:
        try:
            res_text, status = await asyncio.wait_for(
                try_groq(prompt, use_fast_model=False),
                timeout=10.0,
            )
            if status == 200 and res_text:
                response = res_text
        except Exception as e:
            logger.warning(
                "Persona opinion fallback failed: chat_id=%s user_id=%s persona=%s error=%s",
                message.chat.id,
                message.from_user.id if message.from_user else "unknown",
                target_persona.get("p_id"),
                e,
            )

    try:
        await status_msg.delete()
    except Exception as e:
        logger.debug("Failed to delete persona status message: chat_id=%s message_id=%s error=%s", message.chat.id, status_msg.message_id, e)

    if response:
        try:
            sent = await message.reply(
                f"{target_persona['emoji']} **{target_persona['name']}**: {response}",
                parse_mode="Markdown",
            )
        except Exception:
            sent = await message.reply(f"{target_persona['emoji']} {target_persona['name']}: {response}")

        try:
            await db.messages.insert_one({
                "message_id": sent.message_id,
                "chat_id": message.chat.id,
                "user_name": f"🤖 {target_persona['name']}",
                "text": response,
                "reactions": [],
                "createdAt": get_msk_now(),
            })
        except Exception as e:
            logger.error(
                "DB log error in persona opinion: chat_id=%s user_id=%s persona=%s error=%s",
                message.chat.id,
                message.from_user.id if message.from_user else "unknown",
                target_persona.get("p_id"),
                e,
            )
    else:
        await message.reply(
            "⚠️ Не смог собрать мнение персонажа: модель сейчас не ответила. "
            "Попробуй повторить через минуту."
        )


def extract_persona_from_text(personas: list[dict], text: str | None):
    if not text:
        return None

    text_lower = text.lower()
    for persona in personas:
        if persona["name"].lower() in text_lower or persona["p_id"].lower() in text_lower:
            return persona
    return None


def get_reply_source_text(message: types.Message) -> str:
    if not message.reply_to_message:
        return ""
    return (message.reply_to_message.text or message.reply_to_message.caption or "").strip()


@router.message(F.text.lower() == "/vest")
async def hidden_daily_summary_command(message: types.Message):
    if message.chat.type not in ["group", "supergroup"]:
        return

    member = await message.bot.get_chat_member(message.chat.id, message.from_user.id)
    if "creator" not in str(member.status) and "administrator" not in str(member.status):
        return

    status_msg = await message.reply("⏳ <i>Изучаю архивы...</i>", parse_mode="HTML")
    report = await get_daily_summary(message.chat.id)
    try:
        if report:
            await status_msg.edit_text(report, parse_mode="Markdown")
        else:
            await status_msg.edit_text("❌ Нет данных для отчета.")
    except Exception:
        await status_msg.edit_text(str(report))


@router.message()
async def handle_ex_request(message: types.Message):
    if not message.text:
        return

    text = message.text
    is_ex_triggered = False
    query = ""

    match = re.match(r"(?i)^экс(?:перт)?[\s,]*(.*)", text)
    if match:
        is_ex_triggered = True
        query = match.group(1).strip()
    elif (
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == message.bot.id
    ):
        is_ex_triggered = True
        query = text.strip()

    if not is_ex_triggered:
        return

    if not query:
        query = "Продолжи диалог."

    if message.reply_to_message and (message.reply_to_message.voice or message.reply_to_message.video_note):
        triggers = ["что там", "че там", "расшифруй", "голос", "гс", "слушай"]
        if any(tr in query.lower() for tr in triggers) or query == "Продолжи диалог.":
            if not await is_feature_enabled(message.chat.id, "voice"):
                return

            from ai_logic import transcribe_audio_groq

            target_msg = message.reply_to_message
            file_id = target_msg.voice.file_id if target_msg.voice else target_msg.video_note.file_id
            file_ext = "ogg" if target_msg.voice else "mp4"
            file_path = f"temp_manual_{file_id}.{file_ext}"

            status_msg = await message.reply("⏳ <i>Расшифровываю...</i>", parse_mode="HTML")
            try:
                file = await message.bot.get_file(file_id)
                await message.bot.download_file(file.file_path, file_path)
                audio_text = await transcribe_audio_groq(file_path)

                if audio_text and "❌" not in audio_text:
                    await status_msg.edit_text(
                        f"🎙 <b>Там сказали:</b>\n{audio_text}",
                        parse_mode="HTML",
                    )
                else:
                    await status_msg.edit_text(
                        "⚠️ Расшифровка не удалась. Возможно, запись слишком шумная, короткая "
                        "или сервис распознавания временно недоступен."
                    )
            except Exception as e:
                logger.error(
                    "Voice manual transcription error: chat_id=%s user_id=%s file_id=%s error=%s",
                    message.chat.id,
                    message.from_user.id if message.from_user else "unknown",
                    file_id,
                    e,
                )
                await status_msg.edit_text(
                    "❌ Не смог расшифровать голосовое. Проверь, что это обычное голосовое или "
                    "кружок, и попробуй еще раз позже."
                )
            finally:
                if os.path.exists(file_path):
                    os.remove(file_path)
            return

    if query.lower() in ["хэлп", "help", "помоги", "справка"]:
        return await message.reply(HELP_TEXT, parse_mode="Markdown")

    all_personas = await db.personas.find().to_list(length=100)

    reply_source_text = get_reply_source_text(message)

    if reply_source_text:
        opinion_triggers = ["думает", "считает", "мнение", "скажет", "мысли"]
        if any(trigger in query.lower() for trigger in opinion_triggers):
            target_persona = extract_persona_from_text(all_personas, query)

            if not target_persona and message.reply_to_message and message.reply_to_message.from_user:
                if message.reply_to_message.from_user.id == message.bot.id:
                    target_persona = extract_persona_from_text(all_personas, reply_source_text)

            if not target_persona and len(all_personas) == 1:
                target_persona = all_personas[0]

            if target_persona:
                if target_persona.get('p_id') == 'bender' and await offer_response_choice(
                        message, query, reply_source_text, kind='opinion', persona_id='bender'):
                    return
                await generate_persona_opinion(
                    message,
                    target_persona,
                    query,
                    reply_source_text,
                )
                return
            logger.info(
                "Persona opinion skipped: chat_id=%s user_id=%s query=%s reason=no_persona_match",
                message.chat.id,
                message.from_user.id if message.from_user else "unknown",
                query,
            )

    reply_context = ""
    if reply_source_text:
        reply_context = reply_source_text

    intent, clean_query = await analyze_intent(query, context=reply_context)
    logger.info("LLM Router -> chat_id=%s intent=%s query=%s", message.chat.id, intent, clean_query)

    if intent == "IMAGE":
        if not await is_feature_enabled(message.chat.id, "images"):
            return
        if not clean_query:
            return await message.reply("Что рисуем? Напиши запрос чуть точнее.")
        await process_image_request(message, clean_query)
        return

    if intent == "WEATHER":
        if not await is_feature_enabled(message.chat.id, "assistant"):
            return
        if not clean_query or clean_query.lower() in ["погода", "прогноз"]:
            return await message.reply("Напиши город, например: `Экс, погода Новосибирск`.", parse_mode="Markdown")

        status_msg = await message.reply(f"☁️ Запрашиваю погоду для: «{clean_query}»...")
        try:
            safe_city = urllib.parse.quote(clean_query)
            weather_url = f"https://wttr.in/{safe_city}?m&M&T&0&lang=ru"

            async with aiohttp.ClientSession() as session:
                async with session.get(weather_url, timeout=10) as w_resp:
                    if w_resp.status == 200:
                        forecast = await w_resp.text()
                        if "<html" in forecast.lower():
                            await status_msg.edit_text(
                                "⚠️ Сервис погоды ответил некорректно. Такое бывает при перегрузке. "
                                "Попробуй чуть позже."
                            )
                        else:
                            safe_forecast = forecast.replace("`", "'").strip()
                            await status_msg.edit_text(f"```text\n{safe_forecast}\n```", parse_mode="Markdown")
                    else:
                        logger.warning(
                            "Weather service bad status: chat_id=%s user_id=%s city=%s status=%s",
                            message.chat.id,
                            message.from_user.id if message.from_user else "unknown",
                            clean_query,
                            w_resp.status,
                        )
                        await status_msg.edit_text(
                            "❌ Сервис погоды не ответил как положено. Попробуй позже."
                        )
        except Exception as e:
            logger.error(
                "Weather error: chat_id=%s user_id=%s city=%s error=%s",
                message.chat.id,
                message.from_user.id if message.from_user else "unknown",
                clean_query,
                e,
            )
            await status_msg.edit_text(
                "❌ Не смог получить погоду. Возможно, проблема с сетью или внешний сервис "
                "временно недоступен."
            )
        return

    if intent == "POLE":
        if not await is_feature_enabled(message.chat.id, "games"):
            return
        await message.reply("🎡 Крутите барабан!")
        await start_game(message)
        return

    if intent == "VIS":
        if not await is_feature_enabled(message.chat.id, "games"):
            return
        await message.reply("⚖️ Готовьте мыло и веревку...")
        await start_hangman(message)
        return

    if intent == "ANACH":
        if not await is_feature_enabled(message.chat.id, "games"):
            return
        bet_val = clean_query if clean_query.isdigit() else "1000"
        modified_message = message.model_copy(update={"text": f"/anachronism {bet_val}"})
        await start_anachronism(modified_message)
        return

    if intent == "MUSIC":
        if not await is_feature_enabled(message.chat.id, "music"):
            return
        try:
            await handle_music_request(message, clean_query)
        except Exception as e:
            logger.error(
                "Music error: chat_id=%s user_id=%s query=%s error=%s",
                message.chat.id,
                message.from_user.id if message.from_user else "unknown",
                clean_query,
                e,
            )
            msg = await message.reply(
                "❌ Не смог подобрать или отправить трек. Возможно, источник временно недоступен."
            )
            asyncio.create_task(delete_after(msg, 15))
        return

    if intent == "SEARCH":
        if not await is_feature_enabled(message.chat.id, "assistant"):
            return

        status_msg = await message.reply(f"🌐 Ищу информацию по запросу: «{clean_query}»...")
        try:
            image = None
            if os.getenv('SEARCH_IMAGES', '').strip().lower() == 'enabled':
                result, image = await smart_web_search_with_image(clean_query)
            else:
                result = await smart_web_search(clean_query)
            if image and '❌' not in result:
                await deliver_search_result(message, status_msg, result, image)
            else:
                msg = await status_msg.edit_text(result)
                if "❌" in result:
                    asyncio.create_task(delete_after(msg, 15))
        except Exception as e:
            logger.error(
                "Search error: chat_id=%s user_id=%s query=%s error=%s",
                message.chat.id,
                message.from_user.id if message.from_user else "unknown",
                clean_query,
                e,
            )
            msg = await status_msg.edit_text(
                "🤖 Бендер: ❌ Поиск сейчас не сработал. Возможно, недоступен поисковик "
                "или ИИ-обработка ответа. Попробуй позже, кожаный мешок!"
            )
            asyncio.create_task(delete_after(msg, 15))
        return

    if intent == "GOSSIP":
        if not await is_feature_enabled(message.chat.id, "reports"):
            return
        await send_persona_selection(message, parse_gossip_count(clean_query))
        return

    if intent == "SPORA":
        user_id = message.from_user.id
        now = int(time.time())

        # Проверка таймаута (5 минут) - только для результативных запросов
        if user_id in spora_cooldowns:
            elapsed = now - spora_cooldowns[user_id]
            if elapsed < 300:
                remaining = 300 - elapsed
                minutes = remaining // 60
                seconds = remaining % 60
                return await message.reply(f"⏳ Подожди ещё {minutes} мин {seconds} сек перед следующим запросом.")

        # Пустой запрос - подсказка
        if not clean_query:
            return await message.reply("🤖 Бендер: Эй, кожаный мешок! Укажи, что именно хочешь создать. Например: **Экс, шпора спирт**", parse_mode="Markdown")

        match = find_alchemy_recipe(clean_query)
        if not match:
            return await message.reply("🤖 Бендер: Даже я не знаю, как это сделать. Попробуй другое название.")

        ing1, ing2, res = match
        recipe_key = normalize_recipe_key(ing1, ing2, res)

        # Получаем личность Бендера
        persona = await db.personas.find_one({"p_id": "bender"})
        if not persona:
            spora_cooldowns[user_id] = now
            text = f"📖 **Шпора**\n\n{res} = {ing1} + {ing2}"
            return await message.reply(text, parse_mode="Markdown")

        # Проверяем кэш с вероятностью 80%
        use_cache = random.random() < SPORA_CACHE_HIT_RATE
        cached_response = None

        if use_cache:
            cached_response = await get_cached_spora_response(recipe_key)
            if cached_response:
                spora_cooldowns[user_id] = now
                try:
                    await message.reply(
                        f"{persona['emoji']} **{persona['name']}**: {cached_response}\n\n📖 {res} = {ing1} + {ing2}",
                        parse_mode="Markdown",
                    )
                except Exception:
                    await message.reply(f"{persona['emoji']} {persona['name']}: {cached_response}\n\n📖 {res} = {ing1} + {ing2}")
                return

        # Генерируем новый ответ
        loading_data = persona.get("loading", ["Думаю..."])
        loading_text = random.choice(loading_data) if isinstance(loading_data, list) else loading_data
        status_msg = await message.reply(loading_text)

        try:
            prompt = (
                f"ТЫ: {persona['name']}. {persona.get('prompt', '')}\n\n"
                f"ЗАДАНИЕ: Пользователь хочет узнать рецепт алхимии. "
                f"Результат: {res} (ингредиенты: {ing1} + {ing2}). "
                f"Напиши короткий, смешной ответ в своем стиле, который объяснит рецепт. "
                f"Максимум 200 знаков."
            )

            response = await generate_with_retry(prompt)
        except Exception as e:
            logger.warning(f"Spora AI error: {e}")
            response = None

        try:
            await status_msg.delete()
        except Exception:
            pass

        # Устанавливаем таймаут только при успешном поиске рецепта
        spora_cooldowns[user_id] = now

        if response:
            # Сохраняем в кэш
            await save_spora_response(recipe_key, response, persona.get("p_id", "bender"))

            try:
                await message.reply(
                    f"{persona['emoji']} **{persona['name']}**: {response}\n\n📖 {res} = {ing1} + {ing2}",
                    parse_mode="Markdown",
                )
            except Exception:
                await message.reply(f"{persona['emoji']} {persona['name']}: {response}\n\n📖 {res} = {ing1} + {ing2}")
        else:
            await message.reply(f"📖 **Шпора**\n\n{res} = {ing1} + {ing2}", parse_mode="Markdown")
        return

    if not await is_feature_enabled(message.chat.id, "assistant"):
        return

    if await offer_response_choice(message, clean_query, reply_source_text):
        return
    return await generate_chat_response(message, clean_query, reply_source_text, all_personas)


async def generate_chat_response(message, clean_query, reply_source_text, all_personas):
    """Existing text response path, reused after a format selection."""
    if not all_personas:
        return await message.reply("В редакции сейчас нет ни одной личности. Проверь инициализацию personas.")

    persona = random.choice(all_personas)
    loading_data = persona.get("loading", ["Мыслительный процесс..."])
    loading_text = random.choice(loading_data) if isinstance(loading_data, list) else loading_data
    status_msg = await message.reply(loading_text)

    context_block = ""
    if reply_source_text:
        context_block = (
            "КОНТЕКСТ ДИАЛОГА (СООБЩЕНИЕ, НА КОТОРОЕ ТЫ ОТВЕЧАЕШЬ):\n"
            f"«{reply_source_text}»\n\n"
        )

    full_prompt = (
        "Ты должен строго отыгрывать следующую роль. НИКОГДА не выходи из образа.\n"
        f"РОЛЬ: {persona.get('prompt', 'Будь собой')}\n\n"
        f"{context_block}"
        f"ВОПРОС ПОЛЬЗОВАТЕЛЯ: {clean_query}\n\n"
        "ЗАДАНИЕ: Отвечай развернуто, учитывая детали контекста диалога "
        "(если он предоставлен). Не отделывайся односложными фразами."
    )

    response = await generate_with_retry(full_prompt)

    if not response:
        try:
            res_text, status = await asyncio.wait_for(
                try_groq(full_prompt, use_fast_model=False),
                timeout=10.0,
            )
            if status == 200 and res_text:
                response = res_text
        except Exception as e:
            logger.warning(
                "Chat fallback failed: chat_id=%s user_id=%s persona=%s error=%s",
                message.chat.id,
                message.from_user.id if message.from_user else "unknown",
                persona.get("p_id"),
                e,
            )

    try:
        await status_msg.delete()
    except Exception as e:
        logger.debug("Failed to delete chat status message: chat_id=%s message_id=%s error=%s", message.chat.id, status_msg.message_id, e)

    if response:
        try:
            sent = await message.reply(
                f"{persona['emoji']} **{persona['name']}**: {response}",
                parse_mode="Markdown",
            )
        except Exception:
            sent = await message.reply(f"{persona['emoji']} {persona['name']}: {response}")

        try:
            await db.messages.insert_one({
                "message_id": sent.message_id,
                "chat_id": message.chat.id,
                "user_name": f"🤖 {persona['name']}",
                "text": response,
                "reactions": [],
                "createdAt": get_msk_now(),
            })
        except Exception as e:
            logger.error(
                "DB log error: chat_id=%s user_id=%s persona=%s error=%s",
                message.chat.id,
                message.from_user.id if message.from_user else "unknown",
                persona.get("p_id"),
                e,
            )
    else:
        await message.reply(
            "🤖 Я сейчас не смог ответить: внешние модели перегружены или временно недоступны. "
            "Попробуй еще раз через пару минут."
        )
