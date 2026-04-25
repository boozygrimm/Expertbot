import random
import datetime
import re
from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database import db
from handlers.alc_core import increment_action_counter
from handlers.alc_heritage import try_grant_random_lore_fragment
from handlers.alc_recipes import BATTLE_UNITS, ELEMENTS, RECIPES
from ai_logic import try_groq, try_openrouter

router = Router()

# Кэш сложности крафта (тиров), чтобы не вычислять заново для каждого запроса
TIER_CACHE = {}
INVALID_QUEST_ITEMS = {
    "base_crystal",
    "active_base",
    "pack_s",
    "pack_m",
    "pack_l",
    "pack_xl",
    "recipe_scroll",
    "energy",
    "spirit_f",
    "spirit_w",
    "spirit_e",
    "spirit_a",
    "a",
    "e",
    "f",
    "w",
    *BATTLE_UNITS.keys(),
}

QUEST_REWARD_PROFILES = {
    "supply": "📦 Снабжение",
    "research": "📜 Исследование",
    "workshop": "🔧 Мастерская",
}
QUEST_RARITIES = {
    "normal": "Обычный",
    "golden": "Золотой",
}


def get_pinned_quest_limit(guild_level: int) -> int:
    if guild_level >= 3:
        return 3
    if guild_level >= 2:
        return 2
    if guild_level >= 1:
        return 1
    return 0

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


def is_invalid_quest_item(item_id):
    return item_id in INVALID_QUEST_ITEMS


def get_valid_quest_items(exclude=None):
    excluded = set(exclude or ())
    return [item_id for item_id in ELEMENTS.keys() if item_id not in INVALID_QUEST_ITEMS and item_id not in excluded]


def build_fallback_quest_text(item_id):
    item_name = ELEMENTS.get(item_id, item_id)
    fallback_reasons = [
        f"Неси {item_name}. Прошлый экземпляр кто-то уже уронил в котёл.",
        f"Срочно нужен {item_name}, пока мастер снова не начал заменять его кирпичом.",
        f"Требуется {item_name}. Гильдия устала делать вид, что оно само появится.",
        f"Притащи {item_name}. Без него заказ выглядит как позор, а не ремесло.",
        f"Нужен {item_name}. Клиент нервный, а запасов опять ровно ноль.",
        f"Добудь {item_name}. Иначе придётся объяснять провал бухгалтеру и алхимику одновременно.",
    ]
    return random.choice(fallback_reasons)


def choose_reward_profile(tier: int) -> str:
    roll = random.random()
    if tier >= 14:
        if roll < 0.42:
            return "research"
        if roll < 0.76:
            return "workshop"
        return "supply"
    if tier >= 8:
        if roll < 0.38:
            return "workshop"
        if roll < 0.68:
            return "research"
        return "supply"
    if roll < 0.5:
        return "supply"
    if roll < 0.78:
        return "workshop"
    return "research"


def choose_quest_rarity(guild_level: int) -> str:
    chance = 0.08
    if guild_level >= 2:
        chance += 0.03
    if guild_level >= 3:
        chance += 0.03
    return "golden" if random.random() < chance else "normal"


