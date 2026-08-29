#!/usr/bin/env python3
"""Crash-resumable Guild Saga webhook release orchestration.

The tracked batch manifest is the durable hand-off between ephemeral runners.
Its introducing Git commit identifies the release without embedding its own SHA.
D1 remains authoritative for whether the exact signature set was acknowledged.

Production order is deliberately fail-closed:
prepare -> validate -> commit -> race-safe push -> exact deploy verification -> ACK.
Dry-run mode uses a disposable Git worktree and never writes Git, Helius, or D1.
"""

from __future__ import annotations

import argparse
import hashlib
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_REL = "data/state/production_batch_manifest.json"
MANIFEST = ROOT / MANIFEST_REL
PREPARED = ROOT / ".guild_saga_recon" / "webhook_batch_prepared.json"
WORKER_ORIGIN = "https://guild-saga-webhook-inbox.cjohnson80.workers.dev"
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "cjohnsongh/guild-saga-analytics")
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}"
VERIFY_FILES = (
    "site/public/data/hero-state.json",
    "site/public/data/market-history.json",
    "site/public/data/summary.json",
)
USER_AGENT = "GuildSagaAnalytics-ProductionPipeline/1.0"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return obj


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def git(*args: str, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run(
        ["git", *args], cwd=cwd, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    if check and cp.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{cp.stdout}")
    return cp


def run_python(*args: str, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    cp = subprocess.run([sys.executable, *args], cwd=cwd, env=env)
    if cp.returncode:
        raise RuntimeError(f"python {' '.join(args)} failed with exit code {cp.returncode}")


def request_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: object | None = None,
    github: bool = False,
    timeout: int = 45,
) -> tuple[int, object, dict[str, str]]:
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = ("Bearer " if github else "Bearer ") + token
    if github:
        headers["Accept"] = "application/vnd.github+json"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = int(resp.status)
            response_headers = {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = int(exc.code)
        response_headers = {k.lower(): v for k, v in exc.headers.items()}
    try:
        obj: object = json.loads(raw.decode("utf-8-sig")) if raw else {}
    except json.JSONDecodeError:
        obj = {"error": "non-JSON response"}
    return status, obj, response_headers


@dataclass(frozen=True)
class Snapshot:
    received_at: str
    events: tuple[dict[str, Any], ...]

    @property
    def signatures(self) -> tuple[str, ...]:
        return tuple(str(row.get("signature") or "") for row in self.events)


class WorkerClient:
    def __init__(self, token: str, origin: str = WORKER_ORIGIN):
        if not token:
            raise RuntimeError("PIPELINE_TOKEN is unavailable.")
        self.token = token
        self.origin = origin.rstrip("/")

    def snapshot(self) -> Snapshot:
        snapshot = ""
        after_received = ""
        after_signature = ""
        events: list[dict[str, Any]] = []
        while True:
            params = {"limit": "100"}
            if snapshot:
                params["snapshot_received_at"] = snapshot
            if after_received:
                params["after_received_at"] = after_received
                params["after_signature"] = after_signature
            status, obj, _ = request_json(
                "GET", self.origin + "/internal/pending?" + urllib.parse.urlencode(params),
                token=self.token,
            )
            if status != 200 or not isinstance(obj, dict) or obj.get("ok") is not True:
                raise RuntimeError(f"Worker pending request failed closed (HTTP {status}).")
            page_snapshot = str(obj.get("snapshot_received_at") or "")
            if not page_snapshot or (snapshot and page_snapshot != snapshot):
                raise RuntimeError("Worker pending pagination did not preserve one stable snapshot.")
            snapshot = page_snapshot
            page = obj.get("events") or []
            if not isinstance(page, list) or any(not isinstance(x, dict) for x in page):
                raise RuntimeError("Worker returned an invalid pending event page.")
            events.extend(page)
            cursor = obj.get("next_cursor")
            if not cursor:
                break
            if not isinstance(cursor, dict):
                raise RuntimeError("Worker returned an invalid pending cursor.")
            after_received = str(cursor.get("after_received_at") or "")
            after_signature = str(cursor.get("after_signature") or "")
            if not after_received or not after_signature:
                raise RuntimeError("Worker returned an incomplete pending cursor.")
        signatures = [str(row.get("signature") or "") for row in events]
        if any(not sig for sig in signatures) or len(signatures) != len(set(signatures)):
            raise RuntimeError("Stable pending snapshot contains blank/duplicate signatures.")
        return Snapshot(snapshot, tuple(events))

    def stats(self) -> dict[str, Any]:
        status, obj, _ = request_json("GET", self.origin + "/internal/stats", token=self.token)
        if status != 200 or not isinstance(obj, dict) or obj.get("ok") is not True:
            raise RuntimeError(f"Worker stats request failed closed (HTTP {status}).")
        return obj

    def ack(self, signatures: list[str]) -> dict[str, Any]:
        status, obj, _ = request_json(
            "POST", self.origin + "/internal/ack", token=self.token,
            body={"signatures": signatures},
        )
        if status != 200 or not isinstance(obj, dict) or obj.get("ok") is not True:
            raise RuntimeError(f"Worker ACK failed closed (HTTP {status}).")
        validate_ack_response(obj, signatures)
        return obj


def validate_ack_response(response: dict[str, Any], signatures: list[str]) -> None:
    expected = len(signatures)
    if len(signatures) != len(set(signatures)) or not signatures:
        raise RuntimeError("Refusing an empty or duplicate ACK signature set.")
    if int(response.get("requested") or 0) != expected:
        raise RuntimeError("ACK requested count did not match the committed manifest.")
    if int(response.get("processed") or 0) != expected:
        raise RuntimeError("ACK did not prove every committed signature processed.")
    if response.get("missing") not in ([], None):
        raise RuntimeError("ACK reported a missing committed signature.")
    if response.get("not_processed") not in ([], None):
        raise RuntimeError("ACK reported a committed signature as not processed.")


def assert_push_base(expected_parent: str, origin_main: str) -> None:
    if not expected_parent or origin_main != expected_parent:
        raise RuntimeError("origin/main raced ahead after commit; refusing push and ACK.")


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    if manifest.get("schema_version") != 1:
        raise RuntimeError("Unsupported production batch manifest schema.")
    signatures = manifest.get("signatures")
    if not isinstance(signatures, list) or not signatures:
        raise RuntimeError("Production batch manifest has no signatures.")
    signatures = [str(x) for x in signatures]
    if any(not x for x in signatures) or len(signatures) != len(set(signatures)):
        raise RuntimeError("Production batch manifest signatures are blank/duplicated.")
    if int(manifest.get("signature_count") or 0) != len(signatures):
        raise RuntimeError("Production batch manifest signature_count mismatch.")
    return signatures


def build_manifest(prepared: dict[str, Any], root: Path = ROOT) -> dict[str, Any]:
    signatures = [str(x) for x in prepared.get("signatures") or []]
    if not signatures or len(signatures) != len(set(signatures)):
        raise RuntimeError("Prepared receipt cannot form a deterministic manifest.")
    canonical = dict(prepared.get("canonical_hashes") or {})
    public = {path: sha256(root / path) for path in VERIFY_FILES}
    manifest = {
        "schema_version": 1,
        "snapshot_received_at": prepared.get("snapshot_received_at"),
        "activation_boundary_utc": prepared.get("activation_boundary_utc"),
        "signatures": signatures,
        "signature_count": len(signatures),
        "canonical_hashes": canonical,
        "public_json_hashes": public,
        "reducer_counts": prepared.get("counts") or {},
    }
    validate_manifest(manifest)
    return manifest


def manifest_release_commit() -> str:
    cp = git("log", "-1", "--format=%H", "--", MANIFEST_REL)
    sha = cp.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("Could not identify the commit that introduced the batch manifest.")
    return sha


def git_file(commit: str, path: str) -> bytes:
    cp = subprocess.run(
        ["git", "show", f"{commit}:{path}"], cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if cp.returncode:
        raise RuntimeError(f"Release commit does not contain {path}.")
    return cp.stdout


def expected_release_json(commit: str, manifest: dict[str, Any]) -> dict[str, object]:
    expected: dict[str, object] = {}
    hashes = manifest.get("public_json_hashes") or {}
    for path in VERIFY_FILES:
        raw = git_file(commit, path)
        if hashes.get(path) != sha256_bytes(raw):
            raise RuntimeError(f"Committed manifest hash mismatch for {path}.")
        expected[path.removeprefix("site/public/")] = json.loads(raw.decode("utf-8-sig"))
    return expected


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
    candidates = {"https://guild-saga-analytics.pages.dev"}
    for endpoint in (f"commits/{commit}/status", f"commits/{commit}/check-runs"):
        status, obj, _ = request_json(
            "GET", f"{GITHUB_API}/{endpoint}", token=github_token or None, github=True,
        )
        if status != 200:
            continue
        encoded = json.dumps(obj)
        for raw in re.findall(r"https://[^\s\"'<>]+", encoded):
            origin = normalize_origin(raw.rstrip("\\).,]"))
            if origin:
                candidates.add(origin)
    return candidates


def deployment_discovery_smoke(commit: str, github_token: str) -> None:
    candidates = deployment_candidates(commit, github_token)
    if not candidates:
        raise RuntimeError("Deployment discovery produced no candidate origins.")
    print(f"Deployment discovery connectivity: PASS ({len(candidates)} candidate origins)")


def candidate_matches(origin: str, expected: dict[str, object], commit: str) -> bool:
    for rel, wanted in expected.items():
        url = f"{origin}/{rel}?release={commit}"
        status, obj, _ = request_json("GET", url)
        if status != 200 or obj != wanted:
            return False
    return True


def wait_for_deployment(
    commit: str,
    manifest: dict[str, Any],
    *,
    timeout_seconds: int = 600,
    poll_seconds: int = 15,
    candidate_source: Callable[[str, str], set[str]] = deployment_candidates,
) -> str:
    expected = expected_release_json(commit, manifest)
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    deadline = time.monotonic() + timeout_seconds
    while True:
        for origin in sorted(candidate_source(commit, token)):
            if candidate_matches(origin, expected, commit):
                return origin
        if time.monotonic() >= deadline:
            raise RuntimeError("Cloudflare Pages deployment verification timed out; D1 was not ACKed.")
        time.sleep(min(poll_seconds, max(1, deadline - time.monotonic())))


def load_local_secret_environment() -> dict[str, str]:
    """Load ignored credentials for local execution without logging values."""
    env = os.environ.copy()
    keys = ROOT / "keys.txt"
    if keys.exists():
        for raw in keys.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("alch"):
                value = line.split("=", 1)[-1].strip()
                env.setdefault("ALCHEMY_API_KEY", value)
            elif "=" in line:
                env.setdefault("HELIUS_API_KEY", line.split("=", 1)[1].strip())
            else:
                env.setdefault("HELIUS_API_KEY", line)
    worker = ROOT / "cloudflare" / "webhook-inbox" / ".env.worker-secrets.local"
    if worker.exists():
        for raw in worker.read_text(encoding="utf-8-sig").splitlines():
            if "=" in raw and not raw.lstrip().startswith("#"):
                key, value = raw.split("=", 1)
                env.setdefault(key.strip(), value.strip().strip("\"'"))
    return env


def provider_health(env: dict[str, str]) -> None:
    keys = {
        "Helius": env.get("HELIUS_API_KEY", "").strip(),
        "Alchemy": env.get("ALCHEMY_API_KEY", "").strip(),
    }
    missing = [name for name, value in keys.items() if not value]
    if missing:
        raise RuntimeError(f"Required provider secret unavailable: {', '.join(missing)}")
    urls = {
        "Helius": "https://mainnet.helius-rpc.com/?api-key=" + keys["Helius"],
        "Alchemy": "https://solana-mainnet.g.alchemy.com/v2/" + keys["Alchemy"],
    }
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getHealth", "params": []}
    for name, url in urls.items():
        status, obj, _ = request_json("POST", url, body=payload)
        if status != 200 or not isinstance(obj, dict) or obj.get("result") != "ok":
            raise RuntimeError(f"{name} provider health check failed closed (HTTP {status}).")
        print(f"{name} provider connectivity: PASS")


def run_validation(root: Path, env: dict[str, str]) -> None:
    run_python("-m", "unittest", "discover", "-s", "tests", "-v", cwd=root, env=env)
    run_python("scripts/validate_cutover.py", cwd=root, env=env)
    run_python("scripts/validate_live.py", cwd=root, env=env)


def dry_run(env: dict[str, str]) -> int:
    """Exercise the live reads and reducer in disposable runner state."""
    before = git("status", "--porcelain=v1", "-uall").stdout
    provider_health(env)
    deployment_discovery_smoke(
        git("rev-parse", "HEAD").stdout.strip(), env.get("GITHUB_TOKEN", "")
    )
    worker = WorkerClient(env.get("PIPELINE_TOKEN", ""))
    snapshot = worker.snapshot()
    print(f"Stable D1 snapshot: {snapshot.received_at}; pending={len(snapshot.events)}")

    with tempfile.TemporaryDirectory(prefix="guild-saga-dry-run-") as raw:
        temp_root = Path(raw) / "repo"
        # A local clone keeps Git structural checks meaningful. Uncommitted local
        # implementation work is intentionally not eligible for a dry run.
        git("clone", "--no-hardlinks", "--quiet", str(ROOT), str(temp_root))
        dry_env = env.copy()
        dry_env["GUILD_SAGA_DRY_RUN"] = "1"
        dry_env["GUILD_SAGA_SNAPSHOT_RECEIVED_AT"] = snapshot.received_at
        run_python("scripts/refresh_webhook_watch_frontier.py", cwd=temp_root, env=dry_env)
        if snapshot.events:
            run_python("scripts/prepare_webhook_batch.py", cwd=temp_root, env=dry_env)
            receipt = load_json(temp_root / ".guild_saga_recon" / "webhook_batch_prepared.json")
            selected = set(map(str, receipt.get("signatures") or []))
            if selected != set(snapshot.signatures):
                raise RuntimeError("Dry-run reducer selected a different stable signature set.")
        else:
            run_validation(temp_root, dry_env)

    after = git("status", "--porcelain=v1", "-uall").stdout
    if after != before:
        raise RuntimeError("Dry run changed the canonical source worktree.")
    print("DRY RUN PASS: no Git push, Helius mutation, D1 mutation, deploy, or ACK.")
    return 0


def recover_committed_batch(worker: WorkerClient) -> None:
    if not MANIFEST.exists():
        return
    manifest = load_json(MANIFEST)
    signatures = validate_manifest(manifest)
    release = manifest_release_commit()
    if git("merge-base", "--is-ancestor", release, "HEAD", check=False).returncode:
        raise RuntimeError("Batch manifest release is not an ancestor of HEAD.")
    print(f"Recovering/proving committed batch {release[:12]} ({len(signatures)} signatures)")
    origin = wait_for_deployment(release, manifest)
    worker.ack(signatures)
    pending_after = set(worker.snapshot().signatures)
    if pending_after.intersection(signatures):
        raise RuntimeError("A committed signature remains pending after exact ACK.")
    print(f"Committed batch deployment + ACK proven at {origin}")


def assert_clean_production_tree() -> None:
    visible = git("status", "--porcelain=v1", "-uall").stdout.splitlines()
    if visible:
        raise RuntimeError(f"Production runner worktree is not clean: {visible[:5]}")
    if git("branch", "--show-current").stdout.strip() != "main":
        raise RuntimeError("Production mutation requires branch main.")


def production_run(env: dict[str, str]) -> int:
    assert_clean_production_tree()
    required = ("PIPELINE_TOKEN", "HELIUS_API_KEY", "ALCHEMY_API_KEY", "HELIUS_WEBHOOK_AUTH")
    missing = [name for name in required if not env.get(name, "").strip()]
    if missing:
        raise RuntimeError("Required production secret unavailable: " + ", ".join(missing))
    provider_health(env)
    worker = WorkerClient(env["PIPELINE_TOKEN"])
    recover_committed_batch(worker)
    snapshot = worker.snapshot()
    if not snapshot.events:
        print("NO-OP: stable D1 snapshot contains no pending events.")
        return 0

    batch_env = env.copy()
    batch_env["GUILD_SAGA_SNAPSHOT_RECEIVED_AT"] = snapshot.received_at
    run_python("scripts/prepare_webhook_batch.py", env=batch_env)
    prepared = load_json(PREPARED)
    if set(map(str, prepared.get("signatures") or [])) != set(snapshot.signatures):
        raise RuntimeError("Reducer batch differs from the selected stable snapshot.")
    write_json(MANIFEST, build_manifest(prepared))
    run_python("scripts/audit_prepared_batch_for_commit.py", env=batch_env)
    run_python("scripts/commit_prepared_release.py", env=batch_env)

    commit = git("rev-parse", "HEAD").stdout.strip()
    parent = git("rev-parse", "HEAD^").stdout.strip()
    git("fetch", "origin")
    assert_push_base(parent, git("rev-parse", "origin/main").stdout.strip())
    git("push", "origin", "HEAD:main")
    manifest = load_json(MANIFEST)
    origin = wait_for_deployment(commit, manifest)
    worker.ack(validate_manifest(manifest))
    if set(worker.snapshot().signatures).intersection(validate_manifest(manifest)):
        raise RuntimeError("Released signatures remain pending after exact ACK.")
    print(f"PRODUCTION PASS: {commit} deployed at {origin}; exact batch ACK proven.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("dry-run", "production"), required=True)
    args = parser.parse_args()
    env = load_local_secret_environment()
    if args.mode == "dry-run":
        return dry_run(env)
    return production_run(env)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        print("Fail closed: no D1 ACK occurs before exact committed deployment proof.", file=sys.stderr)
        raise SystemExit(1)
