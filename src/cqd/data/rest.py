"""Kraken REST client: the primary backend on Windows (no CLI binary needed).

Same async surface as the CLI wrapper (`KrakenClient`), same normalizer, same
error taxonomy - panels and services cannot tell which backend they got. Talks
only to Kraken's official REST API (https://api.kraken.com); signing is
implemented in-house per https://docs.kraken.com/api/docs/guides/spot-rest-auth
and verified against Kraken's documented test vector.

Security: keys are held in memory only, sent only in the API-Key header over
HTTPS, and never appear in logs or exceptions.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

from cqd.data import credentials
from cqd.data.errors import (
    KrakenAPIError,
    KrakenAuthError,
    KrakenNetworkError,
    KrakenProtocolError,
    KrakenTimeoutError,
    error_from_api,
)
from cqd.data.normalize import (
    normalize_balance,
    normalize_depth,
    normalize_ledgers,
    normalize_ohlc,
    normalize_ticker,
    normalize_trades,
)
from cqd.data.paths import app_data_dir

_BASE_URL = "https://api.kraken.com"
_TIMEOUT_S = 15.0


def sign_request(api_secret_b64: str, uri_path: str, nonce: str, post_data: str) -> str:
    """Kraken API-Sign: b64(HMAC-SHA512(path + SHA256(nonce + postdata), b64dec(secret))).

    Note the quirk: `post_data` (the exact urlencoded body, which already
    contains the nonce field) gets the nonce string prepended AGAIN before
    hashing - that is Kraken's documented scheme, verified against the
    signature test vector in their docs.
    """
    sha = hashlib.sha256((nonce + post_data).encode()).digest()
    mac = hmac.new(
        base64.b64decode(api_secret_b64), uri_path.encode() + sha, hashlib.sha512
    )
    return base64.b64encode(mac.digest()).decode()


class NonceCounter:
    """Strictly increasing millisecond nonce with a persisted high-water mark.

    Kraken rejects any nonce <= the highest it has seen for a key, so a clock
    step backwards (or two calls in the same millisecond) must never reuse a
    value. The high-water mark is persisted best-effort; a lost file just means
    the next nonce starts from the wall clock, which only regresses if the
    clock itself did.
    """

    def __init__(self, state_file: Path | None = None) -> None:
        self._state_file = state_file
        self._last = 0
        if state_file is not None:
            try:
                self._last = int(state_file.read_text().strip() or 0)
            except (OSError, ValueError):
                self._last = 0

    def next(self) -> int:
        n = max(int(time.time() * 1000), self._last + 1)
        self._last = n
        if self._state_file is not None:
            try:
                self._state_file.write_text(str(n))
            except OSError:
                pass  # persistence is best-effort; monotonicity holds in-process
        return n


class _RateLimiter:
    """Kraken private-call counter as a token bucket (Starter tier defaults).

    Each private call adds its cost; the counter decays over time. When a call
    would exceed capacity we sleep until it fits, so the app throttles itself
    instead of burning the account's rate limit.
    """

    def __init__(self, capacity: float = 15.0, decay_per_s: float = 0.33) -> None:
        self._capacity = capacity
        self._decay = decay_per_s
        self._used = 0.0
        self._at = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, cost: float = 1.0) -> None:
        async with self._lock:
            now = time.monotonic()
            self._used = max(0.0, self._used - (now - self._at) * self._decay)
            self._at = now
            if self._used + cost > self._capacity:
                wait = (self._used + cost - self._capacity) / self._decay
                await asyncio.sleep(wait)
                self._used = max(0.0, self._used - wait * self._decay)
                self._at = time.monotonic()
            self._used += cost


class KrakenRESTClient:
    """Async client for Kraken's official REST API (spot).

    Public methods mirror the CLI wrapper's surface so `make_client` can hand
    either to the panels. Private calls require a key pair (from args or the
    credential store); public calls work keyless.
    """

    is_demo = False

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        timeout: float = _TIMEOUT_S,
        transport: httpx.AsyncBaseTransport | None = None,
        nonce: NonceCounter | None = None,
    ) -> None:
        if api_key is None and api_secret is None:
            pair = credentials.get_kraken_keys()
            if pair:
                api_key, api_secret = pair
        self._api_key = api_key or ""
        self._api_secret = api_secret or ""
        self._nonce = nonce or NonceCounter(app_data_dir() / "nonce")
        self._limiter = _RateLimiter()
        self._http = httpx.AsyncClient(
            base_url=_BASE_URL,
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": "crypto-quant-desk"},
        )

    async def __aenter__(self) -> "KrakenRESTClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._http.aclose()

    # ---------- transport ----------

    async def _request(
        self, method: str, path: str, *, params=None, data=None, headers=None
    ) -> Any:
        try:
            resp = await self._http.request(
                method, path, params=params, data=data, headers=headers
            )
        except httpx.TimeoutException:
            raise KrakenTimeoutError(f"Kraken REST '{path}' timed out") from None
        except httpx.TransportError as e:
            raise KrakenNetworkError(f"Kraken REST '{path}' unreachable: {e}") from None

        try:
            body = resp.json()
        except ValueError:
            raise KrakenProtocolError(
                f"Kraken REST '{path}' returned non-JSON (HTTP {resp.status_code})"
            ) from None

        # Kraken reports errors inside the envelope, sometimes with HTTP 200.
        errors = body.get("error") if isinstance(body, dict) else None
        if errors:
            raise error_from_api(errors)
        if not isinstance(body, dict) or "result" not in body:
            raise KrakenProtocolError(f"Kraken REST '{path}' had no result envelope")
        return body["result"]

    async def _public(self, endpoint: str, params: dict | None = None) -> Any:
        return await self._request("GET", f"/0/public/{endpoint}", params=params)

    async def _private(
        self, endpoint: str, data: dict | None = None, *, cost: float = 1.0
    ) -> Any:
        if not (self._api_key and self._api_secret):
            raise KrakenAuthError(
                "No Kraken API keys configured. Add them in File > Settings."
            )
        await self._limiter.acquire(cost)
        path = f"/0/private/{endpoint}"
        nonce = str(self._nonce.next())
        payload = {"nonce": nonce, **(data or {})}
        post_data = urllib.parse.urlencode(payload)
        headers = {
            "API-Key": self._api_key,
            "API-Sign": sign_request(self._api_secret, path, nonce, post_data),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        return await self._request("POST", path, data=payload, headers=headers)

    # ---------- public API (engine-shaped via the normalizer) ----------

    async def get_marks(self, pairs: list[str]) -> dict[str, float]:
        """Latest marks for `pairs` (friendly form, e.g. "BTCUSD").

        Returned dict is keyed by slash symbol ("BTC/USD"). Degrades per pair:
        one unknown pair fails the whole batch on Kraken's side, so on an
        API-REPORTED batch failure each pair is retried alone and bad ones are
        skipped (absent from the result, surfaced as "unpriced" by the weights
        layer). Transport/protocol failures propagate - a 502 is not an
        unknown pair.
        """
        if not pairs:
            return {}
        try:
            raw = await self._public("Ticker", {"pair": ",".join(pairs)})
            return normalize_ticker(raw)
        except KrakenAPIError:
            if len(pairs) == 1:
                return {}
            out: dict[str, float] = {}
            for pair in pairs:
                try:
                    raw = await self._public("Ticker", {"pair": pair})
                    out.update(normalize_ticker(raw))
                except KrakenAPIError:
                    continue
            return out

    async def get_ohlc_closes(
        self, pair: str, *, interval: int = 1440, since: int | None = None
    ) -> list[tuple[int, float]]:
        params: dict[str, Any] = {"pair": pair, "interval": interval}
        if since is not None:
            params["since"] = since
        raw = await self._public("OHLC", params)
        return normalize_ohlc(raw)

    async def get_depth(self, pair: str, *, count: int = 25) -> dict[str, list[tuple[float, float]]]:
        raw = await self._public("Depth", {"pair": pair, "count": count})
        return normalize_depth(raw)

    async def get_asset_pairs(self) -> dict[str, Any]:
        """Raw AssetPairs map (classic pair -> spec). Used for precision/min-size."""
        return await self._public("AssetPairs")

    async def get_server_time(self) -> int:
        raw = await self._public("Time")
        return int(raw["unixtime"])

    # ---------- private API ----------

    async def get_balance(self) -> dict[str, float]:
        raw = await self._private("Balance")
        return normalize_balance(raw)

    async def get_trades(
        self, *, start: int | None = None, end: int | None = None
    ) -> list[dict[str, Any]]:
        data: dict[str, Any] = {}
        if start is not None:
            data["start"] = start
        if end is not None:
            data["end"] = end
        # TradesHistory costs 2 on Kraken's private counter.
        raw = await self._private("TradesHistory", data, cost=2.0)
        return normalize_trades(raw)

    async def get_ledgers(
        self, *, start: int | None = None, end: int | None = None
    ) -> list[dict[str, Any]]:
        data: dict[str, Any] = {}
        if start is not None:
            data["start"] = start
        if end is not None:
            data["end"] = end
        # Ledgers costs 2 on Kraken's private counter.
        raw = await self._private("Ledgers", data, cost=2.0)
        return normalize_ledgers(raw)

    async def get_open_orders(self) -> dict[str, Any]:
        """Raw open-orders map (txid -> order). Order panel shaping is Phase 3."""
        raw = await self._private("OpenOrders")
        return raw.get("open", {}) if isinstance(raw, dict) else {}

    async def get_ws_token(self) -> str:
        raw = await self._private("GetWebSocketsToken")
        return str(raw["token"])
