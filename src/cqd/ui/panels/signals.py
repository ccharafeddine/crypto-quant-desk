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

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from cqd.data.symbols import parse_symbol
from cqd.ui.format import format_price, format_qty
from cqd.ui.panels.base import Panel
from cqd.ui.theme import get_theme, load_theme_name
from cqd.ui.widgets import Badge, KeyValueGrid, PanelHeader, PanelStatus, set_kind

_FOOTNOTE = (
    "Confidence is trend-strength only (distance above the trend MA + slope), "
    "never order-book data. The timing verdict gates WHEN to act, never the "
    "direction. Backtest: closed-bar signals filled at the next open, paper-broker "
    "fees and slippage, gap-aware stops; only the walk-forward out-of-sample line is "
    "evidence. Live is this app's own recorded signals."
)


# ---- pure formatting (testable without a QApplication) ----


def _is_nan(x) -> bool:
    return x != x


def _num(x, places: int = 2) -> str:
    if x is None or _is_nan(x):
        return "—"
    return f"{x:,.{places}f}"


def _money(x) -> str:
    return "—" if (x is None or _is_nan(x)) else format_price(x)


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
        ("Size", f"{format_qty(setup.size_base)} base  ·  {_money(setup.size_quote)} quote"),
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
        ("Expectancy (R)", _r(getattr(stats, "expectancy_r", None))),
        ("Expectancy 90% CI", _ci(getattr(stats, "expectancy_r_ci", None))),
        (
            "Frictionless (R)",
            _r(getattr(stats, "frictionless_expectancy_r", None)),
        ),
        ("Profit factor", _num(stats.profit_factor)),
        ("Max drawdown", _pct(stats.max_drawdown)),
        ("Costs paid", _money(getattr(stats, "costs_paid", None))),
        ("Out-of-sample", _oos(stats)),
    ]


def _ci(ci) -> str:
    if not ci:
        return "—"
    return f"{_r(ci[0])} … {_r(ci[1])}"


def curve_points(stats) -> tuple[list[float], list[float], list[float]]:
    """(x seconds, net equity, frictionless equity) for the backtest chart.

    The frictionless twin can stop on a different bar (it may PASS sooner), so
    it is cut to its own length; empty lists when there is nothing to draw.
    """
    if stats is None or not getattr(stats, "equity_curve", None):
        return [], [], []
    x = [float(t) for t in stats.curve_times]
    free = list(getattr(stats, "frictionless_curve", None) or [])
    return x, list(stats.equity_curve), free[: len(x)]


