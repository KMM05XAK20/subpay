"""Действия над заявками, общие для бота и Mini App.

Здесь же — уведомления клиенту и переписка по заявке, чтобы обе точки
входа вели себя одинаково.
"""
import logging
from decimal import Decimal
from html import escape
from urllib.parse import urlencode

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Order, OrderLog, User
from ..order_states import TITLES, OrderStatus, TransitionError
from ..views import card, kb_admin, payment_text
from . import orders as svc

TO_CLIENT = "→ клиенту: "
FROM_CLIENT = "← от клиента: "


class ActionError(Exception):
    """Действие невозможно (не тот статус, нет данных)."""


# действие -> статусы, из которых оно допустимо
EXPECTED: dict[str, tuple[OrderStatus, ...]] = {
    "approve": (OrderStatus.REVIEW,),
    "reject": (OrderStatus.REVIEW,),
    "paid": (OrderStatus.AWAITING_PAYMENT,),
    "expire": (OrderStatus.AWAITING_PAYMENT,),
    "work": (OrderStatus.PAID,),
    "done": (OrderStatus.IN_PROGRESS,),
    "refund": (OrderStatus.PAID, OrderStatus.IN_PROGRESS),
    "refunded": (OrderStatus.REFUND_PENDING,),
}


def webapp_link(order_id: int | None = None) -> str | None:
    if not settings.webapp_url:
        return None
    if order_id is None:
        return settings.webapp_url
    sep = "&" if "?" in settings.webapp_url else "?"
    return f"{settings.webapp_url}{sep}{urlencode({'o': order_id})}"


def open_kb(order_id: int | None = None, text: str = "Открыть заявку"):
    url = webapp_link(order_id)
    if not url:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"📱 {text}", web_app=WebAppInfo(url=url))
    ]])


async def notify(bot: Bot, tg_id: int, text: str, order_id: int | None = None) -> None:
    try:
        await bot.send_message(
            tg_id, text,
            reply_markup=open_kb(order_id) if order_id else None,
        )
    except Exception:
        logging.exception("не доставил сообщение %s", tg_id)


async def notify_admin_new_order(bot: Bot, order: Order) -> None:
    try:
        await bot.send_message(settings.admin_id, card(order),
                               reply_markup=kb_admin(order))
    except Exception:
        logging.exception("не уведомил админа о #%s", order.id)


async def forward_client_message(
    bot: Bot, session: AsyncSession, user: User, order: Order, text: str
) -> None:
    handle = f" (@{escape(user.username)})" if user.username else ""
    await bot.send_message(
        settings.admin_id,
        f"💬 <b>Сообщение по #{order.id}</b>\n"
        f'от <a href="tg://user?id={user.tg_id}">'
        f"{escape(user.full_name or 'клиент')}</a>{handle}\n"
        f"<blockquote>{escape(text)}</blockquote>",
        reply_markup=kb_admin(order),
    )
    session.add(OrderLog(order_id=order.id, src=order.status, dst=order.status,
                         actor="client", comment=FROM_CLIENT + text[:200]))
    await session.commit()


async def send_to_client(
    bot: Bot, session: AsyncSession, order: Order, admin_id: int, text: str
) -> None:
    await bot.send_message(
        order.user.tg_id,
        f"📨 <b>Сообщение по заявке #{order.id}</b>\n"
        f"<blockquote>{escape(text)}</blockquote>\n"
        f"<i>Ответь на это сообщение, чтобы написать мне.</i>",
        reply_markup=open_kb(order.id, "Ответить в приложении"),
    )
    session.add(OrderLog(order_id=order.id, src=order.status, dst=order.status,
                         actor=f"admin:{admin_id}", comment=TO_CLIENT + text[:200]))
    await session.commit()


async def perform(
    session: AsyncSession,
    bot: Bot,
    order: Order,
    action: str,
    admin_id: int,
    *,
    reason: str | None = None,
    cost: Decimal | None = None,
) -> str:
    """Выполняет действие админа, коммитит и уведомляет клиента.
    Возвращает короткий текст для тоста."""
    allowed = EXPECTED.get(action)
    if allowed is None:
        raise ActionError("Неизвестное действие")
    current = OrderStatus(order.status)
    if current not in allowed:
        raise ActionError(f"Уже обработано ({TITLES[current]})")

    tg = order.user.tg_id
    try:
        if action == "approve":
            await svc.approve(session, order, admin_id)
            await session.commit()
            await notify(bot, tg, payment_text(order, settings.requisites), order.id)
            return "Принято"

        if action == "reject":
            reason = (reason or "").strip()
            if not reason:
                raise ActionError("Укажи причину отказа")
            await svc.reject(session, order, admin_id, reason)
            await session.commit()
            await notify(bot, tg,
                         f"⛔ Заявка #{order.id} отклонена.\n"
                         f"<blockquote>{escape(reason)}</blockquote>", order.id)
            return "Отклонена"

        if action == "paid":
            await svc.confirm_payment(session, order, admin_id)
            await session.commit()
            await notify(bot, tg, f"💰 Оплата по #{order.id} получена, приступаю.",
                         order.id)
            return "Оплата подтверждена"

        if action == "work":
            await svc.start_work(session, order, admin_id)
            await session.commit()
            return "В работе"

        if action == "done":
            await svc.complete(session, order, admin_id,
                               cost_rub=cost if cost and cost > 0 else None)
            await session.commit()
            await notify(bot, tg, f"✅ Заявка #{order.id} выполнена. Спасибо!",
                         order.id)
            return "Закрыта"

        if action == "refund":
            await svc.to_refund(session, order, admin_id, reason or "ручной возврат")
            await session.commit()
            await notify(bot, tg,
                         f"↩️ По #{order.id} выполнить не получилось, оформляю "
                         f"возврат. Пришли реквизиты для возврата ответом на это "
                         f"сообщение.", order.id)
            return "Возврат"

        if action == "refunded":
            await svc.refunded(session, order, admin_id)
            await session.commit()
            await notify(bot, tg, f"↩️ Возврат по #{order.id} отправлен.", order.id)
            return "Возвращено"

        # expire
        await svc.transition(session, order, OrderStatus.EXPIRED,
                             actor=f"admin:{admin_id}")
        await session.commit()
        await notify(bot, tg, f"⌛ Заявка #{order.id} истекла. Оформи заново.")
        return "Истекла"
    except TransitionError as exc:
        await session.rollback()
        raise ActionError(str(exc)) from exc
