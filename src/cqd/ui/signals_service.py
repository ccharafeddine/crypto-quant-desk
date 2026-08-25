"""SignalsService: schedule the pulls, run the pure engine, emit the result.

Thin and impure BY DESIGN (a timer, the network, a Qt signal). All the math is
in the pure engine (`engine/indicators`, `engine/signals`, `engine/
microstructure`) and in `evaluate_snapshot` below, which composes them without
any I/O or Qt - so the whole decision surface is unit-testable against fixed
snapshots, and the service is a thin orchestrator over it.

There is NO order path here. The service emits an advisory `TradeSetup` (or
`None`); the user still places every order through the ticket (confirmation +
limits + paper mode). Order-flow only times the entry, never the direction.

Wiring into the main window (hub + panel + alerts + analyst) lands in S5; this
step delivers the service and its pure core, tested with a fake client and a
direct `refresh_once`, plus the Strategy settings tab.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from typing import Awaitable, Callable

from PySide6.QtCore import QObject, QTimer, Signal
from pydantic import ValidationError

from cqd.data.client import make_client
from cqd.data.errors import KrakenError
from cqd.data.symbols import Symbol
from cqd.data.track_record import SignalRecord, TrackRecordLog, resolve_record
from cqd.engine.backtest import PropLimits, walk_forward
from cqd.engine.microstructure import (
    BookFeatures,
    FillEstimate,
    TimingVerdict,
    book_features,
    detect_walls,
    entry_timing,
    expected_fill,
)
from cqd.engine.signals import PairSpec, StrategyParams, TradeSetup, TrendState, evaluate_setup
from cqd.ui import settings_store as store

_log = logging.getLogger("cqd")

#: Depth levels to request - enough for the L25 imbalance in `book_features`.
DEPTH_COUNT = 25
#: How many recent imbalance readings the flow buffer keeps for `flow_delta`.
FLOW_WINDOW = 5
#: Cap the backtest window so the per-symbol replay stays snappy on the GUI loop.
BACKTEST_BARS = 400


def build_strategy_params() -> StrategyParams:
    """Assemble `StrategyParams` from persisted settings, fail-safe to v1.

    A hand-edited registry value that breaks a pydantic constraint (e.g. the
    fast<slow<trend ordering) falls back to the STRATEGY.md v1 defaults rather
    than raising into the poll loop.
    """
    try:
        return StrategyParams(
            fast=store.get_strategy_fast(),
            slow=store.get_strategy_slow(),
            trend=store.get_strategy_trend(),
            atr_len=store.get_strategy_atr_len(),
            atr_mult=store.get_strategy_atr_mult(),
            risk_pct=store.get_strategy_risk_pct(),
            variant=store.get_strategy_variant(),
        )
    except ValidationError:
        _log.warning("invalid persisted StrategyParams; using v1 defaults")
        return StrategyParams()


@dataclass(frozen=True)
class SignalsSnapshot:
    """The full engine output for one (bars, book) pull. `imbalance` is the
    current L10 depth imbalance, carried out so the service can feed the next
    call's `flow_delta`."""

    setup: TradeSetup | None
    features: BookFeatures
    fill: FillEstimate
    verdict: TimingVerdict
    flow_delta: float | None
    imbalance: float | None


def evaluate_snapshot(
    candles,
    depth,
    params: StrategyParams,
    equity_quote: float,
    pair_spec: PairSpec | None,
    *,
    prev_imbalance: float | None = None,
    start_of_day_equity: float | None = None,
    starting_equity: float | None = None,
) -> SignalsSnapshot:
    """Pure: bars + book -> setup, book features, fill estimate, timing verdict.

    The trend engine decides direction (long-only v1); order-flow only times it.
    `flow_delta` is the change in L10 imbalance versus `prev_imbalance` (the
    oldest reading in the service's recent window), or `None` on the first pull
    or a one-sided book. The fill estimate walks the book for the setup's own
    intended size (a buy of `size_quote`); with no setup there is nothing to
    fill, so it is a zero-notional estimate.
    """
    setup = (
        evaluate_setup(
            candles,
            params,
            equity_quote,
            pair_spec,
            start_of_day_equity=start_of_day_equity,
            starting_equity=starting_equity,
        )
        if pair_spec is not None
        else None
    )
    features = book_features(depth)
    walls = detect_walls(depth)
    imbalance = features.imbalance_l10
    flow_delta = (
        imbalance - prev_imbalance
        if (imbalance is not None and prev_imbalance is not None)
        else None
    )
    verdict = entry_timing(setup, features, walls, flow_delta)
    notional = setup.size_quote if setup is not None else 0.0
    fill = expected_fill(depth, "buy", notional)
    return SignalsSnapshot(
        setup=setup,
        features=features,
        fill=fill,
        verdict=verdict,
        flow_delta=flow_delta,
        imbalance=imbalance,
    )


async def _default_spec_provider(symbol: Symbol) -> PairSpec | None:
    from cqd.ui import services

    return await services.strategy_pair_spec(symbol)


