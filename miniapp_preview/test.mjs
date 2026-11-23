import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
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
    setAttribute(name, value) { this[name] = String(value); },
    addEventListener(event, fn) { this['on' + event] = fn; },
    click() { this.onclick?.(); },
    querySelectorAll(selector) {
      const classes = (selector.match(/\.[\w-]+/g) || []).map(name => name.slice(1));
      const all = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
      return all.filter(node => classes.every(name => node.className.split(' ').includes(name)));
    },
  };
}
function raidBrowser({ classId = 'adventurer', mode = 'classic', difficulty = 1, seed = 71, menus = false, saved = {}, start = true, fault = {} } = {}) {
  const elements = new Map([
    ['mode-select', { value: mode }], ['difficulty-select', { value: String(difficulty) }],
    ['dmg-text', { textContent: '', title: '' }],
  ]);
  if (menus) for (const id of ['modal', 'modal-title', 'modal-body']) elements.set(id, raidNode());
  const storage = saved instanceof Map ? saved : new Map(Object.entries(saved));
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
      getItem: key => { if (typeof fault.read === 'function' ? fault.read(key) : fault.read) throw new Error('Storage blocked'); return storage.get(key) ?? null; },
      setItem: (key, value) => { if (typeof fault.write === 'function' ? fault.write(key) : fault.write) throw new Error('Quota exceeded'); storage.set(key, String(value)); },
      removeItem: key => { if (typeof fault.remove === 'function' ? fault.remove(key) : fault.remove) throw new Error('Storage blocked'); storage.delete(key); },
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
  if (start) {
    raid.unlockClass(classId);
    raid.setSelectedClassId(classId);
    raid.newGame(seed);
  }
  return {
    raid, elements, storage,
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
  assert.equal(s.equipment[2].mods.maxArmor, 2 + increment, 'starting armour already contains two points');
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
  // One selected and one recursively collected coin, PLUS the special's
  // native reward. Check the TILE draws independently from physical overlap.
  const count = raidDraw(s.rngSeeds[1], 15, 25);
  const coins = raidDraw(count.seed, Math.floor(3 * count.value / 4), Math.floor(count.value / 3));
  s._chainOrder = [0, 1, 2];
  const result = raid.applyChainEffects(1, 0, 1, 0, [1]);
  assert.equal(result.kills, 1);
  assert.equal(s.rngSeeds[1], coins.seed);
  assert.equal(s.hero.gold, 4 + 2 * coins.value, 'no repeated physical coin payout');
  assert.equal(s.hero.xpLevelCum, 2 * (count.value - coins.value));
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
  cards[0].click(); cards[1].click(); raidConfirmLevel(elements);
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
  cards[0].click(); cards[1].click(); raidConfirmLevel(elements);
  assert.equal(s.skills.length, 4);
  assert.equal(s.hero.level, 2);
  assert.equal(s.cooldownAdjust.haste, -1);
  assert.equal(s.pendingModal, null);
  raid.activateSkill('haste');
  assert.equal(s.skillCooldowns.haste, 15);
});

// Token arrival, special rewards, aggregate combat and Rune regressions.
function raidSelect(raid, path) {
  for (const idx of path) raid.tryExtendChain(idx);
  assert.deepEqual(Array.from(raid.state.selected), path, 'fixture must select a real legal path');
}
function raidOld(raid, slot, fields = {}, type = 0) {
  const skull = raidSkull(raid, { hp: 20, maxHp: 20, def: 0, enteredTurn: raid.state.turn - 1, ...fields }, type);
  raid.state.board[slot] = { type: 'skull', skull };
  return skull;
}
function raidConfirmLevel(elements) {
  const confirm = elements.get('modal-body').querySelectorAll('.lv-confirm')[0];
  assert.ok(confirm, 'an explicit confirmation must be present');
  assert.equal(confirm.disabled, false, 'exactly two picks enable confirmation');
  confirm.click();
}
function raidPickLevel(elements) {
  const cards = elements.get('modal-body').querySelectorAll('.lv-card');
  assert.equal(cards.length, 4);
  const skills = cards.filter(card => card.className.includes('lv-skill'));
  const picks = skills.length >= 2 ? skills : cards;
  picks[0].click(); picks[1].click(); raidConfirmLevel(elements);
}
function raidPickShop(elements) {
  const cards = elements.get('modal-body').querySelectorAll('.shop-item');
  assert.ok(cards.length >= 3);
  cards[0].click();
}

for (const method of ['chain', 'repair', 'blast']) test(`raid ${method}: each shield repairs OR adds UP, with discarded repair overflow`, () => {
  const { raid, advance } = raidBrowser({ seed: 0 });
  raidBoard(raid, 'sword');
  const s = raid.state;
  s.forceCoins = 1; s.hero.armor = 0; s.hero.maxArmor = 5; s.armorPerShield = 2;
  for (const i of [0, 1, 2]) s.board[i] = { type: 'shield' };
  if (method === 'chain') { raidSelect(raid, [0, 1, 2]); raid.resolveChain(); advance(420); }
  else if (method === 'repair') raid.activateSkill('repair');
  else { raidOld(raid, 7, {}, 10); raid.explodeKamikazeCell(7, true); }
  assert.equal(s.hero.armor, 5, 'two tokens repair 3 then 2; one excess repair point is lost');
  assert.equal(s.upgradeBar, 2, 'only the third token gives UP');
  assert.equal(s.upgradeEarned, 2);
});

test('raid extra shield tokens use the same per-token repair/UP decision', () => {
  const { raid } = raidBrowser({ seed: 0 });
  raidBoard(raid, 'sword');
  const s = raid.state;
  s.hero.armor = 0; s.hero.maxArmor = 5; s.armorPerShield = 2;
  for (const idx of [0, 1, 2, 3]) s.board[idx] = { type: 'shield' };
  raid.collectIndices([0, 1, 2, 3], { noRefill: true });
  assert.equal(s.hero.armor, 5);
  assert.equal(s.upgradeBar, 6, 'four physical shields + fourth bonus = five tokens, two repair and three UP');
});

test('raid Corrosive converts every shield token to UP even with missing armour', () => {
  const { raid } = raidBrowser();
  raidBoard(raid);
  raid.state.hero.armor = 0;
  raidOld(raid, 35, {}, 12);
  raid.addResource({ type: 'shield' });
  assert.equal(raid.state.hero.armor, 0);
  assert.equal(raid.state.upgradeBar, 2);
});

test('raid fractional XP/gold/UP carry independently across separate arrivals, not rounded batches', () => {
  const { raid } = raidBrowser();
  const s = raid.state;
  s.xpPerSkull = s.goldPerCoin = s.upgradePerShield = 2.25;
  for (const [type, getTotal, key] of [
    ['xp', () => s.hero.xp, 'xp'], ['coin', () => s.hero.gold, 'gold'], ['shield', () => s.upgradeBar, 'upgrade'],
  ]) {
    const totals = [];
    for (let n = 0; n < 4; n++) { raid.addResource({ type }); totals.push(getTotal()); }
    assert.deepEqual(totals, [2, 4, 6, 9], type);
    assert.equal(s.resourceCarry[key], 0);
  }
  s.goldPerCoin = 2.1;
  let carry = 0, expected = 9;
  for (let n = 0; n < 20; n++) {
    carry = Math.fround(carry + Math.fround(.1));
    const whole = Math.floor(carry); carry = Math.fround(carry - whole);
    expected += 2 + whole;
    raid.addResource({ type: 'coin' });
    assert.equal(s.hero.gold, expected);
    assert.equal(s.resourceCarry.gold, carry, 'carry must retain float32 precision');
  }
});

test('raid Sprint and boosts multiply resource points/carry, but never double repair or healing for Sprint', () => {
  const { raid } = raidBrowser({ mode: 'sprint' });
  const s = raid.state;
  s.coinBoost = s.shieldBoost = s.potionBoost = 1;
  s.xpPerSkull = s.goldPerCoin = s.upgradePerShield = 2.25;
  raid.addResource({ type: 'coin' }); raid.addResource({ type: 'shield' }); raid.addResource({ type: 'xp' });
  assert.equal(s.hero.gold, 9);
  assert.equal(s.upgradeBar, 9);
  assert.equal(s.hero.xp, 4);
  assert.equal(s.resourceCarry.xp, .5);
  s.hero.hp = 10; s.hero.armor = 0;
  raid.addResource({ type: 'potion' }); raid.addResource({ type: 'shield' });
  assert.equal(s.hero.hp, 14, 'base healing2 × boost2, not ×Sprint2');
  assert.equal(s.hero.armor, 4, 'base repair2 × boost2, not ×Sprint2');
  assert.equal(s.upgradeBar, 9, 'a repair token never also adds UP');
  raid.addResource({ type: 'potion', variant: 2 });
  assert.equal(s.hero.hp, 12, 'acid ignores healing boost and Sprint');
});

test('raid exceptionally large carry crosses a bar once, banks only100 and preserves modulo remainder', () => {
  const { raid } = raidBrowser({ menus: true });
  const s = raid.state;
  s.resourceCarry.xp = 250;
  raid.addResource({ type: 'xp' });
  assert.equal(s.hero.xp, 52);
  assert.equal(s.hero.xpLevelCum, 152);
  assert.equal(s.resourceCarry.xp, 0);
  assert.equal(s.hero.level, 2);
  assert.equal(s.pendingModal, 'level');
  assert.equal(s.rewardMenus.length, 0, 'no while-loop of multiple level menus');
});

test('raid reward menus pause subsequent arrivals and the enemy turn, and preserve unspent remainders', () => {
  const { raid, elements, advance } = raidBrowser({ menus: true, seed: 0 });
  raidBoard(raid, 'sword');
  const s = raid.state;
  s.forceCoins = 1; s.hero.hp = 10; s.hero.xp = 99; s.hero.xpLevelCum = 99; s.hero.gold = 99; s.goldEarned = 99;
  raidOld(raid, 0, { hp: 1 });
  s.board[1] = { type: 'chain' }; s.board[2] = { type: 'coin' };
  s.board[3] = { type: 'chain' }; s.board[4] = { type: 'potion' };
  raidSelect(raid, [0, 1, 2, 3, 4]); raid.resolveChain(); advance(420);
  assert.equal(s.pendingModal, 'level');
  assert.equal(s.hero.level, 2, 'level is gained at arrival, not on menu confirmation');
  assert.equal(s.hero.xp, 1);
  assert.equal(s.hero.xpLevelCum, 101);
  assert.equal(s.hero.gold, 99, 'the next resource has not arrived');
  assert.equal(s.hero.hp, 10);
  assert.equal(s.arrivals.length, 2);
  assert.equal(s.resolving, true);
  advance(2000);
  assert.equal(s.turn, 0, 'an open menu blocks enemy continuation regardless of time');
  raidPickLevel(elements); advance(80);
  assert.equal(s.pendingModal, 'gold');
  assert.equal(s.hero.gold, 1);
  assert.equal(s.goldEarned, 101);
  assert.equal(s.arrivals.length, 1);
  assert.equal(s.hero.hp, 10);
  raidPickShop(elements);
  const afterPurchaseHp = s.hero.hp;
  advance(80);
  assert.equal(s.pendingModal, null);
  assert.equal(s.hero.gold, 1, 'shop choice must not erase the remainder');
  assert.equal(s.hero.xp, 1, 'level choice must not erase the remainder');
  assert.equal(s.arrivals.length, 0);
  assert.ok(s.hero.hp >= afterPurchaseHp);
  assert.equal(s.turn, 0);
  advance(550);
  assert.equal(s.turn, 1, 'only one enemy continuation after both menus');
  assert.equal(s.resolving, false);
});

test('raid upgrade choice consumes only the crossing; free Enchant never spends saved UP', () => {
  const { raid, elements, advance } = raidBrowser({ menus: true });
  const s = raid.state;
  s.upgradeBar = 99; s.upgradeEarned = 99;
  raid.addResource({ type: 'shield' });
  assert.equal(s.pendingModal, 'armor');
  assert.equal(s.upgradeBar, 1);
  elements.get('modal-body').querySelectorAll('.lv-card')[0].click(); advance(80);
  assert.equal(s.upgradeBar, 1);
  s.upgradeBar = 17;
  raid.activateSkill('enchant');
  assert.equal(s.pendingModal, 'armor');
  elements.get('modal-body').querySelectorAll('.lv-card')[0].click(); advance(80);
  assert.equal(s.upgradeBar, 17);
  assert.equal(s.turn, 0, 'a collection/Enchant skill does not consume a turn');
  assert.equal(s.resolving, false);
});

test('raid restarting a run discards a menu-paused resource queue and its continuation', () => {
  const { raid, advance } = raidBrowser({ menus: true });
  raidBoard(raid, 'coin');
  raid.state.hero.gold = 99;
  raidSelect(raid, [0, 1, 2]); raid.resolveChain(); advance(420);
  assert.equal(raid.state.pendingModal, 'gold');
  assert.equal(raid.state.arrivals.length, 2);
  raid.newGame(88); advance(5000);
  assert.equal(raid.state.turn, 0);
  assert.equal(raid.state.hero.gold, 0);
  assert.equal(raid.state.arrivals.length, 0);
  assert.equal(raid.state.pendingModal, null);
  assert.equal(raid.state.resolving, false);
});

test('raid broken resources still count toward same-type bonuses but pay no own token', () => {
  const { raid } = raidBrowser({ seed: 0 });
  raidBoard(raid, 'sword');
  for (const idx of [0, 1, 2, 3]) raid.state.board[idx] = { type: 'shield', broken: idx === 0 };
  raid.collectIndices([0, 1, 2, 3], { noRefill: true });
  assert.equal(raid.state.collectionCounts.shield, 4);
  assert.equal(raid.state.upgradeBar, 8, 'three normal tokens plus a bonus for the fourth counted shield');
});

test('raid Daring bonus threshold uses each resource count, not total mixed collection length', () => {
  const { raid } = raidBrowser({ classId: 'raider', seed: 37 });
  raidBoard(raid, 'sword');
  const s = raid.state;
  s.hero.hp = 1;
  for (let i = 0; i < 10; i++) s.board[i] = { type: i % 2 ? 'coin' : 'potion' };
  const order = [0, 6, 1, 7, 2, 8, 3, 9, 4, 5]; // column order among these physical slots
  const counts = {}, expected = { coin: 0, potion: 0 };
  let other = s.rngSeeds[5];
  for (const idx of order) {
    const type = s.board[idx].type; counts[type] = (counts[type] || 0) + 1; expected[type]++;
    if (counts[type] >= 4) {
      const draw = raidDraw(other, 1, 100); other = draw.seed;
      if (draw.value <= 25) expected[type]++;
    }
  }
  raid.collectIndices(Array.from({ length: 10 }, (_, i) => i), { noRefill: true });
  assert.equal(s.hero.gold, expected.coin * 2);
  assert.equal(s.hero.hp, 1 + expected.potion * 2);
  assert.equal(s.rngSeeds[5], other);
  assert.ok(expected.coin + expected.potion < 14, 'fixture distinguishes ordinary chance from guaranteed Daring');
});

test('raid Daring guarantees the tenth same-type bonus while still making the native RNG draw', () => {
  const { raid } = raidBrowser({ classId: 'raider', seed: 83 });
  raidBoard(raid, 'sword');
  const s = raid.state;
  for (let i = 0; i < 10; i++) s.board[i] = { type: 'coin' };
  let other = s.rngSeeds[5], tokens = 10;
  for (let count = 4; count <= 10; count++) {
    const draw = raidDraw(other, 1, 100); other = draw.seed;
    if (count === 10 || draw.value <= 25) tokens++;
  }
  raid.collectIndices(Array.from({ length: 10 }, (_, i) => i), { noRefill: true });
  assert.equal(s.hero.gold, 2 * tokens);
  assert.equal(s.rngSeeds[5], other);
});

test('raid mana/acid potions skip ordinary bonus rolls but still advance the potion count', () => {
  const { raid } = raidBrowser({ seed: 0 });
  raidBoard(raid, 'sword');
  const s = raid.state;
  s.hero.hp = 10;
  for (let i = 0; i < 3; i++) s.board[i] = { type: 'potion', mana: true };
  s.board[3] = { type: 'potion' };
  raid.collectIndices([0, 1, 2, 3], { noRefill: true });
  assert.equal(s.hero.hp, 14, 'only the fourth normal potion pays, with its same-type bonus');
  assert.equal(s.collectionCounts.potion, 4);
  for (let i = 0; i < 4; i++) s.board[i] = { type: 'potion', acid: true };
  raid.collectIndices([0, 1, 2, 3], { noRefill: true });
  assert.equal(s.hero.hp, 6, 'four acid tokens, never an ordinary potion bonus');
});

for (const [type, gold, xp, healing, up] of [
  [1, 10, 20, 0, 0], [2, 10, 20, 0, 0], [3, 30, 0, 0, 0],
  [4, 6, 18, 6, 0], [13, 14, 16, 0, 0], [27, 6, 18, 0, 6], [32, 10, 20, 0, 0],
]) test(`raid special ${type} collection uses native ordered reward composition, not flat points`, () => {
  const { raid } = raidBrowser({ seed: 0 });
  raidBoard(raid, 'sword');
  const s = raid.state;
  s.hero.hp = 10;
  raidOld(raid, 14, {}, type);
  const result = raid.collectIndices([14], { includeSpecial: true, noRefill: true });
  assert.deepEqual([s.hero.gold, s.hero.xpLevelCum, s.hero.hp - 10, s.upgradeBar], [gold, xp, healing, up]);
  assert.equal(result.kills, 0, 'collection is not AttackSkull');
  assert.equal(s.skullsKilled, 0);
  assert.equal(s.specialsKilled, 1);
});

test('raid special reward type order and TILE ranges are reproducible for every species', () => {
  for (const seed of [0, 11, 71]) for (let type = 1; type <= 32; type++) {
    const { raid } = raidBrowser({ seed });
    let tile = raid.state.rngSeeds[1];
    const next = (low, high) => { const draw = raidDraw(tile, low, high); tile = draw.seed; return draw.value; };
    let remaining = next(15, 25);
    const expected = [];
    if (type === 3) expected.push(...Array(remaining).fill('coin'));
    else {
      if (type === 4 || type === 27) {
        const first = next(Math.floor(remaining / 4), Math.floor(remaining / 3));
        expected.push(...Array(first).fill(type === 4 ? 'potion' : 'shield')); remaining -= first;
      }
      const coins = next(Math.floor(3 * remaining / 4), Math.floor(remaining / (type === 13 ? 2 : [4, 27].includes(type) ? 4 : 3)));
      expected.push(...Array(coins).fill('coin'), ...Array(remaining - coins).fill('xp'));
    }
    assert.deepEqual(Array.from(raid.specialRewardTypes(type)), expected, `seed ${seed}, type ${type}`);
    assert.equal(raid.state.rngSeeds[1], tile);
  }
});

test('raid source board[0] mana can hide even non-potion special rewards without a mana target', () => {
  const { raid } = raidBrowser({ seed: 0 });
  raidBoard(raid, 'sword');
  const s = raid.state;
  s.board[0] = { type: 'potion', mana: true }; raidOld(raid, 14, {}, 4);
  s.hero.hp = 10;
  raid.collectIndices([14], { includeSpecial: true, noRefill: true });
  assert.deepEqual([s.hero.gold, s.hero.xpLevelCum, s.hero.hp, s.upgradeBar], [0, 0, 10, 0]);
  assert.equal(s.specialsKilled, 1, 'collection passives still run when reward tokens are hidden');
});

test('raid inherited mana charges only Healer potion rewards; coins/XP retain their own meaning', () => {
  const { raid } = raidBrowser({ seed: 0 });
  raidBoard(raid, 'sword');
  const s = raid.state;
  s.board[0] = { type: 'potion', mana: true }; raidOld(raid, 14, {}, 4);
  s.skills = ['heal']; s.skillCooldowns = { heal: 5 }; s.hero.hp = 10;
  raid.collectIndices([14], { includeSpecial: true, noRefill: true });
  assert.deepEqual([s.hero.gold, s.hero.xpLevelCum, s.hero.hp, s.skillCooldowns.heal], [6, 18, 10, 2]);
});

test('raid inherited acid affects only potion rewards and ignores boosts', () => {
  const { raid } = raidBrowser({ seed: 0 });
  raidBoard(raid, 'sword');
  const s = raid.state;
  s.board[0] = { type: 'potion', acid: true }; raidOld(raid, 14, {}, 4);
  s.hero.hp = 10; s.potionBoost = 1;
  raid.collectIndices([14], { includeSpecial: true, noRefill: true });
  assert.deepEqual([s.hero.gold, s.hero.xpLevelCum, s.hero.hp], [6, 18, 4]);
});

test('raid matching the top-left potion earlier in a chain does not erase special reward source variants', () => {
  const { raid, advance } = raidBrowser({ seed: 0 });
  raidBoard(raid, 'sword');
  raid.state.forceCoins = 1;
  raid.state.board[0] = { type: 'potion', mana: true };
  raid.state.board[1] = { type: 'chain' }; raidOld(raid, 2, { hp: 1, def: 0 }, 3);
  raidSelect(raid, [0, 1, 2]); raid.resolveChain(); advance(420);
  assert.equal(raid.state.skullsKilled, 1);
  assert.equal(raid.state.hero.gold, 0, 'all Golden tokens inherit the still-physical top-left mana source');
  assert.equal(raid.state.hero.xpLevelCum, 0);
});

test('raid Lich direct lethal hit transforms without rewards; collecting its phylactery gives normal special rewards', () => {
  const { raid, advance } = raidBrowser({ seed: 0 });
  raidBoard(raid, 'coin');
  const s = raid.state;
  s.forceCoins = 1;
  const lich = raidOld(raid, 1, { hp: 1, def: 0 }, 32);
  raid.hitSkullAt(1, 10, { direct: true });
  assert.equal(lich.phylactery, true);
  assert.equal(s.skullsKilled, 0);
  assert.equal(s.specialsKilled, 0);
  assert.equal(s.hero.gold, 0);
  assert.equal(s.hero.xpLevelCum, 0);
  raidSelect(raid, [0, 1, 2]); raid.resolveChain(); advance(420);
  assert.equal(s.specialsKilled, 1);
  assert.equal(s.skullsKilled, 0);
  assert.equal(s.hero.gold, 14, 'two collected coins plus five special coin tokens, never flat25');
  assert.equal(s.hero.xpLevelCum, 20);
});

test('raid burning specials and poison-killed Lich use the shared special reward path', () => {
  for (const source of ['fire', 'poison']) {
    const { raid } = raidBrowser({ seed: 0 });
    raidBoard(raid, 'sword');
    const s = raid.state;
    s.forceCoins = 1;
    raidOld(raid, 14, source === 'fire' ? { hp: 1, burn: true, fireDamage: 1 } : { hp: 1, poison: [1, 0, 0] }, source === 'fire' ? 2 : 32);
    raid.endEnemyTurn();
    assert.deepEqual([s.hero.gold, s.hero.xpLevelCum, s.specialsKilled], [10, 20, 1], source);
    assert.equal(s.skullsKilled, source === 'fire' ? 1 : 0);
    assert.ok(s.board.every(cell => cell.type !== 'empty'));
  }
});

function raidCombat(options = {}) {
  const b = raidBrowser(options);
  raidBoard(b.raid, 'shield');
  b.raid.state.turn = 10;
  b.raid.state.hero.hp = 50;
  return b;
}

test('raid one aggregate attack8 uses armour5 before wear and loses exactly3 HP', () => {
  const { raid } = raidCombat();
  const s = raid.state;
  s.hero.armor = s.hero.maxArmor = 5;
  raidOld(raid, 0, { atk: 3 }); raidOld(raid, 1, { atk: 5 });
  let other = s.rngSeeds[5], broken = 0;
  for (let n = 0; n < 5; n++) { const draw = raidDraw(other, 0, 100); other = draw.seed; if (draw.value < 50) broken++; }
  const volley = raid.processEnemyAttack();
  assert.equal(volley.attack, 8);
  assert.equal(volley.blocked, 5);
  assert.equal(volley.damage, 3);
  assert.equal(s.hero.hp, 47);
  assert.equal(s.hero.armor, 5 - broken);
  assert.equal(s.rngSeeds[5], other, 'exactly five integer durability draws, not sequential per-enemy blocks');
});

test('raid Defiler breaks all blocked points without consuming durability RNG', () => {
  const { raid } = raidCombat();
  const s = raid.state;
  s.hero.armor = 5;
  raidOld(raid, 0, { atk: 3 }); raidOld(raid, 1, { atk: 2 }); raidOld(raid, 35, {}, 29);
  const before = s.rngSeeds[5];
  raid.processEnemyAttack();
  assert.equal(s.hero.hp, 50);
  assert.equal(s.hero.armor, 0);
  assert.equal(s.rngSeeds[5], before);
});

test('raid durability uses R(0,100)<50, including the strict50 boundary and minimum5 threshold', () => {
  for (const [threshold, roll] of [[50, 49], [50, 50], [5, 4], [5, 5]]) {
    let seed;
    for (let candidate = 1; candidate <= 10000; candidate++) {
      const flushed = raidDraw(candidate, 0, 0).seed;
      if (raidDraw(flushed, 0, 100).value === roll) { seed = candidate; break; }
    }
    assert.ok(seed);
    const { raid } = raidCombat({ seed });
    raid.state.durability = threshold === 5 ? 1 : .5;
    raid.state.hero.armor = 1;
    const expected = raidDraw(raid.state.rngSeeds[5], 0, 100);
    assert.equal(expected.value, roll);
    raid.damageHero(1);
    assert.equal(raid.state.hero.armor, roll < threshold ? 0 : 1, `threshold${threshold}, roll${roll}`);
    assert.equal(raid.state.hero.hp, 50);
    assert.equal(raid.state.rngSeeds[5], expected.seed);
  }
});

test('raid Poisonous never bypasses armour; only a penetrating total volley sets max(old,incoming)', () => {
  const { raid } = raidCombat();
  const s = raid.state;
  s.poison = 4; s.hero.armor = 5;
  raidOld(raid, 0, { atk: 2 }, 9);
  raid.processEnemyAttack();
  assert.equal(s.hero.hp, 50);
  assert.equal(s.poison, 4);
  s.hero.armor = 0; s.board[0].skull.atk = 7;
  raidOld(raid, 1, { atk: 2 }, 9);
  raid.processEnemyAttack();
  assert.equal(s.hero.hp, 41);
  assert.equal(s.poison, 9, 'sum of eligible Poisonous attacks, not old4+incoming9');
});

test('raid even a small poisonous attack can poison when other enemies make the total volley penetrate', () => {
  const { raid } = raidCombat();
  raid.state.hero.armor = 4;
  raidOld(raid, 0, { atk: 1 }, 9); raidOld(raid, 1, { atk: 5 });
  raid.processEnemyAttack();
  assert.equal(raid.state.hero.hp, 48);
  assert.equal(raid.state.poison, 1);
});

test('raid Elf attack reduction reads enemy poison slot0, not the hero persistent poison', () => {
  const { raid } = raidCombat({ classId: 'ranger' });
  raid.state.hero.armor = 0; raid.state.poison = 8;
  raidOld(raid, 0, { atk: 7, poison: [1, 0, 0] });
  raidOld(raid, 1, { atk: 7, poison: [0, 0, 0] });
  raid.processEnemyAttack();
  assert.equal(raid.state.hero.hp, 40, 'floor(7/2)+7, not 7+7 or floor(7/2)*2');
});

test('raid normal volley excludes frozen/new/zero attack skulls', () => {
  const { raid } = raidCombat();
  const s = raid.state;
  s.hero.armor = 0;
  raidOld(raid, 0, { atk: 100 }); s.board[0].frozen = true;
  raidOld(raid, 1, { atk: 100, enteredTurn: s.turn });
  raidOld(raid, 2, { atk: 0 }); raidOld(raid, 3, { atk: 3 });
  assert.equal(raid.processEnemyAttack().attack, 3);
  assert.equal(s.hero.hp, 47);
});

test('raid Counterattack replaces the volley, includes frozen old skulls, and respects defence', () => {
  const { raid } = raidCombat();
  const s = raid.state;
  s.counterattack = 1;
  const a = raidOld(raid, 0, { atk: 4, def: 1 });
  const b = raidOld(raid, 1, { atk: 8, def: 5 }); s.board[1].frozen = true;
  const fresh = raidOld(raid, 2, { atk: 20, enteredTurn: s.turn });
  raid.processEnemyAttack();
  assert.deepEqual([a.hp, b.hp, fresh.hp], [17, 17, 20]);
  assert.equal(s.hero.hp, 50);
  assert.equal(s.hero.armor, 4);
});

test('raid armour-stage weaken/spikes affect frozen old skulls but not fresh ones, without changing volley damage', () => {
  const { raid } = raidCombat();
  const s = raid.state;
  s.hero.armor = 1; s.blunt = 2; s.spikes = 3;
  const frozen = raidOld(raid, 0, { atk: 6, def: 1 }); s.board[0].frozen = true;
  const active = raidOld(raid, 1, { atk: 2 });
  const fresh = raidOld(raid, 2, { atk: 10, enteredTurn: s.turn });
  raid.processEnemyAttack();
  assert.deepEqual([frozen.atk, active.atk, fresh.atk], [4, 1, 10]);
  assert.deepEqual([frozen.hp, active.hp, fresh.hp], [18, 17, 20]);
  assert.equal(s.hero.hp, 49, 'snapshot attack2 minus entry armour1, not weakened attack1');
});

