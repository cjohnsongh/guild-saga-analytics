#!/usr/bin/env python3
"""Read-only audit of the currently pending durable webhook inbox.

This is the bridge between the one-time activation close-out and the production
incremental processor. It intentionally does NOT:
- modify canonical data
- modify checkpoints
- acknowledge/fail any D1 inbox rows
- modify the Helius webhook

It fetches the pending raw transactions, validates their shapes/signatures,
runs the already-backtested independent Hero/World/market decoders against the
effective canonical state, and writes candidate/audit artifacts only under
.guild_saga_recon/.
"""
from __future__ import annotations

import csv
import json
import sys
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collector.constants import ORIGINAL_SUPPLY
from collector.hero_state import (
    HeroState,
    RawMovement,
    WorldCall,
    apply_movement,
    apply_quest_restart_to_collection,
    classify_world_call,
)
from collector.market import decode_sales
from collector.solana_normalize import (
    normalize_token_movements,
    normalize_transaction,
    transaction_has_burn_instruction,
    transaction_signers,
)

WORKER_ORIGIN = "https://guild-saga-webhook-inbox.cjohnson80.workers.dev"
SECRETS = ROOT / "cloudflare" / "webhook-inbox" / ".env.worker-secrets.local"
ASSETS = ROOT / "data" / "baseline" / "assets.csv"
DELTAS = ROOT / "data" / "state" / "hero_deltas.csv"
CHECKPOINTS = ROOT / "data" / "state" / "checkpoints.json"
OUT_DIR = ROOT / ".guild_saga_recon"
REPORT = OUT_DIR / "pending_webhook_audit.json"
HERO_CANDIDATE = OUT_DIR / "pending_webhook_candidate_hero_deltas.csv"
SALES_CANDIDATE = OUT_DIR / "pending_webhook_candidate_sales.json"

STATE_FIELDS = [f.name for f in fields(HeroState) if f.name != "mint"]
CSV_FIELDS = ["mint", *STATE_FIELDS]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36"


