import {
  clampLimit,
  exactAuthorization,
  normalizeHeliusPayload,
  pipelineAuthorization,
} from "./lib.js";

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8" };
const PUBLIC_JSON_HEADERS = {
  ...JSON_HEADERS,
  "access-control-allow-origin": "*",
  "cache-control": "no-store",
};
const HEARTBEAT_META_KEYS = new Map([
  ["production", "production_last_success_at"],
  ["floor_listings", "floor_listings_last_success_at"],
]);

const GITHUB_DISPATCH_URL = "https://api.github.com/repos/cjohnsongh/guild-saga-analytics/dispatches";
const GITHUB_API_VERSION = "2026-03-10";
const CRON_DISPATCH_EVENTS = new Map([
  ["0,30 * * * *", "production_cron"],
  ["30,50 23 * * *", "floor_listings_daily"],
]);
const DISPATCH_RETRY_DELAYS_MS = [0, 2000, 5000];

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function retryableDispatchStatus(status) {
  return status === 408 || status === 429 || status >= 500;
}

async function dispatchScheduledCron(controller, env) {
  if (!env.GITHUB_DISPATCH_TOKEN) {
    throw new Error("GITHUB_DISPATCH_TOKEN is not configured");
  }

  const eventType = CRON_DISPATCH_EVENTS.get(controller.cron);
  if (!eventType) {
    throw new Error(`Unrecognized cron trigger: ${controller.cron}`);
  }

  const payload = JSON.stringify({
    event_type: eventType,
    client_payload: {
      source: "cloudflare_cron",
      scheduled_time: new Date(controller.scheduledTime).toISOString(),
      cron: controller.cron,
    },
  });

  let lastError = null;
  for (let attempt = 0; attempt < DISPATCH_RETRY_DELAYS_MS.length; attempt += 1) {
    const delay = DISPATCH_RETRY_DELAYS_MS[attempt];
    if (delay) await sleep(delay);

    try {
      const response = await fetch(GITHUB_DISPATCH_URL, {
        method: "POST",
        headers: {
          accept: "application/vnd.github+json",
          authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
          "content-type": "application/json",
          "user-agent": "guild-saga-webhook-inbox",
          "x-github-api-version": GITHUB_API_VERSION,
        },
        body: payload,
      });

      if (response.status === 204) {
        console.log(`Dispatched ${eventType} to GitHub on attempt ${attempt + 1}.`);
        return;
      }

      const detail = (await response.text()).slice(0, 1000);
      lastError = new Error(`GitHub repository_dispatch failed: HTTP ${response.status} ${detail}`.trim());
      if (!retryableDispatchStatus(response.status)) throw lastError;
    } catch (error) {
      lastError = error instanceof Error ? error : new Error(String(error));
      if (attempt === DISPATCH_RETRY_DELAYS_MS.length - 1) break;
    }
  }

  throw lastError ?? new Error("GitHub repository_dispatch failed");
}


function json(data, status = 200, headers = JSON_HEADERS) {
  return new Response(JSON.stringify(data), { status, headers });
}

async function parseJson(request) {
  try {
    return await request.json();
  } catch {
    return null;
  }
}

async function receiveHelius(request, env) {
  if (!exactAuthorization(request, env.HELIUS_WEBHOOK_AUTH)) {
    return json({ ok: false, error: "unauthorized" }, 401);
  }

  const body = await parseJson(request);
  if (body === null) return json({ ok: false, error: "invalid_json" }, 400);

  const rows = normalizeHeliusPayload(body);
  if (!rows.length) {
    return json({ ok: true, received: 0, inserted_or_duplicate: 0 });
  }

  const receivedAt = new Date().toISOString();
  const statements = rows.map(({ signature, slot, blockTime, tx }) =>
    env.DB.prepare(
      `INSERT OR IGNORE INTO webhook_events
       (signature, slot, block_time, received_at, source, payload_json, status)
       VALUES (?, ?, ?, ?, 'helius_raw', ?, 'pending')`
    ).bind(signature, slot, blockTime, receivedAt, JSON.stringify(tx))
  );

  await env.DB.batch(statements);
  return json({ ok: true, received: rows.length, inserted_or_duplicate: rows.length });
}

function validIso(value) {
  return typeof value === "string" && value.length >= 20 && Number.isFinite(Date.parse(value));
}

