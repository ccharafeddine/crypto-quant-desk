"""Tests for shared UI widget logic (pure parts, no QApplication)."""

from __future__ import annotations

from cqd.ui.widgets import status_shows_retry


def test_only_error_state_shows_retry():
    assert status_shows_retry("error") is True
    for kind in ("ok", "loading", "empty", "", "info", "success"):
        assert status_shows_retry(kind) is False


def test_trimmed_spin_text_drops_trailing_zeros() -> None:
    from cqd.ui.widgets import trim_decimal_text

    # Regression: the alert threshold read 0.00000000 before any input.
    assert trim_decimal_text(0.0, 8) == "0"
    assert trim_decimal_text(2.5, 8) == "2.5"
    assert trim_decimal_text(0.00006, 8) == "0.00006"
    assert trim_decimal_text(81416.0, 8) == "81416"
