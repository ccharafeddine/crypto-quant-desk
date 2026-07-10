# APP_FLOW.md — Crypto Quant Desk v2

Desktop Qt app: "screens" are adjustable card panels inside one workspace (Qt Advanced Docking System over a `QMainWindow`), plus modal dialogs. Every panel can be dragged, split, tab-stacked, floated, resized, hidden, and reopened; layouts save/restore as named perspectives (FL-W1). No URL routes; identifiers below are the canonical names used in code (`ui/panels/*`, `ui/dialogs/*`).

## Screen inventory

| ID | Kind | Module | Purpose |
|---|---|---|---|
| `main_window` | window | `ui/main_window.py` | Shell: menu bar, status bar, QtAds workspace host, perspectives |
| `watchlist_panel` | dock panel | `ui/panels/watchlist.py` (new, E3) | Markets: price, 24h %, volume, sparkline; selection sets the active symbol |
| `positions_panel` | dock panel | `ui/panels/positions.py` | Holdings, marks, USD value, cost basis, break-even, unrealized PnL, Close action |
| `risk_panel` | dock panel | `ui/panels/risk.py` | Vol, EWMA vol, BTC beta, HHI, effective bets, risk contribution, tail metrics + footnotes |
| `chart_panel` | dock panel | `ui/panels/chart.py` | Candlestick + volume of the active symbol, cost-basis/break-even overlays, fill markers (pyqtgraph) |
| `tape_panel` | dock panel | `ui/panels/tape.py` (new, E3) | Time & sales: streaming trades (price, size, side, time) for the active symbol |
| `analytics_panel` | dock panel | `ui/panels/analytics.py` (new, E4) | Bloomberg-grade suite: ratios, correlation/exposure, attribution, scenario/stress |
| `performance_panel` | dock panel | `ui/panels/performance.py` (new) | Equity curve, PnL history, drawdown, trade stats, per-position performance |
| `ticket_panel` | dock panel | `ui/panels/ticket.py` (new) | Order entry: pair, side, type, qty, price(s), TP/SL, submit |
| `orders_panel` | dock panel | `ui/panels/orders.py` (new) | Open orders (cancel/edit), recent fills |
| `book_panel` | dock panel | `ui/panels/book.py` (new, phase 4) | Live depth ladder for selected pair |
| `analyst_panel` | dock panel | `ui/panels/analyst.py` | Rules-based narration + Claude actions |
| `alerts_panel` | dock panel | `ui/panels/alerts.py` (new) | Alert rules list, add/remove, fired-alert history |
| `settings_dialog` | modal | `ui/dialogs/settings.py` (new) | Keys, paper mode default, max order value, data source, theme |
| `order_confirm_dialog` | modal | `ui/dialogs/order_confirm.py` (new) | Order summary + confirm/cancel |
| `first_run_dialog` | modal | `ui/dialogs/first_run.py` (new) | Welcome: connect keys or continue in demo |

Status bar (always visible): connection state (`LIVE ● / DELAYED ● / OFFLINE ●`), mode badge (`PAPER` amber / `LIVE` red), data source (`REST / DEMO`), last-update clock.

Menus: **File** (Settings, Disconnect account, Exit) · **View** (panel visibility toggles, Perspectives submenu [Trading/Analysis/Monitor + Save/Delete], Reset layout, Theme submenu) · **Trading** (Paper mode toggle, Cancel all orders) · **Help** (About, Open audit log folder).

## Flows

### FL-1 First launch (no credentials)
1. App starts → `keyring` lookup finds no Kraken keys → `first_run_dialog`.
2. Branch A "Connect account" → opens `settings_dialog` (FL-2).
3. Branch B "Explore in demo" → DemoClient; status bar shows `DEMO`; all trading actions route to paper mode with demo book; banner on `ticket_panel`: "Demo account — orders are simulated."
4. Success: main window renders with data. Failure (no network in demo): panels show cached/empty states with "offline" placeholders, retry button.

