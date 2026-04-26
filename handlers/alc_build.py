import time

from aiogram import Router, types, F
from aiogram.filters import Filter
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database import db
from handlers.alc_core import get_heritage_slots, get_or_create_player
from handlers.alc_fighters import (
    build_fighter_label,
    can_name_fighter,
    can_pay_cost,
    find_fighter,
    get_available_fighters,
    get_barracks_capacity,
    get_fighter_stamina_cap,
    get_fighter_title,
    get_name_unlock_xp,
    get_player_fighters,
    get_upgrade_cost,
    refresh_fighter,
    spend_cost,
)
from handlers.alc_recipes import BATTLE_UNITS, ELEMENTS
# Важно импортировать функцию отрисовки базы, чтобы кнопка Назад работала красиво
# Но чтобы избежать циклического импорта, вызовем её через start

router = Router()

BARRACKS_ROWS_PER_PAGE = 8
BARRACKS_HOF_PER_PAGE = 10
barracks_pages: dict[int, int] = {}
barracks_hof_pages: dict[int, int] = {}


def format_supply_report(report: dict | None) -> str:
    if not report:
        return "📊 Отчет службы снабжения: данных пока нет."

    drained = report.get("drained_energy", 0)
    served_units = report.get("served_units", 0)
    deserters = report.get("deserters", 0)
    residual = report.get("energy_left", 0)
    timestamp = report.get("date_label", "сегодня")
    line = (
        f"📊 Отчет службы снабжения за {timestamp}: "
        f"списано {drained} энергии за содержание {served_units} воинов и линий обеспечения. "
        f"Остаток: {residual}."
    )
    if deserters:
        line += f" Дезертировало: {deserters}."
    return line


def shorten_button_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def get_barracks_effect_lines(level: int) -> list[str]:
    levels = BUILDINGS["barracks"]["levels"]
    lines = []
    for current_level in range(1, level + 1):
        effect_desc = levels.get(current_level, {}).get("effect_desc")
        if effect_desc:
            lines.append(f"Ур. {current_level}: {effect_desc}")
    return lines


def get_available_barracks_units(level: int) -> dict[int, list[str]]:
    grouped: dict[int, list[str]] = {}
    for unit_id, meta in BATTLE_UNITS.items():
        if meta["barracks_level"] > level:
            continue
        tier = meta["tier"]
        grouped.setdefault(tier, []).append(unit_id)

    for tier_units in grouped.values():
        tier_units.sort(key=lambda unit_id: (BATTLE_UNITS[unit_id]["barracks_level"], ELEMENTS.get(unit_id, unit_id)))
    return dict(sorted(grouped.items()))


def get_building_help_line(building_id: str, level: int) -> str:
    hints = {
        "storage": "Склад нужен, когда предметы перестают помещаться. Для старта обычно хватает 1 уровня, чтобы спокойно копить ресурсы и не жечь лут.",
        "lab": "Лаборатория - сердце базы. Сначала смешивай простое: стихии -> материалы -> полезные вещи, а Тигель и Реторту оставь на потом.",
        "altar": "Производство и Алтарь нужны, когда хочешь пассивный доход. Это лучше строить после Склада и Лаборатории, когда уже есть что добывать и где хранить.",
        "guild": "Гильдия помогает ходить дальше и чаще. 1 уровень уже открывает нормальный размер отряда, а дальше полезен тем, кто живет экспедициями.",
        "barracks": "Казармы открывают армию из простых связок. Пример для старта: Огнемеч = Дух Огня + Металл. Ищи похожие рецепты для других стихий.",
        "fort": "Форт защищает склад. В ловушку можно положить любой предмет: хоть зомби, хоть велосипед, хоть стекло - главное, чтобы потом было не жалко.",
        "heritage_center": "Наследие мира собирается из обрывков лора. Собранные коллекции ставятся на стенды и питают базу постоянными бонусами.",
    }
    return hints.get(building_id, "")


