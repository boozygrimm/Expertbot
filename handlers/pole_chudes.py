import re
import random
import asyncio
import logging
import time
from datetime import datetime, timedelta
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ai_logic import generate_with_retry
from database import db

router = Router()
logger = logging.getLogger(__name__)

active_games = {}
VOWELS = set("АЕЁИОУЫЭЮЯ")

FALLBACK_WORDS = [
    ("СВЕТОФОР", "Регулировщик на перекрестке с тремя разноцветными глазами."),
    ("ВЕЛОСИПЕД", "Двухколесный транспорт, для движения которого нужно крутить педали."),
    ("МЕЛЬНИЦА", "Сооружение, которое мелет зерно в муку с помощью силы ветра или воды."),
    ("СКАФАНДР", "Герметичный защитный костюм для выхода в открытый космос."),
    ("ТЕЛЕСКОП", "Оптический прибор для наблюдения за удаленными звездами и планетами.")
]

GAME_CATEGORIES = [
    "Устаревшие предметы быта", "Архитектурные термины",
    "Сложные научные термины", "Мореплавание и устройство корабля",
    "Анатомия и медицина", "Экзотическая кулинария",
    "Редкие музыкальные инструменты", "Мифология и фольклор",
    "География и природные явления", "Старинные ремесла и инструменты",
    "Одежда и мода прошлых веков", "Ботаника и редкие растения"
]

SECTORS = [100, 200, 300, 400, 500, 750, 1000, "ПЛЮС"]

BOT_COMMENTS = {
    "win": [
        "Абсолютно верно. Игра завершена.", 
        "Правильный ответ. Поздравляю с победой.", 
        "Точно! Слово угадано.",
        "И это правильный ответ.",
        "Слово отгадано верно."
    ],
    "fail": [
        "К сожалению, это не так. Ход переходит дальше.", 
        "Увы, ошибка. Следующий игрок.", 
        "Мимо. Игра продолжается.",
        "Неверно. Ход передается другому игроку.",
        "К сожалению, нет."
    ],
    "letter_ok": [
        "Такая буква есть. Открываем.",
        "Верно, буква угадана.",
        "Отличный ход, есть такая буква.",
        "Точно, открываем букву.",
        "Правильно, эта буква присутствует."
    ],
    "letter_fail": [
        "Такой буквы нет.",
        "Мимо, ход переходит дальше.",
        "К сожалению, этой буквы в слове нет.",
        "Ошибка, такой буквы нет.",
        "Неверно, буквы нет."
    ]
}

async def unpin_and_delete_after_delay(bot, chat_id, message_id, delay: int = 300):
    await asyncio.sleep(delay)
    try: 
        await bot.unpin_chat_message(chat_id=chat_id, message_id=message_id)
    except Exception as e: 
        logging.error(f"Ошибка отложенного открепа: {e}")
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logging.error(f"Ошибка отложенного удаления: {e}")

async def delete_after_delay(message: types.Message, delay: int = 10):
    await asyncio.sleep(delay)
    try: 
        await message.delete()
    except Exception as e:
        logger.debug("Failed to delete delayed message in pole chudes chat %s: %s", message.chat.id, e)

async def get_comment(event_type):
    return random.choice(BOT_COMMENTS.get(event_type, ["Интересный поворот."]))

def get_letter_score(letter: str) -> int:
    return 2 if letter in VOWELS else 3

def init_player_if_needed(game, user_id, user_name):
    if user_id not in game["players"]:
        game["players"][user_id] = {"name": user_name, "score": 0}

def get_main_kb(game):
    builder = InlineKeyboardBuilder()
    
    if game["current_turn"] is None:
        builder.button(text="🎰 Крутить барабан", callback_data="spin_drum")
        builder.adjust(1)
        return builder.as_markup()
    
    all_used = game["used_letters"] | game["missed_letters"]
    alphabet = [l for l in "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ" if l not in all_used]
    
    for char in alphabet:
        builder.button(text=char, callback_data=f"cl_{char}")
        
    builder.button(text="💡 Слово целиком", callback_data="guess_full_word")
    builder.adjust(6) 
    return builder.as_markup()

