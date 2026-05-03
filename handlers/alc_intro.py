import asyncio
import time
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from typing import Any

from aiogram import Router, types, F
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

from database import db
from handlers.alc_core import (
    STARTER_WORLD_RESOURCE_GRANT,
    STARTER_WORLD_REWARD_BUNDLE,
    STARTER_BIOME_SLOTS,
    WORLD_LAYER_ORDER,
    DEFAULT_WORLD_ARCHETYPE,
    build_world_blueprint,
    build_starter_world_preview,
    evaluate_world_creation,
    ensure_world_state,
    get_world_capacity_left,
    get_world_capacity_total,
    get_world_capacity_used,
    get_world_strain,
    get_world_stability_limit,
    get_world_support_level,
    get_or_create_player,
    get_building_world_capacity_cost,
    can_upgrade_with_world_capacity,
    get_heritage_slots,
)
from handlers.alc_recipes import ELEMENTS, RECIPES, BATTLE_UNITS, is_battle_unit
from handlers.alc_fighters import create_fighter, get_barracks_capacity
from handlers.alc_build import BUILDINGS, get_building_status, format_building_cost

router = Router()

# =====================================================================
# СОСТОЯНИЕ ИНТРО (в памяти + персистентность в БД)
# =====================================================================

@dataclass
class IntroSession:
    phase: str = "awakening"           # awakening, synthesis, biome, expedition, building, combat, complete
    sub_step: int = 0                  # Подшаг внутри фазы
    pending_craft: str | None = None   # Ожидаемый результат крафта
    intro_mix: list[str] = field(default_factory=list)  # Временный котёл [item1, item2]
    expedition_count: int = 0            # Счётчик походов в фазе expedition
    biome_filled: set = field(default_factory=set)       # Заполненные слои биома
    return_target: str | None = None     # Куда возвращаться после лаборатории

    def to_dict(self) -> dict:
        """Сериализация для БД"""
        return {
            "phase": self.phase,
            "sub_step": self.sub_step,
            "pending_craft": self.pending_craft,
            "intro_mix": self.intro_mix,
            "expedition_count": self.expedition_count,
            "biome_filled": list(self.biome_filled),
            "return_target": self.return_target,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "IntroSession":
        """Десериализация из БД"""
        session = cls()
        session.phase = data.get("phase", "awakening")
        session.sub_step = data.get("sub_step", 0)
        session.pending_craft = data.get("pending_craft")
        session.intro_mix = data.get("intro_mix", [])
        session.expedition_count = data.get("expedition_count", 0)
        session.biome_filled = set(data.get("biome_filled", []))
        session.return_target = data.get("return_target")
        return session


# Кэш в памяти для быстрого доступа и пагинации
intro_sessions: dict[int, IntroSession] = {}
intro_lab_pages: dict[int, int] = {}
intro_combat_pages: dict[int, int] = {}


async def get_intro_session(user_id: int) -> IntroSession | None:
    """Получает сессию из кэша или восстанавливает из БД."""
    if user_id in intro_sessions:
        return intro_sessions[user_id]

    player = await db.alchemy_players.find_one({"user_id": user_id})
    if player and "intro_state" in player:
        session = IntroSession.from_dict(player["intro_state"])
        intro_sessions[user_id] = session
        return session
    return None


async def save_intro_session(user_id: int, session: IntroSession):
    """Сохраняет сессию в кэш и БД."""
    intro_sessions[user_id] = session
    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {"intro_state": session.to_dict()}}
    )


async def clear_intro_session(user_id: int):
    """Очищает сессию из кэша и БД."""
    intro_sessions.pop(user_id, None)
    intro_lab_pages.pop(user_id, None)
    intro_combat_pages.pop(user_id, None)
    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$unset": {"intro_state": ""}}
    )


# =====================================================================
# КОНСТАНТЫ И ДАННЫЕ
# =====================================================================

INTRO_STARTER_RESOURCES = {
    "w": 150,
    "f": 150,
    "e": 150,
    "a": 150,
}

SYNTHESIS_CHAIN = [
    ("swamp", "Смешай Землю 🪨 и Воду 💧. Из болота начинается всё живое.", 8),
    ("lava", "Огонь 🔥 и Земля 🪨 дадут жидкую породу.", 1),
    ("stone", "Остуди лаву 🌋 водой 💧. Твёрдый камень — кость мироздания.", 1),
    ("energy", "Воздух 💨 и Огонь 🔥. Без энергии нет движения.", 1),
    ("steam", "Вскипяти воду 💧 огнём 🔥. Пар — дыхание механизмов.", 1),
    ("life", "Энергия 🎇 в болоте 🐸 зародит жизнь.", 1),
    ("seed", "Жизнь 🧬 в земле 🪨 даст семя.", 1),
    ("tree", "Семя 🌱 в земле 🪨 вырастет. Дерево — опора и топливо.", 5),
    ("algae", "Жизнь 🧬 в воде 💧 даст водоросли.", 1),
    ("moss", "Водоросли 🌿 в болоте 🐸 станут мхом.", 5),
    ("lake", "Две воды 💧💧 дадут озеро. Глубина для новых духов.", 1),
    ("pressure", "Две земли 🪨🪨 создадут давление. Сила, что ломает и куёт.", 1),
]

RECIPE_MAP = {tuple(sorted(ingredients)): result for ingredients, result in RECIPES.items()}

BIOME_LAYER_COST = {
    "foundation": {"stone": 1, "tree": 1},
    "medium": {"swamp": 1, "moss": 1},
    "energy": {"steam": 1, "energy": 1},
    "life": {"moss": 1, "life": 1},
}

EXPEDITION_REWARDS = {
    "volcano": {"lava": 50, "brick": 50, "stone": 50, "sand": 50},
    "mountains": {"metal": 50, "ashes": 50, "reed": 50, "stone": 50},
}

BUILDING_ORDER = ["storage", "lab", "altar", "barracks"]

TUTORIAL_BUILDING_COST = {
    "storage": {"stone": 2, "sand": 2},
    "lab": {},
    "altar": {"stone": 4, "tree": 2, "energy": 2},
    "barracks": {"stone": 6, "tree": 4, "brick": 2, "energy": 2},
}

