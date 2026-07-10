"""Tests for the compact number formatter."""

from __future__ import annotations

import math

from cqd.ui.format import format_compact


def test_format_compact_suffixes() -> None:
    assert format_compact(1_234_567) == "1.23M"
    assert format_compact(2_500_000_000) == "2.50B"
    assert format_compact(3_400) == "3.40K"
    assert format_compact(1_500_000_000_000) == "1.50T"


def test_format_compact_small_and_sign() -> None:
    assert format_compact(950) == "950.00"
    assert format_compact(-1_200_000) == "-1.20M"
    assert format_compact(0) == "0.00"


def test_format_compact_nonfinite_is_dash() -> None:
    assert format_compact(float("nan")) == "—"
    assert format_compact(math.inf) == "—"
    assert format_compact(None) == "—"
