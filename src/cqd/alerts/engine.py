"""Alert rules and their evaluator.

Rules persist to alerts.json in the app-data dir. Evaluation is edge-triggered
with rearming: a rule fires when its condition transitions from false to true,
then stays quiet until the condition resets (crosses back), so a price sitting
above a level produces one alert, not one per tick. One-shot rules disable
themselves after firing; repeating rules rearm on reset. The engine only
DECIDES; delivery (toast, status bar) is the caller's job via FiredAlert.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

KINDS = ("price_above", "price_below", "position_pnl_pct", "portfolio_drawdown_pct")

_HISTORY_CAP = 200


class AlertRule(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    kind: Literal["price_above", "price_below", "position_pnl_pct", "portfolio_drawdown_pct"]
    symbol: str | None = None  # "BTC/USD" for price kinds
    asset: str | None = None  # bare symbol for position_pnl_pct
    threshold: float  # price level, pnl %, or drawdown % (positive magnitude)
    repeat: bool = False
    enabled: bool = True
    armed: bool = True  # edge-trigger state; rearms when the condition resets
    created: float = Field(default_factory=time.time)
    last_fired: float | None = None

    def describe(self) -> str:
        if self.kind == "price_above":
            return f"{self.symbol} above {self.threshold:,.8g}"
        if self.kind == "price_below":
            return f"{self.symbol} below {self.threshold:,.8g}"
        if self.kind == "position_pnl_pct":
            return f"{self.asset} PnL beyond ±{self.threshold:g}%"
        return f"Portfolio drawdown beyond {self.threshold:g}%"


@dataclass(frozen=True)
class FiredAlert:
    rule_id: str
    message: str
    value: float
    time: float


class AlertEngine:
    """Owns the rule list, persistence, and edge-triggered evaluation."""

    def __init__(self, store_path: Path | None = None) -> None:
        self._path = store_path
        self.rules: list[AlertRule] = []
        self.history: list[FiredAlert] = []
        self._load()

    # ---------- persistence ----------

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self.rules = [AlertRule(**r) for r in raw.get("rules", [])]
        except (OSError, ValueError, TypeError):
            try:
                self._path.rename(self._path.with_suffix(".json.bak"))
            except OSError:
                pass
            self.rules = []

    def save(self) -> None:
        if self._path is None:
            return
        payload = {"version": 1, "rules": [r.model_dump() for r in self.rules]}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        except OSError:
            pass

    # ---------- rule management ----------

    def add_rule(self, rule: AlertRule) -> None:
        self.rules.append(rule)
        self.save()

    def remove_rule(self, rule_id: str) -> None:
        self.rules = [r for r in self.rules if r.id != rule_id]
        self.save()

    # ---------- evaluation ----------

    def on_price(self, symbol: str, price: float) -> list[FiredAlert]:
        fired: list[FiredAlert] = []
        for rule in self.rules:
            if rule.symbol != symbol or rule.kind not in ("price_above", "price_below"):
                continue
            met = price >= rule.threshold if rule.kind == "price_above" else price <= rule.threshold
            hit = self._edge(rule, met, price, f"{rule.describe()} - now {price:,.8g}")
            if hit:
                fired.append(hit)
        return fired

    def on_position_pnl(self, asset: str, pnl_pct: float) -> list[FiredAlert]:
        """`pnl_pct` is signed percent vs average cost; threshold is a magnitude."""
        fired: list[FiredAlert] = []
        for rule in self.rules:
            if rule.kind != "position_pnl_pct" or rule.asset != asset:
                continue
            met = abs(pnl_pct) >= rule.threshold
            hit = self._edge(rule, met, pnl_pct, f"{asset} PnL {pnl_pct:+.1f}% vs avg cost")
            if hit:
                fired.append(hit)
        return fired

    def on_drawdown(self, drawdown: float) -> list[FiredAlert]:
        """`drawdown` is the engine's negative fraction (e.g. -0.12)."""
        magnitude = -drawdown * 100.0
        fired: list[FiredAlert] = []
        for rule in self.rules:
            if rule.kind != "portfolio_drawdown_pct":
                continue
            met = magnitude >= rule.threshold
            hit = self._edge(rule, met, magnitude, f"Portfolio drawdown {magnitude:.1f}%")
            if hit:
                fired.append(hit)
        return fired

    def _edge(self, rule: AlertRule, met: bool, value: float, message: str) -> FiredAlert | None:
        if not rule.enabled:
            return None
        if not met:
            if not rule.armed:
                rule.armed = True  # condition reset: rearm
                self.save()
            return None
        if not rule.armed:
            return None
        rule.armed = False
        rule.last_fired = time.time()
        if not rule.repeat:
            rule.enabled = False
        self.save()
        fired = FiredAlert(rule.id, message, value, rule.last_fired)
        self.history.append(fired)
        del self.history[:-_HISTORY_CAP]
        return fired
