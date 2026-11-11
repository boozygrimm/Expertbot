import asyncio
import logging
import os
import random

import aiohttp

from ai_providers import fire_groq_key
from config import GROQ_KEYS
from models import GROQ_TRANSCRIBE_MODELS

logger = logging.getLogger(__name__)


async def transcribe_audio_groq(file_path: str) -> str:
    """Расшифровка голоса через Groq Whisper.

    Модель берётся из `GROQ_TRANSCRIBE_MODELS`: сначала основной whisper-large-v3,
    при отказе модели (400/404 — снята с публикации или недоступна на тарифе)
    происходит фолбэк на следующую из списка.
    """
    if not GROQ_KEYS:
        return "❌ Ошибка: Нет ключей Groq"

    url = "https://api.groq.com/openai/v1/audio/transcriptions"

    for model in GROQ_TRANSCRIBE_MODELS:
        for _ in range(len(GROQ_KEYS)):
            headers = {"Authorization": f"Bearer {fire_groq_key()}"}
            try:
                async with aiohttp.ClientSession() as session:
                    data = aiohttp.FormData()
                    with open(file_path, "rb") as audio_file:
                        data.add_field("file", audio_file, filename=os.path.basename(file_path))
                        data.add_field("model", model)
                        data.add_field("response_format", "text")
                        data.add_field("language", "ru")

                        async with session.post(url, headers=headers, data=data, timeout=30) as response:
                            if response.status == 200:
                                return await response.text()

                            err_text = await response.text()
                            if response.status in (400, 404):
                                logger.error(
                                    "Whisper model %s недоступна (%s): %s",
                                    model,
                                    response.status,
                                    err_text[:200],
                                )
                                break

                            logger.warning(
                                "Whisper error %s для модели %s: %s",
                                response.status,
                                model,
                                err_text[:200],
                            )
                            await asyncio.sleep(random.uniform(2.0, 3.5))
                            continue
            except Exception as exc:
                logger.error("Whisper network error (%s): %s", model, exc)
                await asyncio.sleep(random.uniform(2.0, 3.5))
                continue

    return "❌ Ошибка при распознавании аудио (Все ключи перепробованы)."
