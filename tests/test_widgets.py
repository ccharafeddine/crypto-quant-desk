"""Tests for shared UI widget logic (pure parts, no QApplication)."""

from __future__ import annotations

from cqd.ui.widgets import status_shows_retry


def test_only_error_state_shows_retry():
    assert status_shows_retry("error") is True
    for kind in ("ok", "loading", "empty", "", "info", "success"):
        assert status_shows_retry(kind) is False
