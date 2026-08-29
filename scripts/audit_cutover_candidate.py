#!/usr/bin/env python3
"""Offline audit/export of the validated Aug. 26 -> current catch-up candidate.

Reads only:
  data/baseline/assets.csv
  data/state/hero_deltas.csv
  data/state/checkpoints.json
  .guild_saga_recon/cutover_free_backfill.sqlite

Writes only ignored diagnostic artifacts under:
  .guild_saga_recon/candidate_hero_deltas.csv
  .guild_saga_recon/candidate_delta_audit.json

It does NOT modify canonical data or website JSON and makes NO network calls.
"""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import scan_cutover_events as scan
from collector.hero_state import HeroState, apply_quest_restart_to_collection, parse_iso

OUT_DIR = ROOT / ".guild_saga_recon"
CSV_OUT = OUT_DIR / "candidate_hero_deltas.csv"
JSON_OUT = OUT_DIR / "candidate_delta_audit.json"

DYNAMIC_FIELDS = [
    "mint",
    "burned",
    "burn_utc",
    "burn_signature",
    "current_raw_owner",
    "current_world_staked",
    "current_beneficial_owner",
    "current_world_staking_wallet",
    "latest_event_utc",
    "latest_signature",
    "quest_user_wallet",
    "quest_staking_wallet",
    "current_stake_deposit_utc",
    "current_stake_deposit_signature",
    "best_known_last_qualifying_quest_utc",
    "best_known_last_qualifying_quest_signature",
    "quest_history_source",
    "deep_history_status",
]

QUEST_MUTABLE_FIELDS = {
    "best_known_last_qualifying_quest_utc",
    "best_known_last_qualifying_quest_signature",
    "quest_history_source",
    "deep_history_status",
}

IMMUTABLE_DURING_THIS_CATCHUP = {
    "burned",
    "burn_utc",
    "burn_signature",
    "current_raw_owner",
    "current_world_staked",
    "current_beneficial_owner",
    "current_world_staking_wallet",
    "latest_event_utc",
    "latest_signature",
    "quest_user_wallet",
    "quest_staking_wallet",
    "current_stake_deposit_utc",
    "current_stake_deposit_signature",
}

def z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def as_csv_value(v):
    if v is None:
        return ""
    return str(v)

def diff_state(a: HeroState, b: HeroState) -> dict[str, tuple[object, object]]:
    out = {}
    for f in fields(HeroState):
        if f.name == "mint":
            continue
        av = getattr(a, f.name)
        bv = getattr(b, f.name)
        if av != bv:
            out[f.name] = (av, bv)
    return out

