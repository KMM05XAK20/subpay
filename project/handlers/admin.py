import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import escape
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..config import settings
from ..order_states import OrderStatus, TransitionError, TITLES
from ..services import orders as svc
from ..services.rates import all_rates, set_settle_rate, set_cross
from ..services.users import user_rows, user_totals
from ..services import workflow as wf
from ..views import card, kb_admin, user_line
from ..models import Service, OrderLog

router = Router()
router.message.filter(F.from_user.id == settings.admin_id)
router.callback_query.filter(F.from_user.id == settings.admin_id)

A_RATE = "💱 Курсы"
A_ORDERS = "📂 Активные"
A_STATS = "📊 Статистика"
A_SVC = "🗂 Сервисы"
A_USERS = "👥 Клиенты"


class AdminFlow(StatesGroup):
    reject_reason = State()
    cost_input = State()
    rate_input = State()
    cross_input = State()
    dm_input = State()


def admin_menu() -> ReplyKeyboardMarkup:
    b = ReplyKeyboardBuilder()
    b.add(KeyboardButton(text=A_RATE))
    b.add(KeyboardButton(text=A_ORDERS))
    b.add(KeyboardButton(text=A_STATS))
    b.add(KeyboardButton(text=A_USERS))
    b.add(KeyboardButton(text=A_SVC))
    b.adjust(2, 2)
    return b.as_markup(resize_keyboard=True)


def kb_users(only_cold: bool):
    b = InlineKeyboardBuilder()
    if only_cold:
        b.button(text="Показать всех", callback_data="users:all")
    else:
        b.button(text="Только без оплат", callback_data="users:cold")
    b.adjust(1)
    return b.as_markup()


