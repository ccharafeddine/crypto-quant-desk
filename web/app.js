// Crypto Quant Desk live demo: fetches Kraken PUBLIC market data (no keys) and
// renders what the engine (in worker.js) computes. There is no order path here.
"use strict";

const KRAKEN = "https://api.kraken.com/0/public/";
const PAIRS = { BTC: "XBTUSD", ETH: "ETHUSD", SOL: "SOLUSD", XRP: "XRPUSD", ADA: "ADAUSD",
  DOGE: "XDGUSD", LINK: "LINKUSD", DOT: "DOTUSD", LTC: "LTCUSD", AVAX: "AVAXUSD" };
const DEFAULT = { BTC: 40, ETH: 25, SOL: 15, ADA: 5, USD: 15 };
const T0 = performance.now();
const timings = (window.__cqdTimings = {});
const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- formatting
const pct = (x, d = 1, signed = false) =>
  x == null ? "—" : (x < 0 ? "−" : signed && x > 0 ? "+" : "") + Math.abs(x * 100).toFixed(d) + "%";
const usd = (x, d = 0, signed = false) =>
  x == null ? "—" : (x < 0 ? "−" : signed && x > 0 ? "+" : "") + "$" +
  Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const num = (x, d = 2) => (x == null ? "—" : x.toFixed(d));
const compact = (x) => (x == null ? "—" : "$" + Intl.NumberFormat("en-US",
  { notation: "compact", maximumFractionDigits: 1 }).format(x));
const tone = (x) => (x == null || x === 0 ? "" : x > 0 ? "pos" : "neg");
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

function status(text, cls = "") {
  $("status-text").textContent = text;
  $("status").className = "status " + cls;
}

function table(el, headers, rows) {
  // rows: arrays of [text, className?]
  const h = "<tr>" + headers.map((x) => `<th>${esc(x)}</th>`).join("") + "</tr>";
  const b = rows.map((r) => "<tr>" + r.map((c) => {
    const [t, cls] = Array.isArray(c) ? c : [c, ""];
    return `<td class="${cls}">${esc(t)}</td>`;
  }).join("") + "</tr>").join("");
  $(el).innerHTML = h + b;
}

function kv(el, pairs) {
  $(el).innerHTML = pairs.map(([k, v, cls]) =>
    `<div><span>${esc(k)}</span><span class="${cls || ""}">${esc(v)}</span></div>`).join("");
}

// ---------------------------------------------------------------- charts (SVG)
function lineChart(svg, series, { w = 900, h = 300, pad = 36, fmt = (v) => v.toFixed(2), bands = null } = {}) {
  const all = series.flatMap((s) => s.v).concat(bands ? bands.flat() : []).filter((v) => v != null);
  const lo = Math.min(...all), hi = Math.max(...all), n = Math.max(...series.map((s) => s.v.length), bands ? bands[0].length : 0);
  const x = (i) => pad + (i / Math.max(n - 1, 1)) * (w - pad - 8);
  const y = (v) => 8 + (1 - (v - lo) / (hi - lo || 1)) * (h - 8 - 22);
  let out = "";
  for (let k = 0; k <= 4; k++) {
    const v = lo + (k / 4) * (hi - lo), yy = y(v);
    out += `<line x1="${pad}" x2="${w - 8}" y1="${yy}" y2="${yy}" stroke="#1f252b"/>` +
      `<text x="4" y="${yy + 4}" fill="#5c6670" font-size="10" font-family="monospace">${fmt(v)}</text>`;
  }
  if (bands) {
    const area = (a, b, op) => {
      const top = a.map((v, i) => `${x(i)},${y(v)}`).join(" ");
      const bot = b.map((v, i) => `${x(i)},${y(v)}`).reverse().join(" ");
      return `<polygon points="${top} ${bot}" fill="#F59E0B" fill-opacity="${op}"/>`;
    };
    out += area(bands[4], bands[0], 0.16) + area(bands[3], bands[1], 0.3);
  }
  for (const s of series) {
    const pts = s.v.map((v, i) => (v == null ? null : `${x(i)},${y(v)}`)).filter(Boolean).join(" ");
    out += `<polyline points="${pts}" fill="none" stroke="${s.color}" stroke-width="${s.width || 1.6}" ${s.dash ? `stroke-dasharray="${s.dash}"` : ""} vector-effect="non-scaling-stroke"/>`;
  }
  series.forEach((s, i) => {
    out += `<rect x="${pad + 10}" y="${h - 16 - i * 0}" width="10" height="2" fill="${s.color}" transform="translate(${i * 150},0)"/>` +
      `<text x="${pad + 24 + i * 150}" y="${h - 12}" fill="#8b949e" font-size="11">${esc(s.name)}</text>`;
  });
  svg.innerHTML = out;
}

