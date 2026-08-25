"""Tests for the signals service: the pure `evaluate_snapshot` composition, the
fail-safe params builder, and the `SignalsService` orchestration (fake client,
direct `refresh_once`, generation guard, flow buffer). The engine math itself is
covered by test_signals.py / test_microstructure.py; here we test the wiring.
"""

from __future__ import annotations

import asyncio

import pytest
from PySide6.QtWidgets import QApplication

from cqd.data.errors import KrakenError, KrakenTimeoutError
from cqd.data.symbols import Symbol
from cqd.data.normalize import Candle
from cqd.engine.signals import PairSpec, StrategyParams
import cqd.ui.signals_service as ss
from cqd.ui.signals_service import (
    SignalsService,
    build_strategy_params,
    evaluate_snapshot,
)

# A QApplication must exist before any QObject (the service owns a QTimer).
_app = QApplication.instance() or QApplication([])

PARAMS = StrategyParams(
    fast=2, slow=3, trend=5, atr_len=2, atr_mult=2.0, risk_pct=0.01, variant="ma_cross"
)
SPEC = PairSpec(symbol="BTC/USD", price_decimals=1, lot_decimals=3, ordermin=0.001)
BTC = Symbol("BTC", "USD")
ETH = Symbol("ETH", "USD")

UPTREND = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0]  # -> LONG_ACTIVE, entry 16
DOWNTREND = [16.0, 15.0, 14.0, 13.0, 12.0, 11.0, 10.0]  # -> FLAT


def _candles(closes: list[float]) -> list[Candle]:
    return [
        Candle(time=1000 + i * 60, open=c, high=c + 0.5, low=c - 0.5, close=c, volume=1.0)
        for i, c in enumerate(closes)
    ]


def _book(bid_sz: float = 5.0, ask_sz: float = 5.0) -> dict:
    return {
        "bids": [(15.9, bid_sz), (15.8, bid_sz)],
        "asks": [(16.1, ask_sz), (16.2, ask_sz)],
    }


# ---------- pure evaluate_snapshot ----------


def test_evaluate_snapshot_full_setup() -> None:
    snap = evaluate_snapshot(_candles(UPTREND), _book(), PARAMS, 10_000.0, SPEC)
    assert snap.setup is not None
    assert snap.setup.entry_ref == pytest.approx(16.0)
    assert snap.features.mid == pytest.approx(16.0)
    # The fill estimate walks the book for the setup's own intended size.
    assert snap.fill.side == "buy"
    assert snap.fill.notional == pytest.approx(snap.setup.size_quote)
    assert snap.verdict.verdict in {"GO", "WAIT", "CAUTION"}


def test_evaluate_snapshot_no_spec_means_no_setup() -> None:
    snap = evaluate_snapshot(_candles(UPTREND), _book(), PARAMS, 10_000.0, None)
    assert snap.setup is None
    assert snap.fill.notional == pytest.approx(0.0)  # nothing to fill
    assert snap.features.mid == pytest.approx(16.0)  # book still summarized


def test_evaluate_snapshot_flat_means_no_setup() -> None:
    snap = evaluate_snapshot(_candles(DOWNTREND), _book(), PARAMS, 10_000.0, SPEC)
    assert snap.setup is None


def test_evaluate_snapshot_flow_delta_vs_prev() -> None:
    snap = evaluate_snapshot(
        _candles(UPTREND), _book(bid_sz=8.0, ask_sz=2.0), PARAMS, 10_000.0, SPEC, prev_imbalance=0.0
    )
    assert snap.imbalance is not None and snap.imbalance > 0
    assert snap.flow_delta == pytest.approx(snap.imbalance - 0.0)
    # No prior reading -> no delta.
    first = evaluate_snapshot(_candles(UPTREND), _book(), PARAMS, 10_000.0, SPEC)
    assert first.flow_delta is None


def test_evaluate_snapshot_one_sided_book_is_wait() -> None:
    depth = {"bids": [(15.9, 5.0)], "asks": []}
    snap = evaluate_snapshot(_candles(UPTREND), depth, PARAMS, 10_000.0, SPEC)
    assert snap.features.mid is None
    assert snap.verdict.verdict == "WAIT"
    assert snap.flow_delta is None


# ---------- build_strategy_params (fail-safe) ----------


def test_build_strategy_params_from_settings(monkeypatch) -> None:
    monkeypatch.setattr(ss.store, "get_strategy_fast", lambda: 10)
    monkeypatch.setattr(ss.store, "get_strategy_slow", lambda: 30)
    monkeypatch.setattr(ss.store, "get_strategy_trend", lambda: 100)
    monkeypatch.setattr(ss.store, "get_strategy_atr_len", lambda: 7)
    monkeypatch.setattr(ss.store, "get_strategy_atr_mult", lambda: 3.0)
    monkeypatch.setattr(ss.store, "get_strategy_risk_pct", lambda: 0.01)
    monkeypatch.setattr(ss.store, "get_strategy_variant", lambda: "breakout")
    p = build_strategy_params()
    assert (p.fast, p.slow, p.trend, p.atr_len) == (10, 30, 100, 7)
    assert p.atr_mult == 3.0 and p.risk_pct == 0.01 and p.variant == "breakout"


