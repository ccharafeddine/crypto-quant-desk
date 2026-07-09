"""Tests for the daily-returns feed (pure helper + mocked-client orchestration)."""

import asyncio

import pandas as pd
import pytest

from cqd.data.returns import build_returns_frame, closes_to_returns

# Unix-second day anchors (1970-01-02 .. 01-06).
D1, D2, D3, D4, D5 = 86400, 172800, 259200, 345600, 432000


# ---------- pure helper: closes_to_returns ----------


def test_simple_return_values() -> None:
    closes = {"BTC": [(D1, 100.0), (D2, 110.0), (D3, 99.0)]}
    r = closes_to_returns(closes)
    # pct_change: [NaN, 0.10, -0.10]; leading NaN row dropped -> 2 rows.
    assert len(r) == 2
    assert r["BTC"].iloc[0] == pytest.approx(0.10)
    assert r["BTC"].iloc[1] == pytest.approx(-0.10)
    # First (all-NaN) row dropped: index starts at the SECOND date.
    assert r.index[0] == pd.Timestamp("1970-01-03")


def test_outer_join_ffill_no_spurious_returns() -> None:
    # ETH is missing day 2; ffill should carry its day-1 close, yielding a 0.0
    # return on day 2 rather than a spurious jump.
    closes = {
        "BTC": [(D1, 100.0), (D2, 110.0), (D3, 121.0)],
        "ETH": [(D1, 10.0), (D3, 12.0)],
    }
    r = closes_to_returns(closes)
    assert list(r.columns) == ["BTC", "ETH"]
    # Day 2 (index 0 after drop): ETH ffilled 10->10 = 0.0, not a huge move.
    assert r["ETH"].iloc[0] == pytest.approx(0.0)
    # Day 3: ETH 10->12 = +0.20.
    assert r["ETH"].iloc[1] == pytest.approx(0.20)
    assert r["BTC"].iloc[0] == pytest.approx(0.10)


def test_last_days_trimming() -> None:
    closes = {
        "BTC": [(D1, 100.0), (D2, 101.0), (D3, 102.0), (D4, 103.0), (D5, 104.0)]
    }
    r = closes_to_returns(closes, days=3)
    # Trim prices to last 3 rows (D3,D4,D5) -> 2 return rows (D4,D5).
    assert len(r) == 2
    assert list(r.index) == [pd.Timestamp("1970-01-05"), pd.Timestamp("1970-01-06")]
    assert r["BTC"].iloc[0] == pytest.approx(103.0 / 102.0 - 1.0)


def test_first_nan_row_dropped_and_bare_columns() -> None:
    closes = {"SOL": [(D1, 5.0), (D2, 6.0)]}
    r = closes_to_returns(closes)
    assert list(r.columns) == ["SOL"]
    assert len(r) == 1  # 2 prices -> 1 return, NaN head gone
    assert not r.isna().any().any()


def test_empty_points_column_present_all_nan() -> None:
    closes = {"BTC": [(D1, 100.0), (D2, 110.0)], "DEAD": []}
    r = closes_to_returns(closes)
    assert "DEAD" in r.columns
    assert r["DEAD"].isna().all()


# ---------- orchestration: build_returns_frame (mocked client) ----------


class _StubClient:
    """Stub KrakenClient: get_ohlc_closes returns canned closes by bare symbol."""

    def __init__(self, data: dict[str, list[tuple[int, float]]]) -> None:
        self._data = data
        self.requested: list[tuple[str, int]] = []

    async def get_ohlc_closes(self, pair, *, interval=1440, since=None):
        self.requested.append((pair, interval))
        bare = pair[:-3]  # strip the "USD" quote
        return self._data[bare]


def test_btc_benchmark_auto_included_when_not_requested() -> None:
    data = {
        "ETH": [(D1, 10.0), (D2, 11.0), (D3, 12.0)],
        "SOL": [(D1, 5.0), (D2, 5.5), (D3, 6.0)],
        "BTC": [(D1, 100.0), (D2, 110.0), (D3, 121.0)],
    }
    client = _StubClient(data)
    r = asyncio.run(build_returns_frame(client, ["ETH", "SOL"]))
    # BTC present even though it was not in the requested assets.
    assert "BTC" in r.columns
    assert set(r.columns) == {"ETH", "SOL", "BTC"}


