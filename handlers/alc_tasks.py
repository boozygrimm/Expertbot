import time
import random
import logging
from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from bot_instance import bot
from database import db
from handlers.alc_core import STORAGE_CAPACITY, get_stamina_ready_at, get_used_slots, get_world_effects
from handlers.alc_fighters import get_fighter_upkeep, get_player_fighters
from handlers.alc_recipes import BATTLE_UNITS, ELEMENTS
from handlers.alc_heritage import get_active_legacy_bonus_map
from config import get_msk_now

logger = logging.getLogger(__name__)


def build_daily_report_text(supply_report: dict | None, heritage_report: dict | None) -> str:
    supply_report = supply_report or {}
    heritage_report = heritage_report or {}
    drained = supply_report.get("drained_energy", 0)
    served_units = supply_report.get("served_units", 0)
    deserters = supply_report.get("deserters", 0)
    exhausted_units = supply_report.get("exhausted_units", 0)
    xp_lost = supply_report.get("xp_lost", 0)
    energy_left = supply_report.get("energy_left", 0)
    date_label = supply_report.get("date_label") or heritage_report.get("date_label", "сегодня")
    heritage_lines = heritage_report.get("granted_lines", [])
    skipped_lines = heritage_report.get("skipped_lines", [])

    lines = [
        f"📊 <b>УТРЕННИЙ ОТЧЕТ</b> [{date_label}]",
        "",
        "Служба снабжения:",
        f"• Списано энергии: {drained}",
        f"• Обслужено бойцов: {served_units}",
        f"• Остаток энергии: {energy_left}",
    ]
    if exhausted_units:
        lines.append(f"• Остались без отдыха: {exhausted_units}")
        lines.append(f"• Потеряно опыта: {xp_lost}")
    if deserters:
        lines.append(f"• Дезертировало: {deserters}")

    lines.extend(["", "Наследие мира:"])
    if heritage_lines:
        lines.extend(f"• Получено: {entry}" for entry in heritage_lines)
    else:
        lines.append("• Активных начислений не было.")
    if skipped_lines:
        lines.extend(f"• Не влезло на склад: {entry}" for entry in skipped_lines)
    return "\n".join(lines)


def build_report_close_markup() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Закрыть", callback_data="alc:base_close")
    builder.adjust(1)
    return builder.as_markup()

async def check_alchemy_stamina():
    now = int(time.time())
    cursor = db.alchemy_players.find({"last_mine": {"$gt": 0}, "stamina_notified": {"$ne": True}})
    async for player in cursor:
        if get_stamina_ready_at(player) > now:
            continue
        user_id = player["user_id"]
        try:
            await bot.send_message(user_id, "⚡ Выносливость восстановлена! Жми /pohod")
            await db.alchemy_players.update_one({"user_id": user_id}, {"$set": {"stamina_notified": True}})
        except Exception:
            await db.alchemy_players.update_one({"user_id": user_id}, {"$set": {"stamina_notified": True}})

