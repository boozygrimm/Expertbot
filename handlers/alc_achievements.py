from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database import db
from bot_instance import bot

router = Router()

HIDDEN_ITEMS = ["base_crystal", "active_base"]
PAGE_SIZE = 15


def action_counter(player: dict, key: str) -> int:
    return player.get("action_counters", {}).get(key, 0)


def format_reward_label(reward_item: str, reward_count: int) -> str:
    item_name = reward_item
    if reward_item == "pack_s":
        item_name = "📦 Малый лутбокс"
    elif reward_item == "pack_m":
        item_name = "📦 Средний лутбокс"
    elif reward_item == "pack_l":
        item_name = "📦 Большой лутбокс"
    elif reward_item == "recipe_scroll":
        item_name = "📜 Свиток рецепта"
    elif reward_item == "spirit_w":
        item_name = "💧 Дух Воды"
    elif reward_item == "spirit_e":
        item_name = "🪨 Дух Земли"
    elif reward_item == "spirit_f":
        item_name = "🔥 Дух Огня"
    elif reward_item == "spirit_a":
        item_name = "💨 Дух Воздуха"
    elif reward_item == "energy":
        item_name = "🎇 Энергия"
    return f"{item_name} x{reward_count}"

# ==================== СИСТЕМА АЧИВОК ====================
# Награды сбалансированы: recipe_scroll, pack_s/m/l, духи.
ACHIEVEMENTS = {
    # Милоустоуны коллекционирования и прокачки
    "first_steps": {"name": "👶 Первые шаги", "desc": "Собрать суммарно 50 любых ресурсов.", "condition": lambda p: sum(v for k, v in p.get("inventory", {}).items() if k not in HIDDEN_ITEMS) >= 50, "reward_item": "recipe_scroll", "reward_count": 2},
    "hoarder_1": {"name": "🐹 Плюшкин-новичок", "desc": "Накопить 200 ресурсов на складе.", "condition": lambda p: sum(v for k, v in p.get("inventory", {}).items() if k not in HIDDEN_ITEMS) >= 200, "reward_item": "pack_s", "reward_count": 1},
    "hoarder_2": {"name": "🐹 Мастер Плюшкин", "desc": "Накопить 500 ресурсов на складе.", "condition": lambda p: sum(v for k, v in p.get("inventory", {}).items() if k not in HIDDEN_ITEMS) >= 500, "reward_item": "pack_m", "reward_count": 1},
    "hoarder_3": {"name": "🐉 Смауг", "desc": "Накопить 1000 ресурсов на складе. Золото манит!", "condition": lambda p: sum(v for k, v in p.get("inventory", {}).items() if k not in HIDDEN_ITEMS) >= 1000, "reward_item": "pack_l", "reward_count": 1},
    "lab_worker": {"name": "⚗️ Младший научный сотрудник", "desc": "Улучшить Лабораторию до 2 уровня.", "condition": lambda p: p.get("buildings", {}).get("lab", 1) >= 2, "reward_item": "spirit_w", "reward_count": 1},
    "mad_scientist": {"name": "⚡ Безумный ученый", "desc": "Улучшить Лабораторию до 3 уровня.", "condition": lambda p: p.get("buildings", {}).get("lab", 1) >= 3, "reward_item": "spirit_f", "reward_count": 2},
    "builder": {"name": "📦 Завскладом", "desc": "Улучшить Склад до 1 уровня.", "condition": lambda p: p.get("buildings", {}).get("storage", 0) >= 1, "reward_item": "spirit_e", "reward_count": 1},
    "warehouse_manager": {"name": "🏢 Директор логистики", "desc": "Улучшить Склад до 3 уровня.", "condition": lambda p: p.get("buildings", {}).get("storage", 0) >= 3, "reward_item": "pack_m", "reward_count": 1},

    # Игровые (Смешные крафты, отсылки и приколы)
    "craft_vodka": {"name": "🥃 Лекарство от всего", "desc": "Создать Водку. Главное — не злоупотреблять.", "condition": lambda p: p.get("inventory", {}).get("vodka", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    "craft_moonshine": {"name": "🍶 Дедовский секрет", "desc": "Выгнать Самогон. Слепым не станешь, но риск есть.", "condition": lambda p: p.get("inventory", {}).get("moonshine", 0) > 0, "reward_item": "pack_s", "reward_count": 1},
    "craft_beer": {"name": "🍺 Губит людей не пиво", "desc": "Сварить Пиво. Вода-то губит!", "condition": lambda p: p.get("inventory", {}).get("beer", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    "craft_champagne": {"name": "🍾 Кто не рискует...", "desc": "Создать Шампанское. ...тот не пьет!", "condition": lambda p: p.get("inventory", {}).get("champagne", 0) > 0, "reward_item": "recipe_scroll", "reward_count": 1},
    "craft_alcohol": {"name": "🍷 Только в научных целях", "desc": "Синтезировать Спирт.", "condition": lambda p: p.get("inventory", {}).get("alcohol", 0) > 0, "reward_item": "pack_s", "reward_count": 1},
    "craft_alcoholic": {"name": "🥴 Заблудшая душа", "desc": "Создать Алкоголика. Ты в ответе за тех, кого приручил.", "condition": lambda p: p.get("inventory", {}).get("alcoholic", 0) > 0, "reward_item": "spirit_w", "reward_count": 2},
    
    "craft_zombie": {"name": "🧟 Мозги-и-и", "desc": "Оживить Зомби. Только не пускай его в огород с горохом.", "condition": lambda p: p.get("inventory", {}).get("zombie", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    "craft_vampire": {"name": "🧛 Сумерки уже не те", "desc": "Создать Вампира. Блестит на солнце?", "condition": lambda p: p.get("inventory", {}).get("vampire", 0) > 0, "reward_item": "spirit_a", "reward_count": 1},
    "craft_ghost": {"name": "👻 Каспер", "desc": "Вызвать Призрака. Он дружелюбный.", "condition": lambda p: p.get("inventory", {}).get("ghost", 0) > 0, "reward_item": "spirit_a", "reward_count": 1},
    "craft_mummy": {"name": "🧟 Верните проклятую книгу!", "desc": "Забинтовать Мумию. Имхотеп недоволен.", "condition": lambda p: p.get("inventory", {}).get("mummy", 0) > 0, "reward_item": "pack_s", "reward_count": 1},
    
    "craft_dinosaur": {"name": "🦖 Парк Юрского периода", "desc": "Возродить Динозавра. Что могло пойти не так?", "condition": lambda p: p.get("inventory", {}).get("dinosaur", 0) > 0, "reward_item": "spirit_e", "reward_count": 2},
    "craft_pterodactyl": {"name": "🦇 Небо в опасности", "desc": "Создать Птеродактиля. Главное, чтобы на голову не нагадил.", "condition": lambda p: p.get("inventory", {}).get("pterodactyl", 0) > 0, "reward_item": "spirit_a", "reward_count": 1},
    "craft_catdog": {"name": "🐕🐈 Один любит рок...", "desc": "...другой кантри. Создать Котопса.", "condition": lambda p: p.get("inventory", {}).get("catdog", 0) > 0, "reward_item": "pack_s", "reward_count": 1},
    "craft_penguin": {"name": "🐧 Ковальски, варианты!", "desc": "Слепить Пингвина. Улыбаемся и машем.", "condition": lambda p: p.get("inventory", {}).get("penguin", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    
    "craft_pizza": {"name": "🍕 Черепашки одобряют", "desc": "Приготовить Пиццу. Кавабанга!", "condition": lambda p: p.get("inventory", {}).get("pizza", 0) > 0, "reward_item": "pack_s", "reward_count": 1},
    "craft_borscht": {"name": "🍲 А где сметана?", "desc": "Сварить Борщ. Национальная гордость.", "condition": lambda p: p.get("inventory", {}).get("borscht", 0) > 0, "reward_item": "spirit_f", "reward_count": 1},
    "craft_sushi": {"name": "🍣 Дайте палочки", "desc": "Скрутить Суши. Японский минимализм.", "condition": lambda p: p.get("inventory", {}).get("sushi", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    "craft_salo": {"name": "🥓 Украинский сникерс", "desc": "Засолить Сало. Сила!", "condition": lambda p: p.get("inventory", {}).get("salo", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    
    "craft_coca_cola": {"name": "🥤 Праздник к нам приходит", "desc": "Синтезировать Кока-Колу. Очищает ржавчину!", "condition": lambda p: p.get("inventory", {}).get("coca_cola", 0) > 0, "reward_item": "pack_s", "reward_count": 1},
    "craft_coffee": {"name": "☕ Доброе утро", "desc": "Создать Какао/Кофе. Без него глаза не открываются.", "condition": lambda p: p.get("inventory", {}).get("cocoa", 0) > 0, "reward_item": "spirit_f", "reward_count": 1},
    
    "craft_robot": {"name": "🤖 Слава роботам!", "desc": "Собрать Робота. Бендер одобряет.", "condition": lambda p: p.get("inventory", {}).get("robot", 0) > 0, "reward_item": "recipe_scroll", "reward_count": 1},
    "craft_atomic_bomb": {"name": "☢️ Оппенгеймер", "desc": "Создать Атомную бомбу. Я стал Смертью, разрушителем миров.", "condition": lambda p: p.get("inventory", {}).get("atomic_bomb", 0) > 0, "reward_item": "spirit_f", "reward_count": 3},
    "craft_radiation": {"name": "☢️ 3.6 рентген", "desc": "Получить Радиацию. Не отлично, но и не ужасно.", "condition": lambda p: p.get("inventory", {}).get("radiation", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    
    "craft_sex": {"name": "❤️ 18+", "desc": "Создать Любовь/Секс. Детям не показывать.", "condition": lambda p: p.get("inventory", {}).get("sex", 0) > 0, "reward_item": "spirit_f", "reward_count": 1},
    "craft_kama_sutra": {"name": "📖 Гимнастика для двоих", "desc": "Создать Камасутру. Справочник юного натуралиста.", "condition": lambda p: p.get("inventory", {}).get("kama_sutra", 0) > 0, "reward_item": "recipe_scroll", "reward_count": 2},
    
    "craft_firearm": {"name": "🔫 Say hello...", "desc": "...to my little friend! Создать Огнестрел.", "condition": lambda p: p.get("inventory", {}).get("firearm", 0) > 0, "reward_item": "pack_s", "reward_count": 1},
    "craft_weapon": {"name": "🗡 Это не игрушка!", "desc": "Скрафтить Оружие. Не поранься.", "condition": lambda p: p.get("inventory", {}).get("weapon", 0) > 0, "reward_item": "spirit_f", "reward_count": 1},
    "craft_poisoned_weapon": {"name": "🗡 Грязная игра", "desc": "Отравленное оружие. Женевская конвенция? Не слышали.", "condition": lambda p: p.get("inventory", {}).get("poisoned_weapon", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    
    "craft_russia": {"name": "🇷🇺 Широка страна моя родная", "desc": "Открыть Россию. Балалайки в комплекте не идут.", "condition": lambda p: p.get("inventory", {}).get("russia", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    "craft_america": {"name": "🇺🇸 Открыл Америку", "desc": "Хотя плыл в Индию.", "condition": lambda p: p.get("inventory", {}).get("america", 0) > 0, "reward_item": "pack_s", "reward_count": 1},
    "craft_china": {"name": "🇨🇳 Made in...", "desc": "Открыть Китай. Тут делается всё.", "condition": lambda p: p.get("inventory", {}).get("china", 0) > 0, "reward_item": "recipe_scroll", "reward_count": 1},
    "craft_japan": {"name": "🇯🇵 Сакура и аниме", "desc": "Открыть Японию. Омаэ ва мо шиндэйру.", "condition": lambda p: p.get("inventory", {}).get("japan", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    "craft_australia": {"name": "🇦🇺 Вверх ногами", "desc": "Открыть Австралию. Там все хотят тебя убить.", "condition": lambda p: p.get("inventory", {}).get("australia", 0) > 0, "reward_item": "spirit_f", "reward_count": 1},
    
    "craft_bacteria": {"name": "🦠 Мытье рук отменяется", "desc": "Создать Бактерию. Она везде.", "condition": lambda p: p.get("inventory", {}).get("bacteria", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    "craft_life": {"name": "🧬 Создатель", "desc": "Сотворить Жизнь. И увидел он, что это хорошо.", "condition": lambda p: p.get("inventory", {}).get("life", 0) > 0, "reward_item": "recipe_scroll", "reward_count": 2},
    "craft_blood": {"name": "🩸 Первое правило...", "desc": "...Бойцовского клуба. Пустить Кровь.", "condition": lambda p: p.get("inventory", {}).get("blood", 0) > 0, "reward_item": "spirit_f", "reward_count": 1},
    "craft_corpse": {"name": "💀 Что мертво...", "desc": "...то не умрет. Создать Труп.", "condition": lambda p: p.get("inventory", {}).get("corpse", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    
    "craft_gold": {"name": "🥇 Моя прелесть!", "desc": "Добыть Золото. Берегись хоббитов.", "condition": lambda p: p.get("inventory", {}).get("gold", 0) > 0, "reward_item": "pack_m", "reward_count": 1},
    "craft_diamond": {"name": "💎 Лучшие друзья девушек", "desc": "Синтезировать Алмаз. Искал медь, а нашел...", "condition": lambda p: p.get("inventory", {}).get("diamond", 0) > 0, "reward_item": "pack_m", "reward_count": 1},
    "craft_money": {"name": "💵 Не в них счастье...", "desc": "...а в их количестве. Сделать Деньги.", "condition": lambda p: p.get("inventory", {}).get("money", 0) > 0, "reward_item": "pack_s", "reward_count": 2},
    
    "craft_doctor": {"name": "👨‍⚕️ Пишет неразборчиво", "desc": "Выучить Доктора. Клятва Гиппократа.", "condition": lambda p: p.get("inventory", {}).get("doctor", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    "craft_killer": {"name": "🥷 Ничто не истинно...", "desc": "...и всё дозволено. Нанять Убийцу.", "condition": lambda p: p.get("inventory", {}).get("killer", 0) > 0, "reward_item": "spirit_a", "reward_count": 1},
    "craft_pirate": {"name": "🏴‍☠️ Йо-хо-хо!", "desc": "И бутылка рома! Создать Пирата.", "condition": lambda p: p.get("inventory", {}).get("pirate", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    "craft_robin_hood": {"name": "🏹 Берет у богатых", "desc": "И отдает бедным. Робин Гуд в деле.", "condition": lambda p: p.get("inventory", {}).get("robin_hood", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    "craft_shaman": {"name": "🧙‍♂️ Танцы с бубном", "desc": "Вызвать Шамана. Админские штучки.", "condition": lambda p: p.get("inventory", {}).get("shaman", 0) > 0, "reward_item": "spirit_a", "reward_count": 1},
    "craft_scientist": {"name": "👨‍🔬 Эврика!", "desc": "Получить Ученого. Главное не взорвать лабу.", "condition": lambda p: p.get("inventory", {}).get("scientist", 0) > 0, "reward_item": "recipe_scroll", "reward_count": 1},
    "craft_philosopher": {"name": "🧙‍♂️ В чем смысл?", "desc": "42. Создать Философа.", "condition": lambda p: p.get("inventory", {}).get("philosopher", 0) > 0, "reward_item": "spirit_a", "reward_count": 1},
    "craft_columbus": {"name": "👨‍✈️ А где Индия?", "desc": "Вызвать Колумба. Навигатор сломался.", "condition": lambda p: p.get("inventory", {}).get("columbus", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    
    "craft_tractor": {"name": "🚜 Поросенок Петр", "desc": "Завести Трактор. Пора валить!", "condition": lambda p: p.get("inventory", {}).get("tractor", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    "craft_car": {"name": "🚗 Не роскошь", "desc": "А средство передвижения. Собрать Машину.", "condition": lambda p: p.get("inventory", {}).get("car", 0) > 0, "reward_item": "spirit_f", "reward_count": 1},
    "craft_motorcycle": {"name": "🏍 Два колеса возят душу", "desc": "Собрать Мотоцикл. Хруст французской булки.", "condition": lambda p: p.get("inventory", {}).get("motorcycle", 0) > 0, "reward_item": "spirit_a", "reward_count": 1},
    "craft_plane": {"name": "✈️ Первым делом", "desc": "Ну а девушки потом. Собрать Самолет.", "condition": lambda p: p.get("inventory", {}).get("plane", 0) > 0, "reward_item": "spirit_a", "reward_count": 1},
    "craft_submarine": {"name": "🚢 Yellow Submarine", "desc": "Построить Подлодку. Мы все живем в ней.", "condition": lambda p: p.get("inventory", {}).get("submarine", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    "craft_space_shuttle": {"name": "🚀 Хьюстон, у нас проблемы", "desc": "Запустить Шаттл. Поехали!", "condition": lambda p: p.get("inventory", {}).get("space_shuttle", 0) > 0, "reward_item": "pack_m", "reward_count": 1},
    
    "craft_electricity": {"name": "⚡ Не влезай - убьет", "desc": "Открыть Электричество. Розетки детям не игрушка.", "condition": lambda p: p.get("inventory", {}).get("electricity", 0) > 0, "reward_item": "spirit_f", "reward_count": 1},
    "craft_magnet": {"name": "🧲 Притягательная личность", "desc": "Создать Магнит.", "condition": lambda p: p.get("inventory", {}).get("magnet", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    
    "craft_dirt": {"name": "💩 Танки не боятся", "desc": "Намесить Грязь. Свинья найдет.", "condition": lambda p: p.get("inventory", {}).get("dirt", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    "craft_manure": {"name": "💩 Запах весны", "desc": "Получить Навоз. К деньгам!", "condition": lambda p: p.get("inventory", {}).get("manure", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    
    "craft_pig": {"name": "🐷 Хрю-хрю", "desc": "Вырастить Свинью. Никакого бекона... пока.", "condition": lambda p: p.get("inventory", {}).get("pig", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    "craft_bacon": {"name": "🥓 Хрустящее удовольствие", "desc": "Сделать Бекон. Прости, Хрюша.", "condition": lambda p: p.get("inventory", {}).get("bacon", 0) > 0, "reward_item": "spirit_f", "reward_count": 1},
    
    "craft_book": {"name": "📖 Лучший подарок", "desc": "Сделать Книгу. Но телефон лучше.", "condition": lambda p: p.get("inventory", {}).get("book", 0) > 0, "reward_item": "recipe_scroll", "reward_count": 1},
    "craft_library": {"name": "📚 Тишина в зале!", "desc": "Построить Библиотеку. Тссс!", "condition": lambda p: p.get("inventory", {}).get("library", 0) > 0, "reward_item": "pack_s", "reward_count": 1},
    "craft_music": {"name": "🎵 Деспасито", "desc": "Изобрести Музыку. Соседям понравится.", "condition": lambda p: p.get("inventory", {}).get("music", 0) > 0, "reward_item": "spirit_a", "reward_count": 1},
    
    "craft_cigarettes": {"name": "🚬 Минздрав предупреждал", "desc": "Скрутить Сигареты. Курение вредит здоровью.", "condition": lambda p: p.get("inventory", {}).get("cigarettes", 0) > 0, "reward_item": "spirit_a", "reward_count": 1},
    "craft_clothing": {"name": "👕 Опять нечего надеть", "desc": "Сшить Одежду. А шкаф ломится.", "condition": lambda p: p.get("inventory", {}).get("clothing", 0) > 0, "reward_item": "pack_s", "reward_count": 1},
    
    "craft_city": {"name": "🏙 Понаехали!", "desc": "Построить Город. Пробки и смог включены.", "condition": lambda p: p.get("inventory", {}).get("city", 0) > 0, "reward_item": "pack_s", "reward_count": 1},
    "craft_skyscraper": {"name": "🏢 Ближе к звездам", "desc": "Возвести Небоскреб. Лифт не работает.", "condition": lambda p: p.get("inventory", {}).get("skyscraper", 0) > 0, "reward_item": "spirit_a", "reward_count": 1},
    
    "craft_moon": {"name": "🌕 Маленький шаг", "desc": "Для человека, огромный для алхимии.", "condition": lambda p: p.get("inventory", {}).get("moon", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    "craft_sun": {"name": "☀️ Не смотри на сварку", "desc": "Создать Солнце. SPF 5000+.", "condition": lambda p: p.get("inventory", {}).get("sun", 0) > 0, "reward_item": "spirit_f", "reward_count": 1},
    "craft_star": {"name": "⭐ Гори, гори ясно", "desc": "Зажечь Звезду. Чтобы не погасла.", "condition": lambda p: p.get("inventory", {}).get("star", 0) > 0, "reward_item": "spirit_f", "reward_count": 1},
    
    "craft_rainbow": {"name": "🌈 Лепрекон где-то рядом", "desc": "Вызвать Радугу. А где горшочек с золотом?", "condition": lambda p: p.get("inventory", {}).get("rainbow", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    "craft_storm": {"name": "⛈ Люблю грозу в начале мая", "desc": "Призвать Бурю. Зонт не поможет.", "condition": lambda p: p.get("inventory", {}).get("storm", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    "craft_ice": {"name": "🧊 Зима близко", "desc": "Заморозить Лёд. Джон Сноу одобряет.", "condition": lambda p: p.get("inventory", {}).get("ice", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    
    "craft_mushroom": {"name": "🍄 Белый кролик", "desc": "Найти Гриб. Съешь меня.", "condition": lambda p: p.get("inventory", {}).get("mushroom", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    "craft_cactus": {"name": "🌵 Суровый огурец", "desc": "Вырастить Кактус. Не обнимать!", "condition": lambda p: p.get("inventory", {}).get("cactus", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    "craft_fern": {"name": "🌿 Цветет раз в году", "desc": "Но это не точно. Найти Папоротник.", "condition": lambda p: p.get("inventory", {}).get("fern", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    "craft_algae": {"name": "🌿 Морская капуста", "desc": "Собрать Водоросли. Ням-ням.", "condition": lambda p: p.get("inventory", {}).get("algae", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    
    "craft_banana": {"name": "🍌 Миньоны в восторге", "desc": "Вырастить Банан. Ba-na-na!", "condition": lambda p: p.get("inventory", {}).get("banana", 0) > 0, "reward_item": "pack_s", "reward_count": 1},
    "craft_potatoes": {"name": "🥔 Картошка фри", "desc": "Выкопать Картофель. Бульба сила.", "condition": lambda p: p.get("inventory", {}).get("potatoes", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    
    "craft_bear": {"name": "🐻 Маша где-то рядом", "desc": "Приручить Медведя. Не буди до весны.", "condition": lambda p: p.get("inventory", {}).get("bear", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    "craft_wolf": {"name": "🐺 Безумно можно быть первым", "desc": "Приручить Волка. Ауф.", "condition": lambda p: p.get("inventory", {}).get("wolf", 0) > 0, "reward_item": "spirit_a", "reward_count": 1},
    "craft_camel": {"name": "🐫 Корабль пустыни", "desc": "Найти Верблюда. Плюется метко.", "condition": lambda p: p.get("inventory", {}).get("camel", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    
    "craft_parrot": {"name": "🦜 Попка - дурак!", "desc": "Вывести Попугая. Повтори-ка.", "condition": lambda p: p.get("inventory", {}).get("parrot", 0) > 0, "reward_item": "spirit_a", "reward_count": 1},
    "craft_hummingbird": {"name": "🐦 Пернатый вертолет", "desc": "Создать Колибри. Слишком быстро машет.", "condition": lambda p: p.get("inventory", {}).get("hummingbird", 0) > 0, "reward_item": "spirit_a", "reward_count": 1},
    "craft_vulture": {"name": "🦅 Санитар леса", "desc": "Привлечь Стервятника. Падальщик на страже.", "condition": lambda p: p.get("inventory", {}).get("vulture", 0) > 0, "reward_item": "spirit_a", "reward_count": 1},
    
    "craft_scorpion": {"name": "🦂 Get over here!", "desc": "Породить Скорпиона. Жалит больно.", "condition": lambda p: p.get("inventory", {}).get("scorpion", 0) > 0, "reward_item": "spirit_f", "reward_count": 1},
    "craft_spider": {"name": "🕷 Дружелюбный сосед", "desc": "Создать Паука. Радиоактивен?", "condition": lambda p: p.get("inventory", {}).get("spider", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    "craft_leech": {"name": "🪱 Кровопийца", "desc": "Найти Пиявку. Полезно, но мерзко.", "condition": lambda p: p.get("inventory", {}).get("leech", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    "craft_worm": {"name": "🪱 Червяк Джим", "desc": "Выкопать Червя. Супергерой в костюме.", "condition": lambda p: p.get("inventory", {}).get("worm", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    
    "craft_snake": {"name": "🐍 Слизерин", "desc": "Пригреть Змею. На груди.", "condition": lambda p: p.get("inventory", {}).get("snake", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    "craft_turtle": {"name": "🐢 Тише едешь", "desc": "Дальше будешь. Создать Черепаху.", "condition": lambda p: p.get("inventory", {}).get("turtle", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    
    "craft_whale": {"name": "🐋 Моби Дик", "desc": "Призвать Кита. Белый кит грядет.", "condition": lambda p: p.get("inventory", {}).get("whale", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    "craft_octopus": {"name": "🐙 Выпускайте Кракена!", "desc": "Найти Осьминога. 8 ног - это мощь.", "condition": lambda p: p.get("inventory", {}).get("octopus", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    "craft_squid": {"name": "🦑 Игра в...", "desc": "...кальмара. Создать Кальмара.", "condition": lambda p: p.get("inventory", {}).get("squid", 0) > 0, "reward_item": "pack_s", "reward_count": 1},
    "craft_crab": {"name": "🦀 Мистер Крабс", "desc": "Поймать Краба. Деньги, деньги, деньги!", "condition": lambda p: p.get("inventory", {}).get("crab", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    "craft_jellyfish": {"name": "🪼 Губка Боб на охоте", "desc": "Поймать Медузу. Не ужалься.", "condition": lambda p: p.get("inventory", {}).get("jellyfish", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    
    "craft_ukraine": {"name": "🇺🇦 Борщ и сало", "desc": "Открыть Украину. Смачного!", "condition": lambda p: p.get("inventory", {}).get("ukraine", 0) > 0, "reward_item": "spirit_f", "reward_count": 1},
    "craft_italy": {"name": "🇮🇹 Мамма миа!", "desc": "Открыть Италию. Пицца и паста.", "condition": lambda p: p.get("inventory", {}).get("italy", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    "craft_mexico": {"name": "🇲🇽 Амиго!", "desc": "Открыть Мексику. Сомбреро и текила.", "condition": lambda p: p.get("inventory", {}).get("mexico", 0) > 0, "reward_item": "spirit_f", "reward_count": 1},
    "craft_romania": {"name": "🇷🇴 Трансильвания зовет", "desc": "Открыть Румынию. Граф ждет гостей.", "condition": lambda p: p.get("inventory", {}).get("romania", 0) > 0, "reward_item": "spirit_a", "reward_count": 1},
    "craft_great_britain": {"name": "🇬🇧 Овсянка, сэр!", "desc": "Открыть Великобританию. Чай ровно в 5.", "condition": lambda p: p.get("inventory", {}).get("great_britain", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    
    "craft_cheese": {"name": "🧀 В мышеловке", "desc": "Сделать Сыр. Мыши в экстазе.", "condition": lambda p: p.get("inventory", {}).get("cheese", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    "craft_caramels": {"name": "🍬 Прощай, пломба!", "desc": "Сварить Карамель. Стоматолог одобряет.", "condition": lambda p: p.get("inventory", {}).get("caramels", 0) > 0, "reward_item": "spirit_f", "reward_count": 1},
    "craft_chocolate": {"name": "🍫 Слипнется", "desc": "Приготовить Шоколад. Эндорфины пошли.", "condition": lambda p: p.get("inventory", {}).get("chocolate", 0) > 0, "reward_item": "pack_s", "reward_count": 1},
    "craft_caviar": {"name": "🟠 Жизнь удалась", "desc": "Намазать Икру. Буржуазия.", "condition": lambda p: p.get("inventory", {}).get("caviar", 0) > 0, "reward_item": "pack_m", "reward_count": 1},
    
    "craft_medicine": {"name": "💊 Одно лечим...", "desc": "...другое калечим. Синтезировать Лекарство.", "condition": lambda p: p.get("inventory", {}).get("medicine", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    "craft_penicillin": {"name": "💊 Случайное открытие", "desc": "Получить Пенициллин. Спасибо плесени.", "condition": lambda p: p.get("inventory", {}).get("penicillin", 0) > 0, "reward_item": "recipe_scroll", "reward_count": 1},
    "craft_poison": {"name": "☠️ Чайку?", "desc": "Сварить Яд. Только для крыс (наверное).", "condition": lambda p: p.get("inventory", {}).get("poison", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    
    "craft_pearls": {"name": "🦪 Слезы моря", "desc": "Добыть Жемчуг. Очень дорого.", "condition": lambda p: p.get("inventory", {}).get("pearls", 0) > 0, "reward_item": "pack_s", "reward_count": 1},
    
    "craft_fugu_fish": {"name": "🐡 Рулетка по-японски", "desc": "Поймать Рыбу фугу. Шеф-повар не должен ошибиться.", "condition": lambda p: p.get("inventory", {}).get("fugu_fish", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    "craft_cocktail": {"name": "🍸 Взболтать, но не смешивать", "desc": "Приготовить Коктейль. Джеймс Бонд стайл.", "condition": lambda p: p.get("inventory", {}).get("cocktail", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    
    "craft_volcano": {"name": "🌋 Везувий ждет", "desc": "Зажечь Вулкан. Прячьтесь, жители Помпеи.", "condition": lambda p: p.get("inventory", {}).get("volcano", 0) > 0, "reward_item": "spirit_f", "reward_count": 1},
    "craft_geyser": {"name": "⛲ Внезапный душ", "desc": "Открыть Гейзер. Горячая вода бесплатно.", "condition": lambda p: p.get("inventory", {}).get("geyser", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    "craft_ocean": {"name": "🌊 Большая волна", "desc": "Создать Океан. Не забудь спасательный круг.", "condition": lambda p: p.get("inventory", {}).get("ocean", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    
    "craft_locomotive": {"name": "🚂 Чух-чух-чух", "desc": "Построить Паровоз. Билет до Хогвартса.", "condition": lambda p: p.get("inventory", {}).get("locomotive", 0) > 0, "reward_item": "spirit_f", "reward_count": 1},
    "craft_steamboat": {"name": "⛴ Только бы не айсберг", "desc": "Построить Пароход. Селин Дион заиграла.", "condition": lambda p: p.get("inventory", {}).get("steamboat", 0) > 0, "reward_item": "spirit_w", "reward_count": 1},
    "craft_clipper": {"name": "⛵ Поднять паруса!", "desc": "Спустить на воду Клипер. Капитан Джек Воробей.", "condition": lambda p: p.get("inventory", {}).get("clipper", 0) > 0, "reward_item": "spirit_a", "reward_count": 1},
    
    "craft_hut": {"name": "🛖 С милым рай и в шалаше", "desc": "Собрать Хижину. Главное, чтоб крыша не текла.", "condition": lambda p: p.get("inventory", {}).get("hut", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    "craft_brick_house": {"name": "🏠 Мой дом - моя крепость", "desc": "Построить Кирпичный дом. Волк не сдует.", "condition": lambda p: p.get("inventory", {}).get("brick_house", 0) > 0, "reward_item": "pack_s", "reward_count": 1},
    
    "craft_flu": {"name": "🤧 Не подходи, я заразный", "desc": "Синтезировать Грипп. Пора пить чай с малиной.", "condition": lambda p: p.get("inventory", {}).get("flu", 0) > 0, "reward_item": "spirit_a", "reward_count": 1},
    "craft_sickness": {"name": "🤒 Где градусник?", "desc": "Создать Болезнь. Отличный повод не идти в школу.", "condition": lambda p: p.get("inventory", {}).get("sickness", 0) > 0, "reward_item": "spirit_e", "reward_count": 1},
    
    "craft_glass": {"name": "🪟 Осторожно, хрупкое", "desc": "Выплавить Стекло. Не бросай камни.", "condition": lambda p: p.get("inventory", {}).get("glass", 0) > 0, "reward_item": "spirit_f", "reward_count": 1},
    "craft_hourglass": {"name": "⏳ Время песок", "desc": "Сделать Песочные часы. Тик-так.", "condition": lambda p: p.get("inventory", {}).get("hourglass", 0) > 0, "reward_item": "recipe_scroll", "reward_count": 1},
    
    "craft_phoenix": {"name": "🐦‍🔥 Восставший из пепла", "desc": "Возродить Феникса. Фоукс?", "condition": lambda p: p.get("inventory", {}).get("phoenix", 0) > 0, "reward_item": "spirit_f", "reward_count": 2},
    "craft_dragon": {"name": "🐉 Дракарис!", "desc": "Вырастить Дракона. Матерь драконов одобряет.", "condition": lambda p: p.get("inventory", {}).get("dragon", 0) > 0, "reward_item": "pack_m", "reward_count": 1},
    "craft_philosophers_stone": {"name": "🔴 Мечта алхимика", "desc": "Создать Философский камень. Элрики завидуют.", "condition": lambda p: p.get("inventory", {}).get("philosophers_stone", 0) > 0, "reward_item": "pack_l", "reward_count": 1}
}

ACHIEVEMENTS.update({
    "expedition_first": {"name": "🧭 Первый вылаз", "desc": "Запустить первую алхимическую экспедицию.", "condition": lambda p: action_counter(p, "expeditions_started") >= 1, "reward_item": "pack_s", "reward_count": 1},
    "expedition_return": {"name": "🏁 Вернулись живыми", "desc": "Успешно завершить 3 экспедиции.", "condition": lambda p: action_counter(p, "expeditions_completed") >= 3, "reward_item": "recipe_scroll", "reward_count": 1},
    "expedition_survivor": {"name": "🩹 Обожжённый, но упрямый", "desc": "Пережить проваленную экспедицию и вернуться домой.", "condition": lambda p: action_counter(p, "expeditions_failed") >= 1, "reward_item": "spirit_w", "reward_count": 1},
    "retreat_master": {"name": "↩️ Тактический гений", "desc": "Трижды вовремя отозвать экспедицию.", "condition": lambda p: action_counter(p, "expeditions_retreats") >= 3, "reward_item": "energy", "reward_count": 3},
    "raid_ready": {"name": "☠️ Пахнет керосином", "desc": "Подготовить первый рейд-отряд.", "condition": lambda p: action_counter(p, "raids_prepared") >= 1, "reward_item": "pack_s", "reward_count": 1},
    "raid_win": {"name": "💰 Лёгкая нажива", "desc": "Выиграть 3 рейда.", "condition": lambda p: action_counter(p, "raids_won") >= 3, "reward_item": "pack_m", "reward_count": 1},
    "fortress": {"name": "🏰 Не лезь, убьёт", "desc": "Трижды поймать врага ловушками Форта.", "condition": lambda p: action_counter(p, "defenses_triggered") >= 3, "reward_item": "spirit_e", "reward_count": 2},
    "slave_master": {"name": "🪤 Хозяин подвала", "desc": "Захватить 3 пленных.", "condition": lambda p: action_counter(p, "captives_taken") >= 3, "reward_item": "spirit_a", "reward_count": 1},
    "recruiter": {"name": "🤝 Вербовщик", "desc": "Успешно завербовать пленного.", "condition": lambda p: action_counter(p, "captives_recruited") >= 1, "reward_item": "recipe_scroll", "reward_count": 1},
    "retort_butcher": {"name": "⚗️ Без сантиментов", "desc": "Разобрать 5 пленных на запчасти.", "condition": lambda p: action_counter(p, "captives_recycled") >= 5, "reward_item": "energy", "reward_count": 5},
})

async def check_new_achievements(user_id: int):
    """Функция проверки условий. Вызывай её, когда игрок что-то крафтит или собирает."""
    player = await db.alchemy_players.find_one({"user_id": user_id})
    if not player: 
        return
        
    unlocked = player.get("achievements", [])
    inv = player.get("inventory", {})
    new_unlocked = False
    
    for ach_id, data in ACHIEVEMENTS.items():
        if ach_id not in unlocked:
            if data["condition"](player):
                unlocked.append(ach_id)
                new_unlocked = True
                
                reward_item = data["reward_item"]
                reward_count = data["reward_count"]
                inv[reward_item] = inv.get(reward_item, 0) + reward_count
                
                # Формируем красивое название награды
                msg = f"🏆 **Новое достижение: {data['name']}!**\n_{data['desc']}_\n\n🎁 Награда: {format_reward_label(reward_item, reward_count)}"
                try:
                    await bot.send_message(user_id, msg, parse_mode="Markdown")
                except Exception:
                    pass # Если бот в муте или заблокирован
    
    if new_unlocked:
        await db.alchemy_players.update_one(
            {"user_id": user_id},
            {"$set": {"achievements": unlocked, "inventory": inv}}
        )

# ==================== ОТОБРАЖЕНИЕ АЧИВОК (С ПАГИНАЦИЕЙ) ====================

@router.callback_query(F.data == "alc:achievements")
async def show_achievements_first_page(callback: types.CallbackQuery):
    await _show_achievements_page(callback, 0)

@router.callback_query(F.data.startswith("alc_ach_page:"))
async def show_achievements_page(callback: types.CallbackQuery):
    page = int(callback.data.split(":")[1])
    await _show_achievements_page(callback, page)

async def _show_achievements_page(callback: types.CallbackQuery, page: int):
    player = await db.alchemy_players.find_one({"user_id": callback.from_user.id})
    if not player:
        return await callback.answer("Игрок не найден!", show_alert=True)
        
    unlocked = player.get("achievements", [])
    
    total_unlocked = len(unlocked)
    total_pages = max(1, (total_unlocked + PAGE_SIZE - 1) // PAGE_SIZE)
    
    # Защита от выхода за пределы страниц
    if page >= total_pages and total_pages > 0:
        page = total_pages - 1
    elif page < 0:
        page = 0
        
    start_idx = page * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    
    # Берём ачивки только для текущей страницы
    page_achievements = unlocked[start_idx:end_idx]
    
    text = f"🏆 **ЗАЛ СЛАВЫ** (Страница {page + 1}/{total_pages})\nОткрыто: {total_unlocked} / {len(ACHIEVEMENTS)}\n\n"
    
    if not unlocked:
        text += "У тебя пока нет достижений. Экспериментируй и собирай ресурсы!\n(_Достижения выдаются по мере крафта в Лаборатории или при сборе лута._)"
    else:
        for ach_id in page_achievements:
            data = ACHIEVEMENTS.get(ach_id)
            if not data:
                continue 
            text += f"✅ **{data['name']}**\n_{data['desc']}_\n🎁 Получено: {format_reward_label(data['reward_item'], data['reward_count'])}\n\n"
            
    builder = InlineKeyboardBuilder()
    
    nav_row = []
    if page > 0:
        nav_row.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"alc_ach_page:{page-1}"))
    if page < total_pages - 1:
        nav_row.append(types.InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"alc_ach_page:{page+1}"))
        
    if nav_row:
        builder.row(*nav_row)
        
    builder.row(types.InlineKeyboardButton(text="🔙 В Штаб", callback_data="alc:base_return"))
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
