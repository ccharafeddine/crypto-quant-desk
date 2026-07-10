"""Tests for the static crypto-sector map and exposure aggregation (pure)."""

from __future__ import annotations

import pandas as pd
import pytest

from cqd.data.sectors import sector_exposure, sector_of


def test_sector_of_known_and_unknown() -> None:
    assert sector_of("BTC") == "L1"
    assert sector_of("uni") == "DeFi"  # case-insensitive
    assert sector_of("DOGE") == "Meme"
    assert sector_of("USD") == "Cash / Stable"
    assert sector_of("WOOF") == "Other"  # unmapped -> Other


def test_sector_exposure_aggregates_and_sorts() -> None:
    weights = {"BTC": 0.4, "ETH": 0.2, "DOGE": 0.3, "USD": 0.1}
    exp = sector_exposure(weights)
    assert exp["L1"] == pytest.approx(0.6)  # BTC + ETH
    assert exp["Meme"] == pytest.approx(0.3)
    assert exp["Cash / Stable"] == pytest.approx(0.1)
    # Sorted by weight descending.
    assert list(exp) == ["L1", "Meme", "Cash / Stable"]


def test_sector_exposure_accepts_pandas_series() -> None:
    exp = sector_exposure(pd.Series({"BTC": 0.5, "SOL": 0.5}))
    assert exp == {"L1": 1.0}
