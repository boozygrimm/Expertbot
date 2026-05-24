import random
import time
import uuid
import logging

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ai_logic import generate_with_retry
from bot_instance import bot
from database import db
from handlers.alc_core import (
    DEFAULT_ACTION_COUNTERS,
    STORAGE_CAPACITY,
    get_or_create_player,
    get_used_slots,
    increment_action_counter,
)
from handlers.alc_fighters import (
    build_fighter_snapshot,
    consume_fighter_stamina,
    find_fighter,
    get_available_fighters,
    get_fighter_stamina_cap,
    get_fighter_title,
    get_player_fighters,
    grant_mission_progress,
)
from handlers.alc_heritage import try_grant_random_lore_fragment
from handlers.alc_recipes import BATTLE_UNITS, ELEMENTS

router = Router()
logger = logging.getLogger(__name__)

MAX_LOGS = 7
VISIBLE_LOGS = 5
EXPEDITION_TICK = 300
# Длительность экспедиции по уровню Гильдии: 2/3/4 часа
GUILD_EXPEDITION_DURATION = {0: 7200, 1: 7200, 2: 10800, 3: 14400}
RETURN_MIN_TIME = 300
# Опыт за экспедицию: 1 XP за 2 экспедиции (0.5)
EXPEDITION_XP_CHANCE = 0.5
# Время на интерактивный выбор (в секундах)
INTERACTIVE_CHOICE_TIMEOUT = 900  # 15 минут

# --- Каталог интерактивных локаций ---
INTERACTIVE_LOCATIONS = [
    {
        "id": "shattered_alchemy_altar",
        "title": "Разбитый алхимический алтарь",
        "safe_text": "Отряд увидел разбитый алтарь и вытащил оставшиеся регенты. Дальше спокойнее.",
        "risk_text": "Внутри бродит нестабильный филос-камень. Рискнуть?",
        "safe_loot": {"f": (2, 4), "e": (2, 4), "w": (2, 4), "a": (2, 4)},
        "risk_loot": {"energy": (2, 4), "glass": (3, 6), "recipe_scroll": 1, "philosophers_stone": (0, 1)},
        "difficulty": 15,
        "damage_range": (10, 25),
    },
    {
        "id": "collapsed_tunnel",
        "title": "Заваленный тоннель",
        "safe_text": "Нашли проход, но он завален. Снаружи немного серы и песка — пригодится.",
        "risk_text": "Есть узкий лаз в завал. Может быть руды, может быть обвал.",
        "safe_loot": {"sulphur": (2, 4), "sand": (2, 4), "stone": (2, 4)},
        "risk_loot": {"metal": (2, 5), "coal": (3, 5), "diamond": (0, 1)},
        "difficulty": 10,
        "damage_range": (8, 20),
    },
    {
        "id": "fuming_crucible",
        "title": "Парящий тигель",
        "safe_text": "Паровой тигель ещё дымит. Кое-кто унёс вытянутый огонь.",
        "risk_text": "Пар внутри подозрительно горяч. Может быть что-то ещё кипит.",
        "safe_loot": {"steam": (2, 3), "f": (2, 4)},
        "risk_loot": {"energy": (2, 4), "glass": (3, 6), "recipe_scroll": 1},
        "difficulty": 20,
        "damage_range": (12, 30),
    },
    {
        "id": "forgotten_reagent_vault",
        "title": "Забытый склад реагентов",
        "safe_text": "Умышленно оставленные реагенты, так и снаружи растут грибы.",
        "risk_text": "Склад герметичен. За дверью могут быть настоящие сокровища и мутанты.",
        "safe_loot": {"moss": (3, 6), "swamp": (1, 2), "life": (1, 2)},
        "risk_loot": {"poison": (2, 4), "acid": (2, 3), "recipe_scroll": (0, 1), "medicine": (0, 1)},
        "difficulty": 12,
        "damage_range": (8, 18),
    },
    {
        "id": "broken_distillation_tower",
        "title": "Сломанная колонна дистилляции",
        "safe_text": "Около колонны уцелело стекло и немного чистой воды.",
        "risk_text": "Внутри колонны что-то тяжелое и агрессивное. Или былагочное.",
        "safe_loot": {"glass": (2, 4), "w": (2, 4)},
        "risk_loot": {"energy": (2, 4), "alcohol": (2, 3), "recipe_scroll": 1, "gold": (0, 1)},
        "difficulty": 14,
        "damage_range": (10, 22),
    },
    {
        "id": "overgrown_fermenter",
        "title": "Заросший бродильный аппарат",
        "safe_text": "На корпусе растут ягоды и мох. Можно собрать.",
        "risk_text": "Кто-то изнутри стучит по толстым стенкам. Или что-то.",
        "safe_loot": {"moss": (3, 6), "berry": (2, 4), "wood": (3, 5)},
        "risk_loot": {"yeast": (2, 4), "alcohol": (2, 3), "recipe_scroll": (0, 1), "life": (1, 2)},
        "difficulty": 10,
        "damage_range": (5, 15),
    },
    {
        "id": "cracked_reactor",
        "title": "Треснувший распределитель",
        "safe_text": "Здесь висит пробка и немного искр. Забрали магниты.",
        "risk_text": "Распределитель трещит и гудит. Внутри — оголённые провода и что-то ещё активное.",
        "safe_loot": {"metal": (2, 4), "electricity": (1, 2)},
        "risk_loot": {"energy": (3, 6), "electricity": (2, 3), "recipe_scroll": (0, 1), "gem": (0, 1)},
        "difficulty": 22,
        "damage_range": (15, 35),
    },
]

# --- Роль подсказки для интерактивных событий ---
INTERACTIVE_ROLE_TIPS = {
    "medic": "Медик может стабилизировать, если кто-то пострадает.",
    "engineer": "Инженер видит ловушки, которые другие пропускают.",
    "saboteur": "Сапёр узнаёт, где замuroвывание пройдёт безопаснее.",
    "sniper": "Снайпер заранее обнаружит угрозу.",
    "wind_scout": "Ветрослед может проскользнуть, пока никто не заметил.",
    "storm_archer": "Бурелук чувствует перемену давления — угрозы перед вами.",
    "water_guard": "Водостраж уверенно щитит спину в тесных пространствах.",
    "earth_shielder": "Щитоносец крепко будет стоять перед лицом опасности.",
    "mech": "Мех чувствует механизированную схватку.",
    "flame_tank": "Огнетанк заряжает систему ближе к движущейся угрозе.",
    "juggernaut": "Джаггер не сдвинется, пока сам не позволит.",
    "med": "Боец с поддержкой способен зализать раны в бою.",  # wildcard: has support stat high?
}

def _roll_loot_amount(loot_spec) -> dict:
    """{'item': (min, max)} -> {'item': rolled_amount}"""
    result = {}
    for item_id, value in loot_spec.items():
        if isinstance(value, (tuple, list)) and len(value) == 2:
            result[item_id] = random.randint(value[0], value[1])
        else:
            result[item_id] = value
    return result
