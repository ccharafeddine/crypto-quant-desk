"""Tests for the performance engine (pure, deterministic timestamps)."""

import pandas as pd
import pytest

from cqd.engine.performance import (
    RoundTrip,
    balances_over_time,
    build_equity_curve,
    drawdown_stats,
    monthly_return_table,
    periodic_returns,
    realized_pnl_by_asset,
    realized_trades,
    trade_stats,
)

# Day anchors (unix seconds).
D1, D2, D3, D4 = 86400, 172800, 259200, 345600


def _ledger(asset, amount, balance, time):
    return {
        "refid": "R",
        "time": float(time),
        "type": "trade",
        "subtype": "",
        "asset": asset,
        "amount": float(amount),
        "fee": 0.0,
        "balance": float(balance),
    }


# ---------- balances over time ----------


def test_balances_ffill_and_no_retroactive_deposits() -> None:
    ledgers = [
        _ledger("USD", 1000.0, 1000.0, D1),  # deposit
        _ledger("USD", -500.0, 500.0, D2),  # spent on BTC
        _ledger("BTC", 0.01, 0.01, D2),
    ]
    bal = balances_over_time(ledgers)
    assert bal.loc[pd.Timestamp("1970-01-02"), "USD"] == 1000.0
    assert bal.loc[pd.Timestamp("1970-01-03"), "USD"] == 500.0
    # BTC did not exist before its first entry: implied prior balance is 0.
    assert bal.loc[pd.Timestamp("1970-01-02"), "BTC"] == 0.0
    assert bal.loc[pd.Timestamp("1970-01-03"), "BTC"] == 0.01


def test_balances_accumulate_when_running_balance_missing() -> None:
    ledgers = [
        {**_ledger("SOL", 5.0, 0.0, D1), "balance": None},
        {**_ledger("SOL", 3.0, 0.0, D2), "balance": None},
    ]
    bal = balances_over_time(ledgers)
    assert bal.loc[pd.Timestamp("1970-01-02"), "SOL"] == 5.0
    assert bal.loc[pd.Timestamp("1970-01-03"), "SOL"] == 8.0


# ---------- equity curve ----------


def test_equity_curve_values_holdings_at_closes() -> None:
    ledgers = [
        _ledger("USD", 1000.0, 1000.0, D1),
        _ledger("USD", -500.0, 500.0, D2),
        _ledger("BTC", 0.01, 0.01, D2),
    ]
    closes = {"BTC": [(D1, 50_000.0), (D2, 50_000.0), (D3, 60_000.0)]}
    eq = build_equity_curve(ledgers, closes)
    assert eq.loc[pd.Timestamp("1970-01-02")] == pytest.approx(1000.0)
    assert eq.loc[pd.Timestamp("1970-01-03")] == pytest.approx(500.0 + 0.01 * 50_000.0)
    # Curve extends to the last close date even without new ledger entries.
    assert eq.loc[pd.Timestamp("1970-01-04")] == pytest.approx(500.0 + 0.01 * 60_000.0)


def test_equity_curve_unpriced_asset_excluded_and_reported() -> None:
    ledgers = [
        _ledger("USD", 100.0, 100.0, D1),
        _ledger("MYSTERY", 5.0, 5.0, D1),
    ]
    eq = build_equity_curve(ledgers, {})
    assert eq.iloc[-1] == pytest.approx(100.0)  # cash only, not zero-priced
    assert eq.attrs["unpriced"] == ["MYSTERY"]


def test_equity_curve_empty_inputs() -> None:
    assert build_equity_curve([], {}).empty


# ---------- realized trades ----------


def _trade(symbol, side, amount, price, ts):
    return {
        "symbol": symbol,
        "side": side,
        "amount": amount,
        "price": price,
        "cost": amount * price,
        "fee": {"cost": 0.0, "currency": symbol.split("/")[1]},
        "timestamp": float(ts),
    }


def test_realized_trades_average_cost_semantics() -> None:
    trades = [
        _trade("SOL/USD", "buy", 1.0, 100.0, D1),
        _trade("SOL/USD", "sell", 0.5, 300.0, D2),
    ]
    rts = realized_trades(trades)
    assert len(rts) == 1
    rt = rts[0]
    assert rt.asset == "SOL" and rt.quote == "USD"
    assert rt.quantity == 0.5
    assert rt.entry_avg == 100.0
    assert rt.pnl == pytest.approx(100.0)  # 0.5 * (300 - 100)


