import asyncio
import html
import random
import time
import asyncio

from aiogram import Router, types, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import CommandStart, CommandObject, Command
from ai_logic import generate_with_retry
from database import db
from handlers.alc_recipes import ELEMENTS
from handlers.pole_chudes import delete_after_delay
from handlers.alc_achievements import ACHIEVEMENTS, format_reward_label
from handlers.alc_core import (
    STARTER_WORLD_REWARD_BUNDLE,
    DEFAULT_WORLD_ARCHETYPE,
    STARTER_BIOME_SLOTS,
    STARTER_WORLD_REWARD_POOL,
    WORLD_DAILY_SPIRIT_TOKEN,
    WORLD_ARCHETYPES,
    WORLD_LAYER_ORDER,
    WORLD_FOCUS_OPTIONS,
    build_world_blueprint,
    build_starter_world_preview,
    build_base_resource_refund,
    build_world_daily_income,
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
    get_world_daily_income,
    get_world_support_cost,
    get_world_support_level,
    get_world_creation_penalty_percent,
    get_biome_slot_options,
    has_legacy_world_layout,
    is_tutorial_biome,
    is_world_overloaded,
    get_or_create_player,
    resolve_world_archetype,
    get_stamina_ready_at,
    run_alchemy_intro,
    send_expedition_menu,
)
from handlers.alc_fighters import can_pay_cost, spend_cost

router = Router()
universe_focus_modes: dict[int, str | None] = {}
universe_biome_drafts: dict[int, dict[str, dict[str, int]]] = {}
universe_active_layers: dict[int, str] = {}
universe_add_steps: dict[int, int] = {}
TUTORIAL_LAYER_HINTS = {
    "foundation": {"stone": 2, "wood": 2},
    "medium": {"swamp": 1, "algae": 1},
    "energy": {"steam": 1, "energy": 1},
    "life": {"moss": 1, "life": 1},
}


async def safe_edit_text(message: types.Message, text: str, reply_markup=None, parse_mode: str | None = "Markdown"):
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise

