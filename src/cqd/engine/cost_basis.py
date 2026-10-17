"""Cost basis reconstruction from Kraken trade history.

True running average-cost method, computed per (asset, quote) group:

- Buys raise the basis by their full cost PLUS the buy fee (when the fee is in
  the quote currency, which is how Kraken's TradesHistory reports it), so
  `avg_cost` / break-even is what you actually paid per unit.
- Sells release basis at the CURRENT average cost and realize PnL against it,
  net of the sell fee,
  so a sell never changes the average cost of what remains and the basis can
  never go negative (the old net-cash method went negative after profitable
  partial sells).
- Costs stay denominated in the trade's QUOTE currency and are never converted
  or summed across quotes: a BTC-quoted DOT position has a BTC cost basis,
  and the UI labels it as such. Historical USD conversion is deferred.

FIFO and HIFO tax lots live in `engine.tax_lots`, on the same fee rules.
"""

from __future__ import annotations

from dataclasses import dataclass

_EPS = 1e-12


@dataclass
class CostBasisResult:
    asset: str
    quantity: float
    total_cost: float
    avg_cost: float
    fees_paid: float
    quote: str = "USD"
    realized_pnl: float = 0.0
    # True when sells exceeded tracked buys (transfer-ins or history gaps);
    # the excess is ignored rather than guessed, so basis stays trustworthy.
    oversold: bool = False

    @property
    def break_even_price(self) -> float:
        return self.avg_cost

    def required_price_for_multiple(self, multiple: float) -> float:
        """Price needed for the position to be worth `multiple` x its cost."""
        return self.avg_cost * multiple


def quote_fee(trade: dict, quote: str) -> float:
    """The trade's fee in its quote currency (0 if absent or in another asset).

    Kraken charges trade fees in the quote currency; a fee tagged with some
    other currency is not silently mixed into a quote-denominated basis.
    """
    fee = trade.get("fee")
    if not isinstance(fee, dict):
        return 0.0
    currency = fee.get("currency")
    if currency not in (None, "", quote):
        return 0.0
    return float(fee.get("cost", 0.0) or 0.0)


def _zero(asset: str, quote: str) -> CostBasisResult:
    return CostBasisResult(asset, 0.0, 0.0, 0.0, 0.0, quote=quote)


def _accumulate(asset: str, quote: str, legs: list[dict]) -> CostBasisResult:
    """Run the average-cost method over one (asset, quote) group in time order."""
    qty = 0.0
    basis = 0.0
    fees = 0.0
    realized = 0.0
    oversold = False

    # Stable order: timestamp when present, original position otherwise/ties.
    ordered = sorted(enumerate(legs), key=lambda it: (float(it[1].get("timestamp") or 0.0), it[0]))
    for _, t in ordered:
        amount = float(t["amount"])
        cost = float(t["cost"])
        fee = t.get("fee")
        if isinstance(fee, dict):
            fees += float(fee.get("cost", 0.0))
        if amount <= 0:
            continue
        fee_q = quote_fee(t, quote)
        if t["side"] == "buy":
            qty += amount
            basis += cost + fee_q
            continue
        # Sell: release basis at the running average, realize the difference.
        if amount > qty + _EPS:
            oversold = True
        matched = min(amount, qty)
        if matched > 0:
            avg = basis / qty
            unit_price = cost / amount
            realized += matched * (unit_price - avg) - fee_q * (matched / amount)
            basis -= matched * avg
            qty -= matched
        if qty < _EPS:
            qty = 0.0
            basis = 0.0

    avg_cost = basis / qty if qty > _EPS else 0.0
    return CostBasisResult(
        asset=asset,
        quantity=qty,
        total_cost=basis,
        avg_cost=avg_cost,
        fees_paid=fees,
        quote=quote,
        realized_pnl=realized,
        oversold=oversold,
    )


def cost_basis_by_quote(trades: list[dict], asset: str) -> dict[str, CostBasisResult]:
    """Cost basis for `asset` per quote currency it was traded against.

    `trades` are normalized dicts: symbol ("BASE/QUOTE"), side ("buy"|"sell"),
    amount, price, cost, fee={"cost", "currency"}, optional timestamp.
    """
    groups: dict[str, list[dict]] = {}
    prefix = f"{asset}/"
    for t in trades:
        symbol = t.get("symbol", "")
        if symbol.startswith(prefix):
            groups.setdefault(symbol[len(prefix) :], []).append(t)
    return {quote: _accumulate(asset, quote, legs) for quote, legs in groups.items()}


def reconstruct_cost_basis(
    trades: list[dict], asset: str, quote: str | None = None
) -> CostBasisResult:
    """Cost basis for `asset`, in one quote currency.

    With `quote` given, only that group is computed. With `quote=None`, the
    dominant group is returned: largest remaining quantity, then largest basis,
    then alphabetical (deterministic). Quotes are never mixed into one number.
    """
    by_quote = cost_basis_by_quote(trades, asset)
    if quote is not None:
        return by_quote.get(quote, _zero(asset, quote))
    if not by_quote:
        return _zero(asset, "USD")
    ranked = sorted(by_quote.items(), key=lambda kv: (-kv[1].quantity, -kv[1].total_cost, kv[0]))
    return ranked[0][1]
