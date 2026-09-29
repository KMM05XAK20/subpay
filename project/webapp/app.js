/* SubPay Mini App — клиент и админ в одном приложении (без сборки). */
"use strict";

const tg = window.Telegram && Telegram.WebApp;
if (tg) { tg.ready(); tg.expand(); try { tg.setHeaderColor("secondary_bg_color"); } catch (_) {} }

const $ = (s, r = document) => r.querySelector(s);
const $app = $("#app"), $tabs = $("#tabs"), $sheet = $("#sheet"), $toast = $("#toast");

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v, d = 2) => Number(v).toLocaleString("ru-RU", { maximumFractionDigits: d });
const rub = (v) => `${num(v)} ₽`;
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (_) {} },
};
const haptic = (t = "light") => { try { tg.HapticFeedback.impactOccurred(t); } catch (_) {} };
const notifyH = (t) => { try { tg.HapticFeedback.notificationOccurred(t); } catch (_) {} };

function dt(iso) {
  if (!iso) return "—";
  const d = new Date(iso), n = new Date();
  const time = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === n.toDateString()) return `сегодня, ${time}`;
  const y = new Date(n - 864e5);
  if (d.toDateString() === y.toDateString()) return `вчера, ${time}`;
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) + `, ${time}`;
}

/* ---------- статусы ---------- */
const ST = {
  draft: ["📝", "Черновик", ""], review: ["🕐", "На проверке", "info"],
  awaiting_payment: ["💳", "Ждём оплату", "warn"], paid: ["💰", "Оплачена", "violet"],
  in_progress: ["⚙️", "В работе", "violet"], done: ["✅", "Выполнена", "ok"],
  expired: ["⌛", "Истекла", ""], rejected: ["⛔", "Отклонена", "bad"],
  canceled: ["✖️", "Отменена", ""], refund_pending: ["↩️", "Возврат", "warn"],
  refunded: ["↩️", "Возвращена", ""],
};
const pill = (s) => { const [i, t, c] = ST[s] || ["", s, ""]; return `<span class="pill ${c}">${i} ${t}</span>`; };
const TERMINAL = ["done", "expired", "rejected", "canceled", "refunded"];

/* ---------- API ---------- */
async function api(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: { Authorization: "tma " + (tg ? tg.initData : ""), "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (_) { throw new Error("Нет соединения"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Ошибка " + res.status);
  return data;
}
const get = (p) => api("GET", p), post = (p, b) => api("POST", p, b || {});

function toast(text, bad) {
  $toast.textContent = text; $toast.className = "toast" + (bad ? " bad" : ""); $toast.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => ($toast.hidden = true), 2400);
  notifyH(bad ? "error" : "success");
}

/* ---------- состояние и роутинг ---------- */
const S = { me: null, mode: "client", stack: [], view: null, params: {}, cache: {} };

function go(view, params = {}, push = true) {
  if (push && S.view) S.stack.push([S.view, S.params]);
  S.view = view; S.params = params;
  window.scrollTo(0, 0);
  render();
}
function back() {
  const prev = S.stack.pop();
  if (prev) { S.view = prev[0]; S.params = prev[1]; render(); }
  else home();
}
const ROOTS = ["home", "orders", "more", "a_dash", "a_orders", "a_users", "a_settings"];
function home() { S.stack = []; S.view = S.mode === "admin" ? "a_dash" : "home"; S.params = {}; render(); }

function setTabs() {
  const client = [["home", "🏠", "Главная"], ["orders", "📂", "Заявки"], ["more", "☰", "Ещё"]];
  const admin = [["a_dash", "📊", "Обзор"], ["a_orders", "🧾", "Заявки"], ["a_users", "👥", "Клиенты"], ["a_settings", "⚙️", "Настройки"]];
  const tabs = S.mode === "admin" ? admin : client;
  const show = ROOTS.includes(S.view);
  $tabs.hidden = !show;
  $tabs.innerHTML = tabs.map(([v, i, t]) =>
    `<button data-tab="${v}" class="${S.view === v ? "on" : ""}"><span class="i">${i}</span>${t}` +
    `${v === "a_orders" && S.badge ? `<span class="dot">${S.badge}</span>` : ""}</button>`).join("");
  if (tg) {
    if (show) tg.BackButton.hide(); else tg.BackButton.show();
  }
}

const skeleton = (n = 3) => Array.from({ length: n }, () => '<div class="skel"></div>').join("");

async function render() {
  clearInterval(render.timer);
  const v = S.view;
  const fn = VIEWS[v];
  setTabs();
  const token = (render.token = (render.token || 0) + 1);
  $app.innerHTML = topbar() + skeleton();
  try {
    const html = await fn(S.params);
    if (token !== render.token) return;
    $app.innerHTML = topbar() + html;
    $app.style.animation = "none"; void $app.offsetWidth; $app.style.animation = "";
    if (fn.after) fn.after(S.params);
    startTimers();
  } catch (e) {
    if (token !== render.token) return;
    $app.innerHTML = topbar() + `<div class="empty"><div class="ico">😕</div>${esc(e.message)}` +
      `<div class="mt"><button class="btn ghost sm" data-act="reload">Повторить</button></div></div>`;
  }
}

