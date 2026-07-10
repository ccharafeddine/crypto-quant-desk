# IMPLEMENTATION_PLAN.md — Crypto Quant Desk v2

Numbered build sequence. Each step is one verifiable unit (module + its tests where applicable). Requirements live in the doc referenced per step; this file orders the work. Verification default: `pytest -q` green + `ruff check src tests` clean; UI steps additionally launch `python -m cqd`.

## Phase 0 — Repo hygiene and debranding

- **0.1** Remove `docs/PROJECT_INSTRUCTIONS.md` (done 2026-07-09, security audit) and replace contest-era docs with these six. *(PRD: repo-hygiene metric)*
- **0.2** Rewrite `CLAUDE.md` for the overhaul (new rules: REST-primary, trading guardrails, Windows-first). Update `README.md`: purpose, Windows quick start, remove contest/$DOG/macOS-install sections.
- **0.3** Debrand code: theme registry renames `$DOG`→`Amber`, default→`Slate`; remove `DOG` from `demo.py` sample book (replace with a neutral fourth asset, e.g. ADA); update `tests/test_theme.py`, `test_demo.py`. *(FRONTEND: Theme system)*
- **0.4** Decision (owner): purge git history (fresh initial commit or `git filter-repo` on `docs/PROJECT_INSTRUCTIONS.md`) vs. leave history. Execute if chosen; force-push.
- **0.5** Add `docs/lessons.md` + `progress.txt` session files; wire the session-management habit.

## Phase 1 — Correctness: fix the 2026-07-09 audit findings

*(BACKEND: Known defects; all engine work stays pure with tests in the same step)*

- **1.1** Fix `normalize.split_pair` suffix bug (XTZUSD, REZUSD cases) + regression tests. *(finding 2)*
- **1.2** Rework `engine/cost_basis.py`: per-(asset, quote) grouping, true average-cost with realized-PnL capture, non-negative basis invariant, `quote` in result; update `ui/panels/positions.py` labels ("cost basis (BTC)"). Tests: mixed-quote, profitable-partial-sell, oversell. *(findings 1, 3)*
- **1.3** `get_marks` per-pair degradation (`missing` set) + `portfolio.compute_account_risk` consumes it; one bad pair no longer fails Risk/Analyst. Same pattern for per-asset OHLC failure in `returns.build_returns_frame` (asset dropped + caveat). *(finding 4)*
- **1.4** Returns alignment policy: portfolio series computed over common-history window with per-day weight renormalization for missing assets; caveat string emitted when window shrank. Tests with staggered histories. *(finding 5)*
- **1.5** CLI client: `asyncio.wait_for` timeout (30 s) → `KrakenTimeoutError`; strict JSON (exit-0 + bad JSON → `KrakenProtocolError`). *(findings 6, 7)*
- **1.6** UI polish fixes: USD/stables cash rows show 1.00 mark and value; refresh-all includes analyst; per-panel refresh generation counter; NaN guards in `risk._pct` and `narrate` (all-NaN risk contribution). *(findings 8–11)*
- **1.7** Fiat policy: EUR/GBP/JPY out of the zero-vol cash set (or footnoted explicitly); document in risk footnotes. *(finding 12)*

## Phase 2 — Windows foundation (REST data layer + credentials + settings)

