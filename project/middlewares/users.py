from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from aiogram.types import User as TgUser
from sqlalchemy import select

from ..models import User


class UserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user: TgUser | None = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)

        session = data["session"]
        user = await session.scalar(select(User).where(User.tg_id == tg_user.id))
        if user is None:
            user = User(tg_id=tg_user.id, username=tg_user.username)
            session.add(user)
            await session.commit()
        elif user.username != tg_user.username:
            user.username = tg_user.username
            await session.commit()

        if user.is_blocked:
            return None

        data["user"] = user
        return await handler(event, data)