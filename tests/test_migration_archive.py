from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "docs" / "migration" / "2026-08-26"
MANIFEST = MIGRATION / "source-manifest.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MigrationArchiveContracts(unittest.TestCase):
    def test_archived_final_queries_match_manifest(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        for name, info in manifest["dune_queries"].items():
            path = ROOT / info["archived_path"]
            self.assertTrue(path.exists(), name)
            self.assertEqual(sha256(path), info["sha256"], name)

    def test_immutable_canonical_baselines_match_supplied_sources(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        mappings = manifest["canonical_dataset_mapping"]
        for source_name, canonical in mappings.items():
            if not canonical.startswith("data/"):
                continue
            expected = manifest["uploaded_datasets"][source_name]["sha256"]
            self.assertEqual(sha256(ROOT / canonical), expected, source_name)

    def test_archived_original_floor_history_matches_manifest(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        name = "guild_saga_tensor_history_dune.csv"
        source = MIGRATION / "source-data" / name
        self.assertEqual(sha256(source), manifest["uploaded_datasets"][name]["sha256"])


if __name__ == "__main__":
    unittest.main()
