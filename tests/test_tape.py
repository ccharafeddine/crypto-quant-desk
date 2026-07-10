"""Tests for the time & sales tape's pure helper.

The streaming table (prepend, cap, side coloring) is verified visually; the
timestamp formatter is pure.
"""

from __future__ import annotations

from cqd.ui.panels.tape import format_trade_time


def test_format_trade_time_extracts_hms() -> None:
    assert format_trade_time("2026-07-09T07:49:37.708706Z") == "07:49:37"
    assert format_trade_time("2026-07-09T23:00:01Z") == "23:00:01"


def test_format_trade_time_handles_missing() -> None:
    assert format_trade_time("") == ""
    # No 'T' separator -> best-effort first 8 chars, never raises.
    assert format_trade_time("bogus") == "bogus"
