#!/usr/bin/env python3
"""Production-safe validation for current Guild Saga website data."""
from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUB = ROOT / "site" / "public" / "data"
GOLD_PATH = ROOT / "tests" / "golden" / "2026-08-26.json"
CHECKPOINTS_PATH = ROOT / "data" / "state" / "checkpoints.json"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def close(a, b, tol=1e-8):
    return math.isclose(float(a), float(b), rel_tol=0, abs_tol=tol)


def parse_iso_or_date(value):
    if not value:
        return None
    value = str(value).strip()
    if len(value) == 10:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(
        value.replace(" UTC", "+00:00").replace("Z", "+00:00")
    ).astimezone(timezone.utc)


def assert_unique(values, label):
    values = list(values)
    assert len(values) == len(set(values)), f"duplicate {label}"


def validate_public(pub: Path, max_age_hours: float | None = None):
    s = load(pub / "summary.json")
    h = load(pub / "hero-state.json")
    m = load(pub / "market-history.json")
    f = load(pub / "floor-listings.json")
    launch = load(pub / "launch.json")
    gold = load(GOLD_PATH)
    gold_summary = gold["summary"]

    assert s.get("cutover_date") == gold["cutover_date"], "cutover_date is immutable"

    # Historical launch facts are immutable.
    assert launch["kpis"] == gold_summary["launch"]
    assert s["launch"] == gold_summary["launch"]

    hero = s["hero"]
    assert hero["active_supply"] + hero["burned"] == 10_000
    assert hero["burned"] >= gold_summary["hero"]["burned"], "burn count cannot decrease"
    assert hero["active_supply"] <= gold_summary["hero"]["active_supply"], "active supply cannot increase after burn cutover"
    assert 0 <= hero["staked_heroes"] <= hero["active_supply"]
    expected_staked_pct = (
        round(100.0 * hero["staked_heroes"] / hero["active_supply"], 1)
        if hero["active_supply"] else 0.0
    )
    assert close(hero["staked_supply_pct"], expected_staked_pct, 1e-9)

    quest = {x["bucket"]: x["heroes"] for x in h["quest_activity"]}
    assert sum(quest.values()) == hero["staked_heroes"]
    assert hero["quest_active_30d"] == quest.get("Active 0–7d", 0) + quest.get("Idle 8–30d", 0)
    assert hero["quest_limbo_1y"] == quest.get("Idle 1+ year", 0) + quest.get("Never quested", 0)

    assert sum(x["burned"] for x in h["burned_by_rarity"]) == hero["burned"]
    assert h["burn_history"], "burn history cannot be empty"
    assert h["burn_history"][-1]["total_burns"] == hero["burned"]
    assert all(
        b["total_burns"] >= a["total_burns"]
        for a, b in zip(h["burn_history"], h["burn_history"][1:])
    )

    holders = h["holder_distribution"]
    assert sum(x["holder_count"] for x in holders) == hero["beneficial_holders"]
    assert sum(x["hero_supply"] for x in holders) == hero["active_supply"]
    assert_unique((x["tier"] for x in holders), "holder tier")

    market = s["market"]
    assert m["kpis"] == market
    assert market["secondary_sales"] >= gold_summary["market"]["secondary_sales"]
    assert market["secondary_volume_sol"] + 1e-9 >= gold_summary["market"]["secondary_volume_sol"]
    assert market["unique_buyers"] >= gold_summary["market"]["unique_buyers"]
    assert market["heroes_ever_sold"] >= gold_summary["market"]["heroes_ever_sold"]
    assert market["guild_saga_royalties_sol"] + 1e-9 >= gold_summary["market"]["guild_saga_royalties_sol"]
    assert close(
        market["mint_plus_royalties_sol"],
        s["launch"]["public_mint_proceeds_sol"] + market["guild_saga_royalties_sol"],
        1e-8,
    )
    assert sum(x["sales"] for x in m["monthly_activity"]) == market["secondary_sales"]
    assert close(
        sum(x["volume_sol"] for x in m["monthly_activity"]),
        market["secondary_volume_sol"],
        0.05,
    )
    assert sum(x["heroes"] for x in m["first_resale_timing"]) == s["launch"]["public_mint_supply"]

    assert f["kpis"] == s["floor"]
    assert f["history"], "floor/listing history cannot be empty"
    assert f["history"][-1]["snapshot_date"] == f["as_of"]
    assert f["history"][-1]["floor_sol"] == s["floor"]["floor_sol"]
    assert f["history"][-1]["listed_count"] == s["floor"]["listed_count"]
    dates = [x["snapshot_date"] for x in f["history"]]
    assert_unique(dates, "floor/listing snapshot date")
    assert dates == sorted(dates), "floor/listing dates must be ascending"

    domain_as_of = {
        "hero_state": h["as_of"],
        "market": m["as_of"],
        "floor": f["as_of"],
    }
    now = datetime.now(timezone.utc)
    for domain, value in domain_as_of.items():
        dt = parse_iso_or_date(value)
        assert dt is not None
        assert dt <= now, f"{domain} as_of is in the future: {value}"
        if max_age_hours is not None:
            age_hours = (now - dt).total_seconds() / 3600.0
            assert age_hours <= max_age_hours, f"{domain} is stale ({age_hours:.1f}h > {max_age_hours}h)"

    return domain_as_of


