"""Backtests of the signal strategy: a costed historical replay and a real
anchored walk-forward with out-of-sample reporting.

Pure and deterministic - no I/O, no Qt, no RNG, no wall clock: the same candles
and params always give the same result. Entries come from the live signal
engine (`trend_states` / `evaluate_setup`), so the backtest measures the exact
logic the app emits rather than a re-implementation that could drift from it.

Execution model (each rule closes a bias the v2.2 replay had):
  - Signals are read on CLOSED bars only and act on the NEXT bar: the entry
    fills at the next bar's OPEN plus slippage. (The v2.2 breakout filled at the
    channel level, below the close that confirmed it, which made a driftless
    random walk earn about +0.26R per trade.)
  - Entries need a FRESH trigger: the bar where the state turns LONG_ACTIVE,
    not every bar the state stays active (MA-cross used to re-enter right
    after each stop-out).
  - Stop and take-profit keep the setup's risk distance R around the actual
    fill. A bar that OPENS through the stop exits at the open (gap fill), not
    at the stop; a bar that opens through the target exits at the open; a bar
    that spans both books the stop first (conservative).
  - Costs default to the paper broker's schedule: taker fee 0.40% per side
    and 5 bp slippage per fill (`PAPER_TAKER_FEE_BPS`, `PAPER_SLIPPAGE_BPS`;
    a test pins them to `trading.paper`).
  - Prop limits: loss limits are checked against the worst intrabar mark (an
    open long at the bar low); PASS needs the CLOSE-marked equity to reach the
    target (an intrabar high cannot be banked). Max drawdown is measured on
    close-marked equity including open trades, from the starting equity.

One long position at a time (v1 is long-only), sized by the setup at signal
time (frictionless lot precision, the engine's leverage cap still applies).
"""

from __future__ import annotations

import math
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from cqd.data.normalize import Candle
from cqd.engine.indicators import sma
from cqd.engine.signals import (
    TARGET_MULTIPLES,
    PairSpec,
    StrategyParams,
    TrendState,
    evaluate_setup,
    trend_states,
)

#: Paper-broker cost schedule (trading/paper.py: TAKER_FEE=0.0040, SLIPPAGE_BPS=5).
#: Mirrored, not imported - the engine may not depend on trading/ - and pinned
#: equal by tests/test_backtest.py so the two can never drift apart.
PAPER_TAKER_FEE_BPS = 40.0
PAPER_SLIPPAGE_BPS = 5.0

#: Frictionless lot spec for the backtest: sizing is purely risk-based.
_BACKTEST_SPEC = PairSpec(symbol="BACKTEST", price_decimals=8, lot_decimals=8, ordermin=0.0)

_EPS = 1e-12


class PropLimits(BaseModel):
    """The prop-firm envelope and the cost model the backtest is judged against.

    Losses are fractions of equity: `daily_loss` of start-of-day (prior close)
    equity, `total_loss` of the starting equity, `profit_target` of starting
    equity for a PASS. `fee_bps` is charged on EACH side's notional;
    `slippage_bps` worsens each fill. Defaults match paper trading.
    """

    starting_equity: float = Field(gt=0)
    daily_loss: float = Field(0.03, gt=0, lt=1)
    total_loss: float = Field(0.05, gt=0, lt=1)
    profit_target: float = Field(0.08, gt=0)
    fee_bps: float = Field(PAPER_TAKER_FEE_BPS, ge=0)
    slippage_bps: float = Field(PAPER_SLIPPAGE_BPS, ge=0)