async def collect_production_loot():
    """Сбор ресурсов, остановка линий через 24 часа и учет подробной статистики."""
    from handlers.alc_production import STATIONS
    
    cursor = db.alchemy_players.find({"production_slots": {"$exists": True, "$ne": []}})
    now = int(time.time())
    
    async for player in cursor:
        inv = player.get("inventory", {})
        slots = player.get("production_slots", [])
        world_effects = get_world_effects(player)
        
        storage_lvl = player.get("buildings", {}).get("storage", 0)
        from handlers.alc_core import STORAGE_CAPACITY
        max_slots = STORAGE_CAPACITY.get(storage_lvl, 20)
        
        added_count = 0
        slots_changed = False
        
        for slot in slots:
            s_type = slot['type']
            assigned = slot.get('assigned_at', 0)
            
            # Проверяем остановку линии (24 часа для всех: и духов, и ручного труда)
            if assigned > 0 and (now - assigned) >= 24 * 3600:
                if not slot.get('stopped'):
                    slot['worker'] = None # Дух сгорает
                    slot['stopped'] = True
                    slots_changed = True
                continue # Линия стоит, пропускаем добычу
            
            if slot.get('stopped'):
                continue
                
            # Инициализация словаря для детальной статистики
            if 'mined_items' not in slot:
                slot['mined_items'] = {}
                slots_changed = True
                
            possible = STATIONS[s_type]['items']
            
            # Логика урожайности
            count = 1 # Базовая добыча при ручном труде
            if slot.get('worker'):
                w = slot['worker']
                # Синергия: Родная стихия дает максимальный буст (х3)
                if (w == 'spirit_e' and s_type == 'mine') or \
                   (w == 'spirit_w' and s_type == 'farm') or \
                   (w == 'spirit_f' and s_type == 'factory') or \
                   (w == 'spirit_a' and s_type == 'aero'):
                    count = 3
                else:
                    count = 2 # Базовый буст духа в чужом цехе (х2)
            if s_type == "mine":
                count += int(world_effects.get("mine_yield", 0) or 0)
            elif s_type == "farm":
                count += int(world_effects.get("farm_yield", 0) or 0)
            elif s_type == "factory":
                count += int(world_effects.get("factory_yield", 0) or 0)
            elif s_type == "aero":
                count += int(world_effects.get("aero_yield", 0) or 0)
            
            # Сбор лута
            for _ in range(count):
                item = random.choice(possible)
                
                from handlers.alc_core import get_used_slots
                if item not in inv and get_used_slots(inv) >= max_slots:
                    break # Склад полон
                
                # Добавляем в инвентарь
                inv[item] = inv.get(item, 0) + 1
                added_count += 1
                
                # Учет конкретного предмета в слот
                slot['mined_items'][item] = slot['mined_items'].get(item, 0) + 1
                slot['total_mined'] = slot.get('total_mined', 0) + 1
                slots_changed = True
                
                # Шанс 2% найти свиток или пак на производстве
                if random.random() < 0.02 + float(world_effects.get("rare_find_bonus", 0) or 0):
                    bonus = random.choice(["recipe_scroll", "pack_s"])
                    if bonus in inv or get_used_slots(inv) < max_slots:
                        inv[bonus] = inv.get(bonus, 0) + 1
                
        if added_count > 0 or slots_changed:
            await db.alchemy_players.update_one(
                {"user_id": player["user_id"]},
                {"$set": {"inventory": inv, "production_slots": slots}}
            )


def build_barracks_report_text(report: dict) -> str:
    drained = report.get("drained_energy", 0)
    served_units = report.get("served_units", 0)
    deserters = report.get("deserters", 0)
    exhausted_units = report.get("exhausted_units", 0)
    xp_lost = report.get("xp_lost", 0)
    energy_left = report.get("energy_left", 0)
    text = (
        f"📊 Отчет службы снабжения:\n"
        f"Списано {drained} энергии за содержание {served_units} воинов, линий производства и гарнизона.\n"
        f"Остаток: {energy_left}."
    )
    if exhausted_units:
        text += f"\n😵 Без отдыха осталось: {exhausted_units}. Потеряно XP: {xp_lost}."
    if deserters:
        text += f"\n⚠️ Из-за нехватки энергии дезертировало: {deserters}."
    return text


