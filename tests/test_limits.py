"""Tests for order validation (pure, no I/O)."""

from cqd.trading.limits import PairSpec, validate_order

# Shaped like Kraken's real AssetPairs entry for XXBTZUSD.
BTC_ENTRY = {
    "altname": "XBTUSD",
    "wsname": "XBT/USD",
    "base": "XXBT",
    "quote": "ZUSD",
    "pair_decimals": 1,
    "lot_decimals": 8,
    "ordermin": "0.00005",
    "costmin": "0.5",
}

SPEC = PairSpec.from_asset_pairs_entry("XXBTZUSD", BTC_ENTRY)


def _ok(**kw) -> list[str]:
    args = dict(side="buy", ordertype="limit", volume=0.001, price=60000.0, mark=60000.0)
    args.update(kw)
    return validate_order(SPEC, **args)


def test_spec_from_asset_pairs_entry() -> None:
    assert SPEC.pair == "XBTUSD"
    assert SPEC.price_decimals == 1
    assert SPEC.lot_decimals == 8
    assert SPEC.ordermin == 0.00005
    assert SPEC.costmin == 0.5


def test_valid_limit_order_passes() -> None:
    assert _ok() == []


def test_market_order_uses_mark_for_value() -> None:
    assert _ok(ordertype="market", price=None) == []
    # Without a mark the cap cannot be checked: fail closed.
    problems = _ok(ordertype="market", price=None, mark=None)
    assert any("cannot be estimated" in p for p in problems)


def test_volume_minimum_and_precision() -> None:
    assert any("below the pair minimum" in p for p in _ok(volume=0.00001))
    assert any("Volume must be positive" in p for p in _ok(volume=0.0))
    # 9 decimals > lot_decimals 8.
    assert any("decimals" in p for p in _ok(volume=0.000000001))


def test_price_precision_enforced() -> None:
    # pair_decimals=1: 60000.05 has 2 decimals.
    assert any("decimals" in p for p in _ok(price=60000.05))
    assert _ok(price=60000.5) == []


def test_priced_types_require_price() -> None:
    for ot in ("limit", "stop-loss", "take-profit"):
        problems = _ok(ordertype=ot, price=None)
        assert any("requires a positive price" in p for p in problems), ot


def test_stop_loss_limit_requires_price2() -> None:
    problems = _ok(ordertype="stop-loss-limit", price=55000.0, price2=None)
    assert any("secondary" in p for p in problems)
    assert _ok(ordertype="stop-loss-limit", price=55000.0, price2=54900.0, volume=0.001) == []


def test_trailing_stop_offset_not_precision_checked() -> None:
    # Trailing price is a relative offset (e.g. +500), not a quote price.
    assert _ok(ordertype="trailing-stop", price=500.0) == []


def test_max_order_cap_blocks() -> None:
    problems = _ok(volume=0.02, price=60000.0, max_order_value=500.0)  # $1,200
    assert any("max order cap" in p for p in problems)
    assert _ok(volume=0.008, price=60000.0, max_order_value=500.0) == []  # $480


def test_costmin_enforced() -> None:
    # 0.000005 BTC * 60000 = $0.30 < costmin $0.50 (also below ordermin).
    problems = _ok(volume=0.000005, price=60000.0)
    assert any("minimum cost" in p for p in problems)


def test_multiple_violations_all_reported() -> None:
    problems = _ok(
        volume=0.00001,  # below ordermin
        price=60000.001,  # too many decimals
        max_order_value=0.1,  # exceeds cap
    )
    assert len(problems) >= 3


def test_unknown_type_and_side() -> None:
    assert any("Unsupported order type" in p for p in _ok(ordertype="iceberg"))
    assert any("Unknown side" in p for p in _ok(side="hold"))
