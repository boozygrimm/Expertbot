import time
from aiogram.exceptions import TelegramBadRequest
from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database import db
from config import ADMIN_ID
from handlers.alc_fighters import create_fighter, get_barracks_capacity

# Импортируем нашу базу рецептов
from handlers.alc_recipes import BATTLE_UNITS, ELEMENTS, RECIPES, is_battle_unit

router = Router()

# Временное хранилище (в памяти) для текущего замеса
# Формат: { 123456789: ["w", "e"] }
active_mixes = {}
lab_pages = {} # Хранит текущую страницу пользователя: {user_id: page_int}

ROWS_PER_PAGE = 15 # <-- МЕНЯЙ КОЛИЧЕСТВО РЯДОВ НА СТРАНИЦЕ ЗДЕСЬ

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


def normalize_recipe_name(value: str) -> str:
    normalized = value.strip().lower().replace("ё", "е")
    return " ".join(normalized.split())


def split_long_message(lines: list[str], limit: int = 3500) -> list[str]:
    messages = []
    current = []
    current_len = 0

    for line in lines:
        extra = len(line) + 1
        if current and current_len + extra > limit:
            messages.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += extra

    if current:
        messages.append("\n".join(current))
    return messages


def build_recipe_reference() -> list[tuple[str, tuple[str, str], str]]:
    entries = []
    for ingredients, result in RECIPES.items():
        entries.append((get_el_name(result), ingredients, result))
    entries.sort(key=lambda entry: normalize_recipe_name(entry[0]))
    return entries


def find_recipe_by_query(query: str) -> tuple[tuple[str, str], str] | None:
    query_norm = normalize_recipe_name(query)
    for _, ingredients, result in build_recipe_reference():
        result_name = normalize_recipe_name(get_el_name(result))
        if query_norm in {result_name, normalize_recipe_name(result)}:
            return ingredients, result
    return None


HIDDEN_ITEMS = ["base_crystal", "active_base"]
USE_ITEMS = ["pack_s", "pack_m", "pack_l", "pack_xl", "recipe_scroll"]


def get_barracks_lock_text(result: str, player: dict) -> str | None:
    if not is_battle_unit(result):
        return None

    required_level = BATTLE_UNITS[result]["barracks_level"]
    barracks_level = player.get("buildings", {}).get("barracks", 0)
    if barracks_level >= required_level:
        return None

    return (
        f"🎖 Для создания {ELEMENTS.get(result, result)} нужны Казармы {required_level} ур. "
        f"Здание открывает доступ, но рецепт все равно придется найти в Лаборатории."
    )


def get_barracks_capacity_error(result: str, player: dict, amount: int = 1) -> str | None:
    if not is_battle_unit(result):
        return None
    capacity = get_barracks_capacity(player.get("buildings", {}).get("barracks", 0))
    current = len([fighter for fighter in player.get("fighters", []) if fighter.get("status") != "dead"])
    if current + amount > capacity:
        return f"🎖 В Казармах нет места. Лимит личного состава: {current}/{capacity}."
    return None


