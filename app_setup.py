import logging

from aiohttp import web
from aiogram import Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats, BotCommandScopeChat
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot_commands import ADMIN_PRIVATE_COMMAND_SPECS, GROUP_COMMAND_SPECS, USER_PRIVATE_COMMAND_SPECS, build_bot_commands
from bot_instance import bot
from config import ADMIN_ID, MSK, get_msk_now
from handlers import (
    reports, pole_chudes, ex_logic, music, web_search,
    admin_cron, admin_panel, bender_clip, viselitsa, game_stats, anachronism, image_gen, auth, social_rp, alc_core, alc_base, alc_lab, alc_build, alc_crucible, alc_production, alc_quests, alc_achievements, alc_expeditions, alc_raids, alc_heritage, alc_intro
)
from handlers import voice_rec
from handlers.alc_expeditions import notify_arrived_expeditions, process_expeditions_tick
from handlers.alc_raids import process_random_fort_events
from handlers.alc_tasks import check_alchemy_stamina, collect_production_loot, process_barracks_upkeep, process_heritage_income
from handlers.admin_panel import FeatureAccessMiddleware
from handlers.auth import AuthMiddleware
from handlers.ex_logic import HELP_TEXT
from handlers.logger import ChatLoggerMiddleware


async def set_commands(target_bot):
    user_private_commands = build_bot_commands(USER_PRIVATE_COMMAND_SPECS)
    admin_private_commands = build_bot_commands(ADMIN_PRIVATE_COMMAND_SPECS)
    group_commands = build_bot_commands(GROUP_COMMAND_SPECS)

    try:
        await target_bot.set_my_commands(user_private_commands, scope=BotCommandScopeAllPrivateChats())
        await target_bot.set_my_commands(admin_private_commands, scope=BotCommandScopeChat(chat_id=ADMIN_ID))
        await target_bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())
    except Exception as e:
        logging.error(f"Ошибка установки меню: {e}")


async def check_cron_time():
    now = get_msk_now()
    try:
        await admin_cron.check_all_chats(bot, now)
    except Exception as e:
        logging.error(f"Ошибка в cron-задаче: {e}")


def configure_dispatcher() -> Dispatcher:
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
        await message.answer("👨‍💻 **Связь с владельцем:**\nПо всем вопросам пишите: @boozygrimm", parse_mode="Markdown")

    # Сначала ставим наш игровой штаб, чтобы он первым проверял команду /start alcbase
    dp.include_router(alc_base.router)
    dp.include_router(alc_intro.router)
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

    # Явный запрос кружка должен перехватываться до общего обработчика Экса.
    dp.include_router(bender_clip.router)

    # всегда последний
    dp.include_router(ex_logic.router)
    return dp


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_cron_time, "cron", minute="*")
    scheduler.add_job(check_alchemy_stamina, "interval", minutes=10)
    scheduler.add_job(collect_production_loot, "interval", hours=1)
    scheduler.add_job(process_heritage_income, "cron", hour=3, minute=5, timezone=MSK)
    scheduler.add_job(process_barracks_upkeep, "cron", hour=3, minute=10, timezone=MSK)
    scheduler.add_job(process_expeditions_tick, "interval", minutes=1)
    scheduler.add_job(notify_arrived_expeditions, "interval", minutes=2)
    scheduler.add_job(process_random_fort_events, "cron", hour="*/2", minute=15)
    return scheduler


async def handle_healthcheck(request):
    return web.Response(text="Бот онлайн. Меню настроено.", status=200)


async def start_healthcheck_server(port: int) -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", handle_healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    return runner
