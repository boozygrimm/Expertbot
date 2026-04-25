import random
import time
import uuid
import asyncio
from copy import deepcopy

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot_instance import bot
from database import db
from handlers.alc_core import (
    DEFAULT_ACTION_COUNTERS,
    get_or_create_player,
    increment_action_counter,
)
from handlers.alc_fighters import (
    build_fighter_snapshot,
    consume_fighter_stamina,
    create_fighter,
    find_fighter,
    get_available_fighters,
    get_fighter_risk,
    get_fighter_stamina_cap,
    get_fighter_title,
    get_player_fighters,
    grant_mission_progress,
    hall_of_fame_entry,
)
from handlers.alc_expeditions import get_expedition_team_limit, get_role_name
from handlers.alc_recipes import BATTLE_UNITS, ELEMENTS
from handlers.pole_chudes import delete_after_delay

router = Router()

FORT_SLOTS = {1: ["moat"], 2: ["moat", "catapult"], 3: ["moat", "catapult", "aura"]}
TRAP_LABELS = {"moat": "Ров", "catapult": "Катапульта", "aura": "Распылитель"}
raid_drafts: dict[int, list[str]] = {}
raid_pages: dict[int, int] = {}
RAID_ROWS_PER_PAGE = 10
fort_item_pages: dict[tuple[int, str], int] = {}
fort_captive_pages: dict[int, int] = {}
LIVE_PLAYER_WINDOW = 36 * 3600
RAID_REPEAT_COOLDOWN = 18 * 3600
RAID_PROTECTION_ON_SUCCESS = 12 * 3600
FORT_CAPTIVES_PER_PAGE = 8
FORT_SLOT_ROWS_PER_PAGE = 8


def cleanup_raid_draft(player: dict, user_id: int) -> list[str]:
    valid_ids = {fighter["id"] for fighter in get_available_fighters(player, include_zero_stamina=True)}
    cleaned = [fighter_id for fighter_id in raid_drafts.get(user_id, []) if fighter_id in valid_ids]
    raid_drafts[user_id] = cleaned
    return cleaned


def get_el_name(item_id: str) -> str:
    label = ELEMENTS.get(item_id, item_id)
    if isinstance(label, str):
        parts = label.split(" ", 1)
        return parts[1] if len(parts) > 1 else label
    return str(label)


def get_el_emoji(item_id: str) -> str:
    label = ELEMENTS.get(item_id, item_id)
    if isinstance(label, str):
        parts = label.split(" ", 1)
        return parts[0] if len(parts) > 1 else "❓"
    return "❓"


def shorten_button_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def get_raid_power(units: list, energy: int) -> int:
    unit_ids = [unit["unit_id"] if isinstance(unit, dict) else unit for unit in units]
    tiers = [int(unit.get("tier", BATTLE_UNITS.get(unit.get("unit_id", ""), {}).get("tier", 1))) if isinstance(unit, dict) else BATTLE_UNITS[unit]["tier"] for unit in units]
    power = sum(tier * 18 + 10 for tier in tiers)
    power += energy * 3
    if "sniper" in unit_ids:
        power += 12
    if "saboteur" in unit_ids:
        power += 10
    if "medic" in unit_ids:
        power += 8
    return power


def get_fort_power(player: dict) -> int:
    fort_level = player.get("buildings", {}).get("fort", 0)
    base_power = fort_level * 28
    loaded_traps = sum(1 for value in player.get("fort_defense", {}).values() if value)
    captive_bonus = min(18, len(player.get("captives", [])) * 2)
    return base_power + loaded_traps * 12 + captive_bonus


def get_allowed_fort_slots(fort_level: int) -> list[str]:
    if fort_level >= 3:
        return FORT_SLOTS[3]
    if fort_level == 2:
        return FORT_SLOTS[2]
    if fort_level == 1:
        return FORT_SLOTS[1]
    return []


def is_live_player(player: dict) -> bool:
    return int(time.time()) - int(player.get("last_mine", 0)) <= LIVE_PLAYER_WINDOW


def get_recent_raid_ts(attacker: dict, defender_id: int) -> int:
    history = attacker.get("raid_history", {})
    return int(history.get(str(defender_id), 0) or 0)


def update_raid_history(attacker: dict, defender_id: int) -> dict:
    history = deepcopy(attacker.get("raid_history", {}))
    history[str(defender_id)] = int(time.time())
    attacker["raid_history"] = history
    return history


def build_public_raid_line(attacker: str, defender: str, success: bool, live_target: bool, trap_triggered: bool) -> str:
    if success and live_target:
        pool = [
            f"Отряд {attacker} урвал у {defender} немного припасов и быстро слился до ответа гарнизона.",
            f"{attacker} продавил оборону {defender}, но вынес только то, что успел схватить на бегу.",
            f"Налёт {attacker} на {defender} удался, хотя форт заставил платить временем и бойцами.",
        ]
    elif success:
        pool = [
            f"{attacker} вскрыл склады {defender} и унёс добычу без лишнего шума.",
            f"Форт {defender} просел, и {attacker} собрал трофеи раньше, чем подняли тревогу.",
            f"Рейд {attacker} на {defender} закончился удачей и пыльными мешками лута.",
        ]
    elif trap_triggered:
        pool = [
            f"Форт {defender} щёлкнул ловушками так, что {attacker} пришлось отползать без добычи.",
            f"{attacker} вляпался в оборону {defender} и утащил домой только плохие воспоминания.",
            f"Рейд {attacker} захлебнулся у стен {defender}: ловушки сработали как часы.",
        ]
    else:
        pool = [
            f"{attacker} сунулся к {defender}, но форт не дал превратить вылазку в лёгкую прогулку.",
            f"Налёт {attacker} на {defender} сорвался: защитники успели собраться быстрее, чем хотелось.",
            f"{attacker} не продавил {defender} и ушёл чинить строй после неудачного штурма.",
        ]
    return random.choice(pool)


