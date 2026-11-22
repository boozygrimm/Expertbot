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
function raidPickLevel(elements) {
  const cards = elements.get('modal-body').querySelectorAll('.lv-card');
  assert.equal(cards.length, 4);
  const skills = cards.filter(card => card.className.includes('lv-skill'));
  const picks = skills.length >= 2 ? skills : cards;
  picks[0].click(); picks[1].click();
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
      const trophy = raidDraw(flushed, 20, 30).seed;
      if (raidDraw(trophy, 0, 100).value === roll) { seed = candidate; break; }
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
  assert.equal(s.equipment[2].mods.maxArmor || 0, 0);
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
  for (let n = 0; n < 200; n++) for (const slot of ['shield', 'accessory']) {
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
  for (let n = 0; n < 100; n++) for (const slot of ['shield', 'accessory']) {
    const offer = raid.makeUpgradeOffer(slot);
    if (keys.includes(offer.key)) eligible.add(offer.key);
  }
  assert.equal(eligible.size, 3, 'the step from +1.75 to +2 remains eligible');
  for (const key of keys) s[key] = 4;
  for (let n = 0; n < 100; n++) for (const slot of ['shield', 'accessory']) {
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