async function pendingEvents(request, env, url) {
  if (!pipelineAuthorization(request, env.PIPELINE_TOKEN)) {
    return json({ ok: false, error: "unauthorized" }, 401);
  }

  const limit = clampLimit(url.searchParams.get("limit"), 100, 100);
  const snapshotParam = url.searchParams.get("snapshot_received_at");
  const snapshotReceivedAt = snapshotParam || new Date().toISOString();
  if (!validIso(snapshotReceivedAt)) {
    return json({ ok: false, error: "invalid_snapshot_received_at" }, 400);
  }

  const afterReceivedAt = url.searchParams.get("after_received_at");
  const afterSignature = url.searchParams.get("after_signature");
  if ((afterReceivedAt && !afterSignature) || (!afterReceivedAt && afterSignature)) {
    return json({ ok: false, error: "incomplete_cursor" }, 400);
  }
  if (afterReceivedAt && !validIso(afterReceivedAt)) {
    return json({ ok: false, error: "invalid_after_received_at" }, 400);
  }
  if (afterSignature && afterSignature.length <= 20) {
    return json({ ok: false, error: "invalid_after_signature" }, 400);
  }

  let statement;
  if (afterReceivedAt) {
    statement = env.DB.prepare(
      `SELECT signature, slot, block_time, received_at, source, payload_json, attempt_count
       FROM webhook_events
       WHERE status = 'pending'
         AND received_at <= ?
         AND (received_at > ? OR (received_at = ? AND signature > ?))
       ORDER BY received_at, signature
       LIMIT ?`
    ).bind(snapshotReceivedAt, afterReceivedAt, afterReceivedAt, afterSignature, limit);
  } else {
    statement = env.DB.prepare(
      `SELECT signature, slot, block_time, received_at, source, payload_json, attempt_count
       FROM webhook_events
       WHERE status = 'pending'
         AND received_at <= ?
       ORDER BY received_at, signature
       LIMIT ?`
    ).bind(snapshotReceivedAt, limit);
  }

  const result = await statement.all();
  const events = result.results ?? [];
  const last = events.length === limit ? events[events.length - 1] : null;

  return json({
    ok: true,
    snapshot_received_at: snapshotReceivedAt,
    events,
    next_cursor: last ? {
      after_received_at: last.received_at,
      after_signature: last.signature,
    } : null,
  });
}

async function ackEvents(request, env) {
  if (!pipelineAuthorization(request, env.PIPELINE_TOKEN)) {
    return json({ ok: false, error: "unauthorized" }, 401);
  }
  const body = await parseJson(request);
  const signatures = Array.isArray(body?.signatures)
    ? [...new Set(body.signatures.filter((x) => typeof x === "string" && x.length > 20))].slice(0, 100)
    : [];
  if (!signatures.length) return json({ ok: false, error: "no_signatures" }, 400);

  const processedAt = new Date().toISOString();
  await env.DB.batch(
    signatures.map((signature) =>
      env.DB.prepare(
        `UPDATE webhook_events
         SET status='processed', processed_at=?, last_error=NULL
         WHERE signature=? AND status='pending'`
      ).bind(processedAt, signature)
    )
  );

  const placeholders = signatures.map(() => "?").join(",");
  const statusResult = await env.DB.prepare(
    `SELECT signature, status FROM webhook_events WHERE signature IN (${placeholders})`
  ).bind(...signatures).all();
  const statuses = Object.fromEntries((statusResult.results ?? []).map((row) => [row.signature, row.status]));
  const missing = signatures.filter((signature) => !(signature in statuses));
  const notProcessed = signatures.filter((signature) => statuses[signature] !== "processed");

  return json({
    ok: missing.length === 0 && notProcessed.length === 0,
    requested: signatures.length,
    processed: signatures.length - missing.length - notProcessed.length,
    missing,
    not_processed: notProcessed,
  }, missing.length === 0 && notProcessed.length === 0 ? 200 : 409);
}

async function failEvents(request, env) {
  if (!pipelineAuthorization(request, env.PIPELINE_TOKEN)) {
    return json({ ok: false, error: "unauthorized" }, 401);
  }
  const body = await parseJson(request);
  const signature = typeof body?.signature === "string" ? body.signature : "";
  const error = typeof body?.error === "string" ? body.error.slice(0, 4000) : "processing_failed";
  if (signature.length <= 20) return json({ ok: false, error: "invalid_signature" }, 400);

  await env.DB.prepare(
    `UPDATE webhook_events
     SET attempt_count=attempt_count+1, last_error=?
     WHERE signature=? AND status='pending'`
  ).bind(error, signature).run();
  return json({ ok: true });
}

