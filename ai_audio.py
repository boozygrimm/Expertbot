import asyncio
import logging
import os
import random

import aiohttp

from ai_providers import fire_groq_key
from config import GROQ_KEYS

logger = logging.getLogger(__name__)


async def transcribe_audio_groq(file_path: str) -> str:
    if not GROQ_KEYS:
        return "❌ Ошибка: Нет ключей Groq"

    url = "https://api.groq.com/openai/v1/audio/transcriptions"

    for _ in range(len(GROQ_KEYS)):
        headers = {"Authorization": f"Bearer {fire_groq_key()}"}
        try:
            async with aiohttp.ClientSession() as session:
                data = aiohttp.FormData()
                with open(file_path, "rb") as audio_file:
                    data.add_field("file", audio_file, filename=os.path.basename(file_path))
                    data.add_field("model", "whisper-large-v3")
                    data.add_field("response_format", "text")
                    data.add_field("language", "ru")

                    async with session.post(url, headers=headers, data=data, timeout=30) as response:
                        if response.status == 200:
                            return await response.text()
                        await asyncio.sleep(random.uniform(2.0, 3.5))
                        continue
        except Exception as exc:
            logger.error("Whisper network error: %s", exc)
            await asyncio.sleep(random.uniform(2.0, 3.5))
            continue

    return "❌ Ошибка при распознавании аудио (Все ключи перепробованы)."
