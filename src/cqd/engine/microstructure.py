"""Order-flow (L2) engine: execution timing, NEVER direction.

Pure and deterministic (numpy/pydantic only): every function receives a depth
snapshot (`{"bids": [(px, sz)...], "asks": [(px, sz)...]}`, best-first, as
`normalize_depth` produces) and returns values. No I/O, no Qt.

This layer's honest, high-value use is: time the entry on a setup the TREND
engine already chose, estimate slippage for the intended size, and flag
deteriorating liquidity. It does NOT emit direction - order-book imbalance is a
noisy, seconds-horizon, easily-spoofed quantity, and treating it as multi-hour
alpha invites overfitting (SIGNALS_PLAN.md §0/§2). `entry_timing` is therefore
CONSERVATIVE by default: an unknown, thin, or one-sided book yields `WAIT`,
never `GO`.

Guards throughout: empty / one-sided / NaN books return `None` fields (or empty
lists), never raise.
"""

from __future__ import annotations

import math
from typing import Literal

import numpy as np
from pydantic import BaseModel

from cqd.engine.signals import TradeSetup

Depth = dict[str, list[tuple[float, float]]]

# --- entry_timing thresholds (documented; the truth table pins them) ---
SPREAD_WIDE_BPS = 20.0  # spread wider than this is a hard block (never GO)
IMBALANCE_STRONG = 0.20  # depth imbalance at/above this is "strong" support
GO_SCORE = 1.0  # score at/above this (no hard block) -> GO
CAUTION_SCORE = 0.0  # score at/above this -> CAUTION, else WAIT
SUPPORT_BPS = 30.0  # a same-side wall within this distance counts as support
OVERHEAD_BPS = 15.0  # an opposing wall within this distance is a hard block


class BookFeatures(BaseModel):
    """Summary of a depth snapshot. All fields `None` for an empty, one-sided,
    or non-finite book (mid needs both sides). `depth_bid_bps`/`depth_ask_bps`
    are cumulative resting size within `depth_window_bps` of mid."""

    mid: float | None = None
    microprice: float | None = None
    spread_abs: float | None = None
    spread_bps: float | None = None
    imbalance_l5: float | None = None
    imbalance_l10: float | None = None
    imbalance_l25: float | None = None
    depth_bid_bps: float | None = None
    depth_ask_bps: float | None = None


class Wall(BaseModel):
    """A resting-size outlier level: `size` is a `z`-score outlier vs its side's
    level-size distribution, `dist_bps` its distance from mid."""

    side: Literal["bid", "ask"]
    price: float
    size: float
    dist_bps: float
    z: float


class FillEstimate(BaseModel):
    """Result of walking the book for `notional` quote. `vwap`/`slippage_bps`
    are `None` when nothing could be filled; `partial` is `True` when the book
    was too thin to fill the whole notional. `slippage_bps` is signed as a COST
    (positive = worse than mid)."""

    side: Literal["buy", "sell"]
    notional: float
    vwap: float | None = None
    slippage_bps: float | None = None
    levels_consumed: int = 0
    sweeps_wall: bool = False
    partial: bool = False


class TimingVerdict(BaseModel):
    """Entry-timing verdict for a setup given the current book. `score` is the
    additive rule score; `reasons` explain it. `GO`/`WAIT`/`CAUTION` only ever
    gate WHEN to act, never the direction."""

    verdict: Literal["GO", "WAIT", "CAUTION"]
    reasons: list[str]
    score: float


def _finite_pos(*values: float) -> bool:
    return all(math.isfinite(v) and v > 0 for v in values)


def _imbalance(
    bids: list[tuple[float, float]], asks: list[tuple[float, float]], n: int
) -> float | None:
    """Depth imbalance over the top `n` levels: `(Σbid - Σask)/(Σbid + Σask)`."""
    bid_sz = sum(float(sz) for _, sz in bids[:n])
    ask_sz = sum(float(sz) for _, sz in asks[:n])
    total = bid_sz + ask_sz
    if total <= 0:
        return None
    return (bid_sz - ask_sz) / total


