import re
import asyncio
import random
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

from database import db
from ai_logic import get_expert_report
from config import get_msk_now

router = Router()


def parse_gossip_count(text: str | None, default: int = 50) -> int:
    if not text:
        return default
    match = re.search(r"(?:^|\s)(\d+)(?:\s|$)", text.strip())
    count = int(match.group(1)) if match else default
    return max(1, min(count, 3000))

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def split_text(text, max_len=4000):
    if not text: return []
    return [text[i:i+max_len] for i in range(0, len(text), max_len)]

async def run_expert_report(message: types.Message, persona: dict, count: int):
    """Общая функция для запуска генерации и отправки"""
    
    # Получаем фразу ожидания из настроек персонажа
    loading_data = persona.get('loading', 'Так, минуточку...')
    
    # Если в базе оказался список фраз, берем случайную. Иначе берем как есть.
    if isinstance(loading_data, list):
        loading_text = random.choice(loading_data) if loading_data else 'Так, минуточку...'
    else:
        loading_text = loading_data
        
    status_msg = await message.answer(loading_text)
    
    try:
        # 1. Сбор истории
        cursor = db.messages.find({"chat_id": message.chat.id}).sort("createdAt", -1).limit(count)
        messages = await cursor.to_list(length=count)
        
        if not messages:
            await status_msg.edit_text("Тут пусто, как в голове у депутата! Нечего обсуждать.")
            return

        messages.reverse()
        history_text = "\n".join([f"{m['user_name']}: {m['text']}" for m in messages])
        
        # 2. Генерация
        report = await get_expert_report(history_text, persona['p_id'])
        
        try: await status_msg.delete()
        except: pass

        if report:
            header = f"{persona['emoji']} **МНЕНИЕ ЭКСПЕРТА: {persona['name'].upper()}**\n{'-'*20}\n"
            full_content = header + report
            
            parts = split_text(full_content)
            for part in parts:
                try:
                    sent = await message.answer(part, parse_mode="Markdown")
                except TelegramBadRequest as e:
                    if "can't parse entities" in str(e).lower() or "bad request" in str(e).lower():
                        sent = await message.answer(part, parse_mode=None)
                    else:
                        raise e

                await db.messages.insert_one({
                    "message_id": sent.message_id,
                    "chat_id": message.chat.id,
                    "user_name": f"🤖 {persona['name']}",
                    "text": part,
                    "reactions": [],
                    "createdAt": get_msk_now()
                })
                await asyncio.sleep(0.5)
        else:
            await message.answer("Эксперт посмотрел на ваш чат и потерял дар речи. (Пустой ответ)")

    except Exception as e:
        await message.answer(f"⚠️ Ошибка связи с астралом: {str(e)[:100]}")

async def send_persona_selection(message: types.Message, count: int):
    """Выводит клавиатуру выбора эксперта с передачей нужного кол-ва сообщений"""
    all_personas = await db.personas.find().to_list(length=100)
    if not all_personas:
        return await message.answer("В базе нет ни одного эксперта. Проверь запуск init_personas!")
    
    builder = InlineKeyboardBuilder()
    for p in all_personas:
        # Вшиваем ID и количество в callback_data (максимум 64 байта в Telegram)
        builder.button(text=f"{p['emoji']} {p['name']}", callback_data=f"rep:{p['p_id']}:{count}")
    
    builder.adjust(2)
    await message.answer(
        f"📊 Будет собрана сводка по последним **{count}** сообщениям.\n\n"
        f"🏛 **ЗАЛ ДИВАННЫХ ЭКСПЕРТОВ**\nКто сегодня будет препарировать ваш чат?", 
        reply_markup=builder.as_markup()
    )

# --- КОМАНДЫ ---

# РУЛЕТКА И ВЫБОР (/вещай, /сплетни, /выбор)
@router.message(Command("gossip"))
@router.message(F.text.lower().regexp(r"^/?(вещай|сплетни|выбор)(\s+\d+)?$"))
async def gossip_cmd(message: types.Message):
    await send_persona_selection(message, parse_gossip_count(message.text))

@router.callback_query(F.data.startswith("rep:"))
async def persona_callback(callback: types.CallbackQuery):
    # Парсим callback_data: rep : p_id : count
    parts = callback.data.split(":")
    if len(parts) != 3:
        return await callback.answer("Ошибка данных кнопки.")
        
    p_id = parts[1]
    count = int(parts[2])
    
    persona = await db.personas.find_one({"p_id": p_id})
    
    if not persona:
        return await callback.answer("Эксперт ушел в запой (не найден).")
    
    # Меняем сообщение, чтобы убрать кнопки
    await callback.message.edit_text(f"✅ Выбран: **{persona['name']}** {persona['emoji']}\nГотовлю сводку за {count} сообщений...")
    await callback.answer()
    
    # Запускаем генерацию
    await run_expert_report(callback.message, persona, count)
    
