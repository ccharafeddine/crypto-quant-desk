"""Signal engine: trend regime + sized, stopped trade setups.

Pure and deterministic (numpy/pandas/pydantic only): every function receives a
candle snapshot and returns values, so it is unit-testable against fixed
vectors. There is NO path from anything here to an order - the app proposes a
`TradeSetup`; the user still places every order through the existing ticket
(confirmation + limits + paper mode). See SIGNALS_PLAN.md §0.

v1 is LONG-ONLY. Direction comes from the trend engine (`trend_state`); the
order-flow layer (`microstructure.py`) only times the entry, never flips it.

House rules encoded here:
  - Look-ahead guard: breakouts use the SHIFTED Donchian channel (`indicators`).
  - Sizing: distance-to-stop risk = `risk_pct * equity`; size is FLOORED to the
    pair's lot precision (so realized risk never exceeds the budget), FAILS
    CLOSED below the pair minimum (returns `None`, never bumps size up past the
    risk budget), and respects a 5:1 leverage cap.
  - Confidence is derived from TREND strength ONLY (distance above the trend MA
    and its slope), never from order-book data - L2 is execution, not alpha.
  - `daily_room` / `total_room` are advisory distances to the prop limits,
    computed from equity inputs PASSED IN (never fetched); the panel decides
    whether to warn or suppress, the engine just reports the numbers.
"""

from __future__ import annotations

import math
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field, model_validator

from cqd.data.normalize import Candle
from cqd.engine.indicators import atr, donchian, sma

# --- strategy-level constants (not pair-specific) ---
MAX_LEVERAGE = 5.0  # BTC/ETH spot cap; notional never exceeds 5x equity
DAILY_LIMIT = 0.03  # prop firm daily loss limit (3% of start-of-day equity)
TOTAL_LIMIT = 0.05  # prop firm total loss limit (5% of starting equity)
TARGET_MULTIPLES: tuple[float, ...] = (1.0, 2.0, 3.0)  # take-profits at 1R/2R/3R

# --- confidence model (trend strength only; see _confidence) ---
_W_DIST = 0.6  # weight on distance above the trend MA
_W_SLOPE = 0.4  # weight on trend MA slope
_DIST_SCALE = 0.10  # 10% above the trend MA saturates the distance term
_SLOPE_SCALE = 0.05  # 5% MA rise over the lookback saturates the slope term
_SLOPE_LOOKBACK = 20  # bars used to measure trend MA slope


class TrendState(str, Enum):
    """Long-only regime machine. `FLAT` = no long regime (price <= trend MA);
    `LONG_ARMED` = long regime on but the entry trigger has not fired;
    `LONG_ACTIVE` = trigger fired (MA cross up, or breakout of the channel)."""

    FLAT = "flat"
    LONG_ARMED = "long_armed"
    LONG_ACTIVE = "long_active"


class StrategyParams(BaseModel):
    """Tunable strategy parameters. Defaults are STRATEGY.md v1.

    For the `breakout` variant, `fast` doubles as the Donchian channel length
    (the classic 20-bar breakout), so no separate channel param is needed.
    """

    fast: int = Field(20, gt=0)
    slow: int = Field(50, gt=0)
    trend: int = Field(200, gt=0)
    atr_len: int = Field(14, gt=0)
    atr_mult: float = Field(2.0, gt=0)
    risk_pct: float = Field(0.005, gt=0, lt=1)
    variant: Literal["ma_cross", "breakout"] = "ma_cross"

    @model_validator(mode="after")
    def _ordered_windows(self) -> StrategyParams:
        if not (self.fast < self.slow < self.trend):
            raise ValueError("windows must satisfy fast < slow < trend")
        return self


