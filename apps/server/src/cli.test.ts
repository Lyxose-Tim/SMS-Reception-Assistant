import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { runCli } from './cli';
import { openDatabase } from './db/client';
import { loadEnv, type Env } from './env';
import { getBusinessBySlug, listFaqs } from './repos/business';
import { findUserByEmail } from './repos/users';

let dir: string;
let env: Env;
let lines: string[];
const out = (line: string) => lines.push(line);

beforeEach(() => {
  dir = mkdtempSync(path.join(tmpdir(), 'reception-cli-'));
  env = loadEnv({ APP_SECRET: 'c'.repeat(40), DATABASE_URL: `file:${path.join(dir, 'cli.db')}`, PUBLIC_ORIGIN: 'http://127.0.0.1:8787' });
  lines = [];
});
afterEach(() => {
  try {
    rmSync(dir, { recursive: true, force: true, maxRetries: 5 });
  } catch {
    // On Windows the native libsql connection keeps the .db/-wal/-shm handles until GC even after
    // close(), so removal can fail with EPERM/EBUSY. The dir lives in the OS temp folder; ignore.
  }
});

describe('runCli', () => {
  it('seeds the demo business with an owner, idempotently', async () => {
    expect(await runCli(['seed-demo', '--email', 'demo@harbor.example', '--password', 'demo password 123'], env, out)).toBe(0);
    expect(await runCli(['seed-demo', '--email', 'demo@harbor.example', '--password', 'demo password 123'], env, out)).toBe(0);
    const db = await openDatabase(env.DATABASE_URL);
    const business = await getBusinessBySlug(db.db, 'harbor-home');
    expect(business?.widget.allowedOrigins).toEqual(['http://127.0.0.1:8787']);
    expect(await listFaqs(db.db, business!.id)).toHaveLength(5);
    expect(await findUserByEmail(db.db, 'demo@harbor.example')).not.toBeNull();
    db.close();
  });

  it('creates a business owner and refuses duplicates', async () => {
    const args = ['create-owner', '--slug', 'acme-plumbing', '--name', 'Acme Plumbing', '--email', 'boss@acme.example', '--password', 'a long password'];
    expect(await runCli(args, env, out)).toBe(0);
    expect(await runCli(args, env, out)).toBe(1);
    expect(lines.join('\n')).toContain('already exists');
  });

  it('prints usage for unknown commands and invalid input', async () => {
    expect(await runCli(['nope'], env, out)).toBe(1);
    expect(lines.join('\n')).toContain('Usage');
    expect(await runCli(['create-owner', '--slug', 'x'], env, out)).toBe(1);
    expect(await runCli(['create-owner', '--slug', 'acme', '--name', 'Acme', '--email', 'a@acme.example', '--password', 'short'], env, out)).toBe(1);
  });
});
