import asyncio
import html
import time

from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import CommandStart, CommandObject, Command
from database import db
from handlers.alc_recipes import ELEMENTS
from handlers.pole_chudes import delete_after_delay
from handlers.alc_achievements import ACHIEVEMENTS
from handlers.alc_core import get_or_create_player, run_alchemy_intro

router = Router()

ALCHEMY_ALPHA_VERSION = "0.8.2-alpha"
ALCHEMY_CHANGELOG = [
    ("0.8.2-alpha", [
        "Редактор Наследий переделан в пошаговый диалог с кнопками и подсказками вместо невнятного набора команд.",
        "Третье стартовое Наследие заменено на 'Хроники приматов и первичного бульона'.",
        "Активация любого Наследия теперь стоит 50 энергии.",
        "Обрывки лора стали выпадать заметно стабильнее: экспедиции теперь почти гарантированно закрывают дневной лимит, а квесты помогают добрать остаток.",
        "Подтянуты свежие балансные правки из alc_build.",
        "Главный штаб ужат в статусную сводку: верхний ряд теперь состоит из эмодзи-кнопок по доступным модулям.",
        "Иконка штаба стала зависеть от уровня Форта, а в верхний ряд добавлена кнопка справки по эмодзи.",
        "Активация паков, свитков и кристалла перенесена в Склад, чтобы главное окно не смешивало навигацию и действия.",
        "Цвета статусов в штабе унифицированы: красный - пусто или не запущено, синий - процесс идет, зеленый - готово или можно забирать.",
    ]),
    ("0.8.1-alpha", [
        "Исправлено окно 'Альфа и изменения': список версий снова открывается без краша.",
        "Утренний отчет снабжения и Наследия теперь приходит одним отдельным окном с кнопкой закрытия.",
        "Отчет о встрече экспедиции получил кнопку 'Закрыть'.",
        "Бойцы больше не дезертируют сразу из-за нехватки энергии: сначала остаются без отдыха и теряют 2 XP, а уходят только на нуле опыта.",
    ]),
    ("0.8.0-alpha", [
        "Добавлена История миров: коллекции лора, стенды Наследия и постоянные бонусы для базы.",
        "Старые профили мягко мигрируют на новую систему, а новые игроки получают обновленный старт через ЛС.",
        "Появились разовые дары Наследий, отставка бойцов и админ-редактор /lore_edit для наполнения мира.",
        "Обрывки лора теперь могут выпадать из квестов и экспедиций с дневным лимитом.",
        "В базовые сиды добавлены первые истории мира, так что модуль уже живет не как заготовка, а как почти бета.",
    ]),
    ("0.5.0-alpha", [
        "Появилось меню Наследия: коллекции лора, стенды, чтение хроник и постоянные бафы.",
        "История миров теперь мягко мигрирует старые профили и сразу доступна новым игрокам.",
        "Добавлены разовые дары Наследий, отставка бойцов и новая стартовая сцена в ЛС.",
    ]),
    ("0.4.4-alpha", [
        "Доска объявлений больше не выдает задания на боевых юнитов, которые нельзя сдать после обновления.",
        "Старые зависшие задания на таких юнитов автоматически заменяются при открытии доски.",
        "Исправлен ввод имени бойца: обычные сообщения больше не перехватываются вне режима переименования.",
        "Черновик экспедиции теперь очищается от недоступных бойцов, чтобы состав не застревал после изменений ростера.",
    ]),
    ("0.4.3-alpha", [
        "Персональные бойцы в Казармах: у каждого теперь свой XP, бодрость, имя и история походов.",
        "Рейды и экспедиции переведены на личный состав вместо безликих пачек.",
        "Добавлены Зал славы, апгрейды бойцов и безопасный переход со старых сохранений.",
        "Появилась пагинация в длинных окнах Казарм и Форта.",
    ]),
    ("0.4.2-alpha", [
        "Экспедиции получили отдельный интерфейс, события по маршруту и подробные отчеты.",
        "Форт, ловушки и пленные стали частью обороны базы.",
    ]),
    ("0.4.1-alpha", [
        "Казармы, рейды и военные рецепты были вынесены в отдельный контур развития.",
        "Появились достижения и доска объявлений.",
    ]),
    ("0.4.0-alpha", [
        "База алхимика оформилась в модульную систему: штаб, стройка, лаборатория, производство.",
    ]),
]

