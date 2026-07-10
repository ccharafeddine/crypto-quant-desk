"""Return/risk metrics — ported from the Portfolio Analyzer (transforms.py)
and adapted for crypto.

Two crypto adaptations vs the equity original:
  1. PERIODS_PER_YEAR = 365, not 252. Crypto trades every calendar day.
  2. An EWMA vol option (RiskMetrics lambda=0.94) alongside simple rolling,
     because crypto volatility clusters hard and the most recent regime
     should dominate a "current vol" reading.

Everything else is the same math as the tested equity code.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PERIODS_PER_YEAR = 365  # crypto: 24/7/365, not 252 equity trading days


def annualize_return(returns: pd.Series, periods_per_year: int = PERIODS_PER_YEAR) -> float:
    """Geometric annualized return from periodic simple returns."""
    r = returns.dropna()
    if r.empty:
        return np.nan
    gross = (1.0 + r).prod()
    n = len(r)
    if n == 0 or gross <= 0:
        return np.nan
    return float(gross ** (periods_per_year / n) - 1.0)


def annualize_vol(returns: pd.Series, periods_per_year: int = PERIODS_PER_YEAR) -> float:
    """Annualized volatility from periodic returns (simple rolling std)."""
    r = returns.dropna()
    if r.empty:
        return np.nan
    return float(r.std(ddof=1) * np.sqrt(periods_per_year))


def ewma_vol(
    returns: pd.Series,
    lam: float = 0.94,
    periods_per_year: int = PERIODS_PER_YEAR,
) -> float:
    """Annualized EWMA volatility (RiskMetrics). Weights recent observations
    more heavily, which suits crypto's volatility clustering better than a
    flat rolling window. lam=0.94 is the standard daily decay.
    """
    r = returns.dropna().values
    if len(r) < 2:
        return np.nan
    # Recursive EWMA of squared returns, seeded with sample variance.
    var = float(np.var(r, ddof=1))
    for x in r:
        var = lam * var + (1.0 - lam) * x * x
    return float(np.sqrt(var) * np.sqrt(periods_per_year))


def sharpe_ratio(
    returns: pd.Series,
    rf_annual: float = 0.0,
    periods_per_year: int = PERIODS_PER_YEAR,
) -> float:
    """Annualized Sharpe ratio."""
    r = returns.dropna()
    if r.empty:
        return np.nan
    rf_per = rf_annual / periods_per_year
    excess = r - rf_per
    vol = excess.std(ddof=1)
    if vol == 0 or np.isnan(vol):
        return np.nan
    return float(np.sqrt(periods_per_year) * excess.mean() / vol)


def max_drawdown(values: pd.Series) -> float:
    """Maximum drawdown from a value series. Returns a negative decimal."""
    v = values.dropna().astype(float).sort_index()
    if v.empty:
        return np.nan
    running_max = v.cummax()
    dd = v / running_max - 1.0
    return float(dd.min())


def drawdown_series(values: pd.Series) -> pd.Series:
    """Full drawdown time series from a value series."""
    v = values.dropna().astype(float).sort_index()
    if v.empty:
        return pd.Series(dtype=float)
    running_max = v.cummax()
    return v / running_max - 1.0


def var_cvar(returns: pd.Series, alpha: float = 0.95) -> tuple[float, float]:
    """Historical VaR and Conditional VaR (expected shortfall) at `alpha`.
    Both returned as negative numbers representing losses.
    """
    r = returns.dropna().astype(float)
    if r.empty:
        return np.nan, np.nan
    p = 1.0 - alpha
    var_val = float(r.quantile(p))
    tail = r[r <= var_val]
    cvar_val = float(tail.mean()) if not tail.empty else np.nan
    return var_val, cvar_val


def gain_to_pain(returns: pd.Series) -> float | None:
    """Gain-to-Pain: sum(gains) / |sum(losses)|."""
    r = returns.dropna()
    if r.empty:
        return None
    gains = r[r > 0].sum()
    losses = r[r < 0].sum()
    if losses >= 0:
        return None
    return float(gains / abs(losses))


def sortino_ratio(
    returns: pd.Series,
    rf_annual: float = 0.0,
    periods_per_year: int = PERIODS_PER_YEAR,
) -> float:
    """Annualized Sortino ratio: excess return over downside deviation only.

    Downside deviation is the RMS of the negative part of excess returns taken
    over ALL observations (target-semivariance with target = rf), which is the
    standard convention and never rewards an all-up sample with an infinite
    ratio the way dividing by an empty downside set would.
    """
    r = returns.dropna()
    if r.empty:
        return np.nan
    excess = r - rf_annual / periods_per_year
    downside = float(np.sqrt((excess.clip(upper=0.0) ** 2).mean()))
    if downside == 0 or np.isnan(downside):
        return np.nan
    return float(np.sqrt(periods_per_year) * excess.mean() / downside)


def calmar_ratio(returns: pd.Series, periods_per_year: int = PERIODS_PER_YEAR) -> float:
    """Annualized return divided by |max drawdown| of the compounded curve."""
    r = returns.dropna()
    if r.empty:
        return np.nan
    equity = (1.0 + r).cumprod()
    mdd = max_drawdown(equity)
    if mdd == 0 or np.isnan(mdd):
        return np.nan
    return float(annualize_return(r, periods_per_year) / abs(mdd))


def rolling_vol(
    returns: pd.Series, window: int = 30, periods_per_year: int = PERIODS_PER_YEAR
) -> pd.Series:
    """Annualized rolling volatility (windowed std), as a series."""
    return returns.rolling(window).std(ddof=1) * np.sqrt(periods_per_year)


def rolling_sharpe(
    returns: pd.Series,
    window: int = 30,
    rf_annual: float = 0.0,
    periods_per_year: int = PERIODS_PER_YEAR,
) -> pd.Series:
    """Annualized rolling Sharpe ratio, as a series."""
    excess = returns - rf_annual / periods_per_year
    mean = excess.rolling(window).mean()
    std = excess.rolling(window).std(ddof=1)
    return np.sqrt(periods_per_year) * mean / std


def ratio_summary(returns: pd.Series, rf_annual: float = 0.0) -> dict[str, float]:
    """All headline risk/return ratios from a periodic return series.

    A single dict so the analytics panel stays a thin view over tested math.
    """
    equity = (1.0 + returns.dropna()).cumprod()
    var95, cvar95 = var_cvar(returns, 0.95)
    return {
        "ann_return": annualize_return(returns),
        "ann_vol": annualize_vol(returns),
        "ewma_vol": ewma_vol(returns),
        "sharpe": sharpe_ratio(returns, rf_annual),
        "sortino": sortino_ratio(returns, rf_annual),
        "calmar": calmar_ratio(returns),
        "max_drawdown": max_drawdown(equity),
        "var_95": var95,
        "cvar_95": cvar95,
        "gain_to_pain": gain_to_pain(returns),
    }