test('raid zero entry armour skips weaken and spikes entirely', () => {
  const { raid } = raidCombat();
  raid.state.hero.armor = 0; raid.state.blunt = 2; raid.state.spikes = 3;
  const skull = raidOld(raid, 0, { atk: 3 });
  raid.processEnemyAttack();
  assert.deepEqual([skull.atk, skull.hp], [3, 20]);
  assert.equal(raid.state.hero.hp, 47);
});

test('raid spike kill cannot cancel snapshotted incoming damage and pays the shared special reward', () => {
  const { raid } = raidCombat({ seed: 0 });
  const s = raid.state;
  s.forceCoins = 1; s.hero.armor = 5; s.spikes = 1;
  raidOld(raid, 0, { atk: 10, hp: 1 }); raidOld(raid, 1, { hp: 1 }, 3);
  raid.processEnemyAttack();
  assert.equal(s.hero.hp, 45);
  assert.equal(s.skullsKilled, 2);
  assert.equal(s.hero.gold, 30, 'Golden spike death uses 15 coin tokens');
  assert.equal(s.hero.xpLevelCum, 2);
  assert.ok(s.board.every(cell => cell.type !== 'empty'));
});

test('raid Vampire healing follows weaken/spikes and only occurs on penetrating volleys', () => {
  for (const [classId, expectedHp, expectedMax] of [['adventurer', 9, 10], ['assassin', 11, 11]]) {
    const { raid } = raidCombat({ classId });
    const s = raid.state;
    s.hero.armor = 1; s.spikes = 1; s.blunt = 1;
    const vampire = raidOld(raid, 0, { atk: 3, hp: classId === 'assassin' ? 10 : 8, maxHp: 10 }, 20);
    raidOld(raid, 1, { atk: 2 });
    raid.processEnemyAttack();
    assert.deepEqual([vampire.hp, vampire.maxHp, vampire.atk], [expectedHp, expectedMax, 2], classId);
    assert.equal(s.hero.hp, 46);
  }
});

test('raid directly hitting a Vampire does not heal it; a blocked volley does not trigger Vampires', () => {
  const { raid } = raidCombat();
  const vampire = raidOld(raid, 0, { atk: 3, hp: 10, maxHp: 10 }, 20);
  raid.hitSkullAt(0, 3, { direct: true });
  assert.equal(vampire.hp, 7);
  raid.state.hero.armor = 5;
  raid.processEnemyAttack();
  assert.equal(vampire.hp, 7);
  assert.equal(raid.state.hero.hp, 50);
});

test('raid old burning attackers strike before their fire tick; Mage blocks recovery before burning away', () => {
  const { raid } = raidCombat({ seed: 0 });
  const s = raid.state;
  s.forceCoins = 1; s.hero.armor = 0;
  s.skills = ['heal']; s.skillCooldowns = { heal: 5 };
  raidOld(raid, 0, { atk: 3, hp: 1, burn: true, fireDamage: 1 }, 15);
  raid.enemyPhase();
  assert.equal(s.hero.hp, 47);
  assert.equal(s.turn, 11);
  assert.equal(s.skillCooldowns.heal, 5);
  assert.equal(s.skullsKilled, 1);
  assert.equal(s.hero.gold, 10);
  assert.equal(s.hero.xpLevelCum, 20);
  raid.endEnemyTurn();
  assert.equal(s.skillCooldowns.heal, 4);
});

test('raid EndOfTurn thaws OR burns, regenerates before persistent poison, then shifts enemy poison', () => {
  const { raid } = raidCombat();
  const s = raid.state;
  s.hero.hp = 10; s.regen = 3; s.poison = 4;
  const skull = raidOld(raid, 0, { burn: true, fireDamage: 3, poison: [2, 3, 4] }); s.board[0].frozen = true;
  raid.endEnemyTurn();
  assert.equal(s.hero.hp, 9);
  assert.equal(skull.hp, 18, 'thaw prevents the fire tick, but not enemy poison');
  assert.equal(skull.burn, true);
  assert.deepEqual(Array.from(skull.poison), [3, 4, 0]);
  raid.endEnemyTurn();
  assert.equal(skull.hp, 12, 'next turn fire3 then poison3');
  assert.equal(skull.burn, false);
});

test('raid penetrating poison applies max poison and ticks after regeneration in the same EndOfTurn', () => {
  const { raid } = raidCombat();
  const s = raid.state;
  s.hero.hp = 20; s.hero.armor = 0; s.regen = 1;
  raidOld(raid, 0, { atk: 3 }, 9);
  raid.enemyPhase();
  assert.equal(s.hero.hp, 15, '20 - volley3 + regen1 - poison3');
  assert.equal(s.poison, 3);
});

test('raid enemy poison death collects without direct kill/defence or a Lich transformation', () => {
  const { raid } = raidCombat({ seed: 0 });
  const s = raid.state;
  s.forceCoins = 1;
  const lich = raidOld(raid, 0, { hp: 1, def: 100, poison: [2, 3, 4] }, 32);
  raid.endEnemyTurn();
  assert.ok(!s.board.some(cell => cell.type === 'skull' && cell.skull.uid === lich.uid));
  assert.equal(s.skullsKilled, 0);
  assert.equal(s.specialsKilled, 1);
  assert.equal(s.hero.xpLevelCum, 20);
});

test('raid direct zero-damage hit can poison all three enemy slots; Poisonous itself is immune', () => {
  const { raid } = raidCombat();
  raid.state.weaponPoison = 2;
  const normal = raidOld(raid, 0, { def: 100 });
  const immune = raidOld(raid, 1, { def: 100 }, 9);
  raid.hitSkullAt(0, 0, { direct: true }); raid.hitSkullAt(1, 0, { direct: true });
  assert.deepEqual(Array.from(normal.poison), [2, 2, 2]);
  assert.deepEqual(Array.from(immune.poison), [0, 0, 0]);
});

test('raid death is checked after turn++ before regeneration; Last Stand can rescue before poison', () => {
  const dead = raidCombat().raid;
  dead.state.hero.hp = 0; dead.state.regen = 10;
  dead.endEnemyTurn();
  assert.equal(dead.state.over, true);
  assert.equal(dead.state.turn, 11);
  assert.equal(dead.state.hero.hp, 0);
  const saved = raidCombat().raid;
  saved.state.hero.hp = 2; saved.state.hero.armor = 0; saved.state.poison = 3; saved.state.lastStand = 1;
  raidOld(saved, 0, { atk: 5 });
  saved.enemyPhase();
  assert.equal(saved.state.hero.hp, 47);
  assert.equal(saved.state.over, false);
  assert.equal(saved.state.lastStand, 0);
});

test('raid Sprint turn100 death precedes victory; victory checks the entire EndOfTurn tail', () => {
  const dead = raidCombat({ mode: 'sprint' }).raid;
  dead.state.turn = 99; dead.state.hero.hp = 2; dead.state.poison = 3;
  dead.endEnemyTurn();
  assert.equal(dead.state.turn, 100);
  assert.equal(dead.state.won, false);
  assert.equal(dead.state.over, true);
  const winner = raidCombat({ mode: 'sprint' }).raid;
  winner.state.turn = 99; winner.state.promotionRemaining = 10;
  winner.endEnemyTurn();
  assert.equal(winner.state.won, true);
  assert.equal(winner.state.promotionRemaining, 9);
});

test('raid Vengeful receives chain kills once after refill, not again in its ability stage', () => {
  const { raid, advance } = raidCombat({ seed: 0 });
  const s = raid.state;
  s.forceCoins = 1;
  s.board[0] = { type: 'sword' }; s.board[1] = { type: 'sword' }; raidOld(raid, 2, { hp: 1 });
  const vengeful = raidOld(raid, 35, { atk: 2 }, 21);
  raidSelect(raid, [0, 1, 2]); raid.resolveChain(); advance(420);
  assert.equal(vengeful.atk, 3);
  advance(550);
  assert.equal(s.turn, 11);
  assert.equal(vengeful.atk, 3);
});

const runeSquare = [7, 8, 9, 15, 21, 20, 19, 13];
const runeOuter = [0, 1, 2, 3, 4, 5, 11, 17, 23, 29, 35, 34, 33, 32, 31, 30, 24, 18, 12, 6];

test('raid Rune row parity covers square/outer/diamond shapes and rejects open/short/self-crossing interiors', () => {
  const { raid } = raidBrowser();
  assert.deepEqual(Array.from(raid.interiorCells(runeSquare)), [14]);
  assert.deepEqual(Array.from(raid.interiorCells(runeOuter)), [7, 8, 9, 10, 13, 14, 15, 16, 19, 20, 21, 22, 25, 26, 27, 28]);
  assert.deepEqual(Array.from(raid.interiorCells([8, 15, 20, 13])), [14]);
  assert.deepEqual(Array.from(raid.interiorCells([7, 14, 13, 8])), []);
  assert.deepEqual(Array.from(raid.interiorCells([7, 8, 9, 15])), []);
  assert.deepEqual(Array.from(raid.interiorCells([7, 8, 13])), []);
});

test('raid active Rune works for a non-Mage and directly collects a huge defended ordinary skull without a kill', () => {
  const { raid, advance } = raidBrowser({ seed: 0 });
  raidBoard(raid, 'shield');
  const s = raid.state;
  s.runeActive = 1; s.forceCoins = 1;
  for (const idx of runeSquare) s.board[idx] = { type: 'coin' };
  const inside = raidOld(raid, 14, { hp: 999, def: 999 });
  raidSelect(raid, runeSquare); raid.resolveChain(); advance(420);
  assert.ok(!s.board.some(cell => cell.type === 'skull' && cell.skull.uid === inside.uid));
  assert.equal(s.hero.xpLevelCum, 2);
  assert.equal(s.skullsKilled, 0);
  assert.equal(s.hero.gold, 26);
  assert.equal(s.specialsKilled, 0);
});

for (const type of [1, 2, 3, 32]) test(`raid Rune preview includes interior special${type}, but execution preserves it`, () => {
  const { raid, advance } = raidBrowser({ seed: 0 });
  raidBoard(raid, 'shield');
  const s = raid.state;
  s.runeActive = 1; s.forceCoins = 1;
  for (const idx of runeSquare) s.board[idx] = { type: 'coin' };
  const inside = raidOld(raid, 14, { hp: 1, def: 0 }, type);
  raidSelect(raid, runeSquare);
  assert.deepEqual(Array.from(raid.chainPreview().interior), [14]);
  raid.resolveChain(); advance(420);
  assert.equal(inside.hp, 1);
  assert.ok(s.board.some(cell => cell.type === 'skull' && cell.skull.uid === inside.uid));
  assert.equal(s.specialsKilled, 0);
  assert.equal(s.skullsKilled, 0);
  assert.equal(s.hero.xpLevelCum, 0);
});

test('raid sixteen Rune-collected skulls give XP but never Guts or direct kill counts', () => {
  const { raid, advance } = raidBrowser({ classId: 'raider', seed: 0 });
  raidBoard(raid, 'shield');
  const s = raid.state;
  s.runeActive = 1; s.forceCoins = 1;
  for (const idx of runeOuter) s.board[idx] = { type: 'coin' };
  for (const idx of raid.interiorCells(runeOuter)) raidOld(raid, idx, { hp: 999, def: 999 });
  const before = s.hero.maxArmor;
  raidSelect(raid, runeOuter); raid.resolveChain(); advance(420);
  assert.equal(s.skullsKilled, 0);
  assert.equal(s.hero.maxArmor, before);
  assert.equal(s.equipment[2].mods.maxArmor, 2, 'Rune does not upgrade the starting armour');
  assert.ok(s.hero.xpLevelCum >= 32);
});

for (const prior of [0, 3]) test(`raid Rune consumes prior same-type count${prior}, then normal chain resets the counters`, () => {
  const { raid, advance } = raidBrowser({ seed: 0 });
  raidBoard(raid, 'shield');
  const s = raid.state;
  s.runeActive = 1; s.forceCoins = 1; s.collectionCounts.coin = prior;
  for (const idx of [...runeSquare, 14]) s.board[idx] = { type: 'coin' };
  raidSelect(raid, runeSquare); raid.resolveChain(); advance(420);
  assert.equal(s.hero.gold, prior === 3 ? 30 : 28);
  assert.equal(s.collectionCounts.coin, 8, 'Rune count must not leak into MatchTiles');
});

test('raid Rune explosive potion can collect selected boundary resources without duplicate payout', () => {
  const { raid, advance } = raidBrowser({ seed: 0 });
  raidBoard(raid, 'shield');
  const s = raid.state;
  s.runeActive = 1; s.forceCoins = 1; s.hero.hp = 10;
  for (const idx of runeSquare) s.board[idx] = { type: 'coin' };
  s.board[14] = { type: 'potion', explosive: true };
  raidSelect(raid, runeSquare); raid.resolveChain(); advance(420);
  assert.equal(s.hero.gold, 26);
  assert.equal(s.hero.hp, 10);
  assert.equal(s.collectionCounts.coin || 0, 0, 'boundary had already been collected by Rune blast before the normal reset');
});

test('raid Rune runs before cancelling a two-effective-node arrow diamond, without an enemy turn', () => {
  const { raid, advance } = raidBrowser({ seed: 0 });
  raidBoard(raid, 'coin');
  const s = raid.state;
  s.runeActive = 1; s.forceCoins = 1;
  const a = { type: 'arrow' }, b = { type: 'arrow' };
  s.board[8] = a; s.board[20] = b;
  raidOld(raid, 35, { atk: 100 });
  raidSelect(raid, [8, 15, 20, 13]);
  assert.equal(raid.chainPreview().effectiveLength, 2);
  raid.resolveChain(); advance(2000);
  assert.equal(s.hero.gold, 2, 'only interior14 is collected');
  assert.equal(s.turn, 0);
  assert.equal(s.chainCount, 0);
  assert.equal(s.hero.hp, 50);
  assert.equal(s.resolving, false);
  assert.ok(s.board.includes(a) && s.board.includes(b), 'invalid main chain is not collected');
  assert.ok(s.board.every(cell => cell.type !== 'empty'));
});

test('raid resource preview shows actual base repair/UP and consumes neither RNG nor carry', () => {
  const { raid } = raidBrowser();
  raidBoard(raid, 'shield');
  const s = raid.state;
  s.hero.armor = 3; s.hero.maxArmor = 5; s.armorPerShield = 2;
  raidSelect(raid, [0, 1, 2]);
  const before = JSON.stringify(s);
  const p = raid.chainPreview();
  assert.deepEqual([p.resources.armorGained, p.resources.upGained], [2, 4]);
  assert.equal(JSON.stringify(s), before, 'preview must be pure');
});

test('raid broken magic sword contributes zero in both preview and actual combat', () => {
  const { raid, advance } = raidBrowser();
  raidBoard(raid, 'shield');
  const s = raid.state;
  s.forceCoins = 1;
  s.board[0] = { type: 'sword', magicSword: true, broken: true }; s.board[1] = { type: 'sword' };
  const skull = raidOld(raid, 2, { hp: 8 });
  raidSelect(raid, [0, 1, 2]);
  const preview = raid.chainPreview();
  assert.equal(preview.damage, 5);
  assert.equal(preview.lethals.length, 0);
  raid.resolveChain(); advance(420);
  assert.equal(skull.hp, 3);
  assert.equal(s.skullsKilled, 0);
});

test('raid leech truncates float32 chain damage once, even when enemy defence absorbs all HP damage', () => {
  const { raid, advance } = raidBrowser();
  raidBoard(raid, 'shield');
  const s = raid.state;
  s.forceCoins = 1; s.hero.hp = 10; s.leech = .2;
  s.board[0] = { type: 'sword', magicSword: true }; s.board[1] = { type: 'sword' };
  const skull = raidOld(raid, 2, { hp: 20, def: 100 });
  raidSelect(raid, [0, 1, 2]); raid.resolveChain(); advance(420);
  assert.equal(skull.hp, 20);
  assert.equal(s.hero.hp, 13, 'D15 ×20% =3, not per-enemy lost HP');
});

for (const combat of [false, true]) test(`raid Cocky three-node ${combat ? 'combat still rewards kills' : 'resource chain clears without reward'} and commits a turn`, () => {
  const { raid, advance } = raidCombat({ classId: 'raider', seed: 0 });
  const s = raid.state;
  s.forceCoins = 1; s.hero.armor = 0;
  for (let i = 0; i < 3; i++) s.board[i] = { type: combat ? 'sword' : 'coin' };
  if (combat) raidOld(raid, 2, { hp: 1 });
  raidOld(raid, 35, { atk: 2 });
  raidSelect(raid, [0, 1, 2]); raid.resolveChain(); advance(420);
  assert.equal(s.hero.gold, 0);
  assert.equal(s.hero.xpLevelCum, combat ? 2 : 0);
  advance(550);
  assert.equal(s.turn, 11);
  assert.equal(s.hero.hp, 48);
});

test('raid an ordinary potion cures player poison, except for the Elf mortal Poisonous still on board', () => {
  for (const [classId, expectedPoison] of [['adventurer', 0], ['ranger', 4]]) {
    const { raid } = raidBrowser({ classId });
    raidBoard(raid);
    raidOld(raid, 35, {}, 9);
    raid.state.poison = 4;
    raid.addResource({ type: 'potion' });
    assert.equal(raid.state.poison, expectedPoison, classId);
    raid.state.board[35] = { type: 'coin' };
    raid.addResource({ type: 'potion' });
    assert.equal(raid.state.poison, 0);
  }
});

test('raid Faun rejuvenation happens on the threshold arrival before a level menu choice', () => {
  const { raid } = raidBrowser({ classId: 'priest', menus: true });
  const s = raid.state;
  s.hero.maxHp = 51; s.hero.hp = 1; s.hero.xp = 99; s.poison = 4;
  raid.addResource({ type: 'xp' });
  assert.equal(s.pendingModal, 'level');
  assert.equal(s.hero.hp, 12);
  assert.equal(s.hero.xp, 1);
  assert.equal(s.hero.level, 2);
  assert.equal(s.poison, 4, 'Faun healing is not a detoxifying potion');
});

test('raid mana slot disappearance falls through to healing; a surviving slot charges its current skill', () => {
  const { raid } = raidBrowser();
  const s = raid.state;
  s.hero.hp = 10; s.skills = [];
  raid.addResource({ type: 'potion', variant: 1, skillSlot: 0 });
  assert.equal(s.hero.hp, 12);
  s.skills = ['freeze']; s.skillCooldowns = { freeze: 3 };
  raid.addResource({ type: 'potion', variant: 1, skillSlot: 0 });
  assert.equal(s.skillCooldowns.freeze, 2);
  assert.equal(s.hero.hp, 12);
});

test('raid acid death discards later resource arrivals unless Last Stand rescues it', () => {
  for (const protect of [false, true]) {
    const { raid, advance } = raidBrowser({ seed: 0 });
    raidBoard(raid, 'sword');
    const s = raid.state;
    s.hero.hp = 1; s.lastStand = protect ? 1 : 0; s.forceCoins = 1;
    s.board[0] = { type: 'potion', acid: true }; s.board[1] = { type: 'chain' }; s.board[2] = { type: 'coin' };
    raidSelect(raid, [0, 1, 2]); raid.resolveChain(); advance(420);
    assert.equal(s.over, !protect);
    assert.equal(s.hero.gold, protect ? 2 : 0, 'later coin must not arrive after terminal acid damage');
    assert.equal(s.arrivals.length, 0);
    if (protect) assert.equal(s.hero.hp, s.hero.maxHp);
  }
});

test('raid spikes level menu settles before EndOfTurn increments the turn', () => {
  const { raid, elements, advance } = raidCombat({ menus: true, seed: 0 });
  const s = raid.state;
  s.forceCoins = 1; s.hero.xp = 99; s.hero.xpLevelCum = 99; s.spikes = 1;
  raidOld(raid, 0, { hp: 1, atk: 1 });
  raid.enemyPhase();
  assert.equal(s.pendingModal, 'level');
  assert.equal(s.turn, 10);
  assert.equal(s.hero.xp, 1);
  assert.equal(s.resolving, true);
  raidPickLevel(elements); advance(80);
  assert.equal(s.turn, 11);
  assert.equal(s.resolving, false);
});

test('raid burn/Cripple resource menus pause arrivals after EndOfTurn, without starting another turn', () => {
  const { raid, elements, advance } = raidCombat({ menus: true, seed: 0 });
  const s = raid.state;
  s.forceCoins = 1; s.hero.xp = 99; s.hero.xpLevelCum = 99; s.coinBoost = 1;
  raidOld(raid, 0, { hp: 1, burn: true, fireDamage: 1 }, 2);
  raid.enemyPhase();
  assert.equal(s.turn, 11);
  assert.equal(s.pendingModal, 'level');
  assert.equal(s.hero.gold, 10, 'end-turn rewards no longer use the expired coin boost');
  assert.ok(s.arrivals.length > 0);
  raidPickLevel(elements); advance(1000);
  assert.equal(s.turn, 11);
  assert.equal(s.hero.xpLevelCum, 119);
  assert.equal(s.resolving, false);
});

test('raid skill removals refill holes, collect ordinary skulls, and keep specials safe in no-reward Greed', () => {
  for (const skill of ['wish', 'greed', 'explosivearmour']) {
    const { raid } = raidBrowser({ seed: 0 });
    raidBoard(raid, 'sword');
    const s = raid.state;
    s.forceCoins = 1;
    s.board[0] = { type: 'coin' };
    const normal = raidOld(raid, 1, { hp: 1 });
    const special = raidOld(raid, 6, { hp: 1 }, 2);
    raid.activateSkill(skill);
    assert.ok(!s.board.some(cell => cell.type === 'skull' && cell.skull.uid === normal.uid), skill);
    assert.ok(s.board.every(cell => cell.type !== 'empty'), skill);
    assert.equal(s.turn, 0);
    if (skill === 'greed') {
      assert.equal(s.skullsKilled, 0);
      assert.equal(s.specialsKilled, 0);
      assert.equal(s.hero.xpLevelCum, 0);
      assert.ok(s.board.some(cell => cell.type === 'skull' && cell.skull.uid === special.uid));
    }
    if (skill === 'wish') assert.equal(s.skullsKilled, 0, 'Wish collection is not a direct attack');
  }
});

test('raid frozen skulls remain selectable; arrow-ignored resources stay on the board and do not satisfy minimum length', () => {
  const { raid, advance } = raidBrowser({ seed: 0 });
  raidBoard(raid, 'shield');
  const s = raid.state;
  s.forceCoins = 1;
  const skipped = { type: 'coin' };
  s.board[0] = { type: 'arrow' }; s.board[1] = skipped;
  const target = raidOld(raid, 2, { hp: 1 }); s.board[2].frozen = true;
  s.board[3] = { type: 'sword' };
  raidSelect(raid, [0, 1, 2]); raid.resolveChain(); advance(1500);
  assert.equal(s.turn, 0, 'only arrow and skull count, so the main chain is cancelled');
  assert.ok(s.board.includes(skipped)); assert.equal(target.hp, 1);
  raidSelect(raid, [0, 1, 2, 3]); raid.resolveChain(); advance(420);
  assert.equal(s.skullsKilled, 1);
  assert.ok(s.board.includes(skipped));
  assert.equal(s.hero.gold, 0);
});

test('raid returning to any prior selected cell removes the whole suffix without consuming RNG', () => {
  const { raid } = raidBrowser();
  raidBoard(raid, 'coin');
  raidSelect(raid, [0, 1, 2, 3, 4]);
  const before = Array.from(raid.state.rngSeeds);
  raid.tryExtendChain(1);
  assert.deepEqual(Array.from(raid.state.selected), [0, 1]);
  assert.deepEqual(Array.from(raid.state.rngSeeds), before);
});

test('raid all-frozen attackers skip the entire ordinary/counterattack stage', () => {
  const { raid } = raidCombat();
  const s = raid.state;
  s.counterattack = 1; s.spikes = 5; s.blunt = 3;
  const skull = raidOld(raid, 0, { atk: 10 }); s.board[0].frozen = true;
  assert.equal(raid.processEnemyAttack(), null);
  assert.deepEqual([skull.hp, skull.atk, s.hero.hp, s.hero.armor], [20, 10, 50, 4]);
});

test('raid help/build includes actual shield, combined volley and Rune preview rules/styles', () => {
  const css = readFileSync(new URL('./raid.css', import.meta.url), 'utf8');
  const template = readFileSync(new URL('./raid.html', import.meta.url), 'utf8');
  const built = readFileSync(new URL('../lunapark-raid.html', import.meta.url), 'utf8');
  assert.ok(built.includes(css));
  assert.match(css, /\.cell\.rune-inside\.rune-protected/);
  assert.match(template, /Каждый щит либо чинит броню, либо даёт 2 UP/);
  assert.match(template, /атакуют общим залпом/);
  assert.match(template, /обычные черепа — без проверки HP\/защиты/);
  assert.doesNotMatch(template, /Щиты → броня;/);
});

test('raid item resource upgrades use quarter-point steps and display points rather than percentages', () => {
  const { raid } = raidBrowser({ seed: 71 });
  const found = new Set();
  for (let n = 0; n < 200; n++) for (const slot of ['helmet', 'shield']) {
    const offer = raid.makeUpgradeOffer(slot);
    if (!['goldPerCoin', 'xpPerSkull', 'upgradePerShield'].includes(offer.key)) continue;
    found.add(offer.key);
    assert.equal(offer.value, .25);
    assert.match(offer.label, /\+0\.25$/);
    assert.doesNotMatch(offer.label, /%/);
  }
  assert.deepEqual([...found].sort(), ['goldPerCoin', 'upgradePerShield', 'xpPerSkull']);
});

test('raid capped resource bonuses are excluded from further item upgrade offers, not capped before the last legal step', () => {
  const { raid } = raidBrowser({ seed: 71 });
  const s = raid.state, keys = ['goldPerCoin', 'xpPerSkull', 'upgradePerShield'];
  for (const key of keys) s[key] = 3.75;
  const eligible = new Set();
  for (let n = 0; n < 100; n++) for (const slot of ['helmet', 'shield']) {
    const offer = raid.makeUpgradeOffer(slot);
    if (keys.includes(offer.key)) eligible.add(offer.key);
  }
  assert.equal(eligible.size, 3, 'the step from +1.75 to +2 remains eligible');
  for (const key of keys) s[key] = 4;
  for (let n = 0; n < 100; n++) for (const slot of ['helmet', 'shield']) {
    assert.ok(!keys.includes(raid.makeUpgradeOffer(slot).key), 'a derived bonus of +2 is already capped');
  }
});

test('raid capped XP/gold/UP yields preserve raw equipment sums when an over-cap item is removed', () => {
  const { raid } = raidBrowser();
  const s = raid.state;
  const mods = value => ({ goldPerCoin: value, xpPerSkull: value, upgradePerShield: value });
  raid.equipItem({ slot: 'shield', mods: mods(3) });
  raid.equipItem({ slot: 'helmet', mods: mods(.5) });
  for (const key of Object.keys(mods(0))) assert.equal(s[key], 5.5, 'raw item sum must not lose overflow');
  for (const type of ['coin', 'xp', 'shield']) raid.addResource({ type });
  assert.deepEqual([s.hero.gold, s.hero.xp, s.upgradeBar], [4, 4, 4], 'each derived bonus caps at +2 over base2');
  raid.equipItem({ slot: 'shield', mods: {} });
  for (const key of Object.keys(mods(0))) assert.equal(s[key], 2.5, 'the other item still contributes +0.5');
  for (let n = 0; n < 2; n++) for (const type of ['coin', 'xp', 'shield']) raid.addResource({ type });
  assert.deepEqual([s.hero.gold, s.hero.xp, s.upgradeBar], [9, 9, 9]);
  for (const key of ['gold', 'xp', 'upgrade']) assert.equal(s.resourceCarry[key], 0);
});

test('raid character summary shows capped effective resource yields and item text keeps fractional UP', () => {
  const { raid } = raidBrowser();
  const s = raid.state, panel = raidNode();
  s.goldPerCoin = s.xpPerSkull = s.upgradePerShield = 9;
  raid.renderCharacterSummary(panel);
  const rows = panel.querySelectorAll('.character-summary-row');
  assert.match(rows.find(row => row.innerHTML.includes('Золото за монету')).innerHTML, /4\.00 \/ 4\.00/);
  assert.match(rows.find(row => row.innerHTML.includes('Щит: ремонт')).innerHTML, /4\.00 UP/);
  assert.match(raid.itemModsText({ mods: { upgradePerShield: .25 } }), /\+0\.25$/);
});

