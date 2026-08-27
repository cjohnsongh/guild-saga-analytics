#!/usr/bin/env python3
from __future__ import annotations
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUB = ROOT / "site" / "public" / "data"
GOLD = json.loads((ROOT / "tests" / "golden" / "2026-08-26.json").read_text(encoding="utf-8"))
SUMMARY = json.loads((PUB / "summary.json").read_text(encoding="utf-8"))
HERO = json.loads((PUB / "hero-state.json").read_text(encoding="utf-8"))
MARKET = json.loads((PUB / "market-history.json").read_text(encoding="utf-8"))
FLOOR = json.loads((PUB / "floor-listings.json").read_text(encoding="utf-8"))


def same(a, b):
    if isinstance(a, float) or isinstance(b, float):
        return math.isclose(float(a), float(b), rel_tol=0, abs_tol=1e-9)
    return a == b


def check_group(name):
    got = SUMMARY[name]
    expected = GOLD[name]
    for k, v in expected.items():
        if not same(got[k], v):
            raise AssertionError(f"{name}.{k}: got {got[k]!r}, expected {v!r}")


for group in ("hero", "market", "floor", "launch"):
    check_group(group)

# Structural invariants that should remain true on future live runs.
assert SUMMARY["hero"]["active_supply"] + SUMMARY["hero"]["burned"] == 10000
assert SUMMARY["hero"]["staked_heroes"] <= SUMMARY["hero"]["active_supply"]
assert sum(x["heroes"] for x in HERO["quest_activity"]) == SUMMARY["hero"]["staked_heroes"]
assert sum(x["burned"] for x in HERO["burned_by_rarity"]) == SUMMARY["hero"]["burned"]
assert sum(x["holder_count"] for x in HERO["holder_distribution"]) == SUMMARY["hero"]["beneficial_holders"]
assert MARKET["monthly_activity"][-1]["month"] <= MARKET["as_of"][:7] + "-01"
assert FLOOR["history"][-1]["snapshot_date"] == FLOOR["as_of"]
assert FLOOR["history"][-1]["listed_count"] == SUMMARY["floor"]["listed_count"]

print("PASS: offline snapshot exactly matches the Aug. 26, 2026 Dune gold master.")
print("PASS: structural invariants are healthy.")
