const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const net = require('node:net');
const { JSDOM, VirtualConsole } = require('jsdom');

const root = require('node:path').resolve(__dirname, '..');
const python = process.env.MINIAPP_PYTHON || 'python3';
let server, base;

async function freePort() {
  const socket = net.createServer();
  await new Promise((resolve, reject) => { socket.once('error', reject); socket.listen(0, '127.0.0.1', resolve); });
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  return port;
}
async function until(predicate, timeout = 7000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  throw new Error('Condition not met before timeout');
}
before(async () => {
  const port = await freePort();
  base = `http://127.0.0.1:${port}`;
  server = spawn(python, ['-m', 'miniapp_backend.sandbox', '--host', '127.0.0.1', '--port', String(port)], { cwd: root, stdio: 'ignore' });
  await until(async () => { try { return (await fetch(base + '/')).ok; } catch { return false; } });
});
after(() => { if (server) server.kill(); });

async function openApp(intercept) {
  const requests = [];
  const errors = [];
  const console = new VirtualConsole();
  console.on('jsdomError', error => errors.push(error.message));
  const dom = await JSDOM.fromURL(base + '/', {
    resources: 'usable', runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: console,
    beforeParse(window) {
      window.scrollTo = () => {};
      window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
      window.HTMLDialogElement.prototype.close = function () { this.open = false; };
      window.fetch = async (path, options = {}) => {
        requests.push({ path, method: options.method || 'GET' });
        if (intercept && path === '/api/alchemy/me') return intercept(path, options);
        return fetch(new URL(path, base), options);
      };
    },
  });
  await until(() => dom.window.document.querySelector('#refresh-button')?.hidden === false);
  return { dom, requests, errors };
}

test('sandbox frontend loads a signed profile through the real HTTP API and navigates', async () => {
  const { dom, requests, errors } = await openApp();
  const doc = dom.window.document;
  try {
    await until(() => doc.querySelector('#profile-status').textContent.includes('загружен'));
    assert.match(doc.querySelector('#demo-banner').textContent, /ВЫМЫШЛЕННЫЙ ПРОФИЛЬ/);
    doc.querySelector('[data-action="enter"]').click();
    assert.equal(doc.querySelector('#park-page').hidden, true);
    assert.equal(doc.querySelector('#lab-page').hidden, false);
    doc.querySelector('[data-area="inventory"]').click();
    assert.match(doc.querySelector('#inventory-grid').textContent, /Вода/);
    assert.match(doc.querySelector('#inventory-grid').textContent, /12/);
    doc.querySelector('.dialog-close').click();
    doc.querySelector('[data-area="laboratory"]').click();
    doc.querySelector('[data-action="open-recipes"]').click();
    assert.match(doc.querySelector('#recipes-grid').textContent, /Пар/);
    doc.querySelector('.dialog-close').click();
    doc.querySelector('[data-area="fighters"]').click();
    assert.match(doc.querySelector('#fighters-grid').textContent, /Искра/);
    assert.deepEqual(requests.map(r => r.path), ['/api/dev/session', '/api/alchemy/me']);
    assert.ok(requests.every(r => r.method === 'GET'));
    assert.deepEqual(errors, []);
  } finally { dom.window.close(); }
});

test('sandbox craft updates inventory through the server without touching a real account', async () => {
  const { dom, requests } = await openApp();
  const doc = dom.window.document;
  try {
    await until(() => doc.querySelector('#profile-status').textContent.includes('загружен'));
    doc.querySelector('[data-action="enter"]').click();
    doc.querySelector('[data-area="laboratory"]').click();
    assert.equal(doc.querySelector('#craft-panel').hidden, false);
    doc.querySelector('#craft-first').value = 'w';
    doc.querySelector('#craft-second').value = 'f';
    doc.querySelector('[data-action="craft"]').click();
    await until(() => doc.querySelector('#craft-message').textContent.includes('Получено:'));
    assert.match(doc.querySelector('#craft-message').textContent, /Пар/);
    assert.ok(requests.some(r => r.path === '/api/dev/craft' && r.method === 'POST'));
    doc.querySelector('.dialog-close').click();
    doc.querySelector('[data-area="inventory"]').click();
    const text = doc.querySelector('#inventory-grid').textContent;
    assert.match(text, /11Вода/);
    assert.match(text, /7Огонь/);
  } finally { dom.window.close(); }
});

test('production controls remove, start and simulate one hour on the fake profile', async () => {
  const { dom, requests, errors } = await openApp();
  const doc = dom.window.document;
  try {
    await until(() => doc.querySelector('#profile-status').textContent.includes('загружен'));
    doc.querySelector('[data-action="enter"]').click();
    doc.querySelector('[data-area="production"]').click();
    assert.equal(doc.querySelector('#production-panel').hidden, false);
    assert.match(doc.querySelector('#production-summary').textContent, /2 \/ 2/);
    assert.equal(doc.querySelector('[data-action="production-start"]').disabled, true);
    const remove = [...doc.querySelectorAll('[data-action="production-remove"]')].find(x => x.textContent.includes('Собрать'));
    remove.click();
    await until(() => doc.querySelector('#production-message').textContent.includes('разобрана'));
    assert.match(doc.querySelector('#production-summary').textContent, /1 \/ 2/);
    doc.querySelector('#production-station').value = 'farm';
    doc.querySelector('#production-worker').value = 'spirit_w';
    doc.querySelector('[data-action="production-start"]').click();
    await until(() => doc.querySelector('#production-message').textContent.includes('запущена'));
    assert.match(doc.querySelector('#dialog-content').textContent, /Ферма/);
    doc.querySelector('[data-action="production-advance"]').click();
    await until(() => doc.querySelector('#production-message').textContent.includes('час прошёл'));
    assert.match(doc.querySelector('#production-summary').textContent, /время: 1 ч/);
    assert.match(doc.querySelector('#dialog-content').textContent, /добыто 3/);
    assert.equal(requests.filter(r => r.path === '/api/dev/production' && r.method === 'POST').length, 3);
    assert.deepEqual(errors, []);
  } finally { dom.window.close(); }
});

test('sandbox exposes scene assets, profile-driven buildings and animated expedition statuses', async () => {
  for (const image of ['base_scene.jpg', 'expedition_scene.jpg']) {
    const response = await fetch(base + '/assets/' + image);
    assert.equal(response.status, 200);
    assert.match(response.headers.get('content-type'), /^image\/jpeg/);
  }
  const { dom, errors } = await openApp(); const doc = dom.window.document;
  try {
    await until(() => doc.querySelector('#profile-status').textContent.includes('загружен'));
    doc.querySelector('[data-action="enter"]').click();
    doc.querySelector('[data-area="buildings"]').click();
    assert.equal(doc.querySelector('#base-visual').hidden, false);
    doc.querySelector('[data-building="storage"]').click();
    assert.match(doc.querySelector('#building-detail').textContent, /Склад.*уровень 1/);
    doc.querySelector('.dialog-close').click();
    doc.querySelector('[data-area="expeditions"]').click();
    assert.equal(doc.querySelector('#expedition-visual').hidden, false);
    assert.equal(doc.querySelectorAll('.expedition-party').length, 2);
    assert.deepEqual(errors, []);
  } finally { dom.window.close(); }
});

test('failed API does not silently display demo profile and can retry', async () => {
  let attempts = 0;
  const { dom } = await openApp((path, options) => {
    attempts++;
    return attempts === 1 ? new Response('{}', {status:503}) : fetch(base + path, options);
  });
  const doc = dom.window.document;
  try {
    await until(() => doc.querySelector('#profile-status').textContent.includes('Не удалось'));
    doc.querySelector('[data-action="enter"]').click();
    doc.querySelector('[data-area="inventory"]').click();
    assert.match(doc.querySelector('#dialog-content').textContent, /Профиль недоступен/);
    assert.doesNotMatch(doc.querySelector('#dialog-content').textContent, /Вода/);
    doc.querySelector('.dialog-close').click();
    doc.querySelector('[data-action="refresh"]').click();
    await until(() => attempts === 2 && doc.querySelector('#profile-status').textContent.includes('загружен'));
    doc.querySelector('[data-area="inventory"]').click();
    assert.match(doc.querySelector('#inventory-grid').textContent, /Вода/);
  } finally { dom.window.close(); }
});

test('server-provided names are treated as text, not executable markup', async () => {
  const profile = {inventory:[{name:'<img src=x onerror=alert(1)>',quantity:1}], buildings:{}, discovered_count:0, recipes:[], expeditions:{}, production:[], fighters:[]};
  const { dom } = await openApp(() => new Response(JSON.stringify({profile}), {status:200, headers:{'Content-Type':'application/json'}}));
  const doc = dom.window.document;
  try {
    await until(() => doc.querySelector('#profile-status').textContent.includes('загружен'));
    doc.querySelector('[data-area="inventory"]').click();
    assert.match(doc.querySelector('#inventory-grid').textContent, /<img src=x/);
    assert.equal(doc.querySelector('#inventory-grid img'), null);
  } finally { dom.window.close(); }
});

// Use the GENERATED deliverable, including real CSS. Source-only DOM doubles
// cannot detect display:grid overriding the browser's built-in [hidden] rule.
async function openRaid({ saved = {}, start = true, setup, url = 'https://raid.test/', variant = 'classic' } = {}) {
  const errors = [], console = new VirtualConsole();
  console.on('jsdomError', error => errors.push(error.message));
  const artifact = variant === 'magitech' ? 'lunapark-raid-magitech.html' : 'lunapark-raid.html';
  const html = require('node:fs').readFileSync(require('node:path').join(root, artifact), 'utf8');
  const dom = new JSDOM(html, {
    url, runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: console,
    beforeParse(window) {
      for (const [key, value] of Object.entries(saved)) window.localStorage.setItem(key, value);
      if (setup) setup(window);
    },
  });
  await until(() => dom.window.document.querySelector('.picker-card-btn:not(.locked)'));
  const raid = dom.window.RAID;
  if (start) {
    dom.window.document.querySelector('.picker-card-btn:not(.locked)').click();
    raid.newGame(0);
    raid.state.board = Array.from({ length: 36 }, () => ({ type: 'sword' }));
  }
  return { dom, raid, errors };
}

for (const kind of ['gold', 'armor']) test(`built raid ${kind}: character/equipment summary really hides choices and never rerolls them`, async () => {
  const { dom, raid, errors } = await openRaid();
  const doc = dom.window.document;
  try {
    if (kind === 'gold') { raid.state.hero.gold = 100; raid.checkGoldBox(); }
    else { raid.state.upgradeBar = 100; raid.checkUpgradeBar(); }
    const grid = doc.querySelector(kind === 'gold' ? '.shop-grid' : '.lv-grid');
    const summary = doc.querySelector('#modal-body .character-summary');
    const toggle = doc.querySelector('.shop-summary-toggle');
    assert.ok(summary && toggle, 'both reward menus expose worn gear, not just generic bonuses');
    assert.equal(dom.window.getComputedStyle(summary).display, 'none');
    assert.notEqual(dom.window.getComputedStyle(grid).display, 'none');
    const before = JSON.stringify([raid.state.hero, raid.state.equipment, raid.state.rngSeeds, raid.state.menuCandidates]);
    toggle.click();
    assert.equal(toggle.getAttribute('aria-expanded'), 'true');
    assert.equal(dom.window.getComputedStyle(grid).display, 'none', 'hidden grid must not remain rendered/clickable');
    assert.notEqual(dom.window.getComputedStyle(summary).display, 'none');
    for (const item of raid.state.equipment) assert.ok(summary.textContent.includes(item.name));
    toggle.click();
    assert.equal(toggle.getAttribute('aria-expanded'), 'false');
    assert.notEqual(dom.window.getComputedStyle(grid).display, 'none');
    assert.equal(dom.window.getComputedStyle(summary).display, 'none');
    assert.equal(JSON.stringify([raid.state.hero, raid.state.equipment, raid.state.rngSeeds, raid.state.menuCandidates]), before);
    assert.deepEqual(errors, []);
  } finally { dom.window.close(); }
});

test('built raid shop/upgrade cards show item-local before/after and clicks change only the chosen gear', async () => {
  const { dom, raid, errors } = await openRaid();
  const doc = dom.window.document;
  try {
    const weapon = raid.state.equipment.find(item => item.slot === 'weapon');
    const shield = raid.state.equipment.find(item => item.slot === 'shield');
    raid.equipItem({ ...weapon, mods: { weaponDamage: 3 } });
    raid.equipItem({ ...shield, mods: { maxArmor: 1, weaponDamage: 5 } });
    assert.equal(raid.state.hero.weaponDamage, 8);
    raid.state.hero.gold = 100; raid.checkGoldBox();
    const index = raid.state.menuCandidates.findIndex(item => item.slot === 'weapon');
    assert.ok(index >= 0);
    const offer = raid.state.menuCandidates[index];
    const card = doc.querySelectorAll('button.shop-item')[index];
    assert.ok(card.querySelector('.item-before').textContent.includes(weapon.name));
    assert.match(card.querySelector('.item-before').textContent, /Урон оружия \+3/);
    assert.match(card.querySelector('.item-after').textContent, /Урон оружия \+4/);
    assert.match(card.querySelector('.item-change').textContent, /Урон оружия: 3 → 4 \(\+1\)/);
    assert.doesNotMatch(card.textContent, /8 → 12/);
    card.click();
    assert.equal(raid.state.hero.weaponDamage, 9, 'five on shield plus four on new weapon, not eight plus four');
    assert.equal(raid.state.equipment.find(item => item.slot === 'weapon').name, offer.name);
    const equipped = JSON.parse(JSON.stringify(raid.state.equipment));
    raid.state.upgradeBar = 100; raid.checkUpgradeBar();
    const upgrade = raid.state.menuCandidates[0];
    const old = equipped.find(item => item.slot === upgrade.slot);
    const upgradeCard = doc.querySelector('.lv-card');
    assert.ok(upgradeCard.querySelector('.lv-name').textContent.includes(old.name));
    assert.ok(upgradeCard.querySelector('.item-before').textContent.includes(raid.itemModsText(old)));
    const after = { ...old, mods: { ...old.mods, [upgrade.key]: (old.mods[upgrade.key] || 0) + upgrade.value } };
    assert.ok(upgradeCard.querySelector('.item-after').textContent.includes(raid.itemModsText(after)));
    upgradeCard.click();
    raid.state.equipment.forEach((item, n) => {
      assert.deepEqual(JSON.parse(JSON.stringify(item)), item.slot === upgrade.slot ? after : equipped[n]);
    });
    assert.deepEqual(errors, []);
  } finally { dom.window.close(); }
});

test('built raid level choices remain editable and uncommitted until the enabled confirmation is clicked', async () => {
  const { dom, raid, errors } = await openRaid();
  const doc = dom.window.document;
  try {
    raid.state.hero.xp = 100; raid.checkLevelUp();
    const cards = [...doc.querySelectorAll('.lv-card')], confirm = doc.querySelector('.lv-confirm');
    assert.ok(confirm);
    assert.equal(confirm.textContent, 'Подтвердить');
    assert.equal(confirm.disabled, true);
    const snapshot = () => JSON.stringify([raid.state.hero, raid.state.skills, raid.state.skillLevels, raid.state.skillCooldowns, raid.state.rngSeeds]);
    const before = snapshot();
    confirm.click(); cards[0].click();
    assert.equal(confirm.disabled, true);
    cards[1].click();
    assert.equal(confirm.disabled, false);
    assert.equal(raid.state.pendingModal, 'level');
    assert.equal(snapshot(), before);
    assert.equal(cards[0].getAttribute('aria-pressed'), 'true');
    assert.equal(cards[1].getAttribute('aria-pressed'), 'true');
    assert.equal(cards[2].disabled, true);
    cards[0].click();
    assert.equal(confirm.disabled, true);
    assert.equal(cards[2].disabled, false);
    cards[2].click();
    assert.equal(snapshot(), before);
    assert.match(doc.querySelector('.lv-status').textContent, /2.*2/);
    confirm.click();
    assert.equal(raid.state.pendingModal, null);
    assert.deepEqual(Array.from(raid.state.skills), ['freeze']);
    assert.equal(raid.state.hero.stats.strength, 2);
    const after = snapshot();
    confirm.click(); cards[3].click();
    assert.equal(snapshot(), after);
    assert.deepEqual(errors, []);
  } finally { dom.window.close(); }
});

test('built raid an unconfirmed two-pick level keeps resource arrivals and enemy continuation paused', async () => {
  const { dom, raid, errors } = await openRaid();
  const doc = dom.window.document, s = raid.state;
  try {
    s.forceCoins = 1; s.hero.hp = 10; s.hero.xp = 99; s.hero.xpLevelCum = 99;
    s.hero.gold = 99; s.goldEarned = 99;
    const skull = Object.assign(raid.makeSkull(false, false, 0), { hp: 1, maxHp: 1, def: 0, atk: 0, enteredTurn: -1 });
    s.board[0] = { type: 'skull', skull }; s.board[1] = { type: 'chain' };
    s.board[2] = { type: 'coin' }; s.board[3] = { type: 'chain' }; s.board[4] = { type: 'potion' };
    for (const slot of [0, 1, 2, 3, 4]) raid.tryExtendChain(slot);
    raid.resolveChain();
    await until(() => s.pendingModal === 'level');
    const cards = doc.querySelectorAll('.lv-card'); cards[0].click(); cards[1].click();
    await new Promise(resolve => setTimeout(resolve, 700));
    assert.equal(s.pendingModal, 'level');
    assert.deepEqual([s.turn, s.hero.xp, s.hero.gold, s.hero.hp, s.arrivals.length, s.resolving], [0, 1, 99, 10, 2, true]);
    const confirm = doc.querySelector('.lv-confirm'); confirm.click();
    await until(() => s.pendingModal === 'gold');
    assert.equal(s.hero.gold, 1);
    doc.querySelector('button.shop-item').click();
    await until(() => s.turn === 1 && !s.resolving);
    assert.deepEqual([s.pendingModal, s.arrivals.length, s.hero.xp, s.hero.gold], [null, 0, 1, 1]);
    confirm.click();
    assert.equal(s.turn, 1);
    assert.deepEqual(errors, []);
  } finally { dom.window.close(); }
});

test('built raid a shop trade-off has red/green deltas, while upgrading the bought item has no losses', async () => {
  const { dom, raid, errors } = await openRaid();
  const doc = dom.window.document;
  try {
    const weapon = raid.state.equipment.find(item => item.slot === 'weapon');
    raid.equipItem({ ...weapon, mods: { weaponDamage: 2, baseDamage: 2 } });
    raid.state.hero.gold = 100; raid.checkGoldBox();
    const card = doc.querySelector('button.shop-item[data-slot="weapon"]');
    assert.match(card.querySelector('.item-change.better').textContent, /Урон оружия: 2 → 4 \(\+2\)/);
    assert.match(card.querySelector('.item-change.worse').textContent, /Базовый урон: 2 → 1 \(−1\)/);
    card.click();
    assert.deepEqual([raid.state.hero.weaponDamage, raid.baseDamage()], [4, 4]);
    raid.state.upgradeBar = 100; raid.checkUpgradeBar();
    const upgrade = doc.querySelector('.lv-card[data-slot="weapon"]');
    assert.equal(doc.querySelectorAll('.item-change.worse').length, 0);
    assert.match(upgrade.querySelector('.item-after').textContent, /Урон оружия \+5.*Базовый урон \+1/s);
    upgrade.click();
    assert.deepEqual([raid.state.hero.weaponDamage, raid.baseDamage()], [5, 4]);
    assert.deepEqual(errors, []);
  } finally { dom.window.close(); }
});

const classIds = ['adventurer','ranger','raider','priest','barbarian','mage','rogue','paladin','warrior','assassin'];
function growClass(raid, id, level = 10) { raid.unlockClass(id); while (raid.classLevelOf(id) < level) raid.levelUpClass(id); }
function editClass(doc, raid, id) {
  raid.showClassPicker(); doc.querySelector(`.class-config-btn[data-class-id="${id}"]`).click();
  assert.equal(doc.querySelector('#class-editor').hidden, false);
}
function editField(doc, kind, value, slot) {
  const node = doc.querySelector(`#class-editor select[data-kind="${kind}"]` + (slot == null ? '' : `[data-slot="${slot}"]`));
  node.value = value; node.dispatchEvent(new doc.defaultView.Event('change', { bubbles: true })); return node;
}

test('built raid class editor previews locked/Adventurer classes without changing profile, state or RNG', async () => {
  const {dom,raid,errors}=await openRaid();const doc=dom.window.document;
  try {
    const before=JSON.stringify(raid.state), profile=JSON.stringify({...dom.window.localStorage});
    for(const id of ['mage','adventurer']) {
      editClass(doc,raid,id);
      assert.equal(doc.querySelector('#class-editor-save').hidden,true);
      assert.equal(doc.querySelector('#class-picker-main').hidden,true);
      assert.equal(dom.window.getComputedStyle(doc.querySelector('#class-picker-main')).display,'none');
      assert.ok([...doc.querySelectorAll('#class-editor select')].every(n=>n.disabled));
      doc.querySelector('#class-editor-back').click();
    }
    assert.equal(JSON.stringify(raid.state),before);assert.equal(JSON.stringify({...dom.window.localStorage}),profile);
    assert.deepEqual(errors,[]);
  }finally{dom.window.close()}
});

