from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_ARCHIVE = ROOT / "docs" / "migration" / "2026-08-26"
MANIFEST = BASELINE_ARCHIVE / "source-manifest.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BaselineArchiveContracts(unittest.TestCase):
    def test_immutable_canonical_baselines_match_preserved_sources(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        mappings = manifest["canonical_dataset_mapping"]
        for source_name, canonical in mappings.items():
            if not canonical.startswith("data/"):
                continue
            expected = manifest["source_datasets"][source_name]["sha256"]
            self.assertEqual(sha256(ROOT / canonical), expected, source_name)

            attrs = subprocess.run(
                ["git", "check-attr", "text", "diff", "--", canonical],
                cwd=ROOT,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.splitlines()
            self.assertTrue(attrs[0].endswith(": text: unset"), attrs)
            self.assertTrue(attrs[1].endswith(": diff: unset"), attrs)

    def test_preserved_floor_history_matches_manifest(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        name = "guild_saga_floor_listings_history.csv"
        source = BASELINE_ARCHIVE / "source-data" / name
        self.assertTrue(source.exists())
        self.assertEqual(sha256(source), manifest["source_datasets"][name]["sha256"])

    def test_manifest_contains_only_neutral_baseline_provenance(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["baseline_date"], "2026-08-26")
        self.assertEqual(len(manifest["source_datasets"]), 5)


if __name__ == "__main__":
    unittest.main()
