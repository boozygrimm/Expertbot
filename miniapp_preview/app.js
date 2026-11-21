'use strict';
// Experimental test modes only; no production database writes or fake-data fallback on errors.
const mode = ['sandbox', 'telegram', 'demo'].includes(window.LUNAPARK_MODE) ? window.LUNAPARK_MODE : 'unsupported';
const pages = { park: document.getElementById('park-page'), lab: document.getElementById('lab-page') };
const dialog = document.getElementById('detail-dialog');
const demoProfile = {
  inventory: [{name:'💧 Вода', quantity:12}, {name:'🔥 Огонь', quantity:8}, {name:'🪨 Камень', quantity:6}],
  buildings: {storage:1, lab:2, altar:1, guild:1, barracks:1, fort:0, heritage_center:1},
  discovered_count:3,
  recipes: [{ingredients:['🔥 Огонь', '💧 Вода'], result:'☁️ Пар'}],
  expeditions: {searching:1},
  production: [{name:'⛏ Шахта', worker:'🪨 Дух Земли', stopped:false, total_mined:12}],
  fighters: [{name:'Искра', tier:1, xp:3, stamina:2, status:'available'}],
};
let profile = mode === 'demo' ? demoProfile : null;
let profileState = mode === 'demo' ? 'ready' : 'loading';
let activeArea = null;
let craftPending = false;
let productionPending = false;
let refreshPending = false;
let telegramBearer = null;
let telegramExchangeAttempted = false;
const orbViews = {craft:{filter:'all',page:0}, inventory:{filter:'all',page:0}, fighters:{filter:'all',page:0}, recipes:{filter:'all',page:0}};
const ORB_PAGE_SIZE = 8;
const expeditionStatuses = {searching:'Исследуют мир', awaiting_choice:'Ожидают решения', returning:'Возвращаются', arrived:'Прибыли', unknown:'Статус неизвестен'};
const fighterStatuses = {available:'На базе', expedition:'В экспедиции', raid:'В рейде', garrison:'В гарнизоне', dead:'Погиб', captured:'В плену', unknown:'Статус неизвестен'};
const buildingNames = {storage:'🎒 Склад', lab:'🧪 Лаборатория', altar:'⚙️ Алтарь', guild:'🧭 Гильдия', barracks:'⚔️ Казармы', fort:'🛡 Форт', heritage_center:'✨ Центр наследия'};
const areaMeta = {
  inventory: ['Склад', 'ИНВЕНТАРЬ', 'Ингредиенты и ресурсы алхимика.'],
  laboratory: ['Лаборатория', 'КОТЁЛ', 'Смешивай элементы и открывай новое.'],
  recipes: ['Книга рецептов', 'ОТКРЫТЫЕ РЕЦЕПТЫ', 'Известные сочетания алхимика.'],
  expeditions: ['Экспедиции', 'ПУТЕШЕСТВИЯ', 'Текущее состояние отрядов.'],
  production: ['Производство', 'РАБОЧИЕ ЛИНИИ', 'Состояние производственных станций.'],
  fighters: ['Бойцы', 'КАЗАРМЫ', 'Состав и состояние бойцов.'],
  buildings: ['Постройки', 'БАЗА', 'Уровни построек алхимика.'],
  about: ['Это тестовый стенд', 'ПРОТОТИП · БЕЗ РАБОЧЕЙ БАЗЫ', 'Здесь можно посмотреть парк и лабораторию на искусственном профиле. Реальный Telegram и MongoDB не подключены.'],
};
function showPage(name) {
  for (const [key, page] of Object.entries(pages)) page.hidden = key !== name;
  window.scrollTo({ top: 0, behavior: 'auto' });
  const heading = document.getElementById(name === 'park' ? 'park-title' : 'lab-title');
  heading.setAttribute('tabindex', '-1'); heading.focus({ preventScroll: true });
}
function renderVisualArea(name) {
  const container = document.getElementById('scene-content');
  const base = document.getElementById('base-visual');
  const expedition = document.getElementById('expedition-visual');
  const available = !!profile && (name === 'buildings' || name === 'expeditions');
  container.hidden = !available;
  base.hidden = !available || name !== 'buildings';
  expedition.hidden = !available || name !== 'expeditions';
  if (!available) return;
  if (name === 'buildings') {
    const pins = document.getElementById('building-pins'); pins.replaceChildren();
    const more = document.getElementById('other-buildings'); more.replaceChildren();
    document.getElementById('building-detail').textContent = 'Выбери постройку на сцене.';
    for (const [key, where] of [['storage','storage'], ['lab','lab'], ['altar','altar'], ['fort','fort'], ['guild','extra'], ['barracks','extra'], ['heritage_center','extra']]) {
      const level = Number.isInteger(profile.buildings[key]) ? profile.buildings[key] : 0;
      const button = document.createElement('button'); button.type = 'button';
      button.className = where === 'extra' ? 'building-extra' : 'building-pin pin-' + where;
      button.dataset.action = 'building-focus'; button.dataset.building = key;
      button.setAttribute('aria-label', buildingNames[key] + ': ' + (level ? 'уровень ' + level : 'не построено'));
      const shortNames = {storage:'Склад', lab:'Лаборатория', altar:'Алтарь', fort:'Форт', guild:'Гильдия', barracks:'Казармы', heritage_center:'Наследие'};
      const title = document.createElement('span'); title.textContent = shortNames[key];
      const badge = document.createElement('b'); badge.textContent = level ? 'УР. ' + level : 'НЕТ';
      button.append(title, badge); (where === 'extra' ? more : pins).append(button);
    }
  } else {
    const groups = document.getElementById('expedition-travellers'); groups.replaceChildren();
    for (const [status, count] of Object.entries(profile.expeditions)) {
      if (!Number.isInteger(count) || count <= 0) continue;
      const party = document.createElement('div');
      party.className = 'expedition-party party-' + (['searching', 'returning', 'awaiting_choice', 'arrived'].includes(status) ? status : 'unknown');
      party.setAttribute('aria-label', (expeditionStatuses[status] || 'Статус неизвестен') + ': ' + count);
      const figure = document.createElement('span'); figure.className = 'party-figure'; figure.setAttribute('aria-hidden', 'true');
      for (let i = 0; i < 3; i++) {
        const bot = document.createElement('i'); bot.className = 'march-bot';
        const head = document.createElement('i'); head.className = 'bot-head';
        const body = document.createElement('i'); body.className = 'bot-body';
        bot.append(head, body); figure.append(bot);
      }
      const label = document.createElement('strong'); label.textContent = (expeditionStatuses[status] || 'Статус неизвестен') + ' · ' + count;
      party.append(figure, label); groups.append(party);
    }
  }
}
function rowsFor(name) {
  if (name === 'about') {
    if (mode === 'sandbox') return [['Профиль и база', 'Вымышлены'], ['Крафт и производство', 'Только тестовые'], ['Сохранение', 'До перезапуска']];
    if (mode === 'telegram') return [['Вход', 'Через Telegram'], ['Профиль', 'Тестовая база'], ['Действия', 'Только просмотр']];
    return [['Профиль и база', 'Не подключены'], ['Игровые действия', 'Недоступны']];
  }
  if (!profile) return [[profileState === 'loading' ? 'Профиль загружается' : 'Профиль недоступен', '']];
  switch (name) {
    case 'inventory': return profile.inventory.map(x => [x.name, String(x.quantity)]);
    case 'laboratory': case 'recipes': return [['Открыто элементов', String(profile.discovered_count)], ...profile.recipes.map(x => [x.ingredients.join(' + '), x.result])];
    case 'expeditions': return Object.entries(profile.expeditions).map(([key, n]) => [expeditionStatuses[key] || 'Статус неизвестен', String(n)]);
    case 'production': return profile.production.map(x => [x.name + ' · ' + x.worker, x.stopped ? 'Остановлено' : 'Работает']);
    case 'fighters': return profile.fighters.map(x => [x.name + ' · ур. ' + x.tier, (fighterStatuses[x.status] || 'Статус неизвестен') + ' · XP ' + x.xp]);
    case 'buildings': return Object.entries(profile.buildings).map(([key, level]) => [buildingNames[key] || key, 'Ур. ' + level]);
    default: return [];
  }
}
function showArea(name) {
  const meta = areaMeta[name]; if (!meta) return;
  activeArea = name;
  dialog.dataset.panel = name;
  document.getElementById('dialog-kicker').textContent = (mode === 'telegram' ? 'ТЕСТОВЫЙ СТЕНД · ' : 'ПРИМЕР · ') + meta[1];
  document.getElementById('dialog-title').textContent = meta[0];
  document.getElementById('dialog-description').textContent = name === 'about' && mode === 'telegram'
    ? 'Закрытый тестовый стенд: просмотр через Telegram и отдельную тестовую базу. Запись игровых действий отключена.' : meta[2];
  const panel = document.getElementById('craft-panel');
  panel.hidden = mode !== 'sandbox' || name !== 'laboratory' || !profile;
  if (!panel.hidden) fillCraftOptions();
  const productionPanel = document.getElementById('production-panel');
  productionPanel.hidden = mode !== 'sandbox' || name !== 'production' || !profile?.sandbox;
  if (!productionPanel.hidden) fillProductionOptions();
  const recipesPanel = document.getElementById('recipes-panel');
  recipesPanel.hidden = name !== 'recipes' || !profile;
  if (!recipesPanel.hidden) renderRecipes();
  const inventoryPanel = document.getElementById('inventory-panel');
  inventoryPanel.hidden = name !== 'inventory' || !profile;
  if (!inventoryPanel.hidden) renderInventory();
  const fightersPanel = document.getElementById('fighters-panel');
  fightersPanel.hidden = name !== 'fighters' || !profile;
  if (!fightersPanel.hidden) renderFighters();
  const content = document.getElementById('dialog-content'); content.replaceChildren();
  renderVisualArea(name);
  content.hidden = (!!profile && ['buildings', 'inventory', 'fighters', 'recipes'].includes(name)) || !panel.hidden;
  content.className = 'dialog-content ' + (name === 'inventory' || name === 'laboratory' || name === 'expeditions' ? 'visual-cards' : '');
  const rows = content.hidden ? [] : rowsFor(name);
  if (!content.hidden && !rows.length) rows.push(['Здесь пока пусто', '']);
  for (const [label, value] of rows) {
    const row = document.createElement('div'); row.className = 'demo-item';
    const text = document.createElement('span'); text.textContent = label;
    const result = document.createElement('strong'); result.textContent = value;
    row.append(text, result); content.append(row);
  }
  if (name === 'production' && !productionPanel.hidden) {
    content.replaceChildren();
    if (!profile.production.length) {
      const empty = document.createElement('div'); empty.className = 'demo-item'; empty.textContent = 'Линий пока нет.'; content.append(empty);
    }
    for (const station of profile.production) {
      const row = document.createElement('div'); row.className = 'demo-item production-line';
      const text = document.createElement('span'); text.textContent = station.name + ' · ' + station.worker;
      const state = document.createElement('strong'); state.textContent = (station.stopped ? 'Остановлено' : 'Работает') + ' · добыто ' + station.total_mined;
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'production-remove';
      remove.dataset.action = 'production-remove'; remove.dataset.slot = station.sandbox_id;
      remove.disabled = productionPending || craftPending || refreshPending;
      remove.textContent = station.stopped ? 'Собрать и разобрать' : 'Демонтировать';
      row.append(text, state, remove); content.append(row);
    }
  }
  if (!dialog.open) dialog.showModal();
}
function fillCraftOptions() {
  for (const id of ['craft-first', 'craft-second']) {
    const select = document.getElementById(id);
    const selected = select.value;
    select.replaceChildren();
    const empty = document.createElement('option'); empty.value = ''; empty.textContent = 'Выбери элемент'; select.append(empty);
    for (const item of profile.inventory) {
      if (!item.id || ['recipe_scroll', 'pack_xl'].includes(item.id)) continue;
      const option = document.createElement('option'); option.value = item.id;
      option.textContent = item.name + ' · ' + item.quantity; select.append(option);
    }
    select.value = selected;
  }
  renderWorkbench();
}
const CORE_ELEMENTS = ['w', 'f', 'e', 'a'];
const DRAWN_ITEMS = new Set(['w','f','e','a','steam','stone','moss','energy']);
// Illustrated portraits exist for the two demo fighters; the remaining known
// types use distinct temporary emblems rather than pretending to be portraits.
const FIGHTER_EMBLEMS = {
  earth_shielder:['🛡',95], wind_scout:['🧭',195], ash_pikeman:['🔱',20], wave_spearman:['🔱',205],
  stone_axeman:['🪓',90], storm_archer:['🏹',195], rider_f:['🐎',20], rider_w:['🐎',205],
  rider_e:['🐎',90], rider_a:['🐎',195], saboteur:['💣',20], medic:['✚',205],
  engineer:['⚙',90], sniper:['🎯',195], mech:['🤖',90], flame_tank:['🔥',20],
  tesla_tank:['⚡',195], hover:['🛸',205], drone:['🚁',195], artillery:['💥',90],
  walker:['🦿',90], juggernaut:['⚔',20]
};
function itemGroup(item) {
  if (CORE_ELEMENTS.includes(item.id)) return 'elements';
  if (typeof item.id === 'string' && item.id.startsWith('spirit_')) return 'spirits';
  return 'other';
}
function itemLabel(item) {
  const name = typeof item.name === 'string' ? item.name : String(item.id || 'Предмет');
  return name.replace(/^[\p{Extended_Pictographic}\p{Regional_Indicator}\p{Emoji_Modifier}\uFE0F\u200D]+\s+/u, '') || name;
}
function itemIcon(item) {
  const icon = document.createElement('span');
  const id = typeof item.id === 'string' ? item.id : '';
  const base = id.startsWith('spirit_') ? id.slice(7) : id;
  const drawn = DRAWN_ITEMS.has(base);
  icon.className = 'ingredient-icon' + (drawn ? ' icon-' + base : '') + (id.startsWith('spirit_') ? ' spirit-icon' : '');
  if (!drawn) {
    const first = typeof item.name === 'string' ? item.name.split(' ')[0] : '';
    icon.textContent = first && first !== itemLabel(item) ? first : itemLabel(item).slice(0, 1).toUpperCase();
  }
  icon.setAttribute('aria-hidden', 'true');
  return icon;
}
function paginated(kind, entries, accepts, pageSize = ORB_PAGE_SIZE) {
  const state = orbViews[kind];
  const matching = entries.filter(entry => state.filter === 'all' || accepts(entry) === state.filter);
  const pages = Math.max(1, Math.ceil(matching.length / pageSize));
  state.page = Math.max(0, Math.min(state.page, pages - 1));
  document.getElementById(kind + '-page-label').textContent = (state.page + 1) + ' / ' + pages;
  document.querySelector('[data-action="' + kind + '-prev"]').disabled = state.page === 0;
  document.querySelector('[data-action="' + kind + '-next"]').disabled = state.page >= pages - 1;
  for (const button of document.querySelectorAll('#' + kind + '-filters [data-filter]')) {
    const selected = button.dataset.filter === state.filter;
    button.classList.toggle('active', selected); button.setAttribute('aria-pressed', String(selected));
  }
  return matching.slice(state.page * pageSize, (state.page + 1) * pageSize);
}
function orderedInventory() {
  return profile.inventory.map((item, index) => ({item, index})).sort((a, b) => {
    const aOrder = CORE_ELEMENTS.includes(a.item.id) ? CORE_ELEMENTS.indexOf(a.item.id) : 100;
    const bOrder = CORE_ELEMENTS.includes(b.item.id) ? CORE_ELEMENTS.indexOf(b.item.id) : 100;
    return aOrder - bOrder || a.index - b.index;
  });
}
function itemButton(item, kind, index) {
  const button = document.createElement('button'); button.type = 'button'; button.className = 'orb-token';
  if (kind === 'craft') button.dataset.ingredient = item.id;
  else { button.dataset.action = 'inventory-item'; button.dataset.index = String(index); }
  button.setAttribute('aria-label', item.name + ', количество ' + item.quantity + (kind === 'craft' ? '. Нажми или перетащи в котёл' : ''));
  button.setAttribute('title', item.name + ' · ' + item.quantity);
  const orb = document.createElement('span'); orb.className = 'orb-portrait';
  const quantity = document.createElement('b'); quantity.className = 'orb-quantity'; quantity.textContent = String(item.quantity);
  orb.append(itemIcon(item), quantity);
  const label = document.createElement('span'); label.className = 'orb-name'; label.textContent = itemLabel(item);
  button.append(orb, label); return button;
}
function renderRecipes() {
  const grid = document.getElementById('recipes-grid'); grid.replaceChildren();
  const recipes = paginated('recipes', profile.recipes, () => 'all', 6);
  for (const recipe of recipes) {
    const card = document.createElement('div'); card.className = 'recipe-card';
    const ingredients = document.createElement('span'); ingredients.textContent = recipe.ingredients.join(' + ');
    const result = document.createElement('strong'); result.textContent = '= ' + recipe.result;
    card.append(ingredients, result); grid.append(card);
  }
  if (!recipes.length) { const empty = document.createElement('p'); empty.className = 'orb-empty'; empty.textContent = 'Пока нет открытых рецептов.'; grid.append(empty); }
}
function renderInventory() {
  const grid = document.getElementById('inventory-grid'); grid.replaceChildren();
  const items = paginated('inventory', orderedInventory(), entry => itemGroup(entry.item));
  for (const entry of items) grid.append(itemButton(entry.item, 'inventory', entry.index));
  if (!items.length) { const empty = document.createElement('p'); empty.className = 'orb-empty'; empty.textContent = 'В этой группе пока пусто.'; grid.append(empty); }
}
function renderFighters() {
  const grid = document.getElementById('fighters-grid'); grid.replaceChildren();
  const entries = profile.fighters.map((fighter, index) => ({fighter, index}));
  const selected = paginated('fighters', entries, entry => ['available','expedition'].includes(entry.fighter.status) ? entry.fighter.status : 'other');
  for (const {fighter, index} of selected) {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'orb-token fighter-token';
    button.dataset.action = 'fighter-item'; button.dataset.index = String(index);
    const status = fighterStatuses[fighter.status] || 'Статус неизвестен';
    button.setAttribute('aria-label', fighter.name + ', уровень ' + fighter.tier + ', ' + status + ', опыт ' + fighter.xp);
    const portrait = document.createElement('span'); portrait.className = 'orb-portrait fighter-portrait';
    const image = document.createElement('span'); image.className = 'fighter-image';
    if (fighter.unit_id === 'fire_swordsman') image.classList.add('fighter-fire');
    else if (fighter.unit_id === 'water_guard') image.classList.add('fighter-water');
    else {
      const [symbol, hue] = FIGHTER_EMBLEMS[fighter.unit_id] || ['⚔', 260];
      image.classList.add('fighter-placeholder'); image.style.setProperty('--fighter-hue', String(hue)); image.textContent = symbol;
    }
    const level = document.createElement('b'); level.className = 'orb-quantity fighter-level'; level.textContent = String(fighter.tier);
    portrait.append(image, level);
    const label = document.createElement('span'); label.className = 'orb-name'; label.textContent = itemLabel(fighter);
    const state = document.createElement('small'); state.className = 'fighter-status'; state.textContent = status;
    button.append(portrait, label, state); grid.append(button);
  }
  if (!selected.length) { const empty = document.createElement('p'); empty.className = 'orb-empty'; empty.textContent = 'Бойцов в этой группе нет.'; grid.append(empty); }
}
function renderWorkbench() {
  const shelf = document.getElementById('ingredient-shelf'); shelf.replaceChildren();
  const selections = ['craft-first', 'craft-second'].map(id => document.getElementById(id).value);
  const entries = orderedInventory().filter(entry => entry.item.id && !['recipe_scroll', 'pack_xl'].includes(entry.item.id));
  const items = paginated('craft', entries, entry => itemGroup(entry.item));
  for (const {item, index} of items) shelf.append(itemButton(item, 'craft', index));
  if (!items.length) { const empty = document.createElement('p'); empty.className = 'orb-empty'; empty.textContent = 'В этой группе нет ингредиентов.'; shelf.append(empty); }
  for (let i = 0; i < 2; i++) {
    const slot = document.querySelector('[data-slot-index="' + i + '"]');
    const item = profile.inventory.find(x => x.id === selections[i]);
    slot.replaceChildren();
    if (item) slot.append(itemIcon(item)); else slot.textContent = '+';
    slot.classList.toggle('filled', !!item);
    slot.setAttribute('title', item ? item.name + ' — нажми, чтобы убрать' : 'Пустой слот');
    slot.setAttribute('aria-label', item ? item.name + ', убрать из котла' : 'Пустой слот ингредиента');
  }
}
function chooseIngredient(id) {
  if (!profile || craftPending || productionPending || refreshPending) return;
  const item = profile.inventory.find(x => x.id === id);
  if (!item) return;
  const inputs = ['craft-first', 'craft-second'].map(key => document.getElementById(key));
  const empty = inputs.find(input => !input.value);
  const message = document.getElementById('craft-message');
  if (!empty) { message.textContent = 'Сначала освободи место в котле.'; return; }
  if (inputs.filter(input => input.value === id).length >= item.quantity) { message.textContent = 'Больше этого элемента на складе нет.'; return; }
  empty.value = id; message.textContent = ''; renderWorkbench();
}
function fillProductionOptions() {
  const details = profile.sandbox;
  document.getElementById('production-summary').textContent = 'Линий: ' + profile.production.length + ' / ' + details.production_capacity + ' · тестовое время: ' + details.elapsed_hours + ' ч';
  const select = document.getElementById('production-worker');
  const selected = select.value;
  select.replaceChildren();
  const manual = document.createElement('option'); manual.value = 'none'; manual.textContent = '✋ Ручной труд'; select.append(manual);
  for (const worker of ['spirit_e', 'spirit_w', 'spirit_f', 'spirit_a']) {
    const item = profile.inventory.find(x => x.id === worker);
    if (!item) continue;
    const option = document.createElement('option'); option.value = worker; option.textContent = item.name + ' · ' + item.quantity; select.append(option);
  }
  select.value = [...select.options].some(option => option.value === selected) ? selected : 'none';
  const busy = productionPending || craftPending || refreshPending;
  document.querySelector('[data-action="production-start"]').disabled = busy || profile.production.length >= details.production_capacity;
  document.querySelector('[data-action="production-advance"]').disabled = busy || !profile.production.length;
}
async function productionAction(action, slotId) {
  if (mode !== 'sandbox' || craftPending || productionPending || refreshPending || !profile?.sandbox) return;
  const message = document.getElementById('production-message');
  const command = {action, request_id:crypto.randomUUID()};
  if (action === 'start') {
    command.station = document.getElementById('production-station').value;
    command.worker = document.getElementById('production-worker').value;
  } else if (action === 'remove') command.slot_id = slotId;
  productionPending = true;
  document.getElementById('refresh-button').disabled = true;
  showArea('production'); message.textContent = 'Шестерёнки крутятся…';
  try {
    const sessionResponse = await fetch('/api/dev/session', {cache:'no-store'});
    if (!sessionResponse.ok) throw new Error('session unavailable');
    const session = await sessionResponse.json();
    const response = await fetch('/api/dev/production', {method:'POST', cache:'no-store',
      headers:{Authorization:'tma ' + session.initData, 'Content-Type':'application/json'}, body:JSON.stringify(command)});
    const data = await response.json();
    if (!response.ok) {
      const known = {slots_full:'Нет свободных линий. Сначала разбери станцию.', missing_worker:'Этого духа уже нет на складе.', missing_energy:'Для духа нужна 1 энергия.', slot_missing:'Станция уже изменена. Обнови профиль.', sandbox_full:'Тестовый стенд заполнен. Нужен перезапуск.'};
      message.textContent = known[data.error] || 'Не удалось выполнить действие. Обнови профиль.';
      return;
    }
    if (!isProfile(data.profile) || !data.profile.sandbox) throw new Error('invalid profile');
    profile = data.profile;
    if (dialog.open && activeArea === 'production') showArea('production');
    message.textContent = {started:'Станция запущена на 24 тестовых часа.', removed:'Станция разобрана.', advanced:'Один тестовый час прошёл.'}[data.outcome] || 'Готово.';
  } catch (_error) {
    message.textContent = 'Результат неизвестен. Обнови профиль перед новым действием.';
  } finally {
    productionPending = false;
    document.getElementById('refresh-button').disabled = false;
    if (dialog.open && activeArea === 'production') showArea('production');
  }
}
async function craftExample() {
  if (mode !== 'sandbox' || craftPending || productionPending || refreshPending || !profile) return;
  const first = document.getElementById('craft-first').value;
  const second = document.getElementById('craft-second').value;
  const message = document.getElementById('craft-message');
  if (!first || !second) { message.textContent = 'Выбери два ингредиента.'; return; }
  const button = document.querySelector('[data-action="craft"]');
  craftPending = true; button.disabled = true; document.getElementById('refresh-button').disabled = true; message.textContent = 'Котёл работает…';
  try {
    // A fresh synthetic session and one operation ID per deliberate click.
    // There are no automatic retries when the server's result is uncertain.
    const requestId = crypto.randomUUID();
    const sessionResponse = await fetch('/api/dev/session', {cache:'no-store'});
    if (!sessionResponse.ok) throw new Error('session unavailable');
    const session = await sessionResponse.json();
    const response = await fetch('/api/dev/craft', {method:'POST', cache:'no-store',
      headers:{Authorization:'tma ' + session.initData, 'Content-Type':'application/json'},
      body:JSON.stringify({ingredients:[first, second], request_id:requestId})});
    const data = await response.json();
    if (!response.ok) {
      const known = {insufficient_resources:'Не хватает ингредиентов. Обнови профиль.', fighter_recipe:'Бойцы создаются в казармах.', invalid_request:'Не удалось проверить ингредиенты.', sandbox_full:'Тестовый стенд заполнен. Нужен перезапуск.'};
      message.textContent = known[data.error] || 'Не удалось выполнить действие.';
      return;
    }
    if (!isProfile(data.profile)) throw new Error('invalid profile');
    profile = data.profile;
    document.getElementById('craft-first').value = '';
    document.getElementById('craft-second').value = '';
    if (dialog.open && activeArea === 'laboratory') showArea('laboratory');
    message.textContent = data.outcome === 'crafted' ? 'Получено: ' + data.result : 'Пшик! Такого рецепта нет — ингредиенты сохранены.';
  } catch (_error) {
    // The write might have happened even if the response was lost: do not replay.
    message.textContent = 'Результат неизвестен. Обнови профиль перед новым действием.';
  } finally { craftPending = false; button.disabled = false; document.getElementById('refresh-button').disabled = false; }
}
function isProfile(value) {
  return value && typeof value === 'object' && Array.isArray(value.inventory) && Array.isArray(value.recipes)
    && Array.isArray(value.production) && Array.isArray(value.fighters)
    && value.buildings && typeof value.buildings === 'object' && value.expeditions && typeof value.expeditions === 'object';
}
async function telegramAuthorization() {
  if (telegramBearer) return 'Bearer ' + telegramBearer;
  const webApp = window.Telegram?.WebApp;
  if (!webApp || typeof webApp.initData !== 'string' || !webApp.initData) throw new Error('telegram_missing');
  if (telegramExchangeAttempted) throw new Error('reopen');
  telegramExchangeAttempted = true;
  const response = await fetch('/api/session', {method:'POST', cache:'no-store',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify({initData:webApp.initData})});
  if (response.status === 403) throw new Error('forbidden');
  if (!response.ok) throw new Error('reopen');
  const session = await response.json();
  if (typeof session.access_token !== 'string' || !session.access_token) throw new Error('reopen');
  telegramBearer = session.access_token; // memory only; never localStorage or URL
  return 'Bearer ' + telegramBearer;
}
async function refreshProfile() {
  if (!['sandbox', 'telegram'].includes(mode) || refreshPending || craftPending || productionPending) return;
  const status = document.getElementById('profile-status');
  const button = document.getElementById('refresh-button');
  refreshPending = true; profile = null; profileState = 'loading'; button.disabled = true;
  status.textContent = 'Загружаем тестовый профиль…';
  try {
    let authorization;
    if (mode === 'telegram') {
      authorization = await telegramAuthorization();
    } else {
      const sessionResponse = await fetch('/api/dev/session', {cache:'no-store'});
      if (!sessionResponse.ok) throw new Error('unavailable');
      const session = await sessionResponse.json();
      if (typeof session.initData !== 'string' || !session.initData) throw new Error('unavailable');
      authorization = 'tma ' + session.initData;
    }
    const response = await fetch('/api/alchemy/me', {headers:{Authorization:authorization}, cache:'no-store'});
    if (response.status === 401 && mode === 'telegram') { telegramBearer = null; throw new Error('reopen'); }
    if (response.status === 404) throw new Error('not_found');
    if (!response.ok) throw new Error('unavailable');
    const body = await response.json();
    if (!isProfile(body.profile)) throw new Error('unavailable');
    profile = body.profile; profileState = 'ready';
    status.textContent = mode === 'telegram' ? 'Тестовый профиль загружен · только просмотр' : 'Тестовый профиль загружен · действия только в песочнице';
  } catch (error) {
    profileState = 'error';
    const errors = {telegram_missing:'Открой приложение из тестового бота в Telegram.', reopen:'Сессия не подтверждена или истекла. Открой приложение заново.', forbidden:'Доступ к тестовому стенду не разрешён.', not_found:'Профиль в тестовой базе не найден.'};
    status.textContent = errors[error.message] || (mode === 'telegram' && telegramExchangeAttempted && !telegramBearer
      ? 'Не удалось подтвердить вход. Открой приложение заново.' : 'Не удалось загрузить тестовый профиль. Попробуй ещё раз.');
  } finally {
    refreshPending = false; button.disabled = false;
    if (dialog.open && activeArea) showArea(activeArea);
  }
}
let ingredientDrag = null;
let suppressIngredientClick = false;
document.addEventListener('pointerdown', event => {
  const token = event.target.closest('[data-ingredient]');
  if (!token || mode !== 'sandbox' || event.isPrimary === false || (event.button !== undefined && event.button !== 0)) return;
  ingredientDrag = {id: token.dataset.ingredient, pointer: event.pointerId, x: event.clientX, y: event.clientY,
    moved: false, ghost: null};
  token.setPointerCapture?.(event.pointerId);
});
document.addEventListener('pointermove', event => {
  if (!ingredientDrag || event.pointerId !== ingredientDrag.pointer) return;
  const dx = event.clientX - ingredientDrag.x, dy = event.clientY - ingredientDrag.y;
  if (!ingredientDrag.moved && Math.hypot(dx, dy) > 8) {
    ingredientDrag.moved = true;
    ingredientDrag.ghost = document.createElement('div'); ingredientDrag.ghost.className = 'drag-ghost';
    ingredientDrag.ghost.textContent = profile?.inventory.find(item => item.id === ingredientDrag.id)?.name.split(' ')[0] || '✦';
    document.body.append(ingredientDrag.ghost);
  }
  if (!ingredientDrag.moved) return;
  ingredientDrag.ghost.style.left = event.clientX + 'px'; ingredientDrag.ghost.style.top = event.clientY + 'px';
  const under = document.elementFromPoint(event.clientX, event.clientY);
  document.getElementById('brew-stage').classList.toggle('drag-over', !!under?.closest('#brew-stage'));
});
function finishIngredientDrag(event, cancelled) {
  if (!ingredientDrag || event.pointerId !== ingredientDrag.pointer) return;
  const drag = ingredientDrag; ingredientDrag = null;
  drag.ghost?.remove(); document.getElementById('brew-stage').classList.remove('drag-over');
  if (drag.moved) {
    suppressIngredientClick = true; setTimeout(() => { suppressIngredientClick = false; }, 0);
    const under = document.elementFromPoint(event.clientX, event.clientY);
    if (!cancelled && under?.closest('#brew-stage')) chooseIngredient(drag.id);
  }
}
document.addEventListener('pointerup', event => finishIngredientDrag(event, false));
document.addEventListener('pointercancel', event => finishIngredientDrag(event, true));
document.addEventListener('change', event => {
  if (event.target.id === 'craft-first' || event.target.id === 'craft-second') renderWorkbench();
});
document.addEventListener('click', event => {
  const ingredient = event.target.closest('[data-ingredient]');
  if (ingredient) { if (!suppressIngredientClick) chooseIngredient(ingredient.dataset.ingredient); return; }
  const slot = event.target.closest('[data-slot-index]');
  if (slot && mode === 'sandbox' && profile) {
    const key = slot.dataset.slotIndex === '0' ? 'craft-first' : 'craft-second';
    document.getElementById(key).value = ''; renderWorkbench(); return;
  }
  const area = event.target.closest('[data-area]');
  if (area) { showArea(area.dataset.area); return; }
  const actionElement = event.target.closest('[data-action]');
  const action = actionElement?.dataset.action;
  const viewAction = /^(craft|inventory|fighters|recipes)-(filter|prev|next)$/.exec(action || '');
  if (viewAction && profile) {
    const [, kind, operation] = viewAction, state = orbViews[kind];
    if (operation === 'filter') {
      const allowed = kind === 'fighters' ? ['all','available','expedition','other'] : ['all','elements','spirits','other'];
      if (!allowed.includes(actionElement.dataset.filter)) return;
      state.filter = actionElement.dataset.filter; state.page = 0;
    } else state.page += operation === 'next' ? 1 : -1;
    if (kind === 'craft') renderWorkbench();
    else if (kind === 'inventory') renderInventory();
    else if (kind === 'fighters') renderFighters();
    else renderRecipes();
    return;
  }
  if (action === 'inventory-item' && profile) {
    const item = profile.inventory[Number(actionElement.dataset.index)];
    if (item) document.getElementById('inventory-detail').textContent = item.name + ' · количество: ' + item.quantity;
    return;
  }
  if (action === 'fighter-item' && profile) {
    const fighter = profile.fighters[Number(actionElement.dataset.index)];
    if (fighter) document.getElementById('fighters-detail').textContent = fighter.name + ' · ур. ' + fighter.tier + ' · ' + (fighterStatuses[fighter.status] || 'Статус неизвестен') + ' · XP ' + fighter.xp + ' · выносливость ' + fighter.stamina;
    return;
  }
  if (action === 'enter') showPage('lab');
  else if (action === 'park') showPage('park');
  else if (action === 'about') showArea('about');
  else if (action === 'refresh') refreshProfile();
  else if (action === 'craft') craftExample();
  else if (action === 'open-recipes') showArea('recipes');
  else if (action === 'return-craft') showArea('laboratory');
  else if (action === 'production-start') productionAction('start');
  else if (action === 'production-advance') productionAction('advance');
  else if (action === 'production-remove') productionAction('remove', actionElement.dataset.slot);
  else if (action === 'building-focus' && profile) {
    const key = actionElement.dataset.building;
    const level = Number.isInteger(profile.buildings[key]) ? profile.buildings[key] : 0;
    document.getElementById('building-detail').textContent = buildingNames[key] + ' · ' + (level ? 'уровень ' + level : 'не построено') + '. Пока доступен только просмотр.';
  }
  else if (event.target.closest('.dialog-close')) dialog.close();
});
if (mode === 'sandbox' || mode === 'telegram') {
  document.getElementById('demo-banner').textContent = mode === 'sandbox'
    ? 'ТЕСТОВЫЙ СТЕНД · ВЫМЫШЛЕННЫЙ ПРОФИЛЬ' : 'ЗАКРЫТЫЙ TELEGRAM-СТЕНД · ТОЛЬКО ПРОСМОТР';
  if (mode === 'telegram') {
    document.getElementById('dialog-disclaimer').textContent = '✦ Тестовый стенд. Игровые действия и запись отключены.';
    window.Telegram?.WebApp?.ready?.();
  }
  document.getElementById('refresh-button').hidden = false;
  refreshProfile();
} else if (mode === 'unsupported') {
  profileState = 'error';
  document.getElementById('demo-banner').textContent = 'СТЕНД НЕ НАСТРОЕН';
  document.getElementById('profile-status').textContent = 'Режим приложения не настроен.';
}
