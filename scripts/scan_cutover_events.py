#!/usr/bin/env python3
"""Free-tier, read-only Guild Saga cutover backfill.

Authoritative evidence flow:
    frozen Aug. 26 canonical state
      -> standard Solana getSignaturesForAddress over every active Hero mint
         (1 credit/request on Helius Free; no paid historical RPC required)
      -> World Mode program signatures for stake / unstake / quest events
      -> creator royalty-wallet signatures as an independent market-sale net
      -> raw getTransaction verification / independent parsers
      -> in-memory Hero-state reduction
      -> direct SPL owner-frontier inventory + targeted mint/supply snapshots
      -> token-account signature recovery for any owner gaps
      -> exact raw-chain reconciliation
      -> DAS current-state audit only

No canonical state, checkpoints, or website JSON are modified. Resume/cache data is
written only under ignored .guild_saga_recon/.

Why this replaces Phase 1G:
getTransfersByAddress and getTransactionsForAddress are paid-plan methods. This
scanner intentionally uses standard methods available on Helius Free throughout
the authoritative backfill. DAS is used only as a final non-authoritative audit.
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import sys
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collector.constants import (
    ORIGINAL_SUPPLY,
    WORLD_MODE_PROGRAM,
    ROYALTY_90_ADDRESS,
    ROYALTY_10_ADDRESS,
    TOKEN_PROGRAM,
)
from collector.hero_state import (
    HeroState,
    RawMovement,
    WorldCall,
    apply_movement,
    apply_quest_restart_to_collection,
    classify_world_call,
)
from collector.market import decode_sales
from collector.solana_normalize import (
    normalize_token_movements,
    normalize_transaction,
    transaction_has_burn_instruction,
    transaction_signers,
)
from collector.solana_rpc import clients_from_repo, get_transaction_with_fallback

ASSETS = ROOT / "data" / "baseline" / "assets.csv"
DELTAS = ROOT / "data" / "state" / "hero_deltas.csv"
CHECKPOINTS = ROOT / "data" / "state" / "checkpoints.json"
RECON_DIR = ROOT / ".guild_saga_recon"
DB_PATH = RECON_DIR / "cutover_free_backfill.sqlite"

DISCOVERY_LOOKBACK_SECONDS = 300
SIGNATURE_HEAD_LIMIT = 1
MINT_HISTORY_PAGE_LIMIT = 100
SIGNATURE_PAGE_LIMIT = 1000
MAX_SIGNATURE_PAGES_PER_MINT = 25
# Throughput targets deliberately stay below the current free-tier ceilings.
# Helius Free: 10 RPC requests/sec. We target 9.
# Alchemy Free: 300 CU/sec. getSignaturesForAddress/getTransaction cost 40 CU,
# so we target 6.5/sec (=260 CU/sec). Lighter 20-CU snapshot calls target 12/sec.
HELIUS_RPC_RPS = 8.0
ALCHEMY_HEAVY_RPS = 6.0
ALCHEMY_LIGHT_RPS = 10.0
ALCHEMY_OWNER_RPS = 12.0
HELIUS_OWNER_RPS = 5.0
PARALLEL_WORKERS = 48
TOKEN_ACCOUNT_BATCH = 100
MAX_CHAIN_GAP_MINTS = 2000
DAS_BATCH_LIMIT = 1000
ME_V2_CUSTODY = "1BWutmTvYPwDtmw9abTkS4Ssr8no61spGAvW1X6NDix"

STATE_FIELDS = (
    "burned", "burn_utc", "burn_signature", "current_raw_owner",
    "current_world_staked", "current_beneficial_owner",
    "current_world_staking_wallet", "latest_event_utc", "latest_signature",
    "quest_user_wallet", "quest_staking_wallet", "current_stake_deposit_utc",
    "current_stake_deposit_signature", "best_known_last_qualifying_quest_utc",
    "best_known_last_qualifying_quest_signature", "quest_history_source",
    "deep_history_status",
)

# These are already independently validated raw-chain transactions from our
# migration backtests. If standard getSignaturesForAddress(mint) cannot see
# these transaction families, the cheap mint-history strategy is not safe and
# the full 9,832-mint crawl is aborted before it starts.
DISCOVERY_PROBES = [
    {
        "label": "Magic Eden V2",
        "mint": "AankJNdbJzk7Ka1uQiHQHMfy6GUWPn8eAUviHkqJySMA",
        "signature": "4E1sF4iC3UyFUAsZ9d2HnhrVnP4XddwrLKZJnkRxgDPbgUvavm9GoMh4zsUWUKeK5bBqX7R7cCwgmVNbxDfuAXv",
    },
    {
        "label": "Tensor Marketplace",
        "mint": "7xTwKUkPVe5eB5nPs7dxipq5iK5Ea8Gjuh2GAKvMXKzh",
        "signature": "2uvMNeNWqRwSGPaVZAb5mQLvw5YHiVfmhLE5JVpeeER1VLpHtZQq6C9dUxbbMkct5sqw3VCQmkPBVeY6JF2zyrdC",
    },
    {
        "label": "Tensor AMM",
        "mint": "2dtvUt8qYcV8DWpKXXzXJLs2zjnaNR3wA3jFmhcZMXic",
        "signature": "2k6K48tLFaWQeoH5by4s2tMBv1P76VABSzx77PYJPaC7dE1TtU6DtxWTdURq7qvHrDK7g4L3x98qrnL2jDLPqL81",
    },
    {
        "label": "explicit burn",
        "mint": "uZ1xUzKhrAy1UTjUGEEwU6JuPB52qDGuFJ1xC2yqfRv",
        "signature": "4vCVtgkBVLBk7mDP9ph8dujTsrC1u9peWZbWF6sc8o4s2GD7PShbFWsbVLoo7ivijgNvkc1SVT7PcHiEGMrk61wx",
    },
]


class RateGate:
    """Thread-safe fixed-spacing request gate."""
    def __init__(self, rps: float):
        if rps <= 0:
            raise ValueError("rps must be positive")
        self.interval = 1.0 / rps
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next_at)
            self.next_at = start + self.interval
        delay = start - now
        if delay > 0:
            time.sleep(delay)


def prepare_rpc_clients(clients):
    """Use Helius Gatekeeper and return clients by label."""
    by_label = {}
    for client in clients:
        label = client.label.casefold()
        if label == "helius":
            client.url = client.url.replace(
                "https://mainnet.helius-rpc.com/",
                "https://beta.helius-rpc.com/",
            )
        by_label[label] = client
    return by_label


def phase_lanes(clients, alchemy_rps: float, helius_rps: float = HELIUS_RPC_RPS):
    by = prepare_rpc_clients(clients)
    lanes = []
    helius = by.get("helius")
    alchemy = by.get("alchemy")
    if helius:
        lanes.append({"client": helius, "gate": RateGate(helius_rps), "weight": helius_rps})
    if alchemy:
        lanes.append({"client": alchemy, "gate": RateGate(alchemy_rps), "weight": alchemy_rps})
    if not lanes:
        raise RuntimeError("No usable RPC clients.")
    return lanes


def weighted_lane_cycle(lanes):
    """Deterministic weighted provider cycle."""
    slots = []
    for idx, lane in enumerate(lanes):
        count = max(1, round(float(lane["weight"]) * 4))
        slots.extend([idx] * count)
    counts = Counter(slots)
    out = []
    while sum(counts.values()):
        for idx in sorted(counts, key=lambda i: (-counts[i], i)):
            if counts[idx] > 0:
                out.append(idx)
                counts[idx] -= 1
    return out


def call_with_lane(primary_lane, fallback_lanes, method: str, params):
    errors = []
    ordered = [primary_lane] + [x for x in fallback_lanes if x is not primary_lane]
    for lane in ordered:
        client = lane["client"]
        try:
            lane["gate"].wait()
            return client.call(method, params), client.label
        except Exception as exc:
            errors.append(f"{client.label}: {exc}")
    raise RuntimeError(f"{method} failed on all providers: {' | '.join(errors)}")


def call_with_lane_resilient(primary_lane, fallback_lanes, method: str, params, rounds: int = 3):
    """Retry a whole provider cycle for transient provider/indexer failures."""
    last_errors = []
    for round_idx in range(rounds):
        try:
            return call_with_lane(primary_lane, fallback_lanes, method, params)
        except Exception as exc:
            last_errors.append(str(exc))
            if round_idx + 1 < rounds:
                time.sleep(2.0 * (round_idx + 1))
    raise RuntimeError(
        f"{method} failed after {rounds} provider cycles: " + " || ".join(last_errors[-2:])
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def parse_utc(value: str) -> datetime:
    value = value.strip().replace(" UTC", "+00:00").replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def truthy_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def empty_to_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def cutover_state() -> tuple[dict[str, dict[str, str]], datetime]:
    assets = read_csv(ASSETS)
    if len(assets) != ORIGINAL_SUPPLY:
        raise RuntimeError(f"Expected {ORIGINAL_SUPPLY:,} baseline rows; found {len(assets):,}.")
    state = {row["mint"]: dict(row) for row in assets}
    if len(state) != ORIGINAL_SUPPLY:
        raise RuntimeError("Baseline mint addresses are not unique.")

    for delta in read_csv(DELTAS):
        mint = delta.get("mint") or ""
        if mint not in state:
            raise RuntimeError(f"hero_deltas.csv contains unknown mint {mint!r}")
        for field in STATE_FIELDS:
            if field in delta:
                state[mint][field] = delta.get(field, "")

    checkpoints = json.loads(CHECKPOINTS.read_text(encoding="utf-8"))
    checkpoint_text = str(checkpoints.get("hero_state_checkpoint") or "").strip()
    if not checkpoint_text:
        raise RuntimeError("checkpoints.json has no hero_state_checkpoint")
    checkpoint = parse_utc(checkpoint_text)

    burns = sum(truthy_int(row.get("burned")) for row in state.values())
    if burns != 168:
        raise RuntimeError(
            f"Frozen cutover merge produced {burns} burns; expected 168. "
            "Stop rather than scan from an uncertain seed."
        )
    return state, checkpoint


def hero_objects(rows: dict[str, dict[str, str]]) -> dict[str, HeroState]:
    out: dict[str, HeroState] = {}
    for mint, row in rows.items():
        out[mint] = HeroState(
            mint=mint,
            burned=truthy_int(row.get("burned")),
            burn_utc=empty_to_none(row.get("burn_utc")),
            burn_signature=empty_to_none(row.get("burn_signature")),
            current_raw_owner=empty_to_none(row.get("current_raw_owner")),
            current_world_staked=truthy_int(row.get("current_world_staked")),
            current_beneficial_owner=empty_to_none(row.get("current_beneficial_owner")),
            current_world_staking_wallet=empty_to_none(row.get("current_world_staking_wallet")),
            latest_event_utc=empty_to_none(row.get("latest_event_utc")),
            latest_signature=empty_to_none(row.get("latest_signature")),
            quest_user_wallet=empty_to_none(row.get("quest_user_wallet")),
            quest_staking_wallet=empty_to_none(row.get("quest_staking_wallet")),
            current_stake_deposit_utc=empty_to_none(row.get("current_stake_deposit_utc")),
            current_stake_deposit_signature=empty_to_none(row.get("current_stake_deposit_signature")),
            best_known_last_qualifying_quest_utc=empty_to_none(row.get("best_known_last_qualifying_quest_utc")),
            best_known_last_qualifying_quest_signature=empty_to_none(row.get("best_known_last_qualifying_quest_signature")),
            quest_history_source=empty_to_none(row.get("quest_history_source")),
            deep_history_status=empty_to_none(row.get("deep_history_status")),
        )
    return out


def helius_client(clients):
    for client in clients:
        if client.label.casefold() == "helius":
            return client
    raise RuntimeError("This backfill needs Helius for free-tier signature discovery.")


def open_db(checkpoint: datetime) -> sqlite3.Connection:
    RECON_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript("""
    PRAGMA journal_mode=WAL;
    PRAGMA synchronous=NORMAL;

    CREATE TABLE IF NOT EXISTS meta(
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS mint_scan(
      mint TEXT PRIMARY KEY,
      hero_name TEXT,
      complete INTEGER NOT NULL DEFAULT 0,
      pages INTEGER NOT NULL DEFAULT 0,
      recent_signatures INTEGER NOT NULL DEFAULT 0,
      error TEXT
    );

    CREATE TABLE IF NOT EXISTS discovered_signatures(
      signature TEXT NOT NULL,
      mint TEXT NOT NULL,
      source TEXT NOT NULL,
      block_time INTEGER,
      PRIMARY KEY(signature,mint,source)
    );

    CREATE TABLE IF NOT EXISTS raw_transactions(
      signature TEXT PRIMARY KEY,
      provider TEXT NOT NULL,
      block_time INTEGER,
      tx_json TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS chain_snapshot(
      mint TEXT PRIMARY KEY,
      complete INTEGER NOT NULL DEFAULT 0,
      token_account TEXT,
      token_amount TEXT,
      supply_zero INTEGER NOT NULL DEFAULT 0,
      owner TEXT,
      error TEXT
    );

    CREATE TABLE IF NOT EXISTS owner_inventory(
      owner TEXT PRIMARY KEY,
      complete INTEGER NOT NULL DEFAULT 0,
      error TEXT
    );

    CREATE TABLE IF NOT EXISTS owner_holdings(
      mint TEXT PRIMARY KEY,
      owner TEXT NOT NULL,
      token_account TEXT NOT NULL,
      token_amount TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS owner_gap_audit(
      mint TEXT PRIMARY KEY,
      token_account TEXT NOT NULL,
      chain_owner TEXT NOT NULL,
      reconstructed_owner TEXT NOT NULL,
      signatures_after_cutover INTEGER NOT NULL DEFAULT 0,
      complete INTEGER NOT NULL DEFAULT 0,
      error TEXT
    );
    """)

    version = "phase1h-free-tier-v2"
    existing_version = con.execute("SELECT value FROM meta WHERE key='scanner_version'").fetchone()
    existing_cp = con.execute("SELECT value FROM meta WHERE key='cutover_checkpoint'").fetchone()
    if existing_version and str(existing_version[0]) != version:
        raise RuntimeError(
            f"{DB_PATH.name} belongs to scanner version {existing_version[0]!r}, expected {version!r}. "
            "Delete only this ignored recon DB if you intentionally want a clean rerun."
        )
    if existing_cp and str(existing_cp[0]) != iso_z(checkpoint):
        raise RuntimeError(
            f"{DB_PATH.name} was seeded for checkpoint {existing_cp[0]}, not {iso_z(checkpoint)}."
        )
    con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('scanner_version',?)", (version,))
    con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('cutover_checkpoint',?)", (iso_z(checkpoint),))
    con.commit()
    return con


def seed_mints(con: sqlite3.Connection, rows: dict[str, dict[str, str]]) -> int:
    n = 0
    for mint, row in rows.items():
        if truthy_int(row.get("burned")):
            continue
        con.execute(
            "INSERT OR IGNORE INTO mint_scan(mint,hero_name) VALUES(?,?)",
            (mint, row.get("hero_name") or ""),
        )
        n += 1
    con.commit()
    return n


def signature_rows(client, address: str, before: str | None = None) -> list[dict[str, Any]]:
    opts: dict[str, Any] = {"limit": SIGNATURE_PAGE_LIMIT, "commitment": "finalized"}
    if before:
        opts["before"] = before
    rows = client.call("getSignaturesForAddress", [address, opts]) or []
    if not isinstance(rows, list):
        raise RuntimeError(f"getSignaturesForAddress({address}) returned {type(rows).__name__}")
    return [r for r in rows if isinstance(r, dict)]


def preflight_standard_history(clients, cutover_rows: dict[str, dict[str, str]]):
    print("[1/8] Free-tier standard-RPC discovery preflight")
    eligible = []
    for client in clients:
        failures = []
        print(f"    provider: {client.label}")
        for probe in DISCOVERY_PROBES:
            mint = str(probe["mint"])
            try:
                rows = signature_rows(client, mint)
                sigs = {str(r.get("signature") or "") for r in rows}
                ok = str(probe["signature"]) in sigs
            except Exception:
                ok = False
            print(f"      [{'PASS' if ok else 'FAIL'}] {probe['label']}")
            if not ok:
                failures.append(str(probe["label"]))
        if failures:
            print(f"      -> disabled for bulk history: {', '.join(failures)}")
        else:
            eligible.append(client)
            print("      -> eligible for concurrent bulk history")
    if not eligible:
        raise RuntimeError(
            "No configured RPC provider passed all mint-history migration probes. "
            "The 9,832-mint crawl was NOT started."
        )
    print("    bulk history providers: " + " + ".join(c.label for c in eligible))
    return eligible

def _scan_one_mint(client_lane, all_lanes, mint: str, start_ts: int):
    """Scan one mint efficiently.

    Most Heroes are idle. First request only the newest signature. If that
    transaction is already at/before the cutover boundary, the mint is done
    after a tiny one-row response. Only genuinely active Heroes pay for deeper
    pagination.
    """
    pages = 0
    recent_rows = []
    provider_counts = Counter()

    # Fast head probe: one row is enough to prove an idle mint has no
    # post-cutover activity. This avoids downloading up to 1,000 historical
    # signatures for every inactive Hero.
    opts: dict[str, Any] = {
        "limit": SIGNATURE_HEAD_LIMIT,
        "commitment": "finalized",
    }
    rows, provider = call_with_lane(
        client_lane, all_lanes, "getSignaturesForAddress", [mint, opts]
    )
    pages += 1
    provider_counts[provider] += 1
    if not isinstance(rows, list):
        raise RuntimeError(
            f"getSignaturesForAddress({mint}) returned {type(rows).__name__}"
        )
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return mint, pages, recent_rows, provider_counts

    head = rows[0]
    head_sig = str(head.get("signature") or "")
    head_bt = head.get("blockTime")
    if head_bt is not None and int(head_bt) <= start_ts:
        return mint, pages, recent_rows, provider_counts

    if head_sig and head.get("err") is None:
        recent_rows.append((head_sig, head_bt))

    before = head_sig
    if not before:
        return mint, pages, recent_rows, provider_counts

    # Only active mints reach here. Walk backward in modest pages until the
    # cutover boundary is crossed.
    while True:
        pages += 1
        if pages > MAX_SIGNATURE_PAGES_PER_MINT:
            raise RuntimeError(
                f"{mint}: exceeded {MAX_SIGNATURE_PAGES_PER_MINT} signature pages"
            )
        opts = {
            "limit": MINT_HISTORY_PAGE_LIMIT,
            "commitment": "finalized",
            "before": before,
        }
        rows, provider = call_with_lane(
            client_lane, all_lanes, "getSignaturesForAddress", [mint, opts]
        )
        provider_counts[provider] += 1
        if not isinstance(rows, list):
            raise RuntimeError(
                f"getSignaturesForAddress({mint}) returned {type(rows).__name__}"
            )
        rows = [r for r in rows if isinstance(r, dict)]
        if not rows:
            break

        reached_boundary = False
        for item in rows:
            sig = str(item.get("signature") or "")
            bt = item.get("blockTime")
            if bt is not None and int(bt) <= start_ts:
                reached_boundary = True
                continue
            if not sig or item.get("err") is not None:
                continue
            recent_rows.append((sig, bt))

        if reached_boundary or len(rows) < MINT_HISTORY_PAGE_LIMIT:
            break
        before = str(rows[-1].get("signature") or "")
        if not before:
            break

    return mint, pages, recent_rows, provider_counts


def scan_mint_histories(
    con: sqlite3.Connection,
    clients,
    start_ts: int,
) -> Counter:
    print()
    print("[2/8] Active-Hero mint histories since cutover")
    total = con.execute("SELECT COUNT(*) FROM mint_scan").fetchone()[0]
    done0 = con.execute("SELECT COUNT(*) FROM mint_scan WHERE complete=1").fetchone()[0]
    pending_rows = con.execute(
        "SELECT mint,hero_name FROM mint_scan WHERE complete=0 ORDER BY mint"
    ).fetchall()
    print(f"    active mints queued: {total:,} ({done0:,} already cached)")
    print("    method: adaptive concurrent getSignaturesForAddress")
    print("    idle mint fast-path: newest signature only; deeper pages only when active")
    print(
        f"    targets: Helius {HELIUS_RPC_RPS:g} RPS + "
        f"Alchemy {ALCHEMY_HEAVY_RPS:g} RPS when both are available"
    )
    print("    cache is resumable; completed Phase 1H mints are reused")

    lanes = phase_lanes(clients, ALCHEMY_HEAVY_RPS)
    cycle = weighted_lane_cycle(lanes)
    providers = Counter()
    scanned = 0
    start_clock = time.monotonic()

    if pending_rows:
        with ThreadPoolExecutor(max_workers=min(PARALLEL_WORKERS, len(pending_rows))) as ex:
            futures = {}
            for idx, row in enumerate(pending_rows):
                lane = lanes[cycle[idx % len(cycle)]]
                mint = str(row["mint"])
                fut = ex.submit(_scan_one_mint, lane, lanes, mint, start_ts)
                futures[fut] = (mint, str(row["hero_name"] or ""))

            for fut in as_completed(futures):
                mint, hero_name = futures[fut]
                try:
                    mint2, pages, recent_rows, pcounts = fut.result()
                    assert mint2 == mint
                except Exception as exc:
                    con.execute(
                        "UPDATE mint_scan SET error=? WHERE mint=?",
                        (str(exc)[:1500], mint),
                    )
                    con.commit()
                    raise

                for sig, bt in recent_rows:
                    con.execute(
                        """INSERT OR IGNORE INTO discovered_signatures
                           (signature,mint,source,block_time) VALUES(?,?,?,?)""",
                        (sig, mint, "MINT_HISTORY", bt),
                    )
                con.execute(
                    "UPDATE mint_scan SET complete=1,pages=?,recent_signatures=?,error=NULL WHERE mint=?",
                    (pages, len(recent_rows), mint),
                )
                scanned += 1
                providers.update(pcounts)
                if scanned % 25 == 0 or recent_rows:
                    con.commit()
                done = done0 + scanned
                if scanned <= 5 or done % 250 == 0 or recent_rows:
                    unique = con.execute(
                        "SELECT COUNT(DISTINCT signature) FROM discovered_signatures"
                    ).fetchone()[0]
                    elapsed = max(0.001, time.monotonic() - start_clock)
                    rate = scanned / elapsed
                    remaining = len(pending_rows) - scanned
                    eta_min = (remaining / rate / 60.0) if rate > 0 else 0
                    print(
                        f"    mints {done:>4}/{total:<4} | this run {scanned:>4} | "
                        f"{rate:>4.1f} mints/s | ETA {eta_min:>4.1f}m | "
                        f"unique recent signatures {unique:>4} | "
                        f"H {providers.get('Helius', 0):>4} / A {providers.get('Alchemy', 0):>4}"
                        + (f" | {hero_name} found {len(recent_rows)}" if recent_rows else "")
                    )
    con.commit()

    unique = con.execute(
        "SELECT COUNT(DISTINCT signature) FROM discovered_signatures"
    ).fetchone()[0]
    print(f"    complete: {total:,}/{total:,} active cutover mints")
    print(f"    unique post-overlap signatures from mint histories: {unique:,}")
    print(f"    discovery calls by provider: {dict(providers)}")
    return providers

def signatures_since(client, address: str, start_ts: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    before: str | None = None
    seen: set[str] = set()
    while True:
        rows = signature_rows(client, address, before)
        if not rows:
            break
        reached = False
        for row in rows:
            sig = str(row.get("signature") or "")
            bt = row.get("blockTime")
            if bt is not None and int(bt) <= start_ts:
                reached = True
                continue
            if sig and sig not in seen and row.get("err") is None:
                seen.add(sig)
                out.append(row)
        if reached or len(rows) < SIGNATURE_PAGE_LIMIT:
            break
        before = str(rows[-1].get("signature") or "")
        if not before:
            break
    return out


def scan_global_sources(con: sqlite3.Connection, client, start_ts: int) -> dict[str, int]:
    print()
    print("[3/8] World Mode + royalty-wallet safety nets")
    sources = [
        ("WORLD_MODE", WORLD_MODE_PROGRAM),
        ("ROYALTY_90", ROYALTY_90_ADDRESS),
        ("ROYALTY_10", ROYALTY_10_ADDRESS),
    ]
    result = {}
    for label, address in sources:
        rows = signatures_since(client, address, start_ts)
        for row in rows:
            sig = str(row.get("signature") or "")
            if not sig:
                continue
            con.execute(
                """INSERT OR IGNORE INTO discovered_signatures
                   (signature,mint,source,block_time) VALUES(?,?,?,?)""",
                (sig, address, label, row.get("blockTime")),
            )
        con.commit()
        result[label] = len(rows)
        print(f"    {label:<10}: {len(rows):,} successful signatures")
    return result


def signatures_to_fetch(con: sqlite3.Connection) -> list[str]:
    rows = con.execute(
        "SELECT DISTINCT signature FROM discovered_signatures ORDER BY signature"
    ).fetchall()
    return [str(r[0]) for r in rows]


def _fetch_one_transaction(primary_lane, lanes, sig: str):
    errors = []
    for lane in [primary_lane] + [x for x in lanes if x is not primary_lane]:
        client = lane["client"]
        try:
            lane["gate"].wait()
            tx = client.get_transaction(sig)
            if tx is not None:
                return sig, tx, client.label
            errors.append(f"{client.label}: null transaction")
        except Exception as exc:
            errors.append(f"{client.label}: {exc}")
    raise RuntimeError(f"Could not fetch {sig}: {' | '.join(errors)}")


def fetch_raw_transactions(con: sqlite3.Connection, clients, label: str = "[4/8]") -> Counter:
    print()
    print(f"{label} Raw transaction verification")
    sigs = signatures_to_fetch(con)
    cached = {str(r[0]) for r in con.execute("SELECT signature FROM raw_transactions")}
    remaining = [s for s in sigs if s not in cached]
    print(f"    unique discovered signatures: {len(sigs):,}")
    print(f"    cached raw transactions:      {len(cached & set(sigs)):,}")
    print(f"    need now:                     {len(remaining):,}")
    providers = Counter()
    if not remaining:
        return providers

    lanes = phase_lanes(clients, ALCHEMY_HEAVY_RPS)
    cycle = weighted_lane_cycle(lanes)
    completed = 0
    with ThreadPoolExecutor(max_workers=min(PARALLEL_WORKERS, len(remaining))) as ex:
        futures = {}
        for idx, sig in enumerate(remaining):
            lane = lanes[cycle[idx % len(cycle)]]
            futures[ex.submit(_fetch_one_transaction, lane, lanes, sig)] = sig
        for fut in as_completed(futures):
            sig, tx, provider = fut.result()
            providers[provider] += 1
            con.execute(
                "INSERT OR REPLACE INTO raw_transactions(signature,provider,block_time,tx_json) VALUES(?,?,?,?)",
                (sig, provider, tx.get("blockTime"), json.dumps(tx, separators=(",", ":"))),
            )
            completed += 1
            if completed % 20 == 0 or completed == len(remaining):
                con.commit()
                print(f"    fetched {completed:>4}/{len(remaining):<4}")
    con.commit()
    return providers

def load_raw(con: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {
        str(r["signature"]): json.loads(str(r["tx_json"]))
        for r in con.execute("SELECT signature,tx_json FROM raw_transactions")
    }



def seed_owner_inventory(con: sqlite3.Connection, cutover_rows: dict[str, dict[str, str]]) -> int:
    owners = set()
    for row in cutover_rows.values():
        if truthy_int(row.get("burned")):
            continue
        owner = str(row.get("current_raw_owner") or "").strip()
        if not owner:
            raise RuntimeError(
                f"Active cutover Hero {row.get('hero_name') or row.get('mint')} has no raw owner"
            )
        owners.add(owner)
    for owner in sorted(owners):
        con.execute("INSERT OR IGNORE INTO owner_inventory(owner) VALUES(?)", (owner,))
    con.commit()
    return len(owners)


def _inventory_one_owner(primary_lane, lanes, owner: str, known_mints: set[str]):
    result, provider = call_with_lane_resilient(
        primary_lane, lanes, "getTokenAccountsByOwner",
        [
            owner,
            {"programId": TOKEN_PROGRAM},
            {"encoding": "jsonParsed", "commitment": "finalized"},
        ],
    )
    values = (result or {}).get("value") if isinstance(result, dict) else None
    if not isinstance(values, list):
        raise RuntimeError(f"{owner}: getTokenAccountsByOwner returned unexpected data")

    holdings = []
    for item in values:
        if not isinstance(item, dict):
            continue
        pubkey = str(item.get("pubkey") or "")
        account = item.get("account") if isinstance(item.get("account"), dict) else {}
        data = account.get("data") if isinstance(account, dict) else None
        parsed = data.get("parsed") if isinstance(data, dict) else None
        info = parsed.get("info") if isinstance(parsed, dict) else None
        if not isinstance(info, dict):
            continue
        mint = str(info.get("mint") or "")
        if mint not in known_mints:
            continue
        token_amount = info.get("tokenAmount") if isinstance(info.get("tokenAmount"), dict) else {}
        amount = str(token_amount.get("amount") or "0")
        if amount != "1":
            continue
        parsed_owner = str(info.get("owner") or "")
        if parsed_owner != owner or not pubkey:
            raise RuntimeError(
                f"{mint}: owner-inventory parse mismatch owner={parsed_owner!r} expected={owner!r}"
            )
        holdings.append((mint, owner, pubkey, amount))
    return owner, holdings, provider


def _snapshot_one_mint(primary_lane, lanes, mint: str):
    result, provider = call_with_lane_resilient(
        primary_lane, lanes, "getTokenLargestAccounts",
        [mint, {"commitment": "finalized"}],
    )
    values = (result or {}).get("value") if isinstance(result, dict) else None
    if not isinstance(values, list):
        raise RuntimeError(f"{mint}: getTokenLargestAccounts returned unexpected data")

    positive = []
    for item in values:
        if not isinstance(item, dict):
            continue
        try:
            amount = int(str(item.get("amount") or "0"))
        except Exception:
            continue
        if amount > 0:
            positive.append((str(item.get("address") or ""), amount))

    provider_counts = Counter({provider: 1})
    if len(positive) == 1 and positive[0][1] == 1 and positive[0][0]:
        token_account, amount = positive[0]
        return mint, token_account, amount, 0, provider_counts

    if len(positive) == 0:
        supply, provider2 = call_with_lane_resilient(
            primary_lane, lanes, "getTokenSupply",
            [mint, {"commitment": "finalized"}],
        )
        provider_counts[provider2] += 1
        value = (supply or {}).get("value") if isinstance(supply, dict) else None
        raw_amount = str((value or {}).get("amount") or "") if isinstance(value, dict) else ""
        if raw_amount != "0":
            raise RuntimeError(
                f"{mint}: no positive token account but getTokenSupply={raw_amount!r}"
            )
        return mint, None, 0, 1, provider_counts

    raise RuntimeError(
        f"{mint}: expected one NFT token account with raw amount=1; got {positive[:5]}"
    )


def scan_chain_token_accounts(
    con: sqlite3.Connection,
    clients,
    cutover_rows: dict[str, dict[str, str]],
) -> Counter:
    """Authoritative current-state proof without 9,832 per-mint snapshot calls.

    Query the 2,008 known cutover raw owners once each. Every cutover-active Hero
    either still exists in that owner-union (possibly under a different old owner),
    or it must have moved to a genuinely new owner / been burned. Only that much
    smaller missing set needs getTokenLargestAccounts/getTokenSupply.
    """
    print()
    print("[6/8] Direct SPL current-state reconciliation")
    known_active = {
        mint for mint, row in cutover_rows.items() if not truthy_int(row.get("burned"))
    }
    owner_total = con.execute("SELECT COUNT(*) FROM owner_inventory").fetchone()[0]
    owner_done0 = con.execute(
        "SELECT COUNT(*) FROM owner_inventory WHERE complete=1"
    ).fetchone()[0]
    pending_owners = [
        str(r["owner"]) for r in con.execute(
            "SELECT owner FROM owner_inventory WHERE complete=0 ORDER BY owner"
        ).fetchall()
    ]
    print(f"    cutover raw owners: {owner_total:,} ({owner_done0:,} already cached)")
    print("    method: getTokenAccountsByOwner over the cutover owner frontier")
    print(
        f"    targets: Helius {HELIUS_OWNER_RPS:g} RPS + "
        f"Alchemy {ALCHEMY_OWNER_RPS:g} RPS (Alchemy cost: 10 CU/call)"
    )

    lanes = phase_lanes(clients, ALCHEMY_OWNER_RPS, HELIUS_OWNER_RPS)
    cycle = weighted_lane_cycle(lanes)
    providers = Counter()
    processed = 0
    start_clock = time.monotonic()

    if pending_owners:
        with ThreadPoolExecutor(max_workers=min(PARALLEL_WORKERS, len(pending_owners))) as ex:
            futures = {}
            for idx, owner in enumerate(pending_owners):
                lane = lanes[cycle[idx % len(cycle)]]
                futures[ex.submit(_inventory_one_owner, lane, lanes, owner, known_active)] = owner
            for fut in as_completed(futures):
                owner = futures[fut]
                try:
                    owner2, holdings, provider = fut.result()
                    assert owner2 == owner
                except Exception as exc:
                    con.execute(
                        "UPDATE owner_inventory SET error=? WHERE owner=?",
                        (str(exc)[:1500], owner),
                    )
                    con.commit()
                    raise
                for mint, howner, token_account, amount in holdings:
                    existing = con.execute(
                        "SELECT owner FROM owner_holdings WHERE mint=?", (mint,)
                    ).fetchone()
                    if existing and str(existing[0]) != howner:
                        raise RuntimeError(
                            f"{mint}: raw amount=1 appeared under two owners: "
                            f"{existing[0]} and {howner}"
                        )
                    con.execute(
                        "INSERT OR REPLACE INTO owner_holdings(mint,owner,token_account,token_amount) "
                        "VALUES(?,?,?,?)",
                        (mint, howner, token_account, amount),
                    )
                con.execute(
                    "UPDATE owner_inventory SET complete=1,error=NULL WHERE owner=?", (owner,)
                )
                providers[provider] += 1
                processed += 1
                if processed % 20 == 0:
                    con.commit()
                done = owner_done0 + processed
                if processed <= 5 or done % 100 == 0 or done == owner_total:
                    elapsed = max(0.001, time.monotonic() - start_clock)
                    rate = processed / elapsed
                    remaining = len(pending_owners) - processed
                    eta_min = remaining / rate / 60 if rate > 0 else 0
                    found = con.execute("SELECT COUNT(*) FROM owner_holdings").fetchone()[0]
                    print(
                        f"    owners {done:>4}/{owner_total:<4} | {rate:>4.1f}/s | "
                        f"ETA {eta_min:>4.1f}m | active Heroes located {found:>4}/{len(known_active)}"
                    )
        con.commit()

    # Every active Hero not found anywhere in the old-owner union has either
    # moved to a brand-new owner or reached zero supply. Query only those mints.
    located = {str(r[0]) for r in con.execute("SELECT mint FROM owner_holdings")}
    candidates = sorted(known_active - located)
    print(f"    active Heroes located under cutover-owner union: {len(located):,}")
    print(f"    moved-to-new-owner / burn candidates:            {len(candidates):,}")

    for mint in candidates:
        con.execute("INSERT OR IGNORE INTO chain_snapshot(mint) VALUES(?)", (mint,))
    con.commit()
    pending = [
        str(r["mint"]) for r in con.execute(
            "SELECT mint FROM chain_snapshot WHERE complete=0 AND mint IN (%s) ORDER BY mint"
            % ",".join("?" for _ in candidates),
            candidates,
        ).fetchall()
    ] if candidates else []
    cached_candidates = len(candidates) - len(pending)
    print(f"    direct mint snapshots needed: {len(candidates):,} ({cached_candidates:,} cached)")

    if pending:
        lanes2 = phase_lanes(clients, ALCHEMY_LIGHT_RPS, HELIUS_OWNER_RPS)
        cycle2 = weighted_lane_cycle(lanes2)
        start2 = time.monotonic()
        done2 = 0
        with ThreadPoolExecutor(max_workers=min(PARALLEL_WORKERS, len(pending))) as ex:
            futures = {}
            for idx, mint in enumerate(pending):
                lane = lanes2[cycle2[idx % len(cycle2)]]
                futures[ex.submit(_snapshot_one_mint, lane, lanes2, mint)] = mint
            for fut in as_completed(futures):
                mint = futures[fut]
                try:
                    mint2, token_account, amount, supply_zero, pcounts = fut.result()
                    assert mint2 == mint
                except Exception as exc:
                    con.execute(
                        "UPDATE chain_snapshot SET error=? WHERE mint=?",
                        (str(exc)[:1500], mint),
                    )
                    con.commit()
                    raise
                con.execute(
                    "UPDATE chain_snapshot SET complete=1,token_account=?,token_amount=?,"
                    "supply_zero=?,error=NULL WHERE mint=?",
                    (token_account, str(amount), supply_zero, mint),
                )
                providers.update(pcounts)
                done2 += 1
                if done2 % 20 == 0 or supply_zero:
                    con.commit()
                if done2 <= 5 or done2 % 50 == 0 or done2 == len(pending) or supply_zero:
                    elapsed = max(0.001, time.monotonic() - start2)
                    rate = done2 / elapsed
                    suffix = " | SUPPLY ZERO" if supply_zero else ""
                    hero = cutover_rows[mint].get("hero_name") or mint[:8]
                    print(
                        f"    candidates {cached_candidates + done2:>4}/{len(candidates):<4} | "
                        f"{rate:>4.1f}/s | {hero}{suffix}"
                    )
        con.commit()

    unresolved = con.execute(
        "SELECT mint,token_account FROM chain_snapshot WHERE complete=1 AND supply_zero=0 "
        "AND mint IN (%s) AND (owner IS NULL OR owner='') ORDER BY mint"
        % ",".join("?" for _ in candidates),
        candidates,
    ).fetchall() if candidates else []
    if unresolved:
        print(f"    candidate token accounts needing owner decode: {len(unresolved):,}")
        chunks = [
            unresolved[start:start + TOKEN_ACCOUNT_BATCH]
            for start in range(0, len(unresolved), TOKEN_ACCOUNT_BATCH)
        ]
        lanes3 = phase_lanes(clients, ALCHEMY_LIGHT_RPS, HELIUS_OWNER_RPS)
        cycle3 = weighted_lane_cycle(lanes3)

        def fetch_owner_chunk(idx, chunk):
            accounts = [str(r["token_account"]) for r in chunk]
            lane = lanes3[cycle3[idx % len(cycle3)]]
            result, provider = call_with_lane_resilient(
                lane, lanes3, "getMultipleAccounts",
                [accounts, {"encoding": "jsonParsed", "commitment": "finalized"}],
            )
            return chunk, result, provider

        with ThreadPoolExecutor(max_workers=min(8, len(chunks))) as ex:
            futures = {
                ex.submit(fetch_owner_chunk, idx, chunk): idx
                for idx, chunk in enumerate(chunks)
            }
            finished = 0
            for fut in as_completed(futures):
                chunk, result, provider = fut.result()
                providers[provider] += 1
                values = (result or {}).get("value") if isinstance(result, dict) else None
                if not isinstance(values, list) or len(values) != len(chunk):
                    raise RuntimeError("getMultipleAccounts returned an unexpected row count")
                for dbrow, account in zip(chunk, values):
                    mint = str(dbrow["mint"])
                    if not isinstance(account, dict):
                        raise RuntimeError(f"{mint}: current token account is missing")
                    data = account.get("data")
                    parsed = data.get("parsed") if isinstance(data, dict) else None
                    info = parsed.get("info") if isinstance(parsed, dict) else None
                    if not isinstance(info, dict):
                        raise RuntimeError(f"{mint}: token account did not return jsonParsed info")
                    owner = str(info.get("owner") or "")
                    parsed_mint = str(info.get("mint") or "")
                    ta = info.get("tokenAmount") if isinstance(info.get("tokenAmount"), dict) else {}
                    amount = str(ta.get("amount") or "")
                    if parsed_mint != mint or amount != "1" or not owner:
                        raise RuntimeError(
                            f"{mint}: token-account parse mismatch "
                            f"mint={parsed_mint!r} amount={amount!r} owner={owner!r}"
                        )
                    con.execute("UPDATE chain_snapshot SET owner=? WHERE mint=?", (owner, mint))
                finished += len(chunk)
                con.commit()
                print(f"    candidate owner decode {finished:>4}/{len(unresolved):<4}")

    owner_done = con.execute(
        "SELECT COUNT(*) FROM owner_inventory WHERE complete=1"
    ).fetchone()[0]
    if owner_done != owner_total:
        raise RuntimeError(f"Owner inventory complete {owner_done}/{owner_total}; refusing reconciliation")
    print(f"    owner-inventory calls by provider: {dict(providers)}")
    return providers


def _chain_evidence_for_mint(
    con: sqlite3.Connection, mint: str
) -> tuple[int, str, str]:
    holding = con.execute(
        "SELECT owner,token_account FROM owner_holdings WHERE mint=?", (mint,)
    ).fetchone()
    if holding:
        return 0, str(holding["owner"]), str(holding["token_account"])
    snap = con.execute(
        "SELECT complete,token_account,supply_zero,owner FROM chain_snapshot WHERE mint=?",
        (mint,),
    ).fetchone()
    if not snap or int(snap["complete"]) != 1:
        raise RuntimeError(f"{mint}: no complete direct-chain evidence")
    return int(snap["supply_zero"]), str(snap["owner"] or ""), str(snap["token_account"] or "")


def chain_gap_status(
    con: sqlite3.Connection,
    states: dict[str, HeroState],
    cutover_rows: dict[str, dict[str, str]],
) -> tuple[list[dict[str, str]], list[str], list[str]]:
    owner_gaps: list[dict[str, str]] = []
    missing_burns: list[str] = []
    false_burns: list[str] = []

    for mint, row in cutover_rows.items():
        if truthy_int(row.get("burned")):
            continue
        state = states[mint]
        supply_zero, owner, token_account = _chain_evidence_for_mint(con, mint)
        if supply_zero:
            if state.burned != 1:
                missing_burns.append(mint)
            continue
        if state.burned == 1:
            false_burns.append(mint)
            continue
        if not owner:
            raise RuntimeError(f"{mint}: direct chain evidence has no owner")
        if owner != (state.current_raw_owner or ""):
            owner_gaps.append({
                "mint": mint,
                "token_account": token_account,
                "chain_owner": owner,
                "reconstructed_owner": state.current_raw_owner or "",
            })
    return owner_gaps, missing_burns, false_burns


def recover_owner_gap_signatures(
    con: sqlite3.Connection,
    clients,
    owner_gaps: list[dict[str, str]],
    start_ts: int,
) -> int:
    print()
    print("[7/8] Direct-chain owner-gap signature recovery")
    if not owner_gaps:
        print("    no owner gaps; no extra signature calls required")
        return 0
    if len(owner_gaps) > MAX_CHAIN_GAP_MINTS:
        raise RuntimeError(
            f"Direct snapshot found {len(owner_gaps):,} owner gaps, above safety cap "
            f"{MAX_CHAIN_GAP_MINTS:,}."
        )

    # A direct token-account owner can legitimately differ from the dashboard's
    # canonical owner semantics for pre-existing marketplace custody. We only
    # care whether that token account changed AFTER the frozen cutover. Cache the
    # proof so reruns do not keep re-querying the same old custody accounts.
    cached_preexisting = 0
    pending = []
    for gap in owner_gaps:
        row = con.execute(
            "SELECT token_account,chain_owner,reconstructed_owner,"
            "signatures_after_cutover,complete FROM owner_gap_audit WHERE mint=?",
            (gap["mint"],),
        ).fetchone()
        if (
            row and int(row["complete"]) == 1
            and str(row["token_account"]) == gap["token_account"]
            and str(row["chain_owner"]) == gap["chain_owner"]
            and str(row["reconstructed_owner"]) == gap["reconstructed_owner"]
        ):
            if int(row["signatures_after_cutover"]) == 0:
                cached_preexisting += 1
            continue
        pending.append(gap)

    if cached_preexisting:
        print(f"    cached pre-cutover semantic/custody gaps: {cached_preexisting:,}")
    if not pending:
        print("    all current owner gaps already audited")
        return 0

    lanes = phase_lanes(clients, ALCHEMY_HEAVY_RPS)
    cycle = weighted_lane_cycle(lanes)
    before_count = con.execute(
        "SELECT COUNT(DISTINCT signature) FROM discovered_signatures"
    ).fetchone()[0]

    def fetch_gap(idx, gap):
        token_account = gap["token_account"]
        mint = gap["mint"]
        if not token_account:
            raise RuntimeError(f"{mint}: owner gap has no current token account")
        lane = lanes[cycle[idx % len(cycle)]]
        before = None
        out = []
        while True:
            opts = {"limit": SIGNATURE_PAGE_LIMIT, "commitment": "finalized"}
            if before:
                opts["before"] = before
            rows, _provider = call_with_lane(
                lane, lanes, "getSignaturesForAddress", [token_account, opts]
            )
            if not isinstance(rows, list):
                raise RuntimeError(f"{mint}: token-account history returned invalid data")
            reached = False
            for row in rows:
                if not isinstance(row, dict):
                    continue
                bt = row.get("blockTime")
                if bt is not None and int(bt) <= start_ts:
                    reached = True
                    continue
                sig = str(row.get("signature") or "")
                if sig and row.get("err") is None:
                    out.append((sig, bt))
            if reached or len(rows) < SIGNATURE_PAGE_LIMIT:
                break
            before = str((rows[-1] or {}).get("signature") or "")
            if not before:
                break
        return gap, out

    with ThreadPoolExecutor(max_workers=min(PARALLEL_WORKERS, len(pending))) as ex:
        futures = {
            ex.submit(fetch_gap, idx, gap): gap
            for idx, gap in enumerate(pending)
        }
        completed = 0
        for fut in as_completed(futures):
            gap = futures[fut]
            mint = gap["mint"]
            try:
                gap2, rows = fut.result()
                assert gap2["mint"] == mint
            except Exception as exc:
                con.execute(
                    "INSERT OR REPLACE INTO owner_gap_audit"
                    "(mint,token_account,chain_owner,reconstructed_owner,"
                    "signatures_after_cutover,complete,error) VALUES(?,?,?,?,0,0,?)",
                    (mint, gap["token_account"], gap["chain_owner"],
                     gap["reconstructed_owner"], str(exc)[:1500]),
                )
                con.commit()
                raise
            for sig, bt in rows:
                con.execute(
                    """INSERT OR IGNORE INTO discovered_signatures
                       (signature,mint,source,block_time) VALUES(?,?,?,?)""",
                    (sig, mint, "CURRENT_TOKEN_ACCOUNT_GAP", bt),
                )
            con.execute(
                "INSERT OR REPLACE INTO owner_gap_audit"
                "(mint,token_account,chain_owner,reconstructed_owner,"
                "signatures_after_cutover,complete,error) VALUES(?,?,?,?,?,1,NULL)",
                (mint, gap["token_account"], gap["chain_owner"],
                 gap["reconstructed_owner"], len(rows)),
            )
            completed += 1
            if completed % 20 == 0 or rows:
                con.commit()
            if completed <= 5 or completed % 25 == 0 or rows:
                owner_label = "Magic Eden V2 custody" if gap["chain_owner"] == ME_V2_CUSTODY else gap["chain_owner"][:8] + "…"
                print(
                    f"    gaps {completed:>4}/{len(pending):<4} | {mint[:8]}… | "
                    f"{owner_label} | post-cutover token-account signatures {len(rows)}"
                )
    con.commit()

    after_count = con.execute(
        "SELECT COUNT(DISTINCT signature) FROM discovered_signatures"
    ).fetchone()[0]
    added = after_count - before_count
    zero_count = con.execute(
        "SELECT COUNT(*) FROM owner_gap_audit WHERE complete=1 AND signatures_after_cutover=0"
    ).fetchone()[0]
    print(f"    proven pre-cutover owner-semantic/custody gaps: {zero_count:,}")
    print(f"    new unique signatures added: {added:,}")
    return added

def assert_chain_reconciled(
    con: sqlite3.Connection,
    states: dict[str, HeroState],
    cutover_rows: dict[str, dict[str, str]],
) -> dict[str, int]:
    owner_gaps, missing_burns, false_burns = chain_gap_status(con, states, cutover_rows)
    owner_rows = con.execute("SELECT COUNT(*) FROM owner_inventory WHERE complete=1").fetchone()[0]
    holdings_set = {str(r[0]) for r in con.execute("SELECT mint FROM owner_holdings")}
    active_mints = {
        mint for mint, row in cutover_rows.items() if not truthy_int(row.get("burned"))
    }
    candidate_mints = active_mints - holdings_set
    candidate_complete = 0
    zero = 0
    for mint in candidate_mints:
        row = con.execute(
            "SELECT complete,supply_zero FROM chain_snapshot WHERE mint=?", (mint,)
        ).fetchone()
        if row and int(row["complete"]) == 1:
            candidate_complete += 1
            zero += int(row["supply_zero"])

    # The cutover CSV's canonical owner field is not guaranteed to equal literal
    # SPL token-account authority for old marketplace custody. A mismatch is safe
    # for this *post-cutover* backfill only when the current token account has
    # independently been proven to have ZERO successful signatures after the
    # overlap boundary. That means the discrepancy necessarily predates cutover.
    preexisting_gaps = []
    unresolved_gaps = []
    for gap in owner_gaps:
        audit = con.execute(
            "SELECT token_account,chain_owner,reconstructed_owner,"
            "signatures_after_cutover,complete FROM owner_gap_audit WHERE mint=?",
            (gap["mint"],),
        ).fetchone()
        if (
            audit and int(audit["complete"]) == 1
            and int(audit["signatures_after_cutover"]) == 0
            and str(audit["token_account"]) == gap["token_account"]
            and str(audit["chain_owner"]) == gap["chain_owner"]
            and str(audit["reconstructed_owner"]) == gap["reconstructed_owner"]
        ):
            preexisting_gaps.append(gap)
        else:
            unresolved_gaps.append(gap)

    custody_counts = Counter(g["chain_owner"] for g in preexisting_gaps)
    me_custody = custody_counts.get(ME_V2_CUSTODY, 0)

    print()
    print("[8/8] Authoritative direct-chain reconciliation")
    print(f"    cutover owners inventoried: {owner_rows:,}")
    print(f"    active Heroes found there:  {len(holdings_set):,}")
    print(f"    direct candidate snapshots: {candidate_complete:,}/{len(candidate_mints):,}")
    print(f"    post-cutover owner mismatches unresolved: {len(unresolved_gaps):,}")
    print(f"    proven pre-cutover owner-semantic/custody gaps: {len(preexisting_gaps):,}")
    if me_custody:
        print(f"      Magic Eden V2 custody ({ME_V2_CUSTODY[:8]}…): {me_custody:,}")
    other = [(owner, count) for owner, count in custody_counts.most_common() if owner != ME_V2_CUSTODY]
    for owner, count in other[:8]:
        print(f"      other pre-cutover chain owner {owner}: {count:,}")
    print(f"    supply-zero not parsed burn:{len(missing_burns):,}")
    print(f"    parsed burn but supply=1:   {len(false_burns):,}")

    if unresolved_gaps or missing_burns or false_burns:
        for gap in unresolved_gaps[:10]:
            hero = cutover_rows[gap["mint"]].get("hero_name") or gap["mint"]
            print(
                f"      UNRESOLVED OWNER GAP {hero}: reconstructed={gap['reconstructed_owner']} "
                f"chain={gap['chain_owner']}"
            )
        for mint in missing_burns[:10]:
            print(f"      MISSING BURN {cutover_rows[mint].get('hero_name') or mint}")
        for mint in false_burns[:10]:
            print(f"      FALSE BURN {cutover_rows[mint].get('hero_name') or mint}")
        raise RuntimeError(
            "Post-cutover reconstruction still has direct-chain discrepancies that were not "
            "proven to predate the frozen checkpoint. Canonical files remain untouched."
        )

    return {
        "cutover_owners_inventoried": owner_rows,
        "active_heroes_found_in_owner_union": len(holdings_set),
        "direct_candidate_snapshot_rows": candidate_complete,
        "direct_candidate_mints": len(candidate_mints),
        "current_supply_zero_from_cutover_active": zero,
        "unresolved_post_cutover_owner_mismatches": 0,
        "proven_pre_cutover_owner_semantic_gaps": len(preexisting_gaps),
        "proven_pre_cutover_magic_eden_v2_custody_gaps": me_custody,
        "missing_burns": 0,
        "false_burns": 0,
    }


def world_calls_for_tx(tx: dict[str, Any]) -> list[WorldCall]:
    signers = transaction_signers(tx)
    signer = signers[0] if signers else None
    calls: dict[tuple[str, str, str | None], WorldCall] = {}
    for call in normalize_transaction(tx):
        wc = classify_world_call(
            signature=call.signature,
            event_time=call.block_time,
            executing_account=call.executing_account,
            data=call.data,
            signer=signer,
            account_arguments=call.account_arguments,
        )
        if wc:
            calls[(wc.action, wc.user_wallet, wc.staking_wallet)] = wc
    return list(calls.values())


def choose_world_call(movement, calls: list[WorldCall]) -> WorldCall | None:
    for wc in calls:
        if wc.action == "STAKE" and wc.staking_wallet and (
            movement.to_owner == wc.staking_wallet and movement.from_owner == wc.user_wallet
        ):
            return wc
        if wc.action == "UNSTAKE" and wc.staking_wallet and (
            movement.from_owner == wc.staking_wallet and movement.to_owner == wc.user_wallet
        ):
            return wc
    return None


def verify_and_reduce(
    con: sqlite3.Connection,
    cutover_rows: dict[str, dict[str, str]],
    checkpoint: datetime,
    known_mints: set[str],
) -> tuple[dict[str, HeroState], dict[str, Any]]:
    print()
    print("[5/8] Independent decode + in-memory cutover reduction")
    states = hero_objects(cutover_rows)
    raw = load_raw(con)
    records = []
    for sig, tx in raw.items():
        bt = tx.get("blockTime")
        if bt is None:
            raise RuntimeError(f"{sig}: raw transaction has no blockTime")
        dt = datetime.fromtimestamp(int(bt), timezone.utc)
        if dt <= checkpoint:
            # The discovery crawl intentionally overlaps the boundary; never replay
            # a transaction at/before the frozen checkpoint.
            continue
        records.append((dt, sig, tx))
    records.sort(key=lambda x: (x[0], x[1]))

    counts = Counter()
    sales_out = []
    unresolved = []
    seen_movement_keys = set()
    world_event_rows = []

    for dt, sig, tx in records:
        if (tx.get("meta") or {}).get("err") is not None:
            counts["FAILED_TX_SKIPPED"] += 1
            continue
        calls = world_calls_for_tx(tx)
        for wc in calls:
            counts[f"WORLD_{wc.action}"] += 1
            world_event_rows.append(wc)

        movements = normalize_token_movements(tx, known_mints)
        for move in movements:
            key = (move.signature, move.mint, move.classification, move.from_owners, move.to_owners)
            if key in seen_movement_keys:
                continue
            seen_movement_keys.add(key)

            if move.classification == "TRANSFER":
                if move.from_owner is None or move.to_owner is None:
                    unresolved.append(
                        f"{sig} {move.mint}: ambiguous TRANSFER owners {move.from_owners}->{move.to_owners}"
                    )
                    continue
                raw_move = RawMovement(sig, move.mint, move.block_time, "transfer", move.from_owner, move.to_owner)
                wc = choose_world_call(move, calls)
                states[move.mint] = apply_movement(states[move.mint], raw_move, wc)
                counts["TRANSFER"] += 1
                if wc:
                    counts[f"HERO_{wc.action}"] += 1
            elif move.classification == "BURN_OR_SEND_TO_ZERO":
                if not transaction_has_burn_instruction(tx, move.mint):
                    unresolved.append(
                        f"{sig} {move.mint}: sender-only movement without explicit SPL Burn/BurnChecked"
                    )
                    continue
                if move.from_owner is None:
                    unresolved.append(f"{sig} {move.mint}: burn has ambiguous source {move.from_owners}")
                    continue
                raw_move = RawMovement(sig, move.mint, move.block_time, "burn", move.from_owner, None)
                states[move.mint] = apply_movement(states[move.mint], raw_move)
                counts["BURN"] += 1
            elif move.classification == "MINT_OR_RECEIVE_FROM_ZERO":
                # All collection mints already existed at cutover. A post-cutover
                # receive-from-zero is unexpected and must be reviewed rather than
                # silently treated as ownership.
                unresolved.append(f"{sig} {move.mint}: unexpected receive-from-zero after cutover")

        # Quest Restart is user-level and can touch many currently staked Heroes.
        for wc in calls:
            if wc.action == "QUEST_RESTART":
                changed = apply_quest_restart_to_collection(states, wc)
                counts["QUEST_HERO_UPDATES"] += changed

        # Market sale decoding is independent from transfer discovery. A real sale
        # should be recoverable from the same raw transaction if it occurred.
        try:
            sales = decode_sales(normalize_transaction(tx), known_mints)
        except Exception as exc:
            unresolved.append(f"{sig}: market decoder error: {exc}")
            sales = []
        for sale in sales:
            sales_out.append(sale)
            counts["SALE"] += 1

    if unresolved:
        print(f"    unresolved raw cases: {len(unresolved)}")
        for msg in unresolved[:20]:
            print(f"      - {msg}")
        raise RuntimeError(
            "Raw backfill contains unresolved movement/decoder cases. "
            "No canonical state was written; stop for review."
        )

    initial_burns = sum(truthy_int(r.get("burned")) for r in cutover_rows.values())
    final_burns = sum(s.burned for s in states.values())
    final_staked = sum(s.current_world_staked for s in states.values() if not s.burned)
    final_holders = len({
        s.current_beneficial_owner for s in states.values()
        if not s.burned and s.current_beneficial_owner
    })

    summary = {
        "transactions_after_cutover": len(records),
        "transfers": counts["TRANSFER"],
        "burns": counts["BURN"],
        "hero_stakes": counts["HERO_STAKE"],
        "hero_unstakes": counts["HERO_UNSTAKE"],
        "world_stake_calls": counts["WORLD_STAKE"],
        "world_unstake_calls": counts["WORLD_UNSTAKE"],
        "world_quest_restart_calls": counts["WORLD_QUEST_RESTART"],
        "quest_hero_updates": counts["QUEST_HERO_UPDATES"],
        "sales": counts["SALE"],
        "cutover_burned": initial_burns,
        "reconstructed_burned": final_burns,
        "reconstructed_active": ORIGINAL_SUPPLY - final_burns,
        "reconstructed_staked": final_staked,
        "reconstructed_beneficial_holders": final_holders,
    }

    print(f"    raw tx after checkpoint: {summary['transactions_after_cutover']:,}")
    print(f"    Hero transfers:         {summary['transfers']:,}")
    print(f"    explicit burns:         {summary['burns']:,}")
    print(f"    Hero stake / unstake:   {summary['hero_stakes']:,} / {summary['hero_unstakes']:,}")
    print(f"    Quest Restart calls:    {summary['world_quest_restart_calls']:,}")
    print(f"    Hero quest updates:     {summary['quest_hero_updates']:,}")
    print(f"    decoded market sales:   {summary['sales']:,}")
    print(f"    reconstructed supply:   {summary['reconstructed_active']:,} active / {summary['reconstructed_burned']:,} burned")
    print(f"    reconstructed staked:   {summary['reconstructed_staked']:,}")
    print(f"    beneficial holders:     {summary['reconstructed_beneficial_holders']:,}")
    return states, summary


def fetch_asset_batch_resilient(client, ids: list[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    try:
        result = client.call(
            "getAssetBatch",
            {"ids": ids, "options": {"showUnverifiedCollections": True, "showCollectionMetadata": False}},
        )
    except Exception as exc:
        if len(ids) == 1:
            raise RuntimeError(f"getAssetBatch could not resolve {ids[0]}: {exc}") from exc
        mid = len(ids) // 2
        out = fetch_asset_batch_resilient(client, ids[:mid])
        out.update(fetch_asset_batch_resilient(client, ids[mid:]))
        return out
    if not isinstance(result, list):
        raise RuntimeError(f"getAssetBatch returned unexpected {type(result).__name__}")
    found = {str(x.get("id")): x for x in result if isinstance(x, dict) and x.get("id") in ids}
    missing = [x for x in ids if x not in found]
    if missing and len(missing) < len(ids):
        found.update(fetch_asset_batch_resilient(client, missing))
    return found




def das_audit(client, states: dict[str, HeroState]) -> tuple[dict[str, int], list[str]]:
    print()
    print("[audit] DAS current-state comparison (non-authoritative)")
    mints = sorted(states)
    inventory: dict[str, dict[str, Any]] = {}
    for start in range(0, len(mints), DAS_BATCH_LIMIT):
        batch = mints[start:start + DAS_BATCH_LIMIT]
        inventory.update(fetch_asset_batch_resilient(client, batch))
    if len(inventory) != len(states):
        raise RuntimeError(f"DAS audit resolved {len(inventory):,}/{len(states):,} known mints")

    active_owner_match = 0
    active_owner_diff = 0
    active_owner_missing = 0
    burned_flag_match = 0
    burned_flag_regression = 0
    das_new_burn_disagreement = 0
    gap_targets: set[str] = set()

    for mint, state in states.items():
        asset = inventory[mint]
        burnt = asset.get("burnt") is True
        ownership = asset.get("ownership") if isinstance(asset.get("ownership"), dict) else {}
        owner = str((ownership or {}).get("owner") or "") or None
        if state.burned:
            if burnt:
                burned_flag_match += 1
            else:
                # We already proved DAS can regress old burn flags, so this is
                # reported but never used to mutate/reopen a burned Hero.
                burned_flag_regression += 1
        else:
            if burnt:
                das_new_burn_disagreement += 1
                gap_targets.add(mint)
            if not owner:
                active_owner_missing += 1
                gap_targets.add(mint)
            elif owner == state.current_raw_owner:
                active_owner_match += 1
            else:
                active_owner_diff += 1
                gap_targets.add(mint)

    out = {
        "active_owner_match": active_owner_match,
        "active_owner_diff": active_owner_diff,
        "active_owner_missing": active_owner_missing,
        "burned_flag_match": burned_flag_match,
        "burned_flag_regression": burned_flag_regression,
        "das_marks_active_as_burned": das_new_burn_disagreement,
        "gap_targets": len(gap_targets),
    }
    print(f"    active raw-owner matches: {active_owner_match:,}")
    print(f"    active raw-owner diffs:   {active_owner_diff:,}")
    print(f"    active owner missing:     {active_owner_missing:,}")
    print(f"    burned flag matches:      {burned_flag_match:,}")
    print(f"    burned flag regressions:  {burned_flag_regression:,}")
    if das_new_burn_disagreement:
        print(f"    DAS marks reconstructed-active as burned: {das_new_burn_disagreement:,}")
    print(f"    mints selected for gap audit: {len(gap_targets):,}")
    return out, sorted(gap_targets)


def main() -> None:
    print("=" * 78)
    print("GUILD SAGA — CUTOVER TRANSACTION BACKFILL · DUAL FREE TIER · READ ONLY")
    print("=" * 78)
    cutover_rows, checkpoint = cutover_state()
    states0 = hero_objects(cutover_rows)
    known_mints = set(states0)
    clients = clients_from_repo(ROOT)
    helius = helius_client(clients)
    prepare_rpc_clients(clients)
    discovery_start_ts = int(checkpoint.timestamp()) - DISCOVERY_LOOKBACK_SECONDS

    active_cutover = sum(1 for st in states0.values() if not st.burned)
    print(f"Cutover checkpoint:       {iso_z(checkpoint)}")
    print(f"Known Heroes:             {len(known_mints):,}")
    print(f"Active at cutover:        {active_cutover:,}")
    print(f"Discovery overlap:        {DISCOVERY_LOOKBACK_SECONDS // 60} minutes")
    print("Paid Helius methods used: NONE")
    print("Bulk methods:             concurrent standard Solana RPC (Free-compatible)")
    print("Canonical files modified: NONE")
    print(f"Resume cache:             {DB_PATH.relative_to(ROOT)}")
    print()
    print("Throughput plan:          Helius + Alchemy concurrently")
    print(f"  Helius target:          {HELIUS_RPC_RPS:g} RPC requests/sec (Free cap 10)")
    print(f"  Alchemy history target: {ALCHEMY_HEAVY_RPS:g}/sec (40 CU each; <300 CU/sec)")
    print(f"  Owner inventory target: Helius {HELIUS_OWNER_RPS:g}/sec + Alchemy {ALCHEMY_OWNER_RPS:g}/sec (10 CU each)")
    print()

    con = open_db(checkpoint)
    try:
        queued = seed_mints(con, cutover_rows)
        if queued != active_cutover:
            raise RuntimeError(f"Seeded {queued:,} active mints; expected {active_cutover:,}")
        owner_seed = seed_owner_inventory(con, cutover_rows)
        print(f"Cutover raw-owner frontier: {owner_seed:,}")

        eligible_history = preflight_standard_history(clients, cutover_rows)
        discovery_providers = scan_mint_histories(con, eligible_history, discovery_start_ts)
        global_sources = scan_global_sources(con, helius, discovery_start_ts)

        providers1 = fetch_raw_transactions(con, clients, "[4/8]")
        reconstructed1, summary1 = verify_and_reduce(
            con, cutover_rows, checkpoint, known_mints
        )

        snapshot_providers = scan_chain_token_accounts(con, clients, cutover_rows)
        owner_gaps, missing_burns1, false_burns1 = chain_gap_status(con, reconstructed1, cutover_rows)
        print()
        print("    first direct-chain comparison:")
        print(f"      owner gaps:              {len(owner_gaps):,}")
        print(f"      supply-zero missing burn:{len(missing_burns1):,}")
        print(f"      false parsed burns:      {len(false_burns1):,}")

        if false_burns1:
            raise RuntimeError(
                f"Reducer marked {len(false_burns1)} cutover-active Hero(s) burned while "
                "direct token supply is still 1. Stop for review."
            )

        # Burn instructions explicitly reference the mint, so the complete mint
        # history scan should already have found every post-cutover burn. If it
        # did not, do not invent a burn from current supply alone.
        if missing_burns1:
            print("    NOTE: supply-zero gaps will be checked after owner-gap recovery;")
            print("          no burn is inferred without its raw SPL Burn/BurnChecked transaction.")

        gap_added = recover_owner_gap_signatures(
            con, clients, owner_gaps, discovery_start_ts
        )

        providers2 = Counter()
        if gap_added:
            providers2 = fetch_raw_transactions(con, clients, "[7/8]")
            print()
            print("[final reducer] Replaying from frozen cutover with recovered signatures")
            reconstructed2, summary2 = verify_and_reduce(
                con, cutover_rows, checkpoint, known_mints
            )
        else:
            reconstructed2, summary2 = reconstructed1, summary1

        chain_check = assert_chain_reconciled(
            con, reconstructed2, cutover_rows
        )

        # DAS is intentionally last and non-authoritative. We already proved its
        # burn flag can regress historical burns, so discrepancies are reported,
        # never used to rewrite the direct-chain result.
        das_counts, _ = das_audit(helius, reconstructed2)

        provider_totals = providers1 + providers2
        mint_total = con.execute("SELECT COUNT(*) FROM mint_scan").fetchone()[0]
        mint_done = con.execute("SELECT COUNT(*) FROM mint_scan WHERE complete=1").fetchone()[0]
        distinct_sigs = con.execute(
            "SELECT COUNT(DISTINCT signature) FROM discovered_signatures"
        ).fetchone()[0]

        print()
        print("=" * 78)
        print("FREE-TIER BACKFILL RECONSTRUCTION COMPLETE — STILL READ ONLY")
        print("=" * 78)
        final = {
            "cutover_checkpoint": iso_z(checkpoint),
            "active_mints_scanned": f"{mint_done}/{mint_total}",
            "global_source_signatures": global_sources,
            "mint_history_providers_this_run": dict(discovery_providers),
            "snapshot_providers_this_run": dict(snapshot_providers),
            "unique_discovered_signatures": distinct_sigs,
            "raw_transaction_providers_this_run": dict(provider_totals),
            "owner_gap_signatures_added": gap_added,
            **summary2,
            "direct_chain_reconciliation": chain_check,
            "das_audit_non_authoritative": das_counts,
        }
        print(json.dumps(final, indent=2))
        print()
        print("Paste this full console output back into ChatGPT. Do not commit/push yet.")
    finally:
        con.close()


if __name__ == "__main__":
    main()