- **2.1** `data/credentials.py`: keyring get/set/delete (service `cqd`), `.env` dev fallback, no key material in logs. Unit tests with a fake keyring backend. *(BACKEND: Credential Manager)*
- **2.2** `data/rest.py` signing core: nonce counter (persisted high-water mark), HMAC-SHA512 signature, error-envelope parsing → typed taxonomy. Pure-logic tests against Kraken's documented signature example. *(TECH_STACK: Kraken REST; verify shapes against docs.kraken.com)*
- **2.3** `data/rest.py` public wrappers: `Time, Assets, AssetPairs, Ticker, OHLC, Depth` → normalized shapes (reuse `normalize.py`); rate-limit token bucket; 15 s timeouts. Tests with recorded fixtures.
- **2.4** Private read wrappers: `Balance, TradesHistory, Ledgers, OpenOrders, ClosedOrders, QueryOrders` (requires 2.2). Normalizer gains ledger support.
- **2.5** `client.py` factory: `data/source = auto|rest|cli|demo`; `auto` = rest when keys exist, demo otherwise; CLI available where binary resolves. Panels unchanged.
- **2.6** `ui/dialogs/settings.py`: Keys tab (verify-then-save via `Balance`, masked fields, disconnect), Trading tab (paper default, max order USD, dust threshold), Data tab (source), wiring to QSettings + credentials.py. *(APP_FLOW FL-2)*
- **2.7** `ui/dialogs/first_run.py` + startup path: keyring → first-run dialog → demo/connect branches. *(APP_FLOW FL-1)*
- **2.8** Windows fonts + cache relocation: font fallback chains (Segoe UI / Cascadia Mono), diskcache to `%LOCALAPPDATA%\CryptoQuantDesk\cache`. *(FRONTEND: Typography; TECH_STACK: Storage)*
- **2.9** Milestone check (PRD F1/F2): live account renders end-to-end on this Windows machine via REST with vault keys; demo unchanged. Manual verification script in `docs/verify/phase2.md`.

## Phase 3 — Trading core

