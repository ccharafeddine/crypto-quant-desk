"""Signals panel: the sized trade setup, its execution/timing read, and the
strategy's backtest-vs-live track record.

The panel is a thin view over the pure engines. The engine outputs
(`TradeSetup`, `BookFeatures`, `FillEstimate`, `TimingVerdict`, `StrategyStats`,
live summary) are mapped to render-ready strings by `build_signals_view`, a pure
function tested without a QApplication. No math, no I/O here.

Guardrail: there is NO path from a signal to an order. "Send to ticket" only
pre-fills the ticket (pair, price, size) for the user to review and submit; it
never places an order and never changes trading mode. Order-flow is shown as
execution timing, never as direction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton

from cqd.data.symbols import parse_symbol
from cqd.ui.panels.base import Panel
from cqd.ui.widgets import Badge, PanelHeader, PanelStatus

_FOOTNOTE = (
    "Confidence is trend-strength only (distance above the trend MA + slope), "
    "never order-book data. The timing verdict gates WHEN to act, never the "
    "direction. Backtest is frictionless; live is this app's realized signals."
)


# ---- pure formatting (testable without a QApplication) ----


def _is_nan(x) -> bool:
    return x != x


def _num(x, places: int = 2) -> str:
    if x is None or _is_nan(x):
        return "—"
    return f"{x:,.{places}f}"


def _money(x) -> str:
    return "—" if (x is None or _is_nan(x)) else f"{x:,.8g}"


def _pct(x, places: int = 1) -> str:
    if x is None or _is_nan(x):
        return "—"
    return f"{x * 100:.{places}f}%"


def _signed(x, places: int = 2) -> str:
    if x is None or _is_nan(x):
        return "—"
    return f"{x:+.{places}f}"


def _bps(x, places: int = 1) -> str:
    if x is None or _is_nan(x):
        return "—"
    return f"{x:.{places}f} bps"


_STATE_LABELS = {"long_active": "Long · active", "long_armed": "Long · armed", "flat": "Flat"}
_VERDICT_ROLE = {"GO": "go", "CAUTION": "caution", "WAIT": "wait"}


@dataclass
class SignalsView:
    """Fully formatted, render-ready view (all values are display strings)."""

    has_setup: bool
    state_label: str
    setup_rows: list[tuple[str, str]] = field(default_factory=list)
    rationale: str = ""
    prop_warnings: list[str] = field(default_factory=list)
    verdict: str = "WAIT"
    verdict_role: str = "wait"
    verdict_reasons: list[str] = field(default_factory=list)
    exec_rows: list[tuple[str, str]] = field(default_factory=list)
    backtest_rows: list[tuple[str, str]] = field(default_factory=list)
    live_rows: list[tuple[str, str]] = field(default_factory=list)
    live_note: str = ""
    footnote: str = _FOOTNOTE


def _setup_view(setup) -> tuple[str, list[tuple[str, str]], str, list[str]]:
    state = _STATE_LABELS.get(getattr(setup.state, "value", ""), "Long")
    rows = [
        ("Direction", setup.direction.capitalize()),
        ("Entry", _money(setup.entry_ref)),
        ("Stop", f"{_money(setup.stop)}  (risk {_money(setup.risk_quote)})"),
        ("Size", f"{_money(setup.size_base)} base  ·  {_money(setup.size_quote)} quote"),
        ("Targets", ", ".join(_money(t) for t in setup.targets) or "—"),
        ("Reward : Risk", f"{setup.rr:g} : 1"),
        ("Confidence", _pct(setup.confidence)),
    ]
    warnings: list[str] = []
    room_pairs = (("Daily", setup.daily_room), ("Total", setup.total_room))
    for label, room in room_pairs:
        if room is not None and room < setup.risk_quote:
            warnings.append(
                f"{label} loss room ({_money(room)}) is below one unit of risk "
                f"({_money(setup.risk_quote)}) — setup should be suppressed."
            )
    return state, rows, setup.rationale, warnings


def _exec_rows(setup, features, fill) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if features is not None:
        micro = features.microprice
        mid = features.mid
        lean = "—"
        if micro is not None and mid is not None:
            lean = f"{_money(micro)}  ({_signed((micro - mid))} vs mid)"
        rows += [
            ("Mid", _money(mid)),
            ("Microprice", lean),
            ("Spread", _bps(features.spread_bps)),
            ("Imbalance L10", _signed(features.imbalance_l10)),
        ]
    if fill is not None and setup is not None:
        slip = _bps(fill.slippage_bps)
        if fill.partial:
            slip += "  (book too thin — partial)"
        rows += [
            ("Est. fill VWAP", _money(fill.vwap)),
            ("Est. slippage", slip),
        ]
    return rows


def _backtest_rows(stats) -> list[tuple[str, str]]:
    if stats is None:
        return [("Backtest", "computing…")]
    return [
        ("Outcome", stats.outcome.capitalize()),
        ("Regime", stats.regime.capitalize()),
        ("Trades", str(stats.trades)),
        ("Win rate", _pct(stats.win_rate)),
        ("Expectancy", _money(stats.expectancy)),
        ("Profit factor", _num(stats.profit_factor)),
        ("Max drawdown", _pct(stats.max_drawdown)),
    ]


def _live_rows(live) -> tuple[list[tuple[str, str]], str]:
    if not live:
        return [("Live", "no data yet")], ""
    rows = [
        ("Trades", str(live.get("trades", 0))),
        ("Pending", str(live.get("pending", 0))),
        ("Win rate", _pct(live.get("win_rate"))),
        ("Expectancy", _money(live.get("expectancy"))),
        ("Profit factor", _num(live.get("profit_factor"))),
    ]
    note = "" if live.get("enough") else "Too few resolved trades yet for a meaningful live edge."
    return rows, note


def build_signals_view(setup, features, fill, verdict, backtest, live) -> SignalsView:
    """Map the engine outputs to a render-ready `SignalsView` (pure)."""
    if setup is None:
        state_label, setup_rows, rationale, warnings = "No active setup", [], "", []
    else:
        state_label, setup_rows, rationale, warnings = _setup_view(setup)

    v = getattr(verdict, "verdict", "WAIT") if verdict is not None else "WAIT"
    reasons = list(getattr(verdict, "reasons", None) or []) if verdict is not None else []
    live_rows, live_note = _live_rows(live)

    return SignalsView(
        has_setup=setup is not None,
        state_label=state_label,
        setup_rows=setup_rows,
        rationale=rationale,
        prop_warnings=warnings,
        verdict=v,
        verdict_role=_VERDICT_ROLE.get(v, "wait"),
        verdict_reasons=reasons,
        exec_rows=_exec_rows(setup, features, fill),
        backtest_rows=_backtest_rows(backtest),
        live_rows=live_rows,
        live_note=live_note,
    )


def _rows_html(rows: list[tuple[str, str]]) -> str:
    return "<br>".join(f"<b>{label}:</b> {value}" for label, value in rows) or "—"


class SignalsPanel(Panel):
    title = "Signals"

    #: (kraken altname, price, volume) to pre-fill the ticket. Never submits.
    send_to_ticket = Signal(str, float, float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._setup = None  # last TradeSetup | None
        self._features = None
        self._fill = None
        self._verdict = None
        self._backtest = None
        self._live: dict | None = None

        header = PanelHeader("Signals")
        self.symbol_label = QLabel("—")
        self.symbol_label.setProperty("role", "subtitle")
        header.add_left(self.symbol_label)
        self.timeframe_label = QLabel("")
        self.timeframe_label.setProperty("role", "footnote")
        header.add_right(self.timeframe_label)
        self._layout.addWidget(header)

        # Setup card.
        self._layout.addWidget(_section("Setup"))
        self.state_label = QLabel("No active setup")
        self.state_label.setProperty("role", "subtitle")
        self._layout.addWidget(self.state_label)
        self.setup_body = _rich_label()
        self._layout.addWidget(self.setup_body)
        self.rationale_label = QLabel("")
        self.rationale_label.setProperty("role", "footnote")
        self.rationale_label.setWordWrap(True)
        self._layout.addWidget(self.rationale_label)
        self.warnings_label = QLabel("")
        self.warnings_label.setProperty("role", "warning")
        self.warnings_label.setWordWrap(True)
        self.warnings_label.setVisible(False)
        self._layout.addWidget(self.warnings_label)

        self.send_btn = QPushButton("Send to ticket")
        self.send_btn.setEnabled(False)
        self.send_btn.clicked.connect(self._on_send)
        self._layout.addWidget(self.send_btn)

        # Execution overlay.
        self._layout.addWidget(_section("Execution / timing"))
        self.verdict_badge = Badge("WAIT")
        self.verdict_badge.setProperty("verdict", "wait")
        self._layout.addWidget(self.verdict_badge)
        self.reasons_label = QLabel("")
        self.reasons_label.setProperty("role", "footnote")
        self.reasons_label.setWordWrap(True)
        self._layout.addWidget(self.reasons_label)
        self.exec_body = _rich_label()
        self._layout.addWidget(self.exec_body)

        # Track record.
        self._layout.addWidget(_section("Track record"))
        self.backtest_body = _rich_label()
        self.backtest_body.setProperty("role", "subtitle")
        self._layout.addWidget(QLabel("Backtest"))
        self._layout.addWidget(self.backtest_body)
        self._layout.addWidget(QLabel("Live"))
        self.live_body = _rich_label()
        self._layout.addWidget(self.live_body)
        self.live_note = QLabel("")
        self.live_note.setProperty("role", "footnote")
        self.live_note.setWordWrap(True)
        self._layout.addWidget(self.live_note)

        self._layout.addStretch(1)

        self.footnote = QLabel(_FOOTNOTE)
        self.footnote.setProperty("role", "footnote")
        self.footnote.setWordWrap(True)
        self._layout.addWidget(self.footnote)

        self.status = PanelStatus("Waiting for a signal…", self.refresh)
        self._layout.addWidget(self.status)

    # ---- external updates (wired to SignalsService in main_window) ----

    def set_context(self, symbol_label: str, timeframe_label: str) -> None:
        self.symbol_label.setText(symbol_label)
        self.timeframe_label.setText(timeframe_label)

    def set_enabled_state(self, enabled: bool) -> None:
        """Reflect the Settings > Strategy on/off switch in the status line."""
        if enabled:
            self.status.setText("Waiting for a signal…")
        else:
            self.status.setText("Signals are off — enable them in Settings › Strategy.")

    def on_setup_update(self, setup, features, fill, verdict) -> None:
        self._setup, self._features, self._fill, self._verdict = setup, features, fill, verdict
        self._rebuild()
        self.status.setText("Updated")

    def on_stats_update(self, backtest, live) -> None:
        self._backtest, self._live = backtest, live
        self._rebuild()

    def on_error(self, message: str) -> None:
        self.status.error(message)

    def refresh(self) -> None:
        """Retry hook: request a fresh pull (connected in main_window)."""
        self.refresh_requested.emit()

    refresh_requested = Signal()

    # ---- rendering ----

    def _rebuild(self) -> None:
        view = build_signals_view(
            self._setup, self._features, self._fill, self._verdict, self._backtest, self._live
        )
        self.state_label.setText(view.state_label)
        self.setup_body.setText(_rows_html(view.setup_rows) if view.has_setup else "—")
        self.rationale_label.setText(view.rationale)
        self.warnings_label.setText("\n".join(view.prop_warnings))
        self.warnings_label.setVisible(bool(view.prop_warnings))
        self.send_btn.setEnabled(view.has_setup)

        self.verdict_badge.setText(view.verdict)
        self.verdict_badge.setProperty("verdict", view.verdict_role)
        _repolish(self.verdict_badge)
        self.reasons_label.setText(" · ".join(view.verdict_reasons))
        self.exec_body.setText(_rows_html(view.exec_rows))

        self.backtest_body.setText(_rows_html(view.backtest_rows))
        self.live_body.setText(_rows_html(view.live_rows))
        self.live_note.setText(view.live_note)

    def _on_send(self) -> None:
        if self._setup is None:
            return
        altname = parse_symbol(self._setup.symbol).rest
        self.send_to_ticket.emit(
            altname, float(self._setup.entry_ref), float(self._setup.size_base)
        )


def _section(title: str) -> QLabel:
    label = QLabel(title)
    label.setProperty("role", "panel-title")
    return label


def _rich_label() -> QLabel:
    label = QLabel("—")
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setWordWrap(True)
    return label


def _repolish(widget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
