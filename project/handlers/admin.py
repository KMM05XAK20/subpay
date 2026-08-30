import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..order_states import OrderStatus, TransitionError, TITLES
from ..services import orders as svc
from ..services.rates import all_rates, set_settle_rate, set_cross
from ..views import card, kb_admin, payment_text

router = Router()
router.message.filter(F.from_user.id == settings.admin_id)
router.callback_query.filter(F.from_user.id == settings.admin_id)


class AdminFlow(StatesGroup):
    reject_reason = State()
    cost_input = State()


async def notify(bot: Bot, tg_id: int, text: str) -> None:
    try:
        await bot.send_message(tg_id, text)
    except Exception:
        logging.exception("не доставил сообщение %s", tg_id)


@router.message(Command("rate"))
async def on_rate(msg: Message, session: AsyncSession) -> None:
    parts = (msg.text or "").split()
    if len(parts) == 1:
        rows = await all_rates(session)
        out = []
        for r in rows:
            if r.currency == "USD":
                out.append(f"USD: {r.base_rate} ₽ за доллар")
            else:
                out.append(f"{r.currency}: {r.to_usd} USD за единицу")
        await msg.answer("\n".join(out) or "курсы не заданы")
        return
    try:
        value = Decimal(parts[1].replace(",", "."))
    except (IndexError, InvalidOperation):
        await msg.answer("Формат: /rate 100.5  (рублей за доллар)")
        return
    await set_settle_rate(session, value)
    await msg.answer(f"USD = {value} ₽")


@router.message(Command("cross"))
async def on_cross(msg: Message, session: AsyncSession) -> None:
    parts = (msg.text or "").split()
    try:
        currency = parts[1].upper()
        value = Decimal(parts[2].replace(",", "."))
    except (IndexError, InvalidOperation):
        await msg.answer("Формат: /cross EUR 1.08  (долларов за евро)")
        return
    if currency == "USD":
        await msg.answer("USD — базовая валюта, кросс не нужен")
        return
    await set_cross(session, currency, value)
    await msg.answer(f"1 {currency} = {value} USD")


