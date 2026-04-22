import os
import datetime
from dotenv import load_dotenv

# Загружаем переменные из .env один раз здесь
load_dotenv()

# Основные токены
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
MONGO_URI = os.getenv("MONGO_URI", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "980561369"))

# Ключи Gemini (теперь используем единое имя GEMINI_KEYS)
raw_keys = os.getenv("GEMINI_KEYS") or os.getenv("GEMINI_API_KEYS") or ""
GEMINI_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]

# Ключи Groq (список)
raw_groq_keys = os.getenv("GROQ_KEYS") or os.getenv("GROQ_API_KEY") or ""
GROQ_KEYS = [k.strip() for k in raw_groq_keys.split(",") if k.strip()]

# Ключ специально для мини-игр
GEMINI_GAME_KEY = os.getenv("GEMINI_GAME_KEY", "").strip()

# Настройка часового пояса (МСК)
MSK = datetime.timezone(datetime.timedelta(hours=3))

def get_msk_now():
    return datetime.datetime.now(MSK)