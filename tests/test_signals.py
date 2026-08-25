"""Tests for the signal engine (pure, hand-computed vectors).

Covers the trend machine, entry/stop math, risk-based sizing (rounding,
fail-closed-below-min, leverage cap), the trend-only confidence score, advisory
prop-room fields, and determinism. Sizing property: realized per-trade risk is
never above `risk_pct*equity` (+ at most one tick of size).
"""

from __future__ import annotations

import pytest

from cqd.data.normalize import Candle
from cqd.engine.signals import (
    MAX_LEVERAGE,
    PairSpec,
    StrategyParams,
    TrendState,
    evaluate_setup,
    trend_state,
)


def _candles(closes: list[float], hl: float = 0.5, t0: int = 1000, step: int = 60) -> list[Candle]:
    return [
        Candle(time=t0 + i * step, open=c, high=c + hl, low=c - hl, close=c, volume=1.0)
        for i, c in enumerate(closes)
    ]


# Small windows so fixtures stay hand-verifiable: fast<slow<trend.
PARAMS = StrategyParams(
    fast=2, slow=3, trend=5, atr_len=2, atr_mult=2.0, risk_pct=0.01, variant="ma_cross"
)
# price_decimals=1, lot_decimals=3, ordermin tiny.
SPEC = PairSpec(symbol="BTC/USD", price_decimals=1, lot_decimals=3, ordermin=0.001)

# Steady +1 uptrend: trend MA(5) last = 14, price = 16 (> MA -> long regime);
# fast MA(2)=15.5 > slow MA(3)=15 -> LONG_ACTIVE. ATR(2) = 1.5 (see test_indicators).
UPTREND = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0]
DOWNTREND = [16.0, 15.0, 14.0, 13.0, 12.0, 11.0, 10.0]


# --- trend machine ---


def test_trend_state_active_on_uptrend() -> None:
    assert trend_state(_candles(UPTREND), PARAMS) == TrendState.LONG_ACTIVE


def test_trend_state_flat_below_trend_ma() -> None:
    assert trend_state(_candles(DOWNTREND), PARAMS) == TrendState.FLAT


def test_trend_state_flat_on_insufficient_history() -> None:
    assert trend_state(_candles([10.0, 11.0, 12.0]), PARAMS) == TrendState.FLAT


def test_trend_state_armed_when_regime_on_but_cross_not_confirmed() -> None:
    # Price above trend MA, but fast MA <= slow MA (a dip on the last bar).
    # trend MA(5) = (12+13+20+19+18.5)/5 = 16.5; price 18.5 > 16.5 -> regime on.
    # fast MA(2) = (19+18.5)/2 = 18.75; slow MA(3) = (20+19+18.5)/3 = 19.166 -> armed.
    closes = [10.0, 11.0, 12.0, 13.0, 20.0, 19.0, 18.5]
    assert trend_state(_candles(closes), PARAMS) == TrendState.LONG_ARMED


# --- entry / stop / sizing math ---


def test_evaluate_setup_entry_stop_size_confidence() -> None:
    setup = evaluate_setup(
        _candles(UPTREND),
        PARAMS,
        equity_quote=10_000.0,
        pair_spec=SPEC,
        start_of_day_equity=10_000.0,
        starting_equity=10_000.0,
    )
    assert setup is not None
    assert setup.direction == "long"
    assert setup.state == TrendState.LONG_ACTIVE
    assert setup.entry_ref == pytest.approx(16.0)  # ma_cross -> last close
    assert setup.stop == pytest.approx(13.0)  # 16 - 2 * ATR(1.5)
    # risk target 100 quote / risk-per-unit 3 = 33.333.. floored to 3 lot dec.
    assert setup.size_base == pytest.approx(33.333)
    assert setup.risk_quote == pytest.approx(99.999)  # 33.333 * 3
    assert setup.size_quote == pytest.approx(33.333 * 16.0)
    assert setup.targets == [pytest.approx(19.0), pytest.approx(22.0), pytest.approx(25.0)]
    assert setup.rr == pytest.approx(3.0)
    # Confidence: dist above trend MA (16-14)/14 = 0.1428 -> /0.10 clipped to 1;
    # slope term 0 (too few bars) -> 0.6 * 1 + 0.4 * 0 = 0.6.
    assert setup.confidence == pytest.approx(0.6)
    assert setup.created_ts == 1000 + 6 * 60  # last bar's open time
    # Advisory prop room: 10000 - 10000*0.97 = 300; 10000 - 10000*0.95 = 500.
    assert setup.daily_room == pytest.approx(300.0)
    assert setup.total_room == pytest.approx(500.0)


