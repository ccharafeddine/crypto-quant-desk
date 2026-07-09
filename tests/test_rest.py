"""Hermetic tests for the Kraken REST client (httpx.MockTransport, no network)."""

import asyncio
import json

import httpx
import pytest

from cqd.data.errors import (
    KrakenAPIError,
    KrakenAuthError,
    KrakenNetworkError,
    KrakenPermissionError,
    KrakenProtocolError,
    KrakenRateLimitError,
    KrakenTimeoutError,
    OrderRejected,
    error_from_api,
)
from cqd.data.rest import KrakenRESTClient, NonceCounter, sign_request

# Kraken's documented signature test vector
# (https://docs.kraken.com/api/docs/guides/spot-rest-auth).
_DOC_SECRET = "kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg=="
_DOC_POST = "nonce=1616492376594&ordertype=limit&pair=XBTUSD&price=37500&type=buy&volume=1.25"
_DOC_PATH = "/0/private/AddOrder"
_DOC_SIGN = "4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ=="

TICKER_RESULT = {"XXBTZUSD": {"c": ["70860.00000", "0.00000138"]}}


def _envelope(result) -> dict:
    return {"error": [], "result": result}


def _client(handler, *, key: str = "KEY123", secret: str = _DOC_SECRET) -> KrakenRESTClient:
    return KrakenRESTClient(
        api_key=key,
        api_secret=secret,
        transport=httpx.MockTransport(handler),
        nonce=NonceCounter(None),
    )


# ---------- signing ----------


def test_signature_matches_kraken_documented_vector() -> None:
    assert sign_request(_DOC_SECRET, _DOC_PATH, "1616492376594", _DOC_POST) == _DOC_SIGN


def test_nonce_strictly_increasing_and_persisted(tmp_path) -> None:
    state = tmp_path / "nonce"
    n = NonceCounter(state)
    a, b, c = n.next(), n.next(), n.next()
    assert a < b < c
    # A new counter resumes above the persisted high-water mark even if the
    # clock were behind (simulated by a huge persisted value).
    state.write_text(str(c + 10_000_000_000))
    n2 = NonceCounter(state)
    assert n2.next() == c + 10_000_000_001


# ---------- error taxonomy ----------


def test_error_from_api_mapping() -> None:
    assert isinstance(error_from_api(["EAPI:Invalid key"]), KrakenAuthError)
    assert isinstance(error_from_api(["EAPI:Invalid signature"]), KrakenAuthError)
    assert isinstance(error_from_api(["EGeneral:Permission denied"]), KrakenPermissionError)
    assert isinstance(error_from_api(["EAPI:Rate limit exceeded"]), KrakenRateLimitError)
    assert isinstance(error_from_api(["EOrder:Insufficient funds"]), OrderRejected)
    assert isinstance(error_from_api(["EQuery:Unknown asset pair"]), KrakenAPIError)
    # Raw string preserved for the audit log.
    assert "EOrder:Insufficient funds" in str(error_from_api(["EOrder:Insufficient funds"]))


# ---------- public calls ----------


def test_get_marks_normalized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/0/public/Ticker"
        assert request.url.params["pair"] == "BTCUSD"
        return httpx.Response(200, json=_envelope(TICKER_RESULT))

    out = asyncio.run(_client(handler).get_marks(["BTCUSD"]))
    assert out == {"BTC/USD": 70860.0}


def test_get_marks_degrades_per_pair() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pair = request.url.params["pair"]
        calls.append(pair)
        if "ETH2USD" in pair:
            return httpx.Response(200, json={"error": ["EQuery:Unknown asset pair"]})
        return httpx.Response(200, json=_envelope(TICKER_RESULT))

    out = asyncio.run(_client(handler).get_marks(["BTCUSD", "ETH2USD"]))
    assert out == {"BTC/USD": 70860.0}
    assert calls == ["BTCUSD,ETH2USD", "BTCUSD", "ETH2USD"]


