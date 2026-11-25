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
function raidBrowser({ classId = 'adventurer', mode = 'classic', difficulty = 1, seed = 71, menus = false, saved = {}, start = true, fault = {}, variant = 'classic' } = {}) {
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
  vm.runInContext(readFileSync(new URL(variant === 'magitech' ? './raid_magitech.js' : './raid.js', import.meta.url), 'utf8'), context);
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
  r.endEnemyTurn(); assert.equal(r.state.over, false);
  assert.equal(r.getLocalHistory().classes.adventurer.games, 0);
  assert.equal(r.getLocalHistory().records.flat().length, 0);
  assert.equal(r.getLocalHistory().achievements.progress[61], 100, 'rescue is an achievement, not a finished game');
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

// Local achievements: real event paths, conservative v1 migration and receipts.
const achEarned = (raid, id) => raid.state.achievementRun.earned[id] || 0;
const achProgress = (raid, id) => raid.getLocalAchievements().catalog[id - 1].progress;
const achSnake = Array.from({ length: 36 }, (_, n) => Math.floor(n / 6) * 6 + (Math.floor(n / 6) % 2 ? 5 - n % 6 : n % 6));
const achRim = [0, 1, 2, 3, 4, 5, 11, 17, 23, 29, 35, 34, 33, 32, 31, 30, 24, 18, 12, 6];

function achCollectBroken(browser, count) {
  const r = browser.raid;
  raidBoard(r, 'sword'); r.state.forceCoins = 1;
  for (let slot = 0; slot < count; slot++) r.state.board[slot] = { type: slot % 2 ? 'shield' : 'sword', broken: true };
  r.collectIndices(Array.from({ length: count }, (_, slot) => slot));
  assert.equal(r.saveRun(), true);
}

test('raid achievements: 77 Russian entries, 76 available, no sentinel or invented I See trigger; reads are detached and write-free', () => {
  const b = raidBrowser({ start: false }), before = [...b.storage];
  const result = b.raid.getLocalAchievements();
  assert.equal(result.total, 77); assert.equal(result.available, 76); assert.equal(result.unlocked, 0);
  assert.deepEqual(Array.from(result.catalog, a => a.id), Array.from({ length: 77 }, (_, i) => i + 1));
  assert.ok(result.catalog.every(a => /[а-яё]/i.test(a.title) && a.description.length > 15 && a.progress === 0));
  assert.equal(result.catalog[44].available, false); assert.match(result.catalog[44].description, /не найден/);
  result.catalog[0].progress = 100; assert.equal(b.raid.getLocalAchievements().catalog[0].progress, 0);
  assert.deepEqual([...b.storage], before); assert.equal(b.raid.state, null);
});

for (const [native, classId, raceId] of [
  [1, 'ranger', 10], [2, 'raider', 15], [3, 'priest', 13], [4, 'barbarian', 14], [5, 'mage', 18],
  [6, 'rogue', 12], [7, 'paladin', 17], [8, 'warrior', 11], [9, 'assassin', 16],
]) test(`raid achievements: ${classId} class/race/master gates are 1/5/10, not run XP`, () => {
  for (const level of [1, 4, 5, 9, 10]) {
    const b = raidBrowser({ classId, saved: { 'raid:classLevels': JSON.stringify({ [classId]: level }) } }), r = b.raid;
    assert.equal(achProgress(r, native), 100);
    assert.equal(achProgress(r, raceId), level >= 5 ? 100 : 0);
    assert.equal(achProgress(r, native + 18), level === 10 ? 100 : 0);
    assert.equal(achProgress(r, 28), 0); assert.equal(achProgress(r, 29), 0);
  }
});

test('raid achievements: Classy excludes Adventurer, and a customized Human does not replace the native Dwarf unlock', () => {
  const b = raidBrowser({ classId: 'warrior', saved: { 'raid:classLevels': JSON.stringify({ warrior: 5 }) } }), r = b.raid;
  const config = r.defaultClassConfig('warrior'); config.race = 'Human';
  assert.equal(r.saveClassConfig('warrior', config), true); r.newGame(71);
  assert.equal(r.clsRace(), 'Human'); assert.equal(achEarned(r, 11), 100);
  for (const id of raidClassIds.slice(1)) { r.unlockClass(id); while (r.classLevelOf(id) < 10) r.levelUpClass(id); }
  assert.equal(r.classLevelOf('adventurer'), 1); assert.equal(achEarned(r, 28), 100);
  assert.equal(r.saveRun(), true); assert.equal(achProgress(r, 28), 100);
});

for (const [level, id] of [[5, 29], [10, 30], [25, 31], [50, 32], [100, 33]]) test(`raid achievements: actual XP arrival reaches hero ${level}, including exact milestone ${id}`, () => {
  const b = raidBrowser({ menus: true }), r = b.raid;
  r.state.hero.level = level - 1; r.state.hero.xp = 98;
  r.addResource({ type: 'xp', variant: 0 });
  assert.equal(r.state.hero.level, level); assert.equal(achEarned(r, id), 100);
  assert.equal(r.saveRun(), true); assert.equal(achProgress(r, id), 100);
});

for (const [cause, id] of [['spikes', 34], ['fire', 35], ['poison', 36], ['acid', 37]]) test(`raid achievements: ${cause} is strict raw HP<0 before Last Stand, not HP=0 or final death`, () => {
  for (const below of [false, true]) {
    const b = raidBrowser({ menus: true }), r = b.raid, s = r.state;
    raidBoard(r, 'sword'); s.lastStand = 1;
    const damage = cause === 'spikes' ? 7 : 2; s.hero.hp = damage - (below ? 1 : 0);
    if (cause === 'spikes') {
      raidOld(r, 2, { hp: 999, maxHp: 999 }, 2); s._chainOrder = [0, 1, 2];
      r.applyChainEffects(2, 0, 0, 0, [2]);
    } else if (cause === 'fire') {
      s.board[0].burn = 2; s._chainOrder = [0, 1, 2]; r.applyChainEffects(3, 0, 0, 0, []);
    } else if (cause === 'poison') { s.poison = 2; r.endEnemyTurn(); }
    else r.addResource({ type: 'potion', variant: 2 });
    assert.equal(s.over, false); assert.equal(s.hero.hp, s.hero.maxHp);
    assert.equal(achEarned(r, 61), 100); assert.equal(achEarned(r, id), below ? 100 : 0);
    assert.equal(achEarned(r, 67), 0);
  }
});

test('raid achievements: Nemesis is checked before rescue for the active race, not for Human or an absent mortal enemy', () => {
  for (const [classId, present, expected] of [['ranger', true, 100], ['ranger', false, 0], ['adventurer', true, 0]]) {
    const b = raidBrowser({ menus: true, classId }), r = b.raid;
    raidBoard(r, 'sword'); if (present) raidOld(r, 35, { hp: 999, maxHp: 999 }, 9);
    r.state.hero.hp = 0; r.state.lastStand = 1; r.endEnemyTurn();
    assert.equal(r.state.over, false); assert.equal(achEarned(r, 40), expected); assert.equal(achEarned(r, 61), 100);
    assert.equal(r.getLocalHistory().classes[classId].games, 0);
  }
});

for (const [classId, expected] of [['mage', 100], ['adventurer', 0]]) test(`raid achievements: Unstable uses the actual ${classId} mortal-enemy refill branch`, () => {
  const b = raidBrowser({ classId }), r = b.raid;
  raidBoard(r, 'coin'); r.unstableTeleport(); assert.equal(achEarned(r, 38), expected);
  assert.equal(r.state.refillFilter.excludeSwords, classId === 'mage');
});

test('raid achievements: Devourer requires a positive stat and a real reduction, not an empty selected item', () => {
  for (const positive of [false, true]) {
    const { raid: r } = raidCombat({ seed: 0 });
    if (!positive) { r.state.equipment.forEach(item => { item.mods = {}; }); r.updateEquipmentStats(); }
    raidOld(r, 35, { countdown: 1 }, 28); r.applySpecialAbilities();
    assert.equal(achEarned(r, 39), positive ? 100 : 0);
  }
});

test('raid achievements: promotion checks three distinct specials, and Trap applies to a generated special, not an ordinary skull', () => {
  for (const duplicate of [false, true]) {
    const { raid: r } = raidBrowser({ mode: 'sprint' }); raidBoard(r, 'sword');
    const first = raidOld(r, 0, {}, 1); raidOld(r, 1, {}, 2);
    if (duplicate) r.state.board[1].skull = first;
    r.state.oneHealthSkulls = 1; r.state.promotionRemaining = 0;
    const tile = r.createNewTile(); assert.equal(tile.type, 'skull'); assert.equal(tile.skull.hp, 1);
    assert.equal(achEarned(r, 41), duplicate ? 0 : 100); assert.equal(achEarned(r, 62), 100);
  }
  const { raid: r } = raidBrowser({ seed: 0 }); r.state.oneHealthSkulls = 1;
  for (let n = 0; n < 100; n++) r.createNewTile(); assert.equal(achEarned(r, 62), 0);
});

for (const [classId, type, id, amount] of [['warrior', 14, 42, 100], ['adventurer', 3, 44, 100], ['adventurer', 2, 46, 1], ['mage', 2, 55, 2]]) test(`raid achievements: special reward path ${id} counts collections, not a banish or an unfinished Lich`, () => {
  const { raid: r } = raidBrowser({ classId, seed: 0 }); raidBoard(r, 'sword'); r.state.forceCoins = 1;
  raidOld(r, 0, { hp: 1 }, type); r.collectIndices([0], { includeSpecial: true }); assert.equal(achEarned(r, id), amount);
  raidOld(r, 0, { hp: 1, def: 0 }, 32); assert.equal(r.hitSkullAt(0, 100, { direct: true }), false);
  assert.equal(achEarned(r, id), amount);
  raidOld(r, 0, { hp: 1 }, type); r.state.skillCooldowns.banish = 0; r.activateSkill('banish'); assert.equal(achEarned(r, id), amount);
});

test('raid achievements: Touche only comes from armour-stage spikes, never Counterattack', () => {
  for (const counter of [false, true]) {
    const { raid: r } = raidCombat({ seed: 0 }); r.state.forceCoins = 1; r.state.spikes = 5; r.state.counterattack = counter ? 1 : 0;
    raidOld(r, 0, { hp: 1, atk: 1, def: 0 }, 2); r.processEnemyAttack();
    assert.equal(achEarned(r, 43), counter ? 0 : 100);
  }
});

test('raid achievements: Elf poison advances only on lethal poison, while Werewolf and Dragonkin follow their own end-turn branches', () => {
  for (const [classId, id] of [['ranger', 47], ['assassin', 53], ['paladin', 54]]) {
    const { raid: r } = raidCombat({ classId, seed: 0 }); r.state.forceCoins = 1;
    raidOld(r, 0, { hp: 1, def: id === 53 ? 2 : 0, poison: id === 47 ? [1, 0, 0] : [0, 0, 0], burn: id === 54, fireDamage: 1 });
    raidOld(r, 1, { hp: 9, def: 0, poison: [1, 0, 0] });
    r.endEnemyTurn(); assert.equal(achEarned(r, id), 2, classId);
  }
});

test('raid achievements: Dwarf uppercut needs a successful upward kill, retains its priority over an outside Meat Shield', () => {
  for (const upward of [false, true]) {
    const b = raidBrowser({ classId: 'warrior' }), r = b.raid; raidBoard(r, 'sword'); r.state.forceCoins = 1;
    raidOld(r, 6, { hp: 1, def: 0 }); raidOld(r, 35, { hp: 999, maxHp: 999, def: 999 }, 30);
    raidSelect(r, upward ? [12, 6, 0] : [0, 6, 12]); r.resolveChain(); b.advance(420);
    assert.equal(achEarned(r, 48), upward ? 2 : 0);
  }
});

test('raid achievements: Sauren scroll purchase adds 2%, preview and non-Sauren purchase do not', () => {
  for (const classId of ['rogue', 'adventurer']) {
    const b = raidBrowser({ menus: true, classId }), r = b.raid; raidBoard(r, 'sword');
    r.state.hero.gold = 100; r.checkGoldBox();
    const item = r.state.menuCandidates[0]; item.scroll = r.prepareSkillOption('boostdmg'); delete item.scrollBlocked;
    assert.equal(achEarned(r, 49), 0); assert.equal(r.purchaseShopItem(0), true);
    assert.equal(achEarned(r, 49), classId === 'rogue' ? 2 : 0);
    r.purchaseShopItem(0); assert.equal(achEarned(r, 49), classId === 'rogue' ? 2 : 0);
  }
});

test('raid achievements: Faun adds 5% only when the quarter-HP level healing actually raises HP', () => {
  for (const low of [false, true]) {
    const { raid: r } = raidBrowser({ classId: 'priest', menus: true });
    r.state.hero.hp = low ? 1 : Math.floor(r.state.hero.maxHp / 4); r.state.hero.xp = 98;
    r.addResource({ type: 'xp', variant: 0 }); assert.equal(achEarned(r, 50), low ? 5 : 0);
  }
});

test('raid achievements: Orc stun successes add 2% on surviving direct hits, capped at 100, not on indirect hits or kills', () => {
  const { raid: r } = raidBrowser({ classId: 'barbarian' }); raidBoard(r, 'sword');
  const target = raidOld(r, 0, { hp: 999, maxHp: 999 }); let count = 0;
  for (let n = 0; n < 800; n++) { target.stunned = false; r.damageSkull(target, 0, { direct: true }); if (target.stunned) count++; }
  assert.ok(count >= 50); assert.equal(achEarned(r, 51), 100);
  const { raid: other } = raidBrowser({ classId: 'barbarian' }); raidBoard(other, 'sword');
  const victim = raidOld(other, 0, { hp: 999, maxHp: 999 });
  for (let n = 0; n < 100; n++) other.damageSkull(victim, 0, { direct: false });
  other.damageSkull(victim, 10000, { direct: true }); assert.equal(achEarned(other, 51), 0);
});

test('raid achievements: Halfling 15-kill Guts adds 10%; Masochism is a same-turn flag and survives an active checkpoint', () => {
  for (const masochism of [false, true]) {
    let b = raidBrowser({ classId: 'raider', menus: true }), r = b.raid;
    raidBoard(r, masochism ? 'coin' : 'sword'); r.state.forceCoins = 1;
    if (masochism) { r.activateSkill('masochism'); b = raidReload(b); r = b.raid; assert.equal(r.state.achievementRun.masochism, true); }
    raidBoard(r, 'sword');
    for (const slot of achSnake.slice(0, 15)) raidOld(r, slot, { hp: 1, def: 0 });
    raidSelect(r, achSnake.slice(0, 15)); r.resolveChain(); b.advance(420);
    assert.equal(achEarned(r, 52), 10); assert.equal(achEarned(r, 57), masochism ? 100 : 0);
  }
});

test('raid achievements: Sniper checks the ignored arrow gap, special tail kill and post-kill trophy conversion', () => {
  for (const variant of ['hit', 'trophy', 'ordinary', 'lich', 'survivor']) {
    const b = raidBrowser({ seed: 0 }), r = b.raid; raidBoard(r, 'sword'); r.state.forceCoins = 1;
    r.state.trophyRemaining = variant === 'trophy' ? 0 : 30;
    r.state.board[1] = { type: 'arrow' }; r.state.board[2] = { type: 'coin' };
    raidOld(r, 3, { hp: variant === 'survivor' ? 999 : 1, maxHp: 999, def: 0 }, variant === 'ordinary' ? 0 : variant === 'lich' ? 32 : 1);
    raidSelect(r, [0, 1, 2, 3]); r.resolveChain(); b.advance(420);
    assert.equal(achEarned(r, 56), variant === 'hit' ? 100 : 0, variant);
  }
});

test('raid achievements: Rune compares actual collectable interior with effective, not physical, contour length', () => {
  for (const arrows of [false, true]) {
    const b = raidBrowser(), r = b.raid; raidBoard(r, 'sword'); r.activateSkill('rune'); r.state.forceCoins = 1;
    if (arrows) achRim.forEach((slot, i) => { r.state.board[slot] = { type: i % 2 ? 'coin' : 'arrow' }; });
    assert.equal(r.interiorCells(achRim).length, 16);
    raidSelect(r, achRim); r.resolveChain(); b.advance(420);
    assert.equal(achEarned(r, 58), arrows ? 100 : 0);
  }
});

for (const [kind, id] of [['necklace', 59], ['rim', 68], ['gold', 69]]) test(`raid achievements: legal ${kind} path triggers ${id} only after collection, not its preview`, () => {
  const b = raidBrowser(), r = b.raid; raidBoard(r, kind === 'gold' ? 'coin' : 'sword'); r.state.forceCoins = 1;
  if (kind === 'necklace') r.state.board[7] = { type: 'chain' };
  raidSelect(r, kind === 'rim' ? achRim : achSnake); assert.equal(achEarned(r, id), 0);
  r.resolveChain(); assert.equal(achEarned(r, id), 0); b.advance(420); assert.equal(achEarned(r, id), 100);
  if (kind === 'necklace') assert.equal(achEarned(r, 69), 0);
});

test('raid achievements: Slasher needs six ordinary skulls in the collected row, not five plus a special', () => {
  for (const special of [false, true]) {
    const { raid: r } = raidBrowser({ seed: 0 }); raidBoard(r, 'sword'); r.state.forceCoins = 1;
    for (let slot = 0; slot < 36; slot++) raidOld(r, slot, { hp: 999, maxHp: 999 }, special && slot % 6 === 0 ? 2 : 0);
    r.activateSkill('slash'); assert.equal(achEarned(r, 60), special ? 0 : 100);
  }
});

test('raid achievements: Shock and Awe is the four collection flags, survives resume and resets on enemy turn; 40/41 are not substitutes', () => {
  let b = raidBrowser({ menus: true }), r = b.raid;
  for (const id of ['slash', 'fireball']) { raidBoard(r, 'sword'); r.activateSkill(id); }
  b = raidReload(b); r = b.raid; assert.equal(r.state.achievementRun.combo, 3);
  for (const id of ['charge', 'wish']) { raidBoard(r, 'sword'); r.activateSkill(id); }
  assert.equal(achEarned(r, 63), 100);
  const other = raidBrowser().raid; raidBoard(other, 'sword'); other.activateSkill('shock'); other.activateSkill('awe'); assert.equal(achEarned(other, 63), 0);
  const reset = raidBrowser().raid; raidBoard(reset, 'sword'); reset.activateSkill('slash'); reset.activateSkill('fireball');
  raidBoard(reset, 'sword'); reset.endEnemyTurn(); assert.equal(reset.state.achievementRun.combo, 0);
  raidBoard(reset, 'sword'); reset.activateSkill('charge'); raidBoard(reset, 'sword'); reset.activateSkill('wish'); assert.equal(achEarned(reset, 63), 0);
});

test('raid achievements: Hack+Slash can supply the CollectBoard flag, independently from the two-skill cosmetic combo', () => {
  const { raid: r } = raidBrowser();
  for (const id of ['hack', 'slash', 'fireball', 'charge']) { raidBoard(r, 'sword'); r.activateSkill(id); }
  assert.equal(achEarned(r, 63), 100);
});

test('raid achievements: Wish threshold is 20 ordinary skulls before collection, not 19 plus special enemies', () => {
  for (const count of [19, 20]) {
    const { raid: r } = raidBrowser(); raidBoard(r, 'sword'); r.state.forceCoins = 1;
    for (let slot = 0; slot < count; slot++) raidOld(r, slot, { hp: 999, maxHp: 999 });
    raidOld(r, 35, { hp: 999, maxHp: 999 }, 1); r.activateSkill('wish'); assert.equal(achEarned(r, 64), count === 20 ? 100 : 0);
  }
});

test('raid achievements: Teleport needs a special before activation; one percent per cast, not per special; repeated saves do not increment', () => {
  const b = raidBrowser(), r = b.raid; raidBoard(r, 'sword'); r.activateSkill('teleport'); assert.equal(achEarned(r, 65), 0);
  for (let slot = 32; slot < 36; slot++) raidOld(r, slot, { hp: 999, maxHp: 999 }, 2);
  r.state.skillCooldowns.teleport = 0; r.activateSkill('teleport'); assert.equal(achEarned(r, 65), 1);
  for (let n = 0; n < 3; n++) r.saveRun(); assert.equal(achProgress(r, 65), 1);
});

test('raid achievements: Big Game provenance survives reload; Safari requires an actual trophy, not just its kill', () => {
  for (const eligible of [false, true]) {
    let b = raidBrowser({ seed: 0, menus: true, saved: eligible ? {} : { 'raid:classLevels': JSON.stringify(Object.fromEntries(raidClassIds.map(id => [id, 10]))) } }), r = b.raid;
    raidBoard(r, 'sword'); r.state.forceCoins = 1; r.state.trophyRemaining = 0; raidOld(r, 0, { hp: 1 });
    r.activateSkill('biggame'); assert.equal(r.state.board[0].skull.promotedByBigGame, true);
    b = raidReload(b); r = b.raid; assert.equal(r.state.board[0].skull.promotedByBigGame, true);
    r.hitSkullAt(0, 1000, { direct: true }); assert.equal(achEarned(r, 66), eligible ? 100 : 0);
    assert.equal(r.state.trophiesGenerated, eligible ? 1 : 0);
  }
});

for (const difficulty of [0, 1, 2, 3]) test(`raid achievements: difficulty ${difficulty} gates all event types; only final67 bypasses Easy, including Sprint`, () => {
  const b = raidBrowser({ menus: true, mode: 'sprint', difficulty, classId: 'mage' }), r = b.raid;
  achCollectBroken(b, 2); r.state.hero.level = 4; r.state.hero.xp = 98; r.addResource({ type: 'xp', variant: 0 });
  if (r.state.pendingModal === 'level') { raidPickLevel(b.elements); b.advance(80); }
  raidBoard(r, 'sword'); r.state.turn = 99; r.state.hero.hp = r.state.hero.maxHp; r.state.poison = 0; r.endEnemyTurn();
  assert.equal(r.state.won, true); assert.equal(achEarned(r, 67), 100); assert.equal(achProgress(r, 67), 100);
  for (const id of [5, 29, 73, 76]) assert.equal(achProgress(r, id), difficulty === 0 ? 0 : id === 73 ? 2 : 100, String(id));
  if (!difficulty) assert.deepEqual(raidJSON(r.state.achievementRun.earned), { 67: 100 });
});

test('raid achievements: Harder setup challenges use frozen perk/flaw counts and exact level; lower difficulty and an equipped skill fail', () => {
  for (const difficulty of [1, 3]) for (const skill of [false, true]) {
    const b = raidBrowser({ classId: 'barbarian', menus: true, difficulty, saved: { 'raid:classLevels': JSON.stringify(Object.fromEntries(raidClassIds.map(id => [id, 10]))) } }), r = b.raid;
    const config = r.defaultClassConfig('barbarian'); config.flaws = ['Reckless', 'Frail', 'Sneaky', 'Clumsy'];
    assert.equal(r.saveClassConfig('barbarian', config), true); r.newGame(71);
    if (skill) { r.state.skills = ['heal']; r.state.skillLevels.heal = 1; }
    r.state.hero.level = 4; r.state.hero.xp = 98; r.addResource({ type: 'xp', variant: 0 });
    assert.equal(achEarned(r, 71), difficulty === 3 && !skill ? 100 : 0);
    raidPickLevel(b.elements); b.advance(80);
    const next = r.defaultClassConfig('barbarian'); assert.equal(r.saveClassConfig('barbarian', next), true);
    r.state.hero.level = 9; r.state.hero.xp = 98; r.addResource({ type: 'xp', variant: 0 });
    assert.equal(achEarned(r, 70), difficulty === 3 ? 100 : 0);
  }
});

test('raid achievements: Armed uses eight distinct raw nonzero worn stats at active serialization, not seven or a shop preview', () => {
  const b = raidBrowser({ menus: true }), r = b.raid;
  const keys = ['weaponDamage', 'baseDamage', 'pierce', 'leech', 'quicken', 'xpPerSkull', 'strength', 'luck'];
  r.state.equipment[0].mods = Object.fromEntries(keys.slice(0, 7).map(key => [key, 1])); r.saveRun(); assert.equal(achEarned(r, 72), 0);
  r.state.hero.gold = 100; r.checkGoldBox(); r.state.menuCandidates[0].mods = Object.fromEntries(keys.map(key => [key, 1]));
  assert.equal(r.saveRun(), true); assert.equal(achEarned(r, 72), 0);
  r.state.equipment[0].mods.luck = 1; assert.equal(r.saveRun(), true); assert.equal(achProgress(r, 72), 100);
});

for (const count of [9, 10]) test(`raid achievements: all-skull post-chain refill needs >9 generated cells (${count})`, () => {
  let b = raidBrowser({ menus: true }), r = b.raid; raidBoard(r, 'sword'); r.state.turn = 10; r.saveRun();
  const checkpoint = JSON.parse(b.storage.get(RAID_RUN_KEY)); checkpoint.rng[1] = 1857768; b.storage.set(RAID_RUN_KEY, JSON.stringify(checkpoint));
  b = raidReload(b); r = b.raid;
  raidSelect(r, achSnake.slice(0, count)); r.resolveChain(); b.advance(420);
  assert.equal(r.state.board.filter(cell => cell.type === 'skull').length, count, 'seeded TILE fixture actually generated every skull');
  assert.equal(achEarned(r, 74), count > 9 ? 100 : 0);
});

test('raid achievements: real legal high-crossing path works outside Pretzel too; 77 stays locked below fifteen', () => {
  // Numerical path fixture, reflected from the audited 14-cross board example.
  const path = [35, 28, 33, 26, 31, 24, 30, 25, 32, 27, 34, 29, 23, 16, 21, 14, 19, 12, 18, 13, 20, 15, 22, 17, 11, 5, 4, 9, 2, 7, 0, 6, 1, 8, 3, 10].map(slot => Math.floor(slot / 6) * 6 + 5 - slot % 6);
  for (const mode of ['classic', 'pretzel']) {
    const b = raidBrowser({ mode }), r = b.raid; raidBoard(r, 'sword'); r.state.forceCoins = 1;
    assert.equal(r.pretzelCrossings(path), 14); raidSelect(r, path); r.resolveChain(); b.advance(420);
    assert.equal(achEarned(r, 75), 100); assert.equal(achEarned(r, 77), 0); assert.equal(r.state.pretzelScore, mode === 'pretzel' ? 42 : 0);
  }
});

test('raid achievements: 77 scoring primitive uses >=15 only in Pretzel (synthetic repeated nodes, NOT a legal user gesture)', () => {
  for (const mode of ['classic', 'pretzel']) for (const crosses of [14, 15]) {
    const b = raidBrowser({ mode }), r = b.raid; raidBoard(r, 'sword'); r.state.forceCoins = 1;
    // Deliberately bypass selection to isolate the numeric branch. Save validation
    // rejects this intermediate duplicate-node fixture; no natural-run claim.
    r.state.selected = Array.from({ length: crosses + 2 }, (_, i) => i % 2 ? 7 : 0);
    assert.equal(r.pretzelCrossings(r.state.selected), crosses); r.resolveChain(); b.advance(420);
    assert.equal(achEarned(r, 77), mode === 'pretzel' && crosses >= 15 ? 100 : 0);
  }
});

test('raid achievements: capped percentages combine distinct runs, survive repeated save/reload, and do not add games on restart', () => {
  let b = raidBrowser({ menus: true }); achCollectBroken(b, 3);
  assert.equal(achProgress(b.raid, 73), 3);
  for (let n = 0; n < 5; n++) { b.raid.saveRun(); b = raidReload(b); assert.equal(achProgress(b.raid, 73), 3); }
  b.raid.newGame(72); achCollectBroken(b, 2); assert.equal(achProgress(b.raid, 73), 5);
  assert.equal(b.raid.getLocalHistory().classes.adventurer.games, 0);
  for (let n = 0; n < 4; n++) achCollectBroken(b, 30);
  assert.equal(achEarned(b.raid, 73), 100); assert.equal(achProgress(b.raid, 73), 100);
  assert.ok(b.raid.getLocalHistory().achievements.runs.every(run => !Object.hasOwn(run.earned, '73')));
});

test('raid achievements: profile quota failure keeps earned progress in the active checkpoint; reload/retry merges once', () => {
  const fault = { write: key => key === RAID_HISTORY_KEY }, b = raidBrowser({ menus: true, fault });
  achCollectBroken(b, 3); assert.equal(b.raid.historyStatus.state, 'unavailable'); assert.equal(b.storage.has(RAID_HISTORY_KEY), false);
  const saved = JSON.parse(b.storage.get(RAID_RUN_KEY)); assert.equal(saved.state.achievementRun.earned[73], 3);
  const next = raidReload(b); assert.equal(achProgress(next.raid, 73), 3); const raw = next.storage.get(RAID_HISTORY_KEY);
  next.raid.retryRunHistory(); next.raid.saveRun(); assert.equal(next.storage.get(RAID_HISTORY_KEY), raw);
});

test('raid achievements: active-slot quota failure still persists event receipts in the independent writable profile', () => {
  const fault = {}, b = raidBrowser({ menus: true, fault }); fault.write = key => key === RAID_RUN_KEY;
  const r = b.raid; raidBoard(r, 'sword'); r.state.forceCoins = 1; r.state.board[0].broken = true; r.collectIndices([0]);
  assert.equal(r.saveRun(), false); assert.equal(achProgress(r, 73), 1); assert.equal(r.saveStatus.state, 'unavailable');
  r.saveRun(); assert.equal(achProgress(r, 73), 1);
});

test('raid achievements: terminal backup combines final events, incremental high-water receipts, rankings and class totals exactly once', () => {
  const fault = {}, b = raidBrowser({ menus: true, fault }); achCollectBroken(b, 3);
  fault.write = key => key === RAID_HISTORY_KEY; raidFinish(b);
  const terminal = JSON.parse(b.storage.get(RAID_RUN_KEY)); assert.equal(terminal.record.achievements[73], 3); assert.equal(terminal.record.achievements[67], 100);
  const next = raidBrowser({ menus: true, start: false, saved: b.storage }); assert.equal(next.raid.savedRunInfo(), null);
  assert.equal(achProgress(next.raid, 73), 3); assert.equal(achProgress(next.raid, 67), 100); assert.equal(next.raid.getLocalHistory().classes.adventurer.games, 1);
  const raw = next.storage.get(RAID_HISTORY_KEY); next.raid.retryRunHistory(); next.raid.savedRunInfo(); assert.equal(next.storage.get(RAID_HISTORY_KEY), raw);
});

test('raid achievements: history-v1 defaults are read-only and preserve all records, receipts, bests and independent counters on first write', () => {
  const b = raidBrowser({ menus: true }); raidFinish(b); const profile = JSON.parse(b.storage.get(RAID_HISTORY_KEY));
  delete profile.achievements; for (const rows of profile.records) for (const row of rows) delete row.achievements;
  const raw = JSON.stringify(profile); const saved = new Map([[RAID_HISTORY_KEY, raw]]), next = raidBrowser({ start: false, saved });
  assert.equal(achProgress(next.raid, 67), 0, 'old death events are not fabricated'); assert.equal(saved.get(RAID_HISTORY_KEY), raw);
  const before = raidJSON(next.raid.getLocalHistory()); next.raid.newGame(71); achCollectBroken(next, 2);
  const after = next.raid.getLocalHistory();
  for (const key of ['records', 'classes', 'completed', 'last']) assert.deepEqual(raidJSON(after[key]), before[key]);
  assert.equal(after.achievements.progress[73], 2); assert.equal(after.version, 1);
});

test('raid achievements: released active-v1 migration keeps RNG, rolled offers, partial level draft, I1 history and frozen configuration; no unknown past combo', () => {
  const b = raidBrowser({ menus: true, classId: 'mage' }), r = b.raid;
  r.state.hero.xp = 100; r.checkLevelUp(); b.elements.get('modal-body').querySelectorAll('.lv-card')[0].click(); r.saveRun();
  const raw = JSON.parse(b.storage.get(RAID_RUN_KEY)); delete raw.state.achievementRun; const old = raidJSON(raw);
  b.storage.set(RAID_RUN_KEY, JSON.stringify(raw)); const next = raidReload(b), restored = next.raid.state;
  assert.deepEqual(raidJSON(restored.achievementRun), { version: 1, earned: { 5: 100 }, combo: 0, masochism: false });
  assert.deepEqual(raidJSON(restored.rngSeeds), old.rng); assert.deepEqual(raidJSON(restored.classConfig), old.state.classConfig);
  assert.deepEqual(raidJSON(restored.scrollLeechLabel), old.state.scrollLeechLabel); assert.deepEqual(Array.from(restored.levelDraft), old.menu.picked);
  assert.deepEqual(raidJSON(next.raid.state.menuCandidates), raidJSON(r.state.menuCandidates));
});

for (const corruption of ['unknown', 'negative', 'fraction', 'sentinel', 'unconfirmed', 'receipt', 'duplicate']) test(`raid achievements: malformed ${corruption} profile remains untouched instead of resetting records`, () => {
  const b = raidBrowser({ menus: true }); achCollectBroken(b, 3); const data = JSON.parse(b.storage.get(RAID_HISTORY_KEY));
  if (corruption === 'unknown') data.achievements.version = 2;
  if (corruption === 'negative') data.achievements.progress[73] = -1;
  if (corruption === 'fraction') data.achievements.progress[73] = 1.5;
  if (corruption === 'sentinel') data.achievements.progress[0] = 100;
  if (corruption === 'unconfirmed') data.achievements.progress[45] = 100;
  if (corruption === 'receipt') data.achievements.runs[0].earned[73] = 101;
  if (corruption === 'duplicate') data.achievements.runs.push(data.achievements.runs[0]);
  const raw = JSON.stringify(data); b.storage.set(RAID_HISTORY_KEY, raw);
  assert.equal(b.raid.getLocalAchievements(), null); b.raid.retryRunHistory(); b.raid.saveRun();
  assert.equal(b.storage.get(RAID_HISTORY_KEY), raw); assert.equal(b.raid.historyStatus.state, 'invalid');
});

test('raid achievements: stale run cannot write events; concurrent profile changes are re-read and merged by explicit retry', () => {
  const a = raidBrowser({ menus: true }), peer = raidReload(a); const terminalBefore = a.storage.get(RAID_HISTORY_KEY);
  raidBoard(a.raid, 'sword'); a.raid.state.forceCoins = 1; a.raid.state.board[0].broken = true; a.raid.collectIndices([0]);
  assert.equal(a.raid.saveRun(), false); assert.equal(a.storage.get(RAID_HISTORY_KEY), terminalBefore);
  achCollectBroken(peer, 2); assert.equal(achProgress(peer.raid, 73), 2);
  const data = JSON.parse(peer.storage.get(RAID_HISTORY_KEY)); data.achievements.progress[73] = 5; data.revision++;
  peer.storage.set(RAID_HISTORY_KEY, JSON.stringify(data));
  achCollectBroken(peer, 1); assert.equal(achProgress(peer.raid, 73), 6);
});

test('raid achievements: missing queued damage cause in a released checkpoint is not guessed retroactively', () => {
  const b = raidBrowser({ menus: true }), r = b.raid; raidBoard(r, 'sword');
  r.state.hero.xp = 98; r.state.hero.hp = 1; r.state.lastStand = 1;
  raidOld(r, 2, { hp: 1, def: 0 }, 2); r.state.forceCoins = 1;
  raidSelect(r, [0, 1, 2]); r.resolveChain(); b.advance(420);
  assert.equal(r.state.pendingModal, 'level'); assert.ok(r.state.arrivals.some(event => event.cause === 34));
  assert.equal(r.saveRun(), true); const data = JSON.parse(b.storage.get(RAID_RUN_KEY)); for (const event of data.arrivals) delete event.cause;
  delete data.state.achievementRun; b.storage.set(RAID_RUN_KEY, JSON.stringify(data));
  const next = raidReload(b); raidPickLevel(next.elements); next.advance(2000);
  assert.equal(achEarned(next.raid, 34), 0); assert.equal(achEarned(next.raid, 61), 100);
});

for (const kind of ['version', 'amount', 'combo', 'raceFlag', 'easy', 'source']) test(`raid achievements: invalid active extension ${kind} cannot replace a live run or corrupt its prior checkpoint`, () => {
  const b = raidBrowser({ menus: true }), r = b.raid, original = b.storage.get(RAID_RUN_KEY), data = JSON.parse(original);
  if (kind === 'version') data.state.achievementRun.version = 2;
  if (kind === 'amount') data.state.achievementRun.earned[73] = 101;
  if (kind === 'combo') data.state.achievementRun.combo = 16;
  if (kind === 'raceFlag') data.state.achievementRun.masochism = 'yes';
  if (kind === 'easy') { data.state.difficulty = 0; data.state.achievementRun.earned[73] = 1; }
  if (kind === 'source') { data.arrivals = [{ kind: 'damage', amount: 1, result: 0, cause: 999 }]; data.results = [{ xpGained: 0, goldGained: 0, upGained: 0, armorGained: 0, healGained: 0, dmgShown: 0, kills: 0, bossKills: 0, manaCollected: 0 }]; }
  const state = r.state, rng = raidJSON(state.rngSeeds), invalid = JSON.stringify(data); b.storage.set(RAID_RUN_KEY, invalid);
  assert.equal(r.resumeSavedRun(), false); assert.equal(r.state, state); assert.deepEqual(raidJSON(state.rngSeeds), rng);
  assert.equal(b.storage.get(RAID_RUN_KEY), invalid);
});

test('raid achievements: active profile conflict preserves the other writer and retries the unapplied delta against its new baseline', () => {
  const fault = {}, b = raidBrowser({ menus: true, fault }); achCollectBroken(b, 2);
  const baseline = b.storage.get(RAID_HISTORY_KEY), rival = JSON.parse(baseline); rival.achievements.progress[73] = 5; rival.revision++;
  const raw = JSON.stringify(rival); let reads = 0;
  fault.read = key => { if (key === RAID_HISTORY_KEY && ++reads === 2) b.storage.set(RAID_HISTORY_KEY, raw); return false; };
  achCollectBroken(b, 1); assert.equal(b.raid.historyStatus.state, 'conflict'); assert.equal(b.storage.get(RAID_HISTORY_KEY), raw);
  fault.read = false; assert.equal(b.raid.retryRunHistory(), true); assert.equal(achProgress(b.raid, 73), 6);
});

test('raid achievements: 64 bounded partial-run receipts coexist with the released 512 terminal receipts', () => {
  const b = raidBrowser({ menus: true }), data = b.raid.getLocalHistory();
  data.completed = Array.from({ length: 512 }, (_, i) => 'terminal-' + String(i).padStart(5, '0'));
  data.achievements.progress[73] = 64;
  data.achievements.runs = Array.from({ length: 64 }, (_, i) => ({ id: 'partial-' + String(i).padStart(5, '0'), earned: { 73: 1 } }));
  b.storage.set(RAID_HISTORY_KEY, JSON.stringify(data)); achCollectBroken(b, 1);
  const after = b.raid.getLocalHistory(); assert.equal(after.achievements.progress[73], 65); assert.equal(after.achievements.runs.length, 64);
  assert.equal(after.achievements.runs.at(-1).id, b.raid.state.historyId); assert.deepEqual(raidJSON(after.completed), data.completed);
  assert.ok(b.storage.get(RAID_HISTORY_KEY).length < 262144);
});

// Released v1 shape, created only INSIDE TESTS. Production never downgrades or
// imports a classic profile. These fixtures retain the prior combat/6-RNG checks
// while new starts intentionally use the approved elemental rules.
function legacyMagitechSave(value) {
  const data = raidJSON(value); data.version = 1; delete data.combat;
  for (const cell of data.state.board) {
    delete cell.element;
    if (cell.skull) { delete cell.skull.element; delete cell.skull.secondaryElement; }
  }
  return data;
}
function legacyMagitechBrowser(options = {}) {
  const b = raidBrowser(Object.assign({}, options, { variant: 'magitech' }));
  assert.equal(b.raid.saveRun(), true);
  b.storage.set('raid:magitech:activeRun', JSON.stringify(legacyMagitechSave(JSON.parse(b.storage.get('raid:magitech:activeRun')))));
  assert.equal(b.raid.resumeSavedRun(), true);
  assert.equal(b.raid.savedRunInfo().combatRules, 'classic-v1');
  return b;
}

// Controlled fixtures, not native replay or naturally played runs.
const magitechSnapshot = raid => {
  const state = raidJSON(raid.state);
  delete state.historyId; // Independent, non-gameplay UUID.
  delete state.log; // Terminology is presentation (class -> mech).
  // Item-offer wording is presentation too; the redesign may clarify it without
  // making a legacy Continue acquire different gameplay state.
  for (const offer of state.menuCandidates || []) {
    delete offer.name; delete offer.label; delete offer.desc;
  }
  return state;
};
function settleMagitechFixture(browser) {
  for (let guard = 0; guard < 30; guard++) {
    browser.advance(2000);
    const modal = browser.raid.state.pendingModal;
    if (modal === 'level') raidPickLevel(browser.elements);
    else if (modal === 'gold') raidPickShop(browser.elements);
    else if (modal === 'armor') browser.elements.get('modal-body').querySelectorAll('.lv-card')[0].click();
    else if (!browser.raid.state.resolving || browser.raid.state.over) return;
  }
  throw new Error('Controlled redesign fixture did not settle');
}

test('magitech source: unchanged combat/resource/generator functions remain byte-identical to the classic control', () => {
  const original = readFileSync(new URL('./raid.js', import.meta.url), 'utf8');
  const fork = readFileSync(new URL('./raid_magitech.js', import.meta.url), 'utf8');
  const names = ['random', 'randInt', 'createTile', 'createNewTile', 'setupBoard',
    'damageSkull', 'damageHero', 'baseDamage', 'swordDamage', 'chainDamageFor',
    'processEnemyAttack', 'endEnemyTurn', 'enemyGetAttack',
    'perShield', 'bonusPct', 'addResource', 'makeItem', 'makeUpgradeOffer',
    'applyUpgradeOffer', 'equipItem', 'updateEquipmentStats', 'classicScrollWord',
    'classRules', 'runClassConfig', 'specialSpawnCount'];
  for (const name of names) {
    const expression = new RegExp(`^  function ${name}\\([^]*?^  }`, 'm');
    const a = original.match(expression)?.[0], b = fork.match(expression)?.[0];
    assert.ok(a && b, name);
    assert.equal(b, a, `Elemental integration must not rebalance ${name}`);
  }
});

test('magitech player copy explains old runs and the weakening shield modifier in plain language', () => {
  const html = readFileSync(new URL('./raid_magitech.html', import.meta.url), 'utf8');
  const js = readFileSync(new URL('./raid_magitech.js', import.meta.url), 'utf8');
  assert.match(html, /если рейд уже начат, он продолжится по прежним правилам — без склонности\. Склонность можно выбрать перед следующим рейдом\. Ничего удалять не нужно\./);
  assert.ok(html.includes('<li><b>Понятнее описания экипировки.</b> Уточнили, когда срабатывает снижение атаки врагов и другие свойства предметов. Правила боя не менялись.</li>'));
  assert.ok(html.includes('<li><b>Четыре узнаваемых хрустальных зелья.</b> Лечение — рубин, кислота — пузырящийся лайм, мана — сапфир со звездой, взрывное — янтарь с искрой. Крышка закреплена, нижняя часть раскачивается, жидкость заметно плещется внутри. Игровые эффекты не менялись.</li>'));
  assert.ok(html.includes('<li><b>Щиты на поле.</b> Пять моделей — деревянная, круглая, башенная, гербовая и руническая — теперь показываются по надетому щиту. После покупки картинка на поле обновляется сразу, как у меча. Характеристики защиты не менялись.</li>'));
  assert.ok(html.includes('<li><b>Гараж мехов.</b> В круглой карточке мех показан крупно; сбоку — кнопки «Пилот» и «Мех», снизу фигурная кнопка «Поехали!». Все десять портретов перерисованы с чистым прозрачным фоном: края и детали брони восстановлены, случайные фрагменты фона удалены. Карточка закрывается нажатием вне круга. Фигурки слегка парят; движение учитывает настройку анимации и системное «уменьшение движения».</li>'));
  assert.ok(html.includes('<li><b>Выбор стихии — отдельный шаг после выбора меха.</b> На втором экране четыре стихии занимают четверти экрана. Выбери Огонь, Воздух, Землю или Воду: центральная эмблема меняет оттенок; нажми её, чтобы начать рейд. Здесь же виден текущий формат «До гибели», выбирается сложность; кнопка «Назад» возвращает в гараж.</li>'));
  assert.ok(html.includes('<li><b>Мечи в бою — без полного вращения.</b> На поле клинок едва покачивается; теперь по его силуэту заметно проходит короткий холодный металлический блик. При сборе меча для атаки появляется отдельный росчерк с послесвечением; длительность хода и урон не меняются.</li>'));
  assert.match(js, /textContent = 'Поехали!'/);
  assert.match(js, /\['pilot', 'Пилот', 'О пилоте'\], \['mech', 'Мех', 'О мехе'\]/);
  assert.match(js, /className = 'mech-garage-hero'/);
  assert.match(js, /event\.target === detail\) closeDetail\(\)/);
  assert.match(js, /requestNewGame\(cls\.id\)/);
  assert.match(js, /name: 'Снижение атаки врагов'.*desc: 'Если перед атакой врагов у тебя есть броня, после этого залпа снижает силу атаки черепов, уже стоявших на поле\. Снижение равно бонусу; сила атаки не опускается ниже 1'/);
  assert.doesNotMatch(html + js, /старые v1\/v2\/v3|без миграции|текущий залп|токен опыта|токен монеты|к кулдаунам/);
});

