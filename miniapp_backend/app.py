"""Experimental API factories. Deliberately not registered in the production bot."""
import logging
from aiohttp import web
from .auth import InvalidInitData, verify_init_data
from .profile import PROJECTION, build_profile
from .sessions import CapacityError, ReplayError, SessionStore

log = logging.getLogger(__name__)


def set_security_headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'


@web.middleware
async def security_headers(request, handler):
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        set_security_headers(exc)
        raise
    set_security_headers(response)
    return response


async def _profile_response(players, user_id, profile_builder=build_profile):
    try:
        player = await players.find_one({'user_id': user_id}, PROJECTION)
    except Exception:
        # Never log exception details: connection errors can include credentials.
        log.warning('Mini App profile read failed')
        return web.json_response({'error': 'temporarily_unavailable'}, status=503)
    if player is None:
        return web.json_response({'error': 'profile_not_found'}, status=404)
    return web.json_response({'profile': profile_builder(player)})


def create_app(*, token: str, allowed_users: set[int], players, profile_builder=build_profile) -> web.Application:
    """Original direct-initData API used by the synthetic sandbox only."""
    if not token or not allowed_users or any(type(uid) is not int or uid <= 0 for uid in allowed_users):
        raise ValueError('A token and an explicit non-empty allowlist are required')
    allowed_users = frozenset(allowed_users)

    async def get_profile(request):
        authorization = request.headers.get('Authorization', '')
        if not authorization.startswith('tma '):
            return web.json_response({'error': 'unauthorized'}, status=401)
        try:
            user_id = verify_init_data(authorization[4:], token)
        except InvalidInitData:
            return web.json_response({'error': 'unauthorized'}, status=401)
        if user_id not in allowed_users:
            return web.json_response({'error': 'forbidden'}, status=403)
        return await _profile_response(players, user_id, profile_builder)

    app = web.Application(middlewares=[security_headers], client_max_size=8192)
    app.router.add_get('/api/alchemy/me', get_profile, allow_head=False)
    return app


def create_session_app(*, token: str, allowed_users: set[int], players, store=None) -> web.Application:
    """Experimental Telegram test stand: one process only, never a production service."""
    if not token or not allowed_users or any(type(uid) is not int or uid <= 0 for uid in allowed_users):
        raise ValueError('A token and an explicit non-empty allowlist are required')
    sessions = store if store is not None else SessionStore(token, allowed_users)

    async def exchange(request):
        if request.content_type != 'application/json':
            return web.json_response({'error': 'invalid_request'}, status=400)
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {'initData'} or not isinstance(body['initData'], str):
                raise ValueError('invalid body')
        except (ValueError, TypeError):
            return web.json_response({'error': 'invalid_request'}, status=400)
        try:
            access_token, ttl = await sessions.exchange(body['initData'])
        except InvalidInitData:
            return web.json_response({'error': 'unauthorized'}, status=401)
        except PermissionError:
            return web.json_response({'error': 'forbidden'}, status=403)
        except ReplayError:
            return web.json_response({'error': 'replayed'}, status=409)
        except CapacityError:
            return web.json_response({'error': 'capacity'}, status=503)
        return web.json_response({'access_token': access_token, 'expires_in': ttl})

    async def identify(request):
        header = request.headers.get('Authorization', '')
        if not header.startswith('Bearer '):
            return None
        return await sessions.resolve(header[7:])

    async def get_profile(request):
        user_id = await identify(request)
        if user_id is None:
            return web.json_response({'error': 'unauthorized'}, status=401)
        return await _profile_response(players, user_id)

    async def logout(request):
        header = request.headers.get('Authorization', '')
        if not header.startswith('Bearer ') or await sessions.resolve(header[7:]) is None:
            return web.json_response({'error': 'unauthorized'}, status=401)
        await sessions.revoke(header[7:])
        return web.Response(status=204)

    app = web.Application(middlewares=[security_headers], client_max_size=8192)
    app.router.add_post('/api/session', exchange)
    app.router.add_delete('/api/session', logout)
    app.router.add_get('/api/alchemy/me', get_profile, allow_head=False)
    return app
