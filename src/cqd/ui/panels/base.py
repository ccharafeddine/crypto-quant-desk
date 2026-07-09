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
        self._load_gen = 0

    def refresh(self) -> None:
        """Override in subclasses to reload data."""

    # ---- overlapping-load guard ----
    # refresh() fires load() without awaiting the previous one, so two loads
    # can overlap and the OLDER snapshot can finish LAST, overwriting fresher
    # data (2026-07-09 audit). Each load takes a generation ticket at start and
    # must not touch the UI once a newer load has started.

    def _begin_load(self) -> int:
        self._load_gen += 1
        return self._load_gen

    def _is_current(self, gen: int) -> bool:
        return gen == self._load_gen
