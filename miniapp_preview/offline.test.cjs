const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require('jsdom');

async function until(predicate, timeout=3000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { if (predicate()) return; await new Promise(r => setTimeout(r, 10)); }
  throw new Error('timeout');
}
function open() {
  const html = fs.readFileSync(path.join(__dirname, '..', 'lunapark-offline.html'), 'utf8');
  const errors = [], network = [];
  const console = new VirtualConsole(); console.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM(html, {url:'file:///lunapark-offline.html', runScripts:'dangerously', pretendToBeVisual:true,
    virtualConsole:console,
    beforeParse(window) {
      window.scrollTo = () => {};
      window.fetch = (...args) => { network.push(args); throw new Error('external network prohibited'); };
      window.HTMLDialogElement.prototype.showModal = function() {this.open = true;};
      window.HTMLDialogElement.prototype.close = function() {this.open = false;};
    }});
  return {dom, errors, network};
}

test('single HTML opens with self-contained assets and runs fake craft and production without network', async () => {
  const {dom, errors, network} = open();
  const doc = dom.window.document;
  try {
    await until(() => doc.querySelector('#profile-status').textContent.includes('Автономное демо'));
    assert.match(doc.querySelector('#demo-banner').textContent, /АВТОНОМНАЯ/);
    assert.ok([...doc.querySelectorAll('.scene > img')].every(img => img.src.startsWith('data:image/jpeg;base64,')));
    assert.equal(doc.querySelector('script[src], link[rel="stylesheet"]'), null);
    doc.querySelector('[data-action="enter"]').click();
    doc.querySelector('[data-area="laboratory"]').click();
    doc.querySelector('#craft-first').value = 'w'; doc.querySelector('#craft-second').value = 'f';
    doc.querySelector('[data-action="craft"]').click();
    await until(() => doc.querySelector('#craft-message').textContent.includes('Получено:'));
    assert.match(doc.querySelector('#craft-message').textContent, /Пар/);
    doc.querySelector('.dialog-close').click();
    doc.querySelector('[data-area="production"]').click();
    const remove = [...doc.querySelectorAll('[data-action="production-remove"]')].find(x => x.textContent.includes('Собрать'));
    remove.click();
    await until(() => doc.querySelector('#production-message').textContent.includes('разобрана'));
    doc.querySelector('#production-station').value = 'farm';
    doc.querySelector('#production-worker').value = 'spirit_w';
    doc.querySelector('[data-action="production-start"]').click();
    await until(() => doc.querySelector('#production-message').textContent.includes('запущена'));
    doc.querySelector('[data-action="production-advance"]').click();
    await until(() => doc.querySelector('#production-message').textContent.includes('час прошёл'));
    assert.match(doc.querySelector('#dialog-content').textContent, /добыто 3/);
    assert.deepEqual(network, []);
    assert.deepEqual(errors, []);
  } finally { dom.window.close(); }
});

test('ingredient cards can be dragged into the cauldron, removed, tapped and mixed', async () => {
  const {dom, errors} = open();
  const win = dom.window, doc = win.document;
  try {
    await until(() => doc.querySelector('#profile-status').textContent.includes('Автономное демо'));
    doc.querySelector('[data-action="enter"]').click();
    doc.querySelector('[data-area="laboratory"]').click();
    const token = doc.querySelector('[data-ingredient="w"]');
    const stage = doc.querySelector('#brew-stage');
    doc.elementFromPoint = () => stage;
    function pointer(type, x, y) {
      const event = new win.MouseEvent(type, {bubbles:true, button:0, clientX:x, clientY:y});
      Object.defineProperty(event, 'pointerId', {value:1});
      token.dispatchEvent(event);
    }
    pointer('pointerdown', 5, 5); pointer('pointermove', 50, 60); pointer('pointerup', 50, 60);
    assert.equal(doc.querySelector('#craft-first').value, 'w');
    assert.ok(doc.querySelector('[data-slot-index="0"] .icon-w'));
    doc.querySelector('[data-slot-index="0"]').click();
    assert.equal(doc.querySelector('#craft-first').value, '');
    await new Promise(r => setTimeout(r, 1));
    doc.querySelector('[data-ingredient="w"]').click();
    doc.querySelector('[data-ingredient="f"]').click();
    assert.equal(doc.querySelector('#craft-second').value, 'f');
    doc.querySelector('[data-action="craft"]').click();
    await until(() => doc.querySelector('#craft-message').textContent.includes('Получено:'));
    assert.match(doc.querySelector('#craft-message').textContent, /Пар/);
    assert.equal(doc.querySelector('#craft-first').value, '');
    assert.equal(doc.querySelector('#craft-second').value, '');
    assert.deepEqual(errors, []);
  } finally { dom.window.close(); }
});

