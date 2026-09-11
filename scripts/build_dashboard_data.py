#!/usr/bin/env python3
"""Build the public Guild Saga dashboard JSON from canonical local state.

Stdlib-only by design. This script is the offline parity layer: it reproduces the
canonical migration baseline before any live API collector is introduced.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "site" / "public" / "data"
OUT.mkdir(parents=True, exist_ok=True)

NULLS = {"", "<nil>", "NULL", "null", None}
HERO_STATE_FIELDS = [
    "burned", "burn_utc", "burn_signature", "current_raw_owner",
    "current_world_staked", "current_beneficial_owner",
    "current_world_staking_wallet", "latest_event_utc", "latest_signature",
    "quest_user_wallet", "quest_staking_wallet", "current_stake_deposit_utc",
    "current_stake_deposit_signature", "best_known_last_qualifying_quest_utc",
    "best_known_last_qualifying_quest_signature", "quest_history_source",
    "deep_history_status",
]


def null(v):
    return None if v in NULLS else v


def as_int(v, default=0):
    v = null(v)
    if v is None:
        return default
    return int(float(v))


def as_float(v, default=0.0):
    v = null(v)
    if v is None:
        return default
    return float(v)


def parse_time(v):
    v = null(v)
    if v is None:
        return None
    v = v.replace(" UTC", "+00:00").replace("Z", "+00:00")
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_z(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_json(name, obj):
    (OUT / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_compact_json(name, obj):
    # Wallet Explorer is intentionally one privacy-preserving public index rather
    # than address-specific shards. Keep the tracked/downloaded file compact.
    (OUT / name).write_text(
        json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def month_key_from_iso(v):
    return v[:7] + "-01"


def next_month(ymd):
    y, m, _ = map(int, ymd.split("-"))
    if m == 12:
        return f"{y+1:04d}-01-01"
    return f"{y:04d}-{m+1:02d}-01"


def build_hero_state(checkpoints):
    assets = {r["mint"]: r for r in read_csv(DATA / "baseline" / "assets.csv")}
    deltas = read_csv(DATA / "state" / "hero_deltas.csv")
    rarity = {r["mint"]: r["rarity_level"] for r in read_csv(DATA / "baseline" / "rarity.csv")}

    for d in deltas:
        a = assets[d["mint"]]
        for field in HERO_STATE_FIELDS:
            # Empty canonical delta fields mean an explicit SQL NULL/cleared value.
            a[field] = null(d[field])

    as_of = parse_time(checkpoints["hero_state_checkpoint"])
    if as_of is None:
        raise RuntimeError("Missing hero-state checkpoint")

    active_supply = 0
    staked = 0
    beneficial_owners = []
    quest_counts = Counter()
    quest_active_30d = 0
    quest_limbo_1y = 0
    burned_mints = []
    burn_by_month = Counter()

    for a in assets.values():
        burned = as_int(a.get("burned"))
        is_staked = as_int(a.get("current_world_staked")) == 1

        if burned:
            burned_mints.append(a["mint"])
            bt = parse_time(a.get("burn_utc"))
            if bt:
                burn_by_month[f"{bt.year:04d}-{bt.month:02d}-01"] += 1
        else:
            active_supply += 1
            owner = null(a.get("current_beneficial_owner"))
            if owner:
                beneficial_owners.append(owner)

        if is_staked:
            staked += 1
            qt = parse_time(a.get("best_known_last_qualifying_quest_utc"))
            if qt is None:
                bucket = "Never quested"
                quest_limbo_1y += 1
            else:
                days = (as_of - qt).total_seconds() / 86400.0
                if days <= 30:
                    quest_active_30d += 1
                if days <= 7:
                    bucket = "Active 0–7d"
                elif days <= 30:
                    bucket = "Idle 8–30d"
                elif days <= 90:
                    bucket = "Idle 31–90d"
                elif days <= 180:
                    bucket = "Idle 91–180d"
                elif days <= 365:
                    bucket = "Idle 181–365d"
                else:
                    bucket = "Idle 1+ year"
                    quest_limbo_1y += 1
            quest_counts[bucket] += 1

    owner_balances = Counter(beneficial_owners)
    holder_defs = [
        ("1-4", 1, 4), ("5-14", 5, 14), ("15-29", 15, 29),
        ("30-49", 30, 49), ("50-99", 50, 99), ("100+", 100, 10**9),
    ]
    holder_distribution = []
    for order, (label, lo, hi) in enumerate(holder_defs, start=1):
        balances = [n for n in owner_balances.values() if lo <= n <= hi]
        supply = sum(balances)
        holder_distribution.append({
            "order": order,
            "tier": label,
            "holder_count": len(balances),
            "hero_supply": supply,
            "supply_pct": round(100.0 * supply / active_supply, 1),
        })

    quest_order = [
        "Active 0–7d", "Idle 8–30d", "Idle 31–90d", "Idle 91–180d",
        "Idle 181–365d", "Idle 1+ year", "Never quested",
    ]
    quest_activity = [
        {"order": i, "bucket": name, "heroes": quest_counts[name]}
        for i, name in enumerate(quest_order, start=1)
    ]

    rarity_order = ["Bronze", "Silver", "Gold", "Arcane", "Elven"]
    rarity_counts = Counter(rarity[m] for m in burned_mints)
    burn_rarity = [
        {"order": i, "rarity": name, "burned": rarity_counts[name]}
        for i, name in enumerate(rarity_order, start=1)
    ]

    burn_history = []
    if burn_by_month:
        month = min(burn_by_month)
        last = f"{as_of.year:04d}-{as_of.month:02d}-01"
        cumulative = 0
        while month <= last:
            monthly = burn_by_month[month]
            cumulative += monthly
            burn_history.append({"month": month, "monthly_burns": monthly, "total_burns": cumulative})
            month = next_month(month)

    kpis = {
        "active_supply": active_supply,
        "beneficial_holders": len(owner_balances),
        "staked_heroes": staked,
        "staked_supply_pct": round(100.0 * staked / active_supply, 1),
        "quest_active_30d": quest_active_30d,
        "quest_limbo_1y": quest_limbo_1y,
        "burned": len(burned_mints),
    }

    return {
        "as_of": iso_z(as_of),
        "kpis": kpis,
        "holder_distribution": holder_distribution,
        "quest_activity": quest_activity,
        "burn_history": burn_history,
        "burned_by_rarity": burn_rarity,
    }


def build_launch():
    assets = read_csv(DATA / "baseline" / "assets.csv")
    public = [r for r in assets if r["genesis_type"] == "public_candy_machine"]
    minters = Counter(r["genesis_payer"] for r in public if null(r["genesis_payer"]))

    phases = []
    for order, key, label in [(1, "PHASE_I", "Phase I"), (2, "PHASE_II", "Phase II"), (3, "PHASE_III", "Phase III")]:
        rows = [r for r in public if r["mint_phase"] == key]
        times = sorted(parse_time(r["genesis_utc"]) for r in rows)
        phases.append({
            "order": order,
            "phase": label,
            "heroes_minted": len(rows),
            "unique_minters": len({r["genesis_payer"] for r in rows}),
            "first_mint": iso_z(times[0]),
            "last_mint": iso_z(times[-1]),
        })

    distribution = []
    for heroes in sorted(Counter(minters.values())):
        count = sum(1 for n in minters.values() if n == heroes)
        distribution.append({"heroes_minted": heroes, "minters": count, "total_heroes": heroes * count})

    return {
        "kpis": {
            "public_mint_supply": len(public),
            "unique_public_minters": len(minters),
            "mint_price_sol": 1.5,
            "public_mint_proceeds_sol": len(public) * 1.5,
        },
        "mint_phases": phases,
        "public_mint_distribution": distribution,
    }


def build_market(checkpoints):
    baseline = read_csv(DATA / "baseline" / "market_sales.csv")
    live = read_csv(DATA / "state" / "market_live_sales.csv")

    # Historical baseline wins if an overlapping row is ever present in the live ledger.
    seen = {(r["signature"], r["mint"]) for r in baseline}
    final_sales = list(baseline)
    final_sales.extend(r for r in live if (r["signature"], r["mint"]) not in seen)

    secondary_sales = len(final_sales)
    volume = sum(as_float(r["gross_price_sol"]) for r in final_sales)
    buyers = len({r["buyer"] for r in final_sales if null(r["buyer"])})
    heroes_sold = len({r["mint"] for r in final_sales})
    royalties = round(sum(as_float(r["royalty_90_sol"]) for r in final_sales), 2)

    monthly = defaultdict(lambda: {"sales": 0, "volume_sol": 0.0, "royalties_sol": 0.0})
    for r in final_sales:
        month = month_key_from_iso(r["utc"])
        monthly[month]["sales"] += 1
        monthly[month]["volume_sol"] += as_float(r["gross_price_sol"])
        monthly[month]["royalties_sol"] += as_float(r["royalty_90_sol"])
    monthly_activity = [
        {
            "month": month,
            "sales": v["sales"],
            "volume_sol": round(v["volume_sol"], 2),
            "royalties_sol": round(v["royalties_sol"], 2),
        }
        for month, v in sorted(monthly.items())
    ]

    # Preserve the canonical historical distribution; only baseline 'Never sold'
    # Heroes can move to >1y after cutover.
    resale_rows = read_csv(DATA / "baseline" / "first_resale_timing.csv")
    never_mints = {r["mint"] for r in resale_rows if r["first_resale_bucket"] == "Never sold"}
    moved_to_gt1y = len({r["mint"] for r in final_sales if r["mint"] in never_mints})
    resale = [
        {"order": 1, "bucket": "≤24h", "heroes": 3866},
        {"order": 2, "bucket": "2–7d", "heroes": 1236},
        {"order": 3, "bucket": "8–30d", "heroes": 832},
        {"order": 4, "bucket": "31–90d", "heroes": 1113},
        {"order": 5, "bucket": "91–365d", "heroes": 948},
        {"order": 6, "bucket": ">1y", "heroes": 410 + moved_to_gt1y},
        {"order": 7, "bucket": "Never sold", "heroes": 1495 - moved_to_gt1y},
    ]

    return {
        "as_of": checkpoints["market_checkpoint_date"],
        "kpis": {
            "secondary_sales": secondary_sales,
            "secondary_volume_sol": volume,
            "unique_buyers": buyers,
            "heroes_ever_sold": heroes_sold,
            "guild_saga_royalties_sol": royalties,
            "mint_plus_royalties_sol": round(14850.0 + royalties, 2),
        },
        "monthly_activity": monthly_activity,
        "first_resale_timing": resale,
    }



def build_wallet_explorer(checkpoints):
    """Build one client-side wallet index from the same canonical state as the dashboard.

    The browser downloads this single file only when Wallet Explorer is opened.
    Because every address is in the same published artifact, entering a wallet does
    not cause an address-specific request to the site or to a blockchain provider.
    """
    assets = {r["mint"]: r for r in read_csv(DATA / "baseline" / "assets.csv")}
    for d in read_csv(DATA / "state" / "hero_deltas.csv"):
        a = assets[d["mint"]]
        for field in HERO_STATE_FIELDS:
            a[field] = null(d[field])

    rarity_by_mint = {
        r["mint"]: r["rarity_level"]
        for r in read_csv(DATA / "baseline" / "rarity.csv")
    }
    hero_as_of = parse_time(checkpoints["hero_state_checkpoint"])
    if hero_as_of is None:
        raise RuntimeError("Missing hero-state checkpoint for Wallet Explorer")

    rarity_names = ["Bronze", "Silver", "Gold", "Elven", "Arcane"]
    rarity_code = {name: i for i, name in enumerate(rarity_names)}
    quest_names = [
        "Not staked",
        "Active 0–7d",
        "Idle 8–30d",
        "Idle 31–90d",
        "Idle 91–180d",
        "Idle 181–365d",
        "Idle 1+ year",
        "Never quested",
    ]
    quest_code = {name: i for i, name in enumerate(quest_names)}
    phase_names = ["", "Phase I", "Phase II", "Phase III"]
    phase_code = {"PHASE_I": 1, "PHASE_II": 2, "PHASE_III": 3}

    # Wallet rows are compact positional arrays. The public file carries field
    # legends so the schema remains self-describing while avoiding repeated keys.
    # [current_heroes, public_mints, supported_market_buys, supported_market_sells]
    wallets = defaultdict(lambda: [[], [], [], []])
    heroes = [None] * 10_000
    known_numbers = set()
    holder_balances = Counter()
    active_rarity = Counter()
    staked_heroes = 0

    def quest_bucket(a):
        if as_int(a.get("current_world_staked")) != 1:
            return "Not staked"
        qt = parse_time(a.get("best_known_last_qualifying_quest_utc"))
        if qt is None:
            return "Never quested"
        days = (hero_as_of - qt).total_seconds() / 86400.0
        if days <= 7:
            return "Active 0–7d"
        if days <= 30:
            return "Idle 8–30d"
        if days <= 90:
            return "Idle 31–90d"
        if days <= 180:
            return "Idle 91–180d"
        if days <= 365:
            return "Idle 181–365d"
        return "Idle 1+ year"

    for a in assets.values():
        raw_number = null(a.get("hero_number"))
        hero_number = int(float(raw_number)) if raw_number is not None else None
        rarity = rarity_by_mint[a["mint"]]
        burned = as_int(a.get("burned")) == 1
        is_staked = as_int(a.get("current_world_staked")) == 1

        if hero_number is not None:
            if not 0 <= hero_number <= 9999 or hero_number in known_numbers:
                raise RuntimeError(f"Invalid/duplicate Hero number in Wallet Explorer: {hero_number}")
            known_numbers.add(hero_number)
            bucket = quest_bucket(a)
            heroes[hero_number] = [
                rarity_code[rarity],
                1 if is_staked else 0,
                quest_code[bucket],
                iso_z(parse_time(a.get("current_stake_deposit_utc"))) if is_staked and parse_time(a.get("current_stake_deposit_utc")) else "",
                iso_z(parse_time(a.get("best_known_last_qualifying_quest_utc"))) if is_staked and parse_time(a.get("best_known_last_qualifying_quest_utc")) else "",
            ]

        if not burned:
            owner = null(a.get("current_beneficial_owner"))
            if owner:
                if hero_number is None:
                    raise RuntimeError("Active Hero is missing hero_number in Wallet Explorer")
                wallets[owner][0].append(hero_number)
                holder_balances[owner] += 1
                active_rarity[rarity] += 1
            if is_staked:
                staked_heroes += 1

    # Preserve every public mint, including the three historical burned assets
    # whose baseline rows do not have a recoverable Hero number.
    mints = []
    for a in sorted(
        (r for r in assets.values() if r["genesis_type"] == "public_candy_machine"),
        key=lambda r: (parse_time(r["genesis_utc"]), r["mint"]),
    ):
        payer = null(a.get("genesis_payer"))
        if not payer:
            continue
        raw_number = null(a.get("hero_number"))
        hero_number = int(float(raw_number)) if raw_number is not None else None
        phase = phase_code.get(a.get("mint_phase") or "", 0)
        if phase == 0:
            raise RuntimeError(f"Unexpected public mint phase for {a['mint']}")
        mint_index = len(mints)
        mints.append([hero_number, iso_z(parse_time(a["genesis_utc"])), phase])
        wallets[payer][1].append(mint_index)

    baseline_sales = read_csv(DATA / "baseline" / "market_sales.csv")
    live_sales = read_csv(DATA / "state" / "market_live_sales.csv")
    seen = {(r["signature"], r["mint"]) for r in baseline_sales}
    final_sales = list(baseline_sales)
    final_sales.extend(r for r in live_sales if (r["signature"], r["mint"]) not in seen)
    final_sales.sort(key=lambda r: (parse_time(r["utc"]), r["signature"], r["mint"]))

    marketplace_names = []
    marketplace_code = {}
    sales = []
    for r in final_sales:
        raw_number = null(r.get("hero_number"))
        if raw_number is None:
            continue
        hero_number = int(float(raw_number))
        marketplace = null(r.get("marketplace")) or "Unknown"
        if marketplace not in marketplace_code:
            marketplace_code[marketplace] = len(marketplace_names)
            marketplace_names.append(marketplace)
        sale_index = len(sales)
        sales.append([
            hero_number,
            iso_z(parse_time(r["utc"])),
            round(as_float(r.get("gross_price_sol")), 9),
            marketplace_code[marketplace],
            r["signature"],
        ])
        buyer = null(r.get("buyer"))
        seller = null(r.get("seller"))
        if buyer:
            wallets[buyer][2].append(sale_index)
        if seller:
            wallets[seller][3].append(sale_index)

    active_supply = sum(holder_balances.values())
    beneficial_holders = len(holder_balances)
    public_minters = sum(1 for row in wallets.values() if row[1])
    unique_buyers = sum(1 for row in wallets.values() if row[2])

    return {
        "schema_version": 1,
        "as_of": {
            "hero": iso_z(hero_as_of),
            "market": checkpoints["market_checkpoint_date"],
        },
        "fields": {
            "hero": ["rarity", "staked", "quest", "stake_since", "last_quest"],
            "mint": ["hero", "utc", "phase"],
            "sale": ["hero", "utc", "sol", "marketplace", "signature"],
            "wallet": ["current_heroes", "public_mints", "buys", "sells"],
        },
        "lookups": {
            "rarities": rarity_names,
            "quest_buckets": quest_names,
            "mint_phases": phase_names,
            "marketplaces": marketplace_names,
        },
        "collection": {
            "active_supply": active_supply,
            "beneficial_holders": beneficial_holders,
            "staked_heroes": staked_heroes,
            "staked_supply_pct": round(100.0 * staked_heroes / active_supply, 1) if active_supply else 0.0,
            "rarity_counts": [active_rarity[name] for name in rarity_names],
            "public_mint_supply": len(mints),
            "unique_public_minters": public_minters,
            "unique_market_buyers": unique_buyers,
        },
        "benchmarks": {
            "holder_balances": sorted(holder_balances.values(), reverse=True),
            "market_purchase_counts": sorted((len(row[2]) for row in wallets.values() if row[2]), reverse=True),
            "public_mint_counts": sorted((len(row[1]) for row in wallets.values() if row[1]), reverse=True),
        },
        "heroes": heroes,
        "mints": mints,
        "sales": sales,
        "wallets": {
            address: [
                sorted(row[0]),
                sorted(row[1]),
                sorted(row[2]),
                sorted(row[3]),
            ]
            for address, row in sorted(wallets.items())
        },
    }

def build_top_holders(wallet_explorer, limit=10):
    """Build the small always-visible holder shortcut product for Ownership."""
    ranked = sorted(
        (
            (address, len(row[0]))
            for address, row in wallet_explorer["wallets"].items()
            if row and row[0]
        ),
        key=lambda item: (-item[1], item[0]),
    )[:limit]

    holders = []
    previous_count = None
    previous_rank = 0
    for index, (address, heroes) in enumerate(ranked, start=1):
        rank = previous_rank if heroes == previous_count else index
        holders.append({"rank": rank, "address": address, "heroes": heroes})
        previous_count = heroes
        previous_rank = rank

    return {
        "schema_version": 1,
        "as_of": wallet_explorer["as_of"]["hero"],
        "holders": holders,
    }


def build_floor(checkpoints):
    rows = read_csv(DATA / "history" / "floor_listings.csv")
    rows.sort(key=lambda r: r["snapshot_date"])
    latest = rows[-1]
    history = [{
        "snapshot_date": r["snapshot_date"],
        "floor_sol": as_float(r["floor_sol"]),
        "listed_count": as_int(r["listed_count"]),
        "source": r["source"],
    } for r in rows]
    return {
        "as_of": checkpoints["floor_checkpoint_date"],
        "kpis": {"floor_sol": as_float(latest["floor_sol"]), "listed_count": as_int(latest["listed_count"])},
        "history": history,
    }


def build_treasury():
    swaps = [
        ("2022-03-02T03:56:01Z", 770.000010, 79328.404560),
        ("2022-03-19T17:51:25Z", 550.000010, 50675.638785),
        ("2022-03-24T23:34:35Z", 500.000010, 50378.622377),
        ("2022-03-28T20:44:17Z", 450.000010, 49570.498336),
        ("2022-03-31T04:54:47Z", 420.000010, 50287.715199),
        ("2022-04-04T04:57:59Z", 375.000010, 50318.357107),
        ("2022-05-15T23:25:26Z", 850.000010, 48775.104645),
        ("2022-06-16T03:55:51Z", 1200.000010, 40004.679168),
        ("2023-09-04T20:51:59Z", 150.000016, 2911.380059),
        ("2023-09-18T04:31:08Z", 400.000018, 7619.904121),
        ("2023-10-02T16:31:35Z", 400.000016, 9633.380238),
        ("2023-10-18T01:07:27Z", 400.000016, 9586.328282),
        ("2023-11-11T08:01:38Z", 450.000015, 24466.133035),
        ("2025-03-02T18:04:58Z", 85.000704, 14378.595419),
        ("2025-03-07T00:28:52Z", 83.901915, 11548.837117),
    ]
    month_agg = defaultdict(lambda: [0.0, 0.0])
    for ts, sol, usdc in swaps:
        month_agg[ts[:7] + "-01"][0] += sol
        month_agg[ts[:7] + "-01"][1] += usdc
    month = "2022-03-01"
    last = "2025-03-01"
    cumulative_sol = cumulative_usdc = 0.0
    conversion_history = []
    while month <= last:
        sol, usdc = month_agg[month]
        cumulative_sol += sol
        cumulative_usdc += usdc
        conversion_history.append({
            "month": month,
            "sol_sold": round(sol, 3),
            "total_sol_sold": round(cumulative_sol, 3),
            "usdc_purchased": round(usdc, 2),
            "total_usdc_purchased": round(cumulative_usdc, 2),
        })
        month = next_month(month)

    return {
        "initial_mint_treasury_split": [
            {"order": 1, "destination": "Primary branch", "sol": 14107.720, "pct": 95.0},
            {"order": 2, "destination": "Secondary branch", "sol": 742.512, "pct": 5.0},
        ],
        "asset_handoff": {"usdc_transferred": 57000, "sol_transferred": 150, "guild_heroes_transferred": 7, "other_nft_assets_transferred": 4},
        "core_wallet_usdc_distribution": [
            {"order": 1, "destination": "3xQ project-connected wallet", "usdc": 120361.0},
            {"order": 2, "destination": "FS9b project-connected wallet", "usdc": 120361.0},
        ],
        "conversion_history": conversion_history,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only-floor",
        action="store_true",
        help="Rebuild floor-listings.json and only the floor section of summary.json.",
    )
    args = parser.parse_args()

    checkpoints = json.loads((DATA / "state" / "checkpoints.json").read_text(encoding="utf-8"))
    floor = build_floor(checkpoints)

    if args.only_floor:
        summary_path = OUT / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["floor"] = floor["kpis"]
        write_json("summary.json", summary)
        write_json("floor-listings.json", floor)
        print(json.dumps({"floor": floor["kpis"]}, indent=2, ensure_ascii=False))
        return

    hero = build_hero_state(checkpoints)
    launch = build_launch()
    market = build_market(checkpoints)
    wallet_explorer = build_wallet_explorer(checkpoints)
    top_holders = build_top_holders(wallet_explorer)
    treasury = build_treasury()

    summary = {
        "cutover_date": checkpoints["cutover_date"],
        "hero": hero["kpis"],
        "launch": launch["kpis"],
        "market": market["kpis"],
        "floor": floor["kpis"],
    }

    write_json("summary.json", summary)
    write_json("hero-state.json", hero)
    write_json("launch.json", launch)
    write_json("market-history.json", market)
    write_compact_json("wallet-explorer.json", wallet_explorer)
    write_compact_json("top-holders.json", top_holders)
    write_json("floor-listings.json", floor)
    write_json("treasury.json", treasury)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
