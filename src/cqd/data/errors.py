"""Shared Kraken error taxonomy.

One hierarchy for every backend (REST, CLI, and later WebSocket), so panels and
services catch by MEANING (auth vs rate limit vs rejected order) and never care
which transport produced the failure. Raw Kraken error strings ("EAPI:Invalid
key") are preserved as the exception message for the audit log; no exception
ever carries key material.
"""

from __future__ import annotations


class KrakenError(Exception):
    """Base for every Kraken-facing failure. Panels catch this as the fallback."""


class KrakenAuthError(KrakenError):
    """Credentials missing, invalid, or rejected (EAPI:Invalid key/signature/nonce)."""


class KrakenPermissionError(KrakenAuthError):
    """Key pair is valid but lacks the required permission (EGeneral:Permission denied)."""


class KrakenTimeoutError(KrakenError):
    """The backend did not respond within its timeout."""


class KrakenNetworkError(KrakenError):
    """The request never reached Kraken (DNS, connection, TLS failure)."""


class KrakenProtocolError(KrakenError):
    """The backend responded, but not with the JSON shape the contract requires."""


class KrakenRateLimitError(KrakenError):
    """Kraken's rate limiter rejected the call; retry after backoff."""


class KrakenAPIError(KrakenError):
    """Kraken reported an error this taxonomy has no more specific class for."""


class OrderRejected(KrakenAPIError):
    """Kraken rejected an order mutation (EOrder:*). Never retried automatically."""


#: Exact-match and prefix rules, checked in order. First hit wins.
_EXACT: dict[str, type[KrakenError]] = {
    "EAPI:Invalid key": KrakenAuthError,
    "EAPI:Invalid signature": KrakenAuthError,
    "EAPI:Invalid nonce": KrakenAuthError,
    "EGeneral:Permission denied": KrakenPermissionError,
    "EAPI:Rate limit exceeded": KrakenRateLimitError,
    "EOrder:Rate limit exceeded": KrakenRateLimitError,
    "EGeneral:Too many requests": KrakenRateLimitError,
    "EService:Unavailable": KrakenAPIError,
}
_PREFIX: list[tuple[str, type[KrakenError]]] = [
    ("EAPI:", KrakenAuthError),
    ("EOrder:", OrderRejected),
]


def error_from_api(messages: list[str] | str) -> KrakenError:
    """Map Kraken's error strings (the "error" envelope list) to the taxonomy.

    The full raw string list is kept as the message so nothing is lost between
    the API and the audit log.
    """
    if isinstance(messages, str):
        messages = [messages]
    raw = "; ".join(messages) or "unknown Kraken error"
    for msg in messages:
        if msg in _EXACT:
            return _EXACT[msg](raw)
    for msg in messages:
        for prefix, exc in _PREFIX:
            if msg.startswith(prefix):
                return exc(raw)
    return KrakenAPIError(raw)
