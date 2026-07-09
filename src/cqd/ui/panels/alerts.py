"""Alerts panel: rule management and fired history.

The AlertEngine (services.alert_engine) owns evaluation and persistence; this
panel is CRUD plus a history list. Firing is wired in the main window (stream
ticks -> engine -> toast + history refresh here).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QCheckBox,
    QListWidget,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
)

from cqd.alerts.engine import AlertRule
from cqd.ui import services
from cqd.ui.panels.base import Panel
from cqd.ui.widgets import PanelHeader

_KIND_LABELS = [
    ("price_above", "Price above"),
    ("price_below", "Price below"),
    ("position_pnl_pct", "Position PnL ±%"),
    ("portfolio_drawdown_pct", "Portfolio drawdown %"),
]


class AlertsPanel(Panel):
    title = "Alerts"

    HEADERS = ["Rule", "Repeat", "Status", ""]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._layout.addWidget(PanelHeader("Alerts"))

        form = QHBoxLayout()
        self.kind_combo = QComboBox()
        for value, label in _KIND_LABELS:
            self.kind_combo.addItem(label, value)
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        form.addWidget(self.kind_combo)
        self.target_edit = QLineEdit()
        self.target_edit.setPlaceholderText("BTC/USD")
        form.addWidget(self.target_edit, 1)
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.0, 1e12)
        self.threshold_spin.setDecimals(8)
        form.addWidget(self.threshold_spin, 1)
        self.repeat_check = QCheckBox("repeat")
        form.addWidget(self.repeat_check)
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._on_add)
        form.addWidget(add_btn)
        self._layout.addLayout(form)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self._layout.addWidget(self.table, 2)

        history_label = QLabel("Fired")
        history_label.setProperty("role", "metric-label")
        self._layout.addWidget(history_label)
        self.history_list = QListWidget()
        self._layout.addWidget(self.history_list, 1)

        self.status = QLabel("")
        self.status.setProperty("role", "subtitle")
        self._layout.addWidget(self.status)

        self._render()

    # ---------- CRUD ----------

    def _on_kind_changed(self) -> None:
        kind = self.kind_combo.currentData()
        if kind in ("price_above", "price_below"):
            self.target_edit.setPlaceholderText("BTC/USD")
            self.target_edit.setEnabled(True)
        elif kind == "position_pnl_pct":
            self.target_edit.setPlaceholderText("BTC")
            self.target_edit.setEnabled(True)
        else:
            self.target_edit.setPlaceholderText("(whole portfolio)")
            self.target_edit.setEnabled(False)

    def _on_add(self) -> None:
        kind = self.kind_combo.currentData()
        target = self.target_edit.text().strip().upper()
        threshold = self.threshold_spin.value()
        if threshold <= 0:
            self.status.setText("Threshold must be positive.")
            return
        symbol = asset = None
        if kind in ("price_above", "price_below"):
            if "/" not in target:
                self.status.setText("Price alerts need a slash symbol like BTC/USD.")
                return
            symbol = target
        elif kind == "position_pnl_pct":
            if not target:
                self.status.setText("Position alerts need an asset like BTC.")
                return
            asset = target
        rule = AlertRule(
            kind=kind,
            symbol=symbol,
            asset=asset,
            threshold=threshold,
            repeat=self.repeat_check.isChecked(),
        )
        services.alert_engine().add_rule(rule)
        # Price alerts need the symbol streaming even if not held.
        if symbol:
            self.symbols_needed(symbol)
        self.status.setText(f"Added: {rule.describe()}")
        self._render()

    def symbols_needed(self, symbol: str) -> None:
        """Overridden by main-window wiring to subscribe the stream."""

    def _on_delete(self, rule_id: str) -> None:
        services.alert_engine().remove_rule(rule_id)
        self._render()

    # ---------- render ----------

    def _render(self) -> None:
        engine = services.alert_engine()
        self.table.clearContents()
        self.table.setRowCount(len(engine.rules))
        for i, rule in enumerate(engine.rules):
            desc = QTableWidgetItem(rule.describe())
            desc.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            self.table.setItem(i, 0, desc)
            self.table.setItem(i, 1, QTableWidgetItem("yes" if rule.repeat else "once"))
            self.table.setItem(i, 2, QTableWidgetItem("armed" if rule.enabled else "fired (done)"))
            btn = QPushButton("Delete")
            btn.setProperty("role", "table-action")
            btn.clicked.connect(lambda _c=False, rid=rule.id: self._on_delete(rid))
            self.table.setCellWidget(i, 3, btn)
        self._render_history()

    def _render_history(self) -> None:
        from datetime import datetime

        engine = services.alert_engine()
        self.history_list.clear()
        for fired in reversed(engine.history[-50:]):
            stamp = datetime.fromtimestamp(fired.time).strftime("%H:%M:%S")
            self.history_list.addItem(f"{stamp}  {fired.message}")

    def on_fired(self) -> None:
        """Called by the main window after any alert fires."""
        self._render()

    def refresh(self) -> None:
        self._render()
