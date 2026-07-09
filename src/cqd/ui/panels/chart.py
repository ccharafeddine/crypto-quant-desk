"""Chart panel: price + cost basis overlay.

Placeholder pending the real chart. v0.3 will load OHLC via the Kraken CLI and
draw the cost basis / break-even lines with recent fills as markers.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel

from cqd.ui.panels.base import Panel
from cqd.ui.widgets import PanelHeader


class ChartPanel(Panel):
    title = "Chart"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._layout.addWidget(PanelHeader("Chart"))

        placeholder = QLabel(
            "Portfolio chart, coming in v0.3.\n"
            "Price with cost-basis and break-even overlays, recent fills as markers."
        )
        placeholder.setProperty("role", "subtitle")
        placeholder.setWordWrap(True)
        self._layout.addWidget(placeholder)
        self._layout.addStretch()
