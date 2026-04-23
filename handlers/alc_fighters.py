import time
import uuid
from copy import deepcopy

from handlers.alc_recipes import BATTLE_UNITS, ELEMENTS

FIGHTER_SCHEMA_VERSION = 1
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


def get_name_unlock_xp(fighter: dict) -> int:
    return 20 if fighter.get("crafted_tier3") else 70


def can_name_fighter(fighter: dict) -> bool:
    return int(fighter.get("tier", 1)) >= 3 and int(fighter.get("xp", 0)) >= get_name_unlock_xp(fighter)


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
    refresh_fighter(fighter, now_ts)
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
    for fighter in get_player_fighters(player, now_ts):
        if fighter.get("status") != "available":
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
    fighters = [normalize_fighter(item, now_ts) for item in player.get("fighters", [])]
    changed = False

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
        elif fighter.get("status") not in {"captive"}:
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
