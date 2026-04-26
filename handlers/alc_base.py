import asyncio
import html
import random
import time

from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import CommandStart, CommandObject, Command
from database import db
from handlers.alc_recipes import ELEMENTS
from handlers.pole_chudes import delete_after_delay
from handlers.alc_achievements import ACHIEVEMENTS, format_reward_label
from handlers.alc_core import (
    DEFAULT_WORLD_ARCHETYPE,
    STARTER_BIOME_SLOTS,
    STARTER_WORLD_REWARD_POOL,
    WORLD_ARCHETYPES,
    WORLD_FOCUS_OPTIONS,
    build_starter_world_preview,
    ensure_world_state,
    evaluate_world_creation,
    format_duration,
    get_world_capacity_left,
    get_world_capacity_total,
    get_world_stability_limit,
    get_world_capacity_used,
    get_world_strain,
    get_world_strain_left,
    get_world_effects,
    get_world_support_cost,
    get_world_support_level,
    is_world_overloaded,
    get_or_create_player,
    get_stamina_ready_at,
    run_alchemy_intro,
    send_expedition_menu,
)
from handlers.alc_fighters import can_pay_cost, spend_cost

router = Router()
universe_focus_modes: dict[int, str | None] = {}

ALCHEMY_ALPHA_VERSION = "0.9.0.1"
ALCHEMY_CHANGELOG = [
    ("0.9.0.1", [
        "Старые игроки больше не застревают в аварийной заглушке: при открытии 🌍 они сразу входят в обычную схему создания первого биома.",
        "Текст Модуля Вселенная переписан под новую миграцию: прямо сказано, что старые постройки и ресурсы сохранены, а дальше мир развивается по тем же правилам, что и у новичков.",
        "Стройка стала суше по интерфейсу: вместо фраз вроде 'апгрейд доступен' и 'всё построено' теперь используются только статусные эмодзи с короткой легендой сверху.",
    ]),
    ("0.9.0.0", [
        "Запущен Модуль Вселенная: старт алхимии теперь ведет через кристалл базы, создание первого микробиома и отдельное окно мира.",
        "Биомы стали настоящей системой прогрессии: у мира появились вместимость, нагрузка на каркас, укрепление и безопасная миграция старых профилей.",
        "Добавлены архетипы и фокусы биомов: они меняют бонусы походов и производства, а поздние апгрейды зданий теперь требуют не только ресурсы, но и подходящий профиль мира.",
    ]),
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


def build_world_status(player: dict) -> str:
    inventory = player.get("inventory", {})
    worlds, _, _ = ensure_world_state(player)
    if inventory.get("active_base", 0) <= 0:
        return "🌍 Мир: 🔴 пустота шипит, нужен кристалл базы"
    if not worlds:
        return "🌍 Мир: 🔵 карман пуст, собери первый микробиом"

    main_world = worlds[0]
    total = get_world_capacity_total(player)
    used = get_world_capacity_used(player)
    strain = get_world_strain(player)
    limit = get_world_stability_limit(player)
    support_bonus = sum(int(world.get("support_bonus", 0) or 0) for world in worlds)
    humor = main_world.get("humor") or "держится на алхимическом оптимизме"
    if is_world_overloaded(player):
        return f"🌍 Мир: 🔴 {main_world.get('name', 'Безымянный карман')} | {used}/{total} вместимости | трещины {strain}/{limit} | опора {support_bonus:+d}"
    return f"🌍 Мир: 🟢 {main_world.get('name', 'Безымянный карман')} | {used}/{total} вместимости | каркас {strain}/{limit} | опора {support_bonus:+d}"


def build_universe_intro_text(player: dict) -> str:
    inventory = player.get("inventory", {})
    worlds, _, _ = ensure_world_state(player)
    if inventory.get("active_base", 0) <= 0:
        return (
            "🌍 **МОДУЛЬ ВСЕЛЕННАЯ**\n\n"
            "Сначала активируй 🔮 Кристалл Базы на складе. Без него здесь только пустота и твои сомнительные решения."
        )
    if worlds:
        world = worlds[0]
        extra_worlds = max(0, len(worlds) - 1)
        archetype_lines = []
        for item in worlds[:4]:
            archetype_lines.append(
                f"• {item.get('name', 'Безымянный биом')} [{item.get('role', 'универсальный')}]"
                + (f" + {item.get('focus_label')}" if item.get("focus_label") else "")
                + f" | вместимость {int(item.get('capacity', 0) or 0)} | каркас {int(item.get('strain_cost', 0) or 0)}/{int(item.get('support_bonus', 0) or 0):+d}"
            )
        effects = get_world_effects(player)
        effect_lines = []
        if effects.get("farm_yield"):
            effect_lines.append(f"• 🌾 Фермы: +{int(effects['farm_yield'])} добычи")
        if effects.get("mine_yield"):
            effect_lines.append(f"• ⛏ Шахты: +{int(effects['mine_yield'])} добычи")
        if effects.get("factory_yield"):
            effect_lines.append(f"• 🏭 Заводы: +{int(effects['factory_yield'])} добычи")
        if effects.get("aero_yield"):
            effect_lines.append(f"• ☁️ Аэроцех: +{int(effects['aero_yield'])} добычи")
        if effects.get("river_bonus"):
            effect_lines.append(f"• 💧 Поход к реке: +{int(effects['river_bonus'])} лута")
        if effects.get("mountains_bonus"):
            effect_lines.append(f"• ⛰ Поход в горы: +{int(effects['mountains_bonus'])} лута")
        if effects.get("cave_bonus"):
            effect_lines.append(f"• 🕳 Пещеры: +{int(effects['cave_bonus'])} лута")
        if effects.get("volcano_bonus"):
            effect_lines.append(f"• 🌋 Вулкан: +{int(effects['volcano_bonus'])} лута")
        if effects.get("global_loot_bonus"):
            effect_lines.append(f"• 🎁 Любой поход: +{int(effects['global_loot_bonus'])} лута")
        if effects.get("rare_find_bonus"):
            effect_lines.append(f"• ✨ Редкости: +{int(float(effects['rare_find_bonus']) * 100)}% к шансу")
        overload_line = (
            f"\n⚠️ Перегруз: **{abs(get_world_strain_left(player))}**. Мир уже требует укрепления."
            if is_world_overloaded(player)
            else ""
        )
        return (
            "🌍 **МОДУЛЬ ВСЕЛЕННАЯ**\n\n"
            f"Текущий карман: **{world.get('name', 'Безымянный мир')}**\n"
            f"{world.get('description', 'Описание пока переживает миграцию.')}\n\n"
            f"Биомов собрано: **{len(worlds)}**"
            + (f" (+{extra_worlds} дополнительных)" if extra_worlds else "")
            + "\n"
            f"Вместимость: **{get_world_capacity_used(player)}/{get_world_capacity_total(player)}**\n"
            f"Свободно: **{max(0, get_world_capacity_left(player))}**\n"
            f"Каркас: **{get_world_strain(player)}/{get_world_stability_limit(player)}**\n"
            f"Укрепление: **{get_world_support_level(player)}**\n"
            "Ближайшие биомы:\n"
            + "\n".join(archetype_lines)
            + ("\n\nБонусы мира:\n" + "\n".join(effect_lines) if effect_lines else "")
            + "\n\n"
            + f"Статус: _{world.get('humor', 'на удивление не взорвался')}_"
            + overload_line
        )

    inventory_lines = []
    for category, options in STARTER_BIOME_SLOTS.items():
        available = []
        for item_id, meta in options.items():
            count = int(inventory.get(item_id, 0) or 0)
            marker = "✅" if count > 0 else "❌"
            available.append(f"{marker} {meta['label']} x{count}")
        inventory_lines.append(f"{category.title()}: " + " | ".join(available))

    lines = [
        "🌍 **МОДУЛЬ ВСЕЛЕННАЯ**",
        "",
        "На стене кармана мигает надпись: `мир уничтожен, требуется создатель`.",
        "Старые постройки и ресурсы уже сохранены. Теперь просто собери первый биом и дальше мир пойдёт по той же схеме, что и у новичков.",
        "Первый микробиом всегда стабилизируется успешно. Второй, если бахнет, бахнет без штрафа. Дальше будет уже смешнее.",
        "Позднее у биомов открывается `фокус`: он тратит более редкий предмет, но делает мир заметно полезнее под конкретный стиль игры.",
        "",
        "Для старта собери три части биома и выбери одну из доступных в каждом слоте:",
        "• Материя: Лава или Камень",
        "• Среда: Пар или Болото",
        "• Жизнь: Бактерия или Мох",
        "",
        *inventory_lines,
        "",
        "💡 Нужные стартовые рецепты уже открыты. Если чего-то не хватает, иди в Лабораторию и смешивай вручную.",
    ]
    return "\n".join(lines)


def build_universe_keyboard(player: dict) -> types.InlineKeyboardMarkup:
    inventory = player.get("inventory", {})
    worlds, _, _ = ensure_world_state(player)
    builder = InlineKeyboardBuilder()
    advanced_focus_unlocked = len(worlds) >= 2 or get_world_support_level(player) >= 1
    current_focus = universe_focus_modes.get(player.get("user_id"), None)

    if inventory.get("active_base", 0) > 0 and not worlds:
        for matter_id in STARTER_BIOME_SLOTS["matter"]:
            for medium_id in STARTER_BIOME_SLOTS["medium"]:
                for life_id in STARTER_BIOME_SLOTS["life"]:
                    if inventory.get(matter_id, 0) <= 0 or inventory.get(medium_id, 0) <= 0 or inventory.get(life_id, 0) <= 0:
                        continue
                    name, _, capacity, absurdity, _ = build_starter_world_preview(
                        {"matter": matter_id, "medium": medium_id, "life": life_id},
                        focus_id=current_focus if advanced_focus_unlocked else None,
                    )
                    archetype = WORLD_ARCHETYPES.get((matter_id, medium_id, life_id), DEFAULT_WORLD_ARCHETYPE)
                    builder.button(
                        text=f"🧬 {name} | {capacity}V | {archetype['strain_cost']}T/{archetype['support_bonus']:+d}O",
                        callback_data=f"universe_create:{matter_id}:{medium_id}:{life_id}",
                    )
        builder.button(text="⚗️ В Лабораторию", callback_data="lab_open")
    elif inventory.get("active_base", 0) > 0:
        if advanced_focus_unlocked:
            focus_label = WORLD_FOCUS_OPTIONS.get(current_focus or "", {}).get("label", "без фокуса")
            builder.button(text=f"🎯 Фокус нового биома: {focus_label}", callback_data="universe_focus:none")
            for focus_id, meta in WORLD_FOCUS_OPTIONS.items():
                count = int(inventory.get(meta["item"], 0) or 0)
                if count > 0:
                    builder.button(
                        text=f"{meta['label']} x{count}",
                        callback_data=f"universe_focus:{focus_id}",
                    )
        for matter_id in STARTER_BIOME_SLOTS["matter"]:
            for medium_id in STARTER_BIOME_SLOTS["medium"]:
                for life_id in STARTER_BIOME_SLOTS["life"]:
                    if inventory.get(matter_id, 0) <= 0 or inventory.get(medium_id, 0) <= 0 or inventory.get(life_id, 0) <= 0:
                        continue
                    name, _, capacity, absurdity, _ = build_starter_world_preview(
                        {"matter": matter_id, "medium": medium_id, "life": life_id},
                        focus_id=current_focus if advanced_focus_unlocked else None,
                    )
                    archetype = WORLD_ARCHETYPES.get((matter_id, medium_id, life_id), DEFAULT_WORLD_ARCHETYPE)
                    focus = WORLD_FOCUS_OPTIONS.get(current_focus or "") if advanced_focus_unlocked and current_focus else None
                    strain_cost = archetype["strain_cost"] + (focus["strain_delta"] if focus else 0)
                    support_bonus = archetype["support_bonus"] + (focus["support_bonus"] if focus else 0)
                    builder.button(
                        text=f"➕ {name} | {capacity}V | {strain_cost}T/{support_bonus:+d}O",
                        callback_data=f"universe_create:{matter_id}:{medium_id}:{life_id}",
                    )
        support_cost = get_world_support_cost(player)
        cost_label = ", ".join(f"{ELEMENTS.get(item_id, item_id)} x{amount}" for item_id, amount in support_cost.items())
        builder.button(text=f"🛠 Укрепить каркас | {cost_label}", callback_data="universe_fortify")
        builder.button(text="⚗️ В Лабораторию", callback_data="lab_open")

    builder.button(text="🔙 В Штаб", callback_data="alc:base_return")
    builder.adjust(1)
    return builder.as_markup()


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
        build_world_status(player),
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
        types.InlineKeyboardButton(text="🌍", callback_data="universe_open"),
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
        "🌍 Модуль Вселенная\n"
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


@router.callback_query(F.data == "universe_open")
async def universe_open(callback: types.CallbackQuery):
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    universe_focus_modes.setdefault(callback.from_user.id, None)
    await callback.message.edit_text(
        build_universe_intro_text(player),
        reply_markup=build_universe_keyboard(player),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("universe_focus:"))
async def universe_focus_select(callback: types.CallbackQuery):
    focus_id = callback.data.split(":")[1]
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    worlds, _, _ = ensure_world_state(player)
    if not (len(worlds) >= 2 or get_world_support_level(player) >= 1):
        return await callback.answer("Фокус биома откроется, когда мир чуть повзрослеет.", show_alert=True)
    if focus_id == "none":
        universe_focus_modes[callback.from_user.id] = None
    else:
        meta = WORLD_FOCUS_OPTIONS.get(focus_id)
        if not meta:
            return await callback.answer("Неизвестный фокус.", show_alert=True)
        if int(player.get("inventory", {}).get(meta["item"], 0) or 0) <= 0:
            return await callback.answer("Для этого фокуса сейчас нет нужного предмета.", show_alert=True)
        universe_focus_modes[callback.from_user.id] = focus_id
    await callback.message.edit_text(
        build_universe_intro_text(player),
        reply_markup=build_universe_keyboard(player),
        parse_mode="Markdown",
    )
    await callback.answer("Фокус обновлён.")


@router.callback_query(F.data.startswith("universe_create:"))
async def universe_create(callback: types.CallbackQuery):
    matter_id, medium_id, life_id = callback.data.split(":")[1:]
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    worlds, _, _ = ensure_world_state(player)

    inventory = player.get("inventory", {})
    required_items = [matter_id, medium_id, life_id]
    focus_id = universe_focus_modes.get(callback.from_user.id, None)
    advanced_focus_unlocked = len(worlds) >= 2 or get_world_support_level(player) >= 1
    if not advanced_focus_unlocked:
        focus_id = None
    focus_meta = WORLD_FOCUS_OPTIONS.get(focus_id or "")
    if any(int(inventory.get(item_id, 0) or 0) <= 0 for item_id in required_items):
        return await callback.answer("Не хватает одной из частей биома. Сначала докрути её в Лаборатории.", show_alert=True)
    if focus_meta and int(inventory.get(focus_meta["item"], 0) or 0) <= 0:
        return await callback.answer("Фокус выбран, но нужный предмет уже закончился. Переключи режим или докрафти его.", show_alert=True)

    attempt_number = len(worlds) + 1
    success, capacity, absurdity = evaluate_world_creation(
        player,
        {"matter": matter_id, "medium": medium_id, "life": life_id},
        focus_id=focus_id,
    )
    for item_id in required_items:
        inventory[item_id] -= 1
        if inventory[item_id] <= 0:
            del inventory[item_id]
    if focus_meta:
        inventory[focus_meta["item"]] -= 1
        if inventory[focus_meta["item"]] <= 0:
            del inventory[focus_meta["item"]]

    name, description, _, _, humor = build_starter_world_preview(
        {"matter": matter_id, "medium": medium_id, "life": life_id},
        focus_id=focus_id,
    )
    archetype = WORLD_ARCHETYPES.get((matter_id, medium_id, life_id), DEFAULT_WORLD_ARCHETYPE)
    if not success:
        refund_note = ""
        if attempt_number == 2:
            for item_id in required_items:
                inventory[item_id] = inventory.get(item_id, 0) + 1
            if focus_meta:
                inventory[focus_meta["item"]] = inventory.get(focus_meta["item"], 0) + 1
            refund_note = "Вторая попытка учебная, поэтому карман выплюнул всё назад без штрафа."
        else:
            refund_items = required_items[:2]
            for item_id in refund_items:
                inventory[item_id] = inventory.get(item_id, 0) + 1
            refund_note = f"Удалось вытащить обратно только {', '.join(ELEMENTS.get(item_id, item_id) for item_id in refund_items)}."

        await db.alchemy_players.update_one(
            {"user_id": callback.from_user.id},
            {
                "$set": {
                    "inventory": inventory,
                    "world_failures": int(player.get("world_failures", 0) or 0) + 1,
                    "world_tutorial": {"active": True, "created": bool(worlds)},
                }
            },
        )
        await callback.message.edit_text(
            "💥 **БАМММ! БИОМ НЕ УДЕРЖАЛСЯ**\n\n"
            f"Попытка #{attempt_number} захлопнулась на сочетании **{name}**.\n"
            f"Абсурдность была `{absurdity}`, каркас недовольно хрустнул и отказался это держать.\n\n"
            f"{refund_note}\n"
            "Попробуй более спокойную сборку или сначала укрепи каркас мира.",
            reply_markup=build_universe_keyboard(await get_or_create_player(callback.from_user.id, callback.from_user.first_name)),
            parse_mode="Markdown",
        )
        return await callback.answer("Биом бахнул.")

    reward_item = random.choice(STARTER_WORLD_REWARD_POOL) if attempt_number == 1 else None
    if reward_item:
        inventory[reward_item] = inventory.get(reward_item, 0) + 1

    worlds.append(
        {
            "id": "starter_microbiome" if attempt_number == 1 else f"world_{attempt_number}",
            "name": name,
            "description": description,
            "tier": 1,
            "capacity": capacity,
            "absurdity": absurdity,
            "support_bonus": archetype["support_bonus"] + (focus_meta["support_bonus"] if focus_meta else 0),
            "strain_cost": max(1, archetype["strain_cost"] + (focus_meta["strain_delta"] if focus_meta else 0)),
            "status": "stable",
            "humor": humor,
            "archetype": archetype["id"],
            "focus": focus_id or "",
            "focus_label": focus_meta["label"] if focus_meta else "",
            "slots": {"matter": matter_id, "medium": medium_id, "life": life_id},
            "role": archetype["role"],
            "starter": attempt_number == 1,
        }
    )

    await db.alchemy_players.update_one(
        {"user_id": callback.from_user.id},
        {
            "$set": {
                "inventory": inventory,
                "worlds": worlds,
                "tutorial_step": max(3, int(player.get("tutorial_step", 0) or 0)),
                "world_tutorial": {"active": True, "created": True},
            }
        },
    )
    refreshed_player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    overloaded_note = (
        f"\n\n⚠️ Мир уже перегружен на **{abs(get_world_strain_left(refreshed_player))}**. Следующий разумный шаг: укрепить каркас."
        if is_world_overloaded(refreshed_player)
        else ""
    )
    success_text = "🌍 **МИКРОБИОМ СТАБИЛИЗИРОВАН**\n\n" if attempt_number == 1 else "🌍 **НОВЫЙ БИОМ ЗАКРЕПЛЁН**\n\n"
    success_text += f"Создано: **{name}**\n{description}\n\n"
    if reward_item:
        success_text += f"Награда за первый успешный мир: **{ELEMENTS.get(reward_item, reward_item)} x1**\n"
    success_text += (
        "Теперь у тебя есть опора под ногами и повод строить осмысленно."
        if attempt_number == 1
        else "Карман расширился, но за всё это уже начинает расплачиваться общий каркас мира."
    )
    success_text += overloaded_note
    await callback.message.edit_text(
        success_text,
        reply_markup=build_universe_keyboard(refreshed_player),
        parse_mode="Markdown",
    )
    await callback.answer("Биом закреплён.")


@router.callback_query(F.data == "universe_fortify")
async def universe_fortify(callback: types.CallbackQuery):
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    worlds, _, _ = ensure_world_state(player)
    if not worlds:
        return await callback.answer("Сначала нужен хотя бы один биом.", show_alert=True)

    inventory = player.get("inventory", {})
    cost = get_world_support_cost(player)
    if not can_pay_cost(inventory, cost):
        missing = ", ".join(f"{ELEMENTS.get(item_id, item_id)} x{amount}" for item_id, amount in cost.items())
        return await callback.answer(f"Не хватает ресурсов на укрепление: {missing}.", show_alert=True)

    spend_cost(inventory, cost)
    next_level = get_world_support_level(player) + 1
    await db.alchemy_players.update_one(
        {"user_id": callback.from_user.id},
        {"$set": {"inventory": inventory, "world_support_level": next_level}},
    )
    refreshed_player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    await callback.message.edit_text(
        build_universe_intro_text(refreshed_player),
        reply_markup=build_universe_keyboard(refreshed_player),
        parse_mode="Markdown",
    )
    await callback.answer(f"Каркас укреплён. Уровень опоры: {next_level}.")

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
