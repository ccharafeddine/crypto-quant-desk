"""Tests for the alert engine (tmp persistence, no toasts)."""

from cqd.alerts.engine import AlertEngine, AlertRule


def _engine(tmp_path=None) -> AlertEngine:
    return AlertEngine((tmp_path / "alerts.json") if tmp_path else None)


def test_price_above_fires_once_until_reset() -> None:
    e = _engine()
    e.add_rule(AlertRule(kind="price_above", symbol="BTC/USD", threshold=65_000.0, repeat=True))
    assert e.on_price("BTC/USD", 64_000.0) == []
    fired = e.on_price("BTC/USD", 65_100.0)
    assert len(fired) == 1
    assert "BTC/USD above 65,000" in fired[0].message
    # Still above: edge-triggered, no refire per tick.
    assert e.on_price("BTC/USD", 66_000.0) == []
    # Cross back below (rearm), then above again -> repeating rule refires.
    assert e.on_price("BTC/USD", 64_500.0) == []
    assert len(e.on_price("BTC/USD", 65_200.0)) == 1


def test_one_shot_disables_after_firing() -> None:
    e = _engine()
    e.add_rule(AlertRule(kind="price_below", symbol="ETH/USD", threshold=1_500.0, repeat=False))
    assert len(e.on_price("ETH/USD", 1_400.0)) == 1
    rule = e.rules[0]
    assert rule.enabled is False
    # Even after crossing back and forth, a disabled one-shot stays quiet.
    e.on_price("ETH/USD", 1_600.0)
    assert e.on_price("ETH/USD", 1_400.0) == []


def test_symbol_scoping() -> None:
    e = _engine()
    e.add_rule(AlertRule(kind="price_above", symbol="BTC/USD", threshold=1.0, repeat=True))
    assert e.on_price("ETH/USD", 99_999.0) == []


def test_position_pnl_magnitude_both_directions() -> None:
    e = _engine()
    e.add_rule(AlertRule(kind="position_pnl_pct", asset="SOL", threshold=10.0, repeat=True))
    assert e.on_position_pnl("SOL", 5.0) == []
    assert len(e.on_position_pnl("SOL", 12.0)) == 1  # gain
    e.on_position_pnl("SOL", 0.0)  # rearm
    assert len(e.on_position_pnl("SOL", -11.0)) == 1  # loss fires too


def test_drawdown_uses_engine_negative_fraction() -> None:
    e = _engine()
    e.add_rule(AlertRule(kind="portfolio_drawdown_pct", threshold=10.0, repeat=True))
    assert e.on_drawdown(-0.05) == []
    fired = e.on_drawdown(-0.12)
    assert len(fired) == 1
    assert "12.0%" in fired[0].message


def test_persistence_roundtrip_and_remove(tmp_path) -> None:
    e = _engine(tmp_path)
    e.add_rule(AlertRule(kind="price_above", symbol="BTC/USD", threshold=70_000.0))
    e2 = _engine(tmp_path)
    assert len(e2.rules) == 1
    assert e2.rules[0].describe() == "BTC/USD above 70,000"
    e2.remove_rule(e2.rules[0].id)
    assert _engine(tmp_path).rules == []


def test_corrupt_rules_file_backed_up(tmp_path) -> None:
    (tmp_path / "alerts.json").write_text("{broken", encoding="utf-8")
    e = _engine(tmp_path)
    assert e.rules == []
    assert (tmp_path / "alerts.json.bak").exists()


def test_fired_history_recorded() -> None:
    e = _engine()
    e.add_rule(AlertRule(kind="price_above", symbol="BTC/USD", threshold=1.0))
    e.on_price("BTC/USD", 2.0)
    assert len(e.history) == 1
    assert e.history[0].value == 2.0
