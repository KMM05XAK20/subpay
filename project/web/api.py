import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from aiogram import Bot
from aiohttp import web
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..config import settings
from ..models import Order, Rate, Service, User
from ..order_states import ACTIVE, TERMINAL, TITLES, OrderStatus
from ..pricing import calc_quote
from ..services import orders as svc
from ..services import workflow as wf
from ..services.rates import (
    SETTLE, all_rates, available_currencies, get_quote_input,
    set_cross, set_settle_rate,
)
from ..services.users import get_or_create_user, user_rows, user_totals
from .auth import validate_init_data

routes = web.RouteTableDef()


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        self.message, self.status = message, status


# ---------- middleware ----------

@web.middleware
async def api_middleware(request: web.Request, handler):
    if not request.path.startswith("/api/"):
        return await handler(request)

    try:
        header = request.headers.get("Authorization", "")
        tg = validate_init_data(header.removeprefix("tma "), settings.bot_token)
        if tg is None:
            raise ApiError("Откройте приложение из Telegram", 401)

        async with request.app["sessionmaker"]() as session:
            full_name = " ".join(
                p for p in (tg.get("first_name"), tg.get("last_name")) if p
            ) or None
            user = await get_or_create_user(
                session, tg["id"], tg.get("username"), full_name
            )
            if user.is_blocked:
                raise ApiError("Доступ закрыт", 403)
            request["session"] = session
            request["user"] = user
            request["is_admin"] = user.tg_id == settings.admin_id
            if request.path.startswith("/api/admin") and not request["is_admin"]:
                raise ApiError("Нет доступа", 403)
            return await handler(request)
    except ApiError as e:
        return web.json_response({"error": e.message}, status=e.status)
    except wf.ActionError as e:
        return web.json_response({"error": str(e)}, status=409)
    except web.HTTPException:
        raise
    except Exception:
        logging.exception("api error %s", request.path)
        return web.json_response({"error": "Внутренняя ошибка"}, status=500)


# ---------- helpers ----------

def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def dec(value, default: str | None = None) -> Decimal:
    try:
        d = Decimal(str(value).replace(",", ".").strip())
        if not d.is_finite():
            raise InvalidOperation
        return d
    except (InvalidOperation, AttributeError):
        raise ApiError(default or "Некорректное число")


async def body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise ApiError("Некорректный запрос")
    if not isinstance(data, dict):
        raise ApiError("Некорректный запрос")
    return data


def order_id(request: web.Request) -> int:
    try:
        return int(request.match_info["id"])
    except ValueError:
        raise ApiError("Некорректный номер", 404)


async def load_order(session, oid: int) -> Order | None:
    return await session.scalar(
        select(Order).where(Order.id == oid)
        .options(selectinload(Order.user), selectinload(Order.logs))
        .execution_options(populate_existing=True)
    )


def chat_of(order: Order) -> list[dict]:
    out = []
    for log in sorted(order.logs, key=lambda l: l.id):
        c = log.comment or ""
        if c.startswith(wf.TO_CLIENT):
            out.append({"from": "admin", "text": c[len(wf.TO_CLIENT):],
                        "at": iso(log.created_at)})
        elif c.startswith(wf.FROM_CLIENT):
            out.append({"from": "client", "text": c[len(wf.FROM_CLIENT):],
                        "at": iso(log.created_at)})
    return out


def timeline_of(order: Order) -> list[dict]:
    return [
        {"status": l.dst, "title": TITLES[OrderStatus(l.dst)], "at": iso(l.created_at),
         "comment": l.comment if l.actor != "client" or l.src != l.dst else None}
        for l in sorted(order.logs, key=lambda l: l.id) if l.src != l.dst
    ]


