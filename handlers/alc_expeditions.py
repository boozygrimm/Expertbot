import random
import time
import uuid

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ai_logic import generate_with_retry
from bot_instance import bot
from database import db
from handlers.alc_core import (
    DEFAULT_ACTION_COUNTERS,
    STORAGE_CAPACITY,
    get_or_create_player,
    get_used_slots,
    increment_action_counter,
)
from handlers.alc_fighters import (
    build_fighter_snapshot,
    consume_fighter_stamina,
    find_fighter,
    get_available_fighters,
    get_fighter_stamina_cap,
    get_fighter_title,
    get_player_fighters,
    grant_mission_progress,
)
from handlers.alc_heritage import try_grant_random_lore_fragment
from handlers.alc_recipes import BATTLE_UNITS, ELEMENTS

router = Router()

MAX_LOGS = 7
VISIBLE_LOGS = 5
EXPEDITION_TICK = 300
BASE_EXPEDITION_DURATION = 3600
RETURN_MIN_TIME = 300
ROLE_ALIASES = {
    "fire_swordsman": "Огнемеч",
    "water_guard": "Водостраж",
    "earth_shielder": "Щитоносец",
    "wind_scout": "Ветрослед",
    "ash_pikeman": "Пеплокопь",
    "wave_spearman": "Волникопь",
    "stone_axeman": "Камнетёс",
    "storm_archer": "Бурелук",
    "rider_f": "Пламерейд",
    "rider_w": "Приливрейд",
    "rider_e": "Скаларейд",
    "rider_a": "Вихререйд",
    "saboteur": "Сапёр",
    "medic": "Медик",
    "engineer": "Инженер",
    "sniper": "Снайпер",
    "mech": "Мех",
    "flame_tank": "Огнетанк",
    "tesla_tank": "Теслатанк",
    "hover": "Ховер",
    "drone": "Дрон",
    "artillery": "Арта",
    "walker": "Ходун",
    "juggernaut": "Джаггер",
}
TIER_HP = {1: 45, 2: 72, 3: 110}
TIER_CARGO = {1: 10, 2: 15, 3: 22}
FALLBACK_ACTIONS = [
    "убегал от",
    "споткнулся о",
    "героически победил",
    "случайно поджёг",
    "переубедил силой",
    "обманул",
    "обменял банку мази на",
    "вытащил из болота",
    "запер в бочке",
    "напугал до линьки",
    "усыпил песней про реторту",
    "переварил в котле",
    "потроллил",
    "отрезвил кирпичом",
    "обогнал на ходулях",
    "накрыл котелком",
]
FALLBACK_ENEMIES = [
    "кислотного слизня",
    "живой философский камень",
    "дикого гомункула",
    "пьяную мантикору",
    "грибного голема",
    "ядовитый гербарий",
    "подвального алхимика",
    "обиженный сундук-мимик",
    "стеклянного василиска",
    "карманного некроманта",
    "плотоядную тыкву",
    "раздражённый перегонный куб",
    "болотного аптекаря",
    "лишайникового шамана",
    "прыгающий котёл",
    "саблезубый одуванчик",
]
LOOT_TABLE = [
    ("w", 7), ("f", 7), ("e", 7), ("a", 7),
    ("stone", 6), ("wood", 6), ("sand", 5), ("moss", 4),
    ("glass", 4), ("brick", 4), ("metal", 4), ("energy", 4),
    ("life", 3), ("ghost", 2), ("electricity", 2),
    ("recipe_scroll", 2), ("pack_s", 3), ("pack_m", 2), ("pack_l", 1),
    ("philosophers_stone", 1),
]
MEETING_SPECIALS = ["alchemist", "wanderer", "hero", "scientist"]
MEETING_PACKS = ["pack_s", "pack_s", "pack_m", "pack_m", "pack_l"]
expedition_drafts: dict[int, list[str]] = {}


def get_role_name(fighter_or_unit) -> str:
    if isinstance(fighter_or_unit, dict):
        custom_name = fighter_or_unit.get("custom_name") or fighter_or_unit.get("name")
        if custom_name:
            return custom_name
        unit_id = fighter_or_unit.get("unit_id", "")
        return ROLE_ALIASES.get(unit_id, ELEMENTS.get(unit_id, unit_id))
    unit_id = str(fighter_or_unit)
    return ROLE_ALIASES.get(unit_id, ELEMENTS.get(unit_id, unit_id))