test('raid Flaming adds multiple fire sources and thaws resources/skulls without igniting other Flaming', () => {
  const { raid } = raidBrowser();
  raidBoard(raid);
  const s = raid.state;
  const target = raidOld(raid, 7, { burn: true, fireDamage: 4, stunned: true });
  s.board[7].frozen = true;
  const resource = { type: 'coin', burn: 4, frozen: true }; s.board[1] = resource;
  const a = raidOld(raid, 6, { atk: 2 }, 7), b = raidOld(raid, 8, { atk: 3 }, 7);
  raid.applySpecialAbilities();
  assert.deepEqual([target.fireDamage, target.burn, target.stunned, s.board[7].frozen], [9, true, false, false]);
  assert.deepEqual([resource.burn, resource.frozen], [9, false]);
  assert.equal(!!a.burn || !!b.burn, false);
});

test('raid Dragonkin Fire Breath stacks on an ordinary skull, clears frost and marks safe fire', () => {
  const { raid } = raidBrowser({ classId: 'paladin' });
  raidBoard(raid);
  const s = raid.state;
  const target = raidOld(raid, 0, { burn: true, fireDamage: 2, stunned: true });
  s.board[0].frozen = true;
  const special = raidOld(raid, 35, {}, 2);
  raid.activateSkill('boostdmg'); raid.activateSkill('boostgold');
  assert.deepEqual([target.fireDamage, target.burn, target.fireSafe, target.stunned], [8, true, true, false]);
  assert.equal(s.board.find(cell => cell.skull === target).frozen, false);
  assert.equal(!!special.burn, false);
  assert.equal(s.turn, 0);
});

test('raid both Freeze skill and Freezing ability extinguish fire when freezing a tile', () => {
  const { raid } = raidBrowser({ seed: 0 });
  raidBoard(raid);
  const s = raid.state;
  const target = raidOld(raid, 0, { burn: true, fireDamage: 5, fireSafe: true });
  raid.activateSkill('freeze');
  assert.deepEqual([s.board[0].frozen, target.burn, target.fireDamage, target.fireSafe], [true, false, 0, false]);
  for (let slot = 0; slot < 36; slot++) raidOld(raid, slot, {}, 2);
  const resource = { type: 'coin', burn: 3 }; s.board[0] = resource;
  const ordinary = raidOld(raid, 1, { burn: true, fireDamage: 4 });
  raidOld(raid, 35, {}, 8);
  raid.applySpecialAbilities();
  assert.deepEqual([resource.frozen, resource.burn, resource.fireDamage], [true, false, 0]);
  assert.deepEqual([s.board[1].frozen, ordinary.burn, ordinary.fireDamage], [true, false, 0]);
});

test('raid Golden timeout refills immediately so a later Healer can buff the newly filled ordinary skull', () => {
  const { raid } = raidBrowser({ seed: 71 });
  raidBoard(raid);
  const s = raid.state;
  const golden = raidOld(raid, 0, { countdown: 1 }, 3);
  raidOld(raid, 6, {}, 4);
  // rngSeeds is a diagnostic mirror, not a setter. Advance the real stream
  // through the public tile generator until the independently predicted draw
  // selects a skull for the Golden's single empty slot.
  for (let n = 0; n < 100 && raidDraw(s.rngSeeds[1], 0, 4).value !== 1; n++) raid.createTile();
  assert.equal(raidDraw(s.rngSeeds[1], 0, 4).value, 1);
  raid.applySpecialAbilities();
  assert.equal(s.board[0].type, 'skull');
  assert.equal(s.board[0].skull.special, false);
  assert.equal(s.board[0].skull.atk, 2, 'refill attack1 was buffed by the later Healer');
  assert.ok(!s.board.some(cell => cell.skull?.uid === golden.uid));
  assert.ok(s.board.every(cell => cell.type !== 'empty'));
  assert.deepEqual([s.hero.gold, s.hero.xpLevelCum, s.specialsKilled, s.skullsKilled], [0, 0, 0, 0]);
});

test('raid Kamikaze timeout resets same-type counters before matching its neighbouring resources', () => {
  const { raid } = raidBrowser({ seed: 0 });
  raidBoard(raid, 'sword');
  const s = raid.state;
  s.collectionCounts = { coin: 3 };
  s.board[0] = { type: 'coin' }; s.board[1] = { type: 'coin' };
  raidOld(raid, 7, { countdown: 1 }, 10);
  raid.applySpecialAbilities();
  assert.equal(s.collectionCounts.coin, 2);
  assert.deepEqual([s.hero.hp, s.hero.gold, s.hero.xpLevelCum, s.specialsKilled, s.skullsKilled], [25, 4, 0, 0, 0]);
});

test('raid two selected Spiky skulls reflect one chain damage total, not once per skull', () => {
  const { raid, advance } = raidBrowser();
  raidBoard(raid);
  const s = raid.state;
  s.forceCoins = 1;
  s.board[0] = { type: 'sword' };
  for (const slot of [1, 2]) raidOld(raid, slot, { hp: 100, def: 100 }, 2);
  raidSelect(raid, [0, 1, 2]); raid.resolveChain(); advance(420);
  assert.equal(s.hero.hp, 45, 'D=3+2 once, not 2×D');
  assert.equal(s.hero.armor, 4, 'Spiky reflection bypasses armour');
});

test('raid Chaotic consuming an ordinary skull pays no XP and does not register a player kill', () => {
  const { raid } = raidBrowser({ seed: 0 });
  raidBoard(raid);
  const s = raid.state;
  const normal = raidOld(raid, 0);
  const chaotic = raidOld(raid, 35, {}, 14);
  raid.applySpecialAbilities();
  assert.equal(s.board[0].skull.uid, chaotic.uid);
  assert.ok(!s.board.some(cell => cell.skull?.uid === normal.uid));
  assert.deepEqual([s.skullsKilled, s.specialsKilled, s.hero.xpLevelCum, s.hero.gold], [0, 0, 0, 0]);
});

// Equipment / shop / scroll / trophy parity. Numeric fixtures and expected
// draws are independent of the implementation; no reference code is imported.
function raidGear(raid, slot, mods) {
  const old = raid.state.equipment.find(item => item.slot === slot);
  raid.equipItem({ ...old, mods: { ...old.mods, ...mods } });
  return raid.state.equipment.find(item => item.slot === slot);
}
function raidUnits(seed, count = 12) {
  let state = Math.imul(seed, 16807) >>> 0;
  return Array.from({ length: count }, () => {
    state = Math.imul(state, 16807) >>> 0;
    return { seed: state, value: (state >>> 10) / 4194304 };
  });
}
function raidSeedFor(predicate) {
  for (let seed = 1; seed < 200000; seed++) {
    const units = raidUnits(seed);
    if (predicate(units.map(unit => unit.value))) return seed;
  }
  throw Error('No independent RNG fixture found');
}
const raidItemPools = {
  weapon: ['weaponDamage', 'baseDamage', 'leech', 'weaponPoison', 'pierce', 'xpPerSkull', 'strength', 'luck'],
  helmet: ['xpPerSkull', 'goldPerCoin', 'maxHp', 'maxArmor', 'spikes', 'upgradePerShield', 'regen'],
  armour: ['maxHp', 'maxArmor', 'spikes', 'durability', 'blunt', 'regen', 'dexterity', 'vitality'],
  shield: ['weaponDamage', 'baseDamage', 'maxArmor', 'spikes', 'upgradePerShield', 'blunt', 'armorPerShield'],
  accessory: ['quicken', 'strength', 'luck', 'maxHp', 'regen', 'dexterity', 'vitality'],
};
function forecastStartingShop(seed, { sauren = false, pretzel = false } = {}) {
  let state = Math.imul(seed, 16807) >>> 0;
  const integer = (lo, hi) => { const next = raidDraw(state, lo, hi); state = next.seed; return next.value; };
  const unit = () => { state = Math.imul(state, 16807) >>> 0; return (state >>> 10) / 4194304; };
  const suffixes = [[20, 15], [40, 12], [50, 13], [60, 12], [80, 15], [160, 17], [240, 16], [320, 17], [340, 14], [360, 11], [370, 14], [380, 16], [400, 16]];
  const ranges = { weapon: [0, 79], helmet: [80, 159], armour: [240, 319], shield: [160, 239], accessory: [320, 399] };
  const pool = Object.keys(ranges), items = [];
  for (let i = 0; i < 3; i++) {
    const slot = pool.splice(integer(0, pool.length - 1), 1)[0];
    const appearance = integer(...ranges[slot]);
    const displayPrefix = integer(0, 9);
    const suffixIndex = integer(0, suffixes.find(([end]) => appearance < end)[1] - 1);
    let double = false;
    if (slot === 'armour') double = unit() < Math.fround(.2);
    if (slot === 'accessory' && !pretzel) unit(); // Its final random stat is on UPGRADE.
    items.push({ slot, appearance, displayPrefix, suffixIndex, double });
  }
  const probability = unit(), hasScroll = probability < (sauren ? .5 : .25);
  const index = hasScroll ? integer(0, 2) : null;
  const exempt = hasScroll ? unit() < Math.fround(.1) : false;
  return { items, probability, hasScroll, index, exempt, seed: state };
}
function raidShopSeed(predicate, options) {
  for (let seed = 1; seed < 10000; seed++) if (predicate(forecastStartingShop(seed, options))) return seed;
  throw Error('No shop RNG fixture found');
}
function raidOpenShop(raid) { raid.state.hero.gold = 100; raid.checkGoldBox(); }
function raidAttachScroll(raid, id, index = 0) {
  assert.equal(raid.state.pendingModal, 'gold');
  raid.state.menuCandidates[index].scroll = { id };
  return raid.state.menuCandidates[index];
}

for (const [classId, mode, hp, armour] of [
  ['adventurer', 'classic', 50, 4], ['ranger', 'classic', 50, 3],
  ['mage', 'classic', 40, 4], ['warrior', 'classic', 60, 4], ['adventurer', 'pretzel', 100, 4],
]) test(`raid ${classId}/${mode}: real starting gear does not double-count base hero stats`, () => {
  const { raid } = raidBrowser({ classId, mode });
  const s = raid.state;
  assert.deepEqual(Array.from(s.equipment, item => item.slot), Object.keys(raidItemPools));
  assert.equal(s.equipment[0].mods.weaponDamage, 2);
  assert.equal(s.equipment[1].mods.maxArmor, 1);
  assert.equal(s.equipment[2].mods.maxArmor, classId === 'ranger' ? 1 : 2);
  assert.equal(s.equipment[3].mods.maxArmor, 1);
  assert.equal(s.equipment[4].mods.maxHp, mode === 'pretzel' ? 0 : 10);
  const before = JSON.stringify(s.hero);
  raid.updateEquipmentStats(); raid.updateEquipmentStats();
  assert.equal(JSON.stringify(s.hero), before, 'recomputation must be idempotent');
  assert.deepEqual([s.hero.hp, s.hero.maxHp, s.hero.armor, s.hero.maxArmor], [hp, hp, armour, armour]);
  assert.equal(raid.baseDamage(), 3);
  assert.ok(s.equipment.every(item => item.prefixAnchor === -1));
});

test('raid all20 equipment modifiers recompute HP, armour, attributes, combat and resource totals once', () => {
  const { raid } = raidBrowser();
  const s = raid.state;
  s.hero.hp = 20; s.hero.armor = 1;
  raidGear(raid, 'weapon', {
    weaponDamage: 3, baseDamage: 2, leech: .05, weaponPoison: 2, pierce: .1, quicken: 1,
    xpPerSkull: .25, goldPerCoin: .5, strength: 2, luck: 3, maxHp: 10, maxArmor: 2,
    spikes: 2, upgradePerShield: .75, durability: .1, blunt: 2, armorPerShield: 3,
    regen: 2, dexterity: 4, vitality: 2,
  });
  assert.deepEqual([s.hero.hp, s.hero.maxHp, s.hero.armor, s.hero.maxArmor], [40, 70, 3, 6]);
  assert.deepEqual([s.hero.stats.strength, s.hero.stats.luck, s.hero.stats.dexterity, s.hero.stats.vitality], [3, 4, 5, 3]);
  assert.deepEqual([raid.baseDamage(), s.hero.weaponDamage, s.armorPerShield], [7, 3, 4]);
  assert.deepEqual([s.leech, s.weaponPoison, s.pierce, s.quicken, s.durability, s.blunt, s.regen, s.spikes], [.05, 2, .6, 1, .6, 2, 2, 2]);
  assert.deepEqual([s.xpPerSkull, s.goldPerCoin, s.upgradePerShield], [2.25, 2.5, 2.75]);
  assert.deepEqual(['xp', 'coin', 'shield', 'potion'].map(type => raid.bonusPct(type)), [35, 40, 45, 35]);
  const snapshot = JSON.stringify([s.hero, s.equipmentApplied]);
  raid.updateEquipmentStats();
  assert.equal(JSON.stringify([s.hero, s.equipmentApplied]), snapshot);
  raid.equipItem({ slot: 'weapon', mods: { weaponDamage: 2 } });
  assert.deepEqual([s.hero.hp, s.hero.maxHp, s.hero.armor, s.hero.maxArmor], [40, 50, 1, 4], 'losing20 maxHP is not20 damage');
  assert.deepEqual([raid.baseDamage(), s.hero.stats.strength, s.hero.stats.luck, s.regen, s.pierce], [3, 1, 1, 0, .5]);
});

for (const hp of [20, 45]) test(`raid removing accessory maxHP clamps current${hp}, never deals flat damage`, () => {
  const { raid } = raidBrowser();
  raid.state.hero.hp = hp;
  raid.equipItem({ slot: 'accessory', mods: {} });
  assert.deepEqual([raid.state.hero.hp, raid.state.hero.maxHp], [Math.min(hp, 40), 40]);
});

test('raid one replacement preserves armour deficit without intermediate clamp/refill', () => {
  const { raid } = raidBrowser();
  raidGear(raid, 'armour', { maxArmor: 6 });
  raid.state.hero.armor = 2;
  raidGear(raid, 'armour', { maxArmor: 8 });
  assert.deepEqual([raid.state.hero.armor, raid.state.hero.maxArmor], [4, 10]);
  raidGear(raid, 'armour', { maxArmor: 1 });
  assert.deepEqual([raid.state.hero.armor, raid.state.hero.maxArmor], [0, 3]);
});

test('raid pierce/durability caps retain raw item overflow, with Cautious baseline30', () => {
  const { raid } = raidBrowser({ classId: 'priest' });
  raidGear(raid, 'weapon', { pierce: .7, durability: .7 });
  raidGear(raid, 'helmet', { pierce: .1, durability: .1 });
  assert.deepEqual([raid.state.pierce, raid.state.durability], [.95, .95]);
  raid.equipItem({ slot: 'weapon', mods: { weaponDamage: 2 } });
  assert.deepEqual([raid.state.pierce, raid.state.durability], [.4, .6]);
});

test('raid base bonus chance caps independently from equipment; removing gear cannot lose base overflow', () => {
  const { raid } = raidBrowser();
  raid.state.hero.stats.strength = 30;
  assert.equal(raid.bonusPct('xp'), 100);
  raidGear(raid, 'weapon', { strength: 2 });
  assert.equal(raid.bonusPct('xp'), 110);
  assert.equal(raid.bonusPct('xp', 5), 110);
  raidGear(raid, 'weapon', { strength: 0 });
  assert.equal(raid.bonusPct('xp'), 100);
});

for (const [mode, classId] of [['classic', 'adventurer'], ['sprint', 'adventurer'], ['pretzel', 'priest']]) {
  test(`raid ${mode}: every slot uses its sorted native upgrade pool and eligibility`, () => {
    const { raid } = raidBrowser({ mode, classId, seed: 913 });
    for (const [slot, full] of Object.entries(raidItemPools)) {
      const found = new Set();
      for (let n = 0; n < 400; n++) found.add(raid.makeUpgradeOffer(slot).key);
      const expected = full.filter(key => !(mode === 'sprint' && key === 'blunt') && !(mode === 'pretzel' && key === 'maxHp'));
      assert.deepEqual([...found].sort(), expected.slice().sort(), slot);
    }
    raidGear(raid, 'accessory', { quicken: 1 });
    for (let n = 0; n < 100; n++) assert.notEqual(raid.makeUpgradeOffer('accessory').key, 'quicken');
  });
}

for (const sprint of [false, true]) test(`raid random upgrade consumes ${sprint ? 'one' : 'two'} UPGRADE draws, not ITEM/OTHER`, () => {
  const { raid } = raidBrowser({ mode: sprint ? 'sprint' : 'classic', seed: 913 });
  const before = [...raid.state.rngSeeds];
  const weaken = sprint ? { seed: before[4] } : raidDraw(before[4], 0, 0);
  const choice = raidDraw(weaken.seed, 0, 7);
  const key = raid.randomItemUpgrade('weapon');
  assert.equal(key, raidItemPools.weapon[choice.value]);
  assert.equal(raid.state.rngSeeds[4], choice.seed);
  for (const lane of [0, 1, 2, 3, 5]) assert.equal(raid.state.rngSeeds[lane], before[lane]);
});

test('raid weaken probability filters15 even though other slots consume that same preliminary draw', () => {
  const { raid } = raidBrowser({ seed: 913 });
  raidGear(raid, 'shield', { blunt: 5 });
  let allowed = 0, filtered = 0;
  for (let n = 0; n < 100; n++) {
    const first = raidDraw(raid.state.rngSeeds[4], 0, 5);
    const pool = raidItemPools.shield.filter(key => key !== 'blunt' || first.value === 0);
    const second = raidDraw(first.seed, 0, pool.length - 1);
    assert.equal(raid.randomItemUpgrade('shield'), pool[second.value]);
    assert.equal(raid.state.rngSeeds[4], second.seed);
    if (first.value) filtered++; else allowed++;
  }
  assert.ok(allowed && filtered);
});

for (const [metric, total, up, down] of [
  ['leech', 14, 5, 5], ['leech', 15, 3, 5], ['leech', 17, 3, 5], ['leech', 18, 3, 3],
  ['leech', 20, 3, 3], ['leech', 21, 2, 3], ['leech', 22, 2, 3], ['leech', 23, 2, 2], ['leech', 24, 2, 2],
  ['durability', 69, 10, 10], ['durability', 70, 5, 10], ['durability', 74, 5, 10], ['durability', 75, 5, 5],
  ['durability', 79, 5, 5], ['durability', 80, 3, 5], ['durability', 82, 3, 5], ['durability', 83, 3, 3],
  ['durability', 85, 3, 3], ['durability', 86, 2, 3], ['durability', 87, 2, 3], ['durability', 88, 2, 2],
]) test(`raid ${metric}${total}: independent up${up}/down${down} bands`, () => {
  const { raid } = raidBrowser();
  raid.state[metric] = total / 100;
  assert.equal(raid.itemStep(metric), up / 100);
  assert.equal(raid.itemStep(metric, true), down / 100);
});

test('raid upgrade thresholds test existing totals without clipping the last legal increment', () => {
  const { raid } = raidBrowser();
  raidGear(raid, 'weapon', { leech: .24, pierce: .4 });
  assert.equal(raid.itemUpgradeAllowed('leech'), true);
  raid.applyUpgradeOffer({ slot: 'weapon', key: 'leech', value: raid.itemStep('leech') });
  assert.equal(raid.state.leech, .26);
  assert.equal(raid.itemUpgradeAllowed('leech'), false);
  raid.applyUpgradeOffer({ slot: 'weapon', key: 'pierce', value: raid.itemStep('pierce') });
  assert.equal(raid.state.equipment[0].mods.pierce, .5);
  assert.equal(raid.state.pierce, .95);
  assert.equal(raid.itemUpgradeAllowed('pierce'), false);
  raidGear(raid, 'armour', { durability: .39 });
  assert.equal(raid.itemUpgradeAllowed('durability'), true);
  raid.applyUpgradeOffer({ slot: 'armour', key: 'durability', value: raid.itemStep('durability') });
  assert.equal(raid.state.durability, .91);
  assert.equal(raid.itemUpgradeAllowed('durability'), false);
});

test('raid poison2, HP10, quarter-point, Faithful regen2 steps and Pretzel HP0 are effective, not labels', () => {
  const { raid } = raidBrowser({ classId: 'priest' });
  assert.equal(raid.itemStep('weaponPoison'), 2);
  assert.equal(raid.itemStep('maxHp'), 10);
  assert.equal(raid.itemStep('goldPerCoin'), .25);
  assert.equal(raid.itemStep('regen'), 2);
  raid.applyUpgradeOffer({ slot: 'helmet', key: 'regen', value: raid.itemStep('regen') });
  raidBoard(raid, 'sword'); raid.state.hero.hp = 10;
  raid.endEnemyTurn();
  assert.equal(raid.state.hero.hp, 12, 'Faithful step is doubled once, not again each turn');
  const item = raid.state.equipment[1];
  assert.equal(raid.reduceItemModifier(item, 'regen'), 2);
  assert.equal(raid.state.regen, 0);
  const pretzel = raidBrowser({ mode: 'pretzel' }).raid;
  assert.equal(pretzel.itemStep('maxHp'), 0);
  assert.equal(pretzel.itemUpgradeAllowed('maxHp'), false);
  raidGear(pretzel, 'accessory', { maxHp: 50, vitality: 3 });
  assert.deepEqual([pretzel.state.hero.hp, pretzel.state.hero.maxHp, pretzel.bonusPct('potion')], [100, 100, 40]);
});

test('raid upgrade menu interleaves slot/weakening/stat draws and applies the displayed offer', () => {
  const { raid, elements } = raidBrowser({ menus: true, seed: 913 });
  const slots = Object.keys(raidItemPools), expected = [];
  const before = [...raid.state.rngSeeds];
  let state = before[4];
  for (let n = 0; n < 4; n++) {
    const slotDraw = raidDraw(state, 0, slots.length - 1);
    const slot = slots.splice(slotDraw.value, 1)[0];
    const weaken = raidDraw(slotDraw.seed, 0, 0);
    const stat = raidDraw(weaken.seed, 0, raidItemPools[slot].length - 1);
    expected.push([slot, raidItemPools[slot][stat.value]]); state = stat.seed;
  }
  raid.state.upgradeBar = 100; raid.checkUpgradeBar();
  assert.equal(raid.state.rngSeeds[4], state);
  assert.deepEqual(Array.from(raid.state.menuCandidates, offer => [offer.slot, offer.key]), expected);
  assert.equal(raid.state.rngSeeds[5], before[5]);
  const offer = raid.state.menuCandidates[0], equipped = raid.state.equipment.find(item => item.slot === offer.slot);
  const old = equipped.mods[offer.key] || 0;
  const cards = elements.get('modal-body').querySelectorAll('.lv-card');
  assert.equal(cards.length, 4);
  assert.ok(cards[0].innerHTML.includes(offer.desc));
  assert.ok(cards[0].innerHTML.includes(offer.comparison));
  cards[0].click();
  assert.equal(equipped.mods[offer.key], Number((old + offer.value).toFixed(2)));
  assert.equal(raid.state.upgradeBar, 0);
});

test('raid shop interleaves whole item generation between distinct ITEM slot draws', () => {
  const seed = raidShopSeed(prediction => !prediction.hasScroll);
  const { raid } = raidBrowser({ seed });
  const expected = forecastStartingShop(seed), before = [...raid.state.rngSeeds];
  const items = raid.makeShopOffers();
  assert.equal(new Set(items.map(item => item.slot)).size, 3);
  expected.items.forEach((old, n) => {
    const actual = items[n];
    for (const key of ['slot', 'appearance', 'displayPrefix', 'suffixIndex']) assert.equal(actual[key], old[key], key);
    if (actual.slot === 'weapon') assert.equal(actual.mods.weaponDamage, 3);
    if (actual.slot === 'armour') assert.equal(actual.mods.maxArmor, old.double ? 4 : 3);
    if (actual.slot === 'helmet' || actual.slot === 'shield') assert.equal(actual.mods.maxArmor, 2);
  });
  assert.equal(raid.state.rngSeeds[2], expected.seed);
  for (const lane of [0, 1, 3, 5]) assert.equal(raid.state.rngSeeds[lane], before[lane]);
});

test('raid item appearance/name draws are separate from quality, including the stable high prefix anchor', () => {
  const { raid } = raidBrowser({ seed: 913 });
  const old = raid.state.equipment[0]; old.prefixAnchor = 140;
  const before = [...raid.state.rngSeeds];
  const icon = raidDraw(before[2], 0, 79), prefix = raidDraw(icon.seed, 140, 147);
  const pools = [[20, 15], [40, 12], [50, 13], [60, 12], [80, 15]];
  const suffix = raidDraw(prefix.seed, 0, pools.find(([end]) => icon.value < end)[1] - 1);
  const next = raid.makeItem('weapon');
  assert.deepEqual([next.appearance, next.prefixAnchor, next.displayPrefix, next.suffixIndex], [icon.value, 140, prefix.value, suffix.value]);
  assert.equal(next.mods.weaponDamage, 3, 'prefix does not add a quality-tier bonus');
  assert.equal(raid.state.rngSeeds[2], suffix.seed);
  assert.equal(raid.state.rngSeeds[4], before[4]);
  assert.doesNotMatch(next.name, /Novice|Torpid|Awful/);
});

for (const slot of Object.keys(raidItemPools)) test(`raid ${slot} generator transfers old secondary stats in ascending ID order`, () => {
  let predicate, mods, expected, itemDrawCount;
  if (slot === 'weapon') {
    predicate = u => u[3] < .15 && u[4] >= .15;
    mods = { baseDamage: 2, leech: .15 }; expected = { weaponDamage: 4, baseDamage: 1, leech: .15 }; itemDrawCount = 5;
  } else if (slot === 'helmet') {
    predicate = u => u[3] < .2 && u[4] < .5 && u[5] < .2;
    mods = { goldPerCoin: .25, spikes: 1 }; expected = { maxArmor: 3, goldPerCoin: .5 }; itemDrawCount = 6;
  } else if (slot === 'armour') {
    predicate = u => u[3] >= .2 && u[4] < .1 && u[5] >= .1;
    mods = { strength: 1, regen: 2 }; expected = { maxArmor: 4, regen: 2 }; itemDrawCount = 6;
  } else if (slot === 'shield') {
    predicate = u => u[3] < .7 && u[4] < .2 && u[5] < .2 && u[6] >= .2;
    mods = { weaponDamage: 2, baseDamage: 2, spikes: 1 }; expected = { maxArmor: 3, weaponDamage: 2, baseDamage: 1, spikes: 1 }; itemDrawCount = 7;
  } else {
    // First UPGRADE draw is weaken R(0,0); second chooses LUK (index1).
    predicate = u => u[3] < .1 && u[4] >= .1 && u[5] >= .5 && Math.floor(6 * u[1]) === 1;
    mods = { strength: 1, luck: 1 }; expected = { maxHp: 15, luck: 1 }; itemDrawCount = 6;
  }
  const seed = raidSeedFor(predicate), { raid } = raidBrowser({ seed });
  raidGear(raid, slot, mods);
  const before = [...raid.state.rngSeeds];
  const item = raid.makeItem(slot);
  assert.deepEqual({ ...item.mods }, expected);
  assert.equal(raid.state.rngSeeds[2], raidUnits(seed)[itemDrawCount - 1].seed);
  if (slot === 'accessory') assert.equal(raid.state.rngSeeds[4], raidUnits(seed)[1].seed);
  else assert.equal(raid.state.rngSeeds[4], before[4]);
});

test('raid armour20% double-main branch copies secondaries without drawing more probabilities', () => {
  const seed = raidSeedFor(u => u[3] < .2), { raid } = raidBrowser({ seed });
  raidGear(raid, 'armour', { strength: 2, regen: 3 });
  const item = raid.makeItem('armour');
  assert.deepEqual({ ...item.mods }, { maxArmor: 4, strength: 2, regen: 3 });
  assert.equal(raid.state.rngSeeds[2], raidUnits(seed)[3].seed);
});

test('raid Pretzel accessory has zero HP and always gets a random eligible non-HP stat', () => {
  const { raid } = raidBrowser({ mode: 'pretzel', seed: 913 });
  const before = [...raid.state.rngSeeds];
  const item = raid.makeItem('accessory');
  assert.equal(item.mods.maxHp || 0, 0);
  assert.equal(Object.values(item.mods).reduce((sum, value) => sum + value, 0), 1);
  assert.equal(raid.state.rngSeeds[2], raidUnits(913)[2].seed, 'no final50% ITEM draw in Pretzel');
  const weaken = raidDraw(before[4], 0, 0), selected = raidDraw(weaken.seed, 0, 5);
  assert.equal(raid.state.rngSeeds[4], selected.seed);
});

test('raid Devourer can reduce actual starting weapon, not an imaginary additive bonus', () => {
  const { raid } = raidBrowser({ seed: 0 }); raidBoard(raid);
  raidOld(raid, 0, { countdown: 1 }, 28);
  raid.applySpecialAbilities();
  assert.equal(raid.state.equipment[0].mods.weaponDamage, 1);
  assert.equal(raid.state.hero.weaponDamage, 1);
  assert.equal(raid.state.board[0].skull.countdown, 5);
});

