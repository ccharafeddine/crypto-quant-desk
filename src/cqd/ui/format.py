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


def _strip(text: str, min_decimals: int) -> str:
    if "." not in text:
        return text
    whole, frac = text.split(".")
    frac = frac.rstrip("0")
    if len(frac) < min_decimals:
        frac = frac + "0" * (min_decimals - len(frac))
    return f"{whole}.{frac}" if frac else whole


def format_qty(value: float | None, max_decimals: int = 8) -> str:
    """Asset quantity in fixed notation: 6e-05 -> '0.00006', 2.5 -> '2.5'.

    Never scientific (Kraken lot sizes go to 8 decimals), thousands-grouped,
    trailing zeros trimmed. None/NaN/inf -> '—'.
    """
    if value is None or not math.isfinite(value):
        return "—"
    return _strip(f"{value:,.{max_decimals}f}", 0)


def price_decimals(value: float) -> int:
    """Display decimals for a price: 2 at >= 1,000, 4 at >= 1, else 4 significant."""
    magnitude = abs(value)
    if magnitude >= 1000:
        return 2
    if magnitude >= 1 or magnitude == 0:
        return 4
    return min(10, 3 - math.floor(math.log10(magnitude)))


def format_price(value: float | None) -> str:
    """Price with magnitude-aware decimals, never scientific: 81416.4 ->
    '81,416.40', 0.2241 -> '0.2241', 0.00001234 -> '0.00001234'."""
    if value is None or not math.isfinite(value):
        return "—"
    places = price_decimals(value)
    return _strip(f"{value:,.{places}f}", min(2, places))


def format_usd(value: float | None, decimals: int = 2, *, signed: bool = False) -> str:
    """'$1,234.56' / '−$55.76' (true minus sign); `signed` adds '+' to gains."""
    if value is None or not math.isfinite(value):
        return "—"
    sign = "−" if value < 0 else ("+" if signed and value > 0 else "")
    return f"{sign}${abs(value):,.{decimals}f}"


def format_pct(value: float | None, decimals: int = 1, *, signed: bool = False) -> str:
    """Fraction -> percent: 0.118 -> '11.8%' ('+11.8%' when signed)."""
    if value is None or not math.isfinite(value):
        return "—"
    sign = "−" if value < 0 else ("+" if signed and value > 0 else "")
    return f"{sign}{abs(value) * 100:,.{decimals}f}%"


def display_pair(text: str) -> str:
    """Any Kraken pair spelling -> the one display form, 'BTC/USD'."""
    from cqd.data.symbols import parse_symbol

    try:
        return parse_symbol(text).display
    except Exception:  # noqa: BLE001 - unknown spellings render as given
        return text


def tone(value: float | None) -> str:
    """'pos' / 'neg' / '' for P&L colouring (the only place green/red is used)."""
    if value is None or not math.isfinite(value) or value == 0:
        return ""
    return "pos" if value > 0 else "neg"
