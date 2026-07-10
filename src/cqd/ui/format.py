"""Small pure display formatters shared across panels."""

from __future__ import annotations

import math

_UNITS = ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K"))


def format_compact(value: float, decimals: int = 2) -> str:
    """Human-compact number: 1_234_567 -> '1.23M', 950 -> '950.00'.

    Keeps the sign, uses T/B/M/K suffixes above a thousand, and never falls back
    to scientific notation (which `:g` would). NaN/inf render as '—'.
    """
    if value is None or not math.isfinite(value):
        return "—"
    magnitude = abs(value)
    for divisor, suffix in _UNITS:
        if magnitude >= divisor:
            return f"{value / divisor:.{decimals}f}{suffix}"
    return f"{value:,.{decimals}f}"