# Системные переменные
HIDDEN_ITEMS = ["base_crystal", "active_base"]
USE_ITEMS = ["pack_s", "pack_m", "pack_l", "pack_xl", "base_crystal", "recipe_scroll"]

base_pages = {}
ROWS_PER_PAGE = 15 #количество рядов на странице 
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


def get_headquarters_icon(fort_level: int) -> str:
    if fort_level >= 3:
        return "🏯"
    if fort_level == 2:
        return "🏰"
    if fort_level == 1:
        return "🏕"
    return "🛖"


def format_process_status(process: dict | None, *, running_text: str, ready_text: str) -> str:
    if not process:
        return "🔴 пусто"
    if int(process.get("finish_time", 0)) > int(time.time()):
        return f"🔵 {running_text}"
    return f"🟢 {ready_text}"


def build_storage_status(player: dict) -> str:
    from handlers.alc_core import STORAGE_CAPACITY

    inventory = player.get("inventory", {})
    storage_lvl = player.get("buildings", {}).get("storage", 0)
    max_slots = STORAGE_CAPACITY.get(storage_lvl, 20)
    used_slots = len([k for k, v in inventory.items() if v > 0 and k not in HIDDEN_ITEMS])
    marker = "🔴" if used_slots >= max_slots else "🟢"
    return f"{marker} {used_slots}/{max_slots}"


def build_heritage_status(player: dict) -> str:
    stands = player.get("active_stands", [])
    slots = len(stands)
    active = sum(1 for stand in stands if stand)
    marker = "🟢" if active else "🔴"
    return f"{marker} {active}/{slots} стендов"


def build_lab_status_lines(player: dict) -> list[str]:
    lab_level = player.get("buildings", {}).get("lab", 0)
    if lab_level <= 0:
        return ["⚗️ Лаборатория: 🔴 не собрана"]

    lines = [f"⚗️ Лаборатория: 🟢 ур. {lab_level}"]
    if lab_level >= 2:
        lines.append(
            "🔥 Тигель: "
            + format_process_status(
                player.get("crucible_process"),
                running_text="переплавляет",
                ready_text="результат готов",
            )
        )
    if lab_level >= 3:
        lines.append(
            "🧪 Реторта: "
            + format_process_status(
                player.get("retort_process"),
                running_text="фракционирует",
                ready_text="фракции готовы",
            )
        )
    return lines


def build_production_status(player: dict) -> str:
    altar_level = player.get("buildings", {}).get("altar", 0)
    if altar_level <= 0:
        return ""

    slots = player.get("production_slots", [])
    max_slots = min(3, max(1, altar_level))
    running = 0
    stopped = 0
    now = int(time.time())
    for slot in slots:
        assigned_at = int(slot.get("assigned_at", 0) or 0)
        expired = assigned_at > 0 and now - assigned_at >= 24 * 3600
        if slot.get("stopped") or expired:
            stopped += 1
        else:
            running += 1

    if running:
        return f"🏭 Производство: 🔵 {running}/{max_slots} в работе"
    if stopped:
        return f"🏭 Производство: 🟢 {stopped}/{max_slots} ждут сбор"
    return f"🏭 Производство: 🔴 {len(slots)}/{max_slots} пусто"


def build_barracks_status(player: dict) -> str:
    barracks_level = player.get("buildings", {}).get("barracks", 0)
    if barracks_level <= 0:
        return ""

    fighters = player.get("fighters", [])
    roster = [fighter for fighter in fighters if fighter.get("status") != "dead"]
    if not roster:
        return "🎖 Казармы: 🔴 пусто"

    ready = len([fighter for fighter in roster if fighter.get("status") == "available" and int(fighter.get("stamina", 0)) > 0])
    tired = len([fighter for fighter in roster if fighter.get("status") == "available" and int(fighter.get("stamina", 0)) <= 0])
    away = len([fighter for fighter in roster if fighter.get("status") in {"expedition", "raid"}])
    marker = "🔵" if away else "🟢"
    return f"🎖 Казармы: {marker} строй {ready} | поход {away} | отдых {tired}"


def build_expeditions_status(player: dict) -> str:
    if player.get("buildings", {}).get("barracks", 0) <= 0:
        return ""

    expeditions = player.get("active_expeditions", [])
    if not expeditions:
        return "🧭 Экспедиции: 🔴 пусто"

    arrived = len([item for item in expeditions if item.get("status") == "arrived"])
    active = len(expeditions) - arrived
    if arrived:
        return f"🧭 Экспедиции: 🟢 ждут {arrived} | в пути {active}"
    return f"🧭 Экспедиции: 🔵 в пути {active}"