def test_friendly_pair_mapping_and_interval() -> None:
    data = {
        "ETH": [(D1, 10.0), (D2, 11.0)],
        "BTC": [(D1, 100.0), (D2, 110.0)],
    }
    client = _StubClient(data)
    asyncio.run(build_returns_frame(client, ["ETH"], interval=1440))
    pairs = {p for p, _ in client.requested}
    # bare + "USD"; BTC auto-added.
    assert pairs == {"ETHUSD", "BTCUSD"}
    assert all(iv == 1440 for _, iv in client.requested)


def test_output_is_datetimeindex_frame_ready_for_engine() -> None:
    data = {
        "BTC": [(D1, 100.0), (D2, 110.0), (D3, 121.0)],
        "ETH": [(D1, 10.0), (D2, 11.0), (D3, 12.1)],
    }
    client = _StubClient(data)
    r = asyncio.run(build_returns_frame(client, ["BTC", "ETH"]))
    assert isinstance(r, pd.DataFrame)
    assert isinstance(r.index, pd.DatetimeIndex)
    assert list(r.columns) == ["BTC", "ETH"]
    # BTC not duplicated when already requested.
    pairs = [p for p, _ in client.requested]
    assert pairs.count("BTCUSD") == 1


# ---------- cash/quote handling (regression for the USDUSD crash) ----------


class _GuardedStubClient:
    """Stub that mimics the live CLI by RAISING on a cash pair like USDUSD."""

    def __init__(self, data: dict[str, list[tuple[int, float]]]) -> None:
        self._data = data
        self.requested: list[str] = []

    async def get_ohlc_closes(self, pair, *, interval=1440, since=None):
        self.requested.append(pair)
        bare = pair[:-3]
        if bare not in self._data:
            # The real CLI returns EQuery:Unknown asset pair for {quote}{quote}.
            raise AssertionError(f"cash pair must not be fetched: {pair}")
        return self._data[bare]


def test_cash_asset_not_fetched_and_zero_returns() -> None:
    data = {
        "BTC": [(D1, 100.0), (D2, 110.0), (D3, 121.0)],
        "ETH": [(D1, 10.0), (D2, 11.0), (D3, 12.1)],
    }
    client = _GuardedStubClient(data)
    r = asyncio.run(build_returns_frame(client, ["BTC", "ETH", "USD"]))
    # USD stays as a column and is all zeros (constant-1.0 price -> 0 returns).
    assert "USD" in r.columns
    assert (r["USD"] == 0.0).all()
    # The cash pair was never fetched.
    assert "USDUSD" not in client.requested
    assert {"BTCUSD", "ETHUSD"} <= set(client.requested)
    # Real assets unaffected; BTC present.
    assert r["BTC"].iloc[0] == pytest.approx(0.10)
    assert "BTC" in r.columns and "ETH" in r.columns


def test_stablecoin_treated_as_cash() -> None:
    data = {"BTC": [(D1, 100.0), (D2, 110.0), (D3, 121.0)]}
    client = _GuardedStubClient(data)
    r = asyncio.run(build_returns_frame(client, ["BTC", "USDC"]))
    assert "USDC" in r.columns
    assert (r["USDC"] == 0.0).all()
    assert "USDCUSD" not in client.requested


def test_quote_param_respected() -> None:
    # With quote="EUR", the friendly pair is bare+"EUR" and EUR is cash.
    data = {"BTC": [(D1, 100.0), (D2, 110.0), (D3, 121.0)]}
    client = _GuardedStubClient(data)
    r = asyncio.run(build_returns_frame(client, ["BTC", "EUR"], quote="EUR"))
    assert client.requested == ["BTCEUR"]  # BTC fetched in EUR, EUR not fetched
    assert (r["EUR"] == 0.0).all()
