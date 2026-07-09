"""Tests for the append-only order audit log (tmp dir, no app-data writes)."""

import asyncio
import json
from datetime import datetime, timezone

import pytest

from cqd.trading.audit import AuditLog, read_entries


def _month_file(tmp_path):
    return tmp_path / f"orders-{datetime.now(timezone.utc):%Y%m}.jsonl"


def test_record_appends_json_lines(tmp_path) -> None:
    log = AuditLog(tmp_path)
    req = {"pair": "BTC/USD", "side": "buy", "ordertype": "limit", "volume": 0.001}

    async def run():
        await log.record("submit", mode="paper", request=req, order_value_usd=60.0)
        await log.record("reject", mode="paper", request=req, error="EOrder:Insufficient funds")

    asyncio.run(run())

    entries = read_entries(_month_file(tmp_path))
    assert len(entries) == 2
    assert entries[0]["event"] == "submit"
    assert entries[0]["mode"] == "paper"
    assert entries[0]["source"] == "ui"
    assert entries[0]["request"]["pair"] == "BTC/USD"
    assert entries[0]["order_value_usd"] == 60.0
    assert entries[0]["ts"].endswith("Z")
    assert entries[1]["event"] == "reject"
    assert entries[1]["error"] == "EOrder:Insufficient funds"


def test_append_only_across_instances(tmp_path) -> None:
    # A new AuditLog over the same directory appends, never truncates.
    async def run():
        await AuditLog(tmp_path).record("submit", mode="live", request={})
        await AuditLog(tmp_path).record("ack", mode="live", response={"txid": ["X"]})

    asyncio.run(run())
    entries = read_entries(_month_file(tmp_path))
    assert [e["event"] for e in entries] == ["submit", "ack"]


def test_concurrent_writes_all_land_as_valid_lines(tmp_path) -> None:
    log = AuditLog(tmp_path)

    async def run():
        await asyncio.gather(
            *(log.record("submit", mode="paper", request={"n": i}) for i in range(25))
        )

    asyncio.run(run())
    raw = _month_file(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(raw) == 25
    ns = sorted(json.loads(line)["request"]["n"] for line in raw)
    assert ns == list(range(25))  # every write landed, none torn


def test_unknown_event_or_mode_rejected(tmp_path) -> None:
    log = AuditLog(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(log.record("yolo", mode="paper"))
    with pytest.raises(ValueError):
        asyncio.run(log.record("submit", mode="real"))


def test_autotrader_source_field(tmp_path) -> None:
    log = AuditLog(tmp_path)
    asyncio.run(log.record("submit", mode="paper", request={}, source="autotrader"))
    assert read_entries(_month_file(tmp_path))[0]["source"] == "autotrader"
