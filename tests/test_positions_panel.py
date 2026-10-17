"""Tests for the Positions panel's pure cost-basis formatting (no QApplication)."""

import pytest

from cqd.engine.cost_basis import CostBasisResult, reconstruct_cost_basis
from cqd.ui.panels.positions import format_cost_basis


def test_format_cost_basis_with_values() -> None:
    cb = CostBasisResult(
        asset="BTC",
        quantity=0.5,
        total_cost=35000.0,
        avg_cost=70000.0,
        fees_paid=56.0,
    )
    avg, be = format_cost_basis(cb)
    assert avg == "$70,000.000000"
    # break_even_price == avg_cost by the engine's definition.
    assert be == "$70,000.000000"


def test_format_cost_basis_non_usd_quote_is_labeled() -> None:
    # BTC-quoted basis must render in BTC, never with a dollar sign.
    cb = CostBasisResult(
        asset="DOT",
        quantity=10.0,
        total_cost=0.001,
        avg_cost=0.0001,
        fees_paid=0.0,
        quote="BTC",
    )
    avg, be = format_cost_basis(cb)
    assert avg == "0.00010000 BTC"
    assert be == avg
    assert "$" not in avg


def test_format_cost_basis_none_is_dash() -> None:
    assert format_cost_basis(None) == ("-", "-")


def test_format_cost_basis_zero_basis_is_dash() -> None:
    # No trades for the asset -> engine returns a zero result, shown as "-".
    cb = CostBasisResult("DOGE", 0.0, 0.0, 0.0, 0.0)
    assert format_cost_basis(cb) == ("-", "-")


def test_no_trade_history_asset_shows_dash() -> None:
    # Asset held but absent from the trade list -> "-" (not a crash or zero).
    trades = [
        {
            "symbol": "BTC/USD",
            "side": "buy",
            "amount": 0.5,
            "price": 70000.0,
            "cost": 35000.0,
            "fee": {"cost": 56.0, "currency": "USD"},
        }
    ]
    cb = reconstruct_cost_basis(trades, "SOL")  # no SOL trades
    assert format_cost_basis(cb) == ("-", "-")


def test_reconstruct_then_format_offline() -> None:
    # End-to-end pure path: normalized trades -> engine -> formatted cells.
    trades = [
        {
            "symbol": "ADA/USD",
            "side": "buy",
            "amount": 2_000_000.0,
            "price": 0.0009,
            "cost": 1800.0,
            "fee": {"cost": 2.88, "currency": "USD"},
        },
        {
            "symbol": "ADA/USD",
            "side": "buy",
            "amount": 1_000_000.0,
            "price": 0.0007,
            "cost": 700.0,
            "fee": {"cost": 1.12, "currency": "USD"},
        },
    ]
    cb = reconstruct_cost_basis(trades, "ADA")
    # 3,000,000 ADA for $2,500 total -> avg 0.000833...
    assert cb.quantity == 3_000_000.0
    avg, be = format_cost_basis(cb)
    assert avg == f"${cb.avg_cost:,.6f}"
    assert be == avg  # break-even == avg cost
    assert avg != "-"


def test_book_totals_feed_the_header() -> None:
    from cqd.ui.panels.positions import book_totals

    rows = [("BTC", 0.1), ("USD", 1000.0), ("USDC", 500.0)]
    marks = {"BTC": 80_000.0, "USD": 1.0, "USDC": 1.0}
    out = book_totals(rows, marks, {"BTC": 70_000.0})
    assert out["nav"] == pytest.approx(9_500.0)
    assert out["unrealized"] == pytest.approx(1_000.0)
    assert out["holdings"] == 1
    assert out["cash_pct"] == pytest.approx(1500 / 9500)
    assert book_totals([], {}, {})["cash_pct"] is None
