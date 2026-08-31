# Guild Saga Analytics production data pipeline

The production website is an incremental, fail-closed system. The browser reads static JSON, while raw Solana events, daily marketplace observations, deterministic reducers, validation, release commits, and deployment proof happen outside the browser.

For the complete architecture and reimplementation guide, see [`architecture.md`](architecture.md).

## Activation boundary

- Canonical baseline date: **2026-08-26**.
- The baseline website output is frozen under `tests/fixtures/cutover-2026-08-26/`.
- Historical baseline datasets remain immutable.
- Future Hero/market production operates incrementally from persisted checkpoints and append-only ledgers.

## Live production cycle

```text
Helius RAW webhook
  -> authenticated Cloudflare Worker
  -> D1 durable inbox
  -> stable pending snapshot
  -> GitHub Actions
  -> Python reducer
  -> canonical state + public JSON
  -> validation
  -> Git commit
  -> Cloudflare Pages proof
  -> exact D1 ACK
```

Cloudflare Cron Triggers dispatch `production_cron` at :00 and :30 UTC. Manual GitHub Actions execution is dry-run only.

The Worker stores and deduplicates raw payloads. It does not calculate Guild Saga state.

## Dynamic domains

### Hero state

Derived from Guild Hero token movements and validated World Mode instructions. One coherent state produces active supply, burns, beneficial holders, holder tiers, staking, quest activity, burn history, and burned rarity.

Validated World Mode program:

- Program: `6AzuBKDsR88vinh399HV5v7fgB1eZyoYwQ3PmdYqFRZG`
- Stake: `5de284a68d093065`
- Unstake: `8eb5bf9552afd864`
- Quest Restart: `042211a8aa29e88801`
- Stake/unstake staking wallet: fourth account argument, Python index `3`

Quest Restart is user-level. A qualifying restart advances Heroes currently staked by that user only when the restart follows the current stake and any already-known qualifying restart.

Quest buckets are calculated from timestamps at build time, so a Hero can age into another bucket without a new transaction.

### Market activity

Validated direct-program paths:

- Magic Eden V2 `M2mx93ekt1fmXSVkTrUL9xVFHkmME8HTUi5Cyc5aF7K`
- Tensor Marketplace `TCMPhJdwDryooaGtiocG1u3xcYbRpiJzb283XfCZsDp`
- Tensor AMM `TAMM6ub33ij1mbetoMyVBLeKY5iP41i4UPUJQGkhfsg`

Sales are deduplicated by `(signature, mint)`. Buyer, seller, mint, price, and royalty values come from validated program layouts.

### Floor/listings

This domain is independently checkpointed. Cloudflare dispatches it at 23:30 UTC and 23:50 UTC. The second firing is same-day recovery only.

A source failure does not write an observation and never copies the previous day's value forward. The pipeline writes at most one row per UTC date.

### Historical/static

Launch/mint history, rarity, established historical sales, and known funding history are local baseline data and do not require a scheduled full-chain scan.

## Stable webhook batches

`GET /internal/pending` freezes a `snapshot_received_at` watermark on the first page. The watermark is carried through subsequent pages. Deliveries received after that point remain pending for the next run.

The inbox signature primary key makes repeated Helius delivery harmless.

## Watch frontier

`scripts/refresh_webhook_watch_frontier.py` overlays token accounts discovered in the stable batch onto `data/state/webhook_token_accounts.csv`. Old token accounts remain watched. Gap/overlap auditing covers transactions that could have landed while the watch set changed.

Dry-run mode computes the frontier without changing provider configuration or acknowledging D1 events.

## Commit-backed recovery

`data/state/production_batch_manifest.json` records the exact prepared stable snapshot, exact signatures, and expected hashes.

At the start of every production run, the pipeline first checks for a committed manifest that has not yet been fully acknowledged. It discovers the introducing release, verifies the corresponding Pages output, then submits only the manifest signature set to the idempotent ACK endpoint.

This makes crashes recoverable without guessing whether a previous runner had reached deployment or ACK.

## GitHub Actions

Both production domains use the concurrency group `guild-saga-production-pipeline` with `cancel-in-progress: false`.

- `.github/workflows/production-pipeline.yml` accepts production only from `production_cron`.
- `.github/workflows/floor-listings.yml` accepts production only from `floor_listings_daily`.
- Manual `workflow_dispatch` remains dry-run only.

## Commit, deploy, and ACK gates

Production:

1. runs offline tests and live/baseline validators;
2. checks the exact release inventory and `git diff --check`;
3. fetches `origin/main` immediately before commit/push and refuses a race;
4. never force-pushes;
5. discovers the Cloudflare Pages deployment for the release commit;
6. compares deployed JSON with the expected release output;
7. ACKs D1 only after exact deployment proof.

The invariant is simple: **no proven public release means no live D1 ACK**.

## Validation split

- `scripts/validate_cutover.py`: permanent baseline regression fixture
- `scripts/validate_live.py`: current production invariants and optional freshness checks
- `scripts/validate.py`: production validation entry point

## Operations

Non-mutating inspection:

```text
python scripts/audit_webhook_pending.py
python scripts/validate_cutover.py
python scripts/validate_live.py
```

If processing is temporarily disabled, leave the Worker and Helius webhook running so raw deliveries continue accumulating in D1. The next successful production run can catch up from the unchanged checkpoint.