def book_features(depth: Depth, depth_window_bps: float = 25.0) -> BookFeatures:
    """Mid, microprice, spread, multi-depth imbalance, and windowed cumulative
    depth. Returns all-`None` fields for an empty/one-sided/non-finite book.

    microprice = `(bid_px*ask_sz + ask_px*bid_sz)/(bid_sz+ask_sz)` (size-weighted
    toward the thinner side). Imbalance is reported at L5/L10/L25.
    """
    bids = depth.get("bids") or []
    asks = depth.get("asks") or []
    if not bids or not asks:
        return BookFeatures()

    bb_px, bb_sz = float(bids[0][0]), float(bids[0][1])
    ba_px, ba_sz = float(asks[0][0]), float(asks[0][1])
    if not _finite_pos(bb_px, ba_px) or ba_px < bb_px:
        return BookFeatures()

    mid = (bb_px + ba_px) / 2.0
    size_sum = bb_sz + ba_sz
    microprice = (bb_px * ba_sz + ba_px * bb_sz) / size_sum if size_sum > 0 else None
    spread_abs = ba_px - bb_px
    spread_bps = spread_abs / mid * 1e4 if mid > 0 else None

    window = depth_window_bps / 1e4
    bid_floor = mid * (1.0 - window)
    ask_ceil = mid * (1.0 + window)
    depth_bid_bps = sum(float(sz) for px, sz in bids if float(px) >= bid_floor)
    depth_ask_bps = sum(float(sz) for px, sz in asks if float(px) <= ask_ceil)

    return BookFeatures(
        mid=mid,
        microprice=microprice,
        spread_abs=spread_abs,
        spread_bps=spread_bps,
        imbalance_l5=_imbalance(bids, asks, 5),
        imbalance_l10=_imbalance(bids, asks, 10),
        imbalance_l25=_imbalance(bids, asks, 25),
        depth_bid_bps=depth_bid_bps,
        depth_ask_bps=depth_ask_bps,
    )


def _side_walls(
    levels: list[tuple[float, float]], side: Literal["bid", "ask"], mid: float, z_thresh: float
) -> list[Wall]:
    sizes = np.array([float(sz) for _, sz in levels], dtype=float)
    if sizes.size < 3 or not np.all(np.isfinite(sizes)):
        return []  # too few levels to define an outlier distribution
    mean = float(sizes.mean())
    std = float(sizes.std(ddof=0))
    if std <= 0:
        return []  # uniform side: nothing is an outlier
    out: list[Wall] = []
    for px, sz in levels:
        z = (float(sz) - mean) / std
        if z >= z_thresh:
            out.append(
                Wall(
                    side=side,
                    price=float(px),
                    size=float(sz),
                    dist_bps=abs(float(px) - mid) / mid * 1e4,
                    z=z,
                )
            )
    return out


def detect_walls(depth: Depth, z_thresh: float = 3.0) -> list[Wall]:
    """Levels whose resting size is a `z_thresh`-outlier vs their side's
    level-size distribution, closest-to-mid first. Needs both sides (for mid)
    and >= 3 levels per side; otherwise that side contributes no walls."""
    bids = depth.get("bids") or []
    asks = depth.get("asks") or []
    if not bids or not asks:
        return []
    bb_px, ba_px = float(bids[0][0]), float(asks[0][0])
    if not _finite_pos(bb_px, ba_px):
        return []
    mid = (bb_px + ba_px) / 2.0
    walls = _side_walls(bids, "bid", mid, z_thresh) + _side_walls(asks, "ask", mid, z_thresh)
    walls.sort(key=lambda w: w.dist_bps)
    return walls


def expected_fill(depth: Depth, side: Literal["buy", "sell"], notional: float) -> FillEstimate:
    """Walk the book for `notional` quote: VWAP fill, slippage vs mid, levels
    consumed, whether it sweeps a wall, and a `partial` flag if the book is too
    thin. A buy walks the asks, a sell walks the bids.
    """
    base = FillEstimate(side=side, notional=float(notional))
    bids = depth.get("bids") or []
    asks = depth.get("asks") or []
    mid = None
    if bids and asks and _finite_pos(float(bids[0][0]), float(asks[0][0])):
        mid = (float(bids[0][0]) + float(asks[0][0])) / 2.0
    levels = asks if side == "buy" else bids

    if notional <= 0:
        return base
    if not levels:
        return base.model_copy(update={"partial": True})

    remaining = float(notional)
    cost = 0.0
    qty = 0.0
    levels_consumed = 0
    consumed_prices: list[float] = []
    for px, sz in levels:
        px, sz = float(px), float(sz)
        if not _finite_pos(px) or sz <= 0:
            continue
        level_notional = px * sz
        levels_consumed += 1
        consumed_prices.append(px)
        if remaining <= level_notional:
            qty += remaining / px
            cost += remaining
            remaining = 0.0
            break
        qty += sz
        cost += level_notional
        remaining -= level_notional

    partial = remaining > 1e-9
    vwap = cost / qty if qty > 0 else None
    if vwap is None or mid is None or mid <= 0:
        slippage_bps = None
    else:
        raw = (vwap - mid) if side == "buy" else (mid - vwap)
        slippage_bps = raw / mid * 1e4

    sweeps_wall = False
    if consumed_prices:
        walls = detect_walls(depth)
        if side == "buy":
            edge = max(consumed_prices)
            sweeps_wall = any(w.side == "ask" and w.price <= edge for w in walls)
        else:
            edge = min(consumed_prices)
            sweeps_wall = any(w.side == "bid" and w.price >= edge for w in walls)

    return FillEstimate(
        side=side,
        notional=float(notional),
        vwap=vwap,
        slippage_bps=slippage_bps,
        levels_consumed=levels_consumed,
        sweeps_wall=sweeps_wall,
        partial=partial,
    )