def roll_quest_rewards(tier: int, guild_level: int, rarity: str) -> dict:
    profile = choose_reward_profile(tier)
    reward_pack = "pack_s"
    reward_res = "stone"
    reward_amt = 1
    reward_bonus_item = None
    reward_bonus_amt = 0
    golden = rarity == "golden"

    if profile == "supply":
        pack_roll = random.random()
        if tier >= 16 and pack_roll < (0.14 if golden else 0.04):
            reward_pack = "pack_m"
        elif pack_roll < (0.26 if golden else 0.14):
            reward_pack = "recipe_scroll"
        reward_res = random.choice(["stone", "sand", "wood", "clay", "glass"])
        reward_amt = random.randint(2, 4 if tier >= 8 else 3) + (1 if golden else 0)
        if random.random() < (0.45 if golden else 0.24):
            reward_bonus_item = random.choice(["stone", "sand", "wood", "clay"])
            reward_bonus_amt = 1

    elif profile == "research":
        pack_roll = random.random()
        if tier >= 18 and pack_roll < (0.14 if golden else 0.04):
            reward_pack = "pack_m"
        elif pack_roll < (0.65 if golden else 0.45):
            reward_pack = "recipe_scroll"
        reward_res = random.choice(["energy", "glass", "paper", "book", "life"] if tier >= 16 else ["energy", "glass", "clay"])
        reward_amt = (1 if reward_res in {"paper", "book", "life"} else random.randint(1, 2)) + (1 if golden and reward_res not in {"paper", "book", "life"} else 0)
        if random.random() < (0.34 if golden else 0.18):
            reward_bonus_item = random.choice(["recipe_scroll", "energy"])
            reward_bonus_amt = 1

    else:
        pack_roll = random.random()
        if tier >= 18 and pack_roll < (0.09 if golden else 0.03):
            reward_pack = "pack_l"
        elif tier >= 10 and pack_roll < (0.30 if golden else 0.16):
            reward_pack = "pack_m"
        elif pack_roll < (0.34 if golden else 0.22):
            reward_pack = "recipe_scroll"
        reward_res = random.choice(["glass", "brick", "metal", "energy"] if tier >= 8 else ["glass", "clay", "wood"])
        reward_amt = random.randint(1, 3 if tier >= 8 else 2) + (1 if golden else 0)
        if random.random() < (0.30 if golden else 0.16):
            reward_bonus_item = random.choice(["glass", "brick", "metal", "energy"])
            reward_bonus_amt = 1

    if golden and guild_level >= 3 and reward_bonus_item is None and random.random() < 0.18:
        reward_bonus_item = "energy"
        reward_bonus_amt = 1

    return {
        "rarity": rarity,
        "reward_profile": profile,
        "reward_pack": reward_pack,
        "reward_res": reward_res,
        "reward_amt": reward_amt,
        "reward_bonus_item": reward_bonus_item,
        "reward_bonus_amt": reward_bonus_amt,
    }


def build_quest_entry(item_id, quest_id, text=None, *, guild_level: int = 0, rarity: str | None = None):
    tier = get_item_tier(item_id)
    actual_rarity = rarity or choose_quest_rarity(guild_level)
    rewards = roll_quest_rewards(tier, guild_level, actual_rarity)
    return {
        "id": quest_id,
        "item": item_id,
        "text": text or build_fallback_quest_text(item_id),
        **rewards,
        "done": False,
        "pinned": False,
    }


async def sanitize_daily_quests(user_id, player, daily_data):
    quests = daily_data.get("quests", [])
    if not quests:
        return daily_data, False

    used_items = {quest.get("item") for quest in quests if not is_invalid_quest_item(quest.get("item"))}
    replacements = get_valid_quest_items(used_items)
    changed = False

    for idx, quest in enumerate(quests):
        quest.setdefault("pinned", False)
        item_id = quest.get("item")
        if quest.get("done") or not is_invalid_quest_item(item_id):
            continue
        if not replacements:
            break
        new_item = random.choice(replacements)
        replacements.remove(new_item)
        used_items.add(new_item)
        quests[idx] = build_quest_entry(new_item, quest.get("id", idx), guild_level=player.get("buildings", {}).get("guild", 0))
        changed = True

    pin_limit = get_pinned_quest_limit(player.get("buildings", {}).get("guild", 0))
    pinned_kept = 0
    for quest in quests:
        if quest.get("done"):
            if quest.get("pinned"):
                quest["pinned"] = False
                changed = True
            continue
        if not quest.get("pinned"):
            continue
        if pinned_kept < pin_limit:
            pinned_kept += 1
        else:
            quest["pinned"] = False
            changed = True

    if changed:
        daily_data["quests"] = quests
        await db.alchemy_players.update_one({"user_id": user_id}, {"$set": {"daily_quests": daily_data}})
    return daily_data, changed


def build_quest_pool(valid_items, count):
    if count <= 0:
        return []
    if count >= len(valid_items):
        return list(valid_items)
    return random.sample(valid_items, count)

