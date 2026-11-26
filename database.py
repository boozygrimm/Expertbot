from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_URI
import logging
import math

logger = logging.getLogger(__name__)
MESSAGE_RETENTION_SECONDS = 180 * 24 * 60 * 60


class Database:
    def __init__(self):
        self.client = AsyncIOMotorClient(MONGO_URI)
        # Подключаемся к твоей базе данных
        self.db = self.client.beleyakov_db
        
        # Существующие коллекции
        self.messages = self.db.messages
        self.game_data = self.db.game_data 
        self.personas = self.db.personas 
        self.pole_stats = self.db.pole_stats
        self.pole_history = self.db.pole_history
        self.economy = self.db.economy 
        self.settings = self.db.settings
        self.ex_response_choices = self.db.ex_response_choices
        
        # НОВАЯ КОЛЛЕКЦИЯ ДЛЯ АВТОРИЗАЦИИ ЧАТОВ
        self.auth_chats = self.db.auth_chats
        
        # КОЛЛЕКЦИЯ ДЛЯ ПРОФИЛЕЙ СОЦИАЛЬНОГО РП
        self.profiles = self.db.profiles
        
    async def ensure_message_retention(self):
        """Extend the existing message TTL without rebuilding its index.

        Do not shorten a longer retention or convert a non-TTL/ambiguous index:
        either could unexpectedly delete older data. A failed migration must not
        prevent the bot from starting, but may leave the previous TTL active.
        """
        try:
            indexes = await self.messages.index_information()
            matches = [(name, spec) for name, spec in indexes.items()
                       if list(spec.get('key', [])) == [('createdAt', 1)]]
            if any('expireAfterSeconds' in spec and list(spec.get('key', [])) != [('createdAt', 1)]
                   for spec in indexes.values()):
                logger.warning('Other message TTL indexes exist; they may delete messages earlier')
            if not matches:
                await self.messages.create_index('createdAt', expireAfterSeconds=MESSAGE_RETENTION_SECONDS)
                logger.info('Message retention configured: 180 days')
                return
            if (len(matches) != 1 or 'expireAfterSeconds' not in matches[0][1]
                    or matches[0][1].get('partialFilterExpression') or matches[0][1].get('sparse')):
                logger.warning('Message retention not changed: ambiguous, partial or non-TTL createdAt index; inspect MongoDB indexes')
                return
            name, spec = matches[0]
            current = spec['expireAfterSeconds']
            if not isinstance(current, (int, float)) or isinstance(current, bool) or not math.isfinite(current):
                logger.warning('Message retention not changed: invalid TTL value; inspect MongoDB indexes')
            elif current > MESSAGE_RETENTION_SECONDS:
                logger.warning('Message retention not shortened: existing TTL exceeds 180 days; inspect MongoDB indexes')
            elif current < MESSAGE_RETENTION_SECONDS:
                await self.db.command({'collMod': 'messages', 'index': {
                    'name': name, 'expireAfterSeconds': MESSAGE_RETENTION_SECONDS,
                }})
                logger.info('Message retention extended to 180 days')
            else:
                logger.info('Message retention already set to 180 days')
        except Exception as exc:
            logger.warning('Message retention migration failed; previous TTL may remain active: %s', type(exc).__name__)

    async def setup(self):
        await self.ensure_message_retention()
        # Индекс для быстрого поиска персонажей
        await self.personas.create_index("p_id", unique=True)
        # Индекс для настроек
        await self.settings.create_index([("setting", 1), ("chat_id", 1)], unique=True)
        # Короткоживущие меню выбора формата ответа.
        await self.ex_response_choices.create_index("expires_at", expireAfterSeconds=0)
        await self.ex_response_choices.create_index("cleanup_at", sparse=True)
        # Индекс для авторизованных чатов
        await self.auth_chats.create_index("chat_id", unique=True)

db = Database()
