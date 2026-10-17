"""Pins on the live-trading safety rails.

These tests exist so a refactor cannot quietly loosen a rail. Each one pins
behaviour that already holds; if a test here fails, the change that broke it
needs an explicit owner decision, not a test edit.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
from pathlib import Path

import pytest

from cqd.engine.backtest import PropLimits
from cqd.trading import paper
from cqd.trading.audit import AuditLog
from cqd.trading.limits import PairSpec, validate_order
from cqd.trading.orders import OrderRequest, OrderService
from cqd.trading.paper import PaperBroker

SRC = Path(__file__).resolve().parents[1] / "src" / "cqd"

SPEC = PairSpec(
    pair="XBTUSD",
    wsname="XBT/USD",
    base="XXBT",
    quote="ZUSD",
    price_decimals=1,
    lot_decimals=8,
    ordermin=0.0001,
    costmin=0.5,
)


class _FakeQS:
    def __init__(self, values: dict) -> None:
        self.values = values

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value) -> None:  # noqa: N802 - Qt naming
        self.values[key] = value


# ---------- defaults: paper first, small cap ----------


def test_first_run_is_paper_mode_with_a_500_dollar_cap(monkeypatch) -> None:
    from cqd.ui import settings_store as store

    monkeypatch.setattr(store, "_qs", lambda: _FakeQS({}))
    assert store.get_paper_mode() is True
    assert store.get_max_order_usd() == 500.0


def test_live_needs_paper_off_and_a_real_account(monkeypatch) -> None:
    from cqd.ui import services

    monkeypatch.setattr(services.store, "get_paper_mode", lambda: True)
    monkeypatch.setattr(services, "resolve_demo", lambda: False)
    assert services.trading_mode() == "paper"
    monkeypatch.setattr(services.store, "get_paper_mode", lambda: False)
    monkeypatch.setattr(services, "resolve_demo", lambda: True)
    assert services.trading_mode() == "paper"  # demo data never routes live
    monkeypatch.setattr(services, "resolve_demo", lambda: False)
    assert services.trading_mode() == "live"


def test_going_live_requires_typing_LIVE() -> None:  # noqa: N802
    from cqd.ui.main_window import MainWindow

    src = inspect.getsource(MainWindow._on_paper_toggled)
    assert "QInputDialog.getText" in src
    assert 'text.strip() != "LIVE"' in src
    # The only path to set_paper_mode(False) sits after the typed check.
    assert src.index('!= "LIVE"') < src.index("store.set_paper_mode(False)")


# ---------- the value cap: both modes, fail closed ----------


@pytest.mark.parametrize("ordertype", ["market", "trailing-stop"])
def test_cap_fails_closed_without_a_price(ordertype: str) -> None:
    price = 100.0 if ordertype == "trailing-stop" else None
    problems = validate_order(
        SPEC,
        side="buy",
        ordertype=ordertype,
        volume=0.01,
        price=price,
        mark=None,
        max_order_value=500.0,
    )
    assert any("cannot be estimated" in p for p in problems)


def _service(tmp_path, mode: str) -> tuple[OrderService, list]:
    calls: list = []

    class _Live:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def add_order(self, **kw):
            calls.append(kw)
            return {"txid": ["OLIVE-1"], "descr": {"order": "x"}}

    broker = PaperBroker(tmp_path / "paper.json")
    broker.seed_if_empty({"USD": 1_000_000.0})
    svc = OrderService(
        paper=broker,
        live_client_factory=_Live,
        audit=AuditLog(tmp_path),
        mode_provider=lambda: mode,
        max_order_value_provider=lambda: 500.0,
    )
    return svc, calls


@pytest.mark.parametrize("mode", ["paper", "live"])
def test_cap_applies_in_both_modes_and_mints_no_token(tmp_path, mode: str) -> None:
    svc, calls = _service(tmp_path, mode)
    big = OrderRequest(
        pair="BTC/USD",
        kraken_pair="XBTUSD",
        side="buy",
        ordertype="limit",
        volume=0.1,
        price=60_000.0,
        mark=60_000.0,
    )
    prepared, problems = svc.prepare(big, SPEC)
    assert prepared is None and any("max order cap" in p for p in problems)
    assert svc._pending == {}
    assert calls == []


def test_no_submit_without_a_prepare_token(tmp_path) -> None:
    svc, calls = _service(tmp_path, "live")
    with pytest.raises(ValueError):
        asyncio.run(svc.submit("not-a-token"))
    assert calls == []
    # And the public API has no other way to send.
    public = {n for n in dir(OrderService) if not n.startswith("_")}
    assert public == {"prepare", "submit", "reconcile_unknown", "cancel", "cancel_all"}


# ---------- structural rails ----------


def _calls_to_private(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_private"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            out.add(node.args[0].value)
    return out


def test_private_endpoints_are_an_allowlist_without_withdrawals() -> None:
    used = _calls_to_private(SRC / "data" / "rest.py")
    assert used == {
        "Balance",
        "TradesHistory",
        "Ledgers",
        "OpenOrders",
        "GetWebSocketsToken",
        "AddOrder",
        "EditOrder",
        "CancelOrder",
        "CancelAll",
        "QueryOrders",
    }
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for word in ("WithdrawFunds", "WithdrawInfo", "WalletTransfer", '"Withdraw"'):
            assert word not in text, f"{word} in {path}"


def _imports(path: Path) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
        elif isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
    return mods


def test_signals_have_no_path_to_orders() -> None:
    for rel in (
        "engine/signals.py",
        "engine/backtest.py",
        "ui/signals_service.py",
        "ui/panels/signals.py",
        "data/track_record.py",
    ):
        mods = _imports(SRC / rel)
        assert not any(m.startswith("cqd.trading") for m in mods), (rel, mods)


def test_engine_never_imports_trading_or_io() -> None:
    for path in (SRC / "engine").rglob("*.py"):
        mods = _imports(path)
        # cqd.data.normalize is the pure Candle model; nothing else from data/.
        bad = [
            m
            for m in mods
            if m.startswith(("cqd.trading", "cqd.data", "cqd.ui", "PySide6"))
            and m != "cqd.data.normalize"
        ]
        assert not bad, (path.name, bad)


def test_audit_entries_carry_no_key_material(tmp_path) -> None:
    svc, _ = _service(tmp_path, "paper")
    req = OrderRequest(
        pair="BTC/USD",
        kraken_pair="XBTUSD",
        side="buy",
        ordertype="market",
        volume=0.001,
        mark=60_000.0,
    )
    prepared, problems = svc.prepare(req, SPEC)
    assert not problems
    asyncio.run(svc.submit(prepared.token))
    text = "".join(p.read_text() for p in tmp_path.glob("orders-*.jsonl"))
    assert text
    for line in text.splitlines():
        keys = json.dumps(json.loads(line)).lower()
        for secret in ("api_key", "api_secret", "apikey", "secret", "nonce", "otp", "signature"):
            assert secret not in keys


# ---------- numeric limits: values pinned ----------


def test_prop_limits_and_paper_costs_are_unchanged() -> None:
    limits = PropLimits(starting_equity=1.0)
    assert (limits.daily_loss, limits.total_loss, limits.profit_target) == (0.03, 0.05, 0.08)
    assert (paper.TAKER_FEE, paper.MAKER_FEE, paper.SLIPPAGE_BPS) == (0.0040, 0.0025, 5.0)


# ---------- send-time re-check (tighten only) ----------


def _mutable_service(tmp_path, state: dict):
    calls: list[dict] = []

    class _Live:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def add_order(self, **kw):
            calls.append(kw)
            return {"txid": ["OLIVE-1"], "descr": {"order": "x"}}

    broker = PaperBroker(tmp_path / "paper.json")
    broker.seed_if_empty({"USD": 1_000_000.0})
    svc = OrderService(
        paper=broker,
        live_client_factory=_Live,
        audit=AuditLog(tmp_path),
        mode_provider=lambda: state["mode"],
        max_order_value_provider=lambda: state["cap"],
    )
    return svc, calls


_SMALL = dict(
    pair="BTC/USD",
    kraken_pair="XBTUSD",
    side="buy",
    ordertype="limit",
    volume=0.004,
    price=60_000.0,
    mark=60_000.0,
)  # $240


@pytest.mark.parametrize("mode", ["paper", "live"])
def test_cap_lowered_after_confirmation_rejects_at_send(tmp_path, mode: str) -> None:
    state = {"mode": mode, "cap": 500.0}
    svc, calls = _mutable_service(tmp_path, state)
    prepared, problems = svc.prepare(OrderRequest(**_SMALL), SPEC)
    assert prepared is not None and problems == []
    state["cap"] = 100.0  # user tightens the cap while the dialog is open
    result = asyncio.run(svc.submit(prepared.token))
    assert result.status == "rejected" and "max order cap" in result.detail
    assert calls == []
    (log,) = tmp_path.glob("orders-*.jsonl")
    events = [json.loads(x)["event"] for x in log.read_text().splitlines()]
    assert events[-1] == "reject"


def test_mode_switch_after_confirmation_rejects_at_send(tmp_path) -> None:
    state = {"mode": "paper", "cap": 500.0}
    svc, calls = _mutable_service(tmp_path, state)
    prepared, _ = svc.prepare(OrderRequest(**_SMALL), SPEC)
    state["mode"] = "live"  # confirmed as paper, must never go out live
    result = asyncio.run(svc.submit(prepared.token))
    assert result.status == "rejected" and "Mode changed" in result.detail
    assert calls == []
    with pytest.raises(ValueError):  # the token is spent either way
        asyncio.run(svc.submit(prepared.token))


def test_unchanged_rails_still_send(tmp_path) -> None:
    state = {"mode": "live", "cap": 500.0}
    svc, calls = _mutable_service(tmp_path, state)
    prepared, _ = svc.prepare(OrderRequest(**_SMALL), SPEC)
    state["cap"] = 1_000.0  # loosening after confirmation changes nothing
    result = asyncio.run(svc.submit(prepared.token))
    assert result.status != "rejected" and len(calls) == 1
