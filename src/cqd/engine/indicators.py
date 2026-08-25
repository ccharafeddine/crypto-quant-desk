"""Technical indicators: pure functions over price series / candle lists.

Everything here is a deterministic transform of a `pd.Series` or a
`list[Candle]` - no I/O, no Qt, no network, no randomness. Length is always
preserved and undefined leading values are `NaN` (never dropped, never raised),
so callers can align an indicator against the bars it was computed from.

Two deliberate choices, both documented so the tests can hand-compute them:

  1. `atr` uses a SIMPLE mean of the true range over `n`, not Wilder's RMA
     smoothing. Simple mean is exactly hand-verifiable and matches the `sma`
     convention used elsewhere in the engine.
  2. `rsi` uses SIMPLE rolling averages of gains/losses ("Cutler's RSI"), not
     Wilder's smoothing, for the same hand-verifiability reason. A flat window
     (no gains, no losses) returns 50; an all-gains window returns 100.

`donchian` returns the PRIOR `n`-bar high/low SHIFTED by one bar so the current
bar is excluded. That shift is the look-ahead-bug guard (the channel a live
strategy could actually have seen at bar close never contains bar `i`'s own
high/low). `tests/test_indicators.py` pins it with a regression test that fails
if the shift is removed.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
import pandas as pd

from cqd.data.normalize import Candle


def _require_window(n: int) -> None:
    """A window length is a programmer-supplied constant; a non-positive one is
    a bug, not a data condition, so it raises rather than returning NaN."""
    if n < 1:
        raise ValueError(f"window n must be >= 1, got {n}")


def sma(series: pd.Series, n: int) -> pd.Series:
    """Simple moving average over `n` observations.

    Returns a same-length series; the first `n-1` entries are `NaN`. An empty
    or too-short series yields `NaN` without raising.
    """
    _require_window(n)
    return series.rolling(n).mean()


def atr(candles: list[Candle], n: int) -> pd.Series:
    """Average True Range over `n` bars (simple mean of true range).

    True range at bar `i` is `max(high-low, |high-prev_close|, |low-prev_close|)`;
    the first bar has no prior close, so its TR is `high-low`. Returns a
    same-length series indexed 0..len-1 with `NaN` until `n` TR values exist.
    An empty candle list yields an empty series.
    """
    _require_window(n)
    if not candles:
        return pd.Series([], dtype=float)
    highs = pd.Series([c.high for c in candles], dtype=float)
    lows = pd.Series([c.low for c in candles], dtype=float)
    closes = pd.Series([c.close for c in candles], dtype=float)
    prev_close = closes.shift(1)
    # skipna max: at bar 0 the prev-close columns are NaN, so TR[0] = high-low.
    true_range = pd.concat(
        [highs - lows, (highs - prev_close).abs(), (lows - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(n).mean()


class Donchian(NamedTuple):
    """The prior-`n`-bar channel: `upper` (rolling high) and `lower` (rolling
    low), both SHIFTED by one bar so the current bar is excluded."""

    upper: pd.Series
    lower: pd.Series


def donchian(candles: list[Candle], n: int) -> Donchian:
    """Prior `n`-bar high/low, shifted to EXCLUDE the current bar.

    `upper[i] = max(high[i-n .. i-1])`, `lower[i] = min(low[i-n .. i-1])`. The
    `.shift(1)` is the look-ahead guard: the channel never includes bar `i`'s
    own high/low, so a breakout of `upper[i]` by `close[i]` is a real signal the
    strategy could have acted on. Same-length series; `NaN` until `n` prior bars
    exist. Empty candle list yields two empty series.
    """
    _require_window(n)
    if not candles:
        empty = pd.Series([], dtype=float)
        return Donchian(empty, empty.copy())
    highs = pd.Series([c.high for c in candles], dtype=float)
    lows = pd.Series([c.low for c in candles], dtype=float)
    upper = highs.rolling(n).max().shift(1)
    lower = lows.rolling(n).min().shift(1)
    return Donchian(upper, lower)


def rsi(series: pd.Series, n: int) -> pd.Series:
    """Relative Strength Index over `n` (simple-average / Cutler's variant).

    `avg_gain`/`avg_loss` are simple rolling means of the up/down moves;
    `RSI = 100 - 100/(1 + avg_gain/avg_loss)`. Special cases keep the value
    finite and hand-checkable: a window with no losses returns 100, a window
    with no gains returns 0, and a perfectly flat window (neither) returns 50.
    Same-length series; `NaN` until `n+1` observations exist (one diff is lost).
    """
    _require_window(n)
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.rolling(n).mean()
    avg_loss = loss.rolling(n).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
    out = 100.0 - 100.0 / (1.0 + rs)
    # avg_loss == 0: all-up window -> 100, flat window (no gain either) -> 50.
    out = out.mask((avg_loss == 0.0) & (avg_gain > 0.0), 100.0)
    out = out.mask((avg_loss == 0.0) & (avg_gain == 0.0), 50.0)
    # avg_gain == 0 with losses -> RSI 0 (the formula already gives this, but
    # pin it explicitly against float noise in rs).
    out = out.mask((avg_gain == 0.0) & (avg_loss > 0.0), 0.0)
    return out


__all__ = ["Donchian", "atr", "donchian", "rsi", "sma"]
