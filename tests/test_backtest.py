"""Tests for the costed replay and the walk-forward backtest (pure, deterministic).

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
)
from cqd.engine.backtest import replay as walk_forward  # the v2.2 loop tests, on replay
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
        # Longer than v2.2's 10..21: PASS now needs the CLOSE-marked equity to
        # reach the target after a next-open fill and fees, not an intrabar high.
        _candles(list(range(10, 26))),
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
        _candles([10, 11, 12, 13, 14, 15, 16, 17]), PARAMS, PropLimits(starting_equity=10_000.0)
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


# ---------- Phase 1 regressions: execution realism ----------

import numpy as np  # noqa: E402

from cqd.engine.backtest import (  # noqa: E402
    PAPER_SLIPPAGE_BPS,
    PAPER_TAKER_FEE_BPS,
    replay,
    walk_forward as real_walk_forward,
)
from cqd.engine.signals import TrendState, trend_state, trend_states  # noqa: E402

FRICTIONLESS = {"fee_bps": 0.0, "slippage_bps": 0.0}
RESEARCH = {"profit_target": 1e9, "total_loss": 0.99, "daily_loss": 0.99}


def _bars(rows: list[tuple[float, float, float, float]]) -> list[Candle]:
    return [
        Candle(time=1000 + i * 60, open=o, high=h, low=lo, close=c, volume=1.0)
        for i, (o, h, lo, c) in enumerate(rows)
    ]


def _gbm(n: int, seed: int, sig: float = 0.6) -> list[Candle]:
    g = np.random.default_rng(seed)
    dt = 1 / 365
    lr = g.normal(-0.5 * sig**2 * dt, sig * np.sqrt(dt), n)
    c = 100.0 * np.exp(np.cumsum(lr))
    o = np.r_[100.0, c[:-1]]
    wig = sig * np.sqrt(dt) * 0.5
    hi = np.maximum(o, c) * np.exp(np.abs(g.normal(0, wig, n)))
    lo = np.minimum(o, c) * np.exp(-np.abs(g.normal(0, wig, n)))
    return [Candle(1000 + i * 86400, o[i], hi[i], lo[i], c[i], 1.0) for i in range(n)]


def test_default_costs_match_the_paper_broker() -> None:
    from cqd.trading import paper

    assert PAPER_TAKER_FEE_BPS == pytest.approx(paper.TAKER_FEE * 1e4)
    assert PAPER_SLIPPAGE_BPS == pytest.approx(paper.SLIPPAGE_BPS)
    limits = PropLimits(starting_equity=1.0)
    assert (limits.fee_bps, limits.slippage_bps) == (PAPER_TAKER_FEE_BPS, PAPER_SLIPPAGE_BPS)


def test_classify_exit_gap_through_stop_fills_at_open() -> None:
    # Regression: open 90 through a 95 stop used to book the exit at 95.
    assert classify_exit("long", 100, 95, 110, high=91, low=88, open_=90) == ("loss", 90)
    assert classify_exit("long", 100, 95, 110, high=118, low=111, open_=112) == ("win", 112)
    # Without a gap the stop-first rule is unchanged.
    assert classify_exit("long", 100, 95, 110, high=111, low=94, open_=100) == ("loss", 95)


@pytest.mark.parametrize("variant", ["breakout", "ma_cross"])
def test_driftless_random_walk_has_no_edge(variant: str) -> None:
    # The headline bias: v2.2's breakout filled at the channel below the
    # confirming close and earned about +0.26R/trade on a driftless random
    # walk. With next-open fills, expectancy must be zero within 3 std errors.
    params = StrategyParams(fast=10, slow=20, trend=50, variant=variant)
    rs: list[float] = []
    for seed in range(120):
        st = replay(
            _gbm(500, seed), params, PropLimits(starting_equity=1e5, **FRICTIONLESS, **RESEARCH)
        )
        rs.extend(st.r_multiples)
    r = np.array(rs)
    se = r.std(ddof=1) / np.sqrt(len(r))
    assert len(r) > 300
    assert abs(r.mean()) < 3 * se, (r.mean(), se)


def test_costs_cost_about_what_the_paper_broker_charges() -> None:
    params = StrategyParams(fast=10, slow=20, trend=50, variant="breakout")
    free, costed = [], []
    for seed in range(40):
        c = _gbm(500, seed)
        free += replay(
            c, params, PropLimits(starting_equity=1e5, **FRICTIONLESS, **RESEARCH)
        ).r_multiples
        costed += replay(c, params, PropLimits(starting_equity=1e5, **RESEARCH)).r_multiples
    assert len(free) == len(costed)
    assert np.mean(costed) < np.mean(free) - 0.01  # fees + slippage are a real drag


def _entry_series() -> list[Candle]:
    # trend=5 warm-up flat at 10, then a rally; ma_cross turns ACTIVE on bar 5.
    rows = [(10, 10.2, 9.8, 10)] * 5 + [(10, 11.2, 9.9, 11), (11.5, 12.2, 11.4, 12)]
    rows += [(12, 12.3, 11.8, 12.1)] * 3
    return _bars(rows)


def test_entry_fills_at_next_bar_open_plus_slippage() -> None:
    candles = _entry_series()
    states = trend_states(candles, PARAMS)
    first = states.index(TrendState.LONG_ACTIVE)
    st = replay(candles, PARAMS, PropLimits(starting_equity=10_000.0, slippage_bps=10, **RESEARCH))
    # Marked at the last close: equity = start - entry fee + size*(close - fill).
    fill = candles[first + 1].open * (1 + 10 / 1e4)
    assert st.exposure > 0
    assert st.final_equity < 10_000.0 + (candles[-1].close - fill) * 1e9  # sanity
    # The fill is the next bar's open, never the signal bar's close.
    assert candles[first + 1].open != candles[first].close


def test_no_reentry_while_trend_stays_active() -> None:
    # A stop-out with the MA state still ACTIVE must not re-enter on the next
    # bar (v2.2 re-entered on the state, 16 trades from 10 activations).
    params = StrategyParams(fast=10, slow=20, trend=50, variant="ma_cross")
    for seed in range(10):
        c = _gbm(500, seed)
        st = replay(c, params, PropLimits(starting_equity=1e5, **FRICTIONLESS, **RESEARCH))
        states = trend_states(c, params)
        fresh = sum(
            1
            for i in range(1, len(states) - 1)
            if states[i] == TrendState.LONG_ACTIVE and states[i - 1] != TrendState.LONG_ACTIVE
        )
        assert st.trades <= fresh


def test_trend_states_equals_prefix_trend_state() -> None:
    for variant in ("ma_cross", "breakout"):
        params = StrategyParams(fast=5, slow=10, trend=20, variant=variant)
        c = _gbm(120, 3)
        vec = trend_states(c, params)
        assert vec == [trend_state(c[: i + 1], params) for i in range(len(c))]


def test_drawdown_counts_the_open_trade_from_the_start() -> None:
    # Enter, then the open trade sinks without hitting the stop: drawdown must
    # show it (v2.2 tracked realized equity only, so this read 0%).
    rows = [(10, 10.2, 9.8, 10)] * 5 + [(10, 11.2, 9.9, 11), (11.5, 12.2, 11.4, 12)]
    rows += [(12, 12.1, 11.0, 11.0), (11.0, 11.1, 10.6, 10.7)]
    st = replay(_bars(rows), PARAMS, PropLimits(starting_equity=10_000.0, **RESEARCH))
    assert st.max_drawdown < 0


def test_pass_needs_the_close_not_the_intrabar_high() -> None:
    rows = [(10, 10.2, 9.8, 10)] * 5 + [(10, 11.2, 9.9, 11), (11.5, 12.2, 11.4, 12)]
    # A wick between 2R and 3R: the intrabar mark clears +2% (v2.2 booked a
    # PASS there) but neither the 3R target nor the close does.
    rows += [(12, 15.8, 11.9, 12.0)]
    st = replay(_bars(rows), PARAMS, PropLimits(starting_equity=10_000.0, profit_target=0.02))
    assert st.outcome != "pass"


def test_walk_forward_out_of_sample_only_uses_past_bars() -> None:
    params = StrategyParams(fast=10, slow=20, trend=50)
    c = _gbm(400, 11)
    limits = PropLimits(starting_equity=1e5)
    a = real_walk_forward(c, params, limits, train_bars=150, test_bars=60)
    assert len(a.folds) == 5
    assert [f.test_start for f in a.folds] == [150, 210, 270, 330, 390]
    assert all(f.test_end == min(f.test_start + 60, 400) for f in a.folds)
    # Scramble every bar after the first fold's test window: fold 1's chosen
    # parameters and OOS result cannot change (no look-ahead into the future).
    tail = _gbm(400, 99)[210:]
    b = real_walk_forward(c[:210] + tail, params, limits, train_bars=150, test_bars=60)
    assert (a.folds[0].fast, a.folds[0].slow) == (b.folds[0].fast, b.folds[0].slow)
    assert a.folds[0].oos_return == pytest.approx(b.folds[0].oos_return)
    assert a.oos_trades == len(a.oos_r_multiples) == sum(f.oos_trades for f in a.folds)
    json.dumps(a.model_dump())


def test_walk_forward_too_short_is_empty() -> None:
    res = real_walk_forward(_gbm(100, 1), StrategyParams(), PropLimits(starting_equity=1.0))
    assert res.folds == [] and res.oos_expectancy_r is None


def test_replay_exposes_a_close_marked_curve_and_ci() -> None:
    from cqd.engine.backtest import bootstrap_mean_ci

    params = StrategyParams(fast=10, slow=20, trend=50)
    c = _gbm(400, 5)
    st = replay(c, params, PropLimits(starting_equity=1e4, **RESEARCH))
    assert len(st.equity_curve) == len(st.curve_times) == st.bars
    assert st.curve_times == [int(b.time) for b in c]
    assert st.equity_curve[-1] == pytest.approx(st.final_equity)
    if st.trades >= 2:
        lo, hi = st.expectancy_r_ci
        assert lo <= st.expectancy_r <= hi
    assert bootstrap_mean_ci([1.0]) is None
    assert bootstrap_mean_ci([1.0, 1.0]) == (1.0, 1.0)
    # Deterministic: same input, same interval.
    assert bootstrap_mean_ci([0.5, -1.0, 2.0]) == bootstrap_mean_ci([0.5, -1.0, 2.0])
