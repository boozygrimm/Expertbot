"""Experimental single-document CAS/idempotency for isolated Mongo test DBs only.

NOT wired to the bot or web API. This cannot coexist safely with legacy writers
that replace the same fields without bumping _alc_rev. All relevant bot and job
writers must adopt one protocol before this can ever touch real player data.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re
from uuid import UUID

from pymongo import ReadPreference
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from .crafting import CraftRejected, craft_transition

MAX_OPERATIONS = 256  # Fail closed instead of evicting keys and risking duplicate execution.
MAX_RETRIES = 12
ALLOWED_FIELDS = frozenset({'inventory', 'discovered', 'last_craft', 'production_slots'})


class StoreError(RuntimeError):
    pass


class InvalidStoreState(StoreError):
    pass


class OperationKeyConflict(StoreError):
    pass


class OperationLogFull(StoreError):
    pass


class ConcurrentUpdate(StoreError):
    pass


class ReadUnavailable(StoreError):
    pass


class OutcomeUnknown(StoreError):
    """The write may have happened; inspect/retry ONLY with the same operation ID."""


@dataclass(frozen=True)
class Change:
    fields: dict
    result: dict


def _validated_id(user_id, operation_id):
    if type(user_id) is not int or not 0 < user_id < 2**63:
        raise ValueError('invalid user')
    try:
        if not isinstance(operation_id, str) or str(UUID(operation_id)) != operation_id:
            raise ValueError('invalid operation ID')
    except (ValueError, TypeError, AttributeError):
        raise ValueError('invalid operation ID') from None


def _fingerprint(command):
    if not isinstance(command, dict):
        raise ValueError('invalid command')
    try:
        encoded = json.dumps(command, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
    except (ValueError, TypeError):
        raise ValueError('invalid command') from None
    if len(encoded) > 4096:
        raise ValueError('command too large')
    return hashlib.sha256(encoded.encode()).hexdigest()


def _state(document):
    if not isinstance(document, dict):
        raise InvalidStoreState('profile missing or invalid')
    revision = document.get('_alc_rev', 0)
    if type(revision) is not int or not 0 <= revision < 2**63 - 1:
        raise InvalidStoreState('invalid revision')
    operations = document.get('_alc_ops', [])
    if not isinstance(operations, list) or len(operations) > MAX_OPERATIONS:
        raise InvalidStoreState('invalid operation log')
    seen = set()
    for entry in operations:
        if (not isinstance(entry, dict) or not isinstance(entry.get('key'), str) or
                not isinstance(entry.get('fingerprint'), str) or not isinstance(entry.get('result'), dict) or
                entry['key'] in seen):
            raise InvalidStoreState('invalid operation log')
        seen.add(entry['key'])
    return revision, operations


class IsolatedProfileStore:
    def __init__(self, collection, *, isolated_test_only=False):
        # A naming check is a guardrail, NOT proof that a cluster is isolated.
        name = getattr(getattr(collection, 'database', None), 'name', '')
        if not isolated_test_only or not re.fullmatch(r'lunapark_test_[a-z0-9_]{1,32}', name or ''):
            raise ValueError('use only an explicitly isolated test database')
        self._players = collection.with_options(
            write_concern=WriteConcern(w='majority'), read_concern=ReadConcern('majority'),
            read_preference=ReadPreference.PRIMARY,
        )

    async def execute(self, user_id, operation_id, command, transition):
        """Run a trusted pure transition; only server code may supply transition.

        The log and state update are committed in one document update. The
        bounded log deliberately never evicts a key. An unknown write outcome
        is never retried internally; callers may retry with the SAME operation ID.
        """
        _validated_id(user_id, operation_id)
        fingerprint = _fingerprint(command)
        for _attempt in range(MAX_RETRIES):
            try:
                player = await self._players.find_one({'user_id': user_id})
            except Exception:
                raise ReadUnavailable('profile read unavailable') from None
            revision, operations = _state(player)
            previous = next((item for item in operations if item['key'] == operation_id), None)
            if previous:
                if previous['fingerprint'] != fingerprint:
                    raise OperationKeyConflict('operation ID already used for a different command')
                return deepcopy(previous['result'])
            if len(operations) >= MAX_OPERATIONS:
                raise OperationLogFull('operation log is full; no keys were evicted')
            change = transition(deepcopy(player))
            if (not isinstance(change, Change) or not isinstance(change.fields, dict) or
                    not set(change.fields).issubset(ALLOWED_FIELDS) or not isinstance(change.result, dict)):
                raise ValueError('invalid transition')
            try:
                result_size = len(json.dumps(change.result, allow_nan=False, ensure_ascii=True))
            except (ValueError, TypeError):
                raise ValueError('result must be JSON serializable') from None
            if result_size > 2048:
                raise ValueError('result too large')
            log_entry = {'key': operation_id, 'fingerprint': fingerprint, 'result': deepcopy(change.result)}
            condition = {
                'user_id': user_id,
                '_alc_rev': revision if '_alc_rev' in player else {'$exists': False},
                '_alc_ops.key': {'$ne': operation_id},
                f'_alc_ops.{MAX_OPERATIONS - 1}': {'$exists': False},
            }
            update = {'$inc': {'_alc_rev': 1}, '$push': {'_alc_ops': log_entry}}
            if change.fields:
                update['$set'] = deepcopy(change.fields)
            try:
                write = await self._players.update_one(condition, update)
            except Exception:
                raise OutcomeUnknown('write result unknown; retain the same operation ID') from None
            if write.matched_count == 1:
                return deepcopy(change.result)
        raise ConcurrentUpdate('version conflict; retry with the same operation ID')

    async def craft(self, user_id, operation_id, ingredients):
        # Validate before touching the database; game-level rejections are recorded
        # so a retry of the same request cannot later spend newly earned resources.
        if not isinstance(ingredients, list) or len(ingredients) != 2 or any(not isinstance(x, str) for x in ingredients):
            raise ValueError('invalid ingredients')
        ingredients = list(ingredients)
        command = {'kind': 'craft_single_v1', 'ingredients': ingredients}

        def transition(player):
            try:
                decision = craft_transition(player, ingredients)
            except CraftRejected as exc:
                if exc.code == 'invalid_request':
                    raise ValueError('invalid ingredients') from None
                if exc.code == 'invalid_profile':
                    raise InvalidStoreState('invalid player profile') from None
                return Change({}, {'outcome': 'rejected', 'reason': exc.code})
            if decision.outcome == 'no_recipe':
                return Change({'last_craft': decision.player['last_craft']}, {'outcome': 'no_recipe'})
            return Change({key: decision.player[key] for key in ('inventory', 'discovered', 'last_craft')},
                          {'outcome': 'crafted', 'result_id': decision.result_id})

        return await self.execute(user_id, operation_id, command, transition)
