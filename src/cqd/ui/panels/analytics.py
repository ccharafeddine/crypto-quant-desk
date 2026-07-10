"""Analytics panel: the Bloomberg-grade suite.

Sections live in a tab widget so the suite can grow (E4a Ratios; E4b Exposure -
correlation heatmap, per-holding risk contribution, concentration, sector mix;
E4c attribution and E4d scenario land as further tabs). Every number is
engine-computed (pure, tested); this panel fetches the portfolio's history,
derives the inputs, and renders - it never invents a metric.
"""

from __future__ import annotations

import asyncio

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QGridLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from cqd.data.client import make_client
from cqd.data.errors import KrakenAuthError, KrakenError
from cqd.data.portfolio import EmptyPortfolioError, compute_account_risk
from cqd.data.sectors import sector_exposure, sector_of
from cqd.engine.metrics import ratio_summary
from cqd.engine.performance import CASH_ASSETS, build_equity_curve
from cqd.engine.risk import correlation_matrix
from cqd.ui.panels.base import Panel
from cqd.ui.theme import get_theme, load_theme_name
from cqd.ui.widgets import PanelHeader

_RATIO_FOOTNOTE = (
    "365-day annualization · simple returns from daily equity · EWMA vol λ=0.94 · "
    "Sortino downside dev vs 0 · Calmar = ann. return / |max drawdown| · "
    "VaR/CVaR historical at 95%."
)

# key -> (label, is_percent). Percent metrics render as %; ratios as x.xx.
_RATIO_METRICS: list[tuple[str, str, bool]] = [
    ("ann_return", "Ann. return", True),
    ("ann_vol", "Ann. vol", True),
    ("ewma_vol", "EWMA vol", True),
    ("sharpe", "Sharpe", False),
    ("sortino", "Sortino", False),
    ("calmar", "Calmar", False),
    ("max_drawdown", "Max drawdown", True),
    ("var_95", "VaR 95%", True),
    ("cvar_95", "CVaR 95%", True),
    ("gain_to_pain", "Gain / Pain", False),
]
_COLS = 5


def format_metric(value, is_percent: bool) -> str:
    """Format one ratio-summary value: '—' for None/NaN, % or x.xx otherwise."""
    if value is None or (isinstance(value, float) and value != value):
        return "—"
    return f"{value * 100:,.1f}%" if is_percent else f"{value:,.2f}"


def _blend(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    )


def diverging_color(value: float, neg: QColor, mid: QColor, pos: QColor) -> QColor:
    """Map a correlation in [-1, 1] to a diverging color: neg <- mid -> pos."""
    v = max(-1.0, min(1.0, value))
    return _blend(mid, pos, v) if v >= 0 else _blend(mid, neg, -v)


class CorrelationHeatmap(QWidget):
    """Paints a correlation matrix as a diverging-color grid with axis labels."""

    _MARGIN = 46

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._labels: list[str] = []
        self._values: list[list[float]] = []
        self.setMinimumHeight(160)

    def set_matrix(self, matrix) -> None:
        if matrix is None or matrix.empty:
            self._labels, self._values = [], []
        else:
            self._labels = [str(c) for c in matrix.columns]
            self._values = matrix.to_numpy().tolist()
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        if not self._labels:
            return
        theme = get_theme(load_theme_name())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        neg, mid, pos = QColor(theme.negative), QColor(theme.surface_raised), QColor(theme.positive)
        n = len(self._labels)
        grid = min(self.width() - self._MARGIN, self.height() - self._MARGIN)
        if grid <= 0:
            return
        cell = grid / n
        painter.setPen(QColor(theme.text_muted))
        for i in range(n):
            for j in range(n):
                color = diverging_color(float(self._values[i][j]), neg, mid, pos)
                x = self._MARGIN + j * cell
                y = self._MARGIN + i * cell
                painter.fillRect(QRectF(x, y, cell - 1, cell - 1), color)
        painter.setPen(QColor(theme.text_muted))
        for k, label in enumerate(self._labels):
            short = label[:4]
            y = self._MARGIN + k * cell + cell / 2 + 4
            painter.drawText(
                QRectF(0, y - 10, self._MARGIN - 4, 14), Qt.AlignmentFlag.AlignRight, short
            )
            x = self._MARGIN + k * cell
            painter.drawText(
                QRectF(x, self._MARGIN - 16, cell, 14), Qt.AlignmentFlag.AlignCenter, short
            )


