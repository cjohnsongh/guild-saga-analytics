# Guild Saga webhook inbox

Deployment is managed by Cloudflare Workers Builds from this directory.

Durable Cloudflare Worker + D1 inbox for raw Solana webhook deliveries.

The Worker authenticates, deduplicates by transaction signature, stores the raw payload, and acknowledges quickly. It does **not** interpret Guild Saga state. Parsing/reduction remains in the audited Python collector in GitHub Actions. Cloudflare Cron Triggers also use this Worker as the scheduler for the 30-minute webhook production pipeline and the independently checkpointed floor/listings snapshot.

## Endpoints

- `GET /health` — public liveness only.
- `POST /webhooks/helius` — raw Helius webhook receiver; requires exact `Authorization` value stored in Worker secret `HELIUS_WEBHOOK_AUTH`.
- `GET /internal/pending?limit=100` — pending raw deliveries; requires `Authorization: Bearer <PIPELINE_TOKEN>`.
- `POST /internal/ack` — mark a batch processed after the canonical pipeline succeeds.
- `POST /internal/fail` — record a processing error while leaving an event pending.
- `GET /internal/stats` — inbox counts + activation metadata.
- `POST /internal/activation` — stores the successful webhook activation boundary.

## Secrets

Never commit values. Configure these as Cloudflare Worker secrets:

- `HELIUS_WEBHOOK_AUTH`
- `PIPELINE_TOKEN`
- `GITHUB_DISPATCH_TOKEN` — fine-grained GitHub token restricted to `cjohnsongh/guild-saga-analytics` with **Contents: Read and write**, used only to create the allow-listed `production_cron` and `floor_listings_daily` repository-dispatch events.

## D1

Apply `schema.sql` to the bound D1 database before enabling the Helius webhook.

The `signature` primary key makes duplicate Helius deliveries harmless.

## Scheduled dispatches

`wrangler.jsonc` defines two UTC schedules:

- `0,30 * * * *` -> `production_cron` every 30 minutes for Hero/market webhook processing.
- `30,50 23 * * *` -> `floor_listings_daily` at 23:30 UTC, with a same-day retry at 23:50 UTC. This samples close to the end of the UTC day to better match the historical Tensor daily-close series while avoiding an exact-midnight scrape. The workflow writes at most one row per UTC date, so the second firing is a no-op if the first succeeded.

The scheduled handler refuses unknown cron strings. Transient network, HTTP 408/429, and 5xx failures are retried inside the Worker; authentication/configuration failures fail visibly. GitHub manual `workflow_dispatch` remains dry-run only for both pipelines.
