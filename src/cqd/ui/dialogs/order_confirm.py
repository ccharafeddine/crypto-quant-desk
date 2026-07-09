"""Order confirmation dialog: the human checkpoint before any order is sent."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from cqd.trading.orders import PreparedOrder
from cqd.ui.widgets import Badge


def _fmt(value: float | None, *, money: bool = False) -> str:
    if value is None:
        return "-"
    return f"${value:,.2f}" if money else f"{value:,.8f}".rstrip("0").rstrip(".")


class OrderConfirmDialog(QDialog):
    """Summary + explicit confirm. Rejecting invalidates nothing but the click."""

    def __init__(self, prepared: PreparedOrder, parent=None) -> None:
        super().__init__(parent)
        req = prepared.request
        self.setWindowTitle("Confirm order")
        self.setModal(True)

        header = QHBoxLayout()
        title = QLabel(f"{req.side.upper()} {req.pair}")
        title.setProperty("role", "title")
        header.addWidget(title)
        header.addStretch(1)
        badge = Badge("PAPER" if prepared.mode == "paper" else "LIVE")
        header.addWidget(badge)

        rows: list[tuple[str, str]] = [
            ("Order type", req.ordertype),
            ("Volume", f"{_fmt(req.volume)} {req.pair.split('/')[0]}"),
        ]
        if req.price is not None:
            label = "Offset" if req.ordertype.startswith("trailing-stop") else "Price"
            rows.append((label, _fmt(req.price)))
        if req.price2 is not None:
            rows.append(("Limit after trigger", _fmt(req.price2)))
        if req.close_ordertype:
            rows.append(("Conditional close", f"{req.close_ordertype} @ {_fmt(req.close_price)}"))
        rows.append(("Est. value", _fmt(prepared.estimated_value, money=True)))
        if req.mark is not None:
            rows.append(("Current mark", _fmt(req.mark)))

        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        for i, (k, v) in enumerate(rows):
            key = QLabel(k)
            key.setProperty("role", "metric-label")
            val = QLabel(v)
            val.setProperty("role", "metric-value-small")
            grid.addWidget(key, i, 0)
            grid.addWidget(val, i, 1)

        warn = QLabel(
            "Simulated order - never sent to Kraken."
            if prepared.mode == "paper"
            else "REAL order - this will be sent to Kraken with real funds."
        )
        warn.setWordWrap(True)
        warn.setProperty("role", "footnote")

        confirm = QPushButton(f"Confirm {req.side}")
        confirm.setObjectName("buyButton" if req.side == "buy" else "sellButton")
        confirm.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.setDefault(True)  # safe default: Enter cancels, confirm is a click
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(confirm)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        layout.addLayout(header)
        layout.addLayout(grid)
        layout.addWidget(warn)
        layout.addLayout(buttons)
