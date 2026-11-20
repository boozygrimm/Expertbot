// Dev-only DOM smoke test for the lab preview. Not part of the bot runtime.
//
// Usage:
//   npm install --no-save jsdom@24
//   node tools/lab_preview_check.js [lunapark-discovery.html]
//
// It loads the page in jsdom, plays the first contract, reloads with the saved
// state and feeds a corrupted save. It does NOT check visual layout, real touch
// input, Telegram or a real browser.
const fs = require('fs');
const path = require('path');
let JSDOM, VirtualConsole;
try {
  ({JSDOM, VirtualConsole} = require('jsdom'));
} catch (_) {
  console.error('jsdom is not installed. Run: npm install --no-save jsdom@24');
  process.exit(2);
}
const htmlPath = path.resolve(process.argv[2] || 'lunapark-discovery.html');
const html = fs.readFileSync(htmlPath, 'utf8');
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
const check = (ok, msg, extra) => {
  console.log((ok ? 'PASS ' : 'FAIL ') + msg + (extra !== undefined && !ok ? ' → ' + JSON.stringify(extra) : ''));
  if (!ok) failures++;
};
const vc = new VirtualConsole();
const errors = [];
vc.on('jsdomError', e => errors.push(String(e && e.message || e)));

function load(beforeParse) {
  return new JSDOM(html, {
    url: 'https://localhost/lab', runScripts: 'dangerously', pretendToBeVisual: true,
    virtualConsole: vc, beforeParse
  });
}

function clickEl(doc, id, n = 1) {
  for (let i = 0; i < n; i++) {
    const el = doc.querySelector(`#elements .element[data-id="${id}"]`);
    if (!el) throw new Error('element not found: ' + id);
    el.click();
  }
}

async function mixPair(win, doc, a, b) {
  clickEl(doc, a); clickEl(doc, b);
  const mix = doc.querySelector('#mix');
  if (mix.disabled) return {disabled: true, state: mix.dataset.state};
  mix.click();
  await sleep(900);
  return {disabled: false, state: doc.querySelector('#mix').dataset.state};
}

function texts(doc) {
  return {
    title: doc.querySelector('#research-title').textContent,
    progress: doc.querySelector('#research-progress').textContent,
    hintMode: doc.querySelector('#research-hint').textContent,
    hintDisabled: doc.querySelector('#research-hint').disabled,
    nextHidden: doc.querySelector('#research-next').hidden,
    feedback: doc.querySelector('#research-feedback').textContent,
    feedbackHidden: doc.querySelector('#research-feedback').hidden
  };
}

