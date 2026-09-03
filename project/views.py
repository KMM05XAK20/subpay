from html import escape

from aiogram.utils.keyboard import InlineKeyboardBuilder

from .models import Order
from .order_states import TITLES, OrderStatus, TERMINAL


def kb_admin(order: Order):
    b = InlineKeyboardBuilder()
    st = OrderStatus(order.status)
    oid = order.id

    if st is OrderStatus.REVIEW:
        b.button(text="✅ Принять", callback_data=f"ord:approve:{oid}")
        b.button(text="❌ Отказ", callback_data=f"ord:reject:{oid}")
    elif st is OrderStatus.AWAITING_PAYMENT:
        b.button(text="💰 Деньги пришли", callback_data=f"ord:paid:{oid}")
        b.button(text="⌛ Протухла", callback_data=f"ord:expire:{oid}")
    elif st is OrderStatus.PAID:
        b.button(text="▶️ В работу", callback_data=f"ord:work:{oid}")
        b.button(text="↩️ Возврат", callback_data=f"ord:refund:{oid}")
    elif st is OrderStatus.IN_PROGRESS:
        b.button(text="🏁 Выполнено", callback_data=f"ord:done:{oid}")
        b.button(text="↩️ Возврат", callback_data=f"ord:refund:{oid}")
    elif st is OrderStatus.REFUND_PENDING:
        b.button(text="✅ Вернул", callback_data=f"ord:refunded:{oid}")

    if st not in TERMINAL:
        b.button(text="💬 Написать", callback_data=f"ord:msg:{oid}")

    b.adjust(2)
    return b.as_markup()


def card(order: Order) -> str:
    """Карточка для админа. Всё пользовательское — через escape."""
    name =  escape(order.user.full_name or "клиент")
    handle = f" (@{escape(order.user.username)})" if order.user.username else ""
    lines = [
        f"<b>Заявка #{order.id}</b> — {TITLES[OrderStatus(order.status)]}",
        f"Сервис: {escape(order.service_title)}",
        f"К оплате: {order.amount_foreign} {order.currency}",
        f"Курс: {order.base_rate} → {order.client_rate} (+{order.markup_pct}%)",
        f"С клиента: <b>{order.total_rub} ₽</b>",
        f'Клиент: <a href="tg://user?id={order.user.tg_id}">{name}</a>{handle}',
    ]
    if order.payment_link:
        lines.append(f"Ссылка: {escape(order.payment_link)}")
    if order.client_note:
        lines.append(f"Коммент: {escape(order.client_note)}")
    if order.admin_note:
        lines.append(f"Заметка: {escape(order.admin_note)}")
    if order.amount_usd is not None and order.currency != "USD":
        lines.append(f"Тебе платить: <b>{order.amount_usd} USD</b>")
    if order.cost_rub is not None:
        lines.append(
            f"Себестоимость: {order.cost_rub} ₽ | Профит: {order.profit_rub} ₽"
        )
    return "\n".join(lines)


def order_line(order: Order) -> str:
    return (
        f"#{order.id} · {escape(order.service_title)} · "
        f"{order.amount_foreign} {order.currency} · "
        f"{order.total_rub} ₽ · {TITLES[OrderStatus(order.status)]}"
    )


def payment_text(order: Order, requisites: str) -> str:
    return (
        f"Заявка #{order.id} принята.\n\n"
        f"Переведи <b>ровно {order.total_rub} ₽</b>\n"
        f"{escape(requisites)}\n\n"
        f"Копейки в сумме — идентификатор платежа, не округляй.\n"
        f"Курс держится до {order.expires_at:%H:%M} UTC."
    )


def user_line(row) -> str:
    u = row.user
    name = escape(u.full_name or "без имени")
    handle = f" @{escape(u.username)}" if u.username else ""
    link = f'<a href="tg://user?id={u.tg_id}">{name}</a>'

    if row.done:
        tail = f"{row.done} опл. · {row.turnover} ₽"
    elif row.active:
        tail = f"⏳ {row.active} в работе"
    elif row.orders:
        tail = f"{row.orders} заявок, ни одной оплаты"
    else:
        tail = "только зашёл"

    return f"{link}{handle} — {tail}"