def entry_timing(
    setup: TradeSetup | None,
    features: BookFeatures | None,
    walls: list[Wall] | None = None,
    flow_delta: float | None = None,
) -> TimingVerdict:
    """Gate WHEN to enter `setup` given the current book. Conservative: no setup,
    or an unknown/thin/one-sided book, returns `WAIT` (never `GO`).

    Hard blocks (a wide spread, adverse imbalance, or an opposing wall directly
    overhead) cap the verdict at `CAUTION`. Otherwise the additive score decides:
    `>= GO_SCORE` -> `GO`, `>= CAUTION_SCORE` -> `CAUTION`, else `WAIT`. Imbalance
    and wall polarity are read relative to the setup's direction, so the logic is
    correct for a future short variant without ever choosing the direction here.
    """
    walls = walls or []
    if setup is None:
        return TimingVerdict(verdict="WAIT", reasons=["no active setup"], score=0.0)
    if (
        features is None
        or features.mid is None
        or features.imbalance_l10 is None
        or features.spread_bps is None
    ):
        return TimingVerdict(verdict="WAIT", reasons=["book unknown / thin / one-sided"], score=0.0)

    favor = 1.0 if setup.direction == "long" else -1.0
    imb = features.imbalance_l10 * favor
    entry = setup.entry_ref
    reasons: list[str] = []
    score = 0.0
    hard_block = False

    if features.spread_bps > SPREAD_WIDE_BPS:
        hard_block = True
        score -= 1.0
        reasons.append(f"wide spread {features.spread_bps:.1f} bps")
    else:
        score += 0.25
        reasons.append(f"tight spread {features.spread_bps:.1f} bps")

    if imb >= IMBALANCE_STRONG:
        score += 1.0
        reasons.append("strong supportive imbalance")
    elif imb >= 0:
        score += 0.5
        reasons.append("supportive imbalance")
    else:
        hard_block = True
        score -= 1.0
        reasons.append("adverse imbalance")

    if features.microprice is not None and entry > 0:
        lean = (features.microprice - features.mid) * favor
        if lean > 0:
            score += 0.25
            reasons.append("microprice supportive")
        elif lean < 0:
            score -= 0.25
            reasons.append("microprice adverse")

    if entry > 0:
        for w in walls:
            rel_bps = (w.price - entry) / entry * 1e4  # + = above entry
            same_side = "bid" if setup.direction == "long" else "ask"
            opp_side = "ask" if setup.direction == "long" else "bid"
            supportive = rel_bps <= 0 if setup.direction == "long" else rel_bps >= 0
            if w.side == same_side and supportive and abs(rel_bps) <= SUPPORT_BPS:
                score += 0.5
                reasons.append(f"{w.side} wall support {abs(rel_bps):.0f} bps away")
            overhead = (
                0 < rel_bps <= OVERHEAD_BPS
                if setup.direction == "long"
                else (-OVERHEAD_BPS <= rel_bps < 0)
            )
            if w.side == opp_side and overhead:
                hard_block = True
                score -= 1.0
                reasons.append(f"{w.side} wall overhead {abs(rel_bps):.0f} bps away")

    if flow_delta is not None:
        adj = flow_delta * favor
        if adj > 0:
            score += 0.5
            reasons.append("order flow improving")
        elif adj < 0:
            score -= 0.5
            reasons.append("order flow deteriorating")

    if hard_block:
        verdict = "CAUTION" if score >= CAUTION_SCORE else "WAIT"
    elif score >= GO_SCORE:
        verdict = "GO"
    elif score >= CAUTION_SCORE:
        verdict = "CAUTION"
    else:
        verdict = "WAIT"

    return TimingVerdict(verdict=verdict, reasons=reasons, score=round(score, 6))


__all__ = [
    "BookFeatures",
    "FillEstimate",
    "TimingVerdict",
    "Wall",
    "book_features",
    "detect_walls",
    "entry_timing",
    "expected_fill",
]