ROLE_ALIASES = {
    "fire_swordsman": "Огнемеч",
    "water_guard": "Водостраж",
    "earth_shielder": "Щитоносец",
    "wind_scout": "Ветрослед",
    "ash_pikeman": "Пеплокопь",
    "wave_spearman": "Волникопь",
    "stone_axeman": "Камнетёс",
    "storm_archer": "Бурелук",
    "rider_f": "Пламерейд",
    "rider_w": "Приливрейд",
    "rider_e": "Скаларейд",
    "rider_a": "Вихререйд",
    "saboteur": "Сапёр",
    "medic": "Медик",
    "engineer": "Инженер",
    "sniper": "Снайпер",
    "mech": "Мех",
    "flame_tank": "Огнетанк",
    "tesla_tank": "Теслатанк",
    "hover": "Ховер",
    "drone": "Дрон",
    "artillery": "Арта",
    "walker": "Ходун",
    "juggernaut": "Джаггер",
}
TIER_HP = {1: 45, 2: 72, 3: 110}
TIER_CARGO = {1: 10, 2: 15, 3: 22}
FALLBACK_ACTIONS = [
    "убегал от",
    "споткнулся о",
    "героически победил",
    "случайно поджёг",
    "переубедил силой",
    "обманул",
    "обменял банку мази на",
    "вытащил из болота",
    "запер в бочке",
    "напугал до линьки",
    "усыпил песней про реторту",
    "переварил в котле",
    "потроллил",
    "отрезвил кирпичом",
    "обогнал на ходулях",
    "накрыл котелком",
    "убедил подписать капитуляцию",
    "выкурил из руин",
    "объявил санитарной проблемой",
    "перекрестил в трофей",
]
FALLBACK_ENEMIES = [
    "кислотного слизня",
    "живой философский камень",
    "дикого гомункула",
    "пьяную мантикору",
    "грибного голема",
    "ядовитый гербарий",
    "подвального алхимика",
    "обиженный сундук-мимик",
    "стеклянного василиска",
    "карманного некроманта",
    "плотоядную тыкву",
    "раздражённый перегонный куб",
    "болотного аптекаря",
    "лишайникового шамана",
    "прыгающий котёл",
    "саблезубый одуванчик",
    "бродячий перегрузочный кристалл",
    "трёхдневный суп с характером",
    "полкового жабоманта",
    "архивного упыря на ставке",
]
LOOT_TABLE = [
    # Базовые элементы (частые)
    ("w", 8), ("f", 8), ("e", 8), ("a", 8),
    # Материалы (средние)
    ("stone", 6), ("wood", 6), ("sand", 5), ("moss", 4),
    ("glass", 4), ("brick", 4), ("metal", 4),
    # Редкие элементы (редкие)
    ("energy", 5), ("life", 3), ("ghost", 3), ("electricity", 3),
    # Ценные предметы (очень редкие)
    ("recipe_scroll", 2), ("pack_s", 2), ("pack_m", 1), ("pack_l", 1),
    ("philosophers_stone", 1),
    # Редкие награды (очень редкие)
    ("diamond", 1), ("gold", 1), ("gem", 1),
]
MEETING_SPECIALS = ["alchemist", "wanderer", "hero", "scientist"]
MEETING_PACKS = ["pack_s", "pack_s", "pack_m", "pack_m", "pack_l"]
expedition_drafts: dict[int, list[str]] = {}
expedition_pages: dict[int, int] = {}
EXPEDITION_ROWS_PER_PAGE = 10


def get_role_name(fighter_or_unit) -> str:
    if isinstance(fighter_or_unit, dict):
        custom_name = fighter_or_unit.get("custom_name") or fighter_or_unit.get("name")
        if custom_name:
            return custom_name
        unit_id = fighter_or_unit.get("unit_id", "")
        return ROLE_ALIASES.get(unit_id, ELEMENTS.get(unit_id, unit_id))
    unit_id = str(fighter_or_unit)
    return ROLE_ALIASES.get(unit_id, ELEMENTS.get(unit_id, unit_id))


def get_expedition_slot_limit(barracks_level: int) -> int:
    if barracks_level >= 5:
        return 3
    if barracks_level >= 3:
        return 2
    return 1


def get_expedition_team_limit(guild_level: int) -> int:
    if guild_level >= 3:
        return 5
    if guild_level >= 2:
        return 4
    return 3


def get_unit_id(subject) -> str:
    if isinstance(subject, dict):
        return subject.get("unit_id", "")
    return str(subject)


def get_unit_tier(subject) -> int:
    if isinstance(subject, dict):
        return int(subject.get("tier", BATTLE_UNITS.get(subject.get("unit_id", ""), {}).get("tier", 1)) or 1)
    return int(BATTLE_UNITS[str(subject)]["tier"])


def calculate_unit_hp(subject, barracks_level: int) -> int:
    tier = get_unit_tier(subject)
    return TIER_HP[tier] + barracks_level * 6


def calculate_unit_cargo(subject) -> int:
    return TIER_CARGO[get_unit_tier(subject)]


def build_team_stats(units: list, barracks_level: int) -> dict:
    unit_ids = [get_unit_id(unit) for unit in units]
    total_hp = sum(calculate_unit_hp(unit, barracks_level) for unit in units)
    total_cargo = sum(calculate_unit_cargo(unit) for unit in units)
    has_medic = "medic" in unit_ids
    defense_bonus = sum(1 for unit_id in unit_ids if unit_id in {"earth_shielder", "water_guard", "mech", "juggernaut"})
    loot_bonus = sum(1 for unit_id in unit_ids if unit_id in {"wind_scout", "storm_archer", "saboteur", "engineer", "sniper", "drone", "hover"})
    tier_pressure = sum(get_unit_tier(unit) for unit in units)

    # Суммируем статы бойцов
    total_speed = 0
    total_perception = 0
    total_endurance = 0
    total_attack = 0
    total_defense = 0
    total_support = 0

    for unit in units:
        if isinstance(unit, dict) and "stats" in unit:
            stats = unit["stats"]
            total_speed += stats.get("speed", 0)
            total_perception += stats.get("perception", 0)
            total_endurance += stats.get("endurance", 0)
            total_attack += stats.get("attack", 0)
            total_defense += stats.get("defense", 0)
            total_support += stats.get("support", 0)

    # Бонус к грузу от стойкости
    cargo_bonus = total_endurance // 3

    return {
        "total_hp": total_hp,
        "cargo_capacity": total_cargo + cargo_bonus,
        "has_medic": has_medic,
        "defense_bonus": defense_bonus,
        "loot_bonus": loot_bonus,
        "tier_pressure": tier_pressure,
        "total_speed": total_speed,
        "total_perception": total_perception,
        "total_endurance": total_endurance,
        "total_attack": total_attack,
        "total_defense": total_defense,
        "total_support": total_support,
    }


def trim_logs(logs: list[str]) -> list[str]:
    return logs[-MAX_LOGS:]


def choose_weighted_loot() -> tuple[str, int]:
    entries = [(item_id, weight) for item_id, weight in LOOT_TABLE]
    total = sum(weight for _, weight in entries)
    roll = random.randint(1, total)
    passed = 0
    for item_id, weight in entries:
        passed += weight
        if roll <= passed:
            # Рандомное количество для всех предметов
            if item_id in {"diamond", "gold", "gem"}:
                amount = random.randint(1, 2)
            elif item_id == "philosophers_stone":
                amount = random.randint(0, 1)
            elif item_id in {"energy", "life", "ghost", "electricity"}:
                amount = random.randint(1, 3)
            elif item_id in {"recipe_scroll", "pack_s", "pack_m", "pack_l"}:
                amount = 1
            elif item_id in {"stone", "wood", "sand", "moss", "glass", "brick", "metal"}:
                amount = random.randint(2, 5)
            else:  # Базовые элементы
                amount = random.randint(3, 6)
            return item_id, amount
    return "stone", random.randint(2, 5)


def build_fallback_text(units: list) -> str:
    actor = get_role_name(random.choice(units))
    action = random.choice(FALLBACK_ACTIONS)
    enemy = random.choice(FALLBACK_ENEMIES)
    tails = [
        "а потом уверял, что это был план.",
        "и потребовал записать это как тактический успех.",
        "после чего отряд единогласно запретил ему импровизировать.",
        "и сразу начал делить трофеи, которых ещё не было.",
        "а штаб потом назвал это аккуратной работой на местности.",
    ]
    return f"{actor} {action} {enemy}, {random.choice(tails)}"


def build_planned_event_text(units: list, event_type: str) -> str:
    actor = get_role_name(random.choice(units))
    wingman = get_role_name(random.choice(units))
    enemy = random.choice(FALLBACK_ENEMIES)

    if event_type == "loot":
        options = [
            f"{actor} вытряхнул добычу из {enemy}, а {wingman} немедленно назвал это плановой находкой.",
            f"{actor} вскрыл тайник рядом с {enemy}, пока {wingman} делал вид, что всё идёт по уставу.",
            f"{actor} добыл трофеи после встречи с {enemy}, а {wingman} уже считал премию.",
        ]
        return random.choice(options)
    if event_type == "damage":
        options = [
            f"{actor} полез вперёд и нарвался на {enemy}, пока {wingman} вытаскивал отряд из месива.",
            f"{actor} проверил тропу на прочность собственным здоровьем, а {wingman} отбивал {enemy} от строя.",
            f"{actor} недооценил {enemy}, и {wingman} потом долго собирал дисциплину по кустам.",
        ]
        return random.choice(options)
    if event_type == "heal":
        options = [
            f"{actor} перевёл дух, а {wingman} быстро залатал строй после стычки с {enemy}.",
            f"{wingman} подлатал отряд на ходу, пока {actor} доказывал {enemy}, что добивать уже поздно.",
            f"{actor} кое-как выдохнул, а {wingman} собрал строй обратно после знакомства с {enemy}.",
        ]
        return random.choice(options)
    options = [
        f"{actor} договорился с гостем у костра, пока {wingman} следил, чтобы сделка не закончилась поножовщиной.",
        f"{actor} сторговался у костра на странных условиях, а {wingman} заранее держал руку на оружии.",
        f"{actor} вёл переговоры с полевым проходимцем, пока {wingman} оценивал, кого придётся бить первым.",
    ]
    return random.choice(options)