### FL-2 Connect account (Settings)
1. File > Settings → `settings_dialog`, Keys tab.
2. User pastes Kraken API key + secret (masked inputs) → "Verify & Save".
3. App calls private `Balance` once.
   - Success → keys written to Credential Manager, dialog shows green check, client switches to live REST, all panels refresh. Status bar → `REST`, mode stays PAPER until user flips it (FL-5).
   - Auth failure (`EAPI:Invalid key`) → inline error "Kraken rejected the key pair", keys NOT saved.
   - Permission gap (balance ok, later order rejected `EGeneral:Permission denied`) → error surfaces at order time with hint to enable "Create & Modify Orders".
   - Network failure → inline error, keys NOT saved, offer retry.
4. Optional: Anthropic key field, same pattern (verified with a minimal ping on save; analyst features stay hidden without it).
5. "Disconnect account" (File menu) → confirm dialog → keys deleted from vault → app drops to demo mode.

### FL-3 Monitor portfolio (default loop)
1. On connect: REST snapshot (balance, open orders, trades history) → panels render; WebSocket subscribes (ticker for held assets + selected pair; authed `executions`).
2. Ticks update marks/PnL in place (no full-table rebuild). Heartbeat gap >10 s → status `DELAYED`, REST poll fallback every 30 s until stream recovers.
3. Empty account (all balances < $1 dust threshold) → positions panel empty state: "No positions above $1. Deposit or lower the dust threshold in Settings."
4. Engine failures (e.g., too little history for risk metrics) → risk panel renders what it can, per-metric `n/a` with footnote, never a crash.

### FL-4 Place an order
1. Entry points: `ticket_panel` directly; or `positions_panel` row → "Close" (pre-fills market sell of position); or (phase 4) `book_panel` price click pre-fills limit price.
2. User picks pair → ticket shows live bid/ask/last; qty entered in base or quote (toggle); order type reveals relevant price fields (limit price, trigger, trailing offset); optional conditional close (TP/SL prices).
3. Client-side validation (before any network call): qty > pair minimum, price > 0, decimals within pair rules, **order value ≤ max-order cap**. Violations disable Submit with inline reason.
4. Submit → `order_confirm_dialog`: pair, side, type, qty, price(s), estimated cost + est. fee, big mode badge (PAPER amber / LIVE red).
5. Confirm →
   - **Paper**: simulator accepts → order appears in `orders_panel` badged PAPER; fills simulated against live marks (market: immediate at mark ± configurable slippage; limit/stop: when mark crosses). Audit-logged.
   - **Live**: REST `AddOrder` → pending spinner (button disabled, no double-submit) →
     - Accepted: txid shown, order appears via `executions` stream. Audit-logged.
     - Rejected (`EOrder:Insufficient funds`, `EOrder:Invalid price`, etc.): dialog shows Kraken's error verbatim + plain-English hint; nothing retried automatically. Audit-logged.
     - Timeout/ambiguous (sent, no response): warning state "Order status unknown — reconciling", app queries `OpenOrders`/`QueryOrders` until resolved. Audit-logged as `unknown→resolved:<state>`.
6. Success state: toast "Order placed: BUY 0.05 BTC @ 60,000 (PAPER)"; ticket clears price fields, keeps pair.

### FL-5 Toggle paper/live
1. Trading > Paper mode (checkable) or status-bar badge click.
2. Paper → Live: confirm dialog "Orders will be sent to Kraken with real funds." requires typing `LIVE` to confirm. Blocked (with explanation) if no keys or key lacks trade permission.
3. Live → Paper: immediate, no confirmation.
4. Mode persists across restarts (QSettings); paper simulator state (open paper orders/fills) persists in local app data.

