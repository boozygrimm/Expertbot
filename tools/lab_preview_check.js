// Dev-only DOM smoke test for the lab preview. Not part of the bot runtime.
//
// Usage:
//   npm install --no-save jsdom@24
//   node tools/lab_preview_check.js [lunapark-discovery.html]
//
// It loads the page in jsdom, plays several research contracts through the real
// UI, reloads with the saved state and feeds a corrupted save. It does NOT check
// visual layout, real touch input, Telegram or a real browser.
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
const SAVE_KEY = 'bender-lunapark-lab-prealpha-v1';
const sleep = ms => new Promise(r => setTimeout(r, ms));

let failures = 0;
const check = (ok, msg, extra) => {
  console.log((ok ? 'PASS ' : 'FAIL ') + msg + (extra !== undefined && !ok ? ' → ' + JSON.stringify(extra) : ''));
  if (!ok) failures++;
};

const vc = new VirtualConsole();
let errors = [];
vc.on('jsdomError', e => errors.push(String((e && e.message) || e)));

function load(beforeParse) {
  return new JSDOM(html, {
    url: 'https://localhost/lab', runScripts: 'dangerously', pretendToBeVisual: true,
    virtualConsole: vc, beforeParse
  });
}

// Category sets mirrored from the page on purpose: if they change there, this
// test fails loudly instead of silently accepting an unreachable contract.
const CATEGORIES = {
  nature: new Set(('steam cloud ice cold lake sea ocean swamp dust wind pressure energy ' +
    'lava stone sand desert forest tree grass flower moss lichen algae mushroom fern bamboo cactus palm bush ' +
    'grove vine reed cane seed peat coal oil salt sulphur silicon limestone clay dirt ashes sky sun moon ' +
    'star rainbow storm thunder geyser volcano').split(' ')),
  animals: new Set(('beast cat catdog dog chicken livestock pig mouse bear beaver bat bird hummingbird parrot ' +
    'penguin vulture woodpecker fish fugu_fish stingray electric_eel whale crab lobster squid octopus jellyfish ' +
    'starfish clam mussels snail slug worm leech tick spider scorpion ant bee butterfly beetle insect dragonfly ' +
    'firefly termites cochineal scarab frog lizard salamander snake turtle dinosaur pterodactyl elephant ' +
    'kangaroo monkey panda camel wolf dragon phoenix').split(' ')),
  machines: new Set(('wheel cart chariot car motor plane hydroplane robot mech drone steam_boiler steam_engine ' +
    'steamboat locomotive bicycle motorcycle tractor lawnmower tool weapon firearm artillery watch magnet bulb ' +
    'hover space_shuttle submarine motorboat clipper wooden_ship tesla_tank flame_tank juggernaut').split(' '))
};
const TITLES = {'Природный след': 'nature', 'Живая лаборатория': 'animals', 'Механический цех': 'machines'};
const pairKey = ids => JSON.stringify([...ids].sort());
const label = (names, id) => names[id].replace(/^[\p{Extended_Pictographic}\p{Regional_Indicator}\p{Emoji_Modifier}\uFE0F\u200D]+\s+/u, '');

const save = win => JSON.parse(win.localStorage.getItem(SAVE_KEY) || '{}');
const texts = doc => ({
  title: doc.querySelector('#research-title').textContent,
  progress: doc.querySelector('#research-progress').textContent,
  hintMode: doc.querySelector('#research-hint').textContent,
  hintDisabled: doc.querySelector('#research-hint').disabled,
  nextHidden: doc.querySelector('#research-next').hidden,
  feedback: doc.querySelector('#research-feedback').textContent,
  feedbackHidden: doc.querySelector('#research-feedback').hidden
});
const goalCount = doc => Number((doc.querySelector('#research-progress').textContent.match(/Прогресс (\d+) \/ (\d+)/) || [])[2]);
const isDone = doc => doc.querySelector('#research-title').textContent.includes('выполнен');

function pick(win, doc, id) {
  const box = doc.querySelector('#search-box');
  if (box.hidden) doc.querySelector('#search-toggle').click();
  const input = doc.querySelector('#search');
  input.value = label(win.DISCOVERY_DATA.elements, id);
  input.dispatchEvent(new win.Event('input', {bubbles: true}));
  const el = doc.querySelector(`#elements .element[data-id="${id}"]`);
  if (!el) throw new Error(`element ${id} is not reachable through the collection search`);
  el.click();
}

