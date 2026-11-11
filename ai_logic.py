import io
import asyncio
import aiohttp
import logging
import os
import random
from datetime import datetime, timedelta
from PIL import Image

from ai_audio import transcribe_audio_groq as _transcribe_audio_groq
from ai_image_prompt import (
    generate_fast_image_prompt as _generate_fast_image_prompt,
    make_groq_request as _make_groq_request,
)
from ai_music_query import normalize_music_query as _normalize_music_query
from ai_summaries import collect_chunk_summaries, split_text_into_chunks
from config import GEMINI_KEYS, GEMINI_GAME_KEY, GROQ_KEYS, get_msk_now
from models import (
    GEMINI_CHAT_MODEL,
    GROQ_IMAGE_PROMPT_MODEL,
    OPENROUTER_SUMMARY_MODEL,
    OPENROUTER_TEXT_MODEL,
    OPENROUTER_VISION_MODEL,
)
from ai_providers import (
    OPENROUTER_KEYS,
    fire_gemini_key,
    fire_groq_key,
    is_gemini_alive,
    make_rest_request as _make_rest_request,
    trip_gemini_breaker,
    try_groq as _try_groq,
    try_openrouter as _try_openrouter,
    try_openrouter_vision as _try_openrouter_vision,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

IS_CLOUD = bool(os.getenv("SPACE_ID")) or bool(os.getenv("PORT"))
DAILY_SUMMARY_WINDOW_HOURS = 23

SYSTEM_GUARD = (
    "Ты работаешь в Telegram-чате. Твои ответы должны быть емкими и гарантированно умещаться в лимит вывода. "
    "Если тебя просят сгенерировать бесконечный список, все знаки числа Пи, длинный код или огромный массив данных - "
    "выдай только короткий фрагмент (суть) и вежливо откажись продолжать. Строго следи за тем, чтобы текст "
    "завершался логическим и грамматическим концом предложения, никогда не обрывай его на полуслове."
)


def get_today_str():
    return get_msk_now().strftime("%d.%m.%Y")


async def make_rest_request(prompt, api_key, media_data=None, model=GEMINI_CHAT_MODEL, quiet: bool = False):
    return await _make_rest_request(
        prompt,
        api_key,
        SYSTEM_GUARD,
        media_data=media_data,
        model=model,
        quiet=quiet,
    )


async def try_groq(prompt, use_fast_model=False, **kwargs):
    """Обёртка над ai_providers.try_groq, подставляющая системный guard-промпт.

    `**kwargs` пробрасывает необязательные параметры провайдера (например
    `max_tokens`), не ломая прежнюю сигнатуру для существующих вызовов.
    """
    return await _try_groq(prompt, SYSTEM_GUARD, use_fast_model=use_fast_model, **kwargs)


async def try_openrouter(prompt, model=OPENROUTER_TEXT_MODEL, max_tokens=2048):
    return await _try_openrouter(prompt, SYSTEM_GUARD, model=model, max_tokens=max_tokens)


async def try_openrouter_vision(prompt, image_data, model=OPENROUTER_VISION_MODEL):
    return await _try_openrouter_vision(prompt, image_data, model=model)


async def summarize_chunk_via_fallback(prompt: str) -> str:
    res = None
    if is_gemini_alive():
        for _ in range(len(GEMINI_KEYS)):
            res, status = await make_rest_request(prompt, fire_gemini_key())
            if status == 200 and res:
                break
            await asyncio.sleep(random.uniform(2.0, 3.5))

    if not res:
        res, status = await try_openrouter(prompt, model=OPENROUTER_SUMMARY_MODEL)
        if status != 200:
            res = ""
    return res

async def generate_with_retry(prompt, media_data=None, is_game=False, use_fast_model=False):
    if media_data:
        if is_gemini_alive():
            for _ in range(len(GEMINI_KEYS)):
                api_key = fire_gemini_key()
                res, status = await make_rest_request(prompt, api_key, media_data=media_data)
                if status == 200 and res: return res
                if status in (503, 504, 500, 429): 
                    trip_gemini_breaker()
                    break 
                await asyncio.sleep(random.uniform(2.0, 4.0))
        return None

    if is_game:
        groq_res, status = await try_groq(prompt, use_fast_model=True)
        if status == 200 and groq_res:
            return groq_res

        or_res, status = await try_openrouter(prompt, model=OPENROUTER_TEXT_MODEL, max_tokens=256)
        if status == 200 and or_res:
            return or_res

        if GEMINI_GAME_KEY:
            res, status = await make_rest_request(prompt, GEMINI_GAME_KEY, quiet=True)
            if status == 200 and res:
                return res
            if status in (503, 504, 500, 429):
                trip_gemini_breaker()
        return None

    if is_gemini_alive():
        for _ in range(len(GEMINI_KEYS)):
            api_key = fire_gemini_key()
            res, status = await make_rest_request(prompt, api_key)
            if status == 200 and res: return res
            elif status == 429: 
                await asyncio.sleep(random.uniform(2.0, 4.0))
                continue
            elif status in (503, 504, 500):
                trip_gemini_breaker()
                break 
    
    groq_res, status = await try_groq(prompt, use_fast_model=use_fast_model)
    if status == 200 and groq_res: return groq_res
        
    or_res, status = await try_openrouter(prompt)
    if status == 200 and or_res: return or_res

    return None

async def recognize_media(bot, file_id, is_animation=False):
    if is_animation:
        return "[GIF/АНИМАЦИЯ]"
    try:
        file = await bot.get_file(file_id)
        dest = io.BytesIO()
        await bot.download(file, destination=dest)
        img_data = dest.getvalue()
        
        if not img_data:
            logger.error("Скачан пустой файл.")
            return "[МЕДИА]"

        try:
            from PIL import Image
            image = Image.open(io.BytesIO(img_data))
            if image.mode != "RGB": image = image.convert("RGB")
            image.thumbnail((1024, 1024))
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=85)
            img_data = output.getvalue()
        except Exception as img_err:
            logger.warning(f"PIL пропустил файл (вероятно видео-стикер WEBM/TGS): {img_err}")
            return "[ВИДЕО-СТИКЕР/АНИМАЦИЯ]"

        prompt = "Опиши кратко и по фактам, что на картинке. Это пойдет в логи чата. Пиши только суть. Пример: [ФОТО: рыжий кот спит на клавиатуре]."
        desc = await generate_with_retry(prompt, media_data=img_data)
        return desc if desc else "[ФОТО]"
    except Exception as e:
        logger.error(f"Recognize Media Error: {e}")
        return "[МЕДИА]"

