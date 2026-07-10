"""Main window with dockable panels (Kraken-Desktop-style layout)."""

from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QApplication,
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
from cqd.alerts.notify import send_toast
from cqd.ui.panels.alerts import AlertsPanel
from cqd.ui.panels.analyst import AnalystPanel
from cqd.ui.panels.book import BookPanel
from cqd.ui.panels.chart import ChartPanel
from cqd.ui.panels.orders import OrdersPanel
from cqd.ui.panels.performance import PerformancePanel
from cqd.ui.panels.positions import PositionsPanel
from cqd.ui.panels.risk import RiskPanel
from cqd.ui.panels.ticket import TicketPanel
from cqd.ui.stream import StreamBridge
from cqd.ui.theme import (
    THEMES,
    build_qss,
    build_qtads_qss,
    get_theme,
    load_theme_name,
    save_theme_name,
)
from cqd.ui.widgets import Badge
from cqd.ui.workspace import PRESET_NAMES, Workspace


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._is_demo = resolve_demo()
        self.setWindowTitle(
            "Crypto Quant Desk (Demo data)" if self._is_demo else "Crypto Quant Desk"
        )
        self.resize(1600, 1000)

        self._settings = QSettings("crypto-quant-desk", "cqd")
        self._theme_name = load_theme_name()
        self._apply_theme(self._theme_name)

        self._build_header()
        self._build_panels()
        self._build_menus()
        self._build_status_bar()
        self._build_stream()

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
        self.performance_panel = PerformancePanel(self)
        self.alerts_panel = AlertsPanel(self)
        self.book_panel = BookPanel(self)

        # Adjustable card workspace (QtAds): register every panel under a stable
        # key, build the shipped perspectives, then restore last session's
        # arrangement (falling back to the default if none/corrupt).
        self.workspace = Workspace(self)
        self.workspace.add_panel("positions", "Positions", self.positions_panel)
        self.workspace.add_panel("risk", "Risk", self.risk_panel)
        self.workspace.add_panel("performance", "Performance", self.performance_panel)
        self.workspace.add_panel("chart", "Chart", self.chart_panel)
        self.workspace.add_panel("book", "Depth", self.book_panel)
        self.workspace.add_panel("ticket", "Ticket", self.ticket_panel)
        self.workspace.add_panel("orders", "Orders", self.orders_panel)
        self.workspace.add_panel("alerts", "Alerts", self.alerts_panel)
        self.workspace.add_panel("analyst", "Analyst", self.analyst_panel)
        self.workspace.ensure_presets(self._settings)
        self.workspace.restore_state(self._settings)
        # Theme the QtAds chrome now that the manager exists (the __init__
        # _apply_theme ran before the workspace did).
        self.workspace.apply_theme(build_qtads_qss(get_theme(self._theme_name)))

        # Trading flows: submissions refresh open orders; Positions "Close"
        # pre-fills the ticket (never auto-submits); the depth ladder follows
        # the ticket's pair.
        self.ticket_panel.order_submitted.connect(self.orders_panel.refresh)
        self.positions_panel.close_requested.connect(self.ticket_panel.prefill_close)
        self.ticket_panel.kraken_pair_selected.connect(self.book_panel.set_pair)
        self.ticket_panel.kraken_pair_selected.connect(self.chart_panel.set_pair)

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
        panels_menu: QMenu = view_menu.addMenu("Panels")
        for action in self.workspace.toggle_actions():
            panels_menu.addAction(action)

        self._persp_menu: QMenu = view_menu.addMenu("Perspectives")
        self._rebuild_perspectives_menu()
        self.workspace.manager.perspectiveListChanged.connect(self._rebuild_perspectives_menu)

        reset_action = QAction("Reset layout", self)
        reset_action.triggered.connect(self._on_reset_layout)
        view_menu.addAction(reset_action)

        view_menu.addSeparator()
        self._build_theme_menu(view_menu)

    # ---------- workspace: perspectives + layout ----------

    def _rebuild_perspectives_menu(self) -> None:
        menu = self._persp_menu
        menu.clear()
        for name in self.workspace.perspective_names():
            action = QAction(name, self)
            action.triggered.connect(
                lambda _checked=False, n=name: self.workspace.open_perspective(n)
            )
            menu.addAction(action)
        menu.addSeparator()
        save_action = QAction("Save perspective...", self)
        save_action.triggered.connect(self._on_save_perspective)
        menu.addAction(save_action)
        delete_action = QAction("Delete perspective...", self)
        delete_action.triggered.connect(self._on_delete_perspective)
        menu.addAction(delete_action)

    def _on_save_perspective(self) -> None:
        name, ok = QInputDialog.getText(self, "Save perspective", "Name:")
        name = name.strip()
        if not ok or not name:
            return
        if name in PRESET_NAMES:
            QMessageBox.warning(
                self,
                "Reserved name",
                f'"{name}" is a built-in preset. Choose a different name.',
            )
            return
        self.workspace.save_perspective(name, self._settings)
        self.statusBar().showMessage(f'Saved perspective "{name}"', 3000)

    def _on_delete_perspective(self) -> None:
        custom = self.workspace.custom_perspectives()
        if not custom:
            QMessageBox.information(
                self,
                "No custom perspectives",
                "Only your own saved perspectives can be deleted; the built-in presets stay.",
            )
            return
        name, ok = QInputDialog.getItem(
            self, "Delete perspective", "Perspective:", custom, 0, False
        )
        if not ok or not name:
            return
        self.workspace.delete_perspective(name, self._settings)
        self.statusBar().showMessage(f'Deleted perspective "{name}"', 3000)

    def _on_reset_layout(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Reset layout",
            "Restore the default panel arrangement?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.workspace.reset()

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
        theme = get_theme(name)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_qss(theme))
        # The QtAds chrome carries its own stylesheet; theme it too (guarded so
        # the initial call in __init__, before the workspace exists, is a no-op).
        workspace = getattr(self, "workspace", None)
        if workspace is not None:
            workspace.apply_theme(build_qtads_qss(theme))

    def _build_status_bar(self) -> None:
        bar = QStatusBar(self)
        self.setStatusBar(bar)
        # Permanent right-side widgets: stream health + last-tick clock.
        self._tick_clock = QLabel("")
        self._tick_clock.setProperty("role", "footnote")
        bar.addPermanentWidget(self._tick_clock)
        self._stream_label = QLabel("STREAM: OFFLINE")
        self._stream_label.setProperty("role", "stream-state")
        self._stream_label.setProperty("streamState", "offline")
        bar.addPermanentWidget(self._stream_label)
        self._update_status_message()

    # ---------- live stream ----------

    def _build_stream(self) -> None:
        self.stream = StreamBridge(self)
        self.stream.tick.connect(self._on_tick)
        self.stream.state_changed.connect(self._on_stream_state)
        self.stream.execution.connect(self._on_execution)
        self.positions_panel.symbols_available.connect(self.stream.ensure_symbols)
        self.ticket_panel.pair_selected.connect(lambda s: self.stream.ensure_symbols([s]))

        # Alerts: price rules ride ticks, PnL rules ride the positions feed,
        # drawdown rules ride the performance panel's refresh.
        self.positions_panel.pnl_tick.connect(self._on_pnl_tick)
        self.performance_panel.drawdown_updated.connect(self._on_drawdown)
        self.alerts_panel.symbols_needed = lambda s: self.stream.ensure_symbols([s])
        for rule in services.alert_engine().rules:
            if rule.symbol:
                self.stream.ensure_symbols([rule.symbol])

        # While the stream is degraded, fall back to REST polling; on recovery
        # resync open orders (they may have changed while we were dark).
        self._fallback_timer = QTimer(self)
        self._fallback_timer.setInterval(30_000)
        self._fallback_timer.timeout.connect(self.positions_panel.refresh)
        self._fallback_timer.timeout.connect(self.ticket_panel.refresh)

        self.stream.start()

    def _on_tick(self, symbol: str, price: float) -> None:
        self.positions_panel.on_tick(symbol, price)
        self.ticket_panel.on_tick(symbol, price)
        self._tick_clock.setText(f"last tick {datetime.now():%H:%M:%S}")
        self._handle_fired(services.alert_engine().on_price(symbol, price))

    def _on_pnl_tick(self, asset: str, pct: float) -> None:
        self._handle_fired(services.alert_engine().on_position_pnl(asset, pct))

    def _on_drawdown(self, drawdown: float) -> None:
        self._handle_fired(services.alert_engine().on_drawdown(drawdown))

    def _handle_fired(self, fired: list) -> None:
        for alert in fired:
            send_toast("Crypto Quant Desk alert", alert.message)
            self.statusBar().showMessage(f"ALERT: {alert.message}", 8000)
        if fired:
            self.alerts_panel.on_fired()

    def _on_stream_state(self, state: str) -> None:
        self._stream_label.setText(f"STREAM: {state.upper()}")
        self._stream_label.setProperty("streamState", state)
        style = self._stream_label.style()
        style.unpolish(self._stream_label)
        style.polish(self._stream_label)
        if state == "live":
            if self._fallback_timer.isActive():
                self._fallback_timer.stop()
                self.orders_panel.refresh()  # resync after being dark
        else:
            self._fallback_timer.start()

    def _on_execution(self, data: dict) -> None:
        self.orders_panel.refresh()
        exec_type = str(data.get("exec_type", "update"))
        order_id = str(data.get("order_id", ""))
        self.statusBar().showMessage(f"Order {exec_type}: {order_id}", 4000)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # Persist the current arrangement (and any saved perspectives) so the
        # next launch reopens exactly as the user left it.
        self.workspace.save_state(self._settings)
        self.stream.stop()
        super().closeEvent(event)

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
            self.performance_panel,
            self.alerts_panel,
            self.book_panel,
        )
        for panel in panels:
            if hasattr(panel, "refresh"):
                panel.refresh()
        self.statusBar().showMessage("Refreshed", 2000)
