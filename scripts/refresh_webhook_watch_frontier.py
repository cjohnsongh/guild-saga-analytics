#!/usr/bin/env python3
"""Persist and refresh the production Helius token-account watch frontier.

Why this exists:
- The activation webhook watches every mint plus the 9,832 token accounts proven
  by Phase 1L.
- A real post-cutover Hero transfer creates/moves the NFT into a new token
  account. That new account must be added to the webhook frontier for durable
  future raw-transfer discovery.
- Old token accounts stay in the watch set; we only add, never remove, so there
  is no blind spot caused by switching from one token account to another.

Safety:
- Does NOT modify Hero/market canonical state or checkpoints.
- Does NOT acknowledge D1 events.
- Bootstraps a tracked data/state/webhook_token_accounts.csv from the audited
  Phase 1L reconciliation DB, then overlays token accounts observed in the
  currently pending raw webhook inbox.
- Updates the existing Helius webhook only after full local validation.
- After the webhook edit, it scans every newly-added token account for any
  signatures newer than the transfer that introduced it. Missing raw tx are
  fetched from Helius RPC and injected into the same durable Worker inbox before
  the run can PASS.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collector.constants import ORIGINAL_SUPPLY, WORLD_MODE_PROGRAM, ROYALTY_90_ADDRESS, ROYALTY_10_ADDRESS
from collector.solana_normalize import transaction_account_keys

WORKER_ORIGIN = "https://guild-saga-webhook-inbox.cjohnson80.workers.dev"
WEBHOOK_PATH = "/webhooks/helius"
HELIUS_API_ORIGIN = "https://api-mainnet.helius-rpc.com"
HELIUS_RPC_ORIGIN = "https://mainnet.helius-rpc.com/"
MAX_WEBHOOK_ADDRESSES = 100_000

ASSETS = ROOT / "data" / "baseline" / "assets.csv"
DELTAS = ROOT / "data" / "state" / "hero_deltas.csv"
FRONTIER = ROOT / "data" / "state" / "webhook_token_accounts.csv"
RECON_DB = ROOT / ".guild_saga_recon" / "cutover_free_backfill.sqlite"
RECEIPT = ROOT / ".guild_saga_recon" / "helius_webhook_setup.json"
WORKER_SECRETS = ROOT / "cloudflare" / "webhook-inbox" / ".env.worker-secrets.local"
OUT = ROOT / ".guild_saga_recon" / "webhook_frontier_refresh.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
FRONTIER_FIELDS = [
    "mint",
    "token_account",
    "first_observed_utc",
    "last_observed_utc",
    "is_current",
    "source",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_utc(value: Any) -> datetime:
    text = str(value or "").strip().replace(" UTC", "+00:00").replace("Z", "+00:00")
    if not text:
        raise RuntimeError("Missing timestamp")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_block_time(value: int) -> str:
    return datetime.fromtimestamp(int(value), timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv_atomic(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})
    tmp.replace(path)


def parse_env_file(path: Path) -> dict[str, str]:
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


def load_worker_secrets() -> tuple[str, str]:
    values = parse_env_file(WORKER_SECRETS)
    helius_auth = os.environ.get("HELIUS_WEBHOOK_AUTH") or values.get("HELIUS_WEBHOOK_AUTH")
    pipeline = os.environ.get("PIPELINE_TOKEN") or values.get("PIPELINE_TOKEN")
    if not helius_auth or not pipeline:
        raise RuntimeError("Missing HELIUS_WEBHOOK_AUTH / PIPELINE_TOKEN.")
    return helius_auth, pipeline


def load_helius_key() -> str:
    value = os.environ.get("HELIUS_API_KEY", "").strip()
    if value:
        return value
    for path in (ROOT / "keys.txt", ROOT / "key.txt"):
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            low = line.lower()
            if low.startswith("alch"):
                continue
            if low.startswith("helius_api_key=") or low.startswith("helius="):
                return line.split("=", 1)[1].strip()
            if "=" not in line:
                return line
    raise RuntimeError("Helius API key not found.")


def http_json(method: str, url: str, *, headers=None, body=None, timeout=90):
    hdr = {"Accept": "application/json", "User-Agent": UA}
    if headers:
        hdr.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        hdr["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdr, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {raw[:1200]}") from None
    try:
        payload = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        raise RuntimeError(f"HTTP {status} returned non-JSON: {raw[:500]}") from None
    return status, payload


def rpc(api_key: str, method: str, params: list[Any]) -> Any:
    url = HELIUS_RPC_ORIGIN + "?" + urllib.parse.urlencode({"api-key": api_key})
    _, payload = http_json(
        "POST",
        url,
        body={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=90,
    )
    if not isinstance(payload, dict):
        raise RuntimeError(f"{method}: non-object RPC response")
    if payload.get("error"):
        raise RuntimeError(f"{method}: RPC error {payload['error']}")
    return payload.get("result")


def effective_mints() -> tuple[set[str], set[str]]:
    rows = read_csv(ASSETS)
    if len(rows) != ORIGINAL_SUPPLY:
        raise RuntimeError(f"Expected {ORIGINAL_SUPPLY:,} baseline Heroes, found {len(rows):,}.")
    burned = {r["mint"]: str(r.get("burned") or "").strip() in {"1", "true", "True"} for r in rows}
    for r in read_csv(DELTAS):
        mint = r.get("mint", "")
        if mint not in burned:
            raise RuntimeError(f"Unknown delta mint {mint}")
        if str(r.get("burned") or "").strip() != "":
            burned[mint] = str(r.get("burned") or "").strip() in {"1", "true", "True"}
    all_mints = set(burned)
    active = {m for m, is_burned in burned.items() if not is_burned}
    return all_mints, active


def bootstrap_frontier(active_mints: set[str], activation_utc: str) -> list[dict[str, Any]]:
    if FRONTIER.exists():
        rows = read_csv(FRONTIER)
        if not rows:
            raise RuntimeError("Existing webhook_token_accounts.csv is empty.")
        return rows

    if not RECON_DB.exists():
        raise RuntimeError("Missing Phase 1L reconciliation DB required for one-time frontier bootstrap.")

    mapping: dict[str, str] = {}
    con = sqlite3.connect(f"file:{RECON_DB.as_posix()}?mode=ro", uri=True)
    try:
        for mint, token_account in con.execute(
            "SELECT mint, token_account FROM owner_holdings "
            "WHERE token_account IS NOT NULL AND token_account<>''"
        ):
            if mint in active_mints:
                mapping[str(mint)] = str(token_account)
        for mint, token_account in con.execute(
            "SELECT mint, token_account FROM chain_snapshot "
            "WHERE complete=1 AND supply_zero=0 AND token_account IS NOT NULL AND token_account<>''"
        ):
            if mint in active_mints and mint not in mapping:
                mapping[str(mint)] = str(token_account)
    finally:
        con.close()

    missing = sorted(active_mints - set(mapping))
    if missing:
        raise RuntimeError(f"Frontier bootstrap missing {len(missing):,} active mints; first {missing[:3]}")
    if len(mapping) != len(active_mints):
        raise RuntimeError("Frontier bootstrap cardinality mismatch.")
    if len(set(mapping.values())) != len(mapping):
        raise RuntimeError("Frontier bootstrap has duplicate current token accounts.")

    return [
        {
            "mint": mint,
            "token_account": mapping[mint],
            "first_observed_utc": activation_utc,
            "last_observed_utc": activation_utc,
            "is_current": "1",
            "source": "PHASE1L_ACTIVATION_FRONTIER",
        }
        for mint in sorted(mapping)
    ]


def fetch_pending_snapshot(pipeline_token: str) -> tuple[str, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    snapshot = None
    after_received = None
    after_sig = None

    while True:
        params = {"limit": "100"}
        if snapshot:
            params["snapshot_received_at"] = snapshot
        if after_received:
            params["after_received_at"] = after_received
            params["after_signature"] = after_sig
        url = WORKER_ORIGIN + "/internal/pending?" + urllib.parse.urlencode(params)
        _, payload = http_json(
            "GET",
            url,
            headers={"Authorization": f"Bearer {pipeline_token}"},
        )
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise RuntimeError(f"Unexpected pending response: {payload!r}")
        page_snapshot = str(payload.get("snapshot_received_at") or "")
        if not page_snapshot:
            raise RuntimeError("Pending response missing snapshot_received_at.")
        if snapshot is None:
            snapshot = page_snapshot
        elif snapshot != page_snapshot:
            raise RuntimeError("Pending pagination snapshot changed mid-read.")

        page = payload.get("events") or []
        if not isinstance(page, list):
            raise RuntimeError("Pending response events is not a list.")
        events.extend(page)

        cursor = payload.get("next_cursor")
        if not cursor:
            break
        after_received = str(cursor.get("after_received_at") or "")
        after_sig = str(cursor.get("after_signature") or "")
        if not after_received or not after_sig:
            raise RuntimeError("Malformed pending pagination cursor.")

    sigs = [str(x.get("signature") or "") for x in events]
    if any(not s for s in sigs) or len(sigs) != len(set(sigs)):
        raise RuntimeError("Pending snapshot contains blank/duplicate signatures.")
    return snapshot or utc_now(), events


def positive_accounts(tx: dict[str, Any], known_mints: set[str], key: str) -> dict[str, set[str]]:
    keys = transaction_account_keys(tx)
    meta = tx.get("meta") or {}
    out: dict[str, set[str]] = defaultdict(set)
    for row in meta.get(key) or []:
        mint = str(row.get("mint") or "")
        if mint not in known_mints:
            continue
        idx = row.get("accountIndex")
        if not isinstance(idx, int) or not (0 <= idx < len(keys)):
            continue
        try:
            amount = int(str(((row.get("uiTokenAmount") or {}).get("amount")) or "0"))
        except Exception:
            amount = 0
        if amount == 1:
            out[mint].add(keys[idx])
    return out


def overlay_pending_frontier(
    rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
    known_mints: set[str],
    active_mints: set[str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    by_key = {(r["mint"], r["token_account"]): dict(r) for r in rows}
    if len(by_key) != len(rows):
        raise RuntimeError("Frontier file contains duplicate mint/token_account rows.")

    current: dict[str, str] = {}
    for r in rows:
        if str(r.get("is_current") or "") == "1":
            mint = r["mint"]
            if mint in current:
                raise RuntimeError(f"Frontier has multiple current token accounts for {mint}")
            current[mint] = r["token_account"]

    if set(current) != active_mints:
        missing = sorted(active_mints - set(current))
        extra = sorted(set(current) - active_mints)
        raise RuntimeError(f"Current frontier != active Hero set; missing={missing[:3]} extra={extra[:3]}")

    introduced: dict[str, dict[str, Any]] = {}
    parsed_events = []
    for row in events:
        raw = row.get("payload_json")
        if not isinstance(raw, str):
            raise RuntimeError(f"{row.get('signature')}: payload_json missing")
        tx = json.loads(raw)
        if not isinstance(tx, dict):
            raise RuntimeError(f"{row.get('signature')}: payload_json is not an object")
        bt = tx.get("blockTime")
        if bt is None:
            raise RuntimeError(f"{row.get('signature')}: no blockTime")
        slot = int(tx.get("slot") or row.get("slot") or 0)
        parsed_events.append((int(bt), slot, str(row["signature"]), tx))

    parsed_events.sort(key=lambda x: (x[0], x[1], x[2]))

    for bt, slot, sig, tx in parsed_events:
        if (tx.get("meta") or {}).get("err") is not None:
            continue
        when = iso_block_time(bt)
        pre = positive_accounts(tx, known_mints, "preTokenBalances")
        post = positive_accounts(tx, known_mints, "postTokenBalances")

        for mint in sorted(set(pre) | set(post)):
            before = pre.get(mint, set())
            after = post.get(mint, set())
            if len(before) > 1 or len(after) > 1:
                raise RuntimeError(
                    f"{sig} {mint}: expected <=1 positive NFT token account; pre={sorted(before)} post={sorted(after)}"
                )
            before_acct = next(iter(before), None)
            after_acct = next(iter(after), None)

            # No token-account identity change.
            if before_acct == after_acct:
                continue

            if before_acct:
                key = (mint, before_acct)
                existing = by_key.get(key)
                if existing is None:
                    # Preserve every observed historical token account.
                    existing = {
                        "mint": mint,
                        "token_account": before_acct,
                        "first_observed_utc": when,
                        "last_observed_utc": when,
                        "is_current": "0",
                        "source": "HELIUS_RAW",
                    }
                    by_key[key] = existing
                    introduced.setdefault(before_acct, {
                        "mint": mint,
                        "token_account": before_acct,
                        "first_seen_signature": sig,
                        "first_seen_block_time": bt,
                    })
                existing["last_observed_utc"] = when
                existing["is_current"] = "0"
                if current.get(mint) == before_acct:
                    current.pop(mint, None)

            if after_acct:
                key = (mint, after_acct)
                existing = by_key.get(key)
                if existing is None:
                    existing = {
                        "mint": mint,
                        "token_account": after_acct,
                        "first_observed_utc": when,
                        "last_observed_utc": when,
                        "is_current": "1",
                        "source": "HELIUS_RAW",
                    }
                    by_key[key] = existing
                    introduced.setdefault(after_acct, {
                        "mint": mint,
                        "token_account": after_acct,
                        "first_seen_signature": sig,
                        "first_seen_block_time": bt,
                    })
                else:
                    existing["last_observed_utc"] = when
                    existing["is_current"] = "1"
                current[mint] = after_acct

    # Pending events may include burns, so current rows are allowed to be fewer
    # than the current canonical active set until the Hero batch itself is applied.
    # But no mint may have two current token accounts.
    current_counts = defaultdict(int)
    for r in by_key.values():
        if str(r.get("is_current") or "") == "1":
            current_counts[r["mint"]] += 1
    bad = [m for m, n in current_counts.items() if n > 1]
    if bad:
        raise RuntimeError(f"Multiple current token accounts after overlay: {bad[:3]}")

    final_rows = [by_key[k] for k in sorted(by_key)]
    return final_rows, introduced


def webhook_url(api_key: str, webhook_id: str) -> str:
    return (
        f"{HELIUS_API_ORIGIN}/v0/webhooks/{webhook_id}?"
        + urllib.parse.urlencode({"api-key": api_key})
    )


def get_webhook(api_key: str, webhook_id: str) -> dict[str, Any]:
    _, payload = http_json("GET", webhook_url(api_key, webhook_id))
    if not isinstance(payload, dict):
        raise RuntimeError("Helius get-webhook returned unexpected payload.")
    return payload


def update_webhook(api_key: str, auth: str, webhook_id: str, addresses: list[str], expected_url: str) -> dict[str, Any]:
    body = {
        "webhookURL": expected_url,
        "transactionTypes": ["ANY"],
        "accountAddresses": addresses,
        "webhookType": "raw",
        "authHeader": auth,
    }
    _, payload = http_json("PUT", webhook_url(api_key, webhook_id), body=body, timeout=120)
    if not isinstance(payload, dict):
        raise RuntimeError("Helius update-webhook returned unexpected payload.")
    return payload


def list_newer_signatures(api_key: str, token_account: str, first_sig: str, first_bt: int) -> list[str]:
    """Return signatures after the introducing transfer until that signature/time is reached."""
    found: list[str] = []
    before = None
    for _page in range(20):
        cfg: dict[str, Any] = {"limit": 100, "commitment": "finalized"}
        if before:
            cfg["before"] = before
        result = rpc(api_key, "getSignaturesForAddress", [token_account, cfg])
        if not isinstance(result, list):
            raise RuntimeError(f"{token_account}: getSignaturesForAddress returned unexpected data")
        if not result:
            break

        stop = False
        for item in result:
            if not isinstance(item, dict):
                continue
            sig = str(item.get("signature") or "")
            bt = item.get("blockTime")
            if sig == first_sig:
                stop = True
                break
            if bt is not None and int(bt) < first_bt:
                stop = True
                break
            if sig:
                found.append(sig)
        if stop or len(result) < 100:
            break
        before = str(result[-1].get("signature") or "")
        if not before:
            break
    else:
        raise RuntimeError(f"{token_account}: gap audit exceeded 20 pages")
    return list(dict.fromkeys(found))


def fetch_tx(api_key: str, signature: str) -> dict[str, Any]:
    tx = rpc(
        api_key,
        "getTransaction",
        [
            signature,
            {
                "encoding": "json",
                "commitment": "finalized",
                "maxSupportedTransactionVersion": 0,
            },
        ],
    )
    if not isinstance(tx, dict):
        raise RuntimeError(f"{signature}: getTransaction returned no transaction")
    return tx


def inject_raw(helius_auth: str, txs: list[dict[str, Any]]) -> None:
    if not txs:
        return
    _, payload = http_json(
        "POST",
        WORKER_ORIGIN + WEBHOOK_PATH,
        headers={"Authorization": helius_auth},
        body=txs,
        timeout=90,
    )
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError(f"Worker replay injection failed: {payload!r}")


def sha_watch(addresses: list[str]) -> str:
    return hashlib.sha256(("\n".join(addresses) + "\n").encode()).hexdigest()


def main() -> int:
    print("=" * 78)
    print("Guild Saga — Phase 2I Persistent Webhook Token-Account Frontier")
    print("=" * 78)
    print("Hero/market canonical files: UNCHANGED")
    print("D1 acknowledgements:         NONE")
    print()

    print("[1/7] Loading production evidence and credentials")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if receipt.get("active") is not True:
        raise RuntimeError("Helius activation receipt is not active.")
    webhook_id = str(receipt.get("webhook_id") or "")
    expected_url = str(receipt.get("webhook_url") or "")
    activation = str(receipt.get("activation_boundary_utc") or "")
    if not webhook_id or not expected_url or not activation:
        raise RuntimeError("Activation receipt is incomplete.")
    helius_auth, pipeline_token = load_worker_secrets()
    api_key = load_helius_key()
    all_mints, active_mints = effective_mints()
    print(f"    collection mints:        {len(all_mints):,}")
    print(f"    canonical active Heroes: {len(active_mints):,}")
    print(f"    webhook ID:              {webhook_id}")

    print("[2/7] Bootstrapping/loading tracked token-account frontier")
    existed = FRONTIER.exists()
    frontier = bootstrap_frontier(active_mints, activation)
    initial_accounts = {r["token_account"] for r in frontier}
    initial_current = sum(str(r.get("is_current") or "") == "1" for r in frontier)
    print(f"    source:                  {'tracked file' if existed else 'Phase 1L reconciliation DB'}")
    print(f"    historical accounts:     {len(initial_accounts):,}")
    print(f"    current accounts:        {initial_current:,}")

    print("[3/7] Reading stable pending-inbox snapshot")
    snapshot, events = fetch_pending_snapshot(pipeline_token)
    print(f"    snapshot:                {snapshot}")
    print(f"    pending rows:            {len(events):,}")

    print("[4/7] Overlaying token accounts observed in pending raw transactions")
    final_rows, introduced = overlay_pending_frontier(frontier, events, all_mints, active_mints)
    final_accounts = {r["token_account"] for r in final_rows}
    additions = sorted(final_accounts - initial_accounts)
    print(f"    newly observed accounts: {len(additions):,}")
    for acct in additions[:10]:
        info = introduced.get(acct, {})
        print(f"      + {acct}  mint={info.get('mint','')}")
    if len(additions) > 10:
        print(f"      ... plus {len(additions)-10:,} more")

    print("[5/7] Reconciling Helius production webhook watch set")
    special = {WORLD_MODE_PROGRAM, ROYALTY_90_ADDRESS, ROYALTY_10_ADDRESS}
    target = sorted(all_mints | final_accounts | special)
    if len(target) > MAX_WEBHOOK_ADDRESSES:
        raise RuntimeError(f"Target watch set {len(target):,} exceeds Helius limit.")

    remote_before = get_webhook(api_key, webhook_id)
    if remote_before.get("active") is not True:
        raise RuntimeError("Production Helius webhook is not active.")
    if remote_before.get("webhookURL") != expected_url:
        raise RuntimeError("Production Helius webhook URL changed unexpectedly.")
    remote_set = set(map(str, remote_before.get("accountAddresses") or []))
    missing_remote = sorted(set(target) - remote_set)
    unexpected_remote = sorted(remote_set - set(target))
    if unexpected_remote:
        # Never silently remove an address from a production watch set.
        raise RuntimeError(
            f"Remote webhook has {len(unexpected_remote)} addresses absent from the audited target; "
            f"refusing destructive replacement. First: {unexpected_remote[:3]}"
        )

    edit_started = utc_now()
    if missing_remote:
        update_webhook(api_key, helius_auth, webhook_id, target, expected_url)
        remote_after = get_webhook(api_key, webhook_id)
        returned = set(map(str, remote_after.get("accountAddresses") or []))
        if returned != set(target) or remote_after.get("active") is not True:
            raise RuntimeError("Post-edit Helius watch-set verification failed.")
        print(f"    addresses added remotely:{len(missing_remote):>6,}")
    else:
        print("    remote watch set:        already exact")
    print(f"    target watch addresses:  {len(target):,}")
    print(f"    watch-set SHA-256:        {sha_watch(target)[:16]}…")

    print("[6/7] Closing any token-account watch-switch gap")
    pending_sigs = {str(r.get("signature") or "") for r in events}
    backfilled: list[str] = []
    checked = 0
    # Only accounts absent from the *previous* remote watch set need a gap audit.
    gap_accounts = sorted(set(target) - remote_set)
    for acct in gap_accounts:
        info = introduced.get(acct)
        if not info:
            # This can occur only during first bootstrap if the activation remote
            # set were unexpectedly incomplete. Refuse to invent a safe start.
            raise RuntimeError(f"No introducing transaction known for newly watched account {acct}")
        checked += 1
        newer = list_newer_signatures(
            api_key,
            acct,
            str(info["first_seen_signature"]),
            int(info["first_seen_block_time"]),
        )
        missing = [sig for sig in newer if sig not in pending_sigs]
        if missing:
            txs = [fetch_tx(api_key, sig) for sig in missing]
            inject_raw(helius_auth, txs)
            backfilled.extend(missing)
    print(f"    newly watched accounts checked: {checked:,}")
    print(f"    gap transactions replayed:      {len(backfilled):,}")

    if backfilled:
        _snapshot2, events2 = fetch_pending_snapshot(pipeline_token)
        now_pending = {str(r.get("signature") or "") for r in events2}
        absent = sorted(set(backfilled) - now_pending)
        if absent:
            raise RuntimeError(f"Gap replay signatures are not durable in D1: {absent[:3]}")

    print("[7/7] Persisting audited tracked frontier")
    # All network/update/gap checks passed; only now make the tracked repo file.
    write_csv_atomic(FRONTIER, final_rows, FRONTIER_FIELDS)
    if len({r["token_account"] for r in read_csv(FRONTIER)}) != len(final_accounts):
        raise RuntimeError("Persisted frontier verification failed.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "status": "PASS",
                "refreshed_at_utc": utc_now(),
                "pending_snapshot_received_at": snapshot,
                "webhook_id": webhook_id,
                "remote_addresses_before": len(remote_set),
                "target_addresses": len(target),
                "newly_observed_token_accounts": additions,
                "remote_addresses_added": missing_remote,
                "gap_accounts_checked": gap_accounts,
                "gap_signatures_replayed": backfilled,
                "watch_set_sha256": sha_watch(target),
                "canonical_hero_market_modified": False,
                "d1_events_acknowledged": 0,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print()
    print("=" * 78)
    print("[PASS] WEBHOOK TOKEN-ACCOUNT FRONTIER IS DURABLE AND CURRENT")
    print("=" * 78)
    print(f"Tracked token accounts:     {len(final_accounts):,}")
    print(f"New token accounts added:   {len(additions):,}")
    print(f"Helius watch addresses:     {len(target):,}")
    print(f"Gap accounts checked:       {checked:,}")
    print(f"Gap transactions replayed:  {len(backfilled):,}")
    print(f"Tracked frontier:           {FRONTIER.relative_to(ROOT)}")
    print(f"Audit receipt:              {OUT.relative_to(ROOT)}")
    print("Hero/market canonical:      UNCHANGED")
    print("D1 events acknowledged:     NONE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print()
        print(f"[FAIL] {exc}")
        print("D1 events were not acknowledged.")
        raise SystemExit(1)