@router.message(Command("шпора"))
async def cmd_recipe_cheatsheet(message: types.Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return

    query = (command.args or "").strip()

    if not query:
        if message.chat.type != "private":
            return await message.reply("ℹ️ Полная шпора доступна только в ЛС бота.")

        lines = ["📚 **Шпора по алхимии**", ""]
        for idx, (result_name, ingredients, result) in enumerate(build_recipe_reference(), start=1):
            ing1 = ELEMENTS.get(ingredients[0], ingredients[0])
            ing2 = ELEMENTS.get(ingredients[1], ingredients[1])
            res = ELEMENTS.get(result, result)
            lines.append(f"{idx}. {res} = {ing1} + {ing2}")

        for chunk in split_long_message(lines):
            await message.answer(chunk, parse_mode="Markdown")
        return

    match = find_recipe_by_query(query)
    if not match:
        return await message.reply("❌ Рецепт не найден. Используй точное русское имя результата, например: `/шпора огнемеч`", parse_mode="Markdown")

    ingredients, result = match
    ing1 = ELEMENTS.get(ingredients[0], ingredients[0])
    ing2 = ELEMENTS.get(ingredients[1], ingredients[1])
    res = ELEMENTS.get(result, result)
    text = f"📖 **Шпора**\n\n{res} = {ing1} + {ing2}"
    await message.reply(text, parse_mode="Markdown")

# =====================================================================
# 1. ГЛАВНАЯ ФУНКЦИЯ ОТРИСОВКИ МЕНЮ ЛАБОРАТОРИИ
# =====================================================================
async def render_lab_window(message_or_query, user_id: int):
    player = await db.alchemy_players.find_one({"user_id": user_id})
    if not player:
        text = "⚠️ Профиль алхимика не найден. Сначала сходи в поход и активируй базу."
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 В Штаб", callback_data="alc:base_return")
        builder.adjust(1)
        if isinstance(message_or_query, types.Message):
            await message_or_query.answer(text, reply_markup=builder.as_markup())
        else:
            await message_or_query.message.edit_text(text, reply_markup=builder.as_markup())
            await message_or_query.answer()
        return

    inventory = player.get("inventory", {})
    if not isinstance(inventory, dict):
        inventory = {}
    grid_size = player.get("inv_grid", 3)
    last_craft = player.get("last_craft", [])
    lab_lvl = player.get("buildings", {}).get("lab", 1)
    current_mix = active_mixes.get(user_id, [])
    page = lab_pages.get(user_id, 0)
    
    text = (
        "⚗️ **ЛАБОРАТОРИЯ**\n"
        "💡 Справка: сначала смешивай базовые стихии, потом простые материалы, а книга рецептов всегда возвращает назад в лабораторию.\n\n"
    )
    if int(player.get("tutorial_step", 0) or 0) == 2:
        text += (
            "🌍 Для первого биома тебе пригодятся слои под основу, среду, энергию и жизнь. Смотри чертёж в модуле Вселенная и докручивай нужные заготовки здесь."
            " Если хочешь спокойный старт, Болото + Камень + Мох выглядят подозрительно разумно.\n\n"
        )
    
    if not current_mix:
        text += "🧪 В котле пусто. Выбери **два** ингредиента из инвентаря.\n"
    else:
        names = [ELEMENTS.get(i, i) for i in current_mix]
        text += f"♨️ В котле бурлит: **{' + '.join(names)}**\n"
        if len(current_mix) == 1:
            text += "⏳ Жду второй ингредиент..."
        else:
            text += "✨ Готово к смешиванию!"

    builder = InlineKeyboardBuilder()

    # --- ПАНЕЛЬ УПРАВЛЕНИЯ КОТЛОМ ---
    ctrl_btns = InlineKeyboardBuilder()
    if len(current_mix) == 2:
        ctrl_btns.button(text="🌀 СМЕШАТЬ!", callback_data="lab_mix")
    if len(current_mix) > 0:
        ctrl_btns.button(text="🗑 Очистить котел", callback_data="lab_clear")
    
    ctrl_btns.adjust(1)
    builder.attach(ctrl_btns)

    # --- ПАНЕЛЬ МАССОВОГО ПОВТОРА ---
    if not current_mix and last_craft and len(last_craft) == 2:
        i1, i2 = last_craft
        has_resources = False
        if i1 == i2 and inventory.get(i1, 0) >= 2:
            has_resources = True
        elif i1 != i2 and inventory.get(i1, 0) >= 1 and inventory.get(i2, 0) >= 1:
            has_resources = True

        if has_resources:
            rep_text = f"🔁 Повтор: {ELEMENTS.get(i1, i1)} + {ELEMENTS.get(i2, i2)}"
            rep_btns = InlineKeyboardBuilder()
            rep_btns.button(text=rep_text, callback_data="dummy_btn")
            rep_btns.button(text="x1", callback_data="lab_repeat:1")
            rep_btns.button(text="x5", callback_data="lab_repeat:5")
            rep_btns.button(text="Max", callback_data="lab_repeat:max")
            rep_btns.adjust(1, 3) 
            builder.attach(rep_btns)

    # --- ИНВЕНТАРЬ (БАЗОВЫЕ ЭЛЕМЕНТЫ + АЛФАВИТ + СЕТКА) ---
    visible_items = [(k, v) for k, v in inventory.items() if v > 0 and k not in HIDDEN_ITEMS and k != "recipe_scroll" and k != "pack_xl"]
    
    if not visible_items:
        text += "\n\n🎒 Твой инвентарь пуст."
    else:
        text += f"\n\n🎒 **Выбери ресурсы:**"
        inv_btns = InlineKeyboardBuilder()
        BASIC_ELEMENTS = ["w", "f", "e", "a"]
        
        # 1. Сначала всегда 4 базовых элемента (если они есть)
        basic_items = [(k, v) for k, v in visible_items if k in BASIC_ELEMENTS]
        if basic_items:
            basic_row = []
            for k, v in basic_items:
                emoji = get_el_emoji(k)
                basic_row.append(types.InlineKeyboardButton(text=f"{emoji} {v}", callback_data=f"lab_add:{k}"))
            inv_btns.row(*basic_row)

        # 2. Остальные предметы
        other_items = [(k, v) for k, v in visible_items if k not in BASIC_ELEMENTS]
        other_items.sort(key=lambda x: get_el_name(x[0])) # Сортировка по алфавиту
        
        items_per_page = grid_size * ROWS_PER_PAGE
        total_pages = max(1, (len(other_items) + items_per_page - 1) // items_per_page)
        
        if page >= total_pages: page = max(0, total_pages - 1)
        
        start_idx = page * items_per_page
        page_items = other_items[start_idx : start_idx + items_per_page]
        
        row = []
        for code, amount in page_items:
            name = get_el_name(code)
            if grid_size == 4:
                btn_text = f"{name} {amount}" # Без эмодзи
            else:
                emoji = get_el_emoji(code)
                btn_text = f"{emoji} {name} {amount}" # С эмодзи
                
            row.append(types.InlineKeyboardButton(text=btn_text, callback_data=f"lab_add:{code}"))
            
            if len(row) == grid_size:
                inv_btns.row(*row)
                row = []
        if row:
            inv_btns.row(*row)
            
        builder.attach(inv_btns)

        # Пагинация
        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"lab_page:{page-1}"))
            if page < total_pages - 1:
                nav_row.append(types.InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"lab_page:{page+1}"))
            if nav_row:
                paginator = InlineKeyboardBuilder()
                paginator.row(*nav_row)
                builder.attach(paginator)

    # --- НАСТРОЙКИ СЕТКИ И НАВИГАЦИЯ ---
    nav_btns = InlineKeyboardBuilder()
    next_grid = 3 if grid_size == 2 else 4 if grid_size == 3 else 2
    nav_btns.button(text=f"⚙️ Сетка: {grid_size} в ряд (Жми для {next_grid})", callback_data=f"lab_set_grid:{next_grid}")
    
    if lab_lvl >= 2: nav_btns.button(text="🔥 Открыть Тигель", callback_data="crucible_open")
    else: nav_btns.button(text="🔒 Тигель (Нужен 2 ур. Лаборатории)", callback_data="dummy_btn")
        
    if lab_lvl >= 3: nav_btns.button(text="🧪 Открыть Реторту", callback_data="retort_open")
    else: nav_btns.button(text="🔒 Реторта (Нужен 3 ур. Лаборатории)", callback_data="dummy_btn")

    nav_btns.button(text="📖 Книга рецептов", callback_data="lab_recipes")
    nav_btns.button(text="🔙 В Штаб", callback_data="alc:base_return")
    nav_btns.adjust(1)
    builder.attach(nav_btns)

    markup = builder.as_markup()
    if isinstance(message_or_query, types.Message):
        await message_or_query.answer(text, reply_markup=markup, parse_mode="Markdown")
    else:
        try:
            await message_or_query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        except TelegramBadRequest:
            pass
        await message_or_query.answer()

