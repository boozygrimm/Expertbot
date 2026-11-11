import asyncio
import logging
import random

import aiohttp

from ai_providers import build_groq_payload, fire_groq_key
from config import GROQ_KEYS
from models import GROQ_IMAGE_PROMPT_MODEL

logger = logging.getLogger(__name__)


async def make_groq_request(
    prompt: str,
    key: str,
    system_prompt: str,
    model: str = GROQ_IMAGE_PROMPT_MODEL,
    max_tokens: int = 1024,
):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    # fast=True -> температура 1: это единственное значение, которое gpt-oss
    # гарантированно принимает, а для остальных моделей оно тоже валидно.
    payload = build_groq_payload(model, system_prompt, prompt, max_tokens=max_tokens, fast=True)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=30) as response:
                if response.status == 200:
                    data = await response.json()
                    content = data["choices"][0]["message"]["content"]
                    return (content.strip() if content else None), 200

                err_text = await response.text()
                logger.warning("Groq image-prompt error %s (%s): %s", response.status, model, err_text[:200])
                return None, response.status
    except Exception as exc:
        logger.warning("Groq image-prompt network error (%s): %s", model, exc)
        return None, 500


async def generate_fast_image_prompt(user_query: str):
    system_task = "Translate the user's image request to English for Flux Image AI. Be descriptive. ONLY return prompt."
    prompt = f"Request: {user_query}"

    for _ in range(len(GROQ_KEYS)):
        res, status = await make_groq_request(prompt, fire_groq_key(), system_task)
        if status == 200 and res:
            return res.strip()
        # 400/401/403/404 — модель или ключ не работают, повторять бессмысленно.
        if status in (400, 401, 403, 404):
            break
        await asyncio.sleep(random.uniform(2.0, 3.5))
    return None