async def generate_event_line(units: list, hp_now: int, hp_total: int) -> str:
    roles = ", ".join(get_role_name(unit_id) for unit_id in units)
    prompt = (
        "Ты пишешь короткую ленту событий для экспедиции в ироничной фэнтези-алхимической игре. "
        "Мир: големы, мутировавшие растения, зелья, разбойники, старые руины и бардак на выезде. "
        f"Отряд: {roles}. Текущее HP: {hp_now}/{hp_total}. "
        "Напиши ровно одно короткое предложение до 150 символов. "
        "Тон: сухая ирония, лёгкий чёрный юмор, походная неразбериха, но без абсурда ради абсурда. "
        "Используй роли бойцов прямо в тексте. "
        "Предложение должно описывать конкретный эпизод: находку, драку, рану, лечение или мутную встречу."
    )
    text = await generate_with_retry(prompt, is_game=True, use_fast_model=True)
    if not text:
        return build_fallback_text(units)
    return text.strip().replace("\n", " ")[:150]


def format_loot_summary(loot: dict) -> str:
    if not loot:
        return "ничего"
    ordered = sorted(loot.items(), key=lambda item: (item[0] not in {"energy", "recipe_scroll", "pack_l", "pack_m", "pack_s"}, ELEMENTS.get(item[0], item[0])))
    return ", ".join(f"{ELEMENTS.get(item_id, item_id)} x{amount}" for item_id, amount in ordered)


async def generate_expedition_final_report(expedition: dict) -> str:
    fighters = ", ".join(get_role_name(item) for item in expedition.get("fighter_snapshots", expedition.get("fighters", [])))
    loot_text = format_loot_summary(expedition.get("loot", {}))
    reason = expedition.get("return_reason", "маршрут завершён")
    hp_text = f"{expedition.get('current_hp', 0)}/{expedition.get('total_hp', 0)}"
    prompt = (
        "Ты пишешь итоговый полевой отчет для ироничной фэнтези-алхимической игры. "
        "Тон: живо, смешно, по делу, с сухой иронией штаба после тяжелого маршрута. "
        f"Отряд: {fighters}. Итоговое HP: {hp_text}. "
        f"Причина завершения похода: {reason}. "
        f"Добыча: {loot_text}. "
        "Напиши короткий итоговый отчет ровно в 2 предложения. "
        "Нужно ясно упомянуть исход похода, состояние отряда и добычу. "
        "Без пафоса, без канцелярита, без бреда, но с характером."
    )
    text = await generate_with_retry(prompt, is_game=True, use_fast_model=True)
    if text:
        return text.strip().replace("\n", " ")[:350]

    if expedition.get("loot"):
        options = [
            (
                f"Отряд вернулся с маршрута '{reason}' и выгрузил {loot_text}. "
                f"Строй держится на {hp_text}, так что штаб ворчит, но добычу уже считает."
            ),
            (
                f"Маршрут '{reason}' закончился возвратом и вполне осязаемой добычей: {loot_text}. "
                f"По лицам видно {hp_text}, зато по ведомости вылазка вышла убедительной."
            ),
            (
                f"После маршрута '{reason}' отряд дотащил обратно {loot_text}. "
                f"Живучесть на уровне {hp_text}, мораль спорная, бухгалтерия довольна."
            ),
        ]
        return random.choice(options)
    options = [
        (
            f"Отряд вернулся после маршрута '{reason}', но мешки звучно разочаровали склад. "
            f"По состоянию {hp_text} видно, что прогулка была содержательной, просто не прибыльной."
        ),
        (
            f"Маршрут '{reason}' закончился без серьёзной добычи, зато с полным набором усталых взглядов. "
            f"Строй дотянул до {hp_text}, а штаб снова назвал это ценным полевым опытом."
        ),
        (
            f"С маршрута '{reason}' бойцы вернулись почти налегке и заметно злее обычного. "
            f"Итог по прочности {hp_text}; формально живы, значит экспедиция уже не провал."
        ),
    ]
    return random.choice(options)


def insert_interactive_events(planned: list, stats: dict, units: list) -> list:
    """Добавляет интерактивные события в план экспедиции.
    Количество зависит от скорости и восприятия (1–2 события)."""
    speed = stats.get("total_speed", 0)
    perception = stats.get("total_perception", 0)
    # Базовый шанс появления + бонус от скорости и восприятия
    chance = 0.10 + min((speed + perception) / 100, 0.30)
    if random.random() > chance:
        return planned

    count = random.randint(1, 2)  # From speed/perception
    for _ in range(count):
        location = random.choice(INTERACTIVE_LOCATIONS)
        event = {
            "type": "interactive",
            "location_id": location["id"],
            "text": location["risk_text"],  # Краткий текст-затычка о приближении к опасному месту
            "safe_text": location["safe_text"],
            "safe_loot": location["safe_loot"],
            "risk_loot": location["risk_loot"],
            "difficulty": location["difficulty"],
            "damage_range": location["damage_range"],
        }
        planned.append(event)
    return planned