test('raid Quicken is existential, idempotent, and removing the last copy preserves elapsed charge', () => {
  const { raid } = raidBrowser();
  raid.installSkillOption(raid.prepareSkillOption('heal'));
  raidGear(raid, 'weapon', { quicken: 1 });
  assert.deepEqual([raid.skillDuration('heal'), raid.state.skillCooldowns.heal], [14, 0]);
  raidGear(raid, 'accessory', { quicken: 2 }); raid.updateEquipmentStats();
  raidGear(raid, 'weapon', { quicken: 0 });
  assert.deepEqual([raid.skillDuration('heal'), raid.state.skillCooldowns.heal], [14, 0]);
  const accessory = raid.state.equipment[4];
  raid.reduceItemModifier(accessory, 'quicken');
  assert.equal(raid.skillDuration('heal'), 14, 'positive2→1 still has Quicken');
  raid.reduceItemModifier(accessory, 'quicken');
  assert.deepEqual([raid.skillDuration('heal'), raid.state.skillCooldowns.heal], [15, 1]);
});

test('raid Quicken and skill upgrade each shorten remaining turns, never reset a partial charge', () => {
  const { raid } = raidBrowser();
  raid.installSkillOption(raid.prepareSkillOption('heal'));
  raid.state.skillCooldowns.heal = 5; // CD15, elapsed10.
  raidGear(raid, 'accessory', { quicken: 1 });
  assert.deepEqual([raid.skillDuration('heal'), raid.state.skillCooldowns.heal], [14, 4]);
  const upgrade = raid.prepareSkillOption('heal');
  assert.deepEqual([upgrade.duration, upgrade.charge, upgrade.level], [13, 10, 2]);
  raid.installSkillOption(upgrade);
  assert.deepEqual([raid.skillDuration('heal'), raid.state.skillCooldowns.heal], [13, 3]);
  raidGear(raid, 'accessory', { quicken: 0 });
  assert.deepEqual([raid.skillDuration('heal'), raid.state.skillCooldowns.heal], [14, 4]);
});

test('raid new Quicken/Cunning fourth skill starts ready and discounts are not reapplied on upgrade', () => {
  const { raid } = raidBrowser({ classId: 'assassin' });
  raidGear(raid, 'accessory', { quicken: 1 });
  for (const id of ['steal', 'freeze', 'repair', 'heal']) raid.installSkillOption(raid.prepareSkillOption(id));
  assert.deepEqual([raid.skillDuration('heal'), raid.state.skillCooldowns.heal, raid.state.cooldownAdjust.heal], [13, 0, -1]);
  raid.state.skillCooldowns.heal = 5;
  raid.installSkillOption(raid.prepareSkillOption('heal'));
  assert.deepEqual([raid.skillDuration('heal'), raid.state.skillCooldowns.heal], [12, 4]);
});

test('raid level menu consumes SKILL draw before class-first override, then two distinct stat draws', () => {
  const { raid } = raidBrowser({ classId: 'ranger', seed: 913 });
  const s = raid.state; s.hero.level = 2;
  const pool = ['heal', 'repair', 'counter', 'shatter', 'teleport', 'explosive', 'elixir', 'enchant', 'treasure', 'scavenge', 'disarm', 'boostdmg', 'boosthealth', 'banish', 'trap', 'biggame', 'chamber', 'magicsword', 'mana', 'volley'];
  const before = [...s.rngSeeds], first = raidDraw(before[3], 0, 19), second = raidDraw(first.seed, 0, 18);
  const stat1 = raidDraw(second.seed, 0, 7), stat2 = raidDraw(stat1.seed, 0, 6);
  const stats = ['strength', 'dexterity', 'vitality', 'luck', 'damage', 'defense', 'health', 'charisma'];
  const a = stats.splice(stat1.value, 1)[0], b = stats.splice(stat2.value, 1)[0];
  const options = raid.chooseLevelOptions();
  assert.deepEqual(Array.from(options, opt => opt.id), ['volley', pool.filter(id => id !== 'volley')[second.value], a, b]);
  assert.equal(s.rngSeeds[3], stat2.seed);
  for (const lane of [0, 1, 2, 4, 5]) assert.equal(s.rngSeeds[lane], before[lane]);
  assert.equal(s.skills.length, 0);
});

test('raid class skill is forced only at level2, not repeatedly at later levels', () => {
  const { raid } = raidBrowser({ classId: 'ranger', seed: 913 });
  raid.state.hero.level = 3;
  const first = raidDraw(raid.state.rngSeeds[3], 0, 19);
  assert.notEqual(first.value, 19, 'independent fixture must not naturally draw Volley');
  const option = raid.chooseLevelOptions()[0];
  assert.notEqual(option.id, 'volley');
});

test('raid owned skill upgrades are offered before four slots and preserve charge through real level clicks', () => {
  const { raid, elements } = raidBrowser({ seed: 0, menus: true });
  const s = raid.state;
  raid.installSkillOption(raid.prepareSkillOption('steal')); s.skillCooldowns.steal = 5;
  s.hero.level = 2; s.hero.xp = 100; raid.checkLevelUp();
  assert.deepEqual(Array.from(s.menuCandidates.slice(0, 2), option => [option.id, option.upgrade]), [['steal', true], ['freeze', false]]);
  const cards = elements.get('modal-body').querySelectorAll('.lv-card');
  cards[0].click(); cards[1].click(); raidConfirmLevel(elements);
  assert.deepEqual(Array.from(s.skills), ['steal', 'freeze']);
  assert.deepEqual([raid.skillDuration('steal'), s.skillCooldowns.steal, s.skillLevels.steal], [12, 4, 2]);
  assert.equal(s.skillCooldowns.freeze, 0, 'new skill is fully ready');
});

test('raid third-slot ownership restricts a second candidate to upgrades after a new first candidate', () => {
  const { raid } = raidBrowser({ seed: 0 });
  for (const id of ['heal', 'repair', 'counter']) raid.installSkillOption(raid.prepareSkillOption(id));
  const options = raid.chooseLevelOptions();
  assert.deepEqual(Array.from(options.slice(0, 2), opt => [opt.id, opt.upgrade]), [['steal', false], ['heal', true]]);
  for (const id of raid.state.skills) raid.state.skillLevels[id] = 10;
  const next = raid.chooseLevelOptions();
  assert.equal(next.filter(option => 'native' in option).length, 1);
});

test('raid full mastered slots fill all four options with distinct stats and no skill draws', () => {
  const { raid } = raidBrowser({ seed: 913 });
  for (const id of ['heal', 'repair', 'counter', 'freeze']) {
    raid.installSkillOption(raid.prepareSkillOption(id)); raid.state.skillLevels[id] = 10;
  }
  let expected = raid.state.rngSeeds[3];
  for (const last of [7, 6, 5, 4]) expected = raidDraw(expected, 0, last).seed;
  const options = raid.chooseLevelOptions();
  assert.ok(options.every(option => !('native' in option)));
  assert.equal(new Set(options.map(option => option.id)).size, 4);
  assert.equal(raid.state.rngSeeds[3], expected);
});

test('raid Sprint filters owned skills and Trap; new level5 skills are immediately ready at baseCD-4', () => {
  const { raid } = raidBrowser({ mode: 'sprint', seed: 0 });
  raid.installSkillOption(raid.prepareSkillOption('steal'));
  for (let n = 0; n < 100; n++) {
    const options = raid.chooseLevelOptions();
    const skills = options.filter(option => 'native' in option);
    assert.ok(skills.every(option => option.id !== 'steal' && option.id !== 'trap'));
    assert.ok(skills.every(option => option.level === 5 && option.duration === option.cooldown - 4));
  }
  raid.installSkillOption(raid.prepareSkillOption('heal'));
  assert.deepEqual([raid.state.skillLevels.heal, raid.skillDuration('heal'), raid.state.skillCooldowns.heal], [5, 11, 0]);
  const priest = raidBrowser({ classId: 'priest', mode: 'sprint', seed: 0 }).raid;
  priest.state.hero.level = 2;
  assert.notEqual(priest.chooseLevelOptions()[0].id, 'wish', 'Sprint does not force the one-use Wish');
});

test('raid Pretzel level menu excludes Health/Teleport/Chamber but keeps vitality bonus-potion growth', () => {
  const { raid } = raidBrowser({ mode: 'pretzel', seed: 913 });
  let vitality = false;
  for (let n = 0; n < 100; n++) {
    const options = raid.chooseLevelOptions();
    assert.ok(options.every(option => !['health', 'teleport', 'chamber'].includes(option.id)));
    if (options.some(option => option.id === 'vitality')) vitality = true;
  }
  assert.equal(vitality, true);
});

test('raid Adventurer unlocks native31..39 only through their mastered class, never hidden40/41', () => {
  const { raid } = raidBrowser();
  assert.equal(raid.isSkillUnlocked('volley'), false);
  raid.unlockClass('ranger');
  for (let n = 0; n < 9; n++) raid.levelUpClass('ranger');
  assert.equal(raid.isSkillUnlocked('volley'), true);
  assert.equal(raid.isSkillUnlocked('berserk'), false);
  for (const id of ['adventurer', 'barbarian', 'mage', 'ranger', 'rogue', 'paladin', 'priest', 'raider', 'warrior', 'assassin']) {
    raid.unlockClass(id); for (let n = 0; n < 10; n++) raid.levelUpClass(id);
  }
  for (const id of ['shock', 'awe']) assert.equal(raid.isSkillUnlocked(id), false);
  const found = new Set();
  for (let n = 0; n < 300; n++) for (const option of raid.chooseLevelOptions()) if ('native' in option) found.add(option.native);
  assert.deepEqual([...found].sort((a, b) => a - b), Array.from({ length: 40 }, (_, n) => n));
});

test('raid shop shows three equipment buttons, inert fourth card, attached skill and clear before/after', () => {
  const { raid, elements } = raidBrowser({ seed: 0, menus: true }); raidBoard(raid, 'sword');
  raidOpenShop(raid);
  const body = elements.get('modal-body'), cards = body.querySelectorAll('.shop-item');
  assert.equal(cards.length, 4);
  assert.equal(cards.filter(card => typeof card.onclick === 'function').length, 3);
  assert.equal(cards[3]['aria-hidden'], 'true');
  assert.match(cards[0].innerHTML, /shop-scroll/);
  assert.match(cards[0].innerHTML, /разово при покупке/);
  assert.match(cards[0].innerHTML, /Урон оружия: 2 → 3/);
  assert.match(cards[0].innerHTML, /item-change better/);
  assert.ok(!cards[3].innerHTML, 'no fourth upgrade-scroll purchase');
  const seeds = [...raid.state.rngSeeds];
  body.querySelectorAll('.shop-summary-toggle')[0].click();
  body.querySelectorAll('.shop-summary-toggle')[0].click();
  assert.deepEqual(Array.from(raid.state.rngSeeds), seeds, 'viewing summary never rerolls offers');
});

test('raid confirmed scroll exemption may select an OWNED cooling-down skill and leaves its cooldown alone', () => {
  const { raid, elements } = raidBrowser({ seed: 0, menus: true }); raidBoard(raid, 'sword');
  const s = raid.state;
  raid.installSkillOption(raid.prepareSkillOption('goldtouch')); s.skillCooldowns.goldtouch = 7;
  raidOpenShop(raid);
  assert.equal(s.menuCandidates[0].scroll.id, 'goldtouch', 'lowest eligible native ID is6, even though owned');
  const beforeSkills = JSON.stringify([s.skills, s.skillCooldowns, s.skillLevels]);
  elements.get('modal-body').querySelectorAll('.shop-item')[0].click();
  assert.ok(s.board.every(cell => cell.type === 'coin'));
  assert.equal(JSON.stringify([s.skills, s.skillCooldowns, s.skillLevels]), beforeSkills);
  assert.equal(s.equipment[0].mods.weaponDamage, 3);
  assert.equal(s.equipment[0].scroll, undefined, 'the scroll is not a permanent item modifier');
  assert.equal(s.turn, 0);
  assert.equal(s.resolving, false);
});

test('raid fixture-only policy skips an unresolved non-exempt scroll, never inventing an owned-skill heuristic', () => {
  const seed = raidShopSeed(result => result.hasScroll && !result.exempt);
  const { raid, elements } = raidBrowser({ seed, menus: true }); raidBoard(raid, 'sword');
  const expected = forecastStartingShop(seed);
  raid.state.scrollPolicy = 'fixture-only';
  raidOpenShop(raid);
  assert.equal(raid.state.menuCandidates.filter(item => item.scrollBlocked).length, 1);
  assert.equal(raid.state.menuCandidates.filter(item => item.scroll).length, 0);
  assert.equal(raid.state.rngSeeds[2], expected.seed, 'unknown candidate choice must not be fabricated');
  assert.match(raid.state.compatibilityNotices.join('\n'), /I1/);
  const card = elements.get('modal-body').querySelectorAll('.shop-item')[expected.index];
  assert.match(card.innerHTML, /полных данных фильтра/);
  card.click();
  assert.equal(raid.state.pendingModal, null);
  assert.equal(raid.state.skills.length, 0);
});

test('raid full raw-word scroll fixture filters numeric native IDs, not equipped skill membership', () => {
  const seed = raidShopSeed(result => result.hasScroll && !result.exempt);
  const { raid } = raidBrowser({ seed, menus: true }); raidBoard(raid, 'sword');
  const s = raid.state;
  s.scrollNativeWords = Object.fromEntries(Array.from({ length: 42 }, (_, id) => [id, id === 16 ? 0 : 1]));
  raid.installSkillOption(raid.prepareSkillOption('boostdmg')); s.skillCooldowns.boostdmg = 8;
  raidOpenShop(raid);
  const withScroll = s.menuCandidates.filter(item => item.scroll);
  assert.equal(withScroll.length, 1);
  assert.equal(withScroll[0].scroll.id, 'boostdmg');
  assert.equal(s.rngSeeds[2], raidDraw(forecastStartingShop(seed).seed, 0, 0).seed);
  const index = s.menuCandidates.indexOf(withScroll[0]);
  assert.equal(raid.purchaseShopItem(index), true);
  assert.deepEqual([s.swordBoost, s.skillCooldowns.boostdmg, s.skills.length], [1, 8, 1]);
});

for (const fixture of ['partial', 'empty']) test(`raid ${fixture} native scroll fixture cannot crash or block the three equipment choices`, () => {
  const seed = raidShopSeed(result => result.hasScroll && !result.exempt);
  const { raid } = raidBrowser({ seed, menus: true });
  raid.state.scrollNativeWords = fixture === 'partial' ? { 16: 0 } : Object.fromEntries(Array.from({ length: 42 }, (_, id) => [id, 1]));
  raidOpenShop(raid);
  assert.equal(raid.state.menuCandidates.length, 3);
  assert.equal(raid.state.menuCandidates.filter(item => item.scrollBlocked).length, 1);
  assert.equal(raid.purchaseShopItem(0), true);
});

test('raid Sauren50 versus ordinary25 uses the same ITEM float draw, not OTHER randomness', () => {
  const seed = raidShopSeed(result => result.probability >= .25 && result.probability < .5);
  const human = raidBrowser({ seed }).raid, sauren = raidBrowser({ seed, classId: 'rogue' }).raid;
  raidBoard(human, 'sword'); raidBoard(sauren, 'sword');
  const h = human.makeShopOffers(), r = sauren.makeShopOffers();
  assert.ok(h.every(item => !item.scroll && !item.scrollBlocked));
  assert.equal(r.filter(item => item.scroll || item.scrollBlocked).length, 1);
  assert.equal(human.state.rngSeeds[5], sauren.state.rngSeeds[5]);
});

test('raid scroll CanActivate checks real targets and armour, but not equipped cooldowns', () => {
  const { raid } = raidBrowser(); raidBoard(raid, 'sword');
  for (const id of ['freeze', 'steal', 'berserk', 'heal', 'elixir', 'mana', 'repair', 'treasure', 'banish', 'disarm', 'greed', 'explosivearmour']) assert.equal(raid.canActivateSkill(id), false, id);
  assert.equal(raid.canActivateSkill('goldtouch'), true);
  assert.equal(raid.canActivateSkill('teleport'), true);
  raidOld(raid, 0, {}, 2);
  assert.equal(raid.canActivateSkill('banish'), true);
  assert.equal(raid.canActivateSkill('biggame'), false);
  raidOld(raid, 1); raid.state.board[2] = { type: 'coin' };
  assert.equal(raid.canActivateSkill('biggame'), true);
  assert.equal(raid.canActivateSkill('greed'), true);
  assert.equal(raid.canActivateSkill('explosivearmour'), true);
  raid.state.hero.armor = 0;
  assert.equal(raid.canActivateSkill('explosivearmour'), false);
  raid.state.board[3] = { type: 'potion', mana: true };
  assert.equal(raid.canActivateSkill('heal'), false);
  raid.state.board[3] = { type: 'potion', explosive: true };
  assert.equal(raid.canActivateSkill('heal'), false);
  raid.state.board[3] = { type: 'potion' };
  assert.equal(raid.canActivateSkill('heal'), true);
});

test('raid scroll equips/recomputes first and never calls Dragonkin manual Fire Breath', () => {
  const { raid } = raidBrowser({ classId: 'paladin', seed: 0, menus: true }); raidBoard(raid, 'sword');
  const s = raid.state, target = raidOld(raid, 35, { hp: 30, maxHp: 30, def: 0 });
  raid.installSkillOption(raid.prepareSkillOption('explosivearmour')); s.skillCooldowns.explosivearmour = 9;
  s.hero.armor = 1;
  raidOpenShop(raid);
  s.menuCandidates[0] = { slot: 'helmet', mods: { maxArmor: 3 }, scroll: { id: 'explosivearmour' } };
  assert.equal(raid.purchaseShopItem(0), true);
  assert.deepEqual([s.hero.maxArmor, s.hero.armor, target.hp], [6, 3, 24], 'explosion reads the purchased armour');
  assert.equal(!!target.burn, false);
  assert.deepEqual([s.skillCooldowns.explosivearmour, s.skills.length, s.turn], [9, 1, 0]);
});

test('raid Enchant scroll preserves queued arrivals and interrupted enemy continuation through the nested upgrade', () => {
  const { raid, elements, advance } = raidBrowser({ seed: 0, menus: true }); raidBoard(raid, 'sword');
  const s = raid.state;
  s.hero.gold = 99; s.upgradeBar = 37; s.forceCoins = 1;
  for (const slot of [0, 1, 2]) s.board[slot] = { type: 'coin' };
  raidSelect(raid, [0, 1, 2]); raid.resolveChain(); advance(420);
  assert.equal(s.pendingModal, 'gold'); assert.equal(s.arrivals.length, 2);
  raidAttachScroll(raid, 'enchant'); raid.purchaseShopItem(0);
  assert.equal(s.pendingModal, 'armor'); assert.equal(s.arrivals.length, 2);
  assert.equal(s.turn, 0); assert.equal(s.upgradeBar, 37);
  elements.get('modal-body').querySelectorAll('.lv-card')[0].click();
  advance(80);
  assert.equal(s.pendingModal, null); assert.equal(s.arrivals.length, 0);
  assert.deepEqual([s.hero.gold, s.goldEarned, s.upgradeBar, s.turn], [5, 6, 37, 0]);
  advance(550);
  assert.deepEqual([s.turn, s.resolving, s.actionCommitted], [1, false, false]);
  advance(3000); assert.equal(s.turn, 1, 'enemy continuation resumes exactly once');
});

test('raid collecting scroll from input drains its own rewards, refills and unlocks input without an enemy turn', () => {
  const { raid } = raidBrowser({ seed: 0, menus: true }); raidBoard(raid, 'sword');
  const s = raid.state;
  s.board[0] = { type: 'coin' }; s.forceCoins = 1;
  raidOpenShop(raid); raidAttachScroll(raid, 'treasure');
  raid.purchaseShopItem(0);
  assert.deepEqual([s.hero.gold, s.turn, s.resolving, s.pendingModal, s.arrivals.length], [2, 0, false, null, 0]);
  assert.ok(s.board.every(cell => cell.type !== 'empty'));
});

test('raid committed-turn boost scroll lasts through the next input, not just the interrupted animation', () => {
  const { raid, advance } = raidBrowser({ seed: 0, menus: true }); raidBoard(raid, 'sword');
  const s = raid.state;
  s.hero.gold = 99; s.forceCoins = 1;
  for (const slot of [0, 1, 2]) s.board[slot] = { type: 'coin' };
  raidSelect(raid, [0, 1, 2]); raid.resolveChain(); advance(420);
  raidAttachScroll(raid, 'boostdmg'); raid.purchaseShopItem(0); advance(700);
  assert.deepEqual([s.turn, s.swordBoost, s.nextTurnFlags.swordBoost], [1, 1, 0]);
  assert.equal(raid.swordDamage(2), 15, 'purchased weapon3 contributes doubled6 per sword');
  raid.endEnemyTurn();
  assert.equal(s.swordBoost, 0, 'expires after the following ordinary turn');
});

test('raid wrong-source, inert-slot and stale shop clicks cannot grant another free item', () => {
  const { raid, elements } = raidBrowser({ seed: 0, menus: true });
  assert.equal(raid.purchaseShopItem(0), false);
  raidOpenShop(raid);
  const old = elements.get('modal-body').querySelectorAll('.shop-item')[0];
  assert.equal(raid.purchaseShopItem(3), false);
  assert.equal(raid.purchaseShopItem(-1), false);
  old.click();
  raidOpenShop(raid);
  const snapshot = JSON.stringify(raid.state.equipment);
  old.click();
  assert.equal(JSON.stringify(raid.state.equipment), snapshot);
  assert.equal(raid.state.pendingModal, 'gold');
});

function raidRewardSpecial(raid, slot = 0, type = 2, hp = 1) {
  const skull = raidSkull(raid, { hp, maxHp: hp, def: 0 }, type);
  raid.state.board[slot] = { type: 'skull', skull };
  raid.collectIndices([slot], { includeSpecial: true, noRefill: true });
  return skull;
}
function raidMasterClass(raid, id) { raid.unlockClass(id); for (let n = 0; n < 10; n++) raid.levelUpClass(id); }
function raidUnlockAll(raid) {
  for (const id of ['adventurer', 'barbarian', 'mage', 'ranger', 'rogue', 'paladin', 'priest', 'raider', 'warrior', 'assassin']) raid.unlockClass(id);
}

test('raid first eligible special collection generates a board trophy with COSMETIC interval/appearance, not a modal', () => {
  const { raid } = raidBrowser({ seed: 913, menus: true }); raidBoard(raid, 'sword');
  const s = raid.state;
  assert.equal(s.trophyRemaining, 0);
  const skull = raidSkull(raid, { hp: 1, def: 0 }, 2); s.board[7] = { type: 'skull', skull };
  const before = [...s.rngSeeds], interval = raidDraw(before[0], 20, 30), appearance = raidDraw(interval.seed, 0, 7);
  raid.collectIndices([7], { includeSpecial: true, noRefill: true });
  assert.deepEqual([s.board[7].type, s.board[7].appearance], ['trophy', appearance.value]);
  assert.deepEqual([s.trophyRemaining, s.trophiesGenerated, s.trophiesThisRun], [interval.value, 1, 0]);
  assert.equal(s.rngSeeds[0], appearance.seed);
  assert.equal(s.rngSeeds[5], before[5], 'no class outcome rolled before the tap');
  assert.equal(s.pendingModal, null);
  assert.equal(s.skills.length, 0);
});

test('raid ordinary kills and non-reward removals never advance trophy countdown', () => {
  const { raid } = raidBrowser({ seed: 913 }); raidBoard(raid, 'sword');
  const s = raid.state; s.trophyRemaining = 2;
  raidOld(raid, 0, { hp: 1 }); raid.collectIndices([0], { noRefill: true });
  raidOld(raid, 1, { hp: 1 }, 2); raid.collectIndices([1], { includeSpecial: true, noRefill: true, rewardType: 'sword' });
  assert.deepEqual([s.trophyRemaining, s.trophiesGenerated, s.specialsKilled], [2, 0, 0]);
});

test('raid Easy special rewards do not decrement or create trophies, even with a due counter', () => {
  for (const remaining of [0, 3]) {
    const { raid } = raidBrowser({ difficulty: 0, seed: 913 }); raidBoard(raid, 'sword');
    const s = raid.state; s.trophyRemaining = remaining;
    const skull = raidSkull(raid, { hp: 1 }, 3); s.board[7] = { type: 'skull', skull };
    const cosmetic = s.rngSeeds[0];
    raid.collectIndices([7], { includeSpecial: true, noRefill: true });
    assert.deepEqual([s.trophyRemaining, s.trophiesGenerated, s.rngSeeds[0]], [remaining, 0, cosmetic]);
    assert.equal(s.board[7].type, 'empty');
  }
});

for (const roll of [49, 50]) test(`raid Sprint trophy decrement uses strict R(0,100,COSMETIC)<50 at${roll}`, () => {
  let seed;
  for (let candidate = 1; candidate < 10000; candidate++) {
    const flushed = Math.imul(candidate, 16807) >>> 0;
    const art = raidDraw(flushed, 0, 3);
    if (raidDraw(art.seed, 0, 100).value === roll) { seed = candidate; break; }
  }
  assert.ok(seed);
  const { raid } = raidBrowser({ mode: 'sprint', seed }); raidBoard(raid, 'sword');
  const s = raid.state; s.trophyRemaining = 1;
  const skull = raidSkull(raid, { hp: 1 }, 3); s.board[7] = { type: 'skull', skull };
  assert.equal(raidDraw(s.rngSeeds[0], 0, 100).value, roll);
  raid.collectIndices([7], { includeSpecial: true, noRefill: true });
  assert.equal(s.trophiesGenerated, roll === 49 ? 1 : 0);
  assert.equal(s.board[7].type, roll === 49 ? 'trophy' : 'empty');
  if (roll === 50) assert.equal(s.trophyRemaining, 1);
});

test('raid already-due Sprint trophy can appear even if the50/101 decrement fails', () => {
  const { raid } = raidBrowser({ mode: 'sprint', seed: 913 }); raidBoard(raid, 'sword');
  // The value is due before the probability test; either branch must award.
  raid.state.trophyRemaining = 0;
  raidRewardSpecial(raid, 7);
  assert.equal(raid.state.board[7].type, 'trophy');
  assert.equal(raid.state.trophiesGenerated, 1);
});

for (const policy of ['adventurer-unlocked', 'current-can-level', 'current-capped-locked', 'current-capped-unlocked']) {
  test(`raid trophy eligibility: ${policy}`, () => {
    const { raid } = raidBrowser({ classId: policy.startsWith('adventurer') ? 'adventurer' : 'barbarian' });
    raidBoard(raid, 'sword');
    if (!policy.endsWith('-locked')) raidUnlockAll(raid);
    if (policy.startsWith('current-capped')) raidMasterClass(raid, 'barbarian');
    raid.state.trophyRemaining = 0;
    raidRewardSpecial(raid, 7);
    const eligible = policy === 'current-can-level' || policy === 'current-capped-locked';
    assert.equal(raid.state.board[7].type, eligible ? 'trophy' : 'empty');
    assert.equal(raid.state.trophiesGenerated, eligible ? 1 : 0);
  });
}

test('raid three-GENERATED-trophy cap includes lost/unclaimed trophies and does not count unlocked classes', () => {
  const { raid } = raidBrowser({ seed: 0 }); raidBoard(raid, 'sword');
  const s = raid.state;
  for (const slot of [0, 1, 2, 3]) {
    s.trophyRemaining = 1;
    raidRewardSpecial(raid, slot);
    assert.equal(s.board[slot].type, slot < 3 ? 'trophy' : 'empty');
  }
  assert.deepEqual([s.trophiesGenerated, s.trophiesThisRun, s.unlocksThisRun, s.trophyRemaining], [3, 0, 0, 0]);
  raid.collectIndices([0, 1, 2], { noRefill: true });
  s.trophyRemaining = 1; raidRewardSpecial(raid, 4);
  assert.equal(s.board[4].type, 'empty', 'lost trophy does not reopen the limit');
});

test('raid remaining trophy interval carries over restart and local profile reload; per-run cap resets', () => {
  const b = raidBrowser({ seed: 0 }); const { raid, storage } = b; raidBoard(raid, 'sword');
  raidRewardSpecial(raid, 7);
  const interval = raid.state.trophyRemaining;
  assert.equal(interval, 20);
  raidRewardSpecial(raid, 8);
  assert.equal(raid.state.trophyRemaining, interval - 1);
  assert.equal(storage.get('raid:trophyRemaining'), String(interval - 1));
  raid.newGame(913);
  assert.deepEqual([raid.state.trophyRemaining, raid.state.trophiesGenerated], [interval - 1, 0]);
  const reloaded = raidBrowser({ seed: 913, saved: Object.fromEntries(storage) }).raid;
  assert.equal(reloaded.state.trophyRemaining, interval - 1);
  assert.equal(reloaded.state.rngSeeds[5], Math.imul(913, 16807) >>> 0, 'restart does not reroll a trophy target on OTHER');
});

test('raid tapping a single trophy unlocks a class, refills, clears selection and spends no enemy turn', () => {
  const { raid, advance } = raidBrowser({ seed: 0 }); raidBoard(raid, 'sword');
  const s = raid.state; s.forceCoins = 1;
  raidOld(raid, 35, { atk: 30 }); s.board[0] = { type: 'trophy' };
  raidSelect(raid, [0]); raid.resolveChain(); advance(3000);
  assert.equal(raid.isClassUnlocked('ranger'), true, 'native class1 is Ranger, not picker-order Barbarian');
  assert.deepEqual([s.trophiesThisRun, s.unlocksThisRun, s.turn, s.hero.hp, s.hero.armor, s.chainCount], [1, 1, 0, 50, 4, 0]);
  assert.equal(s.selected.length, 0);
  assert.ok(s.board.every(cell => cell.type !== 'empty'));
  assert.equal(s.pendingModal, null);
  assert.equal(s.resolving, false);
});

