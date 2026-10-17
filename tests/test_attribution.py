"""Beta attribution, drawdown table and fee drag (engine/attribution.py)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from cqd.engine.attribution import beta_attribution, drawdown_table, fee_drag


def test_attribution_parts_add_up_to_the_active_return() -> None:
    rng = np.random.default_rng(0)
    idx = pd.date_range("2025-01-01", periods=400)
    btc = rng.normal(0.001, 0.03, 400)
    rets = pd.DataFrame({"BTC": btc, "ETH": 1.3 * btc + rng.normal(0, 0.01, 400)}, index=idx)
    w = pd.Series({"BTC": 0.5, "ETH": 0.3, "USD": 0.2})
    att = beta_attribution(w, rets, pd.Series({"BTC": 1.0, "ETH": 1.3}), window=365)
    assert att.window_days == 365
    assert att.allocation + att.btc_beta + att.selection == pytest.approx(att.active)
    assert att.active == pytest.approx(att.portfolio - att.btc)
    assert att.allocation == pytest.approx(-0.2 * att.btc)  # 20% cash vs fully invested
    assert set(att.per_asset) == {"BTC", "ETH"}


def test_attribution_needs_btc() -> None:
    frame = pd.DataFrame({"ETH": [0.1]})
    assert beta_attribution(pd.Series({"ETH": 1.0}), frame, pd.Series(dtype=float)) is None


def test_drawdown_table_finds_episodes_and_recoveries() -> None:
    idx = pd.date_range("2026-01-01", periods=9)
    eq = pd.Series([100, 90, 80, 100, 110, 99, 105, 111, 100], index=idx, dtype=float)
    table = drawdown_table(eq)
    assert [round(d.depth, 4) for d in table] == [-0.2, -0.1, -0.0991]
    first = table[0]
    assert first.peak == idx[0] and first.trough == idx[2] and first.recovery == idx[3]
    assert (first.days_to_trough, first.days_to_recover, first.underwater_days) == (2, 1, 3)
    open_dd = table[2]
    assert open_dd.recovery is None and open_dd.days_to_recover is None
    assert drawdown_table(pd.Series([1.0])) == []


def test_fee_drag_is_annualised_over_average_equity() -> None:
    idx = pd.date_range("2025-01-01", periods=366)
    assert fee_drag(100.0, pd.Series(10_000.0, index=idx)).annual_drag == pytest.approx(0.01)
    assert math.isnan(fee_drag(1.0, pd.Series([1.0])).annual_drag)
