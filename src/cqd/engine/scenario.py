"""Scenario & stress math: historical shocks and Monte Carlo NAV projection.

Pure and deterministic - the Monte Carlo is seeded, so the same inputs always
give the same bands (no I/O, no Qt). The stress model is a first-order beta
propagation: a BTC move of `shock` maps to a portfolio move of book_beta * shock.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Default BTC shocks (fractional moves) for the historical stress table.
DEFAULT_SHOCKS: tuple[float, ...] = (-0.50, -0.30, -0.20, -0.10, 0.10, 0.20)


def scenario_impacts(
    portfolio_beta: float, shocks: tuple[float, ...] = DEFAULT_SHOCKS
) -> dict[float, float]:
    """Portfolio return under each BTC shock, via the book's beta to BTC.

    First-order: portfolio_move = portfolio_beta * btc_shock. Returns
    {shock: portfolio_return}, both as fractions.
    """
    return {shock: portfolio_beta * shock for shock in shocks}


def monte_carlo_nav(
    returns: pd.Series,
    horizon: int = 30,
    n_paths: int = 1000,
    seed: int = 7,
    start: float = 1.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Percentile NAV bands over `horizon` days from the daily return dist.

    Draws normal daily returns with the sample mean/std, compounds `n_paths`
    paths, and returns (p5, p50, p95) arrays of length horizon+1 (index 0 =
    `start`). Seeded for determinism. With too little history, returns three
    flat bands at `start` (nothing to project).
    """
    r = returns.dropna().to_numpy() if hasattr(returns, "dropna") else np.asarray(returns, float)
    if r.size < 2:
        flat = np.full(horizon + 1, start, dtype=float)
        return flat, flat.copy(), flat.copy()
    mu, sigma = float(r.mean()), float(r.std(ddof=1))
    rng = np.random.default_rng(seed)
    draws = rng.normal(mu, sigma, size=(n_paths, horizon))
    paths = start * np.cumprod(1.0 + draws, axis=1)
    paths = np.hstack([np.full((n_paths, 1), start), paths])  # day 0 pinned to start
    return (
        np.percentile(paths, 5, axis=0),
        np.percentile(paths, 50, axis=0),
        np.percentile(paths, 95, axis=0),
    )
