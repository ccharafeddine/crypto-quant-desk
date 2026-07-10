"""Shared UI widgets: a small badge/pill and a styled panel header.

Presentation only. Styling is driven by the theme template via the `role` /
objectName selectors; these classes just set those and the layout.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget


class Badge(QLabel):
    """A small accent-tinted pill that hugs its text (never spans full width)."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("role", "badge")
        # Maximum policy keeps the pill the width of its text, not the column.
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)


class PanelHeader(QWidget):
    """A panel's title row: name (weighted) with a 1px bottom border.

    Optional small widgets (e.g. a demo Badge) sit just right of the title,
    left-aligned; everything else is pushed away by a trailing stretch.
    """

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("panelHeader")
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 8)
        self._lay.setSpacing(8)
        label = QLabel(title)
        label.setProperty("role", "panel-title")
        self._lay.addWidget(label)
        self._lay.addStretch(1)

    def add_left(self, widget: QWidget) -> None:
        """Insert a widget just after the title (left-aligned, before stretch)."""
        self._lay.insertWidget(1, widget)

    def add_right(self, widget: QWidget) -> None:
        """Append a widget on the right of the header (after the stretch), where
        per-panel controls live (symbol/timeframe selectors, a settings gear)."""
        self._lay.addWidget(widget)