COMBAT_CHAIN = [
    ("spirit_f", "Дух Огня: Огонь 🔥 + Жизнь 🧬", True),
    ("spirit_w", "Дух Воды: Озеро 🌊 + Жизнь 🧬", True),
    ("spirit_e", "Дух Земли: Давление 🗜 + Жизнь 🧬", True),
    ("fire_swordsman", "Огнемеч: Металл ⚙️ + Дух Огня 🔥", False),
    ("water_guard", "Водостраж: Камень 🪨 + Дух Воды 💧", False),
    ("earth_shielder", "Щитоносец: Кирпич 🧱 + Дух Земли 🪨", False),
]

# =====================================================================
# ФИЛЬТРЫ
# =====================================================================

class IsInIntroPhase:
    def __init__(self, phase: str | None = None):
        self.phase = phase

    async def __call__(self, callback: types.CallbackQuery) -> bool:
        session = await get_intro_session(callback.from_user.id)
        if not session:
            return False
        if self.phase is None:
            return session.phase in {
                "awakening", "synthesis", "biome",
                "expedition", "building", "combat", "complete"
            }
        return session.phase == self.phase

# =====================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =====================================================================

def get_el_name(k: str) -> str:
    el = ELEMENTS.get(k, k)
    if isinstance(el, str):
        parts = el.split(" ", 1)
        return parts[1] if len(parts) > 1 else el
    return str(k)


def get_el_emoji(k: str) -> str:
    el = ELEMENTS.get(k, k)
    if isinstance(el, str):
        parts = el.split(" ", 1)
        return parts[0] if len(parts) > 1 else "❓"
    return "❓"


def truncate_text(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit - 50] + "\n\n...(сообщение обрезано, слишком длинное)"


async def safe_edit_text(message: types.Message, text: str, reply_markup=None, parse_mode: str | None = "Markdown"):
    text = truncate_text(text)
    try:
        return await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as exc:
        error = str(exc).lower()
        if "message is not modified" in error:
            return None
        if "message can't be edited" in error or "message to edit not found" in error:
            try:
                return await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
            except TelegramBadRequest as exc2:
                if "message is too long" in str(exc2).lower():
                    short_text = truncate_text(text, 3500)
                    return await message.answer(short_text, reply_markup=reply_markup, parse_mode=parse_mode)
                raise
        if "message is too long" in error:
            short_text = truncate_text(text, 3500)
            try:
                return await message.edit_text(short_text, reply_markup=reply_markup, parse_mode=parse_mode)
            except TelegramBadRequest:
                return await message.answer(short_text, reply_markup=reply_markup, parse_mode=parse_mode)
        raise


def get_inventory(player: dict) -> dict:
    return player.get("inventory", {})


def get_discovered(player: dict) -> list[str]:
    return player.get("discovered", [])


def get_buildings(player: dict) -> dict:
    return player.get("buildings", {
        "storage": 0, "lab": 0, "altar": 0, "guild": 0,
        "barracks": 0, "fort": 0, "heritage_center": 1
    })


# =====================================================================
# ТОЧКА ВХОДА И НАСТРОЙКИ СЕТКИ
# =====================================================================

async def run_new_intro(user_id: int, user_name: str, message: types.Message):
    player = await get_or_create_player(user_id, user_name)

    if player.get("tutorial_step", 0) >= 100:
        text = "🎓 Обучение уже пройдено. Отправляйся в Штаб — /base"
        await message.answer(text)
        return

    session = await get_intro_session(user_id)

    if session:
        await _restore_phase(user_id, session, message)
        return

    await run_intro_awakening(user_id, user_name, message)


async def _restore_phase(user_id: int, session: IntroSession, message: types.Message):
    fake_callback = types.CallbackQuery(
        id="restore",
        from_user=types.User(id=user_id, is_bot=False, first_name=""),
        chat_instance="",
        data="restore",
        message=message,
    )

    phase_renderers = {
        "awakening": run_intro_awakening,
        "synthesis": render_synthesis_window,
        "biome": render_biome_window,
        "expedition": render_expedition_window,
        "building": render_building_window,
        "combat": render_combat_window,
        "complete": render_complete_window,
    }

    renderer = phase_renderers.get(session.phase)
    if renderer:
        if session.phase == "awakening":
            await renderer(user_id, "", message)
        else:
            await renderer(fake_callback)
    else:
        await clear_intro_session(user_id)
        await run_intro_awakening(user_id, "", message)


@router.callback_query(F.data.startswith("intro:set_grid:"))
async def intro_set_grid(callback: types.CallbackQuery):
    """Единый хендлер для изменения сетки в интро."""
    new_grid = int(callback.data.split(":")[2])
    await db.alchemy_players.update_one({"user_id": callback.from_user.id}, {"$set": {"inv_grid": new_grid}})
    
    session = await get_intro_session(callback.from_user.id)
    if session:
        if session.phase == "synthesis":
            await render_synthesis_window(callback)
        elif session.phase == "combat":
            await render_combat_window(callback)
        else:
            await callback.answer()
    else:
        await callback.answer("Сессия устарела.", show_alert=True)


# =====================================================================
# ФАЗА 1: ПРОБУЖДЕНИЕ (awakening)
# =====================================================================

async def run_intro_awakening(user_id: int, user_name: str, message: types.Message | None = None):
    player = await get_or_create_player(user_id, user_name)

    session = IntroSession(phase="awakening", sub_step=0)
    await save_intro_session(user_id, session)

    inventory = get_inventory(player)
    inventory["base_crystal"] = inventory.get("base_crystal", 0) + 1

    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {
            "inventory": inventory,
            "tutorial_step": 0,
            "world_tutorial": {"active": False, "created": False, "hints_shown": False},
        }}
    )

    text = (
        "🌑 *ПУСТОТА*\n\n"
        "\"Пшик, пшик, пшик... А это так должно было?!! **БАМММ!**\"\n\n"
        "Это последнее, что ты услышал, прежде чем очнуться в пустоте. "
        "Под рукой оказался манящий кристалл базы, рядом валялись коробочки и сырье. "
        "Пахнет катастрофой, но старт у создателя всегда такой."
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="🔮 Сжать кристалл", callback_data="intro:activate_crystal")
    builder.adjust(1)

    if message:
        await message.answer(text, reply_markup=builder.as_markup(), parse_mode="Markdown")


