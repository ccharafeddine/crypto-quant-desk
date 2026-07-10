"""Chart panel: candlestick + volume for the active pair, via public REST OHLC.

pyqtgraph has no candlestick primitive, so `CandlestickItem` paints the bars to a
cached QPicture (the standard GraphicsObject pattern). A volume subplot is x-linked
below, a crosshair reads out the bar under the cursor, and an optional cost-basis
overlay can be fed in (wired to portfolio data by the active-symbol bus).
"""

from __future__ import annotations

import asyncio

import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPicture
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QWidget

from cqd.data.errors import KrakenError
from cqd.data.normalize import Candle
from cqd.data.rest import KrakenRESTClient
from cqd.ui.panels.base import Panel
from cqd.ui.theme import get_theme, load_theme_name
from cqd.ui.widgets import PanelHeader, PanelStatus

# Timeframe label -> Kraken OHLC interval in minutes (the endpoint's allowed set).
_TIMEFRAMES: list[tuple[str, int]] = [
    ("1m", 1),
    ("5m", 5),
    ("15m", 15),
    ("1H", 60),
    ("4H", 240),
    ("1D", 1440),
    ("1W", 10080),
]
_DEFAULT_TF = "1H"
_REFRESH_MS = 30_000
_DEFAULT_PAIR = "XBTUSD"


# ---- pure helpers (testable without a QApplication) ----


def nearest_candle(candles: list[Candle], x: float) -> Candle | None:
    """The candle whose open time is closest to x (epoch seconds), or None."""
    if not candles:
        return None
    return min(candles, key=lambda c: abs(c.time - x))


def format_readout(c: Candle) -> str:
    """Mono OHLCV line for the crosshair label. `8g` keeps tick precision without
    trailing-zero noise across BTC-scale and sub-cent prices alike."""
    return (
        f"O {c.open:,.8g}   H {c.high:,.8g}   L {c.low:,.8g}   C {c.close:,.8g}   V {c.volume:,.4g}"
    )


def candle_body_halfwidth(candles: list[Candle]) -> float:
    """Half the drawn body width in x-units (seconds): 40% of the bar spacing."""
    if len(candles) >= 2:
        return (candles[1].time - candles[0].time) * 0.4
    return 0.4


class CandlestickItem(pg.GraphicsObject):
    """Paints OHLC bars once to a cached picture (up = positive, down = negative)."""

    def __init__(self, candles: list[Candle], up: str, down: str) -> None:
        super().__init__()
        self._candles = candles
        self._up = QColor(up)
        self._down = QColor(down)
        self._picture = QPicture()
        self._generate()

    def _generate(self) -> None:
        half = candle_body_halfwidth(self._candles)
        painter = QPainter(self._picture)
        for c in self._candles:
            color = self._up if c.close >= c.open else self._down
            painter.setPen(QPen(color))
            painter.setBrush(QBrush(color))
            # Wick: a single vertical line low->high at the bar's time.
            painter.drawLine(QPointF(c.time, c.low), QPointF(c.time, c.high))
            # Body: open..close rectangle (min height so dojis stay visible).
            top, bottom = max(c.open, c.close), min(c.open, c.close)
            height = max(top - bottom, (c.high - c.low) * 0.001) or 1e-9
            painter.drawRect(QRectF(c.time - half, bottom, half * 2, height))
        painter.end()

    def paint(self, painter: QPainter, *_args) -> None:
        painter.drawPicture(0, 0, self._picture)

    def boundingRect(self) -> QRectF:
        return QRectF(self._picture.boundingRect())


