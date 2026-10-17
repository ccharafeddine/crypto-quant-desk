"""Crash replays, downside beta, depeg, block-bootstrap fan, liquidity."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from cqd.data.crash_history import load_crash_windows
from cqd.engine import stress as S


def _window(**moves: float) -> S.CrashWindow:
    return S.CrashWindow("w", "Test crash", "2022-01-01", "2022-01-02", "", moves)


def test_bundled_crash_windows_cover_the_four_named_events() -> None:
    windows = {w.key: w for w in load_crash_windows()}
    assert set(windows) == {"luna_2022", "ftx_2022", "yen_carry_2024", "oct10_2025"}
    for w in windows.values():
        assert -0.6 < w.moves["BTC"] < -0.1  # every window is a real BTC drawdown
        assert abs(w.moves["USDC"]) < 0.05


def test_replay_uses_real_moves_cash_is_flat_and_contributions_sum() -> None:
    weights = pd.Series({"BTC": 0.5, "ETH": 0.3, "USD": 0.2})
    rep = S.replay_crash(weights, _window(BTC=-0.30, ETH=-0.40))
    assert rep.portfolio_return == pytest.approx(0.5 * -0.30 + 0.3 * -0.40)
    assert rep.contributions["USD"] == 0.0 and rep.proxied == ()
    assert sum(rep.contributions.values()) == pytest.approx(rep.portfolio_return)


def test_replay_proxies_an_unlisted_asset_by_beta_and_flags_it() -> None:
    weights = pd.Series({"BTC": 0.5, "NEW": 0.5})
    rep = S.replay_crash(weights, _window(BTC=-0.20), betas=pd.Series({"NEW": 1.5}))
    assert rep.contributions["NEW"] == pytest.approx(0.5 * 1.5 * -0.20)
    assert rep.proxied == ("NEW",)


def test_depeg_shock_hits_only_stablecoins() -> None:
    out = S.depeg_shock(pd.Series({"BTC": 0.8, "USDC": 0.2}))
    assert out["portfolio"] == pytest.approx(0.2 * -0.13)
    assert "USDT" not in out


def test_downside_beta_sees_asymmetric_selloffs() -> None:
    rng = np.random.default_rng(0)
    btc = pd.Series(rng.normal(0, 0.03, 2000))
    alt = pd.Series(np.where(btc < 0, 1.8 * btc, 0.8 * btc) + rng.normal(0, 0.005, 2000))
    assert S.downside_beta(alt, btc) == pytest.approx(1.8, abs=0.1)
    assert alt.cov(btc) / btc.var() < S.downside_beta(alt, btc)


def test_rolling_beta_and_correlation_shapes() -> None:
    rng = np.random.default_rng(1)
    btc = rng.normal(0, 0.03, 300)
    df = pd.DataFrame({"BTC": btc, "ETH": 1.2 * btc + rng.normal(0, 0.01, 300)})
    rb, rc = S.rolling_beta(df, window=60), S.rolling_correlation(df, window=60)
    assert list(rb.columns) == ["ETH"] == list(rc.columns)
    assert rb["ETH"].iloc[-1] == pytest.approx(1.2, abs=0.15)
    assert 0.9 < rc["ETH"].iloc[-1] <= 1.0 and math.isnan(rc["ETH"].iloc[10])


def test_block_bootstrap_fan_is_zero_drift_ordered_and_seeded() -> None:
    r = pd.Series(np.random.default_rng(2).normal(0.01, 0.03, 500))  # strong drift
    fan = S.block_bootstrap_fan(r, horizon=30, n_paths=4000, block=5)
    assert fan.bands[0.5][0] == 1.0 and len(fan.bands[0.5]) == 31
    assert abs(fan.bands[0.5][-1] - 1.0) < 0.03  # demeaned: no 1%/day trend
    assert all(fan.bands[0.05][-1] < fan.bands[q][-1] for q in (0.25, 0.5, 0.75, 0.95))
    again = S.block_bootstrap_fan(r, horizon=30, n_paths=4000, block=5)
    assert again.bands == fan.bands and fan.var95_horizon < 0
    drift = S.block_bootstrap_fan(r, horizon=30, n_paths=4000, zero_drift=False)
    assert drift.bands[0.5][-1] > 1.2


def test_fan_with_too_little_history_is_flat() -> None:
    fan = S.block_bootstrap_fan(pd.Series([0.01]), horizon=5)
    assert fan.n_paths == 0 and fan.bands[0.95] == [1.0] * 6


def test_liquidity_days_to_exit_at_ten_percent_of_adv() -> None:
    rows = S.liquidity_table(
        {"BTC": 1_000_000.0, "TINY": 50_000.0, "USD": 9.0}, {"BTC": 5e8, "TINY": 1e5}
    )
    by = {r.asset: r for r in rows}
    assert "USD" not in by
    assert by["BTC"].days_to_liquidate == pytest.approx(1e6 / 5e7)
    assert by["TINY"].pct_of_adv == pytest.approx(0.5)
    assert by["TINY"].days_to_liquidate == pytest.approx(5.0)
    assert math.isnan(S.liquidity_table({"X": 1.0}, {})[0].days_to_liquidate)


def test_average_daily_volume_uses_the_last_n_bars() -> None:
    idx = pd.date_range("2026-01-01", periods=40)
    vol, close = pd.Series(1.0, idx), pd.Series(np.arange(40, dtype=float), idx)
    assert S.average_daily_volume_usd(vol, close, days=30) == pytest.approx(
        np.arange(10, 40).mean()
    )
