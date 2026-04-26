import asyncio
import random
import time
from copy import deepcopy
from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import db
from config import ADMIN_ID
from handlers.pole_chudes import delete_after_delay
from handlers.alc_fighters import FIGHTER_SCHEMA_VERSION, normalize_player_fighters
from handlers.alc_recipes import BATTLE_UNITS, ELEMENTS, RECIPES, is_battle_unit

router = Router()

# ==================== КОНСТАНТЫ ====================

COOLDOWN = 4 * 3600 # 4 часа
STORAGE_CAPACITY = {0: 20, 1: 50, 2: 150, 3: 350}
STARTER_DISCOVERIES = ["lava", "stone", "steam", "swamp", "life", "algae", "moss", "bacteria"]
STARTER_WORLD_REWARD_POOL = ["spirit_f", "spirit_w", "spirit_e", "spirit_a"]
STARTER_BIOME_SLOTS = {
    "matter": {
        "lava": {"label": "🌋 Лавовое ядро", "absurd": 2, "capacity": 1},
        "stone": {"label": "🪨 Каменное ядро", "absurd": 0, "capacity": 2},
    },
    "medium": {
        "steam": {"label": "☁️ Паровая среда", "absurd": 1, "capacity": 1},
        "swamp": {"label": "🐸 Болотная среда", "absurd": 0, "capacity": 2},
    },
    "life": {
        "bacteria": {"label": "🦠 Бактериальная жизнь", "absurd": 1, "capacity": 1},
        "moss": {"label": "🌿 Моховая жизнь", "absurd": 0, "capacity": 2},
    },
}
WORLD_ARCHETYPES = {
    ("stone", "swamp", "moss"): {
        "id": "nursery",
        "name": "🌱 Колыбельная чаща",
        "role": "опора",
        "capacity_bonus": 0,
        "support_bonus": 2,
        "strain_cost": 1,
        "bonuses": {"farm_yield": 1, "aero_yield": 1, "river_bonus": 2},
        "humor": "укоризненно смотрит на хаотичных соседей и держит мир в тонусе",
    },
    ("stone", "steam", "bacteria"): {
        "id": "forge",
        "name": "⚙️ Паровой шов",
        "role": "промышленный",
        "capacity_bonus": 1,
        "support_bonus": 1,
        "strain_cost": 2,
        "bonuses": {"mine_yield": 1, "factory_yield": 1, "mountains_bonus": 2, "cave_bonus": 2},
        "humor": "с шипением скрепляет реальность промышленным упрямством",
    },
    ("lava", "swamp", "moss"): {
        "id": "wildfire",
        "name": "🌋 Цветущий разлом",
        "role": "добывающий",
        "capacity_bonus": 1,
        "support_bonus": 0,
        "strain_cost": 3,
        "bonuses": {"volcano_bonus": 3, "factory_yield": 1},
        "humor": "живёт вопреки здравому смыслу и пахнет авантюрой",
    },
    ("lava", "steam", "bacteria"): {
        "id": "volatile",
        "name": "💥 Шипящий кошмар",
        "role": "нестабильный",
        "capacity_bonus": 2,
        "support_bonus": -1,
        "strain_cost": 5,
        "bonuses": {"global_loot_bonus": 2, "rare_find_bonus": 0.05},
        "humor": "очень хочет взорваться, но пока ограничивается угрозами",
    },
}
DEFAULT_WORLD_ARCHETYPE = {
    "id": "patchwork",
    "name": "🧩 Лоскутный карман",
    "role": "универсальный",
    "capacity_bonus": 0,
    "support_bonus": 0,
    "strain_cost": 2,
    "bonuses": {},
    "humor": "держится на алхимической изоленте и сомнительных обещаниях",
}
WORLD_FOCUS_OPTIONS = {
    "tech": {
        "item": "electricity",
        "label": "⚡ Технофокус",
        "capacity_bonus": 1,
        "support_bonus": 0,
        "strain_delta": 1,
        "bonuses": {"factory_yield": 1, "cave_bonus": 1, "global_loot_bonus": 1},
    },
    "lore": {
        "item": "book",
        "label": "📖 Лор-фокус",
        "capacity_bonus": 0,
        "support_bonus": 1,
        "strain_delta": 0,
        "bonuses": {"rare_find_bonus": 0.03},
    },
    "war": {
        "item": "weapon",
        "label": "🗡 Военный фокус",
        "capacity_bonus": 1,
        "support_bonus": 0,
        "strain_delta": 1,
        "bonuses": {"mountains_bonus": 2, "volcano_bonus": 1, "global_loot_bonus": 1},
    },
    "wild": {
        "item": "forest",
        "label": "🌲 Дикий фокус",
        "capacity_bonus": 0,
        "support_bonus": 1,
        "strain_delta": 0,
        "bonuses": {"river_bonus": 2, "farm_yield": 1, "aero_yield": 1},
    },
}
WORLD_TIER_LABELS = {
    1: "микробиом",
    2: "малый биом",
    3: "средний биом",
    4: "большой биом",
    5: "огромный биом",
}
WORLD_SUPPORT_COSTS = {
    0: {"stone": 6, "wood": 4, "energy": 1},
    1: {"stone": 10, "glass": 3, "energy": 2},
    2: {"brick": 8, "metal": 4, "energy": 3},
    3: {"glass": 8, "metal": 6, "energy": 4},
    4: {"diamond": 1, "glass": 10, "energy": 6},
}
DEFAULT_ACTION_COUNTERS = {
    "expeditions_started": 0,
    "expeditions_completed": 0,
    "expeditions_failed": 0,
    "expeditions_retreats": 0,
    "quests_completed": 0,
    "quests_pinned": 0,
    "quests_pinned_completed": 0,
    "quests_golden_completed": 0,
    "raids_prepared": 0,
    "raids_started": 0,
    "raids_won": 0,
    "raids_lost": 0,
    "defenses_triggered": 0,
    "captives_taken": 0,
    "captives_recruited": 0,
    "captives_recycled": 0,
    "production_lines_started": 0,
    "robberies_suffered": 0,
}

