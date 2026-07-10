"""Tests for the ported risk/metrics engine.

Shapes mirror the Portfolio Analyzer's TestRisk / TestConcentration, plus
crypto-specific checks (365 annualization, BTC beta recovery, orchestrator).
"""

import numpy as np
import pandas as pd
import pytest

from cqd.engine import metrics as M
from cqd.engine import risk as R


class TestMetrics:
    @pytest.fixture
    def rets(self):
        rng = np.random.default_rng(42)
        return pd.Series(rng.normal(0.001, 0.04, 500))

    def test_annualize_vol_uses_365(self, rets):
        # vol should scale by sqrt(365), not sqrt(252)
        v = M.annualize_vol(rets)
        expected = float(rets.std(ddof=1) * np.sqrt(365))
        assert v == pytest.approx(expected, rel=1e-9)

    def test_ewma_vol_positive(self, rets):
        assert M.ewma_vol(rets) > 0

    def test_ewma_reacts_to_recent_spike(self):
        calm = [0.001] * 100
        spike = [0.20] * 10  # recent high-vol regime
        s = pd.Series(calm + spike)
        # EWMA should read higher than simple because recent obs dominate
        assert M.ewma_vol(s) > M.annualize_vol(s)

    def test_max_drawdown(self):
        values = pd.Series([100, 110, 90, 95, 80, 100])
        assert M.max_drawdown(values) == pytest.approx(-0.2727, abs=0.01)

    def test_var_cvar_ordering(self, rets):
        var95, cvar95 = M.var_cvar(rets, 0.95)
        assert var95 < 0
        assert cvar95 <= var95

    def test_sortino_ge_sharpe_for_positive_skew(self):
        # Many small gains, few large losses -> downside dev < total dev, so
        # Sortino should exceed Sharpe.
        s = pd.Series([0.01] * 40 + [-0.05] * 4)
        assert M.sortino_ratio(s) > M.sharpe_ratio(s)

    def test_sortino_all_positive_is_nan(self):
        # No downside -> undefined (not +inf).
        assert np.isnan(M.sortino_ratio(pd.Series([0.01, 0.02, 0.03])))

    def test_calmar_is_ann_return_over_maxdd(self):
        r = pd.Series([0.02, -0.10, 0.05, 0.03, -0.02])
        equity = (1.0 + r).cumprod()
        expected = M.annualize_return(r) / abs(M.max_drawdown(equity))
        assert M.calmar_ratio(r) == pytest.approx(expected, rel=1e-9)

    def test_rolling_vol_and_sharpe_are_series_with_leading_nans(self, rets):
        rv = M.rolling_vol(rets, window=30)
        rs = M.rolling_sharpe(rets, window=30)
        assert isinstance(rv, pd.Series) and isinstance(rs, pd.Series)
        assert rv.iloc[:29].isna().all()  # window not yet full
        assert rv.iloc[29:].notna().all()

    def test_ratio_summary_has_all_keys_and_matches_components(self, rets):
        summ = M.ratio_summary(rets)
        assert set(summ) == {
            "ann_return",
            "ann_vol",
            "ewma_vol",
            "sharpe",
            "sortino",
            "calmar",
            "max_drawdown",
            "var_95",
            "cvar_95",
            "gain_to_pain",
        }
        assert summ["sharpe"] == pytest.approx(M.sharpe_ratio(rets), rel=1e-9)
        assert summ["ann_vol"] == pytest.approx(M.annualize_vol(rets), rel=1e-9)


class TestConcentration:
    def test_hhi_equal_weights(self):
        w = np.array([0.25, 0.25, 0.25, 0.25])
        assert R.herfindahl_index(w) == pytest.approx(0.25, abs=1e-6)

    def test_hhi_single_asset(self):
        assert R.herfindahl_index(np.array([1.0, 0.0, 0.0])) == pytest.approx(1.0)

    def test_effective_bets_equal(self):
        w = np.array([0.2, 0.2, 0.2, 0.2, 0.2])
        assert R.effective_n_bets(w) == pytest.approx(5.0, abs=1e-6)

    def test_top3_concentration(self):
        w = np.array([0.5, 0.3, 0.1, 0.05, 0.05])
        assert R.concentration_ratio(w, 3) == pytest.approx(0.9, abs=1e-6)


class TestRiskContribution:
    def test_sums_to_100(self):
        w = np.array([0.4, 0.3, 0.3])
        cov = np.array(
            [
                [0.04, 0.01, 0.005],
                [0.01, 0.06, 0.01],
                [0.005, 0.01, 0.03],
            ]
        )
        rc = R.risk_contribution_pct(w, cov)
        assert rc.sum() == pytest.approx(100.0, abs=0.1)


