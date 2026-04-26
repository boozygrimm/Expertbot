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
WORLD_DAILY_SPIRIT_TOKEN = "random_spirit"
WORLD_LAYER_ORDER = ("foundation", "medium", "energy", "life")
WORLD_BASE_RESOURCE_ITEMS = {"w", "f", "e", "a", "energy", "stone", "wood"}
STARTER_WORLD_RESOURCE_GRANT = {
    "stone": 2,
    "wood": 2,
    "swamp": 1,
    "algae": 1,
    "steam": 1,
    "energy": 1,
    "life": 1,
    "moss": 1,
}
STARTER_WORLD_REWARD_BUNDLE = {
    "pack_m": 1,
    "stone": 4,
    "sand": 4,
    "wood": 6,
    "energy": 2,
}
STARTER_BIOME_COLLAPSE_BUNDLE = {
    "stone": 3,
    "wood": 3,
    "sand": 2,
    "brick": 2,
    "glass": 1,
    "metal": 1,
    "energy": 1,
}
STARTER_BIOME_SLOTS = {
    "foundation": {
        "tutorial_seed": {
            "label": "🫧 Каменное зерно",
            "size": 1,
            "cost": {"stone": 2, "wood": 2},
            "affinity": "опора",
            "hint": "учебная опора для первого микробиома",
            "tutorial": True,
        },
        "stone_root": {
            "label": "🪨 Каменный корень",
            "size": 3,
            "cost": {"stone": 6, "sand": 3, "wood": 2},
            "affinity": "опора",
            "hint": "тяжёлая основа под спокойный рост",
        },
        "lava_bed": {
            "label": "🌋 Лавовый пласт",
            "size": 3,
            "cost": {"lava": 4, "stone": 4, "brick": 2, "energy": 1},
            "affinity": "добывающий",
            "hint": "жёсткая геология под мощную добычу",
        },
        "metal_frame": {
            "label": "🛠 Металлический костяк",
            "size": 4,
            "cost": {"metal": 4, "brick": 3, "glass": 2, "energy": 1},
            "affinity": "промышленный",
            "hint": "каркас под техничный и плотный мир",
        },
    },
    "medium": {
        "tutorial_broth": {
            "label": "🫧 Болотный бульон",
            "size": 1,
            "cost": {"swamp": 1, "algae": 1},
            "affinity": "опора",
            "hint": "учебная среда, где жизнь может закрепиться",
            "tutorial": True,
        },
        "swamp_basin": {
            "label": "🐸 Болотный бассейн",
            "size": 3,
            "cost": {"swamp": 2, "sand": 2, "algae": 2},
            "affinity": "опора",
            "hint": "вязкая среда, где жизнь цепляется лучше всего",
        },
        "steam_front": {
            "label": "☁️ Паровой фронт",
            "size": 2,
            "cost": {"steam": 3, "glass": 2, "metal": 1},
            "affinity": "экспедиционный",
            "hint": "подвижная среда с постоянным давлением",
        },
        "sealed_dome": {
            "label": "🔮 Герметичный купол",
            "size": 3,
            "cost": {"glass": 4, "brick": 2, "energy": 1},
            "affinity": "промышленный",
            "hint": "контролируемая среда для сложных контуров",
        },
    },
    "energy": {
        "tutorial_pulse": {
            "label": "🫧 Паровой импульс",
            "size": 1,
            "cost": {"steam": 1, "energy": 1},
            "affinity": "опора",
            "hint": "учебный толчок для первого контура мира",
            "tutorial": True,
        },
        "ember_grid": {
            "label": "🔥 Жаровая сеть",
            "size": 2,
            "cost": {"energy": 3, "lava": 2, "steam": 1},
            "affinity": "добывающий",
            "hint": "подпитывает тяжёлые узлы и добычу",
        },
        "spark_mesh": {
            "label": "⚡ Искровая сетка",
            "size": 3,
            "cost": {"energy": 2, "electricity": 2, "metal": 2},
            "affinity": "промышленный",
            "hint": "любит механику, цеха и точный контроль",
        },
        "spore_cycle": {
            "label": "🫧 Споровый цикл",
            "size": 2,
            "cost": {"energy": 2, "life": 1, "algae": 2},
            "affinity": "опора",
            "hint": "медленный, но устойчивый источник роста",
        },
    },
    "life": {
        "tutorial_spore": {
            "label": "🫧 Моховая спора",
            "size": 1,
            "cost": {"moss": 1, "life": 1},
            "affinity": "опора",
            "hint": "учебная жизнь, которая просто должна удержаться",
            "tutorial": True,
        },
        "moss_bloom": {
            "label": "🌿 Моховое цветение",
            "size": 3,
            "cost": {"moss": 3, "algae": 2, "life": 1},
            "affinity": "опора",
            "hint": "стабилизирует биом и делает его обжитым",
        },
        "bacteria_hive": {
            "label": "🦠 Бактериальный улей",
            "size": 2,
            "cost": {"bacteria": 3, "life": 1, "swamp": 1},
            "affinity": "экспедиционный",
            "hint": "агрессивная жизнь, которая быстро захватывает среду",
        },
        "spirit_garden": {
            "label": "✨ Духосад",
            "size": 3,
            "cost": {"life": 2, "moss": 2, "energy": 1, "paper": 1},
            "affinity": "универсальный",
            "hint": "редкая жизнь, которую можно направить в любую сторону",
        },
    },
}
WORLD_ARCHETYPES = {
    "опора": {
        "id": "nursery",
        "name": "🌱 Колыбельный массив",
        "role": "опора",
        "support_base": 2,
        "strain_base": 1,
        "bonuses": {"farm_yield": 1, "aero_yield": 1, "river_bonus": 2},
        "daily_income": {"wood": 2, "stone": 1, "moss": 1, "life": 1},
        "humor": "тихо наращивает почву и терпеливо держит мир на себе",
    },
    "промышленный": {
        "id": "forge",
        "name": "⚙️ Индустриальный пояс",
        "role": "промышленный",
        "support_base": 1,
        "strain_base": 2,
        "bonuses": {"mine_yield": 1, "factory_yield": 1, "mountains_bonus": 2, "cave_bonus": 2},
        "daily_income": {"metal": 1, "glass": 1, "brick": 1, "energy": 1},
        "humor": "гудит, коптит и превращает пустоту в инфраструктуру",
    },
    "добывающий": {
        "id": "wildfire",
        "name": "🌋 Разломный карьер",
        "role": "добывающий",
        "support_base": 0,
        "strain_base": 3,
        "bonuses": {"volcano_bonus": 3, "mine_yield": 1},
        "daily_income": {"stone": 2, "sand": 1, "lava": 1, "metal": 1},
        "humor": "трещит, роет и всё время просит ещё топлива",
    },
    "экспедиционный": {
        "id": "expedition",
        "name": "🚀 Фронтирный пояс",
        "role": "экспедиционный",
        "support_base": 0,
        "strain_base": 3,
        "bonuses": {"global_loot_bonus": 1, "rare_find_bonus": 0.03},
        "daily_income": {"paper": 1, "energy": 1, "sand": 1, "bacteria": 1},
        "humor": "раскачивает границы мира и постоянно смотрит наружу",
    },
    "универсальный": {
        "id": "patchwork",
        "name": "🧩 Лоскутный карман",
        "role": "универсальный",
        "support_base": 1,
        "strain_base": 2,
        "bonuses": {"farm_yield": 1},
        "daily_income": {"stone": 1, "wood": 1, "glass": 1},
        "humor": "собран из разного, но неожиданно живуч",
    },
}
DEFAULT_WORLD_ARCHETYPE = WORLD_ARCHETYPES["универсальный"]
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
    "world_tutorial": {"active": False, "created": False, "hints_shown": False},
    "world_support_level": 0,
    "world_legacy_reset_used": False,
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