class StrategyStats(BaseModel):
    """Backtest result. Undefined ratios are `None` (JSON-safe, honest) rather
    than NaN/inf - e.g. `profit_factor` is `None` when there were no losing
    trades, `win_rate` is `None` with zero trades.

    PnL fields (`expectancy`, `avg_win`, ...) are in quote currency and NET of
    fees and slippage; `expectancy_r` is the mean net R-multiple (PnL divided by
    the risk planned at entry). `exposure` is the share of evaluated bars that
    closed with a position open. The `oos_*` fields are filled in when the
    caller also ran `walk_forward` (out-of-sample, see `WalkForwardResult`).
    """

    outcome: Literal["pass", "bust", "unresolved"]
    bars: int
    trades: int
    wins: int
    losses: int
    win_rate: float | None
    expectancy: float | None
    profit_factor: float | None
    avg_win: float | None
    avg_loss: float | None
    max_drawdown: float
    total_return: float
    final_equity: float
    regime: Literal["bull", "bear", "chop"]
    expectancy_r: float | None = None
    r_multiples: list[float] = Field(default_factory=list)
    costs_paid: float = 0.0
    exposure: float = 0.0
    buy_hold_return: float | None = None
    fee_bps: float = 0.0
    slippage_bps: float = 0.0
    oos_trades: int | None = None
    oos_expectancy_r: float | None = None
    oos_return: float | None = None
    oos_folds: int | None = None
    #: Close-marked equity after each evaluated bar (open trade included), with
    #: the bar open times; ends early on a PASS/bust like the replay itself.
    equity_curve: list[float] = Field(default_factory=list)
    curve_times: list[int] = Field(default_factory=list)
    #: 90% bootstrap interval for `expectancy_r` (None under 2 trades).
    expectancy_r_ci: tuple[float, float] | None = None
    #: The same replay with zero fees and slippage, set by the caller so the
    #: panel can show what costs take out (None when not computed).
    frictionless_curve: list[float] | None = None
    frictionless_expectancy_r: float | None = None


def bootstrap_mean_ci(
    values: list[float], *, level: float = 0.90, n_boot: int = 2000, seed: int = 7
) -> tuple[float, float] | None:
    """Percentile bootstrap interval for the mean of `values` (trade resampling).

    Deterministic (fixed seed) so the panel does not flicker between refreshes.
    `None` with fewer than two values - one trade has no spread to resample.
    """
    if len(values) < 2:
        return None
    rng = np.random.default_rng(seed)
    arr = np.asarray(values, dtype=float)
    means = rng.choice(arr, size=(n_boot, arr.size), replace=True).mean(axis=1)
    tail = (1.0 - level) / 2.0
    lo, hi = np.quantile(means, [tail, 1.0 - tail])
    return float(lo), float(hi)


def classify_exit(
    direction: str,
    entry: float,
    stop: float,
    target: float,
    high: float,
    low: float,
    *,
    open_: float | None = None,
) -> tuple[Literal["win", "loss", "pending"], float | None]:
    """Resolve one bar against an open position: (outcome, exit_price).

    Long only. With the bar's `open_`, a gap through a level fills AT THE OPEN
    (open <= stop -> loss at the open, open >= target -> win at the open): a
    stop is a market order once triggered, it cannot fill at a price the market
    skipped. Otherwise the STOP is checked before the target, so a bar that
    spans both books the loss (never the optimistic fill). `pending` when
    neither level trades. Shared with the live track record so the backtest and
    the live record classify identically.
    """
    if direction != "long":
        return "pending", None  # v1 is long-only
    if open_ is not None and math.isfinite(open_):
        if open_ <= stop:
            return "loss", open_
        if open_ >= target:
            return "win", open_
    if low <= stop:
        return "loss", stop
    if high >= target:
        return "win", target
    return "pending", None


def _regime(candles: list[Candle], params: StrategyParams) -> Literal["bull", "bear", "chop"]:
    """Deterministic period tag from the net move and trend participation."""
    closes = pd.Series([c.close for c in candles], dtype=float)
    if len(closes) < 2 or closes.iloc[0] <= 0:
        return "chop"
    net = float(closes.iloc[-1] / closes.iloc[0] - 1.0)
    trend_ma = sma(closes, params.trend)
    valid = trend_ma.notna()
    above = float((closes[valid] > trend_ma[valid]).mean()) if valid.any() else 0.5
    if net > 0.05 and above > 0.55:
        return "bull"
    if net < -0.05 and above < 0.45:
        return "bear"
    return "chop"


