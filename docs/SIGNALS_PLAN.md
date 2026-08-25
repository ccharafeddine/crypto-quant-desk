# SIGNALS_PLAN.md — Expansion v2.2: Signals, Trade Setups & Order-Flow

Adds a **signal engine** that turns the backtested strategy into concrete, sized
trade setups, and an **order-flow (L2) layer** that improves *entry timing and
execution* on those setups. Same discipline as every prior phase: engine stays
pure (numpy/pandas, deterministic, no I/O, no Qt); each step is a module + its
tests; every step ends `pytest -q` green + `ruff check src tests` clean; the
money path (`OrderService` → validation → confirm → mode) is never touched.

Strategy source: `Kraken Integration` project doc `STRATEGY.md` (v1). Data paths
already exist: `KrakenRESTClient.get_ohlc()` → `list[Candle]` (full OHLCV) and
`get_depth(pair, count)` → `{"bids": [...], "asks": [...]}`. Active-symbol routing
via `SymbolHub`. LLM surfacing via `analyst/context.py`.

---

## 0. What this is — and is NOT (read first)

This section is a guardrail, not preamble; it constrains every step below.

- **Signals only, never execution.** The app proposes setups; the user places
  every order through the existing ticket (confirmation + limits + paper mode).
  There is no code path from a signal to an order. This is deliberate: Kraken
  Prop / Breakout prohibits third-party automated systems, so an autotrader is
  off the table by rule, not just by choice.
- **"Provably profitable" is not a claim we can make — and we won't.** No trading
  strategy is provable; a prop firm's economics depend on most strategies failing.
  What we build instead is *measurable*: the strategy is backtested with
  regime-tagged metrics (S4), and its **live signals are tracked** so the panel
  shows the strategy's own realized expectancy over time. The user judges it on
  its record, the way a prop desk would — not on a promise.
- **L2 order-flow is for execution, not direction.** Order-book imbalance is a
  noisy, easily-spoofed, seconds-horizon quantity; treating it as directional
  alpha over minutes-to-hours is not supported by evidence and invites
  overfitting. Its honest, high-value use — the "best use" — is: time the entry,
  estimate slippage for the intended size, and flag deteriorating liquidity.
  Direction comes from the trend engine; L2 decides *whether now is a good moment
  to act on it and at what price.*

If a future step blurs any of these three lines, it is out of scope for v2.2.

---

## 1. Architecture at a glance

```
data (existing)                 engine (NEW, pure)                 ui (NEW)
─────────────────               ──────────────────                ─────────
get_ohlc()  ──► bars ──────────► signals.py                        panels/
                                   indicators (sma, atr, donchian)   signals.py
                                   TrendState machine                (SignalsPanel)
                                   evaluate_setup() ──► TradeSetup ─────►│
                                                                         │ shows:
get_depth() ──► snapshot ──────► microstructure.py                       │  • setup
                                   book_features()  ──► BookFeatures      │  • exec
                                   expected_fill()  ──► FillEstimate ────►│    overlay
                                   detect_walls()                         │  • timing
                                   entry_timing(setup, feats) ──► Verdict │    verdict
                                                                         │
                                 backtest.py (pure, deterministic)  ────►│  • track
                                   walk_forward() ──► StrategyStats       │    record
```

Orchestration (impure) lives in a `SignalsService` (§S3): it schedules the pulls,
keeps a short rolling depth buffer for order-flow *deltas*, calls the pure engine,
and emits results to the panel, the alerts engine, and the analyst context. The
engine itself never fetches — it receives snapshots and returns values, so every
function is unit-testable against fixed vectors (see `scenario.py` for the house
style).

---

## 2. The L2 "best use" design (rationale)

The trend strategy operates on daily bars; a setup is valid for hours-to-days.
The order book describes liquidity for the next seconds-to-minutes. They answer
different questions, so L2 is layered *on top of* a trend setup, three ways:

1. **Entry timing.** When a long setup is active, prefer to enter when near-term
   liquidity is supportive: non-negative depth imbalance, a bid wall below acting
   as support, and no large ask wall immediately overhead. Output a verdict —
   `GO` / `WAIT` / `CAUTION` — with the reasons. This never flips direction; it
   only gates *when*.
2. **Execution/slippage.** Walk the book for the setup's intended size to compute
   the expected VWAP fill and slippage in bps, and whether the order would sweep
   past a wall. On a small account the backtest showed fees+slippage eat a third
   of the target — so choosing limit-vs-market and a sane entry price matters.
