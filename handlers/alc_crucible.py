import time
import random
from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database import db
from handlers.alc_fighters import create_fighter, get_barracks_capacity
from handlers.alc_recipes import ELEMENTS, RECIPES

router = Router()

# =====================================================================
# НАСТРОЙКИ БАЛАНСА И ИНТЕРФЕЙСА (МЕНЯТЬ ЗДЕСЬ)
# =====================================================================
ROWS_PER_PAGE = 15 # Количество рядов кнопок на 1 странице инвентаря
# =====================================================================
# ФУНКЦИИ-ПОМОЩНИКИ ДЛЯ РАБОТЫ С ИМЕНАМИ И ЭМОДЗИ
# =====================================================================

def get_el_name(k):
    """Возвращает только название предмета (без эмодзи)"""
    el = ELEMENTS.get(k, k)
    if isinstance(el, str):
        parts = el.split(" ", 1)
        return parts[1] if len(parts) > 1 else el
    return str(k)

def get_el_emoji(k):
    """Возвращает только эмодзи предмета"""
    el = ELEMENTS.get(k, k)
    if isinstance(el, str):
        parts = el.split(" ", 1)
        return parts[0] if len(parts) > 1 else "❓"
    return "❓"
    
# Время Тигля
CRUCIBLE_BASE_TIME = 0       # Базовое время старта (в секундах)
CRUCIBLE_TIME_PER_CYCLE = 600 # Время за 1 цикл варки пары (по умолчанию 10 минут)

# Время Реторты
RETORT_BASE_TIME = 300       # Базовое время на разогрев колбы (по умолчанию 5 минут)
RETORT_TIME_PER_ITEM = 30    # Добавочное время за КАЖДУЮ расщепляемую штуку (по умолчанию 30 сек)
# =====================================================================

crucible_amounts = {} # Добавить в начало файла alc_crucible.py
retort_amounts = {}

# Временные хранилища (в памяти)
active_crucible_mixes = {} 
active_retort_mixes = {}   
crucible_pages = {}        
retort_pages = {}          

# --- ВСПОМОГАТЕЛЬНАЯ ЛОГИКА ---
def find_recipe_result(i1, i2):
    for (r1, r2), res in RECIPES.items():
        if (i1 == r1 and i2 == r2) or (i1 == r2 and i2 == r1):
            return res
    return None

def get_reverse_recipes():
    return {res: (r1, r2) for (r1, r2), res in RECIPES.items()}

def get_known_items(player):
    """Возвращает set известных игроку элементов (как в Книге рецептов)"""
    known = set(player.get("discovered", []))
    for k in player.get("inventory", {}).keys():
        known.add(k)
    return known

def decompose_item(item, amount, known_items, reverse_recipes):
    """Рекурсивный разбор элемента до простейших известных компонентов (DFS)"""
    results = {}
    stack = [(item, amount)]
    
    while stack:
        curr_item, curr_amount = stack.pop()
        # Если предмет можно разложить и игрок знает его рецепт
        if curr_item in reverse_recipes and curr_item in known_items:
            i1, i2 = reverse_recipes[curr_item]
            stack.append((i1, curr_amount))
            stack.append((i2, curr_amount))
        else:
            # Дальше разложить не можем (или базовый, или не изучен)
            results[curr_item] = results.get(curr_item, 0) + curr_amount
            
    return results

# =====================================================================
# ТИГЕЛЬ (Слияние любых рецептов без потерь)
# =====================================================================

