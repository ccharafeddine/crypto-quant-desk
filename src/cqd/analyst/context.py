"""Pure grounding layer for the AI analyst.

Turns engine outputs (AccountRisk, normalized trades, engine-computed realized
PnL) into a compact, JSON-serializable context block, and builds the system and
user messages sent to Claude. No Qt, no I/O, no network, no key material - this
module never touches secrets and never invents numbers; it only reshapes numbers
the engine already computed so the model can narrate them.

The hard guarantee (PRD AC7.3): every figure the model sees is engine-computed
and passed in here. The system prompt forbids inventing or recomputing numbers.
"""

from __future__ import annotations

import json
import math
from typing import Any

# Per-1M-token pricing (USD): (input, output). Mirrors the analyst model choice
# in docs/TECH_STACK.md. Unknown models fall back to a "cost unknown" label.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

SYSTEM_PROMPT = (
    "You are the risk analyst inside a personal Kraken trading desk. You are "
    "given a JSON block of numbers that the app's engine has already computed "
    "from the user's account. Your job is to explain those numbers in plain, "
    "direct English.\n\n"
    "Hard rules:\n"
    "- Use ONLY the numbers in the provided JSON. Never invent, estimate, "
    "recompute, or extrapolate a figure that is not present. If something the "
    "user asks about is not in the data, say it is not available.\n"
    "- A value of null means the engine could not compute it (usually too "
    "little return history). Say so plainly; do not guess.\n"
    "- Be descriptive, not prescriptive. Explain what the book looks like; do "
    "not tell the user to buy or sell. This is analytics, not financial advice.\n"
    "- Be concise. Lead with the point. No preamble, no filler.\n"
    "- Weights, shares and vols are fractions (0.25 means 25%); render them as "
    "percentages. Dollar figures are USD."
)


def _f(x: Any) -> float | None:
    """Round a numeric to 6 sig places; NaN/inf/None -> None (JSON null)."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return round(v, 6)


def _series_map(series: Any, *, dropna: bool = True) -> dict[str, float]:
    """A pandas Series -> {label: rounded value}, skipping NaN when asked."""
    if series is None:
        return {}
    items = series.dropna().items() if dropna else series.items()
    out: dict[str, float] = {}
    for key, val in items:
        v = _f(val)
        if v is not None:
            out[str(key)] = v
    return out


def portfolio_snapshot(ar: Any) -> dict[str, Any]:
    """AccountRisk -> compact dict of the numbers the model may narrate."""
    r = ar.risk
    weights = r.weights.sort_values(ascending=False) if len(r.weights) else r.weights
    info = ar.info or {}
    return {
        "total_value_usd": _f(ar.total_usd),
        "holdings_count": int(len(r.weights)),
        "concentration": {
            "hhi": _f(r.hhi),
            "effective_bets": _f(r.effective_bets),
            "top3_share": _f(r.top3_concentration),
        },
        "weights": _series_map(weights, dropna=False),
        "beta_to_btc": _f(r.book_beta_btc),
        "per_asset_beta_to_btc": _series_map(r.per_asset_beta),
        "annualized_vol": _f(r.ann_vol),
        "ewma_vol": _f(r.ewma_vol),
        "risk_contribution_pct": _series_map(r.risk_contribution),
        "excluded": {
            "unpriced": [str(x) for x in (info.get("unpriced") or [])],
            "dust_below_usd": _f(info.get("min_usd", 1.0)),
            "dust": [str(x) for x in (info.get("dust") or {})],
        },
    }


def trades_digest(trades: list[dict], realized_by_asset: dict[str, float]) -> dict[str, Any]:
    """Recent normalized trades + engine realized PnL -> narratable summary.

    Notional and fees are summed only over USD-quoted fills; non-USD quotes are
    counted separately, never silently summed as USD (project guardrail).
    """
    usd = [t for t in trades if str(t.get("symbol", "")).endswith("/USD")]
    buys = sum(1 for t in trades if t.get("side") == "buy")
    sells = sum(1 for t in trades if t.get("side") == "sell")
    notional = sum(float(t["cost"]) for t in usd)
    fees = sum(float(t.get("fee", {}).get("cost", 0.0)) for t in usd)
    realized = {k: round(float(v), 2) for k, v in realized_by_asset.items()}
    return {
        "trade_count": len(trades),
        "buys": buys,
        "sells": sells,
        "usd_gross_notional": round(notional, 2),
        "usd_total_fees": round(fees, 2),
        "non_usd_quoted_trades": len(trades) - len(usd),
        "realized_pnl_by_asset_usd": realized,
        "net_realized_pnl_usd": round(sum(realized.values()), 2),
    }


def build_user_message(mode: str, context: dict[str, Any], question: str | None = None) -> str:
    """Assemble the user turn: an instruction line plus the engine JSON block."""
    blob = json.dumps(context, indent=2, sort_keys=True)
    if mode == "commentary":
        ask = (
            "Give a short portfolio commentary: concentration, market "
            "sensitivity to BTC, volatility regime, and what is driving risk. "
            "A few tight paragraphs."
        )
    elif mode == "trades":
        ask = (
            "Review my recent trading activity: what I did and what it realized "
            "(and cost in fees). Keep it to the numbers shown."
        )
    elif mode == "ask":
        ask = (question or "").strip() or "Summarize this portfolio."
    else:
        raise ValueError(f"unknown analyst mode: {mode}")
    return f"{ask}\n\nEngine-computed data (JSON):\n{blob}"


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """USD cost of one call, or None if the model's pricing is unknown."""
    price = PRICING.get(model)
    if price is None:
        return None
    in_rate, out_rate = price
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000


def format_cost(model: str, input_tokens: int, output_tokens: int) -> str:
    """Human line for the panel footer, e.g. '$0.0123  (1,204 in / 486 out)'."""
    cost = estimate_cost_usd(model, input_tokens, output_tokens)
    tokens = f"{input_tokens:,} in / {output_tokens:,} out"
    if cost is None:
        return f"cost unknown  ({tokens})"
    return f"${cost:.4f}  ({tokens})"
