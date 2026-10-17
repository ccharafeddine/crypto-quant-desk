"""Header + KPI strip model (pure) and the overview helpers behind it."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from cqd.ui.kpis import KpiState, build_kpis, day_pnl, header_values
from cqd.ui.panels.overview import (
    benchmark_curves,
    closes_series,
    flow_markers,
    rebased_pct,
    trailing_return,
    window,
)


def test_empty_state_renders_dashes_not_zeros() -> None:
    kpis = build_kpis(KpiState())
    assert len(kpis) == 8
    assert [k.label for k in kpis][:4] == ["NAV", "Unrealized P&L", "Realized", "Return 1Y (TWR)"]
    assert all(k.value == "—" for k in kpis)
    assert header_values(KpiState()) == ("—", "—", "")


def test_kpis_carry_units_and_pnl_tones_only() -> None:
    state = KpiState(
        nav=24886.38,
        holdings=4,
        cash_pct=0.16,
        unrealized=-55.76,
        realized=1204.18,
        fees=86.4,
        twr_1y=0.118,
        btc_1y=0.142,
        ann_vol=0.386,
        ewma_vol=0.326,
        btc_beta=0.96,
        var95=-0.035,
        es=-0.057,
        max_drawdown=-0.241,
        underwater_days=47,
    )
    k = {kpi.label: kpi for kpi in build_kpis(state)}
    assert k["NAV"].value == "$24,886.38" and k["NAV"].sub == "4 holdings · 16% cash"
    assert (k["Unrealized P&L"].value, k["Unrealized P&L"].tone) == ("−$55.76", "neg")
    assert k["Realized"].sub == "net of $86.40 fees" and k["Realized"].tone == "pos"
    assert k["Return 1Y (TWR)"].value == "+11.8%"
    assert k["Return 1Y (TWR)"].sub == "BTC +14.2% same window"
    assert k["Ann. vol"].tone == "" and k["BTC beta"].tone == ""  # no green/red on risk
    assert k["1-day VaR 95%"].value == "−$871.02"
    assert k["Max drawdown"].sub == "underwater 47 d"


def test_day_pnl_is_since_the_last_close_with_flows_removed() -> None:
    # A deposit after the close must not read as a gain in the header.
    s = KpiState(nav=26_000.0, last_close_value=20_000.0, flows_since_close=5_000.0)
    usd, frac = day_pnl(s)
    assert usd == pytest.approx(1_000.0) and frac == pytest.approx(0.05)
    nav, day, tone = header_values(s)
    assert (nav, day, tone) == ("$26,000.00", "+$1,000.00 (+5.00%)", "pos")
    assert day_pnl(KpiState(nav=1.0, last_close_value=0.0)) == (None, None)


def _series(values, start="2025-01-01") -> pd.Series:
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="D"))


def test_window_rebase_and_trailing_return() -> None:
    s = _series(np.linspace(100, 200, 400))
    assert len(window(s, "1M")) == 31 and len(window(s, "All")) == 400
    r = rebased_pct(window(s, "1Y"))
    assert r.iloc[0] == 0.0 and r.iloc[-1] > 0
    assert trailing_return(s, 365) == pytest.approx(s.iloc[-1] / s.iloc[-366] - 1)
    assert trailing_return(pd.Series(dtype=float)) is None


def test_benchmark_aligns_btc_to_portfolio_dates() -> None:
    twr = _series([1.0, 1.1, 1.21, 1.331])
    btc = closes_series([(int(ts.timestamp()), 100.0 * (i + 1)) for i, ts in enumerate(twr.index)])
    port, bench = benchmark_curves(twr, btc, "All")
    assert list(port.index) == list(bench.index)
    assert port.iloc[-1] == pytest.approx(33.1)
    assert bench.iloc[-1] == pytest.approx(300.0)


def test_flow_markers_label_amounts() -> None:
    idx = pd.date_range("2025-01-01", periods=5, freq="D")
    flows = pd.DataFrame({"flow": [0, 5000.0, 0, -1500.0, 0], "weighted": 0.0}, index=idx)
    marks = flow_markers(flows)
    assert [m.label for m in marks] == ["+$5,000 deposit", "−$1,500 withdrawal"]
    assert flow_markers(flows, idx[2])[0].amount == -1500.0
    assert flow_markers(None) == []


def test_demo_book_twr_ignores_its_deposit_and_withdrawal() -> None:
    # The demo ledger carries a +5,000 deposit and a -5,000 withdrawal on flat
    # cash; the time-weighted index must not jump on either day.
    import asyncio

    from cqd.data.demo import _DEMO_FLOWS, SAMPLE_BOOK, DemoClient
    from cqd.engine.performance import build_twr

    class _Flat:
        async def get_ohlc_closes(self, *a, **k):
            return []

    ledgers = asyncio.run(DemoClient(_Flat()).get_ledgers())
    usd = [e for e in ledgers if e["asset"] == "USD"]
    assert usd[-1]["balance"] == pytest.approx(SAMPLE_BOOK["USD"])
    assert min(e["balance"] for e in usd) >= 0
    days = pd.date_range("2025-10-01", "2026-07-01", freq="D")
    closes = {a: [(int(d.timestamp()), 1.0) for d in days] for a in ("BTC", "ETH", "SOL", "ADA")}
    equity, twr = build_twr(ledgers, closes)
    for _refid, ts, _amt, _kind in _DEMO_FLOWS:
        day = pd.Timestamp(int(ts), unit="s").normalize()
        before = twr[twr.index < day].iloc[-1]
        assert math.isclose(twr.loc[day], before, rel_tol=1e-9)