for (const branch of [0, 1]) test(`raid non-Adventurer trophy branch${branch}: random unlock versus current level`, () => {
  const seed = raidSeedFor(u => Math.floor(2 * u[0]) === branch);
  const { raid } = raidBrowser({ seed, classId: 'barbarian' }); raidBoard(raid, 'sword');
  raid.state.board[0] = { type: 'trophy' };
  const first = raidDraw(raid.state.rngSeeds[5], 0, 1);
  const second = branch ? first : raidDraw(first.seed, 0, 7);
  assert.equal(raid.claimTrophy(0), true);
  assert.equal(raid.classLevelOf('barbarian'), branch ? 2 : 1);
  assert.equal(raid.state.unlocksThisRun, branch ? 0 : 1);
  assert.equal(raid.state.rngSeeds[5], second.seed);
});

test('raid capped current class with no locked classes never levels a different unlocked class', () => {
  const { raid } = raidBrowser({ classId: 'barbarian', seed: 913 }); raidBoard(raid, 'sword');
  raidUnlockAll(raid); raidMasterClass(raid, 'barbarian');
  raid.state.board[0] = { type: 'trophy' };
  const other = raid.state.rngSeeds[5];
  assert.equal(raid.claimTrophy(0), true);
  assert.equal(raid.classLevelOf('barbarian'), 10);
  assert.equal(raid.classLevelOf('mage'), 1);
  assert.equal(raid.state.rngSeeds[5], other, 'no branch draw when current capped and nothing locked');
  assert.equal(raid.state.trophiesThisRun, 1);
});

test('raid frozen, matched, absent or suspended trophy taps are no-ops without class/RNG changes', () => {
  const { raid } = raidBrowser({ seed: 913 }); raidBoard(raid, 'sword');
  const s = raid.state, other = s.rngSeeds[5];
  for (const cell of [{ type: 'trophy', frozen: true }, { type: 'trophy', matched: true }, { type: 'coin' }]) {
    s.board[0] = cell; assert.equal(raid.claimTrophy(0), false);
  }
  s.board[0] = { type: 'trophy' };
  s.pendingModal = 'level'; assert.equal(raid.claimTrophy(0), false); s.pendingModal = null;
  s.resolving = true; assert.equal(raid.claimTrophy(0), false); s.resolving = false;
  for (const slot of [undefined, -1, 36, .5, '0']) assert.equal(raid.claimTrophy(slot), false);
  assert.deepEqual([s.rngSeeds[5], s.trophiesThisRun, raid.classLevelOf('barbarian')], [other, 0, 0]);
});

test('raid trophy cannot be appended after an arrow or normal resource; Chain priority remains native', () => {
  const { raid } = raidBrowser(); raidBoard(raid, 'sword');
  const s = raid.state;
  s.board[0] = { type: 'arrow' }; s.board[1] = { type: 'trophy' }; s.board[2] = { type: 'chain' };
  assert.equal(raid.canChainSelect(1, [0]), false);
  assert.equal(raid.canChainSelect(0, [1]), false);
  assert.equal(raid.canChainSelect(2, [1]), true, 'candidate Chain precedes previous Trophy check');
  assert.equal(raid.canChainSelect(1, []), true);
  s.board[1].frozen = true;
  assert.equal(raid.canChainSelect(1, []), false);
});

test('raid Lich attack transformation does not advance trophies; collecting its phylactery does', () => {
  const { raid } = raidBrowser({ seed: 0 }); raidBoard(raid, 'sword');
  raidOld(raid, 7, { hp: 1, def: 0 }, 32);
  raid.hitSkullAt(7, 10, { direct: true });
  assert.equal(raid.state.board[7].skull.phylactery, true);
  assert.equal(raid.state.trophiesGenerated, 0);
  raid.collectIndices([7], { includeSpecial: true, noRefill: true });
  assert.equal(raid.state.board[7].type, 'trophy');
  assert.equal(raid.state.trophiesGenerated, 1);
});

test('raid class profile sanitises excessive levels to10 before trophy and skill eligibility checks', () => {
  const { raid } = raidBrowser({ saved: { 'raid:classLevels': JSON.stringify({ adventurer: 999, barbarian: 12.5, mage: '10', rogue: '<script>', alien: 9 }) } });
  assert.deepEqual(['adventurer', 'barbarian', 'mage', 'rogue', 'alien'].map(id => raid.classLevelOf(id)), [10, 10, 10, 0, 0]);
  assert.equal(raid.isSkillUnlocked('berserk'), true);
  assert.equal(raid.isSkillUnlocked('rune'), true);
});

test('raid Russian help/styles describe real equipment scrolls, explicit I1 model limits and board trophies', () => {
  const css = readFileSync(new URL('./raid.css', import.meta.url), 'utf8');
  const template = readFileSync(new URL('./raid.html', import.meta.url), 'utf8');
  assert.match(css, /\.item-change\.worse/);
  assert.match(css, /\.cell\.type-trophy/);
  assert.match(template, /3 появления за забег/);
  assert.match(template, /счётчик уменьшается с шансом 50\/101/);
  assert.match(template, /10%-е исключения/);
  assert.match(template, /история надписи сохраняется между забегами в этой вкладке/);
  assert.match(template, /после перезагрузки восстанавливает историю именно сохранённого забега/);
  assert.match(template, /модель совместимости, не точная копия памяти и таймингов оригинала/);
  assert.doesNotMatch(template, /остальные свитки явно пропускаются/);
  assert.match(template, /кроме Желания в Спринте/);
  assert.match(template, /не занимает слот и не меняет заряд/);
});

test('raid trophy unlock vector follows native class IDs, never presentation order', () => {
  const native = ['ranger', 'raider', 'priest', 'barbarian', 'mage', 'rogue', 'paladin', 'warrior', 'assassin'];
  const { raid } = raidBrowser({ seed: 913 }); raidBoard(raid, 'sword');
  for (let n = 0; n < native.length; n++) {
    const locked = native.filter(id => !raid.isClassUnlocked(id));
    const choice = raidDraw(raid.state.rngSeeds[5], 0, locked.length - 1);
    raid.state.board[0] = { type: 'trophy' };
    assert.equal(raid.claimTrophy(0), true);
    assert.equal(raid.classLevelOf(locked[choice.value]), 1);
    assert.equal(raid.state.rngSeeds[5], choice.seed);
  }
});

test('raid generating offers never mutates equipped modifiers, health, cooldowns or prefix anchors', () => {
  const { raid } = raidBrowser({ seed: 913 });
  raidGear(raid, 'helmet', { goldPerCoin: .75, regen: 2 });
  raid.installSkillOption(raid.prepareSkillOption('heal'));
  raid.state.skillCooldowns.heal = 6;
  const before = JSON.stringify([raid.state.equipment, raid.state.hero, raid.state.skillCooldowns, raid.state.equipmentApplied]);
  for (const slot of Object.keys(raidItemPools)) { raid.makeItem(slot); raid.makeUpgradeOffer(slot); }
  raid.makeShopOffers();
  assert.equal(JSON.stringify([raid.state.equipment, raid.state.hero, raid.state.skillCooldowns, raid.state.equipmentApplied]), before);
});

test('raid item comparison exposes a secondary downgrade in red, not a blanket green quality claim', () => {
  const { raid, elements } = raidBrowser({ seed: 0, menus: true }); raidBoard(raid, 'sword');
  raidGear(raid, 'weapon', { baseDamage: 2 });
  raidOpenShop(raid);
  const card = elements.get('modal-body').querySelectorAll('.shop-item')[0];
  assert.match(card.innerHTML, /item-change worse[^>]*>Базовый урон: 2 → 1/);
  assert.match(card.innerHTML, /Урон оружия: 2 → 4/);
});

test('raid resting Enchant scroll pauses and resumes its own input continuation, without spending UP', () => {
  const { raid, elements, advance } = raidBrowser({ seed: 0, menus: true }); raidBoard(raid, 'sword');
  raid.state.upgradeBar = 89;
  raidOpenShop(raid); raidAttachScroll(raid, 'enchant'); raid.purchaseShopItem(0);
  assert.deepEqual([raid.state.pendingModal, raid.state.resolving, raid.state.turn], ['armor', true, 0]);
  elements.get('modal-body').querySelectorAll('.lv-card')[0].click(); advance(1000);
  assert.deepEqual([raid.state.pendingModal, raid.state.resolving, raid.state.turn, raid.state.upgradeBar], [null, false, 0, 89]);
});

test('raid restart during nested Enchant discards old shop/upgrade handlers and enemy continuation', () => {
  const { raid, elements, advance } = raidBrowser({ seed: 0, menus: true }); raidBoard(raid, 'coin');
  const old = raid.state; old.hero.gold = 99;
  raidSelect(raid, [0, 1, 2]); raid.resolveChain(); advance(420);
  raidAttachScroll(raid, 'enchant'); raid.purchaseShopItem(0);
  const stale = elements.get('modal-body').querySelectorAll('.lv-card')[0];
  raid.newGame(913);
  raid.state.upgradeBar = 100; raid.checkUpgradeBar();
  const before = JSON.stringify(raid.state.equipment);
  stale.click(); advance(5000);
  assert.equal(JSON.stringify(raid.state.equipment), before);
  assert.deepEqual([raid.state.turn, raid.state.pendingModal], [0, 'armor']);
});

test('raid Quicken transitions also retain the native negative duration of the one-use Wish', () => {
  const { raid } = raidBrowser({ classId: 'priest' });
  raid.installSkillOption(raid.prepareSkillOption('wish'));
  assert.equal(raid.skillDuration('wish'), -1);
  raidGear(raid, 'accessory', { quicken: 1 });
  assert.deepEqual([raid.skillDuration('wish'), raid.state.skillCooldowns.wish], [-2, 0]);
  raidGear(raid, 'accessory', { quicken: 0 });
  assert.deepEqual([raid.skillDuration('wish'), raid.state.skillCooldowns.wish], [-1, 1]);
});

test('raid trophy collection by an area effect is not a free class claim and cannot restore the generation budget', () => {
  const { raid } = raidBrowser({ seed: 0 }); raidBoard(raid, 'sword');
  raidRewardSpecial(raid, 7);
  raid.collectIndices([7], { noRefill: true });
  assert.equal(raid.state.board[7].type, 'empty');
  assert.deepEqual([raid.state.trophiesGenerated, raid.state.trophiesThisRun, raid.state.unlocksThisRun], [1, 0, 0]);
  assert.equal(raid.classLevelOf('ranger'), 0);
});

// The reported bug was a fixed +3 shop reward, not the legitimate generation
// of a new weapon one point above the one currently worn. Pin the same offer
// across separate shop visits to distinguish replacement from accumulation.
for (const [slot, key, value, total, expected] of [
  ['weapon', 'weaponDamage', 3, 'weaponDamage', 3],
  ['helmet', 'maxArmor', 2, 'maxArmor', 5],
  ['armour', 'maxArmor', 3, 'maxArmor', 5],
  ['shield', 'maxArmor', 2, 'maxArmor', 5],
  ['accessory', 'maxHp', 15, 'maxHp', 55],
]) test(`raid ${slot}: buying an identical item three times replaces its slot, never stacks its full value`, () => {
  const { raid } = raidBrowser({ seed: 0, menus: true }); raidBoard(raid, 'sword');
  const original = raid.state.equipment.find(item => item.slot === slot);
  const offer = { ...original, name: 'Повторный предмет', mods: { [key]: value } };
  const unchanged = JSON.stringify(raid.state.equipment.filter(item => item.slot !== slot));
  let hero;
  for (let n = 0; n < 3; n++) {
    raidOpenShop(raid);
    raid.state.menuCandidates[0] = offer;
    assert.equal(raid.purchaseShopItem(0), true);
    assert.equal(raid.state.hero[total], expected);
    assert.equal(raid.state.equipment.find(item => item.slot === slot).mods[key], value);
    assert.equal(raid.state.equipment.length, 5);
    assert.equal(new Set(raid.state.equipment.map(item => item.slot)).size, 5);
    assert.equal(JSON.stringify(raid.state.equipment.filter(item => item.slot !== slot)), unchanged);
    if (n) assert.equal(JSON.stringify(raid.state.hero), hero);
    hero = JSON.stringify(raid.state.hero);
  }
  assert.equal(offer.mods[key], value, 'the purchased offer is not a mutable equipped-item alias');
});

test('raid replacing a weapon removes its upgrades but keeps another slot and permanent character rewards', () => {
  const { raid } = raidBrowser({ seed: 0, menus: true }); raidBoard(raid, 'sword');
  const s = raid.state;
  s.hero.stats.damage = 2; // A level reward, not equipment.
  raidGear(raid, 'shield', { weaponDamage: 2 });
  raidGear(raid, 'weapon', { weaponDamage: 8, strength: 2, baseDamage: 4, regen: 1 });
  raid.applyUpgradeOffer({ slot: 'weapon', key: 'strength', value: 1 });
  assert.deepEqual([s.hero.weaponDamage, raid.baseDamage(), s.regen], [10, 14, 1]);
  raidOpenShop(raid);
  s.menuCandidates[0] = { slot: 'weapon', name: 'Булава', mods: { weaponDamage: 3 } };
  raid.purchaseShopItem(0);
  assert.deepEqual([s.hero.weaponDamage, raid.baseDamage(), s.regen, s.hero.stats.damage], [5, 7, 0, 2]);
  assert.equal(s.equipment.find(item => item.slot === 'shield').mods.weaponDamage, 2);
  assert.equal(s.equipment[0].mods.strength, undefined, 'no phantom upgraded strength survives replacement');
});

for (const [slot, key] of [
  ['weapon', 'baseDamage'], ['helmet', 'regen'], ['armour', 'dexterity'],
  ['shield', 'armorPerShield'], ['accessory', 'maxHp'],
]) test(`raid ${slot}: an upgrade belongs only to this worn item and is removed with it`, () => {
  const { raid } = raidBrowser();
  const s = raid.state, original = s.equipment.find(item => item.slot === slot);
  const replacement = { ...original, mods: { ...original.mods } };
  const others = JSON.stringify(s.equipment.filter(item => item.slot !== slot));
  const snapshot = () => JSON.stringify([s.hero, s.baseDamageBonus, s.regen, s.armorPerShield, s.equipmentApplied]);
  const before = snapshot(), amount = raid.itemStep(key);
  assert.equal(raid.applyUpgradeOffer({ slot, key, value: amount }), true);
  assert.equal(original.mods[key], (replacement.mods[key] || 0) + amount);
  assert.equal(JSON.stringify(s.equipment.filter(item => item.slot !== slot)), others);
  assert.notEqual(snapshot(), before);
  raid.equipItem(replacement);
  assert.equal(snapshot(), before, 'removing the item removes its upgrade, not permanent character stats');
});

test('raid successive newly generated weapons grow from the worn item, not from an additive shop reward', () => {
  const { raid } = raidBrowser({ seed: 0 });
  raidGear(raid, 'weapon', { weaponDamage: 8 });
  for (const expected of [9, 10, 11]) {
    const offer = raid.makeItem('weapon');
    assert.equal(offer.mods.weaponDamage, expected);
    raid.equipItem(offer);
    assert.equal(raid.state.hero.weaponDamage, expected);
  }
});

test('raid upgrade card changes exactly its displayed worn item, once even on a second click', () => {
  const { raid, elements } = raidBrowser({ seed: 913, menus: true });
  const s = raid.state, before = JSON.parse(JSON.stringify(s.equipment));
  s.upgradeBar = 100; raid.checkUpgradeBar();
  const opt = s.menuCandidates[0], card = elements.get('modal-body').querySelectorAll('.lv-card')[0];
  assert.ok(card.innerHTML.includes(before.find(item => item.slot === opt.slot).name));
  card.click();
  s.equipment.forEach((item, index) => {
    const expected = before[index];
    if (item.slot === opt.slot) expected.mods[opt.key] = Number(((expected.mods[opt.key] || 0) + opt.value).toFixed(2));
    assert.equal(JSON.stringify(item), JSON.stringify(expected));
  });
  const after = JSON.stringify([s.equipment, s.hero]);
  card.click();
  assert.equal(JSON.stringify([s.equipment, s.hero]), after);
});

test('raid standalone displays a reproducible source fingerprint instead of an unidentifiable cached build', () => {
  const hash = createHash('sha256');
  for (const name of ['build_raid.py', 'raid.html', 'raid.css', 'raid.js', 'skull_sprites.json']) {
    hash.update(name + '\0'); hash.update(readFileSync(new URL('./' + name, import.meta.url))); hash.update('\0');
  }
  const id = hash.digest('hex').slice(0, 12);
  const html = readFileSync(new URL('../lunapark-raid.html', import.meta.url), 'utf8');
  assert.ok(html.includes(`<meta name="raid-build" content="${id}">`));
  assert.equal((html.match(new RegExp('Сборка ' + id, 'g')) || []).length, 3, 'picker, menu and help identify the same build');
  assert.doesNotMatch(html, /__RAID_BUILD__/);
});

// Level choices are a draft until an explicit confirmation, unlike buying a
// replacement or adding a modifier to a worn item.
test('raid level draft can be changed after both picks without applying stats, skills or RNG', () => {
  const { raid, elements, advance } = raidBrowser({ menus: true, seed: 0 });
  raid.state.hero.xp = 100; raid.checkLevelUp();
  const s = raid.state, body = elements.get('modal-body');
  const cards = body.querySelectorAll('.lv-card'), confirm = body.querySelectorAll('.lv-confirm')[0];
  assert.ok(confirm, 'level choices need their own confirmation button');
  assert.equal(confirm.disabled, true);
  const snapshot = () => JSON.stringify([s.hero, s.equipment, s.skills, s.skillLevels, s.skillCooldowns, s.skillDurations, s.rngSeeds]);
  const before = snapshot();
  cards[0].click(); cards[2].click(); advance(10000);
  assert.equal(confirm.disabled, false);
  assert.equal(s.pendingModal, 'level', 'two picks alone must not close/apply the menu');
  assert.equal(snapshot(), before);
  assert.equal(cards[0]['aria-pressed'], 'true');
  assert.equal(cards[2]['aria-pressed'], 'true');
  cards[3].click(); // At most two; remove a selected option before adding another.
  assert.equal(cards[3]['aria-pressed'], 'false');
  cards[0].click();
  assert.equal(cards[0]['aria-pressed'], 'false');
  assert.equal(confirm.disabled, true);
  cards[3].click();
  assert.equal(confirm.disabled, false);
  assert.equal(snapshot(), before, 'deselection/reselection must not mutate the run');
  confirm.click();
  assert.equal(s.pendingModal, null);
  assert.equal(s.skills.length, 0, 'the cancelled skill was never installed');
  assert.deepEqual([s.hero.stats.strength, s.hero.stats.dexterity], [2, 2]);
  const after = snapshot();
  confirm.click(); cards[0].click(); advance(1000);
  assert.equal(snapshot(), after, 'old buttons cannot apply the reward twice');
});

for (const count of [0, 1]) test(`raid level cannot confirm ${count} picks, even through a direct handler call`, () => {
  const { raid, elements } = raidBrowser({ menus: true, seed: 0 });
  raid.state.hero.xp = 100; raid.checkLevelUp();
  const body = elements.get('modal-body'), confirm = body.querySelectorAll('.lv-confirm')[0];
  assert.ok(confirm);
  if (count) body.querySelectorAll('.lv-card')[0].click();
  const before = JSON.stringify(raid.state);
  confirm.click();
  assert.equal(confirm.disabled, true);
  assert.equal(JSON.stringify(raid.state), before);
  assert.equal(raid.state.pendingModal, 'level');
});

test('raid level confirmation retains the final selection order, not the order of cancelled picks', () => {
  const { raid, elements } = raidBrowser({ menus: true, seed: 0 });
  raid.state.hero.xp = 100; raid.checkLevelUp();
  const body = elements.get('modal-body'), cards = body.querySelectorAll('.lv-card');
  cards[0].click(); cards[1].click(); cards[0].click(); cards[0].click();
  assert.equal(raid.state.skills.length, 0);
  body.querySelectorAll('.lv-confirm')[0].click();
  assert.deepEqual(Array.from(raid.state.skills), ['freeze', 'steal']);
});

test('raid stale level picks and confirmation are invalid after restart, including another open level menu', () => {
  const { raid, elements, advance } = raidBrowser({ menus: true, seed: 0 });
  raid.state.hero.xp = 100; raid.checkLevelUp();
  const body = elements.get('modal-body'), oldCards = body.querySelectorAll('.lv-card');
  const oldConfirm = body.querySelectorAll('.lv-confirm')[0];
  assert.ok(oldConfirm);
  oldCards[0].click(); oldCards[1].click();
  raid.newGame(913); raid.state.hero.xp = 100; raid.checkLevelUp();
  const freshCards = body.querySelectorAll('.lv-card'), freshConfirm = body.querySelectorAll('.lv-confirm')[0];
  freshCards[0].click(); freshCards[1].click();
  const before = JSON.stringify(raid.state);
  oldCards[0].click(); oldConfirm.click(); advance(1000);
  assert.equal(JSON.stringify(raid.state), before);
  assert.equal(raid.state.pendingModal, 'level');
  freshConfirm.click();
  assert.equal(raid.state.pendingModal, null);
  assert.equal(raid.state.skills.length, 2);
});

test('raid each queued level has a fresh empty draft and an old confirmation cannot confirm the next one', () => {
  const { raid, elements, advance } = raidBrowser({ menus: true, seed: 913 });
  raid.state.hero.xp = 100; raid.checkLevelUp();
  const body = elements.get('modal-body'), cards = body.querySelectorAll('.lv-card');
  const oldConfirm = body.querySelectorAll('.lv-confirm')[0];
  assert.ok(oldConfirm);
  raid.state.rewardMenus.push('level'); // Isolate two adjacent queued reward menus.
  cards[2].click(); cards[3].click(); oldConfirm.click(); advance(80);
  assert.equal(raid.state.pendingModal, 'level');
  const confirm = body.querySelectorAll('.lv-confirm')[0];
  assert.notEqual(confirm, oldConfirm);
  assert.equal(confirm.disabled, true);
  assert.ok(body.querySelectorAll('.lv-card').every(card => card['aria-pressed'] === 'false'));
  const before = JSON.stringify(raid.state);
  oldConfirm.click();
  assert.equal(JSON.stringify(raid.state), before);
});

for (const mode of ['classic', 'sprint', 'pretzel']) test(`raid ${mode}: successive item upgrades only add one modifier and preserve all other worn properties`, () => {
  const { raid } = raidBrowser({ mode, seed: 913 });
  for (const slot of Object.keys(raidItemPools)) {
    for (let n = 0; n < 40; n++) {
      const item = raid.state.equipment.find(item => item.slot === slot);
      const before = JSON.parse(JSON.stringify(item));
      const others = JSON.stringify(raid.state.equipment.filter(item => item.slot !== slot));
      const offer = raid.makeUpgradeOffer(slot);
      assert.ok(offer.value > 0 && Number.isFinite(offer.value));
      assert.equal(raid.applyUpgradeOffer(offer), true);
      assert.equal(item.name, before.name);
      assert.equal(item.slot, before.slot);
      const expected = { ...before.mods, [offer.key]: Number(((before.mods[offer.key] || 0) + offer.value).toFixed(2)) };
      assert.equal(JSON.stringify(item.mods), JSON.stringify(expected));
      for (const key of Object.keys(before.mods)) assert.ok(item.mods[key] >= before.mods[key], key);
      assert.equal(JSON.stringify(raid.state.equipment.filter(item => item.slot !== slot)), others);
    }
  }
});

test('raid item-upgrade entry point rejects non-additive or invalid offers, without changing the item', () => {
  const { raid } = raidBrowser();
  const before = JSON.stringify(raid.state);
  const offers = [-1, 0, NaN, Infinity, -Infinity, undefined, '1']
    .map(value => ({ slot: 'weapon', key: 'weaponDamage', value }));
  offers.push(undefined, null, {}, { slot: 'missing', key: 'weaponDamage', value: 1 });
  for (const key of ['missing', 'toString', '__proto__', undefined]) offers.push({ slot: 'weapon', key, value: 1 });
  for (const offer of offers) {
    assert.equal(raid.applyUpgradeOffer(offer), false);
    assert.equal(JSON.stringify(raid.state), before);
  }
});

test('raid shop can trade one base-damage point for a double weapon increase; an upgrade never makes that trade', () => {
  const { raid, elements } = raidBrowser({ menus: true, seed: 0 }); raidBoard(raid, 'sword');
  raidGear(raid, 'weapon', { baseDamage: 2 });
  assert.deepEqual([raid.state.hero.weaponDamage, raid.baseDamage()], [2, 5]);
  raidOpenShop(raid);
  const item = raid.state.menuCandidates[0], card = elements.get('modal-body').querySelectorAll('.shop-item')[0];
  assert.equal(item.slot, 'weapon');
  assert.equal(JSON.stringify(item.mods), JSON.stringify({ weaponDamage: 4, baseDamage: 1 }));
  assert.match(card.innerHTML, /item-change better[^>]*>Урон оружия: 2 → 4 \(\+2\)/);
  assert.match(card.innerHTML, /item-change worse[^>]*>Базовый урон: 2 → 1 \(−1\)/);
  card.click();
  assert.deepEqual([raid.state.hero.weaponDamage, raid.baseDamage()], [4, 4]);
  raid.state.upgradeBar = 100; raid.checkUpgradeBar();
  const upgrade = raid.state.menuCandidates[0];
  assert.equal(upgrade.slot, 'weapon');
  assert.equal(upgrade.key, 'weaponDamage');
  elements.get('modal-body').querySelectorAll('.lv-card')[0].click();
  assert.deepEqual([raid.state.hero.weaponDamage, raid.baseDamage(), raid.state.equipment[0].mods.baseDamage], [5, 4, 1]);
});

// Class growth edits the next-run configuration, never the live character.
const raidClassIds = ['adventurer', 'ranger', 'raider', 'priest', 'barbarian', 'mage', 'rogue', 'paladin', 'warrior', 'assassin'];
function raidClassLevel(raid, id, level = 10) {
  raid.unlockClass(id);
  while (raid.classLevelOf(id) < level) raid.levelUpClass(id);
}
function raidMasterClasses(raid) { for (const id of raidClassIds) raidClassLevel(raid, id); }
const raidClassVectors = {
  ranger: [31,2,3,4,5,7,8,9,10,12,13,14,16,18,21,22,23,24,25,29],
  raider: [32,2,3,4,6,8,9,11,12,15,16,17,18,19,20,22,23,24,25,27],
  priest: [33,1,2,5,6,7,8,9,10,12,13,15,18,19,21,20,25,26,28,29],
  barbarian: [34,3,4,5,7,9,10,11,12,13,14,16,18,19,21,20,22,23,25,27],
  mage: [35,1,2,3,6,7,8,9,10,12,15,17,18,21,20,24,25,26,28,29],
  rogue: [36,0,2,3,4,6,8,9,10,11,12,13,14,15,17,18,22,23,24,25],
  paladin: [37,2,3,4,5,8,9,11,12,13,14,16,18,19,21,20,23,24,25,27],
  warrior: [38,2,3,4,5,6,7,10,11,12,13,14,15,16,19,21,22,23,25,27],
  assassin: [39,0,1,4,5,7,8,9,12,14,15,16,18,21,22,23,24,25,27,28],
};
for (const [classId, vector] of Object.entries(raidClassVectors)) test(`raid class editor preserves ${classId}'s ordered twenty-slot native vector`, () => {
  const { raid } = raidBrowser({ classId });
  const info = raid.classEditorInfo(classId), config = raid.getClassConfig(classId);
  assert.deepEqual(Array.from(info.baseSkills, id => raid.prepareSkillOption(id).native), vector);
  assert.equal(config.perks.length, 4); assert.equal(config.flaws.length, 4);
  assert.equal(config.replacements.length, 20);
  assert.deepEqual(Array.from(raid.classSkillPool(), id => raid.prepareSkillOption(id).native), [...vector].sort((a,b) => a-b));
  assert.equal(raid.state.skills.length, 0, 'pool entries are not equipped at start');
});

test('raid class editor uses class levels: pairs3/7/9, race5, two replacements per even level', () => {
  for (let level = 1; level <= 10; level++) {
    const { raid } = raidBrowser({ classId: 'barbarian' }); raidClassLevel(raid, 'barbarian', level);
    const rules = raid.classRules('barbarian');
    assert.equal(rules.replacementLimit, 2 * Math.floor(level / 2));
    assert.equal(rules.canChangeRace, level >= 5);
    assert.deepEqual(Array.from(rules.pairSlots), [true, level>=3, level>=7, level>=9]);
  }
});

