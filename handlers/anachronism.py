import json
import re
import asyncio
import random
from datetime import datetime
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import db
from ai_logic import generate_with_retry
from config import get_msk_now

router = Router()

# Словарь для хранения активных игр в памяти (связка ID сообщения и данных игры)
active_anachronism_games = {}

BATCH_PROMPT = """Ты — циничный архивариус забытых и самых абсурдных эпох. Твоя задача — сгенерировать 5 уникальных JSON-объектов для хардкорной игры "3 лжи, 1 истина" (Анахронизм).
Тематика: малоизвестная дикая история, безумная гастрономия древности или забытые ретро-технологии.

ПРАВИЛА:
1. ЗАПРЕЩЕНЫ банальные факты (никакого Наполеона, Пирамид, Эйнштейна или Римской Империи). Ищи самые маргинальные, неизвестные широкой публике, но РЕАЛЬНЫЕ факты.
2. ИСТИНА должна звучать абсолютно абсурдно и нелогично, но быть исторически достоверной.
3. ЛОЖЬ должна маскироваться под здравый смысл, известные исторические мифы и звучать максимально академично, чтобы запутать умного игрока.
4. "is_correct": true должен быть строго у одного варианта.

Формат каждого объекта:
{
  "situation": "Текст короткого, но сочного описания исторического контекста",
  "options": [
    {"text": "Вариант 1", "is_correct": false, "comment": "Ироничное объяснение, почему это логично, но ложь"},
    {"text": "Вариант 2", "is_correct": true, "comment": "Подтверждение факта с пруфом (кто, когда, где)"},
    {"text": "Вариант 3", "is_correct": false, "comment": "Текст подколки"},
    {"text": "Вариант 4", "is_correct": false, "comment": "Текст подколки"}
  ]
}
Ответь ТОЛЬКО валидным JSON-массивом, начиная с [ и заканчивая ], без маркдауна и других слов."""

async def fetch_anachronism_questions():
    """Запрашивает пакет вопросов у ИИ и сохраняет в БД."""
    response = await generate_with_retry(BATCH_PROMPT)
    if not response:
        return False
        
    try:
        # Извлекаем JSON массив из ответа
        match = re.search(r'\[.*\]', response, re.DOTALL)
        if match:
            json_str = match.group(0)
            data = json.loads(json_str)
            if isinstance(data, list) and len(data) > 0:
                # Кладем в коллекцию кэша (создастся автоматически)
                await db.db.anachronism_cache.insert_many(data)
                return True
    except Exception as e:
        print(f"Ошибка парсинга Анахронизма: {e}")
    return False

async def cleanup_game_session(msg_id: int, delay_seconds: int = 3600):
    """Сборщик мусора: удаляет зависшие игровые сессии из памяти через заданное время."""
    await asyncio.sleep(delay_seconds)
    if msg_id in active_anachronism_games:
        del active_anachronism_games[msg_id]

