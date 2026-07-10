# CLAUDE.md — crypto-quant-desk v2

Project layer over the global `~/.claude/CLAUDE.md`. The contest era is over; this is now a personal cross-platform (Windows & macOS) Kraken spot dashboard and trading terminal, and the future cockpit for an autotrader.

## Source of truth

The six canonical docs in `docs/` define the product. Read the relevant one before implementing; if a task contradicts them, update the doc in the same change or stop and flag it.

- `docs/PRD.md` — features, acceptance criteria, non-goals
- `docs/APP_FLOW.md` — panels, dialogs, every user flow incl. error states
- `docs/TECH_STACK.md` — dependencies (exact pins), Kraken endpoints, forbidden packages
- `docs/FRONTEND_GUIDELINES.md` — theme tokens, typography, component patterns
- `docs/BACKEND_STRUCTURE.md` — service contracts, storage schemas, defect list, edge cases
- `docs/IMPLEMENTATION_PLAN.md` — numbered build order; work the next unchecked step

Session habit: read `progress.txt` and `docs/lessons.md` at session start; update `progress.txt` after completing any step; add to `docs/lessons.md` after any correction.

## Architecture in one paragraph

PySide6 + qasync desktop app. Panels talk to services; services talk to Kraken. Primary backend is `KrakenRESTClient` (native HTTPS, in-house signing) plus `KrakenWSClient` (WebSocket v2 streaming); the legacy CLI subprocess client remains an alternate backend where the `kraken` binary exists (never required on Windows). `src/cqd/engine/` is pure math — no I/O, no Qt, no subprocess, ever; every engine change ships with tests. `src/cqd/trading/` is the only path that can move money. No database: QSettings + JSON files under the per-user data dir (`%LOCALAPPDATA%\CryptoQuantDesk` on Windows, `~/Library/Application Support/CryptoQuantDesk` on macOS) + the OS credential store.

## Hard guardrails

- **Data sources:** Kraken official APIs (REST/WS/CLI) and nothing else for market/portfolio data. No ccxt, yfinance, or any third-party feed. Anthropic API only for the analyst panel, narrating engine-computed numbers.
- **Money path:** every order goes through `OrderService` → validation → confirmation dialog → mode routing. Never add a bypass. Paper mode is the default; live mode requires typed confirmation. Max-order-value cap applies in both modes.
- **Withdrawals:** no code path may construct, wrap, or call any `Withdraw*` endpoint. Never request withdraw permission. Permanent.
- **Secrets:** keys live in the OS credential store (Windows Credential Manager / macOS Keychain) via `data/credentials.py` (the only module allowed to touch key material); `.env` is a gitignored dev fallback. No key in logs, exceptions, audit entries, argv, or LLM prompts. Only `.env.example` is committed.
- **Privacy:** this repo is public and may be installed on other devices. No real balances, trade amounts, txids, account details, or private-strategy references in code, tests, fixtures, or docs. Demo/test data is synthetic only. The autotrader's strategy logic will live in a separate private repo; only its interfaces (order service, paper broker, audit log `source` field) exist here.
- **Symbols:** Kraken-native handling per `data/normalize.py` (classic X/Z codes on output, `BASE/QUOTE` slash form internally). Annualization 365; simple returns; EWMA λ=0.94; footnote conventions in the UI.
- **Known caveat:** non-USD-quoted cost bases are labeled in their quote currency, never silently converted or summed as USD.

## Commands

```
pip install -e ".[dev]"     # setup
git config core.hooksPath .githooks   # once per clone: pre-commit lint+tests
python -m cqd               # run
pytest -q                   # tests (must pass before any commit)
ruff check src tests        # lint
ruff format src tests       # format
# packaging: PyInstaller + Inno Setup, see packaging/windows/ (Phase 7)
```

## Testing discipline

Every behavior change ships with tests in the same commit; new modules get a
test file from the start. The pre-commit hook (.githooks) runs ruff + pytest
locally; GitHub Actions (.github/workflows/ci.yml) runs the same gate on every
push. Never merge or push over a red CI; never skip the hook except for a
genuine emergency (--no-verify), and fix the breakage in the next commit.

## Style

Match the global config: no em dashes, no filler, comments explain why not what. When something is wrong, say so and show the fix.
