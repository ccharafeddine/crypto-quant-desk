"""Local app-data locations.

All mutable state (cache, nonce high-water mark, paper broker state, alert
rules, audit logs) lives under one per-user directory OUTSIDE the repo, so an
installed copy never writes next to its own code and `git status` never sees
private data. Per platform:
  Windows: %LOCALAPPDATA%\\CryptoQuantDesk
  macOS:   ~/Library/Application Support/CryptoQuantDesk
  other:   ~/.cqd
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path


def resolve_app_data_root(platform: str, env: Mapping[str, str], home: Path) -> Path:
    """Per-user state directory for a given platform (pure; no filesystem I/O).

    Kept separate from `app_data_dir` so the platform mapping is testable without
    touching the real filesystem or environment.
    """
    if platform == "win32":
        base = env.get("LOCALAPPDATA")
        return Path(base) / "CryptoQuantDesk" if base else home / ".cqd"
    if platform == "darwin":
        return home / "Library" / "Application Support" / "CryptoQuantDesk"
    return home / ".cqd"


def app_data_dir() -> Path:
    """The per-user state directory, created on first use."""
    root = resolve_app_data_root(sys.platform, os.environ, Path.home())
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
