"""Shared UI widgets: a small badge/pill and a styled panel header.

Presentation only. Styling is driven by the theme template via the `role` /
objectName selectors; these classes just set those and the layout.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

# Only the error state surfaces a Retry button; loading/ok/empty are
# informational (FRONTEND_GUIDELINES: error = message + Retry, panel stays
# mounted). Pure so the contract is testable without a QApplication.
_RETRYABLE = frozenset({"error"})


def status_shows_retry(kind: str) -> bool:
    """Whether a status of `kind` should show the Retry button."""
    return kind in _RETRYABLE


class Badge(QLabel):
    """A small outlined chip that hugs its text (never spans full width).

    `kind` picks the outline colour: "" (accent), "neutral", "live", "go", "warn".
    """

    def __init__(self, text: str = "", parent: QWidget | None = None, kind: str = "") -> None:
        super().__init__(text, parent)
        self.setProperty("role", "badge")
        self.setProperty("kind", kind)
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


class PanelStatus(QWidget):
    """A panel's footer status line: a subtitle message plus a Retry button that
    appears only in the error state and re-runs the panel's refresh().

    Drop-in for the old `QLabel` status: `setText()` still shows a plain message
    with no button, so existing loading/ok/empty call sites keep working; only
    error paths switch to `error()` to get the Retry affordance the spec requires.
    """

    def __init__(
        self,
        text: str = "",
        on_retry: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(8)
        self._label = QLabel(text)
        self._label.setProperty("role", "subtitle")
        self._label.setWordWrap(True)
        self._lay.addWidget(self._label, 1)
        self._retry = QPushButton("Retry")
        self._retry.setProperty("role", "retry")
        self._retry.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._retry.setVisible(False)
        if on_retry is not None:
            self._retry.clicked.connect(lambda: on_retry())
        self._lay.addWidget(self._retry)

    def _set(self, kind: str, text: str) -> None:
        self._label.setText(text)
        self._retry.setVisible(status_shows_retry(kind))

    # QLabel-compatible: an informational message, no Retry.
    def setText(self, text: str) -> None:
        self._set("ok", text)

    def text(self) -> str:
        return self._label.text()

    def loading(self, text: str = "Loading…") -> None:
        self._set("loading", text)

    def empty(self, text: str) -> None:
        self._set("empty", text)

    def error(self, text: str) -> None:
        """Show an error message and reveal the Retry button."""
        self._set("error", text)


def trim_decimal_text(value: float, decimals: int) -> str:
    """'0.00000000' -> '0', '2.50000000' -> '2.5': the spin box keeps its full
    precision for input but never displays a wall of zeros."""
    text = f"{value:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


class TrimmedDoubleSpinBox(QDoubleSpinBox):
    """A QDoubleSpinBox that accepts 8 decimals but displays trimmed values."""

    def textFromValue(self, value: float) -> str:  # noqa: N802 - Qt naming
        return trim_decimal_text(value, self.decimals())


def set_kind(widget: QWidget, prop: str, value: str) -> None:
    """Change a styling property (e.g. a Badge's kind, a label's tone) and
    re-polish so the stylesheet picks it up immediately."""
    widget.setProperty(prop, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
