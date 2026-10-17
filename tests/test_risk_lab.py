"""Risk lab: pure row builders and the compute orchestration (no network)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from cqd.data.normalize import Candle
from cqd.data.portfolio import AccountRisk
from cqd.engine.risk import compute_portfolio_risk
from cqd.engine.signals import StrategyParams
from cqd.ui.panels import risk_lab as L


def _account(n: int = 400) -> AccountRisk:
    rng = np.random.default_rng(5)
    idx = pd.date_range("2025-01-01", periods=n)
    btc = rng.normal(0.0, 0.03, n)
    rets = pd.DataFrame(
        {
            "BTC": btc,
            "ETH": 1.2 * btc + rng.normal(0, 0.015, n),
            "SOL": 1.4 * btc + rng.normal(0, 0.03, n),
        },
        index=idx,
    )
    w = pd.Series({"BTC": 0.5, "ETH": 0.25, "SOL": 0.15, "USD": 0.10})
    risk = compute_portfolio_risk(w, rets)
    return AccountRisk(risk=risk, weights=w, total_usd=20_000.0, info={}, returns=rets)


def _candles(n: int = 400, start: float = 100.0, seed: int = 1) -> list[Candle]:
    rng = np.random.default_rng(seed)
    c = start * np.exp(np.cumsum(rng.normal(0, 0.03, n)))
    return [
        Candle(1_700_000_000 + i * 86400, c[i], c[i] * 1.01, c[i] * 0.99, c[i], 50.0)
        for i in range(n)
    ]


def test_compute_lab_fills_every_tab() -> None:
    ar = _account()
    candles = {"BTC": _candles(seed=1), "ETH": _candles(seed=2), "SOL": _candles(seed=3)}
    equity = pd.Series(np.linspace(100, 120, 200), index=pd.date_range("2025-06-01", periods=200))
    trades = [
        {
            "symbol": "BTC/USD",
            "side": "buy",
            "amount": 0.1,
            "cost": 10.0,
            "fee": {"cost": 0.04, "currency": "USD"},
            "timestamp": 1,
        },
    ]
    res = L.compute_lab(
        ar,
        candles,
        equity,
        trades,
        candles["BTC"],
        StrategyParams(fast=10, slow=30, trend=100),
        10_000.0,
        equity * 100,
    )
    assert res.suite is not None and res.n_days == 399  # 400 daily candles -> 399 returns
    assert len(res.backtests) == 4 and all(bt.n == 149 for bt in res.backtests)
    assert [r.key for r in res.replays] == ["luna_2022", "ftx_2022", "yen_carry_2024", "oct10_2025"]
    assert all(r.portfolio_return < 0 for r in res.replays)
    assert res.fan is not None and res.fan.n_paths > 0
    assert [row[0] for row in res.liquidity] == ["BTC", "ETH", "SOL"]
    assert res.attribution is not None
    assert len(res.lots) == 1 and res.lots[0].asset == "BTC"
    assert res.stability is not None and res.fees is not None
    assert L.es_975(ar) is not None and L.es_975(ar) < 0


def test_rows_are_formatted_for_display() -> None:
    ar = _account()
    res = L.compute_lab(ar, {}, pd.Series(dtype=float), [], [], None)
    rows = L.var_rows(res.suite, res.nav)
    assert [r[0] for r in rows] == [
        "Historical",
        "Filtered historical (EWMA)",
        "Parametric (normal)",
        "Cornish-Fisher",
    ]
    assert all(r[1].startswith("−") and r[2].startswith("−$") for r in rows)
    bt = L.backtest_rows(res.backtests)
    assert all(r[4] in {"Pass", "Reject"} for r in bt)
    rep = L.replay_rows(res.replays, res.nav)
    assert rep[0][1] == "2022-05-05 → 2022-05-12" and rep[0][3].startswith("−")
    assert any("proxied" in r[5] or r[5] == "all real" for r in rep)
    attr = dict((k, v) for k, v, _t in L.attribution_rows(res.attribution))
    assert "Active return" in attr
    assert L.attribution_rows(None)[0][1] == "needs BTC history"
    assert "Not enough" in L.stability_summary(None)
    es = L.es_rows(res.suite, res.nav)
    assert es[0][0] == "ES 97.5% · historical" and es[-1][1].startswith("−$")


def test_drawdown_and_lot_rows() -> None:
    from cqd.engine.attribution import drawdown_table
    from cqd.engine.tax_lots import compare_methods

    idx = pd.date_range("2026-01-01", periods=5)
    rows = L.drawdown_rows(drawdown_table(pd.Series([10.0, 8.0, 10.0, 9.0, 9.5], index=idx)))
    assert rows[0] == ("−20.0%", "2026-01-01", "2026-01-02", "2026-01-03", "1 d", "1 d")
    assert rows[1][3] == "open" and rows[1][5] == "—"
    t = [{"symbol": "BTC/USD", "side": "buy", "amount": 1.0, "cost": 100.0, "timestamp": 0}]
    lots = L.lot_rows([compare_methods(t, "BTC", mark=150.0)])
    assert [r[1] for r in lots] == ["Average cost", "FIFO", "HIFO"]
    assert all(r[4] == "+$50.00" for r in lots)


def test_lab_is_a_reachable_card_on_the_risk_page() -> None:
    from cqd.ui.shell import nav_items
    from cqd.ui.workspace import PANEL_KEYS, page_panels

    assert "lab" in PANEL_KEYS and "lab" in page_panels("Risk")
    assert any(item.focus == "lab" and item.page == "Risk" for item in nav_items())


def test_kpi_tail_cell_reads_es_975() -> None:
    from cqd.ui.kpis import KpiState, build_kpis

    cells = build_kpis(KpiState(nav=10_000.0, var95=-0.04, es=-0.07))
    var_cell = next(c for c in cells if c.label.startswith("1-day VaR"))
    assert "ES 97.5%" in var_cell.sub and "−$700.00" in var_cell.sub


def test_long_returns_use_the_full_candle_history() -> None:
    frame = L.long_returns({"BTC": _candles(30, seed=1), "ETH": _candles(20, seed=2)})
    assert list(frame.columns) == ["BTC", "ETH"] and len(frame) == 29
    assert frame["ETH"].isna().sum() == 10  # shorter history stays missing, never 0%
    assert L.long_returns({}).empty