function topbar() {
  if (!S.me) return "";
  const isRoot = ROOTS.includes(S.view);
  const left = isRoot ? `<span class="sub">${S.mode === "admin" ? "🛠 Панель управления" : "SubPay"}</span>`
    : `<button class="back" data-act="back">‹ Назад</button>`;
  const sw = S.me.is_admin && isRoot
    ? `<button class="mode" data-act="mode">${S.mode === "admin" ? "Как клиент" : "Админка"}</button>` : "";
  return `<div class="topbar">${left}${sw}</div>`;
}

/* ---------- таймеры ---------- */
function startTimers() {
  const tick = () => document.querySelectorAll("[data-until]").forEach((el) => {
    const left = Math.floor((new Date(el.dataset.until) - Date.now()) / 1000);
    if (left <= 0) { el.textContent = "истёк"; return; }
    const m = Math.floor(left / 60), s = left % 60;
    el.textContent = `${m}:${String(s).padStart(2, "0")}`;
  });
  tick(); render.timer = setInterval(tick, 1000);
}

/* ---------- шторка ---------- */
function sheet(html, onMount) {
  $sheet.innerHTML = `<div class="sheet"><div class="grab"></div>${html}</div>`;
  $sheet.hidden = false;
  if (onMount) onMount($sheet);
}
const closeSheet = () => { $sheet.hidden = true; $sheet.innerHTML = ""; };
$sheet.addEventListener("click", (e) => { if (e.target === $sheet) closeSheet(); });

function prompt(title, fields, submitLabel, onSubmit, danger) {
  const f = fields.map((x) => x.area
    ? `<label class="field"><span>${esc(x.label)}</span><textarea class="input" name="${x.name}" placeholder="${esc(x.ph || "")}"></textarea></label>`
    : `<label class="field"><span>${esc(x.label)}</span><input class="input" name="${x.name}" inputmode="${x.mode || "text"}" placeholder="${esc(x.ph || "")}" value="${esc(x.value || "")}"></label>`).join("");
  sheet(`<h3>${esc(title)}</h3><form class="stack">${f}<div class="err" id="perr"></div>` +
    `<button class="btn ${danger ? "danger" : ""}" type="submit">${esc(submitLabel)}</button></form>`, (root) => {
    const form = $("form", root);
    const first = form.querySelector("input,textarea"); if (first) setTimeout(() => first.focus(), 250);
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const vals = Object.fromEntries(new FormData(form));
      const btn = $("button[type=submit]", form); btn.disabled = true;
      try { await onSubmit(vals); closeSheet(); }
      catch (e) { $("#perr").textContent = e.message; btn.disabled = false; }
    });
  });
}

/* ================= КЛИЕНТ ================= */
const STEPS = [["review", "Проверка"], ["awaiting_payment", "Оплата"], ["work", "Работа"], ["done", "Готово"]];
function stepper(status) {
  const idx = { draft: 0, review: 0, awaiting_payment: 1, paid: 2, in_progress: 2, done: 3, refund_pending: 2 }[status];
  if (idx === undefined) return "";
  return `<div class="steps">` + STEPS.map(([, t], i) =>
    `${i ? `<div class="bar ${i <= idx ? "done" : ""}"></div>` : ""}` +
    `<div class="step ${i < idx || status === "done" ? "done" : i === idx ? "cur" : ""}"><i>${i < idx || status === "done" ? "✓" : i + 1}</i>${t}</div>`
  ).join("") + `</div>`;
}

function orderCard(o, admin) {
  return `<div class="card tap" data-act="open" data-id="${o.id}">
    <div class="list-item"><div class="avatar">${(ST[o.status] || ["🧾"])[0]}</div>
    <div class="grow"><div class="row between"><span class="b ell">${esc(o.service)}</span><span class="b nowrap">${rub(o.total_rub)}</span></div>
    <div class="row between mt-s"><span class="sub ell">#${o.id} · ${num(o.amount_foreign)} ${esc(o.currency)}${admin ? " · " + esc(o.client) : ""}</span>${pill(o.status)}</div></div></div></div>`;
}
const emptyBox = (ico, text) => `<div class="empty"><div class="ico">${ico}</div>${text}</div>`;

