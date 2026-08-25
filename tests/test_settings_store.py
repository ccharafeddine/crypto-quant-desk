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