# =====================================================================
# 2. ХЭНДЛЕРЫ ЛАБОРАТОРИИ
# =====================================================================

@router.callback_query(F.data == "lab_open")
async def open_lab_main(callback: types.CallbackQuery):
    await render_lab_window(callback, callback.from_user.id)

@router.callback_query(F.data == "dummy_btn")
async def dummy_answer(callback: types.CallbackQuery):
    await callback.answer()

@router.callback_query(F.data.startswith("lab_set_grid:"))
async def lab_change_grid(callback: types.CallbackQuery):
    new_grid = int(callback.data.split(":")[1])
    await db.alchemy_players.update_one({"user_id": callback.from_user.id}, {"$set": {"inv_grid": new_grid}})
    await render_lab_window(callback, callback.from_user.id)

@router.callback_query(F.data.startswith("lab_add:"))
async def lab_add_item(callback: types.CallbackQuery):
    item = callback.data.split(":")[1]
    uid = callback.from_user.id
    
    mix = active_mixes.get(uid, [])
    if len(mix) >= 2:
        return await callback.answer("Котел полон! Нажми 'Смешать' или 'Очистить'.", show_alert=True)
        
    player = await db.alchemy_players.find_one({"user_id": uid})
    inventory = player.get("inventory", {})
    
    # Запрещаем класть элемент, если он закончился
    if inventory.get(item, 0) <= mix.count(item):
        return await callback.answer("У тебя больше нет этого ингредиента!", show_alert=True)
        
    mix.append(item)
    active_mixes[uid] = mix
    await render_lab_window(callback, uid)

