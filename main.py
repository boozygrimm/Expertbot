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

async def run_news_idle(queue):
    """Share the existing media queue; runtime settings can change without restart."""
    from news_prototype import owner_trial
    from news_prototype.news_worker_client import NewsWorkerClient
    if await owner_trial.runtime_enabled(db.db):
        client = NewsWorkerClient(os.getenv('BENDER_WORKER_URL', ''), os.getenv('BENDER_WORKER_KEY', ''))
        await owner_trial.dispatch_once(queue, db.db, client)
    elif os.getenv('NEWS_RENDER_QUEUE_ENABLED', '').strip() == '1':
        from news_prototype.news_queue import run_idle_once
        client = NewsWorkerClient(os.getenv('BENDER_WORKER_URL', ''), os.getenv('BENDER_WORKER_KEY', ''))
        await run_idle_once(queue, db.db.news_render_jobs, client)


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
    
    # Ordinary user media stays first; the idle hook checks persistent settings.
    queue_task = asyncio.create_task(video_queue.run(
        lambda queue, item: process_queued_video(queue, bot, item), idle_process=run_news_idle))
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
    
