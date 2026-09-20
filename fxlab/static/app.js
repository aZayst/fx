// fxlab dashboard: plain JS, no build step.
// Pattern: REST for the initial state, WebSocket for "something changed" nudges.
"use strict";

const $ = (sel) => document.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const num = (v, dp = 2) => Number(v).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
const tag = (s) => `<span class="tag ${esc(s)}">${esc(s)}</span>`;

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? res.statusText));
  return data;
}

function say(el, text, isErr = false) { el.textContent = text; el.classList.toggle("err", isErr); }

// Debounce so a burst of events causes one refetch, not ten.
function debounced(fn, ms = 150) { let t; return () => { clearTimeout(t); t = setTimeout(fn, ms); }; }

// ---------------------------------------------------------------- quotes (pushed)
const lastMid = {};
function renderQuotes(quotes) {
  const body = $("#quotes tbody");
  for (const q of quotes) {
    let row = body.querySelector(`tr[data-sym="${q.symbol}"]`);
    if (!row) { row = document.createElement("tr"); row.dataset.sym = q.symbol; row.innerHTML = `<td>${esc(q.symbol)}</td><td class="num bid"></td><td class="num ask"></td>`; body.appendChild(row); }
    const prev = lastMid[q.symbol], mid = Number(q.mid);
    row.querySelector(".bid").textContent = q.bid; row.querySelector(".ask").textContent = q.ask;
    if (prev !== undefined && prev !== mid) row.className = mid > prev ? "up" : "down";
    lastMid[q.symbol] = mid;
  }
}

// ---------------------------------------------------------------- ticket
async function loadStatic() {
  const [instruments, accounts] = await Promise.all([api("/api/instruments"), api("/api/accounts")]);
  $("#ticket [name=symbol]").innerHTML = instruments.map((i) => `<option>${esc(i.symbol)}</option>`).join("");
  $("#ticket [name=account_id]").innerHTML = accounts.map((a) => `<option value="${esc(a.id)}">${esc(a.id)}</option>`).join("");
}
$("#ticket [name=order_type]").addEventListener("change", (e) => { $("#ticket [name=limit_price]").disabled = e.target.value !== "LIMIT"; });
$("#ticket").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  const body = { account_id: f.account_id, symbol: f.symbol, side: f.side, quantity: f.quantity, order_type: f.order_type };
  if (f.order_type === "LIMIT") body.limit_price = f.limit_price;
  try {
    const o = await api("/api/orders", { method: "POST", body });
    say($("#order-msg"), `Order #${o.id} ${o.status}${o.fill_price ? ` @ ${o.fill_price}` : ""}${o.reject_reason ? ` — ${o.reject_reason}` : ""}`, o.status === "REJECTED");
  } catch (err) { say($("#order-msg"), err.message, true); }
});

// ---------------------------------------------------------------- REST-backed panels
async function refreshOrders() {
  const open = await api("/api/orders?status=OPEN");
  $("#open-orders tbody").innerHTML = open.length ? open.map((o) =>
    `<tr><td>#${o.id}</td><td>${esc(o.symbol)}</td><td>${esc(o.side)}</td><td class="num">${num(o.quantity, 0)}</td><td class="num">${esc(o.limit_price)}</td>
     <td><button class="ghost" data-cancel="${o.id}">cancel</button></td></tr>`).join("") : `<tr><td class="empty">none</td></tr>`;
}
$("#open-orders").addEventListener("click", async (e) => {
  const id = e.target.dataset.cancel; if (!id) return;
  try { await api(`/api/orders/${id}`, { method: "DELETE" }); } catch (err) { say($("#order-msg"), err.message, true); }
});

