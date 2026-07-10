"""Analytics panel: the Bloomberg-grade suite.

Sections live in a tab widget so the suite can grow (E4a Ratios; E4b correlation
& exposure, E4c attribution, E4d scenario & stress land as further tabs). Every
number is engine-computed (pure, tested); this panel fetches the portfolio's
history, derives the daily return series, and renders - it never invents a metric.
"""

from __future__ import annotations

import asyncio

from PySide6.QtWidgets import QGridLayout, QLabel, QTabWidget, QWidget

from cqd.data.client import make_client
from cqd.data.errors import KrakenAuthError, KrakenError
from cqd.engine.metrics import ratio_summary
from cqd.engine.performance import CASH_ASSETS, build_equity_curve
from cqd.ui.panels.base import Panel
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


class AnalyticsPanel(Panel):
    title = "Analytics"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._layout.addWidget(PanelHeader("Analytics"))

        self.tabs = QTabWidget()
        self._layout.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_ratios_tab(), "Ratios")

        self.status = QLabel("Not loaded")
        self.status.setProperty("role", "subtitle")
        self._layout.addWidget(self.status)

        asyncio.ensure_future(self.load())

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
        summary = ratio_summary(returns)
        self._render_ratios(summary)
        n = len(returns)
        self.status.setText(f"{n} daily returns" if n else "Not enough history for ratios yet.")

    def _render_ratios(self, summary: dict) -> None:
        for key, _label, is_percent in _RATIO_METRICS:
            self._ratio_values[key].setText(format_metric(summary.get(key), is_percent))

    def refresh(self) -> None:
        asyncio.ensure_future(self.load())
