import html
import random
import time
import json
import os
from typing import Optional, List, Dict, Any, Tuple

from aiogram import F, Router, types
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from pydantic import BaseModel, Field

from config import ADMIN_ID, get_msk_now
from database import db
from handlers.alc_core import STORAGE_CAPACITY, get_heritage_slots, get_or_create_player, get_used_slots
from handlers.alc_fighters import create_fighter, get_barracks_capacity
from handlers.alc_recipes import BATTLE_UNITS, ELEMENTS

router = Router()

LEGACY_FRAGMENT_DAILY_LIMIT = 2
LEGACY_INSTALL_ENERGY = 50
DEFAULT_REWARD_ITEM_AMOUNT = 1
HERITAGE_ITEMS_PER_PAGE = 10

# --- FSM СОСТОЯНИЯ ДЛЯ АДМИНКИ ---
class LoreEditorStates(StatesGroup):
    new_lore = State()
    edit_basic = State()
    edit_button = State()
    edit_bonus = State()
    edit_reward = State()
    edit_fragments = State()
    edit_text = State()

# --- PYDANTIC МОДЕЛИ ДЛЯ ВАЛИДАЦИИ БД ---
class LoreFragmentModel(BaseModel):
    id: str
    title: str = "Обрывок"
    preview: str = ""

class LoreDocModel(BaseModel):
    id: str
    name: str
    button_name: str = ""
    emoji: str = "📜"
    bonus_type: str = "spirit_f"
    bonus_value: int = 1
    reward_type: str = "item"
    reward_payload: str = ""
    reward_amount: int = 1
    reward_name: str = ""
    sort_order: int = 100
    fragments: List[LoreFragmentModel] = Field(default_factory=list)
    full_text: str = ""


# --- ВАЛИДАЦИЯ И НОРМАЛИЗАЦИЯ ---
def normalize_lore_doc(doc: dict) -> dict:
    """Нормализует сырой документ из БД, сохраняя 100% обратную совместимость."""
    try:
        current = dict(doc)
        
        # 1. Ручная очистка от мусора и пустых строк (защита от ошибок парсинга Pydantic)
        current["id"] = str(current.get("id", "")).strip()
        current["name"] = str(current.get("name", current["id"])).strip() or current["id"]
        current["button_name"] = str(current.get("button_name", "")).strip()[:9]
        current["emoji"] = str(current.get("emoji", "📜")).strip() or "📜"
        current["bonus_type"] = str(current.get("bonus_type", "spirit_f")).strip() or "spirit_f"
        current["bonus_value"] = max(1, min(3, int(current.get("bonus_value", 1) or 1)))
        current["reward_type"] = str(current.get("reward_type", "item")).strip() or "item"
        current["reward_payload"] = str(current.get("reward_payload", "")).strip()
        current["reward_amount"] = max(1, int(current.get("reward_amount", DEFAULT_REWARD_ITEM_AMOUNT) or DEFAULT_REWARD_ITEM_AMOUNT))
        current["reward_name"] = str(current.get("reward_name", "")).strip()
        current["sort_order"] = int(current.get("sort_order", 100) or 100)
        current["full_text"] = str(current.get("full_text", "")).strip()
        
        # Предварительная очистка фрагментов (максимум 3, только с валидными id)
        fragments_raw = current.get("fragments", [])
        valid_fragments = [
            {
                "id": str(f.get("id", "")).strip(),
                "title": str(f.get("title", f.get("id", "Обрывок"))).strip() or "Обрывок",
                "preview": str(f.get("preview", "")).strip()
            }
            for f in fragments_raw if isinstance(f, dict) and str(f.get("id", "")).strip()
        ][:3]
        current["fragments"] = valid_fragments
        
        # 2. Прогон через Pydantic для гарантии структуры
        model = LoreDocModel(**current)
        
        # Поддержка Pydantic V1 и V2
        validated = model.model_dump() if hasattr(model, 'model_dump') else model.dict()
        return validated
    except Exception as e:
        print(f"Error normalizing lore doc (ID: {doc.get('id', 'unknown')}): {e}")
        # Возвращаем как есть в случае критического сбоя, чтобы не ронять бота
        return doc


def with_bammm(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        cleaned = "Память мира пока молчит. Но в глубине уже бродит знакомое шипение."
    if "БАМММ" not in cleaned.upper():
        cleaned += "\n\nПшик, пшик... БАМММ!"
    return cleaned


def get_collection_progress(player: dict, lore_doc: dict) -> tuple[int, int]:
    fragments = player.get("lore_fragments", {})
    total = len(lore_doc.get("fragments", []))
    owned = sum(1 for fragment in lore_doc.get("fragments", []) if fragments.get(fragment["id"]))
    return owned, total


def get_install_cost(lore_doc: dict) -> int:
    return LEGACY_INSTALL_ENERGY


def get_heritage_multiplier(player: dict) -> int:
    return 2 if player.get("buildings", {}).get("heritage_center", 1) >= 5 else 1


def get_bonus_label(item_id: str, amount: int) -> str:
    return f"{ELEMENTS.get(item_id, item_id)} +{amount}/день"


def get_reward_label(lore_doc: dict) -> str:
    reward_type = lore_doc.get("reward_type", "item")
    payload = lore_doc.get("reward_payload", "")
    amount = int(lore_doc.get("reward_amount", 1) or 1)
    if reward_type == "fighter":
        title = lore_doc.get("reward_name") or "Герой Наследия"
        unit_name = ELEMENTS.get(payload, payload)
        return f"Боец Т3: {title} ({unit_name})"
    if reward_type == "recipe":
        return f"Рецепт: {ELEMENTS.get(payload, payload)}"
    return f"{ELEMENTS.get(payload, payload)} x{amount}"


def shorten_heritage_name(name: str, limit: int = 9) -> str:
    value = str(name or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)] + "…"


def get_heritage_button_label(lore_doc: dict) -> str:
    button_name = str(lore_doc.get("button_name", "")).strip()
    if button_name:
        return button_name[:9]
    return shorten_heritage_name(lore_doc.get("name", ""), 9)