test('built raid cancel discards drafts, save persists only next-run setup and resume keeps the live run unchanged', async () => {
  const {dom,raid,errors}=await openRaid();const doc=dom.window.document;
  try {
    for(const id of classIds)growClass(raid,id);
    raid.setSelectedClassId('barbarian');raid.newGame(913);
    const before=JSON.stringify(raid.state), oldConfig=JSON.stringify(raid.getClassConfig('barbarian'));
    function draft(){
      editField(doc,'race','Faun');editField(doc,'perks','Tough',1);editField(doc,'flaws','Frail',1);editField(doc,'replacements','steal',1);
      assert.match(doc.querySelector('#class-replacement-count').textContent,/1 из 10/);
    }
    editClass(doc,raid,'barbarian');draft();doc.querySelector('#class-editor-back').click();
    assert.equal(JSON.stringify(raid.getClassConfig('barbarian')),oldConfig);
    editClass(doc,raid,'barbarian');draft();doc.querySelector('#class-editor-save').click();
    assert.equal(doc.querySelector('#class-editor').hidden,true);
    assert.equal(JSON.stringify(raid.state),before);
    assert.equal(raid.getClassConfig('barbarian').race,'Faun');
    const saved=JSON.parse(dom.window.localStorage.getItem('raid:classConfigs'));
    assert.equal(saved.version,1);assert.equal(saved.classes.barbarian.perks[1],'Tough');
    doc.querySelector('#picker-resume').click();
    assert.equal(doc.querySelector('#class-picker').classList.contains('show'),false);
    assert.equal(JSON.stringify(raid.state),before);
    raid.newGame(913);
    assert.equal(raid.clsRace(),'Faun');assert.equal(raid.hasPerk('Tough'),true);
    assert.ok(raid.classSkillPool().includes('steal'));assert.ok(!raid.classSkillPool().includes('repair'));
    assert.deepEqual(errors,[]);
  }finally{dom.window.close()}
});

test('built raid editor enforces replacement budget, fixed first skill, class milestone and paired traits in native controls', async () => {
  const {dom,raid,errors}=await openRaid();const doc=dom.window.document;
  try {
    growClass(raid,'barbarian',2);raid.unlockClass('mage');editClass(doc,raid,'barbarian');
    const field=(kind,slot)=>doc.querySelector(`select[data-kind="${kind}"][data-slot="${slot}"]`);
    assert.equal(doc.querySelector('select[data-kind="race"]').disabled,true);
    assert.equal(field('perks',1).disabled,true);assert.equal(field('replacements',0).disabled,true);
    editField(doc,'replacements','steal',1);editField(doc,'replacements','freeze',2);
    const third=field('replacements',3);
    assert.equal([...third.options].find(n=>n.value==='heal').disabled,true);
    editField(doc,'replacements','',1);
    assert.equal([...third.options].find(n=>n.value==='heal').disabled,false);
    doc.querySelector('#class-editor-save').click();
    growClass(raid,'barbarian',3);editClass(doc,raid,'barbarian');
    assert.equal(field('perks',1).disabled,false);assert.equal(field('perks',2).disabled,true);
    assert.equal([...field('perks',1).options].find(n=>n.value==='Strong').disabled,true);
    editField(doc,'perks','Gifted',1);assert.equal(doc.querySelector('#class-editor-save').disabled,true);
    assert.match(doc.querySelector('#class-editor-status').textContent,/изъян/);
    editField(doc,'flaws','Frail',1);assert.equal(doc.querySelector('#class-editor-save').disabled,false);
    doc.querySelector('#class-editor-save').click();
    assert.equal(raid.getClassConfig('barbarian').perks[1],'Gifted');assert.deepEqual(errors,[]);
  }finally{dom.window.close()}
});

test('built raid reset is only a draft; old select/save handlers and detached picker cards cannot change a restarted run', async () => {
  const {dom,raid,errors}=await openRaid();const doc=dom.window.document;
  try {
    for(const id of classIds)growClass(raid,id);
    editClass(doc,raid,'barbarian');editField(doc,'race','Faun');doc.querySelector('#class-editor-save').click();
    editClass(doc,raid,'barbarian');doc.querySelector('#class-editor-reset').click();
    assert.equal(doc.querySelector('select[data-kind="race"]').value,'Orc');
    assert.equal(raid.getClassConfig('barbarian').race,'Faun');doc.querySelector('#class-editor-back').click();
    const oldCard=doc.querySelector('.picker-card-btn[data-class-id="mage"]');
    editClass(doc,raid,'barbarian');const oldSelect=doc.querySelector('select[data-kind="race"]'), oldSave=doc.querySelector('#class-editor-save').onclick;
    raid.newGame(71);const before=JSON.stringify(raid.state), profile=dom.window.localStorage.getItem('raid:classConfigs');
    oldSelect.value='Human';oldSelect.dispatchEvent(new dom.window.Event('change'));oldSave();
    assert.equal(JSON.stringify(raid.state),before);assert.equal(dom.window.localStorage.getItem('raid:classConfigs'),profile);
    raid.showClassPicker();oldCard.click();
    assert.equal(JSON.stringify(raid.state),before,'detached start button must be stale after reopening the picker');
    assert.deepEqual(errors,[]);
  }finally{dom.window.close()}
});

test('built raid editor reports a blocked local store without pretending that a configuration was saved', async () => {
  const {dom,raid,errors}=await openRaid();const doc=dom.window.document;
  try {
    for(const id of classIds)growClass(raid,id);
    editClass(doc,raid,'barbarian');editField(doc,'race','Faun');
    const original=dom.window.Storage.prototype.setItem;
    dom.window.Storage.prototype.setItem=function(key,value){if(key==='raid:classConfigs')throw new Error('quota');return original.call(this,key,value)};
    doc.querySelector('#class-editor-save').click();
    assert.match(doc.querySelector('#class-editor-status').textContent,/Не удалось сохранить/);
    assert.equal(doc.querySelector('#class-editor').hidden,false);assert.equal(raid.getClassConfig('barbarian').race,'Orc');
    dom.window.Storage.prototype.setItem=original;doc.querySelector('#class-editor-save').click();
    assert.equal(raid.getClassConfig('barbarian').race,'Faun');assert.deepEqual(errors,[]);
  }finally{dom.window.close()}
});

test('built raid I1 non-exempt scroll is attached and purchased through real DOM without occupying a slot', async () => {
  const {dom,raid,errors}=await openRaid(); const doc=dom.window.document;
  try {
    raid.newGame(10); raid.state.board=Array.from({length:36},()=>({type:'sword'}));
    raid.state.hero.gold=100; raid.checkGoldBox();
    const index=raid.state.menuCandidates.findIndex(item=>item.scroll);
    assert.equal(index,0); assert.equal(raid.state.menuCandidates[index].scroll.id,'trap');
    const card=doc.querySelectorAll('button.shop-item')[index];
    assert.match(card.querySelector('.shop-scroll').textContent,/Ловушка.*разово при покупке/);
    assert.equal(doc.querySelector('.shop-compatibility'),null);
    const before=JSON.stringify([raid.state.skills,raid.state.skillCooldowns,raid.state.skillLevels]);
    card.click(); assert.equal(raid.state.oneHealthSkulls,1);
    assert.equal(JSON.stringify([raid.state.skills,raid.state.skillCooldowns,raid.state.skillLevels]),before);
    assert.equal(raid.state.pendingModal,null); assert.equal(raid.state.turn,0);
    const after=JSON.stringify(raid.state); card.click(); assert.equal(JSON.stringify(raid.state),after);
    assert.deepEqual(errors,[]);
  } finally { dom.window.close(); }
});

test('built raid I1 non-exempt Enchant opens the nested item upgrade and releases its continuation once', async () => {
  const {dom,raid,errors}=await openRaid(); const doc=dom.window.document;
  try {
    raid.newGame(25); raid.state.board=Array.from({length:36},()=>({type:'sword'}));
    raid.state.hero.gold=100; raid.checkGoldBox();
    const index=raid.state.menuCandidates.findIndex(item=>item.scroll?.id==='enchant');
    assert.ok(index>=0); doc.querySelectorAll('button.shop-item')[index].click();
    assert.equal(raid.state.pendingModal,'armor'); assert.equal(doc.querySelectorAll('.lv-card').length,4);
    doc.querySelector('.lv-card').click(); await until(()=>!raid.state.resolving);
    assert.equal(raid.state.pendingModal,null); assert.equal(raid.state.turn,0);
    assert.equal(raid.state.skills.length,0); assert.deepEqual(errors,[]);
  } finally { dom.window.close(); }
});

test('built raid I1 pointer selection persists label geometry across restart; fresh document resets its history', async () => {
  const {dom,raid,errors}=await openRaid(); const doc=dom.window.document;
  try {
    raid.state.leech=1; raid.state.hero.weaponDamage=10;
    doc.querySelector('.cell[data-idx="0"]').dispatchEvent(new dom.window.MouseEvent('pointerdown',{bubbles:true,cancelable:true}));
    assert.equal(raid.state.scrollLeechLabel.number,10); assert.equal(raid.classicScrollWord(12),1);
    raid.tryExtendChain(1); assert.equal(raid.state.scrollLeechLabel.number,20);
    assert.equal(raid.classicScrollWord(19),0);
    dom.window.dispatchEvent(new dom.window.MouseEvent('pointercancel'));
    assert.equal(raid.state.selected.length,0); await until(()=>raid.classicScrollWord(12)===0);
    raid.newGame(10); assert.equal(raid.state.scrollLeechLabel.number,20); assert.equal(raid.classicScrollWord(13),8);
    const fresh=await openRaid();
    try { assert.equal(fresh.raid.classicScrollWord(13),0); assert.equal(fresh.raid.classicScrollWord(17),0); assert.deepEqual(fresh.errors,[]); }
    finally { fresh.dom.window.close(); }
    assert.deepEqual(errors,[]);
  } finally { dom.window.close(); }
});


async function reloadRaid(previous) {
  previous.dom.window.dispatchEvent(new previous.dom.window.Event('pagehide'));
  const storage = previous.dom.window.localStorage;
  const saved = Object.fromEntries(Array.from({ length: storage.length }, (_, index) => {
    const key = storage.key(index); return [key, storage.getItem(key)];
  }));
  assert.deepEqual(previous.errors, []);
  previous.dom.window.close();
  return openRaid({ saved, start: false });
}

