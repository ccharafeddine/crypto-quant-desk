"""Entry points for the in-browser demo (web/), run under Pyodide.

The page fetches Kraken *public* endpoints (OHLC, AssetPairs) in JavaScript and
hands the raw JSON here; everything returned is plain JSON for the page to
render. This module is pure: no network, no keys, no clock, and it imports
nothing from the trading package: the demo has no order path at all (a test
pins that). The same functions run under pytest, so the browser build is tested
in CI like the desktop app.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd

from cqd.data.crash_history import load_crash_windows
from cqd.data.normalize import Candle, normalize_ohlc_full
from cqd.engine import var as V
from cqd.engine.attribution import beta_attribution, drawdown_table
from cqd.engine.backtest import PropLimits, parameter_stability, replay, walk_forward
from cqd.engine.risk import compute_portfolio_risk
from cqd.engine.signals import PairSpec, StrategyParams, evaluate_setup
from cqd.engine.stress import (
    average_daily_volume_usd,
    block_bootstrap_fan,
    depeg_shock,
    downside_betas,
    liquidity_table,
    replay_all,
)

#: Demo universe: bare asset -> Kraken USD pair (altname). All ten have crash history.
PAIRS: dict[str, str] = {
    "BTC": "XBTUSD",
    "ETH": "ETHUSD",
    "SOL": "SOLUSD",
    "XRP": "XRPUSD",
    "ADA": "ADAUSD",
    "DOGE": "XDGUSD",
    "LINK": "LINKUSD",
    "DOT": "DOTUSD",
    "LTC": "LTCUSD",
    "AVAX": "AVAXUSD",
}
CASH = "USD"
DEFAULT_WEIGHTS: dict[str, float] = {
    "BTC": 0.40,
    "ETH": 0.25,
    "SOL": 0.15,
    "ADA": 0.05,
    "USD": 0.15,
}
DEFAULT_NAV = 25_000.0
RISK_FREE = 0.041  # same default as the desktop app (3-month T-bill)


def _num(x: Any, nd: int = 6) -> float | None:
    """JSON-safe float: NaN/inf become null so the page shows a dash, never 'NaN'."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return round(f, nd) if math.isfinite(f) else None


def candles_from(raw: dict[str, Any]) -> list[Candle]:
    """Kraken OHLC `result` -> closed candles (the forming bar is dropped)."""
    return normalize_ohlc_full(raw, closed_only=True)


def returns_frame(candles: dict[str, list[Candle]]) -> pd.DataFrame:
    cols = {}
    for asset, cs in candles.items():
        if len(cs) > 1:
            idx = pd.to_datetime([c.time for c in cs], unit="s")
            cols[asset] = pd.Series([c.close for c in cs], index=idx).pct_change()
    return pd.DataFrame(cols).iloc[1:] if cols else pd.DataFrame()


def clean_weights(weights: dict[str, float]) -> pd.Series:
    """Known assets only, negatives dropped, normalised to 1 (cash keeps its share)."""
    w = {a: float(v) for a, v in weights.items() if (a in PAIRS or a == CASH) and float(v) > 0}
    total = sum(w.values())
    if total <= 0:
        raise ValueError("weights must include at least one positive holding")
    return pd.Series({a: v / total for a, v in w.items()})


