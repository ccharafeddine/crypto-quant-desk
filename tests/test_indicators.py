"""Tests for the indicator engine (pure, hand-computed vectors).

Includes the mandatory Donchian look-ahead regression: `test_donchian_*` fails
if the one-bar shift is removed from `indicators.donchian`.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from cqd.data.normalize import Candle
from cqd.engine.indicators import atr, donchian, rsi, sma


def _candles(rows: list[tuple[float, float, float]]) -> list[Candle]:
    """Build candles from (high, low, close) rows; open/time/volume are filler."""
    return [
        Candle(time=1000 + i * 60, open=c, high=h, low=lo, close=c, volume=1.0)
        for i, (h, lo, c) in enumerate(rows)
    ]


# --- sma ---


def test_sma_known_vector() -> None:
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = sma(s, 3)
    assert math.isnan(out.iloc[0]) and math.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(2.0)  # (1+2+3)/3
    assert out.iloc[3] == pytest.approx(3.0)
    assert out.iloc[4] == pytest.approx(4.0)


def test_sma_short_and_empty_return_nan_never_raise() -> None:
    assert sma(pd.Series([1.0, 2.0]), 5).isna().all()  # too short -> all NaN
    assert sma(pd.Series([], dtype=float), 3).empty  # empty in, empty out


def test_sma_bad_window_raises() -> None:
    with pytest.raises(ValueError):
        sma(pd.Series([1.0]), 0)


# --- atr ---


def test_atr_known_vector() -> None:
    # TR: bar0 = high-low = 2; bar1 = max(3, |12-9|, |9-9|) = 3; bar2 = max(1,0,1)=1.
    candles = _candles([(10.0, 8.0, 9.0), (12.0, 9.0, 11.0), (11.0, 10.0, 10.5)])
    out = atr(candles, 2)
    assert math.isnan(out.iloc[0])
    assert out.iloc[1] == pytest.approx(2.5)  # (2+3)/2
    assert out.iloc[2] == pytest.approx(2.0)  # (3+1)/2


def test_atr_empty_returns_empty() -> None:
    assert atr([], 3).empty


# --- donchian (look-ahead regression) ---


def test_donchian_excludes_current_bar() -> None:
    # highs [10,12,11,15], lows [8,9,10,7]; n=2. The last bar's own high (15) is
    # the series max, so a correct shifted channel MUST NOT include it.
    candles = _candles([(10.0, 8.0, 9.0), (12.0, 9.0, 11.0), (11.0, 10.0, 10.5), (15.0, 7.0, 14.0)])
    ch = donchian(candles, 2)
    # upper[-1] = max(high[1], high[2]) = max(12, 11) = 12, NOT 15 (current bar).
    assert ch.upper.iloc[-1] == pytest.approx(12.0)
    assert ch.upper.iloc[-1] != 15.0  # regression: fails if the shift is removed
    # lower[-1] = min(low[1], low[2]) = min(9, 10) = 9, NOT 7 (current bar).
    assert ch.lower.iloc[-1] == pytest.approx(9.0)
    assert ch.lower.iloc[-1] != 7.0
    # First n bars have no prior window.
    assert math.isnan(ch.upper.iloc[0]) and math.isnan(ch.upper.iloc[1])


def test_donchian_empty_returns_empty() -> None:
    ch = donchian([], 3)
    assert ch.upper.empty and ch.lower.empty


# --- rsi ---


def test_rsi_all_gains_is_100() -> None:
    out = rsi(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), 2)
    assert out.iloc[-1] == pytest.approx(100.0)


def test_rsi_alternating_is_50() -> None:
    # Equal average gain and loss -> RS = 1 -> RSI = 50.
    out = rsi(pd.Series([10.0, 11.0, 10.0, 11.0, 10.0]), 2)
    assert out.iloc[2:].round(6).eq(50.0).all()


def test_rsi_flat_window_is_50() -> None:
    out = rsi(pd.Series([5.0, 5.0, 5.0, 5.0]), 2)
    assert out.iloc[-1] == pytest.approx(50.0)


def test_rsi_all_losses_is_0() -> None:
    out = rsi(pd.Series([5.0, 4.0, 3.0, 2.0, 1.0]), 2)
    assert out.iloc[-1] == pytest.approx(0.0)


# --- guards + determinism ---


def test_nan_inputs_propagate_never_raise() -> None:
    s = pd.Series([1.0, np.nan, 3.0, 4.0])
    assert math.isnan(sma(s, 2).iloc[1])  # NaN in window -> NaN out, no raise
    assert not rsi(s, 2).empty


def test_determinism_same_input_same_output() -> None:
    s = pd.Series([1.0, 3.0, 2.0, 5.0, 4.0, 6.0])
    pd.testing.assert_series_equal(sma(s, 3), sma(s, 3))
    pd.testing.assert_series_equal(rsi(s, 3), rsi(s, 3))
    candles = _candles([(h, h - 2, h - 1) for h in (10.0, 11.0, 12.0, 13.0)])
    pd.testing.assert_series_equal(atr(candles, 2), atr(candles, 2))