async function refreshTrades() {
  const [trades, positions] = await Promise.all([api("/api/trades?limit=15"), api("/api/positions")]);
  $("#trades tbody").innerHTML = trades.length ? trades.map((t) =>
    `<tr><td>${esc(t.trade_ref)}</td><td>${esc(t.account_id)}</td><td>${esc(t.symbol)}</td><td>${esc(t.side)}</td>
     <td class="num">${num(t.quantity, 0)}</td><td class="num">${esc(t.price)}</td><td>${esc(t.value_date)}</td><td>${tag(t.status)}</td></tr>`).join("")
    : `<tr><td colspan="8" class="empty">no trades yet — send an order</td></tr>`;
  $("#positions tbody").innerHTML = positions.length ? positions.map((p) =>
    `<tr><td>${esc(p.account_id)}</td><td>${esc(p.symbol)}</td><td class="num">${num(p.net_base, 0)}</td><td class="num">${num(p.net_quote, 0)}</td>
     <td class="num">${esc(p.mid)}</td><td class="num ${Number(p.pnl_usd) >= 0 ? "up" : "down"}">${num(p.pnl_usd)}</td></tr>`).join("")
    : `<tr><td colspan="6" class="empty">flat</td></tr>`;
}

async function refreshMonitor() {
  const breaksOnly = $("#breaks-only").checked;
  const [rows, sum] = await Promise.all([api(`/api/monitoring/reconciliation?breaks_only=${breaksOnly}&limit=25`), api("/api/monitoring/summary")]);
  $("#summary").innerHTML =
    `<span class="stat ${sum.breaks ? "bad" : ""}"><b>${sum.breaks}</b>breaks</span>` +
    `<span class="stat ${sum.stuck ? "bad" : ""}"><b>${sum.stuck}</b>past SLA</span>` +
    `<span class="stat"><b>${sum.open_alerts}</b>open alerts</span>` +
    Object.entries(sum.trades_by_status).map(([s, n]) => `<span class="stat"><b>${n}</b>${esc(s)}</span>`).join("");

  const cps = [...new Set(Object.keys(sum.counterparties))];
  $("#recon thead").innerHTML = `<tr><th>Trade</th><th>Pair</th><th>Side</th><th class="num">Qty</th>${cps.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>`;
  const byTrade = new Map();
  for (const r of rows) { if (!byTrade.has(r.trade_id)) byTrade.set(r.trade_id, { r, cells: {} }); byTrade.get(r.trade_id).cells[r.counterparty] = r; }
  $("#recon tbody").innerHTML = byTrade.size ? [...byTrade.values()].map(({ r, cells }) => {
    const tds = cps.map((cp) => {
      const c = cells[cp]; if (!c) return "<td></td>";
      const title = c.reject_reason ? ` title="${esc(c.reject_reason)}"` : "";
      const lat = c.ack_latency_ms != null ? ` <small>${c.ack_latency_ms}ms</small>` : "";
      const resend = c.is_break ? `<button class="ghost" data-resend="${c.trade_id}" data-cp="${esc(cp)}" title="resend to ${esc(cp)}">↻</button>` : "";
      return `<td><span class="cell"><span class="tag ${esc(c.status)} ${c.stuck ? "stuck" : ""}"${title}>${esc(c.status)}</span>${lat}${resend}</span></td>`;
    }).join("");
    return `<tr><td>${esc(r.trade_ref)}</td><td>${esc(r.symbol)}</td><td>${esc(r.side)}</td><td class="num">${num(r.quantity, 0)}</td>${tds}</tr>`;
  }).join("") : `<tr><td colspan="${4 + cps.length}" class="empty">${breaksOnly ? "no breaks 🎉" : "no trades yet"}</td></tr>`;
}
$("#breaks-only").addEventListener("change", refreshMonitor);
$("#recon").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-resend]"); if (!btn) return;
  btn.disabled = true;
  try { await api("/api/posttrade/resend", { method: "POST", body: { trade_id: Number(btn.dataset.resend), counterparty: btn.dataset.cp } }); }
  catch (err) { alert(err.message); }
});

