"""Depth panel: bid/ask ladder for the ticket's selected pair.

Polls the public REST Depth endpoint every 2.5s while the panel is visible.
(Kraken's WS v2 book channel requires checksummed local book maintenance; REST
polling is the accepted v1 simplification - the endpoint is public and cheap.)
"""

from __future__ import annotations

import asyncio

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHeaderView, QLabel, QTableWidget, QTableWidgetItem

from cqd.data.errors import KrakenError
from cqd.data.rest import KrakenRESTClient
from cqd.ui.panels.base import Panel
from cqd.ui.theme import get_theme, load_theme_name
from cqd.ui.widgets import PanelHeader

_LEVELS = 10
_POLL_MS = 2500


class BookPanel(Panel):
    title = "Depth"

    HEADERS = ["Bid vol", "Bid", "Ask", "Ask vol"]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pair: str | None = None  # friendly form, e.g. "XBTUSD"

        header = PanelHeader("Depth")
        self.pair_label = QLabel("-")
        self.pair_label.setProperty("role", "subtitle")
        header.add_left(self.pair_label)
        self._layout.addWidget(header)

        self.table = QTableWidget(_LEVELS, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self._layout.addWidget(self.table, 1)

        self.status = QLabel("Waiting for a pair from the ticket panel")
        self.status.setProperty("role", "subtitle")
        self._layout.addWidget(self.status)

        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def set_pair(self, friendly: str) -> None:
        """Follow the ticket's selection (friendly form, e.g. XBTUSD)."""
        self._pair = friendly
        self.pair_label.setText(friendly)
        self._poll()

    def _poll(self) -> None:
        if self._pair and self.isVisible():
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
        green = QColor(theme.positive)
        red = QColor(theme.negative)
        bids = depth.get("bids", [])[:_LEVELS]
        asks = depth.get("asks", [])[:_LEVELS]
        self.table.clearContents()
        for i in range(_LEVELS):
            if i < len(bids):
                price, vol = bids[i]
                vol_item = _cell(f"{vol:,.4f}")
                px_item = _cell(f"{price:,.8g}")
                px_item.setForeground(green)
                self.table.setItem(i, 0, vol_item)
                self.table.setItem(i, 1, px_item)
            if i < len(asks):
                price, vol = asks[i]
                px_item = _cell(f"{price:,.8g}")
                px_item.setForeground(red)
                self.table.setItem(i, 2, px_item)
                self.table.setItem(i, 3, _cell(f"{vol:,.4f}"))

    def refresh(self) -> None:
        self._poll()


def _cell(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
    return item