async def generate_daily_quests(user_id, player):
    guild_lvl = player.get("buildings", {}).get("guild", 0)
    pin_limit = get_pinned_quest_limit(guild_lvl)
    
    num_quests = 3
    if guild_lvl in [1, 2]: num_quests = 4
    elif guild_lvl >= 3: num_quests = 5

    previous_data = player.get("daily_quests", {})
    previous_quests = previous_data.get("quests", [])
    pinned_quests = []
    used_items = set()
    for quest in previous_quests:
        if quest.get("done") or not quest.get("pinned"):
            continue
        if is_invalid_quest_item(quest.get("item")):
            continue
        if len(pinned_quests) >= pin_limit:
            break
        item_id = quest.get("item")
        if item_id in used_items:
            continue
        quest_copy = dict(quest)
        quest_copy["done"] = False
        quest_copy["pinned"] = True
        quest_copy.setdefault("rarity", "normal")
        quest_copy.setdefault("reward_profile", "supply")
        pinned_quests.append(quest_copy)
        used_items.add(item_id)

    valid_items = get_valid_quest_items(used_items)
    fresh_count = max(0, num_quests - len(pinned_quests))
    chosen_items = build_quest_pool(valid_items, fresh_count)
    item_names = [ELEMENTS[k] for k in chosen_items]
    
    prompt = (
        "Ты пишешь тексты для доски заданий в ироничной фэнтези-алхимической игре. "
        "Мир живой, с ремесленниками, стражей, жуликами, монстрами, котлами и вечным бардаком на складе. "
        "Тексты должны быть чуть веселее обычного, но без абсурда и кринжа. "
        f"Тебе нужны следующие предметы: {', '.join(item_names)}.\n"
        "Для каждого предмета напиши РОВНО одно короткое, понятное и слегка смешное предложение, зачем он нужен. "
        "Причина должна быть бытовой, ремесленной, военной или торговой. "
        "Без метафизики, без длинных вступлений, без бреда, без повторов одной и той же шутки. "
        "В каждом предложении назови сам предмет.\n"
        "Хороший тон: сухая ирония, лёгкое раздражение, рабочий бардак.\n"
        "Плохой тон: бессвязный сюр, пафос, мемы, современный сленг.\n"
        "Формат ответа строго такой:\n"
        "1. [короткое предложение]\n"
        "2. [короткое предложение]"
    )
    
    texts = []
    if fresh_count > 0:
        res, status = await try_groq(prompt, use_fast_model=True)
        if status != 200 or not res:
            res, status = await try_openrouter(prompt)

        if res and status == 200:
            lines = [line.strip() for line in res.split("\n") if re.match(r'^\d+\.', line.strip())]
            if len(lines) == fresh_count:
                texts = [re.sub(r'^\d+\.\s*', '', line).strip() for line in lines]

    if len(texts) != fresh_count:
        texts = [f"Срочный заказ из столицы. Оплачу по тарифу." for _ in item_names]

    fresh_quests = [build_quest_entry(item, idx, texts[idx], guild_level=guild_lvl) for idx, item in enumerate(chosen_items)]
    quests = pinned_quests + fresh_quests
    for idx, quest in enumerate(quests):
        quest["id"] = idx
        
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
    else:
        daily_data, _ = await sanitize_daily_quests(user_id, player, daily_data)
        
    quests = daily_data.get("quests", [])
    
    pin_limit = get_pinned_quest_limit(player.get("buildings", {}).get("guild", 0))
    pinned_count = sum(1 for q in quests if q.get("pinned") and not q.get("done"))
    text = "📜 **ДОСКА ОБЪЯВЛЕНИЙ**\n_Задания обновляются каждый день._\n\n"
    if pin_limit > 0:
        text += f"📌 Закреплено: {pinned_count}/{pin_limit}\n\n"
    builder = InlineKeyboardBuilder()
    
    for q in quests:
        q.setdefault("pinned", False)
        q.setdefault("rarity", "normal")
        q.setdefault("reward_profile", "supply")
        status = "✅ Выполнено" if q["done"] else "В процессе"
        i_name = ELEMENTS.get(q["item"], q["item"])
        p_name = ELEMENTS.get(q["reward_pack"], q["reward_pack"])
        r_name = ELEMENTS.get(q["reward_res"], q["reward_res"])
        profile_name = QUEST_REWARD_PROFILES.get(q.get("reward_profile", "supply"), "📦 Снабжение")
        rarity_name = QUEST_RARITIES.get(q.get("rarity", "normal"), "Обычный")
        rarity_icon = "✨" if q.get("rarity") == "golden" else "🔹"
        bonus_name = ELEMENTS.get(q.get("reward_bonus_item"), q.get("reward_bonus_item")) if q.get("reward_bonus_item") else ""
        pin_mark = " 📌" if q.get("pinned") and not q.get("done") else ""
        reward_line = f"{p_name} + {r_name} (x{q['reward_amt']})"
        if q.get("reward_bonus_item") and q.get("reward_bonus_amt", 0) > 0:
            reward_line += f" + {bonus_name} (x{q['reward_bonus_amt']})"
        
        text += f"{rarity_icon} **Задача{pin_mark}:** {q['text']}\n"
        text += f"📦 **Требуется:** {i_name}\n"
        text += f"🏷 **Редкость:** {rarity_name}\n"
        text += f"🎯 **Профиль:** {profile_name}\n"
        text += f"🎁 **Награда:** {reward_line}\n"
        text += f"Статус: {status}\n\n"
        
        if not q["done"]:
            builder.button(text=f"Сдать {i_name}", callback_data=f"quest_turnin:{q['id']}")
            if pin_limit > 0:
                pin_text = f"📌 Снять {i_name}" if q.get("pinned") else f"📌 Закрепить {i_name}"
                builder.button(text=pin_text, callback_data=f"quest_pin:{q['id']}")
            
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
    was_pinned = False
    was_golden = False
    
    quest = next((q for q in quests if q["id"] == q_id), None)
    if not quest or quest["done"]:
        return await callback.answer("Задание уже выполнено или не найдено!", show_alert=True)
    if is_invalid_quest_item(quest["item"]):
        daily_data, _ = await sanitize_daily_quests(user_id, player, daily_data)
        await callback.answer("Это задание устарело после обновления. Доска уже обновлена.", show_alert=True)
        return await open_quests(callback)
        
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
    if quest.get("reward_bonus_item") and quest.get("reward_bonus_amt", 0) > 0:
        inv[quest["reward_bonus_item"]] = inv.get(quest["reward_bonus_item"], 0) + quest["reward_bonus_amt"]
    
    was_pinned = bool(quest.get("pinned"))
    was_golden = quest.get("rarity") == "golden"
    quest["done"] = True
    quest["pinned"] = False
    found_fragment = await try_grant_random_lore_fragment(player, chance=0.5)
    
    # Проверяем лимиты склада
    from handlers.alc_core import STORAGE_CAPACITY, get_used_slots
    max_slots = STORAGE_CAPACITY.get(player.get("buildings", {}).get("storage", 0), 20)
    if get_used_slots(inv) > max_slots:
         return await callback.answer("На складе нет места для награды! Освободи слоты.", show_alert=True)

    counters = increment_action_counter(player, "quests_completed")
    if was_pinned:
        counters = increment_action_counter(player, "quests_pinned_completed")
    if was_golden:
        counters = increment_action_counter(player, "quests_golden_completed")
    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {"inventory": inv, "daily_quests": daily_data, "action_counters": counters, "lore_fragments": player.get("lore_fragments", {}), "lore_fragment_progress": player.get("lore_fragment_progress", {})}}
    )
    
    answer_text = "🎉 Задание успешно сдано! Награда добавлена на склад."
    if found_fragment:
        answer_text += f"\n📜 Найден обрывок: {found_fragment['title']}."
    await callback.answer(answer_text, show_alert=True)
    await open_quests(callback)


