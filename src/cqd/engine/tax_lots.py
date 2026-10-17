"""Tax-lot accounting (FIFO, HIFO) next to the running average cost.

Pure. Same normalized trade dicts as `cost_basis` (symbol "BASE/QUOTE", side,
amount, cost, fee, timestamp), one quote at a time, never mixed. Buy fees in
the quote currency go into the lot's cost; sell fees reduce proceeds, exactly
as the average-cost method books them, so the three methods differ only in
WHICH units a sale consumes: realized + unrealized (total P&L at a mark) is
the same under every method, the identity the tests pin.

Not tax advice: jurisdictions differ (wash sales, specific-ID rules); this is
the arithmetic, not a filing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cqd.engine.cost_basis import quote_fee, reconstruct_cost_basis

_EPS = 1e-12
METHODS = ("fifo", "hifo")


@dataclass
class Lot:
    opened: float  # timestamp
    qty: float
    unit_cost: float  # quote per unit, buy fee included


@dataclass(frozen=True)
class Disposal:
    when: float
    qty: float
    proceeds: float
    cost: float
    opened: float

    @property
    def gain(self) -> float:
        return self.proceeds - self.cost

    def holding_days(self) -> float:
        return (self.when - self.opened) / 86400.0


@dataclass
class LotResult:
    asset: str
    quote: str
    method: str
    realized: float = 0.0
    open_lots: list[Lot] = field(default_factory=list)
    disposals: list[Disposal] = field(default_factory=list)
    oversold: bool = False

    @property
    def quantity(self) -> float:
        return sum(lot.qty for lot in self.open_lots)

    @property
    def basis(self) -> float:
        return sum(lot.qty * lot.unit_cost for lot in self.open_lots)

    def unrealized(self, mark: float) -> float:
        return self.quantity * mark - self.basis

    def long_term_realized(self, days: float = 365.0) -> float:
        return sum(d.gain for d in self.disposals if d.holding_days() > days)


def _pick(open_lots: list[Lot], method: str) -> int:
    if method == "fifo":
        return 0
    return max(range(len(open_lots)), key=lambda k: open_lots[k].unit_cost)


def lots(trades: list[dict], asset: str, quote: str = "USD", method: str = "fifo") -> LotResult:
    if method not in METHODS:
        raise ValueError(f"unknown lot method {method!r}")
    pair = f"{asset}/{quote}"
    legs = [t for t in trades if t.get("symbol") == pair]
    ordered = sorted(enumerate(legs), key=lambda it: (float(it[1].get("timestamp") or 0.0), it[0]))
    res = LotResult(asset, quote, method)
    for _, t in ordered:
        amount, cost = float(t["amount"]), float(t["cost"])
        if amount <= 0:
            continue
        fee = quote_fee(t, quote)
        when = float(t.get("timestamp") or 0.0)
        if t["side"] == "buy":
            res.open_lots.append(Lot(when, amount, (cost + fee) / amount))
            continue
        unit_proceeds = (cost - fee) / amount
        remaining = amount
        while remaining > _EPS and res.open_lots:
            i = _pick(res.open_lots, method)
            lot = res.open_lots[i]
            take = min(lot.qty, remaining)
            d = Disposal(when, take, take * unit_proceeds, take * lot.unit_cost, lot.opened)
            res.disposals.append(d)
            res.realized += d.gain
            lot.qty -= take
            remaining -= take
            if lot.qty <= _EPS:
                res.open_lots.pop(i)
        if remaining > _EPS:
            res.oversold = True  # sold more than tracked buys: ignore, as avg cost does
    return res


@dataclass(frozen=True)
class LotComparison:
    asset: str
    quote: str
    quantity: float
    mark: float
    rows: dict[str, dict[str, float]]  # method -> realized/basis/unrealized


def compare_methods(
    trades: list[dict], asset: str, mark: float, quote: str = "USD"
) -> LotComparison:
    """Realized, remaining basis and unrealized under average cost, FIFO, HIFO."""
    avg = reconstruct_cost_basis(trades, asset, quote)
    rows = {
        "average": {
            "realized": avg.realized_pnl,
            "basis": avg.total_cost,
            "unrealized": avg.quantity * mark - avg.total_cost,
        }
    }
    qty = avg.quantity
    for m in METHODS:
        r = lots(trades, asset, quote, m)
        rows[m] = {"realized": r.realized, "basis": r.basis, "unrealized": r.unrealized(mark)}
        qty = r.quantity
    return LotComparison(asset, quote, qty, mark, rows)
