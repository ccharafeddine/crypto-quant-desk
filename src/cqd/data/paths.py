"""Local app-data locations.

All mutable state (cache, nonce high-water mark, paper broker state, alert
rules, audit logs) lives under one per-user directory OUTSIDE the repo, so an
installed copy never writes next to its own code and `git status` never sees
private data: %LOCALAPPDATA%\\CryptoQuantDesk on Windows, ~/.cqd elsewhere.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def app_data_dir() -> Path:
    """The per-user state directory, created on first use."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) / "CryptoQuantDesk" if base else Path.home() / ".cqd"
    else:
        root = Path.home() / ".cqd"
    root.mkdir(parents=True, exist_ok=True)
    return root


def cache_dir() -> Path:
    d = app_data_dir() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def audit_dir() -> Path:
    d = app_data_dir() / "audit"
    d.mkdir(parents=True, exist_ok=True)
    return d
