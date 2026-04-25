import json
from datetime import datetime, timezone

import httpx

from shyft_solana import ShyftClient


def test_get_transaction_history_sends_expected_rest_request():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers["x-api-key"]
        return httpx.Response(200, json={"result": [{"signature": "sig1"}]})

    client = ShyftClient(api_key="key1", client=httpx.Client(transport=httpx.MockTransport(handler)))
    response = client.get_transaction_history("Account111", tx_num=25)

    assert response == {"result": [{"signature": "sig1"}]}
    assert seen["key"] == "key1"
    assert "account=Account111" in seen["url"]
    assert "tx_num=25" in seen["url"]


def test_batch_token_supply_maps_responses_by_mint():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json=[
                {"jsonrpc": "2.0", "id": row["id"], "result": {"value": {"amount": str(row["id"] + 10)}}}
                for row in payload
            ],
        )

    client = ShyftClient(api_keys=["k1", "k2"], client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert client.batch_token_supply(["mint-a", "mint-b"]) == {
        "mint-a": {"amount": "10"},
        "mint-b": {"amount": "11"},
    }


def test_count_recent_swaps_filters_by_timestamp():
    since = datetime(2026, 3, 14, 0, 0, tzinfo=timezone.utc)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": [
                    {"signature": "old", "blockTime": int(since.timestamp()) - 1},
                    {"signature": "new", "blockTime": int(since.timestamp())},
                ],
            },
        )

    client = ShyftClient(api_key="key1", client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert client.count_recent_swaps("pool", since=since) == 1


def test_http_error_sets_last_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "upstream"})

    client = ShyftClient(
        api_key="key1",
        max_retries=0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.get_transaction_history("account") is None
    assert client.last_error is not None
    assert client.last_error["status_code"] == 500
