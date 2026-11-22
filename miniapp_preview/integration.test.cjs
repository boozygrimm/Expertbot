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
async function openRaid() {
  const errors = [], console = new VirtualConsole();
  console.on('jsdomError', error => errors.push(error.message));
  const html = require('node:fs').readFileSync(require('node:path').join(root, 'lunapark-raid.html'), 'utf8');
  const dom = new JSDOM(html, {
    url: 'https://raid.test/', runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: console,
  });
  await until(() => dom.window.document.querySelector('.picker-card-btn:not(.locked)'));
  dom.window.document.querySelector('.picker-card-btn:not(.locked)').click();
  const raid = dom.window.RAID;
  raid.newGame(0);
  raid.state.board = Array.from({ length: 36 }, () => ({ type: 'sword' }));
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