def get_building_status(building_id: str, player: dict) -> tuple[str, bool, bool]:
    inventory = player.get("inventory", {})
    buildings = player.get("buildings", {"storage": 0, "lab": 0, "altar": 0, "guild": 0, "barracks": 0, "fort": 0, "heritage_center": 1})
    current_level = buildings.get(building_id, 0)
    next_level = current_level + 1
    building = BUILDINGS[building_id]
    if next_level not in building["levels"]:
        return "🟢 всё построено", False, True

    cost = building["levels"][next_level]["cost"]
    can_upgrade = all(inventory.get(item_id, 0) >= amount for item_id, amount in cost.items())
    if can_upgrade:
        return "🔵 апгрейд доступен", True, False
    return "🔴 апгрейд недоступен", False, False


def get_building_status_emoji(building_id: str, player: dict) -> str:
    status_line, _, _ = get_building_status(building_id, player)
    return status_line.split(" ", 1)[0]


def format_building_cost(inventory: dict, cost: dict) -> str:
    parts = []
    for item_id, amount in cost.items():
        have = int(inventory.get(item_id, 0) or 0)
        marker = "✅" if have >= amount else "❌"
        parts.append(f"{marker} {ELEMENTS.get(item_id, item_id)}: {have}/{amount}")
    return ", ".join(parts) if parts else "Бесплатно."


def build_building_detail_text(player: dict, building_id: str) -> tuple[str, bool]:
    inventory = player.get("inventory", {})
    buildings = player.get("buildings", {"storage": 0, "lab": 0, "altar": 0, "guild": 0, "barracks": 0, "fort": 0, "heritage_center": 1})
    building = BUILDINGS[building_id]
    current_level = buildings.get(building_id, 0)
    next_level = current_level + 1
    status_line, can_upgrade, is_maxed = get_building_status(building_id, player)

    lines = [
        f"{building['name']}",
        f"Статус: {status_line}",
        "",
        building["desc"],
    ]

    help_line = get_building_help_line(building_id, current_level)
    if help_line:
        lines.extend(["", f"💡 {help_line}"])

    lines.extend(["", "Уровни:"])
    if current_level <= 0:
        lines.append("• Пока не построено.")
    else:
        for level in range(1, current_level + 1):
            effect_desc = building["levels"].get(level, {}).get("effect_desc", "")
            marker = "✅" if level < current_level else "✅ вы тут"
            lines.append(f"• {marker} Ур. {level}: {effect_desc}")

    if not is_maxed and next_level in building["levels"]:
        next_data = building["levels"][next_level]
        lines.extend([
            "",
            f"Следующий апгрейд: Ур. {next_level}",
            f"📈 {next_data['effect_desc']}",
            f"💰 {format_building_cost(inventory, next_data['cost'])}",
        ])
    else:
        lines.extend(["", "🌟 Все уровни уже построены."])

    return "\n".join(lines), can_upgrade


def build_building_detail_keyboard(player: dict, building_id: str) -> types.InlineKeyboardMarkup:
    _, can_upgrade, is_maxed = get_building_status(building_id, player)
    builder = InlineKeyboardBuilder()
    if can_upgrade and not is_maxed:
        builder.button(text="🏗 Строить", callback_data=f"build_upg:{building_id}")
    builder.button(text="🔙 Вернуться", callback_data="build_open")
    builder.adjust(1)
    return builder.as_markup()