// ---------------------------------------------------------------- worker bridge
const worker = new Worker("worker.js");
let seq = 0;
const pending = new Map();
let engineReady = false;
const readyWaiters = [];
worker.onmessage = (ev) => {
  const m = ev.data;
  if (m.type === "progress") status(m.text);
  else if (m.type === "ready") {
    engineReady = true;
    timings.engine_ms = Math.round(performance.now() - T0);
    readyWaiters.splice(0).forEach((f) => f());
  } else if (m.type === "fatal") status("Engine failed to load: " + m.error, "err");
  else if (m.type === "result") {
    const p = pending.get(m.id);
    pending.delete(m.id);
    m.error ? p.reject(new Error(m.error)) : p.resolve({ data: JSON.parse(m.json), ms: m.ms });
  }
};
function call(fn, args) {
  return new Promise((resolve, reject) => {
    const id = ++seq;
    pending.set(id, { resolve, reject });
    worker.postMessage({ id, fn, args });
  });
}

// ---------------------------------------------------------------- Kraken public data
const cache = {};
async function kraken(path) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const res = await fetch(KRAKEN + path);
    const body = await res.json();
    if (body.error && body.error.length) {
      if (String(body.error).includes("Too many requests")) { await new Promise((r) => setTimeout(r, 1500)); continue; }
      throw new Error(body.error.join(", "));
    }
    return body.result;
  }
  throw new Error("Kraken rate limit; try again in a minute");
}
async function ohlc(asset) {
  if (!cache[asset]) cache[asset] = kraken(`OHLC?pair=${PAIRS[asset]}&interval=1440`);
  return cache[asset];
}

// ---------------------------------------------------------------- book editor
function buildEditor() {
  const assets = [...Object.keys(PAIRS), "USD"];
  $("weights").innerHTML = assets.map((a) =>
    `<label>${a}<input type="number" min="0" max="100" step="1" data-asset="${a}" value="${DEFAULT[a] || 0}"></label>`).join("");
  $("weights").addEventListener("input", showTotal);
  showTotal();
}
function readWeights() {
  const w = {};
  document.querySelectorAll("#weights input").forEach((i) => {
    const v = parseFloat(i.value);
    if (v > 0) w[i.dataset.asset] = v;
  });
  return w;
}
function showTotal() {
  const t = Object.values(readWeights()).reduce((a, b) => a + b, 0);
  $("weight-total").textContent = `Total ${t.toFixed(0)}%` + (Math.abs(t - 100) > 0.5 ? " (rescaled to 100%)" : "");
}

