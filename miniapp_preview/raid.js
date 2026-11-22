// miniapp_preview/raid.js
// Пробник Рейда на классических механиках (Fireflame Games, Dungeon Raid).
// Свой код, своя графика, своя стилистика. Запускать через lunapark-raid.html.
'use strict';
(function () {
  // ============================================================
  // КЛАССИЧЕСКИЕ ПАРАМЕТРЫ: Dungeon Raid, сложность «Нормaл»
  //
  // Официальные точные числа черепов/боссов не опубликованы — ниже
  // механика классики + честная калибровка клона (см. LUNAPARK.md §7).
  // ============================================================
  const COLS = 6, ROWS = 6, CELL_COUNT = COLS * ROWS;
  const START_SKULLS = [3, 4];
  // Original start: base max health 40 (InitNewGame) + accessory 10 = 50;
  // armour = helmet 1 + armour 2 + shield 1 = 4; weapon damage 2.
  const START_HP = 50, START_MAX_ARMOR = 4;

  // --- формулы урона (классика) ---
  // Урон цепочки со мечом: (база + сила + рост «Урона» + наследник класса)
  //                         + оружие × (число мечей в цепочке).
  // Пробитие брони (pierce) — вероятность, что атака игнорирует оборону черепа.
  // DPS по черепу = суммарный урон; применяется сначала к обороны, потом к HP.
  // (не путать с бронёй героя, где работает «долговечность»)
  const BASE_DAMAGE = 3;           // базовый урон без мечей (по исходной спецификации)
  const HP_PER_SHIELD_ARMOR = 1;   // базовая броня за щит (+ точность + 2 × защита)
  const SHIELD_UPGRADE_POINTS = 8; // очки upgrade bar за каждый щит (калибровка клона)
  const HEAL_PER_POTION = 1;       // базовая лечение за зелье (+ удача)
  const GOLD_PER_COIN = 2;         // базовая золото за монетку (+ boost gold)
  const XP_PER_SKULL = 2;          // опыт за убитого черепа (+ boost XP)
  const XP_PER_BOSS = 6;           // опыт за убитого босса

  // Bonus resource (классика): шанс каждого «лишнего» типа сверх первых трёх
  // в цепочке; база 20% + 5% за соответствующий стат + 5% за харизматику
  // + классные бонусы (Raider: 100% на 10+ клеток).
  const BONUS_BASE_CHANCE = 0.20;
  // Confused flaw / Unexpected perk window (turnData.earlyTurns).
  const EARLY_TURNS = 5;
  const BASE_PIERCING = 0.50;      // вероятность пробития брони черепа (классика)
  const BASE_DURABILITY = 0.50;    // вероятность, что пункт брони героя не списывается
  const MAX_DURABILITY = 0.90;     // потолок долговечности с апгрейдами

  const SPAWN_EVERY_TURNS = 3;
  const BOSS_EVERY_TURNS = 10;
  // Классика: трофей за ~15 убитых спец-черепов; не более 3 классов unlock за забег
  const TROPHY_KILLS_PER_TROPHY = 25;   // fallback when trophyRemaining is unset (20..30 roll)
  const MAX_UNLOCKS_PER_RUN = 3;
  const SPECIAL_EVERY_TURNS = 4;
  const UPGRADE_BAR_MAX = 100;    // полоса UP (от щитов) → выбор 1-из-4
  const GOLD_BOX_MAX = 100;       // золотой ящик → 1-из-4 снаряжение

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
    17: { atk: 2,   def: 2,  hp: 4 },    // Doppelganger (def = текущая броня героя)
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
    2:  { name: 'Острый',      feats: ['Отражает одну величину D за выбранного Острый', 'Статы: ×2 ⚔ / броня / ×3 ❤️', 'Отражение напрямую отнимает HP, минуя броню'] },
    3:  { name: 'Золотой',     feats: ['Убит до конца таймера — +40 золота', 'Не убит к концу — исчезает без награды', 'Статы: 0 ⚔ / ×6 🛡 / ×6 ❤️ · таймер 5'] },
    4:  { name: 'Исцеление',   feats: ['Лечит до 2 раненых черепов до макс. HP', 'Без раненых — даст до 2 черепам +1 к атаке', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    5:  { name: 'Невидимый',   feats: ['Повышенная броня и половина обычного HP', 'Статы: ×2 ⚔ / ×10 🛡 / ½ ❤️', 'Без отдельной способности'] },
    6:  { name: 'Кислотный',   feats: ['Каждый ход превращает одну обычную зелье в кислотное', 'Кислое зелье — ранит героя, а не лечит', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    7:  { name: 'Огненный',    feats: ['Каждый ход поджигает до 8 соседних клеток (не других Огненных)', 'Обгоревший череп получает урон этим врагом', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    8:  { name: 'Застывший',   feats: ['Каждый ход замораживает до 2 клеток', 'Замороженные не совпадают; черепа не атакуют', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    9:  { name: 'Отравленный', feats: ['Его проникшая атака отравляет героя', 'Яд снимается зельем, пока Poisonous не на поле', 'Статы: ⚔ / ×2 🛡 / ×4 ❤️'] },
    10: { name: 'Ка-Ми-Казе',  feats: ['Таймер 5 → 0 — взрыв: пол-HP героя', 'При убийстве — тоже взрыв в соседних не-черепах', 'Статы: ⚔ / ×2 🛡 / ×6 ❤️ · таймер 5'] },
    11: { name: 'Наблюдатель', feats: ['Каждый ход превращает один меч в череп', 'Новый череп — обычный, уязвим для целей', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    12: { name: 'Коррозия',    feats: ['Пока на поле — броня не восстанавливается', 'Своё появление: убивает всю броню героя', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    13: { name: 'Вор',         feats: ['Крадёт до 2 монетных клеток без награды', 'Если их нет — до 2 золота; украденные очки наносят HP-урон', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    14: { name: 'Хаотический', feats: ['Каждый ход меняется местом со случайной клеткой', 'Целевая клетка (вкл. обычный череп) — поглощается', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    15: { name: 'Маг',         feats: ['Пока на поле — запрет на естественное восстановление кулдаунов', 'Кулдауны навыков не растут', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    16: { name: 'Ассасин',      feats: ['В нижнем ряду — 2× ⚔ дополнительного урона герою', 'Сама атакует — в сумме 3× ⚔', 'Статы: ×3 ⚔ / ×2 🛡 / ×3 ❤️'] },
    17: { name: 'Доппелгангер', feats: ['Каждую игру копирует текущую броню героя', 'Лучше атаковать на 0 брони', 'Статы: ×2 ⚔ / 🛡 = броня / ×4 ❤️'] },
    18: { name: 'Алхимик',      feats: ['Каждый ход меняет до 2 случайных клеток', 'Результат может быть тем же типом', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    19: { name: 'Нестабильный', feats: ['Таймер 5 → 0 — телепорт: все клетки меняются', 'Новые клетки — без черепов', 'Статы: ×4 ⚔ / броня / ×5 ❤️ · таймер 5'] },
    20: { name: 'Вампир',       feats: ['Восстанавливает атаку при входящем HP-уроне', 'При прямом ударе лечится сверх старого максимума', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    21: { name: 'Мстительный',  feats: ['За убийство героем +1 к атаке', '+1 атака за каждое убийство в ходе', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    22: { name: 'Трамплящий',   feats: ['Каждый ход ломает один случайный щит или меч', 'Сломанное не приносит ничего', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    23: { name: 'Скользкий',    feats: ['В каждом столбце удаляет клетку под первым подходящим черепом', 'Удаление не даёт награды', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    24: { name: 'Альфа',        feats: ['Каждый ход меняет внешнего черепа с соседней не-черепной клеткой', 'Соседние клетки — только обычные (не черепа)', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    25: { name: 'Магнитный',     feats: ['Каждый ход меняет внешние мечи/щиты с соседними другими клетками', 'Целевая клетка может быть спец-черепом', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    26: { name: 'Брутал',        feats: ['Каждый ход выбрасывает старый обычный череп в героя', 'Урон героя = HP этого черепа', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    27: { name: 'Бронебойный',   feats: ['Удаётся только путь через 3 клетки выше', 'Неуязвим в верхнем ряду', 'Статы: ×2 ⚔ / ×4 🛡 / ×3 ❤️'] },
    28: { name: 'Съедобный',     feats: ['Таймер 5 → 0 — обнуляет одно улучшение героя', 'Таймер сбрасывается на 5', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️ · таймер 5'] },
    29: { name: 'Загрязнитель',  feats: ['Пока на поле — каждый блок брони снимает 1 броню', 'Без атак, но постоянно вредит', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    30: { name: 'Желе-Щит',      feats: ['Перехватывает урон цепочки от других черепов', 'Сам поглощает урон и уязвим', 'Статы: ½ ⚔ / броня / ×5 ❤️'] },
    31: { name: 'Бегун',        feats: ['Каждый ход уходит на одну клетку выше', 'На верхнем краю — исчезает без трофея', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    32: { name: 'Лич',          feats: ['Смерть создаёт филактерий; его сбор даёт +25 золота', 'Если филактерий не собрать, Лич возродится', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
  };

  const SKILLS = [
    // 42 native skills. 0..30 are ordinary level-up skills, 31..39 are class
    // skills, 40/41 are native-only (Shock/Awe) and can appear only at class 10.
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
        `Шанс бонуса опыта: ${bonusPct('xp')}% >> ${bonusPct('xp') + 5}%`,
      ],
      apply: () => S.hero.stats.strength++
    },
    {
      id: 'dexterity', name: 'Точность', icon: '🎯', chip: '+ТОЧ', color: '#b8a8ff',
      desc: '+1 броня за щит, +5% шанс бонуса щита',
      effects: () => [
        `Броня за щит: ${fmt(perShield())} >> ${fmt(perShield() + 1)}`,
        `Шанс бонуса щита: ${bonusPct('shield')}% >> ${bonusPct('shield') + 5}%`,
      ],
      apply: () => S.hero.stats.dexterity++
    },
    {
      id: 'vitality', name: 'Живучесть', icon: '❤', chip: '+ЖИВ', color: '#ff9d9d',
      desc: '+5 макс. HP, +5% шанс бонуса зелий',
      effects: () => [
        `Макс. HP: ${fmt(S.hero.maxHp)} >> ${fmt(S.hero.maxHp + 5)}`,
        `Шанс бонуса зелия: ${bonusPct('potion')}% >> ${bonusPct('potion') + 5}%`,
      ],
      apply: () => { S.hero.stats.vitality++; if (S.mode !== 'pretzel') { S.hero.hp += 5; S.hero.maxHp += 5; } }
    },
    {
      id: 'luck', name: 'Удача', icon: '🍀', chip: '+УДА', color: '#ffd94a',
      desc: '+1 лечение за зелье, +5% шанс бонусных монет',
      effects: () => [
        `Лечение за зелье: ${fmt(HEAL_PER_POTION + S.hero.stats.luck)} >> ${fmt(HEAL_PER_POTION + S.hero.stats.luck + 1)}`,
        `Шанс бонусной монеты: ${bonusPct('coin')}% >> ${bonusPct('coin') + 5}%`,
      ],
      apply: () => S.hero.stats.luck++
    },
    {
      // Native stat 7: +5 to every bonus-token chance at once.
      id: 'charisma', name: 'Все бонусы', icon: '💎', chip: '+БОН', color: '#8fd0ff',
      desc: '+5% ко всем бонусам',
      effects: () => [
        `Монеты/зелья/опыт/щиты: ${bonusPct('coin')}% >> ${bonusPct('coin') + 5}%`,
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
  // 18 (из 19) классических апгрейдов — без «Яда» (в клоне нет механики яда).
  const UPGRADES = [
    { id: 'pierce', name: 'Пронзание', icon: '🛡', desc: '+10% пробитию брони черепов', apply: () => { S.pierce = Math.min(1, S.pierce + 0.10); } },
    { id: 'armor', name: 'Бронированное кольцо', icon: '🛡', desc: '+3 к макс. броне', apply: () => { S.hero.maxArmor += 3; S.hero.armor += 3; } },
    { id: 'base', name: 'Ускорение', icon: '⚡', desc: '+1 базового урона', apply: () => { S.baseDamageBonus = (S.baseDamageBonus || 0) + 1; } },
    { id: 'blunt', name: 'Сбивание', icon: '🚫', desc: '-1 к урону черепов', apply: () => { S.blunt += 1; } },
    { id: 'dex_up', name: 'Буст точности', icon: '🎯', desc: '+1 точность', apply: () => { S.hero.stats.dexterity++; } },
    { id: 'gold_up', name: 'Буст золота', icon: '🪙', desc: '+0.25 к золоту за монету', apply: () => { S.goldPerCoin += 0.25; } },
    { id: 'luck_up', name: 'Буст удачи', icon: '🍀', desc: '+1 удача', apply: () => { S.hero.stats.luck++; } },
    { id: 'str_up', name: 'Буст силы', icon: '💪', desc: '+1 сила', apply: () => { S.hero.stats.strength++; } },
    { id: 'up_up', name: 'Буст апгрейдов', icon: '⬆', desc: '+1 очко к upgrade bar за щит', apply: () => { S.upgradePerShield += 1; } },
    { id: 'vit_up', name: 'Буст живучести', icon: '❤', desc: '+1 живучесть (+5 HP)', apply: () => { S.hero.stats.vitality++; S.hero.hp += 5; S.hero.maxHp += 5; } },
    { id: 'xp_up', name: 'Буст опыта', icon: '⭐', desc: '+0.25 к опыту за черепа', apply: () => { S.xpPerSkull += 0.25; } },
    { id: 'durability', name: 'Долговечность', icon: '🛡', desc: '+10% стойкости брони (≤90%)', apply: () => { S.durability = Math.min(MAX_DURABILITY, S.durability + 0.10); } },
    { id: 'leech', name: 'Сок', icon: '🩸', desc: '+5% лечения за вред', apply: () => { S.leech += 0.05; } },
    { id: 'quicken', name: 'Ускорение кулдаунов', icon: '⏱', desc: '−1 ко всем кулдаунам навыков (постоянно)', apply: () => { S.quicken += 1; } },
    { id: 'regen', name: 'Регенерация', icon: '♻', desc: '+1 HP в конце хода', apply: () => { S.regen += 1; } },
    { id: 'repair', name: 'Ремонт брони', icon: '⚒', desc: '+1 броня за щит', apply: () => { S.armorPerShield += 1; } },
    { id: 'spikes', name: 'Шипы', icon: '🗡', desc: '+1 урон черепу за каждый бросок', apply: () => { S.spikes += 1; } },
    { id: 'weapon', name: 'Оружие', icon: '⚔', desc: '+1 урон оружия', apply: () => { S.hero.weaponDamage += 1; } },
  ];
  function upgradePreview(opt) {
    const h = S.hero, st = h.stats;
    const pct = n => Math.round(n * 100) + '%';
    switch (opt.id) {
      case 'pierce': return `Пробитие черепов: ${pct(S.pierce)} → ${pct(Math.min(1, S.pierce + 0.10))}`;
      case 'armor': return `Макс. броня: ${fmt(h.maxArmor)} → ${fmt(h.maxArmor + 3)}; сейчас: ${fmt(h.armor)} → ${fmt(h.armor + 3)}`;
      case 'base': return `Базовый урон: ${fmt(baseDamage())} → ${fmt(baseDamage() + 1)}`;
      case 'blunt': return `Урон каждого черепа: −${fmt(S.blunt)} → −${fmt(S.blunt + 1)}`;
      case 'dex_up': return `Точность: ${fmt(st.dexterity)} → ${fmt(st.dexterity + 1)}; броня за щит +1`;
      case 'gold_up': return `Золото за монету: ${S.goldPerCoin.toFixed(2)} → ${(S.goldPerCoin + 0.25).toFixed(2)}`;
      case 'luck_up': return `Удача: ${fmt(st.luck)} → ${fmt(st.luck + 1)}; лечение зелий +1`;
      case 'str_up': return `Сила: ${fmt(st.strength)} → ${fmt(st.strength + 1)}; базовый урон +1`;
      case 'up_up': return `Очки апгрейда за щит: ${fmt(S.upgradePerShield)} → ${fmt(S.upgradePerShield + 1)}`;
      case 'vit_up': return `Живучесть: ${fmt(st.vitality)} → ${fmt(st.vitality + 1)}; макс. HP: ${fmt(h.maxHp)} → ${fmt(h.maxHp + 5)}`;
      case 'xp_up': return `Опыт за обычного черепа: ${S.xpPerSkull.toFixed(2)} → ${(S.xpPerSkull + 0.25).toFixed(2)}`;
      case 'durability': return `Стойкость брони: ${pct(S.durability)} → ${pct(Math.min(MAX_DURABILITY, S.durability + 0.10))}`;
      case 'leech': return `Возврат урона лечением: ${pct(S.leech)} → ${pct(S.leech + 0.05)}`;
      case 'quicken': return `Сокращение кулдаунов: ${fmt(S.quicken)} → ${fmt(S.quicken + 1)}`;
      case 'regen': return `Лечение в конце хода: ${fmt(S.regen)} → ${fmt(S.regen + 1)} HP`;
      case 'repair': return `Броня за щит: ${fmt(perShield())} → ${fmt(perShield() + 1)}`;
      case 'spikes': return `Ответный урон черепам: ${fmt(S.spikes)} → ${fmt(S.spikes + 1)}`;
      case 'weapon': return `Урон каждого меча: ${fmt(h.weaponDamage)} → ${fmt(h.weaponDamage + 1)}`;
      default: return opt.desc || '';
    }
  }

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
      ['Броня за щит', `${fmt(perShield())}; +${fmt(S.upgradePerShield)} очков апгрейда`],
      ['Лечение за зелье', `${fmt(HEAL_PER_POTION + st.luck)} HP`],
      ['Золото за монету / опыт за черепа', `${S.goldPerCoin.toFixed(2)} / ${S.xpPerSkull.toFixed(2)}`],
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

  // Магазин/апгрейды теперь целиком на слот-предметах (см. ITEM_SLOTS ниже).
  // --- 5 equipment slots (original: weapon/helmet/armour/shield/accessory) --
  const ITEM_SLOTS = [
    { id: 'weapon', name: 'Оружие', icon: '⚔', pool: ['weaponDamage', 'pierce', 'leech', 'spikes', 'baseDamage'] },
    { id: 'helmet', name: 'Шлем', icon: '🪖', pool: ['maxArmor', 'durability', 'baseDamage', 'pierce'] },
    { id: 'armour', name: 'Доспех', icon: '🥋', pool: ['maxArmor', 'durability', 'maxHp', 'blunt'] },
    { id: 'shield', name: 'Щит', icon: '🛡', pool: ['armorPerShield', 'upgradePerShield', 'maxArmor', 'durability', 'spikes'] },
    { id: 'accessory', name: 'Аксессуар', icon: '💎', pool: ['maxHp', 'regen', 'goldPerCoin', 'xpPerSkull', 'leech', 'pierce'] },
  ];
  const ITEM_STAT = {
    weaponDamage: { name: 'Урон оружия', icon: '⚔', step: 1, kind: 'hero' },
    maxArmor: { name: 'Макс. броня', icon: '🛡', step: 2, kind: 'heroArmor' },
    maxHp: { name: 'Макс. HP', icon: '❤', step: 8, kind: 'heroHp' },
    pierce: { name: 'Пробитие брони', icon: '🎯', step: 0.05, kind: 'pierce' },
    leech: { name: 'Сок (лечение)', icon: '🩸', step: 0.05, kind: 'leech' },
    durability: { name: 'Стойкость брони', icon: '🪨', step: 0.05, kind: 'durability' },
    spikes: { name: 'Шипы', icon: '🗡', step: 1, kind: 'spikes' },
    baseDamage: { name: 'Базовый урон', icon: '💥', step: 1, kind: 'base' },
    armorPerShield: { name: 'Броня за щит', icon: '🔰', step: 1, kind: 'armorPerShield' },
    upgradePerShield: { name: 'Очки UP за щит', icon: '⬆', step: 1, kind: 'upgradePerShield' },
    regen: { name: 'Регенерация', icon: '♻', step: 1, kind: 'regen' },
    goldPerCoin: { name: 'Золото за монету', icon: '🪙', step: 0.1, kind: 'goldPerCoin' },
    xpPerSkull: { name: 'Опыт за череп', icon: '⭐', step: 0.1, kind: 'xpPerSkull' },
    blunt: { name: 'Сбивание атаки', icon: '🚫', step: 1, kind: 'blunt' },
  };
  const ITEM_NAMES = {
    weapon: ['Ржавый клинок', 'Кривая сабля', 'Тяжёлая булава', 'Колючий молот', 'Топор палача', 'Клинок стражи'],
    helmet: ['Мятый шлем', 'Кожаный капюшон', 'Рогатый венец', 'Шлем рыцаря', 'Каска рудокопа'],
    armour: ['Стёганый жилет', 'Кольчуга', 'Чешуйчатый панцирь', 'Кираса стража', 'Мантия мага'],
    shield: ['Щепка', 'Круглый щит', 'Башенный щит', 'Щит-башня', 'Рунический барьер'],
    accessory: ['Медное кольцо', 'Амулет удачи', 'Кулон охотника', 'Печать жреца', 'Талисман героя'],
  };
  const ITEM_PREFIXES = ['Старый', 'Крепкий', 'Блестящий', 'Проклятый', 'Гномий', 'Эльфийский', 'Кровавый', 'Лунный', 'Кованый'];

  function itemSlotById(id) { return ITEM_SLOTS.find(s => s.id === id); }
  function itemModText(key, value) {
    const def = ITEM_STAT[key];
    if (!def) return '';
    const v = Math.abs(value);
    return `${def.icon} ${def.name} +${def.kind === 'pierce' || def.kind === 'leech' || def.kind === 'durability' || def.kind === 'goldPerCoin' || def.kind === 'xpPerSkull' ? Math.round(v * 100) + '%' : fmt(v)}`;
  }
  function applyItemMod(key, value, sign) {
    const d = ITEM_STAT[key];
    if (!d) return;
    const v = value * sign;
    switch (d.kind) {
      case 'hero': S.hero.weaponDamage += v; break;
      case 'heroArmor':
        S.hero.maxArmor = Math.max(0, S.hero.maxArmor + v);
        S.hero.armor = Math.max(0, Math.min(S.hero.maxArmor, S.hero.armor + v));
        break;
      case 'heroHp':
        if (S.mode === 'pretzel') break;
        S.hero.maxHp = Math.max(1, S.hero.maxHp + v);
        S.hero.hp = Math.max(1, Math.min(S.hero.maxHp, S.hero.hp + v));
        break;
      case 'pierce': S.pierce = Math.max(0, Math.min(1, S.pierce + v)); break;
      case 'leech': S.leech = Math.max(0, S.leech + v); break;
      case 'durability': S.durability = Math.max(0, Math.min(MAX_DURABILITY, S.durability + v)); break;
      case 'spikes': S.spikes = Math.max(0, S.spikes + v); break;
      case 'base': S.baseDamageBonus = Math.max(0, (S.baseDamageBonus || 0) + v); break;
      case 'armorPerShield': S.armorPerShield = Math.max(1, S.armorPerShield + v); break;
      case 'upgradePerShield': S.upgradePerShield = Math.max(1, S.upgradePerShield + v); break;
      case 'regen': S.regen = Math.max(0, S.regen + v); break;
      case 'blunt': S.blunt = Math.max(0, S.blunt + v); break;
      case 'goldPerCoin': S.goldPerCoin = Math.max(0.25, S.goldPerCoin + v); break;
      case 'xpPerSkull': S.xpPerSkull = Math.max(0.25, S.xpPerSkull + v); break;
      default: break;
    }
  }
  function applyItem(item, sign) {
    if (!item || !item.mods) return;
    Object.keys(item.mods).forEach(k => applyItemMod(k, item.mods[k], sign));
  }
  function makeItem(slotId) {
    const slot = itemSlotById(slotId) || ITEM_SLOTS[0];
    const keys = shuffle([...slot.pool]).slice(0, randInt(2, 4, 5));
    const mods = {};
    keys.forEach(k => {
      const def = ITEM_STAT[k];
      const steps = randInt(1, 2, 5);
      mods[k] = +(def.step * steps).toFixed(2);
    });
    const base = ITEM_NAMES[slot.id] || ITEM_NAMES.weapon;
    const name = ITEM_PREFIXES[randInt(0, ITEM_PREFIXES.length - 1, 5)] + ' ' + base[randInt(0, base.length - 1, 5)].toLowerCase();
    return { slot: slot.id, slotName: slot.name, icon: slot.icon, name, mods };
  }
  function itemModsText(item) {
    if (!item || !item.mods) return 'пусто';
    const parts = Object.keys(item.mods).map(k => itemModText(k, item.mods[k]));
    return parts.length ? parts.join(' · ') : 'без бонусов';
  }
  function equipItem(item) {
    const index = S.equipment.findIndex(e => e.slot === item.slot);
    if (index < 0) return;
    const old = S.equipment[index];
    applyItem(old, -1);
    applyItem(item, 1);
    S.equipment[index] = item;
  }
  function makeUpgradeOffer(slotId) {
    const slot = itemSlotById(slotId);
    const key = slot.pool[randInt(0, slot.pool.length - 1, 5)];
    const def = ITEM_STAT[key];
    return { slot: slot.id, slotName: slot.name, icon: slot.icon, key, value: def.step, name: def.name, label: itemModText(key, def.step) };
  }

  // ============================================================
  // КЛАССЫ И РАСЫ (классика: 10 классов, каждый — Perk/Flaw/Race/Уникальный навык)
  // 0 = Аduventurer (стартовый). Остальные — выбираемы в пикeрe (владелец:
  // «полная классика» — классы сразу доступны для выбора, как в классике
  // после 100+ забегов; trophy-механика — нет, см. LUNAPARK.md §7).
  // ============================================================
  const CLASSES = [
    { id: 'adventurer', name: 'Авантюрист', icon: '❔', race: 'Human', uniqueSkill: 'teleport', pool: ['steal', 'freeze', 'heal', 'repair', 'counter', 'shatter', 'goldtouch', 'teleport', 'explosive', 'elixir', 'enchant', 'slash', 'treasure', 'scavenge', 'disarm', 'dazzle', 'boostdmg', 'boostgold', 'boosthealth', 'boostarmour'],
      perk: '—', flaw: '—', raceName: 'Человек',
      perkDesc: 'нет пассивов', flawDesc: 'нет изъянов',
      clsDesc: 'Стартовый класс: нет пассива или изъяна, свой навык — Телепорт',
      uniqueSkillDesc: 'Телепорт (20): все клетки без черепов → новые' },
    { id: 'barbarian', name: 'Варвар', icon: '🪓', race: 'Orc', uniqueSkill: 'berserk', pool: ['berserk', 'repair', 'counter', 'shatter', 'teleport', 'elixir', 'enchant', 'slash', 'treasure', 'scavenge', 'disarm', 'boostdmg', 'boosthealth', 'boostarmour', 'banish', 'exorcise', 'trap', 'biggame', 'magicsword', 'explosivearmour'],
      perk: 'Strong', flaw: 'Reckless', perkName: 'Сила', flawName: 'Безрассудство', raceName: 'Орк',
      perkDesc: '+1 базового урона', flawDesc: '-10% шанс бонусных щитов',
      clsDesc: 'пассив: Сила · изъян: Безрассудство · раса: Орк (оглушение 10%)',
      uniqueSkillDesc: 'Берсерк (14): убийство черепа даёт HP равный его атаке' },
    { id: 'mage', name: 'Маг', icon: '🔮', race: 'Changeling', uniqueSkill: 'rune', pool: ['rune', 'freeze', 'heal', 'repair', 'goldtouch', 'teleport', 'explosive', 'elixir', 'enchant', 'treasure', 'dazzle', 'boostgold', 'boosthealth', 'banish', 'exorcise', 'chamber', 'magicsword', 'earthquake', 'fireball', 'mana'],
      perk: 'Gifted', flaw: 'Frail', perkName: 'Одарённость', flawName: 'Хрупкость', raceName: 'Изменник',
      perkDesc: '5% зелий выпадают как mana-зелья (−1 к кулдауну навыка)', flawDesc: '-10 макс. HP',
      clsDesc: 'пассив: Одарённость · изъян: Хрупкость · раса: Изменник (менятся)',
      uniqueSkillDesc: 'Руна (12): замкнутая цепочка собирает все клетки внутри' },
    { id: 'ranger', name: 'Разведчик', icon: '🏹', race: 'Elf', uniqueSkill: 'volley', pool: ['volley', 'heal', 'repair', 'counter', 'shatter', 'teleport', 'explosive', 'elixir', 'enchant', 'treasure', 'scavenge', 'disarm', 'boostdmg', 'boosthealth', 'banish', 'trap', 'biggame', 'chamber', 'magicsword', 'mana'],
      perk: 'Agile', flaw: 'Slim', perkName: 'Ловкость', flawName: 'Тощий', raceName: 'Эльф',
      perkDesc: '5% мечей выпадают стрелками (стрелять на 1 клетку)', flawDesc: '-1 броня',
      clsDesc: 'пассив: Ловкость · изъян: Тощий · раса: Эльф (медленный яд)',
      uniqueSkillDesc: 'Воллей (15): все мечи → стрелы (стрелы стреляют на 1 клетку)' },
    { id: 'rogue', name: 'Вор', icon: '🗡', race: 'Sauren', uniqueSkill: 'greed', pool: ['greed', 'steal', 'heal', 'repair', 'counter', 'goldtouch', 'explosive', 'elixir', 'enchant', 'slash', 'treasure', 'scavenge', 'disarm', 'dazzle', 'boostgold', 'boosthealth', 'trap', 'biggame', 'chamber', 'magicsword'],
      perk: 'Frugal', flaw: 'Clumsy', perkName: 'Бережливость', flawName: 'Неловкость', raceName: 'Саурен',
      perkDesc: '+10% шанс бонусных монет', flawDesc: '3% щитов выпадают сломанными (дают 0)',
      clsDesc: 'пассив: Бережливость · изъян: Неловкость · раса: Саурен (поиск — нет магазина)',
      uniqueSkillDesc: 'Жадность (25): убивает всех черепов рядом с монеткой' },
    { id: 'paladin', name: 'Паладин', icon: '🛡', race: 'Dragonkin', uniqueSkill: 'laststand', pool: ['laststand', 'heal', 'repair', 'counter', 'shatter', 'explosive', 'elixir', 'slash', 'treasure', 'scavenge', 'disarm', 'boostdmg', 'boosthealth', 'boostarmour', 'banish', 'exorcise', 'biggame', 'chamber', 'magicsword', 'explosivearmour'],
      perk: 'Intimidating', flaw: 'Zealous', perkName: 'Устрашающий', flawName: 'Рьяный', raceName: 'Драконкин',
      perkDesc: 'смертный враг появляется на 50% реже (нет смертных врагов в клоне)', flawDesc: 'черепа-монстры появляются на 1 ход раньше',
      clsDesc: 'пассив: Устрашающий (не реализовано) · изъян: Рьяный · раса: Драконкин (огненное дыхание)',
      uniqueSkillDesc: 'Последний рубеж (35): полное лечение HP, если герой погибнет в этот ход' },
    { id: 'priest', name: 'Жрец', icon: '✨', race: 'Faun', uniqueSkill: 'wish', pool: ['wish', 'freeze', 'heal', 'shatter', 'goldtouch', 'teleport', 'explosive', 'elixir', 'enchant', 'treasure', 'scavenge', 'dazzle', 'boosthealth', 'boostarmour', 'banish', 'exorcise', 'magicsword', 'earthquake', 'fireball', 'mana'],
      perk: 'Faithful', flaw: 'Cautious', perkName: 'Праведный', flawName: 'Осторожный', raceName: 'Фавн',
      perkDesc: 'двойной эффект регенерации', flawDesc: '-20% пробития брони',
      clsDesc: 'пассив: Праведный · изъян: Осторожный · раса: Фавн (возрождение)',
      uniqueSkillDesc: 'Желание — одноразовый навык: собрать все клетки доски' },
    { id: 'raider', name: 'Разбойник', icon: '🏴', race: 'Halfling', uniqueSkill: 'masochism', pool: ['masochism', 'heal', 'repair', 'counter', 'goldtouch', 'explosive', 'elixir', 'slash', 'treasure', 'dazzle', 'boostdmg', 'boostgold', 'boosthealth', 'boostarmour', 'exorcise', 'trap', 'biggame', 'chamber', 'magicsword', 'explosivearmour'],
      perk: 'Daring', flaw: 'Cocky', perkName: 'Дерзкий', flawName: 'Наглый', raceName: 'Халфлинг',
      perkDesc: '100% шанс бонусов при цепочке 10+ клеток', flawDesc: 'цепочка из ровно 3 клеток ничего не даёт',
      clsDesc: 'пассив: Дерзкий · изъян: Наглый · раса: Халфлинг (храбрость)',
      uniqueSkillDesc: 'Мазохизм (15): превращает все монетки в черепов' },
    { id: 'warrior', name: 'Воин', icon: '⚔️', race: 'Dwarf', uniqueSkill: 'charge', pool: ['charge', 'heal', 'repair', 'counter', 'shatter', 'goldtouch', 'teleport', 'enchant', 'slash', 'treasure', 'scavenge', 'disarm', 'dazzle', 'boostdmg', 'boostarmour', 'banish', 'trap', 'biggame', 'magicsword', 'explosivearmour'],
      perk: 'Tough', flaw: 'Noisy', perkName: 'Прочный', flawName: 'Громкий', raceName: 'Дворф',
      perkDesc: '+10 макс. HP', flawDesc: 'черепа-монстры +5% HP',
      clsDesc: 'пассив: Прочный · изъян: Громкий · раса: Дворф (верховая атака)',
      uniqueSkillDesc: 'Рывок (18): собирать все клетки по краю поля' },
    { id: 'assassin', name: 'Ассасин', icon: '🗡', race: 'Werewolf', uniqueSkill: 'haste', pool: ['haste', 'steal', 'freeze', 'counter', 'shatter', 'teleport', 'explosive', 'elixir', 'treasure', 'disarm', 'dazzle', 'boostdmg', 'boosthealth', 'banish', 'trap', 'biggame', 'chamber', 'magicsword', 'explosivearmour', 'fireball'],
      perk: 'Cunning', flaw: 'Sneaky', perkName: 'Хитрый', flawName: 'Незаметный', raceName: 'Вервольф',
      perkDesc: '-1 к кулдауну 4-го навыка', flawDesc: '+1 HP у обычных черепов',
      raceDesc: 'Калечение: в конце твоего хода убивает не более одного старого обычного черепа с HP ниже твоей брони.',
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
  // которые получат доступ сразу при выборе класса (не из пула уровня).
  const UNIQUE_SKILLS = [
    { id: 'berserk', name: 'Берсерк', icon: '🪓', cooldown: 14, desc: 'Убийство черепа даёт HP равный его атаке' },
    { id: 'rune', name: 'Руна', icon: '🔮', cooldown: 12, desc: 'Замкнутая цепочка собирает все клетки внутри неё' },
    { id: 'greed', name: 'Жадность', icon: '💀', cooldown: 25, desc: 'Убить всех черепов рядом с монеткой' },
    { id: 'laststand', name: 'Последний рубеж', icon: '🛡', cooldown: 35, desc: 'Полное лечение HP, если герой погибнет в этот ход' },
    { id: 'wish', name: 'Желание', icon: '✨', cooldown: 0, desc: 'Одноразовый навык: собрать все клетки доски' },
    { id: 'masochism', name: 'Мазохизм', icon: '🏴', cooldown: 15, desc: 'Все монетки → черепа' },
    { id: 'charge', name: 'Рывок', icon: '⚔️', cooldown: 18, desc: 'Сорвать все клетки по краю поля' },
    { id: 'haste', name: 'Спешка', icon: '🗡', cooldown: 16, desc: 'Ставит цепную клетку: подходит к любому типу меток' },
    { id: 'volley', name: 'Воллей', icon: '🏹', cooldown: 15, desc: 'Все мечи → стрелы (стрелы стреляют на 1 клетку)' },
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
        if (Number.isFinite(lvl) && lvl >= 1) clean[id] = Math.floor(lvl);
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
        gold: 0,
        goldBox: 0,
        stats: { strength: 1, vitality: 1, luck: 1, dexterity: 1, charisma: 0, damage: 0, defense: 0 },
      },
      // Five original equipment slots.  Base hero values already include the
      // starting gear; bought items only add their rolled mods.
      equipment: ITEM_SLOTS.map(sl => ({ slot: sl.id, slotName: sl.name, icon: sl.icon,
        name: 'Стартовое ' + sl.name.toLowerCase(), mods: {} })),
      skills: [],                  // active skill ids (class-unique + chosen in level-ups)
      skillCooldowns: {},         // id -> turns remaining (cooldowns may be reduced by Quicken)
      pierce: BASE_PIERCING,        // вероятность пробития брони черепа
      durability: BASE_DURABILITY,  // вероятность, что пункт брони героя не списывается
      regen: 0, blunt: 0, leech: 0, spikes: 0, quicken: 0,
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
      baseDamageBonus: 0,           // +1 к базовому урону за апгрейд «Ускорение»
      specialsKilled: 0,            // сколько спец/боссов убито за забег (прогресс трофея)
      unlocksThisRun: 0,          // сколько классов разблокировано за забег (cap)
      trophiesThisRun: 0,         // сколько трофеев забрано за забег
      pendingTrophy: null,        // { kind, cls, text } — ожидает захвата
      upgradeBar: 0,
      xpLevelCum: 0,
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
      // Trophy counter: 20..30 special kills, reduced only on non-easy.
      trophyRemaining: 25,      // re-rolled to 20..30 after each trophy
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

  function randomTileType() {
    let t = randInt(0, 4, 1);
    while (t === 1) t = randInt(0, 4, 1); // refill filter excludes skulls, re-rolling consumes RNG
    return t === 0 ? 'potion' : t === 2 ? 'coin' : t === 3 ? 'shield' : 'sword';
  }

  // Клетка с возможной сломанностью щита (Rogue Flaw: 3%)
  function createTile() {
    const t = randomTileType();
    // Rogue Clumsy: 3% щитов выпадают сломанными (0 брони/upgrade-очков, 0 бонусов)
    if (t === 'shield' && S && (S.classId ? CLASS_BY_ID[S.classId].flaw === 'Clumsy' : false) && random() < 0.03) {
      S.brokenShields++;
      return { type: 'shield', broken: true };
    }
    // Mage Gifted: 5% зелий выпадают как mana-зелья (+1 к кулдауну навыка)
    if (t === 'potion' && S && (S.classId ? CLASS_BY_ID[S.classId].perk === 'Gifted' : false) && random() < 0.05) {
      return { type: 'potion', mana: true };
    }
    // Ranger Agile: 5% мечей выпадают стрелками (стреляют на 1 клетку)
    if (t === 'sword' && S && (S.classId ? CLASS_BY_ID[S.classId].perk === 'Agile' : false) && random() < 0.05) {
      return { type: 'arrow' };
    }
    return { type: t };
  }

  // Спец-череп: определяется рандомно из 31 спец-типа (spec 02-chain-and-turn.md).
  function randSpecialType() {
    return SPECIAL_ABILITY_TYPES[randInt(0, SPECIAL_ABILITY_TYPES.length - 1)];
  }

  function makeSkull(boss, special, typeId) {
    const cls = S && S.classId ? CLASS_BY_ID[S.classId] : null;
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
    if (level > 40) q = Math.floor(q * (1 + (level - 40) / 60));
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
    // Sprint overrides the three base stats after the normal rolls.
    if (S.mode === 'sprint') {
      a = Math.max(1, randInt(Math.floor(q / 20), Math.floor(q / 12)));
      d = Math.max(0, randInt(Math.floor(q / 10), Math.floor(q / 6)));
      h = Math.max(4, randInt(Math.floor(q / 8), Math.floor(q / 4)));
    }
    // спец-череп: тип определяет модификатор статов. Если передан typeId — используем, иначе рана.
    let effType = (boss || !special) ? (boss ? 1 : 0) : (typeId != null ? typeId : randSpecialType());
    // Mortal enemy: non-Human races have a 5% (2.5% Intimidating) chance that
    // a generated special is their personal nemesis.
    if (special && !boss && cls && cls.race !== 'Human') {
      const chanceP = cls.perk === 'Intimidating' ? 0.025 : 0.05;
      if (random(5) < chanceP && MORTAL_BY_RACE[cls.race] != null) {
        effType = MORTAL_BY_RACE[cls.race];
      }
    }
    let atk = a, def = d, hp = h;
    if (boss) {
      atk = Math.round(a * BOSS_STATS.atk);
      def = Math.round(d * BOSS_STATS.def);
      hp = Math.round(h * BOSS_STATS.hp);
    } else if (special) {
      const mod = ENEMY_STAT_OVERRIDES[effType] || { atk: 2, def: 2, hp: 4 };
      // 0 — Golden (A=0): атака 0; 0.5 — усечение x/2 (Invisible H, Meat Shield A)
      atk = mod.atk === 0 ? 0 : (mod.atk === 0.5 ? Math.floor(a * 0.5) : Math.round(a * mod.atk));
      def = effType === 17 ? Math.max(0, (S && S.hero ? S.hero.armor : 0)) : Math.round(d * mod.def);
      hp = mod.hp === 0.5 ? Math.floor(h * 0.5) : Math.round(h * mod.hp);
    }
    // Спец/босс: броня не ниже 1 (ориг: defence = max(1, defence))
    if (boss || (special && effType !== 17)) def = Math.max(1, def);
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
    hp = Math.round(hp * noisy) + flawBonusHp;
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
      enteredTurn: (S && S.turn != null) ? S.turn : 0,
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
      for (let r = 0; r <= writeRow; r++) emptySlots.push({ col: c, row: r });
    }
    emptySlots.forEach(slot => {
      S.board[slot.row * COLS + slot.col] = createNewTile();
    });
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
        if (!cell || cell.type === 'skull') continue;
        S.board[j] = { type: randomTileType() };
      }
    }
  }

  // a guaranteed chainable run must exist: same-category (or sword+skull) adjacent x3
  function hasLegalChain(board) {
    // Ищем не только прямые линии, но и любую связную цепочку: если в одной
    // категории есть компонента из 3+ клеток, в ней всегда найдётся путь из 3.
    // Замороженные клетки не выбираются (Freezing), поэтому их не считаем.
    function catOf(cell) {
      if (!cell || cell.type === 'empty' || cell.frozen) return null;
      if (cell.type === 'skull' || cell.type === 'arrow') return 'sword';
      return cell.type;
    }
    const seen = new Set();
    for (let start = 0; start < CELL_COUNT; start++) {
      const startCat = catOf(board[start]);
      if (!startCat || seen.has(start)) continue;
      const stack = [start];
      seen.add(start);
      let count = 0;
      while (stack.length > 0) {
        const idx = stack.pop();
        count++;
        const r = Math.floor(idx / COLS), c = idx % COLS;
        for (let dr = -1; dr <= 1; dr++) for (let dc = -1; dc <= 1; dc++) {
          if (dr === 0 && dc === 0) continue;
          const nr = r + dr, nc = c + dc;
          if (nr < 0 || nr >= ROWS || nc < 0 || nc >= COLS) continue;
          const nIdx = nr * COLS + nc;
          if (seen.has(nIdx)) continue;
          const nCat = catOf(board[nIdx]);
          if (!nCat || !chainAllows(nCat, startCat)) continue;
          seen.add(nIdx);
          stack.push(nIdx);
        }
      }
      if (count >= 3) return true;
    }
    return false;
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

  function createNewTile() {
    // Treasure Chamber: every new tile is a coin this turn.
    if (S.forceCoins > 0) return { type: 'coin' };
    // Original refill: 20% of new tiles are skulls. A skull created while the
    // promotion counter is 0 becomes a special (types 1..32, 1 = boss).
    if (randInt(0, 99, 1) < 20) {
      let skull;
      if ((S.promotionRemaining || 0) <= 0) {
        const type = randInt(1, 32, 1);
        skull = type === 1 ? makeSkull(true) : makeSkull(false, true, type);
        // Sprint: 200-turn opening budget, then a promoted enemy every 10 turns.
        S.promotionRemaining = S.mode === 'sprint' ? 10 : (clsFlaw() === 'Zealous' ? 42 : 43);
        S.specialsPromoted = (S.specialsPromoted || 0) + 1;
      } else {
        skull = makeSkull(false);
      }
      skull.enteredTurn = S.turn;
      return { type: 'skull', skull };
    }
    return createTile();
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
      for (let r = 0; r <= writeRow; r++) emptySlots.push({ col: c, row: r });
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

    return { placed, bossPlaced };
  }

  // --- chain rules ----------------------------------------------------------
  // Стрелы (Ranger): особые меч-клетки — идут в одну цепочку с мечами.
  // «Стрелять на 1 клетку» — стреляющие на 1 клетку: череп может быть добавлен
  // в цепочку, если он в 1 клетке от стрелы (1 ячейка зазором).
  function chainTypeForCell(cell) {
    if (!cell) return null;
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

  function tryExtendChain(cellIdx) {
    if (S.resolving || S.over) return;
    if (cellIdx < 0 || cellIdx >= CELL_COUNT) return;
    const cell = S.board[cellIdx];
    if (!cell || cell.type === 'empty') return;
    // Замороженные клетки не совпадают, включая черепа (Freezing)
    if (cell.frozen) return;

    let chain = S.selected;
    if (chain.length === 0) {
      // любая клетка начинает цепочку; череп/стрела = категория меча
      chain = [cellIdx];
      S.selected = chain;
      S.chainType = chainTypeForCell(cell);
      onChainChanged();
      return;
    }

    const last = chain[chain.length - 1];
    if (cellIdx === last) return;

    // back-step: move to the cell two steps back
    if (chain.length >= 2 && cellIdx === chain[chain.length - 2]) {
      chain.pop();
      S.selected = chain;
      onChainChanged();
      return;
    }

    // rune: clicking the first cell closes the loop (chain is kept as-is —
    // rune triggers on resolve if the last cell is adjacent to the first)
    if (chain.length >= 3 && cellIdx === chain[0] && isAdjacent(last, chain[0])) {
      return;
    }

    if (chain.includes(cellIdx)) return;

    const cellType = S.board[cellIdx].type;
    // Chain tiles (Shock+Awe, Haste) are wildcards; everything else must match
    // the chain's type (skulls and arrows count as swords).
    if (!chainAllows(cellType, S.chainType)) return;

    let adjacent = isAdjacent(last, cellIdx);
    if (!adjacent) {
      // стрелка может стрелять по черепу в 1 клетке от себя (+череп и стрелка — меч-категория)
      const lastCell = S.board[last];
      if (lastCell && lastCell.type === 'arrow' && cellType === 'skull') {
        adjacent = isAdjacentArrow(last, cellIdx);
      }
    }
    if (!adjacent) return;

    chain.push(cellIdx);
    S.selected = chain;
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
      cellEl.classList.toggle('frozen', !!cell.frozen);
      const crippleCandidate = S.classId === 'assassin' && !s.special && !s.boss
        && (s.enteredTurn || 0) < S.turn && s.hp > 0 && s.hp < S.hero.armor;
      const crippleTarget = crippleCandidate && S.board
        .filter(c => c && c.type === 'skull' && c.skull && !c.skull.special && !c.skull.boss
          && (c.skull.enteredTurn || 0) < S.turn && c.skull.hp > 0 && c.skull.hp < S.hero.armor)
        .sort((a, b) => a.skull.hp - b.skull.hp)[0]?.skull === s;
      cellEl.classList.toggle('cripple-target', !!crippleTarget);
      cellEl.title = crippleTarget
        ? `🐺 Калечение: выбран самый слабый старый обычный череп (HP ${s.hp} < броня ${S.hero.armor}); не более одного за ход.`
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

  // exact mirror of the resolveChain math, for the live chain preview
  // интерьер цепочки: цель BFS от всех клеток границы (не в пути) — всё не посещённое и не в пути — интерьер
  // Interior of a closed chain (Rune): the path forms a polygon through cell
  // centers; cells whose centers fall inside it are the enclosed interior.
  // Closed if the last cell is adjacent to the first (ray-casting, even-odd).
  function interiorCells(chain) {
    if (!chain || chain.length < 3) return [];
    const pathSet = new Set(chain);
    const pts = chain.map(i => ({ x: i % COLS, y: Math.floor(i / COLS) }));
    const n = pts.length;
    function inside(px, py) {
      let crossings = 0;
      for (let i = 0; i < n; i++) {
        const a = pts[i], b = pts[(i + 1) % n];
        if ((a.y > py) !== (b.y > py)) {
          const xAtY = a.x + (b.x - a.x) * ((py - a.y) / (b.y - a.y));
          if (xAtY > px) crossings++;
        }
      }
      return crossings % 2 === 1;
    }
    const interior = [];
    for (let i = 0; i < CELL_COUNT; i++) {
      if (pathSet.has(i)) continue;
      const x = i % COLS, y = Math.floor(i / COLS);
      if (inside(x, y)) interior.push(i);
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
    const raw = baseDamage() + S.hero.weaponDamage * swordCount;
    return Math.round(raw * (S.swordBoost > 0 ? 2 : 1));
  }

  function chainPreview() {
    const chain = S.selected;
    if (!chain || chain.length === 0) return null;
    let swordCount = 0, shieldCount = 0, coinCount = 0, potionCount = 0;
    const skulls = [];
    chain.forEach(idx => {
      const cell = S.board[idx];
      if (!cell) return;
      if (cell.type === 'sword' || cell.type === 'arrow') swordCount += swordWeight(cell);
      else if (cell.type === 'shield') shieldCount++;
      else if (cell.type === 'coin') coinCount++;
      else if (cell.type === 'potion') potionCount++;
      else if (cell.type === 'skull') skulls.push({ idx, skull: cell.skull });
    });
    const first = S.board[chain[0]];
    const chainType = S.chainType || (first ? (first.type === 'skull' || first.type === 'arrow' ? 'sword' : first.type) : null);
    if (chainType === 'sword') {
      const damage = swordDamage(swordCount);
      const lethals = skulls.filter(x => damage >= x.skull.def + x.skull.hp);
      return { kind: 'sword', count: chain.length, damage, swordCount, skulls, lethals };
    }
    if (chainType === 'shield') {
      const perShield = S.armorPerShield + S.hero.stats.dexterity + S.hero.stats.defense * 2;
      const gain = shieldCount * perShield;
      return { kind: 'shield', count: chain.length, gain };
    }
    if (chainType === 'coin') {
      const gold = Math.round(coinCount * S.goldPerCoin);
      return { kind: 'coin', count: chain.length, gold };
    }
    if (chainType === 'potion') {
      const luck = S.hero.stats.luck;
      const gain = Math.round(potionCount * (HEAL_PER_POTION + luck));
      return { kind: 'potion', count: chain.length, gain };
    }
    return null;
  }

  function renderChainTip() {
    const tip = el('chain-tip');
    if (!tip) return;
    if (el('chain-count')) el('chain-count').textContent = '⛓ ' + (S.selected ? S.selected.length : 0);
    const p = chainPreview();
    if (!p || p.count < 3) {
      tip.classList.remove('show');
      tip.textContent = '';
      return;
    }
    tip.classList.add('show');
    let effect;
    if (p.kind === 'sword') {
      effect = p.skulls.length > 0
        ? `💥 ${p.damage} урона · ☠ ${p.lethals.length}/${p.skulls.length} убиты`
        : `💥 ${p.damage} урона · ${p.swordCount} мечей`;
    } else if (p.kind === 'shield') {
      effect = `🛡 +${p.gain} брони`;
    } else if (p.kind === 'coin') {
      effect = `💰 +${p.gold} золота`;
    } else if (p.kind === 'potion') {
      effect = `❤ +${p.gain} HP`;
    } else { effect = ''; }
    tip.innerHTML = `<b>${p.count}</b> клеток · ${effect}`;
  }

  function renderSelection() {
    qsa('.cell').forEach(c => c.classList.remove('selected'));
    qsa('.cell').forEach(c => c.classList.remove('lethal'));
    S.selected.forEach(idx => {
      const cellEl = qs(`.cell[data-idx="${idx}"]`);
      if (cellEl) cellEl.classList.add('selected');
    });
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
    if (S.selected.length >= 3 && isAdjacent(first, last)) {
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

  function bonusPct(kind) {
    // шанс бонуса (классика): 20% + 5% за соответствующий стат + 5% за хитрость
    const base = BONUS_BASE_CHANCE;
    const luck = S.hero.stats.luck, dex = S.hero.stats.dexterity, vit = S.hero.stats.vitality, str = S.hero.stats.strength, cha = S.hero.stats.charisma;
    const per = { coin: luck, shield: dex, potion: vit, xp: str }[kind] || 0;
    return Math.round((base + 0.05 * per + 0.05 * cha) * 100);
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
    if (el('dmg-text')) el('dmg-text').textContent = `⚔ ${fmt(baseDamage())} + ${fmt(h.weaponDamage)}×меч`;
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
      const capped = S.unlocksThisRun >= MAX_UNLOCKS_PER_RUN;
      const need = S.trophyRemaining || TROPHY_KILLS_PER_TROPHY;
      el('trophy-text').textContent = capped ? '🏆 max' : `🏆 ${Math.min(S.specialsKilled, need)} / ${need}`;
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
      btn.title = skill.desc + ' (кд: ' + Math.max(0, skill.cooldown - (level - 1)) + ')' + (level > 1 ? ', уровень ' + level : '');
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
    seedRandom(runSeed);
    S = newGameState();
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

    // Priest flaw Cautious: pierce threshold 30% instead of the base 50%.
    if (cls.flaw === 'Cautious') S.pierce = 0.30;
    // Original promotion/trophy counters (Sprint gets a 200-turn first budget).
    S.promotionRemaining = cls.flaw === 'Zealous' ? 49 : (S.mode === 'sprint' ? 200 : 50);
    S.trophyRemaining = randInt(20, 30, 5);

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

  function activateSkill(id) {
    if (S.over || S.resolving) return;
    const left = S.skillCooldowns[id] || 0;
    if (left > 0) return;
    const skill = SKILL_BY_ID[id];
    if (!skill) return;
    // Quicken (Upgrade): −1 к кулдауну всех навыков
    const adjust = (S.cooldownAdjust && S.cooldownAdjust[id]) || 0;
    const level = (S.skillLevels && S.skillLevels[id]) || 1;
    S.skillCooldowns[id] = Math.max(id === 'wish' ? 0 : 2, skill.cooldown - (level - 1) - S.quicken + adjust);
    // Fire Breath (Dragonkin/Paladin race): ежедневная вспышка — любого навык → случайный черепу вспышка
    fireBreathOnSkill();
    applySkill(id);
    renderHUD();
    renderBoard();
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
        target.burn = true;
        target.fireDamage = baseDamage();
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

  function hitSkullAt(idx, amount, opts) {
    opts = opts || {};
    const cell = S.board[idx];
    if (!cell || cell.type !== 'skull' || !cell.skull || cell.skull.hp <= 0) return false;
    const s = cell.skull;
    let dmg = Math.max(0, Math.round(amount));
    if (dmg <= 0) return false;
    if (!opts.ignoreDefence) {
      if (randInt(0, 100, 5) >= S.pierce * 100) {
        const absorbed = Math.min(dmg, Math.max(0, s.def));
        s.def -= absorbed;
        dmg -= absorbed;
      }
    }
    s.hp -= dmg;
    floatOn('💥 ' + dmg, 'hurt');
    if (s.hp <= 0) {
      S.board[idx] = { type: 'empty' };
      S.skullsKilled++;
      registerSkullEnd(s);
      if (opts.reward !== false) awardKillXp(s);
      audioKill();
      return true;
    }
    return false;
  }

  // Collect a set of board cells the same way a chain does.  Used by all
  // native skills that "grab" tiles (Freeze/Repair/Treasure/…).
  function collectIndices(indices, opts) {
    opts = opts || {};
    const uniq = [...new Set(indices)].filter(i => i >= 0 && i < CELL_COUNT);
    let swords = 0, shields = 0, coins = 0, potions = 0;
    const skullIdxs = [], potionIdxs = [], broken = new Set();
    uniq.forEach(i => {
      const c = S.board[i];
      if (!c || c.type === 'empty') return;
      if (c.type === 'sword' || c.type === 'arrow') {
        if (c.broken) broken.add(i); else swords += swordWeight(c);
      } else if (c.type === 'shield') {
        if (c.broken) broken.add(i); else shields++;
      } else if (c.type === 'coin') coins++;
      else if (c.type === 'potion') {
        const allowed = (!c.acid || !opts.skipAcid) && (!c.mana || !opts.skipMana);
        if (allowed) { potions++; potionIdxs.push(i); }
      } else if (c.type === 'skull') {
        if (!c.skull || !(c.skull.special || c.skull.boss) || opts.includeSpecial) skullIdxs.push(i);
      }
    });
    S._chainOrder = uniq.slice();
    S._runePathSet = new Set(uniq);
    S._lastChainCells = opts.burn !== false ? uniq.slice() : [];
    S._potionIdxs = potionIdxs;
    const result = applyChainEffects(swords, shields, coins, potions,
      opts.noSkulls ? [] : skullIdxs, uniq.length >= 10, broken, potionIdxs);
    if (result.xpGained > 0) gainXp(result.xpGained);
    const manaCount = uniq.filter(i => { const c = S.board[i]; return c && c.type === 'potion' && c.mana; }).length;
    if (manaCount > 0) refillRandomSkill(manaCount);
    if (S.leech > 0 && result.dmgShown > 0) heal(S.leech * result.dmgShown);
    // cleared tiles + compact/refill, exactly like a resolved chain
    uniq.forEach(i => {
      const c = S.board[i];
      if (!c) return;
      if (c.type === 'skull') {
        if (c.skull && c.skull.hp > 0 && opts.keepSurvivingSkulls) return;
        if (!c.skull || c.skull.hp <= 0) S.board[i] = { type: 'empty' };
        return;
      }
      S.board[i] = { type: 'empty' };
    });
    compactBoardAndRefill();
    if (!hasLegalChain(S.board)) ensureChainable();
    return result;
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
      S.stealActive = 1;
      flash('🦊 Воровство: атака черепов в этом ходу даёт золото');
    } else if (id === 'freeze') {
      let frozen = 0;
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (c && c.type === 'skull' && c.skull) {
          c.skull.frozenTurn = S.turn;
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
      const res = collectIndices(idxs, { noSkulls: true, skipAcid: true, skipMana: true });
      flash('❤ Собрано зелий: ' + idxs.length + ' (+' + Math.round(res.healGained) + ' HP)');
    } else if (id === 'repair') {
      const idxs = [];
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (c && c.type === 'shield') idxs.push(i);
      }
      const res = collectIndices(idxs, { noSkulls: true });
      flash('⚒ Собрано щитов: ' + idxs.length + ' (+' + Math.round(res.armorGained) + ' брони)');
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
      const idxs = [];
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (c && c.type === 'potion' && !c.acid) idxs.push(i);
      }
      idxs.forEach(i => { S.board[i] = { type: 'empty' }; });
      fillAfterConversion();
      const xp = idxs.length * 2;
      gainXp(xp);
      flash('⚗ Эликсир: зелий → XP ' + xp);
    } else if (id === 'enchant') {
      openModal('armor');
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
      const res = collectIndices(idxs, { noSkulls: true });
      flash('💎 Кладовая: собрано монет ' + idxs.length + ' (+' + Math.round(res.goldGained) + ' золота)');
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
      S.swordBoost = 1;
      flash('⚔ Усиление урона: оружие ×2 в этом ходу');
    } else if (id === 'boostgold') {
      S.coinBoost = 1;
      flash('🪙 Усиление золота: золото ×2 в этом ходу');
    } else if (id === 'boosthealth') {
      S.potionBoost = 1;
      flash('❤ Усиление лечения: зелья ×2 в этом ходу');
    } else if (id === 'boostarmour') {
      S.shieldBoost = 1;
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
      S.oneHealthSkulls = 1;
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
      S.forceCoins = 1;
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
      S.berserkActive = 1;
      flash('🪓 БЕРСЕРК: HP за убийства этого хода');
    } else if (id === 'rune') {
      S.runeActive = 1;
      S.runeUsedThisTurn = 0;
      flash('🔮 РУНА: замкнутая цепочка соберёт всё внутри');
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
          if (cell && cell.type === 'skull') {
            S.board[j] = { type: 'empty' };
            S.skullsKilled++;
            registerSkullEnd(cell.skull);
            killed++;
            audioKill();
          }
        }
      });
      flash('🗡 Жадность: убито ' + killed + ' черепов (без награды)');
    } else if (id === 'laststand') {
      S.lastStand = 1;
      flash('🛡 Последний рубеж готов: смерть → полное HP');
    } else if (id === 'wish') {
      const idxs = [];
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (!c || c.type === 'skull') continue;
        idxs.push(i);
      }
      const res = collectIndices(idxs, { keepSurvivingSkulls: true });
      if (!S.usedSkillIds) S.usedSkillIds = {};
      S.usedSkillIds.wish = true;
      S.skills = S.skills.filter(sid => sid !== 'wish');
      flash('✨ ЖЕЛАНИЕ: собрано всё (+' + Math.round(res.healGained) + ' HP, +' + Math.round(res.goldGained) + ' золота)');
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
  function registerSkullEnd(s) {
    if (s && (s.boss || s.special)) S.specialsKilled = (S.specialsKilled || 0) + 1;
  }

  function gainXp(amount) {
    const v = amount * (S.mode === 'sprint' ? 2 : 1);
    S.hero.xp += v;
    S.hero.xpLevelCum += v;
    return v;
  }

  // Mana potion: refill one random equipped skill slot (original AddResource).
  function refillRandomSkill(count) {
    let n = 0;
    for (let k = 0; k < count; k++) {
      if (!S.skills || S.skills.length === 0) break;
      const pick = S.skills[randInt(0, S.skills.length - 1, 5)];
      if ((S.skillCooldowns[pick] || 0) > 0) n++;
      S.skillCooldowns[pick] = 0;
    }
    if (n > 0) flash('🔹 Мана: восстановлено навыков — ' + n);
    return n;
  }

  // Mana potions: снять по 1 с самых длинных кулдаунов.
  function reduceSkillCooldowns(count) {
    let left = count;
    while (left > 0) {
      const cdIds = Object.keys(S.skillCooldowns).filter(id => S.skillCooldowns[id] > 0);
      if (cdIds.length === 0) break;
      const longest = cdIds.reduce((a, b) => (S.skillCooldowns[a] > S.skillCooldowns[b] ? a : b));
      S.skillCooldowns[longest] = Math.max(0, S.skillCooldowns[longest] - 1);
      left--;
    }
    return count - left;
  }

  function heal(amount) {
    const before = S.hero.hp;
    S.hero.hp = Math.min(S.hero.maxHp, S.hero.hp + amount);
    if (S.hero.hp !== before) {
      floatOn('+' + Math.round(S.hero.hp - before), 'heal');
    }
  }

  function repair(amount) {
    const before = S.hero.armor;
    S.hero.armor = Math.min(S.hero.maxArmor, S.hero.armor + amount);
    if (S.hero.armor !== before) {
      floatOn('+' + Math.round(S.hero.armor - before), 'armor');
    }
  }

  function gainGold(amount) {
    const v = amount * (S.mode === 'sprint' ? 2 : 1);
    S.hero.gold = Math.min(GOLD_BOX_MAX, S.hero.gold + v);
    S.goldEarned += v;
  }

  function addUpgradeBar(amount) {
    const v = amount * (S.mode === 'sprint' ? 2 : 1);
    S.upgradeEarned = (S.upgradeEarned || 0) + v;
    S.upgradeBar = Math.min(UPGRADE_BAR_MAX, S.upgradeBar + v);
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

  // --- resolve chain (классика: оружие/щиты/монетки/зелья + бонусные эффекты) ---
  function applyChainEffects(swordCount, shieldCount, coinCount, potionCount, skullIdxs, raidarPerk, brokenSet, potionIdxs) {
    potionIdxs = potionIdxs || [];
    // Без сторонних побочных эффектов от «выборочности» — чистая арифметика.
    // brokenSet (Set) — индексы брошенных (сломанных) щитов; для рун-интерьера — пусто.
    const result = { xpGained: 0, goldGained: 0, armorGained: 0, healGained: 0, dmgShown: 0, kills: 0, bossKills: 0, manaCollected: 0, brokenShields: brokenSet ? brokenSet.size : 0 };
    const cls = S.classId ? CLASS_BY_ID[S.classId] : null;
    const raiderPerk = !!(cls && cls.perk === 'Daring' && raidarPerk);

    // --- swords → skull damage ---
    if (skullIdxs.length > 0) {
      const totalDamage = swordDamage(swordCount);
      const pathSet = S._runePathSet; // цепочка, чтобы проверить uppercut (Dwarf) и Armoured (27)

      // Meat Shield (30): пока один жив — не-МС черепы получают 0 урона, урон переходит к Meat Shield
      const meatShieldIdxs = [];
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (c && c.type === 'skull' && c.skull && c.skull.type === 30) meatShieldIdxs.push(i);
      }
      const meatShieldAlive = meatShieldIdxs.length > 0;

      // Spiky (2): сумма урона, нанесённого Spiky черепам в этой цепочке (отдаётся герою)
      let spikyReflect = 0;

      skullIdxs.forEach(idx => {
        const cell = S.board[idx];
        if (!cell || cell.type !== 'skull') return;
        const s = cell.skull;
        // Lich phylactery: collecting the box grants 25 gold and removes it.
        if (s.type === 32 && s.phylactery) {
          S.board[idx] = { type: 'empty' };
          S.skullsKilled++;
          registerSkullEnd(s);   // сбор филактерия — это и есть убийство Лича
          result.kills++;
          result.xpGained += XP_PER_BOSS;
          gainGold(25);
          result.goldGained += 25;
          audioKill();
          log('💀 Филактерий Лича: +25 золота');
          flash('💀 Филактерий: +25 золота');
          return;
        }
        // Armoured (27): урон только если путь идёт через 3 клетки выше черепа
        const isArmoured = s.type === 27;
        let armouredHit = true;
        if (isArmoured) {
          const r = Math.floor(idx / COLS);
          const order = S._chainOrder || [];
          const pos = order.indexOf(idx);
          const next = pos >= 0 && pos + 1 < order.length ? order[pos + 1] : -1;
          armouredHit = next >= 0 && Math.floor(next / COLS) < r;
        }
        if (!armouredHit) {
          // Armoured: only the next chain cell moving upward can damage it.
          return;
        }
        const order = S._chainOrder || [];
        const pos = order.indexOf(idx);
        let dwarfUppercut = false;
        if (cls && cls.race === 'Dwarf' && pos > 0 && pos + 1 < order.length) {
          const prev = order[pos - 1], next = order[pos + 1];
          const col = idx % COLS;
          dwarfUppercut = prev % COLS === col && next % COLS === col
            && Math.floor(prev / COLS) > Math.floor(idx / COLS)
            && Math.floor(next / COLS) < Math.floor(idx / COLS);
        }
        if (meatShieldAlive && s.type !== 30 && !dwarfUppercut) {
          // Meat Shield blocks ordinary direct damage; Dwarf/Armoured priority branches precede it.
          return;
        }
        // Dwarf's mortal enemy Chaotic can ONLY be hurt by an uppercut; a
        // Chaotic skull is otherwise a normal target.
        if (s.type === 14 && cls && cls.race === 'Dwarf' && !dwarfUppercut) return;
        let dmg = totalDamage;
        // Dwarf Uppercut: chain must pass straight down → skull → straight up.
        if (dwarfUppercut) dmg += S.hero.weaponDamage;
        result.dmgShown += dmg;
        S.swordsHits += dmg;
        // Orc Stun: 10% шанс замёрзнуть аткующий череп (если выжил)
        // Spiky reflect — отдельно накапливается
        if (s.type === 2) spikyReflect = Math.max(spikyReflect, totalDamage);

        if (randInt(0, 100, 5) < S.pierce * 100) {
          s.hp -= dmg;
        } else {
          const dmgToDef = Math.min(dmg, s.def);
          s.def -= dmgToDef;
          s.hp -= (dmg - dmgToDef);
        }
        // Vampire hostile branch: direct attack adds GetAttack even beyond old max HP.
        if (s.type === 20 && s.hp > 0) {
          s.hp += enemyGetAttack(s);
          s.maxHp = Math.max(s.maxHp || 0, s.hp);
        }

        if (cls && cls.race === 'Orc' && randInt(0, 100, 5) < 10 && s.hp > 0) {
          s.stunned = true;
        }
        if (s.hp <= 0) {
          // Lich turns into a one-HP phylactery; it is not a kill until the box is collected.
          if (s.type === 32 && !s.phylactery) {
            s.phylactery = true;
            s.lichDelay = true;
            s.countdown = 1;
            s.atk = 0; s.def = 0; s.hp = 1; s.maxHp = 1;
            S.poison = 0;
            log('💀 Лич оставил филактерий');
            flash('💀 Лич оставил филактерий');
            return;
          }
          const isBoss = s.boss;
          S.board[idx] = { type: 'empty' };
          S.skullsKilled++;
          registerSkullEnd(s);
          result.kills++;
          if (isBoss) result.bossKills++;
          // Changeling Shapeshift: collecting a special/boss immediately
          // refills one random equipped skill (may pick a ready one).
          if ((s.special || isBoss) && cls && cls.race === 'Changeling' && S.skills.length > 0) {
            const pick = S.skills[randInt(0, S.skills.length - 1, 5)];
            S.skillCooldowns[pick] = 0;
            flash('🔮 Меняться: навык «' + (SKILL_BY_ID[pick] ? SKILL_BY_ID[pick].name : pick) + '» готов');
          }
          const xp = isBoss ? XP_PER_BOSS : (S.xpPerSkull || XP_PER_SKULL);
          result.xpGained += xp;
          if (S.stealActive > 0) { gainGold(4); result.goldGained += 4; }
          // Золотой (3): убит до конца таймера — +40 золота
          if (s.type === 3) {
            gainGold(40);
            result.goldGained += 40;
            log('🏆 Золотой погиб: +40 золота');
            flash('🏆 Золотой погиб: +40 золота');
          }
          audioKill();
          // Barbarian Berserk: HP за убийства этого хода
          if (S.berserkActive > 0) {
            const healAmount = Math.max(0, s.atk);
            const beforeHp = S.hero.hp;
            S.hero.hp = Math.min(S.hero.maxHp, S.hero.hp + healAmount);
            result.healGained += S.hero.hp - beforeHp;
          }
          // Ка-Ми-Казе (10): при убийстве — тот же взрыв в соседних не-черепах
          if (s.type === 10) explodeKamikazeCell(idx, false);
        }
      });
      // Невыбранные Meat Shield — каждая получает D (урона в цепочке)
      if (meatShieldAlive) {
        const unselected = meatShieldIdxs.filter(i => !skullIdxs.includes(i));
        if (unselected.length > 0) {
          for (const i of unselected) {
            const cell = S.board[i];
            if (!cell || cell.type !== 'skull' || cell.skull.hp <= 0) continue;
            // Original AttackSkull: the skull's defence absorbs damage first.
            const absorbed = Math.min(totalDamage, Math.max(0, cell.skull.def || 0));
            cell.skull.def = Math.max(0, (cell.skull.def || 0) - absorbed);
            cell.skull.hp -= (totalDamage - absorbed);
            if (cell.skull.hp <= 0) {
              S.board[i] = { type: 'empty' };
              S.skullsKilled++;
              registerSkullEnd(cell.skull);
              result.kills++;
              result.xpGained += (S.xpPerSkull || XP_PER_SKULL);
              audioKill();
            }
          }
        }
      }
      // Смерть от эффектов цепочки: Last Stand имеет право спасти, и только
      // после этого — game over. Остальные эффекты цепочки уже не применяем.
      const chainDeathCheck = () => {
        if (S.hero.hp > 0) return false;
        if (lastStandTriggerIfNeeded()) return false;
        gameOver();
        return true;
      };
      // Spiky (2): отдача — игрок получает сумма урона по Spiky (armor bypass)
      let chainDead = false;
      if (spikyReflect > 0) {
        S.hero.hp = Math.max(0, S.hero.hp - spikyReflect);
        flash('🛡 Острый: возвращено ' + spikyReflect + ' HP');
        floatOn('💥 -' + spikyReflect, 'hurt');
        chainDead = chainDeathCheck();
      }
      // Burned tiles (Flaming 7): собранные обгоревшие клетки — урон героям (armor bypass)
      if (!chainDead) {
        let burnDamage = 0;
        (S._lastChainCells || []).forEach(idx => {
          const c = S.board[idx];
          if (c && typeof c.burn === 'number' && c.burn > 0) burnDamage += c.burn;
        });
        if (burnDamage > 0) {
          S.hero.hp = Math.max(0, S.hero.hp - burnDamage);
          flash('🔥 Пламя: −' + burnDamage + ' HP');
          floatOn('💥 -' + burnDamage, 'hurt');
          chainDead = chainDeathCheck();
        }
      }
      if (result.dmgShown > 0) floatOn('💥 ' + result.dmgShown, 'hurt');
      if (chainDead) return result;
    }

    // --- shields ---
    if (shieldCount > 0) {
      let perShield = S.armorPerShield + S.hero.stats.dexterity + S.hero.stats.defense * 2;
      if (S.shieldBoost > 0) perShield *= 2;
      const validShields = Math.max(0, shieldCount - result.brokenShields);
      const corrosivePresent = S.board.some(c => c && c.type === 'skull' && c.skull && c.skull.type === 12);
      const armorGain = corrosivePresent ? 0 : validShields * perShield;

      const before = S.hero.armor;
      S.hero.armor = Math.min(S.hero.maxArmor, S.hero.armor + armorGain);
      result.armorGained = S.hero.armor - before;
      // Corrosive: shields award upgrade points directly instead of repairing armor.
      const headroom = S.hero.maxArmor - before;
      const overflow = Math.max(0, armorGain - headroom);
      const upgradePoints = validShields * S.upgradePerShield * (S.shieldBoost > 0 ? 2 : 1);
      if (overflow > 0) addUpgradeBar(overflow * 2 * (S.shieldBoost > 0 ? 2 : 1));
      if (upgradePoints > 0) addUpgradeBar(upgradePoints);
      for (let i = 0; i < validShields; i++) audioArmor();

      // Bonus shields (classic) — каждый лишний щит сверх первых трёх
      const extra = Math.max(0, validShields - 3);
      if (extra > 0) {
        let chanceP = BONUS_BASE_CHANCE + 0.05 * S.hero.stats.dexterity + 0.05 * S.hero.stats.charisma;
        if (cls && cls.flaw === 'Reckless') chanceP -= 0.10;
        if (raiderPerk) chanceP = 1.0;
        chanceP = Math.min(1, Math.max(0, chanceP));
        for (let k = 0; k < extra; k++) {
          if (chance(chanceP)) {
            const bonus = perShield;
            const before2 = S.hero.armor;
            S.hero.armor = Math.min(S.hero.maxArmor, S.hero.armor + bonus);
            result.armorGained += S.hero.armor - before2;
            const headroom2 = S.hero.maxArmor - before2;
            const overflow2 = Math.max(0, bonus - headroom2);
            if (overflow2 > 0) addUpgradeBar(overflow2 * 2);
          }
        }
      }
    }

    // --- coins ---
    if (coinCount > 0) {
      const gold = Math.round(coinCount * S.goldPerCoin * (S.coinBoost > 0 ? 2 : 1));
      gainGold(gold);
      result.goldGained += gold;
      audioGold();
      // Bonus coins — каждый лишний монетный тип сверх первых трёх
      const extra = Math.max(0, coinCount - 3);
      if (extra > 0) {
        let chanceP = BONUS_BASE_CHANCE + 0.05 * S.hero.stats.luck + 0.05 * S.hero.stats.charisma;
        if (cls && cls.perk === 'Frugal') chanceP += 0.10;
        if (raiderPerk) chanceP = 1.0;
        chanceP = Math.min(1, chanceP);
        for (let k = 0; k < extra; k++) {
          if (chance(chanceP)) {
            const bonus = Math.round(S.goldPerCoin * (S.coinBoost > 0 ? 2 : 1));
            gainGold(bonus);
            result.goldGained += bonus;
          }
        }
      }
    }

    // --- potions ---
    if (potionCount > 0) {
      // Acid (6): кислотное зелье — ранит героя вместо лечения
      const normalPotionIdxs = potionIdxs.filter(idx => { const c = S.board[idx]; return c && c.type === 'potion' && !c.acid && !c.mana; });
      const actualAcidCount = potionIdxs.filter(idx => { const c = S.board[idx]; return c && c.type === 'potion' && c.acid; }).length;
      const luck = S.hero.stats.luck;
      const healBase = (HEAL_PER_POTION + luck) * (S.potionBoost > 0 ? 2 : 1);
      if (actualAcidCount > 0) {
        const acidDmg = Math.max(0, healBase * actualAcidCount);
        S.hero.hp = Math.max(0, S.hero.hp - acidDmg);
        flash('☣️ Кислотное зелье: −' + acidDmg + ' HP');
        floatOn('💥 -' + acidDmg, 'hurt');
        if (S.hero.hp <= 0 && !lastStandTriggerIfNeeded()) {
          gameOver();
          return result;
        }
      }
      // Обычное зелье лечит и снимает яд. Исключение — смертный враг Эльфа
      // (Poisonous): пока он на доске, яд не снимается.
      const poisonousPresent = S.board.some(c => c && c.type === 'skull' && c.skull && c.skull.type === 9);
      const poisonLocked = clsRace() === 'Elf' && poisonousPresent;
      if (normalPotionIdxs.length > 0 && S.poison > 0 && !poisonLocked) {
        S.poison = 0;
        flash('💚 Яд снят зельем');
      }
      const healPotionCount = normalPotionIdxs.length;
      // mana potions (Mage Gifted): collected mana potions reduce longest cooldown by 1
      const manaIdxs = brokenSet ? [] : []; // no mana info here — mana handled in resolveChain via caller chain
      let gain = Math.round(healPotionCount * healBase);
      const before = S.hero.hp;
      heal(gain);
      result.healGained = S.hero.hp - before;

      // Bonus potions — каждый лишний зелье сверх первых трёх
      const extra = Math.max(0, healPotionCount - 3);
      if (extra > 0) {
        let chanceP = BONUS_BASE_CHANCE + 0.05 * S.hero.stats.vitality + 0.05 * S.hero.stats.charisma;
        if (raiderPerk) chanceP = 1.0;
        chanceP = Math.min(1, chanceP);
        for (let k = 0; k < extra; k++) {
          if (chance(chanceP)) {
            const bonus = Math.round(healBase);
            const before2 = S.hero.hp;
            heal(bonus);
            result.healGained += S.hero.hp - before2;
          }
        }
      }
      if (result.healGained > 0) audioHeal();
    }

    return result;
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
    if (S.resolving || S.over) return;
    const chain = S.selected.slice();
    if (!chain || chain.length < 3) { clearChain(); return; }
    const cls = S.classId ? CLASS_BY_ID[S.classId] : null;

    if (S.mode === 'pretzel') {
      const p = pretzelPoints(pretzelCrossings(chain));
      S.pretzelScore = (S.pretzelScore || 0) + p.points;
      if (p.rating > 0) flash('🥨 Крендель: ' + p.points + ' очков (пересечений ' + pretzelCrossings(chain) + ')');
    }

    // Raider flaw: 3-tile chain gives nothing
    if (cls && cls.flaw === 'Cocky' && chain.length === 3) {
      clearChain();
      flash('🏴 Наглый: 3 клетки — ничего! (≥4 для урона)');
      return;
    }

    S.resolving = true;
    S.chainCount++;
    S.selected = [];
    S.chainType = null;
    const runId = RUN_ID;
    renderChainLine();
    renderChainTip();

    // categorize
    let swordCount = 0, shieldCount = 0, coinCount = 0, potionCount = 0;
    const skullIdxs = [];
    chain.forEach(idx => {
      const cell = S.board[idx];
      if (!cell || cell.type === 'empty') return;
      if ((cell.type === 'sword' || cell.type === 'arrow') && !cell.broken) swordCount += swordWeight(cell);
      else if (cell.type === 'shield') shieldCount++;
      else if (cell.type === 'coin') coinCount++;
      else if (cell.type === 'potion') potionCount++;
      else if (cell.type === 'skull') skullIdxs.push(idx);
    });

    // broken shield cells (rogue flaw) — сохранены, чтобы разделить валидные щиты
    const brokenChainSet = new Set();
    chain.forEach(idx => {
      const cell = S.board[idx];
      if (cell && cell.type === 'shield' && cell.broken) brokenChainSet.add(idx);
    });

    // clear chain cells (during fade) — pop only the cells that vanish
    chain.forEach(idx => {
      const cell = S.board[idx];
      if (!cell || cell.type === 'skull') return;
      const cellEl = qs(`.cell[data-idx="${idx}"]`);
      if (cellEl) cellEl.classList.add('pop');
    });

    // --- Rune: замкнутая цепочка → интерьер (mage class) ---
    let runeInterior = null;
    if (S.runeActive && !S.runeUsedThisTurn && cls && cls.uniqueSkill === 'rune') {
      const first = chain[0], last = chain[chain.length - 1];
      if (chain.length >= 3 && isAdjacent(first, last)) {
        const interior = interiorCells(chain);
        if (interior.length > 0) {
          S.runeUsedThisTurn = 1;
          runeInterior = interior;
          flash('🔮 РУНА: собрано внутри: ' + interior.length + ' клеток');
        }
      }
    }

    // uppercut (Dwarf): пути пути цепочки как Set — передаётся в applyChainEffects
    S._runePathSet = new Set(chain);
    S._chainOrder = chain.slice();

    setTimeout(() => {
      // Рестарт во время анимации: старая цепочка не должна резолвиться
      // в новый забег.
      if (runId !== RUN_ID) return;
      // --- основная цепочка ---
      S._lastChainCells = chain.slice();
      S._potionIdxs = [];
      chain.forEach(idx => { const c = S.board[idx]; if (c && c.type === 'potion') S._potionIdxs.push(idx); });
      const result = applyChainEffects(swordCount, shieldCount, coinCount, potionCount, skullIdxs, chain.length >= 10, brokenChainSet, S._potionIdxs);

      // Mana potion: refill one random equipped skill per token.
      if (potionCount > 0) {
        const manaCount = chain.filter(idx => {
          const c = S.board[idx];
          return c && c.type === 'potion' && c.mana;
        }).length;
        if (manaCount > 0) refillRandomSkill(manaCount);
      }

      // --- интерьер Руны ---
      let runeXp = 0;
      let runeKills = 0;
      if (runeInterior) {
        let swords = 0, shields = 0, coins = 0, potions = 0;
        const runeSk = [];
        const runePathSet = new Set(runeInterior);
        runeInterior.forEach(idx => {
          const c = S.board[idx];
          if (!c || c.type === 'empty') return;
          if ((c.type === 'sword' || c.type === 'arrow') && !c.broken) swords++;
          else if (c.type === 'shield' && !c.broken) shields++;
          else if (c.type === 'coin') coins++;
          else if (c.type === 'potion') potions++;
          else if (c.type === 'skull') runeSk.push(idx);
        });
        S._runePathSet = runePathSet; // uppercut внутри интерьера применяется к интерьеру
        S._chainOrder = runeInterior.slice();
        const runePotions = [];
        runeInterior.forEach(idx => { const c = S.board[idx]; if (c && c.type === 'potion') runePotions.push(idx); });
        const savedBurnCells = S._lastChainCells;
        S._lastChainCells = [];
        // Руна бьёт черепов внутри мечами самой цепочки: у интерьера своих
        // мечей может не быть, и тогда урон был бы нулевым.
        const res2 = applyChainEffects(swordCount, shields, coins, potions, runeSk, chain.length >= 10, new Set(), runePotions);
        S._lastChainCells = savedBurnCells;
        runeXp += res2.xpGained;
        runeKills = res2.kills;
        // Закрываем интерьер, но выжившего черепа оставляем на доске —
        // иначе он молча исчезал без убийства, XP и трофея.
        S.board = S.board.map((cell, i) => {
          if (!runeInterior.includes(i)) return cell;
          if (cell && cell.type === 'skull' && cell.skull && cell.skull.hp > 0) return cell;
          return { type: 'empty' };
        });
        S._runePathSet = new Set(chain);
        S._chainOrder = chain.slice();
      }

      S.killsThisTurn = result.kills + runeKills;

      // clear chain cells (sword/shield/coin/potion); skulls stay if alive
      const chainSet = new Set(chain);
      S.board = S.board.map((cell, i) => {
        if (!chainSet.has(i) || !cell) return cell;
        if (cell.type === 'skull') return cell;
        return { type: 'empty' };
      });

      // xp + bonus XP (classic): каждый лишний череп сверх трёх убитых этим ходом даёт шанс бонуса
      const totalKills = result.kills + (typeof runeKills === 'number' ? runeKills : 0);
      S.killsThisTurn = totalKills;
      // Halfling Guts: 15+ kills in one chain permanently +1 max armour.
      if (cls && cls.race === 'Halfling' && totalKills >= 15) {
        S.hero.maxArmor += 1;
        S.hero.armor = Math.min(S.hero.maxArmor, S.hero.armor + 1);
        flash('🍀 Храбрость: 15+ убийств за цепочку — +1 макс. брони!');
      }
      if (result.xpGained > 0 || runeXp > 0) {
        gainXp(result.xpGained + runeXp);
        const extraKills = Math.max(0, totalKills - 3);
        if (extraKills > 0 && totalKills > 0) {
          let chanceP = BONUS_BASE_CHANCE + 0.05 * S.hero.stats.strength + 0.05 * S.hero.stats.charisma;
          if (cls && cls.perk === 'Daring' && chain.length >= 10) chanceP = 1.0;
          chanceP = Math.min(1, chanceP);
          for (let k = 0; k < extraKills; k++) {
            if (chance(chanceP)) gainXp(S.xpPerSkull || XP_PER_SKULL);
          }
        }
        log(`⚔ ${result.xpGained + runeXp} XP за ${S.skullsKilled} черепа`);
      }

      // Leech («Сок»): лечит за нанесённый урон, даже если череп не убит.
      if (S.leech > 0 && result.dmgShown > 0) heal(S.leech * result.dmgShown);

      // refill: gravity + skulls enter from the top (new enemies cannot
      // attack until the next turn — enteredTurn tag)
      // Original: refill only. Skulls appear naturally (20% of new tiles) and
      // the promotion counter turns one of them into a special/boss.
      const spawned = placeNewTiles({});

      renderBoard();
      renderHUD();

      // enemy phase
      setTimeout(() => {
        if (runId !== RUN_ID) return;
        enemyPhase();
      }, 550);
    }, 420);
  }

  function awardKillXp(skull) {
    gainXp(skull && skull.boss ? XP_PER_BOSS : (S.xpPerSkull || XP_PER_SKULL));
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

  function enemyPhase() {
    // skulls that were on board at the START of this turn and not freshly
    // spawned this turn — only they do damage (enemy phase at the START of the turn)
    const living = S.board.filter(c => c && c.type === 'skull' && (c.skull.enteredTurn || 0) < S.turn);
    const poisonousPresent = S.board.some(c => c && c.type === 'skull' && c.skull && c.skull.type === 9);

    // Яд тикает в начале хода врага — до того, как Poisonous успеет наложить
    // новый яд (иначе первый ход бьёт дважды: атакой и ядом).
    if (S.poison > 0) {
      S.hero.hp = Math.max(0, S.hero.hp - S.poison);
      floatOn('☠️ -' + S.poison, 'hurt');
      flash('☠️ Яд: −' + S.poison + ' HP');
      if (enemyPhaseDeathCheck()) return;
    }

    // Burning tiles take their stored fire damage at the start of the enemy
    // phase — every burning skull, including ones that arrived this turn
    // (original EndOfTurn has no newThisTurn filter here).
    const baseDmg = baseDamage();
    for (let i = 0; i < CELL_COUNT; i++) {
      const cell = S.board[i];
      if (!cell || cell.type !== 'skull' || !cell.skull || !cell.skull.burn) continue;
      const s = cell.skull;
      const burnDmg = typeof s.fireDamage === 'number' && s.fireDamage > 0 ? s.fireDamage : baseDmg;
      s.burn = false;
      s.fireDamage = 0;
      s.hp -= burnDmg;
      if (s.hp <= 0) {
        S.board[i] = { type: 'empty' };
        S.skullsKilled++;
        registerSkullEnd(s);
        awardKillXp(s);
        audioKill();
      }
    }

    // Unexpected perk: enemies simply do not attack during the first five turns.
    const noAttackWindow = (() => {
      const cls = S.classId ? CLASS_BY_ID[S.classId] : null;
      return !!(cls && cls.perk === 'Unexpected') && isEarlyGame();
    })();

    let hpDamageThisPhase = 0;
    let poisonDamageThisPhase = 0;
    living.forEach(cell => {
      if (noAttackWindow) return;
      const s = cell.skull;
      if (s.hp <= 0) return;   // already dead by burn
      if (s.frozenTurn === S.turn) { s.frozenTurn = 0; return; }  // Freezing: не атакует в ход заморозки
      if (s.stunned) { s.stunned = false; return; }  // Orc Stun: doesn't attack this turn
      // Counterattack: each living skull hits itself instead of the hero.
      if (S.counterattack > 0) {
        const idx = S.board.indexOf(cell);
        if (idx >= 0) hitSkullAt(idx, Math.max(1, s.atk - (S.blunt || 0)), { ignoreDefence: true });
        return;
      }
      const atk = Math.max(0, s.atk - S.blunt);
      if (s.type === 9) {
        const poisonHit = damageHero(atk, s.boss, true);
        hpDamageThisPhase += poisonHit;
        if (poisonHit > 0) poisonDamageThisPhase += atk;
      } else {
        hpDamageThisPhase += damageHero(atk, s.boss);
      }
      // Spikes: reflect damage to attacker
      if (S.spikes > 0 && s.hp > 0) {
        s.hp -= S.spikes;
        if (s.hp <= 0) {
          const idx = S.board.indexOf(cell);
          if (idx >= 0) S.board[idx] = { type: 'empty' };
          S.skullsKilled++;
          registerSkullEnd(s);
          awardKillXp(s);
        }
      }
    });

    if (poisonDamageThisPhase > 0) S.poison = Math.max(S.poison || 0, poisonDamageThisPhase);

    // ProcessVampires: ordinary Vampires only heal back up to their own max HP;
    // the mortal enemy (Werewolf) may exceed it and raises its max.
    if (hpDamageThisPhase > 0) {
      const mortalVampire = clsRace() === 'Werewolf';
      S.board.forEach(c => {
        if (!c || c.type !== 'skull' || !c.skull || c.skull.type !== 20 || c.skull.hp <= 0) return;
        const healed = c.skull.hp + enemyGetAttack(c.skull);
        if (mortalVampire) { c.skull.hp = healed; c.skull.maxHp = Math.max(healed, c.skull.maxHp || 0); }
        else c.skull.hp = Math.min(c.skull.maxHp || healed, healed);
      });
    }

    // Last Stand (Paladin): при смерти этого хода — полное лечение.
    // Если герой всё равно мёртв, реген НЕ должен его воскрешать.
    if (enemyPhaseDeathCheck()) return;

    // regenerate (Priest perk Faithful: double effect)
    if (S.regen > 0) {
      const cls = S.classId ? CLASS_BY_ID[S.classId] : null;
      heal(cls && cls.perk === 'Faithful' ? S.regen * 2 : S.regen);
    }

    // Cripple (Werewolf/Assassin): at most one old ordinary skull under armor dies per turn.
    if (S.classId && CLASS_BY_ID[S.classId].race === 'Werewolf') {
      // Exclude this-turn spawns, special skulls, and bosses; choose the weakest eligible target.
      let targetIndex = -1;
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (c && c.type === 'skull' && !c.skull.special && !c.skull.boss
            && (c.skull.enteredTurn || 0) < S.turn && c.skull.hp > 0 && c.skull.hp < S.hero.armor
            && (targetIndex < 0 || c.skull.hp < S.board[targetIndex].skull.hp)) targetIndex = i;
      }
      if (targetIndex >= 0) {
        const skull = S.board[targetIndex].skull;
        S.board[targetIndex] = { type: 'empty' };
        S.skullsKilled++;
        registerSkullEnd(skull);
        awardKillXp(skull);
        audioKill();
        const message = `🐺 Калечение Вервольфа: уничтожен 1 старый обычный череп с минимальным HP (${skull.hp} < броня ${S.hero.armor}); максимум один за ход.`;
        log(message);
        flash(message);
      }
    }

    S.turn++;
    // Sprint: survive 100 turns and win at the end of turn 100.
    if (S.mode === 'sprint' && S.turn >= 100) {
      S.won = true;
      S.over = true;
      openModal('gameover');
      return;
    }

    // Expire freezes created on the previous enemy phase before activating this turn's abilities.
    for (let j = 0; j < CELL_COUNT; j++) {
      const fc = S.board[j];
      if (fc && fc.frozen) fc.frozen = false;
      if (fc && fc.type === 'skull' && fc.skull) fc.skull.frozenTurn = 0;
    }

    // Promotion countdown (EndOfTurn, before abilities). It only reaches 0;
    // the next generated skull is the one that gets promoted.
    if (S.promotionRemaining > 0) S.promotionRemaining--;
    // Mage blocks natural skill recovery (EndOfTurn ordering).
    const magePresent = S.board.some(c => c && c.type === 'skull' && c.skull && c.skull.type === 15);
    if (!magePresent) {
      for (const id of Object.keys(S.skillCooldowns)) S.skillCooldowns[id] = Math.max(0, S.skillCooldowns[id] - 1);
    }

    // --- особые способности врагов (end-of-turn) ---
    applySpecialAbilities();
    lastStandTriggerIfNeeded();
    if (S.freezeNextTurn > 0) S.freezeNextTurn = 0;
    if (S.stealActive > 0) S.stealActive--;
    if (S.berserkActive > 0) S.berserkActive = 0;
    if (S.lastStand > 0) S.lastStand = 0;
    if (S.runeActive > 0) S.runeActive = 0;
    if (S.runeUsedThisTurn > 0) S.runeUsedThisTurn = 0;
    // Skill flags live through this turn only (EndOfTurn rotates current/next).
    S.swordBoost = S.shieldBoost = S.potionBoost = S.coinBoost = 0;
    S.counterattack = 0;
    S.oneHealthSkulls = S.forceCoins = 0;
    S.slashCombo = S.hackCombo = S.shockCombo = S.aweCombo = 0;

    // убийство черепа в enemy phase оставило пустое место — уплотняем
    // доску и снова наполняем пустые клетки, чтобы пустое место не торчало
    // посреди поля
    compactBoardAndRefill();

    if (!hasLegalChain(S.board)) {
      ensureChainable();
      log('♻ Нет ходов — поле перемешано');
    }

    renderBoard();
    renderHUD();

    if (S.hero.hp <= 0) {
      gameOver();
      return;
    }

    // check level-up
    checkLevelUp();
    // check gold box
    checkGoldBox();
    // check upgrade bar
    checkUpgradeBar();
    // check trophy (~15 убитых спец-черепов → трофей)
    checkTrophy();
    S.resolving = false;
  }

  function damageHero(amount, isBoss, bypassArmor) {
    if (amount <= 0 || S.freezeNextTurn > 0) return 0;
    if (bypassArmor) {
      S.hero.hp = Math.max(0, S.hero.hp - amount);
      floatOn('-' + Math.round(amount), 'hurt');
      audioEnemy();
      return amount;
    }
    const armorHit = Math.min(S.hero.armor, amount);
    const defilerPresent = S.board.some(c => c && c.type === 'skull' && c.skull && c.skull.type === 29);
    // Стойкость — вероятность сохранить КАЖДЫЙ пункт брони (см. BASE_DURABILITY),
    // а не доля с округлением: иначе при 50% атака в 1 всегда снимает броню.
    let loss = 0;
    if (defilerPresent) {
      // Defiler short-circuits durability: every blocked point loses one armor.
      loss = armorHit;
    } else {
      for (let k = 0; k < armorHit; k++) {
        if (!chance(S.durability)) loss++;
      }
    }
    S.hero.armor = Math.max(0, S.hero.armor - loss);
    const overflow = amount - armorHit;
    if (overflow > 0) {
      S.hero.hp = Math.max(0, S.hero.hp - overflow);
      floatOn('-' + Math.round(overflow), 'hurt');
    }
    audioEnemy();
    return overflow;
  }

  // Last Stand (Paladin, классный уникальный навык): если герой погиб — полное лечение.
  // Вызывается всегда после урона в бою, если Hero.hp <= 0.
  function lastStandTriggerIfNeeded() {
    if (S.lastStand > 0 && S.hero.hp <= 0) {
      S.hero.hp = S.hero.maxHp;
      S.lastStand = 0;
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
      body.innerHTML = '<p class="modal-hint">Выбери 1 из 4 снаряжения</p>';
      renderEquipChoices(body);
    } else if (kind === 'armor') {
      title.textContent = '🛡 АПГРЕЙД!';
      body.innerHTML = '<p class="modal-hint">Выбери 1 из 4 улучшений</p>';
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
    } else if (kind === 'trophy') {
      title.textContent = '🏆 ТРОФЕЙ!';
      const t = S.pendingTrophy;
      body.innerHTML = '';
      const hint = document.createElement('p');
      hint.className = 'modal-hint';
      hint.textContent = t ? t.text : 'Трофей за убитых спец-черепов.';
      body.appendChild(hint);
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn';
      btn.textContent = '🏆 Забрать трофей';
      btn.addEventListener('click', () => claimTrophy());
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
      checkLevelUp(); checkGoldBox(); checkUpgradeBar(); checkTrophy();
    }, 80);
  }

  // --- особые черепа: особенности по тапу на спец/босс ---
  // Спец-череп — элитность ×2/×2/×4 (оригинал); босс — ×3/×3/×6.
  // Убив спец-черепа, игрок получает трофей (~15 → трофей).
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

  // --- трофей: 20..30 убитых спец-черепов → трофей ---
  // Не более 3 классов разблокируется за забег (далее трофей — в уровень текущего класса).
  // Original ClaimTrophy @0x437b0: 50% the current class levels up (unless it
  // is the Adventurer or already level 10); otherwise a random locked class is
  // unlocked, falling back to leveling the current class.
  function computeTrophyOutcome() {
    const currentId = S.classId;
    const currentLvl = classLevelOf(currentId);
    const maxLvl = 10;
    if (currentId !== 'adventurer' && currentLvl < maxLvl && randInt(0, 1, 5) !== 0) {
      return levelOutcome(currentId, currentLvl);
    }
    const locked = CLASSES.filter(c => !isClassUnlocked(c.id));
    if (locked.length > 0) {
      const cls = locked[randInt(0, locked.length - 1, 5)];
      return { kind: 'unlock', clsId: cls.id, text: '✨ Класс «' + cls.name + '» разблокирован! (ур. 1)' };
    }
    if (currentLvl < maxLvl) return levelOutcome(currentId, currentLvl);
    const other = CLASSES.find(c => c.id !== currentId && classLevelOf(c.id) < maxLvl);
    if (other) return levelOutcome(other.id, classLevelOf(other.id));
    const clsName = CLASS_BY_ID[currentId] ? CLASS_BY_ID[currentId].name : currentId;
    return { kind: 'levelup', clsId: currentId, text: '🏆 «' + clsName + '» уже на макс. уровне (10)' };
  }
  function levelOutcome(id, lvl) {
    const cls = CLASS_BY_ID[id];
    const name = cls ? cls.name : id;
    const next = Math.min(10, (lvl || 1) + 1);
    return { kind: 'levelup', clsId: id, text: '⬆ Класс «' + name + '» поднялся на ур. ' + next + '!' };
  }
  function checkTrophy() {
    if (S.over || S.pendingModal) return;
    if (S.difficulty === 0) return;   // Easy never drops trophies
    if (S.pendingTrophy) return;
    const need = S.trophyRemaining || TROPHY_KILLS_PER_TROPHY;
    if (S.specialsKilled < need) return;
    S.specialsKilled = 0;
    S.trophyRemaining = randInt(20, 30, 5);
    let outcome = computeTrophyOutcome();
    // Лимит разблокировок за забег не должен полностью отключать трофеи:
    // после него падает только шанс нового класса, а уровень класса растёт.
    if (S.unlocksThisRun >= MAX_UNLOCKS_PER_RUN && outcome.kind === 'unlock') {
      const fallback = levelOutcome(S.classId, classLevelOf(S.classId));
      fallback.text += ' (лимит разблокировок)';
      outcome = fallback;
    }
    S.pendingTrophy = outcome;
    openModal('trophy');
  }
  function claimTrophy() {
    const t = S.pendingTrophy;
    if (!t) return;
    S.pendingTrophy = null;
    S.trophiesThisRun = (S.trophiesThisRun || 0) + 1;
    if (t.kind === 'unlock') {
      unlockClass(t.clsId);
      S.unlocksThisRun = (S.unlocksThisRun || 0) + 1;
    } else {
      levelUpClass(t.clsId);
    }
    flash(t.text);
    log(t.text);
    closeModal();
  }

  function chooseLevelOptions() {
    // classic: 2 stats + 2 skills (2-of-4). If 4 skills are taken, no more skills
    // can be chosen → 4 stats instead.
    // Уникальный навык класса предлагается «по желанию» (не сразу — только при
    // выборе 2-из-4 на уровень, как обычный базальный навык).
    const skillSlotsLeft = Math.max(0, 4 - S.skills.length);
    const statOptions = shuffle([...STATS]).slice(0, 2);
    // Одноразовые навыки после использования не возвращаются в пул.
    const availableSkill = sk => !S.skills.includes(sk.id) && !(S.usedSkillIds && S.usedSkillIds[sk.id])
      && !(S.mode === 'sprint' && sk.id === 'trap')
      && !(S.mode === 'pretzel' && (sk.id === 'teleport' || sk.id === 'chamber'));
    const cls = S.classId ? CLASS_BY_ID[S.classId] : null;
    // Adventurer (class 0 in the original) has no fixed 20-slot vector: its pool
    // is *every unlocked* native skill, so the class skills (31..39) join it once
    // their owning class reaches level 10. Skills 40/41 are native-only and never
    // unlock (they have no wrapper enum in the original).
    const adventurerPool = () => ALL_SKILLS.filter(sk => {
      if (sk.nativeOnly) return false;
      if (sk.native != null && sk.native >= 31) {
        const owner = CLASSES.find(c => c.uniqueSkill === sk.id);
        return !!owner && classLevelOf(owner.id) >= 10;
      }
      return true;
    });
    const classPool = !cls || cls.id === 'adventurer' ? adventurerPool()
      : (cls.pool ? ALL_SKILLS.filter(sk => cls.pool.includes(sk.id)) : ALL_SKILLS);
    const basalOptions = shuffle([...classPool].filter(sk => availableSkill(sk) && (!cls || sk.id !== cls.uniqueSkill)));
    // уникальный навык текущего класса: не базовый (Teleport у Adventurer уже в BASAL)
    const uniqueOption = cls && cls.uniqueSkill
      ? UNIQUE_SKILLS.find(s => s.id === cls.uniqueSkill)
      : null;
    let skillOptions = [];
    if (skillSlotsLeft <= 0 && S.mode !== 'sprint') {
      // All four slots taken: offer upgrades of existing skills (levels 1..9).
      const up = S.skills.filter(id => id !== 'wish' && ((S.skillLevels && S.skillLevels[id]) || 1) < 10)
        .map(id => ({ upgrade: true, id, skill: SKILL_BY_ID[id] }));
      skillOptions = shuffle(up).slice(0, 2);
    } else {
      if (uniqueOption && availableSkill(uniqueOption)) {
        skillOptions.push(uniqueOption);
      }
      while (skillOptions.length < 2 && skillOptions.length < skillSlotsLeft && basalOptions.length > 0) {
        const b = basalOptions.shift();
        skillOptions.push(b);
      }
      skillOptions = skillOptions.slice(0, Math.min(2, skillSlotsLeft));
    }
    // fill remaining slots with stats to reach 4 options
    while (statOptions.length + skillOptions.length < 4) {
      const extra = shuffle([...STATS].filter(s => !statOptions.includes(s)))[0];
      statOptions.push(extra);
    }
    return [...statOptions, ...skillOptions];
  }

  function renderLevelChoices(options, body) {
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
        btn.innerHTML = `<span class="lv-icon-box"><span class="lv-icon">${opt.icon}</span><span class="lv-corner">1</span></span>
          <span class="lv-body">
            <span class="lv-row"><span class="lv-name">${opt.name}</span><span class="lv-cool">Кулдаун: ${opt.cooldown}</span></span>
            <span class="lv-desc">${opt.desc}</span>
          </span>`;
      }
      btn.addEventListener('click', () => {
        if (picked.has(i)) {
          picked.delete(i);
          mark();
          return;
        } else {
          picked.add(i);
          audioPick();
          mark();
          if (picked.size === 2) {
            // apply now
            const chosenSkills = [];
            picked.forEach(idx => {
              const o = options[idx];
              if (STATS.includes(o)) o.apply();
              else if (o.upgrade) {
                const id = o.id;
                S.skillLevels = S.skillLevels || {};
                const lvl = Math.min(10, (S.skillLevels[id] || 1) + 1);
                S.skillLevels[id] = lvl;
                const base = SKILL_BY_ID[id] ? SKILL_BY_ID[id].cooldown : 10;
                const adjust = (S.cooldownAdjust && S.cooldownAdjust[id]) || 0;
                S.skillCooldowns[id] = Math.max(0, base - (lvl - 1) - S.quicken + adjust);
                flash('⬆ Навык «' + (SKILL_BY_ID[id] ? SKILL_BY_ID[id].name : id) + '» → ур. ' + lvl);
              }
              else if (SKILLS.includes(o) || UNIQUE_SKILLS.includes(o)) {
                if (S.skills.length >= 4) return;
                chosenSkills.push(o.id);
                S.skills.push(o.id);
                if (S.mode === 'sprint') {
                  S.skillLevels[o.id] = 5;
                  S.skillCooldowns[o.id] = Math.max(0, o.cooldown - 4);
                }
              }
            });
            // Cunning perk (Assassin): the fourth skill costs one turn less — both
            // now and on every later activation.
            if (cls && cls.perk === 'Cunning' && chosenSkills.length > 0 && S.skills.length >= 4) {
              const sk = chosenSkills[chosenSkills.length - 1];
              S.cooldownAdjust = S.cooldownAdjust || {};
              S.cooldownAdjust[sk] = (S.cooldownAdjust[sk] || 0) - 1;
              S.skillCooldowns[sk] = Math.max(0, (S.skillCooldowns[sk] || 0) - 1);
              flash('🗡 Хитрость: кд навыка «' + (SKILL_BY_ID[sk] ? SKILL_BY_ID[sk].name : sk) + '» −1');
            }
            // Faun Rejuvenate (Priest race): HP ≥ 25% max
            if (clsRace() === 'Faun') {
              const need = Math.ceil(S.hero.maxHp * 0.25);
              if (S.hero.hp < need) {
                S.hero.hp = need;
                flash('🦌 Возрождение: HP до ' + need + ' (25%)');
              }
            }
            S.hero.level++;
            S.maxLevel = Math.max(S.maxLevel, S.hero.level);
            S.hero.xp = 0;
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

  function renderEquipChoices(body) {
    // Original shop: 3 of the 5 equipment slots, one generated item each,
    // the fourth card is empty.
    const options = shuffle([...ITEM_SLOTS]).slice(0, 3).map(sl => makeItem(sl.id));
    const summaryBtn = document.createElement('button');
    summaryBtn.type = 'button';
    summaryBtn.className = 'btn secondary shop-summary-toggle';
    summaryBtn.textContent = '📊 Итог персонажа';
    const grid = document.createElement('div');
    grid.className = 'shop-grid';
    const summary = document.createElement('div');
    summary.className = 'character-summary';
    summary.hidden = true;
    renderCharacterSummary(summary);
    summaryBtn.addEventListener('click', () => {
      const show = summary.hidden;
      summary.hidden = !show;
      grid.hidden = show;
      summaryBtn.textContent = show ? '↩ К снаряжению' : '📊 Итог персонажа';
    });
    body.appendChild(summaryBtn);
    options.forEach(item => {
      const equipped = S.equipment.find(e => e.slot === item.slot);
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'shop-item';
      btn.innerHTML = `<span class="shop-icon">${item.icon}</span><span class="shop-body"><span class="shop-slot">${item.slotName}</span><span class="shop-name">${item.name}</span><span class="shop-eff">${itemModsText(item)}</span><span class="shop-preview">Заменяет: ${equipped ? equipped.name : '—'}</span></span>`;
      btn.addEventListener('click', () => {
        equipItem(item);
        S.hero.gold = 0;
        closeModal();
        renderBoard();
        renderHUD();
        audioLevel();
      });
      grid.appendChild(btn);
    });
    // Fourth slot: upgrade scroll (25%; Sauren Search = 50%), otherwise empty.
    const scrollChance = clsRace() === 'Sauren' ? 0.5 : 0.25;
    if (random(5) < scrollChance) {
      const slot = ITEM_SLOTS[randInt(0, ITEM_SLOTS.length - 1, 5)];
      const key = slot.pool[randInt(0, slot.pool.length - 1, 5)];
      const def = ITEM_STAT[key];
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'shop-item';
      btn.innerHTML = `<span class="shop-icon">📜</span><span class="shop-body"><span class="shop-slot">Свиток</span><span class="shop-name">Свиток ${slot.name.toLowerCase()}</span><span class="shop-eff">${itemModText(key, def.step)}</span><span class="shop-preview">Улучшает надетый предмет</span></span>`;
      btn.addEventListener('click', () => {
        const equipped = S.equipment.find(e => e.slot === slot.id);
        if (equipped) {
          equipped.mods = equipped.mods || {};
          equipped.mods[key] = +(equipped.mods[key] || 0) + def.step;
        }
        applyItemMod(key, def.step, 1);
        S.hero.gold = 0;
        closeModal();
        renderBoard();
        renderHUD();
        audioLevel();
      });
      grid.appendChild(btn);
    } else {
      const empty = document.createElement('div');
      empty.className = 'shop-item shop-empty';
      empty.setAttribute('aria-hidden', 'true');
      grid.appendChild(empty);
    }
    body.appendChild(grid);
    body.appendChild(summary);
  }

  function renderUpgradeChoices(body) {
    // Original upgrade menu: 4 of the 5 slots, each offers one random
    // stat upgrade for the item equipped in that slot.
    const options = shuffle([...ITEM_SLOTS]).slice(0, 4).map(sl => makeUpgradeOffer(sl.id));
    const grid = document.createElement('div');
    grid.className = 'lv-grid';
    options.forEach(opt => {
      const item = S.equipment.find(e => e.slot === opt.slot);
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'lv-card lv-stat';
      btn.innerHTML = `<span class="lv-chip" style="color:#d9a8ff">${opt.icon}</span>
        <span class="lv-body">
          <span class="lv-name">${opt.slotName}</span>
          <span class="lv-desc">${item ? item.name : '—'}</span>
          <span class="lv-effs"><span class="lv-line">${opt.label}</span></span>
        </span>`;
      btn.addEventListener('click', () => {
        const equipped = S.equipment.find(e => e.slot === opt.slot);
        if (equipped) {
          equipped.mods = equipped.mods || {};
          equipped.mods[opt.key] = +(equipped.mods[opt.key] || 0) + opt.value;
        }
        applyItemMod(opt.key, opt.value, 1);
        S.upgradeBar = 0;
        closeModal();
        renderBoard();
        renderHUD();
        audioLevel();
      });
      grid.appendChild(btn);
    });
    body.appendChild(grid);
  }

  function checkLevelUp() {
    if (S.pendingModal || S.over) return;
    if (S.hero.xp >= S.hero.xpNext) openModal('level');
  }
  function checkGoldBox() {
    if (S.pendingModal || S.over) return;
    if (S.hero.gold >= GOLD_BOX_MAX) openModal('gold');
  }
  function checkUpgradeBar() {
    if (S.pendingModal || S.over) return;
    if (S.upgradeBar >= UPGRADE_BAR_MAX) openModal('armor');
  }

  function gameOver() {
    if (S.over) return;
    S.over = true;
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
  // Взрыв Ка-Ми-Казе: убивает 8 не-черепах вокруг, игрок теряет пол-HP (min 1).
  // idx — клетка калибуса (может быть уже пуста в kill-ветке).
  function explodeKamikazeCell(idx, hurtHero) {
    if (idx == null || idx < 0) return;
    const col = idx % COLS, row = Math.floor(idx / COLS);
    for (let dr = -1; dr <= 1; dr++) for (let dc = -1; dc <= 1; dc++) {
      if (dr === 0 && dc === 0) continue;
      const r = row + dr, c = col + dc;
      if (r < 0 || r >= ROWS || c < 0 || c >= COLS) continue;
      const ni = r * COLS + c;
      const nc = S.board[ni];
      if (!nc || nc.type === 'empty') continue;
      if (nc.type !== 'skull') S.board[ni] = { type: 'empty' };
    }
    const cell = S.board[idx];
    if (cell && cell.type === 'skull') S.board[idx] = { type: 'empty' };
    if (hurtHero) {
      const hpBefore = S.hero.hp;
      S.hero.hp = Math.max(1, Math.floor(S.hero.hp / 2));
      if (S.hero.hp < hpBefore) flash('💠 КА-МИ-КАЗЕ: пол-HP → ' + S.hero.hp);
    }
    audioKill();
  }

  // Нестабильный (19): очищает доску, не-черепы возвращаются новыми
  function createAlchemistTile() {
    if (randInt(0, 4) === 4) {
      const created = makeSkull(false, false);
      created.maxHp = created.hp;
      created.enteredTurn = S.turn;
      return { type: 'skull', skull: created };
    }
    return createTile();
  }

  function unstableTeleport(idx) {
    // Сброс доски — обычный набор клеток; раньше мечи/стрелы намеренно
    // исключались, из-за чего на доске не оставалось мечей.
    for (let j = 0; j < CELL_COUNT; j++) {
      S.board[j] = createNewTile();
    }
    flash('🌀 НЕСТАБИЛЬНЫЙ: поле обновлено');
    log('🌀 Нестабильный: поле обновлено');
  }

  function enemyGetAttack(skull) {
    let atk = Math.max(0, (skull && skull.atk || 0) - (S.blunt || 0));
    // Elf Slow Poison: while poisoned, attacks above 1 are halved (floor).
    const cls = S.classId ? CLASS_BY_ID[S.classId] : null;
    if (cls && cls.race === 'Elf' && (S.poison || 0) > 0 && atk > 1) atk = Math.floor(atk / 2);
    return atk;
  }

  // Применение особых способностей спец-черепов (end-of-turn).
  function applySpecialAbilities() {
    // Flame marks suppress duplicate hits only within one activation pass.
    S.board.forEach(c => { if (c && c.type === 'skull' && c.skull) c.skull._flamedThisTurn = false; });
    // Яд тикает в начале enemyPhase (см. enemyPhase), чтобы не бить дважды
    // в ход, когда Poisonous только что наложил его.
    const abilityDone = new Set();
    for (let i = 0; i < CELL_COUNT; i++) {
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
          if (p && p.type === 'potion' && !p.mana && !p.acid) potions.push(j);
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
            if (!nc.skull._flamedThisTurn) {
              nc.skull._flamedThisTurn = true;
              // Original IgniteTiles: the target simply starts burning with the
              // Flaming skull's attack as fire damage (no immediate HP loss).
              nc.skull.burn = true;
              nc.skull.fireDamage = (nc.skull.fireDamage || 0) + enemyGetAttack(s);
            }
          } else {
            nc.burn = (typeof nc.burn === 'number' ? nc.burn : 0) + s.atk;
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
          const before = S.hero.gold;
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
            S.skullsKilled++;
            registerSkullEnd(tj.skull);
            log('🌪️ Хаотический съел череп');
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
          unstableTeleport(i);
          S.killsThisTurn = 0;
          return;
        }
      } else if (t === 21) { // Vengeful — атака += killsThisTurn
        if (S.killsThisTurn > 0) {
          s.atk += S.killsThisTurn;
          log('😡 Мстительный: +' + S.killsThisTurn + ' атака');
        }
      } else if (t === 22) { // Trampling — ломает щит/меч
        const candidates = [];
        for (let j = 0; j < CELL_COUNT; j++) {
          const tj = S.board[j];
          if (!tj) continue;
          if (tj.type === 'shield' && !tj.broken) candidates.push(j);
          else if (tj.type === 'sword' && !tj.broken) candidates.push(j);
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
          const owned = (S.equipment || []).filter(it => it && it.mods && Object.keys(it.mods).length > 0);
          if (owned.length > 0) {
            const item = owned[randInt(0, owned.length - 1, 5)];
            const keys = Object.keys(item.mods);
            const key = keys[randInt(0, keys.length - 1, 5)];
            const step = (ITEM_STAT[key] && ITEM_STAT[key].step) || 1;
            const dec = Math.min(step, item.mods[key]);
            item.mods[key] = +(item.mods[key] - dec).toFixed(2);
            if (item.mods[key] <= 0) delete item.mods[key];
            applyItemMod(key, dec, -1);
            const def = ITEM_STAT[key];
            const label = def ? def.name : key;
            flash('🦷 Съедобный: −' + (dec >= 1 ? dec : Math.round(dec * 100) + '%') + ' «' + label + '» (' + item.name + ')');
            log('🦷 Съедобный уничтожил улучшение: ' + label);
          } else {
            flash('🦷 Съедобный: нет улучшений');
          }
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
          S.poison = 0;
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
        : `<span class="pick-locked">🔒 20–30 спец-черепов → трофей</span>`;
      btn.innerHTML = `<span class="pick-head"><span class="pick-icon">${cls.icon}</span><span><span class="pick-name">${cls.name}</span><br><span class="pick-race">${cls.raceName || cls.race}</span></span></span>${desc.join('')}${uniqueInfo}${status}`;
      btn.addEventListener('click', () => {
        if (!unlocked) {
          flash('⚠ «' + cls.name + '» заблокирован — убей 20–30 спец-черепов, чтобы получить трофей');
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
    if (statsBtn) statsBtn.addEventListener('click', () => openModal('stats'));
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
    computeTrophyOutcome,
    specialSpawnCount,
    makeSkull,
    showSkullModal,
    placeNewTiles,
    applySpecialAbilities,
    enemyPhase,
    explodeKamikazeCell,
    unstableTeleport,
    applyChainEffects,
    damageHero,
    checkLevelUp,
    checkGoldBox,
    checkUpgradeBar,
    createTile,
    hasLegalChain,
    chainTypeForCell,
    chainAllows,
    isAdjacentArrow,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
