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
async function openRaid({ saved = {}, start = true, setup } = {}) {
  const errors = [], console = new VirtualConsole();
  console.on('jsdomError', error => errors.push(error.message));
  const html = require('node:fs').readFileSync(require('node:path').join(root, 'lunapark-raid.html'), 'utf8');
  const dom = new JSDOM(html, {
    url: 'https://raid.test/', runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: console,
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
