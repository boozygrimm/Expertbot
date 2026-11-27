// miniapp_preview/raid_magitech.js
// Separate RAID design prototype, forked from classic build 63b4ca81a10e.
// Approved elemental combat for NEW runs only; released v1 Continue stays classic.
// Separate element RNG; released v1/v2/v3 retain their rules, new affinity runs use v4.
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
      loadRaidAppearance(); loadVisualPreferences();
      RUN_SAVE_BASE = undefined;
      readRunSlot(); readRunHistory();
      if (HISTORY_OPEN) renderRunHistory();
      else if (pickerOpen) showClassPicker();
    }
    profileNotice(`Профиль Telegram ${PROFILE_ID} · свой прогресс на этом устройстве. Между устройствами не синхронизируется.`);
    maybeShowUpdateNotice();
  }

  // V0: two independent LOCAL presentation choices, outside the active run,
  // next-run class config and release acknowledgement. Never migrate a save or
  // copy browser/anonymous choices into a late or changed Telegram identity.
  const VISUAL_KEY = 'raid:visualPreferences';
  let VISUAL_PREFERENCES = { shells: true, animations: true };
  let VISUAL_STATUS = '';
  function systemReducedMotion() {
    try { return !!window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches; }
    catch (ignored) { return false; }
  }
  function applyVisualPreferences() {
    const reduced = systemReducedMotion();
    document.documentElement?.setAttribute('data-raid-shell', VISUAL_PREFERENCES.shells ? 'on' : 'off');
    document.documentElement?.setAttribute('data-raid-motion', orbReducedMotion() ? 'off' : 'on');
    for (const input of document.querySelectorAll('[data-raid-visual]')) {
      input.checked = VISUAL_PREFERENCES[input.dataset.raidVisual];
    }
    for (const node of document.querySelectorAll('[data-raid-motion-note]')) {
      node.textContent = 'В устройстве включено уменьшение движения: анимации отключены независимо от переключателя.';
      node.hidden = !reduced;
    }
    for (const node of document.querySelectorAll('[data-raid-visual-status]')) {
      node.textContent = VISUAL_STATUS; node.hidden = !VISUAL_STATUS;
    }
    // Only cancel visual effects, never a gameplay timer/gesture/target draft.
    // Surviving orbs already occupy their FINAL sockets; cleanup reveals them.
    if (orbReducedMotion()) finishOrbAnimations();
    if (FLOAT_FRAME) window.cancelAnimationFrame?.(FLOAT_FRAME);
    FLOAT_FRAME = 0;
    if (FLOAT_EFFECTS.size) renderFloatEffects();
    positionGameSettings();
  }
  function loadVisualPreferences() {
    const defaults = { shells: true, animations: true };
    VISUAL_PREFERENCES = defaults; VISUAL_STATUS = '';
    try {
      const raw = profileStorage.getItem(VISUAL_KEY);
      if (raw !== null) {
        if (raw.length > 1024) throw new Error('Visual metadata too large');
        const data = JSON.parse(raw);
        if (!data || typeof data !== 'object' || Array.isArray(data) || data.version !== 1) throw new Error('Unknown visual metadata');
        for (const key of ['shells', 'animations']) if (Object.hasOwn(data, key) && typeof data[key] === 'boolean') defaults[key] = data[key];
      }
    } catch (ignored) {
      VISUAL_STATUS = 'Не удалось прочитать настройки вида этого профиля. Используется обычный вид; сохранение рейда не меняется.';
    }
    // Reading even malformed preferences is read-only. No eager repair/write.
    applyVisualPreferences();
  }
  function initVisualPreferences() {
    loadVisualPreferences();
    for (const input of document.querySelectorAll('[data-raid-visual]')) input.addEventListener('change', () => {
      const key = input.dataset.raidVisual;
      if (!['shells', 'animations'].includes(key)) return;
      VISUAL_PREFERENCES[key] = !!input.checked;
      try {
        profileStorage.setItem(VISUAL_KEY, JSON.stringify({ version: 1, ...VISUAL_PREFERENCES }));
        VISUAL_STATUS = '';
      } catch (ignored) {
        VISUAL_STATUS = 'Не удалось сохранить настройки вида этого профиля. Выбор действует только в этом окне; прогресс не меняется.';
      }
      applyVisualPreferences();
    });
    try { window.matchMedia?.('(prefers-reduced-motion: reduce)')?.addEventListener?.('change', applyVisualPreferences); }
    catch (ignored) { /* CSS still honours OS reduced motion on older clients. */ }
    window.addEventListener?.('storage', event => {
      if (event.key === null || event.key === profileStorage.key(VISUAL_KEY)) loadVisualPreferences();
    });
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
    4:  { name: 'Исцеление',   feats: ['Лечит до 2 раненых врагов до максимума HP', 'Если раненых нет — до 2 врагов получают +1 к атаке', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    5:  { name: 'Невидимый',   feats: ['Повышенная броня и половина обычного HP', 'Статы: ×2 ⚔ / ×10 🛡 / ½ ❤️', 'Без отдельной способности'] },
    6:  { name: 'Кислотный',   feats: ['Каждый ход превращает одну обычную зелье в кислотное', 'Кислое зелье — ранит героя, а не лечит', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    7:  { name: 'Огненный',    feats: ['Каждый ход поджигает до 8 занятых соседних клеток, кроме клеток других Огненных', 'После общего залпа горящие враги получают урон, равный атаке Огненного; сам поджог герою не вредит', 'Опасность для героя — его обычная атака (×2), уже учтённая в общем залпе. Соседние враги успевают атаковать до урона от огня. Убей Огненного, чтобы прекратить поджоги. Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    8:  { name: 'Застывший',   feats: ['Каждый ход замораживает до 2 клеток', 'Замороженные ресурсы не соединяются; враги могут входить в цепь, но не атакуют', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    9:  { name: 'Отравленный', feats: ['Если общий залп пробил броню, накладывает яд силой своей атаки', 'Пока на поле есть Отравленный, зелье не снимает яд у героя с пилотом «Биохимик»', 'Статы: ⚔ / ×2 🛡 / ×4 ❤️'] },
    10: { name: 'Ка-Ми-Казе',  feats: ['Таймер 5 → 0 — взрыв: половина HP героя', 'При убийстве тоже взрывается и задевает соседние клетки без врагов', 'Статы: ⚔ / ×2 🛡 / ×6 ❤️ · таймер 5'] },
    11: { name: 'Наблюдатель', feats: ['Каждый ход превращает один меч в обычного врага', 'Новый враг уязвим для целей цепочки', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    12: { name: 'Коррозия',    feats: ['Пока на поле, броня героя не восстанавливается', 'При появлении обнуляет броню героя, если выбран пилот «Штурмовик»', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    13: { name: 'Вор',         feats: ['Крадёт до 2 клеток-монет без награды', 'Если монет нет, крадёт до 2 золота; урон по HP получает только герой с пилотом «Снабженец»', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    14: { name: 'Хаотический', feats: ['Каждый ход меняется местом со случайной клеткой', 'Клетка назначения поглощается, если там стоит обычный враг', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    15: { name: 'Маг',         feats: ['Пока на поле — запрет на естественное восстановление кулдаунов', 'Кулдауны навыков не растут', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    16: { name: 'Ассасин',      feats: ['В нижнем ряду — 2× ⚔ дополнительного урона герою', 'Сама атакует — в сумме 3× ⚔', 'Статы: ×3 ⚔ / ×2 🛡 / ×3 ❤️'] },
    17: { name: 'Доппелгангер', feats: ['Каждую игру копирует текущую броню героя', 'Лучше атаковать на 0 брони', 'Статы: ×2 ⚔ / 🛡 = броня / ×4 ❤️'] },
    18: { name: 'Алхимик',      feats: ['Каждый ход меняет до 2 случайных клеток', 'Результат может быть тем же типом', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    19: { name: 'Нестабильный', feats: ['Таймер 5 → 0 — телепорт: поле полностью обновляется', 'Новое поле появляется без врагов', 'Статы: ×4 ⚔ / броня / ×5 ❤️ · таймер 5'] },
    20: { name: 'Вампир',       feats: ['Лечится на свою атаку, когда общий залп ранит героя', 'Обычно — до максимума HP; при пилоте «Перехватчик» может превысить его', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    21: { name: 'Мстительный',  feats: ['После цепочки +1 атака за каждое прямое убийство', 'Даже новый Мстительный получает эту прибавку', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    22: { name: 'Трамплящий',   feats: ['Каждый ход ломает один случайный щит или меч', 'Сломанное не приносит ничего', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    23: { name: 'Скользкий',    feats: ['В каждом столбце удаляет клетку под первым подходящим врагом', 'Удаление не даёт награды', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    24: { name: 'Альфа',        feats: ['Каждый ход меняет местами врага на краю и соседнюю клетку без врага', 'Целью может быть только обычный враг', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    25: { name: 'Магнитный',     feats: ['Каждый ход меняет внешние мечи или щиты с соседними клетками', 'Целевая клетка может содержать особого врага', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    26: { name: 'Брутал',        feats: ['Каждый ход швыряет в героя старого обычного врага', 'Урон герою равен HP этого врага', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    27: { name: 'Бронебойный',   feats: ['Получает урон цепочки, только если следующий узел — выше', 'Неуязвим в верхнем ряду', 'Статы: ×2 ⚔ / ×4 🛡 / ×3 ❤️'] },
    28: { name: 'Съедобный',     feats: ['Таймер 5 → 0 — снижает один модификатор снаряжения на шаг', 'Таймер сбрасывается на 5', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️ · таймер 5'] },
    29: { name: 'Загрязнитель',  feats: ['Пока на поле — каждый поглощённый пункт залпа ломает броню', 'Долговечность не действует, броски разрушения пропускаются', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    30: { name: 'Желе-Щит',      feats: ['Перехватывает урон цепочки, предназначенный другим врагам', 'Сам поглощает урон и уязвим', 'Статы: ½ ⚔ / броня / ×5 ❤️'] },
    31: { name: 'Бегун',        feats: ['Каждый ход уходит на одну клетку выше', 'На верхнем краю — исчезает без трофея', 'Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
    32: { name: 'Лич',          feats: ['Нанеси Личу смертельный урон цепочкой мечей: он превратится в филактерию, а не погибнет.', 'В следующем ходу собери цепочку из 3+ монет, обязательно включив филактерию.', 'Не соберёшь её до конца хода — Лич воскреснет. Сбор филактерия убивает его окончательно и даёт награду особого. Статы: ×2 ⚔ / ×2 🛡 / ×4 ❤️'] },
  };

  const SKILLS = [
    // 42 native skills. 0..30 are ordinary, 31..39 unlock globally at class
    // level 10. Hidden 40/41 never unlock (debug/imported states only).
    { id: 'steal', name: 'Воровство', icon: '🦊', cooldown: 13, native: 0, desc: 'Атаки врагов в этом ходу приносят золото' },
    { id: 'freeze', name: 'Мороз', icon: '❄', cooldown: 16, native: 1, desc: 'Все враги заморожены и не атакуют в этом ходу' },
    { id: 'heal', name: 'Благословение', icon: '❤', cooldown: 15, native: 2, desc: 'Собрать все обычные зелья' },
    { id: 'repair', name: 'Ремонт', icon: '⚒', cooldown: 16, native: 3, desc: 'Собрать все щиты' },
    { id: 'counter', name: 'Контратака', icon: '↩', cooldown: 18, native: 4, desc: 'Вместо атаки враги наносят урон сами себе' },
    { id: 'shatter', name: 'Раскол', icon: '💥', cooldown: 18, native: 5, desc: 'У обычных врагов броня 0, атака не выше 1' },
    { id: 'goldtouch', name: 'Золотое касание', icon: '🪙', cooldown: 15, native: 6, desc: 'Все мечи → монеты' },
    { id: 'teleport', name: 'Телепорт', icon: '✦', cooldown: 20, native: 7, desc: 'Все клетки без врагов → новые' },
    { id: 'explosive', name: 'Взрывное зелье', icon: '🧨', cooldown: 18, native: 8, desc: 'Все зелья становятся взрывными' },
    { id: 'elixir', name: 'Эликсир', icon: '⚗', cooldown: 18, native: 9, desc: 'Все зелья дают опыт вместо лечения' },
    { id: 'enchant', name: 'Апгрейд', icon: '🛠', cooldown: 35, native: 10, desc: 'Бесплатно усилить снаряжение (1 из 4)' },
    { id: 'slash', name: 'Рассечение', icon: '🗡', cooldown: 12, native: 11, desc: 'Собрать случайную строку; с «Взломом» — всю доску' },
    { id: 'treasure', name: 'Кладовая', icon: '💎', cooldown: 18, native: 12, desc: 'Собрать все монеты' },
    { id: 'scavenge', name: 'Сбор', icon: '🛡', cooldown: 15, native: 13, desc: 'Все мечи → щиты' },
    { id: 'disarm', name: 'Обезоруживание', icon: '⚔', cooldown: 25, native: 14, desc: 'Все обычные враги → мечи' },
    { id: 'dazzle', name: 'Слепота', icon: '✦', cooldown: 15, native: 15, desc: 'Все монеты → мечи' },
    { id: 'boostdmg', name: 'Усиление урона', icon: '⚔', cooldown: 14, native: 16, desc: 'Урон оружия ×2 в этом ходу' },
    { id: 'boostgold', name: 'Усиление золота', icon: '🪙', cooldown: 15, native: 17, desc: 'Золото ×2 в этом ходу' },
    { id: 'boosthealth', name: 'Усиление лечения', icon: '❤', cooldown: 15, native: 18, desc: 'Лечение зелий ×2 в этом ходу' },
    { id: 'boostarmour', name: 'Усиление брони', icon: '🛡', cooldown: 15, native: 19, desc: 'Броня и очки улучшений ×2 в этом ходу' },
    { id: 'exorcise', name: 'Изгнание', icon: '✝', cooldown: 18, native: 20, desc: 'Случайный особый враг становится обычным' },
    { id: 'banish', name: 'Уничтожение', icon: '☄', cooldown: 20, native: 21, desc: 'Убрать случайного особого врага без награды' },
    { id: 'trap', name: 'Ловушка', icon: '🕸', cooldown: 18, native: 22, desc: 'Новые враги появляются с 1 HP' },
    { id: 'biggame', name: 'Крупная дичь', icon: '👹', cooldown: 15, native: 23, desc: 'Случайный обычный враг становится особым' },
    { id: 'chamber', name: 'Сокровищница', icon: '🪙', cooldown: 25, native: 24, desc: 'Новые клетки — монеты' },
    { id: 'magicsword', name: 'Магический меч', icon: '✨', cooldown: 14, native: 25, desc: 'Случайный меч бьёт как 5 мечей' },
    { id: 'earthquake', name: 'Землетрясение', icon: '🌋', cooldown: 12, native: 26, desc: 'Перемешать все клетки поля' },
    { id: 'explosivearmour', name: 'Взрывная броня', icon: '🛡', cooldown: 16, native: 27, desc: 'Наносит всем врагам урон, равный 2× текущей брони' },
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
      ['Мех', cls.name + ' · специализация и модули'],
      ['Пилот', RACE_INFO[clsRace()].name + ' · особенность и смертный враг'],
      ['Особенность и смертный враг пилота', RACE_INFO[clsRace()].desc],
      ['Склонность на этот рейд', pilotAffinityDescription()],
      ['Пассивы меха', runClassConfig().perks.filter(Boolean).map(id => CLASS_TRAITS.perks[id].name).join(', ') || 'нет'],
      ['Изъяны меха', runClassConfig().flaws.filter(Boolean).map(id => CLASS_TRAITS.flaws[id].name).join(', ') || 'нет'],
      ['Здоровье', `${fmt(h.hp)} / ${fmt(h.maxHp)} HP`],
      ['Броня', `${fmt(h.armor)} / ${fmt(h.maxArmor)}`],
      ['Базовый урон', `${fmt(baseDamage())} + ${fmt(h.weaponDamage)} за каждый меч`],
      ['Сила / Урон', `${fmt(st.strength)} / ${fmt(st.damage)}`],
      ['Точность / Защита', `${fmt(st.dexterity)} / ${fmt(st.defense)}`],
      ['Живучесть / Удача / Все бонусы', `${fmt(st.vitality)} / ${fmt(st.luck)} / ${fmt(st.charisma)}`],
      ['Щит: ремонт ИЛИ улучшение', `${fmt(perShield())} брони; при полной броне/Коррозии — ${resourceYield(S.upgradePerShield).toFixed(2)} UP`],
      ['Лечение за зелье', `${fmt(HEAL_PER_POTION + st.luck)} HP`],
      ['Золото за монету / опыт за врага', `${resourceYield(S.goldPerCoin).toFixed(2)} / ${resourceYield(S.xpPerSkull).toFixed(2)}`],
      ['Пробитие / стойкость брони', `${Math.round(S.pierce*100)}% / ${Math.round(S.durability*100)}%`],
      ['Регенерация / ответный урон', `${fmt(S.regen)} HP/ход / ${fmt(S.spikes)}`],
      ['Навыки', S.skills.length ? S.skills.map(id => (SKILL_BY_ID[id] || {}).name || id).join(', ') : 'пока нет'],
      ...((S.equipment || []).map(it => [it.slotName, it.name + ' — ' + itemModsText(it)])),
    ];
    panel.innerHTML = '<p class="summary-hint">Конфигурация рейда закреплена при старте: пары и пул модулей задаёт мех, особенность и смертного врага — выбранный пилот. Это прежние пакеты, а не дополнительный бонус. Ниже итог с улучшениями и снаряжением.</p>';
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
    weaponDamage: { name: 'Урон оружия', icon: '⚔', unit: 1, desc: 'Добавляет урон за каждый меч в цепочке' },
    baseDamage: { name: 'Базовый урон', icon: '💥', unit: 1, desc: 'Добавляет урон цепочки по врагам' },
    leech: { name: 'Сок (лечение)', icon: '🩸', unit: .01, percent: true, desc: 'После атаки лечит на эту долю нанесённого урона; дробная часть отбрасывается' },
    weaponPoison: { name: 'Яд оружия', icon: '☠', unit: 1, desc: 'Попадание мечом отравляет выжившего врага на 3 хода; повторно отравить его нельзя' },
    pierce: { name: 'Пробитие брони', icon: '🎯', unit: .01, percent: true, desc: 'За каждый пункт урона, поглощённый бронёй врага, есть шанс разрушить этот пункт брони. Шанс не выше 95%' },
    quicken: { name: 'Ускорение навыков', icon: '⏱', unit: 1, desc: 'Уменьшает перезарядку всех навыков на 1 ход; несколько предметов не усиливают эффект' },
    xpPerSkull: { name: 'Опыт за врага', icon: '⭐', unit: .25, desc: 'Каждый собранный значок опыта приносит больше очков; бонус от предметов — до +2' },
    goldPerCoin: { name: 'Золото за монету', icon: '🪙', unit: .25, desc: 'Каждая собранная монета приносит больше золота; бонус от предметов — до +2' },
    strength: { name: 'Сила', icon: '💪', unit: 1, desc: 'За каждую единицу: +1 к базовому урону и +5% к шансу получить дополнительный значок опыта' },
    luck: { name: 'Удача', icon: '🍀', unit: 1, desc: 'За каждую единицу: +1 HP от зелья и +5% к шансу получить дополнительную монету' },
    maxHp: { name: 'Макс. HP', icon: '❤', unit: 1, desc: 'Увеличивает максимальный запас здоровья; в испытании «Крендель» не действует' },
    maxArmor: { name: 'Макс. броня', icon: '🛡', unit: 1, desc: 'Увеличивает предел брони; недостающий до полного запас остаётся прежним' },
    spikes: { name: 'Шипы', icon: '🗡', unit: 1, desc: 'Если перед атакой у тебя есть броня, наносит ответный урон врагам, уже стоявшим на поле. Их броня поглощает часть урона' },
    upgradePerShield: { name: 'Очки улучшения за щит', icon: '⬆', unit: .25, desc: 'Если щит вместо брони приносит очки улучшения, добавляет к ним бонус; от предметов — не больше +2' },
    durability: { name: 'Стойкость брони', icon: '🪨', unit: .01, percent: true, desc: 'Уменьшает шанс потерять броню за каждый поглощённый пункт урона. Шанс не ниже 5%' },
    blunt: { name: 'Снижение атаки врагов', icon: '🚫', unit: 1, desc: 'Если перед атакой врагов у тебя есть броня, после залпа снижает силу атаки тех, кто уже стоял на поле. Снижение равно бонусу; сила атаки не опускается ниже 1' },
    armorPerShield: { name: 'Броня за щит', icon: '🔰', unit: 1, desc: 'Каждый собранный щит восстанавливает дополнительную броню; не повышает её предел' },
    regen: { name: 'Регенерация', icon: '♻', unit: 1, desc: 'Восстанавливает здоровье в конце хода, до урона от яда' },
    dexterity: { name: 'Точность', icon: '🎯', unit: 1, desc: 'За каждую единицу: +1 броня при сборе щитов и +5% к шансу получить дополнительный щит' },
    vitality: { name: 'Живучесть', icon: '❤', unit: 1, get desc() { return S.mode === 'pretzel' ? '+5% к шансу получить дополнительное зелье; максимум здоровья не растёт' : '+5 к максимуму здоровья и +5% к шансу получить дополнительное зелье'; } },
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
      perk: '—', flaw: '—', raceName: 'Наёмник',
      perkDesc: 'нет пассивов', flawDesc: 'нет изъянов',
      clsDesc: 'Стартовый мех без пассива и изъяна; получает все открытые навыки',
      uniqueSkillDesc: 'Нет фиксированного: случайный выбор из всех открытых навыков' },
    { id: 'barbarian', native: 4, name: 'Варвар', icon: '🪓', race: 'Orc', uniqueSkill: 'berserk', pool: ['berserk', 'repair', 'counter', 'shatter', 'teleport', 'elixir', 'enchant', 'slash', 'treasure', 'scavenge', 'disarm', 'boostdmg', 'boosthealth', 'boostarmour', 'banish', 'exorcise', 'trap', 'biggame', 'magicsword', 'explosivearmour'],
      perk: 'Strong', flaw: 'Reckless', perkName: 'Сила', flawName: 'Безрассудство', raceName: 'Штурмовик',
      perkDesc: '+1 базового урона', flawDesc: '-10% шанс бонусных щитов',
      clsDesc: 'пассив: Сила · изъян: Безрассудство · пилот: Штурмовик (оглушение 10%)',
      uniqueSkillDesc: 'Берсерк (14): убийство врага восстанавливает HP в размере его атаки' },
    { id: 'mage', native: 5, name: 'Маг', icon: '🔮', race: 'Changeling', uniqueSkill: 'rune', pool: ['rune', 'freeze', 'heal', 'repair', 'goldtouch', 'teleport', 'explosive', 'elixir', 'enchant', 'treasure', 'dazzle', 'boostgold', 'boosthealth', 'banish', 'exorcise', 'chamber', 'magicsword', 'earthquake', 'fireball', 'mana'],
      perk: 'Gifted', flaw: 'Frail', perkName: 'Одарённость', flawName: 'Хрупкость', raceName: 'Тактик',
      perkDesc: '5% зелий выпадают как mana-зелья (−1 к кулдауну навыка)', flawDesc: '-10 макс. HP',
      clsDesc: 'пассив: Одарённость · изъян: Хрупкость · пилот: Тактик (зарядка навыка)',
      uniqueSkillDesc: 'Руна (12): замкнутая цепочка из 4+ узлов собирает интерьер, кроме особых и боссов' },
    { id: 'ranger', native: 1, name: 'Разведчик', icon: '🏹', race: 'Elf', uniqueSkill: 'volley', pool: ['volley', 'heal', 'repair', 'counter', 'shatter', 'teleport', 'explosive', 'elixir', 'enchant', 'treasure', 'scavenge', 'disarm', 'boostdmg', 'boosthealth', 'banish', 'trap', 'biggame', 'chamber', 'magicsword', 'mana'],
      perk: 'Agile', flaw: 'Slim', perkName: 'Ловкость', flawName: 'Тощий', raceName: 'Биохимик',
      perkDesc: '5% мечей выпадают стрелками (стрелять на 1 клетку)', flawDesc: '-1 броня',
      clsDesc: 'пассив: Ловкость · изъян: Тощий · пилот: Биохимик (медленный яд)',
      uniqueSkillDesc: 'Воллей (15): все мечи → стрелы (стрелы стреляют на 1 клетку)' },
    { id: 'rogue', native: 6, name: 'Вор', icon: '🗡', race: 'Sauren', uniqueSkill: 'greed', pool: ['greed', 'steal', 'heal', 'repair', 'counter', 'goldtouch', 'explosive', 'elixir', 'enchant', 'slash', 'treasure', 'scavenge', 'disarm', 'dazzle', 'boostgold', 'boosthealth', 'trap', 'biggame', 'chamber', 'magicsword'],
      perk: 'Frugal', flaw: 'Clumsy', perkName: 'Бережливость', flawName: 'Неловкость', raceName: 'Снабженец',
      perkDesc: '+10% шанс бонусных монет', flawDesc: '3% щитов выпадают сломанными (дают 0)',
      clsDesc: 'пассив: Бережливость · изъян: Неловкость · пилот: Снабженец (50% шанс розыгрыша свитка)',
      uniqueSkillDesc: 'Жадность (25): убивает всех врагов рядом с монетой' },
    { id: 'paladin', native: 7, name: 'Паладин', icon: '🛡', race: 'Dragonkin', uniqueSkill: 'laststand', pool: ['laststand', 'heal', 'repair', 'counter', 'shatter', 'explosive', 'elixir', 'slash', 'treasure', 'scavenge', 'disarm', 'boostdmg', 'boosthealth', 'boostarmour', 'banish', 'exorcise', 'biggame', 'chamber', 'magicsword', 'explosivearmour'],
      perk: 'Intimidating', flaw: 'Zealous', perkName: 'Устрашающий', flawName: 'Рьяный', raceName: 'Термит',
      perkDesc: 'особый враг вдвое реже заменяется смертным врагом пилота', flawDesc: 'монстры появляются на 1 ход раньше',
      clsDesc: 'пассив: Устрашающий · изъян: Рьяный · пилот: Термит (поджог при применении навыка)',
      uniqueSkillDesc: 'Последний рубеж (35): полное лечение HP, если герой погибнет в этот ход' },
    { id: 'priest', native: 3, name: 'Жрец', icon: '✨', race: 'Faun', uniqueSkill: 'wish', pool: ['wish', 'freeze', 'heal', 'shatter', 'goldtouch', 'teleport', 'explosive', 'elixir', 'enchant', 'treasure', 'scavenge', 'dazzle', 'boosthealth', 'boostarmour', 'banish', 'exorcise', 'magicsword', 'earthquake', 'fireball', 'mana'],
      perk: 'Faithful', flaw: 'Cautious', perkName: 'Праведный', flawName: 'Осторожный', raceName: 'Выживальщик',
      perkDesc: 'улучшение регенерации даёт +2 вместо +1', flawDesc: '-20% пробития брони',
      clsDesc: 'пассив: Праведный · изъян: Осторожный · пилот: Выживальщик (минимум HP при повышении уровня)',
      uniqueSkillDesc: 'Желание — одноразовый навык: собрать все клетки доски' },
    { id: 'raider', native: 2, name: 'Разбойник', icon: '🏴', race: 'Halfling', uniqueSkill: 'masochism', pool: ['masochism', 'heal', 'repair', 'counter', 'goldtouch', 'explosive', 'elixir', 'slash', 'treasure', 'dazzle', 'boostdmg', 'boostgold', 'boosthealth', 'boostarmour', 'exorcise', 'trap', 'biggame', 'chamber', 'magicsword', 'explosivearmour'],
      perk: 'Daring', flaw: 'Cocky', perkName: 'Дерзкий', flawName: 'Наглый', raceName: 'Бронник',
      perkDesc: '100% бонус с десятой клетки каждого типа', flawDesc: '3 узла: ресурсы убираются без награды; убийства дают опыт',
      clsDesc: 'пассив: Дерзкий · изъян: Наглый · пилот: Бронник (улучшение брони за серию убийств)',
      uniqueSkillDesc: 'Мазохизм (15): превращает все монетки во врагов' },
    { id: 'warrior', native: 8, name: 'Воин', icon: '⚔️', race: 'Dwarf', uniqueSkill: 'charge', pool: ['charge', 'heal', 'repair', 'counter', 'shatter', 'goldtouch', 'teleport', 'enchant', 'slash', 'treasure', 'scavenge', 'disarm', 'dazzle', 'boostdmg', 'boostarmour', 'banish', 'trap', 'biggame', 'magicsword', 'explosivearmour'],
      perk: 'Tough', flaw: 'Noisy', perkName: 'Прочный', flawName: 'Громкий', raceName: 'Проломщик',
      perkDesc: '+10 макс. HP', flawDesc: 'монстры получают +5% HP',
      clsDesc: 'пассив: Прочный · изъян: Громкий · пилот: Проломщик (вертикальный апперкот)',
      uniqueSkillDesc: 'Рывок (18): собирать все клетки по краю поля' },
    { id: 'assassin', native: 9, name: 'Ассасин', icon: '🗡', race: 'Werewolf', uniqueSkill: 'haste', pool: ['haste', 'steal', 'freeze', 'counter', 'shatter', 'teleport', 'explosive', 'elixir', 'treasure', 'disarm', 'dazzle', 'boostdmg', 'boosthealth', 'banish', 'trap', 'biggame', 'chamber', 'magicsword', 'explosivearmour', 'fireball'],
      perk: 'Cunning', flaw: 'Sneaky', perkName: 'Хитрый', flawName: 'Незаметный', raceName: 'Перехватчик',
      perkDesc: '-1 к перезарядке 4-го навыка', flawDesc: '+1 HP у обычных врагов',
      raceDesc: 'Калечение: в конце хода собирает обычных врагов с HP ниже их собственной защиты; даёт опыт, не действует на особых и боссов.',
      clsDesc: 'пассив: Хитрый · изъян: Незаметный · пилот: Перехватчик (автосбор ослабленных обычных врагов)',
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
    { id: 'berserk', native: 34, name: 'Берсерк', icon: '🪓', cooldown: 14, desc: 'Убийство врага восстанавливает HP в размере его атаки' },
    { id: 'rune', native: 35, name: 'Руна', icon: '🔮', cooldown: 12, desc: 'Контур из 4+ узлов собирает ресурсы и обычных врагов внутри без урона. Особые и боссы остаются' },
    { id: 'greed', native: 36, name: 'Жадность', icon: '💀', cooldown: 25, desc: 'Убить всех врагов рядом с монетой' },
    { id: 'laststand', native: 37, name: 'Последний рубеж', icon: '🛡', cooldown: 35, desc: 'Полное лечение HP, если герой погибнет в этот ход' },
    { id: 'wish', native: 33, name: 'Желание', icon: '✨', cooldown: 0, desc: 'Одноразовый навык: собрать все клетки доски' },
    { id: 'masochism', native: 32, name: 'Мазохизм', icon: '🏴', cooldown: 15, desc: 'Все монетки → враги' },
    { id: 'charge', native: 38, name: 'Рывок', icon: '⚔️', cooldown: 18, desc: 'Сорвать все клетки по краю поля' },
    { id: 'haste', native: 39, name: 'Спешка', icon: '🗡', cooldown: 16, desc: 'Ставит цепную клетку: подходит к любому типу меток' },
    { id: 'volley', native: 31, name: 'Воллей', icon: '🏹', cooldown: 15, desc: 'Все мечи → стрелы (стрелы стреляют на 1 клетку)' },
    { id: 'shock', name: 'Шок', icon: '⚡', cooldown: 14, native: 40, nativeOnly: true, desc: 'Собрать щиты и монеты в верхней половине; с «Благоговением» — цепь' },
    { id: 'awe', name: 'Благоговение', icon: '🌀', cooldown: 14, native: 41, nativeOnly: true, desc: 'Собрать щиты и зелья в нижней половине; с «Шоком» — цепь' },
  ];
  const ALL_SKILLS = SKILLS.concat(UNIQUE_SKILLS);
  // Keep the 42-ID native/I1 domain intact. This approved RAID-only module
  // is available in NEW v3 runs, never as a native shop scroll.
  const RETUNE_SKILL = { id: 'retune', name: 'Перенастройка', icon: '◇', native: 42,
    cooldown: 12, desc: 'Выбрать стихию и 1–3 целых незамороженных меча/стрелы. Отдельное подтверждение; без хода врагов' };
  const SKILL_BY_ID = Object.fromEntries([...ALL_SKILLS, RETUNE_SKILL].map(s => [s.id, s]));

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
    Human: { name: 'Наёмник', desc: 'Базовый профиль без отдельного пассива пилота.' },
    Elf: { name: 'Биохимик', desc: 'Яд на враге ослабляет его атаку. Смертный враг пилота — Отравленный: пока он на поле, зелья не снимают яд героя.' },
    Dwarf: { name: 'Проломщик', desc: 'Удар снизу вверх по вертикальной цепочке усиливается. Смертный враг пилота — Хаотический: блокирует остальные прямые удары цепочкой.' },
    Sauren: { name: 'Снабженец', desc: 'Вдвое выше шанс розыгрыша свитка в магазине. Смертный враг пилота — Вор: кража золота также ранит героя.' },
    Faun: { name: 'Выживальщик', desc: 'При повышении уровня поднимает HP хотя бы до 25% максимума. Смертный враг пилота — Ассассин: в нижнем ряду наносит герою дополнительный урон.' },
    Orc: { name: 'Штурмовик', desc: '10% шанс оглушить пережившего прямой удар врага. Смертный враг пилота — Коррозия: появление обнуляет броню героя.' },
    Halfling: { name: 'Бронник', desc: '15 прямых убийств одной цепочкой улучшают надетый доспех. Смертный враг пилота — Невидимый: удваивает защиту других новых врагов.' },
    Werewolf: { name: 'Перехватчик', desc: 'В конце хода собирает обычных врагов с HP ниже их защиты. Смертный враг пилота — Вампир: может лечиться выше прежнего максимума HP.' },
    Dragonkin: { name: 'Термит', desc: 'Ручное применение навыка поджигает случайного обычного врага. Смертный враг пилота — Застывший: вдвое уменьшает базовую часть урона цепочки.' },
    Changeling: { name: 'Тактик', desc: 'Сбор особого врага заряжает случайный надетый навык. Смертный враг пилота — Нестабильный: его телепорт обновляет поле без мечей.' },
  };
  // Display-only pilot professions; legacy race IDs and all linked bonuses/nemeses remain stable.
  const PILOT_ART_BY_RACE = Object.freeze({
    Human: 'mercenary', Elf: 'biochemist', Dwarf: 'breacher', Sauren: 'quartermaster',
    Faun: 'survivalist', Orc: 'assault', Halfling: 'armorer', Werewolf: 'interceptor',
    Dragonkin: 'thermite', Changeling: 'tactician',
  });
  const pilotArtSlug = race => PILOT_ART_BY_RACE[race] || 'mercenary';
  const pilotArtPath = (race, kind = 'portrait') => `miniapp_preview/assets/pilots/${pilotArtSlug(race)}_${kind}.webp`;
  const CLASS_HEAD_ART = Object.freeze(Object.fromEntries(CLASSES.map(cls => [cls.id, `miniapp_preview/assets/mech_head_${cls.id}.webp`])));
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
    if (state.classId === 'adventurer') return activeSkillDefinitions().filter(s => isSkillUnlocked(s.id)).sort((a,b) => a.native-b.native).map(s => s.id);
    const config = runClassConfig(state), base = CLASS_BY_ID[state.classId].pool;
    return [...new Set(base.map((id, i) => config.replacements[i] || id))].sort((a,b) => SKILL_BY_ID[a].native-SKILL_BY_ID[b].native);
  }

  // concept-art sprites from the catalog (window.SKULL_ART injected by the build)
  const SKULL_ART = (typeof window !== 'undefined' && window.SKULL_ART) || {};

  const RAID_MONSTER_ART = (typeof window !== 'undefined' && window.RAID_MONSTER_ART) || {};
  const RAID_REGIONS = {
    earth: { title: 'Забытый лес', short: 'Лес', element: 'Земля', index: '03' },
    fire: { title: 'Пылающая кузня', short: 'Кузня', element: 'Огонь', index: '01' },
    air: { title: 'Небесный разлом', short: 'Разлом', element: 'Воздух', index: '02' },
    water: { title: 'Затопленный храм', short: 'Храм', element: 'Вода', index: '04' },
  };
  const RAID_PORTRAITS = {
    fire: ['fire_spark', 'fire_crab', 'fire_demon', 'fire_dragon'],
    air: ['air_spirit', 'air_storm', 'air_griffin', 'air_dragon'],
    earth: ['earth_golem', 'earth_ent', 'earth_worm', 'earth_crystal'],
    water: ['water_elemental', 'water_octopus', 'water_angler', 'water_dragon'],
  };

  // Handcrafted luxury magitech skill medallions ("О положенная на бок", 4:3 oval).
  // Presentation only: original skill objects, IDs, save data, and effects stay intact.

  const RAID_SKILL_PALETTES = {
    gold:     { c0: '#ffe588', c1: '#b86c12', c2: '#421e05', c3: '#140802', acc: '#fff6c4', rim: '#ffd65e' },
    crimson:  { c0: '#ff964d', c1: '#b81e12', c2: '#4a0907', c3: '#160303', acc: '#ffe0a8', rim: '#ff703d' },
    frost:    { c0: '#a0f8ff', c1: '#1882bd', c2: '#082c50', c3: '#03101f', acc: '#e6fdff', rim: '#66e2ff' },
    emerald:  { c0: '#8cffca', c1: '#148f54', c2: '#063620', c3: '#02130b', acc: '#d8ffea', rim: '#52f0a0' },
    steel:    { c0: '#bce4fc', c1: '#34698f', c2: '#12293e', c3: '#06121c', acc: '#edf8ff', rim: '#88ccf5' },
    amethyst: { c0: '#ebb2ff', c1: '#782ac4', c2: '#2c0e52', c3: '#0f0420', acc: '#fae6ff', rim: '#d482ff' },
    blade:    { c0: '#ffb27a', c1: '#992e24', c2: '#3b1110', c3: '#150606', acc: '#ffead0', rim: '#f78a5c' },
    holy:     { c0: '#fff5c2', c1: '#c48a22', c2: '#462e0b', c3: '#170f03', acc: '#fffef2', rim: '#ffe885' },
    earth:    { c0: '#e6c475', c1: '#755922', c2: '#2d200c', c3: '#120d04', acc: '#fcf0c8', rim: '#e0bb6a' },
    prism:    { c0: '#98f7e0', c1: '#1c6d72', c2: '#0b2c32', c3: '#041316', acc: '#edfffa', rim: '#72f0da' },
  };

  const raidSkillGlint = (x, y, r = 5, col = '#ffffff', op = 0.95) =>
    `<path d="M${x} ${y - r} Q${x} ${y} ${x + r} ${y} Q${x} ${y} ${x} ${y + r} Q${x} ${y} ${x - r} ${y} Q${x} ${y} ${x} ${y - r}Z" fill="${col}" opacity="${op}"/>`;

  const RAID_SKILL_ICONS = {
    // 0. Воровство — rogue obsidian-gold domino mask + gleaming minted coin
    steal: {
      p: 'gold',
      art: (u) => `
        <path d="M26 31 C33 20, 46 20, 56 26 C66 20, 79 20, 86 31 C83 44, 72 49, 63 43 L56 38 L49 43 C40 49, 29 44, 26 31 Z" fill="#1b1424" stroke="url(#${u}eg)" stroke-width="2.2" stroke-linejoin="round"/>
        <path d="M34 32 Q42 27.5 48.5 34.5 Q41 38.5 34 32 Z M78 32 Q70 27.5 63.5 34.5 Q71 38.5 78 32 Z" fill="#ffe880" stroke="#7a3e00" stroke-width="0.9"/>
        <circle cx="41.5" cy="33" r="1.8" fill="#ffffff"/>
        <circle cx="70.5" cy="33" r="1.8" fill="#ffffff"/>
        <circle cx="56" cy="54" r="11.5" fill="url(#${u}eg)" stroke="#4a2806" stroke-width="1.8"/>
        <circle cx="56" cy="54" r="7.8" fill="none" stroke="#fff6cc" stroke-width="1.2" opacity="0.9"/>
        <polygon points="56,47.5 60.5,54 56,60.5 51.5,54" fill="#fff8d6" stroke="#8a4f0a" stroke-width="0.9"/>
        ${raidSkillGlint(66, 46, 5.5)}
      `,
    },

    // 1. Мороз — faceted royal 6-point cryo-crystal snowflake
    freeze: {
      p: 'frost',
      art: (u) => `
        <g stroke="#083052" stroke-width="4.4" stroke-linecap="round" opacity="0.55">
          <path d="M56 16 V68 M33.5 29 L78.5 55 M78.5 29 L33.5 55"/>
        </g>
        <g stroke="url(#${u}sv)" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M56 16 V68 M33.5 29 L78.5 55 M78.5 29 L33.5 55"/>
          <path d="M50 22.5 L56 28 L62 22.5 M50 61.5 L56 56 L62 61.5"/>
          <path d="M35.5 36.5 L42.8 34.3 L40.8 27 M76.5 47.5 L69.2 49.7 L71.2 57"/>
          <path d="M76.5 36.5 L69.2 34.3 L71.2 27 M35.5 47.5 L42.8 49.7 L40.8 57"/>
        </g>
        <polygon points="56,30.5 66,36.2 66,47.8 56,53.5 46,47.8 46,36.2" fill="#5eeaff" stroke="#ffffff" stroke-width="1.8"/>
        <polygon points="56,34.5 62.5,38.3 62.5,45.7 56,49.5 49.5,45.7 49.5,38.3" fill="#ebfeff"/>
        <polygon points="56,38 59.5,42 56,46 52.5,42" fill="#1ea4e6"/>
        ${raidSkillGlint(43, 24, 5)} ${raidSkillGlint(72, 57, 4.5)}
      `,
    },

    // 2. Благословение — faceted ruby heart crystal cradled by golden wings
    heal: {
      p: 'emerald',
      art: (u) => `
        <path d="M43 43 C31 42, 22 33, 20 24 C27 26, 33 28, 39 34 C31 34, 25 36, 24 43 C31 45, 37 46, 43 45 Z" fill="url(#${u}eg)" stroke="#4a2b07" stroke-width="1.4" stroke-linejoin="round"/>
        <path d="M69 43 C81 42, 90 33, 92 24 C85 26, 79 28, 73 34 C81 34, 87 36, 88 43 C81 45, 75 46, 69 45 Z" fill="url(#${u}eg)" stroke="#4a2b07" stroke-width="1.4" stroke-linejoin="round"/>
        <path d="M56 65 C43.5 55.5, 35.5 46.5, 35.5 36 C35.5 26.5, 43 21.5, 50 23.5 C53 24.5, 55 27, 56 30 C57 27, 59 24.5, 62 23.5 C69 21.5, 76.5 26.5, 76.5 36 C76.5 46.5, 68.5 55.5, 56 65 Z" fill="#d91e46" stroke="#fff2bc" stroke-width="2"/>
        <polygon points="56,31 68,36 56,59 44,36" fill="#ff5e7e" opacity="0.58"/>
        <path d="M43 33 C44 28.5, 48.5 27.5, 51.5 29.5" fill="none" stroke="#ffe0e8" stroke-width="2.5" stroke-linecap="round"/>
        <polygon points="56,37 60.5,43 56,49 51.5,43" fill="#fffbe8"/>
        ${raidSkillGlint(56, 20, 6.5, '#fff8c8')}
      `,
    },

    // 3. Ремонт — polished steel shield + sculpted golden-steel forge hammer & repair sparks
    repair: {
      p: 'steel',
      art: (u) => `
        <path d="M46 19 L66 25 V42 C66 53, 56 61, 46 65 C36 61, 26 53, 26 42 V25 Z" fill="url(#${u}sv)" stroke="#182c3d" stroke-width="2.2" stroke-linejoin="round"/>
        <path d="M46 24 L60 28.5 V41.5 C60 50, 53 56, 46 59.5 C39 56, 32 50, 32 41.5 V28.5 Z" fill="#1b4869" stroke="url(#${u}eg)" stroke-width="1.6"/>
        <polygon points="46,29 52,41 46,53 40,41" fill="#78e2ff" stroke="#ecfcff" stroke-width="1.1"/>
        <g transform="rotate(-38 71 44)">
          <rect x="67.5" y="34" width="7" height="31" rx="2.5" fill="url(#${u}eg)" stroke="#452406" stroke-width="1.5"/>
          <path d="M56 24 H86 L88 30.5 L86 37 H56 L54 30.5 Z" fill="url(#${u}sv)" stroke="#1c2f40" stroke-width="1.8" stroke-linejoin="round"/>
          <rect x="62" y="26.5" width="18" height="3.2" rx="1.2" fill="#ffffff" opacity="0.78"/>
          <path d="M62 34 H80" stroke="url(#${u}eg)" stroke-width="2"/>
        </g>
        ${raidSkillGlint(80, 24, 6, '#ffe888')} ${raidSkillGlint(62, 22, 4.5, '#fff8cc')}
      `,
    },

    // 4. Контратака — twin curved golden ricochet arrows bouncing off a faceted crystal mirror
    counter: {
      p: 'holy',
      art: (u) => `
        <polygon points="56,15 63.5,42 56,69 48.5,42" fill="#8cf2ff" stroke="#fff9da" stroke-width="1.8" stroke-linejoin="round"/>
        <polygon points="56,21 60,42 56,63 52,42" fill="#ffffff" opacity="0.75"/>
        <path d="M23 29 C32 21, 43 23, 49 34" fill="none" stroke="#472706" stroke-width="6.5" stroke-linecap="round"/>
        <path d="M23 29 C32 21, 43 23, 49 34" fill="none" stroke="url(#${u}eg)" stroke-width="4" stroke-linecap="round"/>
        <path d="M49 34 C41 44, 34 52, 26 55" fill="none" stroke="#472706" stroke-width="6.5" stroke-linecap="round"/>
        <path d="M49 34 C41 44, 34 52, 26 55" fill="none" stroke="url(#${u}eg)" stroke-width="4" stroke-linecap="round"/>
        <polygon points="20,57 32,46 34,60" fill="url(#${u}eg)" stroke="#472706" stroke-width="1.4" stroke-linejoin="round"/>
        <path d="M89 55 C80 63, 69 61, 63 50" fill="none" stroke="#472706" stroke-width="6.5" stroke-linecap="round"/>
        <path d="M89 55 C80 63, 69 61, 63 50" fill="none" stroke="url(#${u}eg)" stroke-width="4" stroke-linecap="round"/>
        <path d="M63 50 C71 40, 78 32, 86 29" fill="none" stroke="#472706" stroke-width="6.5" stroke-linecap="round"/>
        <path d="M63 50 C71 40, 78 32, 86 29" fill="none" stroke="url(#${u}eg)" stroke-width="4" stroke-linecap="round"/>
        <polygon points="92,27 80,38 78,24" fill="url(#${u}eg)" stroke="#472706" stroke-width="1.4" stroke-linejoin="round"/>
        ${raidSkillGlint(56, 42, 7)}
      `,
    },

    // 5. Раскол — fractured steel shield splintering apart with blazing molten crack
    shatter: {
      p: 'crimson',
      art: (u) => `
        <path d="M31 24 L53 19 L50 37 L54 46 L49 63 C39 58, 31 50, 31 40 Z" fill="url(#${u}sv)" stroke="#241010" stroke-width="2" stroke-linejoin="round" transform="translate(-4, 0) rotate(-6 44 42)"/>
        <path d="M59 19 L81 24 V40 C81 50, 73 58, 63 63 L59 46 L63 36 Z" fill="url(#${u}sv)" stroke="#241010" stroke-width="2" stroke-linejoin="round" transform="translate(4, 0) rotate(6 68 42)"/>
        <path d="M56 16 L49.5 35 L60.5 41 L51.5 66 L63.5 44 L54 37 Z" fill="#ffeb7d" stroke="#ff5218" stroke-width="1.5" stroke-linejoin="round"/>
        <polygon points="23,27 30,24 28,32" fill="#ffdc7a" stroke="#4a1408" stroke-width="1"/>
        <polygon points="84,26 90,30 83,33" fill="#ffdc7a" stroke="#4a1408" stroke-width="1"/>
        <polygon points="82,53 89,56 83,60" fill="#ff9e4a" stroke="#4a1408" stroke-width="1"/>
        <polygon points="24,52 31,57 24,59" fill="#ff9e4a" stroke="#4a1408" stroke-width="1"/>
        ${raidSkillGlint(56, 39, 7.5, '#fffbe0')}
      `,
    },

    // 6. Золотое касание — gleaming sword mid-transmutation into cascading gold coins
    goldtouch: {
      p: 'gold',
      art: (u) => `
        <path d="M26 57 L45 38 L52 45 L33 64 Z" fill="url(#${u}sv)" stroke="#291a0a" stroke-width="1.6"/>
        <path d="M23 50 L39 66" stroke="url(#${u}eg)" stroke-width="4.2" stroke-linecap="round"/>
        <path d="M20 65 L28 57" stroke="#6e3e0c" stroke-width="4.5" stroke-linecap="round"/>
        <path d="M43 40 L60 23 L66 23 L66 29 L49 46 Z" fill="url(#${u}eg)" stroke="#4a2806" stroke-width="1.6"/>
        <circle cx="73" cy="27" r="9.5" fill="url(#${u}eg)" stroke="#4a2806" stroke-width="1.7"/>
        <circle cx="73" cy="27" r="6" fill="none" stroke="#fff6cc" stroke-width="1.2"/>
        <circle cx="81" cy="46" r="8.5" fill="url(#${u}eg)" stroke="#4a2806" stroke-width="1.7"/>
        <circle cx="81" cy="46" r="5" fill="none" stroke="#fff6cc" stroke-width="1.1"/>
        <circle cx="64" cy="56" r="7.8" fill="url(#${u}eg)" stroke="#4a2806" stroke-width="1.6"/>
        <circle cx="64" cy="56" r="4.6" fill="none" stroke="#fff6cc" stroke-width="1"/>
        ${raidSkillGlint(56, 29, 7)} ${raidSkillGlint(82, 35, 4.5, '#ffe896')}
      `,
    },

    // 7. Телепорт — swirling magitech arcane vortex portal & luminous central rift
    teleport: {
      p: 'amethyst',
      art: (u) => `
        <ellipse cx="56" cy="42" rx="32" ry="21.5" fill="none" stroke="#cc84ff" stroke-width="2.2" stroke-dasharray="14 6" opacity="0.88"/>
        <path d="M28 44 C30 24, 57 17, 76 26 C87 32, 87 48, 72 56 C56 64, 35 58, 37 44 C39 33, 55 29, 66 34 C73 38, 71 48, 60 51 C51 53, 44 47, 48 41" fill="none" stroke="#2c0b4f" stroke-width="5.5" stroke-linecap="round"/>
        <path d="M28 44 C30 24, 57 17, 76 26 C87 32, 87 48, 72 56 C56 64, 35 58, 37 44 C39 33, 55 29, 66 34 C73 38, 71 48, 60 51 C51 53, 44 47, 48 41" fill="none" stroke="#f2ccff" stroke-width="2.8" stroke-linecap="round"/>
        <circle cx="56" cy="42" r="8.5" fill="#70ebff" opacity="0.52"/>
        <circle cx="56" cy="42" r="4.5" fill="#ffffff"/>
        ${raidSkillGlint(56, 42, 10.5, '#ffffff')} ${raidSkillGlint(32, 26, 4.5, '#ffe8a0')} ${raidSkillGlint(82, 55, 4.5, '#8cf4ff')}
      `,
    },

    // 8. Взрывное зелье — crystal-faceted amber bomb-flask with ornate gold collar & sparkling fuse
    explosive: {
      p: 'crimson',
      art: (u) => `
        <path d="M56 29 C43 29, 35 38.5, 35 50 C35 60.5, 43.5 66.5, 56 66.5 C68.5 66.5, 77 60.5, 77 50 C77 38.5, 69 29, 56 29 Z" fill="#c72812" stroke="url(#${u}eg)" stroke-width="2.2"/>
        <polygon points="56,32 70,43 65,62 47,62 42,43" fill="#ff7f24" opacity="0.85"/>
        <polygon points="56,37 64,46 56,59 48,46" fill="#ffe46e"/>
        <path d="M42 43 L56 32 L70 43 M47 62 L56 32 L65 62" stroke="#fff2b8" stroke-width="0.9" opacity="0.45"/>
        <rect x="47.5" y="21.5" width="17" height="5.5" rx="2" fill="url(#${u}eg)" stroke="#472306" stroke-width="1.3"/>
        <rect x="49.5" y="26.5" width="13" height="4.5" rx="1" fill="url(#${u}eg)" stroke="#472306" stroke-width="1.2"/>
        <path d="M56 21.5 C59 16, 66 15, 73 19.5" fill="none" stroke="#ffe090" stroke-width="2.6" stroke-linecap="round"/>
        <polygon points="76,20 83,13 79,22 87,25 78,25 80,33 74,26 68,29 73,22 69,15" fill="#fff496" stroke="#ff5c1a" stroke-width="1.1"/>
        ${raidSkillGlint(76, 21, 6.5)}
      `,
    },

    // 9. Эликсир — ornate emerald-gold alchemical crystal flask with rising XP star
    elixir: {
      p: 'emerald',
      art: (u) => `
        <path d="M49 23 H63 V31 L76.5 52 C79.5 58, 74.5 65.5, 66 65.5 H46 C37.5 65.5, 32.5 58, 35.5 52 L49 31 Z" fill="#0c482e" stroke="url(#${u}sv)" stroke-width="2.1" stroke-linejoin="round"/>
        <path d="M41.5 46 H70.5 L74.5 53 C76.5 57.5, 72.5 62.5, 65 62.5 H47 C39.5 62.5, 35.5 57.5, 37.5 53 Z" fill="#34ee90"/>
        <path d="M44 48 H68 L62 59.5 H50 Z" fill="#beffdc" opacity="0.68"/>
        <rect x="46.5" y="17.5" width="19" height="5.5" rx="2" fill="url(#${u}eg)" stroke="#3d2205" stroke-width="1.3"/>
        <rect x="49" y="22.8" width="14" height="4.2" rx="1" fill="url(#${u}eg)" stroke="#3d2205" stroke-width="1.2"/>
        <circle cx="50.5" cy="54.5" r="2.2" fill="#ffffff" opacity="0.9"/>
        <circle cx="62.5" cy="51.5" r="1.7" fill="#ffffff" opacity="0.85"/>
        <polygon points="56,31 58.6,37.4 65.5,38 60.2,42.5 61.8,49 56,45.5 50.2,49 51.8,42.5 46.5,38 53.4,37.4" fill="url(#${u}eg)" stroke="#3d2205" stroke-width="1.2" stroke-linejoin="round"/>
        ${raidSkillGlint(74, 30, 5.5, '#fff6b8')}
      `,
    },

    // 10. Затачивание — faceted arcane prism crystal bathed in golden upgrade chevrons
    enchant: {
      p: 'amethyst',
      art: (u) => `
        <polygon points="56,16 71,31 64.5,53.5 56,65 47.5,53.5 41,31" fill="#46ccff" stroke="#fff6cc" stroke-width="2" stroke-linejoin="round"/>
        <polygon points="56,16 63.5,31 56,65 48.5,31" fill="#ccf7ff" opacity="0.82"/>
        <polygon points="41,31 56,37 71,31 56,16" fill="#ffffff" opacity="0.52"/>
        <path d="M26 42 H36 M76 42 H86 M31 25 L38 30 M81 25 L74 30 M31 59 L38 54 M81 59 L74 54" stroke="url(#${u}eg)" stroke-width="2.6" stroke-linecap="round"/>
        <path d="M27 50 L35 42 L27 34 M85 50 L77 42 L85 34" fill="none" stroke="#ffdc72" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
        ${raidSkillGlint(56, 33, 7.5)} ${raidSkillGlint(34, 24, 4.5, '#ffe898')} ${raidSkillGlint(79, 24, 4.5, '#ffe898')}
      `,
    },

    // 11. Рассечение — razor horizontal crescent sword-slash cleaving across a row of 3 squares
    slash: {
      p: 'blade',
      art: (u) => `
        <g fill="#5c1e16" stroke="#c97a4b" stroke-width="1.1" opacity="0.82">
          <rect x="26" y="34" width="14" height="14" rx="2.5"/>
          <rect x="49" y="34" width="14" height="14" rx="2.5"/>
          <rect x="72" y="34" width="14" height="14" rx="2.5"/>
        </g>
        <path d="M18 52 C33 23, 73 19, 94 37 C76 32, 41 39, 24 58 Z" fill="url(#${u}sv)" stroke="#fff7d6" stroke-width="1.7" stroke-linejoin="round"/>
        <path d="M22 49 C38 27, 71 24, 90 36 C73 30, 42 35, 27 53 Z" fill="#ffffff" opacity="0.88"/>
        <path d="M20 41 H92" stroke="#ffd86e" stroke-width="2.2" stroke-linecap="round" opacity="0.9"/>
        ${raidSkillGlint(90, 36, 8)} ${raidSkillGlint(27, 50, 5, '#ffe690')}
      `,
    },

    // 12. Кладовая — overflowing ironbound royal gold treasure chest with turquoise gem
    treasure: {
      p: 'gold',
      art: (u) => `
        <path d="M30 37 C33 22, 79 22, 82 37 Z" fill="#7d4410" stroke="url(#${u}eg)" stroke-width="2"/>
        <path d="M34 37 C40 31, 72 31, 78 37" fill="#ffe066" stroke="#8a4f0a" stroke-width="1"/>
        <circle cx="44" cy="35" r="5" fill="url(#${u}eg)" stroke="#5c3106" stroke-width="1.2"/>
        <circle cx="56" cy="32" r="5.6" fill="url(#${u}eg)" stroke="#5c3106" stroke-width="1.2"/>
        <circle cx="68" cy="35" r="5" fill="url(#${u}eg)" stroke="#5c3106" stroke-width="1.2"/>
        <rect x="29" y="38" width="54" height="25.5" rx="4" fill="#63320a" stroke="url(#${u}eg)" stroke-width="2.4"/>
        <path d="M39 38 V63.5 M73 38 V63.5 M29 48.5 H83" stroke="url(#${u}eg)" stroke-width="2.2"/>
        <rect x="50" y="42.5" width="12" height="13" rx="2.5" fill="url(#${u}eg)" stroke="#3b1d04" stroke-width="1.4"/>
        <polygon points="56,45 59.8,49 56,53 52.2,49" fill="#32eed8" stroke="#ffffff" stroke-width="0.9"/>
        ${raidSkillGlint(56, 23, 6.5)} ${raidSkillGlint(76, 30, 4.5, '#fff0a8')}
      `,
    },

    // 13. Сбор — sword forged via golden transmutation arc into a gleaming steel buckler
    scavenge: {
      p: 'steel',
      art: (u) => `
        <path d="M23 56 L38 24 L43 26 L29 58 Z" fill="url(#${u}sv)" stroke="#1b2d3d" stroke-width="1.6"/>
        <path d="M21 50 L33 56" stroke="url(#${u}eg)" stroke-width="3.4" stroke-linecap="round"/>
        <path d="M42 29 C48 23, 55 24, 58 30" fill="none" stroke="url(#${u}eg)" stroke-width="3.2" stroke-linecap="round"/>
        <polygon points="60,33 52,31 59,24" fill="#ffe68a"/>
        <path d="M70 25 L87 31 V46 C87 55.5, 79 62, 70 65 C61 62, 53 55.5, 53 46 V31 Z" fill="url(#${u}sv)" stroke="#152b3d" stroke-width="2.1"/>
        <path d="M70 30 L82 34.5 V45.5 C82 52.5, 76.5 57.5, 70 60 C63.5 57.5, 58 52.5, 58 45.5 V34.5 Z" fill="#20587d" stroke="url(#${u}eg)" stroke-width="1.5"/>
        <polygon points="70,35.5 75.5,44.5 70,53.5 64.5,44.5" fill="#84eeff" stroke="#ffffff" stroke-width="1.1"/>
        ${raidSkillGlint(70, 44.5, 5.5)}
      `,
    },

    // 14. Обезоруживание — snapped curved enemy skull-horns shattering outward as a gleaming sword rises
    disarm: {
      p: 'blade',
      art: (u) => `
        <path d="M44 33 C34 31, 27 25, 25 18 C21 27, 25 39, 39 43 Z" fill="#f0dfc0" stroke="#3d1712" stroke-width="1.6" stroke-linejoin="round"/>
        <path d="M68 33 C78 31, 85 25, 87 18 C91 27, 87 39, 73 43 Z" fill="#f0dfc0" stroke="#3d1712" stroke-width="1.6" stroke-linejoin="round"/>
        <path d="M35 46 L45 43 L42 54 Z M77 46 L67 43 L70 54 Z" fill="#d98c6c" stroke="#3b1510" stroke-width="1.2"/>
        <polygon points="56,14 62.5,24 60.5,51 51.5,51 49.5,24" fill="url(#${u}sv)" stroke="#241010" stroke-width="1.9" stroke-linejoin="round"/>
        <path d="M56 17 V50" stroke="#ffffff" stroke-width="1.8"/>
        <path d="M43 51 H69 L65.5 55.5 H46.5 Z" fill="url(#${u}eg)" stroke="#472306" stroke-width="1.5"/>
        <rect x="53.3" y="55.5" width="5.4" height="9" rx="1.5" fill="#703810" stroke="url(#${u}eg)" stroke-width="1.3"/>
        <circle cx="56" cy="66.5" r="3.4" fill="url(#${u}eg)" stroke="#472306" stroke-width="1.3"/>
        ${raidSkillGlint(56, 22, 6.5)}
      `,
    },

    // 15. Слепота — blinding radiant 8-ray golden sunburst flare
    dazzle: {
      p: 'holy',
      art: (u) => `
        <polygon points="56,13 60,33 78,20 65,38 89,42 65,46 78,64 60,51 56,71 52,51 34,64 47,46 23,42 47,38 34,20 52,33" fill="url(#${u}eg)" stroke="#5c3608" stroke-width="1.5" stroke-linejoin="round"/>
        <polygon points="56,21 59,36 74,42 59,48 56,63 53,48 38,42 53,36" fill="#fffce4"/>
        <circle cx="56" cy="42" r="8" fill="#ffffff" stroke="#ffda5c" stroke-width="2"/>
        <circle cx="56" cy="42" r="3.5" fill="#fff2a6"/>
        ${raidSkillGlint(33, 25, 4.5)} ${raidSkillGlint(79, 58, 4.5)}
      `,
    },

    // 16. Усиление урона — crossed gleaming greatswords with clean golden upward power crest below tips
    boostdmg: {
      p: 'blade',
      art: (u) => `
        <path d="M27 59 L67 19 L74 19 L74 26 L34 66 Z" fill="url(#${u}sv)" stroke="#260c0c" stroke-width="1.7" stroke-linejoin="round"/>
        <path d="M85 59 L45 19 L38 19 L38 26 L78 66 Z" fill="url(#${u}sv)" stroke="#260c0c" stroke-width="1.7" stroke-linejoin="round"/>
        <path d="M25 51 L39 65 M87 51 L73 65" stroke="url(#${u}eg)" stroke-width="4.2" stroke-linecap="round"/>
        <path d="M44 43 L56 31 L68 43" fill="none" stroke="#3b0c06" stroke-width="7.8" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M44 43 L56 31 L68 43" fill="none" stroke="url(#${u}eg)" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M46 54 L56 44 L66 54" fill="none" stroke="#3b0c06" stroke-width="6.8" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M46 54 L56 44 L66 54" fill="none" stroke="#fff2a6" stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round"/>
        ${raidSkillGlint(56, 30, 6.5)}
      `,
    },

    // 17. Усиление золота — stacked royal gold coins + gleaming double-up chevrons
    boostgold: {
      p: 'gold',
      art: (u) => `
        <ellipse cx="44" cy="56" rx="16" ry="7.5" fill="url(#${u}eg)" stroke="#4a2606" stroke-width="1.6"/>
        <ellipse cx="44" cy="49" rx="16" ry="7.5" fill="url(#${u}eg)" stroke="#4a2606" stroke-width="1.6"/>
        <ellipse cx="44" cy="42" rx="16" ry="7.5" fill="#ffe885" stroke="#4a2606" stroke-width="1.6"/>
        <ellipse cx="44" cy="42" rx="10" ry="4.2" fill="none" stroke="#995b0c" stroke-width="1.2"/>
        <circle cx="67" cy="48" r="14.2" fill="url(#${u}eg)" stroke="#4a2606" stroke-width="1.8"/>
        <circle cx="67" cy="48" r="9.8" fill="none" stroke="#fff6cc" stroke-width="1.3"/>
        <polygon points="67,41 71.5,48 67,55 62.5,48" fill="#fff8d6" stroke="#8f5208" stroke-width="1"/>
        <path d="M56 29 L67 18 L78 29 M59 37 L67 29 L75 37" fill="none" stroke="#452205" stroke-width="5.5" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M56 29 L67 18 L78 29 M59 37 L67 29 L75 37" fill="none" stroke="#fff4b0" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
        ${raidSkillGlint(67, 18, 6)}
      `,
    },

    // 18. Усиление лечения — faceted crimson-ruby crystal heart chalice + twin golden rising vitality chevrons
    boosthealth: {
      p: 'emerald',
      art: (u) => `
        <path d="M24 44 L34 34 L44 44 M68 44 L78 34 L88 44" fill="none" stroke="#06301b" stroke-width="5.8" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M24 44 L34 34 L44 44 M68 44 L78 34 L88 44" fill="none" stroke="url(#${u}eg)" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M26 32 L34 24 L42 32 M70 32 L78 24 L86 32" fill="none" stroke="#8cffca" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" opacity="0.9"/>
        <polygon points="56,17 72,33 65,58 56,66 47,58 40,33" fill="#e01e44" stroke="url(#${u}eg)" stroke-width="2.2" stroke-linejoin="round"/>
        <polygon points="56,17 65,33 56,66 47,33" fill="#ff6686" opacity="0.68"/>
        <polygon points="40,33 56,40 72,33 56,17" fill="#ffb8c8" opacity="0.5"/>
        <polygon points="56,34 61.5,42 56,50 50.5,42" fill="#fff6da"/>
        ${raidSkillGlint(56, 20, 6)} ${raidSkillGlint(78, 24, 4.5, '#b8ffe0')}
      `,
    },

    // 19. Усиление брони — crowned fortress tower-shield with luminous azure-gold chevrons
    boostarmour: {
      p: 'steel',
      art: (u) => `
        <path d="M56 17 L78 24 V42 C78 54, 68 62, 56 67 C44 62, 34 54, 34 42 V24 Z" fill="url(#${u}eg)" stroke="#241b0b" stroke-width="2" stroke-linejoin="round"/>
        <path d="M56 22 L73 27.5 V41.5 C73 51, 65 58, 56 62 C47 58, 39 51, 39 41.5 V27.5 Z" fill="#184769" stroke="#a6e0ff" stroke-width="1.4"/>
        <path d="M45 41 L56 31 L67 41 M46 51 L56 42 L66 51" fill="none" stroke="#fff4bd" stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M25 36 L31 30 M87 36 L81 30 M24 46 H30 M88 46 H82" stroke="#8ce0ff" stroke-width="2.4" stroke-linecap="round"/>
        ${raidSkillGlint(56, 22, 6)}
      `,
    },

    // 20. Изгнание — radiant celestial purification spear-cross banishing dark mist
    exorcise: {
      p: 'holy',
      art: (u) => `
        <circle cx="56" cy="42" r="20.5" fill="none" stroke="#ffe48c" stroke-width="1.8" stroke-dasharray="6 4" opacity="0.78"/>
        <path d="M35 30 C28 38, 30 50, 39 54 M77 30 C84 38, 82 50, 73 54" fill="none" stroke="#a87eff" stroke-width="3.2" stroke-linecap="round" opacity="0.82"/>
        <polygon points="56,14 61,28 59,67 56,71 53,67 51,28" fill="url(#${u}eg)" stroke="#472806" stroke-width="1.6" stroke-linejoin="round"/>
        <polygon points="34,35 56,31 78,35 56,40" fill="url(#${u}eg)" stroke="#472806" stroke-width="1.6" stroke-linejoin="round"/>
        <polygon points="56,28 63,35.5 56,43 49,35.5" fill="#ffffff" stroke="#c88d24" stroke-width="1.3"/>
        ${raidSkillGlint(56, 35.5, 7)} ${raidSkillGlint(56, 15, 4.5)}
      `,
    },

    // 21. Уничтожение — blazing void-obliteration seal / meteor strike target
    banish: {
      p: 'amethyst',
      art: (u) => `
        <circle cx="56" cy="42" r="22" fill="#21093d" stroke="url(#${u}eg)" stroke-width="2.6"/>
        <circle cx="56" cy="42" r="15" fill="none" stroke="#d88cff" stroke-width="1.8" stroke-dasharray="7 4"/>
        <path d="M38 24 L74 60 M74 24 L38 60" stroke="#240836" stroke-width="7.5" stroke-linecap="round"/>
        <path d="M38 24 L74 60 M74 24 L38 60" stroke="url(#${u}eg)" stroke-width="4.2" stroke-linecap="round"/>
        <path d="M38 24 L74 60 M74 24 L38 60" stroke="#fff7d1" stroke-width="1.4" stroke-linecap="round"/>
        <polygon points="56,33 65,42 56,51 47,42" fill="#ff5ca8" stroke="#ffffff" stroke-width="1.6"/>
        ${raidSkillGlint(56, 42, 7)}
      `,
    },

    // 22. Ловушка — heavy forged steel serrated bear-trap jaws snapping shut around amber trigger plate
    trap: {
      p: 'steel',
      art: (u) => `
        <path d="M24 44 C26 24, 86 24, 88 44" fill="none" stroke="#101d28" stroke-width="7.5" stroke-linecap="round"/>
        <path d="M24 44 C26 24, 86 24, 88 44" fill="none" stroke="url(#${u}sv)" stroke-width="4.2" stroke-linecap="round"/>
        <path d="M24 44 C26 62, 86 62, 88 44" fill="none" stroke="#101d28" stroke-width="7.5" stroke-linecap="round"/>
        <path d="M24 44 C26 62, 86 62, 88 44" fill="none" stroke="url(#${u}sv)" stroke-width="4.2" stroke-linecap="round"/>
        <g fill="url(#${u}sv)" stroke="#12212e" stroke-width="1.2" stroke-linejoin="round">
          <polygon points="35,31 40,42 44,30"/>
          <polygon points="48,28 52,39 56,28"/>
          <polygon points="60,28 64,39 68,28"/>
          <polygon points="71,31 74,42 78,32"/>
          <polygon points="36,56 41,46 45,57"/>
          <polygon points="51,58 56,48 61,58"/>
          <polygon points="67,57 71,46 76,56"/>
        </g>
        <circle cx="24" cy="44" r="4.5" fill="url(#${u}eg)" stroke="#3d2105" stroke-width="1.4"/>
        <circle cx="88" cy="44" r="4.5" fill="url(#${u}eg)" stroke="#3d2105" stroke-width="1.4"/>
        <ellipse cx="56" cy="44" rx="10" ry="6" fill="url(#${u}eg)" stroke="#472606" stroke-width="1.5"/>
        <polygon points="56,40 60,44 56,48 52,44" fill="#ff5524" stroke="#fff4b8" stroke-width="1"/>
        ${raidSkillGlint(56, 44, 5)}
      `,
    },

    // 23. Крупная дичь — golden hunter's crosshair over fierce horned trophy skull
    biggame: {
      p: 'earth',
      art: (u) => `
        <circle cx="56" cy="42" r="22.5" fill="none" stroke="url(#${u}eg)" stroke-width="2.5"/>
        <path d="M56 13 V23 M56 61 V71 M23 42 H35 M77 42 H89" stroke="url(#${u}eg)" stroke-width="2.8" stroke-linecap="round"/>
        <path d="M40 28 C32 23, 30 16, 37 14 C36 19, 41 22, 47 26" fill="url(#${u}eg)" stroke="#452506" stroke-width="1.3" stroke-linejoin="round"/>
        <path d="M72 28 C80 23, 82 16, 75 14 C76 19, 71 22, 65 26" fill="url(#${u}eg)" stroke="#452506" stroke-width="1.3" stroke-linejoin="round"/>
        <path d="M43 37 C43 28, 69 28, 69 37 C69 44, 65 48, 63 54 H49 C47 48, 43 44, 43 37 Z" fill="#f2e6cc" stroke="#3d240b" stroke-width="1.8"/>
        <path d="M47 38 L53 40 L48 44 Z M65 38 L59 40 L64 44 Z" fill="#ff4824"/>
        <path d="M52 50 V55 M56 50 V55 M60 50 V55" stroke="#3d240b" stroke-width="1.5"/>
        ${raidSkillGlint(56, 29, 5, '#ffe888')}
      `,
    },

    // 24. Сокровищница — vaulted golden bank arch & gleaming gold bullion pyramid
    chamber: {
      p: 'gold',
      art: (u) => `
        <path d="M30 62 V38 C30 22, 82 22, 82 38 V62 Z" fill="#2b1404" stroke="url(#${u}eg)" stroke-width="3.2" stroke-linejoin="round"/>
        <path d="M36 60 V39 C36 28, 76 28, 76 39 V60 Z" fill="#522607" stroke="#ffd86e" stroke-width="1.2"/>
        <polygon points="39,58 45,48 56,48 52,58" fill="url(#${u}eg)" stroke="#4a2405" stroke-width="1.3"/>
        <polygon points="54,58 58,48 69,48 73,58" fill="url(#${u}eg)" stroke="#4a2405" stroke-width="1.3"/>
        <polygon points="46,48 51,38 61,38 66,48" fill="#ffe880" stroke="#4a2405" stroke-width="1.3"/>
        <path d="M51 38 H61" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round"/>
        <polygon points="56,21 60,26 56,31 52,26" fill="#46f0dc" stroke="#fff8d6" stroke-width="1.1"/>
        ${raidSkillGlint(56, 37, 6)} ${raidSkillGlint(70, 48, 4)}
      `,
    },

    // 25. Магический меч — glowing arcane crystal greatsword + floating runes
    magicsword: {
      p: 'amethyst',
      art: (u) => `
        <polygon points="79,17 75,30 43,60 36,53 66,21" fill="#6ae8ff" stroke="#ffffff" stroke-width="1.8" stroke-linejoin="round"/>
        <path d="M76 20 L40 56" stroke="#ffffff" stroke-width="2.2" stroke-linecap="round"/>
        <path d="M30 46 L48 64" stroke="url(#${u}eg)" stroke-width="5" stroke-linecap="round"/>
        <path d="M25 65 L37 53" stroke="#52246e" stroke-width="4.5" stroke-linecap="round"/>
        <circle cx="24" cy="66" r="3.8" fill="url(#${u}eg)" stroke="#3d1d06" stroke-width="1.2"/>
        <circle cx="39" cy="55" r="3.5" fill="#ff70e8" stroke="#ffffff" stroke-width="1"/>
        <polygon points="36,27 40,32 36,37 32,32" fill="#ffe888" stroke="#6624a8" stroke-width="1"/>
        <polygon points="77,46 81,51 77,56 73,51" fill="#ffe888" stroke="#6624a8" stroke-width="1"/>
        ${raidSkillGlint(77, 19, 7.5)} ${raidSkillGlint(54, 41, 5, '#ffffff')}
      `,
    },

    // 26. Землетрясение — jagged magma-gold tectonic fracture splitting beveled stone slabs
    earthquake: {
      p: 'earth',
      art: (u) => `
        <polygon points="23,49 33,26 51,33 45,58 27,61" fill="#6e5a3a" stroke="#241a0a" stroke-width="1.8" stroke-linejoin="round"/>
        <polygon points="33,26 51,33 43,44 28,39" fill="#b89b66" opacity="0.75"/>
        <polygon points="61,27 84,31 89,51 67,60 58,42" fill="#5e4c30" stroke="#241a0a" stroke-width="1.8" stroke-linejoin="round"/>
        <polygon points="61,27 84,31 76,42 60,38" fill="#a88b58" opacity="0.75"/>
        <path d="M56 16 L47.5 34 L59.5 41 L46.5 68 L64.5 43 L54 35 L63.5 18 Z" fill="#ffe86b" stroke="#ff5218" stroke-width="1.6" stroke-linejoin="round"/>
        <polygon points="39,18 45,22 38,26" fill="#e2c57e" stroke="#2e200a" stroke-width="1"/>
        <polygon points="73,18 80,22 72,26" fill="#e2c57e" stroke="#2e200a" stroke-width="1"/>
        ${raidSkillGlint(56, 39, 7, '#fffbe0')}
      `,
    },

    // 27. Взрывная броня — steel shield erupting in fiery radial shockwave
    explosivearmour: {
      p: 'crimson',
      art: (u) => `
        <polygon points="56,14 63,24 77,18 75,31 89,37 78,46 86,58 72,57 68,69 56,62 44,69 40,57 26,58 34,46 23,37 37,31 35,18 49,24" fill="#ff942e" stroke="#631206" stroke-width="1.4" stroke-linejoin="round"/>
        <polygon points="56,20 62,28 73,24 71,34 82,39 73,45 78,54 67,53 56,63 45,53 34,54 39,45 30,39 41,34 39,24 50,28" fill="#ffea7c"/>
        <path d="M56 26 L70 31 V44 C70 52, 63 58, 56 61 C49 58, 42 52, 42 44 V31 Z" fill="url(#${u}sv)" stroke="#240b08" stroke-width="2"/>
        <path d="M56 30 L66 33.8 V43.5 C66 49.5, 61 54, 56 56.5 C51 54, 46 49.5, 46 43.5 V33.8 Z" fill="#c92418" stroke="url(#${u}eg)" stroke-width="1.3"/>
        <polygon points="56,35 61,43 56,51 51,43" fill="#fff6b8"/>
        ${raidSkillGlint(56, 43, 6)}
      `,
    },

    // 28. Огненный шар — dynamic swirling solar fireball with flame licks & glowing embers
    fireball: {
      p: 'crimson',
      art: (u) => `
        <path d="M62 15 C61 23, 76 23, 82 37 C86 51, 74 66, 56 67 C37 68, 27 55, 29 41 C31 31, 38 27, 40 19 C44 26, 49 23, 52 17 C55 22, 58 17, 62 15 Z" fill="#ff4214" stroke="#5c0d04" stroke-width="1.8" stroke-linejoin="round"/>
        <path d="M58 22 C63 30, 75 33, 74 45 C73 56, 65 63, 55 63 C43 63, 37 55, 38 45 C39 36, 46 33, 47 26 C51 31, 55 29, 58 22 Z" fill="#ffaa22"/>
        <path d="M44 45 C44 37, 52 33, 58 36 C64 39, 67 47, 63 54 C59 60, 49 60, 45 53 Z" fill="#fff296"/>
        <circle cx="55" cy="47" r="7.5" fill="#ffffff"/>
        <circle cx="30" cy="26" r="2.8" fill="#ffd966"/>
        <circle cx="83" cy="29" r="2.6" fill="#ffd966"/>
        <circle cx="81" cy="57" r="2.3" fill="#ff9e38"/>
        ${raidSkillGlint(55, 47, 7.5)}
      `,
    },

    // 29. Зелье маны — faceted sapphire crystal star-flask matching raid_potion_mana.png
    mana: {
      p: 'frost',
      art: (u) => `
        <path d="M49 23 H63 V29 C74 35, 79 44, 76 54 C73 62, 65 66, 56 66 C47 66, 39 62, 36 54 C33 44, 38 35, 49 29 Z" fill="#1464d4" stroke="#c8f0ff" stroke-width="2" stroke-linejoin="round"/>
        <polygon points="56,31 71,44 65,61 47,61 41,44" fill="#32a4ff" opacity="0.72"/>
        <path d="M41 44 L56 31 L71 44 M47 61 L56 31 L65 61" stroke="#b8eeff" stroke-width="1.1" opacity="0.6"/>
        <rect x="47" y="18" width="18" height="6" rx="2.5" fill="url(#${u}eg)" stroke="#3d2205" stroke-width="1.4"/>
        <rect x="49" y="23.5" width="14" height="5" rx="1" fill="url(#${u}eg)" stroke="#3d2205" stroke-width="1.2"/>
        <polygon points="56,39 58.6,45.5 65.5,46.2 60.2,50.8 61.8,57.5 56,54 50.2,57.5 51.8,50.8 46.5,46.2 53.4,45.5" fill="#e8fbff" stroke="#0d3d85" stroke-width="1.1"/>
        ${raidSkillGlint(43, 39, 5.5)} ${raidSkillGlint(68, 56, 4.5)}
      `,
    },

    // 30. Взлом — razor-sharp curved twin crescent battleaxe cleaving a vertical energy beam
    hack: {
      p: 'blade',
      art: (u) => `
        <path d="M56 15 V69" stroke="#ff8248" stroke-width="9" stroke-linecap="round" opacity="0.26"/>
        <rect x="53" y="17" width="6" height="51" rx="2.2" fill="url(#${u}eg)" stroke="#422006" stroke-width="1.5"/>
        <path d="M53 26 C44 25, 34 21, 26 17 C29 29, 29 41, 26 53 C34 49, 44 45, 53 44 Z" fill="url(#${u}sv)" stroke="#241010" stroke-width="1.9" stroke-linejoin="round"/>
        <path d="M59 26 C68 25, 78 21, 86 17 C83 29, 83 41, 86 53 C78 49, 68 45, 59 44 Z" fill="url(#${u}sv)" stroke="#241010" stroke-width="1.9" stroke-linejoin="round"/>
        <path d="M28 21 C31 30, 31 40, 28 49 M84 21 C81 30, 81 40, 84 49" fill="none" stroke="#ffffff" stroke-width="2.1" stroke-linecap="round"/>
        <polygon points="56,28 61,35 56,42 51,35" fill="#ff5432" stroke="#fff2bc" stroke-width="1.2"/>
        ${raidSkillGlint(84, 21, 5.5)} ${raidSkillGlint(28, 21, 5)}
      `,
    },

    // 31. Воллей — three sleek fanned war-arrows in diagonal volley
    volley: {
      p: 'blade',
      art: (u) => `
        <g stroke-linecap="round" stroke-linejoin="round">
          <path d="M28 53 L64 19 M34 61 L78 24 M45 65 L84 35" stroke="#2b0f0a" stroke-width="5.2"/>
          <path d="M28 53 L64 19 M34 61 L78 24 M45 65 L84 35" stroke="url(#${u}eg)" stroke-width="2.6"/>
          <polygon points="68,16 55,20 62,28" fill="url(#${u}sv)" stroke="#26100a" stroke-width="1.3"/>
          <polygon points="82,21 69,25 76,33" fill="url(#${u}sv)" stroke="#26100a" stroke-width="1.3"/>
          <polygon points="88,32 75,35 81,44" fill="url(#${u}sv)" stroke="#26100a" stroke-width="1.3"/>
          <path d="M24 51 L31 51 L29 58 M30 59 L37 59 L35 66 M41 63 L48 63 L46 70" fill="none" stroke="#ffd880" stroke-width="2.2"/>
        </g>
        ${raidSkillGlint(82, 22, 6)} ${raidSkillGlint(67, 17, 4.5)}
      `,
    },

    // 32. Мазохизм — cursed barbed gold coin cracking into a dark horned crimson skull
    masochism: {
      p: 'earth',
      art: (u) => `
        <circle cx="56" cy="43" r="22" fill="url(#${u}eg)" stroke="#421c06" stroke-width="2"/>
        <circle cx="56" cy="43" r="17.5" fill="#2e110c" stroke="#ff8248" stroke-width="1.3"/>
        <path d="M40 31 C33 25, 32 18, 39 16 C39 21, 43 24, 48 27" fill="url(#${u}eg)" stroke="#3d1706" stroke-width="1.4"/>
        <path d="M72 31 C79 25, 80 18, 73 16 C73 21, 69 24, 64 27" fill="url(#${u}eg)" stroke="#3d1706" stroke-width="1.4"/>
        <path d="M44 41 C44 31, 68 31, 68 41 C68 47, 64 51, 62 56 H50 C48 51, 44 47, 44 41 Z" fill="#e8d6b2" stroke="#36140a" stroke-width="1.7"/>
        <path d="M48 41 L54 43 L49 47 Z M64 41 L58 43 L63 47 Z" fill="#ff2e24"/>
        <path d="M52 52 V57 M56 52 V57 M60 52 V57" stroke="#36140a" stroke-width="1.5"/>
        <path d="M25 43 L34 39 L34 47 Z M87 43 L78 39 L78 47 Z" fill="url(#${u}sv)" stroke="#36140a" stroke-width="1.1"/>
        ${raidSkillGlint(56, 25, 5, '#ffe888')}
      `,
    },

    // 33. Желание — radiant shooting wish-star comet with golden stardust
    wish: {
      p: 'holy',
      art: (u) => `
        <path d="M22 61 C36 56, 46 49, 56 38" fill="none" stroke="#ffdc6b" stroke-width="11" stroke-linecap="round" opacity="0.28"/>
        <path d="M23 59 C36 54, 47 47, 57 38" fill="none" stroke="#fff4bd" stroke-width="4.5" stroke-linecap="round"/>
        <path d="M28 50 C38 46, 46 41, 54 35" fill="none" stroke="#ffe078" stroke-width="2.2" stroke-linecap="round" opacity="0.85"/>
        <polygon points="64,15 69,31 86,33 73,43 77,60 64,50 51,60 55,43 42,33 59,31" fill="url(#${u}eg)" stroke="#522e06" stroke-width="1.7" stroke-linejoin="round"/>
        <polygon points="64,22 67.5,33 79,34.5 70,41.5 73,53 64,46 55,53 58,41.5 49,34.5 60.5,33" fill="#fffbe6"/>
        <circle cx="33" cy="42" r="2.3" fill="#fff8d6"/>
        <circle cx="44" cy="61" r="2.2" fill="#ffe078"/>
        ${raidSkillGlint(64, 38, 7.5)} ${raidSkillGlint(35, 31, 4.5, '#fff8c8')}
      `,
    },

    // 34. Берсерк — twin razor-curved crossed war-axes over pulsing blood-ruby diamond
    berserk: {
      p: 'crimson',
      art: (u) => `
        <path d="M30 62 L76 20 M82 62 L36 20" stroke="#361206" stroke-width="6.5" stroke-linecap="round"/>
        <path d="M30 62 L76 20 M82 62 L36 20" stroke="url(#${u}eg)" stroke-width="3.6" stroke-linecap="round"/>
        <path d="M37 22 C29 22, 24 27, 23 36 C28 38, 34 42, 38 47 L48 34 C45 29, 41 25, 37 22 Z" fill="url(#${u}sv)" stroke="#2b0808" stroke-width="1.8" stroke-linejoin="round"/>
        <path d="M75 22 C83 22, 88 27, 89 36 C84 38, 78 42, 74 47 L64 34 C67 29, 71 25, 75 22 Z" fill="url(#${u}sv)" stroke="#2b0808" stroke-width="1.8" stroke-linejoin="round"/>
        <path d="M27 33 C29 28, 32 25, 36 24 M85 33 C83 28, 80 25, 76 24" fill="none" stroke="#ff5c42" stroke-width="2.2" stroke-linecap="round"/>
        <polygon points="56,28 66,42 56,57 46,42" fill="#e61c38" stroke="#ffe6a0" stroke-width="2" stroke-linejoin="round"/>
        <polygon points="56,33 62,42 56,51 50,42" fill="#ff8ca0" opacity="0.78"/>
        ${raidSkillGlint(56, 38, 6.5)}
      `,
    },

    // 35. Руна — luminous magitech arcane transmutation rune circle
    rune: {
      p: 'amethyst',
      art: (u) => `
        <ellipse cx="56" cy="42" rx="30" ry="22" fill="none" stroke="url(#${u}eg)" stroke-width="2.6"/>
        <ellipse cx="56" cy="42" rx="23" ry="16" fill="none" stroke="#d896ff" stroke-width="1.5" stroke-dasharray="5 3"/>
        <polygon points="56,19 78,54 34,54" fill="none" stroke="#fff2b8" stroke-width="2.3" stroke-linejoin="round"/>
        <polygon points="56,65 34,30 78,30" fill="none" stroke="#88f2ff" stroke-width="2.1" stroke-linejoin="round" opacity="0.9"/>
        <circle cx="56" cy="42" r="7.5" fill="#a84cff" stroke="#ffffff" stroke-width="1.8"/>
        <circle cx="56" cy="19" r="3.4" fill="#ffe888" stroke="#3d175c" stroke-width="1"/>
        <circle cx="78" cy="54" r="3.4" fill="#ffe888" stroke="#3d175c" stroke-width="1"/>
        <circle cx="34" cy="54" r="3.4" fill="#ffe888" stroke="#3d175c" stroke-width="1"/>
        ${raidSkillGlint(56, 42, 6.5)}
      `,
    },

    // 36. Жадность — fierce razor-hooked steel talons seizing a blazing ruby-inlaid gold coin
    greed: {
      p: 'gold',
      art: (u) => `
        <circle cx="56" cy="43" r="16.5" fill="url(#${u}eg)" stroke="#4a2606" stroke-width="2"/>
        <circle cx="56" cy="43" r="11.5" fill="none" stroke="#fff6cc" stroke-width="1.3"/>
        <polygon points="56,34 62,43 56,52 50,43" fill="#e82438" stroke="#fff4bd" stroke-width="1.4"/>
        <path d="M23 30 C27 19, 39 18, 49 27 C41 25, 34 27, 32 34 Z" fill="url(#${u}sv)" stroke="#381b04" stroke-width="1.5" stroke-linejoin="round"/>
        <path d="M22 45 C26 37, 35 36, 43 41 C36 42, 32 45, 31 51 Z" fill="url(#${u}sv)" stroke="#381b04" stroke-width="1.5" stroke-linejoin="round"/>
        <path d="M29 58 C34 51, 41 50, 47 54 C41 56, 37 59, 36 64 Z" fill="url(#${u}sv)" stroke="#381b04" stroke-width="1.5" stroke-linejoin="round"/>
        <path d="M89 30 C85 19, 73 18, 63 27 C71 25, 78 27, 80 34 Z" fill="url(#${u}sv)" stroke="#381b04" stroke-width="1.5" stroke-linejoin="round"/>
        <path d="M90 45 C86 37, 77 36, 69 41 C76 42, 80 45, 81 51 Z" fill="url(#${u}sv)" stroke="#381b04" stroke-width="1.5" stroke-linejoin="round"/>
        <path d="M83 58 C78 51, 71 50, 65 54 C71 56, 75 59, 76 64 Z" fill="url(#${u}sv)" stroke="#381b04" stroke-width="1.5" stroke-linejoin="round"/>
        ${raidSkillGlint(56, 39, 6)}
      `,
    },

    // 37. Последний рубеж — sweeping golden phoenix wings cradling an emerald-and-flame revival aegis
    laststand: {
      p: 'emerald',
      art: (u) => `
        <path d="M56 55 C41 57, 27 47, 20 29 C28 31, 35 35, 41 40 C34 29, 32 22, 36 18 C43 23, 48 31, 52 38 Z" fill="url(#${u}eg)" stroke="#472706" stroke-width="1.5" stroke-linejoin="round"/>
        <path d="M56 55 C71 57, 85 47, 92 29 C84 31, 77 35, 71 40 C78 29, 80 22, 76 18 C69 23, 64 31, 60 38 Z" fill="url(#${u}eg)" stroke="#472706" stroke-width="1.5" stroke-linejoin="round"/>
        <path d="M56 22 L70 29.5 V44 C70 54, 63 60.5, 56 64.5 C49 60.5, 42 54, 42 44 V29.5 Z" fill="#12a85e" stroke="#fff4bd" stroke-width="2.1" stroke-linejoin="round"/>
        <path d="M56 27 L65 32.5 V43.5 C65 50, 61 55, 56 58.5 C51 55, 47 50, 47 43.5 V32.5 Z" fill="#32df88" opacity="0.65"/>
        <path d="M56 32 C60 38, 63 42, 61 48 C59.5 52, 52.5 52, 51 48 C49 42, 52 38, 56 32 Z" fill="#ffe878" stroke="#fffdf0" stroke-width="1.2"/>
        <circle cx="56" cy="45" r="2.6" fill="#ffffff"/>
        ${raidSkillGlint(56, 19, 6.5, '#fff8c8')}
      `,
    },

    // 38. Рывок — armored war-spearhead surging forward with golden velocity streaks
    charge: {
      p: 'blade',
      art: (u) => `
        <path d="M22 30 H44 M18 42 H40 M22 54 H44" stroke="url(#${u}eg)" stroke-width="3.4" stroke-linecap="round"/>
        <path d="M26 24 H36 M26 60 H36" stroke="#fff2b8" stroke-width="2" stroke-linecap="round" opacity="0.82"/>
        <polygon points="89,42 53,21 61,42 53,63" fill="url(#${u}sv)" stroke="#2b100b" stroke-width="2.1" stroke-linejoin="round"/>
        <polygon points="86,42 57,26 64,42 57,58" fill="#ffffff" opacity="0.48"/>
        <polygon points="73,42 41,25 48,42 41,59" fill="url(#${u}eg)" stroke="#452006" stroke-width="1.8" stroke-linejoin="round"/>
        <path d="M51 42 H84" stroke="#ffffff" stroke-width="2" stroke-linecap="round"/>
        ${raidSkillGlint(87, 42, 7.5)}
      `,
    },

    // 39. Спешка — golden winged chronos hourglass glowing with cyan-gold energy
    haste: {
      p: 'holy',
      art: (u) => `
        <path d="M39 40 C28 38, 22 31, 20 24 C27 26, 33 28, 39 33 M73 40 C84 38, 90 31, 92 24 C85 26, 79 28, 73 33" fill="url(#${u}eg)" stroke="#472806" stroke-width="1.5" stroke-linejoin="round"/>
        <rect x="40" y="18" width="32" height="5" rx="2" fill="url(#${u}eg)" stroke="#472806" stroke-width="1.4"/>
        <rect x="40" y="61" width="32" height="5" rx="2" fill="url(#${u}eg)" stroke="#472806" stroke-width="1.4"/>
        <path d="M44 23 H68 C68 33, 60 39, 56 42 C60 45, 68 51, 68 61 H44 C44 51, 52 45, 56 42 C52 39, 44 33, 44 23 Z" fill="#193b52" stroke="#d8f4ff" stroke-width="1.8" stroke-linejoin="round"/>
        <polygon points="48,26 64,26 56,38" fill="#ffe066"/>
        <polygon points="56,47 64,58 48,58" fill="#5ceeff"/>
        <path d="M56 39 V47" stroke="#ffffff" stroke-width="2" stroke-linecap="round"/>
        ${raidSkillGlint(56, 42, 6)}
      `,
    },

    // 40. Шок — jagged electric cryo-plasma thunderbolt with golden sparks
    shock: {
      p: 'frost',
      art: (u) => `
        <path d="M23 38 C32 23, 80 23, 89 38" fill="none" stroke="#8ef4ff" stroke-width="2.2" stroke-dasharray="6 4" opacity="0.82"/>
        <polygon points="62,14 37,44 53,44 47,70 76,36 59,36" fill="#5eeeff" stroke="#ffffff" stroke-width="2" stroke-linejoin="round"/>
        <polygon points="60,20 43,41 56,41 51,61 70,39 57,39" fill="#ffffff" opacity="0.84"/>
        <path d="M26 48 L35 43 M78 27 L87 22 M76 52 L85 56" stroke="#ffe47a" stroke-width="2.4" stroke-linecap="round"/>
        ${raidSkillGlint(61, 17, 6.5)} ${raidSkillGlint(48, 67, 4.5)}
      `,
    },

    // 41. Благоговение — radiant sacred golden-violet mystic eye with celestial rays
    awe: {
      p: 'amethyst',
      art: (u) => `
        <path d="M24 42 C34 25, 78 25, 88 42 C78 59, 34 59, 24 42 Z" fill="#1e0b3a" stroke="url(#${u}eg)" stroke-width="2.6" stroke-linejoin="round"/>
        <path d="M30 42 C39 30, 73 30, 82 42 C73 54, 39 54, 30 42 Z" fill="#6326b0" opacity="0.72"/>
        <circle cx="56" cy="42" r="12" fill="url(#${u}eg)" stroke="#fff8d6" stroke-width="1.6"/>
        <circle cx="56" cy="42" r="7.5" fill="#46e8ff"/>
        <polygon points="56,35 59,42 56,49 53,42" fill="#17062e"/>
        <circle cx="52.5" cy="38.5" r="2.2" fill="#ffffff"/>
        <path d="M56 17 V23 M42 20 L45 25 M70 20 L67 25 M56 61 V67 M42 64 L45 59 M70 64 L67 59" stroke="#ffe68c" stroke-width="2.2" stroke-linecap="round"/>
        ${raidSkillGlint(56, 42, 6, '#ffffff', 0.8)}
      `,
    },

    // 42. Перенастройка — 4-element magitech prism astrolabe compass
    retune: {
      p: 'prism',
      art: (u) => `
        <circle cx="56" cy="42" r="21" fill="#092428" stroke="url(#${u}eg)" stroke-width="2.6"/>
        <circle cx="56" cy="42" r="14.5" fill="none" stroke="#8cf2e0" stroke-width="1.3" stroke-dasharray="5 3"/>
        <path d="M56 15 L60 27 L56 24 L52 27 Z" fill="#ff6633" stroke="#fff0b8" stroke-width="1"/>
        <path d="M83 42 L71 46 L74 42 L71 38 Z" fill="#d8ecff" stroke="#ffffff" stroke-width="1"/>
        <path d="M56 69 L52 57 L56 60 L60 57 Z" fill="#46e078" stroke="#e2ffe8" stroke-width="1"/>
        <path d="M29 42 L41 38 L38 42 L41 46 Z" fill="#32b4ff" stroke="#d8f6ff" stroke-width="1"/>
        <polygon points="56,28 69,42 56,56 43,42" fill="url(#${u}eg)" stroke="#2b1905" stroke-width="1.6" stroke-linejoin="round"/>
        <polygon points="56,33 64,42 56,51 48,42" fill="#78fff0" stroke="#ffffff" stroke-width="1.2"/>
        <circle cx="56" cy="42" r="3.2" fill="#ffffff"/>
        ${raidSkillGlint(56, 42, 6.5)}
      `,
    },
  };

  let raidSkillIconUid = 0;

  function raidModuleIcon(id) {
    const spec = RAID_SKILL_ICONS[id] || RAID_SKILL_ICONS.enchant;
    const pal = RAID_SKILL_PALETTES[spec.p] || RAID_SKILL_PALETTES.gold;
    const u = 'rs' + (++raidSkillIconUid) + '_';
    const art = spec.art(u);
    const shadow = art.replace(/fill="(?!none")[^"]*"/g, 'fill="#040709"').replace(/stroke="(?!none")[^"]*"/g, 'stroke="#040709"');
    return `<svg class="module-glyph" data-skill-art="${id}" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 112 84" aria-hidden="true">
    <defs>
      <linearGradient id="${u}gd" x1="10%" y1="4%" x2="90%" y2="96%">
        <stop offset="0%" stop-color="#fffbe0"/>
        <stop offset="16%" stop-color="#f5cf76"/>
        <stop offset="42%" stop-color="#b8822b"/>
        <stop offset="62%" stop-color="#57350e"/>
        <stop offset="82%" stop-color="#d4a348"/>
        <stop offset="100%" stop-color="#ffefb8"/>
      </linearGradient>
      <linearGradient id="${u}gi" x1="88%" y1="94%" x2="12%" y2="6%">
        <stop offset="0%" stop-color="#ffeda8"/>
        <stop offset="32%" stop-color="#a87526"/>
        <stop offset="68%" stop-color="#281606"/>
        <stop offset="100%" stop-color="#613e14"/>
      </linearGradient>
      <linearGradient id="${u}eg" x1="14%" y1="4%" x2="86%" y2="96%">
        <stop offset="0%" stop-color="#fffbdc"/>
        <stop offset="28%" stop-color="#ffd866"/>
        <stop offset="62%" stop-color="#d68c1c"/>
        <stop offset="88%" stop-color="#8c4e0a"/>
        <stop offset="100%" stop-color="#f9cc5c"/>
      </linearGradient>
      <linearGradient id="${u}sv" x1="10%" y1="4%" x2="90%" y2="96%">
        <stop offset="0%" stop-color="#ffffff"/>
        <stop offset="26%" stop-color="#dff0fa"/>
        <stop offset="58%" stop-color="#8da9bf"/>
        <stop offset="85%" stop-color="#4c6478"/>
        <stop offset="100%" stop-color="#d6e8f5"/>
      </linearGradient>
      <radialGradient id="${u}bg" cx="50%" cy="38%" r="58%">
        <stop offset="0%" stop-color="${pal.c0}"/>
        <stop offset="34%" stop-color="${pal.c1}"/>
        <stop offset="76%" stop-color="${pal.c2}"/>
        <stop offset="100%" stop-color="${pal.c3}"/>
      </radialGradient>
      <linearGradient id="${u}gl" x1="48%" y1="0%" x2="52%" y2="100%">
        <stop offset="0%" stop-color="#ffffff" stop-opacity="0.6"/>
        <stop offset="46%" stop-color="#fff9eb" stop-opacity="0.18"/>
        <stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>
      </linearGradient>
      <clipPath id="${u}cl">
        <ellipse cx="56" cy="42" rx="43.8" ry="30.4"/>
      </clipPath>
    </defs>
    <ellipse cx="56" cy="44.2" rx="52.5" ry="38" fill="#000000" opacity="0.48"/>
    <ellipse cx="56" cy="42" rx="51.2" ry="37" fill="url(#${u}gd)" stroke="#170e04" stroke-width="1.8"/>
    <ellipse cx="56" cy="42" rx="47.2" ry="33.4" fill="#160f08" stroke="#36220a" stroke-width="0.9"/>
    <ellipse cx="56" cy="42" rx="45.8" ry="32.2" fill="url(#${u}gi)"/>
    <g clip-path="url(#${u}cl)">
      <ellipse cx="56" cy="42" rx="44.2" ry="30.8" fill="url(#${u}bg)"/>
      <path d="M10 17 L56 9 L36 75 L8 66 Z" fill="#ffffff" opacity="0.07"/>
      <ellipse cx="56" cy="42" rx="39.8" ry="26.8" fill="none" stroke="${pal.acc}" stroke-width="0.8" stroke-dasharray="3 4" opacity="0.28"/>
      <ellipse cx="56" cy="42" rx="35.5" ry="23.2" fill="none" stroke="${pal.rim}" stroke-width="0.7" opacity="0.22"/>
      <g transform="translate(0, 2.3)" opacity="0.46">${shadow}</g>
      <g>${art}</g>
      <path d="M14 34.5 C20 10.5, 92 10.5, 98 34.5 C83 23.5, 29 23.5, 14 34.5 Z" fill="url(#${u}gl)"/>
      <path d="M22 19 C37 11, 75 11, 90 19" fill="none" stroke="#ffffff" stroke-width="1.6" stroke-linecap="round" opacity="0.65"/>
      <path d="M26 65.8 C41 72.5, 71 72.5, 86 65.8" fill="none" stroke="${pal.rim}" stroke-width="1.7" stroke-linecap="round" opacity="0.6"/>
    </g>
    <ellipse cx="56" cy="42" rx="44.1" ry="30.7" fill="none" stroke="#fff4c4" stroke-width="0.95" opacity="0.48"/>
    <path d="M17 22.5 C32 7, 80 7, 95 22.5" fill="none" stroke="#fffce8" stroke-width="1.5" stroke-linecap="round" opacity="0.76"/>
    <g fill="url(#${u}gd)" stroke="#241506" stroke-width="1.1" stroke-linejoin="round">
      <polygon points="56,2 60.8,6.8 56,11.4 51.2,6.8"/>
      <polygon points="56,72.6 60.8,77.2 56,82 51.2,77.2"/>
      <polygon points="2,42 7,37.5 11.8,42 7,46.5"/>
      <polygon points="110,42 105,37.5 100.2,42 105,46.5"/>
    </g>
    <circle cx="56" cy="6.8" r="1.45" fill="#fffce8"/>
    <circle cx="56" cy="77.2" r="1.25" fill="#ffe89e"/>
    <circle cx="7" cy="42" r="1.35" fill="#fffce8"/>
    <circle cx="105" cy="42" r="1.35" fill="#fffce8"/>
  </svg>`;
  }

  function raidEmptyModuleIcon() {
    const u = 'rse' + (++raidSkillIconUid) + '_';
    return `<svg class="module-glyph-empty" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 112 84" aria-hidden="true">
    <defs>
      <linearGradient id="${u}gd" x1="12%" y1="6%" x2="88%" y2="94%">
        <stop offset="0%" stop-color="#b5a676"/>
        <stop offset="48%" stop-color="#5d5239"/>
        <stop offset="100%" stop-color="#9a8b60"/>
      </linearGradient>
      <radialGradient id="${u}bg" cx="50%" cy="44%" r="58%">
        <stop offset="0%" stop-color="#1a3531"/>
        <stop offset="70%" stop-color="#0b1f1e"/>
        <stop offset="100%" stop-color="#061212"/>
      </radialGradient>
    </defs>
    <ellipse cx="56" cy="42" rx="50.8" ry="36.6" fill="url(#${u}gd)" stroke="#0c1716" stroke-width="1.6" opacity="0.68"/>
    <ellipse cx="56" cy="42" rx="45.6" ry="31.8" fill="url(#${u}bg)" stroke="#2b423c" stroke-width="1.2"/>
    <ellipse cx="56" cy="42" rx="39.2" ry="26.2" fill="none" stroke="#99b394" stroke-width="0.9" stroke-dasharray="3 5" opacity="0.34"/>
    <path d="M24 19.5 C39 12.5, 73 12.5, 88 19.5" fill="none" stroke="#d9e8d2" stroke-width="1.1" stroke-linecap="round" opacity="0.22"/>
    <path d="M56 30 V54 M44 42 H68" stroke="#a6bfa0" stroke-width="2.6" stroke-linecap="round" opacity="0.54"/>
    <circle cx="56" cy="6.8" r="1.4" fill="#c9ba89" opacity="0.55"/>
    <circle cx="56" cy="77.2" r="1.4" fill="#c9ba89" opacity="0.45"/>
    <circle cx="7" cy="42" r="1.4" fill="#c9ba89" opacity="0.55"/>
    <circle cx="105" cy="42" r="1.4" fill="#c9ba89" opacity="0.55"/>
  </svg>`;
  }
  // The six silhouettes correspond to the existing weapon name pool, NOT
  // new item types. A shop card resolves its OWN item; a tile resolves the worn
  // item. Existing suffixIndex/appearance already survive save-v1. No new roll,
  // saved field, rarity or stat is needed to keep the two views in agreement.
  const RAID_WEAPON_ART = [
    { id: 'techno-rusted-blade-v2', src: 'data:image/webp;base64,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' },
    { id: 'techno-sabre-v2', src: 'data:image/webp;base64,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' },
    { id: 'techno-mace-v2', src: 'data:image/webp;base64,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' },
    { id: 'techno-hammer-v2', src: 'data:image/webp;base64,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' },
    { id: 'techno-axe-v2', src: 'data:image/webp;base64,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' },
    { id: 'techno-guard-blade-v2', src: 'data:image/webp;base64,UklGRvQrAABXRUJQVlA4WAoAAAAQAAAAPwEAPwEAQUxQSD0NAAABr6egbRtp7vjT3r0AIiLL33EQCLSt2GlTEO8SjttGcqTuUp/LP2Tfwtw/ov8TUP+Y9jwUPAt4vYeED5ewcBi+PAAWLFiwYMGCBQsWLJwGQ2YqSfZmpkjYq5luuoRJdykTZmaPZwNveov3p8BCW0ZzWviitW1522zbtv08L8mO44YZ7kAxycPMzMzMzMzMzMzMTGs3M/NdpnDSu0HbVxtLus59IbYsnSs6Ho6ICeD//f9fq5ViRwlyChslSP1P/rpNQSMV+ke+/QXtZ5AiRqbZ8il/d7ltn5uJV6mw6a5P+sy2Xb7afk1K0SLMwvt9xUf3Wbp5Y/nN21GsCDN36js/ia6ktr3ZfnUSkSpg7u7P/4qFkXOiXV5+6ggpUATW8c/9ssWuNALaG+1P9QhU4XTgAz9nsYyaxO3J73a6pDBJCe3/+K/e51HOrO3e4WdxkCjDro/6qvsY5My6LndeHCZiNMHOj/vnE6zmxJjDZudlYlSZ/KF/07arKYsxPTx8oVOIZNj/nU+1V280YuxBf/UiEapM+tB/bZevLbeM36WnHioKkAwHvutye2WpbTfgYf6aJeJTQife746SMxsV+SMb4jONOPAe78lqZuO5+4DDhGcSnHifbc6ZjafuwIdmFBxZHHqP9+RWZoKCT9iCCE01cOK9tjtnJpkH73jcJjRT5tD3vDurYqLJez+sJ4VGFh/4Ty2px0QlPn53EZHZg895a3s1MeHm1t0nMYEpsfnenSgz4cTWD5lLKTDUsf/gViQmncvHHehEZHDkCKuZiefhsffKDozUse0wiIkn+h8yZxGWyemeeSSmMHqPwxZRKXPgwF4kJp+7kx+QCAuZI0fpxBST+ehtRQRlY/YeoiSmmbv7jpuoVMd9W5GYZuoOnUpZQSF2HdsJYprZc+/THyVi0ixubxDTFe981CIklXjObjox3Tw4dBJiQiZtBzHdxM5361tEpMQ7NJgpy5zc0omIbDrmdpM0rTy89wQiIufywkFhpp109H0bhYS7sncTYtqpcHJhmAjIxF3zmAq7+44hAtLMHwZNL/u+95YCIom9c5jpp9J7x/mRCEfBoS3UKHNqrzPx2LGpD6ogd4ffERQOIt07T5WZA++WEQGpOapUx3tuGSaiUdSbypF3QoRjMaAqxMFTSuGg7vhOai1zh/JIhGOzT6WOBMcXOhGPxtQpckKErcyRgyTisYDqgPmthKMATJ2JHcdRMGiETL39nYQD7NyCavHm/ZhgTOzdQa0yBzaNUizYIEotMN+JcLQt6pR5zrGSY0GAMNXOz5tglEy9YscJFA1eWq1HzC0iYtFcOE2phoU7iUdJolJZ23JJsWBsTK3Wjg4RjMWYWsX+YyUTiuL6CqZWk/LIhGLm2dPkasyuBRKhmMHUK/onEKGY2GyKaxECE4xF1Gu2JqJRzz6FqxHNKaJRXLyRSjVFCSsYIPVMrSUJE482tYp7IcWCkEWtMnOHTDiKqsuIUDSYis1BkaJBVCz69xCOomr3CcZEZ1VUtgmHguGJoalWvuM4UiiI4ZlUj2ARE4oGIaodZVEIxYaqZQ4hhUIPp6rYsasQjaJiY0woCkAV3a5YAFG7iESjRNUGE4kGUE0WsajUicht9MgSkdvLXAJXZAWDwSSqFQYUCYJEvQITjKbuwu4+CgXJVYndu4nFQu3ahGMh5dpMLCq155CrEeGoOa7cEhUrHECFmkQwmtoNigVwXRLBmHjWuCIRjYX7r1OviMiyiqsRigcNOqoWcij0eOQidj0iGm3dIlGxw0HDjqpFMJa8/AhyLUKgYLCeHYmKRTA6uYjAtXj4KqUexQO0q1QrQrKj5qxwME9ecKlF3LGHHAsyg2WJajcdwYSiAYHrgUI0yJhKhSClWBCdMZUag00wXr0KqgURjoYL5EKtBkUDGEzsmmrlmHA9BgeDkUFULGJRadBRsUw0FnVPDlXqAQeDzMrbReAKcnYlBoeDDWDqNICjATCiTrFkUDAkbgpTZ2HlDDahaHPlkkyt5SrRKOiWEbXapGCwsajVAMXBIDqwK8FE5KUhplYLR4MZPUaiVgOKBhUj6vRa8ZiQ6zAmIBsBpkaLiDSrl6nVDB0QcGlZtXB6BUeDC3QN1V4iHjM1WygeeAxci8GOhkJ7meAtVGtQPOQroCpKAXA4mCtPYqrMhGQRA1RFyaNzHY6HfKtD1JmvtSgguJ9aRUc82gzbVIVtQjKRRJVqBDggeNsA11B09jwRWWiXqdPl+lARAYV6MwEpqpXBAaHzgyqclISIR+v6WaoUV5Yw8WhTqNJcu4SJR4HBNZA6FBDGVGkDiJCsQwkRkiUvPYk8PedyoYsJc2MoakxXLucSEmDqTJaISFlUacAEpGVqlIhKA/b0jEExkU2NgkRIqmw7kFEFrA5QQKih252GTN+s3A+OBzXI1CgwIhyVYB/1ytGgBJs/6kWoAq9hYlEJ5t/zd2+2NQhENCqJfPCTP6dZlYhXJQGHPvUzmmHKTN/Y4QDp4Kd/4uHiLKZvInLru3zHS1qGKVGhmNGVNDnxXr93oW2vp4YKDTcKnsEEpEkp8S3t1WvXV6jStG/BzN6Zo+87hyZE5sSbbt5s2zooHWL2Vi//7qWf3IsmlFj4ufZqHUZgZvDe8JM+ZfFb7hyhyTg/86pbTaFOMZPL+pLFruXiDTwZ9LrXNaMKhM1snrut+9xbaHiaSbu9QJmaRREzusqRHSoik7o8GdSg6XG2MKsn7poHKIUn0GQWdkzNsHQaz2rixGYEoEs3QBPQjrumZWESM3vhfGdud09QJuD+FqfppEEnyuw2yv/waq2BYQfWBuTeh24dTsU887jNLJ+7d3lHfBtix3GSxmP4vDO5Y4oFlkbM+ImP3FbSbbj0RTdW0zz4olyYosBkz3re8dENa8s0u0nr9fIz/77MNMyZW9jM+s3gg46XtZA40Qfdlvuw67F+YeKG0WnE7C94l81oDWR62yCR+uLeX7kLMfmCOxQBiDveCWsNMNsP0Z9r6H/hC1vMxEuig2JCMA/33cV6gr33AR/1l+fb62LS7sSZZ4hD+eT+0Tq37/v5z/zZB9v25gqTHrpPe5lIgHdZsNYRcGUXw5SZ8Kiba1/4hYs0gYDK4jbGTaTizATdkT3s623ffC9kQlEjjYVLEhPs3GNI78pf/i3kRDgamvUm2416XPZ+/uQvXzdIjYhIg6agOR7+mdenEzdfuEwWMakTeHLW0//xqjdwe1MKMZn41Q/q0sRGfvIhlDAUglJaeNNnO08oac/JknMxwsRlWWTiN9t/30GP4BQHN6EJjVYe/FD6RGdK73JHYbJFyz+Q5xQedF+YmfCoeejDmCM6m+4rP6CkCZV05auYU3Bk7/jmRkyK9vvpB4fofupYmRhd+yc559houk/4dBITH7X/uJiCAz5w3kyheehn2l4XHG8bMNXy0BOU0BDb81QsbBwYeXj4a5qpgNKmxcBQ8m8dsSbhcczBfSgo1B/++IdYbNxgrwOFVAjKPp/61YmNuwgjrwfYMdHjyE9utjYmzn3Vh7+QMhZ2SKDrmI2n0Ste/qq/fuMLrmZ5vcITZ3BAJDbsYto3nG1yyqvPfStjWAzAsWBk7PGKUhq9/sVA6SwuPtmprAXCJhQFZoOWb53+m797UBQAO12/f8T6Lpy7QYkDIzaswV9++Xf++Ug2azvx2BnZa+CmPIUUBBI2Hs+snn7VkppcGNPizFmy18BaeqDkIADMBkX3IL3cDc1Gl24O5TUY6k9eSgoBIzZo83RBndmw0eWHBsi3DTa97k/IESDkjYjrb8NmoqXhkacBiVKar0xJM59BhQ2qW+0kJm3x5LlhD8Pq/N9tI8184pbxeIX7n6Zjqqdf8al3q8tc+rX52a9w8xzjG+iSmGYpzdV93/OJe5de8FMPIGb/66TxEKgw5ZIHfq8ve+lf0hezv8HjWdRYmjSAnjtmfoHN+IVK1cgjM/ub8U3VMrO+2LhxTbN/YYIiVrPBG0CRkp9ZxWzQOFC0eqFlbIOJVLPybPZYSRYKFEhiXKsdiUAVNh7HrFzqHCcuTPA6KVByngSIOO2ueDytYaLU5vIleRwTrMZDxPiKFEEHeAyDcaD0Ob9Cx9gCEad9uotkr2exMooUmblCYszClYuYODV7DyHG9g0UJ4Lt2yiMKTCJGFXT65nnbKZonNvtGEmNVju2byOJcUWY5sS2d/7Ew3OI8eMkaf63X/DDn7qAWF8JGRQjqVn847bdnxDjUAATombl7YxGIo1TvH8rIkg7LgxImbHlxQOEidUOMhtMSX0VglT+56dUNiAG2ERpuvbpg+QNsO2oEnHy6Cclj5c8933fM+zHCS89dtrdWJJ/4G9GKcXJ0+/4zXk4Fnn08S88Wnpxghc+feTx/G4/1ncKlMT7f4g1jtm5Z5SIVOdj1xt663F4FyJYy1uvadgI8KDMH0Dhgs8+9tFDlFx6u+8jZpe+4zvA4gX3504hQ+/U+33QB775115YukLIGtTfdvMWibBVAhKxK/H//v+v0ABWUDggkB4AALBhAJ0BKkABQAE+LRSIQ6GhIRI+PGAYAsS0t3C7sIQZAgEtKrwN/z0MbcLnjfOG30n0AOlK8oDNIP6l+OPeZ/Rfxe83/xf6H+9fjz+93+g+LLEn1Nai/x37Sfi/7x+3ftH3g/LjUF/FP5Z/cv7X+3P5q/Og8b53fZ/9b1AvX36r/qf8D+8/+E9P3UC8I/8z7TfsA/l/91/0X5uf5H5a/7HgDfY/99/x/cC/mP9O/0v+d/dj/IfSL/Hf9z/Jf6b9z/Yp+bf3j/sf47/W/97/Of//8Af4x/OP8n/cP8//wv71///+t9wHrN/ZD2HP1C+f8YItz5DLz/wuRydSfhohbhufIZdTEvbd9HoUUaO68aWwoew1R/4XJrUW5zay0KnfmwEMX5kXBe1HwhTJsTnfEXThohbhuc2/zuH0PFm9b8fENtVwvop5EsG50J4VG4bnyGXn/EEyj5bM/momKFaT+ZXDmIqfAZukmwHDhbhufIZeaiRYrPRf2dqSBKozzZYfqH9WLj5wk5aljs+Qy8/8LfGwnsLlOuDtG1huOM7TwL26yPJOJJUfPUW58hl5+S4TpMM1U/azT0cehGShgKVQDH7i3Nef+FyawrZaHzjltUBFSIt1YjVSgz1DpEem0XP7m6nDRC3Dc5s3Vcriy6SVAVOC6pYj9caRsx2/wL8VervvP/C5NaYsJst/S5e9xqYAjtNyXO0Qv2crhSa6WQGSUW58hl1RpRRLjDlsB3YLUx68PhqQHuy9tgWwci4bnyGXUxklvXkGjs1AWEByd0q06GJPjS7nULfYKRzZ8hl5/4UxA1q003kEK34hq+PPD84IPJc/o3zsE7zkyLWcWqEkyG58hl5/3vWykJpX0E+6ErzVNnbEJMNbrVGUhBpRqya1FufIZdVJ4vTphnWHTj34dH861bqxje7fSWFCirJKLc+Qy81BdxdIeLhnEs/4c94kU+IYXv0ZaBwanDRCuTOwGFA1TYcRCLVn0IpqVk1qLc+Qy81mrL5nVDsXuQoLnZrWnwJqTHn/hcmtRbnx7gvg1OGiFuG57sAA/v/6v0AL7QHMO44xnLp010SXhCDKlgFPbrmyCMc95HUAd9hQCbT/C2k0ybd+9oiNiqTcPDoWY5q4pOQXXz4U/OZ807yZ7v5051XBvAgd5LdxwFG5NyaAIluiWFKYm+4arfgf8qMhdy1X8K4H2AMukDxVnzCIAWJjEzTScL7hdFbLbCd4yzcEZheSzA11O64wRKGNNnSWHWQekTJ/jl8edhpYUX4C6fEJrtu+TOOAafgSLbL3wa7WjwLrTNGi7x2LMSPL7xN79FJ8+nKktjwMtAGjwwKltEO67GbAoo8a6+ZGozLl4+LHKkYCM7m9exFKmSLc3EDu5W1lsl+pifRBXRBymT9uMSvfOog2NNLt8rT+GrItoeF+YCZje1JJ1w6sP6g+Xa5EuUeAAAG/JJJM33qCVA1fe1JjpIoxeJBRP8kc9ux8b2Gqj3CKC62l2H55N7+7E16HB86agGGV88xUALf8QCQOdLqo8WIzd4mnQgZHufisF3Dqto7KxofBDzM2/19ZQQOWef0T0eocCAiURvQvyz3pL2oV03BfgSXX6jds/BKHb731tIqi6vxKrpxwyh7EPl2d8KuXWjFNHyF32HtgsSjaaYU9yNVAbWJBJP0dZXE4zYQqbt8EC3R3Gzg2tmNfjhACRpdV7gqCQu79isOEUzhVN5Y0NSeddb4HROa8I8PX+1OZj89czmGiWbN4Jq4mbcIo2orCgjUxk4mHUhVING1y27OimeaEYnWu7bA0OcvnYPXojlJB0qAD2wIOWp+Irkq+EOjLJa+woF8JX0+vwlAOYQJCbp/itJDA+6/QF1yKmDWGtYQZtuN/yft0QAAMjDK1FsIB1U5Vtmu5Ea4fALpVQdGwJbdBMtyD7NOiT9+hCIDP2Coz0oi1vWXP/jUTUhQP/4xQsiAry9lQYJuyIO7WyVs/9t03/GIXw0fPS0ggColho01Go5GqgTfTnRt/3Ievn4ona/69jyvW3/r8heIeZFN7iNaxjR/mtWjx7Vvk+8YbZwb/7gde/jbn8Td87MLDHQI6kILX0u7n2qWZv77AZxmmYQY2k1S76PqplHJ8JuGgagCl3nkikkKlHt9je43LMMvKk+yNY68FlROKH0hsltEQsP2q566mxbkPOpse8EAkgfVmXahYctHOBol07O2oY8kiNgfwDeWXnpl3y/T02KMZ81B2hHow0PkiOn6BboeEq7/nYVDpLNLSnwOyfh9+2OnsHeIkSOVILTHcSC46rfG/GwFesPwhpzT4fBFpU81OIsu9+4oqPhrc8+abWTEI/ZVe/AmhFOzfBUQ+DTtIByfQxIXwNVy2XghxBuuwwxmU1EZaH5PsN3qMbM8MPfIRc0QSXRPl85924jdC/7gKzRsE24ZV2SSiMis3I+CTDlcpCvtuAA/ACgCM/dSxSvu5RPltVRm/6bZPIxzup/IKwXXNe4LEKLlDJvSjdgT/oNHYLAr2uSMRYv3pGv5jhcpCMFiPo41sOnZ+UfmyclpgQIqNg/KUaIXDyvDpb/4vgePvOcw+l0PMTPVNZFOzLj3dZGlZ47nEIFkd7V0iFoYMe7cuhYP4XfTPYouQ39Uf9qTvROviEHGcwmogF7DpUmFHKdSyENIB2ScOHMOMaqjOrAtD1xfhJC5xhu5XVRmyRqJO0VdrXXlQZObtj5lKo7B+w+t+AZtDFcz4RDp2SUS4MKbfPqwEbM6rn2Op4M//Jq9i9dl9Df6lOnHN7sH/CFz4ZvwTPYU/oTRZrw6oxUBUbdA+lz57MVrCweTW8tEPuxnwbv0M4S8DUXfdSBF25nB//ICh/pOlNVokbA7K/o4LmafSY+NsirjnG/uqZrGai5bYKKmWTu+lf5z6rLpetry0vE3N008nScLMvcXz1X6eGErMOlVywHqR23klYm0zd7sKh5uHjJZVUe41g58ZFlEDoRMvcY4C6Tt1/N6zZRHDr6bCSAO1CYk8vsEXHMBOmvMVJb1+Nf4i2cuoA8v8EY/i2/Trpcw89k5sDZMC+hiB4X6vPXU1z2SvRQWMUDkqQF0ZviexwSkKzz9T8sOYticWtEz0NEK9iJm0b1imp2pka06kv7y89OpiShrVNfnSNJ7nYtgWooQNa87fquojjUwIqifuOpgO+jj5Fvoq8jhmyjx5SfTAH+wtZY1EsQYEIqMhJbTnlPnyk1C9rXCe6XZ3KhQZ8HEZBbCWu9iPpok1z5IOGUzrIhLE+gu1LtleqvlL0YNST4mY9SSiDeaZzuoS0u7bnUV+j+5gnWJxW2wBTSH7XTZxIfdD+FgNr97uipUZZ/3RB2IZImN1xHguzIrUzXOE+2ygVaZizUowzy/AWUIU40T/1UZIN5mrYlyPGUkD6bK7J9615dRvcjwdcjUhueQFDlkE2TutbKmfM5zljS8zWhUpV5jceg8KYvVfWXiSGfJjY+1yXyIl+K8x0EMz76zYjNuj/XpCKG3xoH9c3pxk3TTdh531hQ/OIXqrg7Waa7SlBQnIxvsL2/FVw+rHcocc+9H4XFiRmlZXv7qxfPK2FjBNpJrshp7aRQ1z7afyVxCAYoemSpem696PHDJc115TVLUNABMqBsAu3ahlTAyya6zcM0f2rgvIbPO0ZA9gs8DCUaQcyXQsqFjz8BSEsFO1uWdymTbFGzHK+/wYpsAhOWes5p6ZYRtU7rMLNdcUsTr1Foqo9nFnbY3yqX2sO8NVna2026mP3954PcMOvYi5gNTuKNmH11uzGBxFpuVtM2t3BpVBJ0o8cKQcdo1JsTcBdQZPHU67ALrEzBPGetTGRajvVfexN7fRoFAnNGCvEEWQYY8dLB1OHih/SseM+fFCXymF94SyAyQfJScYkJmc9G7GdZxbnTglLP8UtMOPWujd8ZWNKuz7R03SdqkQVEJBddQRMslh79FVBpZpH6fEEWf+AGGcQ1sLJ9WYxLAWicvqDMtLzYJRh+BUrGM0KlDt0oVfi+K33nS+7wWLi5P5Y50VCiMKybpMaEtU6I670gUmclnPRQYdFJuNo3wx++zliugeR7USsBkIgIX/c4cLz7w/NHTi5vFCz9EUakR0WTCyPRkpfqnKyaTROz7f+UWh/Ed1lrgj3Xz61/sn0QMkKAbyLTF32fbR4c7cxsa+XTsv/yy/2IxigDvbKR/+YGiDoF8GnklqCJCYCGjKdYwXIQQPNSI26n5vZ0EFurR/X2kFJ/dl7T0gAAAryDowJN0nZfGUwfUxXdQ4QhetXdMuASicasbYMzwXuJvR+PHz5vlBPDyEjtXmZJu0/3plkiLbRRT0na3U91onDqXc/XEaRBcmEr89pulTeduVOO00RkUoHN8hYOdbszpv6EBUgdNV48GPQe10KmPHEX35hwDhxqUWV7Qo5qsyDEKAuTDKqoXHnEhykb5K6av1CV+2LNPubUxL4lIkFryn86bzAtE6d3Km8+K2rxW951HukIPbMEsPUsD5lC3lmSZ0w0NU67MiGcw97U3RyP1ctzp3i1e+NuqGCGiudMj0rrmDlNqC3mpkWWln+ZDaLq1zP+QwSEdSgSf/dCDArAmFIk7moWlFUTvfG9zkxtBr6WuMHAQvfvrGROf3vPI70kjYnNKIA8eeB2v+kgmfCa9SZgpgt9T5+anCMKzrugl3gQ8aPUv/xEOD7Y/8S58nn1VQDJ/WzQ+vZKwcoASJqd65vyAt0Mu7+WMRUwLtUJ2Y3I4TediGZRziwiIFOxZ5VO083p+hhI2VtlDo373AB0rr3ldlPamL5BuJ/z9wzY8oQ1PN5vKX1/SIhvO+9mriwJxsI1E4fRTy0sDgGpsNOAAA63F+7x5FSO4Y1iXjlgd7MuvRHI9YwLBTP0vQIrqYpqdYSqy9IDjOySl/aP6vLOSIKnEJYLfu5E9H38fdlIcNKxr4yGxPTYYUiDvCgrxzETSS6LWNC400zCNfZEp3ybTdccOaz0Wltfg3LvIr4b52BEyul9LIPUP2k9srp/Rkb36OHiEOO+qjFou6edyVWw2loTRhRSGFTwrOUWEmSs2PbC6ldb+1VmNBFXLEiDU2H+olfIumXbQnQDqqEQMxfjQYFLghzGqmHlANmAUZmtqTA5kTBtwXvXwnFxwUoApXNupe2PEqoQ3PZn2Greb8oZxfuIVOSQaox8SgPgvXKCFObZx3sOCV5JMDoRa4RnxT0gXcqXDKt3/k6r5e4usGo6gvtsTmgDJfdrRlFBj+il/dj83qZ+cTZmrlK7fWgtJN8LICecdBj7QeLg3Bhy2eTgaNcknPMi7Y+WZd/UyKhyj/fgX2ueDBaA9OgA/Sny8i3DD9TW3MGmhaG6x9W6lTdc2+/8l8XZfPOUqBf91ajGnd9CXDQFgwCIdzsAWpt+cl9KWWR4owTjEJrCcNIcdt5OGZ5qOa0ecmgSSHjWjx2qzAAAegXInKGy0FJ1nsSZz4oPpKG2pmNMpG2ibMJRbRcXOdJc+t446Xr9u3w/2QpHmww3yi7HLIGWl2kHwmX1AdyXxXitid/fM0dNtQCWa2BpruCgTSO1QsYhyMx7CfQRsRZG7U/ULZDS+txAQbDoBG15TeDFxR5G6HGfYPYvMyPig95ZBsa6C7v3EaaTvBwA83xvm+1Hr9+rehwnNL6BcBl/NOiBO9/97WJ4ntO6G5d67y5EiYTaUOIRDWrgCf3/ZWSgn1P04d6tsClzU5IOm5ydeYNGwPMZjgLKBOdKJcdDcdPIQJ4Zcdh1GjROwbUbEajqbvGYk38ttQGcUNkdTRLTr4FZxjwaovqsMOdydEKjlW5mu44XhCl9bE3UkGDfXykefNz6TMy5ryOhWWfyU7dgV4lZgCW7wfq/JhsKI6Q2y11dUCu2VG9zyu10sq5G8idv3rPwbrdVVN+hp43FdPWrTsikEAL7vRm3YAM23Gyamdn6WG4QIib4ySe/0PB3F+O137UShMcQeatO/twr6WKkfGJvO6/lUKONbq+Nr1k0SywZhtV1JykNPcDyRt/V2A9+vmKNWpOJuoBo/O7NGdTh35nWvi4FuuwusDCzGAgH+HwkBcpcYrDMqyyz2TDneMR7caeMrMXPcRYoIvArceWtuEFdREmXoFLzt+gpUeHZMYh9t5jkynze42srDqx4i9vF9mpCjOENNEfjmHKLVUHxmpPahcCvMo9bdSKn1L+qaIaiqTX4ylm1hVVFAzBQ8XTfX4XZ9sVPZoWJz+HGn70XCfhkOOJ65EiaWAnOVaTjfAzBUW7xx1QMYBSwGbPwQX7JpfINbE8PsYNVysawDdPZU7TNBtX6ZsTeTDnHCgWG8/ThGB6FQ1ue5gVnUWKao3O/smWwQy0sbnz62IjOujzx96g55MCnB8XsnlViWTMPlY/g9BJwuDKyj8qBMiarxFEhwpWLfufmosXkbXPMCI8Y8qu6ASqqY0pQv3ekf9CR6ob1HXRBKoVFAKfV9hYVk6fG1+vjiAasUSZvLuyO7XXlkqFMd+dbbC8ZsoYgs2t5LpwJdVMuDFMa46O0fXAevd56bGDkGq/dFgit/ScOZqrFUXG9SvrykBIQCKkh+kL62elPP+yMPcioFCclUP0D/MLkET1V6GoSZQnC1UXP/4g8s18ZF9YnpMoJqP7xTSuxCr2/50cEApN8S8yjqWI2PrOlqpk7EGee3MV3qPvnFoVVAhsZXcl6TwltYpXL7gZtSxlbcN6rJVJKynVUOjndgjEMuZ6S8zHy1trDHDI28JX+qNusR0AgI0EShDHWZOZvjnJhkN2cuTrs9/Qs471CelDL0pE5mstjCNlVKHZqC0nZU2JHhIdmDXDZ7YBm5cNOpG4QR61W6CjPO5IMj2aOh9LsBF/oEPdkXr2/UEMr8CuINPwpRRb0X8G+AQH8lMpwassUCGa4uCHSN2NvzsViR/ZODeekiuE6c6ZWd4T77erGEPy9JRkwsMMbD77XIbOT/LwrV28r6e2tJk/vstaCtK6hC3WVxIijnkFFmgcB/p0nnsyC8MOar8VqYtRFL7EAmmXyde9YmLBqaNnrGv80TC4vzawgjYgUjBOS74tORODkQtbS+hDNep8gmaIudwfHLKuh2ffvKH3Sj4m2HEvTNUzERACE0IVo69dtJnUII8PYAq0qrjS7m7wfoca+GPJ2QWR/Z0kTYBxhgwbJaqP1e4LJhMzggyhQFWnES8wSx5Idf50UUXchoNA9G8uLrtDOojgJvwWm+RddRcf9DW8xp8WBmRLuu7AGBr12uFUpCVQuB3nZ/H0YsmIcOaU1HMliTgr0tEGSpOo+vHysbKaIdWiLPp2yCEnSJFKk5VGVDfIIhfBaAXV+3y5FcEtatmfDqa8m0oUUJeJlIin5anwNtULStGJ1Twg9Ug2d2VZk1MKiiKSNqFAeKMH7AUCAQitiqDk4toQAO7pUdXLhG5Tx96jyE0AUHrYnrL+I2qAUO76zC+C5AdLWNzbA0OR3bmaYEdIIekVe4N4yv5AYgQDIjQ2rvZ8im4FGDVtFE5nQ30hYWGxKIaBTtFUKB6XWaa8bTuR6G2U+nPc5vNE7eJIO/JajH7LVqGDuKaWZdPZ4NuO2pipu4FWQn/pAUN8oyR9MXIRvmpYExMC853pZQx9H+j8o5D8dT0gZZEOntCT9phlHpohNwFFdM/3IytckVpcStDh8DC0oB70wvNkVCWqgkTkzJLiYhBmsG8sSSUzM1gnb5s1BGmZBlUxoQdMUVcAcDM66C2AADMTPFJnIWMT5Sk1HzjVWV1IhBHFYqn2fSHSswyXyRrQE6bJUumspc1X4o9BLLbrhaVdVj38iI6yhUYGUb7MUvbyPJBkaV0HC7Z+2ekPGYC1/k+sXWliOxbPhVkIoZpBEQMOm7R193Wmp+z2OBPcmqg0Ty1dw3AZq3qkkuQDZlxA4GP85GRETvMx1q1sZScdachp2MKfXsuwFKw6d9o1SE6DjFPcp8RETfPdH9+SIs7e9/RvljhNCfuMmf6SAr5RAKTqXJTYxsZ/FNbmGBc8ogoQ9quiltuyv2qkoRVrVgpv3e30VaqvDpworIp9aG576nszfh2ByKgYWgjoCHn0CkqVGTtC/fxwLBYRj5ew0pQdv7v9XT+Wpa/05zndSk8ntJM7isxV40iyh0YmNJIZvOU8dX/J2b3CcvDQ/XlBQHbePxZVMvPN/jVovHOp2YASrf3szXsoaZZgpr2i1dBlh0DqfgXGtqcuKsxLY0nXq01PANCCRyKklihN8kVols2qzuk5UDHzX6jH7lP6eX/3MK2wCr5gHB8biNiURWbkVkkYTZWeHQPWy6vwCC4bBgFyreo0juQlP+iX0xj4t9g//q6ckSX7paEQNSkdQJopvKv2dY+dWrlREUqNIJB7nGHc8TpuAprANjUsJU8ADlLS2tS5QOYo9aQYE/10eGLIHkm7MGpXi9WKkuvguHGfwoAAHGYsyqcE3fQaBr79OxK+5gzwJ9kjePDTCj9BcU4XjL0jjyY2T1DToK7MjaTNiazm9ZGfBMSJD+NBwUvjLqMepXVICHED7KgHQhDCXBARUEyuIGf8E9qb6jPx2hewD3jSxfXnVU5RpL+qS1rhLnz/qV0B8gN8QC2Huwh4FyihX4liVWnb2KKF5+sVtRKzY8GETu2/WdofuNpuWDTeORURrxKvIzNhYbn++u8sFf3Zr0w9ouxs2+oQTQvQ2ah9xGFYiQY+/pwxL6FOHg7pKAxQF+UH+zjnzc+VcKJ49SFqQJN2jwLYXSjC9grmlFxpyAQJM3CEXze/vQbvuGCq8EAIgJUcDCtKtjsLfODGBTn2I5jzDysEoo4hf7ZqTTvB1l4M65h7AAUBL47WK5LeBz4Wgpgpa0E+dKQUBqNjqaQSPDEsvjkqbeU8IdRTbgy4Sh00CxinYOJt+O6yhVSVukuR1QeVxm6DP3Oz2X0vgHG+yJzdsfVGxXWgeRofotvgAoiRx5PUpVDcnCONpw7vQPAASFLkpmKO0KphjLkQgMiesXcnwaC5CjuqivkgpLzMDGV5102Ym0bNGKGlquIpMDn1Q3rKI1Is//3NnwDcHJsXD9nyaxnKF9VUHfXa821r9Y0zX9+FU93IMrpet8R6vsDXGuKL8EY+NVypjlDQMb4ZEF3IXMPc0la8CO/IszP8mZ1o5E4SlZc73LoEt+ZmXwb9GxUsbsKrOy3hXIcLBgltyRe8wc0Tl5+MooOiq97hFENe8WCWxE5Q181VFi4bqSfGR3uRwfS5WWj6ki0/k/gGBUJiVA1d7RVYCA6m0xSxT+o1kzlyePDvPpNnGDOteSjyVhTWnDMCXLTgi6qsxhSwYVsfzVaS70bSZW5+hlAjVF/X3Qa7sg2UNkciR2EOLocbj44AJrfoXUII/c80tPVqXiEbsgjR/Ru3Pvaq3wbeNOFYBL7d/W9eCkGa4v54zIHXEesD+UXmodrLNw1m+9lv0PDr19YIL7wiMOvvB1xu/wtEhjCAFx0TZyhiXFZbWCWSVakKHWmi7LUfsrjCojVwc3jLAYWV3ALcql/PHq2XjHWSe4BVb52C0irCjwloML2kl0YbRIWNWAuz1Mq9OwkVC9FXs5agEZGGuBINsO+Qj3q/8UeDmE2VUMHOu4n/nqhSE+B+EWk7syJpUlNFMfC+ccSpgizGHYalayg4MgLNvTQK9g+8WITagm5uGLtfDMgO18agyG3K0Dea5zffZ4FR9ZrSPX0gy8mLesVIYuVCF+QHJ5HRVJjPT4QWd0J5LgPMxeCZ7IvRBoGA3Rnfq+NaXOC6jnzhi+mgGGGzOhyRgSWlZe8YW849wBK1Iguu2tGPjaROkfAxxTPyzX7VVDSLwiQ41YB291K23UfAAbKua3wc7CgTPH2KTORJxNX/GEsjOzKfzWOptT+/nnd+D+ul/O1oztKj1FsVWedELphsBp6XNTdvZmdOJoRWCFrDLklTRhDC8MuXe/fM+urLI22VFDf6c+vX6AZqk5JKC7F4iqop6WOScXZatf4m0lkP243rRLka+N+ix2sxI+HAmpBLo7B37sB4hEbqEg0FHmx/BN0lYI3N278rqGWS1DGGVK2PjLeIe4eSiKu2tV8KsDHAAJK3yCZqaEseUIoSUuosQTk96jbDWlXN1giocbZjQuo/WI69zBnvL++7sUqt+By3XSp8oYH2jRvnMNogbmbcGE+NYaDIL3Fr4E8BXWwuaXqStUfC6/UcMT0big5RZ2OS1swT2PVbCVBDjfXuWbY2sy2h3b8OM7mp7X35ze2MoDIhpIjAnQ/zFcjm32usfw97pwjFJ+0H0W5hc3dcza5WdycfhZNMdrf/Ns2YuLoyOPZGw2t//1xuTDUKl+IdWzfTnQZsbsWAV/uTJR/1DTKQRcVEKTxDxGBF2RLCqNAwJSctoq8Jify7mufpBrrwuYnH2YvqQKCbPiG27Njgf9oruGgAAAAAA==' },
  ];
  const RAID_SHIELD_ART = [
    { id: 'plank-splinter', src: 'miniapp_preview/assets/raid_shield_0.png' },
    { id: 'bronze-buckler', src: 'miniapp_preview/assets/raid_shield_1.png' },
    { id: 'sun-tower', src: 'miniapp_preview/assets/raid_shield_2.png' },
    { id: 'emerald-fortress', src: 'miniapp_preview/assets/raid_shield_3.png' },
    { id: 'rune-barrier', src: 'miniapp_preview/assets/raid_shield_4.png' },
  ];
  function raidShieldVisual(item = S?.equipment?.find(entry => entry.slot === 'shield')) {
    const appearance = Number.isInteger(item?.appearance) && item.appearance >= 160 && item.appearance < 240 ? item.appearance : 160;
    const suffix = Number.isInteger(item?.suffixIndex) && item.suffixIndex >= 0 ? item.suffixIndex : 0;
    const art = RAID_SHIELD_ART[suffix % ITEM_NAMES.shield.length];
    const finish = `hue-rotate(${(appearance % 20 - 10) * 1.5}deg) saturate(.96) brightness(${(.94 + Math.floor((appearance - 160) / 20) * .035).toFixed(2)})`;
    return { src: art.src, id: art.id, key: art.id + ':' + appearance, style: '--shield-finish:' + finish };
  }
  function raidShieldTexture(className, item) {
    const art = raidShieldVisual(item);
    return `<img class="${className} shield-texture" data-shield-art="${art.id}" data-shield-key="${art.key}" src="${art.src}" style="${art.style}" alt="" aria-hidden="true" draggable="false">`;
  }
  function syncRaidShieldTexture(image) {
    if (!image) return;
    const art = raidShieldVisual();
    if (image.dataset.shieldKey === art.key) return;
    if (image.getAttribute('src') !== art.src) image.setAttribute('src', art.src);
    image.dataset.shieldArt = art.id; image.dataset.shieldKey = art.key;
    image.setAttribute('style', art.style);
  }
  function raidWeaponVisual(item = S?.equipment?.find(entry => entry.slot === 'weapon')) {
    const appearance = Number.isInteger(item?.appearance) && item.appearance >= 0 && item.appearance < 80 ? item.appearance : 0;
    const suffix = Number.isInteger(item?.suffixIndex) && item.suffixIndex >= 0 ? item.suffixIndex : 0;
    const art = RAID_WEAPON_ART[suffix % RAID_WEAPON_ART.length]; // same order as ITEM_NAMES.weapon
    // A stable metal finish, not an elemental tint or a quality/stat promise.
    // Upgrading mods cannot repaint a weapon. Equal appearances may look alike.
    const finish = `hue-rotate(${(appearance % 20 - 10) * 1.5}deg) saturate(.9) brightness(${(.94 + Math.floor(appearance / 20) * .04).toFixed(2)})`;
    return { src: art.src, id: art.id, key: art.id + ':' + appearance, style: '--weapon-finish:' + finish + ';--weapon-mask:url(' + art.src + ')' };
  }
  function raidWeaponTexture(className, item) {
    const art = raidWeaponVisual(item);
    return `<img class="${className} weapon-texture" data-weapon-art="${art.id}" data-weapon-key="${art.key}" src="${art.src}" style="${art.style}" alt="" aria-hidden="true" draggable="false">`;
  }
  function syncRaidWeaponTexture(image) {
    if (!image) return;
    const art = raidWeaponVisual();
    if (image.dataset.weaponKey === art.key) return;
    // Change only the worn image/finish, never rebuild a surviving orb, its
    // material, ice or image node. A purchase is not a fall/destruction event.
    if (image.getAttribute('src') !== art.src) image.setAttribute('src', art.src);
    image.dataset.weaponArt = art.id; image.dataset.weaponKey = art.key;
    image.setAttribute('style', art.style);
  }
  const RAID_EQUIPMENT_SHAPES = {
    helmet: '<path d="M5 27V14C5-2 27-2 27 14v13l-8 3v-9h-6v9Z" fill="#758e83"/><path d="m7 12 9 4 9-4-3 8H10Z" fill="#102c2c"/><path d="M16 3v8m-7 16 2-4m12 4-2-4"/>',
    armour: '<path d="M11 3h10l7 5-4 8-3-2v15H11V14l-3 2-4-8Z" fill="#566f64"/><path d="m11 3 5 6 5-6M9 12l7 5 7-5m-12 9h10m-10 5h10"/><path d="m16 11 4 5-4 6-4-6Z" fill="#9fd7be"/>',
    shield: '<path d="m16 2 12 5v11c0 5-7 10-12 13C11 28 4 23 4 18V7Z" fill="#526e65"/><path d="m16 6 8 3v9c0 4-5 7-8 9-3-2-8-5-8-9V9Z" fill="#163d3c"/><path d="m16 10 5 7-5 7-5-7Z" fill="#a8d9c1"/>',
    accessory: '<path d="M6 3c0 12 20 12 20 0M13 12h6v5h-6Z" fill="none"/><path d="m16 15 9 7-9 9-9-9Z" fill="#a1aa75"/><path d="m16 19 4 4-4 4-4-4Z" fill="#b6e2c5"/>',
  };
  function raidItemIcon(slot, item) {
    if (slot === 'weapon') return raidWeaponTexture('equipment-glyph', item);
    if (slot === 'shield') return raidShieldTexture('equipment-glyph', item);
    return `<svg class="equipment-glyph" viewBox="0 0 32 34" fill="none" stroke="#c7b57e" stroke-width="1.2" stroke-linejoin="round" aria-hidden="true">${RAID_EQUIPMENT_SHAPES[slot] || RAID_EQUIPMENT_SHAPES.shield}</svg>`;
  }
  function raidAchievementAvailable(id) { return ![45, 70, 71, 76, 77].includes(id); }

  let RAID_REGION = 'earth'; // Preference / preview for the NEXT run.
  let RUN_REGION = null; // Pinned location; v1 uses it only for appearance.
  const RAID_ELEMENTS = ['fire', 'air', 'earth', 'water'];
  const ELEMENTAL_RULES = 'elements-v1';
  // Null means a released v1 run: Continue must NEVER enable new combat in it.
  // Gameplay metadata lives beside state in v2, separate from all six old RNGs.
  let ELEMENTAL_COMBAT = null;
  const RETUNE_RULES = 'elements-retune-v1';
  const AFFINITY_RULES = 'elements-affinity-v1';
  let RETUNE_DRAFT = null; // unpaid UI only; never part of a checkpoint
  function hasRetuneRules() { return [RETUNE_RULES, AFFINITY_RULES].includes(ELEMENTAL_COMBAT?.rules); }
  function activeSkillDefinitions(retuning = hasRetuneRules()) { return retuning ? [...ALL_SKILLS, RETUNE_SKILL] : ALL_SKILLS; }
  function runSaveVersion() { return ELEMENTAL_COMBAT?.rules === AFFINITY_RULES ? 4 : hasRetuneRules() ? 3 : ELEMENTAL_COMBAT ? 2 : 1; }
  // A run owns this frozen snapshot. Neither current meta progression nor the
  // preparation for another raid may alter its level, drops or damage.
  function runPilotAffinity() { return ELEMENTAL_COMBAT?.rules === AFFINITY_RULES ? ELEMENTAL_COMBAT.affinity : null; }
  function affinityCounterElement(element) { return RAID_ELEMENTS[(RAID_ELEMENTS.indexOf(element) + 3) % 4]; }
  function preparedPilotAffinity(classId) {
    const level = Math.max(1, Math.min(10, classLevelOf(classId)));
    const choice = pickerOpen && !classEditor && !UPDATES_OPEN && !HISTORY_OPEN ? pickerAffinities.get(classId) : null;
    return Object.freeze({ element: level >= 2 && RAID_ELEMENTS.includes(choice) ? choice : null, level });
  }
  function pilotAffinityDescription(affinity = runPilotAffinity()) {
    if (!affinity) return 'Прежние правила: склонности нет; сохранённый рейд не меняется.';
    if (!affinity.element) return 'Без склонности: по 25% каждой стихии, без бонуса и штрафа урона. Только этот рейд.';
    const own = RAID_REGIONS[affinity.element].element, counter = RAID_REGIONS[affinityCounterElement(affinity.element)].element;
    return `${own}: 30%; ${counter}: 20%; остальные по 25%. `
      + (affinity.level >= 3 ? `Урон плитками: ${own} +5%, ${counter} −5%.` : 'Урон без изменений; бонус и штраф открываются с уровня меха 3.')
      + ' Только этот рейд; следующий выбор заново.';
  }
  function pilotWeaponMultiplier(element) {
    const affinity = runPilotAffinity();
    if (!affinity?.element || affinity.level < 3) return 1;
    return element === affinity.element ? 1.05 : element === affinityCounterElement(affinity.element) ? .95 : 1;
  }
  function rollWeaponElement() {
    const roll = elementRandom(), affinity = runPilotAffinity();
    if (!affinity?.element) return RAID_ELEMENTS[Math.floor(roll * 4)];
    const bucket = Math.floor(roll * 100), counter = affinityCounterElement(affinity.element);
    let ceiling = 0;
    for (const element of RAID_ELEMENTS) {
      ceiling += element === affinity.element ? 30 : element === counter ? 20 : 25;
      if (bucket < ceiling) return element;
    }
    return RAID_ELEMENTS[3];
  }
  function raidPortraitKey(skull) {
    // New runs show the assigned combat element, not an independent art roll.
    // Ordinary spawns are local and share ONE common skin; legacy runs stay local.
    const region = ELEMENTAL_COMBAT && RAID_ELEMENTS.includes(skull.element) ? skull.element : RUN_REGION || RAID_REGION;
    const family = RAID_PORTRAITS[region];
    if (skull.boss) return family[3];
    if (!skull.special) return family[0];
    const key = `${region}_special_${String(skull.type).padStart(2, '0')}`;
    if (RAID_MONSTER_ART[key]) return key;
    // Defensive fallback for missing/corrupt assets, not a normal catalogue
    // path: all 31 inherited special types now have their own four palettes.
    // Exact coverage is in RAID_MONSTER_CATALOG and raid_monsters.json.
    return family[1 + Math.abs(skull.type || 0) % 2];
  }
  function raidPortrait(skull) { return RAID_MONSTER_ART[raidPortraitKey(skull)] || SKULL_ART[skull.art] || ''; }
  function applyRaidRegion(region) {
    if (!Object.hasOwn(RAID_REGIONS, region)) return false;
    RAID_REGION = region;
    // Preparation may preview a different NEXT location. Never repaint the
    // shell of an existing run (including a paused run or its death screen).
    const current = S && RUN_REGION ? RUN_REGION : region;
    if (document.documentElement && document.documentElement.dataset.raidRegion !== current) document.documentElement.dataset.raidRegion = current;
    const picker = document.getElementById('class-picker');
    if (picker) picker.dataset.raidRegion = region;
    const regionScreen = document.getElementById('raid-region-screen');
    if (regionScreen) regionScreen.dataset.selectedRegion = region;
    const launchLabel = document.getElementById('region-launch-label');
    if (launchLabel) launchLabel.textContent = 'В бой · ' + RAID_REGIONS[region].element;
    for (const node of document.querySelectorAll('[data-raid-region-title], [data-raid-region-caption]')) {
      const data = RAID_REGIONS[node.closest('#class-picker') ? region : current];
      node.textContent = node.hasAttribute('data-raid-region-title') ? data.title : data.index + ' / ' + data.element;
    }
    for (const button of document.querySelectorAll('button[data-raid-region]')) button.setAttribute('aria-pressed', String(button.dataset.raidRegion === region));
    return true;
  }
  function loadRaidAppearance() {
    let region = 'earth';
    try { const saved = profileStorage.getItem('raid:appearance'); if (Object.hasOwn(RAID_REGIONS, saved)) region = saved; } catch (ignored) { /* UI remains available without persistence. */ }
    applyRaidRegion(region);
  }
  function saveRaidAppearance() {
    // An older checkpoint has no region of its own. Preserve its first-read
    // fallback across a preview + page close until a successful pinned save.
    if (LEGACY_RUN_REGION) return;
    try { profileStorage.setItem('raid:appearance', RAID_REGION); } catch (ignored) { /* profileStorage already shows the identity warning. */ }
  }
  function initRaidAppearance() {
    loadRaidAppearance();
    for (const button of document.querySelectorAll('button[data-raid-region]')) button.addEventListener('click', () => {
      // Hidden/stale controls cannot change even the next-run preference.
      if (!pickerOpen || NEW_RUN_REQUEST || !button.closest('#class-picker') || el('raid-region-screen')?.hidden) return;
      if (!applyRaidRegion(button.dataset.raidRegion)) return;
      saveRaidAppearance();
    });
    el('region-back-button')?.addEventListener('click', () => {
      if (!pickerOpen || NEW_RUN_REQUEST) return;
      PREPARED_MECH_ID = null;
      el('raid-region-screen').hidden = true;
      el('class-picker-main').hidden = false;
      el('picker-card').classList.remove('region-screen-open');
      el('class-picker').classList.remove('region-mode-open');
      document.querySelector('.picker-card-btn')?.focus();
    });
    el('region-emblem-launch')?.addEventListener('click', () => {
      if (!pickerOpen || NEW_RUN_REQUEST || !PREPARED_MECH_ID) return;
      requestNewGame(PREPARED_MECH_ID);
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
  // Independent Mulberry32 stream. No element decision may call random/randInt,
  // Math.random, render, or the cosmetic palette hash. State is saved verbatim.
  function elementRandom() {
    let word = ELEMENTAL_COMBAT.rng = (ELEMENTAL_COMBAT.rng + 0x6d2b79f5) >>> 0;
    word = Math.imul(word ^ word >>> 15, word | 1);
    word ^= word + Math.imul(word ^ word >>> 7, word | 61);
    return ((word ^ word >>> 14) >>> 0) / 4294967296;
  }
  function withWeaponElement(tile, source) {
    if (ELEMENTAL_COMBAT && ['sword', 'arrow'].includes(tile.type)) {
      // An existing weapon keeps its element through sword/arrow conversions.
      // A genuinely new weapon (including a converted coin/enemy) rolls once.
      tile.element = source && ['sword', 'arrow'].includes(source.type) && RAID_ELEMENTS.includes(source.element)
        ? source.element : rollWeaponElement();
    }
    return tile;
  }
  function withSkullElement(skull, source) {
    if (!ELEMENTAL_COMBAT) return skull;
    const local = RUN_REGION || RAID_REGION, index = RAID_ELEMENTS.indexOf(local);
    if (source && RAID_ELEMENTS.includes(source.element)) skull.element = source.element;
    else if (!skull.special || skull.boss) skull.element = local;
    else {
      const roll = Math.floor(elementRandom() * 10);
      skull.element = roll < 7 ? local : RAID_ELEMENTS[(index + 1 + roll - 7) % 4];
    }
    if (skull.boss) skull.secondaryElement = source?.secondaryElement
      || RAID_ELEMENTS[(RAID_ELEMENTS.indexOf(skull.element) + 1 + Math.floor(elementRandom() * 3)) % 4];
    return skull;
  }
  function combatElementLabel(skull) {
    const primary = RAID_REGIONS[skull.element]?.element || '';
    return primary + (skull.boss && skull.secondaryElement ? ' + ' + RAID_REGIONS[skull.secondaryElement].element : '');
  }
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
  let LEGACY_RUN_REGION = false;
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
  function saveConfig(value, classId, retuning = false) {
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
    saveCheck(value.replacements[0] === null && value.replacements.every(id => id === null || typeof id === 'string' && Object.hasOwn(SKILL_BY_ID, id) && (id !== 'retune' || retuning) && !SKILL_BY_ID[id].nativeOnly));
    if (classId === 'adventurer') saveCheck(value.race === base.race && ['perks', 'flaws', 'replacements'].every(key => value[key].every(entry => entry === null)));
    // Do not revalidate against today's donor levels/editor. This is the
    // already-started run's frozen configuration, not next-run preferences.
    return value;
  }
  function savedSkill(option) {
    return { kind: 'skill', id: option.id, duration: option.duration, charge: option.charge, level: option.level, upgrade: !!option.upgrade };
  }
  function readSavedSkill(value, retuning = false) {
    saveObject(value, ['kind', 'id', 'duration', 'charge', 'level', 'upgrade']);
    saveCheck(value.kind === 'skill' && typeof value.id === 'string' && Object.hasOwn(SKILL_BY_ID, value.id) && (value.id !== 'retune' || retuning));
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
  function readSavedCell(cell, elemental) {
    saveObject(cell, ['type', 'skull', 'broken', 'mana', 'acid', 'explosive', 'magicSword', 'arrow', 'frozen', 'burn', 'fireDamage', 'fireSafe', 'stunned', 'frozenTurn', 'matched', 'appearance', ...(elemental ? ['element'] : [])], ['type']);
    const weapon = ['sword', 'arrow'].includes(cell.type);
    if (elemental && weapon) saveCheck(typeof cell.element === 'string' && RAID_ELEMENTS.includes(cell.element));
    else saveCheck(!Object.hasOwn(cell, 'element'));
    saveCheck(['potion', 'skull', 'coin', 'shield', 'sword', 'arrow', 'chain', 'trophy'].includes(cell.type));
    for (const [key, value] of Object.entries(cell)) {
      if (key === 'type' || key === 'skull' || key === 'element') continue;
      if (key === 'frozenTurn') saveNumber(value, -1, 1e12, true);
      else if (key === 'burn' && typeof value === 'number' || ['fireDamage', 'appearance'].includes(key)) saveNumber(value);
      else saveBool(value);
    }
    if (cell.type !== 'skull') { saveCheck(!Object.hasOwn(cell, 'skull')); return cell; }
    const skull = cell.skull;
    const required = ['boss', 'special', 'type', 'countdown', 'uid', 'art', 'atk', 'def', 'hp', 'maxHp', 'promotionInterval', 'enteredTurn', 'poison', ...(elemental ? ['element'] : [])];
    saveObject(skull, [...required, 'burn', 'fireDamage', 'fireSafe', 'stunned', 'frozenTurn', 'phylactery', 'lichDelay', 'promotedByBigGame', ...(elemental ? ['secondaryElement'] : [])], required);
    if (elemental) {
      saveCheck(typeof skull.element === 'string' && RAID_ELEMENTS.includes(skull.element));
      if (skull.boss) saveCheck(typeof skull.secondaryElement === 'string' && RAID_ELEMENTS.includes(skull.secondaryElement) && skull.secondaryElement !== skull.element);
      else saveCheck(!Object.hasOwn(skull, 'secondaryElement'));
    }
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
  function readSavedMenu(menu, retuning = false) {
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
      } else readSavedSkill(option, retuning);
    }
    saveArray(menu.picked, menu.kind === 'level' ? 2 : 0);
    menu.picked.forEach(n => saveNumber(n, 0, count - 1, true));
    saveCheck(new Set(menu.picked).size === menu.picked.length);
    return menu;
  }
  function readSavedState(data, legacyId, elemental, retuning = false) {
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
    saveConfig(state.classConfig, state.classId, retuning); readHistoryId(state.historyId); readPretzelStats(state.pretzelStats);
    readAchievementRun(state.achievementRun, state.difficulty);
    const heroKeys = Object.keys(newGameState().hero);
    saveObject(state.hero, heroKeys);
    for (const key of heroKeys.filter(key => key !== 'stats')) saveNumber(state.hero[key]);
    saveNumber(state.hero.level, 1, 1e12, true);
    saveCheck(state.hero.hp <= state.hero.maxHp && state.hero.armor <= state.hero.maxArmor && state.hero.maxHp > 0);
    saveMap(state.hero.stats, Object.keys(newGameState().hero.stats));
    saveCheck(Object.keys(state.hero.stats).length === 7);
    saveArray(state.board, CELL_COUNT, CELL_COUNT).forEach(cell => readSavedCell(cell, elemental));
    const uids = state.board.filter(cell => cell.type === 'skull').map(cell => cell.skull.uid);
    saveCheck(new Set(uids).size === uids.length);
    saveArray(state.equipment, ITEM_SLOTS.length, ITEM_SLOTS.length).forEach(item => readSavedItem(item));
    saveCheck(new Set(state.equipment.map(item => item.slot)).size === ITEM_SLOTS.length);
    saveObject(state.equipmentApplied, [...ITEM_KEYS, 'health']);
    Object.values(state.equipmentApplied).forEach(n => saveNumber(n));
    const skillIds = activeSkillDefinitions(retuning).map(skill => skill.id);
    saveArray(state.skills, 4).forEach(id => saveCheck(typeof id === 'string' && skillIds.includes(id)));
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
    saveCheck(data && data.format === 'lunapark-raid-magitech-run' && [1, 2, 3, 4].includes(data.version));
    if (data.status === 'finished') {
      saveObject(data, ['format', 'version', 'status', 'record'], ['format', 'version', 'status']);
      if (!Object.hasOwn(data, 'record')) return null; // original v1 tombstone
      readHistoryRecord(data.record, data.version >= 3); return { status: 'finished', record: data.record };
    }
    const keys = ['format', 'version', 'status', 'revision', 'savedAt', 'build', 'state', 'rng', 'skullUid', 'menu', 'results', 'arrivals', 'next', 'leechFadeMs'];
    const elemental = data.version >= 2, retuning = data.version >= 3, affinity = data.version === 4;
    saveObject(data, [...keys, 'orbElements', 'region', ...(elemental ? ['combat'] : [])], [...keys, ...(elemental ? ['region', 'combat'] : [])]);
    if (elemental) {
      saveObject(data.combat, ['rules', 'rng', ...(affinity ? ['affinity'] : [])]);
      saveCheck(data.combat.rules === (affinity ? AFFINITY_RULES : retuning ? RETUNE_RULES : ELEMENTAL_RULES));
      saveNumber(data.combat.rng, 0, 0xffffffff, true);
      if (affinity) {
        const value = data.combat.affinity;
        saveObject(value, ['element', 'level']); saveNumber(value.level, 1, 10, true);
        saveCheck(value.element === null || value.level >= 2 && typeof value.element === 'string' && RAID_ELEMENTS.includes(value.element));
      }
    }
    if (Object.hasOwn(data, 'region')) saveCheck(typeof data.region === 'string' && Object.hasOwn(RAID_REGIONS, data.region));
    if (Object.hasOwn(data, 'orbElements')) saveArray(data.orbElements, CELL_COUNT, CELL_COUNT).forEach(n => saveNumber(n, 0, 3, true));
    saveCheck(data.status === 'active'); saveNumber(data.savedAt, 0, Number.MAX_SAFE_INTEGER, true);
    saveNumber(data.revision, 1, Number.MAX_SAFE_INTEGER, true);
    saveCheck(typeof data.build === 'string' && /^(?:[a-f0-9]{12}|source)$/.test(data.build));
    data.state = readSavedState(data.state, data.state && Object.hasOwn(data.state, 'historyId') ? null : legacyHistoryId(raw, data.savedAt), elemental, retuning);
    saveArray(data.rng, 6, 6).forEach(n => saveNumber(n, 0, 0xffffffff, true));
    saveNumber(data.skullUid, 1, 1e12, true);
    saveCheck(data.state.board.every(cell => cell.type !== 'skull' || cell.skull.uid < data.skullUid));
    readSavedMenu(data.menu, retuning);
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
    return JSON.stringify({ format: 'lunapark-raid-magitech-run', version: runSaveVersion(), ...(ELEMENTAL_COMBAT ? { combat: { ...ELEMENTAL_COMBAT } } : {}), status: 'active', revision: SAVE_REVISION + 1, savedAt: Date.now(),
      build: qs('meta[name="raid-build"]')?.content || 'source', state, rng: Array.from(RNG_SEEDS), skullUid: SKULL_UID,
      menu, results, arrivals, next, leechFadeMs, orbElements: S.board.map(orbElementIndex), region: RUN_REGION || RAID_REGION });
  }
  function saveStatus(state, message) {
    RUN_SAVE_STATUS = { state, message };
    for (const id of ['picker-save-status', 'menu-save-status', 'save-status', 'run-save-status']) {
      const node = el(id); if (!node) continue;
      node.textContent = message;
      if (id === 'save-status' || id === 'run-save-status') node.hidden = !['unavailable', 'invalid', 'conflict'].includes(state);
    }
  }
  function readRunSlot() {
    let raw;
    try { raw = profileStorage.getItem(RUN_SAVE_KEY); }
    catch (e) { saveStatus('unavailable', 'Хранилище браузера недоступно. Продолжить после закрытия страницы не получится.'); return false; }
    RUN_SAVE_BASE = raw;
    try {
      const decoded = raw === null ? null : decodeRunSave(raw);
      LEGACY_RUN_REGION = decoded?.status === 'active' && !Object.hasOwn(decoded, 'region');
      if (LEGACY_RUN_REGION) decoded.region = SAVED_RUN?.state.historyId === decoded.state.historyId ? SAVED_RUN.region : RAID_REGION;
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
      raw = S.over ? JSON.stringify({ format: 'lunapark-raid-magitech-run', version: runSaveVersion(), status: 'finished', record: FINISHED_RECORD }) : encodeRunSave();
      decoded = decodeRunSave(raw);
    } catch (e) {
      saveStatus('invalid', 'Текущая точка не записана: состояние не прошло проверку. Предыдущее корректное сохранение не изменено.');
      return false;
    }
    try {
      profileStorage.setItem(RUN_SAVE_KEY, raw);
      RUN_SAVE_BASE = raw; SAVED_RUN = decoded?.status === 'active' ? decoded : null;
      LEGACY_RUN_REGION = false;
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
      region: Object.hasOwn(RAID_REGIONS, SAVED_RUN.region) ? SAVED_RUN.region : RAID_REGION,
      pendingModal: SAVED_RUN.menu?.kind || null, combatRules: SAVED_RUN.combat?.rules || 'classic-v1', savedAt };
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
    if (UPDATES_OPEN) return false;
    closeGameSettings(false);
    closeRetune(false);
    if (!readRunSlot() || !SAVED_RUN) return false;
    const data = SAVED_RUN;
    RUN_ID++; SAVE_CONFLICT = false; NEW_RUN_REQUEST = null; FINISHED_RECORD = null;
    resetOrbPresentation();
    clearFloatEffects();
    stopMaterialAudio();
    cancelTelegramExit(false);
    clearTimeout(ACTION_TIMER); clearTimeout(SAVE_TIMER); clearTimeout(SCROLL_LABEL_TIMER);
    ACTION_TIMER = SAVE_TIMER = SCROLL_LABEL_TIMER = null; SCROLL_LABEL_DEADLINE = 0;
    SCHEDULED_STEP = AFTER_ARRIVALS = RESOURCE_BATCH = null; DRAINING_ARRIVALS = false;
    activeDrag = false; window.removeEventListener?.('pointermove', onPointerMove);
    closeRunHistory();
    S = data.state;
    ELEMENTAL_COMBAT = data.combat ? { ...data.combat } : null;
    if (runPilotAffinity()) Object.freeze(ELEMENTAL_COMBAT.affinity);
    // Released v1 checkpoints without region use the profile's appearance once;
    // all subsequent checkpoints pin it independently from next-run preferences.
    RUN_REGION = data.region ?? RAID_REGION;
    applyRaidRegion(RAID_REGION);
    if (!ELEMENTAL_COMBAT && data.orbElements) S.board.forEach((cell, idx) => ORB_ELEMENTS.set(cell, data.orbElements[idx]));
    syncClassAchievements();
    S.rngSeeds = [...data.rng]; RNG_SEEDS.set(data.rng); SKULL_UID = data.skullUid; SKULL_IMAGES.clear();
    for (const key of ['perks', 'flaws', 'replacements']) Object.freeze(S.classConfig[key]); Object.freeze(S.classConfig);
    S.equipment = S.equipment.map(item => ({ ...item, slotName: itemSlotById(item.slot).name, icon: itemSlotById(item.slot).icon }));
    closeClassEditor(false); pickerOpen = false; pickerGeneration++;
    for (const id of ['class-picker', 'modal', 'menu-modal', 'help-modal', 'beginner-guide', 'restart-modal']) el(id)?.classList.remove('show');
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
    syncIntroMusic();
    if (saveRun()) saveRaidAppearance();
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
      description: `Поднять до уровня меха 5 мех с этим родным пилотом. Выбранный в редакторе пилот не учитывается.` };
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
    ['Непредвиденный поворот', 'Нестабильный — смертный враг пилота «Тактик» — обновляет поле без мечей.'],
    ['Приятного аппетита', 'Пожиратель действительно уменьшает положительный параметр выбранной вещи.'],
    ['Немезида', 'Дойти до HP ≤ 0, пока на поле есть смертный враг выбранного пилота. Последний рубеж может спасти.'],
    ['Уже тесно', 'Когда появляется новый особый враг, на поле уже должно быть не меньше трёх особых, включая боссов.'],
    ['Не дотронешься', 'Пилотом «Проломщик» собрать Хаотического — смертного врага этого пилота — с наградой особого.'],
    ['Ответный укол', 'Убить Острого врага шипами при блокировании. Контратака не считается.'],
    ['Джекпот', 'Собрать Золотого с наградой особого, не дать ему уйти по таймеру.'],
    ['Я всё вижу', 'Триггер этого достижения в проверенной версии не найден. Пока недоступно; обнаружение Невидимого не считается.'],
    ['Жажда битвы', 'Получить награду за 100 особых врагов. За каждого — 1%.'],
    ['Всего лишь царапина', 'Пилотом «Биохимик» добить ядом 50 врагов в конце хода. За каждого — 2%.'],
    ['Удар снизу', 'Пилотом «Проломщик» убить 50 врагов прямым апперкотом снизу вверх по вертикали. За каждого — 2%.'],
    ['Останься и послушай', 'Пилотом «Снабженец» купить и применить 50 свитков, прикреплённых к вещам. За каждый — 2%.'],
    ['Ещё жив', 'Пилотом «Выживальщик» 20 раз поднять HP до четверти максимума при повышении уровня героя. За лечение — 5%.'],
    ['Время молота', 'Пилотом «Штурмовик» 50 раз успешно оглушить пережившую прямой удар цель. За успех — 2%.'],
    ['Чем они больше', 'Пилотом «Бронник» 10 раз убить одной цепочкой не менее 15 врагов и улучшить доспех. За цепь — 10%.'],
    ['Ещё один пал', 'Пилотом «Перехватчик» собрать Калечением 50 обычных врагов с HP ниже их защиты. За каждого — 2%.'],
    ['Пламя дракона', 'Пилотом «Термит» 50 раз добить врага огнём в конце хода. Источник огня может быть любым. За каждого — 2%.'],
    ['Слёзы под дождём', 'Пилотом «Тактик» 50 раз получить награду за особого врага и зарядить навык. За каждый — 2%.'],
    ['Снайпер', 'Убить особого на конце цепочки после пропуска стрелой: стрела за два узла до цели, разница по строке или столбцу больше 1.'],
    ['Что не убивает', 'Пилотом «Бронник» совершить атаку с 15+ прямыми убийствами в том же ходу после Мазохизма.'],
    ['Мастер Руны', 'Руной собрать внутри больше клеток, чем эффективных узлов в её контуре. Особые внутри не считаются.'],
    ['Ожерелье', 'Провести цепочку по всем 36 клеткам и собрать хотя бы одну цепную клетку.'],
    ['Чистая строка', 'Одним Рассечением собрать строку из шести обычных врагов.'],
    ['Вернулся с того света', 'Последний рубеж должен действительно спасти при HP ≤ 0.'],
    ['Предчувствие', 'Под Ловушкой создать особого врага с 1 HP.'],
    ['Шок и трепет', 'В одном окне до конца хода применить Рассечение, Огненный шар, Рывок и сбор всей доски (Желание либо связка Рассечения со Взломом). Это не навыки Шок и Благоговение.'],
    ['Загадай желание', 'Применить Желание, когда на поле не менее 20 обычных врагов.'],
    ['Храбрый беглец', '100 раз применить Телепорт при наличии хотя бы одного особого врага. За применение — 1%, не за число врагов.'],
    ['Сафари', 'Особый, созданный Крупной дичью, должен действительно превратиться в трофей. Просто убийство или подбор другого трофея не считаются.'],
    ['Миссия завершена', 'Завершить забег смертью или победой в Спринте. Доступно и на Лёгком; рестарт не считается.'],
    ['Вокруг света', 'Провести цепочку из всех 20 клеток по границе поля.'],
    ['Золотоискатель', 'Одной цепочкой собрать поле из 36 монет без пропущенных стрелой узлов.'],
    ['Трагический герой', 'На Очень сложном достичь уровня героя 10 с одним пассивом и четырьмя изъянами в замороженной конфигурации забега.'],
    ['Проще простого', 'На Очень сложном достичь уровня героя 5, не имея ни одного надетого навыка в этот момент.'],
    ['Вооружён и опасен', 'При записи активного забега иметь на одной надетой вещи не менее восьми ненулевых параметров. Предложения магазина не считаются.'],
    ['Собиратель обломков', 'Собрать 100 сломанных клеток. За каждую — 1%; награда ресурсом не нужна.'],
    ['Просто шучу', 'После подтверждённой цепи заполнить более девяти клеток, причём все новые клетки должны занять враги.'],
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
  function readHistoryRecord(record, retuning = true) {
    const keys = ['id', 'type', 'classId', 'difficulty', 'turn', 'xp', 'gold', 'upgrade', 'score', 'kills', 'won', 'finishedAt', 'skills', 'pretzel'];
    saveObject(record, [...keys, 'achievements'], keys);
    if (Object.hasOwn(record, 'achievements')) readAchievementMap(record.achievements, record.difficulty);
    readHistoryId(record.id); saveNumber(record.type, 0, 5, true); saveNumber(record.difficulty, 0, 3, true);
    saveCheck(!!classDefinition(record.classId) && (record.type > 3 || record.type === record.difficulty));
    for (const key of ['turn', 'xp', 'gold', 'upgrade', 'score', 'kills']) saveNumber(record[key], 0, Number.MAX_SAFE_INTEGER, true);
    saveNumber(record.finishedAt, 0, 8640000000000000, true); saveBool(record.won);
    saveCheck(!record.won || record.type === 4 && record.turn >= 100);
    saveArray(record.skills, 4).forEach(skill => {
      saveObject(skill, ['id', 'level']); saveCheck(typeof skill.id === 'string' && Object.hasOwn(SKILL_BY_ID, skill.id) && (skill.id !== 'retune' || retuning)); saveNumber(skill.level, 1, 10, true);
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
    if (RETUNE_DRAFT) return false;
    if (S?.resolving || S?.pendingModal && S.pendingModal !== 'gameover') { flash('Сначала заверши ход и выбор наград'); return false; }
    const wrap = el('history-modal'); if (!wrap) return false;
    closeGameSettings(false);
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
      return withWeaponElement({ type: 'arrow' });
    }
    return withWeaponElement({ type: t });
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

  function makeSkull(boss, special, typeId, elementsFrom) {

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
    return withSkullElement({
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
    }, elementsFrom);
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
        S.board[j] = withWeaponElement({ type: randomTileType() }, cell);
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
    // Пока пользователь в хосте (Telegram-пауза), ввод не наш: иначе первое
    // же нажатие после возврата «доклеивалось» к цепочке, начатой до паузы.
    if (!audioHostActive) return;
    if (!S || UPDATES_OPEN || HISTORY_OPEN || RETUNE_DRAFT || SAVE_CONFLICT || S.resolving || S.over || S.pendingModal || cellIdx < 0 || cellIdx >= CELL_COUNT) return;
    const chain = S.selected;
    const repeated = chain.indexOf(cellIdx);
    if (repeated >= 0) {
      chain.splice(repeated + 1);
      onChainChanged();
      return;
    }
    if (!canChainSelect(cellIdx, chain) || (chain.length && !isAdjacent(chain[chain.length - 1], cellIdx))) return;
    const firstSelection = chain.length === 0;
    chain.push(cellIdx);
    if (S.board[cellIdx].type === 'skull') queueCombatHaptic('selection');
    S.chainType = chainTypeForCell(S.board[chain[0]]);
    if (firstSelection && S.board[cellIdx].type === 'sword') audioSwordSelect();
    else if (firstSelection && S.board[cellIdx].type === 'coin') audioCoinSelect();
    else if (firstSelection && S.board[cellIdx].type === 'potion') audioPotionSelect();
    else if (firstSelection && S.board[cellIdx].type === 'shield') audioShieldSelect();
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

  function tileSymbol(cell, cellIdx = 0) {
    if (!cell || cell.type === 'empty') return '';
    if (cell.type === 'skull' && cell.skull?.phylactery) return `<img class="phylactery-art" src="${PHYLACTERY_IMAGE}" alt="">`;
    const start = '<svg viewBox="0 0 48 48" class="ic" aria-hidden="true">', end = '</svg>';
    if (cell.type === 'sword') return '<span class="weapon-art">' + raidWeaponTexture('ic') + raidWeaponTexture('weapon-glint') + '<span class="weapon-tip-sparkle" aria-hidden="true"></span></span>';
    if (cell.type === 'arrow') return start + '<path d="m35 4 8 1 1 8-9 8-2-6L12 36l-3-3 21-21-5-1Z" fill="var(--weapon-colour,#d5e9e4)"/><path d="m7 29 12 12-10 3-5-5Z" fill="#c9a769"/><path d="m14 34 20-20" stroke="var(--weapon-edge,#7fe5d2)" stroke-width="2"/>' + end;
    if (cell.type === 'shield') return raidShieldTexture('shield-texture');
    if (cell.type === 'coin') return '<span class="ic coin-3d" aria-hidden="true">'
      + '<i class="coin-edge"></i><i class="coin-edge coin-edge-belt"></i>'
      + '<span class="coin-face coin-obverse"><svg viewBox="0 0 48 48"><path d="m24 7 12 17-12 17L12 24Z" fill="#9b6213" stroke="#ffedaa" stroke-width="1.6"/><path d="m24 13 7 11-7 11-7-11Z" fill="#edbb4c"/><path d="m24 17 4 7-4 7-4-7Z" fill="#ffdf83"/><path d="M8 20 5 24l3 4m32-8 3 4-3 4" fill="none" stroke="#8e5710" stroke-width="1.5"/></svg></span>'
      + '<span class="coin-face coin-reverse"><svg viewBox="0 0 48 48"><path d="m24 8 4 10 11 1-8 7 3 11-10-6-10 6 3-11-8-7 11-1Z" fill="#a36813" stroke="#ffe9a3" stroke-width="1.5"/><path d="m24 17 2 6 6 1-5 4 2 5-5-3-5 3 2-5-5-4 6-1Z" fill="#f5cf65"/></svg></span></span>';
    if (cell.type === 'potion') {
      const kind = cell.acid ? 'acid' : cell.mana ? 'mana' : cell.explosive ? 'explosive' : 'health';
      const liquid = { health: '#d75b78', acid: '#b4d53b', mana: '#4d9fe6', explosive: '#e98b3c' }[kind];
      const clip = `raid-potion-fill-${cellIdx}`;
      const mark = kind === 'mana'
        ? '<path class="potion-mark" d="m24 25 4 6-4 7-4-7Z" fill="#f4fbff" stroke="#bfe8ff" stroke-width=".8"/>'
        : kind === 'acid'
          ? '<circle class="potion-mark potion-bubble" cx="20" cy="32" r="1.5"/><circle class="potion-mark potion-bubble potion-bubble-delay" cx="28" cy="35" r="1"/>'
          : kind === 'explosive'
            ? '<path class="potion-mark" d="m24 27 1.7 4.1 4.3.4-3.3 2.8 1 4.2-3.7-2.3-3.8 2.3 1-4.2-3.2-2.8 4.2-.4Z" fill="#fff0bc" stroke="#ffbd70" stroke-width=".7"/>'
            : '<path class="potion-mark" d="M24 28v10m-5-5h10" stroke="#fff1e5" stroke-width="2.2" stroke-linecap="round"/>';
      const surfaceY = { health: 26, acid: 29, mana: 26, explosive: 27 }[kind];
      const headClip = `raid-potion-head-${cellIdx}`;
      const bodyWindow = `raid-potion-body-${cellIdx}`;
      return start + `<defs><clipPath id="${headClip}"><rect x="0" y="0" width="48" height="23"/></clipPath><clipPath id="${bodyWindow}"><rect x="0" y="21" width="48" height="28"/></clipPath><clipPath id="${clip}"><path d="M24 16c-2.5 3.4-9 4.6-13 8.5-5 5.1-5.8 11.3-.3 18.1 7.1 4.1 25.5 4.1 32.6 0 5.5-6.8 4.7-13-.3-18.1-4-3.9-10.5-5.1-13-8.5Z"/></clipPath></defs><g class="potion-vessel" data-potion="${kind}"><image class="potion-art-head" href="miniapp_preview/assets/raid_potion_${kind}.png" x="0" y="0" width="48" height="48" preserveAspectRatio="xMidYMid meet" clip-path="url(#${headClip})"/><g class="potion-lower-window" clip-path="url(#${bodyWindow})"><g class="potion-lower-motion"><image class="potion-art-body" href="miniapp_preview/assets/raid_potion_${kind}.png" x="0" y="0" width="48" height="48" preserveAspectRatio="xMidYMid meet"/><g class="potion-slosh-overlay" clip-path="url(#${clip})"><path class="potion-wave potion-wave-back" d="M3 ${surfaceY}c5-2 8 1 13-1s8-2 12 0 8 1 17-1v10H3Z" fill="${liquid}" fill-opacity=".24"/><path class="potion-wave potion-wave-front" d="M3 ${surfaceY + 1}c6 1 8-2 14 0s8 2 13 0 9-1 15 1v10H3Z" fill="${liquid}" fill-opacity=".47"/><path class="potion-wave potion-surface-glint" d="M7 ${surfaceY + .4}q5-1 8-.2m7 .2q6 1 11-.1" fill="none" stroke="#fff8e8" stroke-opacity=".8" stroke-width=".7" stroke-linecap="round"/><circle class="potion-bubble" cx="18" cy="34" r=".9"/><circle class="potion-bubble potion-bubble-delay" cx="31" cy="37" r=".65"/>${mark}</g></g></g></g>` + end;
    }
    if (cell.type === 'trophy') return `<img class="trophy-art" src="${TROPHY_IMAGE}" alt="">`;
    if (cell.type === 'chain') return start + '<path d="m21 9-9 9a9 9 0 0 0 13 13l4-4m-10-6-4 4a9 9 0 0 0 13 13l9-9a9 9 0 0 0-13-13Z" stroke="#a6dce0" stroke-width="5" fill="none"/><path d="m17 30 14-14" stroke="#e9d597" stroke-width="3"/>' + end;
    return start + '<circle cx="24" cy="24" r="17" fill="#607c73"/><path d="m14 19 7 2-3 7-6-2m22-7-7 2 3 7 6-2M20 34h8" stroke="#142826" stroke-width="3" fill="none"/>' + end;
  }

  // Alchemy reference: discovery.css/js on arena/771875a3-expertbot
  // (37a857da), consolidated in PR87. Six resource/enemy materials plus the
  // weapon selection haze display assignments; legacy v1 palettes stay cosmetic.
  const ORB_ELEMENT_ORDER = RAID_ELEMENTS;
  const PHYLACTERY_IMAGE = 'assets/lich_phylactery.png';
  const TROPHY_IMAGE = 'assets/raid_trophy.png';
  const ORB_ELEMENT_LOOKS = [
    { state: 'fire', rgb: '249 111 65', light: '#ffd9a4', mid: '#a4492c', dark: '#361b22' },
    { state: 'gas', rgb: '177 222 241', light: '#e8faff', mid: '#537c91', dark: '#172b42' },
    { state: 'sand', rgb: '171 197 111', light: '#e2e9ad', mid: '#63733b', dark: '#1c3028' },
    { state: 'liquid', rgb: '71 185 232', light: '#b2eeff', mid: '#266c99', dark: '#112b44' },
  ];
  const ORB_ICE = '<svg viewBox="0 0 100 100" fill="none" aria-hidden="true"><path d="m11 31 16-20 24 5L69 9l20 23-5 22 7 17-24 19-18-7-22 7-16-22 8-19Z" fill="#c5f5ff" fill-opacity=".16" stroke="#dcfbff" stroke-opacity=".8" stroke-width="1.4"/><path d="m27 11 9 24-17 14m32-33-5 22 20 5L89 32M11 68l25-9 13 24m18 7-1-24 18-12M36 35l10 3-10 21 16-7 14 14m-20-28 6 14 14-9" stroke="#ebfcff" stroke-opacity=".7" stroke-width="1.1"/><path d="M52 29v36M36 38l32 18M36 56l32-18m-22-2 6 5 6-5m-12 22 6-5 6 5" stroke="#f1feff" stroke-width="1.7" stroke-linecap="round"/></svg>';
  const ORB_FIRE = '<svg viewBox="0 0 100 100" aria-hidden="true"><path d="M8 82c7-12 4-22 14-34-1 14 7 15 10 4 2-9 0-17 10-30 0 15 7 20 11 27 4-13 2-25 13-39 1 18 13 23 13 40 7-7 8-13 13-17-2 19 7 32 1 47-6 15-21 21-45 21S13 96 8 82Z" fill="#ff622b" fill-opacity=".72"/><path d="M24 84c8-11 1-17 11-29-1 12 7 14 12 3 4 11 11 12 13-2 11 13 6 21 17 30-5 9-16 13-28 13S31 93 24 84Z" fill="#ffd35e" fill-opacity=".78"/><path d="M43 87c1-10 5-15 10-22 3 9 10 13 9 22-2 7-6 10-10 10s-8-3-9-10Z" fill="#fff1a6" fill-opacity=".85"/></svg>';

  let ORB_ELEMENTS = new WeakMap();
  let ORB_VIEWS = new Map(); // resource object / enemy UID -> one movable DOM orb
  let ORB_VIEW_UID = 0;
  const ORB_ANIMATIONS = new Map();

  function orbElementIndex(cell, idx) {
    if (ELEMENTAL_COMBAT && (cell?.type === 'skull' || ['sword', 'arrow'].includes(cell?.type))) {
      const element = ORB_ELEMENT_ORDER.indexOf(cell.type === 'skull' ? cell.skull.element : cell.element);
      // Only mirror gameplay into the presentation cache (also read by SFX).
      // Rendering/saving cannot assign or reroll a gameplay element.
      ORB_ELEMENTS.set(cell, Math.max(0, element));
      return Math.max(0, element);
    }
    if (cell?.type === 'skull') return ORB_ELEMENT_ORDER.indexOf(RUN_REGION || RAID_REGION);
    if (!cell || !['sword', 'arrow'].includes(cell.type)) return 0;
    if (!ORB_ELEMENTS.has(cell)) {
      // Stable cosmetic hash, not any of the six gameplay streams. Cache by
      // object, not slot: gravity cannot recolour a sword. The optional save
      // array preserves that palette through explicit Continue, even mid-drop.
      let word = (S.seed ^ Math.imul(S.turn + 1, 0x9e3779b1) ^ Math.imul(idx + 1, 0x85ebca6b)) >>> 0;
      word = Math.imul(word ^ word >>> 16, 0x45d9f3b);
      ORB_ELEMENTS.set(cell, (word ^ word >>> 16) & 3);
    }
    return ORB_ELEMENTS.get(cell);
  }
  function orbIdentity(cell) { return cell.type === 'skull' ? 'skull:' + cell.skull.uid : cell; }
  function orbReducedMotion() { return !VISUAL_PREFERENCES.animations || systemReducedMotion(); }
  function animateOrb(node, frames, options, cleanup) {
    if (!node || orbReducedMotion() || typeof node.animate !== 'function') { cleanup?.(); return null; }
    let animation;
    try { animation = node.animate(frames, options); }
    catch (ignored) { cleanup?.(); return null; }
    let done = false;
    const finish = () => {
      if (done) return;
      done = true; ORB_ANIMATIONS.delete(animation); cleanup?.();
      animation.onfinish = animation.oncancel = null;
      animation.cancel(); // no retained fill/opacity/transform on a surviving orb
    };
    ORB_ANIMATIONS.set(animation, finish);
    animation.onfinish = animation.oncancel = finish;
    return animation;
  }
  const BOARD_FX = { canvas: null, ctx: null, w: 0, h: 0, dpr: 1, raf: 0, items: [] };
  let LAST_BOARD_ANIM_END = 0;

  function clearBoardFxCanvas() {
    BOARD_FX.items.length = 0;
    if (BOARD_FX.raf) { cancelAnimationFrame(BOARD_FX.raf); BOARD_FX.raf = 0; }
    if (BOARD_FX.ctx && BOARD_FX.canvas) {
      BOARD_FX.ctx.setTransform(1, 0, 0, 1, 0, 0);
      BOARD_FX.ctx.clearRect(0, 0, BOARD_FX.canvas.width, BOARD_FX.canvas.height);
    }
  }
  function ensureBoardFx() {
    const board = el('board');
    if (!board) return null;
    let layer = el('board-fx');
    if (!layer) {
      layer = document.createElement('div'); layer.id = 'board-fx';
      layer.setAttribute('aria-hidden', 'true'); board.appendChild(layer);
    }
    return layer;
  }
  function ensureBoardFxCanvas(boardRect) {
    if (orbReducedMotion() || !boardRect?.width || !boardRect?.height) return null;
    const layer = ensureBoardFx();
    if (!layer) return null;
    let canvas = BOARD_FX.canvas;
    if (!canvas || canvas.parentNode !== layer) {
      canvas = el('board-fx-canvas') || document.createElement('canvas');
      canvas.id = 'board-fx-canvas';
      canvas.setAttribute('aria-hidden', 'true');
      layer.appendChild(canvas);
      BOARD_FX.canvas = canvas;
      if (typeof window.CanvasRenderingContext2D === 'function' && !/jsdom/i.test(navigator.userAgent || '')) {
        try { BOARD_FX.ctx = canvas.getContext('2d'); }
        catch (ignored) { BOARD_FX.ctx = null; }
      } else {
        BOARD_FX.ctx = null;
      }
    }
    if (!BOARD_FX.ctx) return null;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.round(boardRect.width), h = Math.round(boardRect.height);
    if (BOARD_FX.w !== w || BOARD_FX.h !== h || BOARD_FX.dpr !== dpr) {
      BOARD_FX.w = w; BOARD_FX.h = h; BOARD_FX.dpr = dpr;
      canvas.width = Math.max(1, Math.round(w * dpr));
      canvas.height = Math.max(1, Math.round(h * dpr));
    }
    return BOARD_FX.ctx;
  }
  function parseOrbRgb(rgbStr) {
    const m = String(rgbStr || '').match(/(\d+)\D+(\d+)\D+(\d+)/);
    return m ? `${m[1]},${m[2]},${m[3]}` : '244,223,161';
  }
  function startBoardFxLoop() {
    if (BOARD_FX.raf || !BOARD_FX.ctx || !BOARD_FX.items.length) return;
    const tick = now => {
      BOARD_FX.raf = 0;
      const ctx = BOARD_FX.ctx;
      if (!ctx || !BOARD_FX.canvas?.isConnected || orbReducedMotion()) { clearBoardFxCanvas(); return; }
      const dpr = BOARD_FX.dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, BOARD_FX.w, BOARD_FX.h);
      const alive = [];
      for (const fx of BOARD_FX.items) {
        if (now < fx.start) { alive.push(fx); continue; }
        const t = (now - fx.start) / fx.dur;
        if (t >= 1) continue;
        alive.push(fx);
        if (fx.kind === 'surge') drawChainSurgeFx(ctx, fx, t);
        else drawOrbBurstFx(ctx, fx, t);
      }
      BOARD_FX.items = alive;
      if (alive.length) BOARD_FX.raf = requestAnimationFrame(tick);
      else ctx.clearRect(0, 0, BOARD_FX.w, BOARD_FX.h);
    };
    BOARD_FX.raf = requestAnimationFrame(tick);
  }
  function drawChainSurgeFx(ctx, fx, t) {
    const pts = fx.pts, segs = fx.segs, total = fx.total;
    if (pts.length < 2 || total < 1) return;
    const fade = t < .16 ? t / .16 : t > .78 ? (1 - t) / .22 : 1;
    ctx.save();
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
    ctx.strokeStyle = `rgba(${fx.rgb},${(0.26 * fade).toFixed(3)})`;
    ctx.lineWidth = 11; ctx.stroke();
    ctx.strokeStyle = `rgba(255,246,214,${(0.7 * fade).toFixed(3)})`;
    ctx.lineWidth = 2.8; ctx.stroke();
    // Moving energy pulse along the chain polyline.
    const prog = Math.min(1, t / .86);
    const eased = prog < .5 ? 2 * prog * prog : 1 - Math.pow(-2 * prog + 2, 2) / 2;
    const headDist = eased * total;
    const tailDist = Math.max(0, headDist - Math.min(82, total * .36));
    ctx.beginPath();
    let started = false, headX = pts[0].x, headY = pts[0].y;
    for (let i = 0; i < segs.length; i++) {
      const s = segs[i];
      if (s.end < tailDist) continue;
      if (s.start > headDist) break;
      const u0 = Math.max(0, (tailDist - s.start) / s.len);
      const u1 = Math.min(1, (headDist - s.start) / s.len);
      const x0 = s.x0 + (s.x1 - s.x0) * u0, y0 = s.y0 + (s.y1 - s.y0) * u0;
      const x1 = s.x0 + (s.x1 - s.x0) * u1, y1 = s.y0 + (s.y1 - s.y0) * u1;
      if (!started) { ctx.moveTo(x0, y0); started = true; }
      ctx.lineTo(x1, y1);
      headX = x1; headY = y1;
    }
    if (started) {
      ctx.strokeStyle = `rgba(255,255,255,${(0.95 * fade).toFixed(3)})`;
      ctx.lineWidth = 5.4; ctx.stroke();
      const r = 10 + 4 * fade;
      const g = ctx.createRadialGradient(headX, headY, 0, headX, headY, r);
      g.addColorStop(0, `rgba(255,255,255,${(0.95 * fade).toFixed(3)})`);
      g.addColorStop(0.45, `rgba(${fx.rgb},${(0.65 * fade).toFixed(3)})`);
      g.addColorStop(1, `rgba(${fx.rgb},0)`);
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(headX, headY, r, 0, Math.PI * 2); ctx.fill();
    }
    ctx.restore();
  }
  function drawOrbBurstFx(ctx, fx, t) {
    const { cx, cy, r, rgb, type, climax, boss, explosive, particles } = fx;
    const scale = climax || boss || explosive ? 1.24 : 1;
    ctx.save();
    // 1. Core bloom flash
    if (t < .62) {
      const ft = t / .62;
      const alpha = (ft < .22 ? ft / .22 : 1 - (ft - .22) / .78) * .88;
      const fr = r * (.42 + ft * 1.05) * scale;
      const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, fr);
      grad.addColorStop(0, `rgba(255,255,255,${alpha.toFixed(3)})`);
      grad.addColorStop(.42, `rgba(${rgb},${(alpha * .78).toFixed(3)})`);
      grad.addColorStop(1, `rgba(${rgb},0)`);
      ctx.fillStyle = grad;
      ctx.beginPath(); ctx.arc(cx, cy, fr, 0, Math.PI * 2); ctx.fill();
    }
    // 2. Expanding shockwave ring
    if (t < .86) {
      const rt = t / .86, ease = 1 - Math.pow(1 - rt, 2.2);
      const rr = r * (.36 + ease * 1.18) * scale;
      ctx.strokeStyle = `rgba(${rgb},${((1 - rt) * .9).toFixed(3)})`;
      ctx.lineWidth = Math.max(1, (2.4 - rt * 1.4) * scale);
      ctx.beginPath(); ctx.arc(cx, cy, rr, 0, Math.PI * 2); ctx.stroke();
    }
    // 3. Echo ring for shields, enemies, bosses & climax
    if ((climax || boss || explosive || type === 'shield' || type === 'skull') && t > .11 && t < .95) {
      const et = (t - .11) / .84, ease = 1 - Math.pow(1 - et, 2);
      const er = r * (.3 + ease * 1.38) * scale;
      ctx.strokeStyle = `rgba(255,255,255,${((1 - et) * .68).toFixed(3)})`;
      ctx.lineWidth = Math.max(.8, 1.6 - et);
      ctx.beginPath(); ctx.arc(cx, cy, er, 0, Math.PI * 2); ctx.stroke();
    }
    // 4. 4-point star flare for coins, trophy, climax & bosses
    if ((type === 'coin' || type === 'trophy' || climax || boss || explosive) && t < .68) {
      const st = t / .68, alpha = st < .25 ? st / .25 : 1 - (st - .25) / .75;
      const len = r * (.55 + st * .82) * scale, wid = Math.max(1.6, r * .13 * (1 - st * .55));
      ctx.save(); ctx.translate(cx, cy); ctx.rotate(.24 * st);
      ctx.fillStyle = `rgba(255,252,232,${(alpha * .92).toFixed(3)})`;
      for (let a = 0; a < 2; a++) {
        ctx.beginPath();
        ctx.moveTo(-len, 0); ctx.lineTo(0, -wid); ctx.lineTo(len, 0); ctx.lineTo(0, wid);
        ctx.closePath(); ctx.fill();
        ctx.rotate(Math.PI / 2);
      }
      ctx.restore();
    }
    // 5. Crisp diagonal slash for swords
    if (type === 'sword' && t < .58) {
      const st = t / .58, alpha = st < .22 ? st / .22 : 1 - (st - .22) / .78;
      const half = r * (0.4 + st * 1.05);
      ctx.save(); ctx.translate(cx, cy); ctx.rotate(-0.73);
      ctx.strokeStyle = `rgba(235,252,255,${(alpha * .95).toFixed(3)})`;
      ctx.lineWidth = Math.max(1.2, 2.8 * (1 - st * .5));
      ctx.lineCap = 'round';
      ctx.beginPath(); ctx.moveTo(-half, 0); ctx.lineTo(half, 0); ctx.stroke();
      ctx.restore();
    }
    // 6. Ballistic sparks, droplets, star-glints & shards
    const easeP = 1 - Math.pow(1 - t, 1.85);
    const alphaP = t < .12 ? t / .12 : Math.max(0, 1 - (t - .12) / .88);
    for (const p of particles) {
      const px = cx + p.dx * easeP;
      const py = cy + p.dy * easeP + p.grav * t * t;
      const sz = p.sz * (1 - t * .68);
      if (sz < .4 || alphaP <= .01) continue;
      if (p.shape === 'spark') {
        const prevEase = Math.max(0, 1 - Math.pow(1 - Math.max(0, t - .09), 1.85));
        const tx = cx + p.dx * prevEase;
        const ty = cy + p.dy * prevEase + p.grav * Math.max(0, t - .09) * Math.max(0, t - .09);
        ctx.strokeStyle = p.bright ? `rgba(255,255,255,${alphaP.toFixed(3)})` : `rgba(${rgb},${alphaP.toFixed(3)})`;
        ctx.lineWidth = Math.max(1.1, sz * .58);
        ctx.lineCap = 'round';
        ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(px, py); ctx.stroke();
      } else if (p.shape === 'droplet') {
        ctx.fillStyle = `rgba(${rgb},${(alphaP * .92).toFixed(3)})`;
        ctx.beginPath(); ctx.arc(px, py, sz, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = `rgba(255,255,255,${(alphaP * .75).toFixed(3)})`;
        ctx.beginPath(); ctx.arc(px - sz * .28, py - sz * .28, sz * .34, 0, Math.PI * 2); ctx.fill();
      } else if (p.shape === 'glint') {
        ctx.save(); ctx.translate(px, py); ctx.rotate(p.rot * t);
        ctx.fillStyle = `rgba(255,248,214,${alphaP.toFixed(3)})`;
        ctx.beginPath();
        ctx.moveTo(0, -sz * 1.45); ctx.lineTo(sz * .45, 0); ctx.lineTo(0, sz * 1.45); ctx.lineTo(-sz * .45, 0);
        ctx.closePath(); ctx.fill();
        ctx.rotate(Math.PI / 2); ctx.fill();
        ctx.restore();
      } else {
        ctx.save(); ctx.translate(px, py); ctx.rotate(p.rot * t);
        ctx.fillStyle = p.bright ? `rgba(255,255,255,${alphaP.toFixed(3)})` : `rgba(${rgb},${(alphaP * .95).toFixed(3)})`;
        ctx.fillRect(-sz * .7, -sz * .55, sz * 1.4, sz * 1.1);
        ctx.restore();
      }
    }
    ctx.restore();
  }
  function finishOrbAnimations() {
    for (const finish of [...ORB_ANIMATIONS.values()]) finish();
    clearBoardFxCanvas();
    LAST_BOARD_ANIM_END = 0;
    // Also clear anticipation; cancellation never runs a gameplay continuation.
    for (const view of ORB_VIEWS.values()) {
      view.motion = null; delete view.node.dataset.motion;
      view.node.classList.remove('primed');
      view.node.style.removeProperty('--prime-delay');
    }
    el('board-fx')?.replaceChildren();
    BOARD_FX.canvas = null; BOARD_FX.ctx = null; BOARD_FX.w = 0; BOARD_FX.h = 0;
  }
  function resetOrbPresentation() {
    finishOrbAnimations();
    ORB_VIEWS.clear(); ORB_ELEMENTS = new WeakMap(); ORB_VIEW_UID = 0;
  }
  function orbLook(cell, idx) {
    if (isPhylacteryCell(cell)) return { state: 'sand', rgb: '228 190 94', light: '#ffeab0', mid: '#907331', dark: '#352c20' };
    const index = orbElementIndex(cell, idx), elemental = ORB_ELEMENT_LOOKS[index];
    // Unselected glass is clear. Elemental metal tint is CSS-only; the old
    // 2/3-height body appears only on selection (air above, water below).
    if (['sword', 'arrow'].includes(cell.type)) return { ...elemental, state: 'gas', light: '#d9e7ed', mid: '#344957', dark: '#101e2b' };
    if (cell.type === 'skull') {
      if (index === 2) return { ...elemental, state: /ent|worm/.test(raidPortraitKey(cell.skull)) ? 'organic' : 'solid' };
      return elemental;
    }
    if (cell.type === 'coin' || cell.type === 'trophy') return { state: cell.type === 'coin' ? 'sand' : 'solid', rgb: '228 190 94', light: '#ffeab0', mid: '#907331', dark: '#352c20' };
    if (cell.type === 'shield') return { state: 'solid', rgb: '117 192 195', light: '#d0ece7', mid: '#3b666c', dark: '#152e38' };
    if (cell.type === 'chain') return { state: 'gas', rgb: '183 151 235', light: '#e7d8ff', mid: '#6a548d', dark: '#29223e' };
    if (cell.acid) return { state: 'liquid', rgb: '167 218 84', light: '#e7fca7', mid: '#5b7930', dark: '#253627' };
    if (cell.mana) return { ...ORB_ELEMENT_LOOKS[3], light: '#e0d6ff', mid: '#4e65a7' };
    return { state: 'liquid', rgb: '237 128 153', light: '#ffdade', mid: '#9b4e68', dark: '#3e2438' };
  }
  function primeOrbChain(indices) {
    if (!indices?.length) return;
    const step = indices.length > 1 ? Math.min(28, 150 / (indices.length - 1)) : 0;
    let surgeRgb = '';
    for (let i = 0; i < indices.length; i++) {
      const cell = S.board[indices[i]];
      if (!cell || cell.type === 'empty') continue;
      const node = ORB_VIEWS.get(orbIdentity(cell))?.node;
      if (!node) continue;
      node.style.setProperty('--prime-delay', Math.round(i * step) + 'ms');
      node.classList.add('primed');
      if (!surgeRgb) surgeRgb = node.style.getPropertyValue('--orb-rgb').trim();
    }
    if (orbReducedMotion() || indices.length < 2) return;
    const board = el('board');
    const boardRect = board?.getBoundingClientRect();
    if (!ensureBoardFxCanvas(boardRect)) return;
    const pts = indices.map(cellCenter);
    const segs = [];
    let total = 0;
    for (let i = 1; i < pts.length; i++) {
      const len = Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
      if (len > .5) {
        segs.push({ x0: pts[i - 1].x, y0: pts[i - 1].y, x1: pts[i].x, y1: pts[i].y, start: total, end: total + len, len });
        total += len;
      }
    }
    if (total < 2) return;
    BOARD_FX.items = BOARD_FX.items.filter(item => item.kind !== 'surge');
    BOARD_FX.items.push({ kind: 'surge', pts, segs, total, rgb: parseOrbRgb(surgeRgb), start: performance.now(), dur: 405 });
    startBoardFxLoop();
  }
  function addOrbBurstFx(boardRect, cx, cy, size, rgbStr, type, delay, orbId, opts = {}) {
    if (!ensureBoardFxCanvas(boardRect)) return;
    const climax = !!opts.climax, boss = !!opts.boss, explosive = !!opts.explosive;
    const intensity = Math.max(.9, Math.min(1.4, opts.intensity ?? 1));
    const seed = Math.imul(Number(orbId) || 1, 2654435761) >>> 0;
    const count = boss || explosive ? 12 : climax ? 10 : opts.dense ? 7 : 9;
    const radius = size * (.68 + intensity * .2);
    const particles = [];
    for (let i = 0; i < count; i++) {
      const h1 = (Math.imul(seed ^ (i + 1) * 2246822519, 3266489917) >>> 0) / 4294967296;
      const h2 = (Math.imul(seed ^ (i + 7) * 3266489917, 668265263) >>> 0) / 4294967296;
      const angle = (i / count) * Math.PI * 2 + (h1 - .5) * .55;
      const dist = radius * (.58 + h2 * .56);
      const shape = type === 'potion' ? 'droplet'
        : type === 'coin' || type === 'trophy' ? (i % 2 ? 'glint' : 'spark')
        : type === 'sword' || type === 'arrow' || type === 'chain' ? 'spark'
        : (i % 2 ? 'shard' : 'spark');
      particles.push({
        dx: Math.cos(angle) * dist,
        dy: Math.sin(angle) * dist - size * .08,
        grav: 10 + h1 * 16,
        sz: 2.2 + h2 * 2.8,
        rot: (h2 - .5) * 6.5,
        bright: i % 3 === 0,
        shape,
      });
    }
    BOARD_FX.items.push({
      kind: 'burst', cx, cy, r: size * .52, rgb: parseOrbRgb(rgbStr), type,
      climax, boss, explosive, particles,
      start: performance.now() + delay, dur: 380,
    });
    startBoardFxLoop();
  }
  function burstOrb(view, rect, boardRect, delay = 0, duration = 240, opts = {}) {
    view.motion?.cancel();
    view.node.classList.remove('primed');
    view.node.style.removeProperty('--prime-delay');
    if (!rect?.width || orbReducedMotion() || typeof view.node.animate !== 'function') { view.node.remove(); return; }
    const board = el('board'), layer = ensureBoardFx();
    if (!board || !layer) { view.node.remove(); return; }
    const type = view.node.dataset.type || '';
    const climax = !!opts.climax, boss = !!opts.boss || view.node.classList.contains('boss');
    const explosive = !!opts.explosive || view.node.classList.contains('explosive');
    const left = rect.left - boardRect.left - board.clientLeft;
    const top = rect.top - boardRect.top - board.clientTop;
    const rgb = view.node.style.getPropertyValue('--orb-rgb');
    addOrbBurstFx(boardRect, left + rect.width / 2, top + rect.height / 2, rect.width, rgb, type, delay, view.node.dataset.orbId, {
      intensity: opts.intensity, climax, boss, explosive, dense: opts.dense,
    });
    // Minimal 1-node DOM ghost for the collapsing orb; all sparks/rings/flashes render on the single canvas.
    const burst = document.createElement('div');
    burst.className = 'orb-burst';
    burst.dataset.orbId = view.node.dataset.orbId;
    burst.dataset.type = type;
    if (view.node.dataset.element) burst.dataset.element = view.node.dataset.element;
    burst.style.cssText = `left:${left}px;top:${top}px;width:${rect.width}px;height:${rect.height}px;--orb-rgb:${rgb};--burst-delay:${delay}ms`;
    view.node.classList.add('orb-ghost'); burst.appendChild(view.node);
    layer.appendChild(burst);
    animateOrb(view.node, [
      { transform: 'scale(1)', opacity: 1 },
      { transform: `scale(${climax || boss ? 1.18 : 1.13})`, opacity: .96, offset: .24 },
      { transform: 'scale(.08)', opacity: 0 },
    ], { duration, delay, easing: 'ease-out', fill: 'both' }, () => view.node.remove());
    animateOrb(burst, [{ opacity: 1 }, { opacity: 1, offset: .64 }, { opacity: 0 }], { duration: Math.max(360, duration + 200), delay }, () => burst.remove());
  }
  function moveOrb(view, dx, dy, incoming, delay, pitch = 0) {
    if (Math.abs(dx) + Math.abs(dy) < 1) return 0;
    view.motion?.cancel();
    const falling = Math.abs(dx) < 1 && dy < 0;
    view.node.dataset.motion = incoming ? 'refill' : falling ? 'fall' : 'shift';
    let frames, duration, easing;
    if (falling) {
      const cellPitch = pitch > 1 ? pitch : Math.max(44, Math.abs(dy));
      const rows = Math.max(1, Math.abs(dy) / cellPitch);
      // Natural per-orb fall duration determined strictly by its own drop distance —
      // never compressed by chain length (1 row ~335ms, 3 rows ~441ms, 6 rows ~545ms).
      duration = Math.round(190 + Math.sqrt(rows) * 145);
      frames = [
        { transform: `translate3d(0,${dy}px,0)`, opacity: 1 },
        { transform: 'translate3d(0,0,0)', opacity: 1 },
      ];
      easing = 'cubic-bezier(.30,.08,.42,1)';
    } else {
      duration = 340;
      frames = [
        { transform: `translate3d(${dx}px,${dy}px,0)`, opacity: incoming ? .35 : 1 },
        { transform: 'translate3d(0,0,0)', opacity: 1 },
      ];
      easing = 'cubic-bezier(.22,1,.36,1)';
    }
    let animation;
    animation = animateOrb(view.node, frames, { duration, delay, easing, fill: 'both' }, () => {
      if (view.motion === animation) { view.motion = null; delete view.node.dataset.motion; }
    });
    view.motion = animation;
    if (!animation) { delete view.node.dataset.motion; return 0; }
    return delay + duration;
  }

  function renderCell(cell, idx, view) {
    const cellEl = qs(`.cell[data-idx="${idx}"]`);
    if (!cellEl) return;
    if (!view) {
      cellEl.className = 'cell empty'; cellEl.replaceChildren();
      cellEl.title = ''; cellEl.removeAttribute('aria-label'); return;
    }
    const orb = view.node, s = cell.skull, phylactery = isPhylacteryCell(cell);
    const isSkull = cell.type === 'skull' && !phylactery;
    const frozen = !!cell.frozen || !!(isSkull ? s.stunned : cell.stunned);
    const burned = !!(isSkull ? s.burn : cell.burn), boss = !!s?.boss && !phylactery, special = !!s?.special && !boss && !phylactery;
    const look = orbLook(cell, idx), element = ORB_ELEMENT_ORDER[orbElementIndex(cell, idx)];
    const status = (cell.broken ? ' broken' : '') + (cell.acid ? ' acid' : '') + (cell.mana ? ' mana' : '')
      + (burned ? ' burned' : '') + (frozen ? ' frozen' : '') + (boss ? ' boss' : special ? ' special' : '')
      + (cell.explosive ? ' explosive' : '') + (cell.magicSword ? ' magic-sword' : '') + (s?.phylactery ? ' phylactery' : '');
    cellEl.className = 'cell tile type-' + cell.type + status;
    orb.className = 'raid-orb fx state-' + look.state + status;
    orb.dataset.type = cell.type; orb.dataset.state = look.state;
    if (['sword', 'arrow', 'skull'].includes(cell.type)) orb.dataset.element = element;
    else delete orb.dataset.element;
    orb.style.setProperty('--orb-rgb', look.rgb); orb.style.setProperty('--orb-light', look.light);
    orb.style.setProperty('--orb-mid', look.mid); orb.style.setProperty('--orb-dark', look.dark);
    const key = `${cell.type}:${!!cell.acid}:${!!cell.mana}:${!!cell.explosive}:${!!cell.magicSword}:${isSkull ? s.uid : ''}`;
    if (view.key !== key) {
      const weapon = ['sword', 'arrow'].includes(cell.type);
      orb.innerHTML = (weapon
        ? '<span class="orb-material orb-fog" aria-hidden="true"><i class="orb-fog-wisp"></i><i class="orb-fog-wisp orb-fog-back"></i></span>'
        : '<span class="orb-material" aria-hidden="true"></span>') + '<span class="orb-core"></span>'
        + (isSkull ? '<div class="skull-badge"></div><span class="orb-countdown"></span>' : cell.magicSword ? '<span class="orb-charge">5</span>' : '')
        + '<span class="orb-ice">' + ORB_ICE + '</span><span class="orb-fire" aria-hidden="true">' + ORB_FIRE + '</span><span class="orb-crack" aria-hidden="true"></span>';
      view.key = key;
      const core = orb.querySelector('.orb-core');
      if (isSkull) {
        let img = SKULL_IMAGES.get(s.uid);
        if (!img) { img = document.createElement('img'); SKULL_IMAGES.set(s.uid, img); }
        img.className = 'skull-art'; img.alt = ''; img.draggable = false;
        core.appendChild(img);
      } else core.innerHTML = tileSymbol(cell, idx);
    }
    if (cell.type === 'sword') orb.querySelectorAll('.weapon-texture').forEach(syncRaidWeaponTexture);
    if (cell.type === 'shield') syncRaidShieldTexture(orb.querySelector('.shield-texture'));
    let label;
    if (isSkull) {
      const img = orb.querySelector('img'), portrait = raidPortraitKey(s);
      if (img.dataset.portrait !== portrait) { img.src = raidPortrait(s); img.dataset.portrait = portrait; }
      const badge = orb.querySelector('.skull-badge');
      badge.className = 'skull-badge' + (boss ? ' boss' : special ? ' special' : '');
      const text = `${s.atk}·${s.def}·${s.hp}`;
      if (badge.textContent !== text) badge.textContent = text;
      badge.style.setProperty('--badge-font', Math.min(19, 160 / text.length) + 'cqw');
      const countdown = orb.querySelector('.orb-countdown');
      countdown.textContent = s.countdown > 0 ? String(s.countdown) : ''; countdown.hidden = !(s.countdown > 0);
      const cripple = clsRace() === 'Werewolf' && isCrippleTarget(s);
      cellEl.classList.toggle('cripple-target', cripple);
      label = `${boss ? 'Босс' : special ? ENEMY_FEATS[s.type]?.name || 'Особый враг' : 'Враг'}: атака ${s.atk}, защита ${s.def}, HP ${s.hp}`
        + (s.countdown > 0 ? `, таймер ${s.countdown}` : '') + (cripple ? '. Калечение: HP ниже защиты; сбор в конце хода' : '')
        + ' · ' + (ELEMENTAL_COMBAT ? combatElementLabel(s) + (boss ? ' (среднее двух стихий)' : '') : RAID_REGIONS[element].element + ' (внешность локации; бой классический)');
      if (s.type === 7) label += ' · Опасность для героя — обычная атака Огненного (×2), она входит в общий залп. Каждый ход он также поджигает до 8 занятых соседних клеток: после залпа горящие соседи-враги получают урон, равный его атаке, но сам поджог герою не вредит. Убей Огненного, чтобы прекратить будущие поджоги.';
    } else {
      label = phylactery ? 'Филактерий Лича: включи в цепочку из 3+ монет в следующем ходу, иначе Лич воскреснет'
        : { sword: 'Оружие: ' + S.equipment.find(item => item.slot === 'weapon').name, arrow: 'Стрела', shield: 'Щит', potion: cell.acid ? 'Кислотное зелье' : cell.mana ? 'Зелье маны' : 'Зелье', coin: 'Монета', chain: 'Цепь', trophy: 'Трофей: рост или открытие меха без хода врагов' }[cell.type] || '';
      if (['sword', 'arrow'].includes(cell.type)) label += ' · ' + RAID_REGIONS[element].element + (ELEMENTAL_COMBAT ? ' (×1,25 / ×1 / ×0,75 по цели)' : ' (оформление; урон классический)');
      if (cell.magicSword) label += ', сила 5 мечей';
      if (cell.explosive) label += ', взрывное';
      if (cell.broken) label += ', сломано: без награды';
    }
    if (frozen) label += ', заморожено';
    if (burned) label += `, горит: ${isSkull ? (s.fireDamage ?? (typeof s.burn === 'number' ? s.burn : baseDamage())) : (cell.fireDamage ?? (typeof cell.burn === 'number' ? cell.burn : baseDamage()))} огненного урона в конце хода${frozen ? ', после оттаивания' : ''}`;
    cellEl.title = label; cellEl.setAttribute('aria-label', label);
    if (orb.parentNode !== cellEl) cellEl.appendChild(orb);
    // Remove an orb vacating this socket, never rebuild the incoming object.
    for (const child of [...cellEl.children]) if (child !== orb) child.remove();
    view.burned = burned;
    if (view.initialized && view.frozen !== frozen) {
      animateOrb(orb.querySelector('.orb-ice'), frozen
        ? [{ transform: 'scale(.15) rotate(-12deg)', opacity: 0 }, { transform: 'scale(1.06)', opacity: 1, offset: .75 }, { transform: 'scale(1)', opacity: 1 }]
        : [{ transform: 'scale(1)', opacity: 1 }, { transform: 'scale(1.15)', opacity: 0 }], { duration: frozen ? 380 : 260, easing: 'ease-out' });
    }
    view.frozen = frozen; view.initialized = true;
  }

  function renderBoard(chainOrder = []) {
    const board = qs('#board');
    if (!board) return;
    if (!board.dataset.cellsReady) {
      board.innerHTML = '';
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('id', 'chain-line'); svg.setAttribute('aria-hidden', 'true'); board.appendChild(svg);
      for (let i = 0; i < CELL_COUNT; i++) {
        const cellEl = document.createElement('div'); cellEl.className = 'cell'; cellEl.dataset.idx = String(i); board.appendChild(cellEl);
      }
      board.dataset.cellsReady = '1';
    }
    const previous = ORB_VIEWS, next = new Map(), rects = new Map(), boardRect = board.getBoundingClientRect();
    const keys = S.board.map(cell => cell && cell.type !== 'empty' ? orbIdentity(cell) : null);
    // FIRST: capture the actual, possibly interrupted on-screen positions before
    // touching the DOM. A same-type neighbour is NOT the same orb.
    for (const [key, view] of previous) rects.set(key, view.node.getBoundingClientRect());
    const removed = [...previous].filter(([key]) => !keys.includes(key));
    if (removed.length) BOARD_FX.items = BOARD_FX.items.filter(item => item.kind !== 'surge');
    // Gesture order is local presentation data captured before settlement.
    // Only actual disappearances participate: never burst a surviving target
    // or an arrow-skipped resource. Area/Rune victims have no gesture order.
    const ordered = chainOrder.filter(key => previous.has(key) && !keys.includes(key));
    // Sequential per-orb pop cadence (never crushed into a fixed 120ms cap):
    // 62ms/orb for normal chains, gently tapering down to 36ms/orb on huge snakes.
    const step = ordered.length > 1 ? Math.max(36, 62 - Math.max(0, ordered.length - 6) * 2) : 0;
    const corePopDur = 160;
    const delays = new Map(ordered.map((key, index) => [key, index * step]));
    const orderIdx = new Map(ordered.map((key, index) => [key, index]));
    // Per-column readiness: each column starts dropping as soon as all its own
    // disappearing orbs have finished popping — no column waits on the far side.
    const colFallDelay = Array(COLS).fill(null);
    const chainIntensity = ordered.length >= 8 ? 1.35 : ordered.length >= 5 ? 1.18 : 1;
    const dense = removed.length >= 12;
    let animEndMs = 0;
    const canAnimate = previous.size > 0 && !orbReducedMotion() && typeof board.animate === 'function';
    for (const [key, view] of removed) {
      const col = typeof view.slot === 'number' ? view.slot % COLS : -1;
      const d = delays.get(key) || 0, coreDur = delays.has(key) ? corePopDur : 240;
      const doneAt = d + coreDur;
      if (col >= 0 && col < COLS) colFallDelay[col] = Math.max(colFallDelay[col] ?? 0, doneAt);
      const isBoss = view.node.classList.contains('boss'), isExplosive = view.node.classList.contains('explosive');
      const idxInChain = orderIdx.get(key);
      const climax = isBoss || isExplosive || (ordered.length >= 5 && idxInChain === ordered.length - 1);
      const progress = ordered.length > 1 && idxInChain !== undefined ? idxInChain / (ordered.length - 1) : 0;
      const r = rects.get(key);
      if (canAnimate && r?.width) animEndMs = Math.max(animEndMs, doneAt);
      burstOrb(view, r, boardRect, d, coreDur, {
        intensity: chainIntensity + progress * .2,
        climax, boss: isBoss, explosive: isExplosive, dense,
      });
      if (typeof key === 'string' && key.startsWith('skull:')) SKULL_IMAGES.delete(Number(key.slice(6)));
    }
    const fallDelay = ordered.length ? Math.max(...removed.map(([key]) => delays.has(key) ? delays.get(key) + corePopDur : 240)) : 65;
    const incoming = Array(COLS).fill(0);
    for (let c = 0; c < COLS; c++) for (let r = 0; r < ROWS; r++) {
      const key = keys[r * COLS + c];
      if (!key || previous.has(key)) break;
      incoming[c]++;
    }
    // LAST: batch ALL writes before ALL final geometry reads. Measuring and
    // starting each orb in one loop forced 36 layouts and staggered animation
    // clocks on phones. Interrupted moves still start at their on-screen rect.
    const plans = [], firePulses = [];
    for (let idx = 0; idx < CELL_COUNT; idx++) {
      const key = keys[idx]; let view = key && previous.get(key);
      const wasBurned = view?.burned;
      if (key && !view) {
        const node = document.createElement('div'); node.dataset.orbId = String(++ORB_VIEW_UID);
        node.style.setProperty('--orb-phase', -(ORB_VIEW_UID * 137 % 3100) + 'ms');
        view = { node, slot: idx, initialized: false, motion: null };
      }
      const moved = previous.has(key) && view.slot !== idx;
      if (moved) view.motion?.cancel();
      renderCell(S.board[idx], idx, view);
      const cell = S.board[idx], target = cell?.type === 'skull' ? cell.skull : cell;
      if (view && wasBurned === false && !!target?.burn) firePulses.push(view.node.querySelector('.orb-fire'));
      if (!view) continue;
      next.set(key, view);
      if (moved || !previous.has(key)) plans.push({ view, idx, old: rects.get(key), moved });
      view.slot = idx;
    }
    if (previous.size && !orbReducedMotion()) {
      for (const plan of plans) plan.rect = plan.view.node.getBoundingClientRect();
      const first = qs('.cell[data-idx="0"]').getBoundingClientRect();
      const pitch = qs('.cell[data-idx="6"]').getBoundingClientRect().top - first.top;
      // Rank falling orbs bottom-up inside each column for a natural cascade.
      const colRanks = Array.from({ length: COLS }, () => []);
      for (const plan of plans) if (plan.rect.width) colRanks[plan.idx % COLS].push(plan);
      const rankOf = new Map();
      for (const colPlans of colRanks) {
        colPlans.sort((a, b) => b.idx - a.idx);
        colPlans.forEach((plan, rank) => rankOf.set(plan, rank));
      }
      // All animate() calls stay pending until the SAME next rendering frame.
      // Never force startTime to document.timeline.currentTime here: during a
      // long input/layout task it is the PREVIOUS frame's timestamp and would
      // skip the beginning (or all) of a fall on a slow device.
      // Each orb drops individually at its own natural pace (+45ms per orb bottom-up
      // in the column), never squeezed into a fixed global time cap.
      for (const plan of plans) {
        const { view, idx, old, moved, rect } = plan;
        if (!rect.width) continue;
        const col = idx % COLS, baseColDelay = colFallDelay[col] ?? fallDelay;
        const rank = rankOf.get(plan) || 0;
        const orbDelay = baseColDelay + rank * 45;
        if (moved && old?.width) animEndMs = Math.max(animEndMs, moveOrb(view, old.left - rect.left, old.top - rect.top, false, orbDelay, pitch));
        else if (!old) {
          const row = Math.floor(idx / COLS), count = incoming[col];
          if (row < count) animEndMs = Math.max(animEndMs, moveOrb(view, 0, -pitch * count, true, orbDelay, pitch));
          else {
            const appearDelay = ordered.length ? baseColDelay : 0;
            if (animateOrb(view.node, [{ transform: 'scale(.72)', opacity: 0 }, { transform: 'scale(1)', opacity: 1 }], { duration: 260, delay: appearDelay, fill: 'both' })) {
              animEndMs = Math.max(animEndMs, appearDelay + 260);
            }
          }
        }
      }
    }
    if (chainOrder.length || removed.length) LAST_BOARD_ANIM_END = animEndMs;
    ORB_VIEWS = next;
    if (firePulses.length && !orbReducedMotion()) requestAnimationFrame(() => {
      for (const overlay of firePulses) {
        if (!overlay?.isConnected) continue;
        overlay.classList.remove('ignite-pop');
        requestAnimationFrame(() => { if (overlay.isConnected && !orbReducedMotion()) overlay.classList.add('ignite-pop'); });
      }
    });
    renderSelection(); renderChainLine();
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
    for (const target of skulls) target.damage = primaryChainDamage(target.idx, chain, effective, damage);
    // Fractional elemental sums round ONCE in damageSkull, not per sword.
    // Keep legacy previews unchanged, including their old fractional boundary.
    const targetLethal = target => (ELEMENTAL_COMBAT ? Math.round(target.damage) : target.damage) >= target.skull.hp + Math.max(0, target.skull.def || 0);
    const lethals = skulls.filter(target => target.skull.type !== 32 && targetLethal(target));
    const phylacteries = skulls.filter(target => target.skull.type === 32 && targetLethal(target));
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
      swordCount, skulls, lethals, phylacteries, interior, resources, specialRewards, acid, mana };
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
    if (preview.swordCount || preview.skulls.length) {
      parts.push(`Урон ${preview.damage}${runPilotAffinity()?.element ? ' (до стихий и склонности)' : ELEMENTAL_COMBAT ? ' (нейтр.)' : ''}`);
      const phylacteryCount = preview.phylacteries?.length || 0;
      const killableCount = preview.skulls.length - phylacteryCount;
      if (killableCount > 0 || !phylacteryCount) parts.push(`Убито ${preview.lethals.length}/${killableCount}`);
      if (phylacteryCount > 0) parts.push(phylacteryCount === 1 ? 'Лич → филактерий' : `Лич → филактерий ×${phylacteryCount}`);
    }
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
    qsa('.cell').forEach(c => c.classList.remove('lethal', 'phylactery-ready', 'rune-inside', 'rune-protected'));
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
    // lethal skulls in the chain: highlight + cross out; Lich at lethal damage: gold phylactery highlight
    if (S.selected.length >= 3) {
      const p = chainPreview();
      if (p && p.skulls && p.skulls.length > 0) {
        p.lethals.forEach(x => {
          const cellEl = qs(`.cell[data-idx="${x.idx}"]`);
          if (cellEl) cellEl.classList.add('lethal');
        });
        (p.phylacteries || []).forEach(x => {
          const cellEl = qs(`.cell[data-idx="${x.idx}"]`);
          if (cellEl) cellEl.classList.add('phylactery-ready');
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

  const HUD_WIDE_WEAPONS = new Set(['techno-hammer-v2', 'techno-axe-v2', 'techno-mace-v2']);
  let hudAnimPrev = null;

  function triggerHudPulse(node, cls, ms = 420) {
    if (!node || document.documentElement.dataset.raidMotion === 'off') return;
    if (!node._hudTimers) node._hudTimers = Object.create(null);
    if (node._hudTimers[cls]) clearTimeout(node._hudTimers[cls]);
    node.classList.remove(cls);
    void node.offsetWidth;
    node.classList.add(cls);
    node._hudTimers[cls] = setTimeout(() => {
      node.classList.remove(cls);
      delete node._hudTimers[cls];
    }, ms);
  }

  function renderHUD() {
    for (const node of qsa('[data-raid-combat-title]')) node.textContent = ELEMENTAL_COMBAT ? 'RAID / СТИХИИ' : 'RAID / ПРЕЖНИЙ БОЙ';
    for (const node of qsa('[data-raid-combat-note]')) node.textContent = ELEMENTAL_COMBAT
      ? 'Стихийный бой · ±25% каждого меча/стрелы; вторичные эффекты — как прежде.' + (runPilotAffinity()?.element ? ' ' + pilotAffinityDescription() : '')
      : 'Прежний забег · классический бой без стихийных множителей. Новый старт включает стихии.';
    const h = S.hero;
    const hpPct = Math.max(0, Math.min(100, h.hp / h.maxHp * 100));
    const armorPct = Math.max(0, Math.min(100, h.maxArmor > 0 ? h.armor / h.maxArmor * 100 : 0));
    const xpPct = Math.max(0, Math.min(100, h.xp / h.xpNext * 100));
    const goldPct = Math.max(0, Math.min(100, h.gold / GOLD_BOX_MAX * 100));
    const upPct = Math.max(0, Math.min(100, S.upgradeBar / UPGRADE_BAR_MAX * 100));
    const w = el('hp-bar'); if (w) w.style.height = hpPct + '%';
    const a = el('armor-bar'); if (a) a.style.height = armorPct + '%';
    const x = el('xp-bar'); if (x) x.style.height = xpPct + '%';
    const g = el('gold-bar'); if (g) g.style.height = goldPct + '%';
    const u = el('upgrade-bar'); if (u) u.style.height = upPct + '%';
    if (el('hp-text')) el('hp-text').textContent = `${fmt(h.hp)} / ${fmt(h.maxHp)}`;
    if (el('armor-text')) el('armor-text').textContent = `${fmt(h.armor)} / ${fmt(h.maxArmor)}`;
    if (el('xp-text')) el('xp-text').textContent = `${fmt(h.xp)}/${fmt(h.xpNext)}`;
    if (el('gold-text')) el('gold-text').textContent = `${fmt(h.gold)}/${GOLD_BOX_MAX}`;
    if (el('upgrade-text')) el('upgrade-text').textContent = `${fmt(S.upgradeBar)}/${UPGRADE_BAR_MAX}`;
    const upIconHost = el('hud-upgrade-icon');
    if (upIconHost && !upIconHost.firstElementChild) upIconHost.innerHTML = raidModuleIcon('enchant');
    const hpSlot = el('hud-hp');
    if (hpSlot) hpSlot.dataset.lowHp = h.hp > 0 && hpPct <= 30 ? '1' : '0';
    const cls = CLASSES.find(entry => entry.id === S.classId) || CLASSES[0];
    const mechArt = el('hud-mech-art');
    if (mechArt && cls) {
      const nextSrc = `miniapp_preview/assets/mech_${cls.id}.png`;
      if (mechArt.getAttribute('src') !== nextSrc) mechArt.setAttribute('src', nextSrc);
      mechArt.dataset.mechId = cls.id;
    }
    if (el('hud-mech-name') && cls) el('hud-mech-name').textContent = `${cls.name} · Ур. ${h.level}`;
    const weaponImg = el('hud-weapon-art');
    if (weaponImg) syncRaidWeaponTexture(weaponImg);
    const weaponGlint = el('hud-weapon-glint');
    if (weaponGlint) syncRaidWeaponTexture(weaponGlint);
    const weaponStage = el('hud-weapon-stage');
    if (weaponStage && weaponImg) {
      weaponStage.dataset.weaponWide = HUD_WIDE_WEAPONS.has(weaponImg.dataset.weaponArt) ? '1' : '0';
    }
    const shieldImg = el('hud-shield-art');
    if (shieldImg) syncRaidShieldTexture(shieldImg);
    const armorSlot = el('hud-armor');
    if (armorSlot) {
      armorSlot.dataset.empty = h.armor <= 0 ? '1' : '0';
      const shieldItem = S.equipment?.find(entry => entry.slot === 'shield');
      armorSlot.title = `${shieldItem ? shieldItem.name : 'Щит'} · броня ${fmt(h.armor)} / ${fmt(h.maxArmor)}`;
    }
    const weapon = h.weaponDamage * (S.swordBoost > 0 ? 2 : 1);
    const baseDmg = baseDamage();
    const weaponVal = el('hud-weapon-val');
    if (weaponVal) {
      weaponVal.textContent = fmt(weapon);
      weaponVal.classList.toggle('boosted', S.swordBoost > 0);
    }
    if (el('hud-weapon-base')) el('hud-weapon-base').textContent = `+${fmt(baseDmg)} база`;
    const dmgTitle = `Урон цепочки: база ${fmt(baseDmg)} + ${fmt(weapon)} за каждый меч; защита врага поглощает урон до HP.`
      + (ELEMENTAL_COMBAT ? ' База нейтральна; вклад каждого меча/стрелы ×1,25 / ×1 / ×0,75 по стихии цели. Для босса — среднее двух множителей; округление один раз перед бронёй.' : ' Прежний забег: стихийных множителей нет.')
      + (runPilotAffinity()?.element ? ' ' + pilotAffinityDescription() : '');
    const weaponSlot = el('hud-weapon');
    if (weaponSlot) {
      weaponSlot.dataset.boosted = S.swordBoost > 0 ? '1' : '0';
      const weaponItem = S.equipment?.find(entry => entry.slot === 'weapon');
      weaponSlot.title = `${weaponItem ? weaponItem.name : 'Оружие'} · ${dmgTitle}`;
    }
    const weaponKey = weaponImg ? weaponImg.dataset.weaponKey || '' : '';
    const shieldKey = shieldImg ? shieldImg.dataset.shieldKey || '' : '';
    if (hudAnimPrev && hudAnimPrev.stateRef === S) {
      if (h.hp < hudAnimPrev.hp) triggerHudPulse(hpSlot, 'hud-react-hurt', 380);
      else if (h.hp > hudAnimPrev.hp) triggerHudPulse(hpSlot, 'hud-react-heal', 400);
      if (h.armor < hudAnimPrev.armor) triggerHudPulse(armorSlot, 'hud-react-hit', 360);
      else if (h.armor > hudAnimPrev.armor) triggerHudPulse(armorSlot, 'hud-react-gain', 400);
      if (h.level > hudAnimPrev.level) triggerHudPulse(el('hud-xp'), 'hud-react-full', 500);
      else if (h.xp > hudAnimPrev.xp || (h.xpLevelCum || 0) > hudAnimPrev.xpCum) triggerHudPulse(el('hud-xp'), 'hud-react-gain', 400);
      const upGained = (S.upgradeEarned || 0) > hudAnimPrev.upgradeEarned || S.upgradeBar > hudAnimPrev.upgradeBar;
      if (upGained && S.upgradeBar < hudAnimPrev.upgradeBar) triggerHudPulse(el('hud-upgrade'), 'hud-react-full', 500);
      else if (upGained) triggerHudPulse(el('hud-upgrade'), 'hud-react-gain', 400);
      const goldGained = (S.goldEarned || 0) > hudAnimPrev.goldEarned || h.gold > hudAnimPrev.gold;
      if (goldGained && h.gold < hudAnimPrev.gold) triggerHudPulse(el('hud-gold'), 'hud-react-full', 500);
      else if (goldGained) triggerHudPulse(el('hud-gold'), 'hud-react-gain', 440);
      if (weaponKey && hudAnimPrev.weaponKey && weaponKey !== hudAnimPrev.weaponKey) triggerHudPulse(weaponSlot, 'hud-react-swap', 440);
      if (shieldKey && hudAnimPrev.shieldKey && shieldKey !== hudAnimPrev.shieldKey) triggerHudPulse(armorSlot, 'hud-react-swap', 440);
    }
    hudAnimPrev = {
      stateRef: S,
      hp: h.hp,
      armor: h.armor,
      level: h.level,
      xp: h.xp,
      xpCum: h.xpLevelCum || 0,
      upgradeBar: S.upgradeBar,
      upgradeEarned: S.upgradeEarned || 0,
      gold: h.gold,
      goldEarned: S.goldEarned || 0,
      weaponKey,
      shieldKey,
    };
    const damageLabel = el('dmg-text');
    if (damageLabel) {
      damageLabel.textContent = `Меч ${fmt(weapon)} · база ${fmt(baseDmg)}`;
      damageLabel.title = dmgTitle;
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
    const trophyImg = el('hud-trophy-img');
    if (trophyImg && trophyImg.getAttribute('src') !== TROPHY_IMAGE) trophyImg.setAttribute('src', TROPHY_IMAGE);
    if (el('trophy-text')) {
      const onBoard = S.board.filter(cell => cell.type === 'trophy').length;
      const fullText = onBoard ? 'на поле: ' + onBoard : S.difficulty === 0 ? 'не на лёгком'
        : S.trophiesGenerated >= MAX_TROPHIES_PER_RUN ? 'лимит 3/3'
        : !trophyEligible() ? 'рост закрыт'
        : 'через ' + (S.mode === 'sprint' && S.trophyRemaining > 0 ? '≥' : '') + Math.max(1, S.trophyRemaining);
      const compactText = onBoard ? '★' : S.difficulty === 0 ? '—'
        : S.trophiesGenerated >= MAX_TROPHIES_PER_RUN ? '3/3'
        : !trophyEligible() ? '✕'
        : (S.mode === 'sprint' && S.trophyRemaining > 0 ? '≥' : '') + Math.max(1, S.trophyRemaining);
      el('trophy-text').textContent = compactText;
      const trophyTitle = `Трофей ${fullText}. Тап по трофею на поле — рост меха без хода врагов. Не больше 3 появлений за забег; интервал 20–30 переносится между забегами. На Лёгком трофеи не появляются.`;
      el('trophy-text').title = trophyTitle;
      const trophyBadge = el('hud-trophy');
      if (trophyBadge) {
        trophyBadge.title = trophyTitle;
        trophyBadge.setAttribute('aria-label', `Трофей ${fullText}`);
        trophyBadge.classList.toggle('is-on-board', onBoard > 0);
      }
    }
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
        empty.innerHTML = `<span class="skill-icon skill-slot-empty" aria-hidden="true">${raidEmptyModuleIcon()}</span><span class="skill-name">пусто</span>`;
        empty.title = 'Слот навыка откроется на уровне 2';
        row.appendChild(empty);
        continue;
      }
      const left = S.skillCooldowns[skillId] || 0;
      const level = (S.skillLevels && S.skillLevels[skillId]) || 1;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'skill' + (left > 0 ? ' on-cd' : '');
      btn.dataset.slot = String(i); btn.dataset.skillId = skillId;
      if (skillId === 'retune') btn.disabled = !canActivateSkill(skillId) || left > 0;
      btn.innerHTML = `<span class="skill-icon">${raidModuleIcon(skill.id)}</span><span class="skill-name">${skillId === 'retune' ? 'Перена<wbr>стройка' : skill.name}</span>${left > 0 ? `<span class="cd">${left}</span>` : (level > 1 ? `<span class="cd">ур.${level}</span>` : '')}`;
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
    if (UPDATES_OPEN) return false;
    const affinity = NEW_RUN_REQUEST?.classId === getSelectedClassId()
      ? NEW_RUN_REQUEST.affinity : preparedPilotAffinity(getSelectedClassId());
    pickerAffinities.clear();
    for (const input of qsa('select.picker-affinity')) input.value = '';
    closeGameSettings(false);
    closeRetune(false);
    const runSeed = Number.isFinite(Number(seed)) ? (Number(seed) >>> 0) : (Math.floor(Date.now() / 1000) >>> 0);
    if (hasPendingProgress()) retryRunHistory();
    RUN_ID++;
    resetOrbPresentation();
    clearFloatEffects();
    stopMaterialAudio();
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
    ELEMENTAL_COMBAT = { rules: AFFINITY_RULES, rng: (runSeed ^ 0x454c454d) >>> 0, affinity };
    RUN_REGION = RAID_REGION;
    applyRaidRegion(RAID_REGION);
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
    syncIntroMusic();
    if (saveRun()) saveRaidAppearance();
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
    if (option.id === 'retune' && !hasRetuneRules()) return false;
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
    if (id === 'retune') return true; // next-run editor; active saves gate it separately
    return skill.native < 31 || CLASSES.some(cls => cls.id !== 'adventurer'
      && cls.uniqueSkill === id && classLevelOf(cls.id) === 10);
  }
  function canActivateSkill(id) {
    if (id === 'retune') return hasRetuneRules() && S.board.some(cell => retuneWeapon(cell));
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
    if (!S || UPDATES_OPEN || HISTORY_OPEN || RETUNE_DRAFT || S.over || S.resolving || S.pendingModal || S.selected.length || !ownsRunSlot()) return;
    if ((S.skillCooldowns[id] || 0) > 0 || !SKILL_BY_ID[id]) return;
    if (id === 'retune') { openRetune(); return; }
    commitSkill(id, () => applySkill(id));
  }
  function commitSkill(id, action) {
    S.skillDurations[id] = skillDuration(id);
    S.skillCooldowns[id] = Math.max(0, S.skillDurations[id]);
    S.resolving = true;
    fireBreathOnSkill();
    withResourceBatch(() => {
      action();
      // A1 changes colour only. It cannot change chain legality or vacancies,
      // and must not age/thaw/shuffle any other cell as transformation cleanup.
      // The 42 inherited casts retain their exact original housekeeping; A1
      // still uses their charge, true-cast reaction, resource and save pipeline.
      if (id !== 'retune') {
        if (S.board.some(cell => !cell || cell.type === 'empty')) compactBoardAndRefill();
        if (!S.actionCommitted) markExistingSkullsOld();
        if (!hasLegalChain(S.board)) ensureChainable();
      }
    });
    renderHUD(); renderBoard();
    continueAfterRewards({ phase: 'skill' });
  }

  // A1 input is an unpaid, cancellable draft, not S.pendingModal/a reward.
  function retuneWeapon(cell, element = null) {
    return !!cell && ['sword', 'arrow'].includes(cell.type) && !cell.frozen && !cell.stunned && !cell.broken && !cell.matched
      && RAID_ELEMENTS.includes(cell.element) && (!element || cell.element !== element);
  }
  function retuneContext(draft) {
    if (TELEGRAM_PROFILE && telegramProfileId() !== PROFILE_ID) {
      try { profileStorageCheck(); } catch (ignored) { /* shows the identity warning */ }
      return false;
    }
    return !!draft && draft === RETUNE_DRAFT && draft.runId === RUN_ID && draft.state === S
      && draft.prefix === PROFILE_PREFIX && PROFILE_PREFIX !== null
      && (!TELEGRAM_PROFILE || telegramProfileId() === PROFILE_ID)
      && hasRetuneRules() && S.skills.includes('retune') && !(S.skillCooldowns.retune > 0)
      && !S.over && !S.resolving && !S.pendingModal && !S.selected.length && !UPDATES_OPEN && !HISTORY_OPEN
      && !pickerOpen && !['menu-modal', 'help-modal', 'exit-modal', 'restart-modal'].some(telegramLayer)
      && S.board.every((cell, i) => cell === draft.board[i]) && ownsRunSlot();
  }
  function renderRetuneTargets() {
    const draft = RETUNE_DRAFT;
    for (const node of qsa('#board > .cell')) {
      const idx = Number(node.dataset.idx), eligible = !!draft?.element && retuneWeapon(S.board[idx], draft.element);
      node.classList.toggle('retune-eligible', eligible);
      node.classList.toggle('retune-chosen', !!draft?.selected.has(idx));
      if (draft) {
        node.setAttribute('role', 'button'); node.tabIndex = eligible ? 0 : -1;
        node.setAttribute('aria-disabled', String(!eligible)); node.setAttribute('aria-pressed', String(draft.selected.has(idx)));
      } else for (const attr of ['role', 'tabindex', 'aria-disabled', 'aria-pressed']) node.removeAttribute(attr);
    }
    if (el('board')) el('board').classList.toggle('retuning', !!draft);
    if (!draft) return;
    for (const node of qsa('[data-retune-element]')) node.setAttribute('aria-pressed', String(node.dataset.retuneElement === draft.element));
    el('retune-status').textContent = draft.element
      ? RAID_REGIONS[draft.element].element + ' · выбрано ' + draft.selected.size + ' из 3. Отметь оружие на поле.'
      : 'Выбери стихию, затем 1–3 целых незамороженных меча/стрелы.';
    el('retune-apply').disabled = !draft.selected.size;
  }
  function closeRetune(focus = true) {
    if (!RETUNE_DRAFT) return;
    RETUNE_DRAFT = null;
    if (el('retune-panel')) el('retune-panel').hidden = true;
    renderRetuneTargets(); syncTelegramChrome();
    if (focus) qs('[data-skill-id="retune"]')?.focus();
  }
  function openRetune() {
    if (!hasRetuneRules() || !S.skills.includes('retune') || !canActivateSkill('retune')
      || activeDrag || pickerOpen || ['menu-modal', 'help-modal', 'exit-modal', 'restart-modal'].some(telegramLayer)
      || [...ORB_VIEWS.values()].some(view => view.motion) || !el('retune-panel')
      || PROFILE_PREFIX === null || TELEGRAM_PROFILE && telegramProfileId() !== PROFILE_ID) return false;
    RETUNE_DRAFT = { runId: RUN_ID, state: S, prefix: PROFILE_PREFIX, board: [...S.board], element: null, selected: new Set() };
    el('retune-panel').hidden = false; renderRetuneTargets(); syncTelegramChrome();
    qs('[data-retune-element]')?.focus();
    return true;
  }
  function toggleRetuneTarget(idx) {
    const draft = RETUNE_DRAFT;
    if (!retuneContext(draft)) { closeRetune(false); return; }
    if (!draft.element || !Number.isInteger(idx) || !retuneWeapon(S.board[idx], draft.element)) return;
    if (draft.selected.has(idx)) draft.selected.delete(idx);
    else if (draft.selected.size < 3) draft.selected.add(idx);
    renderRetuneTargets();
  }
  function confirmRetune() {
    const draft = RETUNE_DRAFT;
    if (!retuneContext(draft)) { closeRetune(false); return false; }
    const slots = [...draft.selected];
    if (!RAID_ELEMENTS.includes(draft.element) || slots.length < 1 || slots.length > 3
      || !slots.every(idx => retuneWeapon(S.board[idx], draft.element))) return false;
    closeRetune(false); // invalidates queued/double clicks BEFORE any effect
    commitSkill('retune', () => { for (const idx of slots) S.board[idx].element = draft.element; });
    qs('[data-skill-id="retune"]')?.focus();
    return true;
  }
  function initRetune() {
    for (const button of qsa('[data-retune-element]')) button.addEventListener('click', () => {
      const draft = RETUNE_DRAFT;
      if (!retuneContext(draft)) { closeRetune(false); return; }
      if (draft.element !== button.dataset.retuneElement) draft.selected.clear();
      draft.element = button.dataset.retuneElement; renderRetuneTargets();
    });
    el('retune-cancel')?.addEventListener('click', () => closeRetune());
    el('retune-apply')?.addEventListener('click', confirmRetune);
    el('board')?.addEventListener('click', event => {
      if (!RETUNE_DRAFT) return;
      const cell = event.target.closest('.cell');
      if (cell) toggleRetuneTarget(Number(cell.dataset.idx));
    });
    document.addEventListener('keydown', event => {
      if (!RETUNE_DRAFT || GAME_SETTINGS) return;
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeRetune(); return; }
      const cell = event.target.closest?.('#board > .cell');
      if (cell && ['Enter', ' '].includes(event.key)) { event.preventDefault(); toggleRetuneTarget(Number(cell.dataset.idx)); }
      if (event.key === 'Tab') {
        const controls = [el('settings-btn'), ...qsa('#retune-panel button:not(:disabled)'), ...qsa('#board .retune-eligible')];
        const index = controls.indexOf(document.activeElement);
        // Explicit order skips unrelated header/skill buttons between the
        // settings opener and A1 panel in DOM order, in BOTH directions.
        const next = index < 0 ? (event.shiftKey ? controls.length - 1 : 0)
          : (index + (event.shiftKey ? controls.length - 1 : 1)) % controls.length;
        event.preventDefault(); controls[next]?.focus();
      }
    });
    for (const kind of ['pointerdown', 'click']) document.addEventListener(kind, event => {
      if (!RETUNE_DRAFT) return;
      if (!retuneContext(RETUNE_DRAFT)) { closeRetune(false); }
      else if (el('retune-panel').contains(event.target) || el('board').contains(event.target)
        || el('settings-btn').contains(event.target) || GAME_SETTINGS && el('game-settings').contains(event.target)) return;
      // Leave native vertical scrolling available outside the board. Only
      // activations are blocked; a non-control pointerdown need not cancel pan.
      if (kind === 'click' || event.target.closest?.('button,select,a,input')) event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
    document.addEventListener('focusin', event => {
      if (RETUNE_DRAFT && !GAME_SETTINGS && !el('retune-panel').contains(event.target) && !el('board').contains(event.target)
        && !el('settings-btn').contains(event.target)) qs('[data-retune-element]')?.focus();
    });
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

  function targetArtName(s) { return s && s.boss ? 'босс' : 'враг'; }

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
      if (skull.hp > 0) { queueCombatHaptic('hit'); return false; }
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
      flash('🦊 Воровство: атаки врагов в этом ходу приносят золото');
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
      flash('❄ Заморожено врагов: ' + frozen);
    } else if (id === 'heal') {
      const idxs = [];
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (c && c.type === 'potion' && !c.acid && !c.mana) idxs.push(i);
      }
      collectIndices(idxs, { noSkulls: true, skipAcid: true, skipMana: true });
      if (idxs.length) audioPotionChain();
      flash('❤ Собрано обычных зелий: ' + idxs.length);
    } else if (id === 'repair') {
      const idxs = [];
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (c && c.type === 'shield') idxs.push(i);
      }
      collectIndices(idxs, { noSkulls: true });
      if (idxs.length) audioShieldChain();
      flash('⚒ Собрано щитов: ' + idxs.length + ' — ремонт или UP');
    } else if (id === 'counter') {
      S.counterattack = 1;
      flash('↩ Контратака: враги бьют себя вместо героя');
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
      flash('💥 Раскол: ослаблено обычных врагов — ' + n);
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
      flash('✦ Телепорт: клетки без врагов обновлены');
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
      if (idxs.length) audioPotionChain();
      flash('⚗ Эликсир: ' + idxs.length + ' обычных зелий → токены XP');
    } else if (id === 'enchant') {
      requestRewardMenu('armor');
      flash('🛠 Апгрейд: выбери улучшение');
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
      if (idxs.length) audioCoinChain();
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
          S.board[i] = withWeaponElement({ type: 'sword' });
          n++;
        }
      }
      flash('⚔ Обезоруживание: врагов → мечи ' + n);
    } else if (id === 'dazzle') {
      let n = 0;
      // Original ConvertTiles: skipping special tiles only; coins are not special.
      for (let i = 0; i < CELL_COUNT; i++) {
        const c = S.board[i];
        if (c && c.type === 'coin') { S.board[i] = withWeaponElement({ type: 'sword' }); n++; }
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
      if (idxs.length === 0) { flash('✝ Нет особых врагов'); return; }
      const i = idxs[randInt(0, idxs.length - 1, 5)];
      const s = S.board[i].skull;
      s.special = false;
      s.type = 0;
      s.boss = false;
      // Same enemy keeps its primary assignment; only a boss has two elements.
      if (ELEMENTAL_COMBAT) delete s.secondaryElement;
      s.maxHp = Math.max(1, s.maxHp);
      flash('✝ Изгнание: особый враг стал обычным');
    } else if (id === 'banish') {
      const idxs = liveSkullIndices(true).filter(i => S.board[i].skull.special || S.board[i].skull.boss);
      if (idxs.length === 0) { flash('☄ Нет особых врагов'); return; }
      const i = idxs[randInt(0, idxs.length - 1, 5)];
      S.board[i] = { type: 'empty' };
      flash('☄ Уничтожение: особый враг убран без награды');
      compactBoardAndRefill();
    } else if (id === 'trap') {
      activateTurnFlag('oneHealthSkulls');
      flash('🕸 Ловушка: новые враги появятся с 1 HP');
    } else if (id === 'biggame') {
      const idxs = liveSkullIndices(false).filter(i => !S.board[i].skull.special && !S.board[i].skull.boss);
      if (idxs.length === 0) { flash('👹 Нет обычных врагов'); return; }
      const i = idxs[randInt(0, idxs.length - 1, 5)];
      const cell = S.board[i];
      const old = cell.skull;
      // Promotion changes stats/type, not an already assigned primary element.
      const fresh = makeSkull(false, true, undefined, old);
      fresh.promotedByBigGame = true;
      fresh.hp = Math.min(fresh.hp, old ? old.hp : fresh.hp);
      fresh.maxHp = fresh.hp;
      fresh.enteredTurn = old && old.enteredTurn != null ? old.enteredTurn : S.turn;
      S.board[i] = { type: 'skull', skull: fresh };
      checkPromotionAchievement();
      flash('👹 Крупная дичь: обычный враг стал особым');
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
      flash('🛡 Взрывная броня: ' + amount + ' урона по всем врагам');
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
        idxs.forEach(i => { S.board[i] = withWeaponElement({ type: 'sword' }, S.board[i]); });
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
      const collectedTypes = new Set(idxs.map(i => S.board[i]?.type));
      collectIndices(idxs, { noSkulls: true, skipAcid: true });
      if (collectedTypes.has('shield')) audioShieldChain();
      if (collectedTypes.has('coin')) audioCoinChain();
      if (collectedTypes.has('potion')) audioPotionChain();
      flash((id === 'shock' ? '⚡ Шок' : '🌀 Благоговение') + ': собрано клеток ' + idxs.length);
    } else if (id === 'berserk') {
      activateTurnFlag('berserkActive');
      flash('🪓 БЕРСЕРК: HP за убийства этого хода');
    } else if (id === 'rune') {
      activateTurnFlag('runeActive');
      S.runeUsedThisTurn = 0;
      flash('🔮 РУНА: замкни 4+ узла — соберутся ресурсы и обычные враги внутри');
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
      flash('🗡 Жадность: убрано ' + killed + ' обычных врагов без награды');
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
      flash('🏴 Мазохизм: ' + skullCount + ' монет → враги');
    } else if (id === 'volley') {
      let converted = 0;
      for (let i = 0; i < CELL_COUNT; i++) {
        const cell = S.board[i];
        if (cell && cell.type === 'sword') { S.board[i] = withWeaponElement({ type: 'arrow' }, cell); converted++; }
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
          damageHeroWithHaptics(event.amount, false, true, event.cause);
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
      if (next.committed) {
        const enemyDelay = LAST_BOARD_ANIM_END > 0 ? Math.max(550, Math.round(LAST_BOARD_ANIM_END + 90)) : 550;
        LAST_BOARD_ANIM_END = 0;
        scheduleRunStep({ phase: 'enemy' }, enemyDelay);
        return;
      }
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
    if (step.phase === 'match-chain') primeOrbChain(step.snapshot?.effective || effectiveChain(step.chain));
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
    if (cell.type === 'skull') queueCombatHaptic('kill', !!cell.skull?.boss);
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
  let FLOAT_FRAME = 0, FLOAT_TIMER = null;
  function clearFloatEffects() {
    if (FLOAT_FRAME) window.cancelAnimationFrame?.(FLOAT_FRAME);
    clearTimeout(FLOAT_TIMER); FLOAT_TIMER = null;
    FLOAT_FRAME = 0;
    for (const effect of FLOAT_EFFECTS) effect.node.remove();
    FLOAT_EFFECTS.clear();
  }
  function renderFloatEffects() {
    FLOAT_FRAME = 0;
    clearTimeout(FLOAT_TIMER); FLOAT_TIMER = null;
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
    if (FLOAT_EFFECTS.size) {
      // Motion off still shows readable static numbers at the actual recipient,
      // but does not run a positioning RAF loop. One expiry timer is UI-only.
      if (orbReducedMotion()) FLOAT_TIMER = setTimeout(renderFloatEffects, Math.max(1, Math.min(...[...FLOAT_EFFECTS].map(effect => effect.until)) - now));
      else FLOAT_FRAME = window.requestAnimationFrame(renderFloatEffects);
    }
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

  // --- haptics --------------------------------------------------------------
  // Haptics are best-effort and entirely local. Emit at the combat event.
  const HAPTICS_KEY = 'raid.magitech.haptics.v1';
  let hapticsMode = 'important';
  function hapticsEnabled() { return hapticsMode !== 'off' && document.visibilityState !== 'hidden'; }
  function emitHaptic(kind) {
    if (!hapticsEnabled()) return;
    let delivered = false;
    try {
      const feedback = window.Telegram?.WebApp?.HapticFeedback;
      if (kind !== 'confirmation' && typeof feedback?.selectionChanged === 'function') {
        feedback.selectionChanged(); delivered = true;
      } else if (typeof feedback?.impactOccurred === 'function') {
        feedback.impactOccurred(kind === 'confirmation' ? 'soft' : 'heavy'); delivered = true;
      }
    } catch (ignored) { /* Try the browser API if the host bridge rejects this call. */ }
    if (delivered) return;
    // Browser fallback is optional; iOS and some embedded WebViews do not
    // expose it. Never make gameplay depend on vibration support.
    try {
      if (typeof navigator.vibrate === 'function') navigator.vibrate(kind === 'boss' ? [35, 25, 45] : kind === 'damage' ? [45, 30, 45] : kind === 'kill' ? 35 : 12);
    } catch (ignored) {}
  }
  function queueCombatHaptic(kind, boss = false) {
    if (!hapticsEnabled()) return;
    if (kind === 'selection') emitHaptic('selection');
    else if (kind === 'kill') emitHaptic(boss ? 'boss' : 'kill');
    else if (kind === 'damage') emitHaptic('damage');
    else if (kind === 'hit' && hapticsMode === 'all') emitHaptic('hit');
  }
  function initHaptics() {
    const controls = [...document.querySelectorAll('[data-raid-haptics]')]; if (!controls.length) return;
    try {
      const saved = localStorage.getItem(HAPTICS_KEY);
      if (['important', 'all', 'off'].includes(saved)) hapticsMode = saved;
    } catch (ignored) {}
    const syncControls = () => controls.forEach(control => { control.checked = control.value === hapticsMode; });
    syncControls();
    controls.forEach(control => control.addEventListener('change', event => {
      const value = event.target.value;
      hapticsMode = ['important', 'all', 'off'].includes(value) ? value : 'important';
      try { localStorage.setItem(HAPTICS_KEY, hapticsMode); } catch (ignored) {}
      syncControls();
      // Confirm a manual selection synchronously within the user's gesture.
      // This is also a practical capability check for browser Vibration API.
      if (hapticsMode !== 'off') emitHaptic('confirmation');
    }));
  }

  // --- audio ----------------------------------------------------------------
  // Quiet, locally synthesized material SFX. They are separate from the
  // persistent intro track and use no samples, network or gameplay RNG. A
  // trusted gesture unlocks effects; suspended events are dropped, never queued.
  let audioCtx = null, audioMaster = null, audioNoise = null, audioGesture = false, audioPageActive = true, audioHostActive = true;
  let menuMusicEnabled = true, battleMusicEnabled = true, introMusicGesture = false;
  const INTRO_MUSIC_KEY = 'raid.magitech.introMusic.v1'; // legacy preference for migration
  const MENU_MUSIC_KEY = 'raid.magitech.menuMusic.v1';
  const BATTLE_MUSIC_KEY = 'raid.magitech.battleMusic.v1';
  const AUDIO_VOICES = new Set(), AUDIO_LAST = new Map();
  const MATERIAL_SOUNDS = {
    gold: [[1180, 1050, .25, .052], [2360, 2310, .19, .016, .012], [1720, 1680, .32, .018, .035]],
    water: [[560, 270, .17, .038], [760, 430, .13, .023, .045], [1400, 800, .17, .018, 0, 'noise']],
    air: [[1250, 1850, .24, .027, 0, 'noise'], [690, 660, .19, .012, .025]],
    metal: [[820, 740, .19, .036], [1970, 1870, .14, .012, .008], [2300, 1700, .08, .016, 0, 'noise']],
    armour: [[470, 390, .20, .038], [1130, 1080, .18, .016, .012]],
    impact: [[190, 85, .20, .055], [950, 380, .12, .026, 0, 'noise']],
    enemy: [[130, 75, .18, .043], [420, 230, .10, .018, 0, 'noise']],
    boss: [[105, 68, .43, .048], [310, 180, .28, .018, .025, 'noise']],
    level: [[740, 735, .30, .038], [1110, 1100, .36, .025, .065], [1480, 1470, .38, .012, .10]],
    over: [[260, 165, .38, .032], [170, 110, .48, .025, .08]],
    ui: [[920, 850, .10, .022]],
  };
  function audioAllowed() {
    return audioGesture && audioPageActive && audioHostActive && !!el('sound-on')?.checked && document.visibilityState !== 'hidden';
  }
  function audio() {
    if (!audioAllowed()) return null;
    const Constructor = window.AudioContext || window.webkitAudioContext;
    if (!audioCtx && Constructor) {
      try {
        audioCtx = new Constructor({ latencyHint: 'interactive' });
        audioMaster = audioCtx.createGain(); audioMaster.gain.value = .45;
        audioMaster.connect(audioCtx.destination);
      } catch (ignored) { audioCtx = audioMaster = null; }
    }
    return audioCtx;
  }
  function stopMaterialAudio() {
    // Silence even delayed sources immediately, not just the next pick.
    try { audioMaster?.gain.cancelScheduledValues(audioCtx.currentTime); if (audioMaster) audioMaster.gain.value = 0; } catch (ignored) {}
    for (const voice of [...AUDIO_VOICES]) {
      voice.source.onended = null;
      try { voice.source.stop(); } catch (ignored) {}
      for (const node of voice.nodes) { try { node.disconnect(); } catch (ignored) {} }
      AUDIO_VOICES.delete(voice);
    }
    AUDIO_LAST.clear();
    for (const id of ['raid-sword-taken', 'raid-sword-blade', 'raid-coin-taken', 'raid-coin-drop', 'raid-bottle-clink', 'raid-bottle-open', 'raid-shield-first', 'raid-shield-collect']) {
      const clip = el(id); if (clip) { clip.ontimeupdate = null; clip.pause(); try { clip.currentTime = 0; } catch (ignored) {} }
    }
  }
  function playAudioClip(id) {
    if (!audioAllowed()) return;
    const clip = el(id); if (!clip) return;
    clip.volume = .65;
    try {
      clip.pause();
      clip.currentTime = 0;
      const playing = clip.play(); playing?.catch?.(() => {});
    } catch (ignored) {}
  }
  function introMusicAppVisible() {
    return audioPageActive && audioHostActive && document.visibilityState !== 'hidden';
  }
  function isMenuMusicContext() { return !!el('class-picker')?.classList.contains('show'); }
  function activeMusicEnabled() { return isMenuMusicContext() ? menuMusicEnabled : battleMusicEnabled; }
  function updateIntroMusicToggle() {
    const toggle = el('raid-music-toggle');
    const enabled = activeMusicEnabled(), context = isMenuMusicContext() ? 'меню' : 'бою';
    if (toggle) {
      toggle.classList.toggle('is-muted', !enabled);
      toggle.setAttribute('aria-pressed', String(enabled));
      toggle.setAttribute('aria-label', `${enabled ? 'Выключить' : 'Включить'} музыку в ${context}`);
      toggle.title = `${enabled ? 'Выключить' : 'Включить'} музыку в ${context}`;
    }
    const setting = el('music-on'); if (setting) setting.checked = battleMusicEnabled;
  }
  function syncIntroMusic(allowAutoplayAttempt = false) {
    const track = el('raid-intro-music'); if (!track) return;
    updateIntroMusicToggle();
    if (!activeMusicEnabled() || (!introMusicGesture && !allowAutoplayAttempt) || !introMusicAppVisible()) {
      track.pause(); return;
    }
    track.volume = .28;
    try { const playing = track.play(); playing?.catch?.(() => {}); } catch (ignored) {}
  }
  function stopIntroMusic(reset = false) {
    const track = el('raid-intro-music'); if (!track) return;
    track.pause(); if (reset) { try { track.currentTime = 0; } catch (ignored) {} }
  }
  function setMusicPreference(enabled, menuContext = isMenuMusicContext()) {
    if (menuContext) {
      menuMusicEnabled = !!enabled;
      try { localStorage.setItem(MENU_MUSIC_KEY, menuMusicEnabled ? 'on' : 'off'); } catch (ignored) {}
    } else {
      battleMusicEnabled = !!enabled;
      try { localStorage.setItem(BATTLE_MUSIC_KEY, battleMusicEnabled ? 'on' : 'off'); } catch (ignored) {}
    }
    updateIntroMusicToggle();
    if (activeMusicEnabled()) { introMusicGesture = true; syncIntroMusic(); }
    else stopIntroMusic();
  }
  function initIntroMusic() {
    const track = el('raid-intro-music'), toggle = el('raid-music-toggle');
    if (!track || !toggle) return;
    track.volume = .28;
    try {
      const legacy = localStorage.getItem(INTRO_MUSIC_KEY);
      const menuSaved = localStorage.getItem(MENU_MUSIC_KEY), battleSaved = localStorage.getItem(BATTLE_MUSIC_KEY);
      menuMusicEnabled = menuSaved === null ? legacy !== 'off' : menuSaved !== 'off';
      battleMusicEnabled = battleSaved === null ? legacy !== 'off' : battleSaved !== 'off';
    } catch (ignored) {}
    updateIntroMusicToggle();
    toggle.addEventListener('click', () => setMusicPreference(!activeMusicEnabled()));
    el('music-on')?.addEventListener('change', event => setMusicPreference(event.target.checked, false));
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') stopIntroMusic(); else syncIntroMusic();
    });
    window.addEventListener?.('pagehide', () => stopIntroMusic());
    window.addEventListener?.('pageshow', () => syncIntroMusic());
    syncIntroMusic(true);
  }
  function unlockMaterialAudio(event) {
    if (!event.isTrusted) return;
    audioGesture = true; introMusicGesture = true;
    if (event.target?.closest?.('#raid-music-toggle')) return;
    syncIntroMusic();
    const ctx = audio();
    if (ctx?.state === 'suspended') {
      try { ctx.resume()?.catch?.(() => {}); } catch (ignored) {}
    }
  }
  function materialNoise(ctx) {
    if (!audioNoise) {
      audioNoise = ctx.createBuffer(1, Math.ceil(ctx.sampleRate * .6), ctx.sampleRate);
      const data = audioNoise.getChannelData(0); let seed = 0x51f15e;
      for (let i = 0; i < data.length; i++) {
        seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5;
        data[i] = (seed >>> 0) / 2147483648 - 1;
      }
    }
    return audioNoise;
  }
  function materialVoice(ctx, spec, now) {
    const [from, to, duration, volume, delay = 0, type = 'sine'] = spec;
    const nodes = []; let voice;
    try {
      const source = type === 'noise' ? ctx.createBufferSource() : ctx.createOscillator(); nodes.push(source);
      const filter = ctx.createBiquadFilter(); nodes.push(filter);
      const gain = ctx.createGain(); nodes.push(gain);
      const start = now + delay;
      filter.type = type === 'noise' ? 'bandpass' : 'lowpass';
      filter.Q.value = type === 'noise' ? .65 : .5;
      if (type === 'noise') source.buffer = materialNoise(ctx);
      else {
        source.type = 'sine'; source.frequency.setValueAtTime(from, start);
        source.frequency.exponentialRampToValueAtTime(to, start + duration);
      }
      filter.frequency.setValueAtTime(type === 'noise' ? from : 4200, start);
      if (type === 'noise') filter.frequency.exponentialRampToValueAtTime(to, start + duration);
      gain.gain.setValueAtTime(.0001, now);
      gain.gain.setValueAtTime(.0001, start);
      gain.gain.exponentialRampToValueAtTime(volume, start + .008);
      gain.gain.exponentialRampToValueAtTime(.0001, start + duration);
      source.connect(filter); filter.connect(gain); gain.connect(audioMaster);
      voice = { source, nodes }; AUDIO_VOICES.add(voice);
      source.onended = () => {
        for (const node of nodes) { try { node.disconnect(); } catch (ignored) {} }
        AUDIO_VOICES.delete(voice);
      };
      source.start(start); source.stop(start + duration + .015);
    } catch (ignored) {
      if (voice) { try { voice.source.stop(); } catch (ignored) {} AUDIO_VOICES.delete(voice); }
      for (const node of nodes) { try { node.disconnect(); } catch (ignored) {} }
    }
  }
  function materialSound(kind) {
    if (!audioAllowed()) return;
    const ctx = audio(), voices = MATERIAL_SOUNDS[kind];
    if (!ctx || ctx.state !== 'running' || !voices) return;
    const now = ctx.currentTime;
    // Dense drags/combat cannot turn a quiet set into a loud wall of notes.
    if (now - (AUDIO_LAST.get(kind) ?? -1) < .085 || AUDIO_VOICES.size + voices.length > 18) return;
    AUDIO_LAST.set(kind, now); audioMaster.gain.setValueAtTime(.45, now);
    for (const spec of voices) materialVoice(ctx, spec, now);
  }
  function initMaterialAudio() {
    initIntroMusic();
    document.addEventListener('pointerdown', unlockMaterialAudio, { capture: true, passive: true });
    document.addEventListener('keydown', unlockMaterialAudio, { capture: true });
    el('sound-on')?.addEventListener('change', event => {
      if (!event.target.checked) stopMaterialAudio();
      // Re-enabling stays silent. The next actual pointer/key gesture may
      // resume audio; a programmatic checkbox click is not an activation.
    });
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') stopMaterialAudio(); });
    window.addEventListener?.('pagehide', () => { audioPageActive = false; stopMaterialAudio(); });
    window.addEventListener?.('pageshow', () => { audioPageActive = true; });
  }
  function audioPick(kind) {
    if (kind === 'coin') return audioGold();
    if (kind === 'potion') return audioHeal();
    if (kind === 'shield') return audioArmor();
    if (kind === 'sword') {
      const cell = S.board[S.selected.at(-1)];
      // Read an already rendered palette; sound must not assign/save colours.
      const element = cell && ['sword', 'arrow'].includes(cell.type) ? ORB_ELEMENTS.get(cell) : -1;
      return materialSound(element === 1 ? 'air' : element === 3 ? 'water' : 'metal');
    }
    materialSound('ui');
  }
  function audioSwordSelect() { playAudioClip('raid-sword-taken'); }
  function audioSwordChain() { playAudioClip('raid-sword-blade'); }
  function audioCoinSelect() { playAudioClip('raid-coin-taken'); }
  function audioCoinChain() { playAudioClip('raid-coin-drop'); }
  function audioPotionSelect() { playAudioClip('raid-bottle-open'); }
  function audioPotionChain() { playAudioClip('raid-bottle-clink'); }
  function audioShieldSelect() { playAudioClip('raid-shield-first'); }
  function audioShieldChain() { playAudioClip('raid-shield-collect'); }
  function audioKill() { materialSound('impact'); }
  function audioHeal() { materialSound('water'); }
  function audioArmor() { materialSound('armour'); }
  function audioGold() { materialSound('gold'); }
  function audioLevel() { materialSound('level'); }
  function audioGameOver() { materialSound('over'); }
  function audioEnemy() { materialSound('enemy'); }
  function audioBoss() { materialSound('boss'); }

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

  function primaryChainDamage(idx, chain, effective, neutralDamage) {
    let damage = neutralDamage;
    if (ELEMENTAL_COMBAT) {
      const skull = S.board[idx].skull;
      const multiplier = (weapon, target) => {
        const from = RAID_ELEMENTS.indexOf(weapon), to = RAID_ELEMENTS.indexOf(target);
        if (from < 0 || to < 0) return 1; // Invalid debug fixtures never roll from rendering/combat.
        return (from + 1) % 4 === to ? 1.25 : (to + 1) % 4 === from ? .75 : 1;
      };
      let delta = 0;
      for (const slot of effective) {
        const cell = S.board[slot], weight = cell.broken ? 0 : swordWeight(cell);
        if (!weight) continue;
        const primary = multiplier(cell.element, skull.element);
        const k = skull.boss ? (primary + multiplier(cell.element, skull.secondaryElement)) / 2 : primary;
        delta += weight * (k * pilotWeaponMultiplier(cell.element) - 1);
      }
      // B is already in D0 and stays neutral. q/Boost apply ONLY to weapons.
      damage += S.hero.weaponDamage * (S.swordBoost > 0 ? 2 : 1) * delta;
    }
    // Preserve Confused -> Armoured exit -> Uppercut +unboosted W -> Dwarf/
    // Chaotic -> Meat Shield gate priority, then damageSkull's round/armour.
    return chainDamageFor(idx, chain, damage);
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
      const targetDamage = snapshot?.targetDamage || new Map(skullIdxs.map(idx => [idx, primaryChainDamage(idx, chain, effective, damage)]));
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
      targetDamage: new Map(skulls.map(idx => [idx, primaryChainDamage(idx, chain, effective, damage)])),
      uppercuts: skulls.filter(idx => isChainUppercut(idx, chain)),
      burning: effective.reduce((sum, idx) => {
        const cell = S.board[idx], target = cell.type === 'skull' ? cell.skull : cell;
        return sum + (target.burn && !target.fireSafe ? target.fireDamage ?? (typeof target.burn === 'number' ? target.burn : 0) : 0);
      }, 0) };
  }

  function resolveChain() {
    if (!S || UPDATES_OPEN || HISTORY_OPEN || RETUNE_DRAFT || S.resolving || S.over || S.pendingModal || !ownsRunSlot()) return;
    const chain = S.selected.slice();
    if (chain.length === 1 && S.board[chain[0]].type === 'trophy') { claimTrophy(chain[0]); return; }
    const snapshot = chainSnapshot(chain), { committed, rune, skulls } = snapshot;
    if (!committed && !rune.length) { clearChain(); return; }
    if (committed && S.chainType === 'sword') audioSwordChain();
    else if (committed && S.chainType === 'coin') audioCoinChain();
    else if (committed && S.chainType === 'potion') audioPotionChain();
    else if (committed && S.chainType === 'shield') audioShieldChain();
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
    scheduleRunStep({ phase: 'match-chain', snapshot }, 420);
  }

  function settleChain(snapshot) {
    const { committed, rune, skulls, swords } = snapshot;
    // Capture identities before damage/collection/gravity, not refilled slots.
    // This short-lived array is neither a save field nor a gameplay timer.
    const burstOrder = snapshot.effective.map(idx => S.board[idx]).filter(cell => cell && cell.type !== 'empty').map(orbIdentity);
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
    renderBoard(burstOrder); renderHUD();
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
    const incoming = processEnemyAttack();
    if (incoming?.damage >= Math.max(3, Math.ceil(S.hero.maxHp * .25))) queueCombatHaptic('damage');
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
          const message = `🐺 Калечение пилота «Перехватчик»: уничтожено врагов с HP ниже их защиты — ${crippled}`;
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

  function damageHeroWithHaptics(...args) {
    const damage = damageHero(...args);
    if (damage >= Math.max(3, Math.ceil(S.hero.maxHp * .25))) queueCombatHaptic('damage');
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
    const hadSettings = !!GAME_SETTINGS;
    closeGameSettings(false);
    closeRetune(false);
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
      wrap.classList.add('gameover');
      body.innerHTML = `<p class="modal-hint">${S.won ? 'Спринт пройден: 100 ходов!' : 'Герой погиб на ходу <strong>' + S.turn + '</strong>'}</p>`;
      const actions = document.createElement('div'); actions.className = 'gameover-actions';
      const picker = document.createElement('button'); picker.type = 'button'; picker.id = 'gameover-picker'; picker.className = 'btn'; picker.textContent = 'К выбору меха';
      const current = () => runId === RUN_ID && finishedRunView() && el('gameover-picker') === picker;
      bindReadOnlyDismiss(picker, () => { if (current()) leaveFinishedRun(); });
      const exit = document.createElement('button'); exit.type = 'button'; exit.id = 'gameover-close'; exit.className = 'btn secondary'; exit.textContent = 'Закрыть Mini App'; exit.hidden = !TELEGRAM_APP;
      bindReadOnlyDismiss(exit, () => { if (current() && !exit.hidden && !exit.disabled) requestTelegramClose(); });
      actions.appendChild(picker); actions.appendChild(exit); body.appendChild(actions);
      const saveNote = document.createElement('p'); saveNote.id = 'run-save-status'; saveNote.className = 'save-note';
      saveNote.setAttribute('role', 'status'); saveNote.setAttribute('aria-live', 'polite'); saveNote.textContent = RUN_SAVE_STATUS.message;
      saveNote.hidden = !['unavailable', 'invalid', 'conflict'].includes(RUN_SAVE_STATUS.state); body.appendChild(saveNote);
      const historyNote = document.createElement('p'); historyNote.id = 'run-history-status'; historyNote.className = 'save-note';
      historyNote.setAttribute('role', 'status'); historyNote.setAttribute('aria-live', 'polite'); historyNote.textContent = HISTORY_STATUS.message;
      body.appendChild(historyNote);
      const telegramNote = document.createElement('p'); telegramNote.id = 'run-telegram-status'; telegramNote.className = 'telegram-status';
      telegramNote.setAttribute('role', 'status'); telegramNote.setAttribute('aria-live', 'polite'); telegramNote.hidden = true; body.appendChild(telegramNote);
      const stats = document.createElement('div'); stats.className = 'stats-grid';
      stats.innerHTML = `<div><span class="result-glyph">${raidModuleIcon('slash')}</span><small>убито врагов</small><strong>${S.skullsKilled}</strong></div>
          <div><span class="result-glyph">${raidModuleIcon('enchant')}</span><small>максимальный уровень</small><strong>${S.maxLevel}</strong></div>
          <div><span class="result-glyph">${raidModuleIcon('goldtouch')}</span><small>золота собрано</small><strong>${S.goldEarned}</strong></div>
          <div><span class="result-glyph">${raidItemIcon('accessory')}</span><small>трофеев забрано</small><strong>${S.trophiesThisRun || 0}</strong></div>`;
      body.appendChild(stats);
      const history = document.createElement('button'); history.type = 'button'; history.className = 'btn secondary'; history.textContent = 'Рекорды и статистика';
      bindReadOnlyDismiss(history, () => { if (current()) openRunRecords(); });
      const achievements = document.createElement('button'); achievements.type = 'button'; achievements.className = 'btn secondary'; achievements.textContent = 'Достижения';
      bindReadOnlyDismiss(achievements, () => { if (current()) openAchievements(); });
      const restart = document.createElement('button'); restart.type = 'button'; restart.id = 'gameover-restart'; restart.className = 'btn secondary'; restart.textContent = '↻ Ещё раз';
      bindReadOnlyDismiss(restart, () => { if (current()) requestNewGame(); });
      body.appendChild(history); body.appendChild(achievements); body.appendChild(restart);
      // The default action leaves the result; Enter must never auto-restart.
      picker.focus?.({ preventScroll: true });
      syncTelegramChrome();
    }
    // An asynchronous reward may arrive while the transient settings are up.
    // Hand focus to its real choice, never keep it inside a now-hidden panel.
    if (hadSettings) body.querySelector('button:not(:disabled)')?.focus({ preventScroll: true });
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

  // Finished results are navigation, NOT a mandatory reward or surrender.
  function finishedRunView() {
    return !!S?.over && S.pendingModal === 'gameover' && telegramLayer('modal') && !pickerOpen
      && !UPDATES_OPEN && !HISTORY_OPEN && !NEW_RUN_REQUEST && !TELEGRAM_EXIT
      && !['menu-modal', 'help-modal'].some(telegramLayer);
  }
  function leaveFinishedRun() {
    if (!finishedRunView() || !el('class-picker')) return false;
    // Do not use closeModal/resumeRewards: the terminal receipt, pending
    // history retry, S.over and every combat/RNG value stay exactly as earned.
    S.pendingModal = null;
    el('modal').classList.remove('show');
    showClassPicker();
    el('picker-title')?.focus();
    syncTelegramChrome();
    return true;
  }
  function initFinishedRunNavigation() {
    const wrap = el('modal'); if (!wrap) return;
    wrap.addEventListener('keydown', event => {
      if (!finishedRunView()) return;
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); leaveFinishedRun(); return; }
      if (event.key !== 'Tab') return;
      const controls = [...wrap.querySelectorAll('button:not(:disabled)')].filter(node => !node.hidden);
      const first = controls[0], last = controls[controls.length - 1];
      if (!controls.includes(document.activeElement) || event.shiftKey && document.activeElement === first || !event.shiftKey && document.activeElement === last) {
        event.preventDefault(); (event.shiftKey ? last : first)?.focus();
      }
    });
    document.addEventListener('focusin', event => {
      if (finishedRunView() && !wrap.contains(event.target)) el('gameover-picker')?.focus({ preventScroll: true });
    });
    for (const kind of ['click', 'pointerdown']) document.addEventListener(kind, event => {
      if (finishedRunView() && !wrap.contains(event.target)) { event.preventDefault(); event.stopImmediatePropagation(); }
    }, true);
  }

  // --- особые черепа: особенности по тапу на спец/босс ---
  // Спец-череп — элитность ×2/×2/×4 (оригинал); босс — ×3/×3/×6.
  // Сбор особого продвигает трофейный счётчик; выпадение — клеткой на поле.
  // Особенности спец/босс черепа: тап по нему (без драга).
  function specialTypeName(skull) {
    const t = skull && skull.type;
    if (skull && skull.boss) return 'Босс';
    if (t && ENEMY_FEATS[t]) return ENEMY_FEATS[t].name;
    return 'Особый враг';
  }

  function showSkullModal(skull) {
    closeGameSettings(false);
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
    title.textContent = skull.phylactery ? 'ФИЛАКТЕРИЙ ЛИЧА' : boss ? 'БОСС' : ('ОСОБЫЙ · ' + specialTypeName(skull).toUpperCase());
    body.innerHTML = '';

    if (skull.phylactery) {
      const art = document.createElement('div');
      art.className = 'phylactery-modal-art'; art.setAttribute('role', 'img'); art.setAttribute('aria-label', 'Золотой филактерий со зловещей душой Лича');
      art.innerHTML = tileSymbol({ type: 'skull', skull: { phylactery: true } }); body.appendChild(art);
    } else {
      const img = document.createElement('img');
      img.className = 'skull-feat-art';
      img.src = raidPortrait(skull);
      img.dataset.portrait = raidPortraitKey(skull);
      img.alt = boss ? 'Босс' : ('Особый противник: ' + specialTypeName(skull));
      body.appendChild(img);
    }

    const stats = document.createElement('p');
    stats.className = 'skull-feat-stats';
    const cd = skull.countdown > 0 ? ` · ⏱ ${skull.countdown}` : '';
    stats.innerHTML = skull.phylactery
      ? '<b>Возродится в конце следующего хода, если не собрать</b>'
      : `<b>Атака ${skull.atk}</b> · <b>Броня ${skull.def}</b> · <b>HP ${skull.hp}</b>${cd}`;
    body.appendChild(stats);
    if (ELEMENTAL_COMBAT) {
      const elements = document.createElement('p');
      elements.className = 'save-note';
      elements.textContent = 'Стихия: ' + combatElementLabel(skull) + (boss ? '. Множитель каждого меча — среднее по двум стихиям.' : '. Вклад каждого меча/стрелы: ×1,25 / ×1 / ×0,75.');
      body.appendChild(elements);
    }

    const feats = document.createElement('div');
    feats.className = 'skull-feat-list';
    const items = skull.phylactery
      ? ['Филактерий считается монетой и может входить в цепочку монет.', 'Собери цепочку из 3 или более монет вместе с филактерием в следующем ходу.', 'Если не собрать её до конца хода, Лич воскреснет. Сбор филактерия окончательно побеждает Лича и даёт награду особого.']
      : boss
      ? ['Лич — босс: атака ×3, броня ×3 и HP ×6 от базовых параметров.',
         'Броня Лича не ниже 1; изъян меха «Громкий» добавляет ему 5% HP.',
         ELEMENTAL_COMBAT ? 'Способности Лича одинаковы при любом пилоте; особые эффекты пилотов по-прежнему связаны с их смертными врагами.' : 'Портрет Лича не назначает ему стихию или новую способность.']
      : (skull.type && ENEMY_FEATS[skull.type] ? ENEMY_FEATS[skull.type].feats : ['Особенность особого врага.', 'Броня не ниже 1.', 'Убийства особых врагов приближают трофей.']);
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
    bindReadOnlyDismiss(btn, () => { if (runId === RUN_ID && S.pendingModal === 'skull') closeModal(); });
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
    if (!S || UPDATES_OPEN || HISTORY_OPEN || RETUNE_DRAFT || S.over || S.resolving || S.pendingModal || !Number.isInteger(slot) || !ownsRunSlot()) return false;
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
    const ordered = activeSkillDefinitions().slice().sort((a, b) => a.native - b.native);
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
      btn.innerHTML = `<span class="shop-icon">${raidItemIcon(item.slot, item)}</span><span class="shop-body">
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
      btn.innerHTML = `<span class="lv-chip">${raidItemIcon(opt.slot, item)}</span>
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
          log('🔭 Наблюдатель превратил меч во врага');
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
            log('🌪️ Хаотический поглотил обычного врага без награды');
          }
          S.board[i] = { type: 'empty' };
          S.board[pick] = { type: 'skull', skull: s };
        }
      } else if (t === 16) { // Assassin — bottom row: direct 2× hit; mortal enemy (Faun): kill the tile below
        if (roundOrCol === ROWS - 1) {
          const bonusDmg = 2 * enemyGetAttack(s);
          damageHeroWithHaptics(bonusDmg, false, true);
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
          const fresh = makeSkull(false, true, 32, s); // Stats-only revival, never reroll this Lich's element.
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
      if (activeDrag || e.isPrimary === false || e.button > 0 || [...ORB_VIEWS.values()].some(view => view.motion)) return;
      if (!S || UPDATES_OPEN || HISTORY_OPEN || RETUNE_DRAFT || SAVE_CONFLICT || S.over || S.resolving || S.pendingModal) return;
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
  let PREPARED_MECH_ID = null;
  const pickerAffinities = new Map(); // unpaid preparation only; NEVER profile storage
  let classEditor = null;

  function renderClassPicker() {
    const grid = el('picker-grid');
    if (!grid) return;
    PREPARED_MECH_ID = null;
    el('class-picker-main').hidden = false;
    el('raid-region-screen').hidden = true;
    el('picker-card')?.classList.remove('region-screen-open');
    el('class-picker')?.classList.remove('region-mode-open');
    grid.innerHTML = '';
    const generation = ++pickerGeneration;
    const current = () => pickerOpen && !classEditor && !NEW_RUN_REQUEST && !UPDATES_OPEN && !HISTORY_OPEN && generation === pickerGeneration;
    el('picker-card')?.querySelector('.mech-detail-popover')?.remove();
    const detail = document.createElement('section');
    let detailOwner = null;
    detail.className = 'mech-detail-popover'; detail.hidden = true;
    detail.setAttribute('role', 'dialog'); detail.setAttribute('aria-modal', 'true'); detail.setAttribute('aria-label', 'Сведения о мехе');
    const detailBody = document.createElement('div'); detailBody.className = 'mech-detail-body';
    detail.appendChild(detailBody); el('picker-card')?.appendChild(detail);
    const restoreDetailControls = () => {
      if (!detailOwner) return;
      for (const selector of ['.picker-affinity-field', '.picker-affinity-note', '.class-config-btn']) {
        const node = detailBody.querySelector(selector); if (node) detailOwner.appendChild(node);
      }
    };
    const closeDetail = () => {
      restoreDetailControls();
      const trigger = detailOwner?.querySelector('.picker-card-btn');
      detailOwner = null; detail.hidden = true; detailBody.replaceChildren();
      trigger?.focus();
    };
    for (const cls of CLASSES) {
      const unlocked = isClassUnlocked(cls.id), config = getClassConfig(cls.id);
      // A mech always has its native pilot unless a valid alternate pilot is explicitly saved.
      let pilotRace = RACE_INFO[config.race] ? config.race : cls.race;
      const row = document.createElement('div'); row.className = 'picker-class';
      const btn = document.createElement('button');
      btn.type = 'button'; btn.dataset.classId = cls.id;
      btn.className = 'picker-card-btn' + (unlocked ? '' : ' locked') + (getSelectedClassId() === cls.id ? ' selected' : '');
      btn.setAttribute('aria-label', (unlocked ? 'Новый забег: ' : 'Закрытый мех: ') + cls.name);
      const desc = [];
      for (const kind of ['perks', 'flaws']) {
        const names = config[kind].filter(Boolean).map(name => CLASS_TRAITS[kind][name].name), positive = kind === 'perks';
        if (names.length) desc.push(`<span class="${positive ? 'pick-perk' : 'pick-flaw'}">Мех · ${positive ? '+' : '−'} ${names.join(' · ')}</span>`);
      }
      const status = unlocked ? `<span class="pick-lvl">Уровень меха ${classLevelOf(cls.id)} · начать новый забег</span>`
        : '<span class="pick-locked">Закрыт · нужен трофей на поле</span>';
      btn.innerHTML = `<span class="mech-portrait"><img src="miniapp_preview/assets/mech_${cls.id}.png" alt="" draggable="false"><span class="mech-portrait-ring"></span></span><span class="pick-name">${cls.name}</span><span class="mech-card-status">${unlocked ? `УР. ${classLevelOf(cls.id)}` : 'ЗАКРЫТ'}</span>`;
      btn.title = `Открыть карточку меха «${cls.name}»`;
      btn.setAttribute('aria-haspopup', 'dialog');
      const line = (label, text) => {
        const p = document.createElement('p'); p.className = 'mech-detail-line';
        const b = document.createElement('b'); b.textContent = label;
        p.append(b, document.createTextNode(text)); return p;
      };
      const openGarage = () => {
        if (!current()) return;
        restoreDetailControls(); detailOwner = row; detailBody.classList.remove('mech-info-view'); detailBody.classList.add('mech-garage-home'); detailBody.replaceChildren();
        const portrait = document.createElement('div'); portrait.className = 'mech-detail-portrait';
        const image = document.createElement('img'); image.src = `miniapp_preview/assets/mech_${cls.id}.png`; image.alt = ''; image.draggable = false; portrait.appendChild(image);
        const heading = document.createElement('h3'); heading.textContent = cls.name;
        const hero = document.createElement('div'); hero.className = 'mech-garage-hero'; hero.classList.add('mech-garage-mech-button');
        hero.setAttribute('role', 'button'); hero.tabIndex = 0; hero.setAttribute('aria-label', `О мехе: ${cls.name}`);
        hero.append(portrait, heading);
        const cockpit = document.createElement('div'); cockpit.className = 'mech-cockpit-art';
        cockpit.setAttribute('role', 'button'); cockpit.tabIndex = 0;
        cockpit.setAttribute('aria-label', `Пилот: ${RACE_INFO[pilotRace].name}`);
        cockpit.dataset.mechId = cls.id; cockpit.dataset.pilotRace = pilotRace;
        const headArt = document.createElement('img'); headArt.className = 'mech-cockpit-head';
        const artRevision = document.querySelector('meta[name="raid-build"]')?.content || 'dev';
        headArt.src = `${CLASS_HEAD_ART[cls.id] || CLASS_HEAD_ART.adventurer}?v=${encodeURIComponent(artRevision)}`;
        headArt.alt = ''; headArt.draggable = false;
        const cockpitCaption = document.createElement('span'); cockpitCaption.className = 'mech-cockpit-caption';
        cockpitCaption.textContent = `Пилот: ${RACE_INFO[pilotRace].name}`;
        cockpit.append(headArt, cockpitCaption);
        const actions = document.createElement('div'); actions.className = 'mech-detail-actions mech-garage-side-actions';
        const detailsView = (kind) => {
          restoreDetailControls(); detailBody.classList.remove('mech-garage-home'); detailBody.classList.add('mech-info-view'); detailBody.replaceChildren();
          const back = document.createElement('button'); back.type = 'button'; back.className = 'mech-detail-back'; back.textContent = 'Назад'; back.addEventListener('click', openGarage);
          const footer = document.createElement('div'); footer.className = 'mech-detail-footer';
          const tabTitle = document.createElement('h3'); tabTitle.className = 'mech-tab-title'; tabTitle.textContent = kind === 'pilot' ? RACE_INFO[pilotRace].name : 'О мехе';
          detailBody.append(tabTitle);
          if (kind === 'pilot') {
            const selectablePilots = Object.keys(RACE_INFO).filter(isRaceUnlocked);
            const canSelectPilot = classRules(cls.id).canChangeRace && selectablePilots.length > 1;
            let pendingPilotRace = pilotRace;
            const detailPortrait = document.createElement('div'); detailPortrait.className = 'mech-detail-portrait mech-pilot-small-portrait';
            const pilotImage = document.createElement('img'); pilotImage.src = pilotArtPath(pendingPilotRace); pilotImage.alt = RACE_INFO[pendingPilotRace].name; pilotImage.draggable = false;
            detailPortrait.appendChild(pilotImage);
            const pilotDescription = line('Пилот · ', `${RACE_INFO[pendingPilotRace].name} — ${RACE_INFO[pendingPilotRace].desc}`);
            if (canSelectPilot) {
              const selection = document.createElement('div'); selection.className = 'mech-pilot-selection';
              const arrow = (direction, glyph, label) => {
                const button = document.createElement('button'); button.type = 'button'; button.className = `mech-pilot-arrow ${direction}`;
                button.textContent = glyph; button.setAttribute('aria-label', label); selection.appendChild(button); return button;
              };
              const previous = arrow('previous', '', 'Предыдущий пилот');
              selection.appendChild(detailPortrait);
              const next = arrow('next', '', 'Следующий пилот');
              detailBody.appendChild(selection);
              const refreshPilot = () => {
                pilotImage.src = pilotArtPath(pendingPilotRace); pilotImage.alt = RACE_INFO[pendingPilotRace].name;
                pilotDescription.lastChild.textContent = `${RACE_INFO[pendingPilotRace].name} — ${RACE_INFO[pendingPilotRace].desc}`;
                tabTitle.textContent = RACE_INFO[pendingPilotRace].name;
              };
              const stepPilot = delta => {
                const index = selectablePilots.indexOf(pendingPilotRace);
                pendingPilotRace = selectablePilots[(index + delta + selectablePilots.length) % selectablePilots.length];
                refreshPilot();
              };
              previous.addEventListener('click', () => stepPilot(-1));
              next.addEventListener('click', () => stepPilot(1));
              const accept = document.createElement('button'); accept.type = 'button'; accept.className = 'mech-pilot-accept';
              accept.textContent = 'Принять'; accept.addEventListener('click', () => {
                if (pendingPilotRace === pilotRace) { openGarage(); return; }
                if (!saveClassConfig(cls.id, { ...config, race: pendingPilotRace })) {
                  accept.textContent = 'Ошибка сохранения'; accept.disabled = true; return;
                }
                pilotRace = config.race = pendingPilotRace;
                cockpit.dataset.pilotRace = pilotRace;
                cockpit.setAttribute('aria-label', `Пилот: ${RACE_INFO[pilotRace].name}`);
                cockpitCaption.textContent = `Пилот: ${RACE_INFO[pilotRace].name}`;
                openGarage();
              });
              footer.append(back, accept);
            } else {
              detailBody.appendChild(detailPortrait);
              footer.appendChild(back);
            }
            detailBody.append(pilotDescription, line('Склонность · ', 'Выбирается на этот рейд, начиная со 2-го уровня меха; бонус и штраф урона открываются на 3-м.'));
            const affinity = row.querySelector('.picker-affinity-field');
            const affinityNote = row.querySelector('.picker-affinity-note');
            if (affinity) detailBody.appendChild(affinity);
            if (affinityNote) detailBody.appendChild(affinityNote);
          } else {
            detailBody.appendChild(portrait.cloneNode(true));
            detailBody.append(line('Специализация · ', cls.clsDesc || 'Особенности задаются настройками этого меха.'),
              line('Модуль · ', cls.uniqueSkillDesc),
              line('Пассивы · ', config.perks.filter(Boolean).map(name => CLASS_TRAITS.perks[name].name).join(' · ') || 'нет'),
              line('Изъяны · ', config.flaws.filter(Boolean).map(name => CLASS_TRAITS.flaws[name].name).join(' · ') || 'нет'),
              line('Доступ · ', unlocked ? `уровень меха ${classLevelOf(cls.id)} · можно начать новый рейд` : 'закрыт · откроется за трофей на поле'));
            const configButton = row.querySelector('.class-config-btn');
            if (configButton) detailBody.appendChild(configButton);
            footer.appendChild(back);
          }
          detailBody.appendChild(footer);
        };
        hero.addEventListener('click', () => detailsView('mech'));
        hero.addEventListener('keydown', event => {
          if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); detailsView('mech'); }
        });
        cockpit.addEventListener('click', () => detailsView('pilot'));
        cockpit.addEventListener('keydown', event => {
          if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); detailsView('pilot'); }
        });
        for (const [kind, text, label] of [['pilot', 'Пилот', 'О пилоте'], ['mech', 'Мех', 'О мехе']]) {
          const control = document.createElement('div'); control.className = 'mech-garage-action';
          const action = document.createElement('button'); action.type = 'button'; action.className = `mech-detail-topic mech-garage-icon-button ${kind}`;
          action.setAttribute('aria-label', label);
          if (kind === 'pilot') {
            const pilotIcon = document.createElement('img'); pilotIcon.className = 'mech-garage-pilot-icon';
            pilotIcon.src = pilotArtPath(pilotRace); pilotIcon.alt = ''; pilotIcon.draggable = false; action.appendChild(pilotIcon);
          }
          if (kind === 'mech') {
            const art = document.createElement('img'); art.className = 'mech-garage-icon-art';
            art.src = `miniapp_preview/assets/mech_${cls.id}.png`; art.alt = ''; art.draggable = false; action.appendChild(art);
          }
          const caption = document.createElement('span'); caption.className = 'mech-garage-icon-label'; caption.textContent = text;
          action.addEventListener('click', () => detailsView(kind)); control.append(action, caption); actions.appendChild(control);
        }
        const go = document.createElement('button'); go.type = 'button'; go.className = 'mech-go-button'; go.textContent = 'Поехали!';
        go.disabled = !isClassUnlocked(cls.id);
        go.addEventListener('click', () => {
          if (!current() || !isClassUnlocked(cls.id)) return;
          closeDetail();
          const screen = el('raid-region-screen'), card = el('picker-card');
          if (!screen || !card) { requestNewGame(cls.id); return; }
          PREPARED_MECH_ID = cls.id;
          el('class-picker-main').hidden = true;
          screen.dataset.preparedClassId = cls.id;
          screen.hidden = false; card.classList.add('region-screen-open');
          el('class-picker').classList.add('region-mode-open');
          applyRaidRegion(RAID_REGION);
          el('region-emblem-launch')?.focus();
        });
        detailBody.append(hero, cockpit, actions, go);
        detail.hidden = false; go.focus();
      };
      // Selecting any mech opens its circular garage card; launch is a deliberate second action.
      bindReadOnlyDismiss(btn, openGarage, () => generation);
      row.appendChild(btn);
      if (unlocked && classLevelOf(cls.id) >= 2) {
        const label = document.createElement('fieldset'); label.className = 'picker-affinity-field';
        const caption = document.createElement('legend'); caption.textContent = 'Склонность пилота · на этот рейд';
        const toggle = document.createElement('div'); toggle.className = 'affinity-segments';
        toggle.setAttribute('role', 'radiogroup'); toggle.setAttribute('aria-label', 'Склонность пилота на этот рейд');
        const note = document.createElement('p'); note.className = 'picker-affinity-note'; note.id = `affinity-note-${cls.id}`;
        note.setAttribute('role', 'status'); note.setAttribute('aria-live', 'polite'); toggle.setAttribute('aria-describedby', note.id);
        const choices = ['fire', 'air', 'earth', 'water', ''];
        const labels = { fire: 'Огонь', air: 'Воздух', earth: 'Земля', water: 'Вода', '': 'Без склонности' };
        for (const element of choices) {
          const choice = document.createElement('label'); choice.className = 'affinity-choice'; choice.title = labels[element];
          const input = document.createElement('input'); input.type = 'radio'; input.name = `affinity-${cls.id}`;
          input.value = element; input.dataset.classId = cls.id; input.checked = (pickerAffinities.get(cls.id) || '') === element;
          input.setAttribute('aria-label', labels[element]);
          const text = document.createElement('span'); text.textContent = element === 'air' ? 'Возд.' : element === '' ? 'Нет' : labels[element];
          const refresh = () => { note.textContent = pilotAffinityDescription(preparedPilotAffinity(cls.id)); };
          input.addEventListener('change', () => {
              if (!current() || !isClassUnlocked(cls.id) || classLevelOf(cls.id) < 2 || input.value && !RAID_ELEMENTS.includes(input.value)) {
              const selected = pickerAffinities.get(cls.id) || '';
              row.querySelectorAll(`input[name="affinity-${cls.id}"]`).forEach(option => { option.checked = option.value === selected; });
              return;
            }
            pickerAffinities.set(cls.id, input.value); refresh();
          });
          choice.append(input, text); toggle.appendChild(choice);
        }
        label.append(caption, toggle); row.append(label, note);
        // renderClassPicker also runs just before pickerOpen is set true.
        note.textContent = pilotAffinityDescription({ element: null, level: classLevelOf(cls.id) });
      } else if (unlocked) {
        // A locked mech already explains its trophy gate; only a usable level-one
        // mech needs to say when the per-raid choice opens.
        const note = document.createElement('p'); note.className = 'picker-affinity-note';
        note.textContent = 'Склонность пилота — с уровня меха 2; бонус и штраф урона — с 3.'; row.appendChild(note);
      }
      const edit = document.createElement('button');
      edit.type = 'button'; edit.className = 'class-config-btn'; edit.dataset.classId = cls.id;
      edit.textContent = classRules(cls.id).editable ? 'Настроить мех и пилота' : 'О мехе и пилоте';
      edit.setAttribute('aria-label', edit.textContent + ': ' + cls.name);
      edit.addEventListener('click', () => { if (current()) { closeDetail(); openClassEditor(cls.id); } });
      row.appendChild(edit); grid.appendChild(row);
    }
    detail.addEventListener('click', event => { if (event.target === detail) closeDetail(); });
    detail.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); closeDetail(); } });
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
    const title = el('class-editor-title'); title.textContent = 'Мех «' + info.name + '» и пилот'; title.focus();
    el('class-editor-hint').textContent = !info.editable
      ? id === 'adventurer' ? 'Авантюрист не настраивается: пилот Наёмник, без пассивов и изъянов меха; пул — все открытые навыки.'
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
    const raceSection = block('Пилот · особенность и смертный враг'); raceSection.dataset.configSource = 'pilot';
    note(raceSection, 'Один пакет целиком: особенность и взаимодействие со смертным врагом. Он заменяет прежнюю расу, а не добавляет третий слой бонусов.');
    select(raceSection, 'Пилот следующего забега', 'race', null,
      Object.entries(RACE_INFO).map(([race, data]) => {
        const donor = CLASSES.find(c => c.race === race);
        const locked = !isRaceUnlocked(race);
        return { value: race, label: data.name + (locked ? ` · ${donor.name}, ур. 5` : ''), locked };
      }), !info.canChangeRace);
    note(raceSection, id === 'adventurer' ? 'У Авантюриста всегда пилот Наёмник.' : info.canChangeRace ? 'Доступны Наёмник и родные пилоты мехов-доноров, достигших уровня 5.' : 'Независимый выбор пилота — с уровня меха 5; пилот другого меха открывается при уровне меха-донора 5.');
    const raceDesc = note(raceSection, RACE_INFO[draft.race].desc);
    const traits = block('Мех · пассивы и изъяны'); traits.dataset.configSource = 'mech';
    note(traits, 'Первая пара фиксирована. Новые пары — на уровнях меха 3, 7 и 9. Пассиву нужен изъян в той же паре; повторы не складываются.');
    for (let i = 0; i < 4; i++) {
      const pair = document.createElement('div'); pair.className = 'class-config-pair'; traits.appendChild(pair);
      note(pair, `Пара ${i + 1}` + (i === 0 ? ' · основная' : ` · с уровня ${CLASS_PAIR_LEVELS[i]}`));
      for (const kind of ['perks', 'flaws']) select(pair, kind === 'perks' ? 'Пассив' : 'Изъян', kind, i,
        [{ value: '', label: 'Нет' }, ...Object.entries(CLASS_TRAITS[kind]).map(([name, data]) => ({
          value: name, label: data.name + ' — ' + data.desc + (isTraitUnlocked(kind, name) ? '' : ' · мех ещё закрыт'), locked: !isTraitUnlocked(kind, name),
        }))], !info.editable || i === 0 || !info.pairSlots[i]);
    }
    const skillSection = block(id === 'adventurer' ? 'Мех · все открытые модули' : 'Мех · пул модулей: 20 позиций'); skillSection.dataset.configSource = 'mech';
    const count = note(skillSection, ''); count.id = 'class-replacement-count'; count.setAttribute('role', 'status');
    note(skillSection, 'Это возможные награды уровня, не стартовые умения. Первый навык меха фиксирован. Повторы не увеличивают шанс выпадения.');
    if (id === 'adventurer') note(skillSection, activeSkillDefinitions(true).filter(s => isSkillUnlocked(s.id)).sort((a,b) => a.native-b.native).map(s => s.name).join(' · '));
    else for (let i = 0; i < 20; i++) {
      const base = SKILL_BY_ID[info.baseSkills[i]];
      select(skillSection, `${i + 1}. ${base.name}` + (i === 0 ? ' · меховый' : ''), 'replacements', i,
        [{ value: '', label: base.name + ' · по умолчанию' }, ...activeSkillDefinitions(true).filter(s => !s.nativeOnly && s.id !== base.id).sort((a,b) => a.native-b.native).map(skill => ({
          value: skill.id, label: skill.name + (isSkillUnlocked(skill.id) ? '' : ' · мех-источник: ур. 10'), locked: !isSkillUnlocked(skill.id),
        }))], !info.editable || i === 0 || info.replacementLimit === 0);
    }
    const growth = block('Развитие меха');
    note(growth, 'Склонность пилота выбирается заново перед каждым рейдом: 2 — выпадение 30/20/25/25; 3 — дополнительно урон +5%/−5%. Это не постоянная настройка меха или пилота. 1 — основная пара доступна другим мехам; 2/4/6/8/10 — можно заменить 2/4/6/8/10 навыков; 3/7/9 — новая пара; 5 — независимый выбор пилота и открытие родного пилота другим; 10 — уникальный навык доступен другим мехам. Рост — за трофеи, не за опыт героя.');
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
    closeGameSettings(false);
    closeRetune(false);
    if (S?.resolving || S?.pendingModal) return false;
    const picker = el('class-picker');
    if (!picker) { newGame(); return true; }
    closeClassEditor(false); pickerAffinities.clear(); renderClassPicker(); pickerOpen = true;
    const resume = el('picker-resume'), info = savedRunInfo();
    if (resume) {
      resume.hidden = !(S && !S.over || info);
      const resumeRegion = S && !S.over && Object.hasOwn(RAID_REGIONS, RUN_REGION) ? RUN_REGION
        : info && Object.hasOwn(RAID_REGIONS, info.region) ? info.region : null;
      const loc = resumeRegion ? RAID_REGIONS[resumeRegion] : null;
      const isLegacy = !(S && !S.over) && info?.combatRules === 'classic-v1';
      resume.textContent = isLegacy
        ? (loc ? `Продолжить · ${loc.short} · прежние правила` : 'Продолжить · прежние правила')
        : (loc ? `Продолжить · ${loc.short}` : 'Продолжить');
      resume.title = S && !S.over ? `Вернуться в текущий забег${loc ? ` (${loc.title})` : ''}`
        : info ? `Продолжить рейд меха «${CLASS_BY_ID[info.classId].name}»${loc ? ` · ${loc.title}` : ''}, ход ${info.turn}` : 'Продолжить забег';
    }
    picker.classList.add('show');
    const card = el('picker-card'); if (card) card.scrollTop = 0;
    syncIntroMusic();
    return true;
  }
  function resumeFromClassPicker() {
    if (!pickerOpen) return;
    if (!S || S.over) { resumeSavedRun(); return; }
    if (SAVE_CONFLICT) { flash('Обнови страницу: сохранение изменено в другой вкладке'); return; }
    closeClassEditor(false); pickerOpen = false; el('class-picker').classList.remove('show');
    syncIntroMusic();
    el('menu-btn')?.focus();
  }

  function requestNewGame(classId = getSelectedClassId()) {
    if (UPDATES_OPEN) return false;
    if (!isClassUnlocked(classId)) return false;
    // Repeat/menu starts at level 2+ must offer a FRESH choice, never silently
    // reuse the last run's affinity or turn a live checkpoint into a surrender.
    if (classLevelOf(classId) >= 2 && !pickerOpen) {
      if (finishedRunView()) leaveFinishedRun();
      else {
        if (S?.resolving || S?.pendingModal) { flash('Сначала заверши ход и выбор наград'); return false; }
        el('menu-modal')?.classList.remove('show'); showClassPicker();
      }
      return false;
    }
    if ((!S || S.over) && !savedRunInfo() && !hasPendingProgress()) { setSelectedClassId(classId); newGame(); return true; }
    const wrap = el('restart-modal');
    if (!wrap) return false;
    NEW_RUN_REQUEST = { classId, runId: RUN_ID, focus: document.activeElement, affinity: preparedPilotAffinity(classId) };
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
    for (const id of ['picker-telegram-status', 'menu-telegram-status', 'run-telegram-status']) {
      const node = el(id); if (node) { node.textContent = text; node.hidden = false; }
    }
  }
  function telegramLayer(id) { return !!el(id)?.classList.contains('show'); }
  function syncTelegramChrome() {
    if (!TELEGRAM_APP) return;
    const exit = el('gameover-close'); if (exit) exit.hidden = false; // also a late SDK connection
    const live = !!S && !S.over;
    // Back belongs to nested views only. At the board/reward root Telegram
    // must keep its native Close control, not trap the user in a menu loop.
    const back = UPDATES_OPEN || !!GAME_SETTINGS || HISTORY_OPEN || !!RETUNE_DRAFT || !!classEditor || pickerOpen && live
      || ['help-modal', 'beginner-guide', 'restart-modal', 'menu-modal', 'exit-modal'].some(telegramLayer)
      || ['stats', 'skull'].includes(S?.pendingModal) || finishedRunView();
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
    closeGameSettings(false);
    closeRetune(false);
    cancelPointerGesture();
    // A pending reward is a resumable checkpoint, not a reason to forbid exit.
    // close() can bypass the native closing prompt, so an unsuccessful write
    // needs our explicit, accessible discard confirmation before that request.
    const saved = !S || saveRun();
    if (!saved && !discard) {
      const wrap = el('exit-modal');
      if (!wrap) return false;
      TELEGRAM_EXIT = { runId: RUN_ID, focus: document.activeElement };
      el('exit-confirm-text').textContent = S?.over
        ? 'Результат завершённого забега не удалось надёжно сохранить. После закрытия результат или достижения могут потеряться. Остаться и повторить запись или всё равно закрыть?'
        : 'Текущий момент не удалось сохранить. После закрытия последние действия могут потеряться; продолжение не гарантируется. Остаться и повторить запись или всё равно закрыть?';
      wrap.hidden = false; wrap.classList.add('show'); el('exit-cancel')?.focus();
      syncTelegramChrome();
      return false;
    }
    cancelTelegramExit(false);
    if (telegramCall(TELEGRAM_APP, 'close')) {
      const exit = el('gameover-close'); if (exit) exit.disabled = true;
      return true;
    }
    telegramNotice('Команда закрытия недоступна. Вернись в игру и используй кнопку закрытия Telegram в шапке.');
    flash('Для выхода используй кнопку закрытия Telegram в шапке');
    syncTelegramChrome();
    return false;
  }
  function telegramBack() {
    // A read-only notice has no gesture/reward to cancel underneath it.
    if (UPDATES_OPEN) { closeUpdateNotice(); return; }
    if (GAME_SETTINGS) { closeGameSettings(); return; }
    if (RETUNE_DRAFT) { closeRetune(); return; }
    cancelPointerGesture();
    if (telegramLayer('exit-modal')) cancelTelegramExit();
    else if (telegramLayer('restart-modal')) cancelNewGame();
    else if (telegramLayer('beginner-guide')) { el('beginner-guide').classList.remove('show'); el('menu-help')?.focus(); }
    else if (telegramLayer('help-modal')) { el('help-modal').classList.remove('show'); el('menu-btn')?.focus(); }
    else if (HISTORY_OPEN) closeRunHistory();
    else if (classEditor) closeClassEditor();
    else if (pickerOpen && S && !S.over) resumeFromClassPicker();
    else if (telegramLayer('menu-modal')) { el('menu-modal').classList.remove('show'); el('menu-btn')?.focus(); }
    else if (S?.pendingModal === 'stats' || S?.pendingModal === 'skull') closeModal();
    else if (finishedRunView()) leaveFinishedRun();
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
        positionGameSettings();
      }
      telegramCall(tg, 'ready'); telegramCall(tg, 'expand');
      let canDisable = typeof tg.disableVerticalSwipes === 'function';
      try { if (typeof tg.isVersionAtLeast === 'function') canDisable = canDisable && tg.isVersionAtLeast('7.7'); } catch (ignored) { canDisable = false; }
      const protectedSwipes = canDisable && telegramCall(tg, 'disableVerticalSwipes');
      telegramCall(tg, 'setHeaderColor', '#140e22'); telegramCall(tg, 'setBackgroundColor', '#140e22');
      viewport();
      for (const event of ['viewportChanged', 'safeAreaChanged', 'contentSafeAreaChanged']) telegramCall(tg, 'onEvent', event, viewport);
      telegramCall(tg, 'onEvent', 'activated', event => { audioHostActive = true; viewport(event); syncIntroMusic(); });
      telegramCall(tg, 'onEvent', 'deactivated', () => {
        closeGameSettings(false); audioHostActive = false; stopMaterialAudio(); stopIntroMusic(); closeRetune(false);
        // Уход в хост бросает недостроенную цепочку: возвращение начинается
        // с чистого выбора, а не с продолжения прежнего.
        cancelPointerGesture(); if (S && !S.resolving) clearChain(); saveRun();
      });
      telegramCall(tg.BackButton, 'onClick', telegramBack);
      document.addEventListener('click', syncTelegramChrome);
      // Includes Escape and programmatic reader/editor changes, without timers
      // or touching any gameplay state. Mutations of host chrome are external.
      if (typeof window.MutationObserver === 'function') {
        const observer = new window.MutationObserver(syncTelegramChrome);
        for (const id of ['class-picker', 'class-editor', 'history-modal', 'help-modal', 'beginner-guide', 'restart-modal', 'exit-modal', 'menu-modal', 'modal', 'updates-modal', 'game-settings']) {
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

  // --- direct board settings (transient UI, NOT a pause/checkpoint) ----------
  let GAME_SETTINGS = null, SETTINGS_OUTSIDE = null;
  function positionGameSettings() {
    if (!GAME_SETTINGS) return;
    const wrap = el('game-settings'), card = qs('.game-settings-card');
    const bounds = wrap.getBoundingClientRect(), anchor = el('settings-btn').getBoundingClientRect();
    const size = card.getBoundingClientRect(), style = window.getComputedStyle(wrap);
    const pad = side => parseFloat(style['padding' + side]) || 12;
    const left = Math.max(pad('Left'), Math.min(anchor.left - bounds.left + anchor.width / 2 - size.width / 2, bounds.width - pad('Right') - size.width));
    const top = Math.max(pad('Top'), Math.min(anchor.bottom - bounds.top + 8, bounds.height - pad('Bottom') - size.height));
    card.style.left = left + 'px'; card.style.top = top + 'px';
  }
  function openGameSettings() {
    if (GAME_SETTINGS || !S || S.over || activeDrag || pickerOpen || UPDATES_OPEN || HISTORY_OPEN || S.pendingModal
      || ['menu-modal', 'help-modal', 'beginner-guide', 'restart-modal', 'exit-modal'].some(telegramLayer)) return false;
    const wrap = el('game-settings'); if (!wrap) return false;
    GAME_SETTINGS = { background: [] }; SETTINGS_OUTSIDE = null;
    wrap.hidden = false; wrap.classList.add('show'); el('settings-btn').setAttribute('aria-expanded', 'true');
    // Retain the underlying chain/A1 draft. Inert plus capture guards block
    // queued input too; no S writes, saveRun, gesture cancellation or timers.
    for (const node of document.body.children) if (node !== wrap && node.tagName !== 'SCRIPT') {
      GAME_SETTINGS.background.push({ node, inert: node.hasAttribute('inert'), aria: node.getAttribute('aria-hidden') });
      node.setAttribute('inert', ''); node.setAttribute('aria-hidden', 'true');
    }
    positionGameSettings(); qs('#game-visual input')?.focus({ preventScroll: true });
    syncTelegramChrome(); return true;
  }
  function closeGameSettings(focus = true) {
    if (!GAME_SETTINGS) return;
    const session = GAME_SETTINGS; GAME_SETTINGS = SETTINGS_OUTSIDE = null;
    const wrap = el('game-settings'); wrap.classList.remove('show'); wrap.hidden = true;
    el('settings-btn').setAttribute('aria-expanded', 'false');
    for (const { node, inert, aria } of session.background) {
      if (!inert) node.removeAttribute('inert');
      if (aria === null) node.removeAttribute('aria-hidden'); else node.setAttribute('aria-hidden', aria);
    }
    if (focus) el('settings-btn')?.focus({ preventScroll: true });
    syncTelegramChrome();
  }
  function initGameSettings() {
    const button = el('settings-btn'), close = el('settings-close'), wrap = el('game-settings'), card = qs('.game-settings-card');
    if (!button || !close || !wrap || !card) return;
    bindReadOnlyDismiss(button, openGameSettings, () => RUN_ID);
    bindReadOnlyDismiss(close, closeGameSettings, () => GAME_SETTINGS);
    // Dismiss on CLICK, not pointerdown: keep the shield through pointerup
    // and the synthesized click. That same tap must never touch the board,
    // cast/confirm A1 or press another header control after closing the panel.
    for (const kind of ['pointerdown', 'pointerup', 'click', 'dblclick', 'contextmenu']) document.addEventListener(kind, event => {
      if (!GAME_SETTINGS || card.contains(event.target)) return;
      if (kind === 'pointerdown' && event.isPrimary !== false && !(event.button > 0)) SETTINGS_OUTSIDE = GAME_SETTINGS;
      const dismiss = kind === 'click' && (event.detail === 0 || SETTINGS_OUTSIDE === GAME_SETTINGS);
      event.preventDefault(); event.stopImmediatePropagation();
      if (dismiss) closeGameSettings();
    }, true);
    document.addEventListener('pointercancel', () => { SETTINGS_OUTSIDE = null; }, true);
    document.addEventListener('keydown', event => {
      if (!GAME_SETTINGS) return;
      if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); closeGameSettings(); return; }
      if (event.key !== 'Tab') return;
      const controls = [...wrap.querySelectorAll('button:not(:disabled), input:not(:disabled), summary')].filter(node => !node.closest('details:not([open])') || node.tagName === 'SUMMARY');
      const first = controls[0], last = controls[controls.length - 1];
      if (!wrap.contains(document.activeElement) || event.shiftKey && document.activeElement === first || !event.shiftKey && document.activeElement === last) {
        event.preventDefault(); (event.shiftKey ? last : first)?.focus();
      }
      event.stopImmediatePropagation(); // A1 has its OWN underlying Tab ring.
    }, true);
    document.addEventListener('focusin', event => {
      if (GAME_SETTINGS && !wrap.contains(event.target)) { event.stopImmediatePropagation(); qs('#game-visual input')?.focus({ preventScroll: true }); }
    }, true);
    // Prevent background scroll without changing shell sizing/scrollbar width.
    for (const kind of ['wheel', 'touchmove']) wrap.addEventListener(kind, event => {
      if (!card.contains(event.target)) event.preventDefault();
    }, { passive: false });
    window.addEventListener?.('resize', positionGameSettings);
    document.addEventListener('scroll', positionGameSettings, true);
    if (typeof window.ResizeObserver === 'function') {
      const observer = new window.ResizeObserver(positionGameSettings); observer.observe(wrap); observer.observe(card);
    }
  }

  // --- first-entry release notes (local UI metadata, NOT a run checkpoint) ---
  // The builder fingerprints all game inputs. Compare the CONTENT meta, never
  // the ?build cache hint. Curated release/history copy lives in the template;
  // each entry carries a monotonic data-release id so only unread notes appear
  // above the fold while already-read notes stay collapsed in history.
  const UPDATES_KEY = 'raid:updatesSeenBuild';
  const UPDATES_RELEASE_KEY = 'raid:updatesSeenRelease';
  const UPDATES_BUILD = document.querySelector('meta[name="raid-build"]')?.getAttribute('content') || '';
  let UPDATES_READY = false, UPDATES_OPEN = false, UPDATES_SESSION = null;
  let UPDATES_CATALOG = [], UPDATES_LATEST_RELEASE = 0;
  const UPDATES_SHOWN = new Set();
  function collectUpdatesCatalog() {
    const wrap = el('updates-modal');
    if (!wrap) return [];
    const items = [];
    for (const node of wrap.querySelectorAll('li[data-release]')) {
      const release = Number(node.getAttribute('data-release'));
      if (!Number.isInteger(release) || release <= 0) continue;
      const bucket = node.closest('ol') ? 'legacy' : 'recent';
      items.push({ release, bucket, template: node.cloneNode(true) });
    }
    items.sort((a, b) => b.release - a.release);
    return items;
  }
  function parseSeenRelease(raw) {
    return typeof raw === 'string' && /^[1-9]\d{0,5}$/.test(raw) ? Number(raw) : null;
  }
  function readUpdateProgress() {
    let seenBuild = null, seenRelease = null;
    try {
      seenBuild = profileStorage.getItem(UPDATES_KEY);
      seenRelease = parseSeenRelease(profileStorage.getItem(UPDATES_RELEASE_KEY));
      return { readFailed: false, seenBuild, seenRelease };
    } catch (ignored) {
      return { readFailed: true, seenBuild: null, seenRelease: null };
    }
  }
  function partitionUpdates(seenBuild, seenRelease) {
    if (!UPDATES_CATALOG.length) return { unread: [], read: [] };
    if (seenRelease !== null) {
      return {
        unread: UPDATES_CATALOG.filter(item => item.release > seenRelease),
        read: UPDATES_CATALOG.filter(item => item.release <= seenRelease),
      };
    }
    if (seenBuild === UPDATES_BUILD) return { unread: [], read: UPDATES_CATALOG.slice() };
    return { unread: UPDATES_CATALOG.slice(0, 1), read: UPDATES_CATALOG.slice(1) };
  }
  function renderUpdateNotice(seenBuild, seenRelease) {
    const current = el('updates-current');
    const history = el('updates-history');
    const desc = el('updates-description');
    if (!current || !history || !UPDATES_CATALOG.length) return;
    const recentList = history.querySelector('.updates-archive-previous');
    const legacyList = history.querySelector('ol');
    const { unread, read } = partitionUpdates(seenBuild, seenRelease);
    current.innerHTML = '';
    for (const item of unread) current.appendChild(item.template.cloneNode(true));
    current.hidden = unread.length === 0;
    if (recentList) {
      recentList.innerHTML = '';
      for (const item of read) if (item.bucket === 'recent') recentList.appendChild(item.template.cloneNode(true));
      recentList.hidden = recentList.children.length === 0;
    }
    if (legacyList) {
      legacyList.innerHTML = '';
      for (const item of read) if (item.bucket === 'legacy') legacyList.appendChild(item.template.cloneNode(true));
      legacyList.hidden = legacyList.children.length === 0;
    }
    if (desc) {
      if (unread.length === 0) desc.textContent = 'Все обновления прочитаны. Полный список изменений — в истории ниже.';
      else if (unread.length === 1) desc.textContent = 'Непрочитанное обновление. Предыдущие выпуски — в истории ниже.';
      else desc.textContent = `Непрочитанные обновления (${unread.length}). Предыдущие выпуски — в истории ниже.`;
    }
    history.open = unread.length === 0;
  }
  function updateNoticeStatus(text) {
    for (const id of ['picker-updates-status', 'menu-updates-status']) {
      const node = el(id); if (node) { node.textContent = text; node.hidden = !text; }
    }
  }
  function openUpdateNotice(automatic = false, readFailed = false, opener = null, progress = null) {
    const wrap = el('updates-modal');
    if (!wrap || !/^[a-f0-9]{12}$/.test(UPDATES_BUILD) || UPDATES_OPEN) return false;
    // Startup never restores/starts a run. A manual reader may cover only an
    // idle view, not a chain/mandatory reward or its asynchronous continuation.
    if (automatic && S) return false;
    if (S && (RETUNE_DRAFT || S.resolving || S.pendingModal || S.selected.length)) {
      flash('Сначала заверши цепочку и выбор наград'); return false;
    }
    closeGameSettings(false);
    const state = progress || (PROFILE_PREFIX === null ? { readFailed: false, seenBuild: null, seenRelease: null } : readUpdateProgress());
    const failed = readFailed || state.readFailed;
    renderUpdateNotice(state.seenBuild, state.seenRelease);
    UPDATES_SESSION = { prefix: PROFILE_PREFIX, focus: opener || document.activeElement, background: [] };
    UPDATES_OPEN = true; UPDATES_SHOWN.add(`${PROFILE_PREFIX}:${UPDATES_BUILD}`);
    el('updates-build').textContent = 'Сборка ' + UPDATES_BUILD;
    el('updates-storage-note').hidden = !(failed || PROFILE_PREFIX === null);
    wrap.hidden = false; wrap.classList.add('show');
    el('updates-body').scrollTop = 0; el('updates-title').focus();
    // Preserve, rather than replace, every underlying view/focus/ARIA state.
    // No S.pendingModal, saveRun, resumeRewards, timers or RNG in this reader.
    for (const node of document.body.children) if (node !== wrap && node.tagName !== 'SCRIPT') {
      UPDATES_SESSION.background.push({ node, inert: node.hasAttribute('inert'), aria: node.getAttribute('aria-hidden') });
      node.setAttribute('inert', ''); node.setAttribute('aria-hidden', 'true');
    }
    syncTelegramChrome(); return true;
  }
  function maybeShowUpdateNotice() {
    // The asynchronous SDK must identify its OWN local profile first. Never
    // read an anonymous/browser acknowledgement on behalf of a Telegram ID.
    if (!UPDATES_READY || UPDATES_OPEN || S || PROFILE_PREFIX === null
      || TELEGRAM_PROFILE && telegramProfileId() !== PROFILE_ID
      || UPDATES_SHOWN.has(`${PROFILE_PREFIX}:${UPDATES_BUILD}`)) return;
    const progress = readUpdateProgress();
    if (!progress.readFailed && partitionUpdates(progress.seenBuild, progress.seenRelease).unread.length === 0) return;
    openUpdateNotice(true, progress.readFailed, null, progress);
  }
  function closeUpdateNotice() {
    if (!UPDATES_OPEN) return;
    const session = UPDATES_SESSION;
    try {
      if (session.prefix === null || session.prefix !== PROFILE_PREFIX) throw new Error('Release notice profile unavailable');
      profileStorage.setItem(UPDATES_KEY, UPDATES_BUILD);
      if (UPDATES_LATEST_RELEASE > 0) profileStorage.setItem(UPDATES_RELEASE_KEY, String(UPDATES_LATEST_RELEASE));
      updateNoticeStatus('');
    } catch (ignored) {
      updateNoticeStatus('Не удалось запомнить прочтение. При следующем входе окно может появиться снова. Сохранение рейда это окно не меняет.');
    }
    UPDATES_OPEN = false; UPDATES_SESSION = null;
    const wrap = el('updates-modal'); wrap.classList.remove('show'); wrap.hidden = true;
    for (const { node, inert, aria } of session.background) {
      if (!inert) node.removeAttribute('inert');
      if (aria === null) node.removeAttribute('aria-hidden'); else node.setAttribute('aria-hidden', aria);
    }
    const previous = session.focus;
    if (previous?.isConnected && previous !== document.body && !previous.closest('[inert]')) previous.focus();
    else (pickerOpen ? el('picker-updates') : el('menu-updates') || el('menu-btn'))?.focus();
    syncTelegramChrome();
  }
  function bindReadOnlyDismiss(button, dismiss, session = () => button) {
    // Touch synthesizes click AFTER pointerup. If pointerup opened a new
    // portrait/late-SDK notice under the finger, that OLD gesture must not
    // immediately press its new close button. Keyboard/AT click (detail 0)
    // remains valid; a real pointer close must start on this button/session.
    let armed = null;
    button.addEventListener('pointerdown', event => {
      if (event.isPrimary !== false && !(event.button > 0)) armed = session();
    });
    button.addEventListener('pointercancel', () => { armed = null; });
    button.addEventListener('click', event => {
      const ownGesture = armed !== null && armed === session(); armed = null;
      if (event.detail > 0 && !ownGesture) return;
      dismiss();
    });
  }
  function initUpdateNotice() {
    UPDATES_CATALOG = collectUpdatesCatalog();
    UPDATES_LATEST_RELEASE = UPDATES_CATALOG[0]?.release || 0;
    for (const id of ['picker-updates', 'menu-updates']) el(id)?.addEventListener('click', event => openUpdateNotice(false, false, event.currentTarget));
    const close = el('updates-close'); if (close) bindReadOnlyDismiss(close, closeUpdateNotice, () => UPDATES_SESSION);
    el('updates-modal')?.addEventListener('keydown', event => {
      if (!UPDATES_OPEN) return;
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeUpdateNotice(); }
      if (event.key !== 'Tab') return;
      const controls = [...el('updates-modal').querySelectorAll('button:not(:disabled), [href], [tabindex="0"]')].filter(node => !node.hidden);
      const first = controls[0], last = controls[controls.length - 1], title = el('updates-title');
      if (controls.length && (document.activeElement === title || event.shiftKey && document.activeElement === first || !event.shiftKey && document.activeElement === last)) {
        event.preventDefault(); (event.shiftKey ? last : first).focus();
      }
    });
    // inert handles native focus/pointer input; also reject queued/synthetic
    // background activations without modifying gameplay or a class draft.
    for (const kind of ['click', 'pointerdown']) document.addEventListener(kind, event => {
      if (UPDATES_OPEN && !el('updates-modal').contains(event.target)) { event.preventDefault(); event.stopImmediatePropagation(); }
    }, true);
    document.addEventListener('focusin', event => {
      if (UPDATES_OPEN && !el('updates-modal').contains(event.target)) el('updates-title')?.focus();
    });
    UPDATES_READY = true; maybeShowUpdateNotice();
  }

  // --- init -----------------------------------------------------------------
  function init() {
    initRaidAppearance();
    initVisualPreferences();
    initMaterialAudio();
    initGameSettings();
    initHaptics();
    window.addEventListener?.('resize', finishOrbAnimations);
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') finishOrbAnimations(); });
    initTelegramPreview();
    readRunSlot(); readRunHistory();
    initInput(); initRetune(); initFinishedRunNavigation();
    window.addEventListener?.('pagehide', () => { closeGameSettings(false); closeRetune(false); cancelPointerGesture(); saveRun(); });
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') { closeGameSettings(false); closeRetune(false); cancelPointerGesture(); saveRun(); } });
    window.addEventListener?.('storage', event => {
      if (event.key === profileStorage.key(RUN_HISTORY_KEY) || event.key === null) { if (HISTORY_OPEN) renderRunHistory(); else readRunHistory(); }
      if (event.key !== profileStorage.key(RUN_SAVE_KEY) && event.key !== null) return;
      // Finished pages still own timers/pagehide callbacks: never adopt a new
      // tab's baseline and then overwrite it with our old terminal checkpoint.
      if (S) { if (!ownsRunSlot()) closeRetune(false); }
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
    const pickerResume = el('picker-resume'); if (pickerResume) pickerResume.addEventListener('click', resumeFromClassPicker);
    const picker = el('class-picker');
    if (picker) picker.addEventListener('keydown', event => {
      if (!pickerOpen || NEW_RUN_REQUEST) return;
      if (event.key === 'Escape') { event.preventDefault(); if (classEditor) closeClassEditor(); else resumeFromClassPicker(); }
      if (event.key !== 'Tab') return;
      const controls = [...picker.querySelectorAll('button:not(:disabled), select:not(:disabled), input:not(:disabled), summary, [tabindex="0"]')].filter(node => node.getClientRects().length);
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
      const openBeginnerGuide = () => { qs('#beginner-guide')?.classList.add('show'); qs('#guide-close')?.focus(); };
      qs('#picker-guide')?.addEventListener('click', openBeginnerGuide);
      const mhelp = qs('#menu-help'); if (mhelp) mhelp.addEventListener('click', () => {
        hide();
        const wrap = qs('#beginner-guide');
        if (wrap) { wrap.classList.add('show'); qs('#guide-close')?.focus(); }
      });
      const guideClose = qs('#guide-close');
      guideClose?.addEventListener('click', () => { qs('#beginner-guide')?.classList.remove('show'); qs('#menu-help')?.focus(); });
      qs('#guide-tech-help')?.addEventListener('click', () => {
        qs('#beginner-guide')?.classList.remove('show');
        qs('#help-modal')?.classList.add('show'); qs('#help-close')?.focus();
      });
    }
    const statsBtn = qs('#stats-btn');
    if (statsBtn) statsBtn.addEventListener('click', () => {
      if (S && !S.resolving && !S.pendingModal && !S.over && !SAVE_CONFLICT) openModal('stats');
    });
    const helpClose = qs('#help-close');
    if (helpClose) helpClose.addEventListener('click', () => {
      const wrap = qs('#help-modal');
      if (wrap) wrap.classList.remove('show');
      el('menu-btn')?.focus();
    });
    showClassPicker();
    initUpdateNotice();
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
    clearChain,
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
    raidModuleIcon,
    raidEmptyModuleIcon,
    skillIconIds: () => Object.keys(RAID_SKILL_ICONS),
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
