"""PaperBroker: order simulator with live marks and a persisted overlay.

Paper mode must feel like the real thing without ever touching Kraken's
private order endpoints: the broker keeps its own balance overlay (seeded from
the account snapshot or demo book), fills market orders at the live mark plus
slippage, rests limit/stop orders until a tick crosses them, charges simulated
maker/taker fees, and persists everything to paper_state.json so paper
positions survive restarts. This is also the harness the future autotrader
will be validated against before any live order.

Fill model (documented simplifications):
- market: fills immediately at mark +/- slippage (taker fee).
- limit: fills at the LIMIT price when marketable now (taker) or when a tick
  crosses it (maker). No partial fills, no queue position.
- stop-loss / take-profit: trigger on a tick crossing, then fill as market at
  the triggering mark +/- slippage.
- stop-loss-limit / take-profit-limit: trigger converts the order to a resting
  limit at price2.
- trailing-stop: `price` is an absolute offset; the trigger ratchets with the
  best mark seen since submission, fills as market when hit.
- Insufficient overlay funds reject at submit; resting orders re-check at fill
  and cancel with an error note instead of overdrawing.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from cqd.data.errors import OrderRejected

TAKER_FEE = 0.0040
MAKER_FEE = 0.0025
SLIPPAGE_BPS = 5.0


@dataclass
class PaperOrder:
    txid: str
    pair: str  # slash symbol, e.g. "BTC/USD"
    side: str  # "buy" | "sell"
    ordertype: str
    volume: float
    price: float | None = None  # limit price, trigger, or trailing offset
    price2: float | None = None  # post-trigger limit price
    status: str = "open"  # open | filled | cancelled
    created: float = field(default_factory=time.time)
    filled_price: float | None = None
    filled_time: float | None = None
    fee: float = 0.0
    error: str | None = None
    triggered: bool = False  # stop-limit crossed its trigger, now a limit
    best_mark: float | None = None  # trailing-stop ratchet anchor

    @property
    def is_open(self) -> bool:
        return self.status == "open"


def _split(pair: str) -> tuple[str, str]:
    base, _, quote = pair.partition("/")
    return base, quote


class PaperBroker:
    """Simulated broker over a persisted balance overlay."""

    def __init__(
        self,
        state_file: Path | None = None,
        *,
        slippage_bps: float = SLIPPAGE_BPS,
        taker_fee: float = TAKER_FEE,
        maker_fee: float = MAKER_FEE,
    ) -> None:
        self._state_file = state_file
        self._slippage = slippage_bps / 10_000.0
        self._taker = taker_fee
        self._maker = maker_fee
        self._seq = 0
        self.balances: dict[str, float] = {}
        self.orders: dict[str, PaperOrder] = {}
        self._load()

    # ---------- persistence ----------

    def _load(self) -> None:
        if self._state_file is None or not self._state_file.exists():
            return
        try:
            raw = json.loads(self._state_file.read_text(encoding="utf-8"))
            self._seq = int(raw["seq"])
            self.balances = {str(k): float(v) for k, v in raw["balances"].items()}
            self.orders = {o["txid"]: PaperOrder(**o) for o in raw["orders"]}
        except (OSError, ValueError, KeyError, TypeError):
            # Corrupt state: preserve it for inspection, start fresh.
            try:
                self._state_file.rename(self._state_file.with_suffix(".json.bak"))
            except OSError:
                pass
            self._seq = 0
            self.balances = {}
            self.orders = {}

    def _save(self) -> None:
        if self._state_file is None:
            return
        payload = {
            "seq": self._seq,
            "balances": self.balances,
            "orders": [asdict(o) for o in self.orders.values()],
        }
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(
                json.dumps(payload, separators=(",", ":")), encoding="utf-8"
            )
        except OSError:
            pass  # persistence is best-effort; in-memory state stays correct

    # ---------- seeding ----------

    def seed_if_empty(self, balances: dict[str, float]) -> None:
        """Adopt the account snapshot as the starting overlay, once."""
        if not self.balances:
            self.balances = dict(balances)
            self._save()

    # ---------- submission ----------

    def submit(
        self,
        *,
        pair: str,
        side: str,
        ordertype: str,
        volume: float,
        price: float | None = None,
        price2: float | None = None,
        mark: float,
    ) -> PaperOrder:
        """Accept one order; fills immediately when marketable."""
        self._seq += 1
        order = PaperOrder(
            txid=f"PAPER-{self._seq:06d}",
            pair=pair,
            side=side,
            ordertype=ordertype,
            volume=volume,
            price=price,
            price2=price2,
            best_mark=mark if ordertype.startswith("trailing-stop") else None,
        )

        if ordertype == "market":
            fill_price = self._slipped(mark, side)
            if not self._can_afford(order, fill_price):
                raise OrderRejected("EOrder:Insufficient funds (paper)")
            self.orders[order.txid] = order
            self._fill(order, fill_price, self._taker)
        elif ordertype == "limit" and self._limit_marketable(order, mark):
            if not self._can_afford(order, float(order.price)):
                raise OrderRejected("EOrder:Insufficient funds (paper)")
            self.orders[order.txid] = order
            self._fill(order, float(order.price), self._taker)
        else:
            # Resting order: a loose affordability check at the price it would
            # fill near, so obvious fat-fingers reject up front.
            ref = price if (price and ordertype != "trailing-stop") else mark
            if not self._can_afford(order, float(ref or mark)):
                raise OrderRejected("EOrder:Insufficient funds (paper)")
            self.orders[order.txid] = order

        self._save()
        return order

    # ---------- ticking ----------

    def on_tick(self, pair: str, mark: float) -> list[PaperOrder]:
        """Advance resting orders for `pair` against a new mark; returns fills."""
        fills: list[PaperOrder] = []
        for order in list(self.orders.values()):
            if not order.is_open or order.pair != pair:
                continue
            filled = self._advance(order, mark)
            if filled:
                fills.append(order)
        if fills:
            self._save()
        return fills

    def _advance(self, order: PaperOrder, mark: float) -> bool:
        ot = order.ordertype
        if ot == "limit" or order.triggered:
            limit = float(order.price2 if order.triggered else order.price)
            if self._limit_crossed(order.side, limit, mark):
                return self._fill_checked(order, limit, self._maker)
            return False
        if ot in ("stop-loss", "take-profit"):
            if self._trigger_crossed(order, mark):
                return self._fill_checked(order, self._slipped(mark, order.side), self._taker)
            return False
        if ot in ("stop-loss-limit", "take-profit-limit"):
            if self._trigger_crossed(order, mark):
                order.triggered = True  # now a resting limit at price2
            return False
        if ot in ("trailing-stop", "trailing-stop-limit"):
            offset = float(order.price or 0.0)
            best = order.best_mark if order.best_mark is not None else mark
            # Ratchet: sells trail a rising market, buys trail a falling one.
            order.best_mark = max(best, mark) if order.side == "sell" else min(best, mark)
            trigger = order.best_mark - offset if order.side == "sell" else order.best_mark + offset
            hit = mark <= trigger if order.side == "sell" else mark >= trigger
            if hit:
                if ot == "trailing-stop-limit":
                    order.triggered = True
                    order.price2 = order.price2 or self._slipped(mark, order.side)
                    return False
                return self._fill_checked(order, self._slipped(mark, order.side), self._taker)
            return False
        return False

    # ---------- cancel ----------

    def cancel(self, txid: str) -> PaperOrder:
        order = self.orders.get(txid)
        if order is None or not order.is_open:
            raise OrderRejected(f"EOrder:Unknown or closed order (paper): {txid}")
        order.status = "cancelled"
        self._save()
        return order

    def cancel_all(self) -> int:
        n = 0
        for order in self.orders.values():
            if order.is_open:
                order.status = "cancelled"
                n += 1
        if n:
            self._save()
        return n

    def open_orders(self) -> list[PaperOrder]:
        return [o for o in self.orders.values() if o.is_open]

    # ---------- internals ----------

    def _slipped(self, mark: float, side: str) -> float:
        return mark * (1 + self._slippage) if side == "buy" else mark * (1 - self._slippage)

    @staticmethod
    def _limit_marketable(order: PaperOrder, mark: float) -> bool:
        price = float(order.price or 0.0)
        return (order.side == "buy" and price >= mark) or (order.side == "sell" and price <= mark)

    @staticmethod
    def _limit_crossed(side: str, limit: float, mark: float) -> bool:
        return (side == "buy" and mark <= limit) or (side == "sell" and mark >= limit)

    @staticmethod
    def _trigger_crossed(order: PaperOrder, mark: float) -> bool:
        trigger = float(order.price or 0.0)
        if order.ordertype.startswith("stop-loss"):
            # Protective stop: sell-stop below market triggers on a fall,
            # buy-stop above market triggers on a rise.
            return mark <= trigger if order.side == "sell" else mark >= trigger
        # take-profit: sell above market on a rise, buy below market on a fall.
        return mark >= trigger if order.side == "sell" else mark <= trigger

    def _can_afford(self, order: PaperOrder, unit_price: float) -> bool:
        base, quote = _split(order.pair)
        if order.side == "buy":
            need = order.volume * unit_price * (1 + self._taker)
            return self.balances.get(quote, 0.0) >= need
        return self.balances.get(base, 0.0) >= order.volume

    def _fill_checked(self, order: PaperOrder, price: float, fee_rate: float) -> bool:
        if not self._can_afford(order, price):
            order.status = "cancelled"
            order.error = "EOrder:Insufficient funds at fill time (paper)"
            return False
        self._fill(order, price, fee_rate)
        return True

    def _fill(self, order: PaperOrder, price: float, fee_rate: float) -> None:
        base, quote = _split(order.pair)
        cost = order.volume * price
        fee = cost * fee_rate
        if order.side == "buy":
            self.balances[quote] = self.balances.get(quote, 0.0) - cost - fee
            self.balances[base] = self.balances.get(base, 0.0) + order.volume
        else:
            self.balances[base] = self.balances.get(base, 0.0) - order.volume
            self.balances[quote] = self.balances.get(quote, 0.0) + cost - fee
        order.status = "filled"
        order.filled_price = price
        order.filled_time = time.time()
        order.fee = fee
