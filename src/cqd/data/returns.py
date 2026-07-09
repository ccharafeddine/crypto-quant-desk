"""Daily-returns feed for compute_portfolio_risk.

Pure assembly + orchestration: the only I/O is via an injected KrakenClient
(`get_ohlc_closes`). This module turns per-asset CLI close series into the exact
frame the risk engine consumes — a daily SIMPLE-return DataFrame, one column per
asset keyed by BARE symbol ("BTC", "ETH"), on a DatetimeIndex, with the BTC
benchmark column always present.

The price -> returns transform is split into a pure sync helper
(`closes_to_returns`) so the alignment / pct_change logic is unit-testable with
no client or network.

Conventions (match the engine, do not re-implement its math here):
  - Simple returns via pct_change. Annualization (365) happens inside the engine.
  - Gaps: forward-fill missing CLOSES before pct_change so a missing day does not
    manufacture a spurious jump. Returns themselves are never filled with 0; the
    single leading all-NaN row from pct_change is dropped, and any per-asset
    leading NaN (assets with shorter history) is left for the engine to handle
    (it does pairwise dropna).
  - Cash/quote currencies (USD, and fiat/stablecoins) have no own market and no
    return series. They are NOT fetched (a {quote}{quote} pair like USDUSD does
    not exist and the CLI rejects it). Instead each is given a constant-1.0
    price series, so pct_change yields an all-zero column: the asset stays in the
    frame, contributes zero vol and zero beta, and still counts toward weights
    and concentration (HHI) downstream. The cash set is reused from the
    normalizer's known-quote suffixes, not hardcoded here.
"""

from __future__ import annotations

from typing import Protocol

import pandas as pd

from cqd.data.normalize import _QUOTE_SUFFIXES, translate_asset

# Bare engine symbols treated as cash (no own market). Derived from the
# normalizer's known quote/fiat/stable suffixes so the two stay in sync; e.g.
# ZUSD/USD -> USD, ZEUR/EUR -> EUR, USDT, USDC, DAI. A stablecoin that DOES have
# a USD market (USDC/USD) is still treated as cash: simpler and correct, since a
# stable held as cash carries ~zero risk and its tiny deviations from $1 are
# negligible for portfolio vol/beta.
_CASH_SYMBOLS: frozenset[str] = frozenset(translate_asset(q) for q in _QUOTE_SUFFIXES)


def _is_cash(symbol: str, quote: str) -> bool:
    """True if `symbol` is the portfolio quote currency or a known fiat/stable."""
    return symbol == quote or symbol in _CASH_SYMBOLS


class _OHLCClient(Protocol):
    """Minimal client surface this module needs (KrakenClient satisfies it)."""

    async def get_ohlc_closes(
        self, pair: str, *, interval: int = ..., since: int | None = ...
    ) -> list[tuple[int, float]]: ...


def closes_to_returns(
    closes: dict[str, list[tuple[int, float]]], *, days: int = 90
) -> pd.DataFrame:
    """Aligned daily simple returns from per-asset close series.

    `closes` maps a BARE symbol to its ascending [(unix_seconds, close)] list.

    Steps: index each series by normalized date -> outer-join into one frame on
    the union of dates -> sort -> forward-fill closes -> trim to the last `days`
    price rows -> pct_change -> drop the leading all-NaN row. Columns are the
    bare symbols in input order; index is a DatetimeIndex.
    """
    series: dict[str, pd.Series] = {}
    for sym, points in closes.items():
        if not points:
            series[sym] = pd.Series(dtype=float, name=sym)
            continue
        idx = pd.to_datetime([t for t, _ in points], unit="s").normalize()
        s = pd.Series([c for _, c in points], index=idx, name=sym, dtype=float)
        # Collapse duplicate timestamps (keep the latest close for that day).
        s = s[~s.index.duplicated(keep="last")]
        series[sym] = s

    # Outer-join on the union of dates; pandas aligns on the index automatically.
    prices = pd.DataFrame(series).sort_index()

    # Forward-fill missing closes BEFORE computing returns (no spurious jumps).
    prices = prices.ffill()

    # The ~720-cap daily series already exceeds any 30-90d window, so trimming
    # client-side to the last `days` price rows is sufficient and avoids an
    # extra `since` round-trip. days price rows -> days-1 return rows.
    if days is not None and len(prices) > days:
        prices = prices.iloc[-days:]

    # fill_method=None: we already ffilled; this also silences pandas' default-
    # fill deprecation. The first row is all-NaN by construction and is dropped.
    returns = prices.pct_change(fill_method=None)
    if len(returns) > 0:
        returns = returns.iloc[1:]
    return returns


async def build_returns_frame(
    client: _OHLCClient,
    assets: list[str],
    *,
    days: int = 90,
    interval: int = 1440,
    btc_symbol: str = "BTC",
    quote: str = "USD",
) -> pd.DataFrame:
    """Fetch CLI closes for `assets` (+ the BTC benchmark) and assemble returns.

    `assets` are BARE engine symbols (e.g. ["BTC", "ETH", "SOL"]), derived by the
    caller from the user's balance. `btc_symbol` is always fetched and included
    even if the user holds no BTC, because the risk engine needs it as the beta
    benchmark column; the weights (built elsewhere) simply won't weight it.

    Each non-cash bare symbol maps to the CLI's friendly pair (bare + `quote`);
    closes come back ascending as [(unix_seconds, close)]. Cash/quote currencies
    (see `_is_cash`) are never fetched - they get a constant-1.0 series (zero
    returns) on the real assets' date union, so they stay in the frame as
    zero-vol/zero-beta columns. `quote` defaults to "USD", matching the weights
    built by compute_weights. Returns a DataFrame with the BTC column guaranteed
    present, ready for compute_portfolio_risk.
    """
    # Ensure the BTC benchmark is in the fetch set; preserve order, dedupe.
    fetch_set = list(dict.fromkeys([*assets, btc_symbol]))

    # Fetch only the real (non-cash) assets; cash has no {quote}{quote} market.
    # One asset's failed fetch (no {sym}{quote} pair, transient CLI error) drops
    # that asset instead of failing the whole frame; the engine renormalizes
    # weights over the remaining columns. Dropped symbols are reported via
    # DataFrame.attrs["dropped_assets"] so the caller can surface a caveat.
    real_closes: dict[str, list[tuple[int, float]]] = {}
    dropped: list[str] = []
    for sym in fetch_set:
        if _is_cash(sym, quote):
            continue
        pair = f"{sym}{quote}"
        try:
            real_closes[sym] = await client.get_ohlc_closes(pair, interval=interval)
        except Exception:  # noqa: BLE001 - degrade per asset, never per frame
            dropped.append(sym)

    # Union of real timestamps; cash series are pinned to these dates at 1.0.
    timestamps = sorted({ts for pts in real_closes.values() for ts, _ in pts})

    # Rebuild in original fetch order so column order is stable.
    closes: dict[str, list[tuple[int, float]]] = {}
    for sym in fetch_set:
        if sym in dropped:
            continue
        if sym in real_closes:
            closes[sym] = real_closes[sym]
        else:
            closes[sym] = [(ts, 1.0) for ts in timestamps]

    frame = closes_to_returns(closes, days=days)
    frame.attrs["dropped_assets"] = dropped
    return frame