def build_raid_dm_log(units: list, trap_item: str | None, success: bool, live_target: bool, loot: dict, captives_taken: list[dict]) -> str:
    named_units = [get_role_name(unit) for unit in units]
    lead_unit = named_units[0] if named_units else "Командир"
    support_unit = named_units[1] if len(named_units) > 1 else lead_unit
    trap_name = ELEMENTS.get(trap_item, trap_item) if trap_item else None
    loot_line = ", ".join(ELEMENTS.get(item_id, item_id) for item_id in loot) if loot else "ничего ценного"

    openers = [
        f"{lead_unit} повёл отряд коротким маршрутом и решил брать форт без долгой раскачки.",
        f"{lead_unit} первым полез под стены и сразу проверил, насколько у хозяев крепкие нервы.",
        f"{lead_unit} собрал группу тихо, но штурм всё равно вышел шумным.",
    ]
    if trap_name:
        openers.append(f"{lead_unit} заметил заряд в ловушке: внутри был {trap_name}, и темп атаки сразу просел.")

    if success:
        middles = [
            f"{support_unit} удержал строй, пока остальные снимали со склада {loot_line}.",
            f"{support_unit} прикрыл отход, и отряд унёс {loot_line}, не задерживаясь под ответкой.",
            f"{support_unit} не дал расползтись строю, поэтому трофеи вроде {loot_line} всё-таки ушли в обоз.",
        ]
        endings = [
            "Рейд завершён быстро: затяжного разгрома не вышло, только короткий удар и отход.",
            "Победа получилась рабочей, без тотального выноса и без лишнего фарса.",
            "Штурм удался, но форт успел огрызнуться, так что лёгкой прогулкой это не назвать.",
        ]
        if live_target:
            endings.append("Живой владелец держал оборону в тонусе, поэтому унести получилось только часть запасов.")
    else:
        middles = [
            f"{support_unit} пытался прикрыть отход, но темп потеряли ещё у внешнего кольца.",
            f"{support_unit} стянул бойцов назад, когда стало ясно, что форт пережуёт затяжную атаку.",
            f"{support_unit} собрал остатки строя и приказал не превращать вылазку в самоубийство.",
        ]
        endings = [
            "Рейд сорвался: оборона оказалась плотнее расчёта, а времени на второй заход не осталось.",
            "Штурм захлебнулся, и отряду пришлось уходить, пока потери не стали совсем глупыми.",
            "До складов не добрались: форт встретил жёстко и быстро сбил атакующий темп.",
        ]
        if captives_taken:
            endings.append("Часть бойцов не вернулась из-под стен и ушла в плен к защитникам.")

    return " ".join((random.choice(openers), random.choice(middles), random.choice(endings)))


def render_raids_menu(player: dict, draft: list[str] | None = None) -> str:
    squad = player.get("raid_squad", {"fighter_ids": [], "energy_invested": 0})
    guild_level = player.get("buildings", {}).get("guild", 0)
    team_limit = get_expedition_team_limit(guild_level)
    lines = [
        "☠️ **РЕЙДЕРЫ**",
        f"Лимит бойцов: {team_limit}. Для запуска /грабить нужен готовый отряд и энергия.",
        "💡 Справка: рейд-отряд всегда 3-5 бойцов. Меньше нельзя, это ударная группа, а не разведка.",
        "",
    ]
    if draft is not None:
        if draft:
            fighters = [find_fighter(player, fighter_id) for fighter_id in draft]
            lines.append("Черновик: " + ", ".join(get_fighter_title(fighter) for fighter in fighters if fighter))
        else:
            lines.append("Черновик пуст.")
        lines.append("")

    if squad.get("fighter_snapshots"):
        lines.append("Подготовленный рейд-отряд:")
        lines.append("• " + ", ".join(get_role_name(unit) for unit in squad["fighter_snapshots"]))
        lines.append(f"• Вложено энергии: {squad.get('energy_invested', 0)}")
    else:
        lines.append("Готового рейд-отряда нет.")
    return "\n".join(lines)


def build_raids_keyboard(player: dict, draft: list[str]) -> types.InlineKeyboardMarkup:
    inventory = player.get("inventory", {})
    guild_level = player.get("buildings", {}).get("guild", 0)
    team_limit = get_expedition_team_limit(guild_level)
    grid_size = int(player.get("inv_grid", 3) or 3)
    if grid_size not in {1, 2, 3, 4}:
        grid_size = 3
    fighters = get_available_fighters(player, include_zero_stamina=True)
    user_id = player.get("user_id", 0)
    items_per_page = grid_size * RAID_ROWS_PER_PAGE
    total_pages = max(1, (len(fighters) + items_per_page - 1) // items_per_page)
    current_page = raid_pages.get(user_id, 0)
    if current_page >= total_pages:
        current_page = max(0, total_pages - 1)
    raid_pages[user_id] = current_page
    page_start = current_page * items_per_page
    page_fighters = fighters[page_start: page_start + items_per_page]
    builder = InlineKeyboardBuilder()
    row = []
    for fighter in page_fighters:
        marker = "✅" if fighter["id"] in draft else "▫️"
        row.append(
            types.InlineKeyboardButton(
                text=f"{marker} {get_fighter_title(fighter)} [{fighter['stamina']}/{get_fighter_stamina_cap(fighter)}]",
                callback_data=f"raid_pick:{fighter['id']}",
            )
        )
        if len(row) == grid_size:
            builder.row(*row)
            row = []
    if row:
        builder.row(*row)

    if total_pages > 1:
        nav_row = []
        if current_page > 0:
            nav_row.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"raid_page:{current_page - 1}"))
        if current_page < total_pages - 1:
            nav_row.append(types.InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"raid_page:{current_page + 1}"))
        if nav_row:
            builder.row(*nav_row)

    next_grid = {1: 2, 2: 3, 3: 4, 4: 1}[grid_size]
    builder.button(text=f"⚙️ Сетка: {grid_size} в ряд (Жми для {next_grid})", callback_data=f"raid_set_grid:{next_grid}")
    can_prepare = 3 <= len(draft) <= team_limit and inventory.get("energy", 0) > 0
    builder.button(text="⚡ Подготовить", callback_data="raid_prepare" if can_prepare else "raid_nope")
    builder.button(text="🧹 Очистить", callback_data="raid_clear")
    if player.get("raid_squad", {}).get("fighter_ids"):
        builder.button(text="↩️ Расформировать", callback_data="raid_disband")
    builder.button(text="🔙 В Казармы", callback_data="barracks_open")
    builder.adjust(1)
    return builder.as_markup()


