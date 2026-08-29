#!/usr/bin/env python3
"""Stage and locally commit the exact audited Guild Saga production release.

This script MAY create one local Git commit.
It does NOT push to GitHub and does NOT ACK Cloudflare D1.

Phase 2L4 handles Git-normalized no-op paths at BOTH safety gates:
1) initial worktree inventory
2) post-staging manifest verification

A formerly-audited path may be absent only when Git itself proves the current
normalized content is identical to HEAD. Any genuinely missing/changed path
still aborts.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECON = ROOT / ".guild_saga_recon"
PREPARED = RECON / "webhook_batch_prepared.json"
PRECOMMIT = RECON / "precommit_audit.json"
COMMIT_RECEIPT = RECON / "prepared_batch_commit.json"
SELF_PATH = "scripts/commit_prepared_release.py"

SECRET_FILES = [
    ROOT / "keys.txt",
    ROOT / "key.txt",
    ROOT / "alchemy_key.txt",
    ROOT / ".env",
    ROOT / "cloudflare" / "webhook-inbox" / ".env.worker-secrets.local",
]

COMMIT_MESSAGE = "Build independent Guild Saga data pipeline"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def load_json(path: Path) -> dict:
    if not path.exists():
        raise RuntimeError(f"Missing required file: {path.relative_to(ROOT)}")
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError(f"Expected JSON object: {path.relative_to(ROOT)}")
    return obj


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
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


def normalize_audited_path(path: str) -> str:
    text = str(path or "")
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Could not decode audited Git path {text!r}: {exc}") from exc
        if not isinstance(decoded, str):
            raise RuntimeError(f"Audited Git path did not decode to text: {text!r}")
        return decoded
    return text


def status_paths_z() -> set[str]:
    cp = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "-uall"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if cp.returncode:
        raise RuntimeError(cp.stderr.decode(errors="replace"))
    data = cp.stdout
    out = set()
    i = 0
    while i < len(data):
        end = data.find(b"\0", i)
        if end < 0:
            break
        rec = data[i:end]
        i = end + 1
        if len(rec) < 4:
            continue
        path = rec[3:].decode("utf-8", errors="surrogateescape")
        out.add(path)
        xy = rec[:2].decode("ascii", errors="replace")
        if "R" in xy or "C" in xy:
            end2 = data.find(b"\0", i)
            if end2 < 0:
                raise RuntimeError("Malformed porcelain rename/copy record.")
            old = data[i:end2].decode("utf-8", errors="surrogateescape")
            i = end2 + 1
            raise RuntimeError(f"Unexpected rename/copy in release: {old} -> {path}")
    return out


def read_secret_values() -> list[str]:
    vals = []
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
            value = line.split("=", 1)[1].strip() if "=" in line else line
            value = value.strip().strip('"').strip("'")
            if len(value) >= 20:
                vals.append(value)
    return sorted(set(vals))


def unstage_release() -> None:
    git("reset", "--quiet", check=False)


def git_proves_head_equivalent(path: str) -> bool:
    """True only when Git says current normalized content equals HEAD."""
    cp = git("diff", "--quiet", "HEAD", "--", path, check=False)
    return cp.returncode == 0


def split_missing_into_noops(paths: set[str]) -> tuple[list[str], list[str]]:
    proven_noops = []
    unsafe = []
    for path in sorted(paths):
        if git_proves_head_equivalent(path):
            proven_noops.append(path)
        else:
            unsafe.append(path)
    return proven_noops, unsafe


def main() -> int:
    print("=" * 78)
    print("Guild Saga — Phase 2L4 Stage + Local Commit Prepared Release")
    print("=" * 78)
    print("This command MAY create one local Git commit.")
    print("GitHub push:                 NO")
    print("D1 acknowledgements:         NO")
    print()

    receipt = load_json(PREPARED)
    audit = load_json(PRECOMMIT)

    print("[1/8] Verifying prepared/audited release evidence")
    if receipt.get("status") != "PREPARED":
        raise RuntimeError("Prepared batch is not in PREPARED state.")
    if receipt.get("d1_acknowledged") is not False or receipt.get("acked_at_utc"):
        raise RuntimeError("Prepared batch already appears acknowledged.")
    if audit.get("status") != "PASS":
        raise RuntimeError("Phase 2K2 precommit audit is not PASS.")
    if audit.get("prepared_snapshot") != receipt.get("snapshot_received_at"):
        raise RuntimeError("Precommit audit and PREPARED receipt snapshot differ.")
    if int(audit.get("prepared_signature_count") or 0) != int(receipt.get("signature_count") or 0):
        raise RuntimeError("Precommit audit and PREPARED receipt signature counts differ.")

    hashes = receipt.get("canonical_hashes") or {}
    for name, expected in hashes.items():
        path = ROOT / name
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"Prepared canonical hash changed: {name}")
    print(f"    prepared signatures:     {receipt.get('signature_count')}")
    print(f"    canonical hashes exact:  {len(hashes)}/{len(hashes)}")

    print("[2/8] Verifying exact worktree inventory")
    staged_before = [x for x in git("diff", "--cached", "--name-only").stdout.splitlines() if x]
    if staged_before:
        raise RuntimeError(f"Pre-existing staged files found: {staged_before[:10]}")

    audited_rows = audit.get("changed_files")
    if not isinstance(audited_rows, list):
        raise RuntimeError("Precommit audit has no changed_files inventory.")

    approved = set()
    for row in audited_rows:
        if isinstance(row, dict):
            raw = str(row.get("path") or "")
            if raw:
                approved.add(normalize_audited_path(raw))
    approved.add(SELF_PATH)

    current = status_paths_z()
    unexpected = sorted(current - approved)
    initial_missing = approved - current
    initial_noops, unsafe_initial_missing = split_missing_into_noops(initial_missing)

    if unexpected or unsafe_initial_missing:
        raise RuntimeError(
            "Worktree changed since Phase 2K2 audit.\n"
            f"  missing approved paths not proven HEAD-equivalent: {unsafe_initial_missing[:10]}\n"
            f"  unexpected paths: {unexpected[:10]}"
        )

    effective_approved = approved - set(initial_noops)
    print(f"    audited paths:           {len(approved)}")
    print(f"    initial normalized no-op:{len(initial_noops):>6}")
    for path in initial_noops:
        print(f"      = {path}")
    print("    unexpected paths:        0")
    print("    staged paths before:     0")

    print("[3/8] Fetching origin and proving branch base")
    branch = git("branch", "--show-current").stdout.strip()
    if branch != "main":
        raise RuntimeError(f"Expected branch main, found {branch!r}.")
    git("fetch", "origin")
    local_head = git("rev-parse", "HEAD").stdout.strip()
    remote_head = git("rev-parse", "origin/main").stdout.strip()
    if local_head != remote_head:
        raise RuntimeError(
            "Local main is not exactly at current origin/main. "
            "Refusing to create a release commit on a stale/diverged base."
        )
    print("    branch:                  main")
    print("    local HEAD = origin/main:YES")

    print("[4/8] Staging explicit effective manifest")
    try:
        for path in sorted(effective_approved):
            cp = git("add", "--", path, check=False)
            if cp.returncode:
                raise RuntimeError(f"git add failed for {path}:\n{cp.stdout}")

        staged = set(x for x in git("diff", "--cached", "--name-only").stdout.splitlines() if x)

        unexpected_staged = sorted(staged - effective_approved)
        if unexpected_staged:
            raise RuntimeError(f"Unexpected staged paths: {unexpected_staged[:10]}")

        post_missing = effective_approved - staged
        post_noops, unsafe_post_missing = split_missing_into_noops(post_missing)
        if unsafe_post_missing:
            raise RuntimeError(
                "Approved paths disappeared from staging without Git proving "
                f"HEAD-equivalence: {unsafe_post_missing[:10]}"
            )

        expected_commit_paths = effective_approved - set(post_noops)
        if staged != expected_commit_paths:
            raise RuntimeError("Internal staged-manifest accounting mismatch.")

        all_noops = sorted(set(initial_noops) | set(post_noops))
        print(f"    staged paths:            {len(staged)}")
        print(f"    post-stage no-op paths:  {len(post_noops)}")
        for path in post_noops:
            print(f"      = {path}")
        print("    effective manifest:      EXACT")

        print("[5/8] Scanning staged release for secrets/structural problems")
        diff_check = git("diff", "--cached", "--check", check=False)
        if diff_check.returncode:
            raise RuntimeError("git diff --cached --check failed:\n" + diff_check.stdout)

        staged_diff = git("diff", "--cached", "--no-ext-diff", "--binary").stdout
        leaked = sum(1 for value in read_secret_values() if value and value in staged_diff)
        if leaked:
            raise RuntimeError(f"Detected {leaked} known secret value(s) in staged release.")
        print("    git diff --cached --check: PASS")
        print("    known secret leaks:        0")

        print("[6/8] Re-running validators on the staged release")
        run_py("offline unit tests", "-m", "unittest", "discover", "-s", "tests", "-v")
        run_py("frozen cutover validator", "scripts/validate_cutover.py")
        run_py("live production validator", "scripts/validate_live.py")

        print("[7/8] Creating local release commit")
        cp = git("commit", "-m", COMMIT_MESSAGE, check=False)
        if cp.returncode:
            raise RuntimeError("git commit failed:\n" + cp.stdout)
        commit_sha = git("rev-parse", "HEAD").stdout.strip()

    except Exception:
        unstage_release()
        print()
        print("[UNSTAGED] Release was not committed. Working files were preserved.")
        raise

    print("[8/8] Verifying local commit and recording ignored receipt")
    parent = git("rev-parse", "HEAD^").stdout.strip()
    if parent != remote_head:
        raise RuntimeError("Created commit parent is not the fetched origin/main SHA.")

    committed = set(
        x for x in git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").stdout.splitlines()
        if x
    )
    if committed != expected_commit_paths:
        raise RuntimeError(
            "Committed path set differs from the proven effective manifest. "
            "DO NOT PUSH OR ACK; manual review required."
        )

    visible_after = status_paths_z()
    if visible_after:
        raise RuntimeError(
            f"Commit succeeded but visible worktree changes remain: {sorted(visible_after)[:10]}. "
            "DO NOT PUSH OR ACK until reviewed."
        )

    write_json(
        COMMIT_RECEIPT,
        {
            "status": "LOCAL_COMMIT_READY",
            "commit_sha": commit_sha,
            "commit_message": COMMIT_MESSAGE,
            "prepared_snapshot": receipt.get("snapshot_received_at"),
            "prepared_signature_count": receipt.get("signature_count"),
            "audited_path_count": len(approved),
            "normalized_noop_paths": all_noops,
            "committed_path_count": len(committed),
            "origin_main_parent": remote_head,
            "pushed": False,
            "d1_acknowledged": False,
            "created_at_utc": utc_now(),
        },
    )

    print()
    print("=" * 78)
    print("[PASS] PREPARED RELEASE COMMITTED LOCALLY — NOT PUSHED / NOT ACKED")
    print("=" * 78)
    print(f"Commit SHA:                 {commit_sha}")
    print(f"Audited paths:              {len(approved)}")
    print(f"Normalized no-op paths:     {len(all_noops)}")
    print(f"Committed paths:            {len(committed)}")
    print("Commit parent = origin/main: YES")
    print("Visible worktree changes:   0")
    print("Offline tests:              PASS")
    print("Frozen cutover validator:   PASS")
    print("Live validator:             PASS")
    print("GitHub push performed:      NO")
    print("D1 events acknowledged:     NO")
    print(f"Ignored commit receipt:     {COMMIT_RECEIPT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print()
        print(f"[FAIL] {exc}")
        print("GitHub was not pushed and D1 was not acknowledged.")
        raise SystemExit(1)
