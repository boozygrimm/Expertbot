"""Opt-in Telegram read-only test stand, separate from the running bot.

Never point it at a production bot or production database; not deployed automatically.
"""
import argparse
import os
from pathlib import Path
import re

from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient
from .app import create_session_app

ROOT = Path(__file__).resolve().parent.parent / 'miniapp_preview'
CSP = ("default-src 'none'; script-src 'self' https://telegram.org; style-src 'self'; "
       "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors https://web.telegram.org https://*.telegram.org")


def create_telegram_stand(*, token, allowed_users, players):
    app = create_session_app(token=token, allowed_users=allowed_users, players=players)

    @web.middleware
    async def csp_headers(request, handler):
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            exc.headers['Content-Security-Policy'] = CSP
            raise
        response.headers['Content-Security-Policy'] = CSP
        return response

    app.middlewares.append(csp_headers)

    async def index(request):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        html = html.replace('<script src="config.js" defer></script>',
                            '<script src="https://telegram.org/js/telegram-web-app.js" defer></script>\n  <script src="config.js" defer></script>')
        return web.Response(text=html, content_type='text/html')

    async def config(request):
        return web.Response(text="window.LUNAPARK_MODE = 'telegram';", content_type='application/javascript')

    async def static(request):
        return web.FileResponse(ROOT / request.match_info['file'])

    async def asset(request):
        return web.FileResponse(ROOT / 'assets' / request.match_info['file'])

    app.router.add_get('/', index)
    app.router.add_get('/config.js', config)
    app.router.add_get(r'/{file:app\.js|styles\.css}', static)
    app.router.add_get(r'/assets/{file:park\.jpg|lab\.jpg|base_scene\.jpg|expedition_scene\.jpg|fighter_fire\.jpg|fighter_water\.jpg}', asset)
    return app


def configuration_from_env():
    if os.environ.get('LUNAPARK_STAND_CONFIRM') != 'isolated_test_only':
        raise ValueError('Set LUNAPARK_STAND_CONFIRM=isolated_test_only for a separate test bot and test database')
    token = os.environ.get('LUNAPARK_TEST_BOT_TOKEN', '').strip()
    uri = os.environ.get('LUNAPARK_TEST_MONGO_URI', '').strip()
    db_name = os.environ.get('LUNAPARK_TEST_DB', '').strip()
    if not token or not uri or not re.fullmatch(r'lunapark_test_[a-z0-9_]{1,32}', db_name):
        raise ValueError('Test-only bot token, Mongo URI and lunapark_test_* database name are required')
    users_raw = os.environ.get('LUNAPARK_TEST_USERS', '')
    try:
        if not re.fullmatch(r'\s*\d+(\s*,\s*\d+)*\s*', users_raw):
            raise ValueError('invalid allowlist')
        users = {int(item.strip()) for item in users_raw.split(',')}
        if any(uid <= 0 or uid > 2**63 - 1 for uid in users):
            raise ValueError('invalid allowlist')
    except ValueError:
        raise ValueError('An explicit list of positive test user IDs is required') from None
    return token, uri, db_name, users


def main():
    parser = argparse.ArgumentParser(description='Isolated Telegram test stand; never use production credentials')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8002)
    args = parser.parse_args()
    try:
        token, uri, db_name, users = configuration_from_env()
    except ValueError as exc:
        parser.error(str(exc))
    try:
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    except Exception:
        # Some driver errors include the URI; do not print credentials.
        parser.error('The test MongoDB URI is invalid')
    app = create_telegram_stand(token=token, allowed_users=users, players=client[db_name].alchemy_players)

    async def close_mongo(_app):
        client.close()

    app.on_cleanup.append(close_mongo)
    web.run_app(app, host=args.host, port=args.port, access_log=None)


if __name__ == '__main__':
    main()