BUILDINGS = {
    "storage": {
        "id": "storage",
        "name": "📦 Склад",
        "desc": "Увеличивает макс. количество слотов инвентаря.",
        "levels": {
            1: {"cost": {"stone": 2, "sand": 2}, "effect_desc": "Лимит: 50 слотов", "cap": 50},
            2: {"cost": {"brick": 4, "stone": 6, "metal": 1}, "effect_desc": "Лимит: 150 слотов", "cap": 150},
            3: {"cost": {"glass": 5, "metal": 4, "energy": 2}, "effect_desc": "Лимит: 350 слотов", "cap": 350}
        }
    },
    "lab": {
        "id": "lab",
        "name": "⚗️ Лаборатория",
        "desc": "Место для опытов.",
        "levels": {
            1: {"cost": {}, "effect_desc": "Базовый крафт. Бесплатно."},
            2: {"cost": {"stone": 6, "brick": 4, "glass": 2}, "effect_desc": "Открывает Тигель и более уверенный крафт"},
            3: {"cost": {"metal": 5, "glass": 4, "energy": 3}, "effect_desc": "Открывает Реторту и крафт 3-го уровня"}
        }
    },
    "altar": {
        "id": "altar",
        "name": "⛩ Производство и Алтарь духов",
        "desc": "Позволяет отправлять духов стихий на работу (авто-добыча).",
        "levels": {
            1: {"cost": {"stone": 8, "wood": 8, "energy": 2}, "effect_desc": "Доступна 1 линия производства.", "slots": 1},
            2: {"cost": {"brick": 10, "metal": 4, "glass": 3}, "effect_desc": "Доступно 2 линии производства.", "slots": 2},
            3: {"cost": {"electricity": 5, "diamond": 1, "robot": 1}, "effect_desc": "Доступно 3 линии производства.", "slots": 3}
        }
    },
    "guild": {
        "id": "guild",
        "name": "⛺ Гильдия Искателей",
        "desc": "Уменьшает таймер походов и увеличивает добычу.",
        "levels": {
            1: {"cost": {"wood": 8, "stone": 6}, "effect_desc": "-30 мин к кд, отряд до 3 бойцов, +2 ресурса"},
            2: {"cost": {"brick": 10, "metal": 4, "glass": 2}, "effect_desc": "-1 час к кд, отряд до 4 бойцов, +5 ресурсов"},
            3: {"cost": {"book": 3, "energy": 4, "metal": 6}, "effect_desc": "-2 часа к кд, отряд до 5 бойцов, +10 ресурсов"}
        }
    },
    "barracks": {
        "id": "barracks",
        "name": "🎖 Казармы",
        "desc": "Открывают военные рецепты и показывают состав армии.",
        "levels": {
            1: {
                "cost": {"stone": 12, "wood": 12, "brick": 6, "energy": 2},
                "effect_desc": "Доступна тренировка Пехоты: тир-1 юниты на базе рабочих духов и простых материалов."
            },
            2: {
                "cost": {"brick": 14, "metal": 8, "glass": 4, "energy": 5},
                "effect_desc": "Доступны рейдеры и спецы: уже нужен более собранный склад и профильные заготовки."
            },
            3: {
                "cost": {"glass": 14, "metal": 14, "electricity": 6, "car": 2, "robot": 1, "energy": 10},
                "effect_desc": "Доступна сборка тяжелой техники: Мех, Огнетанк, Теслатанк, Ховер. Это уже роскошь: рабочий дух должен лечь в основу дорогой платформы. Проверяй сочетания робота, машины, лодки, электричества и духов."
            },
            4: {
                "cost": {"plane": 2, "tractor": 2, "robot": 2, "electricity": 10, "diamond": 1, "energy": 14},
                "effect_desc": "Доступны продвинутые платформы: Дрон, Арта, Ходун. Тут уже нужны не просто духи, а дорогие военные шасси. Проверяй самолёт, трактор, паука, бомбу, робота и усиление духами."
            },
            5: {
                "cost": {"hero": 1, "diamond": 2, "electricity": 18, "robot": 4, "car": 2, "energy": 22},
                "effect_desc": "Доступен элитный штурмовой класс: Джаггер. Это предмет роскоши для сильной экономики. Основа все еще идет от рабочих духов, но только через дорогую героическую платформу."
            }
        }
    },
    "fort": {
        "id": "fort",
        "name": "🏰 Форт",
        "desc": "Открывает оборону базы, хаотичные ловушки и подвал для пленных.",
        "levels": {
            1: {"cost": {"stone": 10, "wood": 10, "brick": 6, "energy": 2}, "effect_desc": "Открывает ров и первый слот ловушки."},
            2: {"cost": {"metal": 8, "glass": 5, "bomb": 1, "energy": 5}, "effect_desc": "Открывает катапульту и второй слот ловушки."},
            3: {"cost": {"electricity": 6, "robot": 1, "diamond": 1, "energy": 9}, "effect_desc": "Открывает алхимический распылитель и третий слот ловушки."},
        }
    },
    "heritage_center": {
        "id": "heritage_center",
        "name": "📚 История миров",
        "desc": "Стенды с собранными Наследиями мира и их постоянными бафами.",
        "levels": {
            1: {"cost": {}, "effect_desc": "Стартовый архив: 1 стенд Наследия уже доступен."},
            2: {"cost": {"paper": 3, "wood": 6, "stone": 4}, "effect_desc": "Открывает 2-й стенд Наследия."},
            3: {"cost": {"book": 2, "glass": 4, "energy": 6}, "effect_desc": "Открывает 3-й стенд Наследия."},
            4: {"cost": {"library": 1, "electricity": 5, "metal": 8}, "effect_desc": "Открывает 4-й стенд Наследия."},
            5: {"cost": {"philosophers_stone": 1, "diamond": 1, "book": 4, "energy": 12}, "effect_desc": "Открывает 5-й стенд и удваивает все активные бонусы."},
        },
    }
}