def trade_summary(pnls: list[float]) -> dict:
    """Trade stats from closed-trade PnLs; `None` for undefined ratios.

    Shared by the backtests and the live track record so both report
    expectancy / profit factor / win rate with identical semantics.
    """
    n = len(pnls)
    if n == 0:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "expectancy": None,
            "profit_factor": None,
            "avg_win": None,
            "avg_loss": None,
        }
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / n,
        "expectancy": sum(pnls) / n,
        # None (not inf) when there are no losing trades: an honest "undefined".
        "profit_factor": (gross_win / gross_loss) if gross_loss > _EPS else None,
        "avg_win": (gross_win / len(wins)) if wins else None,
        "avg_loss": (-gross_loss / len(losses)) if losses else None,
    }


def replay(
    candles: list[Candle],
    params: StrategyParams,
    limits: PropLimits,
    *,
    target_index: int = -1,
    start_index: int = 0,
    close_at_end: bool = False,
) -> StrategyStats:
    """Replay the strategy bar by bar with costs and judge it against the limits.

    `candles` must be CLOSED bars. Bars before `start_index` only warm up the
    indicators (no trading, not counted), which is how the walk-forward runs an
    out-of-sample window with real history behind it. `target_index` picks the
    take-profit among the setup's R-multiple targets (default the furthest).
    `close_at_end` books a still-open position as a trade at the last close
    (with exit costs); otherwise it is marked unrealized and not counted.
    """
    n = len(candles)
    start = max(0, min(start_index, n))
    states = trend_states(candles, params)
    fee = limits.fee_bps / 1e4
    slip = limits.slippage_bps / 1e4
    equity = limits.starting_equity  # cash-basis equity (fees already paid)
    total_floor = limits.starting_equity * (1.0 - limits.total_loss)
    pass_at = limits.starting_equity * (1.0 + limits.profit_target)

    position: dict | None = None
    pending: tuple[float, float] | None = None  # (size, risk distance) to fill next open
    pnls: list[float] = []
    rs: list[float] = []
    costs = 0.0
    in_market = 0
    peak = equity
    max_dd = 0.0
    prev_mark = equity
    curve: list[float] = []
    times: list[int] = []
    outcome: Literal["pass", "bust", "unresolved"] = "unresolved"
    multiple = _target_multiple(target_index)

    def _close(px_raw: float) -> None:
        nonlocal equity, position, costs
        assert position is not None
        exit_px = px_raw * (1.0 - slip)
        fee_out = position["size"] * exit_px * fee
        gross = position["size"] * (exit_px - position["fill"])
        equity += gross - fee_out
        pnl = gross - position["fee_in"] - fee_out
        costs += fee_out + position["size"] * px_raw * slip
        pnls.append(pnl)
        rs.append(pnl / position["risk"] if position["risk"] > _EPS else 0.0)
        position = None

    for i in range(start, n):
        bar = candles[i]
        daily_floor = prev_mark * (1.0 - limits.daily_loss)  # start of day = prior close

        # 1. Fill yesterday's signal at today's open.
        if pending is not None and position is None:
            size, dist = pending
            fill = bar.open * (1.0 + slip)
            fee_in = size * fill * fee
            equity -= fee_in
            costs += fee_in + size * bar.open * slip
            position = {
                "size": size,
                "fill": fill,
                "stop": fill - dist,
                "target": fill + multiple * dist,
                "risk": size * dist,
                "fee_in": fee_in,
            }
        pending = None

        # 2. Resolve the open position against this bar (gap-aware).
        if position is not None:
            verdict, px = classify_exit(
                "long",
                position["fill"],
                position["stop"],
                position["target"],
                bar.high,
                bar.low,
                open_=bar.open,
            )
            if px is not None:
                _close(px)

        # 3. Loss limits on the worst intrabar mark.
        worst = equity + (position["size"] * (bar.low - position["fill"]) if position else 0.0)
        if worst <= total_floor or worst <= daily_floor:
            outcome = "bust"
            equity = worst
            max_dd = min(max_dd, equity / peak - 1.0)
            position = None
            curve.append(equity)
            times.append(int(bar.time))
            break

        # 4. Close mark: drawdown (incl. open trade) and PASS on the close.
        mark = equity + (position["size"] * (bar.close - position["fill"]) if position else 0.0)
        if position is not None:
            in_market += 1
        peak = max(peak, mark)
        max_dd = min(max_dd, mark / peak - 1.0)
        prev_mark = mark
        curve.append(mark)
        times.append(int(bar.time))
        if mark >= pass_at:
            outcome = "pass"
            if position is not None:
                _close(bar.close)
            break

        # 5. A fresh trigger on this CLOSED bar queues an entry for the next open.
        fresh = (
            states[i] == TrendState.LONG_ACTIVE
            and (i == 0 or states[i - 1] != TrendState.LONG_ACTIVE)
            and i + 1 < n
        )
        if position is None and fresh:
            setup = evaluate_setup(candles[: i + 1], params, equity, _BACKTEST_SPEC)
            if setup is not None and setup.targets:
                pending = (setup.size_base, setup.entry_ref - setup.stop)

    if position is not None and n:
        if close_at_end:
            _close(candles[-1].close)
        else:
            equity += position["size"] * (candles[-1].close - position["fill"])

    evaluated = candles[start:]
    stats = trade_summary(pnls)
    bh = None
    if evaluated and evaluated[0].open > 0:
        bh = evaluated[-1].close / evaluated[0].open - 1.0
    return StrategyStats(
        outcome=outcome,
        bars=len(evaluated),
        max_drawdown=max_dd,
        total_return=(equity / limits.starting_equity - 1.0),
        final_equity=equity,
        regime=_regime(evaluated or candles, params),
        expectancy_r=(sum(rs) / len(rs)) if rs else None,
        r_multiples=rs,
        costs_paid=costs,
        exposure=(in_market / len(evaluated)) if evaluated else 0.0,
        buy_hold_return=bh,
        fee_bps=limits.fee_bps,
        slippage_bps=limits.slippage_bps,
        equity_curve=curve,
        curve_times=times,
        expectancy_r_ci=bootstrap_mean_ci(rs),
        **stats,
    )


