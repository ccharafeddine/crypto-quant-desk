"""Tests for credential storage (fake in-memory keyring, no OS vault)."""

import os
from unittest.mock import patch

import keyring
import keyring.backend
import pytest

from cqd.data import credentials


class _FakeKeyring(keyring.backend.KeyringBackend):
    """In-memory backend so tests never touch the real Credential Manager."""

    priority = 1

    def __init__(self):
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.store:
            raise keyring.errors.PasswordDeleteError("not found")
        del self.store[(service, username)]


class _BrokenKeyring(keyring.backend.KeyringBackend):
    """Backend whose reads fail, simulating a locked/broken vault."""

    priority = 1

    def get_password(self, service, username):
        raise keyring.errors.KeyringError("vault unavailable")

    def set_password(self, service, username, password):
        raise keyring.errors.KeyringError("vault unavailable")

    def delete_password(self, service, username):
        raise keyring.errors.PasswordDeleteError("vault unavailable")


@pytest.fixture()
def fake_vault():
    backend = _FakeKeyring()
    old = keyring.get_keyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(old)


def test_roundtrip_kraken_keys(fake_vault) -> None:
    with patch.dict(os.environ, {}, clear=True):
        assert credentials.get_kraken_keys() is None
        assert credentials.kraken_keys_present() is False

        credentials.set_kraken_keys("KEY123", "SECRET456")
        assert credentials.get_kraken_keys() == ("KEY123", "SECRET456")
        assert credentials.kraken_keys_present() is True
        # Stored under the expected service/entry names.
        assert fake_vault.store[("cqd", "kraken-api-key")] == "KEY123"

        credentials.delete_kraken_keys()
        assert credentials.get_kraken_keys() is None
        # Deleting again is a no-op, not an error.
        credentials.delete_kraken_keys()


def test_env_fallback_when_vault_empty(fake_vault) -> None:
    env = {"KRAKEN_API_KEY": "ENVKEY", "KRAKEN_API_SECRET": "ENVSECRET"}
    with patch.dict(os.environ, env, clear=True):
        assert credentials.get_kraken_keys() == ("ENVKEY", "ENVSECRET")


def test_vault_wins_over_env(fake_vault) -> None:
    env = {"KRAKEN_API_KEY": "ENVKEY", "KRAKEN_API_SECRET": "ENVSECRET"}
    with patch.dict(os.environ, env, clear=True):
        credentials.set_kraken_keys("VAULTKEY", "VAULTSECRET")
        assert credentials.get_kraken_keys() == ("VAULTKEY", "VAULTSECRET")


def test_no_mixed_sources(fake_vault) -> None:
    # Vault key present but secret missing: must NOT pair with the env secret.
    env = {"KRAKEN_API_SECRET": "ENVSECRET"}
    with patch.dict(os.environ, env, clear=True):
        fake_vault.store[("cqd", "kraken-api-key")] = "VAULTKEY"
        assert credentials.get_kraken_keys() is None


def test_broken_vault_degrades_to_env() -> None:
    old = keyring.get_keyring()
    keyring.set_keyring(_BrokenKeyring())
    try:
        env = {"KRAKEN_API_KEY": "ENVKEY", "KRAKEN_API_SECRET": "ENVSECRET"}
        with patch.dict(os.environ, env, clear=True):
            assert credentials.get_kraken_keys() == ("ENVKEY", "ENVSECRET")
    finally:
        keyring.set_keyring(old)


def test_anthropic_key_roundtrip(fake_vault) -> None:
    with patch.dict(os.environ, {}, clear=True):
        assert credentials.get_anthropic_key() is None
        credentials.set_anthropic_key("sk-test")
        assert credentials.get_anthropic_key() == "sk-test"
        credentials.delete_anthropic_key()
        assert credentials.get_anthropic_key() is None
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-env"}, clear=True):
        assert credentials.get_anthropic_key() == "sk-env"