@router.callback_query(F.data == "crucible_open")
async def render_crucible(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await db.alchemy_players.find_one({"user_id": user_id})
    inventory = player.get("inventory", {})
    process = player.get("crucible_process", None)
    current_mix = active_crucible_mixes.get(user_id, [])
    grid_size = player.get("inv_grid", 3)
    page = crucible_pages.get(user_id, 0)
    
    text = "🔥 **АЛХИМИЧЕСКИЙ ТИГЕЛЬ**\nРесурсы переплавляются без потерь (1+1=2).\n\n"
    builder = InlineKeyboardBuilder()

    if process:
        remaining = process["finish_time"] - int(time.time())
        res_name = ELEMENTS.get(process["result"], process["result"])
        
        if remaining <= 0:
            text += f"✅ **Готово!**\nПолучено: {process['amount']} шт. {res_name}"
            builder.button(text="🎁 Забрать результат", callback_data="crucible_claim")
        else:
            mins = remaining // 60
            text += f"⏳ **В процессе...**\nСоздается: {res_name}\nОсталось: {mins} мин."
            builder.button(text="🔄 Обновить статус", callback_data="crucible_open")
        builder.adjust(1)
    else:
        if not current_mix:
            text += "Тигель остыл. Выбери **два** ингредиента."
        else:
            names = [ELEMENTS.get(i, i) for i in current_mix]
            text += f"🔥 В тигле: **{' + '.join(names)}**\n"
            
            ctrl_btns = InlineKeyboardBuilder()
            if len(current_mix) == 2:
                res = find_recipe_result(current_mix[0], current_mix[1])
                if res:
                    i1, i2 = current_mix[0], current_mix[1]
                    max_amt = min(inventory.get(i1, 0), inventory.get(i2, 0)) if i1 != i2 else inventory.get(i1, 0) // 2
                    
                    amt = crucible_amounts.get(user_id, 1)
                    if amt > max_amt: amt = max_amt
                    if amt < 1: amt = 1
                    crucible_amounts[user_id] = amt

                    text += f"✨ Рецепт распознан: **{ELEMENTS.get(res, res)}**\nВыбрано циклов: **{amt}** из {max_amt}"
                    
                    ctrl_btns.button(text="-5", callback_data="crucible_amt:-5")
                    ctrl_btns.button(text="-1", callback_data="crucible_amt:-1")
                    ctrl_btns.button(text="+1", callback_data="crucible_amt:1")
                    ctrl_btns.button(text="+5", callback_data="crucible_amt:5")
                    ctrl_btns.button(text="Максимум", callback_data="crucible_amt:max")
                    ctrl_btns.button(text=f"🔥 ЗАПУСТИТЬ ТИГЕЛЬ ({amt})", callback_data="crucible_start_run")
                    ctrl_btns.adjust(4, 1, 1)
                else:
                    text += "❌ Неизвестная комбинация. Пшик."
                    
            ctrl_btns.button(text="🗑 Очистить тигель", callback_data="crucible_clear")
            builder.attach(ctrl_btns)

        # Вывод инвентаря
        visible_items = [(k, v) for k, v in inventory.items() if v > 0 and not k.startswith("pack_") and k != "recipe_scroll" and k not in ["base_crystal", "active_base"]]
        
        if not visible_items:
            text += "\n\n🎒 Твой инвентарь пуст."
        else:
            text += "\n\n🎒 **Выбери ресурсы:**"
            inv_btns = InlineKeyboardBuilder()
            BASIC_ELEMENTS = ["w", "f", "e", "a"]
            
            basic_items = [(k, v) for k, v in visible_items if k in BASIC_ELEMENTS]
            if basic_items:
                basic_row = []
                for k, v in basic_items:
                    emoji = get_el_emoji(k)
                    basic_row.append(types.InlineKeyboardButton(text=f"{emoji} {v}", callback_data=f"crucible_add:{k}"))
                inv_btns.row(*basic_row)

            other_items = [(k, v) for k, v in visible_items if k not in BASIC_ELEMENTS]
            other_items.sort(key=lambda x: get_el_name(x[0]))
            
            items_per_page = grid_size * ROWS_PER_PAGE
            total_pages = max(1, (len(other_items) + items_per_page - 1) // items_per_page)
            if page >= total_pages: page = max(0, total_pages - 1)
            
            start_idx = page * items_per_page
            page_items = other_items[start_idx : start_idx + items_per_page]
            
            row = []
            for code, amount in page_items:
                name = get_el_name(code)
                if grid_size == 4:
                    btn_text = f"{name} {amount}"
                else:
                    emoji = get_el_emoji(code)
                    btn_text = f"{emoji} {name} {amount}"
                    
                row.append(types.InlineKeyboardButton(text=btn_text, callback_data=f"crucible_add:{code}"))
                if len(row) == grid_size:
                    inv_btns.row(*row)
                    row = []
            if row:
                inv_btns.row(*row)
                
            builder.attach(inv_btns)

            if total_pages > 1:
                nav_row = []
                if page > 0: nav_row.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"crucible_page:{page-1}"))
                if page < total_pages - 1: nav_row.append(types.InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"crucible_page:{page+1}"))
                if nav_row:
                    paginator = InlineKeyboardBuilder()
                    paginator.row(*nav_row)
                    builder.attach(paginator)

    nav = InlineKeyboardBuilder()
    nav.button(text="🔙 В Лабораторию", callback_data="lab_open")
    nav.adjust(1)
    builder.attach(nav)
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

