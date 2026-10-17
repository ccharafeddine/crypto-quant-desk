"""Return attribution against BTC buy-and-hold, a drawdown table, fee drag.

Pure pandas. Attribution is holdings-based on the CURRENT weights over a
window (a static-weight approximation, labelled as such in the UI), split
three ways so that the parts add up exactly to the active return:

    active        = sum_i w_i r_i  -  r_btc                (cash earns 0)
    allocation    = (w_crypto - 1) r_btc       crypto exposure vs fully invested
    btc_beta      = sum_i w_i (beta_i - 1) r_btc    more/less market sensitivity
    selection     = sum_i w_i (r_i - beta_i r_btc)  what the coins did beyond beta

Every term is in return units over the window.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

_EPS = 1e-12
CASH = frozenset({"USD", "ZUSD", "USDC", "USDT", "DAI", "PYUSD"})


@dataclass(frozen=True)
class Attribution:
    window_days: int
    portfolio: float
    btc: float
    active: float
    allocation: float
    btc_beta: float
    selection: float
    per_asset: dict[str, dict[str, float]] = field(default_factory=dict)


def beta_attribution(
    weights: pd.Series,
    returns: pd.DataFrame,
    betas: pd.Series,
    btc_col: str = "BTC",
    window: int = 365,
) -> Attribution | None:
    """Decompose the window's active return vs BTC (see module docstring)."""
    if btc_col not in returns.columns:
        return None
    frame = returns.tail(window)
    growth = (1.0 + frame.fillna(0.0)).prod() - 1.0
    r_b = float(growth[btc_col])
    port = alloc_w = beta_t = sel = 0.0
    per: dict[str, dict[str, float]] = {}
    for asset, w in weights.items():
        a, w = str(asset), float(w)
        if a in CASH or a not in growth.index:
            continue
        r_i = float(growth[a])
        beta = float(betas.get(a, math.nan))
        if not math.isfinite(beta):
            beta = 1.0
        alloc_w += w
        port += w * r_i
        beta_t += w * (beta - 1.0) * r_b
        sel_i = w * (r_i - beta * r_b)
        sel += sel_i
        per[a] = {"weight": w, "return": r_i, "beta": beta, "selection": sel_i}
    return Attribution(
        window_days=len(frame),
        portfolio=port,
        btc=r_b,
        active=port - r_b,
        allocation=(alloc_w - 1.0) * r_b,
        btc_beta=beta_t,
        selection=sel,
        per_asset=per,
    )


@dataclass(frozen=True)
class Drawdown:
    peak: pd.Timestamp
    trough: pd.Timestamp
    recovery: pd.Timestamp | None  # None: still under water
    depth: float
    days_to_trough: int
    days_to_recover: int | None
    underwater_days: int


def drawdown_table(equity: pd.Series, top: int = 5) -> list[Drawdown]:
    """The `top` deepest peak-to-trough episodes with their recovery times.

    An episode starts at a running high, bottoms at its trough and recovers on
    the first close back at or above that high; an open episode has
    recovery None. Sorted deepest first."""
    v = equity.dropna().astype(float).sort_index()
    if len(v) < 2:
        return []
    peak_val, peak_t = v.iloc[0], v.index[0]
    trough_val, trough_t = peak_val, peak_t
    episodes: list[Drawdown] = []
    in_dd = False
    for t, x in v.iloc[1:].items():
        if x >= peak_val - _EPS * abs(peak_val):
            if in_dd:
                episodes.append(_episode(peak_t, trough_t, t, trough_val / peak_val - 1.0))
                in_dd = False
            peak_val, peak_t = x, t
            trough_val, trough_t = x, t
            continue
        in_dd = True
        if x < trough_val:
            trough_val, trough_t = x, t
    if in_dd:
        episodes.append(_episode(peak_t, trough_t, None, trough_val / peak_val - 1.0, v.index[-1]))
    episodes.sort(key=lambda d: d.depth)
    return episodes[:top]


def _episode(peak, trough, recovery, depth, last=None) -> Drawdown:
    end = recovery if recovery is not None else last
    return Drawdown(
        peak=peak,
        trough=trough,
        recovery=recovery,
        depth=float(depth),
        days_to_trough=int((trough - peak).days),
        days_to_recover=int((recovery - trough).days) if recovery is not None else None,
        underwater_days=int((end - peak).days),
    )


@dataclass(frozen=True)
class FeeDrag:
    fees_usd: float
    avg_equity: float
    years: float
    annual_drag: float  # fees per year / average equity


def fee_drag(fees_usd: float, equity_usd: pd.Series) -> FeeDrag:
    """Trading fees as an annual percentage of average account value."""
    e = equity_usd.dropna()
    if len(e) < 2 or e.mean() <= 0:
        return FeeDrag(fees_usd, math.nan, 0.0, math.nan)
    years = max((e.index[-1] - e.index[0]).days, 1) / 365.0
    avg = float(e.mean())
    return FeeDrag(fees_usd, avg, years, fees_usd / years / avg)