def test_realized_pnl_by_asset_groups_usd_only_and_sorts() -> None:
    trades = [
        _trade("SOL/USD", "buy", 1.0, 100.0, D1),
        _trade("SOL/USD", "sell", 1.0, 300.0, D2),  # +200 realized
        _trade("ADA/USD", "buy", 10.0, 1.0, D1),
        _trade("ADA/USD", "sell", 10.0, 0.5, D2),  # -5 realized
        _trade("DOT/BTC", "buy", 10.0, 0.0001, D1),
        _trade("DOT/BTC", "sell", 10.0, 0.0002, D2),  # BTC-quoted: excluded
    ]
    out = realized_pnl_by_asset(realized_trades(trades))
    assert out == {"SOL": pytest.approx(200.0), "ADA": pytest.approx(-5.0)}
    assert list(out) == ["SOL", "ADA"]  # sorted by PnL descending
    assert "DOT" not in out  # non-USD quote skipped


def test_monthly_return_table_pivots_year_by_month() -> None:
    idx = pd.date_range("2025-01-01", periods=120, freq="D")
    equity = pd.Series(range(100, 220), index=idx, dtype=float)  # steadily rising
    table = monthly_return_table(equity)
    assert not table.empty
    assert table.index.name == "year" and 2025 in table.index
    # Columns are month numbers; a rising curve gives positive monthly returns.
    assert (table.loc[2025].dropna() > 0).all()


def test_monthly_return_table_empty_history() -> None:
    assert monthly_return_table(pd.Series(dtype=float)).empty


def test_realized_trades_oversell_skipped_and_quotes_separate() -> None:
    trades = [
        _trade("DOT/USD", "sell", 1.0, 10.0, D1),  # no basis: skipped
        _trade("DOT/BTC", "buy", 10.0, 0.0001, D1),
        _trade("DOT/BTC", "sell", 10.0, 0.0002, D2),
    ]
    rts = realized_trades(trades)
    assert len(rts) == 1
    assert rts[0].quote == "BTC"
    assert rts[0].pnl == pytest.approx(10.0 * 0.0001)


def test_trade_stats_expectancy_and_profit_factor() -> None:
    rts = [
        RoundTrip("A", "USD", 1, 1, 2, 100.0, 0.0, D1),
        RoundTrip("A", "USD", 1, 1, 2, 50.0, 0.0, D2),
        RoundTrip("B", "USD", 1, 2, 1, -50.0, 0.0, D3),
    ]
    s = trade_stats(rts)
    assert s["trades"] == 3
    assert s["win_rate"] == pytest.approx(2 / 3)
    assert s["avg_win"] == pytest.approx(75.0)
    assert s["avg_loss"] == pytest.approx(-50.0)
    assert s["expectancy"] == pytest.approx(100.0 / 3)
    assert s["profit_factor"] == pytest.approx(3.0)
    assert s["total_realized"] == pytest.approx(100.0)


def test_trade_stats_fees_reduce_pnl_and_empty_is_nan() -> None:
    rts = [RoundTrip("A", "USD", 1, 1, 2, 10.0, 4.0, D1)]
    assert trade_stats(rts)["total_realized"] == pytest.approx(6.0)
    empty = trade_stats([])
    assert empty["trades"] == 0
    assert empty["win_rate"] != empty["win_rate"]  # NaN


# ---------- drawdown + periodic ----------


def test_drawdown_stats() -> None:
    idx = pd.date_range("2025-01-01", periods=6, freq="D")
    eq = pd.Series([100.0, 110.0, 99.0, 95.0, 104.0, 112.0], index=idx)
    s = drawdown_stats(eq)
    assert s["max_drawdown"] == pytest.approx(95.0 / 110.0 - 1.0)
    assert s["current_drawdown"] == pytest.approx(0.0)
    assert s["underwater_days"] == 0
    assert s["max_underwater_days"] == 3  # days 3-5 below the 110 peak


def test_periodic_returns_shapes() -> None:
    idx = pd.date_range("2025-01-01", periods=60, freq="D")
    eq = pd.Series(range(100, 160), index=idx, dtype=float)
    r = periodic_returns(eq)
    assert len(r["daily"]) == 59
    assert (r["daily"] > 0).all()
    assert len(r["weekly"]) >= 7
    assert len(r["monthly"]) >= 1
