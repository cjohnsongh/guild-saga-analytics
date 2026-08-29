#!/usr/bin/env python3
"""Validate the permanently frozen Aug. 26 cutover fixture.

This test must stay stable forever. It does NOT compare today's live site to
Aug. 26, which would make legitimate future updates fail.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "cutover-2026-08-26" / "public-data"
GOLD = ROOT / "tests" / "golden" / "2026-08-26.json"


def load(name):
    return json.loads((FIXTURE / name).read_text(encoding="utf-8"))


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def same(a, b):
    if isinstance(a, float) or isinstance(b, float):
        return math.isclose(float(a), float(b), rel_tol=0, abs_tol=1e-9)
    return a == b


def main():
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    summary = load("summary.json")
    hero = load("hero-state.json")
    market = load("market-history.json")
    floor = load("floor-listings.json")

    assert summary["cutover_date"] == "2026-08-26"
    for group in ("hero", "market", "floor", "launch"):
        for key, expected in gold["summary"][group].items():
            got = summary[group][key]
            assert same(got, expected), f"{group}.{key}: got {got!r}, expected {expected!r}"

    for name, expected_hash in gold["file_sha256"].items():
        got_hash = sha256(FIXTURE / name)
        assert got_hash == expected_hash, f"{name}: fixture hash changed"

    assert summary["hero"]["active_supply"] + summary["hero"]["burned"] == 10_000
    assert sum(x["heroes"] for x in hero["quest_activity"]) == summary["hero"]["staked_heroes"]
    assert sum(x["burned"] for x in hero["burned_by_rarity"]) == summary["hero"]["burned"]
    assert sum(x["holder_count"] for x in hero["holder_distribution"]) == summary["hero"]["beneficial_holders"]
    assert market["kpis"] == summary["market"]
    assert floor["kpis"] == summary["floor"]

    print("PASS: Aug. 26, 2026 cutover fixture is unchanged and internally coherent.")


if __name__ == "__main__":
    main()