# НОВЫЙ ХЭНДЛЕР ВЫБОРА КОЛИЧЕСТВА
@router.callback_query(F.data.startswith("crucible_amt:"))
async def crucible_change_amt(callback: types.CallbackQuery):
    uid = callback.from_user.id
    val = callback.data.split(":")[1]
    current = crucible_amounts.get(uid, 1)
    
    if val == "max":
        crucible_amounts[uid] = 999999 # Точный максимум подрежется в render_crucible
    else:
        crucible_amounts[uid] = current + int(val)
        
    await render_crucible(callback)

@router.callback_query(F.data.startswith("crucible_page:"))
async def crucible_change_page(callback: types.CallbackQuery):
    crucible_pages[callback.from_user.id] = int(callback.data.split(":")[1])
    await render_crucible(callback)

@router.callback_query(F.data.startswith("crucible_add:"))
async def crucible_add(callback: types.CallbackQuery):
    item = callback.data.split(":")[1]
    uid = callback.from_user.id
    mix = active_crucible_mixes.get(uid, [])
    
    if len(mix) >= 2:
        return await callback.answer("Тигель полон!", show_alert=True)
        
    player = await db.alchemy_players.find_one({"user_id": uid})
    if player.get("inventory", {}).get(item, 0) <= mix.count(item):
        return await callback.answer("У тебя больше нет этого ресурса!", show_alert=True)
        
    mix.append(item)
    active_crucible_mixes[uid] = mix
    await render_crucible(callback)

# ОБНОВЛЕННЫЙ ХЭНДЛЕР ОЧИСТКИ
@router.callback_query(F.data == "crucible_clear")
async def crucible_clear(callback: types.CallbackQuery):
    active_crucible_mixes.pop(callback.from_user.id, None)
    crucible_amounts.pop(callback.from_user.id, None) # Очищаем и выбранное кол-во
    await render_crucible(callback)

