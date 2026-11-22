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
  const START_HP = 60, START_MAX_ARMOR = 15;

  // --- формулы урона (классика) ---
  // Урон цепочки со мечом: (база + сила + рост «Урона» + наследник класса)
  //                         + оружие × (число мечей в цепочке).
  // Пробитие брони (pierce) — вероятность, что атака игнорирует оборону черепа.
  // DPS по черепу = суммарный урон; применяется сначала к обороны, потом к HP.
  // (не путать с бронёй героя, где работает «долговечность»)
  const BASE_DAMAGE = 0;           // базовый урон без мечей (сила/рост Урона — выше основного)
  const HP_PER_SHIELD_ARMOR = 1;   // базовая броня за щит (+ точность + 2 × защита)
  const SHIELD_UPGRADE_POINTS = 8; // очки upgrade bar за каждый щит (калибровка клона)
  const HEAL_PER_POTION = 1;       // базовая лечение за зелье (+ удача)
  const GOLD_PER_COIN = 2;         // базовая золото за монетку (+ boost gold)
  const XP_PER_SKULL = 1;          // опыт за убитого черепа (+ boost XP)
  const XP_PER_BOSS = 6;           // опыт за убитого босса

  // Bonus resource (классика): шанс каждого «лишнего» типа сверх первых трёх
  // в цепочке; база 20% + 5% за соответствующий стат + 5% за харизматику
  // + классные бонусы (Raider: 100% на 10+ клеток).
  const BONUS_BASE_CHANCE = 0.20;
  const BASE_PIERCING = 0.50;      // вероятность пробития брони черепа (классика)
  const BASE_DURABILITY = 0.50;    // вероятность, что пункт брони героя не списывается
  const MAX_DURABILITY = 0.90;     // потолок долговечности с апгрейдами

  const SPAWN_EVERY_TURNS = 3;
  const BOSS_EVERY_TURNS = 10;
  // Классика: трофей за ~15 убитых спец-черепов; не более 3 классов unlock за забег
  const TROPHY_KILLS_PER_TROPHY = 15;
  const MAX_UNLOCKS_PER_RUN = 3;
  const SPECIAL_EVERY_TURNS = 4;
  const UPGRADE_BAR_MAX = 80;     // синяя полоса (от щитов) → выбор 1-из-4
  const GOLD_BOX_MAX = 100;       // золотой ящик → 1-из-4 снаряжение

  // Черепа (классика: стартовые значения рядового монстра — 1⚔️·0🛡·4❤️
  // или 1⚔️·1🛡·4❤️; рост каждый ход)
  const SKULL_BASE = { hp: 4, def: 0, atk: 1 };
  const SKULL_GROWTH = { hp: 0.55, def: 0.3, atk: 0.4 };
  const SKULL_START_FREE = 3;     // первые 3 хода — база без роста
  // Босс: «черепу — гораздо сильнее обычного» (владелец: ×1.8/×1.5/×2.2,
  // было ×2.0/×2.0/×3.0 — избыточно слабый клон; пересборка в 4710f0b затирала ×1.8)
  const BOSS_STATS = { atk: 1.8, def: 1.5, hp: 2.2 };

  const SKILLS = [
    { id: 'teleport', name: 'Телепорт', icon: '✦', cooldown: 20, desc: 'Все клетки без черепов → новые' },
    { id: 'freeze', name: 'Мороз', icon: '❄', cooldown: 16, desc: 'Черепа не бьют этим ходом' },
    { id: 'heal', name: 'Благословение', icon: '❤', cooldown: 15, desc: 'Собрать все зелья' },
    { id: 'repair', name: 'Ремонт', icon: '⚒', cooldown: 16, desc: 'Собрать все щиты' },
    { id: 'dazzle', name: 'Слепота', icon: '✦', cooldown: 15, desc: 'Все монеты → мечи' },
    { id: 'disarm', name: 'Обезоружание', icon: '⚔', cooldown: 25, desc: 'Все щиты → мечи' },
    { id: 'treasure', name: 'Кладовая', icon: '💎', cooldown: 18, desc: 'Собрать все монеты' },
    { id: 'enchant', name: 'Затачивание', icon: '⚒', cooldown: 35, desc: 'Мгновенно: +2 к оружию, +4 к HP' },
    { id: 'bribe', name: 'Воровство', icon: '🦊', cooldown: 13, desc: 'Атака черепов → золото (1 ход)' },
    { id: 'elixir', name: 'Эликсир', icon: '⚗', cooldown: 18, desc: 'Все зелья дают 2 XP вместо HP' },
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
      apply: () => { S.hero.stats.vitality++; S.hero.hp += 5; S.hero.maxHp += 5; }
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
      id: 'charisma', name: 'Хитрость', icon: '💎', chip: '+ХИТ', color: '#8fd0ff',
      desc: '+5% ко всем бонусам',
      effects: () => [
        `Шанс бонусной монеты: ${bonusPct('coin')}% >> ${bonusPct('coin') + 5}%`,
        `Шанс бонуса щита: ${bonusPct('shield')}% >> ${bonusPct('shield') + 5}%`,
      ],
      apply: () => S.hero.stats.charisma++
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
      id: 'defense', name: 'Защита', icon: '🛡', chip: '+ЗАЩ', color: '#a8c8ff',
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
    { id: 'quicken', name: 'Ускорение кулдаунов', icon: '⏱', desc: '-1 к кулдауну следующего навыка', apply: () => { S.quicken += 1; } },
    { id: 'regen', name: 'Регенерация', icon: '♻', desc: '+1 HP в конце хода', apply: () => { S.regen += 1; } },
    { id: 'repair', name: 'Ремонт брони', icon: '⚒', desc: '+1 броня за щит', apply: () => { S.armorPerShield += 1; } },
    { id: 'spikes', name: 'Шипы', icon: '🗡', desc: '+1 урон черепу за каждый бросок', apply: () => { S.spikes += 1; } },
    { id: 'weapon', name: 'Оружие', icon: '⚔', desc: '+1 урон оружия', apply: () => { S.hero.weaponDamage += 1; } },
  ];
  // --- магазин предметов (классика: золото → 1 из 4 предметов) ---
  // 3 предмета + 1 пустой слот (как в оригинале). Эффект: «name + effect»
  const EQUIP = [
    { id: 'necklace', name: 'Ужасное ожерелье', icon: '❤', eff: 'hp', value: 15, desc: '15 HP', apply: () => { S.hero.maxHp += 15; S.hero.hp += 15; } },
    { id: 'battlemace', name: 'Тусклая булава', icon: '⚔', eff: 'dmg', value: 3, desc: '3 ур.', apply: () => { S.hero.weaponDamage += 3; } },
    { id: 'spangenhelm', name: 'Сломанный шлем', icon: '🛡', eff: 'def', value: 2, desc: '2 броня', apply: () => { S.hero.maxArmor += 2; S.hero.armor += 2; } },
    { id: 'amulet', name: 'Храбрый кулон', icon: '💎', eff: 'hp', value: 10, desc: '10 HP', apply: () => { S.hero.maxHp += 10; S.hero.hp += 10; } },
  ];
  // ============================================================
  // КЛАССЫ И РАСЫ (классика: 10 классов, каждый — Perk/Flaw/Race/Уникальный навык)
  // 0 = Аduventurer (стартовый). Остальные — выбираемы в пикeрe (владелец:
  // «полная классика» — классы сразу доступны для выбора, как в классике
  // после 100+ забегов; trophy-механика — нет, см. LUNAPARK.md §7).
  // ============================================================
  const CLASSES = [
    { id: 'adventurer', name: 'Авантюрист', icon: '❔', race: 'Human', uniqueSkill: 'teleport',
      perk: '—', flaw: '—', raceName: 'Человек',
      perkDesc: 'нет пассивов', flawDesc: 'нет изъянов',
      clsDesc: 'Стартовый класс: нет пассива или изъяна, свой навык — Телепорт',
      uniqueSkillDesc: 'Телепорт (12): все клетки без черепов → новые' },
    { id: 'barbarian', name: 'Варвар', icon: '🪓', race: 'Orc', uniqueSkill: 'berserk',
      perk: 'Strong', flaw: 'Reckless', perkName: 'Сила', flawName: 'Безрассудство', raceName: 'Орк',
      perkDesc: '+1 базового урона', flawDesc: '-10% шанс бонусных щитов',
      clsDesc: 'пассив: Сила · изъян: Безрассудство · раса: Орк (оглушение 10%)',
      uniqueSkillDesc: 'Берсерк (14): убийство черепа даёт HP равный его атаке' },
    { id: 'mage', name: 'Маг', icon: '🔮', race: 'Changeling', uniqueSkill: 'rune',
      perk: 'Gifted', flaw: 'Frail', perkName: 'Одарённость', flawName: 'Хрупкость', raceName: 'Изменник',
      perkDesc: '5% зелий выпадают как mana-зелья (−1 к кулдауну навыка)', flawDesc: '-10 макс. HP',
      clsDesc: 'пассив: Одарённость · изъян: Хрупкость · раса: Изменник (менятся)',
      uniqueSkillDesc: 'Руна (12): замкнутая цепочка собирает все клетки внутри' },
    { id: 'ranger', name: 'Разведчик', icon: '🏹', race: 'Elf', uniqueSkill: 'volley',
      perk: 'Agile', flaw: 'Slim', perkName: 'Ловкость', flawName: 'Тощий', raceName: 'Эльф',
      perkDesc: '5% мечей выпадают стрелками (стрелять на 1 клетку)', flawDesc: '-1 броня',
      clsDesc: 'пассив: Ловкость · изъян: Тощий · раса: Эльф (медленный яд)',
      uniqueSkillDesc: 'Воллей (15): все мечи → стрелы (стрелы стреляют на 1 клетку)' },
    { id: 'rogue', name: 'Вор', icon: '🗡', race: 'Sauren', uniqueSkill: 'greed',
      perk: 'Frugal', flaw: 'Clumsy', perkName: 'Бережливость', flawName: 'Неловкость', raceName: 'Саурен',
      perkDesc: '+10% шанс бонусных монет', flawDesc: '3% щитов выпадают сломанными (дают 0)',
      clsDesc: 'пассив: Бережливость · изъян: Неловкость · раса: Саурен (поиск — нет магазина)',
      uniqueSkillDesc: 'Жадность (25): убивает всех черепов рядом с монеткой' },
    { id: 'paladin', name: 'Паладин', icon: '🛡', race: 'Dragonkin', uniqueSkill: 'laststand',
      perk: 'Intimidating', flaw: 'Zealous', perkName: 'Устрашающий', flawName: 'Рьяный', raceName: 'Драконкин',
      perkDesc: 'смертный враг появляется на 50% реже (нет смертных врагов в клоне)', flawDesc: 'черепа-монстры появляются на 1 ход раньше',
      clsDesc: 'пассив: Устрашающий (не реализовано) · изъян: Рьяный · раса: Драконкин (огненное дыхание)',
      uniqueSkillDesc: 'Последний рубеж (35): полное лечение HP, если герой погибнет в этот ход' },
    { id: 'priest', name: 'Жрец', icon: '✨', race: 'Faun', uniqueSkill: 'wish',
      perk: 'Faithful', flaw: 'Cautious', perkName: 'Праведный', flawName: 'Осторожный', raceName: 'Фавн',
      perkDesc: 'двойной эффект регенерации', flawDesc: '-20% пробития брони',
      clsDesc: 'пассив: Праведный · изъян: Осторожный · раса: Фавн (возрождение)',
      uniqueSkillDesc: 'Желание — одноразовый навык: собрать все клетки доски' },
    { id: 'raider', name: 'Разбойник', icon: '🏴', race: 'Halfling', uniqueSkill: 'masochism',
      perk: 'Daring', flaw: 'Cocky', perkName: 'Дерзкий', flawName: 'Наглый', raceName: 'Халфлинг',
      perkDesc: '100% шанс бонусов при цепочке 10+ клеток', flawDesc: 'цепочка из ровно 3 клеток ничего не даёт',
      clsDesc: 'пассив: Дерзкий · изъян: Наглый · раса: Халфлинг (храбрость)',
      uniqueSkillDesc: 'Мазохизм (15): превращает все монетки в черепов' },
    { id: 'warrior', name: 'Воин', icon: '⚔️', race: 'Dwarf', uniqueSkill: 'charge',
      perk: 'Tough', flaw: 'Noisy', perkName: 'Прочный', flawName: 'Громкий', raceName: 'Дворф',
      perkDesc: '+10 макс. HP', flawDesc: 'черепа-монстры +5% HP',
      clsDesc: 'пассив: Прочный · изъян: Громкий · раса: Дворф (верховая атака)',
      uniqueSkillDesc: 'Рывок (18): собирать все клетки по краю поля' },
    { id: 'assassin', name: 'Ассасин', icon: '🗡', race: 'Werewolf', uniqueSkill: 'haste',
      perk: 'Cunning', flaw: 'Sneaky', perkName: 'Хитрый', flawName: 'Незаметный', raceName: 'Вервольф',
      perkDesc: '-1 к кулдауну 4-го навыка', flawDesc: '+1 HP у всех черепов',
      clsDesc: 'пассив: Хитрый · изъян: Незаметный · раса: Вервольф (паралич)',
      uniqueSkillDesc: 'Хействие (16): цепочка может смешивать 2 разных типа меток' },
  ];
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
    { id: 'haste', name: 'Хействие', icon: '🗡', cooldown: 16, desc: 'Цепочка может смешивать 2 разных типа (1 ход)' },
    { id: 'volley', name: 'Воллей', icon: '🏹', cooldown: 15, desc: 'Все мечи → стрелы (стрелы стреляют на 1 клетку)' },
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
      const obj = raw ? JSON.parse(raw) : {};
      if (!obj || typeof obj !== 'object') obj = {};
      obj.adventurer = Math.max(1, obj.adventurer || 1);
      return obj;
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
    else classLevels[id] = (classLevelOf(id) || 1) + 1;
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
  const SKULL_KEYS = ['e1_ghost', 'e2_cult', 'e3_beholder', 'e4_plasma', 'e5_shadow'];
  const BOSS_KEY = 'boss_lich';

  let SKULL_UID = 1;
  const SKULL_IMAGES = new Map(); // uid -> cached <img> (same sprite never re-created)

  let S = null;

  // --- DOM helpers ---------------------------------------------------------
  const el = id => document.getElementById(id);
  function qs(sel, root) { return (root || document).querySelector(sel); }
  function qsa(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

  // --- helpers -------------------------------------------------------------
  function randInt(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }
  function chance(p) { return Math.random() < p; }
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
      hero: {
        hp: START_HP,
        maxHp: START_HP,
        armor: START_MAX_ARMOR,
        maxArmor: START_MAX_ARMOR,
        weaponDamage: 3,
        level: 1,
        xp: 0,
        xpNext: 10,
        gold: 0,
        goldBox: 0,
        stats: { strength: 0, vitality: 0, luck: 0, dexterity: 0, charisma: 0, damage: 0, defense: 0 },
      },
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
      berserkActive: 0,            // лекарство от этого хода (Barbarian perk)
      cooldownAdjust: {},          // skill id -> extra cooldown (−1 для Cunning)
      hasteActive: 0,              // 2 разных типа в цепочке (1 ход)
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
      potionsUsed: 0,
      chainCount: 0,
      maxLevel: 1,
      log: [],
      over: false,
      spawnsDone: 0,
      bossTurn: BOSS_EVERY_TURNS,
      selected: [],
      chainType: null,
      pendingModal: null,         // null | 'level' | 'armor' | 'gold' | 'gameover'
      resolving: false,
      lastKillCount: 0,           // статистика цепочки (для Guts/Volley/Uppercut)
    };
  }

  function randomTileType() {
    const r = Math.random();
    if (r < 0.26) return 'sword';
    if (r < 0.52) return 'shield';
    if (r < 0.76) return 'coin';
    return 'potion';
  }

  // Клетка с возможной сломанностью щита (Rogue Flaw: 3%)
  function createTile() {
    const t = randomTileType();
    // Rogue Clumsy: 3% щитов выпадают сломанными (0 брони/upgrade-очков, 0 бонусов)
    if (t === 'shield' && S && (S.classId ? CLASS_BY_ID[S.classId].flaw === 'Clumsy' : false) && Math.random() < 0.03) {
      S.brokenShields++;
      return { type: 'shield', broken: true };
    }
    // Mage Gifted: 5% зелий выпадают как mana-зелья (+1 к кулдауну навыка)
    if (t === 'potion' && S && (S.classId ? CLASS_BY_ID[S.classId].perk === 'Gifted' : false) && Math.random() < 0.05) {
      return { type: 'potion', mana: true };
    }
    // Ranger Agile: 5% мечей выпадают стрелками (стреляют на 1 клетку)
    if (t === 'sword' && S && (S.classId ? CLASS_BY_ID[S.classId].perk === 'Agile' : false) && Math.random() < 0.05) {
      return { type: 'arrow' };
    }
    return { type: t };
  }

  function makeSkull(level, boss, special) {
    const cls = S && S.classId ? CLASS_BY_ID[S.classId] : null;
    const flawBonusHp = cls && cls.flaw === 'Sneaky' ? 1 : 0;      // Assassin Flaw: +1 HP у всех черепов
    const noisy = (boss || special) && cls && cls.flaw === 'Noisy' ? 1.05 : 1;  // Warrior Flaw: спец-череп +5% HP
    const growthTurns = Math.max(0, S.turn - SKULL_START_FREE);
    // Классика: рядовой монстр на старте — 1⚔️·0🛡·4❤️ или 1⚔️·1🛡·4❤️ (def 0/1)
    const startDef = boss ? 0 : (Math.random() < 0.5 ? 0 : 1);
    return {
      boss: !!boss,
      special: !!special,
      uid: SKULL_UID++,
      art: boss ? BOSS_KEY : SKULL_KEYS[Math.floor(Math.random() * SKULL_KEYS.length)],
      atk: Math.round((SKULL_BASE.atk + SKULL_GROWTH.atk * growthTurns) * (boss ? BOSS_STATS.atk : 1)),
      def: Math.round((startDef + SKULL_GROWTH.def * growthTurns) * (boss ? BOSS_STATS.def : 1)),
      hp: Math.round((SKULL_BASE.hp + SKULL_GROWTH.hp * growthTurns) * (boss ? BOSS_STATS.hp : 1) * noisy) + flawBonusHp,
      maxHp: 0,
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
    // есть ли на доске цепочка из 3+ клеток в одном направлении (прямой/диагональный).
    // стрелы и черепа — категория меча.
    function catOf(cell) {
      if (!cell) return null;
      if (cell.type === 'skull' || cell.type === 'arrow') return 'sword';
      return cell.type;
    }
    for (let r = 0; r < ROWS; r++) {
      for (let c = 0; c < COLS; c++) {
        const startCat = catOf(board[r * COLS + c]);
        if (!startCat || startCat === 'empty') continue;
        for (const [dr, dc] of [[0, 1], [1, 0], [1, 1], [1, -1]]) {
          let cat = startCat;
          const cells = [r * COLS + c];
          let cur = { r, c };
          for (let k = 0; k < 2; k++) {
            const nr = cur.r + dr, nc = cur.c + dc;
            if (nr < 0 || nr >= ROWS || nc < 0 || nc >= COLS) break;
            const ncat = catOf(board[nr * COLS + nc]);
            if (!ncat || ncat === 'empty') break;
            if (!chainAllows(ncat, cat)) break;
            cells.push(nr * COLS + nc);
            cur = { r: nr, c: nc };
          }
          if (cells.length >= 3) return true;
        }
      }
    }
    return false;
  }

  function setupBoard() {
    let cells = null;
    for (let tries = 0; tries < 30; tries++) {
      cells = new Array(CELL_COUNT).fill(null).map(() => createTile());
      const count = START_SKULLS[0];
      // skulls enter from the top of random columns; then gravity compacts rows down
      const cols = shuffle([...Array(COLS).keys()]).slice(0, count);
      cols.forEach((c, i) => {
        const skull = makeSkull(1 + Math.floor(i / 2), false);
        skull.maxHp = skull.hp;
        skull.enteredTurn = -1; // present from the start of the game
        cells[c] = { type: 'skull', skull };
      });
      // compact each column to the bottom (skulls fall with the rest)
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
      const j = Math.floor(Math.random() * (i + 1));
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
  // Классика: трофей за ~15 убитых спец-черепов (см. TROPHY_KILLS_PER_TROPHY).
  function specialSpawnCount() {
    const t = S.turn;
    if (t < 2) return 0;
    if (t % BOSS_EVERY_TURNS === 0) return 0;
    if (t % SPECIAL_EVERY_TURNS !== 0) return 0;
    return 1;
  }

  function createNewTile() {
    // new tiles appear at the top; only non-skull types
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
        const isSpecial = !isBoss && specialCount > 0 && (bossSpawn ? i >= 1 : i >= skullCount);
        const skull = makeSkull(1 + Math.floor(S.turn / 10), isBoss, isSpecial);
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
    return cell.type;
  }
  function chainAllows(cellType, chainType) {
    if (chainType === 'sword') return cellType === 'sword' || cellType === 'arrow' || cellType === 'skull';
    return cellType === chainType;
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

  function distinctNonSkullTypes(chain) {
    const set = new Set();
    for (const idx of chain) {
      const cell = S.board[idx];
      if (!cell) continue;
      if (cell.type === 'skull') continue;
      set.add(cell.type === 'arrow' ? 'sword' : cell.type);
    }
    return set;
  }

  function tryExtendChain(cellIdx) {
    if (S.resolving || S.over) return;
    if (cellIdx < 0 || cellIdx >= CELL_COUNT) return;
    const cell = S.board[cellIdx];
    if (!cell || cell.type === 'empty') return;

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
    // Haste (Assassin): смешивать 2 типа меток в цепочке (череп/стрелы — меч-категория)
    if (S.hasteActive > 0) {
      const types = distinctNonSkullTypes(chain);
      const mapped = cellType === 'skull' || cellType === 'arrow' ? 'sword' : cellType;
      if (mapped !== 'skull' && !types.has(mapped)) {
        if (types.size >= 2) return;  // больше 2 типов — запрет
        types.add(mapped);
      }
      // skull допускается всегда в цепочках (как категория меча)
    } else {
      if (!chainAllows(cellType, S.chainType)) return;
    }

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
    if (cell.type === 'potion') return '<svg viewBox="0 0 24 24" class="ic" aria-hidden="true"><path d="M12 2.5 10 6 8.5 9.5C7.6 11.6 6.8 13 6.8 14.8A5.2 5.2 0 0 0 12 20 5.2 5.2 0 0 0 17.2 14.8C17.2 13 16.4 11.6 15.5 9.5L14 6Z" fill="currentColor"/><path d="M12 2.5 12 6" stroke="#1c1428" stroke-width="1.6"/><circle cx="12" cy="14.6" r="2.4" fill="#fff" opacity="0.5"/></svg>';
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
        : `tile:${cell.type}`;
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
      img.className = 'skull-art' + (boss ? ' boss' : '');
      img.alt = boss ? 'Босс-череп' : 'Череп';
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
      if (img.parentNode !== cellEl) cellEl.appendChild(img);
      // badge: reuse the same element — only the numbers change in place
      let badge = cellEl.querySelector('.skull-badge');
      if (!badge) {
        badge = document.createElement('div');
        cellEl.appendChild(badge);
      }
      badge.className = 'skull-badge' + (boss ? ' boss' : special ? ' special' : '');
      const text = (boss ? '👑 ' : special ? '◆ ' : '') + `${s.atk}·${s.def}·${s.hp}`;
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
    cellEl.className = 'cell tile type-' + cell.type;
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
    return S.hero.stats.strength + 2 * S.hero.stats.damage + (cls && cls.perk === 'Strong' ? 1 : 0) + (S.baseDamageBonus || 0);
  }
  function swordDamage(swordCount) {
    return Math.round(baseDamage() + S.hero.weaponDamage * swordCount);
  }

  function chainPreview() {
    const chain = S.selected;
    if (!chain || chain.length === 0) return null;
    let swordCount = 0, shieldCount = 0, coinCount = 0, potionCount = 0;
    const skulls = [];
    chain.forEach(idx => {
      const cell = S.board[idx];
      if (!cell) return;
      if (cell.type === 'sword' || cell.type === 'arrow') swordCount++;
      else if (cell.type === 'shield') shieldCount++;
      else if (cell.type === 'coin') coinCount++;
      else if (cell.type === 'potion') potionCount++;
      else if (cell.type === 'skull') skulls.push({ idx, skull: cell.skull });
    });
    const first = S.board[chain[0]];
    const chainType = S.chainType || (first ? (first.type === 'skull' || first.type === 'arrow' ? 'sword' : first.type) : null);
    // haste active / mixed chain → preview all effects
    const distinctTypes = new Set();
    chain.forEach(idx => {
      const c = S.board[idx];
      if (!c) return;
      distinctTypes.add(c.type === 'skull' || c.type === 'arrow' ? 'sword' : c.type);
    });
    if (S.hasteActive > 0 && distinctTypes.size > 1) {
      const baseDmg = swordDamage(swordCount);
      const perShield = S.armorPerShield + S.hero.stats.dexterity + S.hero.stats.defense * 2;
      const shieldGain = Math.max(0, shieldCount - 0) * perShield; // broken shields preview: 0 (approx)
      const gold = Math.round(coinCount * S.goldPerCoin * (1 + 0.05 * S.hero.stats.charisma));
      const heal = Math.round(potionCount * (HEAL_PER_POTION + S.hero.stats.luck) * (1 + 0.05 * S.hero.stats.charisma));
      const lethals = skulls.filter(x => baseDmg >= x.skull.def + x.skull.hp);
      return { kind: 'mixed', count: chain.length, damage: baseDmg, swordCount, gain: shieldGain, gold, heal, skulls, lethals };
    }
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
      const charisma = S.hero.stats.charisma;
      const gold = Math.round(coinCount * S.goldPerCoin * (1 + 0.05 * charisma));
      return { kind: 'coin', count: chain.length, gold };
    }
    if (chainType === 'potion') {
      const luck = S.hero.stats.luck;
      const charisma = S.hero.stats.charisma;
      const gain = Math.round(potionCount * (HEAL_PER_POTION + luck) * (1 + 0.05 * charisma));
      return { kind: 'potion', count: chain.length, gain };
    }
    return null;
  }

  function renderChainTip() {
    const tip = el('chain-tip');
    if (!tip) return;
    const p = chainPreview();
    if (!p || p.count < 3) {
      tip.classList.remove('show');
      tip.textContent = '';
      return;
    }
    tip.classList.add('show');
    let effect;
    if (p.kind === 'mixed') {
      const parts = [];
      if (p.damage > 0 && p.swordCount > 0) parts.push(`💥 ${p.damage} урона`);
      if (p.skulls && p.skulls.length > 0) parts.push(`☠ ${p.lethals.length}/${p.skulls.length} убиты`);
      if (p.gain > 0) parts.push(`🛡 +${p.gain} брони`);
      if (p.gold > 0) parts.push(`💰 +${p.gold} золота`);
      if (p.heal > 0) parts.push(`❤ +${p.heal} HP`);
      effect = parts.join(' · ');
    } else if (p.kind === 'sword') {
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
    if (el('trophy-text')) {
      const capped = S.unlocksThisRun >= MAX_UNLOCKS_PER_RUN;
      el('trophy-text').textContent = capped ? '🏆 max' : `🏆 ${Math.min(S.specialsKilled, TROPHY_KILLS_PER_TROPHY)} / ${TROPHY_KILLS_PER_TROPHY}`;
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
    const allSkills = SKILLS.concat(UNIQUE_SKILLS);
    S.skills.forEach(skillId => {
      const skill = allSkills.find(s => s.id === skillId);
      if (!skill) return;
      const left = S.skillCooldowns[skillId] || 0;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'skill' + (left > 0 ? ' on-cd' : '');
      btn.innerHTML = `<span class="skill-icon">${skill.icon}</span><span class="skill-name">${skill.name}</span>${left > 0 ? `<span class="cd">${left}</span>` : ''}`;
      btn.title = skill.desc + ' (кд: ' + skill.cooldown + ')';
      btn.addEventListener('click', () => activateSkill(skillId));
      row.appendChild(btn);
    });
  }

  // --- log ------------------------------------------------------------------
  function log(msg) {
    S.log.unshift(msg);
    S.log = S.log.slice(0, 5);
    const wrap = el('log');
    if (wrap) wrap.innerHTML = S.log.map(m => `<div>${m}</div>`).join('');
  }

  // --- main flow -----------------------------------------------------------
  function newGame() {
    S = newGameState();
    SKULL_UID = 1;
    SKULL_IMAGES.clear();

    // --- class setup: perks/flaws/race (сохраняем класс между перезапусками) ---
    S.classId = getSelectedClassId();
    let cls = CLASS_BY_ID[S.classId];
    if (!cls) {       // класс недоступен (заблокирован) — Adventurer как старт
      S.classId = 'adventurer';
      cls = CLASS_BY_ID.adventurer;
    }

    // Perps классов (fixed at start):
    if (cls.flaw === 'Slim') { S.hero.maxArmor = Math.max(1, S.hero.maxArmor - 1); S.hero.armor = Math.min(S.hero.armor, S.hero.maxArmor); }
    if (cls.perk === 'Tough') { S.hero.maxHp += 10; S.hero.hp += 10; }   // Warrior perk
    if (cls.flaw === 'Frail') { S.hero.maxHp = Math.max(1, S.hero.maxHp - 10); S.hero.hp = Math.min(S.hero.hp, S.hero.maxHp); }  // Mage flaw

    // Priest flaw: -20% pierce
    if (cls.flaw === 'Cautious') S.pierce = Math.max(0, S.pierce * 0.8);

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
    log('✦ Новый забег: череп входит сверху (' + cls.name + ')');
  }

  function isSelectable(cellType) { return cellType !== 'empty'; }

  function activateSkill(id) {
    if (S.over || S.resolving) return;
    const left = S.skillCooldowns[id] || 0;
    if (left > 0) return;
    const skill = SKILL_BY_ID[id];
    if (!skill) return;
    // Quicken (Upgrade): −1 к кулдауну всех навыков
    const adjust = (S.cooldownAdjust && S.cooldownAdjust[id]) || 0;
    S.skillCooldowns[id] = Math.max(id === 'wish' ? 0 : 2, skill.cooldown - S.quicken + adjust);
    // Fire Breath (Dragonkin/Paladin race): ежедневная вспышка — любого навык → случайный черепу вспышка
    fireBreathOnSkill();
    applySkill(id);
    renderHUD();
    renderBoard();
  }

  function fireBreathOnSkill() {
    if (S.classId && CLASS_BY_ID[S.classId].race === 'Dragonkin') {
      const skulls = S.board.filter(c => c && c.type === 'skull').map(c => c.skull);
      if (skulls.length > 0) {
        const target = skulls[Math.floor(Math.random() * skulls.length)];
        target.burn = true;
        flash('🔥 Дыхание дракона: «' + targetArtName(target) + '» подожжен');
      }
    }
  }

  function targetArtName(s) { return s && s.boss ? 'босс' : 'череп'; }

  function applySkill(id) {
    if (id === 'teleport') {
      for (let i = 0; i < CELL_COUNT; i++) {
        const cell = S.board[i];
        if (cell.type !== 'skull') {
          S.board[i] = { type: randomTileType() };
        }
      }
      flash('✦ Все клетки (без черепов) → новые');
    } else if (id === 'freeze') {
      S.freezeNextTurn = 1;
      flash('❄ Черепа не бьют в этом ходу');
    } else if (id === 'heal') {
      let total = 0;
      for (let i = 0; i < CELL_COUNT; i++) {
        if (S.board[i].type === 'potion') {
          total += HEAL_PER_POTION + (S.hero.stats.luck || 0);
          S.board[i] = { type: randomTileType() };
        }
      }
      heal(total);
      flash('❤ Все зелья собраны (+' + total + ' HP)');
    } else if (id === 'repair') {
      let total = 0;
      for (let i = 0; i < CELL_COUNT; i++) {
        if (S.board[i].type === 'shield') {
          total += S.armorPerShield + (S.hero.stats.dexterity || 0) + S.hero.stats.defense * 2;
          S.board[i] = { type: randomTileType() };
        }
      }
      repair(total);
      flash('⚒ Все щиты собраны (+' + total + ' брони)');
    } else if (id === 'dazzle') {
      for (let i = 0; i < CELL_COUNT; i++) {
        if (S.board[i].type === 'coin') S.board[i] = { type: 'sword' };
      }
      flash('✦ Все монеты → мечи');
    } else if (id === 'disarm') {
      for (let i = 0; i < CELL_COUNT; i++) {
        if (S.board[i].type === 'shield') S.board[i] = { type: 'sword' };
      }
      flash('⚔ Все щиты → мечи');
    } else if (id === 'treasure') {
      // Классика: Treasure — собрать все монеты (монеты → золото)
      let collected = 0;
      for (let i = 0; i < CELL_COUNT; i++) {
        const cell = S.board[i];
        if (cell.type === 'coin') {
          collected++;
          S.board[i] = createNewTile();
        }
      }
      const gold = Math.round(collected * S.goldPerCoin * (1 + 0.05 * (S.hero.stats.charisma || 0)));
      if (gold > 0) gainGold(gold);
      flash('💰 Собрано монет: ' + collected + ' (+' + gold + ' золота)');
    } else if (id === 'enchant') {
      S.hero.weaponDamage += 2;
      S.hero.hp = Math.min(S.hero.maxHp, S.hero.hp + 4);
      flash('⚒ Мгновенный улучшение: +2 оружия');
    } else if (id === 'bribe') {
      // Классика: Steal — атака черепов даёт золото в этом ходу
      S.stealActive = 1;
      log('🦊 Воровство: атака → золото (1 ход)');
    } else if (id === 'elixir') {
      let count = 0;
      for (let i = 0; i < CELL_COUNT; i++) {
        if (S.board[i].type === 'potion') { count++; S.board[i] = { type: randomTileType() }; }
      }
      gainXp(count * 2);
      flash('⚗ Все зелья → XP (+' + (count * 2) + ')');
    } else if (id === 'berserk') {
      // Barbarian: Берсерк — убийства этого хода дают HP равный атаку
      S.berserkActive = 1;
      flash('🪓 БЕРЕРСЕК: HP за убийства этого хода');
    } else if (id === 'rune') {
      // Mage: Руна — замкнутая цепочка собирает все клетки внутри (1 ход)
      S.runeActive = 1;
      S.runeUsedThisTurn = 0;
      flash('🔮 РУНА: цепочка замкнётся — тогда соборится всё внутри');
    } else if (id === 'haste') {
      // Assassin: Haste — цепочка смешивает 2 типа меток (этот ход)
      S.hasteActive = 1;
      flash('🗡 Хействие: цепочка — 2 типа меток (этот ход)');
    } else if (id === 'greed') {
      // Rogue: Жадность — убить всех черепов рядом с монеткой (без вознаграждения)
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
            S.lastKillCount = (S.lastKillCount || 0) + 1;
            audioKill();
          }
        }
      });
      flash('🗡 Жадность: убито ' + killed + ' черепов (без награды)');
    } else if (id === 'laststand') {
      // Paladin: Last Stand — полное лечение при смерти в этот ход
      S.lastStand = 1;
      flash('🛡 Last Stand готов (при смерти — полное HP)');
    } else if (id === 'wish') {
      // Priest: Желание — одноразовый навык; собрать все клетки доски
      // (зелья/щиты/монетки → эффекты, черепа — атакуются current sword damage)
      let hpBefore = S.hero.hp, shieldCount = 0, coinCount = 0, potionCount = 0;
      const skullIdxs = [];
      for (let i = 0; i < CELL_COUNT; i++) {
        const cell = S.board[i];
        if (!cell) continue;
        if (cell.type === 'potion') potionCount++;
        else if (cell.type === 'shield' && !cell.broken) shieldCount++;
        else if (cell.type === 'coin') coinCount++;
        else if (cell.type === 'skull') skullIdxs.push(i);
      }
      // эффекты зелий/щитов/монет
      const charisma = S.hero.stats.charisma;
      const gainHeal = Math.round(potionCount * (HEAL_PER_POTION + S.hero.stats.luck) * (1 + 0.05 * charisma));
      heal(gainHeal);
      const perShield = S.armorPerShield + S.hero.stats.dexterity + S.hero.stats.defense * 2;
      const armorGain = shieldCount * perShield;
      const headroom = S.hero.maxArmor - S.hero.armor;
      const armorLocked = Math.min(headroom, armorGain);
      S.hero.armor += armorLocked;
      const overflow = Math.max(0, armorGain - headroom);
      if (overflow > 0) addUpgradeBar(overflow * 2);
      const gold = Math.round(coinCount * S.goldPerCoin * (1 + 0.05 * charisma));
      gainGold(gold);
      S.goldEarned += gold;
      // черепата — атаковать (обычный current-damage)
      const base = baseDamage();
      const weapons = swordCountFrom(S.board) ; // не используется — просто = 1 оружие
      const dmg = Math.round(base + S.hero.weaponDamage);
      skullIdxs.forEach(idx => {
        const s = S.board[idx].skull;
        if (Math.random() < S.pierce) { s.hp -= dmg; }
        else {
          const toDef = Math.min(dmg, s.def);
          s.def -= toDef; s.hp -= (dmg - toDef);
        }
        if (s.hp <= 0) { S.board[idx] = { type: 'empty' }; S.skullsKilled++; registerSkullEnd(s); }
      });
      // очистка собранных клеток (без черепов/пусто)
      S.board = S.board.map(cell => {
        if (!cell) return cell;
        if (cell.type === 'skull') return cell;
        return { type: 'empty' };
      });
      flash('✨ ЖЕЛАНИЕ: собрано всё (+' + gainHeal + ' HP, +' + armorLocked + ' брони, +' + gold + ' золота)');
      // одноразовый навык: удаляем из S.skills
      S.skills = S.skills.filter(sid => sid !== 'wish');
    } else if (id === 'masochism') {
      // Raider: Masochism — все монетки → черепа
      let skullCount = 0;
      for (let i = 0; i < CELL_COUNT; i++) {
        if (S.board[i] && S.board[i].type === 'coin') {
          const skull = makeSkull(1 + Math.floor(S.turn / 10), false);
          skull.maxHp = skull.hp;
          skull.enteredTurn = S.turn;
          S.board[i] = { type: 'skull', skull };
          skullCount++;
        }
      }
      // re-render board once below (caller does renderBoard)
      flash('🏴 Мазохизм: ' + skullCount + ' монетки → черепа');
    } else if (id === 'volley') {
      // Ranger: Volley — все мечи → стрелы (стрелы стреляют на 1 клетку)
      let converted = 0;
      for (let i = 0; i < CELL_COUNT; i++) {
        const cell = S.board[i];
        if (cell && cell.type === 'sword') {
          S.board[i] = { type: 'arrow' };
          converted++;
        }
      }
      flash('🏹 Воллей: ' + converted + ' меч → стрелы');
    } else if (id === 'charge') {
      // Warrior: Charge — собрать все клетки по краю поля
      let swords = 0, shields = 0, coins = 0, potions = 0;
      const skullsInBorder = [];
      for (let i = 0; i < CELL_COUNT; i++) {
        const r = Math.floor(i / COLS), c = i % COLS;
        if (r === 0 || r === ROWS - 1 || c === 0 || c === COLS - 1) {
          const cell = S.board[i];
          if (!cell || cell.type === 'empty') continue;
          if (cell.type === 'sword') swords++;
          else if (cell.type === 'shield' && !cell.broken) shields++;
          else if (cell.type === 'coin') coins++;
          else if (cell.type === 'potion') potions++;
          else if (cell.type === 'skull') skullsInBorder.push(i);
        }
      }
      // sword-attack: skulls in ANY cell (not just border) take charge damage
      const chargeDmg = Math.round(baseDamage() + S.hero.weaponDamage * Math.max(1, swords));
      for (let i = 0; i < CELL_COUNT; i++) {
        const cell = S.board[i];
        if (!cell || cell.type !== 'skull') continue;
        const s = cell.skull;
        if (Math.random() < S.pierce) { s.hp -= chargeDmg; }
        else {
          const toDef = Math.min(chargeDmg, s.def);
          s.def -= toDef; s.hp -= (chargeDmg - toDef);
        }
        if (s.hp <= 0) { S.board[i] = { type: 'empty' }; S.skullsKilled++; registerSkullEnd(s); }
      }
      // resource effects (border only)
      const charisma = S.hero.stats.charisma;
      const gold = Math.round(coins * S.goldPerCoin * (1 + 0.05 * charisma));
      gainGold(gold);
      S.goldEarned += gold;
      const heal = Math.round(potions * (HEAL_PER_POTION + S.hero.stats.luck) * (1 + 0.05 * charisma));
      heal(heal);
      const perShield = S.armorPerShield + S.hero.stats.dexterity + S.hero.stats.defense * 2;
      const armorGain = shields * perShield;
      const headroom2 = S.hero.maxArmor - S.hero.armor;
      const lockArmor = Math.min(headroom2, armorGain);
      S.hero.armor += lockArmor;
      const overflow = Math.max(0, armorGain - headroom2);
      if (overflow > 0) addUpgradeBar(overflow * 2);
      // clear border cells (non-skull)
      S.board = S.board.map((cell, i) => {
        const r = Math.floor(i / COLS), c = i % COLS;
        if (r === 0 || r === ROWS - 1 || c === 0 || c === COLS - 1) {
          if (!cell || cell.type === 'skull') return cell;
          return { type: 'empty' };
        }
        return cell;
      });
      flash('⚔️ Рывок: собрано края (+' + heal + ' HP, +' + lockArmor + ' брони, +' + gold + ' золота)');
    }
  }

  function swordCountFrom(board) {
    let n = 0;
    for (let i = 0; i < board.length; i++) {
      const c = board[i];
      if (c && c.type === 'sword') n++;
    }
    return n;
  }

  // спец-череп (босс или спец-череп): счётчик для трофея
  function registerSkullEnd(s) {
    if (s && (s.boss || s.special)) S.specialsKilled = (S.specialsKilled || 0) + 1;
  }

  function gainXp(amount) {
    S.hero.xp += amount;
    S.hero.xpLevelCum += amount;
    return amount;
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
    S.hero.gold = Math.min(GOLD_BOX_MAX, S.hero.gold + amount);
    S.goldEarned += amount;
  }

  function addUpgradeBar(amount) {
    S.upgradeBar = Math.min(UPGRADE_BAR_MAX, S.upgradeBar + amount);
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
  function applyChainEffects(swordCount, shieldCount, coinCount, potionCount, skullIdxs, raidarPerk, brokenSet) {
    // Без сторонних побочных эффектов от «выборочности» — чистая арифметика.
    // brokenSet (Set) — индексы брошенных (сломанных) щитов; для рун-интерьера — пусто.
    const result = { xpGained: 0, goldGained: 0, armorGained: 0, healGained: 0, dmgShown: 0, kills: 0, bossKills: 0, manaCollected: 0, brokenShields: brokenSet ? brokenSet.size : 0 };
    const cls = S.classId ? CLASS_BY_ID[S.classId] : null;
    const raiderPerk = !!(cls && cls.perk === 'Daring' && raidarPerk);

    // --- swords → skull damage ---
    if (skullIdxs.length > 0) {
      const totalDamage = swordDamage(swordCount);
      const pathSet = S._runePathSet; // цепочка, чтобы проверить uppercut (Dwarf)
      skullIdxs.forEach(idx => {
        const cell = S.board[idx];
        if (!cell || cell.type !== 'skull') return;
        const s = cell.skull;
        let dmg = totalDamage;
        // Dwarf Uppercut: череп атакован, и путь проходит РОВНО под черепом (та клетка — прямо под череп)
        if (cls && cls.race === 'Dwarf' && pathSet && pathSet.has(idx + COLS)) {
          dmg += S.hero.weaponDamage;
        }
        // pierce (пробитие брони): вероятно, весь урон в HP; иначе — в броню, потом в HP
        if (Math.random() < S.pierce) {
          s.hp -= dmg;
        } else {
          const dmgToDef = Math.min(dmg, s.def);
          s.def -= dmgToDef;
          s.hp -= (dmg - dmgToDef);
        }
        result.dmgShown += dmg;
        S.swordsHits += dmg;
        // Orc Stun: 10% шанс замёрзнуть аткующий череп (если выжил)
        if (cls && cls.race === 'Orc' && Math.random() < 0.10 && s.hp > 0) {
          s.stunned = true;
        }
        if (s.hp <= 0) {
          const isBoss = s.boss;
          S.board[idx] = { type: 'empty' };
          S.skullsKilled++;
          registerSkullEnd(s);
          result.kills++;
          if (isBoss) {
            result.bossKills++;
            // Changeling Shapeshift: убить босса → сброс текущего кулдауна навыка
            if (cls && cls.race === 'Changeling') {
              const cdIds = Object.keys(S.skillCooldowns).filter(id => S.skillCooldowns[id] > 0);
              if (cdIds.length > 0) {
                const longest = cdIds.reduce((a, b) => (S.skillCooldowns[a] > S.skillCooldowns[b] ? a : b));
                S.skillCooldowns[longest] = 0;
                flash('🔮 Меняться: кулдаун навыка «' + (SKILL_BY_ID[longest] ? SKILL_BY_ID[longest].name : longest) + '» обнулён');
              }
            }
          }
          const xp = isBoss ? XP_PER_BOSS : (S.xpPerSkull || XP_PER_SKULL);
          result.xpGained += xp;
          if (S.stealActive > 0) result.goldGained += 4;
          audioKill();
          // Barbarian Berserk: HP за убийства этого хода
          if (S.berserkActive > 0) {
            const healAmount = Math.max(0, s.atk);
            const beforeHp = S.hero.hp;
            S.hero.hp = Math.min(S.hero.maxHp, S.hero.hp + healAmount);
            result.healGained += S.hero.hp - beforeHp;
          }
        }
      });
      if (result.dmgShown > 0) floatOn('💥 ' + result.dmgShown, 'hurt');
    }

    // --- shields ---
    if (shieldCount > 0) {
      const perShield = S.armorPerShield + S.hero.stats.dexterity + S.hero.stats.defense * 2;
      const validShields = Math.max(0, shieldCount - result.brokenShields);
      const armorGain = validShields * perShield;

      const before = S.hero.armor;
      S.hero.armor = Math.min(S.hero.maxArmor, S.hero.armor + armorGain);
      result.armorGained = S.hero.armor - before;
      // overflow → upgrade bar (классика)
      const headroom = S.hero.maxArmor - before;
      const overflow = Math.max(0, armorGain - headroom);
      const upgradePoints = validShields * S.upgradePerShield;
      if (overflow > 0) addUpgradeBar(overflow * 2);
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
      const charisma = S.hero.stats.charisma;
      const gold = Math.round(coinCount * S.goldPerCoin * (1 + 0.05 * charisma));
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
            const bonus = Math.round(S.goldPerCoin * (1 + 0.05 * charisma));
            gainGold(bonus);
            result.goldGained += bonus;
          }
        }
      }
    }

    // --- potions ---
    if (potionCount > 0) {
      const luck = S.hero.stats.luck;
      const charisma = S.hero.stats.charisma;
      const healBase = HEAL_PER_POTION + luck;
      // mana potions (Mage Gifted): collected mana potions reduce longest cooldown by 1
      const manaIdxs = brokenSet ? [] : []; // no mana info here — mana handled in resolveChain via caller chain
      let gain = Math.round(potionCount * healBase * (1 + 0.05 * charisma));
      const before = S.hero.hp;
      heal(gain);
      result.healGained = S.hero.hp - before;

      // Bonus potions — каждый лишний зелье сверх первых трёх
      const extra = Math.max(0, potionCount - 3);
      if (extra > 0) {
        let chanceP = BONUS_BASE_CHANCE + 0.05 * S.hero.stats.vitality + 0.05 * S.hero.stats.charisma;
        if (raiderPerk) chanceP = 1.0;
        chanceP = Math.min(1, chanceP);
        for (let k = 0; k < extra; k++) {
          if (chance(chanceP)) {
            const bonus = Math.round(healBase * (1 + 0.05 * charisma));
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

  function resolveChain() {
    if (S.resolving || S.over) return;
    const chain = S.selected.slice();
    if (!chain || chain.length < 3) { clearChain(); return; }
    const cls = S.classId ? CLASS_BY_ID[S.classId] : null;

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
    renderChainLine();
    renderChainTip();

    // categorize
    let swordCount = 0, shieldCount = 0, coinCount = 0, potionCount = 0;
    const skullIdxs = [];
    chain.forEach(idx => {
      const cell = S.board[idx];
      if (!cell || cell.type === 'empty') return;
      if (cell.type === 'sword' || cell.type === 'arrow') swordCount++;
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

    setTimeout(() => {
      // --- основная цепочка ---
      const result = applyChainEffects(swordCount, shieldCount, coinCount, potionCount, skullIdxs, chain.length >= 10, brokenChainSet);

      // mana potions (Mage Gifted): mana potions collected reduce longest cooldowns by 1
      if (cls && cls.perk === 'Gifted' && potionCount > 0) {
        let manaCount = 0;
        chain.forEach(idx => {
          const c = S.board[idx];
          if (c && c.type === 'potion' && c.mana) manaCount++;
        });
        if (manaCount > 0) {
          const cdIds = Object.keys(S.skillCooldowns).filter(id => S.skillCooldowns[id] > 0);
          for (let i = 0; i < Math.min(manaCount, cdIds.length); i++) {
            const longest = cdIds.reduce((a, b) => (S.skillCooldowns[a] > S.skillCooldowns[b] ? a : b));
            S.skillCooldowns[longest] = Math.max(0, S.skillCooldowns[longest] - 1);
            cdIds.splice(cdIds.indexOf(longest), 1);
          }
          flash('📖 Мана: кулдауны −' + manaCount);
        }
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
          if (c.type === 'sword' || c.type === 'arrow') swords++;
          else if (c.type === 'shield') shields++;
          else if (c.type === 'coin') coins++;
          else if (c.type === 'potion') potions++;
          else if (c.type === 'skull') runeSk.push(idx);
        });
        S._runePathSet = runePathSet; // uppercut внутри интерьера применяется к интерьеру
        const res2 = applyChainEffects(swords, shields, coins, potions, runeSk, chain.length >= 10, new Set());
        runeXp += res2.xpGained;
        runeKills = res2.kills;
        // получить эффекты: закрыть все интерьерные клетки
        S.board = S.board.map((cell, i) => runeInterior.includes(i) ? { type: 'empty' } : cell);
        S._runePathSet = new Set(chain);
      }

      // clear chain cells (sword/shield/coin/potion); skulls stay if alive
      const chainSet = new Set(chain);
      S.board = S.board.map((cell, i) => {
        if (!chainSet.has(i) || !cell) return cell;
        if (cell.type === 'skull') return cell;
        return { type: 'empty' };
      });

      // xp + bonus XP (classic): каждый лишний череп сверх трёх убитых этим ходом даёт шанс бонуса
      if (result.xpGained > 0 || runeXp > 0) {
        gainXp(result.xpGained + runeXp);
        if (S.leech > 0) heal(S.leech * (result.dmgShown));
        const totalKills = result.kills + (typeof runeKills === 'number' ? runeKills : 0);
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

      // реген leech за damage dealt уже применено

      // refill: gravity + skulls enter from the top (new enemies cannot
      // attack until the next turn — enteredTurn tag)
      const skullCount = skullSpawnCount();
      const bossSpawn = S.turn > 0 && S.turn % BOSS_EVERY_TURNS === (cls && cls.flaw === 'Zealous' ? 9 : 0);
      const specialCount = specialSpawnCount();
      const spawned = placeNewTiles({ skullCount, boss: bossSpawn, specialCount });
      if (spawned.placed > 0) {
        log(`☠ Появились новые черепа (+${spawned.placed})`);
        if (spawned.bossPlaced) flash('👹 КОНЕЧНЫЙ БОСС');
      }

      renderBoard();
      renderHUD();

      // enemy phase
      setTimeout(() => {
        enemyPhase();
      }, 550);
    }, 420);
  }

  function enemyPhase() {
    // skulls that were on board at the START of this turn and not freshly
    // spawned this turn — only they do damage (enemy phase at the START of the turn)
    const living = S.board.filter(c => c && c.type === 'skull' && (c.skull.enteredTurn || 0) < S.turn);

    // Fire Breath: burned skulls take base damage at the start of the enemy phase (Dragonkin)
    const baseDmg = baseDamage();
    living.forEach(cell => {
      const s = cell.skull;
      if (s.burn) {
        s.burn = false;
        s.hp -= baseDmg;
        if (s.hp <= 0) {
          const idx = S.board.indexOf(cell);
          if (idx >= 0) S.board[idx] = { type: 'empty' };
          S.skullsKilled++;
          registerSkullEnd(s);
          audioKill();
        }
      }
    });

    living.forEach(cell => {
      const s = cell.skull;
      if (s.hp <= 0) return;   // already dead by burn
      if (s.stunned) { s.stunned = false; return; }  // Orc Stun: doesn't attack this turn
      const atk = Math.max(0, s.atk - S.blunt);
      damageHero(atk, s.boss);
      // Spikes: reflect damage to attacker
      if (S.spikes > 0 && s.hp > 0) {
        s.hp -= S.spikes;
        if (s.hp <= 0) {
          const idx = S.board.indexOf(cell);
          if (idx >= 0) S.board[idx] = { type: 'empty' };
          S.skullsKilled++;
          registerSkullEnd(s);
        }
      }
    });

    // Last Stand (Paladin): при смерти этого хода — полное лечение
    if (lastStandTriggerIfNeeded()) {
      // герой выжил — продолжаем ход
    }

    // regenerate (Priest perk Faithful: double effect)
    if (S.regen > 0) {
      const cls = S.classId ? CLASS_BY_ID[S.classId] : null;
      heal(cls && cls.perk === 'Faithful' ? S.regen * 2 : S.regen);
    }

    // Cripple (Werewolf/Assassin): enemies with less HP than armor die at end of turn
    if (S.classId && CLASS_BY_ID[S.classId].race === 'Werewolf') {
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (c && c.type === 'skull' && c.skull.hp > 0 && c.skull.hp < S.hero.armor) {
          S.board[i] = { type: 'empty' };
          S.skullsKilled++;
          registerSkullEnd(c.skull);
          audioKill();
        }
      }
    }

    S.turn++;

    // clear active skill cooldowns
    for (const id of Object.keys(S.skillCooldowns)) {
      S.skillCooldowns[id] = Math.max(0, S.skillCooldowns[id] - 1);
    }
    if (S.freezeNextTurn > 0) S.freezeNextTurn = 0;
    if (S.stealActive > 0) S.stealActive--;
    if (S.berserkActive > 0) S.berserkActive = 0;
    if (S.hasteActive > 0) S.hasteActive = 0;
    if (S.lastStand > 0) S.lastStand = 0;
    if (S.runeActive > 0) S.runeActive = 0;
    if (S.runeUsedThisTurn > 0) S.runeUsedThisTurn = 0;

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

  function damageHero(amount, isBoss) {
    if (amount <= 0) return;
    if (S.freezeNextTurn > 0) {
      return;
    }
    const armorHit = Math.min(S.hero.armor, amount);
    // Durability (классика): каждое пункты брони, поглощённые — списывается с вероятностью (1 - durability)
    const loss = Math.round(armorHit * (1 - S.durability));
    S.hero.armor = Math.max(0, S.hero.armor - loss);
    const overflow = amount - armorHit;
    if (overflow > 0) {
      S.hero.hp -= overflow;
      floatOn('-' + Math.round(overflow), 'hurt');
    }
    audioEnemy();
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
      title.textContent = '☠ ИГРА ОКОНЧЕНА';
      body.innerHTML = `<p class="modal-hint">Герой погиб на ходу <strong>${S.turn}</strong></p>
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
    setTimeout(() => { checkLevelUp(); checkGoldBox(); checkUpgradeBar(); checkTrophy(); }, 80);
  }

  // --- трофей: ~15 спец-черепов → трофей ---
  // Классика: Adventurer — 100% шанс разблокировать любой класс;
  // другой класс — 50% шанс уровня класса / 50% шанс разблокировать новый класс.
  // Не более 3 классов разблокируется за забег (далее трофей — в уровень текущего класса).
  function computeTrophyOutcome() {
    const locked = CLASSES.filter(c => !isClassUnlocked(c.id));
    if (locked.length > 0) {
      const roll = S.classId === 'adventurer' ? 1.0 : 0.5;
      if (Math.random() < roll) {
        const cls = locked[randInt(0, locked.length - 1)];
        return { kind: 'unlock', clsId: cls.id, text: '✨ Класс «' + cls.name + '» разблокирован! (ур. 1)' };
      }
    }
    const currentId = S.classId;
    const clsName = CLASS_BY_ID[currentId] ? CLASS_BY_ID[currentId].name : currentId;
    const nextLvl = (classLevelOf(currentId) || 1) + 1;
    return { kind: 'levelup', clsId: currentId, text: '⬆ Класс «' + clsName + '» поднялся на ур. ' + nextLvl + '!' };
  }
  function checkTrophy() {
    if (S.over || S.pendingModal) return;
    if (S.pendingTrophy) return;
    if (S.unlocksThisRun >= MAX_UNLOCKS_PER_RUN) return;
    if (S.specialsKilled < TROPHY_KILLS_PER_TROPHY) return;
    S.specialsKilled = 0;
    S.pendingTrophy = computeTrophyOutcome();
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
    const basalOptions = shuffle([...SKILLS].filter(sk => !S.skills.includes(sk.id)));
    // уникальный навык текущего класса: не базовый (Teleport у Adventurer уже в BASAL)
    const cls = S.classId ? CLASS_BY_ID[S.classId] : null;
    const uniqueOption = cls && cls.uniqueSkill
      ? UNIQUE_SKILLS.find(s => s.id === cls.uniqueSkill)
      : null;
    let skillOptions = [];
    if (uniqueOption && !S.skills.includes(uniqueOption.id)) {
      skillOptions.push(uniqueOption);
    }
    while (skillOptions.length < 2 && skillOptions.length < skillSlotsLeft && basalOptions.length > 0) {
      const b = basalOptions.shift();
      skillOptions.push(b);
    }
    skillOptions = skillOptions.slice(0, Math.min(2, skillSlotsLeft));
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
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'lv-card ' + (isStat ? 'lv-stat' : 'lv-skill');
      if (isStat) {
        const lines = (opt.effects ? opt.effects() : []).map(e => `<span class="lv-line">${e}</span>`).join('');
        btn.innerHTML = `<span class="lv-chip" style="color:${opt.color}">${opt.chip}</span>
          <span class="lv-body">
            <span class="lv-name">+ ${opt.name}</span>
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
              else if (SKILLS.includes(o) || UNIQUE_SKILLS.includes(o)) {
                if (S.skills.length >= 4) return;
                chosenSkills.push(o.id);
                S.skills.push(o.id);
              }
            });
            // Assassin perk (Cunning): −1 к кулдауну 4-го навыка, выбранного до этого момента
            if (S.classId === 'assassin' && chosenSkills.length > 0) {
              const sk = chosenSkills[chosenSkills.length - 1];
              if (S.skills.length >= 4) {
                S.cooldownAdjust = S.cooldownAdjust || {};
                S.cooldownAdjust[sk] = (S.cooldownAdjust[sk] || 0) - 1;
                flash('🗡 Хитрость: кд навыка «' + (SKILL_BY_ID[sk] ? SKILL_BY_ID[sk].name : sk) + '» −1');
              }
            }
            // Faun Rejuvenate (Priest race): HP ≥ 25% max
            if (S.classId === 'priest') {
              const need = Math.ceil(S.hero.maxHp * 0.25);
              if (S.hero.hp < need) {
                S.hero.hp = need;
                flash('🦌 Возрождение: HP до ' + need + ' (25%)');
              }
            }
            S.hero.level++;
            S.maxLevel = Math.max(S.maxLevel, S.hero.level);
            S.hero.xp = 0;
            S.hero.xpNext = 10 + S.hero.level * 6;
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
    // Магазин (классика): 3 предмета + 1 пустой слот.
    const options = shuffle([...EQUIP]).slice(0, 3);
    const grid = document.createElement('div');
    grid.className = 'shop-grid';
    options.forEach(opt => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'shop-item';
      btn.innerHTML = `<span class="shop-icon">${opt.icon}</span><span class="shop-name">${opt.name}</span><span class="shop-eff">${opt.desc}</span>`;
      btn.addEventListener('click', () => {
        opt.apply();
        S.hero.gold = 0;
        closeModal();
        renderBoard();
        renderHUD();
        audioLevel();
      });
      grid.appendChild(btn);
    });
    // 4-й слот — пустой (как в оригинале)
    const empty = document.createElement('div');
    empty.className = 'shop-item shop-empty';
    empty.setAttribute('aria-hidden', 'true');
    grid.appendChild(empty);
    body.appendChild(grid);
  }

  function renderUpgradeChoices(body) {
    const options = shuffle([...UPGRADES]).slice(0, 4);
    const grid = document.createElement('div');
    grid.className = 'lv-grid';
    options.forEach(opt => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'lv-card lv-stat';
      btn.innerHTML = `<span class="lv-chip" style="color:#d9a8ff">${opt.icon}</span>
        <span class="lv-body">
          <span class="lv-name">${opt.name}</span>
          <span class="lv-desc">${opt.desc}</span>
        </span>`;
      btn.addEventListener('click', () => {
        opt.apply();
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
    S.over = true;
    audioGameOver();
    setTimeout(() => openModal('gameover'), 600);
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

  // --- pointer input --------------------------------------------------------
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
      const uniqueInfo = `<span class="pick-unique">Уникальный навык: ${cls.uniqueSkillDesc}</span>`;
      const status = unlocked
        ? `<span class="pick-lvl">ур. ${lvl || 1}${selected ? ' · выбран' : ''}</span>`
        : `<span class="pick-locked">🔒 15 спец-черепов → трофей</span>`;
      btn.innerHTML = `<span class="pick-head"><span class="pick-icon">${cls.icon}</span><span><span class="pick-name">${cls.name}</span><br><span class="pick-race">${cls.raceName || cls.race}</span></span></span>${desc.join('')}${uniqueInfo}${status}`;
      btn.addEventListener('click', () => {
        if (!unlocked) {
          flash('⚠ «' + cls.name + '» заблокирован — убей ~15 спец-черепов, чтобы получить трофей');
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
    showClassPicker,
    chooseLevelOptions,
    checkTrophy,
    claimTrophy,
    isClassUnlocked,
    classLevelOf,
    specialSpawnCount,
    makeSkull,
    placeNewTiles,
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
