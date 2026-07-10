"""Adjustable card-panel workspace (Qt Advanced Docking System).

Replaces the old fixed-region QDockWidget cockpit. Every panel becomes a
``CDockWidget`` the user can float, tab, split, and freely resize; the whole
arrangement saves/restores to QSettings, and three named perspectives
(Trading / Analysis / Monitor) ship as presets alongside any the user saves.

The placement *specs* below are pure data and are validated without Qt
(`validate_layout`); the `Workspace` class is the thin Qt-facing wrapper.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QWidget

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

# The nine panels of the current app. Keys are the CDockWidget objectNames
# (stable ids the QtAds save/restore keys off); titles are the tab labels.
PANEL_KEYS: tuple[str, ...] = (
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
)

DEFAULT_PERSPECTIVE = "Trading"
PRESET_NAMES: tuple[str, ...] = ("Trading", "Analysis", "Monitor")

# QSettings keys for the last-session layout blob and its schema token.
STATE_KEY = "workspace/state"
VERSION_KEY = "workspace/panelset"
# Token that changes whenever the panel set changes, so saved perspectives/state
# from an older panel set are discarded (else a new panel never gets placed).
LAYOUT_VERSION = "|".join(PANEL_KEYS)

# Each preset places all nine panels exactly once; every non-first target is a
# key placed earlier in the same list (enforced by validate_layout + tests).
LAYOUTS: dict[str, list[tuple[str, object, str | None]]] = {
    # Trading: chart-centric. Watchlist far left, holdings/risk/perf next,
    # chart center, order entry + depth right, orders/alerts/analyst bottom.
    "Trading": [
        ("chart", _C, None),
        ("positions", _L, "chart"),
        ("watchlist", _L, "positions"),
        ("risk", _C, "positions"),
        ("performance", _C, "positions"),
        ("ticket", _R, "chart"),
        ("book", _C, "ticket"),
        ("tape", _C, "book"),
        ("orders", _B, "chart"),
        ("alerts", _C, "orders"),
        ("analyst", _C, "orders"),
        ("analytics", _C, "performance"),
    ],
    # Analysis: performance + risk are the headline; chart drops to a strip,
    # order-entry tucks to the side, watchlist tabs with holdings.
    "Analysis": [
        ("performance", _C, None),
        ("risk", _C, "performance"),
        ("positions", _L, "performance"),
        ("watchlist", _C, "positions"),
        ("book", _C, "positions"),
        ("tape", _C, "book"),
        ("analyst", _R, "performance"),
        ("ticket", _C, "analyst"),
        ("chart", _B, "performance"),
        ("orders", _C, "chart"),
        ("alerts", _C, "chart"),
        ("analytics", _C, "performance"),
    ],
    # Monitor: passive watching. Watchlist + holdings + orders + alerts
    # dominate; a chart and depth ride the right, entry panels tab away.
    "Monitor": [
        ("watchlist", _C, None),
        ("positions", _R, "watchlist"),
        ("orders", _B, "positions"),
        ("alerts", _C, "orders"),
        ("chart", _R, "positions"),
        ("book", _C, "chart"),
        ("tape", _C, "chart"),
        ("risk", _R, "orders"),
        ("performance", _C, "positions"),
        ("ticket", _C, "chart"),
        ("analyst", _C, "orders"),
        ("analytics", _C, "performance"),
    ],
}


def validate_layout(placements: list[tuple[str, object, str | None]]) -> None:
    """Raise ValueError if a placement list is malformed.

    Guarantees every panel is placed exactly once, the first placement has no
    target, every later target was placed before it, and every area is valid.
    Pure (no Qt objects touched beyond the area sentinels).
    """
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
    missing = set(PANEL_KEYS) - seen
    if missing:
        raise ValueError(f"layout omits panels: {sorted(missing)}")


class Workspace:
    """Owns the QtAds dock manager and the app's dock widgets."""

    def __init__(self, host: QMainWindow) -> None:
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
        # Constructing with the main window as parent installs the manager as
        # its central widget.
        self.manager = CDockManager(host)
        # QtAds ships a default stylesheet on the manager (palette()-based, and
        # it defines the title-bar button icons). Keep it as the base layer so
        # the icons survive; theme QSS is appended on top of it (see apply_theme).
        self._ads_default_qss = self.manager.styleSheet()
        self._docks: dict[str, CDockWidget] = {}

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
        """Rebuild the arrangement to a named preset (detaches all first, then
        re-adds per the spec). Panel widgets survive the rebuild."""
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

    def reset(self) -> None:
        self.apply_layout(DEFAULT_PERSPECTIVE)

    # ---- perspectives ----

    def _panelset_current(self, settings: QSettings) -> bool:
        return str(settings.value(VERSION_KEY, "")) == LAYOUT_VERSION

    def ensure_presets(self, settings: QSettings) -> None:
        """Load saved perspectives, then build any missing shipped preset so
        the three are always available. Leaves the docks in the default
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
