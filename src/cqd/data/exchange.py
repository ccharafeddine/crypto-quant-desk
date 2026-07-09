"""Kraken CLI subprocess wrapper.

The app's only backend is the bundled `kraken` binary. This wrapper invokes it
per call with JSON output, then hands the raw JSON to the pure normalizer
(`cqd.data.normalize`) to produce engine-shaped data. No ccxt, no direct network.

Security: private-call API keys are injected into the SUBPROCESS ENVIRONMENT
only, never into argv (argv is world-readable via `ps`) and never logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
from typing import Any

from cqd.data.errors import (
    KrakenAuthError,
    KrakenError,
    KrakenProtocolError,
    KrakenTimeoutError,
)
from cqd.data.normalize import (
    normalize_balance,
    normalize_ohlc,
    normalize_ticker,
    normalize_trades,
)

__all__ = [
    "KrakenAuthError",
    "KrakenCLIError",
    "KrakenCLINotFound",
    "KrakenClient",
    "KrakenProtocolError",
    "KrakenTimeoutError",
]


class KrakenCLIError(KrakenError):
    """The kraken CLI returned a nonzero exit or an {"error": ...} body."""


class KrakenCLINotFound(KrakenCLIError):
    """The kraken binary could not be located."""


class KrakenClient:
    """Async wrapper that shells out to the bundled `kraken` CLI.

    Stateless per call (each method spawns its own subprocess), so the async
    context-manager hooks are no-ops kept only for call-site compatibility.
    """

    #: Hard per-call ceiling; a hung CLI must never wedge a panel forever.
    DEFAULT_TIMEOUT_S = 30.0

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._api_key = api_key or os.environ.get("KRAKEN_API_KEY", "")
        self._api_secret = api_secret or os.environ.get("KRAKEN_API_SECRET", "")
        self._timeout = timeout
        self._binary = self._resolve_binary()

    async def __aenter__(self) -> "KrakenClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # No session to tear down; per-call subprocesses own their lifetime.
        return None

    # ---------- binary + invocation ----------

    @staticmethod
    def _resolve_binary() -> str:
        """Locate the kraken binary: env override, bundled .app, then PATH."""
        override = os.environ.get("CQD_KRAKEN_BIN")
        if override:
            return override
        # PyInstaller one-folder/one-file bundles unpack data files under _MEIPASS.
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled = os.path.join(meipass, "kraken")
            if os.path.exists(bundled):
                return bundled
        found = shutil.which("kraken")
        if found:
            return found
        raise KrakenCLINotFound(
            "kraken binary not found. Set CQD_KRAKEN_BIN, bundle it in the .app, "
            "or install it on PATH."
        )

    async def _run(self, args: list[str], *, private: bool) -> Any:
        """Invoke the CLI with JSON output and return the parsed body.

        Keys are injected into the subprocess env for private calls only, never
        into argv and never logged.
        """
        argv = [self._binary, *args, "-o", "json"]

        env = os.environ.copy()
        if private and self._api_key and self._api_secret:
            env["KRAKEN_API_KEY"] = self._api_key
            env["KRAKEN_API_SECRET"] = self._api_secret

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            raise KrakenTimeoutError(
                f"kraken '{args[0]}' timed out after {self._timeout:.0f}s"
            ) from None

        body: Any = None
        decode_failed = False
        if stdout:
            try:
                body = json.loads(stdout)
            except json.JSONDecodeError:
                decode_failed = True

        # Surface CLI-reported errors (which arrive with returncode 1 and an
        # {"error": ...} JSON body) before trusting the payload.
        if isinstance(body, dict) and body.get("error"):
            kind = body.get("error")
            message = body.get("message", str(kind))
            if kind == "auth":
                raise KrakenAuthError(message)
            raise KrakenCLIError(message)

        if proc.returncode != 0:
            detail = stderr.decode("utf-8", "replace").strip() if stderr else ""
            raise KrakenCLIError(f"kraken exited {proc.returncode}: {detail or 'no stderr'}")

        # Exit 0 must still produce a JSON body; truncated or empty stdout is a
        # protocol failure, never data (None used to normalize to "no trades").
        if decode_failed or body is None:
            raise KrakenProtocolError(
                f"kraken '{args[0]}' exited 0 with invalid or empty JSON output"
            )

        return body

    # ---------- public API (engine-shaped via the normalizer) ----------

    async def get_balance(self) -> dict[str, float]:
        raw = await self._run(["balance"], private=True)
        return normalize_balance(raw)

    async def get_marks(self, pairs: list[str]) -> dict[str, float]:
        """Latest marks for `pairs` (friendly form, e.g. "BTCUSD").

        Returned dict is keyed by slash symbol ("BTC/USD") per the normalizer.

        Degrades per pair: the CLI fails the WHOLE ticker call when any one
        pair is unknown (delisted asset, folded sub-balance like ETH2), so on
        batch failure each pair is retried alone and bad ones are skipped.
        Callers detect a missing pair by its absence from the result; the
        weights layer reports those as "unpriced" rather than erroring.
        """
        if not pairs:
            return {}
        try:
            raw = await self._run(["ticker", *pairs], private=False)
            return normalize_ticker(raw)
        except KrakenCLINotFound:
            raise
        except KrakenError:
            if len(pairs) == 1:
                return {}
            out: dict[str, float] = {}
            for pair in pairs:
                try:
                    raw = await self._run(["ticker", pair], private=False)
                    out.update(normalize_ticker(raw))
                except KrakenError:
                    continue
            return out

    async def get_ohlc_closes(
        self, pair: str, *, interval: int = 1440, since: int | None = None
    ) -> list[tuple[int, float]]:
        args = ["ohlc", pair, "--interval", str(interval)]
        if since is not None:
            args += ["--since", str(since)]
        raw = await self._run(args, private=False)
        return normalize_ohlc(raw)

    async def get_trades(
        self, *, start: int | None = None, end: int | None = None
    ) -> list[dict[str, Any]]:
        args = ["trades-history"]
        if start is not None:
            args += ["--start", str(start)]
        if end is not None:
            args += ["--end", str(end)]
        raw = await self._run(args, private=True)
        return normalize_trades(raw)