def _target_multiple(target_index: int) -> float:
    return float(TARGET_MULTIPLES[target_index])


# ---------- walk-forward ----------


class WalkForwardFold(BaseModel):
    """One fold: parameters chosen on in-sample bars, judged on the next window."""

    train_start: int  # bar index (inclusive) where in-sample scoring starts
    test_start: int  # first out-of-sample bar index
    test_end: int  # exclusive
    test_start_ts: int
    fast: int
    slow: int
    in_sample_trades: int
    in_sample_expectancy_r: float | None
    oos_trades: int
    oos_expectancy_r: float | None
    oos_return: float


class WalkForwardResult(BaseModel):
    """Anchored walk-forward: only the out-of-sample numbers are evidence.

    `oos_r_multiples` concatenates every fold's out-of-sample trades;
    `oos_return` compounds the fold returns. `efficiency` = OOS expectancy /
    in-sample expectancy (near or above 1 = the fit generalised; well below 1
    or negative = the in-sample edge was curve-fitting). `None` when undefined.
    """

    folds: list[WalkForwardFold]
    oos_trades: int
    oos_expectancy_r: float | None
    oos_win_rate: float | None
    oos_return: float
    in_sample_expectancy_r: float | None
    efficiency: float | None
    oos_r_multiples: list[float] = Field(default_factory=list)


