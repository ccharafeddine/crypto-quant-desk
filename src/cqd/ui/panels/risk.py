"""Risk panel: portfolio-level risk metrics from AccountRisk.

The panel only FORMATS fields from AccountRisk (no risk math here - that lives in
the engine). It self-acquires its client via make_client (same async-load pattern
as the Positions panel), so the Demo/Live selection (CQD_DATA_SOURCE) flows
through and the panel works with no Kraken keys in demo mode.

The AccountRisk -> labels/rows mapping is a pure function (build_risk_view) so it
is testable without a running QApplication.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
)

from cqd.data.client import make_client
from cqd.data.errors import KrakenAuthError, KrakenError
from cqd.data.portfolio import AccountRisk, EmptyPortfolioError, compute_account_risk
from cqd.ui.panels.base import Panel
from cqd.ui.widgets import Badge, PanelHeader

_FOOTNOTE = (
    "Annualized over 365 days · simple returns · beta vs BTC · "
    "EWMA vol uses lambda 0.94 · USD and USD-pegged stables contribute zero "
    "vol and beta; non-USD fiat floats like any other asset."
)


@dataclass
class RiskView:
    """Formatted, render-ready view of an AccountRisk (all values are strings)."""

    is_demo: bool
    total_usd_str: str
    metrics: list[tuple[str, str]]  # (label, value)
    rows: list[tuple[str, str, str, str]]  # asset, weight, beta, risk-contrib
    caveats: list[str] = field(default_factory=list)
    footnote: str = _FOOTNOTE


def _is_nan(x) -> bool:
    return x != x


def _pct(x: float, places: int = 1) -> str:
    if x is None or _is_nan(x):
        return "—"
    return f"{x * 100:.{places}f}%"


def _ratio(x: float, places: int = 2) -> str:
    if x is None or _is_nan(x):
        return "—"
    return f"{x:.{places}f}"


def build_risk_view(ar: AccountRisk, *, is_demo: bool) -> RiskView:
    """Map an AccountRisk to render-ready labels, rows, and caveats.

    Per-asset rows use the engine result (ar.risk) so weight, beta, and risk
    contribution stay aligned on the same asset index, sorted by weight desc.
    """
    r = ar.risk

    metrics = [
        ("Annualized vol", _pct(r.ann_vol)),
        ("EWMA vol (λ=0.94)", _pct(r.ewma_vol)),
        ("BTC beta", _ratio(r.book_beta_btc)),
        ("Concentration (HHI)", _ratio(r.hhi, 3)),
        ("Effective bets", _ratio(r.effective_bets)),
        ("Top-3 concentration", _pct(r.top3_concentration)),
    ]

    rows: list[tuple[str, str, str, str]] = []
    for asset, weight in r.weights.sort_values(ascending=False).items():
        beta = r.per_asset_beta.get(asset)
        rc = r.risk_contribution.get(asset)
        rc_str = "—" if rc is None or _is_nan(rc) else f"{rc:.1f}%"
        rows.append((str(asset), _pct(weight), _ratio(beta), rc_str))

    caveats: list[str] = []
    unpriced = ar.info.get("unpriced") or []
    dust = ar.info.get("dust") or {}
    if unpriced:
        caveats.append("Excluded (no price): " + ", ".join(map(str, unpriced)))
    if dust:
        min_usd = ar.info.get("min_usd", 1.0)
        caveats.append(
            f"Excluded (dust < ${min_usd:g}): " + ", ".join(map(str, dust))
        )
    dropped = ar.info.get("returns_dropped") or []
    excluded = list(r.excluded_assets)
    no_history = list(dict.fromkeys([*dropped, *excluded]))
    if no_history:
        caveats.append(
            "Excluded from risk (no return history): " + ", ".join(map(str, no_history))
        )
    if r.frame_days and r.window_days < r.frame_days:
        caveats.append(
            f"Risk window shortened to {r.window_days} of {r.frame_days} days "
            "by the newest holding's history."
        )
    if not caveats:
        caveats.append("All holdings priced.")

    return RiskView(
        is_demo=is_demo,
        total_usd_str=f"${ar.total_usd:,.2f}",
        metrics=metrics,
        rows=rows,
        caveats=caveats,
    )


class RiskPanel(Panel):
    title = "Risk"

    HEADERS = ["Asset", "Weight", "BTC β", "Risk %"]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        # Header row with a small DEMO pill (left-aligned, never full width) so
        # demo data is never mistaken for the user's real account.
        header = PanelHeader("Risk")
        self.demo_badge = Badge("DEMO")
        self.demo_badge.setVisible(False)
        header.add_left(self.demo_badge)
        self._layout.addWidget(header)

        self.value_label = QLabel("")
        self.value_label.setProperty("role", "subtitle")
        self._layout.addWidget(self.value_label)

        # Portfolio-level metrics.
        self.metrics_label = QLabel("")
        self.metrics_label.setTextFormat(Qt.TextFormat.RichText)
        self._layout.addWidget(self.metrics_label)

        # Per-asset table.
        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        # The per-asset table is the core of the cockpit: give it room for
        # several rows and let it absorb the panel's vertical space (scrolling
        # when rows overflow), while the surrounding labels stay natural height.
        self.table.setMinimumHeight(220)
        self.table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._layout.addWidget(self.table, 1)

        self.caveats_label = QLabel("")
        self.caveats_label.setProperty("role", "subtitle")
        self.caveats_label.setWordWrap(True)
        self._layout.addWidget(self.caveats_label)

        self.footnote_label = QLabel(_FOOTNOTE)
        self.footnote_label.setProperty("role", "footnote")
        self.footnote_label.setWordWrap(True)
        self._layout.addWidget(self.footnote_label)

        self.status = QLabel("Not loaded")
        self.status.setProperty("role", "subtitle")
        self._layout.addWidget(self.status)

        # Auto-load on construction (never blocks the UI thread).
        asyncio.ensure_future(self.load())

    async def load(self) -> None:
        gen = self._begin_load()
        self.status.setText("Loading...")
        try:
            from cqd.ui.settings_store import get_dust_threshold_usd

            client = make_client()
            async with client as c:
                ar = await compute_account_risk(c, min_usd=get_dust_threshold_usd())
                is_demo = getattr(c, "is_demo", False)
            if not self._is_current(gen):
                return  # a newer load owns the UI now
            self._render(build_risk_view(ar, is_demo=is_demo))
            self.status.setText("Loaded")
        except EmptyPortfolioError:
            if self._is_current(gen):
                self.status.setText("No priceable holdings to compute risk.")
        except KrakenAuthError:
            if self._is_current(gen):
                self.status.setText(
                    "Authentication failed. Check your Kraken keys in "
                    "File > Settings, or switch to demo data there."
                )
        except KrakenError as e:
            if self._is_current(gen):
                self.status.setText(f"Kraken error: {e}")
        except Exception as e:  # noqa: BLE001
            if self._is_current(gen):
                self.status.setText(f"Error: {e}")

    def _render(self, view: RiskView) -> None:
        self.value_label.setText(f"Total value: {view.total_usd_str}")
        self.demo_badge.setVisible(view.is_demo)

        self.metrics_label.setText(
            "<br>".join(f"<b>{label}:</b> {value}" for label, value in view.metrics)
        )

        self.table.clearContents()
        self.table.setRowCount(len(view.rows))
        for i, (asset, weight, beta, rc) in enumerate(view.rows):
            self.table.setItem(i, 0, _cell(asset, align_left=True))
            self.table.setItem(i, 1, _cell(weight))
            self.table.setItem(i, 2, _cell(beta))
            self.table.setItem(i, 3, _cell(rc))

        self.caveats_label.setText("\n".join(view.caveats))
        self.footnote_label.setText(view.footnote)

    def refresh(self) -> None:
        asyncio.ensure_future(self.load())


def _cell(text: str, align_left: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(
        Qt.AlignmentFlag.AlignVCenter
        | (Qt.AlignmentFlag.AlignLeft if align_left else Qt.AlignmentFlag.AlignRight)
    )
    return item