def analyze_portfolio(
    ohlc: dict[str, dict[str, Any]], weights: dict[str, float], nav: float = DEFAULT_NAV
) -> dict[str, Any]:
    """Risk page + Risk lab for a model book, from raw OHLC results per asset."""
    w = clean_weights(weights)
    candles = {a: candles_from(ohlc[a]) for a in w.index if a != CASH and a in ohlc}
    if "BTC" not in candles and "BTC" in ohlc:
        candles["BTC"] = candles_from(ohlc["BTC"])  # benchmark, even if not held
    rets = returns_frame(candles)
    if rets.empty or "BTC" not in rets:
        raise ValueError("need BTC daily history")
    if CASH in w.index:
        # Same convention as the desktop returns frame: cash is an all-zero
        # column, so it dilutes vol and beta and counts in concentration.
        rets[CASH] = 0.0
    priced = [a for a in w.index if a in rets.columns]
    missing = [a for a in w.index if a not in rets.columns]

    risk = compute_portfolio_risk(w.loc[priced], rets, rf_annual=RISK_FREE)
    port = V.portfolio_returns(w, rets)
    suite = V.var_suite(port)
    backtests = [V.backtest_var(port, m, alpha=0.99, window=250) for m in V.METHODS]
    betas = risk.per_asset_beta
    replays = replay_all(w, load_crash_windows(), betas)
    fan = block_bootstrap_fan(port, horizon=30)
    down = downside_betas(rets)
    adv = {
        a: average_daily_volume_usd(
            pd.Series([c.volume for c in cs]), pd.Series([c.close for c in cs])
        )
        for a, cs in candles.items()
    }
    liq = liquidity_table({a: float(x) * nav for a, x in w.items() if a != CASH}, adv)
    att = beta_attribution(w, rets, betas)

    # Backcast of today's weights (ex-ante; not a track record) against BTC.
    index = (1.0 + port).cumprod()
    btc_index = (1.0 + rets["BTC"].loc[index.index].fillna(0.0)).cumprod()
    drawdowns = drawdown_table(index)

    marks = {a: cs[-1].close for a, cs in candles.items() if cs}
    last_ts = max(cs[-1].time for cs in candles.values() if cs)
    return {
        "asof": int(last_ts),
        "n_days": int(len(port)),
        "nav": _num(nav, 2),
        "weights": {a: _num(x) for a, x in w.items()},
        "missing": missing,
        "marks": {a: _num(x, 8) for a, x in marks.items()},
        "risk": {
            "ann_vol": _num(risk.ann_vol),
            "ewma_vol": _num(risk.ewma_vol),
            "beta": _num(risk.book_beta_btc),
            "hhi": _num(risk.hhi),
            "effective_bets": _num(risk.effective_bets),
            "top3": _num(risk.top3_concentration),
            "rows": [
                {
                    "asset": a,
                    "weight": _num(w.get(a, 0.0)),
                    "beta": _num(betas.get(a)),
                    "down_beta": _num(down.get(a)),
                    # the engine reports percent points; the page wants fractions
                    "risk_pct": _num(risk.risk_contribution.get(a, math.nan) / 100.0),
                }
                for a in w.index
            ],
        },
        "var": {
            "methods": [
                {
                    "key": m,
                    "label": V.METHOD_LABELS[m],
                    "var95": _num(suite.var95[m]),
                    "var99": _num(suite.var99[m]),
                }
                for m in V.METHODS
            ],
            "es975": {
                "historical": _num(suite.es975_hist),
                "fhs": _num(suite.es975_fhs),
                "normal": _num(suite.es975_normal),
            },
            "n_obs": suite.n_obs,
        },
        "backtests": [
            {
                "label": V.METHOD_LABELS[b.method],
                "n": b.n,
                "exceptions": b.exceptions,
                "expected": _num(b.expected, 2),
                "kupiec_p": _num(b.kupiec_p, 4),
                "ind_p": _num(b.ind_p, 4),
                "verdict": b.verdict(),
            }
            for b in backtests
        ],
        "replays": [
            {
                "label": r.label,
                "start": r.start,
                "end": r.end,
                "btc": _num(r.btc_return),
                "portfolio": _num(r.portfolio_return),
                "usd": _num(r.portfolio_return * nav, 2),
                "proxied": list(r.proxied),
            }
            for r in replays
        ],
        "depeg": _num(sum(depeg_shock(w).values())),
        "fan": {
            "quantiles": list(fan.quantiles),
            "bands": [[_num(v) for v in fan.bands[q]] for q in fan.quantiles],
            "var95_horizon": _num(fan.var95_horizon),
            "horizon": fan.horizon,
        },
        "liquidity": [
            {
                "asset": r.asset,
                "value": _num(r.value_usd, 2),
                "adv": _num(r.adv_usd, 0),
                "pct_adv": _num(r.pct_of_adv, 8),
                "days": _num(r.days_to_liquidate, 6),
            }
            for r in liq
        ],
        "attribution": None
        if att is None
        else {
            "days": att.window_days,
            "portfolio": _num(att.portfolio),
            "btc": _num(att.btc),
            "active": _num(att.active),
            "allocation": _num(att.allocation),
            "btc_beta": _num(att.btc_beta),
            "selection": _num(att.selection),
        },
        "backcast": {
            "t": [int(ts.timestamp()) for ts in index.index],
            "portfolio": [_num(v) for v in index.to_numpy()],
            "btc": [_num(v) for v in btc_index.to_numpy()],
        },
        "drawdowns": [
            {
                "depth": _num(d.depth),
                "peak": str(d.peak)[:10],
                "trough": str(d.trough)[:10],
                "recovered": None if d.recovery is None else str(d.recovery)[:10],
            }
            for d in drawdowns
        ],
    }


