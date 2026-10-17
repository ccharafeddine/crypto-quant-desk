"""Header and KPI-strip numbers: one pure model fed by three panels.

The Positions, Performance and Risk cards already compute everything the
header strip and the KPI strip show; they publish their results here instead
of the strip re-fetching. `KpiState` is a plain dataclass and `build_kpis` /
`header_values` are pure, so every label, unit and colour rule is testable
without Qt. `KpiHub` is the thin QObject that merges updates and notifies.

Definitions (also in the README methodology):
- NAV: balances x live marks (USD and USD-pegged stables at 1.00).
- Day P&L: NAV now minus the account value at the last closed daily bar
  (00:00 UTC), less deposits/withdrawals booked since then.
- Return 1Y: time-weighted (flows stripped) over the last 365 closed days.
- Max drawdown: on the time-weighted index, including the starting value.
- VaR 95%: 1-day historical, ex-ante (current weights), in USD of NAV.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from PySide6.QtCore import QObject, Signal

from cqd.ui.format import format_pct, format_usd, tone


@dataclass(frozen=True)
class Kpi:
    label: str
    value: str
    sub: str = ""
    tone: str = ""  # "pos" / "neg" / "" - P&L colouring only


@dataclass(frozen=True)
class KpiState:
    nav: float | None = None
    holdings: int | None = None
    cash_pct: float | None = None
    unrealized: float | None = None
    realized: float | None = None
    fees: float | None = None
    last_close_value: float | None = None
    flows_since_close: float = 0.0
    twr_1y: float | None = None
    btc_1y: float | None = None
    ann_vol: float | None = None
    ewma_vol: float | None = None
    btc_beta: float | None = None
    var95: float | None = None  # loss fraction of NAV (negative)
    es: float | None = None  # expected shortfall, loss fraction of NAV
    es_level: str = "95%"
    max_drawdown: float | None = None
    underwater_days: int | None = None
    is_demo: bool = False
    extra: dict = field(default_factory=dict)


def _ok(x: float | None) -> bool:
    return x is not None and math.isfinite(x)


def day_pnl(state: KpiState) -> tuple[float | None, float | None]:
    """(USD, fraction) change since the last daily close, flows removed."""
    if not (_ok(state.nav) and _ok(state.last_close_value)) or state.last_close_value <= 0:
        return None, None
    usd = state.nav - state.last_close_value - state.flows_since_close
    return usd, usd / state.last_close_value


def header_values(state: KpiState) -> tuple[str, str, str]:
    """(NAV text, day P&L text, day tone) for the header strip."""
    usd, frac = day_pnl(state)
    if usd is None:
        return format_usd(state.nav), "—", ""
    return (
        format_usd(state.nav),
        f"{format_usd(usd, signed=True)} ({format_pct(frac, 2, signed=True)})",
        tone(usd),
    )


def build_kpis(state: KpiState) -> list[Kpi]:
    """The eight KPI cells, in order, each with its unit and a one-line context."""
    nav_sub = []
    if state.holdings is not None:
        nav_sub.append(f"{state.holdings} holdings")
    if _ok(state.cash_pct):
        nav_sub.append(f"{state.cash_pct * 100:.0f}% cash")
    var_usd = state.var95 * state.nav if _ok(state.var95) and _ok(state.nav) else None
    es_usd = state.es * state.nav if _ok(state.es) and _ok(state.nav) else None
    bench = f"BTC {format_pct(state.btc_1y, signed=True)} same window" if _ok(state.btc_1y) else ""
    return [
        Kpi("NAV", format_usd(state.nav), " · ".join(nav_sub)),
        Kpi(
            "Unrealized P&L",
            format_usd(state.unrealized, signed=True),
            "USD-quoted lots",
            tone(state.unrealized),
        ),
        Kpi(
            "Realized",
            format_usd(state.realized, signed=True),
            f"net of {format_usd(state.fees)} fees" if _ok(state.fees) else "net of fees",
            tone(state.realized),
        ),
        Kpi("Return 1Y (TWR)", format_pct(state.twr_1y, signed=True), bench, tone(state.twr_1y)),
        Kpi(
            "Ann. vol",
            format_pct(state.ann_vol),
            f"EWMA {format_pct(state.ewma_vol)} (λ 0.94)" if _ok(state.ewma_vol) else "",
        ),
        Kpi("BTC beta", "—" if not _ok(state.btc_beta) else f"{state.btc_beta:.2f}", "90d window"),
        Kpi(
            "1-day VaR 95%",
            format_usd(var_usd),
            f"ES {state.es_level} {format_usd(es_usd)} · hist."
            if es_usd is not None
            else "historical",
            "neg" if var_usd is not None and var_usd < 0 else "",
        ),
        Kpi(
            "Max drawdown",
            format_pct(state.max_drawdown),
            f"underwater {state.underwater_days} d" if state.underwater_days else "",
            "neg" if _ok(state.max_drawdown) and state.max_drawdown < 0 else "",
        ),
    ]


class KpiHub(QObject):
    """Merges partial updates from the panels and re-emits the whole state."""

    changed = Signal(object)  # KpiState

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.state = KpiState()

    def update(self, **values) -> None:
        known = {k: v for k, v in values.items() if k in KpiState.__dataclass_fields__}
        self.state = replace(self.state, **known)
        self.changed.emit(self.state)


__all__ = ["Kpi", "KpiHub", "KpiState", "build_kpis", "day_pnl", "header_values"]