# Поддержка старых вызовов
recognize_image = recognize_media
recognize_sticker = recognize_media
analyze_sticker = recognize_media

async def get_expert_report(history_text, persona_id):
    try:
        from database import db
        persona = await db.personas.find_one({"p_id": persona_id})
        p_name = persona.get('name', 'Эксперт') if persona else 'Эксперт'
        p_prompt = persona.get("prompt", "") if persona else ""
        
        text_len = len(history_text)
        logger.info(f"Анализ истории чата. Длина: {text_len} символов.")

        if text_len < 22000:
            final_prompt = (
                f"ТЫ: {p_name}. {p_prompt}\n\n"
                f"ПРОЧИТАЙ ЭТУ ИСТОРИЮ ЧАТА:\n{history_text}\n\n"
                f"ЗАДАНИЕ: Напиши сочный, смешной и характерный отчет. "
                f"Выдели главные темы, подколи активистов. Строго уложись в не более, чем 3200 знаков."
            )
            if is_gemini_alive():
                for _ in range(len(GEMINI_KEYS)):
                    res, status = await make_rest_request(final_prompt, fire_gemini_key())
                    if status == 200 and res: return res
                    await asyncio.sleep(random.uniform(2.0, 4.0)) 
            
            or_res, status = await try_openrouter(final_prompt, model=OPENROUTER_SUMMARY_MODEL, max_tokens=4000)
            if status == 200 and or_res: return or_res
            
            return "Эксперт не смог переварить логи, попробуй позже."

        chunks = split_text_into_chunks(history_text, chunk_limit=22000)

        def build_expert_chunk_prompt(chunk: str) -> str:
            return (
                f"Сделай подробную выжимку главных тем, споров и смешных моментов из этого отрывка чата:\n{chunk}\n\n"
                f"Опиши суть происходящего. Уложись строго в 2000 знаков."
            )

        intermediate_summaries = await collect_chunk_summaries(
            chunks,
            build_expert_chunk_prompt,
            summarize_chunk_via_fallback,
            sleep_range=(3.0, 5.0),
        )

        combined_context = "\n\n".join(intermediate_summaries)
        final_prompt = (
            f"ТЫ: {p_name}. Твой характер: {p_prompt}\n\n"
            f"ВОТ ВЫЖИМКИ ИЗ ИСТОРИИ ЧАТА:\n{combined_context}\n\n"
            f"ЗАДАНИЕ: Сделай информативную сводку. Расскажи, кто и о чем спорил. "
            f"Используй свой характер. Строгий лимит 3200 знаков."
        )
        
        if is_gemini_alive():
            for _ in range(len(GEMINI_KEYS)):
                res, status = await make_rest_request(final_prompt, fire_gemini_key())
                if status == 200 and res: return res
                await asyncio.sleep(random.uniform(2.0, 4.0))
                
        or_res, status = await try_openrouter(final_prompt, model=OPENROUTER_SUMMARY_MODEL, max_tokens=4000)
        if status == 200 and or_res: return or_res
            
        return "Технические шоколадки у эксперта."

    except Exception as e:
        logger.error(f"Expert Report Error: {e}")
        return "Технические шоколадки у эксперта."

