import asyncio
import logging
import os

from app_setup import configure_dispatcher, create_scheduler, set_commands, start_healthcheck_server
from bot_instance import bot 
from handlers.alc_heritage import ensure_default_lore
from personas import init_personas
from database import db
from handlers.bender_clip import process_queued_video, video_queue
from handlers.response_choice import run_choice_cleanup

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
    
    queue_task = asyncio.create_task(video_queue.run(
        lambda queue, item: process_queued_video(queue, bot, item)))
    cleanup_task = asyncio.create_task(run_choice_cleanup(bot))
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        queue_task.cancel()
        cleanup_task.cancel()
        await asyncio.gather(queue_task, cleanup_task, return_exceptions=True)
        await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
    
