"""Tests for the pure analyst grounding layer (no Qt, no network)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from cqd.analyst import context


def _account_risk(**over):
    """A minimal AccountRisk-shaped object for snapshot tests."""
    risk = SimpleNamespace(
        weights=pd.Series({"XBT": 0.5, "ETH": 0.3, "SOL": 0.2}),
        hhi=0.38,
        effective_bets=2.6,
        top3_concentration=1.0,
        book_beta_btc=1.15,
        per_asset_beta=pd.Series({"XBT": 1.0, "ETH": 1.3, "SOL": float("nan")}),
        ann_vol=0.55,
        ewma_vol=0.61,
        risk_contribution=pd.Series({"XBT": 45.0, "ETH": 40.0, "SOL": float("nan")}),
    )
    ns = SimpleNamespace(
        risk=risk,
        total_usd=12345.678,
        info={"unpriced": ["FOO"], "dust": {"BAR": 0.1}, "min_usd": 1.0},
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def test_portfolio_snapshot_shapes_numbers():
    snap = context.portfolio_snapshot(_account_risk())
    assert snap["total_value_usd"] == 12345.678
    assert snap["holdings_count"] == 3
    assert snap["concentration"]["hhi"] == 0.38
    # weights kept (fractions), sorted desc by value
    assert list(snap["weights"]) == ["XBT", "ETH", "SOL"]
    assert snap["beta_to_btc"] == 1.15
    # NaN entries are dropped from per-asset maps, not rendered as NaN
    assert "SOL" not in snap["per_asset_beta_to_btc"]
    assert "SOL" not in snap["risk_contribution_pct"]
    assert snap["excluded"]["unpriced"] == ["FOO"]
    assert snap["excluded"]["dust"] == ["BAR"]


def test_snapshot_is_json_serializable_with_null_for_nan():
    risk = _account_risk().risk
    risk.book_beta_btc = float("nan")
    snap = context.portfolio_snapshot(_account_risk(risk=risk))
    blob = json.dumps(snap)  # would raise on a bare NaN
    assert '"beta_to_btc": null' in blob


def test_trades_digest_sums_only_usd_quotes():
    trades = [
        {"symbol": "XBT/USD", "side": "buy", "cost": 100.0, "fee": {"cost": 0.4}},
        {"symbol": "XBT/USD", "side": "sell", "cost": 150.0, "fee": {"cost": 0.6}},
        {"symbol": "ETH/XBT", "side": "buy", "cost": 2.0, "fee": {"cost": 0.01}},
    ]
    digest = context.trades_digest(trades, {"XBT": 42.5})
    assert digest["trade_count"] == 3
    assert digest["buys"] == 2 and digest["sells"] == 1
    assert digest["usd_gross_notional"] == 250.0  # ETH/XBT excluded
    assert digest["usd_total_fees"] == 1.0
    assert digest["non_usd_quoted_trades"] == 1
    assert digest["net_realized_pnl_usd"] == 42.5


def test_build_user_message_embeds_json_and_question():
    ctx = {"total_value_usd": 100.0}
    msg = context.build_user_message("ask", ctx, question="what is my top risk?")
    assert "what is my top risk?" in msg
    assert '"total_value_usd": 100.0' in msg
    # commentary/trades have canned instructions
    assert "portfolio commentary" in context.build_user_message("commentary", ctx).lower()
    with pytest.raises(ValueError):
        context.build_user_message("bogus", ctx)


def test_build_user_message_ask_falls_back_when_blank():
    msg = context.build_user_message("ask", {}, question="   ")
    assert "Summarize this portfolio." in msg


def test_cost_estimate_and_format():
    # 1M in + 1M out on Opus 4.8 = $5 + $25
    assert context.estimate_cost_usd("claude-opus-4-8", 1_000_000, 1_000_000) == 30.0
    line = context.format_cost("claude-opus-4-8", 1200, 480)
    assert line.startswith("$0.0")
    assert "1,200 in / 480 out" in line
    # unknown model -> no invented price
    assert context.estimate_cost_usd("who-knows", 100, 100) is None
    assert "cost unknown" in context.format_cost("who-knows", 100, 100)