3. **Liquidity risk.** Spread widening, microprice diverging from mid, and walls
   being pulled are "conditions deteriorating" flags that downgrade the timing
   verdict or suggest deferring. Order-flow *deltas* (imbalance change across the
   last few snapshots) feed this, computed from the service's rolling buffer.

What we explicitly do **not** do: emit a buy/sell purely because imbalance is
positive. That is the trap this design avoids.

---

## S0 — Encode the plan (this doc)
- **S0.1** Add this file; cross-link from `IMPLEMENTATION_PLAN.md` (new "Expansion
  v2.2") and add PRD acceptance criteria F13 (signals) + F14 (order-flow).
- **S0.2** Settings surface reserved: a **Strategy** tab spec in `APP_FLOW.md`
  (params, target pairs, enable/disable, risk-per-trade %). No code yet.

## S1 — Signal engine (pure)
*(engine stays pure; tests in the same step; known-input vectors)*

- **S1.1** `engine/indicators.py`: `sma(series, n)`, `atr(candles, n)`,
  `donchian(candles, n)` (prior-N high/low, **shifted to exclude the current bar**
  — the look-ahead bug found in the STRATEGY.md backtest; regression test pins it),
  `rsi(series, n)`. Pure functions over `pd.Series` / `list[Candle]`. Tests: hand-
  computed vectors + NaN/short-history guards.
- **S1.2** `engine/signals.py` models (pydantic): `StrategyParams`
  (`fast=20, slow=50, trend=200, atr_len=14, atr_mult=2.0, risk_pct=0.005,
  variant="ma_cross"|"breakout"`); `TradeSetup` (`symbol, direction, state,
  entry_ref, stop, size_base, size_quote, risk_quote, targets, rr, confidence,
  rationale, created_ts`).
- **S1.3** `engine/signals.py` logic: `trend_state(candles, params) -> TrendState`
  (long-only v1: flat / long-armed / long-active, gated by price>trend MA);
  `evaluate_setup(candles, params, equity_quote, pair_spec) -> TradeSetup | None`
  — computes entry reference, `stop = entry − atr_mult·ATR`, position size so
  distance-to-stop = `risk_pct·equity` (respecting `pair_spec` precision/min and
  the 5:1 leverage cap), and a confidence score from trend strength (distance
  above trend MA, slope) — **not** from L2. Deterministic; exhaustively tested
  (entry/stop math, sizing rounding, min-size fail-closed, no-signal states).
- **S1.4** `engine/signals.py` guards mirror the strategy's circuit breakers as
  *advisory* fields: `daily_room` and `total_room` (distance to the 3% / 5% prop
  limits given current equity and start-of-day equity) so the panel can show
  "setup suppressed: daily loss room < one unit of risk." Pure; inputs passed in.

## S2 — Order-flow engine (pure)
- **S2.1** `engine/microstructure.py` models: `BookFeatures` (`mid, microprice,
  spread_abs, spread_bps, imbalance_l5/l10/l25, depth_bid_bps, depth_ask_bps`),
  `Wall` (`side, price, size, dist_bps, z`), `FillEstimate` (`side, notional,
  vwap, slippage_bps, levels_consumed, sweeps_wall`).
- **S2.2** `book_features(depth) -> BookFeatures`: mid, **microprice**
  `(bid_px·ask_sz + ask_px·bid_sz)/(bid_sz+ask_sz)`, spread abs/bps, multi-depth
  imbalance `(Σbid − Σask)/(Σbid + Σask)`, cumulative depth within ±X bps. Pure;
  fixture-tested incl. empty/one-sided books (returns `None` fields, never raises).
- **S2.3** `detect_walls(depth, z_thresh=3.0) -> list[Wall]`: levels whose size is
  a `z_thresh`-outlier vs the side's level-size distribution, with distance-to-mid
  in bps. Tested on synthetic books.
- **S2.4** `expected_fill(depth, side, notional) -> FillEstimate`: walk the book,
  VWAP fill for the notional, slippage vs mid, whether it sweeps a wall, and a
  flag if the book is too thin to fill (partial). Tested against hand-walked books.
- **S2.5** `entry_timing(setup, features, walls, flow_delta=None) -> TimingVerdict`
  (`verdict: GO|WAIT|CAUTION, reasons: list[str], score`). Rule-based, documented,
  and *conservative by default* (unknown/thin book → `WAIT`, never `GO`). Consumes
  optional `flow_delta` (imbalance change) from the service buffer. Fully tested
  truth-table.

## S3 — Service + wiring (impure, thin)
- **S3.1** `SignalsService` (in `ui/services.py` or `data/signals_service.py`):
  on a timer and on `SymbolHub.changed`, pull `get_ohlc(pair, interval)` +
  `get_depth(pair, count)`, keep a bounded rolling deque of recent depth snapshots
  for `flow_delta`, run the pure engine with the user's `StrategyParams` and
  current equity (from portfolio), emit `setup_updated(TradeSetup|None,
  BookFeatures, FillEstimate, TimingVerdict)`. Timeouts + per-symbol generation
  guard reused from existing panels. Tested with fake client + fake clock.
- **S3.2** Settings: **Strategy** tab (`ui/dialogs/settings.py`) — variant, MA/ATR
  params, risk-per-trade %, target pairs, poll interval, enable/disable; persisted
  via `settings_store`. Fail-safe defaults = STRATEGY.md v1.

## S4 — Backtest & track record (pure) — replaces "provable" with "measured"
- **S4.1** `engine/backtest.py`: `walk_forward(candles, params, limits) ->
  StrategyStats` (pass/bust/unresolved under the prop limits, expectancy, profit
  factor, max DD, trades, regime tag). Deterministic (seedless; no RNG). Mirrors
  the hourly-constrained method already prototyped for STRATEGY.md, adapted to the
  repo's `Candle` type. Exhaustively tested on canned series.
- **S4.2** Live track record: log each emitted setup and its realized outcome to a
  JSONL (reuse `trading/audit.py` patterns), and compute rolling live expectancy;
  the panel shows backtest **and** live stats side by side. Honest empty-state
  until enough live samples.

## S5 — Panel + analyst + alerts
- **S5.1** `ui/panels/signals.py` (`SignalsPanel`): header (symbol/timeframe via
  `PanelHeader`); **Setup** card (direction, entry ref, stop, size in base/quote,
  risk in quote, R:R, confidence, rationale, prop-room warnings); **Execution
  overlay** (imbalance gauge, microprice vs mid, spread bps, est. slippage for the
  setup size, wall map, timing verdict GO/WAIT/CAUTION with reasons); **Track
  record** (backtest vs live stats). Loading/error/empty states per FRONTEND;
  theme tokens; no auto-actions — an explicit "Send to ticket" button pre-fills
  the ticket (price/size only, never submits, reuses the FL-4 pre-fill path).
- **S5.2** `analyst/context.py`: add `signals_snapshot(setup, features, verdict,
  stats)` so the Claude analyst can narrate the current setup and its execution
  context — over engine-computed numbers only (AC7.3 guarantee preserved).
- **S5.3** Alerts: new rule type "setup armed/triggered for pair" via the existing
  alerts engine + winotify sink, so the user is notified without watching.

---

## Test plan (per step, house style)
- Every engine function: known-input vectors with hand-computed expectations,
  plus empty / one-sided / too-short / NaN guards (never raise; return `None`
  fields). Determinism asserted (same input → same output).
- Indicators: the Donchian shift regression test is mandatory (guards the
  look-ahead class of bug).
- Sizing: rounds to `pair_spec`, fails closed below min size, respects leverage
  cap; a property test that realized per-trade risk ≤ `risk_pct·equity + one tick`.
- Timing verdict: full truth table incl. thin/unknown book → `WAIT`.
- Service: fake client + fake clock; generation guard; timeout → panel error state.

## Acceptance criteria (v2.2 "done")
1. For a chosen pair with a live/demo book, the panel shows a correct, sized
   `TradeSetup` (or a clear no-signal state) and an execution overlay with a
   timing verdict, updating on symbol change and on the poll interval.
2. "Send to ticket" pre-fills price and size only; it never submits, and the
   money path is unchanged.
3. `walk_forward` reproduces the STRATEGY.md metrics on the same data within
   tolerance; backtest and live stats both render.
4. `pytest -q` green, `ruff check src tests` clean, CI green; engine additions
   are pure and covered.
5. No signal→order code path exists (grep-audited); the three §0 lines hold.

## Dependencies / ordering
S1 and S2 are independent and can be built in parallel (both pure). S3 requires
S1+S2. S4 requires S1. S5 requires S1–S4. No new third-party dependency is needed
(numpy/pandas/pydantic/PySide6 already present).

## Open decisions for the owner
- **Timeframe:** daily bars match the backtested edge (recommended). An
  intraday variant (e.g., 4h) would trade more but needs its own backtest before
  it earns trust — out of scope unless you want it.
- **Universe:** BTC + ETH first (deepest books, 5:1). Adding liquid alts later is
  the safe way to raise pass-speed (per STRATEGY.md), not bigger size.
- **Live L2 source:** REST Depth poll (existing `book.py` path, ~2.5s) is fine for
  v2.2; the WS `book` channel (checksummed local maintenance) can replace it later
  with no engine change, since the engine consumes snapshots.