async function vHome() {
  const [orders] = await Promise.all([get("/api/orders")]);
  const active = orders.orders.filter((o) => !TERMINAL.includes(o.status));
  const rates = Object.entries(S.me.rates);
  const name = esc((S.me.user.name || "").split(" ")[0] || "друг");
  return `
  <div class="hero"><h2>Привет, ${name} 👋</h2>
    <p>Оплачиваем зарубежные сервисы, которые не проходят российской картой.</p>
    <button class="btn" data-act="new">＋ Новая заявка</button></div>
  ${active.length ? `<div class="section-title">Активные заявки</div>` + active.map((o) => orderCard(o)).join("") : ""}
  <div class="section-title">Калькулятор</div>
  <div class="card stack">
    <label class="field"><span>Сумма</span><input class="input amount" id="c-amount" inputmode="decimal" placeholder="20"></label>
    <div class="chips" id="c-cur">${rates.map(([c], i) => `<button class="chip ${i === 0 ? "on" : ""}" data-cur="${c}">${c}</button>`).join("")}</div>
    <div id="c-out" class="sub">Введите сумму — посчитаю в рублях</div>
  </div>
  ${rates.length ? `<div class="section-title">Курсы</div><div class="card"><div class="grid3">${rates.map(([c, r]) =>
    `<div><div class="sub">1 ${c}</div><div class="b">${num(r)} ₽</div></div>`).join("")}</div></div>` : ""}
  <div class="section-title">Как это работает</div>
  <div class="card stack">
    ${[["1", "Создаёте заявку", "Сервис и сумма в валюте"], ["2", "Проверяю и присылаю реквизиты", "Курс фиксируется на " + S.me.ttl_minutes + " минут"],
       ["3", "Переводите точную сумму", "Копейки — идентификатор платежа"], ["4", "Оплачиваю сервис", "Вы получаете уведомление"]]
      .map(([n, t, s]) => `<div class="list-item"><div class="avatar">${n}</div><div><div class="b">${t}</div><div class="sub">${s}</div></div></div>`).join("")}
  </div>`;
}
vHome.after = () => {
  const amount = $("#c-amount"), out = $("#c-out");
  if (!amount) return;
  let cur = ($("#c-cur .on") || {}).dataset?.cur, t;
  const calc = () => {
    clearTimeout(t);
    const v = amount.value.replace(",", ".");
    if (!v || isNaN(v) || Number(v) <= 0) { out.className = "sub"; out.textContent = "Введите сумму — посчитаю в рублях"; return; }
    t = setTimeout(async () => {
      try {
        const q = await post("/api/quote", { amount: v, currency: cur });
        out.className = ""; out.innerHTML = `<span class="big">≈ ${rub(q.total)}</span><div class="sub">по курсу ${num(q.rate, 4)} ₽ за ${q.currency}</div>`;
      } catch (e) { out.className = "err"; out.textContent = e.message; }
    }, 250);
  };
  amount.addEventListener("input", calc);
  $("#c-cur").addEventListener("click", (e) => {
    const b = e.target.closest("[data-cur]"); if (!b) return;
    cur = b.dataset.cur; haptic();
    document.querySelectorAll("#c-cur .chip").forEach((c) => c.classList.toggle("on", c === b)); calc();
  });
};

async function vOrders(p) {
  const { orders } = await get("/api/orders");
  const filter = p.filter || "active";
  const list = orders.filter((o) => filter === "all" || !TERMINAL.includes(o.status));
  return `<h1 class="page-title">Мои заявки</h1>
    <div class="seg"><button data-act="cfilter" data-f="active" class="${filter === "active" ? "on" : ""}">Активные</button>
    <button data-act="cfilter" data-f="all" class="${filter === "all" ? "on" : ""}">Все</button></div><div class="spacer"></div><div class="mt">` +
    (list.length ? list.map((o) => orderCard(o)).join("") : emptyBox("🗂", "Заявок пока нет") +
      `<button class="btn" data-act="new">＋ Новая заявка</button>`) + `</div>`;
}

