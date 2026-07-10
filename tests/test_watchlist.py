"""Tests for the watchlist panel's pure helpers.

Per convention, the table + sparkline rendering is verified visually; here we
cover the percent-change, pair-mapping, and sparkline-geometry transforms.
"""

from __future__ import annotations

from cqd.ui.panels.watchlist import pct_change, sparkline_points, to_ws_pair


def test_pct_change_basic() -> None:
    assert pct_change(110.0, 100.0) == 10.0
    assert pct_change(90.0, 100.0) == -10.0


def test_pct_change_guards_nonpositive_open() -> None:
    assert pct_change(100.0, 0.0) == 0.0
    assert pct_change(100.0, -5.0) == 0.0


def test_to_ws_pair_remaps_only_base() -> None:
    assert to_ws_pair("BTC/USD") == "XBT/USD"  # BTC -> XBT
    assert to_ws_pair("DOGE/USD") == "XDG/USD"  # DOGE -> XDG
    assert to_ws_pair("ETH/USD") == "ETH/USD"  # unchanged
    assert to_ws_pair("SOL/EUR") == "SOL/EUR"  # quote preserved


def test_sparkline_points_maps_into_box_with_inverted_y() -> None:
    pts = sparkline_points([1.0, 2.0, 3.0], width=100.0, height=10.0)
    assert len(pts) == 3
    # x spans 0..width evenly.
    assert [round(x, 4) for x, _y in pts] == [0.0, 50.0, 100.0]
    # y is inverted: min price -> bottom (height), max price -> top (0).
    assert pts[0][1] == 10.0  # lowest close at the bottom
    assert pts[-1][1] == 0.0  # highest close at the top


def test_sparkline_points_flat_series_no_div_by_zero() -> None:
    # Zero span must not divide by zero; a flat series renders a level line.
    pts = sparkline_points([5.0, 5.0], width=10.0, height=8.0)
    assert [y for _x, y in pts] == [8.0, 8.0]  # all at one level, no NaN/inf


def test_sparkline_points_too_short_is_empty() -> None:
    assert sparkline_points([], 10.0, 10.0) == []
    assert sparkline_points([1.0], 10.0, 10.0) == []
