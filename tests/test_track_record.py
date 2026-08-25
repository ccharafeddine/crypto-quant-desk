"""Tests for the live signal track record (tmp dir, no app-data writes)."""

from __future__ import annotations

from cqd.data.track_record import (
    MIN_LIVE_SAMPLES,
    SignalRecord,
    TrackRecordLog,
    resolve_record,
    summarize_records,
)
from cqd.engine.signals import TradeSetup, TrendState


def _setup() -> TradeSetup:
    return TradeSetup(
        symbol="BTC/USD",
        direction="long",
        state=TrendState.LONG_ACTIVE,
        entry_ref=100.0,
        stop=97.0,
        size_base=1.0,
        size_quote=100.0,
        risk_quote=3.0,
        targets=[103.0, 106.0, 109.0],
        rr=3.0,
        confidence=0.6,
        rationale="test",
        created_ts=1000,
    )


def _record(**over) -> SignalRecord:
    base = dict(
        symbol="BTC/USD",
        entry=100.0,
        stop=97.0,
        target=109.0,
        size_base=1.0,
        risk_quote=3.0,
        rr=3.0,
        confidence=0.6,
        created_ts=1000,
    )
    base.update(over)
    return SignalRecord(**base)


# ---------- record + resolution ----------


def test_from_setup_uses_furthest_target() -> None:
    rec = SignalRecord.from_setup(_setup())
    assert rec.outcome == "pending"
    assert rec.target == 109.0 and rec.entry == 100.0 and rec.stop == 97.0


def test_resolve_record_win_loss_pending() -> None:
    rec = _record()
    win = resolve_record(rec, high=110.0, low=99.0, resolved_ts=2000)
    assert win.outcome == "win" and win.exit_price == 109.0
    assert win.pnl_quote == 9.0 and win.resolved_ts == 2000

    loss = resolve_record(rec, high=101.0, low=96.0, resolved_ts=2000)
    assert loss.outcome == "loss" and loss.exit_price == 97.0 and loss.pnl_quote == -3.0

    still = resolve_record(rec, high=105.0, low=98.0, resolved_ts=2000)
    assert still.outcome == "pending" and still.pnl_quote is None


def test_resolve_record_ignores_already_resolved() -> None:
    won = _record(outcome="win", exit_price=109.0, pnl_quote=9.0, resolved_ts=2000)
    assert resolve_record(won, high=96.0, low=95.0, resolved_ts=3000) is won


# ---------- summary ----------


def test_summarize_records_known() -> None:
    records = [
        _record(outcome="win", pnl_quote=9.0),
        _record(outcome="loss", pnl_quote=-3.0),
        _record(outcome="pending"),
    ]
    s = summarize_records(records)
    assert s["trades"] == 2 and s["pending"] == 1
    assert s["win_rate"] == 0.5 and s["expectancy"] == 3.0
    assert s["profit_factor"] == 3.0 and s["total_pnl"] == 6.0
    assert s["enough"] is False  # 2 < MIN_LIVE_SAMPLES


def test_summarize_records_empty_is_honest() -> None:
    s = summarize_records([])
    assert s["trades"] == 0 and s["pending"] == 0
    assert s["expectancy"] is None and s["enough"] is False
    assert s["total_pnl"] == 0.0


def test_enough_flips_at_min_samples() -> None:
    records = [_record(outcome="win", pnl_quote=1.0) for _ in range(MIN_LIVE_SAMPLES)]
    assert summarize_records(records)["enough"] is True


# ---------- persistence (append-only JSONL) ----------


def test_track_log_append_read_roundtrip(tmp_path) -> None:
    log = TrackRecordLog(tmp_path)
    log.append(_record(outcome="win", pnl_quote=9.0, exit_price=109.0, resolved_ts=2000))
    log.append(_record(outcome="loss", pnl_quote=-3.0, exit_price=97.0, resolved_ts=2100))
    records = log.read()
    assert [r.outcome for r in records] == ["win", "loss"]
    assert records[0].pnl_quote == 9.0
    assert log.summary()["trades"] == 2


def test_track_log_append_only_across_instances(tmp_path) -> None:
    TrackRecordLog(tmp_path).append(_record(outcome="win", pnl_quote=1.0))
    TrackRecordLog(tmp_path).append(_record(outcome="loss", pnl_quote=-1.0))
    assert len(TrackRecordLog(tmp_path).read()) == 2  # appended, not truncated


def test_track_log_empty_when_absent(tmp_path) -> None:
    assert TrackRecordLog(tmp_path).read() == []
    assert TrackRecordLog(tmp_path).summary()["trades"] == 0
