"""Cost basis reconstruction from Kraken trade history.

Uses the running average-cost method:
  avg_cost = (cumulative_buy_cost - cumulative_sell_proceeds) / current_qty

This matches what most retail users mentally track as "what did I pay".
For tax purposes you may want FIFO or HIFO; that's a future module.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class CostBasisResult:
    asset: str
    quantity: float
    total_cost_usd: float
    avg_cost: float
    fees_paid_usd: float

    @property
    def break_even_price(self) -> float:
        return self.avg_cost

    def required_price_for_multiple(self, multiple: float) -> float:
        """Price needed for the position to be worth `multiple` x its cost."""
        return self.avg_cost * multiple


def reconstruct_cost_basis(trades: list[dict], asset: str) -> CostBasisResult:
    """Compute cost basis for `asset` from a ccxt-shaped trade list.

    `trades` is the output of ccxt's fetch_my_trades(). Each entry has:
      symbol, side ("buy"|"sell"), amount, price, cost, fee={"cost", "currency"}.
    """
    if not trades:
        return CostBasisResult(asset, 0.0, 0.0, 0.0, 0.0)

    df = pd.DataFrame(trades)
    df = df[df["symbol"].str.startswith(f"{asset}/")]
    if df.empty:
        return CostBasisResult(asset, 0.0, 0.0, 0.0, 0.0)

    df["signed_amount"] = df.apply(
        lambda r: r["amount"] if r["side"] == "buy" else -r["amount"], axis=1
    )
    df["signed_cost"] = df.apply(
        lambda r: r["cost"] if r["side"] == "buy" else -r["cost"], axis=1
    )

    qty = float(df["signed_amount"].sum())
    total_cost = float(df["signed_cost"].sum())
    fees = float(
        df["fee"]
        .apply(lambda f: f.get("cost", 0.0) if isinstance(f, dict) else 0.0)
        .sum()
    )

    avg_cost = total_cost / qty if qty > 0 else 0.0

    return CostBasisResult(
        asset=asset,
        quantity=qty,
        total_cost_usd=total_cost,
        avg_cost=avg_cost,
        fees_paid_usd=fees,
    )