@router.message(Command("admin"))
async def on_admin(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await msg.answer("🛠 <b>Админка</b>", reply_markup=admin_menu())
    kb = wf.open_kb(text="Открыть панель")
    if kb:
        await msg.answer("📱 Полная панель — в приложении:", reply_markup=kb)


@router.message(Command("app"))
async def on_app(msg: Message) -> None:
    kb = wf.open_kb(text="Открыть панель")
    await msg.answer("📱 Панель управления:" if kb else
                     "WEBAPP_URL не задан", reply_markup=kb)

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

@router.message(F.text == A_RATE)
async def on_rate_menu(msg: Message, session: AsyncSession) -> None:
    rows = await all_rates(session)
    out = []
    for r in rows:
        if r.currency == "USD":
            out.append(f"USD: <b>{r.base_rate}</b> ₽ за доллар")
        elif r.to_usd is not None:
            out.append(f"{r.currency}: <b>{r.to_usd}</b> USD за единицу")

    b = InlineKeyboardBuilder()
    b.button(text="✏️ Курс доллара", callback_data="rate:usd")
    b.button(text="✏️ Кросс-курс", callback_data="rate:cross")
    b.adjust(1)

    await msg.answer(
        "\n".join(out) or "курсы не заданы", reply_markup=b.as_markup()
    )


@router.callback_query(F.data == "rate:usd")
async def on_rate_ask(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFlow.rate_input)
    await cb.message.answer("Сколько рублей за доллар? Например: 104")
    await cb.answer()


@router.message(AdminFlow.rate_input)
async def on_rate_set(
    msg: Message, state: FSMContext, session: AsyncSession
) -> None:
    try:
        value = Decimal((msg.text or "").replace(",", "."))
    except InvalidOperation:
        await msg.answer("Числом, например: 104")
        return
    await set_settle_rate(session, value)
    await state.clear()
    await msg.answer(f"USD = {value} ₽", reply_markup=admin_menu())


@router.callback_query(F.data == "rate:cross")
async def on_cross_ask(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFlow.cross_input)
    await cb.message.answer("Валюта и курс к доллару. Например: EUR 1.16")
    await cb.answer()


@router.message(AdminFlow.cross_input)
async def on_cross_set(
    msg: Message, state: FSMContext, session: AsyncSession
) -> None:
    parts = (msg.text or "").split()
    try:
        currency = parts[0].upper()
        value = Decimal(parts[1].replace(",", "."))
    except (IndexError, InvalidOperation):
        await msg.answer("Формат: EUR 1.16")
        return
    if currency == "USD":
        await msg.answer("USD — базовая валюта, кросс не нужен")
        return
    await set_cross(session, currency, value)
    await state.clear()
    await msg.answer(f"1 {currency} = {value} USD", reply_markup=admin_menu())


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
@router.message(F.text == A_STATS)
async def on_stats(msg: Message, session: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    day = await svc.stats_since(session, now - timedelta(days=1))
    week = await svc.stats_since(session, now - timedelta(days=7))
    await msg.answer(
        "📊 <b>Статистика</b>\n\n"
        f"<b>24 часа</b>\n{day['count']} шт · {day['turnover']} ₽ · "
        f"профит <b>{day['profit']} ₽</b>\n\n"
        f"<b>7 дней</b>\n{week['count']} шт · {week['turnover']} ₽ · "
        f"профит <b>{week['profit']} ₽</b>",
        reply_markup=wf.open_kb(text="Подробнее"),
    )

@router.message(F.text == A_ORDERS)
async def on_active(msg: Message, session: AsyncSession) -> None:
    rows = await svc.active_orders(session)
    if not rows:
        await msg.answer("Активных заявок нет")
        return
    for order in rows:
        await msg.answer(card(order), reply_markup=kb_admin(order))


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
@router.message(F.text == A_SVC)
async def on_svc(msg: Message, session: AsyncSession) -> None:
    parts = (msg.text or "").split()
    
    if not parts or parts[0].startswith("🗂") or len(parts) == 1:
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

    if action == "msg":
        await state.set_state(AdminFlow.dm_input)
        await state.update_data(order_id=order.id)
        await cb.message.answer(
            f"💬 Что написать клиенту по #{order.id}? Отправь текст или /cancel"
        )
        await cb.answer()
        return

    current = OrderStatus(order.status)
    allowed = wf.EXPECTED.get(action)
    if allowed is not None and current not in allowed:
        await cb.answer(f"Уже обработано ({TITLES[current]})", show_alert=True)
        await cb.message.edit_text(card(order), reply_markup=kb_admin(order))
        return

    if action == "reject":
        await state.set_state(AdminFlow.reject_reason)
        await state.update_data(order_id=order.id)
        await cb.message.answer(f"⛔ #{order.id}: причина отказа?")
        await cb.answer()
        return

    if action == "done":
        await state.set_state(AdminFlow.cost_input)
        await state.update_data(order_id=order.id)
        await cb.message.answer(
            f"🏁 #{order.id}: сколько реально ушло, ₽? (0 — пропустить)"
        )
        await cb.answer()
        return

    try:
        toast = await wf.perform(
            session, bot, order, action, cb.from_user.id, reason="ручной возврат"
        )
    except wf.ActionError as exc:
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
    try:
        await wf.perform(session, bot, order, "reject", msg.from_user.id,
                         reason=msg.text or "")
    except wf.ActionError as exc:
        await msg.answer(str(exc))
        return
    await msg.answer(f"⛔ Отклонена #{order.id}")
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
    try:
        await wf.perform(session, bot, order, "done", msg.from_user.id, cost=cost)
    except wf.ActionError as exc:
        await msg.answer(str(exc))
        await state.clear()
        return
    await msg.answer(
        f"✅ Закрыта #{order.id}. Профит: <b>{order.profit_rub or '—'} ₽</b>"
    )
    await state.clear()


@router.message(AdminFlow.dm_input)
async def on_dm(
    msg: Message, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    data = await state.get_data()
    order = await svc.get_order(session, data["order_id"])
    if order is None:
        await state.clear()
        await msg.answer("Заявка не найдена")
        return

    await wf.send_to_client(bot, session, order, msg.from_user.id, msg.text or "")
    await state.clear()
    await msg.answer(f"📨 Отправлено по #{order.id}")


async def users_text(session: AsyncSession, only_cold: bool) -> str:
    rows = await user_rows(session, only_cold=only_cold)
    totals = await user_totals(session)

    head = (
        f"<b>Клиенты</b> — всего {totals['total']}, "
        f"с оплатами {totals['buyers']}"
    )
    if not rows:
        return f"{head}\n\nпусто"

    title = "Без оплат:" if only_cold else "Последние:"
    body = "\n".join(user_line(r) for r in rows)
    return f"{head}\n\n{title}\n{body}"


@router.message(F.text == A_USERS)
async def on_users(msg: Message, session: AsyncSession) -> None:
    await msg.answer(
        await users_text(session, only_cold=False),
        reply_markup=kb_users(False),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.in_({"users:all", "users:cold"}))
async def on_users_toggle(cb: CallbackQuery, session: AsyncSession) -> None:
    only_cold = cb.data == "users:cold"
    await cb.message.edit_text(
        await users_text(session, only_cold=only_cold),
        reply_markup=kb_users(only_cold),
        disable_web_page_preview=True,
    )
    await cb.answer()