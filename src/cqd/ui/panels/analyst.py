"""Analyst panel: plain-language narration of the engine's risk numbers.

Default analyst is rules-based and CLI-only: it loads the same AccountRisk the
Risk panel does (via make_client) and runs the pure narrate_account_risk over
it. NO external API call is made here. The "Ask a question" box is present but
disabled, reserved for the optional live-AI analyst (next build step).
"""

from __future__ import annotations

import asyncio

from PySide6.QtWidgets import QLabel, QLineEdit, QTextEdit

from cqd.analyst.narrate import narrate_account_risk
from cqd.data.client import make_client
from cqd.data.errors import KrakenAuthError, KrakenError
from cqd.data.portfolio import EmptyPortfolioError, compute_account_risk
from cqd.ui.panels.base import Panel
from cqd.ui.widgets import Badge, PanelHeader


class AnalystPanel(Panel):
    title = "Analyst"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        header = PanelHeader("Analyst")
        self.demo_badge = Badge("DEMO")
        self.demo_badge.setVisible(False)
        header.add_left(self.demo_badge)
        self._layout.addWidget(header)

        self.transcript = QTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setPlaceholderText("Reading your portfolio...")
        self._layout.addWidget(self.transcript)

        self.disclaimer = QLabel("")
        self.disclaimer.setProperty("role", "footnote")
        self.disclaimer.setWordWrap(True)
        self._layout.addWidget(self.disclaimer)

        # Reserved for the optional live-AI analyst (next build step). Visually
        # present but disabled; no API call is wired in this task.
        self.input = QLineEdit()
        self.input.setPlaceholderText(
            "Live AI analyst: add an Anthropic API key in Settings (coming next)"
        )
        self.input.setEnabled(False)
        self._layout.addWidget(self.input)

        self.status = QLabel("Not loaded")
        self.status.setProperty("role", "subtitle")
        self._layout.addWidget(self.status)

        asyncio.ensure_future(self.load())

    async def load(self) -> None:
        gen = self._begin_load()
        self.status.setText("Loading...")
        try:
            from cqd.ui.settings_store import get_dust_threshold_usd

            client = make_client()
            async with client as c:
                ar = await compute_account_risk(c, min_usd=get_dust_threshold_usd())
                is_demo = getattr(c, "is_demo", False)
            if not self._is_current(gen):
                return  # a newer load owns the UI now
            self.demo_badge.setVisible(is_demo)
            self._render(narrate_account_risk(ar))
            self.status.setText("Loaded")
        except EmptyPortfolioError:
            if self._is_current(gen):
                self.status.setText("No priceable holdings to narrate.")
        except KrakenAuthError:
            if self._is_current(gen):
                self.status.setText(
                    "Authentication failed. Check your Kraken keys in "
                    "File > Settings, or switch to demo data there."
                )
        except KrakenError as e:
            if self._is_current(gen):
                self.status.setText(f"Kraken error: {e}")
        except Exception as e:  # noqa: BLE001
            if self._is_current(gen):
                self.status.setText(f"Error: {e}")

    def _render(self, narration) -> None:
        html = "".join(
            f"<p><b>{title}</b><br>{body}</p>"
            for title, body in narration.sections
        )
        self.transcript.setHtml(html)
        self.disclaimer.setText(narration.disclaimer)

    def refresh(self) -> None:
        asyncio.ensure_future(self.load())
