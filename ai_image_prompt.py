import asyncio
import logging
import random

import aiohttp

from ai_providers import fire_groq_key
from config import GROQ_KEYS

logger = logging.getLogger(__name__)


async def make_groq_request(
    prompt: str,
    key: str,
    system_prompt: str,
    model: str = "llama-3.1-8b-instant",
):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 1024,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=30) as response:
                if response.status == 200:
                    data = await response.json()
                    return data["choices"][0]["message"]["content"].strip(), 200
                return None, response.status
    except Exception:
        return None, 500


async def generate_fast_image_prompt(user_query: str):
    system_task = "Translate the user's image request to English for Flux Image AI. Be descriptive. ONLY return prompt."
    prompt = f"Request: {user_query}"

    for _ in range(len(GROQ_KEYS)):
        res, status = await make_groq_request(prompt, fire_groq_key(), system_task)
        if status == 200 and res:
            return res.strip()
        await asyncio.sleep(random.uniform(2.0, 3.5))
    return None
