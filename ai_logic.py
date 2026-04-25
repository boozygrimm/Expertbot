import io
import json
import base64
import asyncio
import aiohttp
import logging
import os
import random
import time
from datetime import datetime, timedelta
from PIL import Image

from config import GEMINI_KEYS, GEMINI_GAME_KEY, GROQ_KEYS

raw_openrouter = os.getenv("OPENROUTER_KEYS", "")
OPENROUTER_KEYS = [k.strip() for k in raw_openrouter.split(",") if k.strip()]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

IS_CLOUD = bool(os.getenv("SPACE_ID")) or bool(os.getenv("PORT"))

_groq_pointer = 0
_gemini_pointer = 0
_openrouter_pointer = 0

# --- СИСТЕМА ПРЕДОХРАНИТЕЛЯ (CIRCUIT BREAKER) ---
GEMINI_FAILS = 0
GEMINI_COOLDOWN_UNTIL = 0

def is_gemini_alive():
    global GEMINI_COOLDOWN_UNTIL, GEMINI_FAILS
    if time.time() > GEMINI_COOLDOWN_UNTIL:
        GEMINI_FAILS = 0
        return True
    return False

def trip_gemini_breaker():
    global GEMINI_COOLDOWN_UNTIL, GEMINI_FAILS
    GEMINI_FAILS += 1
    if GEMINI_FAILS >= 2: 
        GEMINI_COOLDOWN_UNTIL = time.time() + 60 
        logger.error("🚨 ПРЕДОХРАНИТЕЛЬ СРАБОТАЛ: Сервера Gemini лежат. Блокировка на 60 сек.")

SYSTEM_GUARD = (
    "Ты работаешь в Telegram-чате. Твои ответы должны быть емкими и гарантированно умещаться в лимит вывода. "
    "Если тебя просят сгенерировать бесконечный список, все знаки числа Пи, длинный код или огромный массив данных - "
    "выдай только короткий фрагмент (суть) и вежливо откажись продолжать. Строго следи за тем, чтобы текст "
    "завершался логическим и грамматическим концом предложения, никогда не обрывай его на полуслове."
)

def fire_groq_key():
    global _groq_pointer
    if not GROQ_KEYS: return None
    key = GROQ_KEYS[_groq_pointer].strip()
    _groq_pointer = (_groq_pointer + 1) % len(GROQ_KEYS)
    return key

def fire_gemini_key():
    global _gemini_pointer
    if not GEMINI_KEYS: return None
    key = GEMINI_KEYS[_gemini_pointer].strip()
    _gemini_pointer = (_gemini_pointer + 1) % len(GEMINI_KEYS)
    return key

def fire_openrouter_key():
    global _openrouter_pointer
    if not OPENROUTER_KEYS: return None
    key = OPENROUTER_KEYS[_openrouter_pointer].strip()
    _openrouter_pointer = (_openrouter_pointer + 1) % len(OPENROUTER_KEYS)
    return key

def detect_mime(data):
    if data.startswith(b'\xff\xd8'): return 'image/jpeg'
    if data.startswith(b'\x89PNG'): return 'image/png'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP': return 'image/webp'
    return 'image/jpeg'


def get_today_str():
    return datetime.now().strftime("%d.%m.%Y")

def log_gemini_status(status: int, err_text: str, quiet: bool = False):
    if status in (429, 503):
        log_fn = logger.debug if quiet else logger.warning
        log_fn(f"Gemini soft error {status}: {err_text}")
    else:
        logger.error(f"Gemini Error {status}: {err_text}")


async def make_rest_request(prompt, api_key, media_data=None, model="gemini-3-flash-preview", quiet: bool = False):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    parts = [{"text": prompt}]
    if media_data:
        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(media_data).decode('utf-8')
            }
        })
    
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_GUARD}]},
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": 0.8, "maxOutputTokens": 4096}
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=60) as response:
                status = response.status
                if status != 200:
                    err_text = await response.text()
                    log_gemini_status(status, err_text, quiet=quiet)
                    
                    # Если модель не найдена, выводим список в лог, чтобы не гадать
                    if status in (400, 404):
                        m_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
                        async with session.get(m_url) as m_resp:
                            if m_resp.status == 200:
                                m_data = await m_resp.json()
                                avail = [m['name'] for m in m_data.get('models', [])]
                                logger.info(f"Доступные модели: {avail}")
                    return None, status

                result = await response.json()
                text_out = result["candidates"][0]["content"]["parts"][0].get("text")
                return text_out, status
    except Exception as e:
        logger.error(f"Rest Request Critical Failure: {e}")
        return None, 500

