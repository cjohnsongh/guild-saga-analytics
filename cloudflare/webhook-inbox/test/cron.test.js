import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";

test("scheduled handler dispatches only the production_cron repository event", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return new Response(null, { status: 204 });
  };

  try {
    await worker.scheduled(
      { scheduledTime: Date.parse("2026-08-30T12:00:00Z"), cron: "0,30 * * * *" },
      { GITHUB_DISPATCH_TOKEN: "test-token" },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(calls.length, 1);
  assert.equal(
    calls[0].url,
    "https://api.github.com/repos/cjohnsongh/guild-saga-analytics/dispatches",
  );
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers.authorization, "Bearer test-token");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    event_type: "production_cron",
    client_payload: {
      source: "cloudflare_cron",
      scheduled_time: "2026-08-30T12:00:00.000Z",
      cron: "0,30 * * * *",
    },
  });
});

test("scheduled handler fails closed when GitHub dispatch secret is missing", async () => {
  await assert.rejects(
    worker.scheduled(
      { scheduledTime: Date.parse("2026-08-30T12:00:00Z"), cron: "0,30 * * * *" },
      {},
    ),
    /GITHUB_DISPATCH_TOKEN is not configured/,
  );
});