class ChartPanel(Panel):
    title = "Chart"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pair: str = _DEFAULT_PAIR  # Kraken friendly form, e.g. "XBTUSD"
        self._interval: int = dict(_TIMEFRAMES)[_DEFAULT_TF]
        self._candles: list[Candle] = []

        theme = get_theme(load_theme_name())

        header = PanelHeader("Chart")
        self.pair_label = QLabel(self._pair)
        self.pair_label.setProperty("role", "subtitle")
        header.add_left(self.pair_label)
        header.add_right(self._build_timeframes())
        self._layout.addWidget(header)

        pg.setConfigOptions(antialias=True)
        self.price_plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.price_plot.setBackground(theme.surface)
        self.price_plot.showGrid(x=True, y=True, alpha=0.12)
        self.price_plot.setMinimumHeight(220)
        self.price_plot.getAxis("left").setTextPen(theme.text_muted)
        self.price_plot.getPlotItem().hideAxis("bottom")  # time axis lives under volume
        self._layout.addWidget(self.price_plot, 3)

        self.vol_plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.vol_plot.setBackground(theme.surface)
        self.vol_plot.showGrid(x=True, y=True, alpha=0.10)
        self.vol_plot.setMaximumHeight(110)
        self.vol_plot.setXLink(self.price_plot)
        self.vol_plot.getAxis("left").setTextPen(theme.text_muted)
        self.vol_plot.getAxis("bottom").setTextPen(theme.text_muted)
        self._layout.addWidget(self.vol_plot, 1)

        self.status = PanelStatus("", self.refresh)
        self._layout.addWidget(self.status)

        # Crosshair + readout over the price plot.
        pen = pg.mkPen(theme.border_strong, style=Qt.PenStyle.DashLine)
        self._vline = pg.InfiniteLine(angle=90, movable=False, pen=pen)
        self._hline = pg.InfiniteLine(angle=0, movable=False, pen=pen)
        self.price_plot.addItem(self._vline, ignoreBounds=True)
        self.price_plot.addItem(self._hline, ignoreBounds=True)
        self._readout = pg.TextItem(anchor=(0, 1), color=theme.text)
        self.price_plot.addItem(self._readout, ignoreBounds=True)
        self.price_plot.scene().sigMouseMoved.connect(self._on_mouse)

        self._candle_item: CandlestickItem | None = None
        self._vol_item: pg.BarGraphItem | None = None
        self._cost_line: pg.InfiniteLine | None = None

        # Live-ish refresh, plus one load as soon as the event loop is running
        # (ensure_future needs a running loop, so never call it from __init__).
        self._timer = QTimer(self)
        self._timer.setInterval(_REFRESH_MS)
        self._timer.timeout.connect(self._reload)
        self._timer.start()
        QTimer.singleShot(0, self._reload)

    def _build_timeframes(self) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self._tf_group = QButtonGroup(self)
        self._tf_group.setExclusive(True)
        for label, minutes in _TIMEFRAMES:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setProperty("role", "table-action")
            btn.setChecked(label == _DEFAULT_TF)
            btn.clicked.connect(lambda _checked=False, m=minutes: self._set_interval(m))
            self._tf_group.addButton(btn)
            lay.addWidget(btn)
        return row

    # ---- external wiring ----

    def set_pair(self, friendly: str) -> None:
        """Follow the active symbol (Kraken friendly form, e.g. XBTUSD)."""
        if friendly and friendly != self._pair:
            self._pair = friendly
            self.pair_label.setText(friendly)
            self._reload()

    def set_cost_basis(self, price: float | None) -> None:
        """Draw (or clear) a dashed break-even line for the held base asset."""
        if self._cost_line is not None:
            self.price_plot.removeItem(self._cost_line)
            self._cost_line = None
        if price and price > 0:
            theme = get_theme(load_theme_name())
            self._cost_line = pg.InfiniteLine(
                pos=price,
                angle=0,
                pen=pg.mkPen(theme.accent, style=Qt.PenStyle.DashLine),
                label=f"cost {price:,.8g}",
                labelOpts={"color": theme.accent, "position": 0.05},
            )
            self.price_plot.addItem(self._cost_line, ignoreBounds=True)

    def _set_interval(self, minutes: int) -> None:
        self._interval = minutes
        self._reload()

    # ---- data ----

    def _reload(self) -> None:
        if not (self._pair and self.isVisible()):
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # no qasync loop yet (e.g. under tests) - nothing to schedule
        asyncio.ensure_future(self._load(self._pair, self._interval))

    async def _load(self, pair: str, interval: int) -> None:
        gen = self._begin_load()
        try:
            async with KrakenRESTClient(api_key="", api_secret="") as client:
                candles = await client.get_ohlc(pair, interval=interval)
        except KrakenError as e:
            if self._is_current(gen):
                self.status.error(f"Chart unavailable: {e}")
            return
        # Drop stale loads (a newer request started, or the pair/interval changed).
        if not self._is_current(gen) or pair != self._pair or interval != self._interval:
            return
        self._candles = candles
        self._render(candles)
        if candles:
            self.status.setText("")
        else:
            self.status.empty("No candles for this pair.")

    def _render(self, candles: list[Candle]) -> None:
        theme = get_theme(load_theme_name())
        if self._candle_item is not None:
            self.price_plot.removeItem(self._candle_item)
        if self._vol_item is not None:
            self.vol_plot.removeItem(self._vol_item)
        if not candles:
            self._candle_item = None
            self._vol_item = None
            return
        self._candle_item = CandlestickItem(candles, theme.positive, theme.negative)
        self.price_plot.addItem(self._candle_item)

        half = candle_body_halfwidth(candles)
        up, down = QColor(theme.positive), QColor(theme.negative)
        up.setAlphaF(0.5)
        down.setAlphaF(0.5)
        self._vol_item = pg.BarGraphItem(
            x=[c.time for c in candles],
            height=[c.volume for c in candles],
            width=half * 2,
            brushes=[up if c.close >= c.open else down for c in candles],
            pen=pg.mkPen(None),
        )
        self.vol_plot.addItem(self._vol_item)

    # ---- crosshair ----

    def _on_mouse(self, pos) -> None:
        if not self.price_plot.sceneBoundingRect().contains(pos):
            return
        vb = self.price_plot.getPlotItem().vb
        point = vb.mapSceneToView(pos)
        candle = nearest_candle(self._candles, point.x())
        if candle is None:
            return
        self._vline.setPos(candle.time)
        self._hline.setPos(point.y())
        self._readout.setText(format_readout(candle))
        self._readout.setPos(candle.time, candle.high)

    def refresh(self) -> None:
        self._reload()