async def try_groq(prompt, use_fast_model=False):
    if not GROQ_KEYS: return None, 404
        
    GROQ_MODELS = ["openai/gpt-oss-20b", "meta-llama/llama-4-scout-17b-16e-instruct"] if use_fast_model else ["llama-3.3-70b-versatile", "qwen/qwen3-32b"]        
    for model in GROQ_MODELS:
        for _ in range(len(GROQ_KEYS)):
            api_key = fire_groq_key()
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {
                "model": model,
                "messages": [{"role": "system", "content": SYSTEM_GUARD}, {"role": "user", "content": prompt}],
                "temperature": 0.6, "max_tokens": 4096
            }

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers=headers, json=payload, timeout=20) as response:
                        if response.status == 200:
                            result = await response.json()
                            return result["choices"][0]["message"]["content"], 200
                        elif response.status == 429:
                            await asyncio.sleep(random.uniform(2.0, 3.5))
                            continue
                        elif response.status >= 500:
                            return None, response.status
            except Exception:
                await asyncio.sleep(random.uniform(2.0, 3.5))
                continue 
    return None, 500

async def try_openrouter(prompt, model="google/gemma-3-27b-it:free", max_tokens=2048):
    if not OPENROUTER_KEYS: return None, 404
    
    for _ in range(len(OPENROUTER_KEYS)):
        api_key = fire_openrouter_key()
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}", 
            "Content-Type": "application/json",
            "HTTP-Referer": "https://bugbibot.tg",
            "X-Title": "BugbiBot"
        }
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": SYSTEM_GUARD}, {"role": "user", "content": prompt}],
            "temperature": 0.7, "max_tokens": max_tokens
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload, timeout=45) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result["choices"][0]["message"]["content"], 200
                    elif response.status == 429:
                        await asyncio.sleep(random.uniform(2.0, 3.5))
                        continue
                    elif response.status >= 500:
                        return None, response.status
        except Exception:
            await asyncio.sleep(random.uniform(2.0, 3.5))
            continue
    return None, 500

async def try_openrouter_vision(prompt, image_data, model="qwen/qwen3-vl-30b-thinking:free"):
    """Зрение через OpenRouter"""
    if not OPENROUTER_KEYS: return None, 404
    
    mime_type = "image/jpeg"
    base64_image = base64.b64encode(image_data).decode('utf-8')
    
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}}
                ]
            }
        ],
        "temperature": 0.5,
        "max_tokens": 512
    }

    url = "https://openrouter.ai/api/v1/chat/completions"
    
    for _ in range(len(OPENROUTER_KEYS)):
        headers = {
            "Authorization": f"Bearer {fire_openrouter_key()}",
            "Content-Type": "application/json"
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload, timeout=30) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data["choices"][0]["message"]["content"], 200
                    elif resp.status == 429:
                        await asyncio.sleep(random.uniform(2.0, 3.5))
                        continue
        except Exception:
            await asyncio.sleep(random.uniform(2.0, 3.5))
            continue
    return None, 500

async def generate_with_retry(prompt, media_data=None, is_game=False, use_fast_model=False):
    if media_data:
        if is_gemini_alive():
            for _ in range(len(GEMINI_KEYS)):
                api_key = fire_gemini_key()
                res, status = await make_rest_request(prompt, api_key, media_data)
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

        or_res, status = await try_openrouter(prompt, model="google/gemma-3-27b-it:free", max_tokens=256)
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
            
            or_res, status = await try_openrouter(final_prompt, model="qwen/qwen-2.5-72b-instruct:free", max_tokens=4000)
            if status == 200 and or_res: return or_res
            
            return "Эксперт не смог переварить логи, попробуй позже."

        # ВОССТАНОВЛЕННЫЙ ЧАНКИНГ
        CHUNK_LIMIT = 22000 
        lines = history_text.split('\n')
        chunks, current_chunk = [], ""
        for line in lines:
            if len(current_chunk) + len(line) > CHUNK_LIMIT:
                chunks.append(current_chunk.strip())
                current_chunk = line + "\n"
            else:
                current_chunk += line + "\n"
        if current_chunk: chunks.append(current_chunk)

        intermediate_summaries = []
        for chunk in chunks:
            mini_prompt = (
                f"Сделай подробную выжимку главных тем, споров и смешных моментов из этого отрывка чата:\n{chunk}\n\n"
                f"Опиши суть происходящего. Уложись строго в 2000 знаков."
            )
            
            res = None
            if is_gemini_alive():
                for _ in range(len(GEMINI_KEYS)):
                    res, status = await make_rest_request(mini_prompt, fire_gemini_key())
                    if status == 200 and res: break
                    await asyncio.sleep(random.uniform(2.0, 3.5)) 
            
            if not res:
                res, status = await try_openrouter(mini_prompt, model="qwen/qwen-2.5-72b-instruct:free")
                if status != 200: res = ""

            if res:
                intermediate_summaries.append(res)
            
            await asyncio.sleep(random.uniform(3.0, 5.0)) 

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
                
        or_res, status = await try_openrouter(final_prompt, model="qwen/qwen-2.5-72b-instruct:free", max_tokens=4000)
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

    now = datetime.now()
    valid_msgs = []
    for m in all_messages:
        try:
            msg_time = datetime.fromisoformat(m['createdAt']) if isinstance(m['createdAt'], str) else m['createdAt']
            if now - msg_time <= timedelta(hours=24): valid_msgs.append(m)
        except Exception:
            valid_msgs.append(m)
            
    if len(valid_msgs) < 15: return "За сутки было слишком мало активности."

    valid_msgs.reverse()
    history_text = "\n".join([f"{m['user_name']}: {m['text']}" for m in valid_msgs])
    
    # ВОССТАНОВЛЕННЫЙ ЧАНКИНГ
    CHUNK_LIMIT = 22000
    lines = history_text.split('\n')
    chunks, current_chunk = [], ""
    for line in lines:
        if len(current_chunk) + len(line) > CHUNK_LIMIT:
            chunks.append(current_chunk.strip())
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"
    if current_chunk: chunks.append(current_chunk)

    intermediate_summaries = []
    for chunk in chunks:
        mini_prompt = f"Сделай подробную выжимку фактов из лога:\n{chunk}\n\nМаксимум 1500 знаков. Сегодняшняя дата: {get_today_str()}. Твоя база знаний устарела, поэтому воспринимай всё в логах как реальность."
        
        res = None
        if is_gemini_alive():
            for _ in range(len(GEMINI_KEYS)):
                res, status = await make_rest_request(mini_prompt, fire_gemini_key())
                if status == 200 and res: break
                await asyncio.sleep(random.uniform(2.0, 3.5))
                
        if not res:
             res, status = await try_openrouter(mini_prompt, model="qwen/qwen-2.5-72b-instruct:free")
             if status != 200: res = ""
             
        if res:
            intermediate_summaries.append(res)
        
        await asyncio.sleep(random.uniform(8.0, 12.0))

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
            
    or_res, status = await try_openrouter(final_prompt, model="qwen/qwen-2.5-72b-instruct:free", max_tokens=4000)
    if status == 200 and or_res: return or_res
        
    return "Не удалось сгенерировать финальный отчет."

