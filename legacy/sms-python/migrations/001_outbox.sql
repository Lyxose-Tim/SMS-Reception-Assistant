-- Idempotent durable outbox schema. Contact/message additive columns are applied
-- through Engine._schema after PRAGMA table_info checks, preserving old demos.
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER NOT NULL UNIQUE REFERENCES messages(id),
  event_id TEXT NOT NULL,
  phone TEXT NOT NULL REFERENCES contacts(phone),
  body TEXT NOT NULL,
  origin TEXT NOT NULL,
  purpose TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  provider_sid TEXT NOT NULL DEFAULT '',
  error_code TEXT NOT NULL DEFAULT '',
  callback_nonce TEXT NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  encoding TEXT NOT NULL,
  segments INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  next_attempt_at TEXT,
  claimed_at TEXT,
  conflict INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(status,next_attempt_at,id);
CREATE UNIQUE INDEX IF NOT EXISTS outbox_provider_sid ON outbox(provider_sid) WHERE provider_sid!='';
CREATE TABLE IF NOT EXISTS outbox_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  outbox_id INTEGER NOT NULL REFERENCES outbox(id),
  started_at TEXT NOT NULL,
  segments INTEGER NOT NULL,
  archive_key TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS outbox_callbacks (
  fingerprint TEXT PRIMARY KEY,
  outbox_id INTEGER NOT NULL REFERENCES outbox(id),
  provider_sid TEXT NOT NULL,
  status TEXT NOT NULL,
  error_code TEXT NOT NULL,
  received_at TEXT NOT NULL
);
-- Global usage counters contain no phone, body, event/message ID or secret.
-- They intentionally survive content deletion and must be unioned on restore.
CREATE TABLE IF NOT EXISTS outbox_quota_reservations (
  reservation_key TEXT PRIMARY KEY,
  day_utc TEXT NOT NULL,
  segments INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox_quota_attempts (
  attempt_key TEXT PRIMARY KEY,
  started_at TEXT NOT NULL
);
