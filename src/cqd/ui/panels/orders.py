"""Open Orders panel: working orders (paper + live) with cancel actions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
)

from cqd.data.client import resolve_demo
from cqd.data.credentials import kraken_keys_present
from cqd.data.errors import KrakenError
from cqd.data.normalize import slash_symbol
from cqd.data.rest import KrakenRESTClient
from cqd.ui import services
from cqd.ui.panels.base import Panel
from cqd.ui.widgets import PanelHeader, PanelStatus


@dataclass
class OrderRow:
    """Render-ready open order (pure mapping target, testable)."""

    txid: str
    mode: str  # "paper" | "live"
    pair: str
    side: str
    ordertype: str
    volume: float
    price_str: str


def live_order_to_row(txid: str, info: dict) -> OrderRow:
    """Map one Kraken OpenOrders entry to a render row."""
    descr = info.get("descr", {}) if isinstance(info, dict) else {}
    raw_pair = str(descr.get("pair", ""))
    try:
        pair = slash_symbol(raw_pair) if raw_pair else "?"
    except Exception:  # noqa: BLE001 - display must never crash on odd pairs
        pair = raw_pair or "?"
    price = str(descr.get("price") or descr.get("price2") or "")
    return OrderRow(
        txid=txid,
        mode="live",
        pair=pair,
        side=str(descr.get("type", "?")),
        ordertype=str(descr.get("ordertype", "?")),
        volume=float(info.get("vol", 0.0) or 0.0),
        price_str=price or "market",
    )


class OrdersPanel(Panel):
    title = "Open orders"

    HEADERS = ["Mode", "Pair", "Side", "Type", "Volume", "Price", ""]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[OrderRow] = []

        header = PanelHeader("Open orders")
        self._layout.addWidget(header)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        self._layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        self.cancel_all_btn = QPushButton("Cancel all")
        self.cancel_all_btn.clicked.connect(self._on_cancel_all)
        buttons.addWidget(self.cancel_all_btn)
        buttons.addStretch(1)
        self._layout.addLayout(buttons)

        self.status = PanelStatus("Not loaded", self.refresh)
        self._layout.addWidget(self.status)

        asyncio.ensure_future(self.load())

    async def load(self) -> None:
        gen = self._begin_load()
        self.status.setText("Loading...")
        rows: list[OrderRow] = [
            OrderRow(
                txid=o.txid,
                mode="paper",
                pair=o.pair,
                side=o.side,
                ordertype=o.ordertype,
                volume=o.volume,
                price_str=f"{o.price:,.8g}" if o.price is not None else "market",
            )
            for o in services.paper_broker().open_orders()
        ]
        error: str | None = None
        if kraken_keys_present() and not resolve_demo():
            try:
                async with KrakenRESTClient() as client:
                    live = await client.get_open_orders()
                rows += [live_order_to_row(txid, info) for txid, info in live.items()]
            except KrakenError as e:
                error = f"Live orders unavailable: {e}"
        if not self._is_current(gen):
            return
        self._rows = rows
        self._populate()
        if error:
            self.status.error(error)
        elif rows:
            self.status.setText(f"{len(rows)} open order(s)")
        else:
            self.status.empty("No open orders.")

    def _populate(self) -> None:
        self.table.clearContents()
        self.table.setRowCount(len(self._rows))
        for i, row in enumerate(self._rows):
            self.table.setItem(i, 0, _cell(row.mode.upper(), align_left=True))
            self.table.setItem(i, 1, _cell(row.pair, align_left=True))
            self.table.setItem(i, 2, _cell(row.side))
            self.table.setItem(i, 3, _cell(row.ordertype))
            self.table.setItem(i, 4, _cell(f"{row.volume:,.8g}"))
            self.table.setItem(i, 5, _cell(row.price_str))
            btn = QPushButton("Cancel")
            # Compact style: the default button padding clips inside table rows.
            btn.setProperty("role", "table-action")
            btn.clicked.connect(lambda _c=False, txid=row.txid: self._on_cancel(txid))
            self.table.setCellWidget(i, 6, btn)

    def _on_cancel(self, txid: str) -> None:
        asyncio.ensure_future(self._cancel(txid))

    async def _cancel(self, txid: str) -> None:
        result = await services.order_service().cancel(txid)
        self.status.setText(
            f"Cancel {txid}: {result.detail}" if result.detail else f"Cancelled {txid}"
        )
        await self.load()

    def _on_cancel_all(self) -> None:
        asyncio.ensure_future(self._cancel_all())

    async def _cancel_all(self) -> None:
        result = await services.order_service().cancel_all()
        self.status.setText(result.detail or "Cancelled all")
        await self.load()

    def refresh(self) -> None:
        asyncio.ensure_future(self.load())


def _cell(text: str, align_left: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(
        Qt.AlignmentFlag.AlignVCenter
        | (Qt.AlignmentFlag.AlignLeft if align_left else Qt.AlignmentFlag.AlignRight)
    )
    return item
