"""Walk-forward backtest of the signal strategy under prop-firm limits.

Pure and deterministic - no I/O, no Qt, no RNG (seedless): the same candles and
params always give the same `StrategyStats`. The backtest REUSES the live signal
engine (`evaluate_setup`) bar by bar over expanding history, so it measures the
exact logic the app emits - not a re-implementation that could drift from it.

It answers the prop question: replaying this strategy over this series, does the
account PASS (reach the profit target), BUST (breach the daily or total loss
limit), or finish UNRESOLVED - and with what expectancy, profit factor, and
drawdown along the way.

Modeling choices (documented; the honest simplifications a daily-bar backtest
makes, and the seams where STRATEGY.md's method is reconciled by the owner):
  - One long position at a time (v1 is long-only); entries only on a LONG_ACTIVE
    setup, at the setup's own `entry_ref`, sized by the setup (frictionless
    precision - exchange lot/min rounding is not modeled here).
  - Exits: the first later bar whose low touches the stop (loss) or whose high
    touches the take-profit (win). If a bar spans both, the STOP is assumed hit
    first (conservative - never books the optimistic outcome).
  - The daily/total loss limits are checked against each bar's WORST intrabar
    mark (an open long marked at the bar low), the conservative stand-in for the
    finer-grained ("hourly") equity check a prop desk runs continuously.
  - Optional round-trip `fee_bps` (default 0); slippage is the execution layer's
    concern (microstructure.py), not modeled in the trend backtest.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from cqd.data.normalize import Candle
from cqd.engine.indicators import sma
from cqd.engine.signals import PairSpec, StrategyParams, TrendState, evaluate_setup

#: Frictionless spec for the backtest: high precision, no minimum, so sizing is
#: purely risk-based (the leverage cap in `evaluate_setup` still applies).
_BACKTEST_SPEC = PairSpec(symbol="BACKTEST", price_decimals=8, lot_decimals=8, ordermin=0.0)

_EPS = 1e-12


class PropLimits(BaseModel):
    """The prop-firm envelope the backtest is judged against.

    Losses are fractions of equity: `daily_loss` of start-of-day equity,
    `total_loss` of the starting equity, `profit_target` of starting equity for
    a PASS. `fee_bps` is a per-side round-trip fee in basis points (0 = frictionless).
    """

    starting_equity: float = Field(gt=0)
    daily_loss: float = Field(0.03, gt=0, lt=1)
    total_loss: float = Field(0.05, gt=0, lt=1)
    profit_target: float = Field(0.08, gt=0)
    fee_bps: float = Field(0.0, ge=0)


class StrategyStats(BaseModel):
    """Backtest result. Undefined ratios are `None` (JSON-safe, honest) rather
    than NaN/inf - e.g. `profit_factor` is `None` when there were no losing
    trades, `win_rate` is `None` with zero trades."""

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


def classify_exit(
    direction: str,
    entry: float,
    stop: float,
    target: float,
    high: float,
    low: float,
) -> tuple[Literal["win", "loss", "pending"], float | None]:
    """Resolve one bar against an open position: (outcome, exit_price).

    Long: the STOP is checked before the target, so a bar that spans both books
    the loss (conservative, never the optimistic fill). `pending` when neither
    level trades in the bar. Shared with the live track record so the backtest
    and the live record classify identically.
    """
    if direction != "long":
        return "pending", None  # v1 is long-only
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

    Shared by `walk_forward` (backtest) and the live track record so both report
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


def walk_forward(
    candles: list[Candle],
    params: StrategyParams,
    limits: PropLimits,
    *,
    target_index: int = -1,
) -> StrategyStats:
    """Replay the strategy bar by bar and judge it against the prop limits.

    `target_index` picks which of the setup's R-multiple targets is the
    take-profit (default `-1` = the furthest, i.e. `setup.rr`). Deterministic:
    no RNG, no wall clock.
    """
    equity = limits.starting_equity
    peak = equity
    max_dd = 0.0
    fee = limits.fee_bps / 1e4
    position: dict | None = None
    pnls: list[float] = []
    outcome: Literal["pass", "bust", "unresolved"] = "unresolved"

    total_floor = limits.starting_equity * (1.0 - limits.total_loss)
    pass_at = limits.starting_equity * (1.0 + limits.profit_target)

    for i, bar in enumerate(candles):
        sod_equity = equity  # daily bars: each bar opens a new "day"
        daily_floor = sod_equity * (1.0 - limits.daily_loss)

        # 1. Resolve an open position against this bar's range.
        if position is not None:
            verdict, exit_price = classify_exit(
                "long", position["entry"], position["stop"], position["target"], bar.high, bar.low
            )
            if exit_price is not None:
                gross = position["size"] * (exit_price - position["entry"])
                fees = position["size"] * (position["entry"] + exit_price) * fee
                pnl = gross - fees
                equity += pnl
                pnls.append(pnl)
                position = None

        # 2. Worst intrabar mark (open long at the bar low) for the loss limits.
        marked_low = equity + (
            position["size"] * (bar.low - position["entry"]) if position else 0.0
        )
        if marked_low <= total_floor or marked_low <= daily_floor:
            outcome = "bust"
            equity = marked_low
            break

        # 3. Best intrabar mark for the profit target (an open long at the bar high).
        marked_high = equity + (
            position["size"] * (bar.high - position["entry"]) if position else 0.0
        )
        if marked_high >= pass_at:
            outcome = "pass"
            equity = marked_high
            break

        # 4. Drawdown tracks realized equity between trades.
        peak = max(peak, equity)
        if peak > 0:
            max_dd = min(max_dd, equity / peak - 1.0)

        # 5. Enter on a fresh LONG_ACTIVE trigger (act from the NEXT bar on).
        if position is None:
            setup = evaluate_setup(candles[: i + 1], params, equity, _BACKTEST_SPEC)
            if setup is not None and setup.state == TrendState.LONG_ACTIVE and setup.targets:
                position = {
                    "entry": setup.entry_ref,
                    "stop": setup.stop,
                    "target": setup.targets[target_index],
                    "size": setup.size_base,
                }

    # Close any position still open at the end at the last close (unrealized).
    if position is not None and candles:
        last = candles[-1].close
        equity += position["size"] * (last - position["entry"])

    stats = trade_summary(pnls)
    return StrategyStats(
        outcome=outcome,
        bars=len(candles),
        max_drawdown=max_dd,
        total_return=(equity / limits.starting_equity - 1.0),
        final_equity=equity,
        regime=_regime(candles, params),
        **stats,
    )


__all__ = [
    "PropLimits",
    "StrategyStats",
    "classify_exit",
    "trade_summary",
    "walk_forward",
]
