from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, ROUND_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .models import Order
from .order_states import OrderStatus

QUOTE_STATES = (OrderStatus.REVIEW, OrderStatus.AWAITING_PAYMENT)


def calc_quote(
    amount_foreign: Decimal,
    base_rate: Decimal,
    markup_pct: Decimal,
) -> tuple[Decimal, Decimal]:
    """-> (client_rate, amount_rub) без pay_code."""
    client_rate = (base_rate * (Decimal(1) + markup_pct / 100)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )
    total = (amount_foreign * client_rate).quantize(
        Decimal("1"), rounding=ROUND_UP
    )
    return client_rate, total


async def pick_pay_code(session: AsyncSession, amount_rub: Decimal) -> int:
    """Копейки-маркер: свободный код среди активных заявок с той же суммой."""
    stmt = select(Order.pay_code).where(
        Order.status.in_(QUOTE_STATES),
        Order.amount_rub == amount_rub,
    )
    used = set((await session.scalars(stmt)).all())
    for code in range(1, 100):
        if code not in used:
            return code
    raise RuntimeError("свободных pay_code нет")


def expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(
        minutes=settings.quote_ttl_minutes
    )