@router.callback_query(F.data == "intro:activate_crystal")
async def intro_activate_crystal(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    session = await get_intro_session(user_id)

    if not session or session.phase != "awakening":
        return await callback.answer("Сессия устарела.", show_alert=True)

    player = await get_or_create_player(user_id, callback.from_user.first_name)
    inventory = get_inventory(player)

    inventory.pop("base_crystal", None)

    for item_id, amount in INTRO_STARTER_RESOURCES.items():
        inventory[item_id] = inventory.get(item_id, 0) + amount

    for item_id, amount in STARTER_WORLD_RESOURCE_GRANT.items():
        inventory[item_id] = inventory.get(item_id, 0) + amount

    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {
            "inventory": inventory,
            "tutorial_step": 1,
            "world_tutorial": {"active": True, "created": False, "hints_shown": False},
        }}
    )

    session.phase = "synthesis"
    session.sub_step = 0
    session.pending_craft = SYNTHESIS_CHAIN[0][0]
    await save_intro_session(user_id, session)

    await render_synthesis_window(callback)


# =====================================================================
# ФАЗА 2: СИНТЕЗ (synthesis)
# =====================================================================

async def _build_synthesis_ui(user_id: int, session: IntroSession, player: dict) -> tuple[str, types.InlineKeyboardMarkup]:
    inventory = get_inventory(player)
    discovered = get_discovered(player)

    sub_step = session.sub_step
    target_item, hint, bonus_count = SYNTHESIS_CHAIN[sub_step]
    session.pending_craft = target_item

    mix = session.intro_mix
    mix_text = ""
    if not mix:
        mix_text = "🧪 В котле пусто. Выбери два ингредиента."
    elif len(mix) == 1:
        mix_text = f"♨️ В котле: {ELEMENTS.get(mix[0], mix[0])}\n⏳ Жду второй ингредиент..."
    else:
        mix_text = f"♨️ В котле: {' + '.join(ELEMENTS.get(i, i) for i in mix)}\n✨ Готово к смешиванию!"

    text = (
        f"⚗️ **ЛАБОРАТОРИЯ** (Обучение {sub_step + 1}/{len(SYNTHESIS_CHAIN)})\n\n"
        f"💡 **Задача:** создать {ELEMENTS.get(target_item, target_item)}\n"
        f"_{hint}_\n\n"
        f"{mix_text}"
    )

    builder = InlineKeyboardBuilder()

    if len(mix) == 2:
        builder.button(text="🌀 СМЕШАТЬ!", callback_data="intro:lab_mix")
    if len(mix) > 0:
        builder.button(text="🗑 Очистить котел", callback_data="intro:lab_clear")
    builder.adjust(1)

    available_items = set(["w", "f", "e", "a"])
    available_items.update(discovered)
    available_items.update([item for item, _, _ in SYNTHESIS_CHAIN[:sub_step]])

    # С пагинацией и сеткой
    page = intro_lab_pages.get(user_id, 0)
    grid_size = player.get("inv_grid", 3)
    ROWS_PER_PAGE = 10
    items_per_page = grid_size * ROWS_PER_PAGE

    visible_items = [(k, inventory.get(k, 0)) for k in available_items if inventory.get(k, 0) > 0]
    visible_items.sort(key=lambda x: get_el_name(x[0]))

    if visible_items:
        total_pages = max(1, (len(visible_items) + items_per_page - 1) // items_per_page)
        if page >= total_pages: page = max(0, total_pages - 1)

        start_idx = page * items_per_page
        page_items = visible_items[start_idx : start_idx + items_per_page]

        row = []
        for code, amount in page_items:
            emoji = get_el_emoji(code)
            name = get_el_name(code)
            btn_text = f"{emoji} {amount}" if grid_size == 4 else f"{emoji} {name} {amount}"
            row.append(types.InlineKeyboardButton(
                text=btn_text,
                callback_data=f"intro:lab_add:{code}"
            ))
            if len(row) == grid_size:
                builder.row(*row)
                row = []
        if row:
            builder.row(*row)

        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"intro:lab_page:{page-1}"))
            if page < total_pages - 1:
                nav_row.append(types.InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"intro:lab_page:{page+1}"))
            if nav_row:
                builder.row(*nav_row)

    next_grid = 3 if grid_size == 2 else 4 if grid_size == 3 else 2
    builder.row(types.InlineKeyboardButton(text=f"⚙️ Сетка: {grid_size} в ряд", callback_data=f"intro:set_grid:{next_grid}"))

    if session.return_target:
        if session.return_target == "biome":
            builder.row(types.InlineKeyboardButton(text="🔙 Вернуться к биому", callback_data="intro:return_to_biome"))
        elif session.return_target == "building":
            builder.row(types.InlineKeyboardButton(text="🔙 Вернуться к стройке", callback_data="intro:return_to_building"))

    builder.row(types.InlineKeyboardButton(text="💡 Подсказка", callback_data="intro:hint"))

    return text, builder.as_markup()


