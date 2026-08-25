"""Regenerate the Signals-panel screenshot (docs/screenshots/signals.png).

Run on a real desktop (NOT the offscreen Qt platform - offscreen ships no fonts
and renders tofu). Feeds the panel a representative demo setup + book + track
record, applies the current theme, and grabs the widget:

    python docs/screenshots/capture.py

The perspective shots (trading/analysis/monitor.png) are captured from the
running app under whichever theme is active; this script only refreshes the
standalone Signals card, which is tabbed behind the ticket in the presets.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication

from cqd.engine.backtest import StrategyStats
from cqd.engine.microstructure import BookFeatures, FillEstimate, TimingVerdict
from cqd.engine.signals import TradeSetup, TrendState
from cqd.ui.panels.signals import SignalsPanel
from cqd.ui.theme import build_qss, get_theme, load_theme_name

OUT = Path(__file__).with_name("signals.png")


def _demo():
    setup = TradeSetup(
        symbol="BTC/USD",
        direction="long",
        state=TrendState.LONG_ACTIVE,
        entry_ref=62150.0,
        stop=60420.0,
        size_base=0.0289,
        size_quote=1796.1,
        risk_quote=50.0,
        targets=[63880.0, 65610.0, 67340.0],
        rr=3.0,
        confidence=0.62,
        rationale=(
            "ma_cross long, state=long_active; entry 62150, stop 60420 "
            "(2x ATR 865); risk 50 quote (0.50% of equity)."
        ),
        created_ts=1_700_000_000,
        daily_room=300.0,
        total_room=500.0,
    )
    features = BookFeatures(
        mid=62148.0, microprice=62151.4, spread_abs=4.0, spread_bps=0.64,
        imbalance_l5=0.28, imbalance_l10=0.22, imbalance_l25=0.11,
        depth_bid_bps=18.4, depth_ask_bps=12.1,
    )
    fill = FillEstimate(
        side="buy", notional=1796.1, vwap=62163.0, slippage_bps=2.4, levels_consumed=3
    )
    verdict = TimingVerdict(
        verdict="GO",
        reasons=["tight spread 0.6 bps", "strong supportive imbalance", "microprice supportive"],
        score=1.5,
    )
    stats = StrategyStats(
        outcome="unresolved", bars=365, trades=14, wins=6, losses=8, win_rate=6 / 14,
        expectancy=11.3, profit_factor=1.42, avg_win=118.0, avg_loss=-62.0,
        max_drawdown=-0.071, total_return=0.083, final_equity=10830.0, regime="bull",
    )
    live = {
        "trades": 3, "pending": 1, "win_rate": 2 / 3, "expectancy": 24.0,
        "profit_factor": 2.1, "total_pnl": 72.0, "enough": False,
    }
    return setup, features, fill, verdict, stats, live


def main() -> None:
    app = QApplication([])
    app.setStyleSheet(build_qss(get_theme(load_theme_name())))
    panel = SignalsPanel()
    setup, features, fill, verdict, stats, live = _demo()
    panel.set_context("BTC/USD", "1D")
    panel.set_enabled_state(True)
    panel.on_setup_update(setup, features, fill, verdict)
    panel.on_stats_update(stats, live)
    # Size to the content so no label is squeezed (the dock scrolls in-app).
    panel.resize(430, panel.sizeHint().height() + 40)
    panel.grab().save(str(OUT))
    print(f"saved {OUT}")


if __name__ == "__main__":
    main()