@router.callback_query(F.data == "crucible_start_run")
async def crucible_start_run(callback: types.CallbackQuery):
    uid = callback.from_user.id
    mix = active_crucible_mixes.get(uid, [])
    
    if len(mix) != 2: return await callback.answer("Нужно два ресурса!")
    
    i1, i2 = mix[0], mix[1]
    player = await db.alchemy_players.find_one({"user_id": uid})
    inv = player.get("inventory", {})
    
    max_amt = min(inv.get(i1, 0), inv.get(i2, 0)) if i1 != i2 else inv.get(i1, 0) // 2
    if max_amt <= 0:
        active_crucible_mixes.pop(uid, None)
        return await callback.answer("Не хватает ресурсов!", show_alert=True)
        
    amt = crucible_amounts.get(uid, 1)
    if amt > max_amt: amt = max_amt
    if amt < 1: amt = 1
    
    result_item = find_recipe_result(i1, i2)
    if not result_item:
        active_crucible_mixes.pop(uid, None)
        return await callback.answer("Неизвестный рецепт!", show_alert=True)
        
    if i1 == i2:
        inv[i1] -= amt * 2
    else:
        inv[i1] -= amt
        inv[i2] -= amt
        
    total_output = amt * 2
    if random.random() < 0.05: total_output = int(total_output * 1.2) or (total_output + 1)
    
    duration = CRUCIBLE_BASE_TIME + (amt * CRUCIBLE_TIME_PER_CYCLE)
    
    process = {
        "result": result_item,
        "amount": total_output,
        "finish_time": int(time.time()) + duration
    }
    
    active_crucible_mixes.pop(uid, None)
    crucible_amounts.pop(uid, None)
    
    await db.alchemy_players.update_one({"user_id": uid}, {"$set": {"inventory": inv, "crucible_process": process}})
    await callback.answer(f"🔥 Тигель запущен на {amt} циклов!")
    await render_crucible(callback)

