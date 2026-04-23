import asyncio
import logging
import os

from aiohttp import web
from aiogram import Dispatcher, F, types
from aiogram.filters import Command 
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats, BotCommandScopeChat
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot_instance import bot 
from handlers import (
    reports, pole_chudes, ex_logic, music, web_search, 
    admin_cron, admin_panel, viselitsa, game_stats, anachronism, image_gen, auth, social_rp, alc_core, alc_base, alc_lab, alc_build, alc_crucible, alc_production, alc_quests, alc_achievements, alc_expeditions, alc_raids, alc_heritage
)
from handlers import voice_rec
from handlers.alc_tasks import check_alchemy_stamina, collect_production_loot, process_barracks_upkeep, process_heritage_income
from handlers.alc_expeditions import process_expeditions_tick, notify_arrived_expeditions
from handlers.alc_heritage import ensure_default_lore
from handlers.alc_raids import process_random_fort_events

from handlers.logger import ChatLoggerMiddleware 
from handlers.admin_panel import FeatureAccessMiddleware
from handlers.ex_logic import HELP_TEXT
from handlers.auth import AuthMiddleware
from personas import init_personas
from database import db
from config import ADMIN_ID, MSK, get_msk_now

async def handle_healthcheck(request):
    return web.Response(text="Бот онлайн. Меню настроено.", status=200)

async def set_commands(bot):
    user_private_commands = [
        BotCommand(command="help", description="Справка по боту"),
        BotCommand(command="support", description="Связаться с владельцем"),
        BotCommand(command="base", description="🏕 Главный Штаб") # Добавь эту строку
    ]
    
    admin_private_commands = [
        BotCommand(command="help", description="Справка"),
        BotCommand(command="img", description="Генерация ИИ"),
        BotCommand(command="cron", description="⏱ Настройка отчетов"),
        BotCommand(command="chats", description="🔐 Управление чатами"),
        BotCommand(command="support", description="Связаться с владельцем"),
        BotCommand(command="base", description="🏕 Главный Штаб"), # Добавь эту строку
        BotCommand(command="regen_quests", description="Перезапуск квестов"),
        BotCommand(command="lore_edit", description="Редактор Наследия"),
    ]

    group_commands = [
        BotCommand(command="help", description="Справка"),
        BotCommand(command="img", description="Нарисовать картинку (2 беспл/день, далее 250 очков)"),
        BotCommand(command="gossip", description="Сплетни чата"),
        BotCommand(command="vis", description="Виселица"),
        BotCommand(command="pole", description="Поле чудес"),
        BotCommand(command="topgame", description="Топ игроков"),
        BotCommand(command="stop", description="Остановить игру"),
        BotCommand(command="voice", description="Настройка голосовых"),
        BotCommand(command="panel", description="Панель модулей чата")
    ]

    try:
        await bot.set_my_commands(user_private_commands, scope=BotCommandScopeAllPrivateChats())
        await bot.set_my_commands(admin_private_commands, scope=BotCommandScopeChat(chat_id=ADMIN_ID))
        await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())
    except Exception as e:
        logging.error(f"Ошибка установки меню: {e}")

async def check_cron_time():
    now = get_msk_now()
    try:
        await admin_cron.check_all_chats(bot, now) 
    except Exception as e:
        logging.error(f"Ошибка в cron-задаче: {e}")

async def main():
    logging.getLogger('apscheduler').setLevel(logging.WARNING)
    logging.basicConfig(level=logging.INFO)
    
    await db.setup()
    await ensure_default_lore()
    await init_personas()
    await set_commands(bot)

    dp = Dispatcher()

    dp.message.middleware(AuthMiddleware())
    dp.message.middleware(ChatLoggerMiddleware())
    dp.message.middleware(FeatureAccessMiddleware())
    dp.callback_query.middleware(FeatureAccessMiddleware())
    
    @dp.message(Command(commands=["start", "help", "справка", "экс помощь"]), F.text.in_({"/start", "/help", "/справка", "/экс помощь"}))
    async def cmd_help(message: types.Message):
        await message.answer(HELP_TEXT, parse_mode="Markdown")

    @dp.message(Command("support"))
    async def cmd_support(message: types.Message):
        await message.answer(f"👨‍💻 **Связь с владельцем:**\nПо всем вопросам пишите: @boozygrimm", parse_mode="Markdown")
    # Сначала ставим наш игровой штаб, чтобы он первым проверял команду /start alcbase
    dp.include_router(alc_base.router)
    dp.include_router(alc_achievements.router)
    dp.include_router(alc_heritage.router)
    dp.include_router(alc_quests.router)
    dp.include_router(alc_lab.router)
    dp.include_router(alc_build.router)
    dp.include_router(alc_expeditions.router)
    dp.include_router(alc_raids.router)
    dp.include_router(admin_panel.router)
    dp.include_router(auth.router)
    dp.include_router(admin_cron.router)
    dp.include_router(alc_crucible.router)
    dp.include_router(alc_production.router)
    dp.include_router(pole_chudes.router)
    dp.include_router(reports.router)
    dp.include_router(music.router)      
    dp.include_router(web_search.router)     
    dp.include_router(viselitsa.router)
    dp.include_router(game_stats.router)    
    dp.include_router(anachronism.router)
    dp.include_router(image_gen.router)
    dp.include_router(voice_rec.router)
    dp.include_router(social_rp.router)
    dp.include_router(alc_core.router)

    #всегда последний
    dp.include_router(ex_logic.router)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_cron_time, 'cron', minute='*')
    scheduler.add_job(check_alchemy_stamina, 'interval', minutes=10)
    scheduler.add_job(collect_production_loot, 'interval', hours=1)
    scheduler.add_job(process_heritage_income, 'cron', hour=3, minute=5, timezone=MSK)
    scheduler.add_job(process_barracks_upkeep, 'cron', hour=3, minute=10, timezone=MSK)
    scheduler.add_job(process_expeditions_tick, 'interval', minutes=1)
    scheduler.add_job(notify_arrived_expeditions, 'interval', minutes=2)
    scheduler.add_job(process_random_fort_events, 'cron', hour='*/2', minute=15)
    scheduler.start()


    app = web.Application()
    app.router.add_get("/", handle_healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 7860))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    
    logging.info(f"--- Бот запущен на порту {port} ---")
    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
    
