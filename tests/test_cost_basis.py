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
    assert result.fees_paid == 0.0026
    assert result.quote == "USD"
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


def test_buy_then_partial_sell_keeps_average_cost() -> None:
    # Regression (2026-07-09 audit): the old net-cash method divided
    # (buys - sell proceeds) by remaining qty, so profitable sells dragged the
    # "average cost" down (and below zero on big wins). A sell must release
    # basis at the running average and leave the remaining average unchanged.
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
    assert abs(result.avg_cost - 0.001) < 1e-12  # unchanged by the sell
    assert abs(result.total_cost - 0.8) < 1e-12  # basis released at avg
    # Realized: 200 sold at 0.002 against 0.001 basis = +0.2.
    assert abs(result.realized_pnl - 0.2) < 1e-12
    assert result.oversold is False


def test_profitable_sell_never_negative_basis() -> None:
    # Buy 1 @ 100, sell 0.5 @ 300: old method said avg_cost = -100.
    trades = [
        {"symbol": "SOL/USD", "side": "buy", "amount": 1.0, "price": 100.0, "cost": 100.0},
        {"symbol": "SOL/USD", "side": "sell", "amount": 0.5, "price": 300.0, "cost": 150.0},
    ]
    result = reconstruct_cost_basis(trades, "SOL")
    assert result.quantity == 0.5
    assert result.avg_cost == 100.0
    assert result.total_cost == 50.0
    assert result.realized_pnl == 100.0  # 0.5 * (300 - 100)


def test_oversell_flagged_not_guessed() -> None:
    # Selling more than tracked buys (transfer-in, history gap): the excess is
    # ignored and flagged, never turned into fabricated negative basis.
    trades = [
        {"symbol": "SOL/USD", "side": "buy", "amount": 1.0, "price": 100.0, "cost": 100.0},
        {"symbol": "SOL/USD", "side": "sell", "amount": 2.0, "price": 150.0, "cost": 300.0},
    ]
    result = reconstruct_cost_basis(trades, "SOL")
    assert result.oversold is True
    assert result.quantity == 0.0
    assert result.total_cost == 0.0
    assert result.avg_cost == 0.0
    assert result.realized_pnl == 50.0  # only the matched 1.0 realizes


def test_mixed_quotes_never_summed() -> None:
    # Regression (2026-07-09 audit): a BTC-quoted DOT buy was summed into the
    # USD total as if 0.001 BTC were 0.001 USD. Quotes must stay separate.
    trades = [
        {"symbol": "DOT/USD", "side": "buy", "amount": 10.0, "price": 5.0, "cost": 50.0},
        {"symbol": "DOT/BTC", "side": "buy", "amount": 10.0, "price": 0.0001, "cost": 0.001},
    ]
    from cqd.engine.cost_basis import cost_basis_by_quote

    by_quote = cost_basis_by_quote(trades, "DOT")
    assert set(by_quote) == {"USD", "BTC"}
    assert by_quote["USD"].total_cost == 50.0
    assert by_quote["BTC"].total_cost == 0.001
    assert by_quote["BTC"].quote == "BTC"

    # Explicit quote selection.
    usd = reconstruct_cost_basis(trades, "DOT", quote="USD")
    assert usd.total_cost == 50.0 and usd.quote == "USD"

    # Auto-selection is deterministic: equal quantities -> larger basis wins.
    dominant = reconstruct_cost_basis(trades, "DOT")
    assert dominant.quote == "USD"


def test_timestamp_order_not_input_order() -> None:
    # A sell that happened after a later-listed buy must process in time order.
    trades = [
        {
            "symbol": "ETH/USD",
            "side": "sell",
            "amount": 1.0,
            "price": 200.0,
            "cost": 200.0,
            "timestamp": 2000.0,
        },
        {
            "symbol": "ETH/USD",
            "side": "buy",
            "amount": 2.0,
            "price": 100.0,
            "cost": 200.0,
            "timestamp": 1000.0,
        },
    ]
    result = reconstruct_cost_basis(trades, "ETH")
    assert result.oversold is False
    assert result.quantity == 1.0
    assert result.avg_cost == 100.0
    assert result.realized_pnl == 100.0


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