// ---------------------------------------------------------------- render
function renderPortfolio(d) {
  const r = d.risk, v95 = d.var.methods[0];
  const k = [
    ["Value", usd(d.nav), "model book"],
    ["Ann. vol", pct(r.ann_vol), "EWMA " + pct(r.ewma_vol)],
    ["BTC beta", num(r.beta), d.n_days + " common days"],
    ["1-day VaR 95%", usd(v95.var95 * d.nav), "historical · " + pct(v95.var95, 2)],
    ["ES 97.5%", usd(d.var.es975.historical * d.nav), pct(d.var.es975.historical, 2)],
    ["Effective bets", num(r.effective_bets), "top-3 " + pct(r.top3)],
    ["30-day VaR 95%", pct(d.fan.var95_horizon), "bootstrap"],
  ];
  $("kpis").innerHTML = k.map(([l, val, s]) =>
    `<div class="kpi"><div class="l">${l}</div><div class="v ${val.startsWith("−") ? "neg" : ""}">${val}</div><div class="s">${esc(s)}</div></div>`).join("");

  lineChart($("chart-backcast"), [
    { name: "This book (backcast)", v: d.backcast.portfolio.map((x) => (x - 1) * 100), color: "#F59E0B", width: 2 },
    { name: "BTC buy & hold", v: d.backcast.btc.map((x) => (x - 1) * 100), color: "#8b949e", dash: "4 3" },
  ], { fmt: (v) => v.toFixed(0) + "%" });
  table("t-holdings", ["Asset", "Weight", "Value", "BTC beta", "Down beta", "Risk share"],
    r.rows.map((x) => [x.asset, pct(x.weight), usd(x.weight * d.nav), num(x.beta), num(x.down_beta), pct(x.risk_pct)]));
  table("t-drawdowns", ["Depth", "Peak", "Trough", "Recovered"],
    d.drawdowns.map((x) => [[pct(x.depth), "neg"], x.peak, x.trough, x.recovered || "open"]));

  table("t-var", ["Method", "VaR 95%", "$", "VaR 99%", "$"], d.var.methods.map((m) =>
    [m.label, [pct(m.var95, 2), "neg"], [usd(m.var95 * d.nav), "neg"], [pct(m.var99, 2), "neg"], [usd(m.var99 * d.nav), "neg"]]));
  const es = d.var.es975;
  kv("es", [["ES 97.5% · historical", pct(es.historical, 2), "neg"], ["ES 97.5% · filtered (EWMA)", pct(es.fhs, 2), "neg"],
    ["ES 97.5% · normal", pct(es.normal, 2), "neg"], ["ES 97.5% in dollars (historical)", usd(es.historical * d.nav), "neg"]]);
  table("t-backtest", ["Method", "Exceptions / exp.", "Kupiec p", "Christoff. p", "Verdict"], d.backtests.map((b) =>
    [b.label, b.n ? `${b.exceptions} / ${num(b.expected, 1)}` : "too short", num(b.kupiec_p, 3), num(b.ind_p, 3), [b.verdict, b.verdict]]));

  table("t-replays", ["Event", "Window (UTC)", "BTC", "Book", "$ impact", "Coverage"], d.replays.map((x) =>
    [x.label, `${x.start.slice(0, 10)} → ${x.end.slice(0, 10)}`, [pct(x.btc), tone(x.btc)], [pct(x.portfolio), tone(x.portfolio)],
      [usd(x.usd), tone(x.usd)], x.proxied.length ? "proxied: " + x.proxied.join(", ") : "all real"]));
  $("depeg").textContent = `Stablecoin depeg (USDC −13% as in March 2023, USDT −5%): book ${pct(d.depeg, 2, true)}. Moves come from Kraken's public trade history for each window.`;
  const mid = d.fan.bands[2];
  lineChart($("chart-fan"), [{ name: "Median", v: mid, color: "#F59E0B", width: 2 }],
    { h: 260, bands: d.fan.bands, fmt: (v) => v.toFixed(2) });
  $("fan-note").textContent = `5–95% and 25–75% bands of 2,000 paths. 30-day VaR 95% ${pct(d.fan.var95_horizon)} (${usd(d.fan.var95_horizon * d.nav)}). Drift is removed, so this shows a range of risk, not a forecast.`;

  table("t-liquidity", ["Asset", "Value", "ADV (30d)", "% of ADV", "Days to exit"], d.liquidity.map((x) =>
    [x.asset, usd(x.value), compact(x.adv), pct(x.pct_adv, 3), x.days == null ? "—" : x.days < 0.01 ? "< 0.01" : num(x.days)]));
  const a = d.attribution;
  if (a) kv("attribution", [["Book", pct(a.portfolio, 1, true), tone(a.portfolio)], ["BTC buy & hold", pct(a.btc, 1, true), tone(a.btc)],
    ["Active", pct(a.active, 1, true), tone(a.active)], ["· Allocation (cash vs invested)", pct(a.allocation, 1, true), tone(a.allocation)],
    ["· BTC beta", pct(a.btc_beta, 1, true), tone(a.btc_beta)], ["· Selection", pct(a.selection, 1, true), tone(a.selection)]]);
  if (d.missing.length) status("No Kraken history for " + d.missing.join(", "), "err");
}

function heatColor(v, max) {
  if (v == null) return null;
  const t = Math.max(-1, Math.min(1, v / (max || 1)));
  return t >= 0 ? `rgba(245,158,11,${0.25 + 0.75 * t})` : `rgba(240,97,109,${0.25 + 0.75 * -t})`;
}

