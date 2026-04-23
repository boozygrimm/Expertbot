import random
import time

from aiogram import F, Router, types
from aiogram.filters import Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import ADMIN_ID, get_msk_now
from database import db
from handlers.alc_core import STORAGE_CAPACITY, get_heritage_slots, get_or_create_player, get_used_slots
from handlers.alc_fighters import create_fighter, get_barracks_capacity
from handlers.alc_recipes import BATTLE_UNITS, ELEMENTS

router = Router()

LEGACY_FRAGMENT_DAILY_LIMIT = 2
LEGACY_INSTALL_ENERGY = {1: 50, 2: 75, 3: 100}
DEFAULT_REWARD_ITEM_AMOUNT = 1

DEFAULT_LORE_COLLECTIONS = [
    {
        "id": "goblin_legacy",
        "name": "Наследие Архипа",
        "emoji": "🧀",
        "bonus_type": "spirit_f",
        "bonus_value": 3,
        "reward_type": "fighter",
        "reward_payload": "saboteur",
        "reward_amount": 1,
        "reward_name": "Архип",
        "sort_order": 10,
        "fragments": [
            {"id": "goblin_1", "title": "Диплом Архипа", "preview": "Разумный дорблю с амбициями демиурга решил создать Идеальное Свидание в пустоте."},
            {"id": "goblin_2", "title": "Болото и Жизнь", "preview": "Воздух, Огонь, Земля и Вода быстро превратили романтику в энергетическое болото."},
            {"id": "goblin_3", "title": "Зомби с бензопилами", "preview": "Одна неловкая лапка, и вместо вечера при свечах по миру побежали гнилые признания в любви."},
        ],
        "full_text": (
            "Знакомьтесь, это Архип - разумный плесневелый сыр сорта Дорблю с амбициями демиурга и дипломом курсов "
            "\"Сотвори мир за шесть сек\". Архип сидел в пустоте и страдал от экзистенциального голода. Ему позарез "
            "нужно было создать Идеальное Свидание, потому что одиночество в безвоздушном пространстве - это не стильно.\n\n"
            "В арсенале Архипа была логика игры Doodle Alchemy, где здравый смысл спит, а метафоры бьют по голове. "
            "Сначала он взял Воздух и Огонь. Казалось бы, получится духота, но нет - по рецепту родилась Энергия. "
            "\"Бодро\", - подумал Архип, пуская спору радости, - \"теперь добавим огоньку в прямом смысле\". "
            "Он швырнул в котел Землю и Воду, получив Болото.\n\n"
            "\"Так\", - пробормотал Архип, - \"если смешать Болото и Энергию, я получу Жизнь. А какая жизнь без Света?\" "
            "Смешиваем Огонь и Энергию - получаем Электричество!\n\n"
            "Но тут всё пошло не по ГОСТу. Пока Архип тянулся за ингредиентом Спирт, чтобы вечер перестал быть томным, "
            "его плесневелая лапка соскользнула. Вместо того чтобы аккуратно соединить Жизнь и Электричество для создания "
            "хотя бы Франкенштейна-кавалера, он случайно задел полку с Бактериями и Трупом.\n\n"
            "Мир икнул. Алхимическая реакция пошла вразнос. Вместо романтического ужина при свечах из Жизни и Камня в котле "
            "образовалась критическая масса Зомби, которые мгновенно скрестились с Инструментами. По комнате начали бегать "
            "живые бензопилы с гнилыми зубами, пытающиеся признаться в любви вакууму.\n\n"
            "Архип попытался исправить ситуацию, бросив в хаос Песок и Стекло, надеясь на Время, но попал в Радиацию. "
            "Пространство свернулось в узел, законы физики вышли покурить и не вернулись. Небо стало цвета просроченного "
            "кефира, а гравитация начала работать только по четным секундам.\n\n"
            "Пшик, пшик, пшик... А это так должно было?!! БАМММ!"
        ),
    },
    {
        "id": "garage_legacy",
        "name": "Наследие Аркадия",
        "emoji": "🛠",
        "bonus_type": "metal",
        "bonus_value": 3,
        "reward_type": "recipe",
        "reward_payload": "robot",
        "reward_amount": 1,
        "reward_name": "",
        "sort_order": 20,
        "fragments": [
            {"id": "garage_1", "title": "Диплом Аркадия", "preview": "Бог-недоучка из заборостроительного колледжа решил собрать Идеальное Свидание."},
            {"id": "garage_2", "title": "Лава и Пар", "preview": "Романтика быстро стала слишком жаркой, душной и подозрительно промышленной."},
            {"id": "garage_3", "title": "Лавовый Уксус", "preview": "Кот Шредингера, бактерии и спирт превратили реальность в маринованный апокалипсис."},
        ],
        "full_text": (
            "Знакомьтесь, это Аркадий. Аркадий - бог-недоучка с дипломом заборостроительного колледжа и амбициями создателя "
            "метавселенных. Сегодня в его мастерской, напоминающей гараж соседа-плюшкина, пахнет озоном и жженой карамелью. "
            "Аркадий решил, что миру не хватает Идеального Свидания.\n\n"
            "Вместо того чтобы просто купить цветы, он открыл старое доброе приложение Doodle Alchemy и начал швырять концепции "
            "в свой виртуальный казан.\n\n"
            "\"Так\", - пробормотал Аркадий, потирая руки. - \"Чтобы дама не заскучала, нам нужен Огонь. Плюс Земля. Получаем Лаву. "
            "Романтично? Безусловно. Жарко, свежо, течет красиво\".\n\n"
            "Но для полноценного рандеву одной лавы мало. Аркадий решил добавить атмосферы и бухнул в котел Воздух и Воду, надеясь "
            "на легкий бриз. Но законы алхимии неумолимы: вместо морского настроения получился Пар. Теперь в мастерской стало не только "
            "жарко, но и чертовски душно - прямо как на лекции по термодинамике.\n\n"
            "Кульминация наступила, когда Аркадий потянулся за элементом Жизнь, чтобы свидание было... ну, живым. Но в этот момент "
            "на клавиатуру прыгнул его домашний питомец - разумный Кот Шредингера, который одновременно хотел есть и не хотел. "
            "Аркадий дернулся и вместо Жизни случайно выделил Бактерии и совместил их со Спиртом.\n\n"
            "Мир вздрогнул. По логике Doodle Alchemy, Спирт + Бактерии = Уксус. Но когда у тебя в котле уже бурлит Лава и Пар, "
            "уксусная эссенция сработала как детонатор для реальности. Стены гаража начали превращаться в гигантские маринованные огурцы. "
            "Физика обиделась и ушла, хлопнув дверью: гравитация теперь работала только по четным секундам, а время потекло со вкусом "
            "вишневой косточки.\n\n"
            "Лавовый Уксус начал пожирать пространство, аннигилируя само понятие свидание. Вместо идеального вечера получился "
            "апокалипсис с запахом консервации на зиму. Аркадий смотрел, как его вселенная схлопывается в точку, которая подозрительно "
            "напоминала пустую банку из-под майонеза.\n\n"
            "Пшик, пшик, пшик... А это так должно было?!! БАМММ!"
        ),
    },
    {
        "id": "forge_legacy",
        "name": "Наследие Печной Лиги",
        "emoji": "⚒️",
        "bonus_type": "metal",
        "bonus_value": 3,
        "reward_type": "recipe",
        "reward_payload": "robot",
        "reward_amount": 1,
        "reward_name": "",
        "sort_order": 30,
        "fragments": [
            {"id": "forge_1", "title": "Надбитый жетон", "preview": "Лига считала металл честнее слов: он хотя бы звенит, когда врёт."},
            {"id": "forge_2", "title": "Чертёж без углов", "preview": "Их печи строили так, чтобы жар сначала пугал, а потом убеждал."},
            {"id": "forge_3", "title": "Окалина на письме", "preview": "Мастера ковали механизмы для войн, которых надеялись не увидеть."},
        ],
        "full_text": (
            "Печная Лига выросла не вокруг кузницы, а вокруг долга. Каждый мастер приносил в общую плавку металл, историю и хотя бы одну ненависть.\n\n"
            "Их цеха пылали ночами, потому что днём было слишком много свидетелей. Внутри рождались замки, механизмы и первые корпуса тех машин, "
            "которые позже стали считать слишком умными, чтобы доверять им лопаты.\n\n"
            "Когда Лига пала, остывшие печи нашли целыми. Будто мастера просто вышли на минуту и забыли вернуться, оставив после себя только стук молотов в стенах.\n\n"
            "Если задержаться там подольше, эхо отзывается очень знакомо: Пшик, пшик... БАМММ!"
        ),
    },
]


