"""Tests for the portfolio-risk orchestration layer."""

import asyncio

import pytest

from cqd.data.portfolio import (
    AccountRisk,
    EmptyPortfolioError,
    compute_account_risk,
    compute_weights,
)
from cqd.engine.risk import PortfolioRisk

D1, D2, D3, D4 = 86400, 172800, 259200, 345600


# ---------- compute_weights (pure) ----------


def test_two_asset_weights_sum_to_one() -> None:
    balances = {"BTC": 1.0, "ETH": 10.0}
    marks = {"BTC/USD": 60000.0, "ETH/USD": 3000.0}
    w, info = compute_weights(balances, marks)
    # BTC = 60000, ETH = 30000, total = 90000.
    assert info["total_usd"] == pytest.approx(90000.0)
    assert w["BTC"] == pytest.approx(60000.0 / 90000.0)
    assert w["ETH"] == pytest.approx(30000.0 / 90000.0)
    assert w.sum() == pytest.approx(1.0)
    assert list(w.index) == ["BTC", "ETH"]
    assert w.dtype == float


def test_quote_cash_valued_at_qty() -> None:
    balances = {"USD": 5000.0, "BTC": 0.1}
    marks = {"BTC/USD": 50000.0}
    w, info = compute_weights(balances, marks)
    # USD cash valued at qty (mark 1.0) = 5000; BTC = 5000; total 10000.
    assert info["values_usd"]["USD"] == pytest.approx(5000.0)
    assert w["USD"] == pytest.approx(0.5)
    assert w["BTC"] == pytest.approx(0.5)


def test_dust_asset_excluded_and_reported() -> None:
    balances = {"BTC": 1.0, "DUST": 0.5}
    marks = {"BTC/USD": 60000.0, "DUST/USD": 1.0}  # DUST worth $0.50 < $1
    w, info = compute_weights(balances, marks, min_usd=1.0)
    assert "DUST" not in w.index
    assert "DUST" in info["dust"]
    assert info["dust"]["DUST"] == pytest.approx(0.5)


def test_unpriced_asset_reported_not_dropped() -> None:
    balances = {"BTC": 1.0, "WEIRD": 100.0}
    marks = {"BTC/USD": 60000.0}  # no WEIRD/USD mark
    w, info = compute_weights(balances, marks)
    assert "WEIRD" not in w.index
    assert "WEIRD" in info["unpriced"]


def test_all_dust_yields_empty_weights() -> None:
    balances = {"DUST": 0.1}
    marks = {"DUST/USD": 0.5}
    w, info = compute_weights(balances, marks, min_usd=1.0)
    assert w.empty
    assert info["total_usd"] == 0.0


# ---------- compute_account_risk (mocked client + real engine) ----------


def _ramp(base: float, n: int = 40) -> list[tuple[int, float]]:
    """A gently varying ascending close series so cov/beta are well-defined."""
    out = []
    ts = D1
    price = base
    for i in range(n):
        # Deterministic small zig-zag, no Math.random.
        price = price * (1.0 + (0.01 if i % 2 == 0 else -0.008))
        out.append((ts + i * 86400, round(price, 6)))
    return out


class _StubClient:
    def __init__(self, balances, marks, ohlc) -> None:
        self._balances = balances
        self._marks = marks
        self._ohlc = ohlc

    async def get_balance(self):
        return self._balances

    async def get_marks(self, pairs):
        # Return only the slash marks for requested friendly pairs.
        out = {}
        for p in pairs:
            slash = f"{p[:-3]}/USD"
            if slash in self._marks:
                out[slash] = self._marks[slash]
        return out

    async def get_ohlc_closes(self, pair, *, interval=1440, since=None):
        return self._ohlc.get(pair[:-3], [])


def _make_client():
    balances = {"ETH": 10.0, "SOL": 100.0, "USD": 2000.0}
    marks = {"ETH/USD": 3000.0, "SOL/USD": 150.0}
    ohlc = {
        "ETH": _ramp(3000.0),
        "SOL": _ramp(150.0),
        "BTC": _ramp(60000.0),  # benchmark, not held
    }
    return _StubClient(balances, marks, ohlc)