def order_brief(o: Order, admin: bool = False) -> dict:
    d = {
        "id": o.id, "status": o.status, "status_title": TITLES[OrderStatus(o.status)],
        "service": o.service_title, "amount_foreign": str(o.amount_foreign),
        "currency": o.currency, "total_rub": str(o.total_rub),
        "created_at": iso(o.created_at),
    }
    if admin:
        d["client"] = o.user.full_name or o.user.username or str(o.user.tg_id)
        d["paid_at"] = iso(o.paid_at)
    return d


def order_client(o: Order) -> dict:
    st = OrderStatus(o.status)
    d = order_brief(o)
    d.update({
        "client_rate": str(o.client_rate),
        "amount_rub": str(o.amount_rub),
        "pay_code": o.pay_code,
        "expires_at": iso(o.expires_at),
        "paid_at": iso(o.paid_at),
        "closed_at": iso(o.closed_at),
        "client_note": o.client_note,
        "reason": o.admin_note if st in (OrderStatus.REJECTED, OrderStatus.REFUND_PENDING,
                                         OrderStatus.REFUNDED) else None,
        "requisites": settings.requisites if st is OrderStatus.AWAITING_PAYMENT else None,
        "can_cancel": st in (OrderStatus.DRAFT, OrderStatus.AWAITING_PAYMENT),
        "can_reply": st not in TERMINAL or st is OrderStatus.DONE,
        "timeline": timeline_of(o),
        "chat": chat_of(o),
    })
    return d


def order_admin(o: Order) -> dict:
    st = OrderStatus(o.status)
    d = order_client(o)
    d.update({
        "requisites": None,
        "base_rate": str(o.base_rate), "markup_pct": str(o.markup_pct),
        "amount_usd": str(o.amount_usd) if o.amount_usd is not None else None,
        "cost_rub": str(o.cost_rub) if o.cost_rub is not None else None,
        "profit_rub": str(o.profit_rub) if o.profit_rub is not None else None,
        "admin_note": o.admin_note, "payment_link": o.payment_link,
        "client": {
            "id": o.user.id, "tg_id": o.user.tg_id, "name": o.user.full_name,
            "username": o.user.username,
        },
        "actions": actions_for(st),
        "can_reply": st not in TERMINAL or st is OrderStatus.DONE,
    })
    return d


def actions_for(st: OrderStatus) -> list[str]:
    return [a for a, allowed in wf.EXPECTED.items() if st in allowed]


async def quote_for(session, currency: str, amount: Decimal, service: Service | None):
    try:
        base, _ = await get_quote_input(session, currency)
    except LookupError:
        raise ApiError("Курс для этой валюты пока не задан", 503)
    markup = (service.markup_pct if service and service.markup_pct is not None
              else Decimal(str(settings.default_markup_pct)))
    return calc_quote(amount, base, markup)


# ---------- client ----------

@routes.get("/api/me")
async def me(request: web.Request) -> web.Response:
    session, user = request["session"], request["user"]
    currencies = await available_currencies(session)
    rates = {}
    for cur in currencies:
        try:
            rate, _ = await quote_for(session, cur, Decimal(1), None)
            rates[cur] = str(rate)
        except ApiError:
            pass
    services = list(await session.scalars(
        select(Service).where(Service.is_active.is_(True)).order_by(Service.title)
    ))
    return web.json_response({
        "user": {"id": user.id, "name": user.full_name, "username": user.username},
        "is_admin": request["is_admin"],
        "support": settings.support_username,
        "ttl_minutes": settings.quote_ttl_minutes,
        "max_active": settings.max_active_orders,
        "rates": rates,
        "services": [{
            "id": s.id, "title": s.title, "currency": s.currency,
            "min": str(s.min_amount) if s.min_amount is not None else None,
            "max": str(s.max_amount) if s.max_amount is not None else None,
        } for s in services],
    })


@routes.post("/api/quote")
async def quote(request: web.Request) -> web.Response:
    session = request["session"]
    data = await body(request)
    amount = dec(data.get("amount"), "Введите сумму")
    if amount <= 0:
        raise ApiError("Сумма должна быть больше нуля")
    service = None
    if data.get("service_id"):
        service = await session.get(Service, int(data["service_id"]))
    currency = (service.currency if service else str(data.get("currency", "USD"))).upper()
    rate, total = await quote_for(session, currency, amount, service)
    return web.json_response({"rate": str(rate), "total": str(total), "currency": currency})


