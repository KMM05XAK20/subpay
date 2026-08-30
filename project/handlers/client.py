from decimal import Decimal, InvalidOperation

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Service, User
from ..order_states import OrderStatus
from ..services import orders as svc
from ..services.rates import set_settle_rate, all_rates
from ..services.rates import available_currencies, get_quote_input
from ..views import card, kb_admin, order_line

import logging

router = Router()


class NewOrder(StatesGroup):
    service = State()
    details = State()
    currency = State()
    amount = State()
    confirm = State()


BTN_NEW = "🧾 Новая заявка"
BTN_MY = "📂 Мои заявки"
BTN_RULES = "📋 Правила"
BTN_HELP = "💬 Написать админу"


def main_menu() -> ReplyKeyboardMarkup:
    b = ReplyKeyboardBuilder()
    b.add(KeyboardButton(text=BTN_NEW))
    b.add(KeyboardButton(text=BTN_MY))
    b.add(KeyboardButton(text=BTN_RULES))
    b.add(KeyboardButton(text=BTN_HELP))
    b.adjust(1, 3)
    return b.as_markup(resize_keyboard=True)


@router.message(CommandStart())
async def on_start(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await msg.answer(
        "<b>Оплачиваю зарубежные сервисы</b>\n\n"
        "Подписки, облака, инструменты для работы — всё, что не проходит "
        "российской картой.\n\n"
        "<b>Как это работает</b>\n"
        "1. Говоришь, что оплатить и на какую сумму\n"
        "2. Считаю стоимость в рублях по текущему курсу\n"
        "3. Подтверждаю, что смогу оплатить, и присылаю реквизиты\n"
        "4. Переводишь — я оплачиваю сервис\n\n"
        "Пароли от аккаунтов не запрашиваю. Если оплатить не получится — "
        "возвращаю всю сумму.\n\n"
        f"Вопросы: @{settings.support_username}",
        reply_markup=main_menu(),
    )

@router.message(Command("rules"))
@router.message(F.text == BTN_RULES)
async def on_rules(msg: Message) -> None:
    await msg.answer(
        "<b>Правила</b>\n\n"
        f"<b>Курс.</b> Фиксируется на {settings.quote_ttl_minutes} минут "
        "с момента подтверждения заявки. Не успел оплатить — "
        "оформляем заново по новому курсу.\n\n"
        "<b>Оплата.</b> Переводи ровно ту сумму, что я пришлю, включая "
        "копейки — по ним я нахожу твой платёж.\n\n"
        "<b>Если не получилось.</b> Не смог оплатить сервис — возвращаю "
        "всю сумму на твои реквизиты в течение суток.\n\n"
        "<b>Доступы.</b> Пароли и коды от аккаунтов не запрашиваю. "
        "Если кто-то просит их от моего имени — это не я.\n\n"
        "<b>Сроки.</b> Отвечаю на заявку в течение часа, оплачиваю "
        "в тот же день.\n\n"
        f"Вопросы: @{settings.support_username}"
    )

@router.message(Command("new"))
@router.message(F.text == BTN_NEW)
async def on_new(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    if await svc.count_active(session, user) >= settings.max_active_orders:
        await msg.answer("Есть незакрытые заявки — сначала закроем их. /my")
        return

    services = list(await session.scalars(
        select(Service).where(Service.is_active.is_(True)).order_by(Service.title)
    ))
    b = InlineKeyboardBuilder()
    for s in services:
        b.button(text=s.title, callback_data=f"svc:{s.id}")
    b.button(text="Другое", callback_data="svc:other")
    b.adjust(2)

    await state.set_state(NewOrder.service)
    await msg.answer("Какой сервис?", reply_markup=b.as_markup())


@router.callback_query(NewOrder.service, F.data.startswith("svc:"))
async def on_service(cb: CallbackQuery, state: FSMContext) -> None:
    raw = cb.data.split(":")[1]
    if raw == "other":
        await state.update_data(service_id=None)
        await state.set_state(NewOrder.details)
        await cb.message.edit_text("Напиши, что оплачиваем — сервис и тариф.")
    else:
        await state.update_data(service_id=int(raw))
        await state.set_state(NewOrder.amount)
        await cb.message.edit_text("Сумма в валюте сервиса? Например: 19.99")
    await cb.answer()

@router.message(CommandStart())
async def on_start(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await msg.answer("Оплачиваю зарубежные сервисы.\n\n"
        "/new — новая заявка\n"
        "/my — мои заявки\n"
        "/rules — правила\n\n"
        "Порядок: сервис и сумма → я подтверждаю → реквизиты → "
        "ты платишь → я оплачиваю.\n\n"
        "Вопросы: @knaa005")

@router.message(Command("cancel"))
async def on_cancel_fsm(msg: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await msg.answer("Нечего отменять.")
        return
    await state.clear()
    await msg.answer("Отменил. /new - начать заново.")

async def ask_currency(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    currencies = await available_currencies(session)
    if not currencies:
        await message.answer("Курсы не заданы, напиши чуть позже.")
        await state.clear()
        return

    b = InlineKeyboardBuilder()
    for cur in currencies:
        b.button(text=cur, callback_data=f"cur:{cur}")
    b.adjust(3)

    await state.set_state(NewOrder.currency)
    await message.answer("В какой валюте счёт?", reply_markup=b.as_markup())

@router.message(NewOrder.details)
async def on_details(
    msg: Message, state: FSMContext, session: AsyncSession
) -> None:

    if (msg.text or "") in {BTN_NEW, BTN_MY, BTN_RULES, BTN_HELP}:
        await msg.answer("Сначала закончим заявку или отправь /cancel")
        return

    await state.update_data(service_raw=(msg.text or "")[:128])
    await ask_currency(msg, state, session)


@router.callback_query(NewOrder.currency, F.data.startswith("cur:"))
async def on_currency(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(currency=cb.data.split(":")[1])
    await state.set_state(NewOrder.amount)
    await cb.message.edit_text("Сумма в этой валюте? Например: 19.99")
    await cb.answer()

@router.message(NewOrder.amount)
async def on_amount(
    msg: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:

    logging.info("on_amount: text=%r data=%r", msg.text, await state.get_data())

    if (msg.text or "") in {BTN_NEW, BTN_MY, BTN_RULES, BTN_HELP}:
        await msg.answer("Сначала закончим заявку или отправь /cancel")
    return

    try:
        amount = Decimal((msg.text or "").replace(",", ".")).quantize(
            Decimal("0.01")
        )
    except InvalidOperation:
        await msg.answer("Не понял сумму. Числом, например: 19.99")
        return
    if amount <= 0:
        await msg.answer("Сумма должна быть больше нуля.")
        return

    data = await state.get_data()
    service = (
        await session.get(Service, data["service_id"])
        if data.get("service_id") else None
    )

    if service and service.min_amount and amount < service.min_amount:
        await msg.answer(f"Минимум для {service.title}: {service.min_amount}")
        return
    if service and service.max_amount and amount > service.max_amount:
        await msg.answer(f"Максимум для {service.title}: {service.max_amount}")
        return

    currency = service.currency if service else data.get("currency", "USD")
    try:
        base, to_usd = await get_quote_input(session, currency)
    except LookupError:
        await msg.answer("Курс временно недоступен, напиши чуть позже.")
        await state.clear()
        return

    order = await svc.create_order(
        session, user, amount, base, to_usd,
        currency=currency,
        service=service, service_raw=data.get("service_raw"),
    )
    
    await session.commit()

    await state.update_data(order_id=order.id)
    await state.set_state(NewOrder.confirm)

    b = InlineKeyboardBuilder()
    b.button(text="Отправить", callback_data=f"cl:submit:{order.id}")
    b.button(text="Отмена", callback_data=f"cl:cancel:{order.id}")

    await msg.answer(
        f"{amount} {currency} по курсу {order.client_rate} ₽\n"
        f"К оплате: <b>{order.amount_rub} ₽</b>\n\n"
        f"Точную сумму пришлю после подтверждения.",
        reply_markup=b.as_markup(),
    )


@router.callback_query(NewOrder.confirm, F.data.startswith("cl:submit:"))
async def on_submit(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    order = await svc.get_order(session, int(cb.data.split(":")[2]))
    if order is None or OrderStatus(order.status) is not OrderStatus.DRAFT:
        await cb.answer("Заявка уже неактуальна", show_alert=True)
        await state.clear()
        return

    await svc.submit(session, order)
    await session.commit()
    await state.clear()

    await cb.message.edit_text(
        f"Заявка #{order.id} отправлена. Отвечу в течение ~15 минут."
    )
    await bot.send_message(settings.admin_id, card(order),
                           reply_markup=kb_admin(order))
    await cb.answer()


@router.callback_query(F.data.startswith("cl:cancel:"))
async def on_cancel(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    order = await svc.get_order(session, int(cb.data.split(":")[2]))
    if order is None or order.user_id != user.id:
        await cb.answer("Заявка не найдена", show_alert=True)
        return
    if OrderStatus(order.status) not in (OrderStatus.DRAFT,
                                          OrderStatus.AWAITING_PAYMENT):
        await cb.answer("Эту заявку уже нельзя отменить", show_alert=True)
        return

    await svc.cancel(session, order)
    await session.commit()
    await state.clear()
    await cb.message.edit_text(f"Заявка #{order.id} отменена.")
    await cb.answer()


@router.message(Command("my"))
@router.message(F.text == BTN_MY)
async def on_my(msg: Message, session: AsyncSession, user: User) -> None:
    rows = await svc.user_orders(session, user)
    if not rows:
        await msg.answer("Заявок пока нет. /new")
        return
    await msg.answer("\n".join(order_line(o) for o in rows))


@router.message(F.text == BTN_HELP)
async def on_help(msg: Message):
    await msg.answer(
        f"Пиши @{settings.support_username} — отвечу лично.\n\n"
        "Если по заявке уже переведены деньги, а бот молчит — "
        "это самый быстрый способ меня найти."
    )