@router.message(Command("anachronism", "анахронизм", "ставка"))
async def start_anachronism(message: types.Message):
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    chat_id = message.chat.id
    
    # 1. Проверяем баланс игрока агрегацией всех транзакций в текущем чате
    pipeline = [
        {"$match": {"user_id": user_id, "chat_id": chat_id}},
        {"$group": {"_id": None, "balance": {"$sum": "$score"}}}
    ]
    cursor = db.economy.aggregate(pipeline)
    res = await cursor.to_list(length=1)
    balance = res[0]["balance"] if res else 0
    
    if balance <= 0:
        return await message.answer("💸 А на что ты играть собрался? У тебя ноль на балансе в этом чате! Иди зарабатывай.")

    # Извлекаем ставку из команды
    match = re.search(r'\b(\d+)\b', message.text)
    bet = int(match.group(1)) if match else 1000
        
    if bet <= 0:
        return await message.answer("⚠️ Ставка должна быть больше нуля!")

    # 2. Рассчитываем стоимость входа (комиссию Архива) ПО ЭКСПОНЕНТЕ
    today_str = get_msk_now().strftime("%Y-%m-%d")
    
    stats_doc = await db.game_data.find_one({"user_id": user_id, "chat_id": chat_id, "type": "anach_stats"})
    
    last_date = stats_doc.get("date", "") if stats_doc else ""
    attempts = stats_doc.get("attempts", 0) if stats_doc else 0
    
    if last_date != today_str:
        attempts = 0
        
    # Экспоненциальный рост: 0, 500, 1000, 2000, 4000...
    fee = 0 if attempts == 0 else int(500 * (2 ** (attempts - 1)))
    total_required = bet + fee
    
    if balance < total_required:
        return await message.answer(
            f"❌ Недостаточно очков!\nТвоя ставка: {bet}\nСтоимость запроса в Архив (попытка №{attempts+1}): {fee}\n"
            f"Итого нужно: {total_required}, а у тебя {balance}."
        )

    status_msg = await message.answer("⏳ Открываю Архив Времени... (проверка крипто-хэшей)")

    # 3. Получаем вопрос из кэша и ТИХО пополняем его
    question_doc = await db.db.anachronism_cache.find_one_and_delete({})
    
    if not question_doc:
        # Если кэш вообще пуст - ждем (блокирующий вызов)
        await status_msg.edit_text("⏳ Архив пуст. Генерирую новые временные аномалии. Это займет около 10-15 секунд...")
        success = await fetch_anachronism_questions()
        if not success:
            return await status_msg.edit_text("❌ Временная аномалия нестабильна. Архив не ответил. Попробуйте позже.")
        question_doc = await db.db.anachronism_cache.find_one_and_delete({})
    else:
        # Если вопрос есть, проверяем, сколько осталось в базе
        remaining = await db.db.anachronism_cache.count_documents({})
        if remaining <= 1:
            # ТИХАЯ генерация в фоне. Бот не будет ждать окончания этого процесса!
            asyncio.create_task(fetch_anachronism_questions())

    # 4. Списываем комиссию и ставку как НОВУЮ ТРАНЗАКЦИЮ
    new_attempts = attempts + 1
    
    await db.game_data.update_one(
        {"user_id": user_id, "chat_id": chat_id, "type": "anach_stats"},
        {"$set": {"date": today_str, "attempts": new_attempts}},
        upsert=True
    )
    
    # Добавляем минусовую транзакцию
    await db.economy.insert_one({
        "game_type": "anachronism_bet",
        "chat_id": chat_id,
        "user_id": user_id,
        "user_name": user_name,
        "score": -total_required,
        "date": get_msk_now()
    })

    # 5. Формируем интерфейс и РАНДОМИЗИРУЕМ ОТВЕТЫ
    options = question_doc.get("options", [])
    
    # Перемешиваем список вариантов!
    random.shuffle(options)
    
    builder = InlineKeyboardBuilder()
    for i, opt in enumerate(options):
        builder.button(text=f"Вариант {i+1}", callback_data=f"anach:{i}")
    builder.adjust(1)
    
    fee_text = f"Бесплатный вход" if fee == 0 else f"Сбор Архива: -{fee}"
    text = (
        f"🎩 **ПРОЕКТ «АНАХРОНИЗМ»**\n"
        f"Игрок: {user_name}\n"
        f"Ставка: {bet} очков | {fee_text}\n\n"
        f"📜 **Контекст:**\n{question_doc.get('situation', '')}\n\n"
        f"Где здесь истина?\n"
    )
    
    for i, opt in enumerate(options):
        text += f"**{i+1}.** {opt.get('text', '')}\n"

    await status_msg.delete()
    game_msg = await message.answer(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    
    # Сохраняем стейт игры
    active_anachronism_games[game_msg.message_id] = {
        "user_id": user_id,
        "bet": bet,
        "options": options, # Сохраняем уже перемешанный список
        "status": "active"
    }
    
    # Запускаем отложенную очистку памяти
    asyncio.create_task(cleanup_game_session(game_msg.message_id))
    
@router.callback_query(F.data.startswith("anach:"))
async def process_anachronism_answer(callback: types.CallbackQuery):
    msg_id = callback.message.message_id
    game = active_anachronism_games.get(msg_id)
    
    if not game:
        return await callback.answer("⏳ Время этой аномалии истекло.", show_alert=True)
        
    if callback.from_user.id != game["user_id"]:
        return await callback.answer("❌ Это не ваш вызов в Архив!", show_alert=True)
        
    if game["status"] != "active":
        return await callback.answer("Вы уже сделали выбор.", show_alert=True)
        
    game["status"] = "finished"
    
    selected_idx = int(callback.data.split(":")[1])
    options = game["options"]
    selected_opt = options[selected_idx]
    
    bet = game["bet"]
    
    # Надежная проверка булевых значений из LLM
    is_win = str(selected_opt.get("is_correct", False)).lower() == "true"
    
    result_text = callback.message.text + "\n\n---\n"
    
    if is_win:
        win_amount = int(bet * 2.5) # Возврат ставки + 150% выигрыш
        
        await db.economy.insert_one({
            "game_type": "anachronism_win",
            "chat_id": callback.message.chat.id,
            "user_id": callback.from_user.id,
            "user_name": callback.from_user.first_name,
            "score": win_amount,
            "date": get_msk_now()
        })
        
        result_text += f"✅ **ИСТИНА!**\n{selected_opt.get('comment', 'Комментарий утерян в архиве.')}\n\n"
        result_text += f"💰 Вы сорвали куш: +{win_amount} очков!"
        await callback.answer("Победа!", show_alert=False)
    else:
        # Ищем правильный ответ надежным способом для отображения при проигрыше
        true_opt = next((o for o in options if str(o.get("is_correct", False)).lower() == "true"), {})
        result_text += f"❌ **ЛОЖЬ!**\n{selected_opt.get('comment', '')}\n\n"
        result_text += f"На самом деле:\n{true_opt.get('comment', 'Информация была повреждена.')}\n\n"
        result_text += f"💸 Ваша ставка {bet} сгорела в Архиве."
        await callback.answer("Провал...", show_alert=False)
        
    await callback.message.edit_text(result_text, parse_mode="Markdown")
    del active_anachronism_games[msg_id]
    