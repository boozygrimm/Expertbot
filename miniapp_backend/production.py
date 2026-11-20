"""Adapter for isolated production tests, not connected to the real scheduler.

These imports initialize the legacy database client from environment. Use this
module only in a scrubbed test environment; the demo sandbox uses an injected
engine and never imports these handlers or credentials.
"""
from handlers.alc_core import STORAGE_CAPACITY, get_used_slots, get_world_effects
from handlers.alc_production import STATIONS
from .production_engine import calculate_production_loot as _calculate


def calculate_production_loot(player, now, *, rng=None):
    if rng is None:
        raise ValueError('isolated tick requires an explicit random generator')
    return _calculate(player, now, rng=rng, stations=STATIONS,
                      storage_capacity=STORAGE_CAPACITY, get_used_slots=get_used_slots,
                      get_world_effects=get_world_effects)