def render_fort_menu(player: dict, page: int = 0) -> str:
    fort_level = player.get("buildings", {}).get("fort", 0)
    defense = player.get("fort_defense", {})
    captives = player.get("captives", [])
    lines = [f"🏰 **ФОРТ** (Ур. {fort_level})"]
    if fort_level <= 0:
        lines.append("Форт ещё не построен.")
        return "\n".join(lines)
    lines.append("💡 Справка: в каждый открытый слот можно положить любой предмет. При срабатывании ловушки предмет сгорает.")
    lines.append("Ловушки:")
    for slot_id in get_allowed_fort_slots(fort_level):
        item_id = defense.get(slot_id)
        lines.append(f"• {TRAP_LABELS[slot_id]}: {ELEMENTS.get(item_id, item_id) if item_id else 'пусто'}")
    lines.append("")
    if captives:
        lines.append("Пленные:")
        total_pages = max(1, (len(captives) + FORT_CAPTIVES_PER_PAGE - 1) // FORT_CAPTIVES_PER_PAGE)
        if page >= total_pages:
            page = max(0, total_pages - 1)
        start = page * FORT_CAPTIVES_PER_PAGE
        for captive in captives[start:start + FORT_CAPTIVES_PER_PAGE]:
            lines.append(f"• {get_role_name(captive.get('unit_id', 'ghost'))} [{captive.get('id', '')[:4]}]")
        if total_pages > 1:
            lines.append("")
            lines.append(f"Страница подвала: {page + 1}/{total_pages}")
    else:
        lines.append("Подвал пока пуст.")
    return "\n".join(lines)


def build_fort_keyboard(player: dict, page: int = 0) -> types.InlineKeyboardMarkup:
    fort_level = player.get("buildings", {}).get("fort", 0)
    builder = InlineKeyboardBuilder()
    for slot_id in get_allowed_fort_slots(fort_level):
        builder.button(text=f"🧱 Настроить {TRAP_LABELS[slot_id]}", callback_data=f"fort_slot:{slot_id}")
    captives = player.get("captives", [])
    total_pages = max(1, (len(captives) + FORT_CAPTIVES_PER_PAGE - 1) // FORT_CAPTIVES_PER_PAGE)
    if page >= total_pages:
        page = max(0, total_pages - 1)
    start = page * FORT_CAPTIVES_PER_PAGE
    for captive in captives[start:start + FORT_CAPTIVES_PER_PAGE]:
        label = get_role_name(captive.get("unit_id", "ghost"))
        builder.button(text=f"🪤 {label}", callback_data=f"captive_view:{captive['id']}")
    if total_pages > 1:
        builder.button(text=f"📄 Подвал {page + 1}/{total_pages}", callback_data="fort_page_info")
        if page > 0:
            builder.button(text="⬅️ Подвал", callback_data=f"fort_captives_page:{page - 1}")
        if page < total_pages - 1:
            builder.button(text="Подвал ➡️", callback_data=f"fort_captives_page:{page + 1}")
    builder.button(text="🔙 В Штаб", callback_data="alc:base_return")
    builder.adjust(1)
    return builder.as_markup()


def build_fort_slot_keyboard(player: dict, slot_id: str, page: int = 0) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    inventory = player.get("inventory", {})
    grid_size = player.get("inv_grid", 3)
    items = [(item_id, amount) for item_id, amount in inventory.items() if amount > 0 and item_id not in BATTLE_UNITS]
    items.sort(key=lambda item: ELEMENTS.get(item[0], item[0]))
    page_size = grid_size * FORT_SLOT_ROWS_PER_PAGE
    total_pages = max(1, (len(items) + page_size - 1) // page_size)
    if page >= total_pages:
        page = max(0, total_pages - 1)
    start = page * page_size
    subset = items[start:start + page_size]
    row = []
    for item_id, amount in subset:
        name = get_el_name(item_id)
        emoji = get_el_emoji(item_id)
        if grid_size == 4:
            text = f"{shorten_button_text(name, 8)} {amount}"
        elif grid_size == 3:
            text = f"{emoji} {shorten_button_text(name, 7)}"
        else:
            text = f"{emoji} {shorten_button_text(name, 12)} {amount}"
        row.append(types.InlineKeyboardButton(text=text, callback_data=f"fort_load:{slot_id}:{item_id}"))
        if len(row) == grid_size:
            builder.row(*row)
            row = []
    if row:
        builder.row(*row)
    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"fort_page:{slot_id}:{page-1}"))
        if page < total_pages - 1:
            nav_row.append(types.InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"fort_page:{slot_id}:{page+1}"))
        if nav_row:
            builder.row(*nav_row)
    next_grid = 3 if grid_size == 2 else 4 if grid_size == 3 else 2
    controls = InlineKeyboardBuilder()
    controls.button(text=f"⚙️ Сетка: {grid_size} в ряд (Жми для {next_grid})", callback_data=f"fort_set_grid:{slot_id}:{next_grid}")
    if player.get("fort_defense", {}).get(slot_id):
        controls.button(text="♻️ Снять предмет", callback_data=f"fort_unload:{slot_id}")
    controls.button(text="🔙 В Форт", callback_data="fort_open")
    controls.adjust(1)
    builder.attach(controls)
    return builder.as_markup()


def build_close_markup() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Закрыть", callback_data="close_pm_report")
    return builder.as_markup()


def build_pve_offer_markup() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⚔️ Да, в атаку!", callback_data="raid_pve_yes")
    builder.button(text="🏠 Нет, домой", callback_data="raid_pve_no")
    builder.adjust(1)
    return builder.as_markup()


def choose_loot_from_inventory(inventory: dict, count: int = 3, max_fraction: float = 0.35, hard_cap: int = 8) -> dict:
    lootable = [(item_id, amount) for item_id, amount in inventory.items() if amount > 0 and item_id not in BATTLE_UNITS]
    random.shuffle(lootable)
    stolen = {}
    for item_id, amount in lootable[:count]:
        limit = max(1, min(amount, hard_cap, int(amount * max_fraction) or 1))
        take = min(amount, random.randint(1, limit))
        stolen[item_id] = take
    return stolen


