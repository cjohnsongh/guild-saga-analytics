CREATE TABLE IF NOT EXISTS webhook_events (
  signature TEXT PRIMARY KEY,
  slot INTEGER,
  block_time INTEGER,
  received_at TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'helius_raw',
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'processed', 'failed')),
  attempt_count INTEGER NOT NULL DEFAULT 0,
  processed_at TEXT,
  last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_status_time
  ON webhook_events(status, block_time, received_at);

CREATE TABLE IF NOT EXISTS inbox_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
