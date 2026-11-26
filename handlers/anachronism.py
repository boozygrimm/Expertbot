import json
import re
import asyncio
import random
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import db
from ai_logic import generate_with_retry
from config import get_msk_now
from background import spawn

router = Router()

# Словарь для хранения активных игр в памяти (связка ID сообщения и данных игры).
# Ключ — id сообщения, значение — состояние партии, включая ключ идемпотентности.
active_anachronism_games = {}

GAME_TYPE = 'anachronism'
WIN_MULTIPLIER = 2.5


def game_key(msg_id: int) -> str:
    """Один ключ на партию: связывает ставку, попытку и выплату."""
    return f'{GAME_TYPE}:{msg_id}'


async def pay_once(key: str, *, chat_id: int, user_id: int, user_name: str, score: int) -> bool:
    """Начислить очки ровно один раз на ключ.

    Вместо транзакций Mongo (нужен replica set, а бот работает и на standalone)
    используем отметку в отдельной коллекции: повторный вызов с тем же ключом
    не создаёт вторую запись, а значит и второе начисление. Очки при этом
    остаются обычной строкой в `economy`, как и у остальных игр.
    """
    marked = await db.db.anachronism_payments.update_one(
        {'_id': key}, {'$setOnInsert': {'chat_id': chat_id, 'user_id': user_id, 'score': score,
                                        'date': get_msk_now()}}, upsert=True)
    if marked.upserted_id is None:
        return False
    try:
        await db.economy.insert_one({
            'game_type': GAME_TYPE, 'key': key, 'chat_id': chat_id,
            'user_id': user_id, 'user_name': user_name, 'score': score, 'date': get_msk_now(),
        })
    except Exception:
        # Без записи в экономике отметка врала бы, поэтому снимаем её.
        await db.db.anachronism_payments.delete_one({'_id': key})
        raise
    return True

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

    if bet > balance:
        return await message.answer(
            f"❌ Ставка больше баланса. В этом чате у тебя {balance} очков."
        )

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

    # 3. Сначала берём вопрос, и только потом списываем очки: иначе за сбой
    #    генерации игрок платил бы впустую.
    question_doc = await db.db.anachronism_cache.find_one_and_delete({})

    if not question_doc:
        # Кэш пуст: ждём генерацию, но деньги ещё не тронуты.
        await status_msg.edit_text("⏳ Архив пуст. Генерирую новые временные аномалии. Это займет около 10-15 секунд...")
        success = await fetch_anachronism_questions()
        if not success:
            return await status_msg.edit_text("❌ Временная аномалия нестабильна. Архив не ответил. Попробуйте позже.")
        question_doc = await db.db.anachronism_cache.find_one_and_delete({})
        if not question_doc:
            return await status_msg.edit_text("❌ Архив пуст. Попробуйте позже.")
    else:
        remaining = await db.db.anachronism_cache.count_documents({})
        if remaining <= 1:
            # Тихая генерация в фоне: бот не ждёт её окончания.
            spawn(fetch_anachronism_questions(), name='anachronism-refill')

    # 4. Списываем ставку вместе с комиссией одной строкой. Счётчик попыток
    #    двигаем только после успешного списания: если база откажет, игрок не
    #    потеряет право на бесплатный первый вход.
    await db.economy.insert_one({
        "game_type": GAME_TYPE,
        "key": f'{game_key(status_msg.message_id)}:bet',
        "chat_id": chat_id,
        "user_id": user_id,
        "user_name": user_name,
        "score": -total_required,
        "date": get_msk_now(),
    })
    await db.game_data.update_one(
        {"user_id": user_id, "chat_id": chat_id, "type": "anach_stats"},
        {"$set": {"date": today_str, "attempts": attempts + 1}},
        upsert=True
    )

    # 5. Формируем интерфейс и РАНДОМИЗИРУЕМ ОТВЕТЫ
    options = question_doc.get("options", [])
    
    # Перемешиваем список вариантов!
    random.shuffle(options)
    
    builder = InlineKeyboardBuilder()
    for i, opt in enumerate(options):
        builder.button(text=f"Вариант {i+1}", callback_data=f"anach:{i}")
    builder.adjust(1)
    
    fee_text = "Бесплатный вход" if fee == 0 else f"Сбор Архива: -{fee}"
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
    
    # Сохраняем стейт игры: ключ общий для всех записей этой партии.
    active_anachronism_games[game_msg.message_id] = {
        "user_id": user_id,
        "user_name": user_name,
        "chat_id": chat_id,
        "bet": bet,
        "key": game_key(game_msg.message_id),
        "options": options,  # уже перемешанный список
        "status": "active",
    }

    # Запускаем отложенную очистку памяти
    spawn(cleanup_game_session(game_msg.message_id), name='anachronism-cleanup')
    
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
        win_amount = int(bet * WIN_MULTIPLIER)  # возврат ставки + 150% выигрыша

        # Двойной клик по кнопке не должен платить дважды: ключ партии один.
        await pay_once(game['key'], chat_id=game['chat_id'], user_id=game['user_id'],
                       user_name=game['user_name'], score=win_amount)

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
    