DEFAULT_PLAYER_TEMPLATE = {
    "inventory": {},
    "discovered": [],
    "lore_fragments": {},
    "active_stands": [None],
    "claimed_legacy_rewards": [],
    "lore_fragment_progress": {"date": "", "found": 0},
    "last_heritage_income": "",
    "last_mine": 0,
    "tutorial_step": 0,
    "stats": {"stamina": 1, "luck": 1},
    "buildings": {"storage": 0, "lab": 0, "altar": 0, "guild": 0, "barracks": 0, "fort": 0, "heritage_center": 1},
    "barracks_report": {},
    "last_barracks_drain": "",
    "fighters": [],
    "hall_of_fame": [],
    "fighter_name_pending": None,
    "fighter_schema_version": FIGHTER_SCHEMA_VERSION,
    "active_expeditions": [],
    "raid_squad": {"fighter_ids": [], "fighter_snapshots": [], "energy_invested": 0, "created_at": 0},
    "fort_defense": {"moat": None, "catapult": None, "aura": None},
    "captives": [],
    "action_counters": DEFAULT_ACTION_COUNTERS,
    "raid_history": {},
    "raid_protection_until": 0,
    "worlds": [],
    "world_failures": 0,
    "world_tutorial": {"active": False, "created": False},
    "world_support_level": 0,
}

# Таблица выпадения из лутбоксов
# Убрали несуществующие magic и golem, добавили шанс на всех духов в pack_xl
PACK_LOOT = {
    "pack_s": {"basics": (8, 10), "bonus_chance": 0.15, "bonus_items": ["stone", "sand", "steam", "lava"], "bonus_amount": (1, 2)},
    "pack_m": {"basics": (18, 24), "bonus_chance": 0.35, "bonus_items": ["metal", "brick", "glass", "clay"], "bonus_amount": (2, 3)},
    "pack_l": {"basics": (40, 55), "bonus_chance": 0.60, "bonus_items": ["energy", "life", "mechanism"], "bonus_amount": (3, 5)},
    "pack_xl": {"basics": (90, 120), "bonus_chance": 0.85, "bonus_items": ["spirit_f", "spirit_w", "spirit_e", "spirit_a"], "bonus_amount": (4, 7)}
}

# ==================== БАЗОВЫЕ ФУНКЦИИ ====================

def get_used_slots(inventory: dict) -> int:
    """Считает количество уникальных видов предметов на складе"""
    from handlers.alc_base import HIDDEN_ITEMS
    return len([k for k, v in inventory.items() if v > 0 and k not in HIDDEN_ITEMS])


def get_heritage_slots(level: int) -> int:
    return max(1, min(5, int(level or 1)))


def ensure_world_state(player: dict) -> tuple[list[dict], bool, dict]:
    worlds = player.get("worlds", [])
    if not isinstance(worlds, list):
        worlds = []

    normalized_worlds: list[dict] = []
    for idx, world in enumerate(worlds, start=1):
        if not isinstance(world, dict):
            continue
        capacity = max(0, int(world.get("capacity", 0) or 0))
        normalized_worlds.append(
            {
                "id": str(world.get("id") or f"world_{idx}"),
                "name": str(world.get("name") or f"Карман {idx}"),
                "description": str(world.get("description") or ""),
                "tier": max(1, int(world.get("tier", 1) or 1)),
                "capacity": capacity,
                "absurdity": max(0, int(world.get("absurdity", 0) or 0)),
                "support_bonus": int(world.get("support_bonus", 0) or 0),
                "strain_cost": max(0, int(world.get("strain_cost", 0) or 0)),
                "status": str(world.get("status") or "stable"),
                "humor": str(world.get("humor") or ""),
                "archetype": str(world.get("archetype") or "patchwork"),
                "role": str(world.get("role") or "универсальный"),
                "focus": str(world.get("focus") or ""),
                "focus_label": str(world.get("focus_label") or ""),
                "slots": {
                    "matter": str(((world.get("slots") or {}).get("matter")) or ""),
                    "medium": str(((world.get("slots") or {}).get("medium")) or ""),
                    "life": str(((world.get("slots") or {}).get("life")) or ""),
                },
                "starter": bool(world.get("starter", False)),
            }
        )

    world_tutorial = player.get("world_tutorial", {})
    normalized_tutorial = {
        "active": bool(world_tutorial.get("active", False)),
        "created": bool(world_tutorial.get("created", False)),
    }
    # Второй элемент кортежа сохранён для совместимости со старыми вызовами,
    # но отдельного legacy-режима для мира больше нет.
    is_legacy = False
    return normalized_worlds, is_legacy, normalized_tutorial


