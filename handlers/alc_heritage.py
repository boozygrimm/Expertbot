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
LEGACY_INSTALL_ENERGY = 50
DEFAULT_REWARD_ITEM_AMOUNT = 1
lore_editor_sessions: dict[int, dict] = {}

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
        "id": "primate_legacy",
        "name": "Хроники приматов и первичного бульона",
        "emoji": "🐒",
        "bonus_type": "pack_s",
        "bonus_value": 1,
        "reward_type": "item",
        "reward_payload": "pack_l",
        "reward_amount": 1,
        "reward_name": "",
        "sort_order": 0,
        "fragments": [
            {"id": "primate_1", "title": "Основы хайпа", "preview": "Зигмунд начал шоу-бизнес с Дракона, потому что скромность не даёт сборов."},
            {"id": "primate_2", "title": "Наращивание аудитории", "preview": "Одна ошибка с Жизнью, и вместо мерча появился Человек с Wi‑Fi и запросами."},
            {"id": "primate_3", "title": "Великий Коллапс", "preview": "Спирт, Электричество и паника превратили реальность в огурцы, селфи-палки и налоги."},
        ],
        "full_text": (
            "Знакомьтесь, это Зигмунд. Зигмунд - шимпанзе с амбициями Наполеона и дипломом курсов "
            "\"Алхимия для чайников и макак\". Его текущая цель? Создать Идеальный Шоу-бизнес, потому что "
            "кидаться фекалиями в сородичей стало мейнстримом, а душа требовала страз и мирового турне.\n\n"
            "Зигмунд открыл интерфейс мироздания. Перед ним плавали базовые иконки. Никакой логики, только "
            "чистый хаос, как в голове у продюсера утреннего шоу.\n\n"
            "Первым делом Зигмунд схватил Зверя и Огонь. По логике Алхимика должен был получиться Жареный зверь "
            "или, на худой конец, Шашлык, но мироздание икнуло и выдало Дракона. \"Слишком пафосно для начала "
            "карьеры, - подумал Зигмунд, - но для спецэффектов сойдет.\"\n\n"
            "Далее нужно было добавить немного разумности. Зигмунд взял Дракона и попытался скрестить его с Камнем, "
            "надеясь получить Статую. Но в этот момент мимо пролетала муха цеце, Зигмунд дернулся, и вместо Камня "
            "в котел полетела Жизнь. Экран замигал розовым. Вместо послушного стадионного монстра материализовался "
            "Человек. Существо тут же потребовало кофе на безлактозном молоке и Wi-Fi.\n\n"
            "Зигмунд запаниковал. Чтобы усмирить это создание и превратить его в настоящую поп-звезду, он схватил "
            "иконку Спирт и попытался впихнуть её в Человека, добавив сверху Электричество. Он ждал появления рок-звезды. "
            "Но законы физики Doodle Alchemy не прощают суеты. Спирт вступил в реакцию с драконьим пламенем внутри Человека, "
            "а Электричество закоротило саму ткань реальности.\n\n"
            "Мир начал пикселизироваться. Деревья превратились в гигантские огурцы, небо приобрело цвет взбесившегося "
            "лосося, а сам Зигмунд вдруг осознал, что он - всего лишь набор векторов в чьем-то смартфоне. Пространство "
            "свернулось в трубочку, Человек начал бесконечно делиться, заполняя вселенную селфи-палками и налоговыми "
            "декларациями. Вселенная затрещала, как дешевый пластик.\n\n"
            "Пшик, пшик, пшик... А это так должно было?!! БАМММ!"
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


async def ensure_default_lore():
    await db.alchemy_lore.delete_one({"id": "forge_legacy"})
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


def build_lore_editor_home_text(lore_docs: list[dict]) -> str:
    lines = [
        "🛠 **РЕДАКТОР НАСЛЕДИЯ**",
        "",
        "Как работать:",
        "1. Выбери коллекцию кнопкой ниже или создай новую.",
        "2. Дальше бот сам подскажет, что отправить следующим сообщением.",
        "3. Для полного текста просто отправь рассказ после выбора шага 'Текст'.",
        "",
        "Коллекции:",
    ]
    for lore_doc in lore_docs:
        lines.append(f"• {lore_doc['emoji']} {lore_doc['name']} (`{lore_doc['id']}`)")
    return "\n".join(lines)


def build_lore_editor_home_keyboard(lore_docs: list[dict]) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for lore_doc in lore_docs:
        builder.button(text=f"{lore_doc['emoji']} {lore_doc['name']}", callback_data=f"loreed:open:{lore_doc['id']}")
    builder.button(text="➕ Новое наследие", callback_data="loreed:new")
    builder.button(text="🌱 Перезалить сиды", callback_data="loreed:seed")
    builder.button(text="📚 Краткая справка", callback_data="loreed:help")
    builder.adjust(1)
    return builder.as_markup()


def build_lore_editor_collection_text(lore_doc: dict) -> str:
    fragment_titles = ", ".join(fragment["title"] for fragment in lore_doc.get("fragments", [])) or "пока нет"
    return (
        "🧾 **КАРТОЧКА НАСЛЕДИЯ**\n"
        f"ID: `{lore_doc['id']}`\n"
        f"Имя: {lore_doc['emoji']} {lore_doc['name']}\n"
        f"Бонус: {lore_doc['bonus_type']} +{lore_doc['bonus_value']}\n"
        f"Разовый дар: {lore_doc['reward_type']} / {lore_doc['reward_payload']} x{lore_doc['reward_amount']}\n"
        f"Имя дара: {lore_doc.get('reward_name') or '-'}\n"
        f"Порядок: {lore_doc.get('sort_order', 100)}\n"
        f"Обрывки: {fragment_titles}\n"
        f"Текст: {len((lore_doc.get('full_text') or '').strip())} символов"
    )


def build_lore_editor_collection_keyboard(lore_doc: dict) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🪪 Основа", callback_data=f"loreed:editbasic:{lore_doc['id']}")
    builder.button(text="✨ Бонус", callback_data=f"loreed:editbonus:{lore_doc['id']}")
    builder.button(text="🎁 Награда", callback_data=f"loreed:editreward:{lore_doc['id']}")
    builder.button(text="🧩 Обрывки", callback_data=f"loreed:editfrags:{lore_doc['id']}")
    builder.button(text="📖 Текст", callback_data=f"loreed:edittext:{lore_doc['id']}")
    builder.button(text="👁 Предпросмотр", callback_data=f"loreed:preview:{lore_doc['id']}")
    builder.button(text="🔙 К списку", callback_data="loreed:home")
    builder.adjust(2, 2, 1, 1)
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


async def show_lore_editor_home(target_message: types.Message):
    await ensure_default_lore()
    lore_docs = await get_all_lore_docs()
    await target_message.answer(
        build_lore_editor_home_text(lore_docs),
        reply_markup=build_lore_editor_home_keyboard(lore_docs),
        parse_mode="Markdown",
    )


async def show_lore_editor_collection(target_message: types.Message, lore_id: str):
    lore_doc = await get_lore_doc(lore_id)
    if not lore_doc:
        return await target_message.answer("Коллекция не найдена.")
    await target_message.answer(
        build_lore_editor_collection_text(lore_doc),
        reply_markup=build_lore_editor_collection_keyboard(lore_doc),
        parse_mode="Markdown",
    )


def has_active_lore_editor_session(message: types.Message) -> bool:
    user = message.from_user
    return bool(user and user.id == ADMIN_ID and lore_editor_sessions.get(user.id))


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


@router.callback_query(F.data == "loreed:home")
async def loreed_home(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    await show_lore_editor_home(callback.message)
    await callback.answer()


@router.callback_query(F.data == "loreed:help")
async def loreed_help(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.message.answer(
        "📚 **КРАТКО ПО ЛОРУ**\n\n"
        "1. Жми коллекцию или `Новое наследие`\n"
        "2. Выбирай шаг: Основа, Бонус, Награда, Обрывки, Текст\n"
        "3. Бот сам пишет шаблон\n"
        "4. Просто отправь следующее сообщение по шаблону\n\n"
        "Самое важное:\n"
        "• Обрывки - это 3 строки `id | Название | Анонс`\n"
        "• Текст - просто отправляется обычным сообщением\n"
        "• `seed` перезаливает стандартные сиды",
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data == "loreed:new")
async def loreed_new(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    lore_editor_sessions[callback.from_user.id] = {"mode": "create_basic"}
    await callback.message.answer(
        "➕ **НОВОЕ НАСЛЕДИЕ**\n\n"
        "Пришли следующим сообщением:\n"
        "`id: void_legacy`\n"
        "`name: Наследие Пустоты`\n"
        "`emoji: 🌌`\n"
        "`sort: 40`\n\n"
        "После этого откроется карточка, и ты добьешь бонус, награду, обрывки и текст кнопками.",
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data == "loreed:seed")
async def loreed_seed(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    await ensure_default_lore()
    await callback.message.answer("🌱 Стартовые наследия обновлены.")
    await callback.answer()


@router.callback_query(F.data.startswith("loreed:open:"))
async def loreed_open(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    await show_lore_editor_collection(callback.message, callback.data.split(":")[2])
    await callback.answer()


@router.callback_query(F.data.startswith("loreed:editbasic:"))
async def loreed_editbasic(callback: types.CallbackQuery):
    lore_id = callback.data.split(":")[2]
    lore_editor_sessions[callback.from_user.id] = {"mode": "edit_basic", "id": lore_id}
    await callback.message.answer(
        "🪪 **ОСНОВА НАСЛЕДИЯ**\n\n"
        "Пришли следующим сообщением:\n"
        f"`id: {lore_id}`\n"
        "`name: Название коллекции`\n"
        "`emoji: 📜`\n"
        "`sort: 40`",
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("loreed:editbonus:"))
async def loreed_editbonus(callback: types.CallbackQuery):
    lore_id = callback.data.split(":")[2]
    lore_editor_sessions[callback.from_user.id] = {"mode": "edit_bonus", "id": lore_id}
    await callback.message.answer(
        "✨ **БОНУС НАСЛЕДИЯ**\n\n"
        "Пришли следующим сообщением:\n"
        "`bonus: spirit_f`\n"
        "`bonus_value: 3`\n\n"
        "Примеры bonus:\n"
        "`spirit_f`, `spirit_w`, `spirit_e`, `spirit_a`, `metal`, `energy`, `pack_s`",
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("loreed:editreward:"))
async def loreed_editreward(callback: types.CallbackQuery):
    lore_id = callback.data.split(":")[2]
    lore_editor_sessions[callback.from_user.id] = {"mode": "edit_reward", "id": lore_id}
    await callback.message.answer(
        "🎁 **НАГРАДА НАСЛЕДИЯ**\n\n"
        "Пришли следующим сообщением:\n"
        "`reward_type: item`\n"
        "`reward_payload: pack_l`\n"
        "`reward_amount: 1`\n"
        "`reward_name:`\n\n"
        "Типы: `item`, `recipe`, `fighter`",
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("loreed:editfrags:"))
async def loreed_editfrags(callback: types.CallbackQuery):
    lore_id = callback.data.split(":")[2]
    lore_editor_sessions[callback.from_user.id] = {"mode": "edit_fragments", "id": lore_id}
    await callback.message.answer(
        "🧩 **ОБРЫВКИ**\n\n"
        "Пришли 3 строки в формате:\n"
        "`frag_1 | Название | Короткий анонс`\n"
        "`frag_2 | Название | Короткий анонс`\n"
        "`frag_3 | Название | Короткий анонс`",
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("loreed:edittext:"))
async def loreed_edittext(callback: types.CallbackQuery):
    lore_id = callback.data.split(":")[2]
    lore_editor_sessions[callback.from_user.id] = {"mode": "edit_text", "id": lore_id}
    await callback.message.answer(
        "📖 **ПОЛНЫЙ ТЕКСТ**\n\n"
        "Теперь просто пришли следующим сообщением весь рассказ целиком.\n"
        "Ничего дописывать не надо.",
        parse_mode="Markdown",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("loreed:preview:"))
async def loreed_preview(callback: types.CallbackQuery):
    lore_id = callback.data.split(":")[2]
    lore_doc = await get_lore_doc(lore_id)
    if not lore_doc:
        return await callback.answer("Коллекция не найдена.", show_alert=True)
    await callback.message.answer(build_read_text(lore_doc), parse_mode="Markdown")
    await callback.answer("Предпросмотр отправлен.")


@router.message(Command("lore_edit"))
async def lore_edit(message: types.Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return
    args = (command.args or "").strip().lower()
    if args == "seed":
        await ensure_default_lore()
        return await message.answer("🌱 Стартовые наследия обновлены.")
    await show_lore_editor_home(message)


@router.message(Command("lore_help", "справкалор"))
async def lore_help(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer(
        "📚 **СПРАВКА ПО ЛОРУ**\n\n"
        "Проще всего так:\n"
        "1. `/lore_edit`\n"
        "2. Нажми коллекцию или `Новое наследие`\n"
        "3. Выбери шаг кнопкой\n"
        "4. Отправь следующее сообщение по шаблону от бота\n\n"
        "Что руками помнить не нужно:\n"
        "• для текста просто отправляешь рассказ\n"
        "• для обрывков нужны 3 строки `id | Название | Анонс`\n"
        "• бонус и награду бот подсказывает отдельным шагом",
        parse_mode="Markdown",
    )


@router.message(F.text, has_active_lore_editor_session)
async def lore_editor_input(message: types.Message):
    session = lore_editor_sessions[message.from_user.id]

    mode = session.get("mode")
    lore_id = session.get("id")

    try:
        if mode == "create_basic":
            data = parse_key_value_lines(message.text or "")
            lore_id = data.get("id", "").strip()
            if not lore_id:
                return await message.answer("Нужен `id:`. Пример: `id: void_legacy`", parse_mode="Markdown")
            doc = normalize_lore_doc(
                {
                    "id": lore_id,
                    "name": data.get("name", lore_id),
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
            lore_editor_sessions.pop(message.from_user.id, None)
            await message.answer(f"Создано наследие `{lore_id}`. Теперь добей его по кнопкам ниже.", parse_mode="Markdown")
            return await show_lore_editor_collection(message, lore_id)

        lore_doc = await get_lore_doc(lore_id)
        if not lore_doc:
            lore_editor_sessions.pop(message.from_user.id, None)
            return await message.answer("Коллекция потерялась. Открой `/lore_edit` заново.", parse_mode="Markdown")

        if mode == "edit_basic":
            data = parse_key_value_lines(message.text or "")
            updates = {
                "id": data.get("id", lore_doc["id"]),
                "name": data.get("name", lore_doc["name"]),
                "emoji": data.get("emoji", lore_doc["emoji"]),
                "sort_order": int(data.get("sort", str(lore_doc.get("sort_order", 100))) or lore_doc.get("sort_order", 100)),
            }
            await db.alchemy_lore.update_one({"id": lore_id}, {"$set": updates})
            if updates["id"] != lore_id:
                await db.alchemy_lore.delete_one({"id": lore_id})
                lore_id = updates["id"]

        elif mode == "edit_bonus":
            data = parse_key_value_lines(message.text or "")
            await db.alchemy_lore.update_one(
                {"id": lore_id},
                {
                    "$set": {
                        "bonus_type": data.get("bonus", lore_doc["bonus_type"]),
                        "bonus_value": max(1, int(data.get("bonus_value", str(lore_doc["bonus_value"])) or lore_doc["bonus_value"])),
                    }
                },
            )

        elif mode == "edit_reward":
            data = parse_key_value_lines(message.text or "")
            await db.alchemy_lore.update_one(
                {"id": lore_id},
                {
                    "$set": {
                        "reward_type": data.get("reward_type", lore_doc["reward_type"]),
                        "reward_payload": data.get("reward_payload", lore_doc.get("reward_payload", "")),
                        "reward_amount": max(1, int(data.get("reward_amount", str(lore_doc.get("reward_amount", 1))) or lore_doc.get("reward_amount", 1))),
                        "reward_name": data.get("reward_name", lore_doc.get("reward_name", "")),
                    }
                },
            )

        elif mode == "edit_fragments":
            fragments = parse_fragments_text(message.text or "")
            await db.alchemy_lore.update_one({"id": lore_id}, {"$set": {"fragments": fragments}})

        elif mode == "edit_text":
            await db.alchemy_lore.update_one({"id": lore_id}, {"$set": {"full_text": message.text or ""}})

        else:
            return

        lore_editor_sessions.pop(message.from_user.id, None)
        await message.answer("✅ Сохранено.")
        await show_lore_editor_collection(message, lore_id)

    except ValueError as exc:
        await message.answer(f"❌ {exc}")