(async () => {
  // ---------- first visit ----------
  const dom1 = load();
  const w1 = dom1.window, d1 = w1.document;
  await sleep(50);
  check(errors.length === 0, 'no script errors on load', errors);
  let t = texts(d1);
  check(t.progress.startsWith('Прогресс 0 / 2'), 'initial contract progress 0/2', t);
  check(t.nextHidden === true, 'next-contract button hidden initially', t);
  check(t.hintDisabled === false && t.hintMode.includes('×1'), 'single hint available before mastery', t);
  check(d1.querySelectorAll('#elements .element').length === 4, 'starter collection has 4 elements', d1.querySelectorAll('#elements .element').length);

  d1.querySelector('#research-hint').click();
  await sleep(20);
  t = texts(d1);
  check(!t.feedbackHidden && t.feedback.includes('Улика') && t.feedback.includes('«'), 'hint reveals a masked ingredient clue', t);
  check(t.hintDisabled === true && t.hintMode.includes('×0'), 'hint spent and button disabled', t);

  // known starter reaction, discovered pair
  let r = await mixPair(w1, d1, 'w', 'w');
  check(r.state === 'known', 'water+water produces a known reaction state', r);
  t = texts(d1);
  check(t.progress.startsWith('Прогресс 1 / 2'), 'first discovery advances contract to 1/2', t);

  // duplicate attempt of the same pair must not advance again
  d1.querySelector('#mix').click();
  await sleep(900);
  t = texts(d1);
  check(t.progress.startsWith('Прогресс 1 / 2'), 'repeating the same reaction does not add progress', t);

  // failed pair must not consume anything and must not advance
  await mixPair(w1, d1, 'f', 'f');
  t = texts(d1);
  check(t.progress.startsWith('Прогресс 1 / 2'), 'failed experiment does not advance contract', t);
  const slotsAfterFail = [...d1.querySelectorAll('[data-slot]')].map(s => s.getAttribute('aria-label'));
  check(slotsAfterFail.every(x => /Нажми для замены/.test(x)), 'failed pair leaves both ingredients in the cauldron', slotsAfterFail);
  clickEl(d1, 'a'); clickEl(d1, 'e');
  const chosen = [...d1.querySelectorAll('[data-slot]')].map(s => s.title.replace(' — нажми для замены', ''));
  check(chosen[0] !== chosen[1], 'two clicks after a failure fill both slots instead of overwriting one', chosen);
  // rebuild the intended fresh pair for the contract check
  await mixPair(w1, d1, 'a', 'e');

  r = await mixPair(w1, d1, 'e', 'f');
  check(r.state === 'known', 'fire+earth discovered', r);
  t = texts(d1);
  check(t.title.includes('выполнен') && t.nextHidden === false, 'contract completes and offers next brief', t);
  check(t.progress.includes('Мастерство 1') && t.progress.includes('Анализатор улучшен'), 'mastery unlocked and analyzer upgraded', t);
  check(t.hintDisabled === true, 'hint stays closed while the finished contract waits for its next brief', t);

  d1.querySelector('#research-next').click();
  await sleep(20);
  t = texts(d1);
  check(t.progress.startsWith('Прогресс 0 / 2') && t.nextHidden === true, 'next contract starts clean', t);
  check(t.hintDisabled === false && t.hintMode.includes('×2'), 'analyzer grants a second hint after mastery', t);
  check(t.feedback.includes('Новый бриф'), 'next contract explains itself', t);

  const saved = JSON.parse(w1.localStorage.getItem('bender-lunapark-lab-prealpha-v1'));
  check(saved && saved.research.contract === 2 && saved.found.some(x => x.key === '[\"w\",\"w\"]') && saved.found.some(x => x.key === '[\"e\",\"f\"]') && saved.opened.includes('lake') && saved.opened.includes('lava'), 'state written to localStorage', saved && saved.found);
  const raw = w1.localStorage.getItem('bender-lunapark-lab-prealpha-v1');

  // ---------- reload with persisted save ----------
  errors.length = 0;
  const dom2 = load(win => win.localStorage.setItem('bender-lunapark-lab-prealpha-v1', raw));
  const w2 = dom2.window, d2 = w2.document;
  await sleep(50);
  check(errors.length === 0, 'no script errors on reload', errors);
  t = texts(d2);
  check(t.progress.startsWith('Прогресс 0 / 2'), 'contract survives reload', t);
  check(t.title.includes('две новые реакции'), 'contract title restored', t);
  check(d2.querySelectorAll('#elements .element').length > 4, 'discovered elements restored on reload', d2.querySelectorAll('#elements .element').length);
  const knownPair = await mixPair(w2, d2, 'w', 'w');
  check(knownPair.state === 'known', 'previously discovered pair recognised as known after reload', knownPair);
  t = texts(d2);
  check(t.progress.startsWith('Прогресс 0 / 2'), 'known pair after reload does not count as new discovery', t);

  // ---------- corrupted save must not break the page ----------
  errors.length = 0;
  const dom3 = load(win => win.localStorage.setItem('bender-lunapark-lab-prealpha-v1', '{"opened":["nope",42],"found":[{"ids":["x"]}],"research":"bad"}'));
  const d3 = dom3.window.document;
  await sleep(50);
  check(errors.length === 0, 'corrupted save does not throw', errors);
  t = texts(d3);
  check(t.progress.startsWith('Прогресс 0 / 2'), 'corrupted save falls back to a fresh contract', t);
  check(d3.querySelectorAll('#elements .element').length === 4, 'corrupted save does not inject invalid elements', d3.querySelectorAll('#elements .element').length);
  check(!d3.querySelector('#mix').disabled || d3.querySelector('#mix').dataset.state === 'empty', 'corrupted save leaves a usable cauldron', d3.querySelector('#mix').dataset.state);

  console.log(failures ? `\n${failures} failure(s)` : '\nall checks passed');
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error('HARNESS ERROR', e); process.exit(2); });