def main():
    print("=" * 78)
    print("GUILD SAGA — CATCH-UP CANDIDATE DELTA AUDIT · OFFLINE · READ ONLY")
    print("=" * 78)

    if not scan.DB_PATH.exists():
        raise RuntimeError(
            f"Missing resume DB: {scan.DB_PATH.relative_to(ROOT)}. "
            "Run the completed cutover scanner first."
        )

    cutover_rows, checkpoint = scan.cutover_state()
    base_states = scan.hero_objects(cutover_rows)
    known_mints = set(base_states)

    # Open the already-populated cache directly. No schema creation and no RPC.
    con = sqlite3.connect(scan.DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        raw = scan.load_raw(con)
        if not raw:
            raise RuntimeError("Resume DB contains no raw transactions.")

        records = []
        for sig, tx in raw.items():
            bt = tx.get("blockTime")
            if bt is None:
                continue
            dt = datetime.fromtimestamp(int(bt), timezone.utc)
            if dt > checkpoint and (tx.get("meta") or {}).get("err") is None:
                records.append((dt, sig, tx))
        records.sort(key=lambda x: (x[0], x[1]))

        # Reuse the independently-tested reducer entirely offline.
        reconstructed, summary = scan.verify_and_reduce(
            con, cutover_rows, checkpoint, known_mints
        )

        forbidden_summary = {
            "transfers": summary["transfers"],
            "burns": summary["burns"],
            "hero_stakes": summary["hero_stakes"],
            "hero_unstakes": summary["hero_unstakes"],
            "sales": summary["sales"],
        }
        if any(forbidden_summary.values()):
            raise RuntimeError(
                "This audit expected the completed catch-up to be quest-only, "
                f"but found non-quest changes: {forbidden_summary}"
            )

        # Independently replay just the qualifying Quest Restart calls so the
        # per-call effect can be shown and cross-checked against the full reducer.
        quest_states = dict(base_states)
        quest_calls = []
        per_call = []
        for dt, sig, tx in records:
            for call in scan.world_calls_for_tx(tx):
                if call.action != "QUEST_RESTART":
                    continue
                changed = apply_quest_restart_to_collection(quest_states, call)
                quest_calls.append(call)
                per_call.append({
                    "signature": call.signature,
                    "utc": z(call.event_time),
                    "user_wallet": call.user_wallet,
                    "hero_updates": changed,
                })

        if len(quest_calls) != summary["world_quest_restart_calls"]:
            raise RuntimeError(
                f"Quest-call recount mismatch: {len(quest_calls)} vs "
                f"{summary['world_quest_restart_calls']}"
            )
        if sum(x["hero_updates"] for x in per_call) != summary["quest_hero_updates"]:
            raise RuntimeError(
                "Quest Hero-update recount mismatch: "
                f"{sum(x['hero_updates'] for x in per_call)} vs "
                f"{summary['quest_hero_updates']}"
            )
        if quest_states != reconstructed:
            raise RuntimeError(
                "Quest-only replay does not equal the full reducer result even "
                "though the full reducer reported zero transfer/burn/stake/unstake events."
            )

        changed_rows = []
        field_counts = Counter()
        changed_by_final_sig = Counter()
        changed_by_user = Counter()
        violations = []

        quest_by_sig = {c.signature: c for c in quest_calls}

        for mint in sorted(base_states):
            before = base_states[mint]
            after = reconstructed[mint]
            d = diff_state(before, after)
            if not d:
                continue

            for name in d:
                field_counts[name] += 1
                if name in IMMUTABLE_DURING_THIS_CATCHUP:
                    violations.append(f"{mint}: forbidden field changed: {name}")
                if name not in QUEST_MUTABLE_FIELDS:
                    violations.append(f"{mint}: unexpected field changed: {name}")

            sig = after.best_known_last_qualifying_quest_signature or ""
            call = quest_by_sig.get(sig)
            if not call:
                violations.append(f"{mint}: final quest signature {sig!r} is not one of the post-cutover Quest Restart calls")
            else:
                if after.quest_user_wallet != call.user_wallet:
                    violations.append(
                        f"{mint}: quest user {after.quest_user_wallet} != call signer {call.user_wallet}"
                    )
                qt = parse_iso(after.best_known_last_qualifying_quest_utc)
                if qt != call.event_time:
                    violations.append(
                        f"{mint}: final quest timestamp does not equal raw call timestamp"
                    )
                stake = parse_iso(after.current_stake_deposit_utc)
                if stake is None or call.event_time < stake:
                    violations.append(
                        f"{mint}: qualifying quest is before current stake start"
                    )

            changed_by_final_sig[sig] += 1
            changed_by_user[after.quest_user_wallet or ""] += 1

            row = {"mint": mint}
            for name in DYNAMIC_FIELDS[1:]:
                row[name] = as_csv_value(getattr(after, name))
            changed_rows.append(row)

        if violations:
            print()
            print("VIOLATIONS:")
            for v in violations[:30]:
                print("  -", v)
            raise RuntimeError(
                f"Candidate delta audit found {len(violations)} invariant violation(s). "
                "Canonical files remain untouched."
            )

        # These files are diagnostic/candidate outputs only and live under the
        # ignored .guild_saga_recon directory.
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        with CSV_OUT.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=DYNAMIC_FIELDS)
            w.writeheader()
            w.writerows(changed_rows)

        report = {
            "cutover_checkpoint": z(checkpoint),
            "candidate_is_quest_only": True,
            "quest_restart_calls": len(quest_calls),
            "quest_hero_update_applications": sum(x["hero_updates"] for x in per_call),
            "unique_heroes_changed": len(changed_rows),
            "changed_field_counts": dict(field_counts),
            "per_quest_call": per_call,
            "final_changed_heroes_by_quest_signature": dict(changed_by_final_sig),
            "final_changed_heroes_by_user": dict(changed_by_user),
            "reconstructed_active": summary["reconstructed_active"],
            "reconstructed_burned": summary["reconstructed_burned"],
            "reconstructed_staked": summary["reconstructed_staked"],
            "reconstructed_beneficial_holders": summary["reconstructed_beneficial_holders"],
            "candidate_csv": str(CSV_OUT.relative_to(ROOT)),
            "canonical_files_modified": False,
        }
        JSON_OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")

        print()
        print("[PASS] Candidate is quest-only.")
        print(f"    Quest Restart calls:          {len(quest_calls):,}")
        print(f"    Quest Hero update applications:{sum(x['hero_updates'] for x in per_call):,}")
        print(f"    Unique Heroes changed:        {len(changed_rows):,}")
        print(f"    Ownership changes:            0")
        print(f"    Burns:                        0")
        print(f"    Stake/unstake changes:        0")
        print(f"    Sales:                        0")
        print()
        print("    Per-call effects:")
        for i, row in enumerate(per_call, 1):
            print(
                f"      {i}. {row['utc']} | user {row['user_wallet']} | "
                f"{row['hero_updates']} Hero updates | {row['signature']}"
            )
        print()
        print("    Fields changed across final unique Hero rows:")
        for name, count in sorted(field_counts.items()):
            print(f"      {name}: {count:,}")
        print()
        print(f"Candidate CSV:  {CSV_OUT.relative_to(ROOT)}")
        print(f"Audit report:   {JSON_OUT.relative_to(ROOT)}")
        print("Canonical files modified: NONE")
        print()
        print(json.dumps({
            "status": "PASS",
            "quest_restart_calls": len(quest_calls),
            "quest_hero_update_applications": sum(x["hero_updates"] for x in per_call),
            "unique_heroes_changed": len(changed_rows),
            "canonical_files_modified": False,
        }, indent=2))
    finally:
        con.close()

if __name__ == "__main__":
    main()
