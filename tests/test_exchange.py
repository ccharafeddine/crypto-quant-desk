"""Hermetic tests for the Kraken CLI wrapper.

asyncio.create_subprocess_exec is mocked, so no real CLI and no network. Async
methods are driven via asyncio.run() inside plain sync tests, so no async test
plugin is required.
"""

import asyncio
import json
from unittest.mock import patch

import pytest

from cqd.data.exchange import (
    KrakenAuthError,
    KrakenCLINotFound,
    KrakenClient,
)

# Verified public-data literals (kraken 0.3.2).
TICKER_JSON = {
    "XXBTZUSD": {
        "a": ["70860.00000", "1", "1.000"],
        "b": ["70859.90000", "3", "3.000"],
        "c": ["70860.00000", "0.00000138"],
        "h": ["71315.50000", "73619.80000"],
        "l": ["70000.00000", "70000.00000"],
        "o": "71315.50000",
        "p": ["70680.09937", "71581.40511"],
        "t": [13902, 79365],
        "v": ["270.96343504", "2447.04044688"],
    }
}

OHLC_JSON = {
    "XXBTZUSD": [
        [1718323200, "66000.0", "67000.0", "64000.0", "65500.0", "65800.0", "1200.0", 18000],
        [1718150400, "67348.6", "69969.0", "66923.0", "68233.7", "68567.0", "1900.5", 29809],
        [1718236800, "68233.7", "68500.0", "65000.0", "66000.0", "66800.0", "1500.0", 21000],
    ],
    "last": 1780272000,
}

BALANCE_JSON = {"XXBT": "0.5", "ZUSD": "1000.0", "SOL": "12.25"}

TRADES_JSON = {
    "trades": {
        "TX123": {
            "pair": "XXBTZUSD",
            "type": "buy",
            "vol": "0.5",
            "price": "70000.0",
            "cost": "35000.0",
            "fee": "91.0",
            "time": 1718150400.0,
        }
    },
    "count": 1,
}

AUTH_ERROR_JSON = {
    "error": "auth",
    "message": "Authentication failed: No Spot API credentials found.",
}


class _FakeProc:
    """Stand-in for the asyncio subprocess with a canned communicate()."""

    def __init__(self, stdout: bytes, stderr: bytes, returncode: int) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self):
        return self._stdout, self._stderr


def _patch_subprocess(body, *, returncode: int = 0, stderr: bytes = b""):
    """Patch create_subprocess_exec to return a fake proc emitting `body` JSON.

    Returns the mock so tests can inspect the argv/env it was called with.
    """
    stdout = json.dumps(body).encode() if body is not None else b""
    fake = _FakeProc(stdout, stderr, returncode)

    async def _fake_exec(*args, **kwargs):
        _fake_exec.last_args = args
        _fake_exec.last_kwargs = kwargs
        return fake

    _fake_exec.last_args = None
    _fake_exec.last_kwargs = None
    return patch("asyncio.create_subprocess_exec", side_effect=_fake_exec), _fake_exec


def _client() -> KrakenClient:
    # Force a known binary so the resolver never depends on PATH in tests.
    with patch.dict("os.environ", {"CQD_KRAKEN_BIN": "/fake/kraken"}, clear=False):
        return KrakenClient(api_key="KEY123", api_secret="SECRET456")


# ---------- success paths ----------


def test_get_marks_success() -> None:
    p, _ = _patch_subprocess(TICKER_JSON)
    with p:
        out = asyncio.run(_client().get_marks(["BTCUSD"]))
    assert out == {"BTC/USD": 70860.0}


def test_get_ohlc_closes_success() -> None:
    p, _ = _patch_subprocess(OHLC_JSON)
    with p:
        out = asyncio.run(_client().get_ohlc_closes("BTCUSD"))
    # ascending, "last" dropped, (int, float) tuples.
    assert out == [(1718150400, 68233.7), (1718236800, 66000.0), (1718323200, 65500.0)]
    assert all(isinstance(t, int) and isinstance(c, float) for t, c in out)


def test_get_balance_success() -> None:
    p, _ = _patch_subprocess(BALANCE_JSON)
    with p:
        out = asyncio.run(_client().get_balance())
    assert out == {"BTC": 0.5, "USD": 1000.0, "SOL": 12.25}


def test_get_trades_success() -> None:
    p, _ = _patch_subprocess(TRADES_JSON)
    with p:
        out = asyncio.run(_client().get_trades())
    assert len(out) == 1
    t = out[0]
    assert t["symbol"] == "BTC/USD"
    assert t["side"] == "buy"
    assert t["amount"] == 0.5
    assert t["cost"] == 35000.0
    assert t["fee"] == {"cost": 91.0, "currency": "USD"}


# ---------- per-pair degradation ----------


def test_get_marks_degrades_per_pair_on_batch_failure() -> None:
    # Regression (2026-07-09 audit): one unknown pair (delisted, ETH2 from a
    # folded sub-balance) failed the whole ticker batch and blanked the Risk
    # panel. The wrapper must retry per pair and skip only the bad ones.
    error_body = json.dumps({"error": "api", "message": "Unknown asset pair"}).encode()
    good_body = json.dumps(TICKER_JSON).encode()
    # Batch fails, then per-pair: BTCUSD succeeds, ETH2USD fails.
    responses = [
        _FakeProc(error_body, b"", 1),
        _FakeProc(good_body, b"", 0),
        _FakeProc(error_body, b"", 1),
    ]

    async def _fake_exec(*args, **kwargs):
        return responses.pop(0)

    with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
        out = asyncio.run(_client().get_marks(["BTCUSD", "ETH2USD"]))

    assert out == {"BTC/USD": 70860.0}  # bad pair absent, not raised
    assert not responses  # all three calls were made


# ---------- error paths ----------


def test_auth_failure_raises() -> None:
    p, _ = _patch_subprocess(AUTH_ERROR_JSON, returncode=1)
    with p, pytest.raises(KrakenAuthError):
        asyncio.run(_client().get_balance())


def test_binary_missing_raises() -> None:
    # No CQD_KRAKEN_BIN, no _MEIPASS, and which() returns None.
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("shutil.which", return_value=None),
        pytest.raises(KrakenCLINotFound),
    ):
        KrakenClient()


# ---------- security: keys in env only, never argv ----------


def test_keys_in_env_never_argv() -> None:
    p, fake = _patch_subprocess(BALANCE_JSON)
    with p:
        asyncio.run(_client().get_balance())

    argv = fake.last_args
    env = fake.last_kwargs["env"]

    # No key material anywhere in argv.
    joined = " ".join(argv)
    assert "KEY123" not in joined
    assert "SECRET456" not in joined
    assert "--api-key" not in argv
    assert "--api-secret" not in argv

    # Keys present only in the subprocess env.
    assert env["KRAKEN_API_KEY"] == "KEY123"
    assert env["KRAKEN_API_SECRET"] == "SECRET456"


def test_public_call_omits_keys_from_env() -> None:
    p, fake = _patch_subprocess(TICKER_JSON)
    # Clear any inherited creds so we prove the wrapper did not inject them.
    clean = {"CQD_KRAKEN_BIN": "/fake/kraken"}
    with patch.dict("os.environ", clean, clear=True), p:
        asyncio.run(KrakenClient(api_key="KEY123", api_secret="SECRET456").get_marks(["BTCUSD"]))
    env = fake.last_kwargs["env"]
    # Public calls must not inject credentials.
    assert "KRAKEN_API_KEY" not in env
    assert "KRAKEN_API_SECRET" not in env