def get_world_capacity_total(player: dict) -> int:
    worlds, _, _ = ensure_world_state(player)
    return sum(max(0, int(world.get("capacity", 0) or 0)) for world in worlds)


def get_world_capacity_used(player: dict) -> int:
    buildings = player.get("buildings", {})
    used = 0
    for building_id, level in buildings.items():
        if building_id == "heritage_center":
            used += max(0, int(level or 0) - 1)
            continue
        used += max(0, int(level or 0) - 1)
    return used


def get_world_capacity_left(player: dict) -> int:
    return get_world_capacity_total(player) - get_world_capacity_used(player)


def get_world_support_level(player: dict) -> int:
    return max(0, int(player.get("world_support_level", 0) or 0))


def get_world_strain(player: dict) -> int:
    worlds, _, _ = ensure_world_state(player)
    strain = 0
    for world in worlds:
        strain += max(1, int(world.get("strain_cost", 0) or 0))
    if len(worlds) > 1:
        strain += len(worlds) - 1
    return strain


def get_world_stability_limit(player: dict) -> int:
    worlds, _, _ = ensure_world_state(player)
    buildings = player.get("buildings", {})
    support_bonus = sum(int(world.get("support_bonus", 0) or 0) for world in worlds)
    return 5 + get_world_support_level(player) * 2 + support_bonus + max(0, int(buildings.get("fort", 0) or 0)) + max(0, int(buildings.get("heritage_center", 1) or 1) - 1)


def get_world_strain_left(player: dict) -> int:
    return get_world_stability_limit(player) - get_world_strain(player)


def is_world_overloaded(player: dict) -> bool:
    return get_world_strain(player) > get_world_stability_limit(player)


def get_world_support_cost(player: dict) -> dict[str, int]:
    level = get_world_support_level(player)
    return dict(WORLD_SUPPORT_COSTS.get(level, {"diamond": 1, "metal": 10, "glass": 12, "energy": 8}))


def get_world_effects(player: dict) -> dict[str, float]:
    worlds, _, _ = ensure_world_state(player)
    if not worlds:
        return {}

    effects: dict[str, float] = {}
    for world in worlds:
        slots = world.get("slots", {})
        key = (slots.get("matter", ""), slots.get("medium", ""), slots.get("life", ""))
        archetype = WORLD_ARCHETYPES.get(key, DEFAULT_WORLD_ARCHETYPE)
        bonuses = archetype.get("bonuses", {})
        for bonus_id, bonus_value in bonuses.items():
            effects[bonus_id] = effects.get(bonus_id, 0) + bonus_value
        focus_id = str(world.get("focus") or "")
        if focus_id in WORLD_FOCUS_OPTIONS:
            for bonus_id, bonus_value in WORLD_FOCUS_OPTIONS[focus_id].get("bonuses", {}).items():
                effects[bonus_id] = effects.get(bonus_id, 0) + bonus_value
    return effects


def get_world_role_counts(player: dict) -> dict[str, int]:
    worlds, _, _ = ensure_world_state(player)
    if not worlds:
        return {}

    counts: dict[str, int] = {}
    for world in worlds:
        role = str(world.get("role") or "универсальный")
        counts[role] = counts.get(role, 0) + 1
    return counts


def get_world_focus_counts(player: dict) -> dict[str, int]:
    worlds, _, _ = ensure_world_state(player)
    if not worlds:
        return {}
    counts: dict[str, int] = {}
    for world in worlds:
        focus_id = str(world.get("focus") or "")
        if not focus_id:
            continue
        counts[focus_id] = counts.get(focus_id, 0) + 1
    return counts


def check_world_role_requirements(player: dict, requirements: dict[str, int] | None) -> tuple[bool, str | None]:
    if not requirements:
        return True, None

    worlds, _, _ = ensure_world_state(player)
    if not worlds:
        return False, "Нужны подходящие биомы мира. Пока под ногами только пустота."

    counts = get_world_role_counts(player)
    missing_parts = []
    for role, required in requirements.items():
        current = counts.get(role, 0)
        if current < required:
            missing_parts.append(f"{role}: {current}/{required}")
    if missing_parts:
        return False, "Не хватает профильных биомов: " + ", ".join(missing_parts) + "."
    return True, None


def check_world_focus_requirements(player: dict, requirements: dict[str, int] | None) -> tuple[bool, str | None]:
    if not requirements:
        return True, None

    worlds, _, _ = ensure_world_state(player)
    if not worlds:
        return False, "Нужны зрелые биомы с нужным фокусом."

    counts = get_world_focus_counts(player)
    missing_parts = []
    for focus_id, required in requirements.items():
        current = counts.get(focus_id, 0)
        if current < required:
            label = WORLD_FOCUS_OPTIONS.get(focus_id, {}).get("label", focus_id)
            missing_parts.append(f"{label}: {current}/{required}")
    if missing_parts:
        return False, "Не хватает зрелых фокусов мира: " + ", ".join(missing_parts) + "."
    return True, None