def ensure_legacy_base_crystal(player: dict) -> tuple[dict, bool]:
    """Докладывает кристалл старым игрокам, которые застряли без базы."""
    inventory = player.get("inventory", {})
    if not isinstance(inventory, dict):
        inventory = {}

    if int(player.get("tutorial_step", 0) or 0) < 1:
        return inventory, False
    if int(inventory.get("active_base", 0) or 0) > 0:
        return inventory, False
    if int(inventory.get("base_crystal", 0) or 0) > 0:
        return inventory, False

    updated_inventory = deepcopy(inventory)
    updated_inventory["base_crystal"] = 1
    return updated_inventory, True


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
                "daily_income": {
                    str(item_id): max(0, int(amount or 0))
                    for item_id, amount in dict(world.get("daily_income", {})).items()
                    if int(amount or 0) > 0
                },
                "slots": {
                    "matter": str(((world.get("slots") or {}).get("matter")) or ""),
                    "foundation": str(((world.get("slots") or {}).get("foundation")) or ((world.get("slots") or {}).get("matter")) or ""),
                    "medium": str(((world.get("slots") or {}).get("medium")) or ""),
                    "energy": str(((world.get("slots") or {}).get("energy")) or ""),
                    "life": str(((world.get("slots") or {}).get("life")) or ""),
                },
                "starter": bool(world.get("starter", False)),
            }
        )

    world_tutorial = player.get("world_tutorial", {})
    normalized_tutorial = {
        "active": bool(world_tutorial.get("active", False)),
        "created": bool(world_tutorial.get("created", False)),
        "hints_shown": bool(world_tutorial.get("hints_shown", False)),
    }
    # Второй элемент кортежа сохранён для совместимости со старыми вызовами,
    # но отдельного legacy-режима для мира больше нет.
    is_legacy = False
    return normalized_worlds, is_legacy, normalized_tutorial


