from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .trades import parse_transactions_for_token


def transactions_to_ohlcv(
    transactions: list[dict[str, Any]],
    token_address: str,
    pool_address: str | None = None,
    since: datetime | None = None,
    interval_minutes: int = 5,
) -> list[dict[str, Any]]:
    trades, _, _ = parse_transactions_for_token(transactions, token_address, pool_address)
    if since:
        trades = [trade for trade in trades if trade.timestamp >= since]
    buckets: dict[datetime, list] = {}
    for trade in trades:
        bucket = _floor_time(trade.timestamp, interval_minutes)
        buckets.setdefault(bucket, []).append(trade)

    candles: list[dict[str, Any]] = []
    for timestamp in sorted(buckets):
        rows = sorted(buckets[timestamp], key=lambda trade: trade.timestamp)
        prices = [row.price_per_token_sol for row in rows]
        candles.append(
            {
                "timestamp": timestamp,
                "open_price": prices[0],
                "high_price": max(prices),
                "low_price": min(prices),
                "close_price": prices[-1],
                "volume": sum((row.token_amount for row in rows), Decimal("0")),
                "trade_count": len(rows),
            }
        )
    return candles


def _floor_time(value: datetime, interval_minutes: int) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    discard = timedelta(
        minutes=value.minute % interval_minutes,
        seconds=value.second,
        microseconds=value.microsecond,
    )
    return value - discard
