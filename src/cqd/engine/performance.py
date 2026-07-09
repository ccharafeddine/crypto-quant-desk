"""Performance analytics: equity curve, realized PnL, trade statistics.

Pure functions - no I/O, no Qt, no network (hard engine rule). The data layer
feeds normalized ledgers/trades/closes in; everything here is a deterministic
transform, so it is exhaustively testable offline.

Conventions: daily granularity, 365-day annualization elsewhere (metrics.py),
USD-pegged assets valued at 1.0, and realized PnL follows the same running
average-cost semantics as cost_basis.py (sells release basis at the running
average; quotes are never mixed).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

#: USD-pegged assets valued at 1.0 in the equity curve (matches returns.py).
CASH_ASSETS: frozenset[str] = frozenset({"USD", "USDT", "USDC", "DAI"})

_EPS = 1e-12


# ---------- balances over time ----------


def balances_over_time(ledgers: list[dict]) -> pd.DataFrame:
    """Daily per-asset balances reconstructed from ledger entries.

    Each normalized ledger entry carries the asset's RUNNING balance after the
    entry; entries lacking one are accumulated from amounts. The frame is
    forward-filled between entries, and days before an asset's first entry use
    its implied prior balance (first balance minus first amount), so a deposit
    does not retroactively exist before it happened.
    """
    if not ledgers:
        return pd.DataFrame()
    entries = sorted(ledgers, key=lambda e: float(e["time"]))
    running: dict[str, float] = {}
    points: dict[str, dict[pd.Timestamp, float]] = {}
    prior: dict[str, float] = {}
    for e in entries:
        asset = str(e["asset"])
        amount = float(e.get("amount", 0.0) or 0.0)
        balance = e.get("balance")
        if balance is None:
            running[asset] = running.get(asset, 0.0) + amount
            balance = running[asset]
        else:
            balance = float(balance)
            running[asset] = balance
        day = pd.to_datetime(float(e["time"]), unit="s").normalize()
        if asset not in points:
            prior[asset] = balance - amount
        points.setdefault(asset, {})[day] = balance  # last entry of the day wins

    start = min(min(d) for d in points.values())
    end = max(max(d) for d in points.values())
    index = pd.date_range(start, end, freq="D")
    frame = pd.DataFrame({asset: pd.Series(vals) for asset, vals in points.items()}, index=index)
    frame = frame.ffill()
    for asset in frame.columns:
        frame[asset] = frame[asset].fillna(prior.get(asset, 0.0))
    return frame


def build_equity_curve(
    ledgers: list[dict],
    closes: dict[str, list[tuple[int, float]]],
    *,
    cash_assets: frozenset[str] = CASH_ASSETS,
) -> pd.Series:
    """Daily portfolio USD value: per-asset balances x daily closes.

    `closes` maps a bare symbol to its ascending [(unix_seconds, close)] list
    (USD-quoted). Cash assets are valued at 1.0. Assets with balances but no
    price series are EXCLUDED and reported in result.attrs["unpriced"] -
    excluding is honest, silently pricing at zero would fake a drawdown.
    The curve extends to the latest close date so today's value is included.
    """
    balances = balances_over_time(ledgers)
    if balances.empty:
        return pd.Series(dtype=float)

    price_series: dict[str, pd.Series] = {}
    last_close_day: pd.Timestamp | None = None
    for symbol, pts in closes.items():
        if not pts:
            continue
        idx = pd.to_datetime([t for t, _ in pts], unit="s").normalize()
        s = pd.Series([c for _, c in pts], index=idx, dtype=float)
        s = s[~s.index.duplicated(keep="last")]
        price_series[symbol] = s
        day = s.index.max()
        last_close_day = day if last_close_day is None else max(last_close_day, day)

    end = balances.index.max()
    if last_close_day is not None and last_close_day > end:
        end = last_close_day
    index = pd.date_range(balances.index.min(), end, freq="D")
    balances = balances.reindex(index).ffill()

    unpriced: list[str] = []
    total = pd.Series(0.0, index=index)
    for asset in balances.columns:
        if asset in cash_assets:
            total = total + balances[asset]
            continue
        prices = price_series.get(asset)
        if prices is None:
            unpriced.append(asset)
            continue
        aligned = prices.reindex(index).ffill()
        # Before the first close: value that asset's earliest known price
        # rather than zero (a young price series must not fake a jump).
        aligned = aligned.bfill()
        total = total + balances[asset] * aligned

    total.attrs["unpriced"] = unpriced
    return total


# ---------- realized round trips ----------


@dataclass(frozen=True)
class RoundTrip:
    """One sell matched against the running average cost at that moment."""

    asset: str
    quote: str
    quantity: float
    entry_avg: float
    exit_price: float
    pnl: float  # quote currency
    fee: float
    timestamp: float


def realized_trades(trades: list[dict]) -> list[RoundTrip]:
    """Every sell as a round trip, per (asset, quote), average-cost semantics.

    Mirrors cost_basis.py: buys raise basis, each sell realizes against the
    running average. Oversold quantity (missing history) is skipped, never
    guessed. Output is chronological.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for t in trades:
        symbol = str(t.get("symbol", ""))
        base, _, quote = symbol.partition("/")
        if base and quote:
            groups.setdefault((base, quote), []).append(t)

    out: list[RoundTrip] = []
    for (asset, quote), legs in groups.items():
        ordered = sorted(
            enumerate(legs), key=lambda it: (float(it[1].get("timestamp") or 0.0), it[0])
        )
        qty = 0.0
        basis = 0.0
        for _, t in ordered:
            amount = float(t["amount"])
            cost = float(t["cost"])
            fee = t.get("fee")
            fee_cost = float(fee.get("cost", 0.0)) if isinstance(fee, dict) else 0.0
            if amount <= 0:
                continue
            if t["side"] == "buy":
                qty += amount
                basis += cost
                continue
            matched = min(amount, qty)
            if matched <= _EPS:
                continue
            avg = basis / qty
            unit_price = cost / amount
            out.append(
                RoundTrip(
                    asset=asset,
                    quote=quote,
                    quantity=matched,
                    entry_avg=avg,
                    exit_price=unit_price,
                    pnl=matched * (unit_price - avg),
                    fee=fee_cost,
                    timestamp=float(t.get("timestamp") or 0.0),
                )
            )
            basis -= matched * avg
            qty -= matched
            if qty < _EPS:
                qty = 0.0
                basis = 0.0
    out.sort(key=lambda r: r.timestamp)
    return out


