"""Tests for the candlestick chart panel's pure helpers.

Per the codebase convention (panels are verified through extracted pure logic,
not by constructing the widgets), this covers the nearest-candle / readout /
body-width transforms. The full render path - CandlestickItem painting, the
volume subplot, the cost-basis overlay - is verified visually via the app
screenshot, not with pyqtgraph widgets under pytest-qt (which drain-hangs the
event queue intermittently when combined with the QtAds workspace tests).
"""

from __future__ import annotations

from cqd.data.normalize import Candle
from cqd.ui.panels.chart import (
    candle_body_halfwidth,
    format_readout,
    nearest_candle,
)

_CANDLES = [
    Candle(1_718_150_400, 100.0, 110.0, 95.0, 108.0, 12.0),
    Candle(1_718_154_000, 108.0, 112.0, 104.0, 105.0, 9.0),
    Candle(1_718_157_600, 105.0, 106.0, 99.0, 101.0, 7.5),
]


# ---- pure helpers ----


def test_nearest_candle_picks_closest_time() -> None:
    assert nearest_candle(_CANDLES, 1_718_154_100) is _CANDLES[1]
    assert nearest_candle(_CANDLES, 0) is _CANDLES[0]  # clamps to first
    assert nearest_candle(_CANDLES, 9_999_999_999) is _CANDLES[2]  # clamps to last


def test_nearest_candle_empty_is_none() -> None:
    assert nearest_candle([], 123) is None


def test_body_halfwidth_is_40pct_of_spacing() -> None:
    # Bars are 3600s apart -> half body width 0.4 * 3600 = 1440.
    assert candle_body_halfwidth(_CANDLES) == 1440.0
    # A single candle can't infer spacing; falls back to a sane default.
    assert candle_body_halfwidth(_CANDLES[:1]) == 0.4
    assert candle_body_halfwidth([]) == 0.4


def test_format_readout_has_all_ohlcv_fields() -> None:
    text = format_readout(_CANDLES[0])
    for tag in ("O ", "H ", "L ", "C ", "V "):
        assert tag in text
    assert "108" in text  # close rendered