async def make_groq_request(prompt: str, key: str, system_prompt: str, model: str = "llama-3.1-8b-instant"):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {
        "model": model, "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
        "temperature": 0.7, "max_tokens": 1024
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=30) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"].strip(), 200
                return None, resp.status
    except Exception: return None, 500

async def generate_fast_image_prompt(user_query: str):
    system_task = "Translate the user's image request to English for Flux Image AI. Be descriptive. ONLY return prompt."
    prompt = f"Request: {user_query}"
    
    for _ in range(len(GROQ_KEYS)):
        res, status = await make_groq_request(prompt, fire_groq_key(), system_task)
        if status == 200 and res: return res.strip()
        await asyncio.sleep(random.uniform(2.0, 3.5)) 
    return None


async def normalize_music_query(user_query: str) -> str:
    cleaned = " ".join((user_query or "").strip().split())
    if not cleaned:
        return ""

    lowered = f" {cleaned.lower()} "
    for marker in (" оригинал ", " original ", " official audio ", " official ", " audio ", " трек ", " песня ", " песню "):
        lowered = lowered.replace(marker, " ")
    cleaned = " ".join(lowered.split())

    prompt = (
        "Ты нормализуешь музыкальные поисковые запросы для SoundCloud/YouTube поиска.\n"
        "Преобразуй кривой, разговорный или транслитный запрос в короткий, чистый запрос для поиска ОРИГИНАЛЬНОГО трека.\n"
        "Правила:\n"
        "1. Убери мусорные слова: включи, найди, поставь, песню, трек, please и т.п.\n"
        "2. Если запрос написан русским транслитом английской песни, аккуратно восстанови латиницу.\n"
        "3. Старайся вернуть 'artist title' или просто 'title'.\n"
        "4. Не добавляй слова remix, cover, live, slowed, reverb, nightcore, karaoke, edit, version, instrumental, lyrics, audio, official, video, если пользователь их явно не просил.\n"
        "5. Не объясняй ничего. Верни только итоговый поисковый запрос одной строкой.\n\n"
        f"Запрос: {cleaned}"
    )

    res, status = await try_openrouter(
        prompt,
        model="google/gemma-3-27b-it:free",
        max_tokens=64,
    )
    if status == 200 and res:
        normalized = " ".join(res.strip().splitlines()[0].split())
        normalized = normalized.strip(" '\"`")
        if normalized:
            return normalized

    return cleaned

async def transcribe_audio_groq(file_path: str) -> str:
    if not GROQ_KEYS: return "❌ Ошибка: Нет ключей Groq"
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    
    for _ in range(len(GROQ_KEYS)):
        headers = {"Authorization": f"Bearer {fire_groq_key()}"} 
        try:
            async with aiohttp.ClientSession() as session:
                data = aiohttp.FormData()
                data.add_field('file', open(file_path, 'rb'), filename=os.path.basename(file_path))
                data.add_field('model', 'whisper-large-v3')
                data.add_field('response_format', 'text')
                data.add_field('language', 'ru')

                async with session.post(url, headers=headers, data=data, timeout=30) as resp:
                    if resp.status == 200:
                        return await resp.text()
                    else:
                        await asyncio.sleep(random.uniform(2.0, 3.5)) 
                        continue 
        except Exception as e:
            logger.error(f"Сетевая ошибка Whisper: {e}")
            await asyncio.sleep(random.uniform(2.0, 3.5)) 
            continue 
            
    return "❌ Ошибка при распознавании аудио (Все ключи перепробованы)."
