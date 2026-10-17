# Crypto Quant Desk

A desktop trading terminal for a Kraken spot account, built so I can monitor, trade
and stress my own book in one window, with the risk work a desk would do and
Kraken's own app does not.

**Stack:** Python 3.12+, PySide6 (Qt), Qt Advanced Docking System, pandas, numpy,
pydantic, pyqtgraph, Kraken REST + WebSocket v2. Runs on Windows (primary, with an
installer) and macOS. 548 tests; CI on Windows and macOS for Python 3.12 and 3.13.

---

## Overview

Kraken's dashboard shows balances and orders. It does not show portfolio risk,
true cost basis, time-weighted performance or what a past crash would do to the
book you hold today. Crypto Quant Desk adds all of that to a full order terminal,
across six pages: Portfolio, Markets, Trading, Signals, Risk and Insight. You can
drag, tab, float and resize every panel, and the app saves each page's layout.

Every number comes from a pure, tested engine fed only by Kraken data. The UI
displays and the optional AI analyst narrates, but neither one computes a
figure. Trading starts in paper mode. No code path leads from a signal to an
order, and no code path leads to a withdrawal.

## Features

- **Portfolio**: time-weighted equity curve against BTC buy-and-hold, with
  deposits and withdrawals stripped out. Positions show cost basis that includes
  fees, plus break-even and realized and unrealized P&L. A KPI strip shows NAV,
  P&L, 1-year TWR, vol, BTC beta, one-day VaR and max drawdown.
- **Markets**: watchlist with sparklines, candlestick chart with volume and
  cost-basis overlays, a live depth ladder and time and sales, streamed over
  WebSocket.
- **Trading**: every Kraken spot order type, including an attached take-profit
  or stop-loss. Open orders can be cancelled or edited. Paper mode simulates
  fills against live marks.
- **Signals**: a trend setup sized to a fixed share of equity at risk, then
  capped by leverage and by the free balance. An order-book read (microprice,
  imbalance, estimated slippage) gives a GO, WAIT or CAUTION verdict for timing,
  never for direction. The backtest is shown net of costs next to its
  frictionless twin, with a live track record recorded net of fees.
- **Risk lab**:
  - One-day VaR four ways, plus ES 97.5%, each backtested with Kupiec and
    Christoffersen tests.
  - Crash replays from real Kraken trades.
  - A liquidity table (days to exit at 10% of volume).
  - Attribution against BTC.
  - Drawdown episodes.
  - FIFO, HIFO and average-cost tax lots.
  - A parameter-stability heatmap.
- **Analytics and Insight**: Sharpe, Sortino and Calmar using a real risk-free
  rate, a correlation matrix, risk contribution, a monthly returns heatmap and a
  Monte Carlo fan. A rules-based narrative is always shown. With your own key, an
  AI analyst can comment, but only on the engine's numbers.
- **Alerts**: price, P&L, drawdown and risk-metric rules, delivered as desktop
  notifications.

## Methodology

- **Closed bars only.** Kraken's OHLC always ends with the candle still forming.
  Every statistic and signal drops it, so intraday moves never leak into vol, VaR
  or signals. Only the chart draws the live bar.
- **Time-weighted returns.** Deposits and withdrawals are valued at the same
  daily marks as the account and removed with a Modified Dietz weight. Returns
  are then chain-linked, so a deposit is never counted as a gain.
- **Two risk views, labelled.** The Risk card is ex-ante: today's weights over
  closed daily bars. Analytics is realized history. Each card's footnote names
  its window, which is why their vols can differ.
- **VaR set.**
  - VaR models: historical, filtered historical (EWMA, λ 0.94), parametric normal
    and Cornish-Fisher.
  - ES 97.5%: historical, filtered and normal.
  - Backtest: a rolling 250-day window, one day ahead at 99%. Each method shows
    its exception count against the expected count, a Kupiec proportion-of-failures
    p-value, a Christoffersen independence p-value and a pass/fail verdict.
- **Crash replays.** Four windows rebuilt from Kraken's public trade history:
  LUNA (May 2022), FTX (Nov 2022), the 5 Aug 2024 carry unwind and the 10 Oct
  2025 liquidation cascade. The OHLC endpoint keeps only 720 bars, so it could
  not supply them. An asset with no history in a window is proxied and labelled.
  A stablecoin depeg shock and a 30-day block-bootstrap NAV fan complete the tab.
- **Attribution.** Active return against BTC splits exactly into allocation
  (cash versus invested), BTC beta and selection, and the three parts sum to the
  active return.