def build_planned_events_sync(units: list, stats: dict) -> list[dict]:
    """Синхронная версия для мгновенного создания экспедиции (без AI)."""
    hp_now = stats["total_hp"]
    planned = []

    # Количество событий зависит от скорости (больше скорость = дальше проходёт)
    base_events = random.randint(9, 13)
    speed_bonus = stats.get("total_speed", 0) // 5
    planned_count = base_events + speed_bonus

    # Шанс редких ресурсов зависит от восприятия
    perception_bonus = stats.get("total_perception", 0) // 10

    for _ in range(planned_count):
        roll = random.random()
        if roll < 0.58:
            loot_item, base_amount = choose_weighted_loot()
            # Бонус от loot_bonus к количеству
            loot_amount = base_amount + min(2, stats["loot_bonus"] // 2)
            if loot_amount <= 0:
                continue

            event = {"type": "loot", "loot": {loot_item: loot_amount}}
            if random.random() < min(0.35, 0.08 * stats["loot_bonus"]):
                bonus_item, bonus_base_amount = choose_weighted_loot()
                if bonus_item != loot_item:
                    bonus_amount = bonus_base_amount + min(1, stats["loot_bonus"] // 3)
                    if bonus_amount > 0:
                        event["loot"][bonus_item] = bonus_amount

            # Бонус от восприятия к редким ресурсам
            if perception_bonus > 0 and random.random() < 0.1 + (perception_bonus * 0.02):
                rare_items = ["recipe_scroll", "pack_l", "philosophers_stone"]
                rare_item = random.choice(rare_items)
                if rare_item not in event.get("loot", {}):
                    event["loot"][rare_item] = 1
        elif roll < 0.78:
            damage = random.randint(6, 16) + random.randint(0, 8)
            # Защита снижает урон
            defense_reduction = stats.get("total_defense", 0) // 3
            damage = max(4, damage - defense_reduction)
            event = {"type": "damage", "damage": damage}
            hp_now = max(0, hp_now - damage)
        elif roll < 0.93:
            heal = random.randint(6, 14) + (8 if stats["has_medic"] else 0)
            # Поддержка увеличивает лечение
            support_bonus = stats.get("total_support", 0) // 4
            heal += support_bonus
            event = {"type": "heal", "heal": heal}
            hp_now = min(stats["total_hp"], hp_now + heal)
        else:
            event = {"type": "meeting"}
            if random.random() < 0.45:
                event["reward_recipe"] = True
            else:
                event["reward_pack"] = random.choice(MEETING_PACKS)
            event["visitor"] = random.choice(MEETING_SPECIALS)
        # Используем только fallback тексты для мгновенного создания
        event["text"] = build_planned_event_text(units, event["type"])
        planned.append(event)
    return insert_interactive_events(planned, stats, units)


async def build_planned_events(units: list, stats: dict) -> list[dict]:
    hp_now = stats["total_hp"]
    planned = []

    # Количество событий зависит от скорости (больше скорость = дальше проходят)
    base_events = random.randint(9, 13)
    speed_bonus = stats.get("total_speed", 0) // 5
    planned_count = base_events + speed_bonus

    # Шанс редких ресурсов зависит от восприятия
    perception_bonus = stats.get("total_perception", 0) // 10

    for _ in range(planned_count):
        roll = random.random()
        if roll < 0.58:
            loot_item, base_amount = choose_weighted_loot()
            # Бонус от loot_bonus к количеству
            loot_amount = base_amount + min(2, stats["loot_bonus"] // 2)
            if loot_amount <= 0:
                continue

            event = {"type": "loot", "loot": {loot_item: loot_amount}}
            if random.random() < min(0.35, 0.08 * stats["loot_bonus"]):
                bonus_item, bonus_base_amount = choose_weighted_loot()
                if bonus_item != loot_item:
                    bonus_amount = bonus_base_amount + min(1, stats["loot_bonus"] // 3)
                    if bonus_amount > 0:
                        event["loot"][bonus_item] = bonus_amount

            # Бонус от восприятия к редким ресурсам
            if perception_bonus > 0 and random.random() < 0.1 + (perception_bonus * 0.02):
                rare_items = ["recipe_scroll", "pack_l", "philosophers_stone"]
                rare_item = random.choice(rare_items)
                if rare_item not in event.get("loot", {}):
                    event["loot"][rare_item] = 1
        elif roll < 0.78:
            damage = random.randint(6, 16) + random.randint(0, 8)
            # Защита снижает урон
            defense_reduction = stats.get("total_defense", 0) // 3
            damage = max(4, damage - defense_reduction)
            event = {"type": "damage", "damage": damage}
            hp_now = max(0, hp_now - damage)
        elif roll < 0.93:
            heal = random.randint(6, 14) + (8 if stats["has_medic"] else 0)
            # Поддержка увеличивает лечение
            support_bonus = stats.get("total_support", 0) // 4
            heal += support_bonus
            event = {"type": "heal", "heal": heal}
            hp_now = min(stats["total_hp"], hp_now + heal)
        else:
            event = {"type": "meeting"}
            if random.random() < 0.45:
                event["reward_recipe"] = True
            else:
                event["reward_pack"] = random.choice(MEETING_PACKS)
            event["visitor"] = random.choice(MEETING_SPECIALS)
        if random.random() < 0.35:
            event["text"] = await generate_event_line(units, hp_now, stats["total_hp"])
        else:
            event["text"] = build_planned_event_text(units, event["type"])
        planned.append(event)
    return insert_interactive_events(planned, stats, units)


def add_loot_to_bag(expedition: dict, new_loot: dict):
    loot = expedition.get("loot", {})
    for item_id, amount in new_loot.items():
        loot[item_id] = loot.get(item_id, 0) + amount
    expedition["loot"] = loot


def get_bag_load(loot: dict) -> int:
    return sum(loot.values())


def resolve_draft_fighters(player: dict, draft: list[str], *, expedition_mode: bool = False) -> list[dict]:
    fighters = []
    garrison = set(player.get("fort_garrison", []))
    all_fighters = {fighter["id"]: fighter for fighter in get_player_fighters(player)}
    for fighter_id in draft:
        fighter = all_fighters.get(fighter_id)
        if not fighter or fighter.get("status") != "available":
            continue
        if expedition_mode and int(fighter.get("stamina", 0)) <= 0:
            continue
        if fighter_id in garrison:
            continue
        fighters.append(fighter)
    return fighters


def cleanup_expedition_draft(player: dict, user_id: int) -> list[str]:
    valid_ids = {fighter["id"] for fighter in get_available_fighters(player, include_zero_stamina=False)}
    cleaned = [fighter_id for fighter_id in expedition_drafts.get(user_id, []) if fighter_id in valid_ids]
    expedition_drafts[user_id] = cleaned
    return cleaned


def render_expeditions_text(player: dict, draft: list[str] | None = None) -> str:
    buildings = player.get("buildings", {})
    barracks_level = buildings.get("barracks", 0)
    guild_level = buildings.get("guild", 0)
    active = player.get("active_expeditions", [])
    slot_limit = get_expedition_slot_limit(barracks_level)
    team_limit = get_expedition_team_limit(guild_level)
    duration_hours = GUILD_EXPEDITION_DURATION.get(guild_level, 7200) // 3600
    lines = [
        f"🧪 **ЭКСПЕДИЦИИ** [{len(active)}/{slot_limit}]",
        f"Лимит бойцов в отряде: {team_limit}.",
        f"Длительность: {duration_hours} ч. (зависит от Гильдии).",
        "💡 Опыт: 1 XP с шансом 50% (безопаснее рейдов).",
        "💡 Статы влияют: скорость (5→+1 событие), восприятие (10→редкие ресурсы), стойкость (3→+1 груз).",
        "💡 Бодрость возвращается по 1 каждые 6 часов.",
        "",
    ]
    if draft is not None:
        if draft:
            draft_fighters = resolve_draft_fighters(player, draft, expedition_mode=True)
            stats = build_team_stats(draft_fighters, barracks_level)
            lines.append("📋 **Формируемый отряд:**")
            for i, fighter in enumerate(draft_fighters, 1):
                lines.append(f"{i}. {get_role_name(fighter)}")
            lines.append("")
            lines.append(f"📊 HP: {stats['total_hp']} | Груз: {stats['cargo_capacity']}")
        else:
            lines.append("📋 **Формируемый отряд:**")
            lines.append("• Отряд пуст. Выбери бойцов ниже.")
        lines.append("")

    if not active:
        lines.append("Активных походов нет.")
    else:
        for i, expedition in enumerate(active, 1):
            load_now = get_bag_load(expedition.get("loot", {}))
            status = expedition.get("status", "searching")
            time_left = ""
            if status == "awaiting_choice":
                left = max(0, expedition.get("interactive_timeout_at", 0) - int(time.time()))
                time_left = f" (⏳{left // 60}м{left % 60}с)"
            lines.append(
                f"• Экспедиция #{i} | {expedition['status_label']} "
                f"{time_left}| "
                f"HP {expedition['current_hp']}/{expedition['total_hp']} | Рюкзак {load_now}/{expedition['cargo_capacity']}"
            )
    return "\n".join(lines)


def build_expeditions_keyboard(player: dict, draft: list[str]) -> types.InlineKeyboardMarkup:
    guild_level = player.get("buildings", {}).get("guild", 0)
    barracks_level = player.get("buildings", {}).get("barracks", 0)
    active = player.get("active_expeditions", [])
    team_limit = get_expedition_team_limit(guild_level)
    slot_limit = get_expedition_slot_limit(barracks_level)
    grid_size = int(player.get("inv_grid", 3) or 3)
    if grid_size not in {2, 3, 4}:
        grid_size = 3
    fighters = get_available_fighters(player, include_zero_stamina=False)
    user_id = player.get("user_id", 0)
    items_per_page = grid_size * EXPEDITION_ROWS_PER_PAGE
    total_pages = max(1, (len(fighters) + items_per_page - 1) // items_per_page)
    current_page = expedition_pages.get(user_id, 0)
    if current_page >= total_pages:
        current_page = max(0, total_pages - 1)
    expedition_pages[user_id] = current_page
    page_start = current_page * items_per_page
    page_fighters = fighters[page_start: page_start + items_per_page]
    builder = InlineKeyboardBuilder()

    row = []
    for fighter in page_fighters:
        marker = "✅" if fighter["id"] in draft else "▫️"
        row.append(
            types.InlineKeyboardButton(
                text=f"{marker} {get_fighter_title(fighter)} [{fighter['stamina']}/{get_fighter_stamina_cap(fighter)}]",
                callback_data=f"expedition_pick:{fighter['id']}",
            )
        )
        if len(row) == grid_size:
            builder.row(*row)
            row = []
    if row:
        builder.row(*row)

    if total_pages > 1:
        nav_row = []
        if current_page > 0:
            nav_row.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"expedition_page:{current_page - 1}"))
        if current_page < total_pages - 1:
            nav_row.append(types.InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"expedition_page:{current_page + 1}"))
        if nav_row:
            builder.row(*nav_row)

    next_grid = 3 if grid_size == 2 else 4 if grid_size == 3 else 2
    controls = InlineKeyboardBuilder()
    controls.button(text=f"⚙️ Сетка: {grid_size} в ряд (Жми для {next_grid})", callback_data=f"expedition_set_grid:{next_grid}")

    can_launch = bool(draft) and len(draft) <= team_limit and len(active) < slot_limit
    controls.button(text="🚀 Отправить", callback_data="expedition_launch" if can_launch else "expedition_nope")
    controls.button(text="🧹 Очистить", callback_data="expedition_clear")
    controls.button(text="🔄 Обновить", callback_data="expeditions_open")

    for i, expedition in enumerate(active, 1):
        controls.button(
            text=f"📻 Экспедиция #{i}",
            callback_data=f"expedition_view:{expedition['id']}",
        )

    controls.button(text="🔙 В Казармы", callback_data="barracks_open")
    controls.adjust(1)
    builder.attach(controls)
    return builder.as_markup()


def render_expedition_detail(expedition: dict, index: int) -> str:
    load_now = get_bag_load(expedition.get("loot", {}))
    fighters = ", ".join(get_role_name(item) for item in expedition.get("fighter_snapshots", expedition.get("fighters", [])))
    logs = expedition.get("logs", [])[-VISIBLE_LOGS:]
    status_text = expedition.get("status_label", expedition.get("status", "Неизвестно"))
    if expedition.get("status") == "awaiting_choice":
        left = max(0, expedition.get("interactive_timeout_at", 0) - int(time.time()))
        status_text += f" (⏳{left // 60}м{left % 60}с по истечении — отряд обходит)"
    lines = [
        f"📻 **ЭКСПЕДИЦИЯ #{index}**",
        f"Бойцы: {fighters}",
        "",
        "Перехват:",
    ]
    if logs:
        lines.extend(f"• {line}" for line in logs)
    else:
        lines.append("• Отряд ещё только распаковывает котлы.")
    lines.extend(
        [
            "",
            f"Статус: {status_text}",
            f"HP: {expedition['current_hp']}/{expedition['total_hp']}",
            f"Рюкзак: {load_now}/{expedition['cargo_capacity']}",
        ]
    )
    lines.append(f"Трофеи: {format_loot_summary(expedition.get('loot', {}))}")
    if expedition.get("status") == "returning":
        left = max(0, int(expedition.get("return_end_at", 0) - time.time()))
        lines.append(f"До прибытия: {left // 60}м {left % 60}с")
    return "\n".join(lines)


def build_expedition_detail_keyboard(expedition: dict) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if expedition.get("status") == "searching":
        builder.button(text="↩️ Отступление", callback_data=f"expedition_retreat:{expedition['id']}")
    elif expedition.get("status") == "awaiting_choice":
        builder.button(text="⏳ Отряд ждёт решения…", callback_data=f"expedition_view:{expedition['id']}")
    if expedition.get("status") == "arrived":
        builder.button(text="🏁 Встретить отряд", callback_data=f"expedition_claim:{expedition['id']}")
    builder.button(text="🔙 К экспедициям", callback_data="expeditions_open")
    builder.adjust(1)
    return builder.as_markup()


def build_expedition_claim_keyboard() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Закрыть", callback_data="alc:base_close")
    builder.button(text="🔙 В Штаб", callback_data="alc:base_return")
    builder.adjust(1)
    return builder.as_markup()

LOCA_BY_ID = {loc["id"]: loc for loc in INTERACTIVE_LOCATIONS}


def build_interactive_choice_keyboard(expedition_id: str) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🚪 Идём внутрь (риск)", callback_data=f"exp_int_choice:{expedition_id}:enter")
    builder.button(text="🍄 Собираем и уходим (безопасно)", callback_data=f"exp_int_choice:{expedition_id}:pass")
    builder.adjust(1)
    return builder.as_markup()


def build_fighter_select_keyboard(expedition_id: str, fighters: list[dict]) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for snap in fighters:
        name = snap.get("name", snap.get("unit_id", "Боец"))
        builder.button(text=f"🪖 {name}", callback_data=f"exp_int_fighter:{expedition_id}:{snap.get('id', '')}")
    builder.button(text="🛡 Все вместе", callback_data=f"exp_int_fighter:{expedition_id}:all")
    builder.button(text="↩️ Назад", callback_data=f"exp_int_back:{expedition_id}")
    builder.adjust(1)
    return builder.as_markup()


async def _send_interactive_message(player: dict, expedition: dict, event: dict) -> int | None:
    """Отправляет сообщение с выбором действия.

    Возвращает message_id или None в случае ошибки.
    """
    location = LOCA_BY_ID.get(event.get("location_id"), {})
    title = location.get("title", "Что-то странное")
    text = (
        f"🚨 **{title}**\n\n"
        f"{event.get('text', 'Отряд заметил что-то интересное.')}\n\n"
        "❓ Что предпринять? (15 минут на ответ)"
    )
    try:
        msg = await bot.send_message(
            player["user_id"],
            text,
            reply_markup=build_interactive_choice_keyboard(expedition["id"]),
            parse_mode="Markdown",
        )
        return msg.message_id
    except Exception as exc:
        logger.error("Failed to send interactive event message: %s", exc)
        return None


async def _resolve_interactive_safe(expedition: dict, interactive_state: dict) -> None:
    """Авто-решение по истечении тайм-аута: отряд проходит мимо."""
    safe_loot_spec = interactive_state.get("safe_loot", {})
    if safe_loot_spec:
        loot = _roll_loot_amount(safe_loot_spec)
        add_loot_to_bag(expedition, loot)
        loot_text = ", ".join(f"{ELEMENTS.get(k, k)} x{v}" for k, v in loot.items())
        expedition["logs"] = trim_logs(expedition.get("logs", []) + [f"Отряд не стал рисковать. Собрали {loot_text}."])
    else:
        expedition["logs"] = trim_logs(expedition.get("logs", []) + ["Отряд осторожно обошёл место.", "Ничего примечательного.", "Просто уставшие люди и багги — не время для приключений."])
    expedition["status"] = "searching"
    expedition["next_tick_at"] = int(time.time()) + EXPEDITION_TICK
    for key in ("interactive_timeout_at", "interactive_state", "interactive_message_id"):
        expedition.pop(key, None)


async def discover_random_recipe(player: dict) -> str | None:
    known = set(player.get("discovered", [])) | set(player.get("inventory", {}).keys())
    unknown = [item_id for item_id in ELEMENTS if item_id not in known and item_id not in BATTLE_UNITS]
    if not unknown:
        return None
    recipe = random.choice(unknown)
    player["discovered"].append(recipe)
    return recipe


def finalize_return(expedition: dict, reason: str):
    if expedition.get("status") != "searching":
        return
    now = int(time.time())
    expedition["status"] = "returning"
    expedition["status_label"] = "Идёт домой"
    spent = max(1, now - expedition.get("started_at", now))
    expedition["return_end_at"] = now + max(RETURN_MIN_TIME, spent // 2)
    expedition["return_reason"] = reason
    expedition["next_tick_at"] = 0
    expedition["logs"] = trim_logs(expedition.get("logs", []) + [f"Отряд разворачивается домой: {reason}."])


async def process_single_expedition(player: dict, expedition: dict) -> bool:
    now = int(time.time())
    changed = False
    if expedition.get("status") == "searching" and expedition.get("next_tick_at", 0) <= now:
        events = expedition.get("planned_events", [])
        pointer = expedition.get("event_index", 0)
        if pointer >= len(events) or now >= expedition.get("max_end_at", now):
            finalize_return(expedition, "маршрут исчерпан")
            return True

        event = events[pointer]
        expedition["event_index"] = pointer + 1
        expedition["logs"] = trim_logs(expedition.get("logs", []) + [event["text"]])
        expedition["next_tick_at"] = now + EXPEDITION_TICK
        changed = True

        event_type = event.get("type")
        if event_type == "interactive":
            # Поставить экспедицию на паузу, отправить сообщение игроку
            expedition["status"] = "awaiting_choice"
            expedition["status_label"] = "Ждёт приказа"
            expedition["interactive_timeout_at"] = now + INTERACTIVE_CHOICE_TIMEOUT
            state = event.copy()
            # Нормализуем safe/risk loot specs: tuple -> [min, max] для MongoDB
            def _loot_spec(spec: dict) -> dict:
                out = {}
                for k, v in spec.items():
                    if isinstance(v, tuple):
                        out[k] = [v[0], v[1]]
                    else:
                        out[k] = v
                return out
            state["safe_loot"] = _loot_spec(state.get("safe_loot", {}))
            state["risk_loot"] = _loot_spec(state.get("risk_loot", {}))
            expedition["interactive_state"] = state
            expedition["next_tick_at"] = 0
            msg_id = await _send_interactive_message(player, expedition, event)
            if msg_id:
                expedition["interactive_message_id"] = msg_id
            log_entry = f"Наткнулись на интерактивную локацию: {LOCA_BY_ID.get(event.get('location_id'), {}).get('title', 'что-то непонятное')}. Ждём приказ."
            expedition["logs"] = trim_logs(expedition.get("logs", []) + [log_entry])
        elif event_type == "loot":
            add_loot_to_bag(expedition, event.get("loot", {}))
            loot_text = ", ".join(f"{ELEMENTS.get(item_id, item_id)} x{amount}" for item_id, amount in event.get("loot", {}).items())
            expedition["logs"] = trim_logs(expedition["logs"] + [f"Добыча: {loot_text}."])
        elif event_type == "damage":
            expedition["current_hp"] = max(0, expedition.get("current_hp", 0) - event.get("damage", 0))
            expedition["logs"] = trim_logs(expedition["logs"] + [f"Потеряли {event.get('damage', 0)} HP."])
        elif event_type == "heal":
            expedition["current_hp"] = min(expedition["total_hp"], expedition.get("current_hp", 0) + event.get("heal", 0))
            expedition["logs"] = trim_logs(expedition["logs"] + [f"Передышка вернула {event.get('heal', 0)} HP."])
        elif event_type == "meeting":
            if event.get("reward_recipe"):
                recipe = await discover_random_recipe(player)
                if recipe:
                    expedition["logs"] = trim_logs(expedition["logs"] + [f"Новый рецепт: {ELEMENTS.get(recipe, recipe)}."])
                else:
                    add_loot_to_bag(expedition, {"pack_s": 1})
                    expedition["logs"] = trim_logs(expedition["logs"] + ["Незнакомец сунул запасной набор вместо рецепта."])
            elif event.get("reward_pack"):
                add_loot_to_bag(expedition, {event["reward_pack"]: 1})
                expedition["logs"] = trim_logs(expedition["logs"] + [f"Подарок в дорогу: {ELEMENTS.get(event['reward_pack'], event['reward_pack'])}."])

        load_now = get_bag_load(expedition.get("loot", {}))
        if expedition.get("current_hp", 0) <= 0:
            expedition["loot"] = {}
            expedition["current_hp"] = 1
            finalize_return(expedition, "отряд едва выжил и бросил лут")
        elif expedition.get("current_hp", 0) <= max(1, int(expedition["total_hp"] * 0.10)):
            finalize_return(expedition, "здоровье упало ниже безопасного порога")
        elif load_now >= expedition.get("cargo_capacity", 0):
            finalize_return(expedition, "рюкзаки трещат по швам")

    # Ответ истёк — авто-уход по безопасному пути
    if expedition.get("status") == "awaiting_choice" and expedition.get("interactive_timeout_at", 0) <= now:
        state = expedition.pop("interactive_state", {})
        await _resolve_interactive_safe(expedition, state)
        changed = True

    if expedition.get("status") == "returning" and expedition.get("return_end_at", 0) <= now:
        expedition["status"] = "arrived"
        expedition["status_label"] = "Отряд вернулся"
        expedition["logs"] = trim_logs(expedition.get("logs", []) + ["Отряд добрался до базы и ждёт встречи."])
        expedition["final_report"] = await generate_expedition_final_report(expedition)
        changed = True
    return changed


async def process_expeditions_tick():
    cursor = db.alchemy_players.find({"active_expeditions.0": {"$exists": True}})
    async for player in cursor:
        expeditions = player.get("active_expeditions", [])
        changed = False
        for expedition in expeditions:
            changed = await process_single_expedition(player, expedition) or changed
        if changed:
            await db.alchemy_players.update_one(
                {"user_id": player["user_id"]},
                {"$set": {"active_expeditions": expeditions, "discovered": player.get("discovered", [])}},
            )


@router.callback_query(F.data == "expeditions_open")
async def expeditions_open(callback: types.CallbackQuery):
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    draft = cleanup_expedition_draft(player, callback.from_user.id)
    await safe_edit_text(callback.message, render_expeditions_text(player, draft), build_expeditions_keyboard(player, draft))
    await callback.answer()


@router.callback_query(F.data == "expedition_nope")
async def expedition_nope(callback: types.CallbackQuery):
    await callback.answer("Либо отряд пуст, либо лимит экспедиций уже забит.", show_alert=True)


@router.callback_query(F.data == "expedition_clear")
async def expedition_clear(callback: types.CallbackQuery):
    expedition_drafts[callback.from_user.id] = []
    expedition_pages[callback.from_user.id] = 0
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    await safe_edit_text(callback.message, render_expeditions_text(player, []), build_expeditions_keyboard(player, []))
    await callback.answer("Черновик очищен.")


@router.callback_query(F.data.startswith("expedition_pick:"))
async def expedition_pick(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    guild_level = player.get("buildings", {}).get("guild", 0)
    draft = cleanup_expedition_draft(player, user_id).copy()
    fighter_id = callback.data.split(":")[1]
    team_limit = get_expedition_team_limit(guild_level)

    if fighter_id in draft:
        draft.remove(fighter_id)
    else:
        if len(draft) >= team_limit:
            return await callback.answer("Больше бойцов Гильдия пока не пустит.", show_alert=True)
        fighter = find_fighter(player, fighter_id)
        if not fighter or fighter.get("status") != "available":
            expedition_drafts[user_id] = cleanup_expedition_draft(player, user_id)
            await safe_edit_text(callback.message, render_expeditions_text(player, expedition_drafts[user_id]), build_expeditions_keyboard(player, expedition_drafts[user_id]))
            return await callback.answer("Список бойцов обновлён. Попробуй выбрать заново.", show_alert=True)
        if fighter.get("status") == "garrison":
            return await callback.answer("Боец в гарнизоне и не может пойти в экспедицию.", show_alert=True)
        if int(fighter.get("stamina", 0)) <= 0:
            return await callback.answer("Уставший боец в экспедицию не пойдёт.", show_alert=True)
        draft.append(fighter_id)

    expedition_drafts[user_id] = draft
    await safe_edit_text(callback.message, render_expeditions_text(player, draft), build_expeditions_keyboard(player, draft))
    await callback.answer()


@router.callback_query(F.data.startswith("expedition_page:"))
async def expedition_page(callback: types.CallbackQuery):
    expedition_pages[callback.from_user.id] = int(callback.data.split(":")[1])
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    draft = cleanup_expedition_draft(player, callback.from_user.id)
    await safe_edit_text(callback.message, render_expeditions_text(player, draft), build_expeditions_keyboard(player, draft))
    await callback.answer()


@router.callback_query(F.data.startswith("expedition_set_grid:"))
async def expedition_set_grid(callback: types.CallbackQuery):
    new_grid = int(callback.data.split(":")[1])
    if new_grid not in {2, 3, 4}:
        new_grid = 3
    expedition_pages[callback.from_user.id] = 0
    await db.alchemy_players.update_one({"user_id": callback.from_user.id}, {"$set": {"inv_grid": new_grid}})
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    player["inv_grid"] = new_grid
    draft = cleanup_expedition_draft(player, callback.from_user.id)
    await safe_edit_text(callback.message, render_expeditions_text(player, draft), build_expeditions_keyboard(player, draft))
    await callback.answer()


@router.callback_query(F.data == "expedition_launch")
async def expedition_launch(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    draft = cleanup_expedition_draft(player, user_id).copy()
    if not draft:
        return await callback.answer("Нечего отправлять.", show_alert=True)

    barracks_level = player.get("buildings", {}).get("barracks", 0)
    guild_level = player.get("buildings", {}).get("guild", 0)
    slot_limit = get_expedition_slot_limit(barracks_level)
    if len(player.get("active_expeditions", [])) >= slot_limit:
        return await callback.answer("Все слоты экспедиций уже заняты.", show_alert=True)
    draft_fighters = resolve_draft_fighters(player, draft, expedition_mode=True)
    if len(draft_fighters) != len(draft):
        return await callback.answer("Часть бойцов уже занята или устала. Обновите окно.", show_alert=True)

    await callback.answer("Отряд отправляется.")
    now = int(time.time())
    for fighter in draft_fighters:
        consume_fighter_stamina(fighter, now)
        fighter["status"] = "expedition"
    stats = build_team_stats(draft_fighters, barracks_level)
    expedition = {
        "id": uuid.uuid4().hex[:10],
        "fighters": [fighter["unit_id"] for fighter in draft_fighters],
        "fighter_ids": [fighter["id"] for fighter in draft_fighters],
        "fighter_snapshots": [build_fighter_snapshot(fighter) for fighter in draft_fighters],
        "started_at": now,
        "max_end_at": now + GUILD_EXPEDITION_DURATION.get(guild_level, 7200) + len(draft) * 900,
        "status": "searching",
        "status_label": "Исследует мир",
        "cargo_capacity": stats["cargo_capacity"],
        "loot": {},
        "total_hp": stats["total_hp"],
        "current_hp": stats["total_hp"],
        "logs": [
            "Отряд вышел за ворота и уже спорит, кто понесёт котёл.",
            f"Потенциал отряда: груз {stats['cargo_capacity']}, дальность {9 + stats.get('total_speed', 0) // 5} событий, нюх на редкое {10 + stats.get('total_perception', 0) // 10}%.",
        ],
        "planned_events": build_planned_events_sync(draft_fighters, stats),
        "event_index": 0,
        "next_tick_at": now + EXPEDITION_TICK,
        "return_end_at": 0,
        "final_report": "",
    }
    active = player.get("active_expeditions", [])
    active.append(expedition)
    counters = increment_action_counter(player, "expeditions_started")
    expedition_drafts[user_id] = []

    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "fighters": player.get("fighters", []),
                "active_expeditions": active,
                "action_counters": counters,
            }
        },
    )
    player["active_expeditions"] = active
    await safe_edit_text(callback.message, render_expeditions_text(player, []), build_expeditions_keyboard(player, []))


def find_expedition(player: dict, expedition_id: str) -> dict | None:
    for expedition in player.get("active_expeditions", []):
        if expedition.get("id") == expedition_id:
            return expedition
    return None


@router.callback_query(F.data.startswith("expedition_view:"))
async def expedition_view(callback: types.CallbackQuery):
    player = await get_or_create_player(callback.from_user.id, callback.from_user.first_name)
    expedition_id = callback.data.split(":")[1]
    expedition = find_expedition(player, expedition_id)
    if not expedition:
        return await callback.answer("Экспедиция уже закрыта.", show_alert=True)
    # Находим индекс экспедиции
    active = player.get("active_expeditions", [])
    index = next((i + 1 for i, exp in enumerate(active) if exp.get("id") == expedition_id), 1)
    await safe_edit_text(callback.message, render_expedition_detail(expedition, index), build_expedition_detail_keyboard(expedition))
    await callback.answer()


@router.callback_query(F.data.startswith("expedition_retreat:"))
async def expedition_retreat(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    expedition = find_expedition(player, callback.data.split(":")[1])
    if not expedition:
        return await callback.answer("Экспедиция не найдена.", show_alert=True)
    finalize_return(expedition, "командир нажал отступление")
    counters = increment_action_counter(player, "expeditions_retreats")
    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {"$set": {"active_expeditions": player["active_expeditions"], "action_counters": counters}},
    )
    await safe_edit_text(callback.message, render_expedition_detail(expedition), build_expedition_detail_keyboard(expedition))
    await callback.answer("Отряд развернут домой.")


@router.callback_query(F.data.startswith("expedition_claim:"))
async def expedition_claim(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    expedition_id = callback.data.split(":")[1]
    expedition = find_expedition(player, expedition_id)
    if not expedition or expedition.get("status") != "arrived":
        return await callback.answer("Отряд ещё не прибыл.", show_alert=True)

    inventory = player.get("inventory", {})
    burned = []
    for item_id, amount in expedition.get("loot", {}).items():
        if item_id not in inventory and get_used_slots(inventory) >= STORAGE_CAPACITY.get(player.get("buildings", {}).get("storage", 0), 20):
            burned.append(f"{ELEMENTS.get(item_id, item_id)} x{amount}")
            continue
        inventory[item_id] = inventory.get(item_id, 0) + amount

    for fighter_id in expedition.get("fighter_ids", []):
        fighter = find_fighter(player, fighter_id)
        if not fighter:
            continue
        fighter["status"] = "available"
        grant_mission_progress(fighter, "expedition")

    player["active_expeditions"] = [item for item in player.get("active_expeditions", []) if item.get("id") != expedition_id]
    found_fragment = await try_grant_random_lore_fragment(player, chance=1.0)
    outcome_counter = "expeditions_failed" if expedition.get("return_reason") == "отряд едва выжил и бросил лут" else "expeditions_completed"
    counters = increment_action_counter(player, outcome_counter)
    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "inventory": inventory,
                "fighters": player.get("fighters", []),
                "active_expeditions": player["active_expeditions"],
                "action_counters": counters,
                "lore_fragments": player.get("lore_fragments", {}),
                "lore_fragment_progress": player.get("lore_fragment_progress", {}),
            }
        },
    )

    returned_loot = format_loot_summary(expedition.get("loot", {}))
    returned_units = ", ".join(get_role_name(item) for item in expedition.get("fighter_snapshots", expedition.get("fighters", [])))
    text = (
        "🏁 **ОТЧЁТ ЭКСПЕДИЦИИ**\n"
        f"Бойцы: {returned_units}\n"
        f"Итог: {expedition.get('final_report') or await generate_expedition_final_report(expedition)}\n\n"
        f"Доставлено на склад: {returned_loot}\n"
        f"Причина возврата: {expedition.get('return_reason', 'маршрут завершён')}\n"
        f"Состояние строя: {expedition.get('current_hp', 0)}/{expedition.get('total_hp', 0)} HP"
    )
    if burned:
        text += "\nСгорело из-за лимита склада: " + ", ".join(burned)
    if found_fragment:
        text += (
            "\n\n📜 Найден обрывок лора: "
            f"{found_fragment['title']} ({found_fragment['collection_name']})."
        )
    await callback.message.edit_text(text, reply_markup=build_expedition_claim_keyboard(), parse_mode="Markdown")
    await callback.answer("Отряд вернулся в строй.")