def test_get_ohlc_closes() -> None:
    rows = [
        [1718150400, "1", "2", "0.5", "68233.7", "1.5", "10", 5],
        [1718236800, "1", "2", "0.5", "66000.0", "1.5", "10", 5],
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["interval"] == "1440"
        return httpx.Response(200, json=_envelope({"XXBTZUSD": rows, "last": 999}))

    out = asyncio.run(_client(handler).get_ohlc_closes("BTCUSD"))
    assert out == [(1718150400, 68233.7), (1718236800, 66000.0)]


def test_get_depth_normalized() -> None:
    depth = {
        "XXBTZUSD": {
            "asks": [["70861.0", "0.5", 1718150400], ["70862.0", "1.0", 1718150401]],
            "bids": [["70859.0", "0.7", 1718150400]],
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope(depth))

    out = asyncio.run(_client(handler).get_depth("BTCUSD"))
    assert out["asks"][0] == (70861.0, 0.5)
    assert out["bids"] == [(70859.0, 0.7)]


# ---------- private calls ----------


def test_private_call_signed_and_keys_never_in_url() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["url"] = str(request.url)
        seen["key_header"] = request.headers.get("API-Key")
        seen["sign_header"] = request.headers.get("API-Sign")
        seen["body"] = request.content.decode()
        return httpx.Response(200, json=_envelope({"XXBT": "0.5", "ZUSD": "100.0"}))

    out = asyncio.run(_client(handler).get_balance())
    assert out == {"BTC": 0.5, "USD": 100.0}
    assert seen["path"] == "/0/private/Balance"
    assert seen["key_header"] == "KEY123"
    assert "nonce=" in seen["body"]
    # The signature must be reproducible from exactly what was sent.
    from urllib.parse import parse_qs

    nonce = parse_qs(seen["body"])["nonce"][0]
    assert seen["sign_header"] == sign_request(_DOC_SECRET, seen["path"], nonce, seen["body"])
    # No key material in the URL (argv-equivalent of the REST world).
    assert "KEY123" not in seen["url"]
    assert _DOC_SECRET not in seen["url"]


def test_private_without_keys_raises_auth_before_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no network call may happen without keys")

    client = _client(handler, key="", secret="")
    with pytest.raises(KrakenAuthError):
        asyncio.run(client.get_balance())


def test_get_ledgers_normalized() -> None:
    ledger = {
        "ledger": {
            "L2": {
                "refid": "T2",
                "time": 1718236800.5,
                "type": "trade",
                "asset": "XXBT",
                "amount": "0.1",
                "fee": "0.0002",
                "balance": "0.6",
            },
            "L1": {
                "refid": "T1",
                "time": 1718150400.5,
                "type": "deposit",
                "asset": "ZUSD",
                "amount": "1000.0",
                "fee": "0",
                "balance": "1000.0",
            },
        },
        "count": 2,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/0/private/Ledgers"
        return httpx.Response(200, json=_envelope(ledger))

    out = asyncio.run(_client(handler).get_ledgers())
    assert [e["refid"] for e in out] == ["T1", "T2"]  # ascending by time
    assert out[0]["asset"] == "USD" and out[1]["asset"] == "BTC"  # classic codes folded
    assert out[1]["amount"] == 0.1


# ---------- failure shapes ----------


def test_envelope_error_maps_to_taxonomy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": ["EAPI:Invalid key"]})

    with pytest.raises(KrakenAuthError):
        asyncio.run(_client(handler).get_balance())


def test_non_json_response_is_protocol_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>Bad Gateway</html>")

    with pytest.raises(KrakenProtocolError):
        asyncio.run(_client(handler).get_marks(["BTCUSD"]))


def test_missing_result_envelope_is_protocol_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(KrakenProtocolError):
        asyncio.run(_client(handler).get_server_time())


def test_timeout_maps_to_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(KrakenTimeoutError):
        asyncio.run(_client(handler).get_marks(["BTCUSD"]))


def test_connect_failure_maps_to_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(KrakenNetworkError):
        asyncio.run(_client(handler).get_marks(["BTCUSD"]))


def test_trades_history_roundtrip() -> None:
    trades = {
        "trades": {
            "TX1": {
                "pair": "XXBTZUSD",
                "type": "buy",
                "vol": "0.5",
                "price": "70000.0",
                "cost": "35000.0",
                "fee": "91.0",
                "time": 1718150400.0,
            }
        },
        "count": 1,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        assert "start=1000" in body
        return httpx.Response(200, json=_envelope(trades))

    out = asyncio.run(_client(handler).get_trades(start=1000))
    assert len(out) == 1
    assert out[0]["symbol"] == "BTC/USD"
    assert out[0]["fee"] == {"cost": 91.0, "currency": "USD"}


def test_ws_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/0/private/GetWebSocketsToken"
        return httpx.Response(200, json=_envelope({"token": "WS-TOKEN", "expires": 900}))

    assert asyncio.run(_client(handler).get_ws_token()) == "WS-TOKEN"


def test_body_is_form_encoded_json_free() -> None:
    # Kraken private endpoints take form encoding, not JSON.
    def handler(request: httpx.Request) -> httpx.Response:
        assert "application/x-www-form-urlencoded" in request.headers["Content-Type"]
        with pytest.raises(json.JSONDecodeError):
            json.loads(request.content.decode())
        return httpx.Response(200, json=_envelope({}))

    asyncio.run(_client(handler).get_balance())
