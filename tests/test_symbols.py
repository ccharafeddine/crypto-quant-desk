"""Tests for the canonical Symbol / form converters and the SymbolHub bus."""

from __future__ import annotations

from cqd.data.symbols import Symbol, parse_symbol
from cqd.ui.symbol_hub import SymbolHub


def test_parse_symbol_accepts_every_spelling() -> None:
    want = Symbol("BTC", "USD")
    assert parse_symbol("BTC/USD") == want  # WS v2 / display
    assert parse_symbol("XBT/USD") == want  # wsname (base remapped)
    assert parse_symbol("XBTUSD") == want  # REST altname
    assert parse_symbol("BTCUSD") == want  # bare concatenation
    assert parse_symbol("  btc/usd  ") == want  # trimmed + upper


def test_symbol_forms() -> None:
    s = Symbol("BTC", "USD")
    assert s.ws == "BTC/USD"
    assert s.display == "BTC/USD"
    assert s.rest == "XBTUSD"  # base remapped to Kraken code


def test_symbol_forms_doge_remap_and_plain() -> None:
    assert parse_symbol("XDG/USD").rest == "XDGUSD"  # DOGE -> XDG
    sol = parse_symbol("SOLUSD")
    assert (sol.ws, sol.rest) == ("SOL/USD", "SOLUSD")  # no remap needed


# ---- the bus ----


def test_hub_emits_once_and_dedupes() -> None:
    hub = SymbolHub()
    seen: list[Symbol] = []
    hub.changed.connect(seen.append)

    hub.set("XBTUSD")
    assert seen == [Symbol("BTC", "USD")]
    assert hub.current == Symbol("BTC", "USD")

    # Same market in a different spelling must NOT re-emit (breaks feedback loops).
    hub.set("BTC/USD")
    assert seen == [Symbol("BTC", "USD")]

    # A genuinely different market emits again.
    hub.set("ETHUSD")
    assert seen[-1] == Symbol("ETH", "USD")
    assert len(seen) == 2


def test_hub_ignores_empty_and_unparseable() -> None:
    hub = SymbolHub()
    seen: list[Symbol] = []
    hub.changed.connect(seen.append)
    hub.set("")
    assert seen == []
    assert hub.current is None