def parse_utc(value: str) -> datetime:
    text = str(value or "").strip().replace(" UTC", "+00:00").replace("Z", "+00:00")
    if not text:
        raise RuntimeError("Missing timestamp")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def empty(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def truthy_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def load_effective_state() -> tuple[dict[str, dict[str, str]], dict[str, HeroState], datetime]:
    assets = read_csv(ASSETS)
    if len(assets) != ORIGINAL_SUPPLY:
        raise RuntimeError(f"Expected {ORIGINAL_SUPPLY:,} baseline Heroes, found {len(assets):,}")
    rows = {r["mint"]: dict(r) for r in assets}
    if len(rows) != ORIGINAL_SUPPLY:
        raise RuntimeError("Baseline mint addresses are not unique.")

    for delta in read_csv(DELTAS):
        mint = str(delta.get("mint") or "")
        if mint not in rows:
            raise RuntimeError(f"hero_deltas.csv contains unknown mint {mint!r}")
        for field in STATE_FIELDS:
            if field in delta:
                rows[mint][field] = delta.get(field, "")

    states = {}
    for mint, row in rows.items():
        states[mint] = HeroState(
            mint=mint,
            burned=truthy_int(row.get("burned")),
            burn_utc=empty(row.get("burn_utc")),
            burn_signature=empty(row.get("burn_signature")),
            current_raw_owner=empty(row.get("current_raw_owner")),
            current_world_staked=truthy_int(row.get("current_world_staked")),
            current_beneficial_owner=empty(row.get("current_beneficial_owner")),
            current_world_staking_wallet=empty(row.get("current_world_staking_wallet")),
            latest_event_utc=empty(row.get("latest_event_utc")),
            latest_signature=empty(row.get("latest_signature")),
            quest_user_wallet=empty(row.get("quest_user_wallet")),
            quest_staking_wallet=empty(row.get("quest_staking_wallet")),
            current_stake_deposit_utc=empty(row.get("current_stake_deposit_utc")),
            current_stake_deposit_signature=empty(row.get("current_stake_deposit_signature")),
            best_known_last_qualifying_quest_utc=empty(row.get("best_known_last_qualifying_quest_utc")),
            best_known_last_qualifying_quest_signature=empty(row.get("best_known_last_qualifying_quest_signature")),
            quest_history_source=empty(row.get("quest_history_source")),
            deep_history_status=empty(row.get("deep_history_status")),
        )

    cp = json.loads(CHECKPOINTS.read_text(encoding="utf-8"))
    checkpoint = parse_utc(cp.get("hero_state_checkpoint"))
    return rows, states, checkpoint


def load_pipeline_token() -> str:
    if not SECRETS.exists():
        raise RuntimeError(f"Missing local Worker secrets file: {SECRETS.relative_to(ROOT)}")
    values = {}
    for line in SECRETS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip()
    token = values.get("PIPELINE_TOKEN", "")
    if not token:
        raise RuntimeError("PIPELINE_TOKEN missing from local Worker secrets file.")
    return token


def http_json(url: str, token: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": UA,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Worker HTTP {exc.code}: {body}") from exc
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise RuntimeError(f"Unexpected Worker response: {data!r}")
    return data


def payload_signature(tx: dict[str, Any]) -> str:
    transaction = tx.get("transaction") or {}
    signatures = transaction.get("signatures") or []
    if not signatures:
        raise RuntimeError("Raw webhook transaction has no transaction.signatures")
    sig = str(signatures[0])
    if len(sig) <= 20:
        raise RuntimeError("Raw webhook transaction has invalid first signature")
    return sig


def world_calls_for_tx(tx: dict[str, Any]) -> list[WorldCall]:
    signers = transaction_signers(tx)
    signer = signers[0] if signers else None
    unique: dict[tuple[str, str, str | None], WorldCall] = {}
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
            unique[(wc.action, wc.user_wallet, wc.staking_wallet)] = wc
    return list(unique.values())


def choose_world_call(move, calls: list[WorldCall]) -> WorldCall | None:
    for wc in calls:
        if (
            wc.action == "STAKE"
            and wc.staking_wallet
            and move.to_owner == wc.staking_wallet
            and move.from_owner == wc.user_wallet
        ):
            return wc
        if (
            wc.action == "UNSTAKE"
            and wc.staking_wallet
            and move.from_owner == wc.staking_wallet
            and move.to_owner == wc.user_wallet
        ):
            return wc
    return None


def state_diff(old: HeroState, new: HeroState) -> set[str]:
    return {
        f.name for f in fields(HeroState)
        if f.name != "mint" and getattr(old, f.name) != getattr(new, f.name)
    }


def write_hero_candidate(start: dict[str, HeroState], final: dict[str, HeroState]) -> tuple[int, Counter]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    counts = Counter()
    for mint in sorted(start):
        changed = state_diff(start[mint], final[mint])
        if not changed:
            continue
        counts.update(changed)
        d = asdict(final[mint])
        rows.append({k: ("" if d[k] is None else d[k]) for k in CSV_FIELDS})
    with HERO_CANDIDATE.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return len(rows), counts


def main() -> int:
    print("=" * 78)
    print("Guild Saga — Phase 2G Pending Webhook Inbox Audit · READ ONLY")
    print("=" * 78)
    print("Canonical/checkpoint writes: NONE")
    print("D1 acknowledgements:         NONE")
    print("Helius webhook changes:      NONE")
    print()

    print("[1/5] Loading effective canonical Hero state")
    _, start_states, checkpoint = load_effective_state()
    known_mints = set(start_states)
    print(f"    Heroes:                  {len(start_states):,}")
    print(f"    Hero checkpoint:         {z(checkpoint)}")

    print("[2/5] Fetching pending D1 inbox")
    token = load_pipeline_token()
    payload = http_json(f"{WORKER_ORIGIN}/internal/pending?limit=100", token)
    events = payload.get("events") or []
    if not isinstance(events, list):
        raise RuntimeError("Worker pending response has non-list events.")
    if len(events) >= 100:
        raise RuntimeError(
            "Pending endpoint returned the full 100-row limit. Audit refuses a partial inbox view; "
            "production pagination/batching must be installed first."
        )
    print(f"    pending rows:            {len(events):,}")

    print("[3/5] Validating raw webhook rows")
    validated = []
    signatures = set()
    for row in events:
        if not isinstance(row, dict):
            raise RuntimeError("Pending inbox contains a non-object row.")
        sig = str(row.get("signature") or "")
        if not sig or sig in signatures:
            raise RuntimeError(f"Blank/duplicate pending signature: {sig!r}")
        signatures.add(sig)
        raw = row.get("payload_json")
        if not isinstance(raw, str):
            raise RuntimeError(f"{sig}: payload_json is not text")
        tx = json.loads(raw)
        if not isinstance(tx, dict):
            raise RuntimeError(f"{sig}: raw payload is not a JSON object")
        inner_sig = payload_signature(tx)
        if inner_sig != sig:
            raise RuntimeError(f"{sig}: D1 signature != raw transaction signature {inner_sig}")
        bt = tx.get("blockTime")
        if bt is None:
            raise RuntimeError(f"{sig}: raw transaction has no blockTime")
        bt = int(bt)
        row_bt = row.get("block_time")
        if row_bt is not None and int(row_bt) != bt:
            raise RuntimeError(f"{sig}: D1 block_time {row_bt} != raw blockTime {bt}")
        validated.append((datetime.fromtimestamp(bt, timezone.utc), sig, tx))
    validated.sort(key=lambda x: (x[0], x[1]))
    print(f"    signature/payload match: {len(validated):,}/{len(validated):,}")

    print("[4/5] Running independent decoders against current canonical state")
    states = dict(start_states)
    counts = Counter()
    sales_out = []
    tx_rows = []
    unresolved = []

    for dt, sig, tx in validated:
        row_summary = {
            "signature": sig,
            "block_time_utc": z(dt),
            "relative_to_hero_checkpoint": "after" if dt > checkpoint else "at_or_before",
            "tx_success": (tx.get("meta") or {}).get("err") is None,
            "world_calls": [],
            "hero_movements": [],
            "quest_hero_updates": 0,
            "sales": [],
        }

        if dt <= checkpoint:
            counts["AT_OR_BEFORE_CHECKPOINT"] += 1
            tx_rows.append(row_summary)
            continue

        if (tx.get("meta") or {}).get("err") is not None:
            counts["FAILED_TX"] += 1
            tx_rows.append(row_summary)
            continue

        try:
            calls = world_calls_for_tx(tx)
        except Exception as exc:
            unresolved.append(f"{sig}: World Mode decode failed: {exc}")
            calls = []

        for wc in calls:
            counts[f"WORLD_{wc.action}"] += 1
            row_summary["world_calls"].append({
                "action": wc.action,
                "user_wallet": wc.user_wallet,
                "staking_wallet": wc.staking_wallet,
            })

        try:
            movements = normalize_token_movements(tx, known_mints)
        except Exception as exc:
            unresolved.append(f"{sig}: movement normalization failed: {exc}")
            movements = []

        for move in movements:
            info = {
                "mint": move.mint,
                "classification": move.classification,
                "from_owner": move.from_owner,
                "to_owner": move.to_owner,
            }
            row_summary["hero_movements"].append(info)

            if move.classification == "TRANSFER":
                if move.from_owner is None or move.to_owner is None:
                    unresolved.append(
                        f"{sig} {move.mint}: ambiguous TRANSFER {move.from_owners}->{move.to_owners}"
                    )
                    continue
                wc = choose_world_call(move, calls)
                rm = RawMovement(sig, move.mint, move.block_time, "transfer", move.from_owner, move.to_owner)
                states[move.mint] = apply_movement(states[move.mint], rm, wc)
                counts["TRANSFER"] += 1
                if wc:
                    counts[f"HERO_{wc.action}"] += 1

            elif move.classification == "BURN_OR_SEND_TO_ZERO":
                if not transaction_has_burn_instruction(tx, move.mint):
                    unresolved.append(
                        f"{sig} {move.mint}: sender-only NFT movement lacks explicit SPL Burn/BurnChecked"
                    )
                    continue
                if move.from_owner is None:
                    unresolved.append(f"{sig} {move.mint}: burn source is ambiguous")
                    continue
                rm = RawMovement(sig, move.mint, move.block_time, "burn", move.from_owner, None)
                states[move.mint] = apply_movement(states[move.mint], rm)
                counts["BURN"] += 1

            elif move.classification == "MINT_OR_RECEIVE_FROM_ZERO":
                unresolved.append(f"{sig} {move.mint}: unexpected receive-from-zero after production cutover")

        for wc in calls:
            if wc.action == "QUEST_RESTART":
                changed = apply_quest_restart_to_collection(states, wc)
                row_summary["quest_hero_updates"] += changed
                counts["QUEST_HERO_UPDATES"] += changed

        try:
            decoded = decode_sales(normalize_transaction(tx), known_mints)
        except Exception as exc:
            unresolved.append(f"{sig}: market decoder failed: {exc}")
            decoded = []
        for sale in decoded:
            d = {
                "signature": sale.signature,
                "mint": sale.mint,
                "block_time_utc": z(sale.block_time),
                "marketplace": sale.marketplace,
                "marketplace_detail": sale.marketplace_detail,
                "buyer": sale.buyer,
                "seller": sale.seller,
                "gross_price_sol": sale.gross_price_sol,
                "gross_price_method": sale.gross_price_method,
                "royalty_90_sol": sale.royalty_90_sol,
                "royalty_10_sol": sale.royalty_10_sol,
                "royalty_total_sol": sale.royalty_total_sol,
            }
            sales_out.append(d)
            row_summary["sales"].append(d)
            counts["SALE"] += 1

        tx_rows.append(row_summary)

    if unresolved:
        print(f"    unresolved cases:        {len(unresolved):,}")
        for item in unresolved[:20]:
            print(f"      - {item}")
        raise RuntimeError("Pending inbox contains unresolved raw-chain cases. Nothing was acknowledged or modified.")

    candidate_rows, changed_fields = write_hero_candidate(start_states, states)
    SALES_CANDIDATE.write_text(json.dumps(sales_out, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"    after-checkpoint tx:     {sum(1 for x in validated if x[0] > checkpoint):,}")
    print(f"    stale/overlap tx:        {counts['AT_OR_BEFORE_CHECKPOINT']:,}")
    print(f"    failed tx:               {counts['FAILED_TX']:,}")
    print(f"    Hero transfers:          {counts['TRANSFER']:,}")
    print(f"    explicit burns:          {counts['BURN']:,}")
    print(f"    Hero stake / unstake:    {counts['HERO_STAKE']:,} / {counts['HERO_UNSTAKE']:,}")
    print(f"    Quest Restart calls:     {counts['WORLD_QUEST_RESTART']:,}")
    print(f"    Quest Hero updates:      {counts['QUEST_HERO_UPDATES']:,}")
    print(f"    decoded market sales:    {counts['SALE']:,}")
    print(f"    candidate Hero rows:     {candidate_rows:,}")

    print("[5/5] Writing ignored audit artifacts")
    report = {
        "status": "PASS",
        "read_only": True,
        "canonical_files_modified": False,
        "d1_events_acknowledged": 0,
        "helius_webhook_modified": False,
        "hero_checkpoint": z(checkpoint),
        "pending_count": len(validated),
        "counts": dict(counts),
        "candidate_hero_rows": candidate_rows,
        "candidate_changed_fields": dict(changed_fields),
        "candidate_sales": len(sales_out),
        "transactions": tx_rows,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print()
    print("=" * 78)
    print("[PASS] PENDING WEBHOOK INBOX AUDITED — NOTHING ACKNOWLEDGED")
    print("=" * 78)
    print(f"Pending rows audited:       {len(validated):,}")
    print(f"At/before Hero checkpoint:  {counts['AT_OR_BEFORE_CHECKPOINT']:,}")
    print(f"After Hero checkpoint:      {sum(1 for x in validated if x[0] > checkpoint):,}")
    print(f"Failed transactions:        {counts['FAILED_TX']:,}")
    print(f"Hero transfers:             {counts['TRANSFER']:,}")
    print(f"Explicit burns:             {counts['BURN']:,}")
    print(f"Hero stake / unstake:       {counts['HERO_STAKE']:,} / {counts['HERO_UNSTAKE']:,}")
    print(f"Quest Restart calls:        {counts['WORLD_QUEST_RESTART']:,}")
    print(f"Quest Hero updates:         {counts['QUEST_HERO_UPDATES']:,}")
    print(f"Decoded market sales:       {counts['SALE']:,}")
    print(f"Candidate Hero rows:        {candidate_rows:,}")
    print(f"Audit report:               {REPORT.relative_to(ROOT)}")
    print(f"Hero candidate:             {HERO_CANDIDATE.relative_to(ROOT)}")
    print(f"Sales candidate:            {SALES_CANDIDATE.relative_to(ROOT)}")
    print("Canonical files modified:   NONE")
    print("D1 events acknowledged:     NONE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print()
        print(f"[FAIL] {exc}")
        print("Canonical files and D1 inbox were not modified.")
        raise SystemExit(1)