const N = { service: null, raw: "", cur: "USD" };
async function vNew() {
  const me = S.me;
  const svcs = me.services;
  const curs = Object.keys(me.rates);
  if (!curs.length) return emptyBox("💱", "Курсы пока не заданы — загляните чуть позже");
  return `<h1 class="page-title">Новая заявка</h1>
  <div class="card stack">
    <div><div class="sub" style="margin:0 4px 8px">Сервис</div>
    <div class="chips" id="n-svc">${svcs.map((s) => `<button class="chip ${N.service === s.id ? "on" : ""}" data-svc="${s.id}">${esc(s.title)}</button>`).join("")}
    <button class="chip ${N.service === 0 ? "on" : ""}" data-svc="0">Другое…</button></div></div>
    <div id="n-other" ${N.service === 0 ? "" : "hidden"} class="stack">
      <label class="field"><span>Что оплачиваем</span><input class="input" id="n-raw" maxlength="128" placeholder="Сервис и тариф" value="${esc(N.raw)}"></label>
      <div class="chips" id="n-cur">${curs.map((c) => `<button class="chip ${N.cur === c ? "on" : ""}" data-cur="${c}">${c}</button>`).join("")}</div></div>
    <label class="field"><span id="n-lbl">Сумма</span><input class="input amount" id="n-amount" inputmode="decimal" placeholder="19.99"></label>
    <div id="n-hint" class="sub"></div>
    <label class="field"><span>Комментарий (необязательно)</span><textarea class="input" id="n-note" maxlength="500" placeholder="Ссылка на счёт, e-mail аккаунта…"></textarea></label>
    <div id="n-quote" class="sub">Введите сумму — покажу стоимость в рублях</div>
    <div class="err" id="n-err"></div>
    <button class="btn" id="n-send" disabled>Отправить заявку</button>
    <div class="sub" style="text-align:center">Точную сумму с копейками пришлю после подтверждения</div>
  </div>`;
}
vNew.after = () => {
  const me = S.me, amount = $("#n-amount");
  if (!amount) return;
  let t;
  const svc = () => me.services.find((s) => s.id === N.service);
  const currency = () => (svc() ? svc().currency : N.cur);
  const valid = () => (N.service === null ? false : N.service === 0 ? !!N.raw.trim() : true);
  const update = () => {
    const s = svc(), c = currency();
    $("#n-lbl").textContent = `Сумма в ${c}`;
    $("#n-hint").textContent = s && (s.min || s.max) ? `Лимиты: ${s.min ? "от " + num(s.min) : ""} ${s.max ? "до " + num(s.max) : ""}` : "";
    const v = amount.value.replace(",", ".");
    const ok = valid() && v && !isNaN(v) && Number(v) > 0;
    $("#n-send").disabled = !ok;
    clearTimeout(t);
    if (!ok) { $("#n-quote").className = "sub"; $("#n-quote").textContent = valid() ? "Введите сумму — покажу стоимость в рублях" : "Выберите сервис"; return; }
    t = setTimeout(async () => {
      try {
        const q = await post("/api/quote", { amount: v, currency: c, service_id: s ? s.id : undefined });
        $("#n-quote").className = ""; $("#n-quote").innerHTML = `К оплате: <span class="big">≈ ${rub(q.total)}</span><div class="sub">курс ${num(q.rate, 4)} ₽ за ${q.currency}</div>`;
      } catch (e) { $("#n-quote").className = "err"; $("#n-quote").textContent = e.message; }
    }, 250);
  };
  $("#n-svc").addEventListener("click", (e) => {
    const b = e.target.closest("[data-svc]"); if (!b) return; haptic();
    N.service = Number(b.dataset.svc);
    document.querySelectorAll("#n-svc .chip").forEach((c) => c.classList.toggle("on", c === b));
    $("#n-other").hidden = N.service !== 0; update();
  });
  $("#n-cur").addEventListener("click", (e) => {
    const b = e.target.closest("[data-cur]"); if (!b) return; haptic();
    N.cur = b.dataset.cur;
    document.querySelectorAll("#n-cur .chip").forEach((c) => c.classList.toggle("on", c === b)); update();
  });
  $("#n-raw").addEventListener("input", (e) => { N.raw = e.target.value; update(); });
  amount.addEventListener("input", update);
  $("#n-send").addEventListener("click", async () => {
    const btn = $("#n-send"); btn.disabled = true; $("#n-err").textContent = "";
    try {
      const o = await post("/api/orders", {
        service_id: N.service || undefined, service_raw: N.service === 0 ? N.raw : undefined,
        currency: currency(), amount: amount.value, note: $("#n-note").value,
      });
      N.service = null; N.raw = ""; notifyH("success");
      toast("Заявка отправлена");
      S.stack = []; S.view = "orders"; go("order", { id: o.id }, true);
    } catch (e) { $("#n-err").textContent = e.message; btn.disabled = false; notifyH("error"); }
  });
  update();
};

function copyText(text) {
  const done = () => { toast("Скопировано"); haptic(); };
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
  else fallbackCopy(text, done);
}
function fallbackCopy(text, done) {
  const ta = document.createElement("textarea"); ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.select(); try { document.execCommand("copy"); done(); } catch (_) {} ta.remove();
}

function chatBlock(o, mine) {
  const msgs = (o.chat || []).map((m) =>
    `<div class="msg ${m.from === mine ? "me" : "them"}">${esc(m.text)}<small>${dt(m.at)}</small></div>`).join("");
  return `<div class="section-title">Переписка</div><div class="card">
    ${msgs ? `<div class="chat">${msgs}</div>` : `<div class="sub">Сообщений пока нет</div>`}
    ${o.can_reply ? `<div class="composer"><input class="input" id="chat-in" maxlength="1000" placeholder="Сообщение…"><button class="btn" data-act="send" data-id="${o.id}" aria-label="Отправить">➤</button></div>` : ""}</div>`;
}
function timelineBlock(o) {
  const items = (o.timeline || []).slice().reverse();
  if (!items.length) return "";
  return `<div class="section-title">История</div><div class="card"><div class="tl">` +
    items.map((t) => `<div class="tl-item"><div class="b">${esc(t.title)}</div><div class="sub">${dt(t.at)}${t.comment && t.status !== "draft" && !/^(создана)$/.test(t.comment) ? " · " + esc(t.comment) : ""}</div></div>`).join("") + `</div></div>`;
}