def get_building_world_capacity_cost(building_id: str, next_level: int) -> int:
    if next_level <= 1:
        return 0
    if building_id == "heritage_center":
        return 1
    return 1


def can_upgrade_with_world_capacity(player: dict, building_id: str, next_level: int) -> tuple[bool, str | None]:
    worlds, _, _ = ensure_world_state(player)
    if is_world_overloaded(player):
        left = get_world_strain_left(player)
        return False, f"Мир уже трещит по швам: перегруз {abs(left)}. Сначала укрепи каркас Вселенной."
    required = get_building_world_capacity_cost(building_id, next_level)
    if required <= 0:
        return True, None
    if not worlds:
        return False, "Сначала стабилизируй хотя бы один биом. Без него мир ещё не даёт новой вместимости."

    left = get_world_capacity_left(player)
    if left >= required:
        return True, None
    return False, f"Не хватает вместимости мира: нужно {required}, свободно {max(0, left)}."


def build_starter_world_preview(slots: dict[str, str], focus_id: str | None = None) -> tuple[str, str, int, int, str]:
    matter = STARTER_BIOME_SLOTS["matter"][slots["matter"]]
    medium = STARTER_BIOME_SLOTS["medium"][slots["medium"]]
    life = STARTER_BIOME_SLOTS["life"][slots["life"]]
    archetype_key = (slots["matter"], slots["medium"], slots["life"])
    archetype = WORLD_ARCHETYPES.get(archetype_key, DEFAULT_WORLD_ARCHETYPE)
    focus = WORLD_FOCUS_OPTIONS.get(focus_id or "")
    capacity = matter["capacity"] + medium["capacity"] + life["capacity"] + archetype["capacity_bonus"] + (focus["capacity_bonus"] if focus else 0)
    absurdity = matter["absurd"] + medium["absurd"] + life["absurd"]
    tier = 1 if capacity <= 4 else 2
    name = archetype["name"]
    humor = (
        archetype["humor"]
        if archetype["id"] != "patchwork"
        else "трещит, но держится"
        if absurdity >= 3
        else "выглядит подозрительно живым"
        if absurdity >= 1
        else "стоит уверенно и даже не дымит"
    )
    description = (
        f"{matter['label']}, {medium['label']} и {life['label']}."
        f" Архетип: {archetype['name']} ({archetype['role']})."
        + (f" Фокус: {focus['label']}." if focus else "")
        + f" Абсурдность: {absurdity}. Вместимость: {capacity}."
    )
    return name, description, capacity, absurdity, humor


def evaluate_world_creation(player: dict, slots: dict[str, str], focus_id: str | None = None) -> tuple[bool, int, int]:
    _, _, capacity, absurdity, _ = build_starter_world_preview(slots, focus_id=focus_id)
    attempt_number = len(player.get("worlds", [])) + 1
    if attempt_number == 1:
        return True, capacity, absurdity

    focus = WORLD_FOCUS_OPTIONS.get(focus_id or "")
    future_world = {
        "capacity": capacity,
        "absurdity": absurdity,
        "strain_cost": max(1, WORLD_ARCHETYPES.get((slots["matter"], slots["medium"], slots["life"]), DEFAULT_WORLD_ARCHETYPE)["strain_cost"] + (focus["strain_delta"] if focus else 0)),
        "support_bonus": WORLD_ARCHETYPES.get((slots["matter"], slots["medium"], slots["life"]), DEFAULT_WORLD_ARCHETYPE)["support_bonus"] + (focus["support_bonus"] if focus else 0),
    }
    current_worlds = list(player.get("worlds", []))
    simulated_player = dict(player)
    simulated_player["worlds"] = current_worlds + [future_world]
    overload = max(0, get_world_strain(simulated_player) - get_world_stability_limit(simulated_player))
    fail_chance = absurdity * 15 + overload * 15
    if attempt_number == 2:
        fail_chance += 10
    else:
        fail_chance += 20 + min(25, (attempt_number - 3) * 5)
    fail_chance = max(0, min(85, fail_chance))
    roll = random.randint(1, 100)
    return roll > fail_chance, capacity, absurdity

