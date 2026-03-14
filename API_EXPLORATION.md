# Shyft API Exploration

Tested 2026-03-14 on the **free tier** (`$0/month`).

---

## Free Tier Official Limits

Source: [Shyft Pricing](https://www.shyft.to/solana-rpc-grpc-pricing)

| | Free | Build ($199) | Grow ($349) | Accelerate ($649) |
|---|------|-------------|-------------|-------------------|
| RPC req/sec | 10 | 100 | 150 | 400 |
| REST API req/sec | 1 | 10 | 30 | 100 |
| Credits | Unlimited | Unlimited | Unlimited | Unlimited |
| gRPC | No | Yes | Yes | Yes |

No daily credit cap on any tier. "Unlimited credits" means no per-request cost.

---

## RPC Endpoint (`rpc.shyft.to`)

### Single Requests

| Metric | Value |
|--------|-------|
| Avg latency | ~800-1000ms |
| Sequential throughput | ~1.2 req/sec (network-bound, not rate-limited) |
| 429 rate limiting | Not observed at sequential speeds |

### Batch Requests (JSON-RPC array)

Shyft RPC **supports standard JSON-RPC batching**. Send an array of requests, get an array of responses in one HTTP round-trip.

| Batch Size | Status | Wall Time | Per-Call Effective |
|------------|--------|-----------|-------------------|
| 1 | OK | 984ms | 984ms |
| 3 | OK | 710ms | 237ms |
| 10 | OK | 1,027ms | 103ms |
| 20 | OK | 949ms | 47ms |
| 50 | OK | 769ms | 15ms |
| 100 | OK | 808ms | 8ms |
| 200 | OK | 740ms | 4ms |
| 300 | OK | 949ms | 3ms |
| 400 | OK | 997ms | 2ms |
| 430 | OK | 1,062ms | 2ms |
| 440 | **413** | - | Body too large |
| 500 | **413** | - | Body too large |

**Body size limit: ~50KB** (430 x getTokenSupply = 49.9KB works, 440 = 51KB rejected).

The limit is request body size, not item count. Methods with longer params (like full Solana addresses) will hit the limit at fewer items.

### Mixed-Method Batching

Different RPC methods work in the same batch:

```
Batch of 5 (3x getTokenSupply + 2x getSignaturesForAddress):
  Status: 200  Time: 886ms  All 5 returned successfully
```

### Batch vs Sequential Comparison (20 calls)

| Strategy | Wall Time | Speedup |
|----------|-----------|---------|
| Sequential | ~19,400ms | 1x |
| Batch | 949ms | **~20x** |

### Rapid-Fire Batches (Rate Limit Test)

15 batch requests sent back-to-back with no delay:

```
All 15 returned HTTP 200, no 429s observed.
Times: 670-1126ms per batch request.
```

The 10 req/sec limit appears to be enforced on concurrent requests, not sequential ones (sequential requests are naturally throttled by network latency).

---

## Async Concurrent RPC

Tested with `httpx.AsyncClient` using `asyncio.gather`:

| Concurrent Requests | OK | 429 | Wall Time |
|---------------------|-----|-----|-----------|
| 10 | 10 | 0 | 993ms |
| 12 | 11 | 1 | 1,192ms |
| 15 | 11 | 4 | 1,103ms |
| 20 | 11 | 9 | 936ms |

**Safe concurrency limit: 10 concurrent RPC requests.** After 10-11, rate limiting kicks in.

### Batch vs Async Concurrent (20 calls each)

| Strategy | OK | Wall Time |
|----------|-----|-----------|
| Batch (1 request, 20 methods) | 20/20 | 423ms |
| Async concurrent (2 rounds of 10) | 11/20 | 554ms |

**Batch wins on every metric.** More reliable (no 429s), faster, and simpler.

---

## REST API (`api.shyft.to`)

### Sequential Requests

| Metric | Value |
|--------|-------|
| Avg latency | ~1,000-1,500ms |
| No rate limiting observed | up to 10 sequential (network-bound) |

### Concurrent Requests

| Concurrent | OK | 429 | Wall Time |
|------------|-----|-----|-----------|
| 5 | 2 | 3 | 2,334ms |
| 10 | 2 | 8 | 1,452ms |

**REST is strictly rate-limited on concurrency.** Only ~1-2 concurrent requests succeed. The advertised "1 req/sec" is enforced.

### REST Has No Batch Support

REST endpoints (`/transaction/history`, etc.) are standard GET requests. No batching mechanism exists.

---

## Speed Strategy Rankings

### For RPC calls (getTokenSupply, getSignaturesForAddress)

| Rank | Strategy | Effective Throughput | Complexity |
|------|----------|---------------------|------------|
| 1 | **JSON-RPC Batch** | ~400 calls/sec (batch of 400 in ~1s) | Low |
| 2 | Async concurrent (10) | ~10 calls/sec | Medium |
| 3 | Sequential | ~1 call/sec | None |

### For REST calls (/transaction/history)

| Rank | Strategy | Effective Throughput | Complexity |
|------|----------|---------------------|------------|
| 1 | **Pipeline** (parse while fetching next) | ~1 call/sec + overlap | Low |
| 2 | Sequential with rate limiter | ~1 call/sec | None |
| 3 | **Multi-key concurrent** | ~2 req/sec per key | Medium |

---

## Multi-Key Rate Limit Test

Tested with 2 free-tier API keys to confirm rate limits are **per-key, not per-IP**.

### REST — Per-Key Confirmation

| Test | Keys | OK / Total | Notes |
|------|------|------------|-------|
| 5 concurrent, 1 key | 1 | 2/5 | Baseline |
| 6 concurrent, 2 keys (alternating) | 2 | 4/6 | ~2x improvement |
| 10 concurrent, 2 keys (5 each) | 2 | 4/10 | ~2 OK per key |

**Each key independently allows ~2 concurrent REST requests.** Rate limiting is per API key.

### Projected Multi-Key REST Throughput

| Keys | Concurrent REST OK | Effective Burst |
|------|-------------------|-----------------|
| 1 | ~2 | ~2 req/burst |
| 2 | ~4 | ~4 req/burst |
| 4 | ~8 | ~8 req/burst |
| 6 | ~12 | ~12 req/burst |

### RPC — Multi-Key with Batching

Each key can independently batch up to ~300 RPC calls. With async, multiple keys can fire batches concurrently:

| Keys | Strategy | Effective RPC calls/sec |
|------|----------|------------------------|
| 1 | Single batch of 300 | ~300 |
| 4 | 4 concurrent batches of 300 | ~1,200 |
| 6 | 6 concurrent batches of 300 | ~1,800 |

---

## Key Findings

1. **Batching is the killer feature.** 20x speedup for RPC with zero complexity.
2. **The 10 req/sec RPC limit is per-concurrent-request, not per-second.** Sequential requests aren't throttled because network latency naturally spaces them out.
3. **REST is per-key limited.** ~2 concurrent per key. Multi-key is the only way to scale REST.
4. **Body size is the batch ceiling.** ~50KB max request body, not a fixed item count.
5. **No daily credit limits.** "Unlimited credits" on free tier means volume is uncapped.
6. **httpx works perfectly** for both sync and async Shyft access.

---

## Practical Batch Sizes by Method

Estimated based on ~50KB body limit:

| Method | Param Size | Est. Max Batch | Realistic Safe |
|--------|-----------|----------------|----------------|
| getTokenSupply | ~119 bytes/item | ~430 | 300 |
| getSignaturesForAddress (limit:100) | ~143 bytes/item | ~350 | 250 |
| getSignaturesForAddress (limit:5) | ~139 bytes/item | ~360 | 250 |
| Mixed methods | varies | ~300 | 200 |

---

## Test Environment

- Location: Home network (non-US)
- Latency to Shyft: ~800-1000ms base RTT
- Tool: `httpx` (async) and `requests` (sync)
- Date: 2026-03-14
- Plan: Free ($0/month)
