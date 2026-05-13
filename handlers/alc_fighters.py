import time
import uuid
from copy import deepcopy

from handlers.alc_recipes import BATTLE_UNITS, ELEMENTS

FIGHTER_SCHEMA_VERSION = 2
FATIGUE_RECOVERY_SECONDS = 6 * 3600
BARRACKS_CAPACITY = {0: 0, 1: 8, 2: 14, 3: 20, 4: 28, 5: 36}
FIGHTER_TIER_STAMINA = {1: 3, 2: 5, 3: 7}
FIGHTER_TIER_UPKEEP = {1: 1, 2: 2, 3: 3}
FIGHTER_RISK_BY_TIER = {1: 0.13, 2: 0.09, 3: 0.05}
FIGHTER_ZERO_STAMINA_RISK = 0.80
UPGRADE_XP_REQUIREMENTS = {2: 30, 3: 70}
UPGRADE_COSTS = {
    2: {"energy": 4, "spirits": 2},
    3: {"energy": 8, "spirits": 4},
}
HERO_XP_REQUIREMENT = 30

# Множители статов по тирам
TIER_MULTIPLIERS = {1: 1.0, 2: 2.0, 3: 4.0}

# Профильные бонусы для каждого юнита
UNIT_PROFILES = {
    # Тир-1
    "fire_swordsman": {"attack": 3, "speed": 1},
    "water_guard": {"defense": 3, "endurance": 1},
    "earth_shielder": {"defense": 3, "endurance": 1},
    "wind_scout": {"speed": 3, "perception": 1},
    "ash_pikeman": {"attack": 2, "speed": 2},
    "wave_spearman": {"attack": 2, "speed": 2},
    "stone_axeman": {"attack": 2, "endurance": 2},
    "storm_archer": {"attack": 2, "perception": 2},
    # Тир-2
    "rider_f": {"attack": 4, "speed": 2},
    "rider_w": {"defense": 3, "speed": 3},
    "rider_e": {"defense": 4, "endurance": 2},
    "rider_a": {"speed": 4, "perception": 2},
    "saboteur": {"attack": 3, "perception": 3},
    "medic": {"support": 4, "perception": 2},
    "engineer": {"support": 3, "endurance": 3},
    "sniper": {"attack": 4, "perception": 2},
    # Тир-3
    "mech": {"attack": 5, "defense": 3, "endurance": 2},
    "flame_tank": {"attack": 5, "defense": 2, "speed": 3},
    "tesla_tank": {"attack": 4, "speed": 4, "perception": 2},
    "hover": {"speed": 5, "defense": 3, "perception": 2},
    "drone": {"speed": 5, "perception": 3, "attack": 2},
    "artillery": {"attack": 6, "perception": 2, "endurance": 2},
    "walker": {"attack": 4, "defense": 4, "endurance": 2},
    "juggernaut": {"attack": 6, "defense": 4, "endurance": 2},
}

# Группы юнитов по духам для перекрафта
SPIRIT_UNIT_GROUPS = {
    "spirit_f": {
        1: ["fire_swordsman", "ash_pikeman"],
        2: ["rider_f", "saboteur"],
        3: ["flame_tank", "juggernaut"],
    },
    "spirit_w": {
        1: ["water_guard", "wave_spearman"],
        2: ["rider_w", "medic"],
        3: ["hover"],
    },
    "spirit_e": {
        1: ["earth_shielder", "stone_axeman"],
        2: ["rider_e", "engineer"],
        3: ["mech", "artillery", "walker"],
    },
    "spirit_a": {
        1: ["wind_scout", "storm_archer"],
        2: ["rider_a", "sniper"],
        3: ["tesla_tank", "drone"],
    },
}


def get_barracks_capacity(level: int) -> int:
    if level >= 5:
        return BARRACKS_CAPACITY[5]
    return BARRACKS_CAPACITY.get(level, 0)


def get_fighter_role_name(fighter: dict | None) -> str:
    if not fighter:
        return "Боец"
    custom = (fighter.get("custom_name") or "").strip()
    if custom:
        return custom
    unit_id = fighter.get("unit_id", "")
    return ELEMENTS.get(unit_id, unit_id or "Боец")


