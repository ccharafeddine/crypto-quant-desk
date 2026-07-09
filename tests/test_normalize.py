"""Tests for the Kraken CLI JSON normalizer.

Ticker + OHLC fixtures are the VERIFIED literals captured from kraken 0.3.2.
Balance + trades fixtures are synthetic, built from the documented (auth-gap)
shapes; the trades suite covers both the dict and list container variants.
"""

from cqd.data.normalize import (
    normalize_balance,
    normalize_ohlc,
    normalize_ticker,
    normalize_trades,
    slash_symbol,
    split_pair,
    translate_asset,
)

# Verified captures from `kraken ... -o json` (kraken 0.3.2).
TICKER_RAW = {
    "XXBTZUSD": {
        "a": ["70860.00000", "1", "1.000"],
        "b": ["70859.90000", "3", "3.000"],
        "c": ["70860.00000", "0.00000138"],
        "h": ["71315.50000", "73619.80000"],
        "l": ["70000.00000", "70000.00000"],
        "o": "71315.50000",
        "p": ["70680.09937", "71581.40511"],
        "t": [13902, 79365],
        "v": ["270.96343504", "2447.04044688"],
    }
}

OHLC_RAW = {
    "XXBTZUSD": [
        [1718150400, "67348.6", "69969.0", "66923.0", "68233.7", "68567.0", "1900.5", 29809],
        [1718236800, "68233.7", "68500.0", "65000.0", "66000.0", "66800.0", "1500.0", 21000],
        [1718323200, "66000.0", "67000.0", "64000.0", "65500.0", "65800.0", "1200.0", 18000],
    ],
    "last": 1780272000,
}


# ---------- Asset / pair translation ----------


def test_translate_asset_table() -> None:
    assert translate_asset("XXBT") == "BTC"
    assert translate_asset("XBT") == "BTC"
    assert translate_asset("ZUSD") == "USD"
    assert translate_asset("XETH") == "ETH"
    # Already-bare passes through.
    assert translate_asset("SOL") == "SOL"
    assert translate_asset("USDT") == "USDT"


def test_translate_asset_heuristic_fallback() -> None:
    # Not in the table: 4-char X-prefixed crypto -> strip X.
    assert translate_asset("XZEC") == "ZEC"
    # 4-char Z-prefixed fiat -> strip Z.
    assert translate_asset("ZCAD") == "CAD"
    # 3-char unknown -> unchanged.
    assert translate_asset("ADA") == "ADA"


def test_split_pair_classic_crypto_fiat() -> None:
    assert split_pair("XXBTZUSD") == ("BTC", "USD")


def test_split_pair_fiat_prefixed_quote() -> None:
    assert split_pair("XETHZEUR") == ("ETH", "EUR")


def test_split_pair_newer_no_prefix_usdt() -> None:
    # Exercises the suffix matcher + heuristic on a modern bare pair.
    assert split_pair("SOLUSDT") == ("SOL", "USDT")


def test_slash_symbol() -> None:
    assert slash_symbol("XXBTZUSD") == "BTC/USD"
    assert slash_symbol("SOLUSDT") == "SOL/USDT"


# ---------- Ticker ----------


def test_normalize_ticker_last_price_and_symbol() -> None:
    out = normalize_ticker(TICKER_RAW)
    # Keyed by slash symbol, last = float(c[0]).
    assert out == {"BTC/USD": 70860.0}
    assert isinstance(out["BTC/USD"], float)


# ---------- OHLC ----------


def test_normalize_ohlc_shape_and_close_index() -> None:
    out = normalize_ohlc(OHLC_RAW)
    # "last" cursor dropped; one tuple per candle.
    assert len(out) == 3
    # close is index 4 of each row, parsed to float.
    assert out[0] == (1718150400, 68233.7)
    assert out[1] == (1718236800, 66000.0)
    assert all(isinstance(t, int) and isinstance(c, float) for t, c in out)


def test_normalize_ohlc_ascending() -> None:
    # Feed rows out of order; expect ascending by time.
    scrambled = {"XXBTZUSD": list(reversed(OHLC_RAW["XXBTZUSD"])), "last": 1}
    out = normalize_ohlc(scrambled)
    times = [t for t, _ in out]
    assert times == sorted(times)


# ---------- Balance ----------


def test_normalize_balance_keys_and_floats() -> None:
    raw = {"XXBT": "0.5", "ZUSD": "1000.0", "SOL": "12.25"}
    out = normalize_balance(raw)
    assert out == {"BTC": 0.5, "USD": 1000.0, "SOL": 12.25}
    assert all(isinstance(v, float) for v in out.values())


# ---------- Trades (both container shapes) ----------

_TRADE_OBJ = {
    "pair": "XXBTZUSD",
    "type": "buy",
    "vol": "0.5",
    "price": "70000.0",
    "cost": "35000.0",
    "fee": "91.0",
    "time": 1718150400.1234,
}

TRADES_DICT_RAW = {"trades": {"TX123": _TRADE_OBJ}, "count": 1}
TRADES_LIST_RAW = [_TRADE_OBJ]


def _assert_trade_shape(t: dict) -> None:
    assert t["symbol"] == "BTC/USD"  # slash form for cost_basis filter
    assert t["side"] == "buy"  # mapped from "type"
    assert t["amount"] == 0.5
    assert t["price"] == 70000.0
    assert t["cost"] == 35000.0
    # fee dict synthesized; currency = pair QUOTE asset.
    assert t["fee"] == {"cost": 91.0, "currency": "USD"}
    assert t["timestamp"] == 1718150400.1234


