#!/usr/bin/env python3
"""Close the Aug-26 canonical -> Helius webhook activation gap, read-only.

This is a one-time safety scan. It rechecks every address in the exact production
Helius watch set from the frozen canonical checkpoint through the conservatively
recorded webhook activation boundary, with a five-minute overlap. It is resumable,
uses only standard Solana RPC methods, fetches/decodes any newly discovered raw
transactions, and writes candidate/diagnostic artifacts only under the ignored
.guild_saga_recon directory.

It NEVER edits canonical data, checkpoints, website JSON, or the Helius webhook.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import sqlite3
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import scan_cutover_events as scan
from collector.hero_state import HeroState

RECEIPT_PATH = ROOT / ".guild_saga_recon" / "helius_webhook_setup.json"
REPORT_PATH = ROOT / ".guild_saga_recon" / "webhook_boundary_closeout.json"
CANDIDATE_CSV = ROOT / ".guild_saga_recon" / "closeout_candidate_hero_deltas.csv"
DISCOVERY_OVERLAP_SECONDS = 300
HEAD_LIMIT = 1
PAGE_LIMIT = 1000
MAX_PAGES_PER_ADDRESS = 25
PROGRESS_EVERY = 250

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


def z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_z(value: str) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_setup_module():
    path = ROOT / "cloudflare" / "webhook-inbox" / "setup-helius-webhook.py"
    if not path.exists():
        raise RuntimeError(f"Missing {path.relative_to(ROOT)}. Phase 2D must be present first.")
    spec = importlib.util.spec_from_file_location("guild_saga_helius_setup", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load Phase 2D setup module.")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_receipt() -> dict[str, Any]:
    if not RECEIPT_PATH.exists():
        raise RuntimeError(
            f"Missing {RECEIPT_PATH.relative_to(ROOT)}. The successful Phase 2D activation receipt is required."
        )
    data = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("active") is not True:
        raise RuntimeError("Helius activation receipt is missing active=true.")
    if not data.get("activation_boundary_utc") or not data.get("watch_set_sha256"):
        raise RuntimeError("Helius activation receipt is missing boundary/watch-set metadata.")
    return data


def worker_stats(setup, pipeline_token: str) -> dict[str, Any]:
    status, payload = setup.http_json(
        "GET",
        f"{setup.WORKER_ORIGIN}/internal/stats",
        headers={"Authorization": f"Bearer {pipeline_token}"},
    )
    if status != 200 or not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("Worker /internal/stats verification failed.")
    return payload


def verify_d1_boundary(stats: dict[str, Any], expected: str) -> None:
    meta = {
        str(row.get("key")): str(row.get("value"))
        for row in stats.get("meta", [])
        if isinstance(row, dict)
    }
    actual = meta.get("webhook_activated_at")
    if not actual:
        raise RuntimeError("D1 has no webhook_activated_at metadata row.")
    if parse_z(actual) != parse_z(expected):
        raise RuntimeError(f"D1 activation boundary {actual} != local receipt {expected}.")


def inbox_counts(stats: dict[str, Any]) -> dict[str, int]:
    return {
        str(row.get("status")): int(row.get("n") or 0)
        for row in stats.get("counts", [])
        if isinstance(row, dict)
    }


def ensure_closeout_schema(con: sqlite3.Connection, boundary: str, watch_hash: str, total: int) -> None:
    con.executescript("""
    CREATE TABLE IF NOT EXISTS closeout_address_scan(
      address TEXT PRIMARY KEY,
      complete INTEGER NOT NULL DEFAULT 0,
      pages INTEGER NOT NULL DEFAULT 0,
      recent_signatures INTEGER NOT NULL DEFAULT 0,
      provider TEXT,
      error TEXT
    );
    """)
    keys = {
        "closeout_boundary_utc": boundary,
        "closeout_watch_set_sha256": watch_hash,
        "closeout_watch_address_count": str(total),
    }
    for key, wanted in keys.items():
        row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if row is not None and str(row[0]) != wanted:
            raise RuntimeError(
                f"Resume DB {key}={row[0]!r}, expected {wanted!r}. "
                "Refusing to mix two different close-out scans."
            )
        con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, wanted))
    con.commit()


def seed_addresses(con: sqlite3.Connection, addresses: list[str]) -> None:
    con.executemany(
        "INSERT OR IGNORE INTO closeout_address_scan(address) VALUES(?)",
        ((a,) for a in addresses),
    )
    con.commit()
    n = int(con.execute("SELECT COUNT(*) FROM closeout_address_scan").fetchone()[0])
    if n != len(addresses):
        raise RuntimeError(f"Seeded {n:,} close-out addresses; expected {len(addresses):,}.")


def rpc_signature_page(lane, lanes, address: str, limit: int, before: str | None = None):
    opts: dict[str, Any] = {"limit": limit, "commitment": "finalized"}
    if before:
        opts["before"] = before
    rows, provider = scan.call_with_lane_resilient(
        lane, lanes, "getSignaturesForAddress", [address, opts], rounds=3
    )
    if not isinstance(rows, list):
        raise RuntimeError(f"getSignaturesForAddress({address}) returned {type(rows).__name__}")
    return [r for r in rows if isinstance(r, dict)], provider


def scan_one_address(
    address: str,
    lane,
    lanes,
    start_ts: int,
    end_ts: int,
) -> dict[str, Any]:
    pages = 0
    kept: dict[str, int] = {}
    providers: list[str] = []

    head, provider = rpc_signature_page(lane, lanes, address, HEAD_LIMIT)
    pages += 1
    providers.append(provider)
    if not head:
        return {"address": address, "pages": pages, "rows": [], "provider": provider}

    first = head[0]
    head_sig = str(first.get("signature") or "")
    head_bt = first.get("blockTime")
    if not head_sig:
        raise RuntimeError(f"{address}: newest signature row has no signature")
    if head_bt is None:
        raise RuntimeError(f"{address}: newest signature {head_sig} has null blockTime")
    head_bt = int(head_bt)

    # If the newest finalized transaction predates the overlap start, this
    # address provably has nothing in the close-out interval.
    if head_bt <= start_ts:
        return {"address": address, "pages": pages, "rows": [], "provider": provider}

    if head_bt <= end_ts:
        kept[head_sig] = head_bt

    before = head_sig
    while True:
        if pages >= MAX_PAGES_PER_ADDRESS:
            raise RuntimeError(
                f"{address}: exceeded {MAX_PAGES_PER_ADDRESS} signature pages before crossing close-out start"
            )
        rows, provider = rpc_signature_page(lane, lanes, address, PAGE_LIMIT, before)
        pages += 1
        providers.append(provider)
        if not rows:
            break

        crossed_start = False
        for row in rows:
            sig = str(row.get("signature") or "")
            bt = row.get("blockTime")
            if not sig:
                continue
            if bt is None:
                raise RuntimeError(f"{address}: signature {sig} has null blockTime")
            bt = int(bt)
            if bt <= start_ts:
                crossed_start = True
                continue
            if bt <= end_ts:
                kept[sig] = bt

        if crossed_start or len(rows) < PAGE_LIMIT:
            break
        before = str(rows[-1].get("signature") or "")
        if not before:
            raise RuntimeError(f"{address}: pagination row has no signature")

    provider_label = "+".join(sorted(set(providers)))
    out_rows = sorted(kept.items(), key=lambda x: (x[1], x[0]))
    return {"address": address, "pages": pages, "rows": out_rows, "provider": provider_label}


def scan_watch_set(
    con: sqlite3.Connection,
    clients,
    addresses: list[str],
    start_ts: int,
    end_ts: int,
) -> tuple[Counter, int]:
    pending = [
        str(r[0]) for r in con.execute(
            "SELECT address FROM closeout_address_scan WHERE complete=0 ORDER BY address"
        )
    ]
    already = len(addresses) - len(pending)
    print(f"    already complete from resume cache: {already:,}")
    print(f"    addresses to query this run:        {len(pending):,}")
    if not pending:
        return Counter(), 0

    lanes = scan.phase_lanes(clients, scan.ALCHEMY_HEAVY_RPS, scan.HELIUS_RPC_RPS)
    cycle = scan.weighted_lane_cycle(lanes)
    provider_counts = Counter()
    lock = threading.Lock()
    completed_this_run = 0
    signature_rows_added = 0

    def job(index_address):
        idx, address = index_address
        lane = lanes[cycle[idx % len(cycle)]]
        return scan_one_address(address, lane, lanes, start_ts, end_ts)

    with ThreadPoolExecutor(max_workers=scan.PARALLEL_WORKERS) as pool:
        futures = {pool.submit(job, x): x[1] for x in enumerate(pending)}
        for future in as_completed(futures):
            address = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                con.execute(
                    "UPDATE closeout_address_scan SET error=? WHERE address=?",
                    (str(exc)[:4000], address),
                )
                con.commit()
                for f in futures:
                    f.cancel()
                raise RuntimeError(f"Close-out scan failed at {address}: {exc}") from exc

            rows = result["rows"]
            for sig, bt in rows:
                before_changes = con.total_changes
                con.execute(
                    """INSERT OR IGNORE INTO discovered_signatures
                       (signature,mint,source,block_time) VALUES(?,?,?,?)""",
                    (sig, address, "CLOSEOUT_WATCH_SET", bt),
                )
                if con.total_changes > before_changes:
                    signature_rows_added += 1
            con.execute(
                """UPDATE closeout_address_scan
                   SET complete=1,pages=?,recent_signatures=?,provider=?,error=NULL
                   WHERE address=?""",
                (result["pages"], len(rows), result["provider"], address),
            )
            con.commit()
            completed_this_run += 1
            provider_counts[result["provider"]] += 1

            total_done = already + completed_this_run
            if total_done % PROGRESS_EVERY == 0 or total_done == len(addresses):
                unique = int(con.execute(
                    "SELECT COUNT(DISTINCT signature) FROM discovered_signatures"
                ).fetchone()[0])
                print(
                    f"      {total_done:>6,}/{len(addresses):,} addresses | "
                    f"close-out signature rows +{signature_rows_added:,} | "
                    f"all unique cached signatures {unique:,}"
                )

    return provider_counts, signature_rows_added


def filtered_reduction(con: sqlite3.Connection, checkpoint: datetime, boundary: datetime, cutover_rows):
    raw = scan.load_raw(con)
    selected: list[tuple[str, str, int, str]] = []
    for sig, tx in raw.items():
        bt = tx.get("blockTime")
        if bt is None:
            raise RuntimeError(f"{sig}: cached raw transaction has no blockTime")
        dt = datetime.fromtimestamp(int(bt), timezone.utc)
        if checkpoint < dt <= boundary:
            selected.append((sig, "cached", int(bt), json.dumps(tx, separators=(",", ":"))))

    mem = sqlite3.connect(":memory:")
    mem.row_factory = sqlite3.Row
    mem.execute(
        "CREATE TABLE raw_transactions(signature TEXT PRIMARY KEY, provider TEXT, block_time INTEGER, tx_json TEXT)"
    )
    mem.executemany(
        "INSERT INTO raw_transactions(signature,provider,block_time,tx_json) VALUES(?,?,?,?)",
        selected,
    )
    mem.commit()
    try:
        states, summary = scan.verify_and_reduce(mem, cutover_rows, checkpoint, set(cutover_rows))
    finally:
        mem.close()
    return states, summary, len(selected)


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


def write_candidate(base_states: dict[str, HeroState], final_states: dict[str, HeroState]) -> tuple[int, Counter]:
    rows = []
    field_counts = Counter()
    for mint in sorted(base_states):
        d = diff_state(base_states[mint], final_states[mint])
        if not d:
            continue
        field_counts.update(d.keys())
        st = final_states[mint]
        row = {"mint": mint}
        for name in DYNAMIC_FIELDS[1:]:
            value = getattr(st, name)
            row[name] = "" if value is None else str(value)
        rows.append(row)

    CANDIDATE_CSV.parent.mkdir(parents=True, exist_ok=True)
    with CANDIDATE_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=DYNAMIC_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows), field_counts


def main() -> int:
    print("=" * 78)
    print("Guild Saga — Phase 2E Webhook Boundary Close-Out · READ ONLY")
    print("=" * 78)
    print("Canonical data/checkpoints: NEVER MODIFIED")
    print("Helius webhook config:       NEVER MODIFIED")
    print("Resume/candidate outputs:    .guild_saga_recon only")
    print()

    setup = load_setup_module()
    receipt = load_receipt()
    boundary_text = str(receipt["activation_boundary_utc"])
    boundary = parse_z(boundary_text)

    worker_dir = ROOT / "cloudflare" / "webhook-inbox"
    _, pipeline_token = setup.load_worker_secrets(worker_dir)
    stats_before = worker_stats(setup, pipeline_token)
    verify_d1_boundary(stats_before, boundary_text)
    counts_before = inbox_counts(stats_before)

    print("[1/6] Verifying activation boundary + exact production watch set")
    all_mints, active_mints = setup.read_canonical_active_mints(ROOT)
    token_accounts = setup.read_reconciled_token_accounts(ROOT, active_mints)
    addresses, counts = setup.build_watch_set(all_mints, token_accounts)
    digest = setup.watch_hash(addresses)
    if digest != receipt["watch_set_sha256"]:
        raise RuntimeError(
            "Rebuilt watch set hash does not match the activated Helius webhook receipt. "
            "Do not close the boundary against a different address set."
        )
    if len(addresses) != int((receipt.get("watch_counts") or {}).get("total_unique") or -1):
        raise RuntimeError("Rebuilt watch-set count does not match activation receipt.")
    print(f"    activation boundary:     {boundary_text}")
    print(f"    watch addresses:         {len(addresses):,}/{len(addresses):,} verified")
    print(f"    watch-set SHA-256:       {digest[:16]}… MATCH")
    print(f"    D1 boundary:             MATCH")
    print(f"    webhook inbox pending:   {counts_before.get('pending', 0):,}")

    print("[2/6] Opening successful Phase 1L resume cache")
    cutover_rows, checkpoint = scan.cutover_state()
    base_states = scan.hero_objects(cutover_rows)
    start_ts = int(checkpoint.timestamp()) - DISCOVERY_OVERLAP_SECONDS
    end_ts = int(boundary.timestamp())
    if end_ts <= int(checkpoint.timestamp()):
        raise RuntimeError("Activation boundary is not after canonical cutover checkpoint.")
    if not scan.DB_PATH.exists():
        raise RuntimeError(f"Missing {scan.DB_PATH.relative_to(ROOT)}")
    con = sqlite3.connect(scan.DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        ensure_closeout_schema(con, boundary_text, digest, len(addresses))
        seed_addresses(con, addresses)
        sigs_before = int(con.execute(
            "SELECT COUNT(DISTINCT signature) FROM discovered_signatures"
        ).fetchone()[0])
        raw_before = int(con.execute("SELECT COUNT(*) FROM raw_transactions").fetchone()[0])
        print(f"    frozen checkpoint:       {z(checkpoint)}")
        print(f"    overlap start:           {datetime.fromtimestamp(start_ts, timezone.utc).isoformat().replace('+00:00','Z')}")
        print(f"    cached unique signatures:{sigs_before:,}")
        print(f"    cached raw transactions: {raw_before:,}")

        print("[3/6] Rescanning every production watch address through activation")
        print("    Strategy: 1-signature head check; paginate only addresses active since cutover.")
        print("    This is the one intentionally broad close-out pass; it is resumable.")
        clients = scan.clients_from_repo(ROOT)
        provider_counts, signature_rows_added = scan_watch_set(
            con, clients, addresses, start_ts, end_ts
        )
        total = int(con.execute("SELECT COUNT(*) FROM closeout_address_scan").fetchone()[0])
        done = int(con.execute(
            "SELECT COUNT(*) FROM closeout_address_scan WHERE complete=1"
        ).fetchone()[0])
        if done != total or total != len(addresses):
            raise RuntimeError(f"Close-out address coverage incomplete: {done:,}/{total:,}")
        sigs_after = int(con.execute(
            "SELECT COUNT(DISTINCT signature) FROM discovered_signatures"
        ).fetchone()[0])
        print(f"    address coverage:        {done:,}/{total:,} COMPLETE")
        print(f"    new unique signatures:   {sigs_after - sigs_before:,}")

        print("[4/6] Fetching newly discovered raw transactions")
        providers = scan.fetch_raw_transactions(con, clients, "[4/6]")
        raw_after = int(con.execute("SELECT COUNT(*) FROM raw_transactions").fetchone()[0])
        print(f"    new raw transactions:    {raw_after - raw_before:,}")

        print("[5/6] Reducing exact checkpoint → activation interval")
        final_states, summary, selected_raw = filtered_reduction(
            con, checkpoint, boundary, cutover_rows
        )
        changed_rows, field_counts = write_candidate(base_states, final_states)
        print(f"    raw tx in exact interval:{selected_raw:,}")
        print(f"    candidate Hero rows:     {changed_rows:,}")

        print("[6/6] Writing ignored close-out audit")
        stats_after = worker_stats(setup, pipeline_token)
        verify_d1_boundary(stats_after, boundary_text)
        counts_after = inbox_counts(stats_after)
        report = {
            "status": "PASS",
            "canonical_files_modified": False,
            "helius_webhook_modified": False,
            "cutover_checkpoint": z(checkpoint),
            "activation_boundary_utc": boundary_text,
            "watch_set_sha256": digest,
            "watch_addresses_verified": len(addresses),
            "address_scan_complete": f"{done}/{total}",
            "provider_address_completions_this_run": dict(provider_counts),
            "signature_rows_added_this_run": signature_rows_added,
            "unique_signatures_before": sigs_before,
            "unique_signatures_after": sigs_after,
            "new_unique_signatures": sigs_after - sigs_before,
            "raw_transactions_before": raw_before,
            "raw_transactions_after": raw_after,
            "new_raw_transactions": raw_after - raw_before,
            "raw_transactions_in_exact_interval": selected_raw,
            "reduction": summary,
            "candidate_hero_rows": changed_rows,
            "candidate_changed_field_counts": dict(field_counts),
            "worker_inbox_counts_before": counts_before,
            "worker_inbox_counts_after": counts_after,
            "candidate_csv": str(CANDIDATE_CSV.relative_to(ROOT)),
        }
        REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        print()
        print("=" * 78)
        print("[PASS] WEBHOOK ACTIVATION BOUNDARY CLOSED — CANONICAL STILL UNCHANGED")
        print("=" * 78)
        print(f"Watch-set coverage:         {done:,}/{total:,}")
        print(f"New unique signatures:      {sigs_after - sigs_before:,}")
        print(f"New raw transactions:       {raw_after - raw_before:,}")
        print(f"Hero transfers:             {summary['transfers']:,}")
        print(f"Explicit burns:             {summary['burns']:,}")
        print(f"Hero stake / unstake:       {summary['hero_stakes']:,} / {summary['hero_unstakes']:,}")
        print(f"Quest Restart calls:        {summary['world_quest_restart_calls']:,}")
        print(f"Quest Hero updates:         {summary['quest_hero_updates']:,}")
        print(f"Decoded market sales:       {summary['sales']:,}")
        print(f"Reconstructed supply:       {summary['reconstructed_active']:,} active / {summary['reconstructed_burned']:,} burned")
        print(f"Reconstructed staked:       {summary['reconstructed_staked']:,}")
        print(f"Beneficial holders:         {summary['reconstructed_beneficial_holders']:,}")
        print(f"Candidate Hero rows:        {changed_rows:,}")
        print(f"Webhook pending now:        {counts_after.get('pending', 0):,}")
        print(f"Audit report:               {REPORT_PATH.relative_to(ROOT)}")
        print(f"Candidate CSV:              {CANDIDATE_CSV.relative_to(ROOT)}")
        print("Canonical files modified:   NONE")
        print()
        print("Paste this final PASS block back into ChatGPT. Do not commit/push yet.")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