def _calculate_team_combat_power(player: dict, expedition: dict, chosen_id: str | None = None) -> tuple[int, list[str], list[dict]]:
    """Возвращает (power, list of unit_ids, list of deadly_fighters if any)."""
    from handlers.alc_fighters import find_fighter

    snapshots = expedition.get("fighter_snapshots", [])
    unit_ids = [snap.get("unit_id", "") for snap in snapshots]
    power = 0
    dead_list = []

    for snap in snapshots:
        fid = snap.get("id")
        fighter = find_fighter(player, fid)
        if not fighter:
            continue
        stats = fighter.get("stats", {})
        power += stats.get("attack", 0) * 2 + stats.get("defense", 0) + stats.get("speed", 0) + stats.get("perception", 0) + stats.get("support", 0)

    # Роль бонусы
    for uid in unit_ids:
        if uid == "medic":
            power += 5
        elif uid == "engineer":
            power += 5
        elif uid == "saboteur":
            power += 8
        elif uid == "sniper":
            power += 5
        elif uid in {"mech", "juggernaut", "flame_tank", "walker"}:
            power += 3

    # Если выбран конкретный боец — значительный бонус к его стилю
    if chosen_id:
        fighter = find_fighter(player, chosen_id)
        if fighter:
            stats = fighter.get("stats", {})
            role_power = stats.get("attack", 0) * 2 + stats.get("defense", 0) + stats.get("support", 0)
            # Если боец скоростной — бонус к избежанию урона
            if stats.get("speed", 0) > 8:
                role_power += stats.get("speed", 0)
            power += role_power

    return power, unit_ids, dead_list


