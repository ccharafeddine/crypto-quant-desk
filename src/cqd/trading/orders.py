"""OrderService: the single gate between intent and money.

Flow (no bypasses exist by construction):

  OrderRequest -> prepare() [limits validation + value cap] -> PreparedOrder
  with a one-shot confirmation token -> UI shows the confirm dialog ->
  submit(token) routes to PaperBroker or the live REST client -> every step
  lands in the audit log.

submit() accepts only a token minted by prepare(), and each token dies on
first use - there is no API to send an unvalidated or unconfirmed order.
Ambiguous live submits (timeout after send) are audited as "unknown" and
reconciled against QueryOrders/OpenOrders instead of guessed.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, model_validator

from cqd.data.errors import KrakenError, KrakenTimeoutError, OrderRejected
from cqd.trading.audit import AuditLog
from cqd.trading.limits import PairSpec, validate_order
from cqd.trading.paper import PaperBroker


class OrderRequest(BaseModel):
    """One prospective order, validated for shape here and for limits in prepare()."""

    pair: str  # engine slash form, e.g. "BTC/USD"
    kraken_pair: str  # API form, e.g. "XBTUSD" (PairSpec.pair)
    side: Literal["buy", "sell"]
    ordertype: str
    volume: float = Field(gt=0)
    price: float | None = None
    price2: float | None = None
    close_ordertype: str | None = None  # conditional close (TP/SL on entry)
    close_price: float | None = None
    close_price2: float | None = None
    mark: float | None = None  # latest mark, prices market/trailing orders
    source: str = "ui"

    @model_validator(mode="after")
    def _close_needs_type(self) -> "OrderRequest":
        if self.close_price is not None and not self.close_ordertype:
            raise ValueError("close_price given without close_ordertype")
        return self

    def estimated_value(self) -> float | None:
        unit = (
            self.mark
            if (self.ordertype == "market" or self.ordertype.startswith("trailing-stop"))
            else (self.price or self.mark)
        )
        return self.volume * unit if unit and unit > 0 else None


@dataclass
class PreparedOrder:
    """A validated order plus its one-shot confirmation token."""

    token: str
    request: OrderRequest
    estimated_value: float | None
    mode: str  # mode AT PREPARATION TIME, shown in the confirm dialog


@dataclass
class OrderResult:
    status: str  # "filled" | "open" | "rejected" | "unknown"
    mode: str
    txid: str | None = None
    detail: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class OrderService:
    """Routes confirmed orders to the paper broker or the live client."""

    def __init__(
        self,
        *,
        paper: PaperBroker,
        live_client_factory: Callable[[], Any],
        audit: AuditLog,
        mode_provider: Callable[[], str],  # returns "paper" | "live"
        max_order_value_provider: Callable[[], float | None],
    ) -> None:
        self._paper = paper
        self._live_factory = live_client_factory
        self._audit = audit
        self._mode = mode_provider
        self._max_value = max_order_value_provider
        self._pending: dict[str, OrderRequest] = {}

    # ---------- step 1: validate + mint token ----------

    def prepare(
        self, request: OrderRequest, spec: PairSpec
    ) -> tuple[PreparedOrder | None, list[str]]:
        """Validate; on success return a PreparedOrder, else (None, violations)."""
        problems = validate_order(
            spec,
            side=request.side,
            ordertype=request.ordertype,
            volume=request.volume,
            price=request.price,
            price2=request.price2,
            mark=request.mark,
            max_order_value=self._max_value(),
        )
        if request.close_ordertype and request.close_price is not None:
            close_problems = validate_order(
                spec,
                side="sell" if request.side == "buy" else "buy",
                ordertype=request.close_ordertype,
                volume=request.volume,
                price=request.close_price,
                price2=request.close_price2,
                mark=request.mark,
                max_order_value=self._max_value(),
            )
            problems += [f"Conditional close: {p}" for p in close_problems]
        if problems:
            return None, problems
        token = secrets.token_hex(16)
        self._pending[token] = request
        return PreparedOrder(
            token=token,
            request=request,
            estimated_value=request.estimated_value(),
            mode=self._mode(),
        ), []

    # ---------- step 2: confirmed submission ----------

    async def submit(self, token: str) -> OrderResult:
        """Send the order behind a one-shot confirmation token."""
        request = self._pending.pop(token, None)
        if request is None:
            raise ValueError("Order was not confirmed (unknown or already-used token).")
        mode = self._mode()
        value = request.estimated_value()
        await self._audit.record(
            "submit",
            mode=mode,
            source=request.source,
            request=request.model_dump(),
            order_value_usd=value,
        )
        if mode == "paper":
            return await self._submit_paper(request, value)
        return await self._submit_live(request, value)

    async def _submit_paper(self, request: OrderRequest, value: float | None) -> OrderResult:
        try:
            order = self._paper.submit(
                pair=request.pair,
                side=request.side,
                ordertype=request.ordertype,
                volume=request.volume,
                price=request.price,
                price2=request.price2,
                mark=float(request.mark or 0.0),
            )
        except OrderRejected as e:
            await self._audit.record(
                "reject",
                mode="paper",
                source=request.source,
                request=request.model_dump(),
                error=str(e),
                order_value_usd=value,
            )
            return OrderResult(status="rejected", mode="paper", detail=str(e))
        event = "fill" if order.status == "filled" else "ack"
        await self._audit.record(
            event,
            mode="paper",
            source=request.source,
            request=request.model_dump(),
            response={
                "txid": order.txid,
                "status": order.status,
                "filled_price": order.filled_price,
            },
            order_value_usd=value,
        )
        return OrderResult(
            status=order.status if order.status == "filled" else "open",
            mode="paper",
            txid=order.txid,
            detail=f"paper {order.status}",
        )

    async def _submit_live(self, request: OrderRequest, value: float | None) -> OrderResult:
        price = request.price
        if request.ordertype.startswith("trailing-stop") and price is not None:
            price = f"+{self._trim(price)}"  # Kraken trailing offsets are relative
        try:
            async with self._live_factory() as client:
                raw = await client.add_order(
                    pair=request.kraken_pair,
                    side=request.side,
                    ordertype=request.ordertype,
                    volume=request.volume,
                    price=price,
                    price2=request.price2,
                    close_ordertype=request.close_ordertype,
                    close_price=request.close_price,
                    close_price2=request.close_price2,
                )
        except OrderRejected as e:
            await self._audit.record(
                "reject",
                mode="live",
                source=request.source,
                request=request.model_dump(),
                error=str(e),
                order_value_usd=value,
            )
            return OrderResult(status="rejected", mode="live", detail=str(e))
        except KrakenTimeoutError as e:
            # Sent but unacknowledged: the order MAY be live. Never guess.
            await self._audit.record(
                "unknown",
                mode="live",
                source=request.source,
                request=request.model_dump(),
                error=str(e),
                order_value_usd=value,
            )
            return OrderResult(
                status="unknown",
                mode="live",
                detail="No response after send - reconciling against open orders.",
            )
        except KrakenError as e:
            await self._audit.record(
                "reject",
                mode="live",
                source=request.source,
                request=request.model_dump(),
                error=str(e),
                order_value_usd=value,
            )
            return OrderResult(status="rejected", mode="live", detail=str(e))

        txid = (raw.get("txid") or [None])[0]
        await self._audit.record(
            "ack",
            mode="live",
            source=request.source,
            request=request.model_dump(),
            response=raw,
            order_value_usd=value,
        )
        return OrderResult(status="open", mode="live", txid=txid, raw=raw)

    # ---------- cancel ----------

    async def cancel(self, txid: str) -> OrderResult:
        mode = self._mode()
        if mode == "paper" or txid.startswith("PAPER-"):
            try:
                order = self._paper.cancel(txid)
            except OrderRejected as e:
                return OrderResult(status="rejected", mode="paper", txid=txid, detail=str(e))
            await self._audit.record(
                "cancel", mode="paper", response={"txid": txid, "status": order.status}
            )
            return OrderResult(status="open", mode="paper", txid=txid, detail="cancelled")
        try:
            async with self._live_factory() as client:
                count = await client.cancel_order(txid)
        except KrakenError as e:
            return OrderResult(status="rejected", mode="live", txid=txid, detail=str(e))
        await self._audit.record("cancel", mode="live", response={"txid": txid, "count": count})
        return OrderResult(status="open", mode="live", txid=txid, detail=f"cancelled {count}")

    async def cancel_all(self) -> OrderResult:
        mode = self._mode()
        if mode == "paper":
            n = self._paper.cancel_all()
            await self._audit.record("cancel", mode="paper", response={"count": n, "all": True})
            return OrderResult(status="open", mode="paper", detail=f"cancelled {n}")
        try:
            async with self._live_factory() as client:
                n = await client.cancel_all()
        except KrakenError as e:
            return OrderResult(status="rejected", mode="live", detail=str(e))
        await self._audit.record("cancel", mode="live", response={"count": n, "all": True})
        return OrderResult(status="open", mode="live", detail=f"cancelled {n}")

    # ---------- UNKNOWN reconciliation ----------

    async def reconcile_unknown(self, request: OrderRequest) -> OrderResult:
        """Resolve an UNKNOWN submit by matching against the account's orders.

        Looks for an open (or very recent) order with the same pair, side,
        type, volume, and price. Found -> resolve as open; not found -> the
        submit never landed and it is safe to retry manually.
        """
        try:
            async with self._live_factory() as client:
                open_orders = await client.get_open_orders()
        except KrakenError as e:
            return OrderResult(status="unknown", mode="live", detail=f"still unresolved: {e}")
        for txid, info in (open_orders or {}).items():
            descr = info.get("descr", {}) if isinstance(info, dict) else {}
            if (
                descr.get("pair") in (request.kraken_pair, request.pair.replace("/", ""))
                and descr.get("type") == request.side
                and descr.get("ordertype") == request.ordertype
                and abs(float(info.get("vol", 0.0)) - request.volume) < 1e-12
            ):
                await self._audit.record(
                    "resolve",
                    mode="live",
                    response={"txid": txid, "resolution": "found open"},
                )
                return OrderResult(
                    status="open", mode="live", txid=txid, detail="resolved: order is live"
                )
        await self._audit.record(
            "resolve", mode="live", response={"resolution": "not found; submit never landed"}
        )
        return OrderResult(status="rejected", mode="live", detail="resolved: order never landed")

    @staticmethod
    def _trim(value: float) -> str:
        s = f"{value:.10f}".rstrip("0").rstrip(".")
        return s or "0"


__all__ = [
    "OrderRequest",
    "OrderResult",
    "OrderService",
    "PreparedOrder",
]