async def get_or_create_player(user_id: int, user_name: str):
    """Инициализация профиля."""
    player = await db.alchemy_players.find_one({"user_id": user_id})
    if not player:
        player = {"user_id": user_id, "user_name": user_name} | deepcopy(DEFAULT_PLAYER_TEMPLATE)
        await db.alchemy_players.insert_one(player)
        return player

    update_fields = {}
    for key, value in DEFAULT_PLAYER_TEMPLATE.items():
        if key not in player:
            player[key] = deepcopy(value)
            update_fields[key] = deepcopy(value)

    buildings = player.get("buildings", {})
    expected_buildings = DEFAULT_PLAYER_TEMPLATE["buildings"]
    if any(key not in buildings for key in expected_buildings):
        for key, value in expected_buildings.items():
            buildings.setdefault(key, value)
        player["buildings"] = buildings
        update_fields["buildings"] = buildings

    heritage_slots = get_heritage_slots(buildings.get("heritage_center", 1))
    active_stands = list(player.get("active_stands", []))
    normalized_stands = [stand if stand else None for stand in active_stands[:heritage_slots]]
    while len(normalized_stands) < heritage_slots:
        normalized_stands.append(None)
    if player.get("active_stands") != normalized_stands:
        player["active_stands"] = normalized_stands
        update_fields["active_stands"] = normalized_stands

    lore_progress = player.get("lore_fragment_progress", {})
    normalized_lore_progress = {
        "date": str(lore_progress.get("date", "")),
        "found": max(0, int(lore_progress.get("found", 0) or 0)),
    }
    if player.get("lore_fragment_progress") != normalized_lore_progress:
        player["lore_fragment_progress"] = normalized_lore_progress
        update_fields["lore_fragment_progress"] = normalized_lore_progress

    counters = player.get("action_counters", {})
    if any(key not in counters for key in DEFAULT_ACTION_COUNTERS):
        for key, value in DEFAULT_ACTION_COUNTERS.items():
            counters.setdefault(key, value)
        player["action_counters"] = counters
        update_fields["action_counters"] = counters

    fort_defense = player.get("fort_defense", {})
    normalized_fort_defense = {
        "moat": fort_defense.get("moat"),
        "catapult": fort_defense.get("catapult"),
        "aura": fort_defense.get("aura"),
    }
    if fort_defense != normalized_fort_defense:
        player["fort_defense"] = normalized_fort_defense
        update_fields["fort_defense"] = normalized_fort_defense

    if player.get("user_name") != user_name:
        player["user_name"] = user_name
        update_fields["user_name"] = user_name

    normalized_player = normalize_player_fighters(player)
    for field in [
        "inventory",
        "fighters",
        "active_expeditions",
        "raid_squad",
        "hall_of_fame",
        "fighter_name_pending",
        "fighter_schema_version",
    ]:
        if player.get(field) != normalized_player.get(field):
            player[field] = normalized_player.get(field)
            update_fields[field] = normalized_player.get(field)

    worlds, _, world_tutorial = ensure_world_state(player)
    if player.get("worlds") != worlds:
        player["worlds"] = worlds
        update_fields["worlds"] = worlds
    if player.get("world_tutorial") != world_tutorial:
        player["world_tutorial"] = world_tutorial
        update_fields["world_tutorial"] = world_tutorial
    failures = max(0, int(player.get("world_failures", 0) or 0))
    if player.get("world_failures") != failures:
        player["world_failures"] = failures
        update_fields["world_failures"] = failures
    support_level = max(0, int(player.get("world_support_level", 0) or 0))
    if player.get("world_support_level") != support_level:
        player["world_support_level"] = support_level
        update_fields["world_support_level"] = support_level

    if update_fields:
        await db.alchemy_players.update_one({"user_id": user_id}, {"$set": update_fields})
    return player


def increment_action_counter(player: dict, counter_id: str, amount: int = 1) -> dict:
    counters = deepcopy(player.get("action_counters", DEFAULT_ACTION_COUNTERS))
    counters[counter_id] = counters.get(counter_id, 0) + amount
    player["action_counters"] = counters
    return counters


def get_alchemy_cooldown(player: dict) -> int:
    guild_lvl = player.get("buildings", {}).get("guild", 0)
    cooldown_reduction = 0
    if guild_lvl == 1:
        cooldown_reduction = 1800
    elif guild_lvl == 2:
        cooldown_reduction = 3600
    elif guild_lvl >= 3:
        cooldown_reduction = 7200
    return max(3600, COOLDOWN - cooldown_reduction)


def get_stamina_ready_at(player: dict) -> int:
    return int(player.get("last_mine", 0) + get_alchemy_cooldown(player))


def format_duration(left_sec: int) -> str:
    left_sec = max(0, int(left_sec))
    hours = left_sec // 3600
    mins = (left_sec % 3600) // 60
    if hours:
        return f"{hours} ч. {mins} мин."
    return f"{mins} мин."


async def send_expedition_menu(target_message: types.Message, user_id: int, user_name: str):
    player = await get_or_create_player(user_id, user_name)
    if player.get("tutorial_step", 0) == 0:
        bot_info = await target_message.bot.get_me()
        if target_message.chat.type != "private":
            await target_message.reply(
                "🧪 Перед первым походом надо пережить один контролируемый алхимический взрыв. Жми и переходи в ЛС.",
                reply_markup=build_intro_menu(bot_info.username),
            )
            return
        intro_text, _ = await run_alchemy_intro(user_id, user_name)
        await target_message.answer(intro_text, parse_mode="Markdown")
        return
    if player.get("tutorial_step", 0) < 3:
        await target_message.answer(
            "🌍 Сначала собери первый микробиом в штабе. Без опоры мироздание не пускает тебя в нормальный поход."
        )
        return

    now = time.time()
    last_mine = player.get("last_mine", 0)
    has_base = player.get("inventory", {}).get("active_base", 0) > 0 or player.get("tutorial_step", 0) >= 2
    bot_info = await target_message.bot.get_me()

    actual_cooldown = get_alchemy_cooldown(player)
    if now - last_mine < actual_cooldown:
        left_sec = int(actual_cooldown - (now - last_mine))
        builder = InlineKeyboardBuilder()
        btn_text = "🏕 Моя База" if has_base else "🎒 Инвентарь"
        builder.button(text=btn_text, url=f"https://t.me/{bot_info.username}?start=alcbase")
        await target_message.answer(
            f"⏳ Алхимик отдыхает!\n"
            f"Следующий поход через: **{format_duration(left_sec)}**",
            parse_mode="Markdown",
            reply_markup=builder.as_markup(),
        )
        return

    await target_message.answer(
        f"🗺 **КАРТА ЭКСПЕДИЦИИ**\nКуда отправимся в этот раз, {user_name}?",
        reply_markup=build_locations_menu(bot_info.username, has_base),
        parse_mode="Markdown",
    )