async def render_build_menu(message: types.Message, user_id: int):
    player = await db.alchemy_players.find_one({"user_id": user_id})
    user_buildings = player.get("buildings", {"storage": 0, "lab": 0, "altar": 0, "guild": 0, "barracks": 0, "fort": 0, "heritage_center": 1})
    
    text = "🏗 **СТРОЙПЛОЩАДКА**\nСиний - можно строить, красный - ресурсов не хватает, зеленый - всё готово.\n\n"
    builder = InlineKeyboardBuilder()

    for b_id, b_data in BUILDINGS.items():
        lvl = user_buildings.get(b_id, 0)
        status_line, _, _ = get_building_status(b_id, player)
        text += f"{b_data['name']} | Ур. {lvl} | {status_line}\n"
        status_emoji = get_building_status_emoji(b_id, player)
        builder.button(text=f"{status_emoji} {b_data['name']}", callback_data=f"build_view:{b_id}")

    builder.adjust(2)
    builder.button(text="🔙 В Штаб", callback_data="alc:base_return")

    await message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")


@router.callback_query(F.data.startswith("build_view:"))
async def build_view(callback: types.CallbackQuery):
    building_id = callback.data.split(":")[1]
    player = await db.alchemy_players.find_one({"user_id": callback.from_user.id})
    if not player or building_id not in BUILDINGS:
        return await callback.answer("Здание не найдено.", show_alert=True)
    text, _ = build_building_detail_text(player, building_id)
    markup = build_building_detail_keyboard(player, building_id)
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    await callback.answer()


