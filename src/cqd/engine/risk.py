"""Portfolio risk decomposition — ported from the Portfolio Analyzer
(analytics/risk.py) and adapted for crypto.

This is the SHARED SPINE. The Risk panel surfaces these numbers; the prop
engine's risk manager imports the same functions to enforce limits. Build
once, use twice.

Adaptations vs the equity original:
  - Benchmark is BTC, not SPY: `beta_to_btc` replaces `rolling_beta` against
    a generic market, and `portfolio_beta` aggregates to a book-level BTC beta.
  - Annualization uses 365 (see metrics.PERIODS_PER_YEAR).
Concentration math (HHI, effective bets, MCR) is benchmark-agnostic and
ports verbatim — it's the same code your TestConcentration suite covers.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from cqd.engine import metrics as M


# ──────────────────────────────────────────────────────────────
# Concentration (verbatim from equity risk.py — already tested there)
# ──────────────────────────────────────────────────────────────


def herfindahl_index(weights: np.ndarray | pd.Series) -> float:
    """HHI: sum of squared weights. 1/N (diversified) → 1.0 (single asset)."""
    w = weights.values if isinstance(weights, pd.Series) else np.asarray(weights)
    return float(np.sum(w ** 2))


def effective_n_bets(weights: np.ndarray | pd.Series) -> float:
    """Effective number of independent bets = 1 / HHI."""
    hhi = herfindahl_index(weights)
    if hhi < 1e-12:
        return 0.0
    return 1.0 / hhi


def concentration_ratio(weights: np.ndarray | pd.Series, top_n: int = 3) -> float:
    """Sum of the top-N absolute weights."""
    w = weights.values if isinstance(weights, pd.Series) else np.asarray(weights)
    sorted_abs = np.sort(np.abs(w))[::-1]
    return float(sorted_abs[:top_n].sum())


# ──────────────────────────────────────────────────────────────
# Risk contribution (verbatim — covariance-based, no return forecast)
# ──────────────────────────────────────────────────────────────


def marginal_risk_contribution(
    weights: np.ndarray | pd.Series,
    cov: np.ndarray | pd.DataFrame,
) -> pd.Series:
    """Marginal contribution to risk per asset. Sums to total portfolio vol."""
    if isinstance(weights, pd.Series):
        names = weights.index.tolist()
        w = weights.values
    else:
        w = np.asarray(weights)
        names = [f"Asset_{i}" for i in range(len(w))]

    C = cov.values if isinstance(cov, pd.DataFrame) else np.asarray(cov)

    port_vol = float(np.sqrt(w @ C @ w))
    if port_vol < 1e-12:
        return pd.Series(0.0, index=names, name="MCR")

    mcr = w * (C @ w) / port_vol
    return pd.Series(mcr, index=names, name="MCR")


def risk_contribution_pct(
    weights: np.ndarray | pd.Series,
    cov: np.ndarray | pd.DataFrame,
) -> pd.Series:
    """Percentage contribution to total risk (sums to ~100%)."""
    mcr = marginal_risk_contribution(weights, cov)
    total = mcr.sum()
    if total == 0:
        return mcr * 0
    return (mcr / total * 100).round(2)


# ──────────────────────────────────────────────────────────────
# Beta to BTC (adapted: benchmark is BTC, not SPY)
# ──────────────────────────────────────────────────────────────


def beta_to_btc(asset_returns: pd.Series, btc_returns: pd.Series) -> float:
    """Full-sample beta of an asset's returns to BTC.

    beta = cov(asset, btc) / var(btc).
    Same math as the equity CAPM beta; the market leg is BTC.
    """
    aligned = pd.concat(
        [asset_returns.rename("a"), btc_returns.rename("b")], axis=1
    ).dropna()
    if len(aligned) < 3:
        return np.nan
    var_b = float(aligned["b"].var())
    if var_b < 1e-12:
        return np.nan
    return float(aligned["a"].cov(aligned["b"]) / var_b)


def rolling_beta_to_btc(
    asset_returns: pd.Series,
    btc_returns: pd.Series,
    window: int = 30,
) -> pd.Series:
    """Rolling BTC beta (default 30-day window for a crypto desk)."""
    aligned = pd.concat(
        [asset_returns.rename("a"), btc_returns.rename("b")], axis=1
    ).dropna()
    cov_r = aligned["a"].rolling(window).cov(aligned["b"])
    var_r = aligned["b"].rolling(window).var()
    beta = cov_r / var_r
    beta.name = f"beta_btc_{asset_returns.name}"
    return beta


def portfolio_beta(weights: pd.Series, betas: pd.Series) -> float:
    """Book-level BTC beta = weighted sum of per-asset betas.

    A book beta near 1 means it moves with BTC (no real diversification from
    holding many alts); near 0 means market-neutral to BTC.
    """
    idx = weights.index.intersection(betas.index)
    if len(idx) == 0:
        return np.nan
    w = weights.loc[idx]
    b = betas.loc[idx].fillna(0.0)
    return float((w * b).sum())


# ──────────────────────────────────────────────────────────────
# Tail metrics (adapted: 365 annualization)
# ──────────────────────────────────────────────────────────────


def tail_metrics(returns: pd.Series, periods_per_year: int = M.PERIODS_PER_YEAR) -> dict[str, float]:
    """Comprehensive tail risk metrics for a return series."""
    r = returns.dropna()
    if r.empty:
        return {}

    var95, cvar95 = M.var_cvar(r, 0.95)
    var99, cvar99 = M.var_cvar(r, 0.99)
    gtp = M.gain_to_pain(r)

    skew = float(r.skew())
    kurt = float(r.kurtosis())  # excess kurtosis

    downside = r[r < 0.0]
    downside_vol = (
        float(downside.std() * np.sqrt(periods_per_year)) if len(downside) > 1 else np.nan
    )
    ann_ret = M.annualize_return(r, periods_per_year)
    sortino = ann_ret / downside_vol if downside_vol and downside_vol > 0 else np.nan

    mdd = M.max_drawdown(pd.Series((1 + r).cumprod()))
    calmar = ann_ret / abs(mdd) if mdd and not np.isnan(mdd) and mdd != 0 else np.nan

    return {
        "VaR_95": var95,
        "CVaR_95": cvar95,
        "VaR_99": var99,
        "CVaR_99": cvar99,
        "Skewness": skew,
        "Excess_Kurtosis": kurt,
        "Sortino": sortino,
        "Calmar": calmar,
        "Gain_to_Pain": gtp if gtp is not None else np.nan,
        "Max_Drawdown": mdd,
        "Worst_Day": float(r.min()),
        "Best_Day": float(r.max()),
    }


# ──────────────────────────────────────────────────────────────
# Orchestrator the Risk panel calls
# ──────────────────────────────────────────────────────────────


@dataclass
class PortfolioRisk:
    """Everything the Risk panel renders, computed from a returns frame."""

    weights: pd.Series
    ann_vol: float
    ewma_vol: float
    hhi: float
    effective_bets: float
    top3_concentration: float
    book_beta_btc: float
    per_asset_beta: pd.Series
    risk_contribution: pd.Series
    tail: dict[str, float]
    # Common-history window actually used for vol/tail (see compute_portfolio_risk
    # docstring): rows in the window vs rows in the input frame, plus any assets
    # excluded for having no return data at all. The UI footnotes a shrunk window.
    window_days: int = 0
    frame_days: int = 0
    excluded_assets: tuple[str, ...] = ()


def compute_portfolio_risk(
    weights: pd.Series,
    returns: pd.DataFrame,
    btc_col: str = "BTC",
    rf_annual: float = 0.0,
) -> PortfolioRisk:
    """Compute the full Risk-panel payload.

    Parameters
    ----------
    weights : current portfolio weights, indexed by asset (sum ~1).
    returns : daily simple-return DataFrame, one column per asset. Must
              include `btc_col` for beta. Index is dates.
    btc_col : the BTC column name in `returns`.

    Portfolio vol / risk contribution / tail metrics are computed over the
    COMMON-HISTORY window: rows where every included asset has data. Leading
    NaNs (assets listed mid-window) would otherwise be skipped by the row sum,
    silently treating "asset did not exist" as "asset returned 0%" at full
    weight and diluting every downstream number (2026-07-09 audit). Assets with
    no return data at all are excluded and reported in `excluded_assets`;
    per-asset betas still use each asset's full pairwise overlap with BTC.
    """
    candidates = [a for a in weights.index if a in returns.columns]
    excluded = tuple(a for a in candidates if returns[a].first_valid_index() is None)
    assets = [a for a in candidates if a not in excluded]
    w = weights.loc[assets]
    w = w / w.sum() if w.sum() != 0 else w

    rets = returns[assets].dropna(how="all")
    frame_days = len(rets)
    if assets and frame_days:
        window_start = max(rets[a].first_valid_index() for a in assets)
        rets = rets.loc[rets.index >= window_start]
    window_days = len(rets)
    cov = rets.cov().values

    # Portfolio return series for vol / tail metrics
    port_rets = (rets[assets] * w.reindex(rets.columns).fillna(0.0)).sum(axis=1)

    # Per-asset BTC beta
    if btc_col in returns.columns:
        btc_r = returns[btc_col]
        per_beta = pd.Series(
            {a: beta_to_btc(returns[a], btc_r) for a in assets}, name="beta_btc"
        )
    else:
        per_beta = pd.Series({a: np.nan for a in assets}, name="beta_btc")

    return PortfolioRisk(
        weights=w,
        ann_vol=M.annualize_vol(port_rets),
        ewma_vol=M.ewma_vol(port_rets),
        hhi=herfindahl_index(w),
        effective_bets=effective_n_bets(w),
        top3_concentration=concentration_ratio(w, 3),
        book_beta_btc=portfolio_beta(w, per_beta),
        per_asset_beta=per_beta,
        risk_contribution=risk_contribution_pct(w, cov),
        tail=tail_metrics(port_rets),
        window_days=window_days,
        frame_days=frame_days,
        excluded_assets=excluded,
    )