def build_fort_status(player: dict) -> str:
    fort_level = player.get("buildings", {}).get("fort", 0)
    if fort_level <= 0:
        return ""

    loaded = sum(1 for value in player.get("fort_defense", {}).values() if value)
    captives = len(player.get("captives", []))
    marker = "🟢" if loaded else "🔴"
    return f"🏰 Форт: {marker} ловушки {loaded} | плен {captives}"


def build_base_summary(player: dict) -> str:
    buildings = player.get("buildings", {})
    fort_level = buildings.get("fort", 0)
    header_icon = get_headquarters_icon(fort_level)

    lines = [
        f"{header_icon} **ГЛАВНЫЙ ШТАБ**",
        f"{player.get('user_name', 'Путник')} | версия `{ALCHEMY_ALPHA_VERSION}`",
        "",
        f"🎒 Склад: {build_storage_status(player)}",
        f"📚 Наследие: {build_heritage_status(player)}",
    ]
    lines.extend(build_lab_status_lines(player))

    optional_lines = [
        build_production_status(player),
        build_barracks_status(player),
        build_expeditions_status(player),
        build_fort_status(player),
    ]
    lines.extend(line for line in optional_lines if line)
    return "\n".join(lines)


def build_base_keyboard(player: dict) -> types.InlineKeyboardMarkup:
    buildings = player.get("buildings", {})
    builder = InlineKeyboardBuilder()

    top_row = [
        types.InlineKeyboardButton(text="📚", callback_data="heritage_open"),
        types.InlineKeyboardButton(text="⚗️", callback_data="lab_open"),
    ]
    if buildings.get("altar", 0) > 0:
        top_row.append(types.InlineKeyboardButton(text="🏭", callback_data="altar_open"))
    top_row.append(types.InlineKeyboardButton(text="🧾", callback_data="alc:changelog"))
    if buildings.get("barracks", 0) > 0:
        top_row.append(types.InlineKeyboardButton(text="🧭", callback_data="expeditions_open"))
    top_row.append(types.InlineKeyboardButton(text="🎒", callback_data="alc:inventory"))
    top_row.append(types.InlineKeyboardButton(text="❓", callback_data="alc:base_help"))
    builder.row(*top_row)

    service_rows: list[types.InlineKeyboardButton] = [
        types.InlineKeyboardButton(text="🏗 Стройка", callback_data="build_open"),
        types.InlineKeyboardButton(text="📜 Квесты", callback_data="quests_open"),
        types.InlineKeyboardButton(text="🏆 Ачивки", callback_data="alc:achievements"),
    ]
    if buildings.get("barracks", 0) > 0:
        service_rows.append(types.InlineKeyboardButton(text="🎖 Казармы", callback_data="barracks_open"))
    if buildings.get("fort", 0) > 0:
        service_rows.append(types.InlineKeyboardButton(text="🏰 Форт", callback_data="fort_open"))

    for idx in range(0, len(service_rows), 2):
        builder.row(*service_rows[idx:idx + 2])
    builder.row(types.InlineKeyboardButton(text="❌ Закрыть", callback_data="alc:base_close"))
    return builder.as_markup()


async def check_achievements(user_id: int, player: dict):
    """Проверяет условия и выдает награды за ачивки при загрузке базы"""
    unlocked = player.get("achievements", [])
    new_unlocked = []
    inv = player.get("inventory", {})
    
    for ach_id, data in ACHIEVEMENTS.items():
        if ach_id not in unlocked:
            if data["condition"](player):
                new_unlocked.append(ach_id)
                unlocked.append(ach_id)
                # Выдача награды
                inv[data["reward_item"]] = inv.get(data["reward_item"], 0) + data["reward_count"]
                
    if new_unlocked:
        # Пишем в БД только если есть изменения
        await db.alchemy_players.update_one(
            {"user_id": user_id},
            {"$set": {"achievements": unlocked, "inventory": inv}}
        )
        
        # Формируем текст уведомления
        msgs = ["🎉 **ВЫ ПОЛУЧИЛИ НОВЫЕ ДОСТИЖЕНИЯ!** 🎉"]
        for ach_id in new_unlocked:
            ach = ACHIEVEMENTS[ach_id]
            r_name = ELEMENTS.get(ach['reward_item'], ach['reward_item'])
            msgs.append(f"🏆 **{ach['name']}**\n_Награда: {r_name} x{ach['reward_count']}_")
        return "\n\n".join(msgs)
    return None