async def _resolve_interactive_combat(expedition: dict, player: dict, state: dict, chosen_id: str | None = None) -> tuple[bool, str, list]:
    """Возвращает (win, log_text, dead_fighters_list)."""
    from handlers.alc_fighters import find_fighter, get_fighter_risk, hall_of_fame_entry, is_hero

    difficulty = state.get("difficulty", 15)
    damage_range = state.get("damage_range", (5, 15))
    team_power, unit_ids, _dead = _calculate_team_combat_power(player, expedition, chosen_id)
    enemy_power = difficulty * 2 + random.randint(5, 25)

    # Mediс и Engineer снижают урон
    has_medic = "medic" in unit_ids
    has_engineer = "engineer" in unit_ids
    tank_count = sum(1 for u in unit_ids if u in {"water_guard", "earth_shielder", "mech", "juggernaut"})

    attack_roll = random.randint(0, 25)
    defense_roll = random.randint(0, 20)
    win = (team_power + attack_roll) >= (enemy_power + defense_roll)

    # Урон
    if win:
        base_damage = random.randint(5, 12)
    else:
        base_damage = random.randint(*damage_range)

    # Медик и инжинер уменьшают урон
    if has_medic:
        base_damage = max(1, int(base_damage * 0.85))
    if has_engineer:
        base_damage = max(1, int(base_damage * 0.80))
    if tank_count > 0:
        base_damage = max(1, int(base_damage * (1 - 0.10 * min(tank_count, 2))))

    # Применяем урон к экспедиции
    expedition["current_hp"] = max(0, expedition.get("current_hp", 0) - base_damage)

    log_lines = []
    dead_list = []
    if win:
        log_lines.append(f"Отряд вступил в бой и победил. Потеряли {base_damage} HP.")
    else:
        log_lines.append(f"Отряд оступил. Потеряли {base_damage} HP.")

    # Смерть бойцов
    fighter_ids = expedition.get("fighter_ids", [])
    if win:
        death_chance = 0.01  # 1%
    else:
        death_chance = 0.03 + random.random() * 0.02  # 3-5%
        if tank_count > 0:
            death_chance = max(0.01, death_chance - 0.02 * min(tank_count, 2))

    for fid in fighter_ids:
        fighter = find_fighter(player, fid)
        if not fighter:
            continue
        roll = random.random()
        if roll < death_chance:
            if is_hero(fighter):
                fighter["stamina"] = 0
                fighter["stamina_updated_at"] = int(time.time())
                log_lines.append(f"🩹 {get_role_name(fighter)} получил тяжёлую рану, но выжил. Бодрость сброшена до 0.")
            else:
                fighter["status"] = "dead"
                dead_list.append(fighter)
                player["hall_of_fame"] = player.get("hall_of_fame", []) + [hall_of_fame_entry(fighter, f"Погиб в экспедиции: {state.get('location_id', 'неизвестно')}")]
                log_lines.append(f"💀 {get_role_name(fighter)} не выжил.")

    # Если медик — лечим чуть-чуть (если никто не умер)
    if has_medic and not dead_list:
        heal = random.randint(3, 8)
        expedition["current_hp"] = min(expedition["total_hp"], expedition.get("current_hp", 0) + heal)
        log_lines.append(f"🩺 Медик залатал раны. +{heal} HP.")

    return win, " ".join(log_lines), dead_list


