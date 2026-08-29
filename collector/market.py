"""Validated direct-program sale decoder for Guild Saga Heroes.

This intentionally mirrors the final Live Market History query's three active
marketplace paths. The public pipeline does not trust a generic provider label
as the source of truth for buyer/seller/price.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Sequence

from .constants import (
    LAMPORTS_PER_SOL,
    MAGIC_EDEN_V2_PROGRAM,
    ME_EXECUTE_SALE_V2,
    ROYALTY_10_ADDRESS,
    ROYALTY_90_ADDRESS,
    SYSTEM_PROGRAM,
    TENSOR_AMM_BUY_NFT,
    TENSOR_AMM_BUY_SELL_EVENT,
    TENSOR_AMM_PROGRAM,
    TENSOR_MARKETPLACE_BUY_LEGACY,
    TENSOR_MARKETPLACE_PROGRAM,
    TENSOR_MARKETPLACE_TAKE_EVENT,
)


@dataclass(frozen=True)
class InstructionCall:
    signature: str
    block_time: datetime
    executing_account: str
    outer_instruction_index: int
    account_arguments: Sequence[str]
    data: bytes
    tx_success: bool = True


@dataclass(frozen=True)
class Sale:
    signature: str
    mint: str
    block_time: datetime
    marketplace: str
    marketplace_detail: str
    buyer: str
    seller: str
    gross_price_sol: float
    gross_price_method: str
    royalty_90_sol: float = 0.0
    royalty_10_sol: float = 0.0

    @property
    def royalty_total_sol(self) -> float:
        return self.royalty_90_sol + self.royalty_10_sol

    @property
    def royalty_paid(self) -> int:
        return int(self.royalty_total_sol > 0)


def _u64_le(data: bytes, one_based_start: int) -> int | None:
    start = one_based_start - 1
    end = start + 8
    if len(data) < end:
        return None
    return int.from_bytes(data[start:end], "little", signed=False)


def _u32_le(data: bytes, one_based_start: int) -> int | None:
    start = one_based_start - 1
    end = start + 4
    if len(data) < end:
        return None
    return int.from_bytes(data[start:end], "little", signed=False)


def _arg(args: Sequence[str], one_based_index: int) -> str | None:
    i = one_based_index - 1
    return args[i] if 0 <= i < len(args) else None


def _disc(call: InstructionCall) -> bytes:
    return call.data[:8]


def royalty_lamports(calls: Iterable[InstructionCall]) -> tuple[int, int]:
    r90 = 0
    r10 = 0
    for call in calls:
        if not call.tx_success or call.executing_account != SYSTEM_PROGRAM:
            continue
        if _u32_le(call.data, 1) != 2:
            continue
        recipient = _arg(call.account_arguments, 2)
        amount = _u64_le(call.data, 5)
        if amount is None:
            continue
        if recipient == ROYALTY_90_ADDRESS:
            r90 += amount
        elif recipient == ROYALTY_10_ADDRESS:
            r10 += amount
    return r90, r10


def decode_sale_group(calls: Sequence[InstructionCall]) -> Sale | None:
    """Decode calls sharing (signature, outer_instruction_index)."""
    calls = [c for c in calls if c.tx_success]
    if not calls:
        return None

    sig = calls[0].signature
    bt = calls[0].block_time

    # Magic Eden ExecuteSaleV2: everything is in one instruction.
    for c in calls:
        if c.executing_account == MAGIC_EDEN_V2_PROGRAM and _disc(c) == ME_EXECUTE_SALE_V2:
            mint = _arg(c.account_arguments, 5)
            buyer = _arg(c.account_arguments, 1)
            seller = _arg(c.account_arguments, 2)
            lamports = _u64_le(c.data, 11)
            if mint and buyer and seller and lamports is not None:
                r90, r10 = royalty_lamports(calls)
                return Sale(
                    signature=sig,
                    mint=mint,
                    block_time=bt,
                    marketplace="Magic Eden",
                    marketplace_detail="Magic Eden V2",
                    buyer=buyer,
                    seller=seller,
                    gross_price_sol=lamports / LAMPORTS_PER_SOL,
                    gross_price_method="magic_eden_execute_sale_v2_price",
                    royalty_90_sol=r90 / LAMPORTS_PER_SOL,
                    royalty_10_sol=r10 / LAMPORTS_PER_SOL,
                )

    # Tensor Marketplace requires BuyLegacy + TakeEvent in the same group.
    tm_buy = next((c for c in calls if c.executing_account == TENSOR_MARKETPLACE_PROGRAM and _disc(c) == TENSOR_MARKETPLACE_BUY_LEGACY), None)
    tm_event = next((c for c in calls if c.executing_account == TENSOR_MARKETPLACE_PROGRAM and _disc(c) == TENSOR_MARKETPLACE_TAKE_EVENT), None)
    if tm_buy and tm_event:
        mint = _arg(tm_buy.account_arguments, 6)
        buyer = _arg(tm_buy.account_arguments, 2)
        seller = _arg(tm_buy.account_arguments, 7)
        lamports = _u64_le(tm_event.data, 78)
        if mint and buyer and seller and lamports is not None:
            r90, r10 = royalty_lamports(calls)
            return Sale(
                signature=sig,
                mint=mint,
                block_time=bt,
                marketplace="Tensor",
                marketplace_detail="Tensor Marketplace",
                buyer=buyer,
                seller=seller,
                gross_price_sol=lamports / LAMPORTS_PER_SOL,
                gross_price_method="tensor_tcomp_take_event_amount",
                royalty_90_sol=r90 / LAMPORTS_PER_SOL,
                royalty_10_sol=r10 / LAMPORTS_PER_SOL,
            )

    # Tensor AMM requires BuyNft + BuySellEvent in the same group.
    ta_buy = next((c for c in calls if c.executing_account == TENSOR_AMM_PROGRAM and _disc(c) == TENSOR_AMM_BUY_NFT), None)
    ta_event = next((c for c in calls if c.executing_account == TENSOR_AMM_PROGRAM and _disc(c) == TENSOR_AMM_BUY_SELL_EVENT), None)
    if ta_buy and ta_event:
        mint = _arg(ta_buy.account_arguments, 15)
        buyer = _arg(ta_buy.account_arguments, 2)
        seller = _arg(ta_buy.account_arguments, 8)
        lamports = _u64_le(ta_event.data, 10)
        if mint and buyer and seller and lamports is not None:
            r90, r10 = royalty_lamports(calls)
            return Sale(
                signature=sig,
                mint=mint,
                block_time=bt,
                marketplace="Tensor",
                marketplace_detail="Tensor AMM",
                buyer=buyer,
                seller=seller,
                gross_price_sol=lamports / LAMPORTS_PER_SOL,
                gross_price_method="tensor_amm_buy_sell_event_current_price",
                royalty_90_sol=r90 / LAMPORTS_PER_SOL,
                royalty_10_sol=r10 / LAMPORTS_PER_SOL,
            )

    return None


def decode_sales(calls: Iterable[InstructionCall], guild_mints: set[str]) -> list[Sale]:
    groups: dict[tuple[str, int], list[InstructionCall]] = {}
    tx_calls: dict[str, list[InstructionCall]] = {}
    for call in calls:
        groups.setdefault((call.signature, call.outer_instruction_index), []).append(call)
        tx_calls.setdefault(call.signature, []).append(call)

    sales: list[Sale] = []
    seen: set[tuple[str, str]] = set()
    for (sig, _), group in groups.items():
        sale = decode_sale_group(group)
        if not sale or sale.mint not in guild_mints:
            continue
        # Royalties are transaction-wide in the original query, not merely the
        # marketplace outer group. Attach the transaction aggregate here.
        r90, r10 = royalty_lamports(tx_calls[sig])
        sale = Sale(
            **{**sale.__dict__,
               "royalty_90_sol": r90 / LAMPORTS_PER_SOL,
               "royalty_10_sol": r10 / LAMPORTS_PER_SOL}
        )
        key = (sale.signature, sale.mint)
        if key not in seen:
            seen.add(key)
            sales.append(sale)
    sales.sort(key=lambda s: (s.block_time, s.signature, s.mint))
    return sales