class TestBtcBeta:
    def test_beta_recovers_known_value(self):
        rng = np.random.default_rng(7)
        btc = pd.Series(rng.normal(0.0, 0.03, 400))
        beta_true = 1.5
        asset = beta_true * btc + pd.Series(rng.normal(0, 0.005, 400))
        assert R.beta_to_btc(asset, btc) == pytest.approx(beta_true, abs=0.1)

    def test_book_beta_weighted(self):
        w = pd.Series({"A": 0.5, "B": 0.5})
        betas = pd.Series({"A": 2.0, "B": 0.0})
        assert R.portfolio_beta(w, betas) == pytest.approx(1.0, abs=1e-9)


class TestTailMetrics:
    def test_returns_expected_keys(self):
        rng = np.random.default_rng(42)
        r = pd.Series(rng.normal(0.001, 0.04, 500))
        tm = R.tail_metrics(r)
        for k in ["VaR_95", "CVaR_95", "Skewness", "Sortino", "Calmar", "Max_Drawdown"]:
            assert k in tm


class TestOrchestrator:
    def test_compute_portfolio_risk_end_to_end(self):
        rng = np.random.default_rng(1)
        dates = pd.date_range("2025-01-01", periods=300, freq="D")
        btc = rng.normal(0.0, 0.03, 300)
        df = pd.DataFrame(
            {
                "BTC": btc,
                "ADA": 1.3 * btc + rng.normal(0, 0.02, 300),
                "PEPE": 1.6 * btc + rng.normal(0, 0.03, 300),
            },
            index=dates,
        )
        weights = pd.Series({"ADA": 0.6, "PEPE": 0.4})

        pr = R.compute_portfolio_risk(weights, df, btc_col="BTC")

        assert pr.ann_vol > 0
        assert pr.ewma_vol > 0
        assert pr.effective_bets <= 2.0  # only two positions
        assert pr.risk_contribution.sum() == pytest.approx(100.0, abs=0.5)
        # Both alts are high-beta to BTC by construction â†’ book beta > 1
        assert pr.book_beta_btc > 1.0
        # Full history for every asset: window == frame, nothing excluded.
        assert pr.window_days == pr.frame_days == 300
        assert pr.excluded_assets == ()

    def test_common_history_window_prevents_dilution(self):
        # Regression (2026-07-09 audit): an asset listed mid-window contributed
        # 0% (NaN skipped by the row sum) at full weight on days it did not
        # exist, diluting vol/beta/tail. The computation must restrict itself to
        # the window where every included asset has data.
        rng = np.random.default_rng(7)
        dates = pd.date_range("2025-01-01", periods=60, freq="D")
        btc = pd.Series(rng.normal(0.0, 0.01, 60), index=dates)
        alt = pd.Series(np.nan, index=dates)
        alt.iloc[-10:] = rng.normal(0.0, 0.10, 10)  # 10x vol, 10 days of history
        df = pd.DataFrame({"BTC": btc, "ALT": alt})
        weights = pd.Series({"BTC": 0.5, "ALT": 0.5})

        pr = R.compute_portfolio_risk(weights, df, btc_col="BTC")

        assert pr.frame_days == 60
        assert pr.window_days == 10
        # Vol must match a manual computation over the common window only.
        window = df.iloc[-10:]
        port = (window * 0.5).sum(axis=1)
        import cqd.engine.metrics as M

        assert pr.ann_vol == pytest.approx(M.annualize_vol(port))
        # And must be far above the diluted union-window figure.
        diluted = (df * 0.5).sum(axis=1)  # NaN -> skipped -> 0% at full weight
        assert pr.ann_vol > M.annualize_vol(diluted) * 1.5

    def test_asset_with_no_data_is_excluded_and_reported(self):
        rng = np.random.default_rng(3)
        dates = pd.date_range("2025-01-01", periods=30, freq="D")
        df = pd.DataFrame(
            {
                "BTC": rng.normal(0.0, 0.02, 30),
                "DEAD": np.full(30, np.nan),
            },
            index=dates,
        )
        weights = pd.Series({"BTC": 0.7, "DEAD": 0.3})

        pr = R.compute_portfolio_risk(weights, df, btc_col="BTC")

        assert pr.excluded_assets == ("DEAD",)
        # Weights renormalized over the assets that actually have data.
        assert pr.weights.sum() == pytest.approx(1.0)
        assert list(pr.weights.index) == ["BTC"]
        assert pr.ann_vol > 0
