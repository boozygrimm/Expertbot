// miniapp_preview/raid_magitech.js
// Separate RAID design prototype, forked from classic build 63b4ca81a10e.
// Combat remains classical: no unapproved elemental coefficients or abilities.
// Appearance is cosmetic; the classic URL, source and profiles are not migrated.
'use strict';
(function () {
  // A local namespace, NOT authentication. The public Pages client never
  // validates an admission or sends initData to a server. Unknown Telegram
  // identities must not silently read/write the old shared browser profile.
  const TELEGRAM_PROFILE = typeof URLSearchParams === 'function'
    && new URLSearchParams(window.location?.search || '').get('telegram') === '1';
  function telegramProfileId() {
    try {
      const app = window.Telegram?.WebApp, id = app?.initData && app.initDataUnsafe?.user?.id;
      return Number.isSafeInteger(id) && id > 0 && id < 2 ** 52 ? id : null;
    } catch (ignored) { return null; }
  }
  function localProfilePrefix(id) {
    // Intentionally ignore raid_legacy_owner, including for the actual owner.
    // The redesign never reads, copies or writes classic raid:* / raid:tg:*.
    return `raid:magitech:tg:${id}:`;
  }
  let PROFILE_ID = TELEGRAM_PROFILE ? telegramProfileId() : null;
  let PROFILE_PREFIX = TELEGRAM_PROFILE ? (PROFILE_ID ? localProfilePrefix(PROFILE_ID) : null) : 'raid:magitech:';
  function profileNotice(text) {
    for (const id of ['picker-profile-status', 'menu-profile-status']) {
      const node = document.getElementById(id);
      if (node) { node.textContent = text; node.hidden = !text; }
    }
  }
  function profileStorageCheck() {
    if (PROFILE_PREFIX === null) {
      profileNotice('Профиль Telegram не определён. Общие данные не загружаются; без профиля сохранение недоступно. Открой Mini App заново.');
      throw new Error('Telegram profile unavailable');
    }
    if (TELEGRAM_PROFILE && telegramProfileId() !== PROFILE_ID) {
      profileNotice('Аккаунт Telegram сменился или недоступен. Запись остановлена: закрой Mini App и открой заново для своего аккаунта.');
      throw new Error('Telegram profile changed');
    }
  }
  const profileStorage = {
    key(key) { return PROFILE_PREFIX === null ? null : PROFILE_PREFIX + key.slice(5); },
    getItem(key) { profileStorageCheck(); return localStorage.getItem(this.key(key)); },
    setItem(key, value) { profileStorageCheck(); localStorage.setItem(this.key(key), value); },
    removeItem(key) { profileStorageCheck(); localStorage.removeItem(this.key(key)); },
  };
  function connectLocalProfile() {
    if (!TELEGRAM_PROFILE) return;
    const id = telegramProfileId();
    if (!id || PROFILE_ID && PROFILE_ID !== id || PROFILE_ID === null && S) {
      profileNotice('Профиль Telegram не определён или игра начата до его загрузки. Чтобы не затереть свой прогресс, открой Mini App заново; текущая игра не сохраняется.');
      return;
    }
    if (PROFILE_ID === null) {
      // SDK loads asynchronously. Rehydrate only before a run starts, never
      // relabel an anonymous live run or copy it into an existing account.
      PROFILE_ID = id; PROFILE_PREFIX = localProfilePrefix(id);
      classLevels = loadClassLevels(); classConfigs = loadClassConfigs(); selectedClassId = loadStoredClassId();
      loadRaidAppearance();
      RUN_SAVE_BASE = undefined;
      readRunSlot(); readRunHistory();
      if (HISTORY_OPEN) renderRunHistory();
      else if (pickerOpen) showClassPicker();
    }
    profileNotice(`Профиль Telegram ${PROFILE_ID} · свой прогресс на этом устройстве. Между устройствами не синхронизируется.`);
  }

  // ============================================================
  // КЛАССИЧЕСКИЕ ПАРАМЕТРЫ: Dungeon Raid, сложность «Нормaл»
  //
  // Правила и числа сверяем со спецификацией Android 1.2.11, код/ассеты свои.
  // Недоказанные границы и оставшиеся расхождения — в LUNAPARK.md §7.
  // ============================================================
  const COLS = 6, ROWS = 6, CELL_COUNT = COLS * ROWS;
  const START_SKULLS = [3, 4];
  // Original start: base max health 40 (InitNewGame) + accessory 10 = 50;
  // armour = helmet 1 + armour 2 + shield 1 = 4; weapon damage 2.
  const START_HP = 50, START_MAX_ARMOR = 4;

  // --- формулы урона (классика) ---
  // Урон цепочки со мечом: (база + сила + рост «Урона» + наследник меха)
  //                         + оружие × (число мечей в цепочке).
  // Оборона всегда поглощает этот удар; пробитие — шанс снять каждый пункт
  // поглощённой брони ПОСЛЕ расчёта потери HP (AttackSkull @0x2f630).
  // (не путать с бронёй героя, где работает «долговечность»)
  const BASE_DAMAGE = 2;           // база 2 + стартовая сила 1 = 3 без мечей
  const HP_PER_SHIELD_ARMOR = 1;   // базовая броня за щит (+ точность + 2 × защита)
  const SHIELD_UPGRADE_POINTS = 2; // UP за токен щита, только при полной броне/Коррозии
  const HEAL_PER_POTION = 1;       // базовая лечение за зелье (+ удача)
  const GOLD_PER_COIN = 2;         // базовая золото за монетку (+ boost gold)
  const XP_PER_SKULL = 2;          // опыт за убитого черепа (+ boost XP)

  // Bonus resource (классика): шанс каждого «лишнего» типа сверх первых трёх
  // в цепочке; база 20% + 5% за соответствующий стат + 5% за харизматику
  // + классные бонусы (Raider: 100% на 10+ клеток).
  const BONUS_BASE_CHANCE = 0.20;
  // Confused flaw / Unexpected perk window (turnData.earlyTurns).
  const EARLY_TURNS = 5;
  const BASE_PIERCING = 0.50;      // вероятность пробития брони черепа (классика)
  const BASE_DURABILITY = 0.50;    // вероятность, что пункт брони героя не списывается
  const MAX_DURABILITY = 0.95;     // порог разрушения не ниже 5 из 101 исхода

  const SPAWN_EVERY_TURNS = 3;
  const BOSS_EVERY_TURNS = 10;
  // Trophy interval carries across runs; the cap counts generated board tiles.
  const MAX_TROPHIES_PER_RUN = 3;
  const SPECIAL_EVERY_TURNS = 4;
  const UPGRADE_BAR_MAX = 100;    // полоса UP (от щитов) → выбор 1-из-4
  const GOLD_BOX_MAX = 100;       // золотой ящик → 1-из-3 предметов

  // Черепа: формула оригинала (Classic, Normal) — docs/spec/01-board.md:
  //   q = trunc(t · max(1, 1+(t−500)/700));  level = trunc(totalXP/100);
  //   если level > 40: q = trunc(q · (1+(level−40)/60))
  //   A = max(1, R(trunc(q/65), trunc(q/55)))   (⚔)
  //   D = max(0, R(trunc(q/50), trunc(q/40)))   (🛡)
  //   H = max(4, R(trunc(q/20), trunc(q/15)))   (❤️)
  //   — без «первые 3 хода бесплатно» и без линейного роста.
  // Босс: оригинал (specials.json multiplier «1»: ×3/×3/×6, spec 01-board.md)
  const BOSS_STATS = { atk: 3, def: 3, hp: 6 };

  // Спец-черепы (не босс): оригинал (spec 01-board.md, GenerateStats @0x63098).
  // 0.5 = усечение x/2 (Meat Shield A, Invisible H). Некоторые типы переопределяются по-особому — cf. makeSkull.
  const ENEMY_STAT_OVERRIDES = {
    2:  { atk: 2,   def: 1,  hp: 3 },    // Spiky
    3:  { atk: 0,   def: 6,  hp: 6 },    // Golden
    4:  { atk: 2,   def: 2,  hp: 4 },    // Healer
    5:  { atk: 2,   def: 10, hp: 0.5 },  // Invisible
    6:  { atk: 2,   def: 2,  hp: 4 },    // Acidic
    7:  { atk: 2,   def: 2,  hp: 4 },    // Flaming
    8:  { atk: 2,   def: 2,  hp: 4 },    // Freezing
    9:  { atk: 1,   def: 2,  hp: 4 },    // Poisonous
    10: { atk: 1,   def: 2,  hp: 6 },    // Kamikaze
    11: { atk: 2,   def: 2,  hp: 4 },    // Overseer
    12: { atk: 2,   def: 2,  hp: 4 },    // Corrosive
    13: { atk: 2,   def: 2,  hp: 4 },    // Thief
    14: { atk: 2,   def: 2,  hp: 4 },    // Chaotic
    15: { atk: 2,   def: 2,  hp: 4 },    // Mage
    16: { atk: 3,   def: 2,  hp: 3 },    // Assassin
    17: { atk: 2,   def: 1,  hp: 4 },    // Doppelganger (def = текущая броня героя)
    18: { atk: 2,   def: 2,  hp: 4 },    // Alchemist
    19: { atk: 4,   def: 1,  hp: 5 },    // Unstable
    20: { atk: 2,   def: 2,  hp: 4 },    // Vampire
    21: { atk: 2,   def: 2,  hp: 4 },    // Vengeful
    22: { atk: 2,   def: 2,  hp: 4 },    // Trampling
    23: { atk: 2,   def: 2,  hp: 4 },    // Greasy
    24: { atk: 2,   def: 2,  hp: 4 },    // Alpha
    25: { atk: 2,   def: 2,  hp: 4 },    // Magnetic
    26: { atk: 2,   def: 2,  hp: 4 },    // Brute
    27: { atk: 2,   def: 4,  hp: 3 },    // Armoured
    28: { atk: 2,   def: 2,  hp: 4 },    // Devourer
    29: { atk: 2,   def: 2,  hp: 4 },    // Defiler
    30: { atk: 0.5, def: 1,  hp: 5 },    // Meat Shield
    31: { atk: 2,   def: 2,  hp: 4 },    // Fleeing
    32: { atk: 2,   def: 2,  hp: 4 },    // Lich
  };
  const SPECIAL_ABILITY_TYPES = [2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32];
  // Счётчик 5→0 у Golden/Kamikaze/Unstable/Devourer (spec 01-board.md, «countdown=5»)
  const COUNTDOWN_TYPES = new Set([3, 10, 19, 28]);

  // Русские названия и особенности (3 линии) для спец-черепов.
  const ENEMY_FEATS = {
    2:  { name: 'Острый',      feats: ['Если Острый выбран в цепочке — отражает D один раз за всю цепочку', 'Статы: ×2 ⚔ / броня / ×3 ❤️', 'Отражение напрямую отнимает HP, минуя броню'] },
    3:  { name: 'Золотой',     feats: ['Собран до конца таймера — 15–25 монетных токенов', 'Не убит к концу — исчезает без награды', 'Статы: 0 ⚔ / ×6 🛡 / ×6 ❤️ · таймер 5'] },
    4:  { name: 'Исцеление',   feats: ['Лечит до 2 раненых черепов до макс. HP', 'Без раненых — даст до 2 черепам +1 к атаке', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    5:  { name: 'Невидимый',   feats: ['Повышенная броня и половина обычного HP', 'Статы: ×2 ⚔ / ×10 🛡 / ½ ❤️', 'Без отдельной способности'] },
    6:  { name: 'Кислотный',   feats: ['Каждый ход превращает одну обычную зелье в кислотное', 'Кислое зелье — ранит героя, а не лечит', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    7:  { name: 'Огненный',    feats: ['Каждый ход поджигает до 8 соседних клеток (не других Огненных)', 'Обгоревший череп получает урон этим врагом', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    8:  { name: 'Застывший',   feats: ['Каждый ход замораживает до 2 клеток', 'Замороженные ресурсы не соединяются; черепа соединяются, но не атакуют', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    9:  { name: 'Отравленный', feats: ['Если общий залп пробил броню, накладывает яд по своей атаке', 'Зелье снимает яд; исключение — смертный враг Эльфа на поле', 'Статы: ⚔ / ×2 🛡 / ×4 ❤️'] },
    10: { name: 'Ка-Ми-Казе',  feats: ['Таймер 5 → 0 — взрыв: пол-HP героя', 'При убийстве — тоже взрыв в соседних не-черепах', 'Статы: ⚔ / ×2 🛡 / ×6 ❤️ · таймер 5'] },
    11: { name: 'Наблюдатель', feats: ['Каждый ход превращает один меч в череп', 'Новый череп — обычный, уязвим для целей', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    12: { name: 'Коррозия',    feats: ['Пока на поле — броня не восстанавливается', 'При появлении обнуляет броню только у Орка — смертного врага', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    13: { name: 'Вор',         feats: ['Крадёт до 2 монетных клеток без награды', 'Без монет крадёт до 2 золота; HP-урон — только Саурену', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    14: { name: 'Хаотический', feats: ['Каждый ход меняется местом со случайной клеткой', 'Целевая клетка (вкл. обычный череп) — поглощается', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    15: { name: 'Маг',         feats: ['Пока на поле — запрет на естественное восстановление кулдаунов', 'Кулдауны навыков не растут', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    16: { name: 'Ассасин',      feats: ['В нижнем ряду — 2× ⚔ дополнительного урона герою', 'Сама атакует — в сумме 3× ⚔', 'Статы: ×3 ⚔ / ×2 🛡 / ×3 ❤️'] },
    17: { name: 'Доппелгангер', feats: ['Каждую игру копирует текущую броню героя', 'Лучше атаковать на 0 брони', 'Статы: ×2 ⚔ / 🛡 = броня / ×4 ❤️'] },
    18: { name: 'Алхимик',      feats: ['Каждый ход меняет до 2 случайных клеток', 'Результат может быть тем же типом', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    19: { name: 'Нестабильный', feats: ['Таймер 5 → 0 — телепорт: все клетки меняются', 'Новые клетки — без черепов', 'Статы: ×4 ⚔ / броня / ×5 ❤️ · таймер 5'] },
    20: { name: 'Вампир',       feats: ['Лечится на свою атаку, когда общий залп ранит героя', 'Обычно — до макс. HP; у Вервольфа может превысить максимум', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    21: { name: 'Мстительный',  feats: ['После цепочки +1 атака за каждое прямое убийство', 'Даже новый Мстительный получает эту прибавку', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    22: { name: 'Трамплящий',   feats: ['Каждый ход ломает один случайный щит или меч', 'Сломанное не приносит ничего', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    23: { name: 'Скользкий',    feats: ['В каждом столбце удаляет клетку под первым подходящим черепом', 'Удаление не даёт награды', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    24: { name: 'Альфа',        feats: ['Каждый ход меняет внешнего черепа с соседней не-черепной клеткой', 'Соседние клетки — только обычные (не черепа)', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    25: { name: 'Магнитный',     feats: ['Каждый ход меняет внешние мечи/щиты с соседними другими клетками', 'Целевая клетка может быть спец-черепом', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    26: { name: 'Брутал',        feats: ['Каждый ход выбрасывает старый обычный череп в героя', 'Урон героя = HP этого черепа', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    27: { name: 'Бронебойный',   feats: ['Получает урон цепочки, только если следующий узел — выше', 'Неуязвим в верхнем ряду', 'Статы: ×2 ⚔ / ×4 🛡 / ×3 ❤️'] },
    28: { name: 'Съедобный',     feats: ['Таймер 5 → 0 — снижает один модификатор снаряжения на шаг', 'Таймер сбрасывается на 5', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️ · таймер 5'] },
    29: { name: 'Загрязнитель',  feats: ['Пока на поле — каждый поглощённый пункт залпа ломает броню', 'Долговечность не действует, броски разрушения пропускаются', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    30: { name: 'Желе-Щит',      feats: ['Перехватывает урон цепочки от других черепов', 'Сам поглощает урон и уязвим', 'Статы: ½ ⚔ / броня / ×5 ❤️'] },
    31: { name: 'Бегун',        feats: ['Каждый ход уходит на одну клетку выше', 'На верхнем краю — исчезает без трофея', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    32: { name: 'Лич',          feats: ['Смертельный удар создаёт филактерий; сбор даёт награду особого', 'Если филактерий не собрать, Лич возродится', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
  };

  const SKILLS = [
    // 42 native skills. 0..30 are ordinary, 31..39 unlock globally at class
    // level 10. Hidden 40/41 never unlock (debug/imported states only).
    { id: 'steal', name: 'Воровство', icon: '🦊', cooldown: 13, native: 0, desc: 'Атака черепов в этом ходу даёт золото' },
    { id: 'freeze', name: 'Мороз', icon: '❄', cooldown: 16, native: 1, desc: 'Все черепа заморожены и не бьют этот ход' },
    { id: 'heal', name: 'Благословение', icon: '❤', cooldown: 15, native: 2, desc: 'Собрать все обычные зелья' },
    { id: 'repair', name: 'Ремонт', icon: '⚒', cooldown: 16, native: 3, desc: 'Собрать все щиты' },
    { id: 'counter', name: 'Контратака', icon: '↩', cooldown: 18, native: 4, desc: 'Вместо атаки черепа бьют сами себя' },
    { id: 'shatter', name: 'Раскол', icon: '💥', cooldown: 18, native: 5, desc: 'У всех обычных черепов броня 0, атака не выше 1' },
    { id: 'goldtouch', name: 'Золотое касание', icon: '🪙', cooldown: 15, native: 6, desc: 'Все мечи → монеты' },
    { id: 'teleport', name: 'Телепорт', icon: '✦', cooldown: 20, native: 7, desc: 'Все клетки без черепов → новые' },
    { id: 'explosive', name: 'Взрывное зелье', icon: '🧨', cooldown: 18, native: 8, desc: 'Все зелья становятся взрывными' },
    { id: 'elixir', name: 'Эликсир', icon: '⚗', cooldown: 18, native: 9, desc: 'Все зелья дают опыт вместо лечения' },
    { id: 'enchant', name: 'Затачивание', icon: '🛠', cooldown: 35, native: 10, desc: 'Бесплатно усилить снаряжение (1 из 4)' },
    { id: 'slash', name: 'Рассечение', icon: '🗡', cooldown: 12, native: 11, desc: 'Собрать случайную строку; с «Взломом» — всю доску' },
    { id: 'treasure', name: 'Кладовая', icon: '💎', cooldown: 18, native: 12, desc: 'Собрать все монеты' },
    { id: 'scavenge', name: 'Сбор', icon: '🛡', cooldown: 15, native: 13, desc: 'Все мечи → щиты' },
    { id: 'disarm', name: 'Обезоруживание', icon: '⚔', cooldown: 25, native: 14, desc: 'Все обычные черепа → мечи' },
    { id: 'dazzle', name: 'Слепота', icon: '✦', cooldown: 15, native: 15, desc: 'Все монеты → мечи' },
    { id: 'boostdmg', name: 'Усиление урона', icon: '⚔', cooldown: 14, native: 16, desc: 'Урон оружия ×2 в этом ходу' },
    { id: 'boostgold', name: 'Усиление золота', icon: '🪙', cooldown: 15, native: 17, desc: 'Золото ×2 в этом ходу' },
    { id: 'boosthealth', name: 'Усиление лечения', icon: '❤', cooldown: 15, native: 18, desc: 'Лечение зелий ×2 в этом ходу' },
    { id: 'boostarmour', name: 'Усиление брони', icon: '🛡', cooldown: 15, native: 19, desc: 'Броня и очки улучшений ×2 в этом ходу' },
    { id: 'exorcise', name: 'Изгнание', icon: '✝', cooldown: 18, native: 20, desc: 'Случайный спец-череп становится обычным' },
    { id: 'banish', name: 'Уничтожение', icon: '☄', cooldown: 20, native: 21, desc: 'Убрать случайный спец-череп без награды' },
    { id: 'trap', name: 'Ловушка', icon: '🕸', cooldown: 18, native: 22, desc: 'Новые черепа появляются с 1 HP' },
    { id: 'biggame', name: 'Крупная дичь', icon: '👹', cooldown: 15, native: 23, desc: 'Случайный обычный череп становится особым' },
    { id: 'chamber', name: 'Сокровищница', icon: '🪙', cooldown: 25, native: 24, desc: 'Новые клетки — монеты' },
    { id: 'magicsword', name: 'Магический меч', icon: '✨', cooldown: 14, native: 25, desc: 'Случайный меч бьёт как 5 мечей' },
    { id: 'earthquake', name: 'Землетрясение', icon: '🌋', cooldown: 12, native: 26, desc: 'Перемешать все клетки поля' },
    { id: 'explosivearmour', name: 'Взрывная броня', icon: '🛡', cooldown: 16, native: 27, desc: 'Ударить всех черепов на 2× текущую броню' },
    { id: 'fireball', name: 'Огненный шар', icon: '🔥', cooldown: 14, native: 28, desc: 'Собрать область 3×3 в случайном месте' },
    { id: 'mana', name: 'Зелье маны', icon: '🔹', cooldown: 20, native: 29, desc: 'Все зелья становятся зельями маны' },
    { id: 'hack', name: 'Взлом', icon: '🪓', cooldown: 24, native: 30, desc: 'Случайный столбец → мечи; с «Рассечением» — вся доска' },
  ];

  // 7 уровней-стата (классика: 2 навыка + 2 стата в уровне)
  // --- уровень: два навыка + два старика, выбор 2 из 4 (классика) ---
  // Эффекты выводятся как «старое >> новое», как в оригинале.
  const STATS = [
    {
      id: 'strength', name: 'Сила', icon: '💪', chip: '+СИЛ', color: '#ffd97a',
      desc: '+1 базового урона, +5% шанс бонуса опыта',
      effects: () => [
        `Базовый урон: ${fmt(baseDamage())} >> ${fmt(baseDamage() + 1)}`,
        `Шанс бонуса опыта: ${bonusPct('xp')}% >> ${bonusPct('xp', 5)}%`,
      ],
      apply: () => S.hero.stats.strength++
    },
    {
      id: 'dexterity', name: 'Точность', icon: '🎯', chip: '+ТОЧ', color: '#b8a8ff',
      desc: '+1 броня за щит, +5% шанс бонуса щита',
      effects: () => [
        `Броня за щит: ${fmt(perShield())} >> ${fmt(perShield() + 1)}`,
        `Шанс бонуса щита: ${bonusPct('shield')}% >> ${bonusPct('shield', 5)}%`,
      ],
      apply: () => S.hero.stats.dexterity++
    },
    {
      id: 'vitality', name: 'Живучесть', icon: '❤', chip: '+ЖИВ', color: '#ff9d9d',
      get desc() { return S.mode === 'pretzel' ? '+5% шанс бонуса зелий (без роста HP)' : '+5 макс. HP, +5% шанс бонуса зелий'; },
      effects: () => [
        `Макс. HP: ${fmt(S.hero.maxHp)} >> ${fmt(S.hero.maxHp + (S.mode === 'pretzel' ? 0 : 5))}`,
        `Шанс бонуса зелия: ${bonusPct('potion')}% >> ${bonusPct('potion', 5)}%`,
      ],
      apply: () => { S.hero.stats.vitality++; if (S.mode !== 'pretzel') { S.hero.hp += 5; S.hero.maxHp += 5; } }
    },
    {
      id: 'luck', name: 'Удача', icon: '🍀', chip: '+УДА', color: '#ffd94a',
      desc: '+1 лечение за зелье, +5% шанс бонусных монет',
      effects: () => [
        `Лечение за зелье: ${fmt(HEAL_PER_POTION + S.hero.stats.luck)} >> ${fmt(HEAL_PER_POTION + S.hero.stats.luck + 1)}`,
        `Шанс бонусной монеты: ${bonusPct('coin')}% >> ${bonusPct('coin', 5)}%`,
      ],
      apply: () => S.hero.stats.luck++
    },
    {
      // Native stat 7: +5 to every bonus-token chance at once.
      id: 'charisma', name: 'Все бонусы', icon: '💎', chip: '+БОН', color: '#8fd0ff',
      desc: '+5% ко всем бонусам',
      effects: () => [
        `Монеты/зелья/опыт/щиты: ${bonusPct('coin')}% >> ${bonusPct('coin', 5)}%`,
      ],
      apply: () => S.hero.stats.charisma++
    },
    {
      // Native stat 6: hp(2 * statIncrements.vitality) = +2×живучесть макс. HP.
      id: 'health', name: 'Здоровье', icon: '🩹', chip: '+ЗДР', color: '#ffb0b0',
      desc: '+10 макс. HP (2× живучесть)',
      effects: () => [`Макс. HP: ${fmt(S.hero.maxHp)} >> ${fmt(S.hero.maxHp + 10)}`],
      apply: () => {
        if (S.mode === 'pretzel') return;   // Pretzel: рост HP отключён
        S.hero.maxHp += 10; S.hero.hp += 10;
      }
    },
    {
      id: 'damage', name: 'Урон', icon: '⚔', chip: '+УРН', color: '#ff8a7a',
      desc: '+2 базового урона',
      effects: () => [
        `Базовый урон: ${fmt(baseDamage())} >> ${fmt(baseDamage() + 2)}`,
      ],
      apply: () => S.hero.stats.damage++
    },
    {
      // Native stat 5: base.repair += 2 × dexterity → +2 брони за щит.
      id: 'defense', name: 'Ремонт', icon: '🛡', chip: '+ЗАЩ', color: '#a8c8ff',
      desc: '+2 брони за щит',
      effects: () => [
        `Броня за щит: ${fmt(perShield())} >> ${fmt(perShield() + 2)}`,
      ],
      apply: () => S.hero.stats.defense++
    },
  ];
  function renderCharacterSummary(panel) {
    const h = S.hero, st = h.stats;
    const cls = CLASS_BY_ID[S.classId] || CLASS_BY_ID.adventurer;
    const rows = [
      ['Мех', cls.name + ' · ' + RACE_INFO[clsRace()].name],
      ['Пассивы', runClassConfig().perks.filter(Boolean).map(id => CLASS_TRAITS.perks[id].name).join(', ') || 'нет'],
      ['Изъяны', runClassConfig().flaws.filter(Boolean).map(id => CLASS_TRAITS.flaws[id].name).join(', ') || 'нет'],
      ['Здоровье', `${fmt(h.hp)} / ${fmt(h.maxHp)} HP`],
      ['Броня', `${fmt(h.armor)} / ${fmt(h.maxArmor)}`],
      ['Базовый урон', `${fmt(baseDamage())} + ${fmt(h.weaponDamage)} за каждый меч`],
      ['Сила / Урон', `${fmt(st.strength)} / ${fmt(st.damage)}`],
      ['Точность / Защита', `${fmt(st.dexterity)} / ${fmt(st.defense)}`],
      ['Живучесть / Удача / Все бонусы', `${fmt(st.vitality)} / ${fmt(st.luck)} / ${fmt(st.charisma)}`],
      ['Щит: ремонт ИЛИ улучшение', `${fmt(perShield())} брони; при полной броне/Коррозии — ${resourceYield(S.upgradePerShield).toFixed(2)} UP`],
      ['Лечение за зелье', `${fmt(HEAL_PER_POTION + st.luck)} HP`],
      ['Золото за монету / опыт за черепа', `${resourceYield(S.goldPerCoin).toFixed(2)} / ${resourceYield(S.xpPerSkull).toFixed(2)}`],
      ['Пробитие / стойкость брони', `${Math.round(S.pierce*100)}% / ${Math.round(S.durability*100)}%`],
      ['Регенерация / ответный урон', `${fmt(S.regen)} HP/ход / ${fmt(S.spikes)}`],
      ['Навыки', S.skills.length ? S.skills.map(id => (SKILL_BY_ID[id] || {}).name || id).join(', ') : 'пока нет'],
      ...((S.equipment || []).map(it => [it.slotName, it.name + ' — ' + itemModsText(it)])),
    ];
    panel.innerHTML = '<p class="summary-hint">Итоговые параметры с учётом выбранных улучшений и снаряжения.</p>';
    const list = document.createElement('div');
    list.className = 'character-summary-list';
    rows.forEach(([label, value]) => {
      const row = document.createElement('div');
      row.className = 'character-summary-row';
      row.innerHTML = `<span>${label}</span><b>${value}</b>`;
      list.appendChild(row);
    });
    panel.appendChild(list);
  }

  // Item modifiers use display units; native IDs below define ordering, not
  // foreign item names/art. A quarter point is a point, never a percent.
  const ITEM_KEYS = ['weaponDamage', 'baseDamage', 'leech', 'weaponPoison', 'pierce', 'quicken',
    'xpPerSkull', 'goldPerCoin', 'strength', 'luck', 'maxHp', 'maxArmor', 'spikes',
    'upgradePerShield', 'durability', 'blunt', 'armorPerShield', 'regen', 'dexterity', 'vitality'];
  const ITEM_STAT = {
    weaponDamage: { name: 'Урон оружия', icon: '⚔', unit: 1, desc: 'Урон за каждый меч в цепочке' },
    baseDamage: { name: 'Базовый урон', icon: '💥', unit: 1, desc: 'Прибавка к общему урону цепочки с черепом' },
    leech: { name: 'Сок (лечение)', icon: '🩸', unit: .01, percent: true, desc: 'Лечит на долю общего урона цепочки, с усечением' },
    weaponPoison: { name: 'Яд оружия', icon: '☠', unit: 1, desc: 'Прямой удар отравляет выжившего на 3 хода; Отравленный невосприимчив' },
    pierce: { name: 'Пробитие брони', icon: '🎯', unit: .01, percent: true, desc: 'Шанс разрушить каждый поглощённый пункт брони черепа; порог не выше 95' },
    quicken: { name: 'Ускорение навыков', icon: '⏱', unit: 1, desc: '−1 к кулдаунам; несколько предметов не складываются' },
    xpPerSkull: { name: 'Опыт за череп', icon: '⭐', unit: .25, desc: 'Дополнительные очки за токен опыта; общий бонус не выше +2' },
    goldPerCoin: { name: 'Золото за монету', icon: '🪙', unit: .25, desc: 'Дополнительные очки за токен монеты; общий бонус не выше +2' },
    strength: { name: 'Сила', icon: '💪', unit: 1, desc: '+1 базовый урон и +5% шанс бонусного опыта за единицу' },
    luck: { name: 'Удача', icon: '🍀', unit: 1, desc: '+1 лечение и +5% шанс бонусной монеты за единицу' },
    maxHp: { name: 'Макс. HP', icon: '❤', unit: 1, desc: 'Повышает запас HP; в Кренделе не действует' },
    maxArmor: { name: 'Макс. броня', icon: '🛡', unit: 1, desc: 'Повышает запас брони, сохраняя текущий дефицит' },
    spikes: { name: 'Шипы', icon: '🗡', unit: 1, desc: 'При наличии брони бьёт старых черепов; их защита действует' },
    upgradePerShield: { name: 'Очки UP за щит', icon: '⬆', unit: .25, desc: 'Бонус токена, дающего UP, не ремонта; общий бонус не выше +2' },
    durability: { name: 'Стойкость брони', icon: '🪨', unit: .01, percent: true, desc: 'Снижает шанс износа за поглощённый пункт, не ниже порога 5' },
    blunt: { name: 'Ослабление при блоке', icon: '🚫', unit: 1, desc: 'При наличии брони снижает атаку старых черепов до минимума 1; не меняет текущий залп' },
    armorPerShield: { name: 'Броня за щит', icon: '🔰', unit: 1, desc: 'Дополнительный ремонт за щит, не максимальная броня' },
    regen: { name: 'Регенерация', icon: '♻', unit: 1, desc: 'Лечение в конце хода до урона ядом' },
    dexterity: { name: 'Точность', icon: '🎯', unit: 1, desc: '+1 ремонт и +5% шанс бонусного щита за единицу' },
    vitality: { name: 'Живучесть', icon: '❤', unit: 1, get desc() { return S.mode === 'pretzel' ? '+5% шанс бонусного зелья за единицу (без роста HP)' : '+5 HP и +5% шанс бонусного зелья за единицу'; } },
  };
  const ITEM_SLOTS = [
    { id: 'weapon', name: 'Оружие', icon: '⚔', main: 'weaponDamage', appearance: [0, 79], pool: [0, 1, 2, 3, 4, 6, 8, 9] },
    { id: 'helmet', name: 'Шлем', icon: '🪖', main: 'maxArmor', appearance: [80, 159], pool: [6, 7, 10, 11, 12, 13, 17] },
    { id: 'armour', name: 'Доспех', icon: '🥋', main: 'maxArmor', appearance: [240, 319], pool: [10, 11, 12, 14, 15, 17, 18, 19] },
    { id: 'shield', name: 'Щит', icon: '🛡', main: 'maxArmor', appearance: [160, 239], pool: [0, 1, 11, 12, 13, 15, 16] },
    { id: 'accessory', name: 'Аксессуар', icon: '💎', main: 'maxHp', appearance: [320, 399], pool: [5, 8, 9, 10, 17, 18, 19] },
  ].map(slot => ({ ...slot, pool: slot.pool.map(id => ITEM_KEYS[id]) }));
  const ITEM_NAMES = {
    weapon: ['Ржавый клинок', 'Кривая сабля', 'Тяжёлая булава', 'Колючий молот', 'Топор палача', 'Клинок стражи'],
    helmet: ['Мятый шлем', 'Кожаный капюшон', 'Рогатый венец', 'Шлем рыцаря', 'Каска рудокопа'],
    armour: ['Стёганый жилет', 'Кольчуга', 'Чешуйчатый панцирь', 'Кираса стража', 'Мантия мага'],
    shield: ['Щепка', 'Круглый щит', 'Башенный щит', 'Щит-башня', 'Рунический барьер'],
    accessory: ['Медное кольцо', 'Амулет удачи', 'Кулон охотника', 'Печать жреца', 'Талисман героя'],
  };
  const ITEM_PREFIXES = ['Искра', 'Сумрак', 'Дозор', 'Грань', 'Эхо', 'Рассвет', 'Предел', 'Орбита', 'Зенит'];
  // Only numeric appearance/name-pool sizes are used to preserve ITEM draws;
  // the displayed names and graphics remain our own.
  const ITEM_NAME_POOLS = [[20, 15], [40, 12], [50, 13], [60, 12], [80, 15], [160, 17],
    [240, 16], [320, 17], [340, 14], [360, 11], [370, 14], [380, 16], [400, 16]];

  function itemSlotById(id) { return ITEM_SLOTS.find(slot => slot.id === id); }
  function itemValue(item, key) { return item?.mods?.[key] || 0; }
  function itemNumber(key, value) {
    return ITEM_STAT[key].percent ? Math.round(value * 100) + '%' : String(Number(value.toFixed(2)));
  }
  function itemModText(key, value) {
    const def = ITEM_STAT[key];
    return def ? `${def.icon} ${def.name} +${itemNumber(key, Math.abs(value))}` : '';
  }
  function itemModsText(item) {
    const keys = ITEM_KEYS.filter(key => itemValue(item, key) > 0);
    return keys.length ? keys.map(key => `${ITEM_STAT[key].name} +${itemNumber(key, itemValue(item, key))}`).join(' · ') : 'без бонусов';
  }
  function itemChanges(old, next) {
    return ITEM_KEYS.filter(key => itemValue(old, key) !== itemValue(next, key)).map(key => {
      const before = itemValue(old, key), after = itemValue(next, key);
      const delta = `${after > before ? '+' : '−'}${itemNumber(key, Math.abs(after - before))}`;
      return { key, before, after,
        text: `${ITEM_STAT[key].name}: ${itemNumber(key, before)} → ${itemNumber(key, after)} (${delta})` };
    });
  }
  function resourceYield(total) { return 2 + Math.max(0, Math.min(2, total - 2)); }
  function initialEquipment() {
    return ITEM_SLOTS.map(slot => ({ slot: slot.id, slotName: slot.name, icon: slot.icon,
      appearance: slot.appearance[0], prefixAnchor: -1, displayPrefix: -1, suffixIndex: 0,
      name: 'Стартовое: ' + slot.name.toLowerCase(),
      mods: slot.id === 'weapon' ? { weaponDamage: 2 } : slot.id === 'accessory'
        ? { maxHp: S.mode === 'pretzel' ? 0 : 10 }
        : { maxArmor: slot.id === 'armour' ? (hasFlaw('Slim') ? 1 : 2) : 1 } }));
  }
  function equipmentTotals() {
    const totals = {};
    for (const key of ITEM_KEYS) {
      const unit = ITEM_STAT[key].unit;
      totals[key] = Number((S.equipment.reduce((sum, item) => sum + Math.round(itemValue(item, key) / unit), 0) * unit).toFixed(2));
    }
    totals.health = S.mode === 'pretzel' ? 0 : totals.maxHp + 5 * totals.vitality;
    return totals;
  }
  // Recompute once per operation. Never subtract an old item and clamp before
  // adding its replacement: that loses overflow, armour deficit and HP.
  function updateEquipmentStats() {
    const next = equipmentTotals(), old = S.equipmentApplied || next, h = S.hero;
    const delta = key => Number((next[key] - old[key]).toFixed(2));
    h.weaponDamage = Math.max(0, h.weaponDamage + delta('weaponDamage'));
    h.maxArmor = Math.max(0, h.maxArmor + delta('maxArmor'));
    h.armor = Math.max(0, h.armor + delta('maxArmor'));
    const health = delta('health');
    h.maxHp = Math.max(1, h.maxHp + health);
    h.hp = health > 0 ? h.hp + health : Math.min(h.hp, h.maxHp);
    for (const key of ['strength', 'luck', 'dexterity', 'vitality']) h.stats[key] += delta(key);
    S.baseDamageBonus += delta('baseDamage');
    S.armorPerShield += delta('armorPerShield');
    for (const key of ['spikes', 'leech', 'weaponPoison', 'blunt', 'regen']) S[key] = Math.max(0, Number((S[key] + delta(key)).toFixed(2)));
    for (const key of ['xpPerSkull', 'goldPerCoin', 'upgradePerShield']) S[key] = 2 + next[key];
    S.pierce = Math.min(.95, Number(((hasFlaw('Cautious') ? .30 : .50) + next.pierce).toFixed(2)));
    S.durability = Math.min(MAX_DURABILITY, Number((BASE_DURABILITY + next.durability).toFixed(2)));
    const quicken = next.quicken > 0 ? 1 : 0;
    if (quicken !== S.quicken) {
      const shift = S.quicken - quicken;
      for (const id of S.skills) {
        S.skillDurations[id] = skillDuration(id) + shift;
        S.skillCooldowns[id] = Math.max(0, (S.skillCooldowns[id] || 0) + shift);
      }
      S.quicken = quicken;
    }
    S.equipmentApplied = next;
  }
  function equipItem(item) {
    const index = S.equipment.findIndex(old => old.slot === item.slot);
    if (index < 0) return false;
    const gear = { ...item, mods: { ...item.mods } };
    delete gear.scroll; delete gear.scrollBlocked;
    S.equipment[index] = { ...S.equipment[index], ...gear };
    updateEquipmentStats();
    return true;
  }
  function addItemModifier(item, key, amount) {
    const value = Math.max(0, Number((itemValue(item, key) + amount).toFixed(2)));
    if (value) item.mods[key] = value; else delete item.mods[key];
  }
  function itemStep(key, downgrade = false) {
    if (key === 'leech') {
      const total = Math.round(S.leech * 100);
      return (total < (downgrade ? 18 : 15) ? 5 : total < (downgrade ? 23 : 21) ? 3 : 2) / 100;
    }
    if (key === 'durability') {
      const total = Math.round(S.durability * 100);
      return (total < (downgrade ? 75 : 70) ? 10 : total < (downgrade ? 83 : 80) ? 5 : total < (downgrade ? 88 : 86) ? 3 : 2) / 100;
    }
    if (key === 'maxHp') return S.mode === 'pretzel' ? 0 : 10;
    if (key === 'weaponPoison') return 2;
    if (key === 'pierce') return .10;
    if (key === 'regen') return hasPerk('Faithful') ? 2 : 1;
    return ITEM_STAT[key]?.unit === .25 ? .25 : 1;
  }
  function itemUpgradeAllowed(key) {
    if (key === 'leech') return Math.round(S.leech * 100) < 25;
    if (key === 'pierce') return Math.round(S.pierce * 100) < 95;
    if (key === 'durability') return Math.round(S.durability * 100) < 90;
    if (key === 'maxHp') return S.mode !== 'pretzel';
    return !['goldPerCoin', 'xpPerSkull', 'upgradePerShield'].includes(key) || resourceYield(S[key]) < 4;
  }
  function randomItemUpgrade(slotId, allowQuicken = true) {
    let pool = itemSlotById(slotId).pool.filter(key => allowQuicken || key !== 'quicken');
    // This draw exists outside Sprint even if the slot never offered weaken.
    const weakenAllowed = S.mode !== 'sprint' && randInt(0, S.blunt, 4) === 0;
    pool = pool.filter(key => (key !== 'blunt' || weakenAllowed) && itemUpgradeAllowed(key));
    return pool[randInt(0, pool.length - 1, 4)];
  }
  function makeUpgradeOffer(slotId) {
    const item = S.equipment.find(entry => entry.slot === slotId);
    const key = randomItemUpgrade(slotId, !itemValue(item, 'quicken')), value = itemStep(key);
    return describeUpgradeOffer(slotId, key, value);
  }
  function describeUpgradeOffer(slotId, key, value) {
    const slot = itemSlotById(slotId), item = S.equipment.find(entry => entry.slot === slotId);
    const before = itemValue(item, key);
    return { slot: slot.id, slotName: slot.name, icon: slot.icon, key, value, name: ITEM_STAT[key].name,
      label: itemModText(key, value), desc: ITEM_STAT[key].desc,
      comparison: `${itemNumber(key, before)} → ${itemNumber(key, before + value)}` };
  }
  function applyUpgradeOffer(offer) {
    const item = S.equipment.find(entry => entry.slot === offer?.slot);
    // An upgrade only adds to this item. Trade-offs belong to shop generation;
    // reductions by Devourer use their separate path below.
    if (!item || !ITEM_KEYS.includes(offer.key) || !Number.isFinite(offer.value) || offer.value <= 0) return false;
    addItemModifier(item, offer.key, offer.value);
    updateEquipmentStats();
    return true;
  }
  function reduceItemModifier(item, key) {
    const amount = Math.min(itemValue(item, key), itemStep(key, true));
    addItemModifier(item, key, -amount);
    updateEquipmentStats();
    return amount;
  }
  function makeItem(slotId) {
    const slot = itemSlotById(slotId), old = S.equipment.find(item => item.slot === slotId);
    const appearance = randInt(...slot.appearance, 2), previous = old.prefixAnchor ?? -1;
    const displayPrefix = randInt(previous < 136 ? previous + 1 : previous, previous < 136 ? previous + 10 : 147, 2);
    const suffixCount = ITEM_NAME_POOLS.find(([end]) => appearance < end)[1];
    const suffixIndex = randInt(0, suffixCount - 1, 2);
    const names = ITEM_NAMES[slotId];
    const item = { slot: slotId, slotName: slot.name, icon: slot.icon, appearance,
      prefixAnchor: previous < 136 ? displayPrefix : previous, displayPrefix, suffixIndex,
      name: names[suffixIndex % names.length] + ' «' + ITEM_PREFIXES[Math.min(ITEM_PREFIXES.length - 1, Math.floor(displayPrefix / 18))] + '»', mods: {} };
    const roll = probability => random(2) < Math.fround(probability);
    const doubleArmour = slotId === 'armour' && roll(.2);
    item.mods[slot.main] = itemValue(old, slot.main) + (slotId === 'accessory' ? itemStep('maxHp') / 2 : doubleArmour ? 2 : 1);
    let helmetUpgraded = false;
    for (const key of ITEM_KEYS.filter(key => key !== slot.main && itemValue(old, key) > 0)) {
      const value = itemValue(old, key);
      const transfer = () => { item.mods[key] = Math.max(0, Number((value - itemStep(key, true)).toFixed(2))); };
      if (slotId === 'weapon' && roll(.15)) {
        transfer(); addItemModifier(item, 'weaponDamage', 1);
      } else if (slotId === 'helmet' && roll(.2)) {
        if (itemUpgradeAllowed(key) && !helmetUpgraded && roll(.5)) {
          item.mods[key] = Number((value + itemStep(key)).toFixed(2)); helmetUpgraded = true;
        } else { transfer(); addItemModifier(item, 'maxArmor', 1); }
      } else if (slotId === 'armour' && !doubleArmour && roll(.1)) {
        transfer(); addItemModifier(item, 'maxArmor', 1);
      } else if (slotId === 'shield' && roll(key === 'weaponDamage' ? .7 : .2)) {
        transfer(); addItemModifier(item, key !== 'weaponDamage' && roll(.2) ? 'weaponDamage' : 'maxArmor', 1);
      } else if (slotId === 'accessory' && roll(.1)) {
        transfer(); const chosen = randomItemUpgrade(slotId, false); addItemModifier(item, chosen, itemStep(chosen));
      } else item.mods[key] = value;
    }
    if (slotId === 'accessory' && (S.mode === 'pretzel' || roll(.5))) {
      const chosen = randomItemUpgrade(slotId, !itemValue(item, 'quicken')); addItemModifier(item, chosen, itemStep(chosen));
    }
    for (const key of ITEM_KEYS) if (!itemValue(item, key)) delete item.mods[key];
    return item;
  }
  function drawItemSlots(count, stream, create) {
    const slots = ITEM_SLOTS.map(slot => slot.id), offers = [];
    for (let n = 0; n < count; n++) {
      const [slot] = slots.splice(randInt(0, slots.length - 1, stream), 1);
      offers.push(create(slot));
    }
    return offers;
  }

  // ============================================================
  // КЛАССЫ И РАСЫ (классика: 10 мехов, каждый — Perk/Flaw/Race/Уникальный навык)
  // String IDs are our stable profile keys. Numeric native IDs order random
  // trophy unlocks independently of the presentation order in this picker.
  // ============================================================
  const CLASSES = [
    { id: 'adventurer', native: 0, name: 'Авантюрист', icon: '❔', race: 'Human', uniqueSkill: 'teleport', pool: ['steal', 'freeze', 'heal', 'repair', 'counter', 'shatter', 'goldtouch', 'teleport', 'explosive', 'elixir', 'enchant', 'slash', 'treasure', 'scavenge', 'disarm', 'dazzle', 'boostdmg', 'boostgold', 'boosthealth', 'boostarmour'],
      perk: '—', flaw: '—', raceName: 'Человек',
      perkDesc: 'нет пассивов', flawDesc: 'нет изъянов',
      clsDesc: 'Стартовый мех без пассива и изъяна; получает все открытые навыки',
      uniqueSkillDesc: 'Нет фиксированного: случайный выбор из всех открытых навыков' },
    { id: 'barbarian', native: 4, name: 'Варвар', icon: '🪓', race: 'Orc', uniqueSkill: 'berserk', pool: ['berserk', 'repair', 'counter', 'shatter', 'teleport', 'elixir', 'enchant', 'slash', 'treasure', 'scavenge', 'disarm', 'boostdmg', 'boosthealth', 'boostarmour', 'banish', 'exorcise', 'trap', 'biggame', 'magicsword', 'explosivearmour'],
      perk: 'Strong', flaw: 'Reckless', perkName: 'Сила', flawName: 'Безрассудство', raceName: 'Орк',
      perkDesc: '+1 базового урона', flawDesc: '-10% шанс бонусных щитов',
      clsDesc: 'пассив: Сила · изъян: Безрассудство · раса: Орк (оглушение 10%)',
      uniqueSkillDesc: 'Берсерк (14): убийство черепа даёт HP равный его атаке' },
    { id: 'mage', native: 5, name: 'Маг', icon: '🔮', race: 'Changeling', uniqueSkill: 'rune', pool: ['rune', 'freeze', 'heal', 'repair', 'goldtouch', 'teleport', 'explosive', 'elixir', 'enchant', 'treasure', 'dazzle', 'boostgold', 'boosthealth', 'banish', 'exorcise', 'chamber', 'magicsword', 'earthquake', 'fireball', 'mana'],
      perk: 'Gifted', flaw: 'Frail', perkName: 'Одарённость', flawName: 'Хрупкость', raceName: 'Изменник',
      perkDesc: '5% зелий выпадают как mana-зелья (−1 к кулдауну навыка)', flawDesc: '-10 макс. HP',
      clsDesc: 'пассив: Одарённость · изъян: Хрупкость · раса: Изменник (менятся)',
      uniqueSkillDesc: 'Руна (12): замкнутая цепочка из 4+ узлов собирает интерьер, кроме особых и боссов' },
    { id: 'ranger', native: 1, name: 'Разведчик', icon: '🏹', race: 'Elf', uniqueSkill: 'volley', pool: ['volley', 'heal', 'repair', 'counter', 'shatter', 'teleport', 'explosive', 'elixir', 'enchant', 'treasure', 'scavenge', 'disarm', 'boostdmg', 'boosthealth', 'banish', 'trap', 'biggame', 'chamber', 'magicsword', 'mana'],
      perk: 'Agile', flaw: 'Slim', perkName: 'Ловкость', flawName: 'Тощий', raceName: 'Эльф',
      perkDesc: '5% мечей выпадают стрелками (стрелять на 1 клетку)', flawDesc: '-1 броня',
      clsDesc: 'пассив: Ловкость · изъян: Тощий · раса: Эльф (медленный яд)',
      uniqueSkillDesc: 'Воллей (15): все мечи → стрелы (стрелы стреляют на 1 клетку)' },
    { id: 'rogue', native: 6, name: 'Вор', icon: '🗡', race: 'Sauren', uniqueSkill: 'greed', pool: ['greed', 'steal', 'heal', 'repair', 'counter', 'goldtouch', 'explosive', 'elixir', 'enchant', 'slash', 'treasure', 'scavenge', 'disarm', 'dazzle', 'boostgold', 'boosthealth', 'trap', 'biggame', 'chamber', 'magicsword'],
      perk: 'Frugal', flaw: 'Clumsy', perkName: 'Бережливость', flawName: 'Неловкость', raceName: 'Саурен',
      perkDesc: '+10% шанс бонусных монет', flawDesc: '3% щитов выпадают сломанными (дают 0)',
      clsDesc: 'пассив: Бережливость · изъян: Неловкость · раса: Саурен (50% шанс розыгрыша свитка)',
      uniqueSkillDesc: 'Жадность (25): убивает всех черепов рядом с монеткой' },
    { id: 'paladin', native: 7, name: 'Паладин', icon: '🛡', race: 'Dragonkin', uniqueSkill: 'laststand', pool: ['laststand', 'heal', 'repair', 'counter', 'shatter', 'explosive', 'elixir', 'slash', 'treasure', 'scavenge', 'disarm', 'boostdmg', 'boosthealth', 'boostarmour', 'banish', 'exorcise', 'biggame', 'chamber', 'magicsword', 'explosivearmour'],
      perk: 'Intimidating', flaw: 'Zealous', perkName: 'Устрашающий', flawName: 'Рьяный', raceName: 'Драконкин',
      perkDesc: 'вдвое реже срабатывает замена особого на смертного врага', flawDesc: 'черепа-монстры появляются на 1 ход раньше',
      clsDesc: 'пассив: Устрашающий · изъян: Рьяный · раса: Драконкин (огненное дыхание)',
      uniqueSkillDesc: 'Последний рубеж (35): полное лечение HP, если герой погибнет в этот ход' },
    { id: 'priest', native: 3, name: 'Жрец', icon: '✨', race: 'Faun', uniqueSkill: 'wish', pool: ['wish', 'freeze', 'heal', 'shatter', 'goldtouch', 'teleport', 'explosive', 'elixir', 'enchant', 'treasure', 'scavenge', 'dazzle', 'boosthealth', 'boostarmour', 'banish', 'exorcise', 'magicsword', 'earthquake', 'fireball', 'mana'],
      perk: 'Faithful', flaw: 'Cautious', perkName: 'Праведный', flawName: 'Осторожный', raceName: 'Фавн',
      perkDesc: 'улучшение регенерации даёт +2 вместо +1', flawDesc: '-20% пробития брони',
      clsDesc: 'пассив: Праведный · изъян: Осторожный · раса: Фавн (возрождение)',
      uniqueSkillDesc: 'Желание — одноразовый навык: собрать все клетки доски' },
    { id: 'raider', native: 2, name: 'Разбойник', icon: '🏴', race: 'Halfling', uniqueSkill: 'masochism', pool: ['masochism', 'heal', 'repair', 'counter', 'goldtouch', 'explosive', 'elixir', 'slash', 'treasure', 'dazzle', 'boostdmg', 'boostgold', 'boosthealth', 'boostarmour', 'exorcise', 'trap', 'biggame', 'chamber', 'magicsword', 'explosivearmour'],
      perk: 'Daring', flaw: 'Cocky', perkName: 'Дерзкий', flawName: 'Наглый', raceName: 'Халфлинг',
      perkDesc: '100% бонус с десятой клетки каждого типа', flawDesc: '3 узла: ресурсы убираются без награды; убийства дают опыт',
      clsDesc: 'пассив: Дерзкий · изъян: Наглый · раса: Халфлинг (храбрость)',
      uniqueSkillDesc: 'Мазохизм (15): превращает все монетки в черепов' },
    { id: 'warrior', native: 8, name: 'Воин', icon: '⚔️', race: 'Dwarf', uniqueSkill: 'charge', pool: ['charge', 'heal', 'repair', 'counter', 'shatter', 'goldtouch', 'teleport', 'enchant', 'slash', 'treasure', 'scavenge', 'disarm', 'dazzle', 'boostdmg', 'boostarmour', 'banish', 'trap', 'biggame', 'magicsword', 'explosivearmour'],
      perk: 'Tough', flaw: 'Noisy', perkName: 'Прочный', flawName: 'Громкий', raceName: 'Дворф',
      perkDesc: '+10 макс. HP', flawDesc: 'черепа-монстры +5% HP',
      clsDesc: 'пассив: Прочный · изъян: Громкий · раса: Дворф (верховая атака)',
      uniqueSkillDesc: 'Рывок (18): собирать все клетки по краю поля' },
    { id: 'assassin', native: 9, name: 'Ассасин', icon: '🗡', race: 'Werewolf', uniqueSkill: 'haste', pool: ['haste', 'steal', 'freeze', 'counter', 'shatter', 'teleport', 'explosive', 'elixir', 'treasure', 'disarm', 'dazzle', 'boostdmg', 'boosthealth', 'banish', 'trap', 'biggame', 'chamber', 'magicsword', 'explosivearmour', 'fireball'],
      perk: 'Cunning', flaw: 'Sneaky', perkName: 'Хитрый', flawName: 'Незаметный', raceName: 'Вервольф',
      perkDesc: '-1 к кулдауну 4-го навыка', flawDesc: '+1 HP у обычных черепов',
      raceDesc: 'Калечение: в конце хода собирает всех обычных черепов с HP ниже их собственной защиты; даёт опыт, не действует на особых и боссов.',
      clsDesc: 'пассив: Хитрый · изъян: Незаметный · раса: Вервольф (Калечение)',
      uniqueSkillDesc: 'Спешка (16): ставит на поле цепную клетку — она подходит к любому типу' },
  ];
  // Mortal enemy per race (original GetMortalEnemyType table).
  const MORTAL_BY_RACE = {
    Human: 0, Elf: 9, Dwarf: 14, Sauren: 13, Faun: 16,
    Orc: 12, Halfling: 5, Werewolf: 20, Dragonkin: 8, Changeling: 19,
  };
  const CLASS_BY_ID = Object.fromEntries(CLASSES.map(c => [c.id, c]));

  // --- уникальные навыки мехов (заполнение — после парраметров/мехов) ---
  // Телепорт/Затачивание/Мороз/... — в SKILLS; ниже — уникальные навыки мехов,
  // которые предлагаются в меню уровня; глобальный unlock — на ур.10 меха.
  const UNIQUE_SKILLS = [
    { id: 'berserk', native: 34, name: 'Берсерк', icon: '🪓', cooldown: 14, desc: 'Убийство черепа даёт HP равный его атаке' },
    { id: 'rune', native: 35, name: 'Руна', icon: '🔮', cooldown: 12, desc: 'Контур из 4+ узлов собирает ресурсы и обычных черепов внутри без урона. Особые и боссы остаются' },
    { id: 'greed', native: 36, name: 'Жадность', icon: '💀', cooldown: 25, desc: 'Убить всех черепов рядом с монеткой' },
    { id: 'laststand', native: 37, name: 'Последний рубеж', icon: '🛡', cooldown: 35, desc: 'Полное лечение HP, если герой погибнет в этот ход' },
    { id: 'wish', native: 33, name: 'Желание', icon: '✨', cooldown: 0, desc: 'Одноразовый навык: собрать все клетки доски' },
    { id: 'masochism', native: 32, name: 'Мазохизм', icon: '🏴', cooldown: 15, desc: 'Все монетки → черепа' },
    { id: 'charge', native: 38, name: 'Рывок', icon: '⚔️', cooldown: 18, desc: 'Сорвать все клетки по краю поля' },
    { id: 'haste', native: 39, name: 'Спешка', icon: '🗡', cooldown: 16, desc: 'Ставит цепную клетку: подходит к любому типу меток' },
    { id: 'volley', native: 31, name: 'Воллей', icon: '🏹', cooldown: 15, desc: 'Все мечи → стрелы (стрелы стреляют на 1 клетку)' },
    { id: 'shock', name: 'Шок', icon: '⚡', cooldown: 14, native: 40, nativeOnly: true, desc: 'Собрать щиты и монеты в верхней половине; с «Благоговением» — цепь' },
    { id: 'awe', name: 'Благоговение', icon: '🌀', cooldown: 14, native: 41, nativeOnly: true, desc: 'Собрать щиты и зелья в нижней половине; с «Шоком» — цепь' },
  ];
  const ALL_SKILLS = SKILLS.concat(UNIQUE_SKILLS);
  const SKILL_BY_ID = Object.fromEntries(ALL_SKILLS.map(s => [s.id, s]));

  // --- мех выбранный пользователем (пикер в начале забега) ---
  function loadStoredClassId() {
    try {
      const raw = profileStorage.getItem('raid:class');
      return (raw && classDefinition(raw)) ? raw : 'adventurer';
    } catch (e) { return 'adventurer'; }
  }
  // --- уровни мехов (персистентные, localStorage) ---
  const CLASS_LEVELS_KEY = 'raid:classLevels';
  let classLevels = loadClassLevels();
  function loadClassLevels() {
    try {
      const raw = profileStorage.getItem(CLASS_LEVELS_KEY);
      let obj = raw ? JSON.parse(raw) : {};
      if (!obj || typeof obj !== 'object' || Array.isArray(obj)) obj = {};
      // localStorage — внешние данные: чистим значения, иначе строка из него
      // попадёт в innerHTML карточки меха (и в арифметику levelUpClass).
      const clean = {};
      for (const id of Object.keys(obj)) {
        if (!classDefinition(id)) continue;
        const lvl = Number(obj[id]);
        if (Number.isFinite(lvl) && lvl >= 1) clean[id] = Math.min(10, Math.floor(lvl));
      }
      clean.adventurer = Math.max(1, clean.adventurer || 1);
      return clean;
    } catch (e) { return { adventurer: 1 }; }
  }
  function saveClassLevels() {
    try { profileStorage.setItem(CLASS_LEVELS_KEY, JSON.stringify(classLevels)); } catch (e) {}
  }
  function classLevelOf(id) { return (classLevels && classLevels[id]) || 0; }
  function isClassUnlocked(id) { return classLevelOf(id) >= 1; }
  function unlockClass(id) {
    if (!classDefinition(id)) return;
    classLevels[id] = Math.max(1, classLevelOf(id));
    saveClassLevels();
    syncClassAchievements();
  }
  function levelUpClass(id) {
    if (!classDefinition(id)) return;
    if (!isClassUnlocked(id)) classLevels[id] = 1;
    else classLevels[id] = Math.min(10, (classLevelOf(id) || 1) + 1);   // original cap: level 10
    saveClassLevels();
    syncClassAchievements();
  }

  let selectedClassId = loadStoredClassId();
  function getSelectedClassId() {
    return isClassUnlocked(selectedClassId) ? selectedClassId : 'adventurer';
  }
  function setSelectedClassId(id) {
    if (!classDefinition(id)) return;
    if (!isClassUnlocked(id)) return;
    selectedClassId = id;
    try { profileStorage.setItem('raid:class', id); } catch (e) {}
  }
  // --- persistent class setup; the live run owns a separate frozen snapshot ---
  const CLASS_CONFIG_KEY = 'raid:classConfigs';
  const CLASS_PAIR_LEVELS = [1, 3, 7, 9];
  const RACE_INFO = {
    Human: { name: 'Человек', desc: 'Без расового бонуса и особого смертного врага.' },
    Elf: { name: 'Эльф', desc: 'Яд на враге ослабляет его атаку. Смертный враг — Отравленный: пока он на поле, зелья не снимают яд героя.' },
    Dwarf: { name: 'Дворф', desc: 'Удар снизу вверх по вертикальной цепочке усиливается. Смертный враг — Хаотический: блокирует другие прямые удары цепочкой.' },
    Sauren: { name: 'Саурен', desc: 'Вдвое выше шанс розыгрыша свитка в магазине. Смертный враг — Вор: кража золота также ранит героя.' },
    Faun: { name: 'Фавн', desc: 'При повышении уровня поднимает HP хотя бы до 25% максимума. Смертный враг — Убийца: может убрать клетку под собой и спуститься.' },
    Orc: { name: 'Орк', desc: '10% шанс оглушить пережившего прямой удар врага. Смертный враг — Коррозия: появление обнуляет текущую броню героя.' },
    Halfling: { name: 'Халфлинг', desc: '15 прямых убийств одной цепочкой улучшают надетый доспех. Смертный враг — Невидимый: удваивает защиту других новых черепов.' },
    Werewolf: { name: 'Вервольф', desc: 'В конце хода собирает обычных черепов с HP ниже их защиты. Смертный враг — Вампир: может лечиться выше своего прежнего максимума.' },
    Dragonkin: { name: 'Драконкин', desc: 'Ручное применение навыка поджигает случайного обычного черепа. Смертный враг — Замораживающий: вдвое уменьшает базовую часть урона цепочки.' },
    Changeling: { name: 'Изменник', desc: 'Сбор особого врага заряжает случайный надетый навык. Смертный враг — Нестабильный: его телепорт исключает мечи вместо черепов.' },
  };
  const CLASS_TRAITS = {
    perks: Object.fromEntries([
      ['Unexpected', { name: 'Неожиданность', desc: 'Первые 5 ходов враги пропускают общий залп' }],
      ...CLASSES.filter(c => c.perk !== '—').map(c => [c.perk, { name: c.perkName, desc: c.perkDesc }]),
    ]),
    flaws: Object.fromEntries([
      ['Confused', { name: 'Растерянность', desc: 'Первые 5 ходов прямой урон цепочкой блокируется' }],
      ...CLASSES.filter(c => c.flaw !== '—').map(c => [c.flaw, { name: c.flawName, desc: c.flawDesc }]),
    ]),
  };
  function classDefinition(id) {
    return typeof id === 'string' && Object.hasOwn(CLASS_BY_ID, id) ? CLASS_BY_ID[id] : null;
  }
  function defaultClassConfig(id) {
    const cls = classDefinition(id);
    if (!cls) return null;
    return { classId: id, race: cls.race,
      perks: [cls.perk === '—' ? null : cls.perk, null, null, null],
      flaws: [cls.flaw === '—' ? null : cls.flaw, null, null, null],
      replacements: Array(20).fill(null) };
  }
  function classRules(id) {
    const level = classDefinition(id) ? classLevelOf(id) : 0;
    const editable = id !== 'adventurer' && level > 0;
    return { level, editable, pairSlots: CLASS_PAIR_LEVELS.map(at => editable && level >= at),
      canChangeRace: editable && level >= 5, replacementLimit: editable ? Math.floor(level / 2) * 2 : 0 };
  }
  function isRaceUnlocked(race) {
    if (typeof race !== 'string' || !Object.hasOwn(RACE_INFO, race)) return false;
    return race === 'Human' || CLASSES.some(c => c.race === race && classLevelOf(c.id) >= 5);
  }
  function isTraitUnlocked(kind, name) {
    if (!Object.hasOwn(CLASS_TRAITS, kind) || typeof name !== 'string' || !Object.hasOwn(CLASS_TRAITS[kind], name)) return false;
    return name === 'Unexpected' || name === 'Confused'
      || CLASSES.some(c => (kind === 'perks' ? c.perk : c.flaw) === name && isClassUnlocked(c.id));
  }
  function classEditorInfo(id) {
    const cls = classDefinition(id);
    if (!cls) return null;
    return { id, name: cls.name, ...classRules(id),
      baseSkills: id === 'adventurer'
        ? ALL_SKILLS.filter(s => s.native < 20).sort((a,b) => a.native-b.native).map(s => s.id) : [...cls.pool] };
  }
  // Loading is tolerant of external/old data. Whitelists, progression gates and
  // fixed slots are enforced here too, not just by disabled browser controls.
  function normaliseClassConfig(id, raw) {
    const config = defaultClassConfig(id), rules = classRules(id);
    if (!rules.editable || !raw || typeof raw !== 'object') return config;
    if (rules.canChangeRace && isRaceUnlocked(raw.race)) config.race = raw.race;
    for (const kind of ['flaws', 'perks']) {
      const seen = new Set(config[kind].filter(Boolean));
      for (let i = 1; i < 4; i++) {
        const value = Array.isArray(raw[kind]) ? raw[kind][i] : null;
        if (!rules.pairSlots[i] || !isTraitUnlocked(kind, value) || seen.has(value)) continue;
        if (kind === 'perks' && !config.flaws[i]) continue;
        config[kind][i] = value; seen.add(value);
      }
    }
    let replacements = 0;
    for (let i = 1; i < 20; i++) {
      const value = Array.isArray(raw.replacements) ? raw.replacements[i] : null;
      if (replacements >= rules.replacementLimit || value === CLASS_BY_ID[id].pool[i] || !isSkillUnlocked(value)) continue;
      config.replacements[i] = value; replacements++;
    }
    return config;
  }
  function loadClassConfigs() {
    const configs = {};
    try {
      const raw = JSON.parse(profileStorage.getItem(CLASS_CONFIG_KEY));
      if (raw?.version !== 1 || !raw.classes || typeof raw.classes !== 'object' || Array.isArray(raw.classes)) return configs;
      for (const cls of CLASSES) if (Object.hasOwn(raw.classes, cls.id)) configs[cls.id] = normaliseClassConfig(cls.id, raw.classes[cls.id]);
    } catch (e) { /* A broken or unavailable local profile must not block play. */ }
    return configs;
  }
  let classConfigs = loadClassConfigs();
  function getClassConfig(id) {
    return classDefinition(id) ? normaliseClassConfig(id, classConfigs[id]) : null;
  }
  function saveClassConfig(id, raw) {
    if (!classDefinition(id) || !classRules(id).editable || !raw || typeof raw !== 'object') return false;
    const next = { ...classConfigs, [id]: normaliseClassConfig(id, raw) };
    try { profileStorage.setItem(CLASS_CONFIG_KEY, JSON.stringify({ version: 1, classes: next })); }
    catch (e) { return false; }
    classConfigs = next;
    return true;
  }
  function runClassConfig(state = S) {
    return state?.classConfig || defaultClassConfig(state?.classId || 'adventurer');
  }
  function hasPerk(name, state = S) { return typeof name === 'string' && name !== '—' && !!runClassConfig(state)?.perks.includes(name); }
  function hasFlaw(name, state = S) { return typeof name === 'string' && name !== '—' && !!runClassConfig(state)?.flaws.includes(name); }
  function clsRace(state = S) { return runClassConfig(state)?.race || 'Human'; }
  function classSkillPool(state = S) {
    if (state.classId === 'adventurer') return ALL_SKILLS.filter(s => isSkillUnlocked(s.id)).sort((a,b) => a.native-b.native).map(s => s.id);
    const config = runClassConfig(state), base = CLASS_BY_ID[state.classId].pool;
    return [...new Set(base.map((id, i) => config.replacements[i] || id))].sort((a,b) => SKILL_BY_ID[a].native-SKILL_BY_ID[b].native);
  }

  // concept-art sprites from the catalog (window.SKULL_ART injected by the build)
  const SKULL_ART = (typeof window !== 'undefined' && window.SKULL_ART) || {};

  const RAID_MONSTER_ART = (typeof window !== 'undefined' && window.RAID_MONSTER_ART) || {};
  const RAID_REGIONS = {
    earth: { title: 'Забытый лес', element: 'Земля', index: '03' },
    fire: { title: 'Пылающая кузня', element: 'Огонь', index: '01' },
    air: { title: 'Небесный разлом', element: 'Воздух', index: '02' },
    water: { title: 'Затопленный храм', element: 'Вода', index: '04' },
  };
  const RAID_PORTRAITS = [
    ['fire_spark', 'fire_crab', 'fire_demon', 'fire_dragon'],
    ['air_spirit', 'air_storm', 'air_griffin', 'air_dragon'],
    ['earth_golem', 'earth_ent', 'earth_worm', 'earth_crystal'],
    ['water_elemental', 'water_octopus', 'water_angler', 'water_dragon'],
  ];

  // Hand-drawn interface glyphs. These replace presentation only: the original
  // skill/item objects (including saved icon strings), IDs and effects stay intact.
  const RAID_MODULE_SHAPES = {
    portal: '<ellipse cx="16" cy="16" rx="8" ry="12"/><ellipse cx="16" cy="16" rx="4" ry="8"/><path d="m22 7 5 2-2-5M5 23l5 2-2-5"/>',
    frost: '<path d="M16 3v26M5 9l22 14M5 23 27 9M12 6l4 4 4-4m-8 20 4-4 4 4M5 14l6-1-2-6m14 18-2-6 6-1M9 25l2-6-6-1m22-4-6-1 2-6"/>',
    health: '<path d="M16 27 5 17C-4 8 9 1 16 10 23 1 36 8 27 17Z"/><path d="M16 12v10m-5-5h10"/>',
    shield: '<path d="m16 3 11 4v10c0 6-7 11-11 13C12 28 5 23 5 17V7Z"/><path d="M10 12h12m-6-5v17m-6-7h12"/>',
    counter: '<path d="M26 12A11 11 0 0 0 7 7l-3 4m0-7v7h7m-5 9a11 11 0 0 0 19 5l3-4m0 7v-7h-7M11 16h10"/>',
    shatter: '<path d="m9 5 13 1 5 10-5 11-15-2-3-11Z"/><path d="m17 5-4 9 7 3-4 11M4 15l9-1m7 3 7-1"/>',
    coin: '<circle cx="16" cy="16" r="12"/><circle cx="16" cy="16" r="8"/><path d="m16 10 4 6-4 6-4-6Z"/>',
    sword: '<path d="m20 4 8-1-1 8-14 14-6-6ZM7 16l9 9m-9-4-4 5 3 3 5-4M24 7 11 20"/>',
    flame: '<path d="M18 2c4 11 14 14 8 23-7 10-24 3-19-9 2 4 5 3 6 0 2-5-1-8 5-14Z"/><path d="M17 16c1 5 8 6 3 11-5 4-10-2-7-5Z"/>',
    flask: '<path d="M11 3h10v5H11Zm2 5v7L5 26c-2 5 24 5 22 0l-8-11V8M9 22h14M15 17l2 3"/>',
    crystal: '<path d="m16 2 10 8-2 13-8 7-8-7-2-13Zm0 0-5 11 5 17 5-17ZM6 10l5 3h10l5-3"/>',
    banish: '<circle cx="16" cy="16" r="11"/><path d="m8 8 16 16M3 16h4m18 0h4M16 3v4m0 18v4"/>',
    trap: '<path d="M4 21 7 8l5 9 4-10 4 10 5-9 3 13M4 21l12 7 12-7M9 18l7 4 7-4"/>',
    target: '<circle cx="16" cy="16" r="10"/><circle cx="16" cy="16" r="4"/><path d="M16 1v8m0 14v8M1 16h8m14 0h8"/>',
    axes: '<path d="M7 3 28 28M25 3 4 28M4 6l4-4 9 9-6 6-7-3Zm24 0-4-4-9 9 6 6 7-3Z"/>',
    rune: '<path d="m16 3 13 24H3ZM16 10l7 13H9Zm0-7v7M3 27l6-4m20 4-6-4"/><circle cx="16" cy="18" r="2"/>',
    star: '<path d="m16 2 4 9 10 1-8 7 3 10-9-5-9 5 3-10-8-7 10-1Z"/><path d="M16 10v10m-4-5h8"/>',
    speed: '<path d="m3 6 10 10L3 26M13 6l10 10-10 10M25 6h4v20h-4"/>',
    volley: '<path d="M7 28V10m-4 4 4-4 4 4m5 10V3m-4 4 4-4 4 4m5 21V10m-4 4 4-4 4 4"/>',
    bolt: '<path d="M18 2 6 18h9l-1 12 12-18h-9Z"/>',
  };
  const RAID_MODULE_ART = {
    steal: 'coin', teleport: 'portal', freeze: 'frost', heal: 'health', repair: 'shield',
    counter: 'counter', shatter: 'shatter', goldtouch: 'coin', explosive: 'flame',
    elixir: 'flask', enchant: 'crystal', slash: 'sword', treasure: 'coin',
    scavenge: 'trap', disarm: 'sword', dazzle: 'star', boostdmg: 'sword',
    boostgold: 'coin', boosthealth: 'health', boostarmour: 'shield', banish: 'banish',
    exorcise: 'rune', trap: 'trap', biggame: 'target', magicsword: 'crystal',
    explosivearmour: 'shield', fireball: 'flame', mana: 'flask',
    chamber: 'coin', earthquake: 'shatter', hack: 'axes', berserk: 'axes', rune: 'rune',
    greed: 'coin', laststand: 'shield', wish: 'star', masochism: 'bolt',
    charge: 'speed', haste: 'counter', volley: 'volley', shock: 'bolt', awe: 'star',
  };
  function raidModuleIcon(id) {
    const skill = SKILL_BY_ID[id];
    const shape = RAID_MODULE_SHAPES[RAID_MODULE_ART[id] || 'crystal'];
    const orbit = skill?.native % 2 ? '<path d="M2 2h5M2 2v5m28 18v5h-5" opacity=".4"/>' : '';
    return `<svg class="module-glyph" viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${shape}${orbit}</svg>`;
  }
  const RAID_EQUIPMENT_SHAPES = {
    weapon: '<path d="m22 4 6-1-1 6-14 15-6-6Z" fill="#a4dbcf"/><path d="m7 16 10 10m-8-5-6 6 3 3 6-6" stroke="#e2c78a" stroke-width="3"/>',
    helmet: '<path d="M5 27V14C5-2 27-2 27 14v13l-8 3v-9h-6v9Z" fill="#758e83"/><path d="m7 12 9 4 9-4-3 8H10Z" fill="#102c2c"/><path d="M16 3v8m-7 16 2-4m12 4-2-4"/>',
    armour: '<path d="M11 3h10l7 5-4 8-3-2v15H11V14l-3 2-4-8Z" fill="#566f64"/><path d="m11 3 5 6 5-6M9 12l7 5 7-5m-12 9h10m-10 5h10"/><path d="m16 11 4 5-4 6-4-6Z" fill="#9fd7be"/>',
    shield: '<path d="m16 2 12 5v11c0 5-7 10-12 13C11 28 4 23 4 18V7Z" fill="#526e65"/><path d="m16 6 8 3v9c0 4-5 7-8 9-3-2-8-5-8-9V9Z" fill="#163d3c"/><path d="m16 10 5 7-5 7-5-7Z" fill="#a8d9c1"/>',
    accessory: '<path d="M6 3c0 12 20 12 20 0M13 12h6v5h-6Z" fill="none"/><path d="m16 15 9 7-9 9-9-9Z" fill="#a1aa75"/><path d="m16 19 4 4-4 4-4-4Z" fill="#b6e2c5"/>',
  };
  function raidItemIcon(slot) {
    return `<svg class="equipment-glyph" viewBox="0 0 32 34" fill="none" stroke="#c7b57e" stroke-width="1.2" stroke-linejoin="round" aria-hidden="true">${RAID_EQUIPMENT_SHAPES[slot] || RAID_EQUIPMENT_SHAPES.shield}</svg>`;
  }
  function raidAchievementAvailable(id) { return ![45, 70, 71, 76, 77].includes(id); }

  let RAID_REGION = 'earth';
  function raidPortraitKey(skull) {
    // Deterministic visual assignment only. No element is added to the enemy
    // or sword; no distribution, damage, status, loot or RNG draw changes.
    const family = Math.abs((skull.uid || 1) - 1) % 4;
    const role = skull.boss ? 3 : skull.special ? 1 + Math.abs(skull.type || 0) % 2 : 0;
    return RAID_PORTRAITS[family][role];
  }
  function raidPortrait(skull) { return RAID_MONSTER_ART[raidPortraitKey(skull)] || SKULL_ART[skull.art] || ''; }
  function applyRaidRegion(region) {
    if (!Object.hasOwn(RAID_REGIONS, region)) return false;
    RAID_REGION = region;
    if (document.documentElement) document.documentElement.dataset.raidRegion = region;
    const data = RAID_REGIONS[region];
    for (const node of document.querySelectorAll('[data-raid-region-title]')) node.textContent = data.title;
    for (const node of document.querySelectorAll('[data-raid-region-caption]')) node.textContent = data.index + ' / ' + data.element;
    for (const button of document.querySelectorAll('button[data-raid-region]')) button.setAttribute('aria-pressed', String(button.dataset.raidRegion === region));
    return true;
  }
  function loadRaidAppearance() {
    let region = 'earth';
    try { const saved = profileStorage.getItem('raid:appearance'); if (Object.hasOwn(RAID_REGIONS, saved)) region = saved; } catch (ignored) { /* UI remains available without persistence. */ }
    applyRaidRegion(region);
  }
  function initRaidAppearance() {
    loadRaidAppearance();
    for (const button of document.querySelectorAll('button[data-raid-region]')) button.addEventListener('click', () => {
      if (!applyRaidRegion(button.dataset.raidRegion)) return;
      try { profileStorage.setItem('raid:appearance', RAID_REGION); } catch (ignored) { /* profileStorage already shows the identity warning. */ }
    });
  }
  function mechEmblem(native) {
    const color = ['#76e2c0', '#92c9ec', '#c7a0dc', '#e4bc6c', '#e98c71'][native % 5];
    return `<svg class="mech-emblem" viewBox="0 0 96 112" aria-hidden="true"><circle cx="48" cy="53" r="40" fill="#0c2024" stroke="#8c794f" stroke-width=".6"/><circle cx="48" cy="53" r="33" fill="none" stroke="${color}" stroke-opacity=".22" stroke-dasharray="2 5"/><path d="M29 15 38 10h20l9 5-2 24-17 9-17-9Z" fill="#758d87" stroke="#101e20" stroke-width="3"/><path d="m33 20 15 4 15-4-3 10-12 7-12-7Z" fill="#183133"/><path d="m37 24 11 2 11-2-2 4-9 4-9-4Z" fill="${color}"/><path d="m32 40-13 5-8 20 15 6 8-10 14 8 14-8 8 10 15-6-8-20-13-5-16 9Z" fill="#495f5a" stroke="#101e20" stroke-width="3"/><path d="m34 47 14 9 14-9 3 25-17 11-17-11Z" fill="#a29c78" stroke="#172726" stroke-width="2"/><path d="m48 55 8 8-8 9-8-9Z" fill="${color}"/><path d="M20 50 14 63l11 2 6-10M76 50l6 13-11 2-6-10" fill="#9a936f"/><path d="m31 76 14 8-3 16-16 3 1-17Zm34 0-14 8 3 16 16 3-1-17Z" fill="#4a625c" stroke="#101e20" stroke-width="3"/><path d="m29 90 11 1-2 7-10 1m39-9-11 1 2 7 10 1" stroke="#b3aa81" stroke-width="2"/><path d="M48 4v5m0 94v5M3 53h5m80 0h5" stroke="${color}" stroke-width="1.5"/></svg>`;
  }

  // Рядовой череп — одна внешность; спец-черепы — другие (свои арты); босс — boss_lich
  const REGULAR_SKULL_KEY = 'e1_ghost';
  const SPECIAL_SKULL_KEYS = ['e2_cult', 'e3_beholder', 'e4_plasma', 'e5_shadow'];
  const BOSS_KEY = 'boss_lich';

  let SKULL_UID = 1;
  const SKULL_IMAGES = new Map(); // uid -> cached <img> (same sprite never re-created)

  let S = null;
  // Matching creates resource events; arrival is a separate, ordered pass.
  // Nested blasts share the batch, so a physical cell can only pay once.
  let RESOURCE_BATCH = null;
  let AFTER_ARRIVALS = null; // serialisable next step, never a closure
  let SCHEDULED_STEP = null;
  let ACTION_TIMER = null;
  let DRAINING_ARRIVALS = false;
  // Идентификатор забега: отложенные setTimeout резолва/enemy phase не должны
  // попадать в новый забег после рестарта (см. newGame + resolveChain).
  let RUN_ID = 0;
  let SCROLL_LABEL_TIMER = null;
  let SCROLL_LABEL_DEADLINE = 0;

  // --- DOM helpers ---------------------------------------------------------
  const el = id => document.getElementById(id);
  function qs(sel, root) { return (root || document).querySelector(sel); }
  function qsa(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

  // --- helpers -------------------------------------------------------------
  // Android-compatible six-stream RNG (RandType 0..5), seedable through RAID.newGame(seed).
  const RNG_SEEDS = new Uint32Array(6);
  function seedRandom(seed) {
    for (let i = 0; i < RNG_SEEDS.length; i++) RNG_SEEDS[i] = Number(seed) >>> 0;
    // Start() calls FlushRandSeeds: advance each independent stream once.
    for (let i = 0; i < RNG_SEEDS.length; i++) RNG_SEEDS[i] = Math.imul(RNG_SEEDS[i], 16807) >>> 0;
  }
  function random(stream = 1) {
    const k = Math.max(0, Math.min(5, stream | 0));
    const seed = RNG_SEEDS[k] = Math.imul(RNG_SEEDS[k], 16807) >>> 0;
    if (S && S.rngSeeds) S.rngSeeds[k] = seed;
    return (seed >>> 10) / 4194304;
  }
  function randInt(min, max, stream = 1) {
    const lo = Math.min(min, max);
    const span = Math.fround(Math.abs(max - min) + 1);
    return Math.floor(Math.fround(lo + Math.fround(span * random(stream))));
  }
  function chance(p, stream = 5) { return random(stream) < p; }
  function isAdjacent(i, j) {
    const r1 = Math.floor(i / COLS), c1 = i % COLS;
    const r2 = Math.floor(j / COLS), c2 = j % COLS;
    const dr = Math.abs(r1 - r2), dc = Math.abs(c1 - c2);
    return dr <= 1 && dc <= 1 && !(dr === 0 && dc === 0);
  }
  function randomEmptyCells() {
    return S.board.map((c, i) => c.type === 'empty' || c.type === 'skull' ? i : -1).filter(i => i >= 0);
  }

  // --- state ----------------------------------------------------------------
  function newGameState() {
    return {
      board: [],
      classId: 'adventurer',       // текущий мех забега (см. CLASSES)
      classConfig: null,           // immutable next-run configuration snapshot
      historyId: '', pretzelStats: emptyPretzelStats(), achievementRun: emptyAchievementRun(),
      seed: 0, rngSeeds: [],
      mode: 'classic',             // classic | sprint | pretzel
      difficulty: 1,               // 0 Easy, 1 Normal, 2 Hard, 3 Harder
      skillLevels: {},             // skill id -> level (Sprint starts at 5)
      won: false,
      hero: {
        hp: START_HP,
        maxHp: START_HP,
        armor: START_MAX_ARMOR,
        maxArmor: START_MAX_ARMOR,
        weaponDamage: 2,
        level: 1,
        xp: 0,
        xpNext: 100,
        xpLevelCum: 0,              // cumulative XP used by score and enemy growth
        gold: 0,
        goldBox: 0,
        stats: { strength: 1, vitality: 1, luck: 1, dexterity: 1, charisma: 0, damage: 0, defense: 0 },
      },
      equipment: [], equipmentApplied: null,
      skillDurations: {}, nextTurnFlags: {}, menuCandidates: [], levelDraft: [],
      compatibilityNotices: [], scrollNativeWords: null,
      scrollPolicy: 'classic-memory-v1',
      scrollLeechLabel: { version: 1, number: 0, active: false, inFlight: false },
      refillFilter: { enabled: false, excludeSwords: false },
      skills: [],                  // active skill ids (class-unique + chosen in level-ups)
      skillCooldowns: {},         // id -> turns remaining (cooldowns may be reduced by Quicken)
      pierce: BASE_PIERCING,        // вероятность пробития брони черепа
      durability: BASE_DURABILITY,  // вероятность, что пункт брони героя не списывается
      regen: 0, blunt: 0, leech: 0, spikes: 0, quicken: 0, weaponPoison: 0,
      goldPerCoin: GOLD_PER_COIN,   // золото за монету (base + boosts)
      armorPerShield: HP_PER_SHIELD_ARMOR, // бронь за щит (base + dexterity + defense×2 + repairs)
      upgradePerShield: SHIELD_UPGRADE_POINTS, // очки upgrade bar за щит
      xpPerSkull: XP_PER_SKULL,     // опыт за черепа (base + boost XP)
      freezeNextTurn: 0, stealActive: 0,
      // Skill flags (native c234..c23b / 1433d/e / 15e72..73).
      swordBoost: 0, shieldBoost: 0, potionBoost: 0, coinBoost: 0,
      counterattack: 0, oneHealthSkulls: 0, forceCoins: 0,
      slashCombo: 0, hackCombo: 0, shockCombo: 0, aweCombo: 0,
      berserkActive: 0,            // лекарство от этого хода (Barbarian perk)
      cooldownAdjust: {},          // skill id -> extra cooldown (−1 для Cunning)
      runeActive: 0,
      runeUsedThisTurn: 0,         // замкнутая цепочка собрана этим ходом
      runeCollected: 0,            // замкнутая цепочка уже собрана
      lastStand: 0,                // полное лечение при смерти этого хода
      lastStandUsed: 0,            // Last Stand уже использован за забег
      brokenShields: 0,            // сколько щитов выпало сломанными (rogue flaw)
      baseDamageBonus: 0,           // equipment stat1: flat chain-damage bonus
      specialsPromoted: 0,
      specialsKilled: 0,            // total rewarded special collections this run
      unlocksThisRun: 0,          // claimed unlocks, not the trophy-generation limit
      trophiesThisRun: 0,         // сколько трофеев забрано за забег
      trophiesGenerated: 0,     // includes unclaimed/lost board trophies (limit 3)
      upgradeBar: 0,
      resourceCarry: { xp: 0, gold: 0, upgrade: 0 },
      collectionCounts: {},       // counts reset by MatchTiles/collection stages, not each token
      arrivals: [],               // ordered resource/healing/damage events, paused by menus
      rewardMenus: [],
      actionCommitted: false,
      turn: 0,
      skullsKilled: 0,
      swordsHits: 0,
      goldEarned: 0,
      upgradeEarned: 0,          // всего очков апгрейда за забег (для счёта)
      pretzelScore: 0,           // очки режима «Крендель» за забег
      potionsUsed: 0,
      chainCount: 0,
      maxLevel: 1,
      log: [],
      over: false,
      spawnsDone: 0,
      bossTurn: BOSS_EVERY_TURNS,
      // Original promotion counter: a new skull becomes a special/boss when
      // this reaches 0.  Starts at 50 (Zealous 49).
      promotionRemaining: 50,
      trophyRemaining: 0,      // persisted countdown, not a per-run kill target
      selected: [],
      chainType: null,
      pendingModal: null,         // null | 'level' | 'armor' | 'gold' | 'gameover'
      resolving: false,
      lastKillCount: 0,           // статистика цепочки (для Guts/Volley/Uppercut)
      killsThisTurn: 0,            // убийств за текущий ход (Vengeful)
      poison: 0,                   // урон длительного яда (Poisonous)
      usedSkillIds: {},            // одноразовые навыки (Wish) — не предлагать повторно
      _runePathSet: null,          // цепочка для uppercut / Armoured
      _chainOrder: null,           // упорядоченный путь для направления атаки Armoured
      _lastChainCells: null,       // клетки цепочки для Burn-тайля
    };
  }

  // --- local active-run protocol (not an Android binary import) -------------
  // Only complete JS task boundaries are written. Timed actions and reward
  // continuations have named steps; choices and result identities are data.
  const RUN_SAVE_KEY = 'raid:activeRun';
  const RUN_SAVE_LIMIT = 512 * 1024;
  const REWARD_KINDS = ['level', 'gold', 'armor'];
  const RUN_SAVE_SKIP = new Set(['selected', 'chainType', 'pendingModal', 'menuCandidates', 'levelDraft',
    'arrivals', 'rngSeeds', '_runePathSet', '_chainOrder', '_lastChainCells']);
  let RUN_SAVE_BASE;
  let SAVED_RUN = null;
  let FINISHED_RECORD = null;
  let SAVE_TIMER = null;
  let SAVE_CONFLICT = false;
  let SAVE_REVISION = 0;
  let NEW_RUN_REQUEST = null;
  let RUN_SAVE_STATUS = { state: 'empty', message: 'Сохранённого забега пока нет.' };

  function saveCheck(ok) { if (!ok) throw new Error('Invalid local run'); }
  function saveNumber(value, min = 0, max = 1e12, integer = false) {
    saveCheck(typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max && (!integer || Number.isInteger(value)));
    return value;
  }
  function saveObject(value, keys, required = keys) {
    saveCheck(value && typeof value === 'object' && !Array.isArray(value));
    saveCheck(Object.keys(value).every(key => keys.includes(key)) && required.every(key => Object.hasOwn(value, key)));
    return value;
  }
  function saveText(value, max = 300) {
    saveCheck(typeof value === 'string' && value.length <= max && !/[<>&\u0000-\u001f]/u.test(value));
    return value;
  }
  function saveArray(value, max, min = 0) { saveCheck(Array.isArray(value) && value.length >= min && value.length <= max); return value; }
  function saveBool(value) { saveCheck(typeof value === 'boolean'); return value; }
  function saveMap(value, keys, min = 0, max = 1e12, integer = false) {
    saveObject(value, keys, []);
    for (const n of Object.values(value)) saveNumber(n, min, max, integer);
    return value;
  }
  function saveConfig(value, classId) {
    saveObject(value, ['classId', 'race', 'perks', 'flaws', 'replacements']);
    saveCheck(value.classId === classId && Object.hasOwn(RACE_INFO, value.race));
    const base = defaultClassConfig(classId);
    for (const kind of ['perks', 'flaws']) {
      saveArray(value[kind], 4, 4);
      saveCheck(value[kind][0] === base[kind][0]);
      const used = value[kind].filter(name => name !== null);
      saveCheck(used.every(name => typeof name === 'string' && Object.hasOwn(CLASS_TRAITS[kind], name)) && new Set(used).size === used.length);
    }
    saveCheck(value.perks.every((perk, i) => perk === null || value.flaws[i] !== null));
    saveArray(value.replacements, 20, 20);
    saveCheck(value.replacements[0] === null && value.replacements.every(id => id === null || typeof id === 'string' && Object.hasOwn(SKILL_BY_ID, id) && !SKILL_BY_ID[id].nativeOnly));
    if (classId === 'adventurer') saveCheck(value.race === base.race && ['perks', 'flaws', 'replacements'].every(key => value[key].every(entry => entry === null)));
    // Do not revalidate against today's donor levels/editor. This is the
    // already-started run's frozen configuration, not next-run preferences.
    return value;
  }
  function savedSkill(option) {
    return { kind: 'skill', id: option.id, duration: option.duration, charge: option.charge, level: option.level, upgrade: !!option.upgrade };
  }
  function readSavedSkill(value) {
    saveObject(value, ['kind', 'id', 'duration', 'charge', 'level', 'upgrade']);
    saveCheck(value.kind === 'skill' && typeof value.id === 'string' && Object.hasOwn(SKILL_BY_ID, value.id));
    saveNumber(value.duration, -64, 100000, true); saveNumber(value.charge, -64, 100000, true);
    saveNumber(value.level, 1, 10, true); saveBool(value.upgrade);
    return value;
  }
  function savedItem(item) {
    const value = {};
    for (const key of ['slot', 'appearance', 'prefixAnchor', 'displayPrefix', 'suffixIndex', 'name', 'mods']) value[key] = item[key];
    if (item.scroll) value.scroll = savedSkill(item.scroll);
    if (item.scrollBlocked) value.scrollBlocked = true;
    return value;
  }
  function readSavedItem(value, offer = false) {
    saveObject(value, ['slot', 'appearance', 'prefixAnchor', 'displayPrefix', 'suffixIndex', 'name', 'mods', ...(offer ? ['scroll', 'scrollBlocked'] : [])],
      ['slot', 'appearance', 'prefixAnchor', 'displayPrefix', 'suffixIndex', 'name', 'mods']);
    const slot = itemSlotById(value.slot); saveCheck(!!slot);
    saveNumber(value.appearance, slot.appearance[0], slot.appearance[1], true);
    for (const key of ['prefixAnchor', 'displayPrefix', 'suffixIndex']) saveNumber(value[key], -1, 10000, true);
    saveText(value.name, 160); saveMap(value.mods, ITEM_KEYS);
    if (Object.hasOwn(value, 'scroll')) { readSavedSkill(value.scroll); saveCheck(!value.scroll.upgrade); }
    if (Object.hasOwn(value, 'scrollBlocked')) saveBool(value.scrollBlocked);
    saveCheck(!(value.scroll && value.scrollBlocked));
    return value;
  }
  function readSavedCell(cell) {
    saveObject(cell, ['type', 'skull', 'broken', 'mana', 'acid', 'explosive', 'magicSword', 'arrow', 'frozen', 'burn', 'fireDamage', 'fireSafe', 'stunned', 'frozenTurn', 'matched', 'appearance'], ['type']);
    saveCheck(['potion', 'skull', 'coin', 'shield', 'sword', 'arrow', 'chain', 'trophy'].includes(cell.type));
    for (const [key, value] of Object.entries(cell)) {
      if (key === 'type' || key === 'skull') continue;
      if (key === 'frozenTurn') saveNumber(value, -1, 1e12, true);
      else if (key === 'burn' && typeof value === 'number' || ['fireDamage', 'appearance'].includes(key)) saveNumber(value);
      else saveBool(value);
    }
    if (cell.type !== 'skull') { saveCheck(!Object.hasOwn(cell, 'skull')); return cell; }
    const skull = cell.skull;
    const required = ['boss', 'special', 'type', 'countdown', 'uid', 'art', 'atk', 'def', 'hp', 'maxHp', 'promotionInterval', 'enteredTurn', 'poison'];
    saveObject(skull, [...required, 'burn', 'fireDamage', 'fireSafe', 'stunned', 'frozenTurn', 'phylactery', 'lichDelay', 'promotedByBigGame'], required);
    for (const key of ['boss', 'special', 'fireSafe', 'stunned', 'phylactery', 'lichDelay', 'promotedByBigGame']) if (Object.hasOwn(skull, key)) saveBool(skull[key]);
    for (const key of ['atk', 'def', 'maxHp', 'fireDamage']) if (Object.hasOwn(skull, key)) saveNumber(skull[key]);
    saveNumber(skull.hp, 1); saveNumber(skull.uid, 1, 1e12, true); saveNumber(skull.type, 0, 32, true);
    for (const key of ['countdown', 'enteredTurn', 'frozenTurn']) if (Object.hasOwn(skull, key)) saveNumber(skull[key], -1, 1e12, true);
    if (skull.promotionInterval !== null) saveNumber(skull.promotionInterval, 0, 1e12, true);
    if (Object.hasOwn(skull, 'burn')) typeof skull.burn === 'boolean' ? saveBool(skull.burn) : saveNumber(skull.burn);
    saveCheck([REGULAR_SKULL_KEY, ...SPECIAL_SKULL_KEYS, BOSS_KEY].includes(skull.art));
    saveArray(skull.poison, 3, 3).forEach(n => saveNumber(n));
    return cell;
  }
  function readSavedMenu(menu) {
    if (menu === null) return null;
    saveObject(menu, ['kind', 'choices', 'picked']); saveCheck(REWARD_KINDS.includes(menu.kind));
    const count = menu.kind === 'gold' ? 3 : 4;
    saveArray(menu.choices, count, count);
    for (const option of menu.choices) {
      if (menu.kind === 'gold') readSavedItem(option, true);
      else if (menu.kind === 'armor') {
        saveObject(option, ['slot', 'key', 'value']);
        saveCheck(!!itemSlotById(option.slot) && itemSlotById(option.slot).pool.includes(option.key));
        saveNumber(option.value, Number.MIN_VALUE);
      } else if (option?.kind === 'stat') {
        saveObject(option, ['kind', 'id']); saveCheck(STATS.some(stat => stat.id === option.id));
      } else readSavedSkill(option);
    }
    saveArray(menu.picked, menu.kind === 'level' ? 2 : 0);
    menu.picked.forEach(n => saveNumber(n, 0, count - 1, true));
    saveCheck(new Set(menu.picked).size === menu.picked.length);
    return menu;
  }
  function readSavedState(data, legacyId) {
    const state = newGameState(), keys = Object.keys(state).filter(key => !RUN_SAVE_SKIP.has(key));
    saveCheck(data && typeof data === 'object' && !Array.isArray(data));
    // Compatible extension of released v1: keep the run and RNG; never invent
    // an average for a Pretzel run that already scored before statistics.
    if (!Object.hasOwn(data, 'historyId')) data.historyId = legacyId;
    // Pre-achievement v1 checkpoints have no evidence of past events/combos.
    if (!Object.hasOwn(data, 'achievementRun')) data.achievementRun = emptyAchievementRun();
    if (!Object.hasOwn(data, 'pretzelStats')) data.pretzelStats = data.mode === 'pretzel' && data.pretzelScore > 0 ? null : emptyPretzelStats();
    saveObject(data, keys);
    for (const key of keys) {
      const value = data[key];
      if (typeof state[key] === 'number') saveNumber(value);
      else if (typeof state[key] === 'boolean') saveBool(value);
      state[key] = value;
    }
    saveCheck(!!classDefinition(state.classId) && ['classic', 'sprint', 'pretzel'].includes(state.mode));
    // This fork exposes the three identically named original curves only.
    saveCheck(state.mode === 'classic');
    saveNumber(state.difficulty, 0, 2, true); saveNumber(state.seed, 0, 0xffffffff, true);
    saveNumber(state.turn, 0, 1e12, true); saveNumber(state.trophyRemaining, 0, 30, true);
    saveCheck(!state.over && !state.won && state.scrollPolicy === 'classic-memory-v1');
    saveConfig(state.classConfig, state.classId); readHistoryId(state.historyId); readPretzelStats(state.pretzelStats);
    readAchievementRun(state.achievementRun, state.difficulty);
    const heroKeys = Object.keys(newGameState().hero);
    saveObject(state.hero, heroKeys);
    for (const key of heroKeys.filter(key => key !== 'stats')) saveNumber(state.hero[key]);
    saveNumber(state.hero.level, 1, 1e12, true);
    saveCheck(state.hero.hp <= state.hero.maxHp && state.hero.armor <= state.hero.maxArmor && state.hero.maxHp > 0);
    saveMap(state.hero.stats, Object.keys(newGameState().hero.stats));
    saveCheck(Object.keys(state.hero.stats).length === 7);
    saveArray(state.board, CELL_COUNT, CELL_COUNT).forEach(readSavedCell);
    const uids = state.board.filter(cell => cell.type === 'skull').map(cell => cell.skull.uid);
    saveCheck(new Set(uids).size === uids.length);
    saveArray(state.equipment, ITEM_SLOTS.length, ITEM_SLOTS.length).forEach(item => readSavedItem(item));
    saveCheck(new Set(state.equipment.map(item => item.slot)).size === ITEM_SLOTS.length);
    saveObject(state.equipmentApplied, [...ITEM_KEYS, 'health']);
    Object.values(state.equipmentApplied).forEach(n => saveNumber(n));
    const skillIds = ALL_SKILLS.map(skill => skill.id);
    saveArray(state.skills, 4).forEach(id => saveCheck(typeof id === 'string' && Object.hasOwn(SKILL_BY_ID, id)));
    saveCheck(new Set(state.skills).size === state.skills.length);
    saveMap(state.skillLevels, skillIds, 1, 10, true);
    saveMap(state.skillCooldowns, skillIds, 0, 100000, true);
    saveMap(state.skillDurations, skillIds, -64, 100000, true);
    saveMap(state.cooldownAdjust, skillIds, -1, 0, true);
    saveObject(state.usedSkillIds, skillIds, []); Object.values(state.usedSkillIds).forEach(saveBool);
    saveMap(state.nextTurnFlags, ['runeActive', 'swordBoost', 'shieldBoost', 'potionBoost', 'coinBoost', 'berserkActive', 'stealActive', 'lastStand', 'oneHealthSkulls', 'forceCoins'], 0, 1, true);
    saveObject(state.resourceCarry, ['xp', 'gold', 'upgrade']); Object.values(state.resourceCarry).forEach(n => saveNumber(n, 0, 1));
    saveMap(state.collectionCounts, ['potion', 'xp', 'coin', 'shield', 'sword', 'arrow', 'chain', 'trophy'], 0, 0x7fffffff, true);
    saveObject(state.refillFilter, ['enabled', 'excludeSwords']); Object.values(state.refillFilter).forEach(saveBool);
    saveObject(state.scrollLeechLabel, ['version', 'number', 'active', 'inFlight']);
    saveCheck(state.scrollLeechLabel.version === 1); saveNumber(state.scrollLeechLabel.number, 0, 99999, true);
    saveBool(state.scrollLeechLabel.active); saveBool(state.scrollLeechLabel.inFlight);
    if (state.scrollNativeWords !== null) saveArray(state.scrollNativeWords, 42, 42).forEach(n => saveNumber(n, -0x80000000, 0xffffffff, true));
    saveArray(state.rewardMenus, 128).forEach(kind => saveCheck(REWARD_KINDS.includes(kind)));
    saveArray(state.log, 5).forEach(text => saveText(text, 500));
    saveArray(state.compatibilityNotices, 16).forEach(text => saveText(text, 500));
    return state;
  }
  function decodeRunSave(raw) {
    saveCheck(typeof raw === 'string' && raw.length <= RUN_SAVE_LIMIT);
    const data = JSON.parse(raw, (key, value) => { saveCheck(!['__proto__', 'constructor', 'prototype'].includes(key)); return value; });
    saveCheck(data && data.format === 'lunapark-raid-magitech-run' && data.version === 1);
    if (data.status === 'finished') {
      saveObject(data, ['format', 'version', 'status', 'record'], ['format', 'version', 'status']);
      if (!Object.hasOwn(data, 'record')) return null; // original v1 tombstone
      readHistoryRecord(data.record); return { status: 'finished', record: data.record };
    }
    saveObject(data, ['format', 'version', 'status', 'revision', 'savedAt', 'build', 'state', 'rng', 'skullUid', 'menu', 'results', 'arrivals', 'next', 'leechFadeMs']);
    saveCheck(data.status === 'active'); saveNumber(data.savedAt, 0, Number.MAX_SAFE_INTEGER, true);
    saveNumber(data.revision, 1, Number.MAX_SAFE_INTEGER, true);
    saveCheck(typeof data.build === 'string' && /^(?:[a-f0-9]{12}|source)$/.test(data.build));
    data.state = readSavedState(data.state, data.state && Object.hasOwn(data.state, 'historyId') ? null : legacyHistoryId(raw, data.savedAt));
    saveArray(data.rng, 6, 6).forEach(n => saveNumber(n, 0, 0xffffffff, true));
    saveNumber(data.skullUid, 1, 1e12, true);
    saveCheck(data.state.board.every(cell => cell.type !== 'skull' || cell.skull.uid < data.skullUid));
    readSavedMenu(data.menu);
    const results = saveArray(data.results, 2048).map(result => {
      saveObject(result, Object.keys(collectionResult())); Object.values(result).forEach(n => saveNumber(n)); return result;
    });
    const resultAt = index => { saveNumber(index, 0, results.length - 1, true); return results[index]; };
    data.state.arrivals = saveArray(data.arrivals, 2048).map(event => {
      saveObject(event, ['kind', 'result', 'token', 'amount', 'leech', 'lastStand', 'cause'], ['kind', 'result']);
      const copy = { ...event, result: resultAt(event.result) };
      if (event.kind === 'resource') {
        saveObject(event, ['kind', 'result', 'token']);
        saveObject(event.token, ['type', 'variant', 'skillSlot'], ['type', 'variant']);
        saveCheck(['potion', 'xp', 'coin', 'shield'].includes(event.token.type)); saveNumber(event.token.variant, 0, 2, true);
        if (Object.hasOwn(event.token, 'skillSlot')) saveNumber(event.token.skillSlot, 0, 3, true);
      } else {
        saveCheck(['healing', 'damage'].includes(event.kind));
        saveObject(event, ['kind', 'result', 'amount', ...(event.kind === 'healing' ? ['leech'] : ['lastStand', 'cause'])], ['kind', 'result', 'amount']);
        saveNumber(event.amount);
        if (Object.hasOwn(event, 'cause')) saveCheck([34, 35].includes(event.cause));
        for (const key of ['leech', 'lastStand']) if (Object.hasOwn(event, key)) saveBool(event[key]);
      }
      return copy;
    });
    if (data.next !== null) {
      const next = data.next;
      saveObject(next, ['phase', 'chain', 'committed', 'result'], ['phase']);
      saveCheck(['skill', 'ready', 'end-turn', 'chain', 'match-chain', 'enemy'].includes(next.phase));
      saveObject(next, next.phase === 'chain' ? ['phase', 'committed', 'result'] : next.phase === 'match-chain' ? ['phase', 'chain'] : ['phase']);
      if (next.phase === 'chain') { saveBool(next.committed); next.result = resultAt(next.result); }
      else if (next.phase === 'match-chain') {
        saveArray(next.chain, CELL_COUNT, 3).forEach(n => saveNumber(n, 0, CELL_COUNT - 1, true));
        saveCheck(new Set(next.chain).size === next.chain.length);
        saveCheck(next.chain.every((slot, i) => canChainSelect(slot, next.chain.slice(0, i), data.state.board) && (!i || isAdjacent(next.chain[i - 1], slot))));
        const effective = next.chain.filter((slot, i) => !i || data.state.board[next.chain[i - 1]].type !== 'arrow' || ['sword', 'arrow', 'skull'].includes(data.state.board[slot].type));
        saveCheck(effective.length >= 3 || data.state.runeActive > 0 && interiorCells(next.chain).length > 0);
        saveCheck(data.state.actionCommitted === (effective.length >= 3));
      }
      if (['match-chain', 'enemy'].includes(next.phase)) saveCheck(data.menu === null && !data.state.arrivals.length && !data.state.rewardMenus.length);
    }
    saveCheck(!data.state.resolving || data.next !== null);
    saveCheck(data.next === null || data.state.resolving);
    saveCheck(!data.state.scrollLeechLabel.inFlight || data.state.scrollLeechLabel.active && data.state.arrivals.some(event => event.kind === 'healing' && event.leech));
    if (data.leechFadeMs !== null) saveNumber(data.leechFadeMs, 0, 200);
    return data;
  }
  function encodeRunSave() {
    const state = {};
    for (const key of Object.keys(newGameState())) if (!RUN_SAVE_SKIP.has(key)) state[key] = S[key];
    state.equipment = S.equipment.map(savedItem);
    if (S.scrollNativeWords !== null) state.scrollNativeWords = Array.from({ length: 42 }, (_, n) => S.scrollNativeWords[n]);
    const results = [], resultRef = value => {
      const old = results.indexOf(value); if (old >= 0) return old;
      results.push(value); return results.length - 1;
    };
    let next = SCHEDULED_STEP || AFTER_ARRIVALS;
    if (next?.phase === 'match-chain') next = { phase: next.phase, chain: next.snapshot?.chain || next.chain };
    else if (next?.phase === 'chain') next = { ...next, result: resultRef(next.result) };
    const arrivals = S.arrivals.map(event => ({ ...event, result: resultRef(event.result) }));
    let menu = null;
    if (REWARD_KINDS.includes(S.pendingModal)) {
      const kind = S.pendingModal;
      const choices = S.menuCandidates.map(option => kind === 'gold' ? savedItem(option)
        : kind === 'armor' ? { slot: option.slot, key: option.key, value: option.value }
        : STATS.includes(option) ? { kind: 'stat', id: option.id } : savedSkill(option));
      menu = { kind, choices, picked: kind === 'level' ? [...S.levelDraft] : [] };
    }
    const label = S.scrollLeechLabel;
    const leechFadeMs = SCROLL_LABEL_TIMER !== null ? Math.max(0, Math.min(200, SCROLL_LABEL_DEADLINE - Date.now()))
      : label.active && !label.inFlight && !SCHEDULED_STEP ? 200 : null;
    return JSON.stringify({ format: 'lunapark-raid-magitech-run', version: 1, status: 'active', revision: SAVE_REVISION + 1, savedAt: Date.now(),
      build: qs('meta[name="raid-build"]')?.content || 'source', state, rng: Array.from(RNG_SEEDS), skullUid: SKULL_UID,
      menu, results, arrivals, next, leechFadeMs });
  }
  function saveStatus(state, message) {
    RUN_SAVE_STATUS = { state, message };
    for (const id of ['picker-save-status', 'menu-save-status', 'save-status']) {
      const node = el(id); if (!node) continue;
      node.textContent = message;
      if (id === 'save-status') node.hidden = !['unavailable', 'invalid', 'conflict'].includes(state);
    }
  }
  function readRunSlot() {
    let raw;
    try { raw = profileStorage.getItem(RUN_SAVE_KEY); }
    catch (e) { saveStatus('unavailable', 'Хранилище браузера недоступно. Продолжить после закрытия страницы не получится.'); return false; }
    RUN_SAVE_BASE = raw;
    try {
      const decoded = raw === null ? null : decodeRunSave(raw);
      SAVED_RUN = decoded?.status === 'active' ? decoded : null;
      if (decoded?.status === 'finished') recordFinishedRun(decoded.record);
      if (SAVED_RUN) {
        SAVE_REVISION = SAVED_RUN.revision;
        // Reconcile only already-earned events from a fully validated backup;
        // this does not resume gameplay or infer events from its board/score.
        const earned = SAVED_RUN.state.achievementRun.earned;
        if (Object.keys(earned).length) {
          ACHIEVEMENT_PENDING.set(SAVED_RUN.state.historyId, { ...earned });
          flushAchievementProgress();
        }
      }
      saveStatus(SAVED_RUN ? 'saved' : 'empty', SAVED_RUN ? `Забег сохранён в этом браузере · ход ${SAVED_RUN.state.turn}.` : 'Сохранённого забега пока нет.');
      return true;
    } catch (e) {
      SAVED_RUN = null;
      saveStatus('invalid', 'Сохранение повреждено или создано несовместимой версией. Оно не загружено; можно начать новый забег.');
      return false;
    }
  }
  function ownsRunSlot() {
    if (SAVE_CONFLICT) return false;
    try {
      const current = profileStorage.getItem(RUN_SAVE_KEY);
      if (RUN_SAVE_BASE !== undefined && current !== RUN_SAVE_BASE) {
        SAVE_CONFLICT = true;
        saveStatus('conflict', 'Сохранение изменено в другой вкладке. Эта вкладка приостановлена: обнови страницу, чтобы продолжить актуальный забег.');
        return false;
      }
      if (RUN_SAVE_BASE === undefined) RUN_SAVE_BASE = current;
    } catch (e) { /* saveRun reports blocked storage; offline play still works. */ }
    return true;
  }
  function queueRunSave() {
    clearTimeout(SAVE_TIMER);
    SAVE_TIMER = setTimeout(() => { SAVE_TIMER = null; saveRun(); }, 0);
  }
  function saveRun() {
    if (!S || RESOURCE_BATCH || DRAINING_ARRIVALS || S.resolving && !AFTER_ARRIVALS && !SCHEDULED_STEP && !S.over) return false;
    if (!ownsRunSlot()) return false;
    let raw, decoded;
    try {
      if (!S.over && S.equipment.some(item => ITEM_KEYS.filter(key => itemValue(item, key) !== 0).length >= 8)) queueAchievement(72);
      if (S.over && !FINISHED_RECORD) FINISHED_RECORD = makeHistoryRecord();
      raw = S.over ? JSON.stringify({ format: 'lunapark-raid-magitech-run', version: 1, status: 'finished', record: FINISHED_RECORD }) : encodeRunSave();
      decoded = decodeRunSave(raw);
    } catch (e) {
      saveStatus('invalid', 'Текущая точка не записана: состояние не прошло проверку. Предыдущее корректное сохранение не изменено.');
      return false;
    }
    try {
      profileStorage.setItem(RUN_SAVE_KEY, raw);
      RUN_SAVE_BASE = raw; SAVED_RUN = decoded?.status === 'active' ? decoded : null;
      if (SAVED_RUN) SAVE_REVISION = SAVED_RUN.revision;
      saveStatus(SAVED_RUN ? 'saved' : 'empty', SAVED_RUN ? `Забег сохранён в этом браузере · ход ${S.turn}.` : 'Забег завершён. Активного сохранения нет.');
      if (S.over) recordFinishedRun(FINISHED_RECORD);
      else { rememberAchievementProgress(); flushAchievementProgress(); }
      return true;
    } catch (e) {
      // Small tombstones/removal usually still work when a large write hits the
      // quota. Never leave a pre-death or pre-trophy checkpoint looking current.
      try { profileStorage.removeItem(RUN_SAVE_KEY); RUN_SAVE_BASE = null; SAVED_RUN = null; } catch (ignored) {}
      saveStatus('unavailable', 'Не удалось сохранить забег. Разреши локальное хранилище и освободи место; до успешной записи не закрывай страницу.');
      if (S.over) recordFinishedRun(FINISHED_RECORD);
      else { rememberAchievementProgress(); flushAchievementProgress(); }
      return false;
    }
  }
  function savedRunInfo() {
    if (RUN_SAVE_BASE === undefined) readRunSlot();
    if (!SAVED_RUN) return null;
    const { state, savedAt } = SAVED_RUN;
    return { classId: state.classId, mode: state.mode, difficulty: state.difficulty, turn: state.turn, hp: state.hero.hp, level: state.hero.level,
      pendingModal: SAVED_RUN.menu?.kind || null, savedAt };
  }
  function restoreMenuChoices(menu) {
    const skill = value => ({ ...SKILL_BY_ID[value.id], skill: SKILL_BY_ID[value.id],
      duration: value.duration, charge: value.charge, level: value.level, upgrade: value.upgrade });
    if (menu.kind === 'level') return menu.choices.map(value => value.kind === 'stat' ? STATS.find(stat => stat.id === value.id) : skill(value));
    if (menu.kind === 'armor') return menu.choices.map(value => describeUpgradeOffer(value.slot, value.key, value.value));
    return menu.choices.map(value => ({ ...value, slotName: itemSlotById(value.slot).name, icon: itemSlotById(value.slot).icon,
      ...(value.scroll ? { scroll: skill(value.scroll) } : {}) }));
  }
  function resumeSavedRun() {
    if (!readRunSlot() || !SAVED_RUN) return false;
    const data = SAVED_RUN;
    RUN_ID++; SAVE_CONFLICT = false; NEW_RUN_REQUEST = null; FINISHED_RECORD = null;
    clearFloatEffects();
    cancelTelegramExit(false);
    clearTimeout(ACTION_TIMER); clearTimeout(SAVE_TIMER); clearTimeout(SCROLL_LABEL_TIMER);
    ACTION_TIMER = SAVE_TIMER = SCROLL_LABEL_TIMER = null; SCROLL_LABEL_DEADLINE = 0;
    SCHEDULED_STEP = AFTER_ARRIVALS = RESOURCE_BATCH = null; DRAINING_ARRIVALS = false;
    activeDrag = false; window.removeEventListener?.('pointermove', onPointerMove);
    closeRunHistory();
    S = data.state;
    syncClassAchievements();
    S.rngSeeds = [...data.rng]; RNG_SEEDS.set(data.rng); SKULL_UID = data.skullUid; SKULL_IMAGES.clear();
    for (const key of ['perks', 'flaws', 'replacements']) Object.freeze(S.classConfig[key]); Object.freeze(S.classConfig);
    S.equipment = S.equipment.map(item => ({ ...item, slotName: itemSlotById(item.slot).name, icon: itemSlotById(item.slot).icon }));
    closeClassEditor(false); pickerOpen = false; pickerGeneration++;
    for (const id of ['class-picker', 'modal', 'menu-modal', 'help-modal', 'restart-modal']) el(id)?.classList.remove('show');
    const board = el('board'); if (board) { board.innerHTML = ''; delete board.dataset.cellsReady; }
    renderBoard(); renderHUD(); renderChainTip();
    const logNode = el('log'); if (logNode) logNode.innerHTML = S.log.map(text => `<div>${text}</div>`).join('');
    if (data.leechFadeMs !== null) fadeScrollLeechLabel(data.leechFadeMs);
    if (data.next && ['match-chain', 'enemy'].includes(data.next.phase)) scheduleRunStep(data.next, data.next.phase === 'match-chain' ? 420 : 550);
    else {
      AFTER_ARRIVALS = data.next;
      if (data.menu) { S.levelDraft = [...data.menu.picked]; openModal(data.menu.kind, restoreMenuChoices(data.menu)); }
      else resumeRewards();
    }
    saveRun();
    return true;
  }

  // --- local records / class statistics (one atomic profile value) ----------
  // Gameplay RNG never creates IDs or sorts records. Receipts make retrying the
  // terminal checkpoint idempotent; they are not a cross-tab transaction/lock.
  const RUN_HISTORY_KEY = 'raid:history';
  const RUN_HISTORY_LIMIT = 256 * 1024;
  const SCORE_TYPES = ['Классика · Лёгкий', 'Классика · Обычный', 'Классика · Сложный', 'Классика · Очень сложный', 'Спринт', 'Крендель'];
  let HISTORY_NONCE = 0;
  let HISTORY_PENDING = null;
  let HISTORY_FOCUS = null;
  let HISTORY_OPEN = false;
  let HISTORY_STATUS = { state: 'empty', message: 'Рекордов пока нет. Они появятся после завершённого забега.' };

  // Local achievements: own catalogue/UI and event ledger. Conditions/numbers:
  // coolking70/DungeonRaid a708df8, spec 06 (A1/45 intentionally unresolved).
  // Native percentages, NOT points/rewards. Easy suppresses everything but 67.
  const ACHIEVEMENT_PENDING = new Map();
  let ACHIEVEMENT_TIMER = null;
  let CHAIN_REFILL = null;
  const ACHIEVEMENT_RACES = ['Elf', 'Dwarf', 'Sauren', 'Faun', 'Orc', 'Halfling', 'Werewolf', 'Dragonkin', 'Changeling'];
  const ACHIEVEMENTS = [null];
  for (let id = 1; id <= 9; id++) {
    const cls = CLASSES.find(entry => entry.native === id);
    ACHIEVEMENTS[id] = { title: `Мех «${cls.name}»`, description: `Открыть мех «${cls.name}» трофеем.` };
    ACHIEVEMENTS[id + 9] = { title: RACE_INFO[ACHIEVEMENT_RACES[id - 1]].name,
      description: `Поднять до уровня меха 5 мех с этим родным бойцом. Выбранный в редакторе боец не учитывается.` };
    ACHIEVEMENTS[id + 18] = { title: `Мастер: ${cls.name}`, description: `Довести мех «${cls.name}» до уровня 10.` };
  }
  [
    ['Все мастера', 'Довести все девять мехов, кроме Авантюриста, до уровня меха 10.'],
    ['Первые открытия', 'Достичь уровня героя 5.'],
    ['Опытный герой', 'Достичь уровня героя 10.'],
    ['Героический путь', 'Достичь уровня героя 25.'],
    ['Легенда рейда', 'Достичь уровня героя 50.'],
    ['На вершине', 'Достичь уровня героя 100.'],
    ['Острый конец', 'Урон отражённых шипов должен опустить HP строго ниже нуля, до проверки Последнего рубежа.'],
    ['Поджаренный', 'Урон горящих клеток цепочки должен опустить HP строго ниже нуля, до спасения.'],
    ['Отравленный герой', 'Урон ядом героя должен опустить HP строго ниже нуля, до спасения.'],
    ['Растворённый', 'Кислотное зелье должно опустить HP строго ниже нуля, до спасения.'],
    ['Непредвиденный поворот', 'Нестабильный — смертный враг Перевёртыша — обновляет поле с запретом мечей.'],
    ['Приятного аппетита', 'Пожиратель действительно уменьшает положительный параметр выбранной вещи.'],
    ['Немезида', 'Дойти до HP ≤ 0, пока на поле есть смертный враг своей нечеловеческой расы. Последний рубеж может спасти.'],
    ['Уже тесно', 'В момент появления нового особого черепа на поле должно быть не меньше трёх особых, включая боссов.'],
    ['Не дотронешься', 'Дворфом собрать Хаотического — своего смертного врага — с наградой особого.'],
    ['Ответный укол', 'Своими шипами при блокировании убить Острого черепа. Контратака не считается.'],
    ['Джекпот', 'Собрать Золотого с наградой особого, не дать ему уйти по таймеру.'],
    ['Я всё вижу', 'Триггер этого достижения в проверенной версии не найден. Пока недоступно; обнаружение Невидимого не считается.'],
    ['Жажда битвы', 'Получить награду за 100 особых черепов. За каждого — 1%.'],
    ['Всего лишь царапина', 'Эльфом добить ядом 50 врагов в конце хода. За каждого — 2%.'],
    ['Удар снизу', 'Дворфом убить 50 врагов прямым апперкотом: через цель снизу вверх по вертикали. За каждого — 2%.'],
    ['Останься и послушай', 'Сауреном купить и применить 50 прикреплённых к вещам свитков. За каждый — 2%.'],
    ['Ещё жив', 'Фавном 20 раз реально поднять HP до четверти максимума при повышении уровня героя. За лечение — 5%.'],
    ['Время молота', 'Орком 50 раз успешно оглушить выжившую цель прямой атаки. За успех — 2%.'],
    ['Чем они больше', 'Полуросликом 10 раз убить прямой цепочкой не менее 15 врагов и усилить доспех. За цепь — 10%.'],
    ['Ещё один пал', 'Вервольфом собрать Калечением 50 обычных черепов с HP ниже их защиты. За каждого — 2%.'],
    ['Пламя дракона', 'Драконидом 50 раз добить врага в огненной стадии конца хода. Источник огня может быть любым. За каждого — 2%.'],
    ['Слёзы под дождём', 'Перевёртышем 50 раз войти в награду особого и расовую смену облика. За каждый — 2%.'],
    ['Снайпер', 'Убить особого на конце цепочки после пропуска стрелой: стрела за два узла до цели, разница по строке или столбцу больше 1.'],
    ['Что не убивает', 'Полуросликом совершить атаку с 15+ прямыми убийствами в том же ходу после Мазохизма.'],
    ['Мастер Руны', 'Руной собрать внутри больше клеток, чем эффективных узлов в её контуре. Особые внутри не считаются.'],
    ['Ожерелье', 'Провести цепочку по всем 36 клеткам и собрать хотя бы одну цепную клетку.'],
    ['Чистая строка', 'Одним Рассечением собрать строку из шести обычных черепов.'],
    ['Вернулся с того света', 'Последний рубеж должен действительно спасти при HP ≤ 0.'],
    ['Предчувствие', 'Под Ловушкой сгенерировать особого черепа с 1 HP.'],
    ['Шок и трепет', 'В одном окне до конца хода применить Рассечение, Огненный шар, Рывок и сбор всей доски (Желание либо связка Рассечения со Взломом). Это не навыки Шок и Благоговение.'],
    ['Загадай желание', 'Применить Желание, когда на поле не менее 20 обычных черепов.'],
    ['Храбрый беглец', '100 раз применить Телепорт при наличии хотя бы одного особого врага. За применение — 1%, не за число врагов.'],
    ['Сафари', 'Особый, созданный Крупной дичью, должен действительно превратиться в трофей. Просто убийство или подбор другого трофея не считаются.'],
    ['Миссия завершена', 'Завершить забег смертью или победой в Спринте. Доступно и на Лёгком; рестарт не считается.'],
    ['Вокруг света', 'Провести цепочку из всех 20 клеток по границе поля.'],
    ['Золотоискатель', 'Одной цепочкой собрать поле из 36 монет без пропущенных стрелой узлов.'],
    ['Трагический герой', 'На Очень сложном достичь уровня героя 10 с одним пассивом и четырьмя изъянами в замороженной конфигурации забега.'],
    ['Проще простого', 'На Очень сложном достичь уровня героя 5, не имея ни одного надетого навыка в этот момент.'],
    ['Вооружён и опасен', 'При записи активного забега иметь на одной надетой вещи не менее восьми ненулевых параметров. Предложения магазина не считаются.'],
    ['Собиратель обломков', 'Собрать 100 сломанных клеток. За каждую — 1%; награда ресурсом не нужна.'],
    ['Просто шучу', 'После подтверждённой цепи заполнить более девяти клеток, причём все новые клетки должны оказаться черепами.'],
    ['Вот это крендель', 'Провести цепочку с шестью или более повторными диагональными пересечениями. В любом режиме.'],
    ['Выживший', 'Действительно победить в Спринте на 100-м ходу; смертельный яд в конце хода отменяет победу.'],
    ['Легенда Кренделя', 'В режиме Крендель провести цепь с очками и не менее чем 15 пересечениями.'],
  ].forEach(([title, description], index) => { ACHIEVEMENTS[index + 28] = { title, description }; });

  function emptyAchievementRun() { return { version: 1, earned: {}, combo: 0, masochism: false }; }
  function emptyAchievementHistory() { return { version: 1, progress: {}, runs: [] }; }
  function readAchievementMap(value, difficulty = 1) {
    saveObject(value, Array.from({ length: 77 }, (_, i) => String(i + 1)), []);
    for (const [key, amount] of Object.entries(value)) {
      saveCheck(key !== '45' && (difficulty !== 0 || key === '67'));
      saveNumber(amount, 1, 100, true);
    }
  }
  function readAchievementRun(value, difficulty) {
    saveObject(value, ['version', 'earned', 'combo', 'masochism']); saveCheck(value.version === 1);
    readAchievementMap(value.earned, difficulty); saveNumber(value.combo, 0, 15, true); saveBool(value.masochism);
  }
  function readAchievementHistory(value) {
    saveObject(value, ['version', 'progress', 'runs']); saveCheck(value.version === 1); readAchievementMap(value.progress);
    const ids = [];
    saveArray(value.runs, 64).forEach(run => {
      saveObject(run, ['id', 'earned']); ids.push(readHistoryId(run.id)); readAchievementMap(run.earned);
      saveCheck(Object.entries(run.earned).every(([id, amount]) => amount <= (value.progress[id] || 0)));
    });
    saveCheck(new Set(ids).size === ids.length);
  }
  function queueAchievement(id, amount = 100) {
    if (!S?.classConfig || S.over || id === 45 || !ACHIEVEMENTS[id] || !Number.isInteger(amount) || amount <= 0 || S.difficulty === 0 && id !== 67) return;
    const earned = S.achievementRun.earned;
    earned[id] = Math.min(100, (earned[id] || 0) + amount);
  }
  function syncClassAchievements() {
    if (!S?.classConfig || S.over) return;
    for (const cls of CLASSES) {
      if (!cls.native || !classLevelOf(cls.id)) continue;
      queueAchievement(cls.native);
      if (classLevelOf(cls.id) >= 5) queueAchievement(10 + ACHIEVEMENT_RACES.indexOf(cls.race));
      if (classLevelOf(cls.id) === 10) queueAchievement(cls.native + 18);
    }
    if (CLASSES.filter(cls => cls.native).every(cls => classLevelOf(cls.id) >= 10)) queueAchievement(28);
  }
  function achievementCombo(bit) {
    S.achievementRun.combo |= bit;
    if (S.achievementRun.combo === 15) queueAchievement(63);
  }
  function checkPromotionAchievement(newSkull) {
    // Refilling columns can temporarily contain the same moved object twice.
    // Count distinct enemy identities, never those transient duplicate slots.
    const ids = new Set(S.board.filter(cell => cell?.type === 'skull' && !cell.matched && cell.skull.type > 0).map(cell => cell.skull.uid));
    if (newSkull?.type > 0) ids.add(newSkull.uid);
    if (ids.size > 2) queueAchievement(41);
  }
  function noteChainRefill(cell) {
    if (CHAIN_REFILL) { CHAIN_REFILL.total++; if (cell.type === 'skull') CHAIN_REFILL.skulls++; }
    return cell;
  }
  function isEdgeSlot(idx) { return idx % COLS === 0 || idx % COLS === COLS - 1 || idx < COLS || idx >= CELL_COUNT - COLS; }
  function hasPendingProgress() { return !!HISTORY_PENDING || ACHIEVEMENT_PENDING.size > 0; }
  function rememberAchievementProgress() {
    if (S && !S.over && Object.keys(S.achievementRun.earned).length) ACHIEVEMENT_PENDING.set(S.historyId, { ...S.achievementRun.earned });
  }
  function isCompletedHistory(data, id) { return data.completed.includes(id) || data.records.some(rows => rows.some(row => row.id === id)); }
  function mergeAchievementProgress(data, id, earned) {
    if (isCompletedHistory(data, id)) return false;
    const history = data.achievements, before = JSON.stringify(history);
    let receipt = history.runs.find(run => run.id === id);
    if (!receipt) { receipt = { id, earned: {} }; history.runs.push(receipt); }
    for (const [key, total] of Object.entries(earned)) {
      const old = receipt.earned[key] || 0, progress = history.progress[key] || 0;
      if (total > old) history.progress[key] = Math.min(100, progress + total - old);
      receipt.earned[key] = Math.max(old, total);
    }
    // Saturated percentages need no per-run receipts. The bounded journal is
    // replay protection for normal save/resume, not against manual rollback.
    for (const run of history.runs) for (const key of Object.keys(run.earned)) if (history.progress[key] === 100) delete run.earned[key];
    history.runs = history.runs.filter(run => Object.keys(run.earned).length).slice(-64);
    return before !== JSON.stringify(history);
  }
  function flushAchievementProgress() {
    if (!ACHIEVEMENT_PENDING.size) return true;
    if (!ownsRunSlot()) { historyStatus('conflict', 'Достижения не записаны: сохранение изменилось в другой вкладке. Обнови страницу.'); return false; }
    const stored = readRunHistory(); if (!stored) return false;
    const data = stored.data, before = { ...data.achievements.progress };
    let changed = false;
    try {
      for (const [id, earned] of ACHIEVEMENT_PENDING) { readAchievementMap(earned); changed = mergeAchievementProgress(data, id, earned) || changed; }
      if (changed) {
        data.revision++;
        const raw = JSON.stringify(data); decodeRunHistory(raw);
        if (profileStorage.getItem(RUN_HISTORY_KEY) !== stored.raw) {
          historyStatus('conflict', 'История изменилась в другой вкладке. Нажми «Повторить запись»; прогресс пока остаётся в сохранении забега.'); return false;
        }
        profileStorage.setItem(RUN_HISTORY_KEY, raw);
      }
      ACHIEVEMENT_PENDING.clear(); announceAchievements(before, data.achievements.progress);
      if (!HISTORY_PENDING) historyStatus('saved', 'Достижения сохранены в этом браузере.');
      return true;
    } catch (e) { historyStatus('unavailable', 'Достижения пока не записаны в профиль. Разреши хранилище и нажми «Повторить запись». До этого не заменяй сохранённый забег.'); return false; }
  }
  function announceAchievements(before, after) {
    const unlocked = Object.keys(after).filter(id => after[id] === 100 && before[id] !== 100);
    const node = el('achievement-notice'); if (!unlocked.length || !node) return;
    clearTimeout(ACHIEVEMENT_TIMER);
    node.textContent = 'Достижение: ' + unlocked.slice(0, 2).map(id => ACHIEVEMENTS[id].title).join(' · ') + (unlocked.length > 2 ? ` · ещё ${unlocked.length - 2}` : '');
    node.hidden = false;
    ACHIEVEMENT_TIMER = setTimeout(() => { node.hidden = true; }, 5000);
  }
  function getLocalAchievements() {
    const data = readRunHistory()?.data; if (!data) return null;
    const catalog = ACHIEVEMENTS.slice(1).map((entry, index) => ({ id: index + 1, ...entry, available: raidAchievementAvailable(index + 1),
      progress: data.achievements.progress[index + 1] || 0, unlocked: data.achievements.progress[index + 1] === 100 }));
    return { catalog, unlocked: catalog.filter(entry => entry.unlocked).length, available: 72, total: 77 };
  }
  function renderAchievements(body, progress) {
    appendHistoryHint(body, 'Правила и совместимость', 'Унаследованы 77 записей. В этой версии 72 доступных: условия Sprint/Pretzel/Harder (70,71,76,77) не заменялись новыми и недоступны, триггер45 не подтверждён. На Лёгком — только «Миссия завершена». Прогресс событий начинается с этой сборки; прошлые боевые события не восстанавливаются. Уже открытые мехи и их родные бойцы распознаются при следующем старте или продолжении не на Лёгком. Проценты копятся между забегами до 100, не дают очков и не зависят от окончательной смерти: спасение Последним рубежом не отменяет уже сработавшее событие.');
    const summary = document.createElement('p'); summary.className = 'achievement-summary';
    summary.textContent = `Открыто ${Object.values(progress).filter(amount => amount === 100).length} из 72 доступных · 77 в исходном каталоге`;
    body.appendChild(summary);
    const filter = el('achievement-filter')?.value || 'all';
    let shown = 0;
    ACHIEVEMENTS.forEach((entry, id) => {
      if (!entry) return;
      const amount = progress[id] || 0, available = raidAchievementAvailable(id);
      if (filter === 'unlocked' && amount !== 100 || filter === 'pending' && (!available || amount === 100) || filter === 'unavailable' && available) return;
      const card = document.createElement('article'); card.className = 'history-record achievement-record' + (amount === 100 ? ' unlocked' : '') + (!available ? ' unavailable' : '');
      card.dataset.achievementId = String(id);
      const title = document.createElement('h4'); title.textContent = `${amount === 100 ? '✓' : available ? '◇' : '?'} ${entry.title}`; card.appendChild(title);
      const description = document.createElement('p'); description.textContent = entry.description; card.appendChild(description);
      const status = document.createElement('p'); status.className = 'achievement-progress'; status.textContent = !available ? id === 45 ? 'Недоступно: условие не подтверждено' : 'Недоступно: условие требует режима или сложности классического теста' : amount === 100 ? 'Открыто · 100%' : `Прогресс: ${amount}%`; card.appendChild(status);
      if (available) {
        const bar = document.createElement('progress'); bar.max = 100; bar.value = amount;
        bar.setAttribute('aria-label', entry.title + ': ' + amount + '%'); card.appendChild(bar);
      }
      body.appendChild(card); shown++;
    });
    if (!shown) { const empty = document.createElement('p'); empty.className = 'save-note'; empty.textContent = 'В этом фильтре пока нет достижений.'; body.appendChild(empty); }
  }
  function openHistorySection(value) {
    if (S?.resolving || S?.pendingModal && S.pendingModal !== 'gameover') { flash('Сначала заверши ход и выбор наград'); return false; }
    const section = el('history-section'); if (!section) return false;
    section.value = value;
    if (HISTORY_OPEN) { renderRunHistory(); return true; }
    return openRunHistory();
  }
  function openAchievements() { return openHistorySection('achievements'); }
  function openRunRecords() { return openHistorySection('records'); }

  function newHistoryId() {
    return 'run-' + (window.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${++HISTORY_NONCE}-${Math.random().toString(36).slice(2)}`);
  }
  function legacyHistoryId(raw, savedAt) {
    let hash = 2166136261;
    for (let i = 0; i < raw.length; i++) hash = Math.imul(hash ^ raw.charCodeAt(i), 16777619);
    return `v1-${savedAt.toString(36)}-${(hash >>> 0).toString(36)}`;
  }
  function readHistoryId(value) {
    saveCheck(typeof value === 'string' && /^[a-z0-9-]{6,100}$/.test(value)); return value;
  }
  function emptyPretzelStats() { return { scoringChains: 0, avgTimes10: 0, maxCross: 0 }; }
  function readPretzelStats(value) {
    if (value === null) return null; // pre-statistics v1 save: the old mean is unknowable
    saveObject(value, ['scoringChains', 'avgTimes10', 'maxCross']);
    saveNumber(value.scoringChains, 0, 1e12, true); saveNumber(value.avgTimes10, 0, 360, true); saveNumber(value.maxCross, 0, 36, true);
    saveCheck(value.scoringChains ? value.avgTimes10 >= 10 && value.avgTimes10 <= value.maxCross * 10 : value.avgTimes10 === 0 && value.maxCross === 0);
    return value;
  }
  function addPretzelStats(crosses) {
    const stats = S.pretzelStats;
    if (!stats || crosses <= 0) return;
    stats.avgTimes10 = Math.trunc((stats.avgTimes10 * stats.scoringChains + 10 * crosses) / (stats.scoringChains + 1));
    stats.scoringChains++; stats.maxCross = Math.max(stats.maxCross, crosses);
  }
  function scoreType() { return S.mode === 'classic' ? S.difficulty : S.mode === 'sprint' ? 4 : 5; }
  function makeHistoryRecord() {
    const quarter = value => Math.floor(Math.round(value || 0) / (S.mode === 'sprint' ? 4 : 1));
    return { id: S.historyId, type: scoreType(), classId: S.classId, difficulty: S.difficulty, turn: S.turn,
      xp: quarter(S.hero.xpLevelCum), gold: quarter(S.goldEarned), upgrade: quarter(S.upgradeEarned),
      score: runScore(), kills: S.skullsKilled, won: S.won, finishedAt: Date.now(),
      skills: S.skills.map(id => ({ id, level: S.skillLevels[id] || 1 })),
      pretzel: S.mode === 'pretzel' && S.pretzelStats ? { ...S.pretzelStats } : null,
      achievements: { ...S.achievementRun.earned } };
  }
  function readHistoryRecord(record) {
    const keys = ['id', 'type', 'classId', 'difficulty', 'turn', 'xp', 'gold', 'upgrade', 'score', 'kills', 'won', 'finishedAt', 'skills', 'pretzel'];
    saveObject(record, [...keys, 'achievements'], keys);
    if (Object.hasOwn(record, 'achievements')) readAchievementMap(record.achievements, record.difficulty);
    readHistoryId(record.id); saveNumber(record.type, 0, 5, true); saveNumber(record.difficulty, 0, 3, true);
    saveCheck(!!classDefinition(record.classId) && (record.type > 3 || record.type === record.difficulty));
    for (const key of ['turn', 'xp', 'gold', 'upgrade', 'score', 'kills']) saveNumber(record[key], 0, Number.MAX_SAFE_INTEGER, true);
    saveNumber(record.finishedAt, 0, 8640000000000000, true); saveBool(record.won);
    saveCheck(!record.won || record.type === 4 && record.turn >= 100);
    saveArray(record.skills, 4).forEach(skill => {
      saveObject(skill, ['id', 'level']); saveCheck(typeof skill.id === 'string' && Object.hasOwn(SKILL_BY_ID, skill.id)); saveNumber(skill.level, 1, 10, true);
    });
    saveCheck(new Set(record.skills.map(skill => skill.id)).size === record.skills.length);
    readPretzelStats(record.pretzel); saveCheck(record.type === 5 || record.pretzel === null);
    return record;
  }
  function emptyRunHistory() {
    return { format: 'lunapark-raid-magitech-history', version: 1, revision: 0, records: SCORE_TYPES.map(() => []),
      classes: Object.fromEntries(CLASSES.map(cls => [cls.id, { games: 0, kills: 0, best: [0, 0, 0] }])), completed: [], last: null, achievements: emptyAchievementHistory() };
  }
  function decodeRunHistory(raw) {
    saveCheck(typeof raw === 'string' && raw.length <= RUN_HISTORY_LIMIT);
    const data = JSON.parse(raw, (key, value) => { saveCheck(!['__proto__', 'constructor', 'prototype'].includes(key)); return value; });
    // Compatible history-v1 extension; a read only adds detached defaults.
    // Rankings, class totals and terminal receipts are never rebuilt/reset.
    if (!Object.hasOwn(data, 'achievements')) data.achievements = emptyAchievementHistory();
    saveObject(data, ['format', 'version', 'revision', 'records', 'classes', 'completed', 'last', 'achievements']);
    readAchievementHistory(data.achievements);
    saveCheck(data.format === 'lunapark-raid-magitech-history' && data.version === 1); saveNumber(data.revision, 0, Number.MAX_SAFE_INTEGER, true);
    const ids = [];
    saveArray(data.records, 6, 6).forEach((rows, type) => {
      saveArray(rows, 10).forEach((row, i) => {
        readHistoryRecord(row); saveCheck(row.type === type && (!i || rows[i - 1].score >= row.score)); ids.push(row.id);
      });
    });
    saveCheck(new Set(ids).size === ids.length);
    saveObject(data.classes, CLASSES.map(cls => cls.id));
    Object.values(data.classes).forEach(stats => {
      saveObject(stats, ['games', 'kills', 'best']);
      saveNumber(stats.games, 0, Number.MAX_SAFE_INTEGER, true); saveNumber(stats.kills, 0, Number.MAX_SAFE_INTEGER, true);
      saveArray(stats.best, 3, 3).forEach(n => saveNumber(n, 0, Number.MAX_SAFE_INTEGER, true));
    });
    saveArray(data.completed, 512).forEach(readHistoryId); saveCheck(new Set(data.completed).size === data.completed.length);
    if (data.last !== null) {
      saveObject(data.last, ['type', 'index', 'id']); saveNumber(data.last.type, 0, 5, true); saveNumber(data.last.index, 0, 9, true); readHistoryId(data.last.id);
      saveCheck(data.records[data.last.type][data.last.index]?.id === data.last.id);
    }
    return data;
  }
  function historyStatus(state, message) {
    HISTORY_STATUS = { state, message };
    for (const id of ['picker-history-status', 'history-status', 'run-history-status', 'progress-status']) {
      const node = el(id); if (!node) continue;
      const achievements = el('history-section')?.value === 'achievements';
      const short = achievements ? { saved: 'Прогресс сохранён.', empty: 'Пока ничего не открыто.', unavailable: 'Прогресс пока не записан. Разреши хранилище и повтори запись.', conflict: 'История изменилась в другой вкладке. Повтори запись или обнови страницу.', invalid: 'История повреждена или несовместима. Она не перезаписана.' }[state]
        : { saved: 'История сохранена.', empty: 'Рекордов пока нет.' }[state];
      node.textContent = id === 'history-status' && short ? short : message;
      if (['picker-history-status', 'progress-status'].includes(id)) node.hidden = !['invalid', 'unavailable', 'conflict'].includes(state);
    }
    const retry = el('history-retry'); if (retry) retry.hidden = !hasPendingProgress();
  }
  function readRunHistory() {
    let raw;
    try { raw = profileStorage.getItem(RUN_HISTORY_KEY); }
    catch (e) { historyStatus('unavailable', 'Рекорды и статистика недоступны: браузер запретил чтение хранилища.'); return null; }
    try {
      const data = raw === null ? emptyRunHistory() : decodeRunHistory(raw);
      if (!hasPendingProgress()) historyStatus(raw === null ? 'empty' : 'saved', raw === null ? 'Рекордов пока нет. Они появятся после завершённого забега.' : 'Рекорды и статистика хранятся только в этом браузере.');
      return { raw, data };
    } catch (e) { historyStatus('invalid', 'История повреждена или несовместима. Она не перезаписана; настройки мехов и активный забег не затронуты.'); return null; }
  }
  function recordFinishedRun(record) {
    HISTORY_PENDING = record;
    if (!ownsRunSlot()) { historyStatus('conflict', 'Результат не записан: сохранение изменилось в другой вкладке. Обнови страницу.'); return false; }
    const stored = readRunHistory(); if (!stored) return false;
    const data = stored.data;
    if (data.completed.includes(record.id) || data.records.some(rows => rows.some(row => row.id === record.id))) {
      ACHIEVEMENT_PENDING.delete(record.id);
      HISTORY_PENDING = null; historyStatus('saved', 'Результат уже записан. Рекорды и статистика не дублируются.'); return true;
    }
    try {
      readHistoryRecord(record);
      const before = { ...data.achievements.progress };
      mergeAchievementProgress(data, record.id, record.achievements || {});
      data.achievements.runs = data.achievements.runs.filter(run => run.id !== record.id);
      const rows = data.records[record.type];
      const lower = rows.findIndex(row => row.score < record.score);
      // Classic quirk: even a new lowest score replaces the tenth entry.
      const index = Math.min(lower < 0 ? rows.length : lower, 9);
      rows.splice(index, 0, record); if (rows.length > 10) rows.pop();
      const stats = data.classes[record.classId]; stats.games++; stats.kills += record.kills;
      if (record.type >= 1 && record.type <= 3) stats.best[record.type - 1] = Math.max(stats.best[record.type - 1], record.score);
      data.completed.push(record.id); data.completed = data.completed.slice(-512);
      data.last = { type: record.type, index, id: record.id }; data.revision++;
      const raw = JSON.stringify(data); decodeRunHistory(raw);
      if (profileStorage.getItem(RUN_HISTORY_KEY) !== stored.raw) {
        historyStatus('conflict', 'История изменилась в другой вкладке. Нажми «Повторить запись», не запускай два забега одновременно.'); return false;
      }
      profileStorage.setItem(RUN_HISTORY_KEY, raw);
      ACHIEVEMENT_PENDING.delete(record.id);
      announceAchievements(before, data.achievements.progress);
      HISTORY_PENDING = null; historyStatus('saved', 'Результат записан в локальные рекорды и статистику меха.'); return true;
    } catch (e) { historyStatus('unavailable', 'Результат пока не записан. Разреши хранилище, освободи место и нажми «Повторить запись» в рекордах.'); return false; }
  }
  function retryRunHistory() {
    if (HISTORY_PENDING && !recordFinishedRun(HISTORY_PENDING)) return false;
    rememberAchievementProgress();
    return flushAchievementProgress() && !!readRunHistory();
  }
  function appendHistoryHint(body, title, text) {
    const hint = document.createElement('details'); hint.className = 'history-hint';
    const summary = document.createElement('summary'); summary.textContent = title; hint.appendChild(summary);
    const note = document.createElement('p'); note.textContent = text + ' Старые завершённые забеги до этой сборки восстановить нельзя. Только отдельный локальный профиль RAID, без лаборатории, сервера и наград парка.';
    hint.appendChild(note); body.appendChild(hint);
  }
  function renderRunHistory() {
    const body = el('history-body'), typeSelect = el('history-type'), section = el('history-section');
    if (!body || !typeSelect || !section) return;
    const stored = readRunHistory(); body.innerHTML = '';
    typeSelect.parentElement.hidden = section.value !== 'records';
    const achievementFilter = el('achievement-filter');
    if (achievementFilter) achievementFilter.parentElement.hidden = section.value !== 'achievements';
    const title = el('history-title'); if (title) title.textContent = section.value === 'achievements' ? 'Достижения' : 'Рекорды и статистика';
    if (!stored) return;
    const data = stored.data;
    if (section.value === 'achievements') { renderAchievements(body, data.achievements.progress); }
    else if (section.value === 'classes') {
      appendHistoryHint(body, 'Что считается игрой и убийством', 'Учитываются только завершённые забеги RAID, включая Лёгкий. Убийства — счётчик атак (AttackSkull), не сбор Руной, ядом или Калечением. Лучшие счёта — на Обычном и Сложном, без Лёгкого. Уровни и настройки мехов не меняются.');
      for (const cls of CLASSES) {
        const stats = data.classes[cls.id], card = document.createElement('article'); card.className = 'history-record'; card.dataset.classId = cls.id;
        card.innerHTML = `<h4>${cls.name}</h4><p>Завершено: <strong>${stats.games}</strong> · Убийств атаками: <strong>${stats.kills}</strong></p><dl class="history-bests">${stats.best.slice(0, 2).map((best, i) => `<div><dt>${['Обычный', 'Сложный', 'Очень сложный'][i]}</dt><dd>${best}</dd></div>`).join('')}</dl>`;
        body.appendChild(card);
      }
    } else {
      const type = Math.max(0, Math.min(5, Number(typeSelect.value) || 0)), rows = data.records[type];
      appendHistoryHint(body, '3 сложности · правила записи', 'Запись при смерти; замена незавершённого забега не считается игрой. По 10 результатов на каждой из трёх сложностей. Равные очки идут после прежних. Классическое правило: новый минимальный результат всё равно заменяет 10-е место.');
      if (!rows.length) { const empty = document.createElement('p'); empty.textContent = 'В этой категории пока нет завершённых забегов.'; body.appendChild(empty); }
      rows.forEach((row, i) => {
        const card = document.createElement('article'), cls = CLASS_BY_ID[row.classId];
        card.className = 'history-record' + (data.last?.id === row.id ? ' latest' : ''); card.dataset.recordId = row.id;
        const stats = row.pretzel;
        const details = type === 5 ? stats ? `Пересечения: среднее ${(stats.avgTimes10 / 10).toFixed(1).replace('.', ',')} · максимум ${stats.maxCross} · цепочек с очками ${stats.scoringChains}` : 'Статистика пересечений недоступна: забег из сохранения до её добавления.'
          : `XP ${row.xp} · GP ${row.gold} · UP ${row.upgrade}${type === 4 ? ' (каждый ресурс ÷4)' : ''}`;
        card.innerHTML = `<h4><span>${i + 1}. ${cls.name}</span><strong>${row.score} очк.</strong></h4><p>Ход ${row.turn} · ${row.won ? 'Победа' : 'Поражение'} · ${new Date(row.finishedAt).toLocaleDateString('ru-RU')}${type > 3 ? ' · ' + ['Лёгкий', 'Обычный', 'Сложный', 'Очень сложный'][row.difficulty] : ''}</p><p>${details}</p><p class="history-skills">${row.skills.length ? row.skills.map(skill => `${SKILL_BY_ID[skill.id].name} ур.${skill.level}`).join(' · ') : 'Без навыков'}</p>`;
        if (data.last?.id === row.id) { const last = document.createElement('p'); last.className = 'history-latest'; last.textContent = 'Последний записанный результат'; card.appendChild(last); }
        body.appendChild(card);
      });
    }
  }
  function openRunHistory() {
    if (HISTORY_OPEN) return true;
    if (S?.resolving || S?.pendingModal && S.pendingModal !== 'gameover') { flash('Сначала заверши ход и выбор наград'); return false; }
    const wrap = el('history-modal'); if (!wrap) return false;
    HISTORY_FOCUS = document.activeElement; HISTORY_OPEN = true;
    renderRunHistory(); wrap.hidden = false; wrap.classList.add('show'); el('history-title')?.focus(); return true;
  }
  function closeRunHistory() {
    HISTORY_OPEN = false;
    const wrap = el('history-modal'); if (wrap) { wrap.classList.remove('show'); wrap.hidden = true; }
    HISTORY_FOCUS?.focus(); HISTORY_FOCUS = null;
  }

  // Original TileType order is POTION(0), SKULL(1), COIN(2), SHIELD(3), SWORD(4),
  // so a refill tile is a skull 1 time in 5 — the "20% of new tiles" rule.
  function rollTileType() {
    const t = randInt(0, 4, 1);
    return t === 0 ? 'potion' : t === 1 ? 'skull' : t === 2 ? 'coin' : t === 3 ? 'shield' : 'sword';
  }
  // Resource tiles only (used where a skull must not appear).
  function randomTileType() {
    let t = rollTileType();
    while (t === 'skull') t = rollTileType();
    return t;
  }

  // Клетка с возможной сломанностью щита (Rogue Flaw: 3%)
  // Original RandomiseTile: R(1,100)<4 breaks 3% of shields; <6 makes
  // 5% of potions mana potions / 5% of swords arrows.
  // Perk/flaw decorations of a freshly generated resource tile.
  function decorateTile(t) {

    if (t === 'shield' && hasFlaw('Clumsy') && randInt(1, 100, 1) < 4) {
      S.brokenShields++;
      return { type: 'shield', broken: true };
    }
    if (t === 'potion' && hasPerk('Gifted') && randInt(1, 100, 1) < 6) {
      return { type: 'potion', mana: true };
    }
    if (t === 'sword' && hasPerk('Agile') && randInt(1, 100, 1) < 6) {
      return { type: 'arrow' };
    }
    return { type: t };
  }

  function createTile() {
    return decorateTile(randomTileType());
  }

  // Promote uses the TILE stream: any of the 32 types, then a mortal-enemy
  // override (including a rolled boss). Explicit makeSkull typeId pins a fixture.
  function randSpecialType() {
    let type = randInt(1, 32, 1);
    const cls = S.classId ? CLASS_BY_ID[S.classId] : null;
    if (cls && cls.id !== 'adventurer' && randInt(1, 200, 1) <= (hasPerk('Intimidating') ? 5 : 10)) {
      type = MORTAL_BY_RACE[clsRace()];
    }
    return type;
  }

  function makeSkull(boss, special, typeId) {

    const effType = boss ? 1 : special ? (typeId != null ? typeId : randSpecialType()) : 0;
    boss = effType === 1;
    special = effType > 1;
    // Оригинал (Tile::GenerateStats @0x63098, Classic + Normal):
    //   level = trunc(totalXP/100);  q = trunc(t · max(1, 1+(t−500)/700));
    //   level > 40 → q = trunc(q·(1+(level−40)/60))
    // Обычные черепы — 1⚔️·0🛡·4❤️ у старта, рост ~q/65…/55 атк, q/50…/40 броня, q/20…/15 HP.
    const turn = S.turn;
    const difficulty = S.mode === 'pretzel' ? 1 : (S.difficulty || 0);
    const totalXp = (S.hero && S.hero.xpLevelCum) || 0;
    const level = Math.floor((S.mode === 'sprint' ? Math.floor(totalXp / 4) : totalXp) / 100);
    let q = turn;
    if (difficulty !== 0) {
      const k = difficulty === 1 ? 700 : difficulty === 2 ? 750 : 500;
      q = Math.floor(turn * Math.max(1, 1 + (turn - 500) / k));
    }
    if (difficulty !== 0 && level > 40) q = Math.floor(q * (1 + (level - 40) / 60));
    let a, d, h;
    if (difficulty === 0) {
      a = Math.max(1, randInt(Math.floor(q / 80), Math.floor(q / 70)));
      d = Math.max(0, randInt(Math.floor(q / 70), Math.floor(q / 55)));
      h = Math.max(3, randInt(Math.floor(q / 25), Math.floor(q / 20)));
    } else if (difficulty === 2) {
      a = Math.max(1, randInt(Math.floor(q / 50), Math.floor(q / 40)));
      d = Math.max(0, randInt(Math.floor(q / 40), Math.floor(q / 37)));
      h = Math.max(4, randInt(Math.floor(q / 17), Math.floor(q / 12)));
    } else if (difficulty === 3) {
      a = Math.max(1, randInt(Math.floor(q / 40), Math.floor(q / 35)));
      d = Math.max(0, randInt(Math.floor(q / 30), Math.floor(q / 35)));
      h = Math.max(4, randInt(Math.floor(q / 15), Math.floor(q / 10)));
    } else {
      a = Math.max(1, randInt(Math.floor(q / 65), Math.floor(q / 55)));
      d = Math.max(0, randInt(Math.floor(q / 50), Math.floor(q / 40)));
      h = Math.max(4, randInt(Math.floor(q / 20), Math.floor(q / 15)));
    }
    // GenerateStats always consumes the interval draw, even for an ordinary
    // skull. q=0 native float-to-int bounds are unresolved: consume its draw,
    // but do not invent a finite promotion interval for that fixture.
    let promotionInterval = null;
    if (q > 0) {
      const root = Math.fround(Math.sqrt(q));
      promotionInterval = randInt(Math.floor(Math.fround((difficulty >= 2 ? 150 : 200) / root)),
        Math.floor(Math.fround(300 / root)), 1) + (difficulty === 0 ? 5 : 0);
      if (hasFlaw('Zealous')) promotionInterval = Math.max(1, promotionInterval - 1);
    } else {
      random(1);
    }
    // Sprint overrides the three base stats after the normal rolls.
    if (S.mode === 'sprint') {
      a = Math.max(1, randInt(Math.floor(q / 20), Math.floor(q / 12)));
      d = Math.max(0, randInt(Math.floor(q / 10), Math.floor(q / 6)));
      h = Math.max(4, randInt(Math.floor(q / 8), Math.floor(q / 4)));
    }
    let atk = a, def = d, hp = h;
    if (boss || special) {
      const mod = boss ? BOSS_STATS : (ENEMY_STAT_OVERRIDES[effType] || { atk: 2, def: 2, hp: 4 });
      // GenerateStats clamps BASE defence before applying special multipliers;
      // Doppelganger then replaces it with the hero's current armour (even 0).
      atk = Math.floor(a * mod.atk);
      def = effType === 17 ? Math.max(0, S.hero.armor) : Math.max(1, d) * mod.def;
      hp = Math.floor(h * mod.hp);
    }
    // Halfling's mortal enemy (Invisible): while it is alive, every other
    // generated monster gets double armour.
    if (clsRace() === 'Halfling' && S && S.board && effType !== 5
        && S.board.some(c => c && c.type === 'skull' && c.skull && c.skull.type === 5 && c.skull.hp > 0)) {
      def *= 2;
    }
    // Оригинал (Tile::GenerateStats): Sneaky (flaw=3) даёт +1 HP только обычным черепам
    const flawBonusHp = (!boss && !special && hasFlaw('Sneaky')) ? 1 : 0;
    // Оригинал: Noisy (flaw=9) → спец/босс +5% HP
    const noisy = (boss || special) && hasFlaw('Noisy') ? 1.05 : 1;
    hp = Math.floor(Math.fround(Math.fround(hp) * Math.fround(noisy))) + flawBonusHp;
    // Спец/босс: минимальные значения
    if (boss) atk = Math.max(1, atk);
    if (special && effType !== 3 && effType !== 30) atk = Math.max(1, atk); // Golden (3): A=0; Meat Shield (30): trunc(A/2)
    // Orc's mortal enemy (Corrosive): its appearance drops the hero's armour to 0.
    if (special && effType === 12 && clsRace() === 'Orc' && S && S.hero) {
      floatOn(-S.hero.armor, 'armor', '#hud-armor', 'брони');
      S.hero.armor = 0;
      log('☣️ Коррозия появилась: броня героя обнулена!');
    }
    if (S.oneHealthSkulls > 0) hp = 1;
    return {
      boss: !!boss,
      special: !!special,
      type: effType,
      countdown: special && COUNTDOWN_TYPES.has(effType) ? 5 : 0,
      uid: SKULL_UID++,
      art: boss ? BOSS_KEY
        : (special ? SPECIAL_SKULL_KEYS[randInt(0, SPECIAL_SKULL_KEYS.length - 1, 0)] : REGULAR_SKULL_KEY),
      atk: atk,
      def: def,
      hp: hp,
      maxHp: hp,
      promotionInterval,
      enteredTurn: (S && S.turn != null) ? S.turn : 0,
      poison: [0, 0, 0],
    };
  }

  // Compact the board bottom-up (skulls pack with tiles) and refill empty
  // cells with fresh tiles — keeps the board full and empty cells out of the
  // middle of the grid after enemy-phase kills (burn / spikes / cripple).
  function compactBoardAndRefill() {
    const emptySlots = [];
    for (let c = 0; c < COLS; c++) {
      const columnItems = [];
      for (let r = ROWS - 1; r >= 0; r--) {
        const cell = S.board[r * COLS + c];
        if (cell && cell.type !== 'empty') columnItems.push(cell);
      }
      let writeRow = ROWS - 1;
      for (const cell of columnItems) {
        S.board[writeRow * COLS + c] = cell;
        writeRow--;
      }
      for (let r = writeRow; r >= 0; r--) emptySlots.push({ col: c, row: r });
    }
    emptySlots.forEach(slot => {
      S.board[slot.row * COLS + slot.col] = createNewTile();
    });
    if (RESOURCE_BATCH) RESOURCE_BATCH.source0 = S.board[0];
  }

  // guarantee at least one legal chain exists (classic dead-board rule)
  function ensureChainable() {
    // Ситуация «ходов нет» — снимаем заморозку, иначе замороженные клетки
    // останутся недоступными и перемешивание не поможет.
    for (let j = 0; j < CELL_COUNT; j++) {
      const cell = S.board[j];
      if (!cell) continue;
      if (cell.frozen) cell.frozen = false;
      if (cell.type === 'skull' && cell.skull) cell.skull.frozenTurn = 0;
    }
    for (let tries = 0; tries < 30; tries++) {
      if (hasLegalChain(S.board)) return;
      for (let j = 0; j < CELL_COUNT; j++) {
        const cell = S.board[j];
        if (!cell || cell.type === 'skull' || cell.type === 'trophy') continue;
        S.board[j] = { type: randomTileType() };
      }
    }
  }

  // Three effective nodes can require up to five physical nodes when arrows
  // skip resource cells. Test actual path rules, not a type component (Chain
  // wildcards, phylacteries and frozen skulls are not a symmetric type graph).
  function hasLegalChain(board) {
    function extend(path, effective) {
      if (effective >= 3) return true;
      if (path.length >= 5) return false;
      const last = path[path.length - 1];
      for (let slot = 0; slot < CELL_COUNT; slot++) {
        if (path.includes(slot) || !isAdjacent(last, slot) || !canChainSelect(slot, path, board)) continue;
        const ignored = board[last].type === 'arrow' && !['sword', 'arrow', 'skull'].includes(board[slot].type);
        if (extend([...path, slot], effective + (ignored ? 0 : 1))) return true;
      }
      return false;
    }
    return board.some((cell, slot) => canChainSelect(slot, [], board) && extend([slot], 1));
  }

  function setupBoard() {
    let cells = null;
    for (let tries = 0; tries < 30; tries++) {
      cells = new Array(CELL_COUNT).fill(null).map(() => createTile());
      // Original new-game board contains no skulls: enemies enter later with
      // refills and are promoted by the 50-turn counter.
      // compact each column to the bottom
      for (let c = 0; c < COLS; c++) {
        const columnItems = [];
        for (let r = ROWS - 1; r >= 0; r--) {
          const cell = cells[r * COLS + c];
          if (cell && cell.type !== 'empty') columnItems.push(cell);
        }
        let writeRow = ROWS - 1;
        for (const cell of columnItems) {
          cells[writeRow * COLS + c] = cell;
          writeRow--;
        }
      }
      if (hasLegalChain(cells)) break;
    }
    S.board = cells;
  }

  function shuffle(a) {
    for (let i = a.length - 1; i > 0; i--) {
      const j = randInt(0, i, 5);
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  // gradual deterministic ramp: 2 at turn 3, +1 every 6 turns, cap 4
  function skullSpawnCount() {
    const t = S.turn;
    if (t < SPAWN_EVERY_TURNS || t % SPAWN_EVERY_TURNS !== 0 || t % BOSS_EVERY_TURNS === 0) return 0;
    return Math.min(4, 2 + Math.floor((t - SPAWN_EVERY_TURNS) / 6));
  }

  // «спец» череп: появляется каждые SPECIAL_EVERY_TURNS ходов (кроме бонусных ходов с боссом).
  // Классика: трофей за 20..30 убитых спец-черепов (перебрасывается после каждого трофея).
  function specialSpawnCount() {
    const t = S.turn;
    if (t < 2) return 0;
    if (t % BOSS_EVERY_TURNS === 0) return 0;
    if (t % SPECIAL_EVERY_TURNS !== 0) return 0;
    return 1;
  }

  function isTimeToPromote() {
    if (S.promotionRemaining === 0) return true;
    if (S.mode !== 'sprint' || S.promotionRemaining === 200) return false;
    const zealous = hasFlaw('Zealous');
    if ((S.turn - (zealous ? 3 : 4)) % (zealous ? 9 : 10) !== 0) return false;
    S.promotionRemaining = 0;
    return true;
  }

  function createNewTile(options) {
    const exclude = options && options.exclude;
    // Force Coin skips the type draw except during a filtered refill.
    let type = S.forceCoins > 0 && !exclude ? 'coin' : rollTileType();
    if (S.mode === 'sprint' && isTimeToPromote()) type = 'skull';
    while (type === exclude) type = rollTileType();
    if (type === 'skull') {
      const promote = isTimeToPromote();
      const specialType = promote ? randSpecialType() : 0;
      const skull = makeSkull(specialType === 1, specialType > 1, specialType);
      if (promote) {
        checkPromotionAchievement(skull);
        S.promotionRemaining = S.mode === 'sprint' ? 200 : (skull.promotionInterval ?? 0);
        S.specialsPromoted = (S.specialsPromoted || 0) + 1;
      }
      if (S.oneHealthSkulls > 0 && skull.type > 0) queueAchievement(62);
      return noteChainRefill({ type: 'skull', skull });
    }
    return noteChainRefill(decorateTile(type));
  }

  // Gravity: each column compacts its non-empty cells to the bottom (skulls
  // fall with the rest). New tiles fill the top. Skull spawns enter from the
  // top of random columns (like new tiles) and cannot attack this turn
  // (enteredTurn tag).
  function placeNewTiles(options) {
    const skullCount = Math.max(0, (options && options.skullCount) || 0);
    const bossSpawn = !!(options && options.boss);
    const specialCount = Math.max(0, (options && options.specialCount) || 0);
    const skullUnits = skullCount + (bossSpawn ? 1 : 0) + specialCount;

    // 1. compact each column bottom-up
    const emptySlots = [];
    for (let c = 0; c < COLS; c++) {
      const columnItems = [];
      for (let r = ROWS - 1; r >= 0; r--) {
        const cell = S.board[r * COLS + c];
        if (cell && cell.type !== 'empty') columnItems.push(cell);
      }
      let writeRow = ROWS - 1;
      for (const cell of columnItems) {
        S.board[writeRow * COLS + c] = cell;
        writeRow--;
      }
      for (let r = writeRow; r >= 0; r--) emptySlots.push({ col: c, row: r });
    }

    // 2. assign skulls to top slots of random columns (1 skull per column)
    let placed = 0, bossPlaced = false;
    if (skullUnits > 0 && emptySlots.length > 0) {
      const slotsByCol = new Map();
      for (const slot of emptySlots) {
        if (!slotsByCol.has(slot.col)) slotsByCol.set(slot.col, []);
        slotsByCol.get(slot.col).push(slot);
      }
      const cols = shuffle([...slotsByCol.keys()]);
      for (let i = 0; i < skullUnits && i < cols.length; i++) {
        const col = cols[i];
        const slot = slotsByCol.get(col)[0]; // topmost empty slot
        const isBoss = bossSpawn && i === 0;
        const isSpecial = !isBoss && specialCount > 0 && (bossSpawn ? i >= skullCount + 1 : i >= skullCount);
        const skull = makeSkull(isBoss, isSpecial);
        if (isSpecial) skull.special = true;
        skull.maxHp = skull.hp;
        skull.enteredTurn = S.turn;
        S.board[slot.row * COLS + slot.col] = { type: 'skull', skull };
        emptySlots.splice(emptySlots.indexOf(slot), 1);
        placed++;
        if (isBoss) { bossPlaced = true; audioBoss(); }
      }
    }

    // 3. remaining empty slots: new tiles
    for (const slot of emptySlots) {
      S.board[slot.row * COLS + slot.col] = createNewTile();
    }

    if (RESOURCE_BATCH) RESOURCE_BATCH.source0 = S.board[0];
    return { placed, bossPlaced };
  }

  // --- chain rules ----------------------------------------------------------
  // Стрелы (Ranger): особые меч-клетки — идут в одну цепочку с мечами.
  // «Стрелять на 1 клетку» — стреляющие на 1 клетку: череп может быть добавлен
  // в цепочку, если он в 1 клетке от стрелы (1 ячейка зазором).
  function chainTypeForCell(cell) {
    if (!cell) return null;
    if (isPhylacteryCell(cell)) return 'coin';
    if (cell.type === 'skull' || cell.type === 'arrow') return 'sword';
    // Chain tiles (Shock+Awe / Haste) are wildcards and never define a type.
    if (cell.type === 'chain') return S.chainType || 'sword';
    return cell.type;
  }
  function chainAllows(cellType, chainType) {
    if (cellType === 'chain' || chainType === 'chain') return true;
    if (chainType === 'sword') return cellType === 'sword' || cellType === 'arrow' || cellType === 'skull' || cellType === 'chain';
    return cellType === chainType || cellType === 'chain';
  }

  function cellTypeAt(i) {
    return S.board[i].type;
  }

  // стрелка: череп в 1 клетке от стрелы (Chebyshev-расстояние 2 = 1 ячейка зазора)
  function isAdjacentArrow(i, j) {
    const r1 = Math.floor(i / COLS), c1 = i % COLS;
    const r2 = Math.floor(j / COLS), c2 = j % COLS;
    return Math.max(Math.abs(r1 - r2), Math.abs(c1 - c2)) === 2;
  }

  function isPhylacteryCell(cell) {
    return !!cell && cell.type === 'skull' && !!cell.skull.phylactery;
  }

  function canChainSelect(slot, path, board = S.board) {
    const cell = board[slot];
    if (!cell || cell.matched || cell.type === 'empty' || (cell.frozen && cell.type !== 'skull')) return false;
    if (!path.length || cell.type === 'chain') return true;
    if (cell.type === 'trophy') return false;
    const last = board[path[path.length - 1]];
    if (last.type === 'arrow' || last.type === 'chain') return true;
    if (last.type === 'trophy') return false;
    const before = board[path[path.length - 2]];
    if (before && before.type === 'arrow') return !isPhylacteryCell(cell) && ['sword', 'arrow', 'skull'].includes(cell.type);
    if (isPhylacteryCell(cell)) return last.type === 'coin' || isPhylacteryCell(last);
    if (isPhylacteryCell(last)) return cell.type === 'coin';
    const combat = type => ['sword', 'arrow', 'skull'].includes(type);
    return cell.type === last.type || (combat(cell.type) && combat(last.type));
  }

  function effectiveChain(path) {
    return path.filter((slot, index) => index === 0 || S.board[path[index - 1]].type !== 'arrow'
      || ['sword', 'arrow', 'skull'].includes(S.board[slot].type));
  }

  function tryExtendChain(cellIdx) {
    if (!S || HISTORY_OPEN || SAVE_CONFLICT || S.resolving || S.over || S.pendingModal || cellIdx < 0 || cellIdx >= CELL_COUNT) return;
    const chain = S.selected;
    const repeated = chain.indexOf(cellIdx);
    if (repeated >= 0) {
      chain.splice(repeated + 1);
      onChainChanged();
      return;
    }
    if (!canChainSelect(cellIdx, chain) || (chain.length && !isAdjacent(chain[chain.length - 1], cellIdx))) return;
    chain.push(cellIdx);
    S.chainType = chainTypeForCell(S.board[chain[0]]);
    onChainChanged();
  }

  function clearChain() {
    S.selected = [];
    S.chainType = null;
    onChainChanged();
  }

  function onChainChanged() {
    updateScrollLeechPreview();
    queueRunSave();
    renderSelection();
    renderChainLine();
    renderChainTip();
    if (S.selected.length >= 3) audioPick(S.chainType);
  }

  // --- board rendering ------------------------------------------------------
  function cellCenter(idx) {
    const cell = qs(`.cell[data-idx="${idx}"]`);
    if (!cell) return { x: 0, y: 0 };
    const board = qs('#board');
    const bRect = board.getBoundingClientRect();
    const rect = cell.getBoundingClientRect();
    return { x: rect.left - bRect.left + rect.width / 2, y: rect.top - bRect.top + rect.height / 2 };
  }

  function tileSymbol(cell) {
    if (!cell || cell.type === 'empty') return '';
    const start = '<svg viewBox="0 0 48 48" class="ic" aria-hidden="true">', end = '</svg>';
    if (cell.type === 'sword') return start + '<path d="m28 5 12-2-2 12-16 17-6-6Z" fill="#d8ebe5" stroke="#698e89" stroke-width="1.2"/><path d="m34 9-15 19" stroke="#75e5cb" stroke-width="2"/><path d="m12 24 13 13-4 4L8 28Z" fill="#b79859" stroke="#ebce8b"/><path d="m12 33 4 4-7 8-5-5Z" fill="#526e68"/><circle cx="14" cy="34" r="2" fill="#9cf5d9"/>' + end;
    if (cell.type === 'arrow') return start + '<path d="m35 4 8 1 1 8-9 8-2-6L12 36l-3-3 21-21-5-1Z" fill="#d5e9e4"/><path d="m7 29 12 12-10 3-5-5Z" fill="#c9a769"/><path d="m14 34 20-20" stroke="#7fe5d2" stroke-width="2"/>' + end;
    if (cell.type === 'shield') return start + '<path d="M24 3 42 11v14c0 10-10 17-18 21C16 42 6 35 6 25V11Z" fill="#a58d57" stroke="#dec692" stroke-width="1.5"/><path d="m24 8 13 6v11c0 7-6 12-13 16-7-4-13-9-13-16V14Z" fill="#2b5c69" stroke="#92cbd4"/><path d="M24 11v27m-10-21 20 13m0-13L14 30" stroke="#6cb0ba" stroke-width="1.2"/><path d="m24 17 7 7-7 9-7-9Z" fill="#b4f1e9"/>' + end;
    if (cell.type === 'coin') return start + '<path d="m21 3 6 0 2 4 5-1 4 5-1 5 5 3v9l-5 2 1 5-5 4-5-1-3 5h-6l-2-4-5 1-4-5 1-5-5-3v-7l5-2-1-5 5-4 5 1Z" fill="#b58c46" stroke="#ebce84"/><circle cx="24" cy="24" r="14" fill="#dfbb63" stroke="#f8dfa1"/><circle cx="24" cy="24" r="10" fill="#a97c37" stroke="#f3d58d"/><path d="m24 15 7 9-7 9-7-9Z" fill="#f9dda0"/><path d="M21 21h6v6h-6Z" fill="#b78a47"/>' + end;
    if (cell.type === 'potion') {
      const liquid = cell.acid ? '#c6e86f' : cell.mana ? '#7cb9f1' : '#e5819c';
      return start + `<path d="M18 4h12v5H18Z" fill="#bb9e66" stroke="#ead8a7"/><path d="M19 9v9L9 31c-5 8 2 13 15 13s20-5 15-13L29 18V9Z" fill="#aacbc9" fill-opacity=".28" stroke="#b6d4cf" stroke-width="1.5"/><path d="M14 29h20l3 6c2 4-4 6-13 6s-15-2-13-6Z" fill="${liquid}"/><path d="M15 30q9-4 18 0" stroke="#fff" stroke-opacity=".5"/><path d="M16 23 13 30" stroke="#fff" stroke-opacity=".7" stroke-width="2" stroke-linecap="round"/>` + (cell.mana ? '<path d="m24 27 3 5-3 5-3-5Z" fill="#fff"/>' : cell.acid ? '<path d="m20 30 4 5 4-5" stroke="#364f27" stroke-width="2" fill="none"/>' : '<path d="M24 30v8m-4-4h8" stroke="#ffe1e7" stroke-width="2"/>') + end;
    }
    if (cell.type === 'trophy') return start + '<path d="M15 6h18v13c0 7-4 11-9 11s-9-4-9-11ZM15 10H6v9c0 5 4 8 10 8m17-17h9v9c0 5-4 8-10 8" fill="#c5a25a" stroke="#f7db8d" stroke-width="2"/><path d="M21 30h6v9h8v5H13v-5h8Z" fill="#e2c276"/><path d="m24 11 5 7-5 7-5-7Z" fill="#a7f3d9"/>' + end;
    if (cell.type === 'chain') return start + '<path d="m21 9-9 9a9 9 0 0 0 13 13l4-4m-10-6-4 4a9 9 0 0 0 13 13l9-9a9 9 0 0 0-13-13Z" stroke="#a6dce0" stroke-width="5" fill="none"/><path d="m17 30 14-14" stroke="#e9d597" stroke-width="3"/>' + end;
    return start + '<circle cx="24" cy="24" r="17" fill="#607c73"/><path d="m14 19 7 2-3 7-6-2m22-7-7 2 3 7 6-2M20 34h8" stroke="#142826" stroke-width="3" fill="none"/>' + end;
  }

  function renderCell(cell, idx) {
    const cellEl = qs(`.cell[data-idx="${idx}"]`);
    if (!cellEl) return;
    const isSkull = !!(cell && cell.type === 'skull');
    // state key: render the cell fresh only when its content identity changes —
    // skull cells reuse their <img> and badge across turns; only the numbers move
    const key = !cell || cell.type === 'empty'
      ? 'empty'
      : isSkull
        ? `skull:${cell.skull.uid}`
        : `tile:${cell.type}:${cell.broken ? 'b' : ''}${cell.acid ? 'a' : ''}${cell.mana ? 'm' : ''}${cell.burn ? 'f' : ''}${cell.frozen ? 'z' : ''}`;
    if (isSkull) {
      const s = cell.skull;
      const boss = !!s.boss;
      const special = !!s.special && !boss;
      let img = SKULL_IMAGES.get(s.uid);
      if (!img) {
        img = document.createElement('img');
        SKULL_IMAGES.set(s.uid, img);
      }
      const portrait = raidPortraitKey(s);
      if (img.dataset.portrait !== portrait) { img.src = raidPortrait(s); img.dataset.portrait = portrait; }
      img.className = 'skull-art' + (boss ? ' boss' : special ? ' special' : '');
      img.alt = boss ? 'Босс' : special ? 'Спец-череп' : 'Череп';
      const keyChanged = cellEl._renderKey !== key;
      if (keyChanged) {
        // cell is now showing a different skull (or a skull replacing a tile) —
        // drop stale tile art and any other skull's art; keep a badge element if any
        Array.from(cellEl.children).forEach(child => {
          if (child === img) return;
          if (child.classList && (child.classList.contains('skull-art') || child.classList.contains('skull-badge'))) {
            if (child.classList.contains('skull-art')) child.remove(); // different skull's art — keep only this uid's img
            return;
          }
          child.remove(); // stale tile svg
        });
        cellEl.className = 'cell tile type-skull' + (boss ? ' boss' : special ? ' special' : '');
        cellEl._renderKey = key;
      }
      // the art is one <img> per skull (uid-keyed) — appending moves it, no re-creation
      cellEl.classList.toggle('frozen', !!cell.frozen || !!s.stunned);
      const crippleTarget = clsRace() === 'Werewolf' && isCrippleTarget(s);
      cellEl.classList.toggle('cripple-target', !!crippleTarget);
      cellEl.title = crippleTarget
        ? `🐺 Калечение: HP ${s.hp} < собственная защита ${s.def}; будет собран в конце хода с наградой опытом.`
        : '';
      if (img.parentNode !== cellEl) cellEl.appendChild(img);
      // badge: reuse the same element — only the numbers change in place
      let badge = cellEl.querySelector('.skull-badge');
      if (!badge) {
        badge = document.createElement('div');
        cellEl.appendChild(badge);
      }
      badge.className = 'skull-badge' + (boss ? ' boss' : special ? ' special' : '');
      const text = (boss ? 'Б ' : special ? '◆ ' : '') + `${s.atk}·${s.def}·${s.hp}` + (s.countdown > 0 ? ` [${s.countdown}]` : '');
      if (badge.textContent !== text) badge.textContent = text;
      return;
    }
    if (cellEl._renderKey === key) return;
    cellEl._renderKey = key;
    cellEl.className = 'cell';
    cellEl.innerHTML = '';
    if (!cell || cell.type === 'empty') {
      cellEl.className = 'cell empty';
      return;
    }
    cellEl.className = 'cell tile type-' + cell.type
      + (cell.broken ? ' broken' : '')
      + (cell.acid ? ' acid' : '')
      + (cell.mana ? ' mana' : '')
      + (cell.burn ? ' burned' : '')
      + (cell.frozen ? ' frozen' : '');
    cellEl.innerHTML = tileSymbol(cell);
    cellEl.title = cell.type === 'trophy' ? (cell.frozen ? '🏆 Трофей заморожен — сначала разморозь' : '🏆 Нажми: рост или открытие меха, без хода врагов') : '';
  }

  function renderBoard() {
    const board = qs('#board');
    if (!board) return;
    if (!board.dataset.cellsReady) {
      board.innerHTML = '';
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('id', 'chain-line');
      svg.setAttribute('aria-hidden', 'true');
      board.appendChild(svg);
      for (let i = 0; i < CELL_COUNT; i++) {
        const cellEl = document.createElement('div');
        cellEl.className = 'cell';
        cellEl.dataset.idx = String(i);
        board.appendChild(cellEl);
      }
      board.dataset.cellsReady = '1';
    }
    // transient pop animation: only for cleared cells on the tiles — drop the class
    // at the start of every render so surviving skulls never stay faded/invisible
    qsa('.cell').forEach(c => c.classList.remove('pop'));
    for (let i = 0; i < CELL_COUNT; i++) {
      renderCell(S.board[i], i);
    }
    renderSelection();
    renderChainLine();
  }

  // Rune uses row parity at CHAIN NODES, not an arbitrary polygon fill. Only
  // rows 1..4 and columns 0..4 are scanned, and a right-hand boundary is needed.
  // A selected node flips parity when its neighbours straddle the row's upper
  // half-plane. This also handles horizontal runs without inventing crossings.
  function interiorCells(chain) {
    if (!chain || chain.length < 4 || !isAdjacent(chain[0], chain[chain.length - 1])) return [];
    const positions = new Map(chain.map((slot, index) => [slot, index]));
    const interior = [];
    for (let row = 1; row < ROWS - 1; row++) {
      const rightEdge = Math.max(-1, ...chain.filter(slot => Math.floor(slot / COLS) === row).map(slot => slot % COLS));
      let inside = false;
      for (let col = 0; col < COLS - 1 && col <= rightEdge; col++) {
        const slot = row * COLS + col;
        if (positions.has(slot)) {
          const index = positions.get(slot);
          const previous = chain[(index + chain.length - 1) % chain.length];
          const next = chain[(index + 1) % chain.length];
          const fromAbove = Math.floor(previous / COLS) < row;
          const toAbove = Math.floor(next / COLS) < row;
          if (fromAbove !== toAbove) inside = !inside;
        } else if (inside && col < rightEdge) interior.push(slot);
      }
    }
    return interior;
  }

  // --- классические формулы урона ---
  function baseDamage() {
    // Классика: урон = база (+Сила + 2×Урон + Ускорение) + оружие × (число мечей)

    const raw = BASE_DAMAGE + S.hero.stats.strength + 2 * S.hero.stats.damage + (hasPerk('Strong') ? 1 : 0) + (S.baseDamageBonus || 0);
    // Mortal enemy Frozen: пока он на доске, базовый урон героя усечён вдвое.
    if (clsRace() === 'Dragonkin' && hasMortalEnemy(8)) return Math.floor(raw / 2);
    return raw;
  }

  // Original GetScore @0x2f868: classic counts collected XP, gold and upgrade
  // points plus one point per turn and 100 per skill level; Sprint divides the
  // three resources by 4 (and doubles everything on a win); Pretzel uses its own
  // pretzel points.
  function skillLevelPoints() {
    return (S.skills || []).reduce((sum, id) => sum + (((S.skillLevels && S.skillLevels[id]) || 1) * 100), 0);
  }
  function runScore() {
    const xp = Math.round(S.hero.xpLevelCum || 0);
    const gold = Math.round(S.goldEarned || 0);
    const up = Math.round(S.upgradeEarned || 0);
    if (S.mode === 'pretzel') return Math.round(S.pretzelScore || 0);
    if (S.mode === 'sprint') {
      const base = Math.floor(xp / 4) + Math.floor(gold / 4) + Math.floor(up / 4);
      return base * (S.won ? 2 : 1);
    }
    return xp + gold + up + S.turn + skillLevelPoints();
  }

  // Confused flaw: no chain damage for the first five turns.
  function isEarlyGame() { return (S.turn || 0) < EARLY_TURNS; }
  function damageBlockedEarly() {

    return hasFlaw('Confused') && isEarlyGame();
  }

  // Pактический враг расы присутствует на доске (mortal enemy).
  function hasMortalEnemy(type) {
    if (!S || !S.board) return false;
    return S.board.some(c => c && c.type === 'skull' && c.skull && c.skull.type === type && c.skull.hp > 0);
  }
  function swordDamage(swordCount) {
    if (damageBlockedEarly()) return 0;   // Confused: no damage in the first 5 turns
    // Boost Damage doubles the sword contribution, not the base damage.
    return Math.round(baseDamage() + S.hero.weaponDamage * swordCount * (S.swordBoost > 0 ? 2 : 1));
  }

  function chainPreview() {
    const chain = S.selected;
    if (!chain || !chain.length) return null;
    const effective = effectiveChain(chain);
    const skulls = effective.filter(idx => S.board[idx].type === 'skull' && !isPhylacteryCell(S.board[idx]))
      .map(idx => ({ idx, skull: S.board[idx].skull }));
    const swordCount = effective.reduce((sum, idx) => sum + (S.board[idx].broken ? 0 : swordWeight(S.board[idx])), 0);
    const damage = damageBlockedEarly() ? 0
      : (skulls.length ? baseDamage() : 0) + S.hero.weaponDamage * swordCount * (S.swordBoost > 0 ? 2 : 1);
    for (const target of skulls) target.damage = chainDamageFor(target.idx, chain, damage);
    const lethals = skulls.filter(target => target.skull.type !== 32 && target.damage >= target.skull.hp + target.skull.def);
    const interior = S.runeActive ? interiorCells(chain) : [];
    // Base-resource projection only: no RNG, no menu selection assumptions.
    // Extra tokens, special rewards and new Corrosive spawns are not predicted.
    const projected = { ...S, hero: { ...S.hero, stats: { ...S.hero.stats } }, board: [...S.board],
      resourceCarry: { ...S.resourceCarry }, skills: [...S.skills], skillCooldowns: { ...S.skillCooldowns } };
    const resources = collectionResult(), tokens = [], removed = new Set();
    let specialRewards = false, acid = 0, mana = 0;
    function collect(slot, allowSpecial = false) {
      const cell = S.board[slot];
      if (!cell || cell.type === 'empty' || removed.has(slot)) return;
      if (cell.type === 'skull' && (cell.skull.special || cell.skull.boss) && !allowSpecial) return;
      removed.add(slot);
      const blast = cell.type === 'skull' && cell.skull.type === 10 || cell.type === 'potion' && cell.explosive;
      if (blast) for (const next of columnSlots()) {
        if (isAdjacent(slot, next) && S.board[next].type !== 'skull') collect(next);
      }
      if (cell.broken) return;
      if (cell.type === 'skull' && (cell.skull.special || cell.skull.boss)) { specialRewards = true; return; }
      if (blast) return;
      const type = cell.type === 'skull' ? 'xp' : cell.type;
      if (!['xp', 'coin', 'shield', 'potion'].includes(type)) return;
      if (cell.type === 'potion' && cell.mana) { mana++; return; }
      tokens.push({ type, variant: cell.type === 'potion' && cell.acid ? 2 : 0 });
    }
    for (const slot of interior) collect(slot);
    const cocky = hasFlaw('Cocky') && chain.length <= 3;
    for (const slot of effective) {
      const cell = S.board[slot];
      if (cell.type === 'skull' && !isPhylacteryCell(cell)) {
        if (lethals.some(target => target.idx === slot)) collect(slot, true);
        if (S.stealActive > 0) tokens.push({ type: 'coin', variant: 0 });
      } else if (!cocky) collect(slot, true);
    }
    for (const slot of removed) projected.board[slot] = { type: 'empty' };
    for (const token of tokens) {
      const before = projected.hero.hp;
      const delta = addResource(token, projected);
      if (token.variant === 2) acid += before - projected.hero.hp;
      for (const key of ['xpGained', 'goldGained', 'upGained', 'armorGained', 'healGained']) resources[key] += delta[key];
    }
    return { kind: S.chainType, count: chain.length, effectiveLength: effective.length, damage,
      swordCount, skulls, lethals, interior, resources, specialRewards, acid, mana };
  }

  function renderChainTip() {
    const tip = el('chain-tip');
    if (!tip) return;
    const preview = chainPreview();
    if (el('chain-count')) el('chain-count').textContent = 'Цепь ' + (preview ? preview.effectiveLength : 0);
    if (!preview || (preview.effectiveLength < 3 && !preview.interior.length)) {
      tip.classList.remove('show'); tip.textContent = ''; return;
    }
    const parts = [], res = preview.resources;
    if (preview.swordCount || preview.skulls.length) parts.push(`Урон ${preview.damage}`, `Убито ${preview.lethals.length}/${preview.skulls.length}`);
    if (res.armorGained) parts.push(`+${res.armorGained} брони`);
    if (res.upGained) parts.push(`+${res.upGained} UP`);
    if (res.goldGained) parts.push(`+${res.goldGained} золота`);
    if (res.xpGained) parts.push(`+${res.xpGained} XP`);
    if (res.healGained) parts.push(`+${res.healGained} HP`);
    if (preview.acid) parts.push(`−${preview.acid} HP`);
    if (preview.mana) parts.push(`${preview.mana} маны`);
    if (preview.specialRewards) parts.push('награда особых — случайная');
    if (preview.interior.length) parts.push(`Руна внутри: ${preview.interior.length} (особые остаются)`);
    if (preview.effectiveLength < 3) parts.push('без хода врагов');
    tip.classList.add('show');
    tip.innerHTML = `<b>${preview.effectiveLength}</b> клеток · ${parts.join(' · ') || 'сбор без награды'}`;
    tip.title = 'Базовые эффекты без случайных бонусов и наград особых; новое поле и выбор в меню могут изменить итог.';
  }

  function renderSelection() {
    qsa('.cell').forEach(c => c.classList.remove('selected'));
    qsa('.cell').forEach(c => c.classList.remove('lethal', 'rune-inside', 'rune-protected'));
    S.selected.forEach(idx => {
      const cellEl = qs(`.cell[data-idx="${idx}"]`);
      if (cellEl) cellEl.classList.add('selected');
    });
    if (S.runeActive) for (const idx of interiorCells(S.selected)) {
      const node = qs(`.cell[data-idx="${idx}"]`), cell = S.board[idx];
      if (!node) continue;
      node.classList.add('rune-inside');
      node.classList.toggle('rune-protected', cell.type === 'skull' && (cell.skull.special || cell.skull.boss));
    }
    // lethal skulls in the chain: highlight + cross out
    if (S.selected.length >= 3) {
      const p = chainPreview();
      if (p && p.skulls && p.skulls.length > 0) {
        p.lethals.forEach(x => {
          const cellEl = qs(`.cell[data-idx="${x.idx}"]`);
          if (cellEl) cellEl.classList.add('lethal');
        });
      }
    }
  }

  function renderChainLine() {
    const svg = qs('#chain-line');
    if (!svg) return;
    svg.innerHTML = '';
    if (S.selected.length < 2) { svg.innerHTML = ''; return; }
    const pts = S.selected.map(cellCenter);
    const d = pts.map((p, i) => (i === 0 ? 'M' : 'L') + p.x.toFixed(1) + ' ' + p.y.toFixed(1)).join(' ');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', d);
    path.setAttribute('class', 'line');
    svg.appendChild(path);
    // glow under layer
    const glow = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    glow.setAttribute('d', d);
    glow.setAttribute('class', 'line-glow');
    svg.insertBefore(glow, path);
    // rune: closed chain → dashed line back to the first cell
    const first = S.selected[0], last = S.selected[S.selected.length - 1];
    if (S.runeActive && S.selected.length > 3 && isAdjacent(first, last)) {
      const p0 = pts[0], pn = pts[pts.length - 1];
      const closing = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      closing.setAttribute('d', 'M' + pn.x.toFixed(1) + ' ' + pn.y.toFixed(1) + ' L' + p0.x.toFixed(1) + ' ' + p0.y.toFixed(1));
      closing.setAttribute('class', 'line-close');
      svg.appendChild(closing);
    }
  }

  // --- HUD ------------------------------------------------------------------
  function fmt(n) { return String(Math.max(0, Math.round(n))); }

  function perShield() {
    return S.armorPerShield + S.hero.stats.dexterity + S.hero.stats.defense * 2;
  }

  function bonusPct(kind, baseIncrease = 0) {
    // шанс бонуса (классика): 20% + 5% за соответствующий стат + 5% за хитрость
    const base = BONUS_BASE_CHANCE;
    const luck = S.hero.stats.luck, dex = S.hero.stats.dexterity, vit = S.hero.stats.vitality, str = S.hero.stats.strength, cha = S.hero.stats.charisma;
    const per = { coin: luck, shield: dex, potion: vit, xp: str }[kind] || 0;

    const offset = kind === 'coin' && hasPerk('Frugal') ? 10
      : kind === 'shield' && hasFlaw('Reckless') ? -10 : 0;
    const key = { coin: 'luck', shield: 'dexterity', potion: 'vitality', xp: 'strength' }[kind];
    const equipment = S.equipmentApplied?.[key] || 0;
    return Math.min(100, Math.round((base + .05 * (per - equipment) + .05 * cha) * 100) + offset + baseIncrease) + 5 * equipment;
  }

  function renderHUD() {
    const h = S.hero;
    const hpPct = Math.max(0, h.hp / h.maxHp * 100);
    const armorPct = Math.max(0, h.armor / h.maxArmor * 100);
    const xpPct = Math.min(100, h.xp / h.xpNext * 100);
    const goldPct = Math.min(100, h.gold / GOLD_BOX_MAX * 100);
    const w = el('hp-bar'); if (w) w.style.width = hpPct + '%';
    const a = el('armor-bar'); if (a) a.style.width = armorPct + '%';
    const x = el('xp-bar'); if (x) x.style.width = xpPct + '%';
    const g = el('gold-bar'); if (g) g.style.width = goldPct + '%';
    if (el('hp-text')) el('hp-text').textContent = `${fmt(h.hp)} / ${fmt(h.maxHp)}`;
    if (el('armor-text')) el('armor-text').textContent = `${fmt(h.armor)} / ${fmt(h.maxArmor)}`;
    if (el('xp-text')) el('xp-text').textContent = `Ур. ${h.level} · ${fmt(h.xp)} / ${fmt(h.xpNext)}`;
    if (el('gold-text')) el('gold-text').textContent = `${fmt(h.gold)} / ${GOLD_BOX_MAX}`;
    const damageLabel = el('dmg-text');
    if (damageLabel) {
      const weapon = h.weaponDamage * (S.swordBoost > 0 ? 2 : 1);
      damageLabel.textContent = `Меч ${fmt(weapon)} · база ${fmt(baseDamage())}`;
      damageLabel.title = `Урон цепочки: база ${fmt(baseDamage())} + ${fmt(weapon)} за каждый меч; защита врага поглощает урон до HP.`;
    }
    if (el('turn-text')) el('turn-text').textContent = `ХОД ${S.turn}`;
    if (el('status-line')) {
      const score = runScore();
      S.score = score;
      el('status-line').textContent = `Ход ${S.turn} · Уровень ${h.level} · Очки ${score}`;
    }
    if (el('mode-badge')) {
      const modeName = 'RAID';
      const diffName = ['Лёгкий', 'Обычный', 'Сложный', 'Очень сложный'][S.difficulty || 0];
      el('mode-badge').textContent = modeName + ' · ' + diffName;
    }
    if (el('chain-count')) el('chain-count').textContent = 'Цепь ' + (S.selected ? S.selected.length : 0);
    if (el('trophy-text')) {
      const onBoard = S.board.filter(cell => cell.type === 'trophy').length;
      const text = onBoard ? 'на поле: ' + onBoard : S.difficulty === 0 ? 'не на лёгком'
        : S.trophiesGenerated >= MAX_TROPHIES_PER_RUN ? 'лимит 3/3'
        : !trophyEligible() ? 'рост закрыт'
        : 'через ' + (S.mode === 'sprint' && S.trophyRemaining > 0 ? '≥' : '') + Math.max(1, S.trophyRemaining);
      el('trophy-text').textContent = 'Трофей ' + text;
      el('trophy-text').title = 'Тап по трофею на поле — рост меха без хода врагов. Не больше 3 появлений за забег; интервал 20–30 переносится между забегами. На Лёгком трофеи не появляются.';
    }
    // upgrade bar
    const u = el('upgrade-bar'); if (u) u.style.width = Math.min(100, S.upgradeBar / UPGRADE_BAR_MAX * 100) + '%';
    if (el('upgrade-text')) el('upgrade-text').textContent = `${fmt(S.upgradeBar)} / ${UPGRADE_BAR_MAX}`;
    // skills
    renderSkills();
    syncTelegramChrome();
  }

  function renderSkills() {
    const row = el('skills');
    if (!row) return;
    row.innerHTML = '';
    // Always show four slots, like the original interface.
    for (let i = 0; i < 4; i++) {
      const skillId = S.skills[i];
      const skill = skillId ? SKILL_BY_ID[skillId] : null;
      if (!skill) {
        const empty = document.createElement('button');
        empty.type = 'button';
        empty.className = 'skill empty';
        empty.dataset.slot = String(i);
        empty.disabled = true;
        empty.innerHTML = '<span class="skill-icon skill-slot-empty" aria-hidden="true">+</span><span class="skill-name">пусто</span>';
        empty.title = 'Слот навыка откроется на уровне 2';
        row.appendChild(empty);
        continue;
      }
      const left = S.skillCooldowns[skillId] || 0;
      const level = (S.skillLevels && S.skillLevels[skillId]) || 1;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'skill' + (left > 0 ? ' on-cd' : '');
      btn.dataset.slot = String(i);
      btn.innerHTML = `<span class="skill-icon">${raidModuleIcon(skill.id)}</span><span class="skill-name">${skill.name}</span>${left > 0 ? `<span class="cd">${left}</span>` : (level > 1 ? `<span class="cd">ур.${level}</span>` : '')}`;
      const duration = skillDuration(skillId);
      btn.title = skill.desc + (duration > 0 ? ' · готовность: ' + (duration - left) + '/' + duration : ' · одноразовый') + (level > 1 ? ', уровень ' + level : '');
      btn.addEventListener('click', () => activateSkill(skillId));
      row.appendChild(btn);
    }
  }

  // --- log ------------------------------------------------------------------
  function log(msg) {
    S.log.unshift(msg);
    S.log = S.log.slice(0, 5);
    const wrap = el('log');
    if (wrap) wrap.innerHTML = S.log.map(m => `<div>${m}</div>`).join('');
  }

  // --- main flow -----------------------------------------------------------
  function newGame(seed) {
    const runSeed = Number.isFinite(Number(seed)) ? (Number(seed) >>> 0) : (Math.floor(Date.now() / 1000) >>> 0);
    if (hasPendingProgress()) retryRunHistory();
    RUN_ID++;
    clearFloatEffects();
    SAVE_CONFLICT = false; NEW_RUN_REQUEST = null; FINISHED_RECORD = null;
    cancelTelegramExit(false);
    try { RUN_SAVE_BASE = profileStorage.getItem(RUN_SAVE_KEY); } catch (e) { RUN_SAVE_BASE = undefined; }
    clearTimeout(SAVE_TIMER); SAVE_TIMER = null;
    closeRunHistory();
    for (const id of ['restart-modal', 'menu-modal', 'help-modal']) el(id)?.classList.remove('show');
    activeDrag = false; window.removeEventListener?.('pointermove', onPointerMove);
    closeClassEditor(false);
    pickerOpen = false; pickerGeneration++;
    const picker = el('class-picker'); if (picker) picker.classList.remove('show');
    AFTER_ARRIVALS = null;
    SCHEDULED_STEP = null; clearTimeout(ACTION_TIMER); ACTION_TIMER = null;
    RESOURCE_BATCH = null;
    seedRandom(runSeed);
    const remaining = S ? S.trophyRemaining : readTrophyRemaining();
    const previousLabel = S?.scrollLeechLabel;
    clearTimeout(SCROLL_LABEL_TIMER); SCROLL_LABEL_TIMER = null;
    S = newGameState(); S.historyId = newHistoryId();
    // InitNewGame clears active, not the adjacent label's type/vertices. A page
    // starts fresh unless explicitly resuming a saved run (including history).
    if (previousLabel) S.scrollLeechLabel = { ...previousLabel, active: false, inFlight: false };
    S.trophyRemaining = remaining;
    saveTrophyRemaining();
    const oldModal = el('modal');
    if (oldModal) oldModal.classList.remove('show');
    S.seed = runSeed;
    S.rngSeeds = Array.from(RNG_SEEDS);
    SKULL_UID = 1;
    SKULL_IMAGES.clear();

    // --- class setup: perks/flaws/race (сохраняем мех между перезапусками) ---
    // Mode / difficulty choose the original rule set independently.
    // RAID's endless structure uses the original Classic rules; the trial
    // retains Sprint/Pretzel/Harder unchanged. No new difficulty curve/gate.
    const diffEl = qs('#difficulty-select');
    S.mode = 'classic';
    S.difficulty = Number((diffEl && diffEl.value) || (() => { try { return profileStorage.getItem('raid:difficulty') || '1'; } catch (e) { return '1'; } })());
    S.difficulty = Math.max(0, Math.min(2, Number.isFinite(S.difficulty) ? S.difficulty : 1));
    try { profileStorage.setItem('raid:mode', S.mode); profileStorage.setItem('raid:difficulty', String(S.difficulty)); } catch (e) {}
    S.classId = getSelectedClassId();
    S.classConfig = getClassConfig(S.classId);
    for (const key of ['perks', 'flaws', 'replacements']) Object.freeze(S.classConfig[key]);
    Object.freeze(S.classConfig);
    syncClassAchievements();
    let cls = CLASS_BY_ID[S.classId];
    if (!cls) {       // мех недоступен (заблокирован) — Adventurer как старт
      S.classId = 'adventurer';
      cls = CLASS_BY_ID.adventurer;
    }

    // Pretzel: base HP 100 and HP growth is removed.
    if (S.mode === 'pretzel') { S.hero.maxHp = 100; S.hero.hp = 100; }
    // Perps мехов (fixed at start):
    if (hasFlaw('Slim')) { S.hero.maxArmor = Math.max(1, S.hero.maxArmor - 1); S.hero.armor = Math.min(S.hero.armor, S.hero.maxArmor); }
    if (hasPerk('Tough')) { S.hero.maxHp += 10; S.hero.hp += 10; }   // Warrior perk
    if (hasFlaw('Frail')) { S.hero.maxHp = Math.max(1, S.hero.maxHp - 10); S.hero.hp = Math.min(S.hero.hp, S.hero.maxHp); }  // Mage flaw

    S.equipment = initialEquipment();
    S.equipmentApplied = equipmentTotals();

    // Priest flaw Cautious: pierce threshold 30% instead of the base 50%.
    if (hasFlaw('Cautious')) S.pierce = 0.30;
    // Original promotion counter (Sprint gets a 200 sentinel, not a delay).
    S.promotionRemaining = S.mode === 'sprint' ? 200 : (hasFlaw('Zealous') ? 49 : 50);

    // Уникальный навык меха добавляется по желанию игрока при выборе 2-из-4
    // на уровень (см. chooseLevelOptions) — не сразу при старте забега.

    setupBoard();
    const board = qs('#board');
    if (board) {
      board.innerHTML = '';
      delete board.dataset.cellsReady;
    }
    renderBoard();
    renderHUD();
    renderChainTip();
    log('✦ Новый забег: ' + cls.name + ' спускается в рейд');
    saveRun();
  }

  function isSelectable(cellType) { return cellType !== 'empty'; }

  // State stores turns LEFT, whereas the native skill stores elapsed charge.
  // Retain the total separately so upgrades/Quicken can preserve elapsed charge.
  function skillDuration(id) {
    if (S.skillDurations[id] !== undefined) return S.skillDurations[id];
    const skill = SKILL_BY_ID[id];
    if (!skill) return 0;
    const base = id === 'wish' ? -1 : skill.cooldown;
    const levels = S.mode === 'sprint' ? 4 : (S.skillLevels[id] || 1) - 1;
    return base - levels - S.quicken + (S.cooldownAdjust[id] || 0);
  }
  function prepareSkillOption(id) {
    const skill = SKILL_BY_ID[id], upgrade = S.skills.includes(id);
    const oldDuration = upgrade ? skillDuration(id) : (id === 'wish' ? -1 : skill.cooldown);
    const oldLevel = upgrade ? S.skillLevels[id] || 1 : 0;
    const duration = oldDuration - (upgrade && oldLevel < 10 ? 1 : 0) - (S.mode === 'sprint' ? 4 : 0);
    const charge = upgrade ? Math.min(oldDuration - (S.skillCooldowns[id] || 0), duration) : duration;
    return { ...skill, upgrade, skill, duration, charge,
      level: S.mode === 'sprint' ? 5 : Math.min(10, oldLevel + 1) };
  }
  function installSkillOption(option) {
    const id = option.id, existing = S.skills.includes(id);
    if (!existing && S.skills.length >= 4) return false;
    let duration = option.duration;
    if (!existing) {
      S.skills.push(id);
      const cunning = S.skills.length === 4 && hasPerk('Cunning');
      S.cooldownAdjust[id] = cunning ? -1 : 0;
      duration -= S.quicken + (cunning ? 1 : 0);
    }
    S.skillDurations[id] = duration;
    S.skillCooldowns[id] = Math.max(0, duration - Math.min(option.charge, duration));
    S.skillLevels[id] = option.level;
    return true;
  }
  function isSkillUnlocked(id) {
    if (typeof id !== 'string' || !Object.hasOwn(SKILL_BY_ID, id)) return false;
    const skill = SKILL_BY_ID[id];
    if (!skill || skill.nativeOnly) return false;
    return skill.native < 31 || CLASSES.some(cls => cls.id !== 'adventurer'
      && cls.uniqueSkill === id && classLevelOf(cls.id) === 10);
  }
  function canActivateSkill(id) {
    const cells = S.board.filter(cell => cell && cell.type !== 'empty' && !cell.matched);
    const any = predicate => cells.some(predicate);
    const enemy = cell => cell.type === 'skull';
    const ordinary = cell => enemy(cell) && !cell.skull.special && !cell.skull.boss;
    const potion = cell => cell.type === 'potion' && !cell.acid && !cell.mana && !cell.explosive;
    const plainSword = cell => cell.type === 'sword' && !cell.magicSword && !cell.arrow;
    if (['steal', 'freeze', 'counter', 'berserk'].includes(id)) return any(enemy);
    if (['heal', 'explosive', 'elixir', 'mana'].includes(id)) return any(potion);
    if (id === 'repair') return any(cell => cell.type === 'shield');
    if (['shatter', 'disarm', 'biggame'].includes(id)) return any(ordinary);
    if (['goldtouch', 'scavenge'].includes(id)) return any(cell => cell.type === 'sword' || cell.type === 'arrow');
    if (['treasure', 'dazzle', 'masochism'].includes(id)) return any(cell => cell.type === 'coin');
    if (['exorcise', 'banish'].includes(id)) return any(cell => enemy(cell) && !ordinary(cell));
    if (['magicsword', 'volley'].includes(id)) return any(plainSword);
    if (id === 'explosivearmour') return S.hero.armor > 0 && any(enemy);
    if (id === 'greed') return any(ordinary) && any(cell => cell.type === 'coin');
    return !!SKILL_BY_ID[id];
  }
  function activateTurnFlag(key) {
    S[key] = 1;
    S.nextTurnFlags[key] = S.actionCommitted ? 1 : 0;
  }
  function rotateTurnFlags(keys) {
    for (const key of keys) {
      S[key] = S.nextTurnFlags[key] || 0;
      S.nextTurnFlags[key] = 0;
    }
  }

  function activateSkill(id) {
    if (!S || HISTORY_OPEN || S.over || S.resolving || S.pendingModal || S.selected.length || !ownsRunSlot()) return;
    const left = S.skillCooldowns[id] || 0;
    if (left > 0) return;
    const skill = SKILL_BY_ID[id];
    if (!skill) return;
    S.skillDurations[id] = skillDuration(id);
    S.skillCooldowns[id] = Math.max(0, S.skillDurations[id]);
    S.resolving = true;
    fireBreathOnSkill();
    withResourceBatch(() => {
      applySkill(id);
      if (S.board.some(cell => !cell || cell.type === 'empty')) compactBoardAndRefill();
      if (!S.actionCommitted) markExistingSkullsOld();
      if (!hasLegalChain(S.board)) ensureChainable();
    });
    renderHUD(); renderBoard();
    continueAfterRewards({ phase: 'skill' });
  }

  function fireBreathOnSkill() {
    if (S.classId && clsRace() === 'Dragonkin') {
      // Original Fire Breath (skills.ts @0x29c34): a random ORDINARY enemy is
      // ignited for base damage; specials and the boss are immune.
      const skulls = S.board
        .filter(c => c && c.type === 'skull' && c.skull && !c.skull.special && !c.skull.boss)
        .map(c => c.skull);
      if (skulls.length > 0) {
        const target = skulls[randInt(0, skulls.length - 1, 5)];
        const cell = S.board.find(cell => cell && cell.skull === target);
        if (cell) cell.frozen = false;
        target.stunned = false;
        target.fireDamage = (target.burn ? target.fireDamage || 0 : 0) + baseDamage();
        target.burn = true; target.fireSafe = true;
        flash('🔥 Дыхание дракона: «' + targetArtName(target) + '» подожжен');
      }
    }
  }

  function targetArtName(s) { return s && s.boss ? 'босс' : 'череп'; }

  // --- helpers for native skills -------------------------------------------
  function swordWeight(cell) {
    if (!cell) return 0;
    if (cell.type === 'sword') return cell.magicSword ? 5 : 1;
    if (cell.type === 'arrow') return 1;
    return 0;
  }

  function liveSkullIndices(includeSpecial) {
    const out = [];
    for (let i = 0; i < CELL_COUNT; i++) {
      const c = S.board[i];
      if (c && c.type === 'skull' && c.skull && c.skull.hp > 0) {
        if (includeSpecial || (!c.skull.special && !c.skull.boss)) out.push(i);
      }
    }
    return out;
  }

  // Shared AttackSkull arithmetic: this hit loses max(D - E, 0) HP, then
  // each of min(D, E) armour points gets its own R(0,100)<pierce erosion roll.
  // Direct effects (Orc Stun) are only rolled on survivors, including D=0.
  function damageSkull(skull, amount, opts) {
    opts = opts || {};
    const damage = Math.max(0, Math.round(amount));
    const defence = Math.max(0, skull.def || 0);
    const loss = opts.ignoreDefence ? damage : Math.max(0, damage - defence);
    skull.hp -= loss;
    if (skull.hp <= 0) return loss;
    if (!opts.ignoreDefence) {
      for (let n = Math.min(damage, defence); n > 0; n--) {
        if (randInt(0, 100, 5) < S.pierce * 100) skull.def--;
      }
    }
    if (opts.direct && S.weaponPoison > 0 && skull.type !== 9) {
      skull.poison = (skull.poison || [0, 0, 0]).map(value => value + S.weaponPoison);
    }
    if (opts.direct && clsRace() === 'Orc' && randInt(1, 100, 5) <= 10) {
      queueAchievement(51, 2);
      skull.stunned = true;
      skull.burn = false;
      skull.fireDamage = 0;
      skull.fireSafe = false;
    }
    return loss;
  }

  function hitSkullAt(idx, amount, opts) {
    return withResourceBatch(batch => {
      opts = opts || {};
      const cell = S.board[idx];
      if (!cell || cell.type !== 'skull' || !cell.skull || cell.skull.hp <= 0 || cell.matched) return false;
      const skull = cell.skull;
      if (skull.phylactery) return false;
      if (opts.direct && S.stealActive > 0) queueResource('coin');
      const defence = skull.def;
      const dmg = damageSkull(skull, amount, opts);
      flashOn(idx, -dmg, 'hurt', 'HP');
      flashOn(idx, skull.def - defence, 'armor', 'брони');
      if (skull.hp > 0) return false;
      if (skull.type === 32) {
        // AttackSkull transforms Lich; collection/poison are different paths.
        Object.assign(skull, { phylactery: true, lichDelay: true, countdown: 1,
          atk: 0, def: 0, hp: 1, poison: [0, 0, 0] });
        return false;
      }
      S.skullsKilled++;
      batch.result.kills++;
      if (skull.boss) batch.result.bossKills++;
      batch.berserkHealing += enemyGetAttack(skull);
      matchCell(idx, { reward: opts.reward !== false });
      audioKill();
      return true;
    });
  }

  // Skills and Rune COLLECT ordinary skulls; they do not call AttackSkull.
  // Resource counting, potion variants and explosions are shared with chains.
  function collectIndices(indices, opts) {
    opts = opts || {};
    return withResourceBatch(batch => {
      if (opts.resetCounts !== false) resetCollectionCounts();
      for (const idx of columnSlots().filter(slot => indices.includes(slot))) {
        const cell = S.board[idx];
        if (!cell || cell.type === 'empty' || cell.matched) continue;
        if (cell.type === 'skull' && (opts.noSkulls || (!opts.includeSpecial && (cell.skull.special || cell.skull.boss)))) continue;
        const skip = cell.type === 'potion' && ((opts.skipAcid && cell.acid) || (opts.skipMana && cell.mana));
        matchCell(idx, { reward: !skip, rewardType: opts.rewardType });
      }
      if (!opts.noRefill) {
        compactBoardAndRefill();
        if (!S.actionCommitted) markExistingSkullsOld();
        if (!hasLegalChain(S.board)) ensureChainable();
      }
      return batch.result;
    });
  }

  function columnSlots() {
    return Array.from({ length: CELL_COUNT }, (_, n) => (n % ROWS) * COLS + Math.floor(n / ROWS));
  }
  function markExistingSkullsOld() {
    for (const cell of S.board) if (cell && cell.type === 'skull') cell.skull.enteredTurn = S.turn - 1;
  }

  function fillAfterConversion() {
    compactBoardAndRefill();
    if (!hasLegalChain(S.board)) ensureChainable();
  }

  function applySkill(id) {
    if (id === 'bribe') id = 'steal';
    const rndTile = () => {
      if (S.forceCoins > 0) return { type: 'coin' };
      return createNewTile();
    };

    if (id === 'steal') {
      activateTurnFlag('stealActive');
      flash('🦊 Воровство: атака черепов в этом ходу даёт золото');
    } else if (id === 'freeze') {
      let frozen = 0;
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (c && c.type === 'skull' && c.skull) {
          c.skull.frozenTurn = S.turn;
          c.skull.burn = false; c.skull.fireDamage = 0; c.skull.fireSafe = false;
          c.frozen = true;
          frozen++;
        }
      }
      flash('❄ Заморожено черепов: ' + frozen);
    } else if (id === 'heal') {
      const idxs = [];
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (c && c.type === 'potion' && !c.acid && !c.mana) idxs.push(i);
      }
      collectIndices(idxs, { noSkulls: true, skipAcid: true, skipMana: true });
      flash('❤ Собрано обычных зелий: ' + idxs.length);
    } else if (id === 'repair') {
      const idxs = [];
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (c && c.type === 'shield') idxs.push(i);
      }
      collectIndices(idxs, { noSkulls: true });
      flash('⚒ Собрано щитов: ' + idxs.length + ' — ремонт или UP');
    } else if (id === 'counter') {
      S.counterattack = 1;
      flash('↩ Контратака: черепа бьют себя вместо героя');
    } else if (id === 'shatter') {
      let n = 0;
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (c && c.type === 'skull' && c.skull && !c.skull.special && !c.skull.boss) {
          flashOn(i, -c.skull.def, 'armor', 'брони');
          flashOn(i, Math.min(c.skull.atk, 1) - c.skull.atk, 'buff', '⚔');
          c.skull.def = 0;
          c.skull.atk = Math.min(c.skull.atk, 1);
          n++;
        }
      }
      flash('💥 Раскол: ослаблено обычных черепов — ' + n);
    } else if (id === 'goldtouch') {
      let n = 0;
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        // Original ConvertTiles skips arrows and special/boss skulls.
        if (c && c.type === 'sword' && !c.arrow) { S.board[i] = { type: 'coin' }; n++; }
      }
      flash('🪙 Золотое касание: мечей → монет ' + n);
    } else if (id === 'teleport') {
      if (S.board.some(cell => cell.type === 'skull' && (cell.skull.special || cell.skull.boss))) queueAchievement(65, 1);
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (c && c.type !== 'skull') S.board[i] = rndTile();
      }
      fillAfterConversion();
      flash('✦ Телепорт: клетки без черепов обновлены');
    } else if (id === 'explosive') {
      let n = 0;
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (c && c.type === 'potion' && !c.acid && !c.mana) { c.explosive = true; n++; }
      }
      flash('🧨 Взрывных зелий: ' + n);
    } else if (id === 'elixir') {
      const idxs = columnSlots().filter(i => {
        const cell = S.board[i];
        return cell && cell.type === 'potion' && !cell.acid && !cell.mana && !cell.explosive;
      });
      collectIndices(idxs, { noSkulls: true, rewardType: 'xp' });
      flash('⚗ Эликсир: ' + idxs.length + ' обычных зелий → токены XP');
    } else if (id === 'enchant') {
      requestRewardMenu('armor');
      flash('🛠 Затачивание: выбери улучшение');
    } else if (id === 'slash') {
      achievementCombo(1);
      S.slashCombo = 1;
      let idxs;
      if (S.hackCombo > 0) {
        idxs = [];
        for (let i = 0; i < CELL_COUNT; i++) {
          const c = S.board[i];
          if (c && c.type !== 'skull') idxs.push(i);
        }
        achievementCombo(8);
        flash('🗡 Рассечение + Взлом: собрана вся доска');
      } else {
        const r = randInt(0, ROWS - 1, 5);
        idxs = [];
        for (let c = 0; c < COLS; c++) {
          const i = r * COLS + c;
          if (S.board[i]) idxs.push(i);
        }
        flash('🗡 Рассечение: строка ' + (r + 1));
      }
      if (!S.hackCombo && idxs.filter(i => S.board[i]?.type === 'skull' && !S.board[i].skull.special && !S.board[i].skull.boss).length === 6) queueAchievement(60);
      collectIndices(idxs, { skipAcid: true });
    } else if (id === 'treasure') {
      const idxs = [];
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (c && c.type === 'coin') idxs.push(i);
      }
      collectIndices(idxs, { noSkulls: true });
      flash('💎 Кладовая: собрано монет ' + idxs.length);
    } else if (id === 'scavenge') {
      let n = 0;
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (c && c.type === 'sword' && !c.arrow) { S.board[i] = { type: 'shield' }; n++; }
      }
      flash('🛡 Сбор: мечей → щитов ' + n);
    } else if (id === 'disarm') {
      let n = 0;
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (c && c.type === 'skull' && c.skull && !c.skull.special && !c.skull.boss) {
          S.board[i] = { type: 'sword' };
          n++;
        }
      }
      flash('⚔ Обезоруживание: черепов → мечей ' + n);
    } else if (id === 'dazzle') {
      let n = 0;
      // Original ConvertTiles: skipping special tiles only; coins are not special.
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (c && c.type === 'coin') { S.board[i] = { type: 'sword' }; n++; }
      }
      flash('✦ Слепота: монет → мечей ' + n);
    } else if (id === 'boostdmg') {
      activateTurnFlag('swordBoost');
      flash('⚔ Усиление урона: оружие ×2 в этом ходу');
    } else if (id === 'boostgold') {
      activateTurnFlag('coinBoost');
      flash('🪙 Усиление золота: золото ×2 в этом ходу');
    } else if (id === 'boosthealth') {
      activateTurnFlag('potionBoost');
      flash('❤ Усиление лечения: зелья ×2 в этом ходу');
    } else if (id === 'boostarmour') {
      activateTurnFlag('shieldBoost');
      flash('🛡 Усиление брони: щиты и UP ×2 в этом ходу');
    } else if (id === 'exorcise') {
      const idxs = liveSkullIndices(true).filter(i => S.board[i].skull.special || S.board[i].skull.boss);
      if (idxs.length === 0) { flash('✝ Нет особых черепов'); return; }
      const i = idxs[randInt(0, idxs.length - 1, 5)];
      const s = S.board[i].skull;
      s.special = false;
      s.type = 0;
      s.boss = false;
      s.maxHp = Math.max(1, s.maxHp);
      flash('✝ Изгнание: особый череп стал обычным');
    } else if (id === 'banish') {
      const idxs = liveSkullIndices(true).filter(i => S.board[i].skull.special || S.board[i].skull.boss);
      if (idxs.length === 0) { flash('☄ Нет особых черепов'); return; }
      const i = idxs[randInt(0, idxs.length - 1, 5)];
      S.board[i] = { type: 'empty' };
      flash('☄ Уничтожение: особый череп убран без награды');
      compactBoardAndRefill();
    } else if (id === 'trap') {
      activateTurnFlag('oneHealthSkulls');
      flash('🕸 Ловушка: новые черепа появятся с 1 HP');
    } else if (id === 'biggame') {
      const idxs = liveSkullIndices(false).filter(i => !S.board[i].skull.special && !S.board[i].skull.boss);
      if (idxs.length === 0) { flash('👹 Нет обычных черепов'); return; }
      const i = idxs[randInt(0, idxs.length - 1, 5)];
      const cell = S.board[i];
      const old = cell.skull;
      const fresh = makeSkull(false, true);
      fresh.promotedByBigGame = true;
      fresh.hp = Math.min(fresh.hp, old ? old.hp : fresh.hp);
      fresh.maxHp = fresh.hp;
      fresh.enteredTurn = old && old.enteredTurn != null ? old.enteredTurn : S.turn;
      S.board[i] = { type: 'skull', skull: fresh };
      checkPromotionAchievement();
      flash('👹 Крупная дичь: обычный череп стал особым');
    } else if (id === 'chamber') {
      activateTurnFlag('forceCoins');
      flash('🪙 Сокровищница: новые клетки станут монетами');
    } else if (id === 'magicsword') {
      const idxs = [];
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (c && c.type === 'sword' && !c.magicSword) idxs.push(i);
      }
      if (idxs.length === 0) { flash('✨ Нет подходящих мечей'); return; }
      const i = idxs[randInt(0, idxs.length - 1, 5)];
      S.board[i].magicSword = true;
      flash('✨ Магический меч: случайный меч бьёт как 5');
    } else if (id === 'earthquake') {
      for (let k = 0; k < 36; k++) {
        const a = randInt(0, CELL_COUNT - 1, 5), b = randInt(0, CELL_COUNT - 1, 5);
        if (a === b) continue;
        const tmp = S.board[a]; S.board[a] = S.board[b]; S.board[b] = tmp;
      }
      fillAfterConversion();
      flash('🌋 Землетрясение: поле перемешано');
    } else if (id === 'explosivearmour') {
      const amount = Math.max(0, S.hero.armor) * 2;
      let hit = 0;
      // Original Explosive Armour: AttackSkull(armour × 2) — defence still applies.
      liveSkullIndices(true).forEach(i => { if (hitSkullAt(i, amount)) hit++; });
      flash('🛡 Взрывная броня: ' + amount + ' урона по всем черепам');
    } else if (id === 'fireball') {
      achievementCombo(2);
      // Original Fireball draws both coordinates in 1..4 and collects the whole
      // 3×3 area, enemies included.
      const r = randInt(1, Math.max(1, ROWS - 2), 5), c = randInt(1, Math.max(1, COLS - 2), 5);
      const idxs = [];
      for (let dr = -1; dr <= 1; dr++) for (let dc = -1; dc <= 1; dc++) {
        const nr = r + dr, nc = c + dc;
        if (nr >= 0 && nr < ROWS && nc >= 0 && nc < COLS) {
          const i = nr * COLS + nc;
          if (S.board[i]) idxs.push(i);
        }
      }
      collectIndices(idxs, { skipAcid: true });
      flash('🔥 Огненный шар: область 3×3');
    } else if (id === 'mana') {
      let n = 0;
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (c && c.type === 'potion' && !c.acid) { c.mana = true; n++; }
      }
      flash('🔹 Зелья маны: ' + n);
    } else if (id === 'hack') {
      let idxs;
      if (S.slashCombo > 0) {
        idxs = [];
        for (let i = 0; i < CELL_COUNT; i++) {
          const c = S.board[i];
          if (c && c.type !== 'skull') idxs.push(i);
        }
        achievementCombo(8);
        flash('🪓 Взлом + Рассечение: собрана вся доска');
      } else {
        S.hackCombo = 1;
        const col = randInt(0, COLS - 1, 5);
        idxs = [];
        for (let r = 0; r < ROWS; r++) {
          const i = r * COLS + col;
          if (S.board[i] && S.board[i].type !== 'skull') idxs.push(i);
        }
        flash('🪓 Взлом: столбец ' + (col + 1) + ' → мечи');
        idxs.forEach(i => { S.board[i] = { type: 'sword' }; });
        return;
      }
      collectIndices(idxs, { skipAcid: true });
    } else if (id === 'shock' || id === 'awe') {
      const both = id === 'shock' ? S.aweCombo > 0 : S.shockCombo > 0;
      if (id === 'shock') S.shockCombo = 1; else S.aweCombo = 1;
      if (both) {
        const idxs = [];
        for (let i = 0; i < CELL_COUNT; i++) {
          const c = S.board[i];
          if (c && c.type !== 'skull' && c.type !== 'empty') idxs.push(i);
        }
        if (idxs.length > 0) {
          const pick = idxs[randInt(0, idxs.length - 1, 5)];
          S.board[pick] = { type: 'chain' };
          flash('⚡🌀 Шок + Благоговение: создана цепная клетка');
        }
        return;
      }
      const idxs = [];
      const from = id === 'shock' ? 0 : Math.floor(ROWS / 2);
      const to = id === 'shock' ? Math.floor(ROWS / 2) : ROWS;
      for (let r = from; r < to; r++) for (let c = 0; c < COLS; c++) {
        const i = r * COLS + c;
        const cell = S.board[i];
        if (!cell) continue;
        if (id === 'shock' && (cell.type === 'shield' || cell.type === 'coin')) idxs.push(i);
        if (id === 'awe' && (cell.type === 'shield' || cell.type === 'potion')) idxs.push(i);
      }
      collectIndices(idxs, { noSkulls: true, skipAcid: true });
      flash((id === 'shock' ? '⚡ Шок' : '🌀 Благоговение') + ': собрано клеток ' + idxs.length);
    } else if (id === 'berserk') {
      activateTurnFlag('berserkActive');
      flash('🪓 БЕРСЕРК: HP за убийства этого хода');
    } else if (id === 'rune') {
      activateTurnFlag('runeActive');
      S.runeUsedThisTurn = 0;
      flash('🔮 РУНА: замкни 4+ узла — соберутся ресурсы и обычные черепа внутри');
    } else if (id === 'haste') {
      // Original Haste (skill 39) = CreateChain: up to 10 tries to turn a
      // random non-special cell into a wildcard chain tile.
      let placed = false;
      for (let n = 0; n < 10 && !placed; n++) {
        const idx = randInt(0, CELL_COUNT - 1, 5);
        const cell = S.board[idx];
        if (!cell || cell.type === 'empty') continue;
        // Original CreateChain skips only *special* enemies — ordinary skulls
        // and every resource tile can become the wildcard.
        if (cell.type === 'skull' && cell.skull && (cell.skull.special || cell.skull.boss)) continue;
        S.board[idx] = { type: 'chain' };
        placed = true;
      }
      flash(placed ? '🗡 СПЕШКА: создана цепная клетка (подходит к любому типу)' : '🗡 СПЕШКА: некуда поставить цепную клетку');
    } else if (id === 'greed') {
      let killed = 0;
      const coinCells = [];
      for (let i = 0; i < CELL_COUNT; i++) {
        if (S.board[i] && S.board[i].type === 'coin') coinCells.push(i);
      }
      coinCells.forEach(ci => {
        const r = Math.floor(ci / COLS), c = ci % COLS;
        for (let dr = -1; dr <= 1; dr++) for (let dc = -1; dc <= 1; dc++) {
          if (dr === 0 && dc === 0) continue;
          const nr = r + dr, nc = c + dc;
          if (nr < 0 || nr >= ROWS || nc < 0 || nc >= COLS) continue;
          const j = nr * COLS + nc;
          const cell = S.board[j];
          if (cell && cell.type === 'skull' && !cell.skull.special && !cell.skull.boss) {
            matchCell(j, { reward: false });
            killed++;
            audioKill();
          }
        }
      });
      flash('🗡 Жадность: убрано ' + killed + ' обычных черепов без награды');
    } else if (id === 'laststand') {
      activateTurnFlag('lastStand');
      flash('🛡 Последний рубеж готов: смерть → полное HP');
    } else if (id === 'wish') {
      achievementCombo(8);
      if (liveSkullIndices(false).length >= 20) queueAchievement(64);
      const idxs = [];
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (!c || (c.type === 'skull' && (c.skull.special || c.skull.boss))) continue;
        idxs.push(i);
      }
      collectIndices(idxs);
      if (!S.usedSkillIds) S.usedSkillIds = {};
      S.usedSkillIds.wish = true;
      S.skills = S.skills.filter(sid => sid !== 'wish');
      flash('✨ ЖЕЛАНИЕ: собрана доска, кроме особых и боссов');
    } else if (id === 'masochism') {
      S.achievementRun.masochism = true;
      let skullCount = 0;
      for (let i = 0; i < CELL_COUNT; i++) {
        if (S.board[i] && S.board[i].type === 'coin') {
          const skull = makeSkull(false);
          skull.maxHp = skull.hp;
          skull.enteredTurn = S.turn;
          S.board[i] = { type: 'skull', skull };
          skullCount++;
        }
      }
      flash('🏴 Мазохизм: ' + skullCount + ' монет → черепа');
    } else if (id === 'volley') {
      let converted = 0;
      for (let i = 0; i < CELL_COUNT; i++) {
        const cell = S.board[i];
        if (cell && cell.type === 'sword') { S.board[i] = { type: 'arrow' }; converted++; }
      }
      flash('🏹 Воллей: ' + converted + ' мечей → стрелы');
    } else if (id === 'charge') {
      achievementCombo(4);
      const idxs = [];
      for (let i = 0; i < CELL_COUNT; i++) {
        const r = Math.floor(i / COLS), c = i % COLS;
        if (r === 0 || r === ROWS - 1 || c === 0 || c === COLS - 1) {
          // Original Charge collects the whole rim, enemies included.
          if (S.board[i]) idxs.push(i);
        }
      }
      collectIndices(idxs, { skipAcid: true });
      flash('⚔ Рывок: собран край поля');
    }
  }

  function swordCountFrom(board) {
    let n = 0;
    for (let i = 0; i < board.length; i++) {
      const c = board[i];
      if (c && c.type === 'sword') n += swordWeight(c);
    }
    return n;
  }

  // спец-череп (босс или спец-череп): счётчик для трофея
  function registerSkullEnd(s, slot) {
    if (!s || !(s.boss || s.special)) return;
    S.specialsKilled = (S.specialsKilled || 0) + 1;
    queueAchievement(46, 1);
    if (s.type === 14 && clsRace() === 'Dwarf') queueAchievement(42);
    if (s.type === 3) queueAchievement(44);
    if (advanceTrophyAt(slot) && s.promotedByBigGame) queueAchievement(66);
    // Native charge=cooldown means ZERO turns remaining in our representation.
    // Shapeshift may hit a ready slot (unlike mana) and applies to every special
    // collection path, not just deaths inside a manually selected chain.
    if (clsRace() === 'Changeling') queueAchievement(55, 2);
    if (clsRace() === 'Changeling' && S.skills.length > 0) {
      const pick = S.skills[randInt(0, S.skills.length - 1, 5)];
      S.skillCooldowns[pick] = 0;
      flash('🔮 Смена облика: навык «' + (SKILL_BY_ID[pick] ? SKILL_BY_ID[pick].name : pick) + '» готов');
    }
  }

  function collectionResult() {
    return { xpGained: 0, goldGained: 0, upGained: 0, armorGained: 0,
      healGained: 0, dmgShown: 0, kills: 0, bossKills: 0, manaCollected: 0 };
  }

  function resetCollectionCounts() {
    S.collectionCounts = {};
    if (RESOURCE_BATCH) RESOURCE_BATCH.counts = S.collectionCounts;
  }

  function withResourceBatch(action) {
    if (RESOURCE_BATCH) return action(RESOURCE_BATCH);
    const batch = { counts: S.collectionCounts, events: [], seen: new Set(), source0: S.board[0],
      result: collectionResult(), berserkHealing: 0 };
    RESOURCE_BATCH = batch;
    let value;
    try { value = action(batch); }
    finally { RESOURCE_BATCH = null; }
    if (!S.over) S.arrivals.push(...batch.events);
    drainArrivals();
    return value;
  }

  function queueArrival(event) {
    RESOURCE_BATCH.events.push({ ...event, result: RESOURCE_BATCH.result });
  }

  // This prototype settles arrivals in creation order. Native animation races
  // between resources/leech/fire/spikes (C2) are not claimed to be reproduced.
  // A full bar opens its menu immediately; the remaining events wait unchanged.
  function drainArrivals() {
    if (DRAINING_ARRIVALS || RESOURCE_BATCH || S.pendingModal || S.over || SAVE_CONFLICT) return;
    DRAINING_ARRIVALS = true;
    try {
      while (S.arrivals.length && !S.pendingModal && !S.over) {
        const event = S.arrivals.shift();
        if (event.kind === 'resource') {
          const delta = addResource(event.token);
          for (const key of ['xpGained', 'goldGained', 'upGained', 'armorGained', 'healGained', 'manaCollected']) event.result[key] += delta[key];
        } else if (event.kind === 'healing') {
          heal(event.amount);
          if (event.leech) { S.scrollLeechLabel.inFlight = false; fadeScrollLeechLabel(); }
        } else if (event.kind === 'damage') {
          damageHero(event.amount, false, true, event.cause);
          const borrowed = event.lastStand && !S.lastStand;
          if (borrowed) S.lastStand = 1;
          if (S.hero.hp <= 0 && !lastStandTriggerIfNeeded()) gameOver();
          if (borrowed) S.lastStand = 0;
        }
      }
    } finally { DRAINING_ARRIVALS = false; }
    renderHUD();
    if (!S.pendingModal && !S.over && !S.arrivals.length && AFTER_ARRIVALS) {
      const next = AFTER_ARRIVALS;
      AFTER_ARRIVALS = null;
      runContinuation(next);
    }
  }

  function continueAfterRewards(next) {
    if (S.over) { S.resolving = false; saveRun(); return; }
    AFTER_ARRIVALS = next;
    drainArrivals();
    saveRun();
  }

  // These named steps are also the save/resume boundary. An interrupted menu
  // must not recreate a closure, reroll rewards, or run an enemy volley twice.
  function runContinuation(next) {
    if (S.over || SAVE_CONFLICT) return;
    if (next.phase === 'end-turn') { endEnemyTurn(); return; }
    if (next.phase === 'chain') {
      const result = next.result;
      if (result.xpGained > 0 || result.goldGained > 0) log(`⚔ +${result.xpGained} XP · +${result.goldGained} золота · убийств ${result.kills}`);
      if (next.committed) { scheduleRunStep({ phase: 'enemy' }, 550); return; }
    } else if (next.phase === 'ready' && !hasLegalChain(S.board)) {
      ensureChainable(); log('♻ Нет ходов — поле перемешано');
    }
    S.resolving = false;
    renderBoard(); renderHUD();
    if (next.phase === 'ready') checkTrophy();
  }

  function scheduleRunStep(step, delay) {
    const runId = RUN_ID;
    clearTimeout(ACTION_TIMER);
    SCHEDULED_STEP = step;
    ACTION_TIMER = setTimeout(() => {
      if (runId !== RUN_ID || S.over || !ownsRunSlot()) return;
      SCHEDULED_STEP = null; ACTION_TIMER = null;
      if (step.phase === 'match-chain') settleChain(step.snapshot || chainSnapshot(step.chain));
      else enemyPhase();
    }, delay);
    saveRun();
  }

  function requestRewardMenu(kind) {
    if (S.pendingModal) S.rewardMenus.push(kind);
    else openModal(kind);
  }

  function levelReached(state) {
    state.hero.level++;
    state.maxLevel = Math.max(state.maxLevel, state.hero.level);
    if (state === S) {
      const milestone = [5, 10, 25, 50, 100].indexOf(state.hero.level);
      if (milestone >= 0) queueAchievement(29 + milestone);
      if (state.difficulty === 3 && state.hero.level === 10 && state.classConfig.perks.filter(Boolean).length === 1 && state.classConfig.flaws.filter(Boolean).length === 4) queueAchievement(70);
      if (state.difficulty === 3 && state.hero.level === 5 && !state.skills.length) queueAchievement(71);
    }
    if (clsRace(state) !== 'Faun') return 0;
    const before = state.hero.hp;
    state.hero.hp = Math.max(before, Math.floor(state.hero.maxHp * .25));
    if (state === S && state.hero.hp > before) {
      queueAchievement(50, 5);
      floatOn(state.hero.hp - before, 'heal', '#hud-hp', 'HP');
    }
    return state.hero.hp - before;
  }

  // Token targets are bound at creation, before any arrivals. Even non-potion
  // special rewards inherit board[0]'s variant and can be hidden by no mana target.
  function queueResource(type, source) {
    const token = { type, variant: source && source.type === 'potion' && source.mana ? 1 : source && source.type === 'potion' && source.acid ? 2 : 0 };
    if (token.variant === 1) {
      const targets = (S.skills || []).flatMap((id, slot) => id && id !== 'mana' && (S.skillCooldowns[id] || 0) > 0 ? [slot] : []);
      if (!targets.length) return;
      token.skillSlot = targets[randInt(0, targets.length - 1, 5)];
    }
    queueArrival({ kind: 'resource', token });
  }

  // GetRewards: count TOKEN EVENTS, not points. Preserve the TILE stream and
  // the native (sometimes reversed) integer ranges and ordering.
  function specialRewardTypes(type) {
    let remaining = randInt(15, 25, 1);
    if (type === 3) return Array(remaining).fill('coin');
    const rewards = [];
    if (type === 4 || type === 27) {
      const first = randInt(Math.floor(remaining / 4), Math.floor(remaining / 3), 1);
      rewards.push(...Array(first).fill(type === 4 ? 'potion' : 'shield'));
      remaining -= first;
    }
    const low = Math.floor(remaining / (type === 13 ? 2 : type === 4 || type === 27 ? 4 : 3));
    const coins = randInt(Math.floor(3 * remaining / 4), low, 1);
    return rewards.concat(Array(coins).fill('coin'), Array(remaining - coins).fill('xp'));
  }

  function matchCell(idx, opts) {
    opts = opts || {};
    const batch = RESOURCE_BATCH;
    const cell = S.board[idx];
    if (!cell || cell.matched || cell.type === 'empty' || batch.seen.has(cell)) return;
    batch.seen.add(cell);
    const type = opts.rewardType || (cell.type === 'skull' ? 'xp' : cell.type);
    S.board[idx] = { type: 'empty' };
    if (opts.reward !== false) batch.counts[type] = (batch.counts[type] || 0) + 1;
    if (cell.type === 'skull' && cell.skull.type === 10) collectBlastResources(idx);
    if (cell.broken) queueAchievement(73, 1);
    if (opts.reward === false || cell.broken || type === 'sword' || type === 'arrow' || type === 'chain') return;
    if (cell.type === 'skull' && (cell.skull.special || cell.skull.boss)) {
      for (const reward of specialRewardTypes(cell.skull.type)) queueResource(reward, batch.source0);
      registerSkullEnd(cell.skull, idx);
      return;
    }
    if (cell.type === 'potion' && cell.explosive) { collectBlastResources(idx); return; }
    if (!['xp', 'coin', 'shield', 'potion'].includes(type)) return;
    queueResource(type, cell);
    if (cell.type === 'potion' && (cell.mana || cell.acid)) return;
    const count = batch.counts[type];
    if (count >= 4) {

      const threshold = hasPerk('Daring') && count >= 10 ? 100 : bonusPct(type);
      if (randInt(1, 100, 5) <= threshold) queueResource(type);
    }
  }

  function resourcePointValue(type, state) {
    const key = type === 'coin' ? 'gold' : type === 'shield' ? 'upgrade' : 'xp';
    const perToken = key === 'gold' ? state.goldPerCoin : key === 'upgrade' ? state.upgradePerShield : state.xpPerSkull;
    const boost = key === 'gold' && state.coinBoost > 0 || key === 'upgrade' && state.shieldBoost > 0 ? 2 : 1;
    const multiplier = (state.mode === 'sprint' ? 2 : 1) * boost;
    const bonus = resourceYield(perToken) - 2;
    const carry = Math.fround(state.resourceCarry[key] + Math.fround(multiplier * Math.fround(bonus)));
    const extra = Math.floor(carry);
    state.resourceCarry[key] = Math.fround(carry - extra);
    return 2 * multiplier + extra;
  }

  // The only resource-arrival arithmetic, also used by deterministic previews.
  // Bars retain all unspent points; each menu consumes 100, never the remainder.
  function addResource(token, state) {
    state = state || S;
    const result = collectionResult(), hero = state.hero;
    const hasEnemy = type => state.board.some(cell => cell && cell.type === 'skull' && cell.skull.type === type);

    if (token.type === 'potion') {
      const skillId = state.skills[token.skillSlot];
      if (token.variant === 1 && skillId) {
        const before = state.skillCooldowns[skillId] || 0;
        state.skillCooldowns[skillId] = Math.max(0, before - 1);
        result.manaCollected = before - state.skillCooldowns[skillId];
        if (state === S) floatOn(-result.manaCollected, 'mana', `.skill[data-slot="${token.skillSlot}"]`, 'КД');
        return result;
      }
      const amount = HEAL_PER_POTION + hero.stats.luck;
      if (token.variant === 2) {
        const rawHp = hero.hp - amount;
        if (state === S && rawHp < 0) queueAchievement(37);
        const before = hero.hp;
        hero.hp = Math.max(0, rawHp);
        if (state === S) floatOn(hero.hp - before, 'hurt', '#hud-hp', 'HP');
        if (state === S && hero.hp <= 0 && !lastStandTriggerIfNeeded()) gameOver();
      } else {
        const before = hero.hp;
        hero.hp = Math.min(hero.maxHp, hero.hp + amount * (state.potionBoost > 0 ? 2 : 1));
        result.healGained = hero.hp - before;
        if (!(clsRace(state) === 'Elf' && hasEnemy(9))) state.poison = 0;
      }
    } else if (token.type === 'shield' && hero.armor !== hero.maxArmor && !hasEnemy(12)) {
      const before = hero.armor;
      const amount = (state.armorPerShield + hero.stats.dexterity + hero.stats.defense * 2) * (state.shieldBoost > 0 ? 2 : 1);
      hero.armor = Math.min(hero.maxArmor, hero.armor + amount);
      result.armorGained = hero.armor - before;
    } else if (['xp', 'coin', 'shield'].includes(token.type)) {
      const points = resourcePointValue(token.type, state);
      const before = token.type === 'xp' ? hero.xp : token.type === 'coin' ? hero.gold : state.upgradeBar;
      const total = before + points;
      const crossed = total >= 100;
      const remainder = crossed ? total % 100 : total;
      // AddResource uses one if, not while: even a synthetic huge carry banks
      // only 100. Normal tokens are small; this also preserves that edge case.
      const earned = (crossed ? 100 : 0) + remainder - before;
      if (token.type === 'xp') {
        hero.xp = remainder; hero.xpLevelCum += earned; result.xpGained = earned;
        if (crossed) result.healGained = levelReached(state);
      } else if (token.type === 'coin') {
        hero.gold = remainder; state.goldEarned += earned; result.goldGained = earned;
      } else {
        state.upgradeBar = remainder; state.upgradeEarned += earned; result.upGained = earned;
      }
      if (crossed && state === S) requestRewardMenu(token.type === 'xp' ? 'level' : token.type === 'coin' ? 'gold' : 'armor');
    }
    if (state === S) {
      if (token.type === 'potion') floatOn(result.healGained, 'heal', '#hud-hp', 'HP');
      floatOn(result.armorGained, 'armor', '#hud-armor', 'брони');
      floatOn(result.xpGained, 'xp', '#hud-xp', 'XP');
      floatOn(result.goldGained, 'gold', '#hud-gold', 'золота');
      floatOn(result.upGained, 'upgrade', '#hud-upgrade', 'UP');
    }
    return result;
  }

  function refillRandomSkill(count) {
    const before = Object.values(S.skillCooldowns).reduce((sum, n) => sum + n, 0);
    withResourceBatch(() => { for (let i = 0; i < count; i++) queueResource('potion', { type: 'potion', mana: true }); });
    return before - Object.values(S.skillCooldowns).reduce((sum, n) => sum + n, 0);
  }

  function heal(amount) {
    const before = S.hero.hp;
    S.hero.hp = Math.min(S.hero.maxHp, S.hero.hp + amount);
    floatOn(S.hero.hp - before, 'heal', '#hud-hp', 'HP');
  }

  function repair(amount) {
    const before = S.hero.armor;
    S.hero.armor = Math.min(S.hero.maxArmor, S.hero.armor + amount);
    floatOn(S.hero.armor - before, 'armor', '#hud-armor', 'брони');
  }

  function flash(msg) {
    const f = el('flash');
    if (!f) return;
    f.textContent = msg;
    f.classList.add('show');
    clearTimeout(f._t);
    f._t = setTimeout(() => f.classList.remove('show'), 1600);
  }

  function hint(msg) { flash(msg); }

  function flashHint(msg) { flash(msg); }

  // Decorations are not state/RNG, save data, arrival events or continuations.
  // One short burst per recipient/sign/kind is summed rather than overprinted.
  // A selector survives renderSkills/board redraw; no center-screen fallback.
  const FLOAT_EFFECTS = new Set();
  let FLOAT_FRAME = 0;
  function clearFloatEffects() {
    if (FLOAT_FRAME) window.cancelAnimationFrame?.(FLOAT_FRAME);
    FLOAT_FRAME = 0;
    for (const effect of FLOAT_EFFECTS) effect.node.remove();
    FLOAT_EFFECTS.clear();
  }
  function renderFloatEffects() {
    FLOAT_FRAME = 0;
    const now = Date.now(), shell = qs('.shell')?.getBoundingClientRect();
    const top = Math.max(0, shell?.top || 0), left = Math.max(0, shell?.left || 0);
    const right = Math.min(window.innerWidth, shell?.right || window.innerWidth);
    const bottom = Math.min(window.innerHeight, shell?.bottom || window.innerHeight);
    const expired = [], positions = [];
    // Read all geometry before writing styles: a board-wide hit must not cause
    // a separate forced layout for every label on every Android frame.
    for (const effect of FLOAT_EFFECTS) {
      const anchor = qs(effect.target);
      if (now >= effect.until || !anchor) { expired.push(effect); continue; }
      const rect = anchor.getBoundingClientRect();
      // Offscreen HP in a short landscape stays offscreen, never jumps to the
      // board. No auto-scroll can interrupt the player's current touch chain.
      const hidden = !rect.width || !rect.height || rect.bottom <= top || rect.top >= bottom
        || rect.right <= left || rect.left >= right;
      const half = Math.min((effect.node.offsetWidth || effect.node.textContent.length * 9) / 2 + 4, (right - left) / 2);
      const x = Math.max(left + half, Math.min(right - half, rect.left + rect.width / 2));
      const y = Math.max(top + 36, Math.min(bottom - 10, rect.top + rect.height / 2 - effect.lane * 18));
      positions.push({ node: effect.node, hidden, x: x + 'px', y: y + 'px' });
    }
    for (const effect of expired) { effect.node.remove(); FLOAT_EFFECTS.delete(effect); }
    for (const { node, hidden, x, y } of positions) {
      if (node.hidden !== hidden) node.hidden = hidden;
      if (node.style.left !== x) node.style.left = x;
      if (node.style.top !== y) node.style.top = y;
    }
    if (FLOAT_EFFECTS.size) FLOAT_FRAME = window.requestAnimationFrame(renderFloatEffects);
  }
  function floatOn(amount, kind, target, label) {
    if (!Number.isFinite(amount) || !amount) return;
    const layer = el('raid-effects');
    if (!layer || !qs(target)) return;
    const now = Date.now();
    let effect = [...FLOAT_EFFECTS].find(old => old.target === target && old.kind === kind && old.label === label
      && Math.sign(old.amount) === Math.sign(amount) && now - old.started < 100);
    if (effect) effect.amount += amount;
    else {
      // Bounded even for synthetic board-wide repeated effects.
      if (FLOAT_EFFECTS.size >= 64) {
        const oldest = FLOAT_EFFECTS.values().next().value;
        oldest.node.remove(); FLOAT_EFFECTS.delete(oldest);
      }
      const node = document.createElement('div');
      node.className = 'float-fx ' + kind; node.dataset.target = target;
      const lane = [...FLOAT_EFFECTS].filter(old => old.target === target).length % 3;
      effect = { node, target, kind, label, amount, lane, started: now, until: now + 900 };
      FLOAT_EFFECTS.add(effect); layer.appendChild(node);
    }
    const value = Number(Math.abs(effect.amount).toFixed(2));
    effect.node.textContent = (effect.amount < 0 ? '−' : '+') + value + (label ? ' ' + label : '');
    if (!FLOAT_FRAME) renderFloatEffects();
  }
  function flashOn(idx, amount, kind, label) {
    floatOn(amount, kind, `.cell[data-idx="${idx}"]`, label);
  }

  // --- audio ----------------------------------------------------------------
  let audioCtx = null;
  function audio() {
    if (!audioCtx && window.AudioContext) {
      try { audioCtx = new AudioContext(); } catch (e) { audioCtx = null; }
    }
    return audioCtx;
  }
  function tone(freq, dur, type, vol) {
    if (!document.getElementById('sound-on')?.checked) return;
    const ctx = audio();
    if (!ctx) return;
    try {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = type || 'sine';
      o.frequency.value = freq;
      g.gain.setValueAtTime(vol || 0.06, ctx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + dur);
      o.connect(g); g.connect(ctx.destination);
      o.start(); o.stop(ctx.currentTime + dur);
    } catch (e) {}
  }
  function audioPick() { tone(520, 0.08, 'sine', 0.04); }
  function audioKill() { tone(180, 0.18, 'triangle', 0.08); tone(80, 0.2, 'sine', 0.06); }
  function audioHeal() { tone(660, 0.12, 'sine', 0.05); tone(880, 0.16, 'sine', 0.04); }
  function audioArmor() { tone(440, 0.09, 'triangle', 0.05); }
  function audioGold() { tone(980, 0.06, 'square', 0.03); }
  function audioLevel() { tone(540, 0.1, 'sine', 0.06); tone(720, 0.14, 'sine', 0.05); tone(960, 0.2, 'sine', 0.04); }
  function audioGameOver() { tone(220, 0.3, 'triangle', 0.08); tone(140, 0.45, 'sine', 0.06); }
  function audioEnemy() { tone(110, 0.15, 'sawtooth', 0.05); }
  function audioBoss() { tone(70, 0.4, 'sawtooth', 0.09); tone(140, 0.5, 'triangle', 0.06); }

  function isChainUppercut(idx, chain) {
    const position = chain.indexOf(idx), previous = chain[position - 1], next = chain[position + 1];
    return !damageBlockedEarly() && S.board[idx]?.skull?.type !== 27 && clsRace() === 'Dwarf' && previous != null && next != null
      && previous % COLS === idx % COLS && next % COLS === idx % COLS
      && Math.floor(previous / COLS) > Math.floor(idx / COLS) && Math.floor(next / COLS) < Math.floor(idx / COLS);
  }

  function chainDamageFor(idx, chain, damage) {
    const skull = S.board[idx].skull;
    if (damageBlockedEarly()) return 0;
    const next = chain[chain.indexOf(idx) + 1];
    if (skull.type === 27) return next != null && Math.floor(next / COLS) < Math.floor(idx / COLS) ? damage : 0;
    const uppercut = isChainUppercut(idx, chain);
    if (uppercut) return damage + S.hero.weaponDamage;
    if (skull.type === 14 && clsRace() === 'Dwarf') return 0;
    return skull.type === 30 || !hasMortalEnemy(30) ? damage : 0;
  }

  // Compatibility/debug entrypoint; real collections use physical cells so
  // nested blasts, Rune and broken tiles cannot duplicate or lose rewards.
  function applyChainEffects(swordCount, shieldCount, coinCount, potionCount, skullIdxs, snapshot) {
    return withResourceBatch(batch => {
      const chain = snapshot?.chain || (S._chainOrder && S._chainOrder.length ? S._chainOrder : skullIdxs);
      const effective = snapshot?.effective || chain;
      const hasSkull = snapshot?.hasSkull ?? skullIdxs.some(idx => S.board[idx] && S.board[idx].skull && !S.board[idx].skull.phylactery);
      const damage = snapshot?.damage ?? ((hasSkull ? baseDamage() : 0) + S.hero.weaponDamage * swordCount * (S.swordBoost > 0 ? 2 : 1));
      const spiky = snapshot?.spiky ?? skullIdxs.some(idx => S.board[idx] && S.board[idx].skull && S.board[idx].skull.type === 2);
      const targetDamage = snapshot?.targetDamage || new Map(skullIdxs.map(idx => [idx, chainDamageFor(idx, chain, damage)]));
      const burning = snapshot?.burning ?? effective.reduce((sum, idx) => {
        const cell = S.board[idx];
        if (!cell) return sum;
        const target = cell.type === 'skull' ? cell.skull : cell;
        return sum + (!target.fireSafe && target.burn ? (target.fireDamage || (typeof target.burn === 'number' ? target.burn : 0)) : 0);
      }, 0);
      const uppercuts = new Set(snapshot?.uppercuts || skullIdxs.filter(idx => isChainUppercut(idx, chain)));
      const tail = chain[chain.length - 1], arrow = chain[chain.length - 3];
      const sniper = chain.length >= 3 && !effective.includes(chain[chain.length - 2]) && S.board[arrow]?.type === 'arrow'
        && (Math.abs(arrow % COLS - tail % COLS) > 1 || Math.abs(Math.floor(arrow / COLS) - Math.floor(tail / COLS)) > 1);
      const meatShields = columnSlots().filter(idx => S.board[idx] && S.board[idx].type === 'skull' && S.board[idx].skull.type === 30 && !chain.includes(idx));
      for (const idx of effective) {
        const cell = S.board[idx];
        if (!cell || cell.type === 'empty') continue;
        if (cell.type === 'skull' && !cell.skull.phylactery) {
          const special = cell.skull.type > 0;
          if (hitSkullAt(idx, targetDamage.get(idx) ?? 0, { direct: true })) {
            if (uppercuts.has(idx)) queueAchievement(48, 2);
            // Native trophy conversion clears the killed tile's enemy identity.
            if (sniper && idx === tail && special && S.board[idx]?.type !== 'trophy') queueAchievement(56);
          }
        } else matchCell(idx, { reward: !(hasFlaw('Cocky') && chain.length <= 3) });
      }
      for (const idx of meatShields) hitSkullAt(idx, damage, { direct: false });
      batch.result.dmgShown = hasSkull ? damage : 0;
      S.swordsHits += hasSkull ? damage : 0;
      // Choose a deterministic creation-order arrival policy, not animation
      // timing. These labels pause behind any resource menu in the same queue.
      if (S.leech > 0 && hasSkull) {
        const amount = Math.floor(Math.fround(damage * Math.fround(S.leech)));
        showScrollLeechLabel(amount, true);
        queueArrival({ kind: 'healing', amount, leech: true });
      } else fadeScrollLeechLabel();
      if (burning > 0) queueArrival({ kind: 'damage', amount: burning, cause: 35 });
      if (spiky && damage > 0) queueArrival({ kind: 'damage', amount: damage, cause: 34 });
      return batch.result;
    });
  }

  // Pretzel mode: every diagonal step whose (min x, min y) key repeats in the
  // chain counts as a crossing; 5/10/15 crossings rate 1/2/3, and 15+ is a
  // "legend" worth a flat 100 points (original ProcessPretzels).
  function pretzelCrossings(chain) {
    const seen = new Set();
    let crosses = 0;
    for (let i = 1; i < chain.length; i++) {
      const a = chain[i - 1], b = chain[i];
      const ax = a % COLS, ay = Math.floor(a / COLS), bx = b % COLS, by = Math.floor(b / COLS);
      if (ax === bx || ay === by) continue;
      const key = Math.min(ax, bx) + 6 * Math.min(ay, by);
      if (seen.has(key)) crosses++; else seen.add(key);
    }
    return crosses;
  }
  function pretzelPoints(crosses) {
    const rating = [5, 10, 15].filter(t => crosses >= t).length;
    if (rating >= 3) return { rating, points: 100 };
    return { rating, points: crosses * [1, 2, 3][rating] };
  }

  function chainSnapshot(chain) {
    const effective = effectiveChain(chain);
    const rune = S.runeActive ? interiorCells(chain) : [];
    const committed = effective.length >= 3;
    const skulls = effective.filter(idx => S.board[idx].type === 'skull' && !isPhylacteryCell(S.board[idx]));
    const swords = effective.reduce((sum, idx) => sum + (S.board[idx].broken ? 0 : swordWeight(S.board[idx])), 0);
    const damage = (skulls.length ? baseDamage() : 0) + S.hero.weaponDamage * swords * (S.swordBoost > 0 ? 2 : 1);
    return { chain, effective, rune, committed, skulls, swords, hasSkull: skulls.length > 0, damage,
      spiky: skulls.some(idx => S.board[idx].skull.type === 2),
      targetDamage: new Map(skulls.map(idx => [idx, chainDamageFor(idx, chain, damage)])),
      uppercuts: skulls.filter(idx => isChainUppercut(idx, chain)),
      burning: effective.reduce((sum, idx) => {
        const cell = S.board[idx], target = cell.type === 'skull' ? cell.skull : cell;
        return sum + (target.burn && !target.fireSafe ? target.fireDamage ?? (typeof target.burn === 'number' ? target.burn : 0) : 0);
      }, 0) };
  }

  function resolveChain() {
    if (!S || HISTORY_OPEN || S.resolving || S.over || S.pendingModal || !ownsRunSlot()) return;
    const chain = S.selected.slice();
    if (chain.length === 1 && S.board[chain[0]].type === 'trophy') { claimTrophy(chain[0]); return; }
    const snapshot = chainSnapshot(chain), { committed, rune, skulls, effective } = snapshot;
    if (!committed && !rune.length) { clearChain(); return; }
    if (!committed || !skulls.length) fadeScrollLeechLabel();
    S.resolving = true;
    S.actionCommitted = committed;
    if (committed) {
      S.chainCount++;
      if (S.mode === 'pretzel') {
        const crosses = pretzelCrossings(chain);
        S.pretzelScore += pretzelPoints(crosses).points; addPretzelStats(crosses);
      }
    }
    S.selected = []; S.chainType = null;
    renderSelection(); renderChainLine(); renderChainTip();
    for (const idx of committed ? effective : []) {
      const node = qs(`.cell[data-idx="${idx}"]`);
      if (node && S.board[idx].type !== 'skull') node.classList.add('pop');
    }
    scheduleRunStep({ phase: 'match-chain', snapshot }, 420);
  }

  function settleChain(snapshot) {
    const { committed, rune, skulls, swords } = snapshot;
    const result = withResourceBatch(batch => {
      // Rune deliberately precedes minimum-chain validation and the normal
      // MatchTiles counter reset. Interior ordinary skulls are COLLECTION,
      // ignoring HP/defence and not contributing to direct kills or Guts.
      let runeCollected = 0;
      for (const idx of rune) {
        const cell = S.board[idx];
        if (cell && cell.type !== 'empty' && !(cell.type === 'skull' && (cell.skull.special || cell.skull.boss))) { runeCollected++; matchCell(idx); }
      }
      if (runeCollected > snapshot.effective.length) queueAchievement(58);
      if (!committed) {
        // Keep the playable board full after a Rune-only arrow cancellation;
        // no ordinary matching, no counter reset and no enemy turn.
        compactBoardAndRefill(); markExistingSkullsOld();
        return batch.result;
      }
      resetCollectionCounts();
      const path = snapshot.chain, crosses = pretzelCrossings(path);
      if (crosses > 5) queueAchievement(75);
      if (S.mode === 'pretzel' && crosses >= 15 && pretzelPoints(crosses).points > 0) queueAchievement(77);
      if (path.length === 20 && path.every(isEdgeSlot)) queueAchievement(68);
      if (path.length === 36 && snapshot.effective.length === 36 && path.every(idx => S.board[idx]?.type === 'coin')) queueAchievement(69);
      if (path.length === 36 && snapshot.effective.some(idx => S.board[idx]?.type === 'chain')) queueAchievement(59);
      const generated = { total: 0, skulls: 0 }; CHAIN_REFILL = generated;
      try {
        applyChainEffects(swords, 0, 0, 0, skulls, snapshot);
        S.killsThisTurn = batch.result.kills;
        markExistingSkullsOld();
        placeNewTiles({});
      } finally { CHAIN_REFILL = null; }
      if (generated.total > 9 && generated.total === generated.skulls) queueAchievement(74);
      // AddRevengeHealth and post-kill passives run after refill, so a newly
      // generated Vengeful also receives this chain's direct kill count.
      for (const cell of S.board) if (cell && cell.type === 'skull' && cell.skull.type === 21) cell.skull.atk += batch.result.kills;
      if (clsRace() === 'Halfling' && batch.result.kills >= 15) {
        const armour = S.equipment.find(item => item.slot === 'armour');
        armour.mods.maxArmor = (armour.mods.maxArmor || 0) + 1;
        updateEquipmentStats();
        queueAchievement(52, 10);
        if (S.achievementRun.masochism) queueAchievement(57);
        flash('🍀 Храбрость: 15+ убийств за цепочку — +1 макс. брони!');
      }
      if (S.berserkActive > 0 && batch.berserkHealing > 0) heal(batch.berserkHealing);
      return batch.result;
    });
    renderBoard(); renderHUD();
    continueAfterRewards({ phase: 'chain', committed, result });
  }

  function isCrippleTarget(skull) {
    return skull && !skull.special && !skull.boss && skull.hp > 0 && skull.hp < (skull.def || 0);
  }

  // Смерть в enemy phase: сначала Last Stand, только потом game over.
  function enemyPhaseDeathCheck() {
    if (S.hero.hp > 0) return false;
    lastStandTriggerIfNeeded();
    if (S.hero.hp > 0) return false;
    renderBoard();
    renderHUD();
    gameOver();
    S.resolving = false;
    return true;
  }

  // Durability resolves from a single armour snapshot. 0..100 has 101
  // outcomes; using a floating-point 50-percent coin toss changes the rule.
  function blockVolley(attack) {
    const armour = Math.max(0, S.hero.armor);
    const blocked = Math.min(attack, armour);
    const defiler = hasMortalEnemy(29);
    const threshold = Math.max(5, Math.round((1 - S.durability) * 100));
    let armourLoss = 0;
    for (let n = 0; n < blocked; n++) if (defiler || randInt(0, 100, 5) < threshold) armourLoss++;
    S.hero.armor -= armourLoss;
    floatOn(-armourLoss, 'armor', '#hud-armor', 'брони');
    return { attack, blocked, damage: Math.max(0, attack - armour), armourLoss, hadArmour: armour > 0 };
  }

  // One normal volley. Kept separate from EndOfTurn so regressions can test
  // the snapshot, poison, spikes and Counterattack without later abilities.
  function processEnemyAttack() {
    return withResourceBatch(() => {

      const attackers = S.board.filter(cell => oldSkull(cell) && !frozenSkull(cell) && enemyGetAttack(cell.skull) > 0);
      if (!attackers.length || (hasPerk('Unexpected') && isEarlyGame())) return null;
      const attack = attackers.reduce((sum, cell) => sum + enemyGetAttack(cell.skull), 0);
      const incomingPoison = attackers.reduce((sum, cell) => sum + (cell.skull.type === 9 ? enemyGetAttack(cell.skull) : 0), 0);
      if (S.counterattack > 0) {
        resetCollectionCounts();
        for (const idx of columnSlots()) {
          const cell = S.board[idx];
          if (oldSkull(cell)) hitSkullAt(idx, enemyGetAttack(cell.skull), { direct: false });
        }
        compactBoardAndRefill();
        markExistingSkullsOld();
        return { attack, blocked: 0, damage: 0, armourLoss: 0, counter: true };
      }
      const volley = blockVolley(attack);
      if (volley.hadArmour) {
        if (S.blunt > 0) for (const idx of columnSlots()) {
          const cell = S.board[idx];
          if (oldSkull(cell) && cell.skull.atk > 1) cell.skull.atk = Math.max(1, cell.skull.atk - S.blunt);
        }
        if (S.spikes > 0) {
          resetCollectionCounts();
          // Frozen old enemies also take spikes. Defence applies, and Lich
          // transforms instead of paying a premature special reward.
          for (const idx of columnSlots()) if (oldSkull(S.board[idx])) {
            const type = S.board[idx].skull.type;
            if (hitSkullAt(idx, S.spikes, { direct: false }) && type === 2) queueAchievement(43);
          }
          compactBoardAndRefill();
          markExistingSkullsOld();
        }
      }
      if (volley.damage > 0) {
        S.poison = Math.max(S.poison, incomingPoison);
        // ProcessVampires follows spikes/refill, before the snapshotted HP hit.
        for (const idx of columnSlots()) {
          const cell = S.board[idx];
          if (!cell || cell.type !== 'skull' || cell.matched || cell.skull.type !== 20) continue;
          const skull = cell.skull, before = skull.hp, hp = skull.hp + enemyGetAttack(skull);
          if (clsRace() === 'Werewolf') { skull.hp = hp; skull.maxHp = Math.max(hp, skull.maxHp); }
          else skull.hp = Math.min(skull.maxHp, hp);
          flashOn(idx, skull.hp - before, 'heal', 'HP');
        }
        damageHero(volley.damage, false, true);
      } else audioEnemy();
      return volley;
    });
  }

  function enemyPhase() {
    if (S.over || S.pendingModal || !ownsRunSlot()) return;
    S.resolving = true;
    S.actionCommitted = true;
    processEnemyAttack();
    renderBoard(); renderHUD();
    // Spikes/counter resources (and their menus) settle before turn++.
    continueAfterRewards({ phase: 'end-turn' });
  }

  function endEnemyTurn() {
    if (S.over) return;
    S.turn++;
    if (enemyPhaseDeathCheck()) return;
    withResourceBatch(() => {
      // Recovery checks Mage BEFORE fire/poison can remove it this turn.
      if (!hasMortalEnemy(15)) {
        for (const id of Object.keys(S.skillCooldowns)) S.skillCooldowns[id] = Math.max(0, S.skillCooldowns[id] - 1);
      }
      S.slashCombo = S.hackCombo = S.shockCombo = S.aweCombo = 0;
      S.achievementRun.combo = 0; S.achievementRun.masochism = false;
      S.runeUsedThisTurn = S.counterattack = 0;
      rotateTurnFlags(['runeActive', 'swordBoost', 'shieldBoost', 'potionBoost', 'coinBoost', 'berserkActive', 'stealActive']);
      resetCollectionCounts();
      // Thaw OR burn, in column order. Burning does not pre-empt a normal
      // volley; frozen cells thaw without also taking their fire tick.
      for (const idx of columnSlots()) {
        const cell = S.board[idx];
        if (!cell || cell.type === 'empty') continue;
        const target = cell.type === 'skull' ? cell.skull : cell;
        if (cell.frozen || target.stunned) {
          cell.frozen = false;
          target.stunned = false;
          target.frozenTurn = -1;
        } else if (target.burn) {
          const amount = target.fireDamage ?? (typeof target.burn === 'number' ? target.burn : baseDamage());
          target.burn = false; target.fireDamage = 0; target.fireSafe = false;
          if (cell.type === 'skull') {
            if (target.hp <= amount) {
              hitSkullAt(idx, amount, { direct: false, ignoreDefence: true });
              if (clsRace() === 'Dragonkin') queueAchievement(54, 2);
            }
            else { target.hp -= amount; flashOn(idx, -amount, 'hurt', 'HP'); }
          }
        }
      }
      if (S.regen > 0) heal(S.regen);
      if (S.poison > 0) {
        damageHero(S.poison, false, true, 36);
        log('☠ Яд: −' + S.poison + ' HP');
      }
      if (enemyPhaseDeathCheck()) return;
      // Enemy poison is direct collection on a lethal tick: no kill counter,
      // no Lich transformation. GetAttack used slot[0] BEFORE this shift.
      for (const idx of columnSlots()) {
        const cell = S.board[idx];
        if (!cell || cell.type !== 'skull') continue;
        const skull = cell.skull, poison = skull.poison || [0, 0, 0];
        skull.poison = [poison[1], poison[2], 0];
        if (poison[0] > 0) {
          flashOn(idx, -poison[0], 'hurt', 'HP');
          if (skull.hp <= poison[0]) { matchCell(idx); if (clsRace() === 'Elf') queueAchievement(47, 2); }
          else skull.hp -= poison[0];
        }
      }
      if (clsRace() === 'Werewolf') {
        let crippled = 0;
        for (let idx = 0; idx < CELL_COUNT; idx++) {
          const cell = S.board[idx];
          if (cell && cell.type === 'skull' && isCrippleTarget(cell.skull)) { matchCell(idx); crippled++; queueAchievement(53, 2); }
        }
        if (crippled > 0) {
          const message = `🐺 Калечение Вервольфа: уничтожено черепов с HP ниже их защиты — ${crippled}`;
          log(message); flash(message);
        }
      }
      applySpecialAbilities();
      if (enemyPhaseDeathCheck()) return;
      rotateTurnFlags(['lastStand']);
      compactBoardAndRefill();
      markExistingSkullsOld();
      rotateTurnFlags(['oneHealthSkulls', 'forceCoins']);
      S.refillFilter.enabled = S.refillFilter.excludeSwords = false;
      S.freezeNextTurn = 0;
      if (S.promotionRemaining > 0) S.promotionRemaining--;
      S.actionCommitted = false;
      // Victory is at the tail, never before lethal turn-100 poison/abilities.
      if (S.mode === 'sprint' && S.turn >= 100) {
        queueAchievement(67); queueAchievement(76);
        S.won = true; S.over = true; S.resolving = false; S.actionCommitted = false;
        S.arrivals = []; S.rewardMenus = []; AFTER_ARRIVALS = SCHEDULED_STEP = null;
        saveRun();
        openModal('gameover');
      }
    });
    renderBoard(); renderHUD();
    continueAfterRewards({ phase: 'ready' });
  }

  function damageHero(amount, isBoss, bypassArmor, cause = 0) {
    if (amount <= 0) return 0;
    const damage = bypassArmor ? amount : blockVolley(amount).damage;
    if (damage > 0) {
      const rawHp = S.hero.hp - damage;
      if (rawHp < 0 && [34, 35, 36].includes(cause)) queueAchievement(cause);
      S.hero.hp = Math.max(0, rawHp);
      floatOn(-damage, 'hurt', '#hud-hp', 'HP');
    }
    audioEnemy();
    return damage;
  }

  // Last Stand (Paladin, классный уникальный навык): если герой погиб — полное лечение.
  // Вызывается всегда после урона в бою, если Hero.hp <= 0.
  function lastStandTriggerIfNeeded() {
    if (S.hero.hp <= 0 && clsRace() !== 'Human' && hasMortalEnemy(MORTAL_BY_RACE[clsRace()])) queueAchievement(40);
    if (S.lastStand > 0 && S.hero.hp <= 0) {
      queueAchievement(61);
      const before = S.hero.hp;
      S.hero.hp = S.hero.maxHp;
      floatOn(S.hero.hp - before, 'heal', '#hud-hp', 'HP');
      S.lastStand = S.nextTurnFlags.lastStand = 0;
      flash('🛡 Последний рубеж: полное лечение!');
      return true;
    }
    return false;
  }

  // --- modals ---------------------------------------------------------------
  function resetModalScroll() {
    const card = qs('#modal .modal-card'), body = el('modal-body');
    if (card) card.scrollTop = 0;
    if (body) body.scrollTop = 0;
  }
  function openModal(kind, restoredChoices = null) {
    const runId = RUN_ID;
    S.pendingModal = kind;
    if (kind === 'level' && !restoredChoices) S.levelDraft = [];
    const wrap = el('modal');
    const title = el('modal-title');
    const body = el('modal-body');
    if (!wrap || !body) return;
    resetModalScroll();
    wrap.className = 'modal show';
    title.className = 'modal-title';
    if (kind === 'level' || kind === 'gold' || kind === 'armor') {
      const cssKind = kind === 'gold' ? 'shop' : kind;
      wrap.classList.add(cssKind);
      title.classList.add(cssKind);
    }

    if (kind === 'level') {
      title.textContent = 'УРОВЕНЬ!';
      body.innerHTML = '<p class="modal-hint">Выбери 2 из 4 и подтверди. Повторный тап снимает выбор.</p>';
      const options = restoredChoices || chooseLevelOptions();
      renderLevelChoices(options, body);
    } else if (kind === 'gold') {
      title.textContent = 'МАГАЗИН';
      body.innerHTML = '<p class="modal-hint">Выбери 1 из 3 замен. Бонусы старой вещи снимаются, а не складываются с новой.</p>';
      renderEquipChoices(body, restoredChoices);
    } else if (kind === 'armor') {
      title.textContent = 'УЛУЧШЕНИЕ';
      body.innerHTML = '<p class="modal-hint">Добавь бонус к надетой вещи. Остальное не меняется; полное сравнение — «Подробнее».</p>';
      renderUpgradeChoices(body, restoredChoices);
    } else if (kind === 'stats') {
      title.textContent = 'СВОЙСТВА';
      body.innerHTML = '';
      const summary = document.createElement('div');
      summary.className = 'character-summary';
      renderCharacterSummary(summary);
      body.appendChild(summary);
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn';
      btn.textContent = 'Закрыть';
      btn.addEventListener('click', () => { if (runId === RUN_ID && S.pendingModal === 'stats') closeModal(); });
      body.appendChild(btn);
    } else if (kind === 'gameover') {
      title.textContent = S.won ? 'ПОБЕДА!' : 'РЕЙД ОКОНЧЕН';
      body.innerHTML = `<p class="modal-hint">${S.won ? 'Спринт пройден: 100 ходов!' : 'Герой погиб на ходу <strong>' + S.turn + '</strong>'}</p>
        <div class="stats-grid">
          <div><span class="result-glyph">${raidModuleIcon('slash')}</span><small>убито черепов</small><strong>${S.skullsKilled}</strong></div>
          <div><span class="result-glyph">${raidModuleIcon('enchant')}</span><small>максимальный уровень</small><strong>${S.maxLevel}</strong></div>
          <div><span class="result-glyph">${raidModuleIcon('goldtouch')}</span><small>золота собрано</small><strong>${S.goldEarned}</strong></div>
          <div><span class="result-glyph">${raidItemIcon('accessory')}</span><small>трофеев забрано</small><strong>${S.trophiesThisRun || 0}</strong></div>
        </div>`;
      const restart = document.createElement('button');
      restart.type = 'button';
      restart.className = 'btn secondary';
      restart.textContent = '↻ Ещё раз';
      restart.addEventListener('click', () => {
        if (runId !== RUN_ID || !S.over) return;
        if (hasPendingProgress()) requestNewGame();
        else { closeModal(); newGame(); }
      });
      const historyNote = document.createElement('p'); historyNote.id = 'run-history-status'; historyNote.className = 'save-note';
      historyNote.setAttribute('role', 'status'); historyNote.setAttribute('aria-live', 'polite'); historyNote.textContent = HISTORY_STATUS.message;
      body.appendChild(historyNote);
      const history = document.createElement('button'); history.type = 'button'; history.className = 'btn secondary'; history.textContent = 'Рекорды и статистика';
      history.addEventListener('click', () => { if (runId === RUN_ID && S.over) openRunRecords(); });
      const achievements = document.createElement('button'); achievements.type = 'button'; achievements.className = 'btn secondary'; achievements.textContent = 'Достижения';
      achievements.addEventListener('click', () => { if (runId === RUN_ID && S.over) openAchievements(); });
      body.appendChild(history); body.appendChild(achievements); body.appendChild(restart);
    }
    queueRunSave();
  }

  function closeModal() {
    const wrap = el('modal');
    if (wrap) wrap.classList.remove('show');
    S.pendingModal = null;
    const runId = RUN_ID;
    setTimeout(() => { if (runId === RUN_ID) resumeRewards(); }, 80);
    queueRunSave();
  }
  function resumeRewards() {
    if (S.pendingModal || S.over || !ownsRunSlot()) return;
    if (S.rewardMenus.length) openModal(S.rewardMenus.shift());
    else { checkLevelUp(); checkGoldBox(); checkUpgradeBar(); }
    drainArrivals();
    if (!S.resolving && !S.pendingModal && !S.over) checkTrophy();
    saveRun();
  }

  // --- особые черепа: особенности по тапу на спец/босс ---
  // Спец-череп — элитность ×2/×2/×4 (оригинал); босс — ×3/×3/×6.
  // Сбор особого продвигает трофейный счётчик; выпадение — клеткой на поле.
  // Особенности спец/босс черепа: тап по нему (без драга).
  function specialTypeName(skull) {
    const t = skull && skull.type;
    if (skull && skull.boss) return 'Босс';
    if (t && ENEMY_FEATS[t]) return ENEMY_FEATS[t].name;
    return 'Спец-череп';
  }

  function showSkullModal(skull) {
    const runId = RUN_ID;
    const wrap = el('modal');
    const title = el('modal-title');
    const body = el('modal-body');
    if (!wrap || !body) return;
    S.pendingModal = 'skull';
    const boss = !!skull.boss;
    wrap.classList.remove('level', 'shop', 'armor', 'trophy', 'gameover', 'skull');
    resetModalScroll();
    wrap.className = 'modal show skull';
    title.className = 'modal-title skull';
    title.textContent = boss ? 'БОСС' : ('ОСОБЫЙ · ' + specialTypeName(skull).toUpperCase());
    body.innerHTML = '';

    const img = document.createElement('img');
    img.className = 'skull-feat-art';
    img.src = raidPortrait(skull);
    img.dataset.portrait = raidPortraitKey(skull);
    img.alt = boss ? 'Босс' : ('Особый противник: ' + specialTypeName(skull));
    body.appendChild(img);

    const stats = document.createElement('p');
    stats.className = 'skull-feat-stats';
    const cd = skull.countdown > 0 ? ` · ⏱ ${skull.countdown}` : '';
    stats.innerHTML = `<b>Атака ${skull.atk}</b> · <b>Броня ${skull.def}</b> · <b>HP ${skull.hp}</b>${cd}`;
    body.appendChild(stats);

    const feats = document.createElement('div');
    feats.className = 'skull-feat-list';
    const items = boss
      ? ['Усиленная элитность — как оригинальный Boss (×3 ⚔ / ×3 🛡 / ×6 ❤️).',
         'Броня не ниже 1, HP ограничен бонусом Громкого (+5% HP).',
         'Исходный Boss; этот портрет пока не назначает ему стихию или новую способность.']
      : (skull.type && ENEMY_FEATS[skull.type] ? ENEMY_FEATS[skull.type].feats : ['Особенность спец-черепа.', 'Броня не ниже 1.', 'Убивая спец-черепа, копим трофей.']);
    items.forEach(text => {
      const li = document.createElement('div');
      li.className = 'skull-feat-line';
      li.textContent = text;
      feats.appendChild(li);
    });
    body.appendChild(feats);

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn';
    btn.textContent = 'Понятно';
    btn.addEventListener('click', () => { if (runId === RUN_ID && S.pendingModal === 'skull') closeModal(); });
    body.appendChild(btn);
  }

  // Trophy generation belongs to SPECIAL COLLECTION, not a turn-end modal.
  // First profile value is 0 (one eligible kill); later intervals are 20..30.
  // Three GENERATED trophies per run, irrespective of whether they are claimed.
  function trophyEligible() {
    return S.classId !== 'adventurer' && classLevelOf(S.classId) < 10
      || CLASSES.some(cls => cls.id !== 'adventurer' && !isClassUnlocked(cls.id));
  }
  function readTrophyRemaining() {
    try {
      const value = Number(profileStorage.getItem('raid:trophyRemaining'));
      return Number.isFinite(value) ? Math.max(0, Math.min(30, Math.trunc(value))) : 0;
    } catch (e) { return 0; }
  }
  function saveTrophyRemaining() {
    try { profileStorage.setItem('raid:trophyRemaining', String(S.trophyRemaining)); } catch (e) {}
  }
  function advanceTrophyAt(slot) {
    let created = false;
    if (S.difficulty === 0) return;
    if (S.mode !== 'sprint' || randInt(0, 100, 0) < 50) S.trophyRemaining--;
    if (S.trophyRemaining < 1) {
      if (S.trophiesGenerated < MAX_TROPHIES_PER_RUN && trophyEligible()) {
        S.trophyRemaining = randInt(20, 30, 0);
        S.trophiesGenerated++;
        created = true;
        // Own SVG, but preserve SetType's numeric appearance draw.
        S.board[slot] = { type: 'trophy', appearance: randInt(0, 7, 0) };
      } else S.trophyRemaining = 0;
    }
    saveTrophyRemaining();
    return created;
  }
  function computeTrophyOutcome() {
    const id = S.classId, level = classLevelOf(id);
    if (id !== 'adventurer' && level < 10 && randInt(0, 1, 5) !== 0) return levelOutcome(id, level);
    const locked = CLASSES.filter(cls => !isClassUnlocked(cls.id)).sort((a, b) => a.native - b.native);
    if (locked.length) {
      const cls = locked[randInt(0, locked.length - 1, 5)];
      return { kind: 'unlock', clsId: cls.id, text: '✨ Мех «' + cls.name + '» разблокирован! (ур. 1)' };
    }
    if (level < 10) return levelOutcome(id, level);
    // Never level a different, already unlocked class as a fallback.
    return { kind: 'none', clsId: null, text: '🏆 Трофей собран; текущий мех уже на уровне 10.' };
  }
  function levelOutcome(id, level) {
    return { kind: 'levelup', clsId: id,
      text: '⬆ Мех «' + CLASS_BY_ID[id].name + '» поднялся на ур. ' + Math.min(10, level + 1) + '!' };
  }
  function checkTrophy() {
    return S.board.flatMap((cell, slot) => cell.type === 'trophy' && !cell.frozen && !cell.matched ? [slot] : []);
  }
  function claimTrophy(slot) {
    if (!S || HISTORY_OPEN || S.over || S.resolving || S.pendingModal || !Number.isInteger(slot) || !ownsRunSlot()) return false;
    const cell = S.board[slot];
    if (!cell || cell.type !== 'trophy' || cell.frozen || cell.matched) return false;
    // Outcome draws happen on the tap, so class state may change between drops.
    const outcome = computeTrophyOutcome();
    if (outcome.kind === 'unlock') { unlockClass(outcome.clsId); S.unlocksThisRun++; }
    else if (outcome.kind === 'levelup') levelUpClass(outcome.clsId);
    S.trophiesThisRun++;
    clearChain();
    withResourceBatch(() => {
      matchCell(slot, { reward: false });
      compactBoardAndRefill();
      markExistingSkullsOld();
      if (!hasLegalChain(S.board)) ensureChainable();
    });
    flash(outcome.text); log(outcome.text);
    renderBoard(); renderHUD(); audioLevel(); saveRun();
    return true;
  }

  function chooseLevelOptions() {
    const owned = S.skills, sprint = S.mode === 'sprint';
    const upgradable = owned.filter(id => id !== 'wish' && (S.skillLevels[id] || 1) < 10);
    const cls = CLASS_BY_ID[S.classId];
    const ordered = ALL_SKILLS.slice().sort((a, b) => a.native - b.native);
    const allowed = new Set(classSkillPool());
    let pool = ordered.filter(skill => owned.length === 4 ? owned.includes(skill.id)
      : allowed.has(skill.id)).map(skill => skill.id);
    pool = pool.filter(id => !(owned.includes(id) && (sprint || id === 'wish' || (S.skillLevels[id] || 1) >= 10))
      && !S.usedSkillIds[id] && !(sprint && id === 'trap')
      && !(S.mode === 'pretzel' && ['teleport', 'chamber'].includes(id)));
    const count = Math.min(owned.length === 3 && (sprint || !upgradable.length) ? 1 : 2, pool.length);
    const options = [];
    for (let n = 0; n < count && pool.length; n++) {
      // The class-first override happens AFTER the SKILL draw. The drawn
      // candidate is not removed when a different first skill replaces it.
      let id = pool[randInt(0, pool.length - 1, 3)];
      const first = cls.uniqueSkill;
      if (!n && S.hero.level === 2 && cls.id !== 'adventurer' && !(sprint && first === 'wish')) id = first;
      options.push(prepareSkillOption(id));
      if (!n && owned.length === 3 && !owned.includes(id)) {
        pool = sprint ? [] : ordered.filter(skill => upgradable.includes(skill.id)).map(skill => skill.id);
      } else pool = pool.filter(value => value !== id);
    }
    const statOrder = ['strength', 'dexterity', 'vitality', 'luck', 'damage', 'defense', 'health', 'charisma'];
    const stats = statOrder.map(id => STATS.find(stat => stat.id === id)).filter(stat =>
      !(S.mode === 'pretzel' && stat.id === 'health')
      && !(stat.id === 'charisma' && ['xp', 'shield', 'potion', 'coin'].every(type => bonusPct(type) >= 100)));
    while (options.length < 4) options.push(stats.splice(randInt(0, stats.length - 1, 3), 1)[0]);
    return options;
  }

  function renderLevelChoices(options, body) {
    S.menuCandidates = options;
    const runId = RUN_ID;
    const grid = document.createElement('div');
    grid.className = 'lv-grid';
    grid.setAttribute('role', 'group');
    grid.setAttribute('aria-label', 'Два улучшения уровня');
    const picked = new Set(S.levelDraft);
    let committed = false;
    const actions = document.createElement('div');
    actions.className = 'lv-actions';
    const choiceRow = document.createElement('div');
    choiceRow.id = 'level-choice-status';
    choiceRow.className = 'lv-status';
    choiceRow.setAttribute('role', 'status');
    choiceRow.setAttribute('aria-live', 'polite');
    const confirm = document.createElement('button');
    confirm.type = 'button';
    confirm.className = 'btn lv-confirm';
    confirm.textContent = 'Подтвердить';
    confirm.setAttribute('aria-describedby', 'level-choice-status');

    function isCurrent() {
      return !committed && runId === RUN_ID && !S.over && ownsRunSlot()
        && S.pendingModal === 'level' && S.menuCandidates === options;
    }
    function mark() {
      choiceRow.textContent = `Выбрано ${picked.size} из 2`;
      confirm.disabled = picked.size !== 2;
      S.levelDraft = [...picked];
      grid.querySelectorAll('.lv-card').forEach((node, i) => {
        const selected = picked.has(i);
        node.classList.toggle('picked', selected);
        node.setAttribute('aria-pressed', String(selected));
        node.disabled = picked.size === 2 && !selected;
      });
    }
    confirm.addEventListener('click', () => {
      if (!isCurrent() || picked.size !== 2) return;
      committed = true;
      confirm.disabled = true;
      // Only the final draft is applied, once, in its selection order.
      picked.forEach(idx => {
        const option = options[idx];
        if (STATS.includes(option)) option.apply();
        else installSkillOption(option);
      });
      S.hero.xpNext = 100;
      closeModal(); renderBoard(); renderHUD(); audioLevel(); saveRun();
    });

    options.forEach((opt, i) => {
      const isStat = STATS.includes(opt);
      const isUpgrade = !!opt.upgrade;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'lv-card ' + (isStat ? 'lv-stat' : 'lv-skill');
      if (isUpgrade) {
        const sk = opt.skill || SKILL_BY_ID[opt.id] || {};
        const lvl = (S.skillLevels && S.skillLevels[opt.id]) || 1;
        btn.innerHTML = `<span class="lv-icon-box"><span class="lv-icon">${raidModuleIcon(opt.id)}</span><span class="lv-corner">${lvl}→${lvl + 1}</span></span>
          <span class="lv-body">
            <span class="lv-row"><span class="lv-name">${sk.name || opt.id} +</span><span class="lv-cool">Улучшение</span></span>
            <span class="lv-desc">Уровень ${lvl} → ${Math.min(10, lvl + 1)}, кулдаун −1</span>
          </span>`;
      } else if (isStat) {
        const lines = (opt.effects ? opt.effects() : []).map(e => `<span class="lv-line">${e}</span>`).join('');
        btn.innerHTML = `<span class="lv-chip" style="color:${opt.color}">${opt.chip}</span>
          <span class="lv-body">
            <span class="lv-name">+ ${opt.name}</span>
            <span class="lv-desc">${opt.desc || ''}</span>
            <span class="lv-effs">${lines}</span>
          </span>`;
      } else {
        btn.innerHTML = `<span class="lv-icon-box"><span class="lv-icon">${raidModuleIcon(opt.id)}</span><span class="lv-corner">${opt.level}</span></span>
          <span class="lv-body">
            <span class="lv-row"><span class="lv-name">${opt.name}</span><span class="lv-cool">${opt.duration > 0 ? 'Кулдаун: ' + (opt.duration - S.quicken - (S.skills.length === 3 && hasPerk('Cunning') ? 1 : 0)) : 'Одноразовый'}</span></span>
            <span class="lv-desc">${opt.desc}</span>
          </span>`;
      }
      btn.addEventListener('click', () => {
        if (!isCurrent()) return;
        if (picked.has(i)) picked.delete(i);
        else {
          if (picked.size >= 2) return;
          picked.add(i);
          audioPick();
        }
        mark();
        saveRun();
      });
      grid.appendChild(btn);
    });
    body.appendChild(grid);
    actions.appendChild(choiceRow);
    actions.appendChild(confirm);
    body.appendChild(actions);
    mark();
  }

  // I1: Game+c358+4*n addresses slots, counters, flags and a life-leech label,
  // NOT the set of owned skill IDs. classic-memory-v1 is an explicit policy:
  // zero fresh/padding bytes, retain label geometry across same-page new games.
  // Evidence/limits: LUNAPARK §7, cdriper/DungeonRaid 6c098c1 (field layout),
  // coolking70/DungeonRaid a708df8 (first 12 words). No foreign renderer/assets.
  // Return a zero/nonzero EQUIVALENCE word; pointers, RGB and UV use 1, never a
  // fabricated native address/texture. A supplied raw fixture bypasses this model.
  function classicScrollWord(n, state = S) {
    if (!Number.isInteger(n) || n < 0 || n >= 42 || !state || !Array.isArray(state.skills)) return null;
    if (n < 4) return state.skills[n] ? 1 : 0;
    if (n === 4) return state.skills.length;
    if (n < 10) {
      const keys = ['potion', 'xp', 'coin', 'shield', 'sword'];
      const counts = state.collectionCounts || {};
      const count = key => Object.hasOwn(counts, key) ? counts[key] : 0;
      const value = count(keys[n - 5]), arrows = n === 9 ? count('arrow') : 0;
      return Number.isInteger(value) && value >= 0 && Number.isInteger(arrows) && arrows >= 0
        && value + arrows <= 0x7fffffff ? value + arrows : null;
    }
    if (n === 10) return (state.refillFilter?.enabled ? 1 : 0) | (state.refillFilter?.excludeSwords ? 0x100 : 0)
      | (state.counterattack > 0 ? 0x10000 : 0) | (state.stealActive > 0 ? 0x1000000 : 0);
    if (n === 11) return state.nextTurnFlags?.stealActive > 0 ? 1 : 0;
    const label = state.scrollLeechLabel;
    if (!label || label.version !== 1 || !Number.isInteger(label.number) || label.number < 0 || label.number > 99999
        || typeof label.active !== 'boolean' || typeof label.inFlight !== 'boolean') return null;
    if (n === 12) return label.active ? 1 : 0;
    if (n === 13) return label.number ? 8 : 0;
    const vertex = Math.floor((n - 14) / 5), component = (n - 14) % 5;
    if (component === 2) return 1; // Start sets nonzero RGB for every vertex; fading only changes alpha.
    if (!label.number) return 0;   // Explicit fresh-zero policy until the first positive preview.
    if (component >= 3) return 1; // Every used glyph UV in this range is strictly positive.
    // Only the first six vertices are read. Their layout is: first word,
    // positive number at half scale, final word. Widths are rule data because
    // the erroneous INT comparison observes exactly-zero vertex coordinates.
    const widths = [20,16,20,20,21,20,20,19,19,20];
    const digits = String(label.number).split('').map(digit => widths[Number(digit)] / 2);
    const left = -(38 + 18 + digits.reduce((a,b) => a+b, 0)) / 2;
    const right = left + 38;
    const coordinate = component === 0
      ? [left, right, right, left, right, right + digits[0]][vertex]
      : (vertex === 2 || vertex === 3 ? 0 : -1);
    return coordinate === 0 ? 0 : 1;
  }

  function scrollFilterWords() {
    try {
      const raw = S.scrollNativeWords;
      if (raw !== null) {
        if (!raw || typeof raw !== 'object') return null;
        const words = [];
        for (let n = 0; n < 42; n++) {
          if (!Object.hasOwn(raw, n)) return null;
          const word = raw[n]; // Snapshot once; getters cannot change the filter halfway through.
          if (!Number.isInteger(word) || word < -0x80000000 || word > 0xffffffff) return null;
          words.push(word);
        }
        return words;
      }
      if (S.scrollPolicy !== 'classic-memory-v1') return null;
      const words = Array.from({ length: 42 }, (_, n) => classicScrollWord(n));
      return words.every(word => word !== null) ? words : null;
    } catch (e) { return null; } // Malformed external debug/restore data cannot break the shop.
  }

  function showScrollLeechLabel(number, inFlight = false) {
    if (!(number > 0)) return;
    clearTimeout(SCROLL_LABEL_TIMER); SCROLL_LABEL_TIMER = null;
    SCROLL_LABEL_DEADLINE = 0;
    S.scrollLeechLabel.number = number;
    S.scrollLeechLabel.active = true;
    S.scrollLeechLabel.inFlight = inFlight;
  }
  function fadeScrollLeechLabel(delay = 200) {
    const label = S.scrollLeechLabel;
    if (!label.active || label.inFlight || SCROLL_LABEL_TIMER !== null) return;
    const runId = RUN_ID;
    // Our arrival-order C2 policy: a cancelled preview or settled healing fades
    // for 200ms. Native frame/flight races are not claimed as bit-exact timing.
    SCROLL_LABEL_DEADLINE = Date.now() + delay;
    SCROLL_LABEL_TIMER = setTimeout(() => {
      if (runId !== RUN_ID || S.scrollLeechLabel !== label) return;
      label.active = false; SCROLL_LABEL_TIMER = null; SCROLL_LABEL_DEADLINE = 0;
      queueRunSave();
    }, delay);
  }
  function updateScrollLeechPreview() {
    const effective = effectiveChain(S.selected);
    const skull = effective.some(slot => S.board[slot].type === 'skull' && !isPhylacteryCell(S.board[slot]));
    const swords = effective.reduce((sum, slot) => sum + (S.board[slot].broken ? 0 : swordWeight(S.board[slot])), 0);
    const damage = (skull ? baseDamage() : 0) + S.hero.weaponDamage * swords * (S.swordBoost > 0 ? 2 : 1);
    const number = Math.floor(Math.fround(damage * Math.fround(S.leech)));
    if (number > 0) showScrollLeechLabel(number);
    else fadeScrollLeechLabel();
  }

  function noteCompatibility(message) {
    if (!S.compatibilityNotices.includes(message)) S.compatibilityNotices.push(message);
  }
  function makeShopOffers() {
    const options = drawItemSlots(3, 2, makeItem);
    if (random(2) >= (clsRace() === 'Sauren' ? .5 : .25)) return options;
    const item = options[randInt(0, 2, 2)];
    const exempt = random(2) < Math.fround(.1);
    const ordered = ALL_SKILLS.slice().sort((a, b) => a.native - b.native)
      .filter(skill => skill.id !== 'wish' && !(S.mode === 'pretzel' && skill.id === 'chamber'));
    // The exemption MUST NOT even read memory. Otherwise snapshot either a
    // complete supplied fixture or the named classic model, never a mix of both.
    const words = exempt ? null : scrollFilterWords();
    if (!exempt && !words) {
      item.scrollBlocked = true;
      noteCompatibility('I1: свиток пропущен — нет полных достоверных данных фильтра.');
      return options;
    }
    const eligible = ordered.filter(skill => (exempt || words[skill.native] === 0)
      && isSkillUnlocked(skill.id) && canActivateSkill(skill.id));
    if (!eligible.length) {
      // Native draws before indexing its empty vector. Consume that ITEM call,
      // but skip the unsafe access and keep all three equipment choices usable.
      random(2);
      item.scrollBlocked = true;
      noteCompatibility('I1: пустой набор кандидатов свитка; покупка предмета доступна.');
      return options;
    }
    item.scroll = prepareSkillOption(eligible[randInt(0, eligible.length - 1, 2)].id);
    // A scroll is a fresh skill template, even if that skill is already owned.
    item.scroll.level = S.mode === 'sprint' ? 5 : 1;
    item.scroll.duration = (item.scroll.id === 'wish' ? -1 : item.scroll.cooldown) - (S.mode === 'sprint' ? 4 : 0);
    item.scroll.charge = item.scroll.duration;
    item.scroll.upgrade = false;
    return options;
  }
  function castPurchasedScroll(scroll) {
    if (clsRace() === 'Sauren') queueAchievement(49, 2);
    const continuedAction = S.resolving, continuation = AFTER_ARRIVALS;
    S.resolving = true;
    // Do not call activateSkill: no owned cooldown, slot or Dragonkin hook.
    // Do not replace AFTER_ARRIVALS: it may be the suspended enemy volley.
    withResourceBatch(() => {
      applySkill(scroll.id);
      if (S.board.some(cell => !cell || cell.type === 'empty')) compactBoardAndRefill();
      if (!S.actionCommitted) markExistingSkullsOld();
      if (!hasLegalChain(S.board)) ensureChainable();
    });
    if (!continuedAction && !continuation) continueAfterRewards({ phase: 'skill' });
  }
  function purchaseShopItem(index) {
    if (!S || S.pendingModal !== 'gold' || !Number.isInteger(index) || !ownsRunSlot()) return false;
    const item = S.menuCandidates[index];
    if (!item || !itemSlotById(item.slot)) return false;
    equipItem(item); // Recompute BEFORE scroll activation.
    closeModal();   // Remove the shop BEFORE Enchant can open another menu.
    if (item.scroll) castPurchasedScroll(item.scroll);
    renderBoard(); renderHUD(); audioLevel(); saveRun();
    return true;
  }
  function appendEquipmentChoices(body, grid) {
    const summaryBtn = document.createElement('button');
    summaryBtn.type = 'button';
    summaryBtn.className = 'btn secondary shop-summary-toggle';
    summaryBtn.textContent = 'Персонаж';
    summaryBtn.setAttribute('aria-controls', 'equipment-summary');
    summaryBtn.setAttribute('aria-expanded', 'false');
    const detailsBtn = document.createElement('button');
    detailsBtn.type = 'button'; detailsBtn.className = 'btn secondary reward-details-toggle';
    detailsBtn.textContent = 'Подробнее'; detailsBtn.setAttribute('aria-label', 'Полные параметры до и после');
    detailsBtn.setAttribute('aria-controls', 'equipment-choices'); detailsBtn.setAttribute('aria-expanded', 'false');
    grid.id = 'equipment-choices';
    detailsBtn.addEventListener('click', () => {
      const show = detailsBtn.getAttribute('aria-expanded') !== 'true';
      grid.querySelectorAll('.item-state').forEach(node => { node.hidden = !show; });
      detailsBtn.setAttribute('aria-expanded', String(show));
      detailsBtn.setAttribute('aria-label', show ? 'Свернуть полное сравнение' : 'Полные параметры до и после');
      detailsBtn.textContent = show ? 'Свернуть' : 'Подробнее';
    });
    const toolbar = document.createElement('div'); toolbar.className = 'reward-toolbar';
    toolbar.appendChild(summaryBtn); toolbar.appendChild(detailsBtn);
    const summary = document.createElement('div');
    summary.id = 'equipment-summary';
    summary.className = 'character-summary';
    summary.hidden = true;
    renderCharacterSummary(summary);
    summaryBtn.addEventListener('click', () => {
      const show = summary.hidden;
      summary.hidden = !show;
      grid.hidden = show;
      detailsBtn.hidden = show;
      summaryBtn.setAttribute('aria-expanded', String(show));
      summaryBtn.textContent = show ? 'К выбору' : 'Персонаж';
    });
    body.appendChild(toolbar);
    body.appendChild(grid);
    body.appendChild(summary);
  }
  function renderEquipChoices(body, restoredChoices = null) {
    const options = restoredChoices || makeShopOffers();
    S.menuCandidates = options;
    const grid = document.createElement('div');
    grid.className = 'shop-grid';
    options.forEach((item, index) => {
      const equipped = S.equipment.find(entry => entry.slot === item.slot);
      const changes = itemChanges(equipped, item).map(change =>
        `<span class="item-change ${change.after > change.before ? 'better' : 'worse'}">${change.text}</span>`).join('');
      const scroll = item.scroll
        ? `<span class="shop-scroll">Свиток: ${item.scroll.name} — разово при покупке</span><span class="shop-preview">${item.scroll.desc}</span>`
        : item.scrollBlocked ? '<span class="shop-compatibility">Свиток пропущен: нет подходящего навыка или полных данных фильтра (I1).</span>' : '';
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'shop-item';
      btn.dataset.slot = item.slot;
      btn.innerHTML = `<span class="shop-icon">${raidItemIcon(item.slot)}</span><span class="shop-body">
        <span class="shop-slot">${item.slotName} · замена</span><span class="shop-name">${item.name}</span>
        <span class="shop-current">Вместо: ${equipped.name}</span>
        <span class="item-state item-before" hidden><span class="item-state-title">Сейчас: ${equipped.name}</span><span class="item-mods">${itemModsText(equipped)}</span></span>
        <span class="item-state item-after" hidden><span class="item-state-title">После замены:</span><span class="item-mods">${itemModsText(item)}</span></span>
        ${changes}${scroll}</span>`;
      btn.addEventListener('click', () => { if (S.menuCandidates === options) purchaseShopItem(index); });
      grid.appendChild(btn);
    });
    // Three equipment choices; the fourth is always inert, never a stat scroll.
    const empty = document.createElement('div');
    empty.className = 'shop-item shop-empty';
    empty.hidden = true; // Keep the inert fourth slot, not its decorative height.
    empty.setAttribute('aria-hidden', 'true');
    grid.appendChild(empty);
    appendEquipmentChoices(body, grid);
  }

  function renderUpgradeChoices(body, restoredChoices = null) {
    const options = restoredChoices || drawItemSlots(4, 4, makeUpgradeOffer);
    S.menuCandidates = options;
    const grid = document.createElement('div');
    grid.className = 'lv-grid';
    options.forEach(opt => {
      const item = S.equipment.find(entry => entry.slot === opt.slot);
      const upgraded = { ...item, mods: { ...item.mods } };
      addItemModifier(upgraded, opt.key, opt.value); // Preview only; never mutate worn gear.
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'lv-card lv-stat';
      btn.dataset.slot = opt.slot;
      btn.dataset.modifier = opt.key;
      btn.innerHTML = `<span class="lv-chip">${raidItemIcon(opt.slot)}</span>
        <span class="lv-body">
          <span class="lv-name">${opt.slotName} · ${item.name}</span>
          <span class="item-state item-before" hidden><span class="item-state-title">Сейчас на предмете:</span><span class="item-mods">${itemModsText(item)}</span></span>
          <span class="item-state item-after" hidden><span class="item-state-title">После улучшения:</span><span class="item-mods">${itemModsText(upgraded)}</span></span>
          <span class="lv-effs"><span class="lv-line item-change better">${opt.name}: ${opt.comparison} (+${itemNumber(opt.key, opt.value)})</span><span class="lv-line">${opt.desc}</span></span>
        </span>`;
      btn.addEventListener('click', () => {
        if (S.pendingModal !== 'armor' || S.menuCandidates !== options || !ownsRunSlot()) return;
        applyUpgradeOffer(opt);
        closeModal(); renderBoard(); renderHUD(); audioLevel(); saveRun();
      });
      grid.appendChild(btn);
    });
    appendEquipmentChoices(body, grid);
  }

  // The checks also accept manually injected full bars from the debug API.
  // Actual resource events cross/pay immediately in addResource above.
  function checkLevelUp() {
    if (S.pendingModal || S.over || S.hero.xp < 100) return;
    S.hero.xp %= 100;
    levelReached(S);
    requestRewardMenu('level');
  }
  function checkGoldBox() {
    if (S.pendingModal || S.over || S.hero.gold < GOLD_BOX_MAX) return;
    S.hero.gold %= GOLD_BOX_MAX;
    requestRewardMenu('gold');
  }
  function checkUpgradeBar() {
    if (S.pendingModal || S.over || S.upgradeBar < UPGRADE_BAR_MAX) return;
    S.upgradeBar %= UPGRADE_BAR_MAX;
    requestRewardMenu('armor');
  }

  function gameOver() {
    if (S.over) return;
    queueAchievement(67);
    S.over = true;
    S.resolving = false;
    S.actionCommitted = false;
    S.arrivals = [];
    S.rewardMenus = [];
    AFTER_ARRIVALS = SCHEDULED_STEP = null;
    clearTimeout(ACTION_TIMER); ACTION_TIMER = null;
    saveRun();
    audioGameOver();
    const runId = RUN_ID;
    setTimeout(() => { if (runId === RUN_ID) openModal('gameover'); }, 600);
  }

  // --- special-ability helpers -------------------------------------------------
  // Match adjacent non-skulls in column order. matchCell's shared seen-set
  // terminates potion recursion and prevents overlaps from awarding twice.
  function collectBlastResources(idx) {
    return withResourceBatch(() => {
      const x = idx % COLS, y = Math.floor(idx / COLS);
      for (let col = Math.max(0, x - 1); col <= Math.min(COLS - 1, x + 1); col++) {
        for (let row = Math.max(0, y - 1); row <= Math.min(ROWS - 1, y + 1); row++) {
          const slot = row * COLS + col;
          if (slot !== idx && S.board[slot] && S.board[slot].type !== 'skull') matchCell(slot);
        }
      }
    });
  }

  // Kamikaze collects adjacent resources (rather than destroying their reward).
  // Its timeout additionally halves hero HP; killing it does not hurt the hero.
  function explodeKamikazeCell(idx, hurtHero) {
    if (idx == null || idx < 0 || idx >= CELL_COUNT) return;
    return withResourceBatch(() => {
      if (hurtHero) resetCollectionCounts();
      matchCell(idx, { reward: !hurtHero });
      if (hurtHero) {
        const before = S.hero.hp;
        S.hero.hp = Math.max(1, Math.floor(S.hero.hp / 2));
        floatOn(S.hero.hp - before, 'hurt', '#hud-hp', 'HP');
        log('💠 КА-МИ-КАЗЕ: пол-HP → ' + S.hero.hp);
      }
      audioKill();
    });
  }

  function createAlchemistTile() {
    return createNewTile();
  }

  function unstableTeleport(idx) {
    // Native filter: normally exclude skulls; only Changeling's mortal enemy
    // excludes swords instead. Treasure Chamber does not override this filter.
    const exclude = clsRace() === 'Changeling' ? 'sword' : 'skull';
    if (exclude === 'sword') queueAchievement(38);
    // Native keeps both bytes until the end-of-turn refill boundary. They are
    // observable by I1 independently of Counterattack and the two Steal flags.
    S.refillFilter.enabled = true;
    S.refillFilter.excludeSwords = exclude === 'sword';
    for (let c = 0; c < COLS; c++) for (let r = ROWS - 1; r >= 0; r--) {
      S.board[r * COLS + c] = createNewTile({ exclude });
    }
    flash('🌀 НЕСТАБИЛЬНЫЙ: поле обновлено');
    log('🌀 Нестабильный: поле обновлено');
  }

  function enemyGetAttack(skull) {
    const atk = Math.max(0, skull && skull.atk || 0);
    return clsRace() === 'Elf' && atk > 1 && skull.poison && skull.poison[0] > 0 ? Math.floor(atk / 2) : atk;
  }

  function oldSkull(cell) {
    return cell && cell.type === 'skull' && !cell.matched && cell.skull.hp > 0
      && (cell.skull.enteredTurn ?? -1) < S.turn;
  }
  function frozenSkull(cell) {
    return !!cell.frozen || !!cell.skull.stunned;
  }

  // Применение особых способностей спец-черепов (end-of-turn).
  function applySpecialAbilities() {
    return withResourceBatch(runSpecialAbilities);
  }

  function runSpecialAbilities() {
    const abilityDone = new Set();
    let unstablePending = false;
    // Original activation order is column-major (x outer, y inner), not
    // row-major: EndOfTurn iterates ColumnSlots() when calling ActivateAbility.
    const activationOrder = [];
    for (let c = 0; c < COLS; c++) for (let r = 0; r < ROWS; r++) activationOrder.push(r * COLS + c);
    for (const i of activationOrder) {
      const cell = S.board[i];
      if (!cell || cell.type !== 'skull') continue;
      const s = cell.skull;
      if (!s || s.boss || !s.type || abilityDone.has(s.uid)) continue;
      abilityDone.add(s.uid);
      const t = s.type;
      const roundOrCol = Math.floor(i / COLS);
      const col0 = i % COLS;

      if (t === 3) { // Golden — таймер → 0, исчезает без награды
        s.countdown = (s.countdown || 0) - 1;
        if (s.countdown <= 0) {
          S.board[i] = { type: 'empty' };
          compactBoardAndRefill(); markExistingSkullsOld();
          log('🏆 Золотой исчез');
        }
      } else if (t === 4) { // Healer — 2 раненых (до maxHp) или +1 атака
        const wounded = [];
        for (let j = 0; j < CELL_COUNT; j++) {
          const wc = S.board[j];
          if (wc && wc.type === 'skull' && wc.skull.uid !== s.uid && wc.skull.hp < wc.skull.maxHp) wounded.push(j);
        }
        if (wounded.length > 0) {
          shuffle(wounded).slice(0, 2).forEach(wi => {
            const wc = S.board[wi];
            const before = wc.skull.hp;
            wc.skull.hp = wc.skull.maxHp;
            flashOn(wi, wc.skull.hp - before, 'heal', 'HP');
          });
        } else {
          const all = [];
          for (let j = 0; j < CELL_COUNT; j++) {
            const ac = S.board[j];
            if (ac && ac.type === 'skull' && ac.skull.uid !== s.uid) all.push(j);
          }
          shuffle(all).slice(0, 2).forEach(ai => { S.board[ai].skull.atk += 1; flashOn(ai, 1, 'buff', '⚔'); });
        }
      } else if (t === 6) { // Acidic — превращает случайное зелье в кислое
        const potions = [];
        for (let j = 0; j < CELL_COUNT; j++) {
          const p = S.board[j];
          // Original Acidic skips mana, acid and explosive potions.
          if (p && p.type === 'potion' && !p.mana && !p.acid && !p.explosive) potions.push(j);
        }
        if (potions.length > 0) {
          const pick = potions[randInt(0, potions.length - 1, 5)];
          S.board[pick].acid = true;
        }
      } else if (t === 7) { // Flaming — поджигает 8 соседей
        for (let dr = -1; dr <= 1; dr++) for (let dc = -1; dc <= 1; dc++) {
          if (dr === 0 && dc === 0) continue;
          const r = roundOrCol + dr, c = col0 + dc;
          if (r < 0 || r >= ROWS || c < 0 || c >= COLS) continue;
          const ni = r * COLS + c;
          const nc = S.board[ni];
          if (!nc || nc.type === 'empty') continue;
          if (nc.type === 'skull') {
            if (nc.skull && nc.skull.type === 7) continue; // другие Огненные — не поджигать
            nc.skull.fireDamage = (nc.skull.burn ? nc.skull.fireDamage || 0 : 0) + enemyGetAttack(s);
            nc.skull.burn = true; nc.skull.fireSafe = false; nc.skull.stunned = false;
            nc.frozen = false;
          } else {
            nc.burn = (typeof nc.burn === 'number' ? nc.burn : 0) + enemyGetAttack(s);
            nc.frozen = false;
          }
        }
      } else if (t === 8) { // Freezing — замораживает до 2 клеток
        const candidates = [];
        for (let j = 0; j < CELL_COUNT; j++) {
          const nc = S.board[j];
          if (!nc || nc.type === 'empty') continue;
          if (nc.frozen) continue;
          if (nc.type === 'skull' && nc.skull && (nc.skull.special || nc.skull.boss)) continue;
          candidates.push(j);
        }
        shuffle(candidates).slice(0, 2).forEach(ci => {
          const nc = S.board[ci];
          nc.frozen = true;
          const target = nc.type === 'skull' ? nc.skull : nc;
          target.burn = false; target.fireDamage = 0; target.fireSafe = false;
          if (nc.type === 'skull') nc.skull.frozenTurn = S.turn;
        });
      } else if (t === 10) { // Kamikaze — таймер → взрыв
        s.countdown = (s.countdown || 0) - 1;
        if (s.countdown <= 0) {
          explodeKamikazeCell(i, true);
        }
      } else if (t === 11) { // Overseer — превращает меч в череп
        const swords = [];
        for (let j = 0; j < CELL_COUNT; j++) {
          const sj = S.board[j];
          if (sj && sj.type === 'sword') swords.push(j);
        }
        if (swords.length > 0) {
          const pick = swords[randInt(0, swords.length - 1, 5)];
          const created = makeSkull(false, false);
          created.hp = 1; created.enteredTurn = S.turn;
          S.board[pick] = { type: 'skull', skull: created };
          log('🔭 Наблюдатель превратил меч в череп');
        }
      } else if (t === 13) { // Thief — ворует до 2 монет; без них — золото
        const coins = [];
        for (let j = 0; j < CELL_COUNT; j++) {
          const ci = S.board[j];
          if (ci && ci.type === 'coin') coins.push(j);
        }
        if (coins.length > 0) {
          const stolen = Math.min(2, coins.length);
          // Original StealGold marks the coins as matched (no reward token), so
          // they simply vanish; only the mortal enemy (Sauren) loses HP on top.
          shuffle(coins).slice(0, stolen).forEach(j => S.board[j] = { type: 'empty' });
          // Кража монет сама по себе урона не наносит; урон — только для
          // смертного врага (Sauren): 2× атака Вора.
          let hpLoss = 0;
          if (clsRace() === 'Sauren') hpLoss = stolen * enemyGetAttack(s) * 2;
          if (hpLoss > 0) {
            const before = S.hero.hp;
            S.hero.hp = Math.max(0, S.hero.hp - hpLoss);
            floatOn(S.hero.hp - before, 'hurt', '#hud-hp', 'HP');
          }
          log('🏴‍☠️ Вор украл ' + stolen + ' монет' + (hpLoss > 0 ? ': −' + hpLoss + ' HP' : ''));
        } else {
          const stolen = Math.min(2, S.hero.gold);
          S.hero.gold -= stolen;
          floatOn(-stolen, 'gold', '#hud-gold', 'золота');
          if (stolen > 0) {
            const hpLoss = clsRace() === 'Sauren' ? stolen * enemyGetAttack(s) * 2 : 0;
            if (hpLoss > 0) {
              const before = S.hero.hp;
              S.hero.hp = Math.max(0, S.hero.hp - hpLoss);
              floatOn(S.hero.hp - before, 'hurt', '#hud-hp', 'HP');
            }
            log('🏴‍☠️ Вор украл золото: −' + stolen + (hpLoss > 0 ? ', HP −' + hpLoss : ''));
          }
        }
      } else if (t === 14) { // Chaotic — перелет на соседнюю клетку (спец-черепов не трогаем)
        const targets = [];
        for (let j = 0; j < CELL_COUNT; j++) {
          const tj = S.board[j];
          if (!tj || tj.type === 'empty') continue;
          if (j === i) continue;
          if (tj.type === 'skull' && tj.skull && (tj.skull.special || tj.skull.boss)) continue;
          targets.push(j);
        }
        if (targets.length > 0) {
          const pick = targets[randInt(0, targets.length - 1, 5)];
          const tj = S.board[pick];
          if (tj.type === 'skull') {
            log('🌪️ Хаотический съел обычного черепа без награды');
          }
          S.board[i] = { type: 'empty' };
          S.board[pick] = { type: 'skull', skull: s };
        }
      } else if (t === 16) { // Assassin — bottom row: direct 2× hit; mortal enemy (Faun): kill the tile below
        if (roundOrCol === ROWS - 1) {
          const bonusDmg = 2 * enemyGetAttack(s);
          damageHero(bonusDmg, false, true);
          log('🗡️ Ассасин: прямой урон −' + bonusDmg + ' HP');
        } else if (clsRace() === 'Faun') {
          // Original StabAttack: only as the Faun's mortal enemy does it match
          // (destroy) the ordinary tile directly below, without a reward.
          const belowIdx = i + COLS;
          const below = S.board[belowIdx];
          if (below && below.type !== 'empty' && !(below.type === 'skull' && below.skull && (below.skull.special || below.skull.boss))) {
            S.board[belowIdx] = { type: 'empty' };
            log('🗡️ Ассасин удалил клетку снизу');
          }
        }
      } else if (t === 17) { // Doppelganger — defence is exactly hero armor
        s.def = Math.max(0, S.hero.armor);
      } else if (t === 18) { // Alchemist — перетасовка до 2 не-черепов
        const candidates = [];
        for (let j = 0; j < CELL_COUNT; j++) {
          const aj = S.board[j];
          if (!aj || aj.type === 'empty') continue;
          if (aj.type === 'skull' && aj.skull && (aj.skull.special || aj.skull.boss)) continue;
          if (j === i) continue;
          candidates.push(j);
        }
        shuffle(candidates).slice(0, 2).forEach(j => {
          S.board[j] = createAlchemistTile();
        });
        if (candidates.length > 0) flash('⚗️ Алхимик перетасовал клетки');
      } else if (t === 19) { // Unstable — таймер → телепорт
        s.countdown = (s.countdown || 0) - 1;
        if (s.countdown <= 0) {
          // Native marks the board for removal but lets the remaining old
          // specials act; refill occurs only after the entire ability pass.
          S.board.forEach(old => { if (old) old.matched = true; });
          unstablePending = true;
          continue;
        }
      } else if (t === 21) { // Vengeful — already applied after chain refill
        // No active end-of-turn ability.
      } else if (t === 22) { // Trampling — ломает щит/меч
        const candidates = [];
        for (let j = 0; j < CELL_COUNT; j++) {
          const tj = S.board[j];
          if (!tj) continue;
          // Original BreakRandomShieldSword skips arrows and already-broken tiles.
          if (tj.type === 'shield' && !tj.broken) candidates.push(j);
          else if (tj.type === 'sword' && !tj.arrow && !tj.broken) candidates.push(j);
        }
        if (candidates.length > 0) {
          const pick = candidates[randInt(0, candidates.length - 1, 5)];
          S.board[pick].broken = true;
          flash('🔨 Трамплев: меч/щит сломан');
        }
      } else if (t === 23) { // Greasy — removes the non-skull tile below the first eligible skull per column
        for (let col = 0; col < COLS; col++) {
          for (let r = Math.min(ROWS - 2, 4); r >= 0; r--) {
            const idx = r * COLS + col;
            const c = S.board[idx];
            if (!c || c.type !== 'skull' || c.skull.uid === s.uid) continue;
            const belowIdx = (r + 1) * COLS + col;
            const below = S.board[belowIdx];
            if (below && below.type === 'skull') continue;
            if (below && below.type !== 'empty') {
              S.board[belowIdx] = { type: 'empty' };
              flash('🧎 Скользкий удалил клетку снизу');
            }
            break;
          }
        }
      } else if (t === 24) { // Alpha — swap черепов вне соседства ↔ не-черепа внутри соседства
        const r0 = roundOrCol, c0 = col0;
        const inside = [];
        for (let dr = -1; dr <= 1; dr++) for (let dc = -1; dc <= 1; dc++) {
          if (dr === 0 && dc === 0) continue;
          const r = r0 + dr, c = c0 + dc;
          if (r < 0 || r >= ROWS || c < 0 || c >= COLS) continue;
          const ni = r * COLS + c;
          const nc = S.board[ni];
          if (!nc || nc.type === 'empty') continue;
          if (nc.type !== 'skull') inside.push(ni);
        }
        const outside = [];
        for (let j = 0; j < CELL_COUNT; j++) {
          if (j === i) continue;
          const nj = S.board[j];
          if (!nj || nj.type !== 'skull') continue;
          const r = Math.floor(j / COLS), c = j % COLS;
          if (Math.abs(r - r0) <= 1 && Math.abs(c - c0) <= 1) continue;
          outside.push(j);
        }
        shuffle(inside); shuffle(outside);
        const pairs = Math.min(inside.length, outside.length);
        for (let k = 0; k < pairs; k++) {
          const a = inside[k], b = outside[k];
          const tmpA = S.board[a], tmpB = S.board[b];
          S.board[a] = tmpB;
          S.board[b] = tmpA;
        }
      } else if (t === 25) { // Magnetic — swap мечи/щиты вне соседства ↔ не-меч/щит внутри соседства
        const r0 = roundOrCol, c0 = col0;
        const inside = [];
        for (let dr = -1; dr <= 1; dr++) for (let dc = -1; dc <= 1; dc++) {
          if (dr === 0 && dc === 0) continue;
          const r = r0 + dr, c = c0 + dc;
          if (r < 0 || r >= ROWS || c < 0 || c >= COLS) continue;
          const ni = r * COLS + c;
          const nc = S.board[ni];
          if (!nc || nc.type === 'empty') continue;
          if (nc.type !== 'sword' && nc.type !== 'shield') inside.push(ni);
        }
        const outside = [];
        for (let j = 0; j < CELL_COUNT; j++) {
          if (j === i) continue;
          const nj = S.board[j];
          if (!nj || nj.type === 'empty') continue;
          if (nj.type !== 'sword' && nj.type !== 'shield') continue;
          const r = Math.floor(j / COLS), c = j % COLS;
          if (Math.abs(r - r0) <= 1 && Math.abs(c - c0) <= 1) continue;
          outside.push(j);
        }
        shuffle(inside); shuffle(outside);
        const pairs = Math.min(inside.length, outside.length);
        for (let k = 0; k < pairs; k++) {
          const a = inside[k], b = outside[k];
          const tmpA = S.board[a], tmpB = S.board[b];
          S.board[a] = tmpB;
          S.board[b] = tmpA;
        }
      } else if (t === 26) { // Brute — выбрасывает обычный череп, урон = HP этого черепа
        const ordinary = [];
        for (let j = 0; j < CELL_COUNT; j++) {
          const cj = S.board[j];
          if (cj && cj.type === 'skull' && !cj.skull.boss && !cj.skull.special && (cj.skull.enteredTurn || 0) < S.turn) ordinary.push(j);
        }
        if (ordinary.length > 0) {
          const pick = ordinary[randInt(0, ordinary.length - 1, 5)];
          const hp = S.board[pick].skull.hp;
          S.board[pick] = { type: 'empty' };
          const before = S.hero.hp;
          S.hero.hp = Math.max(0, S.hero.hp - hp);
          floatOn(S.hero.hp - before, 'hurt', '#hud-hp', 'HP');
          log('🪓 Брутал: −' + hp + ' HP');
        }
      } else if (t === 28) { // Devourer — таймер → уничтожать апгрейд
        s.countdown = (s.countdown || 0) - 1;
        if (s.countdown <= 0) {
          // Original DestroyUpgrade: pick a random equipment slot, then one of
          // its upgrades, and reduce that upgrade by one step.
          const item = S.equipment[randInt(0, S.equipment.length - 1, 5)];
          const keys = ITEM_KEYS.filter(key => itemValue(item, key) > 0);
          if (keys.length) {
            const key = keys[randInt(0, keys.length - 1, 5)];
            const amount = reduceItemModifier(item, key);
            queueAchievement(39);
            const message = '🦷 Съедобный: −' + itemNumber(key, amount) + ' «' + ITEM_STAT[key].name + '» (' + item.name + ')';
            flash(message); log(message);
          } else flash('🦷 Съедобный: выбранный предмет без улучшений');
          s.countdown = 5;
        }
      } else if (t === 29) { // Defiler — armor loss per block handled in damageHero
        // (no per-turn effect needed; see damageHero)
      } else if (t === 32 && s.phylactery) { // Lich phylactery: delay once, then revive with current-turn stats
        if (s.lichDelay) {
          s.lichDelay = false;
        } else {
          const fresh = makeSkull(false, true, 32);
          s.atk = fresh.atk; s.def = fresh.def; s.hp = fresh.hp; s.maxHp = fresh.hp;
          s.phylactery = false; s.countdown = 0;
          s.poison = [0, 0, 0];
          log('💀 Лич возродился из филактерия');
        }
      } else if (t === 31) { // Fleeing — уходит вверх, верхний ряд — исчезает без награды
        if (roundOrCol <= 0) {
          S.board[i] = { type: 'empty' };
          log('🏃 Бегун исчез');
        } else {
          const aboveIdx = (roundOrCol - 1) * COLS + col0;
          const above = S.board[aboveIdx];
          S.board[aboveIdx] = cell;
          S.board[i] = above || { type: 'empty' };
        }
      }
      // t === 5, 12, 15, 20, 30, 32 — handled elsewhere (stats / spawn / cooldown / attack loop / death)
    }

    if (unstablePending) unstableTeleport();
    // Vengeful counter reset — kills this turn
    S.killsThisTurn = 0;
  }

  // --- pointer input -------------------------------------------------------- --------------------------------------------------------
  let activeDrag = false, activePointerId = null;
  function onPointerMove(e) {
    if (!activeDrag || activePointerId != null && e.pointerId != null && e.pointerId !== activePointerId) return;
    const el = document.elementFromPoint(e.clientX, e.clientY);
    const cell = el ? el.closest('.cell') : null;
    if (!cell) return;
    const idx = parseInt(cell.dataset.idx, 10);
    if (isNaN(idx)) return;
    tryExtendChain(idx);
  }

  function cancelPointerGesture() {
    if (!activeDrag) return;
    activeDrag = false; activePointerId = null;
    window.removeEventListener('pointermove', onPointerMove);
    window.removeEventListener('pointerup', onPointerEnd);
    window.removeEventListener('pointercancel', onPointerEnd);
    if (S && !S.resolving && !S.pendingModal) clearChain();
  }

  function onPointerEnd(event) {
    if (activePointerId != null && event?.pointerId != null && event.pointerId !== activePointerId) return;
    if (event?.type === 'pointercancel') { cancelPointerGesture(); return; }
    if (!activeDrag) return;
    activeDrag = false; activePointerId = null;
    window.removeEventListener('pointermove', onPointerMove);
    window.removeEventListener('pointerup', onPointerEnd);
    window.removeEventListener('pointercancel', onPointerEnd);
    // тап по элитному черепу (без драга) → особенности
    const chain = S.selected.slice();
    if (chain.length === 1) {
      const cell = S.board[chain[0]];
      if (cell && cell.type === 'skull' && cell.skull && (cell.skull.special || cell.skull.boss)) {
        clearChain();
        showSkullModal(cell.skull);
        return;
      }
    }
    resolveChain();
  }

  function initInput() {
    const board = qs('#board');
    if (!board) return;

    board.addEventListener('pointerdown', e => {
      if (activeDrag || e.isPrimary === false || e.button > 0) return;
      if (!S || HISTORY_OPEN || SAVE_CONFLICT || S.over || S.resolving || S.pendingModal) return;
      e.preventDefault();
      const cell = e.target.closest('.cell');
      if (!cell) return;
      const idx = parseInt(cell.dataset.idx, 10);
      if (isNaN(idx)) return;
      activeDrag = true; activePointerId = e.pointerId;
      window.addEventListener('pointermove', onPointerMove);
      window.addEventListener('pointerup', onPointerEnd);
      window.addEventListener('pointercancel', onPointerEnd);
      tryExtendChain(idx);
    });
  }

  function flashOnInvalid() {}

  // --- class picker / next-run editor ---------------------------------------
  let pickerOpen = false;
  let pickerGeneration = 0;
  let classEditor = null;

  function renderClassPicker() {
    const grid = el('picker-grid');
    if (!grid) return;
    grid.innerHTML = '';
    const generation = ++pickerGeneration;
    const current = () => pickerOpen && !classEditor && generation === pickerGeneration;
    for (const cls of CLASSES) {
      const unlocked = isClassUnlocked(cls.id), config = getClassConfig(cls.id);
      const row = document.createElement('div'); row.className = 'picker-class';
      const btn = document.createElement('button');
      btn.type = 'button'; btn.dataset.classId = cls.id;
      btn.className = 'picker-card-btn' + (unlocked ? '' : ' locked') + (getSelectedClassId() === cls.id ? ' selected' : '');
      btn.setAttribute('aria-label', (unlocked ? 'Новый забег: ' : 'Закрытый мех: ') + cls.name);
      const desc = [];
      for (const kind of ['perks', 'flaws']) {
        const names = config[kind].filter(Boolean).map(name => CLASS_TRAITS[kind][name].name), positive = kind === 'perks';
        if (names.length) desc.push(`<span class="${positive ? 'pick-perk' : 'pick-flaw'}">${positive ? '+' : '−'} ${names.join(' · ')}</span>`);
      }
      const status = unlocked ? `<span class="pick-lvl">Уровень меха ${classLevelOf(cls.id)} · начать новый забег</span>`
        : '<span class="pick-locked">Закрыт · нужен трофей на поле</span>';
      btn.innerHTML = `<span class="pick-head"><span class="pick-icon">${mechEmblem(cls.native)}</span><span class="pick-identity"><span class="pick-model">КОМПЛЕКТ ${String(cls.native).padStart(2, '0')}</span><span class="pick-name">${cls.name}</span><span class="pick-race">Боец · ${RACE_INFO[config.race].name}</span></span><span class="pick-go" aria-hidden="true">${unlocked ? '↗' : '◇'}</span></span>${desc.join('')}<span class="pick-race-desc">${RACE_INFO[config.race].desc}</span><span class="pick-unique">Модуль: ${cls.uniqueSkillDesc}</span>${status}`;
      btn.addEventListener('click', () => {
        if (!current()) return;
        if (!isClassUnlocked(cls.id)) { flash('⚠ Мех закрыт: подбери трофей после победы над особыми черепами'); return; }
        requestNewGame(cls.id);
      });
      row.appendChild(btn);
      const edit = document.createElement('button');
      edit.type = 'button'; edit.className = 'class-config-btn'; edit.dataset.classId = cls.id;
      edit.textContent = classRules(cls.id).editable ? 'Настроить мех' : 'О мехе и развитии';
      edit.setAttribute('aria-label', edit.textContent + ': ' + cls.name);
      edit.addEventListener('click', () => { if (current()) openClassEditor(cls.id); });
      row.appendChild(edit); grid.appendChild(row);
    }
  }
  function closeClassEditor(focus = true) {
    const id = classEditor?.id;
    classEditor = null;
    const main = el('class-picker-main'), editor = el('class-editor'), card = el('picker-card');
    if (main) main.hidden = false;
    if (editor) editor.hidden = true;
    if (card) card.classList.remove('editor-open');
    if (focus && id) document.querySelector(`.class-config-btn[data-class-id="${id}"]`)?.focus();
  }
  function openClassEditor(id, reset = false) {
    const info = classEditorInfo(id), fields = el('class-editor-fields');
    if (!pickerOpen || !info || !fields) return false;
    const session = { id, runId: RUN_ID, draft: reset ? defaultClassConfig(id) : getClassConfig(id) };
    classEditor = session;
    const current = () => pickerOpen && classEditor === session && RUN_ID === session.runId;
    el('class-picker-main').hidden = true; el('class-editor').hidden = false;
    el('picker-card').classList.add('editor-open');
    const title = el('class-editor-title'); title.textContent = 'Мех «' + info.name + '»'; title.focus();
    el('class-editor-hint').textContent = !info.editable
      ? id === 'adventurer' ? 'Авантюрист не настраивается: Человек, без пассивов и изъянов; пул — все открытые навыки.'
        : 'Мех закрыт. Можно посмотреть правила; настройка откроется после трофея.'
      : `Уровень меха: ${info.level}. Это не уровень героя в забеге. Сохранение действует только на следующие забеги.`;
    fields.innerHTML = '';
    const controls = [], draft = session.draft;
    function block(label) {
      const section = document.createElement('fieldset'); section.className = 'class-config-section';
      const legend = document.createElement('legend'); legend.textContent = label; section.appendChild(legend); fields.appendChild(section);
      return section;
    }
    function note(parent, text) {
      const p = document.createElement('p'); p.className = 'class-config-note'; p.textContent = text; parent.appendChild(p); return p;
    }
    function select(parent, labelText, key, index, options, disabled) {
      const label = document.createElement('label'); label.className = 'class-config-label';
      const caption = document.createElement('span'); caption.textContent = labelText; label.appendChild(caption);
      const input = document.createElement('select'); input.dataset.kind = key;
      if (index != null) input.dataset.slot = String(index);
      for (const entry of options) {
        const option = document.createElement('option'); option.value = entry.value || ''; option.textContent = entry.label;
        option.disabled = !!entry.locked; input.appendChild(option);
      }
      input.value = (index == null ? draft[key] : draft[key][index]) || '';
      input.disabled = disabled; label.appendChild(input); parent.appendChild(label);
      controls.push({ input, key, index, options });
      input.addEventListener('change', () => {
        if (!current() || input.disabled || input.selectedOptions[0]?.disabled) return;
        if (index == null) draft[key] = input.value;
        else draft[key][index] = input.value || null;
        refresh();
      });
      return input;
    }
    const raceSection = block('Боец');
    select(raceSection, 'Боец следующего забега', 'race', null,
      Object.entries(RACE_INFO).map(([race, data]) => {
        const donor = CLASSES.find(c => c.race === race);
        const locked = !isRaceUnlocked(race);
        return { value: race, label: data.name + (locked ? ` · ${donor.name}, ур. 5` : ''), locked };
      }), !info.canChangeRace);
    note(raceSection, info.canChangeRace ? 'Доступны Человек и родные бойцы мехов, достигших уровня 5.' : 'Независимый выбор бойца — с уровня меха 5.');
    const raceDesc = note(raceSection, RACE_INFO[draft.race].desc);
    const traits = block('Пассивы и изъяны');
    note(traits, 'Первая пара фиксирована. Новые пары — на уровнях меха 3, 7 и 9. Пассиву нужен изъян в той же паре; повторы не складываются.');
    for (let i = 0; i < 4; i++) {
      const pair = document.createElement('div'); pair.className = 'class-config-pair'; traits.appendChild(pair);
      note(pair, `Пара ${i + 1}` + (i === 0 ? ' · основная' : ` · с уровня ${CLASS_PAIR_LEVELS[i]}`));
      for (const kind of ['perks', 'flaws']) select(pair, kind === 'perks' ? 'Пассив' : 'Изъян', kind, i,
        [{ value: '', label: 'Нет' }, ...Object.entries(CLASS_TRAITS[kind]).map(([name, data]) => ({
          value: name, label: data.name + ' — ' + data.desc + (isTraitUnlocked(kind, name) ? '' : ' · мех ещё закрыт'), locked: !isTraitUnlocked(kind, name),
        }))], !info.editable || i === 0 || !info.pairSlots[i]);
    }
    const skillSection = block(id === 'adventurer' ? 'Все открытые навыки' : 'Пул навыков: 20 позиций');
    const count = note(skillSection, ''); count.id = 'class-replacement-count'; count.setAttribute('role', 'status');
    note(skillSection, 'Это возможные награды уровня, не стартовые умения. Первый навык меха фиксирован. Повторы не увеличивают шанс выпадения.');
    if (id === 'adventurer') note(skillSection, ALL_SKILLS.filter(s => isSkillUnlocked(s.id)).sort((a,b) => a.native-b.native).map(s => s.name).join(' · '));
    else for (let i = 0; i < 20; i++) {
      const base = SKILL_BY_ID[info.baseSkills[i]];
      select(skillSection, `${i + 1}. ${base.name}` + (i === 0 ? ' · меховый' : ''), 'replacements', i,
        [{ value: '', label: base.name + ' · по умолчанию' }, ...ALL_SKILLS.filter(s => !s.nativeOnly && s.id !== base.id).sort((a,b) => a.native-b.native).map(skill => ({
          value: skill.id, label: skill.name + (isSkillUnlocked(skill.id) ? '' : ' · мех-источник: ур. 10'), locked: !isSkillUnlocked(skill.id),
        }))], !info.editable || i === 0 || info.replacementLimit === 0);
    }
    const growth = block('Развитие меха');
    note(growth, '1 — основная пара доступна другим мехам; 2/4/6/8/10 — можно заменить 2/4/6/8/10 навыков; 3/7/9 — новая пара; 5 — независимый выбор бойца и открытие родного бойца другим; 10 — уникальный навык доступен другим мехам. Рост — за трофеи, не за опыт героя.');
    const status = el('class-editor-status'), save = el('class-editor-save');
    const back = el('class-editor-back'), defaults = el('class-editor-reset');
    save.hidden = !info.editable; defaults.hidden = !info.editable;
    status.textContent = '';
    function refresh() {
      if (!current()) return;
      const used = draft.replacements.filter(Boolean).length;
      count.textContent = `Заменено ${used} из ${info.replacementLimit}`;
      raceDesc.textContent = RACE_INFO[draft.race].desc;
      for (const { input, key, index, options } of controls) for (let n = 0; n < options.length; n++) {
        const option = options[n], value = option.value;
        const duplicate = ['perks','flaws'].includes(key) && value && draft[key].some((other, i) => i !== index && other === value);
        const exhausted = key === 'replacements' && value && !draft.replacements[index] && used >= info.replacementLimit;
        input.options[n].disabled = !!(option.locked || duplicate || exhausted);
      }
      const unpaired = draft.perks.findIndex((perk, i) => perk && !draft.flaws[i]);
      save.disabled = !info.editable || unpaired >= 0 || used > info.replacementLimit;
      status.textContent = unpaired >= 0 ? `Выбери изъян для пары ${unpaired + 1} или убери её пассив.` : 'Изменения ещё не сохранены.';
    }
    // on* is replaced on every opening; the session identity also guards detached
    // controls and queued events after cancel/reset/newGame or switching classes.
    back.onclick = () => { if (current()) closeClassEditor(); };
    defaults.onclick = () => { if (current()) openClassEditor(id, true); };
    save.onclick = () => {
      if (!current() || save.disabled || !info.editable) return;
      if (!saveClassConfig(id, draft)) { status.textContent = 'Не удалось сохранить настройки в браузере. Освободи место или разреши локальное хранилище.'; return; }
      renderClassPicker(); closeClassEditor();
    };
    refresh(); return true;
  }
  function showClassPicker() {
    if (S?.resolving || S?.pendingModal) return false;
    const picker = el('class-picker');
    if (!picker) { newGame(); return true; }
    closeClassEditor(false); renderClassPicker(); pickerOpen = true;
    const resume = el('picker-resume'), info = savedRunInfo();
    if (resume) {
      resume.hidden = !(S && !S.over || info);
      resume.textContent = S && !S.over ? 'Вернуться в забег'
        : info ? `▶ Продолжить: ${CLASS_BY_ID[info.classId].name} · ход ${info.turn}` : 'Продолжить';
    }
    picker.classList.add('show');
    const card = el('picker-card'); if (card) card.scrollTop = 0;
    return true;
  }
  function resumeFromClassPicker() {
    if (!pickerOpen) return;
    if (!S || S.over) { resumeSavedRun(); return; }
    if (SAVE_CONFLICT) { flash('Обнови страницу: сохранение изменено в другой вкладке'); return; }
    closeClassEditor(false); pickerOpen = false; el('class-picker').classList.remove('show');
    el('menu-btn')?.focus();
  }

  function requestNewGame(classId = getSelectedClassId()) {
    if (!isClassUnlocked(classId)) return false;
    if ((!S || S.over) && !savedRunInfo() && !hasPendingProgress()) { setSelectedClassId(classId); newGame(); return true; }
    const wrap = el('restart-modal');
    if (!wrap) return false;
    NEW_RUN_REQUEST = { classId, runId: RUN_ID, focus: document.activeElement };
    const text = el('restart-confirm-text');
    if (text) text.textContent = (hasPendingProgress() ? 'Результат или достижения ещё не записаны в историю. Новый может удалить их резервную запись. ' : '') + `Текущий забег будет заменён новым: «${CLASS_BY_ID[classId].name}». Вернуть его после этого не получится. Настройки и уровни мехов сохранятся.`;
    wrap.classList.add('show'); el('restart-cancel')?.focus();
    return false;
  }
  function cancelNewGame() {
    const previous = NEW_RUN_REQUEST; NEW_RUN_REQUEST = null;
    el('restart-modal')?.classList.remove('show'); previous?.focus?.focus();
  }

  // Opt-in Telegram UI bridge. This is NOT authentication: the Pages demo is
  // public; admission only controls its bot launcher. Only the SDK numeric ID
  // selects a local namespace. No initData/profile is sent to a server or cloud.
  let TELEGRAM_APP = null;
  let TELEGRAM_BACK = null, TELEGRAM_CONFIRM = null, TELEGRAM_EXIT = null;
  function telegramCall(target, method, ...args) {
    try { if (typeof target?.[method] === 'function') { target[method](...args); return true; } }
    catch (ignored) { /* Old clients/SDK failures must not break the game. */ }
    return false;
  }
  function telegramNotice(text) {
    for (const id of ['picker-telegram-status', 'menu-telegram-status']) {
      const node = el(id); if (node) { node.textContent = text; node.hidden = false; }
    }
  }
  function telegramLayer(id) { return !!el(id)?.classList.contains('show'); }
  function syncTelegramChrome() {
    if (!TELEGRAM_APP) return;
    const live = !!S && !S.over;
    // Back belongs to nested views only. At the board/reward root Telegram
    // must keep its native Close control, not trap the user in a menu loop.
    const back = HISTORY_OPEN || !!classEditor || pickerOpen && live
      || ['help-modal', 'restart-modal', 'menu-modal', 'exit-modal'].some(telegramLayer)
      || ['stats', 'skull'].includes(S?.pendingModal);
    if (back !== TELEGRAM_BACK) { telegramCall(TELEGRAM_APP.BackButton, back ? 'show' : 'hide'); TELEGRAM_BACK = back; }
    if (live !== TELEGRAM_CONFIRM) { telegramCall(TELEGRAM_APP, live ? 'enableClosingConfirmation' : 'disableClosingConfirmation'); TELEGRAM_CONFIRM = live; }
  }
  function cancelTelegramExit(focus = true) {
    const previous = TELEGRAM_EXIT; TELEGRAM_EXIT = null;
    const wrap = el('exit-modal');
    if (wrap) { wrap.classList.remove('show'); wrap.hidden = true; }
    if (focus) previous?.focus?.focus();
  }
  function requestTelegramClose(discard = false) {
    if (!TELEGRAM_APP) return false;
    cancelPointerGesture();
    // A pending reward is a resumable checkpoint, not a reason to forbid exit.
    // close() can bypass the native closing prompt, so an unsuccessful write
    // needs our explicit, accessible discard confirmation before that request.
    const saved = !S || saveRun();
    if (!saved && !discard) {
      const wrap = el('exit-modal');
      if (!wrap) return false;
      TELEGRAM_EXIT = { runId: RUN_ID, focus: document.activeElement };
      el('exit-confirm-text').textContent = 'Текущий момент не удалось сохранить. После закрытия последние действия могут потеряться; продолжение не гарантируется. Остаться и повторить запись или всё равно закрыть?';
      wrap.hidden = false; wrap.classList.add('show'); el('exit-cancel')?.focus();
      syncTelegramChrome();
      return false;
    }
    cancelTelegramExit(false);
    if (telegramCall(TELEGRAM_APP, 'close')) return true;
    telegramNotice('Команда закрытия недоступна. Вернись в игру и используй кнопку закрытия Telegram в шапке.');
    flash('Для выхода используй кнопку закрытия Telegram в шапке');
    syncTelegramChrome();
    return false;
  }
  function telegramBack() {
    cancelPointerGesture();
    if (telegramLayer('exit-modal')) cancelTelegramExit();
    else if (telegramLayer('restart-modal')) cancelNewGame();
    else if (telegramLayer('help-modal')) { el('help-modal').classList.remove('show'); el('help')?.focus(); }
    else if (HISTORY_OPEN) closeRunHistory();
    else if (classEditor) closeClassEditor();
    else if (pickerOpen && S && !S.over) resumeFromClassPicker();
    else if (telegramLayer('menu-modal')) { el('menu-modal').classList.remove('show'); el('menu-btn')?.focus(); }
    else if (S?.pendingModal === 'stats' || S?.pendingModal === 'skull') closeModal();
    else if (S?.pendingModal || S?.resolving) flash('Сначала заверши ход и выбор наград');
    syncTelegramChrome();
  }
  function initTelegramPreview() {
    if (new URLSearchParams(window.location.search).get('telegram') !== '1') return;
    telegramNotice('Подключение Telegram… Сохранения здесь могут отличаться от браузерных.');
    const timeout = setTimeout(() => telegramNotice('Telegram SDK не загрузился. Без профиля сохранение недоступно, жесты Telegram не защищены. Открой Mini App заново.'), 8000);
    function connect() {
      clearTimeout(timeout);
      const tg = window.Telegram?.WebApp;
      // Presence is only a UI hint, never a trusted identity/permission check.
      if (!tg?.initData) { telegramNotice('Профиль Telegram не определён: открой Рейд кнопкой в своей личке бота. Игра без профиля не сохраняется.'); return; }
      if (TELEGRAM_APP) return;
      connectLocalProfile();
      TELEGRAM_APP = tg;
      document.documentElement.classList.add('telegram-preview');
      const close = el('menu-close'); if (close) close.textContent = 'Закрыть Mini App';
      const root = document.documentElement.style;
      const validHeight = value => Number.isFinite(value) && value >= 160 && value <= 10000;
      const inset = value => Number.isFinite(value) && value >= 0 && value <= 160 ? value : 0;
      function viewport(event) {
        if (event?.isStateStable === false) return;
        const height = validHeight(tg.viewportStableHeight) ? tg.viewportStableHeight : tg.viewportHeight;
        if (validHeight(height)) root.setProperty('--raid-tg-height', height + 'px');
        for (const side of ['top', 'bottom', 'left', 'right']) {
          root.setProperty('--raid-tg-safe-' + side, inset(tg.safeAreaInset?.[side]) + 'px');
          root.setProperty('--raid-tg-content-' + side, inset(tg.contentSafeAreaInset?.[side]) + 'px');
        }
      }
      telegramCall(tg, 'ready'); telegramCall(tg, 'expand');
      let canDisable = typeof tg.disableVerticalSwipes === 'function';
      try { if (typeof tg.isVersionAtLeast === 'function') canDisable = canDisable && tg.isVersionAtLeast('7.7'); } catch (ignored) { canDisable = false; }
      const protectedSwipes = canDisable && telegramCall(tg, 'disableVerticalSwipes');
      telegramCall(tg, 'setHeaderColor', '#140e22'); telegramCall(tg, 'setBackgroundColor', '#140e22');
      viewport();
      for (const event of ['viewportChanged', 'safeAreaChanged', 'contentSafeAreaChanged', 'activated']) telegramCall(tg, 'onEvent', event, viewport);
      telegramCall(tg, 'onEvent', 'deactivated', () => { cancelPointerGesture(); saveRun(); });
      telegramCall(tg.BackButton, 'onClick', telegramBack);
      document.addEventListener('click', syncTelegramChrome);
      // Includes Escape and programmatic reader/editor changes, without timers
      // or touching any gameplay state. Mutations of host chrome are external.
      if (typeof window.MutationObserver === 'function') {
        const observer = new window.MutationObserver(syncTelegramChrome);
        for (const id of ['class-picker', 'class-editor', 'history-modal', 'help-modal', 'restart-modal', 'exit-modal', 'menu-modal', 'modal']) {
          const node = el(id); if (node) observer.observe(node, { attributes: true, attributeFilter: ['class', 'hidden'] });
        }
      }
      syncTelegramChrome();
      telegramNotice('Telegram · ' + String(tg.platform || 'клиент').slice(0, 24) + ' · локальный пробник' + (protectedSwipes ? '' : ' · свайпы Telegram не отключены: нужен более новый клиент.'));
    }
    if (window.Telegram?.WebApp) { connect(); return; }
    const sdk = document.createElement('script');
    sdk.src = 'https://telegram.org/js/telegram-web-app.js'; sdk.async = true;
    sdk.onload = connect;
    sdk.onerror = () => { clearTimeout(timeout); telegramNotice('Telegram SDK недоступен. Без профиля игра не сохраняется, жесты Telegram не защищены.'); };
    document.head.appendChild(sdk);
  }

  // --- init -----------------------------------------------------------------
  function init() {
    initRaidAppearance();
    initTelegramPreview();
    readRunSlot(); readRunHistory();
    initInput();
    window.addEventListener?.('pagehide', () => { cancelPointerGesture(); saveRun(); });
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') { cancelPointerGesture(); saveRun(); } });
    window.addEventListener?.('storage', event => {
      if (event.key === profileStorage.key(RUN_HISTORY_KEY) || event.key === null) { if (HISTORY_OPEN) renderRunHistory(); else readRunHistory(); }
      if (event.key !== profileStorage.key(RUN_SAVE_KEY) && event.key !== null) return;
      // Finished pages still own timers/pagehide callbacks: never adopt a new
      // tab's baseline and then overwrite it with our old terminal checkpoint.
      if (S) ownsRunSlot();
      else { readRunSlot(); if (pickerOpen && !classEditor) showClassPicker(); }
    });
    el('picker-history')?.addEventListener('click', openRunRecords);
    el('menu-history')?.addEventListener('click', openRunRecords);
    for (const id of ['picker-achievements', 'menu-achievements']) el(id)?.addEventListener('click', openAchievements);
    el('history-close')?.addEventListener('click', closeRunHistory);
    for (const id of ['history-section', 'history-type', 'achievement-filter']) el(id)?.addEventListener('change', renderRunHistory);
    el('history-retry')?.addEventListener('click', () => { retryRunHistory(); renderRunHistory(); });
    el('history-modal')?.addEventListener('keydown', event => {
      if (!HISTORY_OPEN) return;
      if (event.key === 'Escape') { event.preventDefault(); closeRunHistory(); }
      if (event.key === 'Tab') {
        const controls = [...el('history-modal').querySelectorAll('button, select')].filter(node => !node.hidden && node.getClientRects().length);
        const first = controls[0], last = controls[controls.length - 1];
        if (controls.length && (event.shiftKey && (document.activeElement === first || document.activeElement === el('history-title')) || !event.shiftKey && document.activeElement === last)) {
          event.preventDefault(); (event.shiftKey ? last : first).focus();
        }
      }
    });
    el('restart-cancel')?.addEventListener('click', cancelNewGame);
    el('restart-confirm')?.addEventListener('click', () => {
      const request = NEW_RUN_REQUEST;
      if (!request || request.runId !== RUN_ID) return;
      setSelectedClassId(request.classId); newGame();
    });
    el('restart-modal')?.addEventListener('keydown', event => {
      if (!NEW_RUN_REQUEST) return;
      if (event.key === 'Escape') { event.preventDefault(); cancelNewGame(); }
      if (event.key === 'Tab') {
        const first = el('restart-cancel'), last = el('restart-confirm');
        if (event.shiftKey && document.activeElement === first || !event.shiftKey && document.activeElement === last) {
          event.preventDefault(); (event.shiftKey ? last : first)?.focus();
        }
      }
    });
    el('exit-cancel')?.addEventListener('click', () => cancelTelegramExit());
    el('exit-confirm')?.addEventListener('click', () => {
      if (TELEGRAM_EXIT?.runId === RUN_ID) requestTelegramClose(true);
    });
    el('exit-modal')?.addEventListener('keydown', event => {
      if (!TELEGRAM_EXIT) return;
      if (event.key === 'Escape') { event.preventDefault(); cancelTelegramExit(); }
      if (event.key === 'Tab') {
        const first = el('exit-cancel'), last = el('exit-confirm');
        if (event.shiftKey && document.activeElement === first || !event.shiftKey && document.activeElement === last) {
          event.preventDefault(); (event.shiftKey ? last : first)?.focus();
        }
      }
    });
    const restartBtn = qs('#restart');
    if (restartBtn) restartBtn.addEventListener('click', () => requestNewGame());
    const pickerResume = el('picker-resume'); if (pickerResume) pickerResume.addEventListener('click', resumeFromClassPicker);
    const picker = el('class-picker');
    if (picker) picker.addEventListener('keydown', event => {
      if (!pickerOpen || NEW_RUN_REQUEST) return;
      if (event.key === 'Escape') { event.preventDefault(); if (classEditor) closeClassEditor(); else resumeFromClassPicker(); }
      if (event.key !== 'Tab') return;
      const controls = [...picker.querySelectorAll('button:not(:disabled), select:not(:disabled), [tabindex="0"]')].filter(node => node.getClientRects().length);
      const target = event.shiftKey ? controls[controls.length - 1] : controls[0];
      if (controls.length && (event.shiftKey ? document.activeElement === controls[0] : document.activeElement === controls[controls.length - 1])) { event.preventDefault(); target.focus(); }
    });
    const menuBtn = qs('#menu-btn');
    const menuModal = qs('#menu-modal');
    if (menuBtn && menuModal) {
      menuBtn.addEventListener('click', () => { if (S) saveRun(); menuModal.classList.add('show'); });
      const hide = () => menuModal.classList.remove('show');
      const resume = qs('#menu-resume'); if (resume) resume.addEventListener('click', hide);
      const close = qs('#menu-close'); if (close) close.addEventListener('click', () => { if (TELEGRAM_APP) requestTelegramClose(); else hide(); });
      const classes = el('menu-classes'); if (classes) classes.addEventListener('click', () => {
        if (S?.resolving || S?.pendingModal) { flash('Сначала заверши ход и выбор наград'); return; }
        hide(); showClassPicker();
      });
      const restart = qs('#menu-restart'); if (restart) restart.addEventListener('click', () => requestNewGame());
      const mhelp = qs('#menu-help'); if (mhelp) mhelp.addEventListener('click', () => {
        hide();
        const wrap = qs('#help-modal');
        if (wrap) wrap.classList.add('show');
      });
    }
    const statsBtn = qs('#stats-btn');
    if (statsBtn) statsBtn.addEventListener('click', () => {
      if (S && !S.resolving && !S.pendingModal && !S.over && !SAVE_CONFLICT) openModal('stats');
    });
    const helpBtn = qs('#help');
    if (helpBtn) helpBtn.addEventListener('click', () => {
      const wrap = qs('#help-modal');
      if (wrap) wrap.classList.toggle('show');
    });
    const helpClose = qs('#help-close');
    if (helpClose) helpClose.addEventListener('click', () => {
      const wrap = qs('#help-modal');
      if (wrap) wrap.classList.remove('show');
    });
    showClassPicker();
  }

  // expose for testing / debugging
  window.RAID = {
    get state() { return S; },
    newGame,
    saveRun,
    resumeSavedRun,
    savedRunInfo,
    get saveStatus() { return { ...RUN_SAVE_STATUS }; },
    getLocalHistory() { return readRunHistory()?.data || null; },
    retryRunHistory,
    getLocalAchievements,
    openAchievements,
    openRunHistory,
    closeRunHistory,
    get historyStatus() { return { ...HISTORY_STATUS }; },
    tryExtendChain,
    resolveChain,
    activateSkill,
    setSelectedClassId,
    getSelectedClassId,
    classRules,
    openClassEditor,
    closeClassEditor,
    classEditorInfo,
    defaultClassConfig,
    getClassConfig,
    saveClassConfig,
    classSkillPool,
    hasPerk,
    hasFlaw,
    clsRace,
    isRaceUnlocked,
    isTraitUnlocked,
    interiorCells,
    swordDamage,
    baseDamage,
    runScore,
    pretzelPoints,
    pretzelCrossings,
    showClassPicker,
    chooseLevelOptions,
    checkTrophy,
    claimTrophy,
    isClassUnlocked,
    classLevelOf,
    unlockClass,
    levelUpClass,
    renderCharacterSummary,
    makeUpgradeOffer,
    makeItem,
    makeShopOffers,
    purchaseShopItem,
    skillDuration,
    bonusPct,
    trophyEligible,
    prepareSkillOption,
    installSkillOption,
    isSkillUnlocked,
    canActivateSkill,
    classicScrollWord,
    itemStep,
    itemUpgradeAllowed,
    randomItemUpgrade,
    applyUpgradeOffer,
    reduceItemModifier,
    updateEquipmentStats,
    equipmentTotals,
    equipItem,
    itemModsText,
    computeTrophyOutcome,
    specialSpawnCount,
    makeSkull,
    showSkullModal,
    placeNewTiles,
    applySpecialAbilities,
    enemyPhase,
    processEnemyAttack,
    endEnemyTurn,
    enemyGetAttack,
    explodeKamikazeCell,
    unstableTeleport,
    applyChainEffects,
    damageHero,
    damageSkull,
    hitSkullAt,
    collectIndices,
    addResource,
    specialRewardTypes,
    chainPreview,
    refillRandomSkill,
    checkLevelUp,
    checkGoldBox,
    checkUpgradeBar,
    createTile,
    createNewTile,
    hasLegalChain,
    chainTypeForCell,
    canChainSelect,
    effectiveChain,
    chainAllows,
    isAdjacentArrow,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
