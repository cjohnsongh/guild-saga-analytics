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

Daily marketplace snapshot, independently checkpointed. Production preserves the final Dune query's source-field and history semantics without calling Dune: Magic Eden's public Solana collection-stats endpoint is read with `listingAggMode=false` for `floorPrice` and `listingAggMode=true` for aggregated `listedCount`. Floor lamports are converted to SOL. A failed source request must not fabricate today's row using yesterday's values, and missed dates are not backfilled with guessed values.

Cloudflare emits `floor_listings_daily` at 23:30 UTC, with a same-day retry at 23:50 UTC. The late-day primary sample is intentionally close to the UTC day boundary so the post-Tensor series better matches the historical Tensor daily-close semantics without scraping exactly at midnight. The first successful run owns that UTC date; once the date is committed, the second firing skips the marketplace request and only proves that the already-current floor snapshot is deployed. This deliberately makes the second firing a recovery opportunity rather than a second sample for the same date. The pipeline advances only `floor_checkpoint_date`, rebuilds only `floor-listings.json` plus the floor section of `summary.json`, runs the full regression/cutover/live validation suite, commits an exact allow-listed path set, pushes race-safely, and proves the resulting Cloudflare Pages JSON before reporting success.

### Historical/static

Launch/mint history, rarity map, old treasury branches, and historical conversion data are immutable and require no scheduled chain scan.

## Failure behavior

No checkpoint advances until collection, parsing, build, and validation all succeed. If anything fails, the current deployed static site remains untouched.

## Durable webhook production cycle

The production ingestion path is:

`Helius RAW webhook -> authenticated Cloudflare Worker -> D1 durable inbox -> Python reducer -> canonical data -> static JSON -> Git commit -> Cloudflare Pages -> exact D1 ACK`

The Worker authenticates, deduplicates by signature with `INSERT OR IGNORE`, and stores raw payloads. It deliberately does not parse transactions. `/internal/pending` freezes a `snapshot_received_at` watermark on the first page and carries it across every cursor page. Events received after that watermark are outside the selected batch and remain pending for a later run.

The non-secret webhook identity, RAW type, receiver URL, and immutable activation boundary are tracked in `data/state/webhook_production.json`. Ephemeral runners never depend on the ignored one-off setup receipt.

Before reduction, `scripts/refresh_webhook_watch_frontier.py` overlays token accounts found in the stable raw batch onto `data/state/webhook_token_accounts.csv`. Old token accounts are retained permanently. Missing destination accounts are added to the Helius address set, and the established gap/overlap audit fetches and durably replays transactions that could have landed while the watch set changed. `GUILD_SAGA_DRY_RUN=1` computes and reports both deltas without changing Helius or D1.

The reducer persists per-Hero slot/signature cursors and the append-only webhook event and market ledgers. Duplicate delivery is harmless; stale slots cannot regress state; a same-slot/different-signature ownership conflict fails closed. Custody-aware beneficial ownership, explicit burns, World Mode transitions, quest restart semantics, and market discriminators remain in the tested collector modules.

### Commit-backed recovery

Every data release writes `data/state/production_batch_manifest.json`. The manifest contains the exact stable snapshot, exact signatures, canonical hashes, and the three deployed dynamic JSON hashes. It intentionally does not contain its own Git SHA. A fresh runner discovers the release with `git log` on the manifest path.

D1 is authoritative for ACK state. At the start of every production run, `scripts/production_pipeline.py` checks for a committed manifest, locates its introducing commit, re-discovers and semantically verifies the Cloudflare Pages deployment for that commit, and submits the exact manifest signature set to the idempotent ACK endpoint. Only after all signatures are proven processed and absent from pending can a new snapshot be prepared.

This covers runner termination:

- Before commit: no manifest was pushed and D1 remains pending, so reduction safely retries.
- After push but before deploy verification: the next runner finds the manifest commit, verifies its deployment, then ACKs.
- After deployment but before ACK: the same recovery path re-verifies and ACKs.
- Immediately after ACK: the idempotent ACK response proves all manifest signatures processed again.
- During later arrivals: only manifest signatures are ACKed; post-snapshot arrivals remain pending.

