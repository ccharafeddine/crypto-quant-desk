"""Demo data source: a baked sample book valued with REAL market data.

DemoClient lets the app render a meaningful portfolio/risk cockpit with no
Kraken keys. It is a drop-in for the live clients (same async surface) used by
compute_account_risk and the Positions panel.

Market data stays real: DemoClient holds an injected market client (the REST
client by default - keyless, public endpoints only, works on Windows) and
forwards get_marks / get_ohlc_closes to it unchanged. It never fabricates
prices or candles. Only the holdings (balance) and trade history are synthetic.
"""

from __future__ import annotations

from typing import Any

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
    """Live-client-compatible client with synthetic holdings, real prices."""

    is_demo = True

    def __init__(self, market_client: Any | None = None) -> None:
        # Injected real market client, no keys: get_marks/get_ohlc_closes are
        # public calls, so credentials are never involved. Default is the REST
        # client (imported lazily to avoid a module cycle with client.py).
        if market_client is None:
            from cqd.data.rest import KrakenRESTClient

            market_client = KrakenRESTClient(api_key="", api_secret="")
        self._client = market_client

    async def __aenter__(self) -> "DemoClient":
        enter = getattr(self._client, "__aenter__", None)
        if enter is not None:
            await enter()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        exit_ = getattr(self._client, "__aexit__", None)
        if exit_ is not None:
            await exit_(exc_type, exc, tb)

    # ---------- synthetic account data ----------

    async def get_balance(self) -> dict[str, float]:
        return dict(SAMPLE_BOOK)

    async def get_trades(
        self, *, start: int | None = None, end: int | None = None
    ) -> list[dict[str, Any]]:
        return [dict(t) for t in SAMPLE_TRADES]

    async def get_ledgers(
        self, *, start: int | None = None, end: int | None = None
    ) -> list[dict[str, Any]]:
        """Synthetic ledger consistent with the sample book.

        Cash legs arrive as deposits at t0; each crypto buy produces the
        matching asset-credit / cash-debit pair, so the equity curve engine
        sees a coherent history (real market data still prices it).
        """
        entries: list[dict[str, Any]] = []
        t0 = 1704067200.0 - 86400.0  # the day before the first demo buy
        cash = SAMPLE_BOOK["USD"] + sum(
            amount * price for legs in _TRADE_SPEC.values() for amount, price, _ in legs
        )
        entries.append(
            {
                "refid": "DEMO-DEPOSIT",
                "time": t0,
                "type": "deposit",
                "subtype": "",
                "asset": "USD",
                "amount": cash,
                "fee": 0.0,
                "balance": cash,
            }
        )
        entries.append(
            {
                "refid": "DEMO-DEPOSIT-USDC",
                "time": t0,
                "type": "deposit",
                "subtype": "",
                "asset": "USDC",
                "amount": SAMPLE_BOOK["USDC"],
                "fee": 0.0,
                "balance": SAMPLE_BOOK["USDC"],
            }
        )
        usd = cash
        held: dict[str, float] = {}
        events: list[tuple[float, str, float, float]] = []  # ts, asset, qty, cost
        for symbol, legs in _TRADE_SPEC.items():
            asset = symbol.split("/")[0]
            for amount, price, ts in legs:
                events.append((float(ts), asset, amount, amount * price))
        for ts, asset, qty, cost in sorted(events):
            held[asset] = held.get(asset, 0.0) + qty
            usd -= cost
            entries.append(
                {
                    "refid": f"DEMO-{asset}-{int(ts)}",
                    "time": ts,
                    "type": "trade",
                    "subtype": "",
                    "asset": asset,
                    "amount": qty,
                    "fee": 0.0,
                    "balance": held[asset],
                }
            )
            entries.append(
                {
                    "refid": f"DEMO-USD-{int(ts)}",
                    "time": ts,
                    "type": "trade",
                    "subtype": "",
                    "asset": "USD",
                    "amount": -cost,
                    "fee": 0.0,
                    "balance": usd,
                }
            )
        return entries

    # ---------- market data: delegate to the real CLI, unchanged ----------

    async def get_marks(self, pairs: list[str]) -> dict[str, float]:
        return await self._client.get_marks(pairs)

    async def get_ohlc_closes(
        self, pair: str, *, interval: int = 1440, since: int | None = None
    ) -> list[tuple[int, float]]:
        return await self._client.get_ohlc_closes(pair, interval=interval, since=since)
