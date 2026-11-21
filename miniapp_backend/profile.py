"""Read-only projection of the existing game schema; no migrations or writes."""
from handlers.alc_recipes import BATTLE_UNITS, ELEMENTS, RECIPES

HIDDEN_ITEMS = frozenset({'base_crystal', 'active_base'})
BUILDINGS = ('storage', 'lab', 'altar', 'guild', 'barracks', 'fort', 'heritage_center')
STATION_NAMES = {'mine': '⛏ Шахта', 'farm': '🚜 Ферма', 'factory': '🏭 Завод', 'aero': '☁️ Аэроцех'}
PROJECTION = {
    '_id': 0, 'inventory': 1, 'buildings': 1, 'discovered': 1,
    'active_expeditions.status': 1,
    'production_slots.type': 1, 'production_slots.worker': 1, 'production_slots.stopped': 1,
    'production_slots.total_mined': 1,
    'fighters.unit_id': 1, 'fighters.custom_name': 1, 'fighters.status': 1,
    'fighters.tier': 1, 'fighters.xp': 1, 'fighters.stamina': 1,
}


def _dict(value):
    return value if isinstance(value, dict) else {}


def _count(value):
    return value if type(value) is int and value >= 0 else 0


def _list(value):
    return value if isinstance(value, list) else []


def _name(item):
    return ELEMENTS.get(item, item) if isinstance(item, str) else 'Неизвестно'


def build_profile(player: dict) -> dict:
    inventory = _dict(player.get('inventory'))
    items = []
    for item_id, value in inventory.items():
        if not isinstance(item_id, str) or item_id in HIDDEN_ITEMS or _count(value) == 0:
            continue
        items.append({'id': item_id, 'name': _name(item_id), 'quantity': value})
    items.sort(key=lambda item: (item['name'].casefold(), item['id']))
    buildings = _dict(player.get('buildings'))
    discovered = _list(player.get('discovered'))

    # Match the bot's recipe-book rule: a result is known if it is in discovered
    # OR its key exists in inventory, even when the stored quantity is zero.
    known = {x for x in discovered if isinstance(x, str)} | {k for k in inventory if isinstance(k, str)}
    recipes = [{'ingredients': [_name(a), _name(b)], 'result': _name(result)}
               for (a, b), result in RECIPES.items() if result in known]
    recipes.sort(key=lambda recipe: (recipe['result'].casefold(), recipe['ingredients']))

    status_counts = {}
    allowed_statuses = {'searching', 'awaiting_choice', 'returning', 'arrived'}
    for expedition in _list(player.get('active_expeditions')):
        if not isinstance(expedition, dict):
            continue
        status = expedition.get('status')
        status = status if isinstance(status, str) and status in allowed_statuses else 'unknown'
        status_counts[status] = status_counts.get(status, 0) + 1

    stations = []
    for slot in _list(player.get('production_slots')):
        if not isinstance(slot, dict):
            continue
        station_type = slot.get('type')
        worker = slot.get('worker')
        stations.append({
            'name': STATION_NAMES.get(station_type, 'Неизвестная станция') if isinstance(station_type, str) else 'Неизвестная станция',
            'worker': _name(worker) if isinstance(worker, str) and worker not in ('none', '') else 'Ручной труд',
            'stopped': slot.get('stopped') is True,
            'total_mined': _count(slot.get('total_mined')),
        })

    fighters = []
    allowed_fighter_statuses = {'available', 'expedition', 'raid', 'garrison', 'dead', 'captured'}
    for fighter in _list(player.get('fighters')):
        if not isinstance(fighter, dict):
            continue
        name = fighter.get('custom_name')
        status = fighter.get('status')
        unit_id = fighter.get('unit_id')
        fighters.append({
            # Known type only; never expose arbitrary stored values or private data.
            'unit_id': unit_id if isinstance(unit_id, str) and unit_id in BATTLE_UNITS else None,
            'name': name.strip()[:100] if isinstance(name, str) and name.strip() else _name(unit_id),
            'tier': _count(fighter.get('tier')), 'xp': _count(fighter.get('xp')),
            'stamina': _count(fighter.get('stamina')),
            'status': status if isinstance(status, str) and status in allowed_fighter_statuses else 'unknown',
        })
    return {
        'inventory': items,
        'buildings': {key: _count(buildings.get(key)) for key in BUILDINGS},
        'discovered_count': len(discovered), 'recipes': recipes,
        'expeditions': status_counts, 'production': stations, 'fighters': fighters,
    }
