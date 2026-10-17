"""One-day VaR and expected shortfall, four ways, plus a VaR backtest.

Pure numpy/pandas (stdlib NormalDist for the normal quantile): no I/O, no Qt.
Sign convention matches `metrics.var_cvar`: VaR and ES are RETURNS, so a loss
is a negative number (VaR 95% = -0.04 means "a 4% loss is exceeded on 5% of
days"). Inputs are daily simple returns.

Methods
- historical: the empirical (1 - alpha) quantile.
- filtered historical (FHS): returns standardised by their own RiskMetrics
  EWMA vol (lambda 0.94, known before each day), then re-scaled by today's
  vol forecast. Reacts to volatility clustering, keeps the fat tails.
- parametric: normal with the sample mean and standard deviation.
- Cornish-Fisher: the normal quantile corrected for sample skew and excess
  kurtosis (a cheap fat-tail adjustment; unreliable at extreme kurtosis).

ES 97.5% is the Basel FRTB tail measure (about as conservative as VaR 99% for
a normal, more honest for fat tails); it is reported historically and under
FHS. The backtest rolls a one-step-ahead forecast through history and tests
the exceptions with Kupiec (right frequency) and Christoffersen (no
clustering, then both jointly).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import NormalDist

import numpy as np
import pandas as pd

EWMA_LAMBDA = 0.94
METHODS: tuple[str, ...] = ("historical", "fhs", "parametric", "cornish_fisher")
METHOD_LABELS = {
    "historical": "Historical",
    "fhs": "Filtered historical (EWMA)",
    "parametric": "Parametric (normal)",
    "cornish_fisher": "Cornish-Fisher",
}
_N = NormalDist()


def _clean(returns) -> np.ndarray:
    if hasattr(returns, "dropna"):
        return returns.dropna().astype(float).to_numpy()
    r = np.asarray(returns, dtype=float)
    return r[~np.isnan(r)]


def ewma_sigma_path(r: np.ndarray, lam: float = EWMA_LAMBDA) -> np.ndarray:
    """Daily EWMA sigma forecasts: out[t] uses r[:t] only; out[n] is tomorrow's.

    Seeded with the sample variance of the whole series (as `metrics.ewma_vol`
    does), so out[0] is defined. Length len(r) + 1.
    """
    n = len(r)
    out = np.empty(n + 1)
    var = float(np.var(r, ddof=1)) if n > 1 else 0.0
    out[0] = math.sqrt(var)
    for t in range(n):
        var = lam * var + (1.0 - lam) * r[t] * r[t]
        out[t + 1] = math.sqrt(var)
    return out


def historical_var(returns, alpha: float = 0.95) -> float:
    r = _clean(returns)
    return float(np.quantile(r, 1.0 - alpha)) if r.size else math.nan


def historical_es(returns, alpha: float = 0.975) -> float:
    r = _clean(returns)
    if not r.size:
        return math.nan
    q = np.quantile(r, 1.0 - alpha)
    tail = r[r <= q]
    return float(tail.mean()) if tail.size else math.nan


def parametric_var(returns, alpha: float = 0.95) -> float:
    r = _clean(returns)
    if r.size < 2:
        return math.nan
    return float(r.mean() + r.std(ddof=1) * _N.inv_cdf(1.0 - alpha))


def parametric_es(returns, alpha: float = 0.975) -> float:
    r = _clean(returns)
    if r.size < 2:
        return math.nan
    z = _N.inv_cdf(1.0 - alpha)
    return float(r.mean() - r.std(ddof=1) * _N.pdf(z) / (1.0 - alpha))


def cornish_fisher_var(returns, alpha: float = 0.95) -> float:
    r = _clean(returns)
    if r.size < 4:
        return math.nan
    s = pd.Series(r)
    skew, kurt = float(s.skew()), float(s.kurtosis())  # kurtosis is excess
    z = _N.inv_cdf(1.0 - alpha)
    z_cf = (
        z
        + (z * z - 1.0) * skew / 6.0
        + (z**3 - 3.0 * z) * kurt / 24.0
        - (2.0 * z**3 - 5.0 * z) * skew * skew / 36.0
    )
    return float(r.mean() + r.std(ddof=1) * z_cf)


def _fhs_residuals(r: np.ndarray, lam: float) -> tuple[np.ndarray, float]:
    sig = ewma_sigma_path(r, lam)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = r / sig[:-1]
    z = z[np.isfinite(z)]
    return z, float(sig[-1])


def fhs_var(returns, alpha: float = 0.95, lam: float = EWMA_LAMBDA) -> float:
    r = _clean(returns)
    if r.size < 2:
        return math.nan
    z, sigma_next = _fhs_residuals(r, lam)
    return float(sigma_next * np.quantile(z, 1.0 - alpha)) if z.size else math.nan


def fhs_es(returns, alpha: float = 0.975, lam: float = EWMA_LAMBDA) -> float:
    r = _clean(returns)
    if r.size < 2:
        return math.nan
    z, sigma_next = _fhs_residuals(r, lam)
    if not z.size:
        return math.nan
    q = np.quantile(z, 1.0 - alpha)
    return float(sigma_next * z[z <= q].mean())


_VAR_FUNCS = {
    "historical": historical_var,
    "fhs": fhs_var,
    "parametric": parametric_var,
    "cornish_fisher": cornish_fisher_var,
}


def var(returns, method: str = "historical", alpha: float = 0.95) -> float:
    """One-day VaR by `method` (one of METHODS)."""
    try:
        return _VAR_FUNCS[method](returns, alpha)
    except KeyError:
        raise ValueError(f"unknown VaR method {method!r}") from None


@dataclass(frozen=True)
class VarSuite:
    """Every VaR/ES figure the Risk page shows, as daily returns (losses < 0)."""

    var95: dict[str, float]
    var99: dict[str, float]
    es975_hist: float
    es975_fhs: float
    es975_normal: float
    n_obs: int


def var_suite(returns) -> VarSuite:
    r = _clean(returns)
    return VarSuite(
        var95={m: var(r, m, 0.95) for m in METHODS},
        var99={m: var(r, m, 0.99) for m in METHODS},
        es975_hist=historical_es(r, 0.975),
        es975_fhs=fhs_es(r, 0.975),
        es975_normal=parametric_es(r, 0.975),
        n_obs=int(r.size),
    )


# ---------------------------------------------------------------- backtest


def _chi2_sf(x: float, df: int) -> float:
    """Chi-square survival function for df 1 or 2 (all the tests need)."""
    if not math.isfinite(x) or x < 0:
        return math.nan
    if df == 1:
        return math.erfc(math.sqrt(x / 2.0))
    if df == 2:
        return math.exp(-x / 2.0)
    raise ValueError("df must be 1 or 2")


def _xlogy(x: float, y: float) -> float:
    return 0.0 if x == 0 else x * math.log(y)


def kupiec_pof(n: int, x: int, p: float) -> tuple[float, float]:
    """Kupiec proportion-of-failures LR and its p-value (chi2, 1 df).

    H0: the exception rate is p. A small p-value rejects the model (too many
    OR too few exceptions)."""
    if n <= 0:
        return math.nan, math.nan
    phat = x / n
    ll0 = _xlogy(n - x, 1.0 - p) + _xlogy(x, p)
    ll1 = _xlogy(n - x, 1.0 - phat) + _xlogy(x, phat)
    lr = max(0.0, -2.0 * (ll0 - ll1))
    return lr, _chi2_sf(lr, 1)


def christoffersen_independence(hits: np.ndarray) -> tuple[float, float]:
    """Christoffersen Markov independence LR and p-value (chi2, 1 df).

    H0: an exception today does not make one tomorrow more likely."""
    h = np.asarray(hits, dtype=int)
    if h.size < 2:
        return math.nan, math.nan
    prev, cur = h[:-1], h[1:]
    n00 = int(((prev == 0) & (cur == 0)).sum())
    n01 = int(((prev == 0) & (cur == 1)).sum())
    n10 = int(((prev == 1) & (cur == 0)).sum())
    n11 = int(((prev == 1) & (cur == 1)).sum())
    p01 = n01 / (n00 + n01) if (n00 + n01) else 0.0
    p11 = n11 / (n10 + n11) if (n10 + n11) else 0.0
    p = (n01 + n11) / (n00 + n01 + n10 + n11)
    ll0 = _xlogy(n00 + n10, 1.0 - p) + _xlogy(n01 + n11, p)
    ll1 = (
        _xlogy(n00, 1.0 - p01) + _xlogy(n01, p01) + _xlogy(n10, 1.0 - p11) + _xlogy(n11, p11)
    )
    lr = max(0.0, -2.0 * (ll0 - ll1))
    return lr, _chi2_sf(lr, 1)


@dataclass(frozen=True)
class VarBacktest:
    method: str
    alpha: float
    window: int
    n: int
    exceptions: int
    expected: float
    kupiec_lr: float
    kupiec_p: float
    ind_lr: float
    ind_p: float
    cc_lr: float
    cc_p: float
    forecasts: list[float] = field(default_factory=list)
    realized: list[float] = field(default_factory=list)

    @property
    def rate(self) -> float:
        return self.exceptions / self.n if self.n else math.nan

    def verdict(self, level: float = 0.05) -> str:
        """'pass', 'reject' (coverage or clustering fails at `level`) or 'n/a'."""
        if not self.n or not math.isfinite(self.cc_p):
            return "n/a"
        bad = self.kupiec_p < level or self.ind_p < level
        return "reject" if bad else "pass"


def backtest_var(
    returns, method: str = "historical", alpha: float = 0.99, window: int = 250
) -> VarBacktest:
    """Roll a one-day VaR forecast from the trailing `window` days through the
    sample, count days whose return fell below it, and test the exceptions."""
    r = _clean(returns)
    n = r.size - window
    if n <= 0:
        nan = math.nan
        return VarBacktest(method, alpha, window, 0, 0, 0.0, nan, nan, nan, nan, nan, nan)
    fc = np.array([var(r[t - window : t], method, alpha) for t in range(window, r.size)])
    realized = r[window:]
    hits = (realized < fc).astype(int)
    x = int(hits.sum())
    p = 1.0 - alpha
    k_lr, k_p = kupiec_pof(n, x, p)
    i_lr, i_p = christoffersen_independence(hits)
    cc = k_lr + i_lr if math.isfinite(i_lr) else math.nan
    return VarBacktest(
        method=method,
        alpha=alpha,
        window=window,
        n=n,
        exceptions=x,
        expected=n * p,
        kupiec_lr=k_lr,
        kupiec_p=k_p,
        ind_lr=i_lr,
        ind_p=i_p,
        cc_lr=cc,
        cc_p=_chi2_sf(cc, 2),
        forecasts=fc.tolist(),
        realized=realized.tolist(),
    )