def maybe_trigger_trap(defender: dict, attacker_units: list) -> tuple[bool, str | None, str | None]:
    fort_level = defender.get("buildings", {}).get("fort", 0)
    if fort_level <= 0:
        return False, None, None
    loaded = [(slot_id, item_id) for slot_id, item_id in defender.get("fort_defense", {}).items() if item_id and slot_id in get_allowed_fort_slots(fort_level)]
    if not loaded:
        return False, None, None
    slot_id, item_id = random.choice(loaded)
    sample = random.choice(attacker_units)
    attacker_tier = int(sample.get("tier", BATTLE_UNITS.get(sample.get("unit_id", ""), {}).get("tier", 1))) if isinstance(sample, dict) else BATTLE_UNITS[sample]["tier"]
    difficulty = 0.10 + min(0.20, attacker_tier * 0.04)
    trigger = random.random() < 0.35 + difficulty
    return trigger, slot_id, item_id


def build_captive(fighter, owner_id: int | None = None) -> dict:
    if isinstance(fighter, dict):
        return {
            "id": uuid.uuid4().hex[:10],
            "unit_id": fighter.get("unit_id"),
            "tier": fighter.get("tier", 1),
            "owner_id": owner_id,
            "custom_name": fighter.get("custom_name", ""),
            "raids": fighter.get("raids", 0),
            "expeditions": fighter.get("expeditions", 0),
            "crafted_tier3": fighter.get("crafted_tier3", False),
        }
    return {"id": uuid.uuid4().hex[:10], "unit_id": fighter, "tier": BATTLE_UNITS.get(fighter, {}).get("tier", 1), "owner_id": owner_id}