# ==================== ГЛАВНОЕ МЕНЮ БАЗЫ ====================
async def get_base_content(user_id: int):
    player = await db.alchemy_players.find_one({"user_id": user_id})
    if not player:
        return "⚠️ Я тебя не узнаю. Сначала сходи в поход!", None
    player = await get_or_create_player(user_id, player.get("user_name", "Путник"))

    ach_msg = await check_achievements(user_id, player)
    
    if ach_msg:
        player = await db.alchemy_players.find_one({"user_id": user_id})

    text = build_base_summary(player)
    
    if ach_msg:
        text = f"{ach_msg}\n\n" + text

    return text, build_base_keyboard(player)


def build_changelog_text() -> str:
    lines = [
        "🧾 <b>АЛЬФА ИЗМЕНЕНИЙ</b>",
        f"Текущая версия: <b>{html.escape(ALCHEMY_ALPHA_VERSION)}</b>",
        "",
        "Что это значит:",
        "• Альфа - механики ещё двигаются, баланс и интерфейсы могут меняться.",
        "• Если старые сохранения можно перенести без потерь, игра старается сделать это автоматически.",
        "",
        "История ключевых изменений:",
    ]
    for version, notes in ALCHEMY_CHANGELOG:
        lines.append(f"<b>{html.escape(version)}</b>")
        for note in notes:
            lines.append(f"• {html.escape(note)}")
        lines.append("")
    lines.append("Если после обновления что-то ведет себя странно, обычно помогает заново открыть нужное окно базы.")
    return "\n".join(lines)


@router.callback_query(F.data == "alc:changelog")
async def show_changelog(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 В Штаб", callback_data="alc:base_return")
    builder.adjust(1)
    await callback.message.edit_text(build_changelog_text(), reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "alc:base_help")
async def show_base_help(callback: types.CallbackQuery):
    await callback.answer(
        "Эмодзи верхнего ряда:\n"
        "📚 Наследие\n"
        "⚗️ Лаборатория\n"
        "🏭 Производство\n"
        "🧾 Альфа и изменения\n"
        "🧭 Экспедиции\n"
        "🎒 Склад и активация\n"
        "❓ Эта справка\n\n"
        "Цвета статусов:\n"
        "🔴 пусто, не запущено или не собрано\n"
        "🔵 идет работа, модуль занят или отряд в пути\n"
        "🟢 готово, собрано или можно забирать\n\n"
        "Штаб показывает только короткую сводку по базе. Детальные действия открываются из кнопок выше.",
        show_alert=True,
    )


# ==================== ИНВЕНТАРЬ И НАСТРОЙКИ СЕТКИ ====================

@router.callback_query(F.data == "alc:inventory")
async def show_inventory(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    inventory = player.get("inventory", {})
    grid_size = player.get("inv_grid", 3) 
    page = base_pages.get(user_id, 0)
    
    visible_items = [(k, v) for k, v in inventory.items() if v > 0 and k not in HIDDEN_ITEMS and k not in USE_ITEMS]
    active_items = [(code, inventory.get(code, 0)) for code in USE_ITEMS if inventory.get(code, 0) > 0]
    
    storage_lvl = player.get("buildings", {}).get("storage", 0)
    from handlers.alc_core import STORAGE_CAPACITY
    max_slots = STORAGE_CAPACITY.get(storage_lvl, 20)
    
    used_slots = len([k for k, v in inventory.items() if v > 0 and k not in HIDDEN_ITEMS])
    
    text = f"🎒 **ТВОЙ СКЛАД** [{used_slots}/{max_slots} слотов]\n\n"
    if active_items:
        text += "Готово к активации:\n"
        text += "\n".join(f"• {ELEMENTS.get(code, code)} x{amount}" for code, amount in active_items)
        text += "\n\n"
    if not visible_items:
        text += "Ресурсов на хранении пока нет."
    else:
        text += "Ресурсы на складе:"

    builder = InlineKeyboardBuilder()
    if active_items:
        for code, amount in active_items:
            builder.button(text=f"▶️ {ELEMENTS.get(code, code)} x{amount}", callback_data=f"alc_use:{code}")
        builder.adjust(1)

    BASIC_ELEMENTS = ["w", "f", "e", "a"]
    
    basic_items = [(k, v) for k, v in visible_items if k in BASIC_ELEMENTS]
    if basic_items:
        basic_row = []
        for k, v in basic_items:
            emoji = get_el_emoji(k)
            basic_row.append(types.InlineKeyboardButton(text=f"{emoji} {v}", callback_data="dummy_btn"))
        builder.row(*basic_row)

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
            
        row.append(types.InlineKeyboardButton(text=btn_text, callback_data="dummy_btn"))
        if len(row) == grid_size:
            builder.row(*row)
            row = []
    if row:
        builder.row(*row)
    
    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"alc_page:{page-1}"))
        if page < total_pages - 1:
            nav_row.append(types.InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"alc_page:{page+1}"))
        
        if nav_row:
            paginator = InlineKeyboardBuilder()
            paginator.row(*nav_row)
            builder.attach(paginator)
    
    ctrl = InlineKeyboardBuilder()
    next_grid = 3 if grid_size == 2 else 4 if grid_size == 3 else 2
    ctrl.button(text=f"⚙️ Сетка: {grid_size} в ряд (Жми для {next_grid})", callback_data=f"alc:set_grid:{next_grid}")
    ctrl.button(text="🔙 В Штаб", callback_data="alc:base_return")
    ctrl.adjust(1)
    
    builder.attach(ctrl)
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

