"""Time-weighted returns: deposits and withdrawals are flows, never performance."""

from __future__ import annotations

import pandas as pd
import pytest

from cqd.engine import metrics as M
from cqd.engine.performance import (
    build_equity_curve,
    build_twr,
    drawdown_stats,
    external_flows,
    flow_adjusted_returns,
    monthly_return_table,
)

T0 = 1_700_006_400  # 2023-11-15 00:00 UTC (midnight, so day weights are exact)
DAY = 86_400


def _usd(t, amount, balance, kind="deposit"):
    return {"time": t, "type": kind, "asset": "USD", "amount": amount, "balance": balance}


def _flat_closes(n=60, px=1.0):
    return {"BTC": [(T0 + i * DAY, px) for i in range(n)]}


def test_deposit_only_account_has_zero_return() -> None:
    ledgers = [_usd(T0, 10_000, 10_000), _usd(T0 + 30 * DAY, 10_000, 20_000)]
    _, twr = build_twr(ledgers, _flat_closes())
    r = twr.pct_change().dropna()
    s = M.ratio_summary(r)
    assert s["ann_return"] == pytest.approx(0.0, abs=1e-12)
    assert s["max_drawdown"] == pytest.approx(0.0, abs=1e-12)
    # The old path: equity.pct_change() read the deposit as a +100% day.
    old = build_equity_curve(ledgers, _flat_closes()).pct_change().dropna()
    assert old.max() == pytest.approx(1.0)
    assert M.ratio_summary(old)["ann_return"] > 10  # the 7,183%-style artefact


def test_withdrawal_is_not_a_drawdown() -> None:
    ledgers = [_usd(T0, 10_000, 10_000), _usd(T0 + 10 * DAY, -6_000, 4_000, "withdrawal")]
    equity, twr = build_twr(ledgers, _flat_closes())
    assert drawdown_stats(equity)["max_drawdown"] == pytest.approx(-0.6)  # old reading
    assert drawdown_stats(twr)["max_drawdown"] == pytest.approx(0.0, abs=1e-12)


def test_real_gain_survives_a_deposit_modified_dietz() -> None:
    # 1 BTC held; BTC goes 100 -> 110 on day 1, and $1,000 cash arrives at 12:00.
    ledgers = [
        {"time": T0, "type": "deposit", "asset": "BTC", "amount": 1.0, "balance": 1.0},
        _usd(T0 + DAY + DAY // 2, 1_000, 1_000),
    ]
    closes = {"BTC": [(T0, 100.0), (T0 + DAY, 110.0), (T0 + 2 * DAY, 110.0)]}
    equity, twr = build_twr(ledgers, closes)
    r = twr.pct_change().dropna()
    # (1110 - 100 - 1000) / (100 + 0.5 * 1000) = 10 / 600
    assert r.iloc[0] == pytest.approx(10 / 600)
    assert r.iloc[1] == pytest.approx(0.0)


def test_crypto_deposit_valued_at_that_days_close() -> None:
    index = pd.date_range(pd.to_datetime(T0, unit="s"), periods=3, freq="D")
    ledgers = [{"time": T0 + DAY, "type": "deposit", "asset": "BTC", "amount": 0.5}]
    closes = {"BTC": [(T0, 100.0), (T0 + DAY, 200.0), (T0 + 2 * DAY, 300.0)]}
    f = external_flows(ledgers, closes, index)
    assert f["flow"].tolist() == [0.0, 100.0, 0.0]
    assert f["weighted"].iloc[1] == pytest.approx(100.0)  # midnight: in all day


def test_trades_and_unknown_types_are_not_flows() -> None:
    index = pd.date_range(pd.to_datetime(T0, unit="s"), periods=2, freq="D")
    ledgers = [
        {"time": T0, "type": "trade", "asset": "USD", "amount": -500.0},
        {"time": T0, "type": "staking", "asset": "USD", "amount": 3.0},
    ]
    f = external_flows(ledgers, {}, index)
    assert f["flow"].abs().sum() == 0.0


def test_twr_index_pct_change_matches_flow_adjusted_returns() -> None:
    ledgers = [_usd(T0, 10_000, 10_000), _usd(T0 + 5 * DAY, 2_000, 12_000)]
    equity, twr = build_twr(ledgers, _flat_closes(20))
    flows = external_flows(ledgers, _flat_closes(20), equity.index)
    pd.testing.assert_series_equal(
        twr.pct_change().dropna(), flow_adjusted_returns(equity, flows), check_names=False
    )
    assert twr.iloc[0] == 1.0


def test_monthly_table_on_twr_ignores_deposit() -> None:
    ledgers = [_usd(T0, 10_000, 10_000), _usd(T0 + 40 * DAY, 10_000, 20_000)]
    _, twr = build_twr(ledgers, _flat_closes(90))
    table = monthly_return_table(twr)
    assert table.abs().max().max() == pytest.approx(0.0, abs=1e-12)
