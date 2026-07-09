"""Tests for DemoClient and the make_client factory (offline, no network)."""

import asyncio
import os
from unittest.mock import patch

import pytest

from cqd.data.client import make_client
from cqd.data.demo import SAMPLE_BOOK, DemoClient
from cqd.data.exchange import KrakenClient
from cqd.data.portfolio import AccountRisk, compute_account_risk
from cqd.engine.risk import PortfolioRisk

# Construct clients with a fake binary path so KrakenClient._resolve_binary
# succeeds without a real CLI present (no subprocess is ever run in these tests).
_FAKE_BIN = {"CQD_KRAKEN_BIN": "/fake/kraken"}


def _demo() -> DemoClient:
    with patch.dict(os.environ, _FAKE_BIN, clear=False):
        return DemoClient()


def _ramp(base: float, n: int = 40, step: float = 0.01) -> list[tuple[int, float]]:
    """Deterministic gently-varying ascending close series (no randomness)."""
    out = []
    price = base
    for i in range(n):
        price = price * (1.0 + (step if i % 2 == 0 else -step * 0.8))
        out.append((86400 + i * 86400, round(price, 8)))
    return out


# ---------- DemoClient balance ----------


def test_balance_is_sample_book() -> None:
    bal = asyncio.run(_demo().get_balance())
    assert {"BTC", "ETH", "SOL", "USD", "USDC", "ADA"} <= set(bal)
    assert all(v > 0 for v in bal.values())
    # Returns a copy, not the module-level dict.
    assert bal is not SAMPLE_BOOK


def test_trades_consistent_with_book() -> None:
    trades = asyncio.run(_demo().get_trades())
    assert trades  # synthetic buys present
    # Each leg is a normalized cost_basis-shaped dict.
    t = trades[0]
    assert set(t) >= {"symbol", "side", "amount", "price", "cost", "fee"}
    assert t["fee"]["currency"] == "USD"
    # BTC legs sum to the held quantity.
    btc_amt = sum(x["amount"] for x in trades if x["symbol"] == "BTC/USD")
    assert btc_amt == pytest.approx(SAMPLE_BOOK["BTC"])


# ---------- market-data delegation ----------


def test_get_marks_delegates_unchanged() -> None:
    d = _demo()
    seen = {}

    async def fake_marks(pairs):
        seen["pairs"] = pairs
        return {"BTC/USD": 123.45}

    d._client.get_marks = fake_marks
    out = asyncio.run(d.get_marks(["BTCUSD"]))
    assert out == {"BTC/USD": 123.45}  # returned unchanged
    assert seen["pairs"] == ["BTCUSD"]  # forwarded unchanged


def test_get_ohlc_delegates_unchanged() -> None:
    d = _demo()
    seen = {}

    async def fake_ohlc(pair, *, interval=1440, since=None):
        seen["args"] = (pair, interval, since)
        return [(1, 2.0), (2, 3.0)]

    d._client.get_ohlc_closes = fake_ohlc
    out = asyncio.run(d.get_ohlc_closes("ADAUSD", interval=1440))
    assert out == [(1, 2.0), (2, 3.0)]
    assert seen["args"] == ("ADAUSD", 1440, None)


# ---------- drop-in proof: compute_account_risk with a DemoClient ----------


def test_dropin_compute_account_risk() -> None:
    d = _demo()
    marks = {
        "BTC/USD": 70000.0,
        "ETH/USD": 2000.0,
        "SOL/USD": 80.0,
        "USDC/USD": 1.0,
        "ADA/USD": 0.55,
    }

    async def fake_marks(pairs):
        # Map friendly "{bare}USD" -> slash and return only requested marks.
        return {f"{p[:-3]}/USD": marks[f"{p[:-3]}/USD"] for p in pairs}

    async def fake_ohlc(pair, *, interval=1440, since=None):
        # Vary the series per pair so cov/beta are well-defined.
        base = float(len(pair)) + 10.0
        return _ramp(base, step=0.01 + 0.002 * (len(pair) % 3))

    d._client.get_marks = fake_marks
    d._client.get_ohlc_closes = fake_ohlc

    ar = asyncio.run(compute_account_risk(d, days=90))
    assert isinstance(ar, AccountRisk)
    assert isinstance(ar.risk, PortfolioRisk)
    assert ar.total_usd > 0
    # ADA is among the weighted assets.
    assert "ADA" in ar.weights.index
    # Engine fields populated; BTC column drove real (non-NaN) betas.
    assert ar.risk.ann_vol == ar.risk.ann_vol  # not NaN
    assert ar.risk.per_asset_beta.notna().any()
    assert "BTC" in ar.risk.per_asset_beta.index


# ---------- factory ----------


def test_make_client_demo_true() -> None:
    with patch.dict(os.environ, _FAKE_BIN, clear=False):
        c = make_client(demo=True)
    assert isinstance(c, DemoClient)
    assert c.is_demo is True


def test_make_client_demo_false_is_live() -> None:
    with patch.dict(os.environ, _FAKE_BIN, clear=False):
        c = make_client(demo=False)
    assert isinstance(c, KrakenClient)
    # Live client carries no is_demo attribute.
    assert getattr(c, "is_demo", False) is False


def test_make_client_env_demo() -> None:
    env = {**_FAKE_BIN, "CQD_DATA_SOURCE": "demo"}
    with patch.dict(os.environ, env, clear=True):
        c = make_client()
    assert isinstance(c, DemoClient)


def test_make_client_auto_demo_when_no_keys() -> None:
    # No CQD_DATA_SOURCE and no keys -> auto-demo so a fresh launch shows data.
    with patch.dict(os.environ, _FAKE_BIN, clear=True):
        c = make_client()
    assert isinstance(c, DemoClient)


def test_make_client_auto_live_when_keys_present() -> None:
    # No CQD_DATA_SOURCE but keys present -> live.
    env = {**_FAKE_BIN, "KRAKEN_API_KEY": "k", "KRAKEN_API_SECRET": "s"}
    with patch.dict(os.environ, env, clear=True):
        c = make_client()
    assert isinstance(c, KrakenClient)


def test_make_client_explicit_live_overrides_missing_keys() -> None:
    # CQD_DATA_SOURCE=live forces live even without keys (user sees auth error).
    env = {**_FAKE_BIN, "CQD_DATA_SOURCE": "live"}
    with patch.dict(os.environ, env, clear=True):
        c = make_client()
    assert isinstance(c, KrakenClient)