async function mixPair(win, doc, a, b) {
  pick(win, doc, a); pick(win, doc, b);
  const mix = doc.querySelector('#mix');
  const before = mix.dataset.state;
  if (mix.disabled) return {before, state: before, disabled: true};
  mix.click();
  await sleep(900);
  return {before, state: doc.querySelector('#mix').dataset.state, disabled: false};
}

function frontier(win, saved) {
  const known = new Set((saved.found || []).map(x => x.key));
  const opened = new Set(saved.opened || []);
  // Mirrors the page rule: a contract target must also produce an element the
  // player does not own yet, otherwise progress could stall in place.
  return win.DISCOVERY_DATA.recipes.filter(r =>
    r.ingredients.every(id => opened.has(id)) && !known.has(pairKey(r.ingredients)) && !opened.has(r.result));
}

// Plays the visible contract using only information the player also has, and
// checks that every announced goal really can be completed.
async function solveContract(win, doc, labelText) {
  const names = win.DISCOVERY_DATA.elements;
  const wanted = goalCount(doc);
  const category = TITLES[labelText];
  let solved = 0;
  for (let step = 0; step < wanted; step++) {
    const saved = save(win);
    const targets = frontier(win, saved).filter(r => !category || CATEGORIES[category].has(r.result));
    if (!targets.length) {
      check(false, `contract "${labelText}" has no reachable target for step ${step + 1}`, {opened: saved.opened});
      return solved;
    }
    const target = targets[0];
    await mixPair(win, doc, target.ingredients[0], target.ingredients[1]);
    const after = save(win);
    const found = (after.found || []).some(x => x.key === pairKey(target.ingredients));
    const progress = isDone(doc) ? wanted : Number((doc.querySelector('#research-progress').textContent.match(/Прогресс (\d+)/) || [])[1] || 0);
    if (!found) {
      check(false, `announced target ${label(names, target.ingredients[0])} + ${label(names, target.ingredients[1])} did not open`, texts(doc));
      return solved;
    }
    solved = progress;
    check(progress > step, `progress ${progress}/${wanted} after a matching discovery (${labelText})`, texts(doc));
  }
  return solved;
}