def test_normalize_trades_dict_container() -> None:
    out = normalize_trades(TRADES_DICT_RAW)
    assert len(out) == 1
    _assert_trade_shape(out[0])


def test_normalize_trades_list_container() -> None:
    out = normalize_trades(TRADES_LIST_RAW)
    assert len(out) == 1
    _assert_trade_shape(out[0])


def test_normalize_trades_side_mapping_sell() -> None:
    sell = {**_TRADE_OBJ, "type": "sell", "pair": "XETHZEUR"}
    out = normalize_trades([sell])
    assert out[0]["side"] == "sell"
    assert out[0]["symbol"] == "ETH/EUR"
    assert out[0]["fee"]["currency"] == "EUR"


# ---------- Live-verified normalizer fixes (real Kraken codes) ----------


def test_xxdg_maps_to_doge() -> None:
    # Real Dogecoin balance key is double-X "XXDG", not "XDG".
    assert translate_asset("XXDG") == "DOGE"
    assert translate_asset("XDG") == "DOGE"  # pair/stripped form still works


def test_sub_balance_suffix_folding() -> None:
    # Staked / hold sub-balances fold onto the base asset.
    assert translate_asset("DOT.S") == "DOT"
    assert translate_asset("KSM.S") == "KSM"
    assert translate_asset("XTZ.S") == "XTZ"
    assert translate_asset("USD.HOLD") == "USD"


def test_majors_still_translate() -> None:
    assert translate_asset("XXBT") == "BTC"
    assert translate_asset("ZUSD") == "USD"
    assert translate_asset("XETH") == "ETH"


def test_normalize_balance_sums_colliding_bare_symbols() -> None:
    raw = {"DOT": "10.0", "DOT.S": "5.0", "ZUSD": "100.0", "USD.HOLD": "25.0"}
    out = normalize_balance(raw)
    # DOT + DOT.S fold to one "DOT" key, summed.
    assert out["DOT"] == 15.0
    # ZUSD + USD.HOLD both fold to "USD", summed.
    assert out["USD"] == 125.0
    assert set(out) == {"DOT", "USD"}


def test_split_pair_crypto_quotes() -> None:
    assert split_pair("DOTXBT") == ("DOT", "BTC")
    assert split_pair("KSMXBT") == ("KSM", "BTC")
    assert split_pair("XXDGXXBT") == ("DOGE", "BTC")


def test_split_pair_fiat_still_works_after_crypto_quotes() -> None:
    # Adding XXBT/XBT must not break fiat pairs (longest-first ordering).
    assert split_pair("XXBTZUSD") == ("BTC", "USD")
    assert split_pair("XETHZEUR") == ("ETH", "EUR")
    assert split_pair("SOLUSDT") == ("SOL", "USDT")


def test_split_pair_bare_base_ending_in_z() -> None:
    # Regression (2026-07-09 audit): bare bases ending in Z faked the classic
    # ZUSD suffix and lost their last letter, dropping real holdings from risk.
    assert split_pair("XTZUSD") == ("XTZ", "USD")  # Tezos, not "XT"/USD
    assert split_pair("XTZEUR") == ("XTZ", "EUR")
    assert split_pair("REZUSD") == ("REZ", "USD")  # Renzo, not "RE"/USD
    assert split_pair("XTZXBT") == ("XTZ", "BTC")


def test_split_pair_classic_pairs_unaffected_by_z_guard() -> None:
    # Classic bases keep matching classic quotes.
    assert split_pair("XZECZUSD") == ("ZEC", "USD")  # heuristic X-prefix base
    assert split_pair("USDTZUSD") == ("USDT", "USD")  # aliased bare base
    assert split_pair("ZEURZUSD") == ("EUR", "USD")  # fiat/fiat classic pair
    assert split_pair("XETHXXBT") == ("ETH", "BTC")


def test_split_pair_bare_eth_quote() -> None:
    # Newer alts quote against bare ETH (ADAETH), not XETH.
    assert split_pair("ADAETH") == ("ADA", "ETH")


def test_slash_symbol_crypto_quote() -> None:
    assert slash_symbol("DOTXBT") == "DOT/BTC"
    assert slash_symbol("XXDGXXBT") == "DOGE/BTC"


def test_normalize_trades_crypto_quoted_pair_parses() -> None:
    # PARSING ONLY: the symbol must be "DOT/BTC" so reconstruct_cost_basis's
    # "DOT/" filter matches. NOT asserting any USD cost correctness - the
    # BTC-vs-USD denomination is a separate pending decision; cost for a
    # crypto-quoted trade is in the quote currency (BTC), not USD.
    raw = {
        "trades": {
            "TXDOT": {
                "pair": "DOTXBT",
                "type": "buy",
                "vol": "10.0",
                "price": "0.0002",
                "cost": "0.002",
                "fee": "0.000003",
                "time": 1704067200.0,
            }
        },
        "count": 1,
    }
    out = normalize_trades(raw)
    assert len(out) == 1
    assert out[0]["symbol"] == "DOT/BTC"  # engine's "DOT/" filter will match
    assert out[0]["fee"]["currency"] == "BTC"  # quote of the pair