async def render_barracks_menu(message: types.Message, user_id: int):
    player = await db.alchemy_players.find_one({"user_id": user_id})
    if player:
        player = await get_or_create_player(user_id, player.get("user_name", ""))
    if not player:
        return await message.edit_text("⚠️ Профиль алхимика не найден.", parse_mode="Markdown")

    barracks_level = player.get("buildings", {}).get("barracks", 0)
    if barracks_level <= 0:
        builder = InlineKeyboardBuilder()
        builder.button(text="🏗 Построить Казармы", callback_data="build_open")
        builder.button(text="🔙 В Штаб", callback_data="alc:base_return")
        builder.adjust(1)
        return await message.edit_text(
            "🎖 **КАЗАРМЫ**\nКазармы еще не построены. Сначала открой модуль на стройплощадке.",
            reply_markup=builder.as_markup(),
            parse_mode="Markdown",
        )

    fighters = get_player_fighters(player)
    grid_size = int(player.get("inv_grid", 3) or 3)
    if grid_size not in {2, 3, 4}:
        grid_size = 3
    available_units = get_available_barracks_units(barracks_level)
    capacity = get_barracks_capacity(barracks_level)
    roster_fighters = [fighter for fighter in fighters if fighter.get("status") != "dead"]
    active_count = len(roster_fighters)
    available_count = len([fighter for fighter in roster_fighters if fighter.get("status") == "available"])
    tired_count = len([fighter for fighter in roster_fighters if fighter.get("status") == "available" and fighter.get("stamina", 0) <= 0])
    expedition_count = sum(len(expedition.get("fighter_ids", [])) for expedition in player.get("active_expeditions", []) if expedition.get("status") != "arrived")
    raid_count = len(player.get("raid_squad", {}).get("fighter_ids", []))

    lines = [f"🎖 **КАЗАРМЫ** (Ур. {barracks_level})", "Что доступно:"]
    lines.extend(get_barracks_effect_lines(barracks_level))
    lines.append("")
    lines.append(f"Личный состав: {active_count}/{capacity}")
    lines.append(f"В строю: {available_count} | Уставшие: {tired_count} | В походах: {expedition_count + raid_count}")
    lines.append("")
    lines.append("Доступные юниты:")
    for tier, unit_ids in available_units.items():
        unit_names = ", ".join(ELEMENTS.get(unit_id, unit_id) for unit_id in unit_ids)
        lines.append(f"• Тир-{tier}: {unit_names}")
    lines.append("")
    lines.append("Подсказки для командира:")
    lines.append("• Теперь каждый боец - отдельная личность с XP и бодростью. Экспедиции качают безопасно, рейды качают опасно.")
    lines.append("• За каждый выход боец получает 1 XP и теряет 1 бодрость. Бодрость возвращается по 1 ед. каждые 6 часов.")
    lines.append("• Апгрейд в Т2 доступен с 30 XP, в Т3 - с 70 XP. Если Т3 был скрафчен сразу, имя можно дать уже на 20 XP.")
    lines.append("• В экспедицию уставший боец не идёт. В рейд при нуле бодрости пойдёт, но шанс потерять его уже почти суицидальный.")
    lines.append("")

    available_fighters = get_available_fighters(player)
    roster_fighters.sort(
        key=lambda item: (
            item.get("status") not in {"available", "expedition", "raid"},
            item.get("status") != "available",
            -int(item.get("tier", 1)),
            -int(item.get("xp", 0)),
            get_fighter_title(item),
        )
    )
    current_page = barracks_pages.get(user_id, 0)
    items_per_page = grid_size * BARRACKS_ROWS_PER_PAGE
    total_pages = max(1, (len(roster_fighters) + items_per_page - 1) // items_per_page)
    if current_page >= total_pages:
        current_page = max(0, total_pages - 1)
    barracks_pages[user_id] = current_page
    page_start = current_page * items_per_page
    page_fighters = roster_fighters[page_start: page_start + items_per_page]
    if roster_fighters:
        lines.append("Лидеры по опыту:")
        for fighter in sorted(available_fighters, key=lambda item: (-item.get("xp", 0), -item.get("tier", 1), get_fighter_title(item)))[:6]:
            lines.append(f"• {build_fighter_label(fighter)}")
        lines.append("")
        lines.append("Состав страницы:")
        for fighter in page_fighters:
            if fighter.get("status") == "expedition":
                marker = "🧪"
                status_text = "в экспедиции"
            elif fighter.get("status") == "raid":
                marker = "☠️"
                status_text = "в рейде"
            elif fighter.get("stamina", 0) <= 0:
                marker = "😴"
                status_text = "отдыхает"
            else:
                marker = "🪖"
                status_text = "в строю"
            lines.append(f"• {marker} {build_fighter_label(fighter)} | {status_text}")
        if len(roster_fighters) > items_per_page:
            lines.append("")
            lines.append(f"Страница состава: {current_page + 1}/{total_pages}")
    else:
        lines.append("Личный состав пуст. Боевые рецепты из Лаборатории теперь сразу создают бойцов в ростере.")

    lines.append("")
    lines.append(format_supply_report(player.get("barracks_report")))

    builder = InlineKeyboardBuilder()
    row = []
    for fighter in page_fighters:
        if fighter.get("status") == "expedition":
            marker = "🧪"
        elif fighter.get("status") == "raid":
            marker = "☠️"
        elif fighter.get("stamina", 0) <= 0:
            marker = "😴"
        else:
            marker = "🪖"
        base_name = get_fighter_title(fighter)
        if grid_size == 4:
            btn_text = shorten_button_text(base_name, 10)
        elif grid_size == 3:
            btn_text = f"{marker} {shorten_button_text(base_name, 8)}"
        else:
            btn_text = f"{marker} {shorten_button_text(base_name, 14)}"
        row.append(types.InlineKeyboardButton(text=btn_text, callback_data=f"fighter_view:{fighter['id']}"))
        if len(row) == grid_size:
            builder.row(*row)
            row = []
    if row:
        builder.row(*row)
    if total_pages > 1:
        nav_row = []
        if current_page > 0:
            nav_row.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"barracks_page:{current_page - 1}"))
        if current_page < total_pages - 1:
            nav_row.append(types.InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"barracks_page:{current_page + 1}"))
        if nav_row:
            builder.row(*nav_row)
    next_grid = 3 if grid_size == 2 else 4 if grid_size == 3 else 2
    controls = InlineKeyboardBuilder()
    controls.button(text=f"⚙️ Сетка: {grid_size} в ряд (Жми для {next_grid})", callback_data=f"barracks_set_grid:{next_grid}")
    controls.button(text="🏅 Зал славы", callback_data="barracks_hof")
    controls.button(text="🏗 Постройки", callback_data="build_open")
    controls.button(text="🧪 Экспедиции", callback_data="expeditions_open")
    controls.button(text="☠️ Рейдеры", callback_data="raids_open")
    controls.button(text="🔙 В Штаб", callback_data="alc:base_return")
    controls.adjust(1)
    builder.attach(controls)
    await message.edit_text("\n".join(lines), reply_markup=builder.as_markup(), parse_mode="Markdown")


