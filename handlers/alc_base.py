import asyncio
import html
import time

from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import CommandStart, CommandObject, Command
from database import db
from handlers.alc_recipes import ELEMENTS
from handlers.pole_chudes import delete_after_delay
from handlers.alc_achievements import ACHIEVEMENTS, format_reward_label
from handlers.alc_core import (
    format_duration,
    get_or_create_player,
    get_stamina_ready_at,
    run_alchemy_intro,
    send_expedition_menu,
)

router = Router()

ALCHEMY_ALPHA_VERSION = "0.8.4.1"
ALCHEMY_CHANGELOG = [
    ("0.8.4.1", [
        "Окно Стройки переделано в короткую статусную сводку: у каждого здания теперь сразу видно уровень и цветной статус апгрейда.",
        "Названия зданий стали кнопками и открывают отдельную карточку с пройденными уровнями, пометкой 'вы тут' и описанием следующего апа.",
        "В карточке здания кнопка 'Строить' показывается только когда апгрейд реально доступен, а в остальных случаях остается только возврат назад.",
    ]),
    ("0.8.4.0", [
        "На доске появились редкие золотые заказы: они заметнее в списке, дают более вкусную, но не ломаную награду и хорошо сочетаются с закреплением.",
        "Добавлены квестовые счётчики в общую статистику игрока: теперь достижения корректно учитывают обычные, закреплённые и золотые заказы.",
        "Список достижений и одноразовое окно новых ачивок обновлены под новые награды и не ломаются на свежих типах ресурсов.",
    ]),
    ("0.8.3.1", [
        "На доске заданий появились профили наград: снабжение, исследование и мастерская. Теперь квест отличается не только предметом, но и типом выгоды.",
        "Закрепление квестов стало интереснее: вместе с задачей игрок сохраняет и её профиль награды до следующего обновления.",
        "Разброс наград за квесты расширен умеренно: больше вариантов паков, ресурсов и редких мелких бонусов без явной имбы.",
    ]),
    ("0.8.3.0", [
        "Штаб стал короче и полезнее: добавлен статус выносливости алхимика, строка Наследия убрана, а строка Казарм сокращена до строй/поход.",
        "Склад в сводке теперь показывает число активируемых предметов, а в окне Склада появилась подсказка про выгодное объединение бустпаков в Лаборатории.",
        "Кнопка Лаборатории перенесена вниз, а при готовой выносливости вместо 'Закрыть' появляется кнопка 'В ПОХОД!' с мгновенным выходом в карту экспедиции.",
        "Уведомление о восстановлении выносливости теперь ведет сразу на /поход.",
        "Казармы получили сетку 1-2-3-4 в ряд и более позднюю пагинацию: она включается только после 10 рядов.",
        "На доске заданий добавлено закрепление квестов: Гильдия позволяет сохранить 1-3 заказа на следующий день.",
    ]),
    ("0.8.2.5", [
        "Исправлен жадный текстовый хендлер редактора Наследий: обычные сообщения снова не перехватываются вне активной сессии.",
        "Главный штаб подчищен по интерфейсу: строка общего статуса Лаборатории убрана, Склад перенесен в первую кнопку, а Ачивки подняты в верхний ряд.",
        "Кнопка Лаборатории вынесена в крупный блок, чтобы не теряться среди эмодзи-навигации.",
        "Склад упрощен до списка активируемых предметов без дубляжа обычных ресурсов.",
        "Окно 'Альфа и изменения' получило пагинацию вместо одной длинной простыни.",
    ]),
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
    inventory = player.get("inventory", {})
    activatable = sum(max(0, int(inventory.get(code, 0) or 0)) for code in USE_ITEMS)
    marker = "🟢" if activatable else "🔴"
    return f"{marker} активаций {activatable}"


def build_stamina_status(player: dict) -> str:
    ready_at = get_stamina_ready_at(player)
    now = int(time.time())
    if player.get("last_mine", 0) <= 0 or ready_at <= now:
        return "⚡ Поход: 🟢 готов"
    return f"⚡ Поход: 🔵 не готов | {format_duration(ready_at - now)}"


def build_lab_status_lines(player: dict) -> list[str]:
    lab_level = player.get("buildings", {}).get("lab", 0)
    if lab_level <= 0:
        return []

    lines: list[str] = []
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
    away = len([fighter for fighter in roster if fighter.get("status") in {"expedition", "raid"}])
    marker = "🔵" if away else "🟢"
    return f"🎖 Казармы: {marker} строй {ready} | поход {away}"


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
        build_stamina_status(player),
        f"🎒 Склад: {build_storage_status(player)}",
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


def build_changelog_page(page: int) -> tuple[str, types.InlineKeyboardMarkup]:
    total_pages = len(ALCHEMY_CHANGELOG)
    page = max(0, min(page, total_pages - 1))
    version, notes = ALCHEMY_CHANGELOG[page]

    lines = [
        "🧾 <b>АЛЬФА ИЗМЕНЕНИЙ</b>",
        f"Текущая версия: <b>{html.escape(ALCHEMY_ALPHA_VERSION)}</b>",
        f"Страница <b>{page + 1}/{total_pages}</b>",
        "",
        f"<b>{html.escape(version)}</b>",
    ]
    for note in notes:
        lines.append(f"• {html.escape(note)}")
    lines.extend([
        "",
        "Альфа все еще двигается: баланс, окна и отдельные механики могут меняться между апдейтами.",
    ])

    builder = InlineKeyboardBuilder()
    nav_row = []
    if page > 0:
        nav_row.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"alc:changelog:{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(types.InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"alc:changelog:{page + 1}"))
    if nav_row:
        builder.row(*nav_row)
    builder.row(types.InlineKeyboardButton(text="🔙 В Штаб", callback_data="alc:base_return"))
    return "\n".join(lines), builder.as_markup()


def build_base_keyboard(player: dict) -> types.InlineKeyboardMarkup:
    buildings = player.get("buildings", {})
    builder = InlineKeyboardBuilder()
    stamina_ready = get_stamina_ready_at(player) <= int(time.time())

    top_row = [
        types.InlineKeyboardButton(text="🎒", callback_data="alc:inventory"),
        types.InlineKeyboardButton(text="📚", callback_data="heritage_open"),
        types.InlineKeyboardButton(text="🏆", callback_data="alc:achievements"),
    ]
    if buildings.get("altar", 0) > 0:
        top_row.append(types.InlineKeyboardButton(text="🏭", callback_data="altar_open"))
    top_row.append(types.InlineKeyboardButton(text="🧾", callback_data="alc:changelog"))
    if buildings.get("barracks", 0) > 0:
        top_row.append(types.InlineKeyboardButton(text="🧭", callback_data="expeditions_open"))
    top_row.append(types.InlineKeyboardButton(text="❓", callback_data="alc:base_help"))
    builder.row(*top_row)

    service_rows: list[types.InlineKeyboardButton] = [
        types.InlineKeyboardButton(text="🏗 Стройка", callback_data="build_open"),
        types.InlineKeyboardButton(text="📜 Квесты", callback_data="quests_open"),
    ]
    if buildings.get("barracks", 0) > 0:
        service_rows.append(types.InlineKeyboardButton(text="🎖 Казармы", callback_data="barracks_open"))
    if buildings.get("fort", 0) > 0:
        service_rows.append(types.InlineKeyboardButton(text="🏰 Форт", callback_data="fort_open"))

    for idx in range(0, len(service_rows), 2):
        builder.row(*service_rows[idx:idx + 2])
    builder.row(types.InlineKeyboardButton(text="⚗️ Лаборатория", callback_data="lab_open"))
    if stamina_ready:
        builder.row(types.InlineKeyboardButton(text="В ПОХОД!", callback_data="alc:base_pohod"))
    else:
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
            reward_label = format_reward_label(ach["reward_item"], ach["reward_count"])
            msgs.append(f"🏆 **{ach['name']}**\n_Награда: {reward_label}_")
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


@router.callback_query(F.data == "alc:changelog")
async def show_changelog(callback: types.CallbackQuery):
    text, markup = build_changelog_page(0)
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("alc:changelog:"))
async def show_changelog_page(callback: types.CallbackQuery):
    page = int(callback.data.split(":")[2])
    text, markup = build_changelog_page(page)
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "alc:base_help")
async def show_base_help(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 В Штаб", callback_data="alc:base_return")
    builder.adjust(1)
    await callback.message.edit_text(
        "❓ **СПРАВКА ПО ШТАБУ**\n\n"
        "Верхний ряд:\n"
        "🎒 Склад и активация\n"
        "📚 Наследие\n"
        "🏆 Ачивки\n"
        "🏭 Производство\n"
        "🧾 Альфа и изменения\n"
        "🧭 Экспедиции\n"
        "❓ Эта справка\n\n"
        "Большие кнопки ниже:\n"
        "🏗 Стройка\n"
        "📜 Квесты\n"
        "🎖 Казармы\n"
        "🏰 Форт\n"
        "⚗️ Лаборатория\n"
        "❌ Закрыть или В ПОХОД!, если выносливость уже откатилась\n\n"
        "Цвета статусов:\n"
        "🔴 пусто, не запущено или не собрано\n"
        "🔵 идет работа, модуль занят или отряд в пути\n"
        "🟢 готово, собрано или можно забирать\n\n"
        "Штаб показывает короткую сводку. Все действия открываются кнопками сверху.",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown",
    )
    await callback.answer()


# ==================== ИНВЕНТАРЬ И НАСТРОЙКИ СЕТКИ ====================

@router.callback_query(F.data == "alc:inventory")
async def show_inventory(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    inventory = player.get("inventory", {})
    active_items = [(code, inventory.get(code, 0)) for code in USE_ITEMS if inventory.get(code, 0) > 0]
    
    storage_lvl = player.get("buildings", {}).get("storage", 0)
    from handlers.alc_core import STORAGE_CAPACITY
    max_slots = STORAGE_CAPACITY.get(storage_lvl, 20)
    
    used_slots = len([k for k, v in inventory.items() if v > 0 and k not in HIDDEN_ITEMS])
    
    text = f"🎒 **ТВОЙ СКЛАД** [{used_slots}/{max_slots} слотов]\n\n"
    if active_items:
        text += "Готово к активации:\n"
        text += "\n".join(f"• {ELEMENTS.get(code, code)} x{amount}" for code, amount in active_items)
        text += "\n\n💡 Бустпаки выгоднее сначала соединять в Лаборатории."
    else:
        text += "Сейчас здесь нет предметов, которые можно активировать.\n\n💡 Бустпаки выгоднее сначала соединять в Лаборатории."

    builder = InlineKeyboardBuilder()
    if active_items:
        for code, amount in active_items:
            builder.button(text=f"▶️ {ELEMENTS.get(code, code)} x{amount}", callback_data=f"alc_use:{code}")
        builder.adjust(1)
    builder.row(types.InlineKeyboardButton(text="🔙 В Штаб", callback_data="alc:base_return"))
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

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


@router.callback_query(F.data == "alc:base_pohod")
async def close_and_start_pohod(callback: types.CallbackQuery):
    await callback.message.delete()
    await callback.answer()
    await send_expedition_menu(callback.message, callback.from_user.id, callback.from_user.first_name)

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