def encode_lore_ref(index: int) -> str:
    return f"i{index}"


async def get_lore_doc_by_ref(ref: str) -> dict | None:
    value = str(ref or "").strip()
    if value.startswith("i") and value[1:].isdigit():
        docs = await get_all_lore_docs()
        index = int(value[1:])
        if 0 <= index < len(docs):
            return docs[index]
        return None
    return await get_lore_doc(value)


async def get_lore_ref_by_id(collection_id: str) -> str:
    docs = await get_all_lore_docs()
    for index, lore_doc in enumerate(docs):
        if lore_doc["id"] == collection_id:
            return encode_lore_ref(index)
    return collection_id


async def ensure_default_lore():
    await db.alchemy_lore.delete_one({"id": "forge_legacy"})
    
    seed_path = "seeds/default_lore.json"
    if os.path.exists(seed_path):
        try:
            with open(seed_path, "r", encoding="utf-8") as f:
                collections = json.load(f)
                for doc in collections:
                    normalized = normalize_lore_doc(doc)
                    await db.alchemy_lore.update_one(
                        {"id": normalized["id"]},
                        {"$setOnInsert": normalized},
                        upsert=True,
                    )
        except Exception as e:
            print(f"Failed to load seed lore from {seed_path}: {e}")


async def get_all_lore_docs() -> list[dict]:
    docs = []
    cursor = db.alchemy_lore.find({})
    async for doc in cursor:
        docs.append(normalize_lore_doc(doc))
    docs.sort(key=lambda item: (item.get("sort_order", 100), item["name"]))
    return docs


async def get_lore_doc(collection_id: str) -> dict | None:
    doc = await db.alchemy_lore.find_one({"id": collection_id})
    return normalize_lore_doc(doc) if doc else None

# --- UI ГЕНЕРАТОРЫ ИГРОКА ---

def build_heritage_main_text(player: dict, lore_docs: list[dict], page: int = 0) -> str:
    level = player.get("buildings", {}).get("heritage_center", 1)
    stands = list(player.get("active_stands", []))
    multiplier = get_heritage_multiplier(player)
    lore_by_id = {doc["id"]: doc for doc in lore_docs}
    total_pages = max(1, (len(lore_docs) + HERITAGE_ITEMS_PER_PAGE - 1) // HERITAGE_ITEMS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * HERITAGE_ITEMS_PER_PAGE
    page_lore_docs = lore_docs[start:start + HERITAGE_ITEMS_PER_PAGE]
    lines = [
        f"📚 <b>НАСЛЕДИЕ</b> (Ур. {level})",
    ]
    if total_pages > 1:
        lines.append(f"Страница: {page + 1}/{total_pages}")
    if multiplier > 1:
        lines.append("Мастер-архив активен: все бонусы на стендах удвоены.")
    lines.append("")
    lines.append("Стенды:")
    for idx, stand_id in enumerate(stands, start=1):
        if stand_id and stand_id in lore_by_id:
            lore_doc = lore_by_id[stand_id]
            amount = lore_doc["bonus_value"] * multiplier
            lines.append(f"💎 {idx}: {html.escape(lore_doc['name'])} [{html.escape(get_bonus_label(lore_doc['bonus_type'], amount))}]")
        else:
            lines.append(f"▫️ {idx}: пусто")
    lines.append("")
    lines.append("Коллекции:")
    for lore_doc in page_lore_docs:
        owned, total = get_collection_progress(player, lore_doc)
        marker = "✅" if owned == total and total > 0 else "📜"
        lines.append(f"{marker} {lore_doc['emoji']} {html.escape(lore_doc['name'])} {owned}/{total}")
    return "\n".join(lines)


def build_heritage_main_keyboard(player: dict, lore_docs: list[dict], page: int = 0) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    stands = list(player.get("active_stands", []))
    lore_by_id = {doc["id"]: doc for doc in lore_docs}
    total_pages = max(1, (len(lore_docs) + HERITAGE_ITEMS_PER_PAGE - 1) // HERITAGE_ITEMS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * HERITAGE_ITEMS_PER_PAGE
    page_lore_docs = lore_docs[start:start + HERITAGE_ITEMS_PER_PAGE]

    # Кнопки стендов - максимум 5 в ряд + хелп = 6
    stand_buttons = []
    for idx, stand_id in enumerate(stands):
        if stand_id and stand_id in lore_by_id:
            stand_buttons.append(types.InlineKeyboardButton(text=f"🟢 {idx + 1}", callback_data=f"heritage_stand:{idx}:{page}"))
        else:
            stand_buttons.append(types.InlineKeyboardButton(text=f"🔴 {idx + 1}", callback_data=f"heritage_empty:{idx}:{page}"))

    if stand_buttons:
        # Добавляем хелп в конец
        stand_buttons.append(types.InlineKeyboardButton(text="❓", callback_data="heritage_help"))
        # Разбиваем на ряды по 6 кнопок
        for i in range(0, len(stand_buttons), 6):
            builder.row(*stand_buttons[i:i+6])

    # Кнопки наследий - по 2 в ряд
    row = []
    for index, lore_doc in enumerate(page_lore_docs, start=start):
        owned, total = get_collection_progress(player, lore_doc)
        row.append(types.InlineKeyboardButton(
            text=f"{lore_doc['emoji']} {get_heritage_button_label(lore_doc)} {owned}/{total}",
            callback_data=f"heritage_view:{encode_lore_ref(index)}:{page}",
        ))
        if len(row) == 2:
            builder.row(*row)
            row = []
    if row:
        builder.row(*row)

    # Навигация по страницам
    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"heritage_page:{page - 1}"))
        if page < total_pages - 1:
            nav_row.append(types.InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"heritage_page:{page + 1}"))
        if nav_row:
            builder.row(*nav_row)

    builder.button(text="🔙 В Штаб", callback_data="alc:base_return")
    return builder.as_markup()


