from datetime import datetime, timezone
from decimal import Decimal

from shyft_solana.parsers import (
    calculate_fees_from_gross,
    extract_supply_changes,
    parse_transactions_for_token,
    transactions_to_ohlcv,
)


def test_calculate_fees_from_gross_returns_fee_breakdown():
    fees = calculate_fees_from_gross(Decimal("1.0"), dex_name="Raydium V4")
    assert fees["sol_amount_gross"] == Decimal("1.0")
    assert fees["total_fee_bps"] == Decimal("25")
    assert fees["sol_amount_net"] == Decimal("0.9975")


def test_parse_transactions_for_token_extracts_action_trade():
    transactions = [
        {
            "signature": "sig1",
            "blockTime": 1773456000,
            "fee_payer": "wallet1",
            "actions": [
                {
                    "type": "BUY",
                    "info": {
                        "token_address": "mint1",
                        "token_amount": "100",
                        "sol_amount": "2",
                        "dex_name": "Raydium V4",
                        "pool_address": "pool1",
                    },
                }
            ],
        }
    ]

    trades, unparsed, pool = parse_transactions_for_token(transactions, token_address="mint1")

    assert len(trades) == 1
    assert unparsed == []
    assert pool == "pool1"
    assert trades[0].signature == "sig1"
    assert trades[0].token_amount == Decimal("100")


def test_transactions_to_ohlcv_bins_trades():
    transactions = [
        {
            "signature": "sig1",
            "blockTime": 1773456000,
            "actions": [
                {"type": "BUY", "info": {"token_address": "mint1", "token_amount": "100", "sol_amount": "1"}}
            ],
        },
        {
            "signature": "sig2",
            "blockTime": 1773456060,
            "actions": [
                {"type": "BUY", "info": {"token_address": "mint1", "token_amount": "50", "sol_amount": "1"}}
            ],
        },
    ]

    candles = transactions_to_ohlcv(transactions, token_address="mint1")

    assert len(candles) == 1
    assert candles[0]["volume"] == Decimal("150")
    assert candles[0]["trade_count"] == 2


def test_extract_supply_changes_filters_token_and_action():
    transactions = [
        {
            "signature": "sig1",
            "timestamp": datetime(2026, 3, 14, tzinfo=timezone.utc),
            "actions": [
                {"type": "BURN", "info": {"token_address": "mint1", "amount": "5"}},
                {"type": "TRANSFER", "info": {"token_address": "mint1", "amount": "10"}},
            ],
        }
    ]

    changes = extract_supply_changes(transactions, token_address="mint1")

    assert len(changes) == 1
    assert changes[0].change_type == "BURN"
    assert changes[0].token_amount == Decimal("5")
