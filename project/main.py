import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.client.session.aiohttp import AiohttpSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault


from .config import settings
from .db import init_models, sessionmaker
from .handlers import admin, client
from .middlewares.db import DbSessionMiddleware
from .middlewares.users import UserMiddleware
from .services.orders import expire_stale



async def setup_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="new", description="Новая заявка"),
            BotCommand(command="my", description="Мои заявки"),
            BotCommand(command="rules", description="Правила"),
            BotCommand(command="cancel", description="Отменить заявку"),
        ],
        scope=BotCommandScopeDefault(),
    )
    await bot.set_my_commands(
        [
            BotCommand(command="admin", description="Админка"),
            BotCommand(command="rate", description="Курс доллара"),
            BotCommand(command="cross", description="Кросс-курс"),
            BotCommand(command="order", description="Заявка по номеру"),
            BotCommand(command="svc", description="Сервисы"),
            BotCommand(command="stats", description="Статистика"),
        ],
        scope=BotCommandScopeChat(chat_id=settings.admin_id),
    )

async def expire_job(bot: Bot) -> None:
    async with sessionmaker() as session:
        stale = await expire_stale(session)
        await session.commit()
        for order in stale:
            try:
                await bot.send_message(
                    order.user.tg_id,
                    f"Заявка #{order.id} истекла — курс изменился. "
                    f"Оформи заново: /new",
                )
            except Exception:
                logging.exception("не уведомил %s", order.user.tg_id)


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

    
    sched = AsyncIOScheduler()
    sched.add_job(expire_job, "interval", minutes=1, args=(bot,))
    sched.start()

    await setup_commands(bot)
    await bot.send_message(settings.admin_id, "Бот поднялся")
    
    try:
        await dp.start_polling(bot)
    finally:
        sched.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())