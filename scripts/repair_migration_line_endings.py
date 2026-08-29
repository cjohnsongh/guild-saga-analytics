#!/usr/bin/env python3
"""Repair Windows newline conversion in byte-stable migration fixtures.

The migration archive intentionally uses SHA-256 byte hashes. On Windows,
Git's automatic CRLF checkout can change only the newline bytes and make the
immutable-source contract fail even though the CSV/text content is identical.

This script is conservative:
- preflights every manifest-hashed file before writing anything;
- only accepts a mismatch when LF-normalizing the current bytes reproduces the
  exact manifest SHA-256;
- refuses any real content mismatch;
- adds .gitattributes rules so future Windows checkouts preserve LF bytes;
- runs the migration archive contract after repair.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "docs" / "migration" / "2026-08-26"
MANIFEST = MIGRATION / "source-manifest.json"
GITATTRIBUTES = ROOT / ".gitattributes"

ATTR_BLOCK = """# Byte-stable Guild Saga migration fixtures (manifest SHA-256 values are LF-based).\ndata/baseline/*.csv text eol=lf\ndocs/migration/2026-08-26/dune-final-queries/*.txt text eol=lf\ndocs/migration/2026-08-26/source-data/*.csv text eol=lf\n"""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def lf_normalize(data: bytes) -> bytes:
    # CRLF is the expected Windows conversion. Handle bare CR conservatively too.
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def manifest_targets(manifest: dict) -> list[tuple[Path, str, str]]:
    out: list[tuple[Path, str, str]] = []

    # Archived Dune query text files.
    for name, info in manifest["dune_queries"].items():
        out.append((ROOT / info["archived_path"], info["sha256"], name))

    # Canonical immutable datasets that map directly into data/.
    for source_name, canonical in manifest["canonical_dataset_mapping"].items():
        if canonical.startswith("data/"):
            expected = manifest["uploaded_datasets"][source_name]["sha256"]
            out.append((ROOT / canonical, expected, source_name))

    # Archived original floor-history source is also byte-hashed by the tests.
    tensor_name = "guild_saga_tensor_history_dune.csv"
    out.append(
        (
            MIGRATION / "source-data" / tensor_name,
            manifest["uploaded_datasets"][tensor_name]["sha256"],
            tensor_name + " (archived source)",
        )
    )
    return out


def add_attributes(existing: str) -> tuple[str, bool]:
    required = [
        "data/baseline/*.csv text eol=lf",
        "docs/migration/2026-08-26/dune-final-queries/*.txt text eol=lf",
        "docs/migration/2026-08-26/source-data/*.csv text eol=lf",
    ]
    lines = existing.splitlines()
    if all(line in lines for line in required):
        return existing, False

    new = existing
    if new and not new.endswith("\n"):
        new += "\n"
    if new and not new.endswith("\n\n"):
        new += "\n"
    new += ATTR_BLOCK
    return new, True


def main() -> int:
    print("=" * 78)
    print("Guild Saga — Repair Byte-Stable Migration Fixture Line Endings")
    print("=" * 78)
    print("Only CRLF/LF byte differences are eligible for repair.")
    print("Any real content difference aborts before writes.\n")

    if not MANIFEST.exists():
        raise RuntimeError(f"Missing manifest: {MANIFEST.relative_to(ROOT)}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    targets = manifest_targets(manifest)

    print("[1/4] Preflighting manifest-hashed files")
    repairs: list[tuple[Path, bytes, str]] = []
    exact = 0
    for path, expected, label in targets:
        if not path.exists():
            raise RuntimeError(f"Missing hashed file: {path.relative_to(ROOT)}")
        raw = path.read_bytes()
        got = sha256_bytes(raw)
        if got == expected:
            exact += 1
            continue
        normalized = lf_normalize(raw)
        normalized_hash = sha256_bytes(normalized)
        if normalized_hash != expected:
            raise RuntimeError(
                "REAL CONTENT MISMATCH — refusing repair before writes:\n"
                f"  source:   {label}\n"
                f"  path:     {path.relative_to(ROOT)}\n"
                f"  expected: {expected}\n"
                f"  current:  {got}\n"
                f"  LF form:  {normalized_hash}"
            )
        repairs.append((path, normalized, label))
        print(f"    CRLF-only mismatch: {path.relative_to(ROOT)}")

    print(f"    exact byte matches: {exact}")
    print(f"    newline-only fixes: {len(repairs)}")

    print("[2/4] Preparing persistent Git newline rules")
    old_attrs = GITATTRIBUTES.read_text(encoding="utf-8") if GITATTRIBUTES.exists() else ""
    new_attrs, attrs_changed = add_attributes(old_attrs)
    print(f"    .gitattributes update needed: {'YES' if attrs_changed else 'NO'}")

    print("[3/4] Applying newline-only repairs")
    # Preflight is complete, so every mismatch is proven newline-only.
    for path, normalized, _label in repairs:
        path.write_bytes(normalized)
    if attrs_changed:
        GITATTRIBUTES.write_text(new_attrs, encoding="utf-8", newline="\n")

    # Verify exact hashes immediately.
    for path, expected, label in targets:
        got = sha256_bytes(path.read_bytes())
        if got != expected:
            raise RuntimeError(f"Post-repair hash verification failed for {label}: {got}")

    print("    all manifest hashes: EXACT")

    print("[4/4] Running migration archive contract")
    cp = subprocess.run(
        [sys.executable, "-m", "unittest", "tests.test_migration_archive", "-v"],
        cwd=ROOT,
        text=True,
    )
    if cp.returncode != 0:
        raise RuntimeError(f"Migration archive test still failed with exit code {cp.returncode}")

    print()
    print("=" * 78)
    print("[PASS] MIGRATION FIXTURES ARE BYTE-STABLE AGAIN")
    print("=" * 78)
    print(f"Newline-only files repaired: {len(repairs)}")
    print("Manifest hashes:             EXACT")
    print("Migration archive tests:     PASS")
    print(".gitattributes LF rules:     PRESENT")
    print("No dataset values/rows were changed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print()
        print(f"[FAIL] {exc}")
        raise SystemExit(1)