- **3.1** `trading/limits.py`: pair precision/min-size from cached `AssetPairs`, max-order-value check. Pure, tested. *(PRD AC4.3)*
- **3.2** `trading/audit.py`: JSONL writer per BACKEND schema, asyncio-locked, month-rolled. Tested.
- **3.3** `trading/paper.py` PaperBroker: overlay positions/cash, market/limit/stop fills against marks, fee simulation, persisted state. Fully unit-tested (this is also the autotrader's future test harness). *(PRD AC4.2)*
- **3.4** REST order wrappers: `AddOrder` (incl. `close[*]` conditional close), `EditOrder`, `CancelOrder`, `CancelAll`, `GetWebSocketsToken` (requires 2.4). Fixture tests; **paper-mode-only until 3.8 passes**.
- **3.5** `trading/orders.py` OrderService: request model, validation pipeline, mode routing, UNKNOWN-state reconciliation, audit hooks. No UI yet; tested with fake broker + fake REST.
- **3.6** `ui/panels/ticket.py` + `ui/dialogs/order_confirm.py`: full order suite UI per APP_FLOW FL-4; buy/sell coloring per FRONTEND. *(PRD AC3.1, AC4.1)*
- **3.7** `ui/panels/orders.py`: open orders table, cancel/edit/cancel-all flows (FL-6); Positions "Close" pre-fill (AC3.3); paper/live badges; Trading menu + status-bar mode badge + typed-LIVE confirmation (FL-5).
- **3.8** Live-fire verification (owner at keyboard): one minimal real order cycle (place limit far from market → observe ack → cancel) with max-order cap at $10. Audit log inspected. Gate: only after 3.1–3.7 green.

## Phase 4 — Streaming

- **4.1** `data/ws.py` public: connect, subscribe ticker, heartbeat watchdog, backoff reconnect, dispatch to an in-process event bus. Tested against a fake server.
- **4.2** Positions/ticket live marks from stream; DELAYED badge + REST polling fallback. *(PRD AC5.1, AC5.3)*
- **4.3** Private stream: token fetch, `executions` subscription, order-store updates driving `orders_panel`; resync-on-reconnect. *(PRD AC3.4, AC5.2)*
- **4.4** PnL tick-flash + status-bar clock per FRONTEND motion rules.

## Phase 5 — Analytics expansion

- **5.1** `engine/performance.py`: equity curve from ledgers + OHLC, realized PnL per (asset, quote), round-trip builder, trade stats. Pure, exhaustively tested. *(PRD AC6.1–6.3)*
- **5.2** `ui/panels/performance.py`: equity curve + drawdown charts (pyqtgraph per FRONTEND), returns table, per-position performance table, stats row with footnotes.
- **5.3** `alerts/` package: pydantic rules, evaluator on the event bus, winotify sink, `alerts.json` persistence. *(PRD AC6.4)*
- **5.4** `ui/panels/alerts.py`: rule CRUD + fired history (FL-7).
- **5.5** `book_panel` depth ladder via WS `book` channel (FL-4 entry point; price-click pre-fills ticket price only).

## Phase 6 — Analyst expansion

- **6.1** `analyst/llm.py`: Anthropic client, tool-use over engine outputs (portfolio snapshot, risk, performance, recent normalized trades), explicit-action-only, cost display. Key from credentials.py. *(PRD F7)*
- **6.2** Analyst panel UI: commentary / trade-review / ask actions, streaming render, no-key hidden state (FL-8).
- **6.3** Optional `kraken mcp` path via WSL where available; feature-detected, never required.

## Phase 7 — Polish, packaging, ship

- **7.1** State-coverage pass: every panel's loading/error/empty states per FRONTEND; toasts; window-layout persistence.
- **7.2** Full test + lint sweep; manual APP_FLOW walkthrough (every FL-1…FL-10 branch) on the dev machine.
- **7.3** PyInstaller windowed one-dir build on Windows (`packaging/windows/cqd.spec`): app icon, version resource; verify keyring + winotify + Qt plugins inside the bundle.
- **7.4** Inno Setup installer script: Start-menu shortcut, uninstaller, `%LOCALAPPDATA%` untouched on uninstall (user data survives). *(PRD F9)*
- **7.5** Clean-VM test per PRD success metric 3 (fresh Windows VM → install → keys → paper order &lt; 5 min).
- **7.6** README refresh with real screenshots; tag `v2.0.0`.

## Expansion v2.1 — Adjustable workspace + premium UI + Bloomberg analytics

New initiative (started 2026-07-09), parallel to the pending Phase 6/7 work. Turns the fixed dock cockpit into a fully adjustable card-panel workspace, raises the visual bar above Kraken's desktop app, and surfaces an institutional-grade analytics suite. Same discipline: every step ends `pytest -q` green + `ruff` clean + CI green; engine additions are pure math with tests in the same commit; the money path (`OrderService` → validation → confirm → mode) is never touched.

### E0 — Encode the plan (docs + dependency) *(this step)*
- **E0.1** Add `PySide6-QtAds>=5.0` to `pyproject.toml` + `TECH_STACK.md` with the PySide6 lockstep-coupling note. *(done — QtAds 5.0.0 verified importing on py3.14.2 / PySide6 6.11.1)*
- **E0.2** Update `PRD.md` (F10 workspace, F11 premium UI, F12 analytics suite + acceptance criteria), `APP_FLOW.md` (workspace + new-panel flows), `FRONTEND_GUIDELINES.md` (card chrome, elevation tokens, per-panel header controls, candlestick/heatmap/depth-ladder specs, QtAds styling), and this file.

### E1 — Docking foundation (QtAds)
- **E1.1** Introduce a `CDockManager`-based workspace host in `main_window.py`; wrap each existing `Panel` in a `CDockWidget`. Preserve every existing signal wiring verbatim. Default layout ≈ the agreed mockup (Watchlist left; Chart+Book center; Ticket+Depth right; Holdings+Analytics bottom). *(FRONTEND: Layout)*
- **E1.2** Layout persistence: save/restore QtAds state to QSettings on close/open; **Reset layout** action; graceful fallback to the default layout when stored state is missing/incompatible.
- **E1.3** Named **perspectives** ("Trading", "Analysis", "Monitor") with save/load/delete in the View menu; ship the three presets. *(APP_FLOW FL-W1)*
- **E1.4** View-menu panel registry (show/hide/re-open every panel) rebuilt against QtAds `CDockWidget.toggleViewAction()`.
- *Tests:* perspective serialize/restore round-trip (pytest-qt), panel-registry completeness, default-layout builder.

### E2 — Premium visual system
- **E2.1** `ui-architect` audit → refined token set. Reconcile the doc token names with the implemented `Theme` dataclass and add elevation tokens (`surface`, `surface_raised`, `elevated`) + card shadow/border language.
- **E2.2** Card chrome: QtAds title-bar/tab/splitter styling matched to theme tokens; per-panel header controls (symbol selector, timeframe, settings gear) via an extended `PanelHeader`.
- **E2.3** Density + type pass across tables/metrics; sparklines; refined badges/pills; PnL tick-flash retained.
- *Tests:* `build_qss` covers the new QtAds selectors without raising; theme-switch smoke over all panels.

### E3 — Real charts + market microstructure panels
- **E3.1** Candlestick chart panel (pyqtgraph custom `CandlestickItem`): timeframe selector, volume subplot, cost-basis/break-even overlays, recent-fill markers, crosshair + mono tooltip. Replaces the `chart.py` placeholder. *(FRONTEND: Charts)*
- **E3.2** Order-book depth ladder redesign: cumulative colored depth bars (bid green / ask red), spread readout, price-click pre-fills ticket price only. *(reuses existing `book.py` data path)*
- **E3.3** New **Watchlist** panel: market, price, 24h %, volume, inline sparkline; selection drives the active symbol.
- **E3.4** New **Time & Sales / trades tape** panel from the WS `trade` channel.
- **E3.5** Active-symbol bus so chart / book / tape / ticket follow one selection.
- *Tests:* candlestick + volume data transforms, watchlist table model, active-symbol routing.

### E4 — Bloomberg analytics suite (all four tracks; each = pure engine + surfaced panel)
- **E4a Risk & ratios** — add `sortino_ratio`, `calmar_ratio`, `rolling_sharpe`, `rolling_vol` to `engine/`; surface with existing VaR/CVaR, beta-to-BTC, tail metrics in an Analytics panel section. *(PRD AC12.1)*
- **E4b Correlation & exposure** — asset correlation matrix + heatmap, per-holding risk contribution, allocation drift over time, crypto-sector exposure via the static sector map (TECH_STACK). *(PRD AC12.2)*
- **E4c Performance attribution** — per-asset contribution to total PnL, realized vs unrealized split, NAV history, weekly/monthly returns heatmap, benchmark comparison vs BTC. *(PRD AC12.3)*
- **E4d Scenario & stress** — historical shock replay (e.g. −30% BTC), Monte Carlo NAV projection, what-if position sizing, drawdown-recovery analytics. *(PRD AC12.4)*
- *Tests:* each new engine function gets exhaustive unit tests (known-input vectors, NaN/empty guards) in the same commit; panel render smoke.

### E5 — Polish + integration
- **E5.1** Per-panel loading/error/empty states for every new panel per FRONTEND.
- **E5.2** Perspective preset refinement, micro-interactions, empty-workspace handling.
- **E5.3** Full regression + lint sweep; manual APP_FLOW walkthrough of the new FL-W flows; CI green.

### Expansion dependency notes
E1 precedes everything (it is the host). E2 layers on E1. E3 panels register into the E1 workspace and use the E2 header controls. E4 reuses existing engine + `data/returns.py`; E4c needs ledger history (Phase 2.4, done). E5 requires E1–E4.

## Phase 8 — Deferred (post-v1 roadmap, not scheduled)

Autotrader host (strategy hooks onto OrderService + PaperBroker gates), multi-account profiles, trade-from-chart drag orders, historical USD conversion for crypto-quoted cost bases, risk-metric alert kinds if cut from 5.3, macOS packaging revival, more themes.

## Dependency notes

2.x requires 1.1/1.3/1.5 (REST client reuses the fixed normalizer + error discipline). 3.4+ requires 2.2/2.4. 3.6–3.8 require 3.1–3.5. 4.3 requires 3.4 (token) + 3.7 (orders panel). 5.1 requires 2.4 (ledgers). 5.5 requires 4.1. 6.x requires 2.1 (key storage). 7.3+ requires everything prior.
