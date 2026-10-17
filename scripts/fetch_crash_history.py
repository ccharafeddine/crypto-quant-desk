"""Rebuild src/cqd/data/crash_history.json from Kraken's public Trades endpoint.

Kraken's OHLC endpoint only serves the most recent 720 bars, so the crash
windows (2022 onward) are priced from the public trade history instead: the
first trade at or after each window edge is that asset's mark there. No keys,
read-only, stdlib only. Run: python scripts/fetch_crash_history.py
"""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src" / "cqd" / "data" / "crash_history.json"

# (key, label, start UTC, end UTC, note). Edges sit near the BTC pre-crash
# level and the trough, so the move is point-to-point at those marks.
WINDOWS = [
    (
        "luna_2022",
        "LUNA / UST collapse",
        "2022-05-05T00:00",
        "2022-05-12T12:00",
        "UST lost its peg and LUNA went to zero; contagion across majors.",
    ),
    (
        "ftx_2022",
        "FTX collapse",
        "2022-11-06T00:00",
        "2022-11-09T22:00",
        "Run on FTX/Alameda; exchange insolvency, withdrawals halted.",
    ),
    (
        "yen_carry_2024",
        "5 Aug 2024 carry unwind",
        "2024-08-02T00:00",
        "2024-08-05T06:00",
        "Yen carry-trade unwind; global risk-off, thin weekend crypto liquidity.",
    ),
    (
        "oct10_2025",
        "10 Oct 2025 liquidation cascade",
        "2025-10-10T12:00",
        "2025-10-10T21:20",
        "Tariff headline into a record leveraged long book; alts gapped far more than BTC.",
    ),
]

# Bare asset -> Kraken pair. Stablecoins are included so depeg moves are real.
PAIRS = {
    "BTC": "XBTUSD",
    "ETH": "ETHUSD",
    "SOL": "SOLUSD",
    "XRP": "XRPUSD",
    "ADA": "ADAUSD",
    "DOT": "DOTUSD",
    "LINK": "LINKUSD",
    "LTC": "LTCUSD",
    "DOGE": "XDGUSD",
    "AVAX": "AVAXUSD",
    "USDC": "USDCUSD",
    "USDT": "USDTZUSD",
}


def _ts(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp())


def first_trade(pair: str, ts: int) -> tuple[float, float] | None:
    url = f"https://api.kraken.com/0/public/Trades?pair={pair}&since={ts * 10**9}&count=1"
    for attempt in range(5):
        with urllib.request.urlopen(url, timeout=20) as resp:  # noqa: S310 - fixed host
            body = json.load(resp)
        if body.get("error"):
            if any("Too many" in e or "Rate" in e for e in body["error"]):
                time.sleep(2 + attempt * 2)
                continue
            return None
        rows = next((v for k, v in body["result"].items() if k != "last"), [])
        if not rows:
            return None
        price, _vol, when = rows[0][:3]
        if float(when) - ts > 6 * 3600:
            return None  # no trading near the edge: the pair did not list yet
        return float(price), float(when)
    return None


def main() -> None:
    out = {"source": "Kraken public Trades (first trade at/after each edge)", "windows": []}
    for key, label, start, end, note in WINDOWS:
        s, e = _ts(start), _ts(end)
        moves = {}
        for asset, pair in PAIRS.items():
            a = first_trade(pair, s)
            time.sleep(1.1)
            b = first_trade(pair, e)
            time.sleep(1.1)
            if a and b:
                moves[asset] = {"start": a[0], "end": b[0], "return": round(b[0] / a[0] - 1, 6)}
        out["windows"].append(
            {"key": key, "label": label, "start": start, "end": end, "note": note, "assets": moves}
        )
        print(key, {k: v["return"] for k, v in moves.items()})
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