async def resolve_raid(attacker: dict, defender: dict | None, attacker_name: str, defender_name: str, is_pve: bool) -> dict:
    squad = attacker.get("raid_squad", {"fighter_ids": [], "fighter_snapshots": [], "energy_invested": 0})
    units = squad.get("fighter_snapshots", [])
    attack_power = get_raid_power(units, squad.get("energy_invested", 0))
    defense_power = 20 + random.randint(0, 18)
    trap_triggered, trap_slot, trap_item = False, None, None
    defender_inventory = {}
    defender_captives = []
    live_target = False

    if defender:
        live_target = is_live_player(defender)
        defense_power += get_fort_power(defender)
        if live_target:
            defense_power += 26
        defender_inventory = defender.get("inventory", {})
        defender_captives = defender.get("captives", [])
        trap_triggered, trap_slot, trap_item = maybe_trigger_trap(defender, units)
        if trap_triggered and trap_item:
            defense_power += 22 + random.randint(4, 16)
    else:
        defense_power += random.randint(10, 25)

    success = attack_power + random.randint(0, 25) >= defense_power + random.randint(0, 20)
    survivors = units.copy()
    captives_taken = []
    loot = {}
    scraps = {}
    dead_units = []

    if success:
        if defender:
            if live_target:
                loot = choose_loot_from_inventory(defender_inventory, count=2, max_fraction=0.18, hard_cap=4)
            else:
                loot = choose_loot_from_inventory(defender_inventory, count=3, max_fraction=0.30, hard_cap=7)
        else:
            loot = random.choice(
                [
                    {"energy": random.randint(2, 6), "ghost": random.randint(1, 2)},
                    {"w": 8, "e": 8, "pack_s": 1},
                    {"f": 6, "a": 6, "recipe_scroll": 1},
                ]
            )
        if defender and not live_target and random.random() < 0.20 and survivors:
            captured_unit = random.choice(survivors)
            loot["ghost"] = loot.get("ghost", 0) + 1 if is_pve else loot.get("ghost", 0)
            if defender:
                defender_captives.append(build_captive(captured_unit, attacker.get("user_id")))
                captives_taken.append(captured_unit)
                survivors = [fighter for fighter in survivors if fighter.get("id") != captured_unit.get("id")]
    else:
        lost_count = random.randint(1, max(1, len(units) // 2))
        random.shuffle(survivors)
        lost_units = survivors[:lost_count]
        survivors = survivors[lost_count:]
        if defender and trap_triggered and trap_item and lost_units:
            captive_unit = lost_units[0]
            defender_captives.append(build_captive(captive_unit, attacker.get("user_id")))
            captives_taken.append(captive_unit)
            lost_units = lost_units[1:]
        dead_units.extend(lost_units)
        for unit in lost_units:
            tier = int(unit.get("tier", 1))
            scraps["w"] = scraps.get("w", 0) + tier
            scraps["e"] = scraps.get("e", 0) + tier

    raid_casualties = []
    for fighter in list(survivors):
        if random.random() < get_fighter_risk(fighter):
            raid_casualties.append(fighter)
            survivors = [item for item in survivors if item.get("id") != fighter.get("id")]
            tier = int(fighter.get("tier", 1))
            scraps["w"] = scraps.get("w", 0) + tier
            scraps["e"] = scraps.get("e", 0) + tier
    dead_units.extend(raid_casualties)

    public_line = build_public_raid_line(attacker_name, defender_name, success, live_target, trap_triggered)
    dm_log = build_raid_dm_log(units, trap_item, success, live_target, loot, captives_taken)
    return {
        "success": success,
        "public_line": public_line,
        "dm_log": dm_log,
        "trap_triggered": trap_triggered,
        "trap_slot": trap_slot,
        "trap_item": trap_item,
        "survivors": survivors,
        "dead_units": dead_units,
        "loot": loot,
        "scraps": scraps,
        "captives_taken": captives_taken,
        "defender_captives": defender_captives,
        "live_target": live_target,
    }


async def send_dm_report(user_id: int, text: str):
    try:
        await bot.send_message(user_id, text, parse_mode="Markdown", reply_markup=build_close_markup())
    except Exception:
        pass


async def process_random_fort_events():
    cursor = db.alchemy_players.find({"buildings.fort": {"$gt": 0}})
    async for defender in cursor:
        if random.random() > 0.03:
            continue
        fake_attacker = {
            "user_id": 0,
            "user_name": random.choice(["Банда ретортников", "Отряд болотников", "Гоблинский обоз"]),
            "raid_squad": {
                "fighter_snapshots": [build_fighter_snapshot(create_fighter(unit_id)) for unit_id in random.sample(list(BATTLE_UNITS.keys()), k=random.randint(3, 5))],
                "energy_invested": random.randint(3, 8),
            },
        }
        report = await resolve_raid(fake_attacker, defender, fake_attacker["user_name"], defender.get("user_name", "базы"), False)
        defense = defender.get("fort_defense", {})
        counters = defender.get("action_counters", DEFAULT_ACTION_COUNTERS)
        if report["trap_triggered"] and report["trap_slot"]:
            defense[report["trap_slot"]] = None
            counters = increment_action_counter(defender, "defenses_triggered")
        if report["success"]:
            inventory = defender.get("inventory", {})
            for item_id, amount in choose_loot_from_inventory(inventory, count=2).items():
                if inventory.get(item_id, 0) >= amount:
                    inventory[item_id] -= amount
                    if inventory[item_id] <= 0:
                        del inventory[item_id]
            counters = increment_action_counter(defender, "robberies_suffered")
            await db.alchemy_players.update_one(
                {"user_id": defender["user_id"]},
                {"$set": {"inventory": inventory, "action_counters": counters, "captives": report["defender_captives"], "fort_defense": defense}},
            )
        elif report["captives_taken"]:
            counters = increment_action_counter(defender, "captives_taken", len(report["captives_taken"]))
            await db.alchemy_players.update_one(
                {"user_id": defender["user_id"]},
                {"$set": {"captives": report["defender_captives"], "action_counters": counters, "fort_defense": defense}},
            )
        elif report["trap_triggered"] and report["trap_slot"]:
            await db.alchemy_players.update_one(
                {"user_id": defender["user_id"]},
                {"$set": {"fort_defense": defense, "action_counters": counters}},
            )
        await send_dm_report(
            defender["user_id"],
            f"🛡 **НАЛЁТ НА ФОРТ**\n{report['dm_log']}\n\nИтог: {'Прорвались' if report['success'] else 'Форт отбился'}.",
        )


@router.callback_query(F.data == "close_pm_report")
async def close_pm_report(callback: types.CallbackQuery):
    await callback.message.delete()
    await callback.answer()


@router.callback_query(F.data == "raids_open")
async def raids_open(callback: types.CallbackQuery):
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    draft = cleanup_raid_draft(player, callback.from_user.id)
    await callback.message.edit_text(
        render_raids_menu(player, draft),
        reply_markup=build_raids_keyboard(player, draft),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data == "raid_nope")
async def raid_nope(callback: types.CallbackQuery):
    await callback.answer("Нужно собрать 3-5 бойцов и иметь энергию.", show_alert=True)


@router.callback_query(F.data == "raid_clear")
async def raid_clear(callback: types.CallbackQuery):
    raid_drafts[callback.from_user.id] = []
    raid_pages[callback.from_user.id] = 0
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    await callback.message.edit_text(
        render_raids_menu(player, []),
        reply_markup=build_raids_keyboard(player, []),
        parse_mode="Markdown",
    )
    await callback.answer("Черновик очищен.")


@router.callback_query(F.data.startswith("raid_pick:"))
async def raid_pick(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    draft = cleanup_raid_draft(player, user_id).copy()
    fighter_id = callback.data.split(":")[1]
    limit = get_expedition_team_limit(player.get("buildings", {}).get("guild", 0))
    if fighter_id in draft:
        draft.remove(fighter_id)
    else:
        if len(draft) >= limit:
            return await callback.answer("Гильдия не пропускает такой размер отряда.", show_alert=True)
        fighter = find_fighter(player, fighter_id)
        if not fighter or fighter.get("status") != "available":
            raid_drafts[user_id] = cleanup_raid_draft(player, user_id)
            await callback.message.edit_text(
                render_raids_menu(player, raid_drafts[user_id]),
                reply_markup=build_raids_keyboard(player, raid_drafts[user_id]),
                parse_mode="Markdown",
            )
            return await callback.answer("Список бойцов обновлён. Попробуй выбрать заново.", show_alert=True)
        draft.append(fighter_id)
    raid_drafts[user_id] = draft
    await callback.message.edit_text(
        render_raids_menu(player, draft),
        reply_markup=build_raids_keyboard(player, draft),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data == "raid_prepare")
async def raid_prepare(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    inventory = player.get("inventory", {})
    draft = cleanup_raid_draft(player, user_id).copy()
    if not 3 <= len(draft) <= get_expedition_team_limit(player.get("buildings", {}).get("guild", 0)):
        return await callback.answer("Нужно 3-5 бойцов.", show_alert=True)
    if player.get("raid_squad", {}).get("fighter_ids"):
        return await callback.answer("Сначала расформируйте текущий рейд-отряд.", show_alert=True)
    draft_fighters = [find_fighter(player, fighter_id) for fighter_id in draft]
    if any(not fighter or fighter.get("status") != "available" for fighter in draft_fighters):
        return await callback.answer("Бойцы рассинхронизировались с составом.", show_alert=True)

    energy_cost = max(3, len(draft) * 2)
    if inventory.get("energy", 0) < energy_cost:
        return await callback.answer("Не хватает энергии на подготовку.", show_alert=True)

    inventory["energy"] -= energy_cost
    if inventory.get("energy", 0) <= 0:
        inventory.pop("energy", None)

    for fighter in draft_fighters:
        fighter["status"] = "raid"
    squad = {
        "fighter_ids": draft,
        "fighter_snapshots": [build_fighter_snapshot(fighter) for fighter in draft_fighters],
        "energy_invested": energy_cost,
        "created_at": int(time.time()),
    }
    counters = increment_action_counter(player, "raids_prepared")
    raid_drafts[user_id] = []
    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {"inventory": inventory, "fighters": player.get("fighters", []), "raid_squad": squad, "action_counters": counters}},
    )
    player["inventory"] = inventory
    player["raid_squad"] = squad
    await callback.message.edit_text(
        render_raids_menu(player, []),
        reply_markup=build_raids_keyboard(player, []),
        parse_mode="Markdown",
    )
    await callback.answer("Рейд-отряд готов.")


@router.callback_query(F.data == "raid_disband")
async def raid_disband(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    squad = player.get("raid_squad", {"fighter_ids": [], "energy_invested": 0})
    if not squad.get("fighter_ids"):
        return await callback.answer("Нечего расформировывать.", show_alert=True)
    inventory = player.get("inventory", {})
    for fighter_id in squad.get("fighter_ids", []):
        fighter = find_fighter(player, fighter_id)
        if fighter:
            fighter["status"] = "available"
    inventory["energy"] = inventory.get("energy", 0) + squad.get("energy_invested", 0)
    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {"inventory": inventory, "fighters": player.get("fighters", []), "raid_squad": {"fighter_ids": [], "fighter_snapshots": [], "energy_invested": 0, "created_at": 0}}},
    )
    player["inventory"] = inventory
    player["raid_squad"] = {"fighter_ids": [], "fighter_snapshots": [], "energy_invested": 0, "created_at": 0}
    await callback.message.edit_text(
        render_raids_menu(player, []),
        reply_markup=build_raids_keyboard(player, []),
        parse_mode="Markdown",
    )
    await callback.answer("Отряд распущен.")


@router.callback_query(F.data == "fort_open")
async def fort_open(callback: types.CallbackQuery):
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    page = fort_captive_pages.get(callback.from_user.id, 0)
    await callback.message.edit_text(
        render_fort_menu(player, page),
        reply_markup=build_fort_keyboard(player, page),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("raid_page:"))
async def raid_page(callback: types.CallbackQuery):
    raid_pages[callback.from_user.id] = int(callback.data.split(":")[1])
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    draft = cleanup_raid_draft(player, callback.from_user.id)
    await callback.message.edit_text(
        render_raids_menu(player, draft),
        reply_markup=build_raids_keyboard(player, draft),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("raid_set_grid:"))
async def raid_set_grid(callback: types.CallbackQuery):
    new_grid = int(callback.data.split(":")[1])
    if new_grid not in {1, 2, 3, 4}:
        new_grid = 3
    raid_pages[callback.from_user.id] = 0
    await db.alchemy_players.update_one({"user_id": callback.from_user.id}, {"$set": {"inv_grid": new_grid}})
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    player["inv_grid"] = new_grid
    draft = cleanup_raid_draft(player, callback.from_user.id)
    await callback.message.edit_text(
        render_raids_menu(player, draft),
        reply_markup=build_raids_keyboard(player, draft),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("fort_captives_page:"))
async def fort_captives_page(callback: types.CallbackQuery):
    page = int(callback.data.split(":")[1])
    fort_captive_pages[callback.from_user.id] = page
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    await callback.message.edit_text(
        render_fort_menu(player, page),
        reply_markup=build_fort_keyboard(player, page),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data == "fort_page_info")
async def fort_dummy_btn(callback: types.CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.startswith("fort_slot:"))
async def fort_slot(callback: types.CallbackQuery):
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    slot_id = callback.data.split(":")[1]
    fort_item_pages[(callback.from_user.id, slot_id)] = 0
    grid_size = player.get("inv_grid", 3)
    inventory = player.get("inventory", {})
    items = [(item_id, amount) for item_id, amount in inventory.items() if amount > 0 and item_id not in BATTLE_UNITS]
    total_pages = max(1, (len(items) + (grid_size * FORT_SLOT_ROWS_PER_PAGE) - 1) // (grid_size * FORT_SLOT_ROWS_PER_PAGE))
    await callback.message.edit_text(
        f"🧱 **{TRAP_LABELS[slot_id]}**\nВыбери любой предмет для зарядки ловушки.\nСтраница: 1/{total_pages} | Сетка: {grid_size} в ряд.",
        reply_markup=build_fort_slot_keyboard(player, slot_id, 0),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("fort_page:"))
async def fort_page(callback: types.CallbackQuery):
    _, slot_id, page = callback.data.split(":")
    current_page = int(page)
    fort_item_pages[(callback.from_user.id, slot_id)] = current_page
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    grid_size = player.get("inv_grid", 3)
    inventory = player.get("inventory", {})
    items = [(item_id, amount) for item_id, amount in inventory.items() if amount > 0 and item_id not in BATTLE_UNITS]
    total_pages = max(1, (len(items) + (grid_size * FORT_SLOT_ROWS_PER_PAGE) - 1) // (grid_size * FORT_SLOT_ROWS_PER_PAGE))
    await callback.message.edit_text(
        f"🧱 **{TRAP_LABELS[slot_id]}**\nВыбери любой предмет для зарядки ловушки.\nСтраница: {current_page + 1}/{total_pages} | Сетка: {grid_size} в ряд.",
        reply_markup=build_fort_slot_keyboard(player, slot_id, current_page),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("fort_set_grid:"))
async def fort_set_grid(callback: types.CallbackQuery):
    _, slot_id, new_grid = callback.data.split(":")
    new_grid_size = int(new_grid)
    user_id = callback.from_user.id
    fort_item_pages[(user_id, slot_id)] = 0
    await db.alchemy_players.update_one({"user_id": user_id}, {"$set": {"inv_grid": new_grid_size}})
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    inventory = player.get("inventory", {})
    items = [(item_id, amount) for item_id, amount in inventory.items() if amount > 0 and item_id not in BATTLE_UNITS]
    total_pages = max(1, (len(items) + (new_grid_size * FORT_SLOT_ROWS_PER_PAGE) - 1) // (new_grid_size * FORT_SLOT_ROWS_PER_PAGE))
    await callback.message.edit_text(
        f"🧱 **{TRAP_LABELS[slot_id]}**\nВыбери любой предмет для зарядки ловушки.\nСтраница: 1/{total_pages} | Сетка: {new_grid_size} в ряд.",
        reply_markup=build_fort_slot_keyboard(player, slot_id, 0),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("fort_load:"))
async def fort_load(callback: types.CallbackQuery):
    _, slot_id, item_id = callback.data.split(":")
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    inventory = player.get("inventory", {})
    defense = player.get("fort_defense", {})
    if inventory.get(item_id, 0) <= 0:
        return await callback.answer("Предмет уже закончился.", show_alert=True)
    old_item = defense.get(slot_id)
    if old_item:
        inventory[old_item] = inventory.get(old_item, 0) + 1
    inventory[item_id] -= 1
    if inventory[item_id] <= 0:
        del inventory[item_id]
    defense[slot_id] = item_id
    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {"inventory": inventory, "fort_defense": defense}},
    )
    player["inventory"] = inventory
    player["fort_defense"] = defense
    page = fort_captive_pages.get(user_id, 0)
    await callback.message.edit_text(
        render_fort_menu(player, page),
        reply_markup=build_fort_keyboard(player, page),
        parse_mode="Markdown",
    )
    await callback.answer("Ловушка заряжена.")


@router.callback_query(F.data.startswith("fort_unload:"))
async def fort_unload(callback: types.CallbackQuery):
    slot_id = callback.data.split(":")[1]
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    defense = player.get("fort_defense", {})
    item_id = defense.get(slot_id)
    if not item_id:
        return await callback.answer("Слот и так пуст.", show_alert=True)
    inventory = player.get("inventory", {})
    inventory[item_id] = inventory.get(item_id, 0) + 1
    defense[slot_id] = None
    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {"inventory": inventory, "fort_defense": defense}},
    )
    player["inventory"] = inventory
    player["fort_defense"] = defense
    page = fort_captive_pages.get(user_id, 0)
    await callback.message.edit_text(
        render_fort_menu(player, page),
        reply_markup=build_fort_keyboard(player, page),
        parse_mode="Markdown",
    )
    await callback.answer("Предмет снят с ловушки.")


