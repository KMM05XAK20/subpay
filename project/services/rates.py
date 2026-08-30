from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Rate
from ..config import settings

SETTLE = "USD"


async def get_quote_input(
    session: AsyncSession, currency: str
) -> tuple[Decimal, Decimal]:
    """-> (курс валюта→рубль по себестоимости, коэффициент валюта→USD)."""
    settle = await session.get(Rate, SETTLE)
    if settle is None or settle.base_rate is None:
        raise LookupError(SETTLE)

    if currency == SETTLE:
        return settle.base_rate, Decimal(1)

    row = await session.get(Rate, currency)
    if row is None or row.to_usd is None:
        raise LookupError(currency)

    buffer = Decimal(1) + Decimal(str(settings.cross_buffer_pct)) / 100
    base = (settle.base_rate * row.to_usd).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )
    return base, row.to_usd


async def set_settle_rate(session: AsyncSession, value: Decimal) -> None:
    """Сколько рублей стоит доллар при пополнении карты."""
    row = await session.get(Rate, SETTLE)
    if row is None:
        session.add(Rate(currency=SETTLE, base_rate=value, to_usd=Decimal(1)))
    else:
        row.base_rate = value
        row.to_usd = Decimal(1)
    await session.commit()


async def set_cross(
    session: AsyncSession, currency: str, to_usd: Decimal
) -> None:
    """Сколько долларов в единице валюты."""
    row = await session.get(Rate, currency)
    if row is None:
        session.add(Rate(currency=currency, to_usd=to_usd))
    else:
        row.to_usd = to_usd
    await session.commit()


async def available_currencies(session: AsyncSession) -> list[str]:
    settle = await session.get(Rate, SETTLE)
    if settle is None or settle.base_rate is None:
        return []
    rows = await session.scalars(
        select(Rate).where(Rate.to_usd.is_not(None)).order_by(Rate.currency)
    )
    return [r.currency for r in rows]


async def all_rates(session: AsyncSession) -> list[Rate]:
    return list(await session.scalars(select(Rate).order_by(Rate.currency)))