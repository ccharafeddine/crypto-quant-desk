# Crypto Quant Desk

A native Windows desktop dashboard and trading terminal for a Kraken spot
account: live positions with true cost basis, portfolio-level risk (volatility,
BTC beta, concentration, risk contribution, tail metrics), performance
analytics, and a full order suite with hard safety rails. A Claude-powered
analyst panel narrates the numbers; the math engine computes them.

Personal-use software, MIT-licensed. It talks to Kraken's official APIs and
nothing else: no third-party price feeds, no telemetry, and your API keys never
leave your machine (they live in Windows Credential Manager, not on disk).

## Status

Under active overhaul from an earlier contest build. The canonical spec lives in
[`docs/`](docs/) (PRD, app flow, tech stack, frontend guidelines, backend
structure, implementation plan); current state is tracked in
[`progress.txt`](progress.txt).

## What it does

- **Positions** — live holdings, mark price, USD value, average cost,
  break-even, unrealized PnL (cost basis labeled per quote currency)
- **Risk** — annualized and EWMA volatility, BTC beta, HHI, effective bets,
  per-asset risk contribution, VaR/CVaR, with every assumption footnoted
- **Trading** — market/limit/stop/take-profit/trailing orders with a
  confirmation dialog, a paper-mode default, a max-order-value cap, and an
  append-only local audit log; open-orders management; live order state via
  WebSocket
- **Performance** — equity curve, realized/unrealized PnL history, drawdown,
  per-position stats, trade expectancy
- **Alerts** — price/PnL/risk rules with Windows notifications
- **Analyst** — rules-based narration for free; optional Claude analysis
  (your own Anthropic key) that narrates engine output and never invents numbers

## Safety model

- Paper mode is the default; going live requires an explicit, typed confirmation
- Every order passes validation, a size cap, and a confirmation dialog
- Every order attempt is audit-logged locally
- Kraken API keys need only query + trade permissions. **Never enable
  Withdraw Funds** — the app has no withdrawal code path and never will

## Quick start (dev, Windows)

Requires Python 3.11+.

```powershell
git clone https://github.com/ccharafeddine/crypto-quant-desk.git
cd crypto-quant-desk

python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"

python -m cqd
```

First launch opens in demo mode (synthetic portfolio, real market data). Connect
your own account via File > Settings with a Kraken API key created at
https://www.kraken.com/u/security/api (permissions: Query Funds, Query Open/
Closed Orders & Trades, Query Ledger Entries, Create & Modify Orders).

A packaged Windows installer is planned (implementation plan, Phase 7).

## Architecture

```
src/cqd/
├── engine/     # pure math: risk, metrics, cost basis, performance (no I/O)
├── data/       # Kraken REST + WebSocket clients, normalizer, credentials
├── trading/    # order service, paper broker, limits, audit log
├── alerts/     # rule engine + Windows notifications
├── analyst/    # rules narration + optional Claude integration
└── ui/         # PySide6 dockable panels, token-driven themes
```

Design rules: the engine is pure functions (fully tested, no I/O/Qt/network);
all market and portfolio data comes from Kraken's official APIs; every
order flows through one service with non-bypassable rails; no database —
settings, JSON state, and the OS credential vault.

## Development

```powershell
pytest -q                 # tests
ruff check src tests      # lint
ruff format src tests     # format
```

## License

MIT. See `LICENSE`.

## Disclaimer

Experimental software that can place real orders on your live Kraken account
when you enable live mode with trade-permissioned keys. Use at your own risk.
The authors accept no liability for financial losses.
