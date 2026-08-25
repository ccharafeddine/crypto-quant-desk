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


def _as_int(value, default: int) -> int:
    try:
        return int(value)
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


# ---------- strategy / signals ----------
# The signal engine is advisory-only (it proposes setups; it never places an
# order). Defaults mirror StrategyParams v1 (STRATEGY.md) and are duplicated as
# plain literals here so this Qt-settings module stays free of the engine import.
# The engine model is re-validated when the params are assembled
# (signals_service.build_strategy_params), so a hand-edited registry value that
# breaks the fast<slow<trend ordering just falls back to these defaults.

STRATEGY_VARIANTS = ("ma_cross", "breakout")
_DEFAULT_STRATEGY_PAIRS = ("XBTUSD", "ETHUSD")


def get_strategy_enabled() -> bool:
    """Signals off by default - the user opts in (like live trading)."""
    return _as_bool(_qs().value("strategy/enabled"), False)


def set_strategy_enabled(on: bool) -> None:
    _qs().setValue("strategy/enabled", bool(on))


def get_strategy_variant() -> str:
    value = str(_qs().value("strategy/variant") or "ma_cross").strip().lower()
    return value if value in STRATEGY_VARIANTS else "ma_cross"


def set_strategy_variant(value: str) -> None:
    _qs().setValue("strategy/variant", value if value in STRATEGY_VARIANTS else "ma_cross")


def get_strategy_fast() -> int:
    return _as_int(_qs().value("strategy/fast"), 20)


def set_strategy_fast(value: int) -> None:
    _qs().setValue("strategy/fast", int(value))


def get_strategy_slow() -> int:
    return _as_int(_qs().value("strategy/slow"), 50)


def set_strategy_slow(value: int) -> None:
    _qs().setValue("strategy/slow", int(value))


def get_strategy_trend() -> int:
    return _as_int(_qs().value("strategy/trend"), 200)


def set_strategy_trend(value: int) -> None:
    _qs().setValue("strategy/trend", int(value))


def get_strategy_atr_len() -> int:
    return _as_int(_qs().value("strategy/atr_len"), 14)


def set_strategy_atr_len(value: int) -> None:
    _qs().setValue("strategy/atr_len", int(value))


def get_strategy_atr_mult() -> float:
    return _as_float(_qs().value("strategy/atr_mult"), 2.0)


def set_strategy_atr_mult(value: float) -> None:
    _qs().setValue("strategy/atr_mult", float(value))


def get_strategy_risk_pct() -> float:
    """Risk-per-trade as a FRACTION of equity (0.005 = 0.5%)."""
    return _as_float(_qs().value("strategy/risk_pct"), 0.005)


def set_strategy_risk_pct(value: float) -> None:
    _qs().setValue("strategy/risk_pct", float(value))


def get_strategy_timeframe_minutes() -> int:
    """Bar timeframe for the trend engine. 1440 = daily (matches the backtest)."""
    return _as_int(_qs().value("strategy/timeframe_minutes"), 1440)


def set_strategy_timeframe_minutes(value: int) -> None:
    _qs().setValue("strategy/timeframe_minutes", int(value))


def get_strategy_poll_seconds() -> int:
    return max(1, _as_int(_qs().value("strategy/poll_seconds"), 30))


def set_strategy_poll_seconds(value: int) -> None:
    _qs().setValue("strategy/poll_seconds", int(value))


def parse_pairs(text: str) -> list[str]:
    """Split a comma/space-separated pair list into upper-cased altnames."""
    parts = text.replace(",", " ").split()
    return [p.strip().upper() for p in parts if p.strip()]


def get_strategy_pairs() -> list[str]:
    """Target pairs as Kraken altnames (e.g. 'XBTUSD'). Never empty."""
    raw = _qs().value("strategy/pairs")
    pairs = parse_pairs(str(raw)) if raw else []
    return pairs or list(_DEFAULT_STRATEGY_PAIRS)


def set_strategy_pairs(pairs: list[str]) -> None:
    _qs().setValue("strategy/pairs", ", ".join(parse_pairs(", ".join(pairs))))


# ---------- app ----------


def is_first_run_done() -> bool:
    return _as_bool(_qs().value("app/first_run_done"), False)


def mark_first_run_done() -> None:
    _qs().setValue("app/first_run_done", True)
