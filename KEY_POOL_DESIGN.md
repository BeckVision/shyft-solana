# Key Pool Design

## Goal

Transparent key rotation that multiplies throughput. Code using `ShyftClient` shouldn't care whether there's 1 key or 6 behind it.

---

## API

```python
# 1 key — current behavior, nothing changes
client = ShyftClient(api_key="key1")

# Multiple keys — automatic rotation
client = ShyftClient(api_keys=["k1", "k2", "k3", "k4"])

# Mixed — api_key becomes first in pool
client = ShyftClient(api_key="k1", api_keys=["k2", "k3"])
# pool = ["k1", "k2", "k3"]

# From env — comma-separated
# SHYFT_API_KEYS=k1,k2,k3,k4
client = ShyftClient()  # auto-discovers from env
```

The `api_key` (singular) parameter stays for backward compatibility. If both `api_key` and `api_keys` are provided, they merge (deduplicated). If neither is provided, fall back to `SHYFT_API_KEYS` env var (comma-separated), then `SHYFT_API_KEY` (singular, legacy).

---

## KeyPool Class

```python
import time
import asyncio
from dataclasses import dataclass, field


@dataclass
class KeyState:
    """Per-key rate limit tracking."""
    key: str
    rest_available_at: float = 0.0    # monotonic time when REST is next available
    rpc_available_at: float = 0.0     # monotonic time when RPC is next available
    consecutive_429s: int = 0         # backoff escalation
    total_requests: int = 0           # stats


class KeyPool:
    """
    Round-robin key rotation with per-key rate limit awareness.

    Thread-safe for sync, uses asyncio.Lock for async.
    """

    # Free tier intervals (seconds between requests)
    REST_INTERVAL = 1.0   # 1 req/sec per key
    RPC_INTERVAL = 0.1    # 10 req/sec per key

    def __init__(self, keys: list[str]):
        if not keys:
            raise ValueError("At least one API key required")
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for k in keys:
            if k not in seen:
                seen.add(k)
                unique.append(k)
        self._keys = [KeyState(key=k) for k in unique]
        self._index = 0
        self._lock = None       # lazy asyncio.Lock

    @property
    def size(self) -> int:
        return len(self._keys)

    def get_key_for_rest(self) -> tuple[str, float]:
        """
        Get the best key for a REST request.

        Returns (api_key, wait_seconds). Caller should sleep `wait_seconds`
        before making the request (0.0 if immediately available).

        Strategy: pick the key with earliest availability. If all are
        busy, return the one that frees up soonest.
        """
        now = time.monotonic()

        # Find key with earliest REST availability
        best = min(self._keys, key=lambda k: k.rest_available_at)
        wait = max(0.0, best.rest_available_at - now)

        # Mark as used (reserve the slot)
        best.rest_available_at = max(now, best.rest_available_at) + self.REST_INTERVAL
        best.total_requests += 1

        return best.key, wait

    def get_key_for_rpc(self) -> tuple[str, float]:
        """Same as get_key_for_rest but for RPC rate limits."""
        now = time.monotonic()
        best = min(self._keys, key=lambda k: k.rpc_available_at)
        wait = max(0.0, best.rpc_available_at - now)
        best.rpc_available_at = max(now, best.rpc_available_at) + self.RPC_INTERVAL
        best.total_requests += 1
        return best.key, wait

    def get_key_for_batch_rpc(self) -> str:
        """
        Get a key for batch RPC. No rate limit wait needed —
        batching stays within 1 req/sec naturally due to latency.
        Simple round-robin.
        """
        key_state = self._keys[self._index]
        self._index = (self._index + 1) % len(self._keys)
        key_state.total_requests += 1
        return key_state.key

    def report_429(self, api_key: str, endpoint_type: str = "rest"):
        """
        Called when a 429 is received. Extends that key's cooldown.
        """
        for ks in self._keys:
            if ks.key == api_key:
                ks.consecutive_429s += 1
                penalty = min(2 ** ks.consecutive_429s, 30)  # max 30s backoff
                now = time.monotonic()
                if endpoint_type == "rest":
                    ks.rest_available_at = now + penalty
                else:
                    ks.rpc_available_at = now + penalty
                break

    def report_success(self, api_key: str):
        """Called on successful request. Resets 429 counter."""
        for ks in self._keys:
            if ks.key == api_key:
                ks.consecutive_429s = 0
                break

    def stats(self) -> list[dict]:
        """Per-key usage stats."""
        return [
            {
                "key": ks.key[:8] + "...",
                "requests": ks.total_requests,
                "consecutive_429s": ks.consecutive_429s,
            }
            for ks in self._keys
        ]
```

