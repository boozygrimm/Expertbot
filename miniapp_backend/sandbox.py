"""Public-data-only sandbox. NEVER wire its fake routes into a real service."""
import argparse
import asyncio
from copy import deepcopy
import hashlib
import hmac
import json
from pathlib import Path
import time
from urllib.parse import urlencode
from uuid import UUID, uuid4

from aiohttp import web
from handlers.alc_recipes import ELEMENTS
from .app import create_app as create_api
from .auth import InvalidInitData, verify_init_data
from .crafting import CraftRejected, craft_transition
from .profile import HIDDEN_ITEMS, build_profile
from .sandbox_production import STATIONS, WORKERS, apply_production, capacity

# These are public, fake values. The sandbox never reads environment credentials.
SANDBOX_TOKEN = '000000:PUBLIC-SANDBOX-NOT-A-TELEGRAM-TOKEN'
SANDBOX_USER = 42
ROOT = Path(__file__).resolve().parent.parent / 'miniapp_preview'
EXAMPLE_PLAYER = {
    'user_id': SANDBOX_USER,
    'inventory': {'w': 12, 'f': 8, 'e': 6, 'a': 5, 'stone': 6, 'steam': 3, 'moss': 3,
                  'energy': 4, 'spirit_w': 2, 'spirit_f': 1, 'spirit_a': 1},
    'buildings': {'storage': 1, 'lab': 2, 'altar': 2, 'guild': 1, 'barracks': 1, 'fort': 0, 'heritage_center': 1},
    'discovered': ['steam', 'stone', 'lava'],
    'active_expeditions': [{'status': 'searching'}, {'status': 'returning'}],
    'production_slots': [{'type': 'mine', 'worker': 'spirit_e', 'assigned_at': 1000000, 'stopped': False, 'total_mined': 12},
                         {'type': 'farm', 'worker': None, 'assigned_at': 1000000, 'stopped': True, 'total_mined': 5}],
    'fighters': [{'unit_id': 'fire_swordsman', 'custom_name': 'Искра', 'tier': 1, 'xp': 3, 'stamina': 2, 'status': 'available'},
                 {'unit_id': 'water_guard', 'tier': 1, 'xp': 1, 'stamina': 1, 'status': 'expedition'}],
}


def sandbox_profile(player):
    """Project extra controls only for this synthetic sandbox."""
    profile = build_profile(player)
    for projected, slot in zip(profile['production'], player['production_slots']):
        projected['sandbox_id'] = slot['_sandbox_id']
    profile['sandbox'] = {'production_capacity': capacity(player), 'elapsed_hours': player['_sandbox_hours']}
    return profile


class SandboxPlayers:
    """One shared fake profile, in process memory only. Not a Mongo adapter."""
    def __init__(self):
        self.player = deepcopy(EXAMPLE_PLAYER)
        self.clock = 1000000
        for slot in self.player['production_slots']:
            slot['_sandbox_id'] = str(uuid4())
        self.lock = asyncio.Lock()
        self.completed = {}

    def snapshot(self):
        player = deepcopy(self.player)
        player['_sandbox_hours'] = (self.clock - 1000000) // 3600
        return player

    async def find_one(self, query, projection):
        if query != {'user_id': SANDBOX_USER}:
            return None
        async with self.lock:
            return self.snapshot()

    async def craft(self, ingredients, request_id):
        # This lock and bounded operation log protect only THIS single sandbox
        # process. They do not make production Mongo writes safe.
        async with self.lock:
            identity = ('craft', *ingredients)
            if request_id in self.completed:
                previous_identity, payload, status = self.completed[request_id]
                if identity != previous_identity:
                    return {'error': 'request_id_conflict'}, 409
                return deepcopy(payload), status
            if len(self.completed) >= 1024:
                return {'error': 'sandbox_full'}, 503
            try:
                decision = craft_transition(self.player, ingredients)
            except CraftRejected as exc:
                payload, status = {'error': exc.code}, exc.status
            else:
                self.player = decision.player
                payload = {'outcome': decision.outcome, 'profile': sandbox_profile(self.snapshot())}
                if decision.result_id is not None:
                    payload['result'] = ELEMENTS.get(decision.result_id, decision.result_id)
                status = 200
            self.completed[request_id] = (identity, deepcopy(payload), status)
            return payload, status

    async def production(self, command, request_id):
        async with self.lock:
            identity = ('production', json.dumps(command, sort_keys=True))
            if request_id in self.completed:
                previous_identity, payload, status = self.completed[request_id]
                if identity != previous_identity:
                    return {'error': 'request_id_conflict'}, 409
                return deepcopy(payload), status
            if len(self.completed) >= 1024:
                return {'error': 'sandbox_full'}, 503
            player, now, outcome = apply_production(self.player, self.clock, command, request_id)
            if player is None:
                payload, status = {'error': outcome}, 409
            else:
                self.player, self.clock = player, now
                payload, status = {'outcome': outcome, 'profile': sandbox_profile(self.snapshot())}, 200
            self.completed[request_id] = (identity, deepcopy(payload), status)
            return payload, status