def pair_spec_from(asset_pairs: dict[str, Any], symbol: str = "BTC/USD") -> PairSpec:
    """Kraken AssetPairs `result` (one pair) -> the engine's precision spec."""
    info = next(iter(asset_pairs.values()))
    return PairSpec(
        symbol=symbol,
        price_decimals=int(info["pair_decimals"]),
        lot_decimals=int(info["lot_decimals"]),
        ordermin=float(info.get("ordermin", 0.0)),
    )


def analyze_strategy(
    btc_ohlc: dict[str, Any], asset_pairs: dict[str, Any], equity: float = 10_000.0
) -> dict[str, Any]:
    """Signals page for BTC/USD: today's setup (advisory), the costed backtest with
    its frictionless twin and walk-forward, and the parameter-stability grid."""
    candles = candles_from(btc_ohlc)
    params = StrategyParams()
    spec = pair_spec_from(asset_pairs)
    setup = evaluate_setup(candles, params, equity, spec)
    limits = PropLimits(starting_equity=equity)
    stats = replay(candles, params, limits)
    free = replay(candles, params, limits.model_copy(update={"fee_bps": 0.0, "slippage_bps": 0.0}))
    wf = walk_forward(candles, params, limits)
    grid = parameter_stability(candles, params, limits)
    return {
        "bars": len(candles),
        "params": params.model_dump(),
        "setup": None if setup is None else json.loads(setup.model_dump_json()),
        "backtest": {
            "trades": stats.trades,
            "win_rate": _num(stats.win_rate),
            "expectancy_r": _num(stats.expectancy_r),
            "expectancy_r_ci": None
            if stats.expectancy_r_ci is None
            else [_num(x) for x in stats.expectancy_r_ci],
            "frictionless_expectancy_r": _num(free.expectancy_r),
            "profit_factor": _num(stats.profit_factor),
            "max_drawdown": _num(stats.max_drawdown),
            "total_return": _num(stats.total_return),
            "buy_hold_return": _num(stats.buy_hold_return),
            "costs_paid": _num(stats.costs_paid, 2),
            "outcome": stats.outcome,
            "fee_bps": stats.fee_bps,
            "slippage_bps": stats.slippage_bps,
            "curve_t": stats.curve_times,
            "curve": [_num(v, 2) for v in stats.equity_curve],
            "frictionless_curve": [_num(v, 2) for v in free.equity_curve],
            "oos_trades": wf.oos_trades,
            "oos_expectancy_r": _num(wf.oos_expectancy_r),
            "oos_folds": len(wf.folds),
        },
        "stability": {
            "fasts": grid.fasts,
            "slows": grid.slows,
            "cells": [[_num(c, 4) for c in row] for row in grid.cells],
            "chosen": list(grid.chosen),
            "positive_share": _num(grid.positive_share),
        },
    }


# ---- JSON-string wrappers: the narrowest possible bridge to JavaScript ----


def analyze_portfolio_json(ohlc_json: str, weights_json: str, nav: float) -> str:
    return json.dumps(
        analyze_portfolio(json.loads(ohlc_json), json.loads(weights_json), nav), allow_nan=False
    )


def analyze_strategy_json(btc_json: str, pairs_json: str, equity: float = 10_000.0) -> str:
    return json.dumps(
        analyze_strategy(json.loads(btc_json), json.loads(pairs_json), equity), allow_nan=False
    )


__all__ = [
    "CASH",
    "DEFAULT_NAV",
    "DEFAULT_WEIGHTS",
    "PAIRS",
    "analyze_portfolio",
    "analyze_portfolio_json",
    "analyze_strategy",
    "analyze_strategy_json",
    "returns_frame",
]