def get_expedition_slot_limit(barracks_level: int) -> int:
    if barracks_level >= 5:
        return 3
    if barracks_level >= 3:
        return 2
    return 1


def get_expedition_team_limit(guild_level: int) -> int:
    if guild_level >= 3:
        return 5
    if guild_level >= 2:
        return 4
    return 3


def get_unit_id(subject) -> str:
    if isinstance(subject, dict):
        return subject.get("unit_id", "")
    return str(subject)


def get_unit_tier(subject) -> int:
    if isinstance(subject, dict):
        return int(subject.get("tier", BATTLE_UNITS.get(subject.get("unit_id", ""), {}).get("tier", 1)) or 1)
    return int(BATTLE_UNITS[str(subject)]["tier"])


def calculate_unit_hp(subject, barracks_level: int) -> int:
    tier = get_unit_tier(subject)
    return TIER_HP[tier] + barracks_level * 6


def calculate_unit_cargo(subject) -> int:
    return TIER_CARGO[get_unit_tier(subject)]


def build_team_stats(units: list, barracks_level: int) -> dict:
    unit_ids = [get_unit_id(unit) for unit in units]
    total_hp = sum(calculate_unit_hp(unit, barracks_level) for unit in units)
    total_cargo = sum(calculate_unit_cargo(unit) for unit in units)
    has_medic = "medic" in unit_ids
    defense_bonus = sum(1 for unit_id in unit_ids if unit_id in {"earth_shielder", "water_guard", "mech", "juggernaut"})
    loot_bonus = sum(1 for unit_id in unit_ids if unit_id in {"wind_scout", "storm_archer", "saboteur", "engineer", "sniper", "drone", "hover"})
    tier_pressure = sum(get_unit_tier(unit) for unit in units)
    return {
        "total_hp": total_hp,
        "cargo_capacity": total_cargo,
        "has_medic": has_medic,
        "defense_bonus": defense_bonus,
        "loot_bonus": loot_bonus,
        "tier_pressure": tier_pressure,
    }


def trim_logs(logs: list[str]) -> list[str]:
    return logs[-MAX_LOGS:]


def choose_weighted_loot() -> str:
    entries = [(item_id, weight) for item_id, weight in LOOT_TABLE]
    total = sum(weight for _, weight in entries)
    roll = random.randint(1, total)
    passed = 0
    for item_id, weight in entries:
        passed += weight
        if roll <= passed:
            return item_id
    return "stone"


def build_fallback_text(units: list) -> str:
    actor = get_role_name(random.choice(units))
    action = random.choice(FALLBACK_ACTIONS)
    enemy = random.choice(FALLBACK_ENEMIES)
    return f"{actor} {action} {enemy}, а потом сделал вид, что так и надо."


def build_planned_event_text(units: list, event_type: str) -> str:
    actor = get_role_name(random.choice(units))
    wingman = get_role_name(random.choice(units))
    enemy = random.choice(FALLBACK_ENEMIES)

    if event_type == "loot":
        return f"{actor} вытряхнул добычу из {enemy}, а {wingman} сразу объявил это плановой находкой."
    if event_type == "damage":
        return f"{actor} полез вперёд и нарвался на {enemy}, пока {wingman} вытаскивал отряд из месива."
    if event_type == "heal":
        return f"{actor} перевёл дух, а {wingman} быстро залатал строй после стычки с {enemy}."
    return f"{actor} договорился с гостем у костра, пока {wingman} следил, чтобы сделка не закончилась поножовщиной."


async def generate_event_line(units: list, hp_now: int, hp_total: int) -> str:
    roles = ", ".join(get_role_name(unit_id) for unit_id in units)
    prompt = (
        "Мир фэнтезийной алхимии, големов, мутировавших растений, зелий и мошенников. "
        f"Отряд: {roles}. Текущее HP: {hp_now}/{hp_total}. "
        "Напиши ровно одно короткое предложение до 150 символов, ироничное и смешное, "
        "используй роли бойцов прямо в тексте."
    )
    text = await generate_with_retry(prompt, is_game=True, use_fast_model=True)
    if not text:
        return build_fallback_text(units)
    return text.strip().replace("\n", " ")[:150]


