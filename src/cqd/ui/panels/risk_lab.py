"""Risk lab: VaR four ways with a backtest, crash replays, the bootstrap fan,
liquidity, attribution and drawdowns, tax lots and parameter stability.

Every figure comes from the pure engine (`engine.var`, `engine.stress`,
`engine.attribution`, `engine.tax_lots`, `engine.backtest`). The row builders
below are pure too, so labels, units and tones are tested without Qt; the
panel only fetches, hands work to a thread, and renders.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field

import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from cqd.data.client import make_client
from cqd.data.crash_history import load_crash_windows
from cqd.data.errors import KrakenAuthError, KrakenError
from cqd.data.portfolio import EmptyPortfolioError, compute_account_risk
from cqd.engine import var as V
from cqd.engine.attribution import beta_attribution, drawdown_table, fee_drag
from cqd.engine.backtest import PropLimits, StabilityGrid, parameter_stability
from cqd.engine.cost_basis import quote_fee
from cqd.engine.performance import CASH_ASSETS, build_twr
from cqd.engine.stress import (
    average_daily_volume_usd,
    block_bootstrap_fan,
    depeg_shock,
    downside_betas,
    liquidity_table,
    replay_all,
    rolling_correlation,
)
from cqd.engine.tax_lots import compare_methods
from cqd.ui.format import format_compact, format_pct, format_usd, tone
from cqd.ui.panels.base import Panel
from cqd.ui.settings_store import get_risk_free_rate, get_strategy_account_equity
from cqd.ui.theme import get_theme, load_theme_name
from cqd.ui.widgets import KeyValueGrid, PanelStatus

Row = tuple[str, ...]


def _days(ts) -> str:
    return "—" if ts is None else pd.Timestamp(ts).strftime("%Y-%m-%d")


def _num(x: float | None, fmt: str = "{:.2f}") -> str:
    return "—" if x is None or not math.isfinite(x) else fmt.format(x)


# ---------------------------------------------------------------- pure rows


def var_rows(suite: V.VarSuite, nav: float) -> list[Row]:
    """(method, VaR 95% %, VaR 95% $, VaR 99% %, VaR 99% $) per method."""
    rows = []
    for m in V.METHODS:
        v95, v99 = suite.var95[m], suite.var99[m]
        rows.append(
            (
                V.METHOD_LABELS[m],
                format_pct(v95, 2),
                format_usd(v95 * nav),
                format_pct(v99, 2),
                format_usd(v99 * nav),
            )
        )
    return rows


def es_rows(suite: V.VarSuite, nav: float) -> list[tuple[str, str, str]]:
    return [
        (f"ES 97.5% · {label}", format_pct(v, 2), "neg" if v == v and v < 0 else "")
        for label, v in (
            ("historical", suite.es975_hist),
            ("filtered historical", suite.es975_fhs),
            ("normal", suite.es975_normal),
        )
    ] + [("ES 97.5% in dollars (historical)", format_usd(suite.es975_hist * nav), "neg")]


def backtest_rows(tests: list[V.VarBacktest]) -> list[Row]:
    """(method, exceptions vs expected, Kupiec p, Christoffersen p, verdict)."""
    out = []
    for bt in tests:
        out.append(
            (
                V.METHOD_LABELS[bt.method],
                f"{bt.exceptions} / {bt.expected:.1f}" if bt.n else "—",
                _num(bt.kupiec_p, "{:.3f}"),
                _num(bt.ind_p, "{:.3f}"),
                {"pass": "Pass", "reject": "Reject", "n/a": "Too short"}[bt.verdict()],
            )
        )
    return out


def replay_rows(replays, nav: float) -> list[Row]:
    """(event, window, BTC move, portfolio move, $ impact, note on proxies)."""
    out = []
    for r in replays:
        proxy = f"proxied: {', '.join(r.proxied)}" if r.proxied else "all real"
        out.append(
            (
                r.label,
                f"{r.start[:10]} → {r.end[:10]}",
                format_pct(r.btc_return, 1, signed=True),
                format_pct(r.portfolio_return, 1, signed=True),
                format_usd(r.portfolio_return * nav, 0, signed=True),
                proxy,
            )
        )
    return out


def _adv(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return f"${format_compact(value, 1)}"


def _exit_days(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return "< 0.01 d" if value < 0.01 else f"{value:.2f} d"


def liquidity_rows(rows, betas: dict, down: dict, corr: dict) -> list[Row]:
    out = []
    for r in rows:
        out.append(
            (
                r.asset,
                format_usd(r.value_usd, 0),
                _adv(r.adv_usd),
                format_pct(r.pct_of_adv, 2),
                _exit_days(r.days_to_liquidate),
                _num(betas.get(r.asset)),
                _num(down.get(r.asset)),
                _num(corr.get(r.asset)),
            )
        )
    return out


def attribution_rows(att) -> list[tuple[str, str, str]]:
    if att is None:
        return [("Attribution", "needs BTC history", "")]
    return [
        (
            f"Portfolio ({att.window_days}d)",
            format_pct(att.portfolio, 1, signed=True),
            tone(att.portfolio),
        ),
        ("BTC buy & hold", format_pct(att.btc, 1, signed=True), tone(att.btc)),
        ("Active return", format_pct(att.active, 1, signed=True), tone(att.active)),
        (
            "· Allocation (cash vs invested)",
            format_pct(att.allocation, 1, signed=True),
            tone(att.allocation),
        ),
        ("· BTC beta", format_pct(att.btc_beta, 1, signed=True), tone(att.btc_beta)),
        ("· Selection", format_pct(att.selection, 1, signed=True), tone(att.selection)),
    ]


def drawdown_rows(table) -> list[Row]:
    return [
        (
            format_pct(d.depth, 1),
            _days(d.peak),
            _days(d.trough),
            _days(d.recovery) if d.recovery is not None else "open",
            f"{d.days_to_trough} d",
            f"{d.days_to_recover} d" if d.days_to_recover is not None else "—",
        )
        for d in table
    ]


def lot_rows(comparisons) -> list[Row]:
    out = []
    for c in comparisons:
        for method in ("average", "fifo", "hifo"):
            r = c.rows[method]
            out.append(
                (
                    c.asset,
                    {"average": "Average cost", "fifo": "FIFO", "hifo": "HIFO"}[method],
                    format_usd(r["realized"], signed=True),
                    format_usd(r["basis"]),
                    format_usd(r["unrealized"], signed=True),
                )
            )
    return out


def stability_summary(grid: StabilityGrid | None) -> str:
    if grid is None:
        return "Not enough BTC/USD history to scan the grid."
    ci, cj = grid.chosen
    share = "—" if grid.positive_share is None else f"{grid.positive_share:.0%}"
    hood = _num(grid.neighbourhood_mean, "{:+.2f} R")
    return (
        f"Net expectancy per trade (R, costs in) on BTC/USD daily · chosen "
        f"{grid.fasts[ci]}/{grid.slows[cj]} outlined · positive cells {share} · "
        f"neighbourhood mean {hood}. A plateau of positive cells is evidence; a lone "
        "bright cell is curve fit."
    )


def es_975(account_risk) -> float | None:
    """The KPI strip's tail figure: historical ES 97.5% of today's book."""
    try:
        port = V.portfolio_returns(account_risk.weights, account_risk.returns)
    except Exception:  # noqa: BLE001 - a KPI must never break the risk callback
        return None
    es = V.historical_es(port, 0.975) if len(port) else math.nan
    return es if math.isfinite(es) else None


def long_returns(candles: dict[str, list]) -> pd.DataFrame:
    """Daily simple returns from each asset's full Kraken daily history (up to
    720 closed bars), so VaR backtests and the stability grid have room; the
    Risk card's 90-day frame is too short to backtest a 250-day window."""
    cols = {}
    for asset, cs in candles.items():
        if cs:
            idx = pd.to_datetime([c.time for c in cs], unit="s")
            cols[asset] = pd.Series([c.close for c in cs], index=idx).pct_change()
    return pd.DataFrame(cols).iloc[1:] if cols else pd.DataFrame()


