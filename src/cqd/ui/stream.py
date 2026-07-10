"""StreamBridge: WebSocket events -> Qt signals.

One bridge per app. It owns the public ticker socket (always) and the private
executions socket (only with a real keyed account), and re-emits their events
as Qt signals. Under qasync everything runs on the GUI thread, so slots may
touch widgets directly. The combined state signal reports the worst of the two
sockets, and panels ask for symbols on demand (`ensure_symbols`), which also
survives reconnects because subscriptions live in the WS client.
"""

from __future__ import annotations

import asyncio

from PySide6.QtCore import QObject, Signal

from cqd.data.client import resolve_demo
from cqd.data.credentials import kraken_keys_present
from cqd.data.rest import KrakenRESTClient
from cqd.data.ws import PRIVATE_WS_URL, ExecutionEvent, KrakenWSClient, Tick, Trade

_STATE_RANK = {"live": 0, "delayed": 1, "offline": 2}


async def _fetch_ws_token() -> str:
    async with KrakenRESTClient() as client:
        return await client.get_ws_token()


class StreamBridge(QObject):
    tick = Signal(str, float)  # symbol ("BTC/USD"), last price
    trade = Signal(object)  # one public Trade (for the time & sales tape)
    state_changed = Signal(str)  # "live" | "delayed" | "offline"
    execution = Signal(dict)  # one own-order event (raw v2 payload)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._public = KrakenWSClient()
        self._public.on_tick.append(lambda t: self._on_tick(t))
        self._public.on_trade.append(lambda t: self._on_trade(t))
        self._public.on_state.append(lambda s: self._on_state("public", s))
        self._private: KrakenWSClient | None = None
        self._states = {"public": "offline", "private": "live"}  # private optional
        self._tasks: list[asyncio.Task] = []

    # ---------- lifecycle ----------

    def start(self) -> None:
        """Start the public stream; add the private one for real accounts."""
        self._tasks.append(asyncio.ensure_future(self._public.run()))
        if kraken_keys_present() and not resolve_demo():
            self._private = KrakenWSClient(PRIVATE_WS_URL, token_provider=_fetch_ws_token)
            self._private.on_execution.append(lambda e: self._on_execution(e))
            self._private.on_state.append(lambda s: self._on_state("private", s))
            self._states["private"] = "offline"
            self._tasks.append(asyncio.ensure_future(self._private.run()))

    def stop(self) -> None:
        self._public.stop()
        if self._private is not None:
            self._private.stop()
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

    def ensure_symbols(self, symbols: list[str]) -> None:
        """Subscribe live ticks for `symbols` (idempotent, reconnect-safe)."""
        self._public.subscribe_ticker([s for s in symbols if "/" in s])

    def ensure_trades(self, symbols: list[str]) -> None:
        """Subscribe the public trade channel for the tape (idempotent)."""
        self._public.subscribe_trade([s for s in symbols if "/" in s])

    def drop_trades(self, symbols: list[str]) -> None:
        self._public.unsubscribe_trade([s for s in symbols if "/" in s])

    # ---------- fan-out ----------

    def _on_tick(self, t: Tick) -> None:
        self.tick.emit(t.symbol, t.last)

    def _on_trade(self, t: Trade) -> None:
        self.trade.emit(t)

    def _on_execution(self, e: ExecutionEvent) -> None:
        self.execution.emit(e.data)

    def _on_state(self, which: str, state: str) -> None:
        self._states[which] = state
        worst = max(self._states.values(), key=lambda s: _STATE_RANK.get(s, 2))
        self.state_changed.emit(worst)
