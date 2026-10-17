"""Historical crash replays, downside beta, stablecoin depeg, MC fan, rolling
co-movement and liquidity. Pure numpy/pandas: the crash-window moves come in
as data (see `cqd.data.crash_history`), never fetched here.

Replays apply each asset's REAL point-to-point move over a past crash to
today's weights. An asset with no price in that window (listed later) is
proxied by its beta to BTC times BTC's move, and is flagged, so a replay never
silently treats "did not exist" as "did not move".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

USD_CASH = frozenset({"USD", "ZUSD"})
STABLECOINS = frozenset({"USDC", "USDT", "DAI", "PYUSD", "USDG", "RLUSD"})


# ---------------------------------------------------------------- replays


@dataclass(frozen=True)
class CrashWindow:
    key: str
    label: str
    start: str
    end: str
    note: str
    moves: dict[str, float]  # bare asset -> point-to-point return


@dataclass(frozen=True)
class CrashReplay:
    key: str
    label: str
    start: str
    end: str
    portfolio_return: float
    btc_return: float
    contributions: dict[str, float]  # asset -> weight * move (sums to portfolio_return)
    proxied: tuple[str, ...] = ()  # assets priced via beta x BTC
    note: str = ""


def replay_crash(
    weights: pd.Series, window: CrashWindow, betas: pd.Series | None = None
) -> CrashReplay:
    """Today's weights through one historical crash window."""
    btc = float(window.moves.get("BTC", math.nan))
    contrib: dict[str, float] = {}
    proxied: list[str] = []
    for asset, w in weights.items():
        a = str(asset)
        if a in USD_CASH:
            move = 0.0
        elif a in window.moves:
            move = float(window.moves[a])
        else:
            beta = float(betas.get(a, 1.0)) if betas is not None else 1.0
            if not math.isfinite(beta):
                beta = 1.0
            move = beta * btc if a not in STABLECOINS else 0.0
            proxied.append(a)
        contrib[a] = float(w) * move
    return CrashReplay(
        key=window.key,
        label=window.label,
        start=window.start,
        end=window.end,
        portfolio_return=float(sum(contrib.values())),
        btc_return=btc,
        contributions=contrib,
        proxied=tuple(proxied),
        note=window.note,
    )


def replay_all(weights: pd.Series, windows, betas: pd.Series | None = None) -> list[CrashReplay]:
    return [replay_crash(weights, w, betas) for w in windows]


def depeg_shock(weights: pd.Series, shocks: dict[str, float] | None = None) -> dict[str, float]:
    """Portfolio move if stablecoins break their peg.

    Default shocks: USDC -13% (its March 2023 SVB low, ~0.87) and USDT -5%.
    Returns {"portfolio": total, <asset>: contribution} over held stablecoins.
    """
    shocks = shocks or {"USDC": -0.13, "USDT": -0.05}
    out = {a: float(weights.get(a, 0.0)) * s for a, s in shocks.items() if a in weights.index}
    out["portfolio"] = float(sum(out.values()))
    return out


# ---------------------------------------------------------------- betas


def downside_beta(asset: pd.Series, btc: pd.Series) -> float:
    """Beta to BTC estimated on BTC's down days only (cov/var | BTC < 0).

    Crypto co-moves harder in sell-offs; the full-sample beta understates the
    hit when it matters."""
    df = pd.concat([asset, btc], axis=1, join="inner").dropna()
    df = df[df.iloc[:, 1] < 0]
    if len(df) < 10:
        return math.nan
    var = float(df.iloc[:, 1].var(ddof=1))
    return float(df.cov().iloc[0, 1] / var) if var > 0 else math.nan


def downside_betas(returns: pd.DataFrame, btc_col: str = "BTC") -> pd.Series:
    if btc_col not in returns.columns:
        return pd.Series(dtype=float)
    return pd.Series({c: downside_beta(returns[c], returns[btc_col]) for c in returns.columns})


def rolling_correlation(
    returns: pd.DataFrame, btc_col: str = "BTC", window: int = 90
) -> pd.DataFrame:
    """Rolling correlation of every column with BTC (BTC itself dropped)."""
    if btc_col not in returns.columns:
        return pd.DataFrame()
    others = [c for c in returns.columns if c != btc_col]
    return pd.DataFrame(
        {c: returns[c].rolling(window).corr(returns[btc_col]) for c in others}, index=returns.index
    )