@router.callback_query(F.data.startswith("quest_pin:"))
async def toggle_pin_quest(callback: types.CallbackQuery):
    q_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    player = await db.alchemy_players.find_one({"user_id": user_id})
    daily_data = player.get("daily_quests", {})
    quests = daily_data.get("quests", [])
    quest = next((q for q in quests if q["id"] == q_id), None)
    if not quest or quest.get("done"):
        return await callback.answer("Это задание уже нельзя закрепить.", show_alert=True)

    pin_limit = get_pinned_quest_limit(player.get("buildings", {}).get("guild", 0))
    if pin_limit <= 0:
        return await callback.answer("Для закрепления нужна Гильдия хотя бы 1 уровня.", show_alert=True)

    if quest.get("pinned"):
        quest["pinned"] = False
        action_text = "📌 Закрепление снято."
    else:
        pinned_count = sum(1 for item in quests if item.get("pinned") and not item.get("done"))
        if pinned_count >= pin_limit:
            return await callback.answer(f"Лимит закрепления: {pin_limit}.", show_alert=True)
        quest["pinned"] = True
        counters = increment_action_counter(player, "quests_pinned")
        player["action_counters"] = counters
        action_text = "📌 Задание закреплено до следующего обновления."
    update_fields = {"daily_quests": daily_data}
    if quest.get("pinned"):
        update_fields["action_counters"] = player.get("action_counters", {})
    await db.alchemy_players.update_one({"user_id": user_id}, {"$set": update_fields})
    await callback.answer(action_text, show_alert=True)
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
    
    
