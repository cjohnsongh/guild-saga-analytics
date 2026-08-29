"""Normalize raw Solana `getTransaction` JSON for Guild Saga collectors."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .constants import TOKEN_2022_PROGRAM, TOKEN_PROGRAM
from .market import InstructionCall

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_MAP = {c: i for i, c in enumerate(_B58_ALPHABET)}


def b58decode(value: str) -> bytes:
    n = 0
    for ch in value:
        try:
            digit = _B58_MAP[ch]
        except KeyError as exc:
            raise ValueError(f"invalid base58 character {ch!r}") from exc
        n = n * 58 + digit
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    leading = len(value) - len(value.lstrip("1"))
    return b"\x00" * leading + raw


def _account_key_strings(message: dict[str, Any], meta: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for item in message.get("accountKeys") or []:
        if isinstance(item, str):
            keys.append(item)
        elif isinstance(item, dict):
            keys.append(str(item.get("pubkey") or ""))
        else:
            keys.append(str(item))
    loaded = meta.get("loadedAddresses") or {}
    keys.extend(str(x) for x in (loaded.get("writable") or []))
    keys.extend(str(x) for x in (loaded.get("readonly") or []))
    return keys


def transaction_account_keys(tx: dict[str, Any]) -> list[str]:
    message = ((tx.get("transaction") or {}).get("message") or {})
    meta = tx.get("meta") or {}
    return _account_key_strings(message, meta)


def transaction_signers(tx: dict[str, Any]) -> list[str]:
    """Return transaction signers in message order.

    `encoding=json` commonly returns pubkey strings, so signer status comes from
    the message header. Parsed/dict account-key shapes are also supported.
    """
    message = ((tx.get("transaction") or {}).get("message") or {})
    raw_keys = message.get("accountKeys") or []
    signers: list[str] = []
    for item in raw_keys:
        if isinstance(item, dict) and item.get("signer") and item.get("pubkey"):
            signers.append(str(item["pubkey"]))
    if signers:
        return signers

    static_keys = []
    for item in raw_keys:
        if isinstance(item, str):
            static_keys.append(item)
        elif isinstance(item, dict):
            static_keys.append(str(item.get("pubkey") or ""))
        else:
            static_keys.append(str(item))
    n = int((message.get("header") or {}).get("numRequiredSignatures") or 0)
    return static_keys[:n]


def _compiled_call(
    inst: dict[str, Any], *, signature: str, block_time: datetime,
    outer_index: int, keys: list[str], tx_success: bool,
) -> InstructionCall | None:
    if "programId" in inst:
        program = str(inst["programId"])
    else:
        pi = inst.get("programIdIndex")
        if not isinstance(pi, int) or not (0 <= pi < len(keys)):
            return None
        program = keys[pi]

    args: list[str] = []
    for a in inst.get("accounts") or []:
        if isinstance(a, int):
            if 0 <= a < len(keys):
                args.append(keys[a])
        else:
            args.append(str(a))

    data_text = inst.get("data")
    if not isinstance(data_text, str):
        return None

    return InstructionCall(
        signature=signature,
        block_time=block_time,
        executing_account=program,
        outer_instruction_index=outer_index,
        account_arguments=args,
        data=b58decode(data_text),
        tx_success=tx_success,
    )


def normalize_transaction(tx: dict[str, Any]) -> list[InstructionCall]:
    transaction = tx.get("transaction") or {}
    message = transaction.get("message") or {}
    meta = tx.get("meta") or {}
    signatures = transaction.get("signatures") or []
    if not signatures:
        raise ValueError("transaction has no signature")
    signature = str(signatures[0])
    bt = tx.get("blockTime")
    if bt is None:
        raise ValueError(f"{signature}: transaction has no blockTime")
    block_time = datetime.fromtimestamp(int(bt), timezone.utc)
    tx_success = meta.get("err") is None
    keys = _account_key_strings(message, meta)

    calls: list[InstructionCall] = []
    for outer_index, inst in enumerate(message.get("instructions") or []):
        if isinstance(inst, dict):
            call = _compiled_call(inst, signature=signature, block_time=block_time,
                                  outer_index=outer_index, keys=keys, tx_success=tx_success)
            if call:
                calls.append(call)

    for group in meta.get("innerInstructions") or []:
        outer_index = group.get("index")
        if not isinstance(outer_index, int):
            continue
        for inst in group.get("instructions") or []:
            if isinstance(inst, dict):
                call = _compiled_call(inst, signature=signature, block_time=block_time,
                                      outer_index=outer_index, keys=keys, tx_success=tx_success)
                if call:
                    calls.append(call)
    return calls


@dataclass(frozen=True)
class TokenMovement:
    signature: str
    mint: str
    block_time: datetime
    classification: str  # TRANSFER | BURN_OR_SEND_TO_ZERO | MINT_OR_RECEIVE_FROM_ZERO
    from_owners: tuple[str, ...]
    to_owners: tuple[str, ...]

    @property
    def from_owner(self) -> str | None:
        return self.from_owners[0] if len(self.from_owners) == 1 else None

    @property
    def to_owner(self) -> str | None:
        return self.to_owners[0] if len(self.to_owners) == 1 else None


def _token_amount(row: dict[str, Any]) -> int:
    try:
        return int(((row.get("uiTokenAmount") or {}).get("amount")) or "0")
    except Exception:
        return 0


def _balances_for_mints(
    rows: list[dict[str, Any]] | None,
    keys: list[str],
    known_mints: set[str],
) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows or []:
        mint = str(row.get("mint") or "")
        if mint not in known_mints:
            continue
        idx = row.get("accountIndex")
        if not isinstance(idx, int) or not (0 <= idx < len(keys)):
            continue
        account = keys[idx]
        out.setdefault(mint, {})[account] = {
            "owner": row.get("owner"),
            "amount": _token_amount(row),
        }
    return out


def normalize_token_movements(tx: dict[str, Any], known_mints: set[str]) -> list[TokenMovement]:
    """Derive NFT ownership movement from pre/post token balances.

    This is the same evidence model used in the paid-for historical transfer
    crawl. A sender-only movement is deliberately called BURN_OR_SEND_TO_ZERO;
    callers must additionally verify an SPL Burn/BurnChecked instruction before
    publishing it as a burn.
    """
    transaction = tx.get("transaction") or {}
    signatures = transaction.get("signatures") or []
    if not signatures:
        raise ValueError("transaction has no signature")
    signature = str(signatures[0])
    bt = tx.get("blockTime")
    if bt is None:
        raise ValueError(f"{signature}: transaction has no blockTime")
    block_time = datetime.fromtimestamp(int(bt), timezone.utc)
    meta = tx.get("meta") or {}
    keys = transaction_account_keys(tx)

    pre = _balances_for_mints(meta.get("preTokenBalances"), keys, known_mints)
    post = _balances_for_mints(meta.get("postTokenBalances"), keys, known_mints)
    movements: list[TokenMovement] = []

    for mint in sorted(set(pre) | set(post)):
        pre_m = pre.get(mint, {})
        post_m = post.get(mint, {})
        changes = []
        for account in sorted(set(pre_m) | set(post_m)):
            a = pre_m.get(account, {})
            b = post_m.get(account, {})
            before = int(a.get("amount", 0))
            after = int(b.get("amount", 0))
            if before == after:
                continue
            changes.append({
                "delta": after - before,
                "owner_before": a.get("owner"),
                "owner_after": b.get("owner"),
            })

        send = [c for c in changes if c["delta"] < 0]
        recv = [c for c in changes if c["delta"] > 0]
        if send and recv:
            cls = "TRANSFER"
        elif send:
            cls = "BURN_OR_SEND_TO_ZERO"
        elif recv:
            cls = "MINT_OR_RECEIVE_FROM_ZERO"
        else:
            continue

        from_owners = tuple(sorted({str(c["owner_before"]) for c in send if c.get("owner_before")}))
        to_owners = tuple(sorted({str(c["owner_after"]) for c in recv if c.get("owner_after")}))
        movements.append(TokenMovement(
            signature=signature,
            mint=mint,
            block_time=block_time,
            classification=cls,
            from_owners=from_owners,
            to_owners=to_owners,
        ))

    return movements


def transaction_has_burn_instruction(tx: dict[str, Any], mint: str) -> bool:
    """Return True for an explicit SPL Token Burn or BurnChecked of `mint`."""
    for call in normalize_transaction(tx):
        if call.executing_account not in {TOKEN_PROGRAM, TOKEN_2022_PROGRAM}:
            continue
        if not call.data or call.data[0] not in {8, 15}:  # Burn / BurnChecked
            continue
        # Both instructions use accounts: source token account, mint, authority...
        if len(call.account_arguments) >= 2 and call.account_arguments[1] == mint:
            return True
    return False
