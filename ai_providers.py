import asyncio
import base64
import logging
import os
import random
import time

import aiohttp

from config import GEMINI_KEYS, GROQ_KEYS

OPENROUTER_KEYS = [k.strip() for k in os.getenv("OPENROUTER_KEYS", "").split(",") if k.strip()]

logger = logging.getLogger(__name__)

_groq_pointer = 0
_gemini_pointer = 0
_openrouter_pointer = 0

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
        logger.error("Gemini circuit breaker tripped for 60 seconds.")


def fire_groq_key():
    global _groq_pointer
    if not GROQ_KEYS:
        return None
    key = GROQ_KEYS[_groq_pointer].strip()
    _groq_pointer = (_groq_pointer + 1) % len(GROQ_KEYS)
    return key


def fire_gemini_key():
    global _gemini_pointer
    if not GEMINI_KEYS:
        return None
    key = GEMINI_KEYS[_gemini_pointer].strip()
    _gemini_pointer = (_gemini_pointer + 1) % len(GEMINI_KEYS)
    return key


def fire_openrouter_key():
    global _openrouter_pointer
    if not OPENROUTER_KEYS:
        return None
    key = OPENROUTER_KEYS[_openrouter_pointer].strip()
    _openrouter_pointer = (_openrouter_pointer + 1) % len(OPENROUTER_KEYS)
    return key


def log_gemini_status(status: int, err_text: str, quiet: bool = False):
    if status in (429, 503):
        log_fn = logger.debug if quiet else logger.warning
        log_fn("Gemini soft error %s: %s", status, err_text)
    else:
        logger.error("Gemini error %s: %s", status, err_text)


async def make_rest_request(
    prompt,
    api_key,
    system_prompt,
    media_data=None,
    model="gemini-3-flash-preview",
    quiet: bool = False,
):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    parts = [{"text": prompt}]
    if media_data:
        parts.append(
            {
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": base64.b64encode(media_data).decode("utf-8"),
                }
            }
        )

    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": 0.8, "maxOutputTokens": 4096},
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=60) as response:
                status = response.status
                if status != 200:
                    err_text = await response.text()
                    log_gemini_status(status, err_text, quiet=quiet)

                    if status in (400, 404):
                        models_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
                        async with session.get(models_url) as models_response:
                            if models_response.status == 200:
                                models_data = await models_response.json()
                                available = [model_item["name"] for model_item in models_data.get("models", [])]
                                logger.info("Available Gemini models: %s", available)
                    return None, status

                result = await response.json()
                text_out = result["candidates"][0]["content"]["parts"][0].get("text")
                return text_out, status
    except Exception as exc:
        logger.error("Gemini request failure: %s", exc)
        return None, 500


async def try_groq(prompt, system_prompt, use_fast_model=False):
    if not GROQ_KEYS:
        return None, 404

    models = (
        ["openai/gpt-oss-20b", "meta-llama/llama-4-scout-17b-16e-instruct"]
        if use_fast_model
        else ["llama-3.3-70b-versatile", "qwen/qwen3-32b"]
    )
    for model in models:
        for _ in range(len(GROQ_KEYS)):
            api_key = fire_groq_key()
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.6,
                "max_tokens": 4096,
            }

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers=headers, json=payload, timeout=20) as response:
                        if response.status == 200:
                            result = await response.json()
                            return result["choices"][0]["message"]["content"], 200
                        if response.status == 429:
                            await asyncio.sleep(random.uniform(2.0, 3.5))
                            continue
                        if response.status >= 500:
                            return None, response.status
            except Exception:
                await asyncio.sleep(random.uniform(2.0, 3.5))
                continue
    return None, 500


async def try_openrouter(prompt, system_prompt, model="google/gemma-3-27b-it:free", max_tokens=2048):
    if not OPENROUTER_KEYS:
        return None, 404

    for _ in range(len(OPENROUTER_KEYS)):
        api_key = fire_openrouter_key()
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://bugbibot.tg",
            "X-Title": "BugbiBot",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
            "max_tokens": max_tokens,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload, timeout=45) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result["choices"][0]["message"]["content"], 200
                    if response.status == 429:
                        await asyncio.sleep(random.uniform(2.0, 3.5))
                        continue
                    if response.status >= 500:
                        return None, response.status
        except Exception:
            await asyncio.sleep(random.uniform(2.0, 3.5))
            continue
    return None, 500


async def try_openrouter_vision(prompt, image_data, model="qwen/qwen3-vl-30b-thinking:free"):
    if not OPENROUTER_KEYS:
        return None, 404

    base64_image = base64.b64encode(image_data).decode("utf-8")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                    },
                ],
            }
        ],
        "temperature": 0.5,
        "max_tokens": 512,
    }
    url = "https://openrouter.ai/api/v1/chat/completions"

    for _ in range(len(OPENROUTER_KEYS)):
        headers = {
            "Authorization": f"Bearer {fire_openrouter_key()}",
            "Content-Type": "application/json",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload, timeout=30) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data["choices"][0]["message"]["content"], 200
                    if response.status == 429:
                        await asyncio.sleep(random.uniform(2.0, 3.5))
                        continue
        except Exception:
            await asyncio.sleep(random.uniform(2.0, 3.5))
            continue
    return None, 500