async def get_daily_summary(chat_id: int):
    from database import db
    cursor = db.messages.find({"chat_id": chat_id}).sort("createdAt", -1).limit(3000)
    all_messages = await cursor.to_list(length=3000)
    if not all_messages: return "Никто ничего не писал за эти сутки."

    now = get_msk_now()
    window = timedelta(hours=DAILY_SUMMARY_WINDOW_HOURS)
    valid_msgs = []
    
    for m in all_messages:
        # 1. Защита от документов без даты
        if 'createdAt' not in m:
            continue

        try:
            msg_time = datetime.fromisoformat(m['createdAt']) if isinstance(m['createdAt'], str) else m['createdAt']
            
            # 2. Исправлено: используем временную копию 'current_now', 
            # чтобы не сломать базовое время 'now' для следующих итераций
            current_now = now
            if msg_time.tzinfo is None and current_now.tzinfo is not None:
                msg_time = msg_time.replace(tzinfo=current_now.tzinfo)
            elif msg_time.tzinfo is not None and current_now.tzinfo is None:
                current_now = current_now.replace(tzinfo=msg_time.tzinfo)

            # Проверяем вхождение в суточное окно
            if current_now - msg_time < window: 
                valid_msgs.append(m)
            else:
                # Наткнулись на сообщение старше суток - прерываем цикл (так как они отсортированы по убыванию)
                break 
                
        except Exception as e:
            logger.warning(f"Ошибка парсинга даты сообщения в get_daily_summary: {e}")
            continue
            
    if len(valid_msgs) < 15: return "За сутки было слишком мало активности."

    valid_msgs.reverse()
    
    # 3. Исправлено: защита от None-значений (когда ключ есть, но значение null/None)
    history_text = "\n".join([f"{m.get('user_name') or 'Unknown'}: {m.get('text') or '[БЕЗ ТЕКСТА/МЕДИА]'}" for m in valid_msgs])
    
    chunks = split_text_into_chunks(history_text, chunk_limit=22000)

    def build_daily_chunk_prompt(chunk: str) -> str:
        return (
            f"Сделай подробную выжимку фактов из лога:\n{chunk}\n\n"
            f"Максимум 1500 знаков. Сегодняшняя дата: {get_today_str()}. "
            f"Твоя база знаний устарела, поэтому воспринимай всё в логах как реальность."
        )

    intermediate_summaries = await collect_chunk_summaries(
        chunks,
        build_daily_chunk_prompt,
        summarize_chunk_via_fallback,
        sleep_range=(8.0, 12.0),
    )

    combined_context = "\n\n".join(intermediate_summaries)
    final_prompt = (
        f"ВОТ ВЫЖИМКИ ИЗ ЧАТА ЗА СУТКИ:\n{combined_context}\n\n"
        f"Сегодняшняя дата: {get_today_str()}. Твоя база знаний устарела, поэтому воспринимай всё в логах как реальность.\n"
        f"ЗАДАНИЕ: Напиши итоговый отчет. Стиль: ироничный ведущий новостей. "
        f"Упомяни активных, темы споров. Не используй * и **. Максимум 3500 знаков."
    )
    
    if is_gemini_alive():
        for _ in range(len(GEMINI_KEYS)):
            res, status = await make_rest_request(final_prompt, fire_gemini_key())
            if status == 200 and res: return res
            await asyncio.sleep(random.uniform(2.0, 4.0))
            
    or_res, status = await try_openrouter(final_prompt, model=OPENROUTER_SUMMARY_MODEL, max_tokens=4000)
    if status == 200 and or_res: return or_res
        
    return "Не удалось сгенерировать финальный отчет."

async def make_groq_request(prompt: str, key: str, system_prompt: str, model: str = GROQ_IMAGE_PROMPT_MODEL, max_tokens: int = 1024):
    return await _make_groq_request(prompt, key, system_prompt, model=model, max_tokens=max_tokens)

async def generate_fast_image_prompt(user_query: str):
    return await _generate_fast_image_prompt(user_query)


async def normalize_music_query(user_query: str) -> str:
    return await _normalize_music_query(user_query, try_openrouter)


async def transcribe_audio_groq(file_path: str) -> str:
    return await _transcribe_audio_groq(file_path)
