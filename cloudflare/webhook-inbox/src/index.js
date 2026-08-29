import {
  clampLimit,
  exactAuthorization,
  normalizeHeliusPayload,
  pipelineAuthorization,
} from "./lib.js";

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8" };

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
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
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/health") {
      return json({ ok: true, service: "guild-saga-webhook-inbox" });
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

    if (request.method === "POST" && url.pathname === "/internal/activation") {
      return setActivation(request, env);
    }

    return json({ ok: false, error: "not_found" }, 404);
  },
};
