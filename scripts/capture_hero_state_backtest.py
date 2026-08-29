#!/usr/bin/env python3
"""Backtest independent Guild Saga Hero-state rules against real Solana txs.

This is a migration gate, not the production collector. It samples frozen,
known-good state from the repository and proves the raw-chain parser can recover:
- World Mode STAKE custody semantics
- World Mode UNSTAKE custody semantics
- exact 9-byte QUEST_RESTART calls
- explicit SPL burns
- ordinary non-World ownership transfers

Public raw transaction JSON is cached as test evidence. API keys are never
written to disk.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ASSETS = ROOT / "data" / "baseline" / "assets.csv"
DELTAS = ROOT / "data" / "state" / "hero_deltas.csv"
OUT = ROOT / "tests" / "fixtures" / "raw-hero-state-transactions"

from collector.constants import WORLD_MODE_PROGRAM
from collector.hero_state import HeroState, RawMovement, apply_movement, apply_quest_restart, classify_world_call
from collector.solana_normalize import (
    normalize_token_movements,
    normalize_transaction,
    transaction_has_burn_instruction,
    transaction_signers,
)
from collector.solana_rpc import clients_from_repo, get_transaction_with_fallback


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def i(value: str | None) -> int:
    return int(float(value or 0))


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace(" UTC", "+00:00").replace("Z", "+00:00")).astimezone(timezone.utc)


def state_from_row(row: dict[str, str]) -> HeroState:
    def n(name: str):
        value = row.get(name, "")
        return value or None
    return HeroState(
        mint=row["mint"],
        burned=i(row.get("burned")),
        burn_utc=n("burn_utc"),
        burn_signature=n("burn_signature"),
        current_raw_owner=n("current_raw_owner"),
        current_world_staked=i(row.get("current_world_staked")),
        current_beneficial_owner=n("current_beneficial_owner"),
        current_world_staking_wallet=n("current_world_staking_wallet"),
        latest_event_utc=n("latest_event_utc"),
        latest_signature=n("latest_signature"),
        quest_user_wallet=n("quest_user_wallet"),
        quest_staking_wallet=n("quest_staking_wallet"),
        current_stake_deposit_utc=n("current_stake_deposit_utc"),
        current_stake_deposit_signature=n("current_stake_deposit_signature"),
        best_known_last_qualifying_quest_utc=n("best_known_last_qualifying_quest_utc"),
        best_known_last_qualifying_quest_signature=n("best_known_last_qualifying_quest_signature"),
        quest_history_source=n("quest_history_source"),
        deep_history_status=n("deep_history_status"),
    )


def choose_distinct(rows, *, signature_field: str, user_field: str, count: int) -> list[dict[str, str]]:
    out = []
    sigs, users = set(), set()
    for row in rows:
        sig = row.get(signature_field) or ""
        user = row.get(user_field) or ""
        if not sig or not user or sig in sigs or user in users:
            continue
        out.append(row)
        sigs.add(sig)
        users.add(user)
        if len(out) >= count:
            break
    return out


def main() -> None:
    assets = read_csv(ASSETS)
    deltas = read_csv(DELTAS)
    base = {r["mint"]: r for r in assets}
    guild_mints = set(base)
    clients = clients_from_repo(ROOT)
    OUT.mkdir(parents=True, exist_ok=True)

    provider_counts: dict[str, int] = {}
    failures: list[str] = []
    passes: list[str] = []

    def fetch(sig: str):
        fixture = OUT / f"{sig}.json"
        if fixture.exists():
            envelope = json.loads(fixture.read_text(encoding="utf-8"))
            tx = envelope["transaction"]
            provider = envelope.get("provider", "fixture")
        else:
            tx, provider = get_transaction_with_fallback(clients, sig)
            fixture.write_text(
                json.dumps({"provider": provider, "transaction": tx}, indent=2) + "\n",
                encoding="utf-8",
            )
        provider_counts[provider] = provider_counts.get(provider, 0) + 1
        return tx

    def world_calls(tx):
        signers = transaction_signers(tx)
        signer = signers[0] if signers else None
        out = []
        for call in normalize_transaction(tx):
            wc = classify_world_call(
                signature=call.signature,
                event_time=call.block_time,
                executing_account=call.executing_account,
                data=call.data,
                signer=signer,
                account_arguments=call.account_arguments,
            )
            if wc:
                out.append(wc)
        return out

    # ------------------------------------------------------------------
    # 1. STAKE: frozen current-stake records must decode signer -> ARG_4,
    # and the Hero movement must land in that exact staking wallet.
    # ------------------------------------------------------------------
    stake_pool = [
        r for r in assets
        if i(r["current_world_staked"]) == 1
        and r.get("current_stake_deposit_signature")
        and r.get("current_beneficial_owner")
        and r.get("current_world_staking_wallet")
    ]
    stake_pool.sort(key=lambda r: r.get("current_stake_deposit_utc", ""), reverse=True)
    stake_cases = choose_distinct(
        stake_pool, signature_field="current_stake_deposit_signature",
        user_field="current_beneficial_owner", count=4,
    )

    for row in stake_cases:
        sig = row["current_stake_deposit_signature"]
        tx = fetch(sig)
        calls = [c for c in world_calls(tx) if c.action == "STAKE"]
        moves = [m for m in normalize_token_movements(tx, {row["mint"]}) if m.mint == row["mint"]]
        ok = (
            len(calls) >= 1 and len(moves) == 1
            and calls[0].user_wallet == row["current_beneficial_owner"]
            and calls[0].staking_wallet == row["current_world_staking_wallet"]
            and moves[0].classification == "TRANSFER"
            and moves[0].to_owner == row["current_world_staking_wallet"]
            and moves[0].from_owner == row["current_beneficial_owner"]
        )
        label = f"STAKE {row['hero_name']}"
        if ok:
            pre = HeroState(mint=row["mint"], current_raw_owner=moves[0].from_owner,
                            current_beneficial_owner=moves[0].from_owner)
            rm = RawMovement(sig, row["mint"], moves[0].block_time, "transfer",
                             moves[0].from_owner, moves[0].to_owner)
            reduced = apply_movement(pre, rm, calls[0])
            ok = (
                reduced.current_world_staked == 1
                and reduced.current_raw_owner == row["current_raw_owner"]
                and reduced.current_beneficial_owner == row["current_beneficial_owner"]
                and reduced.current_world_staking_wallet == row["current_world_staking_wallet"]
            )
        (passes if ok else failures).append(label)
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")

    # ------------------------------------------------------------------
    # 2. QUEST_RESTART: recent delta must decode the exact 9-byte instruction
    # and reproduce the stored latest qualifying quest for a Hero.
    # ------------------------------------------------------------------
    quest_pool = []
    for d in deltas:
        b = base[d["mint"]]
        if (
            d.get("best_known_last_qualifying_quest_signature")
            and d.get("best_known_last_qualifying_quest_signature") != b.get("best_known_last_qualifying_quest_signature")
            and i(d.get("current_world_staked")) == 1
        ):
            quest_pool.append(d)
    quest_pool.sort(key=lambda r: r.get("best_known_last_qualifying_quest_utc", ""), reverse=True)
    quest_cases = choose_distinct(
        quest_pool, signature_field="best_known_last_qualifying_quest_signature",
        user_field="quest_user_wallet", count=4,
    )

    for d in quest_cases:
        b = base[d["mint"]]
        sig = d["best_known_last_qualifying_quest_signature"]
        tx = fetch(sig)
        calls = [c for c in world_calls(tx) if c.action == "QUEST_RESTART"]
        ok = len(calls) >= 1 and calls[0].user_wallet == d["quest_user_wallet"]
        if ok:
            reduced = apply_quest_restart(state_from_row(b), calls[0])
            ok = (
                reduced.best_known_last_qualifying_quest_signature == sig
                and dt(reduced.best_known_last_qualifying_quest_utc) == dt(d["best_known_last_qualifying_quest_utc"])
            )
        label = f"QUEST_RESTART {b['hero_name']}"
        (passes if ok else failures).append(label)
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")

    # ------------------------------------------------------------------
    # 3. BURN: recent cutover deltas must show sender-only NFT movement plus an
    # explicit SPL Burn/BurnChecked, then reduce to no owner/staking state.
    # ------------------------------------------------------------------
    burn_pool = [d for d in deltas if i(d.get("burned")) == 1 and i(base[d["mint"]].get("burned")) == 0]
    burn_pool.sort(key=lambda r: (r.get("burn_utc", ""), r["mint"]), reverse=True)
    burn_cases = []
    seen_burn_sigs = set()
    for d in burn_pool:
        if d["burn_signature"] in seen_burn_sigs:
            continue
        seen_burn_sigs.add(d["burn_signature"])
        burn_cases.append(d)
        if len(burn_cases) >= 4:
            break

    for d in burn_cases:
        b = base[d["mint"]]
        sig = d["burn_signature"]
        tx = fetch(sig)
        moves = [m for m in normalize_token_movements(tx, {d["mint"]}) if m.mint == d["mint"]]
        ok = (
            len(moves) == 1
            and moves[0].classification == "BURN_OR_SEND_TO_ZERO"
            and transaction_has_burn_instruction(tx, d["mint"])
        )
        if ok:
            rm = RawMovement(sig, d["mint"], moves[0].block_time, "burn", moves[0].from_owner, None)
            reduced = apply_movement(state_from_row(b), rm)
            ok = (
                reduced.burned == 1
                and reduced.current_raw_owner is None
                and reduced.current_beneficial_owner is None
                and reduced.current_world_staked == 0
                and reduced.burn_signature == sig
            )
        label = f"BURN {b['hero_name']}"
        (passes if ok else failures).append(label)
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")

    # ------------------------------------------------------------------
    # 4. Ordinary transfer: post-baseline ownership deltas that are neither
    # burns nor World Mode custody changes must make beneficial owner follow raw.
    # ------------------------------------------------------------------
    transfer_pool = []
    for d in deltas:
        b = base[d["mint"]]
        if (
            i(d.get("burned")) == 0
            and i(b.get("burned")) == 0
            and i(d.get("current_world_staked")) == 0
            and i(b.get("current_world_staked")) == 0
            and d.get("current_raw_owner")
            and d.get("current_raw_owner") != b.get("current_raw_owner")
            and d.get("latest_signature")
        ):
            transfer_pool.append(d)
    transfer_pool.sort(key=lambda r: r.get("latest_event_utc", ""), reverse=True)
    transfer_cases = transfer_pool[:4]

    for d in transfer_cases:
        b = base[d["mint"]]
        sig = d["latest_signature"]
        tx = fetch(sig)
        moves = [m for m in normalize_token_movements(tx, {d["mint"]}) if m.mint == d["mint"]]
        wc = world_calls(tx)
        ok = (
            len(moves) == 1
            and moves[0].classification == "TRANSFER"
            and moves[0].to_owner == d["current_raw_owner"]
            and not any(c.action in {"STAKE", "UNSTAKE"} for c in wc)
        )
        if ok:
            rm = RawMovement(sig, d["mint"], moves[0].block_time, "transfer",
                             moves[0].from_owner, moves[0].to_owner)
            reduced = apply_movement(state_from_row(b), rm)
            ok = (
                reduced.current_raw_owner == d["current_raw_owner"]
                and reduced.current_beneficial_owner == d["current_beneficial_owner"]
                and reduced.current_world_staked == 0
            )
        label = f"TRANSFER {b['hero_name']}"
        (passes if ok else failures).append(label)
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")

    # ------------------------------------------------------------------
    # 5. UNSTAKE: search a narrow frozen 2025 migration-era candidate set.
    # The baseline's latest tx is independent expected state; we only count a
    # case after raw decoding proves that exact tx contains UNSTAKE.
    # ------------------------------------------------------------------
    unstake_candidates = [
        r for r in assets
        if i(r.get("burned")) == 0
        and i(r.get("current_world_staked")) == 0
        and r.get("latest_signature")
        and "2025-03-15" <= r.get("latest_event_utc", "")[:10] <= "2025-05-15"
    ]
    unstake_candidates.sort(key=lambda r: (r.get("latest_event_utc", ""), r["mint"]))
    found_unstakes = 0
    checked = 0
    seen_sigs = set()
    for row in unstake_candidates:
        sig = row["latest_signature"]
        if sig in seen_sigs:
            continue
        seen_sigs.add(sig)
        checked += 1
        tx = fetch(sig)
        calls = [c for c in world_calls(tx) if c.action == "UNSTAKE"]
        if not calls:
            if checked >= 35 and found_unstakes == 0:
                break
            continue
        moves = [m for m in normalize_token_movements(tx, {row["mint"]}) if m.mint == row["mint"]]
        c = calls[0]
        ok = (
            len(moves) == 1
            and moves[0].classification == "TRANSFER"
            and moves[0].from_owner == c.staking_wallet
            and moves[0].to_owner == c.user_wallet
            and c.user_wallet == row["current_raw_owner"]
            and row["current_beneficial_owner"] == c.user_wallet
        )
        if ok:
            pre = HeroState(
                mint=row["mint"], current_raw_owner=c.staking_wallet,
                current_world_staked=1, current_beneficial_owner=c.user_wallet,
                current_world_staking_wallet=c.staking_wallet,
                quest_user_wallet=c.user_wallet, quest_staking_wallet=c.staking_wallet,
                current_stake_deposit_utc="2025-01-01T00:00:00Z",
                current_stake_deposit_signature="historical-prestate",
            )
            rm = RawMovement(sig, row["mint"], moves[0].block_time, "transfer",
                             moves[0].from_owner, moves[0].to_owner)
            reduced = apply_movement(pre, rm, c)
            ok = (
                reduced.current_world_staked == 0
                and reduced.current_raw_owner == row["current_raw_owner"]
                and reduced.current_beneficial_owner == row["current_beneficial_owner"]
                and reduced.current_world_staking_wallet is None
            )
        label = f"UNSTAKE {row['hero_name']}"
        (passes if ok else failures).append(label)
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")
        found_unstakes += 1
        if found_unstakes >= 3:
            break

    if found_unstakes < 2:
        failures.append(f"UNSTAKE discovery: expected at least 2 validated cases, found {found_unstakes} after {checked} candidates")
        print(f"[FAIL] UNSTAKE discovery only found {found_unstakes} after {checked} candidates")

    report = {
        "passed_checks": len(passes),
        "failed_checks": len(failures),
        "validated_unstake_cases": found_unstakes,
        "unstake_candidates_fetched": checked,
        "providers": provider_counts,
        "passes": passes,
        "failures": failures,
    }
    (OUT / "backtest_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print()
    print(json.dumps(report, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
