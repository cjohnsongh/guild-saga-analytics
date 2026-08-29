#!/usr/bin/env python3
"""Read-only reconnaissance for the first independent post-cutover catch-up.

This script deliberately does NOT modify canonical state. It answers three
questions before we build the Aug. 26 -> present reducer:

1. Can Helius DAS resolve all 10,000 known Guild Saga Hero mints directly?
2. Which Heroes have a different raw owner/burn state than the frozen cutover?
3. Which validated World Mode calls occurred after the cutover checkpoint?

Secrets are loaded through collector.solana_rpc (env vars first, then ignored
local key files). No secret is printed or written to disk.
"""
from __future__ import annotations

import csv
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collector.constants import ORIGINAL_SUPPLY, WORLD_MODE_PROGRAM
from collector.hero_state import classify_world_call
from collector.solana_normalize import (
    normalize_token_movements,
    normalize_transaction,
    transaction_signers,
)
from collector.solana_rpc import clients_from_repo, get_transaction_with_fallback

ASSETS = ROOT / "data" / "baseline" / "assets.csv"
DELTAS = ROOT / "data" / "state" / "hero_deltas.csv"
CHECKPOINTS = ROOT / "data" / "state" / "checkpoints.json"
UPDATE_AUTHORITY = "3NpvpHGwHAMYhiuB9GrMXSYqKSj16L2LSHR9czFPWbLu"
DAS_BATCH_LIMIT = 1000
TX_DELAY_SECONDS = 0.13  # conservative local backfill throttle (~7.7 tx/s)

