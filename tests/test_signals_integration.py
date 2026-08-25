"""Tests for the S5 integration seams: the analyst signals snapshot and the
signal-state alert rule (pure/engine-level, no QApplication)."""

from __future__ import annotations

from cqd.alerts.engine import AlertEngine, AlertRule
from cqd.analyst.context import build_user_message, signals_snapshot
from cqd.engine.backtest import StrategyStats
from cqd.engine.microstructure import BookFeatures, TimingVerdict
from cqd.engine.signals import TradeSetup, TrendState


def _setup() -> TradeSetup:
    return TradeSetup(
        symbol="BTC/USD",
        direction="long",
        state=TrendState.LONG_ACTIVE,
        entry_ref=100.0,
        stop=97.0,
        size_base=1.0,
        size_quote=100.0,
        risk_quote=3.0,
        targets=[103.0, 106.0, 109.0],
        rr=3.0,
        confidence=0.6,
        rationale="test",
        created_ts=1000,
    )


def _stats() -> StrategyStats:
    return StrategyStats(
        outcome="pass",
        bars=300,
        trades=10,
        wins=6,
        losses=4,
        win_rate=0.6,
        expectancy=12.0,
        profit_factor=1.8,
        avg_win=30.0,
        avg_loss=-15.0,
        max_drawdown=-0.08,
        total_return=0.15,
        final_equity=11_500.0,
        regime="bull",
    )


# ---------- analyst signals_snapshot (AC7.3: engine numbers only) ----------


def test_signals_snapshot_full() -> None:
    features = BookFeatures(mid=100.0, microprice=100.2, spread_bps=5.0, imbalance_l10=0.3)
    verdict = TimingVerdict(verdict="GO", reasons=["tight spread"], score=1.5)
    snap = signals_snapshot(_setup(), features, verdict, _stats())
    assert snap["setup"]["entry_ref"] == 100.0
    assert snap["setup"]["confidence_trend_only"] == 0.6
    assert snap["execution"]["timing_verdict"] == "GO"
    assert "times the entry only" in snap["execution"]["note"]  # never direction
    assert snap["backtest"]["outcome"] == "pass"


def test_signals_snapshot_none_fields_become_null() -> None:
    snap = signals_snapshot(None, None, None, None)
    assert snap["setup"] is None and snap["backtest"] is None
    assert snap["execution"] == {"note": "order-flow times the entry only; it never sets direction"}


def test_build_user_message_signals_mode() -> None:
    msg = build_user_message("signals", {"setup": None}, None)
    assert "trade setup" in msg and "Engine-computed data" in msg


# ---------- signal_state alert rule ----------


def test_signal_state_alert_edge_triggers_and_rearms() -> None:
    engine = AlertEngine()
    engine.add_rule(AlertRule(kind="signal_state", symbol="BTC/USD", threshold=2, repeat=True))

    assert engine.on_signal("BTC/USD", "long_armed") == []  # below "active" target
    fired = engine.on_signal("BTC/USD", "long_active")
    assert len(fired) == 1 and "active" in fired[0].message
    assert engine.on_signal("BTC/USD", "long_active") == []  # still met -> no refire
    assert engine.on_signal("BTC/USD", "flat") == []  # resets/rearms
    assert len(engine.on_signal("BTC/USD", "long_active")) == 1  # fires again after rearm


def test_signal_state_armed_rule_fires_on_active_too() -> None:
    engine = AlertEngine()
    engine.add_rule(AlertRule(kind="signal_state", symbol="ETH/USD", threshold=1))  # armed
    assert len(engine.on_signal("ETH/USD", "long_armed")) == 1
    # Other symbols never match this rule.
    engine.add_rule(AlertRule(kind="signal_state", symbol="ETH/USD", threshold=1))
    assert engine.on_signal("BTC/USD", "long_active") == []


def test_signal_state_rule_describe() -> None:
    assert (
        AlertRule(kind="signal_state", symbol="BTC/USD", threshold=2).describe()
        == "BTC/USD setup active"
    )
    assert (
        AlertRule(kind="signal_state", symbol="BTC/USD", threshold=1).describe()
        == "BTC/USD setup armed"
    )
