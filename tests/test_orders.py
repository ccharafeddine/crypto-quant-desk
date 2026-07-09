"""Tests for OrderService: the confirmation gate, routing, and audit trail."""

import asyncio

import pytest

from cqd.data.errors import KrakenTimeoutError, OrderRejected
from cqd.trading.audit import AuditLog, read_entries
from cqd.trading.limits import PairSpec
from cqd.trading.orders import OrderRequest, OrderService
from cqd.trading.paper import PaperBroker

SPEC = PairSpec.from_asset_pairs_entry(
    "XXBTZUSD",
    {
        "altname": "XBTUSD",
        "wsname": "XBT/USD",
        "base": "XXBT",
        "quote": "ZUSD",
        "pair_decimals": 1,
        "lot_decimals": 8,
        "ordermin": "0.00005",
        "costmin": "0.5",
    },
)


class FakeLiveClient:
    """Records calls; scripted responses/errors per method."""

    def __init__(self, log: list, *, add_error=None, open_orders=None):
        self.log = log
        self._add_error = add_error
        self._open_orders = open_orders or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def add_order(self, **kw):
        self.log.append(("add_order", kw))
        if self._add_error:
            raise self._add_error
        return {"txid": ["OLIVE-1"], "descr": {"order": "test"}}

    async def cancel_order(self, txid):
        self.log.append(("cancel_order", txid))
        return 1

    async def cancel_all(self):
        self.log.append(("cancel_all",))
        return 2

    async def get_open_orders(self):
        return self._open_orders


def _service(tmp_path, *, mode="paper", max_value=500.0, live=None):
    calls: list = []
    live = live if live is not None else FakeLiveClient(calls)
    paper = PaperBroker(tmp_path / "paper.json")
    paper.seed_if_empty({"USD": 10_000.0, "BTC": 0.5})
    svc = OrderService(
        paper=paper,
        live_client_factory=lambda: live,
        audit=AuditLog(tmp_path),
        mode_provider=lambda: mode,
        max_order_value_provider=lambda: max_value,
    )
    return svc, paper, calls


def _request(**kw) -> OrderRequest:
    args = dict(
        pair="BTC/USD",
        kraken_pair="XBTUSD",
        side="buy",
        ordertype="limit",
        volume=0.001,
        price=50_000.0,
        mark=60_000.0,
    )
    args.update(kw)
    return OrderRequest(**args)


def _audit_events(tmp_path) -> list[str]:
    files = sorted(tmp_path.glob("orders-*.jsonl"))
    events = []
    for f in files:
        events += [e["event"] for e in read_entries(f)]
    return events


# ---------- the confirmation gate ----------


def test_prepare_rejects_violations_and_mints_no_token(tmp_path) -> None:
    svc, _, _ = _service(tmp_path, max_value=10.0)
    prepared, problems = svc.prepare(_request(), SPEC)  # $50 > $10 cap
    assert prepared is None
    assert any("max order cap" in p for p in problems)