async function vOrder(p) {
  const o = await get("/api/orders/" + p.id);
  let banner = "";
  if (o.status === "rejected") banner = `<div class="banner bad mt">⛔ Заявка отклонена${o.reason ? ": " + esc(o.reason) : ""}</div>`;
  if (o.status === "expired") banner = `<div class="banner warn mt">⌛ Время на оплату вышло. Курс изменился — оформите заявку заново.</div>`;
  if (o.status === "refund_pending") banner = `<div class="banner warn mt">↩️ Оплатить сервис не получилось, оформляю возврат. Напишите реквизиты для возврата в чате ниже.</div>`;
  if (o.status === "done") banner = `<div class="banner ok mt">✅ Готово! Сервис оплачен.</div>`;
  const pay = o.status === "awaiting_payment" ? `
    <div class="card pay mt stack">
      <div class="row between"><span class="b">Переведите ровно</span><span class="sub">осталось <span class="timer" data-until="${esc(o.expires_at)}"></span></span></div>
      <div class="row between"><span class="amount">${num(o.total_rub)} ₽</span><span class="copy" data-act="copy" data-v="${Number(o.total_rub).toFixed(2)}">копировать</span></div>
      <div class="req">${esc(o.requisites)}</div>
      <button class="btn ghost sm" data-act="copy" data-v="${esc(o.requisites)}">Скопировать реквизиты</button>
      <div class="sub">Копейки — идентификатор вашего платежа, не округляйте.</div>
    </div>` : "";
  return `<h1 class="page-title">Заявка #${o.id}</h1>
    <div class="card"><div class="row between"><span class="b">${esc(o.service)}</span>${pill(o.status)}</div>
      <div class="mt">${stepper(o.status)}</div>${banner}</div>
    ${pay}
    <div class="section-title">Детали</div><div class="card">
      <div class="kv"><span>Сумма</span><span>${num(o.amount_foreign)} ${esc(o.currency)}</span></div>
      <div class="kv"><span>Курс</span><span>${num(o.client_rate, 4)} ₽</span></div>
      <div class="kv"><span>${o.status === "review" || o.status === "draft" ? "Ориентировочно" : "К оплате"}</span><span class="b">${rub(o.status === "review" || o.status === "draft" ? o.amount_rub : o.total_rub)}</span></div>
      <div class="kv"><span>Создана</span><span>${dt(o.created_at)}</span></div>
      ${o.client_note ? `<div class="kv"><span>Комментарий</span><span>${esc(o.client_note)}</span></div>` : ""}</div>
    ${chatBlock(o, "client")}
    ${timelineBlock(o)}
    ${o.can_cancel ? `<div class="mt"><button class="btn danger" data-act="cancel" data-id="${o.id}">Отменить заявку</button></div>` : ""}`;
}

async function vMore() {
  return `<h1 class="page-title">Ещё</h1>
  <div class="card stack">
    <a class="list-item" href="https://t.me/${esc(S.me.support)}"><div class="avatar">💬</div><div class="grow"><div class="b">Написать в поддержку</div><div class="sub">@${esc(S.me.support)}</div></div><span class="chev">›</span></a>
  </div>
  <div class="section-title">Правила</div>
  <div class="card stack">
    ${[["📈", "Курс", `Фиксируется на ${S.me.ttl_minutes} минут после подтверждения заявки. Не успели оплатить — оформляем заново.`],
       ["💳", "Оплата", "Переводите ровно ту сумму, что пришлю, включая копейки — по ним нахожу ваш платёж."],
       ["↩️", "Возврат", "Если оплатить сервис не получилось — возвращаю всю сумму в течение суток."],
       ["🔐", "Доступы", "Пароли и коды от аккаунтов не запрашиваю. Если просят от моего имени — это не я."],
       ["⏱", "Сроки", "Отвечаю на заявку в течение часа, оплачиваю в тот же день."]]
      .map(([i, t, s]) => `<div class="list-item"><div class="avatar">${i}</div><div><div class="b">${t}</div><div class="sub">${s}</div></div></div>`).join("")}
  </div>`;
}

/* ================= АДМИН ================= */
const ATT = [["review", "На проверке"], ["awaiting_payment", "Ждут оплату"], ["paid", "Оплачены"], ["in_progress", "В работе"], ["refund_pending", "Возврат"]];

