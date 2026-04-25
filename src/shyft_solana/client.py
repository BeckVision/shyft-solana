from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from .key_pool import KeyPool, resolve_api_keys


class ShyftClient:
    REST_BASE_URL = "https://api.shyft.to/sol/v1"
    RPC_BASE_URL = "https://rpc.shyft.to"

    def __init__(
        self,
        api_key: str | None = None,
        api_keys: list[str] | tuple[str, ...] | None = None,
        timeout: float = 15,
        max_retries: int = 3,
        rest_base_url: str | None = None,
        rpc_base_url: str | None = None,
        client: httpx.Client | None = None,
    ):
        keys = resolve_api_keys(api_key=api_key, api_keys=api_keys)
        self.key_pool = KeyPool(keys)
        self.timeout = timeout
        self.max_retries = max_retries
        self.rest_base_url = (rest_base_url or self.REST_BASE_URL).rstrip("/")
        self.rpc_base_url = (rpc_base_url or self.RPC_BASE_URL).rstrip("/")
        self.last_error: dict[str, Any] | None = None
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "ShyftClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get_transaction_history(
        self,
        account: str,
        tx_num: int = 100,
        before_tx_signature: str | None = None,
        **params: Any,
    ) -> dict[str, Any] | None:
        query = {"account": account, "tx_num": tx_num, **params}
        if before_tx_signature:
            query["before_tx_signature"] = before_tx_signature
        return self._get("/transaction/history", params=query)

    def get_transaction_history_paginated(
        self,
        account: str,
        total_txns: int = 500,
        page_size: int = 100,
        **params: Any,
    ):
        fetched = 0
        before: str | None = None
        while fetched < total_txns:
            limit = min(page_size, total_txns - fetched)
            response = self.get_transaction_history(
                account,
                tx_num=limit,
                before_tx_signature=before,
                **params,
            )
            transactions = _extract_result_list(response)
            if not transactions:
                break
            for transaction in transactions:
                fetched += 1
                yield transaction
                if fetched >= total_txns:
                    break
            before = _extract_signature(transactions[-1])
            if not before or len(transactions) < limit:
                break

    def fetch_all_token_transactions(
        self,
        account: str,
        max_txns: int = 500,
        page_size: int = 100,
        **params: Any,
    ) -> list[dict[str, Any]]:
        return list(
            self.get_transaction_history_paginated(
                account,
                total_txns=max_txns,
                page_size=page_size,
                **params,
            )
        )

    def get_token_supply(self, mint: str) -> Any:
        response = self._rpc("getTokenSupply", [mint])
        return _extract_rpc_value(response)

    def batch_token_supply(self, mints: list[str]) -> dict[str, Any]:
        calls = [
            {"jsonrpc": "2.0", "id": index, "method": "getTokenSupply", "params": [mint]}
            for index, mint in enumerate(mints)
        ]
        responses = self._batch_rpc(calls)
        return {
            mints[int(item["id"])]: _extract_rpc_value(item)
            for item in responses or []
            if "id" in item
        }

    def count_recent_swaps(self, pool_address: str, since: Any, limit: int = 1000) -> int | None:
        signatures = self._rpc(
            "getSignaturesForAddress",
            [pool_address, {"limit": limit}],
        )
        rows = _extract_rpc_value(signatures)
        if rows is None:
            return None
        since_ts = _to_timestamp(since)
        return sum(1 for row in rows if row.get("blockTime") and row["blockTime"] >= since_ts)

    def batch_recent_swaps(self, pools: list[str], since: Any, limit: int = 1000) -> dict[str, int]:
        calls = [
            {
                "jsonrpc": "2.0",
                "id": index,
                "method": "getSignaturesForAddress",
                "params": [pool, {"limit": limit}],
            }
            for index, pool in enumerate(pools)
        ]
        responses = self._batch_rpc(calls)
        since_ts = _to_timestamp(since)
        counts: dict[str, int] = {}
        for item in responses or []:
            pool = pools[int(item["id"])]
            rows = _extract_rpc_value(item) or []
            counts[pool] = sum(1 for row in rows if row.get("blockTime") and row["blockTime"] >= since_ts)
        return counts

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
        api_key, wait = self.key_pool.get_key_for_rest()
        if wait:
            time.sleep(wait)
        url = f"{self.rest_base_url}{path}"
        return self._request("GET", url, api_key=api_key, params=params, endpoint_type="rest")

    def _rpc(self, method: str, params: list[Any]) -> dict[str, Any] | None:
        api_key, wait = self.key_pool.get_key_for_rpc()
        if wait:
            time.sleep(wait)
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        return self._request("POST", self.rpc_base_url, api_key=api_key, json=payload, endpoint_type="rpc")

    def _batch_rpc(self, calls: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        api_key = self.key_pool.get_key_for_batch_rpc()
        response = self._request("POST", self.rpc_base_url, api_key=api_key, json=calls, endpoint_type="rpc")
        return response if isinstance(response, list) else None

    def _request(self, method: str, url: str, api_key: str, endpoint_type: str, **kwargs: Any) -> Any:
        headers = {"x-api-key": api_key}
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.request(method, url, headers=headers, **kwargs)
                if response.status_code == 429:
                    self.key_pool.report_429(api_key, endpoint_type)
                    self.last_error = {"status_code": 429, "message": "rate limited", "endpoint": url}
                    if attempt < self.max_retries:
                        retry_after = float(response.headers.get("Retry-After", "1"))
                        time.sleep(retry_after)
                        continue
                response.raise_for_status()
                payload = response.json()
                self.key_pool.report_success(api_key)
                self.last_error = None
                return payload
            except (httpx.HTTPError, ValueError) as exc:
                self.last_error = {"status_code": getattr(getattr(exc, "response", None), "status_code", None), "message": str(exc), "endpoint": url}
                if attempt >= self.max_retries:
                    return None
                time.sleep(min(2 ** attempt, 8))
        return None


class AsyncShyftClient:
    REST_BASE_URL = ShyftClient.REST_BASE_URL
    RPC_BASE_URL = ShyftClient.RPC_BASE_URL

    def __init__(
        self,
        api_key: str | None = None,
        api_keys: list[str] | tuple[str, ...] | None = None,
        timeout: float = 15,
        max_retries: int = 3,
        rest_base_url: str | None = None,
        rpc_base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        keys = resolve_api_keys(api_key=api_key, api_keys=api_keys)
        self.key_pool = KeyPool(keys)
        self.timeout = timeout
        self.max_retries = max_retries
        self.rest_base_url = (rest_base_url or self.REST_BASE_URL).rstrip("/")
        self.rpc_base_url = (rpc_base_url or self.RPC_BASE_URL).rstrip("/")
        self.last_error: dict[str, Any] | None = None
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "AsyncShyftClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def get_transaction_history(self, account: str, tx_num: int = 100, **params: Any) -> dict[str, Any] | None:
        query = {"account": account, "tx_num": tx_num, **params}
        return await self._get("/transaction/history", params=query)

    async def get_token_supply(self, mint: str) -> Any:
        response = await self._rpc("getTokenSupply", [mint])
        return _extract_rpc_value(response)

    async def batch_token_supply(self, mints: list[str]) -> dict[str, Any]:
        calls = [
            {"jsonrpc": "2.0", "id": index, "method": "getTokenSupply", "params": [mint]}
            for index, mint in enumerate(mints)
        ]
        responses = await self._batch_rpc(calls)
        return {
            mints[int(item["id"])]: _extract_rpc_value(item)
            for item in responses or []
            if "id" in item
        }

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
        api_key, wait = self.key_pool.get_key_for_rest()
        if wait:
            await asyncio.sleep(wait)
        return await self._request("GET", f"{self.rest_base_url}{path}", api_key=api_key, params=params, endpoint_type="rest")

    async def _rpc(self, method: str, params: list[Any]) -> dict[str, Any] | None:
        api_key, wait = self.key_pool.get_key_for_rpc()
        if wait:
            await asyncio.sleep(wait)
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        return await self._request("POST", self.rpc_base_url, api_key=api_key, json=payload, endpoint_type="rpc")

    async def _batch_rpc(self, calls: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        api_key = self.key_pool.get_key_for_batch_rpc()
        response = await self._request("POST", self.rpc_base_url, api_key=api_key, json=calls, endpoint_type="rpc")
        return response if isinstance(response, list) else None

    async def _request(self, method: str, url: str, api_key: str, endpoint_type: str, **kwargs: Any) -> Any:
        headers = {"x-api-key": api_key}
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.request(method, url, headers=headers, **kwargs)
                if response.status_code == 429:
                    self.key_pool.report_429(api_key, endpoint_type)
                    self.last_error = {"status_code": 429, "message": "rate limited", "endpoint": url}
                    if attempt < self.max_retries:
                        retry_after = float(response.headers.get("Retry-After", "1"))
                        await asyncio.sleep(retry_after)
                        continue
                response.raise_for_status()
                payload = response.json()
                self.key_pool.report_success(api_key)
                self.last_error = None
                return payload
            except (httpx.HTTPError, ValueError) as exc:
                self.last_error = {"status_code": getattr(getattr(exc, "response", None), "status_code", None), "message": str(exc), "endpoint": url}
                if attempt >= self.max_retries:
                    return None
                await asyncio.sleep(min(2 ** attempt, 8))
        return None


def _extract_result_list(response: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not response:
        return []
    result = response.get("result", [])
    return result if isinstance(result, list) else []


def _extract_signature(transaction: dict[str, Any]) -> str | None:
    signatures = transaction.get("signatures")
    if isinstance(signatures, list) and signatures:
        return signatures[0]
    signature = transaction.get("signature")
    return signature if isinstance(signature, str) else None


def _extract_rpc_value(response: dict[str, Any] | None) -> Any:
    if not response or "error" in response:
        return None
    result = response.get("result")
    if isinstance(result, dict) and "value" in result:
        return result["value"]
    return result


def _to_timestamp(value: Any) -> int:
    if hasattr(value, "timestamp"):
        return int(value.timestamp())
    return int(value)