def build_locations_menu(bot_username: str, has_base: bool):
    """Интерфейс выбора локации для экспедиции."""
    b = InlineKeyboardBuilder()
    b.button(text="💧 Река", callback_data="alc:go:river")
    b.button(text="⛰ Горы", callback_data="alc:go:mountains")
    b.button(text="🕳 Пещера", callback_data="alc:go:cave")
    b.button(text="🌋 Вулкан", callback_data="alc:go:volcano")
    
    btn_text = "🏕 Моя База" if has_base else "🎒 Инвентарь"
    b.button(text=btn_text, url=f"https://t.me/{bot_username}?start=alcbase")
    b.adjust(2, 2, 1)
    return b.as_markup()


def build_intro_menu(bot_username: str):
    builder = InlineKeyboardBuilder()
    builder.button(text="🧪 Начать путь", url=f"https://t.me/{bot_username}?start=alcintro")
    return builder.as_markup()


async def run_alchemy_intro(user_id: int, user_name: str) -> tuple[str, bool]:
    player = await get_or_create_player(user_id, user_name)
    if player.get("tutorial_step", 0) >= 1:
        return "Ты уже пережил первый алхимический БАМММ. Открывай /base и продолжай.", False

    inventory = player.get("inventory", {})
    starters = {
        "base_crystal": 1,
        "pack_s": 1,
        "w": 10,
        "f": 10,
        "e": 10,
        "a": 10,
        "energy": 2,
        "stone": 2,
        "wood": 2,
    }
    for item_id, amount in starters.items():
        inventory[item_id] = inventory.get(item_id, 0) + amount

    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "inventory": inventory,
                "tutorial_step": 1,
                "discovered": sorted(set(player.get("discovered", [])) | set(STARTER_DISCOVERIES)),
                "world_tutorial": {"active": False, "created": False},
            }
        },
    )
    text = (
        "\"Пшик, пшик, пшик... А это так должно было?!! **БАМММ!**\"\n\n"
        "Это последнее, что ты услышал, прежде чем очнуться в пустоте. Под рукой оказался манящий кристалл базы, рядом валялись коробочки и сырье."
        " Пахнет катастрофой, но старт у создателя всегда такой.\n\n"
        "Получено:\n"
        "• 🔮 Кристалл Базы x1\n"
        "• 📦 Малый набор x1\n"
        "• Базовые стихии по 10\n"
        "• 🎇 Энергия x2\n"
        "• 🪨 Камень x2\n"
        "• 🪵 Древесина x2\n\n"
        "Я еще подсунул тебе несколько стартовых рецептов: Лава, Камень, Пар, Болото, Жизнь, Водоросли, Мох и Бактерия."
        " Открой /base, активируй Кристалл Базы и попробуй собрать из этого первый микробиом."
    )
    return text, True

# ==================== ЛОГИКА ПРЕДМЕТОВ ====================

