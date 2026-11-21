const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const net = require('node:net');
const { JSDOM, ResourceLoader, VirtualConsole } = require('jsdom');

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
  const port = await freePort(); base = `http://127.0.0.1:${port}`;
  server = spawn(python, ['-m', 'tests.telegram_fixture_server', '--port', String(port)], {cwd:root, stdio:'ignore'});
  await until(async () => { try { return (await fetch(base + '/')).ok; } catch { return false; } });
});
after(() => { if (server) server.kill(); });

async function openApp(sdkData) {
  const requests = [], errors = [];
  class LocalSDK extends ResourceLoader {
    fetch(url, options) {
      if (url === 'https://telegram.org/js/telegram-web-app.js') {
        const content = sdkData === null ? '' : `window.Telegram={WebApp:{initData:${JSON.stringify(sdkData)},ready(){}}};`;
        return Promise.resolve(Buffer.from(content));
      }
      return super.fetch(url, options);
    }
  }
  const console = new VirtualConsole(); console.on('jsdomError', error => errors.push(error.message));
  const dom = await JSDOM.fromURL(base + '/', {
    resources:new LocalSDK(), runScripts:'dangerously', pretendToBeVisual:true, virtualConsole:console,
    beforeParse(window) {
      window.scrollTo = () => {};
      window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
      window.HTMLDialogElement.prototype.close = function () { this.open = false; };
      window.fetch = (path, options = {}) => { requests.push({path, options}); return fetch(new URL(path, base), options); };
    },
  });
  await until(() => dom.window.document.querySelector('#refresh-button')?.hidden === false);
  return {dom, requests, errors};
}

test('Telegram-mode UI exchanges fake signed initData once, then reads using a bearer session', async () => {
  const initData = (await (await fetch(base + '/test-only/init-data')).json()).initData;
  const {dom, requests, errors} = await openApp(initData);
  const doc = dom.window.document;
  try {
    await until(() => doc.querySelector('#profile-status').textContent.includes('загружен'));
    assert.match(doc.querySelector('#demo-banner').textContent, /TELEGRAM/);
    assert.deepEqual(requests.map(x => x.path), ['/api/session', '/api/alchemy/me']);
    assert.equal(requests[0].options.method, 'POST');
    assert.equal(JSON.parse(requests[0].options.body).initData, initData);
    assert.match(requests[1].options.headers.Authorization, /^Bearer /);
    assert.doesNotMatch(requests[1].options.headers.Authorization, /auth_date/);
    doc.querySelector('[data-action="enter"]').click();
    doc.querySelector('[data-area="inventory"]').click();
    assert.match(doc.querySelector('#inventory-grid').textContent, /12Вода/);
    assert.match(doc.querySelector('#dialog-kicker').textContent, /ТЕСТОВЫЙ СТЕНД/);
    doc.querySelector('.dialog-close').click();
    doc.querySelector('[data-action="refresh"]').click();
    await until(() => requests.length === 3 && doc.querySelector('#profile-status').textContent.includes('загружен'));
    assert.equal(requests[2].path, '/api/alchemy/me');
    assert.equal(requests.filter(x => x.path === '/api/session').length, 1);
    doc.querySelector('[data-area="laboratory"]').click();
    assert.equal(doc.querySelector('#craft-panel').hidden, true);
    doc.querySelector('.dialog-close').click();
    doc.querySelector('[data-area="production"]').click();
    assert.equal(doc.querySelector('#production-panel').hidden, true);
    assert.equal(requests.filter(x => x.path === '/api/dev/production').length, 0);
    assert.deepEqual(errors, []);
  } finally { dom.window.close(); }
});

test('Telegram-mode UI does not fabricate a profile when opened outside Telegram', async () => {
  const {dom, requests} = await openApp(null);
  const doc = dom.window.document;
  try {
    await until(() => doc.querySelector('#profile-status').textContent.includes('Открой приложение'));
    assert.deepEqual(requests, []);
    doc.querySelector('[data-action="enter"]').click();
    doc.querySelector('[data-area="inventory"]').click();
    assert.match(doc.querySelector('#dialog-content').textContent, /Профиль недоступен/);
    assert.doesNotMatch(doc.querySelector('#dialog-content').textContent, /Вода/);
  } finally { dom.window.close(); }
});

test('invalid initData and replay errors never fall back to demo data', async () => {
  const {dom, requests} = await openApp('invalid');
  const doc = dom.window.document;
  try {
    await until(() => doc.querySelector('#profile-status').textContent.includes('Открой приложение заново'));
    assert.deepEqual(requests.map(x => x.path), ['/api/session']);
    doc.querySelector('[data-action="refresh"]').click();
    await new Promise(resolve => setTimeout(resolve, 25));
    assert.deepEqual(requests.map(x => x.path), ['/api/session']);
  } finally { dom.window.close(); }
});
