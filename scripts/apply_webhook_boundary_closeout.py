#!/usr/bin/env python3
"""Apply the audited Phase 2E quest-only close-out to canonical Hero state.

Corrected Phase 2F2 behavior:
- Reconstructs the *effective* current Hero state as baseline assets + existing
  hero_deltas before comparing the Phase 2E candidate. hero_deltas.csv is an
  overlay, so comparing candidate rows directly to sparse delta rows is unsafe.
- Requires the candidate to differ from effective canonical state only in the
  four independently-audited quest fields, with exact per-field counts matching
  the Phase 2E report.
- Advances only the Hero-state checkpoint to the durable webhook activation
  boundary. Market/floor checkpoints remain byte-for-byte values.
- Rebuilds public JSON and runs the full offline regression/live validation suite.
- Rolls canonical/public files back automatically if any post-write check fails.
"""
from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import fields as dataclass_fields
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import scan_cutover_events as scan
from collector.hero_state import HeroState

R = ROOT / ".guild_saga_recon"
REPORT = R / "webhook_boundary_closeout.json"
RECEIPT = R / "helius_webhook_setup.json"
CAND = R / "closeout_candidate_hero_deltas.csv"
DELTAS = ROOT / "data" / "state" / "hero_deltas.csv"
CPATH = ROOT / "data" / "state" / "checkpoints.json"
PUB = ROOT / "site" / "public" / "data"

QUEST_FIELDS = {
    "best_known_last_qualifying_quest_utc",
    "best_known_last_qualifying_quest_signature",
    "quest_history_source",
    "deep_history_status",
}
EXPECTED = {
    "active_supply": 9832,
    "burned": 168,
    "staked_heroes": 5851,
    "beneficial_holders": 1962,
}
EXPECTED_WATCH_ADDRESSES = 19835
EXPECTED_CANDIDATE_ROWS = 338


def load_json(path: Path):
    if not path.exists():
        raise RuntimeError(f"Missing {path.relative_to(ROOT)}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"Expected JSON object in {path.relative_to(ROOT)}")
    return data


def read_csv(path: Path):
    if not path.exists():
        raise RuntimeError(f"Missing {path.relative_to(ROOT)}")
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames or [])
        if not fields:
            raise RuntimeError(f"CSV has no header: {path.relative_to(ROOT)}")
        return fields, list(reader)


