# Guild Saga webhook inbox

Deployment is managed by Cloudflare Workers Builds from this directory.

Durable Cloudflare Worker + D1 inbox for raw Solana webhook deliveries.

The Worker authenticates, deduplicates by transaction signature, stores the raw payload, and acknowledges quickly. It does **not** interpret Guild Saga state. Parsing/reduction remains in the audited Python collector in GitHub Actions. A Cloudflare Cron Trigger also uses this Worker as the production clock and emits one narrowly scoped GitHub `repository_dispatch` event every 30 minutes.

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
- `GITHUB_DISPATCH_TOKEN` — fine-grained GitHub token restricted to `cjohnsongh/guild-saga-analytics` with **Contents: Read and write**, used only to create the `production_cron` repository-dispatch event.

## D1

Apply `schema.sql` to the bound D1 database before enabling the Helius webhook.

The `signature` primary key makes duplicate Helius deliveries harmless.

## Production cron

`wrangler.jsonc` defines `0,30 * * * *` (UTC). The scheduled handler POSTs `repository_dispatch` with event type `production_cron` to the GitHub repository. Transient network, HTTP 408/429, and 5xx failures are retried inside the Worker; authentication/configuration failures fail visibly rather than silently falling back.

During rollout, the old GitHub-native `7,37 * * * *` schedule remains in the Actions workflow as a temporary fallback. Remove that schedule and `schedule-probe.yml` only after at least one Cloudflare-triggered production run has completed successfully. Manual `workflow_dispatch` remains dry-run only.
