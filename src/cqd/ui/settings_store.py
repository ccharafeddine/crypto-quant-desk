"""Typed access to persisted UI/app settings (QSettings).

One place owns the key names and type coercion (the Windows registry hands
QSettings values back as strings), so panels and dialogs never parse raw
QSettings values themselves. Key material does NOT live here - that is
data/credentials.py (OS vault).
"""

from __future__ import annotations

import os

from PySide6.QtCore import QSettings

_ORG = "crypto-quant-desk"
_APP = "cqd"

DATA_SOURCES = ("auto", "rest", "cli", "demo")


def _qs() -> QSettings:
    return QSettings(_ORG, _APP)


def _as_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes")


def _as_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ---------- trading ----------


def get_paper_mode() -> bool:
    """Paper mode default. TRUE on first run - live trading is opt-in."""
    return _as_bool(_qs().value("trading/paper_mode"), True)


def set_paper_mode(on: bool) -> None:
    _qs().setValue("trading/paper_mode", bool(on))


def get_max_order_usd() -> float:
    return _as_float(_qs().value("trading/max_order_usd"), 500.0)


def set_max_order_usd(value: float) -> None:
    _qs().setValue("trading/max_order_usd", float(value))


# ---------- data ----------


def get_dust_threshold_usd() -> float:
    return _as_float(_qs().value("data/dust_threshold_usd"), 1.0)


def set_dust_threshold_usd(value: float) -> None:
    _qs().setValue("data/dust_threshold_usd", float(value))


def get_data_source() -> str:
    value = str(_qs().value("data/source") or "auto").strip().lower()
    return value if value in DATA_SOURCES else "auto"


def set_data_source(value: str) -> None:
    if value not in DATA_SOURCES:
        value = "auto"
    _qs().setValue("data/source", value)
    apply_data_source_env()


def apply_data_source_env() -> None:
    """Seed CQD_DATA_SOURCE from the persisted choice.

    The data layer stays Qt-free and reads only the environment; this is the
    single bridge. "auto" clears the variable so the factory's key-presence
    logic decides.
    """
    source = get_data_source()
    if source == "auto":
        os.environ.pop("CQD_DATA_SOURCE", None)
    else:
        os.environ["CQD_DATA_SOURCE"] = source


# ---------- app ----------


def is_first_run_done() -> bool:
    return _as_bool(_qs().value("app/first_run_done"), False)


def mark_first_run_done() -> None:
    _qs().setValue("app/first_run_done", True)
