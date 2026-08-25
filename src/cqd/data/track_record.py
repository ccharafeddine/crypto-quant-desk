"""Live track record: log resolved signal outcomes, summarize live expectancy.

The counterpart to the backtest (`engine/backtest.py`). Where the backtest
replays history, this records what the strategy's LIVE signals actually did, so
the panel can show backtest and live stats side by side - the honest "measured,
not promised" posture from SIGNALS_PLAN.md §0.

Append-only JSONL, mirroring `trading/audit.py`: one line per resolved setup,
never edited or deleted. Pending setups are held in memory by the service (S5)
and a line is appended only once the stop or target is hit, so the file is a
clean history of realized outcomes. `summarize_records` reuses the backtest's
`trade_summary`, so live and backtest expectancy mean exactly the same thing.

This module does file I/O, so it lives in the data layer, not the pure engine;
it imports the pure resolver/summarizer FROM the engine (data -> engine is fine)
so the two never diverge.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from cqd.data.paths import app_data_dir
from cqd.engine.backtest import classify_exit, trade_summary
from cqd.engine.signals import TradeSetup

#: Below this many resolved trades the live stats are shown as "not yet
#: meaningful" - a handful of samples is noise, not a track record.
MIN_LIVE_SAMPLES = 20


class SignalRecord(BaseModel):
    """One emitted setup and, once it resolves, its realized outcome."""

    symbol: str
    direction: Literal["long", "short"] = "long"
    entry: float
    stop: float
    target: float
    size_base: float
    risk_quote: float
    rr: float
    confidence: float
    created_ts: int
    outcome: Literal["pending", "win", "loss"] = "pending"
    exit_price: float | None = None
    pnl_quote: float | None = None
    resolved_ts: int | None = None

    @classmethod
    def from_setup(cls, setup: TradeSetup, *, target_index: int = -1) -> SignalRecord:
        """Build a pending record from a live `TradeSetup` (target = furthest R)."""
        return cls(
            symbol=setup.symbol,
            direction=setup.direction,
            entry=setup.entry_ref,
            stop=setup.stop,
            target=setup.targets[target_index] if setup.targets else setup.entry_ref,
            size_base=setup.size_base,
            risk_quote=setup.risk_quote,
            rr=setup.rr,
            confidence=setup.confidence,
            created_ts=setup.created_ts,
        )


def resolve_record(
    record: SignalRecord, high: float, low: float, *, resolved_ts: int
) -> SignalRecord:
    """Resolve a pending record against a later bar's range.

    Returns a new resolved record (outcome/exit/pnl set) if the stop or target
    traded, else the record unchanged (still pending). Uses the same stop-first
    `classify_exit` as the backtest, so live and backtest classify identically.
    `pnl_quote` is frictionless (fees/slippage are the execution layer's read).
    """
    if record.outcome != "pending":
        return record
    verdict, exit_price = classify_exit(
        record.direction, record.entry, record.stop, record.target, high, low
    )
    if exit_price is None:
        return record
    return record.model_copy(
        update={
            "outcome": verdict,
            "exit_price": exit_price,
            "pnl_quote": record.size_base * (exit_price - record.entry),
            "resolved_ts": resolved_ts,
        }
    )


def summarize_records(records: list[SignalRecord]) -> dict:
    """Rolling live stats over RESOLVED records (pending ones are counted only).

    Adds `pending` and `enough` (>= `MIN_LIVE_SAMPLES` resolved) to the shared
    `trade_summary` shape so the panel can render an honest empty/low-sample
    state instead of a meaningless one-trade "100% win rate".
    """
    resolved = [r for r in records if r.outcome in ("win", "loss") and r.pnl_quote is not None]
    pending = sum(1 for r in records if r.outcome == "pending")
    summary = trade_summary([r.pnl_quote for r in resolved])
    summary["pending"] = pending
    summary["total_pnl"] = sum(r.pnl_quote for r in resolved) if resolved else 0.0
    summary["enough"] = summary["trades"] >= MIN_LIVE_SAMPLES
    return summary


class TrackRecordLog:
    """Append-only JSONL of resolved signal outcomes (signals-track.jsonl)."""

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory if directory is not None else app_data_dir()
        self._file = self._dir / "signals-track.jsonl"

    def append(self, record: SignalRecord) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record.model_dump(), separators=(",", ":"), default=str)
        with self._file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def read(self) -> list[SignalRecord]:
        if not self._file.exists():
            return []
        out: list[SignalRecord] = []
        for line in self._file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(SignalRecord.model_validate_json(line))
        return out

    def summary(self) -> dict:
        return summarize_records(self.read())


__all__ = [
    "MIN_LIVE_SAMPLES",
    "SignalRecord",
    "TrackRecordLog",
    "resolve_record",
    "summarize_records",
]
