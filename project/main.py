import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram.types import (
    BotCommand, BotCommandScopeChat, BotCommandScopeDefault,
    MenuButtonWebApp, WebAppInfo,
)


from .config import settings
from .db import init_models, sessionmaker
from .handlers import admin, client
from .middlewares.db import DbSessionMiddleware
from .middlewares.users import UserMiddleware
from .services.orders import expire_stale, mark_pinged, stuck_orders
from .services.workflow import open_kb
from .views import card, kb_admin
from .web.server import start_web


async def setup_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="app", description="Открыть приложение"),
            BotCommand(command="new", description="Новая заявка"),
            BotCommand(command="my", description="Мои заявки"),
            BotCommand(command="rules", description="Правила"),
            BotCommand(command="calc", description="Посчитать стоимость"),
            BotCommand(command="cancel", description="Отменить заявку"),
        ],
        scope=BotCommandScopeDefault(),
    )
    await bot.set_my_commands(
        [
            BotCommand(command="app", description="Открыть приложение"),
            BotCommand(command="admin", description="Админка"),
            BotCommand(command="rate", description="Курс доллара"),
            BotCommand(command="cross", description="Кросс-курс"),
            BotCommand(command="order", description="Заявка по номеру"),
            BotCommand(command="svc", description="Сервисы"),
            BotCommand(command="stats", description="Статистика"),
        ],
        scope=BotCommandScopeChat(chat_id=settings.admin_id),
    )

async def setup_menu_button(bot: Bot) -> None:
    """Кнопка слева от поля ввода открывает Mini App."""
    if not settings.webapp_url:
        return
    await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(
        text="Открыть", web_app=WebAppInfo(url=settings.webapp_url)))


async def expire_job(bot: Bot) -> None:
    async with sessionmaker() as session:
        stale = await expire_stale(session)
        await session.commit()
        for order in stale:
            try:
                await bot.send_message(
                    order.user.tg_id,
                    f"⌛ Заявка #{order.id} истекла — курс изменился.\n"
                    f"Оформи заново: /new",
                    reply_markup=open_kb(text="Оформить заново"),
                )
            except Exception:
                logging.exception("не уведомил %s", order.user.tg_id)


async def stuck_job(bot: Bot) -> None:
    async with sessionmaker() as session:
        orders = await stuck_orders(session)
        if not orders:
            return

        now = datetime.now(timezone.utc)
        for order in orders:
            hours = int((now - order.paid_at).total_seconds() // 3600)
            try:
                await bot.send_message(
                    settings.admin_id,
                    f"⏰ <b>Заявка #{order.id} висит {hours} ч</b>\n"
                    f"Клиент заплатил, но заявка не закрыта.\n\n"
                    f"{card(order)}",
                    reply_markup=kb_admin(order),
                )
                await mark_pinged(session, order)
            except Exception:
                logging.exception("не отправил пинг по #%s", order.id)

        await session.commit()

async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    await init_models()


    session = AiohttpSession(proxy=settings.tg_proxy) if settings.tg_proxy else None

    bot = Bot(
        settings.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=RedisStorage.from_url(settings.redis_url))

    dp.update.middleware(DbSessionMiddleware(sessionmaker))
    dp.update.middleware(UserMiddleware())

    dp.include_router(admin.router)   # первым: у него фильтр по admin_id
    dp.include_router(client.router)

    web_runner = await start_web(bot, sessionmaker)
    sched = AsyncIOScheduler()
    sched.add_job(expire_job, "interval", minutes=1, args=(bot,))
    sched.add_job(stuck_job, "interval", minutes=10, args=(bot,))
    sched.start()

    await setup_commands(bot)
    await setup_menu_button(bot)
    await bot.send_message(
        settings.admin_id, "🟢 Бот поднялся", reply_markup=open_kb(text="Открыть панель")
    )

    try:
        await dp.start_polling(bot)
    finally:
        sched.shutdown(wait=False)
        await web_runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())