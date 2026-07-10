"""Tests for the scenario/stress engine (pure, seeded)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from cqd.engine.scenario import DEFAULT_SHOCKS, monte_carlo_nav, scenario_impacts


def test_scenario_impacts_scale_by_beta() -> None:
    imp = scenario_impacts(1.2, shocks=(-0.30, 0.10))
    assert imp[-0.30] == -0.36  # 1.2 * -0.30
    assert imp[0.10] == 0.12
    # Default shocks all present.
    assert set(scenario_impacts(1.0)) == set(DEFAULT_SHOCKS)


def test_monte_carlo_nav_shapes_and_ordering() -> None:
    rng = np.random.default_rng(0)
    returns = pd.Series(rng.normal(0.002, 0.03, 300))
    p5, p50, p95 = monte_carlo_nav(returns, horizon=30, n_paths=500, seed=7)
    assert len(p5) == len(p50) == len(p95) == 31  # horizon + 1
    assert p5[0] == p50[0] == p95[0] == 1.0  # day 0 pinned to start
    # Bands are ordered and fan out over time.
    assert (p5 <= p50).all() and (p50 <= p95).all()
    assert (p95[-1] - p5[-1]) > (p95[1] - p5[1])


def test_monte_carlo_nav_is_deterministic() -> None:
    returns = pd.Series(np.random.default_rng(1).normal(0.001, 0.02, 200))
    a = monte_carlo_nav(returns, seed=42)
    b = monte_carlo_nav(returns, seed=42)
    assert np.array_equal(a[1], b[1])  # same seed -> identical median path


def test_monte_carlo_nav_insufficient_history_is_flat() -> None:
    p5, p50, p95 = monte_carlo_nav(pd.Series([0.01]), horizon=10)
    assert (p5 == 1.0).all() and (p50 == 1.0).all() and (p95 == 1.0).all()
    assert len(p50) == 11
