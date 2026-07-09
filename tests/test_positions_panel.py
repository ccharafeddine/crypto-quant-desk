"""Tests for the Positions panel's pure cost-basis formatting (no QApplication)."""

from cqd.engine.cost_basis import CostBasisResult, reconstruct_cost_basis
from cqd.ui.panels.positions import format_cost_basis


def test_format_cost_basis_with_values() -> None:
    cb = CostBasisResult(
        asset="BTC",
        quantity=0.5,
        total_cost_usd=35000.0,
        avg_cost=70000.0,
        fees_paid_usd=56.0,
    )
    avg, be = format_cost_basis(cb)
    assert avg == "$70,000.000000"
    # break_even_price == avg_cost by the engine's definition.
    assert be == "$70,000.000000"


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
