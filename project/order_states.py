# order_states.py
from enum import StrEnum


class OrderStatus(StrEnum):
    DRAFT = "draft"                    # клиент вводит сумму/сервис
    REVIEW = "review"                  # ушла тебе на аппрув
    AWAITING_PAYMENT = "awaiting_payment"  # выданы реквизиты, тикает TTL
    PAID = "paid"                      # ты подтвердил зачисление рублей
    IN_PROGRESS = "in_progress"        # оплачиваешь сервис
    DONE = "done"

    EXPIRED = "expired"                # не оплатил в срок
    REJECTED = "rejected"              # ты отказал до оплаты
    CANCELED = "canceled"              # клиент отменил до оплаты
    REFUND_PENDING = "refund_pending"  # деньги пришли, но выполнить не смог
    REFUNDED = "refunded"


TERMINAL = {
    OrderStatus.DONE,
    OrderStatus.EXPIRED,
    OrderStatus.REJECTED,
    OrderStatus.CANCELED,
    OrderStatus.REFUNDED,
}

ACTIVE = (
    OrderStatus.DRAFT,
    OrderStatus.REVIEW,
    OrderStatus.AWAITING_PAYMENT,
    OrderStatus.PAID,
    OrderStatus.IN_PROGRESS,
    OrderStatus.REFUND_PENDING,
)

TITLES: dict[OrderStatus, str] = {
    OrderStatus.DRAFT: "черновик",
    OrderStatus.REVIEW: "на проверке",
    OrderStatus.AWAITING_PAYMENT: "ждём оплату",
    OrderStatus.PAID: "оплачена",
    OrderStatus.IN_PROGRESS: "в работе",
    OrderStatus.DONE: "выполнена",
    OrderStatus.EXPIRED: "истекла",
    OrderStatus.REJECTED: "отклонена",
    OrderStatus.CANCELED: "отменена",
    OrderStatus.REFUND_PENDING: "возврат",
    OrderStatus.REFUNDED: "возвращена",
}

TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.DRAFT: {OrderStatus.REVIEW, OrderStatus.CANCELED},
    OrderStatus.REVIEW: {OrderStatus.AWAITING_PAYMENT, OrderStatus.REJECTED},
    OrderStatus.AWAITING_PAYMENT: {
        OrderStatus.PAID,
        OrderStatus.EXPIRED,
        OrderStatus.CANCELED,
    },
    OrderStatus.PAID: {OrderStatus.IN_PROGRESS, OrderStatus.REFUND_PENDING},
    OrderStatus.IN_PROGRESS: {OrderStatus.DONE, OrderStatus.REFUND_PENDING},
    OrderStatus.REFUND_PENDING: {OrderStatus.REFUNDED, OrderStatus.IN_PROGRESS},
}


class TransitionError(Exception):
    pass


def check_transition(src: OrderStatus, dst: OrderStatus) -> None:
    if dst not in TRANSITIONS.get(src, set()):
        raise TransitionError(f"{src} -> {dst} запрещён")