def format_loot_summary(loot: dict) -> str:
    if not loot:
        return "ничего"
    ordered = sorted(loot.items(), key=lambda item: (item[0] not in {"energy", "recipe_scroll", "pack_l", "pack_m", "pack_s"}, ELEMENTS.get(item[0], item[0])))
    return ", ".join(f"{ELEMENTS.get(item_id, item_id)} x{amount}" for item_id, amount in ordered)


async def generate_expedition_final_report(expedition: dict) -> str:
    fighters = ", ".join(get_role_name(item) for item in expedition.get("fighter_snapshots", expedition.get("fighters", [])))
    loot_text = format_loot_summary(expedition.get("loot", {}))
    reason = expedition.get("return_reason", "маршрут завершён")
    hp_text = f"{expedition.get('current_hp', 0)}/{expedition.get('total_hp', 0)}"
    prompt = (
        "Мир фэнтезийной алхимии, рейдов, монстров и мутантов. "
        f"Отряд: {fighters}. Итоговое HP: {hp_text}. "
        f"Причина завершения похода: {reason}. "
        f"Добыча: {loot_text}. "
        "Напиши короткий итоговый полевой отчет в 2 предложения, с иронией, но ясно перечисли результат похода."
    )
    text = await generate_with_retry(prompt, is_game=True, use_fast_model=True)
    if text:
        return text.strip().replace("\n", " ")[:350]

    if expedition.get("loot"):
        return (
            f"Отряд вернулся после маршрута '{reason}' и выгрузил {loot_text}. "
            f"Строй уцелел на {hp_text}, так что вылазка окупилась."
        )
    return (
        f"Отряд вернулся после маршрута '{reason}', но мешки оказались почти пустыми. "
        f"Живыми дошли, и на том бухгалтерия велела считать поход полезным."
    )