class AnalyticsPanel(Panel):
    title = "Analytics"

    EXPOSURE_HEADERS = ["Asset", "Weight", "Risk contrib.", "Sector"]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._layout.addWidget(PanelHeader("Analytics"))

        self.tabs = QTabWidget()
        self._layout.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_ratios_tab(), "Ratios")
        self.tabs.addTab(self._build_exposure_tab(), "Exposure")

        self.status = QLabel("Not loaded")
        self.status.setProperty("role", "subtitle")
        self._layout.addWidget(self.status)

        asyncio.ensure_future(self.load())

    # ---------- tab construction ----------

    def _build_ratios_tab(self) -> QWidget:
        tab = QWidget()
        grid = QGridLayout(tab)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(8)
        self._ratio_values: dict[str, QLabel] = {}
        for i, (key, label, _pct) in enumerate(_RATIO_METRICS):
            block, col = divmod(i, _COLS)
            name = QLabel(label)
            name.setProperty("role", "metric-label")
            value = QLabel("—")
            value.setProperty("role", "metric-value")
            self._ratio_values[key] = value
            grid.addWidget(name, block * 2, col)
            grid.addWidget(value, block * 2 + 1, col)
        foot = QLabel(_RATIO_FOOTNOTE)
        foot.setProperty("role", "footnote")
        foot.setWordWrap(True)
        grid.addWidget(foot, (len(_RATIO_METRICS) // _COLS) * 2 + 2, 0, 1, _COLS)
        grid.setRowStretch((len(_RATIO_METRICS) // _COLS) * 2 + 3, 1)
        return tab

    def _build_exposure_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setSpacing(8)

        conc = QGridLayout()
        conc.setHorizontalSpacing(20)
        self._conc_values: dict[str, QLabel] = {}
        for col, (key, label) in enumerate(
            [("hhi", "Herfindahl"), ("effective_bets", "Effective N"), ("top3", "Top-3 conc.")]
        ):
            name = QLabel(label)
            name.setProperty("role", "metric-label")
            value = QLabel("—")
            value.setProperty("role", "metric-value")
            self._conc_values[key] = value
            conc.addWidget(name, 0, col)
            conc.addWidget(value, 1, col)
        lay.addLayout(conc)

        self.sector_label = QLabel("Sector exposure: —")
        self.sector_label.setProperty("role", "subtitle")
        self.sector_label.setWordWrap(True)
        lay.addWidget(self.sector_label)

        corr_title = QLabel("Return correlation")
        corr_title.setProperty("role", "metric-label")
        lay.addWidget(corr_title)
        self.heatmap = CorrelationHeatmap()
        lay.addWidget(self.heatmap, 2)

        self.exposure_table = QTableWidget(0, len(self.EXPOSURE_HEADERS))
        self.exposure_table.setHorizontalHeaderLabels(self.EXPOSURE_HEADERS)
        self.exposure_table.verticalHeader().hide()
        self.exposure_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.exposure_table.setShowGrid(False)
        self.exposure_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.exposure_table, 2)
        return tab

    # ---------- data ----------

    async def load(self) -> None:
        gen = self._begin_load()
        self.status.setText("Loading...")
        try:
            client = make_client()
            async with client as c:
                ledgers = await c.get_ledgers()
                balances = await c.get_balance()
                assets = sorted(
                    {str(e["asset"]) for e in ledgers} - CASH_ASSETS
                    | {a for a in balances if a not in CASH_ASSETS}
                )
                closes: dict[str, list[tuple[int, float]]] = {}
                for asset in assets:
                    try:
                        closes[asset] = await c.get_ohlc_closes(f"{asset}USD", interval=1440)
                    except KrakenError:
                        continue
                try:
                    account_risk = await compute_account_risk(c)
                except EmptyPortfolioError:
                    account_risk = None
        except KrakenAuthError:
            if self._is_current(gen):
                self.status.setText("Authentication failed. Check File > Settings.")
            return
        except Exception as e:  # noqa: BLE001
            if self._is_current(gen):
                self.status.setText(f"Error: {e}")
            return
        if not self._is_current(gen):
            return

        equity = build_equity_curve(ledgers, closes)
        returns = equity.pct_change().dropna()
        self._render_ratios(ratio_summary(returns))
        self._render_exposure(account_risk)
        n = len(returns)
        self.status.setText(f"{n} daily returns" if n else "Not enough history for ratios yet.")

    # ---------- rendering ----------

    def _render_ratios(self, summary: dict) -> None:
        for key, _label, is_percent in _RATIO_METRICS:
            self._ratio_values[key].setText(format_metric(summary.get(key), is_percent))

    def _render_exposure(self, account_risk) -> None:
        if account_risk is None:
            self.sector_label.setText("Sector exposure: no priceable holdings.")
            self.heatmap.set_matrix(None)
            self.exposure_table.setRowCount(0)
            return
        risk = account_risk.risk
        self._conc_values["hhi"].setText(f"{risk.hhi:.3f}")
        self._conc_values["effective_bets"].setText(f"{risk.effective_bets:.2f}")
        self._conc_values["top3"].setText(format_metric(risk.top3_concentration, is_percent=True))

        sectors = sector_exposure(account_risk.weights)
        self.sector_label.setText(
            "Sector exposure: " + " · ".join(f"{s} {w * 100:.1f}%" for s, w in sectors.items())
        )

        self.heatmap.set_matrix(correlation_matrix(account_risk.returns))

        weights = account_risk.weights.sort_values(ascending=False)
        rc = risk.risk_contribution
        self.exposure_table.setRowCount(len(weights))
        for row, (asset, weight) in enumerate(weights.items()):
            contrib = float(rc.get(asset, float("nan"))) if rc is not None else float("nan")
            self.exposure_table.setItem(row, 0, _cell(str(asset), Qt.AlignmentFlag.AlignLeft))
            self.exposure_table.setItem(row, 1, _cell(f"{weight * 100:.1f}%"))
            self.exposure_table.setItem(
                row, 2, _cell(format_metric(contrib / 100, is_percent=True))
            )
            self.exposure_table.setItem(
                row, 3, _cell(sector_of(str(asset)), Qt.AlignmentFlag.AlignLeft)
            )

    def refresh(self) -> None:
        asyncio.ensure_future(self.load())


def _cell(text: str, align=Qt.AlignmentFlag.AlignRight) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
    return item