@router.callback_query(F.data == "lab_clear")
async def lab_clear_mix(callback: types.CallbackQuery):
    active_mixes.pop(callback.from_user.id, None)
    await render_lab_window(callback, callback.from_user.id)

@router.callback_query(F.data.startswith("lab_page:"))
async def lab_change_page(callback: types.CallbackQuery):
    page = int(callback.data.split(":")[1])
    lab_pages[callback.from_user.id] = page
    await render_lab_window(callback, callback.from_user.id)
    
# =====================================================================
# 3. ЛОГИКА ЕДИНИЧНОГО КРАФТА
# =====================================================================
@router.callback_query(F.data == "lab_mix")
async def lab_mix_items(callback: types.CallbackQuery):
    uid = callback.from_user.id
    mix = active_mixes.get(uid, [])
    
    if len(mix) != 2:
        return await callback.answer("Нужно два ингредиента!", show_alert=True)
        
    player = await db.alchemy_players.find_one({"user_id": uid})
    inv = player.get("inventory", {})
    i1, i2 = mix[0], mix[1]
    
    # Финальная проверка на читы/отсутствие ресурсов
    if inv.get(i1, 0) < mix.count(i1) or inv.get(i2, 0) < mix.count(i2):
        active_mixes.pop(uid, None)
        await callback.answer("Не хватает ресурсов. Котел очищен.", show_alert=True)
        return await render_lab_window(callback, uid)

    # Поиск результата
    result = None
    for (r1, r2), res in RECIPES.items():
        if (i1 == r1 and i2 == r2) or (i1 == r2 and i2 == r1):
            result = res
            break
            
    if not result:
        active_mixes.pop(uid, None)
        # Запоминаем неудачный крафт для кнопки "Повтор", но инвентарь НЕ трогаем
        await db.alchemy_players.update_one(
            {"user_id": uid}, 
            {"$set": {"last_craft": [i1, i2]}}
        )
        await callback.answer("💨 Пшик... Пошла вонь. Попробуй другую комбинацию.", show_alert=True)
        return await render_lab_window(callback, uid)

    barracks_lock = get_barracks_lock_text(result, player)
    if barracks_lock:
        active_mixes.pop(uid, None)
        await callback.answer(barracks_lock, show_alert=True)
        return await render_lab_window(callback, uid)
    barracks_cap = get_barracks_capacity_error(result, player)
    if barracks_cap:
        active_mixes.pop(uid, None)
        await callback.answer(barracks_cap, show_alert=True)
        return await render_lab_window(callback, uid)
        
    # Успешный крафт
    inv[i1] -= 1
    inv[i2] -= 1
    active_mixes.pop(uid, None)
    update_fields = {"inventory": inv}
    if is_battle_unit(result):
        fighters = player.get("fighters", [])
        fighters.append(create_fighter(result, crafted_tier3=BATTLE_UNITS[result]["tier"] >= 3))
        update_fields["fighters"] = fighters
    else:
        inv[result] = inv.get(result, 0) + 1
    
    disc = player.get("discovered", [])
    if result not in disc:
        disc.append(result)
    update_fields["discovered"] = disc
        
    # Обновляем инвентарь и сохраняем последний рецепт для кнопки "Повтор"
    update_fields["last_craft"] = [i1, i2]
    await db.alchemy_players.update_one({"user_id": uid}, {"$set": update_fields})
    
    res_name = ELEMENTS.get(result, result)
    if is_battle_unit(result):
        await callback.answer(f"🎉 Успех! В Казармы прибыл новый боец: {res_name}", show_alert=True)
    else:
        await callback.answer(f"🎉 Успех! Получено: {res_name}", show_alert=True)
    await render_lab_window(callback, uid)


