#!/usr/bin/env python3
"""Independent daily floor/listings snapshot pipeline.

This domain is intentionally separate from the Solana webhook reducer. It reads
Magic Eden's public collection-stats endpoint, writes at most one UTC-dated row
per day, rebuilds only the floor-facing public JSON, validates the repository,
commits/pushes an exact allow-listed release, and proves the Cloudflare Pages
output before reporting success.

A source or deployment failure fails closed: no fabricated row is written and a
failed pre-push run leaves the public site unchanged.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "data" / "history" / "floor_listings.csv"
CHECKPOINTS = ROOT / "data" / "state" / "checkpoints.json"
PUBLIC_FLOOR = ROOT / "site" / "public" / "data" / "floor-listings.json"
PUBLIC_SUMMARY = ROOT / "site" / "public" / "data" / "summary.json"

COLLECTION_SYMBOL = "guild_saga_heroes"
ME_STATS = f"https://api-mainnet.magiceden.dev/v2/collections/{COLLECTION_SYMBOL}/stats"
USER_AGENT = "GuildSagaAnalytics-FloorListings/1.0"
RETRY_DELAYS_SECONDS = (0, 2, 5, 10, 20)
REQUIRED_PATHS = (
    "data/history/floor_listings.csv",
    "data/state/checkpoints.json",
    "site/public/data/floor-listings.json",
)
ALLOWED_PATHS = REQUIRED_PATHS + ("site/public/data/summary.json",)
PAGES_FALLBACK_ORIGINS = (
    "https://guildsaga.pages.dev",
    "https://guild-saga-analytics.pages.dev",
)
NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError)
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "cjohnsongh/guild-saga-analytics")
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}"


def git(*args: str, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run(
        ["git", *args], cwd=cwd, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    if check and cp.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{cp.stdout}")
    return cp


def run_python(*args: str, cwd: Path = ROOT) -> None:
    cp = subprocess.run([sys.executable, *args], cwd=cwd)
    if cp.returncode:
        raise RuntimeError(f"python {' '.join(args)} failed with exit code {cp.returncode}")


def request_json(url: str, *, github_token: str | None = None, timeout: int = 30) -> tuple[int, object]:
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
        headers["Accept"] = "application/vnd.github+json"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = int(resp.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = int(exc.code)
    try:
        obj: object = json.loads(raw.decode("utf-8-sig")) if raw else {}
    except json.JSONDecodeError:
        obj = {"error": "non-JSON response"}
    return status, obj


def fetch_magic_eden_stats(listing_agg_mode: bool) -> dict[str, Any]:
    url = ME_STATS + "?" + urllib.parse.urlencode({"listingAggMode": str(listing_agg_mode).lower()})
    last_error: Exception | None = None
    for attempt, delay in enumerate(RETRY_DELAYS_SECONDS, start=1):
        if delay:
            time.sleep(delay)
        try:
            status, obj = request_json(url)
            if status == 200 and isinstance(obj, dict):
                return obj
            last_error = RuntimeError(f"Magic Eden stats request failed: HTTP {status}")
            if status not in (408, 429) and status < 500:
                break
        except NETWORK_ERRORS as exc:
            last_error = exc
        print(f"Magic Eden request attempt {attempt} failed; retrying.", flush=True)
    raise RuntimeError(f"Magic Eden stats unavailable after retries: {last_error}")


def parse_snapshot(magic_eden: dict[str, Any], aggregated: dict[str, Any]) -> tuple[float, int]:
    if "floorPrice" not in magic_eden:
        raise RuntimeError("Magic Eden non-aggregated stats omitted floorPrice.")
    if "listedCount" not in aggregated:
        raise RuntimeError("Magic Eden aggregated stats omitted listedCount.")
    floor_raw = magic_eden["floorPrice"]
    listed_raw = aggregated["listedCount"]
    if isinstance(floor_raw, bool) or not isinstance(floor_raw, (int, float)):
        raise RuntimeError("Magic Eden floorPrice is not numeric.")
    if isinstance(listed_raw, bool) or not isinstance(listed_raw, (int, float)):
        raise RuntimeError("Magic Eden listedCount is not numeric.")
    if floor_raw < 0 or listed_raw < 0 or int(listed_raw) != listed_raw:
        raise RuntimeError("Magic Eden stats contain invalid negative/fractional values.")
    floor_sol = float(floor_raw) / 1_000_000_000
    return floor_sol, int(listed_raw)


def read_history(root: Path = ROOT) -> list[dict[str, str]]:
    path = root / "data" / "history" / "floor_listings.csv"
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise RuntimeError("Floor/listings history is empty.")
    return rows


def load_checkpoints(root: Path = ROOT) -> dict[str, Any]:
    path = root / "data" / "state" / "checkpoints.json"
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError("checkpoints.json must be an object.")
    return obj


def prepare_snapshot(root: Path, snapshot_date: str, floor_sol: float, listed_count: int) -> bool:
    rows = read_history(root)
    checkpoints = load_checkpoints(root)
    checkpoint = str(checkpoints.get("floor_checkpoint_date") or "")
    last_date = rows[-1]["snapshot_date"]
    if last_date != checkpoint:
        raise RuntimeError(f"Floor history/checkpoint mismatch: history={last_date}, checkpoint={checkpoint}")
    if checkpoint > snapshot_date:
        raise RuntimeError("Floor checkpoint is in the future relative to this UTC snapshot date.")
    if checkpoint == snapshot_date:
        print(f"NO-OP: floor/listings already checkpointed for {snapshot_date}.")
        return False

    rows = [row for row in rows if row["snapshot_date"] != snapshot_date]
    rows.append({
        "snapshot_date": snapshot_date,
        "floor_sol": format(floor_sol, ".9f").rstrip("0").rstrip(".") or "0",
        "listed_count": str(listed_count),
        "source": "magic_eden_snapshot",
    })
    rows.sort(key=lambda row: row["snapshot_date"])

    history_path = root / "data" / "history" / "floor_listings.csv"
    with history_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=("snapshot_date", "floor_sol", "listed_count", "source"), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    checkpoints["floor_checkpoint_date"] = snapshot_date
    cp_path = root / "data" / "state" / "checkpoints.json"
    cp_path.write_text(json.dumps(checkpoints, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


def run_validation(root: Path) -> None:
    run_python("scripts/build_dashboard_data.py", "--only-floor", cwd=root)
    run_python("-m", "unittest", "discover", "-s", "tests", "-v", cwd=root)
    run_python("scripts/validate_cutover.py", cwd=root)
    run_python("scripts/validate_live.py", cwd=root)


def changed_paths(root: Path = ROOT) -> list[str]:
    lines = git("status", "--porcelain=v1", "-uall", cwd=root).stdout.splitlines()
    return sorted(line[3:] for line in lines if len(line) >= 4)


def assert_exact_changes(root: Path = ROOT) -> list[str]:
    actual = changed_paths(root)
    required = set(REQUIRED_PATHS)
    allowed = set(ALLOWED_PATHS)
    if not required.issubset(actual) or not set(actual).issubset(allowed):
        raise RuntimeError(
            f"Unexpected floor release inventory. required={sorted(required)}, "
            f"allowed={sorted(allowed)}, actual={actual}"
        )
    cp = git("diff", "--check", cwd=root, check=False)
    if cp.returncode:
        raise RuntimeError(f"git diff --check failed:\n{cp.stdout}")
    return actual


def normalize_origin(value: str) -> str | None:
    try:
        parsed = urllib.parse.urlsplit(value.strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host.endswith(".pages.dev"):
        return None
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def deployment_candidates(commit: str, github_token: str) -> set[str]:
    candidates = set(PAGES_FALLBACK_ORIGINS)
    for endpoint in (f"commits/{commit}/status", f"commits/{commit}/check-runs"):
        try:
            status, obj = request_json(f"{GITHUB_API}/{endpoint}", github_token=github_token or None)
        except NETWORK_ERRORS as exc:
            print(f"Deployment discovery transient network error ({endpoint}): {exc}", flush=True)
            continue
        if status != 200:
            continue
        encoded = json.dumps(obj)
        for raw in re.findall(r"https://[^\s\"'<>]+", encoded):
            origin = normalize_origin(raw.rstrip("\\).,]"))
            if origin:
                candidates.add(origin)
    return candidates


def expected_public_json(root: Path = ROOT) -> dict[str, object]:
    return {
        "data/floor-listings.json": json.loads((root / "site/public/data/floor-listings.json").read_text(encoding="utf-8")),
        "data/summary.json": json.loads((root / "site/public/data/summary.json").read_text(encoding="utf-8")),
    }


def candidate_matches(origin: str, expected: dict[str, object], commit: str) -> bool:
    for rel, wanted in expected.items():
        try:
            status, obj = request_json(f"{origin}/{rel}?release={commit}")
        except NETWORK_ERRORS as exc:
            print(f"Deployment verification transient network error ({origin}): {exc}", flush=True)
            return False
        if status != 200 or obj != wanted:
            return False
    return True


def wait_for_deployment(commit: str, *, timeout_seconds: int = 600, poll_seconds: int = 15) -> str:
    expected = expected_public_json(ROOT)
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    deadline = time.monotonic() + timeout_seconds
    while True:
        candidates = deployment_candidates(commit, token)
        for origin in sorted(candidates):
            if candidate_matches(origin, expected, commit):
                return origin
        if time.monotonic() >= deadline:
            raise RuntimeError("Cloudflare Pages floor/listings deployment verification timed out.")
        time.sleep(min(poll_seconds, max(1, deadline - time.monotonic())))



def prove_current_date_if_present(snapshot_date: str, root: Path = ROOT) -> bool:
    """Prove an already-committed same-day snapshot without re-querying Magic Eden."""
    checkpoints = load_checkpoints(root)
    rows = read_history(root)
    checkpoint = str(checkpoints.get("floor_checkpoint_date") or "")
    last_date = rows[-1]["snapshot_date"]
    if checkpoint != last_date:
        raise RuntimeError(f"Floor history/checkpoint mismatch: history={last_date}, checkpoint={checkpoint}")
    if checkpoint != snapshot_date:
        return False

    if changed_paths(root):
        raise RuntimeError(f"Production runner worktree is not clean: {changed_paths(root)[:5]}")
    if git("branch", "--show-current", cwd=root).stdout.strip() != "main":
        raise RuntimeError("Floor/listings production mutation requires branch main.")

    run_python("scripts/validate_live.py", cwd=root)
    commit = git("rev-parse", "HEAD", cwd=root).stdout.strip()
    origin = wait_for_deployment(commit)
    print(f"NO-OP PROVEN: {snapshot_date} floor/listings already current and deployed at {origin}.")
    return True

def dry_run(snapshot_date: str, floor_sol: float, listed_count: int) -> int:
    before = git("status", "--porcelain=v1", "-uall").stdout
    with tempfile.TemporaryDirectory(prefix="guild-saga-floor-dry-run-") as raw:
        temp_root = Path(raw) / "repo"
        git("clone", "--no-hardlinks", "--quiet", str(ROOT), str(temp_root))
        changed = prepare_snapshot(temp_root, snapshot_date, floor_sol, listed_count)
        if changed:
            run_validation(temp_root)
            assert_exact_changes(temp_root)
        else:
            run_python("scripts/validate_live.py", cwd=temp_root)
    after = git("status", "--porcelain=v1", "-uall").stdout
    if before != after:
        raise RuntimeError("Floor/listings dry run changed the source worktree.")
    print(f"DRY RUN PASS: {snapshot_date} candidate floor={floor_sol:g} SOL, listed={listed_count}; no commit/push.")
    return 0


def production_run(snapshot_date: str, floor_sol: float, listed_count: int) -> int:
    if changed_paths(ROOT):
        raise RuntimeError(f"Production runner worktree is not clean: {changed_paths(ROOT)[:5]}")
    if git("branch", "--show-current").stdout.strip() != "main":
        raise RuntimeError("Floor/listings production mutation requires branch main.")

    changed = prepare_snapshot(ROOT, snapshot_date, floor_sol, listed_count)
    if not changed:
        run_python("scripts/validate_live.py")
        commit = git("rev-parse", "HEAD").stdout.strip()
        origin = wait_for_deployment(commit)
        print(f"NO-OP PROVEN: {snapshot_date} floor/listings already current and deployed at {origin}.")
        return 0

    run_validation(ROOT)
    release_paths = assert_exact_changes(ROOT)

    git("fetch", "origin")
    head_before = git("rev-parse", "HEAD").stdout.strip()
    origin_before = git("rev-parse", "origin/main").stdout.strip()
    if head_before != origin_before:
        raise RuntimeError("origin/main moved before floor/listings commit; refusing release.")

    git("add", *release_paths)
    staged = sorted(
        line[3:]
        for line in git("status", "--porcelain=v1", "-uall").stdout.splitlines()
        if len(line) >= 4 and line[0] != " "
    )
    if staged != release_paths:
        raise RuntimeError(f"Unexpected staged floor release inventory: {staged}")
    cached_check = git("diff", "--cached", "--check", check=False)
    if cached_check.returncode:
        raise RuntimeError(f"git diff --cached --check failed:\n{cached_check.stdout}")

    git("commit", "-m", f"Update daily floor/listings {snapshot_date}")
    commit = git("rev-parse", "HEAD").stdout.strip()
    parent = git("rev-parse", "HEAD^").stdout.strip()
    git("fetch", "origin")
    if git("rev-parse", "origin/main").stdout.strip() != parent:
        raise RuntimeError("origin/main raced ahead after floor/listings commit; refusing push.")
    git("push", "origin", "HEAD:main")
    origin = wait_for_deployment(commit)
    print(f"FLOOR/LISTINGS PRODUCTION PASS: {commit} deployed at {origin}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("dry-run", "production"), required=True)
    args = parser.parse_args()

    snapshot_date = datetime.now(timezone.utc).date().isoformat()

    # The second daily cron is a recovery opportunity, not a second sample.
    # If today's floor snapshot is already committed, prove its deployment and
    # avoid turning a temporary marketplace outage into a false failed retry.
    if args.mode == "production" and prove_current_date_if_present(snapshot_date):
        return 0

    me = fetch_magic_eden_stats(False)
    agg = fetch_magic_eden_stats(True)
    floor_sol, listed_count = parse_snapshot(me, agg)
    print(f"Magic Eden snapshot {snapshot_date}: floor={floor_sol:g} SOL, listed={listed_count}")

    if args.mode == "dry-run":
        return dry_run(snapshot_date, floor_sol, listed_count)
    return production_run(snapshot_date, floor_sol, listed_count)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(1)
