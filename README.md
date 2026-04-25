# shyft-solana

A Python client for the [Shyft API](https://shyft.to/) on Solana. Fetch transactions, parse trades, build OHLCV candles, and query RPC — with built-in batching, async support, and multi-key rotation.

Works on the **free tier**. No paid plan required.

## Repository Status

This public repository currently contains the project documentation and design notes for the client. Package source and release automation should be added before publishing or advertising the `pip install` path.

Related notes:

- [API_EXPLORATION.md](API_EXPLORATION.md)
- [KEY_POOL_DESIGN.md](KEY_POOL_DESIGN.md)

## Features

- **REST + RPC** in one client — transaction history, token supply, signature lookups
- **JSON-RPC batching** — up to 300 RPC calls in a single request (~300x faster than sequential)
- **Sync and async** — same API, powered by `httpx`
- **Multi-key rotation** — use multiple API keys to multiply throughput
- **Trade parser** — 4-layer detection pipeline for Solana DEX trades (Raydium, Pump.fun, Jupiter)
- **OHLCV builder** — turn raw transactions into candlestick data
- **Fee calculator** — accurate fee breakdowns for major Solana DEXes
- **Zero dependencies beyond `httpx`** — no Django, no frameworks, just `httpx` and stdlib

## Installation

> The package install path below is the intended public interface. Add the package source and PyPI release before treating it as generally available.

```bash
pip install shyft-solana
```

Or install from source:

```bash
git clone https://github.com/BeckVision/shyft-solana.git
cd shyft-solana
pip install -e .
```

## Quick Start

Get a free API key at [dashboard.shyft.to](https://dashboard.shyft.to/).

```python
from shyft_solana import ShyftClient

client = ShyftClient(api_key="your-api-key")

# Fetch transaction history for a token
response = client.get_transaction_history("TokenAddress...", tx_num=50)

# Get token supply
supply = client.get_token_supply("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")

# Count recent swaps for a pool
count = client.count_recent_swaps("PoolAddress...", since=datetime(2026, 3, 1))
```

## Usage

### Transaction History (REST)

```python
from shyft_solana import ShyftClient

client = ShyftClient(api_key="your-key")

# Single page (up to 100 transactions)
response = client.get_transaction_history("TokenAddress...", tx_num=100)
transactions = response.get("result", [])

# Paginated — automatically fetches multiple pages
for tx in client.get_transaction_history_paginated("TokenAddress...", total_txns=500):
    print(tx["signatures"][0], tx["type"])

# Fetch all at once as a list
all_txs = client.fetch_all_token_transactions("TokenAddress...", max_txns=500)
```

### RPC Methods

```python
# Token supply
supply = client.get_token_supply("MintAddress...")

# Recent transaction signatures for a pool
count = client.count_recent_swaps("PoolAddress...", since=some_datetime)
```

### Batch RPC

Send up to 300 RPC calls in a single HTTP request. One round-trip instead of 300.

```python
# Batch token supply for multiple mints
mints = ["mint1...", "mint2...", "mint3...", "mint4..."]
supplies = client.batch_token_supply(mints)
# Returns: {"mint1...": 1000000, "mint2...": 500000, ...}

# Batch signature counts for multiple pools
from datetime import datetime, timezone
since = datetime(2026, 3, 14, tzinfo=timezone.utc)

pools = ["pool1...", "pool2...", "pool3..."]
counts = client.batch_recent_swaps(pools, since=since)
# Returns: {"pool1...": 42, "pool2...": 17, ...}
```

### Async

Every method has an async counterpart. Use `AsyncShyftClient` instead of `ShyftClient`:

```python
import asyncio
from shyft_solana import AsyncShyftClient

async def main():
    async with AsyncShyftClient(api_key="your-key") as client:
        # Same methods, just await them
        response = await client.get_transaction_history("TokenAddress...", tx_num=50)
        supply = await client.get_token_supply("MintAddress...")
        supplies = await client.batch_token_supply(["mint1...", "mint2...", "mint3..."])

asyncio.run(main())
```

### Multi-Key Rotation

Use multiple API keys to multiply your throughput. Each key gets its own rate limit bucket.

```python
# Pass multiple keys — the client rotates automatically
client = ShyftClient(api_keys=["key1", "key2", "key3", "key4"])

# Everything works the same — the client picks the best key for each request
response = client.get_transaction_history("TokenAddress...", tx_num=100)
```

**What you get with multiple keys (free tier):**

| Keys | REST Concurrent | RPC Batch Concurrent |
|------|----------------|---------------------|
| 1 | ~2 req/burst | 300 calls/request |
| 2 | ~4 req/burst | 600 calls (2 parallel batches) |
| 4 | ~8 req/burst | 1,200 calls (4 parallel batches) |

Multi-key shines with async, where requests actually run concurrently:

```python
async with AsyncShyftClient(api_keys=["k1", "k2", "k3", "k4"]) as client:
    # 4 REST requests in parallel, each using a different key
    results = await asyncio.gather(
        client.get_transaction_history("token1..."),
        client.get_transaction_history("token2..."),
        client.get_transaction_history("token3..."),
        client.get_transaction_history("token4..."),
    )
```

You can also configure keys via environment variables:

```bash
# Single key
export SHYFT_API_KEY=your-key

# Multiple keys (comma-separated)
export SHYFT_API_KEYS=key1,key2,key3,key4
```

```python
# Auto-discovers from environment
client = ShyftClient()
```

### Parsing Trades

Parse raw Shyft transaction data into structured trade objects. Supports Raydium V4, Raydium CPMM, Pump.fun AMM, and Jupiter V6.

```python
from shyft_solana import ShyftClient
from shyft_solana.parsers import parse_transactions_for_token

client = ShyftClient(api_key="your-key")

# Fetch raw transactions
response = client.get_transaction_history("TokenAddress...", tx_num=100)
transactions = response.get("result", [])

# Parse into structured trades
trades, unparsed, detected_pool = parse_transactions_for_token(
    transactions,
    token_address="TokenAddress...",
    pool_address="PoolAddress...",  # optional — auto-detected if omitted
)

for trade in trades:
    print(f"{trade.trade_type} {trade.token_amount} tokens @ {trade.price_per_token_sol} SOL")
    print(f"  Wallet: {trade.wallet_address}")
    print(f"  DEX: {trade.dex_name}")
    print(f"  Net SOL: {trade.sol_amount_net}")
    print(f"  Fees: LP={trade.lp_fee_sol} Protocol={trade.protocol_fee_sol}")
```

Each `ExtractedTrade` contains:

| Field | Type | Description |
|-------|------|-------------|
| `signature` | str | Transaction signature |
| `timestamp` | datetime | Block time |
| `wallet_address` | str | Trader's wallet |
| `trade_type` | str | BUY, SELL, TRANSFER_IN, TRANSFER_OUT |
| `token_amount` | Decimal | Tokens traded |
| `price_per_token_sol` | Decimal | Price in SOL |
| `sol_amount_gross` | Decimal | SOL before fees |
| `sol_amount_net` | Decimal | SOL after fees |
| `dex_name` | str | Raydium V4, Pump.fun AMM, etc. |
| `pool_address` | str | Liquidity pool address |
| `lp_fee_sol` | Decimal | LP fee |
| `protocol_fee_sol` | Decimal | Protocol fee |
| `creator_fee_sol` | Decimal | Creator fee |
| `detection_layer` | str | Which parser layer found this trade |

### Building OHLCV Candles

Turn parsed trades into candlestick data:

```python
from shyft_solana.parsers.ohlcv import transactions_to_ohlcv

# From raw transactions (fetches + parses + bins in one call)
candles = transactions_to_ohlcv(transactions, token_address, pool_address, since=some_datetime)

for candle in candles:
    print(f"{candle['timestamp']} O={candle['open_price']:.8f} H={candle['high_price']:.8f} "
          f"L={candle['low_price']:.8f} C={candle['close_price']:.8f} V={candle['volume']:.4f}")
```

Candles are 5-minute intervals, SOL-denominated, sorted oldest-first.

### Fee Calculation

Calculate accurate fee breakdowns for Solana DEX trades:

```python
from decimal import Decimal
from shyft_solana.parsers.fees import calculate_fees_from_gross, calculate_fees_from_net

# From gross SOL amount (what the pool received)
fees = calculate_fees_from_gross(Decimal("1.5"), trade_type="BUY", dex_name="Pump.fun AMM")
print(f"Net: {fees['sol_amount_net']} SOL")
print(f"LP fee: {fees['lp_fee_sol']} SOL ({fees['lp_fee_bps']} bps)")
print(f"Protocol fee: {fees['protocol_fee_sol']} SOL ({fees['protocol_fee_bps']} bps)")

# Supported DEXes
# - Pump.fun AMM (SELL: 1.25%, BUY: 1.25%)
# - Raydium V4 (0.25% both ways)
# - Raydium CPMM (0.25% both ways)
# - Raydium CP Swap (0.25% both ways)
# - Jupiter V6 (0.25% both ways)
```

### Supply Changes

Track token burns and mints:

```python
from shyft_solana.parsers import extract_supply_changes

changes = extract_supply_changes(transactions, token_address="TokenAddress...")

for change in changes:
    print(f"{change.change_type} {change.token_amount} tokens at {change.timestamp}")
```

## Configuration

### Constructor Options

```python
ShyftClient(
    api_key="...",        # Single API key
    api_keys=["..."],     # Multiple API keys (overrides api_key)
    timeout=15,           # Request timeout in seconds (default: 15)
    max_retries=3,        # Max retry attempts on failure (default: 3)
)
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `SHYFT_API_KEY` | Single API key |
| `SHYFT_API_KEYS` | Multiple keys, comma-separated |

If both are set, `SHYFT_API_KEYS` takes priority. `SHYFT_API_KEY` is appended if it's not already in the list.

## Free Tier Limits

Shyft's free plan ($0/month) provides:

| | Limit | With Batching | With 4 Keys |
|---|-------|--------------|-------------|
| **REST** | 1 req/sec (~2 concurrent) | N/A | ~8 concurrent |
| **RPC** | 10 req/sec | 300 calls/request | 1,200 calls/request |
| **Credits** | Unlimited | Unlimited | Unlimited |

No daily caps. No credit costs. Batching is the biggest free speedup — 300 RPC calls in one request costs the same as one call.

## How the Trade Parser Works

The parser uses a 4-layer detection strategy, trying each layer in order until a trade is found:

1. **Action-based** (most reliable) — looks for explicit BUY/SELL actions in the transaction
2. **Swap action** — parses swap-type actions with token transfers
3. **Pool balance analysis** — detects trades from pool balance changes (pool receives tokens = SELL)
4. **Balance change fallback** — infers from the user's token balance changes

Each trade is tagged with its `detection_layer` so you know how it was found.

## Error Handling

All methods return `None` on failure with details in `client.last_error`:

```python
result = client.get_token_supply("InvalidMint...")

if result is None:
    print(f"Failed: {client.last_error}")
    # {'status_code': 200, 'message': 'RPC error: ...', 'endpoint': 'rpc/getTokenSupply'}
```

The client automatically retries on:
- **429** (rate limit) — respects Retry-After header, exponential backoff
- **5xx** (server error) — up to `max_retries` attempts
- **Network errors** — connection timeouts, DNS failures

## Testing

Planned package test entrypoint:

```bash
pytest
```

## License

MIT. See [LICENSE](LICENSE).
