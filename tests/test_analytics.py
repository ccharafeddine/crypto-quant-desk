"""Tests for the analytics panel's pure formatter (ratios math lives in engine)."""

from __future__ import annotations

import math

from PySide6.QtGui import QColor

from cqd.ui.panels.analytics import diverging_color, format_metric


def test_format_metric_percent_and_ratio() -> None:
    assert format_metric(0.1234, is_percent=True) == "12.3%"
    assert format_metric(-0.05, is_percent=True) == "-5.0%"
    assert format_metric(1.876, is_percent=False) == "1.88"


def test_format_metric_none_and_nan_are_dash() -> None:
    assert format_metric(None, is_percent=True) == "—"
    assert format_metric(float("nan"), is_percent=False) == "—"
    assert format_metric(math.nan, is_percent=True) == "—"


def test_diverging_color_endpoints_and_midpoint() -> None:
    neg, mid, pos = QColor(255, 0, 0), QColor(20, 20, 20), QColor(0, 255, 0)
    assert diverging_color(0.0, neg, mid, pos).getRgb()[:3] == (20, 20, 20)  # midpoint
    assert diverging_color(1.0, neg, mid, pos).getRgb()[:3] == (0, 255, 0)  # full positive
    assert diverging_color(-1.0, neg, mid, pos).getRgb()[:3] == (255, 0, 0)  # full negative
    # Clamps out-of-range and interpolates halfway.
    assert diverging_color(2.0, neg, mid, pos).getRgb()[:3] == (0, 255, 0)
    assert diverging_color(0.5, neg, mid, pos).getRgb()[:3] == (10, 137, 10)