test('built raid saves: startup offers an explicit continuation before class cards and never overwrites it', async () => {
  let b = await openRaid();
  try {
    b.raid.state.hero.hp = 13; b.raid.state.poison = 2; assert.equal(b.raid.saveRun(), true);
    b = await reloadRaid(b);
    const { dom, raid } = b, doc = dom.window.document;
    const raw = dom.window.localStorage.getItem('raid:activeRun');
    assert.equal(raid.state, null);
    const resume = doc.querySelector('#picker-resume');
    assert.equal(resume.hidden, false); assert.match(resume.textContent, /Продолжить.*ход 0/);
    assert.ok(resume.compareDocumentPosition(doc.querySelector('#picker-grid')) & dom.window.Node.DOCUMENT_POSITION_FOLLOWING);
    doc.querySelector('#mode-select').value = 'sprint'; doc.querySelector('#difficulty-select').value = '3';
    assert.equal(dom.window.localStorage.getItem('raid:activeRun'), raw);
    resume.click();
    assert.equal(raid.state.hero.hp, 13); assert.equal(raid.state.poison, 2);
    assert.equal(raid.state.mode, 'classic'); assert.equal(raid.state.difficulty, 1);
    assert.equal(doc.querySelector('#class-picker').classList.contains('show'), false);
    assert.ok(Object.isFrozen(raid.state.classConfig)); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid saves: reload preserves the exact level draft, remaining rewards, shop and one enemy turn', async () => {
  let b = await openRaid();
  try {
    let { raid } = b; let doc = b.dom.window.document, s = raid.state;
    s.forceCoins = 1; s.hero.hp = 10; s.hero.xp = 99; s.hero.xpLevelCum = 99;
    s.hero.gold = 99; s.goldEarned = 99;
    s.board[0] = { type: 'skull', skull: Object.assign(raid.makeSkull(false), { hp: 1, atk: 0 }) };
    s.board[1] = { type: 'chain' }; s.board[2] = { type: 'coin' };
    s.board[3] = { type: 'chain' }; s.board[4] = { type: 'potion' };
    [0, 1, 2, 3, 4].forEach(index => raid.tryExtendChain(index)); raid.resolveChain();
    await until(() => s.pendingModal === 'level');
    const cards = doc.querySelectorAll('.lv-card'); cards[1].click(); cards[0].click();
    const menu = JSON.parse(JSON.stringify(s.menuCandidates)), rng = JSON.stringify(s.rngSeeds);
    b = await reloadRaid(b); raid = b.raid; doc = b.dom.window.document;
    doc.querySelector('#picker-resume').click(); s = raid.state;
    assert.equal(s.pendingModal, 'level'); assert.equal(s.arrivals.length, 2);
    assert.deepEqual(JSON.parse(JSON.stringify(s.menuCandidates)), menu); assert.equal(JSON.stringify(s.rngSeeds), rng);
    assert.deepEqual(Array.from(s.levelDraft), [1, 0]);
    assert.equal(doc.querySelectorAll('.lv-card[aria-pressed="true"]').length, 2);
    assert.equal(doc.querySelector('.lv-confirm').disabled, false); assert.equal(s.turn, 0);
    doc.querySelector('.lv-confirm').click();
    await until(() => s.pendingModal === 'gold');
    const shop = JSON.parse(JSON.stringify(s.menuCandidates)), shopRng = JSON.stringify(s.rngSeeds);
    b = await reloadRaid(b); raid = b.raid; doc = b.dom.window.document;
    doc.querySelector('#picker-resume').click(); s = raid.state;
    assert.equal(s.pendingModal, 'gold'); assert.deepEqual(JSON.parse(JSON.stringify(s.menuCandidates)), shop);
    assert.equal(JSON.stringify(s.rngSeeds), shopRng);
    const pick = s.menuCandidates.findIndex(item => !item.scroll);
    doc.querySelectorAll('button.shop-item')[pick].click(); await until(() => !s.resolving);
    assert.equal(s.turn, 1); assert.equal(s.hero.xp, 1); assert.equal(s.hero.gold, 1);
    assert.equal(s.arrivals.length, 0); assert.equal(s.pendingModal, null); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid saves: replacement requires confirmation, Escape cancels, and keyboard focus stays inside the dialog', async () => {
  let b = await openRaid();
  try {
    const { raid, dom } = b, doc = dom.window.document;
    raid.state.hero.hp = 13; raid.saveRun();
    const before = dom.window.localStorage.getItem('raid:activeRun');
    doc.querySelector('#restart').click();
    assert.ok(doc.querySelector('#restart-modal').classList.contains('show'));
    assert.equal(doc.activeElement.id, 'restart-cancel');
    doc.querySelector('#restart-confirm').focus();
    doc.activeElement.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }));
    assert.equal(doc.activeElement.id, 'restart-cancel');
    doc.activeElement.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    assert.equal(doc.querySelector('#restart-modal').classList.contains('show'), false);
    assert.equal(raid.state.hero.hp, 13); assert.equal(dom.window.localStorage.getItem('raid:activeRun'), before);
    raid.unlockClass('mage'); raid.showClassPicker();
    doc.querySelector('.picker-card-btn[data-class-id="mage"]').click();
    assert.equal(raid.getSelectedClassId(), 'adventurer'); assert.equal(raid.state.classId, 'adventurer');
    doc.querySelector('#restart-confirm').click();
    assert.equal(raid.state.classId, 'mage'); assert.equal(raid.state.turn, 0);
    assert.equal(doc.querySelector('#restart-modal').classList.contains('show'), false);
    assert.equal(raid.savedRunInfo().classId, 'mage'); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid saves: choosing a new class on a fresh page cannot silently discard its stored run', async () => {
  let b = await openRaid();
  try {
    b.raid.state.hero.hp = 13; b.raid.saveRun(); b = await reloadRaid(b);
    const doc = b.dom.window.document, raw = b.dom.window.localStorage.getItem('raid:activeRun');
    doc.querySelector('.picker-card-btn:not(.locked)').click();
    assert.equal(b.raid.state, null); assert.ok(doc.querySelector('#restart-modal').classList.contains('show'));
    doc.querySelector('#restart-cancel').click();
    assert.equal(b.dom.window.localStorage.getItem('raid:activeRun'), raw);
    doc.querySelector('#picker-resume').click(); assert.equal(b.raid.state.hero.hp, 13);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid saves: quota errors are visible while the game remains playable, and retry clears the warning', async () => {
  const b = await openRaid({ setup(window) {
    window.blockRunSave = true;
    const original = window.Storage.prototype.setItem;
    window.Storage.prototype.setItem = function(key, value) {
      if (key === 'raid:activeRun' && window.blockRunSave) throw new window.DOMException('Quota exceeded', 'QuotaExceededError');
      return original.call(this, key, value);
    };
  } });
  try {
    const doc = b.dom.window.document;
    assert.ok(b.raid.state); assert.equal(b.raid.saveStatus.state, 'unavailable');
    assert.equal(doc.querySelector('#save-status').hidden, false);
    assert.match(doc.querySelector('#save-status').textContent, /Не удалось сохранить/);
    b.dom.window.blockRunSave = false; assert.equal(b.raid.saveRun(), true);
    assert.equal(doc.querySelector('#save-status').hidden, true); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid saves: damaged storage is reported, not resumed; a new run can replace it', async () => {
  const b = await openRaid({ start: false, saved: { 'raid:activeRun': '{bad' } });
  try {
    const doc = b.dom.window.document;
    assert.equal(b.raid.state, null); assert.equal(doc.querySelector('#picker-resume').hidden, true);
    assert.match(doc.querySelector('#picker-save-status').textContent, /повреждено|несовместимой/);
    doc.querySelector('.picker-card-btn:not(.locked)').click();
    assert.ok(b.raid.state); assert.equal(b.raid.saveStatus.state, 'saved'); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid saves: restoring invalidates detached choices and the earlier close/gameover callbacks', async () => {
  const b = await openRaid(), { raid, dom } = b, doc = dom.window.document;
  try {
    raid.state.hero.xp = 100; raid.checkLevelUp();
    const oldCards = [...doc.querySelectorAll('.lv-card')], oldConfirm = doc.querySelector('.lv-confirm');
    oldCards[0].click(); oldCards[1].click();
    assert.equal(raid.resumeSavedRun(), true);
    const state = raid.state, hero = JSON.stringify(state.hero), rng = JSON.stringify(state.rngSeeds);
    oldConfirm.click(); oldCards[0].click();
    assert.equal(raid.state, state); assert.equal(state.pendingModal, 'level');
    assert.equal(JSON.stringify(state.hero), hero); assert.equal(JSON.stringify(state.rngSeeds), rng);
    doc.querySelector('.lv-confirm').click(); await until(() => !state.pendingModal);
    assert.equal(raid.saveStatus.state, 'saved'); assert.deepEqual(b.errors, []);
  } finally { dom.window.close(); }
});

function raidStorageSnapshot(dom) {
  const storage = dom.window.localStorage;
  return Object.fromEntries(Array.from({ length: storage.length }, (_, i) => { const key = storage.key(i); return [key, storage.getItem(key)]; }));
}
function endBuiltRaid(raid) {
  raid.state.board = Array.from({ length: 36 }, () => ({ type: 'sword' }));
  raid.state.hero.hp = 1; raid.state.poison = 2; raid.state.regen = 0; raid.state.lastStand = 0; raid.state.nextTurnFlags = {};
  raid.endEnemyTurn();
  assert.equal(raid.state.over, true);
}

test('built raid history: startup reader, six categories and keyboard return do not create or change a run', async () => {
  const b = await openRaid({ start: false }), doc = b.dom.window.document;
  try {
    const before = raidStorageSnapshot(b.dom), opener = doc.querySelector('#picker-history');
    assert.equal(doc.querySelector('#history-modal').hidden, true);
    opener.focus(); opener.click();
    assert.equal(b.raid.state, null); assert.equal(doc.querySelector('#history-modal').hidden, false);
    assert.equal(doc.activeElement, doc.querySelector('#history-title'));
    assert.equal(doc.querySelectorAll('#history-type option').length, 6);
    assert.match(doc.querySelector('#history-body').textContent, /пока нет завершённых/);
    doc.querySelector('#history-section').value = 'classes'; doc.querySelector('#history-section').dispatchEvent(new b.dom.window.Event('change'));
    assert.equal(doc.querySelectorAll('#history-body [data-class-id]').length, 10);
    assert.equal(b.dom.window.getComputedStyle(doc.querySelector('#history-type').parentElement).display, 'none');
    assert.match(doc.querySelector('#history-body').textContent, /Лёгкий.*Убийства.*Классика/);
    doc.querySelector('#history-title').dispatchEvent(new b.dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    assert.equal(doc.querySelector('#history-modal').hidden, true); assert.equal(doc.activeElement, opener);
    assert.deepEqual(raidStorageSnapshot(b.dom), before); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid history: real terminal path records, highlights and persists one result with independent class statistics', async () => {
  let b = await openRaid();
  try {
    let { raid, dom } = b, doc = dom.window.document;
    raid.state.hero.xpLevelCum = 103; raid.state.goldEarned = 57; raid.state.upgradeEarned = 29;
    raid.state.skills = ['freeze']; raid.state.skillLevels.freeze = 2; raid.state.skullsKilled = 7;
    const levels = dom.window.localStorage.getItem('raid:classLevels'); endBuiltRaid(raid);
    await until(() => raid.state.pendingModal === 'gameover');
    assert.match(doc.querySelector('#run-history-status').textContent, /записан/);
    const open = [...doc.querySelectorAll('#modal-body button')].find(button => button.textContent === 'Рекорды и статистика');
    open.focus(); open.click();
    assert.equal(doc.querySelectorAll('.history-record.latest').length, 1);
    assert.match(doc.querySelector('.history-record.latest').textContent, /390 очк.*Ход 1.*XP 103.*GP 57.*UP 29.*Мороз ур.2.*Последний/s);
    doc.querySelector('#history-section').value = 'classes'; doc.querySelector('#history-section').dispatchEvent(new dom.window.Event('change'));
    assert.match(doc.querySelector('[data-class-id="adventurer"].history-record').textContent, /Завершено: 1.*Убийств атаками: 7.*Обычный390/s);
    assert.equal(dom.window.localStorage.getItem('raid:classLevels'), levels);
    doc.querySelector('#history-close').click(); assert.equal(doc.activeElement, open);
    const original = dom.window.localStorage.getItem('raid:history');
    b = await reloadRaid(b); ({ raid, dom } = b); doc = dom.window.document;
    assert.equal(raid.state, null); assert.equal(doc.querySelector('#picker-resume').hidden, true);
    assert.equal(dom.window.localStorage.getItem('raid:history'), original);
    doc.querySelector('#picker-history').click(); assert.equal(doc.querySelectorAll('.history-record.latest').length, 1);
    assert.equal(raid.getLocalHistory().classes.adventurer.games, 1); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid history: menu view is read-only and cannot bypass a pending reversible level reward', async () => {
  const b = await openRaid(), { raid, dom } = b, doc = dom.window.document;
  try {
    const before = JSON.stringify(raid.state);
    doc.querySelector('#menu-btn').click(); doc.querySelector('#menu-history').click();
    assert.equal(doc.querySelector('#history-modal').hidden, false); assert.equal(JSON.stringify(raid.state), before);
    doc.querySelector('#history-close').click(); doc.querySelector('#menu-resume').click();
    raid.state.hero.xp = 100; raid.checkLevelUp(); doc.querySelector('.lv-card').click();
    const reward = JSON.stringify([raid.state.hero, raid.state.levelDraft, raid.state.menuCandidates, raid.state.rngSeeds]);
    doc.querySelector('#menu-btn').click(); doc.querySelector('#menu-history').click();
    assert.equal(doc.querySelector('#history-modal').hidden, true); assert.equal(raid.state.pendingModal, 'level');
    assert.equal(JSON.stringify([raid.state.hero, raid.state.levelDraft, raid.state.menuCandidates, raid.state.rngSeeds]), reward);
    assert.equal(dom.window.localStorage.getItem('raid:history'), null); assert.deepEqual(b.errors, []);
  } finally { dom.window.close(); }
});

test('built raid history: a released v1 Pretzel save resumes with original score and explicit missing old crossing statistics', async () => {
  let b = await openRaid();
  try {
    let { raid, dom } = b;
    dom.window.document.querySelector('#mode-select').value = 'pretzel'; raid.newGame(913);
    raid.state.pretzelScore = 19; raid.saveRun();
    const saved = raidStorageSnapshot(dom), data = JSON.parse(saved['raid:activeRun']);
    delete data.state.historyId; delete data.state.pretzelStats; saved['raid:activeRun'] = JSON.stringify(data);
    const expected = JSON.stringify([data.state.board, data.rng, data.state.classConfig]);
    dom.window.close(); b = await openRaid({ saved, start: false }); ({ raid, dom } = b);
    const doc = dom.window.document; doc.querySelector('#picker-resume').click();
    assert.equal(raid.runScore(), 19); assert.equal(raid.state.pretzelStats, null);
    assert.match(raid.state.historyId, /^v1-/); assert.equal(JSON.stringify([raid.state.board, raid.state.rngSeeds, raid.state.classConfig]), expected);
    endBuiltRaid(raid); await until(() => raid.state.pendingModal === 'gameover');
    [...doc.querySelectorAll('#modal-body button')].find(button => button.textContent === 'Рекорды и статистика').click();
    doc.querySelector('#history-type').value = '5'; doc.querySelector('#history-type').dispatchEvent(new dom.window.Event('change'));
    assert.match(doc.querySelector('.history-record').textContent, /19 очк.*Статистика пересечений недоступна.*до её добавления/s);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid history: quota is visible, leaves a terminal receipt, and retry/reload commits the profile once', async () => {
  let b = await openRaid({ setup(window) {
    window.blockHistory = true;
    const original = window.Storage.prototype.setItem;
    window.Storage.prototype.setItem = function(key, value) { if (key === 'raid:history' && window.blockHistory) throw new window.DOMException('quota', 'QuotaExceededError'); return original.call(this, key, value); };
  } });
  try {
    let { dom, raid } = b, doc = dom.window.document;
    endBuiltRaid(raid); await until(() => raid.state.pendingModal === 'gameover');
    assert.equal(raid.savedRunInfo(), null); assert.equal(dom.window.localStorage.getItem('raid:history'), null);
    assert.equal(JSON.parse(dom.window.localStorage.getItem('raid:activeRun')).status, 'finished');
    assert.match(doc.querySelector('#run-history-status').textContent, /пока не записан/);
    [...doc.querySelectorAll('#modal-body button')].find(button => button.textContent === 'Рекорды и статистика').click();
    assert.equal(doc.querySelector('#history-retry').hidden, false); doc.querySelector('#history-retry').click();
    assert.match(doc.querySelector('#history-status').textContent, /пока не записан/);
    doc.querySelector('#history-close').click();
    doc.querySelector('#modal-body button:last-child').click();
    assert.equal(doc.querySelector('#restart-modal').classList.contains('show'), true); assert.match(doc.querySelector('#restart-confirm-text').textContent, /резервную запись/);
    doc.querySelector('#restart-cancel').click();
    b = await reloadRaid(b); ({ dom, raid } = b); doc = dom.window.document;
    assert.equal(raid.state, null); assert.equal(raid.getLocalHistory().classes.adventurer.games, 1);
    assert.equal(doc.querySelector('#history-retry').hidden, true);
    const raw = dom.window.localStorage.getItem('raid:history');
    doc.querySelector('#picker-history').click(); doc.querySelector('#history-retry').click();
    assert.equal(dom.window.localStorage.getItem('raid:history'), raw); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid history: corrupt external data is not executed, shown as a valid empty profile or overwritten', async () => {
  const raw = '{"format":"lunapark-raid-history","version":2,"records":"<img src=x onerror=alert(1)>"}';
  const b = await openRaid({ start: false, saved: { 'raid:history': raw } }), doc = b.dom.window.document;
  try {
    assert.equal(doc.querySelector('#picker-history-status').hidden, false); assert.match(doc.querySelector('#picker-history-status').textContent, /повреждена|несовместима/);
    doc.querySelector('#picker-history').click();
    assert.match(doc.querySelector('#history-status').textContent, /не перезаписана/); assert.equal(doc.querySelector('#history-body').textContent, '');
    assert.equal(doc.querySelector('#history-modal img'), null); assert.equal(b.dom.window.localStorage.getItem('raid:history'), raw);
    assert.equal(b.raid.state, null); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid history: a finished tab keeps stale ownership guards when a new active slot arrives before its delayed modal', async () => {
  const b = await openRaid(), { dom, raid } = b, doc = dom.window.document;
  try {
    const peer = JSON.parse(dom.window.localStorage.getItem('raid:activeRun')); peer.state.historyId = 'run-peer-session'; peer.revision++;
    const active = JSON.stringify(peer); endBuiltRaid(raid);
    const terminal = dom.window.localStorage.getItem('raid:activeRun'), history = dom.window.localStorage.getItem('raid:history');
    dom.window.localStorage.setItem('raid:activeRun', active);
    dom.window.dispatchEvent(new dom.window.StorageEvent('storage', { key: 'raid:activeRun', oldValue: terminal, newValue: active, storageArea: dom.window.localStorage }));
    assert.equal(raid.saveStatus.state, 'conflict'); assert.equal(raid.saveRun(), false);
    await until(() => raid.state.pendingModal === 'gameover'); await new Promise(resolve => setTimeout(resolve, 50));
    assert.equal(dom.window.localStorage.getItem('raid:activeRun'), active);
    assert.equal(dom.window.localStorage.getItem('raid:history'), history); assert.equal(doc.querySelector('#picker-resume').hidden, true);
    assert.deepEqual(b.errors, []);
  } finally { dom.window.close(); }
});

// Local achievements use the built HTML, its actual buttons, storage and menus.
function builtBroken(raid, count = 3) {
  raid.state.board = Array.from({ length: 36 }, (_, slot) => ({ type: 'sword', ...(slot < count ? { broken: true } : {}) }));
  raid.state.forceCoins = 1; raid.collectIndices(Array.from({ length: count }, (_, slot) => slot)); raid.saveRun();
}
function builtAchievementFilter(b, filter) {
  const node = b.dom.window.document.querySelector('#achievement-filter'); node.value = filter; node.dispatchEvent(new b.dom.window.Event('change'));
}

test('built raid achievements: picker entry has 77 Russian cards, four filters, explicit unavailable45 and focus return without a run or writes', async () => {
  const b = await openRaid({ start: false }), doc = b.dom.window.document;
  try {
    const before = raidStorageSnapshot(b.dom), opener = doc.querySelector('#picker-achievements'); opener.focus(); opener.click();
    assert.equal(b.raid.state, null); assert.equal(doc.querySelector('#history-modal').hidden, false); assert.equal(doc.activeElement.id, 'history-title');
    assert.equal(doc.querySelector('#history-title').textContent, 'Достижения'); assert.equal(doc.querySelectorAll('.achievement-record').length, 77);
    assert.equal(doc.querySelectorAll('.achievement-record progress').length, 76); assert.equal(doc.querySelectorAll('#achievement-filter option').length, 4);
    assert.equal(doc.querySelector('#history-type').parentElement.hidden, true); assert.equal(doc.querySelector('#achievement-filter').parentElement.hidden, false);
    assert.match(doc.querySelector('.achievement-summary').textContent, /0 из 76.*77/); assert.match(doc.querySelector('[data-achievement-id="45"]').textContent, /недоступно|Недоступно/);
    assert.equal(doc.querySelector('[data-achievement-id="45"] progress'), null);
    builtAchievementFilter(b, 'unlocked'); assert.equal(doc.querySelectorAll('.achievement-record').length, 0); assert.match(doc.querySelector('#history-body').textContent, /пока нет достижений/);
    builtAchievementFilter(b, 'unavailable'); assert.equal(doc.querySelectorAll('.achievement-record').length, 1);
    builtAchievementFilter(b, 'pending'); assert.equal(doc.querySelectorAll('.achievement-record').length, 76);
    doc.querySelector('#history-close').click(); assert.equal(doc.activeElement, opener); assert.equal(doc.querySelector('#history-modal').hidden, true);
    assert.deepEqual(raidStorageSnapshot(b.dom), before); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid achievements: menu reader blocks background skills, chains and trophy taps, but preserves the running game and RNG', async () => {
  const b = await openRaid(), { raid, dom } = b, doc = dom.window.document;
  try {
    raid.state.board[35] = { type: 'trophy', appearance: 0 }; raid.saveRun();
    doc.querySelector('#menu-btn').click(); const before = JSON.stringify(raid.state), saved = raidStorageSnapshot(dom);
    const open = doc.querySelector('#menu-achievements'); open.focus(); open.click();
    raid.activateSkill('boostdmg'); raid.tryExtendChain(0); raid.resolveChain(); assert.equal(raid.claimTrophy(35), false);
    assert.equal(JSON.stringify(raid.state), before); assert.deepEqual(raidStorageSnapshot(dom), saved);
    doc.querySelector('#history-close').click(); assert.equal(doc.activeElement, open);
    doc.querySelector('#menu-resume').click(); assert.deepEqual(b.errors, []);
  } finally { dom.window.close(); }
});

test('built raid achievements: actual XP milestone notifies without stealing focus; a partial level draft cannot be bypassed by the reader', async () => {
  const b = await openRaid(), { raid, dom } = b, doc = dom.window.document;
  try {
    raid.state.hero.level = 4; raid.state.hero.xp = 98; raid.addResource({ type: 'xp', variant: 0 });
    await until(() => raid.getLocalAchievements().catalog[28].unlocked);
    const notice = doc.querySelector('#achievement-notice'); assert.equal(notice.hidden, false); assert.match(notice.textContent, /Достижение: Первые открытия/);
    assert.equal(raid.state.pendingModal, 'level'); doc.querySelector('.lv-card').click();
    const before = JSON.stringify([raid.state.hero, raid.state.levelDraft, raid.state.menuCandidates, raid.state.rngSeeds]);
    doc.querySelector('#menu-btn').click(); doc.querySelector('#menu-achievements').click(); assert.equal(doc.querySelector('#history-modal').hidden, true);
    assert.equal(JSON.stringify([raid.state.hero, raid.state.levelDraft, raid.state.menuCandidates, raid.state.rngSeeds]), before);
    doc.querySelector('#menu-resume').click(); doc.querySelectorAll('.lv-card')[1].click(); doc.querySelector('.lv-confirm').click();
    await until(() => !raid.state.pendingModal); assert.equal(raid.openAchievements(), true);
    builtAchievementFilter(b, 'unlocked'); assert.equal(doc.querySelectorAll('.achievement-record').length, 1);
    assert.equal(doc.querySelector('progress').value, 100); assert.match(doc.querySelector('progress').getAttribute('aria-label'), /100%/);
    assert.deepEqual(b.errors, []);
  } finally { dom.window.close(); }
});

test('built raid achievements: profile quota warning is visible during play; explicit retry preserves the checkpoint and adds partial progress once', async () => {
  const b = await openRaid({ setup(window) {
    window.blockAchievements = true; const set = window.Storage.prototype.setItem;
    window.Storage.prototype.setItem = function(key, value) { if (key === 'raid:history' && window.blockAchievements) throw new window.DOMException('quota', 'QuotaExceededError'); return set.call(this, key, value); };
  } }), { raid, dom } = b, doc = dom.window.document;
  try {
    builtBroken(raid); const checkpoint = dom.window.localStorage.getItem('raid:activeRun');
    assert.equal(doc.querySelector('#progress-status').hidden, false); assert.match(doc.querySelector('#progress-status').textContent, /Достижения пока не записаны/);
    assert.equal(JSON.parse(checkpoint).state.achievementRun.earned[73], 3);
    doc.querySelector('#restart').click(); assert.match(doc.querySelector('#restart-confirm-text').textContent, /достижения.*резервную запись/); doc.querySelector('#restart-cancel').click();
    doc.querySelector('#menu-btn').click(); const retryCheckpoint = dom.window.localStorage.getItem('raid:activeRun');
    doc.querySelector('#menu-achievements').click(); assert.equal(doc.querySelector('#history-retry').hidden, false);
    dom.window.blockAchievements = false; doc.querySelector('#history-retry').click();
    assert.match(doc.querySelector('[data-achievement-id="73"]').textContent, /Прогресс: 3%/); assert.equal(doc.querySelector('#history-retry').hidden, true);
    assert.equal(doc.querySelector('#progress-status').hidden, true); const raw = dom.window.localStorage.getItem('raid:history');
    doc.querySelector('#history-retry').click(); assert.equal(dom.window.localStorage.getItem('raid:history'), raw);
    assert.equal(dom.window.localStorage.getItem('raid:activeRun'), retryCheckpoint); assert.deepEqual(b.errors, []);
  } finally { dom.window.close(); }
});

test('built raid achievements: validated active backup reconciles failed profile progress on reload without resuming or replaying a turn', async () => {
  let b = await openRaid({ setup(window) {
    const set = window.Storage.prototype.setItem;
    window.Storage.prototype.setItem = function(key, value) { if (key === 'raid:history') throw new window.DOMException('quota', 'QuotaExceededError'); return set.call(this, key, value); };
  } });
  try {
    builtBroken(b.raid); const rawRun = b.dom.window.localStorage.getItem('raid:activeRun');
    for (let n = 0; n < 3; n++) {
      const snapshot = raidStorageSnapshot(b.dom); assert.deepEqual(b.errors, []); b.dom.window.close();
      b = await openRaid({ start: false, saved: snapshot }); const doc = b.dom.window.document;
      assert.equal(b.raid.state, null); assert.equal(b.raid.getLocalAchievements().catalog[72].progress, 3);
      assert.equal(b.dom.window.localStorage.getItem('raid:activeRun'), rawRun); assert.equal(doc.querySelector('#picker-resume').hidden, false);
      doc.querySelector('#picker-achievements').click(); assert.match(doc.querySelector('[data-achievement-id="73"]').textContent, /3%/);
      assert.deepEqual(b.errors, []);
    }
    b.dom.window.document.querySelector('#history-close').click(); b.dom.window.document.querySelector('#picker-resume').click();
    assert.equal(b.raid.state.turn, 0); assert.equal(b.raid.getLocalAchievements().catalog[72].progress, 3);
  } finally { b.dom.window.close(); }
});

test('built raid achievements: terminal backup recovers partial and final achievements together with one game and never resurrects the run', async () => {
  let b = await openRaid({ setup(window) {
    const set = window.Storage.prototype.setItem;
    window.Storage.prototype.setItem = function(key, value) { if (key === 'raid:history') throw new window.DOMException('quota', 'QuotaExceededError'); return set.call(this, key, value); };
  } });
  try {
    builtBroken(b.raid); endBuiltRaid(b.raid); await until(() => b.raid.state.pendingModal === 'gameover');
    const record = JSON.parse(b.dom.window.localStorage.getItem('raid:activeRun')).record;
    assert.equal(record.achievements[73], 3); assert.equal(record.achievements[36], 100); assert.equal(record.achievements[67], 100);
    [...b.dom.window.document.querySelectorAll('#modal-body button')].find(node => node.textContent === 'Достижения').click();
    assert.equal(b.dom.window.document.querySelector('#history-retry').hidden, false);
    b = await reloadRaid(b); const { raid, dom } = b, doc = dom.window.document;
    assert.equal(raid.state, null); assert.equal(doc.querySelector('#picker-resume').hidden, true);
    const profile = raid.getLocalHistory(); assert.equal(profile.classes.adventurer.games, 1); assert.equal(profile.achievements.progress[73], 3);
    assert.equal(profile.achievements.progress[36], 100); assert.equal(profile.achievements.progress[67], 100);
    const raw = dom.window.localStorage.getItem('raid:history'); b = await reloadRaid(b);
    assert.equal(b.dom.window.localStorage.getItem('raid:history'), raw); assert.equal(b.raid.resumeSavedRun(), false); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid achievements: damage identity and Last Stand survive a level-menu reload without duplicate special progress or a false finished game', async () => {
  let b = await openRaid();
  try {
    let { raid, dom } = b; raid.state.hero.xp = 98; raid.state.hero.hp = 1; raid.state.lastStand = 1; raid.state.forceCoins = 1;
    const skull = raid.makeSkull(false, true, 2); Object.assign(skull, { hp: 1, def: 0, atk: 0 }); raid.state.board[2] = { type: 'skull', skull };
    for (const slot of [0, 1, 2]) raid.tryExtendChain(slot); raid.resolveChain(); await until(() => raid.state.pendingModal === 'level');
    assert.ok(raid.state.arrivals.some(event => event.cause === 34));
    b = await reloadRaid(b); ({ raid, dom } = b); const doc = dom.window.document; doc.querySelector('#picker-resume').click();
    assert.equal(raid.state.pendingModal, 'level'); assert.equal(raid.getLocalAchievements().catalog[45].progress, 1);
    const cards = doc.querySelectorAll('.lv-card'); cards[0].click(); cards[1].click(); doc.querySelector('.lv-confirm').click();
    await until(() => !raid.state.resolving && !raid.state.pendingModal);
    assert.equal(raid.state.over, false); assert.equal(raid.state.turn, 1);
    const profile = raid.getLocalHistory(); assert.equal(profile.achievements.progress[34], 100); assert.equal(profile.achievements.progress[61], 100);
    assert.equal(profile.achievements.progress[46], 1); assert.equal(profile.achievements.progress[67] || 0, 0); assert.equal(profile.classes.adventurer.games, 0);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid achievements: incompatible achievement data is visibly rejected and cannot erase or execute alongside valid class settings', async () => {
  const profile = { format: 'lunapark-raid-history', version: 1, revision: 0, records: Array.from({ length: 6 }, () => []),
    classes: Object.fromEntries(classIds.map(id => [id, { games: 0, kills: 0, best: [0, 0, 0] }])), completed: [], last: null,
    achievements: { version: 1, progress: { 45: 100 }, runs: [] } };
  const raw = JSON.stringify(profile), b = await openRaid({ start: false, saved: { 'raid:history': raw } }), doc = b.dom.window.document;
  try {
    doc.querySelector('#picker-achievements').click(); assert.equal(doc.querySelectorAll('.achievement-record').length, 0);
    assert.match(doc.querySelector('#history-status').textContent, /повреждена|несовместима/);
    doc.querySelector('#history-close').click(); doc.querySelector('.picker-card-btn:not(.locked)').click(); builtBroken(b.raid);
    assert.equal(b.dom.window.localStorage.getItem('raid:history'), raw); assert.ok(b.raid.state); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});


test('built raid achievements: dedicated records/achievements entries open their named section instead of the last visited section', async () => {
  const b = await openRaid({ start: false }), doc = b.dom.window.document;
  try {
    for (const [opener, section] of [['picker-achievements', 'achievements'], ['picker-history', 'records'], ['picker-achievements', 'achievements']]) {
      doc.getElementById(opener).click(); assert.equal(doc.getElementById('history-section').value, section);
      assert.equal(doc.querySelectorAll('.achievement-record').length, section === 'achievements' ? 77 : 0);
      doc.getElementById('history-close').click();
    }
    doc.querySelector('.picker-card-btn:not(.locked)').click();
    for (const [opener, section] of [['menu-achievements', 'achievements'], ['menu-history', 'records']]) {
      doc.getElementById('menu-btn').click(); doc.getElementById(opener).click();
      assert.equal(doc.getElementById('history-section').value, section); doc.getElementById('history-close').click(); doc.getElementById('menu-resume').click();
    }
    endBuiltRaid(b.raid); await until(() => b.raid.state.pendingModal === 'gameover');
    const buttons = [...doc.querySelectorAll('#modal-body button')]; buttons.find(n => n.textContent === 'Достижения').click();
    assert.equal(doc.getElementById('history-section').value, 'achievements'); doc.getElementById('history-close').click();
    buttons.find(n => n.textContent === 'Рекорды и статистика').click(); assert.equal(doc.getElementById('history-section').value, 'records');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});


// Raid's small SDK-only shell is separate from the laboratory API stand above.
// No Telegram API request or signed player profile is used in these tests.
function raidTelegramStub({ version = '8.0', overrides = {} } = {}) {
  const calls = [], events = new Map(); let back;
  const tg = {
    initData: 'SYNTHETIC_UI_ONLY_NOT_AUTH', initDataUnsafe: { user: { id: 701 } }, platform: 'android', version,
    viewportStableHeight: 680, viewportHeight: 680,
    safeAreaInset: { top: 24, bottom: 18, left: 0, right: 0 },
    contentSafeAreaInset: { top: 8, bottom: 0, left: 0, right: 0 },
    ready() { calls.push('ready'); }, expand() { calls.push('expand'); },
    isVersionAtLeast(minimum) { return Number(version) >= Number(minimum); },
    disableVerticalSwipes() { calls.push('disableVerticalSwipes'); this.isVerticalSwipesEnabled = false; },
    setHeaderColor(color) { calls.push('header:' + color); },
    setBackgroundColor(color) { calls.push('background:' + color); },
    enableClosingConfirmation() { calls.push('enableClosingConfirmation'); this.isClosingConfirmationEnabled = true; },
    disableClosingConfirmation() { calls.push('disableClosingConfirmation'); this.isClosingConfirmationEnabled = false; },
    close() { calls.push('close'); },
    onEvent(name, fn) { assert.equal(events.has(name), false, 'register SDK event once'); events.set(name, fn); },
    BackButton: {
      isVisible: false,
      show() { calls.push('back:show'); this.isVisible = true; },
      hide() { calls.push('back:hide'); this.isVisible = false; },
      onClick(fn) { assert.equal(back, undefined); back = fn; },
    },
    ...overrides,
  };
  for (const name of ['CloudStorage']) Object.defineProperty(tg, name, {
    get() { throw new Error('Raid shell must not read ' + name); },
  });
  return { tg, calls, events, fire: (name, payload) => events.get(name)?.(payload), back: () => back?.() };
}
const telegramRaidUrl = 'https://raid.test/?telegram=1#raid_legacy_owner=701';
function raidStorage(window) { return Object.fromEntries(Object.entries(window.localStorage)); }
function raidPointer(window, target, type, pointerId = 1, extra = {}) {
  const event = new window.Event(type, { bubbles: true, cancelable: true });
  for (const [key, value] of Object.entries({ pointerId, isPrimary: true, button: 0, ...extra })) Object.defineProperty(event, key, { value });
  target.dispatchEvent(event);
}
function raidDrag(b, pointerId = 1) {
  const { window } = b.dom, doc = window.document;
  const cell = index => doc.querySelector(`.cell[data-idx="${index}"]`);
  raidPointer(window, cell(0), 'pointerdown', pointerId);
  for (const index of [1, 2]) {
    doc.elementFromPoint = () => cell(index);
    raidPointer(window, window, 'pointermove', pointerId);
  }
  assert.deepEqual(Array.from(b.raid.state.selected), [0, 1, 2]);
}

test('built raid Telegram is opt-in; ordinary browser stays SDK/network-free even with an injected object', async () => {
  const stub = raidTelegramStub();
  const { dom, errors } = await openRaid({ start: false, setup(window) { window.Telegram = { WebApp: stub.tg }; } });
  try {
    assert.deepEqual(stub.calls, []);
    assert.equal(dom.window.document.documentElement.classList.contains('telegram-preview'), false);
    assert.equal(dom.window.document.querySelector('script[src]'), null);
    assert.equal(dom.window.document.querySelector('#picker-telegram-status').hidden, true);
    assert.deepEqual(errors, []);
  } finally { dom.window.close(); }
});

test('built raid Telegram calls ready/expand/swipe protection once, without server auth, API, cloud or autoplay', async () => {
  const stub = raidTelegramStub(), requests = [];
  const { dom, raid, errors } = await openRaid({ url: telegramRaidUrl, start: false, setup(window) {
    window.Telegram = { WebApp: stub.tg };
    window.fetch = (...args) => { requests.push(args); throw new Error('Raid must stay local'); };
  } });
  try {
    const doc = dom.window.document;
    assert.equal(raid.state, null);
    for (const method of ['ready', 'expand', 'disableVerticalSwipes']) assert.equal(stub.calls.filter(c => c === method).length, 1);
    assert.equal(stub.tg.BackButton.isVisible, false);
    assert.equal(stub.tg.isClosingConfirmationEnabled, false);
    assert.match(doc.querySelector('#picker-telegram-status').textContent, /Telegram · android · локальный пробник/);
    assert.equal(doc.querySelector('#picker-telegram-status').hidden, false);
    assert.equal(doc.documentElement.style.getPropertyValue('--raid-tg-height'), '680px');
    assert.equal(doc.documentElement.style.getPropertyValue('--raid-tg-safe-top'), '24px');
    assert.equal(doc.documentElement.style.getPropertyValue('--raid-tg-content-top'), '8px');
    assert.equal(doc.querySelector('script[src]'), null, 'reuse loaded SDK');
    stub.fire('activated'); stub.fire('activated');
    assert.equal(raid.state, null);
    assert.deepEqual(requests, []);
    assert.doesNotMatch(JSON.stringify(raidStorage(dom.window)), /SYNTHETIC_UI_ONLY_NOT_AUTH/);
    assert.deepEqual(errors, []);
  } finally { dom.window.close(); }
});

for (const mode of ['no-data', 'old-client', 'missing-methods', 'throwing-methods']) test(`built raid Telegram ${mode} degrades visibly without breaking local game`, async () => {
  const overrides = mode === 'no-data' ? { initData: '' }
    : mode === 'missing-methods' ? { ready: undefined, expand: undefined, BackButton: undefined, onEvent: undefined, disableVerticalSwipes: undefined }
      : mode === 'throwing-methods' ? Object.fromEntries(['ready', 'expand', 'disableVerticalSwipes', 'setHeaderColor', 'setBackgroundColor', 'onEvent', 'enableClosingConfirmation', 'disableClosingConfirmation'].map(name => [name, () => { throw new Error('old SDK'); }])) : {};
  const stub = raidTelegramStub({ version: mode === 'old-client' ? '7.6' : '8.0', overrides });
  const { dom, raid, errors } = await openRaid({ url: telegramRaidUrl, setup(window) { window.Telegram = { WebApp: stub.tg }; } });
  try {
    assert.ok(raid.state && raid.state.board.length === 36);
    assert.match(dom.window.document.querySelector('#picker-telegram-status').textContent,
      mode === 'no-data' ? /Профиль Telegram не определён/ : /свайпы Telegram не отключены/);
    if (mode === 'no-data' || mode === 'old-client') assert.ok(!stub.calls.includes('disableVerticalSwipes'));
    dom.window.document.querySelector('#menu-btn').click();
    assert.equal(dom.window.document.querySelector('#menu-modal').classList.contains('show'), true);
    assert.deepEqual(errors, []);
  } finally { dom.window.close(); }
});

test('built raid Telegram SDK loader is async and bounded; errors/timeouts stay playable and late success can connect', async () => {
  for (const failure of ['error', 'timeout']) {
    const stub = raidTelegramStub(); let timeout;
    const { dom, raid, errors } = await openRaid({ url: telegramRaidUrl, setup(window) {
      const original = window.setTimeout.bind(window);
      window.setTimeout = (fn, delay, ...args) => delay === 8000 ? (timeout = fn, 0) : original(fn, delay, ...args);
    } });
    try {
      const doc = dom.window.document, script = doc.querySelector('script[src]');
      assert.equal(script.src, 'https://telegram.org/js/telegram-web-app.js'); assert.equal(script.async, true);
      if (failure === 'error') script.onerror(); else timeout();
      assert.match(doc.querySelector('#picker-telegram-status').textContent, /жесты Telegram не защищены/);
      assert.ok(raid.state && raid.state.board.length === 36);
      const before = JSON.stringify(raid.state);
      dom.window.Telegram = { WebApp: stub.tg }; script.onload(); script.onload();
      assert.equal(stub.calls.filter(c => c === 'ready').length, 1);
      assert.equal(JSON.stringify(raid.state), before);
      assert.match(doc.querySelector('#menu-telegram-status').textContent, /^Telegram/);
      assert.deepEqual(errors, []);
    } finally { dom.window.close(); }
  }
});

test('built raid Telegram viewport changes honor stability, safe-area events and finite pixel limits', async () => {
  const stub = raidTelegramStub();
  const { dom, raid, errors } = await openRaid({ url: telegramRaidUrl, setup(window) { window.Telegram = { WebApp: stub.tg }; } });
  try {
    const style = dom.window.document.documentElement.style, before = JSON.stringify(raid.state);
    stub.tg.viewportStableHeight = 340; stub.tg.viewportHeight = 350;
    stub.fire('viewportChanged', { isStateStable: false });
    assert.equal(style.getPropertyValue('--raid-tg-height'), '680px');
    stub.fire('viewportChanged', { isStateStable: true });
    assert.equal(style.getPropertyValue('--raid-tg-height'), '340px');
    stub.tg.safeAreaInset = { top: 0, bottom: 21, left: 36, right: 36 };
    stub.tg.contentSafeAreaInset = { top: 18, bottom: 2, left: 0, right: 0 };
    stub.fire('safeAreaChanged'); stub.fire('contentSafeAreaChanged');
    assert.equal(style.getPropertyValue('--raid-tg-safe-left'), '36px');
    assert.equal(style.getPropertyValue('--raid-tg-content-top'), '18px');
    for (const invalid of [NaN, Infinity, -1, '700', 'url(https://example.invalid)', 10001]) {
      stub.tg.viewportStableHeight = invalid; stub.tg.viewportHeight = invalid;
      stub.tg.safeAreaInset.top = invalid; stub.fire('viewportChanged', { isStateStable: true });
      assert.equal(style.getPropertyValue('--raid-tg-height'), '340px');
      assert.equal(style.getPropertyValue('--raid-tg-safe-top'), '0px');
    }
    stub.tg.viewportHeight = 580; stub.fire('activated');
    assert.equal(style.getPropertyValue('--raid-tg-height'), '580px');
    assert.equal(JSON.stringify(raid.state), before);
    assert.deepEqual(errors, []);
  } finally { dom.window.close(); }
});

test('built raid Telegram Back handles startup readers/editor/Escape and live menus without a turn or reward bypass', async () => {
  const stub = raidTelegramStub();
  const { dom, raid, errors } = await openRaid({ url: telegramRaidUrl, start: false, setup(window) { window.Telegram = { WebApp: stub.tg }; } });
  const doc = dom.window.document;
  try {
    for (const id of ['picker-history', 'picker-achievements']) {
      doc.querySelector('#' + id).focus(); doc.querySelector('#' + id).click();
      await until(() => stub.tg.BackButton.isVisible);
      stub.back(); await until(() => !stub.tg.BackButton.isVisible);
      assert.equal(doc.querySelector('#history-modal').hidden, true);
      assert.equal(doc.activeElement.id, id); assert.equal(raid.state, null);
    }
    doc.querySelector('.class-config-btn').click(); await until(() => stub.tg.BackButton.isVisible);
    doc.querySelector('#class-picker').dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await until(() => !stub.tg.BackButton.isVisible);
    assert.equal(doc.querySelector('#class-editor').hidden, true);
    doc.querySelector('.class-config-btn').click(); await until(() => stub.tg.BackButton.isVisible);
    stub.back(); await until(() => !stub.tg.BackButton.isVisible);
    doc.querySelector('.picker-card-btn:not(.locked)').click();
    assert.equal(stub.tg.isClosingConfirmationEnabled, true);
    const before = JSON.stringify(raid.state);
    assert.equal(stub.tg.BackButton.isVisible, false, 'native Close remains at the run root');
    doc.querySelector('#menu-btn').click(); assert.equal(doc.querySelector('#menu-modal').classList.contains('show'), true);
    stub.back(); assert.equal(doc.querySelector('#menu-modal').classList.contains('show'), false);
    doc.querySelector('#help').click(); stub.back();
    assert.equal(doc.querySelector('#help-modal').classList.contains('show'), false);
    doc.querySelector('#restart').click(); stub.back();
    assert.equal(doc.querySelector('#restart-modal').classList.contains('show'), false);
    assert.equal(JSON.stringify(raid.state), before);
    doc.querySelector('#stats-btn').click(); assert.equal(raid.state.pendingModal, 'stats');
    stub.back(); await until(() => !raid.state.pendingModal);
    assert.equal(raid.state.turn, 0);
    raid.state.hero.xp = 100; raid.checkLevelUp();
    const menu = JSON.stringify(raid.state.menuCandidates), rng = JSON.stringify(raid.state.rngSeeds);
    stub.back();
    assert.equal(raid.state.pendingModal, 'level');
    assert.equal(JSON.stringify(raid.state.menuCandidates), menu); assert.equal(JSON.stringify(raid.state.rngSeeds), rng);
    assert.match(doc.querySelector('#flash').textContent, /Сначала заверши ход/);
    assert.deepEqual(errors, []);
  } finally { dom.window.close(); }
});

for (const cancel of ['pointercancel', 'deactivated', 'pagehide', 'visibilitychange']) test(`built raid ${cancel} discards a 3-cell gesture instead of resolving it, and stray pointerup stays inert`, async () => {
  const stub = raidTelegramStub();
  const b = await openRaid({ url: telegramRaidUrl, setup(window) { window.Telegram = { WebApp: stub.tg }; } });
  try {
    const { window } = b.dom, before = JSON.stringify(b.raid.state);
    raidDrag(b);
    if (cancel === 'pointercancel') raidPointer(window, window, cancel);
    else if (cancel === 'deactivated') stub.fire(cancel);
    else if (cancel === 'pagehide') window.dispatchEvent(new window.Event(cancel));
    else { Object.defineProperty(window.document, 'visibilityState', { value: 'hidden' }); window.document.dispatchEvent(new window.Event(cancel)); }
    assert.equal(JSON.stringify(b.raid.state), before);
    raidPointer(window, window, 'pointerup');
    stub.fire('activated'); await new Promise(resolve => setTimeout(resolve, 1100));
    assert.equal(JSON.stringify(b.raid.state), before);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid gesture is owned by one pointer; other fingers cannot restart, extend or commit it', async () => {
  const b = await openRaid(); const { window } = b.dom, doc = window.document;
  try {
    const cell = index => doc.querySelector(`.cell[data-idx="${index}"]`);
    raidPointer(window, cell(4), 'pointerdown', 4, { isPrimary: false });
    raidPointer(window, cell(4), 'pointerdown', 4, { button: 2 });
    assert.equal(b.raid.state.selected.length, 0);
    raidPointer(window, cell(0), 'pointerdown', 1);
    raidPointer(window, cell(1), 'pointerdown', 2);
    doc.elementFromPoint = () => cell(1);
    raidPointer(window, window, 'pointermove', 2); raidPointer(window, window, 'pointerup', 2);
    assert.deepEqual(Array.from(b.raid.state.selected), [0]);
    raidPointer(window, window, 'pointermove', 1);
    doc.elementFromPoint = () => cell(2); raidPointer(window, window, 'pointermove', 1);
    assert.deepEqual(Array.from(b.raid.state.selected), [0, 1, 2]);
    raidPointer(window, window, 'pointerup', 1);
    await until(() => !b.raid.state.resolving);
    assert.equal(b.raid.state.turn, 1); assert.equal(b.raid.state.selected.length, 0);
    raidPointer(window, window, 'pointercancel', 1); raidPointer(window, window, 'pointerup', 1);
    assert.equal(b.raid.state.turn, 1);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid Telegram deactivate/reopen requires explicit Continue and preserves level→shop→one enemy turn', async () => {
  let stub = raidTelegramStub();
  let b = await openRaid({ url: telegramRaidUrl, setup(window) { window.Telegram = { WebApp: stub.tg }; } });
  try {
    let { raid } = b, doc = b.dom.window.document, s = raid.state;
    s.forceCoins = 1; s.hero.hp = 10; s.hero.xp = 99; s.hero.xpLevelCum = 99;
    s.hero.gold = 99; s.goldEarned = 99;
    s.board[0] = { type: 'skull', skull: Object.assign(raid.makeSkull(false), { hp: 1, atk: 0 }) };
    s.board[1] = { type: 'chain' }; s.board[2] = { type: 'coin' };
    s.board[3] = { type: 'chain' }; s.board[4] = { type: 'potion' };
    [0, 1, 2, 3, 4].forEach(index => raid.tryExtendChain(index)); raid.resolveChain();
    await until(() => s.pendingModal === 'level');
    doc.querySelectorAll('.lv-card')[1].click(); doc.querySelectorAll('.lv-card')[0].click();
    const menu = JSON.parse(JSON.stringify(s.menuCandidates)), rng = JSON.stringify(s.rngSeeds);
    stub.back(); assert.equal(s.pendingModal, 'level');
    stub.fire('deactivated'); const saved = raidStorage(b.dom.window);
    assert.deepEqual(b.errors, []); b.dom.window.close();
    stub = raidTelegramStub();
    b = await openRaid({ url: telegramRaidUrl, start: false, saved, setup(window) { window.Telegram = { WebApp: stub.tg }; } });
    raid = b.raid; doc = b.dom.window.document;
    stub.fire('activated'); stub.fire('viewportChanged', { isStateStable: true });
    assert.equal(raid.state, null, 'SDK lifecycle never autoplays a saved run');
    assert.equal(stub.tg.isClosingConfirmationEnabled, false);
    assert.equal(doc.querySelector('#picker-resume').hidden, false);
    doc.querySelector('#picker-resume').click(); s = raid.state;
    assert.equal(stub.tg.isClosingConfirmationEnabled, true);
    assert.equal(s.pendingModal, 'level'); assert.equal(s.turn, 0);
    assert.deepEqual(Array.from(s.levelDraft), [1, 0]);
    assert.deepEqual(JSON.parse(JSON.stringify(s.menuCandidates)), menu); assert.equal(JSON.stringify(s.rngSeeds), rng);
    stub.back(); assert.equal(s.pendingModal, 'level');
    doc.querySelector('.lv-confirm').click(); await until(() => s.pendingModal === 'gold');
    const shop = JSON.stringify(s.menuCandidates), shopRng = JSON.stringify(s.rngSeeds);
    stub.back(); stub.fire('deactivated'); stub.fire('activated');
    assert.equal(s.pendingModal, 'gold'); assert.equal(JSON.stringify(s.menuCandidates), shop); assert.equal(JSON.stringify(s.rngSeeds), shopRng);
    const pick = s.menuCandidates.findIndex(item => !item.scroll);
    doc.querySelectorAll('button.shop-item')[pick].click(); await until(() => !s.resolving);
    assert.equal(s.turn, 1); assert.equal(s.arrivals.length, 0);
    assert.equal(s.hero.xp, 1); assert.equal(s.hero.gold, 1);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

// The first phone report exposed three gaps in the synthetic-host acceptance:
// protected launch messages, a root Back loop, and a shrinking board wrapper.
for (const telegram of [false, true]) test(`built raid ${telegram ? 'Telegram' : 'browser'} layout reserves a real footer row and cannot shrink below the board`, async () => {
  const stub = raidTelegramStub();
  const b = await openRaid({ url: telegram ? telegramRaidUrl : 'https://raid.test/', setup(window) { window.Telegram = { WebApp: stub.tg }; } });
  try {
    const doc = b.dom.window.document, style = node => b.dom.window.getComputedStyle(doc.querySelector(node));
    assert.equal(style('.board-wrap').flexShrink, '0');
    assert.match(style('.board-wrap').gridTemplateRows, /minmax\(180px,\s*1fr\) auto/);
    assert.equal(style('#board').contain, 'size', 'percentage SVG contents cannot inflate the intrinsic grid height');
    assert.equal(style('.board-foot').position, 'static');
    assert.equal(style('.board-foot').transform, 'none');
    assert.equal(style('.shell').overflowY, 'auto', 'short viewports scroll instead of overlapping HP');
    assert.equal(style('#menu-modal .menu-card').overflowY, 'auto', 'Close stays reachable in a short browser too');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid Telegram leaves native Close at the gameplay root; nested Back never loops into a new menu', async () => {
  const stub = raidTelegramStub();
  const b = await openRaid({ url: telegramRaidUrl, setup(window) { window.Telegram = { WebApp: stub.tg }; } });
  try {
    const doc = b.dom.window.document;
    await until(() => !stub.tg.BackButton.isVisible);
    assert.equal(stub.tg.isClosingConfirmationEnabled, true);
    const state = JSON.stringify(b.raid.state);
    doc.querySelector('#menu-btn').click(); await until(() => stub.tg.BackButton.isVisible);
    stub.back(); await until(() => !stub.tg.BackButton.isVisible);
    assert.equal(doc.querySelector('#menu-modal').classList.contains('show'), false);
    stub.back(); // A stale host event at root must not open the menu again.
    assert.equal(doc.querySelector('#menu-modal').classList.contains('show'), false);
    assert.equal(JSON.stringify(b.raid.state), state);
    b.raid.state.hero.xp = 100; b.raid.checkLevelUp();
    await until(() => !stub.tg.BackButton.isVisible);
    const pending = JSON.stringify(b.raid.state);
    stub.back();
    assert.equal(JSON.stringify(b.raid.state), pending, 'Back cannot dismiss or pick mandatory rewards');
    assert.equal(stub.calls.includes('close'), false);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid Telegram explicit Close checkpoints and cancels only the uncommitted gesture before requesting native exit', async () => {
  const stub = raidTelegramStub();
  const b = await openRaid({ url: telegramRaidUrl, setup(window) { window.Telegram = { WebApp: stub.tg }; } });
  try {
    const doc = b.dom.window.document;
    b.raid.state.hero.hp = 31;
    raidDrag(b);
    doc.querySelector('#menu-btn').click();
    assert.equal(doc.querySelector('#menu-close').textContent, 'Закрыть Mini App');
    doc.querySelector('#menu-close').click();
    assert.deepEqual(stub.calls.filter(c => c === 'close'), ['close']);
    const saved = JSON.parse(b.dom.window.localStorage.getItem('raid:activeRun'));
    assert.equal(saved.status, 'active'); assert.equal(saved.state.hero.hp, 31); assert.equal(saved.state.turn, 0);
    assert.equal(b.raid.state.over, false); assert.equal(b.raid.state.selected.length, 0);
    raidPointer(b.dom.window, b.dom.window, 'pointerup');
    assert.equal(b.raid.state.turn, 0);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

for (const failure of ['missing', 'throwing']) test(`built raid Telegram ${failure} close method stays visible and leaves a usable native exit`, async () => {
  const stub = raidTelegramStub({ overrides: { close: failure === 'missing' ? undefined : () => { throw new Error('unsupported close'); } } });
  const b = await openRaid({ url: telegramRaidUrl, setup(window) { window.Telegram = { WebApp: stub.tg }; } });
  try {
    const doc = b.dom.window.document, before = JSON.stringify(b.raid.state);
    doc.querySelector('#menu-btn').click(); doc.querySelector('#menu-close').click();
    assert.match(doc.querySelector('#menu-telegram-status').textContent, /закрытия.*Telegram/);
    assert.equal(JSON.stringify(b.raid.state), before);
    stub.back(); await until(() => !stub.tg.BackButton.isVisible);
    assert.equal(doc.querySelector('#menu-modal').classList.contains('show'), false);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid ordinary-browser Close is explicitly menu-only and never invokes an injected Telegram object', async () => {
  const stub = raidTelegramStub();
  const b = await openRaid({ setup(window) { window.Telegram = { WebApp: stub.tg }; } });
  try {
    const doc = b.dom.window.document, before = JSON.stringify(b.raid.state);
    doc.querySelector('#menu-btn').click();
    assert.equal(doc.querySelector('#menu-close').textContent, 'Закрыть меню');
    doc.querySelector('#menu-close').click();
    assert.equal(doc.querySelector('#menu-modal').classList.contains('show'), false);
    assert.equal(JSON.stringify(b.raid.state), before); assert.deepEqual(stub.calls, []);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid failed exit checkpoint requires explicit discard, offers cancellation/Back/Escape and guards stale confirmations', async () => {
  const stub = raidTelegramStub();
  const b = await openRaid({ url: telegramRaidUrl, setup(window) { window.Telegram = { WebApp: stub.tg }; } });
  try {
    const { window } = b.dom, doc = window.document, original = window.Storage.prototype.setItem;
    let fail = true;
    window.Storage.prototype.setItem = function (key, value) { if (fail && key === 'raid:activeRun') throw new Error('quota'); return original.call(this, key, value); };
    const openExit = () => { doc.querySelector('#menu-btn').click(); doc.querySelector('#menu-close').focus(); doc.querySelector('#menu-close').click(); };
    for (const cancel of ['button', 'back', 'escape']) {
      openExit(); assert.equal(doc.querySelector('#exit-modal').classList.contains('show'), true);
      assert.match(doc.querySelector('#exit-confirm-text').textContent, /не удалось сохранить/i);
      assert.equal(doc.activeElement.id, 'exit-cancel');
      if (cancel === 'button') doc.querySelector('#exit-cancel').click();
      else if (cancel === 'back') stub.back();
      else doc.querySelector('#exit-modal').dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      assert.equal(doc.querySelector('#exit-modal').classList.contains('show'), false);
      assert.equal(doc.activeElement.id, 'menu-close');
      assert.equal(stub.calls.includes('close'), false);
    }
    openExit(); fail = false; b.raid.newGame(2);
    assert.equal(doc.querySelector('#exit-modal').classList.contains('show'), false);
    doc.querySelector('#exit-confirm').click();
    assert.equal(stub.calls.includes('close'), false, 'an earlier run cannot confirm exit of this run');
    fail = true; openExit();
    const before = JSON.stringify(b.raid.state);
    doc.querySelector('#exit-confirm').click(); doc.querySelector('#exit-confirm').click();
    assert.equal(stub.calls.filter(c => c === 'close').length, 1);
    assert.equal(JSON.stringify(b.raid.state), before);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid Close preserves a reversible reward draft, then reopen→Continue has no reroll or bypass', async () => {
  let stub = raidTelegramStub();
  let b = await openRaid({ url: telegramRaidUrl, setup(window) { window.Telegram = { WebApp: stub.tg }; } });
  try {
    b.raid.state.hero.xp = 100; b.raid.checkLevelUp();
    let doc = b.dom.window.document;
    doc.querySelectorAll('.lv-card')[1].click(); doc.querySelectorAll('.lv-card')[0].click();
    const expected = JSON.stringify([b.raid.state.hero, b.raid.state.menuCandidates, b.raid.state.rngSeeds]);
    // A visible menu could have been opened before the pending reward arrived.
    doc.querySelector('#menu-btn').click(); doc.querySelector('#menu-close').click();
    assert.equal(stub.calls.filter(c => c === 'close').length, 1);
    assert.equal(JSON.stringify([b.raid.state.hero, b.raid.state.menuCandidates, b.raid.state.rngSeeds]), expected);
    const saved = raidStorage(b.dom.window); b.dom.window.close();
    stub = raidTelegramStub();
    b = await openRaid({ url: telegramRaidUrl, start: false, saved, setup(window) { window.Telegram = { WebApp: stub.tg }; } });
    assert.equal(b.raid.state, null);
    doc = b.dom.window.document; doc.querySelector('#picker-resume').click();
    assert.equal(b.raid.state.pendingModal, 'level'); assert.deepEqual(Array.from(b.raid.state.levelDraft), [1, 0]);
    assert.deepEqual(JSON.parse(JSON.stringify([b.raid.state.hero, b.raid.state.menuCandidates, b.raid.state.rngSeeds])), JSON.parse(expected));
    assert.equal(b.raid.state.turn, 0); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

// Phone feedback: compact choices retain every trade-off; duplicated full
// modifiers are an optional view, never another reward roll or an extra pick.
for (const kind of ['gold', 'armor']) test(`built raid compact reward ${kind}: details are optional, accessible and pure`, async () => {
  let b = await openRaid();
  try {
    const { raid } = b, s = raid.state, doc = b.dom.window.document;
    if (kind === 'gold') { s.hero.gold = 100; raid.checkGoldBox(); }
    else { s.upgradeBar = 100; raid.checkUpgradeBar(); }
    const grid = doc.querySelector('#equipment-choices');
    const details = doc.querySelector('.reward-details-toggle');
    assert.ok(grid && details, 'separate details control, not nested inside a purchase button');
    assert.equal(details.getAttribute('aria-controls'), grid.id);
    assert.equal(details.getAttribute('aria-expanded'), 'false');
    const states = [...grid.querySelectorAll('.item-state')];
    assert.equal(states.length, kind === 'gold' ? 6 : 8);
    assert.ok(states.every(n => n.hidden && b.dom.window.getComputedStyle(n).display === 'none'));
    assert.ok([...grid.querySelectorAll('.item-change')].every(n => !n.hidden));
    assert.ok([...grid.querySelectorAll('button')].every(n => !n.querySelector('button, summary, input')));
    const expected = JSON.stringify([s.hero, s.equipment, s.menuCandidates, s.rngSeeds, s.turn]);
    for (let n = 0; n < 2; n++) {
      details.focus(); details.click();
      assert.equal(details.getAttribute('aria-expanded'), 'true');
      assert.ok(states.every(n => !n.hidden && b.dom.window.getComputedStyle(n).display !== 'none'));
      assert.ok(states.every(n => n.textContent.trim()), 'full before/after remain available');
      doc.querySelector('.shop-summary-toggle').click();
      assert.equal(details.hidden, true, 'do not offer a details action for the hidden choice list');
      doc.querySelector('.shop-summary-toggle').click();
      assert.equal(details.hidden, false);
      details.click();
      assert.equal(details.getAttribute('aria-expanded'), 'false');
    }
    assert.equal(JSON.stringify([s.hero, s.equipment, s.menuCandidates, s.rngSeeds, s.turn]), expected);
    raid.saveRun(); const saved = raidStorage(b.dom.window); b.dom.window.close();
    b = await openRaid({ start: false, saved });
    assert.equal(b.raid.state, null);
    b.dom.window.document.querySelector('#picker-resume').click();
    assert.equal(b.raid.state.pendingModal, kind);
    assert.deepEqual(JSON.parse(JSON.stringify([b.raid.state.hero, b.raid.state.equipment, b.raid.state.menuCandidates, b.raid.state.rngSeeds, b.raid.state.turn])), JSON.parse(expected));
    assert.equal(b.dom.window.document.querySelector('.reward-details-toggle').getAttribute('aria-expanded'), 'false');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid compact reward frame: stable title, scrolling body, small art and usable touch actions', async () => {
  const b = await openRaid();
  try {
    const doc = b.dom.window.document, style = sel => b.dom.window.getComputedStyle(doc.querySelector(sel));
    b.raid.state.hero.xp = 100; b.raid.checkLevelUp();
    assert.equal(style('.modal-card').display, 'flex');
    assert.equal(style('#modal-body').overflowY, 'auto');
    assert.ok(parseFloat(style('.lv-chip').height) <= 40);
    assert.ok(parseFloat(style('.lv-icon-box').width) <= 40);
    assert.ok(parseFloat(style('.lv-card').minHeight) >= 44);
    assert.ok(parseFloat(style('.lv-confirm').minHeight) >= 44);
    assert.ok(parseFloat(style('.help-card .btn').minHeight) >= 44);
    assert.ok(parseFloat(style('.lv-name').fontSize) >= 13);
    assert.ok(parseFloat(style('.lv-line').fontSize) >= 11);
    assert.equal(style('.lv-actions').bottom, '0px');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid compact reward openings reset both old outer and inner scroll without rerolling a draft', async () => {
  const b = await openRaid();
  try {
    const doc = b.dom.window.document;
    const card = doc.querySelector('.modal-card'), body = doc.querySelector('#modal-body');
    card.scrollTop = 500; body.scrollTop = 500;
    b.raid.state.upgradeBar = 100; b.raid.checkUpgradeBar();
    assert.deepEqual([card.scrollTop, body.scrollTop], [0, 0]);
    card.scrollTop = 500; body.scrollTop = 500;
    doc.querySelector('.lv-card').click();
    b.raid.state.hero.gold = 100; b.raid.checkGoldBox();
    assert.deepEqual([card.scrollTop, body.scrollTop], [0, 0]);
    doc.querySelector('button.shop-item').click();
    card.scrollTop = 500; body.scrollTop = 500;
    b.raid.showSkullModal(b.raid.makeSkull(false, true, 4));
    assert.deepEqual([card.scrollTop, body.scrollTop], [0, 0]);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

// jsdom cannot lay out the board; supply explicit viewport-relative rectangles.
// Chromium acceptance separately exercises real geometry, scroll and touch.
function feedbackGeometry(b) {
  const { window } = b.dom, doc = window.document;
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 393 });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 700 });
  const offset = { x: 0, y: 0 };
  const rect = (x, y, width, height) => ({ x, y, left: x, top: y, width, height, right: x + width, bottom: y + height });
  const rows = { 'hud-hp': [15, 550, 180, 34], 'hud-armor': [200, 550, 178, 34], 'hud-xp': [15, 590, 180, 34], 'hud-gold': [200, 590, 178, 34], 'hud-upgrade': [15, 660, 363, 6] };
  for (const [id, values] of Object.entries(rows)) {
    const node = doc.getElementById(id); assert.ok(node, id + ' is an explicit stable effect target');
    node.getBoundingClientRect = () => rect(...values);
  }
  for (const node of doc.querySelectorAll('.cell')) {
    const index = Number(node.dataset.idx);
    node.getBoundingClientRect = () => rect(20 + index % 6 * 58 + offset.x, 155 + Math.floor(index / 6) * 58 + offset.y, 54, 54);
  }
  doc.querySelector('.shell').getBoundingClientRect = () => rect(0, 0, 393, 700);
  return offset;
}
function feedbackNodes(doc, target, kind = '') {
  return [...doc.querySelectorAll('#raid-effects .float-fx')].filter(n => n.dataset.target === target && (!kind || n.classList.contains(kind)));
}

test('built raid localized feedback: enemy HP/armor at the hit cell and hero damage at HP, surviving rerender', async () => {
  const b = await openRaid();
  try {
    const doc = b.dom.window.document; feedbackGeometry(b);
    const s = b.raid.state;
    s.board[0] = { type: 'skull', skull: Object.assign(b.raid.makeSkull(false, false), { hp: 30, maxHp: 30, def: 2, atk: 0 }) };
    s.pierce = 1;
    b.raid.hitSkullAt(0, 5, { direct: true });
    b.raid.damageHero(4, false, true);
    await until(() => feedbackNodes(doc, '.cell[data-idx="0"]', 'hurt').length === 1);
    assert.match(feedbackNodes(doc, '.cell[data-idx="0"]', 'hurt')[0].textContent, /−3.*HP/);
    assert.match(feedbackNodes(doc, '.cell[data-idx="0"]', 'armor')[0].textContent, /−2/);
    assert.match(feedbackNodes(doc, '#hud-hp', 'hurt')[0].textContent, /−4.*HP/);
    assert.equal(doc.querySelectorAll('#board .float-fx').length, 0, 'cell art/innerHTML must not own ephemeral numbers');
    assert.equal(doc.querySelector('#raid-effects').getAttribute('aria-hidden'), 'true');
    const popup = feedbackNodes(doc, '.cell[data-idx="0"]', 'hurt')[0];
    b.raid.endEnemyTurn();
    assert.equal(popup.isConnected, true, 'clearing/reusing the cell art cannot erase the hit');
    const expected = JSON.stringify(s);
    await new Promise(r => setTimeout(r, 75));
    assert.equal(JSON.stringify(s), expected, 'repositioning/animation does not mutate gameplay or RNG');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid localized feedback: potion/armor/XP/gold/UP and mana use their actual recipient, previews stay silent', async () => {
  const b = await openRaid();
  try {
    const doc = b.dom.window.document; feedbackGeometry(b); const s = b.raid.state;
    s.hero.hp = 20; s.hero.armor = 1;
    for (let n = 0; n < 3; n++) b.raid.addResource({ type: 'potion' });
    s.hero.armor = s.hero.maxArmor - 0.25;
    b.raid.addResource({ type: 'shield' });
    b.raid.addResource({ type: 'xp' }); b.raid.addResource({ type: 'coin' });
    s.hero.armor = s.hero.maxArmor; b.raid.addResource({ type: 'shield' });
    b.raid.installSkillOption(b.raid.prepareSkillOption('heal')); s.skillCooldowns.heal = 5;
    // A normal render rebuilds the four skills; use the live UI to show them.
    doc.querySelector('#stats-btn').click(); doc.querySelector('#modal-body .btn').click();
    b.raid.addResource({ type: 'potion', variant: 1, skillSlot: 0 });
    assert.match(feedbackNodes(doc, '#hud-hp', 'heal')[0].textContent, /\+6.*HP/);
    assert.equal(feedbackNodes(doc, '#hud-hp', 'heal').length, 1, 'one synchronous burst, not three overlapping heal labels');
    assert.match(feedbackNodes(doc, '#hud-armor', 'armor')[0].textContent, /\+0\.25.*брони/, 'fractional applied gains must not be rounded to +0');
    assert.match(feedbackNodes(doc, '#hud-xp', 'xp')[0].textContent, /\+2/);
    assert.match(feedbackNodes(doc, '#hud-gold', 'gold')[0].textContent, /\+2/);
    assert.match(feedbackNodes(doc, '#hud-upgrade', 'upgrade')[0].textContent, /\+2/);
    assert.match(feedbackNodes(doc, '.skill[data-slot="0"]', 'mana')[0].textContent, /−1/);
    const count = doc.querySelectorAll('.float-fx').length, preview = JSON.parse(JSON.stringify(s));
    b.raid.addResource({ type: 'potion' }, preview); b.raid.addResource({ type: 'coin' }, preview);
    assert.equal(doc.querySelectorAll('.float-fx').length, count, 'detached chain preview cannot emit applied feedback');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid localized feedback: anchors follow scroll/resize, hide offscreen, expire without animation events and clear on a new run', async () => {
  const b = await openRaid();
  try {
    const doc = b.dom.window.document, offset = feedbackGeometry(b);
    const s = b.raid.state;
    s.board[8] = { type: 'skull', skull: Object.assign(b.raid.makeSkull(false, false), { hp: 50, maxHp: 50, def: 0, atk: 0 }) };
    b.raid.hitSkullAt(8, 3, { direct: false });
    const popup = feedbackNodes(doc, '.cell[data-idx="8"]', 'hurt')[0];
    await until(() => parseFloat(popup.style.left) > 0);
    const x = parseFloat(popup.style.left), y = parseFloat(popup.style.top);
    assert.ok(Math.abs(x - 163) < 2 && Math.abs(y - 240) < 2);
    offset.x = 15; offset.y = -80;
    await until(() => Math.abs(parseFloat(popup.style.top) - (y - 80)) < 2);
    assert.ok(Math.abs(parseFloat(popup.style.left) - (x + 15)) < 2);
    offset.y = -1000;
    await until(() => popup.hidden);
    offset.y = 0;
    await until(() => !popup.hidden);
    await until(() => !popup.isConnected, 2000);
    b.raid.damageHero(1, false, true);
    assert.equal(feedbackNodes(doc, '#hud-hp').length, 1);
    b.raid.newGame(1);
    assert.equal(doc.querySelectorAll('.float-fx').length, 0);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid localized feedback: enemy healing, fire and poison ticks and blocked hero armor do not fall back to board center', async () => {
  const b = await openRaid();
  try {
    const doc = b.dom.window.document; feedbackGeometry(b); const s = b.raid.state;
    const skull = (type, fields) => Object.assign(b.raid.makeSkull(false, type > 0, type), { atk: 0, def: 0, enteredTurn: -1 }, fields);
    s.board[0] = { type: 'skull', skull: skull(4, { hp: 20, maxHp: 20 }) };
    s.board[7] = { type: 'skull', skull: skull(0, { hp: 3, maxHp: 10 }) };
    b.raid.applySpecialAbilities();
    assert.match(feedbackNodes(doc, '.cell[data-idx="7"]', 'heal')[0].textContent, /\+7.*HP/);
    s.board[0] = { type: 'sword' };
    Object.assign(s.board[7].skull, { hp: 20, maxHp: 20, burn: true, fireDamage: 2, poison: [3, 0, 0] });
    b.raid.endEnemyTurn();
    assert.match(feedbackNodes(doc, '.cell[data-idx="7"]', 'hurt')[0].textContent, /−5.*HP/);
    s.board[7].skull.atk = 3; s.turn = 5; s.hero.armor = 4; s.durability = 0;
    b.raid.processEnemyAttack();
    assert.match(feedbackNodes(doc, '#hud-armor', 'armor')[0].textContent, /−3/);
    assert.equal(feedbackNodes(doc, '#hud-hp', 'hurt').length, 0, 'absorbed volley did not damage HP');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid localized feedback: board-wide stress is bounded and resume never replays old decorations', async () => {
  let b = await openRaid();
  try {
    const doc = b.dom.window.document; feedbackGeometry(b);
    const s = b.raid.state;
    s.pierce = 1;
    s.board = Array.from({ length: 36 }, () => ({ type: 'skull', skull: Object.assign(b.raid.makeSkull(false, false), { hp: 100, maxHp: 100, atk: 0, def: 1 }) }));
    for (let i = 0; i < 36; i++) b.raid.hitSkullAt(i, 2, { direct: false });
    assert.ok(doc.querySelectorAll('.float-fx').length <= 64);
    assert.ok(doc.querySelectorAll('.float-fx').length >= 36);
    assert.ok([...doc.querySelectorAll('.float-fx')].every(n => n.parentElement.id === 'raid-effects'));
    const layerStyle = b.dom.window.getComputedStyle(doc.querySelector('#raid-effects'));
    assert.equal(layerStyle.pointerEvents, 'none'); assert.equal(layerStyle.position, 'fixed');
    b.raid.saveRun(); const expected = JSON.parse(JSON.stringify([s.hero, s.board, s.rngSeeds]));
    const saved = raidStorage(b.dom.window); b.dom.window.close();
    b = await openRaid({ saved, start: false });
    assert.equal(b.raid.state, null);
    b.dom.window.document.querySelector('#picker-resume').click();
    assert.deepEqual(JSON.parse(JSON.stringify([b.raid.state.hero, b.raid.state.board, b.raid.state.rngSeeds])), expected);
    assert.equal(b.dom.window.document.querySelectorAll('.float-fx').length, 0);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

// Account ID below is a UI-only local namespace, never server authentication.
const testerRaidUrl = 'https://raid.test/?telegram=1';
function localAccountSDK(id) { return { initData: 'LOCAL_PROFILE_NOT_AUTH', initDataUnsafe: { user: { id } }, ready() {} }; }
const scopedRaidKey = (id, key) => `raid:tg:${id}:${key.slice(5)}`;
async function openAccountRaid(id, options = {}) {
  const sdk = localAccountSDK(id);
  return openRaid({ ...options, url: options.url || testerRaidUrl, setup(window) {
    window.Telegram = { WebApp: sdk };
    options.setup?.(window, sdk);
  } });
}

test('built raid tester profiles: a tester starts fresh without reading/copying legacy owner or browser progress', async () => {
  const owner = await openRaid();
  let tester;
  try {
    owner.raid.unlockClass('mage'); owner.raid.levelUpClass('mage');
    owner.raid.state.hero.hp = 13; owner.raid.saveRun();
    const legacy = raidStorage(owner.dom.window);
    tester = await openAccountRaid(71001, { saved: legacy, start: false });
    assert.equal(tester.raid.state, null);
    assert.equal(tester.raid.savedRunInfo(), null);
    assert.equal(tester.raid.classLevelOf('mage'), 0);
    assert.equal(tester.dom.window.document.querySelector('#picker-resume').hidden, true);
    tester.raid.newGame(88); tester.raid.unlockClass('ranger'); tester.raid.setSelectedClassId('ranger'); tester.raid.saveRun();
    for (const [key, value] of Object.entries(legacy)) assert.equal(tester.dom.window.localStorage.getItem(key), value);
    assert.ok(tester.dom.window.localStorage.getItem(scopedRaidKey(71001, 'raid:activeRun')));
    assert.ok(tester.dom.window.localStorage.getItem(scopedRaidKey(71001, 'raid:classLevels')));
    assert.match(tester.dom.window.document.querySelector('#picker-profile-status').textContent, /71001/);
    assert.deepEqual(tester.errors, []);
  } finally { owner.dom.window.close(); tester?.dom.window.close(); }
});

test('built raid tester profiles: two accounts on the same origin preserve independent drafts, classes, settings, history and RNG', async () => {
  let a = await openAccountRaid(71002), b;
  try {
    a.raid.unlockClass('mage'); for (let n = 0; n < 4; n++) a.raid.levelUpClass('mage');
    a.raid.setSelectedClassId('mage'); assert.equal(a.raid.saveClassConfig('mage', a.raid.defaultClassConfig('mage')), true);
    endBuiltRaid(a.raid); await until(() => a.raid.state.over);
    a.raid.newGame(19); a.raid.state.hero.hp = 21; a.raid.state.hero.xp = 100; a.raid.checkLevelUp();
    a.dom.window.document.querySelectorAll('.lv-card')[1].click(); a.raid.saveRun();
    const expected = JSON.parse(JSON.stringify([a.raid.state.hero, a.raid.state.board, a.raid.state.menuCandidates, a.raid.state.levelDraft, a.raid.state.rngSeeds]));
    const first = raidStorage(a.dom.window), config = a.raid.getClassConfig('mage');
    const profile = JSON.parse(JSON.stringify(a.raid.getLocalHistory()));
    for (const name of ['class', 'classLevels', 'classConfigs', 'mode', 'difficulty', 'trophyRemaining', 'activeRun', 'history']) {
      assert.ok(Object.hasOwn(first, scopedRaidKey(71002, 'raid:' + name)), 'missing account-scoped ' + name);
    }
    a.dom.window.close();
    b = await openAccountRaid(71003, { saved: first });
    assert.equal(b.raid.classLevelOf('mage'), 0);
    assert.equal(b.raid.getLocalHistory().classes.adventurer.games, 0);
    b.raid.unlockClass('rogue'); b.raid.state.hero.hp = 37; b.raid.saveRun();
    const both = raidStorage(b.dom.window);
    for (const [key, value] of Object.entries(first)) assert.equal(both[key], value, 'B changed A: ' + key);
    b.dom.window.close();
    a = await openAccountRaid(71002, { saved: both, start: false });
    assert.equal(a.raid.state, null); a.dom.window.document.querySelector('#picker-resume').click();
    assert.deepEqual(JSON.parse(JSON.stringify([a.raid.state.hero, a.raid.state.board, a.raid.state.menuCandidates, a.raid.state.levelDraft, a.raid.state.rngSeeds])), expected);
    assert.equal(a.raid.classLevelOf('mage'), 5); assert.equal(a.raid.classLevelOf('rogue'), 0);
    assert.deepEqual(JSON.parse(JSON.stringify(a.raid.getClassConfig('mage'))), JSON.parse(JSON.stringify(config)));
    assert.equal(a.raid.getLocalHistory().classes.adventurer.games, 1);
    assert.deepEqual(JSON.parse(JSON.stringify(a.raid.getLocalHistory())), profile, 'B cannot alter A records, class totals or achievement progress');
    assert.deepEqual(a.errors, []); assert.deepEqual(b.errors, []);
  } finally { a.dom.window.close(); b?.dom.window.close(); }
});

test('built raid tester profiles: old owner data is retained only for a matching owner launcher identity', async () => {
  let owner = await openRaid(), other;
  try {
    owner.raid.state.hero.hp = 17; owner.raid.saveRun(); const saved = raidStorage(owner.dom.window); owner.dom.window.close();
    owner = await openAccountRaid(71004, { url: testerRaidUrl + '#raid_legacy_owner=71004', saved, start: false });
    assert.ok(owner.raid.savedRunInfo()); owner.dom.window.document.querySelector('#picker-resume').click();
    assert.equal(owner.raid.state.hero.hp, 17);
    owner.raid.saveRun(); const ownerSnapshot = raidStorage(owner.dom.window); owner.dom.window.close();
    other = await openAccountRaid(71005, { url: testerRaidUrl + '#raid_legacy_owner=71004', saved: ownerSnapshot, start: false });
    assert.equal(other.raid.savedRunInfo(), null, 'forwarding owner launch URL cannot select owner data for another account');
    other.raid.newGame(5); other.raid.saveRun();
    assert.ok(other.dom.window.localStorage.getItem(scopedRaidKey(71005, 'raid:activeRun')));
    assert.equal(other.dom.window.localStorage.getItem('raid:activeRun'), ownerSnapshot['raid:activeRun']);
  } finally { owner.dom.window.close(); other?.dom.window.close(); }
});

for (const id of [undefined, 0, -1, 1.5, '71006', Infinity]) test(`built raid tester profiles: missing or invalid SDK identity ${id} never falls back to shared persistent storage`, async () => {
  const legacy = { 'raid:classLevels': '{"adventurer":1,"mage":10}', 'raid:activeRun': 'not this profile' };
  const b = await openAccountRaid(id, { saved: legacy });
  try {
    assert.equal(b.raid.classLevelOf('mage'), 0);
    assert.equal(b.raid.saveRun(), false);
    assert.deepEqual(raidStorage(b.dom.window), legacy);
    assert.match(b.dom.window.document.querySelector('#picker-profile-status').textContent, /не определён|не определен/);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('built raid tester profiles: delayed SDK selects account before Continue but cannot rebind a started anonymous run', async () => {
  for (const started of [false, true]) {
    const fixture = await openAccountRaid(71007); fixture.raid.state.hero.hp = 12; fixture.raid.saveRun();
    const saved = raidStorage(fixture.dom.window); fixture.dom.window.close();
    const b = await openRaid({ url: testerRaidUrl, saved, start: started });
    try {
      assert.equal(b.raid.savedRunInfo(), null);
      b.dom.window.Telegram = { WebApp: localAccountSDK(71007) };
      b.dom.window.document.querySelector('script[src]').onload();
      if (!started) {
        assert.ok(b.raid.savedRunInfo()); b.dom.window.document.querySelector('#picker-resume').click(); assert.equal(b.raid.state.hero.hp, 12);
      } else {
        assert.equal(b.raid.saveRun(), false);
        assert.deepEqual(raidStorage(b.dom.window), saved, 'late SDK must not overwrite this account with an anonymous run');
      }
      assert.deepEqual(b.errors, []);
    } finally { b.dom.window.close(); }
  }
});

test('built raid tester profiles: account changes stop writes; other account storage events cannot conflict this run', async () => {
  let sdk; const b = await openAccountRaid(71008, { setup(window, value) { sdk = value; } });
  try {
    b.raid.saveRun(); const before = raidStorage(b.dom.window), raw = JSON.stringify(b.raid.state);
    const otherKey = scopedRaidKey(71009, 'raid:activeRun');
    b.dom.window.localStorage.setItem(otherKey, 'other account');
    b.dom.window.dispatchEvent(new b.dom.window.StorageEvent('storage', { key: otherKey, newValue: 'other account' }));
    assert.equal(b.raid.saveStatus.state, 'saved'); assert.equal(JSON.stringify(b.raid.state), raw);
    sdk.initDataUnsafe.user.id = 71009;
    assert.equal(b.raid.saveRun(), false);
    for (const [key, value] of Object.entries(before)) assert.equal(b.dom.window.localStorage.getItem(key), value);
    assert.equal(b.dom.window.localStorage.getItem(otherKey), 'other account');
    assert.match(b.dom.window.document.querySelector('#menu-profile-status').textContent, /смен|заново/);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

// The redesign is a separate product/URL, never a migration of the trial.
const magitechKey = key => `raid:magitech:${key.slice(5)}`;
const magitechAccountKey = (id, key) => `raid:magitech:tg:${id}:${key.slice(5)}`;
const openMagitech = options => openRaid({ ...options, variant: 'magitech' });

async function reopenMagitech(previous, options = {}) {
  previous.dom.window.dispatchEvent(new previous.dom.window.Event('pagehide'));
  const saved = raidStorage(previous.dom.window);
  assert.deepEqual(previous.errors, []);
  previous.dom.window.close();
  return openMagitech({ saved, start: false, ...options });
}

test('magitech fork: classic artifact stays pinned and the new standalone contains its own inputs', () => {
  const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
  const classic = fs.readFileSync(path.join(root, 'lunapark-raid.html'));
  assert.equal(crypto.createHash('sha256').update(classic).digest('hex'), '7508bf14594cd2eb5b6fc1304d327554201a2bbcb964515191b499214a892e1f');
  const html = fs.readFileSync(path.join(root, 'lunapark-raid-magitech.html'), 'utf8');
  const js = fs.readFileSync(path.join(root, 'miniapp_preview/raid_magitech.js'), 'utf8');
  const css = fs.readFileSync(path.join(root, 'miniapp_preview/raid_magitech.css'), 'utf8');
  assert.ok(html.includes(js.replaceAll('</', '<\\/')));
  assert.doesNotMatch(html, /__(?:RAID_BUILD|RAID_SCENE_\w+)__|\/\*__(?:SCRIPT|STYLE)__\*\//);
  assert.match(css, /\[hidden\]\s*\{\s*display:\s*none\s*!important/);
  assert.match(css, /\.raid-intro\s*\{[^}]*flex:\s*0 0 auto;/, 'mobile introduction grows with its notice, never clips it to a fixed flex basis');
  const landscape = css.slice(css.indexOf('@media (max-height: 610px) and (min-width: 721px)'));
  assert.match(landscape, /\.raid-intro-copy\s*\{\s*display:\s*none;/, 'short landscape prioritizes the location controls over the headline');
  const sprites = JSON.parse(fs.readFileSync(path.join(root, 'miniapp_preview/raid_monsters.json'), 'utf8'));
  assert.equal(Object.keys(sprites.portraits).length, 16);
  for (const [id, image] of Object.entries(sprites.portraits)) {
    assert.match(image, /^data:image\/webp;base64,/);
    assert.ok(sprites.sources[id].file.endsWith('.jpg'));
  }
});

test('magitech fork: old browser progress is neither loaded, copied nor overwritten', async () => {
  const classic = await openRaid(); let b;
  try {
    classic.raid.unlockClass('mage'); classic.raid.levelUpClass('mage');
    classic.raid.state.hero.hp = 13; classic.raid.saveRun();
    const saved = raidStorage(classic.dom.window);
    b = await openMagitech({ saved, start: false });
    assert.equal(b.raid.state, null); assert.equal(b.raid.savedRunInfo(), null);
    assert.equal(b.raid.classLevelOf('mage'), 0); assert.equal(b.raid.getSelectedClassId(), 'adventurer');
    assert.equal(b.dom.window.document.querySelector('#picker-resume').hidden, true);
    b.dom.window.document.querySelector('.picker-card-btn:not(.locked)').click();
    assert.equal(b.raid.state.hero.hp, 50); assert.equal(b.raid.state.classId, 'adventurer');
    assert.equal(JSON.parse(b.dom.window.localStorage.getItem(magitechKey('raid:activeRun'))).format, 'lunapark-raid-magitech-run');
    for (const [key, value] of Object.entries(saved)) assert.equal(b.dom.window.localStorage.getItem(key), value, key);
    for (const key of Object.keys(raidStorage(b.dom.window))) assert.ok(key.startsWith('raid:magitech:') || key in saved, key);
    assert.deepEqual(b.errors, []);
  } finally { classic.dom.window.close(); b?.dom.window.close(); }
});

test('magitech fork: a redesign profile is invisible to the classic trial in the same origin', async () => {
  const b = await openMagitech(); let classic;
  try {
    b.raid.unlockClass('warrior'); b.raid.levelUpClass('warrior'); b.raid.state.hero.hp = 21; b.raid.saveRun();
    const saved = raidStorage(b.dom.window);
    classic = await openRaid({ saved, start: false });
    assert.equal(classic.raid.savedRunInfo(), null); assert.equal(classic.raid.classLevelOf('warrior'), 0);
    assert.equal(classic.raid.state, null);
    classic.dom.window.document.querySelector('.picker-card-btn:not(.locked)').click();
    for (const [key, value] of Object.entries(saved)) assert.equal(classic.dom.window.localStorage.getItem(key), value, key);
    assert.equal(classic.raid.state.hero.hp, 50); assert.deepEqual(classic.errors, []);
  } finally { b.dom.window.close(); classic?.dom.window.close(); }
});

test('magitech fork: account profiles are separate and the owner legacy fragment cannot select classic saves', async () => {
  let a = await openMagitech({ url: testerRaidUrl + '#raid_legacy_owner=71001', setup(w) { w.Telegram = { WebApp: localAccountSDK(71001) }; } });
  let b;
  try {
    a.raid.unlockClass('mage'); a.raid.state.hero.hp = 17; a.raid.saveRun();
    const saved = raidStorage(a.dom.window);
    assert.ok(saved[magitechAccountKey(71001, 'raid:activeRun')]); assert.equal(saved['raid:activeRun'], undefined);
    b = await openMagitech({ saved, start: false, url: testerRaidUrl, setup(w) { w.Telegram = { WebApp: localAccountSDK(71002) }; } });
    assert.equal(b.raid.savedRunInfo(), null); assert.equal(b.raid.classLevelOf('mage'), 0);
    b.dom.window.document.querySelector('.picker-card-btn:not(.locked)').click();
    const both = raidStorage(b.dom.window);
    assert.ok(both[magitechAccountKey(71002, 'raid:activeRun')]);
    for (const [key, value] of Object.entries(saved)) assert.equal(both[key], value);
    b.dom.window.close();
    b = await openMagitech({ saved: both, start: false, url: testerRaidUrl, setup(w) { w.Telegram = { WebApp: localAccountSDK(71001) }; } });
    b.dom.window.document.querySelector('#picker-resume').click();
    assert.equal(b.raid.state.hero.hp, 17); assert.equal(b.raid.classLevelOf('mage'), 1);
    assert.deepEqual(a.errors, []); assert.deepEqual(b.errors, []);
  } finally { a.dom.window.close(); b?.dom.window.close(); }
});

test('magitech fork: unknown or changed Telegram identity never falls back to browser/classic storage', async () => {
  const sentinel = { 'raid:activeRun': 'classic sentinel', [magitechKey('raid:activeRun')]: 'browser sentinel' };
  let sdk;
  const b = await openMagitech({ saved: sentinel, start: false, url: testerRaidUrl, setup(w) { sdk = localAccountSDK(null); w.Telegram = { WebApp: sdk }; } });
  try {
    assert.equal(b.raid.savedRunInfo(), null);
    b.dom.window.document.querySelector('.picker-card-btn:not(.locked)').click();
    assert.equal(b.raid.saveRun(), false);
    b.dom.window.document.querySelector('button[data-raid-region="fire"]').click();
    assert.deepEqual(raidStorage(b.dom.window), sentinel);
    sdk.initDataUnsafe.user.id = 71003;
    b.dom.window.Telegram.WebApp = sdk;
    assert.equal(b.raid.saveRun(), false, 'an already running anonymous game cannot adopt a late identity');
    assert.deepEqual(raidStorage(b.dom.window), sentinel); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech fork: classical checkpoint under a redesign key is rejected without migration or reset', async () => {
  const classic = await openRaid(); let b;
  try {
    classic.raid.saveRun(); const raw = classic.dom.window.localStorage.getItem('raid:activeRun');
    b = await openMagitech({ saved: { [magitechKey('raid:activeRun')]: raw }, start: false });
    assert.equal(b.raid.savedRunInfo(), null); assert.equal(b.raid.resumeSavedRun(), false);
    assert.equal(b.dom.window.localStorage.getItem(magitechKey('raid:activeRun')), raw);
    assert.equal(b.raid.state, null); assert.deepEqual(b.errors, []);
  } finally { classic.dom.window.close(); b?.dom.window.close(); }
});

test('magitech fork: appearance themes are cosmetic, preserve RNG/state and persist only in the separate profile', async () => {
  let b = await openMagitech();
  try {
    const doc = b.dom.window.document, before = JSON.stringify(b.raid.state), classicKeys = Object.keys(raidStorage(b.dom.window)).filter(k => !k.startsWith('raid:magitech:'));
    for (const region of ['fire', 'air', 'earth', 'water']) {
      doc.querySelector(`button[data-raid-region="${region}"]`).click();
      assert.equal(doc.documentElement.dataset.raidRegion, region);
      assert.equal(doc.querySelector(`button[data-raid-region="${region}"]`).getAttribute('aria-pressed'), 'true');
      assert.equal(JSON.stringify(b.raid.state), before);
    }
    assert.equal(b.dom.window.localStorage.getItem(magitechKey('raid:appearance')), 'water');
    assert.deepEqual(Object.keys(raidStorage(b.dom.window)).filter(k => !k.startsWith('raid:magitech:')), classicKeys);
    b = await reopenMagitech(b);
    assert.equal(b.dom.window.document.documentElement.dataset.raidRegion, 'water'); assert.equal(b.raid.state, null);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech fork: only endless mode and the three identically named original difficulty curves are offered', async () => {
  const a = await openRaid({ start: false }), b = await openMagitech({ start: false });
  try {
    const doc = b.dom.window.document;
    assert.deepEqual([...doc.querySelector('#difficulty-select').options].map(o => o.value), ['0','1','2']);
    assert.deepEqual([...doc.querySelector('#mode-select').options].map(o => o.value), ['classic']);
    for (const difficulty of [0,1,2]) {
      a.dom.window.document.querySelector('#difficulty-select').value = String(difficulty);
      doc.querySelector('#difficulty-select').value = String(difficulty);
      a.raid.newGame(1337); b.raid.newGame(1337);
      for (const turn of [0,100,500,1000]) {
        a.raid.state.turn = b.raid.state.turn = turn;
        for (const type of [0,1,5,17,27,32]) {
          const x = a.raid.makeSkull(type === 1, type > 1, type), y = b.raid.makeSkull(type === 1, type > 1, type);
          assert.deepEqual(JSON.parse(JSON.stringify(y)), JSON.parse(JSON.stringify(x)), `${difficulty}/${turn}/${type}`);
        }
      }
      assert.deepEqual([...b.raid.state.rngSeeds], [...a.raid.state.rngSeeds]);
    }
    assert.deepEqual(a.errors, []); assert.deepEqual(b.errors, []);
  } finally { a.dom.window.close(); b.dom.window.close(); }
});

test('magitech fork: partial level choice resumes explicitly without rerolling or auto-confirming', async () => {
  let b = await openMagitech();
  try {
    const doc = b.dom.window.document; b.raid.state.hero.xp = 100; b.raid.checkLevelUp();
    assert.equal(b.raid.state.pendingModal, 'level');
    doc.querySelectorAll('.lv-card')[0].click(); doc.querySelectorAll('.lv-card')[1].click();
    doc.querySelectorAll('.lv-card')[0].click();
    b.raid.saveRun();
    const expected = JSON.parse(JSON.stringify([b.raid.state.hero, b.raid.state.rngSeeds, b.raid.state.menuCandidates, b.raid.state.levelDraft]));
    b = await reopenMagitech(b);
    assert.equal(b.raid.state, null); b.dom.window.document.querySelector('#picker-resume').click();
    assert.equal(b.raid.state.pendingModal, 'level');
    assert.deepEqual(JSON.parse(JSON.stringify([b.raid.state.hero, b.raid.state.rngSeeds, b.raid.state.menuCandidates, b.raid.state.levelDraft])), expected);
    assert.equal(b.dom.window.document.querySelector('.lv-confirm').disabled, true);
    assert.equal(b.dom.window.document.querySelectorAll('.lv-card.picked').length, 1);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech fork: new portraits retain the same UID node and a theme does not invent combat elements or coefficients', async () => {
  const b = await openMagitech({ start: false });
  try {
    const doc = b.dom.window.document;
    doc.querySelector('.picker-card-btn:not(.locked)').click();
    // A genuine new-game board has no enemies; use an explicit survivor fixture.
    b.raid.state.board[0] = { type: 'skull', skull: Object.assign(b.raid.makeSkull(false, false, 0), { hp: 30, maxHp: 30, atk: 0, def: 0, enteredTurn: -1 }) };
    b.raid.endEnemyTurn();
    const cell = doc.querySelector('.cell.type-skull'), img = cell.querySelector('img');
    assert.match(img.src, /^data:image\/webp;base64,/);
    b.raid.endEnemyTurn();
    assert.equal(cell.querySelector('img'), img, 'surviving UID keeps its image through a full render');
    const before = JSON.stringify(b.raid.state);
    doc.querySelector('button[data-raid-region="fire"]').click();
    assert.equal(cell.querySelector('img'), img); assert.equal(JSON.stringify(b.raid.state), before);
    for (const enemy of b.raid.state.board.filter(c => c.type === 'skull')) assert.equal(enemy.skull.element, undefined);
    assert.equal(b.raid.baseDamage(), 3); assert.equal(b.raid.swordDamage(3), 9);
    assert.match(doc.querySelector('#raid-prototype-note').textContent, /не включены/);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});


test('magitech fork: every inherited profile key and appearance is namespaced without touching either classic profile', async () => {
  const names = ['class','classLevels','classConfigs','mode','difficulty','trophyRemaining','activeRun','history'];
  const sentinel = Object.fromEntries(names.flatMap(name => [[`raid:${name}`, `classic browser ${name}`], [`raid:tg:71001:${name}`, `classic account ${name}`]]));
  for (const account of [null, 71001]) {
    const b = await openMagitech({ saved: sentinel, ...(account ? { url: testerRaidUrl + '#raid_legacy_owner=71001', setup(w) { w.Telegram = { WebApp: localAccountSDK(account) }; } } : {}) });
    try {
      const key = name => account ? magitechAccountKey(account, 'raid:' + name) : magitechKey('raid:' + name);
      b.raid.unlockClass('mage');
      assert.equal(b.raid.saveClassConfig('mage', b.raid.defaultClassConfig('mage')), true);
      b.raid.setSelectedClassId('mage');
      b.dom.window.document.querySelector('button[data-raid-region="air"]').click();
      b.raid.state.trophyRemaining = 23;
      b.raid.state.board[0] = { type: 'skull', skull: Object.assign(b.raid.makeSkull(false, true, 4), { hp: 1, def: 0, atk: 0 }) };
      b.raid.collectIndices([0]);
      b.raid.damageHero(9999, false, true);
      b.raid.endEnemyTurn(); // Terminal checks belong to the original turn boundary.
      await until(() => b.raid.state.over && b.dom.window.localStorage.getItem(key('history')));
      for (const name of names) assert.notEqual(b.dom.window.localStorage.getItem(key(name)), null, name);
      assert.equal(b.dom.window.localStorage.getItem(key('appearance')), 'air');
      for (const [key, value] of Object.entries(sentinel)) assert.equal(b.dom.window.localStorage.getItem(key), value);
      assert.equal(JSON.parse(b.dom.window.localStorage.getItem(key('history'))).format, 'lunapark-raid-magitech-history');
      assert.deepEqual(b.errors, []);
    } finally { b.dom.window.close(); }
  }
});

test('magitech fork: original mech-level gates and frozen fighter configuration survive explicit continuation', async () => {
  let b = await openMagitech({ saved: { [magitechKey('raid:classLevels')]: JSON.stringify({ mage: 5, barbarian: 5 }) }, start: false });
  try {
    const info = b.raid.classEditorInfo('mage');
    assert.deepEqual([...info.pairSlots], [true,true,false,false]);
    assert.equal(info.replacementLimit, 4); assert.equal(info.canChangeRace, true);
    b.raid.setSelectedClassId('mage'); b.raid.newGame(71);
    const original = JSON.parse(JSON.stringify(b.raid.state.classConfig));
    const changed = JSON.parse(JSON.stringify(b.raid.getClassConfig('mage'))); changed.race = 'Orc';
    assert.equal(b.raid.saveClassConfig('mage', changed), true);
    assert.deepEqual(JSON.parse(JSON.stringify(b.raid.state.classConfig)), original);
    assert.equal(b.raid.clsRace(), 'Changeling'); assert.equal(b.raid.saveRun(), true);
    b = await reopenMagitech(b);
    b.dom.window.document.querySelector('#picker-resume').click();
    assert.equal(b.raid.clsRace(), 'Changeling');
    assert.deepEqual(JSON.parse(JSON.stringify(b.raid.state.classConfig)), original);
    assert.equal(Object.isFrozen(b.raid.state.classConfig), true);
    b.raid.newGame(71); assert.equal(b.raid.clsRace(), 'Orc');
    assert.equal(b.raid.classLevelOf('mage'), 5); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech fork: unavailable legacy achievement conditions are disclosed, never silently replaced', async () => {
  const b = await openMagitech({ start: false });
  try {
    const before = raidStorage(b.dom.window), achievements = b.raid.getLocalAchievements();
    assert.equal(achievements.total, 77); assert.equal(achievements.available, 72);
    assert.deepEqual(Array.from(achievements.catalog.filter(a => !a.available), a => a.id), [45,70,71,76,77]);
    b.raid.openAchievements();
    const doc = b.dom.window.document, filter = doc.querySelector('#achievement-filter');
    filter.value = 'unavailable'; filter.dispatchEvent(new b.dom.window.Event('change'));
    assert.equal(doc.querySelectorAll('.achievement-record').length, 5);
    assert.match(doc.querySelector('[data-achievement-id="76"]').textContent, /требует режима/);
    assert.match(doc.querySelector('.achievement-summary').textContent, /72 доступных/);
    assert.deepEqual(raidStorage(b.dom.window), before); assert.equal(b.raid.state, null);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech fork: redesigned reward glyphs, details and character view do not mutate or reroll equipment', async () => {
  const b = await openMagitech();
  try {
    const doc = b.dom.window.document;
    b.raid.state.hero.gold = 100; b.raid.checkGoldBox();
    const art = [...doc.querySelectorAll('button.shop-item .shop-icon .equipment-glyph')];
    assert.equal(art.length, 3);
    for (const node of art) assert.ok(node.tagName.toLowerCase() === 'svg' || (node.tagName === 'IMG' && /^techno-(rusted-blade|sabre|mace|hammer|axe|guard-blade)-v2$/.test(node.dataset.weaponArt) && node.dataset.weaponKey));
    const before = JSON.stringify(b.raid.state), details = doc.querySelector('.reward-details-toggle');
    assert.equal(details.getAttribute('aria-expanded'), 'false');
    details.click(); doc.querySelector('.shop-summary-toggle').click();
    assert.equal(b.dom.window.getComputedStyle(doc.querySelector('.shop-grid')).display, 'none');
    doc.querySelector('.shop-summary-toggle').click(); details.click();
    assert.equal(JSON.stringify(b.raid.state), before);
    doc.querySelector('button.shop-item').click();
    await until(() => !b.raid.state.pendingModal);
    b.raid.state.skills = ['freeze']; b.raid.state.skillCooldowns = { freeze: 0 }; b.raid.state.skillLevels = { freeze: 1 };
    b.raid.endEnemyTurn();
    assert.equal(doc.querySelectorAll('#skills .module-glyph').length, 1);
    assert.equal(doc.querySelector('#skills .skill-name').textContent, 'Мороз');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech fork: transforming an enemy updates the portrait on its existing UID node without inventing a new effect', async () => {
  const b = await openMagitech();
  try {
    const doc = b.dom.window.document;
    const skull = Object.assign(b.raid.makeSkull(false, true, 4), { hp: 50, maxHp: 50, atk: 0, def: 0 });
    b.raid.state.board[0] = { type: 'skull', skull };
    b.raid.state.skills = ['exorcise']; b.raid.state.skillCooldowns = { exorcise: 0 }; b.raid.state.skillLevels = { exorcise: 1 };
    b.raid.endEnemyTurn();
    const uid = skull.uid, image = doc.querySelector('.cell[data-idx="0"] img'), portrait = image.dataset.portrait;
    b.raid.activateSkill('exorcise');
    await until(() => !b.raid.state.resolving);
    assert.equal(skull.type, 0); assert.equal(skull.uid, uid);
    assert.equal(doc.querySelector('.cell[data-idx="0"] img'), image);
    assert.notEqual(image.dataset.portrait, portrait);
    assert.equal(skull.element, undefined); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech fork: SDK Close checkpoints the unconfirmed reward and explicit Continue restores it without ending the run', async () => {
  const sentinel = { 'raid:activeRun': 'classic browser sentinel', 'raid:tg:701:activeRun': 'classic account sentinel' };
  let stub = raidTelegramStub();
  let b = await openMagitech({ saved: sentinel, url: telegramRaidUrl, setup(w) { w.Telegram = { WebApp: stub.tg }; } });
  const snapshot = raid => JSON.parse(JSON.stringify([raid.state.hero, raid.state.board, raid.state.equipment, raid.state.classConfig,
    raid.state.rngSeeds, raid.state.menuCandidates, raid.state.levelDraft, raid.state.scrollLeechLabel]));
  try {
    const doc = b.dom.window.document;
    b.raid.state.hero.xp = 100; b.raid.checkLevelUp();
    doc.querySelectorAll('.lv-card')[1].click(); doc.querySelectorAll('.lv-card')[0].click();
    const expected = snapshot(b.raid);
    doc.querySelector('#menu-btn').click();
    assert.equal(doc.querySelector('#menu-close').textContent, 'Закрыть Mini App');
    doc.querySelector('#menu-close').click();
    assert.equal(stub.calls.filter(call => call === 'close').length, 1);
    assert.deepEqual(snapshot(b.raid), expected); assert.equal(b.raid.state.over, false);
    const checkpoint = JSON.parse(b.dom.window.localStorage.getItem(magitechAccountKey(701, 'raid:activeRun')));
    assert.equal(checkpoint.format, 'lunapark-raid-magitech-run'); assert.equal(checkpoint.status, 'active');
    stub = raidTelegramStub();
    b = await reopenMagitech(b, { url: telegramRaidUrl, setup(w) { w.Telegram = { WebApp: stub.tg }; } });
    assert.equal(b.raid.state, null);
    b.dom.window.document.querySelector('#picker-resume').click();
    assert.equal(b.raid.state.pendingModal, 'level'); assert.equal(b.raid.state.turn, 0);
    assert.deepEqual(snapshot(b.raid), expected); assert.deepEqual([...b.raid.state.levelDraft], [1, 0]);
    for (const [key, value] of Object.entries(sentinel)) assert.equal(b.dom.window.localStorage.getItem(key), value);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech fork: a failed SDK exit checkpoint requires explicit discard and rejects stale or repeated confirmations', async () => {
  const stub = raidTelegramStub();
  const b = await openMagitech({ url: telegramRaidUrl, setup(w) { w.Telegram = { WebApp: stub.tg }; } });
  try {
    const { window } = b.dom, doc = window.document, original = window.Storage.prototype.setItem;
    let fail = true;
    window.Storage.prototype.setItem = function(key, value) {
      if (fail && key === magitechAccountKey(701, 'raid:activeRun')) throw new Error('quota');
      return original.call(this, key, value);
    };
    const openExit = () => { doc.querySelector('#menu-btn').click(); doc.querySelector('#menu-close').focus(); doc.querySelector('#menu-close').click(); };
    for (const cancel of ['button', 'back', 'escape']) {
      openExit(); assert.equal(doc.querySelector('#exit-modal').classList.contains('show'), true);
      assert.match(doc.querySelector('#exit-confirm-text').textContent, /не удалось сохранить/i);
      assert.equal(doc.activeElement.id, 'exit-cancel');
      if (cancel === 'button') doc.querySelector('#exit-cancel').click();
      else if (cancel === 'back') stub.back();
      else doc.querySelector('#exit-modal').dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      assert.equal(doc.querySelector('#exit-modal').classList.contains('show'), false);
      assert.equal(doc.activeElement.id, 'menu-close'); assert.equal(stub.calls.includes('close'), false);
    }
    openExit(); fail = false; b.raid.newGame(2);
    doc.querySelector('#exit-confirm').click(); assert.equal(stub.calls.includes('close'), false);
    fail = true; openExit(); const before = JSON.stringify(b.raid.state);
    doc.querySelector('#exit-confirm').click(); doc.querySelector('#exit-confirm').click();
    assert.equal(stub.calls.filter(call => call === 'close').length, 1);
    assert.equal(JSON.stringify(b.raid.state), before); assert.equal(b.raid.state.over, false);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech fork: changing a valid SDK account blocks writes rather than overwriting either player or the classic trial', async () => {
  const sdk = localAccountSDK(71010), sentinel = { 'raid:activeRun': 'classic sentinel' };
  const b = await openMagitech({ saved: sentinel, url: testerRaidUrl, setup(w) { w.Telegram = { WebApp: sdk }; } });
  try {
    b.raid.saveRun();
    const otherKey = magitechAccountKey(71011, 'raid:activeRun');
    b.dom.window.localStorage.setItem(otherKey, 'other player sentinel');
    b.dom.window.dispatchEvent(new b.dom.window.StorageEvent('storage', { key: otherKey, newValue: 'other player sentinel' }));
    assert.equal(b.raid.saveStatus.state, 'saved');
    const before = raidStorage(b.dom.window);
    sdk.initDataUnsafe.user.id = 71011;
    assert.equal(b.raid.saveRun(), false);
    b.dom.window.document.querySelector('button[data-raid-region="fire"]').click();
    assert.deepEqual(raidStorage(b.dom.window), before);
    assert.match(b.dom.window.document.querySelector('#menu-profile-status').textContent, /смен|заново/);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

// Geometry + manually completed Web Animations for the GENERATED standalone.
// Real pixel/trajectory/touch checks additionally run in Chromium; jsdom does
// not lay out a grid or provide Element.animate by itself.
function orbAnimationHarness(window, { reduced = false, webAnimations = true } = {}) {
  const created = [], listeners = new Set();
  const media = { get matches() { return reduced; }, addEventListener(kind, fn) { listeners.add(fn); } };
  window.matchMedia = () => media;
  const nativeRect = window.Element.prototype.getBoundingClientRect;
  const rect = (x, y, width, height) => ({ x, y, left: x, top: y, right: x + width, bottom: y + height, width, height, toJSON() { return { x, y, width, height }; } });
  window.Element.prototype.getBoundingClientRect = function () {
    if (this.id === 'board' || this.id === 'board-fx') return rect(10, 10, 360, 360);
    const cell = this.matches('.cell') ? this : this.closest('.cell');
    if (cell) {
      const idx = Number(cell.dataset.idx), inset = this === cell ? 0 : 1.2;
      return rect(10 + idx % 6 * 60 + inset, 10 + Math.floor(idx / 6) * 60 + inset, 60 - inset * 2, 60 - inset * 2);
    }
    return nativeRect.call(this);
  };
  if (webAnimations) window.Element.prototype.animate = function (frames, options) {
    const animation = {
      target: this, frames: JSON.parse(JSON.stringify(frames)), options: { ...options }, playState: 'running',
      cancel() { if (this.playState === 'idle') return; this.playState = 'idle'; this.oncancel?.(); },
      finish() { if (this.playState !== 'running') return; this.playState = 'finished'; this.onfinish?.(); },
    };
    created.push(animation); return animation;
  };
  window.__orbTest = {
    created, finish() { for (const animation of [...created]) animation.finish(); },
    reduce(value) { reduced = value; for (const fn of listeners) fn({ matches: value }); },
  };
}
async function orbFixture(options = {}) {
  const b = await openMagitech({ start: false, setup(w) { orbAnimationHarness(w, options); } });
  b.dom.window.document.querySelector('.picker-card-btn:not(.locked)').click();
  b.raid.newGame(2718);
  return b;
}
function showOrbFixture(b, fill) {
  b.raid.state.board = Array.from({ length: 36 }, (_, idx) => fill(idx));
  b.raid.state.forceCoins = 1;
  assert.equal(b.raid.saveRun(), true); assert.equal(b.raid.resumeSavedRun(), true);
}
const orbAt = (b, slot) => b.dom.window.document.querySelector(`.cell[data-idx="${slot}"] > .raid-orb`);
const orbGameplay = b => JSON.stringify([b.raid.state.board, b.raid.state.hero, b.raid.state.rngSeeds, b.raid.state.arrivals, b.raid.state.rewardMenus, b.raid.state.menuCandidates, b.raid.state.turn, b.raid.state.chainCount]);

// No text labels are burnt into tiles. Accessible names/details are retained.
test('magitech orbs: every category is a circular animated glass orb; enemies show only numeric stats and countdown', async () => {
  const b = await orbFixture();
  try {
    const r = b.raid, doc = b.dom.window.document;
    const skull = Object.assign(r.makeSkull(false, true, 3), { atk: 0, hp: 24, maxHp: 24, countdown: 5 });
    showOrbFixture(b, idx => idx === 0 ? { type: 'skull', skull } : { type: ['sword', 'shield', 'potion', 'coin', 'arrow', 'chain', 'trophy'][idx % 7], ...(idx === 2 ? { mana: true } : {}) });
    const orbs = [...doc.querySelectorAll('.cell > .raid-orb')]; assert.equal(orbs.length, 36);
    assert.equal(b.dom.window.getComputedStyle(doc.querySelector('.board-wrap')).flexGrow, '0', 'log growth below the HUD must not recenter a falling board');
    for (const orb of orbs) {
      assert.equal(b.dom.window.getComputedStyle(orb).borderRadius, '50%');
      assert.ok(orb.querySelector('.orb-material')); assert.ok(orb.querySelector('.orb-ice svg'));
      assert.match(orb.dataset.state, /^(fire|gas|liquid|sand|solid|organic)$/);
      assert.doesNotMatch(orb.textContent, /[a-zа-яё]/iu, 'no Б/HP/type labels on the orb');
      assert.ok(orb.parentElement.getAttribute('aria-label'));
    }
    assert.equal(orbAt(b, 0).querySelector('.skull-badge').textContent, `0·${skull.def}·24`);
    assert.equal(orbAt(b, 0).querySelector('.orb-countdown').textContent, '5');
    assert.ok(doc.querySelector('.type-sword .orb-fog-wisp')); assert.ok(doc.querySelector('.type-arrow .orb-fog-wisp'));
    assert.equal(doc.querySelector('.orb-sigil'), null, 'elemental haze replaces the old corner badges');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech orbs: the real chain drops the SAME identical-looking nodes down actual vacancies, refilling only from above', async () => {
  const b = await orbFixture();
  try {
    showOrbFixture(b, idx => ({ type: [24, 30, 31].includes(idx) ? 'coin' : 'sword', ...(idx === 0 ? { broken: true, frozen: true } : {}) }));
    const r = b.raid, original = [orbAt(b, 0), orbAt(b, 6), orbAt(b, 1)], elements = original.map(n => n.dataset.element);
    const victims = [24, 30, 31].map(idx => orbAt(b, idx).dataset.orbId);
    [24, 30, 31].forEach(idx => r.tryExtendChain(idx)); r.resolveChain();
    assert.equal(b.dom.window.document.querySelectorAll('.primed').length, 3);
    assert.equal(r.state.hero.gold, 0, 'anticipation does not pay rewards');
    await until(() => r.state.hero.gold === 6);
    assert.equal(orbAt(b, 12), original[0]); assert.equal(orbAt(b, 18), original[1]); assert.equal(orbAt(b, 7), original[2]);
    assert.deepEqual([orbAt(b, 12), orbAt(b, 18), orbAt(b, 7)].map(n => n.dataset.element), elements);
    assert.ok(orbAt(b, 12).classList.contains('frozen')); assert.ok(orbAt(b, 12).classList.contains('broken'));
    const fall = b.dom.window.__orbTest.created.find(a => a.target === original[0]);
    assert.match(fall.frames[0].transform, /^translate\(0px,/);
    assert.ok(Math.abs(parseFloat(fall.frames[0].transform.split(',')[1]) + 120) < .001, 'preserve subpixel geometry, not an integer-only string');
    assert.equal(fall.frames.at(-1).transform, 'translate(0,0) scale(1)');
    const refill = b.dom.window.__orbTest.created.find(a => a.target === orbAt(b, 0));
    assert.equal(refill.frames[0].transform, 'translate(0px,-120px)'); assert.equal(orbAt(b, 0).dataset.motion, 'refill');
    for (const id of victims) assert.ok(b.dom.window.document.querySelector(`.orb-burst[data-orb-id="${id}"]`));
    const before = orbGameplay(b); b.dom.window.__orbTest.finish();
    assert.equal(orbGameplay(b), before, 'finishing all effects cannot pay a second reward or advance a turn');
    assert.equal(b.dom.window.document.querySelectorAll('.orb-burst,[data-motion],.cell.pop').length, 0);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech orbs: a lethal Kamikaze and its unselected blast victims burst; a surviving enemy keeps its UID image', async () => {
  const b = await orbFixture();
  try {
    const r = b.raid;
    const bomb = Object.assign(r.makeSkull(false, true, 10), { hp: 1, maxHp: 1, atk: 0, def: 0, enteredTurn: -1 });
    const survivor = Object.assign(r.makeSkull(false, false, 0), { hp: 99, maxHp: 99, atk: 0, def: 99, enteredTurn: -1 });
    showOrbFixture(b, idx => idx === 2 ? { type: 'skull', skull: bomb } : idx === 10 ? { type: 'skull', skull: survivor } : { type: idx < 2 ? 'sword' : 'potion' });
    const alive = orbAt(b, 10), img = alive.querySelector('img'), dying = orbAt(b, 2), dyingImg = dying.querySelector('img');
    const victims = [2, 3, 7, 8, 9].map(idx => orbAt(b, idx).dataset.orbId);
    [0, 1, 2].forEach(idx => r.tryExtendChain(idx)); r.resolveChain();
    await until(() => r.state.skullsKilled === 1);
    for (const id of victims) assert.ok(b.dom.window.document.querySelector(`.orb-burst[data-orb-id="${id}"]`), `burst ${id}`);
    assert.ok(alive.isConnected); assert.equal(alive.querySelector('img'), img);
    assert.equal(b.dom.window.getComputedStyle(dyingImg).width, '100%', 'a dying portrait must not revert to its intrinsic atlas size outside the hit socket');
    assert.equal(b.dom.window.getComputedStyle(dying.querySelector('.orb-core')).inset, '3% 2% 16%');
    assert.equal(b.dom.window.document.querySelector(`.orb-burst[data-orb-id="${alive.dataset.orbId}"]`), null);
    assert.equal(r.state.hero.hp, 50, 'on-kill blast is not the timeout HP penalty');
    b.dom.window.__orbTest.finish(); assert.equal(b.dom.window.document.querySelectorAll('.cell > .raid-orb').length, 36);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech orbs: the existing Freeze and thaw animate ice on the same UID, without changing their gameplay rules', async () => {
  const b = await orbFixture();
  try {
    const r = b.raid, skull = Object.assign(r.makeSkull(false, false, 0), { hp: 50, maxHp: 50, atk: 0, enteredTurn: -1 });
    showOrbFixture(b, idx => idx === 8 ? { type: 'skull', skull } : { type: 'coin' });
    const orb = orbAt(b, 8), img = orb.querySelector('img');
    r.activateSkill('freeze'); assert.equal(orbAt(b, 8), orb); assert.ok(orb.classList.contains('frozen'));
    const freeze = b.dom.window.__orbTest.created.find(a => a.target === orb.querySelector('.orb-ice'));
    assert.equal(freeze.frames[0].opacity, 0); assert.equal(freeze.frames.at(-1).opacity, 1);
    r.tryExtendChain(8); assert.deepEqual(Array.from(r.state.selected), [8], 'frozen enemies remain chainable');
    r.state.selected = []; r.state.chainType = null;
    b.dom.window.__orbTest.finish(); r.endEnemyTurn();
    assert.equal(orbAt(b, 8), orb); assert.equal(orb.querySelector('img'), img); assert.equal(orb.classList.contains('frozen'), false);
    const thaw = b.dom.window.__orbTest.created.filter(a => a.target === orb.querySelector('.orb-ice')).at(-1);
    assert.equal(thaw.frames[0].opacity, 1); assert.equal(thaw.frames.at(-1).opacity, 0);
    const before = orbGameplay(b); b.dom.window.__orbTest.finish(); assert.equal(orbGameplay(b), before);
    assert.equal(r.state.turn, 1); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech orbs: frozen resources remain unselectable, and Continue restores ice without replaying a freeze or changing sword palettes', async () => {
  let b = await orbFixture();
  try {
    showOrbFixture(b, idx => ({ type: idx === 0 ? 'coin' : 'sword', ...(idx === 0 ? { frozen: true } : {}) }));
    b.raid.tryExtendChain(0); assert.equal(b.raid.state.selected.length, 0);
    const looks = [...b.dom.window.document.querySelectorAll('.cell > .raid-orb')].map(n => n.dataset.element || null), before = orbGameplay(b);
    b.raid.saveRun(); b = await reopenMagitech(b, { setup: orbAnimationHarness }); b.dom.window.document.querySelector('#picker-resume').click();
    assert.equal(orbGameplay(b), before); assert.ok(orbAt(b, 0).classList.contains('frozen'));
    assert.deepEqual([...b.dom.window.document.querySelectorAll('.cell > .raid-orb')].map(n => n.dataset.element || null), looks);
    assert.equal(b.dom.window.__orbTest.created.length, 0, 'restore a settled picture, not old visual callbacks');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech orbs: resize, reduced-motion changes and restart cancel visual work, never game continuations or new-run nodes', async () => {
  const b = await orbFixture();
  try {
    showOrbFixture(b, idx => ({ type: [24, 30, 31].includes(idx) ? 'coin' : 'sword' }));
    b.raid.activateSkill('treasure');
    assert.ok(b.dom.window.document.querySelector('[data-motion]'));
    const before = orbGameplay(b); b.dom.window.dispatchEvent(new b.dom.window.Event('resize'));
    assert.equal(orbGameplay(b), before); assert.equal(b.dom.window.document.querySelectorAll('.orb-burst,[data-motion]').length, 0);
    assert.ok(b.dom.window.__orbTest.created.every(a => a.playState === 'idle'));
    b.dom.window.__orbTest.reduce(true); assert.equal(orbGameplay(b), before);
    b.dom.window.__orbTest.reduce(false);
    // The next fixture creates a second wave of effects while there is a run.
    b.raid.activateSkill('teleport'); const stale = [...b.dom.window.__orbTest.created].filter(a => a.playState === 'running');
    assert.ok(stale.length); b.raid.newGame(991);
    const fresh = orbAt(b, 0), state = orbGameplay(b);
    for (const animation of stale) animation.finish();
    assert.equal(orbAt(b, 0), fresh); assert.ok(fresh.isConnected); assert.equal(orbGameplay(b), state);
    assert.equal(b.dom.window.document.querySelectorAll('.orb-burst,[data-motion]').length, 0);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

for (const mode of ['reduced motion', 'no Web Animations']) test(`magitech orbs: ${mode} has no transient debris or invisible survivors and keeps normal reward/turn timing`, async () => {
  const b = await orbFixture({ reduced: mode === 'reduced motion', webAnimations: mode !== 'no Web Animations' });
  try {
    showOrbFixture(b, idx => ({ type: [24, 30, 31].includes(idx) ? 'coin' : 'sword' }));
    const survivor = orbAt(b, 0);
    [24, 30, 31].forEach(idx => b.raid.tryExtendChain(idx)); b.raid.resolveChain();
    await until(() => b.raid.state.turn === 1 && !b.raid.state.resolving);
    assert.equal(orbAt(b, 12), survivor); assert.equal(b.raid.state.hero.gold, 6); assert.equal(b.raid.state.chainCount, 1);
    assert.equal(b.dom.window.__orbTest.created.length, 0);
    assert.equal(b.dom.window.document.querySelectorAll('.orb-burst,[data-motion],.cell.pop').length, 0);
    assert.equal(b.dom.window.document.querySelectorAll('.cell > .raid-orb').length, 36);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech orbs: Continue during a fall preserves final positions/palettes and runs the pending enemy phase exactly once', async () => {
  let b = await orbFixture();
  try {
    showOrbFixture(b, idx => ({ type: [24, 30, 31].includes(idx) ? 'coin' : 'sword' }));
    [24, 30, 31].forEach(idx => b.raid.tryExtendChain(idx)); b.raid.resolveChain();
    await until(() => b.raid.state.hero.gold === 6);
    assert.ok(b.dom.window.document.querySelector('[data-motion]'));
    const before = orbGameplay(b), looks = [...b.dom.window.document.querySelectorAll('.cell > .raid-orb')].map(n => n.dataset.element || null);
    assert.equal(b.raid.saveRun(), true);
    b = await reopenMagitech(b, { setup: orbAnimationHarness });
    assert.equal(b.raid.state, null); b.dom.window.document.querySelector('#picker-resume').click();
    assert.equal(orbGameplay(b), before);
    assert.deepEqual([...b.dom.window.document.querySelectorAll('.cell > .raid-orb')].map(n => n.dataset.element || null), looks);
    assert.equal(b.dom.window.__orbTest.created.length, 0);
    await until(() => b.raid.state.turn === 1 && !b.raid.state.resolving);
    assert.equal(b.raid.state.hero.gold, 6); assert.equal(b.raid.state.chainCount, 1);
    assert.equal(b.dom.window.document.querySelectorAll('.cell > .raid-orb').length, 36);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});


test('magitech orbs: shuffle and an interrupted second move preserve every orb; stale callbacks cannot clear a newer motion', async () => {
  const b = await orbFixture();
  try {
    showOrbFixture(b, () => ({ type: 'sword' }));
    const r = b.raid, identities = new Map(r.state.board.map((cell, idx) => [cell, orbAt(b, idx)]));
    r.activateSkill('earthquake');
    r.state.board.forEach((cell, idx) => assert.equal(orbAt(b, idx), identities.get(cell)));
    const first = b.dom.window.__orbTest.created.filter(a => a.options.fill === 'both');
    assert.ok(first.length > 20); assert.ok(first.some(a => a.target.dataset.motion === 'shift'));
    const staleCallbacks = new Map(first.map(a => [a, a.onfinish]));
    r.state.skillCooldowns.earthquake = 0; r.activateSkill('earthquake');
    r.state.board.forEach((cell, idx) => assert.equal(orbAt(b, idx), identities.get(cell)));
    const newer = b.dom.window.__orbTest.created.filter(a => a.options.fill === 'both' && !first.includes(a));
    const next = newer.find(a => first.some(old => old.target === a.target));
    assert.ok(next); assert.equal(next.playState, 'running');
    const old = first.find(a => a.target === next.target); assert.equal(old.playState, 'idle');
    staleCallbacks.get(old)();
    assert.equal(next.playState, 'running'); assert.ok(next.target.dataset.motion); assert.ok(next.target.isConnected);
    const state = orbGameplay(b); b.dom.window.__orbTest.finish(); assert.equal(orbGameplay(b), state);
    assert.equal(b.dom.window.document.querySelectorAll('.orb-burst,[data-motion]').length, 0);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

// A shop offer and the currently worn item are different things. They share
// one item-aware renderer, not one universal image for the whole catalogue.
const weaponView = image => ({
  src: image.getAttribute('src'), art: image.dataset.weaponArt, key: image.dataset.weaponKey,
  finish: image.style.getPropertyValue('--weapon-finish'),
});
function assertWeaponAlpha(texture) {
  assert.match(texture, /^data:image\/webp;base64,/);
  const bytes = Buffer.from(texture.split(',')[1], 'base64');
  assert.equal(bytes.toString('ascii', 0, 4), 'RIFF'); assert.equal(bytes.toString('ascii', 8, 12), 'WEBP');
  assert.equal(bytes.toString('ascii', 12, 16), 'VP8X'); assert.ok(bytes[20] & 0x10, 'real alpha, not a painted background');
}
function showWeaponOffers(b, weapon) {
  const r = b.raid;
  r.state.pendingModal = 'gold'; r.state.menuCandidates = [weapon, r.makeItem('helmet'), r.makeItem('shield')];
  assert.equal(r.saveRun(), true); assert.equal(r.resumeSavedRun(), true);
}

test('magitech quiet art: an offer does not repaint worn swords; buying it updates the same images and Continue restores the bought weapon', async () => {
  let b = await orbFixture();
  try {
    let r = b.raid;
    showOrbFixture(b, idx => ({ type: 'sword', ...(idx === 1 ? { magicSword: true } : {}) }));
    const mace = Object.assign(r.makeItem('weapon'), { suffixIndex: 2, appearance: 37, name: 'Тяжёлая булава «Искра»' });
    showWeaponOffers(b, mace);
    let doc = b.dom.window.document;
    const worn = weaponView(orbAt(b, 0).querySelector('.weapon-texture'));
    const offered = weaponView(doc.querySelector('.shop-item[data-slot="weapon"] .weapon-texture'));
    assert.equal(worn.art, 'techno-rusted-blade-v2'); assert.equal(offered.art, 'techno-mace-v2');
    assert.notEqual(worn.src, offered.src); assertWeaponAlpha(worn.src); assertWeaponAlpha(offered.src);
    const before = JSON.stringify(r.state), rng = JSON.stringify(r.state.rngSeeds);
    doc.querySelector('.reward-details-toggle').click(); doc.querySelector('.shop-summary-toggle').click();
    doc.querySelector('.shop-summary-toggle').click(); doc.querySelector('.reward-details-toggle').click();
    assert.equal(JSON.stringify(r.state), before, 'preview/details cannot equip an offered item');
    assert.deepEqual(weaponView(orbAt(b, 0).querySelector('.weapon-texture')), worn);
    const pending = JSON.stringify(r.state.menuCandidates), oldEquipment = JSON.stringify(r.state.equipment);
    // Continue BEFORE buying must keep the old worn image and the distinct offer.
    b = await reopenMagitech(b); r = b.raid; doc = b.dom.window.document;
    doc.querySelector('#picker-resume').click();
    assert.equal(JSON.stringify(r.state.menuCandidates), pending); assert.equal(JSON.stringify(r.state.equipment), oldEquipment);
    assert.equal(JSON.stringify(r.state.rngSeeds), rng);
    assert.deepEqual(weaponView(orbAt(b, 0).querySelector('.weapon-texture')), worn);
    assert.deepEqual(weaponView(doc.querySelector('.shop-item[data-slot="weapon"] .weapon-texture')), offered);
    const nodes = Array.from({ length: 36 }, (_, i) => ({ orb: orbAt(b, i), image: orbAt(b, i).querySelector('.weapon-texture'), fog: orbAt(b, i).querySelector('.orb-fog') }));
    const elements = nodes.map(n => n.orb.dataset.element), damage = r.state.hero.weaponDamage;
    assert.deepEqual([...new Set(elements)].sort(), ['air','earth','fire','water']);
    doc.querySelector('.shop-item[data-slot="weapon"]').click();
    await until(() => !r.state.pendingModal && !r.state.resolving);
    assert.ok(r.state.hero.weaponDamage > damage); assert.equal(JSON.stringify(r.state.rngSeeds), rng, 'item art cannot add a purchase RNG draw');
    nodes.forEach(({ orb, image, fog }, idx) => {
      assert.equal(orbAt(b, idx), orb); assert.equal(orb.querySelector('.weapon-texture'), image); assert.equal(orb.querySelector('.orb-fog'), fog);
      assert.equal(orb.dataset.element, elements[idx]); assert.deepEqual(weaponView(image), offered);
      assert.equal(image.draggable, false); assert.equal(image.alt, ''); assert.equal(image.getAttribute('aria-hidden'), 'true');
    });
    assert.equal(orbAt(b, 1).querySelector('.orb-charge').textContent, '5');
    assert.equal(doc.querySelectorAll('.orb-burst,[data-motion],.primed').length, 0, 'buying gear is not a new orb or fall');
    const board = JSON.stringify(r.state.board), gear = JSON.stringify(r.state.equipment), palettes = JSON.parse(b.dom.window.localStorage.getItem(magitechKey('raid:activeRun'))).orbElements;
    b = await reopenMagitech(b); r = b.raid; doc = b.dom.window.document; doc.querySelector('#picker-resume').click();
    assert.equal(JSON.stringify(r.state.board), board); assert.equal(JSON.stringify(r.state.equipment), gear); assert.equal(JSON.stringify(r.state.rngSeeds), rng);
    assert.deepEqual(JSON.parse(b.dom.window.localStorage.getItem(magitechKey('raid:activeRun'))).orbElements, palettes);
    for (const image of doc.querySelectorAll('.type-sword .weapon-texture')) assert.deepEqual(weaponView(image), offered);
    assert.equal(doc.querySelectorAll('.cell > .raid-orb').length, 36); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech quiet art: worn weapon upgrades and other-slot purchases preserve its model, finish, broken/frozen/magic overlays and RNG', async () => {
  const b = await orbFixture();
  try {
    const r = b.raid;
    const axe = Object.assign(r.makeItem('weapon'), { suffixIndex: 4, appearance: 73, name: 'Топор палача «Дозор»' });
    r.equipItem(axe);
    showOrbFixture(b, idx => ({ type: 'sword', ...(idx === 0 ? { broken: true, frozen: true } : idx === 1 ? { magicSword: true } : {}) }));
    r.state.pendingModal = 'armor'; r.state.menuCandidates = ['weapon','helmet','armour','shield'].map(slot => r.makeUpgradeOffer(slot));
    assert.equal(r.saveRun(), true); assert.equal(r.resumeSavedRun(), true);
    const doc = b.dom.window.document, image = orbAt(b, 0).querySelector('.weapon-texture'), art = weaponView(image);
    assert.equal(art.art, 'techno-axe-v2'); assertWeaponAlpha(art.src);
    assert.deepEqual(weaponView(doc.querySelector('.lv-card[data-slot="weapon"] .weapon-texture')), art);
    assert.deepEqual(weaponView(orbAt(b, 1).querySelector('.weapon-texture')), art);
    assert.equal(orbAt(b, 1).querySelector('.orb-charge').textContent, '5');
    assert.equal(b.dom.window.getComputedStyle(image).opacity, '0.45');
    for (const wisp of orbAt(b, 0).querySelectorAll('.orb-fog-wisp')) assert.equal(b.dom.window.getComputedStyle(wisp).animationPlayState, 'paused');
    assert.equal(r.canChainSelect(0), false);
    const before = JSON.stringify(r.state), rng = JSON.stringify(r.state.rngSeeds), original = orbAt(b, 0), fog = original.querySelector('.orb-fog');
    doc.querySelector('.reward-details-toggle').click(); doc.querySelector('.shop-summary-toggle').click();
    assert.equal(JSON.stringify(r.state), before); doc.querySelector('.shop-summary-toggle').click();
    const offer = r.state.menuCandidates.find(o => o.slot === 'weapon'), oldValue = r.state.equipment[0].mods[offer.key] || 0;
    doc.querySelector('.lv-card[data-slot="weapon"]').click(); await until(() => !r.state.pendingModal);
    assert.equal(r.state.equipment[0].mods[offer.key], oldValue + offer.value); assert.equal(r.state.equipment[0].appearance, 73);
    assert.equal(JSON.stringify(r.state.rngSeeds), rng); assert.equal(orbAt(b, 0), original); assert.equal(original.querySelector('.weapon-texture'), image); assert.equal(original.querySelector('.orb-fog'), fog);
    assert.deepEqual(weaponView(image), art); assert.ok(original.classList.contains('broken') && original.classList.contains('frozen'));
    // A real non-weapon purchase must not repaint the worn axe either.
    r.state.pendingModal = 'gold'; r.state.menuCandidates = ['helmet','shield','accessory'].map(slot => r.makeItem(slot));
    assert.equal(r.saveRun(), true); assert.equal(r.resumeSavedRun(), true);
    const current = orbAt(b, 1), currentImage = current.querySelector('.weapon-texture'), nextRng = JSON.stringify(r.state.rngSeeds);
    doc.querySelector('.shop-item[data-slot="helmet"]').click(); await until(() => !r.state.pendingModal);
    assert.equal(orbAt(b, 1), current); assert.equal(current.querySelector('.weapon-texture'), currentImage);
    assert.deepEqual(weaponView(currentImage), art); assert.equal(JSON.stringify(r.state.rngSeeds), nextRng);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech weapon catalogue: all six existing weapon names have distinct art; seven actual purchases update every sword in place, including a same-model finish change', async () => {
  const b = await orbFixture();
  try {
    const r = b.raid, doc = b.dom.window.document;
    showOrbFixture(b, () => ({ type: 'sword' }));
    const kinds = ['rusted-blade','sabre','mace','hammer','axe','guard-blade'];
    const names = ['Ржавый клинок','Кривая сабля','Тяжёлая булава','Колючий молот','Топор палача','Клинок стражи'];
    const sources = new Set(); let last;
    for (let n = 0; n < 7; n++) {
      const kind = Math.min(n, 5), appearance = n === 6 ? 63 : 7 + n * 11;
      const item = Object.assign(r.makeItem('weapon'), { suffixIndex: kind, appearance, name: names[kind] + ' «Искра»' });
      showWeaponOffers(b, item);
      const offered = weaponView(doc.querySelector('.shop-item[data-slot="weapon"] .weapon-texture'));
      assert.equal(offered.art, 'techno-' + kinds[kind] + '-v2'); assertWeaponAlpha(offered.src);
      const nodes = [...doc.querySelectorAll('.type-sword .weapon-texture')], old = weaponView(nodes[0]);
      assert.equal(nodes.length, 36); assert.notEqual(offered.key, old.key);
      if (n === 6) { assert.equal(offered.src, last.src); assert.notEqual(offered.finish, last.finish); }
      const rng = JSON.stringify(r.state.rngSeeds), elements = [...doc.querySelectorAll('.cell > .raid-orb')].map(n => n.dataset.element);
      doc.querySelector('.shop-item[data-slot="weapon"]').click(); await until(() => !r.state.pendingModal && !r.state.resolving);
      assert.equal(JSON.stringify(r.state.rngSeeds), rng);
      nodes.forEach((image, idx) => { assert.equal(orbAt(b, idx).querySelector('.weapon-texture'), image); assert.deepEqual(weaponView(image), offered); assert.equal(orbAt(b, idx).dataset.element, elements[idx]); });
      assert.ok(orbAt(b, 0).parentElement.getAttribute('aria-label').includes(names[kind]));
      sources.add(offered.src); last = offered;
    }
    assert.equal(sources.size, 6, 'a mace/hammer/axe cannot all be the same blade texture');
    assert.equal(r.state.turn, 0); assert.equal(r.state.chainCount, 0); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech weapon fog: four elements occupy the lower two thirds of neutral glass as gas, not full-shell colours or sigil badges', async () => {
  const b = await orbFixture();
  try {
    showOrbFixture(b, idx => ({ type: idx === 35 ? 'arrow' : 'sword', ...(idx === 0 ? { frozen: true } : {}) }));
    const doc = b.dom.window.document, orbs = [...doc.querySelectorAll('.cell > .raid-orb')], before = JSON.stringify(b.raid.state);
    assert.deepEqual([...new Set(orbs.map(n => n.dataset.element))].sort(), ['air','earth','fire','water']);
    assert.equal(new Set(orbs.map(n => n.style.getPropertyValue('--orb-rgb'))).size, 4);
    for (const property of ['--orb-light','--orb-mid','--orb-dark']) assert.equal(new Set(orbs.map(n => n.style.getPropertyValue(property))).size, 1, 'neutral glass stays the same around four haze colours');
    for (const orb of orbs) {
      assert.equal(orb.dataset.state, 'gas'); assert.equal(orb.querySelector('.orb-sigil'), null);
      const fog = orb.querySelector('.orb-material.orb-fog'); assert.ok(fog);
      assert.equal(b.dom.window.getComputedStyle(fog).height, '66.6667%');
      assert.equal(fog.querySelectorAll('.orb-fog-wisp').length, 2); assert.equal(fog.getAttribute('aria-hidden'), 'true');
      for (const wisp of fog.children) {
        assert.match(b.dom.window.getComputedStyle(wisp).animation, /orb-fog-(?:drift|return) (?:24|30)s ease-in-out infinite/);
        if (orb.classList.contains('frozen')) assert.equal(b.dom.window.getComputedStyle(wisp).animationPlayState, 'paused');
      }
      assert.doesNotMatch(orb.textContent, /[a-zа-яё]/iu);
    }
    assert.equal(JSON.stringify(b.raid.state), before, 'haze rendering adds no element, RNG or saved item fields');
    b.raid.tryExtendChain(0); assert.equal(b.raid.state.selected.length, 0);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});
