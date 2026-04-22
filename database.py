from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_URI
import datetime

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
        
        # НОВАЯ КОЛЛЕКЦИЯ ДЛЯ АВТОРИЗАЦИИ ЧАТОВ
        self.auth_chats = self.db.auth_chats
        
        # КОЛЛЕКЦИЯ ДЛЯ ПРОФИЛЕЙ СОЦИАЛЬНОГО РП
        self.profiles = self.db.profiles

        # НОВАЯ КОЛЛЕКЦИЯ ДЛЯ АЛХИМИИ (Глобальные профили)
        self.alchemy_players = self.db.alchemy_players
        
    async def setup(self):
        # Авто-удаление сообщений через 7 дней
        await self.messages.create_index("createdAt", expireAfterSeconds=604800)
        # Индекс для быстрого поиска персонажей
        await self.personas.create_index("p_id", unique=True)
        # Индекс для настроек
        await self.settings.create_index([("setting", 1), ("chat_id", 1)], unique=True)
        # Индекс для авторизованных чатов
        await self.auth_chats.create_index("chat_id", unique=True)
        # Индекс для алхимических профилей
        await self.alchemy_players.create_index("user_id", unique=True)

db = Database()