(async () => {
  // ---------- first visit ----------
  const dom1 = load();
  const w1 = dom1.window, d1 = w1.document;
  await sleep(50);
  check(errors.length === 0, 'no script errors on load', errors);
  let t = texts(d1);
  check(t.progress.startsWith('Прогресс 0 / 2'), 'first contract asks for two discoveries', t);
  check(t.nextHidden === true, 'next-contract button hidden initially', t);
  check(t.hintDisabled === false && t.hintMode.includes('×1'), 'single hint available before mastery', t);
  check(d1.querySelectorAll('#elements .element').length === 4, 'starter collection has 4 elements', d1.querySelectorAll('#elements .element').length);

  d1.querySelector('#research-hint').click();
  await sleep(20);
  t = texts(d1);
  check(!t.feedbackHidden && t.feedback.startsWith('Подсказка:') && !t.feedback.includes('«'), 'first hint narrows the theme without naming a pair', t);
  check(t.hintDisabled === true && t.hintMode.includes('×0'), 'hint spent and button disabled', t);

  let r = await mixPair(w1, d1, 'w', 'w');
  check(r.state === 'known', 'water+water produces a known reaction state', r);
  t = texts(d1);
  check(t.progress.startsWith('Прогресс 1 / 2'), 'first discovery advances contract to 1/2', t);

  d1.querySelector('#mix').click();
  await sleep(900);
  check(texts(d1).progress.startsWith('Прогресс 1 / 2'), 'repeating the same reaction does not add progress', texts(d1));

  await mixPair(w1, d1, 'f', 'f');
  t = texts(d1);
  check(t.progress.startsWith('Прогресс 1 / 2'), 'failed experiment does not advance contract', t);
  const slotsAfterFail = [...d1.querySelectorAll('[data-slot]')].map(s => s.getAttribute('aria-label'));
  check(slotsAfterFail.every(x => /Нажми для замены/.test(x)), 'failed pair leaves both ingredients in the cauldron', slotsAfterFail);
  pick(w1, d1, 'a'); pick(w1, d1, 'e');
  const chosen = [...d1.querySelectorAll('[data-slot]')].map(s => s.title.replace(' — нажми для замены', ''));
  check(chosen[0] !== chosen[1], 'two clicks after a failure fill both slots instead of overwriting one', chosen);

  await solveContract(w1, d1, 'Открой две новые реакции');
  t = texts(d1);
  check(t.title.includes('выполнен') && t.nextHidden === false, 'contract completes and offers the next brief', t);
  check(t.progress.includes('Мастерство 1') && t.progress.includes('Анализатор улучшен'), 'mastery unlocked and analyzer upgraded', t);
  check(t.hintDisabled === true, 'hint stays closed while the finished contract waits for its next brief', t);

  d1.querySelector('#research-next').click();
  await sleep(20);
  t = texts(d1);
  const secondTitle = t.title;
  check(/Природный след|Живая лаборатория|Механический цех|Открой две новые реакции/.test(secondTitle), 'second contract has a known brief', t);
  check(t.nextHidden === true, 'second contract starts clean', t);
  check(t.hintMode.includes('×2') && t.hintDisabled === false, 'analyzer grants a second hint after mastery', t);

  // second hint is the concrete ingredient clue, and it points at a real target
  d1.querySelector('#research-hint').click();
  await sleep(20);
  d1.querySelector('#research-hint').click();
  await sleep(20);
  t = texts(d1);
  check(t.feedback.includes('«') && t.feedback.includes('Улика'), 'second hint names an ingredient', t);

  const solvedSecond = await solveContract(w1, d1, secondTitle);
  check(solvedSecond > 0 && isDone(d1), `second contract (${secondTitle}) can be completed`, texts(d1));

  // ---------- several contracts in a row stay solvable ----------
  let contractsPlayed = 1;
  for (let i = 0; i < 4; i++) {
    d1.querySelector('#research-next').click();
    await sleep(20);
    const title = texts(d1).title;
    const solved = await solveContract(w1, d1, TITLES[title] ? title : 'Открой две новые реакции');
    contractsPlayed++;
    check(solved > 0 && isDone(d1), `contract ${contractsPlayed} (${title}) is solvable and counted`, texts(d1));
  }

  const saved = save(w1);
  check(saved.research.contract > 1 && saved.found.length >= 4 && saved.opened.length >= 6, 'progress accumulates across contracts', {research: saved.research, found: saved.found.length});
  const raw = w1.localStorage.getItem(SAVE_KEY);
  const contractAtReload = saved.research.contract;
  const goalAtReload = saved.research.goal;

  // ---------- reload with persisted save ----------
  errors = [];
  const dom2 = load(win => win.localStorage.setItem(SAVE_KEY, raw));
  const w2 = dom2.window, d2 = w2.document;
  await sleep(50);
  check(errors.length === 0, 'no script errors on reload', errors);
  const reloaded = save(w2);
  check(reloaded.research.contract === contractAtReload && pairKey([reloaded.research.goal.category || 'x']) === pairKey([goalAtReload.category || 'x']), 'contract and goal survive reload', {before: goalAtReload, after: reloaded.research.goal});
  check(d2.querySelectorAll('#elements .element').length > 4, 'discovered elements restored on reload', d2.querySelectorAll('#elements .element').length);
  const beforeKnown = save(w2).found.length;
  const knownPair = await mixPair(w2, d2, 'w', 'w');
  check(knownPair.before === 'known', 'previously discovered pair recognised as known after reload', knownPair);
  check(save(w2).found.length === beforeKnown && save(w2).research.progress === reloaded.research.progress, 'known pair after reload does not count as a new discovery', save(w2).research);

  // ---------- a broader collection rotates to another theme ----------
  errors = [];
  const broad = JSON.stringify({v: 2, opened: ['w', 'f', 'e', 'a', 'lava', 'stone', 'steam', 'metal', 'dust', 'lake', 'pressure', 'swamp'], found: [], attempts: [], recent: [],
    research: {contract: 6, progress: 0, mastery: 1, hintsUsed: 0, goal: null, lastCategory: 'nature'}});
  const dom5 = load(win => win.localStorage.setItem(SAVE_KEY, broad));
  const w5 = dom5.window, d5 = w5.document;
  await sleep(50);
  check(errors.length === 0, 'broader collection loads without errors', errors);
  const broadTitle = texts(d5).title;
  check(broadTitle === 'Механический цех', 'board rotates away from the previous theme when another one is reachable', texts(d5));
  const broadSolved = await solveContract(w5, d5, broadTitle);
  check(broadSolved === 1 && isDone(d5), 'rotated contract is solvable', texts(d5));

  // ---------- an animal discovery must satisfy the animal brief ----------
  errors = [];
  const animalSeed = JSON.stringify({v: 2, opened: ['w', 'f', 'e', 'a', 'swamp', 'egg', 'life'], found: [], attempts: [], recent: [],
    research: {contract: 7, progress: 0, mastery: 1, hintsUsed: 0, goal: null, lastCategory: 'nature'}});
  const dom6 = load(win => win.localStorage.setItem(SAVE_KEY, animalSeed));
  const w6 = dom6.window, d6 = w6.document;
  await sleep(50);
  check(errors.length === 0, 'animal brief loads without errors', errors);
  check(texts(d6).title === 'Живая лаборатория', 'animal brief is offered when a creature is reachable', texts(d6));

  // a new element outside the theme is explained and does not count
  await mixPair(w6, d6, 'a', 'a');
  let t6 = texts(d6);
  check(!t6.feedbackHidden && t6.feedback.includes('не из темы'), 'off-theme discovery explains why it does not count', t6);
  check(t6.progress.startsWith('Прогресс 0 / 1'), 'off-theme discovery leaves the brief at 0/1', t6);

  await mixPair(w6, d6, 'egg', 'life');
  t6 = texts(d6);
  check(t6.title.includes('выполнен'), 'chicken satisfies the animal brief', t6);

  // a known pair keeps the golden circle tappable and clears the cauldron
  pick(w6, d6, 'egg'); pick(w6, d6, 'life');
  check(d6.querySelector('#mix').dataset.state === 'known', 'known pair is recognised again', d6.querySelector('#mix').dataset.state);
  check(d6.querySelector('#mix').disabled === false, 'known pair leaves the golden circle tappable', d6.querySelector('#mix').dataset.state);
  d6.querySelector('#mix').click();
  await sleep(30);
  const cleared = [...d6.querySelectorAll('[data-slot]')].map(s => s.getAttribute('aria-label'));
  check(cleared.every(x => /Пустой слот/.test(x)), 'tapping the known pair clears the cauldron', cleared);

  // ---------- corrupted save must not break the page ----------
  errors = [];
  const dom3 = load(win => win.localStorage.setItem(SAVE_KEY, '{"opened":["nope",42],"found":[{"ids":["x"]}],"research":{"contract":"bad","goal":{"kind":"category","category":"unicorns","count":99}}}'));
  const d3 = dom3.window.document;
  await sleep(50);
  check(errors.length === 0, 'corrupted save does not throw', errors);
  t = texts(d3);
  check(t.progress.startsWith('Прогресс 0 / 2') && t.title === 'Открой две новые реакции', 'corrupted save falls back to a fresh brief', t);
  check(d3.querySelectorAll('#elements .element').length === 4, 'corrupted save does not inject invalid elements', d3.querySelectorAll('#elements .element').length);

  // ---------- old v1 save without goal is upgraded in place ----------
  errors = [];
  const legacy = JSON.stringify({v: 1, opened: ['w', 'f', 'e', 'a', 'lake', 'dust'], found: [
    {key: '["w","w"]', ids: ['w', 'w'], result: 'lake'},
    {key: '["a","e"]', ids: ['a', 'e'], result: 'dust'}
  ], recent: ['dust'], attempts: [], research: {contract: 2, progress: 0, mastery: 1, hintsUsed: 0}});
  const dom4 = load(win => win.localStorage.setItem(SAVE_KEY, legacy));
  const w4 = dom4.window, d4 = w4.document;
  await sleep(50);
  check(errors.length === 0, 'legacy save without goal does not throw', errors);
  check(goalCount(d4) > 0 && texts(d4).nextHidden === true, 'legacy save gets a usable contract', texts(d4));

  console.log(failures ? `\n${failures} failure(s)` : '\nall checks passed');
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error('HARNESS ERROR', e); process.exit(2); });