---

## How It Integrates with ShyftClient

```python
class ShyftClient:
    def __init__(self, *, api_key=None, api_keys=None):
        keys = _resolve_keys(api_key, api_keys)
        self._pool = KeyPool(keys)

    def get_transaction_history(self, account, **kwargs):
        key, wait = self._pool.get_key_for_rest()
        if wait > 0:
            time.sleep(wait)
        try:
            response = self._get("/transaction/history", api_key=key, **kwargs)
            if response.status_code == 429:
                self._pool.report_429(key, "rest")
                # retry with different key
                key2, wait2 = self._pool.get_key_for_rest()
                if wait2 > 0:
                    time.sleep(wait2)
                response = self._get("/transaction/history", api_key=key2, **kwargs)
            else:
                self._pool.report_success(key)
            return response
        except Exception:
            raise

    def batch_token_supply(self, mints: list[str]):
        key = self._pool.get_key_for_batch_rpc()
        # ... batch call with this key

    async def async_get_transaction_history(self, account, **kwargs):
        key, wait = self._pool.get_key_for_rest()
        if wait > 0:
            await asyncio.sleep(wait)
        # ... same pattern but async
```

The internal `_get`, `_post` methods accept an `api_key` param that overrides the header per-request. This is the only change to the HTTP layer.

---

## Throughput Multiplier (Theoretical)

Based on API_EXPLORATION.md findings:

### REST (rate-limited to 1 concurrent/key)

| Keys | Concurrent REST | Effective req/sec |
|------|----------------|-------------------|
| 1 | 1 | ~1 |
| 2 | 2 | ~2 |
| 4 | 4 | ~4 |
| 6 | 6 | ~6 |

### RPC — Concurrent (10 concurrent/key)

| Keys | Concurrent RPC | Effective req/sec |
|------|---------------|-------------------|
| 1 | 10 | ~10 |
| 4 | 40 | ~40 |
| 6 | 60 | ~60 |

### RPC — Batching (better than concurrent)

| Keys | Batch Size/Key | Calls per ~1s Round-Trip |
|------|---------------|--------------------------|
| 1 | 300 | 300 |
| 4 | 300 each | 1,200 (4 parallel batches) |
| 6 | 300 each | 1,800 (6 parallel batches) |

Batch + multi-key + async = fire 4 batch requests concurrently, each with a different key, each containing 300 methods. **1,200 RPC calls in ~1 second.**

### REST — Multi-key with Async

The real win for REST. Currently limited to 1 req/sec. With 4 keys + async:

```python
async def fetch_4_pages_at_once(client, accounts):
    """4 concurrent REST requests, each using a different key."""
    tasks = [client.async_get_transaction_history(acc) for acc in accounts[:4]]
    return await asyncio.gather(*tasks)
    # KeyPool automatically assigns different keys to each
```

---

## Env Var Convention

```bash
# Single key (legacy, backward compat)
SHYFT_API_KEY=3CLkNbQt...

# Multi-key (new)
SHYFT_API_KEYS=3CLkNbQt...,7xMnPqRs...,9aBcDeFg...,2HiJkLmN...
```

---

## What Doesn't Change

- All public methods (`get_transaction_history`, `count_recent_swaps`, etc.)
- Return types and error handling
- Batch RPC API
- Parser code (doesn't touch HTTP)

The only visible difference: you pass `api_keys=[...]` instead of `api_key="..."` at construction time. Everything else is internal.