async def use_item_logic(user_id: int, item_code: str):
    """Активация предметов из инвентаря (паки, свитки, кристаллы)."""
    player = await db.alchemy_players.find_one({"user_id": user_id})
    inv = player.get("inventory", {})
    discovered = player.get("discovered", [])
    
    if inv.get(item_code, 0) <= 0:
        return "У вас нет этого предмета!", False

    text = ""
    success = False

    # 1. РАСПАКОВКА ПАКОВ
    if item_code.startswith("pack_") and item_code in PACK_LOOT:
        config = PACK_LOOT[item_code]
        inv[item_code] -= 1
        
        basics = ["w", "f", "e", "a"]
        added = []
        for b in basics:
            amt = random.randint(*config["basics"])
            inv[b] = inv.get(b, 0) + amt
            added.append(f"{ELEMENTS.get(b, b)} +{amt}")
        
        # Дополнительные предметы 2-3 уровня с небольшим шансом
        if random.random() < config["bonus_chance"]:
            bonus = random.choice(config["bonus_items"])
            bonus_amt = random.randint(*config["bonus_amount"])
            inv[bonus] = inv.get(bonus, 0) + bonus_amt
            added.append(f"✨ БОНУС: {ELEMENTS.get(bonus, bonus)} +{bonus_amt}")
            
        text = f"📦 Распаковка {ELEMENTS.get(item_code, item_code)}:\n" + "\n".join(added)
        success = True

    # 2. АКТИВАЦИЯ БАЗЫ (Прямая активация без беготни)
    elif item_code == "base_crystal":
        inv[item_code] -= 1
        for technical in ["active_base", "base_crystal"]:
            if technical in inv: del inv[technical]
        
        inv["active_base"] = 1
        text = ("✅ **База активирована!**\n\n"
                "Кристалл вспыхнул и развернул вокруг тебя карманную пустоту. На стене загорелось:\n"
                "`мир уничтожен, требуется создатель`\n\n"
                "Сначала собери первый микробиом. Вариантов немного, но они уже настоящие:\n"
                "1. 🔥 + 🪨 = **Лава**\n"
                "2. 💧 + 🔥 = **Пар**\n"
                "3. 💧 + 🪨 = **Болото**\n"
                "4. 🎇 + **Болото** = **Жизнь**\n"
                "5. **Жизнь** + 💧 = **Водоросли**\n"
                "6. **Водоросли** + **Болото** = **Мох**\n"
                "7. **Жизнь** + **Болото** = **Бактерия**\n\n"
                "Когда что-нибудь из этого добудешь, возвращайся в штаб и жми на создание микробиома.")
        
        # Завершаем туториал
        await db.alchemy_players.update_one(
            {"user_id": user_id},
            {"$set": {"tutorial_step": 2, "world_tutorial": {"active": True, "created": False}}},
        )
        success = True

    # 3. СВИТОК РЕЦЕПТА
    elif item_code == "recipe_scroll":
        known = set(discovered) | set(inv.keys())
        barracks_level = player.get("buildings", {}).get("barracks", 0)
        unknown_results = []
        for _, res in RECIPES.items():
            if res in known:
                continue
            if is_battle_unit(res) and barracks_level < BATTLE_UNITS[res]["barracks_level"]:
                continue
            unknown_results.append(res)
        
        if not unknown_results:
            return "📜 Свиток рассыпался бы в прах, но ты вовремя понял: ты уже знаешь все рецепты этого мира!", False
            
        inv[item_code] -= 1
        new_recipe_result = random.choice(unknown_results)
        discovered.append(new_recipe_result)
        
        await db.alchemy_players.update_one({"user_id": user_id}, {"$set": {"discovered": discovered}})
        res_name = ELEMENTS.get(new_recipe_result, new_recipe_result)
        text = f"📜 Вы изучили новый рецепт!\nТайна создания разгадана: **{res_name}**"
        success = True

    if success:
        await db.alchemy_players.update_one({"user_id": user_id}, {"$set": {"inventory": inv}})
        
    return text, success

# ==================== КОМАНДЫ ЭКСПЕДИЦИИ ====================

@router.message(Command("pohod", "поход", "добыча"))
async def start_expedition(message: types.Message):
    await send_expedition_menu(message, message.from_user.id, message.from_user.first_name)
    asyncio.create_task(delete_after_delay(message, 5))

@router.callback_query(F.data.startswith("alc:go:"))
async def process_expedition(callback: types.CallbackQuery):
    location = callback.data.split(":")[2]
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    
    now = time.time()
    
    # 1. Получаем уровень гильдии, считаем кулдаун и бонусный лут
    guild_lvl = player.get("buildings", {}).get("guild", 0)
    guild_loot_bonus = 0
    
    if guild_lvl == 1: 
        guild_loot_bonus = 2
    elif guild_lvl == 2: 
        guild_loot_bonus = 5
    elif guild_lvl >= 3: 
        guild_loot_bonus = 10
    
    actual_cooldown = get_alchemy_cooldown(player)
    
    # Проверка таймера с учетом гильдии
    if now - player.get("last_mine", 0) < actual_cooldown:
        return await callback.answer("⏳ Силы еще не восстановились!", show_alert=True)

    inv = player.get("inventory", {})
    world_effects = get_world_effects(player)
    
    # 2. Определяем лут в зависимости от локации
    if location == "river": possible_items = ["w", "sand", "cane"]
    elif location == "mountains": possible_items = ["e", "stone", "metal"]
    elif location == "cave": possible_items = ["e", "stone", "energy", "glass"]
    elif location == "volcano": possible_items = ["f", "lava", "brick"]
    else: possible_items = ["w", "e", "f", "a"]
    
    location_bonus = int(world_effects.get(f"{location}_bonus", 0) or 0)
    global_loot_bonus = int(world_effects.get("global_loot_bonus", 0) or 0)
    rare_find_bonus = float(world_effects.get("rare_find_bonus", 0) or 0)
    
    # 3. Настройка добычи (база 15-20 штук + бонус гильдии + бонусы мира)
    loot_count = random.randint(15, 20) + guild_loot_bonus + location_bonus + global_loot_bonus
    drops = [random.choice(possible_items) for _ in range(loot_count)]
    
    # 4. Повышенные шансы на редкий лут
    if random.random() < 0.10 + rare_find_bonus: drops.append("recipe_scroll")
    if random.random() < 0.15 + rare_find_bonus: drops.append("pack_s")

    # --- СЮЖЕТ И ТУТОРИАЛ ---
    step = player.get("tutorial_step", 0)
    narrative_text = ""
    
    if random.random() < 0.05:
        inv["pack_s"] = inv.get("pack_s", 0) + 1
        narrative_text += "\n\n✨ Ого! Ты наткнулся на 📦 Малый набор!"
    if random.random() < 0.03:
        inv["recipe_scroll"] = inv.get("recipe_scroll", 0) + 1
        narrative_text += "\n\n📜 В грязи лежал древний Свиток Рецепта!"
    if location_bonus or global_loot_bonus:
        narrative_text += "\n\n🌍 Мир отозвался на вылазку и подкинул добыче дополнительный импульс."
    if rare_find_bonus > 0:
        narrative_text += "\n💥 Нестабильный биом нервно заискрил, поэтому редкости сегодня липнут охотнее."

    # Обновляем инвентарь базовыми дропами
    for item in drops:
        inv[item] = inv.get(item, 0) + 1
        
    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {"last_mine": now, "tutorial_step": step, "inventory": inv, "stamina_notified": False}}
    )

    found_text = ", ".join([ELEMENTS.get(d, d) for d in drops])
    result_msg = f"🏃‍♂️ {callback.from_user.first_name} сходил в поход и добыл: {found_text}{narrative_text}"
    
    bot_info = await callback.bot.get_me()
    builder = InlineKeyboardBuilder()
    
    has_base_now = step >= 1 or "active_base" in inv or "base_crystal" in inv
    btn_text = "🏕 Моя База" if has_base_now else "🎒 Инвентарь"
    builder.button(text=btn_text, url=f"https://t.me/{bot_info.username}?start=alcbase")
    
    await callback.message.edit_text(result_msg, reply_markup=builder.as_markup(), parse_mode="Markdown")
    asyncio.create_task(delete_after_delay(callback.message, 30))
    