@router.callback_query(F.data.startswith("captive_view:"))
async def captive_view(callback: types.CallbackQuery):
    captive_id = callback.data.split(":")[1]
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    captive = next((item for item in player.get("captives", []) if item.get("id") == captive_id), None)
    if not captive:
        return await callback.answer("Пленный сбежал раньше вас.", show_alert=True)
    builder = InlineKeyboardBuilder()
    builder.button(text="⚡ Вербовка", callback_data=f"captive_recruit:{captive_id}")
    builder.button(text="⚗️ Разобрать", callback_data=f"captive_recycle:{captive_id}")
    builder.button(text="🔙 В Форт", callback_data="fort_open")
    builder.adjust(1)
    text = f"🪤 **ПЛЕННЫЙ**\n{get_role_name(captive.get('unit_id', 'ghost'))}\nТир: {captive.get('tier', 1)}"
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("captive_recruit:"))
async def captive_recruit(callback: types.CallbackQuery):
    captive_id = callback.data.split(":")[1]
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    captives = player.get("captives", [])
    captive = next((item for item in captives if item.get("id") == captive_id), None)
    if not captive:
        return await callback.answer("Пленный пропал.", show_alert=True)
    inventory = player.get("inventory", {})
    cost = captive.get("tier", 1) * 2
    if inventory.get("energy", 0) < cost:
        return await callback.answer("Не хватает энергии на вербовку.", show_alert=True)
    inventory["energy"] -= cost
    if inventory.get("energy", 0) <= 0:
        inventory.pop("energy", None)
    captives = [item for item in captives if item.get("id") != captive_id]
    counters = player.get("action_counters", DEFAULT_ACTION_COUNTERS)
    recruited_success = random.random() < 0.5
    if recruited_success:
        fighters = player.get("fighters", [])
        recruited = create_fighter(captive["unit_id"], tier=captive.get("tier", 1), crafted_tier3=captive.get("crafted_tier3", False))
        recruited["custom_name"] = captive.get("custom_name", "")[:24]
        recruited["raids"] = captive.get("raids", 0)
        recruited["expeditions"] = captive.get("expeditions", 0)
        fighters.append(recruited)
        counters = increment_action_counter(player, "captives_recruited")
        text = "Пленный сломался, согласился служить и получил котелок по размеру."
    else:
        text = "Пленный дождался, пока все моргнут, и сбежал в ближайший туман."
    update_fields = {"inventory": inventory, "captives": captives, "action_counters": counters}
    if recruited_success:
        update_fields["fighters"] = fighters
    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": update_fields},
    )
    await callback.message.edit_text(text, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("captive_recycle:"))
