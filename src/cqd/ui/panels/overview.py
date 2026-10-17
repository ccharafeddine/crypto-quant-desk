"""Portfolio overview: time-weighted equity vs BTC, risk at a glance, positions.

The landing card of the Graphite redesign (mockup 1). It computes nothing of
its own: the Performance card publishes the TWR index, flows and the BTC close
series; the Risk card publishes the AccountRisk. The pure helpers below turn
those into chart series, flow markers and table rows (tested without Qt).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cqd.engine import metrics as M
from cqd.ui.format import format_pct, format_usd
from cqd.ui.panels.base import Panel
from cqd.ui.theme import get_theme, load_theme_name
from cqd.ui.widgets import Badge, PanelHeader, PanelStatus

#: Chart range buttons -> trailing days (None = all history).
RANGES: dict[str, int | None] = {"1M": 30, "3M": 90, "1Y": 365, "All": None}
DEFAULT_RANGE = "1Y"
STRESS_BTC_SHOCK = -0.30
_RC_ROLE = Qt.ItemDataRole.UserRole + 7


# ---------------------------------------------------------------- pure helpers


def closes_series(closes: list[tuple[int, float]]) -> pd.Series:
    """Kraken (unix ts, close) pairs -> a daily UTC-indexed close series."""
    if not closes:
        return pd.Series(dtype=float)
    # Naive UTC dates, matching the engine's equity/TWR index.
    idx = pd.to_datetime([int(t) for t, _ in closes], unit="s").normalize()
    return pd.Series([float(c) for _, c in closes], index=idx).groupby(level=0).last()


def window(series: pd.Series, key: str) -> pd.Series:
    """The trailing slice of `series` for a range key (whole series for All)."""
    days = RANGES.get(key)
    if series.empty or days is None:
        return series
    start = series.index[-1] - pd.Timedelta(days=days)
    return series[series.index >= start]


def rebased_pct(series: pd.Series) -> pd.Series:
    """Cumulative % change from the first point of the slice (first = 0%)."""
    s = series.dropna()
    if s.empty or s.iloc[0] == 0:
        return pd.Series(dtype=float)
    return (s / s.iloc[0] - 1.0) * 100.0


def benchmark_curves(twr: pd.Series, btc: pd.Series, key: str) -> tuple[pd.Series, pd.Series]:
    """Portfolio TWR and BTC buy-and-hold, both rebased to 0% on the same window.

    BTC is aligned to the portfolio's dates (forward-filled over gaps) so the
    two lines start on the same day and compare like for like.
    """
    port = window(twr, key)
    if port.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    if btc.empty:
        return rebased_pct(port), pd.Series(dtype=float)
    if (btc.index.tz is None) != (port.index.tz is None):
        btc = btc.tz_localize(None) if btc.index.tz is not None else btc.tz_localize("UTC")
    bench = btc.sort_index().reindex(port.index, method="ffill")
    return rebased_pct(port), rebased_pct(bench)


def trailing_return(series: pd.Series, days: int = 365) -> float | None:
    """Total return of a wealth/price series over the trailing `days`."""
    s = series.dropna()
    if s.empty:
        return None
    s = s[s.index >= s.index[-1] - pd.Timedelta(days=days)]
    if len(s) < 2 or s.iloc[0] <= 0:
        return None
    return float(s.iloc[-1] / s.iloc[0] - 1.0)


@dataclass(frozen=True)
class FlowMarker:
    ts: float
    amount: float
    label: str


def flow_markers(flows: pd.DataFrame | None, start: pd.Timestamp | None = None) -> list[FlowMarker]:
    """One marker per day with a deposit/withdrawal, labelled with its amount."""
    if flows is None or flows.empty or "flow" not in flows:
        return []
    out = []
    for ts, amount in flows["flow"].items():
        if abs(amount) < 0.01 or (start is not None and ts < start):
            continue
        kind = "deposit" if amount > 0 else "withdrawal"
        out.append(
            FlowMarker(
                ts.timestamp(), float(amount), f"{format_usd(amount, 0, signed=True)} {kind}"
            )
        )
    return out


def window_sharpes(port: pd.Series, bench: pd.Series, rf: float) -> tuple[float, float]:
    """Same-window Sharpe for the portfolio TWR and BTC (rebased % inputs)."""

    def sharpe(pct: pd.Series) -> float:
        if len(pct) < 3:
            return float("nan")
        r = (1 + pct / 100.0).pct_change().dropna()
        return M.sharpe_ratio(r, rf_annual=rf)

    return sharpe(port), sharpe(bench)


def glance_rows(ar) -> list[tuple[str, str, str]]:
    """'Risk at a glance' rows (label, value, tone) from an AccountRisk."""
    r = ar.risk
    total = ar.total_usd
    stress = r.book_beta_btc * STRESS_BTC_SHOCK if _finite(r.book_beta_btc) else None
    var95 = r.tail.get("VaR_95") if r.tail else None
    rows = [
        ("Effective bets (1/HHI)", _num(r.effective_bets), ""),
        ("Top-3 concentration", format_pct(r.top3_concentration), ""),
        ("Book beta to BTC", _num(r.book_beta_btc), ""),
        ("EWMA vol (λ 0.94)", format_pct(r.ewma_vol), ""),
    ]
    if _finite(var95):
        rows.append(("1-day VaR 95% (hist.)", format_usd(var95 * total), "neg"))
    if stress is not None:
        rows.append(
            (
                f"Stress: BTC {STRESS_BTC_SHOCK * 100:.0f}% (linear β)",
                f"{format_usd(stress * total)} ({format_pct(stress)})",
                "neg",
            )
        )
    return rows


def position_rows(ar) -> list[tuple[str, str, str, str, float]]:
    """(asset, value $, weight, beta, risk-contribution fraction) by weight."""
    r = ar.risk
    rows = []
    for asset, weight in r.weights.sort_values(ascending=False).items():
        rc = r.risk_contribution.get(asset)
        beta = r.per_asset_beta.get(asset)
        rows.append(
            (
                str(asset),
                format_usd(weight * ar.total_usd),
                format_pct(weight),
                _num(beta),
                float(rc) / 100.0 if _finite(rc) else 0.0,
            )
        )
    return rows


def _finite(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x)


def _num(x) -> str:
    return f"{x:.2f}" if _finite(x) else "—"


# ---------------------------------------------------------------- widgets


class RiskBarDelegate(QStyledItemDelegate):
    """Paints the risk-contribution share as a hairline amber bar + percent."""

    def paint(self, painter, option, index) -> None:
        frac = index.data(_RC_ROLE)
        if frac is None:
            return super().paint(painter, option, index)
        theme = get_theme(load_theme_name())
        rect = option.rect.adjusted(8, 9, -60, -9)
        width = int(max(0.0, min(1.0, float(frac))) * rect.width())
        painter.fillRect(QRect(rect.left(), rect.top(), width, rect.height()), QColor(theme.accent))
        painter.setPen(QColor(theme.text_muted))
        painter.drawText(
            option.rect.adjusted(0, 0, -8, 0),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"{frac * 100:.1f}%",
        )


class OverviewPanel(Panel):
    title = "Overview"

    POS_HEADERS = ["Asset", "Value", "Weight", "BTC β", "Risk contribution"]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._twr = pd.Series(dtype=float)
        self._btc = pd.Series(dtype=float)
        self._flows: pd.DataFrame | None = None
        self._rf = 0.0
        self._range = DEFAULT_RANGE
        theme = get_theme(load_theme_name())

        header = PanelHeader("Equity curve · time-weighted, net of flows", show_title=True)
        self.demo_badge = Badge("EXAMPLE DATA", kind="neutral")
        self.demo_badge.setVisible(False)
        header.add_left(self.demo_badge)
        self._range_group = QButtonGroup(self)
        for key in RANGES:
            btn = QPushButton(key)
            btn.setCheckable(True)
            btn.setChecked(key == self._range)
            btn.setProperty("role", "table-action")
            btn.clicked.connect(lambda _c=False, k=key: self._set_range(k))
            self._range_group.addButton(btn)
            header.add_right(btn)
        self._layout.addWidget(header)

        body = QHBoxLayout()
        body.setSpacing(12)
        left = QVBoxLayout()
        self.plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.plot.setBackground(theme.surface)
        self.plot.showGrid(x=False, y=True, alpha=0.12)
        self.plot.setMinimumHeight(240)
        for axis in ("left", "bottom"):
            self.plot.getAxis(axis).setTextPen(theme.text_faint)
            self.plot.getAxis(axis).setPen(theme.border)
        self.plot.addLegend(
            offset=(10, -6),  # bottom-left, clear of flow labels
            labelTextColor=theme.text_muted,
        )
        left.addWidget(self.plot, 1)
        self.chart_note = QLabel("")
        self.chart_note.setProperty("role", "footnote")
        self.chart_note.setWordWrap(True)
        left.addWidget(self.chart_note)
        body.addLayout(left, 3)

        right = QWidget()
        right.setMinimumWidth(280)
        right.setMaximumWidth(360)
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Risk at a glance")
        title.setProperty("role", "panel-title")
        rlay.addWidget(title)
        self.glance = QGridLayout()
        self.glance.setHorizontalSpacing(12)
        self.glance.setVerticalSpacing(5)
        rlay.addLayout(self.glance)
        rlay.addStretch(1)
        body.addWidget(right, 1)
        self._layout.addLayout(body, 3)

        pos_title = QLabel("Positions · risk % = contribution to portfolio vol")
        pos_title.setProperty("role", "panel-title")
        self._layout.addWidget(pos_title)
        self.table = QTableWidget(0, len(self.POS_HEADERS))
        self.table.setHorizontalHeaderLabels(self.POS_HEADERS)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setShowGrid(False)
        self.table.setItemDelegateForColumn(4, RiskBarDelegate(self.table))
        self.table.setMinimumHeight(160)
        self._layout.addWidget(self.table, 2)

        self.status = PanelStatus("Waiting for performance and risk…")
        self._layout.addWidget(self.status)

    # ---- inputs (wired in main_window) ----

    def on_performance(self, payload: dict) -> None:
        self._twr = payload.get("twr", pd.Series(dtype=float))
        self._btc = payload.get("btc", pd.Series(dtype=float))
        self._flows = payload.get("flows")
        self._rf = float(payload.get("rf", 0.0))
        self.demo_badge.setVisible(bool(payload.get("is_demo")))
        self._render_chart()
        self.status.setText(f"{len(self._twr)} closed daily bars · as of the last 00:00 UTC close")

    def on_risk(self, ar) -> None:
        self._render_glance(glance_rows(ar))
        rows = position_rows(ar)
        self.table.clearContents()
        self.table.setRowCount(len(rows))
        for i, (asset, value, weight, beta, rc) in enumerate(rows):
            for j, text in enumerate((asset, value, weight, beta)):
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter
                    | (Qt.AlignmentFlag.AlignLeft if j == 0 else Qt.AlignmentFlag.AlignRight)
                )
                self.table.setItem(i, j, item)
            bar = QTableWidgetItem()
            bar.setData(_RC_ROLE, rc)
            self.table.setItem(i, 4, bar)

    # ---- rendering ----

    def _set_range(self, key: str) -> None:
        self._range = key
        self._render_chart()

    def _render_chart(self) -> None:
        theme = get_theme(load_theme_name())
        self.plot.clear()
        port, bench = benchmark_curves(self._twr, self._btc, self._range)
        if port.empty:
            self.chart_note.setText("No history yet.")
            return
        x = [ts.timestamp() for ts in port.index]
        # No area fill: pyqtgraph's fillLevel polygon tears when the curve
        # crosses its level, and a flat line reads cleaner against the grid.
        self.plot.plot(
            x,
            port.values,
            pen=pg.mkPen(theme.accent, width=2),
            name="Portfolio (TWR)",
        )
        if not bench.empty:
            bx = [ts.timestamp() for ts in bench.index]
            self.plot.plot(
                bx,
                bench.values,
                pen=pg.mkPen(theme.text_faint, width=1, style=Qt.PenStyle.DashLine),
                name="BTC buy & hold",
            )
        for marker in flow_markers(self._flows, port.index[0]):
            line = pg.InfiniteLine(
                pos=marker.ts,
                angle=90,
                pen=pg.mkPen(theme.border_strong, style=Qt.PenStyle.DashLine),
                label=marker.label,
                labelOpts={"color": theme.text_muted, "position": 0.93},
            )
            self.plot.addItem(line)
        ps, bs = window_sharpes(port, bench, self._rf)
        ret = port.iloc[-1] / 100.0
        bench_txt = (
            f"BTC {format_pct(bench.iloc[-1] / 100.0, signed=True)}, Sharpe {_num(bs)}"
            if not bench.empty
            else "BTC n/a"
        )
        self.chart_note.setText(
            f"{self._range}: portfolio {format_pct(ret, signed=True)}, Sharpe {_num(ps)} · "
            f"{bench_txt} · rf {self._rf * 100:.1f}%. Deposits and withdrawals are stripped "
            "out (daily time-weighted return), so a deposit never reads as a gain."
        )

    def _render_glance(self, rows: list[tuple[str, str, str]]) -> None:
        while self.glance.count():
            item = self.glance.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for i, (label, value, tone) in enumerate(rows):
            key = QLabel(label)
            key.setProperty("role", "subtitle")
            val = QLabel(value)
            val.setProperty("role", "header-value")
            if tone:
                val.setProperty("tone", tone)
            val.setAlignment(Qt.AlignmentFlag.AlignRight)
            self.glance.addWidget(key, i, 0)
            self.glance.addWidget(val, i, 1)

    def refresh(self) -> None:
        """Inputs arrive from the Performance and Risk cards; nothing to fetch."""


__all__ = [
    "OverviewPanel",
    "benchmark_curves",
    "closes_series",
    "flow_markers",
    "glance_rows",
    "position_rows",
    "rebased_pct",
    "trailing_return",
    "window",
]