@router.message(Command("stats"))
async def on_stats(msg: Message, session: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    day = await svc.stats_since(session, now - timedelta(days=1))
    week = await svc.stats_since(session, now - timedelta(days=7))
    await msg.answer(
        f"<b>24 часа</b>: {day['count']} шт · "
        f"{day['turnover']} ₽ · профит {day['profit']} ₽\n"
        f"<b>7 дней</b>: {week['count']} шт · "
        f"{week['turnover']} ₽ · профит {week['profit']} ₽"
    )


@router.message(Command("order"))
async def on_order(msg: Message, session: AsyncSession) -> None:
    parts = (msg.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await msg.answer("Формат: /order 42")
        return
    order = await svc.get_order(session, int(parts[1]))
    if order is None:
        await msg.answer("Не найдена")
        return
    await msg.answer(card(order), reply_markup=kb_admin(order))

@router.message(Command("svc"))
async def on_svc(msg: Message, session: AsyncSession) -> None:
    parts = (msg.text or "").split(maxsplit=3)

    if len(parts) == 1:
        rows = await session.scalars(select(Service).order_by(Service.title))
        await msg.answer("\n".join(
            f"{s.id}. {s.title} ({s.slug}, {s.currency})"
            f"{'' if s.is_active else ' — выкл'}"
            for s in rows
        ) or "каталог пуст")
        return
    
    if parts[1] == "add" and len(parts) == 4:
        slug, rest = parts[2], parts[3].split(maxsplit=1)
        currency = rest[0].upper()
        title = rest[1] if len(rest) > 1 else slug
        session.add(Service(slug=slug, title=title, currency=currency))
        await session.commit()
        await msg.answer(f"Добавлен {title}")
        return
    
    if parts[1] == "off" and len(parts) >= 3:
        service = await session.get(Service, int(parts[2]))
        if service:
            service.is_active = False
            await session.commit()
            await msg.answer(f"{service.title} выключен")
        return
    
    await msg.answer("/svc — список\n/svc add <slug> <CUR> <название>\n/svc off <id>")


@router.callback_query(F.data.startswith("ord:"))
async def on_action(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    _, action, raw_id = cb.data.split(":")
    order = await svc.get_order(session, int(raw_id))
    if order is None:
        await cb.answer("Заявка не найдена", show_alert=True)
        return

    admin = cb.from_user.id

    expected = {
        "approve": OrderStatus.REVIEW,
        "reject": OrderStatus.REVIEW,
        "paid": OrderStatus.AWAITING_PAYMENT,
        "expire": OrderStatus.AWAITING_PAYMENT,
        "work": OrderStatus.PAID,
        "done": OrderStatus.IN_PROGRESS,
        "refund": (OrderStatus.PAID, OrderStatus.IN_PROGRESS),
        "refunded": OrderStatus.REFUND_PENDING,
    }.get(action)
    
    current = OrderStatus(order.status)
    allowed = expected if isinstance(expected, tuple) else (expected,)
    if expected is not None and current not in allowed:
        await cb.answer(f"Уже обработано ({TITLES[current]})", show_alert=True)
        await cb.message.edit_text(card(order), reply_markup=kb_admin(order))
        return

    try:
        if action == "approve":
            await svc.approve(session, order, admin)
            await session.commit()
            await notify(bot, order.user.tg_id,
                         payment_text(order, settings.requisites))
            toast = "Принято"

        elif action == "reject":
            await state.set_state(AdminFlow.reject_reason)
            await state.update_data(order_id=order.id)
            await cb.message.answer(f"#{order.id}: причина отказа?")
            await cb.answer()
            return

        elif action == "paid":
            await svc.confirm_payment(session, order, admin)
            await session.commit()
            await notify(bot, order.user.tg_id,
                         f"Оплата по #{order.id} получена, работаю.")
            toast = "Оплата подтверждена"

        elif action == "work":
            await svc.start_work(session, order, admin)
            await session.commit()
            toast = "В работе"

        elif action == "done":
            await state.set_state(AdminFlow.cost_input)
            await state.update_data(order_id=order.id)
            await cb.message.answer(
                f"#{order.id}: сколько реально ушло, ₽? (0 — пропустить)"
            )
            await cb.answer()
            return

        elif action == "refund":
            await svc.to_refund(session, order, admin, "ручной возврат")
            await session.commit()
            await notify(bot, order.user.tg_id,
                         f"По #{order.id} выполнить не смог, оформляю возврат. "
                         f"Пришли реквизиты для возврата.")
            toast = "Возврат"

        elif action == "refunded":
            await svc.refunded(session, order, admin)
            await session.commit()
            await notify(bot, order.user.tg_id,
                         f"Возврат по #{order.id} отправлен.")
            toast = "Возвращено"

        elif action == "expire":
            await svc.transition(session, order, OrderStatus.EXPIRED,
                                 actor=f"admin:{admin}")
            await session.commit()
            toast = "Протухла"
        else:
            await cb.answer("Неизвестное действие", show_alert=True)
            return

    except TransitionError as exc:
        await session.rollback()
        await cb.answer(str(exc), show_alert=True)
        return

    await cb.message.edit_text(card(order), reply_markup=kb_admin(order))
    await cb.answer(toast)


@router.message(AdminFlow.reject_reason)
async def on_reject_reason(
    msg: Message, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    data = await state.get_data()
    order = await svc.get_order(session, data["order_id"])
    await svc.reject(session, order, msg.from_user.id, msg.text or "")
    await session.commit()
    await notify(bot, order.user.tg_id,
                 f"Заявка #{order.id} отклонена: {msg.text}")
    await msg.answer(f"Отклонена #{order.id}")
    await state.clear()


@router.message(AdminFlow.cost_input)
async def on_cost(
    msg: Message, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    try:
        cost = Decimal((msg.text or "").replace(",", "."))
    except InvalidOperation:
        await msg.answer("Число, пожалуйста")
        return

    data = await state.get_data()
    order = await svc.get_order(session, data["order_id"])
    await svc.complete(session, order, msg.from_user.id,
                       cost_rub=cost if cost > 0 else None)
    await session.commit()

    await notify(bot, order.user.tg_id, f"Заявка #{order.id} выполнена ✅")
    await msg.answer(f"Закрыта #{order.id}. Профит: {order.profit_rub or '—'} ₽")
    await state.clear()