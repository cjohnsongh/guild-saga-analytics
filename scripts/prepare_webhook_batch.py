#!/usr/bin/env python3
"""Prepare one durable Helius/D1 webhook batch for a Git commit.

This is the production data reducer, but deliberately stops BEFORE D1 ACK.
The intended transaction boundary is:

    refresh watch frontier
    -> freeze pending D1 snapshot
    -> independently decode/reduce
    -> write canonical Hero/market state
    -> rebuild public JSON
    -> run all tests/validators
    -> write PREPARED receipt
    -> Git commit/push (next stage)
    -> ACK exactly the committed receipt signatures (next stage)

Safety:
- Stable D1 received_at snapshot with pagination.
- Every pending raw payload signature must match its D1 signature.
- Failed transactions are harmless but still recorded for eventual ACK.
- Ambiguous NFT movements, sender-only non-burns, unexpected post-cutover mints,
  unmatched World Mode stake/unstake, and unresolved marketplace-custody entries
  abort the whole batch.
- Per-Hero post-activation slot cursors prevent a late webhook from regressing
  ownership state. Same-slot/different-signature conflicts abort for review.
- Sale ledger is append-only and deduped by (signature, mint).
- Any build/test/validator failure restores canonical/public files.
- D1 is NEVER acknowledged by this script.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
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
WORKER_SECRETS = ROOT / "cloudflare" / "webhook-inbox" / ".env.worker-secrets.local"
ASSETS = ROOT / "data" / "baseline" / "assets.csv"
DELTAS = ROOT / "data" / "state" / "hero_deltas.csv"
MARKET_LIVE = ROOT / "data" / "state" / "market_live_sales.csv"
CHECKPOINTS = ROOT / "data" / "state" / "checkpoints.json"
HERO_CURSORS = ROOT / "data" / "state" / "hero_event_cursors.csv"
EVENT_LEDGER = ROOT / "data" / "state" / "webhook_processed_events.csv"
PUBLIC_DATA = ROOT / "site" / "public" / "data"
RECON = ROOT / ".guild_saga_recon"
ACTIVATION = RECON / "helius_webhook_setup.json"
PREPARED = RECON / "webhook_batch_prepared.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

# The Phase 1L direct-chain audit proved that this authority can be a custody
# semantic rather than the dashboard's beneficial/raw-owner semantic. Until a
# dedicated listing-custody reducer is transaction-fixtured, a new transfer
# *into* this custody authority must stop rather than silently count custody as
# a holder.
ME_V2_CUSTODY = "1BWutmTvYPwDtmw9abTkS4Ssr8no61spGAvW1X6NDix"

STATE_FIELDS = [f.name for f in fields(HeroState) if f.name != "mint"]
DELTA_FIELDS = ["mint", *STATE_FIELDS]
CURSOR_FIELDS = ["mint", "last_slot", "last_signature", "last_block_time_utc"]
EVENT_FIELDS = [
    "signature",
    "slot",
    "block_time_utc",
    "received_at",
    "tx_success",
    "hero_movements",
    "world_calls",
    "quest_hero_updates",
    "market_sales",
    "source",
]


def parse_utc(value: Any) -> datetime:
    text = str(value or "").strip().replace(" UTC", "+00:00").replace("Z", "+00:00")
    if not text:
        raise RuntimeError("Missing timestamp.")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def empty(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def as_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        raise RuntimeError(f"Missing required file: {path.relative_to(ROOT)}")
    with path.open(encoding="utf-8-sig", newline="") as fh:
        r = csv.DictReader(fh)
        f = list(r.fieldnames or [])
        if not f:
            raise RuntimeError(f"CSV has no header: {path.relative_to(ROOT)}")
        return f, list(r)


def write_csv_atomic(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fieldnames})
    tmp.replace(path)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"Missing required file: {path.relative_to(ROOT)}")
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError(f"Expected JSON object: {path.relative_to(ROOT)}")
    return obj


def write_json_atomic(path: Path, obj: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_env(path: Path) -> dict[str, str]:
    out = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def pipeline_token() -> str:
    values = parse_env(WORKER_SECRETS)
    token = os.environ.get("PIPELINE_TOKEN", "").strip() or values.get("PIPELINE_TOKEN", "")
    if not token:
        raise RuntimeError("PIPELINE_TOKEN not available.")
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
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Worker HTTP {exc.code}: {body}") from None
    obj = json.loads(raw)
    if not isinstance(obj, dict) or obj.get("ok") is not True:
        raise RuntimeError(f"Unexpected Worker response: {obj!r}")
    return obj


def fetch_pending_snapshot(token: str) -> tuple[str, list[dict[str, Any]]]:
    snapshot = None
    after_received = None
    after_sig = None
    events: list[dict[str, Any]] = []

    while True:
        params = {"limit": "100"}
        if snapshot:
            params["snapshot_received_at"] = snapshot
        if after_received:
            params["after_received_at"] = after_received
            params["after_signature"] = after_sig
        url = WORKER_ORIGIN + "/internal/pending?" + urllib.parse.urlencode(params)
        obj = http_json(url, token)
        page_snapshot = str(obj.get("snapshot_received_at") or "")
        if not page_snapshot:
            raise RuntimeError("Pending endpoint omitted snapshot_received_at.")
        if snapshot is None:
            snapshot = page_snapshot
        elif snapshot != page_snapshot:
            raise RuntimeError("Pending snapshot changed while paginating.")

        page = obj.get("events") or []
        if not isinstance(page, list):
            raise RuntimeError("Pending endpoint returned non-list events.")
        events.extend(page)

        cursor = obj.get("next_cursor")
        if not cursor:
            break
        after_received = str(cursor.get("after_received_at") or "")
        after_sig = str(cursor.get("after_signature") or "")
        if not after_received or not after_sig:
            raise RuntimeError("Malformed pending cursor.")

    sigs = [str(r.get("signature") or "") for r in events]
    if any(not x for x in sigs) or len(sigs) != len(set(sigs)):
        raise RuntimeError("Pending snapshot contains blank/duplicate signatures.")
    return snapshot, events


def payload_signature(tx: dict[str, Any]) -> str:
    sigs = ((tx.get("transaction") or {}).get("signatures") or [])
    if not sigs:
        raise RuntimeError("Raw transaction has no signature.")
    return str(sigs[0])


def load_effective_state() -> tuple[dict[str, dict[str, str]], dict[str, HeroState]]:
    _, assets = read_csv(ASSETS)
    if len(assets) != ORIGINAL_SUPPLY:
        raise RuntimeError(f"Expected {ORIGINAL_SUPPLY:,} baseline Heroes, found {len(assets):,}.")
    rows = {r["mint"]: dict(r) for r in assets}
    if len(rows) != ORIGINAL_SUPPLY:
        raise RuntimeError("Baseline mint addresses are not unique.")

    _, deltas = read_csv(DELTAS)
    for d in deltas:
        mint = d.get("mint", "")
        if mint not in rows:
            raise RuntimeError(f"hero_deltas.csv has unknown mint {mint!r}")
        for field in STATE_FIELDS:
            rows[mint][field] = d.get(field, "")

    states = {}
    for mint, r in rows.items():
        states[mint] = HeroState(
            mint=mint,
            burned=as_int(r.get("burned")),
            burn_utc=empty(r.get("burn_utc")),
            burn_signature=empty(r.get("burn_signature")),
            current_raw_owner=empty(r.get("current_raw_owner")),
            current_world_staked=as_int(r.get("current_world_staked")),
            current_beneficial_owner=empty(r.get("current_beneficial_owner")),
            current_world_staking_wallet=empty(r.get("current_world_staking_wallet")),
            latest_event_utc=empty(r.get("latest_event_utc")),
            latest_signature=empty(r.get("latest_signature")),
            quest_user_wallet=empty(r.get("quest_user_wallet")),
            quest_staking_wallet=empty(r.get("quest_staking_wallet")),
            current_stake_deposit_utc=empty(r.get("current_stake_deposit_utc")),
            current_stake_deposit_signature=empty(r.get("current_stake_deposit_signature")),
            best_known_last_qualifying_quest_utc=empty(r.get("best_known_last_qualifying_quest_utc")),
            best_known_last_qualifying_quest_signature=empty(r.get("best_known_last_qualifying_quest_signature")),
            quest_history_source=empty(r.get("quest_history_source")),
            deep_history_status=empty(r.get("deep_history_status")),
        )
    return rows, states


def state_to_row(state: HeroState) -> dict[str, Any]:
    d = asdict(state)
    return {k: ("" if d[k] is None else d[k]) for k in DELTA_FIELDS}


def load_cursors() -> dict[str, dict[str, str]]:
    if not HERO_CURSORS.exists():
        return {}
    fields_, rows = read_csv(HERO_CURSORS)
    if fields_ != CURSOR_FIELDS:
        raise RuntimeError("hero_event_cursors.csv schema mismatch.")
    out = {}
    for r in rows:
        mint = r.get("mint", "")
        if not mint or mint in out:
            raise RuntimeError("hero_event_cursors.csv has blank/duplicate mint.")
        out[mint] = r
    return out


def load_event_ledger() -> tuple[list[dict[str, str]], set[str]]:
    if not EVENT_LEDGER.exists():
        return [], set()
    fields_, rows = read_csv(EVENT_LEDGER)
    if fields_ != EVENT_FIELDS:
        raise RuntimeError("webhook_processed_events.csv schema mismatch.")
    sigs = [r.get("signature", "") for r in rows]
    if any(not s for s in sigs) or len(sigs) != len(set(sigs)):
        raise RuntimeError("webhook_processed_events.csv has blank/duplicate signature.")
    return rows, set(sigs)


def world_calls_for_tx(tx: dict[str, Any]) -> list[WorldCall]:
    signers = transaction_signers(tx)
    signer = signers[0] if signers else None
    unique = {}
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


def movement_is_new(
    *,
    mint: str,
    slot: int,
    signature: str,
    block_time: datetime,
    cursor: dict[str, str] | None,
    activation_boundary: datetime,
) -> str:
    """Return APPLY, COVERED_PRE_ACTIVATION, DUPLICATE, or STALE.

    Same-slot/different-signature is unsafe because canonical state does not
    currently persist transaction index within a slot.
    """
    if cursor is None:
        if block_time <= activation_boundary:
            return "COVERED_PRE_ACTIVATION"
        return "APPLY"

    last_slot = int(cursor["last_slot"])
    last_sig = cursor["last_signature"]
    if slot < last_slot:
        return "STALE"
    if slot == last_slot:
        if signature == last_sig:
            return "DUPLICATE"
        raise RuntimeError(
            f"{mint}: same-slot movement conflict at slot {slot}: "
            f"committed={last_sig}, pending={signature}"
        )
    return "APPLY"


def direct_family(marketplace_detail: str) -> str:
    mapping = {
        "Magic Eden V2": "Magic Eden v2",
        "Tensor Marketplace": "Tensor Marketplace",
        "Tensor AMM": "Tensor AMM",
    }
    return mapping.get(marketplace_detail, marketplace_detail)


def fmt_num(value: float) -> str:
    # Stable non-scientific representation without gratuitous trailing zeros.
    text = f"{float(value):.9f}".rstrip("0").rstrip(".")
    return text or "0"


def sale_row(sale, asset: dict[str, str]) -> dict[str, str]:
    dt = sale.block_time.astimezone(timezone.utc)
    utc = dt.strftime("%Y-%m-%d %H:%M:%S.000 UTC")
    hero_num = asset.get("hero_number", "")
    try:
        hero_num = str(int(float(hero_num)))
    except Exception:
        pass
    return {
        "signature": sale.signature,
        "mint": sale.mint,
        "hero_number": hero_num,
        "hero_name": asset.get("hero_name", ""),
        "block_time": str(int(dt.timestamp())),
        "utc": utc,
        "sale_date": dt.strftime("%Y-%m-%d"),
        "sale_month": dt.strftime("%Y-%m"),
        "sale_year": dt.strftime("%Y"),
        "marketplace": sale.marketplace,
        "marketplace_detail": sale.marketplace_detail,
        "marketplace_attribution_method": "direct_program_live",
        "direct_program_families": direct_family(sale.marketplace_detail),
        "buyer": sale.buyer,
        "seller": sale.seller,
        "gross_price_sol": fmt_num(sale.gross_price_sol),
        "gross_price_method": sale.gross_price_method,
        "price_recovered_in_v3": "0",
        "sale_class": "LIVE_DETECTED_SALE",
        "legacy_sale": "0",
        "royalty_90_sol": fmt_num(sale.royalty_90_sol),
        "royalty_10_sol": fmt_num(sale.royalty_10_sol),
        "royalty_total_sol": fmt_num(sale.royalty_total_sol),
        "royalty_paid": str(int(sale.royalty_total_sol > 0)),
    }


def run(label: str, *args: str) -> None:
    print(f"    {label}...")
    cp = subprocess.run([sys.executable, *args], cwd=ROOT)
    if cp.returncode:
        raise RuntimeError(f"{label} failed with exit code {cp.returncode}")


def backup(paths: list[Path], root: Path) -> tuple[dict[Path, Path], set[Path]]:
    copies = {}
    existed = set()
    for p in paths:
        if p.exists():
            existed.add(p)
            target = root / p.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
            copies[p] = target
    return copies, existed


def restore(copies: dict[Path, Path], existed: set[Path], paths: list[Path], original_public: set[str]) -> None:
    for p in paths:
        if p not in existed and p.exists() and p.is_file():
            p.unlink()
    if PUBLIC_DATA.exists():
        for p in PUBLIC_DATA.glob("*.json"):
            if p.name not in original_public:
                p.unlink()
    for p, src in copies.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, p)


def main() -> int:
    print("=" * 78)
    print("Guild Saga — Phase 2J Prepare Live Webhook Batch")
    print("=" * 78)
    print("Canonical changes are transactional.")
    print("D1 acknowledgements: NONE")
    print()

    if PREPARED.exists():
        old = load_json(PREPARED)
        if old.get("status") == "PREPARED" and not old.get("acked_at_utc"):
            raise RuntimeError(
                "An unacknowledged PREPARED batch receipt already exists. "
                "Commit/ACK or explicitly resolve that receipt before preparing another batch."
            )

    token = pipeline_token()
    activation = parse_utc(load_json(ACTIVATION).get("activation_boundary_utc"))

    print("[1/8] Freezing durable D1 pending snapshot")
    snapshot, pending = fetch_pending_snapshot(token)
    snapshot_dt = parse_utc(snapshot)
    print(f"    snapshot received-through:{snapshot}")
    print(f"    pending rows:             {len(pending):,}")

    if not pending:
        print()
        print("[PASS] No pending webhook rows. Canonical/D1 unchanged.")
        return 0

    print("[2/8] Refreshing destination token-account watch frontier")
    # The refresh reads at least as new an inbox snapshot as the one frozen
    # above, updates the remote Helius watch set if needed, and closes any
    # address-switch gap before this batch is eligible for eventual ACK.
    run("watch-frontier refresh", "scripts/refresh_webhook_watch_frontier.py")

    print("[3/8] Loading effective canonical state + durable local ledgers")
    asset_rows, states = load_effective_state()
    start_states = dict(states)
    known_mints = set(states)
    cursors = load_cursors()
    ledger_rows, ledger_sigs = load_event_ledger()

    delta_fields, delta_rows = read_csv(DELTAS)
    market_fields, market_rows = read_csv(MARKET_LIVE)
    if delta_fields != DELTA_FIELDS:
        raise RuntimeError("hero_deltas.csv schema differs from HeroState contract.")

    market_keys = {(r["signature"], r["mint"]) for r in market_rows}
    if len(market_keys) != len(market_rows):
        raise RuntimeError("market_live_sales.csv contains duplicate (signature,mint) rows.")

    checkpoints = load_json(CHECKPOINTS)
    old_hero_checkpoint = parse_utc(checkpoints.get("hero_state_checkpoint"))
    if old_hero_checkpoint > snapshot_dt:
        raise RuntimeError("Hero checkpoint is later than the frozen D1 snapshot.")

    print(f"    effective Heroes:        {len(states):,}")
    print(f"    existing live cursors:   {len(cursors):,}")
    print(f"    processed-event ledger:  {len(ledger_rows):,}")
    print(f"    old Hero checkpoint:     {iso_z(old_hero_checkpoint)}")

    print("[4/8] Validating + reducing frozen raw transactions")
    validated = []
    for row in pending:
        sig = str(row.get("signature") or "")
        raw = row.get("payload_json")
        if not isinstance(raw, str):
            raise RuntimeError(f"{sig}: payload_json is not text.")
        tx = json.loads(raw)
        if not isinstance(tx, dict):
            raise RuntimeError(f"{sig}: payload_json is not an object.")
        if payload_signature(tx) != sig:
            raise RuntimeError(f"{sig}: D1/raw signature mismatch.")
        slot = int(tx.get("slot") or row.get("slot") or 0)
        if slot <= 0:
            raise RuntimeError(f"{sig}: missing/invalid Solana slot.")
        bt = tx.get("blockTime")
        if bt is None:
            raise RuntimeError(f"{sig}: missing blockTime.")
        if row.get("block_time") is not None and int(row["block_time"]) != int(bt):
            raise RuntimeError(f"{sig}: D1/raw block_time mismatch.")
        received = str(row.get("received_at") or "")
        if not received or parse_utc(received) > snapshot_dt:
            raise RuntimeError(f"{sig}: row falls outside frozen received_at snapshot.")
        validated.append((slot, int(bt), sig, received, tx))

    # Chain slot is authoritative for post-activation movement ordering.
    validated.sort(key=lambda x: (x[0], x[2]))

    counts = Counter()
    unresolved = []
    new_event_rows = []
    new_sales = []
    batch_signatures = []
    applied_mints = set()

    for slot, bt, sig, received, tx in validated:
        batch_signatures.append(sig)

        if sig in ledger_sigs:
            # This can happen if canonical preparation succeeded but D1 was not
            # ACKed. Do not reduce it twice; include it in the next ACK receipt.
            counts["ALREADY_COMMITTED_LOCAL"] += 1
            continue

        success = (tx.get("meta") or {}).get("err") is None
        hero_movements = []
        world_labels = []
        quest_updates = 0
        sale_labels = []

        if success:
            try:
                calls = world_calls_for_tx(tx)
            except Exception as exc:
                unresolved.append(f"{sig}: World Mode decode failed: {exc}")
                calls = []

            world_labels = [x.action for x in calls]
            for wc in calls:
                counts[f"WORLD_{wc.action}"] += 1

            try:
                movements = normalize_token_movements(tx, known_mints)
            except Exception as exc:
                unresolved.append(f"{sig}: movement normalization failed: {exc}")
                movements = []

            try:
                sales = decode_sales(normalize_transaction(tx), known_mints)
            except Exception as exc:
                unresolved.append(f"{sig}: market decoder failed: {exc}")
                sales = []

            sale_mints = {s.mint for s in sales}

            matched_world = set()
            movement_mints = set()
            for move in movements:
                movement_mints.add(move.mint)
                hero_movements.append(f"{move.mint}:{move.classification}")

                if move.classification == "TRANSFER":
                    if move.from_owner is None or move.to_owner is None:
                        unresolved.append(
                            f"{sig} {move.mint}: ambiguous TRANSFER "
                            f"{move.from_owners}->{move.to_owners}"
                        )
                        continue

                    # A listing/custody deposit needs a dedicated custody fixture,
                    # not the ordinary-transfer beneficial-owner rule.
                    if move.to_owner == ME_V2_CUSTODY and move.mint not in sale_mints:
                        unresolved.append(
                            f"{sig} {move.mint}: transfer into Magic Eden V2 custody "
                            "requires custody-semantic handling before publish"
                        )
                        continue

                    wc = choose_world_call(move, calls)
                    if wc:
                        matched_world.add((wc.action, wc.user_wallet, wc.staking_wallet))

                    status = movement_is_new(
                        mint=move.mint,
                        slot=slot,
                        signature=sig,
                        block_time=move.block_time,
                        cursor=cursors.get(move.mint),
                        activation_boundary=activation,
                    )
                    counts[f"MOVEMENT_{status}"] += 1
                    if status == "APPLY":
                        rm = RawMovement(
                            sig, move.mint, move.block_time,
                            "transfer", move.from_owner, move.to_owner
                        )
                        states[move.mint] = apply_movement(states[move.mint], rm, wc)
                        cursors[move.mint] = {
                            "mint": move.mint,
                            "last_slot": str(slot),
                            "last_signature": sig,
                            "last_block_time_utc": iso_z(move.block_time),
                        }
                        applied_mints.add(move.mint)
                        counts["TRANSFER"] += 1
                        if wc:
                            counts[f"HERO_{wc.action}"] += 1

                elif move.classification == "BURN_OR_SEND_TO_ZERO":
                    if not transaction_has_burn_instruction(tx, move.mint):
                        unresolved.append(
                            f"{sig} {move.mint}: sender-only NFT movement has no explicit Burn/BurnChecked"
                        )
                        continue
                    if move.from_owner is None:
                        unresolved.append(f"{sig} {move.mint}: ambiguous burn source.")
                        continue

                    status = movement_is_new(
                        mint=move.mint,
                        slot=slot,
                        signature=sig,
                        block_time=move.block_time,
                        cursor=cursors.get(move.mint),
                        activation_boundary=activation,
                    )
                    counts[f"MOVEMENT_{status}"] += 1
                    if status == "APPLY":
                        rm = RawMovement(sig, move.mint, move.block_time, "burn", move.from_owner, None)
                        states[move.mint] = apply_movement(states[move.mint], rm)
                        cursors[move.mint] = {
                            "mint": move.mint,
                            "last_slot": str(slot),
                            "last_signature": sig,
                            "last_block_time_utc": iso_z(move.block_time),
                        }
                        applied_mints.add(move.mint)
                        counts["BURN"] += 1

                elif move.classification == "MINT_OR_RECEIVE_FROM_ZERO":
                    unresolved.append(
                        f"{sig} {move.mint}: unexpected receive-from-zero after production cutover"
                    )

            # A Stake/Unstake instruction is only accepted if its validated Hero
            # custody movement occurred in the same transaction.
            for wc in calls:
                key = (wc.action, wc.user_wallet, wc.staking_wallet)
                if wc.action in {"STAKE", "UNSTAKE"} and key not in matched_world:
                    unresolved.append(
                        f"{sig}: {wc.action} call had no matching Guild Hero custody movement"
                    )

            for wc in calls:
                if wc.action == "QUEST_RESTART":
                    changed = apply_quest_restart_to_collection(states, wc)
                    quest_updates += changed
                    counts["QUEST_HERO_UPDATES"] += changed
                    if changed:
                        for mint, state in states.items():
                            if state != start_states[mint]:
                                applied_mints.add(mint)

            # A decoded direct-program sale must coincide with an observable
            # Guild Hero token movement in the raw transaction.
            for sale in sales:
                if sale.mint not in movement_mints:
                    unresolved.append(
                        f"{sig} {sale.mint}: decoded sale has no raw Guild Hero movement"
                    )
                    continue
                key = (sale.signature, sale.mint)
                if key not in market_keys:
                    new_sales.append(sale_row(sale, asset_rows[sale.mint]))
                    market_keys.add(key)
                    counts["SALE"] += 1
                else:
                    counts["SALE_DUPLICATE"] += 1
                sale_labels.append(f"{sale.mint}:{sale.marketplace_detail}")

        else:
            counts["FAILED_TX"] += 1

        new_event_rows.append({
            "signature": sig,
            "slot": str(slot),
            "block_time_utc": iso_z(datetime.fromtimestamp(bt, timezone.utc)),
            "received_at": received,
            "tx_success": "1" if success else "0",
            "hero_movements": "|".join(hero_movements),
            "world_calls": "|".join(world_labels),
            "quest_hero_updates": str(quest_updates),
            "market_sales": "|".join(sale_labels),
            "source": "HELIUS_D1_RAW",
        })

    if unresolved:
        print(f"    unresolved cases:        {len(unresolved):,}")
        for item in unresolved[:30]:
            print(f"      - {item}")
        raise RuntimeError(
            "Frozen batch contains unresolved chain semantics. "
            "Nothing canonical was written and nothing will be ACKed."
        )

    changed_states = {
        mint: state for mint, state in states.items()
        if state != start_states[mint]
    }
    print(f"    transactions validated:  {len(validated):,}")
    print(f"    failed transactions:     {counts['FAILED_TX']:,}")
    print(f"    Hero transfers applied:  {counts['TRANSFER']:,}")
    print(f"    explicit burns applied:  {counts['BURN']:,}")
    print(f"    Hero stake / unstake:    {counts['HERO_STAKE']:,} / {counts['HERO_UNSTAKE']:,}")
    print(f"    Quest Restart calls:     {counts['WORLD_QUEST_RESTART']:,}")
    print(f"    Quest Hero updates:      {counts['QUEST_HERO_UPDATES']:,}")
    print(f"    market sales added:      {counts['SALE']:,}")
    print(f"    Hero rows changed:       {len(changed_states):,}")

    print("[5/8] Preparing transactional canonical files")
    target_paths = [
        DELTAS, MARKET_LIVE, CHECKPOINTS, HERO_CURSORS, EVENT_LEDGER,
        *sorted(PUBLIC_DATA.glob("*.json")),
    ]
    original_public = {p.name for p in PUBLIC_DATA.glob("*.json")}

    with tempfile.TemporaryDirectory(prefix="gs-phase2j-") as tmpdir:
        backups, existed = backup(target_paths, Path(tmpdir))

        try:
            delta_by_mint = {r["mint"]: r for r in delta_rows}
            if len(delta_by_mint) != len(delta_rows):
                raise RuntimeError("hero_deltas.csv has duplicate mints.")
            for mint, state in changed_states.items():
                delta_by_mint[mint] = state_to_row(state)
            write_csv_atomic(
                DELTAS, DELTA_FIELDS,
                [delta_by_mint[m] for m in sorted(delta_by_mint)]
            )

            market_rows.extend(new_sales)
            market_rows.sort(key=lambda r: (
                parse_utc(r["utc"]).timestamp(),
                r["signature"],
                r["mint"],
            ))
            write_csv_atomic(MARKET_LIVE, market_fields, market_rows)

            cursor_rows = [cursors[m] for m in sorted(cursors)]
            write_csv_atomic(HERO_CURSORS, CURSOR_FIELDS, cursor_rows)

            ledger_rows.extend(new_event_rows)
            ledger_rows.sort(key=lambda r: (int(r["slot"]), r["signature"]))
            write_csv_atomic(EVENT_LEDGER, EVENT_FIELDS, ledger_rows)

            checkpoints["hero_state_checkpoint"] = snapshot
            checkpoints["market_checkpoint_date"] = snapshot[:10]
            checkpoints["market_checkpoint_utc"] = snapshot
            checkpoints["webhook_inbox_checkpoint"] = snapshot
            checkpoints["notes"] = (
                "Independent production state. Hero and market domains are reduced "
                "from the durable Helius raw-webhook D1 inbox. hero_state_checkpoint "
                "and webhook_inbox_checkpoint are received-through watermarks; late "
                "chain events remain eligible for later idempotent processing. "
                "Floor/listings retains its independent checkpoint."
            )
            write_json_atomic(CHECKPOINTS, checkpoints)

            print("[6/8] Rebuilding public dashboard JSON")
            run("dashboard builder", "scripts/build_dashboard_data.py")

            print("[7/8] Running full regression/live validation")
            run("offline unit tests", "-m", "unittest", "discover", "-s", "tests", "-v")
            run("frozen cutover validator", "scripts/validate_cutover.py")
            run("live production validator", "scripts/validate_live.py")

            # Catch accidental whitespace/conflict-marker issues when Git is available.
            try:
                cp = subprocess.run(
                    ["git", "diff", "--check"],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                if cp.returncode:
                    raise RuntimeError("git diff --check failed:\n" + cp.stdout)
            except FileNotFoundError:
                pass

            print("[8/8] Writing unacknowledged PREPARED receipt")
            summary = load_json(PUBLIC_DATA / "summary.json")
            hero_json = load_json(PUBLIC_DATA / "hero-state.json")
            market_json = load_json(PUBLIC_DATA / "market-history.json")

            if parse_utc(hero_json.get("as_of")) != snapshot_dt:
                raise RuntimeError("hero-state.json did not advance to the frozen received-through snapshot.")
            if str(market_json.get("as_of") or "") != snapshot[:10]:
                raise RuntimeError("market-history.json did not advance to the frozen snapshot date.")

            canonical_hashes = {}
            for p in [DELTAS, MARKET_LIVE, CHECKPOINTS, HERO_CURSORS, EVENT_LEDGER]:
                canonical_hashes[str(p.relative_to(ROOT))] = sha256(p)

            receipt = {
                "status": "PREPARED",
                "prepared_at_utc": iso_z(datetime.now(timezone.utc)),
                "snapshot_received_at": snapshot,
                "activation_boundary_utc": iso_z(activation),
                "signatures": batch_signatures,
                "signature_count": len(batch_signatures),
                "already_committed_local": counts["ALREADY_COMMITTED_LOCAL"],
                "counts": dict(counts),
                "changed_hero_rows": len(changed_states),
                "new_market_sales": len(new_sales),
                "canonical_hashes": canonical_hashes,
                "d1_acknowledged": False,
                "acked_at_utc": None,
            }
            RECON.mkdir(parents=True, exist_ok=True)
            write_json_atomic(PREPARED, receipt)

        except Exception:
            restore(backups, existed, target_paths, original_public)
            if PREPARED.exists():
                PREPARED.unlink()
            print()
            print("[ROLLBACK] Canonical/public batch writes were restored.")
            print("D1 events remain pending.")
            raise

    hero_kpis = summary.get("hero") or {}
    market_kpis = summary.get("market") or {}

    print()
    print("=" * 78)
    print("[PASS] LIVE WEBHOOK BATCH PREPARED + VALIDATED — D1 STILL PENDING")
    print("=" * 78)
    print(f"Snapshot received-through:  {snapshot}")
    print(f"Transactions in batch:      {len(batch_signatures):,}")
    print(f"Hero transfers applied:     {counts['TRANSFER']:,}")
    print(f"Explicit burns applied:     {counts['BURN']:,}")
    print(f"Hero stake / unstake:       {counts['HERO_STAKE']:,} / {counts['HERO_UNSTAKE']:,}")
    print(f"Quest Hero updates:         {counts['QUEST_HERO_UPDATES']:,}")
    print(f"Market sales added:         {counts['SALE']:,}")
    print(f"Hero rows changed:          {len(changed_states):,}")
    print(f"Active / burned:            {hero_kpis.get('active_supply')} / {hero_kpis.get('burned')}")
    print(f"Beneficial holders:         {hero_kpis.get('beneficial_holders')}")
    print(f"Staked Heroes:              {hero_kpis.get('staked_heroes')}")
    print(f"Secondary sales:            {market_kpis.get('secondary_sales')}")
    print(f"Hero checkpoint:            {snapshot}")
    print(f"Market checkpoint:          {snapshot[:10]}")
    print("Offline tests:              PASS")
    print("Frozen cutover validator:   PASS")
    print("Live validator:             PASS")
    print("D1 events acknowledged:     NONE")
    print(f"Prepared receipt:           {PREPARED.relative_to(ROOT)}")
    print()
    print("Do not ACK the D1 batch yet. The next stage commits/pushes this exact")
    print("validated state before those signatures are marked processed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print()
        print(f"[FAIL] {exc}")
        print("D1 events were not acknowledged.")
        raise SystemExit(1)
