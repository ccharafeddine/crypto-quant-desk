"""Tests for settings_store pure helpers (no QSettings round-trip - that would
touch the real user registry)."""

from __future__ import annotations

from cqd.ui.settings_store import parse_pairs


def test_parse_pairs_splits_on_commas_and_spaces() -> None:
    assert parse_pairs("XBTUSD, ETHUSD") == ["XBTUSD", "ETHUSD"]
    assert parse_pairs("xbtusd ethusd") == ["XBTUSD", "ETHUSD"]  # upper-cased
    assert parse_pairs(" XBTUSD ,, ETHUSD ,") == ["XBTUSD", "ETHUSD"]  # drops empties


def test_parse_pairs_empty() -> None:
    assert parse_pairs("") == []
    assert parse_pairs("  ,  ") == []


class _FakeQS:
    def __init__(self, values: dict) -> None:
        self.values = values

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value) -> None:  # noqa: N802 - Qt naming
        self.values[key] = value


def test_risk_free_rate_default_is_tbill_not_zero(monkeypatch) -> None:
    from cqd.ui import settings_store as store

    monkeypatch.setattr(store, "_qs", lambda: _FakeQS({}))
    assert store.get_risk_free_rate() == store.DEFAULT_RISK_FREE_RATE > 0


def test_risk_free_rate_round_trip_and_clamp(monkeypatch) -> None:
    from cqd.ui import settings_store as store

    fake = _FakeQS({})
    monkeypatch.setattr(store, "_qs", lambda: fake)
    store.set_risk_free_rate(0.035)
    assert store.get_risk_free_rate() == 0.035
    fake.values["data/risk_free_rate"] = "-1"
    assert store.get_risk_free_rate() == 0.0
    fake.values["data/risk_free_rate"] = "9"
    assert store.get_risk_free_rate() == 0.25
