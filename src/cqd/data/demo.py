"""Demo data source: a baked sample book valued with REAL CLI market data.

DemoClient lets the app render a meaningful portfolio/risk cockpit with no Kraken
keys. It is a drop-in for KrakenClient (same async surface) used by
compute_account_risk and the Positions panel.

Hard rule preserved: all market data still comes from the Kraken CLI. DemoClient
holds an internal real KrakenClient (no keys, public data only) and forwards
get_marks / get_ohlc_closes to it unchanged. It never fabricates prices or
candles. Only the holdings (balance) and the trade history are synthetic.
"""

from __future__ import annotations

from typing import Any

from cqd.data.exchange import KrakenClient

# Baked sample book: bare-asset -> quantity, the same shape get_balance produces.
# Quantities chosen so every asset is comfortably above the $1 dust threshold and
# weights are a sensible spread (no single asset dominates) at roughly current
# prices. Two cash legs (USD + USDC) plus four risk assets exercise vol, BTC
# beta, HHI, effective bets, and concentration.
SAMPLE_BOOK: dict[str, float] = {
    "BTC": 0.12,
    "ETH": 2.5,
    "SOL": 40.0,
    "USD": 2500.0,
    "USDC": 1500.0,
    "ADA": 4_000.0,
}

# Synthetic buys (cost_basis normalized shape) consistent with SAMPLE_BOOK: each
# crypto asset's legs sum to its held quantity. Cash (USD/USDC) has no trades.
# Fixed timestamps (no wall-clock) keep this deterministic. Entry prices sit
# below current marks so break-even/cost-basis render sensibly.
_TRADE_SPEC: dict[str, list[tuple[float, float, int]]] = {
    # symbol(slash): [(amount, price, unix_ts), ...]
    "BTC/USD": [(0.07, 42000.0, 1704067200), (0.05, 55000.0, 1714521600)],
    "ETH/USD": [(1.5, 1800.0, 1704067200), (1.0, 2400.0, 1714521600)],
    "SOL/USD": [(25.0, 60.0, 1704067200), (15.0, 95.0, 1714521600)],
    "ADA/USD": [(2_500.0, 0.45, 1704067200), (1_500.0, 0.62, 1714521600)],
}


def _build_trades() -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for symbol, legs in _TRADE_SPEC.items():
        for amount, price, ts in legs:
            cost = round(amount * price, 8)
            trades.append(
                {
                    "symbol": symbol,
                    "side": "buy",
                    "amount": amount,
                    "price": price,
                    "cost": cost,
                    # Kraken charges fees in the quote asset; ~16 bps taker.
                    "fee": {"cost": round(cost * 0.0016, 8), "currency": "USD"},
                    "timestamp": float(ts),
                }
            )
    return trades


SAMPLE_TRADES: list[dict[str, Any]] = _build_trades()


class DemoClient:
    """KrakenClient-compatible client with synthetic holdings, real prices."""

    is_demo = True

    def __init__(self) -> None:
        # Internal real CLI client, no keys: market data is public-only. Keys are
        # never injected because get_marks/get_ohlc_closes are public calls.
        self._client = KrakenClient()

    async def __aenter__(self) -> "DemoClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    # ---------- synthetic account data ----------

    async def get_balance(self) -> dict[str, float]:
        return dict(SAMPLE_BOOK)

    async def get_trades(
        self, *, start: int | None = None, end: int | None = None
    ) -> list[dict[str, Any]]:
        return [dict(t) for t in SAMPLE_TRADES]

    # ---------- market data: delegate to the real CLI, unchanged ----------

    async def get_marks(self, pairs: list[str]) -> dict[str, float]:
        return await self._client.get_marks(pairs)

    async def get_ohlc_closes(
        self, pair: str, *, interval: int = 1440, since: int | None = None
    ) -> list[tuple[int, float]]:
        return await self._client.get_ohlc_closes(pair, interval=interval, since=since)
