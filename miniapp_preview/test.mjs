import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

function browser() {
  const elements = new Map();
  let listener;
  const makeElement = () => ({
    hidden: false, textContent: '', children: [], attributes: {}, dataset: {}, style: {},
    classList: {toggle() {}, remove() {}, add() {}}, 
    setAttribute(key, value) { this.attributes[key] = value; },
    focus() {}, append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    showModal() { this.open = true; }, close() { this.open = false; },
  });
  const document = {
    getElementById(id) { if (!elements.has(id)) elements.set(id, makeElement()); return elements.get(id); },
    createElement() { return makeElement(); },
    querySelector() { return makeElement(); },
    querySelectorAll() { return []; },
    addEventListener(name, fn) { if (name === 'click') listener = fn; },
  };
  const context = vm.createContext({ document, window: { LUNAPARK_MODE: 'demo', scrollTo() {} } });
  vm.runInContext(readFileSync(new URL('./app.js', import.meta.url), 'utf8'), context);
  return { elements, click: (target) => listener({ target }) };
}
function target({ area, action, close } = {}) {
  return { closest(selector) {
    if (selector === '[data-area]' && area) return { dataset: { area } };
    if (selector === '[data-action]' && action) return { dataset: { action } };
    if (selector === '.dialog-close' && close) return {};
    return null;
  } };
}

test('park and lab navigation toggles screens', () => {
  const b = browser();
  b.click(target({ action: 'enter' }));
  assert.equal(b.elements.get('park-page').hidden, true);
  assert.equal(b.elements.get('lab-page').hidden, false);
  b.click(target({ action: 'park' }));
  assert.equal(b.elements.get('park-page').hidden, false);
  assert.equal(b.elements.get('lab-page').hidden, true);
});

test('sections show demo content and dialog closes', () => {
  const b = browser();
  for (const [area, heading] of [['inventory', 'Склад'], ['laboratory', 'Лаборатория'], ['expeditions', 'Экспедиции'], ['production', 'Производство']]) {
    b.click(target({ area }));
    assert.equal(b.elements.get('dialog-title').textContent, heading);
    assert.match(b.elements.get('dialog-kicker').textContent, /ПРИМЕР/);
    assert.ok(b.elements.get(area === 'inventory' ? 'inventory-grid' : 'dialog-content').children.length);
    assert.equal(b.elements.get('detail-dialog').open, true);
    b.click(target({ close: true }));
    assert.equal(b.elements.get('detail-dialog').open, false);
  }
});

test('about identifies disconnected demo, unknown area is ignored', () => {
  const b = browser();
  b.click(target({ action: 'about' }));
  assert.equal(b.elements.get('dialog-title').textContent, 'Это тестовый стенд');
  assert.match(b.elements.get('dialog-description').textContent, /искусственном/);
  b.click(target({ area: 'unknown' }));
  assert.equal(b.elements.get('dialog-title').textContent, 'Это тестовый стенд');
});