test('raid editor donor gates use default class identity: traits1, races5, unique skills10', () => {
  const { raid } = raidBrowser({ classId: 'barbarian' });
  assert.equal(raid.isTraitUnlocked('perks', 'Unexpected'), true);
  assert.equal(raid.isTraitUnlocked('flaws', 'Confused'), true);
  assert.equal(raid.isRaceUnlocked('Human'), true);
  assert.equal(raid.isTraitUnlocked('perks', 'Gifted'), false);
  raid.unlockClass('mage');
  assert.equal(raid.isTraitUnlocked('perks', 'Gifted'), true);
  assert.equal(raid.isRaceUnlocked('Changeling'), false);
  raidClassLevel(raid, 'mage', 5);
  assert.equal(raid.isRaceUnlocked('Changeling'), true);
  assert.equal(raid.isSkillUnlocked('rune'), false);
  raidClassLevel(raid, 'mage'); assert.equal(raid.isSkillUnlocked('rune'), true);
  for (const id of ['<img>', '__proto__', undefined]) assert.equal(raid.isRaceUnlocked(id), false);
});

test('raid editor leaves the primary pair and unique skill fixed, caps replacements and strips invalid/locked fields', () => {
  const { raid } = raidBrowser({ classId: 'barbarian' }); raidClassLevel(raid, 'barbarian', 3);
  const config = raid.getClassConfig('barbarian');
  config.race = 'Changeling'; config.perks = ['Gifted','Unexpected','Tough','Frugal'];
  config.flaws = ['Frail','Confused','Noisy','Clumsy'];
  config.replacements.fill('steal');
  assert.equal(raid.saveClassConfig('barbarian', config), true);
  const saved = raid.getClassConfig('barbarian');
  assert.equal(saved.race, 'Orc');
  assert.deepEqual(Array.from(saved.perks), ['Strong','Unexpected',null,null]);
  assert.deepEqual(Array.from(saved.flaws), ['Reckless','Confused',null,null]);
  assert.equal(saved.replacements[0], null);
  assert.equal(saved.replacements.filter(Boolean).length, 2);
  assert.equal(raid.saveClassConfig('mage', config), false, 'locked class cannot be configured');
});

test('raid class validation removes duplicate traits/unpaired perks but permits a flaw without a perk', () => {
  const { raid } = raidBrowser({ classId: 'barbarian' }); raidMasterClasses(raid);
  const config = raid.getClassConfig('barbarian');
  config.perks = ['Strong','Strong','Gifted','Tough']; config.flaws = ['Reckless','Frail',null,'Noisy'];
  raid.saveClassConfig('barbarian', config);
  const saved = raid.getClassConfig('barbarian');
  assert.deepEqual(Array.from(saved.perks), ['Strong',null,null,'Tough']);
  assert.deepEqual(Array.from(saved.flaws), ['Reckless','Frail',null,'Noisy']);
  config.perks = ['Strong','Gifted','Tough','Frugal']; config.flaws = ['Reckless','Frail','Frail','Clumsy'];
  raid.saveClassConfig('barbarian', config);
  assert.deepEqual(Array.from(raid.getClassConfig('barbarian').perks), ['Strong','Gifted',null,'Frugal']);
});

test('raid class edits save/reload isolated copies and affect only the next run snapshot', () => {
  const { raid, storage } = raidBrowser({ classId: 'barbarian' }); raidMasterClasses(raid);
  const before = JSON.stringify(raid.state), original = raid.classSkillPool().join();
  const config = raid.getClassConfig('barbarian');
  config.race = 'Faun'; config.perks[1] = 'Tough'; config.flaws[1] = 'Frail'; config.replacements[1] = 'steal';
  assert.equal(raid.saveClassConfig('barbarian', config), true);
  config.perks[1] = 'Gifted';
  assert.equal(JSON.stringify(raid.state), before);
  assert.equal(raid.classSkillPool().join(), original);
  assert.equal(raid.getClassConfig('barbarian').perks[1], 'Tough');
  const reload = raidBrowser({ classId: 'barbarian', saved: Object.fromEntries(storage) }).raid;
  assert.equal(reload.clsRace(), 'Faun'); assert.equal(reload.hasPerk('Tough'), true);
  assert.equal(reload.classSkillPool().includes('steal'), true);
  assert.equal(reload.classSkillPool().includes('repair'), false);
  raid.newGame(913);
  assert.equal(raid.clsRace(), 'Faun'); assert.equal(raid.hasFlaw('Frail'), true);
  assert.equal(Object.isFrozen(raid.state.classConfig), true);
  assert.equal(Object.isFrozen(raid.state.classConfig.perks), true);
});

test('raid replacement pool is an ascending unique set, not extra skill weighting; first skill stays class-specific', () => {
  const { raid } = raidBrowser({ classId: 'barbarian' }); raidMasterClasses(raid);
  const config = raid.getClassConfig('barbarian');
  config.replacements[0] = 'rune'; config.replacements[1] = 'rune'; config.replacements[2] = 'rune';
  config.replacements[3] = 'awe'; config.replacements[4] = 'shock';
  raid.saveClassConfig('barbarian', config); raid.newGame(913);
  const pool = Array.from(raid.classSkillPool());
  assert.equal(pool.filter(id => id === 'rune').length, 1);
  assert.equal(pool.includes('awe'), false); assert.equal(pool.includes('shock'), false);
  assert.ok(pool.includes('berserk'));
  raid.state.hero.level = 2; assert.equal(raid.chooseLevelOptions()[0].id, 'berserk');
});

test('raid Adventurer stays Human without traits/replacements and uses the live global unlocked skill pool', () => {
  const { raid } = raidBrowser(); raidMasterClasses(raid);
  const config = raid.getClassConfig('adventurer');
  config.race='Orc'; config.perks.fill('Strong'); config.flaws.fill('Reckless'); config.replacements.fill('rune');
  assert.equal(raid.saveClassConfig('adventurer', config), false);
  raid.newGame(0);
  assert.equal(raid.clsRace(), 'Human'); assert.equal(raid.hasPerk('Strong'), false);
  assert.equal(raid.classRules('adventurer').editable, false);
  assert.deepEqual(Array.from(raid.classSkillPool(), id => raid.prepareSkillOption(id).native), Array.from({length:40}, (_,n)=>n));
});

for (const raw of ['broken', 'null', '[]', '{"version":9,"classes":{}}', '{"version":1,"classes":[]}']) test(`raid malformed class configuration storage is harmless: ${raw}`, () => {
  const { raid } = raidBrowser({ saved: { 'raid:classConfigs': raw } });
  assert.equal(raid.clsRace(), 'Human'); assert.equal(raid.baseDamage(), 3);
  assert.equal(raid.getClassConfig('adventurer').perks.filter(Boolean).length, 0);
});

test('raid external class config values cannot inject names, unlock content or prototype keys', () => {
  const raw = '{"version":1,"classes":{"barbarian":{"race":"<img src=x>","perks":["Strong","toString","Gifted",null],"flaws":["Reckless","Confused","Frail",null],"replacements":["rune","rune","awe"]},"__proto__":{"race":"Orc"}}}';
  const { raid } = raidBrowser({ classId:'barbarian', saved:{'raid:classConfigs':raw} });
  assert.equal(raid.clsRace(), 'Orc');
  assert.deepEqual(Array.from(raid.state.classConfig.perks), ['Strong',null,null,null]);
  assert.deepEqual(Array.from(raid.state.classConfig.replacements), Array(20).fill(null));
  for (const id of ['toString','__proto__','alien']) assert.equal(raid.saveClassConfig(id, {}), false);
  const config=raid.getClassConfig('barbarian');config.replacements[1]={toString:'not a function',valueOf:null};
  assert.equal(raid.saveClassConfig('barbarian',config),true);
  assert.deepEqual(Array.from(raid.getClassConfig('barbarian').replacements),Array(20).fill(null));
});

test('raid configured extra traits affect HP/armour/base/chances/pierce/item steps together without stacking', () => {
  const { raid } = raidBrowser({ classId:'barbarian' }); raidMasterClasses(raid);
  const config = raid.getClassConfig('barbarian');
  config.perks = ['Strong','Tough','Frugal','Faithful']; config.flaws = ['Reckless','Frail','Cautious','Slim'];
  raid.saveClassConfig('barbarian',config); raid.newGame(71);
  assert.deepEqual([raid.state.hero.maxHp,raid.state.hero.hp,raid.state.hero.maxArmor,raid.state.hero.armor],[50,50,3,3]);
  assert.deepEqual([raid.baseDamage(),raid.bonusPct('coin'),raid.bonusPct('shield'),raid.state.pierce,raid.itemStep('regen')],[4,35,15,.3,2]);
  for (const name of [null,'—','missing']) assert.equal(raid.hasPerk(name),false);
});

test('raid extra Gifted/Agile/Clumsy use all three resource decorations in the live run', () => {
  const { raid } = raidBrowser({ classId:'barbarian' }); raidMasterClasses(raid);
  const config=raid.getClassConfig('barbarian');
  config.perks[1]='Gifted';config.perks[2]='Agile';config.flaws[1]='Clumsy';config.flaws[2]='Sneaky';
  raid.saveClassConfig('barbarian',config);raid.newGame(913);
  const found=new Set();
  for(let n=0;n<3000;n++){const cell=raid.createTile();if(cell.mana)found.add('mana');if(cell.type==='arrow')found.add('arrow');if(cell.broken)found.add('broken');}
  assert.deepEqual([...found].sort(),['arrow','broken','mana']);
  assert.equal(raid.makeSkull(false,false,0).hp,5,'an additional Sneaky flaw applies');
});

test('raid additional Cunning shortens only the fourth new skill and Faun heals at level arrival', () => {
  const { raid }=raidBrowser({classId:'barbarian',menus:true});raidMasterClasses(raid);
  const config=raid.getClassConfig('barbarian');config.perks[1]='Cunning';config.flaws[1]='Cautious';config.race='Faun';
  raid.saveClassConfig('barbarian',config);raid.newGame(0);
  for(const id of ['steal','freeze','repair','heal'])raid.installSkillOption(raid.prepareSkillOption(id));
  assert.equal(raid.skillDuration('heal'),14);assert.equal(raid.state.skillCooldowns.heal,0);
  raid.state.hero.hp=1;raid.state.hero.xp=100;raid.checkLevelUp();
  assert.equal(raid.state.hero.hp,12);
});

test('raid additional Intimidating uses its reduced mortal-enemy threshold even on a different class', () => {
  const {raid}=raidBrowser({classId:'barbarian'});raidMasterClasses(raid);
  const config=raid.getClassConfig('barbarian');config.perks[1]='Intimidating';config.flaws[1]='Confused';
  raid.saveClassConfig('barbarian',config);
  for(let seed=0;seed<1000;seed++) {
    raid.newGame(seed);const first=raidDraw(raid.state.rngSeeds[1],1,32), override=raidDraw(first.seed,1,200);
    if(override.value>5&&override.value<=10&&first.value!==12){assert.equal(raid.makeSkull(false,true).type,first.value);return;}
  }
  assert.fail('no discriminating seed');
});

test('raid additional Unexpected and Confused affect only early volley/direct-chain damage and expire at turn5', () => {
  const {raid}=raidBrowser({classId:'barbarian'});raidMasterClasses(raid);
  const config=raid.getClassConfig('barbarian');config.perks[1]='Unexpected';config.flaws[1]='Confused';
  raid.saveClassConfig('barbarian',config);raid.newGame(0);raidBoard(raid,'sword');
  const skull=raidOld(raid,0,{hp:100,maxHp:100,def:0,atk:3});
  for(const i of [0,1,2])raid.tryExtendChain(i);
  assert.equal(raid.chainPreview().damage,0);
  assert.equal(raid.processEnemyAttack(),null);
  raid.state.turn=5;assert.ok(raid.chainPreview().damage>0);assert.ok(raid.processEnemyAttack());
  assert.equal(skull.hp,100,'preview and volley do not themselves attack the skull');
});

test('raid configured Dragonkin and Elf use current race in chain damage and projected potion preview', () => {
  const {raid}=raidBrowser({classId:'barbarian'});raidMasterClasses(raid);
  const config=raid.getClassConfig('barbarian');config.race='Dragonkin';raid.saveClassConfig('barbarian',config);raid.newGame(0);
  raidBoard(raid,'sword');raidOld(raid,0,{hp:20},8);assert.equal(raid.baseDamage(),2);
  config.race='Elf';raid.saveClassConfig('barbarian',config);raid.newGame(0);raidBoard(raid,'sword');
  raidOld(raid,10,{hp:20},9);raid.state.poison=3;
  for(const i of [0,1,2]){raid.state.board[i]={type:'potion'};raid.tryExtendChain(i)}
  const before=JSON.stringify(raid.state);raid.chainPreview();assert.equal(JSON.stringify(raid.state),before);
  raid.addResource({type:'potion'});assert.equal(raid.state.poison,3);
});

test('raid skill replacement quota is enforced at every even class level and restoring defaults frees budget', () => {
  for(const level of [2,4,6,8,10]) {
    const {raid}=raidBrowser({classId:'barbarian'});raidClassLevel(raid,'barbarian',level);
    const config=raid.getClassConfig('barbarian');config.replacements.fill('steal');raid.saveClassConfig('barbarian',config);
    const limited=raid.getClassConfig('barbarian');assert.equal(limited.replacements.filter(Boolean).length,level);
    limited.replacements[1]=null;limited.replacements[19]='freeze';raid.saveClassConfig('barbarian',limited);
    assert.equal(raid.getClassConfig('barbarian').replacements[19],'freeze');
    assert.equal(raid.getClassConfig('barbarian').replacements.filter(Boolean).length,level);
  }
});

for(const mode of ['classic','sprint','pretzel']) test(`raid ${mode}: all nine editable classes x ten races retain their setup through rewards and one real turn`, () => {
  const {raid,elements,advance}=raidBrowser({mode,menus:true});raidMasterClasses(raid);
  const races=['Human','Elf','Dwarf','Sauren','Faun','Orc','Halfling','Werewolf','Dragonkin','Changeling'];
  const perks=['Unexpected','Gifted','Daring','Cunning','Strong','Faithful','Agile','Frugal','Intimidating','Tough'];
  const flaws=['Confused','Frail','Cocky','Sneaky','Reckless','Cautious','Slim','Clumsy','Zealous','Noisy'];
  function finish(){
    for(let n=0;n<20;n++) {
      const menu=raid.state.pendingModal;
      if(menu==='level')raidPickLevel(elements);
      else if(menu==='gold')raidPickShop(elements);
      else if(menu==='armor')elements.get('modal-body').querySelectorAll('.lv-card')[0].click();
      else {advance(1000);if(!raid.state.pendingModal)return;continue}
      advance(1000);
    }
    assert.fail('reward menus did not settle');
  }
  let count=0;
  for(const classId of raidClassIds.slice(1))for(const race of races){
    const config=raid.defaultClassConfig(classId);config.race=race;
    for(const [kind,values] of [['perks',perks],['flaws',flaws]]){
      const ordered=values.slice(count%values.length).concat(values.slice(0,count%values.length)).filter(value=>value!==config[kind][0]);
      for(let i=1;i<4;i++)config[kind][i]=ordered[i-1];
    }
    config.replacements[1]='fireball';config.replacements[2]='freeze';
    raid.saveClassConfig(classId,config);raid.setSelectedClassId(classId);raid.newGame(913+count);
    assert.equal(raid.clsRace(),race);
    const snapshot=JSON.stringify(raid.state.classConfig);
    raid.state.hero.xp=100;raid.checkLevelUp();finish();
    raid.state.hero.gold=100;raid.checkGoldBox();finish();
    raid.state.upgradeBar=100;raid.checkUpgradeBar();finish();
    for(const i of [0,1,2]){raid.state.board[i]={type:'shield'};raid.tryExtendChain(i)}
    raid.resolveChain();advance(1000);finish();
    assert.equal(raid.state.turn,1);assert.equal(raid.state.resolving,false);
    assert.equal(raid.state.board.length,36);assert.ok(raid.state.board.every(cell=>cell&&cell.type!=='empty'));
    assert.equal(JSON.stringify(raid.state.classConfig),snapshot);
    for(const key of ['hp','maxHp','armor','maxArmor','weaponDamage','xp','gold'])assert.ok(Number.isFinite(raid.state.hero[key]),`${classId}/${race}/${key}`);
    count++;
  }
  assert.equal(count,90);
});

test('raid edited races use their mortal-enemy table including Human NORMAL, preserving the class-gated override RNG', () => {
  const { raid } = raidBrowser({ classId: 'barbarian' }); raidMasterClasses(raid);
  const mortals = { Human: 0, Elf: 9, Dwarf: 14, Sauren: 13, Faun: 16, Orc: 12, Halfling: 5, Werewolf: 20, Dragonkin: 8, Changeling: 19 };
  for (const [race, mortal] of Object.entries(mortals)) {
    const config = raid.getClassConfig('barbarian'); config.race = race;
    config.perks[1] = 'Intimidating'; config.flaws[1] = 'Confused';
    raid.saveClassConfig('barbarian', config);
    let found = false;
    for (let seed = 1; seed <= 1000; seed++) {
      raid.newGame(seed);
      const first = raidDraw(raid.state.rngSeeds[1], 1, 32), override = raidDraw(first.seed, 1, 200);
      if (override.value > 5 || first.value === mortal) continue;
      let expectedSeed = override.seed;
      for (let i = 0; i < 4; i++) expectedSeed = raidDraw(expectedSeed, 0, 0).seed;
      const skull = raid.makeSkull(false, true);
      assert.equal(skull.type, mortal, race);
      assert.equal(skull.special, mortal > 1, race);
      assert.equal(skull.boss, false, race);
      assert.equal(raid.state.rngSeeds[1], expectedSeed, race + ': two promotion rolls plus four stat/interval rolls');
      if (race === 'Human') assert.deepEqual([skull.atk, skull.def, skull.hp, skull.art], [1, 0, 4, 'e1_ghost']);
      found = true; break;
    }
    assert.equal(found, true, race + ': discriminating nonzero seed found');
  }
  raid.setSelectedClassId('adventurer'); raid.newGame(913);
  const first = raidDraw(raid.state.rngSeeds[1], 1, 32);
  let expectedSeed = first.seed;
  for (let i = 0; i < 4; i++) expectedSeed = raidDraw(expectedSeed, 0, 0).seed;
  assert.equal(raid.makeSkull(false, true).type, first.value, 'Adventurer does not roll an override even as Human');
  assert.equal(raid.state.rngSeeds[1], expectedSeed);
});

// I1 is a wrong-memory read, not a set of owned skill IDs. The reproducible
// classic-memory-v1 policy models zero/non-zero words, not native pointers/art.
const raidFreshSwordScrollNatives = [6,7,10,11,13,17,18,19,22,24,25,28,30];
function raidNonExemptSeed(native) {
  return raidShopSeed(result => result.hasScroll && !result.exempt && (native == null
    || raidFreshSwordScrollNatives[raidDraw(result.seed, 0, raidFreshSwordScrollNatives.length - 1).value] === native));
}

test('raid I1 defaults to an explicit classic word model: fresh zero memory except initialized label colours', () => {
  const {raid}=raidBrowser();
  assert.equal(raid.state.scrollPolicy,'classic-memory-v1');
  for(let n=0;n<42;n++) assert.equal(raid.classicScrollWord(n),[16,21,26,31,36,41].includes(n)?1:0,`word ${n}`);
  for(const n of [-1,42,1.5,'0',null,{},undefined]) assert.equal(raid.classicScrollWord(n),null);
});

test('raid I1 low words follow occupied slots and count, never the owned skill identity or cooldown', () => {
  const {raid}=raidBrowser(); const s=raid.state;
  s.skills=['boostgold','steal'];s.skillCooldowns={boostgold:5,steal:0};
  assert.deepEqual([0,1,2,3,4].map(n=>raid.classicScrollWord(n)),[1,1,0,0,2]);
  assert.equal(raid.classicScrollWord(17),0,'owned Boost Gold is not blocked by membership');
  assert.equal(raid.classicScrollWord(16),1,'unowned Boost Damage reads a colour word');
  s.skillCooldowns.steal=10;assert.equal(raid.classicScrollWord(0),1);
});

test('raid I1 collection words use native potion/skull/coin/shield/sword order including arrows', () => {
  const {raid}=raidBrowser();
  raid.state.collectionCounts={potion:2,xp:3,coin:5,shield:7,sword:11,arrow:13};
  assert.deepEqual([5,6,7,8,9].map(n=>raid.classicScrollWord(n)),[2,3,5,7,24]);
  raid.collectIndices([], {noRefill:true});
  assert.deepEqual([5,6,7,8,9].map(n=>raid.classicScrollWord(n)),[0,0,0,0,0]);
});

test('raid I1 packs refill/counter/steal bytes and the following next-turn byte', () => {
  const {raid}=raidBrowser();const s=raid.state;
  s.refillFilter={enabled:true,excludeSwords:true};s.counterattack=1;s.stealActive=1;s.nextTurnFlags.stealActive=1;
  assert.equal(raid.classicScrollWord(10),0x01010101);assert.equal(raid.classicScrollWord(11),1);
  s.refillFilter.enabled=false;s.counterattack=0;
  assert.equal(raid.classicScrollWord(10),0x01000100);
  s.stealActive=0;s.refillFilter.excludeSwords=false;s.nextTurnFlags.stealActive=0;
  assert.equal(raid.classicScrollWord(10),0);assert.equal(raid.classicScrollWord(11),0);
});

test('raid I1 actual selection writes life-leech label history without spending RNG or changing hero', () => {
  const {raid}=raidBrowser();
  raidBoard(raid,'sword'); raidOld(raid,0,{hp:100,maxHp:100,def:0,atk:0}); raid.state.leech=.5;
  const before=JSON.stringify(raid.state.hero),seeds=Array.from(raid.state.rngSeeds);
  for(const slot of [0,1,2]) raid.tryExtendChain(slot);
  assert.equal(raid.state.scrollLeechLabel.number,3);
  assert.equal(raid.classicScrollWord(12),1);assert.equal(raid.classicScrollWord(13),8);
  for(let n=14;n<42;n++)assert.equal(raid.classicScrollWord(n),[25,30].includes(n)?0:1,`word ${n}`);
  assert.equal(JSON.stringify(raid.state.hero),before);assert.deepEqual(Array.from(raid.state.rngSeeds),seeds);
  const state=JSON.stringify(raid.state);for(let n=0;n<5;n++)raid.chainPreview();
  assert.equal(JSON.stringify(raid.state),state,'querying preview is still pure');
});

for(const [number,zeroXs] of [[1,[]],[20,[19,24,34]],[2222,[39]]]) test(`raid I1 leech label ${number}: zero vertex coordinates are not a blanket post-leech blacklist`,()=>{
  const {raid}=raidBrowser();
  raid.state.scrollLeechLabel.number=number;raid.state.scrollLeechLabel.active=true;
  for(const n of [14,19,24,29,34,39])assert.equal(raid.classicScrollWord(n),zeroXs.includes(n)?0:1,`x word ${n}`);
  for(const n of [25,30])assert.equal(raid.classicScrollWord(n),0,'first glyph bottom edge is zero');
  for(const n of [16,21,26,31,36,41])assert.equal(raid.classicScrollWord(n),1,'RGB remains nonzero even after fading');
});

test('raid I1 cancelling a one-node leech preview fades active after200ms but keeps type and geometry',()=>{
  const {raid,advance}=raidBrowser();raidBoard(raid,'sword');raid.state.leech=1;
  raid.tryExtendChain(0);assert.equal(raid.state.scrollLeechLabel.number,2);
  raid.resolveChain();assert.equal(raid.state.turn,0);assert.equal(raid.classicScrollWord(12),1);
  advance(199);assert.equal(raid.classicScrollWord(12),1);
  advance(1);assert.equal(raid.classicScrollWord(12),0);assert.equal(raid.classicScrollWord(13),8);
  assert.equal(raid.classicScrollWord(17),1);
});

test('raid I1 label survives a same-tab new run, stale fade cannot affect a new preview, reload starts fresh',()=>{
  const {raid,advance,storage}=raidBrowser();raidBoard(raid,'sword');raid.state.leech=1;
  raid.tryExtendChain(0);raid.resolveChain();advance(100);
  raid.newGame(71);assert.equal(raid.state.scrollLeechLabel.number,2);assert.equal(raid.classicScrollWord(12),0);
  raidBoard(raid,'sword');raid.state.leech=1;raid.tryExtendChain(0);advance(1000);
  assert.equal(raid.classicScrollWord(12),1,'an old timer does not expire a new held selection');
  const reload=raidBrowser({saved:Object.fromEntries(storage)}).raid;
  assert.equal(reload.classicScrollWord(13),0);assert.equal(reload.classicScrollWord(17),0);
});

test('raid I1 ordinary non-exempt shop uses the model, one final ITEM choice and no OTHER randomness',()=>{
  const seed=raidNonExemptSeed(),{raid}=raidBrowser({seed});raidBoard(raid,'sword');
  const expected=forecastStartingShop(seed),other=raid.state.rngSeeds[5];
  const draw=raidDraw(expected.seed,0,raidFreshSwordScrollNatives.length-1);
  const items=raid.makeShopOffers(),item=items[expected.index];
  assert.equal(items.filter(item=>item.scroll).length,1);assert.ok(!item.scrollBlocked);
  assert.equal(item.scroll.native,raidFreshSwordScrollNatives[draw.value]);
  assert.equal(raid.state.rngSeeds[2],draw.seed);assert.equal(raid.state.rngSeeds[5],other);
});

test('raid I1 a non-exempt scroll may repeat an owned skill without equipping it or changing its charge',()=>{
  const seed=raidNonExemptSeed(7),{raid,elements}=raidBrowser({seed,menus:true});raidBoard(raid,'sword');
  raid.installSkillOption(raid.prepareSkillOption('teleport'));raid.state.skillCooldowns.teleport=9;
  const before=JSON.stringify([raid.state.skills,raid.state.skillCooldowns,raid.state.skillLevels]);raidOpenShop(raid);
  const index=raid.state.menuCandidates.findIndex(item=>item.scroll);assert.equal(raid.state.menuCandidates[index].scroll.id,'teleport');
  elements.get('modal-body').querySelectorAll('.shop-item')[index].click();
  assert.equal(JSON.stringify([raid.state.skills,raid.state.skillCooldowns,raid.state.skillLevels]),before);
  assert.equal(raid.state.pendingModal,null);assert.equal(raid.state.turn,0);
});

test('raid I1 confirmed raw words override the model, including a negative-zero float bit pattern',()=>{
  const seed=raidNonExemptSeed(),{raid}=raidBrowser({seed});raidBoard(raid,'sword');
  raid.state.scrollNativeWords=Object.fromEntries(Array.from({length:42},(_,n)=>[n,n===7?0:n===6?0x80000000:1]));
  const item=raid.makeShopOffers().find(item=>item.scroll);
  assert.equal(item.scroll.id,'teleport','0x80000000 is a nonzero integer word, not numeric float zero');
});

for(const fixture of [Object.assign(Object.create({7:0}),{6:0}),{16:0},Array(42).fill(NaN),Array(42).fill(2**32)]) test('raid I1 invalid or incomplete supplied words never silently fall back to the model',()=>{
  const seed=raidNonExemptSeed(),{raid}=raidBrowser({seed});raidBoard(raid,'sword');raid.state.scrollNativeWords=fixture;
  const items=raid.makeShopOffers();assert.equal(items.filter(item=>item.scrollBlocked).length,1);
  assert.equal(items.filter(item=>item.scroll).length,0);
  assert.equal(raid.state.rngSeeds[2],forecastStartingShop(seed).seed);
});

test('raid I1 a fully known empty raw pool consumes the final ITEM draw but never crashes the shop',()=>{
  const seed=raidNonExemptSeed(),{raid}=raidBrowser({seed,menus:true});raidBoard(raid,'sword');
  raid.state.scrollNativeWords=Array(42).fill(1);raidOpenShop(raid);
  assert.equal(raid.state.menuCandidates.filter(item=>item.scrollBlocked).length,1);
  assert.equal(raid.state.rngSeeds[2],raidDraw(forecastStartingShop(seed).seed,0,0).seed);
  assert.equal(raid.purchaseShopItem(0),true);assert.equal(raid.state.pendingModal,null);
});

test('raid I1 10-percent exemption does not read malformed words or an unknown model policy',()=>{
  const {raid}=raidBrowser({seed:0});raidBoard(raid,'sword');
  raid.state.scrollNativeWords={get 6(){throw Error('must not read exempt memory')}};raid.state.scrollPolicy='unsupported';
  assert.equal(raid.makeShopOffers()[0].scroll.id,'goldtouch');
});

test('raid I1 unsupported model state fails closed for the scroll, without blocking equipment',()=>{
  for(const kind of ['version','number','policy']){
    const seed=raidNonExemptSeed(),{raid}=raidBrowser({seed,menus:true});raidBoard(raid,'sword');
    if(kind==='policy')raid.state.scrollPolicy='unsupported';
    else raid.state.scrollLeechLabel[kind]=kind==='version'?2:100000;
    raidOpenShop(raid);assert.equal(raid.state.menuCandidates.filter(item=>item.scrollBlocked).length,1);
    assert.equal(raid.purchaseShopItem(0),true);
  }
});