async function vDash(p) {
  const d = await get("/api/admin/dashboard");
  S.badge = d.counts.review + d.counts.paid || 0;
  setTabs();
  const per = p.per || "day", P = d.periods[per];
  const max = Math.max(1, ...d.chart.map((c) => Number(c.value)));
  const dow = (s) => new Date(s + "T12:00").toLocaleDateString("ru-RU", { weekday: "short" });
  return `<h1 class="page-title">Обзор</h1>
  ${d.stuck ? `<div class="alert" data-act="afilter" data-f="stuck"><span style="font-size:22px">⏰</span><div><div class="b">Зависших заявок: ${d.stuck}</div><div class="sub">Клиент заплатил, заявка не закрыта</div></div></div><div class="spacer"></div>` : ""}
  <div class="grid3">${ATT.map(([k, t]) => `<div class="card tile ${(k === "review" || k === "paid") && d.counts[k] ? "hot" : ""}" data-act="afilter" data-f="${k}"><div class="n">${d.counts[k] || 0}</div><div class="t">${t}</div></div>`).join("")}
    <div class="card tile" data-act="tab" data-v="a_users"><div class="n">${d.users.total}</div><div class="t">Клиентов</div></div></div>
  <div class="section-title">Итоги</div>
  <div class="seg" style="margin-bottom:10px">${[["day", "24 часа"], ["week", "7 дней"], ["month", "30 дней"]].map(([k, t]) => `<button data-act="per" data-p="${k}" class="${per === k ? "on" : ""}">${t}</button>`).join("")}</div>
  <div class="grid3"><div class="card kpi"><div class="lbl">Заявок</div><div class="num">${P.count}</div></div>
    <div class="card kpi"><div class="lbl">Оборот</div><div class="num">${num(P.turnover, 0)} ₽</div></div>
    <div class="card kpi"><div class="lbl">Профит</div><div class="num" style="color:var(--ok)">${num(P.profit, 0)} ₽</div></div></div>
  <div class="section-title">Оборот за 7 дней</div>
  <div class="card"><div class="chart">${d.chart.map((c) => `<div class="col"><span>${Number(c.value) ? num(Number(c.value) / 1000, 1) + "k" : ""}</span><div style="height:${Math.max(3, Number(c.value) / max * 78)}%"></div>${dow(c.date)}</div>`).join("")}</div></div>
  <div class="section-title">Курсы</div>
  <div class="card row" style="flex-wrap:wrap;gap:16px">${d.rates.map((r) => `<div><div class="sub">${r.currency}</div><div class="b">${num(r.value, 4)}${r.currency === "USD" ? " ₽" : ""}</div></div>`).join("") || "не заданы"}
    <button class="btn ghost sm" data-act="tab" data-v="a_settings" style="margin-left:auto">Изменить</button></div>
  <div class="section-title">Клиенты</div>
  <div class="card row between"><span>Всего ${d.users.total}</span><span>С оплатами ${d.users.buyers}</span></div>`;
}

const SCOPES = [["active", "Активные"], ["review", "Проверка"], ["awaiting_payment", "Оплата"], ["paid", "Оплачены"], ["in_progress", "В работе"], ["refund_pending", "Возвраты"], ["done", "Архив"]];
async function vAOrders(p) {
  const scope = p.scope || "active";
  const { orders } = await get("/api/admin/orders?scope=" + scope);
  return `<h1 class="page-title">Заявки</h1>
   <div class="chips scroll">${SCOPES.map(([k, t]) => `<button class="chip ${scope === k ? "on" : ""}" data-act="ascope" data-s="${k}">${t}</button>`).join("")}</div>
   <div class="mt">${orders.length ? orders.map((o) => orderCard(o, true)).join("") : emptyBox("🎉", "Здесь пусто")}</div>`;
}

const ACTIONS = {
  approve: ["✅ Принять", "ok"], reject: ["❌ Отказ", "danger"], paid: ["💰 Деньги пришли", "ok"], expire: ["⌛ Протухла", "ghost"],
  work: ["▶️ В работу", ""], done: ["🏁 Выполнено", "ok"], refund: ["↩️ Возврат", "danger"], refunded: ["✅ Вернул", "ok"],
};
async function vAOrder(p) {
  const o = await get("/api/admin/orders/" + p.id);
  const c = o.client, name = esc(c.name || "клиент");
  return `<h1 class="page-title">Заявка #${o.id}</h1>
  <div class="card"><div class="row between"><span class="b">${esc(o.service)}</span>${pill(o.status)}</div>
    <div class="mt">${stepper(o.status)}</div></div>
  ${o.actions.length ? `<div class="btn-row mt">${o.actions.map((a) => `<button class="btn ${ACTIONS[a][1]}" data-act="do" data-a="${a}" data-id="${o.id}">${ACTIONS[a][0]}</button>`).join("")}</div>` : ""}
  <div class="section-title">Клиент</div>
  <div class="card list-item"><div class="avatar">${esc((c.name || "?")[0])}</div><div class="grow"><div class="b">${name}</div>
    <div class="sub">${c.username ? "@" + esc(c.username) : "без username"}</div></div>
    <a class="btn ghost sm" href="tg://user?id=${c.tg_id}">Открыть</a></div>
  <div class="section-title">Расчёт</div><div class="card">
    <div class="kv"><span>Сумма</span><span>${num(o.amount_foreign)} ${esc(o.currency)}</span></div>
    ${o.amount_usd && o.currency !== "USD" ? `<div class="kv"><span>Тебе платить</span><span class="b">${num(o.amount_usd)} USD</span></div>` : ""}
    <div class="kv"><span>Курс</span><span>${num(o.base_rate, 4)} → ${num(o.client_rate, 4)} (+${num(o.markup_pct)}%)</span></div>
    <div class="kv"><span>С клиента</span><span class="b">${rub(o.total_rub)}</span></div>
    ${o.cost_rub ? `<div class="kv"><span>Себестоимость</span><span>${rub(o.cost_rub)}</span></div><div class="kv"><span>Профит</span><span class="b" style="color:var(--ok)">${rub(o.profit_rub)}</span></div>` : ""}
    <div class="kv"><span>Создана</span><span>${dt(o.created_at)}</span></div>
    ${o.paid_at ? `<div class="kv"><span>Оплачена</span><span>${dt(o.paid_at)}</span></div>` : ""}
    ${o.client_note ? `<div class="kv"><span>Комментарий</span><span>${esc(o.client_note)}</span></div>` : ""}
    ${o.admin_note ? `<div class="kv"><span>Заметка</span><span>${esc(o.admin_note)}</span></div>` : ""}</div>
  ${chatBlock(o, "admin")}
  ${timelineBlock(o)}`;
}

