from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .fees import calculate_fees_from_gross


@dataclass
class ExtractedTrade:
    signature: str
    timestamp: datetime
    wallet_address: str | None
    trade_type: str
    token_amount: Decimal
    price_per_token_sol: Decimal
    sol_amount_gross: Decimal
    sol_amount_net: Decimal
    dex_name: str
    pool_address: str | None
    lp_fee_sol: Decimal
    protocol_fee_sol: Decimal
    creator_fee_sol: Decimal
    detection_layer: str


@dataclass
class SupplyChange:
    signature: str
    timestamp: datetime
    change_type: str
    token_amount: Decimal


def parse_transactions_for_token(
    transactions: list[dict[str, Any]],
    token_address: str,
    pool_address: str | None = None,
) -> tuple[list[ExtractedTrade], list[dict[str, Any]], str | None]:
    trades: list[ExtractedTrade] = []
    unparsed: list[dict[str, Any]] = []
    detected_pool = pool_address

    for transaction in transactions:
        trade = _parse_action_trade(transaction, token_address, pool_address)
        if trade is None:
            unparsed.append(transaction)
            continue
        if not detected_pool and trade.pool_address:
            detected_pool = trade.pool_address
        trades.append(trade)

    return trades, unparsed, detected_pool


def extract_supply_changes(
    transactions: list[dict[str, Any]],
    token_address: str,
) -> list[SupplyChange]:
    changes: list[SupplyChange] = []
    for transaction in transactions:
        for action in transaction.get("actions", []) or []:
            info = action.get("info", {}) or {}
            mint = info.get("token_address") or info.get("mint") or info.get("token")
            if mint != token_address:
                continue
            action_type = str(action.get("type") or action.get("action_type") or "").upper()
            if action_type not in {"BURN", "MINT"}:
                continue
            amount = _decimal(info.get("amount") or info.get("token_amount") or 0)
            changes.append(
                SupplyChange(
                    signature=_signature(transaction),
                    timestamp=_timestamp(transaction),
                    change_type=action_type,
                    token_amount=amount,
                )
            )
    return changes


def _parse_action_trade(
    transaction: dict[str, Any],
    token_address: str,
    pool_address: str | None,
) -> ExtractedTrade | None:
    for action in transaction.get("actions", []) or []:
        action_type = str(action.get("type") or action.get("action_type") or "").upper()
        if action_type not in {"BUY", "SELL", "SWAP"}:
            continue
        info = action.get("info", {}) or {}
        token = info.get("token_address") or info.get("token") or info.get("mint")
        if token and token != token_address:
            continue
        token_amount = _decimal(info.get("token_amount") or info.get("amount") or 0)
        sol_amount = _decimal(info.get("sol_amount") or info.get("native_amount") or 0)
        if token_amount <= 0 or sol_amount <= 0:
            continue
        dex_name = info.get("dex") or info.get("dex_name") or transaction.get("protocol", "Unknown")
        fees = calculate_fees_from_gross(sol_amount, trade_type=action_type, dex_name=dex_name)
        return ExtractedTrade(
            signature=_signature(transaction),
            timestamp=_timestamp(transaction),
            wallet_address=info.get("wallet") or transaction.get("fee_payer"),
            trade_type="BUY" if action_type == "SWAP" else action_type,
            token_amount=token_amount,
            price_per_token_sol=fees["sol_amount_net"] / token_amount,
            sol_amount_gross=fees["sol_amount_gross"],
            sol_amount_net=fees["sol_amount_net"],
            dex_name=dex_name,
            pool_address=pool_address or info.get("pool_address"),
            lp_fee_sol=fees["lp_fee_sol"],
            protocol_fee_sol=fees["protocol_fee_sol"],
            creator_fee_sol=fees["creator_fee_sol"],
            detection_layer="action",
        )
    return None


def _signature(transaction: dict[str, Any]) -> str:
    signatures = transaction.get("signatures")
    if isinstance(signatures, list) and signatures:
        return str(signatures[0])
    return str(transaction.get("signature", ""))


def _timestamp(transaction: dict[str, Any]) -> datetime:
    value = transaction.get("timestamp") or transaction.get("blockTime") or transaction.get("block_time")
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            pass
    return datetime.fromtimestamp(0, tz=timezone.utc)


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value or 0))