# =====================================================================
# 4. ЛОГИКА МАССОВОГО КРАФТА (ПОВТОР)
# =====================================================================
@router.callback_query(F.data.startswith("lab_repeat:"))
async def lab_repeat_craft(callback: types.CallbackQuery):
    uid = callback.from_user.id
    amount_str = callback.data.split(":")[1]
    
    player = await db.alchemy_players.find_one({"user_id": uid})
    inv = player.get("inventory", {})
    last_craft = player.get("last_craft", [])
    
    if not last_craft or len(last_craft) != 2:
        return await callback.answer("Нет данных о прошлом крафте.", show_alert=True)
        
    i1, i2 = last_craft

    # Вычисляем максимальное возможное количество итераций
    if i1 == i2:
        max_possible = inv.get(i1, 0) // 2
    else:
        max_possible = min(inv.get(i1, 0), inv.get(i2, 0))
        
    if max_possible == 0:
        return await callback.answer("Не хватает ресурсов для повтора!", show_alert=True)

    # Определяем сколько реально делаем
    if amount_str == "max":
        iters = max_possible
    else:
        iters = min(int(amount_str), max_possible)

    # Проверяем, существует ли вообще рецепт (чтобы не сжечь ресы массово зря)
    result = None
    for (r1, r2), res in RECIPES.items():
        if (i1 == r1 and i2 == r2) or (i1 == r2 and i2 == r1):
            result = res
            break
            
    if not result:
        # Ресурсы не отнимаем, просто прерываем масс-крафт
        await callback.answer("💨 Пшик... Этот рецепт не работает. Ресурсы целы.", show_alert=True)
        return await render_lab_window(callback, uid)

    barracks_lock = get_barracks_lock_text(result, player)
    if barracks_lock:
        await callback.answer(barracks_lock, show_alert=True)
        return await render_lab_window(callback, uid)
    barracks_cap = get_barracks_capacity_error(result, player, iters)
    if barracks_cap:
        await callback.answer(barracks_cap, show_alert=True)
        return await render_lab_window(callback, uid)

    # Проверка лимитов склада (для новых предметов)
    from handlers.alc_core import STORAGE_CAPACITY, get_used_slots
    storage_lvl = player.get("buildings", {}).get("storage", 0)
    max_slots = STORAGE_CAPACITY.get(storage_lvl, 20)
    
    if result not in inv and get_used_slots(inv) >= max_slots:
        return await callback.answer("Склад полон! Нет места для нового элемента.", show_alert=True)

    # Выполняем массовый крафт (БЕЗ циклов, математически)
    if i1 == i2:
        inv[i1] -= iters * 2
    else:
        inv[i1] -= iters
        inv[i2] -= iters
        
    disc = player.get("discovered", [])
    if result not in disc:
        disc.append(result)

    update_fields = {"inventory": inv, "discovered": disc}
    if is_battle_unit(result):
        fighters = player.get("fighters", [])
        for _ in range(iters):
            fighters.append(create_fighter(result, crafted_tier3=BATTLE_UNITS[result]["tier"] >= 3))
        update_fields["fighters"] = fighters
    else:
        inv[result] = inv.get(result, 0) + iters

    await db.alchemy_players.update_one({"user_id": uid}, {"$set": update_fields})

    res_name = ELEMENTS.get(result, result)
    await callback.answer(f"🎉 Массовый крафт! Создано: {res_name} (x{iters})", show_alert=True)
    await render_lab_window(callback, uid)

# =====================================================================
# 5. КНИГА РЕЦЕПТОВ
# =====================================================================

# Временное хранилище (в памяти) для текущей страницы книги рецептов
# Формат: { user_id: page_int }
recipe_pages = {} 
RECIPES_PER_PAGE = 25 # Количество рецептов на одной странице