// Raid rules use the real source with a small, timer-controlled browser shell.
// No jsdom/backend is required, and assertions run before/after the actual
// asynchronous chain stages instead of silently treating resolveChain as sync.
function raidNode() {
  let html = '';
  return {
    children: [], className: '', style: {}, dataset: {}, textContent: '',
    classList: { add() {}, remove() {}, toggle() {} },
    get innerHTML() { return html; },
    set innerHTML(value) { html = value; this.children = []; },
    appendChild(child) { this.children.push(child); return child; },
    addEventListener(event, fn) { this['on' + event] = fn; },
    click() { this.onclick?.(); },
    querySelectorAll(selector) {
      const classes = (selector.match(/\.[\w-]+/g) || []).map(name => name.slice(1));
      const all = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
      return all.filter(node => classes.every(name => node.className.split(' ').includes(name)));
    },
  };
}
function raidBrowser({ classId = 'adventurer', mode = 'classic', difficulty = 1, seed = 71, menus = false } = {}) {
  const elements = new Map([
    ['mode-select', { value: mode }], ['difficulty-select', { value: String(difficulty) }],
    ['dmg-text', { textContent: '', title: '' }],
  ]);
  if (menus) for (const id of ['modal', 'modal-title', 'modal-body']) elements.set(id, raidNode());
  const storage = new Map();
  let now = 0, nextTimer = 1;
  const timers = new Map();
  const context = vm.createContext({
    window: {},
    document: {
      readyState: 'loading', addEventListener() {},
      getElementById: id => elements.get(id) || null,
      querySelector: selector => selector.startsWith('#') ? elements.get(selector.slice(1)) || null : null,
      querySelectorAll: () => [],
      createElement: () => raidNode(),
    },
    localStorage: {
      getItem: key => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, String(value)),
    },
    setTimeout(fn, delay = 0) {
      const id = nextTimer++;
      timers.set(id, { fn, at: now + delay });
      return id;
    },
    clearTimeout: id => timers.delete(id),
  });
  vm.runInContext(readFileSync(new URL('./raid.js', import.meta.url), 'utf8'), context);
  const raid = context.window.RAID;
  raid.unlockClass(classId);
  raid.setSelectedClassId(classId);
  raid.newGame(seed);
  return {
    raid, elements,
    advance(ms) {
      const end = now + ms;
      for (let n = 0; n < 10000; n++) {
        const due = [...timers].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at || a[0] - b[0]);
        if (!due.length) { now = end; return; }
        const [id, timer] = due[0];
        timers.delete(id);
        now = timer.at;
        timer.fn();
      }
      throw new Error('Raid timer loop did not settle');
    },
  };
}
function raidBoard(raid, type = 'shield') {
  raid.state.board = Array.from({ length: 36 }, () => ({ type }));
}
function raidSkull(raid, fields = {}, type = 0) {
  return Object.assign(raid.makeSkull(type === 1, type > 1, type), { atk: 0 }, fields);
}
function raidDraw(seed, low, high) {
  seed = Math.imul(seed, 16807) >>> 0;
  const value = Math.floor(Math.fround(Math.min(low, high) + Math.fround(Math.fround(Math.abs(high - low) + 1) * ((seed >>> 10) / 4194304))));
  return { seed, value };
}

