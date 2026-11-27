const { test } = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM, VirtualConsole } = require('jsdom');
const root = require('node:path').resolve(__dirname, '..');

async function until(predicate, timeout = 7000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  throw new Error('Condition not met before timeout');
}

// Use the GENERATED deliverable, including real CSS. Source-only DOM doubles
// cannot detect display:grid overriding the browser's built-in [hidden] rule.
async function openRaid({ saved = {}, start = true, setup, url = 'https://raid.test/', variant = 'classic', dismissUpdates = true } = {}) {
  const errors = [], console = new VirtualConsole();
  console.on('jsdomError', error => errors.push(error.message));
  const artifact = variant === 'magitech' ? 'lunapark-raid-magitech.html' : 'lunapark-raid.html';
  const html = require('node:fs').readFileSync(require('node:path').join(root, artifact), 'utf8');
  const dom = new JSDOM(html, {
    url, runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: console,
    beforeParse(window) {
      window.HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
      window.HTMLMediaElement.prototype.pause = function () {};
      for (const [key, value] of Object.entries(saved)) window.localStorage.setItem(key, value);
      if (setup) setup(window);
    },
  });
  await until(() => dom.window.document.querySelector('.picker-card-btn:not(.locked)'));
  // Existing gameplay scenarios explicitly acknowledge the read-only notice;
  // update-notice regressions opt out and exercise the real first-entry flow.
  if (dismissUpdates && dom.window.document.querySelector('#updates-modal.show')) dom.window.document.querySelector('#updates-close').click();
  const raid = dom.window.RAID;
  if (start) {
    dom.window.document.querySelector('.picker-card-btn:not(.locked)').click();
    raid.newGame(0);
    // Explicit deterministic weapon assignments for the injected magitech-v2
    // board; classic fixtures keep their original released shape.
    raid.state.board = Array.from({ length: 36 }, (_, idx) => ({ type: 'sword', ...(variant === 'magitech' ? { element: ['fire','air','earth','water'][idx % 4] } : {}) }));
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
function launchMagitechMech(doc, classId = 'adventurer', region = null) {
  doc.querySelector(`.picker-card-btn[data-class-id="${classId}"]`).click();
  doc.querySelector('.mech-go-button').click();
  if (doc.querySelector('#restart-modal.show')) return;
  if (region) doc.querySelector(`button[data-raid-region="${region}"]`).click();
  doc.querySelector('#region-emblem-launch').click();
}
const magitechElements = ['fire', 'air', 'earth', 'water'];
// Explicit RELEASED-v1 fixture, only in tests. Production never downgrades a v2
// save or imports a classic one. Remove ALL new gameplay metadata, not just v2.
function releasedMagitechCheckpoint(value) {
  const data = JSON.parse(JSON.stringify(value)); data.version = 1; delete data.combat;
  for (const cell of data.state.board) {
    delete cell.element;
    if (cell.skull) { delete cell.skull.element; delete cell.skull.secondaryElement; }
  }
  return data;
}

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
  let js = fs.readFileSync(path.join(root, 'miniapp_preview/raid_magitech.js'), 'utf8');
  const css = fs.readFileSync(path.join(root, 'miniapp_preview/raid_magitech.css'), 'utf8');
  for (const [source, mime] of [['lich_phylactery.png', 'image/png'], ['raid_trophy.png', 'image/png']]) {
    const encoded = fs.readFileSync(path.join(root, 'miniapp_preview/assets', source)).toString('base64');
    js = js.replace(`'assets/${source}'`, `'data:${mime};base64,${encoded}'`);
  }
  assert.ok(html.includes(js.replaceAll('</', '<\\/')));
  assert.doesNotMatch(html, /__(?:RAID_BUILD|RAID_SCENE_\w+)__|\/\*__(?:SCRIPT|STYLE)__\*\//);
  assert.match(css, /\[hidden\]\s*\{\s*display:\s*none\s*!important/);
  assert.match(css, /\.raid-intro\s*\{[^}]*flex:\s*0 0 auto;/, 'mobile introduction grows with its notice, never clips it to a fixed flex basis');
  const landscape = css.slice(css.indexOf('@media (max-height: 610px) and (min-width: 721px)'));
  assert.match(landscape, /\.raid-intro-copy\s*\{\s*display:\s*none;/, 'short landscape prioritizes the location controls over the headline');
  const sprites = JSON.parse(fs.readFileSync(path.join(root, 'miniapp_preview/raid_monsters.json'), 'utf8'));
  const catalog = sprites.special_catalog, ready = catalog.ready_types;
  assert.deepEqual(catalog.expected_types, Array.from({ length: 31 }, (_, i) => i + 2));
  assert.deepEqual(ready, [...new Set(ready)].sort((a, b) => a - b));
  assert.equal(catalog.complete, catalog.expected_types.every(type => ready.includes(type)), 'staging cannot masquerade as all 31 special skins');
  assert.equal(Object.keys(sprites.portraits).length, 16 + 4 * ready.length);
  const original = crypto.createHash('sha256'); let originalCount = 0;
  for (const [id, image] of Object.entries(sprites.portraits)) {
    assert.match(image, /^data:image\/webp;base64,/);
    const bytes = Buffer.from(image.split(',')[1], 'base64');
    assert.equal(bytes.toString('ascii', 0, 4), 'RIFF'); assert.equal(bytes.toString('ascii', 8, 12), 'WEBP');
    const source = sprites.sources[id];
    if (!source.generated) {
      assert.ok(source.file.endsWith('.jpg')); originalCount++;
      original.update(id + '\0' + image + '\0');
    } else {
      assert.equal(id, `${source.element}_special_${String(source.enemy_type).padStart(2, '0')}`);
      assert.ok(ready.includes(source.enemy_type)); assert.equal(source.mechanics_changed, false);
      assert.deepEqual(source.size, [96, 96]); assert.ok(sprites.special_templates[source.template].image);
    }
  }
  const previousVariants = Object.fromEntries(Object.entries(sprites.portraits).filter(([key]) => sprites.sources[key].generated && sprites.sources[key].enemy_type <= 21));
  const previousTemplates = Object.fromEntries(Object.entries(sprites.special_templates).filter(([key]) => Number(key) <= 21));
  for (const [entries, expected, count] of [
    [previousVariants, '512b9409775e4b0a99aa55fed6a13afd6775fed88d7047b4951ebfce204af454', 80],
    [previousTemplates, '26dfa1f1134f5233a9a16362935e1b8ed019369dbe133ebe51ab051cc60b18bc', 20],
  ]) {
    assert.equal(Object.keys(entries).length, count);
    const digest = crypto.createHash('sha256');
    for (const [key, value] of Object.entries(entries)) digest.update(key + '\0' + JSON.stringify(value) + '\0');
    assert.equal(digest.digest('hex'), expected, 'previously accepted templates/palettes/variants stay byte-identical');
  }
  assert.deepEqual(ready, catalog.expected_types, 'all 31 unique special types must now be ready, including Lich32');
  assert.equal(catalog.complete, true);
  const allPrevious = {
    portraits: Object.fromEntries(Object.entries(sprites.portraits).filter(([key]) => !key.endsWith('_special_32'))),
    sources: Object.fromEntries(Object.entries(sprites.sources).filter(([key]) => !key.endsWith('_special_32'))),
    templates: Object.fromEntries(Object.entries(sprites.special_templates).filter(([key]) => key !== '32')),
    palettes: sprites.special_palettes,
  };
  for (const [section, expected, count] of [
    ['portraits', '1adcc2cd55efb0d8f9584d37dbabab8cd40f51a0d4c7d99693a9fa83314af62d', 136],
    ['sources', '3a1fcdfb3e987487802e4730e4403721c99153d5642930727691b3ab71ac65ef', 136],
    ['templates', '0b32b1adae9701609ed3a64f799d63886dd298bb7f9b3358691a6a955cc43a1b', 30],
    ['palettes', '29480bbee0b92e4e07b9d1c0635db3e8720f59570be4e29cd90eaed2d2d29f77', 4],
  ]) {
    assert.equal(Object.keys(allPrevious[section]).length, count);
    const digest = crypto.createHash('sha256');
    for (const [key, value] of Object.entries(allPrevious[section])) digest.update(key + '\0' + JSON.stringify(value) + '\0');
    assert.equal(digest.digest('hex'), expected, 'PR131 art, source records and palettes must not be regenerated');
  }
  assert.equal(originalCount, 16);
  assert.equal(original.digest('hex'), 'cbcc769edcee82eb1627effa2ba085f675cbdb342fb11e377dad2a82cd918cc1', 'all 16 approved temporary crops stay byte-identical');
  for (const element of ['fire', 'air', 'earth', 'water']) {
    const images = ready.map(type => sprites.portraits[`${element}_special_${String(type).padStart(2, '0')}`]);
    assert.equal(new Set(images).size, ready.length, 'every READY type has a distinct portrait, not a renamed duplicate');
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
    launchMagitechMech(b.dom.window.document);
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
    launchMagitechMech(b.dom.window.document);
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

test('magitech fork: pre-run appearance previews are cosmetic and persist only in the separate profile', async () => {
  let b = await openMagitech({ start: false });
  try {
    const doc = b.dom.window.document, before = JSON.stringify(b.raid.state), classicKeys = Object.keys(raidStorage(b.dom.window)).filter(k => !k.startsWith('raid:magitech:'));
    doc.querySelector('.picker-card-btn:not(.locked)').click(); doc.querySelector('.mech-go-button').click();
    for (const region of ['fire', 'air', 'earth', 'water']) {
      doc.querySelector(`button[data-raid-region="${region}"]`).click();
      assert.equal(doc.documentElement.dataset.raidRegion, region);
      assert.equal(doc.querySelector(`button[data-raid-region="${region}"]`).getAttribute('aria-pressed'), 'true');
      assert.equal(JSON.stringify(b.raid.state), before);
    }
    assert.equal(b.dom.window.localStorage.getItem(magitechKey('raid:appearance')), 'water');
    assert.deepEqual(Object.keys(raidStorage(b.dom.window)).filter(k => !k.startsWith('raid:magitech:')), classicKeys);
    doc.querySelector('#region-back-button').click();
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
          assert.ok(magitechElements.includes(y.element));
          if (type === 1) assert.ok(magitechElements.includes(y.secondaryElement) && y.secondaryElement !== y.element);
          const neutral = JSON.parse(JSON.stringify(y)); delete neutral.element; delete neutral.secondaryElement;
          assert.deepEqual(neutral, JSON.parse(JSON.stringify(x)), `${difficulty}/${turn}/${type}`);
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

test('magitech fork: assigned portraits retain the same UID node and next-run preview cannot change combat elements', async () => {
  const b = await openMagitech({ start: false });
  try {
    const doc = b.dom.window.document;
    launchMagitechMech(doc);
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
    for (const enemy of b.raid.state.board.filter(c => c.type === 'skull')) assert.equal(enemy.skull.element, 'earth');
    assert.equal(b.raid.baseDamage(), 3); assert.equal(b.raid.swordDamage(3), 9);
    assert.match(doc.querySelector('#raid-prototype-note').textContent, /Стихийный бой — в новых забегах/);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});


test('magitech fork: every inherited profile key and appearance is namespaced without touching either classic profile', async () => {
  const names = ['class','classLevels','classConfigs','mode','difficulty','trophyRemaining','activeRun','history'];
  const sentinel = Object.fromEntries(names.flatMap(name => [[`raid:${name}`, `classic browser ${name}`], [`raid:tg:71001:${name}`, `classic account ${name}`]]));
  for (const account of [null, 71001]) {
    const b = await openMagitech({ saved: sentinel, start: false, ...(account ? { url: testerRaidUrl + '#raid_legacy_owner=71001', setup(w) { w.Telegram = { WebApp: localAccountSDK(account) }; } } : {}) });
    try {
      launchMagitechMech(b.dom.window.document, 'adventurer', 'air');
      const key = name => account ? magitechAccountKey(account, 'raid:' + name) : magitechKey('raid:' + name);
      b.raid.unlockClass('mage');
      assert.equal(b.raid.saveClassConfig('mage', b.raid.defaultClassConfig('mage')), true);
      b.raid.setSelectedClassId('mage');
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
    const uid = skull.uid, element = skull.element, image = doc.querySelector('.cell[data-idx="0"] img'), portrait = image.dataset.portrait;
    b.raid.activateSkill('exorcise');
    await until(() => !b.raid.state.resolving);
    assert.equal(skull.type, 0); assert.equal(skull.uid, uid);
    assert.equal(doc.querySelector('.cell[data-idx="0"] img'), image);
    assert.notEqual(image.dataset.portrait, portrait);
    assert.equal(skull.element, element); assert.equal(image.closest('.raid-orb').dataset.element, element); assert.deepEqual(b.errors, []);
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
      target: this, frames: JSON.parse(JSON.stringify(frames)), options: { ...options }, playState: 'running', startTime: null,
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
  const doc = b.dom.window.document;
  doc.querySelector('.picker-card-btn:not(.locked)').click();
  if (options.region) {
    doc.querySelector('.mech-go-button').click();
    doc.querySelector(`button[data-raid-region="${options.region}"]`).click();
    doc.querySelector('#region-back-button').click();
  }
  b.raid.newGame(2718);
  return b;
}
function showOrbFixture(b, fill) {
  // Deterministic explicit gameplay assignments for these injected DOM fixtures,
  // NOT palette-hash inference/production generation. Tests may override them.
  b.raid.state.board = Array.from({ length: 36 }, (_, idx) => {
    const cell = fill(idx);
    if (['sword', 'arrow'].includes(cell.type) && !Object.hasOwn(cell, 'element')) cell.element = magitechElements[idx % 4];
    return cell;
  });
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
    assert.ok(b.dom.window.document.querySelector('#board-fx-canvas'), 'canvas FX layer mounts for primed chain surge');
    assert.equal(r.state.hero.gold, 0, 'anticipation does not pay rewards');
    await until(() => r.state.hero.gold === 6);
    assert.equal(orbAt(b, 12), original[0]); assert.equal(orbAt(b, 18), original[1]); assert.equal(orbAt(b, 7), original[2]);
    assert.deepEqual([orbAt(b, 12), orbAt(b, 18), orbAt(b, 7)].map(n => n.dataset.element), elements);
    assert.ok(orbAt(b, 12).classList.contains('frozen')); assert.ok(orbAt(b, 12).classList.contains('broken'));
    const fall = b.dom.window.__orbTest.created.find(a => a.target === original[0]);
    assert.match(fall.frames[0].transform, /^translate3d\(0,/);
    assert.ok(Math.abs(parseFloat(fall.frames[0].transform.split(',')[1]) + 120) < .001, 'preserve subpixel geometry, not an integer-only string');
    assert.equal(fall.frames.at(-1).transform, 'translate3d(0,0,0)');
    assert.equal(fall.frames.length, 2, 'single continuous compositor fall without mid-flight stalls');
    const refill = b.dom.window.__orbTest.created.find(a => a.target === orbAt(b, 0));
    assert.equal(refill.frames[0].transform, 'translate3d(0,-120px,0)');
    assert.equal(refill.frames.length, 2);
    assert.equal(orbAt(b, 0).dataset.motion, 'refill');
    for (const id of victims) {
      const burst = b.dom.window.document.querySelector(`.orb-burst[data-orb-id="${id}"]`);
      assert.ok(burst);
      assert.equal(burst.children.length, 1, 'lightweight 1-node DOM ghost; sparks/rings render on canvas');
    }
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
      const nodes = [...doc.querySelectorAll('.cell.type-sword')].map(cell => cell.querySelector('.weapon-texture')).filter(Boolean), old = weaponView(nodes[0]);
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

test('magitech weapon fog: selection-only water/air keep their two-thirds geometry, without permanent filling or idle animation', async () => {
  const b = await orbFixture();
  try {
    showOrbFixture(b, idx => ({ type: idx === 35 ? 'arrow' : 'sword', ...(idx < 4 ? { frozen: true } : {}) }));
    const key = magitechKey('raid:activeRun'), checkpoint = JSON.parse(b.dom.window.localStorage.getItem(key));
    checkpoint.orbElements = Array.from({ length: 36 }, (_, idx) => (idx + 2) % 4); // Deliberately disagrees with assigned elements.
    b.dom.window.localStorage.setItem(key, JSON.stringify(checkpoint)); assert.equal(b.raid.resumeSavedRun(), true);
    const doc = b.dom.window.document, orbs = [...doc.querySelectorAll('.cell > .raid-orb')], before = JSON.stringify(b.raid.state);
    assert.deepEqual([...new Set(orbs.map(n => n.dataset.element))].sort(), ['air','earth','fire','water']);
    assert.equal(new Set(orbs.map(n => n.style.getPropertyValue('--orb-rgb'))).size, 4);
    for (const property of ['--orb-light','--orb-mid','--orb-dark']) assert.equal(new Set(orbs.map(n => n.style.getPropertyValue(property))).size, 1, 'neutral glass stays the same around four haze colours');
    for (const orb of orbs) {
      assert.equal(orb.dataset.state, 'gas'); assert.equal(orb.querySelector('.orb-sigil'), null);
      const fog = orb.querySelector('.orb-material.orb-fog'); assert.ok(fog);
      const fogStyle = b.dom.window.getComputedStyle(fog), air = orb.dataset.element === 'air', water = orb.dataset.element === 'water';
      assert.equal(fogStyle.opacity, '0', 'the former body is hidden until selection');
      assert.equal(fogStyle.animationPlayState, 'paused');
      assert.equal(fogStyle.height, '66.6667%'); assert.equal(fogStyle.inset, air ? '0 0 auto' : 'auto 0 0');
      if (air) assert.equal(fogStyle.maskImage, 'linear-gradient(to bottom,#000 70%,transparent)');
      if (water) assert.equal(fogStyle.maskImage, 'none');
      assert.equal(fog.querySelectorAll('.orb-fog-wisp').length, 2); assert.equal(fog.getAttribute('aria-hidden'), 'true');
      for (const wisp of fog.children) {
        const style = b.dom.window.getComputedStyle(wisp), back = wisp.classList.contains('orb-fog-back');
        if (water) {
          assert.equal(style.animationName, back ? 'orb-water-return' : 'orb-water-slosh');
          assert.equal(style.animationDuration, back ? '11s' : '8s'); assert.match(style.maskImage, /^url\("data:image\/svg\+xml/);
        } else {
          assert.match(style.animation, /orb-fog-(?:drift|return) (?:24|30)s ease-in-out infinite/);
          if (air) assert.equal(style.animationDuration, back ? '19s' : '14s');
        }
        if (orb.classList.contains('frozen')) assert.equal(b.dom.window.getComputedStyle(wisp).animationPlayState, 'paused');
      }
      assert.doesNotMatch(orb.textContent, /[a-zа-яё]/iu);
    }
    assert.equal(JSON.stringify(b.raid.state), before, 'haze rendering adds no element, RNG or saved item fields');
    b.raid.tryExtendChain(0); assert.equal(b.raid.state.selected.length, 0);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});


test('magitech item motion: only coin/content has slow 3D rotation; frozen items pause, shop images stay static and keyed images survive repaint', async () => {
  const b = await orbFixture();
  try {
    showOrbFixture(b, idx => ({ type: idx % 2 ? 'sword' : 'coin', ...(idx < 2 ? { frozen: true } : {}) }));
    const doc = b.dom.window.document, before = orbGameplay(b), nodes = [...doc.querySelectorAll('.cell > .raid-orb')];
    const images = nodes.map(n => n.querySelector('.weapon-texture,.coin-3d'));
    nodes.forEach((orb, idx) => {
      const core = orb.querySelector('.orb-core'), item = images[idx], motion = idx % 2 ? item.closest('.weapon-art') : item, style = b.dom.window.getComputedStyle(motion);
      assert.equal(b.dom.window.getComputedStyle(core).perspective, '260px');
      assert.match(style.animation, idx % 2 ? /weapon-idle-sway 7\.2s ease-in-out infinite/ : /coin-axial-turn 16s linear infinite/);
      assert.equal(b.dom.window.getComputedStyle(orb).transform, '');
      assert.equal(b.dom.window.getComputedStyle(orb).animation, '', 'glass itself does not rotate or pulse');
      if (idx < 2) assert.equal(style.animationPlayState, 'paused');
      if (idx % 2) assert.equal(item.draggable, false);
      else {
        assert.equal(item.getAttribute('aria-hidden'), 'true'); assert.equal(item.querySelectorAll('.coin-face').length, 2);
        assert.equal(item.querySelectorAll('.coin-edge').length, 2); assert.equal(item.querySelectorAll('svg').length, 2);
        assert.equal(b.dom.window.getComputedStyle(item).transformStyle, 'preserve-3d');
        assert.equal(b.dom.window.getComputedStyle(item.querySelector('.coin-face')).backfaceVisibility, 'hidden');
        assert.equal(b.dom.window.getComputedStyle(item.querySelector('.coin-reverse')).transform, 'rotateY(180deg) translateZ(2px)');
        assert.equal(b.dom.window.getComputedStyle(item.querySelector('.coin-edge-belt')).transform, 'rotateY(90deg)');
      }
    });
    b.raid.tryExtendChain(3); b.raid.tryExtendChain(9); b.raid.tryExtendChain(15);
    nodes.forEach((orb, idx) => { assert.equal(orbAt(b, idx), orb); assert.equal(orb.querySelector('.weapon-texture,.coin-3d'), images[idx]); });
    b.raid.state.selected = []; b.raid.state.chainType = null;
    assert.equal(orbGameplay(b), before, 'painting/selection motion cannot pay rewards, draw RNG or add board fields');
    showWeaponOffers(b, b.raid.makeItem('weapon'));
    const offer = doc.querySelector('.shop-item[data-slot="weapon"] .weapon-texture'); assert.ok(offer);
    assert.equal(b.dom.window.getComputedStyle(offer).animation, '', 'readable static catalogue, same item model/finish as before');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech sound control: the existing checkbox is a keyboard-focusable popup switch without synthetic audio activation', async () => {
  let contexts = 0;
  const b = await openMagitech({ setup(w) { w.AudioContext = class { constructor() { contexts++; } }; } });
  try {
    const doc = b.dom.window.document, input = doc.querySelector('#sound-on');
    assert.equal(input.hidden, false); assert.equal(input.type, 'checkbox'); assert.equal(input.checked, true);
    assert.equal(input.getAttribute('aria-label'), 'Звуковые эффекты');
    doc.querySelector('#settings-btn').click();
    assert.ok(doc.querySelector('#game-settings').contains(input)); assert.equal(input.getAttribute('role'), 'switch');
    input.focus(); assert.equal(doc.activeElement, input);
    input.click(); assert.equal(input.checked, false);
    input.click(); assert.equal(input.checked, true);
    doc.querySelector('#settings-close').click();
    doc.dispatchEvent(new b.dom.window.Event('pointerdown')); doc.dispatchEvent(new b.dom.window.KeyboardEvent('keydown', { key: ' ' }));
    b.raid.tryExtendChain(0); b.raid.tryExtendChain(1); b.raid.tryExtendChain(2);
    assert.equal(contexts, 0, 'jsdom/synthetic inputs must not masquerade as a trusted user activation');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});


test('magitech material SFX: SDK deactivation silences current clips and drops new picks until activation, without replay or gameplay changes', async () => {
  const stub = raidTelegramStub(), played = [], paused = []; let unlock;
  const b = await openMagitech({ url: testerRaidUrl, setup(w) {
    w.Telegram = { WebApp: stub.tg };
    const listen = w.document.addEventListener.bind(w.document);
    w.document.addEventListener = (name, fn, options) => {
      if (name === 'pointerdown' && fn.name === 'unlockMaterialAudio') unlock = () => fn({ isTrusted: true });
      return listen(name, fn, options);
    };
    w.HTMLMediaElement.prototype.play = function () { if (this.id === 'raid-coin-taken') played.push(this); return Promise.resolve(); };
    w.HTMLMediaElement.prototype.pause = function () { if (this.id === 'raid-coin-taken') paused.push(this); };
  } });
  try {
    showOrbFixture(b, () => ({ type: 'coin' }));
    const before = orbGameplay(b); assert.equal(typeof unlock, 'function'); assert.equal(played.length, 0);
    unlock(); [0,1,2].forEach(i => b.raid.tryExtendChain(i)); assert.ok(played.length > 0);
    const playedBeforeDeactivate = played.length;
    stub.fire('deactivated'); assert.ok(paused.length >= 1, 'deactivation pauses and resets the active material clip');
    assert.equal(b.raid.state.selected.length, 0, 'a pause drops the half-built chain');
    // Пока приложение в хосте, ввод не принадлежит игре.
    b.raid.tryExtendChain(3); assert.equal(played.length, playedBeforeDeactivate);
    assert.equal(b.raid.state.selected.length, 0, 'input while paused cannot start a chain');
    stub.fire('activated'); assert.equal(played.length, playedBeforeDeactivate, 'returning to the host is silent, not a replay queue');
    b.raid.tryExtendChain(4); assert.ok(played.length > playedBeforeDeactivate, 'a fresh pick may sound after reactivation');
    assert.equal(orbGameplay(b), before, 'SFX/host lifecycle cannot award resources, move the turn or draw RNG');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech material SFX: potion chains play bottle open on first tile and bottle clink on chain commit', async () => {
  const stub = raidTelegramStub(), played = [], paused = []; let unlock;
  const b = await openMagitech({ url: testerRaidUrl, setup(w) {
    w.Telegram = { WebApp: stub.tg };
    const listen = w.document.addEventListener.bind(w.document);
    w.document.addEventListener = (name, fn, options) => {
      if (name === 'pointerdown' && fn.name === 'unlockMaterialAudio') unlock = () => fn({ isTrusted: true });
      return listen(name, fn, options);
    };
    w.HTMLMediaElement.prototype.play = function () {
      if (this.id === 'raid-bottle-clink' || this.id === 'raid-bottle-open') played.push(this.id);
      return Promise.resolve();
    };
    w.HTMLMediaElement.prototype.pause = function () {
      if (this.id === 'raid-bottle-clink' || this.id === 'raid-bottle-open') paused.push(this.id);
    };
  } });
  try {
    const doc = b.dom.window.document;
    assert.match(doc.querySelector('#raid-bottle-clink').getAttribute('src'), /^data:audio\/mpeg;base64,/);
    assert.match(doc.querySelector('#raid-bottle-open').getAttribute('src'), /^data:audio\/mpeg;base64,/);
    showOrbFixture(b, () => ({ type: 'potion' }));
    unlock();
    [0, 1, 2].forEach(i => b.raid.tryExtendChain(i));
    assert.deepEqual(played, ['raid-bottle-open'], 'first potion tile plays openbottle once per chain');
    b.raid.resolveChain();
    assert.deepEqual(played, ['raid-bottle-open', 'raid-bottle-clink'], 'committed potion chain plays clinkbottle');
    paused.length = 0;
    stub.fire('deactivated');
    assert.ok(paused.includes('raid-bottle-clink') && paused.includes('raid-bottle-open'), 'deactivation stops both potion clips');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech material SFX: shield chains play trimmed firstshield on first tile and sborshield on chain commit', async () => {
  const stub = raidTelegramStub(), played = [], paused = []; let unlock;
  const b = await openMagitech({ url: testerRaidUrl, setup(w) {
    w.Telegram = { WebApp: stub.tg };
    const listen = w.document.addEventListener.bind(w.document);
    w.document.addEventListener = (name, fn, options) => {
      if (name === 'pointerdown' && fn.name === 'unlockMaterialAudio') unlock = () => fn({ isTrusted: true });
      return listen(name, fn, options);
    };
    w.HTMLMediaElement.prototype.play = function () {
      if (this.id === 'raid-shield-first' || this.id === 'raid-shield-collect') played.push(this.id);
      return Promise.resolve();
    };
    w.HTMLMediaElement.prototype.pause = function () {
      if (this.id === 'raid-shield-first' || this.id === 'raid-shield-collect') paused.push(this.id);
    };
  } });
  try {
    const doc = b.dom.window.document;
    assert.match(doc.querySelector('#raid-shield-first').getAttribute('src'), /^data:audio\/mpeg;base64,/);
    assert.match(doc.querySelector('#raid-shield-collect').getAttribute('src'), /^data:audio\/mpeg;base64,/);
    showOrbFixture(b, () => ({ type: 'shield' }));
    unlock();
    [0, 1, 2].forEach(i => b.raid.tryExtendChain(i));
    assert.deepEqual(played, ['raid-shield-first'], 'first shield tile plays firstshield once per chain');
    b.raid.resolveChain();
    assert.deepEqual(played, ['raid-shield-first', 'raid-shield-collect'], 'committed shield chain plays sborshield');
    paused.length = 0;
    stub.fire('deactivated');
    assert.ok(paused.includes('raid-shield-first') && paused.includes('raid-shield-collect'), 'deactivation stops both shield clips');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech material SFX: resource-collecting skills play chain activation clips for shields, potions and coins', async () => {
  const stub = raidTelegramStub(), played = []; let unlock;
  const b = await openMagitech({ url: testerRaidUrl, setup(w) {
    w.Telegram = { WebApp: stub.tg };
    const listen = w.document.addEventListener.bind(w.document);
    w.document.addEventListener = (name, fn, options) => {
      if (name === 'pointerdown' && fn.name === 'unlockMaterialAudio') unlock = () => fn({ isTrusted: true });
      return listen(name, fn, options);
    };
    w.HTMLMediaElement.prototype.play = function () {
      if (['raid-shield-collect', 'raid-bottle-clink', 'raid-coin-drop'].includes(this.id)) played.push(this.id);
      return Promise.resolve();
    };
  } });
  try {
    const r = b.raid;
    unlock();
    for (const [skillId, tileType, expectedClip] of [
      ['repair', 'shield', 'raid-shield-collect'],
      ['heal', 'potion', 'raid-bottle-clink'],
      ['elixir', 'potion', 'raid-bottle-clink'],
      ['treasure', 'coin', 'raid-coin-drop'],
    ]) {
      played.length = 0;
      r.state.skills = [skillId]; r.state.skillCooldowns = {}; r.state.pendingModal = null; r.state.resolving = false;
      showOrbFixture(b, i => i === 0 ? { type: tileType } : { type: 'sword' });
      r.activateSkill(skillId);
      assert.deepEqual(played, [expectedClip], `${skillId} plays ${expectedClip}`);
    }
    played.length = 0;
    r.state.skills = ['shock']; r.state.skillCooldowns = {}; r.state.pendingModal = null; r.state.resolving = false; r.state.shockCombo = 0; r.state.aweCombo = 0;
    showOrbFixture(b, i => i === 0 ? { type: 'shield' } : i === 1 ? { type: 'coin' } : { type: 'sword' });
    r.activateSkill('shock');
    assert.ok(played.includes('raid-shield-collect') && played.includes('raid-coin-drop'), 'shock plays shield and coin chain clips');
    played.length = 0;
    r.state.skills = ['awe']; r.state.skillCooldowns = {}; r.state.pendingModal = null; r.state.resolving = false; r.state.shockCombo = 0; r.state.aweCombo = 0;
    showOrbFixture(b, i => i === 18 ? { type: 'shield' } : i === 19 ? { type: 'potion' } : { type: 'sword' });
    r.activateSkill('awe');
    assert.ok(played.includes('raid-shield-collect') && played.includes('raid-bottle-clink'), 'awe plays shield and potion chain clips');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech battle console HUD: shows full mech, synced weapon/shield art with overlaid stats, vertical tubes, hides log, and renames enchant to Апгрейд', async () => {
  const b = await openMagitech();
  try {
    const doc = b.dom.window.document, r = b.raid;
    assert.equal(doc.querySelector('#log').hidden, true, 'battle log is hidden');
    assert.deepEqual([...doc.querySelectorAll('.hud-console > div')].map(n => n.id || n.className), ['hud-weapon', 'hud-hp', 'hud-armor', 'hud-tubes-zone'], 'HUD order is Weapon -> Mech -> Shield -> Tubes');
    assert.ok(doc.querySelector('.board-foot #hud-trophy'), 'trophy badge lives in board-foot without overlapping HUD tubes');
    assert.ok(doc.querySelector('#hud-gold .hud-coin-3d'), 'spinning 3D coin sits directly above the gold stack column');
    assert.ok(doc.querySelector('#hud-upgrade-icon svg[data-skill-art="enchant"]'), 'upgrade tube uses the enchant skill medallion icon');
    assert.equal(doc.querySelector('#hud-mech-art').getAttribute('src'), `miniapp_preview/assets/mech_${r.state.classId}.png`);
    assert.match(doc.querySelector('#hud-mech-name').textContent, /Ур\. 1/);
    assert.ok(doc.querySelector('#hud-weapon-art').getAttribute('src').startsWith('data:image/webp;base64,'));
    assert.equal(doc.querySelector('#hud-weapon-glint').getAttribute('src'), doc.querySelector('#hud-weapon-art').getAttribute('src'), 'weapon glint matches equipped weapon art');
    assert.match(doc.querySelector('#hud-shield-art').getAttribute('src'), /miniapp_preview\/assets\/raid_shield_\d\.png/);
    assert.equal(doc.querySelector('#hud-weapon-val').textContent, String(r.state.hero.weaponDamage));
    assert.match(doc.querySelector('#hud-weapon-base').textContent, /\+\d+ база/);
    assert.match(doc.querySelector('#armor-text').textContent, /\d+ \/ \d+/);
    assert.match(doc.querySelector('#hp-bar').style.height, /%$/);
    assert.match(doc.querySelector('#xp-bar').style.height, /%$/);
    assert.match(doc.querySelector('#upgrade-bar').style.height, /%$/);
    assert.match(doc.querySelector('#gold-bar').style.height, /%$/);
    // Switching weapon/shield updates the HUD art, wide-weapon fit, and triggers gear-swap pulse.
    const weaponItem = r.state.equipment.find(e => e.slot === 'weapon');
    const shieldItem = r.state.equipment.find(e => e.slot === 'shield');
    r.equipItem({ ...weaponItem, suffixIndex: 3, appearance: 14 });
    r.equipItem({ ...shieldItem, suffixIndex: 2, appearance: 162 });
    // Skill enchant is renamed to "Апгрейд" and triggers renderHUD/renderSkills on cast.
    r.state.skills = ['enchant']; r.state.skillCooldowns = {}; r.state.pendingModal = null; r.state.resolving = false;
    r.activateSkill('enchant');
    assert.equal(doc.querySelector('#hud-weapon-stage').dataset.weaponWide, '1', 'hammer/axe/mace flagged as wide weapon');
    assert.equal(doc.querySelector('#hud-weapon-glint').dataset.weaponArt, 'techno-hammer-v2');
    assert.equal(doc.querySelector('#hud-shield-art').getAttribute('src'), 'miniapp_preview/assets/raid_shield_2.png');
    assert.equal(doc.querySelector('[data-skill-id="enchant"] .skill-name').textContent, 'Апгрейд');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

// Compact controls and immutable per-run scenery, exercised on the built page.
test('magitech compact header: settings replace sound without a fourth action; help and confirmed restart still work from the menu', async () => {
  const b = await openMagitech();
  try {
    const doc = b.dom.window.document, before = JSON.stringify(b.raid.state);
    assert.deepEqual([...doc.querySelectorAll('.brand-actions > *')].map(n => n.id), ['settings-btn', 'stats-btn', 'menu-btn']);
    assert.equal(doc.querySelector('.skills-head .run-actions'), null);
    assert.equal(doc.querySelector('.shell .raid-run-heading'), null);
    assert.equal(doc.querySelector('.shell #help'), null); assert.equal(doc.querySelector('.shell #restart'), null);
    assert.equal(doc.querySelectorAll('#menu-modal button[data-raid-region]').length, 0);
    assert.equal(doc.querySelectorAll('.skill').length, 4);
    doc.querySelector('#stats-btn').click(); assert.equal(b.raid.state.pendingModal, 'stats');
    doc.querySelector('#modal-body .btn').click();
    doc.querySelector('#menu-btn').click(); doc.querySelector('#menu-help').click();
    assert.ok(doc.querySelector('#beginner-guide').classList.contains('show'));
    doc.querySelector('#guide-close').click(); assert.equal(doc.activeElement.id, 'menu-help');
    assert.equal(JSON.stringify(b.raid.state), before);
    doc.querySelector('#menu-btn').click(); doc.querySelector('#menu-restart').click();
    assert.ok(doc.querySelector('#restart-modal').classList.contains('show'));
    doc.querySelector('#restart-cancel').click(); assert.equal(JSON.stringify(b.raid.state), before);
    doc.querySelector('#menu-restart').click(); doc.querySelector('#restart-confirm').click();
    assert.equal(b.raid.state.turn, 0); assert.equal(b.raid.state.over, false);
    assert.equal(doc.querySelector('#restart-modal').classList.contains('show'), false);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech location: hidden controls cannot change a run; next-run previews do not repaint its shell or existing orbs', async () => {
  const b = await openMagitech({ start: false });
  try {
    const doc = b.dom.window.document;
    launchMagitechMech(doc, 'adventurer', 'fire');
    const state = b.raid.state, before = JSON.stringify(state), orbs = [...doc.querySelectorAll('.raid-orb')];
    const saved = b.dom.window.localStorage.getItem(magitechKey('raid:activeRun'));
    assert.equal(doc.documentElement.dataset.raidRegion, 'fire');
    doc.querySelector('button[data-raid-region="water"]').click();
    assert.equal(doc.documentElement.dataset.raidRegion, 'fire');
    assert.equal(b.dom.window.localStorage.getItem(magitechKey('raid:appearance')), 'fire');
    assert.equal(b.dom.window.localStorage.getItem(magitechKey('raid:activeRun')), saved);
    doc.querySelector('#menu-btn').click(); doc.querySelector('#menu-classes').click();
    doc.querySelector('.picker-card-btn[data-class-id="adventurer"]').click(); doc.querySelector('.mech-go-button').click();
    const checkpoint = b.dom.window.localStorage.getItem(magitechKey('raid:activeRun'));
    for (const region of ['water', 'earth', 'air']) {
      doc.querySelector(`button[data-raid-region="${region}"]`).click();
      assert.equal(doc.querySelector('#class-picker').dataset.raidRegion, region);
      assert.equal(doc.documentElement.dataset.raidRegion, 'fire');
      assert.equal(doc.querySelector('.brand [data-raid-region-title]').textContent, 'Пылающая кузня');
      assert.equal(doc.querySelector('.raid-menu-location').textContent, 'Пылающая кузня');
      assert.equal(b.raid.state, state); assert.equal(JSON.stringify(state), before);
      assert.deepEqual([...doc.querySelectorAll('.raid-orb')], orbs);
      assert.equal(b.dom.window.localStorage.getItem(magitechKey('raid:activeRun')), checkpoint);
    }
    doc.querySelector('#picker-resume').click();
    assert.equal(doc.documentElement.dataset.raidRegion, 'fire');
    assert.equal(b.raid.saveRun(), true);
    assert.equal(JSON.parse(b.dom.window.localStorage.getItem(magitechKey('raid:activeRun'))).region, 'fire');
    // Only a confirmed replacement starts the selected next location.
    doc.querySelector('#menu-btn').click(); doc.querySelector('#menu-classes').click();
    doc.querySelector('.picker-card-btn[data-class-id="adventurer"]').click(); doc.querySelector('.mech-go-button').click();
    doc.querySelector('button[data-raid-region="air"]').click(); doc.querySelector('#region-emblem-launch').click();
    assert.ok(doc.querySelector('#restart-modal').classList.contains('show'));
    doc.querySelector('button[data-raid-region="water"]').dispatchEvent(new b.dom.window.MouseEvent('click', { bubbles: true }));
    assert.equal(doc.querySelector('#class-picker').dataset.raidRegion, 'air', 'a pending confirmation rejects stale location events');
    doc.querySelector('#restart-cancel').click();
    assert.equal(b.raid.state, state); assert.equal(doc.documentElement.dataset.raidRegion, 'fire');
    doc.querySelector('#region-emblem-launch').click(); doc.querySelector('#restart-confirm').click();
    assert.notEqual(b.raid.state, state); assert.equal(doc.documentElement.dataset.raidRegion, 'air');
    assert.equal(JSON.parse(b.dom.window.localStorage.getItem(magitechKey('raid:activeRun'))).region, 'air');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

for (const sdk of [false, true]) test(`magitech location: ${sdk ? 'SDK Close' : 'browser reload'} and Continue restore the saved location, not the latest preview, without rerolling rewards`, async () => {
  const stub = raidTelegramStub();
  const host = sdk ? { url: telegramRaidUrl, setup(w) { w.Telegram = { WebApp: stub.tg }; } } : {};
  const key = name => sdk ? magitechAccountKey(701, 'raid:' + name) : magitechKey('raid:' + name);
  const sentinel = { 'raid:activeRun': 'classic browser', 'raid:tg:701:activeRun': 'classic account' };
  let b = await openMagitech({ ...host, saved: sentinel, start: false });
  try {
    let doc = b.dom.window.document;
    launchMagitechMech(doc, 'adventurer', 'water');
    doc.querySelector('#menu-btn').click(); doc.querySelector('#menu-classes').click();
    doc.querySelector('.picker-card-btn[data-class-id="adventurer"]').click(); doc.querySelector('.mech-go-button').click();
    doc.querySelector('button[data-raid-region="air"]').click(); doc.querySelector('#region-back-button').click(); doc.querySelector('#picker-resume').click();
    b.raid.state.hero.xp = 100; b.raid.checkLevelUp(); doc.querySelectorAll('.lv-card')[1].click();
    b.raid.saveRun(); const checkpoint = JSON.parse(b.dom.window.localStorage.getItem(key('activeRun')));
    assert.equal(checkpoint.region, 'water');
    assert.equal(b.dom.window.localStorage.getItem(key('appearance')), 'air');
    if (sdk) { doc.querySelector('#menu-btn').click(); doc.querySelector('#menu-close').click(); assert.ok(stub.calls.includes('close')); }
    b = await reopenMagitech(b, host); doc = b.dom.window.document;
    assert.equal(b.raid.state, null);
    doc.querySelector('button[data-raid-region="earth"]').click();
    assert.equal(JSON.parse(b.dom.window.localStorage.getItem(key('activeRun'))).region, 'water');
    doc.querySelector('#picker-resume').click();
    assert.equal(doc.documentElement.dataset.raidRegion, 'water');
    assert.equal(doc.querySelector('.brand [data-raid-region-title]').textContent, 'Затопленный храм');
    const restored = JSON.parse(b.dom.window.localStorage.getItem(key('activeRun')));
    for (const name of ['state', 'rng', 'combat', 'menu', 'results', 'arrivals', 'next', 'orbElements']) assert.deepEqual(restored[name], checkpoint[name], name);
    assert.equal(b.raid.state.pendingModal, 'level'); assert.deepEqual([...b.raid.state.levelDraft], [1]);
    assert.equal(b.raid.state.over, false);
    for (const [name, value] of Object.entries(sentinel)) assert.equal(b.dom.window.localStorage.getItem(name), value);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech location: a released v1 fallback survives a different preview and closing before Continue, without silently rewriting the old save', async () => {
  let b = await openMagitech({ start: false });
  try {
    let doc = b.dom.window.document;
    launchMagitechMech(doc, 'adventurer', 'water');
    b.raid.saveRun(); const saved = raidStorage(b.dom.window), old = releasedMagitechCheckpoint(JSON.parse(saved[magitechKey('raid:activeRun')]));
    delete old.region; saved[magitechKey('raid:activeRun')] = JSON.stringify(old);
    b.dom.window.close(); b = await openMagitech({ saved, start: false }); doc = b.dom.window.document;
    doc.querySelector('button[data-raid-region="fire"]').click(); b.raid.savedRunInfo(); b.raid.savedRunInfo();
    assert.equal(b.dom.window.localStorage.getItem(magitechKey('raid:activeRun')), saved[magitechKey('raid:activeRun')]);
    assert.equal(b.dom.window.localStorage.getItem(magitechKey('raid:appearance')), 'water', 'an unpinned legacy fallback cannot be overwritten by a next-run preview');
    b = await reopenMagitech(b); doc = b.dom.window.document;
    assert.equal(doc.querySelector('#class-picker').dataset.raidRegion, 'water');
    doc.querySelector('.picker-card-btn[data-class-id="adventurer"]').click(); doc.querySelector('.mech-go-button').click();
    doc.querySelector('button[data-raid-region="air"]').click(); doc.querySelector('#region-back-button').click(); doc.querySelector('#picker-resume').click();
    assert.equal(doc.documentElement.dataset.raidRegion, 'water');
    const restored = JSON.parse(b.dom.window.localStorage.getItem(magitechKey('raid:activeRun')));
    assert.equal(restored.region, 'water'); assert.equal(b.dom.window.localStorage.getItem(magitechKey('raid:appearance')), 'air');
    for (const name of ['state', 'rng', 'menu', 'results', 'arrivals', 'next', 'orbElements']) assert.deepEqual(restored[name], old[name], name);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});


test('magitech clear tiles: every idle category is transparent; only the selected path gets material and undo removes it immediately', async () => {
  const b = await orbFixture();
  try {
    const r = b.raid, doc = b.dom.window.document, style = node => b.dom.window.getComputedStyle(node);
    const skull = Object.assign(r.makeSkull(false, true, 3), { hp: 50, maxHp: 50, atk: 0 });
    const resources = { 1: 'shield', 2: 'potion', 3: 'coin', 4: 'arrow', 5: 'trophy', 35: 'chain' };
    showOrbFixture(b, idx => idx === 0 ? { type: 'skull', skull } : { type: resources[idx] || 'sword' });
    const nodes = [...doc.querySelectorAll('.cell > .raid-orb')];
    assert.equal(nodes.length, 36);
    assert.deepEqual([...new Set(nodes.map(n => n.dataset.type))].sort(), ['arrow','chain','coin','potion','shield','skull','sword','trophy']);
    for (const orb of nodes) {
      assert.ok(['', 'none'].includes(style(orb).backgroundImage), 'jsdom may serialize background:none as an empty image');
      assert.equal(style(orb).backgroundColor, 'rgba(0, 0, 0, 0)');
      assert.equal(style(orb.querySelector('.orb-material')).opacity, '0');
      assert.equal(style(orb.querySelector('.orb-material')).animationPlayState, 'paused');
    }
    const board = JSON.stringify(r.state.board), rng = JSON.stringify(r.state.rngSeeds), equipment = JSON.stringify(r.state.equipment);
    r.tryExtendChain(6); r.tryExtendChain(7); r.tryExtendChain(8);
    assert.deepEqual(Array.from(r.state.selected), [6,7,8]);
    for (const idx of [6,7,8]) {
      const orb = orbAt(b, idx);
      assert.equal(orb, nodes[idx]); assert.equal(style(orb.querySelector('.orb-fog')).opacity, '0.26');
      assert.equal(style(orb.querySelector('.orb-fog-wisp')).animationPlayState, 'running');
      assert.equal(style(orb).outline, '2px solid #ffebaa'); // jsdom keeps the outline shorthand
    }
    assert.equal(style(orbAt(b, 9).querySelector('.orb-material')).opacity, '0');
    r.tryExtendChain(6); // actual backtracking, not merely clearing CSS in the test
    assert.deepEqual(Array.from(r.state.selected), [6]);
    for (const idx of [7,8]) {
      assert.equal(orbAt(b, idx), nodes[idx]);
      assert.equal(style(nodes[idx]).backgroundColor, 'rgba(0, 0, 0, 0)');
      assert.equal(style(nodes[idx].querySelector('.orb-fog')).opacity, '0');
      assert.equal(style(nodes[idx].querySelector('.orb-fog-wisp')).animationPlayState, 'paused');
    }
    assert.equal(JSON.stringify(r.state.board), board); assert.equal(JSON.stringify(r.state.rngSeeds), rng);
    assert.equal(JSON.stringify(r.state.equipment), equipment); assert.equal(r.state.turn, 0); assert.equal(r.state.chainCount, 0);
    assert.equal(doc.querySelectorAll('.orb-burst,[data-motion]').length, 0); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech clear weapons: assigned metal colours ignore cosmetic overrides and preserve item/element identity through selection, purchase and Continue', async () => {
  let b = await orbFixture();
  try {
    let r = b.raid, doc = b.dom.window.document;
    showOrbFixture(b, () => ({ type: 'sword' }));
    const key = magitechKey('raid:activeRun'), checkpoint = JSON.parse(b.dom.window.localStorage.getItem(key));
    checkpoint.orbElements = Array.from({ length: 36 }, (_, idx) => (idx + 2) % 4); // Deliberately disagrees with assigned elements.
    b.dom.window.localStorage.setItem(key, JSON.stringify(checkpoint)); assert.equal(r.resumeSavedRun(), true);
    const style = node => b.dom.window.getComputedStyle(node);
    const nodes = [...doc.querySelectorAll('.cell > .raid-orb')], images = nodes.map(n => n.querySelector('.weapon-texture'));
    const tones = nodes.slice(0,4).map(n => style(n).getPropertyValue('--weapon-tone'));
    assert.equal(new Set(tones).size, 4); assert.ok(tones.every(Boolean));
    const edges = nodes.slice(0,4).map(n => style(n).getPropertyValue('--weapon-edge'));
    assert.equal(new Set(edges).size, 4);
    for (const image of images) {
      assert.match(style(image).filter, /drop-shadow\(-\.65px -\.65px 0 var\(--weapon-edge/);
      assert.match(style(image).filter, /drop-shadow\(1px 1px 0 #020b11\)/);
    }
    const rng = JSON.stringify(r.state.rngSeeds), board = JSON.stringify(r.state.board);
    r.tryExtendChain(0); r.tryExtendChain(1); r.tryExtendChain(2); r.tryExtendChain(0);
    for (let idx = 0; idx < 36; idx++) {
      assert.equal(orbAt(b, idx), nodes[idx]); assert.equal(orbAt(b, idx).querySelector('.weapon-texture'), images[idx]);
      assert.equal(style(nodes[idx]).getPropertyValue('--weapon-tone'), tones[idx % 4]);
    }
    assert.equal(JSON.stringify(r.state.rngSeeds), rng); assert.equal(JSON.stringify(r.state.board), board);
    r.state.selected = []; r.state.chainType = null;
    const mace = Object.assign(r.makeItem('weapon'), { suffixIndex: 2, appearance: 37, name: 'Тяжёлая булава «Искра»' });
    showWeaponOffers(b, mace);
    const offered = weaponView(doc.querySelector('.shop-item[data-slot="weapon"] .weapon-texture'));
    const current = [...doc.querySelectorAll('.cell > .raid-orb')], currentImages = current.map(n => n.querySelector('.weapon-texture'));
    const purchaseRng = JSON.stringify(r.state.rngSeeds);
    doc.querySelector('.shop-item[data-slot="weapon"]').click(); await until(() => !r.state.pendingModal && !r.state.resolving);
    for (let idx = 0; idx < 36; idx++) {
      assert.equal(orbAt(b, idx), current[idx]); assert.equal(orbAt(b, idx).querySelector('.weapon-texture'), currentImages[idx]);
      assert.deepEqual(weaponView(currentImages[idx]), offered);
      assert.equal(style(current[idx]).getPropertyValue('--weapon-tone'), tones[idx % 4]);
      assert.equal(style(current[idx].querySelector('.orb-fog')).opacity, '0');
    }
    assert.equal(JSON.stringify(r.state.rngSeeds), purchaseRng); assert.equal(JSON.stringify(r.state.board), board);
    assert.equal(r.state.turn, 0); assert.equal(r.state.chainCount, 0);
    assert.ok(r.state.board.every((cell, idx) => cell.element === magitechElements[idx % 4]), 'purchase changes model, never the assigned gameplay element');
    b = await reopenMagitech(b); r = b.raid; doc = b.dom.window.document; doc.querySelector('#picker-resume').click();
    const restored = [...doc.querySelectorAll('.cell > .raid-orb')];
    restored.forEach((orb, idx) => {
      const computed = b.dom.window.getComputedStyle(orb);
      assert.equal(computed.getPropertyValue('--weapon-tone'), tones[idx % 4]);
      assert.ok(['', 'none'].includes(computed.backgroundImage)); assert.equal(computed.backgroundColor, 'rgba(0, 0, 0, 0)');
      assert.deepEqual(weaponView(orb.querySelector('.weapon-texture')), offered);
      assert.equal(b.dom.window.getComputedStyle(orb.querySelector('.orb-fog')).opacity, '0');
    });
    assert.equal(JSON.stringify(r.state.rngSeeds), purchaseRng); assert.equal(JSON.stringify(r.state.board), board);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});


for (const region of ['fire', 'air', 'earth', 'water']) test(`magitech monster readability: ${region} ordinary skin is shared; special/boss portraits show stable assigned elements through preview and Continue`, async () => {
  let b = await orbFixture({ region });
  const names = { fire: 'fire_spark', air: 'air_spirit', earth: 'earth_golem', water: 'water_elemental' };
  try {
    let r = b.raid, doc = b.dom.window.document;
    showOrbFixture(b, idx => {
      const type = idx < 4 ? 0 : idx === 4 ? 1 : idx - 3;
      return { type: 'skull', skull: Object.assign(r.makeSkull(type === 1, type > 1, type), { atk: 0, hp: 50, maxHp: 50 }) };
    });
    const before = JSON.stringify(r.state), beforeRng = JSON.stringify(r.state.rngSeeds);
    const images = [...doc.querySelectorAll('.skull-art')], portraits = images.map(n => n.dataset.portrait), sources = images.map(n => n.src);
    assert.equal(images.length, 36);
    assert.equal(new Set(r.state.board.slice(0, 4).map(c => (c.skull.uid - 1) % 4)).size, 4, 'exercise all old UID-based elemental families');
    assert.deepEqual(portraits.slice(0, 4), Array(4).fill(names[region]));
    assert.equal(new Set(sources.slice(0, 4)).size, 1, 'ordinary means ONE identical skin, not four related ones');
    for (const [idx, orb] of [...doc.querySelectorAll('.cell > .raid-orb')].entries()) {
      assert.equal(orb.dataset.element, r.state.board[idx].skull.element);
      assert.ok(portraits[idx].startsWith(r.state.board[idx].skull.element + '_'));
    }
    const elements = Array.from(r.state.board, cell => [cell.skull.element, cell.skull.secondaryElement]);
    assert.ok(new Set(elements.map(pair => pair[0])).size > 1, 'fixture exercises nonlocal specials, not only local skins');
    assert.equal(elements[4][0], region); assert.ok(elements[4][1] && elements[4][1] !== region);
    for (const idx of Array.from({ length: 32 }, (_, i) => i + 4)) assert.notEqual(sources[idx], sources[0], 'boss/special is visibly different from ordinary');
    const catalog = b.dom.window.RAID_MONSTER_CATALOG;
    const readyPortraits = [];
    for (const type of catalog.ready_types) {
      const idx = type + 3, expected = `${r.state.board[idx].skull.element}_special_${String(type).padStart(2, '0')}`;
      assert.equal(portraits[idx], expected); readyPortraits.push(sources[idx]);
      assert.notEqual(sources[idx], sources[4], 'boss is not reused as a ready special skin');
    }
    assert.equal(new Set(readyPortraits).size, catalog.ready_types.length);
    assert.equal(catalog.complete, catalog.expected_types.every(type => catalog.ready_types.includes(type)));
    assert.equal(JSON.stringify(r.state), before); assert.equal(JSON.stringify(r.state.rngSeeds), beforeRng);
    assert.ok(r.state.board.every(c => magitechElements.includes(c.skull.element)), 'gameplay assigned elements exist before rendering');
    // All 31 inherited special types, including the newly completed Lich, are
    // on this board; every one must have its own assigned-element portrait.
    doc.querySelector('#menu-btn').click(); doc.querySelector('#menu-classes').click();
    const other = region === 'water' ? 'fire' : 'water';
    doc.querySelector(`button[data-raid-region="${other}"]`).click();
    assert.equal(doc.documentElement.dataset.raidRegion, region);
    assert.deepEqual([...doc.querySelectorAll('.skull-art')], images);
    assert.deepEqual(images.map(n => n.dataset.portrait), portraits);
    assert.equal(JSON.stringify(r.state), before);
    doc.querySelector('#picker-resume').click();
    const key = magitechKey('raid:activeRun'), saved = JSON.parse(b.dom.window.localStorage.getItem(key));
    saved.orbElements = Array.from({ length: 36 }, (_, idx) => (idx + 2) % 4); // NOT combat state.
    b.dom.window.localStorage.setItem(key, JSON.stringify(saved));
    const storage = raidStorage(b.dom.window); b.dom.window.close(); // No pagehide rewrite of the deliberate mismatch.
    b = await openMagitech({ saved: storage, start: false, setup: orbAnimationHarness }); r = b.raid; doc = b.dom.window.document;
    doc.querySelector('#picker-resume').click();
    assert.equal(doc.documentElement.dataset.raidRegion, region);
    assert.deepEqual([...doc.querySelectorAll('.skull-art')].map(n => n.dataset.portrait), portraits);
    assert.deepEqual([...doc.querySelectorAll('.skull-art')].map(n => n.src), sources);
    for (const [idx, orb] of [...doc.querySelectorAll('.cell > .raid-orb')].entries()) assert.equal(orb.dataset.element, elements[idx][0]);
    assert.equal(JSON.stringify(r.state.rngSeeds), beforeRng);
    assert.deepEqual(Array.from(r.state.board, c => [c.skull.element, c.skull.secondaryElement]), elements);
    assert.deepEqual(JSON.parse(b.dom.window.localStorage.getItem(key)).combat, saved.combat);
    for (const c of r.state.board) assert.equal(c.skull.hp, 50);
    assert.equal(r.baseDamage(), 3); assert.equal(r.swordDamage(3), 9);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech monster readability: falling survivors retain the same UID orb/image and region skin, without animation rewards or a cosmetic RNG draw', async () => {
  const b = await orbFixture({ region: 'water' });
  try {
    const r = b.raid;
    const skull = Object.assign(r.makeSkull(false, false, 0), { atk: 0, def: 99, hp: 50, maxHp: 50, enteredTurn: -1 });
    showOrbFixture(b, idx => idx === 0 ? { type: 'skull', skull } : { type: [24, 30, 31].includes(idx) ? 'coin' : 'sword' });
    const survivor = orbAt(b, 0), image = survivor.querySelector('img'), portrait = image.dataset.portrait, source = image.src;
    assert.equal(portrait, 'water_elemental');
    [24,30,31].forEach(idx => r.tryExtendChain(idx)); r.resolveChain();
    await until(() => r.state.hero.gold === 6);
    const idx = r.state.board.findIndex(c => c.type === 'skull' && c.skull.uid === skull.uid);
    assert.equal(orbAt(b, idx), survivor); assert.equal(survivor.querySelector('img'), image);
    assert.equal(survivor.dataset.element, 'water'); assert.equal(image.dataset.portrait, portrait); assert.equal(image.src, source);
    const before = orbGameplay(b); b.dom.window.__orbTest.finish(); assert.equal(orbGameplay(b), before);
    await until(() => r.state.turn === 1 && !r.state.resolving);
    assert.equal(survivor.querySelector('img'), image); assert.equal(image.src, source);
    assert.equal(r.state.hero.gold, 6); assert.equal(r.state.chainCount, 1);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

// New combat is exercised on the GENERATED page, with explicit assigned
// elements. Legacy tests below use a complete v1 shape, never a partial downgrade.
for (const sdk of [false,true]) test(`magitech elemental built: ${sdk ? 'SDK Close' : 'browser reload'} during a mixed-target hit restores v4 elements/RNG and commits neutral secondaries exactly once`, async () => {
  const stub = raidTelegramStub();
  const setup = w => { orbAnimationHarness(w); if (sdk) w.Telegram = { WebApp: stub.tg }; };
  const key = sdk ? magitechAccountKey(701, 'raid:activeRun') : magitechKey('raid:activeRun');
  let b = await openMagitech({ start: false, setup, ...(sdk ? { url: telegramRaidUrl } : {}) });
  try {
    let doc = b.dom.window.document;
    doc.querySelector('.picker-card-btn:not(.locked)').click(); b.raid.newGame(2718);
    let r = b.raid;
    const a = Object.assign(r.makeSkull(false), { hp: 100, maxHp: 100, def: 4, atk: 0, enteredTurn: 9, element: 'fire' });
    const z = Object.assign(r.makeSkull(false), { hp: 100, maxHp: 100, def: 4, atk: 0, enteredTurn: 9, element: 'earth' });
    r.state.turn = 10; r.state.hero.hp = 10; r.state.leech = .2; r.state.pierce = 0;
    showOrbFixture(b, idx => idx < 3 ? { type: 'sword', element: 'water' } : idx < 5 ? { type: 'skull', skull: idx === 3 ? a : z } : { type: 'shield' });
    assert.equal(doc.querySelector('[data-raid-combat-title]').textContent, 'RAID / СТИХИИ');
    assert.match(doc.querySelector('[data-raid-combat-note]').textContent, /Стихийный бой/);
    [0,1,2,3,4].forEach(idx => r.tryExtendChain(idx));
    assert.deepEqual(Array.from(r.chainPreview().skulls, t => t.damage), [10.5,7.5]);
    assert.match(doc.querySelector('#chain-tip').textContent, /Урон 9 \(нейтр\.\)/);
    const rng = JSON.parse(b.dom.window.localStorage.getItem(key)).combat;
    r.resolveChain();
    assert.equal(JSON.parse(b.dom.window.localStorage.getItem(key)).next.phase, 'match-chain');
    if (sdk) { doc.querySelector('#menu-btn').click(); doc.querySelector('#menu-close').click(); assert.equal(stub.calls.filter(c => c === 'close').length, 1); }
    b = await reopenMagitech(b, { setup, ...(sdk ? { url: telegramRaidUrl } : {}) }); r = b.raid; doc = b.dom.window.document;
    assert.equal(r.state, null); assert.equal(r.savedRunInfo().combatRules, 'elements-affinity-v1');
    assert.equal(doc.querySelector('#picker-resume').textContent, 'Продолжить · Лес');
    doc.querySelector('#picker-resume').click();
    assert.deepEqual(JSON.parse(b.dom.window.localStorage.getItem(key)).combat, rng);
    assert.deepEqual(Array.from(r.state.board.slice(0,3), c => c.element), ['water','water','water']);
    assert.deepEqual(Array.from(doc.querySelectorAll('.cell > .raid-orb')).slice(0,5).map(n => n.dataset.element), ['water','water','water','fire','earth']);
    await until(() => r.state.swordsHits === 9);
    assert.deepEqual([a.uid,z.uid].map(uid => r.state.board.find(c => c.skull?.uid === uid).skull.hp), [93,96]);
    assert.equal(r.state.hero.hp, 11); assert.equal(r.state.scrollLeechLabel.number, 1);
    const before = orbGameplay(b); b.dom.window.__orbTest.finish(); assert.equal(orbGameplay(b), before, 'animation completion never repeats a hit');
    await until(() => r.state.turn === 11 && !r.state.resolving);
    assert.equal(r.state.chainCount, 1); assert.equal(r.state.swordsHits, 9); assert.equal(r.state.hero.hp, 11);
    assert.equal(r.saveRun(), true); assert.deepEqual(JSON.parse(b.dom.window.localStorage.getItem(key)).combat, rng);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

for (const sdk of [false,true]) test(`magitech elemental built: ${sdk ? 'SDK' : 'browser'} released-v1 Continue keeps old damage/copy/profile; only confirmed new start enables elements`, async () => {
  const stub = raidTelegramStub(), setup = w => { orbAnimationHarness(w); if (sdk) w.Telegram = { WebApp: stub.tg }; };
  const key = sdk ? magitechAccountKey(701, 'raid:activeRun') : magitechKey('raid:activeRun');
  let b = await openMagitech({ setup, ...(sdk ? { url: telegramRaidUrl } : {}) });
  try {
    let r = b.raid;
    r.unlockClass('ranger'); r.levelUpClass('ranger');
    const skull = Object.assign(r.makeSkull(false), { hp: 100, maxHp: 100, def: 4, atk: 0, enteredTurn: 9 });
    r.state.turn = 10; r.state.hero.hp = 10; r.state.leech = .2; r.state.pierce = 0;
    showOrbFixture(b, idx => idx < 3 ? { type: 'sword', element: 'water' } : idx === 3 ? { type: 'skull', skull } : { type: 'shield' });
    const saved = raidStorage(b.dom.window), old = releasedMagitechCheckpoint(JSON.parse(saved[key]));
    old.orbElements = Array(36).fill(3); saved[key] = JSON.stringify(old);
    b.dom.window.close();
    b = await openMagitech({ saved, start: false, setup, ...(sdk ? { url: telegramRaidUrl } : {}) }); r = b.raid;
    const doc = b.dom.window.document;
    assert.equal(r.state, null); assert.equal(r.classLevelOf('ranger'), 2); assert.equal(r.savedRunInfo().combatRules, 'classic-v1');
    assert.equal(b.dom.window.localStorage.getItem(key), saved[key], 'reading an old save neither migrates nor rewrites it');
    assert.match(doc.querySelector('#picker-resume').textContent, /прежние правила/);
    doc.querySelector('#picker-resume').click();
    assert.equal(doc.querySelector('[data-raid-combat-title]').textContent, 'RAID / ПРЕЖНИЙ БОЙ');
    assert.match(doc.querySelector('[data-raid-combat-note]').textContent, /без стихийных множителей/);
    assert.ok(r.state.board.every(c => !Object.hasOwn(c, 'element') && (!c.skull || !Object.hasOwn(c.skull, 'element'))));
    assert.match(doc.querySelector('.cell[data-idx="0"]').getAttribute('aria-label'), /урон классический/);
    [0,1,2,3].forEach(idx => r.tryExtendChain(idx)); assert.equal(r.chainPreview().skulls[0].damage, 9);
    r.resolveChain(); await until(() => r.state.swordsHits === 9);
    assert.equal(r.state.board.find(c => c.skull?.uid === skull.uid).skull.hp, 95);
    assert.equal(r.state.hero.hp, 11); await until(() => r.state.turn === 11 && !r.state.resolving);
    assert.equal(r.saveRun(), true); const checkpoint = JSON.parse(b.dom.window.localStorage.getItem(key));
    assert.equal(checkpoint.version, 1); assert.equal(checkpoint.combat, undefined); assert.equal(r.makeSkull(true).element, undefined);
    doc.querySelector('#menu-btn').click(); doc.querySelector('#menu-classes').click();
    launchMagitechMech(doc);
    assert.equal(doc.querySelector('#restart-modal').classList.contains('show'), true);
    assert.equal(r.savedRunInfo().combatRules, 'classic-v1'); doc.querySelector('#restart-cancel').click();
    assert.equal(r.state.turn, 11); assert.equal(r.savedRunInfo().combatRules, 'classic-v1');
    launchMagitechMech(doc); doc.querySelector('#restart-confirm').click();
    assert.equal(r.state.turn, 0); assert.equal(r.classLevelOf('ranger'), 2);
    assert.equal(JSON.parse(b.dom.window.localStorage.getItem(key)).version, 4); assert.equal(r.savedRunInfo().combatRules, 'elements-affinity-v1');
    assert.equal(doc.querySelector('[data-raid-combat-title]').textContent, 'RAID / СТИХИИ');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech elemental built: dual boss shows both fixed elements in accessible name/details and uses their mean, without a render/popup RNG draw', async () => {
  const b = await orbFixture({ region: 'air' });
  try {
    const r = b.raid, doc = b.dom.window.document;
    const boss = Object.assign(r.makeSkull(true), { hp: 100, maxHp: 100, def: 4, atk: 0, element: 'air', secondaryElement: 'earth' });
    showOrbFixture(b, idx => idx < 3 ? { type: 'sword', element: 'fire' } : idx === 3 ? { type: 'skull', skull: boss } : { type: 'shield' });
    const before = JSON.parse(b.dom.window.localStorage.getItem(magitechKey('raid:activeRun'))).combat;
    const cell = doc.querySelector('.cell[data-idx="3"]'), orb = orbAt(b, 3), image = orb.querySelector('img');
    assert.equal(orb.dataset.element, 'air'); assert.match(image.dataset.portrait, /^air_/);
    assert.match(cell.getAttribute('aria-label'), /Воздух \+ Земля.*среднее двух/);
    [0,1,2,3].forEach(idx => r.tryExtendChain(idx));
    assert.equal(r.chainPreview().damage, 9); assert.equal(r.chainPreview().skulls[0].damage, 9.75);
    r.state.selected = []; r.state.chainType = null;
    r.showSkullModal(r.state.board[3].skull);
    const text = doc.querySelector('#modal-body').textContent;
    assert.match(text, /Воздух \+ Земля/); assert.match(text, /среднее по двум стихиям/);
    assert.doesNotMatch(text, /не назначает.*стихию/);
    doc.querySelector('#modal-body > .btn').click();
    assert.equal(orbAt(b, 3), orb); assert.equal(orb.querySelector('img'), image);
    assert.equal(r.saveRun(), true); assert.deepEqual(JSON.parse(b.dom.window.localStorage.getItem(magitechKey('raid:activeRun'))).combat, before);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech elemental built: rounded target lethality is truthful in the selected DOM while the displayed common damage stays neutral', async () => {
  const b = await orbFixture();
  try {
    const r = b.raid, doc = b.dom.window.document;
    const skull = Object.assign(r.makeSkull(false), { hp: 7, maxHp: 7, def: 4, atk: 0, element: 'fire' });
    r.state.pierce = 0;
    showOrbFixture(b, idx => idx < 3 ? { type: 'sword', element: 'water' } : idx === 3 ? { type: 'skull', skull } : { type: 'shield' });
    [0,1,2,3].forEach(idx => r.tryExtendChain(idx));
    assert.equal(r.chainPreview().skulls[0].damage, 10.5);
    assert.equal(doc.querySelector('.cell[data-idx="3"]').classList.contains('lethal'), true);
    assert.match(doc.querySelector('#chain-tip').textContent, /Урон 9 \(нейтр\.\).*Убито 1\/1/);
    r.resolveChain(); await until(() => r.state.skullsKilled === 1);
    assert.equal(r.state.swordsHits, 9); assert.equal(r.state.board.some(c => c.skull?.uid === skull.uid), false);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

// First-entry release notes are UI metadata, never a run/save migration.
const updatesKey = magitechKey('raid:updatesSeenBuild');
const updatesReleaseKey = magitechKey('raid:updatesSeenRelease');
const updateBuild = b => b.dom.window.document.querySelector('meta[name="raid-build"]').content;
const latestUpdateRelease = b => String(Math.max(...[...b.dom.window.document.querySelectorAll('#updates-modal li[data-release]')].map(n => Number(n.dataset.release))));
const updateLayer = b => b.dom.window.document.querySelector('#updates-modal');
const acknowledgeUpdate = b => b.dom.window.document.querySelector('#updates-close').click();

test('magitech update notice: first entry explains this release/history, never starts a run, and acknowledgement suppresses only this build', async () => {
  let b = await openMagitech({ start: false, dismissUpdates: false });
  try {
    const doc = b.dom.window.document, build = updateBuild(b), latest = latestUpdateRelease(b);
    assert.ok(updateLayer(b)?.classList.contains('show'));
    assert.equal(updateLayer(b).hidden, false); assert.equal(updateLayer(b).getAttribute('aria-modal'), 'true');
    assert.equal(b.raid.state, null); assert.equal(b.dom.window.localStorage.getItem(updatesKey), null);
    assert.equal(b.dom.window.localStorage.getItem(magitechKey('raid:activeRun')), null);
    assert.match(doc.querySelector('#updates-build').textContent, new RegExp(build));
    assert.equal(doc.querySelectorAll('#updates-current li').length, 1, 'shows only the latest unread release above the fold');
    assert.match(doc.querySelector('#updates-current').textContent, /Единый Canvas-движок взрывов и размеренное падение без слайд-шоу/);
    assert.match(doc.querySelector('#updates-history').textContent, /Шёлковое 60-fps падение плиток без стоп-кадров/);
    assert.match(doc.querySelector('#updates-history').textContent, /Звуки умений сбора ресурсов/);
    assert.match(doc.querySelector('#updates-history').textContent, /Два звука брони/);
    assert.match(doc.querySelector('#updates-history').textContent, /Подсказка перехода Лича в филактерий/);
    assert.match(doc.querySelector('#updates-history').textContent, /Компактная боевая шапка/);
    assert.match(doc.querySelector('#updates-history').textContent, /Два звука зелий/);
    assert.match(doc.querySelector('#updates-history').textContent, /Новые иконки умений/);
    assert.match(doc.querySelector('#updates-history').textContent, /Склонность пилота.*перед каждым рейдом/s);
    assert.match(doc.querySelector('#updates-history').textContent, /30%.*20%.*25%/s);
    assert.match(doc.querySelector('#updates-history').textContent, /уровня 3.*5%/s);
    assert.match(doc.querySelector('#updates-history').textContent, /не постоянная/);
    assert.match(doc.querySelector('#updates-history').textContent, /Выход после поражения.*К выбору меха.*Закрыть Mini App/s);
    assert.match(doc.querySelector('#updates-history').textContent, /Настройки прямо на игровом экране/);
    assert.match(doc.querySelector('#updates-history').textContent, /шестерёнк/);
    assert.match(doc.querySelector('#updates-history').textContent, /полупрозрачн/);
    assert.match(doc.querySelector('#updates-history').textContent, /Кружки.*анимации и звук/);
    assert.match(doc.querySelector('#updates-history').textContent, /Цели «Перенастройки» сохраняются/);
    assert.match(doc.querySelector('#updates-history').textContent, /мех и пилот/);
    assert.match(doc.querySelector('#updates-history').textContent, /смертным врагом/);
    assert.match(doc.querySelector('#updates-history').textContent, /Новых бонусов.*нет/);
    assert.doesNotMatch(doc.querySelector('#updates-current').textContent, /Новый модуль|Новых? портрет/);
    assert.match(doc.querySelector('#updates-history').textContent, /по порядку выбора/);
    assert.match(doc.querySelector('#updates-history').textContent, /от первой выбранной к последней/);
    assert.match(doc.querySelector('#updates-history').textContent, /Перенастройка/);
    assert.match(doc.querySelector('#updates-history').textContent, /12 ходов/);
    assert.match(doc.querySelector('#updates-history').textContent, /1–3/);
    assert.match(doc.querySelector('#updates-history').textContent, /короче/);
    assert.match(doc.querySelector('#updates-history').textContent, /31.*4/);
    assert.match(doc.querySelector('#updates-history').textContent, /четыр[её]х.*стихи/);
    assert.match(doc.querySelector('#updates-history').textContent, /без общей временной замены/);
    assert.match(doc.querySelector('#updates-history').textContent, /10.*образ/);
    assert.match(doc.querySelector('#updates-history').textContent, /Лич/);
    assert.ok(doc.querySelectorAll('#updates-history li').length >= 3);
    assert.match(doc.querySelector('#updates-history').textContent, /Стихи|стихи/);
    assert.match(doc.querySelector('#updates-boundary').textContent, /интеграция.*не начиналась/i);
    doc.querySelector('.picker-card-btn:not(.locked)').click();
    assert.equal(b.raid.state, null, 'a synthetic background click cannot start a hidden run');
    assert.equal(b.raid.newGame(10), false); assert.equal(b.raid.resumeSavedRun(), false);
    acknowledgeUpdate(b);
    assert.equal(updateLayer(b).hidden, true); assert.equal(b.raid.state, null);
    assert.equal(b.dom.window.localStorage.getItem(updatesKey), build);
    assert.equal(b.dom.window.localStorage.getItem(updatesReleaseKey), latest);
    const saved = raidStorage(b.dom.window); b.dom.window.close();
    b = await openMagitech({ saved, start: false, dismissUpdates: false });
    assert.equal(updateLayer(b).hidden, true); assert.equal(b.raid.state, null);
    b.dom.window.document.querySelector('#picker-updates').click();
    assert.equal(updateLayer(b).hidden, false, 'the history remains available after acknowledgement');
    assert.equal(b.dom.window.document.querySelector('#updates-current').hidden, true, 'already-read updates do not repeat above the fold');
    assert.equal(b.dom.window.document.querySelectorAll('#updates-current li').length, 0);
    assert.equal(b.dom.window.document.querySelector('#updates-history').open, true);
    acknowledgeUpdate(b);
    assert.equal(b.dom.window.localStorage.getItem(updatesKey), build);
    assert.equal(b.dom.window.localStorage.getItem(updatesReleaseKey), latest);
    assert.equal(b.raid.state, null); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech update notice: shows only unread releases since last seen release and suppresses already-read builds', async () => {
  let b = await openMagitech({ start: false, dismissUpdates: false });
  const latest = Number(latestUpdateRelease(b));
  b.dom.window.close();
  // Returning player who missed 2 releases sees exactly those 2 unread entries above the fold.
  b = await openMagitech({
    saved: { [updatesKey]: '000000000000', [updatesReleaseKey]: String(latest - 2) },
    start: false,
    dismissUpdates: false,
  });
  try {
    const doc = b.dom.window.document;
    assert.equal(updateLayer(b).hidden, false);
    const unread = [...doc.querySelectorAll('#updates-current li')].map(n => Number(n.dataset.release));
    assert.deepEqual(unread, [latest, latest - 1]);
    assert.equal(doc.querySelector('#updates-history').open, false);
    assert.ok([...doc.querySelectorAll('#updates-history li')].every(n => Number(n.dataset.release) <= latest - 2));
    acknowledgeUpdate(b);
    assert.equal(b.dom.window.localStorage.getItem(updatesReleaseKey), String(latest));
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
  // Player who already read the latest release does not get nagged even if the build hash changed.
  b = await openMagitech({
    saved: { [updatesKey]: '000000000000', [updatesReleaseKey]: String(latest) },
    start: false,
    dismissUpdates: false,
  });
  try {
    assert.equal(updateLayer(b).hidden, true);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

for (const value of ['000000000000', '{not a json save', '<img src=x onerror=window.BAD_UPDATE=1>']) test(`magitech update notice: obsolete/malformed marker ${value.slice(0, 12)} is not a game save or executable HTML`, async () => {
  const saved = { [updatesKey]: value, 'raid:activeRun': 'classic sentinel', 'raid:tg:701:history': 'classic account' };
  const b = await openMagitech({ saved, start: false, dismissUpdates: false, url: 'https://raid.test/?build=000000000000' });
  try {
    assert.equal(updateLayer(b).hidden, false); assert.equal(b.raid.state, null);
    assert.equal(b.dom.window.BAD_UPDATE, undefined);
    assert.notEqual(updateBuild(b), '000000000000', 'query hints cannot select the content build');
    acknowledgeUpdate(b);
    assert.equal(b.dom.window.localStorage.getItem(updatesKey), updateBuild(b));
    for (const [key, raw] of Object.entries(saved)) if (key !== updatesKey) assert.equal(b.dom.window.localStorage.getItem(key), raw);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech update notice: keyboard focus stays inside, Escape acknowledges, and prior inert/ARIA attributes are restored', async () => {
  const b = await openMagitech({ start: false, dismissUpdates: false, setup(w) {
    w.document.addEventListener('DOMContentLoaded', () => { w.document.querySelector('#help-modal').setAttribute('inert', ''); });
  } });
  try {
    const doc = b.dom.window.document, title = doc.querySelector('#updates-title');
    const body = doc.querySelector('#updates-body'), close = doc.querySelector('#updates-close');
    assert.equal(doc.activeElement, title);
    assert.equal(doc.querySelector('#class-picker').getAttribute('aria-hidden'), 'true');
    assert.equal(doc.querySelector('#class-picker').hasAttribute('inert'), true);
    title.dispatchEvent(new b.dom.window.KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }));
    assert.equal(doc.activeElement, close);
    close.dispatchEvent(new b.dom.window.KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }));
    assert.equal(doc.activeElement, body);
    body.dispatchEvent(new b.dom.window.KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }));
    assert.equal(doc.activeElement, close);
    close.dispatchEvent(new b.dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    assert.equal(updateLayer(b).hidden, true);
    assert.equal(doc.querySelector('#class-picker').hasAttribute('inert'), false);
    assert.equal(doc.querySelector('#class-picker').getAttribute('aria-hidden'), null);
    assert.equal(doc.querySelector('#raid-effects').getAttribute('aria-hidden'), 'true');
    assert.equal(doc.querySelector('#help-modal').hasAttribute('inert'), true);
    assert.equal(doc.activeElement, doc.querySelector('#picker-updates'));
    assert.equal(b.raid.state, null); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

for (const operation of ['getItem', 'setItem']) test(`magitech update notice: denied ${operation} is visible and non-blocking, does not touch progress or pretend persistence`, async () => {
  const b = await openMagitech({ start: false, dismissUpdates: false, setup(w) {
    const original = w.Storage.prototype[operation];
    w.Storage.prototype[operation] = function(key, ...args) {
      if (key === updatesKey) throw new Error('test blocked release marker');
      return original.call(this, key, ...args);
    };
    if (operation === 'getItem') {
      const write = w.Storage.prototype.setItem;
      w.Storage.prototype.setItem = function(key, ...args) {
        if (key === updatesKey) throw new Error('test fully blocked release marker');
        return write.call(this, key, ...args);
      };
    }
  } });
  try {
    assert.equal(updateLayer(b).hidden, false); assert.equal(b.raid.state, null);
    if (operation === 'getItem') assert.equal(b.dom.window.document.querySelector('#updates-storage-note').hidden, false);
    acknowledgeUpdate(b);
    assert.equal(updateLayer(b).hidden, true);
    const note = b.dom.window.document.querySelector('#picker-updates-status');
    assert.equal(note.hidden, false); assert.match(note.textContent, /не удалось|не сохран/i);
    assert.equal(b.dom.window.localStorage.length, 0);
    launchMagitechMech(b.dom.window.document);
    assert.ok(b.raid.state, 'the informational popup cannot lock gameplay on quota failure');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech update notice: classic, magitech browser and each Telegram profile have independent build acknowledgements', async () => {
  let b = await openMagitech({ start: false, dismissUpdates: false });
  try {
    const build = updateBuild(b); b.dom.window.close();
    const sentinel = { [updatesKey]: build, 'raid:updatesSeenBuild': 'classic', 'raid:tg:701:updatesSeenBuild': 'classic account' };
    b = await openMagitech({ saved: sentinel, start: false, dismissUpdates: false, url: testerRaidUrl, setup(w) { w.Telegram = { WebApp: localAccountSDK(701) }; } });
    assert.equal(updateLayer(b).hidden, false, 'browser acknowledgement is not a Telegram acknowledgement');
    acknowledgeUpdate(b);
    const first = raidStorage(b.dom.window);
    assert.equal(first[magitechAccountKey(701, 'raid:updatesSeenBuild')], build);
    for (const [key, value] of Object.entries(sentinel)) assert.equal(first[key], value);
    b.dom.window.close();
    b = await openMagitech({ saved: first, start: false, dismissUpdates: false, url: testerRaidUrl, setup(w) { w.Telegram = { WebApp: localAccountSDK(702) }; } });
    assert.equal(updateLayer(b).hidden, false); acknowledgeUpdate(b);
    const both = raidStorage(b.dom.window);
    for (const [key, value] of Object.entries(first)) assert.equal(both[key], value);
    assert.equal(both[magitechAccountKey(702, 'raid:updatesSeenBuild')], build);
    b.dom.window.close();
    b = await openMagitech({ saved: both, start: false, dismissUpdates: false, url: testerRaidUrl, setup(w) { w.Telegram = { WebApp: localAccountSDK(701) }; } });
    assert.equal(updateLayer(b).hidden, true); assert.equal(b.raid.state, null);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech update notice: late Telegram SDK defers the automatic popup until its own ID is available, never uses the browser marker', async () => {
  const b = await openMagitech({ start: false, dismissUpdates: false, url: testerRaidUrl });
  try {
    const doc = b.dom.window.document;
    assert.equal(updateLayer(b).hidden, true);
    assert.deepEqual(raidStorage(b.dom.window), {});
    b.dom.window.Telegram = { WebApp: localAccountSDK(703) };
    doc.querySelector('script[src="https://telegram.org/js/telegram-web-app.js"]').onload();
    assert.equal(updateLayer(b).hidden, false); assert.equal(b.raid.state, null);
    acknowledgeUpdate(b);
    assert.deepEqual(raidStorage(b.dom.window), {
      [magitechAccountKey(703, 'raid:updatesSeenBuild')]: updateBuild(b),
      [magitechAccountKey(703, 'raid:updatesSeenRelease')]: latestUpdateRelease(b),
    });
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech update notice: changing account while open cannot acknowledge either account or write a shared marker', async () => {
  const sdk = localAccountSDK(704);
  const b = await openMagitech({ start: false, dismissUpdates: false, url: testerRaidUrl, setup(w) { w.Telegram = { WebApp: sdk }; } });
  try {
    assert.equal(updateLayer(b).hidden, false);
    sdk.initDataUnsafe.user.id = 705;
    acknowledgeUpdate(b);
    assert.equal(updateLayer(b).hidden, true); assert.deepEqual(raidStorage(b.dom.window), {});
    assert.match(b.dom.window.document.querySelector('#picker-profile-status').textContent, /смен|заново/);
    assert.equal(b.dom.window.document.querySelector('#picker-updates-status').hidden, false);
    assert.equal(b.raid.state, null); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech update notice: manual history preserves live board/image identity, worn gear, all RNG and raw checkpoints', async () => {
  const b = await openMagitech();
  try {
    const doc = b.dom.window.document;
    b.raid.newGame(0); doc.querySelector('#menu-btn').click();
    const state = JSON.stringify(b.raid.state), saved = raidStorage(b.dom.window);
    const imgs = [...doc.querySelectorAll('#board img')];
    doc.querySelector('#menu-updates').click();
    assert.equal(updateLayer(b).hidden, false);
    b.raid.tryExtendChain(0); b.raid.tryExtendChain(1); b.raid.tryExtendChain(2);
    b.raid.resolveChain(); b.raid.activateSkill('teleport');
    assert.equal(JSON.stringify(b.raid.state), state);
    acknowledgeUpdate(b);
    assert.deepEqual(raidStorage(b.dom.window), saved);
    assert.equal(JSON.stringify(b.raid.state), state);
    assert.deepEqual([...doc.querySelectorAll('#board img')], imgs);
    assert.equal(doc.activeElement, doc.querySelector('#menu-updates'));
    assert.equal(doc.querySelector('#menu-modal').classList.contains('show'), true);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

for (const legacy of [false, true]) for (const kind of ['level', 'gold', 'armor']) test(`magitech update notice: ${legacy ? 'released v1' : 'v2'} ${kind} checkpoint stays untouched until Continue and keeps mandatory offers/draft/RNG`, async () => {
  let b = await openMagitech();
  try {
    if (kind === 'level') { b.raid.state.hero.xp = 100; b.raid.checkLevelUp(); b.dom.window.document.querySelectorAll('.lv-card')[1].click(); }
    else if (kind === 'gold') { b.raid.state.hero.gold = 100; b.raid.checkGoldBox(); }
    else { b.raid.state.upgradeBar = 100; b.raid.checkUpgradeBar(); }
    assert.equal(b.raid.saveRun(), true);
    const key = magitechKey('raid:activeRun'), saved = raidStorage(b.dom.window);
    if (legacy) saved[key] = JSON.stringify(releasedMagitechCheckpoint(JSON.parse(saved[key])));
    saved[updatesKey] = 'previous build'; delete saved[updatesReleaseKey];
    const raw = saved[key], expected = JSON.parse(raw); b.dom.window.close();
    b = await openMagitech({ saved, start: false, dismissUpdates: false });
    const doc = b.dom.window.document;
    assert.equal(updateLayer(b).hidden, false); assert.equal(b.raid.state, null);
    doc.querySelector('#picker-resume').click(); assert.equal(b.raid.state, null);
    assert.equal(b.dom.window.localStorage.getItem(key), raw);
    acknowledgeUpdate(b);
    assert.equal(b.raid.state, null); assert.equal(b.dom.window.localStorage.getItem(key), raw);
    doc.querySelector('#picker-resume').click();
    assert.equal(b.raid.state.pendingModal, kind);
    const actual = JSON.parse(b.dom.window.localStorage.getItem(key));
    for (const name of ['version','region','combat','rng','menu','next','arrivals']) assert.deepEqual(actual[name], expected[name], name);
    if (kind === 'level') assert.deepEqual([...b.raid.state.levelDraft], [1]);
    const state = JSON.stringify(b.raid.state), checkpoint = b.dom.window.localStorage.getItem(key);
    doc.querySelector('#menu-updates').click();
    assert.equal(updateLayer(b).hidden, true, 'manual history cannot cover a mandatory choice');
    assert.equal(JSON.stringify(b.raid.state), state);
    assert.equal(b.dom.window.localStorage.getItem(key), checkpoint);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech update notice: opening/closing at startup does not resume an in-flight saved chain or consume its 420/550ms steps', async () => {
  let b = await openMagitech();
  try {
    b.raid.state.board = Array.from({ length: 36 }, () => ({ type: 'coin' }));
    for (const idx of [0,1,2]) b.raid.tryExtendChain(idx);
    b.raid.resolveChain(); b.raid.saveRun();
    const saved = raidStorage(b.dom.window), key = magitechKey('raid:activeRun');
    assert.equal(JSON.parse(saved[key]).next.phase, 'match-chain');
    saved[updatesKey] = 'previous build'; delete saved[updatesReleaseKey]; b.dom.window.close();
    b = await openMagitech({ saved, start: false, dismissUpdates: false });
    await new Promise(resolve => setTimeout(resolve, 1000));
    assert.equal(b.raid.state, null); assert.equal(b.dom.window.localStorage.getItem(key), saved[key]);
    acknowledgeUpdate(b);
    assert.equal(b.raid.state, null); assert.equal(b.dom.window.localStorage.getItem(key), saved[key]);
    b.dom.window.document.querySelector('#picker-resume').click();
    await until(() => b.raid.state.turn === 1 && !b.raid.state.resolving);
    assert.equal(b.raid.state.chainCount, 1); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech update notice: Telegram Back dismisses this layer first, never closes the app or starts/restores a run', async () => {
  const stub = raidTelegramStub();
  const b = await openMagitech({ start: false, dismissUpdates: false, url: telegramRaidUrl, setup(w) { w.Telegram = { WebApp: stub.tg }; } });
  try {
    assert.equal(updateLayer(b).hidden, false);
    assert.equal(stub.calls.filter(call => call === 'back:show').length, 1);
    stub.back();
    assert.equal(updateLayer(b).hidden, true); assert.equal(b.raid.state, null);
    assert.equal(b.dom.window.localStorage.getItem(magitechAccountKey(701, 'raid:updatesSeenBuild')), updateBuild(b));
    assert.equal(stub.calls.includes('close'), false);
    assert.equal(stub.calls.at(-1), 'back:hide');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech update notice: manual Telegram Back is read-only and returns to its opener instead of cancelling the underlying game view', async () => {
  const stub = raidTelegramStub();
  const b = await openMagitech({ url: telegramRaidUrl, setup(w) { w.Telegram = { WebApp: stub.tg }; } });
  try {
    const doc = b.dom.window.document;
    doc.querySelector('#menu-btn').click();
    const state = JSON.stringify(b.raid.state), saved = raidStorage(b.dom.window);
    doc.querySelector('#menu-updates').click(); assert.equal(updateLayer(b).hidden, false);
    stub.back();
    assert.equal(updateLayer(b).hidden, true); assert.equal(JSON.stringify(b.raid.state), state);
    assert.deepEqual(raidStorage(b.dom.window), saved);
    assert.equal(doc.querySelector('#menu-modal').classList.contains('show'), true);
    assert.equal(doc.activeElement, doc.querySelector('#menu-updates'));
    assert.equal(stub.calls.includes('close'), false); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech update notice: next-run location survives a manual picker history visit without starting or saving a run', async () => {
  const b = await openMagitech({ start: false });
  try {
    const doc = b.dom.window.document;
    doc.querySelector('.picker-card-btn:not(.locked)').click(); doc.querySelector('.mech-go-button').click();
    doc.querySelector('button[data-raid-region="water"]').click(); doc.querySelector('#region-back-button').click();
    const saved = raidStorage(b.dom.window);
    doc.querySelector('#picker-updates').click(); assert.equal(updateLayer(b).hidden, false);
    doc.querySelector('button[data-raid-region="fire"]').click();
    assert.equal(doc.documentElement.dataset.raidRegion, 'water');
    assert.equal(b.raid.state, null);
    acknowledgeUpdate(b);
    assert.equal(doc.documentElement.dataset.raidRegion, 'water');
    assert.deepEqual(raidStorage(b.dom.window), saved);
    assert.equal(doc.activeElement, doc.querySelector('#picker-updates'));
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech update notice: a compatibility click from the opening gesture cannot acknowledge the popup; its own pointer/keyboard actions still work', async () => {
  const b = await openMagitech({ start: false, dismissUpdates: false });
  try {
    const doc = b.dom.window.document, close = doc.querySelector('#updates-close');
    const compatibilityClick = () => close.dispatchEvent(new b.dom.window.MouseEvent('click', { bubbles: true, cancelable: true, detail: 1 }));
    compatibilityClick(); assert.equal(updateLayer(b).hidden, false);
    assert.equal(b.dom.window.localStorage.getItem(updatesKey), null);
    raidPointer(b.dom.window, close, 'pointerdown'); raidPointer(b.dom.window, close, 'pointercancel');
    compatibilityClick(); assert.equal(updateLayer(b).hidden, false);
    raidPointer(b.dom.window, close, 'pointerdown');
    close.dispatchEvent(new b.dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    assert.equal(updateLayer(b).hidden, true);
    doc.querySelector('#picker-updates').click();
    compatibilityClick(); assert.equal(updateLayer(b).hidden, false, 'a previous opening cannot arm the next one');
    raidPointer(b.dom.window, close, 'pointerdown'); compatibilityClick();
    assert.equal(updateLayer(b).hidden, true);
    assert.equal(b.dom.window.localStorage.getItem(updatesKey), updateBuild(b));
    assert.equal(b.raid.state, null); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech special details: touch compatibility click cannot immediately dismiss a newly opened portrait; an explicit close is still free', async () => {
  const b = await openMagitech();
  try {
    const doc = b.dom.window.document;
    b.raid.showSkullModal(b.raid.makeSkull(false, true, 22));
    const close = doc.querySelector('#modal-body .btn'), state = JSON.stringify(b.raid.state);
    close.dispatchEvent(new b.dom.window.MouseEvent('click', { bubbles: true, cancelable: true, detail: 1 }));
    assert.equal(b.raid.state.pendingModal, 'skull'); assert.equal(JSON.stringify(b.raid.state), state);
    raidPointer(b.dom.window, close, 'pointerdown');
    close.dispatchEvent(new b.dom.window.MouseEvent('click', { bubbles: true, cancelable: true, detail: 1 }));
    assert.equal(b.raid.state.pendingModal, null); assert.equal(b.raid.state.turn, 0);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});


for (const region of ['fire', 'air', 'earth', 'water']) test(`magitech complete catalogue: ${region} Lich keeps its own art in details, phylactery, Continue and revival under v1/v2/v3/v4`, async () => {
  for (const version of [1, 2, 3, 4]) {
    let b = await orbFixture({ region, reduced: true }), r = b.raid;
    const savedRun = () => JSON.parse(b.dom.window.localStorage.getItem(magitechKey('raid:activeRun')));
    try {
      showOrbFixture(b, idx => idx === 0
        ? { type: 'skull', skull: Object.assign(r.makeSkull(false, true, 32), { element: region, atk: 0, def: 0, hp: 1, maxHp: 1 }) }
        : { type: 'coin' });
      if (version !== 4) {
        // Explicit legacy fixture ONLY. Genuine released-v1 runtime remains a
        // separate native check, not this test-only metadata conversion.
        const saved = raidStorage(b.dom.window), key = magitechKey('raid:activeRun');
        const raw = JSON.parse(saved[key]);
        if (version === 2) { raw.version = 2; raw.combat.rules = 'elements-v1'; delete raw.combat.affinity; }
        if (version === 3) { raw.version = 3; raw.combat.rules = 'elements-retune-v1'; delete raw.combat.affinity; }
        saved[key] = JSON.stringify(version === 1 ? releasedMagitechCheckpoint(raw) : raw);
        b.dom.window.close();
        b = await openMagitech({ saved, start: false, setup(w) { orbAnimationHarness(w, { reduced: true }); } });
        r = b.raid; assert.equal(r.resumeSavedRun(), true);
      }
      let doc = b.dom.window.document;
      const portrait = `${region}_special_32`, art = b.dom.window.RAID_MONSTER_ART[portrait];
      assert.ok(art, 'Lich must not be a shared fallback');
      const image = doc.querySelector('.cell[data-idx="0"] img.skull-art');
      assert.equal(image.dataset.portrait, portrait); assert.equal(image.src, art);
      const before = savedRun(); assert.equal(before.version, version);
      r.showSkullModal(r.state.board[0].skull);
      assert.equal(doc.querySelector('.skull-feat-art').dataset.portrait, portrait);
      assert.equal(doc.querySelector('.skull-feat-art').src, art);
      assert.match(doc.querySelector('#modal-title').textContent, /ЛИЧ/);
      assert.match(doc.querySelector('.skull-feat-list').textContent, /филактер/i);
      doc.querySelector('#modal-body .btn').click();
      await new Promise(resolve => b.dom.window.setTimeout(resolve, 120));
      assert.equal(doc.querySelector('.cell[data-idx="0"] img.skull-art'), image);
      assert.deepEqual(savedRun().rng, before.rng);
      assert.deepEqual(savedRun().combat, before.combat);
      r.hitSkullAt(0, 100000, { direct: true });
      assert.equal(r.state.board[0].skull.phylactery, true);
      assert.equal(r.state.skullsKilled, 0); assert.equal(r.state.specialsKilled, 0);
      assert.equal(r.saveRun(), true); assert.equal(r.resumeSavedRun(), true);
      assert.match(doc.querySelector('.cell[data-idx="0"] img.phylactery-art').src, /^data:image\/png;base64,/);
      const checkpoint = savedRun(), saved = raidStorage(b.dom.window);
      b.dom.window.close();
      b = await openMagitech({ saved, start: false, setup(w) { orbAnimationHarness(w, { reduced: true }); } });
      r = b.raid; doc = b.dom.window.document; assert.equal(r.resumeSavedRun(), true);
      const resumed = savedRun(); assert.equal(resumed.version, version);
      for (const key of ['region', 'rng', 'combat']) assert.deepEqual(resumed[key], checkpoint[key]);
      assert.deepEqual(resumed.state.board, checkpoint.state.board);
      assert.match(doc.querySelector('.cell[data-idx="0"] img.phylactery-art').src, /^data:image\/png;base64,/);
      r.applySpecialAbilities();
      assert.equal(r.state.board[0].skull.phylactery, true, 'the existing one-step Lich delay stays intact');
      r.applySpecialAbilities();
      assert.equal(r.state.board[0].skull.phylactery, false);
      assert.equal(r.state.board[0].skull.type, 32);
      assert.equal(r.state.board[0].skull.element, version >= 2 ? region : undefined);
      assert.equal(r.saveRun(), true); assert.equal(r.resumeSavedRun(), true);
      assert.equal(doc.querySelector('.cell[data-idx="0"] img.skull-art').src, art);
      assert.deepEqual(savedRun().combat, checkpoint.combat, 'revival does not reroll an element');
      assert.equal(savedRun().version, version);
      assert.deepEqual(b.errors, []);
    } finally { b.dom.window.close(); }
  }
});

// Approved A1: deterministic, explicitly confirmed recolouring; no fifth slot.
async function retuneFixture(options = {}) {
  const b = await orbFixture(options), r = b.raid;
  assert.equal(r.installSkillOption(r.prepareSkillOption('retune')), true);
  showOrbFixture(b, i => i === 1 ? { type: 'arrow', element: 'air' }
    : i === 2 ? { type: 'sword', element: 'earth', magicSword: true }
    : i === 4 ? { type: 'sword', element: 'water', broken: true }
    : i === 5 ? { type: 'sword', element: 'water', frozen: true }
    : i === 6 ? { type: 'coin' } : { type: 'sword', element: 'water' });
  return b;
}
const retuneRaw = b => b.dom.window.localStorage.getItem(magitechKey('raid:activeRun'));
const retunePick = (b, element, slots) => {
  const d = b.dom.window.document;
  d.querySelector(`[data-retune-element="${element}"]`).click();
  slots.forEach(i => d.querySelector(`.cell[data-idx="${i}"]`).click());
};
test('magitech retune: v3 module changes only 1–3 explicitly chosen weapons, charges twelve turns once and preserves identities/RNG', async () => {
  const b = await retuneFixture();
  try {
    const r = b.raid, d = b.dom.window.document, before = JSON.stringify(r.state), raw = retuneRaw(b);
    const combat = JSON.parse(raw).combat, cells=[...r.state.board],orbs=Array.from({length:36},(_,i)=>orbAt(b,i)),images=orbs.map(n=>n?.querySelector('img'));
    assert.equal(JSON.parse(raw).version, 4); assert.equal(combat.rules, 'elements-affinity-v1');
    r.activateSkill('retune'); assert.equal(d.querySelector('#retune-panel').hidden, false);
    retunePick(b, 'fire', [0,1,2,3,4,5,6]);
    assert.equal(d.querySelectorAll('.retune-chosen').length, 3);
    assert.equal(JSON.stringify(r.state), before); assert.equal(retuneRaw(b), raw, 'draft is read-only');
    const apply = d.querySelector('#retune-apply'); assert.equal(apply.disabled, false); apply.click(); apply.click();
    assert.equal(d.querySelector('#retune-panel').hidden, true);
    assert.deepEqual(Array.from(r.state.board.slice(0,4), c => c.element), ['fire','fire','fire','water']);
    assert.equal(r.state.board[1].type, 'arrow'); assert.equal(r.state.board[2].magicSword, true);
    assert.equal(r.state.skillCooldowns.retune, 12); assert.equal(r.state.turn, JSON.parse(before).turn);
    assert.deepEqual(Array.from(r.state.rngSeeds), JSON.parse(before).rngSeeds);
    assert.deepEqual(JSON.parse(retuneRaw(b)).combat, combat);
    for(let i=0;i<36;i++) {assert.equal(r.state.board[i],cells[i]);assert.equal(orbAt(b,i),orbs[i]);assert.equal(orbAt(b,i)?.querySelector('img'),images[i]);}
    assert.equal(r.resumeSavedRun(), true); assert.equal(r.state.skillCooldowns.retune, 12);
    assert.deepEqual(Array.from(r.state.board.slice(0,3), c => c.element), ['fire','fire','fire']);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});
test('magitech retune: cancel, Escape and pagehide discard the unpaid draft without changing a checkpoint', async () => {
  const b = await retuneFixture();
  try {
    const r = b.raid, d = b.dom.window.document, before = JSON.stringify(r.state);
    for (const cancel of ['button','escape','pagehide','hidden']) {
      r.activateSkill('retune'); retunePick(b,'fire',[0,1]);
      if (cancel === 'button') d.querySelector('#retune-cancel').click();
      else if (cancel === 'escape') d.dispatchEvent(new b.dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
      else if (cancel === 'pagehide') b.dom.window.dispatchEvent(new b.dom.window.Event('pagehide'));
      else {Object.defineProperty(d,'visibilityState',{value:'hidden',configurable:true});d.dispatchEvent(new b.dom.window.Event('visibilitychange'));Object.defineProperty(d,'visibilityState',{value:'visible',configurable:true});}
      assert.equal(d.querySelector('#retune-panel').hidden,true,cancel);
      assert.equal(JSON.stringify(r.state),before,cancel);
      assert.equal(JSON.parse(retuneRaw(b)).state.skillCooldowns.retune,0);
    }
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});
test('magitech retune: released v2 remains v2 without the new skill; only a confirmed new start uses v4', async () => {
  let b = await orbFixture();
  try {
    const old = JSON.parse(retuneRaw(b)); old.version=2; old.combat.rules='elements-v1'; delete old.combat.affinity;
    const saved = raidStorage(b.dom.window); saved[magitechKey('raid:activeRun')]=JSON.stringify(old);
    b.dom.window.close(); b = await openMagitech({saved,start:false}); const r=b.raid;
    assert.equal(r.resumeSavedRun(),true); assert.equal(r.classSkillPool().includes('retune'),false);
    assert.equal(r.installSkillOption(r.prepareSkillOption('retune')),false);
    r.activateSkill('retune'); assert.equal(b.dom.window.document.querySelector('#retune-panel').hidden,true);
    assert.equal(r.saveRun(),true); assert.equal(JSON.parse(retuneRaw(b)).version,2);
    assert.equal(JSON.parse(retuneRaw(b)).combat.rules,'elements-v1');
    r.newGame(2718); assert.equal(JSON.parse(retuneRaw(b)).version,4);
    assert.equal(r.classSkillPool().includes('retune'),true); assert.deepEqual(b.errors,[]);
  } finally { b.dom.window.close(); }
});
test('magitech smooth fall: a mass removal batches all final geometry reads before one shared monotone animation', async () => {
  const b=await orbFixture();
  try {
    showOrbFixture(b, i => ({type:i>=18 ? 'coin':'sword'}));
    const r=b.raid, h=b.dom.window.__orbTest;
    r.state.skills=['treasure']; r.state.skillLevels={treasure:1}; r.state.skillCooldowns={treasure:0};
    // An old frame timestamp must not be forced onto a new animation after a
    // long input task. WAAPI's pending-ready batch supplies the next frame.
    Object.defineProperty(b.dom.window.document,'timeline',{value:{currentTime:-10000}});
    const operations=[], proto=b.dom.window.Element.prototype, rect=proto.getBoundingClientRect, animate=proto.animate;
    proto.getBoundingClientRect=function(){operations.push('rect');return rect.call(this);};
    proto.animate=function(frames,options){if(options.delay===65)operations.push('drop');return animate.call(this,frames,options);};
    h.created.length=0; r.activateSkill('treasure');
    const first=operations.indexOf('drop'),last=operations.lastIndexOf('drop');
    assert.ok(first>=0 && last>first);
    assert.ok(operations.slice(first,last+1).every(kind=>kind==='drop'),'no read/write/read layout loop between starts');
    const drops=h.created.filter(a => a.target.dataset.motion && a.options.delay===65);
    assert.ok(drops.length>=18); assert.equal(new Set(drops.map(a=>a.options.duration)).size,1);
    for (const a of drops) {
      assert.equal(a.startTime,null,'do not backdate to the previous document.timeline frame');
      assert.equal(a.frames.length,2,'no stop/overshoot/squash restart before landing');
      assert.equal(a.frames.at(-1).transform,'translate(0,0)');
      assert.equal(a.options.easing,'cubic-bezier(.22,.61,.36,1)');
    }
    const state=orbGameplay(b); h.finish(); assert.equal(orbGameplay(b),state);
  } finally { b.dom.window.close(); }
});

for (const element of magitechElements) test(`magitech retune: ${element} selection is explicit, rejects frozen/broken/nonweapon/no-op and changes no stats`, async () => {
  const b=await retuneFixture();
  try {
    const r=b.raid,d=b.dom.window.document;
    r.activateSkill('retune'); assert.equal(d.querySelector('#retune-apply').disabled,true);
    retunePick(b,element,[4,5,6]); assert.equal(d.querySelectorAll('.retune-chosen').length,0);
    assert.equal(d.querySelector('#retune-apply').disabled,true);
    const slot=r.state.board.findIndex(c => ['sword','arrow'].includes(c.type) && !c.frozen && !c.broken && c.element!==element);
    const copy=JSON.parse(JSON.stringify(r.state.board)),hero=JSON.stringify(r.state.hero),turn=r.state.turn;
    retunePick(b,element,[slot]);
    d.querySelector(`.cell[data-idx="${slot}"]`).click(); assert.equal(d.querySelector('#retune-apply').disabled,true,'toggle off');
    d.querySelector(`.cell[data-idx="${slot}"]`).dispatchEvent(new b.dom.window.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
    assert.equal(d.querySelector(`.cell[data-idx="${slot}"]`).getAttribute('aria-pressed'),'true');
    d.querySelector('#retune-apply').click();
    copy[slot].element=element; assert.deepEqual(JSON.parse(JSON.stringify(r.state.board)),copy);
    assert.equal(JSON.stringify(r.state.hero),hero); assert.equal(r.state.turn,turn);
    assert.equal(d.querySelectorAll('#board [role="button"]').length,0,'temporary keyboard roles removed');
    assert.deepEqual(b.errors,[]);
  } finally { b.dom.window.close(); }
});
test('magitech retune: the unpaid chooser blocks chain/reward/menu/skill inputs and has a keyboard return path', async () => {
  const b=await retuneFixture();
  try {
    const r=b.raid,d=b.dom.window.document; r.activateSkill('retune'); retunePick(b,'fire',[0]);
    const before=JSON.stringify(r.state);
    r.tryExtendChain(0); r.resolveChain(); r.activateSkill('earthquake');
    assert.equal(r.openRunHistory(),false); d.querySelector('#menu-btn').click(); d.querySelector('#stats-btn').click();
    assert.equal(JSON.stringify(r.state),before); assert.equal(d.querySelector('#menu-modal').classList.contains('show'),false);
    d.querySelector('#menu-btn').focus(); assert.ok(d.activeElement.hasAttribute('data-retune-element'));
    d.dispatchEvent(new b.dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
    assert.equal(d.activeElement.dataset.skillId,'retune'); assert.deepEqual(b.errors,[]);
  } finally { b.dom.window.close(); }
});
for (const invalidation of ['stale-slot','replaced-cell','cooldown','removed-skill']) test(`magitech retune: ${invalidation} invalidates a queued confirmation without paying or changing weapons`, async () => {
  const b=await retuneFixture();
  try {
    const r=b.raid,d=b.dom.window.document; r.activateSkill('retune'); retunePick(b,'fire',[0]);
    if(invalidation==='stale-slot') { const saved=JSON.parse(retuneRaw(b)); saved.revision++; b.dom.window.localStorage.setItem(magitechKey('raid:activeRun'),JSON.stringify(saved)); }
    if(invalidation==='replaced-cell') r.state.board[0]={...r.state.board[0]};
    if(invalidation==='cooldown') r.state.skillCooldowns.retune=2;
    if(invalidation==='removed-skill') r.state.skills=[];
    const state=JSON.stringify(r.state),raw=retuneRaw(b); d.querySelector('#retune-apply').click();
    assert.equal(JSON.stringify(r.state),state); assert.equal(retuneRaw(b),raw);
    assert.equal(d.querySelector('#retune-panel').hidden,true); assert.deepEqual(b.errors,[]);
  } finally { b.dom.window.close(); }
});
test('magitech retune: SDK Back cancels, and an account switch cannot apply or overwrite either profile', async () => {
  const stub=raidTelegramStub();
  const b=await openMagitech({start:false,url:telegramRaidUrl,setup(w){orbAnimationHarness(w);w.Telegram={WebApp:stub.tg};}});
  try {
    const r=b.raid,d=b.dom.window.document; d.querySelector('.picker-card-btn:not(.locked)').click(); r.newGame(2718);
    r.installSkillOption(r.prepareSkillOption('retune'));
    showOrbFixture(b,()=>({type:'sword',element:'water'}));
    const key=magitechAccountKey(701,'raid:activeRun'),before=JSON.stringify(r.state);
    r.activateSkill('retune'); retunePick(b,'fire',[0]); stub.back();
    assert.equal(d.querySelector('#retune-panel').hidden,true); assert.equal(JSON.stringify(r.state),before);
    r.activateSkill('retune'); retunePick(b,'fire',[0]); stub.fire('deactivated');
    assert.equal(d.querySelector('#retune-panel').hidden,true); assert.equal(JSON.stringify(r.state),before);
    r.activateSkill('retune'); retunePick(b,'fire',[0]);
    const raw=b.dom.window.localStorage.getItem(key); stub.tg.initDataUnsafe.user.id=702;
    d.querySelector('#retune-apply').click();
    assert.equal(JSON.stringify(r.state),before); assert.equal(b.dom.window.localStorage.getItem(key),raw);
    assert.equal(b.dom.window.localStorage.getItem(magitechAccountKey(702,'raid:activeRun')),null);
    assert.match(d.querySelector('#menu-profile-status').textContent,/смен|заново/); assert.deepEqual(b.errors,[]);
  } finally { b.dom.window.close(); }
});
for (const corrupt of ['v1-owned','v1-map','v1-config','v1-level-offer','v2-owned','v2-map','v2-config','v2-level-offer','v3-shop-scroll','v3-old-rules','v2-new-rules']) test(`magitech retune: strict save rejects ${corrupt} without migration or overwriting data`, async () => {
  let b=await orbFixture();
  try {
    const r=b.raid; let raw=JSON.parse(retuneRaw(b));
    if(corrupt.startsWith('v1')) raw=releasedMagitechCheckpoint(raw);
    if(corrupt.startsWith('v3')) {raw.version=3;raw.combat.rules='elements-retune-v1';delete raw.combat.affinity;}
    if(corrupt.startsWith('v2')) {raw.version=2;raw.combat.rules='elements-v1';delete raw.combat.affinity;}
    if(corrupt.endsWith('-owned')) raw.state.skills=['retune'];
    if(corrupt.endsWith('-map')) raw.state.skillCooldowns.retune=0;
    if(corrupt.endsWith('-config')) {raw.state.classId='warrior';raw.state.classConfig=r.defaultClassConfig('warrior');raw.state.classConfig.replacements[1]='retune';}
    if(corrupt.endsWith('-level-offer')) {raw.menu={kind:'level',picked:[],choices:Array.from({length:4},()=>({kind:'skill',id:'retune',level:1,duration:12,charge:12,upgrade:false}))};}
    if(corrupt==='v3-shop-scroll') {const item=r.makeShopOffers()[0]; raw.menu={kind:'gold',picked:[],choices:[item,item,item].map(i=>JSON.parse(JSON.stringify(i)))}; for(const i of raw.menu.choices) {delete i.slotName;delete i.icon; i.scroll={kind:'skill',id:'retune',level:1,duration:12,charge:12,upgrade:false};}}
    if(corrupt==='v3-old-rules') raw.combat.rules='elements-v1';
    if(corrupt==='v2-new-rules') raw.combat.rules='elements-retune-v1';
    const saved=raidStorage(b.dom.window),bad=JSON.stringify(raw);saved[magitechKey('raid:activeRun')]=bad;
    b.dom.window.close(); b=await openMagitech({saved,start:false});
    assert.equal(b.raid.saveStatus.state,'invalid'); assert.equal(b.raid.resumeSavedRun(),false);
    assert.equal(b.raid.state,null); assert.equal(retuneRaw(b),bad); assert.deepEqual(b.errors,[]);
  } finally { b.dom.window.close(); }
});

test('magitech retune: Quicken, upgrades, Mage suppression and mana use the existing cooldown pipeline', async () => {
  const b=await retuneFixture();
  try {
    const r=b.raid,d=b.dom.window.document;
    r.state.equipment.find(i=>i.slot==='accessory').mods.quicken=1; r.updateEquipmentStats();
    assert.equal(r.skillDuration('retune'),11);
    assert.equal(r.installSkillOption(r.prepareSkillOption('retune')),true); assert.equal(r.skillDuration('retune'),10);
    r.activateSkill('retune');retunePick(b,'fire',[0]);d.querySelector('#retune-apply').click();
    assert.equal(r.state.skillCooldowns.retune,10); r.endEnemyTurn(); assert.equal(r.state.skillCooldowns.retune,9);
    r.state.board[7]={type:'skull',skull:Object.assign(r.makeSkull(false,true,15),{atk:0,hp:100,maxHp:100,def:0})};
    r.endEnemyTurn(); assert.equal(r.state.skillCooldowns.retune,9,'Mage still suppresses natural recovery');
    r.refillRandomSkill(2); await until(()=>r.state.skillCooldowns.retune===7);
    assert.equal(r.saveRun(),true);assert.equal(r.resumeSavedRun(),true);assert.equal(r.skillDuration('retune'),10);
    assert.deepEqual(b.errors,[]);
  } finally {b.dom.window.close();}
});
test('magitech retune: normal Dragonkin reaction fires once on confirmation, never on draft/cancel/double tap', async () => {
  const b=await retuneFixture();
  try {
    const r=b.raid,d=b.dom.window.document;
    r.state.classId='warrior'; r.state.classConfig={...r.defaultClassConfig('warrior'),race:'Dragonkin'};
    r.state.board[7]={type:'skull',skull:Object.assign(r.makeSkull(false),{atk:0,hp:100,maxHp:100,def:0})};
    assert.equal(r.saveRun(),true);assert.equal(r.resumeSavedRun(),true);
    const rng=Array.from(r.state.rngSeeds); const enemy=r.state.board[7].skull;
    r.activateSkill('retune');retunePick(b,'fire',[0]);d.querySelector('#retune-cancel').click();
    assert.deepEqual(Array.from(r.state.rngSeeds),rng);assert.equal(!!enemy.burn,false);
    r.activateSkill('retune');retunePick(b,'fire',[0]);const apply=d.querySelector('#retune-apply');apply.click();apply.click();
    const expected=[...rng];expected[5]=Math.imul(expected[5],16807)>>>0;
    assert.deepEqual(Array.from(r.state.rngSeeds),expected);assert.equal(enemy.burn,true);assert.equal(enemy.fireDamage,r.baseDamage());
    assert.equal(r.state.skillCooldowns.retune,12);assert.deepEqual(b.errors,[]);
  } finally {b.dom.window.close();}
});

for(const version of [1,2,3,4]) test(`magitech retune: v${version} finished record validates its own skill domain and history remains compatible`,async()=>{
  let b=await retuneFixture();
  try{
    const r=b.raid;
    r.state.hero.hp=0;r.enemyPhase();
    assert.equal(JSON.parse(retuneRaw(b)).status,'finished');
    const raw=JSON.parse(retuneRaw(b));raw.version=version;
    const saved=raidStorage(b.dom.window),bad=JSON.stringify(raw);saved[magitechKey('raid:activeRun')]=bad;
    b.dom.window.close();b=await openMagitech({saved,start:false});
    assert.equal(b.raid.saveStatus.state,version>=3?'empty':'invalid');
    assert.equal(retuneRaw(b),bad,'merely reading a terminal checkpoint never rewrites it');
    if(version>=3){
      const history=b.raid.getLocalHistory();
      assert.ok(history.records.flat().some(row=>row.skills.some(skill=>skill.id==='retune')),'new module is named in normal local history');
      assert.equal(b.raid.openRunHistory(),true);
      assert.match(b.dom.window.document.querySelector('#history-modal').textContent,/Перенастройка/);
    }
    assert.deepEqual(b.errors,[]);
  }finally{b.dom.window.close();}
});
for(const secondary of [null,'earth','water']) test(`magitech retune: the next real chain keeps magic weight, armour/one-rounding and ${secondary||'single'}-element target arithmetic`,async()=>{
  const b=await retuneFixture();
  try{
    const r=b.raid,d=b.dom.window.document;
    const enemy=Object.assign(r.makeSkull(!!secondary),{hp:100,maxHp:100,atk:0,def:4,element:'air',...(secondary?{secondaryElement:secondary}:{})});
    showOrbFixture(b,i=>i===3?{type:'skull',skull:enemy}:i===0?{type:'sword',element:'water',magicSword:true}:i===1?{type:'arrow',element:'water'}:i===2?{type:'sword',element:'earth'}:{type:'shield'});
    r.activateSkill('retune');retunePick(b,'fire',[0,1]);d.querySelector('#retune-apply').click();
    [0,1,2,3].forEach(i=>r.tryExtendChain(i));
    const k=secondary==='earth'?1.125:secondary==='water'?1:1.25;
    const oldSwordK=secondary==='earth'?(.75+1)/2:secondary==='water'?(.75+1.25)/2:.75;
    const expected=r.baseDamage()+r.state.hero.weaponDamage*(6*k+oldSwordK);
    assert.equal(r.chainPreview().skulls[0].damage,expected);
    r.resolveChain();await until(()=>r.state.swordsHits===r.swordDamage(7));
    assert.equal(r.state.board.find(c=>c.skull?.uid===enemy.uid).skull.hp,100-Math.max(0,Math.round(expected)-4));
    await until(()=>!r.state.resolving && r.state.turn===1);
    assert.equal(r.state.skillCooldowns.retune,11);assert.equal(r.saveRun(),true);
    assert.deepEqual(b.errors,[]);
  }finally{b.dom.window.close();}
});

test('magitech retune: a colour-only cast preserves fresh/frozen enemy metadata and every other cell, not generic transformation housekeeping', async () => {
  const b=await retuneFixture();
  try {
    const r=b.raid,d=b.dom.window.document;r.state.turn=12;
    showOrbFixture(b,i=>i===7 || i===8
      ? {type:'skull',skull:Object.assign(r.makeSkull(false),{enteredTurn:12,element:'earth',hp:100,maxHp:100}),...(i===8?{frozen:true}:{})}
      : {type:i===1?'arrow':'sword',element:'water',...(i===2?{magicSword:true,burn:true,fireDamage:3,fireSafe:true}:{})});
    const cells=[...r.state.board],expected=JSON.parse(JSON.stringify(r.state.board)),before=JSON.parse(retuneRaw(b));
    r.activateSkill('retune');retunePick(b,'fire',[0,1,2]);d.querySelector('#retune-apply').click();
    for(const i of [0,1,2])expected[i].element='fire';
    assert.deepEqual(JSON.parse(JSON.stringify(r.state.board)),expected,'do not age/thaw/rebuild other enemies');
    cells.forEach((cell,i)=>assert.equal(r.state.board[i],cell));
    const after=JSON.parse(retuneRaw(b));assert.deepEqual(after.rng,before.rng);assert.deepEqual(after.combat,before.combat);
    assert.equal(after.state.skillCooldowns.retune,12);assert.equal(r.state.turn,12);assert.deepEqual(b.errors,[]);
  } finally {b.dom.window.close();}
});

// Bounded presentation experiment: only confirmed disappearing chain members
// have a gesture order. Geometry is simulated here; native compositor follows.
const chainWaveAnimations = b => b.dom.window.__orbTest.created.filter(a => a.frames.at(-1)?.transform === 'scale(.08)');
const chainSnake = Array.from({length:36}, (_,i) => Math.floor(i/6)*6 + (Math.floor(i/6)%2 ? 5-i%6 : i%6));
for (const path of [[31,30,24], [24,30,31], [0,7,14,15,20,27,28,35], chainSnake.slice(0,12), chainSnake]) {
  test(`magitech chain wave: ${path.length} nodes ${path[0]} to ${path.at(-1)} burst in selection order, not grid order`, async () => {
    const b=await orbFixture();
    try {
      showOrbFixture(b, i => ({type:path.includes(i)?'coin':'sword'}));
      const r=b.raid,h=b.dom.window.__orbTest,orbs=path.map(i=>orbAt(b,i));h.created.length=0;
      for(const i of path) r.tryExtendChain(i);
      assert.deepEqual([...r.state.selected],path);r.resolveChain();
      assert.equal(chainWaveAnimations(b).length,0,'do not predict destruction before the existing 420ms settlement');
      assert.equal(r.state.hero.gold,0);
      await until(()=>r.state.hero.gold>=path.length*2);
      const bursts=chainWaveAnimations(b),ordered=orbs.map(n=>bursts.find(a=>a.target===n));
      assert.equal(bursts.length,path.length);assert.ok(ordered.every(Boolean));
      const step=Math.min(32,120/(path.length-1));
      for(const [i,a] of ordered.entries()) {
        assert.ok(Math.abs(a.options.delay-i*step)<.00001,`node${i} follows the actual gesture`);
        assert.equal(a.options.duration,120);assert.equal(a.options.fill,'both','untriggered ghost holds its real image');
        assert.equal(a.startTime,null,'native next-frame start, never backdated');
        assert.equal(a.frames[0].opacity,1);assert.equal(a.frames.at(-1).opacity,0);
        const burst=a.target.parentElement;assert.ok(burst.classList.contains('orb-burst'));
        assert.equal(parseFloat(burst.style.getPropertyValue('--burst-delay')),a.options.delay,'ring/shards use the same stagger');
        const fade=h.created.find(other=>other.target===burst);
        assert.equal(fade.options.delay,a.options.delay);assert.ok(fade.options.delay+fade.options.duration<=480);
      }
      const colEnd = new Map();
      for (const [i, a] of ordered.entries()) {
        const col = path[i] % 6;
        colEnd.set(col, Math.max(colEnd.get(col) || 0, a.options.delay + 120));
      }
      const drops = h.created.filter(a => a.target.dataset.motion);
      assert.ok(drops.length);
      for (const a of drops) {
        const cellIdx = Number(a.target.closest('.cell')?.dataset.idx);
        const colReady = colEnd.get(cellIdx % 6) || 0;
        assert.ok(a.options.delay >= colReady - 0.01, 'column fall starts only when that column\'s burst cores are gone');
        assert.ok(a.options.delay + a.options.duration <= 545, 'keep clean headroom before the 550ms enemy wait ends');
        assert.equal(a.frames.length, 2, 'smooth 2-keyframe compositor fall');
        assert.equal(a.frames.at(-1).transform, 'translate3d(0,0,0)');
        assert.equal(a.options.easing, 'cubic-bezier(.30,.08,.42,1)');
      }
      const before=orbGameplay(b);h.finish();assert.equal(orbGameplay(b),before,'animation completion never pays or advances');
      assert.deepEqual(b.errors,[]);
    } finally {b.dom.window.close();}
  });
}

test('magitech chain wave: arrow-skipped resource, surviving enemy and backtracked tail never receive a false explosion', async()=>{
  const b=await orbFixture();
  try {
    const r=b.raid,survivor=Object.assign(r.makeSkull(false,false,0),{hp:99,maxHp:99,def:99,atk:0,enteredTurn:-1});
    showOrbFixture(b,i=>i===3?{type:'skull',skull:survivor}:{type:i===0?'arrow':i===1?'coin':'sword'});
    const originals=[0,1,2,3,9].map(i=>orbAt(b,i)),img=originals[3].querySelector('img');
    for(const i of [0,1,2,8,2,3])r.tryExtendChain(i);
    assert.deepEqual([...r.state.selected],[0,1,2,3]);r.resolveChain();
    await until(()=>chainWaveAnimations(b).length>0);
    const bursts=chainWaveAnimations(b);assert.equal(bursts.length,2);
    assert.equal(bursts.find(a=>a.target===originals[0]).options.delay,0);
    assert.equal(bursts.find(a=>a.target===originals[2]).options.delay,32);
    for(const n of [originals[1],originals[3],orbAt(b,8)])assert.ok(!bursts.some(a=>a.target===n));
    assert.equal(originals[3].querySelector('img'),img);assert.ok(originals[3].isConnected);
    assert.equal(r.state.hero.gold,0);assert.deepEqual(b.errors,[]);
  }finally{b.dom.window.close();}
});

test('magitech chain wave: collateral Kamikaze victims retain un-ordered bursts and do not overlap solid ghosts with the falling batch',async()=>{
  const b=await orbFixture();
  try {
    const r=b.raid,bomb=Object.assign(r.makeSkull(false,true,10),{hp:1,maxHp:1,def:0,atk:0,enteredTurn:-1});
    showOrbFixture(b,i=>i===2?{type:'skull',skull:bomb}:{type:i<2?'sword':'potion'});
    const path=[2,1,0],ordered=path.map(i=>orbAt(b,i)),extra=orbAt(b,3);
    for(const i of path)r.tryExtendChain(i);r.resolveChain();
    await until(()=>r.state.skullsKilled===1);
    const bursts=chainWaveAnimations(b);
    assert.deepEqual(ordered.map(n=>bursts.find(a=>a.target===n).options.delay),[0,32,64]);
    const collateral=bursts.find(a=>a.target===extra);assert.ok(collateral);assert.equal(collateral.options.delay,0);
    assert.equal(collateral.options.duration,240,'do not invent a selection order for area effects');
    for(const a of b.dom.window.__orbTest.created.filter(a=>a.target.dataset.motion)){
      const cellIdx = Number(a.target.closest('.cell')?.dataset.idx);
      const minStart = cellIdx % 6 === 3 ? 240 : cellIdx % 6 === 0 ? 184 : cellIdx % 6 === 1 ? 152 : 120;
      assert.ok(a.options.delay>=minStart-0.01);assert.ok(a.options.delay+a.options.duration<=545);
    }
    assert.deepEqual(b.errors,[]);
  }finally{b.dom.window.close();}
});

for(const cancel of ['resize','reduced','hidden','restart'])test(`magitech chain wave: ${cancel} clears even not-yet-started bursts, with stale callbacks harmless`,async()=>{
  const b=await orbFixture();
  try {
    showOrbFixture(b,i=>({type:[24,30,31].includes(i)?'coin':'sword'}));
    for(const i of [31,30,24])b.raid.tryExtendChain(i);b.raid.resolveChain();
    await until(()=>b.raid.state.hero.gold===6);
    const stale=b.dom.window.__orbTest.created.filter(a=>a.playState==='running').map(a=>a.onfinish);
    assert.ok(chainWaveAnimations(b).some(a=>a.options.delay>0));
    const before=orbGameplay(b),w=b.dom.window;
    if(cancel==='resize')w.dispatchEvent(new w.Event('resize'));
    else if(cancel==='reduced')w.__orbTest.reduce(true);
    else if(cancel==='hidden'){Object.defineProperty(w.document,'visibilityState',{value:'hidden',configurable:true});w.document.dispatchEvent(new w.Event('visibilitychange'));}
    else b.raid.newGame(991);
    const after=orbGameplay(b),fresh=orbAt(b,0);if(cancel!=='restart')assert.equal(after,before);
    for(const fn of stale)fn?.();
    assert.equal(orbGameplay(b),after);assert.equal(orbAt(b,0),fresh);assert.ok(fresh.isConnected);
    assert.equal(w.document.querySelectorAll('.orb-burst,[data-motion],.primed').length,0);assert.deepEqual(b.errors,[]);
  }finally{b.dom.window.close();}
});

test('magitech chain wave: Rune interior has no invented gesture index; magic weight is still one physical burst', async()=>{
  const b=await orbFixture();
  try {
    const r=b.raid,path=[7,8,9,15,21,20,19,13];
    r.state.runeActive=1;
    showOrbFixture(b,i=>({type:path.includes(i)||i===14?'coin':'sword'}));
    const nodes=path.map(i=>orbAt(b,i)),inside=orbAt(b,14);
    for(const i of path)r.tryExtendChain(i);r.resolveChain();
    await until(()=>chainWaveAnimations(b).length===9);
    const bursts=chainWaveAnimations(b);
    assert.deepEqual(nodes.map(n=>bursts.find(a=>a.target===n).options.delay),path.map((_,i)=>i*120/7));
    const interior=bursts.find(a=>a.target===inside);assert.equal(interior.options.delay,0);assert.equal(interior.options.duration,240);
    b.dom.window.__orbTest.finish();await until(()=>!r.state.resolving);
    r.newGame(2718);showOrbFixture(b,i=>({type:'sword',...(i===0?{magicSword:true}:{})}));
    const magic=[0,1,2].map(i=>orbAt(b,i));b.dom.window.__orbTest.created.length=0;
    for(const i of [0,1,2])r.tryExtendChain(i);r.resolveChain();
    await until(()=>chainWaveAnimations(b).length>0);
    assert.deepEqual(magic.map(n=>chainWaveAnimations(b).find(a=>a.target===n).options.delay),[0,32,64],'magic ×5 is not five animation entries');
    assert.deepEqual(b.errors,[]);
  }finally{b.dom.window.close();}
});

test('magitech chain wave: a lethal Lich transforms on the same UID rather than exploding its still-living phylactery',async()=>{
  const b=await orbFixture();
  try{
    const r=b.raid,lich=Object.assign(r.makeSkull(false,true,32),{hp:1,maxHp:1,def:0,atk:0,enteredTurn:-1});
    showOrbFixture(b,i=>i===2?{type:'skull',skull:lich}:{type:'sword'});
    const alive=orbAt(b,2),img=alive.querySelector('img'),weapons=[orbAt(b,0),orbAt(b,1)];
    for(const i of [2,1,0])r.tryExtendChain(i);r.resolveChain();
    await until(()=>chainWaveAnimations(b).length===2);
    assert.equal(r.state.board.find(c=>c.type==='skull'&&c.skull.uid===lich.uid).skull.phylactery,true);
    assert.ok(alive.isConnected); assert.equal(alive.querySelector('img.phylactery-art')?.src.startsWith('data:image/png;base64,'), true);
    assert.notEqual(alive.querySelector('img'), img, 'the same orb UID receives the phylactery sprite when its visual state changes');
    assert.ok(!chainWaveAnimations(b).some(a=>a.target===alive));
    assert.equal(chainWaveAnimations(b).find(a=>a.target===weapons[1]).options.delay,0);
    assert.equal(chainWaveAnimations(b).find(a=>a.target===weapons[0]).options.delay,32);
    assert.deepEqual(b.errors,[]);
  }finally{b.dom.window.close();}
});

test('magitech Lich preview: chain tip and tile highlight indicate phylactery transition once lethal damage is reached', async () => {
  const b = await orbFixture();
  try {
    const doc = b.dom.window.document, r = b.raid;
    const lich = Object.assign(r.makeSkull(false, true, 32), { hp: 8, maxHp: 8, def: 1, atk: 0, enteredTurn: -1, element: 'fire' });
    const mob = Object.assign(r.makeSkull(false, false, 0), { hp: 4, maxHp: 4, def: 0, atk: 0, enteredTurn: -1, element: 'fire' });
    showOrbFixture(b, i => i === 0 ? { type: 'skull', skull: lich } : i === 4 ? { type: 'skull', skull: mob } : { type: 'sword', element: 'fire' });
    // 2 swords + Lich => damage 3 + 2*2 = 7 < 8 + 1 = 9: not enough for phylactery yet.
    for (const i of [0, 1, 2]) r.tryExtendChain(i);
    assert.equal(r.chainPreview().phylacteries.length, 0);
    assert.doesNotMatch(doc.querySelector('#chain-tip').textContent, /филактерий/);
    assert.equal(doc.querySelector('.cell[data-idx="0"]').classList.contains('phylactery-ready'), false);
    // Add 3rd sword => damage 3 + 3*2 = 9 >= 9: Lich transitions to phylactery.
    r.tryExtendChain(3);
    assert.equal(r.chainPreview().phylacteries.length, 1);
    assert.equal(r.chainPreview().lethals.length, 0, 'Lich phylactery transition still does not count as a rewarded kill');
    assert.match(doc.querySelector('#chain-tip').textContent, /Лич → филактерий/);
    assert.doesNotMatch(doc.querySelector('#chain-tip').textContent, /Убито 0\/1/);
    assert.equal(doc.querySelector('.cell[data-idx="0"]').classList.contains('phylactery-ready'), true);
    // Extending to an ordinary enemy shows both kill count and Lich phylactery transition.
    r.tryExtendChain(4);
    assert.match(doc.querySelector('#chain-tip').textContent, /Убито 1\/1 · Лич → филактерий/);
    assert.equal(doc.querySelector('.cell[data-idx="4"]').classList.contains('lethal'), true);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

for(const removed of [0,1])test(`magitech chain wave: ${removed} actual disappearances among three selected nodes handles survivors without an empty timing range`,async()=>{
  const b=await orbFixture();
  try{
    const r=b.raid,skulls=Array.from({length:3},()=>Object.assign(r.makeSkull(false,false,0),{hp:99,maxHp:99,def:99,atk:0,enteredTurn:-1}));
    showOrbFixture(b,i=>i<3&&!(removed===1&&i===0)?{type:'skull',skull:skulls[i]}:{type:'sword'});
    const original=[0,1,2].map(i=>orbAt(b,i));
    for(const i of [0,1,2])r.tryExtendChain(i);r.resolveChain();
    await until(()=>JSON.parse(retuneRaw(b)).next?.phase==='enemy');
    const bursts=chainWaveAnimations(b);assert.equal(bursts.length,removed);
    if(removed){assert.equal(bursts[0].target,original[0]);assert.equal(bursts[0].options.delay,0);assert.equal(bursts[0].options.duration,120);}
    for(const n of original.slice(removed))assert.ok(n.isConnected&&!bursts.some(a=>a.target===n));
    for(const a of b.dom.window.__orbTest.created.filter(a=>a.target.dataset.motion))assert.ok(Number.isFinite(a.options.delay)&&Number.isFinite(a.options.duration)&&a.options.delay+a.options.duration<=510);
    assert.deepEqual(b.errors,[]);
  }finally{b.dom.window.close();}
});

// V0 preferences are local presentation metadata, never a checkpoint/migration.
const visualKey = magitechKey('raid:visualPreferences');
function visualToggle(b, key, value, scope = 'menu') {
  const input = b.dom.window.document.querySelector(`#${scope}-visual [data-raid-visual="${key}"]`);
  assert.ok(input, `visible preference control: ${scope}/${key}`);
  input.checked = value;
  input.dispatchEvent(new b.dom.window.Event('change', { bubbles: true }));
}
const visualRecord = (shells, animations) => JSON.stringify({ version: 1, shells, animations });

test('magitech visual settings: exactly two independent choices, old appearance by default, no eager storage writes', async () => {
  const b = await openMagitech({ start: false });
  try {
    const d = b.dom.window.document;
    for (const scope of ['picker', 'menu']) {
      const inputs = [...d.querySelectorAll(`#${scope}-visual input[data-raid-visual]`)];
      assert.deepEqual(inputs.map(n => n.dataset.raidVisual), ['shells', 'animations']);
      assert.ok(inputs.every(n => n.type === 'checkbox' && n.checked && n.labels.length));
    }
    assert.equal(d.documentElement.dataset.raidShell, 'on'); assert.equal(d.documentElement.dataset.raidMotion, 'on');
    assert.equal(b.dom.window.localStorage.getItem(visualKey), null);
    assert.equal(b.raid.state, null); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

for (const shells of [true, false]) for (const animations of [true, false]) test(`magitech visual settings: ${shells}/${animations} retains chain, sockets, objects, images, semantics, RNG and raw saves`, async () => {
  const b = await orbFixture();
  try {
    const r = b.raid, d = b.dom.window.document;
    showOrbFixture(b, i => i === 8 ? { type: 'skull', skull: Object.assign(r.makeSkull(true), { atk: 0, hp: 99, maxHp: 99, countdown: 3 }) }
      : { type: 'sword', ...(i === 9 ? { frozen: true } : i === 10 ? { broken: true } : i === 11 ? { burn: true } : i === 12 ? { explosive: true } : {}) });
    [0, 1, 2].forEach(i => r.tryExtendChain(i));
    const state = JSON.stringify(r.state), raw = retuneRaw(b), marker = b.dom.window.localStorage.getItem(updatesKey);
    const objects = [...r.state.board], sockets = [...d.querySelectorAll('#board .cell')], orbs = sockets.map(n => n.firstElementChild);
    const images = orbs.map(n => n.querySelector('img')), labels = sockets.map(n => n.getAttribute('aria-label'));
    visualToggle(b, 'shells', shells); visualToggle(b, 'animations', animations);
    assert.equal(d.documentElement.dataset.raidShell, shells ? 'on' : 'off');
    assert.equal(d.documentElement.dataset.raidMotion, animations ? 'on' : 'off');
    assert.equal(JSON.stringify(r.state), state); assert.equal(retuneRaw(b), raw); assert.equal(b.dom.window.localStorage.getItem(updatesKey), marker);
    assert.equal(b.dom.window.localStorage.getItem(visualKey), visualRecord(shells, animations));
    objects.forEach((c, i) => {
      assert.equal(r.state.board[i], c); assert.equal(d.querySelector(`.cell[data-idx="${i}"]`), sockets[i]);
      assert.equal(orbAt(b, i), orbs[i]); assert.equal(orbs[i].querySelector('img'), images[i]);
      assert.equal(sockets[i].getAttribute('aria-label'), labels[i]);
    });
    for (const key of ['shells', 'animations']) assert.equal(d.querySelector(`#picker-visual [data-raid-visual="${key}"]`).checked, key === 'shells' ? shells : animations);
    assert.equal(d.querySelectorAll('.cell.selected').length, 3); assert.ok(d.querySelector('#chain-line').children.length);
    assert.equal(b.dom.window.getComputedStyle(orbs[8].querySelector('.skull-badge')).display, 'block');
    assert.notEqual(b.dom.window.getComputedStyle(orbs[9].querySelector('.orb-ice')).opacity, '0');
    assert.notEqual(b.dom.window.getComputedStyle(orbs[10].querySelector('.orb-crack')).opacity, '0');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech visual settings: OS reduced motion overrides only effective motion and manual on never defeats it', async () => {
  const b = await orbFixture();
  try {
    const d = b.dom.window.document, h = b.dom.window.__orbTest, raw = retuneRaw(b);
    h.reduce(true);
    assert.equal(d.documentElement.dataset.raidMotion, 'off');
    assert.equal(d.querySelector('[data-raid-visual="animations"]').checked, true);
    assert.match(d.querySelector('[data-raid-motion-note]').textContent, /систем|устройств/i);
    assert.equal(d.querySelector('[data-raid-motion-note]').hidden, false);
    visualToggle(b, 'animations', false); h.reduce(false);
    assert.equal(d.documentElement.dataset.raidMotion, 'off');
    visualToggle(b, 'animations', true); assert.equal(d.documentElement.dataset.raidMotion, 'on');
    assert.equal(retuneRaw(b), raw); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech visual settings: cancelling an active ordered wave finishes only visuals, never a phase or reward continuation', async () => {
  const held = [];
  const b = await openMagitech({ start: false, setup(w) {
    orbAnimationHarness(w);
    const timeout = w.setTimeout.bind(w);
    w.setTimeout = (fn, delay, ...args) => {
      if (delay === 420 || delay === 550) { held.push({ delay, fn: () => fn(...args) }); return timeout(() => {}, 100000); }
      return timeout(fn, delay, ...args);
    };
  } });
  try {
    b.dom.window.document.querySelector('.picker-card-btn:not(.locked)').click(); b.raid.newGame(2718);
    showOrbFixture(b, i => ({ type: i < 3 ? 'coin' : 'sword' }));
    [2, 1, 0].forEach(i => b.raid.tryExtendChain(i)); b.raid.resolveChain();
    assert.deepEqual(held.map(n => n.delay), [420]); held[0].fn();
    assert.deepEqual(held.map(n => n.delay), [420, 550]);
    const h = b.dom.window.__orbTest, all = [...h.created], state = JSON.stringify(b.raid.state), raw = retuneRaw(b);
    assert.equal(chainWaveAnimations(b).length, 3); assert.ok(all.some(n => n.playState === 'running'));
    visualToggle(b, 'animations', false);
    assert.ok(all.every(n => n.playState === 'idle')); assert.equal(b.dom.window.document.querySelectorAll('.orb-burst,[data-motion],.primed').length, 0);
    assert.equal(JSON.stringify(b.raid.state), state); assert.equal(retuneRaw(b), raw);
    all.forEach(a => { a.onfinish?.(); a.oncancel?.(); });
    visualToggle(b, 'animations', true); assert.equal(h.created.length, all.length, 'no replay of ended effects');
    assert.equal(b.raid.state.turn, 0); assert.deepEqual(held.map(n => n.delay), [420, 550]);
    visualToggle(b, 'animations', false); held[1].fn(); assert.equal(b.raid.state.turn, 1);
    assert.equal(h.created.length, all.length); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech visual settings: A1 targeting draft and free cancellation survive all four combinations', async () => {
  const b = await retuneFixture();
  try {
    const r = b.raid, d = b.dom.window.document; r.activateSkill('retune'); retunePick(b, 'fire', [0, 1, 2]);
    const state = JSON.stringify(r.state), raw = retuneRaw(b), status = d.querySelector('#retune-status').textContent;
    const chosen = [...d.querySelectorAll('.retune-chosen')];
    for (const shells of [true, false]) for (const motion of [true, false]) {
      visualToggle(b, 'shells', shells); visualToggle(b, 'animations', motion);
      assert.equal(JSON.stringify(r.state), state); assert.equal(retuneRaw(b), raw);
      assert.equal(d.querySelector('#retune-panel').hidden, false); assert.equal(d.querySelector('#retune-status').textContent, status);
      assert.deepEqual([...d.querySelectorAll('.retune-chosen')], chosen); assert.equal(d.querySelector('#retune-apply').disabled, false);
    }
    d.querySelector('#retune-cancel').click(); assert.equal(retuneRaw(b), raw); assert.equal(r.state.skillCooldowns.retune, 0);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

for (const operation of ['getItem', 'setItem']) test(`magitech visual settings: denied ${operation} is session-only and never touches progress`, async () => {
  const b = await openMagitech({ start: false, setup(w) {
    const native = w.Storage.prototype[operation];
    w.Storage.prototype[operation] = function (key, ...args) {
      if (key === visualKey) throw Error('blocked visual metadata');
      return native.call(this, key, ...args);
    };
  } });
  try {
    const before = raidStorage(b.dom.window); visualToggle(b, 'animations', false, 'picker');
    assert.equal(b.dom.window.document.documentElement.dataset.raidMotion, 'off');
    const status = b.dom.window.document.querySelector('#picker-visual [data-raid-visual-status]');
    if (operation === 'setItem') { assert.equal(status.hidden, false); assert.match(status.textContent, /не.*сохран|не.*запом|окн/i); }
    const after = raidStorage(b.dom.window); delete before[visualKey]; delete after[visualKey]; assert.deepEqual(after, before);
    assert.equal(b.raid.state, null); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech visual settings: malformed/oversized/foreign metadata uses safe defaults without repairing storage or executing text', async () => {
  for (const raw of ['{bad', 'null', '[]', 'false', '"false"', '{"version":2,"shells":false,"animations":false}', '{"version":1,"shells":"false","animations":0}', '{"__proto__":{"animations":false},"version":1}', 'x'.repeat(1100)]) {
    const b = await openMagitech({ start: false, saved: { [visualKey]: raw } });
    try {
      const root = b.dom.window.document.documentElement;
      assert.equal(root.dataset.raidShell, 'on', raw); assert.equal(root.dataset.raidMotion, 'on', raw);
      assert.equal(b.dom.window.localStorage.getItem(visualKey), raw); assert.equal(b.raid.state, null); assert.deepEqual(b.errors, []);
    } finally { b.dom.window.close(); }
  }
});

test('magitech visual settings: reload and storage events change only this profile; classic, browser and each SDK ID stay separate', async () => {
  const sdk = localAccountSDK(1701), accountKey = magitechAccountKey(1701, 'raid:visualPreferences');
  const saved = { [visualKey]: visualRecord(false, false), 'raid:visualPreferences': 'classic', [magitechAccountKey(1702, 'raid:visualPreferences')]: visualRecord(false, false) };
  let b = await openMagitech({ start: false, saved, url: testerRaidUrl, setup(w) { w.Telegram = { WebApp: sdk }; } });
  try {
    const d = b.dom.window.document; assert.equal(d.documentElement.dataset.raidMotion, 'on');
    visualToggle(b, 'shells', false, 'picker'); const persisted = raidStorage(b.dom.window);
    for (const [key, value] of Object.entries(saved)) assert.equal(persisted[key], value);
    assert.equal(persisted[accountKey], visualRecord(false, true));
    b.dom.window.close(); b = await openMagitech({ start: false, saved: persisted, url: testerRaidUrl, setup(w) { w.Telegram = { WebApp: sdk }; } });
    assert.equal(b.dom.window.document.documentElement.dataset.raidShell, 'off');
    b.dom.window.localStorage.setItem(accountKey, visualRecord(true, false));
    b.dom.window.dispatchEvent(new b.dom.window.StorageEvent('storage', { key: accountKey, newValue: visualRecord(true, false) }));
    assert.equal(b.dom.window.document.documentElement.dataset.raidShell, 'on'); assert.equal(b.dom.window.document.documentElement.dataset.raidMotion, 'off');
    const before = raidStorage(b.dom.window); sdk.initDataUnsafe.user.id = 1702;
    visualToggle(b, 'animations', true); assert.deepEqual(raidStorage(b.dom.window), before);
    assert.equal(b.dom.window.document.querySelector('[data-raid-visual-status]').hidden, false); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech visual settings: late SDK loads its own choices, unknown identity never copies browser or anonymous choices', async () => {
  const saved = { [visualKey]: visualRecord(false, false), [magitechAccountKey(1703, 'raid:visualPreferences')]: visualRecord(true, false) };
  const b = await openMagitech({ start: false, saved, url: testerRaidUrl });
  try {
    const d = b.dom.window.document; assert.equal(d.documentElement.dataset.raidMotion, 'on');
    visualToggle(b, 'shells', false, 'picker'); assert.deepEqual(raidStorage(b.dom.window), saved);
    b.dom.window.Telegram = { WebApp: localAccountSDK(1703) }; d.querySelector('script[src="https://telegram.org/js/telegram-web-app.js"]').onload();
    assert.equal(d.documentElement.dataset.raidShell, 'on'); assert.equal(d.documentElement.dataset.raidMotion, 'off');
    assert.deepEqual(raidStorage(b.dom.window), saved); assert.equal(b.raid.state, null); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

for (const version of [1, 2, 3, 4]) test(`magitech visual settings: reading/toggling before Continue v${version} never rewrites the run or frozen mech/pilot`, async () => {
  let b = await orbFixture();
  try {
    const key = magitechKey('raid:activeRun'), data = JSON.parse(retuneRaw(b));
    if (version === 2) { data.version = 2; data.combat.rules = 'elements-v1'; delete data.combat.affinity; }
    if (version === 3) { data.version = 3; data.combat.rules = 'elements-retune-v1'; delete data.combat.affinity; }
    const raw = JSON.stringify(version === 1 ? releasedMagitechCheckpoint(data) : data), saved = { ...raidStorage(b.dom.window), [key]: raw };
    b.dom.window.close(); b = await openMagitech({ start: false, saved });
    visualToggle(b, 'shells', false, 'picker'); visualToggle(b, 'animations', false, 'picker');
    assert.equal(retuneRaw(b), raw); assert.equal(b.raid.state, null);
    assert.equal(b.raid.resumeSavedRun(), true);
    const after = JSON.parse(retuneRaw(b)); assert.equal(after.version, version);
    assert.deepEqual(after.state.classConfig, data.state.classConfig); assert.deepEqual(after.rng, data.rng);
    assert.equal(Object.isFrozen(b.raid.state.classConfig), true); assert.equal(b.dom.window.document.documentElement.dataset.raidMotion, 'off');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech mech and pilot: current pilot details name the nemesis; changing next-run pilot cannot alter the frozen live pilot', async () => {
  const ids = ['adventurer', 'barbarian', 'mage', 'ranger', 'rogue', 'paladin', 'priest', 'raider', 'warrior', 'assassin'];
  const b = await openMagitech({ start: false, saved: { [magitechKey('raid:classLevels')]: JSON.stringify(Object.fromEntries(ids.map(id => [id, 10]))) } });
  try {
    const r = b.raid, d = b.dom.window.document;
    r.setSelectedClassId('warrior');
    const openPilot = () => {
      d.querySelector('.picker-card-btn[data-class-id="warrior"]').click();
      d.querySelector('.mech-detail-topic.pilot').click();
    };
    openPilot();
    const description = d.querySelector('.mech-detail-body .mech-detail-line');
    assert.match(description.textContent, /Пилот · Проломщик/);
    assert.match(description.textContent, /Смертный враг пилота — Хаотический/);
    const originalRace = r.getClassConfig('warrior').race;
    d.querySelector('.mech-pilot-arrow.next').click();
    d.querySelector('.mech-pilot-accept').click();
    const preparedRace = r.getClassConfig('warrior').race;
    assert.notEqual(preparedRace, originalRace);
    r.newGame(2718); const config = r.state.classConfig;
    assert.equal(r.clsRace(), preparedRace); assert.equal(Object.isFrozen(config), true);
    assert.equal(r.showClassPicker(), true); openPilot();
    d.querySelector('.mech-pilot-arrow.next').click(); d.querySelector('.mech-pilot-accept').click();
    assert.notEqual(r.getClassConfig('warrior').race, preparedRace);
    assert.equal(r.state.classConfig, config); assert.equal(r.clsRace(), preparedRace);
    const before = JSON.stringify(r.state), raw = retuneRaw(b), panel = d.createElement('div'); r.renderCharacterSummary(panel);
    const pilotNames = { Human: 'Наёмник', Elf: 'Биохимик', Dwarf: 'Проломщик', Sauren: 'Снабженец', Faun: 'Выживальщик', Orc: 'Штурмовик', Halfling: 'Бронник', Werewolf: 'Перехватчик', Dragonkin: 'Термит', Changeling: 'Тактик' };
    assert.match(panel.textContent, /Пилот/); assert.ok(panel.textContent.includes(pilotNames[preparedRace]));
    assert.match(panel.textContent, /Пассивы меха/); assert.match(panel.textContent, /Изъяны меха/);
    assert.equal(JSON.stringify(r.state), before); assert.equal(retuneRaw(b), raw); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech visual settings: static recipient numbers expire without a RAF loop; sound choice stays independent', async () => {
  let made = 0;
  const b = await openMagitech({ saved: { [visualKey]: visualRecord(false, false) }, setup(w) {
    orbAnimationHarness(w);
    const raf = w.requestAnimationFrame.bind(w); w.requestAnimationFrame = fn => { made++; return raf(fn); };
  } });
  try {
    const d = b.dom.window.document, sound = d.querySelector('#sound-on').checked;
    b.raid.damageHero(3); const label = d.querySelector('#raid-effects .float-fx');
    assert.ok(label); assert.match(label.textContent, /3/); assert.equal(made, 0, 'static feedback requires no positioning frames');
    visualToggle(b, 'shells', true); assert.equal(made, 0); assert.equal(d.querySelector('#sound-on').checked, sound);
    await until(() => !d.querySelector('#raid-effects .float-fx'));
    assert.equal(made, 0); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

// Direct board settings: a transient UI layer, never a reward/checkpoint/pause.
const gameSettings = b => b.dom.window.document.querySelector('#game-settings');
const openGameSettings = b => b.dom.window.document.querySelector('#settings-btn').click();
const closeGameSettings = b => b.dom.window.document.querySelector('#settings-close').click();
function settingsKey(b, key, extra = {}) {
  const w = b.dom.window;
  return w.document.activeElement.dispatchEvent(new w.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...extra }));
}

test('magitech haptics: combat feedback is immediate and off silences it', async () => {
  const calls = [];
  const b = await openMagitech({ start: false, setup(w) {
    w.Telegram = { WebApp: { HapticFeedback: {
      impactOccurred(kind) { calls.push(['impact', kind]); },
      selectionChanged() { calls.push(['selection']); },
      notificationOccurred(kind) { calls.push(['notification', kind]); },
    } } };
  } });
  try {
    const d = b.dom.window.document, radios = [...d.querySelectorAll('[name="haptics-combat"]')];
    radios.find(input => input.value === 'all').click();
    assert.deepEqual(calls, [['impact', 'soft']], 'the control itself gives immediate tactile confirmation within the user gesture');
    b.raid.setSelectedClassId('adventurer'); b.raid.newGame(2718);
    const enemy = b.raid.makeSkull(false, true, 3); enemy.hp = enemy.maxHp = 20;
    b.raid.state.board[0] = { type: 'skull', skull: enemy };
    b.raid.hitSkullAt(0, 0, { direct: false });
    assert.deepEqual(calls, [['impact', 'soft'], ['selection']], 'combat feedback fires synchronously');
    radios.find(input => input.value === 'off').click();
    b.raid.hitSkullAt(0, 0, { direct: false });
    assert.deepEqual(calls, [['impact', 'soft'], ['selection']]);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech haptics: important mode immediately signals kills and bosses but not nonlethal hits', async () => {
  const calls = [];
  const b = await openMagitech({ start: false, setup(w) {
    w.Telegram = { WebApp: { HapticFeedback: {
      impactOccurred(kind) { calls.push(['impact', kind]); },
      selectionChanged() { calls.push(['selection']); },
      notificationOccurred(kind) { calls.push(['notification', kind]); },
    } } };
  } });
  try {
    const r = b.raid, d = b.dom.window.document;
    r.setSelectedClassId('adventurer'); r.newGame(2718);
    r.state.board[0] = { type:'skull', skull:Object.assign(r.makeSkull(false), { hp:20, maxHp:20, def:0 }) };
    r.hitSkullAt(0, 1, { direct:false });
    assert.deepEqual(calls, []);
    r.state.board[0] = { type:'skull', skull:Object.assign(r.makeSkull(false), { hp:1, maxHp:1, def:0 }) };
    r.hitSkullAt(0, 100, { direct:false });
    assert.deepEqual(calls, [['selection']]);
    r.state.board[1] = { type:'skull', skull:Object.assign(r.makeSkull(true), { hp:1, maxHp:1, def:0 }) };
    r.hitSkullAt(1, 100, { direct:false });
    assert.deepEqual(calls, [['selection'], ['selection']]);
    assert.equal(d.querySelectorAll('.haptics-test, [data-haptic-test]').length, 0);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech haptics: selecting enemies responds once per addition and respects off mode', async () => {
  const calls = [];
  const b = await openMagitech({ start: false, setup(w) {
    w.Telegram = { WebApp: { HapticFeedback: { selectionChanged() { calls.push('selection'); } } } };
  } });
  try {
    const r = b.raid, w = b.dom.window, d = w.document;
    r.setSelectedClassId('adventurer'); r.newGame(2718);
    r.state.board[0] = { type:'skull', skull:r.makeSkull(false) };
    r.state.board[1] = { type:'skull', skull:r.makeSkull(false) };
    r.state.board[2] = { type:'sword' };
    const cell = i => d.querySelector(`.cell[data-idx="${i}"]`);
    raidPointer(w, cell(0), 'pointerdown');
    assert.deepEqual(calls, ['selection']);
    d.elementFromPoint = () => cell(1); raidPointer(w, w, 'pointermove');
    assert.deepEqual(calls, ['selection', 'selection']);
    raidPointer(w, w, 'pointermove');
    d.elementFromPoint = () => cell(2); raidPointer(w, w, 'pointermove');
    assert.deepEqual(calls, ['selection', 'selection']);
    raidPointer(w, w, 'pointercancel');
    d.querySelector('[name="haptics-combat"][value="off"]').click();
    raidPointer(w, cell(0), 'pointerdown');
    assert.deepEqual(calls, ['selection', 'selection']);
    raidPointer(w, w, 'pointercancel');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech haptics: browser fallback receives synchronous confirmation when the Telegram bridge rejects it', async () => {
  const calls = [];
  const b = await openMagitech({ start: false, setup(w) {
    Object.defineProperty(w.navigator, 'vibrate', { configurable: true, value(pattern) { calls.push(pattern); return true; } });
    w.Telegram = { WebApp: { HapticFeedback: { impactOccurred() { throw new Error('bridge unavailable'); } } } };
  } });
  try {
    const d = b.dom.window.document;
    d.querySelector('[name="haptics-garage"][value="all"]').click();
    assert.deepEqual(calls, [12]);
    d.querySelector('[name="haptics-menu"][value="off"]').click();
    assert.deepEqual(calls, [12]);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech controls: vibration is three-position radio in garage/menu/combat and affinity is five-position radio for the current mech', async () => {
  const b = await openMagitech({ start: false, saved: { [magitechKey('raid:classLevels')]: '{"adventurer":3}' } });
  try {
    const d = b.dom.window.document;
    const haptics = [...d.querySelectorAll('[data-raid-haptics]')];
    assert.equal(haptics.length, 9);
    for (const group of ['haptics-garage', 'haptics-menu', 'haptics-combat']) {
      const radios = haptics.filter(input => input.name === group);
      assert.deepEqual(radios.map(input => input.value), ['off', 'important', 'all']);
      assert.ok(radios.every(input => input.type === 'radio' && input.labels.length));
      assert.equal(radios.filter(input => input.checked).length, 1);
    }
    const affinity = [...d.querySelectorAll('input[name="affinity-adventurer"]')];
    assert.deepEqual(affinity.map(input => input.value), ['fire', 'air', 'earth', 'water', '']);
    assert.ok(affinity.every(input => input.type === 'radio' && input.labels.length));
    assert.equal(affinity.filter(input => input.checked).length, 1);
    const fire = affinity[0]; fire.checked = true; fire.dispatchEvent(new b.dom.window.Event('change', { bubbles: true }));
    assert.equal([...d.querySelectorAll('input[name="affinity-adventurer"]')].find(input => input.checked).value, 'fire');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech gameplay settings: three existing toggles live in one labelled compact dialog, replacing sound in the three-action header', async () => {
  const b = await openMagitech({ start: false });
  try {
    const d = b.dom.window.document, layer = gameSettings(b), button = d.querySelector('#settings-btn');
    assert.ok(layer && button);
    assert.equal(layer.hidden, true); assert.equal(layer.getAttribute('role'), 'dialog');
    assert.equal(layer.getAttribute('aria-modal'), 'true'); assert.ok(d.getElementById(layer.getAttribute('aria-labelledby')));
    assert.equal(button.getAttribute('aria-haspopup'), 'dialog'); assert.equal(button.getAttribute('aria-controls'), layer.id);
    assert.equal(button.getAttribute('aria-expanded'), 'false');
    assert.deepEqual([...d.querySelectorAll('.brand-actions > *')].map(n => n.id), ['settings-btn','stats-btn','menu-btn']);
    const controls = [...layer.querySelectorAll('input')];
    assert.deepEqual(controls.filter(n => n.dataset.raidVisual).map(n => n.dataset.raidVisual), ['shells','animations']);
    assert.equal(d.querySelectorAll('#sound-on').length, 1);
    assert.ok(controls.filter(n => n.dataset.raidVisual).every(n => n.type === 'checkbox' && n.getAttribute('role') === 'switch' && n.checked && n.labels.length));
    assert.equal(controls.filter(n => n.matches('[data-raid-haptics]')).length, 3, 'vibration is a three-position radio switch, not a dropdown');
    assert.ok(controls.filter(n => n.matches('[data-raid-haptics]')).every(n => n.type === 'radio' && n.labels.length));
    assert.equal(d.querySelectorAll('#sound-on').length, 1);
    openGameSettings(b); assert.equal(layer.hidden, true, 'not a preparation/hidden-board settings route');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech gameplay settings: opening/closing changes no game object, selection, checkpoint, metadata or image', async () => {
  const b = await orbFixture();
  try {
    const d = b.dom.window.document, r = b.raid;
    showOrbFixture(b, () => ({ type:'sword' })); [0,1,2].forEach(i => r.tryExtendChain(i));
    const state = JSON.stringify(r.state), saved = raidStorage(b.dom.window), objects = [...r.state.board];
    const nodes = [...d.querySelectorAll('#board > .cell')], orbs = nodes.map(n=>n.firstElementChild), images = orbs.map(n=>n.querySelector('img'));
    for (let i=0;i<3;i++) {
      openGameSettings(b); assert.equal(gameSettings(b).hidden, false);
      assert.equal(d.querySelector('#settings-btn').getAttribute('aria-expanded'), 'true');
      assert.equal(d.querySelector('#menu-modal').classList.contains('show'), false);
      assert.equal(r.state.pendingModal, null); closeGameSettings(b);
      assert.equal(gameSettings(b).hidden, true); assert.equal(d.activeElement.id, 'settings-btn');
    }
    assert.equal(JSON.stringify(r.state), state); assert.deepEqual(raidStorage(b.dom.window), saved);
    nodes.forEach((n,i)=>{assert.equal(d.querySelector(`.cell[data-idx="${i}"]`),n);assert.equal(n.firstElementChild,orbs[i]);assert.equal(orbs[i].querySelector('img'),images[i]);assert.equal(r.state.board[i],objects[i]);});
    assert.equal(d.querySelectorAll('.cell.selected').length, 3); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

for (const shells of [true,false]) for (const animations of [true,false]) test(`magitech gameplay settings: ${shells}/${animations} uses existing synchronized per-profile choices, not a new save format`, async () => {
  const b = await orbFixture();
  try {
    const d = b.dom.window.document, state = JSON.stringify(b.raid.state), raw = retuneRaw(b), seen = b.dom.window.localStorage.getItem(updatesKey);
    openGameSettings(b); visualToggle(b,'shells',shells,'game'); visualToggle(b,'animations',animations,'game');
    for (const scope of ['picker','menu','game']) for (const key of ['shells','animations']) assert.equal(d.querySelector(`#${scope}-visual [data-raid-visual="${key}"]`).checked,key==='shells'?shells:animations);
    assert.equal(d.documentElement.dataset.raidShell,shells?'on':'off'); assert.equal(d.documentElement.dataset.raidMotion,animations?'on':'off');
    assert.equal(b.dom.window.localStorage.getItem(visualKey),visualRecord(shells,animations));
    assert.equal(JSON.stringify(b.raid.state),state); assert.equal(retuneRaw(b),raw); assert.equal(b.dom.window.localStorage.getItem(updatesKey),seen);
    assert.equal(d.querySelector('#sound-on').checked,true); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech gameplay settings: Escape and Tab belong to this layer; pre-existing inert/ARIA attributes are restored exactly', async () => {
  const b = await orbFixture();
  try {
    const d = b.dom.window.document, menu = d.querySelector('#menu-modal'), shell = d.querySelector('.shell');
    menu.setAttribute('inert',''); menu.setAttribute('aria-hidden','false'); shell.setAttribute('aria-hidden','false');
    openGameSettings(b); assert.equal(shell.hasAttribute('inert'),true); assert.equal(shell.getAttribute('aria-hidden'),'true');
    assert.ok(gameSettings(b).contains(d.activeElement));
    d.querySelector('#settings-close').focus(); settingsKey(b,'Tab',{shiftKey:true}); assert.equal(d.activeElement.id,'music-on');
    settingsKey(b,'Tab'); assert.equal(d.activeElement.id,'settings-close');
    d.querySelector('#menu-btn').focus(); assert.ok(gameSettings(b).contains(d.activeElement),'even synthetic focus cannot escape');
    assert.equal(settingsKey(b,'Escape'),false); assert.equal(gameSettings(b).hidden,true);
    assert.equal(menu.hasAttribute('inert'),true); assert.equal(menu.getAttribute('aria-hidden'),'false');
    assert.equal(shell.hasAttribute('inert'),false); assert.equal(shell.getAttribute('aria-hidden'),'false');
    assert.equal(d.activeElement.id,'settings-btn'); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech gameplay settings: outside dismissal consumes the whole tap and never starts a chain, opens stats or activates a skill', async () => {
  const b = await orbFixture();
  try {
    const w = b.dom.window, d = w.document, r = b.raid;
    assert.equal(r.installSkillOption(r.prepareSkillOption('volley')),true);
    showOrbFixture(b, () => ({ type:'sword' })); const before=JSON.stringify(r.state),raw=retuneRaw(b);
    openGameSettings(b);
    // An old synthetic click, without a pointerdown in THIS popup session,
    // must not close a layer newly exposed by that old pointerup.
    gameSettings(b).dispatchEvent(new w.MouseEvent('click',{detail:1,bubbles:true,cancelable:true})); assert.equal(gameSettings(b).hidden,false);
    const cell=d.querySelector('.cell[data-idx="0"]'); raidPointer(w,cell,'pointerdown',41); raidPointer(w,cell,'pointerup',41);
    cell.dispatchEvent(new w.MouseEvent('click',{detail:1,bubbles:true,cancelable:true})); assert.equal(gameSettings(b).hidden,true);
    assert.equal(JSON.stringify(r.state),before); assert.equal(retuneRaw(b),raw);
    for (const selector of ['#stats-btn','#menu-btn','.skill']) {
      openGameSettings(b); d.querySelector(selector).click(); assert.equal(gameSettings(b).hidden,true);
      assert.equal(r.state.pendingModal,null); assert.equal(d.querySelector('#menu-modal').classList.contains('show'),false);
    }
    assert.equal(JSON.stringify(r.state),before); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech gameplay settings: stale pointer close cannot dismiss a newer session and a non-primary touch cannot open during a board drag', async () => {
  const b = await orbFixture();
  try {
    const w=b.dom.window,d=w.document;
    showOrbFixture(b,()=>({type:'sword'})); raidDrag(b,11);
    raidPointer(w,d.querySelector('#settings-btn'),'pointerdown',12,{isPrimary:false}); d.querySelector('#settings-btn').click();
    assert.equal(gameSettings(b).hidden,true); assert.deepEqual(Array.from(b.raid.state.selected),[0,1,2]);
    raidPointer(w,w,'pointercancel',11);
    openGameSettings(b); const close=d.querySelector('#settings-close'); raidPointer(w,close,'pointerdown',13);
    settingsKey(b,'Escape'); openGameSettings(b);
    close.dispatchEvent(new w.MouseEvent('click',{detail:1,bubbles:true,cancelable:true})); assert.equal(gameSettings(b).hidden,false);
    raidPointer(w,close,'pointerdown',14); close.dispatchEvent(new w.MouseEvent('click',{detail:1,bubbles:true,cancelable:true})); assert.equal(gameSettings(b).hidden,true);
    assert.equal(b.raid.state.turn,0); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech gameplay settings: the unpaid A1 element/targets survive toggles and first Escape, never accidentally apply or cancel', async () => {
  const b = await retuneFixture();
  try {
    const d=b.dom.window.document,r=b.raid; r.activateSkill('retune'); retunePick(b,'fire',[0,1,2]);
    const state=JSON.stringify(r.state),raw=retuneRaw(b);
    d.querySelector('[data-retune-element="fire"]').focus(); settingsKey(b,'Tab',{shiftKey:true});
    assert.equal(d.activeElement.id,'settings-btn','A1 reverse Tab reaches settings, not an unrelated skill or a focus bounce');
    openGameSettings(b); assert.equal(gameSettings(b).hidden,false);
    visualToggle(b,'shells',false,'game');visualToggle(b,'animations',false,'game');
    settingsKey(b,'Escape'); assert.equal(gameSettings(b).hidden,true); assert.equal(d.querySelector('#retune-panel').hidden,false);
    assert.equal(d.querySelector('[data-retune-element="fire"]').getAttribute('aria-pressed'),'true');
    assert.deepEqual([...d.querySelectorAll('.retune-chosen')].map(n=>+n.dataset.idx),[0,1,2]);
    assert.equal(d.querySelector('#retune-apply').disabled,false);assert.equal(JSON.stringify(r.state),state);assert.equal(retuneRaw(b),raw);
    openGameSettings(b); d.querySelector('#retune-apply').click(); assert.equal(gameSettings(b).hidden,true);
    assert.equal(d.querySelector('#retune-panel').hidden,false); assert.equal(JSON.stringify(r.state),state);
    settingsKey(b,'Escape'); assert.equal(d.querySelector('#retune-panel').hidden,true); assert.equal(JSON.stringify(r.state),state);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech gameplay settings: an ongoing wave keeps original 420/550 waits and finishes enemy once with the popup still open', async () => {
  const b=await orbFixture();
  try {
    const w=b.dom.window,d=w.document,r=b.raid,held=[],timeout=w.setTimeout.bind(w);
    showOrbFixture(b,i=>({type:[31,30,24].includes(i)?'coin':'sword'}));
    w.setTimeout=(fn,delay,...args)=>{if(delay===420||delay===550){const id=timeout(()=>{},2147483647);held.push({delay,fn:()=>fn(...args)});return id;}return timeout(fn,delay,...args);};
    [31,30,24].forEach(i=>r.tryExtendChain(i));r.resolveChain();
    openGameSettings(b);assert.equal(gameSettings(b).hidden,false);assert.deepEqual(held.map(n=>n.delay),[420]);
    const state=JSON.stringify(r.state);visualToggle(b,'animations',false,'game');assert.equal(JSON.stringify(r.state),state);assert.equal(r.state.hero.gold,0);
    held[0].fn();assert.deepEqual(held.map(n=>n.delay),[420,550]);assert.equal(r.state.turn,0);
    held[1].fn();assert.equal(r.state.turn,1);assert.equal(gameSettings(b).hidden,false);
    assert.equal(d.querySelectorAll('#board > .cell').length,36);assert.deepEqual(b.errors,[]);
  }finally{b.dom.window.close();}
});

test('magitech gameplay settings: a newly due reward dismisses only the transient panel, restores background and focuses its real choices', async () => {
  const b=await orbFixture();
  try {
    const d=b.dom.window.document,r=b.raid;openGameSettings(b);assert.equal(gameSettings(b).hidden,false);
    r.state.hero.gold=100;r.checkGoldBox();assert.equal(gameSettings(b).hidden,true);assert.equal(r.state.pendingModal,'gold');
    assert.equal(d.querySelector('.shell').hasAttribute('inert'),false);assert.equal(d.querySelector('#modal').hasAttribute('inert'),false);
    assert.ok(d.querySelector('#modal-body').contains(d.activeElement));assert.equal(r.state.hero.gold,0,'the normal full-bar payment occurred once, not an extra settings charge');
    const choices=JSON.stringify(r.state.menuCandidates);openGameSettings(b);assert.equal(gameSettings(b).hidden,true);
    assert.equal(JSON.stringify(r.state.menuCandidates),choices);assert.deepEqual(b.errors,[]);
  }finally{b.dom.window.close();}
});

test('magitech gameplay settings: SDK Back closes settings before A1, returns to native Close at root and never closes the host', async () => {
  const stub=raidTelegramStub();
  const b=await openMagitech({url:testerRaidUrl,setup(w){w.Telegram={WebApp:stub.tg};}});
  try {
    const d=b.dom.window.document;assert.equal(stub.tg.BackButton.isVisible,false);
    openGameSettings(b);assert.equal(gameSettings(b).hidden,false);assert.equal(stub.tg.BackButton.isVisible,true);
    const state=JSON.stringify(b.raid.state),saved=raidStorage(b.dom.window);
    stub.back();assert.equal(gameSettings(b).hidden,true);assert.equal(stub.tg.BackButton.isVisible,false);
    assert.equal(JSON.stringify(b.raid.state),state);assert.deepEqual(raidStorage(b.dom.window),saved);assert.equal(d.activeElement.id,'settings-btn');
    assert.equal(stub.calls.includes('close'),false);assert.deepEqual(b.errors,[]);
  }finally{b.dom.window.close();}
});

for(const lifecycle of ['deactivated','hidden','pagehide'])test(`magitech gameplay settings: ${lifecycle} cannot leave an inert or stale popup after returning`,async()=>{
  const stub=raidTelegramStub(),b=await openMagitech({url:testerRaidUrl,setup(w){w.Telegram={WebApp:stub.tg};}});
  try{
    const w=b.dom.window,d=w.document;openGameSettings(b);assert.equal(gameSettings(b).hidden,false);
    if(lifecycle==='deactivated')stub.fire(lifecycle);
    else if(lifecycle==='pagehide')w.dispatchEvent(new w.Event('pagehide'));
    else {Object.defineProperty(d,'visibilityState',{value:'hidden',configurable:true});d.dispatchEvent(new w.Event('visibilitychange'));}
    assert.equal(gameSettings(b).hidden,true);assert.equal(d.querySelector('.shell').hasAttribute('inert'),false);
    assert.equal(d.querySelector('#settings-btn').getAttribute('aria-expanded'),'false');assert.equal(b.raid.state.turn,0);assert.deepEqual(b.errors,[]);
  }finally{b.dom.window.close();}
});

test('magitech gameplay settings: OS priority and failed-write notices reach the direct controls; reload sound remains its previous session-only default',async()=>{
  const b=await orbFixture();
  try{
    const w=b.dom.window,d=w.document,raw=retuneRaw(b);openGameSettings(b);w.__orbTest.reduce(true);
    assert.equal(d.querySelector('#game-settings [data-raid-motion-note]').hidden,false);
    visualToggle(b,'animations',true,'game');assert.equal(d.documentElement.dataset.raidMotion,'off');
    const set=w.Storage.prototype.setItem;w.Storage.prototype.setItem=function(key,...args){if(key===visualKey)throw Error('Denied prefs');return set.call(this,key,...args);};
    visualToggle(b,'shells',false,'game');const status=d.querySelector('#game-settings [data-raid-visual-status]');assert.equal(status.hidden,false);assert.match(status.textContent,/только в этом окне/);
    d.querySelector('#sound-on').click();assert.equal(d.querySelector('#sound-on').checked,false);
    closeGameSettings(b);openGameSettings(b);assert.equal(d.querySelector('#sound-on').checked,false);assert.equal(retuneRaw(b),raw);assert.deepEqual(b.errors,[]);
  }finally{b.dom.window.close();}
});

// Finished-run navigation must never be another way to start/surrender a run.
const defeatButton = (b, id) => b.dom.window.document.getElementById('gameover-' + id);
const defeatKey = (b, sdk = false) => sdk ? 'raid:magitech:tg:701:activeRun' : magitechKey('raid:activeRun');
async function finishMagitechRaid(b) {
  endBuiltRaid(b.raid);
  await until(() => b.raid.state.pendingModal === 'gameover');
  await new Promise(resolve => setTimeout(resolve, 40)); // terminal UI's ordinary queued save
}
for (const sdk of [false, true]) test(`magitech defeat exit: ${sdk ? 'Telegram' : 'browser'} returns to preparation without restart, resurrection, extra records or foreign writes`, async () => {
  const stub = raidTelegramStub(), foreign = { 'raid:activeRun': 'classic', 'raid:tg:701:history': 'classic account', 'raid:magitech:tg:702:activeRun': 'another account' };
  const b = await openMagitech({ saved: foreign, url: sdk ? testerRaidUrl : 'https://raid.test/', setup(w) { w.Telegram = { WebApp: stub.tg }; } });
  try {
    const r = b.raid, d = b.dom.window.document;
    await finishMagitechRaid(b);
    const state = r.state, frozen = JSON.stringify({ ...state, pendingModal: null }), saved = raidStorage(b.dom.window), selected = r.getSelectedClassId();
    const back = defeatButton(b, 'picker'), close = defeatButton(b, 'close');
    assert.ok(back); assert.equal(back.textContent, 'К выбору меха'); assert.ok(close);
    assert.equal(close.hidden, !sdk); assert.equal(d.activeElement, back);
    assert.equal(d.querySelector('#modal').getAttribute('aria-labelledby'), 'modal-title');
    assert.ok(d.querySelector('.gameover-actions').compareDocumentPosition(d.querySelector('.stats-grid')) & b.dom.window.Node.DOCUMENT_POSITION_FOLLOWING, 'exit actions come before the tall statistics');
    back.click(); back.click();
    assert.equal(d.querySelector('#class-picker').classList.contains('show'), true);
    assert.equal(d.querySelector('#modal').classList.contains('show'), false);
    assert.equal(d.activeElement.id, 'picker-title'); assert.equal(d.querySelector('#picker-resume').hidden, true);
    assert.equal(r.state, state); assert.equal(state.over, true); assert.equal(state.hero.hp, 0); assert.equal(state.pendingModal, null);
    assert.equal(JSON.stringify(state), frozen); assert.equal(r.getSelectedClassId(), selected);
    assert.deepEqual(raidStorage(b.dom.window), saved); assert.equal(r.savedRunInfo(), null);
    assert.equal(r.getLocalHistory().classes.adventurer.games, 1); assert.equal(stub.calls.includes('close'), false);
    if (sdk) { await until(() => !stub.tg.BackButton.isVisible); assert.equal(stub.tg.isClosingConfirmationEnabled, false); }
    else assert.deepEqual(stub.calls, []);
    for (const [key, raw] of Object.entries(foreign)) assert.equal(b.dom.window.localStorage.getItem(key), raw);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

for (const action of ['escape', 'sdk-back']) test(`magitech defeat exit: ${action} leaves the result, but nested records return to it first`, async () => {
  const stub = raidTelegramStub(), sdk = action === 'sdk-back';
  const b = await openMagitech({ url: sdk ? testerRaidUrl : 'https://raid.test/', setup(w) { w.Telegram = { WebApp: stub.tg }; } });
  try {
    const d = b.dom.window.document;
    await finishMagitechRaid(b);
    const records = [...d.querySelectorAll('#modal-body button')].find(n => n.textContent === 'Рекорды и статистика');
    records.focus(); records.click();
    const goBack = () => sdk ? stub.back() : d.activeElement.dispatchEvent(new b.dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    goBack();
    assert.equal(d.querySelector('#history-modal').hidden, true); assert.equal(d.activeElement, records);
    assert.equal(b.raid.state.pendingModal, 'gameover');
    if (sdk) { await until(() => stub.tg.BackButton.isVisible); assert.equal(stub.tg.isClosingConfirmationEnabled, false); }
    goBack(); goBack();
    assert.equal(d.querySelector('#class-picker').classList.contains('show'), true); assert.equal(b.raid.state.over, true);
    assert.equal(b.raid.state.pendingModal, null); assert.equal(d.activeElement.id, 'picker-title');
    assert.equal(stub.calls.includes('close'), false); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech defeat exit: Tab/focus stays on non-destructive result controls, background and old touch clicks cannot start a game', async () => {
  const b = await openMagitech();
  try {
    const w = b.dom.window, d = w.document;
    await finishMagitechRaid(b);
    const state = b.raid.state, back = defeatButton(b, 'picker'), restart = defeatButton(b, 'restart');
    const tab = shiftKey => d.activeElement.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Tab', shiftKey, bubbles: true, cancelable: true }));
    assert.equal(d.activeElement, back); tab(true); assert.equal(d.activeElement, restart);
    tab(false); assert.equal(d.activeElement, back);
    d.querySelector('#menu-btn').focus(); assert.equal(d.activeElement, back);
    d.querySelector('#menu-restart').click(); d.querySelector('.picker-card-btn:not(.locked)').click();
    assert.equal(b.raid.state, state); assert.equal(d.querySelector('#restart-modal').classList.contains('show'), false);
    back.dispatchEvent(new w.MouseEvent('click', { detail: 1, bubbles: true }));
    assert.equal(state.pendingModal, 'gameover', 'the old opening tap did not start on this exit');
    back.click();
    const pick = d.querySelector('.picker-card-btn:not(.locked)');
    pick.dispatchEvent(new w.MouseEvent('click', { detail: 1, bubbles: true }));
    assert.equal(b.raid.state, state, 'a synthesized click after dismissing results must not start the mech underneath');
    back.click(); restart.click(); defeatButton(b, 'close').click();
    assert.equal(b.raid.state, state); assert.equal(state.over, true); assert.equal(d.querySelector('#class-picker').classList.contains('show'), true);
    raidPointer(w, pick, 'pointerdown'); pick.dispatchEvent(new w.MouseEvent('click', { detail: 1, bubbles: true }));
    assert.equal(b.raid.state, state, 'a new gesture selects a mech without skipping its launch confirmation');
    d.querySelector('.mech-go-button').click(); d.querySelector('#region-emblem-launch').click();
    assert.notEqual(b.raid.state, state, 'confirming the new launch starts a fresh game');
    const next = b.raid.state; back.click(); restart.click();
    assert.equal(b.raid.state, next); assert.equal(next.over, false); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech defeat exit: history quota preserves the terminal backup, pending warning and idempotent retry from preparation', async () => {
  const b = await openMagitech({ setup(w) {
    w.blockHistory = true;
    const set = w.Storage.prototype.setItem;
    w.Storage.prototype.setItem = function(key, value) { if (w.blockHistory && key === magitechKey('raid:history')) throw Error('Quota'); return set.call(this, key, value); };
  } });
  try {
    const w = b.dom.window, d = w.document;
    await finishMagitechRaid(b);
    const terminal = w.localStorage.getItem(defeatKey(b)), id = b.raid.state.historyId;
    assert.equal(JSON.parse(terminal).status, 'finished'); assert.match(d.querySelector('#run-history-status').textContent, /пока не записан/);
    defeatButton(b, 'picker').click();
    assert.equal(w.localStorage.getItem(defeatKey(b)), terminal); assert.equal(d.querySelector('#picker-history-status').hidden, false);
    launchMagitechMech(d);
    assert.equal(d.querySelector('#restart-modal').classList.contains('show'), true); assert.match(d.querySelector('#restart-confirm-text').textContent, /резервную запись/);
    d.querySelector('#restart-cancel').click(); assert.equal(b.raid.state.historyId, id);
    d.querySelector('#picker-history').click(); assert.equal(d.querySelector('#history-retry').hidden, false);
    w.blockHistory = false; d.querySelector('#history-retry').click(); d.querySelector('#history-retry').click();
    const history = w.localStorage.getItem(magitechKey('raid:history'));
    assert.equal(b.raid.getLocalHistory().classes.adventurer.games, 1); assert.equal(d.querySelector('#history-retry').hidden, true);
    d.querySelector('#history-close').click(); w.dispatchEvent(new w.Event('pagehide'));
    assert.equal(w.localStorage.getItem(defeatKey(b)), terminal); assert.equal(w.localStorage.getItem(magitechKey('raid:history')), history);
    assert.equal(b.raid.state.over, true); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech defeat exit: Telegram Close with a saved terminal backup recovers failed history once after reopening, never Continue', async () => {
  let stub = raidTelegramStub(), b = await openMagitech({ url: testerRaidUrl, setup(w) {
    w.Telegram = { WebApp: stub.tg };
    const set = w.Storage.prototype.setItem;
    w.Storage.prototype.setItem = function(key, value) { if (key === 'raid:magitech:tg:701:history') throw Error('Quota'); return set.call(this, key, value); };
  } });
  try {
    await finishMagitechRaid(b);
    const close = defeatButton(b, 'close'); close.click(); close.click();
    assert.equal(stub.calls.filter(n => n === 'close').length, 1); assert.equal(close.disabled, true);
    assert.equal(b.dom.window.document.querySelector('#exit-modal').hidden, true);
    const saved = raidStorage(b.dom.window), terminal = saved[defeatKey(b, true)]; b.dom.window.close();
    stub = raidTelegramStub();
    b = await openMagitech({ start: false, saved, url: testerRaidUrl, setup(w) { w.Telegram = { WebApp: stub.tg }; } });
    assert.equal(b.raid.state, null); assert.equal(b.dom.window.document.querySelector('#picker-resume').hidden, true);
    assert.equal(b.raid.getLocalHistory().classes.adventurer.games, 1); assert.equal(b.raid.retryRunHistory(), true);
    assert.equal(b.raid.getLocalHistory().classes.adventurer.games, 1); assert.equal(b.dom.window.localStorage.getItem(defeatKey(b, true)), terminal);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

for (const cancel of ['button', 'escape', 'sdk-back']) test(`magitech defeat exit: a failed terminal write needs explicit close confirmation and ${cancel} restores the result`, async () => {
  const stub = raidTelegramStub();
  const b = await openMagitech({ url: testerRaidUrl, setup(w) { w.Telegram = { WebApp: stub.tg }; } });
  try {
    const w = b.dom.window, d = w.document, set = w.Storage.prototype.setItem;
    let blocked = true;
    w.Storage.prototype.setItem = function(key, value) { if (blocked && /:(activeRun|history)$/.test(key)) throw Error('Denied'); return set.call(this, key, value); };
    await finishMagitechRaid(b);
    const close = defeatButton(b, 'close'), state = b.raid.state;
    assert.equal(d.querySelector('#run-save-status').hidden, false); assert.match(d.querySelector('#run-save-status').textContent, /Не удалось сохранить/);
    close.focus(); close.click();
    assert.equal(d.querySelector('#exit-modal').hidden, false); assert.equal(d.activeElement.id, 'exit-cancel');
    assert.match(d.querySelector('#exit-confirm-text').textContent, /результат|Результат/);
    assert.equal(stub.calls.includes('close'), false);
    if (cancel === 'button') d.querySelector('#exit-cancel').click();
    else if (cancel === 'sdk-back') stub.back();
    else d.activeElement.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    assert.equal(d.querySelector('#exit-modal').hidden, true); assert.equal(d.activeElement, close);
    assert.equal(state.pendingModal, 'gameover'); assert.equal(state.over, true);
    if (cancel === 'button') {
      close.click(); d.querySelector('#exit-confirm').click(); d.querySelector('#exit-confirm').click(); close.click();
      assert.equal(stub.calls.filter(n => n === 'close').length, 1, 'explicit discard is one native request');
    } else {
      blocked = false; close.click();
      assert.equal(stub.calls.filter(n => n === 'close').length, 1); assert.equal(d.querySelector('#run-save-status').hidden, true);
      assert.equal(b.raid.getLocalHistory().classes.adventurer.games, 1);
    }
    assert.equal(b.raid.state, state); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

for (const failure of ['missing', 'throwing']) test(`magitech defeat exit: ${failure} SDK close gives a visible result hint and usable mech exit`, async () => {
  const stub = raidTelegramStub({ overrides: { close: failure === 'missing' ? undefined : () => { throw Error('Unsupported'); } } });
  const b = await openMagitech({ url: testerRaidUrl, setup(w) { w.Telegram = { WebApp: stub.tg }; } });
  try {
    const d = b.dom.window.document;
    await finishMagitechRaid(b); defeatButton(b, 'close').click();
    assert.equal(d.querySelector('#run-telegram-status').hidden, false); assert.match(d.querySelector('#run-telegram-status').textContent, /закрытия.*Telegram/);
    assert.equal(defeatButton(b, 'close').disabled, false);
    defeatButton(b, 'picker').click(); assert.equal(d.querySelector('#class-picker').classList.contains('show'), true);
    assert.equal(b.raid.state.over, true); assert.equal(b.raid.getLocalHistory().classes.adventurer.games, 1); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech defeat exit: late SDK exposes Close without rewriting or adopting the anonymous terminal result', async () => {
  const b = await openMagitech({ url: testerRaidUrl });
  try {
    const w = b.dom.window, d = w.document;
    await finishMagitechRaid(b);
    assert.equal(defeatButton(b, 'close').hidden, true);
    const state = b.raid.state, saved = raidStorage(w), stub = raidTelegramStub();
    w.Telegram = { WebApp: stub.tg }; d.querySelector('script[src]').onload();
    assert.equal(defeatButton(b, 'close').hidden, false); assert.equal(b.raid.state, state);
    assert.deepEqual(raidStorage(w), saved); assert.equal(b.raid.saveRun(), false);
    defeatButton(b, 'close').click(); assert.equal(d.querySelector('#exit-modal').hidden, false); assert.equal(stub.calls.includes('close'), false);
    d.querySelector('#exit-cancel').click(); defeatButton(b, 'picker').click();
    assert.equal(d.querySelector('#class-picker').classList.contains('show'), true); assert.deepEqual(raidStorage(w), saved); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech defeat exit: returning and pagehide never overwrite a peer active checkpoint or a switched Telegram account', async () => {
  const stub = raidTelegramStub(), b = await openMagitech({ url: testerRaidUrl, setup(w) { w.Telegram = { WebApp: stub.tg }; } });
  try {
    const w = b.dom.window, key = defeatKey(b, true), peer = JSON.parse(w.localStorage.getItem(key));
    peer.state.historyId = 'run-peer-finished-ui'; peer.revision++;
    await finishMagitechRaid(b);
    const terminal = w.localStorage.getItem(key), history = w.localStorage.getItem('raid:magitech:tg:701:history'), active = JSON.stringify(peer);
    w.localStorage.setItem(key, active); w.dispatchEvent(new w.StorageEvent('storage', { key, oldValue: terminal, newValue: active, storageArea: w.localStorage }));
    assert.equal(b.raid.saveStatus.state, 'conflict');
    defeatButton(b, 'picker').click(); w.dispatchEvent(new w.Event('pagehide'));
    assert.equal(w.localStorage.getItem(key), active); assert.equal(w.localStorage.getItem('raid:magitech:tg:701:history'), history);
    stub.tg.initDataUnsafe.user.id = 702; w.dispatchEvent(new w.Event('pagehide'));
    assert.equal(w.localStorage.getItem(key), active); assert.equal(w.localStorage.getItem('raid:magitech:tg:702:activeRun'), null);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

test('magitech defeat exit: an explicit retry still starts exactly once, old result controls cannot act on it', async () => {
  const b = await openMagitech();
  try {
    await finishMagitechRaid(b);
    const w = b.dom.window, d = w.document, old = b.raid.state, restart = defeatButton(b, 'restart'), back = defeatButton(b, 'picker');
    assert.match(restart.textContent, /Ещё раз/); restart.click(); restart.click(); back.click();
    assert.notEqual(b.raid.state, old); assert.equal(b.raid.state.over, false); assert.equal(b.raid.state.turn, 0);
    assert.equal(b.raid.getLocalHistory().classes.adventurer.games, 1); assert.equal(d.querySelector('#class-picker').classList.contains('show'), false);
    assert.equal(JSON.parse(w.localStorage.getItem(defeatKey(b))).status, 'active'); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

for (const kind of ['level', 'gold', 'armor']) test(`magitech defeat exit: live ${kind} reward is not a finished run or a new surrender mechanism`, async () => {
  const stub = raidTelegramStub(), b = await openMagitech({ url: testerRaidUrl, setup(w) { w.Telegram = { WebApp: stub.tg }; } });
  try {
    const r = b.raid, d = b.dom.window.document;
    if (kind === 'level') { r.state.hero.xp = 100; r.checkLevelUp(); }
    else if (kind === 'gold') { r.state.hero.gold = 100; r.checkGoldBox(); }
    else { r.state.upgradeBar = 100; r.checkUpgradeBar(); }
    const before = JSON.stringify(r.state);
    assert.equal(defeatButton(b, 'picker'), null); assert.equal(r.showClassPicker(), false);
    d.querySelector('#modal').dispatchEvent(new b.dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); stub.back();
    assert.equal(JSON.stringify(r.state), before); assert.equal(r.state.pendingModal, kind); assert.equal(r.state.over, false);
    assert.equal(stub.calls.includes('close'), false); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});

// Approved per-raid pilot affinity: existing class progression, never a preference.
const affinityRules = 'elements-affinity-v1';
const affinityCounter = { fire: 'water', air: 'fire', earth: 'air', water: 'earth' };
const affinitySelect = (b, id = 'adventurer') => {
  const radios = [...b.dom.window.document.querySelectorAll(`input[type="radio"][name="affinity-${id}"]`)];
  if (!radios.length) return null;
  return {
    get value() { return radios.find(input => input.checked)?.value || ''; },
    get options() { return radios; },
    get length() { return radios.length; },
    set value(value) { radios.forEach(input => { input.checked = input.value === value; }); },
    dispatchEvent(event) { const checked = radios.find(input => input.checked); return checked ? checked.dispatchEvent(event) : false; },
  };
};
async function affinityFixture({ level = 3, element = 'fire', id = 'adventurer', ...options } = {}) {
  const b = await openMagitech({ start: false, ...options, saved: { ...options.saved, [magitechKey('raid:classLevels')]: JSON.stringify({ [id]: level }) } });
  if (level >= 2) {
    const input = affinitySelect(b, id); assert.ok(input, 'a fresh per-run choice is visible before starting');
    input.value = element || ''; input.dispatchEvent(new b.dom.window.Event('change', { bubbles: true }));
  }
  b.raid.setSelectedClassId(id); b.raid.newGame(2718);
  return b;
}
const affinityCombat = b => JSON.parse(retuneRaw(b)).combat;
for (const level of [1, 2, 3, 10]) test(`magitech affinity: class level ${level} freezes the exact per-raid tier, not hero XP or future trophies`, async () => {
  const b = await affinityFixture({ level });
  try {
    const { raid: r } = b, data = JSON.parse(retuneRaw(b));
    assert.equal(data.version, 4); assert.equal(data.combat.rules, affinityRules);
    assert.deepEqual(data.combat.affinity, { element: level >= 2 ? 'fire' : null, level });
    assert.equal(Object.hasOwn(r.state.classConfig, 'affinity'), false);
    assert.equal(r.getClassConfig('adventurer').race, 'Human');
    const before = JSON.stringify(data.combat.affinity);
    r.levelUpClass('adventurer'); r.state.hero.level = 99; assert.equal(r.saveRun(), true);
    assert.equal(JSON.stringify(affinityCombat(b).affinity), before);
    r.showClassPicker();
    if (r.classLevelOf('adventurer') >= 2) assert.equal(affinitySelect(b).value, '', 'preparation never remembers the previous raid');
    b.dom.window.document.querySelector('#picker-resume').click();
    assert.equal(JSON.stringify(affinityCombat(b).affinity), before);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});
for (const element of magitechElements) test(`magitech affinity: ${element} choice is unpaid UI only, not class config, storage, RNG or a live-run edit`, async () => {
  const b = await openMagitech({ start: false, saved: { [magitechKey('raid:classLevels')]: '{"adventurer":3}' } });
  try {
    const r = b.raid, doc = b.dom.window.document, input = affinitySelect(b);
    assert.ok(input); assert.equal(input.value, ''); assert.equal(input.options.length, 5);
    const before = JSON.stringify(raidStorage(b.dom.window)), config = JSON.stringify(r.getClassConfig('adventurer'));
    input.value = element; input.dispatchEvent(new b.dom.window.Event('change', { bubbles: true }));
    assert.equal(JSON.stringify(raidStorage(b.dom.window)), before); assert.equal(r.state, null);
    assert.equal(JSON.stringify(r.getClassConfig('adventurer')), config);
    assert.match(doc.querySelector('#affinity-note-adventurer').textContent, /30%.*20%.*5%/);
    doc.querySelector('.picker-card-btn[data-class-id="adventurer"]').click();
    if (!r.state) r.newGame(2718);
    assert.deepEqual(affinityCombat(b).affinity, { element, level: 3 });
    const live = JSON.stringify(affinityCombat(b));
    input.value = affinityCounter[element]; input.dispatchEvent(new b.dom.window.Event('change', { bubbles: true }));
    assert.equal(JSON.stringify(affinityCombat(b)), live, 'detached/background input cannot change a committed run');
    r.showClassPicker(); assert.equal(affinitySelect(b).value, '');
    const next = affinitySelect(b); next.value = 'water'; next.dispatchEvent(new b.dom.window.Event('change', { bubbles: true }));
    doc.querySelector('#picker-resume').click(); assert.equal(JSON.stringify(affinityCombat(b)), live);
    assert.equal(JSON.stringify(r.getClassConfig('adventurer')), config);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});
test('magitech affinity: replacement captures one prepared choice; cancel, hidden changes and repeat cannot silently reuse it', async () => {
  const b = await affinityFixture();
  try {
    const r = b.raid, doc = b.dom.window.document, original = retuneRaw(b), historyId = r.state.historyId;
    r.showClassPicker(); let input = affinitySelect(b); input.value = 'air'; input.dispatchEvent(new b.dom.window.Event('change'));
    launchMagitechMech(doc);
    assert.equal(doc.querySelector('#restart-modal').classList.contains('show'), true);
    assert.equal(retuneRaw(b), original); doc.querySelector('#restart-cancel').click(); assert.equal(retuneRaw(b), original);
    launchMagitechMech(doc);
    input.value = 'water'; input.dispatchEvent(new b.dom.window.Event('change'));
    doc.querySelector('#restart-confirm').click(); doc.querySelector('#restart-confirm').click();
    assert.deepEqual(affinityCombat(b).affinity, { element: 'air', level: 3 }); assert.notEqual(r.state.historyId, historyId);
    doc.querySelector('#menu-btn').click(); const run = retuneRaw(b); doc.querySelector('#menu-restart').click();
    assert.equal(doc.querySelector('#class-picker').classList.contains('show'), true, 'repeat first returns to fresh preparation');
    assert.equal(affinitySelect(b).value, ''); assert.equal(retuneRaw(b), run);
    launchMagitechMech(doc);
    if (doc.querySelector('#restart-modal.show')) doc.querySelector('#restart-confirm').click();
    assert.deepEqual(affinityCombat(b).affinity, { element: null, level: 3 });
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});
test('magitech affinity: a genuine new choice resets on preparation reload and never writes persistent affinity keys', async () => {
  let b = await openMagitech({ start: false, saved: { [magitechKey('raid:classLevels')]: '{"adventurer":3}', 'raid:activeRun': 'classic sentinel', [magitechAccountKey(702, 'raid:classLevels')]: '{"mage":10}' } });
  try {
    const input = affinitySelect(b); assert.ok(input); input.value = 'fire'; input.dispatchEvent(new b.dom.window.Event('change'));
    b = await reopenMagitech(b); assert.equal(affinitySelect(b).value, ''); assert.equal(b.raid.state, null);
    assert.ok(!Object.keys(raidStorage(b.dom.window)).some(key => /affinity/i.test(key)));
    assert.equal(b.dom.window.localStorage.getItem('raid:activeRun'), 'classic sentinel');
    assert.equal(b.dom.window.localStorage.getItem(magitechAccountKey(702, 'raid:classLevels')), '{"mage":10}');
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});
test('magitech affinity: one weighted draw per new weapon keeps all six original RNG streams and enemy assignments identical', async () => {
  const a = await affinityFixture({ element: null }), b = await affinityFixture();
  try {
    assert.deepEqual(Array.from(b.raid.state.rngSeeds), Array.from(a.raid.state.rngSeeds));
    assert.equal(affinityCombat(a).rng, affinityCombat(b).rng);
    let word = affinityCombat(b).rng, weapons = 0;
    const counts = Object.fromEntries(magitechElements.map(e => [e, 0]));
    for (let i = 0; i < 4000; i++) {
      const x = a.raid.createTile(), y = b.raid.createTile(); assert.equal(y.type, x.type);
      if (!['sword', 'arrow'].includes(y.type)) { assert.equal(Object.hasOwn(y, 'element'), false); continue; }
      weapons++; word = (word + 0x6d2b79f5) >>> 0;
      let z = Math.imul(word ^ word >>> 15, word | 1); z ^= z + Math.imul(z ^ z >>> 7, z | 61);
      const roll = ((z ^ z >>> 14) >>> 0) / 4294967296, bucket = Math.floor(roll * 100);
      const expected = bucket < 30 ? 'fire' : bucket < 55 ? 'air' : bucket < 80 ? 'earth' : 'water';
      assert.equal(y.element, expected); assert.equal(x.element, magitechElements[Math.floor(roll * 4)]); counts[y.element]++;
    }
    assert.ok(weapons > 700); assert.ok(counts.fire > counts.water);
    a.raid.saveRun(); b.raid.saveRun(); assert.equal(affinityCombat(b).rng, word, 'exactly one element draw per genuinely new weapon');
    for (let i = 0; i < 32; i++) assert.deepEqual(JSON.parse(JSON.stringify(b.raid.makeSkull(false, true))), JSON.parse(JSON.stringify(a.raid.makeSkull(false, true))));
    assert.deepEqual(Array.from(b.raid.state.rngSeeds), Array.from(a.raid.state.rngSeeds));
    a.raid.saveRun(); b.raid.saveRun(); assert.equal(affinityCombat(a).rng, affinityCombat(b).rng);
    assert.deepEqual(a.errors, []); assert.deepEqual(b.errors, []);
  } finally { a.dom.window.close(); b.dom.window.close(); }
});
const affinityTargetFactor = (from, to) => (magitechElements.indexOf(from) + 1) % 4 === magitechElements.indexOf(to) ? 1.25
  : (magitechElements.indexOf(to) + 1) % 4 === magitechElements.indexOf(from) ? .75 : 1;
for (const element of magitechElements) test(`magitech affinity: ${element} multiplies only the primary weapon contribution, every single/dual target and Boost`, async () => {
  const b = await affinityFixture({ element });
  try {
    const r = b.raid, s = r.state, counter = affinityCounter[element], other = magitechElements.find(e => e !== element && e !== counter);
    s.hero.weaponDamage = 7; s.hero.stats.strength = 4; s.hero.stats.damage = 2; s.pierce = 0;
    const target = r.makeSkull(false, false); target.hp = target.maxHp = 10000; target.def = 5;
    s.board = Array.from({ length: 36 }, () => ({ type: 'coin' }));
    s.board[0] = { type: 'sword', element, magicSword: true }; s.board[1] = { type: 'arrow', element: counter };
    s.board[2] = { type: 'sword', element: other }; s.board[3] = { type: 'sword', element, broken: true };
    s.board[4] = { type: 'skull', skull: target }; s.selected = [0, 1, 2, 3, 4]; s.chainType = 'sword';
    for (const boost of [0, 1]) for (let i = 0; i < 4; i++) for (const j of [-1, ...Array.from({ length: 3 - i }, (_, k) => i + 1 + k)]) {
      s.swordBoost = boost; target.element = magitechElements[i]; target.boss = j >= 0; target.special = false; target.type = target.boss ? 1 : 0;
      if (target.boss) target.secondaryElement = magitechElements[j]; else delete target.secondaryElement;
      const factor = e => target.boss ? (affinityTargetFactor(e, target.element) + affinityTargetFactor(e, target.secondaryElement)) / 2 : affinityTargetFactor(e, target.element);
      const expected = r.baseDamage() + 7 * (boost ? 2 : 1) * (5 * 1.05 * factor(element) + .95 * factor(counter) + factor(other));
      const before = JSON.stringify([s, affinityCombat(b)]), preview = r.chainPreview();
      assert.ok(Math.abs(preview.skulls[0].damage - expected) < 1e-9, `${element}/${target.element}/${target.secondaryElement}/${boost}`);
      assert.equal(JSON.stringify([s, affinityCombat(b)]), before, 'preview cannot roll, save or mutate');
    }
    target.boss = false; target.type = 0; target.element = other; delete target.secondaryElement; s.swordBoost = 0;
    const expected = r.chainPreview().skulls[0].damage;
    r.resolveChain(); await until(() => !s.resolving);
    assert.equal(target.hp, 10000 - Math.max(0, Math.round(expected) - 5), 'one rounding after mixed/magic sum, then armour');
    const secondary = { hp: 100, def: 0, type: 0 }; r.damageSkull(secondary, 11, { direct: false }); assert.equal(secondary.hp, 89);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});
test('magitech affinity: tier one changes drops but never damage; neutral choice has no hidden multiplier', async () => {
  for (const [level, element] of [[2, 'fire'], [3, null], [10, null]]) {
    const b = await affinityFixture({ level, element });
    try {
      const r = b.raid, s = r.state, skull = r.makeSkull(false, false); skull.element = 'air'; skull.hp = 100; skull.def = 0;
      s.board = Array.from({ length: 36 }, () => ({ type: 'coin' })); s.board[0] = { type: 'sword', element: 'fire' }; s.board[1] = { type: 'sword', element: 'water' }; s.board[2] = { type: 'skull', skull };
      s.selected = [0, 1, 2]; s.chainType = 'sword';
      assert.equal(r.chainPreview().skulls[0].damage, r.baseDamage() + s.hero.weaponDamage * 2.25);
      assert.deepEqual(b.errors, []);
    } finally { b.dom.window.close(); }
  }
});
test('magitech affinity: frozen selection and tier survive reload without consulting current class levels or rewriting the raw checkpoint', async () => {
  let b = await affinityFixture({ level: 3, element: 'earth' });
  try {
    b.raid.state.hero.hp = 7; assert.equal(b.raid.saveRun(), true);
    const raw = retuneRaw(b), saved = raidStorage(b.dom.window); b.dom.window.close();
    saved[magitechKey('raid:classLevels')] = '{"adventurer":1}';
    b = await openMagitech({ start: false, saved }); assert.equal(retuneRaw(b), raw); assert.equal(b.raid.savedRunInfo().combatRules, affinityRules);
    assert.equal(b.raid.resumeSavedRun(), true); assert.equal(b.raid.state.hero.hp, 7);
    assert.deepEqual(affinityCombat(b).affinity, { element: 'earth', level: 3 });
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});
test('magitech affinity: strict v4 decoding rejects corrupt or downgraded affinity without erasing raw progress', async () => {
  const a = await affinityFixture(); const valid = JSON.parse(retuneRaw(a)); a.dom.window.close();
  const edits = [d => { d.combat.affinity.level = 1; }, d => { d.combat.affinity.level = 11; }, d => { d.combat.affinity.element = 'ice'; },
    d => { d.combat.affinity.extra = true; }, d => { delete d.combat.affinity; }, d => { d.combat.rules = 'elements-retune-v1'; },
    d => { d.version = 3; d.combat.rules = 'elements-retune-v1'; }, d => { d.combat.affinity.level = 2.5; }];
  for (const edit of edits) {
    const data = JSON.parse(JSON.stringify(valid)); edit(data); const raw = JSON.stringify(data);
    const b = await openMagitech({ start: false, saved: { [magitechKey('raid:activeRun')]: raw } });
    try { assert.equal(b.raid.saveStatus.state, 'invalid'); assert.equal(b.raid.state, null); assert.equal(retuneRaw(b), raw); assert.deepEqual(b.errors, []); }
    finally { b.dom.window.close(); }
  }
});

test('magitech affinity: A1 retains the chosen raid affinity, and Dragonkin fire remains the inherited neutral secondary reaction', async () => {
  const b = await affinityFixture({ id: 'paladin', element: 'earth' });
  try {
    const r = b.raid; assert.equal(r.installSkillOption(r.prepareSkillOption('retune')), true);
    const enemy = Object.assign(r.makeSkull(false, false), { hp: 100, maxHp: 100, def: 0, atk: 0 });
    showOrbFixture(b, i => i === 7 ? { type: 'skull', skull: enemy } : { type: 'sword', element: 'water' });
    const s = r.state, liveEnemy = s.board[7].skull, combat = affinityCombat(b), rng = Array.from(s.rngSeeds);
    r.activateSkill('retune'); retunePick(b, 'earth', [0]); b.dom.window.document.querySelector('#retune-apply').click();
    assert.equal(s.board[0].element, 'earth'); assert.equal(s.skillCooldowns.retune, 12);
    assert.equal(liveEnemy.fireDamage, r.baseDamage()); assert.equal(liveEnemy.burn, true);
    assert.deepEqual(affinityCombat(b), combat, 'recolouring uses no elemental draws and never changes raid affinity');
    const expected = [...rng]; expected[5] = Math.imul(expected[5], 16807) >>> 0;
    assert.deepEqual(Array.from(s.rngSeeds), expected, 'the inherited single random target reaction is retained');
    assert.equal(r.saveRun(), true); assert.equal(r.resumeSavedRun(), true);
    assert.deepEqual(affinityCombat(b).affinity, { element: 'earth', level: 3 }); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});
test('magitech affinity: Uppercut stays an unboosted neutral W and Confused/Armoured/Chaotic/Meat Shield keep original gate priority', async () => {
  const b = await affinityFixture({ id: 'warrior' });
  try {
    const r = b.raid, s = r.state; s.turn = 10; s.hero.weaponDamage = 10; s.swordBoost = 1;
    s.board = Array.from({ length: 36 }, () => ({ type: 'coin' }));
    const target = Object.assign(r.makeSkull(false, false), { hp: 1000, maxHp: 1000, def: 0, atk: 0, element: 'air' });
    s.board[12] = { type: 'sword', element: 'fire' }; s.board[0] = { type: 'sword', element: 'fire' }; s.board[6] = { type: 'skull', skull: target };
    s.selected = [12, 6, 0]; s.chainType = 'sword';
    const raw = r.baseDamage() + 2 * 10 * 2 * 1.25 * 1.05;
    assert.equal(r.chainPreview().skulls[0].damage, raw + 10);
    target.type = 27; assert.equal(r.chainPreview().skulls[0].damage, raw, 'Armoured exits before Uppercut');
    s.selected = [0, 6, 12]; assert.equal(r.chainPreview().skulls[0].damage, 0);
    target.type = 14; assert.equal(r.chainPreview().skulls[0].damage, 0, 'Chaotic resists non-Uppercut');
    s.selected = [12, 6, 0]; assert.equal(r.chainPreview().skulls[0].damage, raw + 10);
    s.board[35] = { type: 'skull', skull: Object.assign(r.makeSkull(false, true, 30), { hp: 1000 }) };
    assert.equal(r.chainPreview().skulls[0].damage, raw + 10, 'Uppercut still precedes off-chain Meat Shield gate');
    s.selected = [0, 6, 12]; target.type = 0; assert.equal(r.chainPreview().skulls[0].damage, 0);
    s.classConfig = { ...s.classConfig, perks: [...s.classConfig.perks], flaws: [s.classConfig.flaws[0], 'Confused', null, null] }; s.turn = 0; s.selected = [12, 6, 0];
    assert.equal(r.chainPreview().skulls[0].damage, 0); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});
for (const version of [1, 2, 3]) test(`magitech affinity: released-format v${version} Continue never adopts a new tier even with meta level ten`, async () => {
  let b = await affinityFixture({ level: 10 });
  try {
    let data = JSON.parse(retuneRaw(b));
    if (version === 1) data = releasedMagitechCheckpoint(data);
    else { data.version = version; delete data.combat.affinity; data.combat.rules = version === 2 ? 'elements-v1' : 'elements-retune-v1'; }
    const raw = JSON.stringify(data), saved = { ...raidStorage(b.dom.window), [magitechKey('raid:activeRun')]: raw };
    b.dom.window.close(); b = await openMagitech({ saved, start: false });
    assert.equal(retuneRaw(b), raw); const input = affinitySelect(b); assert.ok(input); input.value = 'water'; input.dispatchEvent(new b.dom.window.Event('change'));
    assert.equal(retuneRaw(b), raw); assert.equal(b.raid.resumeSavedRun(), true);
    const after = JSON.parse(retuneRaw(b)); assert.equal(after.version, version); assert.deepEqual(after.combat, data.combat); assert.deepEqual(after.state, data.state);
    assert.deepEqual(after.rng, data.rng); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});
test('magitech affinity: stale preparation, history and update overlays cannot change the choice; the editor cannot persist it', async () => {
  const b = await openMagitech({ start: false, saved: { [magitechKey('raid:classLevels')]: '{"adventurer":3,"warrior":5}' } });
  try {
    const r = b.raid, d = b.dom.window.document, old = affinitySelect(b); assert.ok(old);
    old.value = 'fire'; old.dispatchEvent(new b.dom.window.Event('change'));
    d.querySelector('#picker-history').click(); old.value = 'air'; old.dispatchEvent(new b.dom.window.Event('change')); assert.equal(affinitySelect(b).value, 'fire'); d.querySelector('#history-close').click();
    d.querySelector('#picker-updates').click(); old.value = 'water'; old.dispatchEvent(new b.dom.window.Event('change')); assert.equal(affinitySelect(b).value, 'fire'); acknowledgeUpdate(b);
    r.openClassEditor('warrior'); assert.equal(d.querySelector('#class-editor select.picker-affinity'), null); d.querySelector('#class-editor-save').click();
    old.value = 'earth'; old.dispatchEvent(new b.dom.window.Event('change'));
    assert.equal(affinitySelect(b).value, 'fire', 'blocked stale events and editor rerender keep the unpaid pending choice');
    assert.ok(!b.dom.window.localStorage.getItem(magitechKey('raid:classConfigs')).includes('affinity'));
    launchMagitechMech(d); assert.deepEqual(affinityCombat(b).affinity, { element: 'fire', level: 3 });
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});
test('magitech affinity: defeat retry offers a fresh preparation choice without reviving or duplicating the finished run', async () => {
  const b = await affinityFixture();
  try {
    const r = b.raid, d = b.dom.window.document; await finishMagitechRaid(b);
    const raw = retuneRaw(b), state = JSON.stringify(r.state), history = JSON.stringify(r.getLocalHistory());
    d.querySelector('#gameover-restart').click();
    assert.equal(d.querySelector('#class-picker').classList.contains('show'), true); assert.equal(affinitySelect(b).value, '');
    assert.equal(retuneRaw(b), raw); assert.equal(JSON.stringify(r.getLocalHistory()), history); assert.equal(r.state.over, true);
    assert.equal(JSON.stringify({ ...r.state, pendingModal: 'gameover' }), state);
    launchMagitechMech(d);
    assert.equal(r.state.over, false); assert.deepEqual(affinityCombat(b).affinity, { element: null, level: 3 });
    assert.equal(r.getLocalHistory().classes.adventurer.games, 1); assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});
test('magitech affinity: late Telegram profile gets its own fresh preparation, never the anonymous choice or another account data', async () => {
  const saved = { [magitechAccountKey(701, 'raid:classLevels')]: '{"adventurer":3}', [magitechKey('raid:classLevels')]: '{"adventurer":10}', 'raid:classLevels': '{"mage":10}' };
  const b = await openMagitech({ url: telegramRaidUrl, saved, start: false });
  try {
    const d = b.dom.window.document; assert.equal(affinitySelect(b), null);
    const sdk = localAccountSDK(701); b.dom.window.Telegram = { WebApp: sdk }; d.querySelector('script[src="https://telegram.org/js/telegram-web-app.js"]').onload();
    if (updateLayer(b).classList.contains('show')) acknowledgeUpdate(b);
    assert.equal(affinitySelect(b).value, ''); affinitySelect(b).value = 'water'; affinitySelect(b).dispatchEvent(new b.dom.window.Event('change'));
    launchMagitechMech(d);
    const key = magitechAccountKey(701, 'raid:activeRun'), raw = b.dom.window.localStorage.getItem(key);
    assert.deepEqual(JSON.parse(raw).combat.affinity, { element: 'water', level: 3 });
    sdk.initDataUnsafe.user.id = 702; b.raid.saveRun();
    assert.equal(b.dom.window.localStorage.getItem(key), raw); assert.equal(b.dom.window.localStorage.getItem(magitechAccountKey(702, 'raid:activeRun')), null);
    assert.equal(b.dom.window.localStorage.getItem(magitechKey('raid:activeRun')), null); assert.equal(b.dom.window.localStorage.getItem('raid:classLevels'), saved['raid:classLevels']);
    assert.deepEqual(b.errors, []);
  } finally { b.dom.window.close(); }
});