async function stats(request, env) {
  if (!pipelineAuthorization(request, env.PIPELINE_TOKEN)) {
    return json({ ok: false, error: "unauthorized" }, 401);
  }
  const counts = await env.DB.prepare(
    `SELECT status, COUNT(*) AS n FROM webhook_events GROUP BY status ORDER BY status`
  ).all();
  const meta = await env.DB.prepare(
    `SELECT key, value, updated_at FROM inbox_meta ORDER BY key`
  ).all();
  return json({ ok: true, counts: counts.results ?? [], meta: meta.results ?? [] });
}


async function recordHeartbeat(request, env) {
  if (!pipelineAuthorization(request, env.PIPELINE_TOKEN)) {
    return json({ ok: false, error: "unauthorized" }, 401);
  }

  const body = await parseJson(request);
  const pipeline = typeof body?.pipeline === "string" ? body.pipeline : "";
  const metaKey = HEARTBEAT_META_KEYS.get(pipeline);
  if (!metaKey) return json({ ok: false, error: "invalid_pipeline" }, 400);

  const now = new Date().toISOString();
  await env.DB.prepare(
    `INSERT INTO inbox_meta(key, value, updated_at)
     VALUES (?, ?, ?)
     ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at`
  ).bind(metaKey, now, now).run();

  return json({ ok: true, pipeline, last_success_at: now });
}

async function publicFreshness(env) {
  const keys = [...HEARTBEAT_META_KEYS.values()];
  const result = await env.DB.prepare(
    `SELECT key, value FROM inbox_meta WHERE key IN (?, ?)`
  ).bind(...keys).all();
  const values = Object.fromEntries((result.results ?? []).map((row) => [row.key, row.value]));

  const production = validIso(values.production_last_success_at)
    ? values.production_last_success_at
    : null;
  const floorListings = validIso(values.floor_listings_last_success_at)
    ? values.floor_listings_last_success_at
    : null;

  return json({
    ok: true,
    checked_at: new Date().toISOString(),
    production_last_success_at: production,
    floor_listings_last_success_at: floorListings,
  }, 200, PUBLIC_JSON_HEADERS);
}

async function setActivation(request, env) {
  if (!pipelineAuthorization(request, env.PIPELINE_TOKEN)) {
    return json({ ok: false, error: "unauthorized" }, 401);
  }
  const now = new Date().toISOString();
  const body = await parseJson(request);
  const value = typeof body?.activated_at === "string" && body.activated_at
    ? body.activated_at
    : now;

  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return json({ ok: false, error: "invalid_activated_at" }, 400);

  await env.DB.prepare(
    `INSERT INTO inbox_meta(key, value, updated_at)
     VALUES ('webhook_activated_at', ?, ?)
     ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at`
  ).bind(new Date(parsed).toISOString(), now).run();

  return json({ ok: true, webhook_activated_at: new Date(parsed).toISOString() });
}

export default {
  async scheduled(controller, env) {
    await dispatchScheduledCron(controller, env);
  },

  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/health") {
      return json({ ok: true, service: "guild-saga-webhook-inbox" });
    }

    if (request.method === "GET" && url.pathname === "/freshness") {
      return publicFreshness(env);
    }

    if (request.method === "POST" && url.pathname === "/webhooks/helius") {
      return receiveHelius(request, env);
    }

    if (request.method === "GET" && url.pathname === "/internal/pending") {
      return pendingEvents(request, env, url);
    }

    if (request.method === "POST" && url.pathname === "/internal/ack") {
      return ackEvents(request, env);
    }

    if (request.method === "POST" && url.pathname === "/internal/fail") {
      return failEvents(request, env);
    }

    if (request.method === "GET" && url.pathname === "/internal/stats") {
      return stats(request, env);
    }

    if (request.method === "POST" && url.pathname === "/internal/heartbeat") {
      return recordHeartbeat(request, env);
    }

    if (request.method === "POST" && url.pathname === "/internal/activation") {
      return setActivation(request, env);
    }

    return json({ ok: false, error: "not_found" }, 404);
  },
};
