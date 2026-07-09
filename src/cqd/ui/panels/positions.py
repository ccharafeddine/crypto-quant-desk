"""Positions panel: table of holdings with mark, USD value, and cost basis.

Pulls live balances and marks via the Kraken CLI wrapper, and trade history for
cost basis. Cost basis (average cost + break-even) is computed by the engine's
reconstruct_cost_basis; the panel only formats the result.
"""

from __future__ import annotations

import asyncio

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
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
    """
    if cb is None or cb.avg_cost is None or cb.avg_cost <= 0:
        return ("-", "-")
    return (f"${cb.avg_cost:,.6f}", f"${cb.break_even_price:,.6f}")


class PositionsPanel(Panel):
    title = "Positions"

    HEADERS = ["Asset", "Quantity", "Mark", "Value (USD)", "Avg cost", "Break-even"]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._layout.addWidget(PanelHeader("Positions"))

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self._layout.addWidget(self.table)

        self.status = QLabel("Not loaded")
        self.status.setProperty("role", "subtitle")
        self._layout.addWidget(self.status)

        # Auto-load on construction.
        asyncio.ensure_future(self.load())

    async def load(self) -> None:
        self.status.setText("Loading...")
        try:
            client = make_client()
            async with client as client:
                balances = await client.get_balance()
                marks = await self._marks_for_balance(client, balances)
                trades = await client.get_trades()
                self._populate(balances, marks, trades)
            self.status.setText("Loaded")
        except Exception as e:  # noqa: BLE001
            self.status.setText(f"Error: {e}")

    async def _marks_for_balance(
        self, client: KrakenClient, balances: dict[str, float]
    ) -> dict[str, float]:
        assets = [a for a, v in balances.items() if v and v > 0 and a != "USD"]
        # CLI input is the friendly form "BTCUSD"; marks come back slash-keyed.
        pairs = [f"{a}USD" for a in assets]
        if not pairs:
            return {}
        try:
            return await client.get_marks(pairs)
        except Exception:  # noqa: BLE001
            # Some pairs may not exist; fall back to per-pair fetches.
            out: dict[str, float] = {}
            for a in assets:
                try:
                    out.update(await client.get_marks([f"{a}USD"]))
                except Exception:  # noqa: BLE001
                    continue
            return out

    def _populate(
        self,
        balances: dict[str, float],
        marks: dict[str, float],
        trades: list[dict],
    ) -> None:
        rows = [(a, q) for a, q in balances.items() if q and q > 0]
        rows.sort(key=lambda r: r[0])

        # Clear stale items before resizing so no orphaned cells linger.
        self.table.clearContents()
        self.table.setRowCount(len(rows))
        for i, (asset, qty) in enumerate(rows):
            symbol = f"{asset}/USD"
            mark = float(marks.get(symbol) or 0.0)
            value = qty * mark if mark else 0.0
            avg_str, be_str = format_cost_basis(_safe_cost_basis(trades, asset))

            self.table.setItem(i, 0, _cell(asset, align_left=True))
            self.table.setItem(i, 1, _cell(f"{qty:,.6f}"))
            self.table.setItem(i, 2, _cell(f"${mark:,.6f}" if mark else "-"))
            self.table.setItem(i, 3, _cell(f"${value:,.2f}" if mark else "-"))
            self.table.setItem(i, 4, _cell(avg_str))
            self.table.setItem(i, 5, _cell(be_str))

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
