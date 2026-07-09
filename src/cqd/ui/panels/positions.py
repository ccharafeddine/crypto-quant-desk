"""Positions panel: table of holdings with mark, USD value, and cost basis.

Pulls live balances and marks via the Kraken CLI wrapper, and trade history for
cost basis. Cost basis (average cost + break-even) is computed by the engine's
reconstruct_cost_basis; the panel only formats the result.
"""

from __future__ import annotations

import asyncio

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
)

from cqd.data.client import make_client
from cqd.data.exchange import KrakenClient
from cqd.engine.cost_basis import CostBasisResult, reconstruct_cost_basis
from cqd.ui.panels.base import Panel
from cqd.ui.widgets import PanelHeader


def format_cost_basis(cb: CostBasisResult | None) -> tuple[str, str]:
    """(avg cost, break-even) cells for a CostBasisResult.

    Returns "-" for both when there is no usable basis: the asset has no trade
    history (held via transfer-in, or none in the window), or its net position
    is fully closed. reconstruct_cost_basis returns a zero result (avg_cost=0)
    in those cases rather than None, so a non-positive avg_cost also maps to "-".

    Basis is denominated in the trade's quote currency: USD renders as "$x",
    anything else is labeled explicitly ("0.00012345 BTC"), never mislabeled USD.
    """
    if cb is None or cb.avg_cost is None or cb.avg_cost <= 0:
        return ("-", "-")
    if cb.quote == "USD":
        return (f"${cb.avg_cost:,.6f}", f"${cb.break_even_price:,.6f}")
    return (
        f"{cb.avg_cost:,.8f} {cb.quote}",
        f"{cb.break_even_price:,.8f} {cb.quote}",
    )


class PositionsPanel(Panel):
    title = "Positions"

    HEADERS = ["Asset", "Quantity", "Mark", "Value (USD)", "Avg cost", "Break-even"]

    #: Emitted from the row context menu; the ticket pre-fills a market sell.
    close_requested = Signal(str, float)  # asset, quantity
    #: Emitted after each populate so the stream can subscribe held assets.
    symbols_available = Signal(list)  # ["BTC/USD", ...]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._row_data: list[tuple[str, float]] = []
        self._live_marks: dict[str, float] = {}

        self._layout.addWidget(PanelHeader("Positions"))

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self._layout.addWidget(self.table)

        self.status = QLabel("Not loaded")
        self.status.setProperty("role", "subtitle")
        self._layout.addWidget(self.status)

        # Auto-load on construction.
        asyncio.ensure_future(self.load())

    async def load(self) -> None:
        gen = self._begin_load()
        self.status.setText("Loading...")
        try:
            client = make_client()
            async with client as client:
                balances = await client.get_balance()
                marks = await self._marks_for_balance(client, balances)
                trades = await client.get_trades()
            if not self._is_current(gen):
                return  # a newer load owns the UI now
            self._populate(balances, marks, trades)
            self.status.setText("Loaded")
        except Exception as e:  # noqa: BLE001
            if self._is_current(gen):
                self.status.setText(f"Error: {e}")

    async def _marks_for_balance(
        self, client: KrakenClient, balances: dict[str, float]
    ) -> dict[str, float]:
        assets = [a for a, v in balances.items() if v and v > 0 and a != "USD"]
        # CLI input is the friendly form "BTCUSD"; marks come back slash-keyed.
        # get_marks degrades per pair itself (bad pairs are simply absent).
        pairs = [f"{a}USD" for a in assets]
        if not pairs:
            return {}
        return await client.get_marks(pairs)

    def _populate(
        self,
        balances: dict[str, float],
        marks: dict[str, float],
        trades: list[dict],
    ) -> None:
        rows = [(a, q) for a, q in balances.items() if q and q > 0]
        rows.sort(key=lambda r: r[0])
        self._row_data = rows
        self.symbols_available.emit([f"{a}/USD" for a, _ in rows if a != "USD"])

        # Clear stale items before resizing so no orphaned cells linger.
        self.table.clearContents()
        self.table.setRowCount(len(rows))
        for i, (asset, qty) in enumerate(rows):
            symbol = f"{asset}/USD"
            # USD cash has no USD/USD market: its mark is 1.0 by definition, so
            # the row shows real value instead of dashes (audit finding 8).
            if asset == "USD":
                mark = 1.0
            else:
                mark = float(marks.get(symbol) or 0.0)
            value = qty * mark if mark else 0.0
            avg_str, be_str = format_cost_basis(_safe_cost_basis(trades, asset))

            self.table.setItem(i, 0, _cell(asset, align_left=True))
            self.table.setItem(i, 1, _cell(f"{qty:,.6f}"))
            self.table.setItem(i, 2, _cell(f"${mark:,.6f}" if mark else "-"))
            self.table.setItem(i, 3, _cell(f"${value:,.2f}" if mark else "-"))
            self.table.setItem(i, 4, _cell(avg_str))
            self.table.setItem(i, 5, _cell(be_str))

    # ---------- live stream ----------

    def on_tick(self, symbol: str, price: float) -> None:
        """Update mark/value in place; flash by direction (never a rebuild)."""
        asset = symbol.split("/")[0]
        for i, (a, qty) in enumerate(self._row_data):
            if a != asset:
                continue
            mark_item = self.table.item(i, 2)
            value_item = self.table.item(i, 3)
            if mark_item is None or value_item is None:
                return
            prev = self._live_marks.get(symbol)
            self._live_marks[symbol] = price
            mark_item.setText(f"${price:,.6f}")
            value_item.setText(f"${qty * price:,.2f}")
            if prev is not None and price != prev:
                self._flash(value_item, up=price > prev)
            return

    def _flash(self, item: QTableWidgetItem, *, up: bool) -> None:
        from cqd.ui.theme import get_theme, load_theme_name

        theme = get_theme(load_theme_name())
        color = QColor(theme.positive if up else theme.negative)
        color.setAlpha(64)  # 25% per the motion rules
        item.setBackground(QBrush(color))

        def clear() -> None:
            try:
                item.setBackground(QBrush())
            except RuntimeError:
                pass  # row was rebuilt while the flash was pending

        QTimer.singleShot(400, clear)

    def _on_context_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0 or row >= len(self._row_data):
            return
        asset, qty = self._row_data[row]
        if asset == "USD":
            return  # cash has nothing to close
        menu = QMenu(self)
        action = menu.addAction(f"Close {asset} position (pre-fill sell {qty:g})...")
        if menu.exec(self.table.viewport().mapToGlobal(pos)) is action:
            self.close_requested.emit(asset, float(qty))

    def refresh(self) -> None:
        asyncio.ensure_future(self.load())


def _safe_cost_basis(trades: list[dict], asset: str) -> CostBasisResult | None:
    """Per-asset cost basis; one asset's failure must not break the whole table."""
    try:
        return reconstruct_cost_basis(trades, asset)
    except Exception:  # noqa: BLE001
        return None


def _cell(text: str, align_left: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(
        Qt.AlignmentFlag.AlignVCenter
        | (Qt.AlignmentFlag.AlignLeft if align_left else Qt.AlignmentFlag.AlignRight)
    )
    return item
