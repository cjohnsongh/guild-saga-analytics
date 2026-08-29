#!/usr/bin/env python3
"""Read-only precommit audit for the prepared live webhook batch.

This intentionally does not stage, commit, push, or ACK D1.

It verifies:
- the PREPARED receipt still matches the canonical files byte-for-byte
- no prior staged changes are mixed into the release
- local secret files are ignored and not tracked
- ignored reconciliation artifacts are not tracked
- no known secret value appears in tracked diffs
- node_modules is not tracked
- Git whitespace/conflict checks pass
- the complete offline/cutover/live validation suite still passes
- emits the exact current changed-file inventory for the next commit step
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECON = ROOT / ".guild_saga_recon"
PREPARED = RECON / "webhook_batch_prepared.json"
AUDIT_OUT = RECON / "precommit_audit.json"

SECRET_FILES = [
    ROOT / "keys.txt",
    ROOT / "key.txt",
    ROOT / "alchemy_key.txt",
    ROOT / ".env",
    ROOT / "cloudflare" / "webhook-inbox" / ".env.worker-secrets.local",
]

FORBIDDEN_TRACKED_PREFIXES = [
    ".guild_saga_recon/",
    "cloudflare/webhook-inbox/node_modules/",
]

EXPECTED_PREPARED_STATUS = "PREPARED"

# Real unresolved Git conflict markers must begin at column 1.
# Using a regex here avoids the old scanner flagging its own detector strings.
CONFLICT_RE = re.compile(r"^(?:<{7} .+|={7}|>{7} .+)$", re.MULTILINE)


def run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    cp = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if check and cp.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{cp.stdout}")
    return cp


def run_py(label: str, *args: str) -> None:
    print(f"    {label}...")
    cp = subprocess.run([sys.executable, *args], cwd=ROOT)
    if cp.returncode:
        raise RuntimeError(f"{label} failed with exit code {cp.returncode}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict:
    if not path.exists():
        raise RuntimeError(f"Missing required file: {path.relative_to(ROOT)}")
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError(f"Expected JSON object: {path.relative_to(ROOT)}")
    return obj


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def parse_porcelain(text: str) -> list[dict[str, str]]:
    rows = []
    for raw in text.splitlines():
        if not raw:
            continue
        if raw.startswith("?? "):
            rows.append({"xy": "??", "path": raw[3:]})
            continue
        if len(raw) < 4:
            raise RuntimeError(f"Unexpected git status line: {raw!r}")
        rows.append({"xy": raw[:2], "path": raw[3:]})
    return rows


def read_secret_values() -> list[str]:
    values = []
    for path in SECRET_FILES:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
            else:
                value = line
            if len(value) >= 20:
                values.append(value)
    return sorted(set(values))


def main() -> int:
    print("=" * 78)
    print("Guild Saga — Phase 2K2 Prepared-Batch Precommit Audit · READ ONLY")
    print("=" * 78)
    print("Git staging/commit/push:     NONE")
    print("D1 acknowledgements:         NONE")
    print()

    print("[1/7] Verifying PREPARED receipt")
    receipt = load_json(PREPARED)
    if receipt.get("status") != EXPECTED_PREPARED_STATUS:
        raise RuntimeError(f"Prepared receipt status is {receipt.get('status')!r}, not PREPARED.")
    if receipt.get("d1_acknowledged") is not False:
        raise RuntimeError("Prepared receipt says D1 is already acknowledged.")
    if receipt.get("acked_at_utc"):
        raise RuntimeError("Prepared receipt already has acked_at_utc.")
    signatures = receipt.get("signatures")
    if not isinstance(signatures, list) or not signatures:
        raise RuntimeError("Prepared receipt has no signature batch.")
    if len(signatures) != len(set(map(str, signatures))):
        raise RuntimeError("Prepared receipt contains duplicate signatures.")
    if int(receipt.get("signature_count") or 0) != len(signatures):
        raise RuntimeError("Prepared receipt signature_count mismatch.")
    print(f"    batch signatures:        {len(signatures):,}")
    print(f"    snapshot:                {receipt.get('snapshot_received_at')}")

    print("[2/7] Re-verifying prepared canonical hashes")
    hashes = receipt.get("canonical_hashes")
    if not isinstance(hashes, dict) or not hashes:
        raise RuntimeError("Prepared receipt contains no canonical hashes.")
    for name, expected in hashes.items():
        path = ROOT / str(name)
        if not path.exists():
            raise RuntimeError(f"Prepared canonical file is missing: {name}")
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(
                f"Prepared state changed after validation: {name}\n"
                f"  expected {expected}\n  actual   {actual}"
            )
    print(f"    prepared hashes exact:   {len(hashes):,}/{len(hashes):,}")

    print("[3/7] Checking Git working-tree safety")
    if run_git("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        raise RuntimeError("Not inside the Guild Saga Git worktree.")

    staged = [x for x in run_git("diff", "--cached", "--name-only").stdout.splitlines() if x.strip()]
    if staged:
        raise RuntimeError(
            "There are already staged files. Refusing to mix an unknown staged set "
            f"into the production release. First staged paths: {staged[:10]}"
        )

    status = parse_porcelain(run_git("status", "--porcelain=v1", "-uall").stdout)
    if not status:
        raise RuntimeError("Git worktree is clean, but a PREPARED batch should have canonical changes.")

    tracked = set(run_git("ls-files").stdout.splitlines())
    forbidden = sorted(
        p for p in tracked
        if any(p.startswith(prefix) for prefix in FORBIDDEN_TRACKED_PREFIXES)
    )
    if forbidden:
        raise RuntimeError(f"Forbidden local/runtime paths are tracked: {forbidden[:10]}")
    print(f"    changed/untracked paths: {len(status):,}")
    print("    pre-existing staged set: NONE")

    print("[4/7] Checking local-secret containment")
    secret_status = []
    for path in SECRET_FILES:
        if not path.exists():
            continue
        rp = rel(path)
        if rp in tracked:
            raise RuntimeError(f"Secret file is tracked by Git: {rp}")
        ignored = run_git("check-ignore", "-q", "--", rp, check=False).returncode == 0
        if not ignored:
            raise RuntimeError(f"Existing secret file is not ignored by Git: {rp}")
        secret_status.append(rp)

    secret_values = read_secret_values()
    diff_text = run_git("diff", "--no-ext-diff", "--binary").stdout

    changed_paths = [row["path"] for row in status]
    for path_name in changed_paths:
        if path_name in tracked:
            continue
        p = ROOT / path_name
        if not p.is_file() or p.stat().st_size > 2_000_000:
            continue
        try:
            diff_text += "\n" + p.read_text(encoding="utf-8-sig")
        except (UnicodeDecodeError, OSError):
            pass

    leaked_count = sum(1 for value in secret_values if value and value in diff_text)
    if leaked_count:
        raise RuntimeError(
            f"Detected {leaked_count} known local secret value(s) in changed repository content."
        )
    print(f"    existing secret files ignored: {len(secret_status):,}")
    print("    known secret values in changes: NONE")

    print("[5/7] Running Git structural checks")
    cp = run_git("diff", "--check", check=False)
    if cp.returncode:
        raise RuntimeError("git diff --check failed:\n" + cp.stdout)

    conflict_markers = []
    for row in status:
        p = ROOT / row["path"]
        if not p.is_file() or p.stat().st_size > 2_000_000:
            continue
        try:
            text = p.read_text(encoding="utf-8-sig")
        except (UnicodeDecodeError, OSError):
            continue
        if CONFLICT_RE.search(text):
            conflict_markers.append(row["path"])
    if conflict_markers:
        raise RuntimeError(f"Possible merge conflict markers: {conflict_markers[:10]}")
    print("    git diff --check:        PASS")
    print("    conflict-marker scan:    PASS")

    print("[6/7] Re-running production validation")
    run_py("offline unit tests", "-m", "unittest", "discover", "-s", "tests", "-v")
    run_py("frozen cutover validator", "scripts/validate_cutover.py")
    run_py("live production validator", "scripts/validate_live.py")

    print("[7/7] Writing ignored audit receipt + changed-file inventory")
    inventory = sorted(status, key=lambda r: r["path"].lower())
    AUDIT_OUT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_OUT.write_text(
        json.dumps(
            {
                "status": "PASS",
                "prepared_snapshot": receipt.get("snapshot_received_at"),
                "prepared_signature_count": len(signatures),
                "prepared_canonical_hashes_verified": len(hashes),
                "staged_files": [],
                "changed_files": inventory,
                "secret_files_verified_ignored": secret_status,
                "known_secret_values_in_changed_content": 0,
                "git_diff_check": "PASS",
                "conflict_marker_scan": "PASS",
                "offline_tests": "PASS",
                "cutover_validator": "PASS",
                "live_validator": "PASS",
                "git_mutated": False,
                "d1_acknowledged": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print()
    print("=" * 78)
    print("[PASS] PREPARED LIVE BATCH IS SAFE FOR COMMIT REVIEW")
    print("=" * 78)
    print(f"Prepared signatures:        {len(signatures):,}")
    print(f"Changed/untracked paths:    {len(inventory):,}")
    print("Staged paths:               0")
    print("Known secret leaks:         0")
    print("git diff --check:           PASS")
    print("Conflict-marker scan:       PASS")
    print("Offline tests:              PASS")
    print("Frozen cutover validator:   PASS")
    print("Live validator:             PASS")
    print("Git modified by audit:      NO")
    print("D1 events acknowledged:     NO")
    print()
    print("Changed-file inventory:")
    for row in inventory:
        print(f"    {row['xy']:>2}  {row['path']}")
    print()
    print(f"Audit receipt: {AUDIT_OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print()
        print(f"[FAIL] {exc}")
        print("Nothing was staged/committed/pushed. D1 was not acknowledged.")
        raise SystemExit(1)
