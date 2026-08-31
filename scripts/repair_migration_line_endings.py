#!/usr/bin/env python3
"""Repair Windows newline conversion in byte-stable baseline fixtures.

The baseline archive uses SHA-256 byte hashes. This script accepts only newline
conversion differences, refuses real content changes, restores LF bytes where
required, and runs the baseline archive contract after repair.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "docs" / "migration" / "2026-08-26"
MANIFEST = ARCHIVE / "source-manifest.json"
GITATTRIBUTES = ROOT / ".gitattributes"

ATTR_BLOCK = """# Byte-stable Guild Saga baseline fixtures. Preserve the exact supplied bytes.\ndata/baseline/*.csv -text -diff\n# Preserved source data is normalized to LF for stable manifest hashes.\ndocs/migration/2026-08-26/source-data/*.csv text eol=lf\n"""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def lf_normalize(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def manifest_targets(manifest: dict) -> list[tuple[Path, str, str]]:
    out: list[tuple[Path, str, str]] = []
    for source_name, canonical in manifest["canonical_dataset_mapping"].items():
        if canonical.startswith("data/"):
            expected = manifest["source_datasets"][source_name]["sha256"]
            out.append((ROOT / canonical, expected, source_name))

    floor_name = "guild_saga_floor_listings_history.csv"
    out.append((
        ARCHIVE / "source-data" / floor_name,
        manifest["source_datasets"][floor_name]["sha256"],
        floor_name + " (preserved source)",
    ))
    return out


def main() -> int:
    print("=" * 78)
    print("Guild Saga - Repair Byte-Stable Baseline Fixture Line Endings")
    print("=" * 78)
    print("Only CRLF/LF byte differences are eligible for repair.")
    print("Any real content difference aborts before writes.\n")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    targets = manifest_targets(manifest)
    repairs: list[tuple[Path, bytes, str]] = []

    for path, expected, label in targets:
        if not path.exists():
            raise RuntimeError(f"Missing hashed file: {path.relative_to(ROOT)}")
        raw = path.read_bytes()
        if sha256_bytes(raw) == expected:
            continue
        normalized = lf_normalize(raw)
        if sha256_bytes(normalized) != expected:
            raise RuntimeError(
                "REAL CONTENT MISMATCH - refusing repair before writes:\n"
                f"  source: {label}\n"
                f"  path: {path.relative_to(ROOT)}"
            )
        repairs.append((path, normalized, label))

    for path, normalized, _label in repairs:
        path.write_bytes(normalized)

    GITATTRIBUTES.write_text(ATTR_BLOCK, encoding="utf-8", newline="\n")

    for path, expected, label in targets:
        if sha256_bytes(path.read_bytes()) != expected:
            raise RuntimeError(f"Post-repair hash verification failed for {label}")

    cp = subprocess.run(
        [sys.executable, "-m", "unittest", "tests.test_migration_archive", "-v"],
        cwd=ROOT,
        text=True,
    )
    if cp.returncode != 0:
        raise RuntimeError(f"Baseline archive test failed with exit code {cp.returncode}")

    print(f"[PASS] baseline hashes exact; newline-only files repaired: {len(repairs)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[FAIL] {exc}")
        raise SystemExit(1)