There is no ignored local receipt in the correctness boundary and no D1 schema migration.

### GitHub Actions

`.github/workflows/production-pipeline.yml` and `.github/workflows/floor-listings.yml` share the repository-wide concurrency group `guild-saga-production-pipeline` with `cancel-in-progress: false`, so their writes to `main` cannot race each other. Automatic production is entered only through the exact Cloudflare `repository_dispatch` event type owned by each workflow. Manual `workflow_dispatch` is dry-run only.

The webhook dry run authenticates to the Worker, freezes pending state, checks Helius and Alchemy RPC health, exercises GitHub deployment discovery, computes the watch frontier in a disposable Git clone, reduces any selected batch there, builds candidate JSON, and runs the complete unit/cutover/live validation suite. The floor/listings dry run fetches both Magic Eden stats views, applies the candidate only in a disposable clone, rebuilds the floor-facing JSON, and runs the same regression/cutover/live validation gates. Both verify the source checkout remains unchanged.

Required review/dry-run repository secrets are:

- `HELIUS_API_KEY`
- `ALCHEMY_API_KEY`
- `PIPELINE_TOKEN`

Production frontier mutation and gap replay additionally require `HELIUS_WEBHOOK_AUTH`. This is the existing Worker webhook authorization value; it must be configured as a GitHub secret before changing the workflow to production mode. No secret value belongs in Git, logs, command arguments, or documentation.

### Commit, deploy, ACK gates

Production mode runs the existing complete offline tests plus `validate_cutover.py` and `validate_live.py`, the existing secret-containment audit, `git diff --check`, and explicit-path staging. It fetches immediately before commit/push and refuses a moved `origin/main`; force-push is never used. Deployment verification queries GitHub commit status/check-run metadata using `GITHUB_TOKEN`, discovers the exact Pages origin, and compares deployed JSON semantically with the release commit. A timeout or mismatch leaves D1 pending.

The invariant is absolute: **no successful commit plus verified exact deployment means no D1 ACK**.

### Operations and recovery

To inspect without mutation, run:

```text
python scripts/audit_webhook_pending.py
python scripts/validate_cutover.py
python scripts/validate_live.py
```

The audit reports the stable pending count and decoded candidate effects without ACKing. Worker `/internal/stats` may also be queried with `PIPELINE_TOKEN` supplied through an environment variable; never place its value directly in a logged command.

To disable processing safely, disable the GitHub workflow or remove its schedule after activation. Do not disable the Worker or Helius webhook: ingestion continues accumulating deduplicated pending rows in D1, and the next successful run catches up.

For a stuck committed-but-unACKed batch, do not re-run the reducer and do not edit the manifest. Re-run `python scripts/production_pipeline.py --mode production` from a clean, current `main` checkout with all production secrets. Recovery runs before new preparation, re-verifies the manifest's introducing release, and ACKs only its exact signatures. If verification still fails, leave the rows pending and investigate the Pages check/deployment.

The active scheduler is Cloudflare, not GitHub's native `schedule:` event. The Worker dispatches `production_cron` every 30 minutes and `floor_listings_daily` on its twice-daily retry clock. Do not introduce a second automatic GitHub clock; keep manual Actions runs dry-run only and preserve the shared concurrency group.

## Validation split

- `scripts/validate_cutover.py`: permanent Aug. 26 regression fixture.
- `scripts/validate_live.py`: production invariants and optional freshness gate.
- `scripts/validate.py`: production entry point, intentionally points to live validation.

The old design that compared every future live KPI to the Aug. 26 snapshot is intentionally retired.

## Migration backtest gate

Before any new market row can be published, `scripts/capture_market_backtest.py` must reproduce the already-known post-baseline live sales from raw `getTransaction` responses. Those fetched public transactions become deterministic test fixtures, removing provider/network dependence from future parser regression tests.
