"""Production controls for the in-memory demo only; never a production writer.

Tables are deliberately copied to avoid importing legacy modules that initialize
Mongo and read credentials. The offline parity fixture checks them against the
existing Python bot. This demo has no worlds, so world effects are always empty.
"""
from copy import deepcopy
import hashlib
import random
from uuid import uuid4

from .production_engine import calculate_production_loot
from .profile import HIDDEN_ITEMS

STATIONS = {
    'mine': {'items': ['e', 'stone', 'sand', 'metal']},
    'farm': {'items': ['w', 'cane', 'wood', 'wheat']},
    'factory': {'items': ['f', 'energy', 'brick', 'glass']},
    'aero': {'items': ['a', 'bacteria', 'life', 'bird']},
}
STORAGE_CAPACITY = {0: 20, 1: 50, 2: 150, 3: 350}
ALTAR_SLOTS = {0: 0, 1: 1, 2: 2, 3: 3}
WORKERS = ('spirit_e', 'spirit_w', 'spirit_f', 'spirit_a')


def capacity(player):
    return ALTAR_SLOTS.get(player.get('buildings', {}).get('altar', 0), 0)


def used_slots(inventory):
    return sum(1 for key, value in inventory.items() if value > 0 and key not in HIDDEN_ITEMS)


def apply_production(player, now, command, request_id):
    """Return a candidate copy, virtual time and result or reject unchanged."""
    player = deepcopy(player)
    inventory, slots = player['inventory'], player['production_slots']
    action = command['action']
    if action == 'start':
        if len(slots) >= capacity(player):
            return None, now, 'slots_full'
        worker = command['worker']
        if worker != 'none':
            if inventory.get(worker, 0) <= 0:
                return None, now, 'missing_worker'
            if inventory.get('energy', 0) <= 0:
                return None, now, 'missing_energy'
            for key in (worker, 'energy'):
                inventory[key] -= 1
                if inventory[key] <= 0:
                    del inventory[key]
        slots.append({'type': command['station'], 'worker': None if worker == 'none' else worker,
                      'assigned_at': now, 'total_mined': 0, 'mined_items': {}, 'stopped': False,
                      '_sandbox_id': str(uuid4())})
        return player, now, 'started'
    if action == 'remove':
        index = next((i for i, slot in enumerate(slots) if slot['_sandbox_id'] == command['slot_id']), None)
        if index is None:
            return None, now, 'slot_missing'
        slot = slots.pop(index)
        if slot.get('worker'):
            worker = slot['worker']
            inventory[worker] = inventory.get(worker, 0) + 1
        return player, now, 'removed'
    if action == 'advance':
        # An explicit user-triggered simulation, not a scheduler and not real time.
        # Randomness is tied to this click, so retries are reproducible.
        new_now = now + 3600
        seed = int.from_bytes(hashlib.sha256(request_id.encode('ascii')).digest(), 'big')
        updates = calculate_production_loot(player, new_now, rng=random.Random(seed), stations=STATIONS,
                                            storage_capacity=STORAGE_CAPACITY, get_used_slots=used_slots,
                                            get_world_effects=lambda _: {})
        if updates is not None:
            player.update(updates)
        return player, new_now, 'advanced'
    raise ValueError('invalid action')