async def build_planned_events(units: list, stats: dict) -> list[dict]:
    hp_now = stats["total_hp"]
    planned = []
    planned_count = random.randint(9, 13)
    for _ in range(planned_count):
        roll = random.random()
        if roll < 0.58:
            loot_item = choose_weighted_loot()
            if loot_item in {"recipe_scroll", "pack_s", "pack_m", "pack_l", "philosophers_stone"}:
                loot_amount = 1
            elif loot_item in {"energy", "life", "ghost", "electricity"}:
                loot_amount = random.randint(1, 2 + min(2, stats["loot_bonus"] // 2))
            else:
                loot_amount = random.randint(2, 4 + min(3, stats["loot_bonus"]))
            event = {"type": "loot", "loot": {loot_item: loot_amount}}
            if random.random() < min(0.35, 0.08 * stats["loot_bonus"]):
                bonus_item = choose_weighted_loot()
                if bonus_item != loot_item:
                    bonus_amount = 1 if bonus_item in {"recipe_scroll", "pack_s", "pack_m", "pack_l", "philosophers_stone"} else random.randint(1, 3)
                    event["loot"][bonus_item] = bonus_amount
        elif roll < 0.78:
            damage = random.randint(6, 16) + random.randint(0, 8)
            damage = max(4, damage - stats["defense_bonus"] * 4)
            event = {"type": "damage", "damage": damage}
            hp_now = max(0, hp_now - damage)
        elif roll < 0.93:
            heal = random.randint(6, 14) + (8 if stats["has_medic"] else 0)
            event = {"type": "heal", "heal": heal}
            hp_now = min(stats["total_hp"], hp_now + heal)
        else:
            event = {"type": "meeting"}
            if random.random() < 0.45:
                event["reward_recipe"] = True
            else:
                event["reward_pack"] = random.choice(MEETING_PACKS)
            event["visitor"] = random.choice(MEETING_SPECIALS)
        if random.random() < 0.35:
            event["text"] = await generate_event_line(units, hp_now, stats["total_hp"])
        else:
            event["text"] = build_planned_event_text(units, event["type"])
        planned.append(event)
    return planned


def add_loot_to_bag(expedition: dict, new_loot: dict):
    loot = expedition.get("loot", {})
    for item_id, amount in new_loot.items():
        loot[item_id] = loot.get(item_id, 0) + amount
    expedition["loot"] = loot


def get_bag_load(loot: dict) -> int:
    return sum(loot.values())


def resolve_draft_fighters(player: dict, draft: list[str], *, expedition_mode: bool = False) -> list[dict]:
    fighters = []
    all_fighters = {fighter["id"]: fighter for fighter in get_player_fighters(player)}
    for fighter_id in draft:
        fighter = all_fighters.get(fighter_id)
        if not fighter or fighter.get("status") != "available":
            continue
        if expedition_mode and int(fighter.get("stamina", 0)) <= 0:
            continue
        fighters.append(fighter)
    return fighters


def cleanup_expedition_draft(player: dict, user_id: int) -> list[str]:
    valid_ids = {fighter["id"] for fighter in get_available_fighters(player, include_zero_stamina=False)}
    cleaned = [fighter_id for fighter_id in expedition_drafts.get(user_id, []) if fighter_id in valid_ids]
    expedition_drafts[user_id] = cleaned
    return cleaned


def render_expeditions_text(player: dict, draft: list[str] | None = None) -> str:
    buildings = player.get("buildings", {})
    barracks_level = buildings.get("barracks", 0)
    guild_level = buildings.get("guild", 0)
    active = player.get("active_expeditions", [])
    slot_limit = get_expedition_slot_limit(barracks_level)
    team_limit = get_expedition_team_limit(guild_level)
    lines = [
        f"🧪 **ЭКСПЕДИЦИИ** [{len(active)}/{slot_limit}]",
        f"Лимит бойцов в отряде: {team_limit}.",
        "",
    ]
    if draft is not None:
        if draft:
            draft_fighters = resolve_draft_fighters(player, draft, expedition_mode=True)
            stats = build_team_stats(draft_fighters, barracks_level)
            lines.append("Черновик отряда:")
            lines.append("• " + ", ".join(get_role_name(fighter) for fighter in draft_fighters))
            lines.append(f"• HP: {stats['total_hp']} | Груз: {stats['cargo_capacity']}")
        else:
            lines.append("Черновик отряда пуст.")
        lines.append("")

    if not active:
        lines.append("Активных походов нет.")
    else:
        for expedition in active:
            load_now = get_bag_load(expedition.get("loot", {}))
            lines.append(
                f"• Экспедиция #{expedition['id'][:4]} | {expedition['status_label']} | "
                f"HP {expedition['current_hp']}/{expedition['total_hp']} | Рюкзак {load_now}/{expedition['cargo_capacity']}"
            )
    return "\n".join(lines)


def build_expeditions_keyboard(player: dict, draft: list[str]) -> types.InlineKeyboardMarkup:
    guild_level = player.get("buildings", {}).get("guild", 0)
    barracks_level = player.get("buildings", {}).get("barracks", 0)
    active = player.get("active_expeditions", [])
    team_limit = get_expedition_team_limit(guild_level)
    slot_limit = get_expedition_slot_limit(barracks_level)
    builder = InlineKeyboardBuilder()

    for fighter in get_available_fighters(player, include_zero_stamina=False)[:20]:
        marker = "✅" if fighter["id"] in draft else "▫️"
        builder.button(
            text=f"{marker} {get_fighter_title(fighter)} [{fighter['stamina']}/{get_fighter_stamina_cap(fighter)}]",
            callback_data=f"expedition_pick:{fighter['id']}",
        )

    can_launch = bool(draft) and len(draft) <= team_limit and len(active) < slot_limit
    builder.button(text="🚀 Отправить", callback_data="expedition_launch" if can_launch else "expedition_nope")
    builder.button(text="🧹 Очистить", callback_data="expedition_clear")
    builder.button(text="🔄 Обновить", callback_data="expeditions_open")

    for expedition in active:
        builder.button(
            text=f"📻 Экспедиция {expedition['id'][:4]}",
            callback_data=f"expedition_view:{expedition['id']}",
        )

    builder.button(text="🔙 В Казармы", callback_data="barracks_open")
    builder.adjust(1)
    return builder.as_markup()


def render_expedition_detail(expedition: dict) -> str:
    load_now = get_bag_load(expedition.get("loot", {}))
    fighters = ", ".join(get_role_name(item) for item in expedition.get("fighter_snapshots", expedition.get("fighters", [])))
    logs = expedition.get("logs", [])[-VISIBLE_LOGS:]
    status_text = expedition.get("status_label", expedition.get("status", "Неизвестно"))
    lines = [
        f"📻 **ЭКСПЕДИЦИЯ #{expedition['id'][:4]}**",
        f"Бойцы: {fighters}",
        "",
        "Перехват:",
    ]
    if logs:
        lines.extend(f"• {line}" for line in logs)
    else:
        lines.append("• Отряд ещё только распаковывает котлы.")
    lines.extend(
        [
            "",
            f"Статус: {status_text}",
            f"HP: {expedition['current_hp']}/{expedition['total_hp']}",
            f"Рюкзак: {load_now}/{expedition['cargo_capacity']}",
        ]
    )
    lines.append(f"Трофеи: {format_loot_summary(expedition.get('loot', {}))}")
    if expedition.get("status") == "returning":
        left = max(0, int(expedition.get("return_end_at", 0) - time.time()))
        lines.append(f"До прибытия: {left // 60}м {left % 60}с")
    return "\n".join(lines)


def build_expedition_detail_keyboard(expedition: dict) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if expedition.get("status") == "searching":
        builder.button(text="↩️ Отступление", callback_data=f"expedition_retreat:{expedition['id']}")
    if expedition.get("status") == "arrived":
        builder.button(text="🏁 Встретить отряд", callback_data=f"expedition_claim:{expedition['id']}")
    builder.button(text="🔙 К экспедициям", callback_data="expeditions_open")
    builder.adjust(1)
    return builder.as_markup()


async def discover_random_recipe(player: dict) -> str | None:
    known = set(player.get("discovered", [])) | set(player.get("inventory", {}).keys())
    unknown = [item_id for item_id in ELEMENTS if item_id not in known and item_id not in BATTLE_UNITS]
    if not unknown:
        return None
    recipe = random.choice(unknown)
    player["discovered"].append(recipe)
    return recipe


def finalize_return(expedition: dict, reason: str):
    if expedition.get("status") != "searching":
        return
    now = int(time.time())
    expedition["status"] = "returning"
    expedition["status_label"] = "Идёт домой"
    spent = max(1, now - expedition.get("started_at", now))
    expedition["return_end_at"] = now + max(RETURN_MIN_TIME, spent // 2)
    expedition["return_reason"] = reason
    expedition["next_tick_at"] = 0
    expedition["logs"] = trim_logs(expedition.get("logs", []) + [f"Отряд разворачивается домой: {reason}."])


async def process_single_expedition(player: dict, expedition: dict) -> bool:
    now = int(time.time())
    changed = False
    if expedition.get("status") == "searching" and expedition.get("next_tick_at", 0) <= now:
        events = expedition.get("planned_events", [])
        pointer = expedition.get("event_index", 0)
        if pointer >= len(events) or now >= expedition.get("max_end_at", now):
            finalize_return(expedition, "маршрут исчерпан")
            return True

        event = events[pointer]
        expedition["event_index"] = pointer + 1
        expedition["logs"] = trim_logs(expedition.get("logs", []) + [event["text"]])
        expedition["next_tick_at"] = now + EXPEDITION_TICK
        changed = True

        event_type = event.get("type")
        if event_type == "loot":
            add_loot_to_bag(expedition, event.get("loot", {}))
            loot_text = ", ".join(f"{ELEMENTS.get(item_id, item_id)} x{amount}" for item_id, amount in event.get("loot", {}).items())
            expedition["logs"] = trim_logs(expedition["logs"] + [f"Добыча: {loot_text}."])
        elif event_type == "damage":
            expedition["current_hp"] = max(0, expedition.get("current_hp", 0) - event.get("damage", 0))
            expedition["logs"] = trim_logs(expedition["logs"] + [f"Потеряли {event.get('damage', 0)} HP."])
        elif event_type == "heal":
            expedition["current_hp"] = min(expedition["total_hp"], expedition.get("current_hp", 0) + event.get("heal", 0))
            expedition["logs"] = trim_logs(expedition["logs"] + [f"Передышка вернула {event.get('heal', 0)} HP."])
        elif event_type == "meeting":
            if event.get("reward_recipe"):
                recipe = await discover_random_recipe(player)
                if recipe:
                    expedition["logs"] = trim_logs(expedition["logs"] + [f"Новый рецепт: {ELEMENTS.get(recipe, recipe)}."])
                else:
                    add_loot_to_bag(expedition, {"pack_s": 1})
                    expedition["logs"] = trim_logs(expedition["logs"] + ["Незнакомец сунул запасной набор вместо рецепта."])
            elif event.get("reward_pack"):
                add_loot_to_bag(expedition, {event["reward_pack"]: 1})
                expedition["logs"] = trim_logs(expedition["logs"] + [f"Подарок в дорогу: {ELEMENTS.get(event['reward_pack'], event['reward_pack'])}."])

        load_now = get_bag_load(expedition.get("loot", {}))
        if expedition.get("current_hp", 0) <= 0:
            expedition["loot"] = {}
            expedition["current_hp"] = 1
            finalize_return(expedition, "отряд едва выжил и бросил лут")
        elif expedition.get("current_hp", 0) <= max(1, int(expedition["total_hp"] * 0.10)):
            finalize_return(expedition, "здоровье упало ниже безопасного порога")
        elif load_now >= expedition.get("cargo_capacity", 0):
            finalize_return(expedition, "рюкзаки трещат по швам")

    if expedition.get("status") == "returning" and expedition.get("return_end_at", 0) <= now:
        expedition["status"] = "arrived"
        expedition["status_label"] = "Отряд вернулся"
        expedition["logs"] = trim_logs(expedition.get("logs", []) + ["Отряд добрался до базы и ждёт встречи."])
        expedition["final_report"] = await generate_expedition_final_report(expedition)
        changed = True
    return changed


async def process_expeditions_tick():
    cursor = db.alchemy_players.find({"active_expeditions.0": {"$exists": True}})
    async for player in cursor:
        expeditions = player.get("active_expeditions", [])
        changed = False
        for expedition in expeditions:
            changed = await process_single_expedition(player, expedition) or changed
        if changed:
            await db.alchemy_players.update_one(
                {"user_id": player["user_id"]},
                {"$set": {"active_expeditions": expeditions, "discovered": player.get("discovered", [])}},
            )


@router.callback_query(F.data == "expeditions_open")
async def expeditions_open(callback: types.CallbackQuery):
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    draft = cleanup_expedition_draft(player, callback.from_user.id)
    await safe_edit_text(callback.message, render_expeditions_text(player, draft), build_expeditions_keyboard(player, draft))
    await callback.answer()


@router.callback_query(F.data == "expedition_nope")
async def expedition_nope(callback: types.CallbackQuery):
    await callback.answer("Либо отряд пуст, либо лимит экспедиций уже забит.", show_alert=True)


@router.callback_query(F.data == "expedition_clear")
async def expedition_clear(callback: types.CallbackQuery):
    expedition_drafts[callback.from_user.id] = []
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    await safe_edit_text(callback.message, render_expeditions_text(player, []), build_expeditions_keyboard(player, []))
    await callback.answer("Черновик очищен.")


@router.callback_query(F.data.startswith("expedition_pick:"))
async def expedition_pick(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    guild_level = player.get("buildings", {}).get("guild", 0)
    draft = cleanup_expedition_draft(player, user_id).copy()
    fighter_id = callback.data.split(":")[1]
    team_limit = get_expedition_team_limit(guild_level)

    if fighter_id in draft:
        draft.remove(fighter_id)
    else:
        if len(draft) >= team_limit:
            return await callback.answer("Больше бойцов Гильдия пока не пустит.", show_alert=True)
        fighter = find_fighter(player, fighter_id)
        if not fighter or fighter.get("status") != "available":
            expedition_drafts[user_id] = cleanup_expedition_draft(player, user_id)
            await safe_edit_text(callback.message, render_expeditions_text(player, expedition_drafts[user_id]), build_expeditions_keyboard(player, expedition_drafts[user_id]))
            return await callback.answer("Список бойцов обновлён. Попробуй выбрать заново.", show_alert=True)
        if int(fighter.get("stamina", 0)) <= 0:
            return await callback.answer("Уставший боец в экспедицию не пойдёт.", show_alert=True)
        draft.append(fighter_id)

    expedition_drafts[user_id] = draft
    await safe_edit_text(callback.message, render_expeditions_text(player, draft), build_expeditions_keyboard(player, draft))
    await callback.answer()


@router.callback_query(F.data == "expedition_launch")
async def expedition_launch(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    draft = cleanup_expedition_draft(player, user_id).copy()
    if not draft:
        return await callback.answer("Нечего отправлять.", show_alert=True)

    barracks_level = player.get("buildings", {}).get("barracks", 0)
    slot_limit = get_expedition_slot_limit(barracks_level)
    if len(player.get("active_expeditions", [])) >= slot_limit:
        return await callback.answer("Все слоты экспедиций уже заняты.", show_alert=True)
    draft_fighters = resolve_draft_fighters(player, draft, expedition_mode=True)
    if len(draft_fighters) != len(draft):
        return await callback.answer("Часть бойцов уже занята или устала. Обновите окно.", show_alert=True)

    await callback.answer("Отряд отправляется.")
    now = int(time.time())
    for fighter in draft_fighters:
        consume_fighter_stamina(fighter, now)
        fighter["status"] = "expedition"
    stats = build_team_stats(draft_fighters, barracks_level)
    expedition = {
        "id": uuid.uuid4().hex[:10],
        "fighters": [fighter["unit_id"] for fighter in draft_fighters],
        "fighter_ids": [fighter["id"] for fighter in draft_fighters],
        "fighter_snapshots": [build_fighter_snapshot(fighter) for fighter in draft_fighters],
        "started_at": now,
        "max_end_at": now + BASE_EXPEDITION_DURATION + len(draft) * 900,
        "status": "searching",
        "status_label": "Исследует мир",
        "cargo_capacity": stats["cargo_capacity"],
        "loot": {},
        "total_hp": stats["total_hp"],
        "current_hp": stats["total_hp"],
        "logs": [
            "Отряд вышел за ворота и уже спорит, кто понесёт котёл.",
            f"Командование ждёт богатый рейс: груз {stats['cargo_capacity']}, ударная мощь {stats['tier_pressure']}, нюх на добычу {stats['loot_bonus']}.",
        ],
        "planned_events": await build_planned_events(draft_fighters, stats),
        "event_index": 0,
        "next_tick_at": now + EXPEDITION_TICK,
        "return_end_at": 0,
        "final_report": "",
    }
    active = player.get("active_expeditions", [])
    active.append(expedition)
    counters = increment_action_counter(player, "expeditions_started")
    expedition_drafts[user_id] = []

    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "fighters": player.get("fighters", []),
                "active_expeditions": active,
                "action_counters": counters,
            }
        },
    )
    player["active_expeditions"] = active
    await safe_edit_text(callback.message, render_expeditions_text(player, []), build_expeditions_keyboard(player, []))


def find_expedition(player: dict, expedition_id: str) -> dict | None:
    for expedition in player.get("active_expeditions", []):
        if expedition.get("id") == expedition_id:
            return expedition
    return None


@router.callback_query(F.data.startswith("expedition_view:"))
async def expedition_view(callback: types.CallbackQuery):
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    expedition = find_expedition(player, callback.data.split(":")[1])
    if not expedition:
        return await callback.answer("Экспедиция уже закрыта.", show_alert=True)
    await safe_edit_text(callback.message, render_expedition_detail(expedition), build_expedition_detail_keyboard(expedition))
    await callback.answer()


@router.callback_query(F.data.startswith("expedition_retreat:"))
async def expedition_retreat(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    expedition = find_expedition(player, callback.data.split(":")[1])
    if not expedition:
        return await callback.answer("Экспедиция не найдена.", show_alert=True)
    finalize_return(expedition, "командир нажал отступление")
    counters = increment_action_counter(player, "expeditions_retreats")
    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {"active_expeditions": player["active_expeditions"], "action_counters": counters}},
    )
    await safe_edit_text(callback.message, render_expedition_detail(expedition), build_expedition_detail_keyboard(expedition))
    await callback.answer("Отряд развернут домой.")


@router.callback_query(F.data.startswith("expedition_claim:"))
async def expedition_claim(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    expedition_id = callback.data.split(":")[1]
    expedition = find_expedition(player, expedition_id)
    if not expedition or expedition.get("status") != "arrived":
        return await callback.answer("Отряд ещё не прибыл.", show_alert=True)

    inventory = player.get("inventory", {})
    burned = []
    for item_id, amount in expedition.get("loot", {}).items():
        if item_id not in inventory and get_used_slots(inventory) >= STORAGE_CAPACITY.get(player.get("buildings", {}).get("storage", 0), 20):
            burned.append(f"{ELEMENTS.get(item_id, item_id)} x{amount}")
            continue
        inventory[item_id] = inventory.get(item_id, 0) + amount

    for fighter_id in expedition.get("fighter_ids", []):
        fighter = find_fighter(player, fighter_id)
        if not fighter:
            continue
        fighter["status"] = "available"
        grant_mission_progress(fighter, "expedition")

    player["active_expeditions"] = [item for item in player.get("active_expeditions", []) if item.get("id") != expedition_id]
    found_fragment = await try_grant_random_lore_fragment(player, chance=0.15)
    outcome_counter = "expeditions_failed" if expedition.get("return_reason") == "отряд едва выжил и бросил лут" else "expeditions_completed"
    counters = increment_action_counter(player, outcome_counter)
    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "inventory": inventory,
                "fighters": player.get("fighters", []),
                "active_expeditions": player["active_expeditions"],
                "action_counters": counters,
                "lore_fragments": player.get("lore_fragments", {}),
                "lore_fragment_progress": player.get("lore_fragment_progress", {}),
            }
        },
    )

    returned_loot = format_loot_summary(expedition.get("loot", {}))
    returned_units = ", ".join(get_role_name(item) for item in expedition.get("fighter_snapshots", expedition.get("fighters", [])))
    text = (
        "🏁 **ОТЧЁТ ЭКСПЕДИЦИИ**\n"
        f"Бойцы: {returned_units}\n"
        f"Итог: {expedition.get('final_report') or await generate_expedition_final_report(expedition)}\n\n"
        f"Доставлено на склад: {returned_loot}\n"
        f"Причина возврата: {expedition.get('return_reason', 'маршрут завершён')}\n"
        f"Состояние строя: {expedition.get('current_hp', 0)}/{expedition.get('total_hp', 0)} HP"
    )
    if burned:
        text += "\nСгорело из-за лимита склада: " + ", ".join(burned)
    if found_fragment:
        text += (
            "\n\n📜 Найден обрывок лора: "
            f"{found_fragment['title']} ({found_fragment['collection_name']})."
        )
    await callback.message.edit_text(text, parse_mode="Markdown")
    await callback.answer("Отряд вернулся в строй.")


async def notify_arrived_expeditions():
    cursor = db.alchemy_players.find({"active_expeditions.status": "arrived"})
    async for player in cursor:
        last_notified = False
        for expedition in player.get("active_expeditions", []):
            if expedition.get("status") == "arrived" and not expedition.get("arrival_notified"):
                expedition["arrival_notified"] = True
                last_notified = True
                try:
                    await bot.send_message(player["user_id"], f"📻 Экспедиция #{expedition['id'][:4]} вернулась. Открой /base и встреть отряд.")
                except Exception:
                    pass
        if last_notified:
            await db.alchemy_players.update_one(
                {"user_id": player["user_id"]},
                {"$set": {"active_expeditions": player.get("active_expeditions", [])}},
            )


async def safe_edit_text(message: types.Message, text: str, reply_markup=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
