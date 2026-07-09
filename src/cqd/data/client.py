"""Client-selection factory: live KrakenClient vs DemoClient.

Mode resolution (so the app always shows something on first run):
  CQD_DATA_SOURCE = "demo"   -> demo
  CQD_DATA_SOURCE = "live"   -> live (even with no keys, so the resulting auth
                                      error guides the user to add credentials)
  unset / "" / "auto"        -> auto: demo when no Kraken keys are configured,
                                      live when keys are present.

Auto-demo means a fresh launch - including the double-clicked .app, whose working
directory has no .env - shows the populated sample cockpit instead of auth
errors. The env read is isolated here so a future Settings toggle can drive the
mode by writing CQD_DATA_SOURCE; the panels just call make_client().
"""

from __future__ import annotations

import os

from cqd.data.demo import DemoClient
from cqd.data.exchange import KrakenClient

_ENV_FLAG = "CQD_DATA_SOURCE"


def _keys_present() -> bool:
    """True if both Kraken API credentials are set in the environment."""
    return bool(
        os.environ.get("KRAKEN_API_KEY") and os.environ.get("KRAKEN_API_SECRET")
    )


def resolve_demo() -> bool:
    """Resolve demo vs live from CQD_DATA_SOURCE and key presence.

    Explicit "demo"/"live" win; otherwise auto-demo when no keys are configured.
    Pure read of the environment, safe to call from the UI to label the mode.
    """
    src = os.environ.get(_ENV_FLAG, "").strip().lower()
    if src == "demo":
        return True
    if src == "live":
        return False
    return not _keys_present()


def make_client(
    *,
    demo: bool | None = None,
    api_key: str | None = None,
    api_secret: str | None = None,
):
    """Return a DemoClient or a live KrakenClient.

    `demo=True`/`False` forces the choice; `demo=None` resolves via
    `resolve_demo()`. Both clients expose the same async surface, so callers
    (compute_account_risk, the panels) are agnostic to which one they got.
    """
    if demo is None:
        demo = resolve_demo()
    if demo:
        return DemoClient()
    return KrakenClient(api_key=api_key, api_secret=api_secret)