# НОВЫЙ ХЭНДЛЕР ПАГИНАЦИИ ДЛЯ БАЗЫ (Добавь в alc_base.py)
@router.callback_query(F.data.startswith("alc_page:"))
async def base_change_page(callback: types.CallbackQuery):
    base_pages[callback.from_user.id] = int(callback.data.split(":")[1])
    await show_inventory(callback)

@router.callback_query(F.data == "dummy_btn")
async def dummy_answer(callback: types.CallbackQuery):
    await callback.answer("Это ресурс на складе. Используйте его в Лаборатории или Постройках!", show_alert=False)

@router.callback_query(F.data.startswith("alc:set_grid:"))
async def change_grid(callback: types.CallbackQuery):
    new_grid = int(callback.data.split(":")[2])
    await db.alchemy_players.update_one({"user_id": callback.from_user.id}, {"$set": {"inv_grid": new_grid}})
    await show_inventory(callback)

# ==================== БАЗОВЫЕ ХЭНДЛЕРЫ НАВИГАЦИИ ====================
@router.message(CommandStart(deep_link=True))
async def open_base_menu_start(message: types.Message, command: CommandObject):
    if command.args == "alcintro":
        text, _ = await run_alchemy_intro(message.from_user.id, message.from_user.first_name)
        return await message.answer(text, parse_mode="Markdown")
    if command.args != "alcbase": return
    text, markup = await get_base_content(message.from_user.id)
    await message.answer(text, reply_markup=markup, parse_mode="Markdown")

@router.message(Command("base", "база"))
async def cmd_base(message: types.Message):
    if message.chat.type != "private":
        bot_info = await message.bot.get_me()
        builder = InlineKeyboardBuilder()
        builder.button(text="🏕 Перейти в Штаб", url=f"https://t.me/{bot_info.username}?start=alcbase")
        
        msg = await message.reply("Штаб лучше открывать в личных сообщениях, чтобы не мешать другим!", reply_markup=builder.as_markup())
        
        asyncio.create_task(delete_after_delay(message, 10))
        asyncio.create_task(delete_after_delay(msg, 10))
        return

    text, markup = await get_base_content(message.from_user.id)
    await message.answer(text, reply_markup=markup, parse_mode="Markdown")

@router.callback_query(F.data == "alc:base_return")
async def ret_base(callback: types.CallbackQuery):
    text, markup = await get_base_content(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")

@router.callback_query(F.data == "alc:base_close")
async def close_base_window(callback: types.CallbackQuery):
    await callback.message.delete()
    await callback.answer()

@router.callback_query(F.data.startswith("alc_use:"))
async def handle_use_item(callback: types.CallbackQuery):
    item_code = callback.data.split(":")[1]
    from handlers.alc_core import use_item_logic
    
    result_text, success = await use_item_logic(callback.from_user.id, item_code)
    
    if success:
        await callback.message.answer(result_text, parse_mode="Markdown")
        await show_inventory(callback)
    else:
        await callback.answer(result_text, show_alert=True)
