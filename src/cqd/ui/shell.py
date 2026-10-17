"""The Graphite app shell: left nav, header strip, page title and KPI strip.

Layout (mockups 1-3):

    ┌ header: logo · source · symbol · NAV · day P&L ····· DATA · MODE · stream · New order ┐
    │ nav    │ page title + subtitle                                                       │
    │ rail   │ KPI strip (8 cells, every page)                                             │
    │        │ QtAds cards for the page                                                    │
    └ status: stream · data conventions                                                    ┘

`NAV_SPEC` (pure data) maps each nav item to a workspace page and the card it
brings forward; tests check every target exists. Widgets here only render
what `cqd.ui.kpis` computes - no numbers are derived in this module.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from cqd.ui.kpis import Kpi, KpiState, build_kpis, header_values
from cqd.ui.widgets import Badge, set_kind


@dataclass(frozen=True)
class NavItem:
    label: str
    page: str
    focus: str  # panel key brought to the front


#: (section, items) in display order.
NAV_SPEC: tuple[tuple[str, tuple[NavItem, ...]], ...] = (
    (
        "Portfolio",
        (
            NavItem("Overview", "Portfolio", "overview"),
            NavItem("Positions", "Portfolio", "positions"),
            NavItem("Performance", "Portfolio", "performance"),
        ),
    ),
    (
        "Markets",
        (
            NavItem("Chart & book", "Markets", "chart"),
            NavItem("Watchlist", "Markets", "watchlist"),
            NavItem("Time & sales", "Markets", "tape"),
        ),
    ),
    (
        "Trading",
        (
            NavItem("Order ticket", "Trading", "ticket"),
            NavItem("Open orders", "Trading", "orders"),
            NavItem("Signals & backtest", "Signals", "signals"),
        ),
    ),
    (
        "Risk",
        (
            NavItem("Risk & stress", "Risk", "risk"),
            NavItem("Analytics", "Risk", "analytics"),
            NavItem("Alerts", "Risk", "alerts"),
        ),
    ),
    ("Insight", (NavItem("Analyst", "Insight", "analyst"),)),
)

#: Page title and the one-line context under it.
PAGE_TITLES: dict[str, tuple[str, str]] = {
    "Portfolio": ("Portfolio overview", "time-weighted, net of deposits and withdrawals"),
    "Markets": ("Markets", "Kraken public data · closed daily bars for statistics"),
    "Trading": ("Trading", "every order: validate → confirm → audit · paper by default"),
    "Signals": ("Signals & backtest", "advisory only · costed replay + walk-forward"),
    "Risk": ("Risk & stress", "ex-ante: current weights over closed daily bars"),
    "Insight": ("Insight", "rules-based narration of the computed numbers"),
}


def nav_items() -> list[NavItem]:
    return [item for _section, items in NAV_SPEC for item in items]


class NavRail(QFrame):
    """The left navigation. Emits (page, focus panel) when an item is chosen."""

    navigate = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("navRail")
        self.setFixedWidth(200)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 6)
        lay.setSpacing(0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[tuple[str, str], QPushButton] = {}
        for section, items in NAV_SPEC:
            head = QLabel(section)
            head.setProperty("role", "nav-section")
            lay.addWidget(head)
            for item in items:
                btn = QPushButton(item.label.replace("&", "&&"))  # no mnemonic
                btn.setProperty("role", "nav-item")
                btn.setCheckable(True)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(lambda _c=False, it=item: self.navigate.emit(it.page, it.focus))
                self._group.addButton(btn)
                self._buttons[(item.page, item.focus)] = btn
                lay.addWidget(btn)
        lay.addStretch(1)

    def select(self, page: str, focus: str | None = None) -> None:
        """Check the item for (page, focus), or the page's first item."""
        btn = self._buttons.get((page, focus or ""))
        if btn is None:
            btn = next((b for (p, _f), b in self._buttons.items() if p == page), None)
        if btn is not None:
            btn.setChecked(True)