async def build_recipe_book_content(user_id: int):
    player = await db.alchemy_players.find_one({"user_id": user_id})
    if not player: 
        player = {}
        
    inventory = player.get("inventory", {})
    discovered = set(player.get("discovered", [])) 
    page = recipe_pages.get(user_id, 0)
    
    known_recipes = []
    
    # Сортируем рецепты по русскому алфавиту (по названию результата)
    sorted_recipes = sorted(
        RECIPES.items(),
        key=lambda recipe: normalize_recipe_name(get_el_name(recipe[1])),
    )
    
    # Собираем все открытые рецепты
    for ingredients, result in sorted_recipes:
        if result in inventory or result in discovered:
            i1 = ELEMENTS.get(ingredients[0], ingredients[0])
            i2 = ELEMENTS.get(ingredients[1], ingredients[1])
            res = ELEMENTS.get(result, result)
            known_recipes.append(f"▪️ {i1} + {i2} = *{res}*")
            
    builder = InlineKeyboardBuilder()

    if not known_recipes:
        text = "📖 **КНИГА РЕЦЕПТОВ**\n\nТы пока не открыл ни одного рецепта. Смешивай базовые элементы!\n\n💡 Не знаешь рецепт? Спроси эксперта! Пиши **Экс, шпора**."
        builder.button(text="❌ Закрыть книгу", callback_data="lab_book_close")
        return text, builder.as_markup()

    total_pages = (len(known_recipes) + RECIPES_PER_PAGE - 1) // RECIPES_PER_PAGE
    
    if page >= total_pages:
        page = max(0, total_pages - 1)
        recipe_pages[user_id] = page

    start_idx = page * RECIPES_PER_PAGE
    page_recipes = known_recipes[start_idx : start_idx + RECIPES_PER_PAGE]

    text = f"📖 **КНИГА РЕЦЕПТОВ** (Страница {page + 1} из {total_pages})\n\n"
    text += "💡 Не знаешь рецепт? Спроси эксперта! Пиши **Экс, шпора**.\n\n"
    text += "\n".join(page_recipes)

    nav_row = []
    
    if page > 0:
        nav_row.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"recipe_page:{page-1}"))
    else:
        nav_row.append(types.InlineKeyboardButton(text=" ", callback_data="dummy_btn"))

    nav_row.append(types.InlineKeyboardButton(text="❌ Закрыть", callback_data="lab_book_close"))

    if page < total_pages - 1:
        nav_row.append(types.InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"recipe_page:{page+1}"))
    else:
        nav_row.append(types.InlineKeyboardButton(text=" ", callback_data="dummy_btn"))

    builder.row(*nav_row)
    return text, builder.as_markup()

# Хэндлер открытия книги из меню лаборатории
# ==========================================
# ХЭНДЛЕРЫ КНИГИ РЕЦЕПТОВ
# ==========================================

# 1. Ловим нажатие на кнопку "Книга рецептов" в Лаборатории
@router.callback_query(F.data == "lab_recipes")
async def open_recipe_book(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    # Сбрасываем страницу на первую (0) при каждом новом открытии книги
    recipe_pages[user_id] = 0 
    
    # Получаем текст и кнопки из нашей обновленной функции
    text, markup = await build_recipe_book_content(user_id)
    
    await callback.message.answer(text, reply_markup=markup, parse_mode="Markdown")
    await callback.answer("Книга открыта.")

# 2. Ловим нажатие на кнопку "Закрыть" (крестик) внутри самой книги
@router.callback_query(F.data == "lab_book_close")
async def close_recipe_book(callback: types.CallbackQuery):
    await callback.message.delete()
    await callback.answer()
    
@router.callback_query(F.data.startswith("recipe_page:"))
async def turn_recipe_page(callback: types.CallbackQuery):
    uid = callback.from_user.id
    page = int(callback.data.split(":")[1])
    
    # Сразу отсекаем лишние запросы, если страница не поменялась
    if recipe_pages.get(uid) == page:
        return await callback.answer()
        
    recipe_pages[uid] = page
    text, markup = await build_recipe_book_content(uid)
    
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    except TelegramBadRequest:
        pass # Глушим краш, если контент не изменился
        
    await callback.answer()
