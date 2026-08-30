import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";

async function captureDispatch(controller) {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return new Response(null, { status: 204 });
  };

  try {
    await worker.scheduled(controller, { GITHUB_DISPATCH_TOKEN: "test-token" });
  } finally {
    globalThis.fetch = originalFetch;
  }
  return calls;
}

test("30-minute cron dispatches only production_cron", async () => {
  const calls = await captureDispatch({
    scheduledTime: Date.parse("2026-08-30T12:00:00Z"),
    cron: "0,30 * * * *",
  });

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

test("daily retry cron dispatches floor_listings_daily", async () => {
  const calls = await captureDispatch({
    scheduledTime: Date.parse("2026-08-30T23:50:00Z"),
    cron: "30,50 23 * * *",
  });

  assert.equal(calls.length, 1);
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    event_type: "floor_listings_daily",
    client_payload: {
      source: "cloudflare_cron",
      scheduled_time: "2026-08-30T23:50:00.000Z",
      cron: "30,50 23 * * *",
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

test("scheduled handler refuses unknown cron triggers", async () => {
  await assert.rejects(
    worker.scheduled(
      { scheduledTime: Date.parse("2026-08-30T12:05:00Z"), cron: "5 * * * *" },
      { GITHUB_DISPATCH_TOKEN: "test-token" },
    ),
    /Unrecognized cron trigger/,
  );
});
