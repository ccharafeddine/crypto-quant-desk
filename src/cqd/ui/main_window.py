"""Main window with dockable panels (Kraken-Desktop-style layout)."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStatusBar,
    QToolBar,
    QWidget,
)

from cqd.data import credentials
from cqd.data.client import resolve_demo
from cqd.ui import services
from cqd.ui import settings_store as store
from cqd.ui.dialogs.first_run import CHOICE_CONNECT, FirstRunDialog
from cqd.ui.dialogs.settings import SettingsDialog
from cqd.ui.panels.analyst import AnalystPanel
from cqd.ui.panels.chart import ChartPanel
from cqd.ui.panels.orders import OrdersPanel
from cqd.ui.panels.positions import PositionsPanel
from cqd.ui.panels.risk import RiskPanel
from cqd.ui.panels.ticket import TicketPanel
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
        self._mode_badge = Badge("DEMO" if self._is_demo else "LIVE")
        lay.addWidget(self._mode_badge)
        lay.addStretch(1)
        bar.addWidget(container)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)

    def _build_panels(self) -> None:
        self.positions_panel = PositionsPanel(self)
        self.risk_panel = RiskPanel(self)
        self.chart_panel = ChartPanel(self)
        self.analyst_panel = AnalystPanel(self)
        self.ticket_panel = TicketPanel(self)
        self.orders_panel = OrdersPanel(self)

        self._add_dock(self.positions_panel, "Positions", Qt.DockWidgetArea.LeftDockWidgetArea)
        self._add_dock(self.risk_panel, "Risk", Qt.DockWidgetArea.RightDockWidgetArea)
        self._add_dock(self.chart_panel, "Chart", Qt.DockWidgetArea.RightDockWidgetArea)
        self._add_dock(self.ticket_panel, "Ticket", Qt.DockWidgetArea.RightDockWidgetArea)
        self._add_dock(self.analyst_panel, "Analyst", Qt.DockWidgetArea.BottomDockWidgetArea)
        self._add_dock(self.orders_panel, "Orders", Qt.DockWidgetArea.BottomDockWidgetArea)

        # Trading flows: submissions refresh open orders; Positions "Close"
        # pre-fills the ticket (never auto-submits).
        self.ticket_panel.order_submitted.connect(self.orders_panel.refresh)
        self.positions_panel.close_requested.connect(self.ticket_panel.prefill_close)

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
        settings_action = QAction("Settings...", self)
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)
        file_menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        trading_menu: QMenu = menubar.addMenu("&Trading")
        self.paper_action = QAction("Paper mode", self, checkable=True)
        self.paper_action.setChecked(store.get_paper_mode())
        self.paper_action.triggered.connect(self._on_paper_toggled)
        trading_menu.addAction(self.paper_action)
        cancel_all_action = QAction("Cancel all orders", self)
        cancel_all_action.triggered.connect(self.orders_panel._on_cancel_all)
        trading_menu.addAction(cancel_all_action)

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
        self._update_status_message()

    def _update_status_message(self) -> None:
        mode = services.trading_mode().upper()
        if self._is_demo:
            self.statusBar().showMessage(
                f"Demo data (sample portfolio) · Trading: {mode} - connect your "
                "Kraken account in File > Settings to see your live account."
            )
        else:
            self.statusBar().showMessage(f"Ready · Trading: {mode}")

    # ---------- trading mode ----------

    def _on_paper_toggled(self, checked: bool) -> None:
        if checked:
            store.set_paper_mode(True)
        else:
            # Going live is the one action that needs typed intent.
            if resolve_demo():
                QMessageBox.warning(
                    self,
                    "Live trading unavailable",
                    "Connect a Kraken account in File > Settings first.",
                )
                self.paper_action.setChecked(True)
                return
            text, ok = QInputDialog.getText(
                self,
                "Enable live trading",
                "Orders will be sent to Kraken with REAL funds.\nType LIVE to confirm:",
            )
            if not ok or text.strip() != "LIVE":
                self.paper_action.setChecked(True)
                return
            store.set_paper_mode(False)
        self.ticket_panel.refresh_mode_badge()
        self._update_status_message()

    # ---------- settings + first run ----------

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self)
        dialog.settings_changed.connect(self._on_settings_changed)
        dialog.exec()

    def _on_settings_changed(self) -> None:
        self._is_demo = resolve_demo()
        self.setWindowTitle(
            "Crypto Quant Desk (Demo data)" if self._is_demo else "Crypto Quant Desk"
        )
        self._mode_badge.setText("DEMO" if self._is_demo else "LIVE")
        self.paper_action.setChecked(store.get_paper_mode())
        self.ticket_panel.refresh_mode_badge()
        self._update_status_message()
        self._refresh_all()

    def maybe_show_first_run(self) -> None:
        """Fresh install (no keys, no prior choice): offer connect vs demo."""
        if os.environ.get("CQD_DATA_SOURCE"):
            return  # explicit source override (dev/CI): never nag
        if store.is_first_run_done():
            return
        if credentials.kraken_keys_present():
            store.mark_first_run_done()
            return
        dialog = FirstRunDialog(self)
        dialog.exec()
        if dialog.choice == CHOICE_CONNECT:
            self._open_settings()
        store.mark_first_run_done()
        self._on_settings_changed()

    def _refresh_all(self) -> None:
        panels = (
            self.positions_panel,
            self.risk_panel,
            self.chart_panel,
            self.analyst_panel,
            self.ticket_panel,
            self.orders_panel,
        )
        for panel in panels:
            if hasattr(panel, "refresh"):
                panel.refresh()
        self.statusBar().showMessage("Refreshed", 2000)
