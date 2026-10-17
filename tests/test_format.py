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


# ---------- Graphite formatting rules: fixed notation, one pair spelling ----------

from cqd.ui.format import (  # noqa: E402
    display_pair,
    format_pct,
    format_price,
    format_qty,
    format_usd,
    tone,
)


def test_quantities_never_use_scientific_notation() -> None:
    # Regression: the tape showed 6e-05 BTC.
    assert format_qty(6e-05) == "0.00006"
    assert format_qty(0.12012) == "0.12012"
    assert format_qty(3998.0) == "3,998"
    assert format_qty(1e-9) == "0"
    assert "e" not in format_qty(0.00000123)


def test_prices_scale_decimals_by_magnitude() -> None:
    assert format_price(81416.4) == "81,416.40"
    assert format_price(2417.11) == "2,417.11"
    assert format_price(104.37) == "104.37"
    assert format_price(0.2241) == "0.2241"
    assert format_price(0.00001234) == "0.00001234"
    assert format_price(float("nan")) == "—"


def test_money_and_percent_carry_units_and_true_minus() -> None:
    assert format_usd(24886.38) == "$24,886.38"
    assert format_usd(-55.76) == "−$55.76"
    assert format_usd(212.4, signed=True) == "+$212.40"
    assert format_pct(0.118, signed=True) == "+11.8%"
    assert format_pct(-0.241) == "−24.1%"


def test_one_pair_spelling_everywhere() -> None:
    # Regression: the chart said XBTUSD while every other card said BTC/USD.
    for spelling in ("XBTUSD", "XBT/USD", "BTCUSD", "BTC/USD", "XXBTZUSD"):
        assert display_pair(spelling) == "BTC/USD"
    assert display_pair("ADAUSD") == "ADA/USD"


def test_tone_is_only_for_signed_values() -> None:
    assert (tone(1.0), tone(-1.0), tone(0.0), tone(None)) == ("pos", "neg", "", "")