### FL-6 Manage open orders
1. `orders_panel` lists working orders (live + paper clearly separated by badge).
2. Cancel row → confirm (skippable per-session checkbox "don't ask again for cancels") → REST `CancelOrder` / simulator remove → row drops on `executions` event. Failure: error toast, row marked stale, refresh forced.
3. Edit row → ticket pre-filled in "amend" mode → confirm dialog → `EditOrder`. On rejection, original order remains and the error is shown.
4. Trading > Cancel all orders → confirm with count → `CancelAll`. Result toast with cancelled count.

### FL-7 Alerts
1. `alerts_panel` → "New alert" → inline form: type (price above/below, position PnL %, portfolio drawdown %, risk metric threshold), pair/asset if applicable, threshold, one-shot vs repeating.
2. Rules persist to local app data (JSON). Evaluated on every tick/refresh.
3. Fire → Windows toast + row in fired-history (timestamp, rule, value). One-shot rules disable themselves.
4. Edge: alert on an asset later sold → rule kept but flagged "inactive (not held)". Invalid threshold input → inline validation, not saved.

### FL-8 Analyst
1. Panel always shows rules-based narration (free, local) after each portfolio refresh.
2. With Anthropic key: buttons "Portfolio commentary", "Review recent trades", "Ask…" (free-text).
3. Press → request built from engine outputs (never raw keys, never full trade history beyond what the question needs) → streamed response rendered; token cost shown after completion.
4. No key → buttons hidden, hint "Add an Anthropic key in Settings to enable AI analysis."
5. API error → inline error with retry; never blocks the rest of the app.

### FL-9 Theme switch
View > Theme > {Slate (default), Amber, Teal} → QSS rebuilt and applied live → choice persisted (QSettings). No data effects.

### FL-10 Shutdown
Exit → if live orders were placed this session, no special handling (orders live on Kraken); if paper fills are pending write, flush simulator state; WebSocket closed cleanly; window layout saved (QSettings).

### FL-W1 Arrange the workspace (adjustable panels + perspectives)
1. User drags a panel's title bar → QtAds shows drop zones (edges + center tab targets) → release docks/splits/tabs it; drag beyond the window floats it as its own top-level window.
2. Drag a splitter between panels → both resize live (width and height); double-click a title bar toggles float.
3. View > panel name → hides/shows that panel; a closed panel keeps its state and re-docks to its last position (or default if none).
4. View > Perspectives > {Trading, Analysis, Monitor} → applies a saved arrangement live. "Save perspective…" stores the current arrangement under a name; "Delete" removes a custom one (presets are not deletable).
5. View > Reset layout → confirm → restores the default arrangement.
6. On exit the current arrangement is saved to QSettings; on next launch it restores. A saved layout that fails to deserialize (version/panel mismatch) silently falls back to the default; the app never fails to open because of a bad layout.

### FL-W2 Select the active symbol
1. Click a row in `watchlist_panel` (or pick a pair in `ticket_panel`) → that becomes the active symbol.
2. `chart_panel`, `book_panel`, and `tape_panel` all switch to the active symbol; the ticket's pair follows unless the user has locked it (pair combo edited manually).
3. The stream subscribes to the new symbol's ticker/book/trade channels and unsubscribes the old ones it no longer needs; held-asset marks always stay subscribed regardless of the active symbol.

### FL-W3 Analytics suite
1. `analytics_panel` presents sections: Ratios, Correlation & exposure, Attribution, Scenario & stress (tabs or a scrollable stack).
2. Each section pulls engine-computed values off the current portfolio snapshot + history; every metric carries its footnote. Insufficient history → per-metric `n/a` with the reason, never a crash.
3. Scenario & stress controls (shock size, Monte Carlo horizon/paths, what-if position delta) are inputs that re-run the pure engine and redraw; nothing touches live data or the money path.

## Redirect logic summary

- After key save: stay in Settings with success state; panels refresh behind dialog.
- After order confirm: return to ticket (success toast) or stay in dialog (error shown) — user decides next step.
- After disconnect: drop to demo mode, `first_run_dialog` NOT reshown.
- After any fatal data-layer error: app stays up, affected panel shows error state with retry; only a corrupt local state file prompts a "reset local state?" dialog.