def r_histogram(rs: list[float], width: float = 0.5) -> tuple[list[float], list[int]]:
    """R-multiples binned at `width` R: (bin left edges, counts)."""
    if not rs:
        return [], []
    import math

    lo = math.floor(min(rs) / width) * width
    hi = math.floor(max(rs) / width) * width
    edges = [lo + i * width for i in range(int(round((hi - lo) / width)) + 1)]
    counts = [0] * len(edges)
    for r in rs:
        counts[min(len(edges) - 1, int((r - lo) // width))] += 1
    return edges, counts


def _r(x) -> str:
    return "—" if (x is None or _is_nan(x)) else f"{x:+.2f}R"


def _oos(stats) -> str:
    """Walk-forward out-of-sample line: the only number that is evidence."""
    folds = getattr(stats, "oos_folds", None)
    if not folds:
        return "—"
    return (
        f"{_r(stats.oos_expectancy_r)} over {stats.oos_trades} trades, "
        f"{_pct(stats.oos_return)} ({folds} folds)"
    )


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


class SignalsPanel(Panel):
    title = "Signals"

    #: (kraken altname, price, volume) to pre-fill the ticket. Never submits.
    send_to_ticket = Signal(str, float, float)
    #: The empty state's button: open Settings › Strategy.
    open_settings = Signal()
    refresh_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._setup = None  # last TradeSetup | None
        self._features = None
        self._fill = None
        self._verdict = None
        self._backtest = None
        self._live: dict | None = None
        theme = get_theme(load_theme_name())

        header = PanelHeader("Signals")
        self.symbol_label = QLabel("—")
        self.symbol_label.setProperty("role", "subtitle")
        header.add_left(self.symbol_label)
        self.timeframe_label = QLabel("")
        self.timeframe_label.setProperty("role", "footnote")
        header.add_right(self.timeframe_label)
        self._layout.addWidget(header)

        # Empty state: explains itself instead of an empty shell with a chip.
        self.empty = QWidget()
        elay = QVBoxLayout(self.empty)
        elay.setContentsMargins(0, 18, 0, 18)
        title = QLabel("Signals are off")
        title.setProperty("role", "title")
        elay.addWidget(title)
        body = QLabel(
            "The trend engine (MA-cross or Donchian breakout, ATR stops) sizes a "
            "setup for the active pair, reads the order book for timing, and "
            "backtests itself with paper-broker costs and a walk-forward. It is "
            "advisory: it can pre-fill the order ticket, never send an order. "
            "Turn it on and pick pairs, timeframe and risk per trade in "
            "Settings › Strategy."
        )
        body.setProperty("role", "empty-state")
        body.setWordWrap(True)
        elay.addWidget(body)
        open_btn = QPushButton("Open Settings › Strategy")
        open_btn.setProperty("role", "primary")
        open_btn.clicked.connect(self.open_settings.emit)
        open_btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        elay.addWidget(open_btn)
        elay.addStretch(1)
        self._layout.addWidget(self.empty, 1)

        self.content = QWidget()
        cols = QHBoxLayout(self.content)
        cols.setContentsMargins(0, 0, 0, 0)
        cols.setSpacing(18)
        left = QVBoxLayout()
        left.setSpacing(6)

        left.addWidget(_section("Setup"))
        self.state_label = QLabel("No active setup")
        self.state_label.setProperty("role", "subtitle")
        left.addWidget(self.state_label)
        self.setup_grid = KeyValueGrid()
        left.addWidget(self.setup_grid)
        self.rationale_label = QLabel("")
        self.rationale_label.setProperty("role", "footnote")
        self.rationale_label.setWordWrap(True)
        left.addWidget(self.rationale_label)
        self.warnings_label = QLabel("")
        self.warnings_label.setProperty("role", "subtitle")
        self.warnings_label.setProperty("tone", "warn")
        self.warnings_label.setWordWrap(True)
        self.warnings_label.setVisible(False)
        left.addWidget(self.warnings_label)
        self.send_btn = QPushButton("Send to ticket (pre-fill only)")
        self.send_btn.setEnabled(False)
        self.send_btn.clicked.connect(self._on_send)
        left.addWidget(self.send_btn)

        left.addSpacing(6)
        left.addWidget(_section("Execution · timing"))
        verdict_row = QHBoxLayout()
        self.verdict_badge = Badge("WAIT", kind="neutral")
        verdict_row.addWidget(self.verdict_badge)
        self.reasons_label = QLabel("")
        self.reasons_label.setProperty("role", "footnote")
        self.reasons_label.setWordWrap(True)
        verdict_row.addWidget(self.reasons_label, 1)
        left.addLayout(verdict_row)
        self.exec_grid = KeyValueGrid()
        left.addWidget(self.exec_grid)

        left.addSpacing(6)
        left.addWidget(_section("Live track record"))
        self.live_grid = KeyValueGrid()
        left.addWidget(self.live_grid)
        self.live_note = QLabel("")
        self.live_note.setProperty("role", "footnote")
        self.live_note.setWordWrap(True)
        left.addWidget(self.live_note)
        left.addStretch(1)
        cols.addLayout(left, 2)

        right = QVBoxLayout()
        right.setSpacing(6)
        right.addWidget(_section("Backtest · net of costs vs frictionless"))
        self.curve_plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.curve_plot.setBackground(theme.surface)
        self.curve_plot.showGrid(x=False, y=True, alpha=0.12)
        self.curve_plot.setMinimumHeight(170)
        self.curve_plot.addLegend(offset=(8, 4), labelTextColor=theme.text_muted)
        for axis in ("left", "bottom"):
            self.curve_plot.getAxis(axis).setTextPen(theme.text_faint)
            self.curve_plot.getAxis(axis).setPen(theme.border)
        right.addWidget(self.curve_plot, 3)
        right.addWidget(_section("R-multiple distribution (net)"))
        self.r_plot = pg.PlotWidget()
        self.r_plot.setBackground(theme.surface)
        self.r_plot.setMinimumHeight(110)
        for axis in ("left", "bottom"):
            self.r_plot.getAxis(axis).setTextPen(theme.text_faint)
            self.r_plot.getAxis(axis).setPen(theme.border)
        right.addWidget(self.r_plot, 2)
        self.backtest_grid = KeyValueGrid(columns=2)
        right.addWidget(self.backtest_grid)
        cols.addLayout(right, 3)
        self._layout.addWidget(self.content, 1)

        self.footnote = QLabel(_FOOTNOTE)
        self.footnote.setProperty("role", "footnote")
        self.footnote.setWordWrap(True)
        self._layout.addWidget(self.footnote)

        self.status = PanelStatus("Waiting for a signal…", self.refresh)
        self._layout.addWidget(self.status)
        self.set_enabled_state(False)

    # ---- external updates (wired to SignalsService in main_window) ----

    def set_context(self, symbol_label: str, timeframe_label: str) -> None:
        self.symbol_label.setText(symbol_label)
        self.timeframe_label.setText(timeframe_label)

    def set_enabled_state(self, enabled: bool) -> None:
        """Off: the explanatory empty state. On: the setup/backtest view."""
        self.empty.setVisible(not enabled)
        self.content.setVisible(enabled)
        self.footnote.setVisible(enabled)
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
        self._render_backtest()

    def on_error(self, message: str) -> None:
        self.status.error(message)

    def refresh(self) -> None:
        """Retry hook: request a fresh pull (connected in main_window)."""
        self.refresh_requested.emit()

    # ---- rendering ----

    def _rebuild(self) -> None:
        view = build_signals_view(
            self._setup, self._features, self._fill, self._verdict, self._backtest, self._live
        )
        self.state_label.setText(view.state_label)
        self.setup_grid.set_rows(view.setup_rows)
        self.rationale_label.setText(view.rationale)
        self.warnings_label.setText("\n".join(view.prop_warnings))
        self.warnings_label.setVisible(bool(view.prop_warnings))
        self.send_btn.setEnabled(view.has_setup)

        self.verdict_badge.setText(view.verdict)
        set_kind(self.verdict_badge, "kind", _VERDICT_KIND.get(view.verdict_role, "neutral"))
        self.reasons_label.setText(" · ".join(view.verdict_reasons))
        self.exec_grid.set_rows(view.exec_rows)
        self.backtest_grid.set_rows(view.backtest_rows)
        self.live_grid.set_rows(view.live_rows)
        self.live_note.setText(view.live_note)

    def _render_backtest(self) -> None:
        theme = get_theme(load_theme_name())
        self.curve_plot.clear()
        self.r_plot.clear()
        x, net, free = curve_points(self._backtest)
        if x:
            if free:
                self.curve_plot.plot(
                    x[: len(free)],
                    free,
                    pen=pg.mkPen(theme.text_faint, width=1, style=Qt.PenStyle.DashLine),
                    name="Frictionless",
                )
            self.curve_plot.plot(x, net, pen=pg.mkPen(theme.accent, width=2), name="Net of costs")
        rs = list(getattr(self._backtest, "r_multiples", None) or [])
        edges, counts = r_histogram(rs)
        if edges:
            brushes = [theme.positive if e >= 0 else theme.negative for e in edges]
            self.r_plot.addItem(
                pg.BarGraphItem(
                    x0=edges, width=0.45, height=counts, brushes=brushes, pen=pg.mkPen(None)
                )
            )

    def _on_send(self) -> None:
        if self._setup is None:
            return
        altname = parse_symbol(self._setup.symbol).rest
        self.send_to_ticket.emit(
            altname, float(self._setup.entry_ref), float(self._setup.size_base)
        )


_VERDICT_KIND = {"go": "go", "caution": "warn", "wait": "neutral"}


def _section(title: str) -> QLabel:
    label = QLabel(title)
    label.setProperty("role", "panel-title")
    return label
