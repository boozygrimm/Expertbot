"""Local test fixture: synthetic Telegram SDK data and in-memory test player only.

Not a deployment entrypoint. Never use real tokens or real database connections here.
"""
import argparse
from aiohttp import web
from miniapp_backend.sandbox import SANDBOX_TOKEN, SANDBOX_USER, SandboxPlayers, fake_init_data
from miniapp_backend.telegram_stand import create_telegram_stand


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    app = create_telegram_stand(token=SANDBOX_TOKEN, allowed_users={SANDBOX_USER}, players=SandboxPlayers())

    async def fixture(_request):
        return web.json_response({'initData': fake_init_data()})

    app.router.add_get('/test-only/init-data', fixture)
    web.run_app(app, host='127.0.0.1', port=args.port, access_log=None)


if __name__ == '__main__':
    main()