def build_fighter_detail_text(player: dict, fighter: dict) -> str:
    refresh_fighter(fighter)
    title = get_fighter_title(fighter)
    tier = int(fighter.get("tier", 1))
    name_unlock = get_name_unlock_xp(fighter)
    risk = int(round((0.80 if fighter.get("stamina", 0) <= 0 else {1: 0.13, 2: 0.09, 3: 0.05}.get(tier, 0.13)) * 100))
    lines = [
        f"🪪 **{title}**",
        f"Роль: {ELEMENTS.get(fighter.get('unit_id'), fighter.get('unit_id'))}",
        f"Тир: {tier}",
        f"Опыт: {fighter.get('xp', 0)}",
        f"Бодрость: {fighter.get('stamina', 0)}/{get_fighter_stamina_cap(fighter)}",
        f"Рейды: {fighter.get('raids', 0)} | Экспедиции: {fighter.get('expeditions', 0)}",
        f"Риск смерти в рейде сейчас: {risk}%",
        "",
    ]
    if tier < 3:
        next_tier = tier + 1
        cost = get_upgrade_cost(next_tier, fighter)
        cost_line = ", ".join(f"{ELEMENTS.get(item_id, item_id)} x{amount}" for item_id, amount in cost.items())
        lines.append(f"Апгрейд до Т{next_tier}: нужен XP {fighter.get('xp', 0)}/{30 if next_tier == 2 else 70} и {cost_line}.")
    else:
        lines.append(f"Т3 достигнут. Имя открывается на {name_unlock} XP.")
    if can_name_fighter(fighter):
        lines.append("Имя разблокировано.")
    elif tier >= 3:
        lines.append(f"До имени ещё {max(0, name_unlock - int(fighter.get('xp', 0)))} XP.")
    return "\n".join(lines)


def build_fighter_detail_keyboard(player: dict, fighter: dict) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    tier = int(fighter.get("tier", 1))
    if tier < 3:
        next_tier = tier + 1
        builder.button(text=f"⬆️ Повысить до Т{next_tier}", callback_data=f"fighter_promote:{fighter['id']}")
    if can_name_fighter(fighter):
        builder.button(text="✍️ Дать имя", callback_data=f"fighter_rename:{fighter['id']}")
    if fighter.get("status") == "available":
        builder.button(text="🪦 Отправить в отставку", callback_data=f"fighter_retire:{fighter['id']}")
    builder.button(text="🏅 Зал славы", callback_data="barracks_hof")
    builder.button(text="🔙 К казармам", callback_data="barracks_open")
    builder.adjust(1)
    return builder.as_markup()


@router.callback_query(F.data.startswith("fighter_view:"))
async def fighter_view(callback: types.CallbackQuery):
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    fighter = find_fighter(player, callback.data.split(":")[1])
    if not fighter:
        return await callback.answer("Боец не найден.", show_alert=True)
    await callback.message.edit_text(build_fighter_detail_text(player, fighter), reply_markup=build_fighter_detail_keyboard(player, fighter), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("fighter_promote:"))
