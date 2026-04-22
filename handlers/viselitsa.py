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
from handlers.pole_chudes import unpin_and_delete_after_delay, delete_after_delay

router = Router()

active_hangman_games = {}
VOWELS = set("АЕЁИОУЫЭЮЯ")

# ASCII-арт стадий виселицы (7 стадий)
GALLOWS = [
    "```\n\n\n\n\n=====\n```",
    "```\n  +\n  |\n  |\n  |\n  |\n=====\n```",
    "```\n  +---+\n  |\n  |\n  |\n  |\n=====\n```",
    "```\n  +---+\n  |   |\n  |\n  |\n  |\n=====\n```",
    "```\n  +---+\n  |   |\n  |   O\n  |\n  |\n=====\n```",
    "```\n  +---+\n  |   |\n  |   O\n  |  /|\\\n  |\n=====\n```",
    "```\n  +---+\n  |   |\n  |   O\n  |  /|\\\n  |  / \\\n=====\n```"
]

MAX_ERRORS = 6

async def get_hangman_word(forbidden_list: str):
    prompt = (
        "Твоя задача — сгенерировать слово для игры 'Виселица'.\n"
        "1. Загадай ОДНО реально существующее русское слово (существительное, нарицательное, И.п., ед.ч.).\n"
        "2. Длина слова — СТРОГО ОТ 7 ДО 15 БУКВ.\n"
        "3. Напиши пугающую, мрачную или интригующую загадку-описание.\n"
        f"4. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать следующие слова (они уже были): {forbidden_list}.\n"
        "ОТВЕТ ДАЙ СТРОГО В ФОРМАТЕ: СЛОВО|ОПИСАНИЕ\n"
        "Пример ответа: СКЕЛЕТ|То, что остается, когда уходит всё наносное и мягкое."
    )
    try:
        res = await generate_with_retry(prompt, is_game=True)
        if res and "|" in res:
            parts = res.split("|", 1)
            raw_word = parts[0].strip().upper()
            word = re.sub(r'[^А-ЯЁ]', '', raw_word.split()[0])
            hint = parts[1].strip()
            if 7 <= len(word) <= 15:
                return word, hint
    except Exception as e:
        logging.error(f"Ошибка LLM в виселице: {e}")
    return "ЭКЗЕКУЦИЯ", "Высшая мера наказания или строгое исполнение алгоритма."

@router.message(Command("vis", "виселица"))
async def start_hangman(message: types.Message):
    chat_id = message.chat.id
    if chat_id in active_hangman_games:
        msg = await message.answer("✋ Палач уже занят, доиграйте текущую партию!")
        asyncio.create_task(delete_after_delay(msg, 5))
        return

    status_msg = await message.answer("⏳ Палач точит топор, подбираем слово...")

    # --- 1. ЧИТАЕМ ИСТОРИЮ СЛОВ ИЗ ОБЩЕЙ БАЗЫ ---
    try:
        cursor = db.pole_history.find().sort("date", -1).limit(100)
        history_docs = await cursor.to_list(length=100)
        recent_words_list = [d['word'] for d in history_docs]
        forbidden_list = ", ".join(recent_words_list) if recent_words_list else "пока нет"
    except Exception as e:
        logging.error(f"Ошибка при чтении истории из БД: {e}")
        forbidden_list = "пока нет"

    # --- 2. ГЕНЕРИРУЕМ СЛОВО С УЧЕТОМ ЗАПРЕТОВ ---
    word, hint = await get_hangman_word(forbidden_list)

    # --- 3. СОХРАНЯЕМ НОВОЕ СЛОВО В БАЗУ ---
    if word != "ЭКЗЕКУЦИЯ": # Спасательный круг не сохраняем
        try:
            await db.pole_history.insert_one({
                "word": word,
                "date": datetime.now()
            })
        except Exception as e:
            logging.error(f"Не удалось сохранить слово виселицы в историю: {e}")

    active_hangman_games[chat_id] = {
        "word": word,
        "guessed": ["_"] * len(word),
        "used_letters": set(),
        "missed_letters": set(),
        "errors": 0,
        "hint": hint,
        "players": {}, 
        "main_msg_id": status_msg.message_id,
        "current_turn": None,
        "last_move_time": 0,
        "timeout_task": None,
        "last_text": ""
    }
    
    await update_hangman_message(message.bot, chat_id)
    try: await status_msg.pin(disable_notification=True)
    except Exception: pass
    asyncio.create_task(delete_after_delay(message))

