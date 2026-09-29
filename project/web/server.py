from pathlib import Path

from aiogram import Bot
from aiohttp import web
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..config import settings
from .api import api_middleware, routes

WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"

NO_CACHE = {"Cache-Control": "no-cache"}


async def index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(WEBAPP_DIR / "index.html", headers=NO_CACHE)


async def health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


@web.middleware
async def no_cache_static(request: web.Request, handler):
    resp = await handler(request)
    if request.path.startswith("/static/"):
        resp.headers.update(NO_CACHE)
    return resp


def build_app(bot: Bot, sessionmaker: async_sessionmaker) -> web.Application:
    app = web.Application(middlewares=[no_cache_static, api_middleware])
    app["bot"] = bot
    app["sessionmaker"] = sessionmaker
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.add_routes(routes)
    app.router.add_static("/static/", WEBAPP_DIR, show_index=False)
    return app


async def start_web(bot: Bot, sessionmaker: async_sessionmaker) -> web.AppRunner:
    runner = web.AppRunner(build_app(bot, sessionmaker))
    await runner.setup()
    await web.TCPSite(runner, settings.web_host, settings.web_port).start()
    return runner
