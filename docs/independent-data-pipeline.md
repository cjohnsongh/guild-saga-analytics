# Guild Saga Analytics — Independent Production Data Pipeline

## Non-negotiable production rule

The public site must not depend on Dune. Dune query backups are migration specifications and historical validation material only. No production collector, build step, browser request, scheduled job, or freshness calculation may call Dune.

## Cutover

- Permanent cutover date: **2026-08-26**.
- The Aug. 26 generated website JSON is frozen under `tests/fixtures/cutover-2026-08-26/`.
- Historical baselines remain immutable.
- Future runs persist only new state/events and advance checkpoints only after a fully successful validation.

## Runtime shape

`Solana -> event discovery/inbox -> Python reducer -> canonical state -> existing dashboard builder -> live validator -> commit -> Cloudflare Pages`

The browser receives static JSON only. API/provider failures therefore make the site stale, not broken.

## Incremental rule

A normal run processes only activity since the last successful checkpoint, with a small overlap for late indexing/delivery and deterministic deduplication. A missed run leaves the old checkpoint in place so the next successful run automatically covers the gap.

The Aug. 26-to-first-live-run catch-up is the only initial multi-day catch-up. Six months later the collector still processes only the unprocessed interval, not six months of history.

## Dynamic domains

### Hero state

Derived from Guild Hero token movements plus World Mode instructions. One coherent state produces active supply, burns, beneficial holders, holder tiers, staking, quest activity, burn history, and burned rarity.

Validated World Mode program:

- Program: `6AzuBKDsR88vinh399HV5v7fgB1eZyoYwQ3PmdYqFRZG`
- Stake: `5de284a68d093065`
- Unstake: `8eb5bf9552afd864`
- Quest Restart: `042211a8aa29e88801`
- Stake/unstake staking wallet: account argument 4 (Dune 1-based indexing)

Quest Restart is user-level. A qualifying restart advances each Hero currently staked by that user only when it occurs after the Hero's current stake began and after any already-known qualifying restart.

Quest buckets are derived from the stored timestamp at build time; a Hero can age into another bucket without a new transaction.

### Market activity

Validated direct-program paths:

- Magic Eden V2 `M2mx93ekt1fmXSVkTrUL9xVFHkmME8HTUi5Cyc5aF7K`
- Tensor Marketplace `TCMPhJdwDryooaGtiocG1u3xcYbRpiJzb283XfCZsDp`
- Tensor AMM `TAMM6ub33ij1mbetoMyVBLeKY5iP41i4UPUJQGkhfsg`

Sales are deduplicated by `(signature, mint)`. Buyer, seller, mint, and price come from the validated direct-program layouts, not a generic marketplace label.

### Floor/listings

Daily marketplace snapshot, independently checkpointed. A failed source request must not fabricate today's row using yesterday's values.

### Historical/static

Launch/mint history, rarity map, old treasury branches, and historical conversion data are immutable and require no scheduled chain scan.

## Failure behavior

No checkpoint advances until collection, parsing, build, and validation all succeed. If anything fails, the current deployed static site remains untouched.

## Validation split

- `scripts/validate_cutover.py`: permanent Aug. 26 regression fixture.
- `scripts/validate_live.py`: production invariants and optional freshness gate.
- `scripts/validate.py`: production entry point, intentionally points to live validation.

The old design that compared every future live KPI to the Aug. 26 snapshot is intentionally retired.

## Migration backtest gate

Before any new market row can be published, `scripts/capture_market_backtest.py` must reproduce the already-known post-baseline live sales from raw `getTransaction` responses. Those fetched public transactions become deterministic test fixtures, removing provider/network dependence from future parser regression tests.