def get_hangman_kb(game):
    builder = InlineKeyboardBuilder()
    
    if game["current_turn"] is None:
        builder.button(text="✋ Взять ход", callback_data="hm_take_turn")
        builder.adjust(1)
        return builder.as_markup()
    
    all_used = game["used_letters"] | game["missed_letters"]
    # Е и Ё — разные буквы, обе в алфавите
    alphabet = [l for l in "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ" if l not in all_used]
    
    for char in alphabet:
        builder.button(text=char, callback_data=f"hm_cl_{char}")
        
    builder.adjust(6) 
    return builder.as_markup()

async def update_hangman_message(bot, chat_id):
    game = active_hangman_games.get(chat_id)
    if not game: return
    
    display = " ".join(game["guessed"])
    missed = ", ".join(sorted(game["missed_letters"])) or "нет"
    errors = game["errors"]
    
    scoreboard_lines = [f"👤 {p['name']}: {p['score']} очк." for p in game["players"].values() if p['score'] > 0]
    score_text = f"\n**Счёт игроков:**\n" + "\n".join(scoreboard_lines) + "\n━━━━━━━━━━━━━━━━━━━━" if scoreboard_lines else ""
    
    turn_status = "🟢 Ждем смельчака! Жми 'Взять ход'."
    if game["current_turn"] is not None:
        active_player = game["players"][game["current_turn"]]["name"]
        turn_status = f"🟡 Букву выбирает: **{active_player}**"

    text = (
        f"⚖️ **ВИСЕЛИЦА** ⚖️\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"**Загадка:** {game['hint']}\n\n"
        f"{GALLOWS[errors]}\n"
        f"**Слово:** `{display}`\n"
        f"**Ошибки ({errors}/{MAX_ERRORS}):** `{missed}`\n"
        f"━━━━━━━━━━━━━━━━━━━━{score_text}\n"
        f"{turn_status}"
    )
    
    if game.get("last_text") == text: return
    game["last_text"] = text

    try: 
        await bot.edit_message_text(
            chat_id=chat_id, 
            message_id=game["main_msg_id"], 
            text=text,
            reply_markup=get_hangman_kb(game),
            parse_mode="Markdown"
        )
    except Exception: 
        pass

async def hm_timeout_task(chat_id, user_id, bot):
    await asyncio.sleep(30) # Динамика: 30 секунд на раздумья
    game = active_hangman_games.get(chat_id)
    
    if game and game.get("current_turn") == user_id:
        game["current_turn"] = None
        game["last_move_time"] = time.time()
        
        await update_hangman_message(bot, chat_id)
        msg = await bot.send_message(chat_id, "⏳ Время вышло! Игрок задрожал от страха и пропустил ход.")
        asyncio.create_task(delete_after_delay(msg, 5))
    
    await update_hangman_message(message.bot, chat_id)
    try: await status_msg.pin(disable_notification=True)
    except Exception: pass
    asyncio.create_task(delete_after_delay(message))

