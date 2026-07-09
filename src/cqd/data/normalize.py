"""Pure normalizer: raw Kraken CLI JSON -> shapes the engine consumers expect.

The CLI (`kraken ... -o json`) returns classic Kraken codes on output regardless
of the friendly pair we send in (BTCUSD -> XXBTZUSD), and every price/qty/volume
field is a STRING. This module is the single place that maps those classic codes
to the engine's bare symbols and parses strings to floats.

Pure by contract: no subprocess, no Qt, no network, no ccxt. Everything here is a
deterministic transform of a dict/list literal, so it is fully offline-testable
against captured JSON. The subprocess CLI wrapper lives elsewhere in data/ and
feeds raw JSON into these functions.

Engine targets this feeds:
  - cost_basis.reconstruct_cost_basis: list of trade dicts with keys
    symbol (slash "BASE/QUOTE"), side, amount, price, cost, fee={"cost","currency"}.
  - risk.compute_portfolio_risk: columns/weights indexed by bare symbols (BTC, ETH).
"""

from __future__ import annotations

from typing import Any

# Explicit alias table for the majors. This is the OFFLINE FALLBACK only; the
# AUTHORITATIVE classic->bare map will be injected later from `kraken assets` /
# `kraken pairs` once the CLI is keyed. Until then, this table + the X/Z
# heuristic below cover everything we normalize.
_ASSET_ALIASES: dict[str, str] = {
    "XXBT": "BTC",
    "XBT": "BTC",
    "XETH": "ETH",
    "XXRP": "XRP",
    "XLTC": "LTC",
    "XDG": "DOGE",
    "XXDG": "DOGE",  # real balance code is double-X; XDG is the pair/stripped form
    "ZUSD": "USD",
    "ZEUR": "EUR",
    "ZGBP": "GBP",
    "ZJPY": "JPY",
    # Identity for codes the API already returns bare.
    "BTC": "BTC",
    "ETH": "ETH",
    "SOL": "SOL",
    "USDT": "USDT",
    "USDC": "USDC",
}

# Fiat/stable quote codes. This is also the CASH set: a holding in any of these
# carries no market risk, and returns.py derives its cash columns from this
# tuple, so crypto quotes must NOT be added here (that would wrongly mark
# BTC/ETH as cash).
_QUOTE_SUFFIXES: tuple[str, ...] = (
    "ZUSD",
    "ZEUR",
    "ZGBP",
    "ZJPY",
    "USDT",
    "USDC",
    "DAI",
    "USD",
    "EUR",
    "GBP",
    "JPY",
)

# Crypto quote codes (XXBT/XBT -> BTC, XETH/ETH -> ETH). Real accounts trade
# alts against BTC/ETH (e.g. DOTXBT, ADAETH, XXDGXXBT), so split_pair must
# recognize these as quotes - but they are NOT cash, hence kept separate from
# _QUOTE_SUFFIXES.
_CRYPTO_QUOTE_SUFFIXES: tuple[str, ...] = ("XXBT", "XETH", "XBT", "ETH")

# For pair splitting only: all quote codes, LONGEST FIRST so the first
# endswith-match is the most specific (ZUSD before USD, XXBT before XBT). sorted
# is stable, so same-length codes keep their order (only one can match a pair).
_PAIR_QUOTE_SUFFIXES: tuple[str, ...] = tuple(
    sorted(_QUOTE_SUFFIXES + _CRYPTO_QUOTE_SUFFIXES, key=len, reverse=True)
)

# Classic X/Z-prefixed quote codes only ever appear in classic pairs, where the
# base is itself a classic code (XXBTZUSD, XETHXXBT, XXDGXXBT). A BARE base that
# happens to end in Z or X can fake one of these suffixes when joined to a bare
# quote: XTZUSD is XTZ+USD, not XT+ZUSD; REZUSD is REZ+USD. split_pair therefore
# accepts a classic-suffix match only when the leftover base also looks classic,
# and otherwise keeps scanning down to the bare quote.
_CLASSIC_QUOTE_SUFFIXES: frozenset[str] = frozenset(
    {"ZUSD", "ZEUR", "ZGBP", "ZJPY", "XXBT", "XETH"}
)


