"""Tests for the Signals panel's pure view mapping (no QApplication)."""

from __future__ import annotations

from cqd.engine.backtest import StrategyStats
from cqd.engine.microstructure import BookFeatures, FillEstimate, TimingVerdict
from cqd.engine.signals import TradeSetup, TrendState
from cqd.ui.panels.signals import build_signals_view


def _setup(**over) -> TradeSetup:
    base = dict(
        symbol="BTC/USD",
        direction="long",
        state=TrendState.LONG_ACTIVE,
        entry_ref=100.0,
        stop=97.0,
        size_base=1.5,
        size_quote=150.0,
        risk_quote=4.5,
        targets=[103.0, 106.0, 109.0],
        rr=3.0,
        confidence=0.6,
        rationale="ma_cross long",
        created_ts=1000,
    )
    base.update(over)
    return TradeSetup(**base)


def _features() -> BookFeatures:
    return BookFeatures(
        mid=100.0,
        microprice=100.2,
        spread_bps=5.0,
        imbalance_l10=0.3,
        imbalance_l5=0.3,
        imbalance_l25=0.3,
        depth_bid_bps=50.0,
        depth_ask_bps=50.0,
        spread_abs=0.05,
    )


def _verdict() -> TimingVerdict:
    return TimingVerdict(
        verdict="GO", reasons=["tight spread", "strong supportive imbalance"], score=1.5
    )


def _fill() -> FillEstimate:
    return FillEstimate(
        side="buy", notional=150.0, vwap=100.1, slippage_bps=10.0, levels_consumed=2
    )


def _stats() -> StrategyStats:
    return StrategyStats(
        outcome="pass",
        bars=300,
        trades=10,
        wins=6,
        losses=4,
        win_rate=0.6,
        expectancy=12.0,
        profit_factor=1.8,
        avg_win=30.0,
        avg_loss=-15.0,
        max_drawdown=-0.08,
        total_return=0.15,
        final_equity=11_500.0,
        regime="bull",
    )


def test_build_view_full_setup() -> None:
    view = build_signals_view(
        _setup(),
        _features(),
        _fill(),
        _verdict(),
        _stats(),
        {"trades": 0, "pending": 1, "enough": False},
    )
    assert view.has_setup is True
    assert view.state_label == "Long · active"
    assert view.verdict == "GO" and view.verdict_role == "go"
    assert any("Confidence" in label and value == "60.0%" for label, value in view.setup_rows)
    assert any("Entry" in label for label, _ in view.setup_rows)
    assert ("Outcome", "Pass") in view.backtest_rows
    assert view.live_note  # low-sample honesty note present


def test_build_view_no_setup() -> None:
    view = build_signals_view(None, _features(), None, _verdict(), None, None)
    assert view.has_setup is False
    assert view.state_label == "No active setup"
    assert view.backtest_rows == [("Backtest", "computing…")]


def test_build_view_prop_room_warning() -> None:
    # daily_room below one unit of risk -> a suppression warning is surfaced.
    setup = _setup(daily_room=1.0, total_room=500.0)  # risk_quote is 4.5
    view = build_signals_view(setup, _features(), _fill(), _verdict(), None, None)
    assert view.prop_warnings
    assert "Daily loss room" in view.prop_warnings[0]


def test_build_view_wait_verdict_role() -> None:
    v = TimingVerdict(verdict="WAIT", reasons=["book unknown"], score=0.0)
    view = build_signals_view(None, None, None, v, None, None)
    assert view.verdict == "WAIT" and view.verdict_role == "wait"
