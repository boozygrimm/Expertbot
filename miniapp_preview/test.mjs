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
