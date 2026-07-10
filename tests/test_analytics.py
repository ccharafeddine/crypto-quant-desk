"""Tests for the analytics panel's pure formatter (ratios math lives in engine)."""

from __future__ import annotations

import math

from cqd.ui.panels.analytics import format_metric


def test_format_metric_percent_and_ratio() -> None:
    assert format_metric(0.1234, is_percent=True) == "12.3%"
    assert format_metric(-0.05, is_percent=True) == "-5.0%"
    assert format_metric(1.876, is_percent=False) == "1.88"


def test_format_metric_none_and_nan_are_dash() -> None:
    assert format_metric(None, is_percent=True) == "—"
    assert format_metric(float("nan"), is_percent=False) == "—"
    assert format_metric(math.nan, is_percent=True) == "—"