async def update_main_message(bot, chat_id):
    game = active_games.get(chat_id)
    if not game: 
        return
    
    display = " ".join(game["guessed"])
    missed = ", ".join(sorted(game["missed_letters"])) or "нет"
    
    scoreboard_lines = [f"👤 {p['name']}: {p['score']} очк." for p in game["players"].values() if p['score'] > 0]
    score_text = f"\n**Счёт:**\n" + "\n".join(scoreboard_lines) + "\n━━━━━━━━━━━━━━━━━━━━" if scoreboard_lines else ""
    
    turn_status = "🟢 Барабан свободен! Жмите кнопку."
    if game["current_turn"] is not None:
        active_player = game["players"][game["current_turn"]]["name"]
        turn_status = f"🟡 Ход игрока: **{active_player}**\n🎯 На кону: **{game['current_value']}** очков"

    comment_block = f"\n\n💬 **Ведущий**: _{game['last_comment']}_" if game.get('last_comment') else ""

    text = (
        f"🎤 **ПОЛЕ ЧУДЕС** 🎤\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"**Загадка:** {game['hint']}\n\n"
        f"**Слово:** `{display}`\n"
        f"**Мимо:** `{missed}`\n"
        f"━━━━━━━━━━━━━━━━━━━━{score_text}\n"
        f"{turn_status}{comment_block}"
    )
    
    if game.get("last_text") == text:
        return
    game["last_text"] = text

    try: 
        await bot.edit_message_text(
            chat_id=chat_id, 
            message_id=game["main_msg_id"], 
            text=text,
            reply_markup=get_main_kb(game),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.debug("Failed to update pole chudes main message in chat %s: %s", chat_id, e)

async def turn_timeout_task(chat_id, user_id, bot):
    await asyncio.sleep(60)
    game = active_games.get(chat_id)
    
    if game and game.get("current_turn") == user_id:
        game["current_turn"] = None
        game["last_player_id"] = user_id
        game["last_move_time"] = time.time()
        game["last_comment"] = "Игрок задумался слишком надолго. Барабан свободен."
        
        await update_main_message(bot, chat_id)
        msg = await bot.send_message(chat_id, "⏳ Время вышло! Игрок уснул, барабан снова свободен. 🎰")
        asyncio.create_task(delete_after_delay(msg, 10))

@router.message(Command("stop", "хватит"), lambda msg: msg.chat.id in active_games)
async def stop_game(message: types.Message):
    chat_id = message.chat.id
    if chat_id in active_games:
        # Достаем данные игры перед удалением
        game = active_games[chat_id]
        secret_word = game["word"]
        
        # Отменяем таск таймаута, если он запущен
        if game["timeout_task"]:
            game["timeout_task"].cancel()
            
        # Очищаем игру из памяти
        del active_games[chat_id]
        
        # Запускаем фоновую очистку (открепление и удаление главного сообщения игры)
        asyncio.create_task(unpin_and_delete_after_delay(message.bot, chat_id, game["main_msg_id"]))
        
        # Отвечаем игрокам, раскрывая интригу
        await message.answer(
            f"🛑 **Игра принудительно остановлена.**\n\n"
            f"Эх, а ведь было загадано слово: **{secret_word}**\n\n"
            f"Барабан уносят на склад! 📦",
            parse_mode="Markdown"
        )
    else:
        ans = await message.answer("А мы сейчас и не играем! 🤷‍♂️")
        asyncio.create_task(delete_after_delay(ans, 10))
        asyncio.create_task(delete_after_delay(message, 10))

@router.message(Command("pole"))
async def start_game(message: types.Message):
    chat_id = message.chat.id
    if chat_id in active_games:
        msg = await message.answer("✋ Тише! Тут еще старая игра идет, не мешай!")
        asyncio.create_task(delete_after_delay(msg, 10))
        return

    status_msg = await message.answer("⏳ Готовим студию, запрашиваем архив слов...")
    
    # 1. Достаем из БД последние 100 слов
    try:
        cursor = db.pole_history.find().sort("date", -1).limit(100)
        history_docs = await cursor.to_list(length=100)
        recent_words_list = [d['word'] for d in history_docs]
        forbidden_list = ", ".join(recent_words_list) if recent_words_list else "пока нет"
    except Exception as e:
        logging.error(f"Ошибка при чтении истории из БД: {e}")
        forbidden_list = "пока нет"

    category = random.choice(GAME_CATEGORIES)
    
    # 2. Формируем промпт с учетом списка исключений и категорий
    prompt = (
        "Твоя задача — сгенерировать СЛОЖНОЕ и НЕОБЫЧНОЕ задание для игры 'Поле Чудес'.\n"
        f"ОБЯЗАТЕЛЬНАЯ ТЕМА: {category}\n"
        "ПРАВИЛА КОТОРЫЕ НЕЛЬЗЯ НАРУШАТЬ:\n"
        "1. Загадай ОДНО реально существующее русское слово. Выбирай редкие, узкоспециализированные или забытые слова.\n"
        "2. Слово должно быть существительным, нарицательным, строго в ИМЕНИТЕЛЬНОМ ПАДЕЖЕ и ЕДИНСТВЕННОМ ЧИСЛЕ.\n"
        "3. Длина слова — СТРОГО ОТ 7 ДО 15 БУКВ.\n"
        "4. Напиши умную загадку-описание. Она должна точно описывать суть, но заставлять подумать. Не делай её слишком очевидной.\n"
        f"5. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать следующие слова (они уже были): {forbidden_list}.\n"
        "ОТВЕТ ДАЙ СТРОГО В ФОРМАТЕ: СЛОВО|ОПИСАНИЕ\n"
        "Пример ответа: АСТРОЛЯБИЯ|Старинный прибор для определения широт и долгот в астрономии."
    )

    try:
        res = await generate_with_retry(prompt, is_game=True)
        if not res or "|" not in res:
            word, hint = random.choice(FALLBACK_WORDS)
        else:
            parts = res.split("|", 1)
            raw_word = parts[0].strip().upper()
            word = re.sub(r'[^А-ЯЁ]', '', raw_word.split()[0])
            hint = parts[1].strip() if parts[1].strip() else "Секретный объект..."

            if 7 <= len(word) <= 15:
                # 3. Сохраняем в историю
                try:
                    await db.pole_history.insert_one({
                        "word": word,
                        "date": datetime.now()
                    })
                except Exception as e:
                    logging.error(f"Не удалось сохранить слово в историю: {e}")
            else:
                word, hint = random.choice(FALLBACK_WORDS)
    except Exception as e:
        logging.error(f"Ошибка ИИ при старте игры: {e}")
        word, hint = random.choice(FALLBACK_WORDS)

    active_games[chat_id] = {
        "word": word,
        "guessed": ["_"] * len(word),
        "used_letters": set(),
        "missed_letters": set(),
        "hint": hint,
        "players": {}, 
        "main_msg_id": status_msg.message_id,
        "current_turn": None,
        "current_value": 0,
        "last_player_id": None,
        "last_move_time": 0,
        "timeout_task": None,
        "last_comment": "",
        "last_text": ""
    }
    
    await update_main_message(message.bot, chat_id)
    
    try: 
        await status_msg.pin(disable_notification=True)
    except Exception: 
        pass
        
    asyncio.create_task(delete_after_delay(message))

@router.callback_query(F.data == "spin_drum")
async def handle_spin(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    game = active_games.get(chat_id)
    if not game:
        return await callback.answer("Игра уже окончена!", show_alert=True)

    user_id = callback.from_user.id
    current_time = time.time()

    if game["current_turn"] is not None:
        return await callback.answer("Барабан уже занят! Жди своей очереди. ✋", show_alert=True)

    if game["last_player_id"] == user_id:
        elapsed = current_time - game["last_move_time"]
        if elapsed < 10:
            remaining = int(10 - elapsed)
            return await callback.answer(f"Дай шанс другим! Пауза еще {remaining} сек. ⏳", show_alert=True)

    init_player_if_needed(game, user_id, callback.from_user.first_name)
    
    game["current_turn"] = user_id
    game["last_player_id"] = user_id
    game["last_move_time"] = current_time

    res = random.choice(SECTORS)
    game["current_value"] = res

    if res == "ПЛЮС":
        game["current_turn"] = None
        game["last_comment"] = "Выпал сектор ПЛЮС! Открываем случайную скрытую букву."
        
        unrevealed = [l for i, l in enumerate(game["word"]) if game["guessed"][i] == "_"]
        if unrevealed:
            chosen_letter = random.choice(unrevealed)
            game["used_letters"].add(chosen_letter)
            
            for i, l in enumerate(game["word"]):
                if l == chosen_letter:
                    game["guessed"][i] = chosen_letter
            
            if "_" not in game["guessed"]:
                final_word = game['word']
                player = game["players"][user_id]
                winner_name = player["name"]
                final_score = player["score"]
                comment = await get_comment("win")
                
                # Отправляем очки в единую экономику (ТЕПЕРЬ ОТСТУП ВЕРНЫЙ!)
                try:
                    await db.economy.insert_one({
                        "game_type": "pole",
                        "chat_id": chat_id,
                        "user_id": user_id,
                        "user_name": winner_name,
                        "score": final_score,
                        "date": datetime.now()
                    })
                except Exception as e:
                    logging.error(f"Ошибка БД в поле чудес: {e}")

                asyncio.create_task(unpin_and_delete_after_delay(callback.message.bot, chat_id, game["main_msg_id"]))
                del active_games[chat_id]
                
                await callback.message.edit_text(
                    f"🏆 **ПОБЕДА!**\nСлово: **{final_word}**\n\n🎉 {winner_name} выигрывает со счетом {final_score} очков!\n\n💬 **Ведущий**: _{comment}_",
                    parse_mode="Markdown"
                )
                return await callback.answer()
                
        await callback.answer("✨ Сектор ПЛЮС! Случайная буква открыта.", show_alert=True)
        await update_main_message(callback.message.bot, chat_id)
        return

    if game["timeout_task"]:
        game["timeout_task"].cancel()
    game["timeout_task"] = asyncio.create_task(turn_timeout_task(chat_id, user_id, callback.message.bot))

    game["last_comment"] = ""
    await callback.answer(f"🎰 Выпало {res} очков! Выбирай букву.", show_alert=False)
    await update_main_message(callback.message.bot, chat_id)

@router.callback_query(F.data == "guess_full_word")
async def handle_full_word_btn(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    game = active_games.get(chat_id)
    if not game: return
    
    if game["current_turn"] != callback.from_user.id:
        return await callback.answer("Сейчас не твой ход! ⏳", show_alert=True)
        
    await callback.answer("Напиши в чат ответ в формате:\nслово ТВОЙ_ОТВЕТ", show_alert=True)

@router.message(F.text.lower().startswith("слово "))
async def handle_full_word_text(message: types.Message):
    chat_id = message.chat.id
    game = active_games.get(chat_id)
    if not game: return

    user_id = message.from_user.id
    
    if game["current_turn"] != user_id:
        ans = await message.reply("Сейчас не твой ход крутить барабан! ✋")
        asyncio.create_task(delete_after_delay(ans))
        asyncio.create_task(delete_after_delay(message))
        return

    if game["timeout_task"]:
        game["timeout_task"].cancel()
        game["timeout_task"] = None

    match = re.search(r'(?i)слово\s+(.*)', message.text)
    if not match: return
    
    raw_word = match.group(1).strip().upper()
    user_word = re.sub(r'[^А-ЯЁ]', '', raw_word)
    player = game["players"][user_id]
    
    if user_word == game["word"]:
        bonus_points = 0
        for i, l in enumerate(game["word"]):
            if game["guessed"][i] == "_":
                bonus_points += get_letter_score(l) * game["current_value"]
        
        player["score"] += bonus_points
        final_score = player["score"]
        winner_name = player["name"]
        final_word = game["word"]
        comment = await get_comment("win")
        
        try:
            await db.pole_stats.insert_one({
                "chat_id": chat_id,
                "user_id": user_id,
                "user_name": winner_name,
                "score": final_score,
                "date": datetime.now()
            })
        except Exception as e:
            logging.error(f"Ошибка БД при победе словом: {e}")
        
        asyncio.create_task(unpin_and_delete_after_delay(message.bot, chat_id, game["main_msg_id"]))
        del active_games[chat_id]
        
        await message.answer(
            f"🏆 **БЛЕСТЯЩАЯ ПОБЕДА!**\n"
            f"Слово: **{final_word}**\n\n"
            f"🎉 {winner_name} угадывает слово целиком!\n"
            f"Итоговый счет: {final_score} очков.\n\n"
            f"💬 **Ведущий**: _{comment}_",
            parse_mode="Markdown"
        )
    else:
        game["current_turn"] = None
        game["last_move_time"] = time.time()
        game["last_comment"] = await get_comment("fail")
        
        ans = await message.reply(f"❌ Ошибка! Это не '{user_word}'. Ход переходит дальше.")
        await update_main_message(message.bot, chat_id)
        asyncio.create_task(delete_after_delay(ans))
        
    asyncio.create_task(delete_after_delay(message))

@router.callback_query(F.data.startswith("cl_"))
async def handle_letter(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    game = active_games.get(chat_id)
    if not game:
        return await callback.answer("Игра окончена!")

    user_id = callback.from_user.id
    
    if game["current_turn"] != user_id:
        return await callback.answer("Сейчас не твой ход! ⏳", show_alert=True)

    if game["timeout_task"]:
        game["timeout_task"].cancel()
        game["timeout_task"] = None

    letter = callback.data.split("_")[1]
    player = game["players"][user_id]
    
    if letter in game["word"]:
        game["used_letters"].add(letter)
        count = 0
        for i, l in enumerate(game["word"]):
            if l == letter:
                game["guessed"][i] = letter
                count += 1
                
        points_earned = game["current_value"] * count
        player["score"] += points_earned
        
        if "_" not in game["guessed"]:
            final_word = game['word']
            winner_name = player["name"]
            final_score = player["score"]
            comment = await get_comment("win")
            
            # Отправляем очки в единую экономику
            try:
                await db.economy.insert_one({
                    "game_type": "pole",
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "user_name": winner_name,
                    "score": final_score,
                    "date": datetime.now()
                })
            except Exception as e:
                logging.error(f"Ошибка БД в поле чудес: {e}")
            
            asyncio.create_task(unpin_and_delete_after_delay(callback.message.bot, chat_id, game["main_msg_id"]))
            del active_games[chat_id]
            
            await callback.message.edit_text(
                f"🏆 **ПОБЕДА!**\n"
                f"Слово: **{final_word}**\n\n"
                f"🎉 {winner_name} выигрывает со счетом {final_score} очков!\n\n"
                f"💬 **Ведущий**: _{comment}_",
                parse_mode="Markdown"
            )
            return await callback.answer()
            
        game["last_comment"] = await get_comment("letter_ok")
        await callback.answer(f"✅ Откройте букву {letter}! (+{points_earned} очков)")
    else:
        game["missed_letters"].add(letter)
        game["last_comment"] = await get_comment("letter_fail")
        await callback.answer(f"❌ Нет такой буквы!", show_alert=True)

    game["current_turn"] = None
    game["last_move_time"] = time.time()
    
    await update_main_message(callback.message.bot, chat_id)

@router.message(Command("топполе"))
async def show_top(message: types.Message):
    chat_id = message.chat.id
    args = message.text.split()
    days = None
    
    if len(args) > 1 and args[1].isdigit():
        days = int(args[1])
        
    match_stage = {"chat_id": chat_id}
    if days:
        match_stage["date"] = {"$gte": datetime.now() - timedelta(days=days)}
        
    try:
        cursor_wins = db.pole_stats.aggregate([
            {"$match": match_stage},
            {"$group": {"_id": "$user_id", "user_name": {"$first": "$user_name"}, "wins": {"$sum": 1}}},
            {"$sort": {"wins": -1}},
            {"$limit": 25}
        ])
        top_wins = await cursor_wins.to_list(length=25)
        
        cursor_score = db.pole_stats.aggregate([
            {"$match": match_stage},
            {"$group": {"_id": "$user_id", "user_name": {"$first": "$user_name"}, "total_score": {"$sum": "$score"}}},
            {"$sort": {"total_score": -1}},
            {"$limit": 25}
        ])
        top_score = await cursor_score.to_list(length=25)
        if not top_wins and not top_score:
            msg = await message.answer("📊 Статистика пока пуста.")
            asyncio.create_task(delete_after_delay(msg, 180))
            asyncio.create_task(delete_after_delay(message, 180))
            return

        period_text = f"за {days} дн." if days else "за всё время"
        
        text = f"🏆 **Топ игроков ({period_text})** 🏆\n\n"
        
        text += "🥇 **По победам:**\n"
        text += "```text\n"
        text += f"{'№':<3}| {'Имя':<15} | {'Поб.'}\n"
        text += "-" * 26 + "\n"
        for i, p in enumerate(top_wins, 1):
            name = p.get('user_name', 'Неизвестно')[:14]
            text += f"{i:<3}| {name:<15} | {p.get('wins')}\n"
        text += "```\n"
        
        text += "💎 **По очкам:**\n"
        text += "```text\n"
        text += f"{'№':<3}| {'Имя':<15} | {'Очки'}\n"
        text += "-" * 26 + "\n"
        for i, p in enumerate(top_score, 1):
            name = p.get('user_name', 'Неизвестно')[:14]
            text += f"{i:<3}| {name:<15} | {p.get('total_score')}\n"
        text += "```\n"
        
        msg = await message.answer(text, parse_mode="Markdown")
        asyncio.create_task(delete_after_delay(msg, 180))
        asyncio.create_task(delete_after_delay(message, 180))
        
    except Exception as e:
        logging.error(f"Ошибка команды /топполе: {e}")
        msg = await message.answer("Произошла ошибка при загрузке топа.")
        asyncio.create_task(delete_after_delay(msg, 10))
        asyncio.create_task(delete_after_delay(message, 10))
        