def validate_canonical_if_present():
    data = ROOT / "data"
    if not data.exists():
        return

    assets_path = data / "baseline" / "assets.csv"
    rarity_path = data / "baseline" / "rarity.csv"
    market_base = data / "baseline" / "market_sales.csv"
    hero_deltas = data / "state" / "hero_deltas.csv"
    live_sales = data / "state" / "market_live_sales.csv"

    asset_mints: set[str] = set()
    if assets_path.exists():
        with assets_path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 10_000
        assert_unique((r["mint"] for r in rows), "baseline Hero mint")
        asset_mints = {r["mint"] for r in rows}

    if rarity_path.exists():
        with rarity_path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 10_000
        assert_unique((r["mint"] for r in rows), "rarity Hero mint")
        if asset_mints:
            assert {r["mint"] for r in rows} == asset_mints, "rarity and baseline mint universes differ"

    if hero_deltas.exists():
        with hero_deltas.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert_unique((r["mint"] for r in rows), "Hero delta mint")
        if asset_mints:
            unknown = sorted({r["mint"] for r in rows} - asset_mints)
            assert not unknown, f"Hero deltas contain unknown mints: {unknown[:3]}"

    sale_keys = set()
    for path, label in ((market_base, "baseline sale"), (live_sales, "live sale")):
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        for r in rows:
            if asset_mints:
                assert r["mint"] in asset_mints, f"{label} contains unknown mint {r['mint']}"
            key = (r["signature"], r["mint"])
            assert key not in sale_keys, f"duplicate sale key across ledgers: {key}"
            sale_keys.add(key)

    if CHECKPOINTS_PATH.exists():
        cp = load(CHECKPOINTS_PATH)
        cutover = parse_iso_or_date(cp["cutover_date"])
        hero_cp = parse_iso_or_date(cp["hero_state_checkpoint"])
        market_cp = parse_iso_or_date(cp["market_checkpoint_date"])
        floor_cp = parse_iso_or_date(cp["floor_checkpoint_date"])
        assert cutover and hero_cp and market_cp and floor_cp
        assert hero_cp >= cutover
        assert market_cp >= cutover
        assert floor_cp >= cutover


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, default=DEFAULT_PUB)
    ap.add_argument(
        "--max-age-hours",
        type=float,
        default=None,
        help="Optional CI freshness gate; omitted for local/offline validation.",
    )
    args = ap.parse_args()

    as_of = validate_public(args.data_dir, args.max_age_hours)
    validate_canonical_if_present()
    print("PASS: live production invariants are healthy.")
    print("Domain freshness:", ", ".join(f"{k}={v}" for k, v in as_of.items()))


if __name__ == "__main__":
    main()