@router.callback_query(F.data == "crucible_claim")
async def crucible_claim(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await db.alchemy_players.find_one({"user_id": user_id})
    process = player.get("crucible_process", None)
    
    if not process or int(time.time()) < process["finish_time"]:
        return await callback.answer("Еще не готово!")
    
    inv = player.get("inventory", {})
    item, amt = process["result"], process["amount"]
    
    from handlers.alc_recipes import BATTLE_UNITS
    from handlers.alc_core import STORAGE_CAPACITY, get_used_slots
    max_slots = STORAGE_CAPACITY.get(player.get("buildings", {}).get("storage", 0), 20)
    if item in BATTLE_UNITS:
        capacity = get_barracks_capacity(player.get("buildings", {}).get("barracks", 0))
        current = len([fighter for fighter in player.get("fighters", []) if fighter.get("status") != "dead"])
        if current + amt > capacity:
            return await callback.answer("В Казармах нет места для новых бойцов.", show_alert=True)
    elif item not in inv and get_used_slots(inv) >= max_slots:
        return await callback.answer("На складе нет свободных слотов!", show_alert=True)
    
    update_fields = {"inventory": inv}
    if item in BATTLE_UNITS:
        fighters = player.get("fighters", [])
        for _ in range(amt):
            fighters.append(create_fighter(item, crafted_tier3=BATTLE_UNITS[item]["tier"] >= 3))
        update_fields["fighters"] = fighters
        answer_text = f"🪖 Подготовлено бойцов: {amt}"
    else:
        inv[item] = inv.get(item, 0) + amt
        answer_text = f"📦 Получено {amt} ед. товара!"
    await db.alchemy_players.update_one({"user_id": user_id}, {"$set": update_fields, "$unset": {"crucible_process": ""}})
    await callback.answer(answer_text)
    await render_crucible(callback)

# =====================================================================
# РЕТОРТА (Глубокий анализ и разложение)
# =====================================================================
@router.callback_query(F.data == "retort_open")
async def render_retort(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await db.alchemy_players.find_one({"user_id": user_id})
    inventory = player.get("inventory", {})
    process = player.get("retort_process", None)
    current_mix = active_retort_mixes.get(user_id, [])
    grid_size = player.get("inv_grid", 3)
    page = retort_pages.get(user_id, 0)
    
    text = "🧪 **РЕТОРТА**\nРазбор сложных элементов до простейших известных компонентов по Книге рецептов.\n\n"
    builder = InlineKeyboardBuilder()

    if process:
        remaining = process["finish_time"] - int(time.time())
        
        if remaining <= 0:
            text += f"✅ **Анализ завершен!** Фракции готовы к извлечению."
            builder.button(text="🎁 Извлечь фракции", callback_data="retort_claim")
        else:
            mins = remaining // 60
            text += f"⏳ **Идет разложение...**\nОсталось: {mins} мин."
            builder.button(text="🔄 Обновить статус", callback_data="retort_open")
        builder.adjust(1)
    else:
        if not current_mix:
            text += "Колба пуста. Положи **один** тип элемента для разложения."
        else:
            name = ELEMENTS.get(current_mix[0], current_mix[0])
            item = current_mix[0]
            max_amt = max(1, int(inventory.get(item, 0) or 0))
            amt = retort_amounts.get(user_id, 1)
            if amt > max_amt:
                amt = max_amt
            if amt < 1:
                amt = 1
            retort_amounts[user_id] = amt
            text += f"🧪 В колбе: **{name}**\nВыбрано штук: **{amt}** из {max_amt}"
            ctrl_btns = InlineKeyboardBuilder()
            ctrl_btns.button(text="-5", callback_data="retort_amt:-5")
            ctrl_btns.button(text="-1", callback_data="retort_amt:-1")
            ctrl_btns.button(text="+1", callback_data="retort_amt:1")
            ctrl_btns.button(text="+5", callback_data="retort_amt:5")
            ctrl_btns.button(text="Максимум", callback_data="retort_amt:max")
            ctrl_btns.button(text=f"🧪 ЗАПУСТИТЬ РЕТОРТУ ({amt})", callback_data="retort_start_run")
            ctrl_btns.button(text="🗑 Очистить колбу", callback_data="retort_clear")
            ctrl_btns.adjust(4, 1, 1)
            builder.attach(ctrl_btns)

        inv_btns = InlineKeyboardBuilder()
        rev_recipes = get_reverse_recipes()
        known = get_known_items(player)
        
        visible_items = [(k, v) for k, v in inventory.items() if v > 0 and k in rev_recipes and k in known]
        
        if not visible_items:
            text += "\n\n_У вас нет изученных сложных элементов для разложения._"
        else:
            text += "\n\n🎒 **Доступно для разложения:**"
            BASIC_ELEMENTS = ["w", "f", "e", "a"]
            
            basic_items = [(k, v) for k, v in visible_items if k in BASIC_ELEMENTS]
            if basic_items:
                basic_row = []
                for k, v in basic_items:
                    emoji = get_el_emoji(k)
                    basic_row.append(types.InlineKeyboardButton(text=f"{emoji} {v}", callback_data=f"retort_add:{k}"))
                inv_btns.row(*basic_row)

            other_items = [(k, v) for k, v in visible_items if k not in BASIC_ELEMENTS]
            other_items.sort(key=lambda x: get_el_name(x[0]))
            
            items_per_page = grid_size * ROWS_PER_PAGE
            total_pages = max(1, (len(other_items) + items_per_page - 1) // items_per_page)
            if page >= total_pages: page = max(0, total_pages - 1)
            
            start_idx = page * items_per_page
            page_items = other_items[start_idx : start_idx + items_per_page]
            
            row = []
            for code, amount in page_items:
                name = get_el_name(code)
                if grid_size == 4:
                    btn_text = f"{name} {amount}"
                else:
                    emoji = get_el_emoji(code)
                    btn_text = f"{emoji} {name} {amount}"
                    
                row.append(types.InlineKeyboardButton(text=btn_text, callback_data=f"retort_add:{code}"))
                if len(row) == grid_size:
                    inv_btns.row(*row)
                    row = []
            if row:
                inv_btns.row(*row)
                
            builder.attach(inv_btns)

            if total_pages > 1:
                nav_row = []
                if page > 0: nav_row.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"retort_page:{page-1}"))
                if page < total_pages - 1: nav_row.append(types.InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"retort_page:{page+1}"))
                if nav_row:
                    paginator = InlineKeyboardBuilder()
                    paginator.row(*nav_row)
                    builder.attach(paginator)

    nav = InlineKeyboardBuilder()
    nav.button(text="🔙 В Лабораторию", callback_data="lab_open")
    nav.adjust(1)
    builder.attach(nav)
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@router.callback_query(F.data.startswith("retort_page:"))
async def retort_change_page(callback: types.CallbackQuery):
    retort_pages[callback.from_user.id] = int(callback.data.split(":")[1])
    await render_retort(callback)