@router.callback_query(F.data == "hm_take_turn")
async def hm_handle_take_turn(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    game = active_hangman_games.get(chat_id)
    if not game:
        return await callback.answer("Игра уже окончена!", show_alert=True)

    user_id = callback.from_user.id
    current_time = time.time()

    if game["current_turn"] is not None:
        return await callback.answer("Кто-то уже на эшафоте! Жди.", show_alert=True)

    if user_id not in game["players"]:
        game["players"][user_id] = {"name": callback.from_user.first_name, "score": 0}
    
    game["current_turn"] = user_id
    game["last_move_time"] = current_time

    if game["timeout_task"]: game["timeout_task"].cancel()
    game["timeout_task"] = asyncio.create_task(hm_timeout_task(chat_id, user_id, callback.message.bot))

    await callback.answer("Твой ход! Выбирай букву.")
    await update_hangman_message(callback.message.bot, chat_id)

@router.callback_query(F.data.startswith("hm_cl_"))
async def hm_handle_letter(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    game = active_hangman_games.get(chat_id)
    if not game:
        return await callback.answer("Игра окончена!")

    user_id = callback.from_user.id
    
    if game["current_turn"] != user_id:
        return await callback.answer("Сейчас не твой ход! ⏳", show_alert=True)

    if game["timeout_task"]:
        game["timeout_task"].cancel()
        game["timeout_task"] = None

    letter = callback.data.split("_")[2]
    player = game["players"][user_id]
    
    if letter in game["word"]:
        game["used_letters"].add(letter)
        count = 0
        for i, l in enumerate(game["word"]):
            if l == letter:
                game["guessed"][i] = letter
                count += 1
                
        # Награда: 100 очков за каждую правильную букву
        player["score"] += 100 * count
        
        if "_" not in game["guessed"]:
            # Победа! Выдаем бонус за спасение
            player["score"] += 500
            final_word = game['word']
            winner_name = player["name"]
            final_score = player["score"]
            
            # Отправляем очки в единую экономику
            try:
                await db.economy.insert_one({
                    "game_type": "hangman",
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "user_name": winner_name,
                    "score": final_score,
                    "date": datetime.now()
                })
            except Exception as e:
                logging.error(f"Ошибка БД в виселице: {e}")

            asyncio.create_task(unpin_and_delete_after_delay(callback.message.bot, chat_id, game["main_msg_id"]))
            del active_hangman_games[chat_id]
            
            await callback.message.edit_text(
                f"🎉 **ПОМИЛОВАНИЕ! СЛОВО ОТГАДАНО!**\n"
                f"Слово: **{final_word}**\n\n"
                f"Герой дня: {winner_name} (Заработал {final_score} очк.)",
                parse_mode="Markdown"
            )
            return await callback.answer()
            
        await callback.answer(f"✅ Верно! Открыта буква {letter}.")
        # Игрок сохраняет право хода, если угадал
        game["last_move_time"] = time.time()
        game["timeout_task"] = asyncio.create_task(hm_timeout_task(chat_id, user_id, callback.message.bot))
    else:
        game["missed_letters"].add(letter)
        game["errors"] += 1
        
        if game["errors"] >= MAX_ERRORS:
            final_word = game['word']
            asyncio.create_task(unpin_and_delete_after_delay(callback.message.bot, chat_id, game["main_msg_id"]))
            del active_hangman_games[chat_id]
            
            await callback.message.edit_text(
                f"💀 **ПРИГОВОР ПРИВЕДЕН В ИСПОЛНЕНИЕ** 💀\n"
                f"{GALLOWS[-1]}\n\n"
                f"Вы не смогли отгадать слово: **{final_word}**\n"
                f"Игра окончена.",
                parse_mode="Markdown"
            )
            return await callback.answer("Смерть...", show_alert=True)

        await callback.answer(f"❌ Ошибка! Строится виселица...", show_alert=True)
        # Ошибся — теряет ход
        game["current_turn"] = None
        game["last_move_time"] = time.time()

    await update_hangman_message(callback.message.bot, chat_id)

@router.message(Command("stop", "хватит"), lambda msg: msg.chat.id in active_hangman_games)
async def hm_stop_game(message: types.Message):
    chat_id = message.chat.id
    if chat_id in active_hangman_games:
        game = active_hangman_games[chat_id]
        if game["timeout_task"]: game["timeout_task"].cancel()
        del active_hangman_games[chat_id]
        asyncio.create_task(unpin_and_delete_after_delay(message.bot, chat_id, game["main_msg_id"]))
        await message.answer("🛑 Виселица разобрана. Расходимся.")
    else:
        ans = await message.answer("А мы никого и не вешаем! 🤷‍♂️")
        asyncio.create_task(delete_after_delay(ans, 5))
        