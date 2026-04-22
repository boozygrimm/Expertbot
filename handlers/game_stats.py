import asyncio
from aiogram import Router, types
from aiogram.filters import Command
from database import db
from handlers.pole_chudes import delete_after_delay 
from datetime import datetime
import re

router = Router()

@router.message(Command("topgame", "топигр", "очки"))
async def show_global_top(message: types.Message):
    msg = await message.answer("🔄 Сводим дебет с кредитом...")
    chat_id = message.chat.id
    
    try:
        pipeline = [
            {"$match": {"chat_id": chat_id}},
            {"$group": {
                "_id": "$user_id",
                "user_name": {"$first": "$user_name"},
                "total_score": {"$sum": "$score"},
                "games_played": {"$sum": 1} 
            }},
            {"$sort": {"total_score": -1}},
            {"$limit": 15} 
        ]
        
        cursor = db.economy.aggregate(pipeline)
        top_players = await cursor.to_list(length=15)
        
        if not top_players:
            await msg.edit_text(
                "📊 В банке пока пусто. Играйте, чтобы заработать очки!\n\n"
                "💡 *Подсказка: В день доступно 2 бесплатные картинки, далее за очки!*\n"
                "(Напиши: `/img запрос` или *«Экс, нарисуй...»*)", 
                parse_mode="Markdown"
            )
            asyncio.create_task(delete_after_delay(msg, 60))
            return

        text = "🏆 **РЕЙТИНГ ЧАТА (ВСЕ ИГРЫ)** 🏆\n\n"
        text += "```text\n"
        text += f"{'№':<3}| {'Имя':<12} | {'Очки':<6}\n"
        text += "-" * 26 + "\n"
        
        for i, p in enumerate(top_players, 1):
            name = p.get('user_name', 'Неизвестно')[:11]
            score = p.get('total_score', 0)
            text += f"{i:<3}| {name:<12} | {score:<6}\n"
        
        text += "```\n"
        text += "💡 *В день доступно 2 бесплатные картинки, далее генерация стоит 250 очков.*\n"
        text += "Напиши: `/img запрос` или *«Экс, нарисуй...»*"
        
        await msg.edit_text(text, parse_mode="Markdown")
        asyncio.create_task(delete_after_delay(msg, 180))
        asyncio.create_task(delete_after_delay(message, 180))
        
    except Exception as e:
        await msg.edit_text("❌ Ошибка при подсчете казны.")

@router.message(Command("промокод"))
async def admin_promo_code(message: types.Message):
    ADMIN_ID = 980561369 
    
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ Эта команда доступна только Создателю бота.")

    args = message.text.split()
    amount = 0
    for arg in args[1:]:
        if arg.lstrip('-').isdigit():
            amount = int(arg)
            break
            
    if amount == 0:
        return await message.answer("⚠️ Укажите сумму.\nПример: `/промокод 10000`")
        
    target_user_id = None
    target_user_name = None
    
    if message.reply_to_message:
        target_user_id = message.reply_to_message.from_user.id
        target_user_name = message.reply_to_message.from_user.first_name
    elif len(args) > 2:
        search_name = " ".join(args[2:]).replace('@', '')
        doc = await db.economy.find_one({"user_name": {"$regex": f"^{search_name}$", "$options": "i"}})
        if doc:
            target_user_id = doc["user_id"]
            target_user_name = doc["user_name"]
        else:
            return await message.answer(f"❌ Игрок '{search_name}' не найден.")
    else:
        return await message.answer("⚠️ Сделайте реплай или напишите имя.")

    await db.economy.insert_one({
        "game_type": "promo",
        "chat_id": message.chat.id,
        "user_id": target_user_id,
        "user_name": target_user_name,
        "score": amount,
        "date": datetime.now()
    })
    
    await message.answer(f"💰 **СИСТЕМНОЕ ПОПОЛНЕНИЕ!**\nИгроку **{target_user_name}** начислено **{amount}** очков.")
    