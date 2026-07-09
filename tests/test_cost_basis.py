"""Tests for cost basis reconstruction."""

from cqd.engine.cost_basis import reconstruct_cost_basis


def test_empty_trades() -> None:
    result = reconstruct_cost_basis([], "ADA")
    assert result.quantity == 0.0
    assert result.avg_cost == 0.0


def test_no_matching_asset() -> None:
    trades = [
        {
            "symbol": "BTC/USD",
            "side": "buy",
            "amount": 1.0,
            "price": 50000.0,
            "cost": 50000.0,
            "fee": {"cost": 130.0, "currency": "USD"},
        }
    ]
    result = reconstruct_cost_basis(trades, "ADA")
    assert result.quantity == 0.0


def test_single_buy() -> None:
    trades = [
        {
            "symbol": "ADA/USD",
            "side": "buy",
            "amount": 1000.0,
            "price": 0.001,
            "cost": 1.0,
            "fee": {"cost": 0.0026, "currency": "USD"},
        }
    ]
    result = reconstruct_cost_basis(trades, "ADA")
    assert result.quantity == 1000.0
    assert result.avg_cost == 0.001
    assert result.fees_paid_usd == 0.0026
    assert result.break_even_price == 0.001


def test_multiple_buys_running_average() -> None:
    trades = [
        {
            "symbol": "ADA/USD",
            "side": "buy",
            "amount": 1000.0,
            "price": 0.001,
            "cost": 1.0,
            "fee": {"cost": 0.0026, "currency": "USD"},
        },
        {
            "symbol": "ADA/USD",
            "side": "buy",
            "amount": 1000.0,
            "price": 0.002,
            "cost": 2.0,
            "fee": {"cost": 0.0052, "currency": "USD"},
        },
    ]
    result = reconstruct_cost_basis(trades, "ADA")
    assert result.quantity == 2000.0
    # 3.0 total cost / 2000 qty = 0.0015
    assert abs(result.avg_cost - 0.0015) < 1e-9


def test_buy_then_partial_sell() -> None:
    trades = [
        {
            "symbol": "ADA/USD",
            "side": "buy",
            "amount": 1000.0,
            "price": 0.001,
            "cost": 1.0,
            "fee": {"cost": 0.0026, "currency": "USD"},
        },
        {
            "symbol": "ADA/USD",
            "side": "sell",
            "amount": 200.0,
            "price": 0.002,
            "cost": 0.4,
            "fee": {"cost": 0.001, "currency": "USD"},
        },
    ]
    result = reconstruct_cost_basis(trades, "ADA")
    assert result.quantity == 800.0
    # Net cost = 1.0 - 0.4 = 0.6, divided by 800 = 0.00075
    assert abs(result.avg_cost - 0.00075) < 1e-9


def test_required_price_for_multiple() -> None:
    trades = [
        {
            "symbol": "ADA/USD",
            "side": "buy",
            "amount": 1000.0,
            "price": 0.001,
            "cost": 1.0,
            "fee": {"cost": 0.0026, "currency": "USD"},
        }
    ]
    result = reconstruct_cost_basis(trades, "ADA")
    assert abs(result.required_price_for_multiple(2.0) - 0.002) < 1e-9
    assert abs(result.required_price_for_multiple(10.0) - 0.01) < 1e-9
