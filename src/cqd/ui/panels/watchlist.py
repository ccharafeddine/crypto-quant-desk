"""Watchlist panel: a market list with price, 24h %, volume, and a sparkline.

Ticker data comes from one batched public REST call; a short hourly OHLC series
per row feeds the sparkline (painted by SparklineDelegate). Clicking a row emits
`pair_selected` (a Kraken wsname like "XBT/USD") so the chart and depth follow.
"""

from __future__ import annotations

import asyncio

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
)

from cqd.data.errors import KrakenError
from cqd.data.rest import KrakenRESTClient
from cqd.ui.format import format_compact
from cqd.ui.panels.base import Panel
from cqd.ui.theme import get_theme, load_theme_name
from cqd.ui.widgets import PanelHeader

# Default markets (Kraken altnames used for the ticker query).
_PAIRS = ["XBTUSD", "ETHUSD", "SOLUSD", "XRPUSD", "ADAUSD", "DOTUSD", "LINKUSD", "LTCUSD"]
_POLL_MS = 20_000
_SPARK_ROLE = Qt.ItemDataRole.UserRole  # list[float] of recent closes
# Bare base -> Kraken code, so a display symbol maps to a valid pair to trade.
_BASE_TO_KRAKEN = {"BTC": "XBT", "DOGE": "XDG"}


# ---- pure helpers (testable without a QApplication) ----


def pct_change(last: float, open_: float) -> float:
    """Percent change of last vs the day's open; 0.0 when open is non-positive."""
    if open_ <= 0:
        return 0.0
    return (last - open_) / open_ * 100.0


def to_ws_pair(slash: str) -> str:
    """Display symbol -> Kraken wsname, e.g. 'BTC/USD' -> 'XBT/USD' (OHLC/Depth
    accept wsname). Only the base code is remapped."""
    base, _, quote = slash.partition("/")
    return f"{_BASE_TO_KRAKEN.get(base, base)}/{quote}"


def sparkline_points(closes: list[float], width: float, height: float) -> list[tuple[float, float]]:
    """Map a close series to (x, y) points in a width x height box, y inverted
    (higher price is nearer the top). Empty for fewer than two points."""
    if len(closes) < 2:
        return []
    lo, hi = min(closes), max(closes)
    span = (hi - lo) or 1.0
    n = len(closes)
    return [(i / (n - 1) * width, height - (c - lo) / span * height) for i, c in enumerate(closes)]


class SparklineDelegate(QStyledItemDelegate):
    """Paints a mini price line from the closes stored on the cell."""

    def __init__(self, table: QTableWidget, up: str, down: str) -> None:
        super().__init__(table)
        self._up = QColor(up)
        self._down = QColor(down)

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        closes = index.data(_SPARK_ROLE)
        if not closes or len(closes) < 2:
            return
        rect = option.rect.adjusted(4, 4, -4, -4)
        pts = sparkline_points(closes, rect.width(), rect.height())
        color = self._up if closes[-1] >= closes[0] else self._down
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(color, 1.2))
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            painter.drawLine(
                int(rect.left() + x1),
                int(rect.top() + y1),
                int(rect.left() + x2),
                int(rect.top() + y2),
            )
        painter.restore()


class WatchlistPanel(Panel):
    title = "Watchlist"

    #: A market the user clicked (Kraken wsname, e.g. "XBT/USD").
    pair_selected = Signal(str)

    HEADERS = ["Market", "Price", "24h %", "Volume", "Chart"]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        theme = get_theme(load_theme_name())

        self._layout.addWidget(PanelHeader("Watchlist"))

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setShowGrid(False)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(self.HEADERS)):
            head.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setItemDelegateForColumn(
            4, SparklineDelegate(self.table, theme.positive, theme.negative)
        )
        self.table.cellClicked.connect(self._on_click)
        self._layout.addWidget(self.table, 1)

        self.status = QLabel("Loading markets…")
        self.status.setProperty("role", "subtitle")
        self._layout.addWidget(self.status)

        self._rows: list[str] = []  # ws pair per table row, for click routing

        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        QTimer.singleShot(0, self._poll)

    def _on_click(self, row: int, _col: int) -> None:
        if 0 <= row < len(self._rows):
            self.pair_selected.emit(self._rows[row])

    def _poll(self) -> None:
        if not self.isVisible():
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        gen = self._begin_load()
        try:
            async with KrakenRESTClient(api_key="", api_secret="") as client:
                tickers = await client.get_tickers(_PAIRS)
                tickers.sort(key=lambda t: t.volume, reverse=True)
                sparks: dict[str, list[float]] = {}
                for t in tickers:
                    try:
                        closes = await client.get_ohlc_closes(to_ws_pair(t.symbol), interval=60)
                        sparks[t.symbol] = [c for _ts, c in closes][-24:]
                    except KrakenError:
                        sparks[t.symbol] = []
        except KrakenError as e:
            if self._is_current(gen):
                self.status.setText(f"Markets unavailable: {e}")
            return
        if not self._is_current(gen):
            return
        self._render(tickers, sparks)
        self.status.setText("" if tickers else "No market data.")

    def _render(self, tickers, sparks) -> None:
        theme = get_theme(load_theme_name())
        self.table.setRowCount(len(tickers))
        self._rows = []
        for row, t in enumerate(tickers):
            ws = to_ws_pair(t.symbol)
            self._rows.append(ws)
            pct = pct_change(t.last, t.open)
            self.table.setItem(row, 0, _cell(t.symbol, Qt.AlignmentFlag.AlignLeft))
            self.table.setItem(row, 1, _cell(f"{t.last:,.8g}"))
            pct_item = _cell(f"{pct:+.2f}%")
            pct_item.setForeground(QColor(theme.positive if pct >= 0 else theme.negative))
            self.table.setItem(row, 2, pct_item)
            self.table.setItem(row, 3, _cell(format_compact(t.volume)))
            spark = QTableWidgetItem()
            spark.setData(_SPARK_ROLE, sparks.get(t.symbol, []))
            self.table.setItem(row, 4, spark)

    def refresh(self) -> None:
        self._poll()


def _cell(text: str, align=Qt.AlignmentFlag.AlignRight) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
    return item
