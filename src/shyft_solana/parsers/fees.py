from __future__ import annotations

from decimal import Decimal


FEE_BPS_BY_DEX = {
    "pump.fun amm": Decimal("125"),
    "raydium v4": Decimal("25"),
    "raydium cpmm": Decimal("25"),
    "raydium cp swap": Decimal("25"),
    "jupiter v6": Decimal("25"),
}


def calculate_fees_from_gross(
    sol_amount_gross: Decimal | str | float,
    trade_type: str = "BUY",
    dex_name: str = "Raydium V4",
) -> dict[str, Decimal]:
    gross = Decimal(str(sol_amount_gross))
    fee_bps = _fee_bps(dex_name)
    fee_total = gross * fee_bps / Decimal("10000")
    return _fee_result(gross=gross, net=gross - fee_total, fee_total=fee_total, fee_bps=fee_bps)


def calculate_fees_from_net(
    sol_amount_net: Decimal | str | float,
    trade_type: str = "BUY",
    dex_name: str = "Raydium V4",
) -> dict[str, Decimal]:
    net = Decimal(str(sol_amount_net))
    fee_bps = _fee_bps(dex_name)
    gross = net / (Decimal("1") - fee_bps / Decimal("10000"))
    fee_total = gross - net
    return _fee_result(gross=gross, net=net, fee_total=fee_total, fee_bps=fee_bps)


def _fee_bps(dex_name: str) -> Decimal:
    return FEE_BPS_BY_DEX.get(dex_name.lower(), Decimal("25"))


def _fee_result(gross: Decimal, net: Decimal, fee_total: Decimal, fee_bps: Decimal) -> dict[str, Decimal]:
    # Split protocol/LP evenly as a practical default when provider payloads do not expose fee legs.
    lp_fee = fee_total / Decimal("2")
    protocol_fee = fee_total - lp_fee
    return {
        "sol_amount_gross": gross,
        "sol_amount_net": net,
        "lp_fee_sol": lp_fee,
        "protocol_fee_sol": protocol_fee,
        "creator_fee_sol": Decimal("0"),
        "lp_fee_bps": fee_bps / Decimal("2"),
        "protocol_fee_bps": fee_bps / Decimal("2"),
        "creator_fee_bps": Decimal("0"),
        "total_fee_sol": fee_total,
        "total_fee_bps": fee_bps,
    }