test('buildings and expeditions show visual scenes driven by profile state', async () => {
  const {dom, errors} = open();
  const doc = dom.window.document;
  try {
    await until(() => doc.querySelector('#profile-status').textContent.includes('Автономное демо'));
    doc.querySelector('[data-action="enter"]').click();
    doc.querySelector('[data-area="buildings"]').click();
    assert.equal(doc.querySelector('#base-visual').hidden, false);
    assert.equal(doc.querySelector('#dialog-content').hidden, true);
    assert.equal(doc.querySelectorAll('#building-pins .building-pin').length, 4);
    doc.querySelector('[data-building="storage"]').click();
    assert.match(doc.querySelector('#building-detail').textContent, /Склад.*уровень 1/);
    doc.querySelector('[data-building="fort"]').click();
    assert.match(doc.querySelector('#building-detail').textContent, /Форт.*не построено/);
    doc.querySelector('.dialog-close').click();
    doc.querySelector('[data-area="expeditions"]').click();
    assert.equal(doc.querySelector('#expedition-visual').hidden, false);
    assert.equal(doc.querySelectorAll('.expedition-party').length, 2);
    assert.match(doc.querySelector('.party-searching').textContent, /Исследуют мир · 1/);
    assert.match(doc.querySelector('.party-returning').textContent, /Возвращаются · 1/);
    assert.match(doc.querySelector('#expedition-visual .scene-note').textContent, /Символическая/);
    assert.deepEqual(errors, []);
  } finally { dom.window.close(); }
});

test('offline profile resets on reopening the HTML', async () => {
  const first = open(), second = open();
  try {
    const request = id => ({method:'POST', body:JSON.stringify({ingredients:['w','f'], request_id:id})});
    await first.dom.window.fetch('/api/dev/craft', request('first'));
    const a = await (await first.dom.window.fetch('/api/alchemy/me')).json();
    const b = await (await second.dom.window.fetch('/api/alchemy/me')).json();
    assert.equal(a.profile.inventory.find(x => x.id === 'w').quantity, 11);
    assert.equal(b.profile.inventory.find(x => x.id === 'w').quantity, 12);
  } finally { first.dom.window.close(); second.dom.window.close(); }
});

test('compact inventory, cauldron, fighters and recipes expose every page and filters', async () => {
  const {dom, errors} = open();
  const doc = dom.window.document;
  try {
    await until(() => doc.querySelector('#profile-status').textContent.includes('Автономное демо'));
    const profile = dom.window.eval('profile');
    assert.deepEqual(Array.from(profile.fighters, fighter => fighter.unit_id), ['fire_swordsman', 'water_guard']);
    for (let i = 0; i < 12; i++) {
      profile.inventory.push({id:'test_' + i, name:'Предмет ' + i, quantity:i+1});
      profile.fighters.push({unit_id:i === 0 ? 'earth_shielder' : 'medic', name:'Боец ' + i, tier:1, xp:0, stamina:1, status:i%2 ? 'available' : 'raid'});
      profile.recipes.push({ingredients:['Элемент ' + i, 'Вода'], result:'Результат ' + i});
    }
    const click = selector => { const button = doc.querySelector(selector); assert.ok(button, selector); button.click(); };
    const collectPages = (kind, grid, itemSelector) => {
      const values = [];
      do {
        values.push(...[...doc.querySelectorAll(`${grid} ${itemSelector}`)].map(e => e.textContent));
        if (doc.querySelector(`[data-action="${kind}-next"]`).disabled) break;
        click(`[data-action="${kind}-next"]`);
      } while (values.length < 1000);
      return values;
    };
    click('[data-action="enter"]'); click('[data-area="inventory"]');
    assert.ok(collectPages('inventory', '#inventory-grid', '.orb-token').some(x => x.includes('Предмет 11')));
    click('[data-action="inventory-filter"][data-filter="spirits"]');
    assert.match(doc.querySelector('#inventory-grid').textContent, /Дух/);
    click('[data-action="inventory-filter"][data-filter="all"]');
    click('#inventory-grid .orb-token'); assert.match(doc.querySelector('#inventory-detail').textContent, /количество/);
    click('.dialog-close'); click('[data-area="fighters"]');
    assert.ok(doc.querySelector('.fighter-fire') && doc.querySelector('.fighter-water'));
    assert.ok(doc.querySelector('.fighter-placeholder').textContent.includes('🛡'));
    assert.ok(collectPages('fighters', '#fighters-grid', '.orb-token').some(x => x.includes('Боец 11')));
    click('[data-action="fighters-filter"][data-filter="other"]');
    assert.match(doc.querySelector('#fighters-grid').textContent, /Боец 0/);
    click('#fighters-grid .orb-token'); assert.match(doc.querySelector('#fighters-detail').textContent, /выносливость/);
    click('.dialog-close'); click('[data-area="laboratory"]');
    assert.ok(collectPages('craft', '#ingredient-shelf', '.orb-token').some(x => x.includes('Предмет 11')));
    click('[data-action="craft-filter"][data-filter="spirits"]'); assert.ok(doc.querySelector('[data-ingredient="spirit_w"]'));
    click('[data-action="open-recipes"]');
    assert.ok(collectPages('recipes', '#recipes-grid', '.recipe-card').some(x => x.includes('Результат 11')));
    assert.deepEqual(errors, []);
  } finally { dom.window.close(); }
});