async function vAUsers(p) {
  const cold = !!p.cold;
  const d = await get("/api/admin/users" + (cold ? "?cold=1" : ""));
  return `<h1 class="page-title">Клиенты</h1>
  <div class="seg"><button data-act="ucold" data-c="0" class="${cold ? "" : "on"}">Все · ${d.totals.total}</button><button data-act="ucold" data-c="1" class="${cold ? "on" : ""}">Без оплат</button></div>
  <div class="mt">${d.users.map((u) => `<div class="card tap" data-act="user" data-id="${u.id}" data-b="${u.blocked ? 1 : 0}" data-n="${esc(u.name || u.username || u.tg_id)}">
    <div class="list-item"><div class="avatar">${esc((u.name || "?")[0])}</div><div class="grow">
    <div class="row between"><span class="b ell">${esc(u.name || "Без имени")}</span>${u.blocked ? '<span class="pill bad">блок</span>' : u.active ? `<span class="pill warn">⏳ ${u.active}</span>` : ""}</div>
    <div class="sub ell">${u.username ? "@" + esc(u.username) + " · " : ""}${u.done ? `${u.done} опл. · ${rub(u.turnover)}` : u.orders ? `${u.orders} заявок, без оплат` : "только зашёл"}</div></div></div></div>`).join("") || emptyBox("👥", "Пока никого")}</div>`;
}

async function vASettings() {
  const [r, s] = await Promise.all([get("/api/admin/rates"), get("/api/admin/services")]);
  return `<h1 class="page-title">Настройки</h1>
  <div class="section-title">Курсы</div>
  <div class="card">${r.rates.map((x) => `<div class="kv tap" data-act="rate" data-c="${x.currency}" data-v="${x.value ?? ""}" style="cursor:pointer"><span>${x.currency}${x.kind === "rub_per_usd" ? " · ₽ за доллар" : " · USD за единицу"}</span><span class="b">${x.value ? num(x.value, 4) : "—"} ✏️</span></div>`).join("") || '<div class="sub">курсы не заданы</div>'}
    <div class="mt"><button class="btn ghost sm" data-act="rate" data-c="" data-v="">＋ Валюта</button></div>
    <div class="sub mt-s">Наценка по умолчанию: ${num(r.markup)}%</div></div>
  <div class="section-title">Каталог сервисов</div>
  ${s.services.map((x) => `<div class="card tap" data-act="svcedit" data-id="${x.id}" data-j='${esc(JSON.stringify(x))}'>
    <div class="row between"><div class="grow"><div class="b ell">${esc(x.title)}</div><div class="sub">${esc(x.currency)}${x.markup_pct ? " · +" + num(x.markup_pct) + "%" : ""}${x.min_amount ? " · от " + num(x.min_amount) : ""}${x.max_amount ? " · до " + num(x.max_amount) : ""}</div></div>
    ${x.is_active ? '<span class="pill ok">вкл</span>' : '<span class="pill">выкл</span>'}</div></div>`).join("")}
  <div class="mt"><button class="btn" data-act="svcadd">＋ Добавить сервис</button></div>`;
}

/* ---------- маршруты ---------- */
const VIEWS = { home: vHome, orders: vOrders, more: vMore, new: vNew, order: vOrder,
  a_dash: vDash, a_orders: vAOrders, a_users: vAUsers, a_settings: vASettings, a_order: vAOrder };
for (const k of ["a_dash", "a_orders", "a_users", "a_settings", "a_order"]) if (!VIEWS[k].after) VIEWS[k].after = null;
const openOrder = (id) => go(S.mode === "admin" ? "a_order" : "order", { id });

/* ---------- обработчики ---------- */
async function adminDo(id, action, extra = {}) {
  const r = await post(`/api/admin/orders/${id}/action`, { action, ...extra });
  toast(r.toast); render(); return r;
}

