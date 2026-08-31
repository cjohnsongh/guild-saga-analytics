# Guild Saga Analytics architecture

This is the canonical technical map of the production website and data system. It is written both for maintainers and for an engineer or LLM attempting to understand the system or adapt it to another Solana collection.

## Design goals

1. The browser receives static JSON and images only.
2. Raw live events are durably stored before interpretation.
3. Collection rules are deterministic and regression-tested.
4. A failed run leaves the last proven public state untouched.
5. Checkpoints advance only after the relevant work succeeds.
6. A live webhook batch is acknowledged only after the exact committed site output is verified on Cloudflare Pages.
7. Separate data domains do not silently substitute values for one another.

## System overview

### Live Hero and market state

```text
Solana
  -> Helius RAW webhook
  -> Cloudflare Worker authentication
  -> D1 pending inbox, unique by signature
  -> stable pending snapshot
  -> Cloudflare cron at :00 and :30 UTC
  -> GitHub repository_dispatch: production_cron
  -> scripts/production_pipeline.py
  -> collector normalization + deterministic reducers
  -> public JSON builder + full validation
  -> exact allow-listed Git commit
  -> Cloudflare Pages deployment discovery and content proof
  -> D1 ACK for the exact manifest signatures
```

The Worker is intentionally not a Guild Saga parser. It authenticates, deduplicates, stores raw payloads, exposes the internal pending/ACK interface, and dispatches approved scheduled events. State interpretation remains in audited Python code.

### Daily floor and listings

```text
Cloudflare cron at 23:30 UTC
  -> GitHub repository_dispatch: floor_listings_daily
  -> Magic Eden collection stats
  -> one UTC-dated floor/listing row
  -> public floor JSON rebuild
  -> validation
  -> exact allow-listed Git commit
  -> Cloudflare Pages deployment proof

23:50 UTC repeats the same dispatch as a recovery opportunity.
```

The second firing is not a second sample. If the current UTC date is already checkpointed, the pipeline proves the existing deployment and exits without requesting a replacement observation. Missing days remain missing.

### Historical and static domains

Mint history, rarity, historical sale records, known funding history, and other established baseline facts live in immutable or append-only local datasets. Normal production does not re-scan the entire chain to reconstruct them on every run.

## Cloudflare responsibilities

### Worker

`cloudflare/webhook-inbox/src/index.js`

- receives Helius raw webhook POSTs;
- authenticates the exact configured webhook authorization value;
- inserts each transaction signature once into D1;
- exposes protected pending, ACK, failure, statistics, and activation endpoints;
- maps only known cron strings to allow-listed GitHub repository-dispatch event types;
- retries transient dispatch failures but fails visibly on authentication/configuration problems.

### D1

D1 is the durable handoff between webhook delivery and repository processing. The signature primary key makes repeated provider delivery harmless.

The first pending page freezes a `snapshot_received_at` watermark. Every later page in the same run carries that watermark. Events received after the watermark are outside the selected batch and stay pending for a future run.

### Cron Triggers

`cloudflare/webhook-inbox/wrangler.jsonc`

- `0,30 * * * *` dispatches `production_cron` twice per hour.
- `30,50 23 * * *` dispatches `floor_listings_daily` at 23:30 UTC and again at 23:50 UTC for same-day recovery.

GitHub manual `workflow_dispatch` remains dry-run only for both production domains.

### Pages

Cloudflare Pages serves the static React site and public JSON. A successful Git push is not sufficient proof of publication. Production code discovers the deployment associated with the release commit and compares the deployed JSON with the expected commit output before declaring success.

## GitHub Actions responsibilities

### Live pipeline

`.github/workflows/production-pipeline.yml`

- accepts `repository_dispatch` only for `production_cron` as production entry;
- keeps manual dispatch non-mutating;
- compiles Python before execution;
- grants write permission only to the production job;
- passes provider and Worker secrets through environment variables;
- uses the shared `guild-saga-production-pipeline` concurrency group with cancellation disabled.

### Floor/listings pipeline

`.github/workflows/floor-listings.yml`

- accepts production only for `floor_listings_daily`;
- keeps manual dispatch non-mutating;
- shares the same concurrency group as the live pipeline so two release jobs cannot race on `main`.

## Live Hero state

World Mode state is derived from validated program instructions and Hero token movements. The collector tracks a coherent per-Hero state rather than calculating each public KPI independently.

