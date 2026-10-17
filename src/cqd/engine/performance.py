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

from cqd.engine.cost_basis import quote_fee

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


# ---------- external flows + time-weighted returns ----------

#: Ledger types that move value INTO or OUT OF the account rather than being
#: earned by it. `transfer` covers Kraken's spot<->futures/staking moves and
#: airdrops; treating them as flows (not returns) is the conservative choice:
#: nothing that arrives from outside is ever booked as performance.
EXTERNAL_FLOW_TYPES: frozenset[str] = frozenset({"deposit", "withdrawal", "transfer"})


def _daily_prices(closes: dict[str, list[tuple[int, float]]], index: pd.DatetimeIndex):
    out: dict[str, pd.Series] = {}
    for symbol, pts in closes.items():
        if not pts:
            continue
        idx = pd.to_datetime([t for t, _ in pts], unit="s").normalize()
        s = pd.Series([c for _, c in pts], index=idx, dtype=float)
        s = s[~s.index.duplicated(keep="last")]
        out[symbol] = s.reindex(index.union(s.index)).ffill().bfill().reindex(index)
    return out


def external_flows(
    ledgers: list[dict],
    closes: dict[str, list[tuple[int, float]]],
    index: pd.DatetimeIndex,
    *,
    cash_assets: frozenset[str] = CASH_ASSETS,
) -> pd.DataFrame:
    """Daily external cash flows in USD on `index`: columns `flow` and `weighted`.

    `flow` is the signed USD value of deposits/withdrawals/transfers that day
    (crypto valued at that day's close, cash at 1.0 - the same marks as
    `build_equity_curve`). `weighted` is each flow times the fraction of its day
    it was IN the account (a 06:00 UTC deposit counts 0.75), the Modified Dietz
    day weight. Assets with no price series are skipped, exactly as the equity
    curve excludes them, so flows and equity always cover the same holdings.
    """
    frame = pd.DataFrame({"flow": 0.0, "weighted": 0.0}, index=index)
    if not ledgers or len(index) == 0:
        return frame
    prices = _daily_prices(closes, index)
    for e in ledgers:
        if str(e.get("type", "")).lower() not in EXTERNAL_FLOW_TYPES:
            continue
        asset = str(e["asset"])
        amount = float(e.get("amount", 0.0) or 0.0)
        ts = pd.to_datetime(float(e["time"]), unit="s")
        day = ts.normalize()
        if day not in frame.index:
            continue
        if asset in cash_assets:
            px = 1.0
        elif asset in prices:
            px = float(prices[asset].loc[day])
        else:
            continue  # unpriced: excluded from equity too
        usd = amount * px
        in_account = 1.0 - (ts - day).total_seconds() / 86400.0
        frame.loc[day, "flow"] += usd
        frame.loc[day, "weighted"] += usd * in_account
    return frame


def flow_adjusted_returns(equity: pd.Series, flows: pd.DataFrame) -> pd.Series:
    """Daily Modified Dietz returns: performance with deposits/withdrawals removed.

        r_t = (V_t - V_{t-1} - F_t) / (V_{t-1} + W_t)

    where F_t is the day's net external flow and W_t its time-weighted part.
    A deposit-only account therefore returns exactly 0%, where the old
    `equity.pct_change()` read a $10k -> $20k deposit as +100%. Days with no
    capital at work (denominator <= 0, e.g. before the first deposit) return 0.
    The first day has no prior value and is not returned.
    """
    if equity.empty or len(equity) < 2:
        return pd.Series(dtype=float)
    f = flows.reindex(equity.index).fillna(0.0)
    prev = equity.shift(1)
    denom = prev + f["weighted"]
    num = equity - prev - f["flow"]
    r = (num / denom).where(denom > _EPS, 0.0)
    return r.iloc[1:].astype(float)


def time_weighted_index(equity: pd.Series, flows: pd.DataFrame) -> pd.Series:
    """Chain-linked TWR wealth index on the equity dates, starting at 1.0.

    Feed THIS (not the raw equity curve) to `drawdown_stats`, `periodic_returns`
    and `monthly_return_table`: a withdrawal is not a drawdown and a deposit is
    not a gain. `index.pct_change()` reproduces `flow_adjusted_returns`.
    """
    if equity.empty:
        return pd.Series(dtype=float)
    r = flow_adjusted_returns(equity, flows)
    wealth = pd.concat([pd.Series([1.0], index=equity.index[:1]), (1.0 + r).cumprod()])
    wealth.attrs.update(equity.attrs)
    return wealth


def build_twr(
    ledgers: list[dict],
    closes: dict[str, list[tuple[int, float]]],
    *,
    cash_assets: frozenset[str] = CASH_ASSETS,
) -> tuple[pd.Series, pd.Series]:
    """(account value in USD, TWR wealth index) from ledgers + daily closes."""
    equity = build_equity_curve(ledgers, closes, cash_assets=cash_assets)
    if equity.empty:
        return equity, pd.Series(dtype=float)
    flows = external_flows(ledgers, closes, equity.index, cash_assets=cash_assets)
    return equity, time_weighted_index(equity, flows)


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
            fee_cost = quote_fee(t, quote)
            if amount <= 0:
                continue
            if t["side"] == "buy":
                # Buy fees are part of what the position cost (same as cost_basis).
                qty += amount
                basis += cost + fee_cost
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
                    fee=fee_cost * (matched / amount),  # sell fee on the matched part
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


def realized_pnl_by_asset(round_trips: list[RoundTrip], quote: str = "USD") -> dict[str, float]:
    """Realized PnL per asset for one quote (USD only by default; non-USD-quoted
    round trips are skipped, never summed as USD - the known caveat). Sorted by
    contribution descending."""
    out: dict[str, float] = {}
    for rt in round_trips:
        if rt.quote != quote:
            continue
        out[rt.asset] = out.get(rt.asset, 0.0) + rt.pnl
    return dict(sorted(out.items(), key=lambda kv: kv[1], reverse=True))


def monthly_return_table(equity: pd.Series) -> pd.DataFrame:
    """Monthly returns pivoted to a year x month (1-12) grid, for a heatmap.

    Empty frame when there is no history. Values are simple monthly returns.
    """
    monthly = periodic_returns(equity)["monthly"]
    if monthly.empty:
        return pd.DataFrame()
    frame = monthly.to_frame("ret")
    frame["year"] = frame.index.year
    frame["month"] = frame.index.month
    return frame.pivot_table(index="year", columns="month", values="ret")


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
    "EXTERNAL_FLOW_TYPES",
    "build_twr",
    "external_flows",
    "flow_adjusted_returns",
    "time_weighted_index",
    "RoundTrip",
    "balances_over_time",
    "build_equity_curve",
    "drawdown_stats",
    "monthly_return_table",
    "periodic_returns",
    "realized_pnl_by_asset",
    "realized_trades",
    "trade_stats",
]
