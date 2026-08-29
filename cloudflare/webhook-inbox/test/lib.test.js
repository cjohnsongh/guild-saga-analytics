import test from "node:test";
import assert from "node:assert/strict";
import {
  clampLimit,
  extractSignature,
  normalizeHeliusPayload,
  pipelineAuthorization,
} from "../src/lib.js";

const SIG = "3BpkuDNGr3YDL3fV3yBZprmCnrBsjK6h5CpK6kC9SVGhRkPU7bXMppxgfdwYR3B2auxmz7U4T73PXB4cDhPK4ybc";

test("extracts signature from raw Solana transaction shape", () => {
  assert.equal(extractSignature({ transaction: { signatures: [SIG] } }), SIG);
});

test("normalizes singleton or array payloads and deduplicates", () => {
  const tx = { blockTime: 1787951856, slot: 123, transaction: { signatures: [SIG] } };
  const rows = normalizeHeliusPayload([tx, tx]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].signature, SIG);
  assert.equal(rows[0].blockTime, 1787951856);
});

test("pipeline auth requires Bearer token", () => {
  const req = new Request("https://example.com", { headers: { authorization: "Bearer abc" } });
  assert.equal(pipelineAuthorization(req, "abc"), true);
  assert.equal(pipelineAuthorization(req, "def"), false);
});

test("limit is safely clamped", () => {
  assert.equal(clampLimit("500"), 100);
  assert.equal(clampLimit("25"), 25);
  assert.equal(clampLimit("nope"), 100);
});