document.addEventListener("click", async (e) => {
  const tab = e.target.closest("[data-tab]");
  if (tab) { haptic(); S.stack = []; S.view = tab.dataset.tab; S.params = {}; render(); return; }
  const el = e.target.closest("[data-act]"); if (!el) return;
  const d = el.dataset, act = d.act;
  haptic();
  try {
    switch (act) {
      case "back": back(); break;
      case "reload": render(); break;
      case "mode": S.mode = S.mode === "admin" ? "client" : "admin"; store.set("mode", S.mode); home(); break;
      case "new": go("new"); break;
      case "open": openOrder(d.id); break;
      case "tab": S.stack = []; go(d.v, {}, false); break;
      case "cfilter": go("orders", { filter: d.f }, false); break;
      case "copy": copyText(d.v); break;
      case "cancel": if (!confirm("Отменить заявку?")) break; await post(`/api/orders/${d.id}/cancel`); toast("Заявка отменена"); render(); break;
      case "send": {
        const inp = $("#chat-in"); const text = inp.value.trim(); if (!text) break;
        el.disabled = true;
        if (S.mode === "admin") await adminDo(d.id, "msg", { text });
        else { await post(`/api/orders/${d.id}/message`, { text }); toast("Отправлено"); render(); }
        break;
      }
      case "per": go("a_dash", { per: d.p }, false); break;
      case "afilter": S.stack = []; S.view = "a_orders"; S.params = { scope: d.f === "stuck" ? "active" : d.f }; render(); break;
      case "ascope": go("a_orders", { scope: d.s }, false); break;
      case "ucold": go("a_users", { cold: d.c === "1" }, false); break;
      case "do": {
        const id = d.id, a = d.a;
        if (a === "reject") prompt("Причина отказа", [{ name: "reason", label: "Клиент увидит это сообщение", area: true }], "Отклонить", (v) => adminDo(id, a, { reason: v.reason }), true);
        else if (a === "done") prompt("Заявка выполнена", [{ name: "cost", label: "Сколько реально ушло, ₽ (можно пропустить)", mode: "decimal", ph: "0" }], "Закрыть заявку", (v) => adminDo(id, a, { cost: v.cost }));
        else if (a === "refund") { if (confirm("Оформить возврат?")) await adminDo(id, a); }
        else await adminDo(id, a);
        break;
      }
      case "user": {
        const blocked = d.b === "1";
        sheet(`<h3>${esc(d.n)}</h3><div class="stack"><button class="btn ${blocked ? "ok" : "danger"}" id="ublock">${blocked ? "Разблокировать" : "Заблокировать"}</button>
          <button class="btn ghost" onclick="closeSheet()">Закрыть</button></div>`, (root) => {
          $("#ublock", root).onclick = async () => {
            try { await post(`/api/admin/users/${d.id}/block`, { blocked: !blocked }); closeSheet(); toast(blocked ? "Разблокирован" : "Заблокирован"); render(); }
            catch (err) { toast(err.message, true); }
          };
        });
        break;
      }
      case "rate":
        prompt(d.c ? `Курс ${d.c}` : "Новая валюта",
          [...(d.c ? [] : [{ name: "currency", label: "Код валюты", ph: "EUR" }]),
           { name: "value", label: d.c === "USD" ? "Рублей за 1 USD" : "Долларов за 1 единицу", mode: "decimal", value: d.v }],
          "Сохранить", async (v) => { await api("PUT", "/api/admin/rates", { currency: d.c || v.currency, value: v.value }); toast("Сохранено"); await refreshMe(); render(); });
        break;
      case "svcadd":
        prompt("Новый сервис", [{ name: "title", label: "Название", ph: "Netflix" }, { name: "currency", label: "Валюта", value: "USD" },
          { name: "markup_pct", label: "Наценка, % (пусто — по умолчанию)", mode: "decimal" },
          { name: "min_amount", label: "Мин. сумма", mode: "decimal" }, { name: "max_amount", label: "Макс. сумма", mode: "decimal" }],
          "Добавить", async (v) => { await post("/api/admin/services", v); toast("Добавлено"); await refreshMe(); render(); });
        break;
      case "svcedit": {
        const s = JSON.parse(d.j);
        prompt(s.title, [{ name: "title", label: "Название", value: s.title }, { name: "markup_pct", label: "Наценка, %", mode: "decimal", value: s.markup_pct || "" },
          { name: "min_amount", label: "Мин. сумма", mode: "decimal", value: s.min_amount || "" }, { name: "max_amount", label: "Макс. сумма", mode: "decimal", value: s.max_amount || "" }],
          "Сохранить", async (v) => { await api("PATCH", `/api/admin/services/${s.id}`, v); toast("Сохранено"); await refreshMe(); render(); });
        const tgl = document.createElement("button");
        tgl.className = "btn ghost mt"; tgl.type = "button"; tgl.textContent = s.is_active ? "Выключить сервис" : "Включить сервис";
        tgl.onclick = async () => { await api("PATCH", `/api/admin/services/${s.id}`, { is_active: !s.is_active }); closeSheet(); toast("Готово"); await refreshMe(); render(); };
        $(".sheet form").appendChild(tgl);
        break;
      }
    }
  } catch (err) { toast(err.message, true); document.querySelectorAll("[disabled]").forEach((b) => (b.disabled = false)); }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.id === "chat-in") $('[data-act="send"]').click();
});
if (tg) tg.BackButton.onClick(back);

async function refreshMe() { S.me = await get("/api/me"); }

/* ---------- старт ---------- */
(async function init() {
  $app.innerHTML = skeleton();
  if (!tg || !tg.initData) {
    $app.innerHTML = emptyBox("📱", "Откройте это приложение из Telegram-бота");
    return;
  }
  try { await refreshMe(); }
  catch (e) { $app.innerHTML = emptyBox("😕", esc(e.message)); return; }
  S.mode = S.me.is_admin && store.get("mode") !== "client" ? "admin" : "client";
  home();
  const o = new URLSearchParams(location.search).get("o");
  if (o && /^\d+$/.test(o)) openOrder(o);
})();
