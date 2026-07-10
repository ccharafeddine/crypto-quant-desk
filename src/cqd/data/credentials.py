"""Credential storage: OS vault first, environment fallback.

This is the ONLY module allowed to touch key material. Keys live in the OS
credential vault via `keyring` (Windows Credential Manager, macOS Keychain, or
the platform keyring elsewhere) under service "cqd"; environment variables
(loaded from a gitignored .env by the app bootstrap) remain a dev-only fallback.
Key VALUES must never appear in logs, exceptions, audit entries, or LLM prompts -
functions here return them or None, nothing else.
"""

from __future__ import annotations

import os
import sys

import keyring
import keyring.errors

SERVICE = "cqd"
_KRAKEN_KEY = "kraken-api-key"
_KRAKEN_SECRET = "kraken-api-secret"
_ANTHROPIC_KEY = "anthropic-api-key"


def credential_store_name() -> str:
    """Human name of the OS credential store, for UI/text (not key material)."""
    if sys.platform == "win32":
        return "Windows Credential Manager"
    if sys.platform == "darwin":
        return "macOS Keychain"
    return "your system keyring"


def _vault_get(entry: str) -> str | None:
    """Read one vault entry; a broken/locked vault degrades to None, never raises."""
    try:
        return keyring.get_password(SERVICE, entry)
    except keyring.errors.KeyringError:
        return None


def _vault_set(entry: str, value: str) -> None:
    keyring.set_password(SERVICE, entry, value)


def _vault_delete(entry: str) -> None:
    try:
        keyring.delete_password(SERVICE, entry)
    except keyring.errors.PasswordDeleteError:
        pass  # already absent


# ---------- Kraken API key pair ----------


def get_kraken_keys() -> tuple[str, str] | None:
    """(api_key, api_secret) from the vault, else the environment, else None.

    Both halves must come from the SAME source; a vault key with an env secret
    would silently mix two accounts.
    """
    key = _vault_get(_KRAKEN_KEY)
    secret = _vault_get(_KRAKEN_SECRET)
    if key and secret:
        return key, secret
    env_key = os.environ.get("KRAKEN_API_KEY", "")
    env_secret = os.environ.get("KRAKEN_API_SECRET", "")
    if env_key and env_secret:
        return env_key, env_secret
    return None


def set_kraken_keys(api_key: str, api_secret: str) -> None:
    """Store the pair in the vault. Caller verifies them against Kraken FIRST."""
    _vault_set(_KRAKEN_KEY, api_key)
    _vault_set(_KRAKEN_SECRET, api_secret)


def delete_kraken_keys() -> None:
    _vault_delete(_KRAKEN_KEY)
    _vault_delete(_KRAKEN_SECRET)


def kraken_keys_present() -> bool:
    return get_kraken_keys() is not None


# ---------- Anthropic key (analyst panel) ----------


def get_anthropic_key() -> str | None:
    return _vault_get(_ANTHROPIC_KEY) or os.environ.get("ANTHROPIC_API_KEY") or None


def set_anthropic_key(api_key: str) -> None:
    _vault_set(_ANTHROPIC_KEY, api_key)


def delete_anthropic_key() -> None:
    _vault_delete(_ANTHROPIC_KEY)