def default_grid(base: StrategyParams) -> list[StrategyParams]:
    """Small, deliberate fast/slow grid around the base (trend, ATR, risk fixed).

    Kept coarse on purpose: a wide grid on a few hundred bars is a
    curve-fitting machine. Invalid combinations (fast >= slow >= trend) skip.
    """
    out: list[StrategyParams] = []
    for f_mult in (0.5, 1.0, 1.5):
        for s_mult in (1.0, 2.0):
            fast = max(2, int(round(base.fast * f_mult)))
            slow = max(fast + 1, int(round(base.slow * s_mult)))
            if not (fast < slow < base.trend):
                continue
            p = base.model_copy(update={"fast": fast, "slow": slow})
            if all((q.fast, q.slow) != (p.fast, p.slow) for q in out):
                out.append(p)
    return out or [base]


def _research_limits(limits: PropLimits) -> PropLimits:
    """Same equity and costs, prop limits disabled: score the STRATEGY, not
    whether one unlucky streak tripped a 5% account."""
    return limits.model_copy(update={"daily_loss": 0.99, "total_loss": 0.99, "profit_target": 1e9})


def walk_forward(
    candles: list[Candle],
    params: StrategyParams,
    limits: PropLimits,
    *,
    grid: list[StrategyParams] | None = None,
    train_bars: int | None = None,
    test_bars: int | None = None,
    min_trades: int = 3,
    target_index: int = -1,
) -> WalkForwardResult:
    """Anchored walk-forward: fit on all bars so far, trade the next window.

    Fold k: in-sample = bars [warmup, test_start) (anchored: it grows each
    fold); every grid candidate is replayed there with costs and the one with
    the highest total net R (at least `min_trades` trades, else the base params)
    is chosen; it then trades out-of-sample bars [test_start, test_start +
    test_bars) with all prior bars as indicator warm-up and any open position
    closed at the window end. Defaults: `train_bars` = 2 x trend window,
    `test_bars` = a quarter of the remaining bars (min 30).
    """
    n = len(candles)
    grid = grid or default_grid(params)
    research = _research_limits(limits)
    warmup = max(p.trend for p in grid)
    train = train_bars if train_bars is not None else 2 * params.trend
    train = max(train, warmup + 1)
    if n <= train:
        return WalkForwardResult(
            folds=[],
            oos_trades=0,
            oos_expectancy_r=None,
            oos_win_rate=None,
            oos_return=0.0,
            in_sample_expectancy_r=None,
            efficiency=None,
        )
    step = test_bars if test_bars is not None else max(30, (n - train) // 4)

    folds: list[WalkForwardFold] = []
    oos_rs: list[float] = []
    is_rs: list[float] = []
    growth = 1.0
    test_start = train
    while test_start < n:
        test_end = min(n, test_start + step)
        history = candles[:test_start]
        best, best_score, best_stats = params, -math.inf, None
        for cand in grid:
            st = replay(history, cand, research, target_index=target_index, start_index=warmup)
            if st.trades < min_trades:
                continue
            score = sum(st.r_multiples)
            if score > best_score + _EPS:
                best, best_score, best_stats = cand, score, st
        if best_stats is None:
            best_stats = replay(
                history, params, research, target_index=target_index, start_index=warmup
            )
        oos = replay(
            candles[:test_end],
            best,
            research,
            target_index=target_index,
            start_index=test_start,
            close_at_end=True,
        )
        folds.append(
            WalkForwardFold(
                train_start=warmup,
                test_start=test_start,
                test_end=test_end,
                test_start_ts=int(candles[test_start].time),
                fast=best.fast,
                slow=best.slow,
                in_sample_trades=best_stats.trades,
                in_sample_expectancy_r=best_stats.expectancy_r,
                oos_trades=oos.trades,
                oos_expectancy_r=oos.expectancy_r,
                oos_return=oos.total_return,
            )
        )
        oos_rs.extend(oos.r_multiples)
        is_rs.extend(best_stats.r_multiples)
        growth *= 1.0 + oos.total_return
        test_start = test_end

    oos_exp = (sum(oos_rs) / len(oos_rs)) if oos_rs else None
    is_exp = (sum(is_rs) / len(is_rs)) if is_rs else None
    eff = None
    if oos_exp is not None and is_exp is not None and abs(is_exp) > _EPS:
        eff = oos_exp / is_exp
    return WalkForwardResult(
        folds=folds,
        oos_trades=len(oos_rs),
        oos_expectancy_r=oos_exp,
        oos_win_rate=(sum(1 for r in oos_rs if r > 0) / len(oos_rs)) if oos_rs else None,
        oos_return=growth - 1.0,
        in_sample_expectancy_r=is_exp,
        efficiency=eff,
        oos_r_multiples=oos_rs,
    )


FAST_GRID: tuple[int, ...] = (5, 10, 15, 20, 30, 40)
SLOW_GRID: tuple[int, ...] = (30, 50, 75, 100, 150)


class StabilityGrid(BaseModel):
    """Net expectancy (R per trade, costs in) over a fast x slow grid.

    `cells[i][j]` is for fast=fasts[i], slow=slows[j]; None where the combo is
    invalid (fast >= slow, slow >= trend) or traded too little to score. A
    strategy worth trusting sits on a plateau of positive cells, not a spike.
    """

    fasts: list[int]
    slows: list[int]
    cells: list[list[float | None]]
    trades: list[list[int]]
    chosen: tuple[int, int]
    positive_share: float | None
    neighbourhood_mean: float | None


def parameter_stability(
    candles: list[Candle],
    params: StrategyParams,
    limits: PropLimits,
    *,
    fasts: tuple[int, ...] = FAST_GRID,
    slows: tuple[int, ...] = SLOW_GRID,
    min_trades: int = 3,
) -> StabilityGrid:
    """Replay every valid (fast, slow) with costs and prop limits disabled."""
    research = _research_limits(limits)
    cells: list[list[float | None]] = []
    trades: list[list[int]] = []
    for f in fasts:
        row: list[float | None] = []
        trow: list[int] = []
        for sl in slows:
            if not (f < sl < params.trend):
                row.append(None)
                trow.append(0)
                continue
            st = replay(candles, params.model_copy(update={"fast": f, "slow": sl}), research)
            trow.append(st.trades)
            row.append(st.expectancy_r if st.trades >= min_trades else None)
        cells.append(row)
        trades.append(trow)
    scored = [v for row in cells for v in row if v is not None]
    positive = sum(1 for v in scored if v > 0) / len(scored) if scored else None
    # Mean of the chosen cell and its 8 neighbours (where scored): plateau check.
    ci = min(range(len(fasts)), key=lambda i: abs(fasts[i] - params.fast))
    cj = min(range(len(slows)), key=lambda j: abs(slows[j] - params.slow))
    hood = [
        cells[i][j]
        for i in range(max(0, ci - 1), min(len(fasts), ci + 2))
        for j in range(max(0, cj - 1), min(len(slows), cj + 2))
        if cells[i][j] is not None
    ]
    return StabilityGrid(
        fasts=list(fasts),
        slows=list(slows),
        cells=cells,
        trades=trades,
        chosen=(ci, cj),
        positive_share=positive,
        neighbourhood_mean=sum(hood) / len(hood) if hood else None,
    )


__all__ = [
    "StabilityGrid",
    "parameter_stability",
    "PAPER_SLIPPAGE_BPS",
    "PAPER_TAKER_FEE_BPS",
    "PropLimits",
    "bootstrap_mean_ci",
    "StrategyStats",
    "WalkForwardFold",
    "WalkForwardResult",
    "classify_exit",
    "default_grid",
    "replay",
    "trade_summary",
    "walk_forward",
]
