import time
import random
import logging
from bot_instance import bot
from database import db
from handlers.alc_core import get_stamina_ready_at, get_used_slots
from handlers.alc_recipes import BATTLE_UNITS, ELEMENTS
from config import get_msk_now

logger = logging.getLogger(__name__)

async def check_alchemy_stamina():
    now = int(time.time())
    cursor = db.alchemy_players.find({"last_mine": {"$gt": 0}, "stamina_notified": {"$ne": True}})
    async for player in cursor:
        if get_stamina_ready_at(player) > now:
            continue
        user_id = player["user_id"]
        try:
            await bot.send_message(user_id, "⚡ Выносливость восстановлена! Жми /base")
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
                if random.random() < 0.02:
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
    energy_left = report.get("energy_left", 0)
    text = (
        f"📊 Отчет службы снабжения:\n"
        f"Списано {drained} энергии за содержание {served_units} воинов, линий производства и гарнизона.\n"
        f"Остаток: {energy_left}."
    )
    if deserters:
        text += f"\n⚠️ Из-за нехватки энергии дезертировало: {deserters}."
    return text


async def process_barracks_upkeep():
    today_label = get_msk_now().strftime("%Y-%m-%d")
    unit_filters = [{f"inventory.{unit_id}": {"$gt": 0}} for unit_id in BATTLE_UNITS]
    cursor = db.alchemy_players.find(
        {
            "$or": unit_filters,
            "last_barracks_drain": {"$ne": today_label},
        }
    )

    async for player in cursor:
        user_id = player["user_id"]
        inv = player.get("inventory", {})

        drained_energy = 0
        served_units = 0
        deserters = 0

        unit_ids = [unit_id for unit_id in BATTLE_UNITS if inv.get(unit_id, 0) > 0]
        if not unit_ids:
            await db.alchemy_players.update_one(
                {"user_id": user_id},
                {"$set": {"last_barracks_drain": today_label}},
            )
            continue

        for unit_id in unit_ids:
            meta = BATTLE_UNITS[unit_id]
            amount = inv.get(unit_id, 0)
            upkeep = meta["upkeep"]
            available_energy = inv.get("energy", 0)
            sustainable = min(amount, available_energy // upkeep) if upkeep > 0 else amount

            if sustainable > 0:
                spend = sustainable * upkeep
                inv["energy"] -= spend
                drained_energy += spend
                served_units += sustainable

            lost = amount - sustainable
            if lost > 0:
                inv[unit_id] -= lost
                if inv[unit_id] <= 0:
                    del inv[unit_id]
                fallback = meta["fallback_spirit"]
                inv[fallback] = inv.get(fallback, 0) + lost
                deserters += lost

        if inv.get("energy", 0) <= 0 and "energy" in inv:
            del inv["energy"]

        report = {
            "date_label": today_label,
            "drained_energy": drained_energy,
            "served_units": served_units,
            "deserters": deserters,
            "energy_left": inv.get("energy", 0),
            "unit_lines": [f"{ELEMENTS.get(unit_id, unit_id)} x{player.get('inventory', {}).get(unit_id, 0)}" for unit_id in unit_ids],
        }

        await db.alchemy_players.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "inventory": inv,
                    "barracks_report": report,
                    "last_barracks_drain": today_label,
                }
            },
        )

        try:
            await bot.send_message(user_id, build_barracks_report_text(report))
        except Exception:
            logger.info("Не удалось отправить отчет снабжения user_id=%s", user_id)
            