@routes.get("/api/orders")
async def my_orders(request: web.Request) -> web.Response:
    rows = await svc.user_orders(request["session"], request["user"], limit=50)
    return web.json_response({"orders": [order_brief(o) for o in rows]})


@routes.post("/api/orders")
async def create_order(request: web.Request) -> web.Response:
    session, user = request["session"], request["user"]
    data = await body(request)
    bot: Bot = request.app["bot"]

    if await svc.count_active(session, user) >= settings.max_active_orders:
        raise ApiError("Есть незакрытые заявки — сначала закроем их", 409)

    amount = dec(data.get("amount"), "Введите сумму").quantize(Decimal("0.01"))
    if amount <= 0:
        raise ApiError("Сумма должна быть больше нуля")

    service = None
    service_raw = None
    if data.get("service_id"):
        service = await session.get(Service, int(data["service_id"]))
        if service is None or not service.is_active:
            raise ApiError("Сервис недоступен", 404)
        if service.min_amount and amount < service.min_amount:
            raise ApiError(f"Минимум для {service.title}: {service.min_amount}")
        if service.max_amount and amount > service.max_amount:
            raise ApiError(f"Максимум для {service.title}: {service.max_amount}")
        currency = service.currency
    else:
        service_raw = str(data.get("service_raw") or "").strip()[:128]
        if not service_raw:
            raise ApiError("Напишите, что нужно оплатить")
        currency = str(data.get("currency") or "USD").upper()

    try:
        base, to_usd = await get_quote_input(session, currency)
    except LookupError:
        raise ApiError("Курс временно недоступен", 503)

    note = str(data.get("note") or "").strip()[:500] or None
    order = await svc.create_order(
        session, user, amount, base, to_usd, currency=currency,
        service=service, service_raw=service_raw, client_note=note,
    )
    await svc.submit(session, order)
    await session.commit()
    order = await load_order(session, order.id)
    await wf.notify_admin_new_order(bot, order)
    return web.json_response(order_client(order), status=201)


async def own_order(request: web.Request) -> Order:
    order = await load_order(request["session"], order_id(request))
    if order is None or order.user_id != request["user"].id:
        raise ApiError("Заявка не найдена", 404)
    return order


@routes.get("/api/orders/{id}")
async def get_my_order(request: web.Request) -> web.Response:
    return web.json_response(order_client(await own_order(request)))


@routes.post("/api/orders/{id}/cancel")
async def cancel_order(request: web.Request) -> web.Response:
    session = request["session"]
    order = await own_order(request)
    if OrderStatus(order.status) not in (OrderStatus.DRAFT, OrderStatus.AWAITING_PAYMENT):
        raise ApiError("Эту заявку уже нельзя отменить", 409)
    await svc.cancel(session, order)
    await session.commit()
    return web.json_response(order_client(await load_order(session, order.id)))


@routes.post("/api/orders/{id}/message")
async def client_message(request: web.Request) -> web.Response:
    session, user = request["session"], request["user"]
    order = await own_order(request)
    text = str((await body(request)).get("text") or "").strip()[:1000]
    if not text:
        raise ApiError("Пустое сообщение")
    await wf.forward_client_message(request.app["bot"], session, user, order, text)
    return web.json_response(order_client(await load_order(session, order.id)))


# ---------- admin ----------

