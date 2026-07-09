"""Portfolio-risk orchestration: balance -> weights -> engine PortfolioRisk.

This is the assembly layer between the CLI data feed and the pure risk engine.
It values the user's balance at current marks to build a weights Series, drives
the returns feed, and calls compute_portfolio_risk. No risk math lives here -
every number in PortfolioRisk comes from the engine, passed through untouched.

The only I/O is via an injected KrakenClient; compute_weights is pure and sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from cqd.data.returns import build_returns_frame
from cqd.engine.risk import PortfolioRisk, compute_portfolio_risk


class EmptyPortfolioError(Exception):
    """No priceable, above-dust holdings to build weights from."""


@dataclass
class AccountRisk:
    """Everything the Risk panel needs: engine result plus caveats.

    `risk` is the engine output, untouched. `weights`/`total_usd` describe the
    valued book; `info` carries exclusions (dust, unpriced) so the UI can show
    both the numbers and what was left out.
    """

    risk: PortfolioRisk
    weights: pd.Series
    total_usd: float
    info: dict[str, Any]


def compute_weights(
    balances: dict[str, float],
    marks: dict[str, float],
    *,
    quote: str = "USD",
    min_usd: float = 1.0,
) -> tuple[pd.Series, dict[str, Any]]:
    """Build USD-weighted portfolio weights from balances valued at marks.

    `balances` is bare-asset -> qty (from normalize_balance). `marks` is
    slash-symbol -> price (from normalize_ticker, e.g. {"BTC/USD": 70860.0}).

    Each held asset is valued at qty * mark("{asset}/{quote}"); the quote asset
    itself (USD cash) is valued at qty (mark 1.0). Assets with no available mark
    are excluded and reported as `unpriced` (never silently dropped); holdings
    worth less than `min_usd` are excluded as `dust`. Weights are each asset's
    USD value / total USD value, a pd.Series indexed by BARE symbol, summing ~1.

    Returns (weights, info) where info reports total_usd, per-asset usd values,
    and the dust/unpriced exclusions.
    """
    values: dict[str, float] = {}
    dust: dict[str, float] = {}
    unpriced: list[str] = []

    for asset, qty in balances.items():
        if not qty or qty <= 0:
            continue
        if asset == quote:
            usd = float(qty)  # cash: mark is 1.0 by definition
        else:
            mark = marks.get(f"{asset}/{quote}")
            if mark is None:
                unpriced.append(asset)
                continue
            usd = float(qty) * float(mark)

        if usd < min_usd:
            dust[asset] = usd
            continue
        values[asset] = usd

    total_usd = float(sum(values.values()))
    if total_usd <= 0:
        weights = pd.Series(dtype=float)
    else:
        weights = pd.Series(
            {a: v / total_usd for a, v in values.items()}, dtype=float
        )

    info: dict[str, Any] = {
        "total_usd": total_usd,
        "values_usd": values,
        "dust": dust,
        "unpriced": unpriced,
        "min_usd": min_usd,
    }
    return weights, info


async def compute_account_risk(
    client: Any, *, days: int = 90, min_usd: float = 1.0
) -> AccountRisk:
    """Fetch the account, build weights + returns, and run the risk engine.

    Raises EmptyPortfolioError if nothing priceable above `min_usd` remains.
    A held asset priced via ticker but lacking a USD OHLC pair yields a
    missing/empty returns column; that surfaces here and is left to propagate
    (no speculative quote fallback yet).
    """
    balances = await client.get_balance()
    held = [a for a, q in balances.items() if q and q > 0]

    # Friendly fetch form is bare+"USD"; normalize_ticker returns slash keys, so
    # compute_weights indexes marks by "{asset}/USD" (mirrors positions.py).
    marks: dict[str, float] = {}
    pairs = [f"{a}USD" for a in held if a != "USD"]
    if pairs:
        marks = await client.get_marks(pairs)

    weights, info = compute_weights(balances, marks, min_usd=min_usd)
    if weights.empty:
        raise EmptyPortfolioError(
            "No priceable holdings above the dust threshold "
            f"(min_usd={min_usd}); info={info}"
        )

    returns = await build_returns_frame(client, list(weights.index), days=days)
    # Assets whose OHLC fetch failed were dropped from the frame; the engine
    # renormalizes weights over the remaining columns. Surface them as a caveat.
    info["returns_dropped"] = returns.attrs.get("dropped_assets", [])
    risk = compute_portfolio_risk(weights, returns, btc_col="BTC")

    return AccountRisk(
        risk=risk,
        weights=weights,
        total_usd=info["total_usd"],
        info=info,
    )