def rolling_beta(returns: pd.DataFrame, btc_col: str = "BTC", window: int = 90) -> pd.DataFrame:
    if btc_col not in returns.columns:
        return pd.DataFrame()
    b = returns[btc_col]
    var = b.rolling(window).var(ddof=1)
    others = [c for c in returns.columns if c != btc_col]
    return pd.DataFrame(
        {c: returns[c].rolling(window).cov(b) / var for c in others}, index=returns.index
    )


# ---------------------------------------------------------------- MC fan


FAN_QUANTILES: tuple[float, ...] = (0.05, 0.25, 0.50, 0.75, 0.95)


@dataclass(frozen=True)
class Fan:
    horizon: int
    quantiles: tuple[float, ...]
    bands: dict[float, list[float]]  # quantile -> NAV path (index 0 = start)
    block: int
    n_paths: int
    var95_horizon: float = math.nan  # 5th pct of the horizon return
    extra: dict[str, float] = field(default_factory=dict)


def block_bootstrap_fan(
    returns,
    horizon: int = 30,
    n_paths: int = 2000,
    block: int = 5,
    seed: int = 7,
    start: float = 1.0,
    zero_drift: bool = True,
    quantiles: tuple[float, ...] = FAN_QUANTILES,
) -> Fan:
    """NAV percentile fan from a block bootstrap of daily returns.

    Blocks of `block` consecutive days keep volatility clustering and the fat
    tails that a normal draw throws away. With `zero_drift` the sample mean is
    removed first, so a lucky (or unlucky) history does not project its trend
    forward: the fan shows risk, not a forecast."""
    if hasattr(returns, "dropna"):
        r = returns.dropna().to_numpy(float)
    else:
        r = np.asarray(returns, float)
    r = r[np.isfinite(r)]
    if r.size < max(2, block):
        flat = [start] * (horizon + 1)
        return Fan(horizon, quantiles, {q: list(flat) for q in quantiles}, block, 0)
    if zero_drift:
        r = r - r.mean()
    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(horizon / block)
    starts = rng.integers(0, r.size - block + 1, size=(n_paths, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_paths, -1)[:, :horizon]
    paths = start * np.cumprod(1.0 + r[idx], axis=1)
    paths = np.hstack([np.full((n_paths, 1), start), paths])
    bands = {q: np.quantile(paths, q, axis=0).tolist() for q in quantiles}
    end = paths[:, -1] / start - 1.0
    return Fan(
        horizon, quantiles, bands, block, n_paths, var95_horizon=float(np.quantile(end, 0.05))
    )


# ---------------------------------------------------------------- liquidity


@dataclass(frozen=True)
class LiquidityRow:
    asset: str
    value_usd: float
    adv_usd: float
    pct_of_adv: float
    days_to_liquidate: float


def average_daily_volume_usd(volume_base: pd.Series, close: pd.Series, days: int = 30) -> float:
    """Mean of daily base volume x close over the last `days` closed bars."""
    df = pd.concat([volume_base, close], axis=1, join="inner").dropna().tail(days)
    if df.empty:
        return math.nan
    return float((df.iloc[:, 0] * df.iloc[:, 1]).mean())


def liquidity_table(
    values_usd: dict[str, float], adv_usd: dict[str, float], participation: float = 0.10
) -> list[LiquidityRow]:
    """Position size against Kraken's own ADV, and days to exit at a
    `participation` share of daily volume (10%: the usual no-footprint rule).
    Cash rows are skipped; an asset with no volume data gets NaN, not 0."""
    rows: list[LiquidityRow] = []
    for asset, value in sorted(values_usd.items(), key=lambda kv: -kv[1]):
        if asset in USD_CASH:
            continue
        adv = float(adv_usd.get(asset, math.nan))
        ok = math.isfinite(adv) and adv > 0
        rows.append(
            LiquidityRow(
                asset=asset,
                value_usd=float(value),
                adv_usd=adv,
                pct_of_adv=value / adv if ok else math.nan,
                days_to_liquidate=value / (participation * adv) if ok else math.nan,
            )
        )
    return rows