def _label(text: str, role: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", role)
    return label


class HeaderStrip(QWidget):
    """Brand, account context, NAV and day P&L, data/mode chips, stream state."""

    new_order = Signal()
    refresh = Signal()

    def __init__(self, mark_pixmap=None, parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        if mark_pixmap is not None:
            logo = QLabel()
            logo.setPixmap(mark_pixmap)
            lay.addWidget(logo)
        lay.addWidget(_label("Crypto Quant Desk", "app-title"))
        self.source = _label("Kraken spot", "header-key")
        lay.addWidget(self.source)
        self.symbol = Badge("BTC/USD", kind="neutral")
        lay.addWidget(self.symbol)
        lay.addSpacing(8)
        lay.addWidget(_label("NAV", "header-key"))
        self.nav = _label("—", "header-value")
        lay.addWidget(self.nav)
        lay.addWidget(_label("Day", "header-key"))
        self.day = _label("—", "header-value")
        self.day.setToolTip("Change since the last daily close (00:00 UTC), deposits removed")
        lay.addWidget(self.day)
        lay.addStretch(1)
        self.data_chip = Badge("EXAMPLE DATA", kind="neutral")
        lay.addWidget(self.data_chip)
        self.mode_chip = Badge("PAPER")
        lay.addWidget(self.mode_chip)
        self.stream = _label("● offline", "stream-state")
        self.stream.setProperty("streamState", "offline")
        lay.addWidget(self.stream)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh.emit)
        lay.addWidget(refresh)
        order = QPushButton("New order")
        order.setProperty("role", "primary")
        order.setToolTip("Open the order ticket (validated and confirmed before anything is sent)")
        order.clicked.connect(self.new_order.emit)
        lay.addWidget(order)

    def set_context(self, *, is_demo: bool, mode: str) -> None:
        self.source.setText("Kraken spot · demo book" if is_demo else "Kraken spot · USD book")
        self.data_chip.setVisible(is_demo)
        self.mode_chip.setText(mode.upper())
        set_kind(self.mode_chip, "kind", "live" if mode == "live" else "")

    def set_symbol(self, display: str) -> None:
        self.symbol.setText(display)

    def set_stream(self, state: str) -> None:
        self.stream.setText(f"● stream {state}")
        set_kind(self.stream, "streamState", state)

    def set_state(self, state: KpiState) -> None:
        nav, day, day_tone = header_values(state)
        self.nav.setText(nav)
        self.day.setText(day)
        set_kind(self.day, "tone", day_tone)


class KpiCell(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("kpiCell")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 7, 12, 7)
        lay.setSpacing(1)
        self.label = _label("", "kpi-label")
        self.value = _label("—", "kpi-value")
        self.sub = _label("", "kpi-sub")
        for w in (self.label, self.value, self.sub):
            lay.addWidget(w)

    def set(self, kpi: Kpi) -> None:
        self.label.setText(kpi.label)
        self.value.setText(kpi.value)
        self.sub.setText(kpi.sub)
        set_kind(self.value, "tone", kpi.tone)


class KpiStrip(QFrame):
    """Eight KPI cells shown above every page's cards."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("kpiStrip")
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.cells = [KpiCell(self) for _ in build_kpis(KpiState())]
        for col, cell in enumerate(self.cells):
            cell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            lay.addWidget(cell, 0, col)
        self.set_state(KpiState())

    def set_state(self, state: KpiState) -> None:
        for cell, kpi in zip(self.cells, build_kpis(state), strict=True):
            cell.set(kpi)


class PageTitle(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        self.title = _label("", "page-title")
        self.sub = _label("", "page-sub")
        lay.addWidget(self.title)
        lay.addWidget(self.sub, 1, Qt.AlignmentFlag.AlignBottom)

    def set_page(self, page: str, context: str = "") -> None:
        title, sub = PAGE_TITLES.get(page, (page, ""))
        self.title.setText(title)
        self.sub.setText(" · ".join(x for x in (context, sub) if x))


__all__ = [
    "NAV_SPEC",
    "PAGE_TITLES",
    "HeaderStrip",
    "KpiStrip",
    "NavItem",
    "NavRail",
    "PageTitle",
    "nav_items",
]
