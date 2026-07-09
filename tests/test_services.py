"""Tests for the app-wide trading service accessors (no Qt event loop)."""

import asyncio
from unittest.mock import patch

from cqd.ui import services


class _FakeClient:
    def __init__(self, balances):
        self._balances = balances
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def get_balance(self):
        self.calls += 1
        return dict(self._balances)


def test_paper_broker_seeded_once_from_account(tmp_path) -> None:
    services.reset_for_tests()
    fake = _FakeClient({"USD": 5_000.0, "BTC": 0.1})
    try:
        with (
            patch("cqd.data.client.make_client", return_value=fake),
            patch("cqd.ui.services.app_data_dir", return_value=tmp_path),
        ):
            asyncio.run(services.ensure_paper_seeded())
            # Regression (3.8 gate): an unseeded overlay rejected every paper
            # order as unaffordable.
            assert services.paper_broker().balances == {"USD": 5_000.0, "BTC": 0.1}
            asyncio.run(services.ensure_paper_seeded())  # idempotent
            assert fake.calls == 1
    finally:
        services.reset_for_tests()


def test_trading_mode_requires_paper_off_and_real_account() -> None:
    with (
        patch("cqd.ui.services.store.get_paper_mode", return_value=True),
        patch("cqd.ui.services.resolve_demo", return_value=False),
    ):
        assert services.trading_mode() == "paper"
    with (
        patch("cqd.ui.services.store.get_paper_mode", return_value=False),
        patch("cqd.ui.services.resolve_demo", return_value=True),
    ):
        assert services.trading_mode() == "paper"  # demo can never go live
    with (
        patch("cqd.ui.services.store.get_paper_mode", return_value=False),
        patch("cqd.ui.services.resolve_demo", return_value=False),
    ):
        assert services.trading_mode() == "live"
