"""Tests for the order-flow engine (pure, hand-walked books).

Book features, wall detection, expected fill (VWAP/slippage/partial/sweep), and
the conservative entry-timing truth table (unknown/thin/one-sided -> WAIT,
never GO). Guards: empty/one-sided books return None fields, never raise.
"""

from __future__ import annotations

import pytest

from cqd.engine.microstructure import (
    BookFeatures,
    Wall,
    book_features,
    detect_walls,
    entry_timing,
    expected_fill,
)
from cqd.engine.signals import TradeSetup, TrendState


def _setup(direction: str = "long", entry_ref: float = 100.0) -> TradeSetup:
    """A minimal setup for entry_timing (only direction/entry_ref are read)."""
    return TradeSetup(
        symbol="BTC/USD",
        direction=direction,  # type: ignore[arg-type]
        state=TrendState.LONG_ACTIVE,
        entry_ref=entry_ref,
        stop=entry_ref * 0.97,
        size_base=1.0,
        size_quote=entry_ref,
        risk_quote=entry_ref * 0.03,
        targets=[entry_ref * 1.03],
        rr=1.0,
        confidence=0.5,
        rationale="test",
        created_ts=1000,
    )


# --- book_features ---


def test_book_features_known_values() -> None:
    depth = {
        "bids": [(99.0, 2.0), (98.0, 3.0)],
        "asks": [(101.0, 1.0), (102.0, 4.0)],
    }
    f = book_features(depth, depth_window_bps=250.0)
    assert f.mid == pytest.approx(100.0)
    assert f.spread_abs == pytest.approx(2.0)
    assert f.spread_bps == pytest.approx(200.0)  # 2/100 * 1e4
    # microprice = (99*1 + 101*2)/(2+1) = 301/3.
    assert f.microprice == pytest.approx(301.0 / 3.0)
    # L5 imbalance: bid 5, ask 5 -> 0.
    assert f.imbalance_l5 == pytest.approx(0.0)
    # Within 2.5% window both levels each side are counted.
    assert f.depth_bid_bps == pytest.approx(5.0)
    assert f.depth_ask_bps == pytest.approx(5.0)


def test_book_features_imbalance_leans_to_heavier_side() -> None:
    depth = {"bids": [(99.0, 8.0), (98.0, 2.0)], "asks": [(101.0, 1.0), (102.0, 1.0)]}
    f = book_features(depth)
    # bid 10, ask 2 -> (10-2)/12 = 0.6667.
    assert f.imbalance_l5 == pytest.approx(8.0 / 12.0)


def test_book_features_one_sided_returns_none_fields() -> None:
    f = book_features({"bids": [], "asks": [(101.0, 1.0)]})
    assert f.mid is None and f.microprice is None and f.imbalance_l10 is None


def test_book_features_empty_returns_none_fields() -> None:
    f = book_features({"bids": [], "asks": []})
    assert f == BookFeatures()


# --- detect_walls ---


def test_detect_walls_flags_outlier_level() -> None:
    depth = {
        "bids": [(100.0, 1.0), (99.0, 1.0), (98.0, 1.0)],
        "asks": [(101.0, 1.0), (102.0, 1.0), (103.0, 1.0), (104.0, 10.0), (105.0, 1.0)],
    }
    walls = detect_walls(depth, z_thresh=2.0)
    assert len(walls) == 1
    w = walls[0]
    assert w.side == "ask" and w.price == pytest.approx(104.0) and w.size == pytest.approx(10.0)
    assert w.z == pytest.approx(2.0)  # (10 - 2.8)/3.6


def test_detect_walls_none_below_threshold() -> None:
    depth = {
        "bids": [(100.0, 1.0), (99.0, 1.0), (98.0, 1.0)],
        "asks": [(101.0, 1.0), (102.0, 1.0), (103.0, 1.0), (104.0, 10.0), (105.0, 1.0)],
    }
    assert detect_walls(depth, z_thresh=3.0) == []  # z = 2.0 < 3.0


def test_detect_walls_uniform_side_has_no_walls() -> None:
    depth = {
        "bids": [(100.0, 5.0), (99.0, 5.0), (98.0, 5.0)],
        "asks": [(101.0, 5.0), (102.0, 5.0), (103.0, 5.0)],
    }
    assert detect_walls(depth) == []


# --- expected_fill ---


def test_expected_fill_single_level() -> None:
    depth = {"bids": [(100.0, 10.0)], "asks": [(101.0, 1.0), (102.0, 2.0), (103.0, 5.0)]}
    est = expected_fill(depth, "buy", notional=100.0)
    assert est.vwap == pytest.approx(101.0)  # filled entirely in level 1
    assert est.levels_consumed == 1
    assert est.partial is False
    # mid = 100.5; slippage = (101 - 100.5)/100.5 * 1e4.
    assert est.slippage_bps == pytest.approx((101.0 - 100.5) / 100.5 * 1e4)