test('raid build contains the current source and no unresolved template markers', () => {
  const source = readFileSync(new URL('./raid.js', import.meta.url), 'utf8');
  const html = readFileSync(new URL('../lunapark-raid.html', import.meta.url), 'utf8');
  assert.ok(html.includes(source.replaceAll('</', '<\\/')));
  assert.doesNotMatch(html, /\/\*__(?:SCRIPT|STYLE)__\*\//);
});

test('raid starts at B=3, W=2; boost doubles swords, not base; HUD names both values', () => {
  const { raid, elements } = raidBrowser();
  assert.equal(raid.state.hero.hp, 50);
  assert.equal(raid.state.hero.armor, 4);
  assert.equal(raid.baseDamage(), 3);
  assert.equal(raid.swordDamage(2), 7);
  assert.equal(elements.get('dmg-text').textContent, '⚔ 2/меч · 💥 3');
  raid.activateSkill('boostdmg');
  assert.equal(raid.swordDamage(2), 11);
  assert.equal(elements.get('dmg-text').textContent, '⚔ 4/меч · 💥 3');
});

test('raid refill draws one full-range tile type, not a second resource type', () => {
  const { raid } = raidBrowser();
  raid.state.turn = 0;
  let expected = raid.state.rngSeeds[1];
  const types = ['potion', 'skull', 'coin', 'shield', 'sword'];
  for (let i = 0; i < 100; i++) {
    const draw = raidDraw(expected, 0, 4);
    expected = draw.seed;
    const tile = raid.createNewTile();
    assert.equal(tile.type, types[draw.value]);
    // Stats have three draws plus the unresolved q=0 interval draw.
    if (tile.type === 'skull') for (let n = 0; n < 4; n++) expected = raidDraw(expected, 0, 0).seed;
    assert.equal(raid.state.rngSeeds[1], expected);
  }
  for (let i = 0; i < 200; i++) assert.notEqual(raid.createTile().type, 'skull');
});

test('raid mana adds one charge, excludes ready/Mana slots and does not heal', () => {
  const { raid } = raidBrowser({ classId: 'mage' });
  const s = raid.state;
  s.skills = ['fireball', 'heal', 'mana'];
  s.skillCooldowns = { fireball: 3, heal: 0, mana: 4 };
  s.hero.hp = 1;
  assert.equal(raid.refillRandomSkill(1), 1);
  assert.equal(s.skillCooldowns.fireball, 2);
  assert.equal(s.skillCooldowns.heal, 0);
  assert.equal(s.skillCooldowns.mana, 4);
  assert.equal(s.hero.hp, 1);
  s.skillCooldowns.fireball = 0;
  const before = s.rngSeeds[5];
  assert.equal(raid.refillRandomSkill(10), 0);
  assert.equal(s.rngSeeds[5], before, 'no target means no selection RNG call');
});

test('raid binds all mana targets before arrival instead of redirecting excess charges', () => {
  const { raid } = raidBrowser({ seed: 0 });
  raid.state.skills = ['heal', 'freeze'];
  raid.state.skillCooldowns = { heal: 1, freeze: 4 };
  assert.equal(raid.refillRandomSkill(3), 1);
  assert.equal(raid.state.skillCooldowns.heal, 0);
  assert.equal(raid.state.skillCooldowns.freeze, 4);
});

test('raid real mana chain reduces cooldowns without turning mana into HP', () => {
  const { raid, advance } = raidBrowser({ classId: 'mage' });
  raidBoard(raid);
  raid.state.hero.hp = 3;
  raid.state.skills = ['heal', 'mana'];
  raid.state.skillCooldowns = { heal: 5, mana: 8 };
  for (let i = 0; i < 3; i++) {
    raid.state.board[i] = { type: 'potion', mana: true };
    raid.tryExtendChain(i);
  }
  raid.resolveChain();
  advance(420);
  assert.equal(raid.state.hero.hp, 3);
  assert.equal(raid.state.skillCooldowns.heal, 2);
  assert.equal(raid.state.skillCooldowns.mana, 8);
});

test('raid enemy armour absorbs this hit, then each blocked point gets an erosion roll', () => {
  const { raid } = raidBrowser();
  const skull = raidSkull(raid, { hp: 20, def: 5 });
  let expected = raid.state.rngSeeds[5], breaks = 0;
  for (let n = 0; n < 3; n++) {
    const draw = raidDraw(expected, 0, 100);
    expected = draw.seed;
    if (draw.value < 50) breaks++;
  }
  assert.equal(raid.damageSkull(skull, 3, { direct: false }), 0);
  assert.equal(skull.hp, 20);
  assert.equal(skull.def, 5 - breaks);
  assert.equal(raid.state.rngSeeds[5], expected);
});

test('raid Orc stun applies only to surviving direct hits, cancels fire, and permits zero damage', () => {
  const { raid } = raidBrowser({ classId: 'barbarian', seed: 0 });
  const indirect = raidSkull(raid, { hp: 20, def: 5, burn: true, fireDamage: 4 });
  raid.damageSkull(indirect, 0, { direct: false });
  assert.ok(!indirect.stunned);
  assert.equal(indirect.burn, true);
  raid.damageSkull(indirect, 0, { direct: true });
  assert.equal(indirect.stunned, true);
  assert.equal(indirect.burn, false);
  assert.equal(indirect.fireDamage, 0);
  // A nonzero seed makes an unwanted random call observable even on a kill.
  raid.newGame(97);
  const lethal = raidSkull(raid, { hp: 1, def: 2 });
  const before = raid.state.rngSeeds[5];
  raid.damageSkull(lethal, 10, { direct: true });
  assert.equal(raid.state.rngSeeds[5], before);
  assert.ok(!lethal.stunned);
  assert.equal(lethal.def, 2);
});

test('raid Orc surviving direct hit uses precisely R(1,100,OTHER)<=10', () => {
  let stunned = 0;
  for (let seed = 1; seed <= 50; seed++) {
    const { raid } = raidBrowser({ classId: 'barbarian', seed });
    const skull = raidSkull(raid, { hp: 20, def: 0 });
    const draw = raidDraw(raid.state.rngSeeds[5], 1, 100);
    raid.damageSkull(skull, 0, { direct: true });
    assert.equal(!!skull.stunned, draw.value <= 10);
    assert.equal(raid.state.rngSeeds[5], draw.seed);
    if (skull.stunned) stunned++;
  }
  assert.ok(stunned > 0 && stunned < 50);
});

test('raid Changeling fully readies one slot on indirect special collection, not normal kills', () => {
  const { raid } = raidBrowser({ classId: 'mage', seed: 0 });
  raidBoard(raid);
  const s = raid.state;
  s.skills = ['heal']; s.skillCooldowns = { heal: 6 };
  s.board[0] = { type: 'skull', skull: raidSkull(raid, { hp: 1, def: 0 }, 2) };
  raid.activateSkill('explosivearmour');
  assert.equal(s.skillCooldowns.heal, 0);
  s.skillCooldowns.heal = 6;
  s.skillCooldowns.explosivearmour = 0;
  s.board[0] = { type: 'skull', skull: raidSkull(raid, { hp: 1, def: 0 }) };
  raid.activateSkill('explosivearmour');
  assert.equal(s.skillCooldowns.heal, 6);
});

test('raid Changeling may choose a ready slot rather than forcing a cooling-down one', () => {
  const { raid } = raidBrowser({ classId: 'mage', seed: 0 });
  raidBoard(raid);
  const s = raid.state;
  s.skills = ['heal', 'freeze']; s.skillCooldowns = { heal: 0, freeze: 6 };
  s.board[0] = { type: 'skull', skull: raidSkull(raid, { hp: 1, def: 0 }, 2) };
  raid.activateSkill('explosivearmour');
  assert.equal(s.skillCooldowns.freeze, 6);
});

for (const kills of [14, 15]) test(`raid Halfling ${kills} chain kills upgrade the armour item only at 15`, () => {
  const { raid, advance } = raidBrowser({ classId: 'raider' });
  raidBoard(raid);
  const s = raid.state;
  s.hero.armor = 1;
  const before = s.hero.maxArmor;
  const path = [0, 1, 2, 3, 4, 5, 11, 10, 9, 8, 7, 6, 12, 13, 14].slice(0, kills);
  for (const idx of path) {
    s.board[idx] = { type: 'skull', skull: raidSkull(raid, { hp: 1, def: 0 }) };
    raid.tryExtendChain(idx);
  }
  assert.equal(s.selected.length, kills);
  raid.resolveChain();
  advance(420);
  const increment = kills === 15 ? 1 : 0;
  assert.equal(s.skullsKilled, kills);
  assert.equal(s.equipment[2].slot, 'armour');
  assert.equal(s.equipment[2].mods.maxArmor || 0, increment);
  assert.equal(s.hero.maxArmor, before + increment);
  assert.equal(s.hero.armor, 1 + increment, 'equipment update must retain armour deficit');
});

test('raid Werewolf collects every ordinary HP<own-defence skull, including fresh ones, with XP', () => {
  const { raid } = raidBrowser({ classId: 'assassin' });
  raidBoard(raid);
  const s = raid.state;
  s.hero.armor = 0;
  const targets = [0, 1, 2].map(() => raidSkull(raid, { hp: 1, def: 2 }));
  targets.forEach((skull, i) => s.board[i] = { type: 'skull', skull });
  const immune = [raidSkull(raid, { hp: 2, def: 2 }), raidSkull(raid, { hp: 1, def: 9 }, 5), raidSkull(raid, { hp: 1, def: 9 }, 1)];
  immune.forEach((skull, i) => s.board[i + 3] = { type: 'skull', skull });
  raid.enemyPhase();
  const remaining = new Set(s.board.filter(c => c.type === 'skull').map(c => c.skull.uid));
  targets.forEach(skull => assert.ok(!remaining.has(skull.uid)));
  immune.forEach(skull => assert.ok(remaining.has(skull.uid)));
  assert.equal(s.hero.xpLevelCum, 6);
  assert.equal(s.skullsKilled, 0, 'collection is not a direct attack kill');
  assert.equal(s.specialsKilled, 0);
  assert.ok(s.board.every(c => c.type !== 'empty'));
  assert.ok(s.log.some(line => line.includes('ниже их защиты — 3')));
});

test('raid Kamikaze collects adjacent coins, preserves skulls, and does not refill mid-pass', () => {
  const { raid } = raidBrowser();
  raidBoard(raid, 'empty');
  const s = raid.state;
  s.hero.hp = 9;
  s.board[7] = { type: 'skull', skull: raidSkull(raid, {}, 10) };
  s.board[6] = { type: 'coin' };
  const neighbour = raidSkull(raid, { hp: 20 });
  s.board[8] = { type: 'skull', skull: neighbour };
  raid.explodeKamikazeCell(7, true);
  assert.equal(s.hero.hp, 4);
  assert.equal(s.hero.gold, 2);
  assert.equal(s.goldEarned, 2);
  assert.equal(s.board[7].type, 'empty');
  assert.equal(s.board[6].type, 'empty');
  assert.equal(s.board[8].skull, neighbour);
  assert.equal(neighbour.hp, 20);
});

test('raid Kamikaze overlap with selected resources pays once; explosive potion recursion terminates', () => {
  const { raid } = raidBrowser();
  raidBoard(raid, 'empty');
  const s = raid.state;
  s.hero.hp = 9;
  s.board[0] = { type: 'coin' };
  s.board[1] = { type: 'skull', skull: raidSkull(raid, { hp: 1, def: 0 }, 10) };
  s.board[2] = { type: 'sword' };
  s.board[6] = { type: 'potion', explosive: true };
  s.board[7] = { type: 'potion', explosive: true };
  s.board[12] = { type: 'coin' };
  // Area collection already scheduled this coin alongside the enemy.
  s._chainOrder = [0, 1, 2];
  const result = raid.applyChainEffects(1, 0, 1, 0, [1], false, new Set(), []);
  assert.equal(result.kills, 1);
  assert.equal(s.hero.gold, 4, 'one selected and one recursively collected coin');
  assert.equal(s.hero.hp, 9, 'killing Kamikaze and explosive potions do not heal or halve HP');
  assert.equal(s.board[6].type, 'empty');
  assert.equal(s.board[7].type, 'empty');
});

test('raid special abilities activate in column order (Overseer before Healer)', () => {
  const { raid } = raidBrowser();
  raidBoard(raid, 'coin');
  const s = raid.state;
  s.board[1] = { type: 'skull', skull: raidSkull(raid, {}, 4) };
  s.board[6] = { type: 'skull', skull: raidSkull(raid, {}, 11) };
  s.board[35] = { type: 'sword' };
  raid.applySpecialAbilities();
  assert.equal(s.board[35].type, 'skull');
  assert.equal(s.board[35].skull.hp, s.board[35].skull.maxHp);
  assert.ok(s.board[35].skull.hp > 1);
});

test('raid Unstable defers refill so a remaining old Kamikaze still acts, without collecting matched coins', () => {
  const { raid } = raidBrowser();
  raidBoard(raid, 'coin');
  const s = raid.state;
  s.hero.hp = 9;
  s.board[1] = { type: 'skull', skull: raidSkull(raid, { countdown: 1 }, 10) };
  s.board[6] = { type: 'skull', skull: raidSkull(raid, { countdown: 1 }, 19) };
  raid.applySpecialAbilities();
  assert.equal(s.hero.hp, 4);
  assert.equal(s.hero.gold, 0);
  assert.ok(s.board.every(c => c.type !== 'skull' && c.type !== 'empty'));
});

test('raid only Changeling Unstable refill excludes swords instead of skulls', () => {
  for (const classId of ['adventurer', 'mage']) {
    const { raid } = raidBrowser({ classId });
    raid.state.forceCoins = 1;
    for (let i = 0; i < 10; i++) {
      raid.unstableTeleport();
      const types = raid.state.board.map(c => c.type);
      if (classId === 'mage') {
        assert.ok(!types.includes('sword') && !types.includes('arrow'));
        assert.ok(types.includes('skull'));
      } else {
        assert.ok(!types.includes('skull'));
        assert.ok(types.includes('sword'));
      }
      assert.ok(types.some(type => type !== 'coin'), 'filter takes priority over Force Coin');
    }
  }
});

test('raid all 32 special stat multipliers clamp base defence before multiplying', () => {
  const { raid } = raidBrowser();
  const overrides = { 1: [3, 3, 6], 2: [2, 1, 3], 3: [0, 6, 6], 5: [2, 10, .5], 9: [1, 2, 4], 10: [1, 2, 6], 16: [3, 2, 3], 17: [2, 1, 4], 19: [4, 1, 5], 27: [2, 4, 3], 30: [.5, 1, 5] };
  raid.state.hero.armor = 0;
  for (let type = 1; type <= 32; type++) {
    const [a, d, h] = overrides[type] || [2, 2, 4];
    const skull = raid.makeSkull(type === 1, type > 1, type);
    assert.deepEqual([skull.atk, skull.def, skull.hp], [Math.floor(a), type === 17 ? 0 : d, Math.floor(4 * h)], `type ${type}`);
    assert.equal(skull.countdown, [3, 10, 19, 28].includes(type) ? 5 : 0);
  }
});

test('raid Noisy truncates float32 HP instead of rounding up', () => {
  const { raid } = raidBrowser({ classId: 'warrior' });
  assert.equal(raid.makeSkull(false, true, 2).hp, 12);
  assert.equal(raid.makeSkull(true).hp, 25);
});

test('raid natural promotion uses generated difficulty interval instead of a fixed 43 turns', () => {
  const { raid } = raidBrowser();
  const s = raid.state;
  s.turn = 100; s.promotionRemaining = 0;
  let promoted;
  for (let i = 0; i < 100; i++) {
    const tile = raid.createNewTile();
    if (tile.type === 'skull') { promoted = tile.skull; break; }
  }
  assert.ok(promoted && (promoted.special || promoted.boss));
  assert.ok(s.promotionRemaining >= 20 && s.promotionRemaining <= 30);
  assert.equal(s.promotionRemaining, promoted.promotionInterval);
});

test('raid Sprint promotes on its modulo schedule, forcing a skull, then resets to 200', () => {
  for (const [classId, due, notDue] of [['adventurer', 4, 3], ['paladin', 3, 4]]) {
    const { raid } = raidBrowser({ classId, mode: 'sprint' });
    const s = raid.state;
    assert.equal(s.promotionRemaining, 200);
    s.turn = notDue; s.promotionRemaining = 199; s.forceCoins = 1;
    assert.equal(raid.createNewTile().type, 'coin');
    s.turn = due;
    const tile = raid.createNewTile();
    assert.equal(tile.type, 'skull');
    assert.ok(tile.skull.special || tile.skull.boss);
    assert.equal(s.promotionRemaining, 200);
    assert.equal(raid.createNewTile().type, 'coin', 'only one promotion in the same pass');
  }
});


test('raid actual chain XP stays finite and contributes to score without injecting fixture-only fields', () => {
  const { raid, advance } = raidBrowser();
  raidBoard(raid);
  for (const idx of [0, 1, 2]) {
    raid.state.board[idx] = { type: 'skull', skull: raidSkull(raid, { hp: 1, def: 0 }) };
    raid.tryExtendChain(idx);
  }
  raid.resolveChain();
  advance(420);
  assert.equal(raid.state.hero.xp, 6);
  assert.equal(raid.state.hero.xpLevelCum, 6);
  assert.equal(raid.runScore(), 6);
  raid.newGame(12);
  assert.equal(raid.state.hero.xpLevelCum, 0);
  assert.equal(raid.runScore(), 0);
  advance(1000);
  assert.equal(raid.state.turn, 0, 'old enemy-phase callback must not affect the new run');
});

test('raid Faun level selection completes and truncates a fractional 25-percent health floor', () => {
  const { raid, elements } = raidBrowser({ classId: 'priest', menus: true });
  const s = raid.state;
  s.hero.maxHp = 51; s.hero.hp = 1; s.hero.xp = 100;
  raid.checkLevelUp();
  const cards = elements.get('modal-body').querySelectorAll('.lv-card.lv-skill');
  assert.equal(cards.length, 2);
  cards[0].click(); cards[1].click();
  assert.equal(s.hero.maxHp, 51);
  assert.equal(s.hero.hp, 12);
  assert.equal(s.hero.level, 2);
  assert.equal(s.pendingModal, null);
});

test('raid Cunning fourth-skill level selection completes and retains its cooldown adjustment', () => {
  const { raid, elements } = raidBrowser({ classId: 'assassin', menus: true });
  const s = raid.state;
  s.skills = ['heal', 'freeze', 'steal'];
  s.skillCooldowns = { heal: 5, freeze: 5, steal: 5 };
  s.hero.xp = 100;
  raid.checkLevelUp();
  const cards = elements.get('modal-body').querySelectorAll('.lv-card.lv-skill');
  assert.equal(cards.length, 2);
  cards[0].click(); cards[1].click();
  assert.equal(s.skills.length, 4);
  assert.equal(s.hero.level, 2);
  assert.equal(s.cooldownAdjust.haste, -1);
  assert.equal(s.pendingModal, null);
  raid.activateSkill('haste');
  assert.equal(s.skillCooldowns.haste, 15);
});
