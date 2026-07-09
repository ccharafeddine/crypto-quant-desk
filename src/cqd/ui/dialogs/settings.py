"""Settings dialog: account keys, trading rails, data source.

Keys are verified against Kraken (a live Balance call) BEFORE being stored in
the OS vault, are typed into masked fields, and are never redisplayed after
entry. Everything else persists via settings_store (QSettings).
"""

from __future__ import annotations

import asyncio

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from cqd.data import credentials
from cqd.data.errors import KrakenAuthError, KrakenError
from cqd.data.rest import KrakenRESTClient
from cqd.ui import settings_store as store

_SOURCE_LABELS = [
    ("auto", "Auto (live when keys are set, demo otherwise)"),
    ("rest", "Kraken REST API (live)"),
    ("cli", "Kraken CLI binary (requires kraken on PATH/WSL)"),
    ("demo", "Demo (sample portfolio, real prices)"),
]


class SettingsDialog(QDialog):
    """File > Settings. Emits settings_changed on OK so panels can refresh."""

    settings_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(520)

        tabs = QTabWidget()
        tabs.addTab(self._build_keys_tab(), "Keys")
        tabs.addTab(self._build_trading_tab(), "Trading")
        tabs.addTab(self._build_data_tab(), "Data")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    # ---------- Keys tab ----------

    def _build_keys_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self.kraken_key = QLineEdit()
        self.kraken_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.kraken_key.setPlaceholderText("Kraken API key")
        self.kraken_secret = QLineEdit()
        self.kraken_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.kraken_secret.setPlaceholderText("Kraken API secret")

        self.kraken_status = QLabel(
            "Keys are stored in Windows Credential Manager after a successful "
            "verification. Stored keys are never displayed."
        )
        self.kraken_status.setWordWrap(True)
        self.kraken_status.setProperty("role", "footnote")
        if credentials.kraken_keys_present():
            self.kraken_status.setText(
                "A Kraken key pair is stored. Enter a new pair to replace it, "
                "or disconnect to delete it."
            )

        self.verify_btn = QPushButton("Verify && Save")
        self.verify_btn.clicked.connect(self._on_verify_clicked)
        self.disconnect_btn = QPushButton("Disconnect (delete stored keys)")
        self.disconnect_btn.clicked.connect(self._on_disconnect_clicked)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.verify_btn)
        btn_row.addWidget(self.disconnect_btn)
        btn_row.addStretch(1)

        perm_note = QLabel(
            "Key permissions: Query Funds / Orders / Trades / Ledger, plus "
            "Create && Modify Orders for trading. NEVER enable Withdraw Funds."
        )
        perm_note.setWordWrap(True)
        perm_note.setProperty("role", "footnote")

        self.anthropic_key = QLineEdit()
        self.anthropic_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.anthropic_key.setPlaceholderText("Anthropic API key (optional, for the AI analyst)")
        self.anthropic_btn = QPushButton("Save")
        self.anthropic_btn.clicked.connect(self._on_anthropic_save)
        anthropic_row = QHBoxLayout()
        anthropic_row.addWidget(self.anthropic_key, 1)
        anthropic_row.addWidget(self.anthropic_btn)

        form.addRow("API key", self.kraken_key)
        form.addRow("API secret", self.kraken_secret)
        form.addRow(btn_row)
        form.addRow(self.kraken_status)
        form.addRow(perm_note)
        form.addRow(QLabel(""))
        form.addRow("Anthropic key", anthropic_row)
        return page

    def _on_verify_clicked(self) -> None:
        key = self.kraken_key.text().strip()
        secret = self.kraken_secret.text().strip()
        if not key or not secret:
            self.kraken_status.setText("Enter both the API key and the secret.")
            return
        self.verify_btn.setEnabled(False)
        self.kraken_status.setText("Verifying against Kraken...")
        asyncio.ensure_future(self._verify_and_save(key, secret))

    def _set_status(self, text: str) -> None:
        """UI update that survives the dialog being closed mid-verify.

        The verify task can outlive the dialog (user closes it while the HTTP
        call is in flight); touching a deleted Qt widget raises RuntimeError,
        which must not surface as an unhandled task exception.
        """
        try:
            self.kraken_status.setText(text)
        except RuntimeError:
            pass

    async def _verify_and_save(self, key: str, secret: str) -> None:
        try:
            async with KrakenRESTClient(api_key=key, api_secret=secret) as client:
                balance = await client.get_balance()
        except KrakenAuthError:
            self._set_status(
                "Kraken rejected the key pair (invalid key, secret, or nonce). Keys were NOT saved."
            )
            return
        except KrakenError as e:
            self._set_status(f"Verification failed: {e}. Keys were NOT saved.")
            return
        except Exception:  # noqa: BLE001 - never let a verify crash the app
            self._set_status("Verification failed unexpectedly. Keys were NOT saved.")
            import logging

            logging.getLogger("cqd").exception("key verification failed")
            return
        finally:
            try:
                self.verify_btn.setEnabled(True)
            except RuntimeError:
                pass

        # Verification passed: store the pair, even if the dialog is gone.
        credentials.set_kraken_keys(key, secret)
        try:
            self.kraken_key.clear()
            self.kraken_secret.clear()
        except RuntimeError:
            pass
        self._set_status(
            f"Verified: {len(balance)} balance entries visible. Keys stored in "
            "Windows Credential Manager."
        )

    def _on_disconnect_clicked(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Disconnect account",
            "Delete the stored Kraken keys from Windows Credential Manager?\n"
            "The app will fall back to demo data.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        credentials.delete_kraken_keys()
        self.kraken_status.setText("Stored keys deleted. The app will use demo data.")

    def _on_anthropic_save(self) -> None:
        key = self.anthropic_key.text().strip()
        if not key:
            credentials.delete_anthropic_key()
            self.kraken_status.setText("Anthropic key removed.")
            return
        credentials.set_anthropic_key(key)
        self.anthropic_key.clear()
        self.kraken_status.setText("Anthropic key stored (not verified; used on demand).")

    # ---------- Trading tab ----------

    def _build_trading_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self.paper_checkbox = QCheckBox("Paper mode (orders are simulated, never sent)")
        self.paper_checkbox.setChecked(store.get_paper_mode())

        self.max_order = QDoubleSpinBox()
        self.max_order.setRange(0.0, 1_000_000_000.0)
        self.max_order.setDecimals(2)
        self.max_order.setPrefix("$")
        self.max_order.setValue(store.get_max_order_usd())

        note = QLabel(
            "The max order value blocks any single order above this size, in "
            "paper and live mode alike. Order entry itself arrives in a later "
            "build step."
        )
        note.setWordWrap(True)
        note.setProperty("role", "footnote")

        form.addRow(self.paper_checkbox)
        form.addRow("Max order value", self.max_order)
        form.addRow(note)
        return page

    # ---------- Data tab ----------

    def _build_data_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self.source_combo = QComboBox()
        for value, label in _SOURCE_LABELS:
            self.source_combo.addItem(label, value)
        current = store.get_data_source()
        for i in range(self.source_combo.count()):
            if self.source_combo.itemData(i) == current:
                self.source_combo.setCurrentIndex(i)
                break

        self.dust = QDoubleSpinBox()
        self.dust.setRange(0.0, 1_000_000.0)
        self.dust.setDecimals(2)
        self.dust.setPrefix("$")
        self.dust.setValue(store.get_dust_threshold_usd())

        form.addRow("Data source", self.source_combo)
        form.addRow("Dust threshold", self.dust)
        return page

    # ---------- persist ----------

    def accept(self) -> None:
        store.set_paper_mode(self.paper_checkbox.isChecked())
        store.set_max_order_usd(self.max_order.value())
        store.set_dust_threshold_usd(self.dust.value())
        store.set_data_source(self.source_combo.currentData())
        self.settings_changed.emit()
        super().accept()