async def render_synthesis_window(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    session = await get_intro_session(user_id)

    if not session or session.phase != "synthesis":
        return await callback.answer("Ошибка сессии.", show_alert=True)

    player = await get_or_create_player(user_id, callback.from_user.first_name)

    sub_step = session.sub_step
    if sub_step >= len(SYNTHESIS_CHAIN):
        session.phase = "biome"
        session.sub_step = 0
        session.pending_craft = None
        await save_intro_session(user_id, session)
        return await render_biome_window(callback)

    text, markup = await _build_synthesis_ui(user_id, session, player)
    await save_intro_session(user_id, session)

    await safe_edit_text(callback.message, text, reply_markup=markup, parse_mode="Markdown")
    await callback.answer()


async def render_synthesis_window_as_new_message(message: types.Message, user_id: int):
    session = await get_intro_session(user_id)

    if not session or session.phase != "synthesis":
        return await message.answer("Ошибка сессии.")

    player = await get_or_create_player(user_id, message.chat.first_name or "Путник")

    sub_step = session.sub_step
    if sub_step >= len(SYNTHESIS_CHAIN):
        session.phase = "biome"
        session.sub_step = 0
        session.pending_craft = None
        await save_intro_session(user_id, session)
        fake_callback = types.CallbackQuery(
            id="restore",
            from_user=types.User(id=user_id, is_bot=False, first_name=""),
            chat_instance="",
            data="restore",
            message=message,
        )
        return await render_biome_window(fake_callback)

    text, markup = await _build_synthesis_ui(user_id, session, player)
    await save_intro_session(user_id, session)

    await message.answer(text, reply_markup=markup, parse_mode="Markdown")


@router.callback_query(F.data.startswith("intro:lab_page:"), IsInIntroPhase("synthesis"))
async def intro_lab_page(callback: types.CallbackQuery):
    page = int(callback.data.split(":")[2])
    intro_lab_pages[callback.from_user.id] = page
    await render_synthesis_window(callback)


@router.callback_query(F.data.startswith("intro:lab_add:"), IsInIntroPhase("synthesis"))
async def intro_lab_add(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    item = callback.data.split(":")[2]
    session = await get_intro_session(user_id)

    if not session:
        return await callback.answer("Сессия не найдена.", show_alert=True)

    if len(session.intro_mix) >= 2:
        return await callback.answer("Котел полон!", show_alert=True)

    player = await get_or_create_player(user_id, callback.from_user.first_name)
    inventory = get_inventory(player)

    mix_count = session.intro_mix.count(item)
    if inventory.get(item, 0) <= mix_count:
        return await callback.answer("У тебя больше нет этого ингредиента!", show_alert=True)

    session.intro_mix.append(item)
    await save_intro_session(user_id, session)
    await render_synthesis_window(callback)


@router.callback_query(F.data == "intro:lab_clear", IsInIntroPhase("synthesis"))
async def intro_lab_clear(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    session = await get_intro_session(user_id)
    if session:
        session.intro_mix = []
        await save_intro_session(user_id, session)
    await render_synthesis_window(callback)


@router.callback_query(F.data == "intro:lab_mix", IsInIntroPhase("synthesis"))
async def intro_lab_mix(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    session = await get_intro_session(user_id)

    if not session:
        return await callback.answer("Сессия не найдена.", show_alert=True)

    if len(session.intro_mix) != 2:
        return await callback.answer("Нужно два ингредиента!", show_alert=True)

    i1, i2 = session.intro_mix[0], session.intro_mix[1]
    recipe_key = tuple(sorted((i1, i2)))

    result = RECIPE_MAP.get(recipe_key)

    if not result or result != session.pending_craft:
        session.intro_mix = []
        await save_intro_session(user_id, session)
        await callback.answer("💨 Пшик... Пошла вонь. Попробуй другую комбинацию.", show_alert=True)
        return await render_synthesis_window(callback)

    player = await get_or_create_player(user_id, callback.from_user.first_name)
    inventory = get_inventory(player)
    discovered = get_discovered(player)

    _, _, bonus_count = SYNTHESIS_CHAIN[session.sub_step]

    inventory[i1] -= 1
    if inventory[i1] <= 0:
        inventory.pop(i1, None)
    inventory[i2] -= 1
    if inventory[i2] <= 0:
        inventory.pop(i2, None)

    inventory[result] = inventory.get(result, 0) + bonus_count

    if result not in discovered:
        discovered.append(result)

    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {
            "inventory": inventory,
            "discovered": discovered,
        }}
    )

    session.intro_mix = []
    session.sub_step += 1

    if session.sub_step < len(SYNTHESIS_CHAIN):
        session.pending_craft = SYNTHESIS_CHAIN[session.sub_step][0]
    else:
        session.pending_craft = None

    await save_intro_session(user_id, session)

    await callback.answer(f"🎉 Успех! Получено: {ELEMENTS.get(result, result)} x{bonus_count}", show_alert=True)

    if session.sub_step >= len(SYNTHESIS_CHAIN):
        session.phase = "biome"
        session.sub_step = 0
        await save_intro_session(user_id, session)
        return await render_biome_window(callback)

    await render_synthesis_window(callback)


@router.callback_query(F.data == "intro:hint", IsInIntroPhase("synthesis"))
async def intro_hint(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    session = await get_intro_session(user_id)
    if not session:
        return await callback.answer("Сессия не найдена.", show_alert=True)

    target_item = session.pending_craft
    if not target_item:
        return await callback.answer("Нет активной задачи.", show_alert=True)

    for (r1, r2), res in RECIPES.items():
        if res == target_item:
            ing1 = ELEMENTS.get(r1, r1)
            ing2 = ELEMENTS.get(r2, r2)
            await callback.answer(
                f"Рецепт: {ing1} + {ing2}",
                show_alert=True
            )
            return

    await callback.answer("Подсказка недоступна.", show_alert=True)


# =====================================================================
# ФАЗА 3: БИОМ (biome)
# =====================================================================

async def render_biome_window(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    session = await get_intro_session(user_id)

    if not session or session.phase != "biome":
        return await callback.answer("Ошибка сессии.", show_alert=True)

    player = await get_or_create_player(user_id, callback.from_user.first_name)
    inventory = get_inventory(player)

    text = (
        "🌍 **СТАБИЛИЗАЦИЯ МИРА**\n\n"
        "Собери учебный биом из четырёх слоёв:\n"
        "• 🪨 Основа: Камень + Дерево\n"
        "• 🌫 Среда: Болото + Мох\n"
        "• ⚡ Энергия: Пар + Энергия\n"
        "• 🧬 Жизнь: Мох + Жизнь\n\n"
        "Нажми на слой, чтобы добавить ресурсы."
    )

    builder = InlineKeyboardBuilder()

    layers = {
        "foundation": "🪨 Основа",
        "medium": "🌫 Среда",
        "energy": "⚡ Энергия",
        "life": "🧬 Жизнь",
    }

    for layer_id, label in layers.items():
        layer_cost = BIOME_LAYER_COST[layer_id]

        if layer_id in session.biome_filled:
            builder.button(
                text=f"{label} ✅",
                callback_data=f"intro:biome_check:{layer_id}"
            )
        else:
            can_fill = all(inventory.get(item, 0) >= amount for item, amount in layer_cost.items())
            if can_fill:
                builder.button(
                    text=f"{label} ➕",
                    callback_data=f"intro:biome_fill:{layer_id}"
                )
            else:
                missing = [f"{ELEMENTS.get(i, i)} x{a}" for i, a in layer_cost.items() if inventory.get(i, 0) < a]
                builder.button(
                    text=f"{label} ❌ ({', '.join(missing)})",
                    callback_data=f"intro:biome_check:{layer_id}"
                )

    builder.adjust(1)

    if len(session.biome_filled) == 4:
        builder.button(text="🧬 Стабилизировать биом", callback_data="intro:biome_stabilize")
        builder.adjust(1)

    builder.button(text="💡 Что у меня есть?", callback_data="intro:biome_inventory")
    builder.button(text="🧪 В Лабораторию", callback_data="intro:open_lab_from_biome")
    builder.adjust(1)

    await safe_edit_text(callback.message, text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("intro:biome_fill:"), IsInIntroPhase("biome"))
async def intro_biome_fill(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    layer_id = callback.data.split(":")[2]
    session = await get_intro_session(user_id)

    if not session:
        return await callback.answer("Сессия не найдена.", show_alert=True)

    player = await get_or_create_player(user_id, callback.from_user.first_name)
    inventory = get_inventory(player)

    required = BIOME_LAYER_COST.get(layer_id, {})

    for item, amount in required.items():
        if inventory.get(item, 0) < amount:
            return await callback.answer(f"Не хватает {ELEMENTS.get(item, item)}!", show_alert=True)

    for item, amount in required.items():
        inventory[item] -= amount
        if inventory[item] <= 0:
            inventory.pop(item, None)

    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {"inventory": inventory}}
    )

    session.biome_filled.add(layer_id)
    await save_intro_session(user_id, session)

    await callback.answer(f"Слой {layer_id} заполнен!")
    await render_biome_window(callback)


@router.callback_query(F.data.startswith("intro:biome_check:"), IsInIntroPhase("biome"))
async def intro_biome_check(callback: types.CallbackQuery):
    layer_id = callback.data.split(":")[2]
    session = await get_intro_session(callback.from_user.id)

    if session and layer_id in session.biome_filled:
        await callback.answer(f"Слой {layer_id} уже заполнен!", show_alert=True)
    else:
        await callback.answer(f"Сначала добудь ресурсы для слоя {layer_id}!", show_alert=True)


@router.callback_query(F.data == "intro:biome_stabilize", IsInIntroPhase("biome"))
async def intro_biome_stabilize(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    session = await get_intro_session(user_id)

    if not session or len(session.biome_filled) != 4:
        return await callback.answer("Заполни все слои!", show_alert=True)

    slots = {
        "foundation": "tutorial_seed",
        "medium": "tutorial_broth",
        "energy": "tutorial_pulse",
        "life": "tutorial_spore",
    }

    player = await get_or_create_player(user_id, callback.from_user.first_name)

    blueprint = build_world_blueprint(player, slots, focus_id=None)
    success, capacity, absurdity = evaluate_world_creation(player, slots, focus_id=None)

    fallback_name, description, _, _, humor = build_starter_world_preview(slots, focus_id=None)
    archetype = DEFAULT_WORLD_ARCHETYPE

    world = {
        "id": "starter_microbiome",
        "name": fallback_name,
        "description": description,
        "tier": 1,
        "capacity": capacity,
        "absurdity": absurdity,
        "support_bonus": archetype["support_base"],
        "strain_cost": archetype["strain_base"],
        "status": "stable",
        "humor": humor,
        "archetype": archetype["id"],
        "focus": "",
        "focus_label": "",
        "daily_income": {"random_spirit": 1},
        "slots": slots,
        "role": archetype["role"],
        "starter": True,
    }

    worlds = player.get("worlds", [])
    worlds.append(world)

    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {
            "worlds": worlds,
            "tutorial_step": 2,
            "world_tutorial": {"active": True, "created": True, "hints_shown": True},
        }}
    )

    session.phase = "expedition"
    session.sub_step = 0
    session.expedition_count = 0
    session.return_target = None
    await save_intro_session(user_id, session)

    text = (
        f"🌍 **МИКРОБИОМ СТАБИЛИЗИРОВАН**\n\n"
        f"Создано: **{fallback_name}**\n"
        f"{description}\n\n"
        f"Учебный этап закрыт: теперь у тебя есть мир.\n"
        f"Но для стройки нужны редкие материалы. Отправимся в поход?"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="🌋 В Вулкан", callback_data="intro:expedition:volcano")
    builder.adjust(1)

    await safe_edit_text(callback.message, text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    await callback.answer("Биом создан!")


@router.callback_query(F.data == "intro:biome_inventory", IsInIntroPhase("biome"))
async def intro_biome_inventory(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    inventory = get_inventory(player)

    items = [f"• {ELEMENTS.get(k, k)}: {v}" for k, v in inventory.items() if v > 0]
    text = "🎒 **Твои ресурсы:**\n\n" + "\n".join(items) if items else "🎒 **Твои ресурсы:**\n\nПусто."

    await callback.answer(text, show_alert=True)


@router.callback_query(F.data == "intro:open_lab_from_biome", IsInIntroPhase("biome"))
async def intro_open_lab_from_biome(callback: types.CallbackQuery):
    session = await get_intro_session(callback.from_user.id)
    if session:
        session.return_target = "biome"
        session.phase = "synthesis"
        await save_intro_session(callback.from_user.id, session)
    
    await callback.message.answer("🧪 Переключено в Лабораторию. Скрафти недостающее и возвращайся к биому.")
    await render_synthesis_window_as_new_message(callback.message, callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data == "intro:return_to_biome", IsInIntroPhase("synthesis"))
async def intro_return_to_biome(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    session = await get_intro_session(user_id)
    
    if not session:
        return await callback.answer("Сессия не найдена.", show_alert=True)
    
    session.phase = "biome"
    session.return_target = None
    await save_intro_session(user_id, session)
    
    await render_biome_window(callback)


# =====================================================================
# ФАЗА 4: ЭКСПЕДИЦИЯ / ПОХОД (expedition)
# =====================================================================

async def render_expedition_window(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    session = await get_intro_session(user_id)

    if not session or session.phase != "expedition":
        return await callback.answer("Ошибка сессии.", show_alert=True)

    count = session.expedition_count

    if count == 0:
        text = (
            "🗺 **ПЕРВЫЙ ПОХОД**\n\n"
            "Биом даёт базовый доход, но для стройки нужны редкие материалы.\n"
            "Каждая локация даёт свой набор ресурсов.\n\n"
            "Отправимся в Вулкан — источник огня, камня и кирпича."
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="🌋 Отправиться в Вулкан", callback_data="intro:expedition:volcano")
        builder.adjust(1)

    elif count == 1:
        text = (
            "🗺 **ВТОРОЙ ПОХОД**\n\n"
            "Для Казарм нужен металл. Он водится в Горах.\n\n"
            "Отправимся в Горы?"
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="⛰ Отправиться в Горы", callback_data="intro:expedition:mountains")
        builder.adjust(1)

    else:
        text = (
            "🗺 **КАРТА МИРА**\n\n"
            "Ты открыл базовые маршруты:\n\n"
            "🌋 Вулкан — огонь, камень, кирпич, песок\n"
            "⛰ Горы — металл, пепел, тростник, камень\n\n"
            "В реальной игре походы занимают время, но приносят огромную пользу.\n"
            "Переходим к стройке!"
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="🏗 К стройплощадке", callback_data="intro:expedition:complete")
        builder.adjust(1)

    await safe_edit_text(callback.message, text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("intro:expedition:"), IsInIntroPhase("expedition"))
async def intro_expedition_go(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    location = callback.data.split(":")[2]
    session = await get_intro_session(user_id)

    if not session:
        return await callback.answer("Сессия не найдена.", show_alert=True)

    if location == "complete":
        session.phase = "building"
        session.sub_step = 0
        await save_intro_session(user_id, session)
        return await render_building_window(callback)

    rewards = EXPEDITION_REWARDS.get(location, {})
    if not rewards:
        return await callback.answer("Неизвестная локация.", show_alert=True)

    player = await get_or_create_player(user_id, callback.from_user.first_name)
    inventory = get_inventory(player)

    for item, amount in rewards.items():
        inventory[item] = inventory.get(item, 0) + amount

    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {"inventory": inventory}}
    )

    session.expedition_count += 1
    await save_intro_session(user_id, session)

    reward_lines = [f"• {ELEMENTS.get(k, k)} x{v}" for k, v in rewards.items()]
    text = (
        f"{'🌋' if location == 'volcano' else '⛰'} **ПОХОД ЗАВЕРШЁН**\n\n"
        f"Добыто:\n" + "\n".join(reward_lines) + "\n\n"
        f"💡 {'Вулкан даёт огненные материалы.' if location == 'volcano' else 'Горы — источник металла и камня.'}"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="Продолжить", callback_data="intro:expedition_next")
    builder.adjust(1)

    await safe_edit_text(callback.message, text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    await callback.answer("Поход завершён!")


@router.callback_query(F.data == "intro:expedition_next", IsInIntroPhase("expedition"))
async def intro_expedition_next(callback: types.CallbackQuery):
    await render_expedition_window(callback)


# =====================================================================
# ФАЗА 5: СТРОЙКА (building)
# =====================================================================

async def render_building_window(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    session = await get_intro_session(user_id)

    if not session or session.phase != "building":
        return await callback.answer("Ошибка сессии.", show_alert=True)

    player = await get_or_create_player(user_id, callback.from_user.first_name)
    buildings = get_buildings(player)
    inventory = get_inventory(player)

    sub_step = session.sub_step
    if sub_step >= len(BUILDING_ORDER):
        session.phase = "combat"
        session.sub_step = 0
        await save_intro_session(user_id, session)
        return await render_combat_window(callback)

    building_id = BUILDING_ORDER[sub_step]
    current_level = buildings.get(building_id, 0)

    if current_level >= 1:
        session.sub_step += 1
        await save_intro_session(user_id, session)
        return await render_building_window(callback)

    next_level = 1
    building = BUILDINGS.get(building_id, {})

    if building_id in TUTORIAL_BUILDING_COST:
        cost = TUTORIAL_BUILDING_COST[building_id]
        building_name = building.get("name", building_id.capitalize())
        building_desc = building.get("desc", "")
        effect_desc = building.get("levels", {}).get(1, {}).get("effect_desc", "")
    else:
        cost = {}
        building_name = building_id.capitalize()
        building_desc = ""
        effect_desc = ""

    can_build = all(inventory.get(item, 0) >= amount for item, amount in cost.items())

    text_lines = [
        f"🏗 **СТРОЙКА** ({sub_step + 1}/{len(BUILDING_ORDER)})\n",
        f"**{building_name}** (Ур. {current_level} → {next_level})",
        f"_{building_desc}_\n",
    ]
    if effect_desc:
        text_lines.append(f"📈 {effect_desc}\n")

    if cost:
        text_lines.append("💰 Цена:")
        for item, amount in cost.items():
            have = inventory.get(item, 0)
            status = "✅" if have >= amount else f"❌ ({have}/{amount})"
            text_lines.append(f"  • {ELEMENTS.get(item, item)} x{amount} {status}")
    else:
        text_lines.append("💰 Цена: Бесплатно 🎁")

    if not can_build and cost:
        text_lines.append("\n❌ Не хватает ресурсов! Скрафти в Лаборатории или сходи в поход.")

    text = "\n".join(text_lines)

    builder = InlineKeyboardBuilder()

    if can_build or not cost:
        builder.button(text="🏗 Построить", callback_data=f"intro:build:{building_id}")
    else:
        builder.button(text="🔍 Где добыть?", callback_data="intro:build_hint")
        builder.button(text="🧪 В Лабораторию", callback_data="intro:open_lab_from_building")

    builder.adjust(1)

    await safe_edit_text(callback.message, text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("intro:build:"), IsInIntroPhase("building"))
async def intro_build_do(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    building_id = callback.data.split(":")[2]
    session = await get_intro_session(user_id)

    if not session:
        return await callback.answer("Сессия не найдена.", show_alert=True)

    player = await get_or_create_player(user_id, callback.from_user.first_name)
    buildings = get_buildings(player)
    inventory = get_inventory(player)

    cost = TUTORIAL_BUILDING_COST.get(building_id, {})

    if not all(inventory.get(item, 0) >= amount for item, amount in cost.items()):
        return await callback.answer("Не хватает ресурсов!", show_alert=True)

    for item, amount in cost.items():
        inventory[item] -= amount
        if inventory[item] <= 0:
            inventory.pop(item, None)

    buildings[building_id] = 1

    update_fields = {
        "inventory": inventory,
        "buildings": buildings,
    }

    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": update_fields}
    )

    session.sub_step += 1
    await save_intro_session(user_id, session)

    building_name = BUILDINGS.get(building_id, {}).get("name", building_id.capitalize())
    await callback.answer(f"🏗 {building_name} построен (Ур. 1)!")

    if session.sub_step >= len(BUILDING_ORDER):
        session.phase = "combat"
        session.sub_step = 0
        await save_intro_session(user_id, session)
        return await render_combat_window(callback)

    await render_building_window(callback)


@router.callback_query(F.data == "intro:build_hint", IsInIntroPhase("building"))
async def intro_build_hint(callback: types.CallbackQuery):
    await callback.answer(
        "Ресурсы добываются в походах. Вулкан — огонь, камень, кирпич, песок. Горы — металл, пепел, тростник.",
        show_alert=True
    )


@router.callback_query(F.data == "intro:open_lab_from_building", IsInIntroPhase("building"))
async def intro_open_lab_from_building(callback: types.CallbackQuery):
    session = await get_intro_session(callback.from_user.id)
    if session:
        session.return_target = "building"
        session.phase = "synthesis"
        await save_intro_session(callback.from_user.id, session)
    
    await callback.message.answer("🧪 Переключено в Лабораторию. Скрафти недостающее и возвращайся к стройке.")
    await render_synthesis_window_as_new_message(callback.message, callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data == "intro:return_to_building", IsInIntroPhase("synthesis"))
async def intro_return_to_building(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    session = await get_intro_session(user_id)
    
    if not session:
        return await callback.answer("Сессия не найдена.", show_alert=True)
    
    session.phase = "building"
    session.return_target = None
    await save_intro_session(user_id, session)
    
    await render_building_window(callback)


# =====================================================================
# ФАЗА 6: БОЕВАЯ АЛХИМИЯ (combat)
# =====================================================================

async def render_combat_window(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    session = await get_intro_session(user_id)

    if not session or session.phase != "combat":
        return await callback.answer("Ошибка сессии.", show_alert=True)

    player = await get_or_create_player(user_id, callback.from_user.first_name)
    inventory = get_inventory(player)
    discovered = get_discovered(player)

    sub_step = session.sub_step
    if sub_step >= len(COMBAT_CHAIN):
        session.phase = "complete"
        session.sub_step = 0
        await save_intro_session(user_id, session)
        return await render_complete_window(callback)

    target_item, hint, is_spirit = COMBAT_CHAIN[sub_step]
    session.pending_craft = target_item
    await save_intro_session(user_id, session)

    mix = session.intro_mix
    mix_text = ""
    if not mix:
        mix_text = "🧪 В котле пусто. Выбери два ингредиента."
    elif len(mix) == 1:
        mix_text = f"♨️ В котле: {ELEMENTS.get(mix[0], mix[0])}\n⏳ Жду второй ингредиент..."
    else:
        mix_text = f"♨️ В котле: {' + '.join(ELEMENTS.get(i, i) for i in mix)}\n✨ Готово к смешиванию!"

    text = (
        f"⚔️ **БОЕВАЯ АЛХИМИЯ** ({sub_step + 1}/{len(COMBAT_CHAIN)})\n\n"
        f"💡 **Задача:** создать {ELEMENTS.get(target_item, target_item)}\n"
        f"_{hint}_\n\n"
        f"{mix_text}"
    )

    builder = InlineKeyboardBuilder()

    if len(mix) == 2:
        builder.button(text="🌀 СМЕШАТЬ!", callback_data="intro:combat_mix")
    if len(mix) > 0:
        builder.button(text="🗑 Очистить котел", callback_data="intro:combat_clear")
    builder.adjust(1)

    available_items = set(["w", "f", "e", "a"])
    available_items.update(discovered)
    available_items.update([item for item, _, _ in COMBAT_CHAIN[:sub_step] if item.startswith("spirit_")])

    # Пагинация и сетка
    page = intro_combat_pages.get(user_id, 0)
    grid_size = player.get("inv_grid", 3)
    ROWS_PER_PAGE = 10
    items_per_page = grid_size * ROWS_PER_PAGE

    visible_items = [(k, inventory.get(k, 0)) for k in available_items if inventory.get(k, 0) > 0]
    visible_items.sort(key=lambda x: get_el_name(x[0]))

    if visible_items:
        total_pages = max(1, (len(visible_items) + items_per_page - 1) // items_per_page)
        if page >= total_pages: page = max(0, total_pages - 1)

        start_idx = page * items_per_page
        page_items = visible_items[start_idx : start_idx + items_per_page]

        row = []
        for code, amount in page_items:
            emoji = get_el_emoji(code)
            name = get_el_name(code)
            btn_text = f"{emoji} {amount}" if grid_size == 4 else f"{emoji} {name} {amount}"
            row.append(types.InlineKeyboardButton(
                text=btn_text,
                callback_data=f"intro:combat_add:{code}"
            ))
            if len(row) == grid_size:
                builder.row(*row)
                row = []
        if row:
            builder.row(*row)

        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"intro:combat_page:{page-1}"))
            if page < total_pages - 1:
                nav_row.append(types.InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"intro:combat_page:{page+1}"))
            if nav_row:
                builder.row(*nav_row)

    next_grid = 3 if grid_size == 2 else 4 if grid_size == 3 else 2
    builder.row(types.InlineKeyboardButton(text=f"⚙️ Сетка: {grid_size} в ряд", callback_data=f"intro:set_grid:{next_grid}"))
    builder.row(types.InlineKeyboardButton(text="💡 Подсказка", callback_data="intro:combat_hint"))

    await safe_edit_text(callback.message, text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("intro:combat_page:"), IsInIntroPhase("combat"))
async def intro_combat_page(callback: types.CallbackQuery):
    page = int(callback.data.split(":")[2])
    intro_combat_pages[callback.from_user.id] = page
    await render_combat_window(callback)


@router.callback_query(F.data.startswith("intro:combat_add:"), IsInIntroPhase("combat"))
async def intro_combat_add(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    item = callback.data.split(":")[2]
    session = await get_intro_session(user_id)

    if not session:
        return await callback.answer("Сессия не найдена.", show_alert=True)

    if len(session.intro_mix) >= 2:
        return await callback.answer("Котел полон!", show_alert=True)

    player = await get_or_create_player(user_id, callback.from_user.first_name)
    inventory = get_inventory(player)

    mix_count = session.intro_mix.count(item)
    if inventory.get(item, 0) <= mix_count:
        return await callback.answer("У тебя больше нет этого ингредиента!", show_alert=True)

    session.intro_mix.append(item)
    await save_intro_session(user_id, session)
    await render_combat_window(callback)


@router.callback_query(F.data == "intro:combat_clear", IsInIntroPhase("combat"))
async def intro_combat_clear(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    session = await get_intro_session(user_id)
    if session:
        session.intro_mix = []
        await save_intro_session(user_id, session)
    await render_combat_window(callback)


@router.callback_query(F.data == "intro:combat_mix", IsInIntroPhase("combat"))
async def intro_combat_mix(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    session = await get_intro_session(user_id)

    if not session:
        return await callback.answer("Сессия не найдена.", show_alert=True)

    if len(session.intro_mix) != 2:
        return await callback.answer("Нужно два ингредиента!", show_alert=True)

    i1, i2 = session.intro_mix[0], session.intro_mix[1]
    recipe_key = tuple(sorted((i1, i2)))
    result = RECIPE_MAP.get(recipe_key)

    if not result or result != session.pending_craft:
        session.intro_mix = []
        await save_intro_session(user_id, session)
        await callback.answer("💨 Пшик... Попробуй другую комбинацию.", show_alert=True)
        return await render_combat_window(callback)

    player = await get_or_create_player(user_id, callback.from_user.first_name)
    inventory = get_inventory(player)
    discovered = get_discovered(player)
    fighters = player.get("fighters", [])

    inventory[i1] -= 1
    if inventory[i1] <= 0:
        inventory.pop(i1, None)
    inventory[i2] -= 1
    if inventory[i2] <= 0:
        inventory.pop(i2, None)

    if is_battle_unit(result):
        fighter = create_fighter(result, crafted_tier3=BATTLE_UNITS[result]["tier"] >= 3)
        fighters.append(fighter)
    else:
        inventory[result] = inventory.get(result, 0) + 1

    if result not in discovered:
        discovered.append(result)

    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {
            "inventory": inventory,
            "discovered": discovered,
            "fighters": fighters,
        }}
    )

    session.intro_mix = []
    session.sub_step += 1

    if session.sub_step < len(COMBAT_CHAIN):
        session.pending_craft = COMBAT_CHAIN[session.sub_step][0]
    else:
        session.pending_craft = None

    await save_intro_session(user_id, session)

    res_name = ELEMENTS.get(result, result)
    unit_type = "боец" if is_battle_unit(result) else "элемент"
    await callback.answer(f"🎉 Успех! Получено: {res_name} ({unit_type})", show_alert=True)

    if session.sub_step >= len(COMBAT_CHAIN):
        session.phase = "complete"
        session.sub_step = 0
        await save_intro_session(user_id, session)
        return await render_complete_window(callback)

    await render_combat_window(callback)


@router.callback_query(F.data == "intro:combat_hint", IsInIntroPhase("combat"))
async def intro_combat_hint(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    session = await get_intro_session(user_id)
    if not session:
        return await callback.answer("Сессия не найдена.", show_alert=True)

    target_item = session.pending_craft
    if not target_item:
        return await callback.answer("Нет активной задачи.", show_alert=True)

    for (r1, r2), res in RECIPES.items():
        if res == target_item:
            ing1 = ELEMENTS.get(r1, r1)
            ing2 = ELEMENTS.get(r2, r2)
            await callback.answer(f"Рецепт: {ing1} + {ing2}", show_alert=True)
            return

    await callback.answer("Подсказка недоступна.", show_alert=True)


# =====================================================================
# ФАЗА 7: ЗАВЕРШЕНИЕ (complete)
# =====================================================================

async def render_complete_window(callback: types.CallbackQuery):
    user_id = callback.from_user.id

    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {"tutorial_step": 100}}
    )

    await clear_intro_session(user_id)

    text = (
        "🎉 **ОБУЧЕНИЕ ЗАВЕРШЕНО**\n\n"
        "Ты создал мир, построил базу и собрал отряд.\n"
        "Теперь полный цикл игры открыт:\n"
        "• Походы для добычи ресурсов\n"
        "• Стройка для развития базы\n"
        "• Лаборатория для крафта и бойцов\n\n"
        "Добро пожаловать в Штаб, создатель."
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="🏕 В Штаб", callback_data="alc:base_return")
    builder.adjust(1)

    await safe_edit_text(callback.message, text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    await callback.answer("Добро пожаловать!")
