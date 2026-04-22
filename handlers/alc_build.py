from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database import db
from handlers.alc_recipes import BATTLE_UNITS, ELEMENTS
# Важно импортировать функцию отрисовки базы, чтобы кнопка Назад работала красиво
# Но чтобы избежать циклического импорта, вызовем её через start

router = Router()


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
        "lab": "Лаборатория - сердце базы. Сначала смешивай простое: стихии -> материалы -> полезные вещи, а Тигель и Реторта оставь на потом.",
        "altar": "Алтарь нужен, когда хочешь пассивный доход. Его лучше строить после Склада и Лаборатории, когда уже есть что добывать и где хранить.",
        "guild": "Гильдия помогает ходить дальше и чаще. 1 уровень уже открывает нормальный размер отряда, а дальше полезен тем, кто живет экспедициями.",
        "barracks": "Казармы открывают армию из простых связок. Пример для старта: Огнемеч = Дух Огня + Металл. Ищи похожие рецепты для других стихий.",
        "fort": "Форт защищает склад. В ловушку можно положить любой предмет: хоть зомби, хоть велосипед, хоть стекло - главное, чтобы потом было не жалко.",
    }
    return hints.get(building_id, "")

BUILDINGS = {
    "storage": {
        "id": "storage",
        "name": "📦 Склад",
        "desc": "Увеличивает макс. количество слотов инвентаря.",
        "levels": {
            1: {"cost": {"stone": 2, "wood": 4}, "effect_desc": "Лимит: 50 слотов", "cap": 50},
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
    }
}

async def render_build_menu(message: types.Message, user_id: int):
    player = await db.alchemy_players.find_one({"user_id": user_id})
    inventory = player.get("inventory", {})
    user_buildings = player.get("buildings", {"storage": 0, "lab": 0, "altar": 0, "guild": 0, "barracks": 0, "fort": 0})
    
    text = "🏗 **СТРОЙПЛОЩАДКА**\nРесурсы тратятся на улучшение модулей базы.\n\n"
    builder = InlineKeyboardBuilder()

    for b_id, b_data in BUILDINGS.items():
        lvl = user_buildings.get(b_id, 0)
        nxt = lvl + 1
        
        text += f"**{b_data['name']}** (Ур. {lvl})\n"
        
        if nxt in b_data["levels"]:
            cost = b_data["levels"][nxt]["cost"]
            cost_str = []
            can_up = True
            
            for item, amt in cost.items():
                name = ELEMENTS.get(item, item)
                has = inventory.get(item, 0)
                emoji = "✅" if has >= amt else "❌"
                if has < amt: can_up = False
                cost_str.append(f"{emoji} {name}: {has}/{amt}")
            
            text += f"📈 {b_data['levels'][nxt]['effect_desc']}\n"
            help_line = get_building_help_line(b_id, lvl)
            if help_line:
                text += f"💡 {help_line}\n"
            text += f"💰 {', '.join(cost_str)}\n\n"
            
            btn_text = f"⬆️ Улучшить {b_data['name']}" if can_up else f"🔒 Не хватает ресурсов"
            builder.button(text=btn_text, callback_data=f"build_upg:{b_id}" if can_up else "build_nope")
        else:
            help_line = get_building_help_line(b_id, lvl)
            if help_line:
                text += f"💡 {help_line}\n"
            text += "🌟 Максимальный уровень достигнут.\n\n"

    builder.adjust(1)
    # Используем deep_link для возврата в главное меню
    builder.button(text="🔙 В Штаб", callback_data="alc:base_return")

    await message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")


async def render_barracks_menu(message: types.Message, user_id: int):
    player = await db.alchemy_players.find_one({"user_id": user_id})
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

    inventory = player.get("inventory", {})
    owned_units = [(unit_id, amount) for unit_id, amount in inventory.items() if amount > 0 and unit_id in BATTLE_UNITS]
    owned_units.sort(key=lambda item: (BATTLE_UNITS[item[0]]["tier"], ELEMENTS.get(item[0], item[0])))
    available_units = get_available_barracks_units(barracks_level)

    lines = [f"🎖 **КАЗАРМЫ** (Ур. {barracks_level})", "Что доступно:"]
    lines.extend(get_barracks_effect_lines(barracks_level))
    lines.append("")
    lines.append("Доступные юниты:")
    for tier, unit_ids in available_units.items():
        unit_names = ", ".join(ELEMENTS.get(unit_id, unit_id) for unit_id in unit_ids)
        lines.append(f"• Тир-{tier}: {unit_names}")
    lines.append("")
    lines.append("Подсказки для командира:")
    lines.append("• Начни с простых бойцов. Пример: Огнемеч = Дух Огня + Металл.")
    lines.append("• Если хочешь много армии рано, сначала собирай тир-1, а уже потом лезь в рейдеров и технику.")
    lines.append("• Экспедиции зависят от Казарм, а размер рейд-отряда и походного отряда - от Гильдии.")
    lines.append("")

    if owned_units:
        lines.append("Боевой состав:")
        for unit_id, amount in owned_units:
            meta = BATTLE_UNITS[unit_id]
            lines.append(
                f"• {ELEMENTS.get(unit_id, unit_id)} x{amount} | Тир {meta['tier']} | Содержание: {meta['upkeep']}⚡/сутки"
            )
    else:
        lines.append("Боевых юнитов пока нет. Ищи рецепты в Лаборатории.")

    lines.append("")
    lines.append(format_supply_report(player.get("barracks_report")))

    builder = InlineKeyboardBuilder()
    builder.button(text="🏗 Постройки", callback_data="build_open")
    builder.button(text="🧪 Экспедиции", callback_data="expeditions_open")
    builder.button(text="☠️ Рейдеры", callback_data="raids_open")
    builder.button(text="🔙 В Штаб", callback_data="alc:base_return")
    builder.adjust(1)
    await message.edit_text("\n".join(lines), reply_markup=builder.as_markup(), parse_mode="Markdown")

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
    inv = player.get("inventory", {})
    builds = player.get("buildings", {"storage": 0, "lab": 0, "altar": 0, "guild": 0, "barracks": 0, "fort": 0})
    
    nxt_lvl = builds.get(b_id, 0) + 1
    cost = BUILDINGS[b_id]["levels"][nxt_lvl]["cost"]
    
    # Списываем
    for item, amt in cost.items():
        inv[item] -= amt
    
    builds[b_id] = nxt_lvl
    
    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {"inventory": inv, "buildings": builds}}
    )
    
    await callback.answer(f"🚀 {BUILDINGS[b_id]['name']} теперь {nxt_lvl} уровня!")
    await render_build_menu(callback.message, user_id)
    