# ---------------------------------------------------------------- compute


@dataclass
class LabResult:
    nav: float = 0.0
    suite: V.VarSuite | None = None
    backtests: list[V.VarBacktest] = field(default_factory=list)
    replays: list = field(default_factory=list)
    depeg: dict = field(default_factory=dict)
    fan: object | None = None
    liquidity: list[Row] = field(default_factory=list)
    attribution: object | None = None
    drawdowns: list = field(default_factory=list)
    fees: object | None = None
    lots: list = field(default_factory=list)
    stability: StabilityGrid | None = None
    n_days: int = 0


def compute_lab(
    account_risk,
    candles: dict[str, list],
    equity: pd.Series,
    trades: list[dict],
    btc_candles: list,
    params=None,
    strategy_equity: float = 10_000.0,
    value_usd: pd.Series | None = None,
) -> LabResult:
    """Pure-ish orchestration (no I/O): everything the tabs show."""
    res = LabResult()
    if account_risk is not None:
        w, nav = account_risk.weights, account_risk.total_usd
        rets = long_returns(candles)
        if rets.empty or "BTC" not in rets:
            rets = account_risk.returns
        res.nav = nav
        port = V.portfolio_returns(w, rets)
        res.n_days = len(port)
        res.suite = V.var_suite(port)
        res.backtests = [V.backtest_var(port, m, alpha=0.99, window=250) for m in V.METHODS]
        betas = account_risk.risk.per_asset_beta
        res.replays = replay_all(w, load_crash_windows(), betas)
        res.depeg = depeg_shock(w)
        res.fan = block_bootstrap_fan(port, horizon=30)
        down = downside_betas(rets).to_dict()
        corr_frame = rolling_correlation(rets, window=90)
        corr = {
            c: float(corr_frame[c].dropna().iloc[-1])
            for c in corr_frame
            if corr_frame[c].notna().any()
        }
        values = {str(a): float(x) * nav for a, x in w.items()}
        adv = {}
        for asset, cs in candles.items():
            if cs:
                idx = [c.time for c in cs]
                adv[asset] = average_daily_volume_usd(
                    pd.Series([c.volume for c in cs], idx), pd.Series([c.close for c in cs], idx)
                )
        liq = liquidity_table({a: v for a, v in values.items() if a not in CASH_ASSETS}, adv)
        res.liquidity = liquidity_rows(liq, betas.to_dict(), down, corr)
        res.attribution = beta_attribution(w, rets, betas)
        marks = {a: cs[-1].close for a, cs in candles.items() if cs}
        res.lots = [
            compare_methods(trades, a, marks[a])
            for a in sorted(marks)
            if any(t.get("symbol") == f"{a}/USD" for t in trades)
        ]
    if not equity.empty:
        res.drawdowns = drawdown_table(equity)
        fees = sum(quote_fee(t, "USD") for t in trades if str(t.get("symbol", "")).endswith("/USD"))
        if value_usd is not None and not value_usd.empty:
            res.fees = fee_drag(fees, value_usd)
    if btc_candles and params is not None and len(btc_candles) > params.trend + 30:
        res.stability = parameter_stability(
            btc_candles, params, PropLimits(starting_equity=max(strategy_equity, 1.0))
        )
    return res


