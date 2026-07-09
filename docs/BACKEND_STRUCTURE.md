# BACKEND_STRUCTURE.md — Crypto Quant Desk v2

No server, no database. The "backend" is the in-process service layer: data clients, order service, paper simulator, alert engine, and local storage schemas. Panels talk to services; services talk to Kraken; the engine stays pure (no I/O, no Qt, no subprocess — unchanged hard rule).

## Module map (target)

```
src/cqd/
├── app.py                  # bootstrap: qasync loop, keyring load, client selection
├── engine/                 # PURE math (existing + additions)
│   ├── metrics.py          # vol, EWMA, sharpe, drawdown, VaR/CVaR (existing)
│   ├── risk.py             # portfolio risk (existing)
│   ├── cost_basis.py       # per-quote-currency cost basis (rework, see Defects)
│   └── performance.py      # NEW: equity curve, realized PnL, trade stats
├── data/
│   ├── client.py           # protocol + factory (extend: rest|cli|demo)
│   ├── rest.py             # NEW: KrakenRESTClient (httpx, signing, rate limiter)
│   ├── ws.py               # NEW: KrakenWSClient (websockets, reconnect, dispatch)
│   ├── exchange.py         # CLI client (kept; add timeout + strict JSON)
│   ├── normalize.py        # shared shape normalizer (fix split_pair)
│   ├── returns.py, portfolio.py, demo.py, cache.py   # existing
│   └── credentials.py      # NEW: keyring read/write/delete, .env dev fallback
├── trading/                # NEW package — everything that can move money
│   ├── orders.py           # OrderService: validate → confirm → route → track
│   ├── paper.py            # PaperBroker: simulator, persisted state
│   ├── limits.py           # max-order-value and pair-precision checks
│   └── audit.py            # append-only JSONL audit writer
├── alerts/                 # NEW: rule models, evaluator, winotify sink
├── analyst/                # rules narration (existing) + llm.py (Claude tool-use)
└── ui/                     # panels/dialogs per APP_FLOW.md
```

## Client protocol (what every backend implements)

```
get_balance() -> dict[asset, float]
get_trades(start?, end?) -> list[TradeDict]          # normalized, incl. quote currency
get_ledgers(start?, end?) -> list[LedgerDict]        # NEW (equity curve)
get_marks(pairs) -> dict[symbol, float]
get_ohlc_closes(pair, interval=1440, since?) -> list[(ts, close)]
get_depth(pair, count=25) -> {bids: [...], asks: [...]}   # NEW (book panel)
# trading (REST + paper only; CLI backend raises NotSupported)
add_order(OrderRequest) -> OrderAck
edit_order(txid, ...) -> OrderAck
cancel_order(txid) -> CancelAck
cancel_all() -> CancelAck
get_open_orders() -> list[OrderDict]
get_ws_token() -> str
```

Contract rules (encode the audit fixes):
- Every network/subprocess call has a hard timeout (REST 15 s; CLI 30 s via `asyncio.wait_for`); timeout raises `KrakenTimeoutError`, never hangs a panel.
- Invalid/partial JSON on success exit raises `KrakenProtocolError`; `None` is never returned as data.
- `get_marks` for multiple pairs degrades per-pair: unknown pairs are reported in the result's `missing` set, not raised, so one bad symbol cannot blank the Risk panel.
- All numbers cross the boundary as `float` already normalized; symbols as `BASE/QUOTE` slash form.

## Kraken REST specifics

- Sign: `API-Sign = b64(HMAC_SHA512(key=b64decode(secret), msg=uri_path + sha256(nonce + urlencode(body))))`; nonce = monotonic ms counter (persisted high-water mark to survive restarts).
- Private calls serialized through a token bucket (Starter tier: capacity 15, refill 0.33/s); `AddOrder`/`CancelOrder` additionally respect Kraken's per-pair order limits by simple in-flight cap (max 1 unacked order mutation at a time from the UI).
- Kraken error strings map to a typed taxonomy: `EAPI:Invalid key → AuthError`, `EAPI:Rate limit → RateLimitError(retry_after)`, `EOrder:* → OrderRejected(reason)`, `EGeneral:Permission denied → PermissionError`, anything else → `KrakenAPIError`. Panels render by type, audit log stores the raw string.
- Response envelope is `{"error": [...], "result": {...}}`; non-empty `error` is always a raise, even with a 200 status.

## WebSocket lifecycle

1. Public socket connects at app start (demo included); subscribes `ticker` for held assets + selected pair, `book` when the depth panel is open.
2. Private socket connects when keys exist: REST `GetWebSocketsToken` → subscribe `executions` (+`balances`).
3. Heartbeat watchdog: no message for 10 s → status `DELAYED`, start REST polling (30 s); reconnect with backoff 1/2/4/…/60 s, resubscribe, resync snapshot via REST on reconnect (orders may have changed while dark).
4. Every `executions` event updates the order store and appends to the audit log if it concerns an order this app placed.

## OrderService flow (the only path to money)

`ticket → OrderRequest(pydantic) → limits.py (precision, min size, max value) → order_confirm_dialog → route`