def fake_init_data():
    fields = {'auth_date': str(int(time.time())),
              'user': json.dumps({'id': SANDBOX_USER, 'first_name': 'Тестовый алхимик'}, ensure_ascii=False, separators=(',', ':'))}
    check = '\n'.join(f'{k}={v}' for k, v in sorted(fields.items()))
    secret = hmac.new(b'WebAppData', SANDBOX_TOKEN.encode(), hashlib.sha256).digest()
    fields['hash'] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def create_sandbox() -> web.Application:
    players = SandboxPlayers()
    app = create_api(token=SANDBOX_TOKEN, allowed_users={SANDBOX_USER}, players=players, profile_builder=sandbox_profile)

    async def index(request):
        return web.FileResponse(ROOT / 'index.html')

    async def config(request):
        return web.Response(text="window.LUNAPARK_MODE = 'sandbox';", content_type='application/javascript')

    async def session(request):
        return web.json_response({'initData': fake_init_data()})

    async def craft(request):
        authorization = request.headers.get('Authorization', '')
        try:
            if not authorization.startswith('tma ') or verify_init_data(authorization[4:], SANDBOX_TOKEN) != SANDBOX_USER:
                raise InvalidInitData('invalid session')
        except InvalidInitData:
            return web.json_response({'error': 'unauthorized'}, status=401)
        try:
            if request.content_type != 'application/json':
                raise ValueError('wrong content type')
            payload = await request.json()
            if not isinstance(payload, dict) or set(payload) != {'ingredients', 'request_id'}:
                raise ValueError('bad payload')
            ingredients, request_id = payload['ingredients'], payload['request_id']
            if (not isinstance(ingredients, list) or len(ingredients) != 2 or
                    any(not isinstance(item, str) or item not in ELEMENTS or item in HIDDEN_ITEMS or item in {'recipe_scroll', 'pack_xl'} for item in ingredients)):
                raise ValueError('bad ingredients')
            if not isinstance(request_id, str) or str(UUID(request_id)) != request_id:
                raise ValueError('bad id')
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return web.json_response({'error': 'invalid_request'}, status=400)
        result, status = await players.craft(ingredients, request_id)
        return web.json_response(result, status=status)

    async def production(request):
        authorization = request.headers.get('Authorization', '')
        try:
            if not authorization.startswith('tma ') or verify_init_data(authorization[4:], SANDBOX_TOKEN) != SANDBOX_USER:
                raise InvalidInitData('invalid session')
        except InvalidInitData:
            return web.json_response({'error': 'unauthorized'}, status=401)
        try:
            if request.content_type != 'application/json':
                raise ValueError('wrong content type')
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError('bad payload')
            action, request_id = payload.get('action'), payload.get('request_id')
            if not isinstance(request_id, str) or str(UUID(request_id)) != request_id:
                raise ValueError('bad id')
            if action == 'start' and set(payload) == {'request_id', 'action', 'station', 'worker'}:
                if payload['station'] not in STATIONS or payload['worker'] not in (*WORKERS, 'none'):
                    raise ValueError('bad station or worker')
                command = {'action': action, 'station': payload['station'], 'worker': payload['worker']}
            elif action == 'remove' and set(payload) == {'request_id', 'action', 'slot_id'}:
                slot_id = payload['slot_id']
                if not isinstance(slot_id, str) or str(UUID(slot_id)) != slot_id:
                    raise ValueError('bad slot id')
                command = {'action': action, 'slot_id': slot_id}
            elif action == 'advance' and set(payload) == {'request_id', 'action'}:
                command = {'action': action}
            else:
                raise ValueError('bad action')
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return web.json_response({'error': 'invalid_request'}, status=400)
        result, status = await players.production(command, request_id)
        return web.json_response(result, status=status)

    async def script(request):
        return web.FileResponse(ROOT / request.match_info['file'])

    async def asset(request):
        return web.FileResponse(ROOT / 'assets' / request.match_info['file'])

    app.router.add_get('/', index)
    app.router.add_get('/config.js', config)
    app.router.add_get('/api/dev/session', session)
    app.router.add_post('/api/dev/craft', craft)
    app.router.add_post('/api/dev/production', production)
    app.router.add_get(r'/{file:app\.js|styles\.css}', script)
    app.router.add_get(r'/assets/{file:park\.jpg|lab\.jpg|base_scene\.jpg|expedition_scene\.jpg|fighter_fire\.jpg|fighter_water\.jpg}', asset)
    return app


def main():
    parser = argparse.ArgumentParser(description='Sandbox only: fake token, fake player, no database')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8001)
    args = parser.parse_args()
    web.run_app(create_sandbox(), host=args.host, port=args.port, access_log=None)


if __name__ == '__main__':
    main()
