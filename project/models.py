# models.py
from datetime import datetime
from decimal import Decimal

from typing import Optional

from sqlalchemy import (
    BigInteger, DateTime, ForeignKey, Numeric, String, Text, func
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .order_states import OrderStatus


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    is_blocked: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    orders: Mapped[list["Order"]] = relationship(back_populates="user")


class Service(Base):
    """Каталог. Не обязателен на старте, но избавляет от разнобоя
    'нетфликс/Netflix/netflix' в заявках."""
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(48), unique=True)
    title: Mapped[str] = mapped_column(String(96))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    min_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    max_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    markup_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))  # override
    is_active: Mapped[bool] = mapped_column(default=True)
    note: Mapped[str | None] = mapped_column(Text)  # памятка себе: как платить

class Rate(Base):
    __tablename__ = "rates"

    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    base_rate: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    service_id: Mapped[int | None] = mapped_column(ForeignKey("services.id"))
    service_raw: Mapped[str | None] = mapped_column(String(128))  # если нет в каталоге

    # что оплачиваем
    amount_foreign: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    # снапшот курса на момент расчёта — не пересчитывать никогда
    base_rate: Mapped[Decimal] = mapped_column(Numeric(18, 4))   # твоя себестоимость
    markup_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2))   # наценка
    client_rate: Mapped[Decimal] = mapped_column(Numeric(18, 4)) # по чему платит клиент
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2))  # итог к оплате

    # идентификация входящего перевода
    pay_code: Mapped[int] = mapped_column()  # копейки, 1..99

    status: Mapped[OrderStatus] = mapped_column(String(24), index=True)

    payment_link: Mapped[str | None] = mapped_column(Text)  # инвойс от клиента
    client_note: Mapped[str | None] = mapped_column(Text)
    admin_note: Mapped[str | None] = mapped_column(Text)

    # факт — для честного P&L, а не по прайсу
    cost_rub: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="orders")
    service: Mapped[Optional["Service"]] = relationship(lazy="selectin")
    logs: Mapped[list["OrderLog"]] = relationship(back_populates="order")

    @property
    def total_rub(self) -> Decimal:
        """Сумма к переводу - с копейками-маркером."""
        return self.amount_rub + Decimal(self.pay_code) / 100

    @property
    def profit_rub(self) -> Decimal | None:
        if self.cost_rub is None:
            return None
        return self.amount_rub - self.cost_rub

    @property
    def service_title(self) -> str:
        return self.service.title if self.service else (self.service_raw or "-")


class OrderLog(Base):
    """Кто и когда двигал заявку. Через месяц скажешь спасибо."""
    __tablename__ = "order_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    src: Mapped[str | None] = mapped_column(String(24))
    dst: Mapped[str] = mapped_column(String(24))
    actor: Mapped[str] = mapped_column(String(24))  # client / admin / system
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    order: Mapped["Order"] = relationship(back_populates="logs")