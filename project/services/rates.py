from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Rate


async def get_base_rate(session: AsyncSession, currency: str = "USD") -> Decimal:
    rate = await session.get(Rate, currency)
    if rate is None:
        raise LookupError(currency)
    return rate.base_rate


async def set_base_rate(
    session: AsyncSession, currency: str, value: Decimal
) -> None:
    rate = await session.get(Rate, currency)
    if rate is None:
        session.add(Rate(currency=currency, base_rate=value))
    else:
        rate.base_rate = value
    await session.commit()


async def all_rates(session: AsyncSession) -> list[Rate]:
    return list(await session.scalars(select(Rate).order_by(Rate.currency)))