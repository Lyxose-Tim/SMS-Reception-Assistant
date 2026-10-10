-- Additive service inbox and deidentified replay tombstones.

            CREATE TABLE IF NOT EXISTS provider_inbox(
              sid TEXT PRIMARY KEY,phone TEXT NOT NULL,recipient TEXT NOT NULL,body TEXT NOT NULL,
              opt_out_type TEXT NOT NULL DEFAULT '',received_at TEXT NOT NULL,status TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0,next_attempt_at TEXT NOT NULL,
              error_code TEXT NOT NULL DEFAULT '',resource_at TEXT,result TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS inbox_tombstones(sid_hash TEXT PRIMARY KEY,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS privacy_dedup(event_hash TEXT PRIMARY KEY,created_at TEXT NOT NULL);
            
PRAGMA user_version=2;
