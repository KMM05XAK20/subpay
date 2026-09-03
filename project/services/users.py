from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Order, User
from ..order_states import ACTIVE, OrderStatus


@dataclass
class UserRow:
    user: User
    oreders: int
    done: int
    turnover: Decimal
    active: int
    last_seen: datetime | None



async def user_rows(session: AsyncSession, only_cold: bool = False, limit: int = 15) -> list[UserRow]:
    """only_cold — те, кто ни разу не доводил заявку до оплаты."""
    done_sum = func.sum(
        func.case((Order.status == OrderStatus.DONE, Order.amount_rub), else_=0)
    )
    done_cnt = func.count(
        func.nullif(Order.status != OrderStatus.DONE, True)
    )
    active_cnt = func.count(
        func.nullif(~Order.status.in_(ACTIVE), True)
    )

    stmt = (
        select(
            User,
            func.count(Order.id),
            done_cnt,
            func.coalesce(done_sum, 0),
            active_cnt,
            func.max(Order.created_at),
        )
        .outerjoin(Order, Order.user_id == User.id)
        .group_by(User.id)
        .order_by(func.coalesce(func.max(Order.created_at), User.created_at).desc())
        .limit(limit)
    )
    if only_cold:
        stmt = stmt.having(done_cnt == 0)

    rows = (await session.execute(stmt)).all()
    return [
        UserRow(user=r[0], oreders=r[1], done=r[2],
                turnover=r[3], active=r[4], last_seen=r[5])
        for r in rows
    ]



async def user_totals(session: AsyncSession) -> dict:
    total = await session.scalar(select(func.count()).select_from(User)) or 0
    buyers = await session.scalar(
        select(func.count(func.distinct(Order.user_id)))
        .where(Order.status == OrderStatus.DONE)
    ) or 0
    return {"total": total, "buyers": buyers}