def has_legacy_world_layout(player: dict) -> bool:
    worlds, _, _ = ensure_world_state(player)
    for world in worlds:
        slots = world.get("slots", {})
        if slots.get("matter") or not slots.get("foundation") or not slots.get("energy"):
            return True
    return False


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
        role = str(world.get("role") or "")
        if role in {"", "универсальный"} and slots.get("matter"):
            role = resolve_legacy_world_role(slots)
        archetype = WORLD_ARCHETYPES.get(role, DEFAULT_WORLD_ARCHETYPE)
        bonuses = archetype.get("bonuses", {})
        scale = max(1.0, float(int(world.get("capacity", 0) or 0)) / 10.0)
        for bonus_id, bonus_value in bonuses.items():
            if isinstance(bonus_value, float):
                effects[bonus_id] = effects.get(bonus_id, 0) + round(bonus_value * scale, 4)
            else:
                effects[bonus_id] = effects.get(bonus_id, 0) + max(1, int(round(bonus_value * scale)))
        focus_id = str(world.get("focus") or "")
        if focus_id in WORLD_FOCUS_OPTIONS:
            for bonus_id, bonus_value in WORLD_FOCUS_OPTIONS[focus_id].get("bonuses", {}).items():
                effects[bonus_id] = effects.get(bonus_id, 0) + bonus_value
    return effects


def get_world_daily_income(player: dict) -> dict[str, int]:
    worlds, _, _ = ensure_world_state(player)
    if not worlds:
        return {}

    income: dict[str, int] = {}
    for world in worlds:
        daily_income = dict(world.get("daily_income", {}))
        if not daily_income:
            role = str(world.get("role") or "")
            archetype = WORLD_ARCHETYPES.get(role, DEFAULT_WORLD_ARCHETYPE)
            daily_income = build_world_daily_income(archetype, int(world.get("capacity", 0) or 0))
        for item_id, amount in daily_income.items():
            income[item_id] = income.get(item_id, 0) + max(0, int(amount or 0))
    return income


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


