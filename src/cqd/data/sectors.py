"""Static crypto-sector classification for exposure analytics.

A fixed in-repo lookup (bare asset -> sector), NOT a third-party feed - it ships
no prices or fundamentals, so it doesn't breach the Kraken-only data rule (see
TECH_STACK). Unmapped assets fall to "Other"; USD/fiat and stablecoins are
"Cash / Stable". Pure.
"""

from __future__ import annotations

from typing import Iterable

_SECTOR_BY_ASSET: dict[str, str] = {
    # Layer 1
    "BTC": "L1",
    "ETH": "L1",
    "SOL": "L1",
    "ADA": "L1",
    "DOT": "L1",
    "AVAX": "L1",
    "ATOM": "L1",
    "NEAR": "L1",
    "ALGO": "L1",
    "XLM": "L1",
    "XTZ": "L1",
    "TRX": "L1",
    "KAS": "L1",
    "SUI": "L1",
    "APT": "L1",
    # Layer 2 / scaling
    "MATIC": "L2",
    "ARB": "L2",
    "OP": "L2",
    "IMX": "L2",
    # DeFi
    "UNI": "DeFi",
    "AAVE": "DeFi",
    "MKR": "DeFi",
    "CRV": "DeFi",
    "COMP": "DeFi",
    "SNX": "DeFi",
    "LDO": "DeFi",
    "DYDX": "DeFi",
    # Meme
    "DOGE": "Meme",
    "SHIB": "Meme",
    "PEPE": "Meme",
    "WIF": "Meme",
    "FLOKI": "Meme",
    "BONK": "Meme",
    # Oracle / infra
    "LINK": "Oracle",
    # Payments / store-of-value alts
    "XRP": "Payments",
    "LTC": "Payments",
    "BCH": "Payments",
    # Privacy
    "XMR": "Privacy",
    "ZEC": "Privacy",
    # Cash / stable
    "USDT": "Cash / Stable",
    "USDC": "Cash / Stable",
    "DAI": "Cash / Stable",
    "USD": "Cash / Stable",
    "EUR": "Cash / Stable",
    "GBP": "Cash / Stable",
}
_DEFAULT_SECTOR = "Other"


def sector_of(asset: str) -> str:
    """Sector for a bare asset code (e.g. 'BTC' -> 'L1'), 'Other' if unmapped."""
    return _SECTOR_BY_ASSET.get(asset.upper(), _DEFAULT_SECTOR)


def sector_exposure(weights: Iterable[tuple[str, float]] | dict[str, float]) -> dict[str, float]:
    """Aggregate portfolio weights by sector, sorted by weight descending.

    `weights` is bare-asset -> weight (a dict or any (asset, weight) iterable,
    e.g. a pandas Series via .items()).
    """
    # dict and pandas Series both expose .items() as (key, value); a raw
    # iterable of (asset, weight) tuples is used as-is.
    pairs = weights.items() if hasattr(weights, "items") else weights
    out: dict[str, float] = {}
    for asset, weight in pairs:
        sector = sector_of(asset)
        out[sector] = out.get(sector, 0.0) + float(weight)
    return dict(sorted(out.items(), key=lambda kv: kv[1], reverse=True))