def normalize_lore_doc(doc: dict) -> dict:
    current = dict(doc)
    current["id"] = str(current.get("id", "")).strip()
    current["name"] = str(current.get("name", current["id"])).strip() or current["id"]
    current["emoji"] = str(current.get("emoji", "📜")).strip() or "📜"
    current["bonus_type"] = str(current.get("bonus_type", "spirit_f")).strip() or "spirit_f"
    current["bonus_value"] = max(1, min(3, int(current.get("bonus_value", 1) or 1)))
    current["reward_type"] = str(current.get("reward_type", "item")).strip() or "item"
    current["reward_payload"] = str(current.get("reward_payload", "")).strip()
    current["reward_amount"] = max(1, int(current.get("reward_amount", DEFAULT_REWARD_ITEM_AMOUNT) or DEFAULT_REWARD_ITEM_AMOUNT))
    current["reward_name"] = str(current.get("reward_name", "")).strip()
    current["sort_order"] = int(current.get("sort_order", 100) or 100)
    current["full_text"] = str(current.get("full_text", "")).strip()
    current["fragments"] = [
        {
            "id": str(fragment.get("id", "")).strip(),
            "title": str(fragment.get("title", fragment.get("id", "Обрывок"))).strip() or "Обрывок",
            "preview": str(fragment.get("preview", "")).strip(),
        }
        for fragment in current.get("fragments", [])
        if str(fragment.get("id", "")).strip()
    ][:3]
    return current


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
    return LEGACY_INSTALL_ENERGY.get(int(lore_doc.get("bonus_value", 1)), 100)


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


