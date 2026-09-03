import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";

class FakeStatement {
  constructor(db, sql) {
    this.db = db;
    this.sql = sql;
    this.args = [];
  }

  bind(...args) {
    this.args = args;
    return this;
  }

  async run() {
    if (!this.sql.includes("INSERT INTO inbox_meta")) {
      throw new Error(`Unexpected run SQL: ${this.sql}`);
    }
    const [key, value, updatedAt] = this.args;
    this.db.meta.set(key, { value, updated_at: updatedAt });
    return { success: true };
  }

  async all() {
    if (!this.sql.includes("SELECT key, value FROM inbox_meta")) {
      throw new Error(`Unexpected all SQL: ${this.sql}`);
    }
    return {
      results: this.args
        .filter((key) => this.db.meta.has(key))
        .map((key) => ({ key, value: this.db.meta.get(key).value })),
    };
  }
}

class FakeDb {
  constructor() {
    this.meta = new Map();
  }

  prepare(sql) {
    return new FakeStatement(this, sql);
  }
}

function env() {
  return { DB: new FakeDb(), PIPELINE_TOKEN: "test-token" };
}

function heartbeatRequest(pipeline, token = "test-token") {
  return new Request("https://worker.example/internal/heartbeat", {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({ pipeline }),
  });
}

test("successful pipeline heartbeats are publicly readable without exposing secrets", async () => {
  const testEnv = env();

  const productionResponse = await worker.fetch(heartbeatRequest("production"), testEnv);
  assert.equal(productionResponse.status, 200);
  const productionBody = await productionResponse.json();
  assert.equal(productionBody.ok, true);
  assert.equal(productionBody.pipeline, "production");

  const floorResponse = await worker.fetch(heartbeatRequest("floor_listings"), testEnv);
  assert.equal(floorResponse.status, 200);

  const freshnessResponse = await worker.fetch(
    new Request("https://worker.example/freshness"),
    testEnv,
  );
  assert.equal(freshnessResponse.status, 200);
  assert.equal(freshnessResponse.headers.get("access-control-allow-origin"), "*");
  assert.equal(freshnessResponse.headers.get("cache-control"), "no-store");

  const body = await freshnessResponse.json();
  assert.equal(body.ok, true);
  assert.match(body.checked_at, /^\d{4}-\d{2}-\d{2}T/);
  assert.equal(body.production_last_success_at, productionBody.last_success_at);
  assert.match(body.floor_listings_last_success_at, /^\d{4}-\d{2}-\d{2}T/);
  assert.equal("PIPELINE_TOKEN" in body, false);
});

test("heartbeat endpoint fails closed for invalid pipeline names and authorization", async () => {
  const testEnv = env();

  const invalid = await worker.fetch(heartbeatRequest("unknown"), testEnv);
  assert.equal(invalid.status, 400);

  const unauthorized = await worker.fetch(heartbeatRequest("production", "wrong-token"), testEnv);
  assert.equal(unauthorized.status, 401);
});
