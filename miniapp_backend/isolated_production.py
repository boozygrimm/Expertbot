"""One replayable production tick for the isolated CAS experiment only.

This is NOT connected to the scheduler, Telegram, HTTP, or production data.
The caller must persist/reuse both run_id and tick_time for the entire run,
including after restart and unknown outcomes. The isolated store's DB guard
is required but does not itself prove network or data isolation.
"""
import hashlib
import random
from uuid import UUID, uuid5

from .isolated_store import Change
from .production import calculate_production_loot

# Dedicated namespace, fixed for this experimental protocol.
PRODUCTION_NAMESPACE = UUID('843949f6-0e46-4ac9-b2ef-28a947ad7f7e')


def production_operation_id(run_id, user_id):
    if not isinstance(run_id, str):
        raise ValueError('invalid run ID')
    try:
        if str(UUID(run_id)) != run_id:
            raise ValueError('invalid run ID')
    except (ValueError, TypeError, AttributeError):
        raise ValueError('invalid run ID') from None
    if type(user_id) is not int or not 0 < user_id < 2**63:
        raise ValueError('invalid user')
    return str(uuid5(PRODUCTION_NAMESPACE, f'{run_id}:{user_id}'))


async def run_isolated_production(store, *, user_id, run_id, tick_time):
    if type(tick_time) is not int or not 0 <= tick_time < 2**63:
        raise ValueError('invalid tick time')
    operation_id = production_operation_id(run_id, user_id)
    command = {'kind': 'production_tick_v1', 'run_id': run_id, 'tick_time': tick_time}
    # Every CAS re-evaluation starts from the same random stream. State-dependent
    # branches can consume a different part of the stream after another writer;
    # the resulting decision corresponds to the freshly read profile.
    seed = int.from_bytes(hashlib.sha256(operation_id.encode('ascii')).digest(), 'big')

    def transition(player):
        fields = calculate_production_loot(player, tick_time, rng=random.Random(seed))
        return Change(fields or {}, {'outcome': 'produced' if fields else 'unchanged'})

    return await store.execute(user_id, operation_id, command, transition)