async def _resolve_interactive_choice(expedition: dict, state: dict, win: bool, player: dict, chosen_id: str | None = None) -> str:
    safe_loot_spec = state.get("safe_loot", {})
    risk_loot_spec = state.get("risk_loot", {})
    logs = []

    if win:
        if safe_loot_spec:
            rolled = _roll_loot_amount(safe_loot_spec)
            add_loot_to_bag(expedition, rolled)
            text = ", ".join(f"{ELEMENTS.get(k, k)} x{v}" for k, v in rolled.items())
            logs.append(f"Собрали барыш: {text}.")

    expedition["status"] = "searching"
    expedition["next_tick_at"] = int(time.time()) + EXPEDITION_TICK
    for key in ("interactive_timeout_at", "interactive_state", "interactive_message_id"):
        expedition.pop(key, None)
    return " ".join(logs)


@router.callback_query(F.data.startswith("exp_int_choice:"))
async def exp_int_choice(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 3:
        return await callback.answer("Неверный формат.", show_alert=True)
    expedition_id = parts[1]
    choice = parts[2]
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    expedition = find_expedition(player, expedition_id)
    if not expedition or expedition.get("status") != "awaiting_choice":
        return await callback.answer("Событие уже неактивно.", show_alert=True)

    state = expedition.get("interactive_state", {})
    location = LOCA_BY_ID.get(state.get("location_id"), {})

    if choice == "pass":
        # Безопасный путь
        await _resolve_interactive_safe(expedition, state)
        await db.alchemy_players.update_one({"user_id": user_id}, {"$set": {"active_expeditions": player["active_expeditions"]}})
        await callback.message.edit_text(f"🍄 Отряд осторожно обошёл локацию.\n\n{expedition['logs'][-1]}")
        return await callback.answer("Отряд прошёл мимо.")

    elif choice == "enter":
        # Выбор бойца или всего отряда
        snapshots = expedition.get("fighter_snapshots", [])
        await callback.message.edit_text(
            f"⚔️ Рискнуть со всем отрядом или выбрать разведчика?\n\n_{location.get('risk_text', 'Что-то непонятное...')}_",
            reply_markup=build_fighter_select_keyboard(expedition_id, snapshots),
            parse_mode="Markdown",
        )
        return await callback.answer("Кого отправляем первым?")
    else:
        return await callback.answer("Неизвестный выбор.", show_alert=True)


@router.callback_query(F.data.startswith("exp_int_fighter:"))
async def exp_int_fighter(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 3:
        return await callback.answer("Неверный формат.", show_alert=True)
    expedition_id = parts[1]
    fighter_id = parts[2]
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    expedition = find_expedition(player, expedition_id)
    if not expedition or expedition.get("status") != "awaiting_choice":
        return await callback.answer("Событие уже неактивно.", show_alert=True)

    state = expedition.get("interactive_state", {})

    # Сначала боёвка
    win, log_text, dead_list = await _resolve_interactive_combat(expedition, player, state, chosen_id=(fighter_id if fighter_id != "all" else None))

    # Добыча
    loot_text = ""
    if win:
        risk_loot_spec = state.get("risk_loot", {})
        if risk_loot_spec:
            rolled = _roll_loot_amount(risk_loot_spec)
            add_loot_to_bag(expedition, rolled)
            loot_text = ", ".join(f"{ELEMENTS.get(k, k)} x{v}" for k, v in rolled.items())
    else:
        # При поражении получаем только safe loot
        safe_loot_spec = state.get("safe_loot", {})
        if safe_loot_spec:
            rolled = _roll_loot_amount(safe_loot_spec)
            add_loot_to_bag(expedition, rolled)
            loot_text = ", ".join(f"{ELEMENTS.get(k, k)} x{v}" for k, v in rolled.items())

    expedition["status"] = "searching"
    expedition["status_label"] = "Исследует мир"
    for key in ("interactive_timeout_at", "interactive_state", "interactive_message_id"):
        expedition.pop(key, None)
    expedition["next_tick_at"] = int(time.time()) + EXPEDITION_TICK

    # Обновляем лог
    full_text = f"{log_text} Добыча: {loot_text}." if loot_text else log_text
    expedition["logs"] = trim_logs(expedition.get("logs", []) + [full_text])

    # Те же проверки выхода, что и в process_single_expedition
    load_now = get_bag_load(expedition.get("loot", {}))
    if expedition.get("current_hp", 0) <= 0:
        expedition["loot"] = {}
        expedition["current_hp"] = 1
        finalize_return(expedition, "отряд едва выжил и бросил лут")
    elif expedition.get("current_hp", 0) <= max(1, int(expedition["total_hp"] * 0.10)):
        finalize_return(expedition, "здоровье упало ниже безопасного порога")
    elif load_now >= expedition.get("cargo_capacity", 0):
        finalize_return(expedition, "рюкзаки трещат по швам")

    await db.alchemy_players.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "active_expeditions": player["active_expeditions"],
                "fighters": player.get("fighters", []),
                "hall_of_fame": player.get("hall_of_fame", []),
            }
        },
    )

    await callback.message.edit_text(f"{'✅ Победа!' if win else '❌ Скатертью дорога.'}\n\n{full_text}")
    return await callback.answer("Готово.")


