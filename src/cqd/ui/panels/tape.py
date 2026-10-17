"""Time & Sales tape: streaming public trades for the active symbol.

Rides the WebSocket `trade` channel via StreamBridge - each trade prepends a row
(newest on top). Buy/sell are neutral side labels (green/red is reserved
for profit and loss), quantities are fixed-notation (never 6e-05). Only trades for the active symbol are
shown; the list is capped so it never grows unbounded.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHeaderView, QLabel, QTableWidget, QTableWidgetItem

from cqd.ui.format import format_price, format_qty
from cqd.ui.panels.base import Panel
from cqd.ui.theme import get_theme, load_theme_name
from cqd.ui.widgets import PanelHeader

_MAX_ROWS = 100


def format_trade_time(iso: str) -> str:
    """Kraken ISO timestamp -> 'HH:MM:SS' (or '' when absent/unparseable)."""
    if "T" in iso:
        return iso.split("T", 1)[1][:8]
    return iso[:8]


class TapePanel(Panel):
    title = "Time & Sales"

    HEADERS = ["Time", "Price", "Size", "Side"]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._symbol: str | None = None  # WS v2 form, e.g. "BTC/USD"

        header = PanelHeader("Time & Sales")
        self.symbol_label = QLabel("-")
        self.symbol_label.setProperty("role", "subtitle")
        header.add_left(self.symbol_label)
        self._layout.addWidget(header)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._layout.addWidget(self.table, 1)

        self.status = QLabel("Select a market to see its tape.")
        self.status.setProperty("role", "subtitle")
        self._layout.addWidget(self.status)

    def set_symbol(self, symbol: str) -> None:
        """Follow the active symbol (WS v2 form). Clears the tape on a change."""
        if symbol and symbol != self._symbol:
            self._symbol = symbol
            self.symbol_label.setText(symbol)
            self.table.setRowCount(0)
            self.status.setText(f"Waiting for trades on {symbol}…")

    def on_trade(self, trade) -> None:
        """Prepend one streamed trade if it's for the active symbol."""
        if self._symbol is None or trade.symbol != self._symbol:
            return
        self.status.setText("")
        theme = get_theme(load_theme_name())
        self.table.insertRow(0)
        time_item = _cell(format_trade_time(trade.timestamp), Qt.AlignmentFlag.AlignLeft)
        price_item = _cell(format_price(trade.price))
        side_item = _cell(format_side(trade.side), Qt.AlignmentFlag.AlignLeft)
        side_item.setForeground(QColor(theme.text if trade.side == "buy" else theme.text_muted))
        self.table.setItem(0, 0, time_item)
        self.table.setItem(0, 1, price_item)
        self.table.setItem(0, 2, _cell(format_qty(trade.qty)))
        self.table.setItem(0, 3, side_item)
        while self.table.rowCount() > _MAX_ROWS:
            self.table.removeRow(self.table.rowCount() - 1)


def format_side(side: str | None) -> str:
    """Neutral side label: 'buy' -> 'BUY', missing -> '—'."""
    return side.upper() if side else "—"


def _cell(text: str, align=Qt.AlignmentFlag.AlignRight) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
    return item
