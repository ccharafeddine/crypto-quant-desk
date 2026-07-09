# PRD.md — Crypto Quant Desk v2

## Project

**Crypto Quant Desk** — a Windows-native desktop dashboard and trading terminal for a personal Kraken spot account, with built-in quant analytics that Kraken's own UI does not offer, designed to later host an autotrader.

Version covered by this PRD: **v1 of the overhaul** (internally "v2" of the app, succeeding the contest-era v0.x).

## Target user

One persona, the only user:

- **The owner-operator.** Trades his own Kraken spot account, quant-literate (understands vol, beta, drawdown, expectancy), works on Windows, wants one screen that shows what Kraken's dashboard shows PLUS portfolio-level risk and performance stats, and wants to place/manage orders without switching to Kraken's web UI. Plans to run a systematic autotrader against a funded/prop-style account later; this app is its future cockpit.

No multi-user support, no accounts system, no telemetry. The app may be installed on several of the owner's devices; therefore **nothing private (keys, balances, strategy logic) may live in the repo or the installer** — private state lives only in the OS credential vault and local app data on each machine.

## Problem

1. Kraken's dashboard shows balances and orders but no portfolio risk (vol, beta, concentration, tail risk), no true cost basis, no equity curve, no drawdown/expectancy stats.
2. Trading on Kraken's web UI and analyzing in spreadsheets/scripts splits the workflow across tools.
3. The existing contest-era app is macOS-targeted, view-only, and depends on a CLI binary that does not ship for Windows.

## Features and acceptance criteria

### F1. Windows-native data layer (REST primary)
The app runs natively on Windows with no WSL requirement for core function.
- **AC1.1** `python -m cqd` launches on Windows 10/11 and renders live account data with valid keys.
- **AC1.2** All account/market data flows through a `KrakenRESTClient` (native HTTPS, signed) behind the same client protocol the panels already consume; the CLI client remains available as an alternate backend on platforms that have it.
- **AC1.3** Demo mode (no keys) still works and is clearly badged.
- **AC1.4** No WSL, no `kraken` binary needed for anything except the optional MCP/analyst path.

### F2. Settings dialog + Credential Manager key storage
- **AC2.1** A Settings dialog (menu: File > Settings) accepts Kraken API key/secret and Anthropic API key, verifies the Kraken pair with a live `Balance` call, and stores them via `keyring` in Windows Credential Manager.
- **AC2.2** No key is ever written to disk in plaintext, logged, or displayed after entry (masked fields).
- **AC2.3** The app reads keys from Credential Manager at startup; `.env` remains a dev-only fallback and is gitignored.
- **AC2.4** A "Disconnect" action deletes stored credentials from the vault.

### F3. Full order suite (spot)
- **AC3.1** Order ticket panel supports: market, limit, stop-loss, stop-loss-limit, take-profit, take-profit-limit, trailing-stop; buy and sell; base-quantity or quote-value entry; optional attached conditional close (TP/SL on entry).
- **AC3.2** Open Orders panel lists working orders live and supports cancel (single and all) and edit (price/volume).
- **AC3.3** Positions panel gains a "Close" action that pre-fills a market sell of the full position into the ticket (never auto-submits).
- **AC3.4** Order state changes (submitted, open, filled, cancelled, rejected) surface in the UI within 2 s via WebSocket, without manual refresh.

### F4. Trade safety rails (all mandatory, none bypassable from the UI)
- **AC4.1** Every submission shows a confirmation dialog: pair, side, type, quantity, price(s), estimated cost incl. fee, paper/live badge. Two clicks minimum to send any order.
- **AC4.2** Global **paper mode** toggle: when on, orders route to a local simulator (fills against live marks) and are visibly badged "PAPER"; when off, orders go to Kraken. Mode is unmissable in the status bar. Default on first run: paper.
- **AC4.3** Configurable **max order value** (USD, default 500): any ticket exceeding it is blocked with an explanatory error, in paper and live alike.
- **AC4.4** **Audit log**: every order attempt and outcome appended as one JSON line to a local file (never in the repo), including mode, request, response/error, timestamps.
- **AC4.5** The app never requests or uses withdrawal permission; no withdrawal code path exists.

### F5. Live streaming (WebSocket)
- **AC5.1** Live mark prices for held assets and the selected pair stream via Kraken WebSocket v2; Positions and ticket update without polling.
- **AC5.2** Own-order and own-trade events stream via the authenticated WebSocket (`executions`), driving F3's order state UI.
- **AC5.3** Disconnects auto-reconnect with backoff; staleness is indicated (badge "delayed" when >10 s without a heartbeat); REST polling is the fallback.

