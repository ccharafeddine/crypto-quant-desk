"""Client-selection factory: REST (primary), CLI, or demo.

Source resolution (CQD_DATA_SOURCE, seeded from Settings by the app bootstrap):
  "demo"       -> DemoClient (synthetic book, real market data)
  "rest"       -> KrakenRESTClient (official REST API; primary backend)
  "cli"        -> KrakenClient (kraken binary; needs it on PATH/WSL)
  "live"       -> alias for "rest" (legacy value from the contest era)
  unset/"auto" -> rest when Kraken keys are configured (vault or env),
                  demo otherwise - a fresh launch always shows a populated
                  cockpit instead of auth errors.

DemoClient's market data rides the REST client (public endpoints, keyless), so
demo mode works on Windows where no kraken binary exists. The panels just call
make_client() and stay backend-agnostic.
"""

from __future__ import annotations

import os

from cqd.data import credentials
from cqd.data.demo import DemoClient
from cqd.data.exchange import KrakenClient
from cqd.data.rest import KrakenRESTClient

_ENV_FLAG = "CQD_DATA_SOURCE"


def resolve_source() -> str:
    """Resolve the effective data source: "demo", "rest", or "cli"."""
    src = os.environ.get(_ENV_FLAG, "").strip().lower()
    if src == "demo":
        return "demo"
    if src in ("rest", "live"):
        return "rest"
    if src == "cli":
        return "cli"
    return "rest" if credentials.kraken_keys_present() else "demo"


def resolve_demo() -> bool:
    """True when the app should present demo data. Safe to call from the UI."""
    return resolve_source() == "demo"


def make_client(
    *,
    demo: bool | None = None,
    api_key: str | None = None,
    api_secret: str | None = None,
):
    """Return the client for the resolved source.

    `demo=True`/`False` forces demo vs live; `demo=None` resolves via
    `resolve_source()`. All returned clients expose the same async surface.
    """
    source = resolve_source()
    if demo is True or (demo is None and source == "demo"):
        return DemoClient(market_client=KrakenRESTClient())
    if source == "cli":
        return KrakenClient(api_key=api_key, api_secret=api_secret)
    return KrakenRESTClient(api_key=api_key, api_secret=api_secret)
