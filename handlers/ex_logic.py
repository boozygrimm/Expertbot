import re
import os
import random
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
from handlers.web_search import smart_web_search
from handlers.image_gen import process_image_request
from handlers.admin_panel import is_feature_enabled

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
    "• **Сплетни чата:** команда `/gossip`\n\n"
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
    except Exception:
        pass


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
        "Доступные INTENT: SEARCH, MUSIC, WEATHER, IMAGE, GOSSIP, CHAT.\n\n"
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
            if intent in ["MUSIC", "SEARCH", "WEATHER", "IMAGE", "GOSSIP"]:
                return intent, clean_query
    except Exception:
        pass

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
    except Exception:
        pass

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
            result = await smart_web_search(clean_query)
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
                "❌ Поиск сейчас не сработал. Возможно, недоступен поисковик или ИИ-обработка ответа."
            )
            asyncio.create_task(delete_after(msg, 15))
        return

    if intent == "GOSSIP":
        if not await is_feature_enabled(message.chat.id, "reports"):
            return
        await send_persona_selection(message, parse_gossip_count(clean_query))
        return

    if not await is_feature_enabled(message.chat.id, "assistant"):
        return

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
    except Exception:
        pass

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
