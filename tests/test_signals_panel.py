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


def test_backtest_rows_show_r_costs_and_out_of_sample() -> None:
    stats = _stats().model_copy(
        update={
            "expectancy_r": 0.25,
            "costs_paid": 42.5,
            "oos_trades": 7,
            "oos_expectancy_r": -0.1,
            "oos_return": -0.012,
            "oos_folds": 3,
        }
    )
    rows = dict(build_signals_view(None, _features(), None, _verdict(), stats, None).backtest_rows)
    assert rows["Expectancy (R)"] == "+0.25R"
    assert rows["Costs paid"] == "42.50"
    assert rows["Out-of-sample"] == "-0.10R over 7 trades, -1.2% (3 folds)"
    # Without a walk-forward the line is an honest dash, not a zero.
    rows = dict(
        build_signals_view(None, _features(), None, _verdict(), _stats(), None).backtest_rows
    )
    assert rows["Out-of-sample"] == "—"


def test_curve_points_pair_net_and_frictionless() -> None:
    from cqd.ui.panels.signals import curve_points

    stats = _stats().model_copy(
        update={
            "equity_curve": [100.0, 101.0, 99.0],
            "curve_times": [1, 2, 3],
            "frictionless_curve": [100.0, 102.0],
        }
    )
    x, net, free = curve_points(stats)
    assert x == [1.0, 2.0, 3.0] and net == [100.0, 101.0, 99.0] and free == [100.0, 102.0]
    assert curve_points(None) == ([], [], [])


def test_r_histogram_bins_at_half_r() -> None:
    from cqd.ui.panels.signals import r_histogram

    edges, counts = r_histogram([-1.0, -0.9, 0.1, 2.9])
    assert edges[0] == -1.0 and sum(counts) == 4
    assert counts[0] == 2 and counts[-1] == 1
    assert r_histogram([]) == ([], [])


def test_backtest_rows_include_the_ci_and_the_frictionless_twin() -> None:
    stats = _stats().model_copy(
        update={
            "expectancy_r": -0.1,
            "expectancy_r_ci": (-0.4, 0.2),
            "frictionless_expectancy_r": 0.02,
        }
    )
    rows = dict(build_signals_view(None, _features(), None, _verdict(), stats, None).backtest_rows)
    assert rows["Expectancy 90% CI"] == "-0.40R … +0.20R"
    assert rows["Frictionless (R)"] == "+0.02R"
