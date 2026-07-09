"""App-wide trading services (one OrderService, one PaperBroker per process).

Panels never construct trading objects themselves - they call these accessors,
so there is exactly one paper overlay, one audit log, and one confirmation
gate. Mode resolution lives here too: LIVE requires BOTH the paper-mode switch
off AND a real (non-demo) data source; anything else routes to paper.
"""

from __future__ import annotations

from cqd.data.client import resolve_demo
from cqd.data.errors import KrakenError
from cqd.data.paths import app_data_dir
from cqd.data.rest import KrakenRESTClient
from cqd.trading.audit import AuditLog
from cqd.trading.limits import PairSpec
from cqd.trading.orders import OrderService
from cqd.trading.paper import PaperBroker
from cqd.ui import settings_store as store

_paper: PaperBroker | None = None
_service: OrderService | None = None
_specs: dict[str, PairSpec] | None = None


def trading_mode() -> str:
    """ "live" only when paper mode is off AND the account is real."""
    if store.get_paper_mode() or resolve_demo():
        return "paper"
    return "live"


def paper_broker() -> PaperBroker:
    global _paper
    if _paper is None:
        _paper = PaperBroker(app_data_dir() / "paper_state.json")
    return _paper


def order_service() -> OrderService:
    global _service
    if _service is None:
        _service = OrderService(
            paper=paper_broker(),
            live_client_factory=KrakenRESTClient,
            audit=AuditLog(),
            mode_provider=trading_mode,
            max_order_value_provider=store.get_max_order_usd,
        )
    return _service


async def pair_specs() -> dict[str, PairSpec]:
    """Friendly pair name ("XBTUSD") -> PairSpec, cached for the session.

    Fetched from the public AssetPairs endpoint (keyless), so the ticket works
    in demo mode too. Raises KrakenError on network failure; callers surface it.
    """
    global _specs
    if _specs is None:
        async with KrakenRESTClient(api_key="", api_secret="") as client:
            raw = await client.get_asset_pairs()
        specs: dict[str, PairSpec] = {}
        for classic, entry in raw.items():
            try:
                spec = PairSpec.from_asset_pairs_entry(classic, entry)
            except (TypeError, ValueError):
                continue
            specs[spec.pair] = spec
        if not specs:
            raise KrakenError("AssetPairs returned no usable pairs")
        _specs = specs
    return _specs


def reset_for_tests() -> None:
    """Drop singletons so tests get fresh state."""
    global _paper, _service, _specs
    _paper = None
    _service = None
    _specs = None
