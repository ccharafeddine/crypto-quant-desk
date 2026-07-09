"""Order validation: pair precision, exchange minimums, and the size cap.

Pure functions - no I/O, no Qt. `PairSpec` is built from Kraken's AssetPairs
response (cached by the caller); `validate_order` returns EVERY violation as a
human-readable string so the ticket can show all problems at once. An empty
list means the order passes. The max-order-value cap applies in paper and live
mode alike and cannot be bypassed from the UI (OrderService re-validates).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class PairSpec:
    """The subset of Kraken's AssetPairs entry that order validation needs."""

    pair: str  # friendly form used for orders, e.g. "BTCUSD"
    wsname: str  # "XBT/USD" per Kraken; display uses the normalizer instead
    base: str
    quote: str
    price_decimals: int  # AssetPairs "pair_decimals"
    lot_decimals: int  # AssetPairs "lot_decimals"
    ordermin: float  # minimum volume, base units
    costmin: float  # minimum order cost, quote units (0 when absent)

    @classmethod
    def from_asset_pairs_entry(cls, classic_pair: str, entry: dict[str, Any]) -> "PairSpec":
        """Build from one AssetPairs item ({classic_pair: entry})."""
        altname = str(entry.get("altname") or classic_pair)
        return cls(
            pair=altname,
            wsname=str(entry.get("wsname") or altname),
            base=str(entry.get("base") or ""),
            quote=str(entry.get("quote") or ""),
            price_decimals=int(entry.get("pair_decimals", 8)),
            lot_decimals=int(entry.get("lot_decimals", 8)),
            ordermin=float(entry.get("ordermin", 0.0) or 0.0),
            costmin=float(entry.get("costmin", 0.0) or 0.0),
        )


def _decimals(value: float | str) -> int:
    """Number of decimal places in `value` as the user entered it."""
    try:
        d = Decimal(str(value)).normalize()
    except InvalidOperation:
        return 0
    exponent = d.as_tuple().exponent
    return max(0, -int(exponent)) if isinstance(exponent, int) else 0


#: Order types that require a primary price.
PRICED_TYPES = frozenset(
    {"limit", "stop-loss", "take-profit", "stop-loss-limit", "take-profit-limit"}
)
#: Order types that require a secondary price (the post-trigger limit).
PRICE2_TYPES = frozenset({"stop-loss-limit", "take-profit-limit"})
#: The full supported suite (trailing stops use a relative offset as price).
ORDER_TYPES = PRICED_TYPES | {"market", "trailing-stop", "trailing-stop-limit"}


def validate_order(
    spec: PairSpec,
    *,
    side: str,
    ordertype: str,
    volume: float,
    price: float | None = None,
    price2: float | None = None,
    mark: float | None = None,
    max_order_value: float | None = None,
) -> list[str]:
    """All violations for one prospective order; empty list = valid.

    `mark` (latest price) is used to estimate the order value for market and
    trailing orders; priced orders use their own limit/trigger price. When no
    price source exists at all, the value cap CANNOT be checked and that is
    reported as a violation (fail closed, never fail open).
    """
    problems: list[str] = []

    if side not in ("buy", "sell"):
        problems.append(f"Unknown side '{side}'.")
    if ordertype not in ORDER_TYPES:
        problems.append(f"Unsupported order type '{ordertype}'.")
        return problems  # nothing else is meaningful

    # --- volume ---
    if volume <= 0:
        problems.append("Volume must be positive.")
    else:
        if spec.ordermin and volume < spec.ordermin:
            problems.append(
                f"Volume {volume:g} is below the pair minimum {spec.ordermin:g} {spec.base or 'base'}."
            )
        if _decimals(volume) > spec.lot_decimals:
            problems.append(
                f"Volume has {_decimals(volume)} decimals; {spec.pair} allows {spec.lot_decimals}."
            )

    # --- prices ---
    needs_price = ordertype in PRICED_TYPES or ordertype.startswith("trailing-stop")
    if needs_price:
        if price is None or price <= 0:
            problems.append(f"'{ordertype}' requires a positive price.")
        elif not ordertype.startswith("trailing-stop") and _decimals(price) > spec.price_decimals:
            problems.append(
                f"Price has {_decimals(price)} decimals; {spec.pair} allows {spec.price_decimals}."
            )
    if ordertype in PRICE2_TYPES:
        if price2 is None or price2 <= 0:
            problems.append(f"'{ordertype}' requires a secondary (limit) price.")
        elif _decimals(price2) > spec.price_decimals:
            problems.append(
                f"Secondary price has {_decimals(price2)} decimals; "
                f"{spec.pair} allows {spec.price_decimals}."
            )

    # --- order value: exchange minimum and OUR cap ---
    # Trailing offsets are relative, so only the mark prices those orders.
    unit_price: float | None
    if ordertype == "market" or ordertype.startswith("trailing-stop"):
        unit_price = mark
    else:
        unit_price = price if (price and price > 0) else mark

    if volume > 0:
        if unit_price is None or unit_price <= 0:
            problems.append(
                "Order value cannot be estimated (no price available); refusing to validate the size cap."
            )
        else:
            value = volume * unit_price
            if spec.costmin and value < spec.costmin:
                problems.append(
                    f"Order value {value:.2f} is below the pair minimum cost "
                    f"{spec.costmin:g} {spec.quote or 'quote'}."
                )
            if max_order_value is not None and value > max_order_value:
                problems.append(
                    f"Order value {value:,.2f} exceeds the max order cap "
                    f"{max_order_value:,.2f} (Settings > Trading)."
                )

    return problems