def resolve_world_archetype(slots: dict[str, str]) -> dict:
    scores: dict[str, int] = {}
    for layer_id in WORLD_LAYER_ORDER:
        option_id = slots.get(layer_id, "")
        layer_options = STARTER_BIOME_SLOTS.get(layer_id, {})
        option = layer_options.get(option_id)
        if not option:
            continue
        affinity = str(option.get("affinity") or "универсальный")
        scores[affinity] = scores.get(affinity, 0) + int(option.get("size", 0) or 0)

    if not scores:
        return DEFAULT_WORLD_ARCHETYPE

    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    role = ordered[0][0]
    return WORLD_ARCHETYPES.get(role, DEFAULT_WORLD_ARCHETYPE)


def resolve_legacy_world_role(slots: dict[str, str]) -> str:
    matter = str(slots.get("matter", ""))
    medium = str(slots.get("medium", ""))
    life = str(slots.get("life", ""))
    legacy_map = {
        ("stone", "swamp", "moss"): "опора",
        ("stone", "steam", "bacteria"): "промышленный",
        ("lava", "swamp", "moss"): "добывающий",
        ("lava", "steam", "bacteria"): "экспедиционный",
    }
    return legacy_map.get((matter, medium, life), "универсальный")


def is_tutorial_biome(slots: dict[str, str]) -> bool:
    for layer_id in WORLD_LAYER_ORDER:
        option_id = str(slots.get(layer_id, ""))
        option = STARTER_BIOME_SLOTS.get(layer_id, {}).get(option_id)
        if not option or not bool(option.get("tutorial", False)):
            return False
    return True


def get_biome_slot_options(layer_id: str, tutorial_only: bool) -> dict[str, dict]:
    options = STARTER_BIOME_SLOTS.get(layer_id, {})
    if tutorial_only:
        return {option_id: meta for option_id, meta in options.items() if bool(meta.get("tutorial", False))}
    return {option_id: meta for option_id, meta in options.items() if not bool(meta.get("tutorial", False))}


def is_resource_draft(draft: dict) -> bool:
    return any(isinstance(value, dict) for value in draft.values())


def normalize_resource_bucket(bucket: dict | None) -> dict[str, int]:
    normalized: dict[str, int] = {}
    for item_id, amount in dict(bucket or {}).items():
        amount_int = max(0, int(amount or 0))
        if amount_int > 0:
            normalized[str(item_id)] = amount_int
    return normalized


def build_resource_draft_slots(player: dict, draft: dict[str, dict[str, int]]) -> tuple[dict[str, str], dict[str, dict]]:
    tutorial_only = not bool(ensure_world_state(player)[0])
    slots: dict[str, str] = {}
    reports: dict[str, dict] = {}

    for layer_id in WORLD_LAYER_ORDER:
        bucket = normalize_resource_bucket(draft.get(layer_id, {}))
        options = get_biome_slot_options(layer_id, tutorial_only=tutorial_only)
        if not options:
            continue

        best_option_id = next(iter(options))
        best_score = -10_000.0
        best_foreign = 0
        best_matched = 0
        layer_total = sum(bucket.values())

        for option_id, meta in options.items():
            cost = dict(meta.get("cost", {}))
            matched = sum(min(bucket.get(item_id, 0), int(amount or 0)) for item_id, amount in cost.items())
            relevant_extra = sum(max(0, bucket.get(item_id, 0) - int(amount or 0)) for item_id, amount in cost.items())
            foreign = sum(amount for item_id, amount in bucket.items() if item_id not in cost)
            score = matched * 3.0 + relevant_extra * 0.35 - foreign * 1.15 + float(meta.get("size", 0) or 0)
            if score > best_score:
                best_option_id = option_id
                best_score = score
                best_foreign = foreign
                best_matched = matched

        slots[layer_id] = best_option_id
        base_cost = sum(int(amount or 0) for amount in options[best_option_id].get("cost", {}).values())
        reports[layer_id] = {
            "invested": layer_total,
            "matched": best_matched,
            "foreign": best_foreign,
            "baseline": base_cost,
            "score": best_score,
        }

    return slots, reports