test('raid I1 leech healing waits behind a real reward menu, then fades without losing the deferred turn',()=>{
  const {raid,elements,advance}=raidBrowser({seed:0,menus:true});raidBoard(raid,'sword');
  raidOld(raid,0,{hp:1,maxHp:1,def:0,atk:0},3);raid.state.leech=.5;raid.state.hero.gold=98;raid.state.hero.hp=10;
  for(const slot of [0,1,2])raid.tryExtendChain(slot);raid.resolveChain();advance(420);
  assert.equal(raid.state.pendingModal,'gold');assert.equal(raid.classicScrollWord(12),1);
  assert.equal(raid.state.hero.hp,10,'healing arrival is still queued');
  const index=raid.state.menuCandidates.findIndex(item=>!item.scroll);
  elements.get('modal-body').querySelectorAll('.shop-item')[index].click();
  advance(80);assert.equal(raid.state.hero.hp,13,'only the queued leech heal, no purchased healing scroll');
  assert.equal(raid.classicScrollWord(12),1);advance(200);assert.equal(raid.classicScrollWord(12),0);
  advance(1000);assert.equal(raid.state.turn,1);assert.equal(raid.state.resolving,false);
});

test('raid I1 dragging backwards rewrites positive geometry; cancelled zero preview retains the last bytes', () => {
  const {raid,advance}=raidBrowser(); raidBoard(raid,'sword');
  raid.state.leech=1; raid.state.hero.weaponDamage=10;
  const seeds=Array.from(raid.state.rngSeeds);
  raid.tryExtendChain(0); raid.tryExtendChain(1);
  assert.equal(raid.state.scrollLeechLabel.number,20); assert.equal(raid.classicScrollWord(19),0);
  raid.tryExtendChain(0);
  assert.deepEqual(Array.from(raid.state.selected),[0]);
  assert.equal(raid.state.scrollLeechLabel.number,10); assert.equal(raid.classicScrollWord(19),1);
  raid.resolveChain(); advance(200);
  assert.equal(raid.classicScrollWord(12),0); assert.equal(raid.state.scrollLeechLabel.number,10);
  assert.deepEqual(Array.from(raid.state.rngSeeds),seeds);
});

test('raid I1 sword-only committed chains start fading on release and never invent a healing flight', () => {
  const {raid,advance}=raidBrowser(); raidBoard(raid,'sword'); raid.state.leech=1;
  raid.state.hero.hp=1;
  raidSelect(raid,[0,1,2]); assert.equal(raid.state.scrollLeechLabel.number,6);
  raid.resolveChain(); advance(200);
  assert.equal(raid.classicScrollWord(12),0); assert.equal(raid.state.scrollLeechLabel.inFlight,false);
  advance(1000);
  assert.equal(raid.state.hero.hp,1); assert.equal(raid.state.turn,1);
  assert.equal(raid.classicScrollWord(13),8);
});

test('raid I1 Counterattack and current/next Steal bytes track actual activation and end-of-turn rotation', () => {
  const {raid}=raidBrowser(); raidBoard(raid,'sword'); raidOld(raid,0,{atk:0});
  raid.activateSkill('counter'); assert.equal(raid.classicScrollWord(10),0x10000);
  raid.activateSkill('steal'); assert.equal(raid.classicScrollWord(10),0x01010000);
  assert.equal(raid.classicScrollWord(11),0);
  raid.state.actionCommitted=true; raid.state.skillCooldowns.steal=0; raid.activateSkill('steal');
  assert.equal(raid.classicScrollWord(11),1);
  raid.endEnemyTurn(); assert.equal(raid.classicScrollWord(10),0x01000000);
  assert.equal(raid.classicScrollWord(11),0);
  raid.endEnemyTurn(); assert.equal(raid.classicScrollWord(10),0);
});

for(const classId of ['adventurer','mage']) test(`raid I1 ${classId}: Unstable refill flags live until the turn boundary, then clear`, () => {
  const {raid}=raidBrowser({classId}); raid.unstableTeleport();
  assert.equal(raid.classicScrollWord(10),classId==='mage'?0x101:1);
  raidBoard(raid,'sword'); raid.endEnemyTurn();
  assert.equal(raid.classicScrollWord(10),0);
  raid.newGame(71); assert.equal(raid.classicScrollWord(10),0);
});

test('raid I1 invalid raw getters fail only the scroll; complete typed and signed words remain usable', () => {
  const seed=raidNonExemptSeed();
  const {raid}=raidBrowser({seed}); raidBoard(raid,'sword');
  raid.state.scrollNativeWords=Object.fromEntries(Array.from({length:42},(_,n)=>[n,1]));
  Object.defineProperty(raid.state.scrollNativeWords,7,{get(){throw Error('corrupt fixture')}});
  assert.equal(raid.makeShopOffers().filter(item=>item.scrollBlocked).length,1);
  for(const Constructor of [Int32Array,Uint32Array]) {
    const {raid}=raidBrowser({seed}); raidBoard(raid,'sword');
    raid.state.scrollNativeWords=new Constructor(42).fill(-1); raid.state.scrollNativeWords[7]=0;
    assert.equal(raid.makeShopOffers().find(item=>item.scroll).scroll.id,'teleport');
  }
});

test('raid I1 externally supplied raw words are snapshotted once, not reread per candidate', () => {
  const {raid}=raidBrowser({seed:raidNonExemptSeed()}); raidBoard(raid,'sword');
  const reads=Array(42).fill(0),fixture={};
  for(let n=0;n<42;n++)Object.defineProperty(fixture,n,{get(){reads[n]++;return n===7?0:1;}});
  raid.state.scrollNativeWords=fixture;
  assert.equal(raid.makeShopOffers().find(item=>item.scroll).scroll.id,'teleport');
  assert.ok(reads.every(count=>count===1));
});

test('raid I1 first-six-vertex equivalence agrees with independently laid out float32 quads across digit widths', () => {
  const {raid}=raidBrowser();
  const numbers=[1,9,10,11,19,20,21,22,24,99,100,111,999,1111,1112,1222,2222,9999,10000,99999];
  let seed=83;
  for(let n=0;n<300;n++){seed=Math.imul(seed,16807)>>>0;numbers.push(seed%99999+1);}
  const bits=new DataView(new ArrayBuffer(4));
  const word=value=>{bits.setFloat32(0,value/320,true);return bits.getUint32(0,true)===0?0:1;};
  for(const number of numbers){
    // Rules/dimensions only, NOT the foreign renderer/texture: type 8 starts
    // with a 38-wide word, then half-size digits, then an 18-wide word.
    const digitWidths=[20,16,20,20,21,20,20,19,19,20];
    const glyphs=[[38,13],...String(number).split('').map(d=>[digitWidths[Number(d)]/2,27/2]),[18,13]];
    let x=-glyphs.reduce((sum,[width])=>sum+width,0)/2;
    const expected=[];
    for(const [width,height] of glyphs){
      for(const [vx,vy] of [[x,-height],[x+width,-height],[x+width,0],[x,0]]) expected.push(word(vx),word(vy),1,1,1);
      x+=width;
    }
    raid.state.scrollLeechLabel.number=number;
    assert.deepEqual(Array.from({length:28},(_,n)=>raid.classicScrollWord(n+14)),expected.slice(0,28),`number ${number}`);
  }
});

test('raid I1 a real leech preview changes the ordinary shop candidate pool, not just debug display words', () => {
  const seed=raidNonExemptSeed(),{raid}=raidBrowser({seed});
  raidBoard(raid,'sword'); raidOld(raid,0,{hp:100,maxHp:100,atk:0,def:0}); raid.state.leech=.5;
  raidSelect(raid,[0,1,2]);
  const candidates=[0,1,4,5,6,7,10,11,25,30],forecast=forecastStartingShop(seed);
  const draw=raidDraw(forecast.seed,0,candidates.length-1);
  assert.equal(raid.makeShopOffers().find(item=>item.scroll).scroll.native,candidates[draw.value]);
  assert.equal(raid.state.rngSeeds[2],draw.seed);
});

test('raid reward/help/menu cards size to the padded overlay, not a wider viewport fraction', () => {
  const css=readFileSync(new URL('./raid.css',import.meta.url),'utf8');
  // In Chromium on 320px, 96vw overflowed the grid's 18px padding even though
  // body overflow:hidden made document.scrollWidth look fine. Keep all three
  // overlay cards within the available content box; browser acceptance checks
  // their actual bounding rectangles as well as the document width.
  for(const [selector,max] of [['modal-card',460],['help-card',430],['menu-card',340]]){
    const rule=css.match(new RegExp(`\\.${selector} \\{([^}]+)\\}`))[1];
    assert.ok(rule.includes(`width: min(100%, ${max}px)`),selector);
    assert.ok(rule.includes('min-width: 0'),selector);
  }
});


// Active-run saves are a local, versioned protocol, not the native binary file.
const RAID_RUN_KEY = 'raid:activeRun';
const raidJSON = value => JSON.parse(JSON.stringify(value));
function raidReload(browser, options = {}) {
  const next = raidBrowser({ ...options, menus: true, start: false, saved: browser.storage });
  assert.equal(next.raid.resumeSavedRun(), true);
  return next;
}

test('raid save: new run is checkpointed; loading the page does not start or replace it', () => {
  const b = raidBrowser({ menus: true });
  assert.ok(b.storage.get(RAID_RUN_KEY));
  const raw = b.storage.get(RAID_RUN_KEY);
  const next = raidBrowser({ start: false, saved: b.storage });
  assert.equal(next.raid.state, null);
  assert.equal(next.raid.savedRunInfo().turn, 0);
  assert.equal(next.raid.savedRunInfo().classId, 'adventurer');
  assert.equal(b.storage.get(RAID_RUN_KEY), raw);
});

test('raid save: ready state preserves all six actual RNG streams, gear, carries, poison and skill flags', () => {
  const b = raidBrowser({ menus: true, classId: 'mage', seed: 123 });
  const r = b.raid, s = r.state;
  s.hero.hp = 17; s.hero.armor = 2; s.hero.xpLevelCum = 143; s.hero.xp = 43;
  s.poison = 3; s.resourceCarry = { xp: .25, gold: .5, upgrade: .75 };
  s.skills = ['steal', 'freeze']; s.skillLevels = { steal: 2, freeze: 1 };
  s.skillDurations = { steal: 12, freeze: 16 }; s.skillCooldowns = { steal: 5, freeze: 0 };
  s.swordBoost = 1; s.nextTurnFlags.stealActive = 1;
  s.usedSkillIds.wish = true;
  r.makeShopOffers(); r.chooseLevelOptions(); r.createNewTile();
  assert.equal(r.saveRun(), true);
  const old = raidJSON(s), next = raidReload(b);
  const restored = next.raid.state;
  for (const key of ['hero', 'equipment', 'equipmentApplied', 'resourceCarry', 'poison', 'skills', 'skillLevels', 'skillDurations', 'skillCooldowns', 'nextTurnFlags', 'usedSkillIds'])
    assert.deepEqual(raidJSON(restored[key]), old[key], key);
  assert.deepEqual(raidJSON(next.raid.makeShopOffers()), raidJSON(r.makeShopOffers()));
  assert.deepEqual(raidJSON(next.raid.chooseLevelOptions()), raidJSON(r.chooseLevelOptions()));
  assert.deepEqual(raidJSON(restored.rngSeeds), raidJSON(s.rngSeeds));
});

test('raid save: restoring never reapplies starting/equipment bonuses or current editor settings', () => {
  const b = raidBrowser({ menus: true, classId: 'mage', seed: 0 });
  for (const id of ['mage', 'ranger']) for (let i = 1; i < 10; i++) b.raid.levelUpClass(id);
  const config = b.raid.getClassConfig('mage'); config.race = 'Elf';
  assert.equal(b.raid.saveClassConfig('mage', config), true);
  b.raid.newGame(0);
  const gear = b.raid.state.equipment[0]; gear.mods.weaponDamage = 7; gear.mods.strength = 3;
  b.raid.updateEquipmentStats(); b.raid.state.hero.hp = 11;
  assert.equal(b.raid.saveRun(), true);
  const hero = raidJSON(b.raid.state.hero);
  b.raid.saveClassConfig('mage', b.raid.defaultClassConfig('mage'));
  const next = raidReload(b);
  assert.equal(next.raid.clsRace(), 'Elf');
  assert.deepEqual(raidJSON(next.raid.state.hero), hero);
  assert.ok(Object.isFrozen(next.raid.state.classConfig));
  assert.ok(Object.isFrozen(next.raid.state.classConfig.perks));
  next.raid.updateEquipmentStats();
  assert.deepEqual(raidJSON(next.raid.state.hero), hero, 'derived snapshot makes recompute idempotent');
});

test('raid save: confirmed chain awaiting its animation resumes once without recounting the chain', () => {
  const b = raidBrowser({ menus: true, seed: 0 }); raidBoard(b.raid, 'coin');
  raidSelect(b.raid, [0, 1, 2]); b.raid.resolveChain();
  assert.equal(b.raid.state.chainCount, 1); assert.equal(b.raid.state.hero.gold, 0);
  const next = raidReload(b);
  assert.equal(next.raid.state.resolving, true);
  next.advance(3000);
  assert.equal(next.raid.state.turn, 1); assert.equal(next.raid.state.chainCount, 1);
  assert.ok(next.raid.state.hero.gold >= 6);
  next.advance(3000); assert.equal(next.raid.state.turn, 1);
});

test('raid save: level draft and queued shop/healing resume without rerolls, duplicate rewards or enemy turn', () => {
  const b = raidBrowser({ menus: true, seed: 0 }), r = b.raid, s = r.state;
  raidBoard(r, 'sword'); s.forceCoins = 1; s.hero.hp = 10;
  s.hero.xp = 99; s.hero.xpLevelCum = 99; s.hero.gold = 99; s.goldEarned = 99;
  raidOld(r, 0, { hp: 1 });
  s.board[1] = { type: 'chain' }; s.board[2] = { type: 'coin' };
  s.board[3] = { type: 'chain' }; s.board[4] = { type: 'potion' };
  raidSelect(r, [0, 1, 2, 3, 4]); r.resolveChain(); b.advance(420);
  const cards = b.elements.get('modal-body').querySelectorAll('.lv-card');
  cards[1].click(); cards[0].click();
  const menu = raidJSON(s.menuCandidates), rng = raidJSON(s.rngSeeds), hp = s.hero.hp;
  const next = raidReload(b), nr = next.raid, ns = nr.state;
  assert.equal(ns.pendingModal, 'level'); assert.equal(ns.arrivals.length, 2);
  assert.deepEqual(raidJSON(ns.menuCandidates), menu); assert.deepEqual(raidJSON(ns.rngSeeds), rng);
  assert.deepEqual(raidJSON(ns.levelDraft), [1, 0]);
  assert.equal(ns.hero.hp, hp); assert.equal(ns.turn, 0);
  assert.equal(next.elements.get('modal-body').querySelectorAll('.lv-confirm')[0].disabled, false);
  next.elements.get('modal-body').querySelectorAll('.lv-confirm')[0].click(); next.advance(80);
  assert.equal(ns.pendingModal, 'gold'); assert.equal(ns.hero.gold, 1);
  raidPickShop(next.elements); next.advance(3000);
  assert.equal(ns.turn, 1); assert.equal(ns.hero.xp, 1); assert.equal(ns.arrivals.length, 0);
  assert.equal(ns.resolving, false);
  assert.match(ns.log.join(' '), /⚔ \+2 XP · \+2 золота · убийств 1/);
});

test('raid save: valid I1 history survives resume, but a genuinely fresh page starts with zero history', () => {
  const b = raidBrowser({ menus: true, seed: 0 }), s = b.raid.state;
  s.scrollLeechLabel = { version: 1, number: 2222, active: true, inFlight: false };
  assert.equal(b.raid.saveRun(), true);
  const next = raidReload(b);
  assert.equal(next.raid.state.scrollLeechLabel.number, 2222);
  assert.equal(next.raid.classicScrollWord(13), 8); assert.equal(next.raid.classicScrollWord(39), 0);
  next.advance(200); assert.equal(next.raid.state.scrollLeechLabel.active, false);
  assert.equal(next.raid.state.scrollLeechLabel.number, 2222);
  assert.equal(raidBrowser().raid.state.scrollLeechLabel.number, 0);
});

test('raid save: malformed, unknown-version and HTML-bearing saves never change a live run or execute', () => {
  const b = raidBrowser({ menus: true }), raw = b.storage.get(RAID_RUN_KEY);
  const variants = ['{', '[]', '{}'];
  const unknown = JSON.parse(raw); unknown.version = 999; variants.push(JSON.stringify(unknown));
  const badHP = JSON.parse(raw); badHP.state.hero.hp = null; variants.push(JSON.stringify(badHP));
  const hostile = JSON.parse(raw); hostile.state.equipment[0].name = '<img src=x onerror=alert(1)>'; variants.push(JSON.stringify(hostile));
  for (const value of variants) {
    const next = raidBrowser({ menus: true }); const original = next.raid.state;
    next.storage.set(RAID_RUN_KEY, value);
    assert.equal(next.raid.resumeSavedRun(), false); assert.equal(next.raid.state, original);
    assert.equal(next.raid.saveStatus.state, 'invalid');
  }
});

test('raid save: blocked reads/writes fail visibly without preventing a new game', () => {
  const fault = { read: true, write: true, remove: true };
  const b = raidBrowser({ menus: true, fault });
  assert.ok(b.raid.state); assert.equal(b.raid.saveRun(), false);
  assert.equal(b.raid.saveStatus.state, 'unavailable');
  assert.equal(b.raid.savedRunInfo(), null);
  assert.equal(b.raid.resumeSavedRun(), false);
});


test('raid save: awaiting enemy animation resumes after collection, never rematches the paid cells', () => {
  const b = raidBrowser({ menus: true, seed: 0 }); raidBoard(b.raid, 'coin');
  raidSelect(b.raid, [0, 1, 2]); b.raid.resolveChain(); b.advance(420);
  const gold = b.raid.state.hero.gold, rng = raidJSON(b.raid.state.rngSeeds);
  assert.equal(JSON.parse(b.storage.get(RAID_RUN_KEY)).next.phase, 'enemy');
  const next = raidReload(b); assert.equal(next.raid.state.hero.gold, gold);
  assert.deepEqual(raidJSON(next.raid.state.rngSeeds), rng);
  next.advance(3000);
  assert.equal(next.raid.state.hero.gold, gold); assert.equal(next.raid.state.turn, 1);
  assert.equal(next.raid.state.chainCount, 1);
});

for (const phase of ['volley', 'end-turn']) test(`raid save: ${phase} reward menu continues the correct enemy stage once`, () => {
  const b = raidCombat({ menus: true, seed: 0 }), r = b.raid, s = r.state;
  s.forceCoins = 1; s.hero.xp = 99; s.hero.xpLevelCum = 99;
  if (phase === 'volley') { s.spikes = 1; raidOld(r, 0, { hp: 1, atk: 1 }); }
  else raidOld(r, 0, { hp: 1, burn: true, fireDamage: 1 }, 2);
  r.enemyPhase();
  const turn = phase === 'volley' ? 10 : 11;
  assert.equal(s.turn, turn); assert.equal(s.pendingModal, 'level');
  const next = raidReload(b); next.advance(3000); assert.equal(next.raid.state.turn, turn);
  raidPickLevel(next.elements); next.advance(3000);
  assert.equal(next.raid.state.turn, 11); assert.equal(next.raid.state.resolving, false);
  next.advance(3000); assert.equal(next.raid.state.turn, 11);
});

test('raid save: nested Enchant keeps its original offers, suspended tokens, cooldown and single enemy continuation', () => {
  const b = raidBrowser({ menus: true, seed: 0 }), r = b.raid, s = r.state;
  raidBoard(r, 'coin'); s.hero.gold = 99; s.goldEarned = 99; s.forceCoins = 1; s.upgradeBar = 37;
  raidSelect(r, [0, 1, 2]); r.resolveChain(); b.advance(420);
  const item = s.menuCandidates[0]; item.scroll = r.prepareSkillOption('enchant'); item.scroll.upgrade = false;
  r.purchaseShopItem(0);
  assert.equal(s.pendingModal, 'armor'); assert.equal(s.arrivals.length, 2);
  const menu = raidJSON(s.menuCandidates), gear = raidJSON(s.equipment), rng = raidJSON(s.rngSeeds);
  const next = raidReload(b);
  assert.deepEqual(raidJSON(next.raid.state.menuCandidates), menu);
  assert.deepEqual(raidJSON(next.raid.state.equipment), gear);
  assert.deepEqual(raidJSON(next.raid.state.rngSeeds), rng);
  assert.equal(next.raid.state.upgradeBar, 37);
  next.elements.get('modal-body').querySelectorAll('.lv-card')[0].click(); next.advance(3000);
  assert.equal(next.raid.state.turn, 1); assert.equal(next.raid.state.hero.gold, 5);
  assert.equal(next.raid.state.upgradeBar, 37); assert.equal(next.raid.state.resolving, false);
});

test('raid save: a free manual Enchant restores an upgrade without losing charge or adding an enemy turn', () => {
  const b = raidBrowser({ menus: true }), r = b.raid;
  r.state.skills = ['enchant']; r.state.skillLevels.enchant = 1;
  r.activateSkill('enchant');
  const charge = r.state.skillCooldowns.enchant;
  const next = raidReload(b);
  assert.equal(next.raid.state.skillCooldowns.enchant, charge);
  next.elements.get('modal-body').querySelectorAll('.lv-card')[0].click(); next.advance(3000);
  assert.equal(next.raid.state.turn, 0); assert.equal(next.raid.state.resolving, false);
  assert.equal(next.raid.state.skillCooldowns.enchant, charge);
});

test('raid save: queued leech and damage stay behind the menu, then settle once in order', () => {
  const b = raidBrowser({ menus: true, seed: 0 }), r = b.raid, s = r.state;
  raidBoard(r, 'sword'); s.forceCoins = 1; s.hero.hp = 10; s.hero.xp = 99; s.hero.xpLevelCum = 99;
  s.leech = 1; raidOld(r, 0, { hp: 1, burn: true, fireDamage: 2 }, 2);
  raidSelect(r, [0, 1, 2]); r.resolveChain(); b.advance(420);
  assert.equal(s.pendingModal, 'level'); assert.equal(s.scrollLeechLabel.inFlight, true);
  const control = raidBrowser({ menus: true, start: false, saved: Object.fromEntries(b.storage) });
  assert.equal(control.raid.resumeSavedRun(), true);
  const next = raidReload(b), before = next.raid.state.hero.hp;
  next.advance(2000); assert.equal(next.raid.state.hero.hp, before);
  assert.equal(next.raid.state.scrollLeechLabel.inFlight, true);
  raidPickLevel(next.elements); next.advance(3000);
  raidPickLevel(control.elements); control.advance(3000);
  assert.equal(next.raid.state.hero.hp, control.raid.state.hero.hp);
  assert.equal(next.raid.state.turn, 1); assert.equal(next.raid.state.scrollLeechLabel.inFlight, false);
  assert.equal(next.raid.state.scrollLeechLabel.active, false);
});

test('raid save: all enemy variants, status timers, phylactery and UID allocation survive a ready checkpoint', () => {
  const b = raidBrowser({ menus: true }), r = b.raid;
  raidBoard(r, 'sword');
  for (let type = 0; type <= 32; type++) r.state.board[type] = { type: 'skull', skull: r.makeSkull(type === 1, type > 1, type) };
  Object.assign(r.state.board[0], { frozen: true });
  Object.assign(r.state.board[0].skull, { poison: [4, 2, 1], stunned: true, frozenTurn: 9 });
  Object.assign(r.state.board[32].skull, { phylactery: true, lichDelay: true, countdown: 1, hp: 1 });
  r.state.board[35] = { type: 'potion', explosive: true, burn: true, fireDamage: 3, fireSafe: true };
  assert.equal(r.saveRun(), true);
  const next = raidReload(b); assert.deepEqual(raidJSON(next.raid.state.board), raidJSON(r.state.board));
  assert.equal(next.raid.makeSkull(false).uid, r.makeSkull(false).uid);
  assert.deepEqual(raidJSON(next.raid.state.rngSeeds), raidJSON(r.state.rngSeeds));
});

test('raid save: complete raw I1 fixture is restored without silently switching to the model', () => {
  const b = raidBrowser({ menus: true });
  b.raid.state.scrollNativeWords = Object.fromEntries(Array.from({ length: 42 }, (_, n) => [n, n % 2 ? 0x80000000 : 0]));
  assert.equal(b.raid.saveRun(), true);
  const next = raidReload(b);
  assert.deepEqual(raidJSON(next.raid.state.scrollNativeWords), Array.from({ length: 42 }, (_, n) => n % 2 ? 0x80000000 : 0));
  assert.deepEqual(raidJSON(next.raid.makeShopOffers()), raidJSON(b.raid.makeShopOffers()));
});

test('raid save: an uncommitted gesture is not autoplayed; its I1 preview history remains', () => {
  const b = raidBrowser({ menus: true }); raidBoard(b.raid, 'sword'); b.raid.state.leech = 1;
  raidSelect(b.raid, [0, 1, 2]); b.advance(0);
  const number = b.raid.state.scrollLeechLabel.number, next = raidReload(b);
  assert.deepEqual(raidJSON(next.raid.state.selected), []);
  next.advance(3000); assert.equal(next.raid.state.turn, 0); assert.equal(next.raid.state.chainCount, 0);
  assert.equal(next.raid.state.scrollLeechLabel.number, number);
});

test('raid save: trophy claim updates the checkpoint so reload cannot claim the same trophy again', () => {
  const b = raidBrowser({ menus: true }), r = b.raid;
  raidBoard(r, 'sword'); r.state.board[0] = { type: 'trophy' }; r.state.trophiesGenerated = 1;
  r.state.trophyRemaining = 24; assert.equal(r.claimTrophy(0), true);
  const levels = JSON.parse(b.storage.get('raid:classLevels'));
  const next = raidReload(b);
  assert.equal(next.raid.state.trophiesThisRun, 1); assert.equal(next.raid.state.trophiesGenerated, 1);
  assert.equal(next.raid.state.trophyRemaining, 24); assert.equal(next.raid.claimTrophy(0), false);
  assert.equal(next.raid.state.turn, 0);
  for (const [id, level] of Object.entries(levels)) assert.equal(next.raid.classLevelOf(id), level);
});

for (const terminal of ['death', 'sprint-win']) test(`raid save: ${terminal} immediately invalidates continuation, including reload before the end modal`, () => {
  const b = raidBrowser({ menus: true, mode: terminal === 'sprint-win' ? 'sprint' : 'classic' }), r = b.raid;
  raidBoard(r, 'sword');
  if (terminal === 'death') { r.state.hero.hp = 1; r.state.poison = 2; }
  else r.state.turn = 99;
  r.endEnemyTurn(); assert.equal(r.state.over, true);
  assert.equal(r.savedRunInfo(), null);
  const next = raidBrowser({ menus: true, start: false, saved: b.storage });
  assert.equal(next.raid.resumeSavedRun(), false); assert.equal(next.raid.state, null);
  assert.equal(next.raid.saveStatus.state, 'empty');
});

test('raid save: storage quota failure is visible and removes an obsolete checkpoint, then recovers', () => {
  const fault = {}, b = raidBrowser({ menus: true, fault });
  assert.ok(b.storage.get(RAID_RUN_KEY)); fault.write = true;
  b.raid.state.hero.hp = 12; assert.equal(b.raid.saveRun(), false);
  assert.equal(b.raid.saveStatus.state, 'unavailable'); assert.equal(b.storage.has(RAID_RUN_KEY), false);
  fault.write = false; assert.equal(b.raid.saveRun(), true); assert.equal(b.raid.saveStatus.state, 'saved');
  assert.equal(raidReload(b).raid.state.hero.hp, 12);
});

test('raid save: a nonserialisable runtime state cannot replace or remove the previous valid checkpoint', () => {
  const b = raidBrowser({ menus: true }), raw = b.storage.get(RAID_RUN_KEY);
  b.raid.state.hero.hp = NaN;
  assert.equal(b.raid.saveRun(), false); assert.equal(b.raid.saveStatus.state, 'invalid');
  assert.equal(b.storage.get(RAID_RUN_KEY), raw);
});

test('raid save: another resumed tab prevents old actions, scheduled matching and stale saves from overwriting it', () => {
  const b = raidBrowser({ menus: true }), r = b.raid; raidBoard(r, 'coin');
  raidSelect(r, [0, 1, 2]); r.resolveChain();
  const next = raidReload(b), raw = b.storage.get(RAID_RUN_KEY);
  b.advance(3000);
  assert.equal(r.state.hero.gold, 0); assert.equal(r.saveStatus.state, 'conflict');
  assert.equal(r.saveRun(), false); assert.equal(b.storage.get(RAID_RUN_KEY), raw);
  next.advance(3000); assert.equal(next.raid.state.turn, 1);
  const current = b.storage.get(RAID_RUN_KEY);
  r.activateSkill('teleport'); r.resolveChain(); assert.equal(r.claimTrophy(0), false);
  assert.equal(b.storage.get(RAID_RUN_KEY), current);
});

