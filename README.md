# Guild Saga Analytics

Independent on-chain analytics and historical data for Guild Saga Heroes, a 10,000-piece Solana NFT collection connected to Guild Saga: Labyrinths.

The site tracks collection supply and burns, holders and ownership concentration, World Mode staking and quest activity, secondary-market history, floor price and listings, mint history, rarity, royalties, project-connected wallet activity, and the people behind Guild Saga.

## Production architecture

The browser is deliberately simple: Cloudflare Pages serves a React application plus static JSON. Live collection state is produced outside the browser by a fail-closed pipeline.

```text
Helius raw webhook
  -> Cloudflare Worker
  -> D1 durable pending inbox
  -> Cloudflare Cron Trigger (:00 and :30 UTC)
  -> GitHub Actions
  -> deterministic Python reducers + validation
  -> explicit Git commit
  -> Cloudflare Pages deployment proof
  -> exact D1 acknowledgement
```

Floor/listings uses a separate daily path:

```text
Cloudflare Cron Trigger (23:30 UTC, 23:50 recovery)
  -> GitHub Actions
  -> Magic Eden collection stats
  -> one UTC-dated observation
  -> validation
  -> explicit Git commit
  -> Cloudflare Pages deployment proof
```

A failed provider call, parser, validation, push, or deployment proof leaves the currently deployed site intact. Missing floor/listing days are never fabricated.

See [`docs/architecture.md`](docs/architecture.md) for the full system contract and adaptation guide.

## Repository structure

- `site/` - React website source, public JSON, Hero artwork, and static assets
- `cloudflare/webhook-inbox/` - Worker, D1 schema, webhook receiver, internal inbox API, and production cron dispatch
- `.github/workflows/` - guarded dry-run and production entry points
- `collector/` - Solana normalization and deterministic Hero/market state rules
- `scripts/` - production orchestration, JSON generation, validation, recovery, audit, and backfill tools
- `data/baseline/` - immutable historical baseline datasets
- `data/state/` - compact live overlays, checkpoints, append-only ledgers, and production manifests
- `data/history/` - persisted time series such as daily floor/listings
- `tests/` - parser, state-machine, baseline-integrity, pipeline, and release-safety regression tests
- `docs/` - architecture, operating notes, and baseline provenance

## Public frontend data

The website consumes seven JSON files under `site/public/data/`:

- `summary.json`
- `hero-state.json`
- `floor-listings.json`
- `market-history.json`
- `market-daily.json`
- `launch.json`
- `treasury.json`

The browser does not need provider credentials and does not make live Solana or marketplace API calls.

## Validation

Useful local checks include:

```bash
python -m unittest discover -s tests -v
python scripts/validate_cutover.py
python scripts/validate_live.py
```

Manual GitHub Actions dispatches are dry-run only. Production entry is restricted to the exact repository-dispatch event types emitted by the Cloudflare Cron Trigger.
