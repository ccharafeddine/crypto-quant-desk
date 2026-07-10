"""Canonical trading symbol + the form converters the panels need.

Three symbol spellings coexist in the app:
  - Kraken WS v2 uses the bare slash form  "BTC/USD"  (stream, tape).
  - Kraken REST OHLC/Depth want the altname "XBTUSD"  (chart, depth, ticket).
  - The watchlist/ticker speak the bare slash "BTC/USD" too.

`Symbol` is the one canonical value (bare base + quote); `parse_symbol` accepts
any of the spellings and the properties emit whichever form a consumer needs.
Pure - no Qt, no I/O.
"""

from __future__ import annotations

from typing import NamedTuple

from cqd.data.normalize import split_pair, translate_asset

# Bare base -> Kraken's code, for building REST altnames (XBTUSD, XDGUSD).
_BARE_TO_KRAKEN = {"BTC": "XBT", "DOGE": "XDG"}


class Symbol(NamedTuple):
    """A market as bare codes, e.g. Symbol('BTC', 'USD')."""

    base: str
    quote: str

    @property
    def ws(self) -> str:
        """Kraken WS v2 / display form, e.g. 'BTC/USD'."""
        return f"{self.base}/{self.quote}"

    @property
    def display(self) -> str:
        return self.ws

    @property
    def rest(self) -> str:
        """Kraken REST altname, e.g. 'XBTUSD' (base remapped, quote as-is)."""
        return f"{_BARE_TO_KRAKEN.get(self.base, self.base)}{self.quote}"


def parse_symbol(text: str) -> Symbol:
    """Parse any spelling ('BTC/USD', 'XBT/USD', 'XBTUSD', 'BTCUSD') to a Symbol."""
    s = text.strip().upper()
    if "/" in s:
        base, _, quote = s.partition("/")
        return Symbol(translate_asset(base), translate_asset(quote))
    base, quote = split_pair(s)
    return Symbol(base, quote)
