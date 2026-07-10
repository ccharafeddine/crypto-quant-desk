"""Depth panel: an order-book ladder for the active pair.

Asks stack above the spread, bids below, each row carrying a cumulative-depth
bar (a translucent rectangle scaled to the running size, painted by
`DepthBarDelegate`). Clicking a price emits `price_clicked` so the ticket can
pre-fill it. Polls the public REST Depth endpoint every 2.5s while visible
(Kraken's WS v2 book needs checksummed local maintenance; REST polling is the
accepted v1 simplification).
"""

from __future__ import annotations

import asyncio

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
)

from cqd.data.errors import KrakenError
from cqd.data.rest import KrakenRESTClient
from cqd.ui.panels.base import Panel
from cqd.ui.theme import get_theme, load_theme_name
from cqd.ui.widgets import PanelHeader

_LEVELS = 12
_POLL_MS = 2500
_DEPTH_ROLE = Qt.ItemDataRole.UserRole  # per-cell cumulative-depth fraction (0..1)


# ---- pure helpers (testable without a QApplication) ----


def cumulative_totals(levels: list[tuple[float, float]]) -> list[float]:
    """Running sum of sizes, best-first (index i = depth through level i)."""
    out: list[float] = []
    run = 0.0
    for _price, size in levels:
        run += size
        out.append(run)
    return out


def format_spread(best_bid: float, best_ask: float) -> str:
    """Absolute spread and its fraction of the mid price, e.g. '0.1 (0.00%)'."""
    mid = (best_ask + best_bid) / 2.0
    if mid <= 0:
        return "-"
    spread = best_ask - best_bid
    return f"{spread:,.8g} ({spread / mid * 100:.2f}%)"


class DepthBarDelegate(QStyledItemDelegate):
    """Paints a cumulative-depth bar behind each row, growing from the left.

    Each cell fills its slice of a single row-wide bar (0..fraction of the
    viewport width), so the segments read as one continuous bar across columns.
    """

    def __init__(self, table: QTableWidget, color: str) -> None:
        super().__init__(table)
        self._table = table
        self._color = QColor(color)
        self._color.setAlphaF(0.18)

    def paint(self, painter, option, index) -> None:
        frac = index.data(_DEPTH_ROLE)
        if frac:
            bar_px = int(float(frac) * self._table.viewport().width())
            fill = QRect(0, option.rect.top(), bar_px, option.rect.height()).intersected(
                option.rect
            )
            if not fill.isEmpty():
                painter.fillRect(fill, self._color)
        super().paint(painter, option, index)


class BookPanel(Panel):
    title = "Depth"

    #: A price the user clicked in the ladder (for the ticket to pre-fill).
    price_clicked = Signal(float)

    HEADERS = ["Price", "Size", "Total"]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pair: str | None = None  # friendly form, e.g. "XBTUSD"
        theme = get_theme(load_theme_name())

        header = PanelHeader("Depth")
        self.pair_label = QLabel("-")
        self.pair_label.setProperty("role", "subtitle")
        header.add_left(self.pair_label)
        self._layout.addWidget(header)

        self.asks_table = self._make_table(theme.negative)
        self._layout.addWidget(self.asks_table, 1)

        self.spread_label = QLabel("")
        self.spread_label.setProperty("role", "footnote")
        self.spread_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(self.spread_label)

        self.bids_table = self._make_table(theme.positive, show_header=False)
        self._layout.addWidget(self.bids_table, 1)

        self.status = QLabel("Waiting for a pair.")
        self.status.setProperty("role", "subtitle")
        self._layout.addWidget(self.status)

        # Best-first price per display row, so a click maps back to its price.
        self._ask_prices: list[float] = []
        self._bid_prices: list[float] = []
        self.asks_table.cellClicked.connect(lambda r, _c: self._emit_click(self._ask_prices, r))
        self.bids_table.cellClicked.connect(lambda r, _c: self._emit_click(self._bid_prices, r))

        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def _make_table(self, price_color: str, *, show_header: bool = True) -> QTableWidget:
        table = QTableWidget(_LEVELS, len(self.HEADERS))
        table.setHorizontalHeaderLabels(self.HEADERS)
        table.horizontalHeader().setVisible(show_header)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setShowGrid(False)
        table.setItemDelegate(DepthBarDelegate(table, price_color))
        return table

    def set_pair(self, friendly: str) -> None:
        """Follow the active symbol (friendly form, e.g. XBTUSD)."""
        self._pair = friendly
        self.pair_label.setText(friendly)
        self._poll()

    def _emit_click(self, prices: list[float], row: int) -> None:
        if 0 <= row < len(prices):
            self.price_clicked.emit(prices[row])

    def _poll(self) -> None:
        if not (self._pair and self.isVisible()):
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        asyncio.ensure_future(self._load(self._pair))

    async def _load(self, pair: str) -> None:
        gen = self._begin_load()
        try:
            async with KrakenRESTClient(api_key="", api_secret="") as client:
                depth = await client.get_depth(pair, count=_LEVELS)
        except KrakenError as e:
            if self._is_current(gen):
                self.status.setText(f"Depth unavailable: {e}")
            return
        if not self._is_current(gen) or pair != self._pair:
            return
        self._render(depth)
        self.status.setText("")

    def _render(self, depth: dict) -> None:
        theme = get_theme(load_theme_name())
        bids = depth.get("bids", [])[:_LEVELS]
        asks = depth.get("asks", [])[:_LEVELS]  # best-first (lowest ask first)
        ask_cum = cumulative_totals(asks)
        bid_cum = cumulative_totals(bids)
        max_total = max([*ask_cum, *bid_cum, 1e-12])

        # Asks: best ask nearest the spread -> render best at the BOTTOM row.
        self.asks_table.clearContents()
        self._ask_prices = [0.0] * _LEVELS
        for display_row, i in enumerate(reversed(range(len(asks)))):
            price, size = asks[i]
            self._fill_row(
                self.asks_table, display_row, price, size, ask_cum[i], max_total, theme.negative
            )
            self._ask_prices[display_row] = price

        # Bids: best bid at the TOP row (natural best-first order).
        self.bids_table.clearContents()
        self._bid_prices = [0.0] * _LEVELS
        for i, (price, size) in enumerate(bids):
            self._fill_row(self.bids_table, i, price, size, bid_cum[i], max_total, theme.positive)
            self._bid_prices[i] = price

        if bids and asks:
            self.spread_label.setText(f"Spread {format_spread(bids[0][0], asks[0][0])}")
        else:
            self.spread_label.setText("")

    def _fill_row(
        self, table, row: int, price: float, size: float, cum: float, max_total: float, color: str
    ) -> None:
        frac = cum / max_total if max_total else 0.0
        price_item = _cell(f"{price:,.8g}")
        price_item.setForeground(QColor(color))
        size_item = _cell(f"{size:,.4f}")
        total_item = _cell(f"{cum:,.4f}")  # cumulative depth through this level
        for col, item in enumerate((price_item, size_item, total_item)):
            item.setData(_DEPTH_ROLE, frac)
            table.setItem(row, col, item)

    def refresh(self) -> None:
        self._poll()


def _cell(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
    return item