def get_fighter_title(fighter: dict | None) -> str:
    if not fighter:
        return "Боец"
    unit_name = ELEMENTS.get(fighter.get("unit_id", ""), fighter.get("unit_id", "Боец"))
    custom = (fighter.get("custom_name") or "").strip()
    if custom:
        return f"{custom} ({unit_name})"
    return unit_name


def get_fighter_stamina_cap(fighter: dict) -> int:
    return FIGHTER_TIER_STAMINA.get(int(fighter.get("tier", 1) or 1), 3)


def get_fighter_upkeep(fighter: dict) -> int:
    return FIGHTER_TIER_UPKEEP.get(int(fighter.get("tier", 1) or 1), 1)


def get_upgrade_spirit(fighter: dict) -> str:
    return BATTLE_UNITS.get(fighter.get("unit_id", ""), {}).get("fallback_spirit", "spirit_f")


def get_unit_profile(unit_id: str) -> dict:
    return UNIT_PROFILES.get(unit_id, {})


def generate_base_stats(tier: int, unit_id: str) -> dict:
    """
    Генерирует базовые статы для бойца.
    База = множитель_тира + рандом ±15% + профильный бонус
    """
    import random
    from handlers.alc_recipes import ELEMENT_COSTS

    multiplier = TIER_MULTIPLIERS.get(tier, 1.0)
    profile = get_unit_profile(unit_id)

    # Базовые статы (множитель тира)
    base = {
        "speed": int(multiplier * 3),
        "perception": int(multiplier * 3),
        "endurance": int(multiplier * 3),
        "attack": int(multiplier * 3),
        "defense": int(multiplier * 3),
        "support": int(multiplier * 3),
    }

    # Добавляем рандом ±15%
    for stat in base:
        variance = int(base[stat] * 0.15)
        if variance > 0:
            base[stat] += random.randint(-variance, variance)
        base[stat] = max(1, base[stat])

    # Добавляем профильный бонус
    for stat, bonus in profile.items():
        if stat in base:
            base[stat] += bonus

    return base


def get_upgrade_options(fighter: dict) -> list[dict]:
    """
    Возвращает варианты перекрафта для бойца.
    """
    current_tier = int(fighter.get("tier", 1))
    if current_tier >= 3:
        return []

    spirit = get_upgrade_spirit(fighter)
    next_tier = current_tier + 1
    group = SPIRIT_UNIT_GROUPS.get(spirit, {}).get(next_tier, [])

    options = []
    for unit_id in group:
        options.append({
            "unit_id": unit_id,
            "name": ELEMENTS.get(unit_id, unit_id),
            "profile": get_unit_profile(unit_id),
        })

    return options