def build_collection_detail_text(player: dict, lore_doc: dict) -> str:
    owned, total = get_collection_progress(player, lore_doc)
    lines = [
        f"{lore_doc['emoji']} <b>{html.escape(lore_doc['name'])}</b>",
        f"Собрано: {owned}/{total}",
        f"Пассивный бонус: {html.escape(get_bonus_label(lore_doc['bonus_type'], lore_doc['bonus_value']))}",
        f"Разовый дар: {html.escape(get_reward_label(lore_doc))}",
        f"Цена установки: ⚡ {get_install_cost(lore_doc)}",
        "",
        "Обрывки:",
    ]
    fragments = player.get("lore_fragments", {})
    for fragment in lore_doc.get("fragments", []):
        marker = "✅" if fragments.get(fragment["id"]) else "▫️"
        lines.append(f"{marker} {html.escape(fragment['title'])}")
        if fragment.get("preview"):
            lines.append(f"   <i>{html.escape(fragment['preview'])}</i>")
    active_slot = None
    for idx, stand_id in enumerate(player.get("active_stands", [])):
        if stand_id == lore_doc["id"]:
            active_slot = idx + 1
            break
    lines.append("")
    if active_slot:
        lines.append(f"Сейчас установлено на стенде {active_slot}.")
    elif owned == total and total > 0:
        lines.append("Коллекция готова к установке на стенд.")
    else:
        lines.append("Сначала собери все 3 обрывка.")
    return "\n".join(lines)


def build_collection_detail_keyboard(player: dict, lore_doc: dict, page: int = 0) -> types.InlineKeyboardMarkup:
    owned, total = get_collection_progress(player, lore_doc)
    builder = InlineKeyboardBuilder()
    lore_ref = lore_doc.get("_ref", lore_doc["id"])
    if owned == total and total > 0:
        builder.button(text="📖 Читать целиком", callback_data=f"heritage_read:{lore_ref}:{page}")
        builder.button(text="💎 Установить на стенд", callback_data=f"heritage_install_prompt:{lore_ref}:{page}")
    active_slot = next((idx for idx, stand_id in enumerate(player.get("active_stands", [])) if stand_id == lore_doc["id"]), None)
    if active_slot is not None:
        builder.button(text="🧹 Снять со стенда", callback_data=f"heritage_remove:{active_slot}:{page}")
    builder.button(text="🔙 К наследию", callback_data=f"heritage_page:{page}")
    builder.adjust(1)
    return builder.as_markup()


def build_read_text(lore_doc: dict) -> str:
    return f"{lore_doc['emoji']} <b>{html.escape(lore_doc['name'])}</b>\n\n{html.escape(with_bammm(lore_doc.get('full_text', '')))}"


def build_read_keyboard(lore_doc: dict, page: int = 0) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    lore_ref = lore_doc.get("_ref", lore_doc["id"])
    builder.button(text="🔙 Назад", callback_data=f"heritage_view:{lore_ref}:{page}")
    builder.adjust(1)
    return builder.as_markup()


def build_stand_pick_text(player: dict, lore_doc: dict) -> str:
    lines = [
        f"💎 <b>УСТАНОВКА: {html.escape(lore_doc['name'])}</b>",
        f"Цена зарядки: ⚡ {get_install_cost(lore_doc)}",
        "",
        "Выбери стенд:",
    ]
    for idx, stand_id in enumerate(player.get("active_stands", []), start=1):
        if stand_id:
            lines.append(f"• Стенд {idx}: занят")
        else:
            lines.append(f"• Стенд {idx}: свободен")
    lines.append("")
    lines.append("Если заменить экспонат, старое Наследие разрядится.")
    return "\n".join(lines)


def build_stand_pick_keyboard(player: dict, lore_doc: dict, page: int = 0) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    lore_ref = lore_doc.get("_ref", lore_doc["id"])
    row = []
    for idx, stand_id in enumerate(player.get("active_stands", [])):
        marker = "💎" if stand_id else "▫️"
        row.append(types.InlineKeyboardButton(text=f"{marker} {idx + 1}", callback_data=f"heritage_install:{lore_ref}:{idx}:{page}"))
    if row:
        builder.row(*row)
    builder.button(text="🔙 Назад", callback_data=f"heritage_view:{lore_ref}:{page}")
    builder.adjust(1)
    return builder.as_markup()


def build_stand_detail_text(player: dict, lore_doc: dict, stand_index: int) -> str:
    amount = lore_doc["bonus_value"] * get_heritage_multiplier(player)
    return (
        f"💎 <b>СТЕНД {stand_index + 1}</b>\n"
        f"Установлено: {html.escape(lore_doc['name'])}\n"
        f"Активный бонус: {html.escape(get_bonus_label(lore_doc['bonus_type'], amount))}\n"
        f"Разовый дар: {html.escape(get_reward_label(lore_doc))}"
    )


def build_stand_detail_keyboard(stand_index: int, lore_id: str, page: int = 0) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📖 Открыть коллекцию", callback_data=f"heritage_view:{lore_id}:{page}")
    builder.button(text="🧹 Снять со стенда", callback_data=f"heritage_remove:{stand_index}:{page}")
    builder.button(text="🔙 К наследию", callback_data=f"heritage_page:{page}")
    builder.adjust(1)
    return builder.as_markup()


def build_help_text() -> str:
    return (
        "❓ <b>СПРАВКА ПО НАСЛЕДИЮ</b>\n\n"
        "<b>Как это работает:</b>\n"
        "• Собирай обрывки лора из коллекций (3 шт на каждую)\n"
        "• Полная коллекция даёт пассивный бонус каждый день\n"
        "• Установи коллекцию на стенд для активации бонуса\n"
        "• При первой установке получаешь разовый дар\n\n"
        "<b>Стенды:</b>\n"
        "• Количество стендов зависит от уровня Архива Наследия\n"
        "• Ур. 1: 1 стенд, Ур. 2-4: +1 стенд каждый уровень\n"
        "• Ур. 5: 5 стендов + удвоение всех бонусов\n\n"
        "<b>Бонусы:</b>\n"
        "• spirit_f/w/e/a — стихийные духи\n"
        "• metal/energy — ресурсы\n"
        "• pack_s/m/l — наборы материалов\n\n"
        "<b>Разовые дары:</b>\n"
        "• Предметы, рецепты или бойцы Т3\n"
        "• Выдаются только один раз при первой установке\n"
        "• При снятии со стенда дар не возвращается"
    )


