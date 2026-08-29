#!/usr/bin/env python3
"""Create and verify the production Helius raw webhook for Guild Saga.

This script is intentionally conservative:
- secrets are loaded from existing ignored/local files or environment variables;
- no secret value is printed;
- the watch set is built from the canonical 10k mint inventory plus the directly
  reconciled current SPL token-account frontier;
- an authenticated no-op receiver probe must pass before the webhook is created;
- the activation boundary is recorded conservatively *before* the create request;
- canonical Guild Saga data files are never modified.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

WORKER_ORIGIN = "https://guild-saga-webhook-inbox.cjohnson80.workers.dev"
WEBHOOK_PATH = "/webhooks/helius"
HELIUS_API_ORIGIN = "https://api-mainnet.helius-rpc.com"
WORLD_MODE_PROGRAM = "6AzuBKDsR88vinh399HV5v7fgB1eZyoYwQ3PmdYqFRZG"
ROYALTY_90 = "8VAHrpJ9nsqRLaujbzpuCxhAhjsE8wA4ZvUHwx2VZw3y"
ROYALTY_10 = "RRUMF9KYPcvNSmnicNMAFKx5wDYix3wjNa6bA7R6xqA"
EXPECTED_MINTS = 10_000
EXPECTED_ACTIVE = 9_832
MAX_WEBHOOK_ADDRESSES = 100_000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def load_worker_secrets(worker_dir: Path) -> tuple[str, str]:
    values = parse_env_file(worker_dir / ".env.worker-secrets.local")
    helius_auth = os.environ.get("HELIUS_WEBHOOK_AUTH") or values.get("HELIUS_WEBHOOK_AUTH")
    pipeline = os.environ.get("PIPELINE_TOKEN") or values.get("PIPELINE_TOKEN")
    if not helius_auth or not pipeline:
        raise RuntimeError(
            "Missing Worker secrets. Expected HELIUS_WEBHOOK_AUTH and PIPELINE_TOKEN in "
            "cloudflare/webhook-inbox/.env.worker-secrets.local (created by Phase 2C)."
        )
    return helius_auth, pipeline


def _value_after_equals(line: str) -> str:
    return line.split("=", 1)[1].strip() if "=" in line else line.strip()


def load_helius_api_key(repo_root: Path) -> str:
    env = os.environ.get("HELIUS_API_KEY", "").strip()
    if env:
        return env

    candidates = [repo_root / "keys.txt", repo_root / "key.txt"]
    for path in candidates:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            lower = line.lower()
            if lower.startswith("alchemy") or lower.startswith("alch"):
                continue
            if lower.startswith("helius_api_key=") or lower.startswith("helius="):
                value = _value_after_equals(line)
            elif "=" in line:
                # Do not accidentally treat unrelated named settings as the API key.
                continue
            else:
                value = line
            if value:
                return value
    raise RuntimeError(
        "Helius API key not found. Expected HELIUS_API_KEY or the existing ignored keys.txt/key.txt file."
    )


def read_canonical_active_mints(repo_root: Path) -> tuple[list[str], list[str]]:
    baseline_path = repo_root / "data" / "baseline" / "assets.csv"
    deltas_path = repo_root / "data" / "state" / "hero_deltas.csv"
    if not baseline_path.exists() or not deltas_path.exists():
        raise RuntimeError("Canonical baseline/state CSV files are missing.")

    state: dict[str, bool] = {}
    with baseline_path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            mint = (row.get("mint") or "").strip()
            if not mint:
                continue
            state[mint] = str(row.get("burned") or "").strip().lower() in {"1", "true", "yes"}

    if len(state) != EXPECTED_MINTS:
        raise RuntimeError(f"Expected {EXPECTED_MINTS:,} unique baseline mints, found {len(state):,}.")

    with deltas_path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            mint = (row.get("mint") or "").strip()
            if mint not in state:
                raise RuntimeError(f"hero_deltas.csv contains unknown mint: {mint}")
            burned = str(row.get("burned") or "").strip().lower()
            if burned:
                state[mint] = burned in {"1", "true", "yes"}

    all_mints = sorted(state)
    active = sorted(mint for mint, burned in state.items() if not burned)
    if len(active) != EXPECTED_ACTIVE:
        raise RuntimeError(
            f"Expected {EXPECTED_ACTIVE:,} canonical active mints at cutover, found {len(active):,}. "
            "Do not activate the webhook from an unexpected canonical state."
        )
    return all_mints, active


def read_reconciled_token_accounts(repo_root: Path, active_mints: list[str]) -> dict[str, str]:
    db_path = repo_root / ".guild_saga_recon" / "cutover_free_backfill.sqlite"
    if not db_path.exists():
        raise RuntimeError(
            "Missing .guild_saga_recon/cutover_free_backfill.sqlite. "
            "The successful Phase 1L reconciliation cache is required."
        )

    active_set = set(active_mints)
    mapping: dict[str, str] = {}
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"owner_holdings", "chain_snapshot"}
        if not required.issubset(tables):
            raise RuntimeError(f"Reconciliation DB is missing tables: {sorted(required - tables)}")

        # Owner-frontier rows are the preferred direct SPL observation for the 9,820
        # Heroes found under the reconciled owner union.
        for mint, token_account in con.execute(
            "SELECT mint, token_account FROM owner_holdings WHERE token_account IS NOT NULL AND token_account<>''"
        ):
            if mint in active_set:
                mapping[str(mint)] = str(token_account)

        # The remaining candidates were individually snapshotted. Only use them
        # where owner_holdings did not already provide a token account.
        for mint, token_account in con.execute(
            "SELECT mint, token_account FROM chain_snapshot "
            "WHERE complete=1 AND supply_zero=0 AND token_account IS NOT NULL AND token_account<>''"
        ):
            if mint in active_set and mint not in mapping:
                mapping[str(mint)] = str(token_account)
    finally:
        con.close()

    missing = sorted(active_set - set(mapping))
    if missing:
        sample = ", ".join(missing[:5])
        raise RuntimeError(
            f"Direct reconciliation does not provide a current token account for {len(missing):,} active Heroes "
            f"(first: {sample}). Refusing to create an incomplete webhook."
        )
    if len(mapping) != EXPECTED_ACTIVE:
        raise RuntimeError(f"Expected {EXPECTED_ACTIVE:,} reconciled active token accounts, found {len(mapping):,}.")
    if len(set(mapping.values())) != len(mapping):
        raise RuntimeError("Two active Heroes unexpectedly resolve to the same SPL token account.")
    return mapping


def build_watch_set(all_mints: list[str], token_accounts: dict[str, str]) -> tuple[list[str], dict[str, int]]:
    special = {WORLD_MODE_PROGRAM, ROYALTY_90, ROYALTY_10}
    addresses = sorted(set(all_mints) | set(token_accounts.values()) | special)
    if len(addresses) > MAX_WEBHOOK_ADDRESSES:
        raise RuntimeError(f"Watch set has {len(addresses):,} addresses, over Helius limit {MAX_WEBHOOK_ADDRESSES:,}.")
    counts = {
        "collection_mints": len(set(all_mints)),
        "active_token_accounts": len(set(token_accounts.values())),
        "special_sources": len(special),
        "total_unique": len(addresses),
    }
    return addresses, counts


def watch_hash(addresses: list[str]) -> str:
    return hashlib.sha256(("\n".join(addresses) + "\n").encode("utf-8")).hexdigest()


# Cloudflare Browser Integrity Check is enabled by default and may reject
# Python urllib's default ``Python-urllib/x.y`` user agent with Error 1010.
# Use ordinary browser-shaped request headers for setup/preflight HTTP calls.
# This changes only this local activation client; it does not weaken Worker auth.
HTTP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)


def http_json(method: str, url: str, *, headers: dict[str, str] | None = None, body=None, timeout=60):
    hdr = {
        "accept": "application/json",
        "user-agent": HTTP_USER_AGENT,
        "accept-language": "en-US,en;q=0.9",
    }
    if headers:
        hdr.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        hdr["content-type"] = "application/json"
    req = Request(url, data=data, headers=hdr, method=method)
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = response.status
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        # Deliberately do not include exc.url: the Helius API key is a query parameter.
        raise RuntimeError(f"HTTP {exc.code}: {raw[:1200]}") from None
    except URLError as exc:
        raise RuntimeError(f"Network request failed: {exc.reason}") from None
    try:
        payload = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        raise RuntimeError(f"HTTP {status} returned non-JSON: {raw[:500]}") from None
    return status, payload


def helius_url(api_key: str, suffix: str = "") -> str:
    return f"{HELIUS_API_ORIGIN}/v0/webhooks{suffix}?{urlencode({'api-key': api_key})}"


def receiver_preflight(helius_auth: str, pipeline_token: str) -> None:
    status, payload = http_json("GET", f"{WORKER_ORIGIN}/health")
    if status != 200 or not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("Worker /health preflight failed.")

    # An empty Helius-style array is a valid no-op delivery: authentication is
    # exercised but no D1 event row is created.
    status, payload = http_json(
        "POST",
        f"{WORKER_ORIGIN}{WEBHOOK_PATH}",
        headers={"Authorization": helius_auth},
        body=[],
    )
    if status != 200 or not isinstance(payload, dict) or payload.get("ok") is not True or payload.get("received") != 0:
        raise RuntimeError("Authenticated Helius receiver preflight failed.")

    status, payload = http_json(
        "GET",
        f"{WORKER_ORIGIN}/internal/stats",
        headers={"Authorization": f"Bearer {pipeline_token}"},
    )
    if status != 200 or not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("Authenticated pipeline endpoint preflight failed.")


def get_existing_target_webhooks(api_key: str, target_url: str) -> list[dict]:
    _, payload = http_json("GET", helius_url(api_key), timeout=60)
    if not isinstance(payload, list):
        raise RuntimeError("Helius get-all-webhooks returned an unexpected payload.")
    return [x for x in payload if isinstance(x, dict) and x.get("webhookURL") == target_url]


def create_webhook(api_key: str, auth_header: str, addresses: list[str]) -> tuple[str, str]:
    target_url = f"{WORKER_ORIGIN}{WEBHOOK_PATH}"
    existing = get_existing_target_webhooks(api_key, target_url)
    if existing:
        ids = [str(x.get("webhookID") or "<unknown>") for x in existing]
        raise RuntimeError(
            "A Helius webhook already targets this production receiver. Refusing to create a duplicate. "
            f"Existing webhook ID(s): {', '.join(ids)}"
        )

    # Conservative boundary: any close-out scan starting here overlaps the entire
    # webhook creation call, so there is no gap even if Helius activates mid-request.
    activation_boundary = utc_now()
    body = {
        "webhookURL": target_url,
        "transactionTypes": ["ANY"],
        "accountAddresses": addresses,
        "webhookType": "raw",
        "authHeader": auth_header,
    }
    _, payload = http_json("POST", helius_url(api_key), body=body, timeout=120)
    if not isinstance(payload, dict):
        raise RuntimeError("Helius create-webhook returned an unexpected payload.")
    webhook_id = str(payload.get("webhookID") or "")
    if not webhook_id:
        raise RuntimeError("Helius create-webhook response did not include webhookID.")
    return webhook_id, activation_boundary


def verify_webhook(api_key: str, webhook_id: str, target_url: str, addresses: list[str]) -> dict:
    _, payload = http_json("GET", helius_url(api_key, f"/{webhook_id}"), timeout=90)
    if not isinstance(payload, dict):
        raise RuntimeError("Helius get-webhook returned an unexpected payload.")
    if payload.get("webhookURL") != target_url:
        raise RuntimeError("Helius webhook URL verification failed.")
    if payload.get("webhookType") not in {"raw", None}:
        # Some historical API responses omit webhookType; an explicit different type is unsafe.
        raise RuntimeError(f"Helius returned unexpected webhookType={payload.get('webhookType')!r}.")
    if payload.get("active") is not True:
        raise RuntimeError("Helius webhook was created but is not active.")
    returned = payload.get("accountAddresses")
    if not isinstance(returned, list):
        raise RuntimeError("Helius webhook verification did not return accountAddresses.")
    returned_set = set(map(str, returned))
    wanted_set = set(addresses)
    if returned_set != wanted_set:
        raise RuntimeError(
            f"Helius watch-set verification failed: expected {len(wanted_set):,} addresses, "
            f"returned {len(returned_set):,}."
        )
    return payload


def record_activation(pipeline_token: str, activation_boundary: str) -> str:
    _, payload = http_json(
        "POST",
        f"{WORKER_ORIGIN}/internal/activation",
        headers={"Authorization": f"Bearer {pipeline_token}"},
        body={"activated_at": activation_boundary},
    )
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("Failed to record webhook activation boundary in D1.")
    return str(payload.get("webhook_activated_at") or "")


def write_local_receipt(repo_root: Path, data: dict) -> Path:
    path = repo_root / ".guild_saga_recon" / "helius_webhook_setup.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main() -> int:
    worker_dir = Path(__file__).resolve().parent
    repo_root = worker_dir.parents[1]
    print("=" * 78)
    print("Guild Saga — Phase 2D Helius Raw Webhook Activation")
    print("=" * 78)
    print("No secret values will be printed. Canonical data will not be modified.")
    print()

    print("[1/6] Loading local credentials")
    helius_auth, pipeline_token = load_worker_secrets(worker_dir)
    api_key = load_helius_api_key(repo_root)
    print("    Worker secrets: found")
    print("    Helius API key: found")

    print("[2/6] Building audited watch set")
    all_mints, active_mints = read_canonical_active_mints(repo_root)
    token_accounts = read_reconciled_token_accounts(repo_root, active_mints)
    addresses, counts = build_watch_set(all_mints, token_accounts)
    digest = watch_hash(addresses)
    print(f"    Guild Saga mints:        {counts['collection_mints']:,}")
    print(f"    active token accounts:   {counts['active_token_accounts']:,}")
    print(f"    World Mode + royalties:  {counts['special_sources']:,}")
    print(f"    total unique addresses:  {counts['total_unique']:,} / {MAX_WEBHOOK_ADDRESSES:,}")
    print(f"    watch-set SHA-256:        {digest[:16]}…")

    print("[3/6] Preflighting deployed Worker authentication")
    receiver_preflight(helius_auth, pipeline_token)
    print("    /health:                 PASS")
    print("    Helius auth no-op probe: PASS")
    print("    pipeline auth:           PASS")

    print("[4/6] Creating Helius raw webhook")
    webhook_id, boundary = create_webhook(api_key, helius_auth, addresses)
    print(f"    webhook ID:              {webhook_id}")
    print(f"    conservative boundary:   {boundary}")

    print("[5/6] Verifying active Helius configuration")
    target_url = f"{WORKER_ORIGIN}{WEBHOOK_PATH}"
    verify_webhook(api_key, webhook_id, target_url, addresses)
    print("    type:                    raw")
    print("    active:                  YES")
    print(f"    addresses verified:      {len(addresses):,}/{len(addresses):,}")

    print("[6/6] Recording activation boundary")
    recorded = record_activation(pipeline_token, boundary)
    receipt = {
        "webhook_id": webhook_id,
        "webhook_url": target_url,
        "webhook_type": "raw",
        "active": True,
        "activation_boundary_utc": recorded,
        "verified_at_utc": utc_now(),
        "watch_counts": counts,
        "watch_set_sha256": digest,
        "canonical_files_modified": False,
    }
    receipt_path = write_local_receipt(repo_root, receipt)
    print(f"    D1 boundary:             {recorded}")
    print(f"    local receipt:           {receipt_path.relative_to(repo_root)}")
    print()
    print("[PASS] Durable Helius → Cloudflare webhook intake is active.")
    print("Next safety step is an overlapping close-out scan before advancing canonical state.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled; canonical data was not modified.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        print("Canonical data was not modified.", file=sys.stderr)
        raise SystemExit(1)
