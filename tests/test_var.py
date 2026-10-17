"""VaR suite and VaR backtest (engine/var.py): closed forms and known LRs."""

from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pytest

from cqd.engine import var as V


def _normal(n: int = 4000, sigma: float = 0.02, seed: int = 1) -> np.ndarray:
    return np.random.default_rng(seed).normal(0.0, sigma, n)


def test_historical_var_and_es_are_empirical_tail_figures() -> None:
    r = np.arange(-50, 50) / 1000.0  # -0.050 .. 0.049, uniform
    assert V.historical_var(r, 0.95) == pytest.approx(np.quantile(r, 0.05))
    es = V.historical_es(r, 0.975)
    assert es == pytest.approx(r[r <= np.quantile(r, 0.025)].mean())
    assert es < V.historical_var(r, 0.95) < 0


def test_parametric_var_and_es_match_the_normal_closed_form() -> None:
    r = _normal()
    mu, sd = r.mean(), r.std(ddof=1)
    z = NormalDist().inv_cdf(0.05)
    assert V.parametric_var(r, 0.95) == pytest.approx(mu + sd * z)
    z975 = NormalDist().inv_cdf(0.025)
    assert V.parametric_es(r, 0.975) == pytest.approx(mu - sd * NormalDist().pdf(z975) / 0.025)
    # ES 97.5% of a normal sits close to VaR 99% (the FRTB calibration).
    assert V.parametric_es(r, 0.975) == pytest.approx(V.parametric_var(r, 0.99), rel=0.03)


def test_cornish_fisher_reduces_to_normal_without_skew_or_kurtosis() -> None:
    r = _normal(20000)
    assert V.cornish_fisher_var(r, 0.99) == pytest.approx(V.parametric_var(r, 0.99), rel=0.05)


def test_cornish_fisher_deepens_the_tail_for_fat_tailed_returns() -> None:
    r = np.random.default_rng(3).standard_t(3, 5000) * 0.02
    assert V.cornish_fisher_var(r, 0.99) < V.parametric_var(r, 0.99)


def test_fhs_scales_to_the_current_volatility_regime() -> None:
    calm = _normal(1000, 0.01, seed=2)
    storm = _normal(60, 0.06, seed=4)
    r = np.concatenate([calm, storm])
    # Historical VaR barely moves with 60 storm days; FHS jumps to the new vol.
    assert V.fhs_var(r, 0.99) < 2 * V.historical_var(r, 0.99)


def test_ewma_sigma_path_is_the_riskmetrics_recursion() -> None:
    r = np.array([0.01, -0.02, 0.03])
    path = V.ewma_sigma_path(r)
    assert len(path) == 4 and path[0] ** 2 == pytest.approx(np.var(r, ddof=1))
    for t in range(3):  # day t's forecast feeds on returns before t only
        assert path[t + 1] ** 2 == pytest.approx(0.94 * path[t] ** 2 + 0.06 * r[t] ** 2)


def test_var_rejects_unknown_method() -> None:
    with pytest.raises(ValueError, match="unknown VaR method"):
        V.var([0.1, 0.2], "monte_carlo")


def test_kupiec_matches_the_textbook_value() -> None:
    lr, p = V.kupiec_pof(250, 10, 0.01)
    assert lr == pytest.approx(12.955, abs=0.01)
    assert p < 0.001
    lr0, p0 = V.kupiec_pof(1000, 10, 0.01)
    assert lr0 == pytest.approx(0.0, abs=1e-9) and p0 == pytest.approx(1.0)


def test_christoffersen_flags_clustered_exceptions() -> None:
    spread = np.zeros(500, dtype=int)
    spread[::50] = 1
    clustered = np.zeros(500, dtype=int)
    clustered[200:210] = 1
    assert V.christoffersen_independence(spread)[1] > 0.05
    assert V.christoffersen_independence(clustered)[1] < 0.01


def test_backtest_passes_a_correct_model_and_rejects_a_bad_one() -> None:
    r = _normal(1500, 0.02, seed=11)
    good = V.backtest_var(r, "parametric", alpha=0.99, window=250)
    assert good.n == 1250 and good.expected == pytest.approx(12.5)
    assert good.verdict() == "pass"
    # Volatility doubles halfway: a flat historical window under-forecasts.
    r2 = np.concatenate([_normal(600, 0.01, seed=5), _normal(600, 0.04, seed=6)])
    bad = V.backtest_var(r2, "historical", alpha=0.99, window=250)
    assert bad.exceptions > 2 * bad.expected and bad.verdict() == "reject"
    fhs = V.backtest_var(r2, "fhs", alpha=0.99, window=250)
    assert fhs.exceptions < bad.exceptions


def test_backtest_with_too_little_history_is_empty() -> None:
    bt = V.backtest_var(_normal(100), window=250)
    assert bt.n == 0 and bt.verdict() == "n/a" and math.isnan(bt.rate)


def test_var_suite_reports_every_method() -> None:
    s = V.var_suite(_normal(800))
    assert set(s.var95) == set(V.METHODS) == set(s.var99)
    assert all(s.var99[m] < s.var95[m] < 0 for m in V.METHODS)
    assert s.es975_hist < 0 and s.n_obs == 800