async def ensure_default_lore():
    for doc in DEFAULT_LORE_COLLECTIONS:
        normalized = normalize_lore_doc(doc)
        await db.alchemy_lore.update_one(
            {"id": normalized["id"]},
            {"$setOnInsert": normalized},
            upsert=True,
        )


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


def build_heritage_main_text(player: dict, lore_docs: list[dict]) -> str:
    level = player.get("buildings", {}).get("heritage_center", 1)
    stands = list(player.get("active_stands", []))
    multiplier = get_heritage_multiplier(player)
    lines = [
        f"📚 **НАСЛЕДИЕ** (Ур. {level})",
        f"Стендов доступно: {len(stands)}",
    ]
    if multiplier > 1:
        lines.append("Мастер-архив активен: все бонусы на стендах удвоены.")
    lines.append("")
    lines.append("Витрина:")
    lore_by_id = {doc["id"]: doc for doc in lore_docs}
    for idx, stand_id in enumerate(stands, start=1):
        if stand_id and stand_id in lore_by_id:
            lore_doc = lore_by_id[stand_id]
            amount = lore_doc["bonus_value"] * multiplier
            lines.append(f"💎 Стенд {idx}: {lore_doc['name']} [{get_bonus_label(lore_doc['bonus_type'], amount)}]")
        else:
            lines.append(f"▫️ Стенд {idx}: пусто")
    lines.append("")
    lines.append("Коллекции:")
    for lore_doc in lore_docs:
        owned, total = get_collection_progress(player, lore_doc)
        marker = "✅" if owned == total and total > 0 else "📜"
        lines.append(f"{marker} {lore_doc['emoji']} {lore_doc['name']} {owned}/{total}")
    return "\n".join(lines)


def build_heritage_main_keyboard(player: dict, lore_docs: list[dict]) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    stands = list(player.get("active_stands", []))
    lore_by_id = {doc["id"]: doc for doc in lore_docs}
    for idx, stand_id in enumerate(stands):
        if stand_id and stand_id in lore_by_id:
            builder.button(text=f"💎 Стенд {idx + 1}", callback_data=f"heritage_stand:{idx}")
    for lore_doc in lore_docs:
        owned, total = get_collection_progress(player, lore_doc)
        builder.button(
            text=f"{lore_doc['emoji']} {lore_doc['name']} {owned}/{total}",
            callback_data=f"heritage_view:{lore_doc['id']}",
        )
    builder.button(text="🔙 В Штаб", callback_data="alc:base_return")
    builder.adjust(1)
    return builder.as_markup()