def build_world_blueprint(player: dict, draft: dict, focus_id: str | None = None) -> dict:
    focus = WORLD_FOCUS_OPTIONS.get(focus_id or "")

    if is_resource_draft(draft):
        slots, reports = build_resource_draft_slots(player, draft)
        if not all(draft.get(layer_id) for layer_id in WORLD_LAYER_ORDER):
            return {
                "slots": slots,
                "reports": reports,
                "capacity": 0,
                "absurdity": 0,
                "fail_chance": 0,
                "is_tutorial": False,
            }

        archetype = resolve_world_archetype(slots)
        base_capacity = sum(int(STARTER_BIOME_SLOTS[layer_id][slots[layer_id]].get("size", 0) or 0) for layer_id in WORLD_LAYER_ORDER)
        mass_bonus = 0
        chaos = 0
        role_scores: dict[str, int] = {}
        for layer_id in WORLD_LAYER_ORDER:
            report = reports.get(layer_id, {})
            option = STARTER_BIOME_SLOTS[layer_id][slots[layer_id]]
            role = str(option.get("affinity") or "универсальный")
            role_scores[role] = role_scores.get(role, 0) + int(option.get("size", 0) or 0)
            invested = int(report.get("invested", 0) or 0)
            baseline = max(1, int(report.get("baseline", 0) or 0))
            foreign = int(report.get("foreign", 0) or 0)
            mass_bonus += max(0, invested - baseline) // 6
            chaos += foreign + max(0, invested - baseline * 2)

        capacity = base_capacity + min(4, mass_bonus) + (focus["capacity_bonus"] if focus else 0)
        total_role_score = sum(role_scores.values())
        dominant = max(role_scores.values()) if role_scores else 0
        tension = max(0, total_role_score - dominant)
        absurdity = max(0, 12 - capacity + tension + chaos // 4)
        support_bonus = archetype["support_base"] + max(0, capacity // 6) + (focus["support_bonus"] if focus else 0)
        strain_cost = max(1, archetype["strain_base"] + max(0, capacity // 5) + tension // 3 + chaos // 8 + (focus["strain_delta"] if focus else 0))
        is_tutorial = is_tutorial_biome(slots)

        worlds, _, _ = ensure_world_state(player)
        attempt_number = len(worlds) + 1
        if attempt_number == 1 and is_tutorial:
            fail_chance = 0
        else:
            future_overload = max(0, get_world_strain(player) + strain_cost - get_world_stability_limit(player))
            fail_chance = min(85, tension * 7 + chaos * 2 + future_overload * 15 + (5 if focus else 0))

        return {
            "slots": slots,
            "reports": reports,
            "archetype": archetype,
            "capacity": capacity,
            "absurdity": absurdity,
            "support_bonus": support_bonus,
            "strain_cost": strain_cost,
            "fail_chance": fail_chance,
            "is_tutorial": is_tutorial,
        }

    archetype = resolve_world_archetype(draft)
    _, _, capacity, absurdity, _ = build_starter_world_preview(draft, focus_id=focus_id)
    support_bonus = archetype["support_base"] + max(0, capacity // 6) + (focus["support_bonus"] if focus else 0)
    strain_cost = max(1, archetype["strain_base"] + max(0, capacity // 5) + (focus["strain_delta"] if focus else 0))
    worlds, _, _ = ensure_world_state(player)
    attempt_number = len(worlds) + 1
    affinity_scores: dict[str, int] = {}
    for layer_id in WORLD_LAYER_ORDER:
        option = STARTER_BIOME_SLOTS[layer_id][draft[layer_id]]
        affinity = str(option.get("affinity") or "универсальный")
        affinity_scores[affinity] = affinity_scores.get(affinity, 0) + int(option.get("size", 0) or 0)
    dominant = max(affinity_scores.values()) if affinity_scores else 0
    total = sum(affinity_scores.values())
    tension = max(0, total - dominant)
    diversity = max(0, len(affinity_scores) - 1)
    future_overload = max(0, get_world_strain(player) + strain_cost - get_world_stability_limit(player))
    fail_chance = 0 if attempt_number == 1 or is_tutorial_biome(draft) else min(85, absurdity * 8 + tension * 6 + diversity * 8 + future_overload * 15 + (5 if focus else 0))
    return {
        "slots": draft,
        "reports": {},
        "archetype": archetype,
        "capacity": capacity,
        "absurdity": absurdity,
        "support_bonus": support_bonus,
        "strain_cost": strain_cost,
        "fail_chance": fail_chance,
        "is_tutorial": is_tutorial_biome(draft),
    }


def build_world_daily_income(archetype: dict, capacity: int) -> dict[str, int]:
    if not archetype:
        return {}
    capacity = max(1, int(capacity or 0))
    scale = max(1, capacity // 4)
    income: dict[str, int] = {}
    for item_id, amount in archetype.get("daily_income", {}).items():
        income[item_id] = max(1, int(amount or 0)) * scale
    return income


def build_starter_world_preview(slots: dict[str, str], focus_id: str | None = None) -> tuple[str, str, int, int, str]:
    foundation = STARTER_BIOME_SLOTS["foundation"][slots["foundation"]]
    medium = STARTER_BIOME_SLOTS["medium"][slots["medium"]]
    energy = STARTER_BIOME_SLOTS["energy"][slots["energy"]]
    life = STARTER_BIOME_SLOTS["life"][slots["life"]]
    archetype = resolve_world_archetype(slots)
    focus = WORLD_FOCUS_OPTIONS.get(focus_id or "")
    capacity = foundation["size"] + medium["size"] + energy["size"] + life["size"] + (focus["capacity_bonus"] if focus else 0)
    absurdity = max(0, 12 - capacity)
    if is_tutorial_biome(slots):
        name = "🫧 Учебный микробиом"
        humor = "держится на честном слове и стартовом энтузиазме"
        description = (
            f"{foundation['label']}, {medium['label']}, {energy['label']} и {life['label']}."
            " Это учебный микробиом: он нужен, чтобы запустить мир и открыть первые здания."
            + (f" Фокус: {focus['label']}." if focus else "")
            + f" Размер биома: {capacity}. Ежедневно приводит: 1 случайного духа."
        )
        return name, description, capacity, absurdity, humor

    name = archetype["name"]
    daily_income = build_world_daily_income(archetype, capacity)
    daily_label = ", ".join(f"{ELEMENTS.get(item_id, item_id)} x{amount}" for item_id, amount in daily_income.items())
    humor = (
        archetype["humor"]
        if archetype["id"] != "patchwork"
        else "получился неровным, но пригодным для жизни"
    )
    description = (
        f"{foundation['label']}, {medium['label']}, {energy['label']} и {life['label']}."
        f" Архетип: {archetype['name']} ({archetype['role']})."
        + (f" Фокус: {focus['label']}." if focus else "")
        + f" Размер биома: {capacity}. Ежедневно приносит: {daily_label}."
    )
    return name, description, capacity, absurdity, humor


def get_world_creation_penalty_percent(attempt_number: int) -> int:
    if attempt_number <= 2:
        return 0
    return min(15, (attempt_number - 2) * 5)


def build_base_resource_refund(cost: dict[str, int], ratio: float = 1.0) -> dict[str, int]:
    recipe_map = {result: ingredients for ingredients, result in RECIPES.items()}

    def expand_item(item_id: str, amount: int, trail: set[str] | None = None) -> dict[str, int]:
        trail = trail or set()
        if item_id in WORLD_BASE_RESOURCE_ITEMS:
            return {item_id: amount}
        if item_id in trail or item_id not in recipe_map:
            return {item_id: amount}
        left, right = recipe_map[item_id]
        refund: dict[str, int] = {}
        for child_id in (left, right):
            for base_id, base_amount in expand_item(child_id, amount, trail | {item_id}).items():
                refund[base_id] = refund.get(base_id, 0) + base_amount
        return refund

    refund: dict[str, int] = {}
    for item_id, amount in cost.items():
        for base_id, base_amount in expand_item(item_id, int(amount or 0)).items():
            scaled = int(base_amount * ratio)
            if scaled > 0:
                refund[base_id] = refund.get(base_id, 0) + scaled
    return refund


def evaluate_world_creation(player: dict, slots: dict[str, str], focus_id: str | None = None) -> tuple[bool, int, int]:
    blueprint = build_world_blueprint(player, slots, focus_id=focus_id)
    capacity = int(blueprint.get("capacity", 0) or 0)
    absurdity = int(blueprint.get("absurdity", 0) or 0)
    fail_chance = int(blueprint.get("fail_chance", 0) or 0)
    if fail_chance <= 0:
        return True, capacity, absurdity
    return random.randint(1, 100) > fail_chance, capacity, absurdity

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

    legacy_inventory, base_crystal_added = ensure_legacy_base_crystal(player)
    if base_crystal_added:
        player["inventory"] = legacy_inventory
        update_fields["inventory"] = legacy_inventory

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
    legacy_reset_used = bool(player.get("world_legacy_reset_used", False))
    if player.get("world_legacy_reset_used") != legacy_reset_used:
        player["world_legacy_reset_used"] = legacy_reset_used
        update_fields["world_legacy_reset_used"] = legacy_reset_used

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
        return "Старт уже пройден. Открывай /base и продолжай развитие мира.", False

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
    for item_id, amount in STARTER_WORLD_RESOURCE_GRANT.items():
        starters[item_id] = starters.get(item_id, 0) + amount
    for item_id, amount in starters.items():
        inventory[item_id] = inventory.get(item_id, 0) + amount

    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "inventory": inventory,
                "tutorial_step": 1,
                "discovered": sorted(set(player.get("discovered", [])) | set(STARTER_DISCOVERIES)),
                "world_tutorial": {"active": False, "created": False, "hints_shown": False},
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
        "• 🪵 Древесина x2\n"
        "• 🫧 Заготовки под учебный микробиом\n\n"
        "Я еще подсунул тебе несколько стартовых рецептов: Лава, Камень, Пар, Болото, Жизнь, Водоросли, Мох и Бактерия."
        " Открой /base, активируй Кристалл Базы и начни собирать первый биом слоями."
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
                "Сначала собери первый биом. Он даст миру место для дальнейшего развития.\n\n"
                "Для старта тебе пригодятся материалы под четыре слоя: основа, среда, энергия и жизнь.\n"
                "Базовые связки для старта:\n"
                "1. 🔥 + 🪨 = **Лава**\n"
                "2. 💧 + 🔥 = **Пар**\n"
                "3. 💧 + 🪨 = **Болото**\n"
                "4. 🎇 + **Болото** = **Жизнь**\n"
                "5. **Жизнь** + 💧 = **Водоросли**\n"
                "6. **Водоросли** + **Болото** = **Мох**\n"
                "7. **Жизнь** + **Болото** = **Бактерия**\n\n"
                "Когда соберёшь детали, возвращайся в штаб и укладывай их в чертёж биома по слоям.")
        
        # Завершаем туториал
        await db.alchemy_players.update_one(
            {"user_id": user_id},
            {"$set": {"tutorial_step": 2, "world_tutorial": {"active": True, "created": False, "hints_shown": False}}},
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
        narrative_text += "\n✨ Экспедиционный биом расширил окно поиска, поэтому редкости сегодня липнут охотнее."

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
