import asyncio
import html

from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import CommandStart, CommandObject, Command
from database import db
from handlers.alc_recipes import ELEMENTS
from handlers.pole_chudes import delete_after_delay
from handlers.alc_achievements import ACHIEVEMENTS
from handlers.alc_core import get_or_create_player, run_alchemy_intro

router = Router()

ALCHEMY_ALPHA_VERSION = "0.8.1-alpha"
ALCHEMY_CHANGELOG = [
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

    inventory = player.get("inventory", {})
    builder = InlineKeyboardBuilder()
    
    active_found = False
    for code in USE_ITEMS:
        amount = inventory.get(code, 0)
        if amount > 0:
            active_found = True
            builder.button(text=f"▶️ Использовать {ELEMENTS.get(code, code)} ({amount})", callback_data=f"alc_use:{code}")

    if active_found:
        builder.adjust(1)
        
    # === ИЗМЕНЕННЫЙ БЛОК НАВИГАЦИИ ===
    main_btns = InlineKeyboardBuilder()
    main_btns.button(text="🎒 Мой склад", callback_data="alc:inventory")
    main_btns.button(text="🏆 Достижения", callback_data="alc:achievements")
    main_btns.button(text="🧾 Альфа и изменения", callback_data="alc:changelog")
    main_btns.button(text="📜 Доска объявлений", callback_data="quests_open") # <-- НОВАЯ КНОПКА
    main_btns.button(text="📚 Наследие", callback_data="heritage_open")
    main_btns.button(text="⚗️ Лаборатория", callback_data="lab_open")
    main_btns.button(text="🏗 Постройки", callback_data="build_open")
    if player.get("buildings", {}).get("barracks", 0) > 0:
        main_btns.button(text="🎖 Казармы", callback_data="barracks_open")
    if player.get("buildings", {}).get("fort", 0) > 0:
        main_btns.button(text="🏰 Форт", callback_data="fort_open")
    
    if player.get("buildings", {}).get("altar", 0) > 0:
        main_btns.button(text="🏗 Производство", callback_data="altar_open")

    main_btns.button(text="❌ Закрыть", callback_data="alc:base_close")
    
    # Настраиваем сетку: 2 сверху, 2 в центре, и остальные по одной
    main_btns.adjust(2, 2, 2, 2, 1) 
    builder.attach(main_btns)
    # ==================================
            
    text = f"🏕 **ГЛАВНЫЙ ШТАБ**\nДобро пожаловать, {player.get('user_name', 'Путник')}!\n"
    text += f"\nВерсия: **{ALCHEMY_ALPHA_VERSION}**"
    text += "\n_Тут лежат только активные предметы. Твои ресурсы отсортированы на Складе._"
    
    if ach_msg:
        text = f"{ach_msg}\n\n" + text

    return text, builder.as_markup()


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


# ==================== ИНВЕНТАРЬ И НАСТРОЙКИ СЕТКИ ====================

@router.callback_query(F.data == "alc:inventory")
async def show_inventory(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    inventory = player.get("inventory", {})
    grid_size = player.get("inv_grid", 3) 
    page = base_pages.get(user_id, 0)
    
    visible_items = [(k, v) for k, v in inventory.items() if v > 0 and k not in HIDDEN_ITEMS and k not in USE_ITEMS]
    
    storage_lvl = player.get("buildings", {}).get("storage", 0)
    from handlers.alc_core import STORAGE_CAPACITY
    max_slots = STORAGE_CAPACITY.get(storage_lvl, 20)
    
    used_slots = len([k for k, v in inventory.items() if v > 0 and k not in HIDDEN_ITEMS])
    
    text = f"🎒 **ТВОЙ СКЛАД** [{used_slots}/{max_slots} слотов]\n\n"
    if not visible_items:
        text += "Здесь хранятся ресурсы для опытов и строек. Пока пусто."
    else:
        text += "Все ресурсы рассортированы ниже:"

    builder = InlineKeyboardBuilder()
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
        text, markup = await get_base_content(callback.from_user.id)
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await callback.answer(result_text, show_alert=True)