def build_collection_detail_text(player: dict, lore_doc: dict) -> str:
    owned, total = get_collection_progress(player, lore_doc)
    lines = [
        f"{lore_doc['emoji']} **{lore_doc['name']}**",
        f"Собрано: {owned}/{total}",
        f"Пассивный бонус: {get_bonus_label(lore_doc['bonus_type'], lore_doc['bonus_value'])}",
        f"Разовый дар: {get_reward_label(lore_doc)}",
        f"Цена установки: ⚡ {get_install_cost(lore_doc)}",
        "",
        "Обрывки:",
    ]
    fragments = player.get("lore_fragments", {})
    for fragment in lore_doc.get("fragments", []):
        marker = "✅" if fragments.get(fragment["id"]) else "▫️"
        lines.append(f"{marker} {fragment['title']}")
        if fragment.get("preview"):
            lines.append(f"   _{fragment['preview']}_")
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


def build_collection_detail_keyboard(player: dict, lore_doc: dict) -> types.InlineKeyboardMarkup:
    owned, total = get_collection_progress(player, lore_doc)
    builder = InlineKeyboardBuilder()
    if owned == total and total > 0:
        builder.button(text="📖 Читать целиком", callback_data=f"heritage_read:{lore_doc['id']}")
        builder.button(text="💎 Установить на стенд", callback_data=f"heritage_install_prompt:{lore_doc['id']}")
    active_slot = next((idx for idx, stand_id in enumerate(player.get("active_stands", [])) if stand_id == lore_doc["id"]), None)
    if active_slot is not None:
        builder.button(text="🧹 Снять со стенда", callback_data=f"heritage_remove:{active_slot}")
    builder.button(text="🔙 К наследию", callback_data="heritage_open")
    builder.adjust(1)
    return builder.as_markup()


def build_read_text(lore_doc: dict) -> str:
    return f"{lore_doc['emoji']} **{lore_doc['name']}**\n\n{with_bammm(lore_doc.get('full_text', ''))}"


def build_read_keyboard(lore_doc: dict) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data=f"heritage_view:{lore_doc['id']}")
    builder.adjust(1)
    return builder.as_markup()


