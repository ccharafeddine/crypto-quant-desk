"""Cross-platform behavior: data-dir mapping and notification backend selection."""

from __future__ import annotations

from pathlib import Path

from cqd.alerts.notify import notification_backend
from cqd.data.paths import resolve_app_data_root


def test_app_data_root_per_platform():
    home = Path("/home/user")
    # Windows uses %LOCALAPPDATA% when present, else ~/.cqd.
    assert resolve_app_data_root("win32", {"LOCALAPPDATA": "D:/AppData"}, home) == (
        Path("D:/AppData") / "CryptoQuantDesk"
    )
    assert resolve_app_data_root("win32", {}, home) == home / ".cqd"
    # macOS uses the Application Support convention.
    assert resolve_app_data_root("darwin", {}, home) == (
        home / "Library" / "Application Support" / "CryptoQuantDesk"
    )
    # Everything else falls back to ~/.cqd.
    assert resolve_app_data_root("linux", {}, home) == home / ".cqd"


def test_notification_backend_per_platform():
    assert notification_backend("win32") == "winotify"
    assert notification_backend("darwin") == "osascript"
    assert notification_backend("linux") == "log"
    assert notification_backend("freebsd") == "log"
