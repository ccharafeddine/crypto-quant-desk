"""Tests for the WebSocket client (scripted fake connections, no network)."""

import asyncio
import json

from cqd.data.ws import ExecutionEvent, KrakenWSClient, Tick, parse_message

# ---------- pure parsing ----------


def test_parse_ticker_update() -> None:
    raw = json.dumps(
        {
            "channel": "ticker",
            "type": "update",
            "data": [{"symbol": "BTC/USD", "last": 62814.4, "bid": 62814.3}],
        }
    )
    events = parse_message(raw)
    assert events == [Tick("BTC/USD", 62814.4)]


def test_parse_executions() -> None:
    raw = json.dumps(
        {
            "channel": "executions",
            "type": "update",
            "data": [{"order_id": "OABC-1", "exec_type": "canceled"}],
        }
    )
    events = parse_message(raw)
    assert isinstance(events[0], ExecutionEvent)
    assert events[0].data["order_id"] == "OABC-1"


def test_parse_noise_never_raises() -> None:
    assert parse_message("not json") == []
    assert parse_message(json.dumps({"channel": "heartbeat"})) == []
    assert parse_message(json.dumps({"method": "subscribe", "success": True})) == []
    assert parse_message(json.dumps({"channel": "ticker", "data": [{"symbol": "X"}]})) == []
    assert parse_message(json.dumps(["list"])) == []


# ---------- fake connection machinery ----------


class _Closed(Exception):
    pass


class FakeWS:
    """Scripted connection: strings are frames, floats are silences."""

    def __init__(self, script):
        self.script = list(script)
        self.sent: list[dict] = []
        self.closed = False

    async def send(self, msg: str) -> None:
        self.sent.append(json.loads(msg))

    async def recv(self) -> str:
        while self.script:
            item = self.script.pop(0)
            if isinstance(item, float):
                await asyncio.sleep(item)
                continue
            return item
        raise _Closed("script exhausted")

    async def close(self) -> None:
        self.closed = True


def _connector(connections: list[FakeWS]):
    calls = {"n": 0}

    async def connect(url: str):
        if not connections:
            # No more scripted connections: park forever so backoff dominates.
            await asyncio.sleep(3600)
        calls["n"] += 1
        return connections.pop(0)

    return connect, calls


def _run_briefly(client: KrakenWSClient, seconds: float) -> None:
    async def main():
        task = asyncio.ensure_future(client.run())
        await asyncio.sleep(seconds)
        client.stop()
        # The loop may be parked in the connector or a backoff sleep; callers
        # cancel it like the app's shutdown path does.
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(main())


_TICK = json.dumps(
    {"channel": "ticker", "type": "update", "data": [{"symbol": "BTC/USD", "last": 60000.0}]}
)


# ---------- lifecycle ----------


def test_subscribes_and_dispatches_ticks() -> None:
    ws = FakeWS([_TICK, 10.0])
    connect, calls = _connector([ws])
    client = KrakenWSClient(connector=connect, heartbeat_timeout=1.0)
    client.subscribe_ticker(["BTC/USD", "ETH/USD"])

    ticks: list[Tick] = []
    states: list[str] = []
    client.on_tick.append(ticks.append)
    client.on_state.append(states.append)

    _run_briefly(client, 0.2)

    assert calls["n"] == 1
    sub = ws.sent[0]
    assert sub["method"] == "subscribe"
    assert sub["params"]["channel"] == "ticker"
    assert sub["params"]["symbol"] == ["BTC/USD", "ETH/USD"]
    assert ticks == [Tick("BTC/USD", 60000.0)]
    assert states[0] == "live"
    assert states[-1] == "offline"  # after stop()


def test_silence_goes_delayed_then_reconnects_and_resubscribes() -> None:
    # First connection: one tick, then silence far beyond 3 watchdog windows.
    ws1 = FakeWS([_TICK, 10.0])
    ws2 = FakeWS([_TICK, 10.0])
    connect, calls = _connector([ws1, ws2])
    client = KrakenWSClient(connector=connect, heartbeat_timeout=0.03, initial_backoff=0.01)
    client.subscribe_ticker(["BTC/USD"])
    states: list[str] = []
    ticks: list[Tick] = []
    client.on_state.append(states.append)
    client.on_tick.append(ticks.append)

    _run_briefly(client, 0.5)

    assert calls["n"] == 2  # watchdog forced a reconnect
    assert "delayed" in states
    assert ws2.sent and ws2.sent[0]["params"]["channel"] == "ticker"  # resubscribed
    assert len(ticks) == 2  # one tick per connection


def test_connection_drop_reconnects_with_backoff() -> None:
    ws1 = FakeWS([_TICK])  # script exhausts -> recv raises -> drop
    ws2 = FakeWS([_TICK, 10.0])
    connect, calls = _connector([ws1, ws2])
    client = KrakenWSClient(connector=connect, heartbeat_timeout=1.0, initial_backoff=0.01)
    client.subscribe_ticker(["BTC/USD"])
    states: list[str] = []
    client.on_state.append(states.append)

    _run_briefly(client, 0.3)

    assert calls["n"] == 2
    assert "offline" in states  # between the two connections
    assert states.count("live") >= 2


def test_private_subscription_uses_token() -> None:
    ws = FakeWS([10.0])
    connect, _ = _connector([ws])

    async def token_provider() -> str:
        return "WS-TOKEN-123"

    client = KrakenWSClient(connector=connect, token_provider=token_provider, heartbeat_timeout=1.0)
    executions: list[ExecutionEvent] = []
    client.on_execution.append(executions.append)
    ws.script.insert(
        0,
        json.dumps({"channel": "executions", "data": [{"order_id": "O1", "exec_type": "new"}]}),
    )

    _run_briefly(client, 0.2)

    exec_sub = next(s for s in ws.sent if s["params"]["channel"] == "executions")
    assert exec_sub["params"]["token"] == "WS-TOKEN-123"
    assert executions and executions[0].data["order_id"] == "O1"


def test_late_subscribe_sends_on_live_connection() -> None:
    ws = FakeWS([10.0])
    connect, _ = _connector([ws])
    client = KrakenWSClient(connector=connect, heartbeat_timeout=1.0)

    async def main():
        task = asyncio.ensure_future(client.run())
        await asyncio.sleep(0.05)
        client.subscribe_ticker(["SOL/USD"])
        await asyncio.sleep(0.05)
        client.stop()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 2.0)

    asyncio.run(main())

    subs = [s for s in ws.sent if s["params"]["channel"] == "ticker"]
    assert any(s["params"]["symbol"] == ["SOL/USD"] for s in subs)


def test_bad_callback_does_not_kill_the_loop() -> None:
    ws = FakeWS([_TICK, _TICK, 10.0])
    connect, _ = _connector([ws])
    client = KrakenWSClient(connector=connect, heartbeat_timeout=1.0)
    client.subscribe_ticker(["BTC/USD"])
    good: list[Tick] = []
    client.on_tick.append(lambda t: (_ for _ in ()).throw(RuntimeError("boom")))
    client.on_tick.append(good.append)

    _run_briefly(client, 0.2)

    assert len(good) == 2  # both ticks reached the good callback
