import asyncio
import logging
import os

from app_setup import configure_dispatcher, create_scheduler, set_commands, start_healthcheck_server
from bot_instance import bot 
from handlers.alc_heritage import ensure_default_lore
from personas import init_personas
from database import db

async def main():
    logging.getLogger('apscheduler').setLevel(logging.WARNING)
    logging.basicConfig(level=logging.INFO)
    
    await db.setup()
    await ensure_default_lore()
    await init_personas()
    await set_commands(bot)

    dp = configure_dispatcher()
    scheduler = create_scheduler()
    scheduler.start()

    port = int(os.environ.get("PORT", 7860))
    runner = await start_healthcheck_server(port)
    
    logging.info(f"--- Бот запущен на порту {port} ---")
    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
    
