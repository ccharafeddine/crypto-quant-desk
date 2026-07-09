"""Main window with dockable panels (Kraken-Desktop-style layout)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QStatusBar,
    QToolBar,
    QWidget,
)

from cqd.data.client import resolve_demo
from cqd.ui.panels.analyst import AnalystPanel
from cqd.ui.panels.chart import ChartPanel
from cqd.ui.panels.positions import PositionsPanel
from cqd.ui.panels.risk import RiskPanel
from cqd.ui.theme import THEMES, build_qss, get_theme, load_theme_name, save_theme_name
from cqd.ui.widgets import Badge


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._is_demo = resolve_demo()
        self.setWindowTitle(
            "Crypto Quant Desk (Demo data)" if self._is_demo else "Crypto Quant Desk"
        )
        self.resize(1600, 1000)

        self._theme_name = load_theme_name()
        self._apply_theme(self._theme_name)

        # Fixed cockpit regions: no animation, no detaching. Panels carry their
        # own headers (native dock chrome is removed in _add_dock).
        self.setDockOptions(QMainWindow.DockOption.AllowNestedDocks)

        self._build_header()
        self._build_panels()
        self._build_menus()
        self._build_status_bar()

    def _build_header(self) -> None:
        """Slim in-window header bar above the docks (identity + mode badge)."""
        bar = QToolBar("Header", self)
        bar.setObjectName("appHeaderBar")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)

        container = QWidget()
        lay = QHBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        app_title = QLabel("Crypto Quant Desk")
        app_title.setProperty("role", "app-title")
        lay.addWidget(app_title)
        lay.addWidget(Badge("DEMO" if self._is_demo else "LIVE"))
        lay.addStretch(1)
        bar.addWidget(container)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)

    def _build_panels(self) -> None:
        self.positions_panel = PositionsPanel(self)
        self.risk_panel = RiskPanel(self)
        self.chart_panel = ChartPanel(self)
        self.analyst_panel = AnalystPanel(self)

        self._add_dock(
            self.positions_panel, "Positions", Qt.DockWidgetArea.LeftDockWidgetArea
        )
        self._add_dock(self.risk_panel, "Risk", Qt.DockWidgetArea.RightDockWidgetArea)
        self._add_dock(self.chart_panel, "Chart", Qt.DockWidgetArea.RightDockWidgetArea)
        self._add_dock(
            self.analyst_panel, "Analyst", Qt.DockWidgetArea.BottomDockWidgetArea
        )

    def _add_dock(self, widget, title: str, area: Qt.DockWidgetArea) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(f"dock_{title.lower()}")
        dock.setWidget(widget)
        # Fixed region: no close/float/move chrome, and remove the native title
        # bar entirely (an empty title-bar widget) since the panel has its own.
        dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        dock.setTitleBarWidget(QWidget())
        self.addDockWidget(area, dock)
        return dock

    def _build_menus(self) -> None:
        menubar = self.menuBar()

        file_menu: QMenu = menubar.addMenu("&File")
        refresh_action = QAction("Refresh all", self)
        refresh_action.setShortcut("F5")
        refresh_action.triggered.connect(self._refresh_all)
        file_menu.addAction(refresh_action)
        file_menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu: QMenu = menubar.addMenu("&View")
        for dock in self.findChildren(QDockWidget):
            view_menu.addAction(dock.toggleViewAction())

        view_menu.addSeparator()
        self._build_theme_menu(view_menu)

    def _build_theme_menu(self, view_menu: QMenu) -> None:
        theme_menu: QMenu = view_menu.addMenu("Theme")
        group = QActionGroup(self)
        group.setExclusive(True)
        for name in THEMES:
            action = QAction(name, self, checkable=True)
            action.setChecked(name == self._theme_name)
            action.triggered.connect(lambda _checked=False, n=name: self._on_theme_selected(n))
            group.addAction(action)
            theme_menu.addAction(action)

    def _on_theme_selected(self, name: str) -> None:
        self._theme_name = name
        self._apply_theme(name)
        save_theme_name(name)

    def _apply_theme(self, name: str) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_qss(get_theme(name)))

    def _build_status_bar(self) -> None:
        bar = QStatusBar(self)
        self.setStatusBar(bar)
        if self._is_demo:
            bar.showMessage(
                "Demo data (sample portfolio) - add your Kraken API keys to a "
                ".env file to see your live account."
            )
        else:
            bar.showMessage("Ready")

    def _refresh_all(self) -> None:
        for panel in (self.positions_panel, self.risk_panel, self.chart_panel):
            if hasattr(panel, "refresh"):
                panel.refresh()
        self.statusBar().showMessage("Refreshed", 2000)