def test_compute_account_risk_returns_engine_result() -> None:
    ar = asyncio.run(compute_account_risk(_make_client()))
    assert isinstance(ar, AccountRisk)
    assert isinstance(ar.risk, PortfolioRisk)
    # Engine fields populated (not recomputed here).
    assert not ar.risk.weights.empty
    assert ar.risk.ann_vol == ar.risk.ann_vol  # not NaN
    assert ar.risk.book_beta_btc == ar.risk.book_beta_btc
    assert ar.total_usd > 0
    assert "values_usd" in ar.info
    # USD cash is held and weighted alongside ETH/SOL.
    assert set(ar.weights.index) == {"ETH", "SOL", "USD"}


def test_btc_in_returns_even_when_not_held() -> None:
    # Held = ETH, SOL, USD (no BTC). per_asset_beta is computed against the BTC
    # benchmark column the returns feed auto-includes, so betas are real floats.
    ar = asyncio.run(compute_account_risk(_make_client()))
    betas = ar.risk.per_asset_beta
    assert "ETH" in betas.index and "SOL" in betas.index
    assert betas.notna().any()


def test_empty_portfolio_raises() -> None:
    client = _StubClient(balances={}, marks={}, ohlc={})
    with pytest.raises(EmptyPortfolioError):
        asyncio.run(compute_account_risk(client))


def test_all_dust_account_raises() -> None:
    client = _StubClient(balances={"SHIB": 1.0}, marks={"SHIB/USD": 0.1}, ohlc={"SHIB": _ramp(0.1)})
    with pytest.raises(EmptyPortfolioError):
        asyncio.run(compute_account_risk(client, min_usd=1.0))


class _CashGuardedClient:
    """Mocked client whose get_ohlc_closes RAISES on a cash pair (USDUSD).

    Mirrors the live CLI rejecting {quote}{quote}, so the test fails if the code
    regresses to fetching a cash pair.
    """

    def __init__(self, balances, marks, ohlc) -> None:
        self._balances = balances
        self._marks = marks
        self._ohlc = ohlc

    async def get_balance(self):
        return dict(self._balances)

    async def get_marks(self, pairs):
        return {
            f"{p[:-3]}/USD": self._marks[f"{p[:-3]}/USD"]
            for p in pairs
            if f"{p[:-3]}/USD" in self._marks
        }

    async def get_ohlc_closes(self, pair, *, interval=1440, since=None):
        bare = pair[:-3]
        if bare not in self._ohlc:
            raise AssertionError(f"cash pair must not be fetched: {pair}")
        return self._ohlc[bare]


def test_usd_cash_runs_end_to_end_with_zero_risk_contribution() -> None:
    # Regression for the live USDUSD crash: a book holding USD cash must run.
    balances = {"BTC": 0.1, "ETH": 2.0, "USD": 3000.0}
    marks = {"BTC/USD": 60000.0, "ETH/USD": 3000.0}
    ohlc = {"BTC": _ramp(60000.0), "ETH": _ramp(3000.0)}  # no "USD" entry
    client = _CashGuardedClient(balances, marks, ohlc)

    ar = asyncio.run(compute_account_risk(client, days=90))

    # USD is weighted (BTC 6000, ETH 6000, USD 3000 -> 0.4/0.4/0.2).
    assert "USD" in ar.weights.index
    assert ar.weights["USD"] == pytest.approx(0.2)
    # Cash contributes zero beta and zero risk, no NaN.
    assert ar.risk.per_asset_beta["USD"] == pytest.approx(0.0, abs=1e-9)
    assert ar.risk.risk_contribution["USD"] == pytest.approx(0.0, abs=1e-9)
    assert ar.risk.ann_vol == ar.risk.ann_vol  # not NaN
    # HHI reflects the cash weight (0.4^2 + 0.4^2 + 0.2^2 = 0.36), not 0.5 that
    # dropping USD would give.
    assert ar.risk.hhi == pytest.approx(0.36)
