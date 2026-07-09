"""Rules-based narrator: plain-language commentary on AccountRisk.

Pure and deterministic. No Qt, no I/O, no network, and no risk math - it only
reads fields already computed by the engine and present on AccountRisk, and
turns them into descriptive English. Same input always yields the same output.

This is the default CLI-only analyst. A future optional-AI narrator will consume
the SAME AccountRisk contract, so this module stays cleanly separable from both
the UI and any API client. It imports nothing at runtime beyond the stdlib;
AccountRisk is referenced only for type checking.

Tone is descriptive ("the book is concentrated in BTC"), never prescriptive
("you should sell BTC"). This is analytics, not financial advice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid importing the data layer at runtime; keep this pure
    from cqd.data.portfolio import AccountRisk

# Heuristic thresholds (descriptive buckets, NOT advice). Tunable.
_HHI_DIVERSIFIED = 0.15  # below: well diversified
_HHI_CONCENTRATED = 0.25  # above: concentrated; between: moderately concentrated
_BETA_AMPLIFIED = 1.2  # above: amplified vs BTC
_BETA_DAMPENED = 0.8  # below: dampened vs BTC
_VOL_GAP = 0.10  # EWMA more than 10% above/below annualized -> regime note
_ZERO_EPS = 1e-9  # treat |beta|,|risk| below this as zero (cash-like)
_RISK_DISPROPORTION = 1.25  # risk share this many x weight -> "punches above weight"

DISCLAIMER = (
    "Descriptive analytics generated from your computed metrics. "
    "Not financial advice."
)


@dataclass
class Narration:
    """Titled commentary sections plus the standing disclaimer."""

    sections: list[tuple[str, str]]  # (title, body)
    disclaimer: str = DISCLAIMER


def _is_nan(x) -> bool:
    return x is None or x != x


def _pct0(x: float) -> str:
    return f"{x * 100:.0f}%"


def _names(weights, n: int) -> list[str]:
    return [str(a) for a, _ in weights.sort_values(ascending=False).head(n).items()]


def narrate_account_risk(ar: AccountRisk) -> Narration:
    """Plain-language commentary derived entirely from AccountRisk fields."""
    r = ar.risk
    weights = r.weights
    n_holdings = int(len(weights))
    sections: list[tuple[str, str]] = []

    # --- Concentration ---
    if r.hhi < _HHI_DIVERSIFIED:
        conc = "well diversified"
    elif r.hhi < _HHI_CONCENTRATED:
        conc = "moderately concentrated"
    else:
        conc = "concentrated"
    top3 = _names(weights, 3)
    sections.append(
        (
            "Concentration",
            f"Across {n_holdings} holdings the book is {conc} "
            f"(HHI {r.hhi:.2f}, about {r.effective_bets:.1f} effective bets). "
            f"The top three - {', '.join(top3)} - make up "
            f"{_pct0(r.top3_concentration)} of the book.",
        )
    )

    # --- Market sensitivity (BTC beta) ---
    if _is_nan(r.book_beta_btc):
        sections.append(
            (
                "Market sensitivity",
                "BTC sensitivity is unavailable (insufficient return history).",
            )
        )
    else:
        if r.book_beta_btc > _BETA_AMPLIFIED:
            beta_label = "amplifies BTC moves"
        elif r.book_beta_btc < _BETA_DAMPENED:
            beta_label = "is dampened relative to BTC"
        else:
            beta_label = "moves roughly with BTC"
        body = (
            f"Book beta to BTC is {r.book_beta_btc:.2f}; the portfolio {beta_label}."
        )
        betas = r.per_asset_beta.dropna()
        if len(betas) > 0:
            hi = betas.idxmax()
            body += (
                f" The most BTC-sensitive holding is {hi} "
                f"(beta {float(betas.loc[hi]):.2f})."
            )
        sections.append(("Market sensitivity", body))

    # --- Volatility ---
    if _is_nan(r.ann_vol) or _is_nan(r.ewma_vol):
        sections.append(
            ("Volatility", "Volatility is unavailable (insufficient return history).")
        )
    else:
        if r.ewma_vol > r.ann_vol * (1 + _VOL_GAP):
            gap = "recent volatility is running hotter than the full-window average"
        elif r.ewma_vol < r.ann_vol * (1 - _VOL_GAP):
            gap = "recent volatility is calming versus the full-window average"
        else:
            gap = "recent volatility is in line with the full-window average"
        sections.append(
            (
                "Volatility",
                f"Annualized volatility is {_pct0(r.ann_vol)}; the recent-weighted "
                f"EWMA reading is {_pct0(r.ewma_vol)}, so {gap}.",
            )
        )

    # --- Risk drivers ---
    # dropna before idxmax: an all-NaN contribution series (short history ->
    # NaN portfolio vol) used to raise and blank the whole panel.
    rc = r.risk_contribution
    if rc is not None:
        rc = rc.dropna()
    if rc is not None and len(rc) > 0:
        top_asset = rc.idxmax()
        top_rc = float(rc.loc[top_asset])
        body = (
            f"{top_asset} drives the most risk, about {top_rc:.0f}% of the total."
        )
        # Flag any holding whose risk share punches above its weight.
        flagged = []
        for asset, share in rc.sort_values(ascending=False).items():
            wp = float(weights.get(asset, 0.0)) * 100
            if wp > 0 and float(share) > wp * _RISK_DISPROPORTION:
                flagged.append((str(asset), wp, float(share)))
        if flagged:
            a, wp, sh = flagged[0]
            body += (
                f" {a} is {wp:.0f}% of the book but drives {sh:.0f}% of the risk."
            )
        sections.append(("Risk drivers", body))

    # --- Cash buffer (zero-risk, zero-beta holdings) ---
    betas = r.per_asset_beta
    cash = [
        a
        for a in weights.index
        if not _is_nan(betas.get(a))
        and abs(float(betas.get(a))) < _ZERO_EPS
        and abs(float(rc.get(a, 0.0))) < _ZERO_EPS
    ]
    if cash:
        cash_w = sum(float(weights.get(a, 0.0)) for a in cash)
        sections.append(
            (
                "Cash buffer",
                f"Cash-like, zero-risk holdings ({', '.join(map(str, cash))}) are "
                f"{_pct0(cash_w)} of the book, cushioning drawdowns and lowering "
                f"measured concentration risk.",
            )
        )

    # --- Caveats ---
    unpriced = ar.info.get("unpriced") or []
    dust = ar.info.get("dust") or {}
    caveat_bits = []
    if unpriced:
        caveat_bits.append(f"no price available for {', '.join(map(str, unpriced))}")
    if dust:
        min_usd = ar.info.get("min_usd", 1.0)
        caveat_bits.append(
            f"below the ${min_usd:g} dust threshold: {', '.join(map(str, dust))}"
        )
    if caveat_bits:
        sections.append(
            (
                "Caveats",
                "Excluded from the analysis - " + "; ".join(caveat_bits) + ".",
            )
        )

    return Narration(sections=sections)
