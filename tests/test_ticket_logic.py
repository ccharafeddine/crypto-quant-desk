"""Tests for the pure UI-input -> OrderRequest mapping (no QApplication)."""

from cqd.trading.limits import PairSpec
from cqd.ui.panels.orders import live_order_to_row
from cqd.ui.panels.ticket import build_order_request

SPEC = PairSpec.from_asset_pairs_entry(
    "XXBTZUSD",
    {
        "altname": "XBTUSD",
        "wsname": "XBT/USD",
        "base": "XXBT",
        "quote": "ZUSD",
        "pair_decimals": 1,
        "lot_decimals": 8,
        "ordermin": "0.00005",
        "costmin": "0.5",
    },
)


def test_builds_limit_request_with_engine_slash_pair() -> None:
    req, problems = build_order_request(
        spec=SPEC,
        side="buy",
        ordertype="limit",
        volume_text="0.001",
        price_text="50,000",  # thousands separators tolerated
        mark=60_000.0,
    )
    assert problems == []
    assert req.pair == "BTC/USD"  # classic XXBT/ZUSD folded to engine symbols
    assert req.kraken_pair == "XBTUSD"
    assert req.volume == 0.001
    assert req.price == 50_000.0


def test_market_request_needs_no_price() -> None:
    req, problems = build_order_request(
        spec=SPEC, side="sell", ordertype="market", volume_text="0.5", mark=60_000.0
    )
    assert problems == []
    assert req.price is None


def test_input_problems_reported_not_raised() -> None:
    _, problems = build_order_request(
        spec=SPEC, side="buy", ordertype="limit", volume_text="abc", price_text=""
    )
    assert any("not a number" in p for p in problems)
    assert any("Price is required" in p for p in problems)


def test_missing_volume_reported() -> None:
    _, problems = build_order_request(
        spec=SPEC, side="buy", ordertype="market", volume_text="", mark=1.0
    )
    assert any("Volume is required" in p for p in problems)


def test_stop_limit_needs_price2() -> None:
    _, problems = build_order_request(
        spec=SPEC,
        side="sell",
        ordertype="stop-loss-limit",
        volume_text="0.1",
        price_text="55000",
        price2_text="",
    )
    assert any("limit-after-trigger" in p for p in problems)


def test_conditional_close_wired_through() -> None:
    req, problems = build_order_request(
        spec=SPEC,
        side="buy",
        ordertype="limit",
        volume_text="0.001",
        price_text="50000",
        close_type="stop-loss",
        close_price_text="45000",
        mark=60_000.0,
    )
    assert problems == []
    assert req.close_ordertype == "stop-loss"
    assert req.close_price == 45_000.0


def test_close_without_price_reported() -> None:
    _, problems = build_order_request(
        spec=SPEC,
        side="buy",
        ordertype="limit",
        volume_text="0.001",
        price_text="50000",
        close_type="stop-loss",
        close_price_text="",
    )
    assert any("Conditional close needs a price" in p for p in problems)


def test_live_order_to_row_maps_kraken_open_order() -> None:
    row = live_order_to_row(
        "OABC-1",
        {
            "vol": "0.0100000000",
            "descr": {
                "pair": "XXBTZUSD",
                "type": "buy",
                "ordertype": "limit",
                "price": "50000.0",
            },
        },
    )
    assert row.txid == "OABC-1"
    assert row.mode == "live"
    assert row.pair == "BTC/USD"
    assert row.side == "buy"
    assert row.volume == 0.01
    assert row.price_str == "50000.0"