def build_stand_pick_text(player: dict, lore_doc: dict) -> str:
    lines = [
        f"💎 **УСТАНОВКА: {lore_doc['name']}**",
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


def build_stand_pick_keyboard(player: dict, lore_doc: dict) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for idx, stand_id in enumerate(player.get("active_stands", [])):
        marker = "💎" if stand_id else "▫️"
        builder.button(text=f"{marker} Стенд {idx + 1}", callback_data=f"heritage_install:{lore_doc['id']}:{idx}")
    builder.button(text="🔙 Назад", callback_data=f"heritage_view:{lore_doc['id']}")
    builder.adjust(1)
    return builder.as_markup()


def build_stand_detail_text(player: dict, lore_doc: dict, stand_index: int) -> str:
    amount = lore_doc["bonus_value"] * get_heritage_multiplier(player)
    return (
        f"💎 **СТЕНД {stand_index + 1}**\n"
        f"Установлено: {lore_doc['name']}\n"
        f"Активный бонус: {get_bonus_label(lore_doc['bonus_type'], amount)}\n"
        f"Разовый дар: {get_reward_label(lore_doc)}"
    )


def build_stand_detail_keyboard(stand_index: int, lore_id: str) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📖 Открыть коллекцию", callback_data=f"heritage_view:{lore_id}")
    builder.button(text="🧹 Снять со стенда", callback_data=f"heritage_remove:{stand_index}")
    builder.button(text="🔙 К наследию", callback_data="heritage_open")
    builder.adjust(1)
    return builder.as_markup()


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


@router.callback_query(F.data == "heritage_open")
async def heritage_open(callback: types.CallbackQuery):
    await ensure_default_lore()
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    lore_docs = await get_all_lore_docs()
    await callback.message.edit_text(
        build_heritage_main_text(player, lore_docs),
        reply_markup=build_heritage_main_keyboard(player, lore_docs),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("heritage_view:"))
async def heritage_view(callback: types.CallbackQuery):
    await ensure_default_lore()
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    lore_doc = await get_lore_doc(callback.data.split(":")[1])
    if not lore_doc:
        return await callback.answer("Коллекция не найдена.", show_alert=True)
    await callback.message.edit_text(
        build_collection_detail_text(player, lore_doc),
        reply_markup=build_collection_detail_keyboard(player, lore_doc),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("heritage_read:"))
async def heritage_read(callback: types.CallbackQuery):
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    lore_doc = await get_lore_doc(callback.data.split(":")[1])
    if not lore_doc:
        return await callback.answer("Коллекция не найдена.", show_alert=True)
    owned, total = get_collection_progress(player, lore_doc)
    if owned != total or total == 0:
        return await callback.answer("Сначала собери все обрывки.", show_alert=True)
    await callback.message.edit_text(
        build_read_text(lore_doc),
        reply_markup=build_read_keyboard(lore_doc),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("heritage_install_prompt:"))
async def heritage_install_prompt(callback: types.CallbackQuery):
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    lore_doc = await get_lore_doc(callback.data.split(":")[1])
    if not lore_doc:
        return await callback.answer("Коллекция не найдена.", show_alert=True)
    owned, total = get_collection_progress(player, lore_doc)
    if owned != total or total == 0:
        return await callback.answer("Нужно собрать коллекцию целиком.", show_alert=True)
    await callback.message.edit_text(
        build_stand_pick_text(player, lore_doc),
        reply_markup=build_stand_pick_keyboard(player, lore_doc),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("heritage_install:"))
async def heritage_install(callback: types.CallbackQuery):
    _, collection_id, stand_raw = callback.data.split(":")
    stand_index = int(stand_raw)
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    lore_doc = await get_lore_doc(collection_id)
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
    stands[stand_index] = collection_id
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
    await heritage_open(callback)


@router.callback_query(F.data.startswith("heritage_remove:"))
async def heritage_remove(callback: types.CallbackQuery):
    stand_index = int(callback.data.split(":")[1])
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
    await heritage_open(callback)


@router.callback_query(F.data.startswith("heritage_stand:"))
async def heritage_stand(callback: types.CallbackQuery):
    stand_index = int(callback.data.split(":")[1])
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    stands = list(player.get("active_stands", []))
    if stand_index < 0 or stand_index >= len(stands) or not stands[stand_index]:
        return await callback.answer("Стенд пуст.", show_alert=True)
    lore_doc = await get_lore_doc(stands[stand_index])
    if not lore_doc:
        return await callback.answer("Экспонат потерялся из архива.", show_alert=True)
    await callback.message.edit_text(
        build_stand_detail_text(player, lore_doc, stand_index),
        reply_markup=build_stand_detail_keyboard(stand_index, lore_doc["id"]),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(Command("lore_edit"))
async def lore_edit(message: types.Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return
    await ensure_default_lore()
    args = (command.args or "").strip()
    if not args:
        return await message.answer(
            "Команды:\n"
            "/lore_edit list\n"
            "/lore_edit show <id>\n"
            "/lore_edit set <id>|<name>|<emoji>|<bonus_type>|<bonus_value>|<reward_type>|<reward_payload>|<reward_amount>|<reward_name>|<sort_order>\n"
            "/lore_edit text <id>  (текст брать из реплая)\n"
            "/lore_edit fragments <id>|frag_1:Название:Короткий анонс|frag_2:...|frag_3:...\n"
            "/lore_edit seed",
            parse_mode="Markdown",
        )

    if args == "seed":
        await ensure_default_lore()
        return await message.answer("Сиды Наследия обновлены.")

    if args == "list":
        docs = await get_all_lore_docs()
        if not docs:
            return await message.answer("Коллекций пока нет.")
        lines = ["📚 Коллекции:"]
        for doc in docs:
            lines.append(f"• `{doc['id']}` | {doc['name']} | {doc['bonus_type']} +{doc['bonus_value']} | {doc['reward_type']}")
        return await message.answer("\n".join(lines), parse_mode="Markdown")

    if args.startswith("show "):
        lore_id = args[5:].strip()
        lore_doc = await get_lore_doc(lore_id)
        if not lore_doc:
            return await message.answer("Коллекция не найдена.")
        fragment_lines = [f"{fragment['id']}:{fragment['title']}" for fragment in lore_doc.get("fragments", [])]
        return await message.answer(
            f"`{lore_doc['id']}`\n"
            f"{lore_doc['name']}\n"
            f"Бонус: {lore_doc['bonus_type']} +{lore_doc['bonus_value']}\n"
            f"Награда: {lore_doc['reward_type']} {lore_doc['reward_payload']} x{lore_doc['reward_amount']}\n"
            f"Имя награды: {lore_doc.get('reward_name', '')}\n"
            f"Фрагменты: {' | '.join(fragment_lines)}\n\n"
            f"{with_bammm(lore_doc.get('full_text', ''))[:1200]}",
            parse_mode="Markdown",
        )

    if args.startswith("set "):
        payload = args[4:].split("|")
        if len(payload) < 10:
            return await message.answer("Недостаточно параметров для set.")
        doc = {
            "id": payload[0].strip(),
            "name": payload[1].strip(),
            "emoji": payload[2].strip(),
            "bonus_type": payload[3].strip(),
            "bonus_value": int(payload[4].strip() or 1),
            "reward_type": payload[5].strip(),
            "reward_payload": payload[6].strip(),
            "reward_amount": int(payload[7].strip() or 1),
            "reward_name": payload[8].strip(),
            "sort_order": int(payload[9].strip() or 100),
        }
        normalized = normalize_lore_doc(doc)
        await db.alchemy_lore.update_one({"id": normalized["id"]}, {"$set": normalized}, upsert=True)
        return await message.answer(f"Коллекция `{normalized['id']}` сохранена.", parse_mode="Markdown")

    if args.startswith("text "):
        lore_id = args[5:].strip()
        lore_doc = await get_lore_doc(lore_id)
        if not lore_doc:
            return await message.answer("Коллекция не найдена.")
        full_text = ""
        if message.reply_to_message and message.reply_to_message.text:
            full_text = message.reply_to_message.text
        if not full_text:
            return await message.answer("Для text ответь командой на сообщение с полным текстом коллекции.")
        await db.alchemy_lore.update_one({"id": lore_id}, {"$set": {"full_text": full_text}})
        return await message.answer(f"Текст для `{lore_id}` обновлён.", parse_mode="Markdown")

    if args.startswith("fragments "):
        payload = args[10:].split("|")
        if len(payload) < 4:
            return await message.answer("Нужно указать id коллекции и три фрагмента.")
        lore_id = payload[0].strip()
        fragments = []
        for part in payload[1:4]:
            fields = [piece.strip() for piece in part.split(":", 2)]
            if len(fields) < 2:
                return await message.answer("Формат фрагмента: fragment_id:Название:Анонс")
            fragment_id = fields[0]
            title = fields[1]
            preview = fields[2] if len(fields) > 2 else ""
            fragments.append({"id": fragment_id, "title": title, "preview": preview})
        await db.alchemy_lore.update_one({"id": lore_id}, {"$set": {"fragments": fragments}})
        return await message.answer(f"Фрагменты для `{lore_id}` обновлены.", parse_mode="Markdown")

    await message.answer("Не понял подкоманду `/lore_edit`.", parse_mode="Markdown")


@router.message(Command("справкалор"))
async def lore_help(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    text = (
        "📚 **СПРАВКА ПО ЛОРУ**\n\n"
        "`/lore_edit list` - список коллекций\n"
        "`/lore_edit show <id>` - показать коллекцию\n"
        "`/lore_edit set <id>|<name>|<emoji>|<bonus_type>|<bonus_value>|<reward_type>|<reward_payload>|<reward_amount>|<reward_name>|<sort_order>` - метаданные\n"
        "`/lore_edit fragments <id>|frag1:Название:Анонс|frag2:...|frag3:...` - 3 обрывка\n"
        "Отправь полный текст обычным сообщением и ответь на него: `/lore_edit text <id>`\n"
        "`/lore_edit seed` - досоздать стартовые сиды\n\n"
        "Пример:\n"
        "1. `/lore_edit set void_legacy|Наследие Пустоты|🌌|spirit_a|3|item|pack_l|1||40`\n"
        "2. `/lore_edit fragments void_legacy|void_1:Разлом:Пустота заговорила.|void_2:Ветер:Воздух стал умным.|void_3:Свидетель:После вспышки осталась память.`\n"
        "3. Отправь полный текст\n"
        "4. Reply: `/lore_edit text void_legacy`"
    )
    await message.answer(text, parse_mode="Markdown")
