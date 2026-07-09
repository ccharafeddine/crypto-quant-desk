"""Tests for the Risk panel's pure AccountRisk -> view mapping (no QApplication)."""

import pandas as pd

from cqd.data.portfolio import AccountRisk
from cqd.engine.risk import PortfolioRisk
from cqd.ui.panels.risk import build_risk_view


def _stub_ar(*, unpriced=None, dust=None, beta_override=None) -> AccountRisk:
    weights = pd.Series({"BTC": 0.5, "ETH": 0.3, "USD": 0.2})
    beta = beta_override if beta_override is not None else pd.Series(
        {"BTC": 1.0, "ETH": 1.2, "USD": 0.0}
    )
    rc = pd.Series({"BTC": 60.0, "ETH": 40.0, "USD": 0.0})
    risk = PortfolioRisk(
        weights=weights,
        ann_vol=0.42,
        ewma_vol=0.31,
        hhi=0.38,
        effective_bets=2.63,
        top3_concentration=1.0,
        book_beta_btc=0.95,
        per_asset_beta=beta,
        risk_contribution=rc,
        tail={},
    )
    info = {
        "total_usd": 12345.67,
        "values_usd": {},
        "dust": dust or {},
        "unpriced": unpriced or [],
        "min_usd": 1.0,
    }
    return AccountRisk(risk=risk, weights=weights, total_usd=12345.67, info=info)


def test_metrics_labels_and_rounded_values() -> None:
    view = build_risk_view(_stub_ar(), is_demo=False)
    m = dict(view.metrics)
    assert m["Annualized vol"] == "42.0%"
    assert m["EWMA vol (λ=0.94)"] == "31.0%"
    assert m["BTC beta"] == "0.95"
    assert m["Concentration (HHI)"] == "0.380"
    assert m["Effective bets"] == "2.63"
    assert m["Top-3 concentration"] == "100.0%"
    assert view.total_usd_str == "$12,345.67"


def test_rows_sorted_by_weight_desc_with_fields() -> None:
    view = build_risk_view(_stub_ar(), is_demo=False)
    assert [r[0] for r in view.rows] == ["BTC", "ETH", "USD"]  # weight desc
    # (asset, weight, beta, risk-contrib)
    assert view.rows[0] == ("BTC", "50.0%", "1.00", "60.0%")
    assert view.rows[1] == ("ETH", "30.0%", "1.20", "40.0%")
    # Cash row: zero beta, zero risk contribution.
    assert view.rows[2] == ("USD", "20.0%", "0.00", "0.0%")


def test_demo_badge_flag() -> None:
    assert build_risk_view(_stub_ar(), is_demo=True).is_demo is True
    assert build_risk_view(_stub_ar(), is_demo=False).is_demo is False


def test_caveats_unpriced_and_dust() -> None:
    view = build_risk_view(
        _stub_ar(unpriced=["WEIRD"], dust={"SHIB": 0.5}), is_demo=False
    )
    joined = " | ".join(view.caveats)
    assert "Excluded (no price): WEIRD" in joined
    assert "Excluded (dust < $1): SHIB" in joined


def test_caveats_empty_shows_all_priced() -> None:
    view = build_risk_view(_stub_ar(), is_demo=False)
    assert view.caveats == ["All holdings priced."]


def test_nan_beta_renders_dash() -> None:
    beta = pd.Series({"BTC": 1.0, "ETH": float("nan"), "USD": 0.0})
    view = build_risk_view(_stub_ar(beta_override=beta), is_demo=False)
    eth_row = next(r for r in view.rows if r[0] == "ETH")
    assert eth_row[2] == "—"


def test_footnote_states_conventions() -> None:
    view = build_risk_view(_stub_ar(), is_demo=False)
    assert "365" in view.footnote
    assert "beta vs BTC" in view.footnote
    assert "0.94" in view.footnote
    # Cash convention: only USD-pegged assets are zero-vol; fiat floats.
    assert "USD-pegged" in view.footnote
    assert "non-USD fiat" in view.footnote


def test_nan_vol_renders_dash_not_nan_percent() -> None:
    # Regression (2026-07-09 audit): short-history portfolios rendered
    # "Annualized vol: nan%".
    ar = _stub_ar()
    ar.risk.ann_vol = float("nan")
    ar.risk.ewma_vol = float("nan")
    view = build_risk_view(ar, is_demo=False)
    m = dict(view.metrics)
    assert m["Annualized vol"] == "—"
    assert m["EWMA vol (λ=0.94)"] == "—"


def test_shortened_window_and_exclusions_footnoted() -> None:
    ar = _stub_ar()
    ar.risk.window_days = 10
    ar.risk.frame_days = 90
    ar.risk.excluded_assets = ("NEWCOIN",)
    ar.info["returns_dropped"] = ["DEADCOIN"]
    view = build_risk_view(ar, is_demo=False)
    joined = " | ".join(view.caveats)
    assert "10 of 90 days" in joined
    assert "DEADCOIN" in joined and "NEWCOIN" in joined
