from __future__ import annotations

import os
import time
from dataclasses import dataclass


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.strip()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def resolve_api_keys(
    api_key: str | None = None,
    api_keys: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    keys: list[str] = []
    if api_key:
        keys.append(api_key)
    if api_keys:
        keys.extend(api_keys)
    if not keys:
        env_keys = os.getenv("SHYFT_API_KEYS", "")
        if env_keys:
            keys.extend(env_keys.split(","))
        env_key = os.getenv("SHYFT_API_KEY")
        if env_key:
            keys.append(env_key)
    return _dedupe(keys)


@dataclass
class KeyState:
    key: str
    rest_available_at: float = 0.0
    rpc_available_at: float = 0.0
    consecutive_429s: int = 0
    total_requests: int = 0


class KeyPool:
    REST_INTERVAL = 1.0
    RPC_INTERVAL = 0.1

    def __init__(self, keys: list[str] | tuple[str, ...]):
        unique = _dedupe(list(keys))
        if not unique:
            raise ValueError("At least one Shyft API key is required")
        self._keys = [KeyState(key=key) for key in unique]
        self._batch_index = 0

    @property
    def size(self) -> int:
        return len(self._keys)

    def get_key_for_rest(self) -> tuple[str, float]:
        return self._reserve("rest")

    def get_key_for_rpc(self) -> tuple[str, float]:
        return self._reserve("rpc")

    def get_key_for_batch_rpc(self) -> str:
        key_state = self._keys[self._batch_index]
        self._batch_index = (self._batch_index + 1) % len(self._keys)
        key_state.total_requests += 1
        return key_state.key

    def report_429(self, api_key: str, endpoint_type: str = "rest") -> None:
        key_state = self._find(api_key)
        if key_state is None:
            return
        key_state.consecutive_429s += 1
        penalty = min(2 ** key_state.consecutive_429s, 30)
        available_at = time.monotonic() + penalty
        if endpoint_type == "rpc":
            key_state.rpc_available_at = available_at
        else:
            key_state.rest_available_at = available_at

    def report_success(self, api_key: str) -> None:
        key_state = self._find(api_key)
        if key_state is not None:
            key_state.consecutive_429s = 0

    def stats(self) -> list[dict[str, int | str]]:
        return [
            {
                "key": f"{state.key[:8]}...",
                "requests": state.total_requests,
                "consecutive_429s": state.consecutive_429s,
            }
            for state in self._keys
        ]

    def _reserve(self, endpoint_type: str) -> tuple[str, float]:
        now = time.monotonic()
        attr = "rpc_available_at" if endpoint_type == "rpc" else "rest_available_at"
        interval = self.RPC_INTERVAL if endpoint_type == "rpc" else self.REST_INTERVAL
        key_state = min(self._keys, key=lambda state: getattr(state, attr))
        wait = max(0.0, getattr(key_state, attr) - now)
        setattr(key_state, attr, max(now, getattr(key_state, attr)) + interval)
        key_state.total_requests += 1
        return key_state.key, wait

    def _find(self, api_key: str) -> KeyState | None:
        for key_state in self._keys:
            if key_state.key == api_key:
                return key_state
        return None
