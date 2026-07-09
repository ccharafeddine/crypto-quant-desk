"""Tests for the PaperBroker simulator (pure, tmp-file persistence)."""

import pytest

from cqd.data.errors import OrderRejected
from cqd.trading.paper import PaperBroker


def _broker(tmp_path=None, **kw) -> PaperBroker:
    state = (tmp_path / "paper_state.json") if tmp_path else None
    b = PaperBroker(state, **kw)
    b.seed_if_empty({"USD": 10_000.0, "BTC": 0.5})
    return b


# ---------- market ----------


def test_market_buy_fills_with_slippage_and_taker_fee() -> None:
    b = _broker(slippage_bps=10.0, taker_fee=0.004)
    o = b.submit(pair="BTC/USD", side="buy", ordertype="market", volume=0.1, mark=60_000.0)
    assert o.status == "filled"
    assert o.filled_price == pytest.approx(60_000.0 * 1.001)  # +10 bps
    cost = 0.1 * o.filled_price
    assert o.fee == pytest.approx(cost * 0.004)
    assert b.balances["BTC"] == pytest.approx(0.6)
    assert b.balances["USD"] == pytest.approx(10_000.0 - cost - o.fee)


def test_market_sell_slips_down() -> None:
    b = _broker(slippage_bps=10.0)
    o = b.submit(pair="BTC/USD", side="sell", ordertype="market", volume=0.5, mark=60_000.0)
    assert o.filled_price == pytest.approx(60_000.0 * 0.999)
    assert b.balances["BTC"] == pytest.approx(0.0)


def test_market_insufficient_funds_rejects() -> None:
    b = _broker()
    with pytest.raises(OrderRejected):
        b.submit(pair="BTC/USD", side="buy", ordertype="market", volume=1.0, mark=60_000.0)
    assert b.open_orders() == []  # nothing left dangling


# ---------- limit ----------


def test_resting_limit_fills_on_cross_at_limit_price_with_maker_fee() -> None:
    b = _broker(taker_fee=0.004, maker_fee=0.0025)
    o = b.submit(
        pair="BTC/USD", side="buy", ordertype="limit", volume=0.1, price=59_000.0, mark=60_000.0
    )
    assert o.is_open
    assert b.on_tick("BTC/USD", 59_500.0) == []  # not crossed
    fills = b.on_tick("BTC/USD", 58_900.0)
    assert [f.txid for f in fills] == [o.txid]
    assert o.filled_price == 59_000.0  # at the limit, not the mark
    assert o.fee == pytest.approx(0.1 * 59_000.0 * 0.0025)  # maker


def test_marketable_limit_fills_immediately_as_taker() -> None:
    b = _broker(taker_fee=0.004)
    o = b.submit(
        pair="BTC/USD", side="buy", ordertype="limit", volume=0.1, price=61_000.0, mark=60_000.0
    )
    assert o.status == "filled"
    assert o.filled_price == 61_000.0
    assert o.fee == pytest.approx(0.1 * 61_000.0 * 0.004)


# ---------- stops ----------


def test_stop_loss_sell_triggers_on_fall() -> None:
    b = _broker(slippage_bps=0.0)
    b.submit(
        pair="BTC/USD",
        side="sell",
        ordertype="stop-loss",
        volume=0.2,
        price=55_000.0,
        mark=60_000.0,
    )
    assert b.on_tick("BTC/USD", 56_000.0) == []
    fills = b.on_tick("BTC/USD", 54_800.0)
    assert fills and fills[0].filled_price == pytest.approx(54_800.0)


def test_stop_loss_limit_converts_to_limit_then_fills() -> None:
    b = _broker()
    o = b.submit(
        pair="BTC/USD",
        side="sell",
        ordertype="stop-loss-limit",
        volume=0.2,
        price=55_000.0,  # trigger
        price2=54_500.0,  # limit after trigger
        mark=60_000.0,
    )
    assert b.on_tick("BTC/USD", 54_900.0) == []  # triggered, now resting limit
    assert o.triggered is True and o.is_open
    fills = b.on_tick("BTC/USD", 54_600.0)  # sell limit 54,500 crossed (mark above)
    assert fills and fills[0].filled_price == 54_500.0


def test_take_profit_sell_triggers_on_rise() -> None:
    b = _broker(slippage_bps=0.0)
    b.submit(
        pair="BTC/USD",
        side="sell",
        ordertype="take-profit",
        volume=0.1,
        price=65_000.0,
        mark=60_000.0,
    )
    assert b.on_tick("BTC/USD", 64_000.0) == []
    assert len(b.on_tick("BTC/USD", 65_500.0)) == 1


def test_trailing_stop_ratchets_with_best_mark() -> None:
    b = _broker(slippage_bps=0.0)
    o = b.submit(
        pair="BTC/USD",
        side="sell",
        ordertype="trailing-stop",
        volume=0.1,
        price=1_000.0,
        mark=60_000.0,
    )
    # Market rises: trigger ratchets up to best - offset.
    assert b.on_tick("BTC/USD", 62_000.0) == []
    assert b.on_tick("BTC/USD", 61_500.0) == []  # 61,500 > 62,000 - 1,000
    fills = b.on_tick("BTC/USD", 60_900.0)  # <= 61,000 trigger
    assert fills and fills[0].txid == o.txid


# ---------- cancel + fill-time funds ----------


def test_cancel_and_cancel_all() -> None:
    b = _broker()
    o1 = b.submit(
        pair="BTC/USD", side="buy", ordertype="limit", volume=0.01, price=50_000.0, mark=60_000.0
    )
    o2 = b.submit(
        pair="BTC/USD", side="buy", ordertype="limit", volume=0.01, price=49_000.0, mark=60_000.0
    )
    assert b.cancel(o1.txid).status == "cancelled"
    with pytest.raises(OrderRejected):
        b.cancel(o1.txid)  # already closed
    assert b.cancel_all() == 1
    assert o2.status == "cancelled"
    assert b.open_orders() == []


def test_fill_time_insufficiency_cancels_not_overdraws() -> None:
    b = _broker()
    # Two resting buys that individually fit but together overdraw.
    b.submit(
        pair="BTC/USD", side="buy", ordertype="limit", volume=0.15, price=59_000.0, mark=60_000.0
    )
    b.submit(
        pair="BTC/USD", side="buy", ordertype="limit", volume=0.15, price=59_000.0, mark=60_000.0
    )
    fills = b.on_tick("BTC/USD", 58_000.0)
    assert len(fills) == 1  # first fills, second cancels
    statuses = sorted(o.status for o in b.orders.values())
    assert statuses == ["cancelled", "filled"]
    assert b.balances["USD"] >= 0.0


# ---------- persistence ----------


def test_state_survives_restart(tmp_path) -> None:
    b = _broker(tmp_path)
    o = b.submit(
        pair="BTC/USD", side="buy", ordertype="limit", volume=0.01, price=50_000.0, mark=60_000.0
    )
    b2 = PaperBroker(tmp_path / "paper_state.json")
    assert b2.balances == b.balances
    assert [x.txid for x in b2.open_orders()] == [o.txid]
    # Sequence continues, no txid reuse.
    o2 = b2.submit(
        pair="BTC/USD", side="buy", ordertype="limit", volume=0.01, price=49_000.0, mark=60_000.0
    )
    assert o2.txid != o.txid


def test_corrupt_state_backed_up_and_fresh(tmp_path) -> None:
    state = tmp_path / "paper_state.json"
    state.write_text("{not json", encoding="utf-8")
    b = PaperBroker(state)
    assert b.balances == {} and b.orders == {}
    assert (tmp_path / "paper_state.json.bak").exists()