- Route by mode: `PaperBroker` (paper) or `KrakenRESTClient.add_order` (live). The service, not the panel, owns mode; panels cannot bypass confirmation or limits (no public "send" API without a confirmed request token).
- Ambiguous submits (timeout after send) enter `UNKNOWN` state → reconciliation task polls `OpenOrders`/`QueryOrders` until resolved; UI shows "reconciling".
- Conditional close (TP/SL attached to entry) uses Kraken's `close[ordertype]`/`close[price]` params — one atomic AddOrder, never two racing orders.
- **Withdrawal endpoints are never wrapped.** No code path constructs a request to any `/0/private/Withdraw*` route.

### PaperBroker

- Fills against live marks: market fills at mark ± slippage (default 5 bps, configurable); limit fills when mark crosses price; stops trigger on mark, then fill as market. Fees simulated at taker 0.40% / maker 0.25% defaults.
- Holds its own positions/cash overlay seeded from the live account snapshot (or demo book), so paper PnL is trackable without touching real balances.
- State persisted to `paper_state.json` on every mutation; corrupt file → rename to `.bak`, start fresh, notify.

## Local storage schemas

### Audit log — `audit/orders-YYYYMM.jsonl`, one JSON object per line
```
{"ts": "2026-07-09T14:31:22.123Z", "event": "submit|ack|reject|fill|cancel|edit|unknown|resolve",
 "mode": "paper|live", "source": "ui|autotrader",
 "request": {"pair": "BTC/USD", "side": "buy", "ordertype": "limit", "volume": 0.05,
             "price": 60000.0, "close": {"ordertype": "stop-loss", "price": 55000.0} | null},
 "response": {"txid": ["..."]} | null, "error": "EOrder:Insufficient funds" | null,
 "order_value_usd": 3000.0, "app_version": "2.0.0"}
```
Append-only; never edited; never contains keys. `source` exists so the future autotrader shares the same log.

### Alert rules — `alerts.json`
```
{"version": 1, "rules": [
  {"id": "uuid4", "kind": "price_above|price_below|position_pnl_pct|portfolio_drawdown_pct|risk_metric",
   "pair": "BTC/USD" | null, "asset": "BTC" | null, "metric": "ann_vol" | null,
   "threshold": 65000.0, "repeat": false, "enabled": true, "created": "ISO8601",
   "last_fired": "ISO8601" | null}]}
```

### QSettings keys (registry)
`theme/name`, `layout/state`, `layout/geometry`, `trading/paper_mode` (bool, default true), `trading/max_order_usd` (float, default 500), `trading/confirm_cancels` (bool), `data/dust_threshold_usd` (default 1.0), `data/source` (`auto|rest|cli|demo`).

### Credential Manager (via `keyring`, service name `cqd`)
`kraken-api-key`, `kraken-api-secret`, `anthropic-api-key`. `credentials.py` is the only module that touches keyring or the `.env` fallback; nothing else reads key material, and no key value ever passes through logs, exceptions, audit entries, or LLM prompts.

## Engine additions (pure)

- `performance.py`: `build_equity_curve(ledgers|trades, ohlc_closes) -> pd.Series`; `realized_pnl(trades) -> per-asset + total (per quote currency)`; `trade_stats(round_trips) -> {win_rate, avg_win, avg_loss, expectancy, profit_factor}`; reuses `metrics.max_drawdown/drawdown_series`.
- `cost_basis.py` rework (audit findings 1 & 3): group trades **per (asset, quote)**; true average-cost method (sells reduce quantity at running avg cost, realized PnL captured separately; basis can never go negative); result carries `quote` for label-aware display ("cost basis (BTC)"). Never sum across quote currencies.

## Known defects to fix in Phase 1 (from the 2026-07-09 logic audit)

Contract-level requirements; details in IMPLEMENTATION_PLAN 1.x:
1. `split_pair` longest-suffix bug mangles `XTZUSD`→`XT/USD`, `REZUSD`→`RE/USD` (HIGH)
2. Cost basis sums mixed-quote costs as USD (HIGH — forbidden by the old caveat, still present)
3. Avg cost = net-cash/qty, goes negative after profitable sells (HIGH)
4. One bad pair fails whole risk computation — needs per-pair degradation (MED-HIGH)
5. NaN-day returns treated as 0%, diluting vol/beta/tails — restrict window to common history or weight-renormalize per day, footnote the choice (MED)
6. CLI subprocess has no timeout (MED)
7. Exit-0 + invalid JSON returns `None` as data (MED)
8. USD cash row renders Mark/Value "-" (MED-LOW)
9. Refresh-all skips analyst panel (LOW)
10. Overlapping refresh race — generation counter per panel (LOW)
11. All-NaN risk contribution crashes narrator; `nan%` renders (LOW)
12. EUR/GBP treated as zero-vol cash while valued at floating marks (LOW — footnote or separate fiat bucket)

## Edge cases (system-wide)

- **Duplicate submission:** submit button disabled from confirm-click until ack/reject/timeout; nonce collisions impossible (monotonic counter).
- **Concurrent updates:** one writer per store (order store updated only by WS dispatcher; audit file appended under an asyncio lock).
- **Clock drift:** Kraken rejects stale nonces, not timestamps; no NTP dependency in v1 (matters later for the autotrader host).
- **Orphaned paper orders** referencing pairs no longer held/listed: kept, flagged inactive, cancellable.
- **Corrupt local JSON:** `.bak` + fresh start + user notification; never a crash loop.
- **Key revoked mid-session:** next private call raises AuthError → banner "Kraken rejected credentials" → offer Settings; streaming keeps public data flowing.
