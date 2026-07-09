"""Base panel widget."""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget


class Panel(QWidget):
    """Common base for dockable panels."""

    title: str = "Panel"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(14, 12, 14, 12)
        self._layout.setSpacing(10)

    def refresh(self) -> None:
        """Override in subclasses to reload data."""