async def fighter_promote(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    fighter = find_fighter(player, callback.data.split(":")[1])
    if not fighter:
        return await callback.answer("Боец потерялся по дороге в штаб.", show_alert=True)
    current_tier = int(fighter.get("tier", 1))
    if current_tier >= 3:
        return await callback.answer("Выше Т3 уже некуда.", show_alert=True)
    target_tier = current_tier + 1
    required_xp = 30 if target_tier == 2 else 70
    if player.get("buildings", {}).get("barracks", 0) < target_tier:
        return await callback.answer("Казармы ещё не готовы к такому тиру.", show_alert=True)
    if int(fighter.get("xp", 0)) < required_xp:
        return await callback.answer(f"Нужно минимум {required_xp} XP.", show_alert=True)
    inventory = player.get("inventory", {})
    cost = get_upgrade_cost(target_tier, fighter)
    if not can_pay_cost(inventory, cost):
        return await callback.answer("Не хватает энергии или профильных духов.", show_alert=True)
    spend_cost(inventory, cost)
    fighter["tier"] = target_tier
    fighter["stamina"] = get_fighter_stamina_cap(fighter)
    fighter["stamina_updated_at"] = int(time.time())
    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {"inventory": inventory, "fighters": player.get("fighters", [])}},
    )
    await callback.message.edit_text(build_fighter_detail_text(player, fighter), reply_markup=build_fighter_detail_keyboard(player, fighter), parse_mode="Markdown")
    await callback.answer(f"Боец переведён в Т{target_tier}.")


@router.callback_query(F.data.startswith("fighter_rename:"))
async def fighter_rename(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    fighter = find_fighter(player, callback.data.split(":")[1])
    if not fighter:
        return await callback.answer("Боец не найден.", show_alert=True)
    if not can_name_fighter(fighter):
        return await callback.answer("Имя для него ещё не открыто.", show_alert=True)
    pending = {"fighter_id": fighter["id"], "set_at": int(time.time())}
    await db.alchemy_players.update_one({"user_id": user_id}, {"$set": {"fighter_name_pending": pending}})
    await callback.answer("Отправь имя следующим сообщением в этот чат.", show_alert=True)


@router.callback_query(F.data.startswith("fighter_retire:"))
async def fighter_retire(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    fighter_id = callback.data.split(":")[1]
    fighter = find_fighter(player, fighter_id)
    if not fighter:
        return await callback.answer("Боец не найден.", show_alert=True)
    if fighter.get("status") != "available":
        return await callback.answer("В отставку можно отправить только свободного бойца.", show_alert=True)
    player["fighters"] = [item for item in player.get("fighters", []) if item.get("id") != fighter_id]
    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {"fighters": player.get("fighters", [])}},
    )
    await callback.answer("Боец отправлен в отставку.")
    await render_barracks_menu(callback.message, user_id)


class IsNamingFighter(Filter):
    async def __call__(self, message: types.Message) -> bool:
        player = await db.alchemy_players.find_one({"user_id": message.from_user.id})
        pending = (player or {}).get("fighter_name_pending")
        return bool(pending)
@router.message(F.text, IsNamingFighter())
async def handle_fighter_naming(message: types.Message):
    player = await db.alchemy_players.find_one({"user_id": message.from_user.id})
    pending = player.get("fighter_name_pending")
    fighter = find_fighter(player, pending.get("fighter_id", ""))
    if not fighter or not can_name_fighter(fighter):
        await db.alchemy_players.update_one({"user_id": message.from_user.id}, {"$set": {"fighter_name_pending": None}})
        await message.reply("Имя уже нельзя назначить. Открой Казармы и выбери бойца заново.")
        return
    new_name = " ".join((message.text or "").strip().split())[:24]
    if len(new_name) < 2:
        await message.reply("Имя слишком короткое. Нужно хотя бы 2 символа.")
        return
    fighter["custom_name"] = new_name
    await db.alchemy_players.update_one(
        {"user_id": message.from_user.id},
        {"$set": {"fighters": player.get("fighters", []), "fighter_name_pending": None}},
    )
    await message.reply(f"Имя закреплено: {new_name}. Открой Казармы, чтобы увидеть обновлённую карточку.")