class SignalsService(QObject):
    """Polls OHLC + depth for the active symbol and emits the engine result.

    Impure orchestration only: the pure `evaluate_snapshot` does the deciding.
    A per-symbol generation guard drops a slow response once a newer pull (or a
    symbol change) has started, matching the panels' overlapping-load guard.
    """

    #: (TradeSetup | None, BookFeatures, FillEstimate, TimingVerdict)
    setup_updated = Signal(object, object, object, object)
    #: (StrategyStats | None, live-summary dict | None) - backtest + live record.
    stats_updated = Signal(object, object)
    #: A human-readable failure for the panel's error state.
    error = Signal(str)

    def __init__(
        self,
        *,
        equity_provider: Callable[[], float],
        params_provider: Callable[[], StrategyParams] = build_strategy_params,
        spec_provider: Callable[[Symbol], Awaitable[PairSpec | None]] = _default_spec_provider,
        client_factory: Callable[[], object] = make_client,
        track_log: TrackRecordLog | None = None,
        poll_ms: int | None = None,
        interval_minutes: int | None = None,
        depth_count: int = DEPTH_COUNT,
        backtest_bars: int = BACKTEST_BARS,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._equity_provider = equity_provider
        self._params_provider = params_provider
        self._spec_provider = spec_provider
        self._client_factory = client_factory
        self._track_log = track_log
        self._interval = interval_minutes or store.get_strategy_timeframe_minutes()
        self._depth_count = depth_count
        self._backtest_bars = backtest_bars
        self._symbol: Symbol | None = None
        self._gen = 0
        self._flow: deque[float] = deque(maxlen=FLOW_WINDOW)
        self._pending: SignalRecord | None = None  # setup awaiting a stop/target
        self._backtest_symbol: Symbol | None = None
        self._backtest_stats = None

        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms or store.get_strategy_poll_seconds() * 1000)
        self._timer.timeout.connect(self._tick)

    # ---------- lifecycle ----------

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def set_symbol(self, symbol: Symbol) -> None:
        """Follow a new active symbol: reset per-symbol state and pull now."""
        self._symbol = symbol
        self._flow.clear()
        self._pending = None  # a pending setup belongs to the old symbol
        self._trigger()

    def refresh(self) -> None:
        """Re-run the current symbol now (panel Retry / manual refresh)."""
        self._trigger()

    # ---------- guard ----------

    def _begin(self) -> int:
        self._gen += 1
        return self._gen

    def _is_current(self, gen: int, symbol: Symbol) -> bool:
        return gen == self._gen and symbol == self._symbol

    # ---------- scheduling ----------

    def _tick(self) -> None:
        self._trigger()

    def _trigger(self) -> None:
        if self._symbol is None:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop (e.g. under a headless test that drives refresh_once)
        asyncio.ensure_future(self.refresh_once(self._symbol))

    # ---------- one poll cycle ----------

    async def refresh_once(self, symbol: Symbol) -> None:
        gen = self._begin()
        try:
            async with self._client_factory() as client:
                candles = await client.get_ohlc(symbol.rest, interval=self._interval)
                depth = await client.get_depth(symbol.rest, count=self._depth_count)
        except KrakenError as e:
            if self._is_current(gen, symbol):
                self.error.emit(f"Signals unavailable: {e}")
            return
        except Exception:  # noqa: BLE001 - a poll must never crash the app
            _log.exception("signals poll failed")
            if self._is_current(gen, symbol):
                self.error.emit("Signals update failed unexpectedly.")
            return

        if not self._is_current(gen, symbol):
            return  # a newer pull (or symbol change) superseded this one

        try:
            spec = await self._spec_provider(symbol)
        except KrakenError:
            spec = None  # unknown/unfetchable pair -> no setup, still show the book
        if not self._is_current(gen, symbol):
            return

        equity = self._equity_provider()
        # Resolve a prior pending setup against the newest bar (records on a hit).
        self._resolve_pending(candles)

        prev = self._flow[0] if self._flow else None
        snapshot = evaluate_snapshot(
            candles,
            depth,
            self._params_provider(),
            equity,
            spec,
            prev_imbalance=prev,
        )
        if snapshot.imbalance is not None:
            self._flow.append(snapshot.imbalance)
        self.setup_updated.emit(snapshot.setup, snapshot.features, snapshot.fill, snapshot.verdict)

        # Start tracking a fresh active setup; it is logged when it resolves.
        if (
            self._track_log is not None
            and self._pending is None
            and snapshot.setup is not None
            and snapshot.setup.state == TrendState.LONG_ACTIVE
        ):
            self._pending = SignalRecord.from_setup(snapshot.setup)

        self._emit_stats(candles, symbol, equity)

    # ---------- track record + backtest ----------

    def _resolve_pending(self, candles: list) -> None:
        """Resolve the pending setup against the latest bar, but only a bar AFTER
        the entry bar (strictly later timestamp - no same-bar look-ahead)."""
        if self._track_log is None or self._pending is None or not candles:
            return
        last = candles[-1]
        if int(last.time) <= self._pending.created_ts:
            return
        resolved = resolve_record(self._pending, last.high, last.low, resolved_ts=int(last.time))
        if resolved.outcome != "pending":
            self._track_log.append(resolved)
            self._pending = None

    def _emit_stats(self, candles: list, symbol: Symbol, equity: float) -> None:
        if symbol != self._backtest_symbol:  # backtest is per-symbol; cache it
            self._backtest_symbol = symbol
            self._backtest_stats = self._run_backtest(candles, equity)
        live = self._track_log.summary() if self._track_log is not None else None
        self.stats_updated.emit(self._backtest_stats, live)

    def _run_backtest(self, candles: list, equity: float):
        if equity <= 0 or len(candles) < 2:
            return None
        window = candles[-self._backtest_bars :]
        try:
            return walk_forward(window, self._params_provider(), PropLimits(starting_equity=equity))
        except Exception:  # noqa: BLE001 - a backtest error must not kill the poll
            _log.exception("signals backtest failed")
            return None


__all__ = [
    "DEPTH_COUNT",
    "FLOW_WINDOW",
    "SignalsService",
    "SignalsSnapshot",
    "build_strategy_params",
    "evaluate_snapshot",
]
