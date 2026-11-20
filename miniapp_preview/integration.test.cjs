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
