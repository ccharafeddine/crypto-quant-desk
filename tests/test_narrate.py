"""Tests for the pure rules-based narrator (offline, no Qt/network/engine)."""

import subprocess
import sys

import pandas as pd

from cqd.analyst.narrate import DISCLAIMER, narrate_account_risk
from cqd.data.portfolio import AccountRisk
from cqd.engine.risk import PortfolioRisk


def _ar(
    *,
    weights,
    ann_vol=0.40,
    ewma_vol=0.40,
    hhi=None,
    effective_bets=None,
    top3=None,
    book_beta=1.0,
    per_asset_beta=None,
    risk_contribution=None,
    unpriced=None,
    dust=None,
) -> AccountRisk:
    w = pd.Series(weights, dtype=float)
    if hhi is None:
        hhi = float((w**2).sum())
    if effective_bets is None:
        effective_bets = 1.0 / hhi if hhi else 0.0
    if top3 is None:
        top3 = float(w.sort_values(ascending=False).head(3).sum())
    if per_asset_beta is None:
        per_asset_beta = pd.Series({a: book_beta for a in w.index}, dtype=float)
    if risk_contribution is None:
        risk_contribution = pd.Series(
            {a: float(v) * 100 for a, v in w.items()}, dtype=float
        )
    risk = PortfolioRisk(
        weights=w,
        ann_vol=ann_vol,
        ewma_vol=ewma_vol,
        hhi=hhi,
        effective_bets=effective_bets,
        top3_concentration=top3,
        book_beta_btc=book_beta,
        per_asset_beta=per_asset_beta,
        risk_contribution=risk_contribution,
        tail={},
    )
    info = {
        "total_usd": 10000.0,
        "values_usd": {},
        "dust": dust or {},
        "unpriced": unpriced or [],
        "min_usd": 1.0,
    }
    return AccountRisk(risk=risk, weights=w, total_usd=10000.0, info=info)


def _section(narration, title):
    return next((b for t, b in narration.sections if t == title), None)


def test_concentrated_book_uses_concentration_language() -> None:
    ar = _ar(weights={"BTC": 0.8, "ETH": 0.15, "SOL": 0.05})  # HHI ~0.665
    body = _section(narrate_account_risk(ar), "Concentration")
    assert "concentrated" in body
    assert "BTC" in body  # top holding named


def test_diversified_book_uses_diversified_language() -> None:
    w = {a: 0.1 for a in ["BTC", "ETH", "SOL", "DOT", "XTZ", "KSM", "ADA", "PEPE", "MIM", "BABY"]}
    ar = _ar(weights=w)  # HHI = 0.10 -> diversified
    body = _section(narrate_account_risk(ar), "Concentration")
    assert "well diversified" in body


def test_high_beta_mentions_amplified() -> None:
    ar = _ar(weights={"BTC": 0.5, "ETH": 0.5}, book_beta=1.45)
    body = _section(narrate_account_risk(ar), "Market sensitivity")
    assert "amplifies BTC" in body


def test_highest_beta_holding_named() -> None:
    beta = pd.Series({"BTC": 1.0, "ETH": 1.8, "SOL": 1.2})
    ar = _ar(weights={"BTC": 0.5, "ETH": 0.3, "SOL": 0.2}, book_beta=1.2, per_asset_beta=beta)
    body = _section(narrate_account_risk(ar), "Market sensitivity")
    assert "ETH" in body  # highest-beta holding


def test_cash_heavy_book_mentions_cash_buffer() -> None:
    beta = pd.Series({"BTC": 1.0, "USD": 0.0})
    rc = pd.Series({"BTC": 100.0, "USD": 0.0})
    ar = _ar(
        weights={"BTC": 0.6, "USD": 0.4},
        book_beta=0.6,
        per_asset_beta=beta,
        risk_contribution=rc,
    )
    body = _section(narrate_account_risk(ar), "Cash buffer")
    assert body is not None
    assert "USD" in body and "40%" in body


def test_top_risk_contributor_named_and_disproportion_flagged() -> None:
    # SOL is 20% of weight but drives 50% of risk -> flagged.
    rc = pd.Series({"BTC": 30.0, "ETH": 20.0, "SOL": 50.0})
    ar = _ar(
        weights={"BTC": 0.5, "ETH": 0.3, "SOL": 0.2},
        risk_contribution=rc,
    )
    body = _section(narrate_account_risk(ar), "Risk drivers")
    assert "SOL" in body  # top contributor
    assert "20%" in body and "50%" in body  # punches above its weight


def test_vol_regime_recent_hotter() -> None:
    ar = _ar(weights={"BTC": 0.5, "ETH": 0.5}, ann_vol=0.30, ewma_vol=0.50)
    body = _section(narrate_account_risk(ar), "Volatility")
    assert "hotter" in body


def test_all_nan_risk_contribution_does_not_crash() -> None:
    # Regression (2026-07-09 audit): all-NaN risk contribution (short history ->
    # NaN portfolio vol) made rc.idxmax() raise and blanked the Analyst panel.
    nan = float("nan")
    rc = pd.Series({"BTC": nan, "ETH": nan})
    ar = _ar(
        weights={"BTC": 0.5, "ETH": 0.5},
        ann_vol=nan,
        ewma_vol=nan,
        risk_contribution=rc,
    )
    narration = narrate_account_risk(ar)  # must not raise
    assert _section(narration, "Risk drivers") is None  # section skipped
    assert "unavailable" in _section(narration, "Volatility")


def test_caveats_present_when_unpriced_or_dust() -> None:
    ar = _ar(weights={"BTC": 1.0}, unpriced=["WEIRD"], dust={"SHIB": 0.5})
    body = _section(narrate_account_risk(ar), "Caveats")
    assert body is not None
    assert "WEIRD" in body and "SHIB" in body


def test_caveats_absent_when_clean() -> None:
    ar = _ar(weights={"BTC": 0.5, "ETH": 0.5})
    assert _section(narrate_account_risk(ar), "Caveats") is None


def test_deterministic_same_input_same_output() -> None:
    ar = _ar(weights={"BTC": 0.6, "ETH": 0.25, "SOL": 0.15})
    assert narrate_account_risk(ar).sections == narrate_account_risk(ar).sections


def test_narrate_module_imports_no_qt() -> None:
    # narrate.py must stay pure: importing it (in a fresh process, so other test
    # modules' PySide6 imports don't pollute the check) pulls in no Qt/network.
    code = (
        "import sys, cqd.analyst.narrate; "
        "assert 'PySide6' not in sys.modules, 'narrate imported PySide6'; "
        "assert 'httpx' not in sys.modules and 'anthropic' not in sys.modules, "
        "'narrate imported a network client'"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_disclaimer_present() -> None:
    ar = _ar(weights={"BTC": 1.0})
    assert narrate_account_risk(ar).disclaimer == DISCLAIMER
    assert "Not financial advice" in DISCLAIMER