def test_build_strategy_params_fails_safe_on_bad_ordering(monkeypatch) -> None:
    # fast >= slow violates the pydantic ordering -> v1 defaults, never raises.
    monkeypatch.setattr(ss.store, "get_strategy_fast", lambda: 50)
    monkeypatch.setattr(ss.store, "get_strategy_slow", lambda: 20)
    monkeypatch.setattr(ss.store, "get_strategy_trend", lambda: 200)
    monkeypatch.setattr(ss.store, "get_strategy_atr_len", lambda: 14)
    monkeypatch.setattr(ss.store, "get_strategy_atr_mult", lambda: 2.0)
    monkeypatch.setattr(ss.store, "get_strategy_risk_pct", lambda: 0.005)
    monkeypatch.setattr(ss.store, "get_strategy_variant", lambda: "ma_cross")
    p = build_strategy_params()
    assert (p.fast, p.slow, p.trend) == (20, 50, 200)  # StrategyParams() defaults


# ---------- SignalsService orchestration ----------


class _FakeClient:
    """Async-context client returning canned OHLC + depth (or raising)."""

    def __init__(self, candles, depth, *, error=None, on_ohlc=None):
        self._candles = candles
        self._depth = depth
        self._error = error
        self._on_ohlc = on_ohlc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def get_ohlc(self, pair, *, interval=1440, since=None):
        if self._on_ohlc:
            self._on_ohlc()
        if self._error:
            raise self._error
        return self._candles

    async def get_depth(self, pair, *, count=25):
        return self._depth


def _service(fake, **kw) -> SignalsService:
    async def spec_provider(_symbol):
        return SPEC

    return SignalsService(
        equity_provider=lambda: 10_000.0,
        params_provider=lambda: PARAMS,
        spec_provider=spec_provider,
        client_factory=lambda: fake,
        poll_ms=1000,
        interval_minutes=1440,
        **kw,
    )


def test_service_emits_setup_update() -> None:
    svc = _service(_FakeClient(_candles(UPTREND), _book()))
    svc._symbol = BTC
    got = []
    svc.setup_updated.connect(lambda *a: got.append(a))
    asyncio.run(svc.refresh_once(BTC))
    assert len(got) == 1
    setup, features, fill, verdict = got[0]
    assert setup is not None and features.mid == pytest.approx(16.0)
    assert fill.notional == pytest.approx(setup.size_quote)


def test_service_emits_error_on_kraken_failure() -> None:
    svc = _service(_FakeClient(_candles(UPTREND), _book(), error=KrakenTimeoutError("timed out")))
    svc._symbol = BTC
    updates, errors = [], []
    svc.setup_updated.connect(lambda *a: updates.append(a))
    svc.error.connect(lambda msg: errors.append(msg))
    asyncio.run(svc.refresh_once(BTC))
    assert updates == [] and len(errors) == 1
    assert "timed out" in errors[0]


def test_service_drops_stale_response() -> None:
    # Symbol changes mid-flight (during the OHLC await) -> the guard suppresses.
    svc = None

    def flip():
        svc._symbol = ETH

    svc = _service(_FakeClient(_candles(UPTREND), _book(), on_ohlc=flip))
    svc._symbol = BTC
    got = []
    svc.setup_updated.connect(lambda *a: got.append(a))
    asyncio.run(svc.refresh_once(BTC))
    assert got == []  # response for BTC dropped after the symbol moved to ETH


def test_service_flow_buffer_accumulates_across_cycles() -> None:
    svc = _service(_FakeClient(_candles(UPTREND), _book(bid_sz=8.0, ask_sz=2.0)))
    svc.set_symbol(BTC)  # clears buffer; no running loop so it does not self-schedule
    asyncio.run(svc.refresh_once(BTC))
    assert len(svc._flow) == 1
    asyncio.run(svc.refresh_once(BTC))
    assert len(svc._flow) == 2  # second cycle had a prior reading to diff against


def test_service_ignores_kraken_error_from_spec_provider() -> None:
    async def bad_spec(_symbol):
        raise KrakenError("AssetPairs down")

    svc = SignalsService(
        equity_provider=lambda: 10_000.0,
        params_provider=lambda: PARAMS,
        spec_provider=bad_spec,
        client_factory=lambda: _FakeClient(_candles(UPTREND), _book()),
        poll_ms=1000,
        interval_minutes=1440,
    )
    svc._symbol = BTC
    got = []
    svc.setup_updated.connect(lambda *a: got.append(a))
    asyncio.run(svc.refresh_once(BTC))
    # Spec unavailable -> no setup, but the book is still summarized and emitted.
    assert len(got) == 1
    setup, features, _fill, _verdict = got[0]
    assert setup is None and features.mid == pytest.approx(16.0)