async def captive_recycle(callback: types.CallbackQuery):
    captive_id = callback.data.split(":")[1]
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    captives = player.get("captives", [])
    captive = next((item for item in captives if item.get("id") == captive_id), None)
    if not captive:
        return await callback.answer("Пленный уже разобран.", show_alert=True)
    inventory = player.get("inventory", {})
    captives = [item for item in captives if item.get("id") != captive_id]
    tier = captive.get("tier", 1)
    for item_id in ["w", "f", "a", "e"]:
        inventory[item_id] = inventory.get(item_id, 0) + tier
    if random.random() < 0.10:
        inventory["energy"] = inventory.get("energy", 0) + 1
    counters = increment_action_counter(player, "captives_recycled")
    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {"inventory": inventory, "captives": captives, "action_counters": counters}},
    )
    await callback.message.edit_text("Реторта довольно булькнула, а склад пополнился базовыми элементами.", parse_mode="Markdown")
    await callback.answer()


@router.message(Command("грабить"))
async def cmd_raid(message: types.Message):
    attacker = await get_or_create_player(message.from_user.id, message.from_user.first_name)
    squad = attacker.get("raid_squad", {"fighter_ids": [], "energy_invested": 0})
    if not squad.get("fighter_ids") or squad.get("energy_invested", 0) <= 0:
        return await message.reply("☠️ Рейд невозможен: сначала собери и заряди отряд в Казармах.")

    target_user = message.reply_to_message.from_user if message.reply_to_message else None
    is_pve = not target_user or target_user.is_bot
    if is_pve:
        object_name = random.choice(["лагерь бутлегеров", "пещера гоблинов", "башня шарлатанов"])
        fort_level = random.randint(1, 3)
        offer = (
            f"☠️ Грабить некого. Разведка нашла {object_name} с фортом ур. {fort_level}. "
            "Возможный лут: энергия, духи, базовые ресурсы, шанс на рецепт. Нападаем?"
        )
        return await message.reply(offer, reply_markup=build_pve_offer_markup())

    defender = await get_or_create_player(target_user.id, target_user.first_name)
    if defender["user_id"] == attacker["user_id"]:
        return await message.reply("☠️ Самоограбление бухгалтерия пока не согласовала.")

    now = int(time.time())
    if int(defender.get("raid_protection_until", 0)) > now:
        left = int(defender["raid_protection_until"] - now)
        return await message.reply(
            f"🛡 Цель недавно отбилась от налёта. Повторный штурм доступен через {left // 3600} ч. {(left % 3600) // 60} мин."
        )

    last_same_target = get_recent_raid_ts(attacker, defender["user_id"])
    if last_same_target and now - last_same_target < RAID_REPEAT_COOLDOWN:
        left = RAID_REPEAT_COOLDOWN - (now - last_same_target)
        return await message.reply(
            f"⏳ Этот форт уже трясли недавно. Новая попытка по той же цели через {left // 3600} ч. {(left % 3600) // 60} мин."
        )

    defender_name = defender.get("user_name", target_user.first_name)

    await execute_raid(message, attacker, defender, defender_name, False)


