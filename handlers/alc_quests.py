import random
import datetime
import re
from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database import db
from handlers.alc_recipes import ELEMENTS, RECIPES
from ai_logic import try_groq, try_openrouter

router = Router()

# Кэш сложности крафта (тиров), чтобы не вычислять заново для каждого запроса
TIER_CACHE = {}

def get_item_tier(item, visited=None):
    """Рекурсивно высчитывает себестоимость (сложность) предмета."""
    if item in TIER_CACHE: return TIER_CACHE[item]
    if visited is None: visited = set()
    
    # Защита от потенциальных циклов (хотя в Doodle Alchemy их нет)
    if item in visited: return 10 
    visited.add(item)
    
    basics = {"a", "e", "f", "w"}
    if item in basics:
        TIER_CACHE[item] = 1
        return 1
        
    for (i1, i2), res in RECIPES.items():
        if res == item:
            tier = get_item_tier(i1, visited.copy()) + get_item_tier(i2, visited.copy())
            TIER_CACHE[item] = tier
            return tier
            
    # Если рецепт не найден (духи или базы), ставим высокий тир
    TIER_CACHE[item] = 10
    return 10

async def generate_daily_quests(user_id, player):
    guild_lvl = player.get("buildings", {}).get("guild", 0)
    
    num_quests = 3
    if guild_lvl in [1, 2]: num_quests = 4
    elif guild_lvl >= 3: num_quests = 5
    
    invalid_items = {"base_crystal", "active_base", "pack_s", "pack_m", "pack_l", "pack_xl", "recipe_scroll", "energy", "spirit_f", "spirit_w", "spirit_e", "spirit_a", "a", "e", "f", "w"}
    valid_items = [k for k in ELEMENTS.keys() if k not in invalid_items]
    
    chosen_items = random.sample(valid_items, num_quests)
    item_names = [ELEMENTS[k] for k in chosen_items]
    
    # Строгий промпт, чтобы ИИ не фантазировал дичь
    prompt = (
        "Ты суровый, лаконичный NPC в фэнтези игре (кузнец, крестьянин, стражник). Тебе нужны следующие предметы: "
        f"{', '.join(item_names)}.\n"
        "Напиши СТРОГО 1 короткое и логичное предложение для каждого предмета: зачем он тебе нужен. Без метафор, без бреда, без лишних слов.\n"
        "Формат:\n1. Мне нужен [Предмет], чтобы [адекватная причина].\n2. ..."
    )
    
    texts = []
    res, status = await try_groq(prompt, use_fast_model=True)
    if status != 200 or not res:
        res, status = await try_openrouter(prompt)
        
    if res and status == 200:
        lines = [line.strip() for line in res.split("\n") if re.match(r'^\d+\.', line.strip())]
        if len(lines) == num_quests:
            texts = [re.sub(r'^\d+\.\s*', '', line).strip() for line in lines]
            
    if len(texts) != num_quests:
        texts = [f"Срочный заказ из столицы. Оплачу по тарифу." for _ in item_names]
        
    quests = []
    for idx, item in enumerate(chosen_items):
        tier = get_item_tier(item)
        
        # Ребаланс наград. Никаких больших паков.
        pack = "recipe_scroll" if random.random() < 0.15 else "pack_s"
        
        # Интересные ресурсы вместо базовых, и жесткий лимит количества (от 1 до 3)
        bonus_res = random.choice(["stone", "sand", "glass", "clay", "energy"])
        bonus_amt = min(3, max(1, tier // 15)) 
        
        quests.append({
            "id": idx,
            "item": item,
            "text": texts[idx],
            "reward_pack": pack,
            "reward_res": bonus_res,
            "reward_amt": bonus_amt,
            "done": False
        })
        
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    daily_data = {"date": today_str, "quests": quests}
    
    await db.alchemy_players.update_one({"user_id": user_id}, {"$set": {"daily_quests": daily_data}})
    return daily_data

@router.callback_query(F.data == "quests_open")
async def open_quests(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await db.alchemy_players.find_one({"user_id": user_id})
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    
    daily_data = player.get("daily_quests", {})
    
    # Если сутки сменились — генерируем новые квесты
    if daily_data.get("date") != today_str:
        await callback.message.edit_text("⏳ Торговцы формулируют запросы... Дай пару секунд, связываюсь с гильдией.")
        daily_data = await generate_daily_quests(user_id, player)
        
    quests = daily_data.get("quests", [])
    
    text = "📜 **ДОСКА ОБЪЯВЛЕНИЙ**\n_Задания обновляются каждый день._\n\n"
    builder = InlineKeyboardBuilder()
    
    for q in quests:
        status = "✅ Выполнено" if q["done"] else "В процессе"
        i_name = ELEMENTS.get(q["item"], q["item"])
        p_name = ELEMENTS.get(q["reward_pack"], q["reward_pack"])
        r_name = ELEMENTS.get(q["reward_res"], q["reward_res"])
        
        text += f"🔹 **Задача:** {q['text']}\n"
        text += f"📦 **Требуется:** {i_name}\n"
        text += f"🎁 **Награда:** {p_name} + {r_name} (x{q['reward_amt']})\n"
        text += f"Статус: {status}\n\n"
        
        if not q["done"]:
            builder.button(text=f"Сдать {i_name}", callback_data=f"quest_turnin:{q['id']}")
            
    builder.button(text="🔙 В Штаб", callback_data="alc:base_return")
    builder.adjust(1)
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@router.callback_query(F.data.startswith("quest_turnin:"))
async def turn_in_quest(callback: types.CallbackQuery):
    q_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    
    player = await db.alchemy_players.find_one({"user_id": user_id})
    daily_data = player.get("daily_quests", {})
    quests = daily_data.get("quests", [])
    inv = player.get("inventory", {})
    
    quest = next((q for q in quests if q["id"] == q_id), None)
    if not quest or quest["done"]:
        return await callback.answer("Задание уже выполнено или не найдено!", show_alert=True)
        
    # Проверка наличия предмета
    if inv.get(quest["item"], 0) < 1:
        i_name = ELEMENTS.get(quest["item"], quest["item"])
        return await callback.answer(f"❌ У тебя нет предмета: {i_name}! Зайди в Лабораторию.", show_alert=True)
        
    # Списываем предмет
    inv[quest["item"]] -= 1
    if inv[quest["item"]] <= 0:
        del inv[quest["item"]]
        
    # Выдаем награду
    inv[quest["reward_pack"]] = inv.get(quest["reward_pack"], 0) + 1
    inv[quest["reward_res"]] = inv.get(quest["reward_res"], 0) + quest["reward_amt"]
    
    quest["done"] = True
    
    # Проверяем лимиты склада
    from handlers.alc_core import STORAGE_CAPACITY, get_used_slots
    max_slots = STORAGE_CAPACITY.get(player.get("buildings", {}).get("storage", 0), 20)
    if get_used_slots(inv) > max_slots:
         return await callback.answer("На складе нет места для награды! Освободи слоты.", show_alert=True)
    
    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {"inventory": inv, "daily_quests": daily_data}}
    )
    
    await callback.answer("🎉 Задание успешно сдано! Награда добавлена на склад.", show_alert=True)
    await open_quests(callback)

from aiogram.filters import Command
from config import ADMIN_ID

@router.message(Command("regen_quests"))
async def admin_regen_quests(message: types.Message):
    """Команда для владельца: принудительно перегенерировать квесты всем игрокам."""
    # Защита: реагируем только если команду пишет админ
    if message.from_user.id != ADMIN_ID: 
        return
    
    await message.answer("🔄 Запускаю принудительную генерацию квестов. Это может занять пару секунд...")
    
    # Ищем всех игроков в базе
    cursor = db.alchemy_players.find({})
    count = 0
    
    async for player in cursor:
        user_id = player["user_id"]
        # Вызываем твою функцию генерации из этого же файла
        await generate_daily_quests(user_id, player)
        count += 1
        
    await message.answer(f"✅ Готово! Свежие квесты успешно выданы {count} игрокам.")
    
    