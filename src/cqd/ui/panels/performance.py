"""Performance panel: equity curve, drawdown, trade stats, per-position PnL.

All math comes from the pure engine (performance.py, cost_basis.py); this
panel fetches ledgers/trades/closes, hands them over, and renders. Charts are
pyqtgraph with theme tokens; every metric carries the engine's assumptions
(average-cost realized PnL, quote-labeled non-USD bases, unpriced exclusions).
"""

from __future__ import annotations

import asyncio

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QGridLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
)

from cqd.data.client import make_client
from cqd.data.errors import KrakenAuthError, KrakenError
from cqd.engine.cost_basis import cost_basis_by_quote
from cqd.engine.performance import (
    CASH_ASSETS,
    build_equity_curve,
    drawdown_stats,
    realized_trades,
    trade_stats,
)
from cqd.ui.panels.base import Panel
from cqd.ui.theme import get_theme, load_theme_name
from cqd.ui.widgets import Badge, PanelHeader, PanelStatus

_FOOTNOTE = (
    "Equity from ledger balances × daily closes (USD-pegged assets at 1.00) · "
    "realized PnL uses running average cost, fees included · non-USD-quoted "
    "PnL is labeled in its quote currency, never summed as USD."
)


def _fmt_pct(x: float) -> str:
    return "—" if x != x else f"{x * 100:,.1f}%"


def _fmt_usd(x: float) -> str:
    return "—" if x != x else f"${x:,.2f}"


