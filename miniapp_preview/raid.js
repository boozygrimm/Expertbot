// miniapp_preview/raid.js
// Пробник Рейда на классических механиках (Fireflame Games, Dungeon Raid).
// Свой код, своя графика, своя стилистика. Запускать через lunapark-raid.html.
'use strict';
(function () {
  const COLS = 6, ROWS = 6, CELL_COUNT = COLS * ROWS;
  const START_SKULLS = [3, 4];
  const START_HP = 60, START_MAX_ARMOR = 15;
  const HP_PER_SHIELD_ARMOR = 2;   // 2 пункта броню за щит (+ defense)
  const HEAL_PER_POTION = 6;       // базовое лечение за зелье (+ luck)
  const GOLD_PER_COIN = 2;
  const XP_PER_SKULL = 1, XP_PER_BOSS = 6;
  const SPAWN_EVERY_TURNS = 3, SPAWN_COUNT = [2, 3];
  const BOSS_EVERY_TURNS = 10;
  const UPGRADE_BAR_MAX = 80;     // синяя полоса (от щитов) → выбор 1-из-4
  const GOLD_BOX_MAX = 100;       // золотой ящик → 1-из-4 снаряжение
  const TEMPER = {
    skullBaseHp: 8, skullBaseDef: 3, skullBaseAtk: 3,
    skullHpPerTurn: 0.55, skullDefPerTurn: 0.3, skullAtkPerTurn: 0.4,
    bossHpExtra: 34, bossDefExtra: 9, bossAtkExtra: 7,
  };

  const SKILLS = [
    { id: 'teleport', name: 'Телепорт', icon: '✦', cooldown: 12, desc: 'Все клетки без черепов → новые' },
    { id: 'freeze', name: 'Мороз', icon: '❄', cooldown: 10, desc: 'Черепа не бьют этим ходом' },
    { id: 'heal', name: 'Благословение', icon: '❤', cooldown: 12, desc: 'Собрать все зелья' },
    { id: 'repair', name: 'Ремонт', icon: '⚒', cooldown: 12, desc: 'Собрать все щиты' },
    { id: 'dazzle', name: 'Слепота', icon: '✦', cooldown: 10, desc: 'Все монеты → мечи' },
    { id: 'disarm', name: 'Оружие', icon: '⚔', cooldown: 10, desc: 'Все щиты → мечи' },
    { id: 'treasure', name: 'Кладовая', icon: '💰', cooldown: 14, desc: 'Все не-череп клетки → монеты' },
    { id: 'enchant', name: 'Затачивание', icon: '⚒', cooldown: 15, desc: 'Мгновенно: +2 к оружию, +4 к HP' },
    { id: 'bribe', name: 'Воровство', icon: '🦊', cooldown: 12, desc: '+12 золота вперёд' },
    { id: 'elixir', name: 'Эликсир', icon: '⚗', cooldown: 14, desc: 'Все зелья дают 2 XP вместо HP' },
  ];
  const STATS = [
    { id: 'strength', name: 'Сила', icon: '💪', desc: '+1 к урону мечей', apply: () => S.hero.stats.strength++ },
    { id: 'vitality', name: 'Живучесть', icon: '❤', desc: '+8 макс. HP', apply: () => { S.hero.stats.vitality++; S.hero.hp += 8; S.hero.maxHp += 8; } },
    { id: 'luck', name: 'Удача', icon: '🍀', desc: '+2 к лечению зелий', apply: () => S.hero.stats.luck++ },
    { id: 'dexterity', name: 'Точность', icon: '🎯', desc: '+1 к броне за щит', apply: () => S.hero.stats.dexterity++ },
    { id: 'charisma', name: 'Хитрость', icon: '💎', desc: '+2% монета и щита', apply: () => S.hero.stats.charisma++ },
    { id: 'damage', name: 'Урон', icon: '⚔', desc: '+1 базовый урон', apply: () => S.hero.stats.damage++ },
    { id: 'defense', name: 'Защита', icon: '🛡', desc: '+2 к броне', apply: () => { S.hero.stats.defense++; S.hero.maxArmor = Math.min(60, S.hero.maxArmor + 2); } },
  ];
  const UPGRADES = [
    { id: 'pierce', name: 'Пронзание', icon: '🛡', desc: '+25% урона по броне', apply: () => { S.pierce *= 1.25; } },
    { id: 'armor', name: 'Бронированное кольцо', icon: '🛡', desc: '+3 к макс. броне', apply: () => { S.hero.maxArmor += 3; S.hero.armor += 3; } },
    { id: 'base', name: 'Ускорение', icon: '⚡', desc: '+1 к базовому урону', apply: () => { S.hero.stats.damage++; } },
    { id: 'blunt', name: 'Сбивание', icon: '🚫', desc: '-1 к броне черепов', apply: () => { S.blunt += 1; } },
    { id: 'regen', name: 'Регенерация', icon: '♻', desc: '+2 HP в конце хода', apply: () => { S.regen += 2; } },
    { id: 'leech', name: 'Сок', icon: '🩸', desc: '+1 HP за убийство', apply: () => { S.leech += 1; } },
  ];
  const EQUIP = [
    { id: 'weapon', name: 'Оружие', icon: '⚔', desc: '+3 к урону оружия', apply: () => S.hero.weaponDamage += 3 },
    { id: 'hp', name: 'Жизнь', icon: '❤', desc: '+10 макс. HP', apply: () => { S.hero.maxHp += 10; S.hero.hp += 10; } },
    { id: 'armor', name: 'Броня', icon: '🛡', desc: '+6 макс. броне', apply: () => { S.hero.maxArmor += 6; S.hero.armor += 6; } },
    { id: 'pierce', name: 'Пронза', icon: '⚒', desc: '+30% урона по броне', apply: () => { S.pierce *= 1.3; } },
  ];

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
      skills: [],                  // active: {id, equipped:true}
      skillCooldowns: {},         // id -> turns remaining
      pierce: 1, regen: 0, blunt: 0, leech: 0, freezeNextTurn: 0, stealActive: 0,
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
    };
  }

  function randomTileType() {
    const r = Math.random();
    if (r < 0.26) return 'sword';
    if (r < 0.52) return 'shield';
    if (r < 0.76) return 'coin';
    return 'potion';
  }

  function makeSkull(level, boss) {
    const lv = level || 1;
    const scale = Math.max(1, lv);
    return {
      boss: !!boss,
      atk: Math.round((3 + 0.4 * Math.max(0, S.turn - 3)) * (boss ? 2.6 : 1)),
      def: Math.round((3 + 0.35 * Math.max(0, S.turn - 3)) * (boss ? 2 : 1)),
      hp: Math.round((8 + 0.55 * Math.max(0, S.turn - 3)) * (boss ? 3 : 1)),
      maxHp: 0,
    };
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
    const TYPES = ['sword', 'shield', 'coin', 'potion'];
    for (let r = 0; r < ROWS; r++) {
      for (let c = 0; c < COLS; c++) {
        for (const t of TYPES) {
          // 3-in-a-row in this direction is a legal chain, including sword+skull mixes
          // (we just need at least one legal chain somewhere)
          const dir = [[0, 1], [1, 0], [1, 1], [1, -1]];
          for (const [dr, dc] of dir) {
            const cells = [r * COLS + c];
            let cur = { r, c };
            for (let k = 0; k < 2; k++) {
              const nr = cur.r + dr, nc = cur.c + dc;
              if (nr < 0 || nr >= ROWS || nc < 0 || nc >= COLS) break;
              const nt = board[nr * COLS + nc] && board[nr * COLS + nc].type;
              if (!nt || nt === 'empty') break;
              const tp = board[cur.r * COLS + cur.c] && board[cur.r * COLS + cur.c].type;
              const ok = (tp === 'sword' && (nt === 'sword' || nt === 'skull')) || nt === tp;
              if (!ok) break;
              cells.push(nr * COLS + nc);
              cur = { r: nr, c: nc };
            }
            if (cells.length >= 3) return true;
          }
        }
      }
    }
    return false;
  }

  function setupBoard() {
    let cells = null;
    for (let tries = 0; tries < 30; tries++) {
      cells = new Array(CELL_COUNT).fill(null).map(() => ({ type: randomTileType() }));
      const count = Math.round(START_SKULLS[0] + Math.random() * (START_SKULLS[1] - START_SKULLS[0]));
      // place skulls on random cells
      const positions = shuffle([...Array(CELL_COUNT).keys()]).slice(0, count);
      positions.forEach((pos, i) => {
        const skull = makeSkull(1 + Math.floor(i / 2), false);
        skull.maxHp = skull.hp;
        cells[pos] = { type: 'skull', skull };
      });
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

  function createNewTile(row, col) {
    // new tiles appear at the top; only non-skull types
    return { type: randomTileType() };
  }

  function placeNewTiles() {
    // "gravity": each column: compact existing cells to the bottom, push new tiles in the top
    for (let c = 0; c < COLS; c++) {
      let writeRow = ROWS - 1;
      // collect existing non-empty cell indices from bottom to top
      for (let r = ROWS - 1; r >= 0; r--) {
        const idx = r * COLS + c;
        const cell = S.board[idx];
        if (cell.type !== 'empty') {
          if (writeRow !== r) { S.board[writeRow * COLS + c] = cell; }
          writeRow--;
        }
      }
      // new cells at top = nothing (writeRow already decremented accordingly; top indices stay empty)
      for (let r = 0; r < writeRow + 1; r++) {
        S.board[r * COLS + c] = createNewTile();
      }
    }
  }

  // --- chain rules ----------------------------------------------------------
  function chainAllows(cellType, chainType) {
    if (chainType === 'sword') return cellType === 'sword' || cellType === 'skull';
    return cellType === chainType;
  }

  function cellTypeAt(i) {
    return S.board[i].type;
  }

  function getSelIndexAfter(cells) {
    // none
  }

  function tryExtendChain(cellIdx) {
    if (S.resolving || S.over) return;
    if (cellIdx < 0 || cellIdx >= CELL_COUNT) return;
    const cell = S.board[cellIdx];
    if (!cell || cell.type === 'empty') return;

    let chain = S.selected;
    if (chain.length === 0) {
      // любая клетка начинает цепочку; череп = категория меча (меч+череп
      // идут в одну цепочку, начинаясь с любого из них)
      chain = [cellIdx];
      S.selected = chain;
      S.chainType = cell.type === 'skull' ? 'sword' : cell.type;
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

    if (chain.includes(cellIdx)) return;

    const cellType = S.board[cellIdx].type;
    if (!chainAllows(cellType, S.chainType)) return;
    if (!isAdjacent(last, cellIdx)) return;

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
    if (cell.type === 'shield') return '<svg viewBox="0 0 24 24" class="ic" aria-hidden="true"><path d="M12 2 20 5.5V12c0 4.6-3.2 8.4-8 10-4.8-1.6-8-5.4-8-10V5.5L12 2Z" fill="currentColor"/><path d="M12 5 17.5 7.2V12c0 3.3-2.3 6.1-5.5 7.5-3.2-1.4-5.5-4.2-5.5-7.5V7.2L12 5Z" fill="#1c1428" opacity="0.35"/></svg>';
    if (cell.type === 'coin') return '<svg viewBox="0 0 24 24" class="ic" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="currentColor"/><circle cx="12" cy="12" r="6.5" fill="none" stroke="#1c1428" stroke-width="1.4" opacity="0.45"/><text x="12" y="16" text-anchor="middle" font-size="9" font-weight="900" fill="#1c1428" opacity="0.55">¤</text></svg>';
    if (cell.type === 'potion') return '<svg viewBox="0 0 24 24" class="ic" aria-hidden="true"><path d="M12 2.5 10 6 8.5 9.5C7.6 11.6 6.8 13 6.8 14.8A5.2 5.2 0 0 0 12 20 5.2 5.2 0 0 0 17.2 14.8C17.2 13 16.4 11.6 15.5 9.5L14 6Z" fill="currentColor"/><path d="M12 2.5 12 6" stroke="#1c1428" stroke-width="1.6"/><circle cx="12" cy="14.6" r="2.4" fill="#fff" opacity="0.5"/></svg>';
    if (cell.type === 'skull') return '<svg viewBox="0 0 24 24" class="ic" aria-hidden="true"><path d="M12 3C7 3 4 6.5 4 10.5c0 2 1 3.7 2.2 4.6L6.5 18c0 1.4 1.1 2.5 2.5 2.5h2l1 2.2 1 2.2h3l1-2.2 1-2.2h2c1.4 0 2.5-1.1 2.5-2.5l-.7-2.9c1.2-.9 2.2-2.6 2.2-4.6C20 6.5 17 3 12 3Z" fill="currentColor"/><circle cx="9" cy="11" r="2.2" fill="#1c1428"/><circle cx="15" cy="11" r="2.2" fill="#1c1428"/><path d="M11 15h2l-1 2" fill="#1c1428"/></svg>';
    return '';
  }

  function renderCell(cell, idx) {
    const cellEl = qs(`.cell[data-idx="${idx}"]`);
    if (!cellEl) return;
    if (!cell || cell.type === 'empty') {
      cellEl.className = 'cell empty';
      cellEl.innerHTML = '';
      return;
    }
    const isSkull = cell.type === 'skull';
    const boss = isSkull && cell.skull.boss;
    cellEl.className = 'cell tile type-' + cell.type + (boss ? ' boss' : '');
    cellEl.innerHTML = tileSymbol(cell);
    if (isSkull) {
      const s = cell.skull;
      const badge = document.createElement('div');
      badge.className = 'skull-badge' + (boss ? ' boss' : '');
      badge.textContent = `${s.atk}·${s.def}·${s.hp}`;
      cellEl.appendChild(badge);
    }
  }

  function renderBoard() {
    const board = qs('#board');
    if (!board) return;
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
      renderCell(S.board[i], i);
    }
    renderSelection();
    renderChainLine();
  }

  function renderSelection() {
    qsa('.cell').forEach(c => c.classList.remove('selected'));
    S.selected.forEach(idx => {
      const cellEl = qs(`.cell[data-idx="${idx}"]`);
      if (cellEl) cellEl.classList.add('selected');
    });
  }

  function renderChainLine() {
    const svg = qs('#chain-line');
    if (!svg) return;
    svg.innerHTML = '';
    if (S.selected.length < 2) { svg.innerHTML = ''; return; }
    const pts = S.selected.map(cellCenter);
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    const d = pts.map((p, i) => (i === 0 ? 'M' : 'L') + p.x.toFixed(1) + ' ' + p.y.toFixed(1)).join(' ');
    path.setAttribute('d', d);
    path.setAttribute('class', 'line');
    svg.appendChild(path);
    // glow under layer
    const glow = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    glow.setAttribute('d', d);
    glow.setAttribute('class', 'line-glow');
    svg.insertBefore(glow, path);
  }

  // --- HUD ------------------------------------------------------------------
  function fmt(n) { return String(Math.max(0, Math.round(n))); }

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
    if (el('dmg-text')) el('dmg-text').textContent = `⚔ ${fmt(1 + h.stats.strength + h.stats.damage)} + ${fmt(h.weaponDamage)}×меч`;
    if (el('turn-text')) el('turn-text').textContent = `ХОД ${S.turn}`;
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
    S.skills.forEach(skillId => {
      const skill = SKILLS.find(s => s.id === skillId);
      if (!skill) return;
      const left = S.skillCooldowns[skillId] || 0;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'skill' + (left > 0 ? ' on-cd' : '');
      btn.innerHTML = `<span class="skill-icon">${skill.icon}</span><span class="skill-name">${skill.name}</span>${left > 0 ? `<span class="cd">${left}</span>` : ''}`;
      btn.title = skill.desc + ' (охлаждение: ' + skill.cooldown + ')';
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
    setupBoard();
    renderBoard();
    renderHUD();
    log('✦ Новый забег: 3–4 черепа на поле');
  }

  function isSelectable(cellType) { return cellType !== 'empty'; }

  function activateSkill(id) {
    if (S.over || S.resolving) return;
    const left = S.skillCooldowns[id] || 0;
    if (left > 0) return;
    const skill = SKILLS.find(s => s.id === id);
    if (!skill) return;
    S.skillCooldowns[id] = skill.cooldown;
    applySkill(id);
    renderHUD();
    renderBoard();
  }

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
          total += HEAL_PER_POTION + (S.hero.stats.luck || 0) * 2;
          S.board[i] = { type: randomTileType() };
        }
      }
      heal(total);
      flash('❤ Все зелья собраны (+' + total + ' HP)');
    } else if (id === 'repair') {
      let total = 0;
      for (let i = 0; i < CELL_COUNT; i++) {
        if (S.board[i].type === 'shield') {
          total += HP_PER_SHIELD_ARMOR + (S.hero.stats.dexterity || 0);
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
      for (let i = 0; i < CELL_COUNT; i++) {
        const cell = S.board[i];
        if (cell.type !== 'skull') S.board[i] = { type: 'coin' };
      }
      flash('💰 Все клетки → монеты');
    } else if (id === 'enchant') {
      S.hero.weaponDamage += 2;
      S.hero.hp = Math.min(S.hero.maxHp, S.hero.hp + 4);
      flash('⚒ Мгновенный улучшение: +2 оружия');
    } else if (id === 'bribe') {
      S.hero.gold += 12;
      S.goldEarned += 12;
      log('🦊 Воровство: +12 золота');
    } else if (id === 'elixir') {
      let count = 0;
      for (let i = 0; i < CELL_COUNT; i++) {
        if (S.board[i].type === 'potion') { count++; S.board[i] = { type: randomTileType() }; }
      }
      gainXp(count * 2);
      flash('⚗ Все зелья → XP (+' + (count * 2) + ')');
    }
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

  // --- resolve chain --------------------------------------------------------
  function resolveChain() {
    if (S.resolving || S.over) return;
    const chain = S.selected.slice();
    if (!chain || chain.length < 3) { clearChain(); return; }
    S.resolving = true;
    S.chainCount++;
    S.selected = [];
    S.chainType = null;
    renderChainLine();

    // categorize
    let swordCount = 0, shieldCount = 0, coinCount = 0, potionCount = 0;
    const skullsInChain = [];
    chain.forEach(idx => {
      const cell = S.board[idx];
      if (!cell || cell.type === 'empty') return;
      if (cell.type === 'sword') swordCount++;
      else if (cell.type === 'shield') shieldCount++;
      else if (cell.type === 'coin') coinCount++;
      else if (cell.type === 'potion') potionCount++;
      else if (cell.type === 'skull') skullsInChain.push(idx);
    });

    // clear chain cells (during fade)
    chain.forEach(idx => {
      const cellEl = qs(`.cell[data-idx="${idx}"]`);
      if (cellEl) cellEl.classList.add('pop');
    });

    setTimeout(() => {
      // apply effects
      let xpGained = 0, goldGained = 0, armorGained = 0, healGained = 0;

      // swords → skull damage (classic: база один раз + оружие за каждый меч)
      if (skullsInChain.length > 0) {
        let dmgShown = 0;
        const baseDmg = 1 + S.hero.stats.strength + S.hero.stats.damage;
        const totalDamage = Math.round(baseDmg + S.hero.weaponDamage * swordCount);
        // damage: distribute to skulls in chain (all skulls take the full sword damage)
        skullsInChain.forEach(idx => {
          const cell = S.board[idx];
          if (!cell || cell.type !== 'skull') return;
          const s = cell.skull;
          const armorHit = Math.round(totalDamage * S.pierce);
          dmgShown += armorHit;
          const dmgToDef = Math.min(armorHit, s.def);
          s.def -= dmgToDef;
          const dmgToHp = armorHit - dmgToDef;
          s.hp -= dmgToHp;
          S.swordsHits += armorHit;
          if (s.hp <= 0) {
            S.board[idx] = { type: 'empty' };
            S.skullsKilled++;
            const isBoss = s.boss;
            const xp = isBoss ? XP_PER_BOSS : XP_PER_SKULL;
            xpGained += xp;
            if (S.stealActive > 0) goldGained += 4;
            audioKill();
          }
        });
        if (dmgShown > 0) floatOn('💥 ' + dmgShown, 'hurt');
      }

      // shields
      if (shieldCount > 0) {
        const defenseStat = S.hero.stats.defense * 2;
        const dexterityStat = S.hero.stats.dexterity;
        const gain = shieldCount * (HP_PER_SHIELD_ARMOR + defenseStat) + dexterityStat * 2;
        const before = S.hero.armor;
        S.hero.armor = Math.min(S.hero.maxArmor, S.hero.armor + gain);
        armorGained = S.hero.armor - before;
        // overflow to upgrade bar
        const overflow = Math.max(0, gain - (S.hero.maxArmor - before));
        if (overflow > 0) addUpgradeBar(overflow * 2);
        for (let i = 0; i < shieldCount; i++) audioArmor();
      }

      // coins
      if (coinCount > 0) {
        const charisma = S.hero.stats.charisma;
        const gold = Math.round(coinCount * GOLD_PER_COIN * (1 + 0.05 * charisma));
        gainGold(gold);
        goldGained += gold;
        audioGold();
      }

      // potions
      if (potionCount > 0) {
        const luck = S.hero.stats.luck;
        const charisma = S.hero.stats.charisma;
        const gain = Math.round(potionCount * (HEAL_PER_POTION + luck * 2) * (1 + 0.05 * charisma));
        const before = S.hero.hp;
        heal(gain);
        healGained = S.hero.hp - before;
        if (healGained > 0) audioHeal();
      }

      // clear chain cells (sword/shield/coin/potion); dead skulls already removed
      const chainSet = new Set(chain);
      S.board = S.board.map((cell, i) => {
        if (!chainSet.has(i) || !cell) return cell;
        if (cell.type === 'skull') return cell; // skulls stay if alive
        return { type: 'empty' };
      });

      // xp
      if (xpGained > 0) {
        gainXp(xpGained);
        if (S.leech > 0) heal(S.leech * Math.min(1, xpGained));
        log(`⚔ ${xpGained} XP за ${S.skullsKilled} черепа`);
      }

      // mark skulls chain cells that remain (non-dead) — keep tiles on the board; dead skulls become empty
      // (skull cells that survived should stay as-is — they were in chain; but they were not cleared
      //  unless dead. If still alive, we should keep them. But wait — they were "used" by the chain,
      //  but remain. Actually in the classic, swords hit skulls; if they survive, they stay on the board.
      //  For simplicity, in our prototype, skulls in chain that survive stay — they are NOT removed.

      // refill: gravity for empty cells
      placeNewTiles();

      renderBoard();
      renderHUD();

      // enemy phase
      setTimeout(() => {
        enemyPhase();
      }, 550);
    }, 420);
  }

  function enemyPhase() {
    // skulls that were on board at START of this enemy phase (not killed this turn)
    const living = S.board.filter(c => c && c.type === 'skull');
    living.forEach(cell => {
      const s = cell.skull;
      const atk = Math.max(0, s.atk - S.blunt);
      damageHero(atk, s.boss);
    });

    // regenerate
    if (S.regen > 0) heal(S.regen);

    // spawn new skulls (every 3 turns +2-3; boss every 10 turns)
    S.turn++;
    if (S.turn % SPAWN_EVERY_TURNS === 0) {
      const count = randInt(SPAWN_COUNT[0], SPAWN_COUNT[1]);
      spawnSkulls(count, false);
    }
    if (S.turn % BOSS_EVERY_TURNS === 0) {
      spawnBoss();
    }
    // clear active skill cooldowns
    for (const id of Object.keys(S.skillCooldowns)) {
      S.skillCooldowns[id] = Math.max(0, S.skillCooldowns[id] - 1);
    }
    if (S.freezeNextTurn > 0) S.freezeNextTurn = 0;
    if (S.stealActive > 0) S.stealActive--;

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
    S.resolving = false;
  }

  function damageHero(amount, isBoss) {
    if (amount <= 0) return;
    if (S.freezeNextTurn > 0) {
      return;
    }
    const armorHit = Math.min(S.hero.armor, amount);
    S.hero.armor -= armorHit;
    const overflow = amount - armorHit;
    if (overflow > 0) {
      S.hero.hp -= overflow;
      floatOn('-' + Math.round(overflow), 'hurt');
    }
    audioEnemy();
  }

  function spawnSkulls(count, boss) {
    let placed = 0;
    const positions = shuffle([...Array(CELL_COUNT).keys()]);
    for (const pos of positions) {
      if (placed >= count) break;
      const cell = S.board[pos];
      if (!cell || cell.type === 'skull') continue;
      const skull = makeSkull(1 + Math.floor(S.turn / 10), boss);
      skull.maxHp = skull.hp;
      S.board[pos] = { type: 'skull', skull };
      placed++;
    }
    if (placed > 0) {
      log(`☠ Появились новые черепа (+${placed})`);
    }
  }

  function spawnBoss() {
    const positions = shuffle([...Array(CELL_COUNT).keys()]);
    for (const pos of positions) {
      const cell = S.board[pos];
      if (!cell || cell.type === 'skull') continue;
      const skull = makeSkull(2 + Math.floor(S.turn / 10), true);
      skull.maxHp = skull.hp;
      S.board[pos] = { type: 'skull', skull };
      audioBoss();
      flash('👹 КОНЕЧНЫЙ БОСС');
      return;
    }
  }

  // --- modals ---------------------------------------------------------------
  function openModal(kind) {
    S.pendingModal = kind;
    const wrap = el('modal');
    const title = el('modal-title');
    const body = el('modal-body');
    if (!wrap || !body) return;
    wrap.classList.add('show');

    if (kind === 'level') {
      title.textContent = '⬆ УРОВЕНЬ ' + S.hero.level + '!';
      body.innerHTML = '<p class="modal-hint">Выбери 2 из 4 улучшений:</p>';
      const options = chooseLevelOptions();
      renderLevelChoices(options, body);
    } else if (kind === 'gold') {
      title.textContent = '💰 100 золота!';
      body.innerHTML = '<p class="modal-hint">Выбери 1 из 4 снаряжения:</p>';
      renderEquipChoices(body);
    } else if (kind === 'armor') {
      title.textContent = '🛡 Новая броня!';
      body.innerHTML = '<p class="modal-hint">Выбери 1 из 4 улучшений:</p>';
      renderUpgradeChoices(body);
    } else if (kind === 'gameover') {
      title.textContent = '☠ ИГРА ОКОНЧЕНА';
      body.innerHTML = `<p class="modal-hint">Герой погиб на ходу <strong>${S.turn}</strong></p>
        <div class="stats-grid">
          <div><span>⚔</span><small>убито черепов</small><strong>${S.skullsKilled}</strong></div>
          <div><span>⬆</span><small>максимальный уровень</small><strong>${S.maxLevel}</strong></div>
          <div><span>💰</span><small>золота собрано</small><strong>${S.goldEarned}</strong></div>
          <div><span>🔗</span><small>цепочек взято</small><strong>${S.chainCount}</strong></div>
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
    setTimeout(() => { checkLevelUp(); checkGoldBox(); checkUpgradeBar(); }, 80);
  }

  function chooseLevelOptions() {
    // 2 stats + 2 skills (classic 2-of-4)
    const statOptions = shuffle([...STATS]).slice(0, 2);
    const skillOptions = shuffle([...SKILLS].filter(sk => !S.skills.includes(sk.id))).slice(0, 2);
    // if not enough skills, add stats
    while (statOptions.length + skillOptions.length < 4) {
      const extra = shuffle([...STATS].filter(s => !statOptions.includes(s)))[0];
      statOptions.push(extra);
    }
    return [...statOptions, ...skillOptions];
  }

  function renderLevelChoices(options, body) {
    const grid = document.createElement('div');
    grid.className = 'choice-grid';
    const picked = new Set();
    const choiceRow = document.createElement('div');
    choiceRow.className = 'level-status';
    choiceRow.textContent = 'Выбери 2 из 4 (0/2)';
    body.appendChild(choiceRow);

    function mark() {
      choiceRow.textContent = `Выбери 2 из 4 (${picked.size}/2)`;
      grid.querySelectorAll('.choice').forEach((node, i) => {
        node.classList.toggle('picked', picked.has(i));
      });
    }

    options.forEach((opt, i) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'choice';
      btn.innerHTML = `<span class="choice-icon">${opt.icon}</span><span class="choice-name">${opt.name}</span><span class="choice-desc">${opt.desc}</span>`;
      btn.addEventListener('click', () => {
        if (picked.has(i)) {
          picked.delete(i);
          mark();
          return;
        } else {
          picked.add(i);
          audioPick();
          if (picked.size === 2) {
            // apply now
            picked.forEach(idx => {
              const o = options[idx];
              if (STATS.includes(o)) o.apply();
              else if (SKILLS.includes(o)) {
                if (S.skills.length >= 4) return;
                S.skills.push(o.id);
              }
            });
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
    const options = shuffle([...EQUIP]).slice(0, 4);
    const grid = document.createElement('div');
    grid.className = 'choice-grid';
    options.forEach((opt, i) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'choice';
      btn.innerHTML = `<span class="choice-icon">${opt.icon}</span><span class="choice-name">${opt.name}</span><span class="choice-desc">${opt.desc}</span>`;
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
    body.appendChild(grid);
  }

  function renderUpgradeChoices(body) {
    const options = shuffle([...UPGRADES]).slice(0, 4);
    const grid = document.createElement('div');
    grid.className = 'choice-grid';
    options.forEach(opt => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'choice';
      btn.innerHTML = `<span class="choice-icon">${opt.icon}</span><span class="choice-name">${opt.name}</span><span class="choice-desc">${opt.desc}</span>`;
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
    newGame();
  }

  // expose for testing
  window.RAID = {
    get state() { return S; },
    newGame,
    tryExtendChain,
    resolveChain,
    activateSkill,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