def trade_stats(round_trips: list[RoundTrip]) -> dict[str, float]:
    """The prop-desk numbers over realized round trips (fees included in PnL)."""
    pnls = [r.pnl - r.fee for r in round_trips]
    n = len(pnls)
    if n == 0:
        return {
            "trades": 0,
            "win_rate": float("nan"),
            "avg_win": float("nan"),
            "avg_loss": float("nan"),
            "expectancy": float("nan"),
            "profit_factor": float("nan"),
            "total_realized": 0.0,
        }
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    return {
        "trades": n,
        "win_rate": len(wins) / n,
        "avg_win": gross_win / len(wins) if wins else 0.0,
        "avg_loss": -gross_loss / len(losses) if losses else 0.0,
        "expectancy": sum(pnls) / n,
        "profit_factor": (gross_win / gross_loss) if gross_loss > _EPS else float("inf"),
        "total_realized": sum(pnls),
    }


# ---------- drawdown + periodic returns ----------


def drawdown_stats(equity: pd.Series) -> dict[str, float]:
    """Max/current drawdown and underwater durations from a daily equity curve."""
    if equity.empty or (equity <= 0).all():
        return {
            "max_drawdown": float("nan"),
            "current_drawdown": float("nan"),
            "underwater_days": 0,
            "max_underwater_days": 0,
        }
    peak = equity.cummax()
    dd = equity / peak - 1.0
    underwater = dd < -_EPS
    # Longest and current consecutive underwater runs, in days.
    runs = underwater.astype(int).groupby((~underwater).cumsum()).cumsum()
    return {
        "max_drawdown": float(dd.min()),
        "current_drawdown": float(dd.iloc[-1]),
        "underwater_days": int(runs.iloc[-1]),
        "max_underwater_days": int(runs.max()),
    }


def periodic_returns(equity: pd.Series) -> dict[str, pd.Series]:
    """Daily/weekly/monthly simple returns of the equity curve."""
    if equity.empty:
        empty = pd.Series(dtype=float)
        return {"daily": empty, "weekly": empty, "monthly": empty}
    return {
        "daily": equity.pct_change().dropna(),
        "weekly": equity.resample("W").last().pct_change().dropna(),
        "monthly": equity.resample("ME").last().pct_change().dropna(),
    }


__all__ = [
    "CASH_ASSETS",
    "RoundTrip",
    "balances_over_time",
    "build_equity_curve",
    "drawdown_stats",
    "periodic_returns",
    "realized_trades",
    "trade_stats",
]