def test_expected_fill_partial_when_book_too_thin() -> None:
    depth = {"bids": [(100.0, 10.0)], "asks": [(101.0, 1.0), (102.0, 2.0), (103.0, 5.0)]}
    est = expected_fill(depth, "buy", notional=1000.0)
    # Consumes 101*1 + 102*2 + 103*5 = 820 over 8 base; vwap 820/8 = 102.5.
    assert est.levels_consumed == 3
    assert est.vwap == pytest.approx(102.5)
    assert est.partial is True


def test_expected_fill_sweeps_wall() -> None:
    # 11 ask levels of size 1 except a size-20 wall at 103 (z > 3).
    asks = [(101.0 + i, 20.0 if i == 2 else 1.0) for i in range(11)]
    depth = {"bids": [(100.0, 5.0)], "asks": asks}
    assert any(w.price == pytest.approx(103.0) for w in detect_walls(depth))  # wall exists
    est = expected_fill(depth, "buy", notional=300.0)  # walks into the 103 wall
    assert est.sweeps_wall is True


def test_expected_fill_nonpositive_and_empty_guards() -> None:
    depth = {"bids": [(100.0, 1.0)], "asks": [(101.0, 1.0)]}
    zero = expected_fill(depth, "buy", notional=0.0)
    assert zero.vwap is None and zero.partial is False
    empty = expected_fill({"bids": [(100.0, 1.0)], "asks": []}, "buy", notional=50.0)
    assert empty.vwap is None and empty.partial is True


# --- entry_timing truth table (conservative) ---


def _feats(spread_bps: float, imb: float, microprice: float | None = None) -> BookFeatures:
    return BookFeatures(
        mid=100.0,
        microprice=microprice,
        spread_abs=100.0 * spread_bps / 1e4,
        spread_bps=spread_bps,
        imbalance_l5=imb,
        imbalance_l10=imb,
        imbalance_l25=imb,
        depth_bid_bps=50.0,
        depth_ask_bps=50.0,
    )


def test_entry_timing_no_setup_is_wait() -> None:
    assert entry_timing(None, _feats(5.0, 0.3)).verdict == "WAIT"


def test_entry_timing_unknown_book_is_wait() -> None:
    assert entry_timing(_setup(), None).verdict == "WAIT"
    assert entry_timing(_setup(), BookFeatures()).verdict == "WAIT"  # one-sided/thin


def test_entry_timing_go_on_strong_support() -> None:
    v = entry_timing(_setup(), _feats(5.0, 0.3, microprice=100.05))
    # tight +0.25, strong imbalance +1.0, microprice +0.25 = 1.5 >= GO.
    assert v.verdict == "GO" and v.score == pytest.approx(1.5)


def test_entry_timing_caution_on_mild_support() -> None:
    v = entry_timing(_setup(), _feats(5.0, 0.1))
    # tight +0.25, supportive +0.5 = 0.75 -> CAUTION (below GO threshold 1.0).
    assert v.verdict == "CAUTION"


def test_entry_timing_wait_on_adverse_imbalance() -> None:
    v = entry_timing(_setup(), _feats(5.0, -0.3))
    # adverse imbalance is a hard block; score 0.25 - 1.0 = -0.75 -> WAIT.
    assert v.verdict == "WAIT"


def test_entry_timing_wide_spread_never_go() -> None:
    v = entry_timing(_setup(), _feats(50.0, 0.3))
    # wide spread hard block (-1.0) + strong imbalance (+1.0) = 0.0 -> CAUTION.
    assert v.verdict == "CAUTION"


def test_entry_timing_ask_wall_overhead_never_go() -> None:
    wall = Wall(side="ask", price=100.1, size=99.0, dist_bps=10.0, z=5.0)  # 10 bps over entry
    v = entry_timing(_setup(), _feats(5.0, 0.3), walls=[wall])
    assert v.verdict != "GO"  # overhead wall is a hard block


def test_entry_timing_bid_wall_support_promotes_to_go() -> None:
    wall = Wall(side="bid", price=99.8, size=99.0, dist_bps=20.0, z=5.0)  # 20 bps below entry
    v = entry_timing(_setup(), _feats(5.0, 0.1), walls=[wall])
    # mild support 0.75 + bid wall support 0.5 = 1.25 -> GO.
    assert v.verdict == "GO"


def test_entry_timing_flow_delta_shifts_verdict() -> None:
    improving = entry_timing(_setup(), _feats(5.0, 0.1), flow_delta=0.5)
    deteriorating = entry_timing(_setup(), _feats(5.0, 0.1), flow_delta=-0.5)
    assert improving.verdict == "GO"  # 0.75 + 0.5 = 1.25
    assert deteriorating.verdict == "CAUTION"  # 0.75 - 0.5 = 0.25


def test_entry_timing_is_deterministic() -> None:
    a = entry_timing(_setup(), _feats(5.0, 0.3, microprice=100.05))
    b = entry_timing(_setup(), _feats(5.0, 0.3, microprice=100.05))
    assert a.model_dump() == b.model_dump()
