"""Tax lots (engine/tax_lots.py): FIFO and HIFO next to average cost."""

from __future__ import annotations

import pytest

from cqd.engine.tax_lots import compare_methods, lots


def _t(side: str, amount: float, price: float, ts: float, fee: float = 0.0) -> dict:
    return {
        "symbol": "BTC/USD",
        "side": side,
        "amount": amount,
        "cost": amount * price,
        "fee": {"cost": fee, "currency": "USD"},
        "timestamp": ts,
    }


TRADES = [
    _t("buy", 1.0, 100.0, 0, fee=1.0),
    _t("buy", 1.0, 300.0, 86400, fee=1.0),
    _t("buy", 1.0, 200.0, 2 * 86400),
    _t("sell", 1.5, 400.0, 400 * 86400, fee=6.0),
]


def test_fifo_and_hifo_consume_different_lots() -> None:
    fifo, hifo = lots(TRADES, "BTC", method="fifo"), lots(TRADES, "BTC", method="hifo")
    # Proceeds net of fee: 1.5 * 400 - 6 = 594.
    assert fifo.realized == pytest.approx(594 - (101 + 0.5 * 301))
    assert hifo.realized == pytest.approx(594 - (301 + 0.5 * 200))
    assert hifo.realized < fifo.realized  # HIFO defers gains
    assert fifo.quantity == pytest.approx(1.5) == hifo.quantity
    assert fifo.long_term_realized() == pytest.approx(fifo.realized)  # held > 365 days


def test_every_method_has_the_same_total_pnl() -> None:
    # Methods only move P&L between realized and unrealized, never create it.
    cmp = compare_methods(TRADES, "BTC", mark=250.0)
    totals = {m: r["realized"] + r["unrealized"] for m, r in cmp.rows.items()}
    assert totals["average"] == pytest.approx(totals["fifo"])
    assert totals["fifo"] == pytest.approx(totals["hifo"])
    assert totals["fifo"] == pytest.approx(594 + 1.5 * 250 - 602)
    assert set(cmp.rows) == {"average", "fifo", "hifo"} and cmp.quantity == pytest.approx(1.5)


def test_lots_flags_oversold_and_rejects_unknown_method() -> None:
    res = lots([_t("buy", 1.0, 10.0, 0), _t("sell", 2.0, 12.0, 1)], "BTC")
    assert res.oversold and res.realized == pytest.approx(2.0)
    with pytest.raises(ValueError):
        lots(TRADES, "BTC", method="lifo")
