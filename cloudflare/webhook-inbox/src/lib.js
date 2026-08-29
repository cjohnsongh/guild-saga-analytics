export function extractSignature(tx) {
  if (!tx || typeof tx !== "object") return null;

  const direct = [
    tx.signature,
    tx.transactionSignature,
    tx.txSignature,
    tx?.transaction?.signature,
  ];
  for (const value of direct) {
    if (typeof value === "string" && value.length > 20) return value;
  }

  const signatures = tx?.transaction?.signatures;
  if (Array.isArray(signatures) && signatures.length) {
    const first = signatures[0];
    if (typeof first === "string" && first.length > 20) return first;
    if (first && typeof first === "object") {
      for (const key of ["signature", "sig"]) {
        const value = first[key];
        if (typeof value === "string" && value.length > 20) return value;
      }
    }
  }
  return null;
}

export function normalizeHeliusPayload(body) {
  const rows = Array.isArray(body) ? body : [body];
  const out = [];
  const seen = new Set();

  for (const tx of rows) {
    if (!tx || typeof tx !== "object") continue;
    const signature = extractSignature(tx);
    if (!signature || seen.has(signature)) continue;
    seen.add(signature);

    const blockTime = Number.isFinite(Number(tx.blockTime)) ? Number(tx.blockTime) : null;
    const slot = Number.isFinite(Number(tx.slot)) ? Number(tx.slot) : null;
    out.push({ signature, blockTime, slot, tx });
  }
  return out;
}

export function exactAuthorization(request, expected) {
  if (!expected) return false;
  return request.headers.get("authorization") === expected;
}

export function pipelineAuthorization(request, token) {
  if (!token) return false;
  return request.headers.get("authorization") === `Bearer ${token}`;
}

export function clampLimit(value, fallback = 100, max = 100) {
  const n = Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(n) || n <= 0) return fallback;
  return Math.min(n, max);
}