def build_help_keyboard(page: int = 0) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 К наследию", callback_data=f"heritage_page:{page}")
    builder.adjust(1)
    return builder.as_markup()

# --- ИГРОВАЯ МЕХАНИКА (ВЫЗЫВАЕТСЯ ИЗВНЕ) ---
    
async def try_grant_random_lore_fragment(player: dict, *, chance: float) -> dict | None:
    progress = dict(player.get("lore_fragment_progress", {}))
    today = get_msk_now().strftime("%Y-%m-%d")
    if progress.get("date") != today:
        progress = {"date": today, "found": 0}
    if progress.get("found", 0) >= LEGACY_FRAGMENT_DAILY_LIMIT:
        player["lore_fragment_progress"] = progress
        return None
    if random.random() > chance:
        player["lore_fragment_progress"] = progress
        return None

    lore_docs = await get_all_lore_docs()
    fragments = player.get("lore_fragments", {})
    missing = []
    for lore_doc in lore_docs:
        for fragment in lore_doc.get("fragments", []):
            if not fragments.get(fragment["id"]):
                missing.append({"collection_id": lore_doc["id"], "collection_name": lore_doc["name"], **fragment})
    if not missing:
        player["lore_fragment_progress"] = progress
        return None

    found = random.choice(missing)
    fragments[found["id"]] = True
    progress["found"] = int(progress.get("found", 0)) + 1
    player["lore_fragments"] = fragments
    player["lore_fragment_progress"] = progress
    return found


async def get_active_legacy_bonus_map(player: dict) -> dict[str, int]:
    stands = [stand_id for stand_id in player.get("active_stands", []) if stand_id]
    if not stands:
        return {}
    docs = await db.alchemy_lore.find({"id": {"$in": stands}}).to_list(length=50)
    multiplier = get_heritage_multiplier(player)
    bonus_map: dict[str, int] = {}
    for doc in docs:
        lore_doc = normalize_lore_doc(doc)
        item_id = lore_doc.get("bonus_type")
        amount = int(lore_doc.get("bonus_value", 0) or 0) * multiplier
        if not item_id or amount <= 0:
            continue
        bonus_map[item_id] = bonus_map.get(item_id, 0) + amount
    return bonus_map


async def grant_first_legacy_reward(player: dict, lore_doc: dict) -> tuple[bool, str]:
    collection_id = lore_doc["id"]
    claimed = list(player.get("claimed_legacy_rewards", []))
    if collection_id in claimed:
        return True, ""

    reward_type = lore_doc.get("reward_type", "item")
    payload = lore_doc.get("reward_payload", "")
    amount = int(lore_doc.get("reward_amount", 1) or 1)
    reward_text = ""

    if reward_type == "fighter":
        if payload not in BATTLE_UNITS:
            return False, "Для этого Наследия не настроен корректный боевой юнит."
        capacity = get_barracks_capacity(player.get("buildings", {}).get("barracks", 0))
        alive_count = len([fighter for fighter in player.get("fighters", []) if fighter.get("status") != "dead"])
        if alive_count >= capacity:
            return False, "Для этого дара нужна свободная койка в Казармах. Освободи место или построй их лучше."
        fighter = create_fighter(payload, tier=3, crafted_tier3=True)
        fighter["custom_name"] = lore_doc.get("reward_name", "").strip()[:24]
        fighter["history"] = [
            f"Призван из глубин {lore_doc['name']}.",
            "Пшик, пшик... БАМММ!",
        ]
        fighter["origin"] = f"legacy:{collection_id}"
        player.setdefault("fighters", []).append(fighter)
        reward_text = f"Разовый дар пробудил бойца Т3: {fighter['custom_name'] or ELEMENTS.get(payload, payload)}."
    elif reward_type == "recipe":
        discovered = list(player.get("discovered", []))
        if payload and payload not in discovered:
            discovered.append(payload)
            player["discovered"] = discovered
            reward_text = f"Открыт рецепт: {ELEMENTS.get(payload, payload)}."
        else:
            inventory = player.get("inventory", {})
            inventory["pack_m"] = inventory.get("pack_m", 0) + 1
            player["inventory"] = inventory
            reward_text = "Рецепт уже был известен, поэтому архив выдал запасной средний набор."
    else:
        inventory = player.get("inventory", {})
        if payload not in inventory and get_used_slots(inventory) >= STORAGE_CAPACITY.get(player.get("buildings", {}).get("storage", 0), 20):
            return False, "На складе нет места под разовый дар Наследия."
        inventory[payload] = inventory.get(payload, 0) + amount
        player["inventory"] = inventory
        reward_text = f"Разовый дар получен: {ELEMENTS.get(payload, payload)} x{amount}."

    claimed.append(collection_id)
    player["claimed_legacy_rewards"] = claimed
    return True, reward_text

# --- UI АДМИНКИ ---

def build_lore_editor_home_text(lore_docs: list[dict]) -> str:
    lines = [
        "🛠 <b>РЕДАКТОР НАСЛЕДИЯ</b>",
        "",
        "Как работать:",
        "1. Выбери коллекцию кнопкой ниже или создай новую.",
        "2. Дальше бот сам подскажет, что отправить следующим сообщением.",
        "3. Для полного текста просто отправь рассказ после выбора шага 'Текст'.",
        "",
        "Коллекции:",
    ]
    for lore_doc in lore_docs:
        lines.append(f"• {lore_doc['emoji']} {html.escape(lore_doc['name'])} (<code>{html.escape(lore_doc['id'])}</code>)")
    return "\n".join(lines)


def build_lore_editor_home_keyboard(lore_docs: list[dict]) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, lore_doc in enumerate(lore_docs):
        builder.button(text=f"{lore_doc['emoji']} {lore_doc['name']}", callback_data=f"loreed:open:{encode_lore_ref(index)}")
    builder.button(text="➕ Новое наследие", callback_data="loreed:new")
    builder.button(text="🌱 Перезалить сиды", callback_data="loreed:seed")
    builder.button(text="📚 Краткая справка", callback_data="loreed:help")
    builder.adjust(1)
    return builder.as_markup()