def _is_classic_base(base: str) -> bool:
    return base in _ASSET_ALIASES or (len(base) >= 4 and base[0] in ("X", "Z"))


def translate_asset(cli_asset: str) -> str:
    """Map a classic CLI asset code to the engine's bare symbol.

    Table-first; falls back to the X/Z prefix heuristic for codes we have not
    enumerated (4-char X-prefixed crypto -> strip X; 4-char Z-prefixed fiat ->
    strip Z). Anything else is returned unchanged.
    """
    # Kraken sub-balances carry a dot-suffix (.S staked, .HOLD, .B, .M, ...).
    # Fold them onto the base asset before translating, so DOT.S -> DOT and
    # USD.HOLD -> USD. This also avoids a later get_marks crash, where USD.HOLD
    # would otherwise be fetched as the nonexistent pair USD.HOLDUSD. Pair codes
    # never contain a dot, so this is safe for the split_pair callers too.
    if "." in cli_asset:
        cli_asset = cli_asset.split(".", 1)[0]
    if cli_asset in _ASSET_ALIASES:
        return _ASSET_ALIASES[cli_asset]
    # Heuristic fallback: classic 4-char codes carry an X (crypto) or Z (fiat)
    # prefix over a 3-char root. Newer assets have no prefix and pass through.
    if len(cli_asset) == 4 and cli_asset[0] in ("X", "Z"):
        return cli_asset[1:]
    return cli_asset


def split_pair(cli_pair: str) -> tuple[str, str]:
    """Split a classic CLI pair into (base_engine, quote_engine) bare symbols.

    Matches the longest known quote suffix (fiat/stable and crypto); the
    remainder is the base. Both halves are then translated via `translate_asset`.
    Classic-suffix matches (ZUSD, XXBT, ...) additionally require a classic-
    looking base, so bare bases ending in Z/X (XTZ, REZ) split correctly.
    """
    for suffix in _PAIR_QUOTE_SUFFIXES:
        if cli_pair.endswith(suffix) and len(cli_pair) > len(suffix):
            base = cli_pair[: -len(suffix)]
            if suffix in _CLASSIC_QUOTE_SUFFIXES and not _is_classic_base(base):
                continue
            return translate_asset(base), translate_asset(suffix)
    # No known suffix: translate the whole thing as base, empty quote.
    return translate_asset(cli_pair), ""


def slash_symbol(cli_pair: str) -> str:
    """Classic CLI pair -> engine slash form "BASE/QUOTE" (e.g. "BTC/USD")."""
    base, quote = split_pair(cli_pair)
    return f"{base}/{quote}"


def normalize_ticker(raw: dict[str, Any]) -> dict[str, float]:
    """{classic_pair: ticker_obj} -> {slash_symbol: last_price_float}.

    Last/mark price is float(c[0]) per the verified CLI contract.
    """
    out: dict[str, float] = {}
    for cli_pair, t in raw.items():
        out[slash_symbol(cli_pair)] = float(t["c"][0])
    return out


def normalize_ohlc(raw: dict[str, Any]) -> list[tuple[int, float]]:
    """OHLC response -> ascending list of (time:int, close:float) for the pair.

    The CLI returns {classic_pair: [[time, o, h, l, c, vwap, vol, count], ...],
    "last": cursor}. We drop the "last" pagination cursor and the single pair
    key, keeping only time and close (index 4). DataFrame assembly is a later,
    separate step.
    """
    rows: list[list[Any]] = []
    for key, value in raw.items():
        if key == "last":
            continue
        if isinstance(value, list):
            rows = value
            break
    out = [(int(row[0]), float(row[4])) for row in rows]
    out.sort(key=lambda r: r[0])
    return out