def test_submit_requires_token_and_token_is_single_use(tmp_path) -> None:
    svc, _, _ = _service(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(svc.submit("forged-token"))
    prepared, problems = svc.prepare(_request(), SPEC)
    assert problems == []
    asyncio.run(svc.submit(prepared.token))
    with pytest.raises(ValueError):
        asyncio.run(svc.submit(prepared.token))  # dead after first use


def test_conditional_close_is_validated_too(tmp_path) -> None:
    svc, _, _ = _service(tmp_path)
    req = _request(close_ordertype="stop-loss", close_price=45_000.123)  # 3 decimals > 1
    prepared, problems = svc.prepare(req, SPEC)
    assert prepared is None
    assert any(p.startswith("Conditional close:") for p in problems)


# ---------- paper routing ----------


def test_paper_market_fills_and_audits(tmp_path) -> None:
    svc, paper, _ = _service(tmp_path)
    prepared, _ = svc.prepare(_request(ordertype="market", price=None), SPEC)
    result = asyncio.run(svc.submit(prepared.token))
    assert result.mode == "paper"
    assert result.status == "filled"
    assert result.txid.startswith("PAPER-")
    assert paper.balances["BTC"] > 0.5
    assert _audit_events(tmp_path) == ["submit", "fill"]


def test_paper_rejection_audited(tmp_path) -> None:
    svc, _, _ = _service(tmp_path, max_value=1_000_000.0)
    prepared, _ = svc.prepare(_request(ordertype="market", price=None, volume=1.0), SPEC)
    result = asyncio.run(svc.submit(prepared.token))  # overlay can't afford 1 BTC
    assert result.status == "rejected"
    assert "Insufficient funds" in result.detail
    assert _audit_events(tmp_path) == ["submit", "reject"]


# ---------- live routing ----------


def test_live_submit_passes_params_and_acks(tmp_path) -> None:
    svc, _, calls = _service(tmp_path, mode="live")
    req = _request(close_ordertype="stop-loss", close_price=45_000.0)
    prepared, problems = svc.prepare(req, SPEC)
    assert problems == []
    assert prepared.mode == "live"  # confirm dialog shows LIVE
    result = asyncio.run(svc.submit(prepared.token))
    assert result.status == "open"
    assert result.txid == "OLIVE-1"
    method, kw = calls[0]
    assert method == "add_order"
    assert kw["pair"] == "XBTUSD"
    assert kw["close_ordertype"] == "stop-loss"
    assert _audit_events(tmp_path) == ["submit", "ack"]


def test_live_rejection_never_retried(tmp_path) -> None:
    calls: list = []
    live = FakeLiveClient(calls, add_error=OrderRejected("EOrder:Insufficient funds"))
    svc, _, _ = _service(tmp_path, mode="live", live=live)
    prepared, _ = svc.prepare(_request(), SPEC)
    result = asyncio.run(svc.submit(prepared.token))
    assert result.status == "rejected"
    assert len([c for c in calls if c[0] == "add_order"]) == 1  # exactly one attempt
    assert _audit_events(tmp_path) == ["submit", "reject"]


def test_live_timeout_is_unknown_not_guessed(tmp_path) -> None:
    live = FakeLiveClient([], add_error=KrakenTimeoutError("timed out"))
    svc, _, _ = _service(tmp_path, mode="live", live=live)
    prepared, _ = svc.prepare(_request(), SPEC)
    result = asyncio.run(svc.submit(prepared.token))
    assert result.status == "unknown"
    assert _audit_events(tmp_path) == ["submit", "unknown"]


def test_trailing_stop_price_sent_as_relative_offset(tmp_path) -> None:
    svc, _, calls = _service(tmp_path, mode="live")
    prepared, problems = svc.prepare(
        _request(ordertype="trailing-stop", price=500.0, side="sell"), SPEC
    )
    assert problems == []
    asyncio.run(svc.submit(prepared.token))
    _, kw = calls[0]
    assert kw["price"] == "+500"


# ---------- reconciliation ----------


def test_reconcile_finds_live_order(tmp_path) -> None:
    open_orders = {
        "OFOUND-1": {
            "vol": "0.001",
            "descr": {"pair": "XBTUSD", "type": "buy", "ordertype": "limit"},
        }
    }
    live = FakeLiveClient([], open_orders=open_orders)
    svc, _, _ = _service(tmp_path, mode="live", live=live)
    result = asyncio.run(svc.reconcile_unknown(_request()))
    assert result.status == "open"
    assert result.txid == "OFOUND-1"
    assert _audit_events(tmp_path) == ["resolve"]


def test_reconcile_not_found_means_never_landed(tmp_path) -> None:
    live = FakeLiveClient([], open_orders={})
    svc, _, _ = _service(tmp_path, mode="live", live=live)
    result = asyncio.run(svc.reconcile_unknown(_request()))
    assert result.status == "rejected"
    assert "never landed" in result.detail


# ---------- cancel routing ----------


def test_cancel_routes_by_txid_prefix_and_mode(tmp_path) -> None:
    svc, paper, calls = _service(tmp_path, mode="paper")
    prepared, _ = svc.prepare(_request(), SPEC)  # resting paper limit
    r = asyncio.run(svc.submit(prepared.token))
    out = asyncio.run(svc.cancel(r.txid))
    assert out.detail == "cancelled"
    assert paper.open_orders() == []

    svc_live, _, calls_live = _service(tmp_path, mode="live")
    asyncio.run(svc_live.cancel("OLIVE-9"))
    assert ("cancel_order", "OLIVE-9") in calls_live


def test_cancel_all_by_mode(tmp_path) -> None:
    svc, _, calls = _service(tmp_path, mode="live")
    out = asyncio.run(svc.cancel_all())
    assert ("cancel_all",) in calls
    assert "cancelled 2" in out.detail
