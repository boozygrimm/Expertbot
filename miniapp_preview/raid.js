// miniapp_preview/raid.js
// Пробник Рейда на классических механиках (Fireflame Games, Dungeon Raid).
// Свой код, своя графика, своя стилистика. Запускать через lunapark-raid.html.
'use strict';
(function () {
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
  // Урон цепочки со мечом: (база + сила + рост «Урона» + наследник класса)
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
      ['Класс', cls.name + ' · ' + (cls.raceName || cls.race)],
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
    return keys.length ? keys.map(key => itemModText(key, itemValue(item, key))).join(' · ') : 'без бонусов';
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
        : { maxArmor: slot.id === 'armour' ? (clsFlaw() === 'Slim' ? 1 : 2) : 1 } }));
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
    S.pierce = Math.min(.95, Number(((clsFlaw() === 'Cautious' ? .30 : .50) + next.pierce).toFixed(2)));
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
    if (key === 'regen') return CLASS_BY_ID[S.classId]?.perk === 'Faithful' ? 2 : 1;
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
    const slot = itemSlotById(slotId), item = S.equipment.find(entry => entry.slot === slotId);
    const key = randomItemUpgrade(slotId, !itemValue(item, 'quicken')), value = itemStep(key);
    const before = itemValue(item, key);
    return { slot: slot.id, slotName: slot.name, icon: slot.icon, key, value, name: ITEM_STAT[key].name,
      label: itemModText(key, value), desc: ITEM_STAT[key].desc,
      comparison: `${itemNumber(key, before)} → ${itemNumber(key, before + value)}` };
  }
  function applyUpgradeOffer(offer) {
    const item = S.equipment.find(entry => entry.slot === offer.slot);
    if (!item || !ITEM_STAT[offer.key]) return false;
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
  // КЛАССЫ И РАСЫ (классика: 10 классов, каждый — Perk/Flaw/Race/Уникальный навык)
  // String IDs are our stable profile keys. Numeric native IDs order random
  // trophy unlocks independently of the presentation order in this picker.
  // ============================================================
  const CLASSES = [
    { id: 'adventurer', native: 0, name: 'Авантюрист', icon: '❔', race: 'Human', uniqueSkill: 'teleport', pool: ['steal', 'freeze', 'heal', 'repair', 'counter', 'shatter', 'goldtouch', 'teleport', 'explosive', 'elixir', 'enchant', 'slash', 'treasure', 'scavenge', 'disarm', 'dazzle', 'boostdmg', 'boostgold', 'boosthealth', 'boostarmour'],
      perk: '—', flaw: '—', raceName: 'Человек',
      perkDesc: 'нет пассивов', flawDesc: 'нет изъянов',
      clsDesc: 'Стартовый класс без пассива и изъяна; получает все открытые навыки',
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

  // --- уникальные навыки классов (заполнение — после парраметров/классов) ---
  // Телепорт/Затачивание/Мороз/... — в SKILLS; ниже — уникальные навыки классов,
  // которые предлагаются в меню уровня; глобальный unlock — на ур.10 класса.
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

  // --- класс выбранный пользователем (пикер в начале забега) ---
  function loadStoredClassId() {
    try {
      const raw = localStorage.getItem('raid:class');
      return (raw && CLASS_BY_ID[raw]) ? raw : 'adventurer';
    } catch (e) { return 'adventurer'; }
  }
  // --- уровни классов (персистентные, localStorage) ---
  const CLASS_LEVELS_KEY = 'raid:classLevels';
  let classLevels = loadClassLevels();
  function loadClassLevels() {
    try {
      const raw = localStorage.getItem(CLASS_LEVELS_KEY);
      let obj = raw ? JSON.parse(raw) : {};
      if (!obj || typeof obj !== 'object' || Array.isArray(obj)) obj = {};
      // localStorage — внешние данные: чистим значения, иначе строка из него
      // попадёт в innerHTML карточки класса (и в арифметику levelUpClass).
      const clean = {};
      for (const id of Object.keys(obj)) {
        if (!CLASS_BY_ID[id]) continue;
        const lvl = Number(obj[id]);
        if (Number.isFinite(lvl) && lvl >= 1) clean[id] = Math.min(10, Math.floor(lvl));
      }
      clean.adventurer = Math.max(1, clean.adventurer || 1);
      return clean;
    } catch (e) { return { adventurer: 1 }; }
  }
  function saveClassLevels() {
    try { localStorage.setItem(CLASS_LEVELS_KEY, JSON.stringify(classLevels)); } catch (e) {}
  }
  function classLevelOf(id) { return (classLevels && classLevels[id]) || 0; }
  function isClassUnlocked(id) { return classLevelOf(id) >= 1; }
  function unlockClass(id) {
    if (!CLASS_BY_ID[id]) return;
    classLevels[id] = Math.max(1, classLevelOf(id));
    saveClassLevels();
  }
  function levelUpClass(id) {
    if (!CLASS_BY_ID[id]) return;
    if (!isClassUnlocked(id)) classLevels[id] = 1;
    else classLevels[id] = Math.min(10, (classLevelOf(id) || 1) + 1);   // original cap: level 10
    saveClassLevels();
  }

  let selectedClassId = loadStoredClassId();
  function getSelectedClassId() {
    return isClassUnlocked(selectedClassId) ? selectedClassId : 'adventurer';
  }
  function setSelectedClassId(id) {
    if (!CLASS_BY_ID[id]) return;
    if (!isClassUnlocked(id)) return;
    selectedClassId = id;
    try { localStorage.setItem('raid:class', id); } catch (e) {}
  }
  // concept-art sprites from the catalog (window.SKULL_ART injected by the build)
  const SKULL_ART = (typeof window !== 'undefined' && window.SKULL_ART) || {};
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
  let AFTER_ARRIVALS = null;
  let DRAINING_ARRIVALS = false;
  // Идентификатор забега: отложенные setTimeout резолва/enemy phase не должны
  // попадать в новый забег после рестарта (см. newGame + resolveChain).
  let RUN_ID = 0;

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
      classId: 'adventurer',       // текущий класс забега (см. CLASSES)
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
      skillDurations: {}, nextTurnFlags: {}, menuCandidates: [],
      compatibilityNotices: [], scrollNativeWords: null,
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
      runeUsedThisTurn: 0,         // замкнутая цепочка собрана этим ходом
      runeCollected: 0,            // замкнутая цепочка уже собрана
      lastStand: 0,                // полное лечение при смерти этого хода
      lastStandUsed: 0,            // Last Stand уже использован за забег
      brokenShields: 0,            // сколько щитов выпало сломанными (rogue flaw)
      baseDamageBonus: 0,           // equipment stat1: flat chain-damage bonus
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
    const cls = S && S.classId ? CLASS_BY_ID[S.classId] : null;
    if (t === 'shield' && cls && cls.flaw === 'Clumsy' && randInt(1, 100, 1) < 4) {
      S.brokenShields++;
      return { type: 'shield', broken: true };
    }
    if (t === 'potion' && cls && cls.perk === 'Gifted' && randInt(1, 100, 1) < 6) {
      return { type: 'potion', mana: true };
    }
    if (t === 'sword' && cls && cls.perk === 'Agile' && randInt(1, 100, 1) < 6) {
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
    if (cls && cls.id !== 'adventurer' && randInt(1, 200, 1) <= (cls.perk === 'Intimidating' ? 5 : 10)) {
      type = MORTAL_BY_RACE[cls.race];
    }
    return type;
  }

  function makeSkull(boss, special, typeId) {
    const cls = S && S.classId ? CLASS_BY_ID[S.classId] : null;
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
      if (cls && cls.flaw === 'Zealous') promotionInterval = Math.max(1, promotionInterval - 1);
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
    if (cls && cls.race === 'Halfling' && S && S.board && effType !== 5
        && S.board.some(c => c && c.type === 'skull' && c.skull && c.skull.type === 5 && c.skull.hp > 0)) {
      def *= 2;
    }
    // Оригинал (Tile::GenerateStats): Sneaky (flaw=3) даёт +1 HP только обычным черепам
    const flawBonusHp = (!boss && !special && cls && cls.flaw === 'Sneaky') ? 1 : 0;
    // Оригинал: Noisy (flaw=9) → спец/босс +5% HP
    const noisy = (boss || special) && cls && cls.flaw === 'Noisy' ? 1.05 : 1;
    hp = Math.floor(Math.fround(Math.fround(hp) * Math.fround(noisy))) + flawBonusHp;
    // Спец/босс: минимальные значения
    if (boss) atk = Math.max(1, atk);
    if (special && effType !== 3 && effType !== 30) atk = Math.max(1, atk); // Golden (3): A=0; Meat Shield (30): trunc(A/2)
    // Orc's mortal enemy (Corrosive): its appearance drops the hero's armour to 0.
    if (special && effType === 12 && cls && cls.race === 'Orc' && S && S.hero) {
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
    const zealous = clsFlaw() === 'Zealous';
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
        S.promotionRemaining = S.mode === 'sprint' ? 200 : (skull.promotionInterval ?? 0);
        S.specialsPromoted = (S.specialsPromoted || 0) + 1;
      }
      return { type: 'skull', skull };
    }
    return decorateTile(type);
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
    if (S.resolving || S.over || S.pendingModal || cellIdx < 0 || cellIdx >= CELL_COUNT) return;
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
    if (cell.type === 'sword') return '<svg viewBox="0 0 24 24" class="ic" aria-hidden="true"><path d="M5 19 14.5 9.5 20 4 18 2 12.5 7.5 3 17 2.5 21.5 7 21Z" fill="currentColor"/><path d="M15 6 19.5 10.5 21 9 16.5 4.5Z" fill="currentColor"/><path d="M6 18 4 20 8 20 6 18Z" fill="currentColor"/></svg>';
    if (cell.type === 'arrow') return '<svg viewBox="0 0 24 24" class="ic" aria-hidden="true"><path d="M20 4 4 20" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" fill="none"/><path d="M4 13 4 20 11 20" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>';
    if (cell.type === 'shield') return '<svg viewBox="0 0 24 24" class="ic" aria-hidden="true"><path d="M12 2 20 5.5V12c0 4.6-3.2 8.4-8 10-4.8-1.6-8-5.4-8-10V5.5L12 2Z" fill="currentColor"/><path d="M12 5 17.5 7.2V12c0 3.3-2.3 6.1-5.5 7.5-3.2-1.4-5.5-4.2-5.5-7.5V7.2L12 5Z" fill="#1c1428" opacity="0.35"/></svg>';
    if (cell.type === 'coin') return '<svg viewBox="0 0 24 24" class="ic" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="currentColor"/><circle cx="12" cy="12" r="6.5" fill="none" stroke="#1c1428" stroke-width="1.4" opacity="0.45"/><text x="12" y="16" text-anchor="middle" font-size="9" font-weight="900" fill="#1c1428" opacity="0.55">¤</text></svg>';
    if (cell.type === 'potion' && cell.acid) return '<svg viewBox="0 0 24 24" class="ic" aria-hidden="true"><path d="M12 2.5 10 6 8.5 9.5C7.6 11.6 6.8 13 6.8 14.8A5.2 5.2 0 0 0 12 20 5.2 5.2 0 0 0 17.2 14.8C17.2 13 16.4 11.6 15.5 9.5L14 6Z" fill="currentColor"/><path d="M9 12l2.5 2.5 2.5-3.5 2.5 2.5" stroke="#1c1428" stroke-width="1.5" fill="none"/></svg>';
    if (cell.type === 'potion' && cell.mana) return '<svg viewBox="0 0 24 24" class="ic" aria-hidden="true"><path d="M12 2.5 10 6 8.5 9.5C7.6 11.6 6.8 13 6.8 14.8A5.2 5.2 0 0 0 12 20 5.2 5.2 0 0 0 17.2 14.8C17.2 13 16.4 11.6 15.5 9.5L14 6Z" fill="currentColor"/><path d="M12 9.5 13 12 15.5 13 13 14 12 16.5 11 14 8.5 13 11 12Z" fill="#fff" opacity="0.85"/></svg>';
    if (cell.type === 'potion') return '<svg viewBox="0 0 24 24" class="ic" aria-hidden="true"><path d="M12 2.5 10 6 8.5 9.5C7.6 11.6 6.8 13 6.8 14.8A5.2 5.2 0 0 0 12 20 5.2 5.2 0 0 0 17.2 14.8C17.2 13 16.4 11.6 15.5 9.5L14 6Z" fill="currentColor"/><path d="M12 2.5 12 6" stroke="#1c1428" stroke-width="1.6"/><circle cx="12" cy="14.6" r="2.4" fill="#fff" opacity="0.5"/></svg>';
    if (cell.type === 'trophy') return '<svg viewBox="0 0 24 24" class="ic" aria-hidden="true"><path d="M7 3h10v7a5 5 0 0 1-4 4.9V19h4v2H7v-2h4v-4.1A5 5 0 0 1 7 10Zm0 2H3v3a5 5 0 0 0 4 4m10-7h4v3a5 5 0 0 1-4 4" fill="currentColor" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>';
    if (cell.type === 'chain') return '<svg viewBox="0 0 24 24" class="ic" aria-hidden="true"><path d="M9 7 7.5 8.5a4 4 0 0 0 0 5.6L9 15.6M15 7l1.5 1.5a4 4 0 0 1 0 5.6L15 15.6" stroke="currentColor" stroke-width="2.2" fill="none" stroke-linecap="round"/><path d="M8 12h8" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>';
    if (cell.type === 'skull') return '<svg viewBox="0 0 24 24" class="ic" aria-hidden="true"><path d="M12 3C7 3 4 6.5 4 10.5c0 2 1 3.7 2.2 4.6L6.5 18c0 1.4 1.1 2.5 2.5 2.5h2l1 2.2 1 2.2h3l1-2.2 1-2.2h2c1.4 0 2.5-1.1 2.5-2.5l-.7-2.9c1.2-.9 2.2-2.6 2.2-4.6C20 6.5 17 3 12 3Z" fill="currentColor"/><circle cx="9" cy="11" r="2.2" fill="#1c1428"/><circle cx="15" cy="11" r="2.2" fill="#1c1428"/><path d="M11 15h2l-1 2" fill="#1c1428"/></svg>';
    return '';
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
        img.src = SKULL_ART[s.art] || '';
        SKULL_IMAGES.set(s.uid, img);
      }
      img.className = 'skull-art' + (boss ? ' boss' : special ? ' special' : '');
      img.alt = boss ? 'Босс-череп' : special ? 'Спец-череп' : 'Череп';
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
      const text = (boss ? '👑 ' : special ? '◆ ' : '') + `${s.atk}·${s.def}·${s.hp}` + (s.countdown > 0 ? ` ⏱${s.countdown}` : '');
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
    cellEl.title = cell.type === 'trophy' ? (cell.frozen ? '🏆 Трофей заморожен — сначала разморозь' : '🏆 Нажми: рост или открытие класса, без хода врагов') : '';
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
    const cls = S && S.classId ? CLASS_BY_ID[S.classId] : null;
    const raw = BASE_DAMAGE + S.hero.stats.strength + 2 * S.hero.stats.damage + (cls && cls.perk === 'Strong' ? 1 : 0) + (S.baseDamageBonus || 0);
    // Mortal enemy Frozen: пока он на доске, базовый урон героя усечён вдвое.
    if (cls && cls.race === 'Dragonkin' && hasMortalEnemy(8)) return Math.floor(raw / 2);
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
    const cls = S && S.classId ? CLASS_BY_ID[S.classId] : null;
    return !!(cls && cls.flaw === 'Confused') && isEarlyGame();
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
    const damage = (skulls.length ? baseDamage() : 0) + S.hero.weaponDamage * swordCount * (S.swordBoost > 0 ? 2 : 1);
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
    const cocky = clsFlaw() === 'Cocky' && chain.length <= 3;
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
    if (el('chain-count')) el('chain-count').textContent = '⛓ ' + (preview ? preview.effectiveLength : 0);
    if (!preview || (preview.effectiveLength < 3 && !preview.interior.length)) {
      tip.classList.remove('show'); tip.textContent = ''; return;
    }
    const parts = [], res = preview.resources;
    if (preview.swordCount || preview.skulls.length) parts.push(`💥 ${preview.damage} урона`, `☠ ${preview.lethals.length}/${preview.skulls.length}`);
    if (res.armorGained) parts.push(`🛡 +${res.armorGained} брони`);
    if (res.upGained) parts.push(`⬆ +${res.upGained} UP`);
    if (res.goldGained) parts.push(`💰 +${res.goldGained} золота`);
    if (res.xpGained) parts.push(`⭐ +${res.xpGained} XP`);
    if (res.healGained) parts.push(`❤ +${res.healGained} HP`);
    if (preview.acid) parts.push(`☣ −${preview.acid} HP`);
    if (preview.mana) parts.push(`🔹 ${preview.mana} маны`);
    if (preview.specialRewards) parts.push('награда особых — случайная');
    if (preview.interior.length) parts.push(`🔮 внутри: ${preview.interior.length} (особые остаются)`);
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
    const cls = CLASS_BY_ID[S.classId];
    const offset = kind === 'coin' && cls && cls.perk === 'Frugal' ? 10
      : kind === 'shield' && cls && cls.flaw === 'Reckless' ? -10 : 0;
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
      damageLabel.textContent = `⚔ ${fmt(weapon)}/меч · 💥 ${fmt(baseDamage())}`;
      damageLabel.title = `Урон цепочки: база ${fmt(baseDamage())} + ${fmt(weapon)} за каждый меч; защита врага поглощает урон до HP.`;
    }
    if (el('turn-text')) el('turn-text').textContent = `ХОД ${S.turn}`;
    if (el('status-line')) {
      const score = runScore();
      S.score = score;
      el('status-line').textContent = `Ход ${S.turn} · Уровень ${h.level} · Очки ${score}`;
    }
    if (el('mode-badge')) {
      const modeName = S.mode === 'sprint' ? 'Спринт' : S.mode === 'pretzel' ? 'Крендель' : 'Классика';
      const diffName = ['Лёгкий', 'Обычный', 'Сложный', 'Очень сложный'][S.difficulty || 0];
      el('mode-badge').textContent = modeName + ' · ' + diffName;
    }
    if (el('chain-count')) el('chain-count').textContent = '⛓ ' + (S.selected ? S.selected.length : 0);
    if (el('trophy-text')) {
      const onBoard = S.board.filter(cell => cell.type === 'trophy').length;
      const text = onBoard ? 'на поле: ' + onBoard : S.difficulty === 0 ? 'не на лёгком'
        : S.trophiesGenerated >= MAX_TROPHIES_PER_RUN ? 'лимит 3/3'
        : !trophyEligible() ? 'рост закрыт'
        : 'через ' + (S.mode === 'sprint' && S.trophyRemaining > 0 ? '≥' : '') + Math.max(1, S.trophyRemaining);
      el('trophy-text').textContent = '🏆 ' + text;
      el('trophy-text').title = 'Тап по трофею на поле — рост класса без хода врагов. Не больше 3 появлений за забег; интервал 20–30 переносится между забегами. В Спринте счётчик уменьшается с шансом 50/101.';
    }
    // upgrade bar
    const u = el('upgrade-bar'); if (u) u.style.width = Math.min(100, S.upgradeBar / UPGRADE_BAR_MAX * 100) + '%';
    if (el('upgrade-text')) el('upgrade-text').textContent = `${fmt(S.upgradeBar)} / ${UPGRADE_BAR_MAX}`;
    // skills
    renderSkills();
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
        empty.disabled = true;
        empty.innerHTML = '<span class="skill-icon">＋</span><span class="skill-name">пусто</span>';
        empty.title = 'Слот навыка откроется на уровне 2';
        row.appendChild(empty);
        continue;
      }
      const left = S.skillCooldowns[skillId] || 0;
      const level = (S.skillLevels && S.skillLevels[skillId]) || 1;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'skill' + (left > 0 ? ' on-cd' : '');
      btn.innerHTML = `<span class="skill-icon">${skill.icon}</span><span class="skill-name">${skill.name}</span>${left > 0 ? `<span class="cd">${left}</span>` : (level > 1 ? `<span class="cd">ур.${level}</span>` : '')}`;
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
    RUN_ID++;
    AFTER_ARRIVALS = null;
    RESOURCE_BATCH = null;
    seedRandom(runSeed);
    const remaining = S ? S.trophyRemaining : readTrophyRemaining();
    S = newGameState();
    S.trophyRemaining = remaining;
    saveTrophyRemaining();
    const oldModal = el('modal');
    if (oldModal) oldModal.classList.remove('show');
    S.seed = runSeed;
    S.rngSeeds = Array.from(RNG_SEEDS);
    SKULL_UID = 1;
    SKULL_IMAGES.clear();

    // --- class setup: perks/flaws/race (сохраняем класс между перезапусками) ---
    // Mode / difficulty choose the original rule set independently.
    const modeEl = qs('#mode-select');
    const diffEl = qs('#difficulty-select');
    S.mode = (modeEl && modeEl.value) || (() => { try { return localStorage.getItem('raid:mode') || 'classic'; } catch (e) { return 'classic'; } })();
    S.difficulty = Number(( diffEl && diffEl.value) || (() => { try { return localStorage.getItem('raid:difficulty') || '1'; } catch (e) { return '1'; } })());
    if (!['classic', 'sprint', 'pretzel'].includes(S.mode)) S.mode = 'classic';
    S.difficulty = Math.max(0, Math.min(3, S.difficulty || 0));
    try { localStorage.setItem('raid:mode', S.mode); localStorage.setItem('raid:difficulty', String(S.difficulty)); } catch (e) {}
    S.classId = getSelectedClassId();
    let cls = CLASS_BY_ID[S.classId];
    if (!cls) {       // класс недоступен (заблокирован) — Adventurer как старт
      S.classId = 'adventurer';
      cls = CLASS_BY_ID.adventurer;
    }

    // Pretzel: base HP 100 and HP growth is removed.
    if (S.mode === 'pretzel') { S.hero.maxHp = 100; S.hero.hp = 100; }
    // Perps классов (fixed at start):
    if (cls.flaw === 'Slim') { S.hero.maxArmor = Math.max(1, S.hero.maxArmor - 1); S.hero.armor = Math.min(S.hero.armor, S.hero.maxArmor); }
    if (cls.perk === 'Tough') { S.hero.maxHp += 10; S.hero.hp += 10; }   // Warrior perk
    if (cls.flaw === 'Frail') { S.hero.maxHp = Math.max(1, S.hero.maxHp - 10); S.hero.hp = Math.min(S.hero.hp, S.hero.maxHp); }  // Mage flaw

    S.equipment = initialEquipment();
    S.equipmentApplied = equipmentTotals();

    // Priest flaw Cautious: pierce threshold 30% instead of the base 50%.
    if (cls.flaw === 'Cautious') S.pierce = 0.30;
    // Original promotion counter (Sprint gets a 200 sentinel, not a delay).
    S.promotionRemaining = S.mode === 'sprint' ? 200 : (cls.flaw === 'Zealous' ? 49 : 50);

    // Уникальный навык класса добавляется по желанию игрока при выборе 2-из-4
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
  }

  function isSelectable(cellType) { return cellType !== 'empty'; }
  function clsRace() { const c = S.classId ? CLASS_BY_ID[S.classId] : null; return c ? c.race : 'Human'; }
  function clsFlaw() { const c = S.classId ? CLASS_BY_ID[S.classId] : null; return c ? c.flaw : '—'; }

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
      const cunning = S.skills.length === 4 && CLASS_BY_ID[S.classId]?.perk === 'Cunning';
      S.cooldownAdjust[id] = cunning ? -1 : 0;
      duration -= S.quicken + (cunning ? 1 : 0);
    }
    S.skillDurations[id] = duration;
    S.skillCooldowns[id] = Math.max(0, duration - Math.min(option.charge, duration));
    S.skillLevels[id] = option.level;
    return true;
  }
  function isSkillUnlocked(id) {
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
    if (S.over || S.resolving || S.pendingModal || S.selected.length) return;
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
    continueAfterRewards(() => {
      S.resolving = false;
      renderHUD(); renderBoard();
    });
  }

  function fireBreathOnSkill() {
    if (S.classId && CLASS_BY_ID[S.classId].race === 'Dragonkin') {
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
      const dmg = damageSkull(skull, amount, opts);
      if (dmg > 0) floatOn('💥 ' + dmg, 'hurt');
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
      S.slashCombo = 1;
      let idxs;
      if (S.hackCombo > 0) {
        idxs = [];
        for (let i = 0; i < CELL_COUNT; i++) {
          const c = S.board[i];
          if (c && c.type !== 'skull') idxs.push(i);
        }
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
      fresh.hp = Math.min(fresh.hp, old ? old.hp : fresh.hp);
      fresh.maxHp = fresh.hp;
      fresh.enteredTurn = old && old.enteredTurn != null ? old.enteredTurn : S.turn;
      S.board[i] = { type: 'skull', skull: fresh };
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
    advanceTrophyAt(slot);
    // Native charge=cooldown means ZERO turns remaining in our representation.
    // Shapeshift may hit a ready slot (unlike mana) and applies to every special
    // collection path, not just deaths inside a manually selected chain.
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
    if (DRAINING_ARRIVALS || RESOURCE_BATCH || S.pendingModal || S.over) return;
    DRAINING_ARRIVALS = true;
    try {
      while (S.arrivals.length && !S.pendingModal && !S.over) {
        const event = S.arrivals.shift();
        if (event.kind === 'resource') {
          const delta = addResource(event.token);
          for (const key of ['xpGained', 'goldGained', 'upGained', 'armorGained', 'healGained', 'manaCollected']) event.result[key] += delta[key];
        } else if (event.kind === 'healing') heal(event.amount);
        else if (event.kind === 'damage') {
          damageHero(event.amount, false, true);
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
      next();
    }
  }

  function continueAfterRewards(next) {
    if (S.over) { S.resolving = false; return; }
    const runId = RUN_ID;
    AFTER_ARRIVALS = () => { if (runId === RUN_ID && !S.over) next(); };
    drainArrivals();
  }

  function requestRewardMenu(kind) {
    if (S.pendingModal) S.rewardMenus.push(kind);
    else openModal(kind);
  }

  function levelReached(state) {
    state.hero.level++;
    state.maxLevel = Math.max(state.maxLevel, state.hero.level);
    if (CLASS_BY_ID[state.classId]?.race !== 'Faun') return 0;
    const before = state.hero.hp;
    state.hero.hp = Math.max(before, Math.floor(state.hero.maxHp * .25));
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
      const cls = CLASS_BY_ID[S.classId];
      const threshold = cls && cls.perk === 'Daring' && count >= 10 ? 100 : bonusPct(type);
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
    const cls = CLASS_BY_ID[state.classId];
    if (token.type === 'potion') {
      const skillId = state.skills[token.skillSlot];
      if (token.variant === 1 && skillId) {
        const before = state.skillCooldowns[skillId] || 0;
        state.skillCooldowns[skillId] = Math.max(0, before - 1);
        result.manaCollected = before - state.skillCooldowns[skillId];
        return result;
      }
      const amount = HEAL_PER_POTION + hero.stats.luck;
      if (token.variant === 2) {
        hero.hp = Math.max(0, hero.hp - amount);
        if (state === S && hero.hp <= 0 && !lastStandTriggerIfNeeded()) gameOver();
      } else {
        const before = hero.hp;
        hero.hp = Math.min(hero.maxHp, hero.hp + amount * (state.potionBoost > 0 ? 2 : 1));
        result.healGained = hero.hp - before;
        if (!(cls && cls.race === 'Elf' && hasEnemy(9))) state.poison = 0;
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
    if (S.hero.hp !== before) floatOn('+' + Math.round(S.hero.hp - before), 'heal');
  }

  function repair(amount) {
    const before = S.hero.armor;
    S.hero.armor = Math.min(S.hero.maxArmor, S.hero.armor + amount);
    if (S.hero.armor !== before) floatOn('+' + Math.round(S.hero.armor - before), 'armor');
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

  function floatOn(text, kind) {
    const board = qs('#board');
    if (!board) return;
    const node = document.createElement('div');
    node.className = 'float-fx ' + kind;
    node.textContent = text;
    board.appendChild(node);
    setTimeout(() => node.remove(), 900);
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

  function chainDamageFor(idx, chain, damage) {
    const skull = S.board[idx].skull;
    if (damageBlockedEarly()) return 0;
    const position = chain.indexOf(idx);
    const previous = chain[position - 1], next = chain[position + 1];
    if (skull.type === 27) return next != null && Math.floor(next / COLS) < Math.floor(idx / COLS) ? damage : 0;
    const uppercut = clsRace() === 'Dwarf' && previous != null && next != null
      && previous % COLS === idx % COLS && next % COLS === idx % COLS
      && Math.floor(previous / COLS) > Math.floor(idx / COLS) && Math.floor(next / COLS) < Math.floor(idx / COLS);
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
      const meatShields = columnSlots().filter(idx => S.board[idx] && S.board[idx].type === 'skull' && S.board[idx].skull.type === 30 && !chain.includes(idx));
      for (const idx of effective) {
        const cell = S.board[idx];
        if (!cell || cell.type === 'empty') continue;
        if (cell.type === 'skull' && !cell.skull.phylactery) {
          hitSkullAt(idx, targetDamage.get(idx) ?? 0, { direct: true });
        } else matchCell(idx, { reward: !(clsFlaw() === 'Cocky' && chain.length <= 3) });
      }
      for (const idx of meatShields) hitSkullAt(idx, damage, { direct: false });
      batch.result.dmgShown = hasSkull ? damage : 0;
      S.swordsHits += hasSkull ? damage : 0;
      // Choose a deterministic creation-order arrival policy, not animation
      // timing. These labels pause behind any resource menu in the same queue.
      if (S.leech > 0 && hasSkull) queueArrival({ kind: 'healing', amount: Math.floor(Math.fround(damage * Math.fround(S.leech))) });
      if (burning > 0) queueArrival({ kind: 'damage', amount: burning });
      if (spiky && damage > 0) queueArrival({ kind: 'damage', amount: damage });
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

  function resolveChain() {
    if (S.resolving || S.over || S.pendingModal) return;
    const chain = S.selected.slice(), effective = effectiveChain(chain);
    if (chain.length === 1 && S.board[chain[0]].type === 'trophy') { claimTrophy(chain[0]); return; }
    const rune = S.runeActive ? interiorCells(chain) : [];
    const committed = effective.length >= 3;
    if (!committed && !rune.length) { clearChain(); return; }
    const skulls = effective.filter(idx => S.board[idx].type === 'skull' && !isPhylacteryCell(S.board[idx]));
    const swords = effective.reduce((sum, idx) => sum + (S.board[idx].broken ? 0 : swordWeight(S.board[idx])), 0);
    const damage = (skulls.length ? baseDamage() : 0) + S.hero.weaponDamage * swords * (S.swordBoost > 0 ? 2 : 1);
    const snapshot = { chain, effective, hasSkull: skulls.length > 0, damage,
      spiky: skulls.some(idx => S.board[idx].skull.type === 2),
      targetDamage: new Map(skulls.map(idx => [idx, chainDamageFor(idx, chain, damage)])),
      burning: effective.reduce((sum, idx) => {
        const cell = S.board[idx], target = cell.type === 'skull' ? cell.skull : cell;
        return sum + (target.burn && !target.fireSafe ? target.fireDamage ?? (typeof target.burn === 'number' ? target.burn : 0) : 0);
      }, 0) };
    S.resolving = true;
    S.actionCommitted = committed;
    if (committed) {
      S.chainCount++;
      if (S.mode === 'pretzel') S.pretzelScore += pretzelPoints(pretzelCrossings(chain)).points;
    }
    S.selected = []; S.chainType = null;
    const runId = RUN_ID;
    renderSelection(); renderChainLine(); renderChainTip();
    for (const idx of committed ? effective : []) {
      const node = qs(`.cell[data-idx="${idx}"]`);
      if (node && S.board[idx].type !== 'skull') node.classList.add('pop');
    }
    setTimeout(() => {
      if (runId !== RUN_ID) return;
      const result = withResourceBatch(batch => {
        // Rune deliberately precedes minimum-chain validation and the normal
        // MatchTiles counter reset. Interior ordinary skulls are COLLECTION,
        // ignoring HP/defence and not contributing to direct kills or Guts.
        for (const idx of rune) {
          const cell = S.board[idx];
          if (cell && !(cell.type === 'skull' && (cell.skull.special || cell.skull.boss))) matchCell(idx);
        }
        if (!committed) {
          // Keep the playable board full after a Rune-only arrow cancellation;
          // no ordinary matching, no counter reset and no enemy turn.
          compactBoardAndRefill(); markExistingSkullsOld();
          return batch.result;
        }
        resetCollectionCounts();
        applyChainEffects(swords, 0, 0, 0, skulls, snapshot);
        S.killsThisTurn = batch.result.kills;
        markExistingSkullsOld();
        placeNewTiles({});
        // AddRevengeHealth and post-kill passives run after refill, so a newly
        // generated Vengeful also receives this chain's direct kill count.
        for (const cell of S.board) if (cell && cell.type === 'skull' && cell.skull.type === 21) cell.skull.atk += batch.result.kills;
        if (clsRace() === 'Halfling' && batch.result.kills >= 15) {
          const armour = S.equipment.find(item => item.slot === 'armour');
          armour.mods.maxArmor = (armour.mods.maxArmor || 0) + 1;
          updateEquipmentStats();
          flash('🍀 Храбрость: 15+ убийств за цепочку — +1 макс. брони!');
        }
        if (S.berserkActive > 0 && batch.berserkHealing > 0) heal(batch.berserkHealing);
        return batch.result;
      });
      renderBoard(); renderHUD();
      continueAfterRewards(() => {
        if (result.xpGained > 0 || result.goldGained > 0) log(`⚔ +${result.xpGained} XP · +${result.goldGained} золота · убийств ${result.kills}`);
        if (!committed) { S.resolving = false; renderBoard(); renderHUD(); return; }
        setTimeout(() => { if (runId === RUN_ID && !S.over) enemyPhase(); }, 550);
      });
    }, 420);
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
    return { attack, blocked, damage: Math.max(0, attack - armour), armourLoss, hadArmour: armour > 0 };
  }

  // One normal volley. Kept separate from EndOfTurn so regressions can test
  // the snapshot, poison, spikes and Counterattack without later abilities.
  function processEnemyAttack() {
    return withResourceBatch(() => {
      const cls = CLASS_BY_ID[S.classId];
      const attackers = S.board.filter(cell => oldSkull(cell) && !frozenSkull(cell) && enemyGetAttack(cell.skull) > 0);
      if (!attackers.length || (cls && cls.perk === 'Unexpected' && isEarlyGame())) return null;
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
          for (const idx of columnSlots()) if (oldSkull(S.board[idx])) hitSkullAt(idx, S.spikes, { direct: false });
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
          const skull = cell.skull, hp = skull.hp + enemyGetAttack(skull);
          if (clsRace() === 'Werewolf') { skull.hp = hp; skull.maxHp = Math.max(hp, skull.maxHp); }
          else skull.hp = Math.min(skull.maxHp, hp);
        }
        damageHero(volley.damage, false, true);
      } else audioEnemy();
      return volley;
    });
  }

  function enemyPhase() {
    if (S.over || S.pendingModal) return;
    S.resolving = true;
    S.actionCommitted = true;
    processEnemyAttack();
    renderBoard(); renderHUD();
    // Spikes/counter resources (and their menus) settle before turn++.
    continueAfterRewards(endEnemyTurn);
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
            if (target.hp <= amount) hitSkullAt(idx, amount, { direct: false, ignoreDefence: true });
            else target.hp -= amount;
          }
        }
      }
      if (S.regen > 0) heal(S.regen);
      if (S.poison > 0) {
        damageHero(S.poison, false, true);
        flash('☠ Яд: −' + S.poison + ' HP');
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
          if (skull.hp <= poison[0]) matchCell(idx);
          else skull.hp -= poison[0];
        }
      }
      if (clsRace() === 'Werewolf') {
        let crippled = 0;
        for (let idx = 0; idx < CELL_COUNT; idx++) {
          const cell = S.board[idx];
          if (cell && cell.type === 'skull' && isCrippleTarget(cell.skull)) { matchCell(idx); crippled++; }
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
      S.freezeNextTurn = 0;
      if (S.promotionRemaining > 0) S.promotionRemaining--;
      S.actionCommitted = false;
      // Victory is at the tail, never before lethal turn-100 poison/abilities.
      if (S.mode === 'sprint' && S.turn >= 100) {
        S.won = true; S.over = true; S.resolving = false; S.actionCommitted = false;
        S.arrivals = []; S.rewardMenus = []; AFTER_ARRIVALS = null;
        openModal('gameover');
      }
    });
    renderBoard(); renderHUD();
    continueAfterRewards(() => {
      if (!hasLegalChain(S.board)) { ensureChainable(); log('♻ Нет ходов — поле перемешано'); }
      renderBoard(); renderHUD();
      S.resolving = false;
      checkTrophy();
    });
  }

  function damageHero(amount, isBoss, bypassArmor) {
    if (amount <= 0) return 0;
    const damage = bypassArmor ? amount : blockVolley(amount).damage;
    if (damage > 0) {
      S.hero.hp = Math.max(0, S.hero.hp - damage);
      floatOn('-' + Math.round(damage), 'hurt');
    }
    audioEnemy();
    return damage;
  }

  // Last Stand (Paladin, классный уникальный навык): если герой погиб — полное лечение.
  // Вызывается всегда после урона в бою, если Hero.hp <= 0.
  function lastStandTriggerIfNeeded() {
    if (S.lastStand > 0 && S.hero.hp <= 0) {
      S.hero.hp = S.hero.maxHp;
      S.lastStand = S.nextTurnFlags.lastStand = 0;
      flash('🛡 Последний рубеж: полное лечение!');
      return true;
    }
    return false;
  }

  // --- modals ---------------------------------------------------------------
  function openModal(kind) {
    S.pendingModal = kind;
    const wrap = el('modal');
    const title = el('modal-title');
    const body = el('modal-body');
    if (!wrap || !body) return;
    wrap.className = 'modal show';
    title.className = 'modal-title';
    if (kind === 'level' || kind === 'gold' || kind === 'armor') {
      const cssKind = kind === 'gold' ? 'shop' : kind;
      wrap.classList.add(cssKind);
      title.classList.add(cssKind);
    }

    if (kind === 'level') {
      title.textContent = '⬆ УРОВЕНЬ!';
      body.innerHTML = '<p class="modal-hint">Выбери 2 из 4 улучшений</p>';
      const options = chooseLevelOptions();
      renderLevelChoices(options, body);
    } else if (kind === 'gold') {
      title.textContent = '💰 МАГАЗИН!';
      body.innerHTML = '<p class="modal-hint">Выбери 1 из 3 замен. Бонусы старой вещи снимаются, а не складываются с новой.</p>';
      renderEquipChoices(body);
    } else if (kind === 'armor') {
      title.textContent = '🛡 АПГРЕЙД!';
      body.innerHTML = '<p class="modal-hint">Выбери 1 из 4 улучшений надетых вещей. Бонус останется на выбранном предмете.</p>';
      renderUpgradeChoices(body);
    } else if (kind === 'stats') {
      title.textContent = '📊 СВОЙСТВА';
      body.innerHTML = '';
      const summary = document.createElement('div');
      summary.className = 'character-summary';
      renderCharacterSummary(summary);
      body.appendChild(summary);
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn';
      btn.textContent = 'Закрыть';
      btn.addEventListener('click', () => closeModal());
      body.appendChild(btn);
    } else if (kind === 'gameover') {
      title.textContent = S.won ? '🏆 ПОБЕДА!' : '☠ ИГРА ОКОНЧЕНА';
      body.innerHTML = `<p class="modal-hint">${S.won ? 'Спринт пройден: 100 ходов!' : 'Герой погиб на ходу <strong>' + S.turn + '</strong>'}</p>
        <div class="stats-grid">
          <div><span>⚔</span><small>убито черепов</small><strong>${S.skullsKilled}</strong></div>
          <div><span>⬆</span><small>максимальный уровень</small><strong>${S.maxLevel}</strong></div>
          <div><span>💰</span><small>золота собрано</small><strong>${S.goldEarned}</strong></div>
          <div><span>🏆</span><small>трофеев забрано</small><strong>${S.trophiesThisRun || 0}</strong></div>
        </div>`;
      const restart = document.createElement('button');
      restart.type = 'button';
      restart.className = 'btn secondary';
      restart.textContent = '↻ Ещё раз';
      restart.addEventListener('click', () => { closeModal(); newGame(); });
      body.appendChild(restart);
    }
  }

  function closeModal() {
    const wrap = el('modal');
    if (wrap) wrap.classList.remove('show');
    S.pendingModal = null;
    const runId = RUN_ID;
    setTimeout(() => {
      if (runId !== RUN_ID) return;
      if (S.pendingModal || S.over) return;
      if (S.rewardMenus.length) openModal(S.rewardMenus.shift());
      else { checkLevelUp(); checkGoldBox(); checkUpgradeBar(); }
      drainArrivals();
      if (!S.resolving && !S.pendingModal && !S.over) checkTrophy();
    }, 80);
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
    const wrap = el('modal');
    const title = el('modal-title');
    const body = el('modal-body');
    if (!wrap || !body) return;
    S.pendingModal = 'skull';
    const boss = !!skull.boss;
    wrap.classList.remove('level', 'shop', 'armor', 'trophy', 'gameover', 'skull');
    wrap.className = 'modal show skull';
    title.className = 'modal-title skull';
    title.textContent = boss ? '👑 БОСС-ЧЕРЕП' : ('◆ ' + specialTypeName(skull).toUpperCase() + ' ЧЕРЕП');
    body.innerHTML = '';

    const img = document.createElement('img');
    img.className = 'skull-feat-art';
    img.src = SKULL_ART[skull.art] || '';
    img.alt = boss ? 'Босс-череп' : ('Спец-череп: ' + specialTypeName(skull));
    body.appendChild(img);

    const stats = document.createElement('p');
    stats.className = 'skull-feat-stats';
    const cd = skull.countdown > 0 ? ` · ⏱ ${skull.countdown}` : '';
    stats.innerHTML = `<b>⚔ ${skull.atk}</b> · <b>🛡 ${skull.def}</b> · <b>❤️ ${skull.hp}</b>${cd}`;
    body.appendChild(stats);

    const feats = document.createElement('div');
    feats.className = 'skull-feat-list';
    const items = boss
      ? ['Усиленная элитность — как оригинальный Boss (×3 ⚔ / ×3 🛡 / ×6 ❤️).',
         'Броня не ниже 1, HP ограничен бонусом Громкого (+5% HP).',
         'Демонстрируется в обертке 👑; каждый босс — «трофейный» череп.']
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
    btn.addEventListener('click', () => closeModal());
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
      const value = Number(localStorage.getItem('raid:trophyRemaining'));
      return Number.isFinite(value) ? Math.max(0, Math.min(30, Math.trunc(value))) : 0;
    } catch (e) { return 0; }
  }
  function saveTrophyRemaining() {
    try { localStorage.setItem('raid:trophyRemaining', String(S.trophyRemaining)); } catch (e) {}
  }
  function advanceTrophyAt(slot) {
    if (S.difficulty === 0) return;
    if (S.mode !== 'sprint' || randInt(0, 100, 0) < 50) S.trophyRemaining--;
    if (S.trophyRemaining < 1) {
      if (S.trophiesGenerated < MAX_TROPHIES_PER_RUN && trophyEligible()) {
        S.trophyRemaining = randInt(20, 30, 0);
        S.trophiesGenerated++;
        // Own SVG, but preserve SetType's numeric appearance draw.
        S.board[slot] = { type: 'trophy', appearance: randInt(0, 7, 0) };
      } else S.trophyRemaining = 0;
    }
    saveTrophyRemaining();
  }
  function computeTrophyOutcome() {
    const id = S.classId, level = classLevelOf(id);
    if (id !== 'adventurer' && level < 10 && randInt(0, 1, 5) !== 0) return levelOutcome(id, level);
    const locked = CLASSES.filter(cls => !isClassUnlocked(cls.id)).sort((a, b) => a.native - b.native);
    if (locked.length) {
      const cls = locked[randInt(0, locked.length - 1, 5)];
      return { kind: 'unlock', clsId: cls.id, text: '✨ Класс «' + cls.name + '» разблокирован! (ур. 1)' };
    }
    if (level < 10) return levelOutcome(id, level);
    // Never level a different, already unlocked class as a fallback.
    return { kind: 'none', clsId: null, text: '🏆 Трофей собран; текущий класс уже на уровне 10.' };
  }
  function levelOutcome(id, level) {
    return { kind: 'levelup', clsId: id,
      text: '⬆ Класс «' + CLASS_BY_ID[id].name + '» поднялся на ур. ' + Math.min(10, level + 1) + '!' };
  }
  function checkTrophy() {
    return S.board.flatMap((cell, slot) => cell.type === 'trophy' && !cell.frozen && !cell.matched ? [slot] : []);
  }
  function claimTrophy(slot) {
    if (S.over || S.resolving || S.pendingModal || !Number.isInteger(slot)) return false;
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
    renderBoard(); renderHUD(); audioLevel();
    return true;
  }

  function chooseLevelOptions() {
    const owned = S.skills, sprint = S.mode === 'sprint';
    const upgradable = owned.filter(id => id !== 'wish' && (S.skillLevels[id] || 1) < 10);
    const cls = CLASS_BY_ID[S.classId];
    const ordered = ALL_SKILLS.slice().sort((a, b) => a.native - b.native);
    let pool = ordered.filter(skill => owned.length === 4 ? owned.includes(skill.id)
      : cls.id === 'adventurer' ? isSkillUnlocked(skill.id) : cls.pool.includes(skill.id)).map(skill => skill.id);
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
    const grid = document.createElement('div');
    grid.className = 'lv-grid';
    const picked = new Set();
    const choiceRow = document.createElement('div');
    choiceRow.className = 'lv-status';
    choiceRow.textContent = 'Выбери 2 из 4 (0/2)';
    body.appendChild(choiceRow);

    function mark() {
      choiceRow.textContent = `Выбери 2 из 4 (${picked.size}/2)`;
      grid.querySelectorAll('.lv-card').forEach((node, i) => {
        node.classList.toggle('picked', picked.has(i));
      });
    }

    options.forEach((opt, i) => {
      const isStat = STATS.includes(opt);
      const isUpgrade = !!opt.upgrade;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'lv-card ' + (isStat ? 'lv-stat' : 'lv-skill');
      if (isUpgrade) {
        const sk = opt.skill || SKILL_BY_ID[opt.id] || {};
        const lvl = (S.skillLevels && S.skillLevels[opt.id]) || 1;
        btn.innerHTML = `<span class="lv-icon-box"><span class="lv-icon">${sk.icon || '✦'}</span><span class="lv-corner">${lvl}→${lvl + 1}</span></span>
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
        btn.innerHTML = `<span class="lv-icon-box"><span class="lv-icon">${opt.icon}</span><span class="lv-corner">${opt.level}</span></span>
          <span class="lv-body">
            <span class="lv-row"><span class="lv-name">${opt.name}</span><span class="lv-cool">${opt.duration > 0 ? 'Кулдаун: ' + (opt.duration - S.quicken - (S.skills.length === 3 && CLASS_BY_ID[S.classId]?.perk === 'Cunning' ? 1 : 0)) : 'Одноразовый'}</span></span>
            <span class="lv-desc">${opt.desc}</span>
          </span>`;
      }
      btn.addEventListener('click', () => {
        if (S.pendingModal !== 'level' || S.menuCandidates !== options || picked.size >= 2) return;
        if (picked.has(i)) {
          picked.delete(i);
          mark();
          return;
        } else {
          picked.add(i);
          audioPick();
          mark();
          if (picked.size === 2) {
            // Preserve click order; a prepared skill is not initialised twice.
            picked.forEach(idx => {
              const option = options[idx];
              if (STATS.includes(option)) option.apply();
              else installSkillOption(option);
            });
            S.hero.xpNext = 100;
            closeModal();
            renderBoard();
            renderHUD();
            audioLevel();
          }
        }
        mark();
      });
      grid.appendChild(btn);
    });
    body.appendChild(grid);
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
    // I1 is an unknown native MEMORY WORD, not "skill already owned".
    // Safe playable policy: omit only that unproven scroll, never invent the
    // word or block the equipment purchase. Known 10% exemptions work normally.
    // A complete raw-word fixture can be supplied through the debug state.
    if (!exempt && !ordered.every(skill => Number.isInteger(S.scrollNativeWords?.[skill.native]))) {
      item.scrollBlocked = true;
      noteCompatibility('I1: свиток без 10%-го исключения пропущен — неизвестен нативный фильтр.');
      return options;
    }
    const eligible = ordered.filter(skill => (exempt || S.scrollNativeWords[skill.native] === 0)
      && isSkillUnlocked(skill.id) && canActivateSkill(skill.id));
    if (!eligible.length) {
      item.scrollBlocked = true;
      noteCompatibility('I1: предоставленный фильтр свитка не дал кандидатов; свиток пропущен.');
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
    if (!continuedAction && !continuation) continueAfterRewards(() => {
      S.resolving = false; renderBoard(); renderHUD();
    });
  }
  function purchaseShopItem(index) {
    if (S.pendingModal !== 'gold' || !Number.isInteger(index)) return false;
    const item = S.menuCandidates[index];
    if (!item || !itemSlotById(item.slot)) return false;
    equipItem(item); // Recompute BEFORE scroll activation.
    closeModal();   // Remove the shop BEFORE Enchant can open another menu.
    if (item.scroll) castPurchasedScroll(item.scroll);
    renderBoard(); renderHUD(); audioLevel();
    return true;
  }
  function appendEquipmentChoices(body, grid) {
    const summaryBtn = document.createElement('button');
    summaryBtn.type = 'button';
    summaryBtn.className = 'btn secondary shop-summary-toggle';
    summaryBtn.textContent = '📊 Персонаж и экипировка';
    summaryBtn.setAttribute('aria-controls', 'equipment-summary');
    summaryBtn.setAttribute('aria-expanded', 'false');
    const summary = document.createElement('div');
    summary.id = 'equipment-summary';
    summary.className = 'character-summary';
    summary.hidden = true;
    renderCharacterSummary(summary);
    summaryBtn.addEventListener('click', () => {
      const show = summary.hidden;
      summary.hidden = !show;
      grid.hidden = show;
      summaryBtn.setAttribute('aria-expanded', String(show));
      summaryBtn.textContent = show ? '↩ К выбору предмета' : '📊 Персонаж и экипировка';
    });
    body.appendChild(summaryBtn);
    body.appendChild(grid);
    body.appendChild(summary);
  }
  function renderEquipChoices(body) {
    const options = makeShopOffers();
    S.menuCandidates = options;
    const grid = document.createElement('div');
    grid.className = 'shop-grid';
    options.forEach((item, index) => {
      const equipped = S.equipment.find(entry => entry.slot === item.slot);
      const changes = itemChanges(equipped, item).map(change =>
        `<span class="item-change ${change.after > change.before ? 'better' : 'worse'}">${change.text}</span>`).join('');
      const scroll = item.scroll
        ? `<span class="shop-scroll">📜 ${item.scroll.name} — разово при покупке</span><span class="shop-preview">${item.scroll.desc}</span>`
        : item.scrollBlocked ? '<span class="shop-compatibility">Свиток пропущен: фильтр оригинала не расшифрован (I1).</span>' : '';
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'shop-item';
      btn.dataset.slot = item.slot;
      btn.innerHTML = `<span class="shop-icon">${item.icon}</span><span class="shop-body">
        <span class="shop-slot">${item.slotName} · замена</span><span class="shop-name">${item.name}</span>
        <span class="item-state item-before"><span class="item-state-title">Заменяет: ${equipped.name}</span><span class="item-mods">${itemModsText(equipped)}</span></span>
        <span class="item-state item-after"><span class="item-state-title">После замены:</span><span class="item-mods">${itemModsText(item)}</span></span>
        <span class="shop-preview">Разница в этом слоте:</span>${changes}${scroll}</span>`;
      btn.addEventListener('click', () => { if (S.menuCandidates === options) purchaseShopItem(index); });
      grid.appendChild(btn);
    });
    // Three equipment choices; the fourth is always inert, never a stat scroll.
    const empty = document.createElement('div');
    empty.className = 'shop-item shop-empty';
    empty.setAttribute('aria-hidden', 'true');
    grid.appendChild(empty);
    appendEquipmentChoices(body, grid);
  }

  function renderUpgradeChoices(body) {
    const options = drawItemSlots(4, 4, makeUpgradeOffer);
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
      btn.innerHTML = `<span class="lv-chip" style="color:#d9a8ff">${opt.icon}</span>
        <span class="lv-body">
          <span class="lv-name">${opt.slotName} · ${item.name}</span>
          <span class="item-state item-before"><span class="item-state-title">Сейчас на предмете:</span><span class="item-mods">${itemModsText(item)}</span></span>
          <span class="item-state item-after"><span class="item-state-title">После улучшения:</span><span class="item-mods">${itemModsText(upgraded)}</span></span>
          <span class="lv-effs"><span class="lv-line item-change better">${opt.name}: ${opt.comparison} (+${itemNumber(opt.key, opt.value)})</span><span class="lv-line">${opt.desc}</span></span>
        </span>`;
      btn.addEventListener('click', () => {
        if (S.pendingModal !== 'armor' || S.menuCandidates !== options) return;
        applyUpgradeOffer(opt);
        closeModal(); renderBoard(); renderHUD(); audioLevel();
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
    S.over = true;
    S.resolving = false;
    S.actionCommitted = false;
    S.arrivals = [];
    S.rewardMenus = [];
    AFTER_ARRIVALS = null;
    audioGameOver();
    const runId = RUN_ID;
    setTimeout(() => { if (runId === RUN_ID) openModal('gameover'); }, 600);
  }

  function flashOn(idx, text) {
    const cellEl = qs(`.cell[data-idx="${idx}"]`);
    if (!cellEl) return;
    const node = document.createElement('div');
    node.className = 'float-fx';
    node.textContent = text;
    cellEl.appendChild(node);
    setTimeout(() => node.remove(), 900);
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
        S.hero.hp = Math.max(1, Math.floor(S.hero.hp / 2));
        flash('💠 КА-МИ-КАЗЕ: пол-HP → ' + S.hero.hp);
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
            wc.skull.hp = wc.skull.maxHp;
          });
          flash('💚 Исцеление: черепы исцелены');
        } else {
          const all = [];
          for (let j = 0; j < CELL_COUNT; j++) {
            const ac = S.board[j];
            if (ac && ac.type === 'skull' && ac.skull.uid !== s.uid) all.push(j);
          }
          shuffle(all).slice(0, 2).forEach(ai => { S.board[ai].skull.atk += 1; });
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
          if (hpLoss > 0) S.hero.hp = Math.max(0, S.hero.hp - hpLoss);
          log('🏴‍☠️ Вор украл ' + stolen + ' монет' + (hpLoss > 0 ? ': −' + hpLoss + ' HP' : ''));
        } else {
          const stolen = Math.min(2, S.hero.gold);
          S.hero.gold -= stolen;
          if (stolen > 0) {
            const hpLoss = clsRace() === 'Sauren' ? stolen * enemyGetAttack(s) * 2 : 0;
            if (hpLoss > 0) S.hero.hp = Math.max(0, S.hero.hp - hpLoss);
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
          S.hero.hp = Math.max(0, S.hero.hp - hp);
          floatOn('💥 -' + hp, 'hurt');
          flash('🪓 Брутал: −' + hp + ' HP');
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
  let activeDrag = false;
  function onPointerMove(e) {
    if (!activeDrag) return;
    const el = document.elementFromPoint(e.clientX, e.clientY);
    const cell = el ? el.closest('.cell') : null;
    if (!cell) return;
    const idx = parseInt(cell.dataset.idx, 10);
    if (isNaN(idx)) return;
    tryExtendChain(idx);
  }

  function onPointerEnd() {
    if (!activeDrag) return;
    activeDrag = false;
    window.removeEventListener('pointermove', onPointerMove);
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
      if (S.over || S.resolving || S.pendingModal) return;
      e.preventDefault();
      const cell = e.target.closest('.cell');
      if (!cell) return;
      const idx = parseInt(cell.dataset.idx, 10);
      if (isNaN(idx)) return;
      activeDrag = true;
      window.addEventListener('pointermove', onPointerMove);
      window.addEventListener('pointerup', onPointerEnd, { once: true });
      window.addEventListener('pointercancel', onPointerEnd, { once: true });
      tryExtendChain(idx);
    });
  }

  function flashOnInvalid() {}

  // --- class picker (start screen) ----------------------------------------
  function renderClassPicker() {
    const grid = el('picker-grid');
    if (!grid) return;
    grid.innerHTML = '';
    CLASSES.forEach(cls => {
      const unlocked = isClassUnlocked(cls.id);
      const lvl = classLevelOf(cls.id);
      const selected = getSelectedClassId() === cls.id;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'picker-card-btn' + (unlocked ? '' : ' locked') + (selected ? ' selected' : '');
      const desc = [];
      if (cls.perk !== '—') desc.push(`<span class="pick-perk">⬆ ${cls.perkName || cls.perk}: ${cls.perkDesc}</span>`);
      if (cls.flaw !== '—') desc.push(`<span class="pick-flaw">⬇ ${cls.flawName || cls.flaw}: ${cls.flawDesc}</span>`);
      if (cls.raceDesc) desc.push(`<span class="pick-race-desc">🧬 ${cls.raceName || cls.race}: ${cls.raceDesc}</span>`);
      const uniqueInfo = `<span class="pick-unique">Уникальный навык: ${cls.uniqueSkillDesc}</span>`;
      const status = unlocked
        ? `<span class="pick-lvl">ур. ${lvl || 1}${selected ? ' · выбран' : ''}</span>`
        : `<span class="pick-locked">🔒 Подбери трофей на поле</span>`;
      btn.innerHTML = `<span class="pick-head"><span class="pick-icon">${cls.icon}</span><span><span class="pick-name">${cls.name}</span><br><span class="pick-race">${cls.raceName || cls.race}</span></span></span>${desc.join('')}${uniqueInfo}${status}`;
      btn.addEventListener('click', () => {
        if (!unlocked) {
          flash('⚠ «' + cls.name + '» заблокирован — подбери трофей на поле после победы над особыми');
          return;
        }
        setSelectedClassId(cls.id);
        hideClassPickerAndStart();
      });
      grid.appendChild(btn);
    });
  }

  function showClassPicker() {
    const picker = el('class-picker');
    if (!picker) { newGame(); return; }   // test harness / environments without the picker
    renderClassPicker();
    picker.classList.add('show');
  }

  function hideClassPickerAndStart() {
    const picker = el('class-picker');
    if (picker) picker.classList.remove('show');
    newGame();
  }

  // --- init -----------------------------------------------------------------
  function init() {
    initInput();
    const restartBtn = qs('#restart');
    if (restartBtn) restartBtn.addEventListener('click', () => newGame());
    const menuBtn = qs('#menu-btn');
    const menuModal = qs('#menu-modal');
    if (menuBtn && menuModal) {
      menuBtn.addEventListener('click', () => menuModal.classList.add('show'));
      const hide = () => menuModal.classList.remove('show');
      const resume = qs('#menu-resume'); if (resume) resume.addEventListener('click', hide);
      const close = qs('#menu-close'); if (close) close.addEventListener('click', hide);
      const restart = qs('#menu-restart'); if (restart) restart.addEventListener('click', () => { hide(); newGame(); });
      const mhelp = qs('#menu-help'); if (mhelp) mhelp.addEventListener('click', () => {
        hide();
        const wrap = qs('#help-modal');
        if (wrap) wrap.classList.add('show');
      });
    }
    const statsBtn = qs('#stats-btn');
    if (statsBtn) statsBtn.addEventListener('click', () => {
      if (!S.resolving && !S.pendingModal && !S.over) openModal('stats');
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
    tryExtendChain,
    resolveChain,
    activateSkill,
    setSelectedClassId,
    getSelectedClassId,
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
