from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database import db
from handlers.alc_recipes import ELEMENTS
from handlers.alc_build import BUILDINGS

router = Router()

# Типы производственных точек (добавлен Аэроцех)
STATIONS = {
    "mine": {"name": "⛏ Шахта", "items": ["e", "stone", "sand", "metal"], "icon": "🪨"},
    "farm": {"name": "🚜 Ферма", "items": ["w", "cane", "wood", "wheat"], "icon": "🌱"},
    "factory": {"name": "🏭 Завод", "items": ["f", "energy", "brick", "glass"], "icon": "⚙️"},
    "aero": {"name": "☁️ Аэроцех", "items": ["a", "bacteria", "life", "bird"], "icon": "💨"}
}

# Обновлено время жизни: все живут строго 24 часа
SPIRIT_STATS = {
    "spirit_f": {"name": "🔥 Огонь", "life_h": 24, "desc": "Живет 24ч. Максимум на Заводе."},
    "spirit_w": {"name": "💧 Вода", "life_h": 24, "desc": "Живет 24ч. Максимум на Ферме."},
    "spirit_a": {"name": "💨 Воздух", "life_h": 24, "desc": "Живет 24ч. Максимум в Аэроцехе."},
    "spirit_e": {"name": "🪨 Земля", "life_h": 24, "desc": "Живет 24ч. Максимум в Шахте."}
}

@router.callback_query(F.data == "altar_open")
async def open_production(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await db.alchemy_players.find_one({"user_id": user_id})
    active_slots = player.get("production_slots", [])
    
    altar_lvl = player.get("buildings", {}).get("altar", 0)
    max_slots = BUILDINGS["altar"]["levels"].get(altar_lvl, {}).get("slots", 0)
    
    import time
    text = f"🏗 **ПРОИЗВОДСТВО**\nЛиний доступно: {len(active_slots)}/{max_slots}\n\n"
    
    now = int(time.time())
    for i, slot in enumerate(active_slots):
        assigned = slot.get('assigned_at', 0)
        # Оставшееся время до 24 часов
        left_h = max(0, (24 * 3600 - (now - assigned)) / 3600)
        
        # Формируем список добытого
        mined_dict = slot.get('mined_items', {})
        if mined_dict:
            mined_str = ", ".join([f"{ELEMENTS.get(k, k)}: {v}" for k, v in mined_dict.items()])
        else:
            mined_str = "Ничего"
            
        if slot.get('stopped') or (assigned > 0 and left_h <= 0):
            text += f"Линия {i+1}: {STATIONS[slot['type']]['name']} | 🛑 Остановлена | Добыто: {mined_str}\n"
        else:
            w = slot.get('worker')
            w_name = SPIRIT_STATS.get(w, {}).get('name', w) if w else "✋ Ручной труд"
            text += f"Линия {i+1}: {STATIONS[slot['type']]['name']} | {w_name} ({left_h:.1f}ч) | Добыто: {mined_str}\n"
            
    text += "\nℹ️ **Справка по духам:**\n"
    for k, v in SPIRIT_STATS.items():
        text += f"- {v['name']}: {v['desc']}\n"
    
    builder = InlineKeyboardBuilder()
    
    for i in range(max_slots):
        if i < len(active_slots):
            slot = active_slots[i]
            st_info = STATIONS[slot['type']]
            
            # Проверяем статус для кнопки
            is_stopped = slot.get('stopped') or (slot.get('assigned_at', 0) > 0 and max(0, 24*3600 - (now - slot.get('assigned_at', 0))) <= 0)
            worker = ELEMENTS.get(slot['worker'], "Без рабочего") if slot['worker'] else "✋ Ручной труд"
            status_text = "🛑 Остановлена" if is_stopped else f"👷 {worker}"
            
            text += f"📍 Слот {i+1}: {st_info['name']} | {status_text}\n"
            # Изменена надпись: "Демонтировать", если работает, и "Собрать цех", если встал
            btn_text = f"📦 Собрать ресурсы {i+1}" if is_stopped else f"❌ Демонтировать {i+1}"
            builder.button(text=btn_text, callback_data=f"prod_remove:{i}")
        else:
            text += f"📍 Слот {i+1}: 🔘 **Свободно**\n"
            builder.button(text=f"➕ Установить станцию {i+1}", callback_data=f"prod_setup:{i}")

    builder.button(text="🔙 В Штаб", callback_data="alc:base_return")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@router.callback_query(F.data.startswith("prod_setup:"))
async def setup_station(callback: types.CallbackQuery):
    slot_idx = callback.data.split(":")[1]
    text = "Какую станцию желаете развернуть?"
    builder = InlineKeyboardBuilder()
    for s_id, info in STATIONS.items():
        builder.button(text=info['name'], callback_data=f"prod_select_type:{slot_idx}:{s_id}")
    builder.button(text="🔙 Назад", callback_data="altar_open")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("prod_select_type:"))
