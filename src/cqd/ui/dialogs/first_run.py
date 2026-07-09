"""First-run welcome dialog: connect an account or explore in demo mode."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

CHOICE_DEMO = 0
CHOICE_CONNECT = 1


class FirstRunDialog(QDialog):
    """Shown once on a fresh install (no keys, no stored choice)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to Crypto Quant Desk")
        self.setModal(True)
        self.choice = CHOICE_DEMO

        title = QLabel("Welcome to Crypto Quant Desk")
        title.setProperty("role", "title")
        body = QLabel(
            "Connect your Kraken account to see your live portfolio, or explore "
            "first with a sample portfolio priced with real market data.\n\n"
            "Keys are verified against Kraken and stored in Windows Credential "
            "Manager - never on disk, never in the repo."
        )
        body.setWordWrap(True)

        connect_btn = QPushButton("Connect Kraken account...")
        connect_btn.clicked.connect(self._on_connect)
        demo_btn = QPushButton("Explore with demo data")
        demo_btn.clicked.connect(self._on_demo)
        demo_btn.setDefault(True)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(connect_btn)
        buttons.addWidget(demo_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(body)
        layout.addLayout(buttons)

    def _on_connect(self) -> None:
        self.choice = CHOICE_CONNECT
        self.accept()

    def _on_demo(self) -> None:
        self.choice = CHOICE_DEMO
        self.accept()