@router.callback_query(F.data.startswith("retort_add:"))
async def retort_add(callback: types.CallbackQuery):
    item = callback.data.split(":")[1]
    uid = callback.from_user.id
    if active_retort_mixes.get(uid, []):
        return await callback.answer("Колба уже занята!", show_alert=True)
    active_retort_mixes[uid] = [item]
    await render_retort(callback)

@router.callback_query(F.data == "retort_clear")
async def retort_clear(callback: types.CallbackQuery):
    active_retort_mixes.pop(callback.from_user.id, None)
    retort_amounts.pop(callback.from_user.id, None)
    await render_retort(callback)

@router.callback_query(F.data.startswith("retort_amt:"))
async def retort_change_amt(callback: types.CallbackQuery):
    uid = callback.from_user.id
    val = callback.data.split(":")[1]
    current = retort_amounts.get(uid, 1)
    if val == "max":
        retort_amounts[uid] = 999999
    else:
        retort_amounts[uid] = current + int(val)
    await render_retort(callback)

@router.callback_query(F.data == "retort_start_run")
async def retort_start_run(callback: types.CallbackQuery):
    uid = callback.from_user.id
    mix = active_retort_mixes.get(uid, [])
    
    if not mix: return await callback.answer("Колба пуста!")
    
    item = mix[0]
    player = await db.alchemy_players.find_one({"user_id": uid})
    inv = player.get("inventory", {})
    max_amt = inv.get(item, 0)
    
    if max_amt <= 0:
        active_retort_mixes.pop(uid, None)
        return await callback.answer("Предмет пропал!", show_alert=True)
        
    amt = retort_amounts.get(uid, 1)
    if amt > max_amt:
        amt = max_amt
    if amt < 1:
        amt = 1
    
    rev_recipes = get_reverse_recipes()
    known = get_known_items(player)
    
    results = decompose_item(item, amt, known, rev_recipes)
    
    inv[item] -= amt
    duration = RETORT_BASE_TIME + (amt * RETORT_TIME_PER_ITEM)
    
    process = {
        "yields": results,
        "finish_time": int(time.time()) + duration
    }
    
    active_retort_mixes.pop(uid, None)
    retort_amounts.pop(uid, None)
    await db.alchemy_players.update_one({"user_id": uid}, {"$set": {"inventory": inv, "retort_process": process}})
    await callback.answer(f"🧪 Анализ {amt} шт. запущен!")
    await render_retort(callback)

@router.callback_query(F.data == "retort_claim")
async def retort_claim(callback: types.CallbackQuery):
    uid = callback.from_user.id
    player = await db.alchemy_players.find_one({"user_id": uid})
    process = player.get("retort_process", None)
    
    if not process or int(time.time()) < process["finish_time"]:
        return await callback.answer("Еще не готово!")
    
    inv = player.get("inventory", {})
    yields = process.get("yields", {})
    
    from handlers.alc_core import STORAGE_CAPACITY, get_used_slots
    max_slots = STORAGE_CAPACITY.get(player.get("buildings", {}).get("storage", 0), 20)
    
    needed_slots = sum(1 for k in yields.keys() if k not in inv)
    if get_used_slots(inv) + needed_slots > max_slots:
        return await callback.answer("На складе нет места для извлечения фракций!", show_alert=True)
        
    claimed_text = []
    for k, v in yields.items():
        inv[k] = inv.get(k, 0) + v
        claimed_text.append(f"{ELEMENTS.get(k, k)}: {v}")
        
    await db.alchemy_players.update_one(
        {"user_id": uid},
        {"$set": {"inventory": inv}, "$unset": {"retort_process": ""}}
    )
    
    msg = "📦 Извлечено:\n" + "\n".join(claimed_text)
    await callback.answer(msg, show_alert=True)
    await render_retort(callback)
    