async def select_worker(callback: types.CallbackQuery):
    _, slot_idx, s_type = callback.data.split(":")
    player = await db.alchemy_players.find_one({"user_id": callback.from_user.id})
    inv = player.get("inventory", {})
    
    text = f"Станция: {STATIONS[s_type]['name']}\n⚠️ **Внимание:** Запуск духа требует 1 ед. Энергии!\nЛюбая линия работает ровно 24 часа. Выберите рабочего:"
    builder = InlineKeyboardBuilder()
    
    spirits = ["spirit_f", "spirit_w", "spirit_e", "spirit_a"]
    for sp in spirits:
        if inv.get(sp, 0) > 0:
            builder.button(text=f"📥 Нанять {ELEMENTS.get(sp, sp)}", callback_data=f"prod_final:{slot_idx}:{s_type}:{sp}")
            
    builder.button(text="✋ Без рабочего (Бесплатно)", callback_data=f"prod_final:{slot_idx}:{s_type}:none")
    builder.button(text="🔙 Отмена", callback_data="altar_open")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("prod_final:"))
async def finalize_prod(callback: types.CallbackQuery):
    _, idx, s_type, worker = callback.data.split(":")
    user_id = callback.from_user.id
    player = await db.alchemy_players.find_one({"user_id": user_id})
    inv = player.get("inventory", {})
    active_slots = player.get("production_slots", [])
    
    if worker != "none":
        if inv.get(worker, 0) <= 0: 
            return await callback.answer("Дух куда-то делся...", show_alert=True)
        if inv.get("energy", 0) <= 0:
            return await callback.answer("⚡ Не хватает 1 Энергии для запуска линии!", show_alert=True)
            
        inv[worker] -= 1
        inv["energy"] -= 1
        
        if inv[worker] <= 0: del inv[worker]
        if inv["energy"] <= 0: del inv["energy"]
    else:
        worker = None
        
    import time
    new_slot = {
        "type": s_type, 
        "worker": worker, 
        # Время назначения устанавливается всегда, даже для ручного труда
        "assigned_at": int(time.time()), 
        "total_mined": 0,
        "mined_items": {}, # Новый словарь для учета лута
        "stopped": False
    }

    active_slots.append(new_slot)
    
    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {"inventory": inv, "production_slots": active_slots}}
    )
    await callback.answer("✅ Станция запущена на 24 часа!")
    await open_production(callback)

@router.callback_query(F.data.startswith("prod_remove:"))
async def remove_prod(callback: types.CallbackQuery):
    idx = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    player = await db.alchemy_players.find_one({"user_id": user_id})
    inv = player.get("inventory", {})
    active_slots = player.get("production_slots", [])
    
    removed = active_slots.pop(idx)
    # Рабочий возвращается только если он не сгорел от старости (worker is not None)
    if removed.get('worker'):
        inv[removed['worker']] = inv.get(removed['worker'], 0) + 1
        msg_text = "🚜 Станция демонтирована, рабочий вернулся."
    else:
        msg_text = "📦 Цех разобран, площадка свободна."
        
    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {"inventory": inv, "production_slots": active_slots}}
    )
    await callback.answer(msg_text)
    await open_production(callback)
