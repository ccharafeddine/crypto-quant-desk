"""Alert delivery: native OS notification per platform, log fallback everywhere.

Windows uses winotify toasts; macOS shells out to `osascript` (no extra
dependency); anything else logs. Delivery never raises - a failed notification
must not crash the app or the alert loop.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys

log = logging.getLogger("cqd.alerts")

_APP_ID = "Crypto Quant Desk"


def notification_backend(platform: str) -> str:
    """Which delivery backend a platform uses: 'winotify', 'osascript', or 'log'."""
    if platform == "win32":
        return "winotify"
    if platform == "darwin":
        return "osascript"
    return "log"


def _macos_notify(title: str, message: str) -> None:
    # AppleScript string literals are double-quoted with backslash escapes, which
    # is exactly what json.dumps produces, so it safely quotes arbitrary text.
    script = f"display notification {json.dumps(message)} with title {json.dumps(title)}"
    subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=5)


def send_toast(title: str, message: str) -> None:
    """Fire an OS notification; never raises (alerts must not crash the app)."""
    backend = notification_backend(sys.platform)
    try:
        if backend == "winotify":
            from winotify import Notification

            Notification(app_id=_APP_ID, title=title, msg=message).show()
            return
        if backend == "osascript":
            _macos_notify(title, message)
            return
    except Exception:  # noqa: BLE001 - missing toolkit, osascript failure, etc.
        log.debug("native notification failed; falling back to log", exc_info=True)
    log.info("ALERT %s: %s", title, message)