STATE_FIELDS = (
    "burned", "burn_utc", "burn_signature", "current_raw_owner",
    "current_world_staked", "current_beneficial_owner",
    "current_world_staking_wallet", "latest_event_utc", "latest_signature",
    "quest_user_wallet", "quest_staking_wallet", "current_stake_deposit_utc",
    "current_stake_deposit_signature", "best_known_last_qualifying_quest_utc",
    "best_known_last_qualifying_quest_signature", "quest_history_source",
    "deep_history_status",
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def parse_utc(value: str) -> datetime:
    value = value.strip().replace(" UTC", "+00:00").replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_z_from_ts(ts: int | None) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(int(ts), timezone.utc).isoformat().replace("+00:00", "Z")


def truthy_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def cutover_state() -> tuple[dict[str, dict[str, str]], datetime]:
    assets = read_csv(ASSETS)
    if len(assets) != ORIGINAL_SUPPLY:
        raise RuntimeError(f"Expected {ORIGINAL_SUPPLY:,} baseline rows; found {len(assets):,}.")
    state = {row["mint"]: dict(row) for row in assets}
    if len(state) != ORIGINAL_SUPPLY:
        raise RuntimeError("Baseline mint addresses are not unique.")

    for delta in read_csv(DELTAS):
        mint = delta.get("mint") or ""
        if mint not in state:
            raise RuntimeError(f"hero_deltas.csv contains unknown mint {mint!r}")
        for field in STATE_FIELDS:
            if field in delta:
                state[mint][field] = delta.get(field, "")

    checkpoints = json.loads(CHECKPOINTS.read_text(encoding="utf-8"))
    checkpoint_text = str(checkpoints.get("hero_state_checkpoint") or "").strip()
    if not checkpoint_text:
        raise RuntimeError("checkpoints.json has no hero_state_checkpoint")
    checkpoint = parse_utc(checkpoint_text)

    burns = sum(truthy_int(row.get("burned")) for row in state.values())
    if burns != 168:
        raise RuntimeError(
            f"Frozen cutover merge produced {burns} burns; expected 168. "
            "Stop rather than scan from an uncertain seed."
        )
    return state, checkpoint


def helius_client(clients):
    for client in clients:
        if client.label.casefold() == "helius":
            return client
    raise RuntimeError("This reconnaissance needs Helius for DAS getAssetBatch.")


def fetch_asset_batch_resilient(client, ids: list[str]) -> dict[str, dict[str, Any]]:
    """Resolve a known set of mints, splitting only if a batch is rejected.

    Helius getAssetBatch accepts up to 1,000 IDs.  Using the immutable 10,000-mint
    baseline as the lookup key is more reliable for reconciliation than asking an
    authority index to enumerate the collection: zero-balance/burned assets can be
    omitted by enumeration endpoints even though getAsset/getAssetBatch can still
    resolve those exact mint IDs.
    """
    if not ids:
        return {}

    try:
        result = client.call(
            "getAssetBatch",
            {
                "ids": ids,
                "options": {
                    "showUnverifiedCollections": True,
                    "showCollectionMetadata": False,
                },
            },
        )
    except Exception as exc:
        if len(ids) == 1:
            raise RuntimeError(f"getAssetBatch could not resolve known Hero mint {ids[0]}: {exc}") from exc
        mid = len(ids) // 2
        left = fetch_asset_batch_resilient(client, ids[:mid])
        right = fetch_asset_batch_resilient(client, ids[mid:])
        left.update(right)
        return left

    if not isinstance(result, list):
        raise RuntimeError(f"getAssetBatch returned unexpected {type(result).__name__} data")

    found: dict[str, dict[str, Any]] = {}
    for item in result:
        if not isinstance(item, dict):
            continue
        mint = str(item.get("id") or "")
        if mint in ids:
            found[mint] = item

    missing = [mint for mint in ids if mint not in found]
    if missing:
        # Some DAS implementations can return partial batch data.  Retry only the
        # unresolved subset so a single odd asset cannot hide the rest of the batch.
        retry = fetch_asset_batch_resilient(client, missing) if len(missing) < len(ids) else {}
        found.update(retry)

    return found


def enumerate_known_assets(client, known_mints: set[str]) -> dict[str, dict[str, Any]]:
    print("[1/3] Helius DAS known-mint snapshot")
    ordered = sorted(known_mints)
    found: dict[str, dict[str, Any]] = {}
    total_batches = (len(ordered) + DAS_BATCH_LIMIT - 1) // DAS_BATCH_LIMIT

    for batch_no, start in enumerate(range(0, len(ordered), DAS_BATCH_LIMIT), 1):
        ids = ordered[start:start + DAS_BATCH_LIMIT]
        batch = fetch_asset_batch_resilient(client, ids)
        found.update(batch)
        print(
            f"    batch {batch_no:>2}/{total_batches}: requested {len(ids):>4} | "
            f"resolved {len(batch):>4} | known Heroes {len(found):>5}/{len(known_mints)}"
        )

    missing = known_mints - set(found)
    extra = set(found) - known_mints
    if extra:
        raise RuntimeError(f"DAS returned {len(extra)} unexpected asset IDs during known-mint snapshot.")
    if missing:
        examples = ", ".join(sorted(missing)[:5])
        raise RuntimeError(
            f"DAS known-mint snapshot is incomplete: {len(missing):,} Hero mints unresolved "
            f"(examples: {examples}). Stop before reconciliation."
        )
    return found


def asset_burnt(asset: dict[str, Any]) -> bool:
    return asset.get("burnt") is True


def asset_owner(asset: dict[str, Any]) -> str | None:
    ownership = asset.get("ownership")
    if isinstance(ownership, dict):
        owner = ownership.get("owner")
        if owner:
            return str(owner)
    # Defensive compatibility with alternate provider shapes.
    owner = asset.get("owner")
    return str(owner) if owner else None


def compare_inventory(state, inventory):
    new_burns = []
    owner_changes = []
    burn_regressions = []
    unresolved_owners = []

    for mint, row in state.items():
        asset = inventory[mint]
        was_burned = truthy_int(row.get("burned")) == 1
        now_burned = asset_burnt(asset)
        old_owner = row.get("current_raw_owner") or None
        now_owner = None if now_burned else asset_owner(asset)

        if was_burned and not now_burned:
            burn_regressions.append(mint)
        if not was_burned and now_burned:
            new_burns.append(mint)
        if not now_burned and not now_owner:
            unresolved_owners.append(mint)
        if not was_burned and not now_burned and old_owner != now_owner:
            owner_changes.append((mint, old_owner, now_owner))

    current_burns = sum(asset_burnt(x) for x in inventory.values())
    print()
    print("[2/3] Cutover -> current DAS comparison")
    print(f"    cutover burned:       {sum(truthy_int(r.get('burned')) for r in state.values()):,}")
    print(f"    current DAS burned:   {current_burns:,}")
    print(f"    newly burned:         {len(new_burns):,}")
    print(f"    raw owner differences:{len(owner_changes):,}")
    print(f"    unresolved owners:    {len(unresolved_owners):,}")

    if burn_regressions:
        raise RuntimeError(f"DAS says {len(burn_regressions)} cutover-burned Heroes are no longer burned.")
    if unresolved_owners:
        raise RuntimeError(f"DAS returned {len(unresolved_owners)} active Heroes without owners.")

    return new_burns, owner_changes


def signatures_since(client, address: str, checkpoint: datetime) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    before: str | None = None
    seen: set[str] = set()

    while True:
        opts: dict[str, Any] = {"limit": 1000, "commitment": "finalized"}
        if before:
            opts["before"] = before
        rows = client.call("getSignaturesForAddress", [address, opts]) or []
        if not isinstance(rows, list):
            raise RuntimeError(f"getSignaturesForAddress({address}) returned unexpected data")
        if not rows:
            break

        reached_checkpoint = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            sig = str(row.get("signature") or "")
            bt = row.get("blockTime")
            if bt is not None and datetime.fromtimestamp(int(bt), timezone.utc) <= checkpoint:
                reached_checkpoint = True
                continue
            if sig and sig not in seen:
                seen.add(sig)
                out.append(row)

        if reached_checkpoint or len(rows) < 1000:
            break
        before = str((rows[-1] or {}).get("signature") or "")
        if not before:
            break

    out.sort(key=lambda r: (int(r.get("blockTime") or 0), str(r.get("signature") or "")))
    return out


def scan_world(clients, helius, checkpoint: datetime, known_mints: set[str]):
    print()
    print("[3/3] World Mode program activity since cutover")
    rows = signatures_since(helius, WORLD_MODE_PROGRAM, checkpoint)
    successful = [r for r in rows if r.get("err") is None and r.get("signature")]
    print(f"    signatures after checkpoint: {len(rows):,}")
    print(f"    successful candidates:       {len(successful):,}")

    counts = Counter()
    users: dict[str, set[str]] = {"STAKE": set(), "UNSTAKE": set(), "QUEST_RESTART": set()}
    hero_links = Counter()
    decoded_events = []
    providers = Counter()

    for n, row in enumerate(successful, 1):
        sig = str(row["signature"])
        tx, provider = get_transaction_with_fallback(clients, sig)
        providers[provider] += 1
        signers = transaction_signers(tx)
        signer = signers[0] if signers else None
        calls = []
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
                calls.append(wc)

        # Deduplicate identical logical calls inside one tx.
        unique = {}
        for wc in calls:
            unique[(wc.action, wc.user_wallet, wc.staking_wallet)] = wc

        movements = normalize_token_movements(tx, known_mints)
        for wc in unique.values():
            counts[wc.action] += 1
            users.setdefault(wc.action, set()).add(wc.user_wallet)
            linked_mints = []
            if wc.action == "STAKE" and wc.staking_wallet:
                linked_mints = [
                    m.mint for m in movements
                    if m.classification == "TRANSFER"
                    and m.to_owner == wc.staking_wallet
                    and m.from_owner == wc.user_wallet
                ]
            elif wc.action == "UNSTAKE" and wc.staking_wallet:
                linked_mints = [
                    m.mint for m in movements
                    if m.classification == "TRANSFER"
                    and m.from_owner == wc.staking_wallet
                    and m.to_owner == wc.user_wallet
                ]
            if wc.action in {"STAKE", "UNSTAKE"}:
                hero_links[wc.action] += len(linked_mints)
            decoded_events.append({
                "utc": wc.event_time.isoformat().replace("+00:00", "Z"),
                "action": wc.action,
                "user": wc.user_wallet,
                "staking_wallet": wc.staking_wallet or "",
                "heroes": linked_mints,
                "signature": wc.signature,
            })

        if n < len(successful):
            time.sleep(TX_DELAY_SECONDS)

    print(f"    decoded STAKE:         {counts['STAKE']:,} tx calls / {hero_links['STAKE']:,} Hero moves / {len(users['STAKE']):,} users")
    print(f"    decoded UNSTAKE:       {counts['UNSTAKE']:,} tx calls / {hero_links['UNSTAKE']:,} Hero moves / {len(users['UNSTAKE']):,} users")
    print(f"    decoded QUEST_RESTART: {counts['QUEST_RESTART']:,} tx calls / {len(users['QUEST_RESTART']):,} users")
    if providers:
        print("    transaction providers: " + ", ".join(f"{k}={v}" for k, v in sorted(providers.items())))

    if decoded_events:
        print()
        print("    latest decoded events:")
        for event in decoded_events[-12:]:
            hero_note = f" · {len(event['heroes'])} Hero(s)" if event["heroes"] else ""
            print(f"      {event['utc']} · {event['action']}{hero_note} · {event['signature'][:12]}...")

    return rows, decoded_events


def main() -> None:
    print("=" * 78)
    print("GUILD SAGA — POST-CUTOVER RECONNAISSANCE (READ ONLY)")
    print("=" * 78)

    state, checkpoint = cutover_state()
    known_mints = set(state)
    clients = clients_from_repo(ROOT)
    helius = helius_client(clients)

    print(f"Cutover checkpoint: {checkpoint.isoformat().replace('+00:00', 'Z')}")
    print(f"Known Heroes:       {len(known_mints):,}")
    print("No canonical files will be modified.\n")

    inventory = enumerate_known_assets(helius, known_mints)
    new_burns, owner_changes = compare_inventory(state, inventory)
    world_rows, world_events = scan_world(clients, helius, checkpoint, known_mints)

    print()
    print("=" * 78)
    print("RECONNAISSANCE COMPLETE — NO STATE WRITTEN")
    print("=" * 78)
    print(json.dumps({
        "cutover_checkpoint": checkpoint.isoformat().replace("+00:00", "Z"),
        "known_heroes": len(known_mints),
        "das_known_mint_snapshot_complete": len(inventory) == len(known_mints),
        "current_burned": sum(asset_burnt(x) for x in inventory.values()),
        "new_burn_candidates": len(new_burns),
        "raw_owner_differences": len(owner_changes),
        "world_signatures_after_cutover": len(world_rows),
        "decoded_world_events": len(world_events),
    }, indent=2))
    print()
    print("Paste this full console output back into ChatGPT before the catch-up writes anything.")


if __name__ == "__main__":
    main()