def build_lore_editor_collection_text(lore_doc: dict) -> str:
    fragment_titles = ", ".join(fragment["title"] for fragment in lore_doc.get("fragments", [])) or "пока нет"
    return (
        "🧾 <b>КАРТОЧКА НАСЛЕДИЯ</b>\n"
        f"ID: <code>{html.escape(lore_doc['id'])}</code>\n"
        f"Имя: {html.escape(lore_doc['emoji'])} {html.escape(lore_doc['name'])}\n"
        f"Кнопка: {html.escape(lore_doc.get('button_name') or '-')}\n"
        f"Бонус: {html.escape(str(lore_doc['bonus_type']))} +{int(lore_doc['bonus_value'])}\n"
        f"Разовый дар: {html.escape(str(lore_doc['reward_type']))} / {html.escape(str(lore_doc['reward_payload']))} x{int(lore_doc['reward_amount'])}\n"
        f"Имя дара: {html.escape(lore_doc.get('reward_name') or '-')}\n"
        f"Порядок: {int(lore_doc.get('sort_order', 100) or 100)}\n"
        f"Обрывки: {html.escape(fragment_titles)}\n"
        f"Текст: {len((lore_doc.get('full_text') or '').strip())} символов"
    )


def build_lore_editor_collection_keyboard(lore_doc: dict) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    lore_ref = lore_doc.get("_ref", lore_doc["id"])
    builder.button(text="🪪 Основа", callback_data=f"loreed:editbasic:{lore_ref}")
    builder.button(text="🏷 Кнопка", callback_data=f"loreed:editbutton:{lore_ref}")
    builder.button(text="✨ Бонус", callback_data=f"loreed:editbonus:{lore_ref}")
    builder.button(text="🎁 Награда", callback_data=f"loreed:editreward:{lore_ref}")
    builder.button(text="🧩 Обрывки", callback_data=f"loreed:editfrags:{lore_ref}")
    builder.button(text="📖 Текст", callback_data=f"loreed:edittext:{lore_ref}")
    builder.button(text="👁 Предпросмотр", callback_data=f"loreed:preview:{lore_ref}")
    builder.button(text="🔙 К списку", callback_data="loreed:home")
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def parse_key_value_lines(text: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip().lower()] = value.strip()
    return data


def parse_fragments_text(text: str) -> list[dict]:
    fragments = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line[0].isdigit() and "." in line:
            line = line.split(".", 1)[1].strip()
        parts = [part.strip() for part in line.split("|", 2)]
        if len(parts) < 3:
            raise ValueError("Каждый обрывок нужно писать так: id | Название | Короткий анонс")
        fragments.append({"id": parts[0], "title": parts[1], "preview": parts[2]})
    if len(fragments) != 3:
        raise ValueError("Нужно прислать ровно 3 строки с обрывками.")
    return fragments


def namespace_fragment_ids(lore_id: str, fragments: list[dict]) -> list[dict]:
    namespaced = []
    seen: set[str] = set()
    prefix = f"{str(lore_id).strip()}_"
    for index, fragment in enumerate(fragments, start=1):
        raw_id = str(fragment.get("id", "")).strip()
        suffix = raw_id[len(prefix):] if raw_id.startswith(prefix) else raw_id
        suffix = suffix or f"frag_{index}"
        namespaced_id = f"{prefix}{suffix}"
        if namespaced_id in seen:
            raise ValueError("ID обрывков внутри одного наследия должны быть разными.")
        seen.add(namespaced_id)
        namespaced.append({"id": namespaced_id, "title": fragment.get("title", ""), "preview": fragment.get("preview", "")})
    return namespaced

async def show_lore_editor_home(target_message: types.Message):
    await ensure_default_lore()
    lore_docs = await get_all_lore_docs()
    await target_message.answer(
        build_lore_editor_home_text(lore_docs),
        reply_markup=build_lore_editor_home_keyboard(lore_docs),
        parse_mode="HTML",
    )


async def show_lore_editor_collection(target_message: types.Message, lore_id: str):
    lore_doc = await get_lore_doc_by_ref(lore_id)
    if not lore_doc:
        return await target_message.answer("Коллекция не найдена.")
    lore_doc["_ref"] = await get_lore_ref_by_id(lore_doc["id"])
    await target_message.answer(
        build_lore_editor_collection_text(lore_doc),
        reply_markup=build_lore_editor_collection_keyboard(lore_doc),
        parse_mode="HTML",
    )

async def render_heritage_main(message: types.Message, user_id: int, user_name: str, page: int = 0):
    player = await get_or_create_player(user_id, user_name)
    lore_docs = await get_all_lore_docs()
    await message.edit_text(
        build_heritage_main_text(player, lore_docs, page),
        reply_markup=build_heritage_main_keyboard(player, lore_docs, page),
        parse_mode="HTML",
    )

# --- ИГРОВЫЕ ХЕНДЛЕРЫ ---

@router.callback_query(F.data == "heritage_open")
async def heritage_open(callback: types.CallbackQuery):
    await render_heritage_main(callback.message, callback.from_user.id, callback.from_user.first_name, 0)
    await callback.answer()


@router.callback_query(F.data.startswith("heritage_page:"))
async def heritage_page(callback: types.CallbackQuery):
    page = int(callback.data.split(":")[1])
    await render_heritage_main(callback.message, callback.from_user.id, callback.from_user.first_name, page)
    await callback.answer()


