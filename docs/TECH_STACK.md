# TECH_STACK.md — Crypto Quant Desk v2

## Runtime

- **Language:** Python **3.11+** (developed on 3.14; 3.11 is the floor in `pyproject.toml`)
- **UI framework:** PySide6 (Qt 6) — native desktop. The workspace host is the **Qt Advanced Docking System** (`PySide6-QtAds`): every panel is a floatable/tabbable/freely-resizable card, and named layouts ("perspectives") save/restore to QSettings.
- **Async:** asyncio bridged into Qt via qasync
- **Target OS:** Windows 10/11 and macOS 11+ (both packaged and supported). Code stays importable on Linux. Native notifications: Windows toasts (`winotify`), macOS Notification Center (`osascript`), log fallback elsewhere.

## Dependencies (pyproject `dependencies`)

Versions are minimum pins (`>=`), matching the repo's existing convention; the lockstep source of truth is `pyproject.toml`. Anything not listed here must not be imported.

| Package | Pin | Purpose |
|---|---|---|
| `PySide6` | `>=6.7` | Qt UI |
| `PySide6-QtAds` | `>=5.0` | **new** — adjustable docking workspace (Qt Advanced Docking System). See coupling note below. |
| `qasync` | `>=0.27` | asyncio ↔ Qt event loop |
| `pyqtgraph` | `>=0.13` | Charts (candlesticks, equity curve, drawdown, heatmaps) |
| `pydantic` | `>=2.7` | Typed models for API payloads, alert rules, audit records |
| `pandas` | `>=2.2` | Engine math |
| `numpy` | `>=1.26` | Engine math |
| `diskcache` | `>=5.6` | Transient market-data cache |
| `python-dotenv` | `>=1.0` | Dev-only `.env` fallback |
| `anthropic` | `>=0.40` | Analyst panel (Claude) |
| `httpx` | `>=0.27` | Kraken REST client (async HTTP) |
| `websockets` | `>=12.0` | **new** — Kraken WebSocket v2 streams |
| `keyring` | `>=25.0` | **new** — OS credential store (Windows Credential Manager / macOS Keychain) |
| `winotify` | `>=1.1; sys_platform == 'win32'` | **new** — Windows toast notifications (macOS uses `osascript`, no dep) |

Dev (`[dev]` extra): `pytest>=8.0`, `pytest-asyncio>=0.23`, `pytest-qt>=4.4`, `ruff>=0.5`, `pyinstaller>=6.0`.

**`PySide6-QtAds` version coupling.** QtAds ships `abi3` wheels but hard-pins `PySide6-Essentials==<matching 6.x>` (5.0.0 → 6.11.1). Consequence: PySide6 and QtAds must be upgraded in lockstep; a PySide6 bump that outruns QtAds will fail to resolve. Verified installing and importing (`from PySide6QtAds import CDockManager`) on Python 3.14.2 + PySide6 6.11.1. The import name is `PySide6QtAds` (no hyphen); the distribution name is `PySide6-QtAds`.

**Crypto-sector classification (analytics).** Sector/exposure analytics (L1 / L2 / DeFi / meme / stablecoin, etc.) use a *static, in-repo* mapping of asset → sector — it is a fixed lookup table, not a third-party data feed, so it does not breach the Kraken-only data rule. Unmapped assets fall to an "Other" bucket. This map is the one piece of non-Kraken reference data allowed, and it ships no prices or fundamentals.

## Kraken connectivity

Two interchangeable backends behind one client protocol (`cqd.data.client`); panels never know which is active.

1. **`KrakenRESTClient` (primary, all platforms).**
   - Base: `https://api.kraken.com`
   - Public: `/0/public/Time`, `/0/public/Assets`, `/0/public/AssetPairs`, `/0/public/Ticker`, `/0/public/OHLC`, `/0/public/Depth`
   - Private (signed): `/0/private/Balance`, `/0/private/TradesHistory`, `/0/private/Ledgers`, `/0/private/OpenOrders`, `/0/private/ClosedOrders`, `/0/private/QueryOrders`, `/0/private/AddOrder`, `/0/private/EditOrder`, `/0/private/CancelOrder`, `/0/private/CancelAll`, `/0/private/GetWebSocketsToken`
   - Auth: `API-Key` header + `API-Sign` = base64(HMAC-SHA512(uri_path + SHA256(nonce + POST body), base64-decoded secret)). Implemented in-house (~20 lines); **no third-party Kraken SDK**.
   - Endpoint shapes must be verified against https://docs.kraken.com/api/ when each wrapper is written, not assumed.