@router.callback_query(F.data.startswith("exp_int_back:"))
async def exp_int_back(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 2:
        return await callback.answer("Неверный формат.", show_alert=True)
    expedition_id = parts[1]
    user_id = callback.from_user.id
    player = await get_or_create_player(user_id, callback.from_user.first_name)
    expedition = find_expedition(player, expedition_id)
    if not expedition or expedition.get("status") != "awaiting_choice":
        return await callback.answer("Событие уже неактивно.", show_alert=True)
    state = expedition.get("interactive_state", {})
    location = LOCA_BY_ID.get(state.get("location_id"), {})
    text = f"🚨 **{location.get('title', 'Что-то странное')}**\n\n{state.get('risk_text', 'Отряд заметил что-то интересное.')}\n\n❓ Что предпринять?"
    await callback.message.edit_text(text, reply_markup=build_interactive_choice_keyboard(expedition_id), parse_mode="Markdown")
    return await callback.answer("Возврат к выбору.")


async def notify_arrived_expeditions():
    cursor = db.alchemy_players.find({"active_expeditions.status": "arrived"})
    async for player in cursor:
        last_notified = False
        active = player.get("active_expeditions", [])
        for i, expedition in enumerate(active):
            if expedition.get("status") == "arrived" and not expedition.get("arrival_notified"):
                expedition["arrival_notified"] = True
                last_notified = True
                try:
                    await bot.send_message(player["user_id"], f"📻 Экспедиция #{i + 1} вернулась. Открой /base и встреть отряд.")
                except Exception as e:
                    logger.debug("Failed to notify arrived expedition for user %s: %s", player["user_id"], e)
        if last_notified:
            await db.alchemy_players.update_one(
                {"user_id": player["user_id"]},
                {"$set": {"active_expeditions": active}},
            )


async def safe_edit_text(message: types.Message, text: str, reply_markup=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