- **Backtest.**
  - Signals are taken on closed bars and fill at the next open plus slippage.
  - A stop or target that gaps through fills at the open.
  - Fees and slippage match the paper broker (40 bps taker, 5 bps).
  - Parameters come from an anchored walk-forward that is chosen in-sample and
    traded out-of-sample. Results are reported in R.
  - The stability heatmap shows whether the chosen parameters sit on a plateau or
    on a lone bright cell.
- **Tax lots.** FIFO, HIFO and average cost appear side by side. Realized plus
  unrealized is the same under every method, and a test enforces it. The figures
  are arithmetic, not tax advice.

## Risk controls

- Paper mode is the default. Going live requires typing `LIVE`.
- Every order passes through a confirmation dialog with an estimated cost and
  fee, and needs two clicks at minimum.
- A max-order-value cap (default $500) applies in both paper and live mode. It
  fails closed when there is no price. It is re-checked at send time, together
  with the trading mode, so a change after the confirm dialog refuses the order
  rather than resizing it.
- Orders are submitted with a single-use token from the confirm step. Private
  endpoints are allowlisted, and none of them can withdraw.
- Every order attempt and its outcome go to a local JSONL audit log, kept
  outside the repo, with no key material.
- Signals only pre-fill the ticket. Nothing turns a signal into an order, and a
  test enforces that.
- Keys live in the OS credential vault (Windows Credential Manager or macOS
  Keychain), never on disk in plaintext.
- These rails have their own tests. No change in this round loosened any of
  them.

## Architecture

```
cqd/
  data/     Kraken REST + WebSocket clients, demo client, normalisation,
            ledger-derived portfolio, returns, crash history, track record
  engine/   pure functions: risk, metrics, var, stress, attribution, tax_lots,
            cost_basis, performance, backtest, signals, microstructure, scenario
  trading/  order service (prepare -> confirm -> send), paper broker,
            limits, audit log
  analyst/  rules-based narration + optional LLM over engine outputs
  alerts/   rule engine + desktop notifications
  ui/       PySide6 shell: nav rail, KPI strip, six pages of QtAds dock panels,
            Graphite theme tokens
```

- The engine is pure. It takes plain data and returns dataclasses, imports
  nothing from the UI or network, and is tested with hand-computed vectors.
- Network work runs on an asyncio loop shared with Qt (qasync). Heavy analytics
  run off the UI thread. Panels load when first shown.
- Demo mode uses a sample book with live Kraken public data, so the whole app
  works without keys.
- Packaging is PyInstaller with an Inno Setup installer that contains no
  credentials or personal data. Dependencies are locked in `uv.lock`.

## What I fixed and why

An audit of the app's own math found numbers that looked right but were wrong.
I fixed them one commit at a time, each with a test that fails on the old
behaviour:

- **Deposits counted as performance.** Returns came from `equity.pct_change()`,
  so a deposit read as a gain. A flat cash account with one deposit showed a
  7,183% annual return and a Sharpe of 2.49. Returns are now time-weighted with
  flows removed.
- **The forming candle fed the statistics.** Kraken's last OHLC row is still
  live, so intraday moves leaked into vol, beta and VaR, and signals repainted.
  Statistics and signals now use closed bars only.
- **Fees missing from cost basis.** A 0.40% taker buy at 50,000 showed a
  break-even of 50,000 instead of 50,200. Two "realized P&L" figures also
  disagreed about sell fees. Both paths now treat fees the same way.
- **Drawdown missed day-one losses.** The wealth index started after the first
  return, so a −20% first day showed 0% max drawdown. Risk and Analytics also
  computed different Sortino values for the same series. There is now one
  wealth index and one set of ratio functions.
- **Sharpe assumed a zero risk-free rate.** It now uses a configurable rate,
  defaulting to the 3-month T-bill.
- **An optimistic backtest.** It filled at the signal bar's close, ignored
  costs and gaps, and had no true out-of-sample test. A breakout fill bias was
  worth +0.26R a trade on a driftless random walk. It now uses next-open fills,
  costs, gap-aware stops and an anchored walk-forward.
- **Live signal P&L was gross, and open signals were lost on restart.** P&L is
  now net of fees and slippage, and pending signals persist.
- **Sizing could exceed the cash on hand.** Setups are now capped at the free
  balance, and a zero balance means no trade.
- **The order cap was checked only at confirm.** It is now re-checked, along
  with the mode, just before sending.
- **The VaR figure had never been tested.** It now comes as a set of models,
  each backtested.