def calculate_upgrade_cost(target_unit_id: str, current_unit_id: str) -> dict:
    """
    Рассчитывает стоимость перекрафта на основе разницы в стихийной стоимости.
    Скидка 50% за то, что боец уже имеет опыт и статы.
    """
    from handlers.alc_recipes import ELEMENT_COSTS

    target_cost = ELEMENT_COSTS.get(target_unit_id, {})
    current_cost = ELEMENT_COSTS.get(current_unit_id, {})

    if not target_cost:
        return {}

    # Разница в стихиях между целевым и текущим юнитом
    cost = {}
    target_elements = target_cost.get("elements", {})
    current_elements = current_cost.get("elements", {})

    for element, target_amount in target_elements.items():
        current_amount = current_elements.get(element, 0)
        diff = target_amount - current_amount
        if diff > 0:
            spirit_id = f"spirit_{element}"
            # Скидка 50% на перекрафт
            cost[spirit_id] = max(1, diff // 2)

    # Добавляем энергию (меньше, чем при создании с нуля)
    tier = BATTLE_UNITS.get(target_unit_id, {}).get("tier", 1)
    cost["energy"] = tier

    return cost


def is_hero(fighter: dict) -> bool:
    """Проверяет, является ли боец героем."""
    return fighter.get("is_hero", False)


def get_name_unlock_xp(fighter: dict) -> int:
    return 0 if is_hero(fighter) else (20 if fighter.get("crafted_tier3") else 70)


def can_name_fighter(fighter: dict) -> bool:
    return is_hero(fighter) or (int(fighter.get("tier", 1)) >= 3 and int(fighter.get("xp", 0)) >= get_name_unlock_xp(fighter))


def fighter_is_alive(fighter: dict) -> bool:
    return fighter.get("status") != "dead"


def fighter_is_busy(fighter: dict) -> bool:
    return fighter.get("status") in {"expedition", "raid"}


def fighter_can_join_expedition(fighter: dict, now_ts: int | None = None) -> bool:
    refresh_fighter(fighter, now_ts)
    return fighter.get("status") == "available" and int(fighter.get("stamina", 0)) > 0


def get_fighter_risk(fighter: dict, allow_zero_stamina: bool = True) -> float:
    refresh_fighter(fighter)
    stamina = int(fighter.get("stamina", 0))
    if stamina <= 0 and allow_zero_stamina:
        return FIGHTER_ZERO_STAMINA_RISK
    return FIGHTER_RISK_BY_TIER.get(int(fighter.get("tier", 1)), 0.13)


def create_fighter(unit_id: str, tier: int | None = None, *, crafted_tier3: bool = False, created_at: int | None = None) -> dict:
    now_ts = int(created_at or time.time())
    base_tier = int(BATTLE_UNITS.get(unit_id, {}).get("tier", 1))
    current_tier = max(base_tier, int(tier or base_tier))
    stats = generate_base_stats(current_tier, unit_id)
    fighter = {
        "id": uuid.uuid4().hex[:12],
        "unit_id": unit_id,
        "tier": current_tier,
        "xp": 0,
        "stamina": FIGHTER_TIER_STAMINA.get(current_tier, 3),
        "stamina_updated_at": now_ts,
        "custom_name": "",
        "history": [],
        "crafted_tier3": bool(crafted_tier3 and current_tier >= 3),
        "raids": 0,
        "expeditions": 0,
        "status": "available",
        "created_at": now_ts,
        "origin": "crafted_tier3" if crafted_tier3 else "recruited",
        "stats": stats,
        "is_hero": False,
    }
    return normalize_fighter(fighter, now_ts)


def normalize_fighter(fighter: dict, now_ts: int | None = None) -> dict:
    current = deepcopy(fighter)
    current["id"] = str(current.get("id") or uuid.uuid4().hex[:12])
    current["unit_id"] = str(current.get("unit_id") or "fire_swordsman")
    current["tier"] = max(int(BATTLE_UNITS.get(current["unit_id"], {}).get("tier", 1)), int(current.get("tier", 1) or 1))
    current["xp"] = max(0, int(current.get("xp", 0) or 0))
    current["raids"] = max(0, int(current.get("raids", 0) or 0))
    current["expeditions"] = max(0, int(current.get("expeditions", 0) or 0))
    current["status"] = current.get("status") or "available"
    current["custom_name"] = str(current.get("custom_name") or "").strip()[:32]
    current["history"] = [str(line).strip()[:160] for line in current.get("history", []) if str(line).strip()][:12]
    current["crafted_tier3"] = bool(current.get("crafted_tier3")) or current["tier"] >= 3 and bool(current.get("origin") == "crafted_tier3")
    current["origin"] = str(current.get("origin") or "recruited")
    current["created_at"] = int(current.get("created_at") or now_ts or time.time())
    current["stamina_updated_at"] = int(current.get("stamina_updated_at") or current["created_at"])
    current["stamina"] = max(0, int(current.get("stamina", get_fighter_stamina_cap(current)) or 0))
    current["is_hero"] = bool(current.get("is_hero", False))

    # Нормализация статов
    if "stats" not in current or not current["stats"]:
        current["stats"] = generate_base_stats(current["tier"], current["unit_id"])
    else:
        stats = current["stats"]
        for stat in ["speed", "perception", "endurance", "attack", "defense", "support"]:
            if stat not in stats or stats[stat] is None:
                stats[stat] = 1
            stats[stat] = max(1, int(stats[stat]))

    refresh_fighter(current, now_ts)
    return current


def refresh_fighter(fighter: dict, now_ts: int | None = None) -> dict:
    now_ts = int(now_ts or time.time())
    cap = get_fighter_stamina_cap(fighter)
    current = max(0, int(fighter.get("stamina", cap) or 0))
    updated_at = int(fighter.get("stamina_updated_at", now_ts) or now_ts)
    if current >= cap:
        fighter["stamina"] = cap
        fighter["stamina_updated_at"] = now_ts
        return fighter
    if updated_at >= now_ts:
        fighter["stamina"] = min(cap, current)
        fighter["stamina_updated_at"] = updated_at
        return fighter
    recovered = (now_ts - updated_at) // FATIGUE_RECOVERY_SECONDS
    if recovered <= 0:
        fighter["stamina"] = min(cap, current)
        return fighter
    fighter["stamina"] = min(cap, current + recovered)
    fighter["stamina_updated_at"] = updated_at + recovered * FATIGUE_RECOVERY_SECONDS
    if fighter["stamina"] >= cap:
        fighter["stamina_updated_at"] = now_ts
    return fighter


def refresh_fighters(fighters: list[dict], now_ts: int | None = None) -> list[dict]:
    now_ts = int(now_ts or time.time())
    return [normalize_fighter(fighter, now_ts) for fighter in fighters]


def get_player_fighters(player: dict, now_ts: int | None = None) -> list[dict]:
    fighters = player.get("fighters", [])
    refreshed = refresh_fighters(fighters, now_ts)
    player["fighters"] = refreshed
    return refreshed


def find_fighter(player: dict, fighter_id: str) -> dict | None:
    for fighter in player.get("fighters", []):
        if fighter.get("id") == fighter_id:
            return fighter
    return None


def build_fighter_snapshot(fighter: dict) -> dict:
    return {
        "id": fighter.get("id"),
        "unit_id": fighter.get("unit_id"),
        "tier": fighter.get("tier", 1),
        "name": get_fighter_title(fighter),
        "custom_name": fighter.get("custom_name", ""),
    }


def build_fighter_label(fighter: dict) -> str:
    refresh_fighter(fighter)
    title = get_fighter_title(fighter)
    return f"{title} | Т{fighter['tier']} | XP {fighter['xp']} | Бодрость {fighter['stamina']}/{get_fighter_stamina_cap(fighter)}"


def can_fighter_upgrade(player: dict, fighter: dict) -> bool:
    """
    Проверяет, доступен ли апгрейд для бойца.
    """
    options = get_upgrade_options(fighter)
    if not options:
        return False

    # Проверка XP
    current_tier = int(fighter.get("tier", 1))
    required_xp = 30 if current_tier == 1 else 70
    if int(fighter.get("xp", 0)) < required_xp:
        return False

    inventory = player.get("inventory", {})
    for opt in options:
        cost = calculate_upgrade_cost(opt['unit_id'], fighter.get('unit_id'))
        if cost and can_pay_cost(inventory, cost):
            return True
    return False


def get_upgrade_cost(target_tier: int, fighter: dict) -> dict:
    base_cost = deepcopy(UPGRADE_COSTS.get(target_tier, {}))
    if not base_cost:
        return {}
    base_cost[get_upgrade_spirit(fighter)] = base_cost.pop("spirits")
    return base_cost


def can_pay_cost(inventory: dict, cost: dict) -> bool:
    return all(int(inventory.get(item_id, 0)) >= amount for item_id, amount in cost.items())


def spend_cost(inventory: dict, cost: dict):
    for item_id, amount in cost.items():
        inventory[item_id] = int(inventory.get(item_id, 0)) - amount
        if inventory[item_id] <= 0:
            inventory.pop(item_id, None)


def grant_mission_progress(fighter: dict, mission_type: str, now_ts: int | None = None):
    import random
    refresh_fighter(fighter, now_ts)
    if mission_type == "raid":
        # Рейды дают 2 XP
        fighter["xp"] = int(fighter.get("xp", 0)) + 2
        fighter["raids"] = int(fighter.get("raids", 0)) + 1
    elif mission_type == "expedition":
        # Экспедиции дают 1 XP с шансом 50%
        if random.random() < 0.5:
            fighter["xp"] = int(fighter.get("xp", 0)) + 1
        fighter["expeditions"] = int(fighter.get("expeditions", 0)) + 1
    else:
        fighter["xp"] = int(fighter.get("xp", 0)) + 1
        if mission_type == "raid":
            fighter["raids"] = int(fighter.get("raids", 0)) + 1
        else:
            fighter["expeditions"] = int(fighter.get("expeditions", 0)) + 1


def consume_fighter_stamina(fighter: dict, now_ts: int | None = None):
    now_ts = int(now_ts or time.time())
    refresh_fighter(fighter, now_ts)
    fighter["stamina"] = max(0, int(fighter.get("stamina", 0)) - 1)
    fighter["stamina_updated_at"] = now_ts


def hall_of_fame_entry(fighter: dict, reason: str, now_ts: int | None = None) -> dict:
    now_ts = int(now_ts or time.time())
    return {
        "id": fighter.get("id"),
        "name": fighter.get("custom_name"),
        "unit_id": fighter.get("unit_id"),
        "tier": fighter.get("tier", 1),
        "raids": fighter.get("raids", 0),
        "expeditions": fighter.get("expeditions", 0),
        "xp": fighter.get("xp", 0),
        "died_at": now_ts,
        "reason": reason,
    }


def get_available_fighters(player: dict, *, include_zero_stamina: bool = True, now_ts: int | None = None) -> list[dict]:
    now_ts = int(now_ts or time.time())
    fighters = []
    garrison = set(player.get("fort_garrison", []))
    for fighter in get_player_fighters(player, now_ts):
        if fighter.get("status") != "available":
            continue
        if fighter.get("id") in garrison:
            continue
        if not include_zero_stamina and int(fighter.get("stamina", 0)) <= 0:
            continue
        fighters.append(fighter)
    fighters.sort(
        key=lambda item: (
            int(item.get("status") != "available"),
            int(item.get("tier", 1)),
            -int(item.get("stamina", 0)),
            get_fighter_title(item),
        )
    )
    return fighters


def normalize_player_fighters(player: dict, now_ts: int | None = None) -> dict:
    now_ts = int(now_ts or time.time())
    inventory = deepcopy(player.get("inventory", {}))
    current_schema_version = int(player.get("fighter_schema_version", 0))
    needs_migration = current_schema_version < FIGHTER_SCHEMA_VERSION

    fighters = [normalize_fighter(item, now_ts) for item in player.get("fighters", [])]
    changed = False

    # Миграция для старых версий схемы
    if needs_migration:
        migrated_count = 0
        for fighter in fighters:
            # Добавляем статы если их нет (версия 1 -> 2)
            if "stats" not in fighter or not fighter["stats"]:
                fighter["stats"] = generate_base_stats(fighter.get("tier", 1), fighter.get("unit_id", "fire_swordsman"))
                changed = True
                migrated_count += 1
            # Добавляем флаг героя если его нет
            if "is_hero" not in fighter:
                fighter["is_hero"] = False
                changed = True
        if migrated_count > 0:
            # Логируем миграцию (можно добавить в историю игрока)
            pass

    def append_fighter(unit_id: str, *, tier: int | None = None, status: str = "available", crafted_tier3: bool = False) -> str:
        nonlocal changed
        fighter = create_fighter(unit_id, tier=tier, crafted_tier3=crafted_tier3, created_at=now_ts)
        fighter["status"] = status
        fighters.append(fighter)
        changed = True
        return fighter["id"]

    for unit_id in list(BATTLE_UNITS.keys()):
        amount = int(inventory.get(unit_id, 0) or 0)
        if amount <= 0:
            continue
        crafted_tier3 = BATTLE_UNITS[unit_id]["tier"] >= 3
        for _ in range(amount):
            append_fighter(unit_id, tier=BATTLE_UNITS[unit_id]["tier"], status="available", crafted_tier3=crafted_tier3)
        inventory.pop(unit_id, None)

    normalized_expeditions = []
    for expedition in player.get("active_expeditions", []):
        current = deepcopy(expedition)
        fighter_ids = list(current.get("fighter_ids", []))
        if not fighter_ids and current.get("fighters"):
            snapshots = []
            for unit_id in current.get("fighters", []):
                fighter_id = append_fighter(unit_id, tier=BATTLE_UNITS.get(unit_id, {}).get("tier", 1), status="expedition", crafted_tier3=BATTLE_UNITS.get(unit_id, {}).get("tier", 1) >= 3)
                fighter = next(item for item in fighters if item["id"] == fighter_id)
                snapshots.append(build_fighter_snapshot(fighter))
                fighter_ids.append(fighter_id)
            current["fighter_ids"] = fighter_ids
            current["fighter_snapshots"] = snapshots
            changed = True
        elif fighter_ids and not current.get("fighter_snapshots"):
            snapshots = []
            for fighter_id in fighter_ids:
                fighter = next((item for item in fighters if item["id"] == fighter_id), None)
                if fighter:
                    fighter["status"] = current.get("status") == "arrived" and "available" or "expedition"
                    snapshots.append(build_fighter_snapshot(fighter))
            current["fighter_snapshots"] = snapshots
            changed = True
        normalized_expeditions.append(current)

    squad = deepcopy(player.get("raid_squad", {"units": [], "energy_invested": 0, "created_at": 0}))
    if squad.get("units") and not squad.get("fighter_ids"):
        fighter_ids = []
        for unit_id in squad.get("units", []):
            fighter_id = append_fighter(unit_id, tier=BATTLE_UNITS.get(unit_id, {}).get("tier", 1), status="raid", crafted_tier3=BATTLE_UNITS.get(unit_id, {}).get("tier", 1) >= 3)
            fighter = next(item for item in fighters if item["id"] == fighter_id)
            fighter_ids.append(fighter_id)
        squad["fighter_ids"] = fighter_ids
        squad["fighter_snapshots"] = [build_fighter_snapshot(next(item for item in fighters if item["id"] == fighter_id)) for fighter_id in fighter_ids]
        changed = True
    elif squad.get("fighter_ids"):
        snapshots = []
        for fighter_id in squad.get("fighter_ids", []):
            fighter = next((item for item in fighters if item["id"] == fighter_id), None)
            if fighter:
                fighter["status"] = "raid"
                snapshots.append(build_fighter_snapshot(fighter))
        if squad.get("fighter_snapshots") != snapshots:
            squad["fighter_snapshots"] = snapshots
            changed = True

    for fighter in fighters:
        if fighter.get("status") == "dead":
            continue
        if any(fighter.get("id") in expedition.get("fighter_ids", []) and expedition.get("status") != "arrived" for expedition in normalized_expeditions):
            fighter["status"] = "expedition"
        elif fighter.get("id") in squad.get("fighter_ids", []):
            fighter["status"] = "raid"
        elif fighter.get("status") not in {"captive", "garrison"}:
            fighter["status"] = "available"

    player["inventory"] = inventory
    player["fighters"] = refresh_fighters(fighters, now_ts)
    player["active_expeditions"] = normalized_expeditions
    player["raid_squad"] = {
        "fighter_ids": squad.get("fighter_ids", []),
        "fighter_snapshots": squad.get("fighter_snapshots", []),
        "energy_invested": int(squad.get("energy_invested", 0) or 0),
        "created_at": int(squad.get("created_at", 0) or 0),
    }
    player["hall_of_fame"] = player.get("hall_of_fame", [])
    player["fighter_name_pending"] = player.get("fighter_name_pending")
    player["fighter_schema_version"] = FIGHTER_SCHEMA_VERSION
    player["_fighters_changed"] = changed
    return player