async function refreshAlerts() {
  const alerts = await api("/api/alerts?limit=20");
  $("#alerts tbody").innerHTML = alerts.length ? alerts.map((a) =>
    `<tr><td>${tag(a.severity)}</td><td>${esc(a.rule)}</td><td>${esc(a.message)}</td><td>${esc(a.created_at.slice(11, 19))}</td>
     <td>${a.status === "OPEN" ? `<button class="ghost" data-ack="${a.id}">acknowledge</button>` : tag(a.status)}</td></tr>`).join("")
    : `<tr><td class="empty">no alerts</td></tr>`;
}
$("#alerts").addEventListener("click", async (e) => {
  const id = e.target.dataset.ack; if (!id) return;
  await api(`/api/alerts/${id}/ack`, { method: "POST" }); refreshAlerts(); refreshMonitor();
});

// ---------------------------------------------------------------- admin
async function refreshModes() {
  const box = $("#cp-modes");
  try {
    const modes = await api("/api/admin/counterparties");
    box.innerHTML = Object.entries(modes).map(([cp, mode]) =>
      `<div class="cp"><span>${esc(cp)}</span>${["ACK", "REJECT", "SILENT"].map((m) => `<button class="ghost ${m === mode ? "cur" : ""}" data-cp="${esc(cp)}" data-mode="${m}">${m}</button>`).join("")}</div>`).join("");
  } catch (err) { box.innerHTML = `<p class="msg err">${esc(err.message)}</p>`; }
}
$("#cp-modes").addEventListener("click", async (e) => {
  const { cp, mode } = e.target.dataset; if (!mode) return;
  try { await api(`/api/admin/counterparties/${cp}/mode`, { method: "PUT", body: { mode } }); } catch (err) { say($("#admin-msg"), err.message, true); }
  refreshModes();
});
const actions = {
  inject: () => api("/api/admin/trades/inject", { method: "POST", body: { account_id: "FUND-1", symbol: "EUR/USD", side: "BUY", quantity: "1000000", price: "1.1200" } }),
  bump: async () => { const q = await api("/api/quotes/EUR/USD"); return api("/api/admin/prices/EUR/USD", { method: "POST", body: { mid: String(Number(q.mid) + 0.002) } }); },
  sweep: () => api("/api/admin/sweep", { method: "POST" }),
  settle: () => api(`/api/admin/settle?as_of=${new Date(Date.now() + 3 * 864e5).toISOString().slice(0, 10)}`, { method: "POST" }),
};
document.querySelector("[data-act]").parentElement.addEventListener("click", async (e) => {
  const act = e.target.dataset.act; if (!act) return;
  try { say($("#admin-msg"), JSON.stringify(await actions[act]())); } catch (err) { say($("#admin-msg"), err.message, true); }
});

// ---------------------------------------------------------------- websocket
const refreshOrdersD = debounced(refreshOrders), refreshTradesD = debounced(refreshTrades),
      refreshMonitorD = debounced(refreshMonitor), refreshAlertsD = debounced(refreshAlerts);

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  const pill = $("#ws-status");
  ws.onopen = () => { pill.textContent = "live"; pill.className = "pill on"; };
  ws.onclose = () => { pill.textContent = "reconnecting…"; pill.className = "pill off"; setTimeout(connect, 2000); };
  ws.onmessage = (m) => {
    const { topic, data } = JSON.parse(m.data);
    if (topic === "snapshot") renderQuotes(data.quotes);
    else if (topic === "quote") renderQuotes(data);
    else if (topic === "order") { refreshOrdersD(); }
    else if (topic === "trade") { refreshTradesD(); refreshMonitorD(); }
    else if (topic === "confirmation") { refreshMonitorD(); }
    else if (topic === "alert") { refreshAlertsD(); refreshMonitorD(); }
  };
}

(async function init() {
  await loadStatic();
  await Promise.all([refreshOrders(), refreshTrades(), refreshMonitor(), refreshAlerts(), refreshModes()]);
  connect();
})().catch((err) => say($("#order-msg"), `startup failed: ${err.message}`, true));