async def process_barracks_upkeep():
    today_label = get_msk_now().strftime("%Y-%m-%d")
    cursor = db.alchemy_players.find(
        {
            "$or": [
                {"fighters.0": {"$exists": True}},
                {"active_stands.0": {"$exists": True}},
            ],
            "last_barracks_drain": {"$ne": today_label},
        }
    )

    async for player in cursor:
        user_id = player["user_id"]
        inv = player.get("inventory", {})
        heritage_report = player.get("heritage_report", {})
        if heritage_report.get("date_label") != today_label:
            heritage_report = {}

        fighters = [fighter for fighter in get_player_fighters(player) if fighter.get("status") != "dead"]
        drained_energy = 0
        served_units = 0
        deserters = 0
        exhausted_units = 0
        xp_lost = 0

        deserter_lines = []
        for fighter in list(fighters):
            upkeep = get_fighter_upkeep(fighter)
            available_energy = inv.get("energy", 0)
            if available_energy >= upkeep:
                inv["energy"] -= upkeep
                drained_energy += upkeep
                served_units += 1
                continue
            current_xp = max(0, int(fighter.get("xp", 0) or 0))
            if current_xp > 0:
                lost_now = min(2, current_xp)
                fighter["xp"] = current_xp - lost_now
                fighter["stamina"] = 0
                fighter["stamina_updated_at"] = int(time.time())
                exhausted_units += 1
                xp_lost += lost_now
                continue
            fallback = BATTLE_UNITS.get(fighter.get("unit_id"), {}).get("fallback_spirit", "spirit_f")
            inv[fallback] = inv.get(fallback, 0) + 1
            player["fighters"] = [item for item in player.get("fighters", []) if item.get("id") != fighter.get("id")]
            deserter_lines.append(f"{ELEMENTS.get(fighter.get('unit_id'), fighter.get('unit_id'))}")
            deserters += 1

        if inv.get("energy", 0) <= 0 and "energy" in inv:
            del inv["energy"]

        report = {
            "date_label": today_label,
            "drained_energy": drained_energy,
            "served_units": served_units,
            "deserters": deserters,
            "exhausted_units": exhausted_units,
            "xp_lost": xp_lost,
            "energy_left": inv.get("energy", 0),
            "unit_lines": deserter_lines,
        }

        await db.alchemy_players.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "inventory": inv,
                    "fighters": player.get("fighters", []),
                    "barracks_report": report,
                    "last_barracks_drain": today_label,
                    "heritage_report": heritage_report,
                }
            },
        )

        if fighters or heritage_report.get("granted_lines") or heritage_report.get("skipped_lines"):
            try:
                await bot.send_message(
                    user_id,
                    build_daily_report_text(report, heritage_report),
                    reply_markup=build_report_close_markup(),
                    parse_mode="HTML",
                )
            except Exception:
                logger.info("Не удалось отправить утренний отчет user_id=%s", user_id)


async def process_heritage_income():
    today_label = get_msk_now().strftime("%Y-%m-%d")
    cursor = db.alchemy_players.find(
        {
            "active_stands.0": {"$exists": True},
            "last_heritage_income": {"$ne": today_label},
        }
    )

    async for player in cursor:
        bonus_map = await get_active_legacy_bonus_map(player)
        if not bonus_map:
            await db.alchemy_players.update_one(
                {"user_id": player["user_id"]},
                {
                    "$set": {
                        "last_heritage_income": today_label,
                        "heritage_report": {
                            "date_label": today_label,
                            "granted_lines": [],
                            "skipped_lines": [],
                        },
                    }
                },
            )
            continue

        inventory = player.get("inventory", {})
        storage_lvl = player.get("buildings", {}).get("storage", 0)
        max_slots = STORAGE_CAPACITY.get(storage_lvl, 20)
        granted_lines = []
        skipped_lines = []

        for item_id, amount in bonus_map.items():
            if item_id not in inventory and get_used_slots(inventory) >= max_slots:
                skipped_lines.append(f"{ELEMENTS.get(item_id, item_id)} x{amount}")
                continue
            inventory[item_id] = inventory.get(item_id, 0) + amount
            granted_lines.append(f"{ELEMENTS.get(item_id, item_id)} x{amount}")

        await db.alchemy_players.update_one(
            {"user_id": player["user_id"]},
            {
                "$set": {
                    "inventory": inventory,
                    "last_heritage_income": today_label,
                    "heritage_report": {
                        "date_label": today_label,
                        "granted_lines": granted_lines,
                        "skipped_lines": skipped_lines,
                    },
                }
            },
        )
            