for (const malformed of ['missing-rng', 'bad-menu', 'bad-result', 'duplicate-uid', 'bad-class', 'proto', 'oversize']) test(`raid save validation: ${malformed} is rejected before touching a live state`, () => {
  const b = raidBrowser({ menus: true }), data = JSON.parse(b.storage.get(RAID_RUN_KEY));
  if (malformed === 'missing-rng') data.rng.pop();
  if (malformed === 'bad-menu') data.menu = { kind: 'level', choices: [], picked: [0, 1, 2] };
  if (malformed === 'bad-result') data.arrivals = [{ kind: 'resource', result: 99, token: { type: 'coin', variant: 0 } }];
  if (malformed === 'duplicate-uid') {
    const cell = { type: 'skull', skull: raidJSON(b.raid.makeSkull(false)) };
    data.state.board[0] = cell; data.state.board[1] = cell; data.skullUid = cell.skull.uid + 1;
  }
  if (malformed === 'bad-class') data.state.classConfig.perks[0] = 'Tough';
  let raw = JSON.stringify(data);
  if (malformed === 'proto') raw = raw.replace('"state":{', '"state":{"__proto__":{"polluted":true},');
  if (malformed === 'oversize') raw += ' '.repeat(512 * 1024);
  const state = b.raid.state; b.storage.set(RAID_RUN_KEY, raw);
  assert.equal(b.raid.resumeSavedRun(), false); assert.equal(b.raid.state, state);
  assert.equal(b.raid.saveStatus.state, 'invalid'); assert.equal({}.polluted, undefined);
});


test('raid save: ready checkpoints for all 10 classes, 3 modes and 4 difficulties restore without RNG or stat drift', () => {
  const classes = ['adventurer', 'barbarian', 'mage', 'rogue', 'ranger', 'paladin', 'priest', 'raider', 'warrior', 'assassin'];
  let fixtures = 0;
  for (const classId of classes) for (const mode of ['classic', 'sprint', 'pretzel']) for (let difficulty = 0; difficulty < 4; difficulty++) {
    const b = raidBrowser({ menus: true, classId, mode, difficulty, seed: 913 + fixtures });
    const expected = raidJSON(b.raid.state), next = raidReload(b);
    for (const key of ['classId', 'classConfig', 'hero', 'equipment', 'board', 'rngSeeds', 'mode', 'difficulty', 'promotionRemaining'])
      assert.deepEqual(raidJSON(next.raid.state[key]), expected[key], classId + '/' + mode + '/' + difficulty + ':' + key);
    assert.deepEqual(raidJSON(next.raid.createNewTile()), raidJSON(b.raid.createNewTile()));
    assert.deepEqual(raidJSON(next.raid.state.rngSeeds), raidJSON(b.raid.state.rngSeeds));
    fixtures++;
  }
  assert.equal(fixtures, 120);
});

test('raid save: queued mana retains its original skill slot across reload and a level choice', () => {
  const b = raidBrowser({ menus: true, seed: 0 }), r = b.raid, s = r.state;
  raidBoard(r, 'sword'); s.forceCoins = 1; s.hero.xp = 99; s.hero.xpLevelCum = 99;
  s.skills = ['freeze', 'heal']; s.skillLevels = { freeze: 1, heal: 1 };
  s.skillCooldowns = { freeze: 5, heal: 0 };
  raidOld(r, 0, { hp: 1 }); s.board[1] = { type: 'chain' }; s.board[2] = { type: 'potion', mana: true };
  raidSelect(r, [0, 1, 2]); r.resolveChain(); b.advance(420);
  assert.equal(s.arrivals[0].token.skillSlot, 0);
  const next = raidReload(b), ns = next.raid.state;
  assert.equal(ns.arrivals[0].token.skillSlot, 0); assert.equal(ns.skillCooldowns.freeze, 5);
  const cards = next.elements.get('modal-body').querySelectorAll('.lv-card.lv-stat');
  cards[0].click(); cards[1].click(); raidConfirmLevel(next.elements); next.advance(3000);
  assert.equal(ns.skillCooldowns.freeze, 3, 'one bound mana token then one turn recovery');
  assert.equal(ns.skillCooldowns.heal, 0); assert.equal(ns.turn, 1);
});

test('raid save: Rune-only arrow cancellation resumes collection without creating an enemy turn', () => {
  const b = raidBrowser({ menus: true, seed: 0 }), r = b.raid, s = r.state;
  raidBoard(r, 'coin'); s.runeActive = 1; s.forceCoins = 1;
  s.board[8] = { type: 'arrow' }; s.board[20] = { type: 'arrow' };
  raidOld(r, 35, { atk: 100 });
  raidSelect(r, [8, 15, 20, 13]);
  assert.equal(r.chainPreview().effectiveLength, 2);
  r.resolveChain();
  const next = raidReload(b); next.advance(3000);
  assert.equal(next.raid.state.turn, 0); assert.equal(next.raid.state.chainCount, 0);
  assert.equal(next.raid.state.hero.gold, 2); assert.equal(next.raid.state.hero.hp, 50);
  assert.equal(next.raid.state.resolving, false);
});

test('raid save: resource cells retain valid thaw metadata, not only skull status fields', () => {
  const b = raidBrowser({ menus: true, seed: 0 }), r = b.raid;
  raidBoard(r, 'shield');
  const types = ['potion', 'coin', 'shield', 'sword', 'arrow', 'chain', 'trophy'];
  types.forEach((type, i) => { r.state.board[i] = { type, frozen: true }; });
  r.endEnemyTurn();
  for (let i = 0; i < types.length; i++) {
    assert.equal(r.state.board[i].stunned, false);
    assert.equal(r.state.board[i].frozenTurn, -1);
  }
  assert.equal(r.saveRun(), true);
  const next = raidReload(b);
  assert.deepEqual(raidJSON(next.raid.state.board), raidJSON(r.state.board));
  assert.equal(next.raid.saveStatus.state, 'saved');
});

// Local history: six classic categories and the independent class totals.
const RAID_HISTORY_KEY = 'raid:history';
function raidFinish(browser, { xp = 103, gold = 57, upgrade = 29, turn = 11, kills = 3, win = false } = {}) {
  const r = browser.raid, s = r.state;
  raidBoard(r, 'sword'); s.hero.xpLevelCum = xp; s.goldEarned = gold; s.upgradeEarned = upgrade;
  s.turn = win ? 99 : turn; s.skullsKilled = kills; s.hero.hp = win ? s.hero.maxHp : 1;
  s.poison = win ? 0 : 2; s.regen = 0; s.lastStand = 0; s.nextTurnFlags = {};
  r.endEnemyTurn(); assert.equal(s.over, true); assert.equal(s.won, win);
  return r.getLocalHistory();
}
function raidPretzelChain(browser, crosses) {
  const paths = [[0, 1, 2], [0, 7, 1, 6], [0, 7, 1, 6, 12, 19, 13, 18], [0, 7, 1, 6, 12, 19, 13, 18, 24, 31, 25, 30]];
  raidBoard(browser.raid, 'sword'); browser.raid.state.forceCoins = 1;
  assert.equal(browser.raid.pretzelCrossings(paths[crosses]), crosses);
  raidSelect(browser.raid, paths[crosses]); browser.raid.resolveChain();
}

test('raid history: reading an empty profile neither starts a run nor writes fabricated records', () => {
  const b = raidBrowser({ start: false }), before = [...b.storage];
  const profile = b.raid.getLocalHistory();
  assert.equal(profile.records.length, 6); assert.ok(profile.records.every(rows => !rows.length));
  assert.equal(Object.keys(profile.classes).length, 10);
  assert.ok(Object.values(profile.classes).every(stats => stats.games === 0 && stats.kills === 0 && stats.best.every(n => n === 0)));
  assert.deepEqual([...b.storage], before); assert.equal(b.raid.state, null);
});

test('raid history: 120 class/mode/difficulty endings use the right category and independent class best', () => {
  for (const classId of raidClassIds) for (const mode of ['classic', 'sprint', 'pretzel']) for (let difficulty = 0; difficulty < 4; difficulty++) {
    const b = raidBrowser({ menus: true, classId, mode, difficulty });
    b.raid.state.skills = ['freeze', 'heal']; b.raid.state.skillLevels = { freeze: 2, heal: 3 };
    if (mode === 'pretzel') { b.raid.state.pretzelScore = 19; b.raid.state.pretzelStats = { scoringChains: 2, avgTimes10: 40, maxCross: 6 }; }
    const levels = b.storage.get('raid:classLevels'), config = JSON.stringify(b.raid.state.classConfig);
    const history = raidFinish(b), type = mode === 'classic' ? difficulty : mode === 'sprint' ? 4 : 5;
    const rows = history.records[type]; assert.equal(rows.length, 1, `${classId}/${mode}/${difficulty}`);
    const row = rows[0], score = mode === 'classic' ? 103 + 57 + 29 + 12 + 500 : mode === 'sprint' ? 25 + 14 + 7 : 19;
    assert.equal(row.score, score); assert.equal(row.turn, 12); assert.equal(row.classId, classId); assert.equal(row.difficulty, difficulty);
    assert.deepEqual(raidJSON(row.skills), [{ id: 'freeze', level: 2 }, { id: 'heal', level: 3 }]);
    assert.deepEqual([row.xp, row.gold, row.upgrade], mode === 'sprint' ? [25, 14, 7] : [103, 57, 29]);
    assert.deepEqual(raidJSON(history.classes[classId]), { games: 1, kills: 3, best: [1, 2, 3].map(d => mode === 'classic' && difficulty === d ? score : 0) });
    assert.ok(Object.entries(history.classes).filter(([id]) => id !== classId).every(([, stats]) => stats.games === 0));
    assert.equal(history.records.flat().length, 1); assert.equal(b.raid.savedRunInfo(), null);
    assert.equal(b.storage.get('raid:classLevels'), levels); assert.equal(JSON.stringify(b.raid.state.classConfig), config);
  }
});

test('raid history: Sprint quarters each resource separately; only victory doubles the score, not listed resources', () => {
  for (const win of [false, true]) for (const resource of [3, 7]) {
    const b = raidBrowser({ mode: 'sprint', menus: true });
    const row = raidFinish(b, { xp: resource, gold: resource, upgrade: resource, win }).records[4][0];
    assert.equal(row.score, 3 * Math.trunc(resource / 4) * (win ? 2 : 1));
    assert.deepEqual([row.xp, row.gold, row.upgrade], Array(3).fill(Math.trunc(resource / 4)));
    assert.equal(row.won, win); assert.equal(row.turn, win ? 100 : 12);
  }
});

test('raid history: classic counts only equipped skill levels; removed Wish adds nothing', () => {
  const b = raidBrowser({ classId: 'priest', menus: true });
  b.raid.state.skills = ['freeze']; b.raid.state.skillLevels = { wish: 10, freeze: 2 };
  const row = raidFinish(b, { xp: 1, gold: 2, upgrade: 3, turn: 4 }).records[1][0];
  assert.equal(row.score, 1 + 2 + 3 + 5 + 200); assert.deepEqual(raidJSON(row.skills), [{ id: 'freeze', level: 2 }]);
});

test('raid history: descending insertion is stable for ties and a full list still replaces the tenth with a new lowest', () => {
  const b = raidBrowser({ menus: true });
  const ids = [];
  for (let i = 0; i < 11; i++) {
    if (i) b.raid.newGame(71);
    ids.push(b.raid.state.historyId);
    raidFinish(b, { xp: i < 10 ? 100 - Math.floor(i / 2) * 10 : 1, gold: 0, upgrade: 0, turn: 0, kills: 0 });
  }
  const history = b.raid.getLocalHistory(), rows = history.records[1];
  assert.deepEqual(Array.from(rows, row => row.score), [101, 101, 91, 91, 81, 81, 71, 71, 61, 2]);
  assert.deepEqual(Array.from(rows, row => row.id), [...ids.slice(0, 9), ids[10]]);
  assert.deepEqual(raidJSON(history.last), { type: 1, index: 9, id: ids[10] });
  assert.equal(history.classes.adventurer.games, 11); assert.equal(history.classes.adventurer.best[0], 101);
});

test('raid history: a higher result inserts first; class best never falls when its record is replaced', () => {
  const b = raidBrowser({ menus: true });
  for (const xp of [100, 90, 200]) { b.raid.newGame(71); raidFinish(b, { xp, gold: 0, upgrade: 0, kills: 2 }); }
  const history = b.raid.getLocalHistory();
  assert.deepEqual(Array.from(history.records[1], row => row.score), [212, 112, 102]);
  assert.equal(history.last.index, 0); assert.equal(history.classes.adventurer.best[0], 212);
  assert.equal(history.classes.adventurer.games, 3); assert.equal(history.classes.adventurer.kills, 6);
});

test('raid history: restarting an unfinished run counts neither a game nor its kills', () => {
  const b = raidBrowser({ menus: true });
  b.raid.state.skullsKilled = 50; b.raid.state.hero.xpLevelCum = 1000; b.raid.newGame(71);
  assert.equal(b.storage.has(RAID_HISTORY_KEY), false);
  const history = raidFinish(b, { kills: 2 }); assert.equal(history.classes.adventurer.games, 1); assert.equal(history.classes.adventurer.kills, 2);
});

test('raid history: AttackSkull counter excludes Rune/poison-style collections and uncompleted Lich', () => {
  const b = raidBrowser({ menus: true }), r = b.raid;
  raidBoard(r, 'sword');
  r.state.board[0] = { type: 'skull', skull: raidSkull(r, { hp: 1, def: 0 }) };
  r.hitSkullAt(0, 10, { direct: true });
  r.state.board[1] = { type: 'skull', skull: raidSkull(r, { hp: 1, def: 0 }) };
  r.collectIndices([1]);
  r.state.board[2] = { type: 'skull', skull: raidSkull(r, { hp: 1, def: 0 }, 32) };
  r.hitSkullAt(2, 10, { direct: true });
  assert.equal(r.state.board[2].skull.phylactery, true); assert.equal(r.state.skullsKilled, 1);
  const history = raidFinish(b, { kills: r.state.skullsKilled });
  assert.equal(history.classes.adventurer.kills, 1);
});

test('raid history: Last Stand rescue does not record a game; its later actual death does', () => {
  const b = raidBrowser({ menus: true }), r = b.raid;
  raidBoard(r, 'sword'); r.state.hero.hp = 0; r.state.lastStand = 1;
  r.endEnemyTurn(); assert.equal(r.state.over, false); assert.equal(b.storage.has(RAID_HISTORY_KEY), false);
  assert.equal(raidFinish(b).classes.adventurer.games, 1);
});

test('raid history: terminal retry, delayed modal and repeated reload are idempotent even after list eviction', () => {
  const b = raidBrowser({ menus: true });
  raidFinish(b); const first = b.storage.get(RAID_HISTORY_KEY);
  for (let n = 0; n < 4; n++) { b.raid.saveRun(); b.raid.endEnemyTurn(); b.advance(1000); }
  assert.equal(b.storage.get(RAID_HISTORY_KEY), first);
  for (let n = 0; n < 3; n++) {
    const next = raidBrowser({ start: false, saved: b.storage });
    assert.equal(next.raid.savedRunInfo(), null); assert.equal(next.raid.resumeSavedRun(), false);
    assert.equal(b.storage.get(RAID_HISTORY_KEY), first);
  }
  const profile = JSON.parse(first), record = profile.records[1][0];
  profile.records[1] = []; profile.last = null; b.storage.set(RAID_HISTORY_KEY, JSON.stringify(profile));
  const next = raidBrowser({ start: false, saved: b.storage }); next.raid.savedRunInfo();
  assert.equal(next.raid.getLocalHistory().classes.adventurer.games, 1);
  assert.equal(next.raid.getLocalHistory().records[1].length, 0, 'retained receipt stops reinsertion of a nonlisted result');
  assert.ok(next.raid.getLocalHistory().completed.includes(record.id));
});

test('raid history: record IDs and read-only access do not consume any of the six gameplay RNG streams', () => {
  const a = raidBrowser({ seed: 913 }), b = raidBrowser({ seed: 913 });
  assert.notEqual(a.raid.state.historyId, b.raid.state.historyId);
  assert.deepEqual(raidJSON(a.raid.state.rngSeeds), raidJSON(b.raid.state.rngSeeds));
  assert.deepEqual(raidJSON(a.raid.state.board), raidJSON(b.raid.state.board));
  const before = JSON.stringify(a.raid.state);
  a.raid.getLocalHistory(); a.raid.getLocalHistory(); assert.equal(JSON.stringify(a.raid.state), before);
  assert.deepEqual(raidJSON(a.raid.makeShopOffers()), raidJSON(b.raid.makeShopOffers()));
});

test('raid history: Pretzel keeps only scoring chains and truncates its running mean on every update', () => {
  const b = raidBrowser({ mode: 'pretzel', menus: true });
  let average = 0, count = 0, total = 0;
  for (const cross of [0, 1, 2, 2, 3, 1, 3]) {
    raidPretzelChain(b, cross);
    if (cross) { average = Math.trunc((average * count + 10 * cross) / ++count); total += cross; }
    assert.deepEqual(raidJSON(b.raid.state.pretzelStats), { scoringChains: count, avgTimes10: average, maxCross: Math.min(3, count ? Math.max(...[1, 2, 2, 3, 1, 3].slice(0, count)) : 0) });
    b.advance(2000);
  }
  assert.equal(b.raid.state.pretzelScore, total); assert.notEqual(average, Math.trunc(10 * total / count), 'fixture distinguishes iterative from final-only truncation');
  const row = raidFinish(b).records[5][0];
  assert.deepEqual(raidJSON(row.pretzel), { scoringChains: 6, avgTimes10: average, maxCross: 3 });
  assert.equal(row.score, total);
});

test('raid history: Pretzel stats at the committed match boundary survive resume without double counting', () => {
  const b = raidBrowser({ mode: 'pretzel', menus: true });
  raidPretzelChain(b, 2);
  const stats = raidJSON(b.raid.state.pretzelStats), score = b.raid.runScore(), next = raidReload(b);
  assert.deepEqual(raidJSON(next.raid.state.pretzelStats), stats); assert.equal(next.raid.runScore(), score);
  next.advance(2000); assert.deepEqual(raidJSON(next.raid.state.pretzelStats), stats); assert.equal(next.raid.state.turn, 1);
});

test('raid history: released v1 active saves migrate without losing frozen setup, offers, RNG or identity on the next reload', () => {
  const b = raidBrowser({ menus: true, classId: 'mage', seed: 913 });
  b.raid.state.hero.gold = 100; b.raid.checkGoldBox(); assert.equal(b.raid.saveRun(), true);
  const raw = JSON.parse(b.storage.get(RAID_RUN_KEY)); delete raw.state.historyId; delete raw.state.pretzelStats;
  b.storage.set(RAID_RUN_KEY, JSON.stringify(raw));
  const expected = raidJSON([b.raid.state.board, b.raid.state.rngSeeds, b.raid.state.menuCandidates, b.raid.state.classConfig]);
  const next = raidReload(b); assert.match(next.raid.state.historyId, /^v1-/);
  assert.deepEqual(raidJSON([next.raid.state.board, next.raid.state.rngSeeds, next.raid.state.menuCandidates, next.raid.state.classConfig]), expected);
  const again = raidReload(next); assert.equal(again.raid.state.historyId, next.raid.state.historyId);
  assert.deepEqual(raidJSON(again.raid.state.pretzelStats), { scoringChains: 0, avgTimes10: 0, maxCross: 0 });
});

for (const oldScore of [0, 19]) test(`raid history: legacy Pretzel score ${oldScore} is kept without inventing unavailable crossing statistics`, () => {
  const b = raidBrowser({ mode: 'pretzel', menus: true }); b.raid.state.pretzelScore = oldScore; b.raid.saveRun();
  const raw = JSON.parse(b.storage.get(RAID_RUN_KEY)); delete raw.state.historyId; delete raw.state.pretzelStats; b.storage.set(RAID_RUN_KEY, JSON.stringify(raw));
  const next = raidReload(b); assert.equal(next.raid.runScore(), oldScore);
  assert.deepEqual(raidJSON(next.raid.state.pretzelStats), oldScore ? null : { scoringChains: 0, avgTimes10: 0, maxCross: 0 });
  raidPretzelChain(next, 1); next.advance(2000);
  const row = raidFinish(next).records[5][0]; assert.equal(row.score, oldScore + 1);
  assert.deepEqual(raidJSON(row.pretzel), oldScore ? null : { scoringChains: 1, avgTimes10: 10, maxCross: 1 });
});

test('raid history: old v1 terminal tombstones stay terminal and do not invent past games', () => {
  const b = raidBrowser({ start: false, saved: { [RAID_RUN_KEY]: JSON.stringify({ format: 'lunapark-raid-run', version: 1, status: 'finished' }) } });
  assert.equal(b.raid.resumeSavedRun(), false); assert.equal(b.raid.getLocalHistory().classes.adventurer.games, 0); assert.equal(b.storage.has(RAID_HISTORY_KEY), false);
});

test('raid history: a profile-only write failure preserves the previous profile and a recoverable terminal receipt', () => {
  const fault = {}, b = raidBrowser({ menus: true, fault });
  raidFinish(b); const first = b.storage.get(RAID_HISTORY_KEY);
  b.raid.newGame(71); fault.write = key => key === RAID_HISTORY_KEY; raidFinish(b, { kills: 7 });
  assert.equal(b.storage.get(RAID_HISTORY_KEY), first); assert.equal(b.raid.historyStatus.state, 'unavailable');
  assert.equal(JSON.parse(b.storage.get(RAID_RUN_KEY)).status, 'finished'); assert.equal(b.raid.savedRunInfo(), null);
  const next = raidBrowser({ start: false, saved: b.storage }); assert.equal(next.raid.resumeSavedRun(), false);
  const history = next.raid.getLocalHistory(); assert.equal(history.classes.adventurer.games, 2); assert.equal(history.classes.adventurer.kills, 10);
  const second = b.storage.get(RAID_HISTORY_KEY); next.raid.retryRunHistory(); next.raid.savedRunInfo(); assert.equal(b.storage.get(RAID_HISTORY_KEY), second);
});

test('raid history: same-page retry commits records and class counters together once after storage recovers', () => {
  const fault = { write: key => key === RAID_HISTORY_KEY }, b = raidBrowser({ menus: true, fault });
  raidFinish(b); assert.equal(b.storage.has(RAID_HISTORY_KEY), false); assert.equal(b.raid.historyStatus.state, 'unavailable');
  fault.write = false; assert.equal(b.raid.retryRunHistory(), true);
  assert.equal(b.raid.getLocalHistory().records[1].length, 1); assert.equal(b.raid.getLocalHistory().classes.adventurer.games, 1);
  const raw = b.storage.get(RAID_HISTORY_KEY); assert.equal(b.raid.retryRunHistory(), true); assert.equal(b.storage.get(RAID_HISTORY_KEY), raw);
});

test('raid history: a stale tab cannot finalize or modify profile totals after another tab resumes', () => {
  const b = raidBrowser({ menus: true }); raidBoard(b.raid, 'sword'); b.raid.saveRun(); const other = raidReload(b);
  raidFinish(b); assert.equal(b.storage.has(RAID_HISTORY_KEY), false); assert.equal(b.raid.saveStatus.state, 'conflict');
  assert.equal(raidFinish(other).classes.adventurer.games, 1);
});

test('raid history: concurrent profile change is preserved and explicit retry merges instead of overwriting it', () => {
  const other = raidBrowser({ menus: true }); raidFinish(other, { kills: 8 }); const rival = other.storage.get(RAID_HISTORY_KEY);
  const fault = {}, b = raidBrowser({ menus: true, fault }); let reads = 0;
  fault.read = key => { if (key === RAID_HISTORY_KEY && ++reads === 2) b.storage.set(key, rival); return false; };
  raidFinish(b, { kills: 5 }); assert.equal(b.raid.historyStatus.state, 'conflict'); assert.equal(b.storage.get(RAID_HISTORY_KEY), rival);
  fault.read = false; assert.equal(b.raid.retryRunHistory(), true);
  const history = b.raid.getLocalHistory(); assert.equal(history.classes.adventurer.games, 2); assert.equal(history.classes.adventurer.kills, 13);
});

for (const corruption of ['version', 'nan', 'html', 'prototype', 'shape', 'skills', 'duplicate', 'sort', 'last', 'oversize']) test(`raid history: malformed ${corruption} profile is not displayed as valid, overwritten or used as code`, () => {
  const fixture = raidBrowser({ menus: true }); raidFinish(fixture);
  const profile = JSON.parse(fixture.storage.get(RAID_HISTORY_KEY)); let raw;
  if (corruption === 'version') profile.version = 2;
  if (corruption === 'nan') profile.classes.adventurer.games = null;
  if (corruption === 'html') profile.records[1][0].classId = '<img src=x onerror=alert(1)>';
  if (corruption === 'prototype') profile.classes.adventurer.constructor = {};
  if (corruption === 'shape') profile.records = {};
  if (corruption === 'skills') profile.records[1][0].skills = [{ id: 'constructor', level: 1 }];
  if (corruption === 'duplicate') profile.records[1].push({ ...profile.records[1][0] });
  if (corruption === 'sort') { const row = profile.records[1][0]; profile.records[1].push({ ...row, id: 'run-higher', score: row.score + 1 }); }
  if (corruption === 'last') profile.last.index = 9;
  raw = corruption === 'oversize' ? ' '.repeat(256 * 1024 + 1) : JSON.stringify(profile);
  const b = raidBrowser({ menus: true, saved: { [RAID_HISTORY_KEY]: raw } });
  assert.equal(b.raid.getLocalHistory(), null); assert.equal(b.raid.historyStatus.state, 'invalid');
  raidFinish(b); assert.equal(b.storage.get(RAID_HISTORY_KEY), raw); assert.equal(b.raid.savedRunInfo(), null);
  assert.equal({}.polluted, undefined);
});

test('raid history: a bounded 512-receipt journal and listed-record check keep the profile small and retries idempotent', () => {
  const fixture = raidBrowser({ menus: true }); raidFinish(fixture); const data = fixture.raid.getLocalHistory();
  data.completed = Array.from({ length: 512 }, (_, i) => 'receipt-' + String(i).padStart(5, '0'));
  fixture.storage.set(RAID_HISTORY_KEY, JSON.stringify(data));
  fixture.raid.saveRun(); assert.equal(fixture.raid.getLocalHistory().classes.adventurer.games, 1, 'listed ID is still idempotent without its receipt');
  fixture.raid.newGame(71); const history = raidFinish(fixture);
  assert.equal(history.completed.length, 512); assert.equal(history.completed[0], 'receipt-00001');
  assert.equal(history.completed.at(-1), fixture.raid.state.historyId); assert.equal(history.classes.adventurer.games, 2);
});

test('raid history: terminal slot-write failure can still record the result when the independent profile remains writable', () => {
  const fault = {}, b = raidBrowser({ menus: true, fault }); fault.write = key => key === RAID_RUN_KEY;
  const history = raidFinish(b); assert.equal(history.classes.adventurer.games, 1); assert.equal(history.records[1].length, 1);
  assert.equal(b.raid.saveStatus.state, 'unavailable'); assert.equal(b.storage.has(RAID_RUN_KEY), false);
  assert.equal(b.raid.historyStatus.state, 'saved');
  const next = raidBrowser({ start: false, saved: b.storage }); assert.equal(next.raid.resumeSavedRun(), false); assert.equal(next.raid.getLocalHistory().classes.adventurer.games, 1);
});

test('raid history: profile-read denial leaves the terminal recovery receipt and can be retried without modifying the run', () => {
  const fault = {}, b = raidBrowser({ menus: true, fault }); fault.read = key => key === RAID_HISTORY_KEY;
  raidFinish(b); assert.equal(b.raid.historyStatus.state, 'unavailable'); assert.equal(b.storage.has(RAID_HISTORY_KEY), false);
  const terminal = b.storage.get(RAID_RUN_KEY); fault.read = false; assert.equal(b.raid.retryRunHistory(), true);
  assert.equal(b.storage.get(RAID_RUN_KEY), terminal); assert.equal(b.raid.getLocalHistory().classes.adventurer.games, 1);
});

test('raid history: an invalid terminal receipt is not replayed or used to alter persistent class statistics', () => {
  const fixture = raidBrowser({ menus: true }); raidFinish(fixture);
  const terminal = JSON.parse(fixture.storage.get(RAID_RUN_KEY)); terminal.record.classId = '<svg onload=alert(1)>';
  const raw = JSON.stringify(terminal), b = raidBrowser({ start: false, saved: { [RAID_RUN_KEY]: raw } });
  assert.equal(b.raid.resumeSavedRun(), false); assert.equal(b.raid.saveStatus.state, 'invalid');
  assert.equal(b.storage.has(RAID_HISTORY_KEY), false); assert.equal(b.storage.get(RAID_RUN_KEY), raw);
});

test('raid history: returned data is a detached read-only snapshot, not a mutable stored profile', () => {
  const b = raidBrowser({ menus: true }); const history = raidFinish(b), before = b.storage.get(RAID_HISTORY_KEY);
  history.records[1][0].score = 999999; history.classes.adventurer.games = 999;
  const next = b.raid.getLocalHistory(); assert.equal(next.classes.adventurer.games, 1); assert.notEqual(next.records[1][0].score, 999999);
  assert.equal(b.storage.get(RAID_HISTORY_KEY), before);
});
