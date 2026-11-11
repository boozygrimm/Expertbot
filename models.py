"""Единый реестр идентификаторов моделей всех AI-провайдеров.

Актуально на ноябрь 2026. Groq полностью вывел из free/developer-тарифов старые
Llama и Qwen-модели (см. https://console.groq.com/docs/deprecations):

    llama-3.1-8b-instant                          shutdown 16.08.2026 -> openai/gpt-oss-20b
    llama-3.3-70b-versatile                       shutdown 16.08.2026 -> openai/gpt-oss-120b
    qwen/qwen3-32b                                shutdown 17.07.2026 -> openai/gpt-oss-120b
    meta-llama/llama-4-scout-17b-16e-instruct      shutdown 17.07.2026 -> openai/gpt-oss-120b
    qwen/qwen3.6-27b                              shutdown 14.09.2026 -> qwen/qwen3.8-27b

Запросы к этим ID на обычном ключе возвращают ошибку, поэтому в качестве рабочих
остались только production-модели `openai/gpt-oss-120b` / `openai/gpt-oss-20b`,
preview-модель `qwen/qwen3.8-27b` и Whisper для аудио.

Любой идентификатор можно переопределить через переменную окружения (списки
моделей задаются через запятую, порядок = порядок фолбэка).
"""

import os

__all__ = [
    "OPENROUTER_TEXT_MODEL",
    "OPENROUTER_VISION_MODEL",
    "OPENROUTER_SUMMARY_MODEL",
    "GEMINI_CHAT_MODEL",
    "GROQ_FAST_MODELS",
    "GROQ_IMAGE_PROMPT_MODEL",
    "GROQ_STRONG_MODELS",
    "GROQ_TRANSCRIBE_MODELS",
    "is_reasoning_model",
    "model_env_list",
    "model_env_str",
]


def model_env_str(name: str, default: str) -> str:
    value = (os.getenv(name) or "").strip()
    return value or default


def model_env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name) or ""
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or list(default)


# --- Groq: текстовые модели -------------------------------------------------
# Быстрый контур: мини-игры, нормализация запросов, перевод промптов для картинок.
# gpt-oss-20b — самый быстрый production-вариант (~1000 токенов/с).
GROQ_FAST_MODELS = model_env_list("GROQ_FAST_MODELS", ["openai/gpt-oss-20b"])

# Основной контур: диалоги, отчёты, экспедиции. gpt-oss-120b — флагман,
# qwen/qwen3.8-27b — preview-запасной вариант с тем же контекстом 131K.
GROQ_STRONG_MODELS = model_env_list(
    "GROQ_STRONG_MODELS",
    ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"],
)

# Дешёвая модель для служебных одноразовых запросов (промпт для Flux/Pollinations).
GROQ_IMAGE_PROMPT_MODEL = model_env_str("GROQ_IMAGE_PROMPT_MODEL", "openai/gpt-oss-20b")

# --- Groq: распознавание речи ----------------------------------------------
# whisper-large-v3 точнее на русском, turbo — запасной вариант (дешевле и выше лимиты).
GROQ_TRANSCRIBE_MODELS = model_env_list(
    "GROQ_TRANSCRIBE_MODELS",
    ["whisper-large-v3", "whisper-large-v3-turbo"],
)

# --- Gemini -----------------------------------------------------------------
GEMINI_CHAT_MODEL = model_env_str("GEMINI_CHAT_MODEL", "gemini-3-flash-preview")

# --- OpenRouter (бесплатные :free-модели) -----------------------------------
OPENROUTER_TEXT_MODEL = model_env_str("OPENROUTER_TEXT_MODEL", "google/gemma-3-27b-it:free")
OPENROUTER_VISION_MODEL = model_env_str(
    "OPENROUTER_VISION_MODEL", "qwen/qwen3-vl-30b-thinking:free"
)
OPENROUTER_SUMMARY_MODEL = model_env_str(
    "OPENROUTER_SUMMARY_MODEL", "qwen/qwen-2.5-72b-instruct:free"
)


# Модели с собственными параметрами reasoning: им нужно явно гасить цепочку
# рассуждений, иначе "мысли" попадают в ответ и съедают лимит токенов.
def is_reasoning_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return "gpt-oss" in lowered or "qwen3" in lowered