def normalize_balance(raw: dict[str, str]) -> dict[str, float]:
    """{classic_asset: qty_str} -> {bare_asset: qty_float}.

    Sub-balance folding (DOT.S -> DOT, USD.HOLD -> USD) means two raw keys can
    collapse to the same bare symbol, so quantities are SUMMED rather than
    overwritten (an account commonly holds both spot DOT and staked DOT.S).

    Verified against a live keyed `kraken balance -o json`: flat
    {classic_asset: qty_str} container with classic X/Z codes (XXBT, ZUSD,
    XXDG) plus dot-suffixed sub-balances.
    """
    out: dict[str, float] = {}
    for asset, qty in raw.items():
        bare = translate_asset(asset)
        out[bare] = out.get(bare, 0.0) + float(qty)
    return out


def normalize_ledgers(raw: Any) -> list[dict[str, Any]]:
    """Ledger response -> ascending list of engine-shaped ledger entries.

    Input is {"ledger": {<lid>: {...}}, "count": N} (REST/CLI shape) or a bare
    {<lid>: {...}} / list. Output per entry: refid, time (float unix), type,
    subtype, asset (bare symbol, sub-balances folded), amount, fee, balance.
    The equity-curve engine consumes this shape.
    """
    container: Any = raw
    if isinstance(raw, dict) and "ledger" in raw:
        container = raw["ledger"]
    if isinstance(container, dict):
        items = list(container.values())
    elif isinstance(container, list):
        items = container
    else:
        items = []

    out: list[dict[str, Any]] = []
    for e in items:
        out.append(
            {
                "refid": str(e.get("refid", "")),
                "time": float(e["time"]),
                "type": str(e.get("type", "")),
                "subtype": str(e.get("subtype", "")),
                "asset": translate_asset(str(e["asset"])),
                "amount": float(e["amount"]),
                "fee": float(e.get("fee", 0.0) or 0.0),
                "balance": float(e["balance"]) if e.get("balance") not in (None, "") else None,
            }
        )
    out.sort(key=lambda x: x["time"])
    return out


def normalize_depth(raw: Any) -> dict[str, list[tuple[float, float]]]:
    """Depth response -> {"bids": [(price, vol)], "asks": [(price, vol)]}.

    Input is {<classic_pair>: {"asks": [[price, vol, ts], ...], "bids": ...}}.
    Row order is preserved (Kraken returns best-first). Extra row fields
    beyond price/volume are dropped.
    """
    for value in raw.values() if isinstance(raw, dict) else ():
        if isinstance(value, dict) and "asks" in value and "bids" in value:
            return {
                "asks": [(float(r[0]), float(r[1])) for r in value["asks"]],
                "bids": [(float(r[0]), float(r[1])) for r in value["bids"]],
            }
    return {"asks": [], "bids": []}


def _iter_trade_objects(raw: Any) -> list[dict[str, Any]]:
    """Yield per-trade dicts from either container shape.

    Documented shape is {"trades": {<txid>: {...}}, "count": N}, but the CLI may
    pass through a bare list. Accept both.

    TODO: confirm the real container + per-trade field spelling against a live
    `kraken trades-history -o json` once the CLI is keyed (auth gap at capture).
    """
    if isinstance(raw, dict) and "trades" in raw:
        trades = raw["trades"]
        if isinstance(trades, dict):
            return list(trades.values())
        if isinstance(trades, list):
            return trades
    if isinstance(raw, list):
        return raw
    return []


def normalize_trades(raw: Any) -> list[dict[str, Any]]:
    """CLI trade history -> list of dicts in cost_basis.reconstruct_cost_basis shape.

    Output per trade: symbol (slash form so the engine's startswith("{asset}/")
    filter works unchanged), side, amount, price, cost, fee={"cost","currency"},
    timestamp. The CLI gives a bare `fee` string with no currency, so we
    synthesize the fee dict using the pair's QUOTE asset as the currency.
    """
    out: list[dict[str, Any]] = []
    for trade in _iter_trade_objects(raw):
        cli_pair = trade["pair"]
        _, quote = split_pair(cli_pair)
        out.append(
            {
                "symbol": slash_symbol(cli_pair),
                "side": trade["type"],
                "amount": float(trade["vol"]),
                "price": float(trade["price"]),
                "cost": float(trade["cost"]),
                "fee": {"cost": float(trade["fee"]), "currency": quote},
                "timestamp": float(trade["time"]),
            }
        )
    return out
