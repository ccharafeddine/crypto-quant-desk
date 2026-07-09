"""Append-only order audit log.

Every order attempt and outcome - submitted, acknowledged, rejected, filled,
cancelled, edited, unknown, resolved - becomes one JSON line in a month-rolled
file under the app-data audit directory. The log is the ground truth for
"what did this app (or a future autotrader) do with money": entries are never
edited or deleted, writes are serialized, and no entry ever contains key
material (requests carry order parameters only).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cqd.data.paths import audit_dir

try:  # single source of truth for the version stamp
    from importlib.metadata import version

    _APP_VERSION = version("crypto-quant-desk")
except Exception:  # pragma: no cover - metadata missing in odd environments
    _APP_VERSION = "unknown"

#: The only event vocabulary; anything else is a programming error.
EVENTS = frozenset({"submit", "ack", "reject", "fill", "cancel", "edit", "unknown", "resolve"})


class AuditLog:
    """Serialized JSONL writer, one file per month (orders-YYYYMM.jsonl)."""

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory if directory is not None else audit_dir()
        self._lock = asyncio.Lock()

    def _file_for(self, now: datetime) -> Path:
        return self._dir / f"orders-{now:%Y%m}.jsonl"

    async def record(
        self,
        event: str,
        *,
        mode: str,
        request: dict[str, Any] | None = None,
        response: dict[str, Any] | None = None,
        error: str | None = None,
        order_value_usd: float | None = None,
        source: str = "ui",
    ) -> dict[str, Any]:
        """Append one entry; returns the entry as written (for the UI/tests)."""
        if event not in EVENTS:
            raise ValueError(f"unknown audit event '{event}'")
        if mode not in ("paper", "live"):
            raise ValueError(f"unknown audit mode '{mode}'")
        now = datetime.now(timezone.utc)
        entry: dict[str, Any] = {
            "ts": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "event": event,
            "mode": mode,
            "source": source,
            "request": request,
            "response": response,
            "error": error,
            "order_value_usd": order_value_usd,
            "app_version": _APP_VERSION,
        }
        line = json.dumps(entry, separators=(",", ":"), default=str)
        async with self._lock:
            self._dir.mkdir(parents=True, exist_ok=True)
            with self._file_for(now).open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        return entry


def read_entries(path: Path) -> list[dict[str, Any]]:
    """Parse one audit file back into entries (viewer/tests helper)."""
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out
