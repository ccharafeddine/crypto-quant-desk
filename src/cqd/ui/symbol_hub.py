"""Active-symbol bus: one source of truth for the selected market.

Any panel can push a selection in any spelling via `set`; the hub normalizes it
to a canonical `Symbol` and, only when it actually changed, emits `changed`. The
main window fans that one signal out to the chart, depth, tape, ticket, and the
stream subscriptions - so a click in the watchlist or a pick in the ticket steer
everything together, and the feedback (ticket -> hub -> ticket) can't loop
because an unchanged symbol is swallowed.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from cqd.data.symbols import Symbol, parse_symbol


class SymbolHub(QObject):
    changed = Signal(object)  # a Symbol

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._current: Symbol | None = None

    @property
    def current(self) -> Symbol | None:
        return self._current

    def set(self, text: str) -> None:
        """Select a market (any spelling). No-op + no signal if unchanged."""
        if not text:
            return
        try:
            symbol = parse_symbol(text)
        except Exception:
            return  # an unparseable selection is ignored, never fatal
        if symbol == self._current:
            return
        self._current = symbol
        self.changed.emit(symbol)