@routes.get("/api/admin/dashboard")
async def dashboard(request: web.Request) -> web.Response:
    session = request["session"]
    now = datetime.now(timezone.utc)
    periods = {}
    for key, days in (("day", 1), ("week", 7), ("month", 30)):
        periods[key] = {
            k: str(v) for k, v in
            (await svc.stats_since(session, now - timedelta(days=days))).items()
        }

    counts = dict((await session.execute(
        select(Order.status, func.count()).where(Order.status.in_(ACTIVE))
        .group_by(Order.status)
    )).all())

    since = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    done = await session.scalars(
        select(Order).where(Order.status == OrderStatus.DONE, Order.closed_at >= since)
    )
    days = {(since + timedelta(days=i)).date(): Decimal(0) for i in range(7)}
    for o in done:
        d = o.closed_at.astimezone(timezone.utc).date()
        if d in days:
            days[d] += o.amount_rub
    totals = await user_totals(session)

    stuck = await svc.stuck_orders(session)
    rates = [{"currency": r.currency,
              "value": str(r.base_rate if r.currency == SETTLE else r.to_usd)}
             for r in await all_rates(session)
             if (r.base_rate if r.currency == SETTLE else r.to_usd) is not None]

    return web.json_response({
        "periods": periods,
        "counts": {s.value: counts.get(s.value, counts.get(s, 0)) for s in ACTIVE},
        "chart": [{"date": d.isoformat(), "value": str(v)} for d, v in days.items()],
        "users": totals,
        "stuck": len(stuck),
        "rates": rates,
    })


@routes.get("/api/admin/orders")
async def admin_orders(request: web.Request) -> web.Response:
    session = request["session"]
    scope = request.query.get("scope", "active")
    stmt = select(Order).options(selectinload(Order.user)).order_by(Order.id.desc()).limit(60)
    if scope == "active":
        stmt = stmt.where(Order.status.in_(ACTIVE))
    elif scope == "done":
        stmt = stmt.where(Order.status.in_(TERMINAL))
    elif scope in {s.value for s in OrderStatus}:
        stmt = stmt.where(Order.status == scope)
    rows = await session.scalars(stmt)
    return web.json_response({"orders": [order_brief(o, admin=True) for o in rows]})


async def admin_order(request: web.Request) -> Order:
    order = await load_order(request["session"], order_id(request))
    if order is None:
        raise ApiError("Заявка не найдена", 404)
    return order


@routes.get("/api/admin/orders/{id}")
async def admin_order_get(request: web.Request) -> web.Response:
    return web.json_response(order_admin(await admin_order(request)))


@routes.post("/api/admin/orders/{id}/action")
async def admin_order_action(request: web.Request) -> web.Response:
    session = request["session"]
    order = await admin_order(request)
    data = await body(request)
    action = str(data.get("action", ""))
    admin_id = request["user"].tg_id
    bot: Bot = request.app["bot"]

    if action == "msg":
        text = str(data.get("text") or "").strip()[:1000]
        if not text:
            raise ApiError("Пустое сообщение")
        await wf.send_to_client(bot, session, order, admin_id, text)
        toast = "Отправлено"
    else:
        cost = dec(data["cost"]) if data.get("cost") not in (None, "") else None
        toast = await wf.perform(
            session, bot, order, action, admin_id,
            reason=str(data.get("reason") or "") or None, cost=cost,
        )
    order = await load_order(session, order.id)
    return web.json_response({"toast": toast, "order": order_admin(order)})


@routes.get("/api/admin/users")
async def admin_users(request: web.Request) -> web.Response:
    session = request["session"]
    cold = request.query.get("cold") == "1"
    rows = await user_rows(session, only_cold=cold, limit=60)
    return web.json_response({
        "totals": await user_totals(session),
        "users": [{
            "id": r.user.id, "tg_id": r.user.tg_id, "name": r.user.full_name,
            "username": r.user.username, "blocked": r.user.is_blocked,
            "orders": r.orders, "done": r.done, "turnover": str(r.turnover),
            "active": r.active, "last_seen": iso(r.last_seen),
            "joined": iso(r.user.created_at),
        } for r in rows],
    })