@router.callback_query(F.data == "raid_pve_no")
async def raid_pve_no(callback: types.CallbackQuery):
    await callback.message.edit_text("Рейд-отряд передумал и пошёл обратно полировать сапоги.")
    await callback.answer()


@router.callback_query(F.data == "raid_pve_yes")
async def raid_pve_yes(callback: types.CallbackQuery):
    attacker = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    if not attacker.get("raid_squad", {}).get("fighter_ids"):
        return await callback.answer("Рейд-отряд уже потрачен или распущен.", show_alert=True)
    await execute_raid(callback.message, attacker, None, random.choice(["лагерь бутлегеров", "пещера гоблинов", "башня шарлатанов"]), True)
    await callback.answer()


async def execute_raid(message: types.Message, attacker: dict, defender: dict | None, defender_name: str, is_pve: bool):
    attacker_name = attacker.get("user_name", "Агрессор")

    squad = attacker.get("raid_squad", {"fighter_ids": [], "fighter_snapshots": [], "energy_invested": 0})
    now = int(time.time())
    for fighter_id in squad.get("fighter_ids", []):
        fighter = find_fighter(attacker, fighter_id)
        if fighter:
            consume_fighter_stamina(fighter, now)
            grant_mission_progress(fighter, "raid", now)
    report = await resolve_raid(attacker, defender, attacker_name, defender_name, is_pve)
    attacker_inventory = attacker.get("inventory", {})
    survivor_ids = {fighter.get("id") for fighter in report["survivors"]}
    dead_ids = {fighter.get("id") for fighter in report.get("dead_units", [])}
    captive_ids = {fighter.get("id") for fighter in report.get("captives_taken", [])}
    hall = attacker.get("hall_of_fame", [])
    fighters = []
    for fighter in get_player_fighters(attacker):
        if fighter.get("id") in dead_ids:
            if fighter.get("custom_name"):
                hall.append(hall_of_fame_entry(fighter, "погиб в рейде", now))
            continue
        if fighter.get("id") in captive_ids:
            continue
        if fighter.get("id") in survivor_ids:
            fighter["status"] = "available"
        fighters.append(fighter)
    for item_id, amount in report["loot"].items():
        attacker_inventory[item_id] = attacker_inventory.get(item_id, 0) + amount
    for item_id, amount in report["scraps"].items():
        attacker_inventory[item_id] = attacker_inventory.get(item_id, 0) + amount

    attacker["raid_squad"] = {"fighter_ids": [], "fighter_snapshots": [], "energy_invested": 0, "created_at": 0}
    counters = increment_action_counter(attacker, "raids_started")
    counters = increment_action_counter(attacker, "raids_won" if report["success"] else "raids_lost")

    await db.alchemy_players.update_one(
        {"user_id": attacker["user_id"]},
        {"$set": {"inventory": attacker_inventory, "fighters": fighters, "hall_of_fame": hall, "raid_squad": attacker["raid_squad"], "action_counters": counters}},
    )

    if defender:
        defender_inventory = defender.get("inventory", {})
        counters_def = defender.get("action_counters", DEFAULT_ACTION_COUNTERS)
        attacker_update = {}
        defender_update = {}
        if report["success"]:
            for item_id, amount in report["loot"].items():
                if defender_inventory.get(item_id, 0) >= amount:
                    defender_inventory[item_id] -= amount
                    if defender_inventory[item_id] <= 0:
                        del defender_inventory[item_id]
            counters_def = increment_action_counter(defender, "robberies_suffered")
            if report["live_target"]:
                defender["raid_protection_until"] = int(time.time()) + RAID_PROTECTION_ON_SUCCESS
                defender_update["raid_protection_until"] = defender["raid_protection_until"]
        attacker_update["raid_history"] = update_raid_history(attacker, defender["user_id"])
        if report["trap_triggered"] and report["trap_slot"] and report["trap_item"]:
            defender.get("fort_defense", {})[report["trap_slot"]] = None
            counters_def = increment_action_counter(defender, "defenses_triggered")
            if report["captives_taken"]:
                counters_def = increment_action_counter(defender, "captives_taken", len(report["captives_taken"]))
            defender_update["fort_defense"] = defender.get("fort_defense", {})
        defender_update["inventory"] = defender_inventory
        defender_update["captives"] = report["defender_captives"]
        defender_update["action_counters"] = counters_def
        await db.alchemy_players.update_one({"user_id": defender["user_id"]}, {"$set": defender_update})
        await db.alchemy_players.update_one({"user_id": attacker["user_id"]}, {"$set": attacker_update})

    public_message = await message.reply(report["public_line"])
    delete_after = 60
    asyncio.create_task(delete_after_delay(public_message, delete_after))
    asyncio.create_task(delete_after_delay(message, delete_after))

    loot_line = ", ".join(f"{ELEMENTS.get(item_id, item_id)} x{amount}" for item_id, amount in report["loot"].items()) or "ничего"
    attacker_dm = (
        f"☠️ **ОТЧЁТ О РЕЙДЕ**\n{report['dm_log']}\n\n"
        f"Итог: {'Победа' if report['success'] else 'Поражение'}\n"
        f"Добыча/ошмётки: {loot_line}\n"
        f"Выжили: {', '.join(get_role_name(unit_id) for unit_id in report['survivors']) or 'никто'}\n"
        f"Погибли: {', '.join(get_role_name(unit_id) for unit_id in report.get('dead_units', [])) or 'нет'}\n"
        f"Пленные: {', '.join(get_role_name(unit_id) for unit_id in report['captives_taken']) or 'нет'}"
    )
    await send_dm_report(attacker["user_id"], attacker_dm)

    if defender:
        defender_dm = (
            f"🛡 **АТАКА НА ФОРТ**\n{report['dm_log']}\n\n"
            f"Итог: {'Нападавший прорвался' if report['success'] else 'Форт устоял'}\n"
            f"Похищено: {loot_line}\n"
            f"Пленные в подвале: {', '.join(get_role_name(unit_id) for unit_id in report['captives_taken']) or 'нет'}"
        )
        await send_dm_report(defender["user_id"], defender_dm)