ALCHEMY_ALPHA_VERSION = "0.9.0.2"
ALCHEMY_CHANGELOG = [
    ("0.9.0.2", [
        "Первый биом снова стал учебным микробиомом: ресурсы на него выдаются заранее, он строится без риска и закрывает только вводный этап базы.",
        "После учебного микробиома игрок получает нормальный стартовый набор под раннюю игру, а со второго биома возвращается риск БАМММ с честным возвратом вложений.",
        "Пассивный доход разведен по ролям заново: учебный микробиом приводит 1 случайного духа в сутки, а обычные биомы снова дают разные бонусы и ресурсы по своему типу.",
    ]),
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
    if is_world_overloaded(player):
        return f"🌍 Мир: 🔴 {main_world.get('name', 'Безымянный карман')} | место {used}/{total} ({used}/{total} вместимости) | нагрузка {strain}/{limit} | опора {support_bonus:+d}"
    return f"🌍 Мир: 🟢 {main_world.get('name', 'Безымянный карман')} | место {used}/{total} ({used}/{total} вместимости) | нагрузка {strain}/{limit} | опора {support_bonus:+d}"


def get_universe_draft(user_id: int | None) -> dict[str, dict[str, int]]:
    raw_draft = universe_biome_drafts.get(user_id or 0, {})
    draft: dict[str, dict[str, int]] = {}
    for layer_id in WORLD_LAYER_ORDER:
        bucket: dict[str, int] = {}
        for item_id, amount in dict(raw_draft.get(layer_id, {})).items():
            amount_int = max(0, int(amount or 0))
            if amount_int > 0:
                bucket[str(item_id)] = amount_int
        draft[layer_id] = bucket
    return draft


def draft_is_complete(draft: dict[str, dict[str, int]]) -> bool:
    return all(bool(draft.get(layer_id, {})) for layer_id in WORLD_LAYER_ORDER)


def get_active_universe_layer(user_id: int | None) -> str:
    active_layer = str(universe_active_layers.get(user_id or 0) or WORLD_LAYER_ORDER[0])
    return active_layer if active_layer in WORLD_LAYER_ORDER else WORLD_LAYER_ORDER[0]


def get_layer_options_for_player(player: dict, layer_id: str) -> dict[str, dict]:
    worlds, _, _ = ensure_world_state(player)
    return get_biome_slot_options(layer_id, tutorial_only=not bool(worlds))


def build_draft_cost(draft: dict[str, dict[str, int]], focus_id: str | None = None) -> dict[str, int]:
    total: dict[str, int] = {}
    for layer_id in WORLD_LAYER_ORDER:
        layer_value = draft.get(layer_id, {})
        if isinstance(layer_value, str):
            option = STARTER_BIOME_SLOTS.get(layer_id, {}).get(layer_value)
            if option:
                for item_id, amount in option.get("cost", {}).items():
                    total[item_id] = total.get(item_id, 0) + int(amount or 0)
            continue
        for item_id, amount in dict(layer_value).items():
            total[item_id] = total.get(item_id, 0) + int(amount or 0)
    focus = WORLD_FOCUS_OPTIONS.get(focus_id or "")
    if focus:
        total[focus["item"]] = total.get(focus["item"], 0) + 1
    return total


def build_draft_cost_lines(inventory: dict, cost: dict[str, int]) -> list[str]:
    lines: list[str] = []
    for item_id, amount in cost.items():
        have = int(inventory.get(item_id, 0) or 0)
        marker = "✅" if have >= amount else "❌"
        lines.append(f"• {marker} {ELEMENTS.get(item_id, item_id)}: {have}/{amount}")
    return lines or ["• Бесплатно."]


def build_daily_income_lines(income: dict[str, int]) -> list[str]:
    lines: list[str] = []
    for item_id, amount in income.items():
        if item_id == WORLD_DAILY_SPIRIT_TOKEN:
            lines.append(f"• Случайный дух x{amount}/день")
        else:
            lines.append(f"• {ELEMENTS.get(item_id, item_id)} x{amount}/день")
    return lines


def get_layer_resource_pool(player: dict, layer_id: str) -> list[str]:
    worlds, _, _ = ensure_world_state(player)
    if not worlds and layer_id in TUTORIAL_LAYER_HINTS:
        return list(TUTORIAL_LAYER_HINTS[layer_id].keys())
    items: list[str] = []
    for meta in get_layer_options_for_player(player, layer_id).values():
        for item_id in meta.get("cost", {}):
            if item_id not in items:
                items.append(item_id)
    return items


def get_remaining_inventory_amount(inventory: dict, draft: dict[str, dict[str, int]], item_id: str) -> int:
    allocated = sum(int(draft.get(layer_id, {}).get(item_id, 0) or 0) for layer_id in WORLD_LAYER_ORDER)
    return max(0, int(inventory.get(item_id, 0) or 0) - allocated)


def is_tutorial_world_open(player: dict) -> bool:
    worlds, _, _ = ensure_world_state(player)
    return not bool(worlds)


def get_tutorial_layer_target(layer_id: str, item_id: str) -> int:
    return int(TUTORIAL_LAYER_HINTS.get(layer_id, {}).get(item_id, 0) or 0)


def format_layer_bucket(bucket: dict[str, int]) -> str:
    if not bucket:
        return "пусто"
    return ", ".join(f"{ELEMENTS.get(item_id, item_id)} x{amount}" for item_id, amount in sorted(bucket.items()))


def build_layer_hint_line(blueprint: dict, layer_id: str) -> str:
    report = dict(blueprint.get("reports", {}).get(layer_id, {}))
    invested = int(report.get("invested", 0) or 0)
    baseline = max(1, int(report.get("baseline", 0) or 0))
    foreign = int(report.get("foreign", 0) or 0)
    if invested <= 0:
        return "не заложен"

    if invested <= baseline:
        size_hint = "малый"
    elif invested <= baseline * 2:
        size_hint = "средний"
    else:
        size_hint = "крупный"

    ratio = invested / baseline
    if ratio < 0.8:
        density_hint = "рыхлый"
    elif ratio < 1.5:
        density_hint = "ровный"
    else:
        density_hint = "плотный"

    if foreign <= 0:
        stability_hint = "держится уверенно"
    elif foreign <= max(1, baseline // 3):
        stability_hint = "неплохо держится"
    elif foreign <= baseline:
        stability_hint = "уже спорный"
    else:
        stability_hint = "явно нервный"

    return f"размер {size_hint}, плотность {density_hint}, устойчивость {stability_hint}"


def build_blueprint_hint_lines(player: dict, blueprint: dict) -> list[str]:
    capacity = int(blueprint.get("capacity", 0) or 0)
    absurdity = int(blueprint.get("absurdity", 0) or 0)
    fail_chance = int(blueprint.get("fail_chance", 0) or 0)
    worlds, _, _ = ensure_world_state(player)
    if capacity <= 0:
        return []

    if capacity <= 4:
        size_hint = "карманный"
    elif capacity <= 7:
        size_hint = "малый"
    elif capacity <= 10:
        size_hint = "средний"
    elif capacity <= 13:
        size_hint = "крупный"
    else:
        size_hint = "тяжёлый"

    if fail_chance <= 0:
        stability_hint = "почти гарантированно удержится"
    elif fail_chance <= 20:
        stability_hint = "выглядит терпимо"
    elif fail_chance <= 45:
        stability_hint = "риск уже чувствуется"
    else:
        stability_hint = "очень нервная сборка"

    if absurdity <= 2:
        absurd_hint = "низкая"
    elif absurdity <= 5:
        absurd_hint = "заметная"
    else:
        absurd_hint = "высокая"

    lines = [
        f"• Общий размер: {size_hint}",
        f"• Стабильность: {stability_hint}",
        f"• Абсурдность: {absurd_hint}",
    ]
    if worlds:
        lines.append(f"• Риск БАМММ: {fail_chance}%")
    return lines


async def generate_world_name_with_ai(blueprint: dict, fallback_name: str) -> str:
    if blueprint.get("is_tutorial"):
        return fallback_name
    try:
        role = str(blueprint.get("archetype", {}).get("role") or "универсальный")
        capacity = int(blueprint.get("capacity", 0) or 0)
        absurdity = int(blueprint.get("absurdity", 0) or 0)
        prompt = (
            "Придумай короткое название биома для алхимической игры. "
            f"Роль: {role}. Размер: {capacity}. Абсурдность: {absurdity}. "
            "Нужно 2-4 слова по-русски, без кавычек и без пояснений."
        )
        generated = await asyncio.wait_for(generate_with_retry(prompt, is_game=True, use_fast_model=True), timeout=8)
        if not generated:
            return fallback_name
        candidate = generated.strip().splitlines()[0].strip("`\"'•- ").strip()
        return candidate[:48] if candidate else fallback_name
    except Exception:
        return fallback_name


def can_activate_base_from_universe(player: dict) -> bool:
    inventory = player.get("inventory", {})
    worlds, _, _ = ensure_world_state(player)
    return (
        int(player.get("tutorial_step", 0) or 0) >= 1
        and int(inventory.get("active_base", 0) or 0) <= 0
        and not worlds
    )


def can_reset_legacy_worlds(player: dict) -> bool:
    worlds, _, _ = ensure_world_state(player)
    return (
        int(player.get("tutorial_step", 0) or 0) >= 1
        and bool(worlds)
        and not bool(player.get("world_legacy_reset_used", False))
        and has_legacy_world_layout(player)
    )


def build_universe_intro_text(player: dict) -> str:
    inventory = player.get("inventory", {})
    worlds, _, _ = ensure_world_state(player)
    world_tutorial = dict(player.get("world_tutorial", {}))
    current_focus = universe_focus_modes.get(player.get("user_id"), None)
    draft = get_universe_draft(player.get("user_id"))
    active_layer = get_active_universe_layer(player.get("user_id"))
    add_step = int(universe_add_steps.get(player.get("user_id", 0), 5) or 5)
    blueprint = build_world_blueprint(player, draft, focus_id=current_focus)
    layer_names = {
        "foundation": "Основа",
        "medium": "Среда",
        "energy": "Энергия",
        "life": "Жизнь",
    }
    if inventory.get("active_base", 0) <= 0:
        if can_reset_legacy_worlds(player):
            return (
                "🌍 **МОДУЛЬ ВСЕЛЕННАЯ**\n\n"
                "Старый мир найден, но профиль ещё не переведён на новую базовую схему.\n"
                "Жми кнопку ниже: одноразовая активация кристалла сбросит только старые биомы, включит базу и переведёт тебя на новую слойную сборку мира."
            )
        if can_activate_base_from_universe(player):
            return (
                "🌍 **МОДУЛЬ ВСЕЛЕННАЯ**\n\n"
                "Старый контур базы найден, но кристалл в инвентаре не пережил миграцию.\n"
                "Жми кнопку ниже: прямой запуск развернёт базу и сразу переведёт тебя к первому биому и его строительству."
            )
        return (
            "🌍 **МОДУЛЬ ВСЕЛЕННАЯ**\n\n"
            "Сначала активируй 🔮 Кристалл Базы на складе. Без него здесь нет мира, а значит и развивать базу пока негде."
        )

    top_lines = ["🌍 **МОДУЛЬ ВСЕЛЕННАЯ**", ""]
    tutorial_mode = not bool(worlds)
    if worlds:
        world = worlds[0]
        extra_worlds = max(0, len(worlds) - 1)
        reset_notice = (
            "\n\n🔮 Для старого мира доступна одноразовая активация кристалла: она сбросит только биомы и переведёт тебя на новую слойную схему сборки."
            if can_reset_legacy_worlds(player)
            else ""
        )
        archetype_lines = []
        for item in worlds[:4]:
            focus_suffix = f" + {item.get('focus_label')}" if item.get("focus_label") else ""
            daily_income = dict(item.get("daily_income", {}))
            if daily_income.get(WORLD_DAILY_SPIRIT_TOKEN):
                daily_label = "1 случайный дух/день"
            else:
                daily_label = ", ".join(f"{ELEMENTS.get(item_id, item_id)} x{amount}/день" for item_id, amount in daily_income.items()) or "без ежедневного дохода"
            archetype_lines.append(
                f"• {item.get('name', 'Безымянный биом')} [{item.get('role', 'универсальный')}{focus_suffix}]"
                + f" -> размер {int(item.get('capacity', 0) or 0)}, нагрузка {int(item.get('strain_cost', 0) or 0)}, опора {int(item.get('support_bonus', 0) or 0):+d}, доход {daily_label}"
            )
        daily_lines = build_daily_income_lines(get_world_daily_income(player))
        overload_line = (
            f"\n⚠️ Перегруз: **{abs(get_world_strain_left(player))}**. Мир надо укрепить, прежде чем бездумно наращивать стройку."
            if is_world_overloaded(player)
            else ""
        )
        top_lines.extend([
            "Биомы это фундамент базы: они дают место под здания и задают специализацию мира.\n\n"
            f"Ядро мира: **{world.get('name', 'Безымянный мир')}**\n"
            f"{world.get('description', 'Описание пока переживает миграцию.')}\n\n"
            f"Биомов собрано: **{len(worlds)}**"
            + (f" (+{extra_worlds} дополнительных)" if extra_worlds else "")
            + "\n"
            f"Места под развитие: **{get_world_capacity_used(player)}/{get_world_capacity_total(player)}** занято\n"
            f"Свободно: **{max(0, get_world_capacity_left(player))}**\n"
            f"Нагрузка на мир: **{get_world_strain(player)}/{get_world_stability_limit(player)}**\n"
            f"Уровень укрепления: **{get_world_support_level(player)}**\n"
            "Собранные биомы:\n" + "\n".join(archetype_lines),
        ])
        if daily_lines:
            top_lines.extend(["", "Ежедневный доход мира:", *daily_lines])
        top_lines.extend(["", f"Статус: _{world.get('humor', 'мир держится и выполняет свою работу')}_"])
        if reset_notice:
            top_lines.append(reset_notice)
        if overload_line:
            top_lines.append(overload_line)
        top_lines.extend(["", "━━━━━━━━", ""])
    else:
        top_lines.extend([
            "🫧 **УЧЕБНЫЙ МИКРОБИОМ**",
            "Это короткий вводный этап: собери маленький стабильный биом и открой базу.",
            "Здесь нельзя испортить чертёж лишними ресурсами: учебный режим пускает только нужные материалы и только в нужный слой.",
            "",
        ])
        if not bool(world_tutorial.get("hints_shown", False)):
            top_lines.extend([
                "🧭 Одноразовые подсказки для учебного микробиома:",
                "• Основа: начни с Камня и Древесины.",
                "• Среда: влей Болото и Водоросли.",
                "• Энергия: добавь Пар и Энергию.",
                "• Жизнь: закрепи Мохом и Жизнью.",
                "• Стартовых ресурсов на этот учебный набор хватает, если собрать их по подсказанным рецептам.",
                "",
            ])

    draft_cost = build_draft_cost(draft, current_focus)
    lines = list(top_lines)
    lines.extend([
        f"Активный слой: **{layer_names[active_layer]}**" + (f" | шаг добавления: **{add_step}**" if not tutorial_mode else ""),
        "",
        "Слои чертежа:",
    ])
    for layer_id in WORLD_LAYER_ORDER:
        bucket = draft.get(layer_id, {})
        marker = "👉" if layer_id == active_layer else "•"
        lines.append(f"{marker} {layer_names[layer_id]}: {format_layer_bucket(bucket)}")
        lines.append(f"   {build_layer_hint_line(blueprint, layer_id)}")

    if current_focus and not tutorial_mode:
        focus_meta = WORLD_FOCUS_OPTIONS.get(current_focus)
        if focus_meta:
            lines.extend(["", f"Фокус биома: **{focus_meta['label']}**"])

    if draft_cost:
        lines.extend(["", "Цена текущей закладки:", *build_draft_cost_lines(inventory, draft_cost)])

    hint_lines = build_blueprint_hint_lines(player, blueprint)
    if hint_lines:
        lines.extend(["", "Предварительная оценка:", *hint_lines])

    pool = get_layer_resource_pool(player, active_layer)
    if pool:
        pool_lines = []
        for item_id in pool:
            free_amount = get_remaining_inventory_amount(inventory, draft, item_id)
            invested = int(draft.get(active_layer, {}).get(item_id, 0) or 0)
            if tutorial_mode:
                target = get_tutorial_layer_target(active_layer, item_id)
                pool_lines.append(f"• {ELEMENTS.get(item_id, item_id)}: свободно {free_amount}, в слое {invested}/{target}")
            else:
                pool_lines.append(f"• {ELEMENTS.get(item_id, item_id)}: свободно {free_amount}, в слое {invested}")
        lines.extend(["", "По активному слою:", *pool_lines[:8]])

    lines.extend([
        "",
        "💡 Нажми слой, затем накидывай в него ресурсы." + (
            " В учебном режиме достаточно просто пройтись по подсказанным материалам."
            if tutorial_mode
            else " Точное имя и тип биома не раскрываются до стабилизации."
        ),
    ])
    return "\n".join([line for line in lines if line])


def build_universe_keyboard(player: dict) -> types.InlineKeyboardMarkup:
    inventory = player.get("inventory", {})
    worlds, _, _ = ensure_world_state(player)
    tutorial_mode = not bool(worlds)
    builder = InlineKeyboardBuilder()
    advanced_focus_unlocked = len(worlds) >= 2 or get_world_support_level(player) >= 1
    current_focus = universe_focus_modes.get(player.get("user_id"), None)
    draft = get_universe_draft(player.get("user_id"))
    active_layer = get_active_universe_layer(player.get("user_id"))

    if can_activate_base_from_universe(player):
        builder.button(text="🔮 Активировать Кристалл", callback_data="universe_activate_base")
    elif can_reset_legacy_worlds(player):
        builder.button(text="🔮 Активировать Кристалл Сброса", callback_data="universe_reset_legacy")
    elif inventory.get("active_base", 0) > 0:
        if advanced_focus_unlocked and not tutorial_mode:
            focus_label = WORLD_FOCUS_OPTIONS.get(current_focus or "", {}).get("label", "без фокуса")
            builder.button(text=f"🎯 Фокус нового биома: {focus_label}", callback_data="universe_focus:none")
            for focus_id, meta in WORLD_FOCUS_OPTIONS.items():
                count = int(inventory.get(meta["item"], 0) or 0)
                if count > 0:
                    builder.button(
                        text=f"{meta['label']} x{count}",
                        callback_data=f"universe_focus:{focus_id}",
                    )
        layer_names = {
            "foundation": "Основа",
            "medium": "Среда",
            "energy": "Энергия",
            "life": "Жизнь",
        }
        for layer_id in WORLD_LAYER_ORDER:
            invested_total = sum(int(amount or 0) for amount in draft.get(layer_id, {}).values())
            marker = "👉" if layer_id == active_layer else "▫️"
            builder.button(
                text=f"{marker} {layer_names[layer_id]} | {invested_total}",
                callback_data=f"universe_layer:{layer_id}",
            )
        if not tutorial_mode:
            for step in (1, 5, 10):
                marker = "✅" if int(universe_add_steps.get(player.get("user_id", 0), 5) or 5) == step else "▫️"
                builder.button(text=f"{marker} шаг {step}", callback_data=f"universe_step:{step}")
        for item_id in get_layer_resource_pool(player, active_layer):
            free_amount = get_remaining_inventory_amount(inventory, draft, item_id)
            invested = int(draft.get(active_layer, {}).get(item_id, 0) or 0)
            builder.button(
                text=f"{ELEMENTS.get(item_id, item_id)} {free_amount} [{invested}]",
                callback_data=f"universe_add:{active_layer}:{item_id}",
            )
        builder.button(text="🧹 Очистить слой", callback_data=f"universe_clear:{active_layer}")
        if not tutorial_mode:
            builder.button(text="♻️ Сбросить весь чертёж", callback_data="universe_reset_draft")
        if draft_is_complete(draft):
            builder.button(text="🧬 Стабилизировать биом", callback_data="universe_create")
        if not tutorial_mode:
            support_cost = get_world_support_cost(player)
            cost_label = ", ".join(f"{ELEMENTS.get(item_id, item_id)} x{amount}" for item_id, amount in support_cost.items())
            builder.button(text=f"🛠 Укрепить мир | {cost_label}", callback_data="universe_fortify")
        builder.button(text="⚗️ В Лабораторию", callback_data="lab_open")

    builder.button(text="🔙 В Штаб", callback_data="alc:base_return")
    if tutorial_mode:
        builder.adjust(2, 2, 2, 2, 1, 1, 1)
    else:
        builder.adjust(2, 3, 2, 2, 2, 1, 1, 1)
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
    await safe_edit_text(callback.message, text, reply_markup=markup, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("alc:changelog:"))
async def show_changelog_page(callback: types.CallbackQuery):
    page = int(callback.data.split(":")[2])
    text, markup = build_changelog_page(page)
    await safe_edit_text(callback.message, text, reply_markup=markup, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "alc:base_help")
async def show_base_help(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 В Штаб", callback_data="alc:base_return")
    builder.adjust(1)
    await safe_edit_text(callback.message, 
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
    
    await safe_edit_text(callback.message, text, reply_markup=builder.as_markup(), parse_mode="Markdown")


@router.callback_query(F.data == "universe_open")
async def universe_open(callback: types.CallbackQuery):
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    universe_focus_modes.setdefault(callback.from_user.id, None)
    universe_biome_drafts.setdefault(callback.from_user.id, get_universe_draft(callback.from_user.id))
    universe_active_layers.setdefault(callback.from_user.id, WORLD_LAYER_ORDER[0])
    universe_add_steps.setdefault(callback.from_user.id, 5)
    worlds, _, _ = ensure_world_state(player)
    tutorial = dict(player.get("world_tutorial", {}))
    if not worlds and not bool(tutorial.get("hints_shown", False)):
        tutorial["active"] = bool(tutorial.get("active", False))
        tutorial["created"] = bool(tutorial.get("created", False))
        tutorial["hints_shown"] = True
        await db.alchemy_players.update_one(
            {"user_id": callback.from_user.id},
            {"$set": {"world_tutorial": tutorial}},
        )
        player["world_tutorial"] = tutorial
    await safe_edit_text(callback.message, 
        build_universe_intro_text(player),
        reply_markup=build_universe_keyboard(player),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data == "universe_activate_base")
async def universe_activate_base(callback: types.CallbackQuery):
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    if not can_activate_base_from_universe(player):
        return await callback.answer("База уже активирована или этот обход больше не нужен.", show_alert=True)

    inventory = dict(player.get("inventory", {}))
    inventory.pop("base_crystal", None)
    inventory["active_base"] = 1
    await db.alchemy_players.update_one(
        {"user_id": callback.from_user.id},
        {"$set": {"inventory": inventory, "tutorial_step": max(2, int(player.get("tutorial_step", 0) or 0)), "world_tutorial": {"active": True, "created": False, "hints_shown": False}}},
    )
    refreshed_player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    await safe_edit_text(callback.message, 
        build_universe_intro_text(refreshed_player),
        reply_markup=build_universe_keyboard(refreshed_player),
        parse_mode="Markdown",
    )
    await callback.answer("База развернута. Теперь можно собирать первый биом.")


@router.callback_query(F.data == "universe_reset_legacy")
async def universe_reset_legacy(callback: types.CallbackQuery):
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    if not can_reset_legacy_worlds(player):
        return await callback.answer("Одноразовый сброс биомов уже использован или для этого профиля не нужен.", show_alert=True)

    universe_focus_modes[callback.from_user.id] = None
    universe_biome_drafts[callback.from_user.id] = {layer_id: {} for layer_id in WORLD_LAYER_ORDER}
    inventory = dict(player.get("inventory", {}))
    inventory.pop("base_crystal", None)
    inventory["active_base"] = 1
    await db.alchemy_players.update_one(
        {"user_id": callback.from_user.id},
        {
            "$set": {
                "inventory": inventory,
                "worlds": [],
                "world_failures": 0,
                "world_support_level": 0,
                "world_tutorial": {"active": True, "created": False, "hints_shown": False},
                "world_legacy_reset_used": True,
                "tutorial_step": max(2, int(player.get("tutorial_step", 0) or 0)),
            }
        },
    )
    refreshed_player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    await safe_edit_text(callback.message, 
        build_universe_intro_text(refreshed_player),
        reply_markup=build_universe_keyboard(refreshed_player),
        parse_mode="Markdown",
    )
    await callback.answer("Старые биомы сброшены. Теперь мир можно собрать заново по новой схеме.")


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
    await safe_edit_text(callback.message, 
        build_universe_intro_text(player),
        reply_markup=build_universe_keyboard(player),
        parse_mode="Markdown",
    )
    await callback.answer("Фокус обновлён.")


@router.callback_query(F.data.startswith("universe_layer:"))
async def universe_select_layer(callback: types.CallbackQuery):
    layer_id = callback.data.split(":")[1]
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    if layer_id not in WORLD_LAYER_ORDER:
        return await callback.answer("Неизвестный слой.", show_alert=True)
    universe_active_layers[callback.from_user.id] = layer_id
    await safe_edit_text(callback.message, 
        build_universe_intro_text(player),
        reply_markup=build_universe_keyboard(player),
        parse_mode="Markdown",
    )
    await callback.answer("Слой выбран.")


@router.callback_query(F.data.startswith("universe_step:"))
async def universe_set_step(callback: types.CallbackQuery):
    step = int(callback.data.split(":")[1])
    if step not in (1, 5, 10):
        return await callback.answer("Шаг недоступен.", show_alert=True)
    universe_add_steps[callback.from_user.id] = step
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    await safe_edit_text(callback.message, 
        build_universe_intro_text(player),
        reply_markup=build_universe_keyboard(player),
        parse_mode="Markdown",
    )
    await callback.answer(f"Шаг: {step}")


@router.callback_query(F.data.startswith("universe_add:"))
async def universe_add_resource(callback: types.CallbackQuery):
    _, layer_id, item_id = callback.data.split(":")
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    if layer_id not in WORLD_LAYER_ORDER or item_id not in get_layer_resource_pool(player, layer_id):
        return await callback.answer("Этот ресурс сюда не кладётся.", show_alert=True)

    draft = get_universe_draft(callback.from_user.id)
    inventory = player.get("inventory", {})
    free_amount = get_remaining_inventory_amount(inventory, draft, item_id)
    if free_amount <= 0:
        return await callback.answer("Этого ресурса больше не осталось свободным.", show_alert=True)

    if is_tutorial_world_open(player):
        target = get_tutorial_layer_target(layer_id, item_id)
        if target <= 0:
            return await callback.answer("Для учебного микробиома этот ресурс сюда не нужен.", show_alert=True)
        invested_now = int(draft[layer_id].get(item_id, 0) or 0)
        if invested_now >= target:
            return await callback.answer("Для учебного микробиома этого ресурса в слое уже достаточно.", show_alert=True)
        add_amount = min(target - invested_now, free_amount)
    else:
        step = int(universe_add_steps.get(callback.from_user.id, 5) or 5)
        add_amount = min(step, free_amount)

    draft[layer_id][item_id] = int(draft[layer_id].get(item_id, 0) or 0) + add_amount
    universe_biome_drafts[callback.from_user.id] = draft
    universe_active_layers[callback.from_user.id] = layer_id
    await safe_edit_text(callback.message, 
        build_universe_intro_text(player),
        reply_markup=build_universe_keyboard(player),
        parse_mode="Markdown",
    )
    await callback.answer(f"В слой добавлено: {ELEMENTS.get(item_id, item_id)} x{add_amount}")


@router.callback_query(F.data.startswith("universe_clear:"))
async def universe_clear_layer(callback: types.CallbackQuery):
    layer_id = callback.data.split(":")[1]
    if layer_id not in WORLD_LAYER_ORDER:
        return await callback.answer("Неизвестный слой.", show_alert=True)
    draft = get_universe_draft(callback.from_user.id)
    if not draft.get(layer_id):
        universe_active_layers[callback.from_user.id] = layer_id
        return await callback.answer("Слой уже пуст.")
    draft[layer_id] = {}
    universe_biome_drafts[callback.from_user.id] = draft
    universe_active_layers[callback.from_user.id] = layer_id
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    await safe_edit_text(callback.message, 
        build_universe_intro_text(player),
        reply_markup=build_universe_keyboard(player),
        parse_mode="Markdown",
    )
    await callback.answer("Слой очищен.")


@router.callback_query(F.data == "universe_reset_draft")
async def universe_reset_draft(callback: types.CallbackQuery):
    current_draft = get_universe_draft(callback.from_user.id)
    if not any(current_draft.get(layer_id) for layer_id in WORLD_LAYER_ORDER):
        universe_active_layers[callback.from_user.id] = WORLD_LAYER_ORDER[0]
        return await callback.answer("Черновик уже пуст.")
    universe_biome_drafts[callback.from_user.id] = {layer_id: {} for layer_id in WORLD_LAYER_ORDER}
    universe_active_layers[callback.from_user.id] = WORLD_LAYER_ORDER[0]
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    await safe_edit_text(callback.message, 
        build_universe_intro_text(player),
        reply_markup=build_universe_keyboard(player),
        parse_mode="Markdown",
    )
    await callback.answer("Черновик биома сброшен.")


@router.callback_query(F.data == "universe_create")
async def universe_create(callback: types.CallbackQuery):
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    worlds, _, _ = ensure_world_state(player)

    inventory = player.get("inventory", {})
    draft = get_universe_draft(callback.from_user.id)
    if not draft_is_complete(draft):
        return await callback.answer("Сначала собери полный чертёж: основа, среда, энергия и жизнь.", show_alert=True)
    focus_id = universe_focus_modes.get(callback.from_user.id, None)
    advanced_focus_unlocked = len(worlds) >= 2 or get_world_support_level(player) >= 1
    if not advanced_focus_unlocked:
        focus_id = None
    focus_meta = WORLD_FOCUS_OPTIONS.get(focus_id or "")
    total_cost = build_draft_cost(draft, focus_id)
    if not can_pay_cost(inventory, total_cost):
        return await callback.answer("Не хватает ресурсов на выбранные слои биома. Проверь чертёж или докрути ресурсы в Лаборатории.", show_alert=True)
    if focus_meta and int(inventory.get(focus_meta["item"], 0) or 0) <= 0:
        return await callback.answer("Фокус выбран, но нужный предмет уже закончился. Переключи режим или докрафти его.", show_alert=True)

    attempt_number = len(worlds) + 1
    blueprint = build_world_blueprint(player, draft, focus_id=focus_id)
    success, capacity, absurdity = evaluate_world_creation(
        player,
        draft,
        focus_id=focus_id,
    )
    resolved_slots = dict(blueprint.get("slots", {}))
    if not all(resolved_slots.get(layer_id) for layer_id in WORLD_LAYER_ORDER):
        return await callback.answer("Нужно закинуть ресурсы во все четыре слоя.", show_alert=True)

    fallback_name, description, _, _, humor = build_starter_world_preview(
        resolved_slots,
        focus_id=focus_id,
    )
    archetype = dict(blueprint.get("archetype") or WORLD_ARCHETYPES.get(str(resolve_world_archetype(resolved_slots).get("role") or ""), DEFAULT_WORLD_ARCHETYPE))
    support_bonus = int(blueprint.get("support_bonus", 0) or 0)
    strain_cost = int(blueprint.get("strain_cost", 1) or 1)

    for item_id, amount in total_cost.items():
        inventory[item_id] -= amount
        if inventory[item_id] <= 0:
            del inventory[item_id]

    if not success:
        penalty_percent = get_world_creation_penalty_percent(attempt_number)
        refund_ratio = max(0.0, float(100 - penalty_percent) / 100.0)
        refund_cost = build_base_resource_refund(total_cost, ratio=refund_ratio)
        for item_id, amount in refund_cost.items():
            inventory[item_id] = inventory.get(item_id, 0) + amount

        await db.alchemy_players.update_one(
            {"user_id": callback.from_user.id},
            {
                "$set": {
                    "inventory": inventory,
                    "world_failures": int(player.get("world_failures", 0) or 0) + 1,
                    "world_tutorial": {"active": True, "created": bool(worlds), "hints_shown": True},
                }
            },
        )

        refund_lines = [
            f"• {ELEMENTS.get(item_id, item_id)} x{amount}"
            for item_id, amount in sorted(refund_cost.items())
            if amount > 0
        ] or ["• Ничего спасти не удалось."]
        if attempt_number == 2:
            failure_note = "Это первый настоящий риск после учебного микробиома, поэтому мир выплюнул обратно всю стоимость в базовых ресурсах."
        else:
            failure_note = f"Мир откусил **{penalty_percent}%** вложенного и вернул остальное в базовых ресурсах."

        await safe_edit_text(callback.message, 
            "💥 **БИОМ НЕ УДЕРЖАЛСЯ**\n\n"
            f"Попытка #{attempt_number} сорвалась на сочетании **{name}**.\n"
            f"Абсурдность сборки: `{absurdity}`. Нагрузка проекта: `{strain_cost}`.\n\n"
            f"{failure_note}\n\n"
            "Возврат:\n"
            + "\n".join(refund_lines),
            reply_markup=build_universe_keyboard(await get_or_create_player(callback.from_user.id, callback.from_user.first_name)),
            parse_mode="Markdown",
        )
        return await callback.answer("Биом бахнул.")

    name = await generate_world_name_with_ai(blueprint, fallback_name)
    reward_item = random.choice(STARTER_WORLD_REWARD_POOL) if attempt_number == 1 else None
    if reward_item:
        inventory[reward_item] = inventory.get(reward_item, 0) + 1
    if attempt_number == 1:
        for item_id, amount in STARTER_WORLD_REWARD_BUNDLE.items():
            inventory[item_id] = inventory.get(item_id, 0) + amount

    worlds.append(
        {
            "id": "starter_microbiome" if attempt_number == 1 else f"world_{attempt_number}",
            "name": name,
            "description": description,
            "tier": 1,
            "capacity": capacity,
            "absurdity": absurdity,
            "support_bonus": support_bonus,
            "strain_cost": strain_cost,
            "status": "stable",
            "humor": humor,
            "archetype": archetype["id"],
            "focus": focus_id or "",
            "focus_label": focus_meta["label"] if focus_meta else "",
            "daily_income": {WORLD_DAILY_SPIRIT_TOKEN: 1} if attempt_number == 1 and blueprint.get("is_tutorial") else build_world_daily_income(archetype, capacity),
            "slots": resolved_slots,
            "role": archetype["role"],
            "starter": attempt_number == 1,
        }
    )
    universe_biome_drafts[callback.from_user.id] = {layer_id: {} for layer_id in WORLD_LAYER_ORDER}
    universe_active_layers[callback.from_user.id] = WORLD_LAYER_ORDER[0]

    await db.alchemy_players.update_one(
        {"user_id": callback.from_user.id},
        {
            "$set": {
                "inventory": inventory,
                "worlds": worlds,
                "tutorial_step": max(3, int(player.get("tutorial_step", 0) or 0)),
                "world_tutorial": {"active": True, "created": True, "hints_shown": True},
            }
        },
    )
    refreshed_player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    overloaded_note = (
        f"\n\n⚠️ Мир уже перегружен на **{abs(get_world_strain_left(refreshed_player))}**. Следующий шаг: укрепить мир, прежде чем раздувать стройку дальше."
        if is_world_overloaded(refreshed_player)
        else ""
    )
    success_text = "🌍 **МИКРОБИОМ СТАБИЛИЗИРОВАН**\n\n" if attempt_number == 1 else "🌍 **НОВЫЙ БИОМ ЗАКРЕПЛЁН**\n\n"
    success_text += f"Создано: **{name}**\n{description}\n\n"
    if reward_item:
        success_text += f"Награда за первый успешный мир: **{ELEMENTS.get(reward_item, reward_item)} x1**\n"
    if attempt_number == 1:
        reward_lines = [
            f"• {ELEMENTS.get(item_id, item_id)} x{amount}"
            for item_id, amount in STARTER_WORLD_REWARD_BUNDLE.items()
        ]
        success_text += "Стартовый набор после микробиома:\n" + "\n".join(reward_lines) + "\n"
    success_text += (
        "Учебный этап закрыт: теперь у тебя есть мир, стартовый запас под стройку и свобода пробовать обычную игру."
        if attempt_number == 1
        else "Мир стал шире. Подбирай следующий биом уже под конкретную цель: стройка, производство, добыча или походы."
    )
    success_text += overloaded_note
    await safe_edit_text(callback.message, 
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
    await safe_edit_text(callback.message, 
        build_universe_intro_text(refreshed_player),
        reply_markup=build_universe_keyboard(refreshed_player),
        parse_mode="Markdown",
    )
    await callback.answer(f"Мир укреплён. Уровень опоры: {next_level}.")

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
    await safe_edit_text(callback.message, text, reply_markup=markup, parse_mode="Markdown")

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