function renderStrategy(d) {
  const s = d.setup, b = d.backtest, g = d.stability;
  $("signals-wait").hidden = true;
  $("signals").hidden = false;
  kv("setup", s ? [["State", s.state.replace("_", " ")], ["Entry (last close)", usd(s.entry_ref, 1)], ["ATR stop", usd(s.stop, 1)],
    ["Size", `${s.size_base} BTC · ${usd(s.size_quote)}`], ["Risk", usd(s.risk_quote, 2)], ["Targets", s.targets.map((t) => usd(t)).join(" / ")],
    ["Reward : risk", `${num(s.rr, 1)} : 1`], ["Confidence (trend only)", pct(s.confidence)], ["Bound by", s.capped_by || "risk"]]
    : [["State", "flat: no setup today"]]);
  lineChart($("chart-bt"), [
    { name: "Net of costs", v: b.curve, color: "#F59E0B", width: 2 },
    { name: "Frictionless", v: b.frictionless_curve, color: "#8b949e", dash: "4 3" },
  ], { w: 500, h: 220, fmt: (v) => "$" + (v / 1000).toFixed(1) + "k" });
  const ci = b.expectancy_r_ci ? ` (90% CI ${num(b.expectancy_r_ci[0])} to ${num(b.expectancy_r_ci[1])})` : "";
  kv("bt", [["Trades", String(b.trades)], ["Win rate", pct(b.win_rate)], ["Expectancy, net", (b.expectancy_r == null ? "—" : num(b.expectancy_r) + " R") + ci],
    ["Expectancy, frictionless", b.frictionless_expectancy_r == null ? "—" : num(b.frictionless_expectancy_r) + " R"],
    ["Walk-forward OOS", b.oos_trades ? `${b.oos_trades} trades · ${num(b.oos_expectancy_r)} R` : "no OOS trades yet"],
    ["Costs", `${b.fee_bps} bps fee + ${b.slippage_bps} bps slippage / side`], ["Max drawdown", pct(b.max_drawdown), "neg"]]);
  const flat = g.cells.flat().filter((v) => v != null);
  const max = Math.max(...flat.map(Math.abs), 0.01);
  $("t-stability").innerHTML = "<tr><th></th>" + g.slows.map((s) => `<th>slow ${s}</th>`).join("") + "</tr>" +
    g.cells.map((row, i) => `<tr><th>fast ${g.fasts[i]}</th>` + row.map((v, j) => {
      const chosen = i === g.chosen[0] && j === g.chosen[1] ? " chosen" : "";
      return v == null ? `<td class="na${chosen}">·</td>` :
        `<td class="${chosen}" style="background:${heatColor(v, max)}">${v > 0 ? "+" : ""}${v.toFixed(2)}</td>`;
    }).join("") + "</tr>").join("");
  $("stability-note").textContent = `Chosen cell outlined (fast ${d.params.fast} / slow ${d.params.slow}). ` +
    `${g.positive_share == null ? "" : pct(g.positive_share, 0) + " of scored cells are positive. "}` +
    "A plateau of positive cells is evidence; one bright cell on its own is curve fit. Cells with fewer than 3 trades are blank.";
}

// ---------------------------------------------------------------- run
let running = false;
async function run() {
  if (running) return;
  running = true;
  $("run").disabled = true;
  try {
    const weights = readWeights();
    const nav = Math.max(100, parseFloat($("nav").value) || 25000);
    const assets = [...new Set(["BTC", ...Object.keys(weights).filter((a) => a in PAIRS)])];
    status("Fetching Kraken daily bars…");
    const tf = performance.now();
    const raw = {};
    for (const a of assets) raw[a] = await ohlc(a); // sequential: friendly to Kraken's public rate limit
    timings.data_ms = Math.round(performance.now() - tf);
    if (!engineReady) { status("Waiting for the engine…"); await new Promise((r) => readyWaiters.push(r)); }
    status("Computing…");
    const p = await call("portfolio", { ohlc: JSON.stringify(raw), weights: JSON.stringify(weights), nav });
    renderPortfolio(p.data);
    timings.portfolio_compute_ms = Math.round(p.ms);
    if (timings.first_render_ms == null) timings.first_render_ms = Math.round(performance.now() - T0);
    const asof = new Date(p.data.asof * 1000).toISOString().slice(0, 10);
    status(`Live · Kraken daily bars to ${asof} (closed)`, "ok");
    if (timings.strategy_ms == null) {
      const pairs = await kraken("AssetPairs?pair=XBTUSD");
      const st = await call("strategy", { btc: JSON.stringify(raw.BTC), pairs: JSON.stringify(pairs), equity: 10000 });
      renderStrategy(st.data);
      timings.strategy_ms = Math.round(st.ms);
    }
    $("timing").textContent = `First numbers in ${(timings.first_render_ms / 1000).toFixed(1)} s (Python runtime ${(timings.engine_ms / 1000).toFixed(1)} s, risk compute ${timings.portfolio_compute_ms} ms)`;
    timings.done = true;
  } catch (e) {
    status(String(e.message || e), "err");
    timings.error = String(e.message || e);
  } finally {
    running = false;
    $("run").disabled = false;
  }
}

document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll("#tabs button, .tab").forEach((x) => x.classList.remove("on"));
  b.classList.add("on");
  $("tab-" + b.dataset.tab).classList.add("on");
}));
$("run").addEventListener("click", run);
buildEditor();
run();
