# Guild Saga webhook inbox

Durable Cloudflare Worker + D1 inbox for raw Solana webhook deliveries.

The Worker only authenticates, deduplicates by transaction signature, stores the raw payload, and acknowledges quickly. It does **not** interpret Guild Saga state. Parsing/reduction remains in the audited Python collector in GitHub Actions.

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

## D1

Apply `schema.sql` to the bound D1 database before enabling the Helius webhook.

The `signature` primary key makes duplicate Helius deliveries harmless.