def parse_utc(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise RuntimeError("Missing UTC timestamp")
    dt = datetime.fromisoformat(text.replace(" UTC", "+00:00").replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def diff_state(old: HeroState, new: HeroState):
    changed = set()
    for field in dataclass_fields(HeroState):
        if field.name == "mint":
            continue
        if getattr(old, field.name) != getattr(new, field.name):
            changed.add(field.name)
    return changed


def run(label: str, *args: str):
    print(f"    {label}...")
    result = subprocess.run([sys.executable, *args], cwd=ROOT)
    if result.returncode:
        raise RuntimeError(f"{label} failed with exit code {result.returncode}")


def main():
    print("=" * 78)
    print("Guild Saga — Phase 2F2 Apply Durable Webhook Activation Boundary")
    print("=" * 78)
    print("Effective-state verification + transactional canonical write.")
    print("Automatic rollback on any failed build/test/validator.\n")

    print("[1/6] Verifying Phase 2D/2E evidence")
    receipt = load_json(RECEIPT)
    report = load_json(REPORT)
    boundary = str(receipt.get("activation_boundary_utc") or "").strip()
    if receipt.get("active") is not True or not boundary:
        raise RuntimeError("Phase 2D activation receipt is not valid/active.")
    if report.get("status") != "PASS" or str(report.get("activation_boundary_utc") or "").strip() != boundary:
        raise RuntimeError("Phase 2E PASS/boundary does not match Phase 2D.")
    if report.get("canonical_files_modified") is not False or report.get("helius_webhook_modified") is not False:
        raise RuntimeError("Phase 2E safety flags are not clean.")
    if report.get("watch_addresses_verified") != EXPECTED_WATCH_ADDRESSES:
        raise RuntimeError("Phase 2E watch-address count is not 19,835.")
    if report.get("address_scan_complete") != f"{EXPECTED_WATCH_ADDRESSES}/{EXPECTED_WATCH_ADDRESSES}":
        raise RuntimeError("Phase 2E watch-set coverage is not 19,835/19,835.")

    red = report.get("reduction") or {}
    forbidden = {k: int(red.get(k) or 0) for k in ("transfers", "burns", "hero_stakes", "hero_unstakes", "sales")}
    if any(forbidden.values()):
        raise RuntimeError(f"Close-out is not quest-only: {forbidden}")
    observed_kpis = (
        int(red.get("reconstructed_active") or -1),
        int(red.get("reconstructed_burned") or -1),
        int(red.get("reconstructed_staked") or -1),
        int(red.get("reconstructed_beneficial_holders") or -1),
    )
    if observed_kpis != (9832, 168, 5851, 1962):
        raise RuntimeError(f"Phase 2E reconstructed KPIs are unexpected: {observed_kpis}")

    report_field_counts = Counter({
        str(k): int(v) for k, v in (report.get("candidate_changed_field_counts") or {}).items()
    })
    if set(report_field_counts) != QUEST_FIELDS:
        raise RuntimeError("Phase 2E changed fields are not exactly the four audited quest fields.")
    if int(report.get("candidate_hero_rows") or -1) != EXPECTED_CANDIDATE_ROWS:
        raise RuntimeError("Phase 2E candidate row count is not 338.")

    print(f"    durable boundary:        {boundary}")
    print("    close-out classification: QUEST-ONLY")

    print("[2/6] Reconstructing effective canonical state + verifying candidate")
    # This is the key Phase 2F2 correction: cutover_state() materializes
    # baseline/assets.csv + the current sparse/full-row delta overlay.
    effective_rows, effective_checkpoint = scan.cutover_state()

    fields, delta_rows = read_csv(DELTAS)
    candidate_fields, candidates = read_csv(CAND)
    if fields != candidate_fields:
        raise RuntimeError("Candidate CSV schema differs from hero_deltas.csv.")
    if len(candidates) != EXPECTED_CANDIDATE_ROWS:
        raise RuntimeError(f"Expected 338 audited candidates, found {len(candidates)}.")

    candidate_by_mint = {row.get("mint", ""): row for row in candidates}
    if "" in candidate_by_mint or len(candidate_by_mint) != len(candidates):
        raise RuntimeError("Candidate CSV contains blank or duplicate mint addresses.")
    unknown = sorted(set(candidate_by_mint) - set(effective_rows))
    if unknown:
        raise RuntimeError(f"Candidate contains unknown Hero mint(s): {unknown[:3]}")

    effective_states = scan.hero_objects({mint: effective_rows[mint] for mint in candidate_by_mint})
    candidate_states = scan.hero_objects(candidate_by_mint)

    actual_field_counts = Counter()
    for mint in sorted(candidate_by_mint):
        changed = diff_state(effective_states[mint], candidate_states[mint])
        if not changed:
            raise RuntimeError(f"No-op candidate {mint}; expected an audited quest change.")
        unsafe = changed - QUEST_FIELDS
        if unsafe:
            raise RuntimeError(f"Unsafe candidate {mint}: non-quest changes {sorted(unsafe)}")
        actual_field_counts.update(changed)

    if actual_field_counts != report_field_counts:
        raise RuntimeError(
            "Candidate effective-state field counts do not match Phase 2E audit. "
            f"actual={dict(actual_field_counts)}, report={dict(report_field_counts)}"
        )

    cp = load_json(CPATH)
    oldcp = str(cp.get("hero_state_checkpoint") or "").strip()
    if not oldcp:
        raise RuntimeError("Canonical Hero checkpoint is missing.")
    if parse_utc(oldcp) != effective_checkpoint:
        raise RuntimeError("scan.cutover_state checkpoint disagrees with checkpoints.json.")
    if parse_utc(oldcp) >= parse_utc(boundary):
        raise RuntimeError("Hero checkpoint is already at/past the activation boundary; refusing a second apply.")

    old_market_cp = cp.get("market_checkpoint_date")
    old_floor_cp = cp.get("floor_checkpoint_date")
    print(f"    effective canonical Heroes: {len(effective_rows):,}")
    print(f"    candidate rows:            {len(candidates):,}")
    print(f"    existing delta rows:       {len(delta_rows):,}")
    print(f"    verified changed fields:   {', '.join(sorted(actual_field_counts))}")
    print(f"    old Hero checkpoint:       {oldcp}")

    print("[3/6] Applying candidate + Hero checkpoint")
    originals = [DELTAS, CPATH, *sorted(PUB.glob("*.json"))]
    pub_names = {p.name for p in PUB.glob("*.json")}

    with tempfile.TemporaryDirectory(prefix="gs-phase2f2-") as temp_dir:
        backup_root = Path(temp_dir)
        backups = {}
        for path in originals:
            if not path.exists():
                continue
            backup = backup_root / path.relative_to(ROOT)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
            backups[path] = backup

        try:
            delta_by_mint = {row.get("mint", ""): row for row in delta_rows}
            if "" in delta_by_mint or len(delta_by_mint) != len(delta_rows):
                raise RuntimeError("Canonical hero_deltas.csv contains blank or duplicate mint addresses.")

            # Candidate rows are full dynamic snapshots produced by Phase 2E, so
            # replacing/adding the row creates a valid overlay regardless of
            # whether the mint previously had a delta row.
            for row in candidates:
                delta_by_mint[row["mint"]] = row

            tmp = DELTAS.with_suffix(".csv.tmp")
            with tmp.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
                writer.writeheader()
                writer.writerows(delta_by_mint[mint] for mint in sorted(delta_by_mint))
            tmp.replace(DELTAS)

            cp["hero_state_checkpoint"] = boundary
            cp["notes"] = (
                "Canonical production seed created from final Dune state exports. "
                f"Hero state independently closed through durable Helius webhook boundary {boundary}. "
                "Subsequent Hero updates come from the independent webhook pipeline. "
                "Market and floor/listings retain their own independent checkpoints."
            )
            tmp = CPATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(cp, indent=2) + "\n", encoding="utf-8")
            tmp.replace(CPATH)

            print(f"    resulting delta rows:      {len(delta_by_mint):,}")
            print(f"    new Hero checkpoint:       {boundary}")

            print("[4/6] Rebuilding public dashboard JSON")
            run("dashboard builder", "scripts/build_dashboard_data.py")

            print("[5/6] Running regression + live validators")
            run("offline unit tests", "-m", "unittest", "discover", "-s", "tests", "-v")
            run("frozen cutover validator", "scripts/validate_cutover.py")
            run("live production validator", "scripts/validate_live.py")

            print("[6/6] Verifying post-apply invariants")
            summary = load_json(PUB / "summary.json")
            hero = load_json(PUB / "hero-state.json")
            for key, expected in EXPECTED.items():
                got = summary["hero"].get(key)
                if got != expected:
                    raise RuntimeError(f"summary.hero.{key} changed unexpectedly: got {got}, expected {expected}")

            # build_dashboard_data.py canonicalizes ISO timestamps and may render
            # .082Z as .082000Z. Compare instants rather than text formatting.
            if parse_utc(str(hero.get("as_of") or "")) != parse_utc(boundary):
                raise RuntimeError(f"hero-state.json as_of is not the durable boundary: {hero.get('as_of')!r}")

            cp_after = load_json(CPATH)
            if parse_utc(str(cp_after.get("hero_state_checkpoint") or "")) != parse_utc(boundary):
                raise RuntimeError("Hero checkpoint did not persist at the durable boundary.")
            if cp_after.get("market_checkpoint_date") != old_market_cp:
                raise RuntimeError("Market checkpoint changed unexpectedly.")
            if cp_after.get("floor_checkpoint_date") != old_floor_cp:
                raise RuntimeError("Floor/listings checkpoint changed unexpectedly.")

        except Exception:
            for path in list(PUB.glob("*.json")):
                if path.name not in pub_names:
                    path.unlink()
            for original, backup in backups.items():
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, original)
            print("\n[ROLLBACK] Canonical/public files restored.")
            raise

    print("\n" + "=" * 78)
    print("[PASS] DURABLE WEBHOOK BOUNDARY APPLIED TO CANONICAL HERO STATE")
    print("=" * 78)
    print(f"Hero checkpoint:            {boundary}")
    print(f"Candidate Hero rows merged: {len(candidates):,}")
    print("Ownership changes:          0")
    print("Burn changes:               0")
    print("Stake/unstake changes:      0")
    print("Market sales added:         0")
    print("Supply:                     9,832 active / 168 burned")
    print("Staked:                     5,851")
    print("Beneficial holders:         1,962")
    print("Offline tests:              PASS")
    print("Frozen cutover validator:   PASS")
    print("Live validator:             PASS")
    print("Market/floor checkpoints:   intentionally unchanged")
    print("Rollback needed:            NO")
    print("\nDo not commit/push yet; webhook drain processing is next.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\n[FAIL] {exc}")
        raise SystemExit(1)