class TradeSetup(BaseModel):
    """A concrete, sized long setup for one pair at one bar.

    `entry_ref` is the reference entry price (last close for `ma_cross`, the
    breakout level for `breakout`); `stop = entry_ref - atr_mult*ATR`.
    `size_base`/`size_quote` are the sized position; `risk_quote` is the REALIZED
    per-trade risk (<= `risk_pct*equity` by construction). `targets` are the
    1R/2R/3R take-profits and `rr` is the R-multiple of the furthest target.
    `confidence` in [0,1] is trend-strength only. `created_ts` is the last bar's
    open time (deterministic; the engine never reads the wall clock).
    `daily_room`/`total_room` are advisory prop-limit distances (or `None` when
    the equity inputs were not supplied).
    """

    symbol: str
    direction: Literal["long", "short"] = "long"
    state: TrendState
    entry_ref: float
    stop: float
    size_base: float
    size_quote: float
    risk_quote: float
    targets: list[float]
    rr: float
    confidence: float
    rationale: str
    created_ts: int
    daily_room: float | None = None
    total_room: float | None = None


class PairSpec(BaseModel):
    """Minimal precision spec the engine needs to size and round a setup.

    Deliberately engine-local (the purity rule limits the engine to
    numpy/pandas/pydantic - it must not import from `trading/`). The service
    layer (S3) builds this from Kraken's AssetPairs, the same source that feeds
    `trading.limits.PairSpec`; the two mirror each other by field but are not
    coupled.
    """

    symbol: str  # display/slash form, e.g. "BTC/USD"
    price_decimals: int = Field(ge=0)
    lot_decimals: int = Field(ge=0)
    ordermin: float = Field(0.0, ge=0)


def _closes(candles: list[Candle]) -> pd.Series:
    return pd.Series([c.close for c in candles], dtype=float)


def _round_to(value: float, decimals: int) -> float:
    """Round half-up to `decimals` places (prices, targets)."""
    quant = Decimal(1).scaleb(-decimals)
    return float(Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP))


def _floor_to(value: float, decimals: int) -> float:
    """Floor to `decimals` places (position size, so risk stays under budget)."""
    quant = Decimal(1).scaleb(-decimals)
    return float(Decimal(str(value)).quantize(quant, rounding=ROUND_DOWN))


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


def trend_state(candles: list[Candle], params: StrategyParams) -> TrendState:
    """Long-only trend regime for the latest bar.

    `FLAT` unless there is enough history for the trend MA AND price is above
    it. Above the trend MA, the trigger decides ARMED vs ACTIVE:
      - `ma_cross`: ACTIVE when the fast MA is above the slow MA, else ARMED.
      - `breakout`: ACTIVE when the last close is above the prior Donchian upper
        (the shifted channel), else ARMED.
    Insufficient history or any `NaN` collapses to `FLAT` (conservative).
    """
    if len(candles) < params.trend:
        return TrendState.FLAT
    closes = _closes(candles)
    price = float(closes.iloc[-1])
    trend_ma = float(sma(closes, params.trend).iloc[-1])
    if not (math.isfinite(price) and math.isfinite(trend_ma)) or price <= trend_ma:
        return TrendState.FLAT

    if params.variant == "breakout":
        upper = float(donchian(candles, params.fast).upper.iloc[-1])
        if not math.isfinite(upper):
            return TrendState.LONG_ARMED
        return TrendState.LONG_ACTIVE if price > upper else TrendState.LONG_ARMED

    fast_ma = float(sma(closes, params.fast).iloc[-1])
    slow_ma = float(sma(closes, params.slow).iloc[-1])
    if not (math.isfinite(fast_ma) and math.isfinite(slow_ma)):
        return TrendState.FLAT
    return TrendState.LONG_ACTIVE if fast_ma > slow_ma else TrendState.LONG_ARMED


def _confidence(closes: pd.Series, trend_ma_now: float, params: StrategyParams) -> float:
    """Trend-strength confidence in [0,1]: distance above the trend MA blended
    with the trend MA's slope. No order-book input, by design."""
    price = float(closes.iloc[-1])
    dist = (price - trend_ma_now) / trend_ma_now if trend_ma_now > 0 else 0.0
    ma = sma(closes, params.trend)
    slope = 0.0
    if len(ma) > _SLOPE_LOOKBACK:
        prev = float(ma.iloc[-1 - _SLOPE_LOOKBACK])
        if math.isfinite(prev) and prev > 0:
            slope = (float(ma.iloc[-1]) - prev) / prev
    conf = _W_DIST * _clip01(dist / _DIST_SCALE) + _W_SLOPE * _clip01(slope / _SLOPE_SCALE)
    return round(_clip01(conf), 6)


