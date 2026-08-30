from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import settings
from ..models import Order, OrderLog, Service, User
from ..order_states import ACTIVE, TERMINAL, OrderStatus, check_transition
from ..pricing import calc_quote, expires_at, pick_pay_code

LOAD = (selectinload(Order.user),)


async def get_order(session: AsyncSession, order_id: int) -> Order | None:
    """Всегда через это. В async lazy-load отношений падает MissingGreenlet."""
    return await session.scalar(
        select(Order).where(Order.id == order_id).options(*LOAD)
    )


async def transition(
    session: AsyncSession,
    order: Order,
    dst: OrderStatus,
    actor: str,
    comment: str | None = None,
) -> Order:
    """Единственная точка смены статуса."""
    src = OrderStatus(order.status)
    check_transition(src, dst)

    now = datetime.now(timezone.utc)
    order.status = dst
    if dst is OrderStatus.PAID:
        order.paid_at = now
    if dst in TERMINAL:
        order.closed_at = now

    session.add(OrderLog(order_id=order.id, src=src, dst=dst,
                         actor=actor, comment=comment))
    await session.flush()
    return order


async def count_active(session: AsyncSession, user: User) -> int:
    return await session.scalar(
        select(func.count()).select_from(Order).where(
            Order.user_id == user.id, Order.status.in_(ACTIVE)
        )
    ) or 0


async def create_order(
    session: AsyncSession,
    user: User,
    amount_foreign: Decimal,
    base_rate: Decimal,
    to_usd: Decimal = Decimal(1),
    currency: str | None = None,
    service: Service | None = None,
    service_raw: str | None = None,
    client_note: str | None = None,
) -> Order:
    markup = (
        service.markup_pct if service and service.markup_pct is not None
        else Decimal(str(settings.default_markup_pct))
    )
    client_rate, amount_rub = calc_quote(amount_foreign, base_rate, markup)
    amount_usd = (amount_foreign * to_usd).quantize(Decimal("0.01"))

    order = Order(
        user_id=user.id,
        service_id=service.id if service else None,
        service_raw=service_raw,
        amount_foreign=amount_foreign,
        amount_usd=amount_usd,
        currency=service.currency if service else (currency or "USD"),
        base_rate=base_rate,
        markup_pct=markup,
        client_rate=client_rate,
        amount_rub=amount_rub,
        pay_code=0,
        status=OrderStatus.DRAFT,
        client_note=client_note,
    )
    session.add(order)
    await session.flush()
    session.add(OrderLog(order_id=order.id, src=None, dst=OrderStatus.DRAFT,
                         actor="client", comment="создана"))
    await session.flush()
    return order

async def active_orders(session: AsyncSession, limit: int = 20) -> list[Order]:
    return list(await session.scalars(
        select(Order).where(Order.status.in_(ACTIVE))
        .order_by(Order.id.desc()).limit(limit).options(*LOAD)
    ))

async def submit(session: AsyncSession, order: Order) -> Order:
    return await transition(session, order, OrderStatus.REVIEW, actor="client")


async def approve(session: AsyncSession, order: Order, admin_id: int) -> Order:
    order.pay_code = await pick_pay_code(session, order.amount_rub)
    order.expires_at = expires_at()
    return await transition(session, order, OrderStatus.AWAITING_PAYMENT,
                            actor=f"admin:{admin_id}")


async def reject(session, order: Order, admin_id: int, reason: str) -> Order:
    order.admin_note = reason
    return await transition(session, order, OrderStatus.REJECTED,
                            actor=f"admin:{admin_id}", comment=reason)


async def confirm_payment(session, order: Order, admin_id: int) -> Order:
    return await transition(session, order, OrderStatus.PAID,
                            actor=f"admin:{admin_id}")


async def start_work(session, order: Order, admin_id: int) -> Order:
    return await transition(session, order, OrderStatus.IN_PROGRESS,
                            actor=f"admin:{admin_id}")


async def complete(
    session, order: Order, admin_id: int, cost_rub: Decimal | None = None
) -> Order:
    if cost_rub is not None:
        order.cost_rub = cost_rub
    return await transition(session, order, OrderStatus.DONE,
                            actor=f"admin:{admin_id}")


async def to_refund(session, order: Order, admin_id: int, reason: str) -> Order:
    order.admin_note = reason
    return await transition(session, order, OrderStatus.REFUND_PENDING,
                            actor=f"admin:{admin_id}", comment=reason)


async def refunded(session, order: Order, admin_id: int) -> Order:
    return await transition(session, order, OrderStatus.REFUNDED,
                            actor=f"admin:{admin_id}")


async def cancel(session, order: Order) -> Order:
    return await transition(session, order, OrderStatus.CANCELED, actor="client")


async def expire_stale(session: AsyncSession) -> list[Order]:
    now = datetime.now(timezone.utc)
    stmt = (
        select(Order)
        .where(
            Order.status == OrderStatus.AWAITING_PAYMENT,
            Order.expires_at < now,
        )
        .options(*LOAD)
    )
    stale = list(await session.scalars(stmt))
    for order in stale:
        await transition(session, order, OrderStatus.EXPIRED, actor="system")
    return stale


async def user_orders(
    session: AsyncSession, user: User, limit: int = 10
) -> list[Order]:
    return list(await session.scalars(
        select(Order).where(Order.user_id == user.id)
        .order_by(Order.id.desc()).limit(limit).options(*LOAD)
    ))


async def stats_since(session: AsyncSession, since: datetime) -> dict:
    row = (await session.execute(
        select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.amount_rub), 0),
            func.coalesce(func.sum(Order.amount_rub - Order.cost_rub), 0),
        ).where(Order.status == OrderStatus.DONE, Order.closed_at >= since)
    )).one()
    return {"count": row[0], "turnover": row[1], "profit": row[2]}