def test_evaluate_setup_flat_returns_none() -> None:
    assert evaluate_setup(_candles(DOWNTREND), PARAMS, 10_000.0, SPEC) is None


def test_prop_room_none_when_equity_inputs_absent() -> None:
    setup = evaluate_setup(_candles(UPTREND), PARAMS, 10_000.0, SPEC)
    assert setup is not None
    assert setup.daily_room is None and setup.total_room is None


# --- fail-closed below min size ---


def test_evaluate_setup_fails_closed_below_min_size() -> None:
    # Sub-minimum risk size must NOT be rounded up past the risk budget.
    spec = PairSpec(symbol="BTC/USD", price_decimals=1, lot_decimals=3, ordermin=1.0)
    setup = evaluate_setup(_candles(UPTREND), PARAMS, equity_quote=10.0, pair_spec=spec)
    assert setup is None  # 0.033 base < ordermin 1.0 -> fail closed


# --- leverage cap ---


def test_evaluate_setup_respects_leverage_cap() -> None:
    params = StrategyParams(
        fast=2, slow=3, trend=5, atr_len=2, atr_mult=2.0, risk_pct=0.95, variant="ma_cross"
    )
    equity = 10_000.0
    setup = evaluate_setup(_candles(UPTREND), params, equity_quote=equity, pair_spec=SPEC)
    assert setup is not None
    # risk size 0.95*10000/3 = 3166.7 exceeds cap 5*10000/16 = 3125 -> capped.
    assert setup.size_base == pytest.approx(3125.0)
    assert setup.size_quote == pytest.approx(MAX_LEVERAGE * equity)
    assert setup.size_quote <= MAX_LEVERAGE * equity + 1e-6


# --- sizing property: realized risk never exceeds budget (+ one tick) ---


@pytest.mark.parametrize("equity", [1_000.0, 5_000.0, 12_345.67, 99_999.0, 250_000.0])
def test_realized_risk_within_budget(equity: float) -> None:
    setup = evaluate_setup(_candles(UPTREND), PARAMS, equity_quote=equity, pair_spec=SPEC)
    if setup is None:  # fail-closed cases carry no risk
        return
    tick = 10 ** (-SPEC.price_decimals)
    budget = PARAMS.risk_pct * equity
    assert setup.risk_quote <= budget + setup.size_base * tick + 1e-9


# --- breakout variant ---


def test_breakout_variant_uses_donchian_level_as_entry() -> None:
    params = StrategyParams(
        fast=2, slow=3, trend=5, atr_len=2, atr_mult=2.0, risk_pct=0.01, variant="breakout"
    )
    # Flat then a breakout on the last bar; prior 2-bar high (excl. current) = 10.5.
    closes = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 20.0]
    candles = _candles(closes)
    assert trend_state(candles, params) == TrendState.LONG_ACTIVE
    setup = evaluate_setup(candles, params, equity_quote=100_000.0, pair_spec=SPEC)
    assert setup is not None
    assert setup.entry_ref == pytest.approx(10.5)  # prior Donchian upper, not 20


# --- determinism ---


def test_evaluate_setup_is_deterministic() -> None:
    a = evaluate_setup(_candles(UPTREND), PARAMS, 10_000.0, SPEC, start_of_day_equity=9_000.0)
    b = evaluate_setup(_candles(UPTREND), PARAMS, 10_000.0, SPEC, start_of_day_equity=9_000.0)
    assert a is not None and b is not None
    assert a.model_dump() == b.model_dump()


# --- param validation ---


def test_strategy_params_reject_unordered_windows() -> None:
    with pytest.raises(ValueError):
        StrategyParams(fast=50, slow=20, trend=200)


def test_strategy_params_reject_risk_out_of_range() -> None:
    with pytest.raises(ValueError):
        StrategyParams(risk_pct=1.5)