def evaluate_setup(
    candles: list[Candle],
    params: StrategyParams,
    equity_quote: float,
    pair_spec: PairSpec,
    *,
    start_of_day_equity: float | None = None,
    starting_equity: float | None = None,
) -> TradeSetup | None:
    """Build a sized long `TradeSetup` for the latest bar, or `None`.

    Returns `None` when the regime is `FLAT`, when ATR/entry are non-finite or
    non-positive, when the risk budget cannot be met, or when the risk-based
    size floors below the pair minimum (FAIL CLOSED - the engine never rounds a
    sub-minimum size UP past the risk budget). Otherwise:

      entry_ref  = last close (`ma_cross`) or prior Donchian upper (`breakout`)
      stop       = entry_ref - atr_mult * ATR          (rounded to price prec.)
      size_base  = floor( min(risk_size, leverage_cap_size), lot_prec )
      risk_quote = size_base * (entry_ref - stop)      (<= risk_pct * equity)

    `start_of_day_equity` / `starting_equity`, when supplied, set the advisory
    `daily_room` / `total_room` distances to the 3% / 5% prop limits.
    """
    state = trend_state(candles, params)
    if state == TrendState.FLAT:
        return None
    if equity_quote <= 0:
        return None

    closes = _closes(candles)
    price = float(closes.iloc[-1])
    atr_val = float(atr(candles, params.atr_len).iloc[-1])
    trend_ma = float(sma(closes, params.trend).iloc[-1])
    if not (math.isfinite(atr_val) and math.isfinite(price)) or atr_val <= 0:
        return None

    if params.variant == "breakout":
        entry_ref = float(donchian(candles, params.fast).upper.iloc[-1])
        if not math.isfinite(entry_ref) or entry_ref <= 0:
            return None
    else:
        entry_ref = price

    pd_dec, lot_dec = pair_spec.price_decimals, pair_spec.lot_decimals
    entry_ref = _round_to(entry_ref, pd_dec)
    stop = _round_to(entry_ref - params.atr_mult * atr_val, pd_dec)
    risk_per_unit = entry_ref - stop
    if risk_per_unit <= 0 or entry_ref <= 0:
        return None

    risk_target = params.risk_pct * equity_quote
    size_risk = risk_target / risk_per_unit
    size_cap = (MAX_LEVERAGE * equity_quote) / entry_ref  # 5:1 leverage ceiling
    size_base = _floor_to(min(size_risk, size_cap), lot_dec)
    if size_base <= 0 or (pair_spec.ordermin and size_base < pair_spec.ordermin):
        return None  # fail closed: cannot size within the risk budget above min

    size_quote = size_base * entry_ref
    risk_quote = size_base * risk_per_unit
    targets = [_round_to(entry_ref + m * risk_per_unit, pd_dec) for m in TARGET_MULTIPLES]
    confidence = _confidence(closes, trend_ma, params)

    daily_room = (
        None
        if start_of_day_equity is None
        else equity_quote - start_of_day_equity * (1.0 - DAILY_LIMIT)
    )
    total_room = (
        None if starting_equity is None else equity_quote - starting_equity * (1.0 - TOTAL_LIMIT)
    )

    rationale = (
        f"{params.variant} long, state={state.value}; entry {entry_ref:g}, "
        f"stop {stop:g} ({params.atr_mult:g}x ATR {atr_val:g}); "
        f"risk {risk_quote:g} quote ({params.risk_pct:.2%} of equity)."
    )

    return TradeSetup(
        symbol=pair_spec.symbol,
        direction="long",
        state=state,
        entry_ref=entry_ref,
        stop=stop,
        size_base=size_base,
        size_quote=size_quote,
        risk_quote=risk_quote,
        targets=targets,
        rr=TARGET_MULTIPLES[-1],
        confidence=confidence,
        rationale=rationale,
        created_ts=int(candles[-1].time),
        daily_room=daily_room,
        total_room=total_room,
    )


__all__ = [
    "DAILY_LIMIT",
    "MAX_LEVERAGE",
    "TARGET_MULTIPLES",
    "TOTAL_LIMIT",
    "PairSpec",
    "StrategyParams",
    "TradeSetup",
    "TrendState",
    "evaluate_setup",
    "trend_state",
]
