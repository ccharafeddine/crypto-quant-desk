"""Tests for the depth-ladder panel's pure helpers.

Cumulative depth and spread formatting are pure; the ladder rendering and the
depth-bar delegate are verified visually via the app screenshot (per the
codebase convention of not constructing panels under pytest-qt).
"""

from __future__ import annotations

from cqd.ui.panels.book import cumulative_totals, format_spread


def test_cumulative_totals_running_sum_best_first() -> None:
    levels = [(100.0, 2.0), (99.0, 3.0), (98.0, 5.0)]
    assert cumulative_totals(levels) == [2.0, 5.0, 10.0]


def test_cumulative_totals_empty() -> None:
    assert cumulative_totals([]) == []


def test_format_spread_absolute_and_pct() -> None:
    # bid 100, ask 100.1 -> spread 0.1, mid 100.05 -> ~0.10%.
    text = format_spread(100.0, 100.1)
    assert text.startswith("0.1 (")
    assert text.endswith("%)")
    assert "0.10%" in text


def test_format_spread_zero_when_touching() -> None:
    assert format_spread(100.0, 100.0) == "0 (0.00%)"


def test_format_spread_guards_nonpositive_mid() -> None:
    assert format_spread(0.0, 0.0) == "-"
    assert format_spread(-5.0, 5.0) == "-"  # mid 0
