"""Paged card workspace (Qt Advanced Docking System) behind the left nav.

Every panel is a ``CDockWidget`` the user can float, tab, split and resize.
The Graphite redesign groups them into six PAGES (Portfolio, Markets,
Trading, Signals, Risk, Insight) chosen from the left nav: a page opens only
its own cards, so the old 13-tab soup becomes a handful of readable cards per
page. Each page is a QtAds perspective; the user's rearrangement of a page is
remembered when they switch away, and "Reset layout" rebuilds the shipped
pages. Placement specs are pure data validated without Qt (`validate_layout`).
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QWidget

import PySide6QtAds as qtads
from PySide6QtAds import CDockManager, CDockWidget

# Placement grammar: (panel_key, area, target_key | None). `area` is a QtAds
# DockWidgetArea; `target` is a previously placed key to split/tab against, or
# None to place relative to the whole container. CenterDockWidgetArea against a
# target tabs into that target's area; the four edges split.
_C = qtads.CenterDockWidgetArea
_L = qtads.LeftDockWidgetArea
_R = qtads.RightDockWidgetArea
_B = qtads.BottomDockWidgetArea
_T = qtads.TopDockWidgetArea

_VALID_AREAS = frozenset({_C, _L, _R, _B, _T})

# Every panel in the app. Keys are the CDockWidget objectNames (stable ids the
# QtAds save/restore keys off).
PANEL_KEYS: tuple[str, ...] = (
    "overview",
    "watchlist",
    "positions",
    "risk",
    "performance",
    "chart",
    "book",
    "tape",
    "ticket",
    "orders",
    "alerts",
    "analyst",
    "analytics",
    "signals",
    "lab",
)

DEFAULT_PERSPECTIVE = "Portfolio"
PRESET_NAMES: tuple[str, ...] = ("Portfolio", "Markets", "Trading", "Signals", "Risk", "Insight")

# QSettings keys for the last-session layout blob and its schema token.
STATE_KEY = "workspace/state"
VERSION_KEY = "workspace/panelset"
PAGE_KEY = "workspace/page"
# Changes whenever the panel set or the page model changes, so layouts saved by
# an older version are discarded (else a new panel never gets placed).
LAYOUT_VERSION = "pages-1|" + "|".join(PANEL_KEYS)

# Each page places ITS panels; the first has no target, every later target is
# a key placed earlier on the same page (enforced by validate_layout + tests).
LAYOUTS: dict[str, list[tuple[str, object, str | None]]] = {
    # Overview (equity vs BTC, risk at a glance, positions) with the detail
    # cards one tab away.
    "Portfolio": [
        ("overview", _C, None),
        ("positions", _C, "overview"),
        ("performance", _C, "overview"),
    ],
    # Markets: chart centre, watchlist left, depth + tape right.
    "Markets": [
        ("chart", _C, None),
        ("watchlist", _L, "chart"),
        ("book", _R, "chart"),
        ("tape", _B, "book"),
    ],
    # Trading: chart + order entry, depth beside the ticket, orders below.
    "Trading": [
        ("chart", _C, None),
        ("ticket", _R, "chart"),
        ("book", _C, "ticket"),
        ("orders", _B, "chart"),
    ],
    # Signals & backtest gets the room it needs, with the chart for context.
    "Signals": [
        ("signals", _C, None),
        ("chart", _R, "signals"),
    ],
    "Risk": [
        ("risk", _C, None),
        ("analytics", _R, "risk"),
        ("lab", _C, "analytics"),
        ("alerts", _B, "risk"),
    ],
    "Insight": [
        ("analyst", _C, None),
        ("analytics", _R, "analyst"),
    ],
}

# Pages whose default split should not be even: page -> (area key, ratio).
# The signals card carries its own two-column backtest, so it gets the room.
SPLITS: dict[str, tuple[str, tuple[int, ...]]] = {
    "Signals": ("signals", (62, 38)),
}


def validate_layout(placements: list[tuple[str, object, str | None]]) -> None:
    """Raise ValueError if one page's placement list is malformed.

    Every key is known and placed at most once, the first placement has no
    target, every later target was placed before it, and every area is valid.
    Pure (no Qt objects touched beyond the area sentinels).
    """
    if not placements:
        raise ValueError("a page must place at least one panel")
    seen: set[str] = set()
    for i, (key, area, target) in enumerate(placements):
        if key not in PANEL_KEYS:
            raise ValueError(f"unknown panel key {key!r}")
        if key in seen:
            raise ValueError(f"panel {key!r} placed twice")
        if area not in _VALID_AREAS:
            raise ValueError(f"invalid area for {key!r}")
        if i == 0:
            if target is not None:
                raise ValueError("first placement must have target None")
        else:
            if target is None:
                raise ValueError(f"only the first placement may have target None ({key!r})")
            if target not in seen:
                raise ValueError(f"{key!r} targets {target!r} before it is placed")
        seen.add(key)


def validate_pages(layouts: dict[str, list[tuple[str, object, str | None]]]) -> None:
    """Every page is valid and every panel is reachable from some page."""
    placed: set[str] = set()
    for placements in layouts.values():
        validate_layout(placements)
        placed |= {key for key, _a, _t in placements}
    missing = set(PANEL_KEYS) - placed
    if missing:
        raise ValueError(f"no page shows: {sorted(missing)}")


def page_panels(name: str) -> list[str]:
    """The panel keys a page opens, in placement order."""
    return [key for key, _a, _t in LAYOUTS[name]]


class Workspace:
    """Owns the QtAds dock manager and the app's dock widgets."""

    def __init__(self, host: QWidget) -> None:
        # Live splitter resize and even splits on insertion. NOTE: we do NOT
        # enable FocusHighlighting - it installs an app-global CDockFocusController
        # event filter per manager, and stale filters over torn-down managers
        # segfault qApp.processEvents() (surfaced across the full test suite).
        # E2 styles the active card via QtAds' own active-tab CSS instead.
        CDockManager.setConfigFlag(CDockManager.eConfigFlag.OpaqueSplitterResize, True)
        CDockManager.setConfigFlag(CDockManager.eConfigFlag.FocusHighlighting, False)
        CDockManager.setConfigFlag(CDockManager.eConfigFlag.EqualSplitOnInsertion, True)
        # Keep a strong reference to the host: the manager is a C++ child of it,
        # so if the host were collected the manager would be deleted under us.
        self._host = host
        # With a QMainWindow parent the manager installs itself as the central
        # widget; with a plain QWidget it is a child the caller lays out (the
        # app puts it beside the left nav).
        self.manager = CDockManager(host)
        # QtAds ships a default stylesheet on the manager (palette()-based, and
        # it defines the title-bar button icons). Keep it as the base layer so
        # the icons survive; theme QSS is appended on top of it (see apply_theme).
        self._ads_default_qss = self.manager.styleSheet()
        self._docks: dict[str, CDockWidget] = {}
        self.current_page: str | None = None
        # Shipped pages built (offscreen) this session whose default split has
        # not yet been applied on a real, laid-out show.
        self._split_pending: set[str] = set()

    # ---- registration ----

    def add_panel(self, key: str, title: str, widget: QWidget) -> CDockWidget:
        """Wrap `widget` in a dock card under a stable `key`. Not placed yet;
        `apply_layout` positions it."""
        dock = CDockWidget(self.manager, title)
        dock.setObjectName(key)
        dock.setWidget(widget)
        self._docks[key] = dock
        return dock

    # ---- arrangement ----

    def apply_layout(self, name: str) -> None:
        """Rebuild the arrangement to a shipped page (detaches all first, then
        re-adds per the spec). The page's panels are open; every other panel
        stays registered but closed, so the View menu and saved state still
        know it. Panel widgets survive the rebuild."""
        placements = LAYOUTS[name]
        for dock in list(self.manager.dockWidgetsMap().values()):
            self.manager.removeDockWidget(dock)
        areas: dict[str, object] = {}
        for key, area, target in placements:
            dock = self._docks[key]
            if target is None:
                areas[key] = self.manager.addDockWidget(area, dock)
            else:
                areas[key] = self.manager.addDockWidget(area, dock, areas[target])
        first = placements[0][0]
        for key, dock in self._docks.items():
            if key in areas:
                continue
            self.manager.addDockWidget(_C, dock, areas[first])
            dock.toggleView(False)
        # The first card of the page is the visible tab.
        self._docks[first].setAsCurrentTab()
        if name in SPLITS:
            self._split_pending.add(name)
        self.current_page = name

    def open_panels(self) -> set[str]:
        """Keys of the panels currently open (not closed) in the workspace."""
        return {k for k, d in self._docks.items() if not d.isClosed()}

    def show_page(self, name: str, focus: str | None = None) -> None:
        """Switch to a page, remembering the user's arrangement of the page we
        leave, then bring `focus` (a panel key on that page) to the front."""
        if self.current_page and self.current_page != name:
            self.manager.addPerspective(self.current_page)
        if name in self.perspective_names():
            self.manager.openPerspective(name)
        else:
            self.apply_layout(name)
        self.current_page = name
        self._apply_split(name)
        if focus is not None and focus in self._docks:
            dock = self._docks[focus]
            if dock.isClosed():
                dock.toggleView(True)
            dock.setAsCurrentTab()
            dock.raise_()

    def _apply_split(self, name: str) -> None:
        """Apply a page's default split the first time it is shown for real.
        Presets are built before the window has a size, so sizes set then are
        lost; a user's own arrangement (saved on leaving a page) is kept."""
        if name not in self._split_pending or not self.manager.isVisible():
            return
        key, sizes = SPLITS[name]
        area = self._docks[key].dockAreaWidget()
        if area is None:
            return
        current = list(self.manager.splitterSizes(area))
        if len(current) != len(sizes) or sum(current) <= 0:
            return
        # Qt takes pixels, so scale the ratio to the splitter's real extent.
        total, weight = sum(current), sum(sizes)
        self.manager.setSplitterSizes(area, [round(total * s / weight) for s in sizes])
        self._split_pending.discard(name)

    def reset(self) -> None:
        """Rebuild every shipped page from its spec and open the default."""
        for name in PRESET_NAMES:
            self.apply_layout(name)
            self.manager.addPerspective(name)
        self.apply_layout(DEFAULT_PERSPECTIVE)

    # ---- perspectives ----

    def _panelset_current(self, settings: QSettings) -> bool:
        return str(settings.value(VERSION_KEY, "")) == LAYOUT_VERSION

    def ensure_presets(self, settings: QSettings) -> None:
        """Load saved perspectives, then build any missing shipped preset so
        every page is always available. Leaves the docks in the default
        arrangement (a later restore_state may override it).

        If the saved panel set differs from the current one (a panel was added
        or removed), stale perspectives are discarded first so the new panel
        gets placed by a freshly built preset."""
        self.manager.loadPerspectives(settings)
        if not self._panelset_current(settings):
            for name in self.perspective_names():
                self.manager.removePerspective(name)
            settings.remove(STATE_KEY)
            settings.setValue(VERSION_KEY, LAYOUT_VERSION)
            self.manager.savePerspectives(settings)
        existing = set(self.manager.perspectiveNames())
        for name in PRESET_NAMES:
            if name not in existing:
                self.apply_layout(name)
                self.manager.addPerspective(name)
        self.manager.savePerspectives(settings)
        self.apply_layout(DEFAULT_PERSPECTIVE)

    def open_perspective(self, name: str) -> None:
        self.manager.openPerspective(name)
        if name in PRESET_NAMES:
            self.current_page = name

    def save_perspective(self, name: str, settings: QSettings | None = None) -> None:
        self.manager.addPerspective(name)
        if settings is not None:
            self.manager.savePerspectives(settings)

    def delete_perspective(self, name: str, settings: QSettings | None = None) -> None:
        self.manager.removePerspective(name)
        if settings is not None:
            self.manager.savePerspectives(settings)

    def perspective_names(self) -> list[str]:
        return list(self.manager.perspectiveNames())

    def custom_perspectives(self) -> list[str]:
        """User-saved perspectives (the shipped presets are not deletable)."""
        return [n for n in self.perspective_names() if n not in PRESET_NAMES]

    # ---- persistence ----

    def restore_state(self, settings: QSettings) -> bool:
        """Restore the last-session arrangement. Returns False (leaving the
        default) on absent or corrupt state, never raising (AC10.2)."""
        try:
            if not self._panelset_current(settings):
                return False  # saved layout is for a different panel set
            raw = settings.value(STATE_KEY)
            if raw is None:
                return False
            blob = raw if isinstance(raw, QByteArray) else QByteArray(raw)
            if blob.isEmpty():
                return False
            return bool(self.manager.restoreState(blob))
        except Exception:
            return False

    def save_state(self, settings: QSettings) -> None:
        settings.setValue(STATE_KEY, self.manager.saveState())
        settings.setValue(VERSION_KEY, LAYOUT_VERSION)
        self.manager.savePerspectives(settings)

    # ---- theming ----

    def apply_theme(self, themed_qss: str) -> None:
        """Layer a theme's QtAds QSS over the manager's shipped default so the
        button icons survive and our rules (coming last) win."""
        self.manager.setStyleSheet(f"{self._ads_default_qss}\n{themed_qss}")

    # ---- menu wiring ----

    def toggle_actions(self) -> list[QAction]:
        """One show/hide action per panel, in registration order, for the View
        menu."""
        return [dock.toggleViewAction() for dock in self._docks.values()]