# ==================== АДМИН-КОМАНДЫ ====================

@router.message(Command("promo", "промо"))
async def admin_promo(message: types.Message, command: CommandObject):
    """Выдача промо-пака игроку реплаем."""
    if message.from_user.id != ADMIN_ID: return
    if not message.reply_to_message:
        return await message.reply("❌ Ответь этой командой на сообщение игрока.")
    
    target_id = message.reply_to_message.from_user.id
    target_name = message.reply_to_message.from_user.first_name
    size = command.args.lower() if command.args else "s"
    
    if size not in [k.split('_')[1] for k in PACK_LOOT.keys()]:
        return await message.reply("❌ Доступные размеры: s, m, l, xl")
        
    config = PACK_LOOT[f"pack_{size}"]
    player = await get_or_create_player(target_id, target_name)
    inventory = player.get("inventory", {})
    
    basics = ["w", "f", "e", "a"]
    added_text = []
    
    for b in basics:
        amt = random.randint(*config["basics"])
        inventory[b] = inventory.get(b, 0) + amt
        added_text.append(f"{ELEMENTS.get(b, b)}: {amt} шт.")
    
    if random.random() < config["bonus_chance"]:
        bonus = random.choice(config["bonus_items"])
        bonus_amt = random.randint(*config["bonus_amount"])
        inventory[bonus] = inventory.get(bonus, 0) + bonus_amt
        added_text.append(f"✨ БОНУС: {ELEMENTS.get(bonus, bonus)}: {bonus_amt} шт.")
        
    pack_item = f"pack_{size}"
    inventory[pack_item] = inventory.get(pack_item, 0) + 1
    
    storage_lvl = player.get("buildings", {}).get("storage", 0)
    max_slots = STORAGE_CAPACITY.get(storage_lvl, 20)
    
    if get_used_slots(inventory) > max_slots:
        return await message.reply(f"❌ У {target_name} не хватает слотов на складе!")

    await db.alchemy_players.update_one({"user_id": target_id}, {"$set": {"inventory": inventory}})
    await message.reply(f"🎁 {target_name} получил бустпак [{size.upper()}]!\n" + "\n".join(added_text) + f"\n\nПлюс выдан сам 📦 для крафта!")

@router.message(Command("clear_inv"))
async def admin_clear_inv(message: types.Message):
    """Скрытая команда очистки инвентаря."""
    if message.from_user.id != ADMIN_ID or not message.reply_to_message: return
    
    target_user = message.reply_to_message.from_user
    
    # Защита: проверяем, не ответил ли админ на сообщение бота
    if target_user.is_bot:
        return await message.reply("❌ Ошибка: Ты ответил на сообщение бота. Ответь на любое сообщение самого игрока!")
        
    target_id = target_user.id
    await db.alchemy_players.update_one({"user_id": target_id}, {"$set": {"inventory": {}}})
    await message.reply(f"🗑 Инвентарь игрока {target_user.first_name} успешно обнулён.")

@router.message(Command("alc_reset"))
async def admin_hard_reset(message: types.Message, command: CommandObject):
    """Полное удаление профиля игрока из БД."""
    if message.from_user.id != ADMIN_ID: return
        
    args = command.args
    if not args or not args.isdigit():
        return await message.answer("Укажи ID игрока. Использование: `/alc_reset 123456789`", parse_mode="Markdown")
        
    target_id = int(args)
    result = await db.alchemy_players.delete_one({"user_id": target_id})
    
    if result.deleted_count > 0:
        await message.answer(f"✅ Профиль игрока `{target_id}` полностью удален из базы. При следующем походе он начнется с нуля.", parse_mode="Markdown")
    else:
        await message.answer(f"⚠️ Игрок `{target_id}` не найден в базе.", parse_mode="Markdown")
