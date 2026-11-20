/* Local, synthetic demonstration only. No backend, Telegram, MongoDB or network.
   The Python sandbox remains the authoritative experiment; this is a separate
   browser-only approximation to make the interface portable. */
(() => {
  'use strict';
  const source = window.OFFLINE_DATA;
  const player = JSON.parse(JSON.stringify(source.player));
  let clock = 1000000;
  const completed = new Map();
  const hidden = new Set(['base_crystal', 'active_base']);
  const pairs = new Map(source.recipes.map(r => [JSON.stringify(r.ingredients), r]));
  const stations = source.stations;
  const names = source.elements;
  const stationNames = source.stationNames;
  const name = key => names[key] || key;
  const itemCount = key => player.inventory[key] || 0;
  const copy = x => JSON.parse(JSON.stringify(x));
  const reply = (body, status = 200) => ({ok:status >= 200 && status < 300, status, json:async () => copy(body)});
  const uid = () => crypto.randomUUID();
  player.production_slots.forEach(slot => { slot._sandbox_id = uid(); });

  function projection() {
    const inv = Object.entries(player.inventory).filter(([key, count]) => !hidden.has(key) && count > 0)
      .map(([id, quantity]) => ({id, name:name(id), quantity})).sort((a,b) => a.name.localeCompare(b.name, 'ru'));
    const known = new Set([...Object.keys(player.inventory), ...player.discovered]);
    const recipes = source.recipes.filter(r => known.has(r.result)).map(r => ({ingredients:r.ingredients.map(name), result:name(r.result)}))
      .sort((a,b) => a.result.localeCompare(b.result, 'ru'));
    const production = player.production_slots.map(slot => ({name:stationNames[slot.type],
      worker:slot.worker ? name(slot.worker) : 'Ручной труд', stopped:slot.stopped === true,
      total_mined:slot.total_mined || 0, sandbox_id:slot._sandbox_id}));
    return {inventory:inv, buildings:copy(source.profile.buildings), discovered_count:player.discovered.length,
      recipes, expeditions:copy(source.profile.expeditions), production, fighters:copy(source.profile.fighters),
      sandbox:{production_capacity:source.altarSlots[player.buildings.altar] || 0, elapsed_hours:(clock-1000000)/3600}};
  }
  function craft(body) {
    const ingredients = body.ingredients;
    if (!Array.isArray(ingredients) || ingredients.length !== 2 || ingredients.some(x => !names[x] || hidden.has(x) || ['recipe_scroll','pack_xl'].includes(x))) return [{error:'invalid_request'},400];
    if (ingredients.some(x => itemCount(x) < ingredients.filter(y => y === x).length)) return [{error:'insufficient_resources'},409];
    const recipe = pairs.get(JSON.stringify([...ingredients].sort()));
    if (recipe?.fighter) return [{error:'fighter_recipe'},409];
    player.last_craft = [...ingredients];
    if (!recipe) return [{outcome:'no_recipe', profile:projection()},200];
    for (const item of ingredients) player.inventory[item]--;
    player.inventory[recipe.result] = itemCount(recipe.result) + 1;
    if (!player.discovered.includes(recipe.result)) player.discovered.push(recipe.result);
    return [{outcome:'crafted', result:name(recipe.result), profile:projection()},200];
  }
  function production(body) {
    const slots = player.production_slots;
    if (body.action === 'remove') {
      const i = slots.findIndex(slot => slot._sandbox_id === body.slot_id);
      if (i < 0) return [{error:'slot_missing'},409];
      const [slot] = slots.splice(i, 1);
      if (slot.worker) player.inventory[slot.worker] = itemCount(slot.worker) + 1;
      return [{outcome:'removed', profile:projection()},200];
    }
    if (body.action === 'start') {
      if (!stations[body.station] || !['none','spirit_e','spirit_w','spirit_f','spirit_a'].includes(body.worker)) return [{error:'invalid_request'},400];
      if (slots.length >= (source.altarSlots[player.buildings.altar] || 0)) return [{error:'slots_full'},409];
      if (body.worker !== 'none') {
        if (itemCount(body.worker) <= 0) return [{error:'missing_worker'},409];
        if (itemCount('energy') <= 0) return [{error:'missing_energy'},409];
        for (const key of [body.worker, 'energy']) if (--player.inventory[key] === 0) delete player.inventory[key];
      }
      slots.push({type:body.station, worker:body.worker === 'none' ? null : body.worker, assigned_at:clock,
        total_mined:0, mined_items:{}, stopped:false, _sandbox_id:uid()});
      return [{outcome:'started', profile:projection()},200];
    }
    if (body.action !== 'advance') return [{error:'invalid_request'},400];
    clock += 3600;
    const usedSlots = () => Object.entries(player.inventory).filter(([k,v]) => v > 0 && !hidden.has(k)).length;
    const limit = source.storageCapacity[player.buildings.storage] || 20;
    const choice = values => values[Math.floor(Math.random() * values.length)];
    const suited = {mine:'spirit_e', farm:'spirit_w', factory:'spirit_f', aero:'spirit_a'};
    for (const slot of slots) {
      if (slot.assigned_at > 0 && clock - slot.assigned_at >= 86400) {
        if (!slot.stopped) { slot.worker = null; slot.stopped = true; }
        continue;
      }
      if (slot.stopped) continue;
      slot.mined_items ||= {};
      const count = slot.worker ? (slot.worker === suited[slot.type] ? 3 : 2) : 1;
      for (let i=0; i<count; i++) {
        const item = choice(stations[slot.type].items);
        if (!(item in player.inventory) && usedSlots() >= limit) break;
        player.inventory[item] = itemCount(item) + 1;
        slot.mined_items[item] = (slot.mined_items[item] || 0) + 1;
        slot.total_mined = (slot.total_mined || 0) + 1;
        if (Math.random() < 0.02) {
          const bonus = choice(['recipe_scroll', 'pack_s']);
          if (bonus in player.inventory || usedSlots() < limit) player.inventory[bonus] = itemCount(bonus) + 1;
        }
      }
    }
    return [{outcome:'advanced', profile:projection()},200];
  }
  window.LUNAPARK_MODE = 'sandbox';
  window.fetch = async (path, options = {}) => {
    if (path === '/api/dev/session') return reply({initData:'offline-demo'});
    if (path === '/api/alchemy/me') return reply({profile:projection()});
    if (path !== '/api/dev/craft' && path !== '/api/dev/production') return reply({error:'not_found'},404);
    let body;
    try { body = JSON.parse(options.body); } catch (_) { return reply({error:'invalid_request'},400); }
    const signature = JSON.stringify([path, body]);
    if (completed.has(body.request_id)) {
      const previous = completed.get(body.request_id);
      return previous.signature === signature ? reply(previous.body, previous.status) : reply({error:'request_id_conflict'},409);
    }
    if (completed.size >= 1024) return reply({error:'sandbox_full'},503);
    const [result, status] = path === '/api/dev/craft' ? craft(body) : production(body);
    completed.set(body.request_id, {signature, body:copy(result), status});
    return reply(result, status);
  };
})();