test('magitech potion glass: health, acid, mana and explosive fills get clipped slosh and premium rocking', () => {
  const js = readFileSync(new URL('./raid_magitech.js', import.meta.url), 'utf8');
  const css = readFileSync(new URL('./raid_magitech.css', import.meta.url), 'utf8');
  assert.match(js, /cell\.acid \? 'acid' : cell\.mana \? 'mana' : cell\.explosive \? 'explosive' : 'health'/);
  for (const kind of ['health', 'acid', 'mana', 'explosive']) assert.ok(js.includes(`${kind}: '#`), kind + ' liquid palette');
  assert.match(js, /raid-potion-fill-\$\{cellIdx\}/);
  assert.match(js, /class="potion-vessel" data-potion="\$\{kind\}"/);
  assert.match(js, /class="potion-wave potion-wave-back"/);
  assert.match(js, /class="potion-wave potion-wave-front"/);
  assert.match(js, /!!cell\.explosive.*cell\.magicSword/);
  assert.match(css, /@keyframes potion-rock[^]*rotate\(-6\.5deg\)[^]*rotate\(6\.5deg\)/);
  assert.match(css, /@keyframes potion-slosh-back[^]*translate\(-3px,1px\)[^]*translate\(3px,-1\.4px\)/);
  assert.match(css, /@keyframes potion-slosh-front[^]*translate\(2\.5px,-\.8px\)[^]*translate\(-2\.5px,1\.2px\)/);
  assert.match(css, /\.cell\.type-potion\.frozen[^]*animation-play-state: paused/);
  assert.match(css, /html\[data-raid-motion="off"\][^]*animation: none !important/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
});

test('magitech potion renderer: each fill state creates a unique clipped bottle and its own liquid/accent', () => {
  const js = readFileSync(new URL('./raid_magitech.js', import.meta.url), 'utf8');
  const css = readFileSync(new URL('./raid_magitech.css', import.meta.url), 'utf8');
  const from = js.indexOf('  function tileSymbol('), to = js.indexOf('\n  // Alchemy reference:', from);
  assert.ok(from >= 0 && to > from);
  const render = vm.runInNewContext('(' + js.slice(from, to).trim() + ')', {
    start: '<svg viewBox="0 0 48 48" class="ic" aria-hidden="true">', end: '</svg>',
  });
  const variants = [
    [{ type: 'potion' }, 'health', '#d75b78'],
    [{ type: 'potion', acid: true }, 'acid', '#b4d53b'],
    [{ type: 'potion', mana: true }, 'mana', '#4d9fe6'],
    [{ type: 'potion', explosive: true }, 'explosive', '#e98b3c'],
  ];
  for (const [cell, kind, colour] of variants) {
    const index = variants.findIndex(item => item[1] === kind), svg = render(cell, index);
    assert.match(svg, new RegExp(`data-potion="${kind}"`));
    assert.match(svg, new RegExp(`raid-potion-fill-${index}`));
    assert.ok(svg.includes(`fill="${colour}"`), kind + ' liquid color');
    assert.ok(svg.includes('class="potion-wave potion-wave-front"'));
  }
  assert.notEqual(render(variants[0][0], 0), render(variants[3][0], 3));
  for (const [cell, kind] of variants) {
    const svg = render(cell, variants.findIndex(item => item[1] === kind));
    assert.ok(svg.includes(`href="miniapp_preview/assets/raid_potion_${kind}.png"`), kind + ' unique sprite');
    const png = readFileSync(new URL(`./assets/raid_potion_${kind}.png`, import.meta.url));
    assert.equal(png.subarray(0, 8).toString('hex'), '89504e470d0a1a0a', kind + ' PNG signature');
    assert.equal(png[25], 6, kind + ' real RGBA alpha channel');
    assert.ok(png.readUInt32BE(16) > 400 && png.readUInt32BE(20) > 700, kind + ' detailed sprite dimensions');
  }
  assert.match(render(variants[1][0], 1), /data-potion="acid"[^]*potion-slosh-overlay/);
  assert.match(css, /potion-lower-motion[^]*transform-origin: 24px 21px/);
  assert.match(css, /@keyframes potion-rock[^]*rotate\(-6\.5deg\)/);
});

test('magitech source: legacy Continue keeps 42 skill fixtures and all six RNG streams classical', () => {
  const source = readFileSync(new URL('./raid.js', import.meta.url), 'utf8');
  const skillSection = source.slice(source.indexOf('  const SKILLS = ['), source.indexOf('  // 7 уровней'));
  const uniqueSection = source.slice(source.indexOf('  const UNIQUE_SKILLS = ['), source.indexOf('  const ALL_SKILLS'));
  const ids = [...(skillSection + uniqueSection).matchAll(/\{ id: '(\w+)'/g)].map(match => match[1]);
  assert.equal(ids.length, 42);
  for (const [index, id] of ids.entries()) {
    const options = { classId: raidClassIds[index % 10], difficulty: index % 3, menus: true, seed: 8801 };
    const a = raidBrowser(options), b = legacyMagitechBrowser(options);
    for (const browser of [a, b]) {
      const r = browser.raid, s = r.state;
      s.turn = 80; s.hero.hp = 900; s.hero.maxHp = 1000; s.hero.armor = 20; s.hero.maxArmor = 30;
      s.hero.xp = 99; s.hero.gold = 99; s.upgradeBar = 99; s.trophyRemaining = 30;
      s.skills = [id]; s.skillLevels = { [id]: 1 }; s.skillCooldowns = { [id]: 0 };
      s.board = Array.from({ length: 36 }, (_, slot) => ({ type: ['sword', 'shield', 'coin', 'potion'][slot % 4] }));
      raidOld(r, 4, { atk: 2, hp: 3, maxHp: 20, def: 2 });
      raidOld(r, 15, { atk: 3, hp: 30, maxHp: 30, def: 3 }, 2 + index % 31);
      raidOld(r, 30, { atk: 5, hp: 35, maxHp: 35, def: 4 }, 1);
      r.activateSkill(id); settleMagitechFixture(browser);
    }
    assert.deepEqual(magitechSnapshot(b.raid), magitechSnapshot(a.raid), id);
  }
});

test('magitech source: legacy Continue retains classical gameplay across 300 paired turns and 60 restores', () => {
  let turns = 0, restores = 0;
  for (const classId of raidClassIds) for (const difficulty of [0, 1, 2]) {
    const options = { classId, difficulty, menus: true, seed: 2718 };
    let a = raidBrowser(options), b = legacyMagitechBrowser(options);
    assert.deepEqual(magitechSnapshot(b.raid), magitechSnapshot(a.raid), `${classId}/${difficulty}/start`);
    for (let step = 0; step < 10; step++) {
      for (const browser of [a, b]) {
        const r = browser.raid, s = r.state;
        s.turn = 100 + step; s.hero.hp = 5000; s.hero.maxHp = 5000;
        s.hero.armor = 30; s.hero.maxArmor = 30; s.regen = 1; s.spikes = 2; s.weaponPoison = 1;
        s.hero.xp = 99; s.hero.gold = 99; s.upgradeBar = 99; s.trophyRemaining = 30;
        raidBoard(r, step % 2 ? 'coin' : 'shield');
        raidOld(r, 0, { atk: 1, hp: 3, maxHp: 3, def: 1 });
        s.board[1] = { type: 'sword' }; s.board[2] = { type: 'sword' };
        raidOld(r, 8, { atk: 2, hp: 30, maxHp: 30, def: 2 }, 2 + (step + difficulty) % 31);
        raidOld(r, 30, { atk: 3, hp: 35, maxHp: 35, def: 3 }, step === 9 ? 1 : 0);
        raidSelect(r, [0,1,2]); r.resolveChain(); settleMagitechFixture(browser);
      }
      turns++;
      assert.equal(b.raid.state.over, false);
      assert.deepEqual(magitechSnapshot(b.raid), magitechSnapshot(a.raid), `${classId}/${difficulty}/${step}`);
      if (step === 4 || step === 9) {
        assert.equal(a.raid.saveRun(), true); assert.equal(b.raid.saveRun(), true);
        a = raidReload(a); b = raidReload(b, { variant: 'magitech' }); restores++;
        assert.deepEqual(magitechSnapshot(b.raid), magitechSnapshot(a.raid), `${classId}/${difficulty}/${step}/resume`);
      }
    }
  }
  assert.equal(turns, 300); assert.equal(restores, 60);
});

const MAGITECH_RUN_KEY = 'raid:magitech:activeRun';
const magitechSave = browser => JSON.parse(browser.storage.get(MAGITECH_RUN_KEY));

test('magitech orbs: legacy v1 sword palettes remain cosmetic, never combat fields or gameplay RNG draws', () => {
  const b = legacyMagitechBrowser({ seed: 2718 });
  raidBoard(b.raid, 'sword');
  const before = raidJSON(b.raid.state);
  assert.equal(b.raid.saveRun(), true);
  const saved = magitechSave(b);
  assert.equal(saved.version, 1); assert.equal(saved.orbElements.length, 36);
  assert.deepEqual([...new Set(saved.orbElements)].sort(), [0, 1, 2, 3]);
  assert.deepEqual(raidJSON(b.raid.state), before, 'render/save metadata cannot mutate gameplay state');
  assert.deepEqual(saved.state.board, before.board);
  assert.deepEqual(saved.rng, before.rngSeeds);
  for (const cell of b.raid.state.board) assert.deepEqual(Object.keys(cell), ['type']);
  assert.equal(b.raid.swordDamage(3), 9);
});

test('magitech orbs: indistinguishable swords retain their own palette through two-hole gravity and explicit Continue', () => {
  let b = legacyMagitechBrowser({ seed: 2718 });
  const r = b.raid; raidBoard(r, 'sword'); r.state.forceCoins = 1;
  r.saveRun(); const original = [...r.state.board], elements = magitechSave(b).orbElements;
  r.state.board[24] = { type: 'empty' }; r.state.board[30] = { type: 'empty' }; r.state.board[31] = { type: 'empty' };
  r.placeNewTiles({}); const beforeSave = raidJSON(r.state); r.saveRun();
  assert.equal(r.state.board[12], original[0]); assert.equal(r.state.board[7], original[1]);
  const checkpoint = magitechSave(b);
  assert.equal(checkpoint.orbElements[12], elements[0]); assert.equal(checkpoint.orbElements[7], elements[1]);
  assert.deepEqual(raidJSON(r.state), beforeSave);
  b = raidReload(b, { variant: 'magitech' });
  assert.deepEqual(magitechSave(b).orbElements, checkpoint.orbElements);
  assert.deepEqual(raidJSON(b.raid.state.board), checkpoint.state.board);
  assert.deepEqual(raidJSON(b.raid.state.rngSeeds), checkpoint.rng);
});

test('magitech orbs: released v1 without palette metadata resumes its actual run and six streams without rerolling', () => {
  const b = legacyMagitechBrowser({ seed: 991 });
  const previous = magitechSave(b); delete previous.orbElements;
  b.storage.set(MAGITECH_RUN_KEY, JSON.stringify(previous));
  const next = raidBrowser({ variant: 'magitech', start: false, saved: b.storage });
  assert.equal(next.raid.state, null); assert.equal(next.raid.resumeSavedRun(), true);
  const now = magitechSave(next);
  assert.equal(now.version, 1); assert.equal(now.orbElements.length, 36);
  assert.deepEqual(now.state.board, previous.state.board); assert.deepEqual(now.rng, previous.rng);
  assert.deepEqual(now.menu, previous.menu); assert.deepEqual(now.arrivals, previous.arrivals); assert.deepEqual(now.next, previous.next);
});

test('magitech orbs: malformed optional palette data is rejected without overwriting the checkpoint', () => {
  const b = raidBrowser({ variant: 'magitech' }), valid = magitechSave(b);
  for (const value of [null, {}, [], Array(35).fill(0), Array(37).fill(0), Array(36).fill(-1), Array(36).fill(4), Array(36).fill(.5), Array(36).fill('fire')]) {
    const raw = JSON.stringify({ ...valid, orbElements: value });
    const next = raidBrowser({ variant: 'magitech', start: false, saved: { [MAGITECH_RUN_KEY]: raw } });
    assert.equal(next.raid.resumeSavedRun(), false); assert.equal(next.raid.state, null);
    assert.equal(next.raid.saveStatus.state, 'invalid'); assert.equal(next.storage.get(MAGITECH_RUN_KEY), raw);
  }
});

test('magitech orbs: a checkpoint in the original anticipation window still matches once at 420ms and attacks once at 550ms', () => {
  let b = raidBrowser({ variant: 'magitech', menus: true, seed: 42 });
  raidBoard(b.raid, 'coin'); b.raid.state.forceCoins = 1;
  raidSelect(b.raid, [24, 30, 31]); b.raid.resolveChain(); b.advance(200);
  const saved = magitechSave(b); assert.equal(saved.next.phase, 'match-chain');
  b = raidReload(b, { variant: 'magitech' }); b.advance(419);
  assert.equal(b.raid.state.hero.gold, 0); assert.equal(b.raid.state.turn, 0);
  b.advance(1); assert.equal(b.raid.state.hero.gold, 6); assert.equal(b.raid.state.turn, 0);
  b.advance(549); assert.equal(b.raid.state.turn, 0);
  b.advance(1); assert.equal(b.raid.state.turn, 1); assert.equal(b.raid.state.hero.gold, 6);
  b.advance(3000); assert.equal(b.raid.state.turn, 1); assert.equal(b.raid.state.chainCount, 1);
});

test('magitech quiet motion: six selection-only material loops have no rotation, fast flicker or sphere pulsing', () => {
  const css = readFileSync(new URL('./raid_magitech.css', import.meta.url), 'utf8');
  const material = css.slice(css.indexOf('/* Quiet selection material:'), css.indexOf('.orb-core {', css.indexOf('/* Quiet selection material:')));
  assert.ok(material.length > 1000);
  assert.doesNotMatch(material, /conic-gradient|\b(?:rotate|scale)\(/);
  assert.doesNotMatch(css, /orb-fx-spin|orb-fx-flicker/);
  const loops = [...material.matchAll(/animation:\s*(orb-fx-[a-z]+)\s+([\d.]+)s[^;]*infinite/g)];
  assert.equal(loops.length, 6);
  assert.equal(new Set(loops.map(m => m[1])).size, 6);
  for (const [, name, seconds] of loops) assert.ok(Number(seconds) >= 14, `${name} must be an unobtrusive slow drift, not a spinner`);
  const frames = css.slice(css.indexOf('@keyframes orb-fx-heat'), css.indexOf('/* Content-only 3D rotation'));
  assert.doesNotMatch(frames, /\b(?:rotate(?:[XYZ]|3d)?|scale(?:[XYZ]|3d)?)\(|\b(?:opacity|filter)\s*:/, 'material loops cannot spin, resize or blink the spheres; only the requested item itself may turn');
  assert.match(css, /\.orb-material\s*\{[^}]*opacity:\s*0/);
  assert.match(css, /#board \.cell\.selected \.orb-material\s*\{\s*opacity:\s*\.18/);
  assert.match(css, /\.raid-orb\.frozen \.orb-material[^}]*animation-play-state:\s*paused/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)[^]*\.orb-material::after[^}]*animation:\s*none !important/);
  const explosive = css.match(/\.raid-orb\.explosive:not\(\.frozen\) \.orb-material::after\s*\{([^}]+)\}/)?.[1];
  assert.ok(explosive); assert.doesNotMatch(explosive, /animation:/, 'explosive item is marked statically, not with a .7s idle flash');
});

test('magitech weapon fog: the selection-only two-thirds body stays quiet, frozen/reduced-motion safe and separate from item-aware artwork', () => {
  const css = readFileSync(new URL('./raid_magitech.css', import.meta.url), 'utf8');
  const js = readFileSync(new URL('./raid_magitech.js', import.meta.url), 'utf8');
  const haze = css.match(/\.orb-material\.orb-fog\s*\{([^}]+)\}/)?.[1];
  assert.ok(haze); assert.match(haze, /height:\s*66\.6667%/); assert.match(haze, /inset:\s*auto 0 0/);
  assert.match(haze, /overflow:\s*hidden/); assert.match(haze, /animation:\s*none/);
  assert.match(haze, /mask-image:\s*linear-gradient/);
  const fogFrames = css.slice(css.indexOf('@keyframes orb-fog-drift'), css.indexOf('/* Content-only 3D rotation'));
  assert.ok(fogFrames.includes('@keyframes orb-fog-return'));
  assert.doesNotMatch(fogFrames, /\b(?:rotate(?:[XYZ]|3d)?|scale(?:[XYZ]|3d)?)\(|\b(?:opacity|filter)\s*:/);
  assert.match(css, /animation:\s*orb-fog-drift 24s ease-in-out infinite/);
  assert.match(css, /animation:\s*orb-fog-return 30s ease-in-out infinite/);
  assert.match(css, /\.raid-orb\.frozen \.orb-fog-wisp\s*\{\s*animation-play-state:\s*paused/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)[^]*\.raid-orb \*[^}]*animation:\s*none !important/);
  assert.doesNotMatch(js, /ORB_SIGILS|class="orb-sigil"/);
  const art = js.match(/const RAID_WEAPON_ART = \[([^]*?)^  \];/m)?.[1];
  const textures = [...art.matchAll(/id: '(techno-[a-z-]+-v2)', src: '(data:image\/webp;base64,[^']+)'/g)];
  assert.deepEqual(textures.map(m => m[1]), ['rusted-blade','sabre','mace','hammer','axe','guard-blade'].map(id => 'techno-' + id + '-v2'));
  assert.equal(new Set(textures.map(m => m[2])).size, 6);
  const resolver = js.match(/^  function raidWeaponVisual\([^]*?^  }/m)?.[0];
  assert.ok(resolver); assert.match(resolver, /item\.suffixIndex/); assert.match(resolver, /item\.appearance/);
  assert.doesNotMatch(resolver, /\b(?:random|randInt)\(|Math\.random|localStorage|profileStorage|\.mods\b/);
  const sync = js.match(/^  function syncRaidWeaponTexture\([^]*?^  }/m)?.[0];
  assert.ok(sync); assert.match(sync, /setAttribute\('src', art\.src\)/);
  assert.doesNotMatch(sync, /innerHTML|replaceChildren|createElement|\.remove\(/, 'purchasing a new weapon must update, not replace, a surviving image/orb');
  assert.match(js, /raidItemIcon\(item\.slot, item\)/); assert.match(js, /raidItemIcon\(opt\.slot, item\)/);
});


test('magitech shields: five transparent shield models match shop and equipped board art, and update in place on purchase', () => {
  const js = readFileSync(new URL('./raid_magitech.js', import.meta.url), 'utf8');
  const artBlock = js.match(/const RAID_SHIELD_ART = \[([^]*?)^  \];/m)?.[1];
  assert.ok(artBlock);
  const models = [...artBlock.matchAll(/id: '([a-z-]+)', src: '([^']+)'/g)];
  assert.deepEqual(models.map(match => match[1]), ['plank-splinter', 'bronze-buckler', 'sun-tower', 'emerald-fortress', 'rune-barrier']);
  assert.equal(models.length, 5);
  const context = { ITEM_NAMES: { shield: ['a', 'b', 'c', 'd', 'e'] }, RAID_SHIELD_ART: models.map(match => ({ id: match[1], src: match[2] })), S: { equipment: [{ slot: 'shield', suffixIndex: 0, appearance: 160 }] } };
  const visualSource = js.match(/^  function raidShieldVisual\([^]*?^  }/m)?.[0];
  const syncSource = js.match(/^  function syncRaidShieldTexture\([^]*?^  }/m)?.[0];
  assert.ok(visualSource); assert.ok(syncSource);
  const visual = vm.runInNewContext('(' + visualSource + ')', context);
  const sync = vm.runInNewContext('(' + syncSource + ')', { raidShieldVisual: visual });
  const bySuffix = models.map((_, suffixIndex) => visual({ slot: 'shield', suffixIndex, appearance: 160 + suffixIndex }));
  assert.equal(new Set(bySuffix.map(item => item.id)).size, 5);
  assert.notEqual(visual({ suffixIndex: 0, appearance: 160 }).key, visual({ suffixIndex: 0, appearance: 239 }).key);
  assert.match(js, /if \(slot === 'shield'\) return raidShieldTexture\('equipment-glyph', item\)/);
  assert.match(js, /if \(cell\.type === 'shield'\) return raidShieldTexture\('shield-texture'\)/);
  assert.match(js, /if \(cell\.type === 'shield'\) syncRaidShieldTexture\(orb\.querySelector\('\.shield-texture'\)\)/);
  const image = { dataset: {}, attributes: {}, getAttribute(name) { return this.attributes[name] || null; }, setAttribute(name, value) { this.attributes[name] = value; } };
  context.S.equipment[0] = { slot: 'shield', suffixIndex: 0, appearance: 160 };
  sync(image);
  const originalImage = image;
  assert.equal(image.getAttribute('src'), bySuffix[0].src);
  context.S.equipment[0] = { slot: 'shield', suffixIndex: 4, appearance: 228 };
  sync(image);
  assert.equal(image, originalImage, 'purchase updates the same board image node like sword equipment');
  assert.equal(image.getAttribute('src'), bySuffix[4].src);
  assert.equal(image.dataset.shieldKey, visual({ suffixIndex: 4, appearance: 228 }).key);
  for (let index = 0; index < 5; index++) {
    const png = readFileSync(new URL(`./assets/raid_shield_${index}.png`, import.meta.url));
    assert.equal(png.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
    assert.equal(png[25], 6, `shield ${index} has true RGBA alpha`);
    assert.equal(png.readUInt32BE(16), 256);
  }
});

test('magitech mech gallery: every mech has its own transparent portrait, compact selection and circular glass details', () => {
  const js = readFileSync(new URL('./raid_magitech.js', import.meta.url), 'utf8');
  const css = readFileSync(new URL('./raid_magitech.css', import.meta.url), 'utf8');
  const classBlock = js.match(/const CLASSES = \[([^]*?)\n  \];/)?.[1];
  assert.ok(classBlock);
  const ids = [...classBlock.matchAll(/id: '([a-z]+)'/g)].map(match => match[1]);
  assert.equal(ids.length, 10);
  for (const id of ids) {
    const png = readFileSync(new URL(`./assets/mech_${id}.png`, import.meta.url));
    assert.equal(png.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
    assert.equal(png[25], 6, `${id} portrait has alpha transparency`);
    assert.equal(png.readUInt32BE(16), 512);
    assert.ok(png.length < 500_000, `${id} portrait is a trimmed transparent asset without a full-size checkerboard matte`);
  }
  assert.match(js, /mech_\$\{cls\.id\}\.png/);
  assert.match(js, /className = 'picker-card-btn'/);
  assert.match(js, /className = `mech-detail-topic mech-garage-icon-button \$\{kind\}`/);
  assert.match(js, /textContent = 'Поехали!'/);
  assert.match(js, /event\.target === detail\) closeDetail\(\)/);
  assert.match(js, /role', 'dialog'/);
  assert.match(js, /line\('Модуль · ', cls\.uniqueSkillDesc\)/);
  assert.match(js, /line\('Пассивы · '/);
  assert.match(js, /line\('Изъяны · '/);
  assert.match(css, /grid-template-columns: repeat\(5,minmax\(0,1fr\)\)/);
  assert.match(css, /\.mech-detail-body[^]*border-radius: 50%/);
  assert.match(css, /data-raid-motion="off"[^]*animation: none/);
  assert.match(css, /prefers-reduced-motion: reduce/);
  const html = readFileSync(new URL('./raid_magitech.html', import.meta.url), 'utf8');
  assert.match(html, /id="picker-title"[^>]*><em>Гараж<\/em>/);
  assert.match(css, /\.mech-detail-portrait \{ overflow: hidden; \}/);
  assert.match(css, /\.mech-detail-portrait img \{ width: 100%; height: 100%;/);
  assert.match(css, /\.picker-main \.picker-tools \.btn \{ min-height: 32px/);
  assert.match(css, /mech-detail-actions\.mech-garage-side-actions \{ position: absolute;[^]*left: 5%;[^]*width: 23%/);
  assert.match(css, /\.mech-detail-body\.mech-garage-home > \.mech-go-button \{[\s\S]*?left:21%; bottom:16%; width:58%/);
  assert.match(css, /\.mech-detail-body\.mech-garage-home > \.mech-go-button[^]*border-radius: 999px/);
});

test('magitech preparation flow: mech confirmation opens four-quadrant elemental screen; emblem launches and back returns to garage', () => {
  const js = readFileSync(new URL('./raid_magitech.js', import.meta.url), 'utf8');
  const html = readFileSync(new URL('./raid_magitech.html', import.meta.url), 'utf8');
  const css = readFileSync(new URL('./raid_magitech.css', import.meta.url), 'utf8');
  const screen = html.match(/<section class="raid-region-screen"[^]*?<\/section>/)?.[0];
  assert.ok(screen);
  for (const element of ['fire', 'air', 'earth', 'water']) assert.match(screen, new RegExp(`data-raid-region="${element}"`));
  assert.match(screen, /id="difficulty-select"/);
  assert.match(screen, /До гибели/);
  assert.match(screen, /id="region-emblem-launch"/);
  assert.match(screen, /id="region-back-button"/);
  const logo = readFileSync(new URL('./assets/raid_emblem.png', import.meta.url));
  assert.equal(logo.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
  assert.equal(logo[25], 6); assert.equal(logo.readUInt32BE(16), 512);
  assert.match(js, /PREPARED_MECH_ID = cls\.id/);
  assert.match(js, /screen\.hidden = false; card\.classList\.add\('region-screen-open'\)/);
  assert.match(js, /requestNewGame\(PREPARED_MECH_ID\)/);
  assert.match(js, /region-back-button[^]*classList\.remove\('region-screen-open'\)/);
  assert.match(css, /grid-template-columns: 1fr 1fr; grid-template-rows: 1fr 1fr/);
  assert.match(css, /\.class-picker:not\(\.region-mode-open\) \.raid-world-footer \{ display:none; \}/);
  assert.doesNotMatch(html.match(/<div class="picker-main"[^]*?<\/div>\s*<section class="raid-region-screen"/)?.[0] || '', /raid-run-settings/);
  assert.match(css, /\.region-quadrant\.element-fire[^]*background-image: url\('__RAID_SCENE_FIRE__'\)/);
  for (const element of ['fire', 'air', 'earth', 'water']) assert.match(css, new RegExp(`data-selected-region="${element}"`));
});

test('magitech approved item motion: coin spins; sword uses restrained idle sway and a short slash only when collected', () => {
  const css = readFileSync(new URL('./raid_magitech.css', import.meta.url), 'utf8');
  const js = readFileSync(new URL('./raid_magitech.js', import.meta.url), 'utf8');
  const frames = name => {
    const start = css.indexOf('@keyframes ' + name + ' '); assert.ok(start >= 0, name);
    const next = css.indexOf('\n@keyframes ', start + 1);
    return css.slice(start, next < 0 ? css.indexOf('\n@media', start) : next);
  };
  assert.match(frames('coin-axial-turn'), /rotateY\(0deg\)[^]*rotateY\(360deg\)/);
  assert.match(frames('weapon-idle-sway'), /rotate\(-1\.3deg\)[^]*rotate\(1\.3deg\)/);
  assert.doesNotMatch(css, /weapon-axial-turn|rotate3d\(1,-1,0/);
  assert.match(frames('weapon-natural-glint'), /opacity:0[^]*opacity:\.38/);
  assert.match(frames('weapon-impact-slash'), /scaleX\(\.06\)[^]*scaleX\(1\.08\)/);
  for (const name of ['heat','mist','flow','grain','sheen','sap'].map(n => 'orb-fx-' + n).concat(['orb-fog-drift','orb-fog-return','orb-water-slosh','orb-water-return'])) {
    assert.doesNotMatch(frames(name), /\b(?:rotate(?:[XYZ]|3d)?|scale(?:[XYZ]|3d)?)\(|\b(?:opacity|filter)\s*:/, name + ': still no spinning, pulsing or blinking material');
  }
  assert.match(css, /animation:\s*coin-axial-turn 16s linear infinite/);
  assert.match(css, /animation:\s*weapon-idle-sway 7\.2s ease-in-out infinite/);
  assert.match(css, /animation:\s*weapon-natural-glint 6\.5s ease-in-out infinite/);
  assert.match(css, /\.weapon-glint[^{]*\{[^}]*object-fit:contain[^}]*clip-path:polygon/);
  assert.match(css, /\.raid-orb\.frozen\[data-type="sword"\] \.weapon-glint \{ animation-play-state: paused; \}/);
  assert.match(js, /return raidWeaponTexture\('ic'\) \+ raidWeaponTexture\('weapon-glint'\)/);
  assert.match(css, /animation:weapon-impact-slash \.23s cubic-bezier/);
  assert.doesNotMatch(css, /weapon-axial-turn|rotate3d\(1,-1,0/);
  assert.match(js, /if \(view\.node\.dataset\.type === 'sword'\)/);
  assert.match(js, /className = 'weapon-impact-slash'/);
  assert.match(js, /className = 'weapon-impact-echo'/);
  assert.match(js, /burst\.dataset\.type = view\.node\.dataset\.type/);
  assert.match(js, /view\.node\.dataset\.type === 'sword'/);
  assert.match(js, /weapon-impact-echo/);
  assert.match(css, /transform-style:\s*preserve-3d/); assert.match(css, /backface-visibility:\s*hidden/);
  assert.match(css, /\.coin-reverse\s*\{\s*transform:\s*rotateY\(180deg\) translateZ\(2px\)/);
  assert.match(css, /\.coin-edge-belt\s*\{[^}]*width:\s*4px[^}]*repeating-linear-gradient[^}]*rotateY\(90deg\)/);
  assert.match(css, /\.raid-orb\.frozen :is\(\.weapon-texture,\.coin-3d\)\s*\{\s*animation-play-state:\s*paused/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)[^]*\.raid-orb \*[^}]*animation:\s*none !important/);
  assert.match(js, /class="ic coin-3d" aria-hidden="true"/); assert.match(js, /coin-face coin-obverse/); assert.match(js, /coin-face coin-reverse/);
  assert.match(css, /\.raid-orb\[data-element="air"\] \.orb-fog\s*\{\s*inset:\s*0 0 auto/);
  assert.match(css, /\.raid-orb\[data-element="water"\] \.orb-fog-wisp\s*\{[^}]*animation-name:\s*orb-water-slosh; animation-duration:\s*8s/);
  assert.match(css, /\.raid-orb\[data-element="water"\] \.orb-fog-wisp\.orb-fog-back[^}]*animation-name:\s*orb-water-return; animation-duration:\s*11s/);
});

test('magitech material SFX source: no external audio, autoplay, loops, timer queues, harsh oscillators or gameplay writes', () => {
  const js = readFileSync(new URL('./raid_magitech.js', import.meta.url), 'utf8');
  const audio = js.slice(js.indexOf('  // --- audio'), js.indexOf('  function isChainUppercut'));
  assert.doesNotMatch(audio, /\b(?:fetch|setTimeout|setInterval|randInt|orbElementIndex)\s*\(|Math\.random|RNG_SEEDS|profileStorage|new Audio\b|'(?:square|sawtooth)'|\.loop\s*=/);
  assert.doesNotMatch(audio, /\bS(?:\.[\w]+|\[[^\]]+\])*\s*(?:=|\+\+|--)/);
  assert.match(audio, /if \(!event\.isTrusted\) return/);
  assert.match(audio, /ctx\.state !== 'running'/);
  assert.match(audio, /AUDIO_VOICES\.size \+ voices\.length > 18/);
  assert.match(audio, /now - \(AUDIO_LAST\.get\(kind\) \?\? -1\) < \.085/);
  assert.match(js, /clearFloatEffects\(\);\s*stopMaterialAudio\(\);/);
  const html = readFileSync(new URL('./raid_magitech.html', import.meta.url), 'utf8');
  assert.match(html, /id="sound-on" checked aria-label="Звуковые эффекты"/);
  assert.doesNotMatch(html, /id="sound-on"[^>]*hidden/);
});

// Exercise the REAL standalone audio section, without adding a production
// debug API or pretending synthetic jsdom events can unlock native WebAudio.
// Trusted browser activation and audible PCM are checked separately in Chromium.
function materialAudioFixture(options = {}) {
  const documentListeners = new Map(), windowListeners = new Map(), contexts = [];
  let constructions = 0;
  const input = { checked: true, volume: 1, currentTime: 0,
    classList: { toggle() {}, contains() { return true; } }, setAttribute() {},
    play() { return Promise.resolve(); }, pause() {},
    addEventListener(name, fn) { documentListeners.set('sound:' + name, [fn]); } };
  const parameter = () => ({ value: 0, calls: [],
    setValueAtTime(value, time) { this.value = value; this.calls.push(['set', value, time]); },
    exponentialRampToValueAtTime(value, time) { this.value = value; this.calls.push(['ramp', value, time]); },
    cancelScheduledValues(time) { this.calls.push(['cancel', time]); },
  });
  class MaterialContext {
    constructor() {
      constructions++;
      if (options.constructorFails) throw new Error('Audio unavailable');
      this.state = options.state || 'running'; this.currentTime = 0; this.sampleRate = 24000;
      this.nodes = []; this.buffers = []; this.destination = {}; this.resumes = 0; contexts.push(this);
    }
    node(kind, props = {}) {
      const node = { kind, connections: [], disconnections: 0,
        connect(to) { this.connections.push(to); }, disconnect() { this.disconnections++; }, ...props };
      this.nodes.push(node); return node;
    }
    createGain() { return this.node('gain', { gain: parameter() }); }
    createBiquadFilter() {
      if (options.filterFails) throw new Error('Partial WebAudio implementation');
      return this.node('filter', { frequency: parameter(), Q: parameter() });
    }
    source(kind, props) { return this.node(kind, { ...props, starts: [], stops: [],
      start(time) { this.starts.push(time); }, stop(time) { this.stops.push(time); }, onended: null }); }
    createOscillator() { return this.source('oscillator', { frequency: parameter(), type: '' }); }
    createBufferSource() { return this.source('noise', {}); }
    createBuffer(channels, length, rate) {
      const data = new Float32Array(length), buffer = { channels, length, rate, getChannelData() { return data; } };
      this.buffers.push(buffer); return buffer;
    }
    resume() {
      this.resumes++;
      if (options.resumeFails) return Promise.reject(new Error('Activation rejected'));
      this.state = 'running'; return Promise.resolve();
    }
  }
  const add = (map, name, fn) => map.set(name, [...(map.get(name) || []), fn]);
  const document = { visibilityState: 'visible', addEventListener(name, fn) { add(documentListeners, name, fn); } };
  const window = { addEventListener(name, fn) { add(windowListeners, name, fn); } };
  if (options.api !== 'none') window[options.api === 'webkit' ? 'webkitAudioContext' : 'AudioContext'] = MaterialContext;
  const state = { selected: [0, 1, 2], board: Array.from({ length: 3 }, () => ({ type: 'sword' })), rngSeeds: [1,2,3,4,5,6] };
  const palettes = new WeakMap(state.board.map(cell => [cell, 0]));
  const source = readFileSync(new URL('./raid_magitech.js', import.meta.url), 'utf8');
  const section = source.slice(source.indexOf('  // --- audio'), source.indexOf('  function isChainUppercut'));
  assert.ok(section.includes('function materialVoice'));
  const context = vm.createContext({ window, document, el: () => input, S: state, ORB_ELEMENTS: palettes,
    Math: Object.assign(Object.create(Math), { random() { throw new Error('SFX must not use gameplay/global RNG'); } }),
  });
  vm.runInContext(section + `\nglobalThis.sfx = { play: materialSound, pick: audioPick, kill: audioKill, heal: audioHeal,
    armor: audioArmor, gold: audioGold, level: audioLevel, over: audioGameOver, enemy: audioEnemy, boss: audioBoss,
    get active() { return AUDIO_VOICES.size; }, get kinds() { return Object.keys(MATERIAL_SOUNDS); } }; initMaterialAudio();`, context);
  return { api: context.sfx, input, state, palettes, document, contexts,
    get constructions() { return constructions; }, get ctx() { return contexts[0]; },
    get sources() { return contexts.flatMap(ctx => ctx.nodes.filter(n => n.kind === 'oscillator' || n.kind === 'noise')); },
    fire(name, event = {}, target = 'document') {
      for (const fn of (target === 'window' ? windowListeners : documentListeners).get(name) || []) fn({ target: input, ...event });
    },
  };
}

test('magitech material SFX: only a trusted activation opens audio; a suspended event is dropped, never queued', async () => {
  const b = materialAudioFixture({ state: 'suspended' }), before = JSON.stringify(b.state);
  b.api.pick('coin'); b.api.boss(); assert.equal(b.constructions, 0);
  b.fire('pointerdown', { isTrusted: false }); b.api.gold(); assert.equal(b.constructions, 0);
  b.fire('keydown', { isTrusted: true }); assert.equal(b.constructions, 1); assert.equal(b.ctx.resumes, 1);
  b.api.gold(); assert.equal(b.sources.length, 3);
  b.ctx.state = 'suspended'; const count = b.sources.length;
  b.api.heal(); b.api.enemy(); assert.equal(b.sources.length, count); assert.equal(b.ctx.resumes, 1);
  b.fire('pointerdown', { isTrusted: true }); assert.equal(b.ctx.resumes, 2);
  await Promise.resolve(); assert.equal(b.sources.length, count, 'resume must not replay the dropped events');
  assert.equal(JSON.stringify(b.state), before);
});

test('magitech material SFX: existing pick categories route to distinct gold, water, air and metal without assigning palettes', () => {
  for (const [kind, element, firstFrequency, noise] of [
    ['coin',0,1180,0], ['potion',0,560,1], ['shield',0,470,0],
    ['sword',1,690,1], ['sword',3,560,1], ['sword',2,820,1], [undefined,0,920,0],
  ]) {
    const b = materialAudioFixture(), last = b.state.board[2]; b.palettes.set(last, element);
    const before = JSON.stringify(b.state);
    b.fire('pointerdown', { isTrusted: true }); b.api.pick(kind);
    assert.equal(b.sources.find(n => n.kind === 'oscillator').frequency.calls[0][1], firstFrequency);
    assert.equal(b.sources.filter(n => n.kind === 'noise').length, noise);
    assert.equal(b.palettes.get(last), element); assert.equal(JSON.stringify(b.state), before);
    b.palettes.delete(last); b.ctx.currentTime += 1; b.api.pick('sword');
    assert.equal(b.palettes.has(last), false, 'sound reads the rendered palette; it cannot create cosmetic save metadata');
  }
});

test('magitech material SFX: all eleven quiet presets use filtered sine/noise, soft attack, bounded decay and deterministic local noise', () => {
  const b = materialAudioFixture(); b.fire('pointerdown', { isTrusted: true });
  assert.equal(b.api.kinds.length, 11);
  for (const kind of b.api.kinds) {
    const start = b.ctx.nodes.length; b.api.play(kind);
    const nodes = b.ctx.nodes.slice(start), voices = nodes.filter(n => n.kind === 'oscillator' || n.kind === 'noise');
    assert.ok(voices.length >= 1 && voices.length <= 3, kind);
    for (const voice of voices) {
      if (voice.kind === 'oscillator') assert.equal(voice.type, 'sine');
      assert.equal(voice.starts.length, 1); assert.equal(voice.stops.length, 1);
      assert.ok(voice.stops[0] > voice.starts[0] && voice.stops[0] - voice.starts[0] <= .51);
      const filter = voice.connections[0], gain = filter.connections[0];
      assert.ok(['lowpass','bandpass'].includes(filter.type)); assert.ok(filter.Q.value <= .65);
      const calls = gain.gain.calls;
      assert.equal(calls[0][1], .0001); assert.equal(calls[1][1], .0001);
      assert.equal(calls[2][0], 'ramp'); assert.ok(calls[2][1] <= .055);
      assert.ok(Math.abs(calls[2][2] - voice.starts[0] - .008) < 1e-8, '8ms attack avoids clicks');
      assert.equal(calls[3][1], .0001);
      voice.onended(); assert.equal(voice.disconnections, 1); assert.equal(filter.disconnections, 1); assert.equal(gain.disconnections, 1);
    }
    assert.equal(b.api.active, 0); b.ctx.currentTime += 1;
  }
  assert.equal(b.ctx.buffers.length, 1, 'reuse one short local noise buffer, not samples/URLs');
  const buffer = b.ctx.buffers[0], data = buffer.getChannelData(0);
  assert.equal(buffer.length, 14400); assert.ok(data.some(n => n > 0) && data.some(n => n < 0));
  assert.ok(data.every(n => Number.isFinite(n) && Math.abs(n) <= 1));
  const c = materialAudioFixture(); c.fire('keydown', { isTrusted: true }); c.api.heal();
  assert.deepEqual([...c.ctx.buffers[0].getChannelData(0)], [...data], 'noise seed is independent of gameplay/global RNG');
});

test('magitech material SFX: dense dragging is rate-limited and capped at eighteen voices, with natural-end cleanup', () => {
  const b = materialAudioFixture(); b.fire('pointerdown', { isTrusted: true });
  for (let i = 0; i < 100; i++) b.api.gold();
  assert.equal(b.sources.length, 3, 'same-kind events less than 85ms apart cannot stack');
  for (const kind of b.api.kinds) b.api.play(kind);
  assert.equal(b.api.active, 18); assert.equal(b.sources.length, 18);
  for (let i = 0; i < 50; i++) { b.ctx.currentTime += .086; b.api.play(b.api.kinds[i % 11]); }
  assert.equal(b.sources.length, 18, 'a full voice budget drops, never schedules a backlog');
  const peakBound = b.ctx.nodes.filter(n => n.kind === 'gain').slice(1).reduce((sum, n) => sum + n.gain.calls[2][1], 0) * .45;
  assert.ok(peakBound < .5, 'even an artificial simultaneous maximum has headroom below digital clipping');
  for (const voice of b.sources) voice.onended();
  assert.equal(b.api.active, 0); b.ctx.currentTime += 1; b.api.gold(); assert.equal(b.api.active, 3);
});

test('magitech material SFX: mute/hidden/pagehide stop and disconnect even delayed voices; unmute/foreground never replay', () => {
  for (const boundary of ['mute','hidden','pagehide']) {
    const b = materialAudioFixture(); b.fire('pointerdown', { isTrusted: true }); b.api.level();
    const old = [...b.sources], callbacks = old.map(n => n.onended);
    assert.ok(old.some(n => n.starts[0] > b.ctx.currentTime), 'fixture includes a delayed voice');
    if (boundary === 'mute') { b.input.checked = false; b.fire('sound:change'); }
    if (boundary === 'hidden') { b.document.visibilityState = 'hidden'; b.fire('visibilitychange'); }
    if (boundary === 'pagehide') b.fire('pagehide', {}, 'window');
    assert.equal(b.api.active, 0); assert.equal(b.ctx.nodes[0].gain.value, 0);
    for (const voice of old) { assert.equal(voice.stops.length, 2); assert.equal(voice.onended, null); assert.equal(voice.disconnections, 1); }
    b.api.gold(); assert.equal(b.sources.length, old.length);
    b.input.checked = true; b.document.visibilityState = 'visible'; b.ctx.currentTime += 1;
    b.fire('sound:change', { isTrusted: true }); b.fire('visibilitychange'); b.fire('pageshow', {}, 'window');
    assert.equal(b.sources.length, old.length, 'no replay on unmute/return');
    b.api.gold(); assert.equal(b.api.active, 3);
    for (const finish of callbacks) finish();
    assert.equal(b.api.active, 3, 'late old cleanup cannot delete a new sound');
  }
});

test('magitech material SFX: absent/denied/partial APIs fail silent; WebKit fallback and rejected resume remain safe', async () => {
  for (const options of [{ api: 'none' }, { constructorFails: true }, { filterFails: true }, { state: 'suspended', resumeFails: true }, { state: 'closed' }]) {
    const b = materialAudioFixture(options), before = JSON.stringify(b.state);
    assert.doesNotThrow(() => { b.fire('pointerdown', { isTrusted: true }); for (const kind of b.api.kinds) b.api.play(kind); });
    await Promise.resolve(); assert.equal(b.api.active, 0); assert.equal(JSON.stringify(b.state), before);
    assert.doesNotThrow(() => { b.input.checked = false; b.fire('sound:change'); });
  }
  const webkit = materialAudioFixture({ api: 'webkit' }); webkit.fire('keydown', { isTrusted: true }); webkit.api.gold();
  assert.equal(webkit.sources.length, 3);
});

// The run location is presentation metadata, independent from future previews.
test('magitech location: all four v1 snapshots restore the same rolled reward, board and six RNG streams', () => {
  for (const region of ['fire', 'air', 'earth', 'water']) {
    const b = legacyMagitechBrowser({ menus: true, seed: 1337 });
    b.raid.state.hero.gold = 100; b.raid.checkGoldBox(); b.raid.saveRun();
    const data = magitechSave(b); data.region = region;
    const raw = JSON.stringify(data); b.storage.set(MAGITECH_RUN_KEY, raw);
    const next = raidBrowser({ variant: 'magitech', menus: true, start: false, saved: b.storage });
    assert.equal(next.raid.state, null); assert.ok(next.raid.savedRunInfo());
    assert.equal(next.storage.get(MAGITECH_RUN_KEY), raw, 'reading is not resuming or rewriting the checkpoint');
    assert.equal(next.raid.resumeSavedRun(), true);
    const restored = magitechSave(next);
    assert.equal(restored.version, 1); assert.equal(restored.region, region);
    for (const key of ['state', 'rng', 'menu', 'results', 'arrivals', 'next', 'orbElements']) assert.deepEqual(restored[key], data[key], region + ':' + key);
    assert.equal(Object.hasOwn(next.raid.state, 'region'), false, 'location never enters the combat model');
  }
});

test('magitech location: released v1 without location remains read-only until explicit Continue and keeps its queues', () => {
  const b = legacyMagitechBrowser({ menus: true, seed: 1337 });
  b.raid.state.hero.xp = 100; b.raid.checkLevelUp();
  b.elements.get('modal-body').querySelectorAll('.lv-card')[1].click(); b.raid.saveRun();
  const data = magitechSave(b); delete data.region;
  const raw = JSON.stringify(data); b.storage.set(MAGITECH_RUN_KEY, raw);
  const next = raidBrowser({ variant: 'magitech', menus: true, start: false, saved: b.storage });
  next.raid.savedRunInfo(); next.raid.savedRunInfo();
  assert.equal(next.storage.get(MAGITECH_RUN_KEY), raw); assert.equal(next.raid.state, null);
  assert.equal(next.raid.resumeSavedRun(), true);
  const restored = magitechSave(next);
  assert.equal(restored.region, 'earth'); assert.equal(restored.version, 1);
  for (const key of ['state', 'rng', 'menu', 'results', 'arrivals', 'next', 'orbElements']) assert.deepEqual(restored[key], data[key], key);
});

test('magitech location: malformed optional region cannot replace a live run or overwrite the rejected checkpoint', () => {
  for (const region of [null, 0, false, {}, [], ['fire'], 'lava', 'FIRE', '__proto__', 'constructor']) {
    const b = raidBrowser({ variant: 'magitech', seed: 17 });
    const state = b.raid.state, before = raidJSON(state), raw = JSON.stringify({ ...magitechSave(b), region });
    b.storage.set(MAGITECH_RUN_KEY, raw);
    assert.equal(b.raid.resumeSavedRun(), false); assert.equal(b.raid.state, state);
    assert.deepEqual(raidJSON(state), before); assert.equal(b.raid.saveStatus.state, 'invalid');
    assert.equal(b.storage.get(MAGITECH_RUN_KEY), raw);
  }
});


test('magitech clear tiles: idle shells have no fill; four metal tones and opposing pixel bevels replace the permanent haze', () => {
  const css = readFileSync(new URL('./raid_magitech.css', import.meta.url), 'utf8');
  const shell = css.match(/^\.raid-orb \{([^]*?)^\}/m)?.[1];
  assert.ok(shell); assert.match(shell, /background:\s*none/);
  assert.doesNotMatch(shell, /radial-gradient|var\(--orb-(?:light|mid|dark)\)/);
  const socket = css.match(/^#board \.cell::before \{([^}]+)\}/m)?.[1];
  assert.ok(socket); assert.match(socket, /background:\s*none/);
  assert.match(css, /#board \.cell\.selected \.raid-orb \{[^}]*background:\s*rgb\(var\(--orb-rgb\) \/ \.12\)/);
  assert.match(css, /#board \.cell\.selected \.orb-material\.orb-fog \{\s*opacity:\s*\.26/);
  assert.match(css, /\.raid-orb \.orb-material, \.raid-orb \.orb-material::before,\s*\.raid-orb \.orb-material::after, \.raid-orb \.orb-fog-wisp \{\s*animation-play-state:\s*paused/);
  assert.match(css, /#board \.cell\.selected \.raid-orb:not\(\.frozen\) \.orb-fog-wisp \{\s*animation-play-state:\s*running/);
  const metal = css.match(/^\.weapon-texture, \.raid-orb \.orb-core > \.weapon-texture \{([^]*?)^\}/m)?.[1];
  assert.ok(metal); assert.match(metal, /var\(--weapon-tone/); assert.match(metal, /var\(--weapon-finish/);
  assert.match(metal, /drop-shadow\(-\.65px -\.65px 0 var\(--weapon-edge/);
  assert.match(metal, /drop-shadow\(1px 1px 0 #020b11\)/);
  const tones = ['fire','air','earth','water'].map(element => {
    const block = css.match(new RegExp('^\\.raid-orb\\[data-element="' + element + '"\\] \\{([^]*?)^\\}', 'm'))?.[1];
    assert.ok(block, element); assert.doesNotMatch(block, /animation|background:/);
    assert.match(block, /--weapon-edge:\s*#[0-9a-f]{6}/); assert.match(block, /--weapon-colour:\s*#[0-9a-f]{6}/);
    return block.match(/--weapon-tone:\s*([^;]+);/)[1];
  });
  assert.equal(new Set(tones).size, 4);
  const html = readFileSync(new URL('./raid_magitech.html', import.meta.url), 'utf8');
  assert.match(html, /Постоянная заливка убрана/); assert.match(html, /Стихийный бой — в новых забегах/);
  assert.match(html, /Старые забеги продолжаются по прежним правилам/);
});

// Approved first-stage policy: secondary effects keep ONE neutral D0 even when
// primary targets receive different amounts. Supply those amounts through the
// existing snapshot hook; this does NOT activate elements, assignments or RNG.
function magitechSecondaryFixture({ types = [0, 0], defence = 4 } = {}) {
  const b = raidCombat({ variant: 'magitech', seed: 2718 });
  const r = b.raid, s = r.state;
  s.forceCoins = 1; s.pierce = 0;
  for (const slot of [0, 1, 2]) s.board[slot] = { type: 'sword', element: 'earth' };
  const targets = types.map((type, i) => raidOld(r, i + 3, { hp: 100, maxHp: 100, def: defence }, type));
  // Accepted B3/W2/three-sword example: strong10.5 vs weak7.5, neutral D0=9.
  const snapshot = { chain: [0, 1, 2, 3, 4], effective: [0, 1, 2, 3, 4],
    hasSkull: true, damage: 9, spiky: types.includes(2), uppercuts: [],
    targetDamage: new Map([[3, 10.5], [4, 7.5]]) };
  return { ...b, targets, snapshot, hit: () => r.applyChainEffects(3, 0, 0, 0, [3, 4], snapshot) };
}

test('magitech neutral secondary: leech, shown damage and swordsHits use D0 once, not summed per-target HP or raw damage', () => {
  const { raid: r, targets, hit } = magitechSecondaryFixture();
  r.state.hero.hp = 10; r.state.leech = .2;
  const result = hit();
  assert.deepEqual(targets.map(s => s.hp), [93, 96], 'one final round then one armour subtraction per primary target');
  assert.equal(r.state.hero.hp, 11, 'floor(float32(9 × float32(.2))) =1, not 2 from lost HP or 3 from summed raw hits');
  assert.equal(r.state.scrollLeechLabel.number, 1, 'I1 label retains the same neutral leech amount');
  assert.equal(result.dmgShown, 9); assert.equal(r.state.swordsHits, 9);
  assert.equal(result.kills, 0); assert.equal(r.state.turn, 10);
});

test('magitech neutral secondary: two selected Spiky targets reflect neutral D0 only once, bypassing hero armour', () => {
  const { raid: r, targets, hit } = magitechSecondaryFixture({ types: [2, 2] });
  const armour = r.state.hero.armor;
  hit();
  assert.deepEqual(targets.map(s => s.hp), [93, 96]);
  assert.equal(r.state.hero.hp, 41, 'one reflection of9, not 11,8,18, or one reflection per Spiky');
  assert.equal(r.state.hero.armor, armour);
  assert.equal(r.state.swordsHits, 9); assert.equal(r.state.turn, 10);
});

test('magitech neutral secondary: an off-chain Meat Shield intercepts D0 and does not convert leech to actual HP loss', () => {
  const { raid: r, targets, snapshot, hit } = magitechSecondaryFixture();
  const shield = raidOld(r, 35, { hp: 100, maxHp: 100, def: 4 }, 30);
  r.state.hero.hp = 10; r.state.leech = .2;
  snapshot.targetDamage = new Map([[3, 0], [4, 0]]); // Existing shield blocks these direct targets.
  const result = hit();
  assert.deepEqual(targets.map(s => s.hp), [100, 100]);
  assert.equal(shield.hp, 95, 'neutral9 minus shield armour4, never the sum of blocked primary hits');
  assert.equal(r.state.hero.hp, 11); assert.equal(result.dmgShown, 9);
  assert.equal(r.state.swordsHits, 9); assert.equal(result.kills, 0);
});

test('magitech neutral secondary: Uppercut adds neutral W without Boost and retains priority over an outside Meat Shield', () => {
  const b = raidCombat({ variant: 'magitech', classId: 'warrior', seed: 2718 });
  const r = b.raid, s = r.state;
  s.forceCoins = 1; s.pierce = 0; s.swordBoost = 1; s.leech = .2; s.hero.hp = 10;
  for (const slot of [12, 0, 1]) s.board[slot] = { type: 'sword', element: 'earth' };
  const target = raidOld(r, 6, { hp: 100, maxHp: 100 });
  const shield = raidOld(r, 35, { hp: 100, maxHp: 100 }, 30);
  raidSelect(r, [12, 6, 0, 1]);
  const preview = r.chainPreview();
  assert.equal(preview.damage, 15); assert.equal(preview.skulls[0].damage, 17, 'D0 + W, not D0 + doubled W');
  r.resolveChain(); b.advance(420);
  assert.equal(target.hp, 83); assert.equal(shield.hp, 85);
  assert.equal(s.hero.hp, 13, 'leech remains one neutral15 ×20%, without the target-only Uppercut addition');
  assert.equal(s.swordsHits, 15); assert.equal(s.turn, 10);
});

test('magitech neutral secondary: weapon poison amount and armour-ignoring tick are independent of primary hit strength', () => {
  const { raid: r, targets, hit } = magitechSecondaryFixture({ defence: 100 });
  r.state.weaponPoison = 2;
  hit();
  assert.deepEqual(targets.map(s => s.hp), [100, 100]);
  for (const skull of targets) assert.deepEqual(Array.from(skull.poison), [2, 2, 2]);
  const immune = raidOld(r, 35, { hp: 100, maxHp: 100, def: 100 }, 9);
  r.hitSkullAt(35, 10.5, { direct: true });
  assert.deepEqual(Array.from(immune.poison), [0, 0, 0], 'existing Poisonous immunity remains');
  r.endEnemyTurn();
  assert.deepEqual(targets.map(s => s.hp), [98, 98], 'poison ticks for stored2, not a scaled or armour-reduced amount');
  for (const skull of targets) assert.deepEqual(Array.from(skull.poison), [2, 2, 0]);
  assert.equal(immune.hp, 100);
});

test('magitech neutral secondary: collected burning damage keeps its stored amount and ignores safe fire', () => {
  const { raid: r, targets, hit } = magitechSecondaryFixture();
  Object.assign(r.state.board[0], { burn: true, fireDamage: 4 });
  Object.assign(r.state.board[1], { burn: true, fireDamage: 2 });
  Object.assign(r.state.board[2], { burn: true, fireDamage: 99, fireSafe: true });
  const armour = r.state.hero.armor;
  hit();
  assert.deepEqual(targets.map(s => s.hp), [93, 96]);
  assert.equal(r.state.hero.hp, 44, 'stored4+2 once, no target multiplier and no safe99');
  assert.equal(r.state.hero.armor, armour); assert.equal(r.state.turn, 10);
});

// First approved combat increment. These use real assignments + preview + hit /
// resolve paths, not injected snapshot.targetDamage. v1 coverage above remains.
const raidElements = ['fire', 'air', 'earth', 'water'];
// k*8: explicit independent table makes boss averages exact eighths.
const elementalEighths = [[8,10,8,6], [6,8,10,8], [8,6,8,10], [10,8,6,8]];
function elementalCombat(options = {}) {
  const b = raidCombat({ ...options, variant: 'magitech', menus: true });
  // Pin the already-released elements-v1/v2 contract. A1/v3 has its own DOM
  // coverage below/in integration.test; never call a new pool 'unchanged v2'.
  assert.equal(b.raid.saveRun(), true);
  const released = magitechSave(b); released.version = 2; released.combat.rules = 'elements-v1'; delete released.combat.affinity;
  b.storage.set(MAGITECH_RUN_KEY, JSON.stringify(released));
  assert.equal(b.raid.resumeSavedRun(), true);
  b.raid.state.forceCoins = 1; b.raid.state.pierce = 0;
  return b;
}
function elementalWeapon(r, slot, element, extra = {}) { return r.state.board[slot] = { type: 'sword', element, ...extra }; }
function elementalPair({ classId = 'adventurer', types = [0,0], defence = 4, leech = 0 } = {}) {
  const b = elementalCombat({ classId, seed: 2718 }), r = b.raid;
  for (let slot = 0; slot < 3; slot++) elementalWeapon(r, slot, 'water');
  const targets = ['fire','earth'].map((element, i) => raidOld(r, i + 3, { hp: 100, maxHp: 100, def: defence, element }, types[i]));
  r.state.leech = leech;
  raidSelect(r, [0,1,2,3,4]);
  return { ...b, targets };
}

test('magitech elemental saves: new runs use explicit v4/affinity+A1 rules and independent RNG, without altering a classic start or profile', () => {
  for (const seed of [0,1,71,2718,0xffffffff]) {
    const old = raidBrowser({ seed }), b = raidBrowser({ variant: 'magitech', seed });
    const saved = magitechSave(b);
    assert.equal(saved.version, 4); assert.equal(saved.combat.rules, 'elements-affinity-v1');
    assert.ok(Number.isInteger(saved.combat.rng) && saved.combat.rng >= 0 && saved.combat.rng <= 0xffffffff);
    assert.equal(b.raid.savedRunInfo().combatRules, 'elements-affinity-v1');
    const neutral = magitechSnapshot(b.raid);
    for (const cell of neutral.board) delete cell.element;
    assert.deepEqual(neutral, magitechSnapshot(old.raid), `same starting resources/6 RNG at seed${seed}`);
    for (const cell of saved.state.board) {
      if (['sword','arrow'].includes(cell.type)) assert.ok(raidElements.includes(cell.element));
      else assert.equal(Object.hasOwn(cell, 'element'), false);
    }
    assert.deepEqual(saved.rng, Array.from(old.raid.state.rngSeeds));
    assert.equal(b.storage.has('raid:activeRun'), false);
  }
});

for (let from = 0; from < 4; from++) for (let to = 0; to < 4; to++) test(`magitech elemental hit: ${raidElements[from]} weapons against ${raidElements[to]} keep neutral base and one rounding/armour stage`, () => {
  const b = elementalCombat(), r = b.raid;
  for (let slot = 0; slot < 3; slot++) elementalWeapon(r, slot, raidElements[from]);
  const target = raidOld(r, 3, { hp: 100, maxHp: 100, def: 4, element: raidElements[to] });
  raidSelect(r, [0,1,2,3]);
  const expected = 3 + 6 * elementalEighths[from][to] / 8;
  assert.equal(r.chainPreview().damage, 9); assert.equal(r.chainPreview().skulls[0].damage, expected);
  const rng = magitechSave(b).combat.rng;
  r.resolveChain(); b.advance(419); assert.equal(target.hp, 100);
  b.advance(1); assert.equal(target.hp, 100 - Math.max(0, Math.round(expected) - 4));
  assert.equal(r.state.swordsHits, 9); assert.equal(r.state.turn, 10);
  assert.equal(magitechSave(b).combat.rng, rng, 'preview/commit/coin refill must not reroll elements');
});

test('magitech elemental hit: mixed magic/broken/arrow weapons and every dual boss use exact per-weapon means, never a majority or repeated armour', () => {
  const b = elementalCombat(), r = b.raid, s = r.state;
  let cases = 0;
  for (let a = 0; a < 4; a++) for (let z = a + 1; z < 4; z++) for (let weapon = 0; weapon < 4; weapon++) {
    for (const W of [2,3,10]) for (const boost of [1,2]) for (const def of [0,4,8]) {
      raidBoard(r); s.selected = []; s.chainType = null; s._chainOrder = [0,1,2,3,4];
      s.hero.weaponDamage = W; s.swordBoost = boost - 1;
      const elements = [weapon, (weapon + 1) % 4, (weapon + 3) % 4, (weapon + 2) % 4], weights = [5,1,1,0];
      elementalWeapon(r, 0, raidElements[elements[0]], { magicSword: true });
      elementalWeapon(r, 1, raidElements[elements[1]], { type: 'arrow' });
      elementalWeapon(r, 2, raidElements[elements[2]]);
      elementalWeapon(r, 3, raidElements[elements[3]], { magicSword: true, broken: true });
      const boss = raidOld(r, 4, { hp: 1000, maxHp: 1000, def, element: raidElements[a], secondaryElement: raidElements[z] }, 1);
      raidSelect(r, [0,1,2,3,4]);
      const weightedEighths = weights.reduce((sum, q, i) => sum + q * (elementalEighths[elements[i]][a] + elementalEighths[elements[i]][z]) / 2, 0);
      const raw = (r.baseDamage() * 8 + W * boost * weightedEighths) / 8;
      const preview = r.chainPreview(); assert.equal(preview.swordCount, 7); assert.equal(preview.skulls[0].damage, raw);
      const result = r.applyChainEffects(7, 0, 0, 0, [4]);
      assert.equal(boss.hp, 1000 - Math.max(0, Math.round(raw) - def));
      assert.equal(result.dmgShown, r.baseDamage() + W * boost * 7);
      if (z - a === 2) assert.equal(raw, result.dmgShown, 'opposite boss pair neutralises every individual weapon');
      cases++;
    }
  }
  assert.equal(cases, 432);
});

test('magitech elemental hit: actual multi-target commit keeps leech/I1/common damage neutral and executes the original timed tail once', () => {
  const b = elementalPair({ leech: .2 }), r = b.raid, s = r.state;
  s.hero.hp = 10;
  const preview = r.chainPreview();
  assert.equal(preview.damage, 9); assert.deepEqual(Array.from(preview.skulls, t => t.damage), [10.5,7.5]);
  const before = raidJSON(s.rngSeeds); r.saveRun(); const elementRng = magitechSave(b).combat.rng;
  for (let n = 0; n < 5; n++) { r.chainPreview(); r.saveRun(); }
  assert.deepEqual(raidJSON(s.rngSeeds), before); assert.equal(magitechSave(b).combat.rng, elementRng);
  r.resolveChain(); b.advance(420);
  assert.deepEqual(b.targets.map(t => t.hp), [93,96]); assert.equal(s.hero.hp, 11);
  assert.equal(s.scrollLeechLabel.number, 1); assert.equal(s.swordsHits, 9);
  b.advance(549); assert.equal(s.turn, 10); b.advance(1); assert.equal(s.turn, 11);
  b.advance(2000); assert.equal(s.turn, 11); assert.equal(s.chainCount, 1);
});

test('magitech elemental hit: real selected Spiky targets reflect one neutral D0, not either elemental hit or their sum', () => {
  const b = elementalPair({ types: [2,2] }), r = b.raid, armour = r.state.hero.armor;
  r.resolveChain(); b.advance(420);
  assert.deepEqual(b.targets.map(t => t.hp), [93,96]); assert.equal(r.state.hero.hp, 41);
  assert.equal(r.state.hero.armor, armour); assert.equal(r.state.swordsHits, 9);
});

test('magitech elemental hit: existing off-chain Meat Shield gate blocks direct hits but intercepts neutral D0 with unchanged leech', () => {
  const b = elementalPair({ leech: .2 }), r = b.raid;
  const shield = raidOld(r, 35, { hp: 100, maxHp: 100, def: 4, element: 'water' }, 30);
  r.state.hero.hp = 10;
  assert.deepEqual(Array.from(r.chainPreview().skulls, t => t.damage), [0,0]);
  r.resolveChain(); b.advance(420);
  assert.deepEqual(b.targets.map(t => t.hp), [100,100]); assert.equal(shield.hp, 95);
  assert.equal(r.state.hero.hp, 11); assert.equal(r.state.swordsHits, 9);
});

test('magitech elemental hit: Uppercut adds only unboosted neutral W after elemental sum, before the classic Meat Shield/Chaotic gates', () => {
  for (const type of [0,14,27]) {
    const b = elementalCombat({ classId: 'warrior' }), r = b.raid, s = r.state;
    s.swordBoost = 1; s.leech = .2; s.hero.hp = 10;
    for (const slot of [12,0,1]) elementalWeapon(r, slot, 'water');
    const target = raidOld(r, 6, { hp: 100, maxHp: 100, element: 'fire' }, type);
    const shield = raidOld(r, 35, { hp: 100, maxHp: 100, element: 'air' }, 30);
    raidSelect(r, [12,6,0,1]);
    assert.equal(r.chainPreview().damage, 15); assert.equal(r.chainPreview().skulls[0].damage, type === 27 ? 18 : 20);
    r.resolveChain(); b.advance(420);
    assert.equal(target.hp, type === 27 ? 82 : 80, 'Armoured upward exit precedes Uppercut; Uppercut precedes Chaotic/outside shield');
    assert.equal(shield.hp, 85); assert.equal(s.hero.hp, 13); assert.equal(s.swordsHits, 15);
  }
});

test('magitech elemental hit: Armoured downward exit and Confused still suppress direct damage before elemental or Uppercut bonuses', () => {
  const b = elementalCombat({ classId: 'warrior' }), r = b.raid;
  for (const slot of [0,12]) elementalWeapon(r, slot, 'water');
  const target = raidOld(r, 6, { hp: 100, maxHp: 100, element: 'fire' }, 27);
  raidSelect(r, [0,6,12]); assert.equal(r.chainPreview().skulls[0].damage, 0);
  r.resolveChain(); b.advance(420); assert.equal(target.hp, 100);
  const c = elementalCombat(), q = c.raid, config = raidJSON(q.state.classConfig);
  config.flaws[1] = 'Confused'; config.perks[1] = 'Intimidating'; q.state.classConfig = config; q.state.turn = 4;
  for (const slot of [0,1]) elementalWeapon(q, slot, 'water');
  const confusedTarget = raidOld(q, 2, { hp: 100, maxHp: 100, element: 'fire' });
  raidSelect(q, [0,1,2]); assert.equal(q.chainPreview().skulls[0].damage, 0);
  q.resolveChain(); c.advance(420); assert.equal(confusedTarget.hp, 100);
});

test('magitech elemental preview: lethal highlights use final hit rounding, not raw10.5 versus HP11; base-only attacks stay neutral', () => {
  const b = elementalCombat(), r = b.raid;
  for (const slot of [0,1,2]) elementalWeapon(r, slot, 'water');
  raidOld(r, 3, { hp: 7, maxHp: 7, def: 4, element: 'fire' });
  raidSelect(r, [0,1,2,3]);
  assert.equal(r.chainPreview().skulls[0].damage, 10.5); assert.equal(r.chainPreview().lethals.length, 1);
  r.resolveChain(); b.advance(420); assert.equal(r.state.skullsKilled, 1);
  const c = elementalCombat(), q = c.raid;
  const targets = ['fire','air','water'].map((element, i) => raidOld(q, i, { hp: 100, maxHp: 100, element }));
  raidSelect(q, [0,1,2]); assert.equal(q.chainPreview().damage, 3);
  assert.deepEqual(Array.from(q.chainPreview().skulls, t => t.damage), [3,3,3]);
  q.resolveChain(); c.advance(420); assert.deepEqual(targets.map(t => t.hp), [97,97,97]);
});

test('magitech elemental saves: a v1 Continue stays classical through combat/new enemies and only explicit newGame opts into v4', () => {
  const b = legacyMagitechBrowser({ menus: true, seed: 2718 }), r = b.raid;
  raidBoard(r); r.state.forceCoins = 1; r.state.pierce = 0;
  for (const slot of [0,1,2]) r.state.board[slot] = { type: 'sword' };
  const target = raidOld(r, 3, { hp: 100, maxHp: 100, def: 4 });
  raidSelect(r, [0,1,2,3]); assert.equal(r.chainPreview().skulls[0].damage, 9);
  r.resolveChain(); b.advance(420); assert.equal(target.hp, 95);
  b.advance(2000); assert.equal(r.saveRun(), true);
  const saved = magitechSave(b); assert.equal(saved.version, 1); assert.equal(saved.combat, undefined);
  assert.equal(r.makeSkull(true).element, undefined); assert.equal(r.makeSkull(false, true, 4).element, undefined);
  const next = raidReload(b, { variant: 'magitech' }); assert.equal(next.raid.savedRunInfo().combatRules, 'classic-v1');
  next.raid.newGame(2718); assert.equal(magitechSave(next).version, 4); assert.equal(next.raid.savedRunInfo().combatRules, 'elements-affinity-v1');
});

for (const phase of ['match-chain','enemy','level','gold','armor']) test(`magitech elemental saves: ${phase} checkpoint restores elements/rules/RNG/rolled choices and executes no duplicate action`, () => {
  let b = elementalPair({ leech: .2 }), r = b.raid;
  if (phase === 'match-chain' || phase === 'enemy') {
    r.resolveChain(); b.advance(phase === 'match-chain' ? 200 : 420);
  } else {
    r.state.selected = []; r.state.chainType = null;
    if (phase === 'level') { r.state.hero.xp = 100; r.checkLevelUp(); b.elements.get('modal-body').querySelectorAll('.lv-card')[1].click(); }
    else if (phase === 'gold') { r.state.hero.gold = 100; r.checkGoldBox(); }
    else { r.state.upgradeBar = 100; r.checkUpgradeBar(); }
  }
  assert.equal(r.saveRun(), true); const saved = magitechSave(b);
  const idle = raidBrowser({ variant: 'magitech', menus: true, start: false, saved: b.storage });
  assert.equal(idle.raid.state, null); assert.equal(idle.raid.savedRunInfo().combatRules, 'elements-v1');
  assert.equal(idle.storage.get(MAGITECH_RUN_KEY), JSON.stringify(saved), 'reading never rewrites the save');
  assert.equal(idle.raid.resumeSavedRun(), true); const restored = magitechSave(idle);
  for (const key of ['combat','state','rng','menu','results','arrivals','next','region']) assert.deepEqual(restored[key], saved[key], phase + '/' + key);
  if (phase === 'match-chain' || phase === 'enemy') {
    idle.advance(phase === 'match-chain' ? 419 : 549); assert.equal(idle.raid.state.turn, 10);
    settleMagitechFixture(idle); assert.equal(idle.raid.state.turn, 11); assert.equal(idle.raid.state.chainCount, 1);
    assert.equal(idle.raid.state.swordsHits, 9);
  } else assert.equal(idle.raid.state.pendingModal, phase);
  // The next sequence is identical to an uninterrupted page's independent RNG.
  const a = r.makeSkull(false, true, 4), z = idle.raid.makeSkull(false, true, 4);
  assert.equal(z.element, a.element);
  b = idle; r = b.raid; assert.equal(r.saveStatus.state, 'saved');
});

test('magitech elemental saves: assigned sword/arrow/boss elements and next RNG sequence survive movement, shuffle, conversion and Continue', () => {
  let b = elementalCombat({ seed: 2718 }), r = b.raid;
  for (let i = 0; i < 30; i++) elementalWeapon(r, i, raidElements[i % 4]);
  const boss = raidOld(r, 35, { hp: 100, maxHp: 100 }, 1);
  const pair = [boss.element, boss.secondaryElement], original = [...r.state.board], elements = original.map(c => c.element);
  r.state.board[30] = { type: 'empty' }; r.placeNewTiles({});
  assert.equal(r.state.board[6], original[0]); assert.equal(r.state.board[6].element, elements[0]);
  r.activateSkill('earthquake'); b.advance(2000);
  for (const cell of original.filter(c => c.element)) assert.equal(cell.element, elements[original.indexOf(cell)]);
  r.saveRun(); const before = magitechSave(b).combat;
  r.activateSkill('magicsword'); b.advance(2000); assert.equal(r.state.board.filter(c => c.magicSword).length, 1);
  const weapons = r.state.board.filter(c => c.type === 'sword').map(c => c.element);
  r.activateSkill('volley'); b.advance(2000);
  assert.deepEqual(r.state.board.filter(c => c.type === 'arrow').map(c => c.element), weapons);
  r.saveRun(); assert.deepEqual(magitechSave(b).combat, before, 'magic and volley do not consume element RNG');
  const checkpoint = magitechSave(b); b = raidReload(b, { variant: 'magitech' }); r = b.raid;
  assert.deepEqual(magitechSave(b).combat, checkpoint.combat); assert.deepEqual(raidJSON(r.state.board), checkpoint.state.board);
  const nowBoss = r.state.board.find(c => c.type === 'skull').skull;
  assert.deepEqual([nowBoss.element, nowBoss.secondaryElement], pair);
  const future = Array.from({ length: 50 }, () => r.createNewTile());
  const other = raidBrowser({ variant: 'magitech', menus: true, start: false, saved: { [MAGITECH_RUN_KEY]: JSON.stringify(checkpoint) } });
  assert.equal(other.raid.resumeSavedRun(), true);
  assert.deepEqual(raidJSON(future), raidJSON(Array.from({ length: 50 }, () => other.raid.createNewTile())));
});

test('magitech elemental generation: ordinary local, special70/10/10/10, boss local+uniform other and weapon25 each on a separate reproducible stream', () => {
  const b = raidBrowser({ variant: 'magitech', classId: 'ranger', seed: 8128 });
  const control = legacyMagitechBrowser({ classId: 'ranger', seed: 8128 });
  const r = b.raid, q = control.raid, weapons = [0,0,0,0], specials = [0,0,0,0], bosses = [0,0,0,0];
  r.state.turn = q.state.turn = 100; r.state.promotionRemaining = q.state.promotionRemaining = 1000000;
  let arrows = 0;
  const withoutElements = value => { const v = raidJSON(value); delete v.element; delete v.secondaryElement; if (v.skull) { delete v.skull.element; delete v.skull.secondaryElement; } return v; };
  for (let n = 0; n < 10000; n++) {
    const tile = r.createNewTile(), old = q.createNewTile();
    assert.deepEqual(withoutElements(tile), raidJSON(old), 'element rolls cannot change type/spawn/stats/decoration draws');
    if (['sword','arrow'].includes(tile.type)) { weapons[raidElements.indexOf(tile.element)]++; if (tile.type === 'arrow') arrows++; }
    if (tile.type === 'skull') assert.equal(tile.skull.element, 'earth');
    const s = r.makeSkull(false, true, 4), t = q.makeSkull(false, true, 4);
    specials[raidElements.indexOf(s.element)]++; assert.deepEqual(withoutElements(s), raidJSON(t));
    const boss = r.makeSkull(true), oldBoss = q.makeSkull(true);
    assert.equal(boss.element, 'earth'); assert.notEqual(boss.secondaryElement, 'earth');
    bosses[raidElements.indexOf(boss.secondaryElement)]++; assert.deepEqual(withoutElements(boss), raidJSON(oldBoss));
  }
  assert.ok(arrows > 0);
  const total = weapons.reduce((a,z) => a+z, 0); for (const count of weapons) assert.ok(Math.abs(count / total - .25) < .04, JSON.stringify(weapons));
  specials.forEach((count, i) => assert.ok(Math.abs(count / 10000 - (i === 2 ? .7 : .1)) < .025, JSON.stringify(specials)));
  bosses.forEach((count, i) => assert.ok(i === 2 ? count === 0 : Math.abs(count / 10000 - 1/3) < .025, JSON.stringify(bosses)));
  assert.deepEqual(raidJSON(r.state.rngSeeds), raidJSON(q.state.rngSeeds));
  assert.equal(r.saveRun(), true); const saved = magitechSave(b);
  const next = raidReload(b, { variant: 'magitech' }); assert.deepEqual(magitechSave(next).combat, saved.combat);
});

test('magitech elemental generation: zero run seed still gives new converted weapons all four elements, never the cosmetic hash', () => {
  const b = elementalCombat({ seed: 0 }), r = b.raid;
  raidBoard(r, 'coin'); r.activateSkill('dazzle'); b.advance(2000);
  assert.equal(new Set(r.state.board.map(c => c.element)).size, 4);
  const original = raidJSON(r.state.board); r.saveRun(); const saved = magitechSave(b);
  for (let n = 0; n < 5; n++) { r.chainPreview(); r.saveRun(); }
  assert.deepEqual(raidJSON(r.state.board), original); assert.deepEqual(magitechSave(b).combat, saved.combat);
});

test('magitech elemental generation: Lich stats-only revival and Exorcise keep primary assignment without a hidden element draw', () => {
  const b = elementalCombat(), r = b.raid;
  const lich = raidOld(r, 0, { hp: 1, maxHp: 1, phylactery: true, lichDelay: false, element: 'water' }, 32);
  r.saveRun(); const before = magitechSave(b).combat;
  r.applySpecialAbilities(); assert.equal(lich.phylactery, false); assert.equal(lich.element, 'water');
  r.saveRun(); assert.deepEqual(magitechSave(b).combat, before);
  raidBoard(r); const boss = raidOld(r, 0, { hp: 100, maxHp: 100 }, 1), primary = boss.element;
  r.saveRun(); const other = magitechSave(b).combat;
  r.activateSkill('exorcise'); b.advance(2000);
  assert.equal(boss.boss, false); assert.equal(boss.type, 0); assert.equal(boss.element, primary); assert.equal(Object.hasOwn(boss, 'secondaryElement'), false);
  assert.equal(r.saveRun(), true); assert.deepEqual(magitechSave(b).combat, other);
});

for (const invalid of ['missing-combat','unknown-rules','extra-combat','missing-rng','negative-rng','fraction-rng','overflow-rng','string-rng','missing-region','missing-weapon','bad-weapon','nonweapon-element','missing-enemy','missing-boss-secondary','same-boss-secondary','nonboss-secondary','v1-with-elements','v1-with-combat','unknown-version']) test(`magitech elemental saves: reject ${invalid} without switching the live rules/RNG or overwriting another checkpoint`, () => {
  const b = elementalCombat(), r = b.raid;
  elementalWeapon(r, 0, 'water'); raidOld(r, 1, {}, 1); raidOld(r, 2, {}, 4);
  assert.equal(r.saveRun(), true); const valid = magitechSave(b), data = raidJSON(valid);
  if (invalid === 'missing-combat') delete data.combat;
  if (invalid === 'unknown-rules') data.combat.rules = 'elements-v2';
  if (invalid === 'extra-combat') data.combat.extra = true;
  if (invalid === 'missing-rng') delete data.combat.rng;
  if (invalid === 'negative-rng') data.combat.rng = -1;
  if (invalid === 'fraction-rng') data.combat.rng = .5;
  if (invalid === 'overflow-rng') data.combat.rng = 0x100000000;
  if (invalid === 'string-rng') data.combat.rng = '71';
  if (invalid === 'missing-region') delete data.region;
  if (invalid === 'missing-weapon') delete data.state.board[0].element;
  if (invalid === 'bad-weapon') data.state.board[0].element = 'lava';
  if (invalid === 'nonweapon-element') data.state.board[35].element = 'fire';
  if (invalid === 'missing-enemy') delete data.state.board[2].skull.element;
  if (invalid === 'missing-boss-secondary') delete data.state.board[1].skull.secondaryElement;
  if (invalid === 'same-boss-secondary') data.state.board[1].skull.secondaryElement = data.state.board[1].skull.element;
  if (invalid === 'nonboss-secondary') data.state.board[2].skull.secondaryElement = 'fire';
  if (invalid === 'v1-with-elements') { data.version = 1; delete data.combat; }
  if (invalid === 'v1-with-combat') { const old = legacyMagitechSave(data); Object.assign(data, old); }
  if (invalid === 'unknown-version') data.version = 99;
  const state = r.state, before = raidJSON(state), raw = JSON.stringify(data); b.storage.set(MAGITECH_RUN_KEY, raw);
  assert.equal(r.resumeSavedRun(), false); assert.equal(r.state, state); assert.deepEqual(raidJSON(r.state), before);
  assert.equal(b.storage.get(MAGITECH_RUN_KEY), raw); assert.equal(r.saveStatus.state, 'invalid');
  // A rejected file cannot change ELEMENTAL_COMBAT either.
  b.storage.set(MAGITECH_RUN_KEY, JSON.stringify(valid)); assert.equal(r.resumeSavedRun(), true);
  assert.deepEqual(magitechSave(b).combat, valid.combat);
});

function neutralElementalSnapshot(raid) {
  const state = magitechSnapshot(raid);
  for (const cell of state.board) {
    delete cell.element;
    if (cell.skull) { delete cell.skull.element; delete cell.skull.secondaryElement; }
  }
  return state;
}

test('magitech elemental secondary: all 42 skills keep classical effects/6 RNG and leave a valid resumable v2 checkpoint', () => {
  const source = readFileSync(new URL('./raid.js', import.meta.url), 'utf8');
  const ids = [...(source.slice(source.indexOf('  const SKILLS = ['), source.indexOf('  // 7 уровней'))
    + source.slice(source.indexOf('  const UNIQUE_SKILLS = ['), source.indexOf('  const ALL_SKILLS'))).matchAll(/\{ id: '(\w+)'/g)].map(match => match[1]);
  assert.equal(ids.length, 42);
  for (const [index, id] of ids.entries()) {
    const options = { classId: raidClassIds[index % 10], difficulty: index % 3, menus: true, seed: 8801 };
    const old = raidBrowser(options), b = raidBrowser({ ...options, variant: 'magitech' });
    const released = magitechSave(b); released.version = 2; released.combat.rules = 'elements-v1'; delete released.combat.affinity;
    b.storage.set(MAGITECH_RUN_KEY, JSON.stringify(released)); assert.equal(b.raid.resumeSavedRun(), true);
    for (const browser of [old, b]) {
      const r = browser.raid, s = r.state;
      s.turn = 80; s.hero.hp = 900; s.hero.maxHp = 1000; s.hero.armor = 20; s.hero.maxArmor = 30;
      s.hero.xp = 99; s.hero.gold = 99; s.upgradeBar = 99; s.trophyRemaining = 30;
      s.skills = [id]; s.skillLevels = { [id]: 1 }; s.skillCooldowns = { [id]: 0 };
      s.board = Array.from({ length: 36 }, (_, slot) => ({ type: ['sword', 'shield', 'coin', 'potion'][slot % 4], ...(browser === b && slot % 4 === 0 ? { element: raidElements[slot / 4 % 4] } : {}) }));
      raidOld(r, 4, { atk: 2, hp: 3, maxHp: 20, def: 2 });
      raidOld(r, 15, { atk: 3, hp: 30, maxHp: 30, def: 3 }, 2 + index % 31);
      raidOld(r, 30, { atk: 5, hp: 35, maxHp: 35, def: 4 }, 1);
      r.activateSkill(id); settleMagitechFixture(browser);
    }
    assert.deepEqual(neutralElementalSnapshot(b.raid), magitechSnapshot(old.raid), id + ': no elemental bonus for existing skills');
    assert.equal(b.raid.saveRun(), true, id + ': transformed board must pass v2 validation');
    const saved = magitechSave(b), next = raidReload(b, { variant: 'magitech' });
    assert.equal(saved.version, 2); assert.deepEqual(raidJSON(next.raid.state.board), saved.state.board, id);
    assert.deepEqual(magitechSave(next).combat, saved.combat, id);
  }
});

test('magitech elemental secondary: all 31 inherited special ability paths retain classical effects/6 RNG, stable surviving assignments and valid v2 saves', () => {
  for (let type = 2; type <= 32; type++) {
    const options = { classId: raidClassIds[type % 10], difficulty: type % 3, menus: true, seed: 2718 };
    const old = raidBrowser(options), b = raidBrowser({ ...options, variant: 'magitech' });
    let identities;
    for (const browser of [old, b]) {
      const r = browser.raid, s = r.state;
      s.turn = 100; s.hero.hp = s.hero.maxHp = 10000; s.hero.maxArmor = s.hero.armor = 30;
      s.board = Array.from({ length: 36 }, (_, slot) => ({ type: ['sword','shield','coin','potion'][slot % 4], ...(browser === b && slot % 4 === 0 ? { element: raidElements[slot / 4 % 4] } : {}) }));
      raidOld(r, 14, { atk: 2, hp: 1000, maxHp: 1000, countdown: 1, ...(type === 32 ? { phylactery: true, lichDelay: false, hp: 1 } : {}) }, type);
      raidOld(r, 24, { atk: 1, hp: 4, maxHp: 4 });
      if (browser === b) identities = new Map(s.board.map(c => [c.skull || c, c.skull ? [c.skull.element,c.skull.secondaryElement] : [c.element]]));
      r.endEnemyTurn(); settleMagitechFixture(browser);
    }
    assert.deepEqual(neutralElementalSnapshot(b.raid), magitechSnapshot(old.raid), 'special ' + type);
    for (const cell of b.raid.state.board) {
      const key = cell.skull || cell, prior = identities.get(key);
      if (prior) assert.deepEqual(cell.skull ? [cell.skull.element,cell.skull.secondaryElement] : [cell.element], prior, 'surviving assignment ' + type);
    }
    assert.equal(b.raid.saveRun(), true, 'special ' + type + ': v2 validation after conversion/movement');
    const saved = magitechSave(b), next = raidReload(b, { variant: 'magitech' });
    assert.deepEqual(raidJSON(next.raid.state.board), saved.state.board, 'special ' + type + ': Continue');
    assert.deepEqual(magitechSave(next).combat, saved.combat);
  }
});

test('magitech elemental transformations: Hack preserves existing weapons; Big Game retains an enemy primary; neither rerolls an existing element', () => {
  const b = elementalCombat({ seed: 2718 }), r = b.raid;
  for (let slot = 0; slot < 36; slot++) elementalWeapon(r, slot, raidElements[slot % 4]);
  const assignments = r.state.board.map(c => c.element); r.saveRun(); const before = magitechSave(b).combat;
  r.activateSkill('hack'); b.advance(2000); assert.deepEqual(r.state.board.map(c => c.element), assignments);
  r.saveRun(); assert.deepEqual(magitechSave(b).combat, before, 'Hack did not introduce any new weapon');
  raidBoard(r); const target = raidOld(r, 0, { hp: 100, maxHp: 100, element: 'water' });
  r.saveRun(); const prior = magitechSave(b).combat;
  r.activateSkill('biggame'); b.advance(2000);
  const promoted = r.state.board.find(c => c.type === 'skull').skull;
  assert.notEqual(promoted.uid, target.uid, 'retain classic stats/UID promotion path');
  assert.equal(promoted.promotedByBigGame, true); assert.equal(promoted.element, 'water');
  assert.equal(r.saveRun(), true);
  if (!promoted.boss) assert.deepEqual(magitechSave(b).combat, prior, 'existing primary never rerolled');
  else assert.ok(promoted.secondaryElement && promoted.secondaryElement !== 'water');
  // Exorcise can have left a nonlocal ordinary. If its later promotion rolls a
  // boss, the additional element must differ from THAT primary, not RUN_REGION.
  for (const element of raidElements) for (let i = 0; i < 64; i++) {
    const boss = r.makeSkull(true, false, undefined, { element });
    assert.equal(boss.element, element); assert.notEqual(boss.secondaryElement, element);
    assert.ok(raidElements.includes(boss.secondaryElement));
    r.state.board[0] = { type: 'skull', skull: boss }; assert.equal(r.saveRun(), true);
  }
});

test('magitech A1 pools: one common module, four slots, fixed class-first skill and gated next-run replacements', () => {
  const b=raidBrowser({variant:'magitech',menus:true,seed:2718}),r=b.raid;
  assert.deepEqual(raidJSON(r.state.skills),[],'never automatically granted');
  assert.equal(r.classSkillPool().includes('retune'),true); assert.equal(r.prepareSkillOption('retune').duration,12);
  let seen=false; r.state.hero.level=3;
  for(let n=0;n<200;n++) if(r.chooseLevelOptions().some(o=>o.id==='retune')) seen=true;
  assert.equal(seen,true,'reachable through normal level reward draws');
  for(const id of ['heal','repair','treasure','retune']) assert.equal(r.installSkillOption(r.prepareSkillOption(id)),true);
  assert.equal(r.state.skills.length,4); assert.equal(r.installSkillOption(r.prepareSkillOption('freeze')),false);
  for(let n=0;n<80;n++) for(const offer of r.makeShopOffers()) assert.notEqual(offer.scroll?.id,'retune','no native/I1 scroll');
  r.unlockClass('warrior'); const config=r.defaultClassConfig('warrior');config.replacements[1]='retune';
  assert.equal(r.saveClassConfig('warrior',config),true);
  assert.equal(r.getClassConfig('warrior').replacements[1],null,'level1 grants no replacements');
  r.levelUpClass('warrior'); assert.equal(r.saveClassConfig('warrior',config),true);
  assert.equal(r.getClassConfig('warrior').replacements[1],'retune');
  r.setSelectedClassId('warrior');r.newGame(2718);
  assert.equal(r.state.skills.length,0);assert.equal(r.classSkillPool().includes('retune'),true);
  r.state.hero.level=2;assert.equal(r.chooseLevelOptions()[0].id,'charge','class-first skill unchanged');
  const frozen=JSON.stringify(r.state.classConfig);r.saveClassConfig('warrior',r.defaultClassConfig('warrior'));
  assert.equal(JSON.stringify(r.state.classConfig),frozen,'editing next-run pool never rewrites live config');
});
test('magitech A1 cooldown: Cunning applies only as the fourth skill and the ordinary upgrade preserves charge', () => {
  const b=raidBrowser({variant:'magitech',classId:'assassin',menus:true,seed:2718}),r=b.raid;
  for(const id of ['heal','repair','treasure','retune']) assert.equal(r.installSkillOption(r.prepareSkillOption(id)),true);
  assert.equal(r.skillDuration('retune'),11);assert.equal(r.state.cooldownAdjust.retune,-1);
  r.state.skillCooldowns.retune=5;
  const option=r.prepareSkillOption('retune');assert.equal(option.duration,10);assert.equal(option.charge,6);
  r.installSkillOption(option);assert.equal(r.state.skillCooldowns.retune,4);assert.equal(r.skillDuration('retune'),10);
  assert.equal(r.saveRun(),true);assert.equal(r.resumeSavedRun(),true);assert.equal(r.state.skillCooldowns.retune,4);
});

test('magitech A1 save: a pending normal level reward containing retune and two marks resumes without learning or rerolling it', () => {
  const b=raidBrowser({variant:'magitech',menus:true,seed:2718}),r=b.raid;
  let beforeRng,choices;
  for(let n=0;n<300;n++) {
    beforeRng=Array.from(r.state.rngSeeds);choices=r.chooseLevelOptions();
    if(choices.some(o=>o.id==='retune'))break;
  }
  assert.ok(choices.some(o=>o.id==='retune'));
  // rngSeeds is a display snapshot, not the generator. Rewind the real
  // stream through the same strict checkpoint loader used by Continue.
  assert.equal(r.saveRun(),true);const rewind=magitechSave(b);rewind.rng=beforeRng;
  b.storage.set(MAGITECH_RUN_KEY,JSON.stringify(rewind));assert.equal(r.resumeSavedRun(),true);
  r.state.hero.xp=100;r.checkLevelUp();
  assert.equal(r.state.pendingModal,'level');
  const index=r.state.menuCandidates.findIndex(o=>o.id==='retune');assert.ok(index>=0);
  r.state.levelDraft=[index,index===0?1:0];assert.equal(r.saveRun(),true);
  const saved=magitechSave(b);assert.equal(saved.version,4);assert.equal(saved.menu.choices[index].id,'retune');
  const next=raidReload(b,{variant:'magitech',menus:true});
  assert.equal(next.raid.state.pendingModal,'level');assert.deepEqual(raidJSON(next.raid.state.skills),[]);
  assert.deepEqual(raidJSON(next.raid.state.levelDraft),saved.menu.picked);
  assert.deepEqual(magitechSave(next).menu,saved.menu);assert.deepEqual(magitechSave(next).rng,saved.rng);
});

// M0 is a role/source adaptation, NOT a third layer or a new balance formula.
test('magitech mech and pilot: unchanged progression/configuration functions are identical to the classic control', () => {
  const classic = readFileSync(new URL('./raid.js', import.meta.url), 'utf8');
  const current = readFileSync(new URL('./raid_magitech.js', import.meta.url), 'utf8');
  for (const name of ['defaultClassConfig', 'classRules', 'isRaceUnlocked', 'isTraitUnlocked', 'classEditorInfo',
    'normaliseClassConfig', 'loadClassConfigs', 'getClassConfig', 'saveClassConfig', 'runClassConfig']) {
    const re = new RegExp(`^  function ${name}\\([^]*?^  }`, 'm');
    assert.equal(current.match(re)?.[0], classic.match(re)?.[0], name);
    assert.ok(current.match(re), name);
  }
  const visual = current.slice(current.indexOf('  const VISUAL_KEY'), current.indexOf('  // ============================================================'));
  assert.ok(visual.includes("'raid:visualPreferences'"));
  assert.doesNotMatch(visual, /\b(?:saveRun|newGame|resumeSavedRun|scheduleRunStep|ACTION_TIMER|RNG_SEEDS|RUN_SAVE_KEY|UPDATES_KEY)\b|\bS\./);
});

test('magitech mech and pilot: original level gates, donor thresholds and fixed first unique remain unchanged at levels 1–10', () => {
  const r = raidBrowser({ variant: 'magitech', classId: 'warrior' }).raid;
  assert.equal(r.isRaceUnlocked('Human'), true); assert.equal(r.isRaceUnlocked('Changeling'), false);
  r.unlockClass('mage'); assert.equal(r.isTraitUnlocked('perks', 'Gifted'), true);
  for (let level = 1; level <= 10; level++) {
    while (r.classLevelOf('warrior') < level) r.levelUpClass('warrior');
    while (r.classLevelOf('mage') < level) r.levelUpClass('mage');
    const rules = r.classRules('warrior');
    assert.equal(rules.canChangeRace, level >= 5); assert.equal(rules.replacementLimit, 2 * Math.floor(level / 2));
    assert.deepEqual(Array.from(rules.pairSlots), [true, level >= 3, level >= 7, level >= 9]);
    assert.equal(r.isRaceUnlocked('Changeling'), level >= 5); assert.equal(r.isSkillUnlocked('rune'), level >= 10);
    const draft = r.defaultClassConfig('warrior'); draft.race = 'Changeling'; draft.replacements.fill('rune');
    assert.equal(r.saveClassConfig('warrior', draft), true);
    const clean = r.getClassConfig('warrior');
    assert.equal(clean.race, level >= 5 ? 'Changeling' : 'Dwarf'); assert.equal(clean.replacements[0], null);
    assert.equal(clean.replacements.filter(Boolean).length, level >= 10 ? 10 : 0);
  }
  assert.equal(r.saveClassConfig('adventurer', { race: 'Orc' }), false);
  r.setSelectedClassId('adventurer'); r.newGame(2718);
  assert.equal(r.clsRace(), 'Human'); assert.deepEqual(raidJSON(r.state.classConfig.perks), [null, null, null, null]);
  assert.equal(r.state.skills.length, 0); assert.equal(r.classSkillPool().includes('retune'), true);
});

test('magitech mech and pilot: all 91 unlocked pairings match inherited classic packages/RNG through a real nemesis turn and Continue', () => {
  const pilots = { adventurer: 'Human', barbarian: 'Orc', mage: 'Changeling', ranger: 'Elf', rogue: 'Sauren', paladin: 'Dragonkin', priest: 'Faun', raider: 'Halfling', warrior: 'Dwarf', assassin: 'Werewolf' };
  const nemesis = { Human: 0, Orc: 12, Changeling: 19, Elf: 9, Sauren: 13, Dragonkin: 8, Faun: 16, Halfling: 5, Dwarf: 14, Werewolf: 20 };
  let count = 0;
  for (const [classId, nativePilot] of Object.entries(pilots)) {
    const a = raidBrowser({ classId, menus: true, seed: 2718 }), b = legacyMagitechBrowser({ classId, menus: true, seed: 2718 });
    for (const x of [a, b]) raidMasterClasses(x.raid);
    assert.equal(b.raid.defaultClassConfig(classId).race, nativePilot);
    for (const pilot of classId === 'adventurer' ? ['Human'] : Object.values(pilots)) {
      for (const x of [a, b]) {
        const r = x.raid, config = r.defaultClassConfig(classId); config.race = pilot;
        if (classId !== 'adventurer') {
          config.perks[1] = 'Unexpected'; config.flaws[1] = 'Confused'; config.replacements[1] = 'heal';
          assert.equal(r.saveClassConfig(classId, config), true);
        }
        r.newGame(2718);
      }
      // Test-only v1 fixture: M0 must not change ANY inherited package, even
      // without elemental combat/A1. Production never converts/downgrades runs.
      assert.equal(b.raid.saveRun(), true);
      b.storage.set(MAGITECH_RUN_KEY, JSON.stringify(legacyMagitechSave(JSON.parse(b.storage.get(MAGITECH_RUN_KEY)))));
      assert.equal(b.raid.resumeSavedRun(), true);
      assert.equal(b.raid.clsRace(), pilot); assert.equal(Object.isFrozen(b.raid.state.classConfig), true);
      assert.deepEqual(magitechSnapshot(b.raid), magitechSnapshot(a.raid), `${classId}/${pilot}/start`);
      assert.deepEqual(raidJSON(b.raid.classSkillPool()), raidJSON(a.raid.classSkillPool()), `${classId}/${pilot}/pool`);
      for (const x of [a, b]) {
        const r = x.raid, s = r.state; s.turn = 100; s.hero.hp = s.hero.maxHp = 5000;
        s.hero.armor = s.hero.maxArmor = 30; s.poison = 3; s.trophyRemaining = 30;
        raidBoard(r, 'shield'); raidOld(r, 0, { atk: 1, hp: 1, maxHp: 1, def: 0 });
        s.board[1] = { type: 'sword' }; s.board[2] = { type: 'sword' };
        if (nemesis[pilot]) raidOld(r, 30, { atk: 3, hp: 60, maxHp: 60, def: 3, countdown: 1 }, nemesis[pilot]);
        raidSelect(r, [0, 1, 2]); r.resolveChain(); settleMagitechFixture(x);
      }
      assert.deepEqual(magitechSnapshot(b.raid), magitechSnapshot(a.raid), `${classId}/${pilot}/nemesis-turn`);
      const frozen = JSON.stringify(b.raid.state.classConfig);
      assert.equal(b.raid.saveRun(), true, `${classId}/${pilot}: ${b.raid.saveStatus.message}`);
      assert.equal(b.raid.resumeSavedRun(), true);
      assert.equal(JSON.stringify(b.raid.state.classConfig), frozen); assert.equal(b.raid.clsRace(), pilot);
      assert.equal(a.raid.saveRun(), true);
      assert.deepEqual(magitechSave(b).rng, JSON.parse(a.storage.get('raid:activeRun')).rng); count++;
    }
  }
  assert.equal(count, 91, 'controlled package parity, not a balance simulation or a new roster');
});

test('magitech direct settings source: compact board overlay reuses preferences/audio, never writes gameplay or schedules a combat continuation', () => {
  const js = readFileSync(new URL('./raid_magitech.js', import.meta.url), 'utf8');
  const html = readFileSync(new URL('./raid_magitech.html', import.meta.url), 'utf8');
  const css = readFileSync(new URL('./raid_magitech.css', import.meta.url), 'utf8');
  const section = js.slice(js.indexOf('  // --- direct board settings'), js.indexOf('  // --- first-entry release notes'));
  assert.match(section, /function openGameSettings/); assert.match(section, /function closeGameSettings/);
  assert.doesNotMatch(section, /\b(?:saveRun|queueRunSave|resumeRewards|closeRetune|cancelPointerGesture|setTimeout|setInterval|randInt)\s*\(|Math\.random|RNG_SEEDS|localStorage|profileStorage/);
  assert.doesNotMatch(section, /\bS(?:\.[\w]+|\[[^\]]+\])*\s*(?:=(?!=)|\+\+|--)/);
  assert.match(section, /activeDrag/); assert.match(section, /stopImmediatePropagation/); assert.match(section, /bindReadOnlyDismiss\(close, closeGameSettings/);
  assert.match(html, /id="settings-btn"[^>]*aria-haspopup="dialog"[^>]*aria-controls="game-settings"[^>]*aria-expanded="false"/);
  assert.equal((html.match(/id="sound-on"/g) || []).length, 1);
  assert.equal((html.match(/role="switch"/g) || []).length, 4);
  assert.match(css, /\.game-settings\s*\{[^}]*position:\s*fixed/);
  assert.match(css, /\.game-settings-card\s*\{[^}]*272px[^}]*border-radius:\s*28px/);
  assert.match(css, /\.settings-row\s*\{[^}]*min-height:\s*48px/);
  assert.match(css, /\.raid-region-screen\[hidden\]\s*\{\s*display:\s*none !important;\s*\}/);
});


test('magitech defeat exit source: navigation clears only the terminal UI, never saves, starts or advances a run', () => {
  const js = readFileSync(new URL('./raid_magitech.js', import.meta.url), 'utf8');
  const html = readFileSync(new URL('./raid_magitech.html', import.meta.url), 'utf8');
  const leave = js.match(/^  function leaveFinishedRun\([^]*?^  }/m)?.[0];
  assert.ok(leave); assert.match(leave, /finishedRunView\(\)/); assert.match(leave, /showClassPicker\(\)/);
  assert.doesNotMatch(leave, /\b(?:newGame|saveRun|queueRunSave|resumeRewards|closeModal|recordFinishedRun|setTimeout|randInt)\s*\(|RNG_SEEDS/);
  assert.match(html, /id="picker-title"[^>]*tabindex="-1"/);
  assert.match(html, /id="modal"[^>]*aria-labelledby="modal-title"/);
  assert.match(html, /<b>После поражения\.<\/b>/);
});

test('magitech affinity source: all four distributions have exact 30/20/25/25 buckets and damage tiers; no extra old RNG, preferences or item stats', () => {
  const js = readFileSync(new URL('./raid_magitech.js', import.meta.url), 'utf8');
  const fn = name => js.match(new RegExp('^  function ' + name + '\\([^]*?^  }', 'm'))?.[0];
  const source = ['affinityCounterElement', 'pilotWeaponMultiplier', 'rollWeaponElement'].map(fn).join('\n');
  // Two helpers are single-line declarations; extract those independently.
  const counter = js.match(/^  function affinityCounterElement[^\n]+/m)[0];
  const helpers = counter + '\n' + fn('pilotWeaponMultiplier') + '\n' + fn('rollWeaponElement');
  for (const level of [1, 2, 3, 10]) for (const element of [null, 'fire', 'air', 'earth', 'water']) {
    const context = vm.createContext({ level, element });
    vm.runInContext(`const RAID_ELEMENTS=['fire','air','earth','water']; let roll=0, calls=0;
      function runPilotAffinity(){return {level,element:level>=2?element:null};}
      function elementRandom(){calls++;return roll;}
      ${helpers}
      const counts={fire:0,air:0,earth:0,water:0};
      for(let i=0;i<100;i++){roll=(i+.5)/100;counts[rollWeaponElement()]++;}
      globalThis.result={counts,calls,multipliers:RAID_ELEMENTS.map(pilotWeaponMultiplier)};`, context);
    const value = JSON.parse(JSON.stringify(context.result));
    assert.equal(value.calls, 100);
    for (const e of ['fire', 'air', 'earth', 'water']) {
      const counterElement = {fire:'water',air:'fire',earth:'air',water:'earth'}[element];
      assert.equal(value.counts[e], level < 2 || !element ? 25 : e === element ? 30 : e === counterElement ? 20 : 25);
      assert.equal(value.multipliers[['fire','air','earth','water'].indexOf(e)], level < 3 || !element ? 1 : e === element ? 1.05 : e === counterElement ? .95 : 1);
    }
  }
  assert.doesNotMatch(source, /\b(?:randInt|random)\s*\(|Math\.random|localStorage|profileStorage|equipment|setTimeout/);
  assert.doesNotMatch(fn('saveClassConfig'), /affinity|pickerAffinities/);
  assert.match(fn('newGame'), /pickerAffinities\.clear\(\)/); assert.match(fn('resumeSavedRun'), /Object\.freeze\(ELEMENTAL_COMBAT\.affinity\)/);
});

test('release notice shows one short current item and keeps older notes collapsed in history', () => {
  const html = readFileSync(new URL('./raid_magitech.html', import.meta.url), 'utf8');
  const current = html.match(/<ul id="updates-current">([\s\S]*?)<\/ul>/)?.[1] || '';
  assert.equal((current.match(/<li>/g) || []).length, 1);
  assert.match(current, /Музыка в гараже и бою настраивается отдельно/);
  const archive = html.match(/<details class="updates-history" id="updates-history">([\s\S]*?)<\/details>/)?.[1] || '';
  assert.match(archive, /<summary>История обновлений<\/summary>/);
  assert.match(archive, /<ol>[\s\S]*<li>/);
  assert.doesNotMatch(archive, /<details[^>]*open/);
});

test('the Flaming enemy uses a transient flame effect and clearly explains its actual threat', () => {
  const js = readFileSync(new URL('./raid_magitech.js', import.meta.url), 'utf8');
  const css = readFileSync(new URL('./raid_magitech.css', import.meta.url), 'utf8');
  const html = readFileSync(new URL('./raid_magitech.html', import.meta.url), 'utf8');
  assert.match(html, /<b>Музыка в гараже и бою настраивается отдельно\.<\/b>/);
  assert.match(js, /class="orb-fire" aria-hidden="true">' \+ ORB_FIRE/);
  assert.match(js, /firePulses\.push\(view\.node\.querySelector\('\.orb-fire'\)\)/);
  assert.match(js, /overlay\.classList\.add\('ignite-pop'\)/);
  assert.match(css, /\.orb-fire\.ignite-pop \{ animation:orb-ignite-pop/);
  assert.doesNotMatch(js, /animateOrb\(orb\.querySelector\('\.orb-fire'\)/);
  assert.match(js, /Каждый ход он также поджигает до 8 занятых соседних клеток/);
  assert.match(js, /горящие соседние черепа получают урон, равный его атаке, но сам поджог герою не вредит/);
  assert.match(js, /Опасность для героя — обычная атака Огненного \(×2\), она входит в общий залп/);
  assert.match(css, /\.orb-fire \{ position:absolute/);
  assert.doesNotMatch(js + css, /orb-igniter|orb-burn|in-fire-zone|fireZone/);
});

test('mobile mech garage is a no-scroll full-screen scene with icon tools, opaque mech cards and a textured resume CTA', () => {
  const html = readFileSync(new URL('./raid_magitech.html', import.meta.url), 'utf8');
  const css = readFileSync(new URL('./raid_magitech.css', import.meta.url), 'utf8');
  const builder = readFileSync(new URL('./build_raid_magitech.py', import.meta.url), 'utf8');
  assert.match(html, /id="picker-history"[^>]*>[\s\S]*?picker-tool-icon/);
  assert.equal((html.match(/class="picker-tool-icon(?:\s|\")/g) || []).length, 4);
  assert.match(html, /id="picker-visual"[\s\S]*?<span>Вид<\/span>/);
  assert.match(css, /--raid-garage-background: url\('__RAID_GARAGE_BACKGROUND__'\)/);
  assert.match(css, /\.class-picker:not\(\.region-mode-open\) \.raid-intro \{ display:none !important; \}/);
  assert.match(css, /\.picker-main > \.picker-tools > #picker-updates,[\s\S]*?#picker-visual \{ grid-column:auto; \}/);
  assert.match(css, /\.picker-main > \.picker-tools > #picker-history \{ margin:0; \}/);
  assert.match(css, /\.class-picker:not\(\.region-mode-open\) \.picker-card-btn \{[\s\S]*?background:linear-gradient\(155deg/);
  assert.match(css, /grid-template-rows:repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(css, /height:clamp\(220px,38vh,390px\); max-height:44vh; overflow:hidden/);
  assert.match(css, /background:var\(--raid-continue-texture\) center \/ 100% 100% no-repeat/);
  assert.match(html, /class="picker-tool-icon picker-eye-icon"[\s\S]*?ellipse cx="16" cy="16" rx="4" ry="6"/);
  assert.match(builder, /GARAGE_BACKGROUND = 'assets\/mech_garage_bg\.webp'/);
  assert.match(builder, /LAUNCH_TEXTURE = 'assets\/raid_launch_texture\.webp'/);
  assert.match(builder, /__RAID_LAUNCH_TEXTURE__/);
  assert.match(builder, /ROUND_BUTTON_TEXTURE = 'assets\/raid_round_button_texture\.webp'/);
  assert.match(builder, /__RAID_ROUND_BUTTON_TEXTURE__/);
  assert.match(builder, /CONTINUE_TEXTURE = 'assets\/raid_continue_texture\.webp'/);
  assert.match(builder, /__RAID_CONTINUE_TEXTURE__/);
  assert.match(css, /--raid-continue-texture: url\('__RAID_CONTINUE_TEXTURE__'\)/);
  const continueButton = readFileSync(new URL('./assets/raid_continue_texture.webp', import.meta.url));
  assert.equal(continueButton.subarray(0, 4).toString('ascii'), 'RIFF');
  assert.equal(continueButton.subarray(8, 12).toString('ascii'), 'WEBP');
  const roundButton = readFileSync(new URL('./assets/raid_round_button_texture.webp', import.meta.url));
  assert.equal(roundButton.subarray(0, 4).toString('ascii'), 'RIFF');
  assert.equal(roundButton.subarray(8, 12).toString('ascii'), 'WEBP');
  const launch = readFileSync(new URL('./assets/raid_launch_texture.webp', import.meta.url));
  assert.equal(launch.subarray(0, 4).toString('ascii'), 'RIFF');
  assert.equal(launch.subarray(8, 12).toString('ascii'), 'WEBP');
  assert.match(css, /--raid-launch-texture: url\('__RAID_LAUNCH_TEXTURE__'\)/);
  assert.match(css, /--raid-round-button-texture: url\('__RAID_ROUND_BUTTON_TEXTURE__'\)/);
});

test('mech detail uses round pilot/mech medallions and keeps the textured launch button inset', () => {
  const js = readFileSync(new URL('./raid_magitech.js', import.meta.url), 'utf8');
  const css = readFileSync(new URL('./raid_magitech.css', import.meta.url), 'utf8');
  assert.match(js, /mech-garage-icon-button \$\{kind\}/);
  assert.match(js, /if \(kind === 'mech'\)[\s\S]*?mech-garage-icon-art[\s\S]*?mech_\$\{cls\.id\}\.png/);
  assert.match(js, /caption\.textContent = text/);
  assert.match(css, /\.mech-garage-side-actions \.mech-garage-icon-button[\s\S]*?border-radius:50%/);
  assert.match(css, /\.mech-garage-icon-button\.pilot \{ background:/);
  assert.match(css, /\.mech-garage-icon-art \{[\s\S]*?width:270%; height:270%/);
  assert.match(css, /\.mech-detail-body\.mech-garage-home > \.mech-go-button \{[\s\S]*?left:21%; bottom:16%; width:58%/);
  assert.match(css, /\.mech-detail-body\.mech-garage-home > \.mech-go-button::before/);
  assert.match(css, /background:var\(--raid-launch-texture\) center \/ 100% 100% no-repeat/);
  assert.match(css, /\.mech-detail-body\.mech-garage-home > \.mech-go-button:hover:not\(:disabled\),[\s\S]*?:focus-visible \{[\s\S]*?background:var\(--raid-launch-texture\) center \/ 100% 100% no-repeat/);
  assert.match(css, /\.mech-detail-actions\.mech-garage-side-actions \{ top:27%; left:4%; width:25%; gap:5px !important; \}/);
  assert.match(css, /\.mech-garage-side-actions \.mech-garage-icon-button \{ width:52px; height:52px; \}/);
});

test('all circular garage and region controls use the generated fire medallion with underglow', () => {
  const css = readFileSync(new URL('./raid_magitech.css', import.meta.url), 'utf8');
  const builder = readFileSync(new URL('./build_raid_magitech.py', import.meta.url), 'utf8');
  assert.match(css, /\.picker-tool-icon \{[\s\S]*?background:var\(--raid-round-button-texture\) center \/ 100% 100% no-repeat/);
  assert.match(css, /\.mech-garage-side-actions \.mech-garage-icon-button \{[\s\S]*?background:var\(--raid-round-button-texture\) center \/ 100% 100% no-repeat[\s\S]*?box-shadow:0 0 18px/);
  assert.match(css, /\.region-emblem-launch \{[\s\S]*?background:var\(--raid-round-button-texture\) center \/ 100% 100% no-repeat;[\s\S]*?drop-shadow\(0 0 12px/);
  assert.match(builder, /ROUND_BUTTON_TEXTURE = 'assets\/raid_round_button_texture\.webp'/);
});

test('uploaded intro music is embedded, persisted across screens and available in combat', () => {
  const html = readFileSync(new URL('./raid_magitech.html', import.meta.url), 'utf8');
  const js = readFileSync(new URL('./raid_magitech.js', import.meta.url), 'utf8');
  const css = readFileSync(new URL('./raid_magitech.css', import.meta.url), 'utf8');
  const builder = readFileSync(new URL('./build_raid_magitech.py', import.meta.url), 'utf8');
  const audio = readFileSync(new URL('./intro_music.mp3', import.meta.url));
  assert.equal(audio.subarray(0, 3).toString('ascii'), 'ID3');
  assert.match(html, /<audio id="raid-intro-music" src="__RAID_INTRO_MUSIC__" preload="none" loop/);
  assert.match(html, /id="raid-music-toggle"[^>]*aria-label="Выключить музыку в меню"/);
  assert.match(html, /class="music-wave"/);
  assert.match(html, /class="music-mute"/);
  assert.match(html, /id="music-on" checked aria-label="Музыка в бою" role="switch"/);
  assert.match(builder, /INTRO_MUSIC = 'intro_music\.mp3'/);
  assert.match(builder, /data:audio\/mpeg;base64,/);
  assert.match(js, /clip\.currentTime = 0[\s\S]*?clip\.currentTime >= 1\.25/);
  const swordTaken = readFileSync(new URL('./assets/swordtaken.mp3', import.meta.url));
  assert.ok(swordTaken.length < 61692, 'swordtaken source should have its leading segment trimmed');
  assert.match(js, /function isMenuMusicContext\(\) \{ return !!el\('class-picker'\)\?\.classList\.contains\('show'\); \}/);
  assert.match(js, /introMusicAppVisible\(\)[\s\S]*?audioPageActive && audioHostActive/);
  assert.match(js, /el\('music-on'\)\?\.addEventListener\('change',[\s\S]*?setMusicPreference\(event\.target\.checked, false\)/);
  assert.match(js, /MENU_MUSIC_KEY = 'raid\.magitech\.menuMusic\.v1'/);
  assert.match(js, /BATTLE_MUSIC_KEY = 'raid\.magitech\.battleMusic\.v1'/);
  assert.match(js, /function activeMusicEnabled\(\) \{ return isMenuMusicContext\(\) \? menuMusicEnabled : battleMusicEnabled; \}/);
  assert.match(js, /if \(!activeMusicEnabled\(\) \|\| \(!introMusicGesture && !allowAutoplayAttempt\) \|\| !introMusicAppVisible\(\)\)[\s\S]*?track\.pause\(\)/);
  assert.match(js, /syncIntroMusic\(true\)/);
  const newGameBody = js.slice(js.indexOf('  function newGame(seed)'), js.indexOf('  function isSelectable('));
  assert.doesNotMatch(newGameBody, /stopIntroMusic/);
  const resumeBody = js.slice(js.indexOf('  function resumeSavedRun()'), js.indexOf('  // --- local records'));
  assert.doesNotMatch(resumeBody, /stopIntroMusic/);
  assert.match(js, /'activated',[\s\S]*?syncIntroMusic\(\)/);
  assert.match(css, /\.raid-music-toggle \{[\s\S]*?background:var\(--raid-round-button-texture\)/);
});