class PerformancePanel(Panel):
    title = "Performance"

    POS_HEADERS = ["Asset", "Qty", "Avg cost", "Unrealized", "Realized", "Fees"]

    #: Current drawdown (engine's negative fraction) after each load - alert feed.
    drawdown_updated = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        header = PanelHeader("Performance")
        self.demo_badge = Badge("DEMO")
        self.demo_badge.setVisible(False)
        header.add_left(self.demo_badge)
        self._layout.addWidget(header)

        theme = get_theme(load_theme_name())
        pg.setConfigOptions(antialias=True)

        self.equity_plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.equity_plot.setBackground(theme.bg)
        self.equity_plot.showGrid(x=True, y=True, alpha=0.15)
        self.equity_plot.setMinimumHeight(180)
        self._layout.addWidget(self.equity_plot, 2)

        self.dd_plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.dd_plot.setBackground(theme.bg)
        self.dd_plot.showGrid(x=True, y=True, alpha=0.15)
        self.dd_plot.setMinimumHeight(90)
        self.dd_plot.setXLink(self.equity_plot)
        self._layout.addWidget(self.dd_plot, 1)

        self.stats_grid = QGridLayout()
        self.stats_grid.setHorizontalSpacing(18)
        self._stat_values: dict[str, QLabel] = {}
        for col, label in enumerate(
            ["Max DD", "Current DD", "Win rate", "Expectancy", "Profit factor", "Realized"]
        ):
            key = QLabel(label)
            key.setProperty("role", "metric-label")
            val = QLabel("—")
            val.setProperty("role", "metric-value")
            self._stat_values[label] = val
            self.stats_grid.addWidget(key, 0, col)
            self.stats_grid.addWidget(val, 1, col)
        self._layout.addLayout(self.stats_grid)

        self.pos_table = QTableWidget(0, len(self.POS_HEADERS))
        self.pos_table.setHorizontalHeaderLabels(self.POS_HEADERS)
        self.pos_table.verticalHeader().hide()
        self.pos_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.pos_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.pos_table.setShowGrid(False)
        self.pos_table.setMinimumHeight(120)
        self._layout.addWidget(self.pos_table, 1)

        self.footnote = QLabel(_FOOTNOTE)
        self.footnote.setProperty("role", "footnote")
        self.footnote.setWordWrap(True)
        self._layout.addWidget(self.footnote)

        self.status = PanelStatus("Not loaded", self.refresh)
        self._layout.addWidget(self.status)

        asyncio.ensure_future(self.load())

    # ---------- data ----------

    async def load(self) -> None:
        gen = self._begin_load()
        self.status.setText("Loading...")
        try:
            client = make_client()
            async with client as c:
                is_demo = getattr(c, "is_demo", False)
                balances = await c.get_balance()
                trades = await c.get_trades()
                ledgers = await c.get_ledgers()
                assets = sorted(
                    {str(e["asset"]) for e in ledgers} - CASH_ASSETS
                    | {a for a in balances if a not in CASH_ASSETS}
                )
                closes: dict[str, list[tuple[int, float]]] = {}
                for asset in assets:
                    try:
                        closes[asset] = await c.get_ohlc_closes(f"{asset}USD", interval=1440)
                    except KrakenError:
                        continue  # equity curve reports it as unpriced
                marks = await c.get_marks([f"{a}USD" for a in balances if a not in CASH_ASSETS])
        except KrakenAuthError:
            if self._is_current(gen):
                self.status.error("Authentication failed. Check File > Settings.")
            return
        except Exception as e:  # noqa: BLE001
            if self._is_current(gen):
                self.status.error(f"Couldn't load performance. ({e})")
            return
        if not self._is_current(gen):
            return

        equity = build_equity_curve(ledgers, closes)
        dd = drawdown_stats(equity)
        stats = trade_stats(realized_trades(trades))
        if dd["current_drawdown"] == dd["current_drawdown"]:  # not NaN
            self.drawdown_updated.emit(dd["current_drawdown"])
        self.demo_badge.setVisible(is_demo)
        self._render_plots(equity)
        self._render_stats(dd, stats)
        self._render_positions(balances, trades, marks)

        caveats = []
        unpriced = equity.attrs.get("unpriced") or []
        if unpriced:
            caveats.append("unpriced in equity: " + ", ".join(unpriced))
        n = len(equity)
        self.status.setText(
            f"{n} days of history" + (f" · {'; '.join(caveats)}" if caveats else "")
        )

    # ---------- rendering ----------

    def _render_plots(self, equity) -> None:
        theme = get_theme(load_theme_name())
        self.equity_plot.clear()
        self.dd_plot.clear()
        if equity.empty:
            return
        x = [ts.timestamp() for ts in equity.index]
        y = [float(v) for v in equity.values]
        self.equity_plot.plot(x, y, pen=pg.mkPen(theme.accent, width=2))

        peak = equity.cummax()
        dd = (equity / peak - 1.0) * 100.0
        neg = QColor(theme.negative)
        neg.setAlpha(64)
        self.dd_plot.plot(
            x,
            [float(v) for v in dd.values],
            pen=pg.mkPen(theme.negative, width=1),
            fillLevel=0.0,
            brush=pg.mkBrush(neg),
        )

    def _render_stats(self, dd: dict, stats: dict) -> None:
        self._stat_values["Max DD"].setText(_fmt_pct(dd["max_drawdown"]))
        self._stat_values["Current DD"].setText(_fmt_pct(dd["current_drawdown"]))
        self._stat_values["Win rate"].setText(_fmt_pct(stats["win_rate"]))
        self._stat_values["Expectancy"].setText(_fmt_usd(stats["expectancy"]))
        pf = stats["profit_factor"]
        self._stat_values["Profit factor"].setText(
            "—" if pf != pf else ("∞" if pf == float("inf") else f"{pf:,.2f}")
        )
        self._stat_values["Realized"].setText(_fmt_usd(stats["total_realized"]))

    def _render_positions(self, balances: dict, trades: list, marks: dict) -> None:
        rows = []
        for asset, qty in sorted(balances.items()):
            if not qty or qty <= 0 or asset in CASH_ASSETS:
                continue
            by_quote = cost_basis_by_quote(trades, asset)
            mark = marks.get(f"{asset}/USD")
            usd = by_quote.get("USD")
            avg = usd.avg_cost if usd else 0.0
            unreal = qty * (mark - avg) if (mark is not None and usd and avg > 0) else float("nan")
            realized_parts = [
                f"{cb.realized_pnl:,.2f} {q}" if q != "USD" else f"${cb.realized_pnl:,.2f}"
                for q, cb in sorted(by_quote.items())
                if abs(cb.realized_pnl) > 1e-12
            ]
            fee_parts = [
                f"{cb.fees_paid:,.2f} {q}" if q != "USD" else f"${cb.fees_paid:,.2f}"
                for q, cb in sorted(by_quote.items())
                if cb.fees_paid > 1e-12
            ]
            rows.append(
                (
                    asset,
                    f"{qty:,.6f}",
                    f"${avg:,.6f}" if avg > 0 else "—",
                    _fmt_usd(unreal),
                    "; ".join(realized_parts) or "—",
                    "; ".join(fee_parts) or "—",
                )
            )
        self.pos_table.clearContents()
        self.pos_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, text in enumerate(row):
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter
                    | (Qt.AlignmentFlag.AlignLeft if j == 0 else Qt.AlignmentFlag.AlignRight)
                )
                self.pos_table.setItem(i, j, item)

    def refresh(self) -> None:
        asyncio.ensure_future(self.load())