@router.callback_query(F.data == "barracks_hof")
async def barracks_hof(callback: types.CallbackQuery):
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    hall = player.get("hall_of_fame", [])
    lines = ["🏅 **ЗАЛ СЛАВЫ**"]
    current_page = barracks_hof_pages.get(callback.from_user.id, 0)
    total_pages = max(1, (len(hall) + BARRACKS_HOF_PER_PAGE - 1) // BARRACKS_HOF_PER_PAGE)
    if current_page >= total_pages:
        current_page = max(0, total_pages - 1)
    barracks_hof_pages[callback.from_user.id] = current_page
    if not hall:
        lines.append("Пока пусто. И это хороший знак.")
    else:
        ordered_hall = hall[::-1]
        page_start = current_page * BARRACKS_HOF_PER_PAGE
        page_entries = ordered_hall[page_start: page_start + BARRACKS_HOF_PER_PAGE]
        for entry in page_entries:
            lines.append(
                f"• {entry.get('name')} | Т{entry.get('tier', 1)} | рейды {entry.get('raids', 0)} | экспедиции {entry.get('expeditions', 0)}"
            )
        if total_pages > 1:
            lines.append("")
            lines.append(f"Страница памяти: {current_page + 1}/{total_pages}")
    builder = InlineKeyboardBuilder()
    if total_pages > 1:
        if current_page > 0:
            builder.button(text="⬅️ Память", callback_data=f"barracks_hof_page:{current_page - 1}")
        if current_page < total_pages - 1:
            builder.button(text="Память ➡️", callback_data=f"barracks_hof_page:{current_page + 1}")
    builder.button(text="🔙 В Казармы", callback_data="barracks_open")
    builder.adjust(1)
    await callback.message.edit_text("\n".join(lines), reply_markup=builder.as_markup(), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("barracks_page:"))
async def barracks_page(callback: types.CallbackQuery):
    barracks_pages[callback.from_user.id] = int(callback.data.split(":")[1])
    await render_barracks_menu(callback.message, callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data.startswith("barracks_set_grid:"))
async def barracks_set_grid(callback: types.CallbackQuery):
    new_grid = int(callback.data.split(":")[1])
    if new_grid not in {2, 3, 4}:
        new_grid = 3
    await db.alchemy_players.update_one({"user_id": callback.from_user.id}, {"$set": {"inv_grid": new_grid}})
    barracks_pages[callback.from_user.id] = 0
    await render_barracks_menu(callback.message, callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data.startswith("barracks_hof_page:"))
async def barracks_hof_page(callback: types.CallbackQuery):
    barracks_hof_pages[callback.from_user.id] = int(callback.data.split(":")[1])
    await barracks_hof(callback)

@router.callback_query(F.data == "build_open")
async def build_main(callback: types.CallbackQuery):
    await render_build_menu(callback.message, callback.from_user.id)
    await callback.answer()

@router.callback_query(F.data == "build_nope")
async def build_nope(callback: types.CallbackQuery):
    await callback.answer("Нужно больше ресурсов!", show_alert=True)


@router.callback_query(F.data == "barracks_open")
async def barracks_open(callback: types.CallbackQuery):
    await render_barracks_menu(callback.message, callback.from_user.id)
    await callback.answer()

@router.callback_query(F.data.startswith("build_upg:"))
async def upg_logic(callback: types.CallbackQuery):
    b_id = callback.data.split(":")[1]
    user_id = callback.from_user.id
    
    player = await db.alchemy_players.find_one({"user_id": user_id})
    if not player or b_id not in BUILDINGS:
        return await callback.answer("Апгрейд недоступен.", show_alert=True)
    inv = player.get("inventory", {})
    builds = player.get("buildings", {"storage": 0, "lab": 0, "altar": 0, "guild": 0, "barracks": 0, "fort": 0, "heritage_center": 1})
    
    nxt_lvl = builds.get(b_id, 0) + 1
    if nxt_lvl not in BUILDINGS[b_id]["levels"]:
        return await callback.answer("Тут уже всё построено.", show_alert=True)
    cost = BUILDINGS[b_id]["levels"][nxt_lvl]["cost"]
    if not can_pay_cost(inv, cost):
        return await callback.answer("Пока не хватает ресурсов.", show_alert=True)
    
    for item, amt in cost.items():
        inv[item] -= amt
    
    builds[b_id] = nxt_lvl

    update_fields = {"inventory": inv, "buildings": builds}
    if b_id == "heritage_center":
        stands = list(player.get("active_stands", []))
        target_slots = get_heritage_slots(nxt_lvl)
        stands = [stand if stand else None for stand in stands[:target_slots]]
        while len(stands) < target_slots:
            stands.append(None)
        update_fields["active_stands"] = stands

    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": update_fields}
    )
    
    await callback.answer(f"🚀 {BUILDINGS[b_id]['name']} теперь {nxt_lvl} уровня!")
    await render_build_menu(callback.message, user_id)
    