@router.callback_query(F.data.startswith("heritage_view:"))
async def heritage_view(callback: types.CallbackQuery):
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    parts = callback.data.split(":")
    lore_doc = await get_lore_doc_by_ref(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0
    if not lore_doc:
        return await callback.answer("Коллекция не найдена.", show_alert=True)
    lore_doc["_ref"] = await get_lore_ref_by_id(lore_doc["id"])
    await callback.message.edit_text(
        build_collection_detail_text(player, lore_doc),
        reply_markup=build_collection_detail_keyboard(player, lore_doc, page),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("heritage_read:"))
async def heritage_read(callback: types.CallbackQuery):
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    parts = callback.data.split(":")
    lore_doc = await get_lore_doc_by_ref(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0
    if not lore_doc:
        return await callback.answer("Коллекция не найдена.", show_alert=True)
    lore_doc["_ref"] = await get_lore_ref_by_id(lore_doc["id"])
    owned, total = get_collection_progress(player, lore_doc)
    if owned != total or total == 0:
        return await callback.answer("Сначала собери все обрывки.", show_alert=True)
    await callback.message.edit_text(
        build_read_text(lore_doc),
        reply_markup=build_read_keyboard(lore_doc, page),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("heritage_install_prompt:"))
async def heritage_install_prompt(callback: types.CallbackQuery):
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    parts = callback.data.split(":")
    lore_doc = await get_lore_doc_by_ref(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0
    if not lore_doc:
        return await callback.answer("Коллекция не найдена.", show_alert=True)
    lore_doc["_ref"] = await get_lore_ref_by_id(lore_doc["id"])
    owned, total = get_collection_progress(player, lore_doc)
    if owned != total or total == 0:
        return await callback.answer("Нужно собрать коллекцию целиком.", show_alert=True)
    await callback.message.edit_text(
        build_stand_pick_text(player, lore_doc),
        reply_markup=build_stand_pick_keyboard(player, lore_doc, page),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("heritage_install:"))
async def heritage_install(callback: types.CallbackQuery):
    _, collection_ref, stand_raw, *rest = callback.data.split(":")
    stand_index = int(stand_raw)
    page = int(rest[0]) if rest else 0
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    lore_doc = await get_lore_doc_by_ref(collection_ref)
    if not lore_doc:
        return await callback.answer("Коллекция не найдена.", show_alert=True)
    owned, total = get_collection_progress(player, lore_doc)
    if owned != total or total == 0:
        return await callback.answer("Коллекция ещё не собрана.", show_alert=True)

    stands = list(player.get("active_stands", []))
    if stand_index < 0 or stand_index >= len(stands):
        return await callback.answer("Такого стенда нет.", show_alert=True)
    cost = get_install_cost(lore_doc)
    inventory = player.get("inventory", {})
    if inventory.get("energy", 0) < cost:
        return await callback.answer(f"Нужно ⚡ {cost} для зарядки стенда.", show_alert=True)

    ok, reward_text = await grant_first_legacy_reward(player, lore_doc)
    if not ok:
        return await callback.answer(reward_text, show_alert=True)

    inventory["energy"] -= cost
    if inventory.get("energy", 0) <= 0:
        inventory.pop("energy", None)
    stands[stand_index] = lore_doc["id"]
    player["inventory"] = inventory
    player["active_stands"] = stands

    await db.alchemy_players.update_one(
        {"user_id": callback.from_user.id},
        {
            "$set": {
                "inventory": player.get("inventory", {}),
                "active_stands": player.get("active_stands", []),
                "claimed_legacy_rewards": player.get("claimed_legacy_rewards", []),
                "fighters": player.get("fighters", []),
                "discovered": player.get("discovered", []),
            }
        },
    )
    message = f"{lore_doc['name']} установлено на стенд {stand_index + 1}."
    if reward_text:
        message += f"\n{reward_text}"
    await callback.answer(message, show_alert=True)
    await render_heritage_main(callback.message, callback.from_user.id, callback.from_user.first_name, page)

@router.callback_query(F.data.startswith("heritage_remove:"))
async def heritage_remove(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    stand_index = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    stands = list(player.get("active_stands", []))
    if stand_index < 0 or stand_index >= len(stands) or not stands[stand_index]:
        return await callback.answer("Стенд уже пуст.", show_alert=True)
    stands[stand_index] = None
    await db.alchemy_players.update_one(
        {"user_id": callback.from_user.id},
        {"$set": {"active_stands": stands}},
    )
    await callback.answer("Экспонат снят. При повторной установке нужна новая зарядка.", show_alert=True)
    await render_heritage_main(callback.message, callback.from_user.id, callback.from_user.first_name, page)


@router.callback_query(F.data.startswith("heritage_stand:"))
async def heritage_stand(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    stand_index = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    stands = list(player.get("active_stands", []))
    if stand_index < 0 or stand_index >= len(stands) or not stands[stand_index]:
        return await callback.answer("Стенд пуст.", show_alert=True)
    lore_doc = await get_lore_doc(stands[stand_index])
    if not lore_doc:
        return await callback.answer("Экспонат потерялся из архива.", show_alert=True)
    lore_doc["_ref"] = await get_lore_ref_by_id(lore_doc["id"])
    await callback.message.edit_text(
        build_stand_detail_text(player, lore_doc, stand_index),
        reply_markup=build_stand_detail_keyboard(stand_index, lore_doc["_ref"], page),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("heritage_empty:"))
async def heritage_empty(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    stand_index = int(parts[1])
    await callback.answer(f"Стенд {stand_index + 1} свободен. Установи коллекцию, чтобы активировать бонус.", show_alert=True)


@router.callback_query(F.data == "heritage_help")
async def heritage_help(callback: types.CallbackQuery):
    page = 0
    await callback.message.edit_text(
        build_help_text(),
        reply_markup=build_help_keyboard(page),
        parse_mode="HTML",
    )
    await callback.answer()

# --- ХЕНДЛЕРЫ АДМИНКИ (ЧЕРЕЗ FSM) ---

@router.message(Command("lore_help", "справкалор"))
async def lore_help(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer(
        "📚 <b>СПРАВКА ПО ЛОРУ</b>\n\n"
        "Проще всего так:\n"
        "1. <code>/lore_edit</code>\n"
        "2. Нажми коллекцию или <code>Новое наследие</code>\n"
        "3. Выбери шаг кнопкой\n"
        "4. Отправь следующее сообщение по шаблону от бота\n\n"
        "Что руками помнить не нужно:\n"
        "• для текста просто отправляешь рассказ\n"
        "• для обрывков нужны 3 строки <code>id | Название | Анонс</code>\n"
        "• бонус и награду бот подсказывает отдельным шагом",
        parse_mode="HTML",
    )

@router.callback_query(F.data == "loreed:help")
async def loreed_help(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.answer(
        "📚 <b>КРАТКО ПО ЛОРУ</b>\n\n"
        "1. Жми коллекцию или <code>Новое наследие</code>\n"
        "2. Выбирай шаг: Основа, Бонус, Награда, Обрывки, Текст\n"
        "3. Бот сам пишет шаблон\n"
        "4. Просто отправь следующее сообщение по шаблону\n\n"
        "Самое важное:\n"
        "• Обрывки - это 3 строки <code>id | Название | Анонс</code>\n"
        "• Текст - просто отправляется обычным сообщением\n"
        "• <code>seed</code> перезаливает стандартные сиды из JSON\n"
        "• <code>/cancel_lore</code> для отмены ввода",
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "loreed:new")
async def loreed_new(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(LoreEditorStates.new_lore)
    await callback.message.answer(
        "➕ <b>НОВОЕ НАСЛЕДИЕ</b>\n\n"
        "Пришли следующим сообщением:\n"
        "<code>id: void_legacy</code>\n"
        "<code>name: Наследие Пустоты</code>\n"
        "<code>emoji: 🌌</code>\n"
        "<code>button: Пустота</code>\n"
        "<code>sort: 40</code>\n\n"
        "<code>sort</code> — это порядок в списке: чем меньше число, тем выше наследие.\n\n"
        "После этого откроется карточка, и ты добьешь бонус, награду, обрывки и текст кнопками.",
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data == "loreed:seed")
async def loreed_seed(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    await ensure_default_lore()
    await callback.message.answer("🌱 Стартовые наследия обновлены (если файл сидов существует).")
    await callback.answer()

@router.message(Command("lore_edit"))
async def lore_edit(message: types.Message, command: CommandObject, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.clear()
    
    args = (command.args or "").strip().lower()
    if args == "seed":
        await ensure_default_lore()
        return await message.answer("🌱 Стартовые наследия обновлены.")
    await show_lore_editor_home(message)

@router.message(Command("cancel_lore"))
async def cancel_lore_edit(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.clear()
    await message.answer("Отменено. Ввод остановлен.")

@router.callback_query(F.data == "loreed:home")
async def loreed_home(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.clear()
    await show_lore_editor_home(callback.message)
    await callback.answer()

@router.callback_query(F.data.startswith("loreed:open:"))
async def loreed_open(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.clear()
    
    lore_ref = callback.data.split(":", 2)[2]
    lore_doc = await get_lore_doc_by_ref(lore_ref)
    if not lore_doc:
        return await callback.answer("Коллекция не найдена.", show_alert=True)
    lore_doc["_ref"] = await get_lore_ref_by_id(lore_doc["id"])
    await callback.message.answer(
        build_lore_editor_collection_text(lore_doc),
        reply_markup=build_lore_editor_collection_keyboard(lore_doc),
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("loreed:editbasic:"))
async def loreed_editbasic(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    lore_ref = callback.data.split(":")[2]
    lore_doc = await get_lore_doc_by_ref(lore_ref)
    if not lore_doc:
        return await callback.answer("Коллекция не найдена.", show_alert=True)
    
    await state.update_data(lore_id=lore_ref)
    await state.set_state(LoreEditorStates.edit_basic)
    
    await callback.message.answer(
        "🪪 <b>ОСНОВА НАСЛЕДИЯ</b>\n\n"
        "Пришли следующим сообщением:\n"
        f"<code>id: {html.escape(lore_doc['id'])}</code>\n"
        "<code>name: Название коллекции</code>\n"
        "<code>emoji: 📜</code>\n"
        f"<code>button: {html.escape(lore_doc.get('button_name', ''))}</code>\n"
        "<code>sort: 40</code>\n\n"
        "<code>sort</code> — порядок в списке. Меньше число поднимает коллекцию выше.\n(/cancel_lore для отмены)",
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("loreed:editbutton:"))
async def loreed_editbutton(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    lore_ref = callback.data.split(":")[2]
    lore_doc = await get_lore_doc_by_ref(lore_ref)
    if not lore_doc:
        return await callback.answer("Коллекция не найдена.", show_alert=True)
        
    await state.update_data(lore_id=lore_ref)
    await state.set_state(LoreEditorStates.edit_button)
    
    await callback.message.answer(
        "🏷 <b>ИМЯ КНОПКИ</b>\n\n"
        "Пришли следующим сообщением:\n"
        f"<code>button: {html.escape(lore_doc.get('button_name') or '')}</code>\n\n"
        "Лучше держать до 9 символов. Полное название наследия это не меняет.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("loreed:editbonus:"))
async def loreed_editbonus(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    lore_ref = callback.data.split(":")[2]
    await state.update_data(lore_id=lore_ref)
    await state.set_state(LoreEditorStates.edit_bonus)
    
    await callback.message.answer(
        "✨ <b>БОНУС НАСЛЕДИЯ</b>\n\n"
        "Пришли следующим сообщением:\n"
        "<code>bonus: spirit_f</code>\n"
        "<code>bonus_value: 3</code>\n\n"
        "Примеры bonus:\n"
        "<code>spirit_f</code>, <code>spirit_w</code>, <code>spirit_e</code>, <code>spirit_a</code>, <code>metal</code>, <code>energy</code>, <code>pack_s</code>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("loreed:editreward:"))
async def loreed_editreward(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    lore_ref = callback.data.split(":")[2]
    await state.update_data(lore_id=lore_ref)
    await state.set_state(LoreEditorStates.edit_reward)
    
    await callback.message.answer(
        "🎁 <b>НАГРАДА НАСЛЕДИЯ</b>\n\n"
        "Пришли следующим сообщением:\n"
        "<code>reward_type: item</code>\n"
        "<code>reward_payload: pack_l</code>\n"
        "<code>reward_amount: 1</code>\n"
        "<code>reward_name:</code>\n\n"
        "Типы: <code>item</code>, <code>recipe</code>, <code>fighter</code>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("loreed:editfrags:"))
async def loreed_editfrags(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    lore_ref = callback.data.split(":")[2]
    await state.update_data(lore_id=lore_ref)
    await state.set_state(LoreEditorStates.edit_fragments)
    
    await callback.message.answer(
        "🧩 <b>ОБРЫВКИ</b>\n\n"
        "Пришли 3 строки в формате:\n"
        "<code>frag_1 | Название | Короткий анонс</code>\n"
        "<code>frag_2 | Название | Короткий анонс</code>\n"
        "<code>frag_3 | Название | Короткий анонс</code>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("loreed:edittext:"))
async def loreed_edittext(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    lore_ref = callback.data.split(":")[2]
    await state.update_data(lore_id=lore_ref)
    await state.set_state(LoreEditorStates.edit_text)
    
    await callback.message.answer(
        "📖 <b>ПОЛНЫЙ ТЕКСТ</b>\n\n"
        "Теперь просто пришли следующим сообщением весь рассказ целиком.\n"
        "Ничего дописывать не надо.",
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("loreed:preview:"))
async def loreed_preview(callback: types.CallbackQuery):
    lore_ref = callback.data.split(":")[2]
    lore_doc = await get_lore_doc_by_ref(lore_ref)
    if not lore_doc:
        return await callback.answer("Коллекция не найдена.", show_alert=True)
    await callback.message.answer(build_read_text(lore_doc), parse_mode="HTML")
    await callback.answer("Предпросмотр отправлен.")


@router.message(
    StateFilter(
        LoreEditorStates.new_lore,
        LoreEditorStates.edit_basic,
        LoreEditorStates.edit_button,
        LoreEditorStates.edit_bonus,
        LoreEditorStates.edit_reward,
        LoreEditorStates.edit_fragments,
        LoreEditorStates.edit_text,
    )
)
async def process_lore_editor_input(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await state.clear()
        
    current_state = await state.get_state()
    state_data = await state.get_data()
    lore_id = state_data.get("lore_id")

    try:
        if current_state == LoreEditorStates.new_lore:
            data = parse_key_value_lines(message.text or "")
            lore_id = data.get("id", "").strip()
            if not lore_id:
                return await message.answer("Нужен <code>id:</code>. Пример: <code>id: void_legacy</code>", parse_mode="HTML")
            
            doc = normalize_lore_doc(
                {
                    "id": lore_id,
                    "name": data.get("name", lore_id),
                    "button_name": data.get("button", ""),
                    "emoji": data.get("emoji", "📜"),
                    "sort_order": int(data.get("sort", "100") or 100),
                    "bonus_type": "spirit_f",
                    "bonus_value": 1,
                    "reward_type": "item",
                    "reward_payload": "pack_s",
                    "reward_amount": 1,
                    "reward_name": "",
                    "fragments": [],
                    "full_text": "",
                }
            )
            await db.alchemy_lore.update_one({"id": lore_id}, {"$set": doc}, upsert=True)
            await state.clear()
            await message.answer(f"Создано наследие <code>{html.escape(lore_id)}</code>. Теперь добей его по кнопкам ниже.", parse_mode="HTML")
            return await show_lore_editor_collection(message, lore_id)

        # Для всех остальных состояний нам нужен существующий документ
        lore_doc = await get_lore_doc_by_ref(lore_id)
        if not lore_doc:
            await state.clear()
            return await message.answer("Коллекция потерялась. Открой <code>/lore_edit</code> заново.", parse_mode="HTML")
            
        real_lore_id = lore_doc["id"]

        if current_state == LoreEditorStates.edit_basic:
            data = parse_key_value_lines(message.text or "")
            updates = {
                "id": data.get("id", lore_doc["id"]),
                "name": data.get("name", lore_doc["name"]),
                "button_name": str(data.get("button", lore_doc.get("button_name", ""))).strip()[:9],
                "emoji": data.get("emoji", lore_doc["emoji"]),
                "sort_order": int(data.get("sort", str(lore_doc.get("sort_order", 100))) or lore_doc.get("sort_order", 100)),
            }
            await db.alchemy_lore.update_one({"id": real_lore_id}, {"$set": updates})
            real_lore_id = updates["id"]
            
        elif current_state == LoreEditorStates.edit_button:
            data = parse_key_value_lines(message.text or "")
            button_name = str(data.get("button", lore_doc.get("button_name", ""))).strip()[:9]
            await db.alchemy_lore.update_one({"id": real_lore_id}, {"$set": {"button_name": button_name}})

        elif current_state == LoreEditorStates.edit_bonus:
            data = parse_key_value_lines(message.text or "")
            await db.alchemy_lore.update_one(
                {"id": real_lore_id},
                {
                    "$set": {
                        "bonus_type": data.get("bonus", lore_doc["bonus_type"]),
                        "bonus_value": max(1, int(data.get("bonus_value", str(lore_doc["bonus_value"])) or lore_doc["bonus_value"])),
                    }
                },
            )

        elif current_state == LoreEditorStates.edit_reward:
            data = parse_key_value_lines(message.text or "")
            await db.alchemy_lore.update_one(
                {"id": real_lore_id},
                {
                    "$set": {
                        "reward_type": data.get("reward_type", lore_doc["reward_type"]),
                        "reward_payload": data.get("reward_payload", lore_doc.get("reward_payload", "")),
                        "reward_amount": max(1, int(data.get("reward_amount", str(lore_doc.get("reward_amount", 1))) or lore_doc.get("reward_amount", 1))),
                        "reward_name": data.get("reward_name", lore_doc.get("reward_name", "")),
                    }
                },
            )

        elif current_state == LoreEditorStates.edit_fragments:
            fragments = namespace_fragment_ids(real_lore_id, parse_fragments_text(message.text or ""))
            await db.alchemy_lore.update_one({"id": real_lore_id}, {"$set": {"fragments": fragments}})

        elif current_state == LoreEditorStates.edit_text:
            await db.alchemy_lore.update_one({"id": real_lore_id}, {"$set": {"full_text": message.text or ""}})

        await state.clear()
        await message.answer("✅ Сохранено.")
        await show_lore_editor_collection(message, real_lore_id)

    except ValueError as exc:
        await message.answer(f"❌ {html.escape(str(exc))}")
