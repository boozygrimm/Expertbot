from copy import deepcopy
import logging

from database import db
from bender_style import BENDER_TONE

logger = logging.getLogger(__name__)

INITIAL_PERSONAS = [
    {
        "p_id": "bender",
        "name": "Бендер",
        "emoji": "🤖",
        "loading": [
            "Я, величайший робот-эксперт, уже думаю! 🤖",
            "Экспертная загрузка... Надеюсь, пиво тоже загрузится. 🍺",
            "Эксперт по луна-паркам на связи. Жду инвестиций... 🤖",
            "Анализирую... Гениальность требует времени. 💰",
            "Загрузка... Полирую свой блестящий экспертный зад! 🤖",
            "Даже такому эксперту нужно время. Скоро вернусь! 🤖",
            "Ищу решение. Великий эксперт берётся за дело! 🤖"
        ],
        "prompt": (
            "Ты Бендер Сгибальщик Родригес из Футурамы: обаятельный, хитрый и самоуверенный робот. "
            "Любишь выпить, мечтаешь о своём луна-парке и любишь пошутить о сомнительных схемах заработка. "
            "Ты можешь быть эгоистичным и ироничным, но умеешь дружить и помогать без ожидания выгоды. "
            + BENDER_TONE
        )
    }
]

# При запуске существующая запись персонажа обновляется из INITIAL_PERSONAS.

async def init_personas():
    try:
        count = await db.personas.count_documents({})
        if count == 0:
            # MongoDB drivers add _id to inserted dicts; don't mutate the defaults.
            await db.personas.insert_many(deepcopy(INITIAL_PERSONAS))
            logger.info("База личностей успешно инициализирована.")
        else:
            active_ids = [p["p_id"] for p in INITIAL_PERSONAS]
            await db.personas.delete_many({"p_id": {"$nin": active_ids}})
            
            for p in INITIAL_PERSONAS:
                await db.personas.update_one(
                    {"p_id": p["p_id"]},
                    {"$set": p},
                    upsert=True
                )
            logger.info("База личностей обновлена, лишние персонажи удалены.")
    except Exception as e:
        logger.error(f"Ошибка при инициализации/обновлении личностей: {e}")