Validated World Mode program:

- Program: `6AzuBKDsR88vinh399HV5v7fgB1eZyoYwQ3PmdYqFRZG`
- Stake discriminator: `5de284a68d093065`
- Unstake discriminator: `8eb5bf9552afd864`
- Quest Restart discriminator: `042211a8aa29e88801`
- Stake/unstake staking wallet: fourth account argument, Python index `3`

Quest Restart is user-level. A qualifying restart advances each Hero currently staked by that user only when it occurs after the Hero's current stake began and after any already-known qualifying restart.

Quest activity buckets are calculated from stored timestamps at build time. A Hero can therefore age into another bucket without a new transaction.

## Beneficial ownership

The site reports beneficial ownership, not merely token-account custody. Known World Mode custody/staking state is resolved so a Hero held by an operational staking address can still be attributed to the user state established by the validated staking flow.

Burned Heroes are removed from active ownership and staking state.

## Market activity

Validated direct-program paths include:

- Magic Eden V2: `M2mx93ekt1fmXSVkTrUL9xVFHkmME8HTUi5Cyc5aF7K`
- Tensor Marketplace: `TCMPhJdwDryooaGtiocG1u3xcYbRpiJzb283XfCZsDp`
- Tensor AMM: `TAMM6ub33ij1mbetoMyVBLeKY5iP41i4UPUJQGkhfsg`

Sales are deduplicated by `(signature, mint)`. Buyer, seller, mint, sale price, and royalty interpretation come from validated instruction/account layouts, not a generic transaction label.

## Floor/listings semantics

`scripts/floor_listings_pipeline.py` requests Magic Eden collection statistics in two modes so floor price and aggregate listed count preserve the site's historical units and listing semantics.

The pipeline:

- retries transient network, 408, 429, and server failures;
- rejects missing, non-numeric, negative, or fractional source fields where inappropriate;
- divides lamports by `1_000_000_000` for SOL floor price;
- writes no more than one row for a UTC date;
- never backfills a missed date using a later value;
- only advances `floor_checkpoint_date` after a valid candidate is prepared;
- proves the resulting Pages deployment in production.

## Production batch and crash recovery

Every live release writes `data/state/production_batch_manifest.json`. It identifies the stable input snapshot, exact signatures, and canonical/public hashes for the prepared release.

D1 remains authoritative for whether those signatures have been acknowledged.

Recovery runs before new preparation:

- crash before commit: D1 still holds the batch pending, so it can be reduced again;
- crash after push but before deployment proof: the next runner discovers the manifest-introducing commit and resumes proof;
- crash after deployment but before ACK: the next runner re-proves the release and ACKs the exact manifest signatures;
- later webhook arrivals are never included in that ACK because only manifest signatures are submitted.

## Watch frontier and delivery gaps

The raw webhook address set must evolve as Heroes move to previously unseen token accounts. `scripts/refresh_webhook_watch_frontier.py` derives destination accounts from the stable raw batch and adds missing accounts without removing older watched accounts.

The established overlap/gap audit fetches transactions that could have landed while the watched set changed and replays them through the same deterministic normalization path. Dry-run mode computes the proposed frontier without mutating the provider configuration.

## Release safety

Production is deliberately fail-closed:

- duplicate input does not create duplicate state transitions;
- stale slots cannot regress newer Hero state;
- same-slot ownership conflicts with different signatures fail rather than guessing;
- collection/build/validation failure leaves checkpoints unchanged;
- only explicit release paths can be staged;
- `git diff --check` runs before release;
- `origin/main` is fetched immediately before commit/push and a race causes refusal;
- force-push is never used;
- Cloudflare Pages content is compared semantically with expected JSON;
- no proven deployment means no D1 ACK.

## Frontend data contract

The browser loads static JSON from `site/public/data/`:

- `summary.json`
- `hero-state.json`
- `floor-listings.json`
- `market-history.json`
- `market-daily.json`
- `launch.json`
- `treasury.json`

The public site therefore stays available when a provider is temporarily unavailable. A production outage makes published data stale rather than making the page depend on a failing live API request.

## Freshness behavior

The UI evaluates Hero/market freshness and floor/listings freshness independently.