2. **`KrakenWSClient` (streaming).**
   - Public: `wss://ws.kraken.com/v2` — channels `ticker`, `book`, `ohlc`
   - Private: `wss://ws-auth.kraken.com/v2` — channels `executions`, `balances`; token via REST `GetWebSocketsToken`
   - Reconnect with exponential backoff + resubscribe; heartbeat monitoring feeds the status bar.
3. **`KrakenClient` (legacy CLI subprocess, kept).** Works where the `kraken` binary exists (macOS/Linux/WSL). On Windows it is optional and only relevant for `kraken mcp` (analyst). Never a requirement.

Rate limits: private REST calls consume Kraken's per-tier counter; the client serializes private calls through a token-bucket (assume Starter tier: 15 max, −0.33/s decay) and surfaces `EAPI:Rate limit exceeded` as a typed error with retry-after backoff.

## Storage (no database — deliberate)

| Data | Where | Format |
|---|---|---|
| API keys (Kraken, Anthropic) | Windows Credential Manager via `keyring`, service `cqd`, entries `kraken-api-key`, `kraken-api-secret`, `anthropic-api-key` | vault-encrypted |
| UI prefs (theme, layout, paper-mode flag, max order value, dust threshold) | `QSettings` (registry: `HKCU\Software\CryptoQuantDesk\cqd`) | native |
| Alert rules | `%LOCALAPPDATA%\CryptoQuantDesk\alerts.json` | JSON, pydantic-validated |
| Paper simulator state | `%LOCALAPPDATA%\CryptoQuantDesk\paper_state.json` | JSON |
| Order audit log | `%LOCALAPPDATA%\CryptoQuantDesk\audit\orders-YYYYMM.jsonl` | JSON Lines, append-only |
| Market-data cache | `%LOCALAPPDATA%\CryptoQuantDesk\cache\` (diskcache; moves from `~/.cqd/cache`) | binary |

A real DB (SQLite) is a considered-and-rejected option until the autotrader needs transactional state; JSONL + vault covers v1.

## Auth model

No user accounts. "Auth" = Kraken API key pair with permissions: Query Funds, Query Open/Closed Orders & Trades, Query Ledger Entries, Create & Modify Orders. **Withdraw Funds must never be enabled, requested, or used.**

## Third-party services

| Service | Endpoint | Purpose | Required? |
|---|---|---|---|
| Kraken REST | `https://api.kraken.com` | account + market data, orders | yes |
| Kraken WebSocket v2 | `wss://ws.kraken.com/v2`, `wss://ws-auth.kraken.com/v2` | streaming | yes (REST fallback exists) |
| Anthropic API | `https://api.anthropic.com` (via `anthropic` SDK), model `claude-opus-4-8` (`cqd.analyst.llm.DEFAULT_MODEL`) | analyst panel | optional |
| Kraken CLI / `kraken mcp` | local binary (WSL on Windows) | analyst MCP data path | optional |

No other network calls, ever. No telemetry, no update checks, no analytics.

## Dev tools

- **Lint/format:** ruff (`ruff check src tests`, `ruff format src tests`), line length 100, target py311
- **Tests:** pytest + pytest-asyncio (`asyncio_mode = auto`) + pytest-qt for panel tests; run `pytest -q`
- **Packaging:** PyInstaller (windowed, one-dir) on both OSes. Windows: **Inno Setup 6** installer in `packaging/windows/`. macOS: `.app` via `packaging/crypto-quant-desk.spec`, `.dmg` via `packaging/build_dmg.sh` (unsigned).

## Explicitly forbidden

| Forbidden | Why |
|---|---|
| `ccxt`, `yfinance`, CoinGecko/CMC clients, any third-party price feed | Kraken's official APIs are the only market/portfolio data sources; one source of truth for a trading terminal |
| `krakenex`, `pykrakenapi`, any Kraken SDK wrapper | signing is trivial in-house; avoids an unmaintained dependency in the money path |
| `requests` (sync) in app code | all I/O is async (qasync); sync HTTP would block the UI thread |
| GUI web stacks (Electron, Streamlit, Flask/FastAPI UI) | this is a native Qt app |
| ORM/DB engines (SQLAlchemy, sqlite3 as app store) | no database in v1 (see Storage) |
| `kraken auth set` / writing keys to CLI config | keys go to the CLI via subprocess env only |
| Any package not in `pyproject.toml` | no hallucinated dependencies; adding one requires editing this file + pyproject in the same change |
