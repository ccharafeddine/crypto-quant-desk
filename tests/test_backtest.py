"""Tests for the walk-forward backtest (pure, deterministic).

The entry logic (evaluate_setup) is covered by test_signals.py; here we test the
loop mechanics: exit resolution, prop-limit classification (pass/bust/unresolved),
the trade summary, the regime tag, JSON-safety, and determinism.
"""

from __future__ import annotations

import json

import pytest

from cqd.data.normalize import Candle
from cqd.engine.backtest import (
    PropLimits,
    classify_exit,
    trade_summary,
    walk_forward,
)
from cqd.engine.signals import StrategyParams

PARAMS = StrategyParams(
    fast=2, slow=3, trend=5, atr_len=2, atr_mult=2.0, risk_pct=0.01, variant="ma_cross"
)


def _candles(closes: list[float], hl: float = 0.5) -> list[Candle]:
    return [
        Candle(time=1000 + i * 60, open=c, high=c + hl, low=c - hl, close=c, volume=1.0)
        for i, c in enumerate(closes)
    ]


# ---------- classify_exit truth table (pure) ----------


def test_classify_exit_stop_first_when_bar_spans_both() -> None:
    # entry 100, stop 97, target 109. A bar spanning both books the LOSS.
    assert classify_exit("long", 100, 97, 109, high=110, low=96) == ("loss", 97)
    assert classify_exit("long", 100, 97, 109, high=110, low=99) == ("win", 109)
    assert classify_exit("long", 100, 97, 109, high=105, low=98) == ("pending", None)


def test_classify_exit_non_long_is_pending() -> None:
    assert classify_exit("short", 100, 103, 91, high=120, low=80) == ("pending", None)


# ---------- trade_summary (pure) ----------


def test_trade_summary_known_pnls() -> None:
    s = trade_summary([9.0, -3.0, 6.0, -3.0])
    assert s["trades"] == 4 and s["wins"] == 2 and s["losses"] == 2
    assert s["win_rate"] == pytest.approx(0.5)
    assert s["expectancy"] == pytest.approx((9 - 3 + 6 - 3) / 4)
    assert s["profit_factor"] == pytest.approx(15 / 6)  # gross win 15 / gross loss 6
    assert s["avg_win"] == pytest.approx(7.5) and s["avg_loss"] == pytest.approx(-3.0)


def test_trade_summary_no_losses_profit_factor_none() -> None:
    s = trade_summary([5.0, 2.0])
    assert s["profit_factor"] is None  # undefined, not inf


def test_trade_summary_empty() -> None:
    s = trade_summary([])
    assert s["trades"] == 0
    assert s["win_rate"] is None and s["expectancy"] is None and s["profit_factor"] is None


# ---------- walk_forward outcomes ----------


def test_walk_forward_downtrend_no_trades_unresolved() -> None:
    stats = walk_forward(
        _candles([16, 15, 14, 13, 12, 11, 10]), PARAMS, PropLimits(starting_equity=10_000.0)
    )
    assert stats.outcome == "unresolved"
    assert stats.trades == 0
    assert stats.final_equity == pytest.approx(10_000.0)
    assert stats.regime == "bear"


def test_walk_forward_uptrend_reaches_pass() -> None:
    stats = walk_forward(
        _candles([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]),
        PARAMS,
        PropLimits(starting_equity=10_000.0, profit_target=0.02),
    )
    assert stats.outcome == "pass"
    assert stats.final_equity > 10_000.0
    assert stats.total_return > 0.0
    assert stats.regime == "bull"


def test_walk_forward_reversal_busts_on_tight_limit() -> None:
    stats = walk_forward(
        _candles([10, 11, 12, 13, 14, 13, 12, 11, 10]),
        PARAMS,
        PropLimits(starting_equity=10_000.0, total_loss=0.005),
    )
    assert stats.outcome == "bust"
    assert stats.final_equity < 10_000.0


def test_walk_forward_open_position_at_end_is_unresolved() -> None:
    stats = walk_forward(
        _candles([10, 11, 12, 13, 14, 15]), PARAMS, PropLimits(starting_equity=10_000.0)
    )
    assert stats.outcome == "unresolved"
    assert stats.trades == 0  # position still open -> no closed round trip
    assert stats.final_equity > 10_000.0  # marked at the last close (unrealized)


def test_walk_forward_is_json_safe_and_deterministic() -> None:
    candles = _candles([10, 11, 12, 13, 14, 15, 16, 17, 18])
    limits = PropLimits(starting_equity=10_000.0)
    a = walk_forward(candles, PARAMS, limits)
    b = walk_forward(candles, PARAMS, limits)
    assert a.model_dump() == b.model_dump()
    json.dumps(a.model_dump())  # no NaN/inf -> valid JSON
