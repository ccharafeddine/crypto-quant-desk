"""Kraken WebSocket v2 client: live ticks and own-order events.

One client per socket: public (ticker) at wss://ws.kraken.com/v2 and private
(executions) at wss://ws-auth.kraken.com/v2, authenticated with a short-lived
token from the REST GetWebSocketsToken endpoint. Message parsing is a pure
function; the run loop owns reconnection with exponential backoff, a heartbeat
watchdog (silence -> "delayed" -> forced reconnect), and resubscription after
every reconnect. Kraken v2 symbols are already the engine's slash form
("BTC/USD"), so no translation layer is needed.

The connector is injectable, so the whole lifecycle is tested against scripted
fake connections - no network in tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

log = logging.getLogger("cqd.ws")

PUBLIC_WS_URL = "wss://ws.kraken.com/v2"
PRIVATE_WS_URL = "wss://ws-auth.kraken.com/v2"

#: Consecutive watchdog timeouts before the connection is presumed dead.
_TIMEOUTS_BEFORE_RECONNECT = 3


# ---------- pure message parsing ----------


@dataclass(frozen=True)
class Tick:
    symbol: str  # "BTC/USD"
    last: float


@dataclass(frozen=True)
class ExecutionEvent:
    data: dict[str, Any]


def parse_message(raw: str | bytes) -> list[object]:
    """One wire frame -> zero or more typed events. Never raises."""
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(msg, dict):
        return []
    channel = msg.get("channel")
    if channel == "ticker":
        out: list[object] = []
        for item in msg.get("data") or []:
            symbol = item.get("symbol")
            last = item.get("last")
            if symbol and last is not None:
                try:
                    out.append(Tick(str(symbol), float(last)))
                except (TypeError, ValueError):
                    continue
        return out
    if channel == "executions":
        return [ExecutionEvent(dict(item)) for item in (msg.get("data") or [])]
    # heartbeat / status / method acks reset the watchdog implicitly (any
    # frame does); they carry no events for consumers.
    return []


# ---------- client ----------


async def _default_connector(url: str) -> Any:
    import websockets

    return await websockets.connect(url, ping_interval=20, ping_timeout=10)


class KrakenWSClient:
    """Reconnecting subscriber for one Kraken WS v2 socket."""

    def __init__(
        self,
        url: str = PUBLIC_WS_URL,
        *,
        token_provider: Callable[[], Awaitable[str]] | None = None,
        connector: Callable[[str], Awaitable[Any]] | None = None,
        heartbeat_timeout: float = 10.0,
        max_backoff: float = 60.0,
        initial_backoff: float = 1.0,
    ) -> None:
        self._url = url
        self._token_provider = token_provider
        self._connector = connector or _default_connector
        self._hb_timeout = heartbeat_timeout
        self._max_backoff = max_backoff
        self._initial_backoff = initial_backoff
        self._symbols: set[str] = set()
        self._ws: Any = None
        self._stopping = False
        self._state = "offline"
        self.on_tick: list[Callable[[Tick], None]] = []
        self.on_execution: list[Callable[[ExecutionEvent], None]] = []
        self.on_state: list[Callable[[str], None]] = []

    # ---------- subscriptions ----------

    def subscribe_ticker(self, symbols: list[str]) -> None:
        """Add symbols; takes effect now if connected, and after reconnects."""
        new = [s for s in symbols if s not in self._symbols]
        self._symbols.update(new)
        if new and self._ws is not None:
            asyncio.ensure_future(self._send_ticker_sub(self._ws, new))

    async def _send_ticker_sub(self, ws: Any, symbols: list[str]) -> None:
        if not symbols:
            return
        with contextlib.suppress(Exception):  # a dead ws just reconnects
            await ws.send(
                json.dumps(
                    {
                        "method": "subscribe",
                        "params": {"channel": "ticker", "symbol": sorted(symbols)},
                    }
                )
            )

    async def _send_subscriptions(self, ws: Any) -> None:
        await self._send_ticker_sub(ws, sorted(self._symbols))
        if self._token_provider is not None:
            token = await self._token_provider()
            await ws.send(
                json.dumps(
                    {
                        "method": "subscribe",
                        "params": {"channel": "executions", "token": token},
                    }
                )
            )

    # ---------- lifecycle ----------

    def stop(self) -> None:
        self._stopping = True
        ws, self._ws = self._ws, None
        if ws is not None:
            asyncio.ensure_future(self._close(ws))

    @staticmethod
    async def _close(ws: Any) -> None:
        with contextlib.suppress(Exception):
            await ws.close()

    def _set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        for cb in self.on_state:
            try:
                cb(state)
            except Exception:  # noqa: BLE001 - a bad callback must not kill the loop
                log.exception("state callback failed")

    def _dispatch(self, events: list[object]) -> None:
        for event in events:
            callbacks: list = []
            if isinstance(event, Tick):
                callbacks = self.on_tick
            elif isinstance(event, ExecutionEvent):
                callbacks = self.on_execution
            for cb in callbacks:
                try:
                    cb(event)
                except Exception:  # noqa: BLE001
                    log.exception("event callback failed")

    async def run(self) -> None:
        """Connect-subscribe-listen forever, reconnecting until stop()/cancel."""
        try:
            await self._run_loop()
        finally:
            self._set_state("offline")  # holds even when the task is cancelled

    async def _run_loop(self) -> None:
        backoff = self._initial_backoff
        while not self._stopping:
            try:
                ws = await self._connector(self._url)
            except Exception as e:  # noqa: BLE001 - network failures are routine
                log.info("ws connect failed: %s", e)
                self._set_state("offline")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._max_backoff)
                continue
            try:
                self._ws = ws
                await self._send_subscriptions(ws)
                self._set_state("live")
                backoff = self._initial_backoff
                timeouts = 0
                while not self._stopping:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), self._hb_timeout)
                    except asyncio.TimeoutError:
                        timeouts += 1
                        self._set_state("delayed")
                        if timeouts >= _TIMEOUTS_BEFORE_RECONNECT:
                            log.info("ws silent too long; reconnecting")
                            break
                        continue
                    timeouts = 0
                    self._set_state("live")
                    self._dispatch(parse_message(raw))
            except Exception as e:  # noqa: BLE001 - includes normal closes
                log.info("ws connection ended: %s", e)
            finally:
                self._ws = None
                await self._close(ws)
            if not self._stopping:
                self._set_state("offline")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._max_backoff)