# ---------------------------------------------------------------- widgets


def _table(headers: list[str], fit: tuple[int, ...] = (0,)) -> QTableWidget:
    """Numeric columns share the width; text columns in `fit` size to content."""
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().hide()
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.setShowGrid(False)
    head = t.horizontalHeader()
    head.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    for col in fit:
        head.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
    return t


def _fill(table: QTableWidget, rows: list[Row], toned: tuple[int, ...] = ()) -> None:
    theme = get_theme(load_theme_name())
    table.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, text in enumerate(row):
            item = QTableWidgetItem(text)
            item.setToolTip(text)  # narrow docks elide; the full text is one hover away
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignVCenter
                | (Qt.AlignmentFlag.AlignLeft if j == 0 else Qt.AlignmentFlag.AlignRight)
            )
            if j in toned and text.strip():
                neg = text.startswith(("−", "-"))
                pos = text.startswith("+")
                if neg or pos:
                    item.setForeground(QColor(theme.negative if neg else theme.positive))
            table.setItem(i, j, item)


def _note(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("role", "footnote")
    lab.setWordWrap(True)
    return lab


def _section(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("role", "metric-label")
    return lab


class StabilityHeatmap(QWidget):
    """fast (rows) x slow (columns) grid, amber for positive R, red for negative."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._grid: StabilityGrid | None = None
        self.setMinimumHeight(220)

    def set_grid(self, grid: StabilityGrid | None) -> None:
        self._grid = grid
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        grid = self._grid
        if grid is None:
            return
        theme = get_theme(load_theme_name())
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, top = 46.0, 22.0
        w = (self.width() - left - 8) / max(1, len(grid.slows))
        h = (self.height() - top - 8) / max(1, len(grid.fasts))
        vals = [abs(v) for row in grid.cells for v in row if v is not None]
        scale = max(vals) if vals else 1.0
        p.setPen(QColor(theme.text_muted))
        for j, s in enumerate(grid.slows):
            p.drawText(QRectF(left + j * w, 0, w, top), Qt.AlignmentFlag.AlignCenter, f"slow {s}")
        for i, f in enumerate(grid.fasts):
            p.drawText(
                QRectF(0, top + i * h, left - 4, h),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{f}",
            )
            for j, _s in enumerate(grid.slows):
                v = grid.cells[i][j]
                rect = QRectF(left + j * w + 1, top + i * h + 1, w - 2, h - 2)
                if v is None:
                    p.fillRect(rect, QColor(theme.surface_raised))
                    continue
                c = QColor(theme.accent if v > 0 else theme.negative)
                c.setAlphaF(0.15 + 0.75 * min(1.0, abs(v) / scale))
                p.fillRect(rect, c)
                p.setPen(QColor(theme.text))
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{v:+.2f}")
        ci, cj = grid.chosen
        p.setPen(QColor(theme.text))
        p.drawRect(QRectF(left + cj * w + 1, top + ci * h + 1, w - 2, h - 2))
        p.end()


class RiskLabPanel(Panel):
    title = "Risk lab"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.tabs = QTabWidget()
        self._layout.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_var_tab(), "VaR")
        self.tabs.addTab(self._build_stress_tab(), "Crash replays")
        self.tabs.addTab(self._build_liquidity_tab(), "Liquidity")
        self.tabs.addTab(self._build_attr_tab(), "Attribution")
        self.tabs.addTab(self._build_lots_tab(), "Tax lots")
        self.tabs.addTab(self._build_stability_tab(), "Stability")
        self.status = PanelStatus("Not loaded", self.refresh)
        self._layout.addWidget(self.status)
        self._loaded = False

    # ---- tabs ----

    def _tab(self) -> tuple[QWidget, QVBoxLayout]:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setSpacing(8)
        return tab, lay

    def _build_var_tab(self) -> QWidget:
        tab, lay = self._tab()
        lay.addWidget(_section("One-day VaR · ex-ante (today's weights over the common history)"))
        self.var_table = _table(["Method", "VaR 95%", "$", "VaR 99%", "$"])
        lay.addWidget(self.var_table, 2)
        self.es_grid = KeyValueGrid(columns=2)
        lay.addWidget(self.es_grid)
        lay.addWidget(_section("VaR 99% backtest · rolling 250-day window, one step ahead"))
        self.bt_table = _table(
            ["Method", "Exceptions / exp.", "Kupiec p", "Christoff. p", "Verdict"]
        )
        lay.addWidget(self.bt_table, 2)
        lay.addWidget(
            _note(
                "Kupiec tests the exception count, Christoffersen tests clustering (an exception "
                "today should not predict one tomorrow); p < 0.05 rejects the model. Filtered "
                "historical scales standardised returns by today's EWMA vol (λ 0.94); "
                "Cornish-Fisher adjusts the normal quantile for skew and kurtosis."
            )
        )
        return tab

    def _build_stress_tab(self) -> QWidget:
        tab, lay = self._tab()
        lay.addWidget(
            _section("Today's book through past crashes (real per-asset moves, Kraken trades)")
        )
        self.replay_table = _table(
            ["Event", "Window (UTC)", "BTC", "Portfolio", "$ impact", "Coverage"],
            fit=(1, 2, 3, 4, 5),
        )
        lay.addWidget(self.replay_table, 2)
        self.depeg_label = QLabel("Stablecoin depeg: —")
        self.depeg_label.setProperty("role", "subtitle")
        self.depeg_label.setWordWrap(True)
        lay.addWidget(self.depeg_label)
        lay.addWidget(_section("30-day NAV fan · block bootstrap (5-day blocks), zero drift"))
        theme = get_theme(load_theme_name())
        self.fan_plot = pg.PlotWidget()
        self.fan_plot.setBackground(theme.surface)
        self.fan_plot.showGrid(x=True, y=True, alpha=0.12)
        self.fan_plot.getAxis("left").setTextPen(theme.text_muted)
        self.fan_plot.getAxis("bottom").setTextPen(theme.text_muted)
        self.fan_plot.setMinimumHeight(150)
        lay.addWidget(self.fan_plot, 2)
        self.fan_note = _note("")
        lay.addWidget(self.fan_note)
        return tab

    def _build_liquidity_tab(self) -> QWidget:
        tab, lay = self._tab()
        self.liq_table = _table(
            [
                "Asset",
                "Value",
                "ADV (30d)",
                "% of ADV",
                "Days to exit",
                "Beta",
                "Down beta",
                "Corr 90d",
            ]
        )
        lay.addWidget(self.liq_table, 1)
        lay.addWidget(
            _note(
                "ADV: Kraken's own 30-day average daily USD volume for the pair. Days to exit "
                "assumes 10% participation. Down beta is fitted on BTC's down days only; "
                "correlation is the trailing 90 days."
            )
        )
        return tab

    def _build_attr_tab(self) -> QWidget:
        tab, lay = self._tab()
        lay.addWidget(_section("Active return vs BTC, last 365 days (today's weights)"))
        self.attr_grid = KeyValueGrid(columns=2)
        lay.addWidget(self.attr_grid)
        lay.addWidget(_section("Deepest drawdowns (time-weighted equity)"))
        self.dd_table = _table(["Depth", "Peak", "Trough", "Recovered", "To trough", "To recover"])
        lay.addWidget(self.dd_table, 1)
        self.fee_label = QLabel("Fee drag: —")
        self.fee_label.setProperty("role", "subtitle")
        lay.addWidget(self.fee_label)
        lay.addWidget(
            _note(
                "Allocation + BTC beta + selection add up exactly to the active return. A "
                "static-weight approximation: past rebalancing is not modelled."
            )
        )
        return tab

    def _build_lots_tab(self) -> QWidget:
        tab, lay = self._tab()
        self.lot_table = _table(["Asset", "Method", "Realized", "Basis", "Unrealized"])
        lay.addWidget(self.lot_table, 1)
        lay.addWidget(
            _note(
                "USD-quoted trades only. Methods move P&L between realized and unrealized; the "
                "total is identical. HIFO defers gains. Arithmetic, not tax advice."
            )
        )
        return tab

    def _build_stability_tab(self) -> QWidget:
        tab, lay = self._tab()
        self.heatmap = StabilityHeatmap()
        lay.addWidget(self.heatmap, 1)
        self.stab_note = _note(stability_summary(None))
        lay.addWidget(self.stab_note)
        return tab

    # ---- data ----

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            self.refresh()

    def refresh(self) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        asyncio.ensure_future(self.load())

    async def load(self) -> None:
        from cqd.ui.signals_service import build_strategy_params

        gen = self._begin_load()
        self.status.setText("Loading...")
        try:
            async with make_client() as c:
                ledgers = await c.get_ledgers()
                trades = await c.get_trades()
                try:
                    account_risk = await compute_account_risk(c, rf_annual=get_risk_free_rate())
                except EmptyPortfolioError:
                    account_risk = None
                assets = (
                    [str(a) for a in account_risk.weights.index if str(a) not in CASH_ASSETS]
                    if account_risk is not None
                    else []
                )
                candles: dict[str, list] = {}
                for asset in assets:
                    try:
                        candles[asset] = await c.get_ohlc(f"{asset}USD", interval=1440)
                    except KrakenError:
                        continue
                btc = candles.get("BTC") or await c.get_ohlc("XBTUSD", interval=1440)
        except KrakenAuthError:
            if self._is_current(gen):
                self.status.error("Authentication failed. Check File > Settings.")
            return
        except Exception as e:  # noqa: BLE001
            if self._is_current(gen):
                self.status.error(f"Couldn't load the risk lab. ({e})")
            return
        if not self._is_current(gen):
            return
        closes = {a: [(c.time, c.close) for c in cs] for a, cs in candles.items()}
        value_usd, equity = build_twr(ledgers, closes)
        try:
            params = build_strategy_params()
        except Exception:  # noqa: BLE001
            params = None
        self.status.setText("Computing (VaR backtests, replays, stability grid)...")
        res = await asyncio.to_thread(
            compute_lab,
            account_risk,
            candles,
            equity,
            trades,
            btc,
            params,
            get_strategy_account_equity(),
            value_usd,
        )
        if not self._is_current(gen):
            return
        self.render(res)

    def render(self, res: LabResult) -> None:
        theme = get_theme(load_theme_name())
        if res.suite is not None:
            _fill(self.var_table, var_rows(res.suite, res.nav), toned=(1, 2, 3, 4))
            self.es_grid.set_rows(es_rows(res.suite, res.nav))
        _fill(self.bt_table, backtest_rows(res.backtests))
        _fill(self.replay_table, replay_rows(res.replays, res.nav), toned=(2, 3, 4))
        if res.depeg:
            self.depeg_label.setText(
                "Stablecoin depeg (USDC −13% as in March 2023, USDT −5%): portfolio "
                f"{format_pct(res.depeg.get('portfolio'), 2, signed=True)}"
            )
        self.fan_plot.clear()
        fan = res.fan
        if fan is not None and fan.n_paths:
            x = list(range(fan.horizon + 1))
            band = QColor(theme.accent)
            for lo_q, hi_q, alpha in ((0.05, 0.95, 0.12), (0.25, 0.75, 0.25)):
                lo = self.fan_plot.plot(x, fan.bands[lo_q], pen=pg.mkPen(None))
                hi = self.fan_plot.plot(x, fan.bands[hi_q], pen=pg.mkPen(None))
                band.setAlphaF(alpha)
                self.fan_plot.addItem(pg.FillBetweenItem(lo, hi, brush=QColor(band)))
            self.fan_plot.plot(x, fan.bands[0.5], pen=pg.mkPen(theme.accent, width=2))
            self.fan_note.setText(
                f"5–95% and 25–75% bands of {fan.n_paths:,} paths · 30-day VaR 95% "
                f"{format_pct(fan.var95_horizon, 1)} ({format_usd(fan.var95_horizon * res.nav, 0)}). "
                "Drift removed: this is a risk range, not a forecast."
            )
        _fill(self.liq_table, res.liquidity)
        self.attr_grid.set_rows(attribution_rows(res.attribution))
        _fill(self.dd_table, drawdown_rows(res.drawdowns))
        if res.fees is not None:
            self.fee_label.setText(
                f"Fee drag: {format_usd(res.fees.fees_usd)} paid over {res.fees.years:.1f} years "
                f"= {format_pct(res.fees.annual_drag, 2)} of average account value per year"
            )
        _fill(self.lot_table, lot_rows(res.lots), toned=(2, 4))
        self.heatmap.set_grid(res.stability)
        self.stab_note.setText(stability_summary(res.stability))
        self.status.setText(f"{res.n_days} common daily returns · ex-ante, closed bars")