### F6. Analytics expansion
Existing engine (vol incl. EWMA, BTC beta, HHI, effective bets, risk contribution, tail metrics, cost basis) stays and remains pure. Added:
- **AC6.1** **Equity curve + PnL**: portfolio value over time reconstructed from ledger/trade history + OHLC; realized and unrealized PnL; daily/weekly/monthly return table. Rendered in a Performance panel.
- **AC6.2** **Per-position performance**: per asset — realized PnL, unrealized PnL, fees paid, holding period, win rate of closed round-trips.
- **AC6.3** **Drawdown + trade stats**: max drawdown, current drawdown, recovery days, per-trade avg win/avg loss, expectancy, profit factor.
- **AC6.4** **Alerts**: user-defined rules (price above/below, position PnL threshold, portfolio drawdown threshold, risk-metric breach) checked against streaming data, firing Windows toast notifications and an in-app alerts list. Alert definitions persist locally.
- **AC6.5** Every displayed metric carries its assumption footnote (365-day annualization, simple returns, EWMA λ=0.94, quote-currency cost-basis caveat).

### F7. AI analyst (expand)
- **AC7.1** The current rules-based narration remains the zero-cost default.
- **AC7.2** With an Anthropic key configured, the Analyst panel offers Claude-powered analysis: portfolio commentary, trade review ("what did I do this week and what did it cost me"), and risk Q&A, via tool-use over the app's own engine outputs (and `kraken mcp` where WSL is available — optional, never required).
- **AC7.3** The AI narrates numbers computed by the engine; it never computes or invents them. No AI call is made without explicit user action (button press), and cost per call is displayed.

### F8. Debranding + themes
- **AC8.1** No $DOG references in UI, docs, or code identifiers. The orange theme survives renamed "Amber"; "Slate" becomes the default; "Teal" remains. Theme registry and live switching (View > Theme) stay.
- **AC8.2** Fonts resolve correctly on Windows (Segoe UI for prose, Cascadia Mono/Consolas for numerics) with macOS fallbacks intact.

### F9. Windows packaging
- **AC9.1** PyInstaller builds a windowed one-dir app; an Inno Setup script produces a standard installer (Start-menu shortcut, uninstaller).
- **AC9.2** The installed app is self-contained (no Python required) and stores state only under `%LOCALAPPDATA%\CryptoQuantDesk` and Credential Manager.
- **AC9.3** The installer contains no credentials, no personal data, no strategy code.

## User stories

- As the owner, I want my Kraken keys stored in Windows Credential Manager via a Settings dialog, so that no plaintext secret exists on disk or in the repo.
- As the owner, I want to see my live positions with cost basis, break-even, and unrealized PnL, so that I know where I stand without a spreadsheet.
- As the owner, I want to place a limit order with an attached stop-loss from an order ticket with a confirmation step, so that I can trade without Kraken's web UI and without fat-finger risk.
- As the owner, I want a paper-mode switch, so that I can test the terminal (and later the autotrader) without risking real money.
- As the owner, I want every order attempt logged locally, so that I can audit what the app (or a future bot) did.
- As the owner, I want an equity curve, drawdown, and expectancy stats, so that I can judge my trading the way a prop firm would.
- As the owner, I want price and risk alerts as Windows notifications, so that I don't have to watch the screen.
- As the owner, I want to ask Claude "review this week's trades," so that I get a narrative over numbers the engine computed.
- As the owner, I want a Windows installer, so that I can put the app on another machine in minutes with zero private data inside it.

## Success metrics ("done" for v1)

1. Daily-driver test: the owner can go a full trading week using only this app for monitoring and order management on Kraken spot.
2. All acceptance criteria above pass; `pytest -q` green; `ruff check` clean.
3. Fresh-machine test: installer on a clean Windows VM → enter keys → live dashboard and a paper order in under 5 minutes, no WSL, no Python.
4. Repo hygiene: no secrets, balances, or private-strategy references anywhere in tree or history going forward; audit log and state live outside the repo.

## Non-goals (explicitly out of scope for v1)

- The autotrader itself (strategies, signals, scheduling). v1 only ships the interfaces it will use: order service, paper engine, audit log.
- Multi-account profiles.
- Trade-from-chart (click/drag order placement) — roadmap phase 4+.
- Futures, margin, staking/earn actions. Spot only. (Staked sub-balances still display, folded into base assets.)
- macOS/Linux packaging (code stays cross-platform; packaging effort is Windows-only).
- Mobile, web, or any remote/served UI.
- Withdrawals — permanently out of scope, not just v1.
- Historical USD conversion of crypto-quoted cost bases (quote-aware labeling stays, per the known caveat).

## Scope boundary

v1 ends when F1–F9 meet their acceptance criteria and the success metrics pass. Alerts on risk-metric breaches (AC6.4) may ship as a fast-follow if the alert engine lands late, but price and PnL alerts are v1-blocking.
