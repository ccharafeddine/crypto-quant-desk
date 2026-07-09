"""Alert delivery: Windows toast, with a log fallback everywhere else."""

from __future__ import annotations

import logging

log = logging.getLogger("cqd.alerts")


def send_toast(title: str, message: str) -> None:
    """Fire an OS notification; never raises (alerts must not crash the app)."""
    try:
        from winotify import Notification

        Notification(app_id="Crypto Quant Desk", title=title, msg=message).show()
    except Exception:  # noqa: BLE001 - non-Windows, missing toolkit, etc.
        log.info("ALERT %s: %s", title, message)
