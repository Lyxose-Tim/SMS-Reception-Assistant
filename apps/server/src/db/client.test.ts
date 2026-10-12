import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { openDatabase, type Database } from './client';
import { auditLog } from './schema';

let opened: Database[] = [];
let dirs: string[] = [];

afterEach(() => {
  for (const d of opened) d.close();
  for (const dir of dirs) {
    try {
      rmSync(dir, { recursive: true, force: true, maxRetries: 5 });
    } catch {
      // On Windows the native libsql connection keeps the .db/-wal/-shm handles until GC even after
      // close(), so removal can fail with EPERM/EBUSY. The dir lives in the OS temp folder; ignore.
    }
  }
  opened = [];
  dirs = [];
});

async function open(url: string): Promise<Database> {
  const database = await openDatabase(url);
  opened.push(database);
  return database;
}

describe('openDatabase', () => {
  it('migrates an in-memory database', async () => {
    const { client } = await open(':memory:');
    const tables = await client.execute("SELECT name FROM sqlite_master WHERE type = 'table'");
    const names = tables.rows.map((r) => String(r.name));
    for (const t of ['businesses', 'users', 'sessions', 'faqs', 'contacts', 'conversations', 'messages', 'booking_requests', 'audit_log']) {
      expect(names).toContain(t);
    }
  });

  it('uses WAL for file databases and survives concurrent writes', async () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'reception-db-'));
    dirs.push(dir);
    const { db, client } = await open(`file:${path.join(dir, 'nested', 'test.db')}`);
    const mode = await client.execute('PRAGMA journal_mode');
    expect(String(mode.rows[0]?.journal_mode)).toBe('wal');
    const fk = await client.execute('PRAGMA foreign_keys');
    expect(Number(fk.rows[0]?.foreign_keys)).toBe(1);

    await Promise.all(Array.from({ length: 50 }, (_, i) => db.insert(auditLog).values({ actor: 'test', action: `write-${i}` })));
    const count = await client.execute('SELECT COUNT(*) AS n FROM audit_log');
    expect(Number(count.rows[0]?.n)).toBe(50);
  });
});
