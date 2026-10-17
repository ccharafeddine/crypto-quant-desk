"""Tests for cost basis reconstruction."""

import pytest

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
    # Buy fee is capitalized: (1.0 + 0.0026) / 1000. Was 0.001 (fee ignored).
    assert result.avg_cost == pytest.approx(0.0010026)
    assert result.fees_paid == 0.0026
    assert result.quote == "USD"
    assert result.break_even_price == pytest.approx(0.0010026)


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
    # (3.0 cost + 0.0078 fees) / 2000 qty
    assert result.avg_cost == pytest.approx(3.0078 / 2000)


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
    assert result.avg_cost == pytest.approx(0.0010026)  # unchanged by the sell
    assert result.total_cost == pytest.approx(800 * 0.0010026)  # released at avg
    # Realized: 200 x (0.002 - 0.0010026) less the 0.001 sell fee.
    assert result.realized_pnl == pytest.approx(200 * (0.002 - 0.0010026) - 0.001)
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
    assert result.required_price_for_multiple(2.0) == pytest.approx(0.0020052)
    assert result.required_price_for_multiple(10.0) == pytest.approx(0.010026)


def test_buy_fee_raises_break_even_regression() -> None:
    # 1 BTC bought at 50,000 with a 0.40% taker fee: break-even is 50,200, not
    # 50,000. Selling at exactly 50,000 (fee 200) must realize -400, not 0.
    trades = [
        {
            "symbol": "BTC/USD",
            "side": "buy",
            "amount": 1.0,
            "price": 50_000.0,
            "cost": 50_000.0,
            "fee": {"cost": 200.0, "currency": "USD"},
        },
    ]
    held = reconstruct_cost_basis(trades, "BTC")
    assert held.break_even_price == pytest.approx(50_200.0)
    trades.append(
        {
            "symbol": "BTC/USD",
            "side": "sell",
            "amount": 1.0,
            "price": 50_000.0,
            "cost": 50_000.0,
            "fee": {"cost": 200.0, "currency": "USD"},
        }
    )
    assert reconstruct_cost_basis(trades, "BTC").realized_pnl == pytest.approx(-400.0)


def test_fee_in_another_currency_is_not_mixed_into_quote_basis() -> None:
    trades = [
        {
            "symbol": "BTC/USD",
            "side": "buy",
            "amount": 1.0,
            "price": 100.0,
            "cost": 100.0,
            "fee": {"cost": 0.001, "currency": "BTC"},
        },
    ]
    assert reconstruct_cost_basis(trades, "BTC").avg_cost == pytest.approx(100.0)
