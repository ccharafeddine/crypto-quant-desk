"""The in-browser demo: same pure engine, Kraken public JSON in, JSON out."""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

from cqd.web import demo

ROOT = Path(__file__).resolve().parents[1]
DAY = 86_400


def _ohlc(n: int, seed: int, start: float = 100.0, vol: float = 0.03, forming: bool = True) -> dict:
    """A Kraken OHLC `result`: rows + `last` cursor; the last row is the forming bar."""
    rng = np.random.default_rng(seed)
    px = start * np.cumprod(1 + rng.normal(0.0005, vol, n))
    t0 = 1_700_000_000 - 1_700_000_000 % DAY
    rows = [
        [
            t0 + i * DAY,
            f"{p:.4f}",
            f"{p * 1.01:.4f}",
            f"{p * 0.99:.4f}",
            f"{p:.4f}",
            "0",
            "1000",
            10,
        ]
        for i, p in enumerate(px)
    ]
    last = rows[-2][0] if forming else rows[-1][0]
    return {"XXBTZUSD": rows, "last": last}


@pytest.fixture(scope="module")
def market() -> dict:
    return {
        "BTC": _ohlc(400, 1, 60_000, 0.03),
        "ETH": _ohlc(400, 2, 3_000, 0.04),
        "SOL": _ohlc(400, 3, 150, 0.05),
        "ADA": _ohlc(400, 4, 0.5, 0.05),
    }


def test_forming_candle_is_dropped() -> None:
    raw = _ohlc(10, 9)
    assert len(demo.candles_from(raw)) == 9
    assert len(demo.candles_from(_ohlc(10, 9, forming=False))) == 10


def test_weights_are_cleaned_and_normalised() -> None:
    w = demo.clean_weights({"BTC": 40, "ETH": 40, "USD": 20, "NOPE": 50, "SOL": -5})
    assert dict(w.round(6)) == {"BTC": 0.4, "ETH": 0.4, "USD": 0.2}
    with pytest.raises(ValueError):
        demo.clean_weights({"BTC": 0})


def test_portfolio_payload_is_strict_json_and_consistent(market: dict) -> None:
    text = demo.analyze_portfolio_json(json.dumps(market), json.dumps(demo.DEFAULT_WEIGHTS), 25_000)
    out = json.loads(text)  # allow_nan=False upstream: no NaN can reach the page
    assert out["n_days"] == 398  # 400 rows - forming bar - first pct_change
    assert {m["key"] for m in out["var"]["methods"]} == {
        "historical",
        "fhs",
        "parametric",
        "cornish_fisher",
    }
    assert all(m["var99"] <= m["var95"] < 0 for m in out["var"]["methods"])
    assert all(b["n"] == 398 - 250 for b in out["backtests"])
    assert len(out["replays"]) == 4 and all(r["proxied"] == [] for r in out["replays"])
    shares = [r["risk_pct"] for r in out["risk"]["rows"] if r["risk_pct"] is not None]
    assert sum(shares) == pytest.approx(1.0, abs=0.01)  # fractions, not percent points
    usd = next(r for r in out["risk"]["rows"] if r["asset"] == "USD")
    assert usd["beta"] == 0 and usd["risk_pct"] == 0  # cash dilutes, never adds risk
    att = out["attribution"]
    assert att["allocation"] + att["btc_beta"] + att["selection"] == pytest.approx(
        att["active"], abs=1e-5
    )
    assert out["backcast"]["portfolio"][0] == pytest.approx(1.0, abs=0.2)
    assert len(out["fan"]["bands"]) == 5 and len(out["fan"]["bands"][0]) == 31
    assert out["missing"] == []


def test_btc_is_fetched_as_benchmark_even_when_not_held(market: dict) -> None:
    out = demo.analyze_portfolio(market, {"ETH": 50, "USD": 50}, 1_000)
    assert out["attribution"] is not None and out["attribution"]["btc"] is not None


def test_strategy_payload(market: dict) -> None:
    pairs = {"XXBTZUSD": {"pair_decimals": 1, "lot_decimals": 8, "ordermin": "0.00005"}}
    out = json.loads(demo.analyze_strategy_json(json.dumps(market["BTC"]), json.dumps(pairs)))
    assert out["bars"] == 399
    assert out["backtest"]["fee_bps"] == 40 and out["backtest"]["slippage_bps"] == 5
    g = out["stability"]
    assert len(g["cells"]) == len(g["fasts"]) and len(g["cells"][0]) == len(g["slows"])
    if out["setup"] is not None:
        assert out["setup"]["risk_quote"] <= 0.005 * 10_000 + 1e-6


def test_demo_imports_no_order_ui_or_network_code() -> None:
    code = (
        "import sys, cqd.web.demo\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in "
        "('PySide6','httpx','keyring','websockets','anthropic') "
        "or m.startswith(('cqd.trading','cqd.ui','cqd.analyst','cqd.alerts')) "
        "or m in ('cqd.data.rest','cqd.data.ws','cqd.data.credentials','cqd.data.client')]\n"
        "print(bad)"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert res.stdout.strip() == "[]"


def test_build_ships_only_the_engine(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import build_web_demo
    finally:
        sys.path.pop(0)
    manifest = build_web_demo.build(tmp_path / "dist")
    names = zipfile.ZipFile(tmp_path / "dist" / "cqd-engine.zip").namelist()
    assert "cqd/web/demo.py" in names and "cqd/data/crash_history.json" in names
    assert not [n for n in names if n.startswith(("cqd/trading", "cqd/ui"))]
    assert {"index.html", "app.js", "worker.js", "_headers"} <= set(manifest["files"])
    app = (tmp_path / "dist" / "app.js").read_text(encoding="utf-8")
    assert "https://api.kraken.com/0/public/" in app and "/0/private" not in app
    # every engine module the demo imports is in the bundle
    for mod in ("var", "stress", "attribution", "backtest", "signals", "risk", "indicators"):
        assert f"cqd/engine/{mod}.py" in names


def test_build_refuses_a_bundle_with_order_code(tmp_path: Path, monkeypatch) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import build_web_demo
    finally:
        sys.path.pop(0)
    monkeypatch.setattr(
        build_web_demo, "ENGINE_FILES", [*build_web_demo.ENGINE_FILES, "cqd/trading/orders.py"]
    )
    with pytest.raises(SystemExit, match="refused"):
        build_web_demo.build(tmp_path / "dist")