- Hero/market is expected through the current UTC date.
- Floor/listings is considered current when it is no more than one UTC date behind because it represents an end-of-day observation.
- When both are current, the header status is green and displays the visitor's local calendar date.
- If either domain is outside its expected window, the status is yellow and displays the oldest successful date represented by the required domains.
- If both domains are more than 30 days stale, the status is red.

The status is presentation logic. It does not modify or fabricate source data.

## Browser-only persistence

The site uses ordinary first-party browser storage for convenience features:

- selected Hero and PFP background color are persisted in `localStorage`;
- the previous compact KPI snapshot for "Since your last visit" is persisted in `localStorage`;
- the generated return recap is kept in `sessionStorage` so a page refresh does not erase it during the current tab session.

No account, wallet connection, file-system permission, notification permission, or backend user profile is required for these features.

## Public metric definitions

### Active supply

Heroes in canonical state that have not been explicitly burned.

### Holders

Unique current beneficial owners after known staking/custody state is resolved. This is intentionally not a raw count of SPL token accounts.

### Staked

Heroes whose current canonical World Mode state is staked.

### Quest activity

Time since the last qualifying quest restart for the current stake. Rolling time buckets can change as time passes even without another transaction.

### Secondary sales and volume

Validated supported-marketplace sales, deduplicated by transaction signature and mint.

### Floor and listings

A single daily collection snapshot near the end of the UTC date. Missing dates are not interpolated or copied from neighboring dates.

### Royalties

Creator royalty value associated with validated secondary sales.

### First resale timing

The first validated secondary sale after the Hero entered circulation.

### Project-connected funding activity

Observed on-chain transfers and conversions involving wallets supported by the documented evidence. Transaction relationships do not by themselves prove common control, ownership of every asset in a wallet, or off-chain spending.

## Adapting this system to another Solana collection

The infrastructure can remain mostly unchanged, but the following collection-specific contracts must be replaced intentionally.

### Collection identity

- complete mint inventory;
- metadata and trait schema;
- collection symbol or marketplace identifiers;
- images and local asset mapping;
- public project links and explanatory copy.

### Chain semantics

- program IDs;
- instruction discriminators;
- account positions and signer assumptions;
- staking/custody behavior;
- beneficial-owner rules;
- burn rules;
- quest or other project-specific state transitions.

### Market semantics

- supported marketplace program layouts;
- collection filtering;
- buyer/seller/mint/price parsing;
- royalty parsing;
- floor/listing source and units.

### Baseline

Choose a well-defined activation boundary and freeze:

- canonical Hero state;
- historical sale ledger;
- rarity and metadata;
- mint/launch history;
- floor/listing history;
- checkpoints and append-only control state;
- real transaction fixtures that prove the new parsers.

Do not enable automation until the independent reducer reproduces the expected state at that boundary.

### Repository map

- `collector/`: normalizers and collection state contracts
- `cloudflare/webhook-inbox/`: durable ingestion and production clock
- `.github/workflows/`: guarded execution entry points
- `scripts/production_pipeline.py`: live release orchestration
- `scripts/floor_listings_pipeline.py`: daily marketplace snapshot
- `scripts/build_dashboard_data.py`: frontend JSON builder
- `data/baseline/`: immutable historical baseline
- `data/state/`: checkpoints, ledgers, and release control state
- `data/history/`: persisted time series
- `site/`: frontend and public data contract
- `tests/`: real fixtures and deterministic regression contracts

## Secrets and permissions

Secret values never belong in Git, documentation, fixtures, or command arguments that may be logged.

Relevant secret names include:

- Cloudflare Worker: `HELIUS_WEBHOOK_AUTH`, `PIPELINE_TOKEN`, `GITHUB_DISPATCH_TOKEN`
- GitHub Actions: `HELIUS_API_KEY`, `ALCHEMY_API_KEY`, `PIPELINE_TOKEN`, `HELIUS_WEBHOOK_AUTH`

The dispatch token should be restricted to this repository with the minimum permissions required by the Worker dispatch path.

## Operational checks

Useful non-mutating commands include:

```text
python scripts/audit_webhook_pending.py
python scripts/validate_cutover.py
python scripts/validate_live.py
python -m unittest discover -s tests -v
```

If a committed batch has not been acknowledged, do not edit its manifest or manually mark arbitrary D1 rows processed. Re-run the production pipeline from a clean current `main` checkout with the required secrets so recovery can re-prove the exact release before ACK.
