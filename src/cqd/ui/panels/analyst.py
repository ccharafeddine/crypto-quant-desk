"""Analyst panel: rules-based narration plus an optional Claude-powered analyst.

The default is the zero-cost, local rules narrator: it loads the same
AccountRisk the Risk panel does (via make_client) and runs the pure
narrate_account_risk over it. NO network call happens for that.

With an Anthropic key configured (File > Settings), a live-AI section appears
with three explicit actions - Portfolio commentary, Review recent trades, and a
free-text Ask. Each streams a narration from Claude grounded ONLY on the
engine-computed numbers (see cqd.analyst.context), and shows the token cost after
completion. No AI call is ever made without a button press (PRD AC7.3). Without a
key the section is hidden behind a one-line hint (APP_FLOW FL-8).
"""

from __future__ import annotations

import asyncio

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QWidget,
)

from cqd.analyst import context
from cqd.analyst.llm import AnalystClient, AnalystError
from cqd.analyst.narrate import narrate_account_risk
from cqd.data.client import make_client
from cqd.data.credentials import get_anthropic_key
from cqd.data.errors import KrakenAuthError, KrakenError
from cqd.data.portfolio import EmptyPortfolioError, compute_account_risk
from cqd.engine.performance import realized_pnl_by_asset, realized_trades
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

        # --- Live-AI section (shown only when an Anthropic key is configured) ---
        self.ai_hint = QLabel("Add an Anthropic key in Settings to enable AI analysis.")
        self.ai_hint.setProperty("role", "subtitle")
        self.ai_hint.setWordWrap(True)
        self._layout.addWidget(self.ai_hint)

        self.ai_controls = QWidget()
        controls = QHBoxLayout(self.ai_controls)
        controls.setContentsMargins(0, 0, 0, 0)
        self.btn_commentary = QPushButton("Portfolio commentary")
        self.btn_trades = QPushButton("Review recent trades")
        self.btn_commentary.clicked.connect(lambda: self._run_ai("commentary"))
        self.btn_trades.clicked.connect(lambda: self._run_ai("trades"))
        controls.addWidget(self.btn_commentary)
        controls.addWidget(self.btn_trades)
        controls.addStretch(1)
        self._layout.addWidget(self.ai_controls)

        self.ai_output = QTextEdit()
        self.ai_output.setReadOnly(True)
        self.ai_output.setPlaceholderText(
            "Claude's analysis will stream here. Grounded only on your computed metrics."
        )
        self._layout.addWidget(self.ai_output)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask a question about your portfolio, then press Enter")
        self.input.returnPressed.connect(self._on_ask)
        self._layout.addWidget(self.input)

        self.cost = QLabel("")
        self.cost.setProperty("role", "footnote")
        self._layout.addWidget(self.cost)

        self.status = QLabel("Not loaded")
        self.status.setProperty("role", "subtitle")
        self._layout.addWidget(self.status)

        self._ar = None  # last-loaded AccountRisk, reused for commentary/ask
        self._ai_busy = False
        self._apply_key_state()

        asyncio.ensure_future(self.load())

    # ---- AI availability -------------------------------------------------

    def _has_key(self) -> bool:
        return bool(get_anthropic_key())

    def _apply_key_state(self) -> None:
        """Show or hide the live-AI section based on key presence."""
        has_key = self._has_key()
        self.ai_hint.setVisible(not has_key)
        self.ai_controls.setVisible(has_key)
        self.ai_output.setVisible(has_key)
        self.input.setVisible(has_key)
        self.cost.setVisible(has_key)

    # ---- rules-based load (free, local) ----------------------------------

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
            self._ar = ar
            self.demo_badge.setVisible(is_demo)
            self._render(narrate_account_risk(ar))
            self.status.setText("Loaded")
        except EmptyPortfolioError:
            if self._is_current(gen):
                self._ar = None
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
        self._apply_key_state()

    def _render(self, narration) -> None:
        html = "".join(f"<p><b>{title}</b><br>{body}</p>" for title, body in narration.sections)
        self.transcript.setHtml(html)
        self.disclaimer.setText(narration.disclaimer)

    def refresh(self) -> None:
        asyncio.ensure_future(self.load())

    # ---- live-AI actions -------------------------------------------------

    def _on_ask(self) -> None:
        question = self.input.text().strip()
        if question:
            self._run_ai("ask", question)

    def _run_ai(self, mode: str, question: str | None = None) -> None:
        """Guarded entry point for a button/Enter press."""
        if self._ai_busy:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        asyncio.ensure_future(self._stream_ai(mode, question))

    def _set_ai_enabled(self, enabled: bool) -> None:
        self.btn_commentary.setEnabled(enabled)
        self.btn_trades.setEnabled(enabled)
        self.input.setEnabled(enabled)

    async def _stream_ai(self, mode: str, question: str | None) -> None:
        key = get_anthropic_key()
        if not key:
            self._apply_key_state()
            return
        if self._ar is None and mode != "trades":
            self.status.setText("Load a portfolio before asking the AI analyst.")
            return

        self._ai_busy = True
        self._set_ai_enabled(False)
        self.ai_output.clear()
        self.cost.setText("")
        self.status.setText("Contacting Claude...")
        try:
            engine_context = await self._build_context(mode)
            client = AnalystClient(key)
            result = await client.stream(mode, engine_context, self._append_ai, question)
            self.cost.setText(f"Cost: {result.cost_line}")
            self.status.setText("Done")
        except AnalystError as e:
            self.ai_output.append(f"\n[analyst error] {e}")
            self.status.setText("AI analysis failed. Press again to retry.")
        except (KrakenError, EmptyPortfolioError) as e:
            self.status.setText(f"Could not gather trade data: {e}")
        except Exception as e:  # noqa: BLE001
            self.status.setText(f"Unexpected error: {e}")
        finally:
            self._ai_busy = False
            self._set_ai_enabled(True)

    def _append_ai(self, text: str) -> None:
        """Stream callback: append a chunk and keep the view scrolled to it."""
        cursor = self.ai_output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self.ai_output.setTextCursor(cursor)

    async def _build_context(self, mode: str) -> dict:
        """Assemble the engine-computed context block for the given mode."""
        ctx: dict = {}
        if self._ar is not None:
            ctx["portfolio"] = context.portfolio_snapshot(self._ar)
        if mode == "trades":
            client = make_client()
            async with client as c:
                trades = await c.get_trades()
            realized = realized_pnl_by_asset(realized_trades(trades))
            ctx["recent_trades"] = context.trades_digest(trades, realized)
        return ctx