@routes.post("/api/admin/users/{id}/block")
async def admin_block(request: web.Request) -> web.Response:
    session = request["session"]
    user = await session.get(User, order_id(request))
    if user is None:
        raise ApiError("Не найден", 404)
    if user.tg_id == settings.admin_id:
        raise ApiError("Себя блокировать нельзя")
    user.is_blocked = bool((await body(request)).get("blocked"))
    await session.commit()
    return web.json_response({"blocked": user.is_blocked})


@routes.get("/api/admin/rates")
async def admin_rates(request: web.Request) -> web.Response:
    rows = await all_rates(request["session"])
    return web.json_response({
        "markup": settings.default_markup_pct,
        "rates": [{
            "currency": r.currency,
            "value": str(r.base_rate if r.currency == SETTLE else r.to_usd)
            if (r.base_rate if r.currency == SETTLE else r.to_usd) is not None else None,
            "kind": "rub_per_usd" if r.currency == SETTLE else "usd_per_unit",
            "updated_at": iso(r.updated_at),
        } for r in rows],
    })


@routes.put("/api/admin/rates")
async def admin_rate_set(request: web.Request) -> web.Response:
    session = request["session"]
    data = await body(request)
    currency = str(data.get("currency", "")).strip().upper()
    if not (2 <= len(currency) <= 3 and currency.isalpha()):
        raise ApiError("Некорректная валюта")
    value = dec(data.get("value"), "Введите курс")
    if value <= 0:
        raise ApiError("Курс должен быть больше нуля")
    if currency == SETTLE:
        await set_settle_rate(session, value)
    else:
        await set_cross(session, currency, value)
    return await admin_rates(request)


def service_json(s: Service) -> dict:
    return {
        "id": s.id, "slug": s.slug, "title": s.title, "currency": s.currency,
        "markup_pct": str(s.markup_pct) if s.markup_pct is not None else None,
        "min_amount": str(s.min_amount) if s.min_amount is not None else None,
        "max_amount": str(s.max_amount) if s.max_amount is not None else None,
        "is_active": s.is_active, "note": s.note,
    }


def opt_dec(data: dict, key: str):
    v = data.get(key)
    return None if v in (None, "") else dec(v)


@routes.get("/api/admin/services")
async def admin_services(request: web.Request) -> web.Response:
    rows = await request["session"].scalars(select(Service).order_by(Service.title))
    return web.json_response({"services": [service_json(s) for s in rows]})


@routes.post("/api/admin/services")
async def admin_service_add(request: web.Request) -> web.Response:
    session = request["session"]
    data = await body(request)
    title = str(data.get("title") or "").strip()[:96]
    currency = str(data.get("currency") or "USD").strip().upper()[:3]
    if not title:
        raise ApiError("Укажите название")
    slug = str(data.get("slug") or "").strip().lower()[:48] or "-".join(title.lower().split())[:48]
    if await session.scalar(select(Service).where(Service.slug == slug)):
        raise ApiError("Такой сервис уже есть", 409)
    s = Service(slug=slug, title=title, currency=currency,
                markup_pct=opt_dec(data, "markup_pct"),
                min_amount=opt_dec(data, "min_amount"),
                max_amount=opt_dec(data, "max_amount"),
                note=str(data.get("note") or "") or None)
    session.add(s)
    await session.commit()
    return web.json_response(service_json(s), status=201)


@routes.patch("/api/admin/services/{id}")
async def admin_service_edit(request: web.Request) -> web.Response:
    session = request["session"]
    s = await session.get(Service, order_id(request))
    if s is None:
        raise ApiError("Не найден", 404)
    data = await body(request)
    if "title" in data and str(data["title"]).strip():
        s.title = str(data["title"]).strip()[:96]
    if "currency" in data:
        s.currency = str(data["currency"]).strip().upper()[:3] or s.currency
    for key in ("markup_pct", "min_amount", "max_amount"):
        if key in data:
            setattr(s, key, opt_dec(data, key))
    if "is_active" in data:
        s.is_active = bool(data["is_active"])
    if "note" in data:
        s.note = str(data["note"] or "") or None
    await session.commit()
    return web.json_response(service_json(s))
